#!/usr/bin/env python3
"""A small CLI-level conformance check for anyone who wraps or reimplements agent_mail.py.

    python conformance/run_conformance.py [--tool PATH_TO_agent_mail.py] [--protocol-only]
                                          [--json-out FILE] [--cmd PROGRAM [ARG ...]]

It drives the command line the way an adapter does (subprocesses, a throwaway mailbox
selected with AGENT_MAIL_DIR, no network, no model, stdlib only) and checks the parts
docs/protocol-compatibility.md calls "stable". It is not a certification: passing shows
these behaviours, not that your adapter is correct, and it says nothing about whether a
model reads or obeys anything. See docs/adapter-conformance-guide.md.

Three groups, reported separately:

  PROTOCOL-LEVEL           what PROTOCOL.md requires of any implementation that offers these
                           commands: exit statuses (0/1/2/3), refusals that write nothing, the
                           message file shape, UTF-8 round trips, --json schema 1, claim
                           exclusivity (also under a race), lease expiry, ASK/ANSWER threading,
                           idempotent --key (also under a race), quarantine of malformed files.
                           The id of a sent message is taken from the new file's name (the `id:`
                           line is only a fallback); the --version text and wording on stderr are
                           never read. The checks do depend on the command-line surface below
                           (flags, `--json` schema 1, exit statuses) and on `<ULID>-<slug>.md`
                           names in the mailbox directory.
  IMPLEMENTATION-SPECIFIC  what this CLI happens to print and an adapter may not copy: the
                           --version string, the `id: <ULID>` line, the `NO MAILBOX` and `REJECT`
                           words on stderr. A mismatch here is not a protocol failure.
  OPTIONAL                 features not in every release (owed_truncated, issue 96; the inbox
                           exit status for schedulers, issue 97). SKIPPED when the tool does not
                           have them: an absent JSON key, or `inbox --check`/`--exit-code`
                           answered with exit 2 and a message containing "unrecognized",
                           "unknown", "invalid" or "usage:". Any other answer is treated as a
                           feature the tool has, and is checked in full (and can fail).

--protocol-only runs the first group alone. Exit status: 0 when nothing that ran failed
(skips do not fail), 1 when a check failed, 2 when the tool could not be started or the run
stopped early before any check failed. A tool that starts but exits non-zero for everything
fails checks (exit 1), it does not exit 2. Needs Python 3.10 or newer.

Requirements the runner puts on the tool under test: it is started as `python PATH args...`
(or as the --cmd program), reads the mailbox directory from AGENT_MAIL_DIR, takes the same
flags as agent_mail.py (send --type/--from/--to/--subject/--body/--body-file/--key/--scope/
--expires/--reply-to, list/inbox/status/show, --json, --now), and prints nothing the harness
cannot decode as UTF-8.

Deliberately NOT asserted, because the project has not decided them: how an unreadable (as
opposed to missing) mailbox is reported by list/inbox, and which rows `inbox --limit` drops.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TOOL = Path(__file__).resolve().parent.parent / "agent_mail.py"
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
ROW_KEYS = {"id", "type", "from", "to", "date", "subject", "status", "owes_reply", "reply_to",
            "supersedes", "scope", "expires", "oversize", "result"}
TOP_KEYS = {"total", "matched", "returned", "truncated", "quarantined", "messages", "mailbox",
            "now", "filters"}
PROTO, IMPL, OPT = "protocol", "implementation", "optional"
TITLES = {PROTO: "PROTOCOL-LEVEL (any implementation must satisfy)",
          IMPL: "IMPLEMENTATION-SPECIFIC (this CLI's wording; a mismatch is not a protocol failure)",
          OPT: "OPTIONAL (skipped when the tool does not have the feature)"}
RESULTS: list[dict[str, str]] = []
SCRUB: list[str] = []          # temp paths removed from reported details
TIMEOUT = 60
ONLY_P = False
SECTION = [""]


def _clean(text: object) -> str:
    text = str(text)
    for s in SCRUB:
        text = text.replace(s, "<tmp>")
    return text


def section(title: str) -> None:
    SECTION[0] = title


def check(group: str, name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append({"group": group, "section": SECTION[0], "name": name,
                    "status": "PASS" if ok else "FAIL", "detail": "" if ok else _clean(detail)})


def skip(group: str, name: str, why: str) -> None:
    RESULTS.append({"group": group, "section": SECTION[0], "name": name, "status": "SKIP", "detail": why})


class Box:
    """One throwaway mailbox and a way to run the tool against it."""

    def __init__(self, cmd: list[str], root: Path, name: str) -> None:
        self.cmd, self.dir = cmd, root / name / "mail"
        self.dir.mkdir(parents=True)

    def env(self, box: Path | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env["AGENT_MAIL_DIR"] = str(box or self.dir)
        env["PYTHONIOENCODING"] = "utf-8"   # the harness decodes as UTF-8 whatever the console is
        return env

    def run(self, *argv: str, stdin: bytes | None = None, box: Path | None = None):
        try:
            r = subprocess.run([*self.cmd, *argv], capture_output=True, env=self.env(box),
                               input=stdin if stdin is not None else b"", timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return -1, "", f"TIMEOUT after {TIMEOUT}s"
        except OSError as exc:
            return -1, "", f"cannot run: {exc}"
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    def send_argv(self, mtype: str, frm: str, to: str, subject: str, *extra: str,
                  body: bool = True) -> list[str]:
        return ["send", "--type", mtype, "--from", frm, "--to", to, "--subject", subject,
                *(["--body", "b"] if body else ["--body-file", "-"]), *extra]

    def send(self, mtype: str, frm: str, to: str, subject: str, *extra: str,
             stdin: bytes | None = None):
        """(exit status, id, stderr). The id is the ULID prefix of the one file that appeared; the
        `id:` line of stdout is read only when no single new file can be told apart (an
        implementation that names files differently)."""
        before = {p.name for p in self.files()}
        rc, out, err = self.run(*self.send_argv(mtype, frm, to, subject, *extra, body=stdin is None),
                                stdin=stdin)
        new = [p.name[:26] for p in self.files() if p.name not in before]
        if len(new) == 1 and ULID.match(new[0]):
            return rc, new[0], err
        found = re.search(r"^id: (\S+)", out, re.M)
        return rc, (found.group(1) if found else ""), err

    def files(self) -> list[Path]:
        return sorted(p for p in self.dir.glob("*.md"))

    def rows(self, *argv: str) -> list[dict]:
        return jload(self.run(*argv, "--json")[1]).get("messages", [])

    def many(self, argvs: list[list[str]]) -> list[tuple[int, str]]:
        """Start every command at once, wait for all: (exit status, stderr) per command."""
        procs: list[subprocess.Popen[bytes] | OSError] = []
        for argv in argvs:
            try:
                procs.append(subprocess.Popen([*self.cmd, *argv], env=self.env(), stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            except OSError as exc:
                procs.append(exc)
        out = []
        for p in procs:
            if isinstance(p, OSError):
                out.append((-1, str(p)))
                continue
            try:
                _, err = p.communicate(timeout=TIMEOUT)
                out.append((p.returncode, err.decode("utf-8", "replace")))
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()
                out.append((-1, "TIMEOUT"))
        return out


def jload(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def stamp(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def soon(hours: float) -> str:
    return stamp(dt.timedelta(hours=hours))


def write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:   # no newline translation, whatever the platform
        fh.write(data)


def protocol_checks(cmd: list[str], root: Path) -> None:
    section("send, show, message files")
    b = Box(cmd, root, "basic")
    rc, mid, err = b.send("NOTICE", "agent-a", "agent-b", "café 日本", stdin="héllo — wörld\n".encode())
    check(PROTO, "send exits 0 and writes exactly one message file", rc == 0 and len(b.files()) == 1, f"rc={rc} {err[:80]}")
    files = b.files()
    check(PROTO, "the file is named <ULID>-<slug>.md", len(files) == 1 and bool(ULID.match(files[0].name[:26]))
          and files[0].name[26] == "-" and files[0].name.endswith(".md"), str(files))
    raw = files[0].read_bytes() if files else b""
    text = raw.decode("utf-8", "replace")
    head = text.split("\n---", 1)[0]
    check(PROTO, "file is UTF-8, LF, begins '---', carries id/type/from/to/date/subject",
          raw.startswith(b"---\n") and b"\r" not in raw and all(f"\n{k}: " in head for k in ("id", "type", "from", "to", "date", "subject")), repr(raw[:120]))
    check(PROTO, "non-ASCII body and subject survive stdin (UTF-8) unchanged", "héllo — wörld" in text and "subject: café 日本" in text, repr(text[:200]))
    rc, out, _ = b.run("show", mid)
    check(PROTO, "show <id> exits 0 and prints the body", rc == 0 and "héllo" in out, out[:80])
    rc, _, _ = b.run("show", "NOSUCHID")
    check(PROTO, "show of an unknown id exits 1", rc == 1, f"rc={rc}")
    if not ONLY_P:
        rc, ver, _ = b.run("--version")
        check(IMPL, "--version prints 'agent-postbox X.Y.Z', exit 0", rc == 0 and re.fullmatch(r"agent-postbox \d+\.\d+\.\d+\s*", ver) is not None, ver)
        rc, out, _ = b.run(*b.send_argv("NOTICE", "agent-a", "agent-b", "idline"))
        check(IMPL, "send prints 'id: <ULID>' on stdout", rc == 0 and bool(re.search(r"^id: [0-9A-HJKMNP-TV-Z]{26}\r?$", out, re.M)), out[:80])

    section("refusals write nothing")
    before = len(b.files())
    for label, argv in (("newline in subject", ("NOTICE", "agent-a", "agent-b", "x\ny")),
                        ("non-ASCII identity", ("NOTICE", "agént", "agent-b", "x")),
                        ("control character in subject", ("NOTICE", "agent-a", "agent-b", "x\x1b[31m"))):
        rc, _, _ = b.send(*argv)
        check(PROTO, f"{label}: exit 2, no file", rc == 2 and len(b.files()) == before, f"rc={rc}")
    rc, _, err = b.run("list", box=root / "nowhere")
    check(PROTO, "missing mailbox: exit 2, directory not created", rc == 2 and not (root / "nowhere").exists(), f"rc={rc} {err[:80]}")
    if not ONLY_P:
        check(IMPL, "missing mailbox: 'NO MAILBOX' on stderr", "NO MAILBOX" in err, err[:80])
    rc, _, _ = b.run("inbox", "--to", "")
    check(PROTO, "empty identity is refused (exit 2), not read as an empty inbox", rc == 2, f"rc={rc}")
    rc, _, _ = b.run("bogus-subcommand")
    check(PROTO, "an unknown subcommand exits 2 and writes nothing", rc == 2 and len(b.files()) == before, f"rc={rc}")

    section("JSON schema 1")
    d = jload(b.run("list", "--json")[1])
    check(PROTO, "list --json: schema 1, view 'list', the documented top-level keys",
          d.get("schema") == 1 and d.get("view") == "list" and TOP_KEYS <= set(d), sorted(d))
    check(PROTO, "list --json: every row has the documented keys", bool(d.get("messages")) and all(ROW_KEYS <= set(r) for r in d["messages"]), str(d.get("messages", [])[:1]))
    ib_ = jload(b.run("inbox", "--to", "agent-b", "--json")[1])
    check(PROTO, "inbox --json: schema 1, view 'inbox', the documented top-level keys", ib_.get("schema") == 1 and ib_.get("view") == "inbox" and TOP_KEYS <= set(ib_), sorted(ib_))
    s = jload(b.run("status", "--json")[1])
    check(PROTO, "status --json: schema 1 with 'messages', 'asks', 'claims'", s.get("schema") == 1 and {"messages", "asks", "claims"} <= set(s), sorted(s))
    sh = jload(b.run("show", mid, "--json")[1])
    check(PROTO, "show --json: a JSON document with schema 1", sh.get("schema") == 1, sorted(sh))

    section("ASK / ANSWER threading")
    o = Box(cmd, root, "asks")
    _, ask, _ = o.send("ASK", "agent-a", "agent-b", "who owns it?")
    r1 = o.rows("inbox", "--to", "agent-b")
    check(PROTO, "an ASK is in the recipient's inbox and owes a reply", any(r["id"] == ask and r["owes_reply"] for r in r1), str(r1))
    check(PROTO, "an ASK is not in the asker's inbox", not any(r["id"] == ask for r in o.rows("inbox", "--to", "agent-a")), "")
    _, ans, _ = o.send("ANSWER", "agent-b", "agent-a", "re", "--reply-to", ask)
    r2 = o.rows("inbox", "--to", "agent-b")
    check(PROTO, "an ANSWER that cites it closes it (derived, nothing edited)", not any(r["id"] == ask for r in r2), str(r2))
    allrows = {r["id"]: r for r in o.rows("list")}
    check(PROTO, "the ANSWER's reply_to is the ASK's id and the ASK reads as answered",
          allrows.get(ans, {}).get("reply_to") == ask and allrows.get(ask, {}).get("status") == "answered", str(allrows))
    _, ask2, _ = o.send("ASK", "agent-a", "agent-b", "second")
    o.send("ANSWER", "agent-c", "agent-a", "stray", "--reply-to", "01ZZZZZZZZZZZZZZZZZZZZZZZZ")
    check(PROTO, "an ANSWER citing an id nobody has does not close another ASK",
          any(r["id"] == ask2 and r["owes_reply"] for r in o.rows("inbox", "--to", "agent-b")), "")

    section("claims: exclusivity and lease expiry")
    c = Box(cmd, root, "claims")
    rc, _, _ = c.send("CLAIM", "agent-a", "all", "c", "--scope", "src/x.py")
    check(PROTO, "CLAIM without --expires: exit 2, nothing written", rc == 2 and not c.files(), f"rc={rc}")
    rc, first, _ = c.send("CLAIM", "agent-a", "all", "c", "--scope", "src/x.py", "--expires", soon(2))
    check(PROTO, "CLAIM with a future expiry: exit 0", rc == 0, f"rc={rc}")
    n = len(c.files())
    rc, _, err = c.send("CLAIM", "agent-b", "all", "c", "--scope", "src/x.py", "--expires", soon(2))
    check(PROTO, "second sender, same scope: exit 3, nothing written", rc == 3 and len(c.files()) == n, f"rc={rc} {err[:80]}")
    rc, _, _ = c.send("CLAIM", "agent-b", "all", "c", "--scope", "src/other.py", "--expires", soon(2))
    check(PROTO, "a different scope is not blocked: exit 0", rc == 0, f"rc={rc}")
    rc, _, _ = c.send("CLAIM", "agent-a", "all", "c", "--scope", "src/x.py", "--expires", soon(2))
    check(PROTO, "the holder may claim its own scope again: exit 0", rc == 0, f"rc={rc}")
    rc, _, _ = c.send("CLAIM", "agent-a", "all", "c", "--scope", "src/z.py", "--expires", soon(24 * 30))
    check(PROTO, "a lease of more than 168 hours is refused: exit 2", rc == 2, f"rc={rc}")
    rc, _, _ = c.send("CLAIM", "agent-a", "all", "c", "--scope", "src/y.py", "--expires", "yesterday")
    check(PROTO, "an expiry that is not a timestamp is refused: exit 2", rc == 2, f"rc={rc}")
    far = stamp(dt.timedelta(days=30))
    st = {r["id"]: r["status"] for r in c.rows("list", "--now", far, "--type", "CLAIM")}
    check(PROTO, "at a later instant the lease is 'expired' (status is derived from the clock)", st.get(first) == "expired", str(st))
    st = {r["id"]: r["status"] for r in c.rows("list", "--now", stamp(dt.timedelta(minutes=30)), "--type", "CLAIM")}
    check(PROTO, "before its expiry the lease is 'held'", st.get(first) == "held", str(st))
    e = Box(cmd, root, "lapse")
    e.send("CLAIM", "agent-a", "all", "short", "--scope", "src/short.py", "--expires", stamp(dt.timedelta(seconds=2)))
    time.sleep(3.5)
    rc, _, err = e.send("CLAIM", "agent-b", "all", "c", "--scope", "src/short.py", "--expires", soon(2))
    check(PROTO, "once a lease has lapsed another sender may claim the scope: exit 0", rc == 0, f"rc={rc} {err[:80]}")

    r = Box(cmd, root, "race")
    wins = []
    for kk in range(3):
        res = r.many([r.send_argv("CLAIM", who, "all", "c", "--scope", f"race/{kk}", "--expires", soon(2))
                      for who in ("agent-a", "agent-b", "agent-c", "agent-d")])
        wins.append(sorted(rc for rc, _ in res))
    check(PROTO, "four simultaneous claims of one scope: exactly one exit 0, the rest exit 3 (3 rounds)",
          all(w == [0, 3, 3, 3] for w in wins), str(wins))
    held: dict[str, int] = {}
    for row in r.rows("list", "--type", "CLAIM"):
        held[row.get("scope") or ""] = held.get(row.get("scope") or "", 0) + 1
    check(PROTO, "after the race each scope has exactly one claim on disk", all(held.get(f"race/{kk}") == 1 for kk in range(3)), str(held))

    section("idempotent keys")
    k = Box(cmd, root, "keys")
    _, a, _ = k.send("NOTICE", "agent-a", "agent-b", "once", "--key", "k1")
    rc, again, _ = k.send("NOTICE", "agent-a", "agent-b", "once", "--key", "k1")
    check(PROTO, "same --key and content: exit 0, same id, one file", rc == 0 and again == a and len(k.files()) == 1, f"rc={rc} {again} {a}")
    n = len(k.files())
    rc, _, _ = k.send("NOTICE", "agent-a", "agent-b", "once", "--key", "k1", stdin=b"different body")
    check(PROTO, "same --key, different content: exit 2, nothing written", rc == 2 and len(k.files()) == n, f"rc={rc}")
    rc, other, _ = k.send("NOTICE", "agent-b", "agent-b", "once", "--key", "k1")
    check(PROTO, "the same key from another sender is a different key: new message", rc == 0 and other not in ("", a) and len(k.files()) == n + 1, f"rc={rc}")
    kr = Box(cmd, root, "keyrace")
    res = kr.many([kr.send_argv("NOTICE", "agent-a", "agent-b", "burst", "--key", "burst-1") for _ in range(5)])
    check(PROTO, "five simultaneous sends with one --key: all exit 0, one file", all(rc == 0 for rc, _ in res) and len(kr.files()) == 1,
          f"{[rc for rc, _ in res]} files={len(kr.files())}")

    section("malformed input is quarantined, never delivered, never deleted")
    def still_listed(box: Box, rc: int, subject: str) -> bool:
        """Real mail is still delivered beside a quarantined file, judged from `list --json`
        (schema 1, stable) rather than from the plain text, which is not a contract."""
        code, out, _ = box.run("list", "--json")
        try:
            rows = json.loads(out).get("messages", [])
        except ValueError:
            return False
        return rc == 2 and code == 2 and any(r.get("subject") == subject for r in rows)

    seed = Box(cmd, root, "seed")
    _, sid, _ = seed.send("NOTICE", "agent-a", "agent-b", "ok-mail")
    good = seed.files()[0].read_bytes().decode("utf-8") if seed.files() else ""

    variants = {
        "an unknown type": lambda t: t.replace("type: NOTICE", "type: BOGUS", 1),
        "a duplicate header key": lambda t: re.sub(r"^(subject: .*)$", r"\1\n\1", t, count=1, flags=re.M),
        "a date that does not parse": lambda t: re.sub(r"^date: .*$", "date: not-a-date", t, count=1, flags=re.M),
        "a missing recipient": lambda t: re.sub(r"^to: .*\n", "", t, count=1, flags=re.M),
    }
    bad = "01AAAAAAAAAAAAAAAAAAAAAAAA"
    for n_, (label, fn) in enumerate(variants.items()):
        q = Box(cmd, root, f"q{n_}")
        q.send("NOTICE", "agent-a", "agent-b", "ok-mail")
        text = fn(re.sub(r"^id: .*$", f"id: {bad}", good, count=1, flags=re.M))
        write_bytes(q.dir / f"{bad}-bad.md", text.encode("utf-8"))
        rc, out, err = q.run("list")
        check(PROTO, f"{label}: list exits 2, the file stays on disk, real mail is still listed",
              still_listed(q, rc, "ok-mail") and (q.dir / f"{bad}-bad.md").exists(), f"rc={rc} {err[:100]}")
    q = Box(cmd, root, "qid")
    q.send("NOTICE", "agent-a", "agent-b", "ok-mail")
    write_bytes(q.dir / f"{bad}-bad.md", re.sub(r"^id: .*$", "id: not-a-ulid", good, count=1, flags=re.M).encode("utf-8"))
    rc, out, err = q.run("list")
    check(PROTO, "an id that is not a ULID: list exits 2, the file stays on disk, real mail is still listed",
          still_listed(q, rc, "ok-mail") and (q.dir / f"{bad}-bad.md").exists(), f"rc={rc} {err[:100]}")
    dup = Box(cmd, root, "dup")
    dup.send("NOTICE", "agent-a", "agent-b", "ok-mail")
    for twin in dup.files()[:1]:
        write_bytes(dup.dir / (twin.name[:26] + "-twin.md"), twin.read_bytes())
    rc, out, err = dup.run("list")
    check(PROTO, "two files with one id: list exits 2 and both stay on disk", rc == 2 and len(dup.files()) == 2, f"rc={rc} {err[:100]}")
    nu = Box(cmd, root, "binary")
    nu.send("NOTICE", "agent-a", "agent-b", "ok-mail")
    write_bytes(nu.dir / "01AAAAAAAAAAAAAAAAAAAAAAAA-bad.md", b"---\nid: 01AAAAAAAAAAAAAAAAAAAAAAAA\nfrom: x\xff\n---\n")
    rc, out, err = nu.run("list")
    check(PROTO, "a hand-written non-UTF-8 file: list exits 2, real mail still listed", still_listed(nu, rc, "ok-mail"), f"rc={rc} {err[:100]}")
    if not ONLY_P:
        check(IMPL, "a quarantined file is named by a 'REJECT' line on stderr", "REJECT" in err, err[:100])


def optional_checks(cmd: list[str], root: Path) -> None:
    section("owed_truncated (issue 96)")
    t = Box(cmd, root, "trunc")
    _, a1, _ = t.send("ASK", "agent-a", "agent-b", "one")
    t.send("ANSWER", "agent-b", "agent-a", "re one", "--reply-to", a1)
    t.send("ASK", "agent-a", "agent-b", "two")
    t.send("ASK", "agent-a", "agent-b", "three")
    d = jload(t.run("list", "--type", "ASK", "--limit", "1", "--json")[1])
    if "owed_truncated" not in d:
        skip(OPT, "owed_truncated", "absent from `list --json --limit`: feature not in this tool")
    else:
        check(OPT, "list --limit 1 over three ASKs (one answered): truncated 2, owed_truncated 1",
              d.get("truncated") == 2 and d.get("owed_truncated") == 1, f"truncated={d.get('truncated')} owed_truncated={d.get('owed_truncated')}")
        full = jload(t.run("list", "--type", "ASK", "--json")[1])
        check(OPT, "without --limit owed_truncated is 0 and truncated is 0", full.get("owed_truncated") == 0 and full.get("truncated") == 0, str(full.get("owed_truncated")))
        ib = jload(t.run("inbox", "--to", "agent-b", "--limit", "1", "--json")[1])
        check(OPT, "inbox --limit 1: owed_truncated is an integer, at most truncated, and 1 (two owed, one shown)",
              isinstance(ib.get("owed_truncated"), int) and ib["owed_truncated"] <= ib.get("truncated", -1) and ib["owed_truncated"] == 1,
              f"truncated={ib.get('truncated')} owed_truncated={ib.get('owed_truncated')}")
        check(OPT, "the rows kept and the other top-level keys are unchanged by the new key",
              d.get("schema") == 1 and d.get("returned") == 1 and TOP_KEYS <= set(d), sorted(d))

    section("inbox exit status for schedulers (issue 97)")
    x = Box(cmd, root, "exit")
    _, ask, _ = x.send("ASK", "agent-a", "agent-b", "owed?")
    flag = ""
    for cand in ("--check", "--exit-code"):
        rc, _, err = x.run("inbox", "--to", "agent-b", cand)
        if not (rc == 2 and any(w in err.lower() for w in ("unrecognized", "unknown", "invalid", "usage:"))):
            flag = cand
            break
    if not flag:
        skip(OPT, "inbox exit status (--check / --exit-code)", "neither flag is accepted: feature not in this tool")
        return
    section(f"inbox exit status for schedulers (issue 97), using `inbox {flag}`")
    NOW = ["--now", "2030-01-01T00:00:00Z"]
    rc, _, _ = x.run("inbox", "--to", "agent-b", flag)
    check(OPT, f"inbox {flag}: an ASK owes a reply: exit 10", rc == 10, f"rc={rc}")
    rc, _, _ = x.run("inbox", "--to", "agent-a", flag)
    check(OPT, f"inbox {flag}: nothing owed to this identity: exit 0", rc == 0, f"rc={rc}")
    for label, extra in (("--type NOTICE", ["--type", "NOTICE"]), ("--limit 1", ["--limit", "1"]), ("--json", ["--json"])):
        rc, _, _ = x.run("inbox", "--to", "agent-b", flag, *extra)
        check(OPT, f"inbox {flag} {label}: filters and format never change the answer (still 10)", rc == 10, f"rc={rc}")
    a_out = x.run("inbox", "--to", "agent-b", *NOW)
    f_out = x.run("inbox", "--to", "agent-b", flag, *NOW)
    check(OPT, f"output with {flag} is identical to output without it (stdout and stderr)",
          a_out[1] == f_out[1] and a_out[2] == f_out[2] and a_out[1] != "", f"{a_out[1][:60]!r} vs {f_out[1][:60]!r}")
    check(OPT, f"without {flag} an owed ASK still exits 0 (default statuses unchanged)", a_out[0] == 0, f"rc={a_out[0]}")
    x.send("ANSWER", "agent-b", "agent-a", "re", "--reply-to", ask)
    rc, _, _ = x.run("inbox", "--to", "agent-b", flag)
    check(OPT, f"inbox {flag}: after the ANSWER nothing is owed: exit 0", rc == 0, f"rc={rc}")
    rc, _, _ = x.run("inbox", flag)
    check(OPT, f"inbox {flag} with no identity: exit 2 (cannot tell)", rc == 2, f"rc={rc}")
    rc, _, _ = x.run("inbox", "--to", "agent-b", flag, box=root / "nowhere")
    check(OPT, f"inbox {flag} with no mailbox: exit 2, nothing created", rc == 2 and not (root / "nowhere").exists(), f"rc={rc}")
    write_bytes(x.dir / "01AAAAAAAAAAAAAAAAAAAAAAAA-bad.md", b"---\nid: 01AAAAAAAAAAAAAAAAAAAAAAAA\nfrom: x\xff\n---\n")
    rc, _, _ = x.run("inbox", "--to", "agent-a", flag)
    check(OPT, f"inbox {flag}: a quarantined file and nothing known owed: exit 2", rc == 2, f"rc={rc}")
    x.send("ASK", "agent-a", "agent-b", "owed again")
    rc, _, err = x.run("inbox", "--to", "agent-b", flag)
    check(OPT, f"inbox {flag}: owed work beside a quarantined file is still 10", rc == 10, f"rc={rc} {err[:80]}")
    rc, _, _ = x.run("inbox", "--to", "agent-b")
    check(OPT, f"without {flag} a quarantined file still exits 2 (default statuses unchanged)", rc == 2, f"rc={rc}")
    u = Box(cmd, root, "unreadable")
    u.send("ASK", "agent-a", "agent-b", "hidden")
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        skip(OPT, f"inbox {flag} on an unlistable mailbox", "needs POSIX permissions and a non-root user")
    else:
        os.chmod(u.dir, 0)
        try:
            try:
                os.listdir(u.dir)
                listable = True
            except OSError:
                listable = False
            if listable:
                skip(OPT, f"inbox {flag} on an unlistable mailbox", "this filesystem ignored chmod 000")
            else:
                rc, _, _ = u.run("inbox", "--to", "agent-b", flag)
                check(OPT, f"inbox {flag}: an unlistable mailbox is 'cannot tell' (2), never 0", rc == 2, f"rc={rc}")
        finally:
            os.chmod(u.dir, 0o700)


def main() -> int:
    if sys.version_info < (3, 10):
        print("run_conformance.py needs Python 3.10 or newer (this is %d.%d); run it with a newer interpreter, "
              "for example `python3.12 conformance/run_conformance.py`" % sys.version_info[:2], file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 epilog="--cmd must come last: everything after it is the program and its arguments.")
    ap.add_argument("--tool", type=Path, default=DEFAULT_TOOL, help="path to agent_mail.py (run with this Python)")
    ap.add_argument("--protocol-only", action="store_true", help="run only the protocol-level group")
    ap.add_argument("--json-out", type=Path, metavar="FILE", help="also write the results as JSON")
    ap.add_argument("--cmd", nargs=argparse.REMAINDER, metavar="PROGRAM",
                    help="run this program instead of `python TOOL`, e.g. --cmd node adapter.js")
    args = ap.parse_args()
    cmd = list(args.cmd) if args.cmd else [sys.executable, str(args.tool.resolve())]
    global ONLY_P
    ONLY_P = args.protocol_only
    if not args.cmd and not args.tool.is_file():
        print(f"cannot run: no such file: {args.tool}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="pbconf_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        SCRUB.extend([str(root), str(root.resolve())])
        probe = Box(cmd, root, "probe")
        rc, _, err = probe.run("--version")
        if rc == -1:
            print(f"cannot run the tool: {_clean(err)}", file=sys.stderr)
            return 2
        shown = " ".join(Path(c).name if os.path.isabs(c) else c for c in cmd)
        print(f"tool under test: {shown}   (--version exit {rc})")
        crashed = ""
        try:
            protocol_checks(cmd, root)
            if not args.protocol_only:
                optional_checks(cmd, root)
        except Exception as exc:   # a runner bug or a tool so broken a step cannot be set up
            crashed = f"{type(exc).__name__}: {_clean(exc)}"
        shutil.rmtree(root / "probe", ignore_errors=True)
    failed = 0
    for g in (PROTO, IMPL, OPT):
        if args.protocol_only and g != PROTO:
            continue
        print(f"\n== {TITLES[g]} ==")
        last = None
        for r in (r for r in RESULTS if r["group"] == g):
            if r["section"] != last:
                last = r["section"]
                print(last)
            print(f"  {r['status']}  {r['name']}" + (f"   [{r['detail']}]" if r["detail"] else ""))
    print()
    for g in (PROTO, IMPL, OPT):
        if args.protocol_only and g != PROTO:
            continue
        rs = [r for r in RESULTS if r["group"] == g]
        f = [r for r in rs if r["status"] == "FAIL"]
        failed += len(f)
        print(f"{g}: {sum(r['status'] == 'PASS' for r in rs)} passed, {len(f)} failed, "
              f"{sum(r['status'] == 'SKIP' for r in rs)} skipped" + (f"   FAILED: {'; '.join(r['name'] for r in f)}" if f else ""))
    if crashed:
        print(f"the run stopped early ({crashed}); results above are partial", file=sys.stderr)
    print("all checks that ran passed" if not failed else f"{failed} check(s) failed")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"protocol_only": args.protocol_only, "failed": failed, "results": RESULTS}, fh, indent=2)
            fh.write("\n")
    return 2 if crashed and not failed else (1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
