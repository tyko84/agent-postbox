#!/usr/bin/env python3
"""Experiment v3 harness (docs/experiment-v3.md): does mail reach an agent that is working
through SEVERAL prompts and is not reminded to look? Built on scripts/experiment_v2.py (the
instance, the scoring, the isolation rules, the spend guard, the event parsing are imported,
not copied). Offline tests only in CI; a live run spends usage.

    python scripts/experiment_v3.py --stage readiness --out DIR --dry-run
    POSTBOX_EXPERIMENT_LIVE=1 python scripts/experiment_v3.py --stage readiness --out DIR --live
    POSTBOX_EXPERIMENT_LIVE=1 python scripts/experiment_v3.py --stage main --out DIR --live \\
        --gate READINESS_OUT/readiness_gate.json          # the integrator's decision, not the author's
    python scripts/experiment_v3.py --freeze            # sha256 table for the pre-registration

One *instance* is the v2 ledger project (seeded D and VOID token; only agent-a's checkout holds
the vendor sample). Arms: P (mail, heredoc form taught), N2 (shared notes file, not told to read
it), N0 (no channel), O (oracle: the fact is in B's first prompt). In P, N2 and N0 agent-b runs ONE
live session (`claude -p --input-format stream-json`) with four scripted prompts; between
prompt 1 and prompt 2 a separate agent-a session discovers the fact and (P, N2) writes it to the
channel. Prompts 2 to 4 never mention a channel or a teammate. The outcome is `b_ok` after
prompt 4, from the hidden suites over the files (nothing is read from model text); `b_ok` after
each prompt is recorded as a secondary. No S4 nudge (v2's proxy is dropped; manual nudges are 0
by construction, which is a property of the harness).

NEVER A LIVE CALL BY ACCIDENT. Without exactly one of `--dry-run` / `--live` the script refuses;
`--live` also needs POSTBOX_EXPERIMENT_LIVE=1; the main stage also needs a passing, live
readiness gate whose prompts, model and effort match. `--out` must be outside the repository.
SPEND. Every turn's host-reported cost is summed (and written to OUT/spend.json after each
turn); a turn is not started if spent + 1.5 x the largest turn so far x (turns in flight)
would pass --ceiling-usd (readiness 1, main 15). `--prior-spend-usd` carries spend from earlier
invocations. Exit: 0 finished; 2 refused / usage error. POSIX only. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import queue
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_claude as e2e  # noqa: E402
import experiment_micro as micro  # noqa: E402
import experiment_v2 as v2  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARMS = ("P", "N2", "N0", "O")
A, B = v2.A, v2.B
SCHEMA = 3
STAGES = ("readiness", "main")
CEILING = {"readiness": 1.0, "main": 15.0}
SEEDS = {"readiness": [9101, 9102, 9103, 9104], "main": list(range(4001, 4061))}
BLOCK = 10
MAX_WORKERS = v2.MAX_WORKERS
B_PROMPTS = 4
A_MAX_TURNS = 30
B_MAX_TURNS = 100                      # one process, four prompts; the guard, not this, bounds cost
FIRST_TURN_ESTIMATE = 0.05            # worst-case turn before any has been measured (measured: <= 0.02)
GATE_PER_INSTANCE_USD = 0.15           # readiness: projected main (x60 x1.1 x1.5) must fit USD 15
REPEAT_FACTOR = 1.10                   # infrastructure repeats assumed in the projection
GATE_FACTOR = 1.5
CEILING_GATE = 0.95                    # validity gate 6: N2 at or above this is uninformative
LIVE_ENV = v2.LIVE_ENV
COST_CUMULATIVE = True                 # measured in the readiness smoke: total_cost_usd is a running total per process

# ---- prompts: fixed before the freeze; their hash goes in the gate -----------------------------
INTRO = v2.INTRO
SHARED_LINE = v2.SHARED_LINE
TASK = {"a": "Do your ticket now; stop when you have done what you can in this session.",
        "b1": "Do your ticket now; stop when you have done what you can in this session."}
# Prompts 2 to 4 of agent-b: ordinary turns. None mentions a channel, a teammate or the facts.
B_LATER = ("Continue your ticket and finish it.",
           "Run the unit tests and fix anything that fails, then finish your ticket.",
           "Make sure your ticket is complete.")
PARAGRAPHS = {
    "P": ("Team mail uses `python3 tools/agent-postbox/agent_mail.py`. To tell {other} something, "
          "run `python3 tools/agent-postbox/agent_mail.py send --type NOTICE --from {me} "
          "--to {other} --subject '<subject>' --body-file - <<'EOF'`, the text on the lines "
          "after it, and `EOF` on its own line, all in one Bash call. Write the text without curly "
          "braces."),
    "N2": ("Team notes live in ../../shared/NOTES.md, a file you both can edit. To tell {other} "
           "something, add one line to it with the Edit or Write tool, keeping the earlier lines. "
           "Add one line for each thing a teammate will need, and keep each note to one line of "
           "plain text without curly braces. Leave the heading '# Team notes' as it is."),
    "N0": "",
    "O": "",
}
ORACLE_FACT = v2.ORACLE_FACT
COMMON_ALLOW = v2.COMMON_ALLOW


def arm_order(seed: int) -> list[str]:
    order = list(ARMS)
    random.Random(f"v3-order:{seed}").shuffle(order)
    return order


def paragraph(arm: str, who: str) -> str:
    me, other = (A, B) if who == A else (B, A)
    return PARAGRAPHS[arm].format(me=me, other=other)


def paragraph_chars() -> dict[str, dict[str, int]]:
    return {arm: {who: len(paragraph(arm, who)) for who in (A, B)} for arm in ARMS}


def lengths_within(tolerance: float = 0.10) -> bool:
    """Validity gate 4: the P and N2 paragraphs are within 10% of each other, per role."""
    c = paragraph_chars()
    return all(abs(c["P"][w] - c["N2"][w]) <= tolerance * max(c["P"][w], c["N2"][w])
               for w in (A, B))


def prompts_hash() -> str:
    blob = json.dumps({"shared": SHARED_LINE, "intro": INTRO, "task": TASK, "later": B_LATER,
                       "paragraphs": PARAGRAPHS, "oracle": ORACLE_FACT, "allow": COMMON_ALLOW,
                       "p_rule_forms": ["rel", "abs-quoted-double", "abs-quoted-single", "abs"],
                       "b_prompts": B_PROMPTS}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def a_prompt(arm: str) -> str:
    parts = [INTRO[A], TASK["a"], SHARED_LINE]
    if paragraph(arm, A):
        parts.append(paragraph(arm, A))
    return " ".join(parts)


def b_prompts(arm: str, inst: v2.Instance) -> list[str]:
    """The four prompts of agent-b. Prompt 1 carries the arm's paragraph (O: the fact); the
    other three are identical in every arm."""
    parts = [INTRO[B], TASK["b1"]]
    if arm == "O":
        parts.append(ORACLE_FACT.format(d=inst.d, token=inst.token))
    else:
        parts.append(SHARED_LINE)
        if paragraph(arm, B):
            parts.append(paragraph(arm, B))
    return [" ".join(parts), *B_LATER]


def allow_rules(arm: str, proj: Path) -> tuple[str, ...]:
    """One allow-list for every arm; P adds the rules for the mail command in the four
    spellings (relative, and absolute plain/double-quoted/single-quoted), as in v2."""
    return v2.allowed(arm, proj)


def symmetry(proj: Path) -> dict[str, Any]:
    """The symmetry table (doc section 6): numbers computed from the code, not asserted."""
    inst = v2.build_instance(9101)
    out: dict[str, Any] = {}
    for arm in ARMS:
        rules = allow_rules(arm, proj)
        out[arm] = {
            "tools": "Bash,Read,Edit,Write",
            "common_allow_rules": len(COMMON_ALLOW),
            "extra_allow_rules": [r for r in rules if r not in COMMON_ALLOW],
            "add_dir_shared": arm == "N2",
            "paragraph_chars": paragraph_chars()[arm],
            "prompt_chars": [len(p) for p in b_prompts(arm, inst)],
            "later_prompts_identical": b_prompts(arm, inst)[1:] == list(B_LATER)}
    return out


# ---- sessions: specs, drivers ----------------------------------------------------------------
@dataclass
class Spec3(v2.Spec):
    turn: int = 1                      # agent-b prompt number (1..4); 0 for agent-a
    key: str = ""                      # one live agent-b process per run


class LiveDriver3:
    """The only code that starts `claude`. Agent-a: one `claude -p`. Agent-b: ONE process
    kept open with --input-format stream-json; each prompt is a user message on its stdin
    (as scripts/e2e_claude.py T3 does). Reached only from --live with POSTBOX_EXPERIMENT_LIVE=1."""

    def __init__(self) -> None:
        self.procs: dict[str, tuple[subprocess.Popen[str], queue.Queue[str | None]]] = {}

    def open_b(self, spec: Spec3, stderr_path: Path) -> None:   # pragma: no cover - never run in tests
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        err = stderr_path.open("w")
        proc = subprocess.Popen(spec.cmd + ["--input-format", "stream-json"], cwd=spec.proj,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                text=True, env=spec.env, start_new_session=True)
        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        self.procs[spec.key] = (proc, lines)

    def run(self, spec: Spec3) -> v2.Raw:   # pragma: no cover - never run in tests
        if spec.turn == 0:                    # agent-a: a single-shot session
            try:
                cp = subprocess.run(spec.cmd, input=spec.prompt, cwd=spec.proj, capture_output=True,
                                    text=True, env=spec.env, timeout=e2e.TIMEOUT)
                return v2.Raw(cp.stdout, cp.stderr, cp.returncode)
            except subprocess.TimeoutExpired as e:
                out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
                return v2.Raw(out, "", None, True)
        proc, lines = self.procs[spec.key]
        got: list[str] = []
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps({"type": "user", "message": {
                "role": "user", "content": [{"type": "text", "text": spec.prompt}]}}) + "\n")
            proc.stdin.flush()
        except OSError:
            return v2.Raw("", "agent-b process is gone", proc.poll())
        deadline = time.monotonic() + e2e.TIMEOUT
        while True:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                return v2.Raw("".join(got), "", None, True)
            if line is None:                  # the process ended before a result event
                proc.wait(timeout=10)
                return v2.Raw("".join(got), "process ended", proc.returncode)
            got.append(line)
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                return v2.Raw("".join(got), "", None)

    def close_b(self, key: str) -> None:   # pragma: no cover - never run in tests
        entry = self.procs.pop(key, None)
        if not entry:
            return
        proc, _ = entry
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()


class FakeDriver3(v2.FakeDriver):
    """SIMULATION. Plays both agents without a model: it edits files like a (simple) agent,
    uses the real mailbox CLI and the real hook, and emits stream-json events of the host's
    shape. `look_turn[arm]` is the B prompt at which a simulated B looks at the channel (None:
    never). Nothing here is observed behaviour of any model."""

    def __init__(self, *, look_turn: dict[str, int | None] | None = None, cost: float = 0.02,
                 fail_first: set[tuple[int, str]] | None = None,
                 rate_limit: set[tuple[int, str]] | None = None,
                 mention_mailbox: set[str] | None = None,
                 refuse_send: bool = False) -> None:
        super().__init__(cost=cost, fail_first=fail_first, rate_limit=rate_limit,
                         mention_mailbox=mention_mailbox)
        self.pickup = {a: False for a in ("P", "N2", "N1", "N0", "O")}
        self.look_turn: dict[str, int | None] = {"P": 2, "N2": None, "N0": None, "O": None}
        self.look_turn.update(look_turn or {})
        self.refuse_send = refuse_send
        self.opened: list[str] = []
        self.closed: list[str] = []

    def open_b(self, spec: Spec3, stderr_path: Path) -> None:
        self.opened.append(spec.key)

    def close_b(self, key: str) -> None:
        self.closed.append(key)

    def run(self, spec: Spec3) -> v2.Raw:     # type: ignore[override]
        with self.lock:
            self.calls.append(spec)
        key = (spec.inst.seed, spec.arm)
        events: list[dict[str, Any]] = [{"type": "system", "subtype": "init",
                                         "session_id": f"fake-{spec.tag}", "model": "fake-model"}]
        denials: list[dict[str, Any]] = []
        limited = key in self.rate_limit
        if limited:
            events.append({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        elif key in self.fail_first and spec.tag == "b1":
            self.fail_first.discard(key)
            return v2.Raw("", "host crashed", 1)
        else:
            self.behave3(spec, events, denials)
        events.append({
            "type": "result", "subtype": "success", "is_error": False, "num_turns": 6,
            "result": "done", "total_cost_usd": 0.0 if limited else self.cost,
            "permission_denials": denials,
            "usage": {"input_tokens": 1000, "output_tokens": 200,
                      "cache_creation_input_tokens": 300, "cache_read_input_tokens": 4000},
            "modelUsage": {"fake-model": {"inputTokens": 1000, "outputTokens": 200,
                                          "costUSD": self.cost}}})
        return v2.Raw("\n".join(json.dumps(e) for e in events) + "\n")

    def behave3(self, spec: Spec3, events: list[dict[str, Any]],
                denials: list[dict[str, Any]]) -> None:
        if spec.who == A and spec.arm == "P" and self.refuse_send:
            # simulate a host refusal of the send command: no message is written
            cmd = f"python3 {v2.DEST}/agent_mail.py send --type NOTICE --body-file -"
            self.use(events, "Bash", command=cmd)
            denials.append({"tool_name": "Bash", "tool_input": {"command": cmd}})
            return
        if spec.who == A:
            return self.behave(spec, events)
        if spec.turn == 1:
            spec.stage = "s1"
            return self.behave(spec, events)
        look = self.look_turn.get(spec.arm)
        # v2's fake looks at the channel only in stage "s4"; its hook (P) prints in every stage
        spec.stage = "s4" if look is not None and spec.turn == look else "s3"
        self.behave(spec, events)


# ---- the harness -----------------------------------------------------------------------------
class Harness(v2.Harness):
    """v2's harness with a multi-prompt agent-b. Reuses v2's guard, session accounting,
    build (project, shared dir, mailbox omission checks), mailbox reading and block scheduling."""

    def __init__(self, args: argparse.Namespace, claude: str, work: Path, out: Path,
                 driver: Any, sleep: Any = time.sleep) -> None:
        super().__init__(args, claude, work, out, driver, sleep)
        self.spent = float(getattr(args, "prior_spend_usd", 0.0) or 0.0)
        self.cum_cost: dict[str, float] = {}
        self.biggest_turn = 0.0

    def reserve(self) -> None:
        """v2's guard with a turn-sized worst case (v2's 0.30 first-session estimate is for a
        whole three-session run). Hard stop: spent + 1.5 x the largest turn so far x (turns in
        flight + this one) must not pass the ceiling. A turn's cost is the difference of the
        host's running total (see COST_CUMULATIVE), so it is small."""
        with self.lock:
            if self.stop:
                raise micro.Ceiling(self.stop)
            worst = (self.biggest_turn or FIRST_TURN_ESTIMATE) * 1.5
            if self.spent + worst * (self.inflight + 1) > self.args.ceiling_usd:
                raise micro.Ceiling(f"spent ${self.spent:.4f} + {self.inflight + 1} in flight "
                                    f"x ${worst:.4f} would pass ${self.args.ceiling_usd:.2f}")
            self.inflight += 1

    def absorb(self, s: e2e.Session, ev: dict[str, Any]) -> None:
        super().absorb(s, ev)
        if ev.get("type") == "result":
            s.result_subtype = str(ev.get("subtype") or "")      # type: ignore[attr-defined]

    def one_session(  # type: ignore[override]
            self, spec: Spec3, tag_dir: str) -> tuple[v2.XSession, dict[str, Any]]:
        s, rec = super().one_session(spec, tag_dir)
        rec["turn"] = spec.turn
        rec["result_subtype"] = getattr(s, "result_subtype", "")
        rec["cost_reported"] = rec["cost_usd"]
        rec["denial_details"] = [
            {"tool": d.get("tool_name"), "input": json.dumps(d.get("tool_input"))[:300]}
            for d in s.denials if isinstance(d, dict)]
        if COST_CUMULATIVE and spec.turn > 1 and rec["cost_usd"] is not None:
            # the host's total_cost_usd is a running total for the process: bill the difference
            prev = self.cum_cost.get(spec.key, 0.0)
            delta = max(0.0, rec["cost_usd"] - prev)
            with self.lock:
                self.spent += delta - rec["cost_usd"]
            rec["cost_usd"] = delta
        if rec["cost_reported"] is not None and spec.turn >= 1:
            self.cum_cost[spec.key] = rec["cost_reported"]
        with self.lock:
            self.biggest_turn = max(self.biggest_turn, rec["cost_usd"] or 0.0)
        with self.lock:
            (self.out).mkdir(parents=True, exist_ok=True)
            (self.out / "spend.json").write_text(json.dumps({
                "spent_usd": round(self.spent, 6), "ceiling_usd": self.args.ceiling_usd,
                "sessions": self.n_sessions}), encoding="utf-8")
        return s, rec

    def spec(self, arm: str, who: str, tag: str, turn: int, proj: Path, prompt: str,
             shared: Path, root: Path, extra: dict[str, str], inst: v2.Instance,
             key: str) -> Spec3:
        other = A if who == B else B
        max_turns = B_MAX_TURNS if who == B else A_MAX_TURNS
        cmd = self.command(arm, proj, shared, v2.isolation_rules(root, other))
        cmd[cmd.index("--max-turns") + 1] = str(max_turns)
        return Spec3(tag, arm, who, "s1", proj, prompt, self.env(who, extra), cmd, inst, shared,
                     turn=turn, key=key)

    def run_arm(self, seed: int, arm: str, rep: int = 0, attempt: int = 1) -> dict[str, Any]:
        inst = v2.build_instance(seed)
        order = arm_order(seed)
        rec: dict[str, Any] = {
            "schema": SCHEMA, "seed": seed, "arm": arm, "rep": rep, "attempt": attempt,
            "d": inst.d, "token": inst.token, "sessions": [], "manual_nudges": 0,
            "paragraph_chars": paragraph_chars()[arm], "infra_failure": "",
            "model": self.args.model, "effort": self.args.effort, "order": order,
            "order_index": order.index(arm), "b_ok_by_prompt": []}
        tag_dir = f"{seed}-{arm}-r{rep}-t{attempt}"
        key = f"{os.getpid()}-{tag_dir}"
        root = Path(tempfile.mkdtemp(prefix=f"v3-{tag_dir}-", dir=self.work))
        t0 = time.monotonic()
        opened = False
        try:
            a, b, shared, extra = self.build(inst, arm, root)
            prompts = b_prompts(arm, inst)

            def go(spec: Spec3) -> None:
                _, srec = self.one_session(spec, tag_dir)
                rec["sessions"].append(srec)
                if srec["infra"] and not rec["infra_failure"]:
                    rec["infra_failure"] = srec["infra"]

            def b_spec(k: int) -> Spec3:
                return self.spec(arm, B, f"b{k}", k, b, prompts[k - 1], shared, root, extra, inst,
                                 key)

            self.driver.open_b(  # type: ignore[attr-defined]
                b_spec(1), self.out / "raw" / f"{tag_dir}-b.stderr")
            opened = True
            for k in range(1, B_PROMPTS + 1):
                go(b_spec(k))
                if rec["infra_failure"]:
                    break
                rec["b_ok_by_prompt"].append(bool(v2.score(inst, a, b)["b_ok"]))
                if k == 1 and arm != "O":
                    go(self.spec(arm, A, "a", 0, a, a_prompt(arm), shared, root, extra, inst, key))
                    if rec["infra_failure"]:
                        break
            if not rec["infra_failure"]:
                rec.update(self.score_run(inst, arm, a, b, shared, extra, rec))
        except micro.Ceiling as e:
            rec["aborted"] = f"stopped: {e}"
        finally:
            if opened:
                self.driver.close_b(key)  # type: ignore[attr-defined]
            shutil.rmtree(root, ignore_errors=True)
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec["cost_usd"] = round(sum(s["cost_usd"] or 0.0 for s in rec["sessions"]), 6)
        rec["denials"] = sum(s["denials"] for s in rec["sessions"])
        rec["channel_denials"] = sum(s["channel_denials"] for s in rec["sessions"])
        rec["host_refusals"] = [dict(d, tag=s["tag"]) for s in rec["sessions"]
                                for d in s["denial_details"]
                                if "agent_mail.py" in d["input"] or v2.NOTES_REL in d["input"]]
        rec["cross_checkout"] = any(s["cross_checkout"] for s in rec["sessions"])
        rec["mailbox_mention"] = arm != "P" and any(s["mentions_mailbox"] for s in rec["sessions"])
        runs = self.out / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{tag_dir}.json").write_text(json.dumps(rec, indent=2, sort_keys=True),
                                              encoding="utf-8")
        return rec

    def score_run(self, inst: v2.Instance, arm: str, a: Path, b: Path, shared: Path,
                  extra: dict[str, str], rec: dict[str, Any]) -> dict[str, Any]:
        out = v2.score(inst, a, b)
        later = [s for s in rec["sessions"] if s["agent"] == B and s["turn"] >= 2]
        gen, a_wrote = 0, False
        delivered: bool | None = None
        picked: bool | None = None
        ack: bool | None = None
        first_look: int | None = None
        if arm == "P":
            msgs = self.mailbox(a, extra)
            gen = len(msgs)
            from_a = [m for m in msgs if m.get("from") == A]
            a_wrote = bool(from_a)
            delivered = any(m.get("subject") and m["subject"] in s["hook_stdout"]
                            for m in from_a for s in later)
            looked = [s["turn"] for s in later if s["runs_show"]]
            picked = bool(looked)
            first_look = min(looked) if looked else None
            ids = {m["id"] for m in from_a}
            ack = any(m.get("from") == B and m.get("reply_to") in ids for m in msgs)
        elif arm == "N2":
            notes = (shared / v2.NOTES_REL).read_text(encoding="utf-8")
            gen = max(0, len([ln for ln in notes.splitlines() if ln.strip()]) - 1)
            a_wrote = gen > 0
            looked = [s["turn"] for s in later if s["reads_notes"]]
            picked = bool(looked)
            first_look = min(looked) if looked else None
        out.update(generated=gen, delivered=delivered, picked_up=picked, acknowledged=ack,
                   first_look_prompt=first_look, a_wrote=a_wrote)
        out["missed"] = a_wrote and not out["b_ok"]
        return out

    def run_instance(self, seed: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for arm in arm_order(seed):
            try:
                recs = self.run_with_retry(seed, arm, 0)
            except micro.Ceiling as e:
                records.append({"seed": seed, "arm": arm, "rep": 0, "aborted": str(e)})
                return records
            records += recs
            if "aborted" in recs[-1] or self.stop:
                return records
        return records


def blocks(seeds: list[int]) -> list[list[int]]:
    return v2.blocks(seeds, BLOCK)


def stop_reason(records: list[dict[str, Any]], seeds_done: list[int]) -> str | None:
    """Main-stage stop rules on the first 20 instances: v2's (infrastructure, O, N0) plus the
    ceiling gate on the CONTROL arm only (N2 at or above 95%). None looks at P against N2."""
    why = v2.stop_reason(records, seeds_done)
    if why:
        return why
    first = set(sorted(seeds_done)[:20])
    if len(first) < 20:
        return None
    recs = [r for r in v2.final_records(records) if r["seed"] in first and "aborted" not in r]
    n2 = [r for r in recs if r["arm"] == "N2" and not r.get("infra_failure") and "b_ok" in r]
    if n2 and sum(r["b_ok"] for r in n2) / len(n2) >= CEILING_GATE:
        return ("validity gate 6 on the first 20 instances: N2 b_ok at or above 95% (control at "
                "the ceiling; the contrast is declared uninformative and the run stops)")
    return None


# ---- plan, projection, readiness gate ---------------------------------------------------------
def plan_sessions(stage: str, n: int | None = None) -> dict[str, int]:
    n = len(SEEDS[stage]) if n is None else n
    per = 3 * (B_PROMPTS + 1) + B_PROMPTS          # P, N2, N0: 4 B turns + 1 A; O: 4 B turns
    return {"instances": n, "per_instance": per, "turns": per * n,
            "with_repeats": round(per * n * REPEAT_FACTOR)}


def projection(mean_per_instance: float, n: int = 60) -> dict[str, float]:
    base = mean_per_instance * n
    return {"expected": round(base * REPEAT_FACTOR, 2),
            "with_1.5_margin": round(base * REPEAT_FACTOR * GATE_FACTOR, 2)}


def readiness_gate(records: list[dict[str, Any]], spent: float, args: argparse.Namespace,
                   offline: list[tuple[str, bool]], symm: dict[str, Any]) -> dict[str, Any]:
    recs = [r for r in v2.final_records(records) if "aborted" not in r]
    sess = [s for r in recs for s in r["sessions"]]
    costs = [s["cost_usd"] for s in sess if s["cost_usd"] is not None]
    by = {arm: [r for r in recs if r["arm"] == arm] for arm in ARMS}
    per_inst: dict[int, float] = {}
    for r in recs:
        per_inst[r["seed"]] = per_inst.get(r["seed"], 0.0) + r["cost_usd"]
    mean_inst = sum(per_inst.values()) / len(per_inst) if per_inst else float("inf")
    proj = projection(mean_inst) if per_inst else {"expected": float("inf"),
                                                   "with_1.5_margin": float("inf")}

    def complete(r: dict[str, Any]) -> bool:
        want = B_PROMPTS + (0 if r["arm"] == "O" else 1)
        return not r.get("infra_failure") and "b_ok" in r and len(r["sessions"]) == want

    checks = {"offline: " + n: ok for n, ok in offline}
    checks.update({
        "every arm completed at least one instance end to end": all(
            any(complete(r) for r in by[a]) for a in ARMS),
        "pipeline: every session that ended has cost, turns, usage, model": bool(sess) and all(
            s["cost_usd"] is not None and s["turns"] is not None and s["usage"] and s["model"]
            for s in sess if not s.get("infra")),
        "P: agent-a's message was sent and delivered by the hook at a later prompt": bool(by["P"])
        and all(r.get("a_wrote") and r.get("delivered") for r in by["P"] if complete(r)),
        "no call to a communication channel (mail command, notes file) was refused": not any(
            r.get("channel_denials", 0) for r in recs),
        "O reaches b_ok": bool(by["O"]) and all(r["b_ok"] for r in by["O"] if complete(r)),
        "N0 does not reach b_ok": not any(r["b_ok"] for r in by["N0"] if complete(r)),
        "no mailbox mention in any non-P run": not any(r.get("mailbox_mention") for r in recs),
        "P and N2 paragraphs within 10%": lengths_within(),
        "symmetry: later prompts identical, same tools, same common allow-list": all(
            v["later_prompts_identical"] and v["tools"] == "Bash,Read,Edit,Write"
            and v["common_allow_rules"] == len(COMMON_ALLOW) for v in symm.values()),
        "no cross-checkout attempts": not any(r.get("cross_checkout") for r in recs),
        f"cost gate: projected main (x60 x{REPEAT_FACTOR} x{GATE_FACTOR}) <= "
        f"${args.projection_cap}": proj["with_1.5_margin"] <= args.projection_cap})
    return {"schema": SCHEMA, "passed": all(checks.values()), "dry_run": bool(args.dry_run),
            "checks": checks, "mean_cost_per_instance": round(mean_inst, 6) if per_inst else None,
            "projected_main_usd": proj, "sessions": len(sess), "spent_usd": round(spent, 4),
            "mean_cost_per_turn": round(sum(costs) / len(costs), 6) if costs else None,
            "model": args.model, "effort": args.effort, "prompts_sha256": prompts_hash(),
            "script_sha256": sha(Path(__file__)),
            "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def check_gate(path: str | None, args: argparse.Namespace) -> str | None:
    """Why the main stage may not start, or None."""
    if not path:
        return "the main stage needs --gate PATH (the readiness stage's readiness_gate.json)"
    p = Path(path)
    if not p.is_file():
        return f"readiness gate {p} not found: run the readiness stage first"
    try:
        gate = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return f"readiness gate {p} is not valid JSON"
    if gate.get("dry_run"):
        return "the readiness gate came from a --dry-run, not a live readiness stage"
    if not gate.get("passed"):
        failed = [k for k, v in (gate.get("checks") or {}).items() if not v]
        return f"the readiness gate did not pass ({failed}); the owner decides"
    for key, want in (("prompts_sha256", prompts_hash()), ("model", args.model),
                      ("effort", args.effort)):
        if gate.get(key) != want:
            return (f"the readiness gate was passed with a different {key}; a change to arm "
                    "prompts, model or effort restarts readiness")
    return None


# ---- command line ----------------------------------------------------------------------------
def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


FREEZE_FILES = ("scripts/experiment_v3.py", "scripts/analyze_v3.py", "test_experiment_v3.py",
                "scripts/experiment_v2.py", "scripts/analyze_v2.py", "scripts/experiment_micro.py",
                "scripts/e2e_claude.py", "agent_mail.py", "install.py")


def freeze() -> str:
    lines = [f"{sha(ROOT / f)}  {f}" for f in FREEZE_FILES if (ROOT / f).is_file()]
    lines.append(f"{prompts_hash()}  prompts_hash() (experiment_v3.py)")
    return "\n".join(lines)


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Experiment v3 harness (docs/experiment-v3.md)")
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--out", help="directory for raw results; OUTSIDE the repository, new or empty")
    ap.add_argument("--dry-run", action="store_true", help="fake driver: plan, no cost")
    ap.add_argument("--live", action="store_true",
                    help=f"real `claude` sessions (spends usage); needs {LIVE_ENV}=1")
    ap.add_argument("--freeze", action="store_true", help="print the sha256 freeze table and exit")
    ap.add_argument("--probe", action="store_true",
                    help="readiness: a tiny live probe of the P arm's allow rules (needs --live)")
    ap.add_argument("--gate", metavar="PATH", help="main stage: the readiness_gate.json")
    ap.add_argument("--ceiling-usd", type=float, help="hard spend ceiling (readiness 1, main 15)")
    ap.add_argument("--prior-spend-usd", type=float, default=0.0,
                    help="spend of earlier invocations counted against the ceiling")
    ap.add_argument("--projection-cap", type=float, default=CEILING["main"])
    ap.add_argument("--instances", type=int, help="run only N seeds of the stage")
    ap.add_argument("--skip-instances", type=int, default=0,
                    help="skip the first K seeds of the stage (readiness: continue a smoke)")
    ap.add_argument("--model", default=e2e.DEFAULT_MODEL)
    ap.add_argument("--effort", default=v2.DEFAULT_EFFORT)
    ap.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"at most {MAX_WORKERS}")
    ap.add_argument("--fake-cost", type=float, default=0.02, help="dry run: fake USD per turn")
    ap.add_argument("--claude", metavar="PATH")
    ap.add_argument("--workdir", metavar="DIR")
    args = ap.parse_args(argv)
    if not args.freeze and not args.stage:
        ap.error("--stage is required")
    if not args.freeze and not args.out:
        ap.error("--out is required")
    if args.ceiling_usd is None and args.stage:
        args.ceiling_usd = CEILING[args.stage]
    args.max_turns = A_MAX_TURNS
    args.max_calls = 10**6
    return args


def refuse(msg: str) -> int:
    print(f"refused: {msg}", file=sys.stderr)
    return 2


def inside_repo(path: Path) -> bool:
    p = path.resolve()
    return p == ROOT or ROOT in p.parents


def print_plan(args: argparse.Namespace, n: int) -> None:
    plan = plan_sessions(args.stage, n)
    print(f"stage={args.stage} model={args.model} effort={args.effort} "
          f"ceiling=${args.ceiling_usd:.2f} (prior spend ${args.prior_spend_usd:.2f})")
    print(f"instances={n}; per instance {plan['per_instance']} turns "
          f"(P,N2,N0: 4 B prompts + 1 A session; O: 4 B prompts); total {plan['turns']} turns, "
          f"{plan['with_repeats']} with assumed repeats")
    chars = paragraph_chars()
    print("arm paragraph characters (agent-a/agent-b): "
          + ", ".join(f"{a}={chars[a][A]}/{chars[a][B]}" for a in ARMS)
          + f"; P vs N2 within 10%: {lengths_within()}")
    print(f"prompts sha256 {prompts_hash()}")


def execute(args: argparse.Namespace, driver: Any, claude: str, out: Path,
            seeds: list[int]) -> tuple[Harness, list[dict[str, Any]]]:
    work = e2e.prepare_workdir(Path(args.workdir) if args.workdir
                               else Path(tempfile.gettempdir()) / f"v3-work-{os.getpid()}")
    h = Harness(args, claude, work, out, driver)
    records: list[dict[str, Any]] = []
    done: list[int] = []
    try:
        for block in blocks(seeds):
            records += h.run_block(block)
            done += block
            print(f"block of {len(block)} done: turns={h.n_sessions} spent=${h.spent:.4f} "
                  f"(ceiling ${args.ceiling_usd:.2f})")
            why = h.stop or (stop_reason(records, done) if args.stage == "main" else None)
            if why or any("aborted" in r for r in records):
                h.stop = h.stop or why
                print(f"STOP: {h.stop or 'spend ceiling'}; instances completed or started: "
                      f"{len(done)}")
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return h, records


def manifest(args: argparse.Namespace, version: str, seeds: list[int], proj: Path) -> dict[str, Any]:
    base = e2e.Runner.base_cmd(argparse.Namespace(args=args, claude="claude"),  # type: ignore[arg-type]
                               tools="Bash,Read,Edit,Write", allowed=(), max_turns=A_MAX_TURNS)
    return {"schema": SCHEMA, "stage": args.stage, "claude_version": version,
            "model": args.model, "effort": args.effort, "permission_mode": "dontAsk",
            "tools": "Bash,Read,Edit,Write", "base_command": base,
            "allow_rules": {a: list(allow_rules(a, proj)) for a in ARMS},
            "deny_rules_template": ["Read(/<other container>/**)", "Edit(...)", "Write(...)",
                                    "Bash(*<other container>*)"],
            "add_dir_shared": ["N2"], "seeds": seeds,
            "order": {str(s): arm_order(s) for s in seeds},
            "ceiling_usd": args.ceiling_usd, "prior_spend_usd": args.prior_spend_usd,
            "prompts_sha256": prompts_hash(), "script_sha256": sha(Path(__file__)),
            "analysis_sha256": sha(Path(__file__).with_name("analyze_v3.py")),
            "python": sys.version.split()[0], "platform": platform.platform(),
            "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "argv": sys.argv[1:], "cost_cumulative_assumed": COST_CUMULATIVE}


def probe_cases(a: Path, inst: v2.Instance) -> dict[str, tuple[str, bool | None, str]]:
    """(command, expected allowed, marker the written body must contain) for the P arm's exact
    send forms, with the harness's own allow rules. The `#` lines are the case v2 lost 9 runs to.
    `NEGATIVE_CONTROL` chains an unallowed command (the host refuses it): it shows that this probe
    can see a refusal at all. `INFO_` cases are recorded, not scored (expected None)."""
    text = f"# Vendor facts\namounts are 1/{inst.d} of the currency\nvoided rows: {inst.token}"
    head = "send --type NOTICE --from agent-a --to agent-b --subject 'vendor facts'"
    rel = f"python3 {v2.DEST}/agent_mail.py"
    absq = f'python3 "{a / v2.DEST / "agent_mail.py"}"'
    heredoc = f"--body-file - <<'EOF'\n{text}\nEOF"
    return {
        "relative_heredoc_with_hash_lines": (f"{rel} {head} {heredoc}", True, inst.token),
        "absolute_quoted_heredoc_with_hash_lines": (f"{absq} {head} {heredoc}", True, inst.token),
        "relative_heredoc_plain_text": (
            f"{rel} {head} --body-file - <<'EOF'\nplain line\nEOF", True, "plain line"),
        "NEGATIVE_CONTROL_chained_unallowed_command": (
            f"{rel} {head} --body-file - <<'EOF' && python3 -c 1\nx\nEOF", False, "x"),
        "INFO_heredoc_body_with_backticks": (
            f"{rel} {head} --body-file - <<'EOF'\nrun `python3 -m unittest discover tests`\nEOF",
            None, "unittest discover"),
        "INFO_heredoc_body_with_braces_and_quotes": (
            f"{rel} {head} --body-file - <<'EOF'\nreturns {{'count': live, 'total': x}}\nEOF",
            None, "'count': live"),
        "INFO_heredoc_body_with_blank_line_and_dash_list": (
            f"{rel} {head} --body-file - <<'EOF'\nfirst\n\n- item one\n- item two\nEOF",
            None, "item two"),
        "INFO_heredoc_body_with_dollar_and_parens": (
            f"{rel} {head} --body-file - <<'EOF'\ncost $5 (approx) and $(not run)\nEOF",
            None, "approx"),
        "INFO_inline_multiline_body_with_hash_line": (
            f"{rel} {head} --body '{text}'", None, inst.token)}


def run_probe(args: argparse.Namespace, claude: str, out: Path) -> int:   # pragma: no cover - live
    """A tiny live probe (about USD 0.003 per case): do the P arm's allow rules let its taught send
    forms through under --permission-mode dontAsk, and does the body arrive intact?"""
    work = e2e.prepare_workdir(Path(args.workdir) if args.workdir
                               else Path(tempfile.gettempdir()) / f"v3-probe-{os.getpid()}")
    h = Harness(args, claude, work, out, LiveDriver3())
    inst = v2.build_instance(SEEDS["readiness"][0])
    results: dict[str, Any] = {}
    try:
        for name in probe_cases(Path("/x"), inst):
            root = Path(tempfile.mkdtemp(prefix="v3-probe-", dir=work))
            a, _, shared, extra = h.build(inst, "P", root)
            cmd_text, expect_allowed, marker = probe_cases(a, inst)[name]
            cmd = h.command("P", a, shared, v2.isolation_rules(root, B))
            h.reserve()
            s = e2e.Session()
            try:
                cp = subprocess.run(cmd, input="Run exactly this one Bash command, with no "
                                    "changes, then say done:\n" + cmd_text, cwd=a,
                                    capture_output=True, text=True, env=h.env(A, extra),
                                    timeout=e2e.TIMEOUT)
                raw = cp.stdout
            except subprocess.TimeoutExpired:
                raw = ""
            finally:
                h.inflight -= 1
            for line in raw.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    h.absorb(s, ev)
            (out / "raw").mkdir(parents=True, exist_ok=True)
            (out / "raw" / f"probe-{name}.jsonl").write_text(raw, encoding="utf-8")
            h.spent += s.cost_usd or 0.0
            h.biggest = max(h.biggest, s.cost_usd or 0.0)
            msgs = [p for p in (shared / "agent-mail").rglob("*.md")
                    if p.is_file() and "vendor facts" in p.read_text(encoding="utf-8")]
            intact = any(marker in p.read_text(encoding="utf-8") for p in msgs)
            denied = [d for d in s.denials if isinstance(d, dict)]
            ran = bool(msgs)
            results[name] = {"expected_allowed": expect_allowed, "denied": len(denied),
                             "message_written": ran, "body_intact": intact,
                             "pass": None if expect_allowed is None else
                             ((len(denied) == 0 and ran and intact) if expect_allowed
                              else (len(denied) > 0 and not ran)),
                             "cost_usd": s.cost_usd,
                             "denied_input": [json.dumps(d.get("tool_input"))[:160] for d in denied]}
            print(f"{name:48} denied={len(denied)} written={ran} intact={intact} "
                  f"pass={results[name]['pass']}")
            shutil.rmtree(root, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
    (out / "probe.json").write_text(json.dumps({
        "claude_version": version, "model": args.model, "permission_mode": "dontAsk",
        "allow_rules_P_template": list(allow_rules("P", Path("<checkout>"))),
        "cases": results, "spent_usd": round(h.spent - args.prior_spend_usd, 4),
        "all_pass": all(r["pass"] for r in results.values() if r["pass"] is not None)},
        indent=2, sort_keys=True))
    ok = all(r["pass"] for r in results.values() if r["pass"] is not None)
    print(f"probe spent ${h.spent - args.prior_spend_usd:.4f}; all_pass={ok}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.freeze:
        print(freeze())
        return 0
    if args.dry_run == args.live:
        return refuse("pass exactly one of --dry-run (fake driver, free) or --live")
    if args.live and os.environ.get(LIVE_ENV) != "1":
        return refuse(f"--live spends usage; set {LIVE_ENV}=1 to confirm")
    if args.ceiling_usd <= 0:
        return refuse("--ceiling-usd must be positive")
    if args.ceiling_usd - args.prior_spend_usd <= 0:
        return refuse("the prior spend already reaches the ceiling")
    out = Path(args.out)
    if inside_repo(out):
        return refuse(f"--out {out} is inside the repository; raw data stays outside it")
    if out.exists() and any(out.iterdir()):
        return refuse(f"--out {out} exists and is not empty")
    seeds = SEEDS[args.stage][args.skip_instances:]
    seeds = seeds[:args.instances] if args.instances else seeds
    print_plan(args, len(seeds))
    offline = v2.offline_checks()
    failed = [n for n, ok in offline if not ok]
    if args.dry_run:
        print("offline checks:", "ok" if not failed else f"FAILED {failed}")
        out.mkdir(parents=True, exist_ok=True)
        h, records = execute(args, FakeDriver3(cost=args.fake_cost), "fake", out, seeds)
        print(f"dry run (SIMULATION, fake agents): {len(v2.final_records(records))} runs, "
              f"{h.n_sessions} fake turns, spent ${h.spent:.4f} (fake costs; nothing was spent)")
        if args.stage == "readiness":
            with tempfile.TemporaryDirectory(prefix="v3-sym-") as tmp:
                symm = symmetry(Path(tmp))
            gate = readiness_gate(records, h.spent, args, offline, symm)
            gate["dry_run"] = True
            (out / "readiness_gate.json").write_text(json.dumps(gate, indent=2, sort_keys=True))
            print("dry-run gate written (marked dry_run: the main stage refuses it)")
        return 1 if failed else 0
    if args.stage == "main":
        why = check_gate(args.gate, args)
        if why:
            return refuse(why)
    claude = args.claude or shutil.which("claude")
    if not claude:
        return refuse("`claude` is not on PATH")
    if failed:
        return refuse(f"offline checks failed: {failed}")
    if not lengths_within():
        return refuse("P and N2 arm paragraphs differ by more than 10%")
    version, reason = e2e.claude_status(claude)
    if reason:
        return refuse(reason)
    full_version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
    out.mkdir(parents=True, exist_ok=True)
    if args.probe:
        return run_probe(args, claude, out)
    with tempfile.TemporaryDirectory(prefix="v3-sym-") as tmp:
        symm = symmetry(Path(tmp))
        (out / "manifest.json").write_text(json.dumps(
            dict(manifest(args, full_version or str(version), seeds, Path(tmp)), symmetry=symm),
            indent=2, sort_keys=True), encoding="utf-8")
    h, records = execute(args, LiveDriver3(), claude, out, seeds)
    (out / "run_summary.json").write_text(json.dumps({
        "schema": SCHEMA, "stage": args.stage, "turns": h.n_sessions,
        "spent_usd": round(h.spent, 4), "ceiling_usd": args.ceiling_usd,
        "models_reported": sorted(h.models), "stopped": h.stop}, indent=2, sort_keys=True))
    if args.stage == "readiness":
        gate = readiness_gate(records, h.spent, args, offline, symm)
        (out / "readiness_gate.json").write_text(json.dumps(gate, indent=2, sort_keys=True))
        print(f"readiness gate passed={gate['passed']} -> {out / 'readiness_gate.json'}")
        print(json.dumps(gate["checks"], indent=2))
    print(f"turns={h.n_sessions} spent=${h.spent:.4f}; raw results in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
