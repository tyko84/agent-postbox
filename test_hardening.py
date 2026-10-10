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


def run(box: Path, *argv: str, env_extra: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, AGENT_MAIL_DIR=str(box))
    env.pop("AGENT_MAIL_IDEM_WAIT", None)  # the suite pins the default; never inherit one
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-I", TOOL, *argv],
                          capture_output=True, text=True, env=env)


def soon(hours: float) -> str:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def send(box: Path, who: str, *extra: str, kind: str = "NOTICE", subj: str = "s",
         body: str = "b") -> subprocess.CompletedProcess[str]:
    return run(box, "send", "--type", kind, "--from", who, "--to", "all",
               "--subject", subj, "--body", body, *extra)


def mid_of(stdout: str) -> str:
    return next((x.split(": ", 1)[1] for x in stdout.splitlines() if x.startswith("id: ")), "?")


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
        check("exactly one of the 8 wrote; the other 7 answered 'duplicate of' (exit 0)",
              sum("wrote " in r.stdout for r in rr) == 1
              and sum("duplicate of" in r.stdout for r in rr) == 7
              and all(r.returncode == 0 for r in rr), str([(r.returncode, r.stderr) for r in rr]))
        orphan = box / ".idem.deadbeefdeadbeefdeadbeef"
        check("control: idem markers are hidden from readers", all(
            m.name.startswith("0") for m in box.glob("*.md")) and not orphan.exists())

        print("falsifier regressions")
        import hashlib
        h = hashlib.sha256(b"a\0b\0k1").hexdigest()[:24]
        chash = hashlib.sha256(b"NOTICE\0s\0body").hexdigest()
        (box / f".idem.{h}").write_text(f"01ARZ3NDEKTSV4RRFFQ69GZZZZ\n{chash}\n")
        t_legacy = time.monotonic()
        with ThreadPoolExecutor(8) as pool:
            cr = list(pool.map(lambda _: run(
                box, "send", "--type", "NOTICE", "--from", "a", "--to", "b", "--subject", "s",
                "--body", "body", "--key", "k1"), range(8)))
        n = sum(1 for m in box.glob("*.md") if "idem: k1" in m.read_text())
        t_legacy = time.monotonic() - t_legacy
        check("orphaned marker + 8 simultaneous retries -> one message", n == 1 and
              all(r.returncode == 0 for r in cr), f"files={n}")
        check("default key wait unchanged: an old-format (two-line) orphan is taken over "
              "after 5 s, not before", 5.0 <= t_legacy < 30.0, f"{t_legacy:.2f}s")
        check("the takeover left a locked-format marker and no takeover mutex",
              (box / f".idem.{h}").read_text().split("\n")[2] == "flock"
              and not (box / f".idem.{h}.takeover").exists(), (box / f".idem.{h}").read_text())
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
        V1_KEYS = ["agents", "asks", "claims", "mailbox", "messages", "now", "quarantined",
                   "schema", "stale_tmp", "stalled_agents", "stalled_hours"]
        V33_KEYS = ["dangling_replies", "foreign_supersedes", "idem_markers", "problems",
                    "scope_locks", "takeover_mutexes"]  # added by PROTOCOL.md section 33
        check("documented key set is exactly stable",
              sorted(st) == sorted(V1_KEYS + V33_KEYS)
              and sorted(st.get("scope_locks", {})) == ["held", "in_flight", "orphaned", "total"]
              and sorted(st.get("idem_markers", {})) == ["held", "in_flight", "orphaned", "resolved", "total"]
              and sorted(st.get("takeover_mutexes", {})) == ["stale", "total"]
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
        check("problems lists exactly the four conditions planted in this box, in the fixed order",
              st["problems"] == [{"code": "QUARANTINED", "count": 1}, {"code": "STALE_TMP", "count": 1},
                                 {"code": "MALFORMED_CLAIM_EXPIRY", "count": 1},
                                 {"code": "STALLED_AGENT", "count": 1}], str(st["problems"]))
        check("control: no lock, marker, foreign supersede or dangling reply is invented",
              st["scope_locks"]["total"] == 0 and st["idem_markers"]["total"] == 0
              and st["takeover_mutexes"]["total"] == 0 and st["foreign_supersedes"] == 0
              and st["dangling_replies"] == 0, str(st))
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

        def blocked_send(box: Path, patch: str, *argv: str, tag: str = "",
                         env_extra: dict[str, str] | None = None
                         ) -> tuple[subprocess.Popen[str], Path]:
            """Start the real send path in a child whose `patch` makes one chokepoint
            write a ready-file and block (signal.pause), and return once the child
            is parked there (or has exited). The parent polls in bounded 0.05 s
            steps; it never sleeps a fixed time."""
            ready = box.with_name(box.name + tag + ".ready")
            go = box.with_name(box.name + tag + ".go")
            ready.unlink(missing_ok=True)  # a second child in the same box must park afresh
            go.unlink(missing_ok=True)
            code = (
                "import os, signal, sys, time\n"
                f"sys.path.insert(0, {str(HERE)!r})\n"
                "import agent_mail\n"
                "def block(*_a, **_k):\n"
                f"    open({str(ready)!r}, 'w').close()\n"
                "    signal.pause()\n"
                "def gate(*_a, **_k):\n"  # park, but resumable: returns once <box><tag>.go exists
                f"    open({str(ready)!r}, 'w').close()\n"
                f"    while not os.path.exists({str(go)!r}):\n"
                "        time.sleep(0.05)\n"
                f"{patch}\n"
                f"sys.exit(agent_mail.main({list(argv)!r}))\n")
            env = dict(os.environ, AGENT_MAIL_DIR=str(box))
            env.pop("AGENT_MAIL_IDEM_WAIT", None)
            env.update(env_extra or {})
            proc = subprocess.Popen([sys.executable, "-I", "-c", code], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(400):  # 20 s hard cap
                if ready.exists() or proc.poll() is not None:
                    break
                time.sleep(0.05)
            return proc, ready

        def killed_send(box: Path, patch: str, *argv: str) -> tuple[bool, int | None, str]:
            """blocked_send, then SIGKILL the parked child. Returns (reached the
            chokepoint, child returncode, child stderr)."""
            proc, ready = blocked_send(box, patch, *argv)
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
        check("the dead writer's marker is in the locked format (PROTOCOL.md section 31)",
              orphan_text.split("\n")[2:3] == ["flock"], repr(orphan_text))
        t3 = time.monotonic()
        same = run(r3, *KEYED)  # default key wait: the dead writer's lock is gone, so no wait
        t3 = time.monotonic() - t3
        check("same key, same content: takes over the orphan and writes (exit 0)",
              same.returncode == 0 and "wrote " in same.stdout and "duplicate of" not in same.stdout,
              same.stdout + same.stderr)
        check("the takeover of a dead writer's key did not wait (well under the 5 s default)",
              t3 < 3.0, f"{t3:.2f}s")
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

        print("4. holding a CLAIM --scope lock: alive it blocks rivals, dead it is reclaimed")
        # PROTOCOL.md section 30: the lock is a kernel-held flock, never broken by age.
        r4 = Path(td) / "rec4"
        r4.mkdir()
        CLM = ["--type", "CLAIM", "--to", "all", "--subject", "s", "--body", "b",
               "--expires", soon(1), "--scope", "file:s.py"]
        holder, ready4 = blocked_send(r4, "agent_mail._publish = block",
                                      "send", "--from", "alice", *CLM)
        check("writer took the scope lock and is parked before publish",
              ready4.exists() and holder.poll() is None, str(hidden(r4)))
        locks = list(r4.glob(".scope.*.lock"))
        check("lock on disk, no claim on disk", len(locks) == 1 and not list(r4.glob("*.md")),
              str(hidden(r4)))
        rival = run(r4, "send", "--from", "bob", *CLM)
        check("live holder: rival refused, exit 3, 'being claimed right now; retry'",
              rival.returncode == 3 and "scope 'file:s.py' is being claimed right now; retry"
              in rival.stderr and not list(r4.glob("*.md")), rival.stdout + rival.stderr)
        age(locks[0], 120)
        rival = run(r4, "send", "--from", "bob", *CLM)
        check("live holder, lock file 120 s old: still refused (age means nothing)",
              rival.returncode == 3 and "being claimed right now" in rival.stderr
              and locks[0].exists(), rival.stdout + rival.stderr + str(hidden(r4)))
        holder.kill()
        _, err4 = holder.communicate()
        check("holder SIGKILLed; its lock file is left behind", holder.returncode == -9
              and locks[0].exists() and not list(r4.glob("*.md")), err4[-300:] + str(hidden(r4)))
        rival2 = run(r4, "send", "--from", "bob", *CLM)
        check("dead holder: rival reclaims the lock at once and wins (exit 0, no 60 s wait)",
              rival2.returncode == 0 and "wrote " in rival2.stdout, rival2.stdout + rival2.stderr)
        check("exactly one held claim on disk, by the rival; no lock file left",
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

        print("6. keyed send: a live original is waited for and never taken over (PROTOCOL.md section 31)")
        r6 = Path(td) / "rec6"
        r6.mkdir()
        GATED = "_p = agent_mail._publish\ndef late(*a):\n    gate()\n    _p(*a)\nagent_mail._publish = late"
        orig, ready6 = blocked_send(r6, GATED, *KEYED)
        marks = list(r6.glob(".idem.*"))
        check("original reserved the key and is parked, alive, before publish",
              ready6.exists() and orig.poll() is None and len(marks) == 1
              and not list(r6.glob("*.md")), str(hidden(r6)))
        live_text = marks[0].read_text() if marks else ""
        t0 = time.monotonic()
        busy = run(r6, *KEYED, "--key-wait", "0")
        check("key wait 0, live original: exit 3 at once, 'being sent ... retry', nothing written",
              busy.returncode == 3 and "idempotency key 'K' is being sent by another process right now"
              in busy.stderr and busy.stdout == "" and time.monotonic() - t0 < 3.0
              and not list(r6.glob("*.md")), busy.stdout + busy.stderr)
        t0 = time.monotonic()
        busy = run(r6, *KEYED, env_extra={"AGENT_MAIL_IDEM_WAIT": "0.5"})
        took = time.monotonic() - t0
        check("AGENT_MAIL_IDEM_WAIT=0.5, live original: waits 0.5 s, then exit 3; no takeover",
              busy.returncode == 3 and 0.5 <= took < 4.0 and not list(r6.glob("*.md")),
              f"{took:.2f}s {busy.stderr}")
        check("the live original's marker was never touched",
              hidden(r6) == [marks[0].name] and marks[0].read_text() == live_text, str(hidden(r6)))
        diff = run(r6, *NOTE[:-1], "DIFFERENT", "--key", "K", "--key-wait", "0")
        check("different content against a live original: CONFLICT (exit 2), not 'retry'",
              diff.returncode == 2 and "different content" in diff.stderr, diff.stderr)
        # a retry that is still waiting when the original publishes becomes a duplicate
        NAP = ("_s = time.sleep\ndef nap(x):\n"
               f"    open({str(r6.with_name('rec6w.ready'))!r}, 'w').close()\n"
               "    _s(x)\nagent_mail.time.sleep = nap")
        waiter, wready = blocked_send(r6, NAP, *KEYED, "--key-wait", "60", tag="w")
        check("a second retry is parked in the key wait", wready.exists() and waiter.poll() is None,
              str(waiter.poll()))
        r6.with_name("rec6.go").touch()  # let the original publish
        out_o, err_o = orig.communicate(timeout=60)
        out_w, err_w = waiter.communicate(timeout=60)
        check("the original publishes (exit 0)", orig.returncode == 0 and "wrote " in out_o, err_o)
        check("the waiting retry answers 'duplicate of' the original's id (exit 0), long before its 60 s",
              waiter.returncode == 0 and f"duplicate of {mid_of(out_o)}" in out_w, out_w + err_w)
        check("control: exactly one message for the key; only the marker is hidden",
              len(list(r6.glob("*.md"))) == 1 and rows(r6, "alice -> bob  cut") == 1
              and hidden(r6) == [marks[0].name], str(sorted(p.name for p in r6.iterdir())))

        print("7. keyed send: cancellation (SIGTERM / SIGINT) releases only what the sender owns")
        import signal as signal_  # noqa: PLC0415
        r7 = Path(td) / "rec7"
        r7.mkdir()
        orig, _ = blocked_send(r7, "agent_mail._publish = block", *KEYED)
        marks = list(r7.glob(".idem.*"))
        live_text = marks[0].read_text() if marks else ""
        NAP7 = NAP.replace("rec6w.ready", "rec7w.ready")
        waiter, wready = blocked_send(r7, NAP7, *KEYED, "--key-wait", "60", tag="w")
        check("setup: a live original holds the key and a retry is parked in the key wait",
              len(marks) == 1 and orig.poll() is None and wready.exists() and waiter.poll() is None,
              str(hidden(r7)))
        waiter.send_signal(signal_.SIGTERM)
        _, err_w = waiter.communicate(timeout=60)
        check("SIGTERM to the waiting retry: exit 143, one line, no traceback",
              waiter.returncode == 143 and err_w.strip() == "interrupted (SIGTERM); nothing written",
              f"rc={waiter.returncode} {err_w!r}")
        check("it owned no marker: the original's marker is untouched, no temp, no message",
              hidden(r7) == [marks[0].name] and marks[0].read_text() == live_text
              and not list(r7.glob("*.md")), str(sorted(p.name for p in r7.iterdir())))
        orig.send_signal(signal_.SIGTERM)
        _, err_o = orig.communicate(timeout=60)
        check("SIGTERM to the original (parked before publish): exit 143, 'nothing written'",
              orig.returncode == 143 and err_o.strip() == "interrupted (SIGTERM); nothing written",
              f"rc={orig.returncode} {err_o!r}")
        check("it gave up, so it released its own marker: mailbox is empty again",
              sorted(p.name for p in r7.iterdir()) == [], str(sorted(p.name for p in r7.iterdir())))
        orig, _ = blocked_send(r7, "agent_mail._publish = block", *KEYED)
        orig.send_signal(signal_.SIGINT)
        _, err_o = orig.communicate(timeout=60)
        check("SIGINT behaves the same: exit 130, one line, marker released",
              orig.returncode == 130 and err_o.strip() == "interrupted (SIGINT); nothing written"
              and sorted(p.name for p in r7.iterdir()) == [], f"rc={orig.returncode} {err_o!r}")
        # interrupted AFTER the message was linked: the marker must be kept
        orig, _ = blocked_send(
            r7, "_link = os.link\ndef late(*a):\n    _link(*a)\n    if str(a[1]).endswith('.md'):\n"
                "        block()\nos.link = late", *KEYED)
        orig.send_signal(signal_.SIGTERM)
        _, err_o = orig.communicate(timeout=60)
        mds = list(r7.glob("*.md"))
        check("SIGTERM after the link: exit 143, says 'after publishing' with the id",
              orig.returncode == 143 and len(mds) == 1
              and err_o.strip() == f"interrupted (SIGTERM) after publishing; id: {mds[0].name[:26]}",
              f"rc={orig.returncode} {err_o!r}")
        marks = list(r7.glob(".idem.*"))
        check("the marker is kept (it guards a real message) and the temp file is gone",
              len(marks) == 1 and hidden(r7) == [marks[0].name]
              and marks[0].read_text().split("\n")[0] == mds[0].name[:26], str(hidden(r7)))
        dup = run(r7, *KEYED)
        check("control: a retry after that is a duplicate, not a second message",
              dup.returncode == 0 and "duplicate of" in dup.stdout and len(list(r7.glob("*.md"))) == 1,
              dup.stdout + dup.stderr)
        # a sender that gives up never removes a marker that is no longer its own
        r7b = Path(td) / "rec7b"
        r7b.mkdir()
        orig, _ = blocked_send(r7b, "agent_mail._publish = block", *KEYED)
        mk = next(r7b.glob(".idem.*"))
        foreign = "01ARZ3NDEKTSV4RRFFQ69GFRGN\n" + mk.read_text().split("\n")[1] + "\n"
        swap = r7b / ".swap"
        swap.write_text(foreign)
        os.replace(swap, mk)  # what a pre-section-31 sender's takeover amounts to: a new inode
        orig.send_signal(signal_.SIGTERM)
        orig.communicate(timeout=60)
        check("giving up leaves a marker that another sender put in its place",
              orig.returncode == 143 and mk.exists() and mk.read_text() == foreign, str(hidden(r7b)))

        print("8. keyed send: the retry of a retry, bounds, and a filesystem without flock")
        r8 = Path(td) / "rec8"
        r8.mkdir()
        killed_send(r8, "agent_mail._publish = block", *KEYED)
        reached, rc, _ = killed_send(r8, "agent_mail._publish = block", *KEYED)
        check("a retry that took the orphan over and was itself SIGKILLed before publish",
              reached and rc == -9 and len(list(r8.glob(".idem.*"))) == 1 and not list(r8.glob("*.md")),
              str(hidden(r8)))
        t0 = time.monotonic()
        third = run(r8, *KEYED)
        check("the next retry takes over again at once and publishes exactly one message",
              third.returncode == 0 and "wrote " in third.stdout and time.monotonic() - t0 < 3.0
              and len(list(r8.glob("*.md"))) == 1, third.stdout + third.stderr)
        for bad in ("-1", "60.01", "1e1", "five", "nan"):
            cp = run(r8, *NOTE, "--key", f"bad-{bad}", f"--key-wait={bad}")
            check(f"--key-wait {bad}: refused (exit 2), not clamped; no marker reserved",
                  cp.returncode == 2 and "must be a decimal number of seconds from 0 to 60" in cp.stderr
                  and len(list(r8.glob(".idem.*"))) == 1 and len(list(r8.glob("*.md"))) == 1,
                  cp.stderr + str(hidden(r8)))
        cp = run(r8, *NOTE, "--key", "bad-env", env_extra={"AGENT_MAIL_IDEM_WAIT": "600"})
        check("AGENT_MAIL_IDEM_WAIT=600: refused (exit 2) naming the variable",
              cp.returncode == 2 and "AGENT_MAIL_IDEM_WAIT '600'" in cp.stderr, cp.stderr)
        cp = run(r8, *NOTE, "--key", "ok-env", env_extra={"AGENT_MAIL_IDEM_WAIT": "60"})
        check("control: AGENT_MAIL_IDEM_WAIT=60 (the upper bound) is accepted",
              cp.returncode == 0 and "wrote " in cp.stdout, cp.stderr)
        NOFLOCK = ("import fcntl, errno\ndef refuse(*_a):\n    raise OSError(errno.ENOLCK, 'No locks available')\n"
                   "fcntl.flock = refuse")
        r8f = Path(td) / "rec8f"
        r8f.mkdir()
        nf, _ = blocked_send(r8f, NOFLOCK, *KEYED)
        out_nf, err_nf = nf.communicate(timeout=60)
        mk = next(r8f.glob(".idem.*"), None)
        check("filesystem refusing flock: the keyed send still publishes, with an old-format marker",
              nf.returncode == 0 and "wrote " in out_nf and mk is not None
              and mk.read_text().count("\n") == 2 and hidden(r8f) == [mk.name], out_nf + err_nf)
        nf, _ = blocked_send(r8f, NOFLOCK, *KEYED)
        out_nf, err_nf = nf.communicate(timeout=60)
        check("and its retry is still a duplicate", nf.returncode == 0 and "duplicate of" in out_nf
              and len(list(r8f.glob("*.md"))) == 1, out_nf + err_nf)
        r8g = Path(td) / "rec8g"
        r8g.mkdir()
        killed_send(r8g, "agent_mail._publish = block", *KEYED)  # leaves a locked-format orphan
        t0 = time.monotonic()
        nf, _ = blocked_send(r8g, NOFLOCK, *KEYED, "--key-wait", "0.4")
        out_nf, err_nf = nf.communicate(timeout=60)
        took = time.monotonic() - t0
        check("a locked-format orphan read where flock is refused falls back to the key wait "
              "(taken over after it, not 'retry' for ever)",
              nf.returncode == 0 and "wrote " in out_nf and took >= 0.4
              and len(list(r8g.glob("*.md"))) == 1, f"{took:.2f}s {out_nf} {err_nf}")

        def patched(box: Path, patch: str, *argv: str) -> tuple[int | None, str, str]:
            """Run the real send path to completion in a child with `patch` applied."""
            proc, _ = blocked_send(box, patch, *argv)
            out, err = proc.communicate(timeout=60)
            return proc.returncode, out, err

        def one_line_failure(rc: int | None, out: str, err: str, errno_name: str) -> bool:
            return (rc == 1 and out == "" and len(err.strip().splitlines()) == 1
                    and err.startswith("send failed: cannot write to the mailbox: ")
                    and f"({errno_name}); nothing written" in err and "Traceback" not in err)

        print("9. claims: filesystem errors are one line, exit 1, and leave nothing behind (PROTOCOL.md section 32)")
        r9 = Path(td) / "rec9"
        r9.mkdir()
        CLAIM9 = ["send", "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "c",
                  "--body", "b", "--expires", soon(1), "--scope", "file:9.py", "--key", "k9"]
        ENOSPC = "import errno\ndef full(*_a, **_k):\n    raise OSError(errno.ENOSPC, 'No space left on device')\n"
        rc, out, err = patched(r9, ENOSPC + "os.fsync = full", *CLAIM9)
        check("disk full at fsync (scoped, keyed claim): one line naming ENOSPC, exit 1",
              one_line_failure(rc, out, err, "ENOSPC"), f"rc={rc} {out!r} {err[-300:]!r}")
        check("no claim, temp, key marker or scope lock left", sorted(p.name for p in r9.iterdir()) == [],
              str(sorted(p.name for p in r9.iterdir())))
        rc, out, err = patched(r9, ENOSPC + "os.link = full\nos.replace = full", *CLAIM9)
        check("disk full at link/rename: one line naming ENOSPC, exit 1, nothing left",
              one_line_failure(rc, out, err, "ENOSPC") and sorted(p.name for p in r9.iterdir()) == [],
              f"rc={rc} {err[-300:]!r} {sorted(p.name for p in r9.iterdir())}")
        rc, out, err = patched(r9, ENOSPC + "os.link = full", *CLAIM9)
        mk9 = next(r9.glob(".idem.*"), None)
        check("a filesystem without hard links (link refused, rename fine) still publishes; "
              "the key marker is old-format",
              rc == 0 and "wrote " in out and len(list(r9.glob("*.md"))) == 1 and mk9 is not None
              and mk9.read_text().count("\n") == 2 and hidden(r9) == [mk9.name], out + err + str(hidden(r9)))
        rc, out, err = patched(r9, ENOSPC + "os.link = full", *CLAIM9)
        check("and its retry is a duplicate there too", rc == 0 and "duplicate of" in out
              and len(list(r9.glob("*.md"))) == 1, out + err)
        rc, out, err = patched(
            r9, "def boom(*_a, **_k):\n    raise RuntimeError('not an OS error')\nagent_mail._publish = boom",
            "send", "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "c",
            "--body", "b", "--expires", soon(1), "--scope", "file:9b.py", "--key", "k9b")
        check("an unexpected exception is not masked (traceback, exit 1) but still releases lock and marker",
              rc == 1 and "RuntimeError: not an OS error" in err and hidden(r9) == [mk9.name if mk9 else ""],
              f"rc={rc} {err[-200:]!r} {hidden(r9)}")
        conflict = run(r9, *CLAIM9[:8], "c", "--body", "OTHER", *CLAIM9[11:])
        check("key conflict while holding the scope lock: exit 2, lock released",
              conflict.returncode == 2 and "different content" in conflict.stderr
              and not list(r9.glob(".scope.*")), conflict.stderr + str(hidden(r9)))
        parked, _ = blocked_send(r9, "agent_mail._publish = block", "send", "--type", "CLAIM",
                                 "--from", "alice", "--to", "all", "--subject", "c", "--body", "b",
                                 "--expires", soon(1), "--scope", "file:9c.py")
        had_lock = len(list(r9.glob(".scope.*.lock"))) == 1
        parked.send_signal(signal_.SIGTERM)
        _, err = parked.communicate(timeout=60)
        check("SIGTERM while holding a scope lock: exit 143, one line, lock file removed",
              had_lock and parked.returncode == 143 and err.strip() == "interrupted (SIGTERM); nothing written"
              and not list(r9.glob(".scope.*")), f"had_lock={had_lock} rc={parked.returncode} {err!r}")
        if os.geteuid() == 0:
            print("  SKIP  permission-denied checks (running as root)")
        else:
            import hashlib as hl  # noqa: PLC0415
            lockf = r9 / (".scope." + hl.sha256(b"file:9d.py").hexdigest()[:24] + ".lock")
            lockf.write_text("0\n")
            os.chmod(lockf, 0)
            D9 = ["send", "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "d",
                  "--body", "b", "--expires", soon(1), "--scope", "file:9d.py"]
            den = run(r9, *D9)
            check("permission denied on the scope lock file: one line naming EACCES, exit 1",
                  one_line_failure(den.returncode, den.stdout, den.stderr, "EACCES"),
                  f"rc={den.returncode} {den.stderr!r}")
            check("the lock file it could not open is not removed, and no claim was written",
                  lockf.exists() and rows(r9, "alice -> all  d") == 0, str(hidden(r9)))
            os.chmod(lockf, 0o644)
            okd = run(r9, *D9)
            check("control: with the lock file readable the claim is written and the lock removed",
                  okd.returncode == 0 and not lockf.exists(), okd.stderr)
            os.chmod(r9, 0o555)
            try:
                ro = run(r9, *D9[:-1], "file:9e.py")
                rok = run(r9, *NOTE, "--key", "ro")
            finally:
                os.chmod(r9, 0o755)
            check("read-only mailbox directory: scoped claim and keyed send each one line, EACCES, exit 1",
                  one_line_failure(ro.returncode, ro.stdout, ro.stderr, "EACCES")
                  and one_line_failure(rok.returncode, rok.stdout, rok.stderr, "EACCES"),
                  ro.stderr + rok.stderr)
            lst = run(r9, "list")
            check("control: reading a read-only mailbox still works",
                  lst.returncode == 0 and "alice -> all  d" in lst.stdout, lst.stderr)

        print("10. claims: expiry boundary, concurrent renewal and concurrent give-up by the owner")
        r10 = Path(td) / "rec10"
        r10.mkdir()
        (r10 / "01ARZ3NDEKTSV4RRFFQ69G5EXP-edge.md").write_text(
            "---\nid: 01ARZ3NDEKTSV4RRFFQ69G5EXP\ntype: CLAIM\nfrom: alice\nto: all\n"
            "date: 2026-06-01T00:00:00Z\nsubject: edge\nexpires: 2026-06-02T00:00:00Z\n---\n\nb\n")

        def claims_at(box: Path, now: str) -> dict[str, int]:
            return json.loads(run(box, "status", "--json", "--now", now).stdout)["claims"]

        check("a claim is held one second before its expiry",
              claims_at(r10, "2026-06-01T23:59:59Z")["held"] == 1, str(claims_at(r10, "2026-06-01T23:59:59Z")))
        check("and expired at the expiry instant itself (held iff expires is still in the future)",
              claims_at(r10, "2026-06-02T00:00:00Z") == {"held": 0, "expired": 1, "malformed_expiry": 0,
                                                         "superseded": 0}, str(claims_at(r10, "2026-06-02T00:00:00Z")))
        bid = mid(send(r10, "alice", "--expires", soon(1), kind="CLAIM", subj="base"))
        with ThreadPoolExecutor(8) as pool:
            ren = list(pool.map(lambda n: send(r10, "alice", "--expires", soon(1), "--supersedes", bid,
                                               kind="CLAIM", subj=f"renew{n}"), range(8)))
        check("8 simultaneous unscoped renewals by the owner all succeed (no lock is involved)",
              all(r.returncode == 0 for r in ren), str([(r.returncode, r.stderr) for r in ren]))
        check("the renewed claim is superseded exactly once in the listing; 8 renewals held",
              rows(r10, "[superseded] alice -> all  base") == 1 and rows(r10, "[held      ] alice -> all  renew") == 8,
              run(r10, "list").stdout)
        bid = mid(send(r10, "alice", "--expires", soon(1), "--scope", "file:10.py", kind="CLAIM",
                       subj="scoped"))
        short = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def give_up_or_grab(n: int) -> subprocess.CompletedProcess[str]:
            if n % 2:
                return send(r10, "bob", "--expires", soon(1), "--scope", "file:10.py", kind="CLAIM",
                            subj=f"grab{n}")
            return send(r10, "alice", "--expires", short, "--scope", "file:10.py", "--supersedes", bid,
                        kind="CLAIM", subj=f"giveup{n}")
        with ThreadPoolExecutor(8) as pool:
            mix = list(pool.map(give_up_or_grab, range(8)))
        check("owner giving a scope up (short renewals) while a rival grabs: every rival is refused (exit 3)",
              all(r.returncode == 3 for i, r in enumerate(mix) if i % 2)
              and all(r.returncode in (0, 3) for r in mix)
              and any(r.returncode == 0 for i, r in enumerate(mix) if not i % 2),
              str([(r.returncode, r.stderr.strip()[:60]) for r in mix]))
        check("no rival claim on disk, no scope lock or temp left, box parses clean",
              rows(r10, "bob -> all") == 0 and hidden(r10) == [] and run(r10, "list").returncode == 0,
              str(hidden(r10)) + run(r10, "list").stderr)
        later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        check("once every lease has run out nothing is held (a claim ends by expiry, not by a message)",
              claims_at(r10, later)["held"] == 0 and claims_at(r10, later)["superseded"] == 2,
              str(claims_at(r10, later)))
        held_now = [ln.split()[-1] for ln in run(r10, "list").stdout.splitlines()
                    if ln.startswith("    ")]
        scoped_held = [p.name[:26] for p in r10.glob("*.md") if "scope: file:10.py" in p.read_text()
                       and p.name[:26] != bid and p.name[:26] in held_now]
        rel = None
        for old_id in scoped_held:  # the owner supersedes each scoped lease with a claim naming no scope
            rel = send(r10, "alice", "--expires", short, "--supersedes", old_id, kind="CLAIM",
                       subj="released")
        check("renewing without --scope gives the scope up, and says so on stderr (exit 0)",
              rel is not None and rel.returncode == 0
              and "this one names no scope: 'file:10.py' is released" in rel.stderr,
              str(rel.stderr if rel else scoped_held))
        grab = send(r10, "bob", "--expires", soon(1), "--scope", "file:10.py", kind="CLAIM", subj="grab")
        check("the rival can then claim the scope at once", grab.returncode == 0, grab.stderr)

        print("11. messages: ids, ordering assumptions, and a hook that only reads")
        spec3 = ilu.spec_from_file_location("am_ids", TOOL)
        am3 = ilu.module_from_spec(spec3)  # type: ignore[arg-type]
        spec3.loader.exec_module(am3)  # type: ignore[union-attr]
        t_a = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        minted = [am3._new_ulid(t_a) for _ in range(5000)]
        check("5000 ids minted in the same millisecond are distinct valid ULIDs",
              len(set(minted)) == 5000 and all(am3._is_ulid(i) for i in minted))
        check("ids sort by time across milliseconds (same-millisecond order is not promised)",
              all(am3._new_ulid(t_a)[:10] < am3._new_ulid(t_a + dt.timedelta(milliseconds=1))[:10]
                  for _ in range(50)) and len({i[:10] for i in minted}) == 1)
        r11 = Path(td) / "rec11"
        r11.mkdir()
        (r11 / "01ARZ3NDEKTSV4RRFFQ69GZATE-q.md").write_text(
            "---\nid: 01ARZ3NDEKTSV4RRFFQ69GZATE\ntype: ASK\nfrom: alice\nto: bob\n"
            "date: 2026-06-02T00:00:00Z\nsubject: skewed\n---\n\nb\n")
        check("control: the ASK is open", rows(r11, "[open      ] alice -> bob  skewed") == 1)
        (r11 / "00ARZ3NDEKTSV4RRFFQ69GEARZ-a.md").write_text(
            "---\nid: 00ARZ3NDEKTSV4RRFFQ69GEARZ\ntype: ANSWER\nfrom: bob\nto: alice\n"
            "date: 2026-06-01T00:00:00Z\nsubject: early\nreply_to: 01ARZ3NDEKTSV4RRFFQ69GZATE\n---\n\nb\n")
        check("an ANSWER whose id sorts before the ASK (clock skew) still closes it: no order is assumed",
              rows(r11, "[answered  ] alice -> bob  skewed") == 1, run(r11, "list").stdout)
        for n in range(6):
            run(r11, "send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                "--subject", f"note{n}", "--body", "b")
        run(r11, "send", "--type", "ASK", "--from", "carol", "--to", "bob", "--subject", "open-q", "--body", "b")

        def snap11() -> dict[str, tuple[int, int, str]]:
            return {f.name: (f.stat().st_mtime_ns, f.stat().st_size,
                             hashlib.sha256(f.read_bytes()).hexdigest())
                    for f in sorted(r11.iterdir())}
        before11, dir_mtime = snap11(), r11.stat().st_mtime_ns
        h1, h2 = hook(r11, "bob"), hook(r11, "bob")
        live_ids = [ln.split()[-1] for ln in run(r11, "list", "--to", "bob", "--live").stdout.splitlines()
                    if ln.startswith("    ")]
        check("control: the hook delivered the 7 live messages addressed to bob",
              "Agent mail - 7 live message(s) addressed to 'bob'" in h1.stdout and len(live_ids) == 7, h1.stdout)
        check("no loss and no duplicate: every live id appears exactly once",
              all(h1.stdout.count(f"    id: {i}\n") == 1 for i in live_ids), h1.stdout)
        check("the hook wrote nothing: files, mtimes, hashes and the directory itself are unchanged",
              snap11() == before11 and r11.stat().st_mtime_ns == dir_mtime)
        check("there is no read state: a second pickup delivers the same messages again",
              h2.stdout == h1.stdout)
        run(r11, "send", "--type", "ANSWER", "--from", "bob", "--to", "carol", "--subject", "re",
            "--body", "b", "--reply-to", live_ids[-1].lower())
        h3 = hook(r11, "bob")
        check("only a durable reply removes a message from pickup (the answered ASK is gone, the rest stay)",
              "6 live message(s)" in h3.stdout and "open-q" not in h3.stdout and "note0" in h3.stdout, h3.stdout)

        print("12. diagnostics: what real crashed senders leave is named by status and doctor (PROTOCOL.md section 33)")

        def stj(box: Path, *extra: str) -> dict[str, object]:
            cp = run(box, "status", "--json", *extra)
            return json.loads(cp.stdout) if cp.returncode == 0 else {"error": cp.stderr}

        def codes(box: Path, *extra: str) -> dict[str, int]:
            return {p["code"]: p["count"] for p in stj(box, *extra)["problems"]}  # type: ignore[union-attr,index]

        def tree(box: Path) -> dict[str, tuple[int, int]]:
            return {f.name: (f.lstat().st_mtime_ns, f.lstat().st_size) for f in sorted(box.iterdir())}

        r12 = Path(td) / "rec12"
        r12.mkdir()
        ok12 = run(r12, *NOTE, "--key", "fine")
        check("control: a healthy mailbox after a keyed send has no problems and one resolved marker",
              ok12.returncode == 0 and stj(r12)["problems"] == []
              and stj(r12)["idem_markers"] == {"total": 1, "resolved": 1, "in_flight": 0, "held": 0, "orphaned": 0},
              str(stj(r12)))
        killed_send(r12, "agent_mail._publish = block", *NOTE, "--key", "dead")
        CL12 = ["send", "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "c",
                "--body", "b", "--expires", soon(1), "--scope", "file:12.py"]
        killed_send(r12, "agent_mail._publish = block", *CL12)
        st12 = stj(r12)
        check("just after a sender dies its marker and lock are 'in_flight' (under 60 s is never probed)",
              st12["idem_markers"] == {"total": 2, "resolved": 1, "in_flight": 1, "held": 0, "orphaned": 0}
              and st12["scope_locks"] == {"total": 1, "in_flight": 1, "held": 0, "orphaned": 0}
              and st12["problems"] == [], str(st12))
        dead_marker = next(p for p in r12.glob(".idem.*") if p.read_text().split("\n")[0] != mid(ok12))
        dead_lock = next(r12.glob(".scope.*.lock"))
        age(dead_marker, 59)
        age(dead_lock, 59)
        check("59 s old: still in flight", codes(r12) == {}, str(stj(r12)))
        age(dead_marker, 61)
        age(dead_lock, 61)
        before12 = tree(r12)
        check("61 s old with no living holder: ORPHAN_IDEM_MARKER and ORPHAN_SCOPE_LOCK, one each",
              codes(r12) == {"ORPHAN_IDEM_MARKER": 1, "ORPHAN_SCOPE_LOCK": 1}, str(stj(r12)["problems"]))
        d12 = run(r12, "doctor").stdout
        check("doctor names both files as orphaned",
              f"  - {dead_marker.name} (orphaned)" in d12 and f"  - {dead_lock.name} (orphaned)" in d12, d12)
        check("status and doctor changed nothing on disk (names, sizes, mtimes)", tree(r12) == before12)
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        age(dead_marker, 0)
        age(dead_lock, 0)
        check("ages follow --now: fresh files are orphaned when evaluated 30 minutes ahead",
              codes(r12) == {} and codes(r12, "--now", future).get("ORPHAN_IDEM_MARKER") == 1
              and codes(r12, "--now", future).get("ORPHAN_SCOPE_LOCK") == 1, str(codes(r12, "--now", future)))
        # a LIVE sender stuck for over a minute is 'held', not orphaned
        stuck_k, _ = blocked_send(r12, "agent_mail._publish = block", *NOTE, "--key", "stuck")
        stuck_c, _ = blocked_send(r12, "agent_mail._publish = block", *CL12[:-1], "file:12b.py", tag="c")
        for f in list(r12.glob(".idem.*")) + list(r12.glob(".scope.*.lock")):
            age(f, 61)
        st12 = stj(r12)
        check("live senders parked for 61 s: STALLED_LOCK_HOLDER 2; the dead ones stay orphaned",
              st12["scope_locks"] == {"total": 2, "in_flight": 0, "held": 1, "orphaned": 1}
              and st12["idem_markers"] == {"total": 3, "resolved": 1, "in_flight": 0, "held": 1, "orphaned": 1}
              and codes(r12).get("STALLED_LOCK_HOLDER") == 2, str(st12))
        busy = run(r12, *NOTE, "--key", "stuck", "--key-wait", "0")
        check("probing did not disturb the live holders: a retry of the stuck key is still told to retry",
              busy.returncode == 3 and stuck_k.poll() is None and stuck_c.poll() is None, busy.stderr)
        stuck_k.kill()
        stuck_c.kill()
        stuck_k.communicate()
        stuck_c.communicate()
        check("once they are killed the same files count as orphaned",
              stj(r12)["scope_locks"] == {"total": 2, "in_flight": 0, "held": 0, "orphaned": 2}
              and stj(r12)["idem_markers"] == {"total": 3, "resolved": 1, "in_flight": 0, "held": 0, "orphaned": 2}
              and "STALLED_LOCK_HOLDER" not in codes(r12), str(stj(r12)))
        rec = run(r12, *CL12)
        rek = run(r12, *NOTE, "--key", "dead")
        check("probed orphans are still reclaimable: the claim and the keyed retry both succeed at once",
              rec.returncode == 0 and rek.returncode == 0 and "wrote " in rek.stdout
              and not dead_lock.exists(), rec.stderr + rek.stderr)
        check("and the counts follow: one orphan of each kind left, two markers resolved",
              stj(r12)["scope_locks"] == {"total": 1, "in_flight": 0, "held": 0, "orphaned": 1}
              and stj(r12)["idem_markers"] == {"total": 3, "resolved": 2, "in_flight": 0, "held": 0, "orphaned": 1},
              str(stj(r12)))
        # hostile or odd hidden files: counted, never followed, never fatal
        r12b = Path(td) / "rec12b"
        r12b.mkdir()
        secret = Path(td) / "outside-secret"
        secret.write_text("01ARZ3NDEKTSV4RRFFQ69GSECR\n" + "0" * 64 + "\nflock\n")
        os.symlink(secret, r12b / (".idem." + "a" * 24))
        os.symlink(secret, r12b / (".scope." + "b" * 24 + ".lock"))
        os.symlink(Path(td) / "nowhere", r12b / ".gone.md.1.tmp")
        os.mkfifo(r12b / (".idem." + "c" * 24))
        (r12b / (".idem." + "d" * 24)).write_text("")
        (r12b / (".idem." + "e" * 24 + ".takeover")).write_text("")
        (r12b / ".unrelated").write_text("x")
        for f in r12b.iterdir():
            if not f.is_symlink():
                age(f, 120)
        st12b = stj(r12b, "--now", future)
        check("symlinks and a FIFO planted as marker or lock are counted in total only, never followed",
              st12b.get("idem_markers") == {"total": 3, "resolved": 0, "in_flight": 0, "held": 0, "orphaned": 1}
              and st12b.get("scope_locks") == {"total": 1, "in_flight": 0, "held": 0, "orphaned": 0}
              and st12b.get("takeover_mutexes") == {"total": 1, "stale": 1}, str(st12b))
        d12b = run(r12b, "doctor")
        check("doctor survives a dangling-symlink temp file and the planted files (no traceback)",
              "Traceback" not in d12b.stderr and "stale_tmp: 0" in d12b.stdout
              and "takeover_mutexes: 1 (stale 1)" in d12b.stdout, d12b.stdout + d12b.stderr)
        check("no output of status or doctor contains the linked file's content",
              "GSECR" not in json.dumps(st12b) + d12b.stdout + d12b.stderr)
        txt12 = run(r12b, "status", "--now", future)
        check("text status: PROBLEMS line with the fixed codes; a clean box prints none",
              "PROBLEMS: STALE_TMP=1 ORPHAN_IDEM_MARKER=1 STALE_TAKEOVER_MUTEX=1" in txt12.stdout
              and "PROBLEMS" not in run(r5.with_name("rec11"), "status").stdout.replace("STALLED", ""),
              txt12.stdout + run(r5.with_name("rec11"), "status").stdout)

    if FAILS:
        print("\nFAILED:")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
