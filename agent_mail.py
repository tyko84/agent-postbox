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
import re  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import datetime as dt  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
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

__version__ = "0.2.0"

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

class _Index:
    """One pass over `everything`: which ids are replied to / superseded.

    Pure derivation (nothing stored). It exists so list/inbox/status stay
    O(n) on a big box instead of re-scanning every message per message.
    """
    __slots__ = ("replied", "superseded_by")

    def __init__(self, everything: list[Message]) -> None:
        self.replied: set[str] = {m.re_id for m in everything if m.re_id}
        self.superseded_by: dict[str, set[str]] = {}
        for m in everything:
            if m.supersedes:
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
        return msg.id not in _idx.replied
    return not any(m.re_id and m.re_id == msg.id for m in everything)


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
        superseded = any((m.supersedes or "").upper() == mid.upper()
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
        if m.type == "ANSWER" and m.re_id and m.re_id == ask.id
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
        except FileNotFoundError:
            pass


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


def _idem_claim(box: Path, sender: str, to: str, key: str, msg_id: str,
                mtype: str, subject: str, body: str) -> str:
    """Reserve (sender, to, key) with an O_EXCL marker. Returns "" when this send
    owns the key, the original id for an exact retry, or "CONFLICT" when the same
    key arrives with different content. A marker whose message never landed (crash
    between reserve and publish) is taken over under a mutex, re-checking that the
    marker is still the same orphan, so simultaneous takeovers cannot both win."""
    content = hashlib.sha256(f"{mtype}\0{subject}\0{body}".encode()).hexdigest()
    marker = _idem_marker(box, sender, to, key)
    mutex = marker.with_name(marker.name + ".takeover")
    payload = f"{msg_id}\n{content}\n"
    for _ in range(6):
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            try:
                seen = marker.read_text(encoding="utf-8")
                old_id, old_content = seen.split("\n")[:2]
            except (OSError, ValueError):
                time.sleep(0.05)
                continue  # being written by a concurrent sender; re-read
            if old_content != content:
                return "CONFLICT"
            for _wait in range(100):  # the original may be mid-publish: give it 5s
                if any(box.glob(f"{old_id}-*.md")):
                    return old_id
                if not marker.exists():
                    break  # its sender gave up (refused/failed); race for the key
                time.sleep(0.05)
            else:
                try:
                    mfd = os.open(mutex, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                except FileExistsError:
                    try:
                        if time.time() - mutex.stat().st_mtime > 30:
                            mutex.unlink(missing_ok=True)
                    except FileNotFoundError:
                        pass
                    continue
                os.close(mfd)
                try:
                    # compare-and-delete: only remove the orphan we observed
                    if marker.read_text(encoding="utf-8") == seen:
                        marker.unlink(missing_ok=True)
                except OSError:
                    pass
                finally:
                    mutex.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        return ""
    return "CONFLICT"


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
        box.mkdir(parents=True, exist_ok=True)
    lock: Path | None = None
    marker: Path | None = None
    try:
        if scope:
            lock = box / f".scope.{hashlib.sha256(scope.encode()).hexdigest()[:24]}.lock"
            try:
                if now.timestamp() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)  # crashed holder
            except FileNotFoundError:
                pass
            try:
                os.close(os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
            except FileExistsError:
                lock = None  # not ours: must not be released below
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
            prior = _idem_claim(box, sender, to, idem, msg_id, mtype, args.subject, body)
            if prior == "CONFLICT":
                print(f"idempotency key {idem!r} was already used with different content; "
                      "refusing (use a new key for a new message)", file=sys.stderr)
                return 2
            if prior:
                print(f"duplicate of {prior} (key {idem!r}); nothing written")
                print(f"id: {prior}")
                return 0
            marker = _idem_marker(box, sender, to, idem)
            lines.insert(lines.index("---", 1), f"idem: {idem}")
        try:
            _publish(box / f"{fname}.md", chr(10).join(lines))
        except FileExistsError:
            print(f"ABORT: {fname}.md already exists; refusing to overwrite a message.",
                  file=sys.stderr)
            return 2
        marker = None  # published: the marker now guards a real message
    finally:
        if marker is not None:
            marker.unlink(missing_ok=True)  # reserved but never published: release
        if lock is not None:
            lock.unlink(missing_ok=True)
    print(f"wrote {box / (fname + '.md')}")
    print(f"id: {msg_id}")
    return 0


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


def build_status(everything: list[Message], rejects: list[str], box: Path,
                 now: dt.datetime, stalled_hours: float = DEFAULT_STALLED_HOURS) -> dict[str, Any]:
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
            "last_message_at": when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "age_seconds": age,
        })
    stale_tmp = 0
    if box.is_dir():
        for tmp in box.glob(".*.tmp"):
            try:
                if now.timestamp() - tmp.lstat().st_mtime > 600:
                    stale_tmp += 1
            except OSError:
                pass
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
        "stalled_agents": [str(a["agent"]) for a in agents if a["state"] == "stalled"],
    }


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
    everything, rejects = load_messages()
    st = build_status(everything, rejects, box, now, args.stalled_hours)
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
    print(f"quarantined: {st['quarantined']}  stale_tmp: {st['stale_tmp']}")
    print(f"agents (stalled after {args.stalled_hours:g}h):")
    for a in st["agents"]:
        print(f"  {a['agent']:<24} {a['state']:<8} last={a['last_message_at'] or '-'}"
              f"  age={_fmt_age(a['age_seconds'])}")
    if st["stalled_agents"]:
        print("STALLED: " + ", ".join(st["stalled_agents"]))
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

    stale = [p.name for p in box.glob(".*.tmp")
             if now.timestamp() - p.stat().st_mtime > 600]
    print(f"stale_tmp: {len(stale)}" + ("".join(f"\n  - {n}" for n in stale[:20])))

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
    for m in read_all():
        if msg_id == m.id or msg_id == m.path.stem:
            return m
        if _is_ulid(msg_id) and m.path.stem.startswith(msg_id + "-"):
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
    raise SystemExit(main())
