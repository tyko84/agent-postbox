#!/usr/bin/env python3
"""Deterministic tests for claim/lease validation, idempotent send, and quarantine.

No sleeps: expiry boundaries are fixed ISO strings relative to a far-future/past
constant, and races are forced by running real subprocesses against one mailbox.
Interrupted writers are real children SIGKILLed at a chokepoint; the parent only
polls for their ready-file in bounded 0.05 s steps. File ages are set with utime.
Each negative check has a positive control in the same mailbox.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = str(HERE / "agent_mail.py")
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(f"{name}: {detail[:300]}")


def run(box: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, AGENT_MAIL_DIR=str(box))
    return subprocess.run([sys.executable, "-I", TOOL, *argv],
                          capture_output=True, text=True, env=env)


def soon(hours: float) -> str:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def send(box: Path, who: str, *extra: str, kind: str = "NOTICE", subj: str = "s",
         body: str = "b") -> subprocess.CompletedProcess[str]:
    return run(box, "send", "--type", kind, "--from", who, "--to", "all",
               "--subject", subj, "--body", body, *extra)


def mid(cp: subprocess.CompletedProcess[str]) -> str:
    return next(x.split(": ", 1)[1] for x in cp.stdout.splitlines() if x.startswith("id: "))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        box = Path(td) / "mail"
        box.mkdir()

        print("expiry validation at send")
        ok = send(box, "alice", "--expires", soon(1), kind="CLAIM")
        check("control: valid claim accepted", ok.returncode == 0, ok.stderr)
        for label, val in [("past", "2000-01-01T00:00:00Z"),
                           ("naive (no zone)", "2099-01-01T00:00:00"),
                           ("date only", "2099-01-01"),
                           ("garbage", "tomorrow"),
                           ("far future (> max lease)", "9999-12-31T00:00:00Z")]:
            cp = send(box, "alice", "--expires", val, kind="CLAIM")
            check(f"claim with {label} expiry refused", cp.returncode == 2, cp.stdout + cp.stderr)

        print("doctor distinguishes malformed from missing expiry")
        (box / "01ARZ3NDEKTSV4RRFFQ69G5FAV-hand.md").write_text(
            "---\nid: 01ARZ3NDEKTSV4RRFFQ69G5FAV\ntype: CLAIM\nfrom: x\nto: all\n"
            "date: 2026-01-01T00:00:00Z\nsubject: hand\nexpires: tomorrow\n---\n\nbody\n")
        (box / "01ARZ3NDEKTSV4RRFFQ69G5FAW-none.md").write_text(
            "---\nid: 01ARZ3NDEKTSV4RRFFQ69G5FAW\ntype: CLAIM\nfrom: x\nto: all\n"
            "date: 2026-01-01T00:00:00Z\nsubject: none\n---\n\nbody\n")
        d = run(box, "doctor").stdout
        check("malformed expiry labelled with raw text", "MALFORMED_EXPIRES 'tomorrow'" in d, d)
        check("missing expiry labelled NO_EXPIRES", "NO_EXPIRES" in d, d)
        lst = run(box, "list").stdout
        check("neither becomes a hold", "[expired   ] x -> all  hand" in lst
              and "[expired   ] x -> all  none" in lst, lst)
        check("evidence still on disk", len(list(box.glob("01ARZ3NDEK*"))) == 2)

        print("quarantine of unaddressable / undated / unknown-type mail")
        for name, fm in [("notype", "type: BOGUS\nfrom: x\nto: all\ndate: 2026-01-01T00:00:00Z"),
                         ("noto", "type: NOTICE\nfrom: x\ndate: 2026-01-01T00:00:00Z"),
                         ("baddate", "type: NOTICE\nfrom: x\nto: all\ndate: garbage")]:
            i = f"01ARZ3NDEKTSV4RRFFQ69G6{name[:2].upper()[0]}{len(name)}"
            i = (i + "00000000000000000000000000")[:26]
            (box / f"{i}-{name}.md").write_text(f"---\nid: {i}\n{fm}\nsubject: {name}\n---\n\nbody\n")
            out = run(box, "list")
            check(f"{name}: loud REJECT", f"{name}" in out.stderr and "REJECT" in out.stderr, out.stderr)
            check(f"{name}: not listed as live mail", f"-{name}" not in out.stdout and
                  f"  {name}" not in out.stdout, out.stdout)

        print("duplicate ids")
        src = next(box.glob("*-hand.md"))
        (box / "01ARZ3NDEKTSV4RRFFQ69G5FAV-dup.md").write_text(src.read_text())
        err = run(box, "list").stderr
        check("second file with same id rejected", "duplicate id" in err, err)

        print("supersede rules")
        c = send(box, "alice", "--expires", soon(2), kind="CLAIM", subj="own")
        cid = mid(c)
        r1 = send(box, "mallory", "--expires", soon(1), "--supersedes", cid, kind="CLAIM")
        check("another sender cannot supersede a claim", r1.returncode == 2, r1.stderr)
        r2 = send(box, "alice", "--expires", soon(1), "--supersedes", "NOSUCHID", kind="CLAIM")
        check("nonexistent supersede target refused", r2.returncode == 2, r2.stderr)
        r3 = send(box, "alice", "--expires", soon(1), "--supersedes", cid, kind="CLAIM", subj="renew")
        check("owner can renew (supersede with CLAIM)", r3.returncode == 0, r3.stderr)

        print("scope conflicts")
        a = send(box, "alice", "--expires", soon(1), "--scope", "file:f.py", kind="CLAIM", subj="a")
        check("first claim on scope accepted", a.returncode == 0, a.stderr)
        b = send(box, "bob", "--expires", soon(1), "--scope", "file:f.py", kind="CLAIM", subj="b")
        check("second sender on same scope refused (exit 3)", b.returncode == 3, b.stderr)
        e = send(box, "bob", "--expires", soon(1), "--scope", "file:other.py", kind="CLAIM", subj="c")
        check("control: different scope fine", e.returncode == 0, e.stderr)
        al = send(box, "alice", "--expires", soon(1), "--scope", "file:f.py", kind="CLAIM", subj="a2")
        check("same owner may re-claim own scope", al.returncode == 0, al.stderr)

        print("simultaneous acquisition: exactly one winner")
        with ThreadPoolExecutor(8) as pool:
            res = list(pool.map(
                lambda n: send(box, f"racer{n}", "--expires", soon(1), "--scope", "file:race.py",
                               kind="CLAIM", subj=f"r{n}"), range(8)))
        won = [r for r in res if r.returncode == 0]
        check("exactly one racer wins", len(won) == 1, str([r.returncode for r in res]))
        held = [m for m in box.glob("*.md") if "scope: file:race.py" in m.read_text()]
        check("exactly one claim on disk for the contested scope", len(held) == 1, str(len(held)))

        print("idempotent send")
        k1 = send(box, "alice", "--key", "retry-1", subj="once", body="same")
        k2 = send(box, "alice", "--key", "retry-1", subj="once", body="same")
        check("first send writes", k1.returncode == 0, k1.stderr)
        check("retry returns the original id", k2.returncode == 0 and mid(k2) == mid(k1), k2.stdout)
        n = sum(1 for m in box.glob("*.md") if "idem: retry-1" in m.read_text())
        check("retry wrote nothing", n == 1, str(n))
        k3 = send(box, "alice", "--key", "retry-1", subj="once", body="DIFFERENT")
        check("same key, different content refused", k3.returncode == 2, k3.stderr)
        k4 = send(box, "bob", "--key", "retry-1", subj="once", body="same")
        check("control: key is scoped per sender", k4.returncode == 0 and mid(k4) != mid(k1))
        with ThreadPoolExecutor(8) as pool:
            rr = list(pool.map(lambda _: send(box, "alice", "--key", "burst", subj="b", body="x"),
                               range(8)))
        ids = {mid(r) for r in rr if r.returncode == 0}
        files = sum(1 for m in box.glob("*.md") if "idem: burst" in m.read_text())
        check("8 simultaneous retries -> one message", len(ids) == 1 and files == 1,
              f"ids={len(ids)} files={files}")
        orphan = box / ".idem.deadbeefdeadbeefdeadbeef"
        check("control: idem markers are hidden from readers", all(
            m.name.startswith("0") for m in box.glob("*.md")) and not orphan.exists())

        print("falsifier regressions")
        import hashlib
        h = hashlib.sha256(b"a\0b\0k1").hexdigest()[:24]
        chash = hashlib.sha256(b"NOTICE\0s\0body").hexdigest()
        (box / f".idem.{h}").write_text(f"01ARZ3NDEKTSV4RRFFQ69GZZZZ\n{chash}\n")
        with ThreadPoolExecutor(8) as pool:
            cr = list(pool.map(lambda _: run(
                box, "send", "--type", "NOTICE", "--from", "a", "--to", "b", "--subject", "s",
                "--body", "body", "--key", "k1"), range(8)))
        n = sum(1 for m in box.glob("*.md") if "idem: k1" in m.read_text())
        check("orphaned marker + 8 simultaneous retries -> one message", n == 1 and
              all(r.returncode == 0 for r in cr), f"files={n}")
        s1 = send(box, "carol", "--expires", soon(1), "--scope", "file:s.py", kind="CLAIM")
        s2 = send(box, "dave", "--expires", soon(1), "--scope", "file:s.py", "--key", "kk",
                  kind="CLAIM")
        check("refused scope claim exits 3", s1.returncode == 0 and s2.returncode == 3)
        check("refused claim leaves no idem marker or lock behind", not any(
            p.name.startswith(".idem.") and p.read_text().split("\n")[0] == "" or
            p.name.endswith(".lock") for p in box.iterdir() if p.name != f".idem.{h}"),
              str([p.name for p in box.iterdir() if p.name.startswith(".")]))
        # lock released even when publish blows up (disk full etc.)
        import importlib.util
        spec = importlib.util.spec_from_file_location("am", TOOL)
        am = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(am)  # type: ignore[union-attr]
        os.environ["AGENT_MAIL_DIR"] = str(box)
        def boom(*_a: object) -> None:
            raise OSError("disk full")
        am._publish = boom
        try:
            am.main(["send", "--type", "CLAIM", "--from", "erin", "--to", "all",
                     "--subject", "x", "--body", "b", "--expires", soon(1),
                     "--scope", "file:boom.py", "--key", "bk"])
        except OSError:
            pass
        left = [p.name for p in box.iterdir() if p.name.endswith(".lock")]
        check("scope lock released after a publish failure", not left, str(left))
        check("idem marker released after a publish failure",
              not (box / (".idem." + hashlib.sha256(b"erin\0all\0bk").hexdigest()[:24])).exists())

        print("stale temp files are reported")
        t = box / ".01ARZ-x.md.1.tmp"
        t.write_text("partial")
        os.utime(t, (0, 0))
        check("doctor lists stale_tmp", "stale_tmp: 1" in run(box, "doctor").stdout)

        print("status: read-only, deterministic, stable keys")
        sbox = Path(td) / "status"
        sbox.mkdir()
        P = "01ARZ3NDEKTSV4RRFFQ69G5F"

        def put(suffix: str, kind: str, who: str, date: str | None, extra: str = "") -> None:
            mid_ = P + suffix
            d = f"date: {date}\n" if date else ""
            (sbox / f"{mid_}-x.md").write_text(
                f"---\nid: {mid_}\ntype: {kind}\nfrom: {who}\nto: all\n{d}subject: s{suffix}\n"
                f"{extra}---\n\nbody\n")

        put("A1", "ASK", "alice", "2026-06-01T12:00:00Z")
        put("A2", "ASK", "carol", "2026-06-09T09:00:00Z")
        put("R1", "ANSWER", "bob", "2026-06-09T13:00:00Z", extra=f"reply_to: {P}A2\n")
        put("A3", "ASK", "alice", "2026-06-08T12:00:00Z")
        put("N1", "NOTICE", "dave", "2026-06-10T11:00:00Z")
        put("N2", "NOTICE", "dave", "2026-05-01T00:00:00Z")
        put("C1", "CLAIM", "bob", "2026-06-10T08:00:00Z", extra="expires: 2026-06-11T12:00:00Z\n")
        put("C2", "CLAIM", "bob", "2026-06-08T08:00:00Z", extra="expires: 2026-06-09T00:00:00Z\n")
        put("C3", "CLAIM", "bob", "2026-06-08T09:00:00Z", extra="expires: tomorrow\n")
        put("C4", "CLAIM", "bob", "2026-06-10T09:00:00Z", extra="expires: 2026-06-12T00:00:00Z\n")
        put("C5", "CLAIM", "bob", "2026-06-10T10:00:00Z",
            extra=f"expires: 2026-06-12T00:00:00Z\nsupersedes: {P}C4\n")
        put("E1", "NOTICE", "erin", "2026-05-01T00:00:00Z")
        put("V1", "NOTICE", "frank", None)
        (sbox / "bad.md").write_text("no frontmatter at all\n")
        tmpf = sbox / ".x.md.1.tmp"
        tmpf.write_text("partial")
        os.utime(tmpf, (0, 0))
        NOW = "2026-06-10T12:00:00Z"

        import hashlib  # noqa: PLC0415

        def snap() -> dict[str, tuple[int, int, str]]:
            return {f.name: (f.stat().st_mtime_ns, f.stat().st_size,
                             hashlib.sha256(f.read_bytes()).hexdigest())
                    for f in sorted(sbox.iterdir())}

        before = snap()
        j1 = run(sbox, "status", "--json", "--now", NOW)
        j2 = run(sbox, "status", "--json", "--now", NOW)
        check("status --json exits 0", j1.returncode == 0, j1.stderr)
        check("status is deterministic for a fixed --now", j1.stdout == j2.stdout)
        check("status wrote nothing (files, mtimes, hashes unchanged)", snap() == before)
        st = json.loads(j1.stdout) if j1.returncode == 0 else {}
        check("documented key set is exactly stable",
              sorted(st) == ["agents", "asks", "claims", "mailbox", "messages", "now", "quarantined",
                             "schema", "stale_tmp", "stalled_agents", "stalled_hours"]
              and sorted(st.get("messages", {})) == ["by_type", "live_by_type", "total"]
              and sorted(st.get("asks", {})) == ["oldest_open_age_seconds", "oldest_open_id", "open"]
              and sorted(st.get("claims", {})) == ["expired", "held", "malformed_expiry", "superseded"],
              j1.stdout[:300])
        check("schema is 1, now echoes --now", st.get("schema") == 1 and st.get("now") == NOW)
        check("messages: 13 admitted; by type",
              st["messages"]["total"] == 13
              and st["messages"]["by_type"] == {"ANSWER": 1, "ASK": 3, "CLAIM": 5, "DISPUTE": 0, "NOTICE": 4},
              str(st["messages"]))
        check("live by type (open ASKs, fresh ANSWER/NOTICE, unexpired CLAIMs)",
              st["messages"]["live_by_type"] == {"ANSWER": 1, "ASK": 2, "CLAIM": 3, "DISPUTE": 0, "NOTICE": 2},
              str(st["messages"]["live_by_type"]))
        check("open ASKs: 2, oldest is A1 at exactly 9 days",
              st["asks"] == {"open": 2, "oldest_open_id": P + "A1", "oldest_open_age_seconds": 777600},
              str(st["asks"]))
        check("claims: held 2 / expired 1 / malformed 1 / superseded 1",
              st["claims"] == {"held": 2, "expired": 1, "malformed_expiry": 1, "superseded": 1},
              str(st["claims"]))
        check("quarantined 1, stale_tmp 1", st["quarantined"] == 1 and st["stale_tmp"] == 1)
        by_agent = {a["agent"]: a for a in st["agents"]}
        check("agents sorted; per-agent last-message age",
              [a["agent"] for a in st["agents"]] == ["alice", "bob", "carol", "dave", "erin", "frank"]
              and by_agent["bob"]["age_seconds"] == 7200
              and by_agent["dave"]["last_message_at"] == "2026-06-10T11:00:00Z",
              str(st["agents"]))
        check("stalled agent flagged (erin); undated agent is unknown, not stalled",
              st["stalled_agents"] == ["erin"] and by_agent["frank"]["state"] == "unknown"
              and by_agent["frank"]["age_seconds"] is None and by_agent["alice"]["state"] == "active",
              str(st["stalled_agents"]))
        tight = json.loads(run(sbox, "status", "--json", "--now", NOW, "--stalled-hours", "24").stdout)
        check("--stalled-hours is honoured (alice at 48h stalls under 24h)",
              "alice" in tight["stalled_agents"] and tight["stalled_hours"] == 24.0, str(tight["stalled_agents"]))
        txt = run(sbox, "status", "--now", NOW)
        check("text mode prints the headline numbers and the STALLED line",
              txt.returncode == 0 and "STALLED: erin" in txt.stdout and "open=2" in txt.stdout
              and "held=2" in txt.stdout, txt.stdout)
        check("bad --now refused", run(sbox, "status", "--now", "garbage").returncode == 2)
        check("missing mailbox is a visible miss",
              run(Path(td) / "nope", "status").returncode == 2)
        send(sbox, "zed", subj="new")
        check("control: the snapshot instrument does see a real write", snap() != before)

        print("publish makes the directory entry durable")
        import importlib.util as ilu  # noqa: PLC0415
        import stat as stat_  # noqa: PLC0415
        spec2 = ilu.spec_from_file_location("am_dur", TOOL)
        am2 = ilu.module_from_spec(spec2)  # type: ignore[arg-type]
        spec2.loader.exec_module(am2)  # type: ignore[union-attr]
        kinds: list[str] = []
        real_fsync = os.fsync

        def spy(fd: int) -> None:
            kinds.append("dir" if stat_.S_ISDIR(os.fstat(fd).st_mode) else "file")
            real_fsync(fd)

        dbox = Path(td) / "dur"
        dbox.mkdir()
        os.fsync = spy  # type: ignore[assignment]
        try:
            am2._publish(dbox / "m.md", "x")
        finally:
            os.fsync = real_fsync
        check("publish fsyncs the file, then the directory",
              kinds == ["file", "dir"] and (dbox / "m.md").read_text() == "x", str(kinds))

        print("python version guard")
        old = subprocess.run(
            [sys.executable, "-I", "-c",
             "import sys, runpy; sys.version_info = (3, 9, 18, 'final', 0); "
             f"sys.argv = ['agent_mail.py', '--version']; runpy.run_path({TOOL!r}, run_name='__main__')"],
            capture_output=True, text=True)
        check("python 3.9 -> exit 2, one readable line, no traceback",
              old.returncode == 2 and len(old.stderr.strip().splitlines()) == 1
              and "requires Python 3.10" in old.stderr and "Traceback" not in old.stderr,
              f"rc={old.returncode} {old.stderr!r}")
        imp = subprocess.run(
            [sys.executable, "-I", "-c",
             "import sys; sys.version_info = (3, 8, 0, 'final', 0); sys.path.insert(0, "
             f"{str(HERE)!r}); import agent_mail"], capture_output=True, text=True)
        check("importing under an old python raises ImportError (hook swallows it)",
              imp.returncode == 1 and "ImportError: agent_mail requires Python 3.10" in imp.stderr,
              imp.stderr[-200:])
        cur = subprocess.run([sys.executable, "-I", TOOL, "--version"], capture_output=True, text=True)
        check("control: the current interpreter passes the guard",
              cur.returncode == 0 and "agent-postbox" in cur.stdout, cur.stderr)

        print("recovery after an interrupted write (writer SIGKILLed at each dangerous point)")
        HOOK = str(HERE / "hooks" / "agent_mail_check.py")

        def killed_send(box: Path, patch: str, *argv: str) -> tuple[bool, int | None, str]:
            """Run the real send path in a child whose `patch` makes one chokepoint
            write a ready-file and block (signal.pause); SIGKILL it there. Returns
            (reached the chokepoint, child returncode, child stderr). The parent
            polls in bounded 0.05 s steps; it never sleeps a fixed time."""
            ready = box.with_name(box.name + ".ready")
            code = (
                "import os, signal, sys\n"
                f"sys.path.insert(0, {str(HERE)!r})\n"
                "import agent_mail\n"
                "def block(*_a, **_k):\n"
                f"    open({str(ready)!r}, 'w').close()\n"
                "    signal.pause()\n"
                f"{patch}\n"
                f"sys.exit(agent_mail.main({list(argv)!r}))\n")
            proc = subprocess.Popen([sys.executable, "-I", "-c", code],
                                    env=dict(os.environ, AGENT_MAIL_DIR=str(box)),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(400):  # 20 s hard cap
                if ready.exists() or proc.poll() is not None:
                    break
                time.sleep(0.05)
            proc.kill()
            _, err = proc.communicate()
            return ready.exists(), proc.returncode, err

        def hook(box: Path, me: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([sys.executable, "-I", HOOK], capture_output=True, text=True,
                                  env=dict(os.environ, AGENT_MAIL_DIR=str(box), AGENT_MAIL_IDENTITY=me))

        def rows(box: Path, needle: str) -> int:
            return sum(1 for ln in run(box, "list").stdout.splitlines() if needle in ln)

        def hidden(box: Path) -> list[str]:
            return sorted(p.name for p in box.iterdir() if p.name.startswith("."))

        def age(path: Path, seconds: int) -> None:
            t = time.time() - seconds
            os.utime(path, (t, t))

        NOTE = ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                "--subject", "cut", "--body", "whole body"]

        print("1. killed after the temp file is written, before os.link")
        r1 = Path(td) / "rec1"
        r1.mkdir()
        reached, rc, err = killed_send(r1, "os.link = block", *NOTE)
        check("writer reached os.link and was SIGKILLed there", reached and rc == -9,
              f"rc={rc} {err[-300:]}")
        tmps = list(r1.glob(".*.tmp"))
        check("one hidden .tmp left, no *.md published",
              len(tmps) == 1 and tmps[0].name.endswith(".tmp") and not list(r1.glob("*.md")),
              str(sorted(p.name for p in r1.iterdir())))
        dead = next((x.split(": ", 1)[1] for x in tmps[0].read_text().splitlines()
                     if x.startswith("id: ")), "")
        check("the temp already holds the complete message (id + body)",
              len(dead) == 26 and "whole body" in tmps[0].read_text(), tmps[0].read_text()[:200])
        lst = run(r1, "list")
        check("list: header only, no REJECT, exit 0",
              lst.returncode == 0 and lst.stderr == "" and lst.stdout.startswith("# mailbox:")
              and len(lst.stdout.splitlines()) == 1, lst.stdout + lst.stderr)
        sh = run(r1, "show", dead)
        check("show <dead id>: 'no message', exit 1, no traceback",
              sh.returncode == 1 and f"no message with id {dead}" in sh.stderr
              and "Traceback" not in sh.stderr, sh.stderr)
        hk = hook(r1, "bob")
        check("hook: silent, exit 0", hk.returncode == 0 and hk.stdout == "" and hk.stderr == "",
              hk.stdout + hk.stderr)
        sj = run(r1, "status", "--json")
        st1 = json.loads(sj.stdout) if sj.returncode == 0 else {}
        check("status --json: 0 messages, 0 quarantined, fresh tmp not yet stale",
              sj.returncode == 0 and st1.get("messages", {}).get("total") == 0
              and st1.get("quarantined") == 0 and st1.get("stale_tmp") == 0, sj.stdout[:300] + sj.stderr)
        d = run(r1, "doctor").stdout
        check("doctor: messages 0, fresh tmp not yet counted", "messages: 0" in d and "stale_tmp: 0" in d, d)
        age(tmps[0], 601)
        d = run(r1, "doctor").stdout
        check("doctor counts and names the tmp once it is older than 600 s",
              "stale_tmp: 1" in d and f"  - {tmps[0].name}" in d, d)
        check("status --json stale_tmp follows",
              json.loads(run(r1, "status", "--json").stdout)["stale_tmp"] == 1)
        again = run(r1, *NOTE)
        check("the same send retried succeeds", again.returncode == 0 and "wrote " in again.stdout,
              again.stderr)
        check("retried message listed exactly once; stale tmp is not a second copy",
              rows(r1, "alice -> bob  cut") == 1 and len(list(r1.glob("*.md"))) == 1
              and tmps[0].exists(), run(r1, "list").stdout)
        hk = hook(r1, "bob")
        check("control: hook now shows the one message",
              "Agent mail - 1 live message(s) addressed to 'bob'" in hk.stdout
              and f"id: {mid(again)}" in hk.stdout, hk.stdout)

        print("2. killed after os.link, before the temp is unlinked")
        r2 = Path(td) / "rec2"
        r2.mkdir()
        reached, rc, err = killed_send(
            r2, "_link = os.link\ndef late(*a):\n    _link(*a)\n    block()\nos.link = late", *NOTE)
        check("writer linked the final name and was SIGKILLed before cleanup", reached and rc == -9,
              f"rc={rc} {err[-300:]}")
        mds, tmps = list(r2.glob("*.md")), list(r2.glob(".*.tmp"))
        check("one *.md and one stray .tmp with identical bytes",
              len(mds) == 1 and len(tmps) == 1 and mds[0].read_bytes() == tmps[0].read_bytes(),
              str(sorted(p.name for p in r2.iterdir())))
        lst = run(r2, "list")
        check("list: exactly one row, no REJECT, exit 0",
              lst.returncode == 0 and lst.stderr == "" and rows(r2, "alice -> bob  cut") == 1,
              lst.stdout + lst.stderr)
        linked = mds[0].stem.split("-", 1)[0] if mds else ""
        sh = run(r2, "show", linked)
        check("show prints the complete message", sh.returncode == 0 and "subject: cut" in sh.stdout
              and sh.stdout.rstrip().endswith("whole body"), sh.stdout + sh.stderr)
        check("hook delivers it once", hook(r2, "bob").stdout.count("NOTICE from alice") == 1)
        d = run(r2, "doctor").stdout
        check("doctor: messages 1, fresh tmp not yet counted", "messages: 1" in d and "stale_tmp: 0" in d, d)
        age(tmps[0], 601)
        d = run(r2, "doctor").stdout
        check("doctor counts the orphaned tmp once stale",
              "stale_tmp: 1" in d and f"  - {tmps[0].name}" in d, d)
        check("control: a later send is not blocked by the stray tmp",
              run(r2, *NOTE).returncode == 0 and len(list(r2.glob("*.md"))) == 2)

        print("3. killed with --key after the idempotency marker is reserved, before publish")
        r3 = Path(td) / "rec3"
        r3.mkdir()
        KEYED = [*NOTE, "--key", "K"]
        reached, rc, err = killed_send(r3, "agent_mail._publish = block", *KEYED)
        check("writer reserved the key and was SIGKILLed before publish", reached and rc == -9,
              f"rc={rc} {err[-300:]}")
        marks = list(r3.glob(".idem.*"))
        check("orphan marker on disk, nothing published, nothing listed",
              len(marks) == 1 and not list(r3.glob("*.md")) and rows(r3, "alice -> bob") == 0,
              str(hidden(r3)))
        orphan_text = marks[0].read_text() if marks else ""
        # agent_mail.py _idem_claim: `if old_content != content: return "CONFLICT"` is checked
        # before the orphan wait/takeover, so a dead writer's key still pins its content.
        diff = run(r3, *NOTE[:-1], "DIFFERENT", "--key", "K")
        check("same key, different content: CONFLICT (exit 2) even though the original never landed",
              diff.returncode == 2 and "idempotency key 'K' was already used with different content"
              in diff.stderr and not list(r3.glob("*.md")), diff.stdout + diff.stderr)
        check("the refused retry left the orphan marker untouched",
              marks[0].exists() and marks[0].read_text() == orphan_text, str(hidden(r3)))
        same = run(r3, *KEYED)  # ~5 s: _idem_claim waits 100 x 0.05 s for the dead writer first
        check("same key, same content: takes over the orphan and writes (exit 0)",
              same.returncode == 0 and "wrote " in same.stdout and "duplicate of" not in same.stdout,
              same.stdout + same.stderr)
        check("listed exactly once under a fresh id; the dead writer's id never appears",
              rows(r3, "alice -> bob  cut") == 1 and len(list(r3.glob("*.md"))) == 1
              and same.returncode == 0 and mid(same) != orphan_text.split("\n")[0]
              and not list(r3.glob(orphan_text.split("\n")[0] + "*")), run(r3, "list").stdout)
        check("marker now names the published message; takeover mutex released",
              hidden(r3) == [marks[0].name]
              and marks[0].read_text().split("\n")[0] == (mid(same) if same.returncode == 0 else "?"),
              str(hidden(r3)))
        dup = run(r3, *KEYED)
        check("control: a further retry is now an ordinary duplicate of the recovered id",
              dup.returncode == 0 and same.returncode == 0 and f"duplicate of {mid(same)}" in dup.stdout
              and len(list(r3.glob("*.md"))) == 1, dup.stdout)

        print("4. killed holding a CLAIM --scope lock, before publish")
        r4 = Path(td) / "rec4"
        r4.mkdir()
        CLM = ["--type", "CLAIM", "--to", "all", "--subject", "s", "--body", "b",
               "--expires", soon(1), "--scope", "file:s.py"]
        reached, rc, err = killed_send(r4, "agent_mail._publish = block",
                                       "send", "--from", "alice", *CLM)
        check("writer took the scope lock and was SIGKILLed before publish", reached and rc == -9,
              f"rc={rc} {err[-300:]}")
        locks = list(r4.glob(".scope.*.lock"))
        check("lock left behind, no claim on disk", len(locks) == 1 and not list(r4.glob("*.md")),
              str(hidden(r4)))
        rival = run(r4, "send", "--from", "bob", *CLM)
        check("rival within 60 s: refused, exit 3, 'being claimed right now; retry'",
              rival.returncode == 3 and "scope 'file:s.py' is being claimed right now; retry"
              in rival.stderr and not list(r4.glob("*.md")), rival.stdout + rival.stderr)
        check("the refused rival did not steal or drop the dead holder's lock",
              locks[0].exists(), str(hidden(r4)))
        age(locks[0], 61)
        rival2 = run(r4, "send", "--from", "bob", *CLM)
        check("rival after the lock is 61 s old: breaks it and wins (exit 0)",
              rival2.returncode == 0 and "wrote " in rival2.stdout, rival2.stdout + rival2.stderr)
        check("exactly one held claim on disk, by the rival; lock released",
              rows(r4, "[held      ] bob -> all  s") == 1 and len(list(r4.glob("*.md"))) == 1
              and not list(r4.glob(".scope.*.lock")), run(r4, "list").stdout + str(hidden(r4)))
        check("control: the dead holder's identity gets an ordinary 'held by bob' refusal, not a lock",
              "is already held by bob" in run(r4, "send", "--from", "alice", *CLM).stderr)

        print("5. truncated final file (non-atomic copy by a foreign tool)")
        r5 = Path(td) / "rec5"
        r5.mkdir()
        whole = run(r5, *NOTE)
        check("control: the complete file is admitted", whole.returncode == 0
              and rows(r5, "alice -> bob  cut") == 1, whole.stderr)
        text = next(r5.glob("*.md")).read_text()
        half = text[:len(text) // 2]
        check("setup: the cut lands inside the frontmatter", half.startswith("---\nid: ")
              and "\n---\n" not in half[3:], repr(half))
        bad = r5 / "01ARZ3NDEKTSV4RRFFQ69G5HLF-half.md"
        bad.write_text(half)
        lst = run(r5, "list")
        check("list: the half file is loudly REJECTed, exit 2, no traceback",
              lst.returncode == 2 and "REJECT 01ARZ3NDEKTSV4RRFFQ69G5HLF-half.md: missing id" in lst.stderr
              and "Traceback" not in lst.stderr, lst.stderr)
        check("the complete message is still listed once; the half one never",
              lst.stdout.count("alice -> bob  cut") == 1 and "half" not in lst.stdout, lst.stdout)
        sh = run(r5, "show", "01ARZ3NDEKTSV4RRFFQ69G5HLF")
        check("show of the half file: 'no message', exit 1",
              sh.returncode == 1 and "no message with id" in sh.stderr and "Traceback" not in sh.stderr,
              sh.stderr)
        hk = hook(r5, "bob")
        check("hook delivers only the complete message",
              hk.stdout.count("NOTICE from alice") == 1 and f"id: {mid(whole)}" in hk.stdout, hk.stdout)
        st5 = json.loads(run(r5, "status", "--json").stdout)
        check("status: 1 message, 1 quarantined", st5["messages"]["total"] == 1 and st5["quarantined"] == 1,
              str(st5))
        d = run(r5, "doctor").stdout
        check("doctor lists the reject and keeps the evidence on disk",
              "rejects: 1" in d and "01ARZ3NDEKTSV4RRFFQ69G5HLF-half.md: missing id" in d and bad.exists(), d)

    if FAILS:
        print("\nFAILED:")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
