#!/usr/bin/env python3
"""Synthetic field test: four agents use one mailbox the way real agents would.

Everything goes through the command line, as subprocesses, against a mailbox
in a temporary directory (AGENT_MAIL_DIR). The agents (alice, bob, carol, dave),
subjects and scopes are invented. Phases, each timed:

  1. claims    four agents race for one scope: exactly one holder; `status
               --json` and `list` show it; the owner renews with --supersedes;
               a rival is refused; the lease expires and a rival then wins.
  2. keyed     a send with --key, the same again (duplicate, nothing written);
               a sender parked alive mid-send, against which a retry is
               refused (exit 3) after exactly its --key-wait; then that sender
               is SIGKILLed and its retry must leave exactly one message.
  3. ask       ASK, ANSWER with --reply-to, a retried ANSWER with the same
               --key (no duplicate); the ASK's derived status follows.
  4. burst     4 agents x 25 messages sent concurrently while three readers
               loop `list`/`show`, `inbox`, `status --json` and the pickup
               hook: ids unique, count exact, no reader ever fails or sees a
               partial or malformed message, nothing quarantined. During the
               burst: one key sent from 8 processes at once; a keyed writer
               SIGKILLed mid-publish and restarted with the same keys; an ASK
               answered by two agents at once, each answer retried; a series of
               keyed, scoped claims.
  5. closing   `doctor` and `status --json` clean, exact message counts, no
               lock/temp/takeover file left, no orphaned idempotency marker,
               every child process reaped, the scratch directories removed.

Phase 0 is the positive control for the readers' instruments: in a separate
scratch mailbox, a planted half-written message must be caught by exactly the
checks phase 4 relies on.

Clocks. `status --now` takes an injected instant, so expiry as `status` sees it
is checked deterministically on both sides of the boundary. `send` has no such
option: whether a rival may claim is decided on the real clock. That part
therefore uses a lease a couple of seconds long (longer if the machine is too
slow to renew within it) and polls (bounded, no fixed sleep) until the rival is
admitted, and checks the winner's own date stamp is not before the expiry.

The parked and killed senders are the one place that is not a bare CLI call:
to stop at a known point, the child runs the real `agent_mail.main(argv)` with
one function replaced by "signal readiness, then block". Retries are the plain
CLI. The retry after the kill is given --key-wait 30 and must succeed: a retry
that mistook the dead sender for a live one would wait and exit 3 instead. It
runs in the background while phases 3 and 4 use the same mailbox.

Time. A machine that is busy with something else makes every phase slower by
the same factor, for seconds at a time, and that is not a defect of the tool.
So nothing here compares a phase with a fixed number of seconds. A pacer sends
small messages to a mailbox of its own for the whole run; a phase's budget is
its usual cost in pacer sends, times BUDGET_SLACK, at the pace of the SLOWEST
pacer send that overlapped it, plus any wait the phase asked for itself. A
phase that is slow while the pacer is not (a retry that sleeps, a lock that is
waited for) exceeds its budget and fails the run.

Bounded. Every subprocess wait has a timeout that fails the test and says
which command, for how long, and what the mailbox held. A watchdog dumps every
thread's stack, kills the children and exits 1 after FIELD_WATCHDOG seconds
(default 240; FIELD_STEP_TIMEOUT, default 60, is the limit for one command); a parked child ends itself after 120 s whatever happens to this
process. FIELD_DEBUG=1 logs every child's start and end to stderr (pid, exit
code, wall and CPU time, command) and, for a call slower than FIELD_DEBUG_SLOW
seconds (default 0.7), the mailbox's hidden files with their ages.

About 6 s on a laptop. Stdlib only. POSIX only. Exit status 0 when every check
passes, 1 otherwise.
"""
from __future__ import annotations

import datetime as dt
import faulthandler
import json
import os
import re
import resource
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOOL = str(HERE / "agent_mail.py")
HOOK = str(HERE / "hooks" / "agent_mail_check.py")
AGENTS = ("alice", "bob", "carol", "dave")
PER_AGENT = 25
CRASH_N, CRASH_AT = 6, 2  # the crashing writer: six keyed messages, killed publishing the third
LANES = 8                # keyed, scoped claims made during the burst
SCOPE = "file:billing/client.py"
STEP_TIMEOUT = float(os.environ.get("FIELD_STEP_TIMEOUT", "60"))   # seconds; any single CLI call
PARK_LIMIT = 120         # seconds; a parked child ends itself after this (SIGALRM)
RETRY_LIMIT = 15.0       # seconds; the keyed retry after a killed sender
KEY_WAIT = 0.5           # seconds; the retry against a live sender
TIMED_OUT = 124          # return code this harness reports for a call it had to kill
BUDGET_SLACK = 4.0       # a phase may cost this many times its usual number of pacer sends
BUDGET_FLOOR = 1.0       # seconds; added to every budget
PACE_GAP = 0.1           # seconds between pacer sends
WATCHDOG = float(os.environ.get("FIELD_WATCHDOG", "240"))
DEBUG = os.environ.get("FIELD_DEBUG", "") not in ("", "0")
DEBUG_SLOW = float(os.environ.get("FIELD_DEBUG_SLOW", "0.7"))
FAILS: list[str] = []
CHILDREN: list[subprocess.Popen[str]] = []   # every child this run started
PHASES: list[phase] = []
EXTRA_TIMINGS: list[tuple[str, float]] = []
WATCHED: list[Path] = []                     # the mailbox, for the watchdog's report
FINISHED = threading.Event()
_PRINT = threading.Lock()
_T0 = time.monotonic()

_ROW = re.compile(r"^[! ] (ASK|ANSWER|NOTICE|CLAIM|DISPUTE) +\[([a-z]+) *\] (\S+) -> (\S+)  (.+)$")
_ID_LINE = re.compile(r"^    ([0-9A-HJKMNP-TV-Z]{26})$")
_BURST = re.compile(r"^burst (alice|bob|carol|dave) (\d{2})$")
CP = subprocess.CompletedProcess

# Replacements for one function of the tool, in a parked child (see park()).
BEFORE_PUBLISH = "agent_mail._publish = block"   # the key is reserved, nothing is written
AT_LINK = (                                      # the temp file is written, not yet linked
    "_link = os.link\n"
    "def link(src, dst, *a, **k):\n"
    "    if str(dst).endswith('.md'):\n"
    "        block()\n"
    "    return _link(src, dst, *a, **k)\n"
    "os.link = link")


def fail(name: str, detail: str = "") -> None:
    with _PRINT:
        print(f"  FAIL  {name}")
    FAILS.append(f"{name}: {detail[:600]}")


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fail(name, detail)
        return
    with _PRINT:
        print(f"  PASS  {name}")


def log(text: str) -> None:
    if DEBUG:
        with _PRINT:
            print(f"[field {time.monotonic() - _T0:8.3f}] {text}", file=sys.stderr, flush=True)


def box_state(box: Path) -> str:
    """The mailbox as a diagnostic line: visible count, hidden files with size and age."""
    try:
        names = sorted(os.listdir(box))
    except OSError as exc:
        return f"cannot list {box.name}: {exc}"
    now, hidden = time.time(), []
    for name in names:
        if name.startswith("."):
            try:
                st = os.lstat(box / name)
                hidden.append(f"{name} {st.st_size}b age={now - st.st_mtime:.1f}s")
            except OSError:
                hidden.append(f"{name} (gone)")
    return (f"{box.name}: {len(names) - len(hidden)} visible, {len(hidden)} hidden"
            + "".join(f"\n      {h}" for h in hidden[:40]))


def child_env(box: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env["AGENT_MAIL_DIR"] = str(box)
    env.update(extra)
    return env


def call(cmd: list[str], env: dict[str, str], box: Path, what: str) -> CP[str]:
    """Run one child to its end, output drained, never longer than STEP_TIMEOUT.
    A child that does not finish is killed and reaped, the run is failed with a
    diagnostic, and TIMED_OUT is returned so the caller's own checks fail too."""
    t0 = time.monotonic()
    cpu0 = resource.getrusage(resource.RUSAGE_CHILDREN) if DEBUG else None
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    CHILDREN.append(proc)
    log(f"start pid={proc.pid} {what}")
    try:
        out, err = proc.communicate(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        state = box_state(box)                    # before the kill: what it was holding
        proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", "(its output pipes stayed open after SIGKILL)"
        fail(f"a command did not finish within {STEP_TIMEOUT:g} s: {what}",
             f"killed after {time.monotonic() - t0:.1f} s, pid {proc.pid}, exit {proc.returncode}; "
             f"stderr {err[-200:]!r}; mailbox {state}")
        log(f"TIMEOUT pid={proc.pid} {what}\n      {state}")
        return CP(cmd, TIMED_OUT, out, err)
    took = time.monotonic() - t0
    if cpu0 is not None:
        cpu1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = cpu1.ru_utime + cpu1.ru_stime - cpu0.ru_utime - cpu0.ru_stime
        log(f"end   pid={proc.pid} rc={proc.returncode} wall={took:.3f}s children-cpu={cpu:.3f}s {what}")
        if took > DEBUG_SLOW:
            log(f"SLOW  pid={proc.pid} {took:.3f}s load={os.getloadavg()[0]:.1f} {what}\n"
                f"      {box_state(box)}")
    return CP(cmd, proc.returncode, out, err)


def run(box: Path, *argv: str) -> CP[str]:
    return call([sys.executable, "-I", TOOL, *argv], child_env(box), box, " ".join(argv)[:120])


def send(box: Path, who: str, to: str, kind: str, subj: str, body: str, *extra: str) -> CP[str]:
    return run(box, "send", "--type", kind, "--from", who, "--to", to,
               "--subject", subj, "--body", body, *extra)


def hook(box: Path, me: str) -> CP[str]:
    return call([sys.executable, "-I", HOOK], child_env(box, AGENT_MAIL_IDENTITY=me), box,
                f"hook as {me}")


def mid(cp: CP[str]) -> str:
    return next((x.split(": ", 1)[1] for x in cp.stdout.splitlines() if x.startswith("id: ")), "")


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status(box: Path, *extra: str) -> dict:
    cp = run(box, "status", "--json", *extra)
    try:
        return json.loads(cp.stdout) if cp.returncode == 0 else {}
    except ValueError:
        return {}


def rows(listing: str) -> list[tuple[str, ...]] | None:
    """Parse `list`/`inbox` output into (type, status, from, to, subject, id) rows.
    None when any line is not exactly a row followed by its id line."""
    lines = listing.splitlines()
    if not lines or not lines[0].startswith("# ") or len(lines) % 2 != 1:
        return None
    out = []
    for head, tail in zip(lines[1::2], lines[2::2], strict=True):
        m, i = _ROW.match(head), _ID_LINE.match(tail)
        if not m or not i:
            return None
        out.append((*m.groups(), i.group(1)))
    return out


def complete(raw: str) -> bool:
    """Is this the whole text of a message this test sent? (All its bodies end in " end".)"""
    return raw.startswith("---\nid: ") and raw.endswith(" end\n")


def with_subject(box: Path, subject: str) -> list[tuple[str, ...]]:
    return [r for r in rows(run(box, "list").stdout) or [] if r[4] == subject]


class Pacer(threading.Thread):
    """Sends one small message after another to a mailbox of its own, for the
    whole run, and remembers how long each took. It is the test's measure of
    how fast this machine is running the tool right now."""

    def __init__(self, box: Path) -> None:
        super().__init__(daemon=True)
        self.box = box
        self.base = 0.0
        self.samples: list[tuple[float, float]] = []
        self.failed = 0
        self.halt = threading.Event()

    def one(self) -> float:
        t0 = time.monotonic()
        cp = send(self.box, "alice", "all", "NOTICE", "pace", "tick end")
        t1 = time.monotonic()
        if cp.returncode == 0:
            self.samples.append((t0, t1))
        else:
            self.failed += 1
        return t1 - t0

    def calibrate(self, n: int = 5) -> None:
        self.box.mkdir()
        self.base = statistics.median(self.one() for _ in range(n))

    def run(self) -> None:
        while not self.halt.is_set():
            self.one()
            self.halt.wait(PACE_GAP)

    def pace(self, t0: float, t1: float) -> float:
        """Seconds per send: the slowest one that overlapped [t0, t1], at least the base."""
        return max([e - s for s, e in list(self.samples) if e >= t0 and s <= t1] + [self.base])


PACER: list[Pacer] = []


def margin(t0: float, t1: float) -> float:
    """How late a single timed call may be: 0.75 s plus ten sends at the current pace."""
    return 0.75 + 10 * (PACER[0].pace(t0, t1) if PACER else 0.1)


class phase:
    """Print a heading, record the wall-clock time of the block, and carry its
    budget: `sends` is what the phase usually costs, in pacer sends; allow()
    adds seconds the phase waits on purpose (a lease, a key wait)."""

    def __init__(self, name: str, sends: float) -> None:
        self.name, self.sends, self.waits = name, sends, 0.0
        self.t0 = self.t1 = 0.0

    def allow(self, seconds: float) -> None:
        self.waits += max(0.0, seconds)

    def __enter__(self) -> phase:
        print(self.name)
        self.t0 = time.monotonic()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.t1 = time.monotonic()
        PHASES.append(self)

    @property
    def took(self) -> float:
        return self.t1 - self.t0

    def budget(self) -> tuple[float, float]:
        """(seconds allowed, the pace it was computed at)."""
        pace = PACER[0].pace(self.t0, self.t1) if PACER else 0.1
        return BUDGET_FLOOR + self.waits + BUDGET_SLACK * self.sends * pace, pace


def park(box: Path, ready: Path, patch: str, *argv: str) -> subprocess.Popen[str]:
    """Start the real send in a child in which `patch` makes one function
    "create the ready-file, then block", and return once it is parked there (or
    has exited). The parent polls in bounded 0.02 s steps, 20 s at most. The
    child ends itself after PARK_LIMIT seconds, so it cannot outlive a dead
    harness for long."""
    code = (
        "import os, signal, sys\n"
        f"signal.alarm({PARK_LIMIT})\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "import agent_mail\n"
        "def block(*_a, **_k):\n"
        f"    open({str(ready)!r}, 'w').close()\n"
        "    signal.pause()\n"
        f"{patch}\n"
        f"sys.exit(agent_mail.main({list(argv)!r}))\n")
    proc = subprocess.Popen([sys.executable, "-I", "-c", code], env=child_env(box), text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    CHILDREN.append(proc)
    log(f"start pid={proc.pid} PARKED {' '.join(argv)[:100]}")
    deadline = time.monotonic() + 20
    while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return proc


def kill(proc: subprocess.Popen[str]) -> int | None:
    """SIGKILL a parked child and reap it."""
    proc.kill()
    try:
        proc.wait(timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        fail("a SIGKILLed child was not reaped", f"pid {proc.pid}")
    log(f"end   pid={proc.pid} rc={proc.returncode} KILLED")
    return proc.returncode


def timed(box: Path, *argv: str) -> tuple[CP[str], float]:
    t0 = time.monotonic()
    cp = run(box, *argv)
    return cp, time.monotonic() - t0


def arm_watchdog() -> None:
    """A run that hangs must end by itself, loudly: every thread's stack, the
    children still alive, the mailbox, then exit 1. faulthandler's own timer is
    the last resort should this thread never get to run."""
    faulthandler.dump_traceback_later(WATCHDOG + 30, exit=True)

    def bark() -> None:
        if FINISHED.wait(WATCHDOG):
            return
        sys.stdout.flush()
        err = sys.stderr
        print(f"\nfield test: WATCHDOG, no result after {WATCHDOG:.0f} s; stacks follow", file=err)
        faulthandler.dump_traceback(file=err, all_threads=True)
        alive = [p for p in list(CHILDREN) if p.returncode is None]
        for p in alive:
            print(f"  child still running: pid {p.pid}: {str(p.args)[-160:]}", file=err)
            try:
                p.kill()
            except OSError:
                pass
        for box in WATCHED:
            print("  mailbox " + box_state(box), file=err)
        err.flush()
        os._exit(1)

    threading.Thread(target=bark, daemon=True, name="watchdog").start()


def scenario(td: Path, box: Path) -> None:
    background = ThreadPoolExecutor(1)

    with phase("0. instruments: a half-written message is caught (positive control)", 4):
        ctl = td / "control"
        ctl.mkdir()
        good = send(ctl, "alice", "all", "NOTICE", "burst alice 00", "payload alice 00 end")
        whole = next(ctl.glob("*.md")).read_text(encoding="utf-8")
        clean = run(ctl, "list")
        check("control: a complete message lists cleanly and parses into one row",
              good.returncode == 0 and clean.returncode == 0 and not clean.stderr.strip()
              and len(rows(clean.stdout) or []) == 1 and complete(whole), clean.stdout + clean.stderr)
        cut = whole[:whole.index("subject:")]
        (ctl / "01ARZ3NDEKTSV4RRFFQ69G5FAV-burst-bob-00.md").write_text(cut, encoding="utf-8")
        dirty = run(ctl, "list")
        check("a half-written file makes `list` fail loudly (the readers' first check)",
              dirty.returncode != 0 and dirty.stderr.strip() != "", dirty.stdout + dirty.stderr)
        check("...and fails the readers' completeness check on the raw file",
              not complete(cut) and not complete(whole[:-2]))
        check("a listing with a mangled row or id line does not parse",
              rows(clean.stdout.replace("NOTICE", "NOT")) is None
              and rows(clean.stdout.rstrip("\n")[:-1] + "\n") is None
              and rows(clean.stdout + "trailing\n") is None)
        for f in ctl.iterdir():
            f.unlink()
        ctl.rmdir()

    with phase("1. claims: one scope, four claimants, renewal, expiry, takeover", 20) as ph:
        with ThreadPoolExecutor(len(AGENTS)) as pool:
            race = list(pool.map(
                lambda a: send(box, a, "all", "CLAIM", f"claim by {a}", "working on it end",
                               "--scope", SCOPE, "--expires", iso(time.time() + 3600)),
                AGENTS))
        codes = [r.returncode for r in race]
        check("four simultaneous claims: exactly one accepted", codes.count(0) == 1, str(codes))
        check("every other claimant is refused with exit 3, nothing else",
              sorted(codes) == [0, 3, 3, 3], str(codes) + race[0].stderr)
        owner = AGENTS[codes.index(0)] if 0 in codes else AGENTS[0]
        rival = next(a for a in AGENTS if a != owner)
        cid = mid(race[AGENTS.index(owner)])
        st = status(box)
        check("status --json: one claim held, one message in the box",
              st.get("claims", {}).get("held") == 1 and st.get("messages", {}).get("total") == 1
              and st.get("messages", {}).get("by_type", {}).get("CLAIM") == 1, json.dumps(st)[:300])
        check("status --json: the holder is the only agent seen so far",
              [a.get("agent") for a in st.get("agents", [])] == [owner], str(st.get("agents")))
        held = [r for r in rows(run(box, "list").stdout) or [] if r[0] == "CLAIM"]
        check("list names the holder: one CLAIM, [held], from the winner, with its id",
              len(held) == 1 and held[0][1] == "held" and held[0][2] == owner and held[0][5] == cid,
              str(held))
        again = send(box, rival, "all", "CLAIM", "second try", "me too end",
                     "--scope", SCOPE, "--expires", iso(time.time() + 3600))
        check("a rival is refused while the claim is held (exit 3, names the holder)",
              again.returncode == 3 and f"already held by {owner}" in again.stderr, again.stderr)

        # A lease of one to two seconds. `send` refuses an expiry that has passed
        # by the time it looks, so on a machine too slow for that the lease is
        # doubled and the renewal tried again (nothing was written by the refusal).
        for lease in (2, 4, 8, 16):
            expiry = int(time.time()) + lease
            renew = send(box, owner, "all", "CLAIM", "renewed", "still on it end", "--scope", SCOPE,
                         "--expires", iso(expiry), "--supersedes", cid)
            if not (renew.returncode == 2 and "already in the past" in renew.stderr):
                break
        rid = mid(renew)
        ph.allow(expiry - time.time())
        check("the owner renews its own claim with --supersedes", renew.returncode == 0,
              renew.stderr)
        stolen = send(box, rival, "all", "CLAIM", "not yours", "takeover end", "--scope", SCOPE,
                      "--expires", iso(time.time() + 3600), "--supersedes", cid)
        check("a rival cannot supersede the owner's claim", stolen.returncode == 2, stolen.stderr)
        c_before = status(box, "--now", iso(expiry - 1)).get("claims", {})
        c_after = status(box, "--now", iso(expiry + 1)).get("claims", {})
        check("status --now one second before expiry: held 1, superseded 1, expired 0",
              (c_before.get("held"), c_before.get("superseded"), c_before.get("expired")) == (1, 1, 0),
              str(c_before))
        check("status --now one second after expiry: held 0, superseded 1, expired 1",
              (c_after.get("held"), c_after.get("superseded"), c_after.get("expired")) == (0, 1, 1),
              str(c_after))

        refused: list[int] = []
        won: CP[str] | None = None
        deadline = time.monotonic() + lease + 15
        while time.monotonic() < deadline:   # bounded poll; each try is a real CLI call
            cp = send(box, rival, "all", "CLAIM", "after expiry", "mine now end", "--scope", SCOPE,
                      "--expires", iso(time.time() + 3600))
            if cp.returncode == 0:
                won = cp
                break
            refused.append(cp.returncode)
            time.sleep(0.05)
        check("the rival is refused (exit 3) until the lease runs out, then wins",
              won is not None and set(refused) <= {3}, f"won={won is not None} refused={refused[:8]}")
        stamp = ""
        if won is not None:
            shown = run(box, "show", mid(won)).stdout
            stamp = next((x[6:] for x in shown.splitlines() if x.startswith("date: ")), "")
        check("the winning claim is not dated before the expiry", stamp >= iso(expiry) > "",
              f"date={stamp} expiry={iso(expiry)}")
        now_claims = status(box).get("claims", {})
        check("status now: held 1 (the rival), expired 1, superseded 1",
              (now_claims.get("held"), now_claims.get("expired"), now_claims.get("superseded"))
              == (1, 1, 1), str(now_claims))
        holder = [r for r in rows(run(box, "list").stdout) or [] if r[0] == "CLAIM" and r[1] == "held"]
        check("list: the one held claim is now the rival's",
              len(holder) == 1 and holder[0][2] == rival, str(holder))
        back = send(box, owner, "all", "CLAIM", "too late", "again end", "--scope", SCOPE,
                    "--expires", iso(time.time() + 3600))
        check("the former owner is now the one refused", back.returncode == 3
              and f"already held by {rival}" in back.stderr, back.stderr)

    with phase("2. keyed sends: duplicate, a live sender, a sender killed mid-send", 14) as ph:
        keyed = ("bob", "all", "NOTICE", "deploy window moved", "now 14:00 UTC end", "--key", "k-deploy")
        first = send(box, *keyed)
        n_before = len(list(box.glob("*.md")))
        second = send(box, *keyed)
        check("keyed send writes one message", first.returncode == 0 and "wrote " in first.stdout,
              first.stderr)
        check("the same send again is reported as a duplicate of the original id",
              second.returncode == 0 and "duplicate of" in second.stdout
              and "nothing written" in second.stdout and mid(second) == mid(first) != "",
              second.stdout + second.stderr)
        check("the duplicate wrote nothing", len(list(box.glob("*.md"))) == n_before
              and len(with_subject(box, "deploy window moved")) == 1)
        crash = ("send", "--type", "NOTICE", "--from", "carol", "--to", "all", "--subject",
                 "schema migrated", "--body", "tables are at v7 end", "--key", "k-schema")
        ready = td / "ready"
        parked = park(box, ready, BEFORE_PUBLISH, *crash)
        check("a keyed sender is parked, alive, after reserving its key and before publishing",
              ready.exists() and parked.poll() is None, f"ready={ready.exists()} rc={parked.poll()}")
        t0 = time.monotonic()
        busy0, took0 = timed(box, *crash, "--key-wait", "0")
        busy1, took1 = timed(box, *crash, "--key-wait", str(KEY_WAIT))
        late = margin(t0, time.monotonic())
        ph.allow(KEY_WAIT)
        check("a retry with --key-wait 0 against the live sender is refused at once (exit 3)",
              busy0.returncode == 3 and "being sent by another process" in busy0.stderr
              and took0 <= late, f"rc={busy0.returncode} {took0:.2f}s (limit {late:.2f}s) {busy0.stderr}")
        check(f"with --key-wait {KEY_WAIT} it is refused after that wait: not sooner, not much later",
              busy1.returncode == 3 and "nothing written" in busy1.stderr
              and KEY_WAIT <= took1 <= KEY_WAIT + late,
              f"rc={busy1.returncode} {took1:.2f}s (limit {KEY_WAIT + late:.2f}s) {busy1.stderr}")
        check("neither refusal wrote anything or disturbed the live sender",
              with_subject(box, "schema migrated") == [] and parked.poll() is None)
        rc = kill(parked)
        ready.unlink(missing_ok=True)
        check("the sender is SIGKILLed there", rc == -9, f"rc={rc}")
        check("the killed send left no message (and no partial one)",
              with_subject(box, "schema migrated") == [] and run(box, "list").returncode == 0)
        # The retry runs in the background: phases 3 and 4 share the mailbox with it.
        retry = background.submit(timed, box, *crash, "--key-wait", "30")

    with phase("3. ask and answer, with a retried answer", 14):
        ask = send(box, "carol", "dave", "ASK", "which port does staging use", "need it today end")
        aid = mid(ask)
        check("ASK sent", ask.returncode == 0 and aid != "", ask.stderr)
        live = rows(run(box, "list", "--to", "dave", "--live").stdout) or []
        inbox = rows(run(box, "inbox", "--to", "dave").stdout) or []
        check("the ASK is open and live for dave, in list and in inbox",
              [(r[1], r[5]) for r in live if r[0] == "ASK"] == [("open", aid)]
              and (("ASK", "open", "carol", "dave", "which port does staging use", aid) in inbox),
              f"{live} {inbox}")
        check("status: one open ASK, and it is this one",
              status(box).get("asks", {}).get("open") == 1
              and status(box).get("asks", {}).get("oldest_open_id") == aid)
        reply = ("dave", "carol", "ANSWER", "staging port", "it is 8443 end",
                 "--reply-to", aid, "--key", "k-answer")
        ans = send(box, *reply)
        ans2 = send(box, *reply)
        check("ANSWER with --reply-to accepted", ans.returncode == 0, ans.stderr)
        check("the retried ANSWER is a duplicate of the first (same id, nothing written)",
              ans2.returncode == 0 and "duplicate of" in ans2.stdout and mid(ans2) == mid(ans) != "",
              ans2.stdout + ans2.stderr)
        answers = [r for r in rows(run(box, "list").stdout) or [] if r[0] == "ANSWER"]
        check("exactly one ANSWER exists, and it cites the ASK",
              len(answers) == 1 and f"reply_to: {aid}" in run(box, "show", answers[0][5]).stdout,
              str(answers))
        asked = with_subject(box, "which port does staging use")
        check("the ASK's derived status is now answered", [r[1] for r in asked] == ["answered"],
              str(asked))
        live = rows(run(box, "list", "--to", "dave", "--live").stdout) or []
        st = status(box)
        check("the ASK is no longer live for dave; status: no open ASK",
              not any(r[0] == "ASK" for r in live) and st.get("asks", {}).get("open") == 0
              and st.get("asks", {}).get("oldest_open_id") is None, f"{live} {st.get('asks')}")
        memo = send(box, "alice", "dave", "NOTICE", "standup moved", "now at 10:30 end")
        memo_id = mid(memo)
        check("a NOTICE addressed to dave (what the hook must show him throughout the burst)",
              memo.returncode == 0 and memo_id in hook(box, "dave").stdout, memo.stderr)

    want_n = len(AGENTS) * PER_AGENT
    with phase(f"4. burst: {len(AGENTS)} agents x {PER_AGENT} messages, 3 readers, "
               "a crash, duplicates, answers, claims", 40) as burst_phase:
        done = threading.Event()
        problems: list[str] = []
        passes = [0, 0, 0]
        latencies: list[float] = []
        give_up = time.monotonic() + STEP_TIMEOUT * 4   # no reader loops past this

        def writer(agent: str) -> list[tuple[int, str]]:
            out = []
            for n in range(PER_AGENT):
                cp, took = timed(box, "send", "--type", "NOTICE", "--from", agent, "--to", "all",
                                 "--subject", f"burst {agent} {n:02d}",
                                 "--body", f"payload {agent} {n:02d} end")
                latencies.append(took)
                out.append((cp.returncode, mid(cp)))
            return out

        def reader(slot: int) -> None:
            seen: set[str] = set()
            last = False
            while not last and time.monotonic() < give_up:
                last = done.is_set()     # one more full pass after the writers finish
                cp = run(box, "list")
                got = rows(cp.stdout)
                if cp.returncode != 0 or cp.stderr.strip() or got is None:
                    problems.append(f"list rc={cp.returncode} err={cp.stderr[:200]!r}")
                    continue
                burst = {r[5]: r for r in got if r[4].startswith("burst")}
                bad = [r for r in burst.values()
                       if not _BURST.match(r[4]) or r[0] != "NOTICE" or r[1] != "fresh"
                       or r[2] != r[4].split()[1]]
                if bad or len({r[5] for r in got}) != len(got):
                    problems.append(f"malformed or duplicated row: {bad[:2]}")
                if not seen <= set(burst):
                    problems.append("a message that was listed disappeared")
                seen = set(burst)
                if slot == 1 and burst:   # the second reader also opens the newest message
                    newest = burst[max(burst)]
                    text = run(box, "show", newest[5]).stdout
                    want = "payload " + newest[4].removeprefix("burst ") + " end"
                    if not (text.startswith(f"---\nid: {newest[5]}\n")
                            and text.rstrip("\n").endswith("\n\n" + want)):
                        problems.append(f"partial message shown: {text[-120:]!r}")
                for f in box.glob("*.md"):   # and reads every file directly
                    raw = f.read_text(encoding="utf-8")
                    if not complete(raw):
                        problems.append(f"partial file on disk: {f.name}")
                passes[slot] += 1

        def watcher() -> None:
            """The third reader: dave's inbox, `status --json` and his pickup hook."""
            total, last = 0, False
            while not last and time.monotonic() < give_up:
                last = done.is_set()
                cp = run(box, "inbox", "--to", "dave")
                got = rows(cp.stdout)
                if cp.returncode != 0 or cp.stderr.strip() or got is None \
                        or memo_id not in {r[5] for r in got}:
                    problems.append(f"inbox rc={cp.returncode} err={cp.stderr[:200]!r}")
                cp = run(box, "status", "--json")
                try:
                    st = json.loads(cp.stdout)
                except ValueError:
                    st = {}
                now_total = st.get("messages", {}).get("total", -1)
                if cp.returncode != 0 or cp.stderr.strip() or now_total < total \
                        or (st.get("quarantined"), st.get("stale_tmp")) != (0, 0):
                    problems.append(f"status rc={cp.returncode} total {total}->{now_total} "
                                    f"err={cp.stderr[:200]!r}")
                total = max(total, now_total)
                cp = hook(box, "dave")
                if cp.returncode != 0 or cp.stderr.strip() or memo_id not in cp.stdout \
                        or "live message(s) addressed to 'dave'" not in cp.stdout:
                    problems.append(f"hook rc={cp.returncode} out={cp.stdout[:120]!r}")
                passes[2] += 1

        def same_key_at_once() -> list[CP[str]]:
            gate = threading.Barrier(8)

            def one(_i: int) -> CP[str]:
                gate.wait(timeout=STEP_TIMEOUT)
                return send(box, "bob", "all", "NOTICE", "cache flushed", "all regions end",
                            "--key", "k-flush")
            with ThreadPoolExecutor(8) as eight:
                return list(eight.map(one, range(8)))

        def crash_argv(n: int) -> tuple[str, ...]:
            return ("send", "--type", "NOTICE", "--from", "carol", "--to", "all",
                    "--subject", f"crash carol {n:02d}", "--body", f"payload carol c{n:02d} end",
                    "--key", f"k-crash-{n:02d}")

        def crashing_writer() -> dict[str, Any]:
            """Send CRASH_N keyed messages; be SIGKILLed while publishing number
            CRASH_AT (temp file written, not linked); then start again from the
            first with the same keys, as a restarted agent would."""
            before = [run(box, *crash_argv(n)) for n in range(CRASH_AT)]
            flag = td / "ready-crash"
            proc = park(box, flag, AT_LINK, *crash_argv(CRASH_AT))
            reached = flag.exists()
            temps = [p.name for p in box.glob(f".*-crash-carol-{CRASH_AT:02d}.md.*.tmp")]
            rc = kill(proc)
            flag.unlink(missing_ok=True)
            again = [run(box, *crash_argv(n), "--key-wait", "30") for n in range(CRASH_N)]
            return {"before": before, "reached": reached, "temps": temps, "rc": rc, "again": again}

        def two_answer() -> tuple[CP[str], list[tuple[str, CP[str]]]]:
            """An ASK to everyone; bob and carol each answer it three times at once."""
            ask = send(box, "alice", "all", "ASK", "who owns the staging certificate",
                       "it expires on friday end")

            def answer(i: int) -> tuple[str, CP[str]]:
                who = ("bob", "carol")[i % 2]
                return who, send(box, who, "alice", "ANSWER", "staging certificate",
                                 f"{who} owns it end", "--reply-to", mid(ask), "--key", "k-cert")
            with ThreadPoolExecutor(6) as six:
                return ask, list(six.map(answer, range(6)))

        def lanes() -> tuple[list[CP[str]], int]:
            """LANES keyed, scoped claims in a row while `status` runs in a loop.
            Exit 3 here could only be a spurious "being claimed right now"."""
            got, spurious = [], 0
            for n in range(LANES):
                for _try in range(5):
                    cp = send(box, "dave", "all", "CLAIM", f"lane {n:02d}", "taking this lane end",
                              "--scope", f"dir:lanes/{n:02d}", "--key", f"k-lane-{n:02d}",
                              "--expires", iso(time.time() + 3600))
                    if cp.returncode != 3:
                        break
                    spurious += 1
                got.append(cp)
            return got, spurious

        with ThreadPoolExecutor(len(AGENTS) + 7) as pool:
            try:
                readers = [pool.submit(reader, 0), pool.submit(reader, 1), pool.submit(watcher)]
                f_flush, f_crash = pool.submit(same_key_at_once), pool.submit(crashing_writer)
                f_cert, f_lanes = pool.submit(two_answer), pool.submit(lanes)
                sent = [x for per in pool.map(writer, AGENTS) for x in per]
                flushed = f_flush.result(timeout=STEP_TIMEOUT * 4)
                crashed = f_crash.result(timeout=STEP_TIMEOUT * 4)
                cert_ask, cert_answers = f_cert.result(timeout=STEP_TIMEOUT * 4)
                laned, spurious = f_lanes.result(timeout=STEP_TIMEOUT * 4)
            finally:
                done.set()               # whatever happened, the readers must stop
            for r in readers:
                r.result(timeout=STEP_TIMEOUT * 4)

        ids = [i for _, i in sent]
        check(f"all {want_n} sends succeeded", [rc for rc, _ in sent] == [0] * want_n,
              str([rc for rc, _ in sent if rc]))
        check("every id is unique", len(set(ids)) == want_n and "" not in ids,
              f"{len(set(ids))} distinct")
        listing = rows(run(box, "list").stdout) or []
        listed = [r for r in listing if _BURST.match(r[4])]
        check(f"exactly {want_n} burst messages are listed, with exactly the ids the senders got",
              len(listed) == want_n and {r[5] for r in listed} == set(ids), str(len(listed)))
        check("every (agent, number) pair arrived exactly once",
              sorted(r[4] for r in listed)
              == sorted(f"burst {a} {n:02d}" for a in AGENTS for n in range(PER_AGENT)))
        check("all three readers ran throughout and never failed or saw a partial or malformed message",
              not problems and min(passes) >= 2, f"passes={passes} {problems[:3]}")
        print(f"        (reader passes over the mailbox during the burst: {passes[0]}, {passes[1]} "
              f"and {passes[2]})")

    with phase("4b. what happened during the burst", 12):
        slow = 1.0 + 25 * PACER[0].pace(burst_phase.t0, burst_phase.t1)
        check("no single burst send was held up (latency within 1 s + 25 pacer sends)",
              len(latencies) == want_n and max(latencies) <= slow,
              f"max {max(latencies, default=0):.2f}s limit {slow:.2f}s")
        print(f"        (burst send latency: median {statistics.median(latencies):.3f} s, "
              f"max {max(latencies):.3f} s, limit {slow:.2f} s)")

        wrote = [cp for cp in flushed if cp.returncode == 0 and "wrote " in cp.stdout]
        dups = [cp for cp in flushed if cp.returncode == 0 and "duplicate of" in cp.stdout]
        check("one key sent from 8 processes at once: one wrote, seven are duplicates of its id",
              len(wrote) == 1 and len(dups) == 7 and {mid(cp) for cp in flushed} == {mid(wrote[0])},
              str([(cp.returncode, cp.stdout[:40], cp.stderr[:80]) for cp in flushed]))
        check("...and exactly one such message exists",
              [r[5] for r in listing if r[4] == "cache flushed"] == [mid(cp) for cp in wrote])

        check("the crashing writer was SIGKILLed mid-publish: temp file written, nothing linked",
              crashed["reached"] and crashed["rc"] == -9 and len(crashed["temps"]) == 1
              and [cp.returncode for cp in crashed["before"]] == [0] * CRASH_AT,
              str({k: v for k, v in crashed.items() if k in ("reached", "rc", "temps")}))
        again = crashed["again"]
        check("restarted with the same keys: what was sent is a duplicate, the rest is written",
              [cp.returncode for cp in again] == [0] * CRASH_N
              and [mid(cp) for cp in again[:CRASH_AT]] == [mid(cp) for cp in crashed["before"]]
              and all("duplicate of" in cp.stdout for cp in again[:CRASH_AT])
              and all("wrote " in cp.stdout for cp in again[CRASH_AT:]),
              str([(cp.returncode, cp.stdout[:30], cp.stderr[:80]) for cp in again]))
        crash_rows = sorted((r[4], r[5]) for r in listing if r[4].startswith("crash carol"))
        check(f"exactly {CRASH_N} messages from the crashing writer, one per key, none twice",
              crash_rows == sorted((f"crash carol {n:02d}", mid(cp)) for n, cp in enumerate(again)),
              str(crash_rows))
        left = sorted(p.name for p in box.glob(".*.tmp"))
        st = status(box)
        check("the killed writer's temp file is still there, is not listed, and is not yet stale",
              left == crashed["temps"] and st.get("stale_tmp") == 0
              and not any(crashed["temps"][0][1:27] == r[5] for r in listing) if left else False,
              f"{left} stale_tmp={st.get('stale_tmp')}")
        for name in left:                # ten minutes pass
            old = time.time() - 700
            os.utime(box / name, (old, old))
        doc = run(box, "doctor")
        check("once it is older than ten minutes, status and doctor report it by name",
              status(box).get("stale_tmp") == 1 and "stale_tmp: 1" in doc.stdout
              and all(name in doc.stdout for name in left) and len(left) == 1,
              doc.stdout[-300:])
        check("...and `list` still neither lists nor rejects it",
              rows(run(box, "list").stdout) == listing and run(box, "list").stderr == "")
        for name in left:                # the operator removes what doctor named
            (box / name).unlink()

        by_who = {who: [cp for w, cp in cert_answers if w == who] for who in ("bob", "carol")}
        check("an ASK answered by two agents at once, three attempts each: every attempt exits 0",
              cert_ask.returncode == 0 and [cp.returncode for _, cp in cert_answers] == [0] * 6,
              str([(w, cp.returncode, cp.stderr[:80]) for w, cp in cert_answers]))
        check("each agent's answer was written once; its other attempts are duplicates of it",
              all(sum("wrote " in cp.stdout for cp in cps) == 1
                  and sum("duplicate of" in cp.stdout for cp in cps) == 2
                  and len({mid(cp) for cp in cps}) == 1 for cps in by_who.values()),
              str([(w, cp.stdout[:30]) for w, cp in cert_answers]))
        cert = [r for r in listing if r[4] == "staging certificate"]
        check("two ANSWERs exist for it, one from each; the ASK is answered; no ASK is open",
              sorted((r[0], r[2]) for r in cert) == [("ANSWER", "bob"), ("ANSWER", "carol")]
              and [r[1] for r in listing if r[5] == mid(cert_ask)] == ["answered"]
              and st.get("asks", {}).get("open") == 0, f"{cert} {st.get('asks')}")

        attempts = LANES + spurious
        check(f"{LANES} keyed, scoped claims under a `status` loop: all accepted, "
              "at most one spurious exit 3",
              [cp.returncode for cp in laned] == [0] * LANES and spurious <= 1
              and st.get("claims", {}).get("held") == 1 + LANES,
              f"{[cp.returncode for cp in laned]} spurious={spurious} {st.get('claims')}")
        print(f"        (spurious exit 3 from a claim during the status loop: {spurious} of "
              f"{attempts} attempts; status ran {passes[2]} times)")

    with phase("2b. keyed retry after the killed sender (ran in the background)", 5):
        try:
            cp, took = retry.result(timeout=STEP_TIMEOUT + 15)
        except FutureTimeout as exc:   # a finding, not a crash
            cp, took = CP([], 1, "", repr(exc)), float(STEP_TIMEOUT)
        background.shutdown()
        check("the retry with the same key succeeds (it took the dead sender's key over)",
              cp.returncode == 0 and mid(cp) != "", cp.stdout + cp.stderr)
        check(f"it completes within {RETRY_LIMIT:.0f} s", took < RETRY_LIMIT, f"{took:.1f}s")
        check("exactly one message exists for the killed-and-retried send",
              [r[5] for r in with_subject(box, "schema migrated")] == [mid(cp)],
              str(with_subject(box, "schema migrated")))
        third = send(box, "carol", "all", "NOTICE", "schema migrated", "tables are at v7 end",
                     "--key", "k-schema")
        check("a further retry is a duplicate of it; still exactly one message",
              third.returncode == 0 and mid(third) == mid(cp)
              and len(with_subject(box, "schema migrated")) == 1, third.stdout + third.stderr)
    EXTRA_TIMINGS.append(("    the retry itself, from its start in phase 2", took))

    with phase("5. closing: doctor, status, leftovers", 5):
        canary = run(box, "canary", "--from", "alice", "--to", "all")
        check("pickup canary filed", canary.returncode == 0, canary.stderr)
        doc = run(box, "doctor")
        check("doctor passes: store OK, no rejects, no stale temp file",
              doc.returncode == 0 and "PASS" in doc.stdout and "store: OK" in doc.stdout
              and "stale_tmp: 0" in doc.stdout and "rejects" not in doc.stdout,
              doc.stdout + doc.stderr)
        check("doctor reports the expired lease, and only that one",
              "orphan_claims: 1" in doc.stdout and f"{rid} (EXPIRED)" in doc.stdout, doc.stdout)
        st = status(box)
        # burst + keyed + killed-and-retried + memo + flushed + crashing writer + canary
        n_notice = want_n + 4 + CRASH_N + 1
        n_all = n_notice + (3 + LANES) + 2 + 3
        by_type = st.get("messages", {}).get("by_type", {})
        check(f"status: exact totals ({3 + LANES} CLAIM, 2 ASK, 3 ANSWER, {n_notice} NOTICE)",
              st.get("messages", {}).get("total") == n_all
              and (by_type.get("CLAIM"), by_type.get("ASK"), by_type.get("ANSWER"),
                   by_type.get("NOTICE")) == (3 + LANES, 2, 3, n_notice), json.dumps(st.get("messages")))
        check("status: nothing quarantined, no stale temp file, no open ASK, nobody stalled",
              (st.get("quarantined"), st.get("stale_tmp"), st.get("asks", {}).get("open"),
               st.get("stalled_agents")) == (0, 0, 0, []), json.dumps(st)[:300])
        check("status: all four agents are active",
              {a["agent"]: a["state"] for a in st.get("agents", [])}
              == dict.fromkeys(AGENTS, "active"), str(st.get("agents")))
        files = sorted(p.name for p in box.iterdir())
        check("one file per message, nothing else visible",
              len([f for f in files if not f.startswith(".")]) == n_all
              and all(f.endswith(".md") for f in files if not f.startswith(".")))
        hidden = [f for f in files if f.startswith(".")]
        leftover = [f for f in hidden
                    if f.endswith((".lock", ".tmp", ".takeover")) or f.startswith(".scope.")]
        check("no scope lock, temp or takeover file left behind", not leftover, str(leftover))
        published = {f[:26] for f in files if not f.startswith(".")}
        orphans = [f for f in hidden
                   if not set((box / f).read_text(encoding="utf-8").split()) & published]
        # k-deploy, k-schema, k-answer, k-flush, k-cert twice, the crashing writer, the lanes
        check("every other hidden file is an idempotency marker of a published message, one per key",
              not orphans and all(f.startswith(".idem.") for f in hidden)
              and len(hidden) == 6 + CRASH_N + LANES, str(hidden))
        stray = sorted(p.name for p in td.iterdir() if p.name != "mail")
        check("nothing left next to the mailbox", not stray, str(stray))


def main() -> int:
    started = time.monotonic()
    arm_watchdog()
    scratch = tempfile.TemporaryDirectory()
    aside = tempfile.TemporaryDirectory()     # the pacer's mailbox: same filesystem, not in the way
    td, box = Path(scratch.name), Path(scratch.name) / "mail"
    box.mkdir()
    WATCHED.append(box)
    pacer = Pacer(Path(aside.name) / "pace")
    PACER.append(pacer)
    try:
        t0 = time.monotonic()
        pacer.calibrate()
        EXTRA_TIMINGS.append(("calibration: five pacer sends", time.monotonic() - t0))
        pacer.start()
        scenario(td, box)
    except Exception:   # a harness error is a failed run with a reason, not a hang or a bare trace
        fail("the scenario ran to its end", traceback.format_exc()[-600:])
    finally:
        pacer.halt.set()
        pacer.join(STEP_TIMEOUT + 15)

    print("6. time budgets, teardown, child processes")
    over = []
    for p in PHASES:
        allowed, _pace = p.budget()
        if p.took > allowed:
            over.append(f"{p.name[:24]!r} took {p.took:.2f}s, budget {allowed:.2f}s")
    check("the pacer kept sending after its calibration, stopped when told, and no send of it failed",
          not pacer.is_alive() and pacer.failed == 0 and len(pacer.samples) > 5,
          f"alive={pacer.is_alive()} failed={pacer.failed} samples={len(pacer.samples)}")
    check("every phase finished within its time budget", not over, "; ".join(over))
    errors = []
    for scratch_dir in (scratch, aside):
        try:
            scratch_dir.cleanup()
        except OSError as exc:
            errors.append(repr(exc))
    check("teardown removed both scratch directories without an error",
          not errors and not td.exists() and not Path(aside.name).exists(), str(errors))
    unreaped = [p.pid for p in CHILDREN if p.returncode is None]
    try:
        stray_child = os.waitpid(-1, os.WNOHANG)   # ChildProcessError: this process has no child
    except ChildProcessError:
        stray_child = None
    check(f"all {len(CHILDREN)} child processes were reaped and none is left running",
          not unreaped and stray_child is None, f"unreaped={unreaped} waitpid={stray_child}")
    FINISHED.set()
    faulthandler.cancel_dump_traceback_later()

    total = time.monotonic() - started
    print(f"timings (wall clock; budget = {BUDGET_FLOOR:g} s + waits + {BUDGET_SLACK:g} x usual sends "
          f"x slowest pacer send in the phase; base pace {pacer.base:.3f} s):")
    for label, seconds in EXTRA_TIMINGS[:1]:
        print(f"  {seconds:6.2f} s  {label}")
    for p in PHASES:
        allowed, pace = p.budget()
        print(f"  {p.took:6.2f} s  {p.name}  [budget {allowed:.1f} s at {pace:.3f} s/send]")
    for label, seconds in EXTRA_TIMINGS[1:]:
        print(f"  {seconds:6.2f} s  {label}")
    print(f"  {total:6.2f} s  total")
    if FAILS:
        print(f"\n{len(FAILS)} FAILED:")
        for failure in FAILS:
            print("  " + failure)
        return 1
    print("\nall field checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
