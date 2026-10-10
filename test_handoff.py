#!/usr/bin/env python3
"""Tests for check_handoff.py (PROTOCOL.md section 28). Stdlib only."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import check_handoff as ch

ROOT = Path(__file__).resolve().parent
GOOD = (ROOT / "docs" / "handoff-example.md").read_text(encoding="utf-8")


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "check_handoff.py"), *args],
        input=stdin, text=True, capture_output=True,
    )


class HandoffValidator(unittest.TestCase):
    def test_valid_packet_passes(self) -> None:
        self.assertEqual(ch.problems(GOOD), [])
        r = run("-", stdin=GOOD)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("all required fields present", r.stdout)

    def test_each_missing_field_is_listed(self) -> None:
        for field in ch.REQUIRED:
            lines = GOOD.splitlines()
            # drop the field line and any continuation lines under it
            out, skipping = [], False
            for ln in lines:
                if ln.startswith(field + ":"):
                    skipping = True
                    continue
                if skipping and (ln.startswith((" ", "\t"))):
                    continue
                skipping = False
                out.append(ln)
            r = run("-", stdin="\n".join(out))
            self.assertEqual(r.returncode, 1, field)
            self.assertIn(f"{field}: missing", r.stderr)
            self.assertEqual(len(ch.problems("\n".join(out))), 1, field)

    def test_template_is_rejected_with_every_field_listed(self) -> None:
        r = run(str(ROOT / "handoff_template.md"))
        self.assertEqual(r.returncode, 1)
        for field in ch.REQUIRED:
            self.assertIn(field, r.stderr)

    def test_empty_and_placeholder_values_rejected(self) -> None:
        for bad in ("", "TODO", "tbd", "<fill me>"):
            text = GOOD.replace("ROLLBACK: revert the merge commit and restart the worker; "
                                "no data migration is involved", f"ROLLBACK: {bad}")
            self.assertEqual(ch.problems(text), ["ROLLBACK: empty or placeholder"], bad)

    def test_bad_sha_rejected(self) -> None:
        text = GOOD.replace("BASE_SHA: 3a2c90e", "BASE_SHA: not-a-sha")
        self.assertEqual(len(ch.problems(text)), 1)
        self.assertIn("BASE_SHA", ch.problems(text)[0])

    def test_usage_and_unreadable_exit_2(self) -> None:
        self.assertEqual(run().returncode, 2)
        self.assertEqual(run("/nonexistent/packet.md").returncode, 2)

    def test_values_do_not_bleed_between_fields(self) -> None:
        got = ch.parse(GOOD)
        self.assertEqual(got["SOURCE_BRANCH"], "feature/retry-backoff")
        self.assertNotIn("SOURCE_SHA", got["SOURCE_BRANCH"])
        self.assertIn("billing/client.py", got["CHANGED_FILES"])


if __name__ == "__main__":
    unittest.main()
