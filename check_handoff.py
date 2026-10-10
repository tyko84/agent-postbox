#!/usr/bin/env python3
"""Validate a handoff packet (PROTOCOL.md, "Handoff packet"). Stdlib only.

    python check_handoff.py PACKET.md        # or "-" for stdin
    exit 0  every required field present and filled in
    exit 1  one or more fields missing/empty/placeholder (listed on stderr)
    exit 2  usage error / unreadable file

A field is a line `FIELD_NAME: value`. The value may continue on following
lines (indented or bulleted) until the next field line. A value that is empty,
or still the template placeholder (`<...>`, `TODO`, `TBD`), counts as missing.
This checks that the packet is complete, not that it is true: a receiver still
verifies the claims (PROTOCOL.md section 0 -- a packet is evidence, not authority).
"""
from __future__ import annotations

import re
import sys

REQUIRED = (
    "SOURCE_BRANCH",
    "SOURCE_SHA",
    "BASE_SHA",
    "CHANGED_FILES",
    "TESTS_RUN",
    "DEPLOY_RADIUS",
    "ROLLBACK",
    "AUTHORIZATION_BOUNDARIES",
    "ACCEPTANCE_CRITERIA",
)
SHA_FIELDS = ("SOURCE_SHA", "BASE_SHA")

_FIELD = re.compile(r"^\s*(?:[-*]\s+)?\**([A-Z][A-Z_]+)\**\s*:\**\s*(.*)$")
_PLACEHOLDER = re.compile(r"^(<[^>]*>.*|todo|tbd|\.\.\.)$", re.IGNORECASE)
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def parse(text: str) -> dict[str, str]:
    """Map FIELD -> joined value for every recognised field line (first wins)."""
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        m = _FIELD.match(line)
        if m and m.group(1) in REQUIRED:
            current = m.group(1)
            if current not in fields:
                fields[current] = m.group(2).strip()
            else:
                current = None  # duplicate: first occurrence wins
            continue
        if m and not line.startswith((" ", "\t", "-", "*")):
            current = None  # an unrelated top-level FIELD: line ends the value
            continue
        if current is not None:
            fields[current] = (fields[current] + "\n" + line.strip()).strip()
    return fields


def _is_placeholder(value: str) -> bool:
    """True if every non-empty line (bullet stripped) is still template text."""
    lines = [re.sub(r"^[-*]\s+", "", ln.strip()) for ln in value.splitlines()]
    lines = [ln for ln in lines if ln]
    return not lines or all(_PLACEHOLDER.match(ln) for ln in lines)


def problems(text: str) -> list[str]:
    fields = parse(text)
    out: list[str] = []
    for name in REQUIRED:
        if name not in fields:
            out.append(f"{name}: missing")
            continue
        value = fields[name].strip()
        if _is_placeholder(value):
            out.append(f"{name}: empty or placeholder")
        elif name in SHA_FIELDS and not _SHA.match(value.lower()):
            out.append(f"{name}: not a 7-40 char hex SHA ({value!r})")
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        text = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read {args[0]}: {e}", file=sys.stderr)
        return 2
    bad = problems(text)
    if bad:
        print("INCOMPLETE handoff packet:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        return 1
    print("handoff packet: all required fields present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
