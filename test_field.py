#!/usr/bin/env python3
"""Synthetic field test: four agents use one mailbox the way real agents would.

Everything goes through the command line, as subprocesses, against a mailbox
in a temporary directory (AGENT_MAIL_DIR). The agents (alice, bob, carol, dave),
subjects and scopes are invented. Phases, each timed:

  1. claims    four agents race for one scope: exactly one holder; `status
               --json` and `list` show it; the owner renews with --supersedes;
               a rival is refused; the lease expires and a rival then wins.
  2. keyed     a send with --key, the same again (duplicate, nothing written),
               and a sender SIGKILLed mid-send whose retry with the same key
               must leave exactly one message.
  3. ask       ASK, ANSWER with --reply-to, a retried ANSWER with the same
               --key (no duplicate); the ASK's derived status follows.
  4. burst     4 agents x 25 messages sent concurrently while 2 readers loop
               `list`/`show`: ids unique, count exact, no reader ever sees a
               partial or malformed message, nothing quarantined.
  5. closing   `doctor` and `status --json` clean, exact message counts, no
               lock/temp/takeover file left, no orphaned idempotency marker.

Phase 0 is the positive control for the readers' instruments: in a separate
scratch mailbox, a planted half-written message must be caught by exactly the
checks phase 4 relies on.

Clocks. `status --now` takes an injected instant, so expiry as `status` sees it
is checked deterministically on both sides of the boundary. `send` has no such
option: whether a rival may claim is decided on the real clock. That part
therefore uses a lease a couple of seconds long and polls (bounded, no fixed
sleep) until the rival is admitted, and checks the winner's own date stamp is
not before the expiry.

The killed sender is the one place that is not a bare CLI call: to die at a
known point, the child runs the real `agent_mail.main(argv)` with one function
replaced by "signal readiness, then block", and is SIGKILLed there. Its retry is
the plain CLI. How long that retry waits before taking the key over is not
asserted (only that it finishes within 15 s and leaves exactly one message);
it runs in the background while phases 3 and 4 use the same mailbox.

Bounded: every subprocess has a timeout; about 10 s on a laptop. Stdlib only.
POSIX only. Exit status 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = str(HERE / "agent_mail.py")
AGENTS = ("alice", "bob", "carol", "dave")
PER_AGENT = 25
SCOPE = "file:billing/client.py"
STEP_TIMEOUT = 60        # seconds; any single CLI call
RETRY_LIMIT = 15.0       # seconds; the keyed retry after a killed sender
FAILS: list[str] = []
TIMINGS: list[tuple[str, float]] = []

_ROW = re.compile(r"^[! ] (ASK|ANSWER|NOTICE|CLAIM|DISPUTE) +\[([a-z]+) *\] (\S+) -> (\S+)  (.+)$")
_ID_LINE = re.compile(r"^    ([0-9A-HJKMNP-TV-Z]{26})$")
_BURST = re.compile(r"^burst (alice|bob|carol|dave) (\d{2})$")
CP = subprocess.CompletedProcess


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(f"{name}: {detail[:400]}")


def run(box: Path, *argv: str) -> CP[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env["AGENT_MAIL_DIR"] = str(box)
    return subprocess.run([sys.executable, "-I", TOOL, *argv], capture_output=True, text=True,
                          env=env, timeout=STEP_TIMEOUT)


def send(box: Path, who: str, to: str, kind: str, subj: str, body: str, *extra: str) -> CP[str]:
    return run(box, "send", "--type", kind, "--from", who, "--to", to,
               "--subject", subj, "--body", body, *extra)


def mid(cp: CP[str]) -> str:
    return next((x.split(": ", 1)[1] for x in cp.stdout.splitlines() if x.startswith("id: ")), "")


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status(box: Path, *extra: str) -> dict:
    cp = run(box, "status", "--json", *extra)
    return json.loads(cp.stdout) if cp.returncode == 0 else {}


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


class phase:
    """Print a heading and record the wall-clock time of the block."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> None:
        print(self.name)
        self.t0 = time.monotonic()

    def __exit__(self, *_exc: object) -> None:
        TIMINGS.append((self.name, time.monotonic() - self.t0))


def killed_send(box: Path, ready: Path, *argv: str) -> tuple[bool, int | None]:
    """Run the real send in a child that parks just before publishing (after it
    has reserved its idempotency key), then SIGKILL it there. The parent polls
    for the child's ready-file in bounded 0.05 s steps. Returns (reached the
    chokepoint, return code)."""
    code = (
        "import signal, sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "import agent_mail\n"
        "def block(*_a, **_k):\n"
        f"    open({str(ready)!r}, 'w').close()\n"
        "    signal.pause()\n"
        "agent_mail._publish = block\n"
        f"sys.exit(agent_mail.main({list(argv)!r}))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env["AGENT_MAIL_DIR"] = str(box)
    proc = subprocess.Popen([sys.executable, "-I", "-c", code], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(400):  # 20 s hard cap
        if ready.exists() or proc.poll() is not None:
            break
        time.sleep(0.05)
    reached = ready.exists()
    proc.kill()
    proc.wait(timeout=STEP_TIMEOUT)
    ready.unlink(missing_ok=True)
    return reached, proc.returncode


def timed(box: Path, *argv: str) -> tuple[CP[str], float]:
    t0 = time.monotonic()
    cp = run(box, *argv)
    return cp, time.monotonic() - t0


def main() -> int:
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        box = Path(td) / "mail"
        box.mkdir()
        background = ThreadPoolExecutor(1)

        with phase("0. instruments: a half-written message is caught (positive control)"):
            ctl = Path(td) / "control"
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

        with phase("1. claims: one scope, four claimants, renewal, expiry, takeover"):
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

            expiry = int(time.time()) + 2      # a lease of one to two seconds
            renew = send(box, owner, "all", "CLAIM", "renewed", "still on it end", "--scope", SCOPE,
                         "--expires", iso(expiry), "--supersedes", cid)
            rid = mid(renew)
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
            deadline = time.monotonic() + 15
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

        with phase("2. keyed sends: duplicate, and a sender killed mid-send"):
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
            reached, rc = killed_send(box, Path(td) / "ready", *crash)
            check("a keyed sender is SIGKILLed after reserving its key, before publishing",
                  reached and rc == -9, f"reached={reached} rc={rc}")
            check("the killed send left no message (and no partial one)",
                  with_subject(box, "schema migrated") == [] and run(box, "list").returncode == 0)
            # The retry runs in the background: phases 3 and 4 share the mailbox with it.
            retry = background.submit(timed, box, *crash)

        with phase("3. ask and answer, with a retried answer"):
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

        with phase(f"4. burst: {len(AGENTS)} agents x {PER_AGENT} messages, 2 readers"):
            done = threading.Event()
            problems: list[str] = []
            passes = [0, 0]

            def writer(agent: str) -> list[tuple[int, str]]:
                out = []
                for n in range(PER_AGENT):
                    cp = send(box, agent, "all", "NOTICE", f"burst {agent} {n:02d}",
                              f"payload {agent} {n:02d} end")
                    out.append((cp.returncode, mid(cp)))
                return out

            def reader(slot: int) -> None:
                seen: set[str] = set()
                last = False
                while not last:
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
                    for f in box.glob("*-burst-*.md"):   # and reads the files directly
                        raw = f.read_text(encoding="utf-8")
                        if not complete(raw):
                            problems.append(f"partial file on disk: {f.name}")
                    passes[slot] += 1

            with ThreadPoolExecutor(len(AGENTS) + 2) as pool:
                readers = [pool.submit(reader, i) for i in range(2)]
                sent = [x for per in pool.map(writer, AGENTS) for x in per]
                done.set()
                for r in readers:
                    r.result(timeout=STEP_TIMEOUT * 4)
            want_n = len(AGENTS) * PER_AGENT
            ids = [i for _, i in sent]
            check(f"all {want_n} sends succeeded", [rc for rc, _ in sent] == [0] * want_n,
                  str([rc for rc, _ in sent if rc]))
            check("every id is unique", len(set(ids)) == want_n and "" not in ids,
                  f"{len(set(ids))} distinct")
            listed = [r for r in rows(run(box, "list").stdout) or [] if _BURST.match(r[4])]
            check(f"exactly {want_n} burst messages are listed, with exactly the ids the senders got",
                  len(listed) == want_n and {r[5] for r in listed} == set(ids), str(len(listed)))
            check("every (agent, number) pair arrived exactly once",
                  sorted(r[4] for r in listed)
                  == sorted(f"burst {a} {n:02d}" for a in AGENTS for n in range(PER_AGENT)))
            check("both readers ran throughout and never saw a partial or malformed message",
                  not problems and min(passes) >= 2, f"passes={passes} {problems[:3]}")
            print(f"        (reader passes over the mailbox during the burst: {passes[0]} and {passes[1]})")

        with phase("2b. keyed retry after the killed sender (ran in the background)"):
            try:
                cp, took = retry.result(timeout=STEP_TIMEOUT)
            except (FutureTimeout, subprocess.TimeoutExpired) as exc:   # a finding, not a crash
                cp, took = CP([], 1, "", repr(exc)), float(STEP_TIMEOUT)
            background.shutdown()
            check("the retry with the same key succeeds", cp.returncode == 0 and mid(cp) != "",
                  cp.stdout + cp.stderr)
            check(f"it completes within {RETRY_LIMIT:.0f} s", took < RETRY_LIMIT, f"{took:.1f}s")
            check("exactly one message exists for the killed-and-retried send",
                  [r[5] for r in with_subject(box, "schema migrated")] == [mid(cp)],
                  str(with_subject(box, "schema migrated")))
            third = send(box, "carol", "all", "NOTICE", "schema migrated", "tables are at v7 end",
                         "--key", "k-schema")
            check("a further retry is a duplicate of it; still exactly one message",
                  third.returncode == 0 and mid(third) == mid(cp)
                  and len(with_subject(box, "schema migrated")) == 1, third.stdout + third.stderr)
        TIMINGS.append(("    the retry itself, from its start in phase 2", took))

        with phase("5. closing: doctor, status, leftovers"):
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
            n_notice = want_n + 3      # burst + keyed + killed-and-retried + canary
            by_type = st.get("messages", {}).get("by_type", {})
            check("status: exact totals (3 CLAIM, 1 ASK, 1 ANSWER, burst + 3 NOTICE)",
                  st.get("messages", {}).get("total") == n_notice + 5
                  and (by_type.get("CLAIM"), by_type.get("ASK"), by_type.get("ANSWER"),
                       by_type.get("NOTICE")) == (3, 1, 1, n_notice), json.dumps(st.get("messages")))
            check("status: nothing quarantined, no stale temp file, no open ASK, nobody stalled",
                  (st.get("quarantined"), st.get("stale_tmp"), st.get("asks", {}).get("open"),
                   st.get("stalled_agents")) == (0, 0, 0, []), json.dumps(st)[:300])
            check("status: all four agents are active",
                  {a["agent"]: a["state"] for a in st.get("agents", [])}
                  == dict.fromkeys(AGENTS, "active"), str(st.get("agents")))
            files = sorted(p.name for p in box.iterdir())
            check("one file per message, nothing else visible",
                  len([f for f in files if not f.startswith(".")]) == n_notice + 5
                  and all(f.endswith(".md") for f in files if not f.startswith(".")))
            hidden = [f for f in files if f.startswith(".")]
            leftover = [f for f in hidden if f.endswith((".lock", ".tmp", ".takeover"))]
            check("no lock, temp or takeover file left behind", not leftover, str(leftover))
            published = {f[:26] for f in files if not f.startswith(".")}
            orphans = [f for f in hidden
                       if not set((box / f).read_text(encoding="utf-8").split()) & published]
            check("every other hidden file is an idempotency marker of a published message",
                  not orphans and all(f.startswith(".idem.") for f in hidden), str(hidden))
            stray = sorted(p.name for p in Path(td).iterdir() if p.name != "mail")
            check("nothing left next to the mailbox", not stray, str(stray))
    check("teardown removed the scratch directory", not Path(td).exists())

    total = time.monotonic() - started
    print("timings (wall clock):")
    for label, seconds in TIMINGS:
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
