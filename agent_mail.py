#!/usr/bin/env python3
"""Inter-agent mail for a code repo. Stdlib only. See PROTOCOL.md.

    python agent_mail.py send --type ASK --from agent-a --to agent-b \
        --subject "..." [--re <id>] [--expires <iso>] --body-file F
    # F may be a path, or "-" to read the body from stdin.
    # Short single-line --body is allowed for tiny argv tests only; multi-line
    # bodies and shell metacharacters (` and $) must use --body-file / stdin
    # so an unquoted shell never expands the message into a side effect.
    python agent_mail.py list [--to X] [--from X] [--live]
    python agent_mail.py show <id>
    python agent_mail.py verify <id> [--repo PATH]

WHERE THE MAILBOX LIVES
-----------------------
`AGENT_MAIL_DIR` is the team inbox if set. Otherwise this clone's
`docs/agent-mail` after `install.py` (walk up from the working directory
the way git finds `.git`: nearest existing `docs/agent-mail` wins). This
repository is the spec, not the mailbox — do not file mail here.

A nested `.git` under an ancestor mailbox is occupied — `send` refuses to
mint a second box there (same rule as `install.py`). A `.git` with no
ancestor mailbox may create its own (first owner), except this spec
checkout: `send` will not mint `docs/agent-mail` next to `agent_mail.py`.
A missing store is a visible miss (`NO MAILBOX at <path>`, exit 2), not
an empty inbox. list/show/send all fail that way; AGENT_MAIL_DIR is never minted. Start of the walk is `AGENT_MAIL_PROJECT` or
`CLAUDE_PROJECT_DIR` (a host adapter env) if set, else cwd. This is
install/tooling, not the protocol.

DESIGN NOTES THAT ARE LOAD-BEARING
----------------------------------
* One message per file. Concurrent writers cannot clobber each other because
  there is no shared file to merge. Messages are never edited; you reply with
  a new file.

* Status is DERIVED, never stored. No `status:` field to drift. An ASK is open
  iff no later message cites its id; a CLAIM is held iff its expiry is still
  future. Ask the directory, not a field.

* CLAIM requires an expiry and `send` refuses without one. An agent that dies
  mid-session must not deadlock the repo forever, and eventually one will.

* "Should this be delivered?" and "does this owe a reply?" are DIFFERENT
  QUESTIONS. Conflating them is a real bug that has shipped: delivery gated on
  owes-a-reply drops every NOTICE, and the only symptom is no output —
  indistinguishable from an empty inbox. `live()` and `owes_reply()` are
  separate on purpose and must stay that way.
"""
from __future__ import annotations

import sys

# Version guard. `from __future__ import annotations` (above) lets the type
# hints in this file parse on old interpreters; this turns "SyntaxError
# traceback somewhere else" into one readable line. As a script it exits 2;
# as an import (the hook adapter) it raises ImportError, which callers that
# must stay silent already swallow.
MIN_PYTHON = (3, 10)
if sys.version_info < MIN_PYTHON:
    _why = (f"agent_mail requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ "
            f"(this is {sys.version_info[0]}.{sys.version_info[1]})")
    if __name__ == "__main__":
        print(_why, file=sys.stderr)
        raise SystemExit(2)
    raise ImportError(_why)

import argparse  # noqa: E402
import errno  # noqa: E402
import re  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import datetime as dt  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

TYPES = ("ASK", "ANSWER", "NOTICE", "CLAIM", "DISPUTE")
EXTENSION_TYPES: frozenset[str] = frozenset()
KNOWN_TYPES = frozenset(TYPES) | EXTENSION_TYPES
_ID_SORT_OLDEST = "\x00" * 26

# NOTICE/ANSWER/DISPUTE owe no reply, so nothing ever closes them. They are
# delivered while fresh and then age out, which bounds the inbox without
# storing a read receipt that could drift.
FRESH_DAYS = 7

__version__ = "0.3.0"

_SLUG = re.compile(r"[^a-z0-9]+")


MAILBOX_REL = Path("docs") / "agent-mail"


def discovery_root() -> Path:
    for key in ("AGENT_MAIL_PROJECT", "CLAUDE_PROJECT_DIR"):
        env = os.environ.get(key)
        if env and Path(env).is_dir():
            return Path(env).resolve()
    return Path.cwd().resolve()


def project_root() -> Path:
    start = discovery_root()
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return start


def mailbox_source() -> str:
    return "AGENT_MAIL_DIR" if os.environ.get("AGENT_MAIL_DIR") else "walk-up default"


def python_cmd() -> str:
    """How to invoke Python in commands we print for an agent to run.

    Prefer `python3`, then `python` (many macOS / Linux hosts have no bare
    `python`), falling back to this interpreter's absolute path, so a printed
    command is one that actually runs here.
    """
    for name in ("python3", "python"):
        if shutil.which(name):
            return name
    return sys.executable


def is_tool_checkout(root: Path) -> bool:
    """This spec repo (agent_mail.py + PROTOCOL.md at the same root), not an installed copy."""
    return (root / "agent_mail.py").is_file() and (root / "PROTOCOL.md").is_file()


def mailbox_in_tool_checkout(box: Path) -> bool:
    """True when `box` is the spec repo's own docs/agent-mail (the public artifact)."""
    resolved = box.expanduser().resolve()
    for parent in [resolved, *resolved.parents]:
        if is_tool_checkout(parent) and (parent / MAILBOX_REL).resolve() == resolved:
            return True
        if (parent / ".git").exists() and parent != resolved:
            break
    return False


def discover_mailbox() -> tuple[Path, bool]:
    """(path, may_create). may_create is False when minting would be a second owner."""
    env = os.environ.get("AGENT_MAIL_DIR")
    if env:
        # Explicit path must already exist. A typo must not mkdir a fourth root.
        return Path(env).expanduser(), False
    start = discovery_root()
    first_git: Path | None = None
    for parent in [start, *start.parents]:
        box = parent / MAILBOX_REL
        if box.is_dir():
            if first_git is not None:
                return first_git / MAILBOX_REL, False
            return box, True
        if (parent / ".git").exists() and first_git is None:
            first_git = parent
    if first_git is not None:
        return first_git / MAILBOX_REL, True
    return start / MAILBOX_REL, True


def mail_dir() -> Path:
    return discover_mailbox()[0]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _slug(text: str, limit: int = 48) -> str:
    s = _SLUG.sub("-", (text or "").lower()).strip("-")
    return (s[:limit].strip("-")) or "message"


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _new_ulid(when: dt.datetime | None = None) -> str:
    """Crockford Base32 ULID (26 chars). Stdlib only; sortable by created time."""
    when = when or _now()
    ms = int(when.timestamp() * 1000)
    if ms < 0 or ms >= 2**48:
        raise ValueError("ULID timestamp out of range")
    ts_chars: list[str] = []
    for _ in range(10):
        ts_chars.append(_CROCKFORD[ms & 31])
        ms >>= 5
    time_part = "".join(reversed(ts_chars))
    rand = int.from_bytes(os.urandom(10), "big")
    rp: list[str] = []
    for _ in range(16):
        rp.append(_CROCKFORD[rand & 31])
        rand >>= 5
    return time_part + "".join(reversed(rp))


def _is_ulid(value: str) -> bool:
    return len(value) == 26 and all(c in _CROCKFORD for c in value.upper())

def _is_ephemeral_body_ref(text: str) -> bool:
    """True when the body is only a path to ephemeral content (PROVEN LOSS)."""
    s = (text or "").strip()
    if not s or "\n" in s or "\r" in s:
        return False
    if s in ("/dev/stdin", "/dev/fd/0", "-"):
        return True
    lower = s.lower()
    if lower.startswith("/tmp/") or lower.startswith("/var/tmp/"):
        return True
    if lower.startswith("c:\\temp\\") or lower.startswith("c:/temp/"):
        return True
    if "/tmp/" in lower and " " not in s and len(s) < 512:
        if s.startswith("/") or (len(s) > 2 and s[1] == ":"):
            return True
    return False


def _is_path_looking_body(text: str) -> bool:
    """True when argv --body looks like a filesystem path (A REFERENCE ≠ A BODY)."""
    s = (text or "").strip()
    if not s or '\n' in s or '\r' in s:
        return False
    if any(c.isspace() for c in s):
        return False
    if s.startswith("/") or s.startswith("~/"):
        return True
    if s.startswith("./") or s.startswith("../"):
        return True
    if len(s) >= 3 and s[1] == ":" and s[0].isalpha() and s[2] in '\\/':
        return True
    if s.startswith('\\\\') or s.startswith("//"):
        return True
    return False

def predicate_lines_unbroadened(source: str, needle: str) -> bool:
    """True if needle appears on a line that is not OR-TRUE / OR 1=1 broadened."""
    if needle not in source:
        return False
    return any(
        needle in line and "OR TRUE" not in line and "OR 1=1" not in line
        for line in source.splitlines()
    )


def predicate_survives_broaden(source: str, needle: str) -> bool:
    """Apply a deliberate OR TRUE broaden mutant; return whether unbroadened lines remain.

    A naive substring check stays green under the mutant — this positive control
    must return False after broaden. Used by selftest / docs; not a SQL engine.
    """
    if needle not in source:
        return False
    mutant = source.replace(needle, f"({needle} OR TRUE)", 1)
    return predicate_lines_unbroadened(mutant, needle)


def derived_symbol_census(source: str, pattern: str) -> set[str]:
    """Derive symbols from source via regex — never assert == a hardcoded set."""
    return set(re.findall(pattern, source))

def message_id_raw(msg: Message) -> str:
    """Frontmatter id only — empty when missing (do not invent from filename)."""
    return (msg.meta.get("id") or "").strip()


def id_sort_key(msg: Message) -> tuple[int, str]:
    """Fail-closed sort: valid ULID ascending; missing/malformed → oldest."""
    raw = message_id_raw(msg)
    if _is_ulid(raw):
        return (1, raw.upper())
    return (0, _ID_SORT_OLDEST)


def type_ok(msg: Message) -> bool:
    return msg.type in KNOWN_TYPES


# --- INPUT POLICY (adversarial hardening) -------------------------------
#
# Two deliberately different postures, one per trust boundary:
#
#   SEND refuses.  `send` is the only sanctioned writer, so anything that could
#   forge or smear the envelope is rejected up front (exit 2, nothing written):
#   control/format characters in any header (newline = frontmatter injection,
#   ESC = terminal injection, RTL override / zero-width = spoofing), identities
#   outside [a-z0-9._@+-] (ASCII only: no homoglyphs, no path separators),
#   reply/supersedes references that are not plain tokens, control bytes in the
#   body, and oversized fields.  Subjects are stored NFC-normalised.
#
#   READ quarantines.  Files that did not come through `send` (hand edits, a
#   hostile writer with filesystem access) are untrusted.  Symlinks (never
#   followed: a link out of the box must not leak or inject a foreign file),
#   FIFOs/devices/directories, non-UTF-8, over-cap files, duplicate frontmatter
#   keys (last-wins would let a second `from:` silently override the first),
#   and control/format characters become a loud REJECT line and are never
#   admitted as mail.  Same closed-door rule as a malformed id.
#
# The read-side checks mirror the send-side ones so a message that `send`
# accepts is always one that `load_messages` admits.

MAX_SUBJECT_CHARS = 500
MAX_IDENT_CHARS = 128
MAX_FIELD_CHARS = 4096          # any other single header value
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024   # header + body ceiling when reading

_IDENT_RE = re.compile(r"^[a-z0-9][a-z0-9._@+-]*$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HEADER_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def _first_bad_char(text: str, categories: frozenset[str], allowed: str) -> str:
    """First character whose Unicode category is in `categories`, as 'U+XXXX', else ''."""
    for ch in text:
        if ch not in allowed and unicodedata.category(ch) in categories:
            return f"U+{ord(ch):04X}"
    return ""


def _header_char_error(value: str) -> str:
    bad = _first_bad_char(value, _HEADER_BAD_CATEGORIES, "\t")
    return f"contains control/format character {bad}" if bad else ""


# Cc (C0, DEL, C1) minus tab/LF/CR, plus lone surrogates (Cs). A regex, not a
# per-character loop: bodies can be megabytes.
_BODY_BAD_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]")


def _body_char_error(value: str) -> str:
    hit = _BODY_BAD_RE.search(value)
    return f"contains control character U+{ord(hit.group()):04X}" if hit else ""


def _read_mail_file(path: Path) -> tuple[str | None, str]:
    """Read one mailbox file without trusting it. Returns (text, "") or (None, reason).

    lstat refuses symlinks and non-regular files before opening; the open itself
    uses O_NOFOLLOW|O_NONBLOCK and re-checks with fstat so a swap between the
    check and the open (a planted link, a FIFO that would block forever) cannot
    get through.
    """
    try:
        st = os.lstat(path)
    except OSError as e:
        return None, f"unreadable ({e})"
    if stat.S_ISLNK(st.st_mode):
        return None, "symlink (never followed; a link out of the mailbox is not mail)"
    if not stat.S_ISREG(st.st_mode):
        return None, "not a regular file"
    if st.st_size > MAX_FILE_BYTES:
        return None, f"oversized ({st.st_size} bytes > {MAX_FILE_BYTES})"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        return None, f"unreadable ({e})"
    try:
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None, "not a regular file"
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError as e:
        return None, f"unreadable ({e})"
    if len(data) > MAX_FILE_BYTES:
        return None, f"oversized (> {MAX_FILE_BYTES} bytes)"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, f"not valid UTF-8 ({e.reason} at byte {e.start})"
    return text.replace("\r\n", "\n").replace("\r", "\n"), ""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str, str]:
    """(meta, body, problem). `problem` is non-empty when the file must be quarantined."""
    meta: dict[str, str] = {}
    body = text
    if not text.startswith("---"):
        return meta, body, ""
    end = text.find('\n---', 3)
    if end == -1:
        return meta, body, ""
    head = text[3:end]
    bad = _header_char_error(head.replace("\n", ""))
    if bad:
        return meta, body, f"header {bad}"
    for line in head.split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip().lower()
            if key in meta:
                return meta, body, f"duplicate frontmatter key {key!r} (last-wins would be a silent override)"
            meta[key] = v.strip()
    return meta, text[end + 4:].lstrip('\n'), ""


def load_messages() -> tuple[list[Message], list[str]]:
    """Read mailbox, sort by fail-closed id key, collect loud REJECT reasons."""
    out: list[Message] = []
    rejects: list[str] = []
    box = mail_dir()
    seen_ids: dict[str, str] = {}
    if not box.is_dir():
        return out, rejects
    for path in sorted(box.glob("*.md")):
        if path.name.lower() in ("readme.md", "participants.md", "protocol.md"):
            continue
        text, why = _read_mail_file(path)
        if text is None:
            rejects.append(f"REJECT {path.name}: {why}")
            continue
        meta, body, problem = _parse_frontmatter(text)
        if not problem:
            bad = _body_char_error(body)
            if bad:
                problem = f"body {bad}"
        if problem:
            rejects.append(
                f"REJECT {path.name}: {problem} "
                "(CANNOT_VERIFY; quarantined — never admitted as mail)"
            )
            continue
        msg = Message(path, meta, body)
        raw_id = message_id_raw(msg)
        # Fix A (persist/admit): missing/malformed/REPLACE_ID never enter pickup.
        # INVALID ≠ OLD/NEW — omit from ordered set (distinct from §22 sort).
        if not raw_id:
            rejects.append(
                f"REJECT {path.name}: missing id "
                "(CANNOT_VERIFY; not admitted — INVALID ≠ OLD/NEW)"
            )
            continue
        if raw_id.upper() == "REPLACE_ID" or not _is_ulid(raw_id):
            rejects.append(
                f"REJECT {path.name}: malformed id {raw_id!r} "
                "(CANNOT_VERIFY; not admitted — INVALID ≠ OLD/NEW)"
            )
            continue
        if not type_ok(msg):
            tname = msg.type or "(empty)"
            rejects.append(
                f"REJECT {path.name}: unknown type {tname!r} "
                "(CANNOT_VERIFY; closed set ASK/ANSWER/NOTICE/CLAIM/DISPUTE)"
            )
            continue  # quarantined: listed by doctor, never injected as live mail
        if not msg.sender or not msg.to:
            rejects.append(
                f"REJECT {path.name}: missing from/to "
                "(CANNOT_VERIFY; not admitted — nobody can be addressed)"
            )
            continue
        if "date" in meta and _parse_iso(meta["date"]) is None:
            rejects.append(
                f"REJECT {path.name}: unparseable date {meta['date']!r} "
                "(CANNOT_VERIFY; a message with no valid date never goes stale)"
            )
            continue
        if raw_id.upper() in seen_ids:
            rejects.append(
                f"REJECT {path.name}: duplicate id {raw_id} "
                f"(first seen in {seen_ids[raw_id.upper()]}; a reply or supersede "
                "citing it would be ambiguous)"
            )
            continue
        seen_ids[raw_id.upper()] = path.name
        out.append(msg)
    out.sort(key=id_sort_key)
    return out, rejects






def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


class Message:
    __slots__ = ("path", "meta", "body")

    def __init__(self, path: Path, meta: dict[str, str], body: str) -> None:
        self.path, self.meta, self.body = path, meta, body

    @property
    def id(self) -> str:
        # Canonical identity is frontmatter id (ULID for new mail). Filename is transport.
        return self.meta.get("id", self.path.stem)
    @property
    def type(self) -> str: return (self.meta.get("type") or "").upper()
    @property
    def sender(self) -> str: return (self.meta.get("from") or "").lower()
    @property
    def to(self) -> str: return (self.meta.get("to") or "").lower()
    @property
    def subject(self) -> str: return self.meta.get("subject", "")
    @property
    def reply_to(self) -> str:
        # Prefer reply_to; accept legacy re: for older mail.
        return self.meta.get("reply_to") or self.meta.get("re", "")
    @property
    def re_id(self) -> str:
        # Back-compat alias used by owes_reply / older callers.
        return self.reply_to
    @property
    def supersedes(self) -> str: return self.meta.get("supersedes", "")
    @property
    def sent_at(self) -> dt.datetime | None: return _parse_iso(self.meta.get("date"))
    @property
    def expires_at(self) -> dt.datetime | None: return _parse_iso(self.meta.get("expires"))


def read_all() -> list[Message]:
    """Compatibility: ordered messages only. Prefer load_messages for pickup."""
    msgs, _ = load_messages()
    return msgs

def may_supersede(msg: Message, target: Message) -> bool:
    """PROTOCOL.md sections 5 and 32: a CLAIM is superseded only by a CLAIM from
    its own sender. Checked when reading, not only at send, because a file that
    did not come through `send` must not be able to void somebody else's lease."""
    return target.type != "CLAIM" or (msg.type == "CLAIM" and msg.sender == target.sender)


class _Index:
    """One pass over `everything`: which ids are replied to / superseded.

    Pure derivation (nothing stored). It exists so list/inbox/status stay
    O(n) on a big box instead of re-scanning every message per message.
    """
    __slots__ = ("replied", "superseded_by", "foreign_supersedes")

    def __init__(self, everything: list[Message]) -> None:
        # Ids are Crockford Base32: compared without regard to case (section 32).
        self.replied: set[str] = {m.re_id.upper() for m in everything if m.re_id}
        self.superseded_by: dict[str, set[str]] = {}
        self.foreign_supersedes: list[str] = []  # ids of messages whose supersedes is ignored
        by_id = {m.id.upper(): m for m in everything}
        for m in everything:
            if not m.supersedes or m.supersedes.upper() == m.id.upper():
                continue
            target = by_id.get(m.supersedes.upper())
            if target is not None and not may_supersede(m, target):
                self.foreign_supersedes.append(m.id)
                continue
            self.superseded_by.setdefault(m.supersedes.upper(), set()).add(m.id.upper())

    def is_superseded(self, mid: str) -> bool:
        return bool(self.superseded_by.get(mid.upper(), set()) - {mid.upper()})


def owes_reply(msg: Message, everything: list[Message], _idx: _Index | None = None) -> bool:
    """Only an ASK owes a reply, and only until something cites its id.

    NOT a delivery test — see the module docstring.
    """
    if msg.type != "ASK":
        return False
    if _idx is not None:
        return msg.id.upper() not in _idx.replied
    return not any(m.re_id and m.re_id.upper() == msg.id.upper() for m in everything)


def live(msg: Message, everything: list[Message], now: dt.datetime | None = None,
         _idx: _Index | None = None) -> bool:
    """Should this still be put in front of its recipient? Deliberately
    separate from owes_reply(); this is the question delivery must ask."""
    now = now or _now()
    if msg.type == "ASK":
        return owes_reply(msg, everything, _idx)
    if msg.type == "CLAIM":
        exp = msg.expires_at
        return bool(exp and exp > now)
    sent = msg.sent_at
    return True if sent is None else (now - sent).days < FRESH_DAYS



def derived_status(
    msg: Message,
    everything: list[Message],
    now: dt.datetime | None = None,
    _idx: _Index | None = None,
) -> str:
    """Compute status from durable fields only — never a stored status string."""
    now = now or _now()
    mid = msg.id
    if _idx is not None:
        superseded = _idx.is_superseded(mid)
    else:
        superseded = any((m.supersedes or "").upper() == mid.upper() and may_supersede(m, msg)
                         for m in everything if m.id.upper() != mid.upper())
    if superseded:
        return "superseded"
    if msg.type == "CLAIM":
        exp = msg.expires_at
        if exp and exp > now:
            return "held"
        return "expired"
    if msg.type == "ASK":
        return "open" if owes_reply(msg, everything, _idx) else "answered"
    if (msg.meta.get("blocked_action") or "").strip():
        return "blocked"
    if live(msg, everything, now=now, _idx=_idx):
        return "fresh"
    return "stale"


def actionable(
    msg: Message,
    everything: list[Message],
    identity: str,
    now: dt.datetime | None = None,
    aliases: dict[str, list[str]] | None = None,
    _idx: _Index | None = None,
) -> bool:
    """True when the message belongs in the actionable inbox for identity."""
    now = now or _now()
    if not match_identity(msg.to, identity, aliases=aliases):
        return False
    return derived_status(msg, everything, now=now, _idx=_idx) in (
        "open", "blocked", "held", "fresh",
    )


def resolve_body(args: argparse.Namespace) -> str | None:
    """Load the message body without going through a shell.

    Real bodies come from ``--body-file`` (path or ``-`` for stdin). A short
    single-line ``--body`` remains for tiny argv selftests only. Multi-line
    argv bodies and argv bodies containing shell metacharacters are refused:
    those are exactly how an unquoted ``send --body "…`git merge`…"`` ran a
    merge as a side effect of filing mail. Refuse, do not write. Returns
    None after printing when the call must exit non-zero.
    """
    if args.body_file and args.body:
        print("use either --body-file or --body, not both", file=sys.stderr)
        return None
    if args.body_file:
        if args.body_file == "-":
            body = sys.stdin.read()
        else:
            try:
                body = Path(args.body_file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                print(f"cannot read --body-file {args.body_file!r}: {e}", file=sys.stderr)
                return None
        if _is_ephemeral_body_ref(body):
            print(
                "refusing ephemeral body ref (store must hold bytes, not a /tmp or "
                "/dev/stdin path); materialize the content before send",
                file=sys.stderr,
            )
            return None
        return body
    body = args.body or ""
    if not body:
        # Piped stdin with no flags: accept as body so agents can
        # ``… | agent_mail.py send …`` without inventing a temp file.
        if not sys.stdin.isatty():
            body = sys.stdin.read()
            if _is_ephemeral_body_ref(body):
                print(
                    "refusing ephemeral body ref (store must hold bytes, not a /tmp or "
                    "/dev/stdin path); materialize the content before send",
                    file=sys.stderr,
                )
                return None
            return body
        return ""
    if "\n" in body or "\r" in body:
        print(
            "refusing multi-line --body; pass --body-file PATH or --body-file - (stdin). "
            "Unquoted shell strings expand backticks and $() — that mangled a NOTICE "
            "and ran git merge as a side effect.",
            file=sys.stderr,
        )
        return None
    if "`" in body or "$" in body:
        print(
            "refusing --body that contains shell metacharacters (` or $); "
            "pass --body-file PATH or --body-file - so the body is never shell-expanded.",
            file=sys.stderr,
        )
        return None
    if _is_ephemeral_body_ref(body):
        print(
            "refusing ephemeral body ref (store must hold bytes, not a /tmp or "
            "/dev/stdin path); materialize the content before send",
            file=sys.stderr,
        )
        return None
    if _is_path_looking_body(body):
        print(
            "refusing path-looking --body (A REFERENCE ≠ A BODY); "
            "pass --body-file PATH or --body-file - so the store holds bytes, not a path string",
            file=sys.stderr,
        )
        return None
    return body


def structured_block_incomplete(fields: dict[str, str]) -> list[str]:
    """Return missing keys when any structured-block field is set without all five."""
    keys = (
        "blocked_action", "blocker", "not_blocked", "unblock_condition",
        "safe_parallel_work",
    )
    present = [k for k in keys if (fields.get(k) or "").strip()]
    if not present:
        return []
    return [k for k in keys if k not in present]



# --- participant capabilities (PROTOCOL.md §21) ---

_SOFT_CAN_TOKENS = frozenset({"*", "all", "any", "everything"})


def _split_cap_cell(cell: str) -> set[str]:
    """Parse a can/cannot cell into capability ids. Soft wildcards raise."""
    raw = (cell or "").strip().strip("`")
    if not raw:
        return set()
    out: set[str] = set()
    for part in re.split(r"[,;\s]+", raw):
        tok = part.strip().strip("`").lower()
        if not tok:
            continue
        if tok in _SOFT_CAN_TOKENS:
            raise ValueError(
                f"soft-default capability {tok!r} refused — declare explicit ids "
                "(omit-means-can / can:* looks like consent)"
            )
        out.add(tok)
    return out


def participants_paths() -> list[Path]:
    """Candidate PARTICIPANTS.md locations (mailbox, parent, cwd, tool dir)."""
    seen: list[Path] = []
    for p in (
        mail_dir() / "PARTICIPANTS.md",
        mail_dir().parent / "PARTICIPANTS.md",
        Path.cwd() / "PARTICIPANTS.md",
        Path(__file__).resolve().parent / "PARTICIPANTS.md",
    ):
        if p not in seen:
            seen.append(p)
    return seen


def load_participants(path: Path | None = None) -> dict[str, dict[str, set[str]]]:
    """Parse Identity / can / cannot from a PARTICIPANTS.md table.

    Returns {identity_lower: {"can": set, "cannot": set}}.
    Raises ValueError on soft-default can cells.
    """
    paths = [path] if path else participants_paths()
    text = None
    for p in paths:
        if p is None:
            continue
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            break
    if text is None:
        return {}

    rows: dict[str, dict[str, set[str]]] = {}
    # Find header to locate can/cannot column indices
    can_idx = cannot_idx = ident_idx = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        lower = [c.lower().strip("`*") for c in cells]
        if "identity" in lower and can_idx is None:
            try:
                ident_idx = lower.index("identity")
            except ValueError:
                continue
            can_idx = lower.index("can") if "can" in lower else None
            cannot_idx = lower.index("cannot") if "cannot" in lower else None
            continue
        if ident_idx is None:
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # separator
        if len(cells) <= ident_idx:
            continue
        ident = cells[ident_idx].strip().strip("`").lower()
        if not ident or ident == "identity":
            continue
        can: set[str] = set()
        cannot: set[str] = set()
        if can_idx is not None and can_idx < len(cells):
            can = _split_cap_cell(cells[can_idx])
        if cannot_idx is not None and cannot_idx < len(cells):
            cannot = _split_cap_cell(cells[cannot_idx])
        rows[ident] = {"can": can, "cannot": cannot}
    return rows



def _parse_aliases_text(text: str) -> dict[str, list[str]]:
    """Parse Alias/Writers table from one PARTICIPANTS.md body."""
    section = text
    marker = "## Aliases"
    if marker in text:
        section = text.split(marker, 1)[1]
        nxt = section.find(
            chr(10) + "## "
        )
        if nxt != -1:
            section = section[:nxt]

    alias_idx = writers_idx = None
    out: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        lower = [c.lower().strip("`*") for c in cells]
        if "alias" in lower and "writers" in lower and alias_idx is None:
            alias_idx = lower.index("alias")
            writers_idx = lower.index("writers")
            continue
        if alias_idx is None or writers_idx is None:
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) <= max(alias_idx, writers_idx):
            continue
        alias = cells[alias_idx].strip().strip("`").lower()
        if not alias or alias == "alias":
            continue
        writers_raw = cells[writers_idx].strip()
        writers: list[str] = []
        for part in re.split(r"[,;]+", writers_raw):
            w = part.strip().strip("`").lower()
            if w:
                writers.append(w)
        if writers:
            out[alias] = writers
    return out


def load_aliases(path: Path | None = None) -> dict[str, list[str]]:
    """Parse Alias → Writers from PARTICIPANTS.md Aliases table (§27).

    Returns {alias_lower: [writer_id_lower, ...]}. Empty if undeclared.
    Scans candidate paths and returns the first file that declares a
    non-empty Aliases table (mailbox-local caps-only PARTICIPANTS must not
    shadow a complete table elsewhere).
    """
    paths = [path] if path else participants_paths()
    for cand in paths:
        if cand is None or not cand.is_file():
            continue
        parsed = _parse_aliases_text(cand.read_text(encoding="utf-8"))
        if parsed:
            return parsed
    return {}


def writers_for(
    token: str,
    aliases: dict[str, list[str]] | None = None,
) -> list[str]:
    """Expand a display alias to writer tokens; bare identity → [itself]."""
    tok = (token or "").strip().lower()
    if not tok:
        return []
    table = aliases if aliases is not None else load_aliases()
    if tok in table:
        return list(table[tok])
    return [tok]


def match_identity(
    field_value: str,
    query: str,
    aliases: dict[str, list[str]] | None = None,
) -> bool:
    """True when message from:/to: matches query using §27 alias map."""
    field = (field_value or "").strip().lower()
    q = (query or "").strip().lower()
    if not q:
        return True
    if field == q:
        return True
    writers = set(writers_for(q, aliases=aliases))
    return field in writers


def capability_status(
    identity: str,
    capability: str,
    participants: dict[str, dict[str, set[str]]] | None = None,
) -> str:
    """Return can | cannot | CANNOT_VERIFY. Never infer from prior ANSWER/from:."""
    caps = participants if participants is not None else load_participants()
    ident = (identity or "").strip().lower()
    cap = (capability or "").strip().lower()
    if not ident or not cap:
        return "CANNOT_VERIFY"
    if cap in _SOFT_CAN_TOKENS:
        return "CANNOT_VERIFY"
    row = caps.get(ident)
    if row is None:
        return "CANNOT_VERIFY"
    if cap in row["cannot"]:
        return "cannot"
    if cap in row["can"]:
        return "can"
    return "CANNOT_VERIFY"


def filter_by_capability(
    candidates: list[str],
    capability: str,
    participants: dict[str, dict[str, set[str]]] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split candidates into (able, skipped[(id, status)]).

    Undeclared ⇒ CANNOT_VERIFY and excluded. cannot ⇒ excluded. can ⇒ included.
    """
    caps = participants if participants is not None else load_participants()
    able: list[str] = []
    skipped: list[tuple[str, str]] = []
    for raw in candidates:
        ident = raw.strip().lower()
        if not ident:
            continue
        status = capability_status(ident, capability, participants=caps)
        if status == "can":
            able.append(ident)
        else:
            skipped.append((ident, status))
    return able, skipped


def first_answer(ask: Message, everything: list[Message]) -> Message | None:
    """First ANSWER that cites this ASK via reply_to/re."""
    if ask.type != "ASK":
        return None
    hits = [
        m for m in everything
        if m.type == "ANSWER" and m.re_id and m.re_id.upper() == ask.id.upper()
    ]
    if not hits:
        return None
    hits.sort(key=id_sort_key)
    return hits[0]


def latency_seconds(ask: Message, answer: Message) -> str | float:
    """Durable date: delta only. Missing/unparseable/negative ⇒ CANNOT_VERIFY."""
    a0 = ask.sent_at
    a1 = answer.sent_at
    if a0 is None or a1 is None:
        return "CANNOT_VERIFY"
    delta = (a1 - a0).total_seconds()
    if delta < 0:
        return "CANNOT_VERIFY"
    return delta


def cmd_latency(args: argparse.Namespace) -> int:
    """RO: ASK→ANSWER latency from durable date stamps only."""
    box = mail_dir()
    if not box.is_dir():
        print(f"NO MAILBOX at {box}", file=sys.stderr)
        return 2
    everything, rejects = load_messages()
    for r in rejects:
        print(r, file=sys.stderr)
    print(f"# latency  mailbox: {box}  ({mailbox_source()})")
    rows = 0
    for ask in everything:
        if ask.type != "ASK":
            continue
        if args.to and ask.to != args.to.lower():
            continue
        ans = first_answer(ask, everything)
        if ans is None:
            continue
        val = latency_seconds(ask, ans)
        rows += 1
        if val == "CANNOT_VERIFY":
            print(f"CANNOT_VERIFY  ask={ask.id}  answer={ans.id}")
        else:
            print(f"{val:.3f}s  ask={ask.id}  answer={ans.id}")
    if rows == 0:
        print("(no answered ASKs)")
    if rejects:
        return 2
    return 0


def cmd_capability(args: argparse.Namespace) -> int:
    """Print can | cannot | CANNOT_VERIFY for one identity+capability."""
    try:
        parts = load_participants()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    status = capability_status(args.identity, args.capability, participants=parts)
    print(status)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Send ASK, optionally filtering --to by --capability (no scheduler).

    One ASK per remaining recipient (list --to stays single-identity match).
    """
    candidates = [t.strip() for t in args.to.split(",") if t.strip()]
    # PROTOCOL.md §27: display alias → fan-out to writer tokens
    aliases = load_aliases()
    expanded: list[str] = []
    for c in candidates:
        for w in writers_for(c, aliases=aliases):
            if w not in expanded:
                expanded.append(w)
    candidates = expanded
    if args.capability:
        try:
            parts = load_participants()
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        able, skipped = filter_by_capability(
            candidates, args.capability, participants=parts
        )
        for ident, status in skipped:
            print(f"{ident}: {status}", file=sys.stderr)
        if not able:
            print(
                f"refusing ask --capability {args.capability!r}: "
                "no declared can: recipients remain",
                file=sys.stderr,
            )
            return 2
        recipients = able
    else:
        recipients = [c.lower() for c in candidates]

    rc = 0
    for dest in recipients:
        a = argparse.Namespace()
        a.type = "ASK"
        a.sender = args.sender
        a.to = dest
        a.subject = args.subject
        a.reply_to = ""
        a.re = ""
        a.supersedes = ""
        a.expires = ""
        a.body = args.body
        a.body_file = args.body_file
        for key in (
            "result", "reason", "namespace", "resolved_at", "falsifier", "do_not_infer",
            "artifact_class", "claim_currentness", "evidence_scope",
            "fingerprint_head", "fingerprint_worktree",
            "blocked_action", "blocker", "not_blocked", "unblock_condition",
            "safe_parallel_work",
        ):
            setattr(a, key, "")
        one = cmd_send(a)
        if one != 0:
            rc = one
    return rc



def _fsync_dir(directory: Path) -> None:
    """Make the new directory entry durable (POSIX). Best effort: a platform or
    filesystem that cannot open/fsync a directory simply skips it."""
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _publish(final: Path, text: str) -> None:
    """Create ``final`` atomically with its complete contents, never overwriting.

    Readers (the pickup hook, ``list``) glob ``*.md`` while other agents are
    sending. A plain ``write_text`` makes the file visible empty and then fills
    it, so a reader can act on a half-written message. Instead: write a hidden
    temp file in the same directory, fsync it, then ``os.link`` it into place
    (which fails with FileExistsError rather than replacing), and drop the temp.
    ``stress_test.py`` pins this.
    """
    tmp = final.with_name(f".{final.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, final)
        except OSError as exc:
            if isinstance(exc, FileExistsError):
                raise
            # Filesystem without hard links: fall back, still never overwrite.
            if final.exists():
                raise FileExistsError(str(final)) from exc
            os.replace(tmp, final)
        _fsync_dir(final.parent)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass  # best effort: a leftover temp is hidden and reported once stale


MAX_CLAIM_HOURS = 168  # a lease is a promise to come back; a week is already generous
_EXPIRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$")


def _expiry_error(value: str, now: dt.datetime) -> str:
    """Why a NEW --expires is unacceptable, or "" when fine. Reading stays lenient
    (old mail), writing is strict: explicit time and zone, in the future, bounded."""
    if not _EXPIRY_RE.match(value.strip()):
        return "must be ISO-8601 with a time and an explicit zone, e.g. 2030-01-01T12:00:00Z"
    exp = _parse_iso(value)
    if exp is None:
        return "not parseable ISO-8601"
    if exp <= now:
        return "is already in the past"
    if exp - now > dt.timedelta(hours=MAX_CLAIM_HOURS):
        return f"is more than {MAX_CLAIM_HOURS}h away; renew a shorter lease instead"
    return ""


def _idem_marker(box: Path, sender: str, to: str, key: str) -> Path:
    h = hashlib.sha256(f"{sender}\0{to}\0{key}".encode()).hexdigest()[:24]
    return box / f".idem.{h}"


IDEM_WAIT_DEFAULT = 5.0   # seconds; PROTOCOL.md section 31 "the key wait"
IDEM_WAIT_MAX = 60.0
IDEM_WAIT_ENV = "AGENT_MAIL_IDEM_WAIT"
_IDEM_LOCKED = "flock"    # third marker line: "my holder keeps a kernel lock on me"
_IDEM_WAIT_RE = re.compile(r"^(\d+(\.\d*)?|\.\d+)$")


def _key_wait(flag: str | None) -> tuple[float, str]:
    """(seconds, "") for this send's key wait, or (0.0, why it is refused).

    --key-wait wins over AGENT_MAIL_IDEM_WAIT (an empty variable counts as
    unset), which wins over the default. Out-of-range and unparseable values
    are refused, never clamped: a silently shortened or lengthened wait would
    change what a retry risks without telling the caller.
    """
    env = os.environ.get(IDEM_WAIT_ENV, "")
    if flag is not None:
        raw, src = flag, "--key-wait"
    elif env.strip():
        raw, src = env, IDEM_WAIT_ENV
    else:
        return IDEM_WAIT_DEFAULT, ""
    val = float(raw.strip()) if _IDEM_WAIT_RE.match(raw.strip()) else -1.0
    if not 0.0 <= val <= IDEM_WAIT_MAX:
        return 0.0, (f"{src} {raw!r}: the key wait must be a decimal number of seconds "
                     f"from 0 to {IDEM_WAIT_MAX:g}")
    return val, ""


def _idem_parse(raw: bytes) -> tuple[str, str, bool]:
    """(id, content hash, locked format?) of a marker; id is "" when the marker
    is not (yet) readable as one. The id is checked to be a ULID because it is
    used in a glob: a hand-planted `*` must not match somebody else's message."""
    parts = raw.decode("utf-8", "replace").split("\n")
    old_id = parts[0].strip()
    old_content = parts[1].strip() if len(parts) > 1 else ""
    if not _is_ulid(old_id) or len(old_content) != 64:
        return "", "", False
    return old_id, old_content, len(parts) > 2 and parts[2].strip() == _IDEM_LOCKED


def _idem_landed(box: Path, old_id: str) -> bool:
    return any(box.glob(f"{old_id}-*.md"))


def _same_inode(path: Path, fd: int) -> bool:
    try:
        return os.stat(path).st_ino == os.fstat(fd).st_ino
    except OSError:
        return False


def _try_flock(fd: int) -> bool | None:
    """Non-blocking exclusive flock: True when granted, False when another
    process holds it, None when the filesystem refuses flock altogether (the
    marker then carries no usable liveness signal, whatever it says)."""
    import fcntl  # POSIX only
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    except OSError:
        return None
    return True


def _idem_new_marker(marker: Path, msg_id: str, content: str, over: bool) -> int | None:
    """Make ``marker`` name a complete marker this process already holds locked.

    The marker is written and locked under a hidden temp name and only then
    given its real name, so no reader ever sees it empty or unlocked. With
    ``over`` false it is linked into place, which fails when the name exists
    (returns None: someone else has the key). With ``over`` true it is renamed
    over the orphan the caller holds locked. Returns the descriptor that holds
    the lock; closing it releases the key's liveness signal.
    """
    import fcntl  # POSIX only
    tmp = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            tag = _IDEM_LOCKED + "\n"
        except BlockingIOError:
            raise
        except OSError:
            tag = ""  # the filesystem refuses flock: old-format marker, wait fallback
        os.write(fd, f"{msg_id}\n{content}\n{tag}".encode())
        os.fsync(fd)
        if over:
            os.replace(tmp, marker)
            return fd
        try:
            os.link(tmp, marker)
        except FileExistsError:
            os.close(fd)
            return None
        except OSError:
            # Filesystem without hard links: exclusive create is the only
            # primitive left, so the marker is briefly visible empty. It is
            # written in the old format, which promises no liveness signal.
            os.close(fd)
            try:
                fd = os.open(marker, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                return None
            os.write(fd, f"{msg_id}\n{content}\n".encode())
            os.fsync(fd)
        return fd
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _idem_legacy_takeover(marker: Path, seen: bytes) -> None:
    """Remove an old-format orphan the way it was done before section 31: under
    the `.takeover` mutex, and only if the marker is still the one observed.
    Kept unchanged so it stays compatible with senders that predate the lock."""
    mutex = marker.with_name(marker.name + ".takeover")
    try:
        mfd = os.open(mutex, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        try:
            if time.time() - mutex.stat().st_mtime > 30:
                mutex.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        time.sleep(0.05)
        return
    os.close(mfd)
    try:
        # compare-and-delete: only remove the orphan we observed
        if marker.read_bytes() == seen:
            marker.unlink(missing_ok=True)
    except OSError:
        pass
    finally:
        mutex.unlink(missing_ok=True)


def _idem_claim(box: Path, sender: str, to: str, key: str, msg_id: str,
                mtype: str, subject: str, body: str,
                wait: float = IDEM_WAIT_DEFAULT) -> tuple[str, int | None]:
    """Reserve (sender, to, key). PROTOCOL.md section 31.

    Returns ("", fd) when this send owns the key (fd holds the marker's kernel
    lock; hand it to _idem_release), (original id, None) for an exact retry of
    a message that exists, ("CONFLICT", None) when the key was reserved with
    different content, and ("BUSY", None) when a live sender still holds the
    key after ``wait`` seconds. A live holder's marker is never taken over; a
    dead holder's is taken over at once; only an old-format marker, which
    carries no liveness signal, is taken over on the strength of the wait.
    """
    content = hashlib.sha256(f"{mtype}\0{subject}\0{body}".encode()).hexdigest()
    marker = _idem_marker(box, sender, to, key)
    ro = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    for _ in range(8):
        fd = _idem_new_marker(marker, msg_id, content, over=False)
        if fd is not None:
            return "", fd
        try:
            rfd = os.open(marker, ro)
        except FileNotFoundError:
            continue  # its sender gave up between our link and our open
        try:
            seen = os.pread(rfd, 4096, 0)
            old_id, old_content, locked = _idem_parse(seen)
            if old_id and old_content != content:
                return "CONFLICT", None
            # An unreadable marker is one being written in place by a sender
            # without the atomic reserve: never take that over inside a second.
            deadline = time.monotonic() + (wait if old_id else max(wait, 1.0))
            while True:
                if old_id and _idem_landed(box, old_id):
                    return old_id, None
                if not _same_inode(marker, rfd):
                    break  # released or replaced: start over
                got = _try_flock(rfd) if locked else False
                if got is None:
                    locked = False  # flock refused here: only the wait fallback is left
                if got:
                    # Nobody alive holds it. Decide while holding it ourselves.
                    if not _same_inode(marker, rfd):
                        break
                    if _idem_landed(box, old_id):
                        return old_id, None  # published and exited just now
                    return "", _idem_new_marker(marker, msg_id, content, over=True)
                if not locked and os.pread(rfd, 4096, 0) != seen:
                    break  # an old-format sender finished writing it: re-read
                if time.monotonic() >= deadline:
                    if locked:
                        return "BUSY", None
                    _idem_legacy_takeover(marker, seen)
                    break
                time.sleep(0.05)
        finally:
            os.close(rfd)  # also releases a lock taken on an orphan
    return "BUSY", None


def _idem_release(marker: Path, fd: int, keep: bool) -> None:
    """End this send's hold on its key. ``keep`` (the message was published)
    leaves the marker to guard it; otherwise the marker is removed, but only if
    the name still names the inode this send holds, so a send that gave up can
    never remove a marker that is no longer its own."""
    try:
        if not keep and _same_inode(marker, fd):
            marker.unlink(missing_ok=True)
    except OSError:
        pass
    os.close(fd)


class _FlockRefused(Exception):
    """flock(2) itself was refused by the filesystem (not: the file could not be opened)."""


def _scope_lock(lock: Path) -> int | None:
    """Take the scope lock (PROTOCOL.md section 30) and return the descriptor
    that holds it, or None when a live claimant holds it.

    The lock is an exclusive, non-blocking flock(2) on ``lock``, not the file's
    existence: the file is opened-or-created (never O_EXCL) so every claimant
    locks the same inode, and the kernel releases the lock the instant its
    holder exits. A dead claimant therefore leaves nothing to break by age, and
    no process ever removes a lock another process holds.
    """
    import fcntl  # POSIX only; local so the module still imports where fcntl is absent
    for _ in range(8):
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None  # a live process holds it
        except OSError as exc:
            os.close(fd)
            # the filesystem refuses flock: the caller reports it, no fallback to a race
            raise _FlockRefused(str(exc)) from exc
        # A holder unlinks the file BEFORE it releases (_scope_unlock), so an
        # inode that is no longer at the path is an orphan a previous holder
        # already gave up: locking it proves nothing. Drop it and start over.
        try:
            at_path = os.stat(lock).st_ino
        except FileNotFoundError:
            at_path = -1
        if os.fstat(fd).st_ino == at_path:
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode())  # who holds it, for a human
            except OSError:
                pass
            return fd
        os.close(fd)  # releases the orphan
    return None


def _scope_unlock(lock: Path, fd: int) -> None:
    """Release a scope lock this process holds: unlink the file first, while the
    lock is still held, then close (which releases it). Unlink-before-release
    is what lets _scope_lock tell an orphan inode from the live one; the inode
    check keeps this from ever removing a file that is not ours."""
    try:
        if os.stat(lock).st_ino == os.fstat(fd).st_ino:
            lock.unlink(missing_ok=True)
    except OSError:
        pass
    os.close(fd)


_FREE_HEADER_FIELDS = (
    "scope", "key", "result", "reason", "namespace", "resolved_at", "falsifier",
    "do_not_infer", "artifact_class", "claim_currentness", "evidence_scope",
    "fingerprint_head", "fingerprint_worktree", "blocked_action", "blocker",
    "not_blocked", "unblock_condition", "safe_parallel_work", "expires",
)


def _send_input_error(args: argparse.Namespace, body: str) -> str:
    """Why this send's text is unacceptable (see INPUT POLICY), or "" when fine."""
    for flag, raw in (("--from", getattr(args, "sender", "")), ("--to", getattr(args, "to", ""))):
        val = (raw or "").strip().lower()
        if len(val) > MAX_IDENT_CHARS:
            return f"{flag} is longer than {MAX_IDENT_CHARS} characters"
        if not _IDENT_RE.match(val) or ".." in val:
            return (f"{flag} {raw!r} is not a valid identity "
                    "(ASCII letters, digits and . _ @ + - only; no spaces, slashes or control characters)")
    for flag, raw in (("--reply-to", getattr(args, "reply_to", "")), ("--re", getattr(args, "re", "")),
                      ("--supersedes", getattr(args, "supersedes", ""))):
        val = (raw or "").strip()
        if val and (len(val) > 64 or not _REF_RE.match(val) or ".." in val):
            return f"{flag} {raw!r} is not a plain message id (letters, digits and . _ - only)"
    subject = getattr(args, "subject", "") or ""
    if len(subject) > MAX_SUBJECT_CHARS:
        return f"--subject is longer than {MAX_SUBJECT_CHARS} characters"
    bad = _header_char_error(subject)
    if bad:
        return f"--subject {bad}"
    for name in _FREE_HEADER_FIELDS:
        val = getattr(args, name, "") or ""
        if len(val) > MAX_FIELD_CHARS:
            return f"--{name.replace('_', '-')} is longer than {MAX_FIELD_CHARS} characters"
        bad = _header_char_error(val)
        if bad:
            return f"--{name.replace('_', '-')} {bad}"
    bad = _body_char_error(body)
    if bad:
        return f"body {bad}"
    if len(body.encode("utf-8", "surrogatepass")) > MAX_BODY_BYTES:
        return f"body is larger than {MAX_BODY_BYTES} bytes"
    return ""


def cmd_send(args: argparse.Namespace) -> int:
    mtype = args.type.upper()
    if mtype not in TYPES:
        print(f"type must be one of {', '.join(TYPES)}", file=sys.stderr)
        return 2
    if mtype == "CLAIM" and not args.expires:
        print("CLAIM requires --expires (ISO-8601). Refusing to write an unbounded "
              "lock: an agent that dies mid-session would hold it forever.",
              file=sys.stderr)
        return 2
    if args.expires:
        err = _expiry_error(args.expires, _now())
        if err:
            print(f"--expires {args.expires!r}: {err}", file=sys.stderr)
            return 2

    body = resolve_body(args)
    if body is None:
        return 2
    if not (body or "").strip():
        print(
            "refusing to send an empty message; use --body-file PATH, "
            "--body-file - (stdin), or a short single-line --body",
            file=sys.stderr,
        )
        return 2

    err = _send_input_error(args, body)
    if err:
        print(f"refusing to send: {err}", file=sys.stderr)
        return 2
    key_wait_flag = getattr(args, "key_wait", None)
    key_wait = IDEM_WAIT_DEFAULT
    if (getattr(args, "key", "") or "").strip():
        key_wait, err = _key_wait(key_wait_flag)
        if err:
            print(f"refusing to send: {err}", file=sys.stderr)
            return 2
    elif key_wait_flag is not None:
        print("refusing to send: --key-wait only applies with --key", file=sys.stderr)
        return 2
    args.subject = unicodedata.normalize("NFC", args.subject)

    # PROTOCOL.md §27: fan-out send --to <alias> to mapped writers (once).
    dest = (getattr(args, "to", "") or "").strip().lower()
    aliases = load_aliases()
    if dest in aliases and not getattr(args, "_alias_fanout_done", False):
        rc = 0
        for w in aliases[dest]:
            a = argparse.Namespace(**{k: getattr(args, k) for k in vars(args)})
            a.to = w
            a._alias_fanout_done = True
            one = cmd_send(a)
            if one != 0:
                rc = one
        return rc


    block_fields = {
        k: getattr(args, k, "") or ""
        for k in (
            "blocked_action", "blocker", "not_blocked", "unblock_condition",
            "safe_parallel_work",
        )
    }
    missing = structured_block_incomplete(block_fields)
    if missing:
        print(
            "structured block requires all five fields when any is set; missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    now = _now()
    sender, to = args.sender.lower(), args.to.lower()
    idem = (getattr(args, "key", "") or "").strip()
    msg_id = _new_ulid(now)
    if not _is_ulid(msg_id) or msg_id.upper() == "REPLACE_ID":
        print(
            "refusing to write non-ULID id (Fix A: INVALID ≠ OLD/NEW)",
            file=sys.stderr,
        )
        return 2
    # Filename keeps a subject slug for human browse; id in frontmatter is the ULID.
    fname = f"{msg_id}-{_slug(args.subject)}"

    reply_to = (getattr(args, "reply_to", "") or "") or (getattr(args, "re", "") or "")
    supersedes = getattr(args, "supersedes", "") or ""
    scope = (getattr(args, "scope", "") or "").strip()
    existing, _ = load_messages()
    target: Message | None = None
    if supersedes:
        target = next((m for m in existing if m.id.upper() == supersedes.upper()), None)
        if target is None:
            print(f"--supersedes {supersedes}: no such message", file=sys.stderr)
            return 2
        if target.type == "CLAIM" and (mtype != "CLAIM" or target.sender != sender):
            print("only the claim's own sender can supersede it, with a CLAIM "
                  "(renew/release); anyone else should send a DISPUTE", file=sys.stderr)
            return 2
    if scope and mtype != "CLAIM":
        print("--scope only applies to CLAIM", file=sys.stderr)
        return 2
    dropped_scope = ""
    if supersedes and target is not None and target.type == "CLAIM":
        old_scope = (target.meta.get("scope") or "").strip()
        if old_scope and old_scope != scope:
            dropped_scope = old_scope  # said after a successful publish (section 32)

    lines = ["---", f"id: {msg_id}", f"type: {mtype}", f"from: {sender}", f"to: {to}",
             f"date: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}", f"subject: {args.subject}"]
    if reply_to:
        lines.append(f"reply_to: {reply_to}")
    if supersedes:
        lines.append(f"supersedes: {supersedes}")
    if args.expires:
        lines.append(f"expires: {args.expires}")
    if scope:
        lines.append(f"scope: {scope}")
    # Optional remasure / CANNOT_VERIFY fields
    for key in (
        "result", "reason", "namespace", "resolved_at", "falsifier", "do_not_infer",
        "artifact_class", "claim_currentness", "evidence_scope",
        "fingerprint_head", "fingerprint_worktree",
        "blocked_action", "blocker", "not_blocked", "unblock_condition",
        "safe_parallel_work",
    ):
        val = getattr(args, key, "") or ""
        if val:
            lines.append(f"{key}: {val}")
    lines += ["---", "", body.rstrip(), ""]

    box, may_create = discover_mailbox()
    if mailbox_in_tool_checkout(box):
        print(f"ABORT: refusing to file into the spec checkout at {box}. "
              "AGENT_MAIL_DIR is the team inbox if set; this repo is the spec, not the mailbox.",
              file=sys.stderr)
        return 2
    if not box.is_dir():
        if not may_create:
            if os.environ.get("AGENT_MAIL_DIR"):
                print(f"NO MAILBOX at {box}", file=sys.stderr)
            else:
                print(f"ABORT: ancestor mailbox exists. Refusing to mint a second owner at {box}.",
                      file=sys.stderr)
            return 2
        try:
            box.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"send failed: cannot create the mailbox at {box}: {exc.strerror or exc} "
                  f"({errno.errorcode.get(exc.errno or 0, 'OSError')}); nothing written",
                  file=sys.stderr)
            return 1
    lock = box / f".scope.{hashlib.sha256(scope.encode()).hexdigest()[:24]}.lock"
    lock_fd: int | None = None  # set only while this process holds the scope lock
    marker: Path | None = None
    marker_fd: int | None = None  # set only while this process holds the key (section 31)
    final = box / f"{fname}.md"
    collided = False

    def landed() -> bool:
        # The name carries this send's fresh ULID, so it exists iff we linked it.
        return not collided and final.exists()

    trap = _SignalTrap()
    try:
        if scope:
            # PROTOCOL.md section 30: a kernel-held lock, never broken by age.
            try:
                lock_fd = _scope_lock(lock)
            except _FlockRefused as exc:
                print(f"cannot lock scope {scope!r}: {exc} "
                      "(flock(2) on the mailbox filesystem is required)", file=sys.stderr)
                return 2
            if lock_fd is None:
                print(f"scope {scope!r} is being claimed right now; retry", file=sys.stderr)
                return 3
            current, _ = load_messages()
            rivals = [m for m in current
                      if m.type == "CLAIM" and m.meta.get("scope") == scope
                      and m.sender != sender
                      and m.id.upper() != supersedes.upper()
                      and derived_status(m, current, now) == "held"]
            if rivals:
                print(f"scope {scope!r} is already held by {rivals[0].sender} "
                      f"({rivals[0].id}, until {rivals[0].meta.get('expires')})",
                      file=sys.stderr)
                return 3
        if idem:
            marker = _idem_marker(box, sender, to, idem)
            prior, marker_fd = _idem_claim(box, sender, to, idem, msg_id, mtype,
                                           args.subject, body, key_wait)
            if prior == "CONFLICT":
                print(f"idempotency key {idem!r} was already used with different content; "
                      "refusing (use a new key for a new message)", file=sys.stderr)
                return 2
            if prior == "BUSY":
                print(f"idempotency key {idem!r} is being sent by another process right now; "
                      "nothing written; retry", file=sys.stderr)
                return 3
            if prior:
                print(f"duplicate of {prior} (key {idem!r}); nothing written")
                print(f"id: {prior}")
                return 0
            lines.insert(lines.index("---", 1), f"idem: {idem}")
        try:
            _publish(final, chr(10).join(lines))
        except FileExistsError:
            collided = True
            print(f"ABORT: {fname}.md already exists; refusing to overwrite a message.",
                  file=sys.stderr)
            return 2
    except KeyboardInterrupt:
        trap.quiet = True
        if landed():
            print(f"interrupted ({trap.name}) after publishing; id: {msg_id}", file=sys.stderr)
        else:
            print(f"interrupted ({trap.name}); nothing written", file=sys.stderr)
        return trap.exit_code
    except OSError as exc:
        trap.quiet = True
        why = f"{exc.strerror or exc} ({errno.errorcode.get(exc.errno or 0, 'OSError')})"
        if landed():
            print(f"send failed after publishing: {why}; id: {msg_id}", file=sys.stderr)
        else:
            print(f"send failed: cannot write to the mailbox: {why}; nothing written",
                  file=sys.stderr)
        return 1
    finally:
        trap.quiet = True  # a second signal must not interrupt the release
        if marker is not None and marker_fd is not None:
            # published: the marker now guards a real message; otherwise release
            _idem_release(marker, marker_fd, keep=landed())
        if lock_fd is not None:
            _scope_unlock(lock, lock_fd)  # only ever a lock this process holds
        trap.restore()
    if dropped_scope:
        print(f"note: the superseded claim held scope {dropped_scope!r} and this one names "
              f"{('scope ' + repr(scope)) if scope else 'no scope'}: {dropped_scope!r} is released",
              file=sys.stderr)
    print(f"wrote {final}")
    print(f"id: {msg_id}")
    return 0


class _SignalTrap:
    """Turn SIGTERM into the KeyboardInterrupt SIGINT already raises, for the
    duration of a send, so its ``finally`` releases what it holds (PROTOCOL.md
    section 31). Correctness never depends on this: SIGKILL skips it, and the
    kernel-held locks are what make that safe. It only keeps an orderly
    cancellation from leaving a marker or a temp file behind."""

    def __init__(self) -> None:
        self.signum = 0
        self.quiet = False
        self._old: Any = None
        try:
            self._old = signal.signal(signal.SIGTERM, self._on_term)
        except (ValueError, OSError):
            self._old = None  # not the main thread: leave the disposition alone

    def _on_term(self, signum: int, _frame: Any) -> None:
        self.signum = signum
        if not self.quiet:
            raise KeyboardInterrupt

    @property
    def name(self) -> str:
        return "SIGTERM" if self.signum == signal.SIGTERM else "SIGINT"

    @property
    def exit_code(self) -> int:
        return 128 + (signal.SIGTERM if self.signum == signal.SIGTERM else signal.SIGINT)

    def restore(self) -> None:
        if self._old is not None:
            try:
                signal.signal(signal.SIGTERM, self._old)
            except (ValueError, OSError):
                pass
            self._old = None


def _print_rows(rows: list[Message], everything: list[Message]) -> None:
    idx = _Index(everything)
    for m in rows:
        st = derived_status(m, everything, _idx=idx)
        mark = "!" if owes_reply(m, everything, idx) else " "
        print(f"{mark} {m.type:<7} [{st:<10}] {m.sender} -> {m.to}  {m.subject}")
        print(f"    {m.id}")


def cmd_list(args: argparse.Namespace) -> int:
    box = mail_dir()
    if not box.is_dir():
        print(f"NO MAILBOX at {box}", file=sys.stderr)
        return 2
    print(f"# mailbox: {box}  ({mailbox_source()})")
    everything, rejects = load_messages()
    for r in rejects:
        print(r, file=sys.stderr)
    rows = everything
    aliases = load_aliases()
    if args.to:
        rows = [m for m in rows if match_identity(m.to, args.to, aliases=aliases)]
    if args.sender:
        rows = [m for m in rows if match_identity(m.sender, args.sender, aliases=aliases)]
    if args.live:
        idx = _Index(everything)
        rows = [m for m in rows if live(m, everything, _idx=idx)]
    _print_rows(rows, everything)
    if rejects:
        return 2
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    """Actionable view: open / blocked / held / owed-to-me. Forensic list stays."""
    box = mail_dir()
    if not box.is_dir():
        print(f"NO MAILBOX at {box}", file=sys.stderr)
        return 2
    identity = (args.to or "").lower()
    if not identity:
        print("inbox requires --to <identity>", file=sys.stderr)
        return 2
    print(f"# inbox: {identity}  mailbox: {box}  ({mailbox_source()})")
    everything, rejects = load_messages()
    for r in rejects:
        print(r, file=sys.stderr)
    aliases = load_aliases()
    idx = _Index(everything)
    rows = [m for m in everything
            if actionable(m, everything, identity, aliases=aliases, _idx=idx)]
    _print_rows(rows, everything)
    if rejects:
        return 2
    return 0



CANARY_SUBJECT_PREFIX = "PICKUP-CANARY"


def authorship_status(msg: Message) -> str:
    """Free-text from:/filename are stamp-echo only — never durable authorship."""
    return "CANNOT_VERIFY"


def find_live_canary(
    everything: list[Message],
    now: dt.datetime | None = None,
) -> Message | None:
    now = now or _now()
    best: Message | None = None
    for m in everything:
        if m.type != "NOTICE":
            continue
        if not (m.subject or "").startswith(CANARY_SUBJECT_PREFIX):
            continue
        exp = m.expires_at
        if not exp or exp <= now:
            continue
        if best is None or (m.sent_at or now) > (best.sent_at or now):
            best = m
    return best


def cmd_canary(args: argparse.Namespace) -> int:
    """File a pickup-canary NOTICE with expiry (proves pickup, not just write)."""
    hours = float(args.hours)
    if hours <= 0:
        print("--hours must be positive", file=sys.stderr)
        return 2
    now = _now()
    expires = (now + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        "Pickup canary — renew before expires; doctor fails if missing/stale. "
        "Not authority for any press or mutation."
    )
    # Reuse the send path with an argparse-shaped namespace.
    a = argparse.Namespace()
    a.type = "NOTICE"
    a.sender = args.sender
    a.to = args.to
    a.subject = f"{CANARY_SUBJECT_PREFIX} {now.strftime('%Y-%m-%dT%H:%MZ')}"
    a.reply_to = ""
    a.re = ""
    a.supersedes = ""
    a.expires = expires
    a.body = body
    a.body_file = ""
    for key in (
        "result", "reason", "namespace", "resolved_at", "falsifier", "do_not_infer",
        "artifact_class", "claim_currentness", "evidence_scope",
        "fingerprint_head", "fingerprint_worktree",
        "blocked_action", "blocker", "not_blocked", "unblock_condition",
        "safe_parallel_work",
    ):
        setattr(a, key, "")
    return cmd_send(a)


STATUS_SCHEMA = 1
DEFAULT_STALLED_HOURS = 72.0
STALE_TMP_SECONDS = 600
PROBE_MIN_AGE_SECONDS = IDEM_WAIT_MAX  # nothing younger is probed (PROTOCOL.md section 33)
STALE_MUTEX_SECONDS = 30
_SCOPE_LOCK_RE = re.compile(r"^\.scope\.[0-9a-f]{24}\.lock$")
_IDEM_MARKER_RE = re.compile(r"^\.idem\.[0-9a-f]{24}$")
_IDEM_MUTEX_RE = re.compile(r"^\.idem\.[0-9a-f]{24}\.takeover$")
PROBLEM_CODES = (
    "QUARANTINED", "STALE_TMP", "ORPHAN_IDEM_MARKER", "ORPHAN_SCOPE_LOCK", "STALLED_LOCK_HOLDER",
    "STALE_TAKEOVER_MUTEX", "MALFORMED_CLAIM_EXPIRY", "FOREIGN_SUPERSEDE", "DANGLING_REPLY",
    "STALLED_AGENT", "CONTESTED_SCOPE",
)
CLAIMS_LIST_CAP = 200    # rows per list in status --json (PROTOCOL.md section 34)
CLAIMS_TEXT_ROWS = 20    # rows of the held-claims table in text status
CLAIM_ATTENTION_REASONS = ("CONTESTED_SCOPE", "MALFORMED_EXPIRES", "NO_EXPIRES", "TOO_LONG", "EXPIRED")


def _probe_holder(path: Path) -> tuple[str, bytes]:
    """Ask the kernel whether a live process holds ``path``'s flock, read-only.

    Returns ("held" | "free" | "unknown", first 4096 bytes). The file is opened
    O_RDONLY (never created, never followed if it is a link) and asked for a
    shared non-blocking lock, which is granted exactly when no sender holds
    the exclusive one; closing the descriptor drops it at once. Nothing is
    written and nothing is left locked.
    """
    try:
        import fcntl  # POSIX only
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except (ImportError, OSError):
        return "unknown", b""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "unknown", b""
        head = os.pread(fd, 4096, 0)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return "held", head
        return "free", head
    except OSError:
        return "unknown", b""
    finally:
        os.close(fd)


def scan_hidden(box: Path, now_ts: float) -> dict[str, Any]:
    """Read-only census of the hidden files senders leave in a mailbox
    (PROTOCOL.md section 33). Never creates, writes, removes or keeps a lock on
    anything; a file that vanishes or cannot be examined is counted in `total`
    only. ``*_names`` lists are for `doctor`; `status` prints counts."""
    out: dict[str, Any] = {
        "stale_tmp": [],
        "scope_locks": {"total": 0, "in_flight": 0, "held": 0, "orphaned": 0},
        "idem_markers": {"total": 0, "resolved": 0, "in_flight": 0, "held": 0, "orphaned": 0},
        "takeover_mutexes": {"total": 0, "stale": 0},
        "scope_lock_names": [], "idem_marker_names": [], "takeover_mutex_names": [],
    }
    try:
        names = sorted(os.listdir(box))
    except OSError:
        return out
    landed = {n[:26].upper() for n in names if n.endswith(".md") and n[26:27] == "-"}
    for name in names:
        if not name.startswith("."):
            continue
        path = box / name
        is_lock, is_marker = bool(_SCOPE_LOCK_RE.match(name)), bool(_IDEM_MARKER_RE.match(name))
        is_mutex, is_tmp = bool(_IDEM_MUTEX_RE.match(name)), name.endswith(".tmp") and len(name) > 5
        if not (is_lock or is_marker or is_mutex or is_tmp):
            continue
        group = ("scope_locks" if is_lock else "idem_markers" if is_marker
                 else "takeover_mutexes" if is_mutex else "")
        if group:
            out[group]["total"] += 1
        try:
            st = os.lstat(path)
        except OSError:
            continue  # gone between the listing and the look
        age = now_ts - st.st_mtime
        if is_tmp:
            if age > STALE_TMP_SECONDS:
                out["stale_tmp"].append(name)
            continue
        if is_mutex:
            if age > STALE_MUTEX_SECONDS:
                out["takeover_mutexes"]["stale"] += 1
                out["takeover_mutex_names"].append(name)
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if is_marker:
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
            except OSError:
                continue
            try:
                old_id, _content, locked = _idem_parse(os.pread(fd, 4096, 0))
            except OSError:
                continue
            finally:
                os.close(fd)
            if old_id and old_id.upper() in landed:
                out[group]["resolved"] += 1
                continue
        else:
            locked = True
        if age < PROBE_MIN_AGE_SECONDS:
            out[group]["in_flight"] += 1
            continue
        state = _probe_holder(path)[0] if locked else "free"
        if state == "held":
            out[group]["held"] += 1
            out[group[:-1] + "_names"].append(f"{name} (held)")
        elif state == "free":
            out[group]["orphaned"] += 1
            out[group[:-1] + "_names"].append(f"{name} (orphaned)")
    return out


def dangling_replies(everything: list[Message], now: dt.datetime) -> list[str]:
    """Ids of fresh messages whose reply_to names no message here (section 32)."""
    known = {m.id.upper() for m in everything}
    out = []
    for m in everything:
        if not m.re_id or m.re_id.upper() in known:
            continue
        sent = m.sent_at
        if sent is None or (now - sent).days < FRESH_DAYS:
            out.append(m.id)
    return out


def _utc_text(when: dt.datetime) -> str:
    """`when` as UTC, YYYY-MM-DDTHH:MM:SSZ. An instant UTC cannot represent (a
    hand-written year 9999 with a negative offset, or year 1 with a positive
    one) is reported as the nearest representable second instead of raising."""
    try:
        return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, ValueError):
        return "9999-12-31T23:59:59Z" if when.year > 5000 else "0001-01-01T00:00:00Z"


def claim_report(everything: list[Message], idx: _Index, now: dt.datetime,
                 scope_filter: str | None = None) -> dict[str, Any]:
    """Who holds what (PROTOCOL.md section 34). Pure: reads headers only, never
    a body or a subject, and derives everything from (messages, now).

    Returns the six keys section 34 adds to `status --json`, plus `contested`
    (scope -> [(sender, id)], for `doctor`; never serialised by `status`).
    """
    held: list[tuple[Message, str, dt.datetime]] = []
    flagged: list[tuple[str, Message, str]] = []
    holders: dict[str, set[str]] = {}
    for m in everything:
        if m.type != "CLAIM" or idx.is_superseded(m.id):
            continue
        scope = (m.meta.get("scope") or "").strip()
        exp = m.expires_at
        if exp is None:
            raw = (m.meta.get("expires") or "").strip()
            flagged.append(("MALFORMED_EXPIRES" if raw else "NO_EXPIRES", m, scope))
        elif exp > now:
            held.append((m, scope, exp))
            if scope:
                holders.setdefault(scope, set()).add(m.sender)
            if exp - now > dt.timedelta(hours=MAX_CLAIM_HOURS):
                flagged.append(("TOO_LONG", m, scope))
        else:
            flagged.append(("EXPIRED", m, scope))
    contested: dict[str, list[tuple[str, str]]] = {
        s: [] for s, who in holders.items() if len(who) > 1}
    for m, scope, _exp in held:
        if scope in contested:
            contested[scope].append((m.sender, m.id))
            flagged.append(("CONTESTED_SCOPE", m, scope))

    def shown(scope: str) -> dict[str, Any]:
        # As stored, never shortened; one longer than `send` accepts is withheld.
        if len(scope) > MAX_FIELD_CHARS:
            return {"scope": None, "scope_oversize": True}
        return {"scope": scope or None}

    def order(row: dict[str, Any]) -> tuple[bool, str, str]:
        return (row["scope"] is None, row["scope"] or "", str(row["id"]).upper())

    held_rows: list[dict[str, Any]] = []
    for m, scope, exp in held:
        if scope_filter is not None and scope != scope_filter:
            continue
        sent = m.sent_at
        held_rows.append({
            "id": m.id, "sender": m.sender, "to": m.to, **shown(scope),
            "expires": _utc_text(exp),
            "seconds_left": max(0, int((exp - now).total_seconds())),
            "date": _utc_text(sent) if sent is not None else None,
            "supersedes": m.supersedes or None,
        })
    held_rows.sort(key=order)
    rank = {reason: n for n, reason in enumerate(CLAIM_ATTENTION_REASONS)}
    attention = [{"id": m.id, "sender": m.sender, **shown(scope), "reason": reason}
                 for reason, m, scope in flagged
                 if scope_filter is None or scope == scope_filter]
    attention.sort(key=lambda row: (rank[row["reason"]], *order(row)))
    return {
        "claims_held": held_rows[:CLAIMS_LIST_CAP],
        "claims_held_truncated": max(0, len(held_rows) - CLAIMS_LIST_CAP),
        "claims_attention": attention[:CLAIMS_LIST_CAP],
        "claims_attention_truncated": max(0, len(attention) - CLAIMS_LIST_CAP),
        "contested_scopes": len(contested),
        "scope_filter": scope_filter,
        "contested": {s: sorted(v, key=lambda p: (p[0], p[1].upper())) for s, v in contested.items()},
    }


def build_status(everything: list[Message], rejects: list[str], box: Path,
                 now: dt.datetime, stalled_hours: float = DEFAULT_STALLED_HOURS,
                 scope_filter: str | None = None) -> dict[str, Any]:
    """Pure, read-only summary of a mailbox. Deterministic for a given (files, now).

    Stable JSON key set (STATUS_SCHEMA = 1; keys are only ever added, never
    renamed or repurposed; PROTOCOL.md documents them):

      schema                 int
      now                    ISO-8601 UTC of the evaluation instant
      mailbox                str path
      messages.total         admitted messages
      messages.by_type       {ASK,ANSWER,NOTICE,CLAIM,DISPUTE: count}  (all admitted)
      messages.live_by_type  same keys; only messages live() would deliver
      asks.open              ASKs no message has answered (reply_to / re)
      asks.oldest_open_id    id of the oldest dated open ASK, or null
      asks.oldest_open_age_seconds  int, or null when no dated open ASK
      claims.held            unexpired, unsuperseded CLAIMs
      claims.expired         parseable expiry in the past, unsuperseded
      claims.malformed_expiry  missing or unparseable expiry, unsuperseded
      claims.superseded      CLAIMs replaced by a later one (renewed/released)
      quarantined            count of REJECTed files (never admitted)
      stale_tmp              hidden .tmp files older than 600s
      stalled_hours          threshold used
      agents                 sorted list of {agent, state, last_message_at,
                             age_seconds}; state is active|stalled|unknown
                             (unknown = none of its messages carries a valid date)
      stalled_agents         sorted names whose state is stalled

    Added by PROTOCOL.md section 33 (schema still 1: keys are only added):

      scope_locks            {total, in_flight, held, orphaned}
      idem_markers           {total, resolved, in_flight, held, orphaned}
      takeover_mutexes       {total, stale}
      foreign_supersedes     messages whose supersedes cites a CLAIM they may not supersede
      dangling_replies       fresh messages whose reply_to names no message here
      problems               [{code, count}] for count > 0, in PROBLEM_CODES order

    Added by PROTOCOL.md section 34 (schema still 1), see claim_report():

      claims_held            [{id, sender, to, scope, expires, seconds_left, date,
                             supersedes}] for held CLAIMs, by scope then id, at most 200
      claims_held_truncated  held claims left out by the cap
      claims_attention       [{id, sender, scope, reason}], reason one of
                             CLAIM_ATTENTION_REASONS, at most 200
      claims_attention_truncated  rows left out by the cap
      contested_scopes       scopes held by more than one sender
      scope_filter           the --scope the two lists were filtered by, or null
    """
    idx = _Index(everything)
    by_type = {t: 0 for t in sorted(KNOWN_TYPES)}
    live_by_type = dict(by_type)
    for m in everything:
        by_type[m.type] = by_type.get(m.type, 0) + 1
        if live(m, everything, now=now, _idx=idx):
            live_by_type[m.type] = live_by_type.get(m.type, 0) + 1
    open_asks = [m for m in everything if m.type == "ASK" and owes_reply(m, everything, idx)]
    dated = sorted((m.sent_at, m.id) for m in open_asks if m.sent_at is not None)
    oldest_id: str | None = None
    oldest_age: int | None = None
    if dated:
        oldest_id = dated[0][1]
        oldest_age = max(0, int((now - dated[0][0]).total_seconds()))
    claims = {"held": 0, "expired": 0, "malformed_expiry": 0, "superseded": 0}
    for m in everything:
        if m.type != "CLAIM":
            continue
        if idx.is_superseded(m.id):
            claims["superseded"] += 1
        elif m.expires_at is None:
            claims["malformed_expiry"] += 1
        elif m.expires_at > now:
            claims["held"] += 1
        else:
            claims["expired"] += 1
    last: dict[str, dt.datetime | None] = {}
    for m in everything:
        if not m.sender:
            continue
        sent = m.sent_at
        prev = last.get(m.sender)
        if sent is not None and (prev is None or sent > prev):
            last[m.sender] = sent
        else:
            last.setdefault(m.sender, prev)
    agents: list[dict[str, Any]] = []
    for name in sorted(last):
        when = last[name]
        if when is None:
            agents.append({"agent": name, "state": "unknown",
                           "last_message_at": None, "age_seconds": None})
            continue
        age = max(0, int((now - when).total_seconds()))
        agents.append({
            "agent": name,
            "state": "stalled" if age > stalled_hours * 3600 else "active",
            "last_message_at": _utc_text(when),
            "age_seconds": age,
        })
    hidden = scan_hidden(box, now.timestamp())
    stale_tmp = len(hidden["stale_tmp"])
    stalled = [str(a["agent"]) for a in agents if a["state"] == "stalled"]
    dangling = len(dangling_replies(everything, now))
    report = claim_report(everything, idx, now, scope_filter)
    counts = {
        "QUARANTINED": len(rejects),
        "STALE_TMP": stale_tmp,
        "ORPHAN_IDEM_MARKER": hidden["idem_markers"]["orphaned"],
        "ORPHAN_SCOPE_LOCK": hidden["scope_locks"]["orphaned"],
        "STALLED_LOCK_HOLDER": hidden["scope_locks"]["held"] + hidden["idem_markers"]["held"],
        "STALE_TAKEOVER_MUTEX": hidden["takeover_mutexes"]["stale"],
        "MALFORMED_CLAIM_EXPIRY": claims["malformed_expiry"],
        "FOREIGN_SUPERSEDE": len(idx.foreign_supersedes),
        "DANGLING_REPLY": dangling,
        "STALLED_AGENT": len(stalled),
        "CONTESTED_SCOPE": report["contested_scopes"],
    }
    return {
        "schema": STATUS_SCHEMA,
        "now": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mailbox": str(box),
        "messages": {"total": len(everything), "by_type": by_type, "live_by_type": live_by_type},
        "asks": {"open": len(open_asks), "oldest_open_id": oldest_id,
                 "oldest_open_age_seconds": oldest_age},
        "claims": claims,
        "quarantined": len(rejects),
        "stale_tmp": stale_tmp,
        "stalled_hours": stalled_hours,
        "agents": agents,
        "stalled_agents": stalled,
        "scope_locks": hidden["scope_locks"],
        "idem_markers": hidden["idem_markers"],
        "takeover_mutexes": hidden["takeover_mutexes"],
        "foreign_supersedes": len(idx.foreign_supersedes),
        "dangling_replies": dangling,
        "problems": [{"code": c, "count": counts[c]} for c in PROBLEM_CODES if counts[c] > 0],
        **{k: v for k, v in report.items() if k != "contested"},
    }


def _held_table(st: dict[str, Any]) -> list[str]:
    """Text `status` lines for the held claims (section 34). For people: a long
    scope is shortened here and only here; `--json` reports it as stored."""
    rows = st["claims_held"]
    if not rows:
        return []

    def scope_text(row: dict[str, Any]) -> str:
        if row.get("scope_oversize"):
            return "(oversize)"
        scope = row["scope"]
        if scope is None:
            return "-"
        return str(scope) if len(scope) <= 48 else scope[:45] + "..."

    cells = [(scope_text(r), str(r["sender"])[:32], str(r["expires"]), _fmt_age(r["seconds_left"]))
             for r in rows[:CLAIMS_TEXT_ROWS]]
    w0, w1 = max(len(c[0]) for c in cells), max(len(c[1]) for c in cells)
    out = ["held claims (scope, holder, expires, left):"]
    out += [f"  {c[0]:<{w0}}  {c[1]:<{w1}}  {c[2]}  {c[3]}" for c in cells]
    more = len(rows) - len(cells) + int(st["claims_held_truncated"])
    if more:
        out.append(f"  ... and {more} more")
    return out


def _fmt_age(seconds: object) -> str:
    if not isinstance(seconds, int):
        return "-"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d{h:02d}h" if d else f"{h}h{rem // 60:02d}m"


def cmd_status(args: argparse.Namespace) -> int:
    """Read-only observability. Never writes, never mutates, exit 0 unless the box is missing."""
    box = mail_dir()
    if not box.is_dir():
        print(f"NO MAILBOX at {box}", file=sys.stderr)
        return 2
    if args.now:
        now = _parse_iso(args.now)
        if now is None:
            print(f"--now {args.now!r}: not parseable ISO-8601", file=sys.stderr)
            return 2
    else:
        now = _now()
    if args.stalled_hours <= 0:
        print("--stalled-hours must be positive", file=sys.stderr)
        return 2
    scope_filter: str | None = None
    if getattr(args, "scope", None) is not None:
        scope_filter = args.scope.strip()
        if not scope_filter:
            print("--scope must not be empty", file=sys.stderr)
            return 2
    everything, rejects = load_messages()
    st = build_status(everything, rejects, box, now, args.stalled_hours, scope_filter)
    if args.json:
        print(json.dumps(st, indent=2, sort_keys=True))
        return 0
    msgs, asks, claims = st["messages"], st["asks"], st["claims"]
    print(f"# status  mailbox: {box}  now: {st['now']}")
    print(f"messages: {msgs['total']}  (live " + ", ".join(
        f"{t}={n}" for t, n in msgs["live_by_type"].items()) + ")")
    print(f"asks: open={asks['open']}  oldest_open_age={_fmt_age(asks['oldest_open_age_seconds'])}"
          + (f"  ({asks['oldest_open_id']})" if asks["oldest_open_id"] else ""))
    print("claims: " + "  ".join(f"{k}={v}" for k, v in claims.items()))
    for line in _held_table(st):
        print(line)
    print(f"quarantined: {st['quarantined']}  stale_tmp: {st['stale_tmp']}")
    print("scope_locks: " + "  ".join(f"{k}={v}" for k, v in st["scope_locks"].items()))
    print("idem_markers: " + "  ".join(f"{k}={v}" for k, v in st["idem_markers"].items()))
    print(f"agents (stalled after {args.stalled_hours:g}h):")
    for a in st["agents"]:
        print(f"  {a['agent']:<24} {a['state']:<8} last={a['last_message_at'] or '-'}"
              f"  age={_fmt_age(a['age_seconds'])}")
    if st["stalled_agents"]:
        print("STALLED: " + ", ".join(st["stalled_agents"]))
    if st["problems"]:
        print("PROBLEMS: " + " ".join(f"{p['code']}={p['count']}" for p in st["problems"]))
    for r in rejects[:20]:
        print(r, file=sys.stderr)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only mailbox health. Never mutates. Never invents authorship."""
    box = mail_dir()
    failures: list[str] = []
    print(f"# doctor  mailbox: {box}  ({mailbox_source()})")

    if not box.is_dir():
        print("store: MISSING")
        print("authorship: CANNOT_VERIFY")
        print("FAIL: mailbox missing")
        return 2
    print("store: OK")

    everything, rejects = load_messages()
    print(f"messages: {len(everything)}")
    if rejects:
        print(f"rejects: {len(rejects)}")
        for r in rejects[:20]:
            print(f"  - {r}")
        failures.append("reject envelopes present (malformed id or unknown type)")

    # Authorship — always CANNOT_VERIFY for free-text from (Ledger acceptance)
    print("authorship: CANNOT_VERIFY")

    now = _now()
    orphans = []
    for m in everything:
        if m.type != "CLAIM":
            continue
        exp = m.expires_at
        raw = (m.meta.get("expires") or "").strip()
        if exp is None and raw:
            orphans.append((m.id, f"MALFORMED_EXPIRES {raw!r}"))
        elif exp is None:
            orphans.append((m.id, "NO_EXPIRES"))
        elif exp <= now:
            orphans.append((m.id, "EXPIRED"))
        elif exp - now > dt.timedelta(hours=MAX_CLAIM_HOURS):
            orphans.append((m.id, "TOO_LONG"))
    if orphans:
        print(f"orphan_claims: {len(orphans)}")
        for oid, why in orphans[:20]:
            print(f"  - {oid} ({why})")
    else:
        print("orphan_claims: 0")

    hidden = scan_hidden(box, now.timestamp())
    stale = hidden["stale_tmp"]
    print(f"stale_tmp: {len(stale)}" + ("".join(f"\n  - {n}" for n in stale[:20])))
    # PROTOCOL.md section 33: reported, never a new reason to FAIL.
    for key, names in (("scope_locks", "scope_lock_names"), ("idem_markers", "idem_marker_names"),
                       ("takeover_mutexes", "takeover_mutex_names")):
        c = hidden[key]
        print(f"{key}: {c['total']} (" + ", ".join(f"{k} {v}" for k, v in c.items() if k != "total")
              + ")" + "".join(f"\n  - {n}" for n in hidden[names][:20]))
    idx = _Index(everything)
    foreign = idx.foreign_supersedes
    print(f"foreign_supersedes: {len(foreign)}" + "".join(f"\n  - {i}" for i in foreign[:20]))
    dangling = dangling_replies(everything, now)
    print(f"dangling_replies: {len(dangling)}" + "".join(f"\n  - {i}" for i in dangling[:20]))
    # PROTOCOL.md section 34: reported, never a new reason to FAIL.
    contested = claim_report(everything, idx, now)["contested"]
    print(f"contested_scopes: {len(contested)}")
    for scope in sorted(contested)[:20]:
        label = (repr(scope) if len(scope) <= MAX_FIELD_CHARS
                 else f"(oversize scope, {len(scope)} characters)")
        print(f"  - {label}: " + ", ".join(f"{who} ({mid})" for who, mid in contested[scope][:20]))

    canary = find_live_canary(everything, now=now)
    if canary is None:
        print("canary: MISSING_OR_STALE")
        failures.append("pickup canary missing or stale")
    else:
        print(f"canary: OK  id={canary.id}  expires={canary.meta.get('expires')}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 2
    print("PASS")
    return 0


def _find_message(msg_id: str) -> Message | None:
    """Resolve by exact id, exact stem, or full-ULID filename prefix only."""
    want = msg_id.upper()  # ids have no case (section 32)
    for m in read_all():
        if want == m.id.upper() or msg_id == m.path.stem:
            return m
        if _is_ulid(msg_id) and m.path.stem.upper().startswith(want + "-"):
            return m
    return None


def cmd_show(args: argparse.Namespace) -> int:
    box = mail_dir()
    if not box.is_dir():
        print(f"NO MAILBOX at {box}", file=sys.stderr)
        return 2
    m = _find_message(args.id)
    if m is None:
        print(f"no message with id {args.id}", file=sys.stderr)
        return 1
    text, why = _read_mail_file(m.path)
    if text is None:
        print(f"cannot show {m.path.name}: {why}", file=sys.stderr)
        return 2
    print(text)
    return 0


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_verification_block(body: str) -> dict[str, str]:
    """Parse an indented `verification:` block from the body. Never executes it."""
    lines = body.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("verification:"):
            start = i
            break
    if start is None:
        return {}
    out: dict[str, str] = {}
    section = ""
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        stripped = line.strip()
        if stripped.endswith(":") and stripped.count(":") == 1 and not stripped.startswith("http"):
            section = stripped[:-1].strip()
            continue
        if ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        k, v = k.strip(), v.strip()
        if section and indent >= 4:
            out[f"{section}.{k}"] = v
        else:
            section = ""
            out[k] = v
    return out



def _git_show_head(repo: Path, rel: str) -> bytes | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:{rel}"],
            capture_output=True,
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _worktree_bytes(repo: Path, rel: str) -> bytes | None:
    path = repo / rel
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def cmd_verify(args: argparse.Namespace) -> int:
    """Remasure scoped fingerprints against HEAD (default). Never executes recipes.

    Privilege CANNOT_VERIFY, HEAD-vs-WIP fingerprint,
    and recipe+expected-N (printed, not run).
    """
    box = mail_dir()
    if not box.is_dir():
        print(f"NO MAILBOX at {box}", file=sys.stderr)
        return 2
    m = _find_message(args.id)
    if m is None:
        print(f"no message with id {args.id}", file=sys.stderr)
        return 1

    meta = m.meta
    result = (meta.get("result") or "").upper()
    reason = meta.get("reason") or ""
    artifact = (meta.get("artifact_class") or "HEAD").upper()
    scope_raw = meta.get("evidence_scope") or ""
    scope = [p.strip() for p in scope_raw.replace(";", ",").split(",") if p.strip()]
    fp_head = (meta.get("fingerprint_head") or "").lower()
    fp_wt = (meta.get("fingerprint_worktree") or "").lower()

    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()

    # Re-resolve HEAD mid-falsify (ASK tip ≠ answer tip)
    try:
        hr = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        if hr.returncode == 0:
            print(f"repo_head: {hr.stdout.strip()}")
        else:
            print("repo_head: UNAVAILABLE")
    except OSError:
        print("repo_head: UNAVAILABLE")

    print(f"id: {m.id}")
    print(f"type: {m.type}")
    if result:
        print(f"result: {result}")
    if reason:
        print(f"reason: {reason}")
    for key in ("namespace", "resolved_at", "falsifier", "do_not_infer"):
        if meta.get(key):
            print(f"{key}: {meta[key]}")
    print(f"artifact_class: {artifact}")

    claim = "UNKNOWN"
    head_ok = None
    wt_ok = None

    if scope and artifact in ("HEAD", "HEAD_OR_WORKTREE", "WORKTREE", ""):
        # Default remasure surface is HEAD unless artifact_class says WORKTREE-only.
        if artifact != "WORKTREE":
            digests = []
            missing = []
            for rel in scope:
                blob = _git_show_head(repo, rel)
                if blob is None:
                    missing.append(rel)
                else:
                    digests.append(_sha256_hex(blob))
            if missing:
                print(f"fingerprint_head: MISSING_PATHS {','.join(missing)}")
                head_ok = False
            elif fp_head:
                # One path: compare that file's digest. Multi-path: digest of joined digests.
                if len(digests) == 1:
                    got = digests[0]
                else:
                    got = _sha256_hex(chr(10).join(digests).encode("ascii"))
                head_ok = got == fp_head
                print(f"fingerprint_head: {'MATCH' if head_ok else 'MISMATCH'} got={got}")
            else:
                # No expected digest — report computed HEAD digest for the scope
                got = digests[0] if len(digests) == 1 else _sha256_hex(chr(10).join(digests).encode("ascii"))
                print(f"fingerprint_head: COMPUTED got={got}")
                head_ok = None

        if artifact in ("WORKTREE", "HEAD_OR_WORKTREE") or fp_wt:
            digests_w = []
            missing_w = []
            for rel in scope:
                blob = _worktree_bytes(repo, rel)
                if blob is None:
                    missing_w.append(rel)
                else:
                    digests_w.append(_sha256_hex(blob))
            if missing_w:
                print(f"fingerprint_worktree: MISSING_PATHS {','.join(missing_w)}")
                wt_ok = False
            else:
                got_w = digests_w[0] if len(digests_w) == 1 else _sha256_hex(chr(10).join(digests_w).encode("ascii"))
                if fp_wt:
                    wt_ok = got_w == fp_wt
                    print(f"fingerprint_worktree: {'MATCH' if wt_ok else 'MISMATCH'} got={got_w}")
                else:
                    print(f"fingerprint_worktree: COMPUTED got={got_w}")

        if head_ok is True and (wt_ok is True or wt_ok is None):
            claim = "HEAD_MATCH"
        elif head_ok is False and wt_ok is True:
            claim = "WIP_ONLY"
        elif head_ok is False and (wt_ok is False or wt_ok is None):
            claim = "DIVERGED"
        elif head_ok is True and wt_ok is False:
            claim = "DIVERGED"
        elif meta.get("claim_currentness"):
            claim = meta["claim_currentness"].upper()

    if meta.get("claim_currentness") and claim == "UNKNOWN":
        claim = meta["claim_currentness"].upper()
    print(f"claim_currentness: {claim}")

    ver = _parse_verification_block(m.body)
    if ver:
        print("verification: (recipe only — not executed)")
        for k in sorted(ver):
            print(f"  {k}: {ver[k]}")
        if "expected.value" in ver or "expected" in ver:
            print("  note: run the command yourself; compare to expected — postbox stays dumb")

    if result == "CANNOT_VERIFY":
        print("outcome: CANNOT_VERIFY — do not invent a substitute census")
        return 0

    if claim in ("WIP_ONLY", "DIVERGED"):
        return 1
    if head_ok is False and artifact != "WORKTREE":
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inter-agent mail (see PROTOCOL.md)")
    ap.add_argument("--version", action="version", version=f"agent-postbox {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send")
    s.add_argument("--type", required=True)
    s.add_argument("--from", dest="sender", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--subject", required=True)
    s.add_argument("--reply-to", default="",
                    help="message id this replies to (preferred over --re)")
    s.add_argument("--re", default="",
                    help="legacy alias for --reply-to")
    s.add_argument("--scope", default="",
                   help="CLAIM only: the resource being claimed; a second held claim "
                        "on the same scope from another sender is refused (exit 3)")
    s.add_argument("--key", default="",
                   help="idempotency key: a retry with the same key writes nothing "
                        "and returns the original id")
    s.add_argument("--key-wait", dest="key_wait", default=None, metavar="SECONDS",
                   help=f"with --key: how long a retry waits for a reservation it finds "
                        f"unresolved (0 to {IDEM_WAIT_MAX:g}, default {IDEM_WAIT_DEFAULT:g}, or "
                        f"${IDEM_WAIT_ENV}). A live sender is never taken over: when "
                        "the wait runs out the retry exits 3. A sender that died is "
                        "taken over at once at any value. Only a marker written by a "
                        "version without the marker lock is taken over on the wait "
                        "alone, so a short wait there can publish the key twice "
                        "(PROTOCOL.md section 31)")
    s.add_argument("--supersedes", default="",
                    help="message id this structurally replaces (never edit the old file)")
    s.add_argument("--expires", default="")
    s.add_argument("--body", default="",
                    help="short single-line body only; multi-line / shell metacharacters refused")
    s.add_argument("--body-file", default="",
                    help="read body from PATH, or - for stdin (preferred for real mail)")
    s.add_argument("--result", default="",
                    help="optional outcome, e.g. CANNOT_VERIFY")
    s.add_argument("--reason", default="",
                    help="e.g. INSUFFICIENT_PRIVILEGE")
    s.add_argument("--namespace", default="")
    s.add_argument("--resolved-at", dest="resolved_at", default="")
    s.add_argument("--falsifier", default="")
    s.add_argument("--do-not-infer", dest="do_not_infer", default="")
    s.add_argument("--artifact-class", dest="artifact_class", default="",
                    help="HEAD | WORKTREE | PROCESS")
    s.add_argument("--claim-currentness", dest="claim_currentness", default="")
    s.add_argument("--evidence-scope", dest="evidence_scope", default="",
                    help="comma-separated paths relative to --repo for verify")
    s.add_argument("--fingerprint-head", dest="fingerprint_head", default="")
    s.add_argument("--fingerprint-worktree", dest="fingerprint_worktree", default="")
    s.add_argument("--blocked-action", dest="blocked_action", default="",
                    help="concrete action that must wait (PROTOCOL.md §18)")
    s.add_argument("--blocker", default="",
                    help="what prevents blocked_action")
    s.add_argument("--not-blocked", dest="not_blocked", default="",
                    help="what is explicitly not gated")
    s.add_argument("--unblock-condition", dest="unblock_condition", default="",
                    help="observable that clears the block")
    s.add_argument("--safe-parallel-work", dest="safe_parallel_work", default="",
                    help="work peers may do without waiting")
    s.set_defaults(func=cmd_send)

    list_p = sub.add_parser("list", help="forensic chronology (full history)")
    list_p.add_argument("--to", default="")
    list_p.add_argument("--from", dest="sender", default="")
    list_p.add_argument("--live", action="store_true")
    list_p.set_defaults(func=cmd_list)

    ib = sub.add_parser("inbox", help="actionable view: open/blocked/held/owed-to-me")
    ib.add_argument("--to", required=True, help="identity whose inbox to show")
    ib.set_defaults(func=cmd_inbox)

    sh = sub.add_parser("show")
    sh.add_argument("id")
    sh.set_defaults(func=cmd_show)

    v = sub.add_parser("verify",
                       help="remasure scoped fingerprints vs HEAD; print recipes; never executes")
    v.add_argument("id")
    v.add_argument("--repo", default="",
                   help="git repo root for HEAD/worktree fingerprints (default: cwd)")
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("doctor", help="read-only mailbox health; never mutates")
    d.set_defaults(func=cmd_doctor)

    stp = sub.add_parser("status", help="read-only counts: live by type, open ASKs, "
                                        "claims, quarantine, stalled agents")
    stp.add_argument("--json", action="store_true", help="stable machine-readable output")
    stp.add_argument("--now", default="", help="evaluate at this ISO-8601 instant (tests)")
    stp.add_argument("--scope", default=None,
                     help="list only the claims on exactly this scope (counts still cover the mailbox)")
    stp.add_argument("--stalled-hours", type=float, default=DEFAULT_STALLED_HOURS,
                     help=f"flag agents silent longer than this (default {DEFAULT_STALLED_HOURS:g})")
    stp.set_defaults(func=cmd_status)

    c = sub.add_parser("canary", help="file a pickup-canary NOTICE with expiry")
    c.add_argument("--from", dest="sender", required=True)
    c.add_argument("--to", required=True)
    c.add_argument("--hours", default="24",
                   help="canary TTL in hours (default 24)")
    c.set_defaults(func=cmd_canary)

    lat = sub.add_parser(
        "latency",
        help="RO ASK→ANSWER latency from durable date stamps only",
    )
    lat.add_argument("--to", default="",
                     help="optional: only ASKs addressed to this identity")
    lat.set_defaults(func=cmd_latency)

    cap = sub.add_parser(
        "capability",
        help="print can|cannot|CANNOT_VERIFY for identity+capability",
    )
    cap.add_argument("--identity", required=True)
    cap.add_argument("--capability", required=True)
    cap.set_defaults(func=cmd_capability)

    ask = sub.add_parser(
        "ask",
        help="send ASK; optional --capability filters --to to declared can:",
    )
    ask.add_argument("--from", dest="sender", required=True)
    ask.add_argument("--to", required=True,
                     help="comma-separated candidate identities")
    ask.add_argument("--subject", required=True)
    ask.add_argument("--capability", default="",
                     help="only include recipients with declared can: <id>")
    ask.add_argument("--body", default="",
                     help="short single-line body only")
    ask.add_argument("--body-file", default="",
                     help="read body from PATH, or - for stdin")
    ask.set_defaults(func=cmd_ask)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        _rc = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (`list | head`): not worth a traceback. Point
        # stdout at /dev/null so the interpreter's flush at exit stays quiet.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        _rc = 141  # 128 + SIGPIPE, what a shell reports for a writer killed by it
    raise SystemExit(_rc)
