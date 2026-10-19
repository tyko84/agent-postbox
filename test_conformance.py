#!/usr/bin/env python3
"""conformance/run_conformance.py runs, groups its checks, and cannot pass when nothing works.

* against this checkout's agent_mail.py the full run exits 0, prints the three groups, and
  every OPTIONAL check is a pass or a skip, never a failure (a feature this tool lacks is
  skipped, not failed);
* --protocol-only prints the protocol-level group alone;
* --json-out writes one record per check with a group and a status;
* a program that does nothing and exits 0 (what a broken adapter looks like) fails the
  protocol group, exits 1, and does not crash the runner;
* a tool that cannot be run at all exits 2, not 0 or 1.

Stdlib only; no network, no model.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "conformance" / "run_conformance.py"


def run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(RUNNER), *argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


class Conformance(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    out: Path
    full: subprocess.CompletedProcess[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.out = Path(cls.tmp.name) / "r.json"
        cls.full = run("--json-out", str(cls.out))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_full_run_passes_and_groups(self) -> None:
        self.assertEqual(self.full.returncode, 0, self.full.stdout[-1500:] + self.full.stderr[-500:])
        for title in ("PROTOCOL-LEVEL", "IMPLEMENTATION-SPECIFIC", "OPTIONAL"):
            self.assertIn(f"== {title}", self.full.stdout)

    def test_json_records_and_optional_never_fails(self) -> None:
        data = json.loads(self.out.read_text(encoding="utf-8"))
        rows = data["results"]
        self.assertGreaterEqual(sum(r["group"] == "protocol" for r in rows), 40)
        self.assertTrue(all(r["status"] in ("PASS", "FAIL", "SKIP") for r in rows))
        self.assertFalse([r for r in rows if r["group"] == "optional" and r["status"] == "FAIL"])
        self.assertFalse([r for r in rows if r["group"] != "optional" and r["status"] == "SKIP"])

    def test_protocol_only_prints_one_group(self) -> None:
        r = run("--protocol-only")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:])
        self.assertIn("== PROTOCOL-LEVEL", r.stdout)
        self.assertNotIn("== IMPLEMENTATION-SPECIFIC", r.stdout)
        self.assertNotIn("== OPTIONAL", r.stdout)

    def test_a_program_that_does_nothing_fails(self) -> None:
        r = run("--protocol-only", "--cmd", sys.executable, "-c", "pass")
        self.assertEqual(r.returncode, 1, r.stdout[-800:] + r.stderr[-800:])
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("check(s) failed", r.stdout)

    def test_a_tool_that_cannot_run_exits_2(self) -> None:
        self.assertEqual(run("--tool", str(HERE / "no-such-tool.py")).returncode, 2)
        self.assertEqual(run("--cmd", "no-such-program-for-conformance").returncode, 2)


if __name__ == "__main__":
    unittest.main()
