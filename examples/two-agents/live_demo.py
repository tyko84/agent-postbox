#!/usr/bin/env python3
"""A recording kit for a demonstration with REAL Claude Code sessions.

    python examples/two-agents/live_demo.py setup     [--dir DIR]
    python examples/two-agents/live_demo.py check     [--dir DIR]
    python examples/two-agents/live_demo.py rehearse  [--dir DIR] [--max-calls N] [--max-cost USD]

This is not run.py. run.py drives two SCRIPTED agents (no model) and says so.
Here the agents are real `claude` sessions that you start yourself, in two
terminals, and this file only prepares the ground and inspects what is left:

  setup      builds a throwaway project with the real install.py (adapter
             claude-code), a tiny code task, and a fact only agent "alpha" can
             find (a script that reads a registry file outside the project,
             named by an environment variable set in alpha's terminal only).
             It prints the two terminal commands, the exact prompts, what to
             expect on screen, and narration with timings for 60-90 seconds.
  check      reads ARTIFACTS afterwards (the mailbox files, client.py, the
             hook output captured by a rehearsal or found in Claude Code's own
             transcript for the project) and prints OK or FAIL per beat. It
             never looks at what a model said. Exit 1 if any beat failed.
  rehearse   runs the same scenario headless (`claude -p`, one session per
             role, alpha then beta) and then runs `check`. It spends usage.

The beats: 1 two sessions wrote under two identities; 2 alpha found the fact
and put it in a message; 3 the message is in the mailbox; 4 the hook put it in
beta's context at beta's next prompt (not a poll); 5 beta acknowledged it with
an ANSWER; 6 beta's code uses the fact alpha found.

Safety, as in scripts/e2e_claude.py: --setting-sources project, --strict-mcp-config,
a short --tools list, --permission-mode dontAsk, model haiku, effort low,
--no-session-persistence, a --max-budget-usd per session, and never
--dangerously-skip-permissions. A session beyond --max-calls is refused, and
none starts once the running total from each session's `total_cost_usd` would
pass --max-cost (default 1.50, at most 2.00).

A headless rehearsal does NOT prove an interactive session behaves the same;
it proves the files, the hook and the scenario are sound.

Exit status: 0 every beat OK; 1 a beat failed; 2 not run (usage, `claude`
missing or not signed in, a budget refusal). Stdlib only. POSIX only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL = ROOT / "install.py"
DEST = "tools/agent-postbox"
TOOL = f"{DEST}/agent_mail.py"
MARKER = ".agent-postbox-live-demo"
ALPHA = "alpha"
BETA = "beta"
MODEL = "haiku"
TIMEOUT = 300
DEFAULT_MAX_CALLS = 4
DEFAULT_MAX_COST = 1.50
HARD_MAX_COST = 2.00
PER_SESSION_CAP = 0.40
SKIP = 2

CLIENT_PY = '''\
"""Tiny helpers for the Payments service."""

PAYMENTS_BASE = "https://payments.internal.example/v1/ledger"


def charge_url(charge_id: str) -> str:
    return f"{PAYMENTS_BASE}/charges/{charge_id}"
'''

DISCOVER_PY = '''\
"""Ask the service registry where the Payments service lives today."""
import json
import os
import sys

path = os.environ.get("PAYMENTS_REGISTRY")
if not path:
    sys.exit("PAYMENTS_REGISTRY is not set: this terminal cannot see the registry")
reg = json.load(open(path))
print("payments live base URL:", reg["live_base"])
print("deprecated (do not use):", reg["deprecated"])
'''

PROMPT_A = (
    "Run `python3 discover.py` to find the live Payments base URL. Teammate agent 'beta' is "
    "about to write payments code, so tell them through Postbox: run `python3 "
    f"{TOOL} send --type ASK --from {ALPHA} --to {BETA} --subject '<one line: the exact live "
    "base URL, that the old v1 URL is retired, and that new payments code must use the live "
    "one; ask beta to confirm>' --body '<one sentence>'`. The URL must be in the subject "
    "(beta sees only subjects at pickup). Then say SENT.")
PROMPT_B1 = "Read client.py and summarise it in one line."
PROMPT_B2 = (
    "Handle any new Postbox mail first: answer it with the Reply command shown (use --body "
    "'text' and --result DONE). Then add a function refund_url(charge_id) to client.py that "
    "returns the refund URL of a charge on the same Payments service.")


class Usage(Exception):
    """Bad arguments or a refusal to start: exit 2."""


@dataclass
class Beat:
    n: int
    name: str
    ok: bool
    evidence: str


@dataclass
class Spend:
    """Sessions counted against --max-calls; cost summed from each result."""
    max_calls: int
    max_cost: float
    calls: int = 0
    cost: float = 0.0
    per_session: list[tuple[str, float]] = field(default_factory=list)

    def admit(self, role: str) -> float:
        """The --max-budget-usd to give the next session, or Usage if none may start."""
        if self.calls >= self.max_calls:
            raise Usage(f"refusing to start the {role} session: --max-calls {self.max_calls} "
                        f"already used")
        left = self.max_cost - self.cost
        if left <= 0.01:
            raise Usage(f"refusing to start the {role} session: USD {self.cost:.4f} spent of "
                        f"--max-cost {self.max_cost:.2f}")
        self.calls += 1
        return round(min(PER_SESSION_CAP, left), 2)

    def record(self, role: str, cost: float | None) -> None:
        self.cost += cost or 0.0
        self.per_session.append((role, cost or 0.0))


# ---- setup ------------------------------------------------------------------------------
def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """No inherited AGENT_MAIL_* and none of the variables a parent Claude Code sets."""
    parent = re.compile(r"^(CLAUDECODE|CLAUDE_PID|CLAUDE_PROJECT_DIR|CLAUDE_EFFORT|"
                        r"CLAUDE_CODE_\w+|PAYMENTS_REGISTRY)$")
    env = {k: v for k, v in os.environ.items()
           if not parent.match(k) and not k.startswith("AGENT_MAIL_")}
    env.update(extra or {})
    return env


def default_dir() -> Path:
    return Path(tempfile.gettempdir()) / "agent-postbox-live-demo"


def allow_rules(project: Path) -> list[str]:
    tool = project / TOOL
    return ["Read", "Edit", "Write", "Bash(python3 discover.py)",
            f"Bash(python3 {TOOL} *)", f'Bash(python3 "{tool}" *)', f"Bash(python3 {tool} *)"]


def prepare_dir(demo: Path) -> Path:
    if demo.exists():
        if not (demo / MARKER).exists() and any(demo.iterdir()):
            raise Usage(f"{demo} exists, is not empty and is not a live-demo directory")
        shutil.rmtree(demo)
    demo.mkdir(parents=True)
    (demo / MARKER).write_text("examples/two-agents/live_demo.py work directory\n",
                               encoding="utf-8")
    return demo.resolve()


def load_state(demo: Path) -> dict[str, Any]:
    try:
        state = json.loads((demo / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Usage(f"{demo} has no state.json: run `setup` first") from None
    if not isinstance(state, dict):
        raise Usage(f"{demo}/state.json is not an object")
    return state


def setup(demo: Path, out: Any = None) -> dict[str, Any]:
    """Build the throwaway project; return its state. Prints the guide to `out`."""
    out = out or sys.stdout
    demo = prepare_dir(demo)
    project = demo / "project"
    project.mkdir()
    token = secrets.token_hex(3)
    live = f"https://payments.internal.example/v7-{token}/ledger"
    registry = demo / "registry" / "payments.json"
    registry.parent.mkdir()
    registry.write_text(json.dumps({"live_base": live, "deprecated":
                                    "https://payments.internal.example/v1/ledger"}) + "\n",
                        encoding="utf-8")
    (project / "client.py").write_text(CLIENT_PY, encoding="utf-8")
    (project / "discover.py").write_text(DISCOVER_PY, encoding="utf-8")
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q", str(project)], capture_output=True, env=clean_env())
    subprocess.run([sys.executable, "-I", str(INSTALL), str(project), "--adapter",
                    "claude-code"], check=True, capture_output=True, text=True, env=clean_env())
    local = project / ".claude" / "settings.local.json"
    local.write_text(json.dumps({"permissions": {"allow": allow_rules(project)}}, indent=2)
                     + "\n", encoding="utf-8")
    if shutil.which("git"):
        for cmd in (["add", "-A"], ["-c", "user.name=demo", "-c", "user.email=demo@example.invalid",
                                    "commit", "-q", "-m", "baseline"]):
            subprocess.run(["git", "-C", str(project), *cmd], capture_output=True, env=clean_env())
    (demo / "logs").mkdir()
    state: dict[str, Any] = {"schema": 1, "project": str(project), "registry": str(registry),
                             "live_base": live, "token": token, "alpha": ALPHA, "beta": BETA}
    (demo / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(guide(demo, state), file=out)
    return state


def guide(demo: Path, state: dict[str, Any]) -> str:
    p, reg, live = state["project"], state["registry"], state["live_base"]
    me = "python3 " + shlex.quote(str(Path(__file__).resolve()))
    return f"""\
LIVE DEMO READY (real Claude Code sessions; nothing here is scripted)
  demo dir : {demo}
  project  : {p}
  the fact only alpha can find: {live}
    (client.py still says .../v1/ledger; the registry file is outside the project)

BEFORE RECORDING (off camera)
  1. Open each terminal once, run `claude`, accept "trust this folder" (the project's
     hook lives in .claude/settings.json, so the host asks once), then /exit.
  2. `{me} setup` resets everything; run it again to re-record.

TERMINAL 1 (alpha, left pane)
  cd {shlex.quote(p)}
  export AGENT_MAIL_IDENTITY={ALPHA}
  export PAYMENTS_REGISTRY={shlex.quote(reg)}
  claude --model {MODEL}

TERMINAL 2 (beta, right pane)  -- no registry variable: beta cannot see the fact
  cd {shlex.quote(p)}
  export AGENT_MAIL_IDENTITY={BETA}
  claude --model {MODEL}

PROMPTS (paste exactly; the rehearsal uses the same text)
  beta  #1 : {PROMPT_B1}
  alpha #1 : {PROMPT_A}
  beta  #2 : {PROMPT_B2}

EXPECTED ON SCREEN
  beta  #1 : a one-line summary of client.py. No "Agent mail" block (nothing sent yet).
  alpha #1 : python3 discover.py prints "payments live base URL: {live}", then a send
             command printing "wrote .../docs/agent-mail/<id>-....md" and "id: <id>".
  beta  #2 : BEFORE beta answers, a hook block "Agent mail - 1 live message(s) addressed to
             'beta'." with "ASK from alpha [awaiting your reply]" and the subject holding
             the URL; then a send --type ANSWER ... --result DONE; then client.py gains
             refund_url using {live}.
  after    : {me} check   (OK/FAIL per beat, from the files)

NARRATION (about 80 s)
   0:00  "Two Claude Code sessions in one project, two terminals, two identities.
          They share nothing but a folder of files."
   0:08  beta #1: "Beta reads client.py; it points at the v1 ledger."
   0:16  alpha #1: "Alpha runs the discovery script. Only this terminal can see the
          registry: the live Payments endpoint moved."
   0:34  "Alpha tells beta through Postbox. That is one file in docs/agent-mail."
   0:42  beta #2: "Beta's next prompt. Nobody told beta to look: the hook put the mail
          in front of the model."  (point at the 'Agent mail' block)
   0:55  "Beta acknowledges with an ANSWER, then writes refund_url against the NEW URL,
          which beta had no other way to know."
   1:10  run check: "Six beats, each judged from the files, not from what a model said."
"""


# ---- check ------------------------------------------------------------------------------
def mailbox(state: dict[str, Any]) -> list[dict[str, Any]]:
    tool = Path(state["project"]) / TOOL
    try:
        cp = subprocess.run([sys.executable, "-I", str(tool), "list", "--json"],
                            cwd=state["project"], capture_output=True, text=True,
                            env=clean_env(), timeout=60)
        rows = json.loads(cp.stdout)["messages"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return []
    return [r for r in rows if isinstance(r, dict)]


def transcript_dir(project: str) -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", project)


def pickup_evidence(demo: Path, state: dict[str, Any], ask_id: str,
                    transcripts: Path | None) -> str | None:
    """Where the hook's output with this ASK's id shows beta was handed it."""
    log = demo / "logs" / f"{BETA}.hook.txt"
    if ask_id:
        try:
            text = log.read_text(encoding="utf-8")
            if ask_id in text and "Agent mail - " in text:
                return f"hook output captured in {log.name} names {ask_id}"
        except OSError:
            pass
        tdir = transcripts or transcript_dir(state["project"])
        if tdir.is_dir():
            for f in sorted(tdir.glob("*.jsonl")):
                try:
                    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                        if ask_id in line and "Agent mail - " in line:
                            return f"transcript {f.name} holds the hook output naming {ask_id}"
                except OSError:
                    continue
    return None


def refund_url(state: dict[str, Any]) -> tuple[str | None, str]:
    """(url, why not) from importing the receiver's client.py in a fresh interpreter."""
    code = ("import client; print(client.refund_url('ch_42'))")
    try:
        cp = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, "
                             f"{state['project']!r}); {code}"], cwd=state["project"],
                            capture_output=True, text=True, timeout=30, env=clean_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"could not run client.py ({e.__class__.__name__})"
    if cp.returncode != 0:
        return None, "client.refund_url failed: " + (cp.stderr.strip().splitlines() or ["?"])[-1]
    return cp.stdout.strip(), ""


def check(demo: Path, transcripts: Path | None = None) -> list[Beat]:
    state = load_state(demo)
    live = str(state["live_base"])
    rows = mailbox(state)
    asks = [r for r in rows if r.get("type") == "ASK" and r.get("from") == ALPHA
            and r.get("to") == BETA]
    ask = asks[0] if asks else None
    ask_id = str(ask["id"]) if ask else ""
    answers = [r for r in rows if r.get("type") == "ANSWER" and r.get("from") == BETA
               and r.get("to") == ALPHA and ask and r.get("reply_to") == ask_id]
    beats: list[Beat] = []
    senders = sorted({str(r.get("from")) for r in rows})
    beats.append(Beat(1, "two sessions wrote under two identities",
                      ALPHA in senders and BETA in senders,
                      f"senders in the mailbox: {', '.join(senders) or 'none'}"))
    beats.append(Beat(2, "alpha's discovery is in its message (only alpha could know it)",
                      bool(ask) and live in str(ask.get("subject") if ask else ""),
                      f"ASK subject: {(ask or {}).get('subject')!r}; wanted {live}"
                      if ask else "no ASK from alpha to beta"))
    beats.append(Beat(3, "the message went through Postbox", bool(ask),
                      f"ASK {ask_id} alpha -> beta" if ask else "no ASK file alpha -> beta"))
    found = pickup_evidence(demo, state, ask_id, transcripts)
    beats.append(Beat(4, "beta's prompt hook handed it the message", found is not None,
                      found or "no captured hook output or transcript names the ASK"))
    ack = bool(answers)
    closed = ack and next((r for r in rows if r.get("id") == ask_id), {}).get("owes_reply") is False
    beats.append(Beat(5, "beta acknowledged with an ANSWER citing the ASK", ack and bool(closed),
                      f"ANSWER {answers[0].get('id')} reply_to {ask_id}, ASK no longer owed"
                      if ack and closed else
                      ("ANSWER exists but the ASK is still owed" if ack else "no ANSWER by beta")))
    url, why = refund_url(state)
    changed = (Path(state["project"]) / "client.py").read_text(encoding="utf-8") != CLIENT_PY
    good = bool(url) and live.rsplit("/ledger", 1)[0] in (url or "") and "ch_42" in (url or "")
    beats.append(Beat(6, "beta's code uses the fact alpha found", changed and good,
                      f"refund_url('ch_42') = {url}" if url else why))
    return beats


def print_beats(beats: list[Beat], out: Any = None) -> None:
    out = out or sys.stdout
    for b in beats:
        print(f"{'OK  ' if b.ok else 'FAIL'} beat {b.n}: {b.name}\n       {b.evidence}", file=out)
    bad = [b.n for b in beats if not b.ok]
    print("all 6 beats happened" if not bad else f"beats that did not happen: {bad}", file=out)


# ---- rehearse ---------------------------------------------------------------------------
def claude_status(claude: str) -> str | None:
    """Why `claude` cannot run, or None."""
    try:
        a = subprocess.run([claude, "auth", "status"], capture_output=True, text=True,
                           timeout=60)
        if a.returncode != 0 or not json.loads(a.stdout).get("loggedIn"):
            return "`claude` is not signed in (`claude auth status`)"
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return "`claude` could not be started or its auth status read"
    return None


def session_cmd(claude: str, project: Path, role: str, budget: float) -> list[str]:
    tools = "Bash" if role == ALPHA else "Read,Edit,Write,Bash"
    allowed = list(allow_rules(project)) if role == BETA else [
        r for r in allow_rules(project) if r.startswith("Bash")]
    return [claude, "-p", "--model", MODEL, "--effort", "low", "--max-turns", "8",
            "--tools", tools, "--setting-sources", "project", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--no-session-persistence",
            "--max-budget-usd", f"{budget:.2f}",
            "--output-format", "stream-json", "--verbose", "--include-hook-events",
            "--allowedTools", *allowed]


def run_session(claude: str, demo: Path, state: dict[str, Any], role: str, prompt: str,
                spend: Spend) -> dict[str, Any]:
    budget = spend.admit(role)
    project = Path(state["project"])
    extra = {"AGENT_MAIL_IDENTITY": role}
    if role == ALPHA:
        extra["PAYMENTS_REGISTRY"] = state["registry"]
    result: dict[str, Any] = {"role": role, "timed_out": False}
    hook = ""
    try:
        cp = subprocess.run(session_cmd(claude, project, role, budget), input=prompt,
                            cwd=project, capture_output=True, text=True, timeout=TIMEOUT,
                            env=clean_env(extra))
        out, result["rc"], result["stderr"] = cp.stdout, cp.returncode, cp.stderr[-400:]
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        result["timed_out"] = True
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if (ev.get("type") == "system" and ev.get("subtype") == "hook_response"
                and ev.get("hook_event") == "UserPromptSubmit"):
            hook += str(ev.get("stdout") or ev.get("output") or "")
        elif ev.get("type") == "result":
            cost = ev.get("total_cost_usd")
            result.update(cost=float(cost) if isinstance(cost, (int, float)) else None,
                          turns=ev.get("num_turns"), is_error=bool(ev.get("is_error")),
                          said=str(ev.get("result") or "")[:300],
                          denials=len(ev.get("permission_denials") or []))
    spend.record(role, result.get("cost"))
    (demo / "logs" / f"{role}.hook.txt").write_text(hook, encoding="utf-8")
    (demo / "logs" / f"{role}.session.json").write_text(json.dumps(result, indent=2) + "\n",
                                                        encoding="utf-8")
    return result


def rehearse(args: argparse.Namespace) -> int:
    claude = args.claude or shutil.which("claude")
    reason = "`claude` is not on PATH" if not claude else claude_status(claude)
    if reason or not claude:
        print(f"SKIP rehearse: {reason}. Nothing was run and no beat is claimed.")
        return SKIP
    spend = Spend(args.max_calls, args.max_cost)
    if args.max_calls < 2:
        print(f"refusing to rehearse: it needs 2 sessions and --max-calls is {args.max_calls}")
        return SKIP
    demo = Path(args.dir)
    state = setup(demo, out=open(os.devnull, "w", encoding="utf-8"))
    demo = demo.resolve()
    try:
        for role, prompt in ((ALPHA, PROMPT_A), (BETA, PROMPT_B2)):
            r = run_session(claude, demo, state, role, prompt, spend)
            print(f"{role}: cost USD {r.get('cost')}, turns {r.get('turns')}, "
                  f"denials {r.get('denials')}, error {r.get('is_error')}, "
                  f"timed out {r['timed_out']}; said: {r.get('said', '')[:120]!r}")
    except Usage as e:
        print(f"live_demo: {e}", file=sys.stderr)
        return SKIP
    print(f"model sessions started: {spend.calls} (max {spend.max_calls}); total cost USD "
          f"{spend.cost:.4f} (max {spend.max_cost:.2f})")
    beats = check(demo)
    print_beats(beats)
    return 0 if all(b.ok for b in beats) else 1


def cost_arg(text: str) -> float:
    value = float(text)
    if not 0 < value <= HARD_MAX_COST:
        raise argparse.ArgumentTypeError(f"must be above 0 and at most {HARD_MAX_COST:.2f}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Recording kit for a live two-session demo")
    ap.add_argument("command", choices=("setup", "check", "rehearse"))
    ap.add_argument("--dir", default=str(default_dir()), help="demo directory (default: "
                    "<tmp>/agent-postbox-live-demo; setup and rehearse reset it)")
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS,
                    help=f"most model sessions to start (default {DEFAULT_MAX_CALLS})")
    ap.add_argument("--max-cost", type=cost_arg, default=DEFAULT_MAX_COST,
                    help=f"USD ceiling for rehearse (default {DEFAULT_MAX_COST}, max "
                         f"{HARD_MAX_COST})")
    ap.add_argument("--claude", metavar="PATH", help="the claude executable (default: PATH)")
    ap.add_argument("--transcripts", metavar="DIR", help="check: where Claude Code's transcripts "
                    "for the project are (default ~/.claude/projects/<project>)")
    args = ap.parse_args(argv)
    if args.max_calls < 0:
        ap.error("--max-calls must be 0 or more")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "setup":
            setup(Path(args.dir))
            return 0
        if args.command == "check":
            beats = check(Path(args.dir), Path(args.transcripts) if args.transcripts else None)
            print_beats(beats)
            return 0 if all(b.ok for b in beats) else 1
        return rehearse(args)
    except Usage as e:
        print(f"live_demo: {e}", file=sys.stderr)
        return SKIP


if __name__ == "__main__":
    raise SystemExit(main())
