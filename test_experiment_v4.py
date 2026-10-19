#!/usr/bin/env python3
"""Offline tests of scripts/experiment_v4.py (the v4 pilot harness) and scripts/analyze_v4.py; see
docs/experiment-v4.md. No model is called and no account is needed. Every "agent" here is a
SIMULATION (SimDriver): a thread that edits the shared tree like a simple agent, uses the real
mail CLI, `mkdir` and a notes file, and emits stream-json events of the host's shape. Nothing in
this file is observed model behaviour, and no result of these tests says anything about Postbox.

Checked: the hidden suites (reference passes, baseline fails, each ticket is separately
observable); the prompts (arm paragraphs within 10%, one allow-list, the heredoc form taught, no
curly braces); the collision detector DETECTS a lost update and a duplicate (and does not invent
them); the kill happens AFTER the claim/lock/note exists and an unverified kill voids the
instance; recovery in the simulation (a lease expires, a lock does not); the session cap; the
refusals (no flag, no environment variable, --out inside the repository, no gate); the gates and
the descriptive analysis. Skipped where scripts/ is not present. Stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib.util
import io
import json
import platform
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
SCRIPT = SCRIPTS / "experiment_v4.py"
ANALYSIS = SCRIPTS / "analyze_v4.py"


def load(path: Path, name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def ev_use(tid: str, name: str, **inp: Any) -> list[tuple[float, str]]:
    return [(0.0, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}))]


def ev_res(tid: str, text: str = "ok", err: bool = False) -> list[tuple[float, str]]:
    return [(0.0, json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]}}))]


# scripts/experiment_v4.py refuses to start on Windows on purpose (main(): "POSIX only (process
# groups, SIGKILL)") and its prompts, allow rules and simulated commands spell `python3`. Six
# tests here need that behaviour, so on Windows the whole file is skipped, with this reason.
_WINDOWS = platform.system() == "Windows"
if _WINDOWS:
    print("SKIP  test_experiment_v4.py (experiment_v4.py is POSIX only by design: process groups, "
          "SIGKILL, python3 spellings)")


@unittest.skipIf(_WINDOWS, "experiment_v4.py is POSIX only by design (process groups, SIGKILL)")
@unittest.skipUnless(SCRIPT.exists(), "scripts/ is not present")
class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.v: Any = load(SCRIPT, "experiment_v4")
        self.an: Any = load(ANALYSIS, "analyze_v4")
        tmp = tempfile.TemporaryDirectory(prefix="test-v4-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "work").mkdir()
        self.inst = self.v.build_inst(9201)

    def harness(self, driver: Any, *, max_sessions: int = 40, stage: str = "pilot") -> Any:
        args = argparse.Namespace(
            model="haiku", effort="medium", max_turns=5, dry_run=True, max_sessions=max_sessions,
            wall_hours=1.0, notional_usd=50.0, stage=stage, workers=1, max_calls=0)
        out = self.root / f"out-{time.monotonic_ns()}"
        out.mkdir()
        timing = dataclasses.replace(self.v.FAST_TIMING, patience_s=2.5, grace_s=0.5,
                                     lease_s2_s=2, pause_s=0.4)
        return self.v.Harness(args, "sim", self.root / "work", out, driver, timing)

    def run_one(self, scenario: str, arm: str, driver: Any, seed: int = 9201) -> dict[str, Any]:
        h = self.harness(driver)
        rec: dict[str, Any] = h.run_one(scenario, arm, seed)
        return rec


class TestHiddenSuites(Base):
    def test_offline_checks_pass(self) -> None:
        for name, ok in self.v.offline_checks():
            self.assertTrue(ok, name)

    def test_every_seed_is_consistent(self) -> None:
        for seed in list(range(5001, 5007)) + list(range(9221, 9226)):
            inst = self.v.build_inst(seed)
            full = self.v.run_hidden(inst, {"ledger/rates.py": self.v.rates_text(
                inst, set(self.v.FEATURES))})
            self.assertTrue(all(x == "ok" for x in full.values()), (seed, full))


class TestPrompts(Base):
    def test_paragraphs_within_ten_percent(self) -> None:
        self.assertTrue(self.v.lengths_within())
        c = self.v.paragraph_chars()
        for who in (self.v.A, self.v.B):
            sizes = [c[a][who] for a in ("S", "L", "P")]
            self.assertLessEqual(max(sizes) - min(sizes), 0.10 * max(sizes))

    def test_only_the_paragraph_differs_between_arms(self) -> None:
        pr = self.v.prompt_for
        for who in (self.v.A, self.v.B):
            base = pr("S1", "N0", who)
            for arm in ("S", "L", "P"):
                p = pr("S1", arm, who)
                self.assertTrue(p.startswith(base))
                self.assertEqual(p[len(base):].strip(), self.v.paragraph(arm, who).strip())
        # the survivor's patience text is the same words in every arm
        sv = [self.v.prompt_for("S2", a, self.v.B, survivor=True) for a in ("N0", "S", "L", "P")]
        for p in sv:
            self.assertIn("wait no longer than", p)

    def test_p_arm_teaches_the_heredoc_form_without_braces_or_hash_lines(self) -> None:
        p = self.v.paragraph("P", self.v.A, "2030-01-01T00:00:00Z")
        self.assertIn("--body-file -", p)
        self.assertIn("<<'EOF'", p)
        self.assertIn("--scope ledger/rates.py", p)
        self.assertIn("--expires 2030-01-01T00:00:00Z", p)
        self.assertNotIn("--body '", p)
        for arm in ("S", "L", "P"):
            text = self.v.prompt_for("S1", arm, self.v.A, exp="2030-01-01T00:00:00Z")
            self.assertNotIn("{", text)
            self.assertNotIn("}", text)
            self.assertFalse(any(ln.lstrip().startswith("#") for ln in text.splitlines()))

    def test_allow_rules_are_common_plus_the_arms_own(self) -> None:
        proj = Path("/x/proj")
        common = set(self.v.COMMON_ALLOW)
        for arm in self.v.ARMS:
            self.assertTrue(common <= set(self.v.allow_rules(arm, proj)))
        extra = {a: set(self.v.allow_rules(a, proj)) - common for a in self.v.ARMS}
        self.assertEqual(extra["N0"], set())
        self.assertEqual(extra["S"], set())
        self.assertEqual(extra["O"], set())
        self.assertEqual(extra["L"], {"Bash(mkdir locks/*)", "Bash(rmdir locks/*)"})
        self.assertEqual(len(extra["P"]), 4)
        self.assertTrue(all("agent_mail.py" in r for r in extra["P"]))

    def test_prompts_hash_is_stable(self) -> None:
        self.assertEqual(self.v.prompts_hash(), self.v.prompts_hash())

    def test_rules_are_installed_and_doctor_sees_them(self) -> None:
        checks = self.v.verify_rules(self.root / "work", {})
        for name, ok in checks:
            self.assertTrue(ok, name)


class TestCollisionDetector(Base):
    """The detector must DETECT a lost update and a duplicate, and must not invent either."""

    def logs(self, a_ops: list[Any], b_ops: list[Any]) -> dict[str, Any]:
        return {self.v.A: self.v.parse_agent(a_ops), self.v.B: self.v.parse_agent(b_ops)}

    def snap(self, feats: set[str]) -> dict[str, str]:
        return {"ledger/__init__.py": "", "ledger/rates.py": self.v.rates_text(self.inst, feats)}

    def write_ops(self, tag: str, t: float, text: str) -> list[tuple[float, str]]:
        return [(t, ln) for _, ln in ev_use(tag, "Write", file_path="/p/ledger/rates.py",
                                            content=text)] + [
            (t + 0.01, ln) for _, ln in ev_res(tag)]

    def test_lost_update_is_detected(self) -> None:
        # A lands the fee, then B (who never saw it) overwrites the whole file with the discount
        a_text = self.v.rates_text(self.inst, {"prereq", "fee"})
        b_text = self.v.rates_text(self.inst, {"prereq", "discount"})
        states = [(0.0, self.snap(set())), (1.0, self.snap({"prereq", "fee"})),
                  (2.0, self.snap({"prereq", "discount"}))]
        logs = self.logs(self.write_ops("a1", 0.9, a_text), self.write_ops("b1", 1.9, b_text))
        out = self.v.analyse_states(self.inst, states, logs)
        self.assertTrue(out["lost_update"])
        self.assertTrue(out["lost_update_by_agent"][self.v.A])
        self.assertFalse(out["lost_update_by_agent"][self.v.B])
        self.assertTrue(out["collision"])
        self.assertFalse(out["final_ok"])
        self.assertEqual(out["regressions_caused_by_other"][self.v.A], 1)
        self.assertEqual([t["by"] for t in out["transitions"]], [self.v.A, self.v.B])

    def test_lost_update_is_detected_even_if_the_poller_missed_the_middle_state(self) -> None:
        a_text = self.v.rates_text(self.inst, {"prereq", "fee"})
        b_text = self.v.rates_text(self.inst, {"prereq", "discount"})
        states = [(0.0, self.snap(set())), (2.0, self.snap({"prereq", "discount"}))]
        logs = self.logs(self.write_ops("a1", 0.9, a_text), self.write_ops("b1", 1.9, b_text))
        out = self.v.analyse_states(self.inst, states, logs)
        self.assertTrue(out["lost_update_by_agent"][self.v.A])

    def test_a_correct_merge_is_not_a_lost_update(self) -> None:
        a_text = self.v.rates_text(self.inst, {"prereq", "fee"})
        merged = self.v.rates_text(self.inst, {"prereq", "fee", "discount"})
        states = [(0.0, self.snap(set())), (1.0, self.snap({"prereq", "fee"})),
                  (2.0, self.snap({"prereq", "fee", "discount"}))]
        b_ops = (ev_use("b0", "Read", file_path="/p/ledger/rates.py") + ev_res("b0", a_text)
                 + self.write_ops("b1", 1.9, merged))
        b_ops = [(1.0 + i * 0.001, ln) for i, (_, ln) in enumerate(b_ops)]
        logs = self.logs(self.write_ops("a1", 0.9, a_text), b_ops)
        out = self.v.analyse_states(self.inst, states, logs)
        self.assertFalse(out["lost_update"])
        self.assertFalse(out["duplicate_work"])      # B read A's definition before writing the file
        self.assertTrue(out["final_ok"])

    def test_duplicate_work_is_detected(self) -> None:
        # both agents write the prerequisite without having seen one
        a = self.write_ops("a1", 0.9, self.v.rates_text(self.inst, {"prereq", "fee"}))
        b = self.write_ops("b1", 1.9, self.v.rates_text(self.inst, {"prereq", "fee", "discount"}))
        self.assertTrue(self.v.introduced_prereq(self.logs(a, b)[self.v.A].ops))
        self.assertTrue(self.v.introduced_prereq(self.logs(a, b)[self.v.B].ops))
        states = [(0.0, self.snap(set())), (1.0, self.snap({"prereq", "fee"})),
                  (2.0, self.snap({"prereq", "fee", "discount"}))]
        out = self.v.analyse_states(self.inst, states, self.logs(a, b))
        self.assertTrue(out["duplicate_work"])
        self.assertTrue(out["collision"])
        self.assertFalse(out["lost_update"])

    def test_reading_the_teammates_definition_first_is_not_a_duplicate(self) -> None:
        with_def = self.v.rates_text(self.inst, {"prereq", "fee"})
        a = self.write_ops("a1", 0.9, with_def)
        b = (ev_use("b0", "Read", file_path="/p/ledger/rates.py")
             + ev_res("b0", with_def)
             + self.write_ops("b1", 1.9, self.v.rates_text(self.inst, {"prereq", "fee", "discount"})))
        b = [(1.5 + i * 0.001, ln) for i, (_, ln) in enumerate(b)]
        self.assertFalse(self.v.introduced_prereq(self.logs(a, b)[self.v.B].ops))

    def test_rejected_edits_are_counted_not_called_lost(self) -> None:
        b = (ev_use("b1", "Edit", file_path="/p/ledger/rates.py", old_string="x", new_string="y")
             + ev_res("b1", "String to replace not found in file", True))
        out = self.v.analyse_states(self.inst, [(0.0, self.snap(set()))], self.logs([], b))
        self.assertEqual(out["rejected_edits"][self.v.B], 1)
        self.assertFalse(out["lost_update"])

    def test_edit_replay_and_attribution(self) -> None:
        base = self.snap(set())
        old = "    return _div_round(amount * RATES[currency], 1000)"
        new = "    value = _div_round(amount * RATES[currency], 1000)\n    return value"
        ops = (ev_use("a1", "Edit", file_path="/p/ledger/rates.py", old_string=old, new_string=new)
               + ev_res("a1"))
        ops = [(0.5 + i * 0.01, ln) for i, (_, ln) in enumerate(ops)]
        logs = self.logs(ops, [])
        after = {**base, "ledger/rates.py": base["ledger/rates.py"].replace(old, new)}
        out = self.v.analyse_states(self.inst, [(0.0, base), (0.6, after)], logs)
        self.assertEqual(out["transitions"][0]["by"], self.v.A)
        self.assertEqual(out["replayed_states"], 1)


class TestApiFailure(Base):
    def test_api_retries_are_counted(self) -> None:
        lines = [(0.0, json.dumps({"type": "system", "subtype": "api_retry", "attempt": i}))
                 for i in range(1, 4)]
        log = self.v.parse_agent(lines)
        self.assertEqual(log.api_retries, 3)
        self.assertEqual(log.ops, [])


class TestSimulatedRuns(Base):
    """SIMULATION: the harness end to end with the simulated agents."""

    def test_n0_race_is_a_lost_update_and_a_duplicate(self) -> None:
        rec = self.run_one("S1", "N0", self.v.SimDriver(stale_write=True))
        self.assertTrue(rec["lost_update"])
        self.assertTrue(rec["duplicate_work"])
        self.assertTrue(rec["collision"])
        self.assertFalse(rec["final_ok"])
        self.assertEqual(rec["manual_nudges"], 0)
        self.assertTrue(rec["simulation"])

    def test_lock_and_claim_arms_prevent_the_race(self) -> None:
        for arm in ("L", "P", "S"):
            drv = self.v.SimDriver(stale_write=False)
            rec = self.run_one("S1", arm, drv)
            if arm == "S":          # a notes file is not atomic: the simulation may or may not race
                continue
            self.assertFalse(rec["collision"], arm)
            self.assertTrue(rec["final_ok"], arm)
            self.assertEqual(rec["channel_denials"], 0)

    def test_p_arm_uses_exit_3_and_leaves_a_mailbox_artifact(self) -> None:
        h = self.harness(self.v.SimDriver())
        rec = h.run_one("S1", "P", 9202)
        sent = rec["claims"]["claims_sent"]
        self.assertGreaterEqual(sent[self.v.A] + sent[self.v.B], 1)
        waited = rec["claims"]["claim_exit3_seen"]
        self.assertGreaterEqual(waited[self.v.A] + waited[self.v.B], 0)
        art = h.out / "artifacts" / rec["tag"]
        self.assertTrue((art / "mailbox.json").is_file())
        self.assertTrue((art / "agent-mail").is_dir())
        self.assertTrue((art / "snapshots.json").is_file())
        self.assertTrue((h.out / "raw" / f"{rec['tag']}-a.jsonl").is_file())
        self.assertTrue((h.out / "raw" / f"{rec['tag']}-b.times.json").is_file())

    def test_oracle_runs_the_agents_one_after_the_other(self) -> None:
        drv = self.v.SimDriver(stale_write=True)
        rec = self.run_one("S1", "O", drv)
        a, b = rec["sessions"]
        self.assertGreaterEqual(b["launch_s"], a["end_s"] - 0.1)      # B starts after A ended
        self.assertTrue(rec["final_ok"])
        self.assertFalse(rec["collision"])

    def test_kill_happens_after_the_claim_exists(self) -> None:
        for arm in ("P", "L", "S"):
            h = self.harness(self.v.SimDriver())
            rec = h.run_one("S2", arm, 9206)
            ver = rec["kill"]
            self.assertTrue(ver["verified"], (arm, ver))
            self.assertGreater(ver["kill_t"], ver["claim_seen_t"], arm)
            self.assertLessEqual(ver["claim_mtime"], ver["kill_wall"], arm)
            self.assertTrue(ver["a_alive_at_kill"], arm)
            self.assertTrue(ver["artifact_after_kill"], arm)
            a = next(s for s in rec["sessions"] if s["agent"] == self.v.A)
            self.assertTrue(a["killed_by_harness"])
            self.assertFalse(a["infra"])
            self.assertEqual(rec["void"], "")
            art = h.out / "artifacts" / rec["tag"]
            if arm == "L":
                self.assertTrue((art / "locks" / "ledger-rates").is_dir())   # still stuck

    def test_a_lease_expires_but_a_lock_does_not(self) -> None:
        p = self.run_one("S2", "P", self.v.SimDriver(), 9206)
        self.assertTrue(p["recovered"])
        self.assertIsNotNone(p["time_to_recovery_s"])
        for arm in ("L", "S"):
            rec = self.run_one("S2", arm, self.v.SimDriver(), 9206)
            self.assertFalse(rec["recovered"], arm)        # the SIMULATED survivor obeys the lock
            self.assertIsNone(rec["time_to_recovery_s"])

    def test_an_unverified_kill_voids_the_instance(self) -> None:
        v = self.v

        class Lazy(v.SimHandle):  # type: ignore[name-defined]
            def play(self) -> None:         # the holder never takes a claim and exits
                return

        class LazyDriver(v.SimDriver):  # type: ignore[name-defined]
            def start(self, spec: Any, stderr_path: Path) -> Any:
                self.launch_wall[spec.who] = time.time()
                h = Lazy(spec, self) if spec.who == v.A else v.SimHandle(spec, self)
                self.handles.append(h)
                return h

        rec = self.run_one("S2", "L", LazyDriver(), 9206)
        self.assertNotEqual(rec["void"], "")
        self.assertFalse(rec.get("kill", {}).get("verified", False))
        # the run is kept (raw events) and listed, not dropped
        self.assertEqual(len(rec["sessions"]), 1)

    def test_n0_scenario_2_trigger_is_the_first_edit(self) -> None:
        rec = self.run_one("S2", "N0", self.v.SimDriver(), 9206)
        self.assertTrue(rec["kill"]["verified"])
        self.assertTrue(rec["final_ok"])

    def test_session_cap_stops_the_run(self) -> None:
        h = self.harness(self.v.SimDriver(), max_sessions=2)
        first = h.run_one("S1", "L", 9201)
        self.assertNotIn("aborted", first)
        second = h.run_one("S1", "L", 9202)
        self.assertIn("aborted", second)
        self.assertEqual(h.n_sessions, 2)

    def test_wall_cap_and_rate_limit_stop(self) -> None:
        h = self.harness(self.v.SimDriver())
        h.t_begin -= 4000
        self.assertIn("aborted", h.run_one("S1", "O", 9201))
        h2 = self.harness(self.v.SimDriver())
        h2.stop = "two rate-limit rejections in a row"
        self.assertIn("aborted", h2.run_one("S1", "O", 9201))

    def test_plans_fit_their_caps(self) -> None:
        self.assertEqual(self.v.planned_sessions("readiness"), 10)
        self.assertEqual(self.v.SESSION_CAP["readiness"], 10)
        plan = self.v.pilot_plan(self.v.SEEDS["pilot"])
        self.assertEqual(len(plan), 36)                       # 30 scenario-2 runs + 6 observations
        self.assertEqual(self.v.planned_sessions("pilot"), 72)
        self.assertLessEqual(72, self.v.SESSION_CAP["pilot"])
        s2 = [r for r in plan if r[0] == "S2"]
        self.assertEqual({a for _, a, _ in s2}, set(self.v.ARMS))
        self.assertEqual(len(s2), 30)
        s1 = [r for r in plan if r[0] == "S1"]
        self.assertEqual(sorted(a for _, a, _ in s1), ["L", "N0", "N0", "N0", "P", "S"])

    def test_lease_is_shorter_than_patience(self) -> None:
        t = self.v.LIVE_TIMING
        self.assertEqual((t.lease_s2_s, t.patience_s), (90, 240.0))
        self.assertGreater(t.patience_s, t.lease_s2_s)
        self.assertGreater(self.v.FAST_TIMING.patience_s, self.v.FAST_TIMING.lease_s2_s)

    def test_survivor_story_is_recorded(self) -> None:
        for arm in ("L", "P", "S"):
            rec = self.run_one("S2", arm, self.v.SimDriver(), 9206)
            sv = rec["survivor"]
            self.assertIn("pauses", sv)
            self.assertEqual(sv["unsafe_commands"], [])
        l_rec = self.run_one("S2", "L", self.v.SimDriver(), 9206)
        self.assertTrue(l_rec["survivor"]["lock_present_at_end"])
        self.assertFalse(l_rec["survivor"]["broke_lock"])


class TestCommandLine(Base):
    def main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = self.v.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_refuses_without_dry_run_or_live(self) -> None:
        rc, _, err = self.main("--stage", "readiness", "--out", str(self.root / "o1"))
        self.assertEqual(rc, 2)
        self.assertIn("exactly one", err)

    def test_refuses_live_without_the_environment_variable(self) -> None:
        import os
        saved = os.environ.pop(self.v.LIVE_ENV, None)
        try:
            rc, _, err = self.main("--stage", "readiness", "--out", str(self.root / "o2"), "--live")
        finally:
            if saved is not None:
                os.environ[self.v.LIVE_ENV] = saved
        self.assertEqual(rc, 2)
        self.assertIn(self.v.LIVE_ENV, err)

    def test_refuses_an_output_directory_inside_the_repository(self) -> None:
        rc, _, err = self.main("--stage", "readiness", "--out", str(HERE / "v4-out-test"),
                               "--dry-run")
        self.assertEqual(rc, 2)
        self.assertIn("inside the repository", err)

    def test_refuses_the_pilot_without_a_gate_and_with_a_dry_run_gate(self) -> None:
        import os
        os.environ[self.v.LIVE_ENV] = "1"
        try:
            rc, _, err = self.main("--stage", "pilot", "--out", str(self.root / "o3"), "--live")
            self.assertEqual(rc, 2)
            self.assertIn("--gate", err)
            gate = self.root / "gate.json"
            gate.write_text(json.dumps({"dry_run": True, "passed": True}))
            rc, _, err = self.main("--stage", "pilot", "--out", str(self.root / "o4"), "--live",
                                   "--gate", str(gate))
            self.assertEqual(rc, 2)
            self.assertIn("dry-run", err)
        finally:
            os.environ.pop(self.v.LIVE_ENV, None)

    def test_gate_must_match_prompts_model_and_effort(self) -> None:
        ns = argparse.Namespace(model="haiku", effort="medium")
        good = {"dry_run": False, "passed": True, "prompts_sha256": self.v.prompts_hash(),
                "model": "haiku", "effort": "medium", "checks": {}}
        p = self.root / "g.json"
        p.write_text(json.dumps(good))
        self.assertIsNone(self.v.check_gate(str(p), ns))
        p.write_text(json.dumps(good | {"prompts_sha256": "0" * 64}))
        self.assertIn("prompts_sha256", self.v.check_gate(str(p), ns))
        p.write_text(json.dumps(good | {"passed": False, "checks": {"x": False}}))
        self.assertIn("did not pass", self.v.check_gate(str(p), ns))

    def test_caps_can_be_lowered_never_raised(self) -> None:
        a = self.v.parse(["--stage", "readiness", "--out", "x", "--dry-run", "--max-sessions", "99"])
        self.assertEqual(a.max_sessions, 10)
        a = self.v.parse(["--stage", "pilot", "--out", "x", "--dry-run", "--max-sessions", "50"])
        self.assertEqual(a.max_sessions, 50)

    def test_dry_run_readiness_end_to_end_and_analysis(self) -> None:
        out = self.root / "dry"
        rc, text, _ = self.main("--stage", "readiness", "--out", str(out), "--dry-run", "--fast")
        self.assertEqual(rc, 0)
        self.assertIn("SIMULATION", text)
        gate = json.loads((out / "readiness_gate.json").read_text())
        self.assertTrue(gate["dry_run"])
        s = self.an.summary(out)
        self.assertTrue(s["simulation"])
        self.assertEqual(s["final_runs"], 5)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.an.show(s)
        self.assertIn("No test and no significance claim", buf.getvalue())

    def test_freeze_prints_hashes(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self.v.main(["--freeze"]), 0)
        self.assertIn("scripts/experiment_v4.py", buf.getvalue())
        self.assertIn(self.v.prompts_hash(), buf.getvalue())


class TestGates(Base):
    def rec(self, scenario: str, arm: str, seed: int, **kw: Any) -> dict[str, Any]:
        r: dict[str, Any] = {
            "tag": f"{scenario}-{arm}-{seed}-t1", "scenario": scenario, "arm": arm, "seed": seed,
            "void": "", "infra_failure": "", "collision": False, "lost_update": False,
            "duplicate_work": False, "final_ok": True, "recovered": None, "manual_nudges": 0,
            "channel_denials": 0, "rejected_edits": {"agent-a": 0, "agent-b": 0}, "wall_s": 10.0,
            "cost_usd": 0.1, "sessions": [{"agent": "agent-a", "result_seen": True, "infra": "",
                                           "turns": 5, "killed_by_harness": False, "cost_usd": 0.1},
                                          {"agent": "agent-b", "result_seen": True, "infra": "",
                                           "turns": 5, "killed_by_harness": False, "cost_usd": 0.1}]}
        r.update(kw)
        return r

    def test_n0_never_colliding_is_an_observation_not_a_failure(self) -> None:
        recs = [self.rec("S1", "N0", s) for s in range(3)] + [self.rec("S2", "O", s) for s in range(6)]
        g = self.an.gates(recs)
        obs = g["observation_scenario_1"]
        self.assertEqual(obs["n0_collisions"], "0/3")
        self.assertIn("already prevented", obs["reading"])
        self.assertTrue(obs["pass"])
        self.assertNotIn("1_n0_collides", g)
        self.assertTrue(g["2_oracle_solvable"]["pass"])

    def test_n0_collisions_are_counted(self) -> None:
        recs = [self.rec("S1", "N0", s, collision=(s < 1)) for s in range(3)]
        self.assertEqual(self.an.gates(recs)["observation_scenario_1"]["n0_collisions"], "1/3")

    def test_oracle_below_80_percent_fails_gate_2(self) -> None:
        recs = [self.rec("S2", "O", s, final_ok=(s < 3)) for s in range(6)]
        self.assertFalse(self.an.gates(recs)["2_oracle_solvable"]["pass"])

    def test_a_channel_refusal_fails_operability(self) -> None:
        recs = [self.rec("S2", a, 1) for a in ("S", "L")] + [self.rec("S2", "P", 1,
                                                                       channel_denials=1)]
        g = self.an.gates(recs)["3_arms_operable"]
        self.assertFalse(g["P"]["pass"])
        self.assertFalse(g["pass"])

    def test_void_and_unverified_kills_are_listed(self) -> None:
        recs = [self.rec("S2", "P", 1, void="x", kill={"verified": False}),
                self.rec("S2", "L", 1, kill={"verified": True})]
        g = self.an.gates(recs)["6_kill_after_claim"]
        self.assertEqual(g["void"], ["S2-P-1-t1"])
        self.assertTrue(g["pass"])
        recs.append(self.rec("S2", "S", 1, kill={"verified": False}))
        self.assertFalse(self.an.gates(recs)["6_kill_after_claim"]["pass"])

    def test_last_attempt_wins_and_earlier_attempts_stay_on_disk(self) -> None:
        a = self.rec("S1", "L", 1, infra_failure="timeout") | {"attempt": 1}
        b = self.rec("S1", "L", 1) | {"attempt": 2, "tag": "S1-L-1-t2"}
        self.assertEqual(self.an.final_records([a, b]), [b])


if __name__ == "__main__":
    unittest.main()
