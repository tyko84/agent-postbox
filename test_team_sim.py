#!/usr/bin/env python3
"""Focused tests of scripts/team_sim.py, the task-level team simulation.

The simulation drives the real CLI and adapter with scripted agents (see the
module docstring and docs/coordination-experiment.md); these tests pin what makes
it trustworthy rather than what it concludes: same seed, same workload and same
counts; the two arms get the same workload (checked from the artifacts each run
leaves); the metrics are computed from those artifacts; a planted failure exits
1; a usage error exits 2; an exception inside the harness never exits 0; and the
harness cannot be rigged to always favour Postbox, because at p = 1.0 the
control arm is not worse on a metric the mechanism cannot improve (coordination
overhead) and the interval logic can report "control better". Skipped where
scripts/team_sim.py is not present (an unpacked sdist ships the tests, not the
scripts). Stdlib only.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "scripts" / "team_sim.py"
SMALL = ["--seed", "3", "--seeds", "2", "--p", "0.5,1.0", "--horizon", "20"]
TIMEOUT = 300


def run(*args: str) -> tuple[int, str, str]:
    cp = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                        text=True, timeout=TIMEOUT)
    return cp.returncode, cp.stdout, cp.stderr


def load() -> Any:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import team_sim
    finally:
        sys.path.pop(0)
    return team_sim


@unittest.skipUnless(SCRIPT.is_file(), "scripts/team_sim.py is not here (an unpacked sdist?)")
class Harness(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    first: dict[str, Any]
    first_out: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="test-team-sim-")
        path = Path(cls.tmp.name) / "first.json"
        rc, out, err = run(*SMALL, "--json", str(path))
        assert rc == 0, f"rc={rc}\n{out}\n{err}"
        cls.first = json.loads(path.read_text(encoding="utf-8"))
        cls.first_out = out

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_the_run_passes_and_says_it_is_simulated(self) -> None:
        lines = self.first_out.splitlines()
        self.assertTrue(lines[0].startswith("SIMULATED:"), lines[0])
        names = {ln.split()[1] for ln in lines if ln.startswith("OK ")}
        for required in ("schedule-hash", "positive-control", "cli-exit", "mechanism-gate"):
            self.assertIn(required, names, self.first_out)
        self.assertFalse([ln for ln in lines if ln.startswith("FAIL ")], self.first_out)
        self.assertEqual(lines[-1], "passed: True")
        self.assertRegex(self.first_out, r"(?m)^metric\s+postbox\s+control\s+diff\s+95% CI\s+verdict$")

    def test_the_json_report_has_the_documented_fields(self) -> None:
        d = self.first
        for key in ("schema", "harness", "simulated", "tool_version", "environment", "parameters",
                    "assumptions", "seeds", "schedule_hashes", "read_hashes", "cells", "comparisons",
                    "advantage_vanishes", "checks", "passed", "aborted", "elapsed_seconds"):
            self.assertIn(key, d)
        self.assertEqual((d["schema"], d["harness"], d["simulated"]), (1, "team_sim", True))
        self.assertEqual(d["seeds"], [3, 4])
        self.assertEqual(sorted(d["cells"]), ["control@0.5", "control@1.0", "postbox"])
        self.assertEqual(sorted(d["schedule_hashes"]), ["3", "4"])
        self.assertTrue(all(len(h) == 64 for h in d["schedule_hashes"].values()))
        self.assertEqual(len(d["read_hashes"]), 4)
        self.assertGreaterEqual(len(d["assumptions"]), 5)
        metrics = {"completion", "conflicts", "missed_at_commit", "facts_unused", "unanswered",
                   "recovery_steps", "calls_per_ticket", "steps", "false_success"}
        self.assertEqual(set(d["cells"]["postbox"]), metrics | {"calls", "messages", "reports"})
        for m in metrics:
            self.assertEqual(len(d["cells"]["postbox"][m]["per_seed"]), 2)
        self.assertEqual(len(d["comparisons"]), 2 * len(metrics))
        for c in d["comparisons"]:
            self.assertEqual({"p", "metric", "favourable", "postbox", "control", "diff", "ci_low",
                              "ci_high", "verdict"}, set(c))
            self.assertLessEqual(c["ci_low"], c["ci_high"])
        self.assertTrue(d["passed"])

    def test_the_same_seed_gives_the_same_workload_and_counts(self) -> None:
        path = Path(self.tmp.name) / "second.json"
        rc, out, err = run(*SMALL, "--json", str(path))
        self.assertEqual(rc, 0, out + err)
        second = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(second["schedule_hashes"], self.first["schedule_hashes"])
        self.assertEqual(second["read_hashes"], self.first["read_hashes"])
        self.assertEqual(second["cells"], self.first["cells"])          # outcomes are not timing
        # a different seed is a different workload (control)
        rc, out, err = run("--seed", "9", "--seeds", "1", "--p", "1.0", "--horizon", "20",
                           "--json", str(path))
        self.assertEqual(rc, 0, out + err)
        other = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn(other["schedule_hashes"]["9"], self.first["schedule_hashes"].values())

    def test_the_arms_get_the_same_workload_and_the_artifacts_say_so(self) -> None:
        ts = load()
        params, _ = ts.parse_args(["--seed", "5", "--seeds", "1", "--p", "0.5", "--horizon", "20"])
        sched = ts.build_schedule(5, params)
        self.assertEqual(ts.digest(sched), ts.digest(ts.build_schedule(5, params)))
        self.assertNotEqual(ts.digest(sched), ts.digest(ts.build_schedule(6, params)))
        with tempfile.TemporaryDirectory(prefix="team-sim-test-") as td:
            t0 = float(int(time.time()) - 7200)
            a = ts.run_arm(5, "postbox", 1.0, params, sched, t0, Path(td), time.monotonic() + 600)
            b = ts.run_arm(5, "control", 0.5, params, sched, t0, Path(td), time.monotonic() + 600)
            # hashed from the schedule file each run left behind, not from the engine's object
            self.assertEqual(a["schedule_hash"], b["schedule_hash"])
            self.assertEqual(a["schedule_hash"], ts.digest(sched))
            # and the artifacts differ the way the arms do
            self.assertTrue(any(Path(td, "postbox", "mailbox").glob("*.md")))
            self.assertTrue(Path(td, "control-0.5", "NOTES.md").is_file())
            self.assertFalse(Path(td, "control-0.5", "mailbox").exists())
            self.assertGreater(a["mailbox_claims"], 0)
            self.assertEqual(b["claim_calls"], 0)
            # the outcomes are a function of the artifacts alone: measuring again agrees
            again = ts.measure(Path(td, "postbox"))
            self.assertEqual(again, {k: a[k] for k in again})

    def test_the_read_table_is_coupled_across_p(self) -> None:
        ts = load()
        agents = [f"agent-{i:02d}" for i in range(4)]
        lo = ts.read_table(1, 0.25, agents, 30)
        hi = ts.read_table(1, 1.0, agents, 30)
        self.assertTrue(all(hi[s][a] for s in hi for a in agents))
        self.assertTrue(all(hi[s][a] for s in lo for a in agents if lo[s][a]))
        self.assertTrue(any(not lo[s][a] for s in lo for a in agents))

    def test_the_replay_counts_unordered_edits_and_not_ordered_ones(self) -> None:
        ts = load()

        def commit(seq: int, agent: str, ticket: str, lines: list[int], base: int) -> dict[str, Any]:
            return {"seq": seq, "op": "commit", "file": "f", "agent": agent, "ticket": ticket,
                    "lines": lines, "base_rev": base, "value": 7, "step": seq}
        a = {"id": "A", "file": "f", "lines": [1, 10], "value": 7}
        b = {"id": "B", "file": "f", "lines": [6, 15], "value": 7}
        ordered = ts.replay([commit(1, "x", "A", [1, 10], 0), commit(2, "y", "B", [6, 15], 1)])
        self.assertEqual(ordered.conflicts, 0)
        self.assertTrue(ordered.passes(a) and ordered.passes(b))
        unordered = ts.replay([commit(1, "x", "A", [1, 10], 0), commit(2, "y", "B", [6, 15], 0)])
        self.assertEqual(unordered.conflicts, 1)
        self.assertFalse(unordered.passes(a))          # A's lines 6-10 were overwritten
        self.assertTrue(unordered.passes(b))
        apart = ts.replay([commit(1, "x", "A", [1, 10], 0), commit(2, "y", "B", [11, 20], 0)])
        self.assertEqual(apart.conflicts, 0)           # concurrent, but on different lines

    def test_a_planted_divergence_between_the_arms_fails_the_run(self) -> None:
        path = Path(self.tmp.name) / "fail.json"
        rc, out, err = run("--seed", "3", "--seeds", "1", "--p", "1.0", "--horizon", "20",
                           "--self-test-fail", "--json", str(path))
        self.assertEqual(rc, 1, out + err)
        self.assertRegex(out, r"(?m)^FAIL\s+schedule-hash")
        self.assertIn("NOT PASSED", err)
        self.assertFalse(json.loads(path.read_text(encoding="utf-8"))["passed"])

    def test_a_usage_error_exits_2(self) -> None:
        for bad in (["--p", "1.5"], ["--p", "x"], ["--seeds", "0"], ["--agents", "2"],
                    ["--false-success", "2"], ["--no-such-flag"]):
            rc, out, err = run(*bad)
            self.assertEqual(rc, 2, (bad, out, err))
        rc, _out, err = run("--seeds", "0")
        self.assertIn("team_sim:", err)

    def test_an_exception_inside_the_harness_never_exits_0(self) -> None:
        ts = load()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(ts, "build_schedule", side_effect=RuntimeError("planted harness failure")):
            with redirect_stdout(out), redirect_stderr(err):
                rc = ts.main(["--seeds", "1", "--p", "1.0"])
        self.assertEqual(rc, 1)
        self.assertIn("planted harness failure", err.getvalue())
        self.assertIn("the harness itself failed", err.getvalue())

    def test_the_harness_cannot_be_rigged_to_favour_postbox(self) -> None:
        # At p = 1.0 the control reads the notes at every step. Where the arms tie on outcome and
        # the control had no conflict, what is left is the cost of the channel, which the
        # mechanism cannot reduce (a mailbox costs a claim, a release and a pickup per
        # ticket; a notes line costs one append): the control must not be dearer there.
        path = Path(self.tmp.name) / "pin.json"
        rc, out, err = run("--seed", "1", "--seeds", "8", "--p", "1.0", "--horizon", "20", "--json", str(path))
        self.assertEqual(rc, 0, out + err)
        d = json.loads(path.read_text(encoding="utf-8"))
        pb, ct = d["cells"]["postbox"], d["cells"]["control@1.0"]
        ties = [i for i in range(8)
                if pb["completion"]["per_seed"][i] == ct["completion"]["per_seed"][i]
                and ct["conflicts"]["per_seed"][i] == 0]
        self.assertTrue(ties, "no seed in which the arms tie on outcome: the pin has nothing to compare")
        for i in ties:        # same outcome, no conflict: the channel's own cost is all that differs
            self.assertLessEqual(ct["calls"]["per_seed"][i], pb["calls"]["per_seed"][i], i)
            self.assertLessEqual(ct["messages"]["per_seed"][i], pb["messages"]["per_seed"][i], i)
        at1 = {c["metric"]: c for c in d["comparisons"]}
        # facts published at step s reach a consumer at s+1 in both arms when p = 1.0
        self.assertLessEqual(at1["missed_at_commit"]["control"], at1["missed_at_commit"]["postbox"])
        # and the verdict logic reports the control as better when it is
        ts = load()
        self.assertEqual(ts.verdict(0.2, 0.9, -1), "control better")
        self.assertEqual(ts.verdict(-0.9, -0.2, -1), "postbox better")
        self.assertEqual(ts.verdict(-0.1, 0.4, -1), "no detectable difference")
        self.assertEqual(ts.verdict(0.2, 0.9, +1), "postbox better")

    def test_the_bootstrap_interval_is_seeded_and_brackets_the_mean(self) -> None:
        ts = load()
        diffs = [-2.0, -1.0, 0.0, -3.0, -1.0, -2.0]
        lo, hi = ts.bootstrap_ci(diffs)
        self.assertEqual((lo, hi), ts.bootstrap_ci(diffs))
        self.assertLess(lo, sum(diffs) / len(diffs))
        self.assertGreater(hi, sum(diffs) / len(diffs))
        self.assertEqual(ts.bootstrap_ci([1.5]), (1.5, 1.5))


if __name__ == "__main__":
    unittest.main()
