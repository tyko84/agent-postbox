#!/usr/bin/env python3
"""Offline tests of scripts/experiment_v2.py (the v2 harness) and scripts/analyze_v2.py (its
analysis); see docs/experiment-v2.md. No model is called and no account is needed: sessions
are played by a fake driver that edits files, uses the real mail tool and the real hook, and
emits stream-json events of the host's shape.

Checked here: the instance (seeded D and token, reference solutions pass both hidden suites,
wrong or missing solutions fail them, per-fact flags); the arm projects (the mailbox tool and
mailbox omitted and asserted for non-P arms, no leak into B's checkout, shared channel); the
prompts (P and N2 within 10%, recorded character counts, one allow-list); the run pipeline
for every arm (record fields, scoring, S4, pickup and delivery from events and the hook);
retries, rate-limit stop, the spend guard with workers; the smoke gate that the main stage
requires; the refusals (no live flag, no environment variable, no gate); and the analysis
(exact McNemar, Newcombe interval, Wilson, verdict rows, gates, freeze hash).
Skipped where scripts/ is not present (an unpacked sdist ships the tests, not the scripts).
Stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
SCRIPT = SCRIPTS / "experiment_v2.py"
ANALYSIS = SCRIPTS / "analyze_v2.py"


def load(path: Path, name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclasses look the defining module up
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(SCRIPT.exists(), "scripts/ is not present")
class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.v = load(SCRIPT, "experiment_v2")
        self.an = load(ANALYSIS, "analyze_v2")
        tmp = tempfile.TemporaryDirectory(prefix="test-v2-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "work").mkdir()

    def args(self, **kw: Any) -> argparse.Namespace:
        base = dict(model="haiku", effort="medium", max_turns=30, max_calls=10**6,
                    ceiling_usd=30.0, workers=3, stage="smoke")
        base.update(kw)
        return argparse.Namespace(**base)

    def harness(self, driver: Any = None, **kw: Any) -> Any:
        return self.v.Harness(self.args(**kw), "claude", self.root / "work", self.root / "out",
                              driver or self.v.FakeDriver(), sleep=lambda s: None)

    def main(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        old = dict(os.environ)
        os.environ.pop(self.v.LIVE_ENV, None)
        os.environ.update(env or {})
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = self.v.main(list(argv))
                except SystemExit as e:
                    rc = int(e.code or 0)
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, out.getvalue(), err.getvalue()


class Instance(Base):
    def assemble(self, inst: Any, parse: str | None, report: str | None) -> dict[str, Any]:
        a, b = self.root / "a", self.root / "b"
        a.mkdir(exist_ok=True)
        b.mkdir(exist_ok=True)
        if parse is not None:
            self.v.write_tree(a, {"ledger/parse.py": parse})
        if report is not None:
            self.v.write_tree(b, {"ledger/report.py": report})
        return self.v.score(inst, a, b)

    def test_seeded_and_deterministic(self) -> None:
        a, b = self.v.build_instance(3001), self.v.build_instance(3001)
        self.assertEqual((a.d, a.token, a.sample), (b.d, b.token, b.sample))
        ds = {self.v.build_instance(s).d for s in range(3001, 3061)}
        self.assertTrue(ds <= set(self.v.DS) and len(ds) > 3)
        self.assertNotIn(100, ds)
        self.assertNotIn(1000, ds)
        self.assertRegex(a.token, r"^VOID-[0-9a-f]{4}$")
        self.assertIn(f"units of 1/{a.d} of the currency", a.sample)
        self.assertIn(f"voided rows carry status {a.token}", a.sample)
        self.assertEqual(self.v.arm_order(3001), self.v.arm_order(3001))
        for seed in range(3001, 3030):
            self.assertEqual(sorted(self.v.arm_order(seed)), sorted(self.v.ARMS))

    def test_the_reference_solution_passes_both_hidden_suites(self) -> None:
        for seed in (9001, 9002, 9003, 3001, 3002, 3003, 3004, 3005):
            inst = self.v.build_instance(seed)
            r = self.assemble(inst, inst.ref_parse(), inst.ref_report())
            self.assertTrue(r["a_ok"] and r["b_ok"] and r["completed"], (seed, r["suites"]))
            self.assertFalse(r["b_wrong_default"] or r["b_missing"])

    def test_the_wrong_default_fails_the_hidden_suite(self) -> None:
        for seed in (9001, 3001, 3002, 3003):
            inst = self.v.build_instance(seed)
            r = self.assemble(inst, inst.ref_parse(), inst.wrong_report())
            self.assertTrue(r["a_ok"])
            self.assertFalse(r["b_ok"] or r["b_unit_ok"] or r["b_void_ok"])
            self.assertTrue(r["b_wrong_default"] and not r["b_missing"])

    def test_per_fact_flags(self) -> None:
        inst = self.v.build_instance(3001)
        d_only = inst.ref_report().replace("r.status != VOID]", "True]")      # right D, no voids
        void_only = inst.ref_report().replace(f"D = {inst.d}", "D = 100")      # voids, wrong D
        r = self.assemble(inst, inst.ref_parse(), d_only)
        self.assertEqual((r["b_unit_ok"], r["b_void_ok"], r["b_ok"]), (True, False, False))
        r = self.assemble(inst, inst.ref_parse(), void_only)
        self.assertEqual((r["b_unit_ok"], r["b_void_ok"], r["b_ok"]), (False, True, False))

    def test_missing_and_broken_solutions(self) -> None:
        inst = self.v.build_instance(3001)
        r = self.assemble(inst, None, None)             # both still stubs
        self.assertFalse(r["a_ok"] or r["b_ok"])
        self.assertTrue(r["b_missing"] and not r["b_wrong_default"])
        r = self.assemble(inst, inst.ref_parse(), "def broken(:\n")
        self.assertTrue(r["b_missing"])
        r = self.assemble(inst, "def parse_export(p):\n    return []\n", inst.ref_report())
        self.assertFalse(r["a_ok"])
        self.assertTrue(r["b_ok"] and not r["completed"])

    def test_the_offline_smoke_checks_pass(self) -> None:
        checks = self.v.offline_checks()
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(ok for _, ok in checks), checks)

    def test_a_leak_into_b_is_detected(self) -> None:
        inst = self.v.build_instance(3001)
        b = self.root / "b"
        self.v.write_tree(b, inst.base_files())
        self.v.assert_no_leak(b, inst.token)
        self.v.write_tree(b, {"notes.md": f"the token is {inst.token}"})
        with self.assertRaises(RuntimeError):
            self.v.assert_no_leak(b, inst.token)
        (b / "notes.md").unlink()
        self.v.write_tree(b, {"vendor/sample_export.csv": inst.sample})
        with self.assertRaises(RuntimeError):
            self.v.assert_no_leak(b, inst.token)


class Arms(Base):
    def build(self, arm: str) -> tuple[Path, Path, Path, dict[str, str], Any]:
        inst = self.v.build_instance(9001)
        root = self.root / f"arm-{arm}"
        root.mkdir()
        a, b, shared, extra = self.harness().build(inst, arm, root)
        return a, b, shared, extra, inst

    def test_non_p_arms_omit_the_mailbox_and_it_is_asserted(self) -> None:
        for arm in ("N2", "N1", "N0", "O"):
            a, b, shared, extra, inst = self.build(arm)
            for tree in (a, b):
                self.assertFalse((tree / "tools" / "agent-postbox").exists(), arm)
                self.assertFalse((tree / "docs" / "agent-mail").exists(), arm)
                self.assertFalse((tree / ".claude").exists(), arm)
                self.v.assert_mailbox_omitted(tree)
            self.assertEqual(extra, {})
            self.assertTrue((a / "vendor" / "sample_export.csv").is_file())
            self.assertFalse((b / "vendor").exists())
        planted = self.root / "arm-N0" / "a"
        (planted / "tools" / "agent-postbox").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            self.v.assert_mailbox_omitted(planted)

    def test_the_p_arm_has_the_real_adapter_and_a_shared_mailbox(self) -> None:
        a, b, shared, extra, inst = self.build("P")
        for tree in (a, b):
            self.assertTrue((tree / "tools" / "agent-postbox" / "agent_mail.py").is_file())
            self.assertIn("agent_mail_check.py", (tree / ".claude" / "settings.json").read_text())
            self.assertFalse((tree / "docs" / "agent-mail").exists())
        self.assertTrue((shared / "agent-mail" / "PARTICIPANTS.md").is_file())
        self.assertEqual(extra, {"AGENT_MAIL_DIR": str(shared / "agent-mail")})
        self.assertFalse((b / "vendor").exists())

    def test_notes_arms_share_one_file(self) -> None:
        for arm in ("N2", "N1"):
            a, b, shared, _, _ = self.build(arm)
            self.assertIn("team notes: NOTES.md", (b / "README.md").read_text())
            (a / "NOTES.md").write_text((a / "NOTES.md").read_text() + "x\n")
            self.assertEqual((b / "NOTES.md").read_text(), (shared / "NOTES.md").read_text())
        a, b, shared, _, _ = self.build("N0")
        self.assertNotIn("NOTES.md", (b / "README.md").read_text())
        self.assertFalse((shared / "NOTES.md").exists())

    def test_two_checkouts_are_separate_trees(self) -> None:
        a, b, _, _, inst = self.build("N0")
        self.assertNotEqual(a, b)
        (a / "ledger" / "parse.py").write_text("changed\n")
        self.assertNotEqual((a / "ledger" / "parse.py").read_text(),
                            (b / "ledger" / "parse.py").read_text())
        self.assertEqual(len(inst.base_files()), 8)

    def test_prompts_and_allow_list(self) -> None:
        chars = self.v.paragraph_chars()
        self.assertTrue(self.v.lengths_within(0.10), chars)
        self.assertEqual(chars["P"]["agent-a"], len(self.v.paragraph("P", "agent-a")))
        self.assertEqual(chars["N0"], {"agent-a": 0, "agent-b": 0})
        self.assertFalse(self.v.lengths_within(0.001))
        self.assertRegex(self.v.prompts_hash(), r"^[0-9a-f]{64}$")
        inst = self.v.build_instance(9001)
        s3 = self.v.session_prompt("N2", "agent-b", "s3", inst)
        self.assertIn("Continue your ticket and finish it.", s3)
        self.assertIn(self.v.SHARED_LINE, s3)
        self.assertNotIn("read NOTES.md", s3)        # N2 is told how to write, not to read
        self.assertIn("read NOTES.md", self.v.session_prompt("N1", "agent-b", "s3", inst))
        for arm in ("P", "N2", "N1", "N0"):
            self.assertNotIn(inst.token, self.v.session_prompt(arm, "agent-b", "s1", inst))
            self.assertNotIn("do not guess", self.v.session_prompt(arm, "agent-b", "s1",
                                                                   inst).lower())
        o = self.v.session_prompt("O", "agent-b", "s1", inst)
        self.assertIn(inst.token, o)
        self.assertIn(f"1/{inst.d}", o)
        self.assertIn("agent-b", self.v.session_prompt("P", "agent-b", "s4", inst))
        self.assertIn("Check whether a teammate has left you information",
                      self.v.session_prompt("P", "agent-b", "s4", inst))
        proj = self.root / "p"
        common = set(self.v.COMMON_ALLOW)
        for arm in ("N2", "N1", "N0", "O"):
            self.assertEqual(set(self.v.allowed(arm, proj)), common)
        p = set(self.v.allowed("P", proj))
        self.assertTrue(common < p)
        self.assertIn(f"Bash(python3 {self.v.DEST}/agent_mail.py *)", p)

    def test_command_uses_the_frozen_effort_and_the_allow_list_last(self) -> None:
        h = self.harness()
        cmd = h.command("N2", self.root / "a", self.root / "shared")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertEqual(cmd[cmd.index("--max-turns") + 1], "30")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertLess(cmd.index("--add-dir"), cmd.index("--allowedTools"))
        self.assertNotIn("--add-dir", h.command("P", self.root / "a", self.root / "s"))

    def test_the_two_checkouts_cannot_see_each_other_by_listing_a_parent(self) -> None:
        # the first live smoke run: agent-b ran `find` on its parent and read agent-a's sample
        for arm in ("N0", "P"):
            a, b, shared, extra, inst = self.build(arm)
            self.assertNotEqual(a.parent, b.parent)
            for mine, other in ((a, b), (b, a)):
                listing = {str(p) for p in mine.parent.rglob("*")}
                self.assertFalse(any(str(other) in x for x in listing), arm)
            self.assertTrue((a / "vendor" / "sample_export.csv").is_file())
            self.assertFalse(any(b.parent.rglob("sample_export.csv")))

    def test_isolation_rules_deny_the_file_tools_and_bash_on_the_other_container(self) -> None:
        root = self.root / "iso"
        (root / self.v.TREE["agent-a"]).mkdir(parents=True)
        rules = self.v.isolation_rules(root, "agent-a")
        c = (root / self.v.TREE["agent-a"]).resolve()
        self.assertEqual(rules, (f"Read(/{c}/**)", f"Edit(/{c}/**)", f"Write(/{c}/**)",
                                 f"Bash(*{self.v.TREE['agent-a']}*)"))
        self.assertNotIn(self.v.TREE["agent-b"], " ".join(rules))
        h = self.harness()
        cmd = h.command("N0", self.root / "b", self.root / "shared", rules)
        self.assertEqual(cmd[cmd.index("--disallowedTools") + 1:cmd.index("--allowedTools")],
                         list(rules))
        self.assertNotIn("--disallowedTools", h.command("N0", self.root / "b", self.root / "s"))

    def test_a_tool_call_naming_the_other_container_is_flagged(self) -> None:
        tool = lambda **i: {"name": "Read", "input": i}   # noqa: E731
        other = self.v.TREE["agent-a"]
        self.assertTrue(self.v.crossed([tool(file_path=f"/t/{other}/a/vendor/x.csv")], "agent-a"))
        self.assertTrue(self.v.crossed([{"name": "Bash", "input": {"command": f"find ../../{other}"}}],
                                       "agent-a"))
        self.assertFalse(self.v.crossed([tool(file_path="/t/checkout_of_agent_b/b/x.py")], "agent-a"))
        self.assertFalse(self.v.crossed([], "agent-a"))

    def test_event_detectors(self) -> None:
        r = self.v.reads_notes
        self.assertTrue(r([{"name": "Read", "input": {"file_path": "/x/NOTES.md"}}]))
        self.assertTrue(r([{"name": "Bash", "input": {"command": "cat NOTES.md"}}]))
        self.assertFalse(r([{"name": "Bash", "input": {"command": "echo 'x' >> NOTES.md"}}]))
        self.assertFalse(r([{"name": "Read", "input": {"file_path": "ledger/parse.py"}}]))
        s = self.v.runs_show
        self.assertTrue(s([{"name": "Bash", "input": {
            "command": "python3 tools/agent-postbox/agent_mail.py show 01ABC"}}]))
        self.assertFalse(s([{"name": "Bash", "input": {
            "command": "python3 tools/agent-postbox/agent_mail.py list"}}]))


class Pipeline(Base):
    def go(self, arm: str, driver: Any = None, **kw: Any) -> dict[str, Any]:
        h = self.harness(driver, **kw)
        return dict(h.run_arm(9001, arm))

    def test_p_picks_up_and_completes(self) -> None:
        rec = self.go("P")
        self.assertTrue(rec["b_ok"] and rec["completed"] and not rec["b_wrong_default"])
        self.assertTrue(rec["delivered"], "A's subject is in B's S3 hook output")
        self.assertTrue(rec["picked_up"])
        self.assertEqual((rec["generated"], rec["manual_nudges"]), (1, 0))
        self.assertFalse(rec["s4"]["ran"])
        self.assertEqual([s["tag"] for s in rec["sessions"]], ["s1", "s2", "s3"])
        s3 = rec["sessions"][2]
        self.assertIn("vendor facts", s3["hook_stdout"])
        self.assertTrue(s3["runs_show"])

    def test_p_delivered_but_not_picked_up_then_s4_recovers(self) -> None:
        rec = self.go("P", self.v.FakeDriver(pickup={"P": False}))
        self.assertTrue(rec["delivered"])
        self.assertFalse(rec["picked_up"] or rec["b_ok"])
        self.assertTrue(rec["b_wrong_default"] and rec["missed"])
        self.assertEqual(rec["s4"]["ran"], True)
        self.assertTrue(rec["s4"]["b_ok_after"])
        self.assertEqual(rec["sessions"][-1]["tag"], "s4")
        self.assertEqual(rec["manual_nudges"], 0)

    def test_n2_without_pickup_fails_and_n1_with_it_passes(self) -> None:
        rec = self.go("N2")
        self.assertFalse(rec["b_ok"] or rec["picked_up"])
        self.assertTrue(rec["b_wrong_default"] and rec["a_ok"] and rec["missed"])
        self.assertIsNone(rec["delivered"])
        self.assertEqual(rec["generated"], 1)
        self.assertTrue(rec["s4"]["ran"] and rec["s4"]["b_ok_after"])
        self.assertTrue(self.go("N2", self.v.FakeDriver(pickup={"N2": True}))["b_ok"])
        rec = self.go("N1")
        self.assertTrue(rec["b_ok"] and rec["picked_up"])

    def test_n0_cannot_recover_and_o_needs_one_session(self) -> None:
        rec = self.go("N0")
        self.assertFalse(rec["b_ok"])
        self.assertTrue(rec["s4"]["ran"] and not rec["s4"]["b_ok_after"])
        self.assertEqual(rec["generated"], 0)
        rec = self.go("O")
        self.assertTrue(rec["b_ok"] and not rec["s4"]["ran"])
        self.assertEqual([s["tag"] for s in rec["sessions"]], ["o"])

    def test_the_session_record_carries_usage_model_seconds_and_the_raw_path(self) -> None:
        rec = self.go("N0")
        for s in rec["sessions"]:
            self.assertEqual(s["usage"]["input_tokens"], 1000)
            self.assertIn("cache_read_input_tokens", s["usage"])
            self.assertEqual(s["model"], "fake-model")
            self.assertIn("fake-model", s["model_usage"])
            self.assertIsInstance(s["seconds"], float)
            self.assertTrue(Path(s["raw_events"]).is_file())
            self.assertEqual((s["cost_usd"], s["turns"], s["denials"]), (0.02, 6, 0))
        self.assertTrue((self.root / "out" / "runs" / "9001-N0-r0-t1.json").is_file())
        self.assertAlmostEqual(rec["cost_usd"], 0.08)       # three sessions and S4
        self.assertEqual(rec["paragraph_chars"], {"agent-a": 0, "agent-b": 0})

    def test_a_mailbox_mention_in_a_control_is_recorded(self) -> None:
        rec = self.go("N2", self.v.FakeDriver(mention_mailbox={"N2"}))
        self.assertTrue(rec["mailbox_mention"])
        self.assertFalse(self.go("N2")["mailbox_mention"])
        self.assertFalse(self.go("P")["mailbox_mention"])      # only controls are judged

    def test_an_infrastructure_failure_is_repeated_once_and_both_are_listed(self) -> None:
        h = self.harness(self.v.FakeDriver(fail_first={(9001, "N0")}))
        recs = h.run_with_retry(9001, "N0", 0)
        self.assertEqual([r["attempt"] for r in recs], [1, 2])
        self.assertEqual(recs[0]["infra_failure"], "host failure")
        self.assertEqual(recs[1]["infra_failure"], "")
        self.assertEqual(len(list((self.root / "out" / "runs").glob("9001-N0-*.json"))), 2)
        self.assertEqual(len(self.v.final_records(recs)), 1)

    def test_two_rate_limits_in_a_row_stop_the_run(self) -> None:
        slept: list[int] = []
        h = self.harness(self.v.FakeDriver(rate_limit={(9001, "N0"), (9001, "N1"),
                                                       (9001, "N2"), (9001, "P"),
                                                       (9001, "O")}))
        h.sleep = slept.append
        recs = h.run_instance(9001)
        self.assertIn("two rate-limit rejections", h.stop or "")
        self.assertTrue(slept and slept[0] == self.v.BACKOFF[0])
        self.assertTrue(any("aborted" in r or r["infra_failure"] for r in recs))
        with self.assertRaises(self.v.micro.Ceiling):
            h.reserve()                       # nothing more starts after a stop

    def test_the_ceiling_refuses_before_a_session_and_counts_workers_in_flight(self) -> None:
        h = self.harness(ceiling_usd=1.0)
        h.spent = 0.9
        with self.assertRaises(self.v.micro.Ceiling):
            h.reserve()                                 # 0.9 + 1.5 * 0.30 > 1.0
        h.spent, h.biggest = 0.1, 0.1
        h.reserve()
        self.assertEqual(h.inflight, 1)
        for _ in range(3):
            h.reserve()                                 # 0.1 + 4 * 0.15 <= 1.0
        with self.assertRaises(self.v.micro.Ceiling):
            for _ in range(10):                         # about 6 in flight is the most
                h.reserve()
        self.assertLessEqual(h.inflight, 6)
        rec = self.harness(ceiling_usd=0.1).run_arm(9001, "N0")
        self.assertIn("aborted", rec)
        self.assertEqual(rec["sessions"], [])

    def test_a_block_runs_in_parallel_and_the_totals_add_up(self) -> None:
        h = self.harness(workers=3)
        recs = h.run_block([9001, 9002, 9003])
        self.assertEqual(len(self.v.final_records(recs)), 3 * 4 + 3 * 3)    # O thrice (smoke)
        self.assertAlmostEqual(h.spent, 0.02 * h.n_sessions)
        self.assertEqual(h.inflight, 0)
        self.assertEqual(h.n_sessions, len(list((self.root / "out" / "raw").glob("*.jsonl"))))

    def test_blocks_and_stop_rules(self) -> None:
        self.assertEqual([len(b) for b in self.v.blocks(list(range(3001, 3061)))], [10] * 6)

        def rec(seed: int, arm: str, ok: bool, infra: str = "") -> dict[str, Any]:
            return {"seed": seed, "arm": arm, "b_ok": ok, "infra_failure": infra}
        seeds = list(range(3001, 3021))
        good = [rec(s, "O", True) for s in seeds] + [rec(s, "N0", False) for s in seeds]
        self.assertIsNone(self.v.stop_reason(good, seeds))
        self.assertIsNone(self.v.stop_reason(good[:5], seeds[:10]))      # fewer than 20: no look
        bad_o = [rec(s, "O", s < 3010) for s in seeds] + [rec(s, "N0", False) for s in seeds]
        self.assertIn("gate 1", self.v.stop_reason(bad_o, seeds) or "")
        bad_n0 = [rec(s, "O", True) for s in seeds] + [rec(s, "N0", s < 3010) for s in seeds]
        self.assertIn("gate 2", self.v.stop_reason(bad_n0, seeds) or "")
        infra = good + [rec(s, "P", False, "timeout") for s in seeds[:3]]
        self.assertIn("infrastructure", self.v.stop_reason(infra, seeds) or "")


class Stages(Base):
    def args_ns(self, *argv: str) -> argparse.Namespace:
        return self.v.parse(["--out", str(self.root / "o"), *argv])

    def test_ceiling_defaults_and_flag(self) -> None:
        self.assertEqual(self.args_ns("--stage", "smoke").ceiling_usd, 4.0)
        self.assertEqual(self.args_ns("--stage", "main").ceiling_usd, 30.0)
        self.assertEqual(self.args_ns("--stage", "main", "--ceiling-usd", "12").ceiling_usd, 12.0)
        self.assertEqual(self.v.SEEDS["smoke"], [9001, 9002, 9003])
        self.assertEqual((self.v.SEEDS["main"][0], self.v.SEEDS["main"][-1]), (3001, 3060))

    def test_the_plan_and_projection(self) -> None:
        main = self.v.plan_sessions("main")
        self.assertEqual((main["sessions"], main["total"]), (900, 990))
        proj = self.v.projection("main")
        self.assertAlmostEqual(proj["likely"], 19.8)
        self.assertLessEqual(proj["high"], 60.0 + 1)
        smoke = self.v.plan_sessions("smoke")
        self.assertEqual(smoke["per_instance"], 15)
        self.assertTrue(40 <= smoke["total"] <= 60)

    def test_neither_or_both_modes_and_live_without_the_variable_refuse(self) -> None:
        rc, _, err = self.main("--stage", "smoke", "--out", str(self.root / "o"))
        self.assertEqual(rc, 2)
        self.assertIn("--dry-run", err)
        rc, _, err = self.main("--stage", "smoke", "--out", str(self.root / "o"), "--live",
                               "--dry-run")
        self.assertEqual(rc, 2)
        rc, _, err = self.main("--stage", "smoke", "--out", str(self.root / "o"), "--live")
        self.assertEqual(rc, 2)
        self.assertIn(self.v.LIVE_ENV, err)
        rc, _, err = self.main("--stage", "smoke", "--out", str(self.root / "o"), "--live",
                               env={self.v.LIVE_ENV: "0"})
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "o").exists())

    def test_the_main_stage_refuses_without_a_gate(self) -> None:
        live = {self.v.LIVE_ENV: "1"}
        rc, _, err = self.main("--stage", "main", "--out", str(self.root / "o"), "--live",
                               env=live)
        self.assertEqual(rc, 2)
        self.assertIn("--gate", err)
        rc, _, err = self.main("--stage", "main", "--out", str(self.root / "o"), "--live",
                               "--gate", str(self.root / "missing.json"), env=live)
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)
        self.assertFalse((self.root / "o").exists())

    def gate_file(self, **over: Any) -> Path:
        gate = {"passed": True, "dry_run": False, "checks": {"x": True},
                "prompts_sha256": self.v.prompts_hash(), "model": "haiku", "effort": "medium"}
        gate.update(over)
        p = self.root / "gate.json"
        p.write_text(json.dumps(gate))
        return p

    def test_gate_checks(self) -> None:
        ns = self.args_ns("--stage", "main")
        self.assertIsNone(self.v.check_gate(str(self.gate_file()), ns))
        self.assertIn("did not pass", self.v.check_gate(
            str(self.gate_file(passed=False, checks={"cost gate": False})), ns) or "")
        self.assertIn("dry-run", self.v.check_gate(str(self.gate_file(dry_run=True)), ns) or "")
        self.assertIn("prompts_sha256", self.v.check_gate(
            str(self.gate_file(prompts_sha256="0" * 64)), ns) or "")
        self.assertIn("effort", self.v.check_gate(str(self.gate_file(effort="low")), ns) or "")
        (self.root / "gate.json").write_text("{not json")
        self.assertIn("not valid", self.v.check_gate(str(self.root / "gate.json"), ns) or "")

    def test_a_valid_gate_but_no_claude_still_stops_before_any_session(self) -> None:
        env = {self.v.LIVE_ENV: "1", "PATH": str(self.root)}
        old = os.environ.get("PATH")
        os.environ["PATH"] = str(self.root)
        try:
            rc, _, err = self.main("--stage", "main", "--out", str(self.root / "o"), "--live",
                                   "--gate", str(self.gate_file()), env=env)
        finally:
            os.environ["PATH"] = old or ""
        self.assertEqual(rc, 2)
        self.assertIn("claude", err)
        self.assertFalse((self.root / "o").exists())

    def test_the_smoke_gate_decides_from_the_records(self) -> None:
        h = self.harness(workers=3)
        recs = h.run_block(self.v.SEEDS["smoke"])
        ns = self.args_ns("--stage", "smoke")
        gate = self.v.smoke_gate(recs, h.spent, ns, self.v.offline_checks())
        self.assertTrue(gate["passed"], gate["checks"])
        self.assertAlmostEqual(gate["mean_cost_per_session"], 0.02)
        self.assertAlmostEqual(gate["projected_main_usd"], 29.7)
        self.assertEqual(gate["prompts_sha256"], self.v.prompts_hash())
        costly = self.harness(self.v.FakeDriver(cost=0.04), workers=3,
                              ceiling_usd=100.0)           # over the USD 0.034 gate
        dear = self.v.smoke_gate(costly.run_block(self.v.SEEDS["smoke"]), 1.0, ns,
                                 self.v.offline_checks())
        self.assertFalse(dear["passed"])
        self.assertFalse(dear["checks"][f"cost gate: mean per session <= ${self.v.GATE_MEAN_USD}"])
        leaky = self.harness(self.v.FakeDriver(mention_mailbox={"N0"}), workers=3)
        bad = self.v.smoke_gate(leaky.run_block(self.v.SEEDS["smoke"]), 1.0, ns,
                                self.v.offline_checks())
        self.assertFalse(bad["checks"]["no mailbox mention in any non-P run"])

    def test_dry_run_runs_the_pipeline_offline_and_the_gate_is_marked(self) -> None:
        def boom(self: Any, spec: Any) -> Any:
            raise AssertionError("the live driver must not run in a dry run")
        saved = self.v.LiveDriver.run
        self.v.LiveDriver.run = boom
        try:
            rc, out, err = self.main("--stage", "smoke", "--out", str(self.root / "o"),
                                     "--dry-run", "--dry-instances", "1", "--workdir",
                                     str(self.root / "wd"))
        finally:
            self.v.LiveDriver.run = saved
        self.assertEqual(rc, 0, err)
        self.assertIn("projected cost likely", out)
        self.assertIn("P=346/346", out)
        gate = json.loads((self.root / "o" / "smoke_gate.json").read_text())
        self.assertTrue(gate["dry_run"])
        ns = self.args_ns("--stage", "main")
        self.assertIn("dry-run", self.v.check_gate(str(self.root / "o" / "smoke_gate.json"),
                                                   ns) or "")
        # the analysis reads what the dry run wrote
        with contextlib.redirect_stdout(io.StringIO()):
            rc2 = self.an.main(["--runs", str(self.root / "o")])
        self.assertEqual(rc2, 0)
        summary = json.loads((self.root / "o" / "summary.json").read_text())
        self.assertEqual(summary["arms"]["O"]["runs"], 3)

    def test_the_live_driver_is_not_used_by_any_test_path(self) -> None:
        text = Path(__file__).read_text(encoding="utf-8")
        self.assertEqual(text.count("Live" + "Driver()"), 0)


class Analysis(Base):
    def run_rec(self, seed: int, arm: str, ok: bool, *, cost: float = 0.1, attempt: int = 1,
                infra: str = "", mention: bool = False, done: bool | None = None) -> dict[str, Any]:
        sess = {"tag": "s3", "seconds": 10.0, "cost_usd": cost, "infra": infra,
                "usage": {"input_tokens": 10, "output_tokens": 5}}
        chars = {"P": {"agent-a": 300, "agent-b": 300}, "N2": {"agent-a": 290, "agent-b": 290}}
        return {"seed": seed, "arm": arm, "rep": 0, "attempt": attempt, "b_ok": ok,
                "a_ok": True, "completed": ok if done is None else done, "b_unit_ok": ok,
                "b_void_ok": ok, "b_wrong_default": not ok, "b_missing": False,
                "delivered": arm == "P", "picked_up": ok, "acknowledged": False,
                "missed": not ok, "generated": 1, "cost_usd": cost, "seconds": 30.0,
                "denials": 0, "infra_failure": infra, "mailbox_mention": mention,
                "paragraph_chars": chars.get(arm, {"agent-a": 0, "agent-b": 0}),
                "s4": {"ran": not ok, "b_ok_after": True, "cost_usd": 0.01},
                "sessions": [sess]}

    def data(self, n: int = 60, p_only: int = 20, n2_only: int = 2, both: int = 20,
             **kw: Any) -> list[dict[str, Any]]:
        recs = []
        for i in range(n):
            s = 3001 + i
            p_ok = i < p_only + both
            n2_ok = (p_only <= i < p_only + both) or (p_only + both <= i < p_only + both + n2_only)
            recs += [self.run_rec(s, "P", p_ok), self.run_rec(s, "N2", n2_ok),
                     self.run_rec(s, "N1", p_ok), self.run_rec(s, "N0", False),
                     self.run_rec(s, "O", True)]
        return recs

    def test_exact_mcnemar(self) -> None:
        self.assertEqual(self.an.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(self.an.mcnemar_exact(10, 2), 158 / 4096)
        self.assertAlmostEqual(self.an.mcnemar_exact(2, 10), 158 / 4096)
        self.assertEqual(self.an.mcnemar_exact(5, 5), 1.0)
        self.assertAlmostEqual(self.an.mcnemar_exact(0, 8), 2 / 256)
        self.assertLessEqual(self.an.mcnemar_exact(30, 0), 1e-8 * 4)

    def test_paired_difference_interval(self) -> None:
        d, lo, hi = self.an.paired_difference(18, 3, 20, 19)
        self.assertAlmostEqual(d, 15 / 60)
        self.assertTrue(0 < lo < d < hi < 1)
        d2, lo2, hi2 = self.an.paired_difference(3, 18, 20, 19)          # symmetric
        for got, want in zip((d2, lo2, hi2), (-d, -hi, -lo), strict=True):
            self.assertAlmostEqual(got, want, places=9)
        d0, lo0, hi0 = self.an.paired_difference(0, 0, 30, 30)
        self.assertEqual(d0, 0.0)
        self.assertTrue(lo0 < 0 < hi0)
        wide = self.an.paired_difference(5, 1, 5, 9)
        wider = self.an.paired_difference(50, 10, 50, 90)
        self.assertGreater(wide[2] - wide[1], wider[2] - wider[1])       # more data, tighter
        self.assertEqual(self.an.paired_difference(0, 0, 0, 0), (0.0, -1.0, 1.0))
        self.assertAlmostEqual(self.an.micro.wilson(30, 60)[0], 0.3773, places=3)

    def test_a_clear_advantage(self) -> None:
        s = self.an.summarise(self.data())
        prim = s["primary_P_vs_N2"]
        self.assertEqual((prim["pairs"], prim["x_only"], prim["y_only"]), (60, 20, 2))
        self.assertLess(prim["mcnemar_exact_p"], 0.001)
        self.assertGreater(prim["ci95"][0], 0)
        self.assertTrue(all(g["pass"] for g in s["validity_gates"].values()), s["validity_gates"])
        self.assertTrue(s["verdict"].startswith("net P advantage"))
        self.assertEqual(s["arms"]["P"]["b_ok"], 40)
        self.assertEqual(s["secondary_descriptive"]["contrasts_stated"], 4)

    def test_other_verdict_rows(self) -> None:
        none = self.an.summarise(self.data(p_only=1, n2_only=1, both=30))
        self.assertTrue(none["verdict"].startswith("no advantage of 15"), none["verdict"])
        against = self.an.summarise(self.data(p_only=2, n2_only=20, both=20))
        self.assertTrue(against["verdict"].startswith("AGAINST POSTBOX"), against["verdict"])
        unclear = self.an.summarise(self.data(p_only=9, n2_only=3, both=20))
        self.assertEqual(unclear["verdict"], "inconclusive at this sample size")
        short = self.an.summarise(self.data(n=30))
        self.assertTrue(short["verdict"].startswith("incomplete"))

    def test_a_failed_gate_voids_the_contrast(self) -> None:
        recs = [r for r in self.data() if not (r["arm"] == "O" and r["seed"] % 2 == 0)]
        recs += [self.run_rec(s, "O", False) for s in range(3001, 3061) if s % 2 == 0]
        s = self.an.summarise(recs)
        self.assertFalse(s["validity_gates"]["1_O_b_ok_at_least_80pct"]["pass"])
        self.assertTrue(s["verdict"].startswith("void"))
        leaks = self.data()
        for r in leaks[:15]:
            r["mailbox_mention"] = r["arm"] != "P"
        s = self.an.summarise(leaks)
        self.assertFalse(s["validity_gates"]["3_no_mailbox_mention_in_non_P_runs"]["pass"])
        wide = self.data()
        for r in wide:
            if r["arm"] == "N2":
                r["paragraph_chars"] = {"agent-a": 200, "agent-b": 200}
        self.assertFalse(self.an.summarise(wide)["validity_gates"][
            "4_P_N2_paragraphs_within_10pct"]["pass"])

    def test_infrastructure_repeats_use_the_last_attempt_and_are_listed(self) -> None:
        recs = self.data(n=10, p_only=3, n2_only=0, both=3)
        recs.append(self.run_rec(3001, "P", False, attempt=1, infra="timeout"))   # earlier try
        recs[0]["attempt"] = 2
        s = self.an.summarise(recs)
        self.assertEqual(s["earlier_attempts"], ["3001-P-t1"])
        gate5 = "5_sessions_free_of_infrastructure_failure_90pct"
        self.assertEqual(s["validity_gates"][gate5]["failed"], 0)     # the repeat succeeded
        recs.append(self.run_rec(3002, "P", False, attempt=2, infra="timeout"))
        self.assertEqual(self.an.summarise(recs)["validity_gates"][gate5]["failed"], 1)

    def test_cost_per_completed_tokens_and_s4(self) -> None:
        recs = [self.run_rec(1, "P", True, cost=0.10), self.run_rec(2, "P", False, cost=0.30),
                self.run_rec(3, "P", True, cost=0.20)]
        arm = self.an.arm_summary(recs, "P")
        self.assertEqual((arm["completed"], arm["cost_usd"]), (2, 0.6))
        self.assertAlmostEqual(arm["cost_per_completed_usd"], 0.3)
        self.assertEqual(arm["tokens"]["input_tokens"], 30)
        self.assertEqual((arm["s4_ran"], arm["s4_recovered"]), (1, 1))
        self.assertEqual(arm["manual_nudges"], 0)
        empty = self.an.arm_summary([], "N1")
        self.assertIsNone(empty["cost_per_completed_usd"])

    def test_p_worse_than_n1_and_costly_is_called_out(self) -> None:
        recs = []
        for i in range(60):
            s = 3001 + i
            recs += [self.run_rec(s, "P", i >= 22, cost=0.30), self.run_rec(s, "N2", i >= 22),
                     self.run_rec(s, "N1", True, cost=0.10), self.run_rec(s, "N0", False),
                     self.run_rec(s, "O", True)]
        notes = self.an.summarise(recs)["notes"]
        self.assertTrue(notes and "against the current adapter design" in notes[0], notes)

    def test_freeze_prints_the_script_hash(self) -> None:
        cp = subprocess.run([sys.executable, "-I", str(ANALYSIS), "--freeze"],
                            capture_output=True, text=True, timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        want = hashlib.sha256(ANALYSIS.read_bytes()).hexdigest()
        self.assertTrue(cp.stdout.startswith(f"{want}  analyze_v2.py"), cp.stdout)
        self.assertIn("experiment_micro.py (wilson)", cp.stdout)
        self.assertEqual(self.an.freeze(), cp.stdout.strip())

    def test_cli_writes_summary_json_and_refuses_empty_input(self) -> None:
        runs = self.root / "res" / "runs"
        runs.mkdir(parents=True)
        for i, r in enumerate(self.data(n=4, p_only=1, n2_only=1, both=1)):
            (runs / f"{i}.json").write_text(json.dumps(r))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = self.an.main(["--runs", str(self.root / "res"), "--out",
                               str(self.root / "s.json")])
        self.assertEqual(rc, 0)
        s = json.loads((self.root / "s.json").read_text())
        self.assertTrue(s["verdict"].startswith(("void", "incomplete")))
        (self.root / "empty").mkdir()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.an.main(["--runs", str(self.root / "empty")]), 2)
        self.assertTrue(math.isfinite(s["primary_P_vs_N2"]["difference"]))


if __name__ == "__main__":
    unittest.main()
