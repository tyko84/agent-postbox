#!/usr/bin/env python3
"""Offline tests of scripts/experiment_micro.py, the two-agent micro-experiment with real
sessions (docs/micro-experiment.md). No model is called and no account is needed.

Checked here: the statistics helper; seeded instance codes and arm order (deterministic,
every arm once); that the outcome of a run is computed from the files and from nothing a
model says (right code, wrong code, none; the function check); the message counters of
each arm; that the spend ceiling refuses a session before it starts; that the permission
rules cover every form of the mail command the hook prints; and exit 2 when `claude` is
not on PATH. Skipped where scripts/ is not present (an unpacked sdist ships the tests,
not the scripts). Stdlib only.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "scripts" / "experiment_micro.py"


def load() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("experiment_micro", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(SCRIPT.exists(), "scripts/ is not present")
class MicroExperiment(unittest.TestCase):
    def setUp(self) -> None:
        self.m = load()
        tmp = tempfile.TemporaryDirectory(prefix="test-micro-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.args = argparse.Namespace(model="haiku", max_calls=10**6, ceiling_usd=1.0)
        self.exp = self.m.Experiment(self.args, "claude", self.root / "work", self.root / "out")

    def project(self, window: str, util: str) -> Path:
        proj = self.root / "proj"
        (proj / "app").mkdir(parents=True, exist_ok=True)
        (proj / "app" / "config.py").write_text(f"WINDOW_CODE = {window}\n")
        (proj / "app" / "util.py").write_text(util)
        return proj

    GOOD = ("def is_weekend(day):\n    return day in ('saturday', 'sunday')\n")

    def test_wilson_interval(self) -> None:
        lo, hi = self.m.wilson(0, 0)
        self.assertEqual((lo, hi), (0.0, 1.0))
        lo, hi = self.m.wilson(10, 10)
        self.assertLess(lo, 1.0)
        self.assertAlmostEqual(hi, 1.0, places=6)
        lo, hi = self.m.wilson(5, 10)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)

    def test_seeded_codes_and_order(self) -> None:
        self.assertEqual(self.m.code_for(7), self.m.code_for(7))
        self.assertNotEqual(self.m.code_for(7), self.m.code_for(8))
        self.assertRegex(self.m.code_for(7), r"^W-[0-9A-F]{4}$")
        for seed in range(1, 30):
            order = self.m.arm_order(seed)
            self.assertEqual(sorted(order), sorted(self.m.ARMS))
            self.assertEqual(order, self.m.arm_order(seed))

    def test_the_outcome_comes_from_the_files(self) -> None:
        code = "W-AB12"
        rec: dict[str, Any] = {"sessions": [
            {"agent": "agent-b", "team_lines": [], "read_notes": False, "hook_stdout": "",
             "cost_usd": 0.001},
            {"agent": "agent-a", "team_lines": [], "read_notes": False, "hook_stdout": "",
             "cost_usd": 0.001}]}
        (self.root / "NOTES.md").write_text("# Team notes\n")
        cases = {
            "ok": (f"'{code}'", self.GOOD, "ok", True, True),
            "wrong": ("'W-0000'", self.GOOD, "wrong", True, False),
            "none": ("None", self.GOOD, "none", True, False),
            "a_broken": (f"'{code}'", "def is_weekend(day):\n    return False\n", "ok", False, False),
        }
        for name, (window, util, want, a_ok, done) in cases.items():
            with self.subTest(case=name):
                proj = self.project(window, util)
                (proj / "NOTES.md").write_text("# Team notes\n")
                out = self.exp.measure("C2", proj, code, rec)
                self.assertEqual((out["window"], out["a_ok"], out["completed"]),
                                 (want, a_ok, done))
                self.assertEqual(out["missed"], want != "ok")

    def test_c2_counts_note_lines_and_reads(self) -> None:
        proj = self.project("None", self.GOOD)
        (proj / "NOTES.md").write_text("# Team notes\nagent-b: need code\nagent-a: W-1\n")
        rec = {"sessions": [{"agent": "agent-b", "read_notes": True, "team_lines": [],
                             "hook_stdout": "", "cost_usd": 0.0},
                            {"agent": "agent-a", "read_notes": False, "team_lines": [],
                             "hook_stdout": "", "cost_usd": 0.0}]}
        out = self.exp.measure("C2", proj, "W-1", rec)
        self.assertEqual((out["generated"], out["delivered"], out["acknowledged"]), (2, 1, None))
        self.assertTrue(out["a_shared"])

    def test_c1_counts_relayed_lines(self) -> None:
        proj = self.project("None", self.GOOD)
        rec = {"sessions": [{"agent": "agent-b", "team_lines": ["TO TEAM: need code"],
                             "read_notes": False, "hook_stdout": "", "cost_usd": 0.0},
                            {"agent": "agent-a", "team_lines": ["TO TEAM: W-1"],
                             "read_notes": False, "hook_stdout": "", "cost_usd": 0.0},
                            {"agent": "agent-b", "team_lines": [], "read_notes": False,
                             "hook_stdout": "", "cost_usd": 0.0}]}
        out = self.exp.measure("C1", proj, "W-1", rec)
        self.assertEqual((out["generated"], out["delivered"]), (2, 2))
        self.assertTrue(out["a_shared"])

    def test_summary_counts(self) -> None:
        base = {"a_ok": True, "generated": 1, "delivered": 1, "acknowledged": None,
                "seconds": 1.0, "cost_usd": 0.01}
        rows = [dict(base, arm="P", window="ok", completed=True),
                dict(base, arm="P", window="none", completed=False),
                dict(base, arm="C2", window="wrong", completed=False),
                dict(base, arm="C1", window="ok", completed=True, aborted="x")]
        s = self.m.summarise(rows)
        self.assertEqual((s["P"]["runs"], s["P"]["window_ok"], s["P"]["window_none"]), (2, 1, 1))
        self.assertEqual(s["C2"]["window_wrong"], 1)
        self.assertEqual(s["C1"]["runs"], 0)    # an aborted run is listed elsewhere, not counted

    def test_the_ceiling_refuses_before_a_session_starts(self) -> None:
        self.exp.spent = 0.9
        with self.assertRaises(self.m.Ceiling):
            self.exp.guard()                     # 0.9 + 1.5 * 0.30 > 1.0
        self.exp.spent, self.exp.biggest = 0.1, 0.1
        self.exp.guard()                         # 0.1 + 1.5 * 0.1 <= 1.0

    def test_permission_rules_cover_every_printed_form(self) -> None:
        proj = self.root / "proj"
        rules = self.exp.allowed("P", proj)
        self.assertEqual(self.exp.allowed("C1", proj), ("Read", "Edit", "Write"))
        absolute = str(proj / self.m.DEST / "agent_mail.py")
        for form in (f"{self.m.DEST}/agent_mail.py", f'"{absolute}"', f"'{absolute}'", absolute):
            self.assertIn(f"Bash(python3 {form} *)", rules)

    def test_without_claude_it_exits_2(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PATH"}
        env["PATH"] = str(self.root)
        cp = subprocess.run([sys.executable, "-I", str(SCRIPT), "--out", str(self.root / "o")],
                            capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertIn("claude", cp.stderr)
        self.assertFalse((self.root / "o").exists())


if __name__ == "__main__":
    unittest.main()
