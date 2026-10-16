#!/usr/bin/env python3
"""Build a ~30 event scenario in a fresh mailbox with real processes.

    python3 build_scenario.py --tool PATH/TO/agent_mail.py --mailbox EMPTY_DIR

Takes about 12 seconds (real leases). Includes: a holder killed with SIGKILL
inside a claim send, a holder that stops renewing (crash mid-task), a takeover,
a renewal, a release, a re-scope, a late refile, refused claims, an unanswered
ASK, conflicting ANSWERs and an ASK closed by a non-addressee.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import subprocess
import sys
import tempfile
import time

STALL = """
import sys, time, os
sys.path.insert(0, os.path.dirname(os.environ["TOOL"]))
import agent_mail
orig = agent_mail._publish
def slow(*a, **k):
    open(os.environ["FLAG"], "w").write("in")
    time.sleep(30)
    return orig(*a, **k)
agent_mail._publish = slow
sys.exit(agent_mail.main(sys.argv[1:]))
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--mailbox", required=True)
    a = ap.parse_args()
    tool, box = os.path.abspath(a.tool), a.mailbox
    env = dict(os.environ, AGENT_MAIL_DIR=box)

    def iso(sec: float) -> str:
        return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def am(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, tool, *args], capture_output=True, text=True, env=env)

    def msg(typ: str, frm: str, to: str, subj: str, *extra: str) -> str:
        r = am("send", "--type", typ, "--from", frm, "--to", to, "--subject", subj, "--body", "x", *extra)
        ids = [t for t in r.stdout.split() if len(t) == 26 and t.isalnum()]
        print(f"{typ:<7} {frm}->{to} {subj!r}: exit {r.returncode}" + (f" ({r.stderr.strip()[:70]})" if r.returncode else ""))
        return ids[-1] if ids else ""

    def claim(frm: str, scope: str | None, sec: float, subj: str, *extra: str) -> str:
        sc = ("--scope", scope) if scope else ()
        return msg("CLAIM", frm, "ops", subj, *sc, "--expires", iso(sec), *extra)

    db1 = claim("alice", "db", 60, "alice takes db")
    q1 = msg("ASK", "bob", "alice", "is the schema frozen?")
    msg("ANSWER", "alice", "bob", "yes", "--reply-to", q1, "--result", "DONE")
    claim("carol", "f.py", 5, "carol takes f.py (will stop renewing)")
    # carol is killed with SIGKILL inside a claim send for g.py: nothing may land
    work = tempfile.mkdtemp(prefix="stall-")
    open(os.path.join(work, "stall.py"), "w").write(STALL)
    flag = os.path.join(work, "flag")
    p = subprocess.Popen([sys.executable, os.path.join(work, "stall.py"), "send", "--type", "CLAIM", "--from", "carol",
                          "--to", "ops", "--subject", "carol takes g.py", "--scope", "g.py", "--expires", iso(60),
                          "--body", "x"], env=dict(env, TOOL=tool, FLAG=flag), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    while not os.path.exists(flag):
        time.sleep(0.05)
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    print("SIGKILL during carol's claim send for g.py")
    claim("dave", "g.py", 60, "dave takes g.py right after the kill")
    msg("ASK", "bob", "carol", "did f.py change?")            # never answered: carol is gone
    claim("dave", "f.py", 60, "dave tries f.py while carol holds it")   # refused, exit 3
    db2 = claim("alice", "db", 60, "alice renews db", "--supersedes", db1)
    claim("alice", None, 60, "alice releases db", "--supersedes", db2)
    e1 = claim("erin", "db", 60, "erin takes db")
    h1 = claim("frank", "h", 60, "frank takes h")
    claim("frank", "h", 90, "frank renews h", "--supersedes", h1)
    claim("dave", "h", 60, "dave tries h while frank holds it")          # refused
    q3 = msg("ASK", "bob", "dave", "run the migration?")
    msg("ANSWER", "dave", "bob", "tried, failed", "--reply-to", q3, "--result", "FAILED")
    msg("ANSWER", "dave", "bob", "retried, done", "--reply-to", q3, "--result", "DONE")
    q4 = msg("ASK", "bob", "erin", "review PR 7?")
    msg("ANSWER", "mallory", "bob", "looks fine to me", "--reply-to", q4)  # not the addressee
    msg("NOTICE", "alice", "ops", "db released")
    msg("NOTICE", "alice", "ops", "second notice, same second")
    k1 = claim("gil", "k", 3, "gil takes k (short lease)")
    claim("erin", "db2", 60, "erin re-scopes db to db2", "--supersedes", e1)
    time.sleep(6.5)                                            # carol's f.py and gil's k leases have run out
    claim("dave", "f.py", 60, "dave takes over f.py")
    claim("gil", "k", 60, "gil refiles k after it lapsed", "--supersedes", k1)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
