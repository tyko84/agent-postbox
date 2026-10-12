#!/usr/bin/env python3
"""Team simulation: a task-level, offline experiment on the MECHANISM of agent-postbox.

    python scripts/team_sim.py [--quick] [--seed N] [--seeds N] [--p P[,P...]]
                               [--agents N] [--horizon N] [--lease N]
                               [--patience N] [--false-success Q] [--jobs N]
                               [--json PATH] [--max-seconds S] [--self-test-fail]

READ THIS FIRST. The agents here are scripts, not models. When an agent works,
what it decides, whether it reads the shared notes and whether it reports a
wrong result as done are all SIMULATED, from seeded tables and stated rules.
What is real is the channel of the `postbox` arm: every claim, release, fact,
question and answer is `agent_mail.py send` run as a subprocess, every pickup
is the shipped adapter `hooks/agent_mail_check.py`, and the refusal of a
conflicting claim and the expiry of a lease are the tool's own (the clock is
shifted per call, like `status --now`, so a 10-step lease does not take 10
real minutes). So the simulation measures the effect of the MECHANISM (atomic
claims with expiry, unprompted delivery) under the behavioural assumptions
printed in the report, and says nothing about how real models behave. The
design that a real-agent experiment would follow is docs/coordination-experiment.md.

The task family (docs/coordination-experiment.md, "The task family"): eight
tickets over a shared codebase with planted dependencies.
  producer -> consumer (x2)  the consumer needs a fact only the producer
                             discovers; without it the consumer commits a guess
  shared pair                two tickets edit one file, lines overlap in 3 of 4
                             seeds
  question                   a ticket is blocked until the lead answers a question
  crash bait                 an agent crashes mid-task; its claim must expire
                             before another agent may take the ticket

Arms. Both arms run the same loop: each step, every live agent first reads its
channel, then acts in a fixed order.
  postbox   the channel is the real mailbox. The adapter delivers mail at the
            start of the step; an agent claims the file scope before editing
            (a conflicting claim is refused, exit 3) and releases it by
            superseding its claim when done.
  control   the channel is one shared NOTES file. An agent reads it at the start
            of a step with probability p (a seeded table, common random numbers
            across p) and appends a line when it starts, finishes, learns a fact,
            asks or answers. Nothing is refused: an agent that has not read a
            START line for its file edits it anyway.
The workload (tickets, owners, files, values, crash, false-success draws) is a
pure function of the seed; its SHA-256 is `schedule_hash`, taken from the
schedule file each run leaves behind, and it must agree across the arms (a
checked invariant). The read table is hashed apart (`read_hash`, depends on p).

Outcomes are computed from the ARTIFACTS a run leaves, not from the engine's
state: a simulated repository of per-file edit logs (replayed with last-writer-
wins to a final state, then the hidden acceptance test of each ticket), the
real mailbox files or the notes file, the agents' own "done" reports, a log of
every channel call, and the fault log. See `measure()`.

Metrics (docs/coordination-experiment.md has the exact definitions):
  completion            fraction of tickets whose hidden test passes at the horizon
  conflicts             commits that overwrite lines another agent committed since
                        the committer's base revision, without ordering
  missed_at_commit      facts published before the consumer's first commit, which
                        committed a wrong value anyway
  facts_unused          facts published whose consumer ends failing
  unanswered            questions with no answer at the end
  recovery_steps        crash to the bait ticket passing (censored at the horizon)
  calls_per_ticket      channel calls per ticket passing
  steps                 steps to the last passing commit (the horizon if any fails)
  false_success         tickets reported done whose hidden test fails
All are reported per arm with, for the postbox-minus-control difference, a
paired bootstrap interval over seeds (2000 resamples, percentile, seeded).
The decision rule is fixed in advance (docs/coordination-experiment.md):
"better" only if the 95% interval excludes 0 in the favourable direction.

--seeds N runs seeds SEED..SEED+N-1 (default 5; --quick 3). --p lists the
control's read probabilities (default 0.25,0.5,0.75,1.0). --jobs seeds run at
once (the postbox arm is subprocess-bound).

--json PATH writes one document (keys are only ever added within a schema):
    schema, harness "team_sim", simulated true, tool_version, environment,
    parameters, assumptions[], seeds[], schedule_hashes {seed: sha}, read_hashes
    {"seed:p": sha}, cells {"postbox"|"control@P": {metric: {mean, per_seed}}},
    comparisons [{p, metric, favourable, postbox, control, diff, ci_low,
    ci_high, verdict}], advantage_vanishes {metric: p|null}, checks [{name, ok,
    detail}], passed, aborted, elapsed_seconds

Checks (harness integrity; the outcome is never thresholded): schedule-hash,
positive-control, cli-exit, mechanism-gate. --self-test-fail corrupts one
control run's schedule file after the run, to prove the hash check can fail.

Exit status: 0 every check OK; 1 any FAIL, an aborted run (time budget) or an
exception inside the harness; 2 usage or tooling error.

Stdlib only, Python 3.10+. POSIX only (the tool is).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import platform
import random
import re
import statistics
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
STEP_SECONDS = 60            # one simulated step is a minute on the shifted clock
CALL_TICK = 0.01             # seconds of simulated time between two calls in a step
STEP_TIMEOUT = 120.0
DEFAULT_P = (0.25, 0.5, 0.75, 1.0)
BOOT = 2000
REWORK_STEPS = 2
LEAD = "agent-00"
BOARD = "board"              # a recipient no one picks up as: claims are not mail to anyone
# (metric, favourable direction): +1 higher is better, -1 lower is better
METRICS: tuple[tuple[str, int], ...] = (
    ("completion", +1), ("conflicts", -1), ("missed_at_commit", -1), ("facts_unused", -1),
    ("unanswered", -1), ("recovery_steps", -1), ("calls_per_ticket", -1), ("steps", -1),
    ("false_success", -1))
INFO = ("calls", "messages", "reports")      # reported per arm, not compared
ASSUMPTIONS = (
    "agents are scripts: when they work, claim, read and report is simulated, not a model",
    "an agent works w steps on a ticket, then commits; a consumer waits up to --patience steps "
    "for its fact and then commits a guess",
    "a knowingly wrong commit is reported done with probability --false-success (seeded per "
    "ticket, the same in both arms); otherwise the agent stays on the ticket until the fact "
    "arrives and then reworks it",
    "an agent that was overwritten by an unordered commit does not notice and reports done",
    "control: an agent reads the shared notes at the start of a step with probability p; it "
    "skips a ticket whose file shows a recent START it has read; it takes over a ticket past "
    "its due step only if it has read no recent START for it",
    "postbox: the adapter delivers all live mail at the start of every step; a claim on the "
    "ticket's file is taken before editing and refused while another holder's lease is live; "
    "the holder renews before its lease lapses",
    "an idle agent takes over a ticket past due (planned start + lease + its own index, a "
    "backoff both arms share) that it does not know to be done; a crashed agent never returns",
    "mail and notes written in step s are visible from step s+1 in both arms; agents within a "
    "step act in a fixed order, so a claim race is decided by the tool, a notes race is not",
)


class UsageError(Exception):
    pass


class Aborted(Exception):
    pass


# ---------------------------------------------------------------- schedule ---

@dataclass
class Params:
    seed: int
    seeds: int
    ps: tuple[float, ...]
    agents: int
    horizon: int
    lease: int
    patience: int
    false_success: float
    jobs: int
    quick: bool
    max_seconds: float
    self_test_fail: bool

    def as_json(self) -> dict[str, Any]:
        return {"seed": self.seed, "seeds": self.seeds, "p": list(self.ps), "agents": self.agents,
                "horizon": self.horizon, "lease": self.lease, "patience": self.patience,
                "false_success": self.false_success, "jobs": self.jobs, "quick": self.quick,
                "max_seconds": self.max_seconds, "self_test_fail": self.self_test_fail}


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_schedule(seed: int, p: Params) -> dict[str, Any]:
    """The workload: a pure function of the seed and the parameters that shape it."""
    rng = random.Random(f"{seed}:team-workload")
    agents = [f"agent-{i:02d}" for i in range(p.agents)]
    others = agents[1:]
    kinds = ["producer", "consumer", "producer", "consumer", "shared", "shared", "question", "bait"]
    tickets: dict[str, dict[str, Any]] = {}
    for i, kind in enumerate(kinds, 1):
        tid = f"t{i:02d}"
        tickets[tid] = {"id": tid, "kind": kind, "work": 4 if kind == "bait" else rng.choice((2, 3)),
                        "file": f"src/m{i}.py", "lines": [1, 10],
                        "value": rng.randint(100, 999), "guess": 0, "fs_draw": round(rng.random(), 6),
                        "needs": None, "feeds": None, "owner": ""}
        tickets[tid]["guess"] = tickets[tid]["value"] + rng.randint(1, 50)
    for prod, cons in (("t01", "t02"), ("t03", "t04")):
        tickets[prod]["feeds"] = cons
        tickets[cons]["needs"] = prod
        tickets[cons]["value"] = tickets[prod]["value"]       # the consumer must use the producer's fact
        tickets[cons]["guess"] = tickets[prod]["value"] + rng.randint(1, 50)
    overlap = rng.random() < 0.75
    for t in ("t05", "t06"):
        tickets[t]["file"] = "src/shared.py"
    tickets["t06"]["lines"] = [6, 15] if overlap else [11, 20]
    # owners, with constraints: producer and consumer apart, the shared pair apart, the
    # question ticket not the lead's, the bait ticket the crashing agent's and first
    crash_agent = rng.choice(others)
    owners: dict[str, str] = {}
    for _attempt in range(1000):
        order = list(tickets)
        rng.shuffle(order)
        pool = [a for a in agents[:4] if a != crash_agent]
        owners = {tid: pool[i % len(pool)] for i, tid in enumerate(order) if tid != "t08"}
        owners["t08"] = crash_agent
        if (owners["t01"] != owners["t02"] and owners["t03"] != owners["t04"]
                and owners["t05"] != owners["t06"] and owners["t07"] != LEAD):
            break
    else:                                      # pragma: no cover - 1000 draws always succeed
        raise RuntimeError("no owner assignment satisfies the constraints")
    queues: dict[str, list[str]] = {a: [] for a in agents}
    for tid in sorted(tickets, key=lambda t: rng.random()):
        queues[owners[tid]].append(tid)
    for a, q in queues.items():
        start = 0
        for tid in q:
            tickets[tid]["owner"] = a
            tickets[tid]["planned_start"] = start
            start += tickets[tid]["work"]
    crash = {"agent": crash_agent, "step": rng.choice((1, 2, 3)), "ticket": "t08"}
    return {"agents": agents, "lead": LEAD, "tickets": tickets, "queues": queues, "crash": crash,
            "horizon": p.horizon, "lease": p.lease, "patience": p.patience, "rework": REWORK_STEPS}


def read_table(seed: int, p: float, agents: list[str], horizon: int) -> dict[int, dict[str, bool]]:
    """The control's memory: does agent A read the notes at step s? One uniform per
    (step, agent), thresholded by p, so a higher p reads a superset (common random numbers)."""
    r = random.Random(f"{seed}:control-read")
    u = {s: {a: r.random() for a in agents} for s in range(horizon)}
    return {s: {a: u[s][a] < p for a in agents} for s in range(horizon)}


# ---------------------------------------------------------------- channels ---

class Channel:
    """What an agent can do to communicate. Subclasses are the two arms."""

    name = ""

    def __init__(self, out: Path, sched: dict[str, Any], t0: float) -> None:
        self.out, self.sched, self.t0 = out, sched, t0
        self.calls = out / "calls.jsonl"
        self.step = 0
        self.seq = 0
        self.unexpected: list[str] = []
        self.surfaced = 0

    def log_call(self, agent: str, op: str, **extra: Any) -> None:
        with self.calls.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"step": self.step, "agent": agent, "op": op, **extra}) + "\n")

    def begin_step(self, step: int) -> None:
        self.step, self.seq = step, 0

    def read_all(self, agents: list[str]) -> dict[str, list[dict[str, Any]]]:
        raise NotImplementedError

    def start(self, agent: str, ticket: dict[str, Any]) -> tuple[bool, str]:
        raise NotImplementedError

    def finish(self, agent: str, ticket: dict[str, Any], claim: str) -> None:
        raise NotImplementedError

    def renew(self, agent: str, ticket: dict[str, Any], claim: str) -> str:
        return claim

    def fact(self, agent: str, to: str, ticket: dict[str, Any]) -> None:
        raise NotImplementedError

    def ask(self, agent: str, ticket: dict[str, Any]) -> None:
        raise NotImplementedError

    def answer(self, agent: str, ask: dict[str, Any], ticket: dict[str, Any]) -> None:
        raise NotImplementedError

    def known_done(self, agent: str) -> set[str]:
        return set()

    def message_count(self) -> int:
        raise NotImplementedError


SHIM = r'''
import datetime as _d, os, sys
_real = _d.datetime
_epoch = float(os.environ["SIM_EPOCH"])
_spawn = _real.now(_d.timezone.utc).timestamp()
class Sim(_real):
    @classmethod
    def now(cls, tz=None):
        elapsed = _real.now(_d.timezone.utc).timestamp() - _spawn
        return _real.fromtimestamp(_epoch + elapsed, tz or _d.timezone.utc)
_d.datetime = Sim
sys.path.insert(0, os.environ["SIM_ROOT"])
import agent_mail
agent_mail._now = lambda: Sim.now(_d.timezone.utc)
if sys.argv[1] == "--hook":
    import runpy
    runpy.run_path(os.environ["SIM_ROOT"] + "/hooks/agent_mail_check.py", run_name="__main__")
else:
    sys.exit(agent_mail.main(sys.argv[1:]))
'''
_HOOK_ENTRY = re.compile(r"^  (ASK|ANSWER|NOTICE|CLAIM|DISPUTE) from (\S+)( \[awaiting your reply\])?$")
_HOOK_ID = re.compile(r"^    id: ([0-9A-HJKMNP-TV-Z]{26})$")
_SENT_ID = re.compile(r"^id: ([0-9A-HJKMNP-TV-Z]{26})$", re.M)


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(math.floor(epoch), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_hook(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    out: list[dict[str, str]] = []
    i = 0
    while i < len(lines):
        m = _HOOK_ENTRY.match(lines[i])
        if m and i + 2 < len(lines):
            mid = _HOOK_ID.match(lines[i + 2])
            if mid:
                out.append({"type": m.group(1), "from": m.group(2), "id": mid.group(1),
                            "subject": lines[i + 1].strip()})
                i += 3
                continue
        i += 1
    return out


class PostboxChannel(Channel):
    """The real mailbox, the real CLI, the real adapter, on a per-call shifted clock."""

    name = "postbox"

    def __init__(self, out: Path, sched: dict[str, Any], t0: float) -> None:
        super().__init__(out, sched, t0)
        self.box = out / "mailbox"
        self.box.mkdir()
        self.shim = out / "shim.py"
        self.shim.write_text(SHIM, encoding="utf-8")
        self.asks: dict[str, str] = {}                # ticket -> ASK id
        self.pool = ThreadPoolExecutor(max_workers=max(1, len(sched["agents"])))
        self._pending: dict[str, float] = {}

    def _env(self, epoch: float, identity: str | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env.update(AGENT_MAIL_DIR=str(self.box), SIM_ROOT=str(HERE), SIM_EPOCH=repr(epoch))
        if identity:
            env["AGENT_MAIL_IDENTITY"] = identity
        return env

    def _epoch(self) -> float:
        e = self.t0 + self.step * STEP_SECONDS + self.seq * CALL_TICK
        self.seq += 1
        return e

    def _run(self, argv: list[str], epoch: float, identity: str | None = None) -> tuple[int, str, str]:
        cp = subprocess.run([sys.executable, "-I", str(self.shim), *argv], env=self._env(epoch, identity),
                            capture_output=True, text=True, timeout=STEP_TIMEOUT, stdin=subprocess.DEVNULL)
        return cp.returncode, cp.stdout, cp.stderr

    def _send(self, agent: str, args: list[str], ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
        epoch = self._epoch()
        argv = ["send", "--from", agent, *args]
        if "--expires" in argv:
            argv[argv.index("--expires") + 1] = iso(epoch + self.sched["lease"] * STEP_SECONDS)
        rc, out, err = self._run(argv, epoch)
        if rc not in ok:
            self.unexpected.append(f"{' '.join(args[:4])} exited {rc}: {err.strip()[-160:]}")
        found = _SENT_ID.search(out)
        return rc, found.group(1) if found else ""

    def begin_step(self, step: int) -> None:
        super().begin_step(step)
        self._pending = {a: self._epoch() for a in self.sched["agents"]}

    def read_all(self, agents: list[str]) -> dict[str, list[dict[str, Any]]]:
        def one(a: str) -> tuple[str, list[dict[str, Any]]]:
            rc, out, _err = self._run(["--hook"], self._pending[a], a)
            if rc != 0:
                self.unexpected.append(f"adapter exited {rc} for {a}")
                return a, []
            return a, self._events(parse_hook(out))
        got = dict(self.pool.map(one, agents))
        for a in agents:
            self.log_call(a, "pickup")
            self.surfaced += len(got[a])
        return got

    @staticmethod
    def _events(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
        ev: list[dict[str, Any]] = []
        for e in entries:
            parts = e["subject"].split()
            if e["type"] == "ASK" and parts[:1] == ["QUESTION"]:
                ev.append({"k": "ask", "ticket": parts[1], "from": e["from"], "id": e["id"]})
            elif e["type"] == "NOTICE" and parts[:1] == ["FACT"]:
                ev.append({"k": "fact", "ticket": parts[1], "value": int(parts[2].split("=")[1])})
            elif e["type"] == "ANSWER" and parts[:1] == ["ANSWER"]:
                ev.append({"k": "answer", "ticket": parts[1], "value": int(parts[2].split("=")[1])})
            elif e["type"] == "CLAIM" and parts[:1] == ["done"]:
                ev.append({"k": "done", "ticket": parts[1]})
        return ev

    def start(self, agent: str, ticket: dict[str, Any]) -> tuple[bool, str]:
        rc, cid = self._send(agent, ["--type", "CLAIM", "--to", BOARD, "--subject", f"claim {ticket['id']}",
                                     "--scope", f"file:{ticket['file']}", "--expires", "x",
                                     "--body", f"working on {ticket['id']}"], ok=(0, 3))
        self.log_call(agent, "claim", ticket=ticket["id"], rc=rc)
        return rc == 0, cid

    def finish(self, agent: str, ticket: dict[str, Any], claim: str) -> None:
        to = ticket["owner"] if ticket["owner"] != agent else BOARD
        cid = claim
        args = ["--type", "CLAIM", "--to", to, "--subject", f"done {ticket['id']}", "--expires", "x",
                "--body", f"released {ticket['id']}"]
        if cid:
            args += ["--supersedes", cid]
        self._send(agent, args)
        self.log_call(agent, "release", ticket=ticket["id"])

    def renew(self, agent: str, ticket: dict[str, Any], claim: str) -> str:
        rc, cid = self._send(agent, ["--type", "CLAIM", "--to", BOARD, "--subject", f"claim {ticket['id']}",
                                     "--scope", f"file:{ticket['file']}", "--expires", "x",
                                     "--supersedes", claim, "--body", f"still on {ticket['id']}"])
        self.log_call(agent, "renew", ticket=ticket["id"])
        return cid if rc == 0 else claim

    def fact(self, agent: str, to: str, ticket: dict[str, Any]) -> None:
        self._send(agent, ["--type", "NOTICE", "--to", to, "--subject", f"FACT {ticket['id']} value={ticket['value']}",
                           "--body", "finding for your ticket"])
        self.log_call(agent, "fact", ticket=ticket["id"])

    def ask(self, agent: str, ticket: dict[str, Any]) -> None:
        _rc, aid = self._send(agent, ["--type", "ASK", "--to", self.sched["lead"],
                                      "--subject", f"QUESTION {ticket['id']}", "--body", "blocked: need the decision"])
        self.asks[ticket["id"]] = aid
        self.log_call(agent, "ask", ticket=ticket["id"])

    def answer(self, agent: str, ask: dict[str, Any], ticket: dict[str, Any]) -> None:
        self._send(agent, ["--type", "ANSWER", "--to", ask["from"], "--reply-to", ask["id"],
                           "--subject", f"ANSWER {ticket['id']} value={ticket['value']}", "--body", "the decision"])
        self.log_call(agent, "answer", ticket=ticket["id"])

    def known_done(self, agent: str) -> set[str]:
        rc, out, _err = self._run(["list", "--type", "CLAIM", "--json"], self._epoch())
        self.log_call(agent, "list")
        if rc != 0:
            self.unexpected.append(f"list exited {rc}")
            return set()
        return {row["subject"].split()[1] for row in json.loads(out)["messages"]
                if str(row["subject"]).startswith("done ")}

    def message_count(self) -> int:
        return sum(1 for n in os.listdir(self.box) if n.endswith(".md"))


class NotesChannel(Channel):
    """One shared NOTES file. Reading is a probability; nothing is ever refused."""

    name = "control"

    def __init__(self, out: Path, sched: dict[str, Any], t0: float, reads: dict[int, dict[str, bool]]) -> None:
        super().__init__(out, sched, t0)
        self.notes = out / "NOTES.md"
        self.notes.write_text("# team notes\n", encoding="utf-8")
        self.reads = reads
        self.cursor: dict[str, int] = {}

    def _append(self, agent: str, op: str, line: str) -> None:
        with self.notes.open("a", encoding="utf-8") as fh:
            fh.write(f"s={self.step} {line}\n")
        self.log_call(agent, op)

    def read_all(self, agents: list[str]) -> dict[str, list[dict[str, Any]]]:
        lines = [ln for ln in self.notes.read_text(encoding="utf-8").splitlines() if ln.startswith("s=")]
        out: dict[str, list[dict[str, Any]]] = {}
        for a in agents:
            out[a] = []
            if not self.reads[self.step][a]:
                continue
            self.log_call(a, "notes-read")
            for ln in lines:
                ev = self._parse(ln)
                if ev and ev["step"] < self.step:
                    out[a].append(ev)
            self.surfaced += len(out[a])
        return out

    @staticmethod
    def _parse(line: str) -> dict[str, Any] | None:
        f = line.split()
        step = int(f[0][2:])
        kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
        if f[1] == "START":
            return {"k": "started", "ticket": kv["t"], "by": kv["by"],
                    "file": kv["file"], "step": step}
        if f[1] == "DONE":
            return {"k": "done", "ticket": kv["t"], "step": step}
        if f[1] == "FACT":
            return {"k": "fact", "ticket": kv["t"], "value": int(kv["value"]), "step": step}
        if f[1] == "ASK":
            return {"k": "ask", "ticket": kv["t"], "from": kv["from"], "id": kv["t"], "step": step}
        if f[1] == "ANSWER":
            return {"k": "answer", "ticket": kv["t"], "value": int(kv["value"]), "step": step}
        return None

    def start(self, agent: str, ticket: dict[str, Any]) -> tuple[bool, str]:
        self._append(agent, "start", f"START t={ticket['id']} by={agent} file={ticket['file']}")
        return True, ""

    def finish(self, agent: str, ticket: dict[str, Any], claim: str) -> None:
        self._append(agent, "done", f"DONE t={ticket['id']} by={agent}")

    def fact(self, agent: str, to: str, ticket: dict[str, Any]) -> None:
        self._append(agent, "fact", f"FACT t={ticket['id']} to={to} value={ticket['value']}")

    def ask(self, agent: str, ticket: dict[str, Any]) -> None:
        self._append(agent, "ask", f"ASK t={ticket['id']} from={agent} to={self.sched['lead']}")

    def answer(self, agent: str, ask: dict[str, Any], ticket: dict[str, Any]) -> None:
        self._append(agent, "answer", f"ANSWER t={ticket['id']} by={agent} value={ticket['value']}")

    def message_count(self) -> int:
        return sum(1 for ln in self.notes.read_text(encoding="utf-8").splitlines() if ln.startswith("s="))


# ------------------------------------------------------------------ engine ---

@dataclass
class Job:
    ticket: str
    phase: str                 # work | wait | unsure | rework
    left: int
    claim: str = ""
    claimed_at: int = 0
    waited: int = 0
    base: int = 0


@dataclass
class AgentState:
    name: str
    alive: bool = True
    job: Job | None = None
    facts: dict[str, int] = field(default_factory=dict)
    answers: dict[str, int] = field(default_factory=dict)
    done: set[str] = field(default_factory=set)
    started: dict[str, tuple[str, int, str]] = field(default_factory=dict)   # ticket -> (by, step, file)
    pending_asks: dict[str, dict[str, Any]] = field(default_factory=dict)
    answered: set[str] = field(default_factory=set)
    asked: set[str] = field(default_factory=set)
    refused_at: dict[str, int] = field(default_factory=dict)


class Repo:
    """The simulated repository: one append-only edit log per file (the artifact)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.rev: dict[str, int] = {}
        self.seq = 0

    def _log(self, file: str, entry: dict[str, Any]) -> None:
        self.seq += 1
        with (self.root / (file.replace("/", "__") + ".jsonl")).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": self.seq, "file": file, **entry}) + "\n")

    def start(self, step: int, agent: str, ticket: dict[str, Any]) -> int:
        base = self.rev.get(ticket["file"], 0)
        self._log(ticket["file"], {"step": step, "agent": agent, "ticket": ticket["id"], "op": "start",
                                   "lines": ticket["lines"], "base_rev": base})
        return base

    def commit(self, step: int, agent: str, ticket: dict[str, Any], base: int, value: int) -> None:
        self.rev[ticket["file"]] = self.rev.get(ticket["file"], 0) + 1
        self._log(ticket["file"], {"step": step, "agent": agent, "ticket": ticket["id"], "op": "commit",
                                   "lines": ticket["lines"], "base_rev": base, "value": value})


class World:
    def __init__(self, sched: dict[str, Any], chan: Channel, out: Path) -> None:
        self.s, self.chan, self.out = sched, chan, out
        self.tickets: dict[str, dict[str, Any]] = sched["tickets"]
        self.repo = Repo(out / "repo")
        self.reports = out / "reports.jsonl"
        self.faults = out / "faults.jsonl"
        self.agents = {a: AgentState(a) for a in sched["agents"]}
        self.reported: set[str] = set()
        self.params_q = 0.0

    # -- knowledge ---------------------------------------------------------

    def absorb(self, me: AgentState, events: list[dict[str, Any]]) -> None:
        for e in events:
            k, t = e["k"], e["ticket"]
            if k == "fact":
                me.facts[t] = e["value"]
            elif k == "answer":
                me.answers[t] = e["value"]
            elif k == "done":
                me.done.add(t)
            elif k == "started":
                old = me.started.get(t)
                if old is None or e["step"] >= old[1]:
                    me.started[t] = (e["by"], e["step"], e["file"])
            elif k == "ask" and me.name == self.s["lead"] and t not in me.answered:
                me.pending_asks[t] = e

    def report(self, step: int, agent: str, tid: str) -> None:
        self.reported.add(tid)
        with self.reports.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"step": step, "agent": agent, "ticket": tid, "claim": "done"}) + "\n")

    # -- one agent's turn --------------------------------------------------

    def startable(self, me: AgentState, t: dict[str, Any], step: int) -> bool:
        if t["id"] in me.done:
            return False
        if t["kind"] == "question" and t["id"] not in me.answers:
            return False
        if self.chan.name == "control":
            # polite only to what it has read: a recent START on the same file by someone else
            for tid, (by, at, file) in me.started.items():
                if (file == t["file"] and by != me.name and tid not in me.done
                        and step - at < self.s["lease"]):
                    return False
        return True

    def begin_job(self, me: AgentState, t: dict[str, Any], claim: str, step: int) -> None:
        base = self.repo.start(step, me.name, t)
        me.job = Job(t["id"], "work", t["work"], claim, step, 0, base)

    def pick(self, me: AgentState, step: int) -> None:
        for tid in self.s["queues"][me.name]:
            t = self.tickets[tid]
            if t["kind"] == "question" and tid not in me.answers and tid not in me.asked:
                me.asked.add(tid)
                self.chan.ask(me.name, t)
        mine = [self.tickets[tid] for tid in self.s["queues"][me.name] if tid not in me.done]
        for t in mine:
            if not self.startable(me, t, step):
                continue
            ok, claim = self.chan.start(me.name, t)
            if ok:
                self.begin_job(me, t, claim, step)
                return
        if any(t["id"] not in me.done for t in mine):
            return                           # still has its own tickets, waiting on something
        # idle: take over a ticket past due that it does not know to be done
        cands = self._candidates(me, step)
        done = me.done | (self.chan.known_done(me.name) if self.chan.name == "postbox" and cands else set())
        for t in cands:
            if t["id"] in done:
                continue
            if self.chan.name == "control":
                known = me.started.get(t["id"])
                if known and step - known[1] < self.s["lease"]:
                    continue
                if not self.startable(me, t, step):
                    continue
            elif t["kind"] == "question" and t["id"] not in me.answers:
                continue
            ok, claim = self.chan.start(me.name, t)
            if ok:
                self.begin_job(me, t, claim, step)
                return

    def _candidates(self, me: AgentState, step: int) -> list[dict[str, Any]]:
        return [t for t in self.tickets.values()
                if t["owner"] != me.name and t["id"] not in me.done and t["id"] not in self.reported
                and step >= t["planned_start"] + self.s["lease"] + self.s["agents"].index(me.name)]

    def commit(self, me: AgentState, t: dict[str, Any], job: Job, step: int, wrong: bool) -> None:
        value = t["guess"] if wrong else t["value"]
        self.repo.commit(step, me.name, t, job.base, value)

    def finish_ticket(self, me: AgentState, t: dict[str, Any], job: Job, step: int) -> None:
        self.report(step, me.name, t["id"])
        self.chan.finish(me.name, t, job.claim)
        me.done.add(t["id"])
        if t["feeds"]:
            to = self.tickets[t["feeds"]]["owner"]
            self.chan.fact(me.name, to, t)
        me.job = None

    def progress(self, me: AgentState, step: int) -> None:
        job = me.job
        assert job is not None
        t = self.tickets[job.ticket]
        if job.claim and step - job.claimed_at >= self.s["lease"] - 2:     # renew before it lapses
            job.claim, job.claimed_at = self.chan.renew(me.name, t, job.claim), step
        if job.phase == "work":
            job.left -= 1
            if job.left > 0:
                return
            if t["needs"] and t["needs"] not in me.facts:
                job.phase = "wait"
                return
            self.commit(me, t, job, step, wrong=False)
            self.finish_ticket(me, t, job, step)
        elif job.phase == "wait":
            if t["needs"] in me.facts:
                self.commit(me, t, job, step, wrong=False)
                self.finish_ticket(me, t, job, step)
                return
            job.waited += 1
            if job.waited >= self.s["patience"]:
                self.commit(me, t, job, step, wrong=True)
                # reported done anyway with the ticket's seeded probability; else it stays
                if t["fs_draw"] < self.params_q:
                    self.finish_ticket(me, t, job, step)
                else:
                    job.phase = "unsure"
        elif job.phase == "unsure":
            if t["needs"] in me.facts:
                job.base = self.repo.start(step, me.name, t)
                job.phase, job.left = "rework", self.s["rework"]
                self.progress(me, step)
        elif job.phase == "rework":
            job.left -= 1
            if job.left <= 0:
                self.commit(me, t, job, step, wrong=False)
                self.finish_ticket(me, t, job, step)

    def act(self, me: AgentState, step: int) -> None:
        if me.name == self.s["lead"]:
            for tid, ask in list(me.pending_asks.items()):
                self.chan.answer(me.name, ask, self.tickets[tid])
                me.answered.add(tid)
                del me.pending_asks[tid]
        if me.job is None:
            self.pick(me, step)
            if me.job is None:
                return
            self.progress(me, step)
            return
        self.progress(me, step)

    def wants_turn(self, me: AgentState, step: int) -> bool:
        """Does this agent prompt this step? An agent with nothing to do does not, so the
        adapter (a prompt-time hook) is not run for it either."""
        if me.job is not None or me.name == self.s["lead"] or any(
                t not in me.done for t in self.s["queues"][me.name]):
            return True
        return bool(self._candidates(me, step))

    def run(self, q: float, deadline: float) -> None:
        self.params_q = q
        crash = self.s["crash"]
        names = self.s["agents"]
        for step in range(self.s["horizon"]):
            if time.monotonic() > deadline:
                raise Aborted(f"time budget exceeded at step {step}")
            self.chan.begin_step(step)
            live = [a for a in names if self.agents[a].alive and self.wants_turn(self.agents[a], step)]
            for a, events in self.chan.read_all(live).items():
                self.absorb(self.agents[a], events)
            for a in names:
                me = self.agents[a]
                if not me.alive:
                    continue
                if a == crash["agent"] and step == crash["step"]:
                    me.alive = False
                    with self.faults.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"step": step, "agent": a, "fault": "crash",
                                             "ticket": crash["ticket"]}) + "\n")
                    continue
                if a in live:
                    self.act(me, step)
            if len(self.reported) == len(self.tickets):
                break


# --------------------------------------------------------------- measuring ---

def front_matter(path: Path) -> dict[str, str]:
    head: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines[:1] != ["---"]:
        return head
    for ln in lines[1:]:
        if ln == "---":
            break
        k, _, v = ln.partition(": ")
        head[k] = v
    return head


class Replayer:
    """Replays the edit logs of a simulated repository in write order. A commit that saw
    the previous revision (its base_rev is current) layers on top of the others; a commit
    whose base is older than a commit by another agent on overlapping lines is an unordered
    edit: it is counted as a conflict and, last writer wins, it erases that agent's work on
    the lines they share."""

    def __init__(self) -> None:
        self.cells: dict[tuple[str, int], dict[str, int]] = {}
        self.commits: dict[str, list[dict[str, Any]]] = {}
        self.conflicts = 0

    def apply(self, e: dict[str, Any]) -> None:
        if e["op"] != "commit":
            return
        file, (a, b) = e["file"], e["lines"]
        prior = self.commits.setdefault(file, [])
        clashing = [c for c in prior[e["base_rev"]:]
                    if c["agent"] != e["agent"] and c["lines"][0] <= b and a <= c["lines"][1]]
        if clashing:
            self.conflicts += 1
            for c in clashing:
                for line in range(max(a, c["lines"][0]), min(b, c["lines"][1]) + 1):
                    self.cells[(file, line)] = {}
        prior.append(e)
        for line in range(a, b + 1):
            self.cells.setdefault((file, line), {})[e["ticket"]] = e["value"]

    def passes(self, t: dict[str, Any]) -> bool:
        """The hidden acceptance test: every line of the ticket holds its correct value."""
        a, b = t["lines"]
        return all(self.cells.get((t["file"], ln), {}).get(t["id"]) == t["value"] for ln in range(a, b + 1))


def read_log(repo: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for f in sorted(repo.glob("*.jsonl")):
        entries += [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln]
    entries.sort(key=lambda e: e["seq"])
    return entries


def replay(entries: list[dict[str, Any]]) -> Replayer:
    r = Replayer()
    for e in entries:
        r.apply(e)
    return r


def pass_steps(entries: list[dict[str, Any]], tickets: dict[str, dict[str, Any]]) -> dict[str, int | None]:
    """For each ticket, the step of the commit after which it passes and keeps passing."""
    r = Replayer()
    first: dict[str, int | None] = {tid: None for tid in tickets}
    for e in entries:
        r.apply(e)
        if e["op"] != "commit":
            continue
        for tid, t in tickets.items():
            if r.passes(t):
                if first[tid] is None:
                    first[tid] = e["step"]
            else:
                first[tid] = None
    return first


def mailbox_messages(box: Path, t0: float) -> list[dict[str, Any]]:
    out = []
    for f in sorted(box.glob("*.md")):
        h = front_matter(f)
        when = dt.datetime.strptime(h["date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        h["step"] = str(int((when.timestamp() - t0) // STEP_SECONDS))
        out.append(h)
    return out


def measure(out: Path) -> dict[str, Any]:
    """Every outcome, from the artifacts in `out` alone (no engine state)."""
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    sched = json.loads((out / "schedule.json").read_text(encoding="utf-8"))
    tickets: dict[str, dict[str, Any]] = sched["tickets"]
    horizon: int = sched["horizon"]
    log = read_log(out / "repo")
    rp = replay(log)
    conflicts = rp.conflicts
    history: dict[str, list[dict[str, Any]]] = {}
    for e in log:
        if e["op"] == "commit":
            history.setdefault(e["ticket"], []).append(e)
    ok = {tid: rp.passes(t) for tid, t in tickets.items()}
    reports = [json.loads(ln) for ln in (out / "reports.jsonl").read_text(encoding="utf-8").splitlines() if ln] \
        if (out / "reports.jsonl").exists() else []
    reported = {r["ticket"] for r in reports}
    calls = [json.loads(ln) for ln in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines() if ln] \
        if (out / "calls.jsonl").exists() else []
    # facts and questions, from the channel's own files
    facts: dict[str, int] = {}            # producer ticket -> step published
    asks: dict[str, str] = {}             # ticket -> id (or ticket for notes)
    answered: set[str] = set()
    messages = 0
    if meta["arm"] == "postbox":
        msgs = mailbox_messages(out / "mailbox", meta["t0"])
        messages = len(msgs)
        by_id = {m["id"]: m for m in msgs}
        for m in msgs:
            subj = m.get("subject", "").split()
            if m["type"] == "NOTICE" and subj[:1] == ["FACT"]:
                facts[subj[1]] = int(m["step"])
            elif m["type"] == "ASK" and subj[:1] == ["QUESTION"]:
                asks[subj[1]] = m["id"]
            elif m["type"] == "ANSWER" and m.get("reply_to") in by_id:
                answered.add(m["reply_to"])
        unanswered = sum(1 for _t, i in asks.items() if i not in answered)
    else:
        lines = [ln for ln in (out / "NOTES.md").read_text(encoding="utf-8").splitlines() if ln.startswith("s=")]
        messages = len(lines)
        ans: set[str] = set()
        for ln in lines:
            f = ln.split()
            kv = dict(x.split("=", 1) for x in f[2:] if "=" in x)
            if f[1] == "FACT":
                facts[kv["t"]] = int(f[0][2:])
            elif f[1] == "ASK":
                asks[kv["t"]] = kv["t"]
            elif f[1] == "ANSWER":
                ans.add(kv["t"])
        unanswered = sum(1 for tid in asks if tid not in ans)
    missed = unused = 0
    for t in tickets.values():
        if not t["needs"]:
            continue
        pub = facts.get(t["needs"])
        if pub is None:
            continue
        commits = history.get(t["id"], [])
        if commits and commits[0]["value"] != t["value"] and pub < commits[0]["step"]:
            missed += 1
        if not ok[t["id"]]:
            unused += 1
    crash = sched["crash"]
    psteps = pass_steps(log, tickets)
    pstep = psteps[crash["ticket"]]
    recovery = (pstep - crash["step"]) if pstep is not None else horizon - crash["step"]
    steps_pass = [s for s in psteps.values() if s is not None]
    n_pass = sum(ok.values())
    return {
        "completion": n_pass / len(tickets),
        "conflicts": conflicts,
        "missed_at_commit": missed,
        "facts_unused": unused,
        "unanswered": unanswered,
        "recovery_steps": recovery,
        "recovered": pstep is not None,
        "calls_per_ticket": len(calls) / max(1, n_pass),
        "steps": (max(steps_pass) + 1) if n_pass == len(tickets) and steps_pass else horizon,
        "false_success": sum(1 for tid in reported if not ok[tid]),
        "reports": len(reports),
        "messages": messages,
        "calls": len(calls),
        "claim_calls": sum(1 for c in calls if c["op"] == "claim"),
        "tickets_passing": n_pass,
        "schedule_hash": digest(sched),
    }


# ------------------------------------------------------------------- runner ---

def run_arm(seed: int, arm: str, p: float, params: Params, sched: dict[str, Any], t0: float,
            base: Path, deadline: float) -> dict[str, Any]:
    out = base / f"{arm}-{p}" if arm == "control" else base / arm
    out.mkdir(parents=True)
    (out / "schedule.json").write_text(json.dumps(sched, sort_keys=True), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"arm": arm, "p": p, "seed": seed, "t0": t0}), encoding="utf-8")
    reads = read_table(seed, p, sched["agents"], sched["horizon"])
    chan: Channel
    if arm == "postbox":
        chan = PostboxChannel(out, sched, t0)
    else:
        chan = NotesChannel(out, sched, t0, reads)
    world = World(sched, chan, out)
    try:
        world.run(params.false_success, deadline)
    finally:
        if isinstance(chan, PostboxChannel):
            chan.pool.shutdown(wait=True)
    if params.self_test_fail and arm == "control" and p == params.ps[0]:
        bad = json.loads((out / "schedule.json").read_text(encoding="utf-8"))
        bad["tickets"]["t01"]["value"] += 1                  # a planted divergence
        (out / "schedule.json").write_text(json.dumps(bad, sort_keys=True), encoding="utf-8")
    m = measure(out)
    m["unexpected"] = chan.unexpected[:5]
    m["surfaced"] = chan.surfaced
    m["read_hash"] = digest({str(s): v for s, v in reads.items()})
    m["mailbox_claims"] = 0
    if arm == "postbox":
        m["mailbox_claims"] = sum(1 for h in mailbox_messages(out / "mailbox", t0) if h["type"] == "CLAIM")
    return m


def run_seed(seed: int, params: Params, deadline: float) -> dict[str, Any]:
    sched = build_schedule(seed, params)
    t0 = float(int(time.time()) - 7200)            # the shifted clock stays in the past of the real one
    with tempfile.TemporaryDirectory(prefix="team-sim-") as td:
        base = Path(td)
        res: dict[str, Any] = {"seed": seed, "schedule": sched}
        res["postbox"] = run_arm(seed, "postbox", 1.0, params, sched, t0, base, deadline)
        for p in params.ps:
            res[f"control@{p}"] = run_arm(seed, "control", p, params, sched, t0, base, deadline)
    return res


# ------------------------------------------------------------- statistics ---

def bootstrap_ci(diffs: list[float], seed: int = 12345) -> tuple[float, float]:
    if len(diffs) == 1:
        return diffs[0], diffs[0]
    r = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(r.choice(diffs) for _ in range(n)) / n for _ in range(BOOT))
    return means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]


def verdict(lo: float, hi: float, fav: int) -> str:
    if lo > 0 or hi < 0:
        return "postbox better" if (lo > 0) == (fav > 0) else "control better"
    return "no detectable difference"


def summarise(results: list[dict[str, Any]], params: Params) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    for key in ["postbox", *[f"control@{p}" for p in params.ps]]:
        cells[key] = {}
        for metric in [m for m, _f in METRICS] + list(INFO):
            vals = [float(r[key][metric]) for r in results]
            cells[key][metric] = {"mean": statistics.fmean(vals), "per_seed": vals}
    comps: list[dict[str, Any]] = []
    for p in params.ps:
        for metric, fav in METRICS:
            pb = cells["postbox"][metric]["per_seed"]
            ct = cells[f"control@{p}"][metric]["per_seed"]
            diffs = [a - b for a, b in zip(pb, ct, strict=True)]
            lo, hi = bootstrap_ci(diffs)
            comps.append({"p": p, "metric": metric, "favourable": "higher" if fav > 0 else "lower",
                          "postbox": statistics.fmean(pb), "control": statistics.fmean(ct),
                          "diff": statistics.fmean(diffs), "ci_low": lo, "ci_high": hi,
                          "verdict": verdict(lo, hi, fav)})
    vanishes: dict[str, float | None] = {}
    for metric, _fav in METRICS:
        row = sorted((c for c in comps if c["metric"] == metric), key=lambda c: c["p"])
        # the lowest p from which, upwards, no detectable postbox advantage remains
        none_from: float | None = None
        for c in row:
            if c["verdict"] != "postbox better":
                none_from = c["p"]
                break
        vanishes[metric] = none_from
    return {"cells": cells, "comparisons": comps, "advantage_vanishes": vanishes}


def check_results(results: list[dict[str, Any]], params: Params) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
    bad = []
    for r in results:
        hashes = {k: v["schedule_hash"] for k, v in r.items() if isinstance(v, dict) and "schedule_hash" in v}
        if len(set(hashes.values())) != 1 or next(iter(hashes.values())) != digest(r["schedule"]):
            bad.append(f"seed {r['seed']}: {sorted(set(hashes.values()))}")
    add("schedule-hash", not bad, "every arm of every seed ran the same workload" if not bad
        else "the arms did not run the same workload: " + "; ".join(bad))
    surf = sum(r["postbox"]["surfaced"] for r in results)
    msgs = sum(r["postbox"]["messages"] for r in results)
    add("positive-control", surf > 0 and msgs > 0 and all(r["postbox"]["tickets_passing"] > 0 for r in results),
        f"the postbox arm wrote {msgs} messages and the adapter surfaced {surf} entries (silence proves nothing)")
    unexpected = [f"seed {r['seed']}: {u}" for r in results for u in r["postbox"]["unexpected"]]
    add("cli-exit", not unexpected, "every send and pickup exited as expected (0, or 3 for a refused claim)"
        if not unexpected else "; ".join(unexpected[:3]))
    gate = all(r["postbox"]["mailbox_claims"] > 0 and r["postbox"]["claim_calls"] > 0 for r in results) and \
        all(r[f"control@{p}"]["claim_calls"] == 0 for r in results for p in params.ps)
    add("mechanism-gate", gate, "claims exist in the postbox arm's mailbox and in no control run"
        if gate else "the arms do not differ by the mechanism under test")
    return checks


# --------------------------------------------------------------------- CLI ---

def parse_args(argv: list[str] | None) -> tuple[Params, str | None]:
    ap = argparse.ArgumentParser(prog="team_sim", description="Team-level simulation of the Postbox mechanism.",
                                 allow_abbrev=False)
    ap.add_argument("--quick", action="store_true", help="3 seeds, short horizon (a CI step)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=None, help="number of seeds (default 5; --quick 3)")
    ap.add_argument("--p", default=None, help="control read probabilities, comma separated")
    ap.add_argument("--agents", type=int, default=4)
    ap.add_argument("--horizon", type=int, default=None, help="steps (default 30; --quick 24)")
    ap.add_argument("--lease", type=int, default=10, help="claim lease and takeover patience, in steps")
    ap.add_argument("--patience", type=int, default=3, help="steps a consumer waits for its fact")
    ap.add_argument("--false-success", type=float, default=0.3, dest="q",
                    help="probability a knowingly wrong commit is reported done (both arms)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json", default=None, metavar="PATH")
    ap.add_argument("--max-seconds", type=float, default=900.0)
    ap.add_argument("--self-test-fail", action="store_true", help="plant a divergence; exits 1")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        raise UsageError("bad arguments (see --help)") from None
    seeds = a.seeds if a.seeds is not None else (3 if a.quick else 5)
    horizon = a.horizon if a.horizon is not None else (24 if a.quick else 30)
    try:
        ps = tuple(float(x) for x in a.p.split(",")) if a.p else (DEFAULT_P if not a.quick else (0.5, 1.0))
    except ValueError:
        raise UsageError(f"--p wants numbers: {a.p!r}") from None
    if not ps or any(not 0.0 <= x <= 1.0 for x in ps):
        raise UsageError("--p values must be in [0, 1]")
    if seeds < 1 or a.agents < 4 or horizon < 12 or a.lease < 5 or a.patience < 1 or a.jobs < 1:
        raise UsageError("need --seeds >= 1, --agents >= 4, --horizon >= 12, --lease >= 5, "
                         "--patience >= 1, --jobs >= 1")
    if not 0.0 <= a.q <= 1.0:
        raise UsageError("--false-success must be in [0, 1]")
    if not TOOL.is_file() or not HOOK.is_file():
        raise UsageError(f"agent_mail.py or the adapter is missing next to {HERE}")
    return Params(a.seed, seeds, ps, a.agents, horizon, a.lease, a.patience, a.q, a.jobs, a.quick,
                  a.max_seconds, a.self_test_fail), a.json


def fmt(x: float) -> str:
    return f"{x:.2f}"


def print_summary(params: Params, summ: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    print("SIMULATED: scripted agents, real mailbox mechanism; this is not evidence about real models.")
    print(f"seeds {params.seed}..{params.seed + params.seeds - 1}, agents {params.agents}, horizon "
          f"{params.horizon}, lease {params.lease}, patience {params.patience}, "
          f"false-success q={params.false_success}")
    for p in params.ps:
        print(f"\ncontrol reads the notes with probability p={p}   (diff = postbox - control, 95% bootstrap CI)")
        print(f"{'metric':<20}{'postbox':>9}{'control':>9}{'diff':>8}  {'95% CI':<18}verdict")
        for c in summ["comparisons"]:
            if c["p"] == p:
                ci = f"[{c['ci_low']:+.2f}, {c['ci_high']:+.2f}]"
                print(f"{c['metric']:<20}{fmt(c['postbox']):>9}{fmt(c['control']):>9}"
                      f"{c['diff']:>+8.2f}  {ci:<18}{c['verdict']}")
    print("\nadvantage map (+ postbox better, = no detectable difference, - control better)")
    print(f"{'metric':<20}" + "".join(f"{'p=' + str(p):>8}" for p in params.ps))
    for metric, _fav in METRICS:
        row = {c["p"]: c["verdict"] for c in summ["comparisons"] if c["metric"] == metric}
        sym = {"postbox better": "+", "control better": "-", "no detectable difference": "="}
        print(f"{metric:<20}" + "".join(f"{sym[row[p]]:>8}" for p in params.ps))
    print()
    for c in checks:
        print(f"{'OK' if c['ok'] else 'FAIL':<5}{c['name']:<18}{c['detail']}")
    print(f"passed: {all(c['ok'] for c in checks)}")


def run(params: Params, json_path: str | None) -> int:
    t_start = time.monotonic()
    deadline = t_start + params.max_seconds
    seeds = list(range(params.seed, params.seed + params.seeds))
    aborted: str | None = None
    results: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=params.jobs) as ex:
            results = list(ex.map(lambda s: run_seed(s, params, deadline), seeds))
    except Aborted as exc:
        aborted = str(exc)
    if aborted or not results:
        print(f"FAIL aborted: {aborted}", file=sys.stderr)
        return 1
    summ = summarise(results, params)
    checks = check_results(results, params)
    passed = all(c["ok"] for c in checks)
    print_summary(params, summ, checks)
    if json_path:
        doc = {
            "schema": SCHEMA, "harness": "team_sim", "simulated": True,
            "tool_version": subprocess.run([sys.executable, "-I", str(TOOL), "--version"], capture_output=True,
                                           text=True).stdout.strip(),
            "environment": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                            "platform": platform.platform()},
            "parameters": params.as_json(), "assumptions": list(ASSUMPTIONS), "seeds": seeds,
            "schedule_hashes": {str(r["seed"]): digest(r["schedule"]) for r in results},
            "read_hashes": {f"{r['seed']}:{p}": r[f"control@{p}"]["read_hash"] for r in results for p in params.ps},
            "cells": summ["cells"], "comparisons": summ["comparisons"],
            "advantage_vanishes": summ["advantage_vanishes"], "checks": checks, "passed": passed,
            "aborted": aborted, "elapsed_seconds": round(time.monotonic() - t_start, 3)}
        Path(json_path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not passed:
        print("team_sim: NOT PASSED", file=sys.stderr)
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    try:
        params, json_path = parse_args(argv)
        return run(params, json_path)
    except UsageError as exc:
        print(f"team_sim: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        print("team_sim: the harness itself failed; nothing above is a result", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
