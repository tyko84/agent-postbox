#!/usr/bin/env python3
"""Scenario harness: a reproducible benchmark and reliability run of agent-postbox.

    python scripts/scenario.py [--quick] [--seed N] [--agents N] [--messages N]
                               [--rounds N] [--drain N] [--concurrency N]
                               [--config hook|baseline|both] [--poll-p P]
                               [--faults all|none|NAME[,NAME]] [--json PATH]
                               [--recovery-bound N] [--max-seconds S]
                               [--pickup direct|codex]

Simulated agents drive the real tool: every send is `agent_mail.py send` as a
subprocess, every pickup is the real adapter `hooks/agent_mail_check.py` (the
`hook` configuration) or the real `agent_mail.py inbox --to <me> --json` (the
`baseline` configuration), and the operator's view at every boundary is the
real `agent_mail.py status --json`. Nothing of the protocol is reimplemented
here; the harness only schedules, invokes, parses and counts. There is no LLM
and no host: a "prompt boundary" is the harness calling the pickup, the way a
host's prompt-submit event would.

A run is `rounds` rounds of three phases, then `drain` more boundaries with no
scheduled sends: (1) every agent performs the sends the schedule gives it for
this round, concurrently; (2) the prompt boundary: every awake agent runs its
pickup, concurrently, and `status --json` is read once; (3) every agent answers
the ASKs its pickup surfaced (an ANSWER with --reply-to and --key: reactive
sends are the one thing the schedule does not fix, because they depend on
delivery, and "unanswered ASKs at end" measures exactly that).

The workload is a pure function of --seed and the parameters: the same values
give the same senders, recipients, types, subjects, keys, scopes, leases and
fault schedule, byte for byte. Its SHA-256 (`schedule_hash`) is printed for
every configuration and must agree between `hook` and `baseline`; that is a
checked invariant. Timing is not deterministic, so latencies vary between
runs; counts of what was sent do not.

The baseline models an agent that only checks its inbox when it remembers to:
at each boundary it polls with probability --poll-p (default 0.5), from a
seeded table of its own (`poll_hash`), and forgets with probability 1-p. That
is an ASSUMPTION about agent behaviour, not a measurement of real agents; the
baseline is the comparison, not the system under test, so its numbers are
reported and never thresholded.

Faults, each switchable with --faults and scheduled by the seed:
  interrupted-writes  a sender is SIGKILLed after writing its temp file and
                      before linking it (the real send, with os.link made to
                      block); it leaves one hidden .tmp and a locked key marker
                      with no message; the same sender retries the same keyed
                      send next round, which must take the key over and write
                      exactly one message
  abandoned-locks     a claimant is SIGKILLed while holding the kernel scope
                      lock (the real send, with the lock function made to
                      block once it holds the lock); the lock file stays with
                      no holder; next round the holder retries its claim and
                      another agent's CLAIM on that scope (added to the
                      schedule) contends with it: the orphan lock must be
                      reclaimed by one of them, the other is refused (exit 3)
                      or wins, and no lock file stays
  crashes             an agent stops responding for K rounds (no sends, no
                      pickup, no answers) and then resumes; its sends are
                      deferred to the round it resumes
  malformed           a file with `type: ACK` addressed to an agent is planted
                      in the mailbox; it must be quarantined, counted by
                      `status`, and never surfaced by any pickup
  delayed-polling     an agent keeps sending but skips its pickup for K rounds

Thresholds (the `hook` configuration only), one OK/FAIL line each, in the
style of scripts/verify_release.py: zero missed messages, zero duplicate
deliveries, zero conflicting live claims, every planted file quarantined and
never surfaced, recovery within --recovery-bound boundaries after every
injected crash, every send's exit status as expected (0, or 3 for a CLAIM on a
scope another agent holds, or killed when the fault schedule says so), the
final `status` clean apart from what it must report (the quarantined files;
see `excused` below), no scope lock file left and exactly one .tmp per sender
killed mid-write, every pickup exit 0, and a positive control (at least one
message surfaced: silence proves nothing, PROTOCOL.md section 8). The
schedule-hash invariant applies to both configurations.

Latency is measured from the moment the sender's `send` returned to the
moment the recipient's pickup that first surfaced the message returned, in
seconds and in prompt boundaries (0: the first boundary after the send).
Percentiles are nearest-rank and printed
only when the sample justifies them: p50 needs n >= 10, p95 n >= 20, p99
n >= 100; otherwise `n/a (n=...)`. min, max, mean and n are always printed.

--json PATH writes one document (keys are only ever added within a schema):

    schema          1
    harness         "scenario"
    tool_version    what `agent_mail.py --version` printed
    environment     {"python": "3.12.1", "implementation": "CPython",
                     "platform": platform.platform()}
    parameters      every parameter of the run: agents, messages, rounds, drain,
                    concurrency, poll_p, faults (the enabled names, sorted),
                    recovery_bound, configs (the ones run), quick, pickup
    seed            the seed
    schedule_hash   SHA-256 of the canonical JSON of the workload
    poll_hash       SHA-256 of the baseline's poll table (null if not run)
    schedule        counts: messages, by_type {ASK: n, ...}, faults {name: n}
    configs         {"hook": METRICS, "baseline": METRICS}, one per config run
    thresholds      {"missed": 0, "duplicates": 0, "conflicting_claims": 0,
                     "malformed_surfaced": 0, "recovery_bound": N}
    checks          [{"name": ..., "ok": true|false, "detail": ...}, ...], the
                    OK/FAIL lines in order
    passed          true iff every check is ok
    aborted         null, or why the run stopped early (time budget, status failure)
    elapsed_seconds the whole run

    METRICS keys (one object per configuration):
    pickup          "hook" or "inbox-poll"
    rounds          rounds + drain boundaries actually run
    wall_seconds    the configuration's run
    sends           {"scheduled": n, "reactive": n, "retries": n, "killed": n,
                     "claims_refused": n, "unexpected_exit": n,
                     "unexpected_detail": [up to 5 strings], "total": n}
    pickups         {"opportunities": n, "run": n, "ok": n,
                     "success_rate": 0..1, "skipped_crash": n,
                     "skipped_delay": n, "forgot": n, "exit_nonzero": n}
    delivery        {"deliverable": n, "delivered": n, "missed": n,
                     "delivery_rate": 0..1, "first_opportunity": n}
    latency_seconds {"n", "min", "max", "mean", "p50", "p95", "p99"} (null
                    when not justified)
    latency_boundaries  the same keys, in prompt boundaries
    duplicates      {"ids_per_message": n, "stale_surfacings": n, "total": n,
                     "resurfaced": n} (resurfaced is the live mail shown again
                    at a later boundary, which the stateless adapter does by
                    design; it is informational, not a duplicate)
    claims          {"sent": n, "won": n, "refused": n, "conflicting_max": n,
                     "conflicting_own_view": n, "expired_at_end": n,
                     "held_at_end": n}
    asks            {"sent": n, "answered": n, "unanswered_at_end": n,
                     "open_per_status": n}
    malformed       {"planted": n, "quarantined_at_end": n, "surfaced": n}
    recovery        {"events": n, "max_boundaries": n|null, "unrecovered": n,
                     "per_event": [{"fault", "round", "recovered_round",
                                    "boundaries"}],
                     "final_problems": [codes], "final_problems_shifted": [codes],
                     "excused": {code: why}, "final_clean": bool,
                     "orphan_markers_at_end": n, "leftover_tmp": n,
                     "leftover_tmp_expected": n, "leftover_locks": n,
                     "stale_tmp_per_status": n, "boundaries_with_problems": n,
                     "problems_by_boundary": {"<round>": [codes]}}
                    (final_problems is `status` on the real clock; the
                    shifted lists are `status --now` 61 s ahead, past the 60 s
                    in-flight grace, so an orphaned lock or key marker shows
                    at the boundary it is left, not a minute later; nothing
                    is in flight at a boundary. final_clean is the shifted
                    list minus the excused codes: QUARANTINED when files were
                    planted, because quarantine never deletes)

Exit status: 0 every check OK; 1 at least one FAIL, or the run was aborted
(time budget) or the harness itself raised; 2 usage or tooling error (bad
arguments, agent_mail.py or the adapter missing).

Stdlib only, Python 3.10+. POSIX only (the tool is).
"""
from __future__ import annotations

import argparse
import datetime as dt
import faulthandler
import hashlib
import io
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent.parent
TOOL = HERE / "agent_mail.py"
HOOK = HERE / "hooks" / "agent_mail_check.py"
SCHEMA = 1
FAULTS = ("interrupted-writes", "abandoned-locks", "crashes", "malformed", "delayed-polling")
CONFIGS = ("hook", "baseline")
# How the `hook` configuration starts the adapter. `direct`: the interpreter on
# the script, nothing on stdin. `codex`: the way docs/hosts.md documents Codex
# running a UserPromptSubmit command hook: a shell command of the shape
# `install.py --adapter codex` registers (interpreter, quoted script path,
# `|| true`), with one JSON object of the documented fields on stdin. The path
# is this checkout's, not `$(git rev-parse --show-toplevel)`. No Codex runs.
PICKUPS = ("direct", "codex")
CODEX_COMMAND = '"$1" -I "$2" || true'
TYPE_WEIGHTS = (("ASK", 35), ("NOTICE", 40), ("CLAIM", 20), ("DISPUTE", 5))
LEASE_WEIGHTS = ((4, 25), (3600, 75))     # seconds; a quarter of the leases lapse during the run
PERCENTILE_MIN_N = {"p50": 10, "p95": 20, "p99": 100}
STEP_TIMEOUT = 120.0       # seconds; any single child
PARK_LIMIT = 300           # seconds; a parked child ends itself after this
TIMED_OUT = 124
KILLED = -9
GRACE_SHIFT = 61           # seconds; `status --now` shift that ends the 60 s in-flight grace
DEBUG = os.environ.get("SCENARIO_DEBUG", "") not in ("", "0")   # log slow calls to stderr
DEBUG_SLOW = 1.0           # seconds
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_HOOK_ENTRY = re.compile(r"^  (ASK|ANSWER|NOTICE|CLAIM|DISPUTE) from (\S+)( \[awaiting your reply\])?$")
_HOOK_ID = re.compile(rf"^    id: ({_ULID})$")
_SENT_ID = re.compile(rf"^id: ({_ULID})$", re.M)

# Replacements for one function of the tool, in a parked child (see park()).
AT_LINK = ("_link = os.link\n"
           "def link(src, dst, *a, **k):\n"
           "    if str(dst).endswith('.md'):\n"
           "        block()\n"
           "    return _link(src, dst, *a, **k)\n"
           "os.link = link")                     # temp file written, not yet linked
AT_LOCK = ("_lock = agent_mail._scope_lock\n"
           "def lock(path):\n"
           "    fd = _lock(path)\n"
           "    if fd is not None:\n"
           "        block()\n"
           "    return fd\n"
           "agent_mail._scope_lock = lock")       # scope lock just taken, nothing else yet


class UsageError(Exception):
    pass


class Aborted(Exception):
    pass


# ---------------------------------------------------------------- schedule ---

@dataclass
class Params:
    seed: int
    agents: int
    messages: int
    rounds: int
    drain: int
    concurrency: int
    poll_p: float
    faults: tuple[str, ...]
    recovery_bound: int
    configs: tuple[str, ...]
    quick: bool
    max_seconds: float
    self_test_miss: bool
    pickup: str = "direct"

    def as_json(self) -> dict[str, Any]:
        return {"agents": self.agents, "messages": self.messages, "rounds": self.rounds,
                "drain": self.drain, "concurrency": self.concurrency, "poll_p": self.poll_p,
                "faults": sorted(self.faults), "recovery_bound": self.recovery_bound,
                "configs": list(self.configs), "quick": self.quick,
                "max_seconds": self.max_seconds, "self_test_miss": self.self_test_miss,
                "pickup": self.pickup}


def weighted(rng: random.Random, table: tuple[tuple[Any, int], ...]) -> Any:
    pick = rng.randrange(sum(w for _, w in table))
    for value, w in table:
        if pick < w:
            return value
        pick -= w
    return table[-1][0]


def fake_ulid(rng: random.Random) -> str:
    """A well-formed, never-minted id for a planted file ('7' first: newest-sorting)."""
    return "7" + "".join(rng.choice(CROCKFORD) for _ in range(25))


def windows(rng: random.Random, agents: list[str], rounds: int, n: int) -> list[dict[str, Any]]:
    """n non-overlapping (agent, start, rounds) windows that end before the last round."""
    out: list[dict[str, Any]] = []
    for _ in range(n):
        for _attempt in range(50):
            k = rng.randint(1, 3)
            if rounds - 1 - k < 1:
                k = max(1, rounds - 2)
            agent = rng.choice(agents)
            start = rng.randint(1, max(1, rounds - 1 - k))
            clash = any(w["agent"] == agent and start < w["start"] + w["rounds"]
                        and w["start"] < start + k for w in out)
            if not clash:
                out.append({"agent": agent, "start": start, "rounds": k})
                break
    return out


def build_schedule(p: Params) -> dict[str, Any]:
    """The workload: a pure function of the seed and the parameters."""
    rng = random.Random(f"{p.seed}:workload")
    agents = [f"agent-{i:02d}" for i in range(1, p.agents + 1)]
    scopes = [f"file:mod{k}.py" for k in range(1, max(2, p.agents // 2) + 1)]
    msgs: list[dict[str, Any]] = []
    for seq in range(p.messages):
        sender = rng.choice(agents)
        to = rng.choice([a for a in agents if a != sender])
        mtype = weighted(rng, TYPE_WEIGHTS)
        m: dict[str, Any] = {"seq": seq, "round": rng.randrange(p.rounds), "from": sender,
                             "to": to, "type": mtype, "subject": f"s{seq:04d}",
                             "key": f"k{seq:04d}", "fault": None}
        if mtype == "CLAIM":
            m["scope"] = rng.choice(scopes)
            m["lease"] = weighted(rng, LEASE_WEIGHTS)
        msgs.append(m)
    faults: dict[str, list[dict[str, Any]]] = {name: [] for name in FAULTS}
    if "interrupted-writes" in p.faults:
        r = random.Random(f"{p.seed}:interrupted-writes")
        pool = [m for m in msgs if m["type"] != "CLAIM" and m["round"] < p.rounds - 1]
        for m in r.sample(pool, min(len(pool), max(1, p.messages // 50))):
            m["fault"] = "interrupt"
            faults["interrupted-writes"].append({"seq": m["seq"], "round": m["round"]})
    if "abandoned-locks" in p.faults:
        r = random.Random(f"{p.seed}:abandoned-locks")
        pool = [m for m in msgs if m["type"] == "CLAIM" and m["round"] < p.rounds - 1]
        for i, m in enumerate(r.sample(pool, min(len(pool), max(1, p.agents // 4)))):
            m["fault"] = "abandon-lock"
            other = r.choice([a for a in agents if a != m["from"]])
            seq = p.messages + i
            msgs.append({"seq": seq, "round": m["round"] + 1, "from": other, "to": m["to"],
                         "type": "CLAIM", "subject": f"s{seq:04d}", "key": f"k{seq:04d}",
                         "fault": None, "scope": m["scope"], "lease": 3600,
                         "reclaim_of": m["seq"]})
            faults["abandoned-locks"].append({"seq": m["seq"], "round": m["round"],
                                              "scope": m["scope"], "reclaim_seq": seq})
    if "crashes" in p.faults:
        r = random.Random(f"{p.seed}:crashes")
        faults["crashes"] = windows(r, agents, p.rounds, max(1, p.agents // 4))
    if "malformed" in p.faults:
        r = random.Random(f"{p.seed}:malformed")
        for i in range(max(1, p.messages // 100)):
            uid = fake_ulid(r)
            faults["malformed"].append({"round": r.randrange(p.rounds), "to": r.choice(agents),
                                        "id": uid, "name": f"{uid}-planted-{i}.md"})
    if "delayed-polling" in p.faults:
        r = random.Random(f"{p.seed}:delayed-polling")
        faults["delayed-polling"] = windows(r, agents, p.rounds, max(1, p.agents // 4))
    msgs.sort(key=lambda m: (m["round"], m["seq"]))
    return {"agents": agents, "scopes": scopes, "rounds": p.rounds, "drain": p.drain,
            "messages": msgs, "faults": faults}


def poll_table(p: Params, agents: list[str]) -> dict[int, dict[str, bool]]:
    """The baseline's memory: does agent A remember to look at boundary b? Seeded apart
    from the workload, so the workload is the same whatever the baseline does."""
    r = random.Random(f"{p.seed}:baseline-poll")
    return {b: {a: r.random() < p.poll_p for a in agents} for b in range(p.rounds + p.drain)}


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------- execution ---

@dataclass
class Send:
    kind: str                 # scheduled | reactive | retry | killed
    msg: dict[str, Any]
    round: int
    rc: int = 0
    id: str = ""
    out: str = ""
    err: str = ""
    t_done: float = 0.0       # monotonic
    expires: float = 0.0      # epoch; CLAIM only
    wrote: bool = False


@dataclass
class Pickup:
    agent: str
    round: int
    mode: str                 # hook | poll | crash | delay | forgot
    rc: int = 0
    entries: list[dict[str, str]] = field(default_factory=list)   # type, from, id, subject
    t_done: float = 0.0


@dataclass
class Boundary:
    round: int
    t_start: float
    t_end: float
    wall_start: float
    wall_end: float
    status: dict[str, Any]
    shifted: dict[str, Any]       # status --now shifted past the 60 s in-flight grace
    shifted_problems: list[str]
    lock_files: list[str]
    tmp_files: list[str]


@dataclass
class Agent:
    name: str
    seen: dict[str, int] = field(default_factory=dict)     # id -> round first surfaced
    to_answer: list[dict[str, str]] = field(default_factory=list)
    answered: dict[str, float] = field(default_factory=dict)   # ask id -> t_done of the answer
    deferred: list[dict[str, Any]] = field(default_factory=list)


def child_env(box: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env["AGENT_MAIL_DIR"] = str(box)
    env.update(extra)
    return env


def call(cmd: list[str], env: dict[str, str], stdin: str | None = None) -> tuple[int, str, str]:
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                            text=True)
    try:
        out, err = proc.communicate(stdin, timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate(timeout=30)
        return TIMED_OUT, out, err + f"\n(killed by the harness after {STEP_TIMEOUT:g} s)"
    took = time.monotonic() - t0
    if DEBUG and took > DEBUG_SLOW:
        print(f"[scenario] slow call {took:.2f}s rc={proc.returncode}: {' '.join(cmd[2:])[:160]}"
              f"{' | ' + err.strip()[-160:] if err.strip() else ''}", file=sys.stderr, flush=True)
    return proc.returncode, out, err


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def park_and_kill(box: Path, patch: str, argv: list[str]) -> tuple[int, bool]:
    """Run the real send in a child in which `patch` makes one function create a
    ready-file and block; SIGKILL it once parked. Returns the child's exit status and
    whether it parked (a child that exited on its own did not take the fault)."""
    ready = Path(tempfile.mkdtemp(prefix="scenario-park-")) / "ready"
    code = ("import os, signal, sys\n"
            f"signal.alarm({PARK_LIMIT})\n"
            f"sys.path.insert(0, {str(HERE)!r})\n"
            "import agent_mail\n"
            "def block(*_a, **_k):\n"
            f"    open({str(ready)!r}, 'w').close()\n"
            "    import time as _t\n"
            "    while True:\n"
            "        _t.sleep(0.05)\n"
            f"{patch}\n"
            f"sys.exit(agent_mail.main({argv!r}))\n")
    proc = subprocess.Popen([sys.executable, "-I", "-c", code], env=child_env(box), text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    reached = ready.exists()
    proc.kill()
    rc = proc.wait(timeout=STEP_TIMEOUT)
    try:
        ready.unlink(missing_ok=True)
        ready.parent.rmdir()
    except OSError:
        pass
    return rc, reached


def parse_hook(text: str) -> list[dict[str, str]]:
    """The live-mail entries of the adapter's output: type, from, id, subject."""
    lines = text.splitlines()
    out: list[dict[str, str]] = []
    i = 0
    while i < len(lines):
        m = _HOOK_ENTRY.match(lines[i])
        if m and i + 2 < len(lines):
            subject = lines[i + 1][4:] if lines[i + 1].startswith("    ") else ""
            mid = _HOOK_ID.match(lines[i + 2])
            if mid:
                out.append({"type": m.group(1), "from": m.group(2), "id": mid.group(1),
                            "subject": subject})
                i += 3
                continue
        i += 1
    return out


def parse_inbox(text: str) -> list[dict[str, str]]:
    doc = json.loads(text)
    return [{"type": str(r["type"]), "from": str(r["from"]), "id": str(r["id"]),
             "subject": str(r["subject"])} for r in doc["messages"]]


class Run:
    """One configuration over one schedule, in a mailbox of its own."""

    def __init__(self, config: str, p: Params, sched: dict[str, Any], box: Path,
                 polls: dict[int, dict[str, bool]] | None, t_budget_end: float) -> None:
        self.config, self.p, self.sched, self.box, self.polls = config, p, sched, box, polls
        self.t_budget_end = t_budget_end
        self.agents = {a: Agent(a) for a in sched["agents"]}
        self.sends: list[Send] = []
        self.pickups: list[Pickup] = []
        self.boundaries: list[Boundary] = []
        self.retries: list[dict[str, Any]] = []     # (round -> message) for killed sends
        self.pool = ThreadPoolExecutor(max_workers=max(1, p.concurrency))
        self.planted: list[dict[str, Any]] = []
        self.t0 = time.monotonic()
        self.wall_seconds = 0.0

    # -- the three phases --------------------------------------------------

    def crashed(self, agent: str, rnd: int) -> bool:
        return any(w["agent"] == agent and w["start"] <= rnd < w["start"] + w["rounds"]
                   for w in self.sched["faults"]["crashes"])

    def delayed(self, agent: str, rnd: int) -> bool:
        return any(w["agent"] == agent and w["start"] <= rnd < w["start"] + w["rounds"]
                   for w in self.sched["faults"]["delayed-polling"])

    def awake(self, agent: str, rnd: int) -> bool:
        return not self.crashed(agent, rnd) and not self.delayed(agent, rnd)

    def do_send(self, s: Send) -> Send:
        m = s.msg
        argv = ["send", "--type", m["type"], "--from", m["from"], "--to", m["to"],
                "--subject", m["subject"], "--key", m["key"]]
        if m["type"] == "ANSWER":
            argv += ["--reply-to", m["reply_to"], "--body", f"answer to {m['reply_to']} end"]
        elif m["type"] == "CLAIM":
            # whole seconds: what --expires carries, so the harness's own view of a lease
            # ends exactly when the tool's does
            s.expires = float(math.floor(time.time() + m["lease"]))
            argv += ["--scope", m["scope"], "--expires", iso(s.expires),
                     "--body", f"working on {m['scope']} end"]
        else:
            argv += ["--body", f"{m['type'].lower()} {m['subject']} end"]
        if s.kind == "killed":
            patch = AT_LINK if m["fault"] == "interrupt" else AT_LOCK
            s.rc, fired = park_and_kill(self.box, patch, argv)
            if not fired:
                s.err = f"the fault did not fire: the child exited {s.rc} before parking"
        else:
            s.rc, s.out, s.err = call([sys.executable, "-I", str(TOOL), *argv], child_env(self.box))
            found = _SENT_ID.search(s.out)
            s.id = found.group(1) if found else ""
            s.wrote = s.rc == 0 and s.out.startswith("wrote ")
        s.t_done = time.monotonic()
        return s

    def phase_send(self, rnd: int) -> None:
        batch: list[Send] = []
        for m in self.sched["messages"]:
            if m["round"] != rnd:
                continue
            if self.crashed(m["from"], rnd):
                self.agents[m["from"]].deferred.append(m)
                continue
            batch.append(Send("killed" if m["fault"] else "scheduled", m, rnd))
        for a in self.agents.values():
            if a.deferred and not self.crashed(a.name, rnd):
                batch += [Send("killed" if m["fault"] else "scheduled", m, rnd) for m in a.deferred]
                a.deferred = []
        for r in [r for r in self.retries if r["round"] == rnd]:
            if self.crashed(r["msg"]["from"], rnd):
                r["round"] += 1          # a crashed agent retries once it is back
            else:
                batch.append(Send("retry", r["msg"], rnd))
        # The faults first, one at a time, so that nothing else holds the scope lock at
        # the instant a claimant must be parked on it; then the round's sends, concurrently.
        done = [self.do_send(s) for s in batch if s.kind == "killed"]
        done += list(self.pool.map(self.do_send, [s for s in batch if s.kind != "killed"]))
        self.sends += done
        for s in done:
            if s.kind == "killed":   # the crashed agent retries the same keyed send next round
                self.retries.append({"round": rnd + 1, "msg": s.msg})
        for plant in self.sched["faults"]["malformed"]:
            if plant["round"] == rnd:
                text = (f"---\nid: {plant['id']}\ntype: ACK\nfrom: planted\nto: {plant['to']}\n"
                        f"date: {iso(time.time())}\nsubject: planted {plant['id']}\n---\n"
                        "not a message type anyone handles end\n")
                (self.box / plant["name"]).write_text(text, encoding="utf-8")
                self.planted.append(plant)

    def do_pickup(self, pk: Pickup) -> Pickup:
        if pk.mode == "hook":
            cmd, stdin = [sys.executable, "-I", str(HOOK)], None
            if self.p.pickup == "codex":
                cmd = ["/bin/sh", "-c", CODEX_COMMAND, "sh", sys.executable, str(HOOK)]
                stdin = json.dumps({
                    "session_id": f"scenario-{pk.agent}", "transcript_path": None,
                    "cwd": str(self.box), "hook_event_name": "UserPromptSubmit",
                    "model": "scenario", "permission_mode": "default",
                    "turn_id": f"round-{pk.round}", "prompt": "scenario boundary"})
            pk.rc, out, _err = call(cmd, child_env(self.box, AGENT_MAIL_IDENTITY=pk.agent), stdin)
            pk.entries = parse_hook(out) if pk.rc == 0 else []
        elif pk.mode == "poll":
            pk.rc, out, _err = call([sys.executable, "-I", str(TOOL), "inbox", "--to", pk.agent,
                                     "--json"], child_env(self.box))
            try:
                pk.entries = parse_inbox(out) if out.strip() else []
            except (ValueError, KeyError, TypeError):
                pk.entries = []
                pk.rc = pk.rc or 1
        pk.t_done = time.monotonic()
        return pk

    def read_status(self, now_shift: int = 0) -> dict[str, Any]:
        argv = ["status", "--json"]
        if now_shift:
            argv += ["--now", iso(time.time() + now_shift)]
        rc, out, err = call([sys.executable, "-I", str(TOOL), *argv], child_env(self.box))
        if rc != 0:
            raise Aborted(f"status --json exited {rc}: {err.strip()[-300:]}")
        doc: dict[str, Any] = json.loads(out)
        return doc

    def phase_boundary(self, rnd: int) -> None:
        t_start, wall_start = time.monotonic(), time.time()
        batch: list[Pickup] = []
        for a in self.sched["agents"]:
            if self.crashed(a, rnd):
                mode = "crash"
            elif self.delayed(a, rnd):
                mode = "delay"
            elif self.config == "hook":
                mode = "hook"
            else:
                assert self.polls is not None
                mode = "poll" if self.polls[rnd][a] else "forgot"
            batch.append(Pickup(a, rnd, mode))
        done = list(self.pool.map(self.do_pickup, batch))
        self.pickups += done
        for pk in done:
            if self.p.self_test_miss and self.config == "hook" and pk.entries:
                pk.entries = pk.entries[1:]      # a planted FAIL, for test_scenario.py
            ag = self.agents[pk.agent]
            for e in pk.entries:
                if e["id"] not in ag.seen:
                    ag.seen[e["id"]] = rnd
                    if e["type"] == "ASK" and e["id"] not in ag.answered:
                        ag.to_answer.append(e)
        status = self.read_status()
        shifted = self.read_status(GRACE_SHIFT)
        names = sorted(os.listdir(self.box))
        self.boundaries.append(Boundary(
            rnd, t_start, time.monotonic(), wall_start, time.time(), status, shifted,
            [str(pr["code"]) for pr in shifted["problems"]],
            [n for n in names if n.startswith(".scope.") and n.endswith(".lock")],
            [n for n in names if n.endswith(".tmp")]))

    def phase_react(self, rnd: int) -> None:
        batch: list[Send] = []
        for a in self.agents.values():
            if self.crashed(a.name, rnd):
                continue
            for e in a.to_answer:
                seq = e["subject"]
                m = {"type": "ANSWER", "from": a.name, "to": e["from"], "reply_to": e["id"],
                     "subject": f"re {seq}", "key": f"a-{e['id']}", "fault": None}
                batch.append(Send("reactive", m, rnd))
            a.to_answer = []
        done = list(self.pool.map(self.do_send, batch))
        self.sends += done
        for s in done:
            if s.rc == 0:
                self.agents[s.msg["from"]].answered[s.msg["reply_to"]] = s.t_done

    def run(self) -> None:
        try:
            for rnd in range(self.p.rounds + self.p.drain):
                if time.monotonic() > self.t_budget_end:
                    raise Aborted(f"time budget of {self.p.max_seconds:g} s exceeded at round {rnd}")
                if rnd < self.p.rounds:
                    self.phase_send(rnd)
                self.phase_boundary(rnd)
                self.phase_react(rnd)
        finally:
            self.pool.shutdown(wait=True)
            self.wall_seconds = time.monotonic() - self.t0

    # -- measurement ---------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        by_id = {s.id: s for s in self.sends if s.id}
        first: dict[str, tuple[Pickup, dict[str, str]]] = {}
        resurfaced = 0
        stale = 0
        malformed_ids = {pl["id"] for pl in self.sched["faults"]["malformed"]}
        malformed_seen = 0
        subject_ids: dict[str, set[str]] = {}
        for pk in self.pickups:
            b = self.boundaries[pk.round]
            for e in pk.entries:
                if e["id"] in malformed_ids:
                    malformed_seen += 1
                    continue
                subject_ids.setdefault(e["subject"], set()).add(e["id"])
                if e["id"] in first:
                    resurfaced += 1
                    s = by_id.get(e["id"])
                    if s is None:
                        continue
                    ans = self.agents[pk.agent].answered.get(e["id"])
                    if s.msg["type"] == "ASK" and ans is not None and ans < b.t_start:
                        stale += 1
                    if s.msg["type"] == "CLAIM" and s.expires and b.wall_start >= s.expires:
                        stale += 1
                else:
                    first[e["id"]] = (pk, e)
        dup_ids = sum(len(ids) - 1 for ids in subject_ids.values() if len(ids) > 1)
        # deliverable: live for a whole boundary at which the recipient was awake
        deliverable = delivered = first_opp = 0
        lat_s: list[float] = []
        lat_b: list[int] = []
        for s in self.sends:
            if s.rc != 0 or not s.wrote or not s.id:
                continue
            to = s.msg["to"]
            # a chance is a boundary that began after the send returned (a reactive ANSWER
            # is sent after its round's boundary, so its first chance is the next one)
            chances = [b for b in self.boundaries
                       if b.t_start > s.t_done and self.awake(to, b.round)
                       and (s.msg["type"] != "CLAIM" or s.expires > b.wall_end)]
            if not chances:
                continue
            deliverable += 1
            got = first.get(s.id)
            if got is None:
                continue
            pk, _e = got
            delivered += 1
            if pk.round == chances[0].round:
                first_opp += 1
            lat_s.append(max(0.0, pk.t_done - s.t_done))
            # boundaries that began after the send and went by before the one that surfaced it
            first_after = next(b.round for b in self.boundaries if b.t_start > s.t_done)
            lat_b.append(pk.round - first_after)
        final = self.boundaries[-1].status
        claims = [s for s in self.sends if s.msg["type"] == "CLAIM" and s.kind != "killed"]
        asks = [s for s in self.sends if s.msg["type"] == "ASK" and s.kind != "killed" and s.wrote]
        answered = {s.msg["reply_to"] for s in self.sends if s.kind == "reactive" and s.rc == 0}
        unexpected = [s for s in self.sends
                      if not ((s.kind == "killed" and s.rc == KILLED)
                              or (s.kind != "killed" and s.rc == 0)
                              or (s.kind != "killed" and s.msg["type"] == "CLAIM" and s.rc == 3))]
        expected_codes = {"QUARANTINED"} if self.planted else set()
        problems_at = [b for b in self.boundaries if set(b.shifted_problems) - expected_codes]
        excused = {c: "planted files are quarantined, never deleted" for c in expected_codes}
        last = self.boundaries[-1]
        shifted_final = sorted(set(last.shifted_problems))
        interrupt_kills = sum(1 for s in self.sends if s.kind == "killed"
                              and s.msg["fault"] == "interrupt")
        per_event = self.recovery_events()
        rec_max = max((ev["boundaries"] for ev in per_event if ev["boundaries"] is not None),
                      default=None)
        unrecovered = [ev for ev in per_event if ev["boundaries"] is None]
        final_problems = [str(pr["code"]) for pr in final["problems"]]
        modes = [pk.mode for pk in self.pickups]
        ran = [pk for pk in self.pickups if pk.mode in ("hook", "poll")]
        ok_run = [pk for pk in ran if pk.rc == 0 or (pk.mode == "poll" and pk.rc == 2 and self.planted)]
        opportunities = len([m for m in modes if m != "crash"])
        return {
            "pickup": "hook" if self.config == "hook" else "inbox-poll",
            "rounds": len(self.boundaries),
            "wall_seconds": round(self.wall_seconds, 3),
            "sends": {
                "scheduled": sum(1 for s in self.sends if s.kind == "scheduled"),
                "reactive": sum(1 for s in self.sends if s.kind == "reactive"),
                "retries": sum(1 for s in self.sends if s.kind == "retry"),
                "killed": sum(1 for s in self.sends if s.kind == "killed"),
                "claims_refused": sum(1 for s in claims if s.rc == 3),
                "unexpected_exit": len(unexpected),
                "unexpected_detail": [f"{s.kind} {s.msg['type']} {s.msg.get('subject', '')} "
                                      f"rc={s.rc} {s.err.strip()[-120:]}" for s in unexpected[:5]],
                "total": len(self.sends),
            },
            "pickups": {
                "opportunities": opportunities,
                "run": len(ran),
                "ok": len(ok_run),
                "success_rate": round(len(ok_run) / opportunities, 4) if opportunities else None,
                "skipped_crash": modes.count("crash"),
                "skipped_delay": modes.count("delay"),
                "forgot": modes.count("forgot"),
                "exit_nonzero": sum(1 for pk in ran if pk.rc != 0),
            },
            "delivery": {
                "deliverable": deliverable,
                "delivered": delivered,
                "missed": deliverable - delivered,
                "delivery_rate": round(delivered / deliverable, 4) if deliverable else None,
                "first_opportunity": first_opp,
            },
            "latency_seconds": stats([round(x, 4) for x in lat_s]),
            "latency_boundaries": stats([float(x) for x in lat_b]),
            "duplicates": {"ids_per_message": dup_ids, "stale_surfacings": stale,
                           "total": dup_ids + stale, "resurfaced": resurfaced},
            "claims": {
                "sent": len(claims),
                "won": sum(1 for s in claims if s.wrote),
                "refused": sum(1 for s in claims if s.rc == 3),
                "conflicting_max": max(int(b.status["contested_scopes"]) for b in self.boundaries),
                "conflicting_own_view": self.own_conflicts(),
                "expired_at_end": int(final["claims"]["expired"]),
                "held_at_end": int(final["claims"]["held"]),
            },
            "asks": {
                "sent": len(asks),
                "answered": sum(1 for s in asks if s.id in answered),
                "unanswered_at_end": sum(1 for s in asks if s.id not in answered),
                "open_per_status": int(final["asks"]["open"]),
            },
            "malformed": {"planted": len(self.planted),
                          "quarantined_at_end": int(final["quarantined"]),
                          "surfaced": malformed_seen},
            "recovery": {
                "events": len(per_event),
                "max_boundaries": rec_max,
                "unrecovered": len(unrecovered),
                "per_event": per_event,
                "final_problems": final_problems,
                "final_problems_shifted": shifted_final,
                "excused": excused,
                "final_clean": not (set(shifted_final) - set(excused)),
                "orphan_markers_at_end": int(last.shifted["idem_markers"]["orphaned"]),
                "leftover_tmp": len(last.tmp_files),
                "leftover_tmp_expected": interrupt_kills,
                "leftover_locks": len(last.lock_files),
                "stale_tmp_per_status": int(final["stale_tmp"]),
                "boundaries_with_problems": len(problems_at),
                "problems_by_boundary": {str(b.round): sorted(set(b.shifted_problems) - expected_codes)
                                         for b in problems_at},
            },
        }

    def own_conflicts(self) -> int:
        """Two claims this run wrote, from different senders, on one scope, both live
        over a whole boundary: the harness's own view, next to status's."""
        worst = 0
        won = [s for s in self.sends if s.msg["type"] == "CLAIM" and s.wrote]
        for b in self.boundaries:
            holders: dict[str, set[str]] = {}
            for s in won:
                if s.t_done < b.t_start and s.expires > b.wall_end:
                    holders.setdefault(s.msg["scope"], set()).add(s.msg["from"])
            worst = max(worst, sum(1 for hs in holders.values() if len(hs) > 1))
        return worst

    def recovery_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev in self.sched["faults"]["interrupted-writes"]:
            landed = next((s for s in self.sends if s.kind == "retry" and s.msg["seq"] == ev["seq"]
                           and s.wrote), None)
            rec = landed.round if landed else None
            out.append({"fault": "interrupted-write", "round": ev["round"], "recovered_round": rec,
                        "boundaries": None if rec is None else rec - ev["round"]})
        for ev in self.sched["faults"]["abandoned-locks"]:
            name = f".scope.{hashlib.sha256(ev['scope'].encode()).hexdigest()[:24]}.lock"
            attempts = [s for s in self.sends if s.rc in (0, 3) and s.msg.get("scope") == ev["scope"]
                        and s.kind in ("scheduled", "retry") and s.round > ev["round"]]
            rec = next((b.round for b in self.boundaries if b.round > ev["round"]
                        and name not in b.lock_files
                        and any(s.round <= b.round for s in attempts)), None)
            out.append({"fault": "abandoned-lock", "round": ev["round"], "recovered_round": rec,
                        "boundaries": None if rec is None else rec - ev["round"]})
        return out


# --------------------------------------------------------------- reporting ---

def stats(samples: list[float]) -> dict[str, Any]:
    n = len(samples)
    if not n:
        return {"n": 0, "min": None, "max": None, "mean": None, "p50": None, "p95": None,
                "p99": None}
    xs = sorted(samples)
    out: dict[str, Any] = {"n": n, "min": xs[0], "max": xs[-1],
                           "mean": round(sum(xs) / n, 4)}
    for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
        out[name] = xs[max(0, math.ceil(q * n) - 1)] if n >= PERCENTILE_MIN_N[name] else None
    return out


def fmt_stat(st: dict[str, Any], key: str, unit: str = "") -> str:
    v = st.get(key)
    if v is None:
        return f"n/a (n={st['n']})"
    return f"{v:g}{unit}"


def summary_rows(cfgs: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, str]]]:
    rows: list[tuple[str, dict[str, str]]] = []

    def row(label: str, f: Any) -> None:
        rows.append((label, {c: str(f(m)) for c, m in cfgs.items()}))

    row("pickup mechanism", lambda m: m["pickup"])
    row("prompt boundaries", lambda m: m["rounds"])
    row("wall seconds", lambda m: m["wall_seconds"])
    row("sends scheduled", lambda m: m["sends"]["scheduled"])
    row("sends reactive (ANSWER)", lambda m: m["sends"]["reactive"])
    row("sends killed by fault", lambda m: m["sends"]["killed"])
    row("sends retried after kill", lambda m: m["sends"]["retries"])
    row("claims refused (exit 3)", lambda m: m["sends"]["claims_refused"])
    row("sends unexpected exit", lambda m: m["sends"]["unexpected_exit"])
    row("pickup opportunities", lambda m: m["pickups"]["opportunities"])
    row("pickups run", lambda m: m["pickups"]["run"])
    row("pickups forgotten (baseline)", lambda m: m["pickups"]["forgot"])
    row("pickups skipped (crash)", lambda m: m["pickups"]["skipped_crash"])
    row("pickups skipped (delay)", lambda m: m["pickups"]["skipped_delay"])
    row("pickup exit nonzero", lambda m: m["pickups"]["exit_nonzero"])
    row("pickup success rate", lambda m: m["pickups"]["success_rate"])
    row("deliverable messages", lambda m: m["delivery"]["deliverable"])
    row("delivered", lambda m: m["delivery"]["delivered"])
    row("missed", lambda m: m["delivery"]["missed"])
    row("delivery rate", lambda m: m["delivery"]["delivery_rate"])
    row("delivered at first chance", lambda m: m["delivery"]["first_opportunity"])
    for key, unit, label in (("latency_seconds", "s", "latency"),
                             ("latency_boundaries", "", "latency (boundaries)")):
        row(f"{label} n", lambda m, k=key: m[k]["n"])
        for st in ("min", "mean", "max", "p50", "p95", "p99"):
            row(f"{label} {st}", lambda m, k=key, s=st, u=unit: fmt_stat(m[k], s, u))
    row("duplicate ids per message", lambda m: m["duplicates"]["ids_per_message"])
    row("stale surfacings", lambda m: m["duplicates"]["stale_surfacings"])
    row("re-surfaced live mail (design)", lambda m: m["duplicates"]["resurfaced"])
    row("claims sent", lambda m: m["claims"]["sent"])
    row("claims won", lambda m: m["claims"]["won"])
    row("conflicting live claims (max)", lambda m: m["claims"]["conflicting_max"])
    row("claims expired at end", lambda m: m["claims"]["expired_at_end"])
    row("claims held at end", lambda m: m["claims"]["held_at_end"])
    row("ASKs sent", lambda m: m["asks"]["sent"])
    row("ASKs answered", lambda m: m["asks"]["answered"])
    row("ASKs unanswered at end", lambda m: m["asks"]["unanswered_at_end"])
    row("ASKs open per status", lambda m: m["asks"]["open_per_status"])
    row("malformed planted", lambda m: m["malformed"]["planted"])
    row("quarantined per status", lambda m: m["malformed"]["quarantined_at_end"])
    row("malformed surfaced", lambda m: m["malformed"]["surfaced"])
    row("recovery events", lambda m: m["recovery"]["events"])
    row("recovery max boundaries", lambda m: m["recovery"]["max_boundaries"])
    row("recovery unrecovered", lambda m: m["recovery"]["unrecovered"])
    row("boundaries with problems", lambda m: m["recovery"]["boundaries_with_problems"])
    row("final status clean", lambda m: m["recovery"]["final_clean"])
    row("orphan key markers at end", lambda m: m["recovery"]["orphan_markers_at_end"])
    row("leftover .tmp files", lambda m: m["recovery"]["leftover_tmp"])
    row("leftover scope locks", lambda m: m["recovery"]["leftover_locks"])
    return rows


def print_summary(cfgs: dict[str, dict[str, Any]]) -> None:
    names = list(cfgs)
    width = max(len(n) for n in names) + 2
    print(f"{'metric':<34}" + "".join(f"{n:>{max(width, 14)}}" for n in names))
    for label, vals in summary_rows(cfgs):
        print(f"{label:<34}" + "".join(f"{vals[n]:>{max(width, 14)}}" for n in names))


def checks_for(hook: dict[str, Any] | None, hashes: dict[str, str],
               bound: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    distinct = set(hashes.values())
    check("schedule-hash", len(distinct) == 1,
          f"identical across {len(hashes)} configuration(s): {next(iter(distinct))[:16]}"
          if len(distinct) == 1 else f"differs: {hashes}")
    if hook is None:
        check("hook-config", False, "the hook configuration did not run; nothing to threshold")
        return out
    d, dup, cl, ml, rc, sd, pk = (hook["delivery"], hook["duplicates"], hook["claims"],
                                  hook["malformed"], hook["recovery"], hook["sends"],
                                  hook["pickups"])
    check("positive-control", d["delivered"] > 0,
          f"{d['delivered']} message(s) surfaced by the adapter (silence proves nothing)")
    check("missed", d["missed"] == 0,
          f"{d['missed']} of {d['deliverable']} deliverable message(s) never surfaced (threshold 0)")
    check("duplicates", dup["total"] == 0,
          f"{dup['ids_per_message']} extra id(s) for one message, {dup['stale_surfacings']} "
          "closed message(s) shown again (threshold 0)")
    check("conflicting-claims", cl["conflicting_max"] == 0 and cl["conflicting_own_view"] == 0,
          f"status contested_scopes max {cl['conflicting_max']}, own view "
          f"{cl['conflicting_own_view']} (threshold 0)")
    check("malformed", ml["quarantined_at_end"] == ml["planted"] and ml["surfaced"] == 0,
          f"{ml['planted']} planted, {ml['quarantined_at_end']} quarantined, "
          f"{ml['surfaced']} surfaced (threshold: all quarantined, none surfaced)")
    rec_ok = rc["unrecovered"] == 0 and (rc["max_boundaries"] is None or rc["max_boundaries"] <= bound)
    check("recovery", rec_ok,
          f"{rc['events']} event(s), max {rc['max_boundaries']} boundar(ies), "
          f"{rc['unrecovered']} unrecovered (bound {bound})")
    check("send-exit", sd["unexpected_exit"] == 0,
          f"{sd['unexpected_exit']} unexpected exit status(es) in {sd['total']} sends"
          + (f": {sd['unexpected_detail']}" if sd["unexpected_exit"] else ""))
    check("status-clean", rc["final_clean"],
          f"final problems {rc['final_problems_shifted']} (status --now +{GRACE_SHIFT} s)"
          + "".join(f"; {code} excused: {why}" for code, why in rc["excused"].items()))
    check("leftovers", rc["leftover_locks"] == 0 and rc["leftover_tmp"] == rc["leftover_tmp_expected"],
          f"{rc['leftover_locks']} scope lock file(s), {rc['leftover_tmp']} .tmp file(s) "
          f"({rc['leftover_tmp_expected']} expected, one per sender killed mid-write)")
    check("pickup-exit", pk["exit_nonzero"] == 0,
          f"{pk['exit_nonzero']} of {pk['run']} adapter run(s) exited non-zero")
    return out


def print_checks(checks: list[dict[str, Any]]) -> None:
    for c in checks:
        print(f"{'OK' if c['ok'] else 'FAIL':<10} {c['name']:<20} {c['detail']}")


# -------------------------------------------------------------------- main ---

def parse_faults(text: str) -> tuple[str, ...]:
    if text == "all":
        return FAULTS
    if text == "none":
        return ()
    names = tuple(t.strip() for t in text.split(",") if t.strip())
    bad = [n for n in names if n not in FAULTS]
    if bad:
        raise UsageError(f"--faults: unknown {bad}; choose from {', '.join(FAULTS)}, all, none")
    return tuple(n for n in FAULTS if n in names)


def parse_args(argv: list[str] | None) -> tuple[Params, str | None]:
    ap = argparse.ArgumentParser(
        description="Reproducible benchmark and reliability run of agent-postbox "
                    "(docs/benchmarking.md).")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--agents", type=int, default=8)
    ap.add_argument("--messages", type=int, default=400, help="scheduled sends")
    ap.add_argument("--rounds", type=int, default=40, help="rounds with scheduled sends")
    ap.add_argument("--drain", type=int, default=3,
                    help="prompt boundaries after the last round, with no scheduled sends")
    ap.add_argument("--concurrency", type=int, default=0,
                    help="parallel subprocesses per phase (default: the number of agents)")
    ap.add_argument("--config", choices=("hook", "baseline", "both"), default="both")
    ap.add_argument("--poll-p", type=float, default=0.5,
                    help="baseline: probability that an agent remembers to poll at a boundary")
    ap.add_argument("--faults", default="all", metavar="LIST",
                    help=f"all, none, or a comma list of {', '.join(FAULTS)}")
    ap.add_argument("--recovery-bound", type=int, default=2,
                    help="boundaries within which every injected crash must be recovered")
    ap.add_argument("--max-seconds", type=float, default=900.0,
                    help="abort (exit 1) when the whole run takes longer")
    ap.add_argument("--quick", action="store_true",
                    help="a small profile for CI: 4 agents, 40 messages, 10 rounds, 2 drain")
    ap.add_argument("--json", default=None, metavar="PATH", help="write the machine-readable report")
    ap.add_argument("--pickup", choices=PICKUPS, default="direct",
                    help="how the hook configuration starts the adapter: directly, or as "
                         "Codex's documented UserPromptSubmit command hook (docs/hosts.md)")
    ap.add_argument("--self-test-miss", action="store_true",
                    help="drop one surfaced message from the hook's bookkeeping, to show the "
                         "FAIL path (test_scenario.py uses it)")
    a = ap.parse_args(argv)
    if a.quick:
        for name, value in (("agents", 4), ("messages", 40), ("rounds", 10), ("drain", 2)):
            if getattr(a, name) == ap.get_default(name):
                setattr(a, name, value)
    if a.agents < 2 or a.messages < 1 or a.rounds < 3 or a.drain < 1:
        raise UsageError("need --agents >= 2, --messages >= 1, --rounds >= 3, --drain >= 1")
    if not 0.0 <= a.poll_p <= 1.0:
        raise UsageError("--poll-p must be between 0 and 1")
    if a.recovery_bound < 0 or a.max_seconds <= 0:
        raise UsageError("--recovery-bound must be >= 0 and --max-seconds > 0")
    configs = CONFIGS if a.config == "both" else (a.config,)
    return Params(a.seed, a.agents, a.messages, a.rounds, a.drain,
                  a.concurrency or a.agents, a.poll_p, parse_faults(a.faults),
                  a.recovery_bound, configs, a.quick, a.max_seconds, a.self_test_miss,
                  a.pickup), a.json


def tool_version() -> str:
    rc, out, err = call([sys.executable, "-I", str(TOOL), "--version"], child_env(Path(os.sep)))
    if rc != 0:
        raise UsageError(f"{TOOL} --version exited {rc}: {err.strip()[-200:]}")
    return out.strip()


def run(p: Params, json_path: str | None) -> int:
    for needed in (TOOL, HOOK):
        if not needed.is_file():
            raise UsageError(f"missing {needed}")
    version = tool_version()
    t0 = time.monotonic()
    budget_end = t0 + p.max_seconds
    try:   # a hard stop at twice the budget, where stderr is a real file (best effort)
        faulthandler.dump_traceback_later(p.max_seconds * 2, exit=True)
    except (ValueError, OSError, AttributeError, RuntimeError, io.UnsupportedOperation):
        pass
    sched = build_schedule(p)
    schedule_hash = digest(sched)
    polls = poll_table(p, sched["agents"]) if "baseline" in p.configs else None
    poll_hash = digest({str(k): v for k, v in polls.items()}) if polls else None
    print(f"scenario: seed {p.seed}, {p.agents} agents, {len(sched['messages'])} scheduled sends "
          f"({p.messages} + {len(sched['messages']) - p.messages} reclaim), {p.rounds} rounds + "
          f"{p.drain} drain, concurrency {p.concurrency}, faults "
          f"{','.join(p.faults) or 'none'}, {version}"
          + (f", pickup {p.pickup}" if p.pickup != "direct" else ""))
    print(f"schedule_hash: {schedule_hash}")
    if poll_hash:
        print(f"poll_hash: {poll_hash}  (baseline remembers to poll with p={p.poll_p:g})")
    results: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    aborted: str | None = None
    with tempfile.TemporaryDirectory(prefix="scenario-") as tmp:
        for config in p.configs:
            box = Path(tmp) / config
            box.mkdir()
            # the same schedule object, rebuilt, so one run cannot leak state into the next
            this = build_schedule(p)
            hashes[config] = digest(this)
            r = Run(config, p, this, box, polls, budget_end)
            print(f"[{config}] schedule_hash {hashes[config][:16]} ... running", flush=True)
            try:
                r.run()
            except Aborted as exc:
                aborted = f"{config}: {exc}"
                print(f"[{config}] ABORTED: {exc}", flush=True)
                break
            results[config] = r.metrics()
            print(f"[{config}] done in {r.wall_seconds:.1f} s, {len(r.sends)} sends, "
                  f"{len(r.pickups)} pickups", flush=True)
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    print()
    if results:
        print_summary(results)
        print()
    checks = checks_for(results.get("hook"), hashes, p.recovery_bound)
    if aborted:
        checks.append({"name": "completed", "ok": False, "detail": aborted})
    print_checks(checks)
    passed = all(c["ok"] for c in checks)
    elapsed = time.monotonic() - t0
    report = {
        "schema": SCHEMA, "harness": "scenario", "tool_version": version,
        "environment": {"python": platform.python_version(),
                        "implementation": platform.python_implementation(),
                        "platform": platform.platform()},
        "parameters": p.as_json(), "seed": p.seed,
        "schedule_hash": schedule_hash, "poll_hash": poll_hash,
        "schedule": {"messages": len(sched["messages"]),
                     "by_type": {t: sum(1 for m in sched["messages"] if m["type"] == t)
                                 for t in ("ASK", "NOTICE", "CLAIM", "DISPUTE")},
                     "faults": {k: len(v) for k, v in sched["faults"].items()}},
        "configs": results,
        "thresholds": {"missed": 0, "duplicates": 0, "conflicting_claims": 0,
                       "malformed_surfaced": 0, "recovery_bound": p.recovery_bound},
        "checks": checks, "passed": passed, "aborted": aborted,
        "elapsed_seconds": round(elapsed, 3),
    }
    if json_path:
        Path(json_path).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")
        print(f"json: {json_path}")
    failed = sum(1 for c in checks if not c["ok"])
    if failed:
        print(f"NOT PASSED: {failed} of {len(checks)} check(s) failed in {elapsed:.1f} s",
              file=sys.stderr)
        return 1
    print(f"passed: {len(checks)} check(s) in {elapsed:.1f} s")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        p, json_path = parse_args(argv)
        return run(p, json_path)
    except UsageError as exc:
        print(f"scenario: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        print("scenario: the harness itself failed; nothing above is a result", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
