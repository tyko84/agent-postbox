#!/usr/bin/env python3
"""Offline tests of scripts/experiment_v3.py (the v3 harness) and scripts/analyze_v3.py; see
docs/experiment-v3.md. No model is called and no account is needed. Every "agent" here is a
SIMULATION (FakeDriver3): it edits files like a simple agent, uses the real mail CLI and the real
hook, and emits stream-json events of the host's shape. Nothing in this file is observed model
behaviour, and no result of these tests says anything about Postbox.

Checked: the prompts (four B prompts, prompts 2 to 4 identical in every arm and free of any channel
word, P and N2 paragraphs within 10%, the heredoc form taught, one common allow-list); the run
pipeline for every arm (order of sessions, b_ok by prompt, delivery from the hook at a later
prompt, pickup, simulated host refusals kept and listed); infrastructure repeat; the hard spend
stop and the prior-spend carry-over; the cumulative-cost correction; the refusals (no flag, no
environment variable, --out inside the repository, no gate); the stop rules including the
ceiling gate on the control; and the analysis (verdict rows including 'uninformative', gates,
refusal report, freeze). Skipped where scripts/ is not present. Stdlib only.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
SCRIPT = SCRIPTS / "experiment_v3.py"
ANALYSIS = SCRIPTS / "analyze_v3.py"


def load(path: Path, name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(SCRIPT.exists(), "scripts/ is not present")
class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.v: Any = load(SCRIPT, "experiment_v3")
        self.v2 = sys.modules["experiment_v2"]
        self.an = load(ANALYSIS, "analyze_v3")
        tmp = tempfile.TemporaryDirectory(prefix="test-v3-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "work").mkdir()
        # the fake driver reports a per-turn cost; the live default (running total) is tested apart
        saved = self.v.COST_CUMULATIVE
        vars(self.v)["COST_CUMULATIVE"] = False
        self.addCleanup(setattr, self.v, "COST_CUMULATIVE", saved)

    def args(self, **kw: Any) -> argparse.Namespace:
        base = dict(model="haiku", effort="medium", max_turns=30, max_calls=10**6,
                    ceiling_usd=15.0, workers=3, stage="readiness", prior_spend_usd=0.0,
                    dry_run=True, projection_cap=15.0)
        base.update(kw)
        return argparse.Namespace(**base)

    def harness(self, driver: Any = None, **kw: Any) -> Any:
        return self.v.Harness(self.args(**kw), "claude", self.root / "work", self.root / "out",
                              driver or self.v.FakeDriver3(), sleep=lambda s: None)

    def go(self, arm: str, driver: Any = None, **kw: Any) -> dict[str, Any]:
        return dict(self.harness(driver, **kw).run_arm(9101, arm))

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


class Prompts(Base):
    def test_four_b_prompts_and_later_ones_are_identical_and_channel_free(self) -> None:
        inst = self.v2.build_instance(9101)
        sets = {arm: self.v.b_prompts(arm, inst) for arm in self.v.ARMS}
        for arm, ps in sets.items():
            self.assertEqual(len(ps), 4, arm)
            self.assertEqual(ps[1:], list(self.v.B_LATER), arm)
        words = ("mail", "notes", "NOTES", "teammate", "agent-a", "channel", "agent_mail", "EOF")
        for later in self.v.B_LATER:
            self.assertFalse([w for w in words if w in later], later)

    def test_only_prompt_one_differs_between_arms(self) -> None:
        inst = self.v2.build_instance(9101)
        first = {arm: self.v.b_prompts(arm, inst)[0] for arm in self.v.ARMS}
        self.assertEqual(len(set(first.values())), 4)
        self.assertNotIn("agent_mail", first["N2"] + first["N0"] + first["O"])
        self.assertNotIn("NOTES", first["P"] + first["N0"] + first["O"])
        self.assertIn(str(inst.d), first["O"])
        self.assertNotIn(inst.token, first["P"] + first["N2"] + first["N0"])

    def test_p_is_taught_the_heredoc_form_and_not_the_inline_body(self) -> None:
        p = self.v.paragraph("P", "agent-a")
        self.assertIn("--body-file - <<'EOF'", p)
        self.assertNotIn("--body '", p)
        self.assertNotIn("check", p.lower())                # not told to read
        self.assertNotIn("read", self.v.paragraph("N2", "agent-b").lower().replace("already", ""))

    def test_lengths_within_ten_percent_and_recorded(self) -> None:
        self.assertTrue(self.v.lengths_within())
        chars = self.v.paragraph_chars()
        self.assertLessEqual(abs(chars["P"]["agent-b"] - chars["N2"]["agent-b"]),
                             0.1 * chars["N2"]["agent-b"])

    def test_symmetry_table_is_computed(self) -> None:
        sym = self.v.symmetry(self.root / "proj")
        self.assertEqual(set(sym), set(self.v.ARMS))
        for arm, row in sym.items():
            self.assertEqual(row["tools"], "Bash,Read,Edit,Write")
            self.assertTrue(row["later_prompts_identical"])
            self.assertEqual(row["common_allow_rules"], len(self.v.COMMON_ALLOW))
            self.assertEqual(bool(row["extra_allow_rules"]), arm == "P")
        self.assertEqual(len(sym["P"]["extra_allow_rules"]), 4)
        self.assertTrue(sym["N2"]["add_dir_shared"])
        self.assertFalse(sym["P"]["add_dir_shared"])

    def test_p_allow_rules_cover_the_four_spellings(self) -> None:
        proj = self.root / "proj"
        rules = self.v.allow_rules("P", proj)
        a = str(proj / "tools/agent-postbox/agent_mail.py")
        for want in ("Bash(python3 tools/agent-postbox/agent_mail.py *)",
                     f'Bash(python3 "{a}" *)', f"Bash(python3 '{a}' *)", f"Bash(python3 {a} *)"):
            self.assertIn(want, rules)
        self.assertEqual(self.v.allow_rules("N0", proj), self.v.COMMON_ALLOW)

    def test_prompts_hash_changes_with_a_prompt(self) -> None:
        h = self.v.prompts_hash()
        self.assertEqual(h, self.v.prompts_hash())
        old = self.v.B_LATER
        self.v.B_LATER = ("Continue.",) + old[1:]
        try:
            self.assertNotEqual(h, self.v.prompts_hash())
        finally:
            self.v.B_LATER = old

    def test_arm_order_is_seeded_and_covers_all_arms(self) -> None:
        self.assertEqual(self.v.arm_order(4001), self.v.arm_order(4001))
        self.assertEqual(sorted(self.v.arm_order(4002)), sorted(self.v.ARMS))
        self.assertEqual(len({tuple(self.v.arm_order(s)) for s in self.v.SEEDS["main"]}) > 5, True)

    def test_seeds_do_not_reuse_earlier_experiments(self) -> None:
        main = set(self.v.SEEDS["main"])
        self.assertEqual(len(main), 60)
        self.assertFalse(main & set(range(3001, 3061)) | main & {9001, 9002, 9003})
        self.assertFalse(main & set(self.v.SEEDS["readiness"]))


class Pipeline(Base):
    def test_p_looks_at_prompt_two_and_completes(self) -> None:
        rec = self.go("P")
        self.assertEqual([s["tag"] for s in rec["sessions"]], ["b1", "a", "b2", "b3", "b4"])
        self.assertTrue(rec["b_ok"])
        self.assertEqual(rec["b_ok_by_prompt"], [False, True, True, True])
        self.assertTrue(rec["delivered"] and rec["picked_up"])
        self.assertEqual(rec["first_look_prompt"], 2)
        self.assertEqual((rec["generated"], rec["manual_nudges"]), (1, 0))
        self.assertIn("vendor facts", rec["sessions"][2]["hook_stdout"])
        self.assertEqual(rec["channel_denials"], 0)

    def test_delivery_is_the_hook_at_a_later_prompt_even_if_b_never_looks(self) -> None:
        rec = self.go("P", self.v.FakeDriver3(look_turn={"P": None}))
        self.assertFalse(rec["b_ok"])
        self.assertTrue(rec["delivered"])
        self.assertFalse(rec["picked_up"])
        self.assertIsNone(rec["first_look_prompt"])
        self.assertTrue(rec["missed"])

    def test_n2_that_never_looks_fails_and_one_that_looks_late_is_scored_by_prompt(self) -> None:
        rec = self.go("N2")
        self.assertFalse(rec["b_ok"])
        self.assertEqual(rec["b_ok_by_prompt"], [False] * 4)
        self.assertFalse(rec["picked_up"])
        self.assertTrue(rec["a_wrote"] and rec["missed"])
        late = self.go("N2", self.v.FakeDriver3(look_turn={"N2": 4}))
        self.assertEqual(late["b_ok_by_prompt"], [False, False, False, True])
        self.assertTrue(late["b_ok"])
        self.assertEqual(late["first_look_prompt"], 4)

    def test_n0_cannot_succeed_and_o_has_no_agent_a_session(self) -> None:
        n0 = self.go("N0")
        self.assertFalse(n0["b_ok"])
        self.assertTrue(n0["b_wrong_default"])
        self.assertEqual([s["tag"] for s in n0["sessions"]], ["b1", "a", "b2", "b3", "b4"])
        o = self.go("O")
        self.assertTrue(o["b_ok"])
        self.assertEqual([s["tag"] for s in o["sessions"]], ["b1", "b2", "b3", "b4"])
        self.assertEqual(o["b_ok_by_prompt"], [True] * 4)

    def test_one_live_b_process_per_run_is_opened_and_closed(self) -> None:
        d = self.v.FakeDriver3()
        self.go("P", d)
        self.assertEqual(len(d.opened), 1)
        self.assertEqual(d.closed, d.opened)

    def test_a_simulated_host_refusal_is_kept_listed_and_counted_as_a_failure(self) -> None:
        rec = self.go("P", self.v.FakeDriver3(refuse_send=True))
        self.assertFalse(rec["b_ok"])
        self.assertEqual(rec["channel_denials"], 1)
        self.assertEqual(len(rec["host_refusals"]), 1)
        self.assertIn("agent_mail.py", rec["host_refusals"][0]["input"])
        self.assertEqual(rec["host_refusals"][0]["tag"], "a")
        self.assertFalse(rec["a_wrote"])

    def test_session_record_fields(self) -> None:
        rec = self.go("P")
        for s in rec["sessions"]:
            for key in ("cost_usd", "turns", "usage", "model", "seconds", "raw_events",
                        "denials", "channel_denials", "denial_details", "turn",
                        "result_subtype", "hook_stdout"):
                self.assertIn(key, s)
            self.assertTrue(Path(s["raw_events"]).is_file())
        for key in ("order", "order_index", "model", "effort", "seed", "arm"):
            self.assertIn(key, rec)
        self.assertTrue((self.root / "out" / "runs" / "9101-P-r0-t1.json").is_file())
        self.assertTrue((self.root / "out" / "spend.json").is_file())

    def test_a_mailbox_mention_in_a_control_is_recorded(self) -> None:
        rec = self.go("N2", self.v.FakeDriver3(mention_mailbox={"N2"}))
        self.assertTrue(rec["mailbox_mention"])
        self.assertFalse(self.go("N0")["mailbox_mention"])

    def test_non_p_projects_omit_the_mailbox(self) -> None:
        d = self.v.FakeDriver3()
        self.go("N2", d)
        for spec in d.calls:
            self.assertNotIn("agent_mail", spec.prompt)

    def test_an_infrastructure_failure_is_repeated_once_and_both_are_listed(self) -> None:
        h = self.harness(self.v.FakeDriver3(fail_first={(9101, "N0")}))
        recs = h.run_with_retry(9101, "N0", 0)
        self.assertEqual([r["attempt"] for r in recs], [1, 2])
        self.assertTrue(recs[0]["infra_failure"] and not recs[1]["infra_failure"])
        self.assertTrue((self.root / "out" / "runs" / "9101-N0-r0-t1.json").is_file())
        self.assertTrue((self.root / "out" / "runs" / "9101-N0-r0-t2.json").is_file())

    def test_two_rate_limits_in_a_row_stop_the_run(self) -> None:
        h = self.harness(self.v.FakeDriver3(rate_limit={(9101, "P"), (9101, "N2")}))
        recs = h.run_instance(9101)
        self.assertTrue(h.stop or any("aborted" in r for r in recs))

    def test_the_hard_ceiling_stops_before_it_is_passed(self) -> None:
        h = self.harness(self.v.FakeDriver3(cost=0.30), ceiling_usd=1.0, workers=1)
        recs = h.run_block([9101, 9102])
        self.assertLessEqual(h.spent, 1.0)
        self.assertTrue(any("aborted" in r for r in recs))

    def test_prior_spend_counts_against_the_ceiling(self) -> None:
        h = self.harness(self.v.FakeDriver3(cost=0.10), ceiling_usd=1.0, prior_spend_usd=0.85)
        self.assertAlmostEqual(h.spent, 0.85)
        rec = h.run_arm(9101, "P")
        self.assertIn("aborted", rec)
        self.assertLessEqual(h.spent, 1.0)

    def test_cumulative_cost_is_billed_as_the_difference(self) -> None:
        old = self.v.COST_CUMULATIVE
        self.v.COST_CUMULATIVE = True
        try:
            d = self.v.FakeDriver3(cost=0.01)
            orig = d.run
            counter: dict[str, int] = {}

            def run(spec: Any) -> Any:
                raw = orig(spec)
                if spec.who == "agent-b":
                    n = counter[spec.key] = counter.get(spec.key, 0) + 1
                    lines = [json.loads(x) for x in raw.stdout.splitlines()]
                    for ev in lines:
                        if ev.get("type") == "result":
                            ev["total_cost_usd"] = 0.01 * n        # a running total
                    raw.stdout = "\n".join(json.dumps(e) for e in lines) + "\n"
                return raw

            d.run = run
            h = self.harness(d)
            rec = h.run_arm(9101, "O")
        finally:
            self.v.COST_CUMULATIVE = old
        self.assertAlmostEqual(rec["cost_usd"], 0.04, places=6)
        self.assertAlmostEqual(h.spent, 0.04, places=6)
        self.assertEqual([s["cost_reported"] for s in rec["sessions"]], [0.01, 0.02, 0.03, 0.04])

    def test_a_block_runs_all_arms_on_every_seed(self) -> None:
        h = self.harness(workers=2)
        recs = h.run_block([9101, 9102])
        self.assertEqual(sorted((r["seed"], r["arm"]) for r in recs),
                         sorted((s, a) for s in (9101, 9102) for a in self.v.ARMS))


class Stages(Base):
    def ns(self, *argv: str) -> argparse.Namespace:
        return self.v.parse(["--out", str(self.root / "o"), *argv])

    def test_ceilings_and_seeds(self) -> None:
        self.assertEqual(self.ns("--stage", "readiness").ceiling_usd, 1.0)
        self.assertEqual(self.ns("--stage", "main").ceiling_usd, 15.0)
        self.assertEqual(self.ns("--stage", "main", "--ceiling-usd", "9").ceiling_usd, 9.0)

    def test_plan_and_projection(self) -> None:
        plan = self.v.plan_sessions("main")
        self.assertEqual((plan["instances"], plan["per_instance"], plan["turns"]), (60, 19, 1140))
        proj = self.v.projection(0.10)
        self.assertAlmostEqual(proj["expected"], 6.6)
        self.assertAlmostEqual(proj["with_1.5_margin"], 9.9)

    def test_refusals(self) -> None:
        o = str(self.root / "o")
        rc, _, err = self.main("--stage", "readiness", "--out", o)
        self.assertEqual(rc, 2)
        self.assertIn("--dry-run", err)
        rc, _, err = self.main("--stage", "readiness", "--out", o, "--live", "--dry-run")
        self.assertEqual(rc, 2)
        rc, _, err = self.main("--stage", "readiness", "--out", o, "--live")
        self.assertEqual(rc, 2)
        self.assertIn(self.v.LIVE_ENV, err)
        rc, _, err = self.main("--stage", "readiness", "--out", o, "--live",
                               env={self.v.LIVE_ENV: "0"})
        self.assertEqual(rc, 2)
        self.assertFalse((self.root / "o").exists())

    def test_out_inside_the_repository_is_refused(self) -> None:
        rc, _, err = self.main("--stage", "readiness", "--dry-run", "--out",
                               str(HERE / "raw-data-must-not-go-here"))
        self.assertEqual(rc, 2)
        self.assertIn("inside the repository", err)
        self.assertFalse((HERE / "raw-data-must-not-go-here").exists())

    def test_the_main_stage_refuses_without_a_gate(self) -> None:
        live = {self.v.LIVE_ENV: "1"}
        rc, _, err = self.main("--stage", "main", "--out", str(self.root / "o"), "--live", env=live)
        self.assertEqual(rc, 2)
        self.assertIn("--gate", err)
        self.assertFalse((self.root / "o").exists())

    def gate_file(self, **over: Any) -> Path:
        gate = {"passed": True, "dry_run": False, "checks": {"x": True},
                "prompts_sha256": self.v.prompts_hash(), "model": "haiku", "effort": "medium"}
        gate.update(over)
        p = self.root / "gate.json"
        p.write_text(json.dumps(gate))
        return p

    def test_gate_checks(self) -> None:
        ns = self.ns("--stage", "main")
        self.assertIsNone(self.v.check_gate(str(self.gate_file()), ns))
        self.assertIn("did not pass", self.v.check_gate(
            str(self.gate_file(passed=False, checks={"cost gate": False})), ns) or "")
        self.assertIn("dry-run", self.v.check_gate(str(self.gate_file(dry_run=True)), ns) or "")
        self.assertIn("prompts_sha256", self.v.check_gate(
            str(self.gate_file(prompts_sha256="0" * 64)), ns) or "")
        self.assertIn("not found", self.v.check_gate(str(self.root / "none.json"), ns) or "")

    def test_dry_run_runs_the_whole_pipeline_offline(self) -> None:
        saved = self.v.LiveDriver3.run

        def boom(self: Any, spec: Any) -> Any:
            raise AssertionError("the live driver must not run in a dry run")

        self.v.LiveDriver3.run = boom
        try:
            rc, out, err = self.main("--stage", "readiness", "--out", str(self.root / "o"),
                                     "--dry-run", "--instances", "1", "--workdir",
                                     str(self.root / "wd"))
        finally:
            self.v.LiveDriver3.run = saved
        self.assertEqual(rc, 0, err)
        self.assertIn("SIMULATION", out)
        gate = json.loads((self.root / "o" / "readiness_gate.json").read_text())
        self.assertTrue(gate["dry_run"])
        self.assertIn("dry-run", self.v.check_gate(str(self.root / "o" / "readiness_gate.json"),
                                                   self.ns("--stage", "main")) or "")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.an.main(["--runs", str(self.root / "o")]), 0)
        summary = json.loads((self.root / "o" / "summary.json").read_text())
        self.assertEqual(summary["arms"]["O"]["runs"], 1)

    def test_readiness_gate_decides_from_records(self) -> None:
        h = self.harness(workers=1)
        recs = h.run_instance(9101)
        sym = self.v.symmetry(self.root / "p")
        args = self.args(projection_cap=15.0)
        gate = self.v.readiness_gate(recs, h.spent, args, [("x", True)], sym)
        self.assertEqual(gate["sessions"], 19)
        self.assertFalse(gate["passed"])                       # fake $0.02/turn projects too high
        self.assertFalse([k for k, v in gate["checks"].items() if not v and "cost" not in k])
        cheap = self.harness(self.v.FakeDriver3(cost=0.001), workers=1)
        gate2 = self.v.readiness_gate(cheap.run_instance(9101), cheap.spent, args,
                                      [("x", True)], sym)
        self.assertTrue(gate2["passed"], gate2["checks"])
        bad = self.harness(self.v.FakeDriver3(cost=0.001, refuse_send=True), workers=1)
        gate3 = self.v.readiness_gate(bad.run_instance(9101), bad.spent, args, [("x", True)], sym)
        self.assertFalse(gate3["passed"])

    def test_ceiling_stop_rule_on_the_control_only(self) -> None:
        def recs(n2_ok: int) -> list[dict[str, Any]]:
            out = []
            for i in range(20):
                s = 4001 + i
                out += [{"seed": s, "arm": "P", "rep": 0, "b_ok": False, "sessions": []},
                        {"seed": s, "arm": "N2", "rep": 0, "b_ok": i < n2_ok, "sessions": []},
                        {"seed": s, "arm": "N0", "rep": 0, "b_ok": False, "sessions": []},
                        {"seed": s, "arm": "O", "rep": 0, "b_ok": True, "sessions": []}]
            return out

        done = list(range(4001, 4021))
        self.assertIn("gate 6", self.v.stop_reason(recs(20), done) or "")
        self.assertIn("gate 6", self.v.stop_reason(recs(19), done) or "")
        self.assertIsNone(self.v.stop_reason(recs(18), done))
        self.assertIsNone(self.v.stop_reason(recs(20), done[:10]))

    def test_freeze_lists_the_files_and_the_prompt_hash(self) -> None:
        text = self.v.freeze()
        for name in ("scripts/experiment_v3.py", "scripts/analyze_v3.py", "test_experiment_v3.py",
                     "scripts/experiment_v2.py", "prompts_hash()"):
            self.assertIn(name, text)

    def test_the_live_driver_is_not_used_by_any_test_path(self) -> None:
        text = Path(__file__).read_text(encoding="utf-8")
        self.assertEqual(text.count("LiveDriver3" + "()"), 0)


class Analysis(Base):
    def rec(self, seed: int, arm: str, ok: bool, **kw: Any) -> dict[str, Any]:
        sess = {"tag": "b4", "seconds": 10.0, "cost_usd": 0.01, "infra": kw.get("infra", ""),
                "usage": {"input_tokens": 10, "output_tokens": 5}}
        chars = {"P": {"agent-a": 307, "agent-b": 307}, "N2": {"agent-a": 308, "agent-b": 308}}
        r = {"seed": seed, "arm": arm, "rep": 0, "attempt": 1, "b_ok": ok, "a_ok": True,
             "completed": ok, "b_unit_ok": ok, "b_void_ok": ok, "b_wrong_default": not ok,
             "b_missing": False, "delivered": arm == "P", "picked_up": ok, "acknowledged": False,
             "missed": not ok, "generated": 1, "cost_usd": 0.05, "seconds": 30.0, "denials": 0,
             "channel_denials": 0, "host_refusals": [], "infra_failure": kw.get("infra", ""),
             "mailbox_mention": False, "first_look_prompt": 2 if ok else None,
             "b_ok_by_prompt": [False, ok, ok, ok],
             "paragraph_chars": chars.get(arm, {"agent-a": 0, "agent-b": 0}), "sessions": [sess]}
        r.update({k: v for k, v in kw.items() if k != "infra"})
        return r

    def data(self, n: int = 60, p_only: int = 0, n2_only: int = 0, both: int = 30,
             n2_extra_ok: int = 0) -> list[dict[str, Any]]:
        out = []
        for i in range(n):
            s = 4001 + i
            p_ok = i < p_only + both
            n2_ok = (p_only <= i < p_only + both + n2_only) and i >= p_only
            out += [self.rec(s, "P", p_ok), self.rec(s, "N2", n2_ok),
                    self.rec(s, "N0", False), self.rec(s, "O", True)]
        return out

    def test_ceiling_makes_the_contrast_uninformative_not_a_tie(self) -> None:
        s = self.an.summarise(self.data(p_only=0, n2_only=0, both=60))
        self.assertIn("uninformative", s["verdict"])
        self.assertNotIn("no advantage", s["verdict"])
        self.assertFalse(s["validity_gates"]["6_ceiling_N2_b_ok_below_95pct"]["pass"])
        s = self.an.summarise(self.data(p_only=0, n2_only=0, both=57))      # exactly 95%
        self.assertIn("uninformative", s["verdict"])

    def test_a_clear_advantage_below_the_ceiling(self) -> None:
        s = self.an.summarise(self.data(p_only=22, n2_only=0, both=14))
        self.assertTrue(s["verdict"].startswith("net P advantage"), s["verdict"])

    def test_against_postbox(self) -> None:
        recs = self.data(p_only=0, n2_only=12, both=30)
        s = self.an.summarise(recs)
        self.assertTrue(s["verdict"].startswith("AGAINST POSTBOX"), s["verdict"])

    def test_inconclusive_and_no_advantage_rows(self) -> None:
        s = self.an.summarise(self.data(p_only=10, n2_only=4, both=30))
        self.assertEqual(s["verdict"], "inconclusive at this sample size")
        s = self.an.summarise(self.data(p_only=0, n2_only=0, both=30))
        self.assertTrue(s["verdict"].startswith("no advantage"), s["verdict"])

    def test_a_failed_validity_gate_voids_the_contrast(self) -> None:
        recs = self.data(p_only=22, n2_only=0, both=14)
        for r in recs:
            if r["arm"] == "O":
                r["b_ok"] = False
        self.assertTrue(self.an.summarise(recs)["verdict"].startswith("void"))
        recs = self.data(p_only=22, n2_only=0, both=14)
        for r in recs:
            if r["arm"] == "N0":
                r["b_ok"] = True
        self.assertTrue(self.an.summarise(recs)["verdict"].startswith("void"))

    def test_incomplete_runs_make_no_inferential_claim(self) -> None:
        s = self.an.summarise(self.data(n=30, p_only=10, n2_only=0, both=10))
        self.assertTrue(s["verdict"].startswith("incomplete"))

    def test_refusals_are_reported_separately_with_cause(self) -> None:
        recs = self.data(p_only=0, n2_only=12, both=30)
        bad = [r for r in recs if r["arm"] == "P" and r["seed"] == 4001]
        bad[0]["channel_denials"] = 1
        bad[0]["b_ok"] = False
        bad[0]["host_refusals"] = [{"tool": "Bash", "tag": "a",
                                    "input": '{"command": "python3 tools/agent-postbox/agent_mail.py send"}'}]
        s = self.an.summarise(recs)
        ref = s["host_refusals"]["P"]
        self.assertEqual((ref["runs_with_refusal"], ref["of_which_b_not_ok"]), (1, 1))
        self.assertIn("agent_mail.py", ref["causes"][0]["refused"][0])
        self.assertEqual(s["host_refusals"]["N2"]["runs_with_refusal"], 0)

    def test_infrastructure_repeat_uses_the_last_attempt_and_lists_the_first(self) -> None:
        recs = self.data(p_only=22, n2_only=0, both=14)
        first = self.rec(4001, "P", False, infra="timeout")
        first["attempt"] = 1
        for r in recs:
            if r["seed"] == 4001 and r["arm"] == "P":
                r["attempt"] = 2
        recs.append(first)
        s = self.an.summarise(recs)
        self.assertIn("4001-P-t1", s["earlier_attempts"])

    def test_by_prompt_and_first_look_tallies(self) -> None:
        s = self.an.summarise(self.data(p_only=22, n2_only=0, both=14))
        self.assertEqual(s["b_ok_after_prompt_k_counts"]["O"], [0, 60, 60, 60])
        self.assertEqual(s["first_look_prompt"]["P"]["prompt_2"], 36)

    def test_freeze_prints_hashes(self) -> None:
        text = self.an.freeze()
        self.assertIn("analyze_v3.py", text)
        self.assertIn("analyze_v2.py (imported)", text)

    def test_cli_writes_summary_and_refuses_empty_input(self) -> None:
        runs = self.root / "r" / "runs"
        runs.mkdir(parents=True)
        for i, r in enumerate(self.data(n=2, both=2)):
            (runs / f"{i}.json").write_text(json.dumps(r))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.an.main(["--runs", str(self.root / "r")]), 0)
        self.assertTrue((self.root / "r" / "summary.json").is_file())
        (self.root / "e" / "runs").mkdir(parents=True)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.an.main(["--runs", str(self.root / "e")]), 2)


if __name__ == "__main__":
    unittest.main()
