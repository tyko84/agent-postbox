#!/usr/bin/env python3
"""Adversarial-input tests: hostile text, hostile files, hostile concurrency.

Policy under test (the choice is recorded in agent_mail.py next to the code):

* SEND is strict. Anything that could forge or smear the envelope is refused
  (exit 2, nothing written): control and format characters in any header
  field, identities outside a small ASCII alphabet, id references that are not
  plain tokens, NUL/ESC/other control bytes in the body, oversized fields.
* READ is quarantining. Files that did not come from `send` (hand edits, a
  hostile writer with filesystem access) are never trusted: symlinks, FIFOs,
  non-UTF-8, control characters, duplicate frontmatter keys and oversized files
  become a loud REJECT line, are never admitted as mail, and are never read
  through a link.

Every negative check carries a positive control in the SAME mailbox, so
"nothing happened" cannot be the symptom of a broken harness.

Bounded: every child is waited for with a timeout, and a watchdog dumps every
thread's stack and exits non-zero if the run has not finished after WATCHDOG
seconds.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import faulthandler
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = str(HERE / "agent_mail.py")
WATCHDOG = 900   # seconds; the whole run
FAILS: list[str] = []
ULID_A = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
ULID_B = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(f"{name}: {detail[:300]}")


def run(box: Path, *argv: str, stdin: str | None = None,
        env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, AGENT_MAIL_DIR=str(box), **(env_extra or {}))
    return subprocess.run([sys.executable, "-I", TOOL, *argv], capture_output=True,
                          text=True, env=env, input=stdin, timeout=120)


def send(box: Path, *, frm: str = "alice", to: str = "bob", subj: str = "s",
         body: str = "b", kind: str = "NOTICE", extra: tuple[str, ...] = (),
         stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    if stdin is not None:
        return run(box, "send", "--type", kind, "--from", frm, "--to", to,
                   "--subject", subj, "--body-file", "-", *extra, stdin=stdin)
    return run(box, "send", "--type", kind, "--from", frm, "--to", to,
               "--subject", subj, "--body", body, *extra)


def mid(cp: subprocess.CompletedProcess[str]) -> str:
    return next(x.split(": ", 1)[1] for x in cp.stdout.splitlines() if x.startswith("id: "))


def soon(hours: float) -> str:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def md_files(box: Path) -> list[Path]:
    return sorted(box.glob("*.md"))


def hand(box: Path, mid_: str, extra: str = "", body: str = "hand body\n",
         head: str | None = None, name: str | None = None) -> Path:
    p = box / (name or f"{mid_}-hand.md")
    head = head if head is not None else (
        f"id: {mid_}\ntype: NOTICE\nfrom: eve\nto: bob\n"
        f"date: 2099-01-01T00:00:00Z\nsubject: hand\n")
    p.write_text(f"---\n{head}{extra}---\n\n{body}", encoding="utf-8")
    return p


# ---- a reader that goes away (PROTOCOL.md section 35) -----------------------
# The console script that `pip install` generates is exactly
#     from agent_mail import main; sys.exit(main())
# (test_packaging.py pins that text against a real installed wrapper), so the
# "-c" entry below is that path, available in every CI cell without a build.
GONE = 128 + signal.SIGPIPE
PIPE_NOW = "2099-01-01T12:00:00Z"      # hand() dates its messages 2099-01-01T00:00:00Z
BIG = 150_000                          # bytes; more than any pipe buffer holds
ENTRIES: dict[str, list[str]] = {
    "python agent_mail.py": [sys.executable, "-I", TOOL],
    "python -m agent_mail": [sys.executable, "-m", "agent_mail"],
    "console script": [sys.executable, "-c",
                       f"import sys; sys.path.insert(0, {str(HERE)!r}); "
                       "from agent_mail import main; sys.exit(main())"],
    "python -u agent_mail.py": [sys.executable, "-I", "-u", TOOL],
}
ONE_BYTE_THEN_SIGKILL = ("import os, signal; d = os.read(0, 1); os.write(1, d); "
                         "os.kill(os.getpid(), signal.SIGKILL)")


def piped(argv: list[str], box: Path, how: str, *, stderr_gone: bool = False,
          env_extra: dict[str, str] | None = None) -> tuple[int, bytes, bytes]:
    """Run `argv` with stdout on a pipe and return (exit status, stderr, the
    bytes the reader got). `how` is the reader: "full" reads to the end;
    "line" and "byte" read that much and close; "closed" is gone before the
    command starts (the only one that is not a race for a small output);
    "kill" is a separate process that takes one byte and is then SIGKILLed."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    env.update(AGENT_MAIL_DIR=str(box), **(env_extra or {}))
    err_r, err_w = os.pipe()
    out_r, out_w = os.pipe()
    try:
        if how == "closed":
            os.close(out_r)
            out_r = -1
        if stderr_gone:
            os.close(err_r)
            err_r = -1
        child = subprocess.Popen(argv, stdout=out_w, stderr=err_w, env=env, cwd=HERE)
    finally:
        os.close(out_w)
        os.close(err_w)
    def drain(fd: int) -> bytes:
        with os.fdopen(fd, "rb") as fh:
            return fh.read()

    got = b""
    with ThreadPoolExecutor(1) as pool:      # stderr is drained alongside: neither pipe may fill
        err_f = pool.submit(drain, err_r) if err_r != -1 else None
        if how == "kill":
            killer = subprocess.Popen([sys.executable, "-I", "-c", ONE_BYTE_THEN_SIGKILL],
                                      stdin=out_r, stdout=subprocess.PIPE)
            os.close(out_r)
            got = killer.communicate(timeout=120)[0]
        elif how != "closed":
            with os.fdopen(out_r, "rb") as fh:
                got = fh.read() if how == "full" else fh.readline() if how == "line" else fh.read(1)
        err = err_f.result(timeout=120) if err_f is not None else b""
    return child.wait(timeout=120), err, got


def reader_goes_away(root: Path) -> None:
    print("a reader that goes away: quiet, exit 141 for a read-only command (`list | head -1`)")
    pbox = root / "pipe"
    pbox.mkdir()
    for i in range(1500):   # a listing far larger than a pipe buffer
        hand(pbox, f"01ARZ3NDEKTSV4RRFFQ6{i:06d}", name=f"01ARZ3NDEKTSV4RRFFQ6{i:06d}-p.md")
    for i in range(200):    # held claims with long scopes: a large `status --json`
        hand(pbox, f"01ARZ3NDEKTSV4RRFFQ7{i:06d}", name=f"01ARZ3NDEKTSV4RRFFQ7{i:06d}-c.md",
             head=f"id: 01ARZ3NDEKTSV4RRFFQ7{i:06d}\ntype: CLAIM\nfrom: alice\nto: all\n"
                  f"date: 2099-01-01T00:00:00Z\nsubject: claim {i}\n"
                  f"expires: 2099-01-02T00:00:00Z\nscope: file:{i:04d}-{'s' * 900}\n")
    for i in range(1500):   # open ASKs: a large `inbox`
        hand(pbox, f"01ARZ3NDEKTSV4RRFFQ8{i:06d}", name=f"01ARZ3NDEKTSV4RRFFQ8{i:06d}-a.md",
             head=f"id: 01ARZ3NDEKTSV4RRFFQ8{i:06d}\ntype: ASK\nfrom: alice\nto: bob\n"
                  f"date: 2099-01-01T00:00:00Z\nsubject: ask {i}\n")
    big_id = "01ARZ3NDEKTSV4RRFFQ9000000"
    hand(pbox, big_id, name=f"{big_id}-b.md",
         body="".join(f"line {i} of a large body\n" for i in range(12000)))
    commands: dict[str, list[str]] = {
        "list": ["list"], "list --live": ["list", "--live"], "inbox": ["inbox", "--to", "bob"],
        "show": ["show", big_id], "status": ["status", "--now", PIPE_NOW],
        "status --json": ["status", "--json", "--now", PIPE_NOW], "doctor": ["doctor"],
        "latency": ["latency"], "verify": ["verify", big_id],
        "capability": ["capability", "--identity", "alice", "--capability", "x"],
        "--help": ["--help"], "--version": ["--version"],
    }
    must_be_big = ("list", "list --live", "inbox", "show", "status --json")
    everywhere = ("list", "status", "--help")   # one large, one small, one printed by argparse
    reference: dict[str, bytes] = {}
    for entry, base in ENTRIES.items():
        bad: list[str] = []
        ran = 0
        for name, cmd in commands.items():
            if entry != "console script" and name not in everywhere:
                continue
            own, err, full = piped(base + cmd, pbox, "full")
            if entry == "console script":
                reference[name] = full
                with open(root / "unpiped", "wb") as fh:    # stdout on a file, no pipe at all
                    plain = subprocess.run(base + cmd, stdout=fh, stderr=subprocess.PIPE, cwd=HERE,
                                           env=dict(os.environ, AGENT_MAIL_DIR=str(pbox)),
                                           timeout=120)
                if (root / "unpiped").read_bytes() != full or plain.returncode != own:
                    bad.append(f"{name}: a reader that stays got different bytes than a file")
            if err or not full or (name in must_be_big and len(full) < BIG):
                bad.append(f"{name} control: rc={own} {len(full)} bytes, stderr {err[-120:]!r}")
            for how in ("closed", "line", "byte", "kill"):
                rc, err, got = piped(base + cmd, pbox, how)
                ran += 1
                # A reader that is gone before the first write, or that leaves a listing
                # no pipe can hold, must be noticed. A small output may already be in
                # the pipe when its reader leaves: then the command finished normally.
                # Help and --version go out in one write, which -u cannot delay.
                certain = how == "closed" or len(full) >= BIG
                allowed = (GONE,) if certain else (GONE, own)
                if rc not in allowed or err or not full.startswith(got) or (
                        how != "closed" and not got):
                    bad.append(f"{name} | {how}: rc={rc} want {allowed}, {len(got)} bytes "
                               f"{'prefix' if full.startswith(got) else 'NOT A PREFIX'}, "
                               f"stderr {err[-160:]!r}")
        check(f"{entry}: no traceback, empty stderr, exit {GONE}, and what was delivered is a "
              f"prefix of the full output ({ran} runs)", not bad and ran >= 12, "; ".join(bad))
    same = [n for n in everywhere
            if piped(ENTRIES["python agent_mail.py"] + commands[n], pbox, "full")[2] != reference[n]
            and n != "--help"]      # argparse names the program in its usage line
    check("control: every entry point prints the same bytes to a reader that stays", not same,
          str(same))
    doc = json.loads(reference["status --json"])
    check("control: `status --json` read to the end parses, and holds the 200 claims it lists",
          len(doc["claims_held"]) == 200 and doc["claims"]["held"] == 200
          and len(reference["status --json"]) >= BIG, str(len(reference["status --json"])))
    rc, err, got = piped(ENTRIES["console script"] + commands["status --json"], pbox, "line")
    check("`status --json | head -1` is cut cleanly: the opening brace, nothing repeated",
          rc == GONE and got == b"{\n" and not err, f"rc={rc} {got[:40]!r} {err[-120:]!r}")

    print("a reader that goes away: a command that writes mail still finishes, and exits 0")
    wbox = root / "pipe-w"
    wbox.mkdir()
    exp = soon(2)

    def hidden(box: Path) -> list[str]:
        return sorted(f.name for f in box.iterdir() if f.name.startswith("."))

    for entry in ("console script", "python -u agent_mail.py"):
        base, tag = ENTRIES[entry], entry.split()[1]
        argv = base + ["send", "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject",
                       f"gone {tag}", "--body", "b", "--scope", f"file:{tag}", "--expires", exp,
                       "--key", f"key-{tag}"]
        rc, err, _ = piped(argv, wbox, "closed")
        mine = [f for f in md_files(wbox) if f"subject: gone {tag}\n" in f.read_text()]
        check(f"{entry}: `send --scope --key` with no reader exits 0, says nothing, "
              "and the message exists once",
              rc == 0 and not err and len(mine) == 1, f"rc={rc} {err[-200:]!r} {len(mine)}")
        st = json.loads(run(wbox, "status", "--json").stdout)
        check("...and it left no lock, no temp file and no unresolved key marker",
              st["scope_locks"]["total"] == 0 and st["stale_tmp"] == 0
              and st["idem_markers"]["resolved"] == st["idem_markers"]["total"]
              and not [n for n in hidden(wbox) if not n.startswith(".idem.") or "." in n[6:]],
              f"{st['scope_locks']} {st['idem_markers']} {hidden(wbox)}")
        rc, err, _ = piped(argv, wbox, "closed")
        again = subprocess.run(argv, capture_output=True, text=True, cwd=HERE, timeout=120,
                               env=dict(os.environ, AGENT_MAIL_DIR=str(wbox)))
        check("...a keyed retry with no reader exits 0 quietly; with a reader it says `duplicate of`",
              rc == 0 and not err and again.returncode == 0
              and again.stdout.startswith(f"duplicate of {mine[0].name[:26]} ")
              and len([f for f in md_files(wbox) if f"subject: gone {tag}\n" in f.read_text()]) == 1,
              f"rc={rc} {err[-200:]!r} {again.stdout[:80]!r}")
        rc, err, _ = piped(base + ["ask", "--from", "alice", "--to", "bob,carol,dave", "--subject",
                                   f"fan {tag}", "--body", "b"], wbox, "closed")
        fan = [f for f in md_files(wbox) if f"subject: fan {tag}\n" in f.read_text()]
        check(f"{entry}: `ask` to three recipients with no reader still files all three, exit 0",
              rc == 0 and not err and len(fan) == 3, f"rc={rc} {err[-200:]!r} {len(fan)}")
        rc, err, _ = piped(base + ["canary", "--from", "alice", "--to", "bob"], wbox, "closed")
        check(f"{entry}: `canary` with no reader exits 0", rc == 0 and not err,
              f"rc={rc} {err[-200:]!r}")
    for fd_word, what in ((">&-", "stdout closed outright"), ("2>&-", "stderr closed outright")):
        r = subprocess.run(["sh", "-c", f'exec "$@" {fd_word}', "sh", *ENTRIES["console script"],
                            "send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                            "--subject", f"shut {fd_word[0]}", "--body", "b"],
                           capture_output=True, text=True, cwd=HERE, timeout=120,
                           env=dict(os.environ, AGENT_MAIL_DIR=str(wbox)))
        n = len([f for f in md_files(wbox) if f"subject: shut {fd_word[0]}\n" in f.read_text()])
        check(f"`send {fd_word}` ({what}): exit 0, one message", r.returncode == 0 and n == 1
              and not r.stderr, f"rc={r.returncode} {r.stderr[-200:]!r} {n}")

    print("a reader that goes away must not hide a real error")

    def patched(patch: str, *argv: str, how: str = "full", box: Path = wbox,
                unbuffered: bool = False) -> tuple[int, bytes, bytes]:
        code = (f"import errno, sys; sys.path.insert(0, {str(HERE)!r}); import agent_mail\n"
                f"{patch}\nsys.exit(agent_mail.main({list(argv)!r}))\n")
        return piped([sys.executable, *(["-u"] if unbuffered else []), "-c", code], box, how)

    epipe = ("def boom(*a, **k): raise OSError(errno.EPIPE, 'Broken pipe')\n"
             "agent_mail._publish = boom")
    one = ("send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject",
           "never lands", "--body", "b")
    line = b"send failed: cannot write to the mailbox: Broken pipe (EPIPE); nothing written\n"
    before = len(md_files(wbox))
    for how in ("full", "closed"):
        rc, err, got = patched(epipe, *one, how=how)
        check(f"EPIPE from writing the message file is a write failure, not a quiet exit "
              f"(reader {how}): exit 1, the one line, nothing written",
              rc == 1 and err == line and not got and len(md_files(wbox)) == before,
              f"rc={rc} {err[-200:]!r}")
    rc, err, got = patched("def boom(*a, **k): raise BrokenPipeError(errno.EPIPE, 'Broken pipe')\n"
                           "agent_mail.load_messages = boom", "list", box=pbox)
    check("EPIPE from reading the mailbox in `list` is not mistaken for a lost reader: "
          f"a traceback and exit 1, never a silent {GONE}",
          rc == 1 and b"BrokenPipeError" in err and b"Traceback" in err, f"rc={rc} {err[-200:]!r}")
    for how in ("full", "closed"):
        rc, err, got = patched("def boom(*a, **k): raise KeyboardInterrupt\n"
                               "agent_mail._publish = boom", *one, how=how)
        check(f"SIGINT during a send still exits 130 with its line (reader {how})",
              rc == 130 and err == b"interrupted (SIGINT); nothing written\n",
              f"rc={rc} {err[-200:]!r}")
    rc, err, got = patched("def boom(*a, **k): raise KeyboardInterrupt\n"
                           "agent_mail.load_messages = boom", "list", box=pbox, how="closed")
    check("SIGINT during `list` is still an interrupt, with or without a reader",
          rc in (-signal.SIGINT, 130) and b"KeyboardInterrupt" in err, f"rc={rc} {err[-200:]!r}")
    rc, err, got = patched("def boom(*a, **k): raise OSError(errno.ENOSPC, 'No space left on device')\n"
                           "agent_mail._publish = boom", *one, how="closed")
    check("a full disk with no reader: exit 1 and the ENOSPC line",
          rc == 1 and err == b"send failed: cannot write to the mailbox: No space left on device "
                              b"(ENOSPC); nothing written\n", f"rc={rc} {err[-200:]!r}")
    if os.path.exists("/dev/full"):     # Linux: every write to it fails with ENOSPC
        for name in ("list", "capability"):   # one fails in a print, one in the last flush
            with open("/dev/full", "wb") as full_dev:
                r = subprocess.run(ENTRIES["console script"] + commands[name], stdout=full_dev,
                                   stderr=subprocess.PIPE, cwd=HERE, timeout=120,
                                   env=dict(os.environ, AGENT_MAIL_DIR=str(pbox)))
            check(f"`{name} > /dev/full`: a full disk behind stdout is still an error, "
                  f"not a quiet {GONE} or 0",
                  r.returncode not in (0, GONE) and b"No space left" in r.stderr,
                  f"rc={r.returncode} {r.stderr[-200:]!r}")
    else:
        print("  SKIP  stdout on a full device (no /dev/full on this platform)")
    if os.geteuid() != 0:
        robox = root / "pipe-ro"
        robox.mkdir()
        os.chmod(robox, 0o555)
        try:
            for how in ("full", "closed"):
                rc, err, got = piped(ENTRIES["console script"] + list(one), robox, how)
                check(f"a read-only mailbox still exits 1 with its line (reader {how})",
                      rc == 1 and err.startswith(b"send failed: cannot write to the mailbox: ")
                      and err.endswith(b"; nothing written\n") and not list(robox.iterdir()),
                      f"rc={rc} {err[-200:]!r}")
        finally:
            os.chmod(robox, 0o755)
    for entry in ("console script", "python -u agent_mail.py"):
        rc, err, got = piped(ENTRIES[entry] + ["show", "NOSUCHID"], pbox, "closed")
        check(f"{entry}: an error is still printed on stderr when stdout has no reader, "
              "and the exit status is the error's",
              rc == 1 and err == b"no message with id NOSUCHID\n", f"rc={rc} {err[-200:]!r}")
        rc, err, got = piped(ENTRIES[entry] + ["show", "NOSUCHID"], pbox, "full", stderr_gone=True)
        check(f"{entry}: stderr with no reader does not crash or change the exit status",
              rc == 1 and not got, f"rc={rc} {got[:80]!r}")
        rc, err, got = piped(ENTRIES[entry] + ["list"], pbox, "full", stderr_gone=True)
        check("...and output on stdout is still complete",
              rc == 0 and got == reference["list"], f"rc={rc} {len(got)}")
    r = subprocess.run(["sh", "-c", 'exec "$@" 2>&-', "sh", *ENTRIES["console script"],
                        "show", "NOSUCHID"], capture_output=True, text=True, cwd=HERE,
                       timeout=120, env=dict(os.environ, AGENT_MAIL_DIR=str(pbox)))
    check("`show NOSUCHID 2>&-`: exit 1, and the diagnostic does not land on stdout instead",
          r.returncode == 1 and r.stdout == "", f"rc={r.returncode} {r.stdout[:80]!r}")

    print("a reader that goes away: the pickup hook stays silent and exits 0")
    hook = [sys.executable, str(HERE / "hooks" / "agent_mail_check.py")]
    who = {"AGENT_MAIL_IDENTITY": "bob"}
    hbox = root / "pipe-h"
    hbox.mkdir()
    hand(hbox, ULID_A)
    for label, box in (("a short pickup", hbox), ("a pickup larger than a pipe buffer", pbox)):
        rc, err, full = piped(hook, box, "full", env_extra=who)
        check(f"control: the hook delivers {label} to a reader that stays",
              rc == 0 and not err and b"live message(s) addressed to 'bob'" in full
              and (box is hbox or len(full) >= BIG), f"rc={rc} {len(full)} {err[-120:]!r}")
        for extra in ([], ["-u"]):
            for how in ("closed", "line", "kill"):
                rc, err, got = piped([hook[0], *extra, hook[1]], box, how, env_extra=who)
                check(f"the hook {' '.join(extra)} with its reader gone ({how}): exit 0, "
                      "empty stderr, a prefix delivered",
                      rc == 0 and not err and full.startswith(got), f"rc={rc} {err[-200:]!r}")

    print("a reader that goes away: main() in-process leaves the caller's stdout alone")
    sys.path.insert(0, str(HERE))
    import agent_mail
    saved = os.environ.get("AGENT_MAIL_DIR")
    os.environ["AGENT_MAIL_DIR"] = str(pbox)
    fds_before = len(os.listdir("/dev/fd"))
    try:
        def captured(*argv: str) -> tuple[object, str, str]:
            out, err2 = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err2):
                try:
                    rc2: object = agent_mail.main(list(argv))
                except SystemExit as exc:
                    rc2 = f"SystemExit({exc.code})"
            return rc2, out.getvalue(), err2.getvalue()

        rc_l, out_l, err_l = captured("list")
        check("in-process: main(['list']) into a StringIO returns 0 with the same bytes",
              rc_l == 0 and out_l.encode() == reference["list"] and err_l == "", f"rc={rc_l}")
        check("in-process: exit statuses are what they were (show: 1, bad flag: SystemExit(2), "
              "--version: SystemExit(0))",
              captured("show", "NOSUCHID")[::2] == (1, "no message with id NOSUCHID\n")
              and captured("list", "--bogus")[0] == "SystemExit(2)"
              and captured("--version")[:2] == ("SystemExit(0)",
                                                f"agent-postbox {agent_mail.__version__}\n"),
              repr(captured("show", "NOSUCHID")))
        r_fd, w_fd = os.pipe()
        os.close(r_fd)
        broken = os.fdopen(w_fd, "w", encoding="utf-8")
        codes = []
        for _ in range(3):
            with contextlib.redirect_stdout(broken), contextlib.redirect_stderr(io.StringIO()) as e2:
                try:
                    codes.append((agent_mail.main(["list"]), e2.getvalue()))
                except BrokenPipeError as exc:
                    codes.append((-1, repr(exc)))
        with contextlib.redirect_stdout(broken):
            try:
                agent_mail.main(["--help"])
                helped: object = "returned"
            except SystemExit as exc:
                helped = exc.code
            except BrokenPipeError as exc:
                helped = repr(exc)
        check(f"in-process: a stdout whose reader is gone gives {GONE} every time, silently",
              codes == [(GONE, "")] * 3 and helped == GONE, f"{codes} {helped}")
        still_pipe = stat.S_ISFIFO(os.fstat(w_fd).st_mode)
        try:
            broken.flush()                      # nothing may be left for a later flush
            empty = True
        except BrokenPipeError:
            empty = False
        try:
            os.write(w_fd, b"x")
            wrote = True
        except BrokenPipeError:
            wrote = False
        check("in-process: afterwards that stdout is still the caller's pipe (not /dev/null), "
              "and its buffer is empty", still_pipe and empty and not wrote,
              f"{still_pipe} {empty} {wrote}")
        with contextlib.suppress(OSError):
            broken.close()
        rc_l2, out_l2, _ = captured("list")
        check("in-process: the next call with a working stdout prints everything again",
              rc_l2 == 0 and out_l2 == out_l, f"rc={rc_l2} {len(out_l2)}")
        check("in-process: no descriptor is leaked by any of that",
              len(os.listdir("/dev/fd")) == fds_before,
              f"{fds_before} -> {len(os.listdir('/dev/fd'))}")
    finally:
        if saved is None:
            del os.environ["AGENT_MAIL_DIR"]
        else:
            os.environ["AGENT_MAIL_DIR"] = saved
    src = Path(TOOL).read_text(encoding="utf-8")
    check("every line agent_mail.py prints goes through its one print(): no other writer "
          "to stdout or stderr", src.count("builtins.print(") == 1
          and "sys.stdout.write(" not in src and "sys.stderr.write(" not in src
          and "os.write(1," not in src and "os.write(2," not in src, "")



def main() -> int:
    faulthandler.dump_traceback_later(WATCHDOG, exit=True)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        box = root / "mail"
        box.mkdir()

        print("positive control: the harness can send and list")
        ok = send(box, subj="plain subject")
        check("control: plain send accepted", ok.returncode == 0, ok.stderr)
        lst = run(box, "list")
        check("control: plain message listed", "plain subject" in lst.stdout, lst.stdout)

        print("control characters and escapes in headers are refused at send")
        n0 = len(md_files(box))
        bad_headers = {
            "newline in subject (frontmatter injection)": dict(subj="hi\nto: evil"),
            "CR in subject": dict(subj="hi\rto: evil"),
            "ANSI escape in subject": dict(subj="\x1b[31mred\x1b[0m"),
            "DEL in subject": dict(subj="a\x7fb"),
            "C1 control in subject": dict(subj="a\x85b"),
            "U+2028 in subject": dict(subj="a b"),
            "RTL override in subject": dict(subj="abc‮gpj.exe"),
            "zero-width space in subject": dict(subj="ab​cd"),
            "BOM inside subject": dict(subj="ab﻿cd"),
            "ANSI escape in --from": dict(frm="al\x1b[2Jice"),
            "newline in --to": dict(to="bob\nfrom: root"),
            "RTL override in --to": dict(to="bo‮b"),
            "space in --from": dict(frm="al ice"),
            "colon in --to": dict(to="bob:evil"),
            "non-ASCII homoglyph in --from": dict(frm="alіce"),
        }
        for label, kw in bad_headers.items():
            cp = send(box, **kw)
            check(f"refused: {label}", cp.returncode == 2 and len(md_files(box)) == n0,
                  f"rc={cp.returncode} {cp.stderr}")
        check("control: box unchanged and still accepts mail",
              send(box, subj="after hostile").returncode == 0 and len(md_files(box)) == n0 + 1)

        print("NUL and control bytes in the body")
        n0 = len(md_files(box))
        for label, text in [("NUL byte", "a\x00b"), ("ESC / ANSI", "a\x1b[31mred"),
                            ("BEL", "a\x07b"), ("DEL", "a\x7fb")]:
            cp = send(box, stdin=text)
            check(f"body refused: {label}", cp.returncode == 2 and len(md_files(box)) == n0,
                  f"rc={cp.returncode} {cp.stderr}")
        cp = send(box, stdin="line1\n\tindented\r\nline3 é\U0001f600\n")
        check("control: tabs, CRLF, newlines, emoji in a body are fine",
              cp.returncode == 0, cp.stderr)

        print("path traversal in identifier fields")
        n0 = len(md_files(box))
        for label, kw in {
            "../ in --from": dict(frm="../etc/passwd"),
            "absolute --to": dict(to="/etc/passwd"),
            "backslash --from": dict(frm="..\\..\\evil"),
            "slash --to": dict(to="a/b"),
            "dotdot --to": dict(to=".."),
            "../ in --re": dict(extra=("--re", "../../etc/passwd")),
            "absolute --reply-to": dict(extra=("--reply-to", "/etc/passwd")),
            "backslash --re": dict(extra=("--re", "..\\x")),
            "../ in --supersedes": dict(extra=("--supersedes", "../../x")),
        }.items():
            cp = send(box, **kw)
            check(f"refused: {label}", cp.returncode == 2 and len(md_files(box)) == n0,
                  f"rc={cp.returncode} {cp.stderr}")
        # --scope and --key are only ever hashed into lock/marker names, so a
        # traversal string there is inert; prove it stays inside the box.
        n1 = len(md_files(box))
        cp = send(box, extra=("--key", "../../k"))
        cp2 = send(box, kind="CLAIM", extra=("--scope", "../x", "--expires", soon(2)))
        check("../ in --key/--scope is inert (hashed) and writes only inside the box",
              cp.returncode == 0 and cp2.returncode == 0 and len(md_files(box)) == n1 + 2
              and not (root / "k").exists() and not (root / "x").exists(),
              cp.stderr + cp2.stderr)
        check("control: no file was created outside the box",
              not (root / "etc").exists() and send(box, subj="still ok").returncode == 0)
        # the filename is derived from id+slug only; a hostile subject cannot steer it
        cp = send(box, subj="../../../tmp/pwn /etc/passwd")
        names = [p.name for p in md_files(box)]
        check("hostile-looking subject is slugged into a safe filename",
              cp.returncode == 0 and all("/" not in n and ".." not in n for n in names), str(names))

        print("oversized fields")
        n0 = len(md_files(box))
        cp = send(box, subj="x" * 5000)
        check("huge subject refused", cp.returncode == 2 and len(md_files(box)) == n0, cp.stderr)
        cp = send(box, subj="y" * 200)
        check("control: 200-char subject accepted", cp.returncode == 0, cp.stderr)
        cp = send(box, frm="a" * 500)
        check("huge --from refused", cp.returncode == 2, cp.stderr)
        cp = send(box, stdin="z" * (17 * 1024 * 1024))
        check("17 MiB body refused", cp.returncode == 2 and len(md_files(box)) == n0 + 1, cp.stderr)
        cp = send(box, stdin="z" * (2 * 1024 * 1024))
        check("control: 2 MiB body accepted", cp.returncode == 0, cp.stderr)

        print("Unicode")
        cp = send(box, subj="café \U0001f600 naïve", stdin="‮ body may carry bidi\n")
        check("control: emoji + accents in subject accepted; body text is not policed beyond controls",
              cp.returncode == 0, cp.stderr)
        nfc = send(box, subj="café")
        nfd = send(box, subj="café")
        check("NFD subject accepted and stored NFC",
              nfc.returncode == 0 and nfd.returncode == 0)
        stored = {p.read_text(encoding="utf-8") for p in md_files(box)}
        check("no NFD subject line on disk", all(
            unicodedata.normalize("NFC", t) == t for t in stored if "subject: caf" in t))
        names = [p.name for p in md_files(box)]
        check("filenames unique after normalization collisions", len(names) == len(set(names)))

        print("hand-written files: quarantine at read")
        qbox = root / "q"
        qbox.mkdir()
        send(qbox, subj="good one")
        hand(qbox, ULID_A, head=(f"id: {ULID_A}\ntype: NOTICE\nfrom: eve\nto: bob\n"
                                 "date: 2099-01-01T00:00:00Z\nsubject: ok one\n"))
        ctl = run(qbox, "list")
        check("control: a well-formed hand-written file IS admitted",
              "ok one" in ctl.stdout and ctl.returncode == 0, ctl.stdout + ctl.stderr)
        ULID_C = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
        hand(qbox, ULID_C, name="dup.md", extra="from: root\n")
        ULID_D = "01ARZ3NDEKTSV4RRFFQ69G5FAY"
        hand(qbox, ULID_D, name="dup2.md",
             head=(f"id: {ULID_D}\ntype: NOTICE\nFROM: root\nfrom: eve\nto: bob\n"
                   "date: 2099-01-01T00:00:00Z\nsubject: caseDup\n"))
        ULID_E = "01ARZ3NDEKTSV4RRFFQ69G5FAZ"
        hand(qbox, ULID_E, name="esc.md",
             head=(f"id: {ULID_E}\ntype: NOTICE\nfrom: eve\nto: bob\n"
                   "date: 2099-01-01T00:00:00Z\nsubject: \x1b[31mred\n"))
        ULID_F = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
        hand(qbox, ULID_F, name="nul.md", body="ab\x00cd\n")
        ULID_G = "01ARZ3NDEKTSV4RRFFQ69G5FB1"
        hand(qbox, ULID_G, name="rtl.md",
             head=(f"id: {ULID_G}\ntype: NOTICE\nfrom: eve\nto: bob\n"
                   "date: 2099-01-01T00:00:00Z\nsubject: abc‮gpj\n"))
        (qbox / "latin1.md").write_bytes(b"---\nid: x\n---\n\xff\xfe\n")
        res = run(qbox, "list")
        for tag, fname in [("duplicate from: key", "dup.md"), ("case-variant duplicate key", "dup2.md"),
                           ("ESC in header", "esc.md"), ("NUL in body", "nul.md"),
                           ("RTL override in header", "rtl.md"), ("non-UTF-8 bytes", "latin1.md")]:
            check(f"quarantined: {tag}", f"REJECT {fname}" in res.stderr, res.stderr)
        check("no traceback from hostile files", "Traceback" not in res.stderr + res.stdout, res.stderr)
        # Only the message lines count: the "# mailbox: <path>" header carries a random
        # temp-directory name, which can contain any short letter sequence.
        listed = "\n".join(ln for ln in res.stdout.splitlines() if not ln.startswith("# mailbox"))
        check("quarantined content never listed", "caseDup" not in listed and "\x1b" not in listed
              and "gpj" not in listed)
        check("control: good messages survive beside the rejects",
              "good one" in res.stdout and "ok one" in res.stdout)
        shown = run(qbox, "show", ULID_E)
        check("show does not surface a quarantined message", shown.returncode == 1 and "\x1b" not in shown.stdout)
        docq = run(qbox, "doctor")
        check("doctor surfaces the quarantine count", "rejects:" in docq.stdout, docq.stdout)

        print("symlinks planted in the mailbox")
        sbox = root / "s"
        sbox.mkdir()
        outside = root / "outside"
        outside.mkdir()
        send(sbox, subj="legit")
        secret = outside / "secret.md"
        secret.write_text(
            f"---\nid: {ULID_A}\ntype: NOTICE\nfrom: eve\nto: bob\n"
            "date: 2099-01-01T00:00:00Z\nsubject: SECRET-LEAK\n---\n\nSECRET-BODY\n")
        os.symlink(secret, sbox / "planted.md")
        os.symlink(root / "does-not-exist", sbox / "dangling.md")
        os.symlink(outside, sbox / "dirlink.md")
        res = run(sbox, "list")
        check("symlinked .md is not read", "SECRET-LEAK" not in res.stdout, res.stdout)
        check("symlinked .md is quarantined loudly",
              "REJECT planted.md" in res.stderr and "symlink" in res.stderr, res.stderr)
        check("dangling and directory symlinks quarantined, no traceback",
              "REJECT dangling.md" in res.stderr and "REJECT dirlink.md" in res.stderr
              and "Traceback" not in res.stderr, res.stderr)
        check("control: the real message beside the links is still listed", "legit" in res.stdout)
        sh = run(sbox, "show", ULID_A)
        check("show cannot be steered through the link", "SECRET-BODY" not in sh.stdout, sh.stdout)
        check("control: the same file read directly IS readable (link was the only barrier)",
              "SECRET-LEAK" in secret.read_text())
        # a symlinked mailbox directory chosen by the operator is theirs to choose
        linkbox = root / "boxlink"
        os.symlink(sbox, linkbox)
        res = run(linkbox, "list")
        check("control: operator-chosen symlinked mailbox dir still works", "legit" in res.stdout, res.stderr)

        print("FIFO planted as a .md must not hang the reader")
        fbox = root / "f"
        fbox.mkdir()
        send(fbox, subj="beside fifo")
        os.mkfifo(fbox / "trap.md")
        t0 = time.time()
        try:
            res = run(fbox, "list")
            hung = False
        except subprocess.TimeoutExpired:
            hung = True
        check("FIFO does not hang list", not hung and time.time() - t0 < 30)
        if not hung:
            check("FIFO quarantined; real message listed",
                  "REJECT trap.md" in res.stderr and "beside fifo" in res.stdout, res.stderr)

        print("oversized file at read")
        obox = root / "o"
        obox.mkdir()
        send(obox, subj="small")
        hand(obox, ULID_A, name="huge.md", body="x" * (33 * 1024 * 1024))
        res = run(obox, "list")
        check("file over the read cap is quarantined", "REJECT huge.md" in res.stderr, res.stderr[:200])
        check("control: small message still listed", "small" in res.stdout)

        print("10k-message box: listing stays usable")
        bbox = root / "big"
        bbox.mkdir()
        for i in range(10000):
            ident = (ULID_A[:16] + format(i, "010d"))
            (bbox / f"{ident}-n{i}.md").write_text(
                f"---\nid: {ident}\ntype: {'ASK' if i % 2 == 0 else 'NOTICE'}\nfrom: a{i % 50}\n"
                f"to: all\ndate: 2099-01-01T00:00:00Z\nsubject: n{i}\n---\n\nbody\n")
        t0 = time.time()
        res = run(bbox, "list", "--live")
        dt_list = time.time() - t0
        check(f"list --live of 10k files finishes fast ({dt_list:.1f}s < 30s)",
              res.returncode == 0 and dt_list < 30, res.stderr[:200])
        check("control: the listing actually contains the messages",
              res.stdout.count("\n") > 10000, str(res.stdout.count("\n")))

        print("body-file errors are one line, not a traceback")
        miss = run(box, "send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                   "--subject", "s", "--body-file", str(root / "nope.txt"))
        check("missing --body-file: exit 2, no traceback",
              miss.returncode == 2 and "Traceback" not in miss.stderr, miss.stderr)
        (root / "bin.dat").write_bytes(b"\xff\xfe\x00")
        binf = run(box, "send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                   "--subject", "s", "--body-file", str(root / "bin.dat"))
        check("non-UTF-8 --body-file: exit 2, no traceback",
              binf.returncode == 2 and "Traceback" not in binf.stderr, binf.stderr)
        (root / "ok.txt").write_text("fine body\n")
        okf = run(box, "send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                  "--subject", "s", "--body-file", str(root / "ok.txt"))
        check("control: a readable --body-file works", okf.returncode == 0, okf.stderr)

        print("the prompt hook never surfaces a quarantined message")
        hbox = root / "h"
        hbox.mkdir()
        send(hbox, frm="alice", to="me", subj="visible-to-hook")
        hand(hbox, ULID_A, name="evil.md",
             head=(f"id: {ULID_A}\ntype: NOTICE\nfrom: eve\nto: me\n"
                   "date: 2099-01-01T00:00:00Z\nsubject: \x1b[31mINJECTED\n"))
        os.symlink(root / "outside" / "x.md", hbox / "link.md")
        hook = subprocess.run(
            [sys.executable, "-I", str(HERE / "hooks" / "agent_mail_check.py")],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, AGENT_MAIL_DIR=str(hbox), AGENT_MAIL_IDENTITY="me"))
        check("control: the hook does deliver the good message",
              "visible-to-hook" in hook.stdout, hook.stdout + hook.stderr)
        check("hook output has no ESC and no quarantined subject",
              "\x1b" not in hook.stdout and "INJECTED" not in hook.stdout and hook.returncode == 0)

        print("a hand-written supersedes cannot void another agent's claim (PROTOCOL.md section 32)")
        import json  # noqa: PLC0415
        vbox = root / "void"
        vbox.mkdir()
        exp_v = soon(2)
        own = send(vbox, frm="alice", to="all", kind="CLAIM", subj="alice-lease",
                   extra=("--scope", "db", "--expires", exp_v))
        own_id = mid(own)

        def claims(b: Path) -> dict[str, int]:
            return json.loads(run(b, "status", "--json").stdout)["claims"]

        def listed(b: Path, subject: str) -> str:
            return next((ln for ln in run(b, "list").stdout.splitlines() if ln.endswith("  " + subject)), "")

        check("control: the lease is held", "[held" in listed(vbox, "alice-lease")
              and claims(vbox)["held"] == 1, listed(vbox, "alice-lease"))
        forged = [
            ("01ARZ3NDEKTSV4RRFFQ69GV0D0", "NOTICE", "mallory", own_id, ""),
            ("01ARZ3NDEKTSV4RRFFQ69GV0D1", "NOTICE", "mallory", own_id.lower(), ""),
            ("01ARZ3NDEKTSV4RRFFQ69GV0D2", "CLAIM", "mallory", own_id, f"expires: {exp_v}\n"),
            ("01ARZ3NDEKTSV4RRFFQ69GV0D3", "DISPUTE", "bob", own_id, ""),
            ("01ARZ3NDEKTSV4RRFFQ69GV0D4", "NOTICE", "alice", own_id, ""),  # owner, but not a CLAIM
        ]
        for fid, kind, who, cite, extra in forged:
            hand(vbox, fid, name=f"{fid}-forged.md",
                 head=(f"id: {fid}\ntype: {kind}\nfrom: {who}\nto: all\ndate: 2099-01-01T00:00:00Z\n"
                       f"subject: forged-{fid[-1]}\nsupersedes: {cite}\n{extra}"))
            check(f"{kind} from {who} citing the lease in supersedes: the lease stays held",
                  "[held" in listed(vbox, "alice-lease"), listed(vbox, "alice-lease"))
        check("status counts the lease as held and nothing as superseded",
              claims(vbox)["superseded"] == 0 and claims(vbox)["held"] == 2, str(claims(vbox)))
        rival = send(vbox, frm="mallory", to="all", kind="CLAIM", subj="grab",
                     extra=("--scope", "db", "--expires", exp_v))
        check("the forger's own claim on the scope is refused (exit 3, held by alice)",
              rival.returncode == 3 and "already held by alice" in rival.stderr, rival.stderr)
        hk = subprocess.run(
            [sys.executable, "-I", str(HERE / "hooks" / "agent_mail_check.py")],
            capture_output=True, text=True, timeout=120,
            env=dict(os.environ, AGENT_MAIL_DIR=str(vbox), AGENT_MAIL_IDENTITY="all"))
        check("the hook still shows the lease as held, and delivers the forgeries as ordinary mail",
              f"id: {own_id}" in hk.stdout and f"holds until: {exp_v}" in hk.stdout
              and "forged-0" in hk.stdout, hk.stdout)
        out = run(vbox, "list")
        check("nothing is quarantined: the forgeries are evidence, not authority",
              out.returncode == 0 and "REJECT" not in out.stderr and len(md_files(vbox)) == 6, out.stderr)
        hand(vbox, "01ARZ3NDEKTSV4RRFFQ69GV0D5", name="01ARZ3NDEKTSV4RRFFQ69GV0D5-own.md",
             head=("id: 01ARZ3NDEKTSV4RRFFQ69GV0D5\ntype: claim\nfrom: ALICE\nto: all\n"
                   f"date: 2099-01-01T00:00:00Z\nsubject: own-hand\nsupersedes: {own_id.lower()}\n"
                   f"expires: {exp_v}\n"))
        check("control: a hand-written CLAIM from the owner (any case) does supersede it",
              "[superseded" in listed(vbox, "alice-lease") and claims(vbox)["superseded"] == 1,
              listed(vbox, "alice-lease"))

        print("concurrent create/renew races (real subprocesses)")
        cbox = root / "c"
        cbox.mkdir()
        exp = soon(2)

        def try_claim(who: str) -> int:
            return send(cbox, frm=who, to="all", kind="CLAIM", subj=f"claim {who}",
                        extra=("--scope", "the-thing", "--expires", exp)).returncode

        with ThreadPoolExecutor(max_workers=12) as ex:
            rcs = list(ex.map(try_claim, [f"agent-{i}" for i in range(12)]))
        won = [r for r in rcs if r == 0]
        check("12 racing claims on one scope: exactly one wins",
              len(won) == 1 and all(r in (0, 3) for r in rcs), str(rcs))
        held = [m for m in run(cbox, "list").stdout.splitlines() if "[held" in m]
        check("control: the winner is listed as held; the box has exactly one claim",
              len(held) == 1 and len(md_files(cbox)) == 1, str(held))
        first = next(p.name.split("-")[0] for p in md_files(cbox))
        winner = next(m for m in run(cbox, "list").stdout.splitlines() if "[held" in m).split()[3]

        def renew(i: int) -> int:
            return send(cbox, frm=winner, to="all", kind="CLAIM", subj=f"renew {i}",
                        extra=("--scope", "the-thing", "--expires", exp,
                               "--supersedes", first)).returncode

        with ThreadPoolExecutor(max_workers=8) as ex:
            rcs = list(ex.map(renew, range(8)))
        check("racing renewals by the owner never crash and never fail unexpectedly",
              all(r in (0, 3) for r in rcs) and any(r == 0 for r in rcs), str(rcs))
        out = run(cbox, "list")
        check("renewal race leaves no rejects and a parseable box",
              out.returncode == 0 and "REJECT" not in out.stderr, out.stderr)
        stray = [p.name for p in cbox.iterdir() if p.name.endswith(".tmp")]
        check("no temp files left after the races", not stray, str(stray))
        rival = try_claim("intruder")
        check("control: a rival is still refused while the owner's lease is held", rival == 3, str(rival))

        print("writers SIGKILLed at arbitrary moments, then restarted with the same keys")
        # Not at a chosen chokepoint (test_hardening.py does that): sixteen keyed
        # senders of a 200 kB body are each killed a little later than the one
        # before, from "barely started" to "about done". Wherever the kill lands,
        # the mailbox must stay readable and a restart must end with exactly one
        # message per key.
        kbox = root / "kill"
        kbox.mkdir()
        TAIL = "tail-of-the-body"
        (root / "kbody.txt").write_text("filler line of a large body\n" * 7000 + TAIL + "\n")

        def storm_argv(i: int) -> list[str]:
            return ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject",
                    f"storm {i:02d}", "--body-file", str(root / "kbody.txt"), "--key", f"storm-{i:02d}"]

        def victim(i: int) -> int:
            proc = subprocess.Popen([sys.executable, "-I", TOOL, *storm_argv(i)],
                                    env=dict(os.environ, AGENT_MAIL_DIR=str(kbox)),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            until = time.monotonic() + i * 0.006
            while time.monotonic() < until and proc.poll() is None:   # bounded: 0.09 s at most
                time.sleep(0.001)
            proc.kill()
            return proc.wait(timeout=120)

        with ThreadPoolExecutor(max_workers=4) as ex:
            deaths = list(ex.map(victim, range(16)))
        check("control: every writer ended by SIGKILL or had already finished; at least one was killed",
              set(deaths) <= {-9, 0} and -9 in deaths, str(deaths))
        after = run(kbox, "list")
        whole = [f.read_text(encoding="utf-8").rstrip("\n").endswith(TAIL) for f in md_files(kbox)]
        check("after the kills: `list` exits 0 with no REJECT; every visible message is complete",
              after.returncode == 0 and "REJECT" not in after.stdout + after.stderr and all(whole),
              f"rc={after.returncode} {after.stderr[:200]} whole={whole}")
        check("every writer that exited 0 left its message; no message appears twice",
              all(after.stdout.count(f"storm {i:02d}\n") == 1 for i, rc in enumerate(deaths) if rc == 0)
              and all(after.stdout.count(f"storm {i:02d}\n") <= 1 for i in range(16)), after.stdout[-300:])
        temps = sorted(f.name for f in kbox.iterdir() if f.name.endswith(".tmp"))
        st_k = json.loads(run(kbox, "status", "--json").stdout)
        # A writer killed between link(2) and removing its temp leaves both: that message
        # is published and listed (test_hardening.py, recovery scenario 2). Only a temp
        # whose message was never linked must stay out of the listing.
        unlinked = [t for t in temps if not (kbox / t[1:].rsplit(".", 2)[0]).exists()]
        check("a killed writer's temp file is hidden, not listed unless it was linked, and young: "
              "not yet reported as stale",
              len(temps) <= deaths.count(-9) and st_k["stale_tmp"] == 0 and st_k["quarantined"] == 0
              and not any(t[1:27] in after.stdout for t in unlinked), f"{temps} {st_k['stale_tmp']}")
        again = [run(kbox, *storm_argv(i)) for i in range(16)]
        final = run(kbox, "list")
        check("restart with the same keys: every send exits 0 and exactly one message per key exists",
              [r.returncode for r in again] == [0] * 16 and len(md_files(kbox)) == 16
              and all(final.stdout.count(f"storm {i:02d}\n") == 1 for i in range(16)),
              str([(r.returncode, r.stderr[:80]) for r in again if r.returncode]) + final.stdout[-200:])
        check("...and a writer that had finished before its kill is answered 'duplicate of'",
              all("duplicate of" in again[i].stdout for i, rc in enumerate(deaths) if rc == 0),
              str([again[i].stdout[:40] for i, rc in enumerate(deaths) if rc == 0]))
        for name in temps:
            old = time.time() - 700
            os.utime(kbox / name, (old, old))
        doc = run(kbox, "doctor")
        check("ten minutes on, doctor reports each leftover temp file by name and rejects nothing",
              f"stale_tmp: {len(temps)}" in doc.stdout and all(t in doc.stdout for t in temps)
              and "rejects" not in doc.stdout and "Traceback" not in doc.stderr, doc.stdout[-300:])
        markers = [f for f in kbox.iterdir() if f.name.startswith(".idem.") and "." not in f.name[6:]]
        ids = {f.name[:26] for f in md_files(kbox)}
        check("sixteen key markers, each naming a published message; no lock or takeover file",
              len(markers) == 16 and all(m.read_text().split("\n")[0] in ids for m in markers)
              and not [f.name for f in kbox.iterdir() if f.name.endswith((".lock", ".takeover"))],
              str(sorted(f.name for f in kbox.iterdir() if f.name.startswith("."))[:6]))

        reader_goes_away(root)

    faulthandler.cancel_dump_traceback_later()
    if FAILS:
        print(f"\n{len(FAILS)} FAILED")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
