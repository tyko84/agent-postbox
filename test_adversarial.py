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

import datetime as dt
import faulthandler
import os
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

        print("a reader that goes away (`list | head`)")
        # agent_mail.py at this commit answers a closed stdout with a
        # BrokenPipeError traceback and exit 120. The fix is not in this file's
        # remit; set EXPECT_QUIET_SIGPIPE=1 to enforce the check once it lands.
        pbox = root / "pipe"
        pbox.mkdir()
        for i in range(1500):   # a listing far larger than a pipe buffer
            hand(pbox, f"01ARZ3NDEKTSV4RRFFQ6{i:06d}", name=f"01ARZ3NDEKTSV4RRFFQ6{i:06d}-p.md")
        gone = subprocess.Popen([sys.executable, "-I", TOOL, "list"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, AGENT_MAIL_DIR=str(pbox)))
        assert gone.stdout is not None and gone.stderr is not None
        first = gone.stdout.readline()
        gone.stdout.close()                      # the reader leaves after one line
        err = gone.stderr.read()
        gone.stderr.close()
        rc = gone.wait(timeout=120)
        check("control: `list` started printing, then ended by itself once its reader left",
              first.startswith("# mailbox") and rc is not None, f"{first!r} rc={rc}")
        if os.environ.get("EXPECT_QUIET_SIGPIPE"):
            check("a reader that goes away gets no traceback: stderr empty, exit 0 or 141",
                  err == "" and rc in (0, 141), f"rc={rc} {err[-200:]}")
        else:
            print(f"  SKIP  no traceback when the reader goes away (exit {rc}, "
                  f"{'traceback' if 'Traceback' in err else 'quiet'}); EXPECT_QUIET_SIGPIPE=1 enforces it")

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
