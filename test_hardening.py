#!/usr/bin/env python3
"""Deterministic tests for claim/lease validation, idempotent send, and quarantine.

No sleeps: expiry boundaries are fixed ISO strings relative to a far-future/past
constant, and races are forced by running real subprocesses against one mailbox.
Each negative check has a positive control in the same mailbox.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
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

    if FAILS:
        print("\nFAILED:")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
