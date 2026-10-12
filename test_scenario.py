#!/usr/bin/env python3
"""Focused tests of scripts/scenario.py, the benchmark and reliability harness.

A tiny configuration with a fixed seed, run as a subprocess the way CI runs it:
the schedule hash is the same across two runs and across the two
configurations, a planted FAIL (--self-test-miss) exits 1 with a FAIL line, the
JSON report has the documented fields, a usage error exits 2, and an exception
inside the harness exits 1, never 0. Skipped where scripts/scenario.py is not
present (an unpacked sdist ships the tests, not the scripts). Stdlib only.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "scripts" / "scenario.py"
TINY = ["--seed", "7", "--agents", "3", "--messages", "12", "--rounds", "4", "--drain", "1",
        "--recovery-bound", "2"]
TIMEOUT = 300


def run(*args: str) -> tuple[int, str, str]:
    cp = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                        text=True, timeout=TIMEOUT)
    return cp.returncode, cp.stdout, cp.stderr


@unittest.skipUnless(SCRIPT.is_file(), "scripts/scenario.py is not here (an unpacked sdist?)")
class Harness(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    first: dict[str, Any]
    first_out: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-scenario-")
        path = Path(cls.tmp.name) / "first.json"
        rc, out, err = run(*TINY, "--json", str(path))
        assert rc == 0, f"rc={rc}\n{out}\n{err}"
        cls.first = json.loads(path.read_text(encoding="utf-8"))
        cls.first_out = out

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_the_tiny_run_passes_with_ok_lines_for_every_threshold(self) -> None:
        lines = self.first_out.splitlines()
        oks = [ln for ln in lines if ln.startswith("OK ")]
        names = {ln.split()[1] for ln in oks}
        for required in ("schedule-hash", "positive-control", "missed", "duplicates",
                         "conflicting-claims", "malformed", "recovery", "send-exit",
                         "status-clean", "leftovers", "pickup-exit"):
            self.assertIn(required, names, self.first_out)
        self.assertFalse([ln for ln in lines if ln.startswith("FAIL ")], self.first_out)
        self.assertTrue(lines[-1].startswith("passed: "), lines[-1])
        # the summary has both configurations side by side, one line per metric
        self.assertRegex(self.first_out, r"(?m)metric\s+hook\s+baseline$")
        self.assertRegex(self.first_out, r"(?m)missed\s+0\s+\d+$")
        self.assertRegex(self.first_out, r"(?m)latency p99\s+n/a \(n=\d+\)\s+n/a \(n=\d+\)$")

    def test_the_json_report_has_the_documented_fields(self) -> None:
        d = self.first
        for key in ("schema", "harness", "tool_version", "environment", "parameters", "seed",
                    "schedule_hash", "poll_hash", "schedule", "configs", "thresholds", "checks",
                    "passed", "elapsed_seconds"):
            self.assertIn(key, d)
        self.assertEqual(d["schema"], 1)
        self.assertEqual(d["seed"], 7)
        self.assertTrue(d["passed"])
        self.assertEqual(sorted(d["configs"]), ["baseline", "hook"])
        self.assertEqual({"python", "implementation", "platform"}, set(d["environment"]))
        self.assertEqual(d["parameters"]["agents"], 3)
        self.assertEqual(d["parameters"]["faults"], sorted(d["schedule"]["faults"]))
        self.assertEqual(d["thresholds"]["missed"], 0)
        self.assertEqual(d["thresholds"]["recovery_bound"], 2)
        self.assertEqual(len(d["schedule_hash"]), 64)
        for cfg in ("hook", "baseline"):
            m = d["configs"][cfg]
            for key in ("pickup", "rounds", "wall_seconds", "sends", "pickups", "delivery",
                        "latency_seconds", "latency_boundaries", "duplicates", "claims", "asks",
                        "malformed", "recovery"):
                self.assertIn(key, m, cfg)
            self.assertEqual({"n", "min", "max", "mean", "p50", "p95", "p99"},
                             set(m["latency_seconds"]))
            self.assertEqual(m["sends"]["scheduled"] + m["sends"]["killed"],
                             d["schedule"]["messages"], cfg)
        self.assertEqual(d["configs"]["hook"]["delivery"]["missed"], 0)
        self.assertEqual(d["configs"]["hook"]["malformed"]["surfaced"], 0)
        self.assertEqual(d["configs"]["hook"]["malformed"]["quarantined_at_end"],
                         d["configs"]["hook"]["malformed"]["planted"])
        self.assertTrue(all(c["ok"] for c in d["checks"]))
        self.assertEqual([c["name"] for c in d["checks"]][:2], ["schedule-hash", "positive-control"])

    def test_the_same_seed_gives_the_same_schedule(self) -> None:
        path = Path(self.tmp.name) / "second.json"
        rc, out, err = run(*TINY, "--config", "hook", "--json", str(path))
        self.assertEqual(rc, 0, out + err)
        second = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(second["schedule_hash"], self.first["schedule_hash"])
        self.assertEqual(second["schedule"], self.first["schedule"])
        self.assertIsNone(second["poll_hash"])            # no baseline in this run
        self.assertIsNotNone(self.first["poll_hash"])
        self.assertEqual(second["configs"]["hook"]["sends"]["scheduled"],
                         self.first["configs"]["hook"]["sends"]["scheduled"])
        # the same hash is printed for both configurations of the first run
        hashes = [ln.split()[2] for ln in self.first_out.splitlines()
                  if ln.startswith("[") and "schedule_hash" in ln]
        self.assertEqual(len(set(hashes)), 1, hashes)
        self.assertTrue(self.first["schedule_hash"].startswith(hashes[0]))
        # a different seed is a different schedule (control)
        rc, out, err = run(*TINY[2:], "--seed", "8", "--config", "hook", "--json", str(path))
        self.assertEqual(rc, 0, out + err)
        self.assertNotEqual(json.loads(path.read_text(encoding="utf-8"))["schedule_hash"],
                            self.first["schedule_hash"])

    def test_a_planted_miss_fails_the_run(self) -> None:
        path = Path(self.tmp.name) / "miss.json"
        rc, out, err = run(*TINY, "--config", "hook", "--self-test-miss", "--json", str(path))
        self.assertEqual(rc, 1, out + err)
        self.assertRegex(out, r"(?m)FAIL\s+missed\s+[1-9]\d* of \d+ deliverable", out)
        self.assertIn("NOT PASSED", err)
        d = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(d["passed"])
        self.assertGreater(d["configs"]["hook"]["delivery"]["missed"], 0)
        self.assertFalse([c for c in d["checks"] if c["name"] == "missed"][0]["ok"])

    def test_a_usage_error_exits_2(self) -> None:
        rc, out, err = run("--agents", "1", "--messages", "3")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("scenario:", err)
        rc, out, err = run("--faults", "no-such-fault")
        self.assertEqual(rc, 2, out + err)

    def test_an_exception_inside_the_harness_never_exits_0(self) -> None:
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import scenario
        finally:
            sys.path.pop(0)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(scenario, "build_schedule",
                               side_effect=RuntimeError("planted harness failure")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = scenario.main([*TINY, "--config", "hook"])
        self.assertEqual(rc, 1)
        self.assertIn("planted harness failure", err.getvalue())
        self.assertIn("the harness itself failed", err.getvalue())
        # and the same through the command line, where a usage error is 2 and a crash 1
        cp = subprocess.run(
            [sys.executable, "-I", "-c",
             f"import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); import scenario\n"
             "scenario.Run.run = lambda self: 1 / 0\n"
             f"sys.exit(scenario.main({[*TINY, '--config', 'hook']!r}))"],
            capture_output=True, text=True, timeout=TIMEOUT)
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertIn("ZeroDivisionError", cp.stderr)


if __name__ == "__main__":
    unittest.main()
