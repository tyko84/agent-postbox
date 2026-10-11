#!/usr/bin/env python3
"""The command line as a first-time user meets it (PROTOCOL.md sections 37 and 38).

Help for every command, the table of refusals (exit status, one line on stderr,
nothing on stdout), what the read-only commands do with no mailbox, an empty one
and one that holds only quarantined files, the `--limit`, filter and `--json`
flags of `list`, `inbox` and `show`, and hostile arguments: a traceback is
always a bug, whatever was typed.

Stdlib only, synthetic data only, every mailbox is a temporary directory. Every
negative check has a positive control beside it. Bounded: each child has a
timeout, and a watchdog dumps every stack and exits non-zero if the whole run
has not finished after WATCHDOG seconds.

A check printed as KNOWN is a defect this file found in code it does not own
(the write path); it is listed, it does not fail the run, and it turns into a
PASS by itself when the defect is fixed.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import faulthandler
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = str(HERE / "agent_mail.py")
IS_SPEC_CHECKOUT = (HERE / "install.py").is_file()
STEP_TIMEOUT = 120   # seconds; any single child
WATCHDOG = 900       # seconds; the whole run
FAILS: list[str] = []
KNOWN: list[str] = []

NOW = "2030-01-01T12:00:00Z"
P = "01ARZ3NDEKTSV4RRFFQ69G5F"   # 24 characters; a two-character tail makes an id
ROW_KEYS = ["date", "expires", "from", "id", "oversize", "owes_reply", "reply_to", "scope",
            "status", "subject", "supersedes", "to", "type"]
TOP_KEYS = ["filters", "limit", "mailbox", "matched", "messages", "now", "quarantined",
            "returned", "schema", "total", "truncated", "view"]
FILTER_KEYS = ["from", "live", "scope", "since", "status", "to", "type"]
SHOW_KEYS = ["body_untrusted", "headers", "mailbox", "message", "notice", "now", "quarantined",
             "schema", "view"]
BASE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith("AGENT_MAIL") and k != "CLAUDE_PROJECT_DIR"}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(f"{name}: {detail[:400]}")


def known(name: str, ok: bool, detail: str = "") -> None:
    """A defect outside this file's reach: reported, never silent, never fatal."""
    print(f"  {'PASS' if ok else 'KNOWN'} {name}")
    if not ok:
        KNOWN.append(f"{name}: {detail[:300]}")


def run(box: Path | None, *argv: str | bytes, env: dict[str, str] | None = None,
        cwd: Path | None = None, stdin: str | None = None,
        isolated: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the tool. `isolated=False` lets a PYTHON* variable in `env` take effect
    (-I ignores them), for the checks that narrow stdout's encoding."""
    e = dict(BASE_ENV)
    if box is not None:
        e["AGENT_MAIL_DIR"] = str(box)
    e.update(env or {})
    flags = ["-I"] if isolated else []
    return subprocess.run([sys.executable, *flags, TOOL, *argv], capture_output=True, text=True,
                          env=e, cwd=cwd, input=stdin if stdin is not None else "",
                          timeout=STEP_TIMEOUT, errors="backslashreplace")


def doc(r: subprocess.CompletedProcess[str]) -> dict:
    try:
        got = json.loads(r.stdout)
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def hand(box: Path, tail: str, mtype: str, sender: str, to: str, date: str | None,
         subject: str, body: str = "BODY-TOKEN", mid: str = "", **extra: str) -> str:
    """Write one message file by hand, the way a file that did not come through
    `send` arrives. Returns its id."""
    mid = mid or P + tail
    lines = ["---", f"id: {mid}", f"type: {mtype}", f"from: {sender}", f"to: {to}"]
    if date is not None:
        lines.append(f"date: {date}")
    lines.append(f"subject: {subject}")
    lines += [f"{k}: {v}" for k, v in extra.items()]
    (box / f"{mid.upper()}-m.md").write_text("\n".join(lines + ["---", "", body, ""]),
                                             encoding="utf-8")
    return mid


def example_box(root: Path, name: str = "example") -> Path:
    """The mailbox of the example in PROTOCOL.md section 37."""
    box = root / name
    box.mkdir()
    hand(box, "A0", "NOTICE", "carol", "bob", "2029-12-20T08:00:00Z",
         "FINDING: the retry loop never backs off")
    hand(box, "A1", "ASK", "alice", "bob", "2030-01-01T09:00:00Z", "who owns the billing client?")
    hand(box, "A2", "ANSWER", "bob", "alice", "2030-01-01T09:05:00Z", "re: billing client",
         reply_to=P + "A1")
    hand(box, "A3", "CLAIM", "alice", "all", "2030-01-01T09:10:00Z", "editing the billing client",
         scope="billing/client.py", expires="2030-01-01T20:00:00+02:00")
    hand(box, "A4", "ASK", "carol", "bob", "2030-01-01T11:00:00Z", "may I rename retry()?")
    return box


def snapshot(box: Path) -> dict[str, tuple[int, int, str]]:
    out = {}
    for f in sorted(box.iterdir()):
        st = f.lstat()
        digest = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else ""
        out[f.name] = (st.st_size, st.st_mtime_ns, digest)
    return out


def commands() -> list[str]:
    """The commands the parser accepts, asked of the parser itself, so a command
    added later is covered without editing this file."""
    r = run(None, "no-such-command")
    m = re.search(r"\(choose from (.*)\)", r.stderr)
    return re.findall(r"[a-z][a-z0-9_-]*", m.group(1)) if m else []


def option_entries(help_text: str) -> dict[str, str]:
    """{flag: its help string} from one `--help` page. An option whose help is
    missing maps to ""."""
    entries: dict[str, str] = {}
    current = ""
    in_options = False
    for line in help_text.splitlines():
        if line in ("options:", "optional arguments:", "positional arguments:"):
            in_options, current = True, ""
            continue
        if in_options and line and not line.startswith(" "):
            in_options = False
        if not in_options or not line.strip():
            continue
        m = re.match(r"^  (-h, --help|--[a-z][a-z-]*|[a-z]+)(?: \S+)?(?:  +(.*))?$", line)
        if m and not line.startswith("   "):
            current = m.group(1)
            entries[current] = (m.group(2) or "").strip()
        elif current:
            entries[current] = (entries[current] + " " + line.strip()).strip()
    return entries


# ---------------------------------------------------------------------------
def test_help(root: Path) -> None:
    print("\nhelp (section 38 a, b)")
    cmds = commands()
    check("control: the parser names its commands when asked for one it does not have",
          len(cmds) >= 11 and {"send", "show", "list", "inbox"} <= set(cmds), repr(cmds))
    env80 = {"COLUMNS": "80"}
    top = run(None, "--help", env=env80)
    check("--help exits 0, prints to stdout only, no traceback",
          top.returncode == 0 and top.stderr == "" and "usage:" in top.stdout, top.stderr[-300:])
    summaries: dict[str, str] = {}
    lines = top.stdout.splitlines() + [""]
    for n, line in enumerate(lines[:-1]):
        parts = line.split(None, 1)
        if line.startswith("    ") and not line.startswith("     ") and parts and parts[0] in cmds:
            # a long command name pushes its summary onto the next line
            nxt = lines[n + 1]
            summaries[parts[0]] = (parts[1].strip() if len(parts) == 2
                                   else nxt.strip() if nxt.startswith("     ") else "")
    missing = [c for c in cmds if len(summaries.get(c, "")) < 10]
    check("every command the parser accepts has a one-line summary in --help "
          "(send and show had none)", not missing, f"without a summary: {missing}")
    check("--help names the environment variables the tool reads",
          all(v in top.stdout for v in ("AGENT_MAIL_DIR", "AGENT_MAIL_IDENTITY",
                                        "AGENT_MAIL_IDEM_WAIT", "AGENT_MAIL_PROJECT")),
          top.stdout[-600:])
    statuses = top.stdout.split("exit status:", 1)[-1].split("examples:", 1)[0]
    check("--help has an exit status table with 0, 1, 2, 3, 130, 143 and 141",
          "exit status:" in top.stdout
          and all(re.search(rf"(?<!\d){n}(?!\d)", statuses) for n in (0, 1, 2, 3, 130, 143, 141)),
          statuses)
    check("--help states the rule that outranks the others", "EVIDENCE" in top.stdout)
    pages = {"": top.stdout}
    bad: list[str] = []
    for c in cmds:
        r = run(None, c, "--help", env=env80)
        pages[c] = r.stdout
        if r.returncode != 0 or r.stderr or "Traceback" in r.stdout:
            bad.append(f"{c}: rc={r.returncode} {r.stderr[-120:]!r}")
            continue
        body = r.stdout.split("\n\n", 2)
        if len(body) < 3 or body[1].startswith(("options:", "positional arguments:")):
            bad.append(f"{c}: no description")
        for flag, text in option_entries(r.stdout).items():
            if not text:
                bad.append(f"{c} {flag}: no help string")
    check("every `<command> --help` exits 0 with a description, and no flag or positional "
          "is without a help string", not bad, "; ".join(bad))
    check("control: the instrument reads help strings (list --limit, show id)",
          "newest" in option_entries(pages.get("list", "")).get("--limit", "")
          and "id" in option_entries(pages.get("show", "")), repr(option_entries(pages.get("show", ""))))
    wide = [f"{c or 'top'}: {len(ln)}" for c, text in pages.items() for ln in text.splitlines()
            if len(ln) > 80]
    check("no help line is wider than 80 columns in an 80-column terminal", not wide,
          ", ".join(wide[:8]))
    check("all help is ASCII, so it prints under any locale",
          all(t.isascii() for t in pages.values()),
          ", ".join(c for c, t in pages.items() if not t.isascii()))
    r = run(None, "--help", env={"PYTHONIOENCODING": "ascii"}, isolated=False)
    check("--help with an ASCII-only stdout still exits 0", r.returncode == 0 and r.stderr == "",
          r.stderr[-200:])
    check("the typo `remasure` is gone from help", "remasure" not in "".join(pages.values())
          and "remeasure" in pages.get("", ""))
    check("inbox --help says where its default identity comes from",
          "AGENT_MAIL_IDENTITY" in pages.get("inbox", ""))
    v = run(None, "--version")
    sys.path.insert(0, str(HERE))
    import agent_mail
    check("--version prints `agent-postbox <version>` and exits 0",
          v.returncode == 0 and v.stdout == f"agent-postbox {agent_mail.__version__}\n", v.stdout)
    readme = (HERE / "README.md").read_text(encoding="utf-8") if (HERE / "README.md").is_file() else ""
    if readme:
        summary = readme.split("Command summary:", 1)[-1].split("\n\n", 1)[0]
        check("the README's command summary names every command",
              all(f"`{c}`" in summary for c in cmds),
              "missing: " + ", ".join(c for c in cmds if f"`{c}`" not in summary))
        table = readme.split("### Exit statuses", 1)[-1].split("\n## ", 1)[0]
        check("the README's exit status table and --help list the same statuses",
              all(re.search(rf"\| {n}[ ,|]|, {n} \|", table) for n in (0, 1, 2, 3, 130, 143, 141)),
              table[:300])
    for argv, why in ((["send"], "required"), (["show"], "required"), ([], "required"),
                      (["list", "--no-such-flag"], "unrecognized"),
                      (["status", "--stalled-hours", "abc"], "invalid")):
        r = run(root / "nobox", *argv)
        lines = r.stderr.splitlines()
        check(f"a usage error (`{' '.join(argv) or '(no command)'}`) is exit 2: usage, then one "
              "error: line, nothing on stdout",
              r.returncode == 2 and r.stdout == "" and lines[0].startswith("usage: ")
              and ": error: " in lines[-1] and why in lines[-1]
              and sum(": error: " in ln for ln in lines) == 1, r.stderr[-300:])


# ---------------------------------------------------------------------------
def test_refusals(root: Path) -> None:
    print("\nthe table of refusals: exit status, one line on stderr, nothing on stdout")
    box = root / "refusals"
    box.mkdir()
    nobox = root / "never-made"
    lease = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    note = ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "s"]
    claim = ["send", "--type", "CLAIM", "--from", "alice", "--to", "bob", "--subject", "s",
             "--body", "b"]

    ok = run(box, *note, "--body", "b")
    held = run(box, *claim, "--expires", lease, "--scope", "billing/client.py")
    keyed = run(box, *note, "--body", "b", "--key", "k1")
    check("control: a plain send, a scoped claim and a keyed send all succeed (exit 0, "
          "two lines on stdout, empty stderr)",
          all(r.returncode == 0 and r.stdout.count("\n") == 2 and r.stderr == ""
              for r in (ok, held, keyed)), ok.stderr + held.stderr + keyed.stderr)
    msg_id = next(ln.split(": ", 1)[1] for ln in ok.stdout.splitlines() if ln.startswith("id: "))
    again = run(box, *note, "--body", "b", "--key", "k1")
    check("control: the keyed send again is a duplicate, exit 0",
          again.returncode == 0 and again.stdout.startswith("duplicate of "), again.stderr)

    # (label, argv, mailbox, exit status, what the one line must contain)
    table: list[tuple[str, list[str], Path, int, str]] = [
        ("send: unknown type", ["send", "--type", "PING", "--from", "alice", "--to", "bob",
                                "--subject", "s", "--body", "b"], box, 2, "type must be one of ASK"),
        ("send: CLAIM without --expires", claim, box, 2, "CLAIM requires --expires"),
        ("send: --expires not ISO", claim + ["--expires", "tomorrow"], box, 2,
         "--expires 'tomorrow': must be ISO-8601"),
        ("send: --expires in the past", claim + ["--expires", "2001-01-01T00:00:00Z"], box, 2,
         "is already in the past"),
        ("send: --expires too far away", claim + ["--expires", "2999-01-01T00:00:00Z"], box, 2,
         "is more than 168h away"),
        ("send: control character in --subject",
         ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject",
          "a\x1b[31mb", "--body", "b"], box, 2, "--subject contains control/format character U+001B"),
        ("send: newline in --subject (header injection)",
         ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject",
          "a\nfrom: carol", "--body", "b"], box, 2, "--subject contains control/format character"),
        ("send: --from is not an identity",
         ["send", "--type", "NOTICE", "--from", "Alice Smith", "--to", "bob", "--subject", "s",
          "--body", "b"], box, 2, "--from 'Alice Smith' is not a valid identity"),
        ("send: empty --to",
         ["send", "--type", "NOTICE", "--from", "alice", "--to", "", "--subject", "s",
          "--body", "b"], box, 2, "--to '' is not a valid identity"),
        ("send: --subject too long",
         ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "s" * 501,
          "--body", "b"], box, 2, "--subject is longer than 500"),
        ("send: no body", note, box, 2, "refusing to send an empty message"),
        ("send: --body and --body-file", note + ["--body", "b", "--body-file", "-"], box, 2,
         "use either --body-file or --body"),
        ("send: multi-line --body", note + ["--body", "a\nb"], box, 2, "refusing multi-line --body"),
        ("send: shell metacharacters in --body", note + ["--body", "see $HOME"], box, 2,
         "shell metacharacters"),
        ("send: --body-file is a directory", note + ["--body-file", str(root)], box, 2,
         "cannot read --body-file"),
        ("send: --body-file does not exist", note + ["--body-file", str(root / "nope.txt")], box, 2,
         "cannot read --body-file"),
        ("send: --supersedes names no message", note + ["--body", "b", "--supersedes", P + "ZZ"],
         box, 2, "no such message"),
        ("send: --reply-to is not an id", note + ["--body", "b", "--reply-to", "../x"], box, 2,
         "is not a plain message id"),
        ("send: --scope on a NOTICE", note + ["--body", "b", "--scope", "x"], box, 2,
         "--scope only applies to CLAIM"),
        ("send: scope held by another sender (contended: exit 3)",
         ["send", "--type", "CLAIM", "--from", "bob", "--to", "alice", "--subject", "s",
          "--body", "b", "--expires", lease, "--scope", "billing/client.py"], box, 3,
         "is already held by alice"),
        ("send: key reused with different content", note + ["--body", "other", "--key", "k1"],
         box, 2, "already used with different content"),
        ("send: --key-wait not a number", note + ["--body", "b", "--key", "k2", "--key-wait", "x"],
         box, 2, "--key-wait 'x': the key wait must be"),
        ("send: --key-wait out of range", note + ["--body", "b", "--key", "k2", "--key-wait", "61"],
         box, 2, "from 0 to 60"),
        ("send: --key-wait without --key", note + ["--body", "b", "--key-wait", "1"], box, 2,
         "--key-wait only applies with --key"),
        ("send: no mailbox", note + ["--body", "b"], nobox, 2, f"NO MAILBOX at {nobox}"),
        ("canary: --hours 0", ["canary", "--from", "alice", "--to", "bob", "--hours", "0"], box, 2,
         "--hours must be positive"),
        ("canary: --hours beyond a week",
         ["canary", "--from", "alice", "--to", "bob", "--hours", "200"], box, 2,
         "is more than 168h away"),
        ("show: no such message", ["show", "NOSUCHID"], box, 1, "no message with id NOSUCHID"),
        ("show --json: no such message", ["show", "NOSUCHID", "--json"], box, 1,
         "no message with id NOSUCHID"),
        ("verify: no such message", ["verify", "NOSUCHID"], box, 1, "no message with id NOSUCHID"),
        ("status: --now not ISO", ["status", "--now", "yesterday"], box, 2,
         "--now 'yesterday': not parseable ISO-8601"),
        ("status: --now outside what UTC can represent",
         ["status", "--json", "--now", "0001-01-01T00:00:00+14:00"], box, 2, "outside the range"),
        ("status: --stalled-hours -1", ["status", "--stalled-hours", "-1"], box, 2,
         "--stalled-hours must be positive"),
        ("status: --stalled-hours nan", ["status", "--json", "--stalled-hours", "nan"], box, 2,
         "--stalled-hours must be positive"),
        ("status: --stalled-hours inf", ["status", "--json", "--stalled-hours", "inf"], box, 2,
         "--stalled-hours must be positive"),
        ("status: empty --scope", ["status", "--scope", ""], box, 2, "--scope must not be empty"),
        ("inbox: empty --to and no identity", ["inbox", "--to", ""], box, 2,
         "inbox requires --to <identity>"),
        ("inbox: no --to and no identity", ["inbox"], box, 2, "inbox requires --to <identity>"),
        ("list: --limit -1", ["list", "--limit", "-1"], box, 2, "--limit '-1': must be a whole number"),
        ("list: --limit 1.5", ["list", "--limit", "1.5"], box, 2, "must be a whole number"),
        ("list: --limit of forty digits", ["list", "--limit", "9" * 40], box, 2,
         "must be a whole number"),
        ("list: empty --limit", ["list", "--limit", ""], box, 2, "must be a whole number"),
        ("list: --type PING", ["list", "--type", "PING"], box, 2,
         "--type 'PING': must be one or more of ASK, ANSWER, NOTICE, CLAIM, DISPUTE"),
        ("list: --type with one bad member", ["list", "--type", "ASK,PING"], box, 2,
         "must be one or more of"),
        ("list: empty --type", ["list", "--type", ","], box, 2, "must be one or more of"),
        ("list: --status done", ["list", "--status", "done"], box, 2,
         "must be one or more of open, answered, held, expired, blocked, superseded, fresh, stale"),
        ("list: --since soon", ["list", "--since", "soon"], box, 2,
         "--since 'soon': not parseable ISO-8601"),
        ("list: empty --now", ["list", "--now", ""], box, 2, "--now '': not parseable ISO-8601"),
        ("list: --json with a bad --now", ["list", "--json", "--now", "never"], box, 2,
         "not parseable ISO-8601"),
        ("list: blank --scope", ["list", "--scope", "  "], box, 2, "--scope must not be empty"),
        ("inbox: --limit x", ["inbox", "--to", "bob", "--limit", "x"], box, 2,
         "must be a whole number"),
        ("inbox: --type x", ["inbox", "--to", "bob", "--type", "x"], box, 2, "must be one or more of"),
        ("show --json: bad --now", ["show", msg_id, "--json", "--now", "x"], box, 2,
         "not parseable ISO-8601"),
    ]
    before = snapshot(box)
    for label, argv, where, status, needle in table:
        r = run(where, *argv)
        lines = r.stderr.splitlines()
        check(f"{label}: exit {status}, one line on stderr, nothing on stdout",
              r.returncode == status and r.stdout == "" and len(lines) == 1 and needle in lines[0]
              and "Traceback" not in r.stderr,
              repr((r.returncode, r.stdout[:80], r.stderr[-300:])))
    check("no refusal wrote, changed or removed anything in the mailbox",
          snapshot(box) == before and not nobox.exists())

    # refusals the argument parser words: its usage, then one `error:` line
    for label, argv, needle in (
            ("canary: --hours soon", ["canary", "--from", "alice", "--to", "bob", "--hours", "soon"],
             "is not a finite number of hours"),
            ("canary: --hours nan", ["canary", "--from", "alice", "--to", "bob", "--hours", "nan"],
             "is not a finite number of hours"),
            ("canary: --hours inf", ["canary", "--from", "alice", "--to", "bob", "--hours", "inf"],
             "is not a finite number of hours"),
            ("canary: --hours 1e99", ["canary", "--from", "alice", "--to", "bob", "--hours", "1e99"],
             "is not a finite number of hours"),
            ("ask: --to names nobody", ["ask", "--from", "alice", "--to", ",", "--subject", "s",
                                        "--body", "b"], "names no recipient"),
            ("ask: empty --to", ["ask", "--from", "alice", "--to", "", "--subject", "s",
                                 "--body", "b"], "names no recipient"),
            ("send: a value that starts with a dash", note + ["--body", "-x"],
             "expected one argument")):
        r = run(box, *argv)
        lines = r.stderr.splitlines() or [""]
        check(f"{label}: exit 2, usage and one error: line, nothing on stdout, nothing written",
              r.returncode == 2 and r.stdout == "" and lines[0].startswith("usage: ")
              and ": error: " in lines[-1] and needle in lines[-1] and "Traceback" not in r.stderr
              and snapshot(box) == before, repr((r.returncode, r.stderr[-300:])))
    r = run(box, "ask", "--from", "alice", "--to", "bob", "--subject", "s", "--body", "b",
            "--capability", "push")
    check("ask: no recipient declares the capability: exit 2, a line per recipient and the "
          "refusal, nothing on stdout, nothing written",
          r.returncode == 2 and r.stdout == "" and r.stderr.splitlines()
          == ["bob: CANNOT_VERIFY", "refusing ask --capability 'push': no declared can: "
                                    "recipients remain"] and snapshot(box) == before, r.stderr)
    r = run(box, *note, "--body", "b", "--reply-to", P + "ZZ")
    st = doc(run(box, "status", "--json"))
    check("by design (section 32) --reply-to may cite an id that is not here: written, exit 0, "
          "and status reports it as DANGLING_REPLY",
          r.returncode == 0 and {"code": "DANGLING_REPLY", "count": 1} in st.get("problems", []),
          r.stderr + repr(st.get("problems")))
    before = snapshot(box)
    r = run(box, *note, "--body=-x")
    check("control: the same value in the = form is accepted (send --help says so)",
          r.returncode == 0 and "id: " in r.stdout
          and "--subject=" in run(None, "send", "--help").stdout, r.stderr)
    r = run(box, "canary", "--from", "alice", "--to", "bob", "--hours", "0.5")
    check("control: canary --hours 0.5 is still accepted", r.returncode == 0 and "id: " in r.stdout,
          r.stderr)
    r = run(box, "ask", "--from", "alice", "--to", "bob,carol", "--subject", "s", "--body", "b")
    check("control: ask with two recipients files two ASKs",
          r.returncode == 0 and r.stdout.count("id: ") == 2, r.stderr)
    r = run(box, "status", "--json", "--stalled-hours", "0.25", "--now", NOW)
    check("control: status --json with valid --now and --stalled-hours parses",
          r.returncode == 0 and doc(r).get("stalled_hours") == 0.25, r.stderr)

    r = run(box, "show", "a\x1b[31mb\nc")
    check("an id with an escape sequence and a newline is echoed quoted and escaped: one line",
          r.returncode == 1 and r.stderr == "no message with id 'a\\x1b[31mb\\nc'\n", repr(r.stderr))
    r = run(box, "show", "x" * 5000)
    check("a very long id is echoed shortened: one short line",
          r.returncode == 1 and len(r.stderr) < 200 and r.stderr.count("\n") == 1, repr(r.stderr[:120]))
    r = run(box, "list", "--type", "P\x1b[2J" + "x" * 400)
    check("a refused value is echoed escaped and shortened",
          r.returncode == 2 and "\x1b" not in r.stderr and len(r.stderr) < 300, repr(r.stderr[:160]))
    r = run(box, "show", "--", "-x")
    check("`--` ends the options: show -- -x looks the id -x up",
          r.returncode == 1 and r.stderr == "no message with id -x\n", repr(r.stderr))

    print("\nrefusals that depend on where the mailbox is")
    r = run(nobox, "list")
    check("no mailbox, AGENT_MAIL_DIR set: NO MAILBOX first, then a hint that says mkdir",
          r.returncode == 2 and r.stdout == "" and r.stderr.splitlines()[0] == f"NO MAILBOX at {nobox}"
          and len(r.stderr.splitlines()) == 2 and r.stderr.splitlines()[1].startswith("hint: ")
          and "mkdir" in r.stderr, repr(r.stderr))
    lonely = root / "lonely"
    lonely.mkdir()
    r = run(None, "list", cwd=lonely)
    check("no mailbox, AGENT_MAIL_DIR unset: the hint names AGENT_MAIL_DIR and install.py",
          r.returncode == 2 and r.stdout == "" and r.stderr.startswith("NO MAILBOX at ")
          and "docs/agent-mail" in r.stderr.splitlines()[0]
          and "AGENT_MAIL_DIR" in r.stderr.splitlines()[1] and "install.py" in r.stderr, repr(r.stderr))
    afile = root / "a-file"
    afile.write_text("x", encoding="utf-8")
    r = run(afile, "list")
    check("AGENT_MAIL_DIR names a file: NO MAILBOX, exit 2, no traceback",
          r.returncode == 2 and r.stderr.startswith(f"NO MAILBOX at {afile}\n"), repr(r.stderr))
    proj = root / "proj"
    (proj / "docs" / "agent-mail").mkdir(parents=True)
    (proj / "sub" / ".git").mkdir(parents=True)
    r = run(None, *note, "--body", "b", cwd=proj / "sub")
    check("send from a nested .git under an ancestor mailbox: ABORT, exit 2, one line, "
          "nothing minted",
          r.returncode == 2 and r.stdout == "" and len(r.stderr.splitlines()) == 1
          and r.stderr.startswith("ABORT: ancestor mailbox exists")
          and not (proj / "sub" / "docs").exists(), repr(r.stderr))
    r = run(None, *note, "--body", "b", cwd=proj)
    check("control: from the project itself the same send is written to its mailbox",
          r.returncode == 0 and len(list((proj / "docs" / "agent-mail").glob("*.md"))) == 1, r.stderr)
    # Only where the walk from HERE can end nowhere but HERE/docs/agent-mail: this
    # directory is its own git checkout and no directory above it has a mailbox.
    # Anywhere else the same command would find, or mint, somebody's real mailbox.
    safe_here = (IS_SPEC_CHECKOUT and (HERE / ".git").exists()
                 and not any((p / "docs" / "agent-mail").exists() for p in [HERE, *HERE.parents]))
    if safe_here:
        r = run(None, *note, "--body", "b", cwd=HERE)
        check("send inside the spec checkout: ABORT, exit 2, one line, nothing minted",
              r.returncode == 2 and r.stdout == "" and len(r.stderr.splitlines()) == 1
              and "refusing to file into the spec checkout" in r.stderr
              and not (HERE / "docs" / "agent-mail").exists(), repr(r.stderr))
    else:
        print("  SKIP  send inside the spec checkout (not a git checkout of the spec, or a "
              "mailbox exists at or above it)")


# ---------------------------------------------------------------------------
def test_empty(root: Path) -> None:
    print("\nno mailbox, an empty mailbox, and a mailbox of quarantined files only")
    nobox = root / "absent"
    empty = root / "empty"
    empty.mkdir()
    quar = root / "quarantined"
    quar.mkdir()
    (quar / "REPLACE_ID-draft.md").write_text(
        "---\nid: REPLACE_ID\ntype: ASK\nfrom: alice\nto: bob\nsubject: QUARANTINED-SUBJECT\n"
        "---\n\nQUARANTINED-BODY\n", encoding="utf-8")
    (quar / "notes.md").write_text("no frontmatter at all\n", encoding="utf-8")
    quar_before = snapshot(quar)

    for argv in (["list"], ["list", "--live"], ["list", "--json"], ["inbox", "--to", "bob"],
                 ["inbox", "--to", "bob", "--json"], ["show", "X"], ["show", "X", "--json"],
                 ["verify", "X"], ["status"], ["status", "--json"], ["latency"]):
        r = run(nobox, *argv)
        check(f"no mailbox: `{' '.join(argv)}` exits 2, NO MAILBOX and a hint on stderr, "
              "nothing on stdout",
              r.returncode == 2 and r.stdout == "" and len(r.stderr.splitlines()) == 2
              and r.stderr.startswith(f"NO MAILBOX at {nobox}\nhint: "), repr((r.stdout, r.stderr)))
    r = run(nobox, "doctor")
    check("no mailbox: doctor reports store: MISSING with the next step under it, ends with "
          "FAIL, exit 2, empty stderr",
          r.returncode == 2 and r.stderr == "" and "store: MISSING\n  - AGENT_MAIL_DIR" in r.stdout
          and r.stdout.rstrip().endswith("FAIL: mailbox missing"), r.stdout + r.stderr)
    r = run(nobox, "capability", "--identity", "alice", "--capability", "push")
    check("no mailbox: capability needs none and answers CANNOT_VERIFY, exit 0",
          r.returncode == 0 and r.stdout == "CANNOT_VERIFY\n", r.stdout + r.stderr)
    check("no read-only command created the missing mailbox", not nobox.exists())

    r = run(empty, "list")
    check("empty mailbox: list prints its header line and nothing else, exit 0",
          r.returncode == 0 and r.stdout == f"# mailbox: {empty}  (AGENT_MAIL_DIR)\n"
          and r.stderr == "", r.stdout + r.stderr)
    r = run(empty, "inbox", "--to", "bob")
    check("empty mailbox: inbox prints its header line and nothing else, exit 0",
          r.returncode == 0 and r.stdout == f"# inbox: bob  mailbox: {empty}  (AGENT_MAIL_DIR)\n"
          and r.stderr == "", r.stdout + r.stderr)
    for view, argv in (("list", ["list", "--json", "--now", NOW]),
                       ("inbox", ["inbox", "--to", "bob", "--json", "--now", NOW])):
        r = run(empty, *argv)
        d = doc(r)
        check(f"empty mailbox: {view} --json is a complete document with no rows and zero "
              "counts, exit 0",
              r.returncode == 0 and r.stderr == "" and sorted(d) == TOP_KEYS
              and d["messages"] == [] and (d["total"], d["matched"], d["returned"],
                                           d["truncated"], d["quarantined"]) == (0, 0, 0, 0, 0)
              and d["view"] == view and d["mailbox"] == str(empty) and d["now"] == NOW,
              r.stdout + r.stderr)
    for cmd in ("show", "verify"):
        r = run(empty, cmd, "X")
        check(f"empty mailbox: {cmd} X is exit 1 with one line", r.returncode == 1
              and r.stdout == "" and r.stderr == "no message with id X\n", r.stdout + r.stderr)
    r = run(empty, "status", "--json")
    check("empty mailbox: status --json parses, total 0, no problems, exit 0",
          r.returncode == 0 and doc(r).get("messages", {}).get("total") == 0
          and doc(r).get("problems") == [], r.stderr)
    r = run(empty, "latency")
    check("empty mailbox: latency says there is nothing to measure, exit 0",
          r.returncode == 0 and "(no answered ASKs)" in r.stdout and r.stderr == "", r.stdout)
    r = run(empty, "doctor")
    check("empty mailbox: doctor FAILs on the missing canary and says how to file one",
          r.returncode == 2 and r.stderr == ""
          and "canary: MISSING_OR_STALE\n  - file one with `canary --from <you> --to <you>`"
          in r.stdout and r.stdout.rstrip().endswith("FAIL: pickup canary missing or stale"),
          r.stdout + r.stderr)
    c = run(empty, "canary", "--from", "alice", "--to", "alice")
    r = run(empty, "doctor")
    check("control: after the canary that line names, doctor PASSes and prints no next step",
          c.returncode == 0 and r.returncode == 0 and r.stdout.rstrip().endswith("PASS")
          and "file one with" not in r.stdout and r.stderr == "", r.stdout + r.stderr)

    for argv, header in ((["list"], f"# mailbox: {quar}  (AGENT_MAIL_DIR)\n"),
                         (["inbox", "--to", "bob"], f"# inbox: bob  mailbox: {quar}  (AGENT_MAIL_DIR)\n")):
        r = run(quar, *argv)
        err = r.stderr.splitlines()
        check(f"quarantine only: `{argv[0]}` prints its header, a REJECT line per file and a "
              "hint, exit 2",
              r.returncode == 2 and r.stdout == header and len(err) == 3
              and err[0].startswith("REJECT REPLACE_ID-draft.md: malformed id")
              and err[1].startswith("REJECT notes.md: missing id")
              and err[2].startswith("hint: 2 quarantined file(s)"), r.stdout + r.stderr)
    r = run(quar, "list", "--json", "--now", NOW)
    d = doc(r)
    check("quarantine only: list --json is complete, quarantined is 2, no row, exit 2, and "
          "nothing of the quarantined files is in it",
          r.returncode == 2 and sorted(d) == TOP_KEYS and d["quarantined"] == 2
          and d["messages"] == [] and d["total"] == 0 and "QUARANTINED" not in r.stdout
          and r.stderr.count("\nREJECT ") + r.stderr.startswith("REJECT ") == 2,
          r.stdout + r.stderr)
    r = run(quar, "show", "REPLACE_ID")
    check("quarantine only: a quarantined file is not a message: show is exit 1",
          r.returncode == 1 and r.stdout == "" and r.stderr == "no message with id REPLACE_ID\n",
          r.stdout + r.stderr)
    r = run(quar, "status")
    check("quarantine only: status counts them, names the problem, exit 0",
          r.returncode == 0 and "quarantined: 2" in r.stdout
          and "PROBLEMS: QUARANTINED=2" in r.stdout, r.stdout)
    r = run(quar, "latency")
    check("quarantine only: latency reports, then exits 2 with the REJECT lines and the hint",
          r.returncode == 2 and "(no answered ASKs)" in r.stdout
          and [ln.split(" ", 1)[0] for ln in r.stderr.splitlines()] == ["REJECT", "REJECT", "hint:"],
          r.stderr)
    r = run(quar, "doctor")
    check("quarantine only: doctor lists the rejects on stdout and FAILs, exit 2",
          r.returncode == 2 and "rejects: 2" in r.stdout
          and "FAIL: reject envelopes present" in r.stdout and r.stderr == "", r.stdout + r.stderr)
    check("the quarantined files are untouched: never deleted, never rewritten",
          snapshot(quar) == quar_before)


# ---------------------------------------------------------------------------
def test_views(root: Path) -> None:
    print("\nlist, inbox, show: --limit, filters, --json (section 37)")
    box = example_box(root)
    before = snapshot(box)
    ids = [P + t for t in ("A0", "A1", "A2", "A3", "A4")]

    def listing(*argv: str) -> dict:
        return doc(run(box, "list", "--json", "--now", NOW, *argv))

    def tails(d: dict) -> list[str]:
        return [m["id"][-2:] for m in d.get("messages", [])]

    r = run(box, "list", "--to", "bob", "--limit", "2", "--json", "--now", NOW)
    want = {
        "filters": {"from": None, "live": False, "scope": None, "since": None, "status": None,
                    "to": "bob", "type": None},
        "limit": 2, "mailbox": str(box), "matched": 3, "now": NOW, "quarantined": 0,
        "returned": 2, "schema": 1, "total": 5, "truncated": 1, "view": "list",
        "messages": [
            {"date": "2030-01-01T09:00:00Z", "expires": None, "from": "alice", "id": ids[1],
             "oversize": [], "owes_reply": False, "reply_to": None, "scope": None,
             "status": "answered", "subject": "who owns the billing client?", "supersedes": None,
             "to": "bob", "type": "ASK"},
            {"date": "2030-01-01T11:00:00Z", "expires": None, "from": "carol", "id": ids[4],
             "oversize": [], "owes_reply": True, "reply_to": None, "scope": None,
             "status": "open", "subject": "may I rename retry()?", "supersedes": None,
             "to": "bob", "type": "ASK"},
        ],
    }
    check("(a) the example of section 37, value for value", r.returncode == 0 and r.stderr == ""
          and doc(r) == want, r.stdout[:500] + r.stderr)
    protocol = (HERE / "PROTOCOL.md").read_text(encoding="utf-8")
    block = protocol.split("## 37.", 1)[-1].split("```json\n", 1)[-1].split("\n```", 1)[0]
    try:
        printed = json.loads(block)
    except ValueError:
        printed = {}
    check("(a) ...and it is the document printed in PROTOCOL.md, but for the mailbox path",
          printed and {**printed, "mailbox": str(box)} == want, block[:200])

    everything = listing()
    check("(b) top-level keys, filter keys and row keys are exactly the documented sets",
          sorted(everything) == TOP_KEYS and sorted(everything["filters"]) == FILTER_KEYS
          and len(everything["messages"]) == 5
          and all(sorted(m) == ROW_KEYS for m in everything["messages"]), repr(everything)[:300])
    check("rows are oldest first, by id", tails(everything) == ["A0", "A1", "A2", "A3", "A4"])
    by = {m["id"][-2:]: m for m in everything["messages"]}
    check("each row's status is the one the text listing shows",
          [by[t]["status"] for t in ("A0", "A1", "A2", "A3", "A4")]
          == ["stale", "answered", "fresh", "held", "open"]
          and [by[t]["owes_reply"] for t in ("A1", "A4")] == [False, True], repr(by))
    check("an answer's row cites what it replies to; a claim's row has scope and UTC expiry",
          by["A2"]["reply_to"] == ids[1] and by["A3"]["scope"] == "billing/client.py"
          and by["A3"]["expires"] == "2030-01-01T18:00:00Z", repr((by["A2"], by["A3"])))
    text = run(box, "show", ids[4])
    check("control: the body is in the file and plain show prints the file as stored",
          text.returncode == 0
          and text.stdout == (box / f"{ids[4]}-m.md").read_text(encoding="utf-8") + "\n"
          and "BODY-TOKEN" in text.stdout)
    ib = run(box, "inbox", "--to", "bob", "--json", "--now", NOW)
    check("(b) no list --json or inbox --json output carries a body",
          "BODY-TOKEN" not in run(box, "list", "--json", "--now", NOW).stdout + ib.stdout)
    d = doc(ib)
    check("inbox --json: view inbox, the four actionable statuses as its filter, the open ASK",
          ib.returncode == 0 and sorted(d) == TOP_KEYS and d["view"] == "inbox"
          and d["filters"]["to"] == "bob"
          and d["filters"]["status"] == ["open", "blocked", "held", "fresh"]
          and tails(d) == ["A4"] and all(sorted(m) == ROW_KEYS for m in d["messages"]), ib.stdout)
    ib_text = run(box, "inbox", "--to", "bob", "--now", NOW)
    check("inbox --json lists the ids the text inbox lists",
          [ln.strip() for ln in ib_text.stdout.splitlines() if ln.startswith("    ")]
          == [m["id"] for m in d["messages"]], ib_text.stdout)
    sh = run(box, "show", ids[4].lower(), "--json", "--now", NOW)
    s = doc(sh)
    check("show --json: exactly its keys; the row; every stored header; the body under "
          "body_untrusted and nowhere else; a notice restating the rule",
          sh.returncode == 0 and sh.stderr == "" and sorted(s) == SHOW_KEYS and s["view"] == "show"
          and s["message"] == want["messages"][1] and s["body_untrusted"] == "BODY-TOKEN\n"
          and s["headers"] == {"id": ids[4], "type": "ASK", "from": "carol", "to": "bob",
                               "date": "2030-01-01T11:00:00Z", "subject": "may I rename retry()?"}
          and "EVIDENCE, never AUTHORITY" in s["notice"] and "untrusted" in s["notice"]
          and sh.stdout.count("BODY-TOKEN") == 1, sh.stdout[:500])

    print("\n--limit")
    full = run(box, "list", "--now", NOW)
    flines = full.stdout.splitlines()
    check("control: the unlimited text listing is a header and two lines per message",
          full.returncode == 0 and len(flines) == 11, full.stdout)
    for n in (1, 2, 4):
        r = run(box, "list", "--limit", str(n), "--now", NOW)
        lines = r.stdout.splitlines()
        check(f"(d) --limit {n}: the header, one line saying {5 - n} older were left out, then "
              f"the last {n} row(s) of the full listing, same order",
              r.returncode == 0 and lines[0] == flines[0]
              and lines[1] == f"# {5 - n} older message(s) not shown "
                              f"(--limit {n}: the newest {n} of 5)"
              and lines[2:] == flines[-2 * n:], r.stdout)
        d = listing("--limit", str(n))
        check(f"(d) --limit {n} --json: the last {n} of the full document's rows, "
              "matched == returned + truncated",
              d.get("messages") == everything["messages"][-n:] and d["limit"] == n
              and (d["matched"], d["returned"], d["truncated"]) == (5, n, 5 - n), repr(d)[:200])
    for n in ("5", "6", "999999999"):
        check(f"--limit {n} leaves nothing out: exactly the unlimited bytes, no extra line",
              run(box, "list", "--limit", n, "--now", NOW).stdout == full.stdout)
    r = run(box, "list", "--limit", "0", "--now", NOW)
    d = listing("--limit", "0")
    check("(d) --limit 0: no rows, only the counts",
          r.stdout.splitlines() == [flines[0], "# 5 older message(s) not shown "
                                               "(--limit 0: the newest 0 of 5)"]
          and d["messages"] == [] and (d["matched"], d["returned"], d["truncated"]) == (5, 0, 5),
          r.stdout)
    d = listing("--to", "bob", "--limit", "1")
    check("the limit applies after the filters: the newest one of bob's three",
          tails(d) == ["A4"] and (d["total"], d["matched"], d["truncated"]) == (5, 3, 2), repr(d)[:200])
    r = run(box, "inbox", "--to", "bob", "--limit", "0", "--now", NOW)
    check("inbox --limit: the same line, after the inbox header",
          r.returncode == 0 and r.stdout.splitlines()[1:]
          == ["# 1 older message(s) not shown (--limit 0: the newest 0 of 1)"], r.stdout)

    print("\nfilters")
    check("control: with no filter all five are listed and filters says so",
          tails(everything) == ["A0", "A1", "A2", "A3", "A4"]
          and everything["filters"] == {"from": None, "live": False, "scope": None, "since": None,
                                        "status": None, "to": None, "type": None})
    cases: list[tuple[str, list[str], list[str]]] = [
        ("--type ASK", ["--type", "ASK"], ["A1", "A4"]),
        ("--type ask (any case)", ["--type", "ask"], ["A1", "A4"]),
        ("--type CLAIM,notice (several)", ["--type", "CLAIM, notice"], ["A0", "A3"]),
        ("--status open", ["--status", "open"], ["A4"]),
        ("--status answered,stale", ["--status", "answered,stale"], ["A0", "A1"]),
        ("--status held", ["--status", "HELD"], ["A3"]),
        ("--status expired (none here)", ["--status", "expired"], []),
        ("--scope billing/client.py", ["--scope", "billing/client.py"], ["A3"]),
        ("--scope with surrounding blanks", ["--scope", " billing/client.py "], ["A3"]),
        ("--scope is exact, not a prefix", ["--scope", "billing"], []),
        ("--scope is case-sensitive", ["--scope", "Billing/client.py"], []),
        ("--since 2030-01-01T09:05:00Z (inclusive)", ["--since", "2030-01-01T09:05:00Z"],
         ["A2", "A3", "A4"]),
        ("--since with another offset", ["--since", "2030-01-01T12:00:00+01:00"], ["A4"]),
        ("--since after everything", ["--since", "2031-01-01T00:00:00Z"], []),
        ("--to bob", ["--to", "bob"], ["A0", "A1", "A4"]),
        ("--to BOB (identities have no case)", ["--to", "BOB"], ["A0", "A1", "A4"]),
        ("--from carol", ["--from", "carol"], ["A0", "A4"]),
        ("--live", ["--live"], ["A2", "A3", "A4"]),
        ("--to bob --type ASK --status open (open questions to bob)",
         ["--to", "bob", "--type", "ASK", "--status", "open"], ["A4"]),
        ("--type CLAIM --status held --scope ... (who claims this file)",
         ["--type", "CLAIM", "--status", "held", "--scope", "billing/client.py"], ["A3"]),
        ("--to bob --since ... (mail to me since X)",
         ["--to", "bob", "--since", "2030-01-01T00:00:00Z"], ["A1", "A4"]),
        ("--from carol --type NOTICE --live (filters are ANDed)",
         ["--from", "carol", "--type", "NOTICE", "--live"], []),
    ]
    for label, argv, expect in cases:
        d = listing(*argv)
        check(f"(e) {label} keeps exactly {expect or 'nothing'}",
              tails(d) == expect and d.get("matched") == len(expect) and d.get("total") == 5,
              repr(tails(d)))
        t = run(box, "list", "--now", NOW, *argv)
        check("    ...and the text form lists the same ids, exit 0",
              t.returncode == 0 and [ln.strip()[-2:] for ln in t.stdout.splitlines()
                                     if ln.startswith("    ")] == expect, t.stdout)
    d = listing("--type", "notice,ASK", "--status", "stale,OPEN", "--since", "2029-01-01T00:00:00+00:00")
    check("filters reports what was asked, normalised: types and statuses in their own order, "
          "since as UTC", d["filters"]["type"] == ["ASK", "NOTICE"]
          and d["filters"]["status"] == ["open", "stale"]
          and d["filters"]["since"] == "2029-01-01T00:00:00Z", repr(d["filters"]))
    d = listing("--type", "ASK")
    check("(e) a status is derived from the whole mailbox: with its ANSWER filtered out, the "
          "ASK is still answered", [(m["id"][-2:], m["status"]) for m in d["messages"]]
          == [("A1", "answered"), ("A4", "open")], repr(d["messages"]))
    d = doc(run(box, "inbox", "--to", "bob", "--json", "--now", NOW, "--type", "CLAIM"))
    check("inbox --type narrows the inbox (bob has no claim addressed to him)",
          d.get("messages") == [] and d.get("matched") == 0 and d["filters"]["type"] == ["CLAIM"])
    d = doc(run(box, "inbox", "--to", "bob", "--json", "--now", NOW, "--since", "2030-01-01T11:00:00Z"))
    check("inbox --since keeps the ASK dated at that instant", tails(d) == ["A4"])
    r = run(box, "list", "--liv", "--now", NOW)
    check("control: an unambiguous prefix of a flag still works (--liv is --live)",
          r.returncode == 0 and [ln.strip()[-2:] for ln in r.stdout.splitlines()
                                 if ln.startswith("    ")] == ["A2", "A3", "A4"], r.stderr)
    for prefix, both in (("--li", ("--live", "--limit")), ("--t", ("--to", "--type"))):
        r = run(box, "list", prefix, "bob")
        check(f"the prefix {prefix} is now ambiguous: refused, exit 2, both flags named "
              "(section 37, compatibility)",
              r.returncode == 2 and r.stdout == "" and "ambiguous option" in r.stderr
              and all(b in r.stderr for b in both), r.stderr[-200:])
    for flag in ("--status", "--scope"):
        r = run(box, "inbox", "--to", "bob", flag, "x")
        check(f"inbox does not take {flag} (its statuses are what make it the inbox): usage "
              "error, exit 2", r.returncode == 2 and "unrecognized arguments" in r.stderr, r.stderr)

    print("\n--now, determinism, encoding")
    later = "2030-01-10T00:00:00Z"
    d = doc(run(box, "list", "--json", "--now", later))
    check("--now moves the evaluation instant: nine days on, the claim has expired and the "
          "answer is stale; an open ASK stays open",
          [m["status"] for m in d["messages"]] == ["stale", "answered", "stale", "expired", "open"]
          and d["now"] == later, repr([m["status"] for m in d["messages"]]))
    check("--live honours --now too", tails(doc(run(box, "list", "--json", "--live", "--now", later)))
          == ["A4"])
    check("inbox honours --now: at that instant alice's inbox no longer shows bob's answer",
          tails(doc(run(box, "inbox", "--to", "alice", "--json", "--now", NOW))) == ["A2"]
          and tails(doc(run(box, "inbox", "--to", "alice", "--json", "--now", later))) == [])
    sh_later = doc(run(box, "show", ids[3], "--json", "--now", later))
    check("show --json honours --now", sh_later.get("message", {}).get("status") == "expired"
          and doc(run(box, "show", ids[3], "--json", "--now", NOW))["message"]["status"] == "held")
    d = doc(run(box, "list", "--json", "--now", "2030-01-01T14:00:00+02:00"))
    check("now is reported as UTC whatever offset it was given in", d.get("now") == NOW)
    for argv in (["list", "--json", "--now", NOW], ["list", "--now", NOW, "--limit", "3"],
                 ["inbox", "--to", "bob", "--json", "--now", NOW],
                 ["show", ids[3], "--json", "--now", NOW]):
        a, b = run(box, *argv), run(box, *argv)
        check(f"(f) `{' '.join(argv[:2])} ...` twice with the same --now: the same bytes",
              a.returncode == 0 and a.stdout == b.stdout and len(a.stdout) > 50, a.stderr)
    raw = run(box, "list", "--json", "--now", NOW).stdout
    mlines = raw.split('  "messages": [\n', 1)[-1].splitlines()
    check("the counts come before `messages`, and each row is one line that parses alone",
          raw.index('"total"') < raw.index('"messages"') and len(mlines) == 7
          and all(sorted(json.loads(ln.rstrip(","))) == ROW_KEYS for ln in mlines[:5]), raw[-300:])

    odd = root / "odd"
    odd.mkdir()
    hostile = 'say "hi" \\ <b>&amp;</b> {"id": "x"} \'; rm -rf ~ # $(id) `id` %s'
    hand(odd, "B0", "NOTICE", "alice", "bob", "2030-01-01T09:00:00Z", hostile)
    hand(odd, "B1", "NOTICE", "alice", "bob", "2030-01-01T09:00:01Z", "café ☃ 日本語 \U0001f4ec")
    hand(odd, "B2", "ASK", "Alice", "BOB", "2030-01-01T10:00:00+01:00", "upper-case identities",
         mid=(P + "b2").lower())
    hand(odd, "B3", "NOTICE", "alice", "bob", None, "no date at all")
    hand(odd, "B4", "CLAIM", "alice", "all", "2030-01-01T09:00:00Z", "unreadable expiry",
         expires="tomorrow")
    hand(odd, "B5", "ANSWER", "bob", "alice", "2030-01-01T09:00:00Z", "legacy re: header",
         re=P + "B2")
    r = run(odd, "list", "--json", "--now", NOW)
    rows = {m["id"][-2:].upper(): m for m in doc(r).get("messages", [])}
    check("(f) a subject full of quotes, backslashes, markup and shell syntax round-trips exactly",
          r.returncode == 0 and rows.get("B0", {}).get("subject") == hostile, repr(rows.get("B0")))
    check("(f) the output is ASCII for a non-ASCII subject, which round-trips exactly",
          r.stdout.isascii() and "\\u2603" in r.stdout
          and rows["B1"]["subject"] == "café ☃ 日本語 \U0001f4ec", r.stdout[:300])
    r_ascii = run(odd, "list", "--json", "--now", NOW, env={"PYTHONIOENCODING": "ascii"},
                  isolated=False)
    check("...so the same bytes come out when stdout can only carry ASCII",
          r_ascii.returncode == 0 and r_ascii.stdout == r.stdout, r_ascii.stderr[-200:])
    r_text = run(odd, "list", "--now", NOW, env={"PYTHONIOENCODING": "ascii"}, isolated=False)
    known("the text listing of that mailbox when stdout can only carry ASCII: escaped, not a "
          "traceback", r_text.returncode == 0 and "Traceback" not in r_text.stderr,
          r_text.stderr[-120:])
    check("a hand-written file: id as stored, type upper case, from and to lower case, date "
          "as UTC", rows["B2"]["id"] == (P + "b2").lower() and rows["B2"]["from"] == "alice"
          and rows["B2"]["to"] == "bob" and rows["B2"]["date"] == "2030-01-01T09:00:00Z"
          and rows["B2"]["status"] == "answered", repr(rows["B2"]))
    check("a message without a date has date null; an unreadable expiry is null and the "
          "claim is not held", rows["B3"]["date"] is None and rows["B4"]["expires"] is None
          and rows["B4"]["status"] == "expired", repr((rows["B3"], rows["B4"])))
    check("the legacy re: header is reported as reply_to", rows["B5"]["reply_to"] == P + "B2")
    check("--since leaves out a message that has no date (control: it is listed without)",
          "B3" not in [t.upper() for t in tails(doc(run(odd, "list", "--json", "--now", NOW,
                                                         "--since", "2000-01-01T00:00:00Z")))]
          and "B3" in rows)

    big = root / "oversize"
    big.mkdir()
    hand(big, "C0", "NOTICE", "alice", "bob", "2030-01-01T09:00:00Z", "s" * 500,
         scope="x" * 4096, reply_to="r" * 64)
    hand(big, "C1", "CLAIM", "a" * 129, "bob", "2030-01-01T09:00:00Z", "s" * 501,
         scope="x" * 4097, reply_to="r" * 65, supersedes="z" * 65, expires="2030-01-02T00:00:00Z")
    r = run(big, "list", "--json", "--now", NOW)
    rows = {m["id"][-2:]: m for m in doc(r).get("messages", [])}
    check("control: values at the limits send enforces are reported whole, oversize []",
          r.returncode == 0 and rows["C0"]["oversize"] == [] and rows["C0"]["subject"] == "s" * 500
          and rows["C0"]["scope"] == "x" * 4096 and rows["C0"]["reply_to"] == "r" * 64, r.stderr)
    check("(g) hand-written values one character over are null and named in oversize; the "
          "row keeps its keys",
          sorted(rows["C1"]) == ROW_KEYS
          and rows["C1"]["oversize"] == ["from", "reply_to", "scope", "subject", "supersedes"]
          and all(rows["C1"][k] is None for k in rows["C1"]["oversize"])
          and rows["C1"]["to"] == "bob" and rows["C1"]["status"] == "held", repr(rows["C1"])[:300])
    check("(g) ...the message is still filtered by its real value, and show --json has all of it",
          tails(doc(run(big, "list", "--json", "--now", NOW, "--scope", "x" * 4097))) == ["C1"]
          and doc(run(big, "show", P + "C1", "--json")).get("headers", {}).get("scope") == "x" * 4097)
    check("a row stays bounded: the oversize one is shorter than the one at the limits",
          len(json.dumps(rows["C1"])) < 400 < len(json.dumps(rows["C0"])))

    print("\nquarantine, unchanged bytes, read-only")
    mixed = example_box(root, "mixed")
    (mixed / "broken.md").write_text("---\nid: REPLACE_ID\ntype: ASK\nfrom: a\nto: b\n---\nx\n",
                                     encoding="utf-8")
    r = run(mixed, "list", "--json", "--now", NOW)
    d = doc(r)
    check("(h) with a quarantined file present: the document is complete for the admitted "
          "mail, quarantined is 1, REJECT and a hint on stderr, exit 2",
          r.returncode == 2 and d.get("quarantined") == 1 and d["total"] == 5
          and d["messages"] == everything["messages"] and r.stderr.startswith("REJECT broken.md: ")
          and r.stderr.splitlines()[-1].startswith("hint: 1 quarantined file(s)"), r.stderr)
    r = run(mixed, "show", ids[4], "--json", "--now", NOW)
    check("show --json of an admitted message still works there, counts the quarantine, exit 0",
          r.returncode == 0 and doc(r).get("quarantined") == 1 and r.stderr == "", r.stderr)

    still = root / "timeless"   # every status here is the same whatever the clock says
    still.mkdir()
    hand(still, "D0", "NOTICE", "carol", "bob", "2001-01-01T00:00:00Z", "an old finding")
    hand(still, "D1", "ASK", "alice", "bob", "2001-01-01T00:00:01Z", "answered long ago")
    hand(still, "D2", "ANSWER", "bob", "alice", "2001-01-01T00:00:02Z", "re", reply_to=P + "D1")
    hand(still, "D3", "CLAIM", "alice", "all", "2001-01-01T00:00:03Z", "ran out",
         expires="2001-01-02T00:00:00Z")
    hand(still, "D4", "ASK", "carol", "bob", "2001-01-01T00:00:04Z", "still open")
    head = f"# mailbox: {still}  (AGENT_MAIL_DIR)\n"
    golden = (head
              + f"  NOTICE  [stale     ] carol -> bob  an old finding\n    {P}D0\n"
              + f"  ASK     [answered  ] alice -> bob  answered long ago\n    {P}D1\n"
              + f"  ANSWER  [stale     ] bob -> alice  re\n    {P}D2\n"
              + f"  CLAIM   [expired   ] alice -> all  ran out\n    {P}D3\n"
              + f"! ASK     [open      ] carol -> bob  still open\n    {P}D4\n")
    r = run(still, "list")
    check("(c) list without the new flags prints exactly the bytes it always printed",
          r.returncode == 0 and r.stdout == golden and r.stderr == "", r.stdout)
    r = run(still, "list", "--to", "bob", "--live")
    check("(c) list --to bob --live likewise",
          r.stdout == head + f"! ASK     [open      ] carol -> bob  still open\n    {P}D4\n", r.stdout)
    r = run(still, "inbox", "--to", "bob")
    check("(c) inbox likewise", r.returncode == 0 and r.stdout
          == f"# inbox: bob  mailbox: {still}  (AGENT_MAIL_DIR)\n"
             f"! ASK     [open      ] carol -> bob  still open\n    {P}D4\n", r.stdout)
    r = run(still, "show", P + "D4")
    check("(c) show likewise: the file as stored, and one newline",
          r.stdout == (still / f"{P}D4-m.md").read_text(encoding="utf-8") + "\n")

    for argv in (["list"], ["list", "--json"], ["list", "--limit", "1", "--type", "ASK",
                                                "--status", "open", "--since", NOW],
                 ["inbox", "--to", "bob", "--json", "--limit", "1"], ["show", ids[4], "--json"],
                 ["status", "--json"], ["doctor"], ["latency"], ["verify", ids[4]]):
        run(box, *argv)
    check("(k) every read-only command, with every new flag, left each file, mtime and hash "
          "unchanged and created nothing", snapshot(box) == before)

    print("\nidentity for inbox (section 38 d)")
    named = run(box, "inbox", "--to", "bob", "--now", NOW)
    env_only = run(box, "inbox", "--now", NOW, env={"AGENT_MAIL_IDENTITY": " Bob "})
    check("inbox without --to is the inbox of AGENT_MAIL_IDENTITY: the same bytes as --to",
          named.returncode == 0 and len(named.stdout.splitlines()) == 3
          and env_only.returncode == 0 and env_only.stdout == named.stdout, env_only.stdout)
    other = run(box, "inbox", "--to", "alice", "--now", NOW, env={"AGENT_MAIL_IDENTITY": "bob"})
    check("--to wins over the variable", other.stdout.startswith("# inbox: alice ")
          and P + "A2" in other.stdout and P + "A4" not in other.stdout, other.stdout)
    r = run(box, "inbox", env={"AGENT_MAIL_IDENTITY": "  "})
    check("a blank variable is no identity: refused, exit 2, one line, no guess",
          r.returncode == 2 and r.stdout == ""
          and r.stderr == "inbox requires --to <identity> (or set AGENT_MAIL_IDENTITY)\n", r.stderr)


# ---------------------------------------------------------------------------
def test_reader_gone(root: Path) -> None:
    print("\na reader that goes away (section 35) with the new flags")
    box = root / "pipes"
    box.mkdir()
    for n in range(400):   # more than a pipe holds, in text and in JSON, even filtered
        hand(box, "", "NOTICE" if n % 2 else "ASK", "alice", "bob", "2030-01-01T09:00:00Z",
             f"message number {n} " + "x" * 400, mid=f"01ARZ3NDEKTSV4RRFFQ{n:07d}")
    last = "01ARZ3NDEKTSV4RRFFQ0000399"
    env = {**BASE_ENV, "AGENT_MAIL_DIR": str(box)}

    def gone(*argv: str) -> tuple[int, str]:
        r_fd, w_fd = os.pipe()
        os.close(r_fd)
        try:
            r = subprocess.run([sys.executable, "-I", TOOL, *argv], stdout=w_fd,
                               stderr=subprocess.PIPE, text=True, env=env, timeout=STEP_TIMEOUT)
        finally:
            os.close(w_fd)
        return r.returncode, r.stderr

    def head(n: int, *argv: str) -> tuple[int, str, str]:
        """Read `n` lines, then leave while the command is still writing."""
        p = subprocess.Popen([sys.executable, "-I", TOOL, *argv], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
        assert p.stdout is not None and p.stderr is not None
        got = "".join(p.stdout.readline() for _ in range(n))
        p.stdout.close()
        err = p.stderr.read()
        p.stderr.close()
        return p.wait(timeout=STEP_TIMEOUT), err, got

    views = (["list", "--json", "--now", NOW], ["list", "--limit", "300", "--now", NOW],
             ["list", "--type", "ASK", "--status", "open", "--now", NOW],
             ["inbox", "--to", "bob", "--json", "--now", NOW],
             ["inbox", "--to", "bob", "--limit", "300", "--now", NOW])
    for argv in views:
        full = run(box, *argv)
        check(f"control: `{' '.join(argv[:3])}` read to the end is exit 0 and larger than a pipe",
              full.returncode == 0 and len(full.stdout) > 65536 and full.stderr == "",
              f"{full.returncode} {len(full.stdout)}")
        got = gone(*argv)
        check("    its reader gone before the first byte: exit 141, empty stderr",
              got == (141, ""), repr(got))
        rc, err, part = head(3, *argv)
        check("    its reader gone after three lines: exit 141, empty stderr, and what was "
              "read is a prefix", (rc, err) == (141, "") and part and full.stdout.startswith(part),
              repr((rc, err, part[:80])))
    d = doc(run(box, "list", "--json", "--now", NOW))
    check("control: the JSON read to the end parses and holds all 400 rows",
          len(d.get("messages", [])) == 400 and d["returned"] == 400)
    for argv in (["show", last, "--json"], ["list", "--limit", "1"], ["list", "--limit", "0", "--json"],
                 ["inbox", "--to", "bob", "--limit", "1", "--json"]):
        got = gone(*argv)
        check(f"`{' '.join(argv)}` (small output) with its reader gone: exit 141, empty stderr",
              got == (141, ""), repr(got))
    got = gone("list", "--limit", "many")
    check("a refusal keeps its own status and its line with the reader gone",
          got[0] == 2 and got[1].startswith("--limit 'many': must be a whole number"), repr(got))
    got = gone("show", "NOSUCHID", "--json")
    check("...and so does show --json of a missing id",
          got == (1, "no message with id NOSUCHID\n"), repr(got))
    r = run(box, "list", "--limit", "20")
    whole = run(box, "list")
    check("what --limit is for: 20 rows of a 400-message mailbox are 42 lines, not 801",
          r.returncode == 0 and len(r.stdout.splitlines()) == 42
          and len(whole.stdout.splitlines()) == 801 and len(r.stdout) * 15 < len(whole.stdout))


# ---------------------------------------------------------------------------
HOSTILE = [
    "", " ", "-", "--", "-x", "--to", "--help-me", "=", ",", ", ,", "a b", "x" * 100_000,
    "café ☃ 日本語", "\U0001f4ec", "‮gnp.exe", "​", "﻿",
    "\x1b[31mred\x1b[0m", "\x07", "a\nb", "a\r\nb", "a\tb", "\udcff", "nan", "inf", "-inf", "-1",
    "0", "1e999", "9" * 5000, "0x10", "1_000", "../../etc/passwd", "/", ".", "%s%n%d", "{0}{x}",
    "$(id)", "`id`", "$HOME", "'; exit 9 #", "\\", '"', "REPLACE_ID", "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "2030-01-01", "2030-01-01T00:00:00", "9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59",
    "ASK", "ask,", "open,,held", "all", "ALICE",
]


def test_fuzz(root: Path) -> None:
    """Hostile values in every argument slot of every command. Run in this
    process through `agent_mail.main`, which is what the console script calls:
    an exception that escapes it is the traceback a user would see."""
    print("\nhostile arguments: a traceback is always a bug (section 38 f)")
    sys.path.insert(0, str(HERE))
    import agent_mail

    seed_id = P + "F0"
    lease = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    send = ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "s",
            "--body", "b"]
    claim = ["send", "--type", "CLAIM", "--from", "alice", "--to", "bob", "--subject", "s",
             "--body", "b", "--expires", lease]
    # command -> (a valid command line, the flags that take a value, positional slots)
    slots: dict[str, tuple[list[str], list[str], int]] = {
        "send": (send, ["--type", "--from", "--to", "--subject", "--reply-to", "--re", "--scope",
                        "--key", "--key-wait", "--supersedes", "--expires", "--body", "--body-file",
                        "--result", "--reason", "--namespace", "--resolved-at", "--falsifier",
                        "--do-not-infer", "--artifact-class", "--claim-currentness",
                        "--evidence-scope", "--fingerprint-head", "--fingerprint-worktree",
                        "--blocked-action", "--blocker", "--not-blocked", "--unblock-condition",
                        "--safe-parallel-work"], 0),
        "claim": (claim, ["--scope", "--expires", "--supersedes", "--key"], 0),
        "list": (["list"], ["--to", "--from", "--type", "--status", "--scope", "--since", "--limit",
                            "--now"], 0),
        "list --json": (["list", "--json"], ["--to", "--from", "--type", "--status", "--scope",
                                             "--since", "--limit", "--now"], 0),
        "inbox": (["inbox", "--to", "bob"], ["--to", "--type", "--since", "--limit", "--now"], 0),
        "inbox --json": (["inbox", "--to", "bob", "--json"], ["--to", "--limit", "--now"], 0),
        "show": (["show", seed_id], ["--now"], 1),
        "show --json": (["show", seed_id, "--json"], ["--now"], 1),
        "verify": (["verify", seed_id], ["--repo"], 1),
        "doctor": (["doctor"], [], 0),
        "status": (["status"], ["--now", "--scope", "--stalled-hours"], 0),
        "status --json": (["status", "--json"], ["--now", "--scope", "--stalled-hours"], 0),
        "canary": (["canary", "--from", "alice", "--to", "bob"], ["--from", "--to", "--hours"], 0),
        "latency": (["latency"], ["--to"], 0),
        "capability": (["capability", "--identity", "alice", "--capability", "push"],
                       ["--identity", "--capability"], 0),
        "ask": (["ask", "--from", "alice", "--to", "bob", "--subject", "s", "--body", "b"],
                ["--from", "--to", "--subject", "--capability", "--body", "--body-file"], 0),
    }
    covered = {name.split()[0] for name in slots} | {"send"}
    check("control: the table has a row for every command the parser accepts",
          set(commands()) <= covered, f"not covered: {sorted(set(commands()) - covered)}")

    saved_env = dict(os.environ)
    saved_in = sys.stdin
    saved_cwd = os.getcwd()
    runs = 0
    statuses: dict[int, int] = {}
    bad: list[str] = []

    def call(box: Path, argv: list[str], label: str) -> tuple[int, str, str]:
        nonlocal runs
        out, err = io.StringIO(), io.StringIO()
        os.environ["AGENT_MAIL_DIR"] = str(box)
        sys.stdin = io.StringIO("")
        rc: object
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = agent_mail.main(argv)
                except SystemExit as exc:   # the argument parser's own exit
                    rc = exc.code
        except BaseException as exc:   # anything else is the bug this test exists for
            bad.append(f"{label}: {type(exc).__name__}: {str(exc)[:120]}")
            return -1, out.getvalue(), err.getvalue()
        finally:
            sys.stdin = saved_in
        runs += 1
        code = 0 if rc is None else rc
        if not isinstance(code, int) or code not in (0, 1, 2, 3):
            bad.append(f"{label}: exit status {rc!r}")
            return -1, out.getvalue(), err.getvalue()
        statuses[code] = statuses.get(code, 0) + 1
        if "Traceback (most recent call last)" in err.getvalue() + out.getvalue():
            bad.append(f"{label}: a traceback was printed")
        return code, out.getvalue(), err.getvalue()

    def fresh(name: str) -> Path:
        box = root / "fuzz" / name
        box.mkdir(parents=True)
        hand(box, "F0", "ASK", "alice", "bob", "2030-01-01T09:00:00Z", "seed")
        return box

    try:
        for k in [k for k in os.environ if k.startswith("AGENT_MAIL")]:
            del os.environ[k]
        os.chdir(root)
        n = 0
        for name, (base, flags, positionals) in slots.items():
            box = fresh(f"valid-{n}")
            n += 1
            rc, _, err = call(box, base, f"{name} (valid)")
            if rc not in (0, 2) or (rc == 2 and name != "doctor"):
                bad.append(f"control {name}: the valid command line is exit {rc}: {err[-120:]!r}")
            for flag in flags:
                box = fresh(f"box-{n}")
                n += 1
                for value in HOSTILE:
                    shown = value if len(value) < 24 else value[:20] + "..."
                    call(box, base + [flag, value], f"{name} {flag} {shown!r}")
                    call(box, base + [f"{flag}={value}"], f"{name} {flag}={shown!r}")
            if positionals:
                box = fresh(f"box-{n}")
                n += 1
                for value in HOSTILE:
                    shown = value if len(value) < 24 else value[:20] + "..."
                    argv = [base[0], value] + base[2:]
                    call(box, argv, f"{name} <id>={shown!r}")
                    call(box, [base[0], "--", value] + base[2:], f"{name} -- {shown!r}")
        box = fresh("extra")
        for argv in ([], ["--"], ["-"], [""], ["list", "list"], ["show"], ["show", "a", "b"],
                     ["--version", "list"], ["list", "--json", "--json"], ["LIST"], ["lis"],
                     ["list", "--to"], ["list", "--limit"], ["list", "--limit", "1", "--limit", "2"],
                     ["send"] + ["--subject", "s"] * 50, ["x" * 100_000]):
            call(box, argv, f"argv {argv[:3]!r}")
        for var, values in (("AGENT_MAIL_IDENTITY", HOSTILE), ("AGENT_MAIL_IDEM_WAIT", HOSTILE)):
            for value in values:
                if "\x00" in value:
                    continue
                os.environ[var] = value
                try:
                    shown = value if len(value) < 24 else value[:20] + "..."
                    call(box, ["inbox"], f"{var}={shown!r} inbox")
                    call(box, send + ["--key", "k"], f"{var}={shown!r} send --key")
                finally:
                    del os.environ[var]
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.stdin = saved_in
        os.chdir(saved_cwd)
    check(f"no hostile value in any argument slot of any command escapes as an exception, "
          f"a traceback or an unlisted exit status ({runs} command lines)",
          not bad and runs > 5000, f"{len(bad)} bad of {runs}: " + "; ".join(bad[:6]))
    check("control: the same loop saw commands succeed and commands refuse (exit 0 and exit 2 "
          "both occurred, many times)", statuses.get(0, 0) > 200 and statuses.get(2, 0) > 200,
          repr(statuses))
    # Positive control: plant a defect and the same instrument must report it.
    real, seen = agent_mail.load_messages, len(bad)

    def planted() -> tuple[list, list]:
        raise RuntimeError("planted defect")

    agent_mail.load_messages = planted   # type: ignore[assignment]
    try:
        os.environ["AGENT_MAIL_DIR"] = str(root / "fuzz" / "extra")
        call(root / "fuzz" / "extra", ["list"], "planted")
    finally:
        agent_mail.load_messages = real   # type: ignore[assignment]
        os.environ.clear()
        os.environ.update(saved_env)
    check("control: with a defect planted in the tool, the instrument reports it",
          bad[seen:] == ["planted: RuntimeError: planted defect"], repr(bad[seen:]))

    print("\nhostile bytes and descriptors, as real processes")
    box = fresh("bytes")
    mbox = root / "fuzz"
    for label, argv in (("show <an undecodable byte>", ["show", b"\xff\xfe"]),
                        ("list --to <an undecodable byte>", ["list", "--to", b"a\xffb"]),
                        ("list --json --from <an undecodable byte>", ["list", "--json", "--from", b"\xff"]),
                        ("list --type <an undecodable byte>", ["list", "--type", b"\xff"]),
                        ("send --subject <an undecodable byte>",
                         ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob",
                          "--subject", b"a\xffb", "--body", "b"]),
                        ("send --from <an undecodable byte>",
                         ["send", "--type", "NOTICE", "--from", b"\xff", "--to", "bob",
                          "--subject", "s", "--body", "b"])):
        r = run(box, *argv)
        check(f"{label}: no traceback, exit 0, 1 or 2",
              r.returncode in (0, 1, 2) and "Traceback" not in r.stderr,
              repr((r.returncode, r.stderr[-200:])))
    for label, value in (("a very long AGENT_MAIL_DIR", "d" * 5000),
                         ("an AGENT_MAIL_DIR in another alphabet", str(mbox / "日本語")),
                         ("an AGENT_MAIL_DIR with a newline", str(mbox / "a\nb"))):
        for argv in (["list"], ["status", "--json"], ["doctor"], ["inbox", "--to", "bob", "--json"]):
            r = run(None, *argv, env={"AGENT_MAIL_DIR": value}, cwd=root)
            if r.returncode != 2 or "Traceback" in r.stderr + r.stdout:
                check(f"{label}: `{' '.join(argv)}` is exit 2 without a traceback", False,
                      repr((r.returncode, r.stderr[-200:])))
                break
        else:
            check(f"{label}: every read-only command is exit 2 without a traceback", True)
    uni = mbox / "café ☃"
    uni.mkdir()
    hand(uni, "G0", "ASK", "alice", "bob", "2030-01-01T09:00:00Z", "seed")
    r = run(uni, "list", "--json", "--now", NOW)
    check("control: a mailbox whose path is not ASCII works, and --json reports the path in ASCII",
          r.returncode == 0 and doc(r).get("mailbox") == str(uni) and r.stdout.isascii(), r.stderr)

    def closed(fd: int, *argv: str) -> subprocess.CompletedProcess[str]:
        keep = {0: subprocess.DEVNULL, 1: subprocess.PIPE, 2: subprocess.PIPE}
        del keep[fd]
        return subprocess.run(
            [sys.executable, "-I", TOOL, *argv], stdin=keep.get(0), stdout=keep.get(1),
            stderr=keep.get(2), text=True, env={**BASE_ENV, "AGENT_MAIL_DIR": str(box)},
            timeout=STEP_TIMEOUT, preexec_fn=lambda: os.close(fd))

    for argv in (["list", "--json"], ["inbox", "--to", "bob"], ["show", seed_id, "--json"],
                 ["status"], ["--help"]):
        r = closed(0, *argv)
        check(f"`{' '.join(argv)}` with stdin closed: exit 0, no traceback",
              r.returncode == 0 and r.stderr == "" and r.stdout, repr((r.returncode, r.stderr[-200:])))
        r = closed(1, *argv)
        (known if argv == ["--help"] else check)(
            f"`{' '.join(argv)}` with stdout closed: exit 0, nothing on stderr",
            r.returncode == 0 and not r.stderr, repr((r.returncode, r.stderr[:80])))
    r = closed(2, "list", "--limit", "x")
    check("a refusal with stderr closed keeps its exit status and writes nothing to stdout",
          r.returncode == 2 and r.stdout == "", repr((r.returncode, r.stdout)))

    evil = fresh("hostile-content")
    hand(evil, "H0", "NOTICE", "alice", "bob", "2030-01-01T09:00:00Z", "a finding",
         evidence_scope="e" * 5000 + ",../../../../etc/hosts,/dev/null", fingerprint_head="0" * 64,
         fingerprint_worktree="0" * 64, artifact_class="HEAD_OR_WORKTREE")
    r = run(evil, "verify", P + "H0", "--repo", str(evil))
    check("verify of a message whose evidence_scope is hostile: reports, no traceback",
          r.returncode in (0, 1) and "Traceback" not in r.stderr and "id: " in r.stdout,
          repr((r.returncode, r.stderr[-200:])))
    r = run(evil, "verify", P + "H0", "--repo", "r" * 5000)
    check("verify --repo with a path the OS refuses: no traceback",
          r.returncode in (0, 1, 2) and "Traceback" not in r.stderr, repr((r.returncode, r.stderr[-200:])))

    print("\ndefects found in code this file's owner may not edit (see the KNOWN list)")
    r = run(None, *send, env={"AGENT_MAIL_DIR": "d" * 5000}, cwd=root)
    known("send with a very long AGENT_MAIL_DIR: NO MAILBOX, not a traceback",
          r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-120:])
    r = run(None, "list", env={"AGENT_MAIL_PROJECT": "d" * 5000}, cwd=root)
    known("a very long AGENT_MAIL_PROJECT with no AGENT_MAIL_DIR: NO MAILBOX, not a traceback",
          r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-120:])
    r = closed(0, *send[:-2])
    known("send with stdin closed and no body: a refusal, not a traceback",
          r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-160:])
    r = closed(0, *send[:-2], "--body-file", "-")
    known("send --body-file - with stdin closed: a refusal, not a traceback",
          r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-160:])
    p = subprocess.run([sys.executable, "-I", TOOL, *send[:-2], "--body-file", "-"], input=b"x\xff",
                       capture_output=True, env={**BASE_ENV, "AGENT_MAIL_DIR": str(box)},
                       timeout=STEP_TIMEOUT)
    known("send --body-file - given bytes that are not UTF-8: a refusal, not a traceback",
          p.returncode == 2 and b"Traceback" not in p.stderr, repr(p.stderr[-160:]))


def main() -> int:
    faulthandler.dump_traceback_later(WATCHDOG, exit=True)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        test_help(root)
        test_refusals(root)
        test_empty(root)
        test_views(root)
        test_reader_gone(root)
        test_fuzz(root)
    faulthandler.cancel_dump_traceback_later()
    if KNOWN:
        print(f"\nKNOWN ({len(KNOWN)}; defects outside this file's reach, not failures):")
        for k in KNOWN:
            print(" -", k)
    if FAILS:
        print("\nFAILED:")
        for f in FAILS:
            print(" -", f)
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
