#!/usr/bin/env python3
"""Concurrency stress test: parallel senders vs. readers that poll while they write.

Four properties, all asserted on content that must appear (PROTOCOL.md §8):

1. No message is lost or overwritten: N sends -> N files with N distinct ids.
2. No reader ever observes a half-written message. The reader here is
   deliberately independent of agent_mail.py: it globs ``*.md`` and requires
   every file it opens to end with the sentinel the body carries. A torn
   write (truncated body, unclosed frontmatter) fails this.
3. The tool's own readers stay clean while senders run: `list` never reports
   a REJECT or exits non-zero, and `status --json` and `doctor` never crash on
   a temp file that appears or vanishes mid-scan.
4. Every sender also fires one keyed send with the same key: exactly one of
   them writes, the rest answer "duplicate of" (PROTOCOL.md §31).
5. One more sender is SIGKILLed mid-publish (temp file written, not linked)
   while the others run: they are unaffected, the count is exact for the
   survivors, and what it leaves is one hidden temp file of its own that no
   reader lists or rejects.
6. No writer is starved: the slowest send stays within 20 median sends + 5 s
   while the readers loop.
7. Every sender slot also fires one CLAIM on the same --scope, each from a
   different identity, in the middle of all of the above: exactly one is
   written, every other is refused with exit 3, and no lock file is left
   (PROTOCOL.md §30). So the mailbox sees unkeyed, keyed and scoped writers
   at once.

Bounded: every child is waited for with a timeout, and a watchdog dumps every
thread's stack and exits non-zero if the run has not finished in WATCHDOG
seconds.

8. Afterwards (--faults ci, the default; `full` for everything; `off` to skip): a real
   send is killed at every write step (SIGKILL, and SIGTERM in `full`), 3-4 real senders
   are interleaved with random stalls and kill points under recorded seeds, hostile files
   and PARTICIPANTS.md edge cases meet every reader. The method, the invariants and the
   counts of a full run are in docs/fault-injection-results.md.

Usage: python stress_test.py [--senders 16] [--per-sender 6] [--kb 2048] [--faults ci|full|off]
"""
from __future__ import annotations

import argparse
import calendar
import faulthandler
import json
import os
import random
import re
import shutil
import signal
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)   # Windows: Popen.send_signal(SIGTERM) is TerminateProcess
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
KILLED_RC = -9 if os.name != "nt" else 1   # what Popen.kill() leaves: SIGKILL / TerminateProcess
SENTINEL = "END-OF-BODY-SENTINEL"
STEP_TIMEOUT = 300   # seconds; any single child
WATCHDOG = 900       # seconds; the whole run


def sender(box: Path, body_file: Path, n: int, tag: str, errors: list, took: list) -> None:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    for i in range(n):
        t0 = time.monotonic()
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
             "--from", f"agent-{tag}", "--to", "all", "--subject", f"stress {tag} {i}",
             "--body-file", str(body_file)],
            capture_output=True, text=True, env=env, timeout=STEP_TIMEOUT)
        took.append(time.monotonic() - t0)
        if r.returncode != 0:
            errors.append(f"send failed rc={r.returncode}: {r.stderr.strip()[:200]}")


def tool_reader(box: Path, stop: threading.Event, bad: list, runs: list) -> None:
    """The tool's own read paths, in a loop, for as long as the senders run."""
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    while not stop.is_set():
        for argv in (["list"], ["status", "--json"], ["doctor"]):
            r = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), *argv],
                               capture_output=True, text=True, env=env, timeout=STEP_TIMEOUT)
            runs.append(argv[0])
            if "Traceback" in r.stderr or "REJECT" in r.stderr + r.stdout:
                bad.append(f"{argv[0]}: {(r.stderr or r.stdout).strip()[-200:]}")
            elif argv[0] != "doctor" and r.returncode != 0:  # doctor exits 2 without a canary
                bad.append(f"{argv[0]} rc={r.returncode}: {r.stderr.strip()[-200:]}")


def keyed(box: Path, results: list) -> None:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    env.pop("AGENT_MAIL_IDEM_WAIT", None)
    r = subprocess.run(
        [sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
         "--from", "agent-keyed", "--to", "all", "--subject", "keyed burst",
         "--body", "one of many " + SENTINEL, "--key", "burst"],
        capture_output=True, text=True, env=env, timeout=STEP_TIMEOUT)
    results.append((r.returncode, r.stdout, r.stderr))


def claimer(box: Path, n: int, results: list) -> None:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    r = subprocess.run(
        [sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "CLAIM",
         "--from", f"claimer-{n}", "--to", "all", "--subject", "scoped burst",
         "--body", "one winner " + SENTINEL, "--scope", "stress:one-scope", "--expires", expires],
        capture_output=True, text=True, env=env, timeout=STEP_TIMEOUT)
    results.append((r.returncode, r.stdout, r.stderr))


def killed_sender(box: Path, body_file: Path, out: dict) -> None:
    """One more sender, SIGKILLed at a known point while the others run: the
    child is the real `agent_mail.main` with os.link made to create a
    ready-file and block, so its 2 MB temp file is complete on disk and not yet
    linked. The parent polls for the ready-file in 0.02 s steps, 60 s at most."""
    ready = box.with_name("killed.ready")
    argv = ["send", "--type", "NOTICE", "--from", "agent-killed", "--to", "all",
            "--subject", "stress killed", "--body-file", str(body_file)]
    code = ("import os, signal, sys\n"
            f"if hasattr(signal, 'alarm'):\n    signal.alarm({WATCHDOG})\n"
            "else:\n    import threading\n"
            f"    _wd = threading.Timer({WATCHDOG}, os._exit, [1])\n"
            "    _wd.daemon = True\n    _wd.start()\n"
            f"sys.path.insert(0, {str(HERE)!r})\n"
            "import agent_mail\n"
            "def park(*_a, **_k):\n"
            f"    open({str(ready)!r}, 'w').close()\n"
            "    import time as _t\n"
            "    while True:  # not signal.pause(): a signal just before it would be lost\n"
            "        _t.sleep(0.05)\n"
            "os.link = park\n"
            f"sys.exit(agent_mail.main({argv!r}))\n")
    proc = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "AGENT_MAIL_DIR": str(box)},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    out.update(reached=ready.exists(), pid=proc.pid)
    proc.kill()
    out["rc"] = proc.wait(timeout=STEP_TIMEOUT)
    ready.unlink(missing_ok=True)


def reader(box: Path, stop: threading.Event, torn: list, seen: set) -> None:
    while not stop.is_set():
        for p in box.glob("*.md"):
            try:
                text = p.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            seen.add(p.name)
            if not text.rstrip().endswith(SENTINEL) or text.count("\n---\n") < 1:
                torn.append((p.name, len(text)))


# ---- fault injection: kill a real send at every write step (docs/fault-injection-results.md) ----
# A child runs the real agent_mail.main with the write primitives (os.open for writing, write,
# fsync, ftruncate, link, replace, rename, unlink, flock, close of a tracked descriptor, and
# open for writing) wrapped when called from agent_mail or pathlib. The child numbers the
# points (a "pre" and a "post" per call) and blocks at the one asked for; the parent SIGKILLs
# (or SIGTERMs) it there. A dry run of the same send lists the points. Then every invariant is
# checked on the mailbox the dead sender left, with a reader of the mailbox that does not use
# agent_mail, and a later correct send must succeed.
FI_CHILD = r'''
import os, sys, time, signal, builtins
if hasattr(signal, "alarm"):
    signal.alarm(120)
else:   # no SIGALRM on Windows: a daemon timer ends the child instead
    import threading
    _wd = threading.Timer(120, os._exit, [1])
    _wd.daemon = True
    _wd.start()
sys.path.insert(0, @WT@)
import agent_mail
TARGET = int(os.environ.get("FI_TARGET", "-1"))
LOG, READY = os.environ.get("FI_LOG", ""), os.environ.get("FI_READY", "")
STALLS = {int(a): int(b) for a, b in (x.split(":") for x in os.environ.get("FI_STALLS", "").split(",") if x)}
pt, tracked, real_open = [0], set(), builtins.open
def block(*_a, **_k):
    real_open(READY, "w").close()
    while True:
        time.sleep(0.05)
def mark(label):
    pt[0] += 1
    if LOG:
        with real_open(LOG, "a") as f:
            f.write(label + "\n")
    if STALLS.get(pt[0]):
        time.sleep(STALLS[pt[0]] / 1000.0)
    if pt[0] == TARGET:
        block()
def mine():
    n = sys._getframe(2).f_globals.get("__name__", "")
    return n == "agent_mail" or n.startswith("pathlib")
def nm(p):
    return "fd" if isinstance(p, int) else os.path.basename(os.fspath(p))
def wrap(mod, name, label, track=False, only=None):
    orig = getattr(mod, name)
    def w(*a, **k):
        if not mine() or (only and not only(*a, **k)):
            return orig(*a, **k)
        lab = label(*a, **k)
        mark("pre  " + lab)
        r = orig(*a, **k)
        if track:
            tracked.add(r)
        mark("post " + lab)
        return r
    setattr(mod, name, w)
wrap(os, "open", lambda p, *a, **k: "os.open " + nm(p), True, lambda p, fl, *a, **k: bool(fl & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)))
wrap(os, "write", lambda fd, *a: "os.write")
wrap(os, "fsync", lambda fd: "os.fsync")
wrap(os, "ftruncate", lambda fd, n: "os.ftruncate")
wrap(os, "link", lambda s, d, *a, **k: "os.link -> " + nm(d))
wrap(os, "replace", lambda s, d, *a, **k: "os.replace -> " + nm(d))
wrap(os, "rename", lambda s, d, *a, **k: "os.rename -> " + nm(d))
wrap(os, "unlink", lambda p, *a, **k: "os.unlink " + nm(p))
wrap(os, "close", lambda fd: "os.close", False, lambda fd: fd in tracked)
wrap(agent_mail, "_flock", lambda fd, *a, **k: "flock")   # the lock call, whichever platform it maps to
if os.name == "nt":   # the lock and marker files are opened with CreateFileW there, not os.open
    wrap(agent_mail, "_open", lambda p, *a, **k: "_open " + nm(p), True,
         lambda p, fl, *a, **k: bool(fl & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)))
def aopen(path, mode="r", *a, **k):
    if any(c in mode for c in "wax+"):
        mark("pre  open(w) " + nm(path))
        r = real_open(path, mode, *a, **k)
        mark("post open(w) " + nm(path))
        return r
    return real_open(path, mode, *a, **k)
agent_mail.open = aopen
if os.environ.get("FI_PATCH"):
    exec(os.environ["FI_PATCH"])
sys.exit(agent_mail.main(@ARGV@))
'''
FI_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}-.*\.md$")
FI_PART = ("# Participants\n\n| Identity | Reachable | Mechanism | Verified | can | cannot | uid |\n"
           "|---|---|---|---|---|---|---|\n"
           + "".join(f"| `{n}` | no | poll | no | | | |\n" for n in ("alice", "bob", "carol", "dave"))
           + "\n## Aliases\n\n| Alias | Writers |\n|---|---|\n| `crew` | `bob`, `carol`, `dave` |\n")
FI_SCOPE = "file:x/y.py"


def fi_env(box: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_MAIL", "FI_"))}
    env["AGENT_MAIL_DIR"] = str(box)
    env.update(extra)
    return env


def fi_cli(box: Path, *argv: str, ident: str = "") -> subprocess.CompletedProcess[str]:
    env = fi_env(box, **({"AGENT_MAIL_IDENTITY": ident} if ident else {}))
    return subprocess.run([sys.executable, "-I", str(HERE / "agent_mail.py"), *argv], env=env,
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


def fi_spawn(box: Path, argv: list[str], target: int = -1, log: str = "", ready: str = "",
             patch: str = "", stalls: str = "") -> subprocess.Popen[str]:
    code = FI_CHILD.replace("@WT@", repr(str(HERE))).replace("@ARGV@", repr(list(argv)))
    env = fi_env(box, FI_TARGET=str(target), FI_LOG=log, FI_READY=ready, FI_PATCH=patch,
                 FI_STALLS=stalls)
    return subprocess.Popen([sys.executable, "-I", "-c", code], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def fi_trace(box: Path, argv: list[str], tmp: Path) -> tuple[int, list[str]]:
    """The points of one undisturbed send, in order."""
    log = tmp / "trace.log"
    log.unlink(missing_ok=True)
    p = fi_spawn(box, argv, -1, str(log), str(tmp / "never"))
    p.communicate(timeout=120)
    return p.returncode, (log.read_text().splitlines() if log.exists() else [])


def fi_kill_at(box: Path, argv: list[str], target: int, tmp: Path,
               sig: int = SIGKILL) -> tuple[bool, Any, str]:
    ready = tmp / "ready"
    ready.unlink(missing_ok=True)
    p = fi_spawn(box, argv, target, "", str(ready))
    deadline = time.monotonic() + 30
    while not ready.exists() and p.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    parked, absorbed = ready.exists(), False
    if parked:
        p.send_signal(sig)
        if sig != SIGKILL:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                absorbed = True    # the handler ignored it: a release was already running
                p.kill()
    _, err = p.communicate(timeout=60)
    return parked, ("absorbed" if absorbed else p.returncode), err


def fi_snapshot(box: Path) -> dict[str, tuple[int, int, int, int]]:
    snap = {}
    for n in sorted(os.listdir(box)):
        try:
            st = os.lstat(box / n)
            snap[n] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino)
        except OSError:
            pass
    return snap


def fi_iso(delta_s: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delta_s))


def fi_msgs(box: Path, **kv: str) -> list[dict[str, str]]:
    """The mailbox read without agent_mail: header fields of every message file."""
    out = []
    for n in sorted(os.listdir(box)):
        if not FI_ULID.match(n):
            continue
        t = (box / n).read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n", t, re.S)
        d = {"_file": n, "_text": t, "_torn": "" if m else "1"}
        for line in (m.group(1).split("\n") if m else []):
            if ": " in line:
                k, v = line.split(": ", 1)
                d[k] = v
        if all(d.get(k) == v for k, v in kv.items()):
            out.append(d)
    return out


def fi_held(box: Path, scope: str = FI_SCOPE) -> set[str]:
    ms = fi_msgs(box)
    sup = {m["supersedes"].upper() for m in ms if m.get("supersedes")}
    now = time.time()
    held = set()
    for m in ms:
        if m.get("type") == "CLAIM" and m.get("scope") == scope and m["id"].upper() not in sup:
            exp = calendar.timegm(time.strptime(m["expires"], "%Y-%m-%dT%H:%M:%SZ"))
            if exp > now:
                held.add(m["from"])
    return held


def fi_torn(box: Path) -> list[str]:
    return [f"{m['_file']}: torn or truncated" for m in fi_msgs(box)
            if m["_torn"] or not m["_text"].endswith("\n") or "BODYMARK" not in m["_text"]]


def fi_hidden(box: Path, prefix: str) -> list[str]:
    return sorted(n for n in os.listdir(box) if n.startswith(prefix))


def fi_problems(box: Path) -> list[str]:
    try:
        return [x["code"] for x in json.loads(fi_cli(box, "status", "--json").stdout)["problems"]]
    except (ValueError, KeyError):
        return ["UNPARSEABLE"]


def fi_readers(box: Path, full: bool) -> list[str]:
    """Readers on a mailbox a sender died in: no traceback, valid JSON, mailbox unchanged."""
    bad, before = [], fi_snapshot(box)
    cmds = [["list", "--json"], ["status", "--json"]]
    if full:
        cmds += [["list"], ["inbox", "--to", "bob"], ["status"], ["doctor"]]
    for argv in cmds:
        p = fi_cli(box, *argv)
        if "Traceback" in p.stderr or p.returncode not in (0, 2):
            bad.append(f"{argv[0]}: exit {p.returncode} {p.stderr[-160:]}")
        if "--json" in argv:
            try:
                json.loads(p.stdout)
            except ValueError as e:
                bad.append(f"{argv[0]} --json is not JSON: {e}")
    h = subprocess.run([sys.executable, "-I", str(HERE / "hooks" / "agent_mail_check.py")],
                       env=fi_env(box, AGENT_MAIL_IDENTITY="bob"), capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    if h.returncode != 0 or "Traceback" in h.stderr + h.stdout:
        bad.append(f"hook: exit {h.returncode} {h.stderr[-160:]}")
    if fi_snapshot(box) != before:
        bad.append("a reader changed the mailbox")
    return bad


def fi_notice(frm: str, to: str, key: str = "", subj: str = "hello") -> list[str]:
    a = ["send", "--type", "NOTICE", "--from", frm, "--to", to, "--subject", subj,
         "--body", "BODYMARK notice"]
    return a + (["--key", key] if key else [])


def fi_claim(frm: str, to: str, scope: str = FI_SCOPE, exp: int = 3600, key: str = "",
             sup: str = "", subj: str = "claim") -> list[str]:
    a = ["send", "--type", "CLAIM", "--from", frm, "--to", to, "--subject", subj,
         "--body", "BODYMARK claim", "--expires", fi_iso(exp)]
    if scope:
        a += ["--scope", scope]
    if key:
        a += ["--key", key]
    if sup:
        a += ["--supersedes", sup]
    return a


def fi_new_box(root: Path, name: str) -> Path:
    box = root / name
    box.mkdir(parents=True)
    (box / "PARTICIPANTS.md").write_text(FI_PART)
    return box


def fi_orphan(box: Path, argv: list[str]) -> None:
    """The state a SIGKILLed send leaves: parked after its reservations, before it publishes."""
    ready = box.parent / f"{box.name}.orphan.ready"
    p = fi_spawn(box, argv, -1, "", str(ready), patch="agent_mail._publish = block")
    deadline = time.monotonic() + 30
    while not ready.exists() and p.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), "the orphan-making send did not park"
    p.kill()
    p.communicate()


def fi_no_lock(box: Path, f: list[str]) -> None:
    left = fi_hidden(box, ".scope")
    if left:
        f.append(f"scope lock file left after recovery: {left}")


def fi_sc_plain() -> dict[str, Any]:
    def rec(box: Path, ctx: dict[str, Any]) -> list[str]:
        f, n = [], len(fi_msgs(box, type="NOTICE"))
        if n > 1:
            f.append(f"{n} messages from one send")
        r = fi_cli(box, *fi_notice("alice", "bob", subj="again"))
        if r.returncode != 0 or len(fi_msgs(box, type="NOTICE")) != n + 1:
            f.append(f"a later plain send: exit {r.returncode}, count {len(fi_msgs(box, type='NOTICE'))}")
        return f
    return {"setup": lambda b: {"argv": fi_notice("alice", "bob")}, "recover": rec}


def fi_sc_keyed(fan: bool = False) -> dict[str, Any]:
    recips = ("bob", "carol", "dave") if fan else ("bob",)

    def rec(box: Path, ctx: dict[str, Any]) -> list[str]:
        f = []
        if len(fi_msgs(box, type="NOTICE")) > len(recips):
            f.append("more messages than recipients after the kill")
        t0 = time.monotonic()
        r = fi_cli(box, *ctx["argv"], "--key-wait", "5")
        if r.returncode != 0 or time.monotonic() - t0 > 3:
            f.append(f"keyed retry: exit {r.returncode} in {time.monotonic() - t0:.1f}s {r.stderr[-120:]}")
        for w in recips:
            if len(fi_msgs(box, type="NOTICE", to=w)) != 1:
                f.append(f"{len(fi_msgs(box, type='NOTICE', to=w))} messages for {w}, want exactly 1")
        again = fi_cli(box, *ctx["argv"])
        if again.returncode != 0 or len(fi_msgs(box, type="NOTICE")) != len(recips):
            f.append("a second retry was not a no-op")
        if fi_cli(box, *fi_notice("alice", recips[0] if not fan else "crew", "k1", "other")).returncode != 2:
            f.append("same key with other content was not refused (exit 2)")
        f += [f"problem after recovery: {c}" for c in fi_problems(box)]
        return f
    a0 = fi_notice("alice", "crew" if fan else "bob", key="k1")
    return {"setup": lambda b: {"argv": a0}, "recover": rec}


def fi_sc_claim(keyed: bool, fan: bool = False) -> dict[str, Any]:
    tos = ("bob", "carol", "dave") if fan else ("bob",)

    def rec(box: Path, ctx: dict[str, Any]) -> list[str]:
        f = []
        landed = bool(fi_msgs(box, type="CLAIM"))
        if len(fi_msgs(box, type="CLAIM")) > len(tos):
            f.append("more claim files than recipients after the kill")
        if keyed:
            r0 = fi_cli(box, *ctx["argv"], "--key-wait", "5")
            if r0.returncode != 0:
                f.append(f"owner's keyed retry: exit {r0.returncode} {r0.stderr[-120:]}")
            for w in tos:
                if len(fi_msgs(box, type="CLAIM", to=w)) != 1:
                    f.append(f"claim copies for {w}: {len(fi_msgs(box, type='CLAIM', to=w))}")
        r = fi_cli(box, *fi_claim("erin", "erin", subj="rival"))
        if "being claimed right now" in r.stderr:
            f.append("a dead holder's scope lock blocked a later claim")
        if (landed or keyed) != (r.returncode == 3):
            f.append(f"rival exit {r.returncode} (a claim exists: {landed or keyed})")
        if len(fi_held(box)) != 1:
            f.append(f"holders after: {sorted(fi_held(box))}")
        fi_no_lock(box, f)
        return f
    a0 = fi_claim("alice", "crew" if fan else "bob", key="kc" if keyed else "")
    return {"setup": lambda b: {"argv": a0}, "recover": rec}


def fi_sc_renew(release: bool, fan: bool = False) -> dict[str, Any]:
    tos = ("bob", "carol", "dave") if fan else ("bob",)
    to = "crew" if fan else "bob"

    def setup(box: Path) -> dict[str, Any]:
        r = fi_cli(box, *fi_claim("alice", to))
        assert r.returncode == 0, r.stderr
        first = sorted(m["id"] for m in fi_msgs(box, type="CLAIM"))[0]
        return {"argv": fi_claim("alice", to, "" if release else FI_SCOPE, 60 if release else 7200,
                                 "rn", first)}

    def rec(box: Path, ctx: dict[str, Any]) -> list[str]:
        f, held = [], fi_held(box)
        full = len([m for m in fi_msgs(box, type="CLAIM") if m.get("supersedes")]) == len(tos)
        freed = release and full
        if (not freed) != (held == {"alice"}) or (freed and held):
            f.append(f"after the kill the scope holders are {sorted(held)} (release complete: {freed})")
        r = fi_cli(box, *fi_claim("carol2", "bob", subj="rival"))
        if "being claimed right now" in r.stderr:
            f.append("a dead holder's lock blocked the rival")
        if r.returncode != (0 if freed else 3):
            f.append(f"rival before the retry: exit {r.returncode}, want {0 if freed else 3}")
        r2 = fi_cli(box, *ctx["argv"], "--key-wait", "5")
        if r2.returncode not in (0, 3):
            f.append(f"retry: exit {r2.returncode} {r2.stderr[-120:]}")
        per: dict[str, int] = {}
        for m in fi_msgs(box, type="CLAIM", **{"from": "alice"}):
            if m.get("supersedes"):
                per[m["to"]] = per.get(m["to"], 0) + 1
        for w in tos:
            if r2.returncode == 0 and not freed and per.get(w, 0) != 1:
                f.append(f"{per.get(w, 0)} renewal/release messages for {w}, want exactly 1")
        return f
    return {"setup": setup, "recover": rec}


def fi_sc_takeover(keyed: bool) -> dict[str, Any]:
    def setup(box: Path) -> dict[str, Any]:
        mid = "01HZZZZZZZZZZZZZZZZZZZZZZZ"   # a lease that lapsed years ago, planted
        (box / f"{mid}-lapsed.md").write_text(
            f"---\nid: {mid}\ntype: CLAIM\nfrom: alice\nto: bob\ndate: 2020-01-01T00:00:00Z\n"
            f"subject: lapsed\nexpires: 2020-01-01T01:00:00Z\nscope: {FI_SCOPE}\n---\n\nBODYMARK old\n")
        return {"argv": fi_claim("bob", "bob", key="tk" if keyed else "")}

    def rec(box: Path, ctx: dict[str, Any]) -> list[str]:
        f, mine = [], fi_msgs(box, type="CLAIM", **{"from": "bob"})
        if len(mine) > 1:
            f.append(f"{len(mine)} claims by the taker after one send")
        if keyed:
            r = fi_cli(box, *ctx["argv"], "--key-wait", "5")
            if r.returncode != 0:
                f.append(f"taker's retry: exit {r.returncode} {r.stderr[-120:]}")
        r = fi_cli(box, *fi_claim("carol", "bob", subj="rival"))
        if "being claimed right now" in r.stderr:
            f.append("a dead holder's lock blocked the rival")
        if (keyed or bool(mine)) != (r.returncode == 3):
            f.append(f"rival exit {r.returncode}")
        if len(fi_held(box)) != 1:
            f.append(f"holders {sorted(fi_held(box))}")
        fi_no_lock(box, f)
        return f
    return {"setup": setup, "recover": rec}


def fi_sc_orphan(kind: str) -> dict[str, Any]:
    """The retry that cleans up after a dead sender is itself killed at every step."""
    a_key = fi_notice("alice", "bob", key="ok")
    a_claim = fi_claim("alice", "bob")
    a_both = fi_claim("alice", "bob", key="ob")

    def setup(box: Path) -> dict[str, Any]:
        fi_orphan(box, {"key": a_key, "scope": a_claim, "both": a_both}[kind])
        return {"argv": {"key": a_key, "scope": fi_claim("carol", "bob", subj="takeover"),
                         "both": a_both}[kind]}

    def rec(box: Path, ctx: dict[str, Any]) -> list[str]:
        f = []
        if kind != "scope":
            for _ in range(2):
                r = fi_cli(box, *ctx["argv"], "--key-wait", "5")
                if r.returncode != 0:
                    f.append(f"retry: exit {r.returncode} {r.stderr[-120:]}")
            want = len(fi_msgs(box, type="NOTICE" if kind == "key" else "CLAIM"))
            if want != 1:
                f.append(f"{want} messages for one key after a takeover killed here")
            f += [f"problem after recovery: {c}" for c in fi_problems(box)]
        else:
            r = fi_cli(box, *fi_claim("dave", "bob", subj="rival"))
            if "being claimed right now" in r.stderr:
                f.append("a dead holder's lock blocked a later claim")
            if r.returncode != (3 if fi_msgs(box, type="CLAIM", **{"from": "carol"}) else 0):
                f.append(f"rival exit {r.returncode}")
        if kind != "key":
            if len(fi_held(box)) != 1:
                f.append(f"holders {sorted(fi_held(box))}")
            fi_no_lock(box, f)
        return f
    return {"setup": setup, "recover": rec}


FI_SCENARIOS: dict[str, Any] = {
    "plain": fi_sc_plain,
    "keyed": lambda: fi_sc_keyed(),
    "claim": lambda: fi_sc_claim(False),
    "claim-keyed": lambda: fi_sc_claim(True),
    "fanout-keyed": lambda: fi_sc_keyed(True),
    "fanout-claim-keyed": lambda: fi_sc_claim(True, True),
    "renew-keyed": lambda: fi_sc_renew(False),
    "release-keyed": lambda: fi_sc_renew(True),
    "fanout-renew-keyed": lambda: fi_sc_renew(False, True),
    "fanout-release-keyed": lambda: fi_sc_renew(True, True),
    "takeover-keyed": lambda: fi_sc_takeover(True),
    "takeover-unkeyed": lambda: fi_sc_takeover(False),
    "orphan-key-takeover": lambda: fi_sc_orphan("key"),
    "orphan-scope-takeover": lambda: fi_sc_orphan("scope"),
    "orphan-both-takeover": lambda: fi_sc_orphan("both"),
}
FI_CI = ("plain", "keyed", "claim-keyed", "release-keyed", "takeover-keyed", "orphan-both-takeover")
FI_RESIDUE = ("scope-lock-file", "key-marker", "key-marker-tmp", "msg-tmp")


def fi_point(name: str, tmp: Path, t: int, labels: list[str], full: bool, mode: str) -> dict[str, Any]:
    spec = FI_SCENARIOS[name]()
    box = fi_new_box(tmp, f"p{t}")
    ctx = spec["setup"](box)
    parked, rc, err = fi_kill_at(box, ctx["argv"], t, tmp / f"w{t}",
                                 SIGKILL if mode == "kill" else signal.SIGTERM)
    out: dict[str, Any] = {"t": t, "parked": parked, "fail": [], "residue": set(), "absorbed": rc == "absorbed"}
    if not parked:
        return out
    f: list[str] = out["fail"]
    if mode == "term" and rc != "absorbed":
        if rc not in (143, -15, 0) or "Traceback" in err:
            f.append(f"SIGTERM: exit {rc} {err[-120:]}")
        landed = {m["id"] for m in fi_msgs(box)}
        for h in fi_hidden(box, "."):
            if h.startswith(".idem.") and (box / h).read_text().split("\n")[0] not in landed:
                out["residue"].add("key-marker-tmp" if h.endswith(".tmp") else "key-marker")
            elif h.startswith(".scope") and rc == 143:
                out["residue"].add("scope-lock-file")
            elif h.endswith(".tmp") and rc == 143:
                out["residue"].add("msg-tmp")
    f += fi_torn(box) + fi_readers(box, full)
    f += spec["recover"](box, ctx)
    f += fi_torn(box) + fi_readers(box, False)
    shutil.rmtree(box, ignore_errors=True)
    return out


def fi_scenario(name: str, root: Path, full: bool, mode: str = "kill") -> dict[str, Any]:
    tmp = root / f"{name}-{mode}"
    tmp.mkdir()
    spec = FI_SCENARIOS[name]()
    dry = fi_new_box(tmp, "dry")
    ctx = spec["setup"](dry)
    rc, labels = fi_trace(dry, ctx["argv"], tmp)
    for t in range(1, len(labels) + 1):
        (tmp / f"w{t}").mkdir()
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(lambda t: fi_point(name, tmp, t, labels, full, mode),
                             range(1, len(labels) + 1)))
    res: dict[str, Any] = {"name": name, "mode": mode, "points": len(labels), "dry_rc": rc,
                           "parked": sum(1 for r in rows if r["parked"]),
                           "absorbed": sum(1 for r in rows if r["absorbed"]),
                           "residue_points": sum(1 for r in rows if r["residue"]),
                           "fail": [(r["t"], labels[r["t"] - 1], r["fail"][:3]) for r in rows if r["fail"]]}
    return res


def fi_interleave(variant: str, seed: int, root: Path) -> dict[str, Any]:
    """3 or 4 real senders at once with random stalls and random kill points (seeded)."""
    rnd = random.Random(f"{variant}:{seed}")
    tmp = root / f"i-{variant}-{seed}"
    tmp.mkdir()
    box = fi_new_box(tmp, "box")
    senders = ["alice", "bob", "carol", "dave"][:rnd.choice([3, 4])]
    procs: list[tuple[subprocess.Popen[str], Path]] = []
    for i, s in enumerate(senders):
        argv = {"scope": fi_claim(s, "erin", subj="c"),
                "scope-keyed": fi_claim(s, "erin", key="kk", subj="c"),
                "key-same": fi_notice("alice", "bob", key="kk") + ["--key-wait", "5"],
                "fanout-key": fi_notice("alice", "crew", key="kk") + ["--key-wait", "5"]}[variant]
        kill = rnd.randint(1, 40) if rnd.random() < 0.5 else -1
        stalls = ",".join(f"{rnd.randint(1, 40)}:{rnd.choice([5, 20, 60])}" for _ in range(rnd.randint(0, 3)))
        ready = tmp / f"ready{i}"
        procs.append((fi_spawn(box, argv, kill, "", str(ready), stalls=stalls), ready))
    killed: set[int] = set()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        alive = [(p, r) for p, r in procs if p.poll() is None]
        if not alive:
            break
        if all(r.exists() for _, r in alive):
            time.sleep(rnd.random() * 0.05)
            for p, _ in alive:
                p.kill()
                killed.add(p.pid)
            break
        time.sleep(0.01)
    rows = []
    for p, _ in procs:
        try:
            _, err = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            _, err = p.communicate()
        rows.append((p.pid, p.returncode, err))
    f = [f"survivor exit {rc}: {err[-120:]}" for pid, rc, err in rows
         if pid not in killed and (rc not in (0, 2, 3) or "Traceback" in err)]
    f += fi_torn(box) + fi_readers(box, False)
    if variant.startswith("scope"):
        won = sum(1 for pid, rc, _ in rows if rc == 0 and pid not in killed)
        before = fi_held(box)
        if len(before) > 1 or won > 1:
            f.append(f"two winners: held {sorted(before)}, {won} survivors exited 0")
        r = fi_cli(box, *fi_claim("erin", "erin", subj="rival"))
        if "being claimed right now" in r.stderr or r.returncode != (3 if before else 0):
            f.append(f"later claim: exit {r.returncode} {r.stderr[-100:]}")
        if len(fi_held(box)) != 1:
            f.append("not exactly one holder at the end")
        fi_no_lock(box, f)
    else:
        recips = ("bob",) if variant == "key-same" else ("bob", "carol", "dave")
        argv = fi_notice("alice", "crew" if variant == "fanout-key" else "bob", key="kk")
        if any(len(fi_msgs(box, type="NOTICE", to=w)) > 1 for w in recips):
            f.append("two messages for one key")
        r = fi_cli(box, *argv, "--key-wait", "5")
        if r.returncode != 0 or any(len(fi_msgs(box, type="NOTICE", to=w)) != 1 for w in recips):
            f.append(f"after the final keyed retry: exit {r.returncode}, not one message per recipient")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"variant": variant, "seed": seed, "killed": len(killed), "fail": f}


# ---- hostile files at every read, and participants edge cases ----
FI_GOOD = "01M5347PX0CAPJFEJ94SQHJ8CP"
FI_HDR = ("---\nid: {id}\ntype: NOTICE\nfrom: alice\nto: bob\ndate: 2026-10-16T10:00:00Z\n"
          "subject: {subj}\n---\n\nBODYMARK\n")


def fi_hid(n: int) -> str:
    return f"01M5347PX0CAPJFEJ94SQH{n:04d}"


def fi_w(path: Path, data: str | bytes) -> Path:
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)
    return path


def fi_sym(target: str | Path, link: Path) -> Path:
    os.symlink(target, link)
    return link


def fi_fifo(path: Path) -> Path:
    os.mkfifo(path)
    return path


def fi_mode(path: Path, mode: int) -> Path:
    os.chmod(path, mode)
    return path


def fi_trunc(path: Path, size: int) -> Path:
    os.truncate(path, size)
    return path


def fi_dir(path: Path) -> Path:
    path.mkdir()
    return path


def fi_hostile_cases() -> dict[str, Any]:
    h = FI_HDR
    cases: dict[str, Any] = {
        "no closing fence": lambda b: fi_w(b / f"{fi_hid(1)}-x.md", f"---\nid: {fi_hid(1)}\ntype: NOTICE\nfrom: alice\nto: bob\n\nbody\n"),
        "empty file": lambda b: fi_w(b / f"{fi_hid(2)}-x.md", ""),
        "header line without a colon": lambda b: fi_w(b / f"{fi_hid(4)}-x.md", f"---\nid: {fi_hid(4)}\nnot a field\n---\nb\n"),
        "duplicate header keys": lambda b: fi_w(b / f"{fi_hid(5)}-x.md", f"---\nid: {fi_hid(5)}\nid: {fi_hid(6)}\ntype: NOTICE\ntype: CLAIM\nfrom: alice\nfrom: eve\nto: bob\nsubject: a\nsubject: b\n---\n\nb\n"),
        "3 MB header line": lambda b: fi_w(b / f"{fi_hid(7)}-x.md", h.format(id=fi_hid(7), subj="A" * 3_000_000)),
        "20 MB body": lambda b: fi_w(b / f"{fi_hid(8)}-x.md", h.format(id=fi_hid(8), subj="big") + "X" * 20_000_000),
        "2 GB sparse file": lambda b: (fi_w(b / f"{fi_hid(9)}-x.md", h.format(id=fi_hid(9), subj="sparse")), fi_trunc(b / f"{fi_hid(9)}-x.md", 2 * 1024**3)),
        "non-UTF-8 body": lambda b: fi_w(b / f"{fi_hid(10)}-x.md", h.format(id=fi_hid(10), subj="bad").encode() + b"\xff\xfe\x80 tail"),
        "non-UTF-8 subject": lambda b: fi_w(b / f"{fi_hid(11)}-x.md", h.format(id=fi_hid(11), subj="XX").encode().replace(b"XX", b"\xc3\x28\xff")),
        "NUL bytes in the body": lambda b: fi_w(b / f"{fi_hid(12)}-x.md", h.format(id=fi_hid(12), subj="nul").encode().replace(b"BODYMARK", b"BODY\x00MARK")),
        "NUL in a header": lambda b: fi_w(b / f"{fi_hid(13)}-x.md", h.format(id=fi_hid(13), subj="a\x00b")),
        "UTF-8 BOM first": lambda b: fi_w(b / f"{fi_hid(14)}-x.md", b"\xef\xbb\xbf" + h.format(id=fi_hid(14), subj="bom").encode()),
        "CRLF line ends": lambda b: fi_w(b / f"{fi_hid(15)}-x.md", h.format(id=fi_hid(15), subj="crlf").replace("\n", "\r\n")),
        "terminal escapes in the subject": lambda b: fi_w(b / f"{fi_hid(16)}-x.md", h.format(id=fi_hid(16), subj="\x1b[2J\x1b]0;pwn\x07 hi")),
        "bidi and zero-width characters": lambda b: fi_w(b / f"{fi_hid(17)}-x.md", h.format(id=fi_hid(17), subj="‮evil⁦ ​\u0085")),
        "mode 000": lambda b: (fi_w(b / f"{fi_hid(18)}-x.md", h.format(id=fi_hid(18), subj="locked")), fi_mode(b / f"{fi_hid(18)}-x.md", 0)),
        "symlink to a file outside": lambda b: (fi_w(b.parent / "secret.txt", "TOPSECRET\n"), fi_sym(b.parent / "secret.txt", b / f"{fi_hid(19)}-x.md")),
        "symlink to /dev/zero": lambda b: fi_sym("/dev/zero", b / f"{fi_hid(20)}-x.md"),
        "symlink to /dev/random": lambda b: fi_sym("/dev/random", b / f"{fi_hid(21)}-x.md"),
        "dangling symlink": lambda b: fi_sym("/nonexistent/zzz", b / f"{fi_hid(22)}-x.md"),
        "symlink loop": lambda b: fi_sym(f"{fi_hid(23)}-x.md", b / f"{fi_hid(23)}-x.md"),
        "symlink to a directory": lambda b: fi_sym("/tmp", b / f"{fi_hid(24)}-x.md"),
        "FIFO": lambda b: fi_fifo(b / f"{fi_hid(26)}-x.md"),
        "directory named *.md": lambda b: fi_dir(b / f"{fi_hid(27)}-x.md"),
        "two files with one id": lambda b: (fi_w(b / f"{fi_hid(29)}-a.md", h.format(id=fi_hid(29), subj="a")), fi_w(b / f"{fi_hid(29)}-b.md", h.format(id=fi_hid(29), subj="b"))),
        "id differs from the file name": lambda b: fi_w(b / f"{fi_hid(30)}-x.md", h.format(id=fi_hid(31), subj="mismatch")),
        "date in year 9999": lambda b: fi_w(b / f"{fi_hid(33)}-x.md", h.format(id=fi_hid(33), subj="future").replace("2026-10-16T10:00:00Z", "9999-12-31T23:59:59Z")),
        "date in year 1": lambda b: fi_w(b / f"{fi_hid(34)}-x.md", h.format(id=fi_hid(34), subj="ancient").replace("2026-10-16T10:00:00Z", "0001-01-01T00:00:00Z")),
        "unparseable date": lambda b: fi_w(b / f"{fi_hid(35)}-x.md", h.format(id=fi_hid(35), subj="baddate").replace("2026-10-16T10:00:00Z", "yesterday-ish")),
        "zone offset +99:99": lambda b: fi_w(b / f"{fi_hid(36)}-x.md", h.format(id=fi_hid(36), subj="off").replace("2026-10-16T10:00:00Z", "2026-10-16T10:00:00+99:99")),
        "no date": lambda b: fi_w(b / f"{fi_hid(37)}-x.md", h.format(id=fi_hid(37), subj="nodate").replace("date: 2026-10-16T10:00:00Z\n", "")),
        "CLAIM with garbage expires": lambda b: fi_w(b / f"{fi_hid(38)}-x.md", h.format(id=fi_hid(38), subj="cl").replace("type: NOTICE", "type: CLAIM").replace("---\n\nBODY", "scope: s1\nexpires: nonsense\n---\n\nBODY")),
        "CLAIM expiring in year 9999": lambda b: fi_w(b / f"{fi_hid(39)}-x.md", h.format(id=fi_hid(39), subj="cl9").replace("type: NOTICE", "type: CLAIM").replace("---\n\nBODY", "scope: s2\nexpires: 9999-12-31T23:59:59Z\n---\n\nBODY")),
        "supersedes cycle and self": lambda b: [fi_w(b / f"{fi_hid(n)}-x.md", h.format(id=fi_hid(n), subj=f"s{n}").replace(f"subject: s{n}", f"subject: s{n}\nsupersedes: {fi_hid(m)}")) for n, m in ((41, 42), (42, 41), (43, 43))],
        "ASK that replies to itself": lambda b: fi_w(b / f"{fi_hid(44)}-x.md", h.format(id=fi_hid(44), subj="r").replace("type: NOTICE", "type: ASK").replace("subject: r", f"subject: r\nreply_to: {fi_hid(44)}")),
        "junk dotfiles at lock, marker and temp names": lambda b: [fi_w(b / ".idem.zzzz", "garbage"), fi_w(b / ".scope.x.lock", ""), fi_w(b / f".{fi_hid(45)}-x.md.1.tmp", "partial"), fi_w(b / ".idem.aaaa.takeover", "")],
        "hostile key markers": lambda b: [fi_w(b / ".idem.0123456789abcdef01234567", "*\n" + "0" * 64 + "\nflock\n"), fi_w(b / ".idem.0123456789abcdef01234568", b"\xff\xfe" * 3000), fi_sym("/etc/passwd", b / ".idem.0123456789abcdef01234569")],
        "scope lock names as symlink and FIFO": lambda b: (fi_sym("/etc/hosts", b / ".scope.0123456789abcdef01234567.lock"), fi_fifo(b / ".scope.0123456789abcdef01234568.lock")),
        "1000 garbage files": lambda b: [fi_w(b / f"{fi_hid(1000 + i)[:22]}{i:04d}-x.md", "garbage") for i in range(1000)],
    }
    return cases


def fi_hostile(root: Path, only: tuple[str, ...] = ()) -> tuple[int, list[str]]:
    """One good message plus one hostile entry: every reader and the hook must finish, never
    traceback or print a raw escape, leave the mailbox byte-identical and still show the good one."""
    bad: list[str] = []
    cases = {k: v for k, v in fi_hostile_cases().items() if not only or k in only}
    if os.name == "nt":   # no FIFOs, device files or permission bits to plant there
        cases = {k: v for k, v in cases.items() if k not in (
            "FIFO", "mode 000", "symlink to /dev/zero", "symlink to /dev/random",
            "scope lock names as symlink and FIFO")}
    for i, (name, make) in enumerate(cases.items()):
        box = fi_new_box(root, f"h{i}")
        (box / f"{FI_GOOD}-good.md").write_text(FI_HDR.format(id=FI_GOOD, subj="good"))
        make(box)
        before = fi_snapshot(box)
        outs: dict[str, subprocess.CompletedProcess[str]] = {}
        for argv in (["list"], ["list", "--json"], ["list", "--live", "--to", "bob"], ["inbox", "--to", "bob"],
                     ["inbox", "--to", "bob", "--json"], ["status"], ["status", "--json"], ["doctor"],
                     ["show", FI_GOOD], ["show", FI_GOOD, "--json"]):
            try:
                p = subprocess.run([sys.executable, "-I", str(HERE / "agent_mail.py"), *argv], env=fi_env(box),
                                   capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                bad.append(f"{name}: {' '.join(argv)} did not finish")
                continue
            outs[" ".join(argv)] = p
            if "Traceback" in p.stderr or p.returncode not in (0, 1, 2) or "\x1b" in p.stdout + p.stderr:
                bad.append(f"{name}: {' '.join(argv)}: exit {p.returncode} {p.stderr[-100:]!r}")
            if "--json" in argv and argv[0] != "show":
                try:
                    json.loads(p.stdout)
                except ValueError:
                    bad.append(f"{name}: {' '.join(argv)} is not JSON")
        try:
            hk = subprocess.run([sys.executable, "-I", str(HERE / "hooks" / "agent_mail_check.py")],
                                env=fi_env(box, AGENT_MAIL_IDENTITY="bob"), capture_output=True, text=True,
                                timeout=60, stdin=subprocess.DEVNULL)
            if hk.returncode != 0 or "Traceback" in hk.stderr + hk.stdout or "\x1b" in hk.stdout:
                bad.append(f"{name}: hook exit {hk.returncode}")
            if "good" not in hk.stdout and name != "two files with one id":
                bad.append(f"{name}: the hook did not deliver the good message")
        except subprocess.TimeoutExpired:
            bad.append(f"{name}: hook did not finish")
        if fi_snapshot(box) != before:
            bad.append(f"{name}: a reader changed the mailbox")
        for pth in box.iterdir():
            if pth.is_file() and not pth.is_symlink():
                os.chmod(pth, 0o600)
        shutil.rmtree(box, ignore_errors=True)
    return len(cases), bad


FI_PHEAD = ("# Participants\n\n| Identity | Reachable | Mechanism | Verified | can | cannot | uid |\n"
            "|---|---|---|---|---|---|---|\n")
FI_AHEAD = "\n## Aliases\n\n| Alias | Writers |\n|---|---|\n"


def fi_prow(i: str, caps: str = "", cannot: str = "", uid: str = "") -> str:
    return f"| `{i}` | no | poll | no | {caps} | {cannot} | {uid} |\n"


def fi_participants_cases() -> dict[str, Any]:
    r, a = fi_prow, (lambda n, w: f"| `{n}` | {w} |\n")
    two = FI_PHEAD + r("alice") + r("bob")
    return {
        "duplicate identity rows": two + r("bob", "push") + r("bob", "", "push") + FI_AHEAD + a("crew", "`bob`"),
        "re-registration with conflicting capabilities": two + r("bob", "push") + r("bob", "", "push") + FI_AHEAD + a("crew", "`bob`"),
        "re-registration with two uids": two + r("bob", uid="501") + r("bob", uid="0"),
        "alias naming an unknown writer": two + FI_AHEAD + a("crew", "`bob`, `ghost`"),
        "alias with only unknown writers": FI_PHEAD + r("alice") + FI_AHEAD + a("crew", "`ghost`, `phantom`"),
        "alias naming itself": two + FI_AHEAD + a("crew", "`crew`, `bob`"),
        "alias naming another alias": two + FI_AHEAD + a("crew", "`team2`") + a("team2", "`bob`"),
        "alias cycle": FI_PHEAD + r("alice") + FI_AHEAD + a("crew", "`a2`") + a("a2", "`crew`"),
        "alias with an empty writers cell": two + FI_AHEAD + a("crew", ""),
        "alias rows repeated, writers repeated": two + r("carol") + FI_AHEAD + a("crew", "`bob`, `bob`") + a("crew", "`carol`, `bob`"),
        "alias and writers in capitals": two + FI_AHEAD + a("CREW", "`BOB`") + a("x" * 200, "`bob`"),
        "writers with spaces, zero-width characters, ../": two + FI_AHEAD + a("crew", "`bo b`, `b​ob`, `../etc`"),
        "alias that names the sender": two + FI_AHEAD + a("crew", "`alice`, `bob`"),
        "alias that is also an identity row": two + r("crew") + FI_AHEAD + a("crew", "`bob`"),
        "table without a header": "| `alice` | no |\n| `bob` | no |\n",
        "separator rows only": "|---|---|\n|---|---|\n",
        "30000 identity rows": FI_PHEAD + "".join(r(f"id{i}") for i in range(30000)) + FI_AHEAD + a("crew", "`id1`"),
        "NUL byte in a row": (two + FI_AHEAD + a("crew", "`bob`")).replace("alice", "al\x00ice"),
        "not UTF-8": FI_PHEAD.encode() + b"| `alice` | \xff\xfe |\n" + FI_AHEAD.encode() + b"| `crew` | `bob` |\n",
        "unreadable (mode 000)": "chmod",
        "a directory named PARTICIPANTS.md": "dir",
        "a symlink to a file outside": "symlink",
        "a FIFO named PARTICIPANTS.md": "fifo",
        "empty file": "",
        "only the Aliases heading": "## Aliases\n",
    }


def fi_participants(root: Path, only: tuple[str, ...] = ()) -> tuple[int, list[str]]:
    """Every command that reads PARTICIPANTS.md, against each edge case: no traceback, no hang, no
    mailbox change by a reader, no message to a malformed recipient, one copy per writer."""
    bad: list[str] = []
    cases = {k: v for k, v in fi_participants_cases().items() if not only or k in only}
    if os.name == "nt":   # no FIFOs or permission bits to plant there
        cases = {k: v for k, v in cases.items() if v not in ("chmod", "fifo")}
    for i, (name, spec) in enumerate(cases.items()):
        box = root / f"pp{i}"
        box.mkdir()
        pp = box / "PARTICIPANTS.md"
        if spec == "chmod":
            pp.write_text(FI_PHEAD + fi_prow("alice") + fi_prow("bob") + FI_AHEAD + "| `crew` | `bob` |\n")
            os.chmod(pp, 0)
        elif spec == "dir":
            pp.mkdir()
        elif spec == "symlink":
            (root / f"sec{i}").write_text("secret")
            fi_sym(root / f"sec{i}", pp)
        elif spec == "fifo":
            os.mkfifo(pp)
        else:
            fi_w(pp, spec)
        cmds = {"send alias": fi_notice("alice", "crew"), "send bob": fi_notice("alice", "bob", subj="direct"),
                "ask alias": ["ask", "--from", "alice", "--to", "crew", "--subject", "q", "--body", "BODYMARK q"],
                "claim alias": fi_claim("alice", "crew"), "list": ["list"], "inbox": ["inbox", "--to", "crew"],
                "status": ["status"], "status --json": ["status", "--json"], "doctor": ["doctor"],
                "capability": ["capability", "--identity", "bob", "--capability", "push"],
                "list --to crew": ["list", "--to", "crew", "--live"]}
        readers = {"list", "inbox", "status", "status --json", "doctor", "capability", "list --to crew"}
        for cn, argv in cmds.items():
            snap = fi_snapshot(box)
            try:
                p = fi_cli(box, *argv, ident="bob")
            except subprocess.TimeoutExpired:
                bad.append(f"{name}: {cn} did not finish")
                continue
            if "Traceback" in p.stderr or p.returncode not in (0, 1, 2, 3):
                bad.append(f"{name}: {cn}: exit {p.returncode} {p.stderr[-100:]!r}")
            if cn in readers and fi_snapshot(box) != snap:
                bad.append(f"{name}: {cn} changed the mailbox")
            if cn == "status --json":
                try:
                    json.loads(p.stdout)
                except ValueError:
                    bad.append(f"{name}: status --json is not JSON")
        try:
            hk = subprocess.run([sys.executable, "-I", str(HERE / "hooks" / "agent_mail_check.py")],
                                env=fi_env(box, AGENT_MAIL_IDENTITY="bob"), capture_output=True, text=True,
                                timeout=60, stdin=subprocess.DEVNULL)
            if hk.returncode != 0 or "Traceback" in hk.stderr + hk.stdout:
                bad.append(f"{name}: hook exit {hk.returncode} {hk.stderr[-100:]!r}")
        except subprocess.TimeoutExpired:
            bad.append(f"{name}: hook did not finish")
        per: dict[str, int] = {}
        for m in fi_msgs(box, subject="hello"):
            per[m["to"]] = per.get(m["to"], 0) + 1
        if any(c > 1 for c in per.values()):
            bad.append(f"{name}: two copies for one writer {per}")
        for m in fi_msgs(box):
            if not re.fullmatch(r"[A-Za-z0-9._@+-]+", m["to"]):
                bad.append(f"{name}: a message was filed to {m['to']!r}")
        try:
            os.chmod(pp, 0o600)
        except OSError:
            pass
        shutil.rmtree(box, ignore_errors=True)
    return len(cases), bad



def faults_main(mode: str) -> int:
    """mode 'ci': a fixed subset; 'full': every scenario, SIGTERM too, 150 seeds per variant."""
    full = mode == "full"
    bad, t0 = 0, time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        runs = [(n, "kill") for n in (FI_SCENARIOS if full else FI_CI)]
        if full:
            if os.name != "nt":   # Windows cannot deliver SIGTERM: send_signal kills
                runs += [(n, "term") for n in FI_SCENARIOS if n != "orphan-scope-takeover"]
        print("fault injection: a real send killed at every write step")
        for name, how in runs:
            r = fi_scenario(name, root, full, how)
            ok = not r["fail"] and r["parked"] == r["points"] and r["points"] > 0 and r["dry_rc"] == 0
            extra = (f", {r['absorbed']} absorbed by a release in progress, {r['residue_points']} left "
                     "inert residue" if how == "term" else "")
            print(f"  {'PASS' if ok else 'FAIL'}  {name} {how}: {r['parked']}/{r['points']} points reached"
                  f"{extra}" + ("" if ok else f"  -- {r['fail'][:2]}"))
            bad += 0 if ok else 1
        variants = ("scope", "scope-keyed", "key-same", "fanout-key")
        seeds = range(1, 151 if full else 13)
        with ThreadPoolExecutor(6) as pool:
            rows = list(pool.map(lambda a: fi_interleave(a[0], a[1], root),
                                 [(v, s) for v in variants for s in seeds]))
        for v in variants:
            mine = [r for r in rows if r["variant"] == v]
            fails = [r for r in mine if r["fail"]]
            print(f"  {'PASS' if not fails else 'FAIL'}  interleavings {v}: {len(mine)} seeded runs "
                  f"({seeds.start}..{seeds.stop - 1}), {sum(r['killed'] for r in mine)} processes killed"
                  + ("" if not fails else f"  -- seeds {[r['seed'] for r in fails][:5]} {fails[0]['fail'][:2]}"))
            bad += 1 if fails else 0
        with tempfile.TemporaryDirectory() as td2:
            n, hb = fi_hostile(Path(td2), () if full else ("no closing fence", "FIFO", "mode 000",
                               "symlink to /dev/zero", "two files with one id", "date in year 9999"))
            print(f"  {'PASS' if not hb else 'FAIL'}  hostile entries next to a good message: {n} cases, "
                  f"10 readers and the hook each" + ("" if not hb else f"  -- {hb[:3]}"))
            bad += 1 if hb else 0
            n, pb = fi_participants(Path(td2), () if full else (
                "duplicate identity rows", "alias naming another alias", "alias naming an unknown writer",
                "not UTF-8", "unreadable (mode 000)", "a FIFO named PARTICIPANTS.md"))
            print(f"  {'PASS' if not pb else 'FAIL'}  PARTICIPANTS.md edge cases: {n} cases, 11 commands and the "
                  f"hook each" + ("" if not pb else f"  -- {pb[:3]}"))
            bad += 1 if pb else 0
    print(f"fault injection: {'all checks passed' if not bad else f'{bad} check(s) FAILED'} "
          f"in {time.monotonic() - t0:.0f}s")
    return 1 if bad else 0



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--senders", type=int, default=8)
    ap.add_argument("--per-sender", type=int, default=4)
    ap.add_argument("--kb", type=int, default=2048)
    ap.add_argument("--faults", choices=("off", "ci", "full"), default="ci",
                    help="the fault-injection matrix after the stress run: a fixed subset (ci), every "
                         "scenario and 150 seeds per interleaving (full), or none")
    a = ap.parse_args()
    faulthandler.dump_traceback_later(WATCHDOG * (4 if a.faults == "full" else 1), exit=True)
    expect = a.senders * a.per_sender + 2  # + one message each from the keyed and the scoped burst

    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp) / "mailbox"
        box.mkdir()
        body = Path(tmp) / "body.txt"
        body.write_text(("filler line to make the body large\n" * (a.kb * 1024 // 36))
                        + SENTINEL + "\n", encoding="utf-8")
        errors: list = []
        torn: list = []
        seen: set = set()
        stop = threading.Event()
        tool_bad: list = []
        tool_runs: list = []
        keyed_res: list = []
        claim_res: list = []
        took: list = []
        killed: dict = {}
        rd = threading.Thread(target=reader, args=(box, stop, torn, seen))
        rd.start()
        trd = threading.Thread(target=tool_reader, args=(box, stop, tool_bad, tool_runs))
        trd.start()
        t0 = time.time()
        ts = [threading.Thread(target=sender, args=(box, body, a.per_sender, str(i), errors, took))
              for i in range(a.senders)]
        ts.append(threading.Thread(target=killed_sender, args=(box, body, killed)))
        ts += [threading.Thread(target=keyed, args=(box, keyed_res)) for _ in range(a.senders)]
        ts += [threading.Thread(target=claimer, args=(box, n, claim_res)) for n in range(a.senders)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        stop.set()
        rd.join()
        trd.join()

        elapsed = time.time() - t0
        files = sorted(box.glob("*.md"))
        own_tmp = f".md.{killed.get('pid')}.tmp"
        ids = {f.name.split("-", 1)[0] for f in files}
        leftovers = [p.name for p in box.iterdir()
                     if p.suffix != ".md" and not (p.name.startswith(".idem.") and "." not in p.name[6:])
                     and not p.name.endswith(own_tmp)]
        killed_tmp = [p.name for p in box.iterdir() if p.name.endswith(own_tmp)]
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        listing = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "list"],
                                 capture_output=True, text=True, env=env, timeout=STEP_TIMEOUT)
        slowest, median = max(took, default=0.0), statistics.median(took or [0.0])
        markers = [p.name for p in box.iterdir() if p.name.startswith(".idem.")]
        k_wrote = sum(1 for rc, out, _ in keyed_res if rc == 0 and "wrote " in out)
        k_dup = sum(1 for rc, out, _ in keyed_res if rc == 0 and "duplicate of" in out)
        k_ids = {ln.split(": ", 1)[1] for _, out, _ in keyed_res for ln in out.splitlines()
                 if ln.startswith("id: ")}
        c_won = [out for rc, out, _ in claim_res if rc == 0 and "wrote " in out]
        c_lost = [err for rc, _, err in claim_res if rc == 3
                  and ("being claimed right now" in err or "already held by" in err)]
        c_files = [f.name for f in files if "\nscope: stress:one-scope\n" in f.read_text(encoding="utf-8")]
        results = [
            ("every send exited 0", not errors, errors[:3]),
            (f"{a.senders} simultaneous claims on one scope among the other writers: one written, "
             "the rest refused with exit 3, one claim file, no lock file left",
             len(c_won) == 1 and len(c_lost) == a.senders - 1 and len(c_files) == 1
             and not [p.name for p in box.iterdir() if p.name.startswith(".scope.")],
             f"won={len(c_won)} lost={len(c_lost)} files={c_files} "
             f"{[(rc, e[-100:]) for rc, _, e in claim_res if rc not in (0, 3)][:2]}"),
            (f"{expect} sends -> {expect} files", len(files) == expect, f"got {len(files)}"),
            (f"{expect} distinct ids", len(ids) == expect, f"got {len(ids)}"),
            ("reader observed messages (positive control)", len(seen) > 0, f"saw {len(seen)}"),
            ("reader never saw a torn message", not torn, torn[:3]),
            ("no temp files left behind by any sender that was not killed", not leftovers, leftovers[:3]),
            ("one more sender was SIGKILLed mid-publish: no message from it, one temp file of its own",
             killed.get("reached") is True and killed.get("rc") == KILLED_RC and len(killed_tmp) == 1
             and not [f.name for f in files if f.name.endswith("-stress-killed.md")],
             f"{killed} tmp={killed_tmp}"),
            ("after the kill `list` exits 0, rejects nothing and lists every message once",
             listing.returncode == 0 and "REJECT" not in listing.stdout + listing.stderr
             and len([ln for ln in listing.stdout.splitlines() if ln.startswith("    0")]) == expect,
             listing.stderr[-200:]),
            (f"no writer starved: slowest send {slowest:.2f}s <= 20 x median {median:.2f}s + 5 s",
             len(took) == a.senders * a.per_sender and slowest <= 20 * median + 5, f"{len(took)} sends"),
            ("the tool's own readers ran while senders wrote (positive control)",
             {"list", "status", "doctor"} <= set(tool_runs), f"ran {sorted(set(tool_runs))}"),
            ("list/status/doctor never saw a REJECT, a traceback or a failure", not tool_bad, tool_bad[:3]),
            (f"{a.senders} simultaneous keyed sends: one wrote, the rest 'duplicate of' one id",
             k_wrote == 1 and k_dup == a.senders - 1 and len(k_ids) == 1,
             f"wrote={k_wrote} dup={k_dup} ids={len(k_ids)} {[e[-120:] for _, _, e in keyed_res if e][:2]}"),
            ("exactly one key marker remains, and it names the published message",
             len(markers) == 1 and len(k_ids) == 1
             and (box / markers[0]).read_text(encoding="utf-8").split("\n")[0] in k_ids, markers[:3]),
        ]
        bad = 0
        for name, ok, detail in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  -- {detail}"))
            bad += 0 if ok else 1
        print(f"\n{expect} messages in {elapsed:.1f}s; reader saw {len(seen)} files; "
              f"slowest send {slowest:.2f}s, median {median:.2f}s")
        print("all checks passed" if not bad else f"{bad} check(s) FAILED")
    bad += faults_main(a.faults) if a.faults != "off" else 0
    faulthandler.cancel_dump_traceback_later()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
