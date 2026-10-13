#!/usr/bin/env python3
"""Two-agent micro-experiment with REAL headless Claude Code sessions (spends usage).

    python scripts/experiment_micro.py --out DIR [--arms P,C1,C2] [--seeds 1,2,3]
                                       [--ceiling-usd 14] [--model haiku]

The design, metrics and stopping rules are in docs/micro-experiment.md, written
before the first run. One run = three fresh sessions in one throwaway project:
agent-b (needs a fact), agent-a (holds it), agent-b again. Arms: P (Postbox with the
pickup adapter), C1 (a scripted relay of `TO TEAM:` lines), C2 (a NOTES.md that nothing
injects). Outcomes come from files, never from what a model says. Every session's raw
event stream and every run's record are written under --out and nothing is dropped.

SPEND. The host-reported `total_cost_usd` of every session is summed. A session is not
started if the total plus 1.5 times the largest session so far (0.30 before any) would
pass --ceiling-usd. Exit status: 0 finished (also when a run was skipped for budget, which
is reported); 2 could not start or usage error. Not run in CI. POSIX only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_claude as e2e  # noqa: E402

ARMS = ("P", "C1", "C2")
DEST = e2e.DEST
A, B = "agent-a", "agent-b"
MAX_TURNS = 8
DEFAULT_CEILING = 14.0
FIRST_SESSION_ESTIMATE = 0.30

CONFIG = 'WINDOW_CODE = None\n'
UTIL = ('def is_weekend(day: str) -> bool:\n    raise NotImplementedError\n')
README = 'Tiny app. app/config.py holds settings; app/util.py holds helpers.\n'

B_TICKET = (
    "You are agent-b on a two-agent team working in this repository (your current "
    "directory). Ticket B: set WINDOW_CODE in app/config.py to the team's deployment "
    "window code. The code is not in this repository; agent-a has it. If you cannot get "
    "it, leave WINDOW_CODE = None and say so plainly. Do not guess or invent a code. "
    "Stop when you have done what you can in this session.")
A_TICKET = (
    "You are agent-a on a two-agent team working in this repository (your current "
    "directory). Ticket A: implement is_weekend(day) in app/util.py (day is a lowercase "
    "weekday name; return True for saturday and sunday). Observation from your sandbox: "
    "running `python3 probe.py` printed `window code: {code}`. Your teammate agent-b "
    "needs the deployment window code for its ticket; share what teammates will need. "
    "Stop when you have done what you can in this session.")

CHANNEL = {
    "P": {
        B: ("Team mail uses `python3 tools/agent-postbox/agent_mail.py` (Bash). To ask "
            "agent-a for something: `python3 tools/agent-postbox/agent_mail.py send --type "
            "ASK --from agent-b --to agent-a --subject '<subject>' --body '<text>'`. If "
            "mail you were shown has an answer, read its body with `python3 "
            "tools/agent-postbox/agent_mail.py show <id>`."
            " Run each mail command as its own Bash call: no `cd`, `;`, `&&`, pipes or redirects (a compound command is refused)."),
        A: ("Team mail uses `python3 tools/agent-postbox/agent_mail.py` (Bash). If mail "
            "shown to you asks you something, answer with the Reply command shown (put "
            "the information in --body, add --result DONE). To tell a teammate something "
            "unprompted: `python3 tools/agent-postbox/agent_mail.py send --type NOTICE "
            "--from agent-a --to agent-b --subject '<subject>' --body '<text>'`."
            " Run each mail command as its own Bash call: no `cd`, `;`, `&&`, pipes or redirects (a compound command is refused)."),
    },
    "C1": {
        B: ("To tell or ask the team something, write a line starting with `TO TEAM:` in "
            "your final reply. Lines your teammate wrote that start `TO TEAM:` are shown "
            "to you at the top of your next prompt."),
        A: ("To tell or ask the team something, write a line starting with `TO TEAM:` in "
            "your final reply. Lines your teammate wrote that start `TO TEAM:` are shown "
            "to you at the top of your next prompt."),
    },
    "C2": {
        B: ("The team shares NOTES.md in this repository. Read it before you start a "
            "ticket, and append one line to it when you ask, find or finish something."),
        A: ("The team shares NOTES.md in this repository. Read it before you start a "
            "ticket, and append one line to it when you ask, find or finish something."),
    },
}


class Ceiling(Exception):
    """The next session could push the total past the spend ceiling."""


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def code_for(seed: int) -> str:
    return "W-" + hashlib.sha256(f"micro:{seed}".encode()).hexdigest()[:4].upper()


def arm_order(seed: int) -> list[str]:
    order = list(ARMS)
    random.Random(f"order:{seed}").shuffle(order)
    return order


class Experiment(e2e.Runner):
    def __init__(self, args: argparse.Namespace, claude: str, work: Path, out: Path) -> None:
        super().__init__(args, claude, work)
        self.out = out
        self.spent = 0.0
        self.biggest = 0.0
        self.sessions = 0
        self.tools_used: list[dict[str, Any]] = []
        self.raw: list[str] = []

    def absorb(self, s: e2e.Session, ev: dict[str, Any]) -> None:
        super().absorb(s, ev)
        if ev.get("type") == "assistant":
            for item in (ev.get("message") or {}).get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    self.tools_used.append({"name": item.get("name"),
                                            "input": item.get("input")})

    def guard(self) -> None:
        worst = (self.biggest or FIRST_SESSION_ESTIMATE) * 1.5
        if self.spent + worst > self.args.ceiling_usd:
            raise Ceiling(f"spent ${self.spent:.4f} + worst case ${worst:.4f} would pass "
                          f"${self.args.ceiling_usd:.2f}")

    def run_session(self, tag: str, proj: Path, prompt: str, identity: str,
                    allowed: tuple[str, ...]) -> tuple[e2e.Session, float, list[dict[str, Any]]]:
        self.guard()
        self.tools_used = []
        cmd = self.base_cmd(tools="Bash,Read,Edit,Write", allowed=allowed,
                            max_turns=MAX_TURNS)
        s = e2e.Session()
        t0 = time.monotonic()
        out = ""
        try:
            cp = subprocess.run(cmd, input=prompt, cwd=proj, capture_output=True, text=True,
                                env=self.env(identity), timeout=e2e.TIMEOUT)
            s.rc, out, s.stderr = cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired as e:
            s.timed_out = True
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        elapsed = time.monotonic() - t0
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                self.absorb(s, ev)
        (self.out / "raw").mkdir(parents=True, exist_ok=True)
        (self.out / "raw" / f"{tag}.jsonl").write_text(out, encoding="utf-8")
        self.sessions += 1
        cost = s.cost_usd or 0.0
        self.spent += cost
        self.biggest = max(self.biggest, cost)
        return s, elapsed, list(self.tools_used)

    # ---- one run -------------------------------------------------------------------
    def build_project(self, arm: str, seed: int) -> Path:
        proj = self.project(f"run-{seed}-{arm}", adapter=(arm == "P"))
        (proj / "app").mkdir()
        (proj / "app" / "config.py").write_text(CONFIG)
        (proj / "app" / "util.py").write_text(UTIL)
        (proj / "README.txt").write_text(README)
        if arm == "C2":
            (proj / "NOTES.md").write_text("# Team notes\n")
        return proj

    def allowed(self, arm: str, proj: Path) -> tuple[str, ...]:
        base = ("Read", "Edit", "Write")
        if arm != "P":
            return base
        rel = f"{DEST}/agent_mail.py"
        absolute = str(proj / rel)
        forms = [rel, f'"{absolute}"', f"'{absolute}'", absolute]   # the hook prints all of these
        return base + tuple(f"Bash(python3 {f} *)" for f in forms)

    def prompt(self, arm: str, who: str, ticket: str, relay: list[str]) -> str:
        head = "".join(f"Relayed from your teammate: {line}\n" for line in relay)
        return f"{head}{ticket}\n\n{CHANNEL[arm][who]}"

    @staticmethod
    def team_lines(text: str) -> list[str]:
        return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("TO TEAM:")]

    def run_one(self, arm: str, seed: int) -> dict[str, Any]:
        code = code_for(seed)
        proj = self.build_project(arm, seed)
        allowed = self.allowed(arm, proj)
        rec: dict[str, Any] = {"seed": seed, "arm": arm, "code": code, "sessions": [],
                               "prompt_chars": {}}
        pending: list[str] = []   # C1: lines written by the previous session
        t0 = time.monotonic()
        plan = [("b1", B, B_TICKET), ("a", A, A_TICKET.format(code=code)),
                ("b2", B, B_TICKET)]
        try:
            for tag, who, ticket in plan:
                prompt = self.prompt(arm, who, ticket, pending if arm == "C1" else [])
                rec["prompt_chars"].setdefault(who, len(prompt))
                s, secs, tools = self.run_session(f"{seed}-{arm}-{tag}", proj, prompt, who,
                                                  allowed)
                lines = self.team_lines(s.result)
                pending = lines
                rec["sessions"].append({
                    "tag": tag, "agent": who, "seconds": round(secs, 1), "cost_usd": s.cost_usd,
                    "turns": s.num_turns, "rc": s.rc, "timed_out": s.timed_out,
                    "is_error": s.is_error, "rate_limited": s.rate_limited,
                    "denials": len(s.denials), "result": s.result[:400],
                    "tools": [t["name"] for t in tools],
                    "read_notes": any("NOTES.md" in json.dumps(t.get("input")) for t in tools),
                    "hook_stdout": s.hook_stdout()[:600], "team_lines": lines})
                if s.rate_limited:
                    rec["aborted"] = "rate limited"
                    break
        except Ceiling as e:
            rec["aborted"] = f"spend ceiling: {e}"
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec.update(self.measure(arm, proj, code, rec))
        (self.out / "runs").mkdir(parents=True, exist_ok=True)
        (self.out / "runs" / f"{seed}-{arm}.json").write_text(
            json.dumps(rec, indent=2, sort_keys=True), encoding="utf-8")
        return rec

    # ---- outcomes, from artifacts ---------------------------------------------------
    def measure(self, arm: str, proj: Path, code: str, rec: dict[str, Any]) -> dict[str, Any]:
        cfg = (proj / "app" / "config.py").read_text() if (proj / "app" / "config.py").exists() else ""
        m = re.search(r"^WINDOW_CODE\s*=\s*(.+?)\s*$", cfg, re.M)
        value = m.group(1).strip().strip("'\"") if m else ""
        window = ("ok" if value == code else "none" if value in ("None", "") else "wrong")
        util = proj / "app" / "util.py"
        weekend_ok = False
        if util.exists():
            probe = ("import importlib.util,sys\n"
                     "s=importlib.util.spec_from_file_location('u',sys.argv[1])\n"
                     "u=importlib.util.module_from_spec(s);s.loader.exec_module(u)\n"
                     "print(all(u.is_weekend(d) is (d in('saturday','sunday')) for d in "
                     "('monday','tuesday','wednesday','thursday','friday','saturday','sunday')))\n")
            cp = subprocess.run([sys.executable, "-I", "-c", probe, str(util)],
                                capture_output=True, text=True, timeout=30)
            weekend_ok = cp.stdout.strip() == "True"
        out: dict[str, Any] = {"window": window, "window_value": value[:40],
                               "a_ok": weekend_ok,
                               "completed": window == "ok" and weekend_ok}
        gen = dlv = ack = 0
        if arm == "P":
            msgs = self.mailbox_json(proj, "list")["messages"]
            gen = len(msgs)
            ids = {x["id"] for x in msgs}
            asks = {x["id"] for x in msgs if x.get("type") == "ASK"}
            ack = sum(1 for x in msgs if x.get("type") == "ANSWER" and x.get("reply_to") in asks)
            subjects = [x.get("subject", "") for x in msgs]
            hook_text = " ".join(s["hook_stdout"] for s in rec["sessions"])
            dlv = sum(1 for sub in subjects if sub and sub in hook_text)
            out["mailbox_ids"] = sorted(ids)
        elif arm == "C1":
            gen = sum(len(s["team_lines"]) for s in rec["sessions"])
            dlv = sum(len(s["team_lines"]) for s in rec["sessions"][:2])   # relayed to the next session
        else:
            notes = (proj / "NOTES.md").read_text()
            gen = max(0, len([ln for ln in notes.splitlines() if ln.strip()]) - 1)
            dlv = sum(1 for s in rec["sessions"] if s["read_notes"])
        out.update(generated=gen, delivered=dlv, acknowledged=ack if arm == "P" else None)
        shared = (arm == "P" and gen > 0) or (arm == "C1" and any(
            s["agent"] == A and s["team_lines"] for s in rec["sessions"])) or (
            arm == "C2" and gen > 0)
        out["a_shared"] = shared
        out["missed"] = window != "ok"
        out["coordination_failure"] = window != "ok" or not shared
        out["cost_usd"] = round(sum(s["cost_usd"] or 0.0 for s in rec["sessions"]), 6)
        return out


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for arm in ARMS:
        rs = [r for r in records if r["arm"] == arm and "aborted" not in r]
        n = len(rs)
        k = sum(1 for r in rs if r["window"] == "ok")
        c = sum(1 for r in rs if r["completed"])
        lo, hi = wilson(k, n)
        rows[arm] = {"runs": n, "window_ok": k, "window_wrong": sum(r["window"] == "wrong" for r in rs),
                     "window_none": sum(r["window"] == "none" for r in rs), "a_ok": sum(r["a_ok"] for r in rs),
                     "completed": c, "window_ok_wilson95": [round(lo, 2), round(hi, 2)],
                     "generated": sum(r["generated"] for r in rs),
                     "delivered": sum(r["delivered"] for r in rs),
                     "acknowledged": sum((r["acknowledged"] or 0) for r in rs),
                     "seconds": round(sum(r["seconds"] for r in rs), 1),
                     "cost_usd": round(sum(r["cost_usd"] for r in rs), 4)}
    return rows


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Two-agent micro-experiment (spends usage)")
    ap.add_argument("--out", required=True, help="directory for raw results (must be new or empty)")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--seeds", default="1", help="comma list of instance seeds")
    ap.add_argument("--ceiling-usd", type=float, default=DEFAULT_CEILING)
    ap.add_argument("--spent-before", type=float, default=0.0,
                    help="USD already spent by earlier blocks, counted against the ceiling")
    ap.add_argument("--model", default=e2e.DEFAULT_MODEL)
    ap.add_argument("--claude", metavar="PATH")
    ap.add_argument("--workdir", metavar="DIR")
    args = ap.parse_args(argv)
    args.arms = [a for a in args.arms.split(",") if a]
    bad = [a for a in args.arms if a not in ARMS]
    if bad:
        ap.error(f"unknown arm {bad}; choose from {ARMS}")
    try:
        args.seeds = [int(x) for x in args.seeds.split(",") if x]
    except ValueError:
        ap.error("--seeds must be integers")
    args.max_calls = 10**6
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    claude = args.claude or shutil.which("claude")
    if not claude:
        print("SKIP: `claude` is not on PATH", file=sys.stderr)
        return 2
    version, reason = e2e.claude_status(claude)
    if reason:
        print(f"SKIP: {reason}", file=sys.stderr)
        return 2
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        print(f"--out {out} exists and is not empty", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    work = e2e.prepare_workdir(Path(args.workdir) if args.workdir
                               else Path(tempfile.gettempdir()) / "agent-postbox-micro")
    exp = Experiment(args, claude, work, out)
    exp.spent = args.spent_before
    records: list[dict[str, Any]] = []
    for seed in args.seeds:
        for arm in [a for a in arm_order(seed) if a in args.arms]:
            rec = exp.run_one(arm, seed)
            records.append(rec)
            print(f"seed={seed} arm={arm:<2} window={rec['window']:<5} a_ok={rec['a_ok']} "
                  f"completed={rec['completed']} msgs gen/dlv/ack="
                  f"{rec['generated']}/{rec['delivered']}/{rec['acknowledged']} "
                  f"{rec['seconds']}s ${rec['cost_usd']:.4f} total=${exp.spent:.4f}"
                  + (f"  ABORTED: {rec['aborted']}" if "aborted" in rec else ""))
            if "aborted" in rec:
                break
        else:
            continue
        break
    summary = {"schema": 1, "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "host_version": version, "model": args.model,
               "models_reported": sorted(exp.models), "sessions": exp.sessions,
               "spent_usd": round(exp.spent, 4), "ceiling_usd": args.ceiling_usd,
               "summary": summarise(records)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True),
                                      encoding="utf-8")
    print(json.dumps(summary["summary"], indent=2, sort_keys=True))
    print(f"sessions={exp.sessions} spent=${exp.spent:.4f} (ceiling ${args.ceiling_usd:.2f}); "
          f"raw results in {out}")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
