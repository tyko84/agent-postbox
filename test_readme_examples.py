#!/usr/bin/env python3
"""Docs are tested: README examples run, and section references resolve.

* Every fenced block tagged ```bash runnable in README.md is executed, in
  order, as ONE bash script (so exports persist) in a scratch mailbox, with an
  `agent-postbox` command on PATH that runs this checkout's agent_mail.py.
* Every "section N" / section-sign N reference in the markdown docs must match
  a real "## N." heading in PROTOCOL.md.

Positive controls: a planted failing example and a planted bad reference must
be caught by the same instruments. Stdlib only.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_FENCE = re.compile(r"^```bash runnable[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
_HEADING = re.compile(r"^## (\d+[a-z]?)\.", re.M)
_SECT = re.compile(
    r"(?:§|\b[Ss]ections?\s+)(\d+[a-z]?)"
    r"(?:\s*(?:-|–|and|/|,)\s*§?(\d+[a-z]?))?"
)


def runnable_blocks(markdown: str) -> list[str]:
    return _FENCE.findall(markdown)


def run_blocks(blocks: list[str]) -> subprocess.CompletedProcess[str]:
    """Run blocks as one `bash -e` script in a scratch dir; stdout+stderr merged."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        shim = bindir / "agent-postbox"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "agent_mail.py"}" "$@"\n'
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        work = Path(tmp) / "work"
        work.mkdir()
        script = "set -e\n" + "".join(
            f"echo '### block {i}'\n{b}\n" for i, b in enumerate(blocks, 1)
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            ["bash", "-c", script], cwd=work, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
        )


def protocol_sections() -> set[str]:
    return set(_HEADING.findall((ROOT / "PROTOCOL.md").read_text(encoding="utf-8")))


def bad_references(text: str, sections: set[str]) -> list[str]:
    """Section numbers cited in `text` that PROTOCOL.md does not have."""
    bad: list[str] = []
    for m in _SECT.finditer(text):
        first, second = m.group(1), m.group(2)
        cited = [first] + ([second] if second else [])
        # "10-28" style ranges: every integer in between must exist too
        if second and second.isdigit() and first.isdigit() and "-" in m.group(0).replace("–", "-"):
            cited = [str(n) for n in range(int(first), int(second) + 1)]
        bad += [c for c in cited if c not in sections]
    return bad


def doc_files() -> list[Path]:
    files = [p for p in ROOT.glob("*.md")] + list((ROOT / "docs").glob("*.md"))
    return sorted(files)


class ReadmeExamples(unittest.TestCase):
    def test_readme_runnable_examples_execute(self) -> None:
        blocks = runnable_blocks((ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(blocks), 5, "README lost its runnable examples")
        res = run_blocks(blocks)
        self.assertEqual(res.returncode, 0, f"README example failed:\n{res.stdout}")
        # Content that MUST appear (silence would prove nothing):
        self.assertIn("wrote ", res.stdout)
        self.assertIn("! ASK", res.stdout)          # the ASK was live for agent-b
        # live for `list` + `inbox` in block 2, gone from block 3's list after the ANSWER
        self.assertEqual(res.stdout.count("! ASK"), 2)
        self.assertIn("already held by agent-a", res.stdout)  # rival claim refused
        self.assertIn("store: OK", res.stdout)      # doctor ran

    def test_positive_control_failing_example_is_caught(self) -> None:
        self.assertNotEqual(run_blocks(["false"]).returncode, 0)
        bad = run_blocks([
            'export AGENT_MAIL_DIR="$PWD/nobox"',
            "agent-postbox list --to agent-b --live",   # missing store: must exit 2
        ])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("NO MAILBOX", bad.stdout)

    def test_section_references_resolve(self) -> None:
        sections = protocol_sections()
        self.assertIn("27", sections)
        self.assertIn("5a", sections)
        for path in doc_files():
            bad = bad_references(path.read_text(encoding="utf-8"), sections)
            self.assertEqual(bad, [], f"{path.name} cites missing PROTOCOL sections {bad}")

    def test_positive_control_bad_reference_is_caught(self) -> None:
        sections = protocol_sections()
        self.assertEqual(bad_references("see §99 and sections 10-99", sections).count("99"), 2)
        self.assertEqual(bad_references("see §8, section 5a, sections 10-27", sections), [])


if __name__ == "__main__":
    unittest.main()
