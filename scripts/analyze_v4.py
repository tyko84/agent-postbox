#!/usr/bin/env python3
"""Descriptive summary of an experiment v4 PILOT (docs/experiment-v4.md), from the run records in
OUT/runs written by scripts/experiment_v4.py.

    python scripts/analyze_v4.py OUT [--json]

Prints counts per scenario and arm, the validity gates (design section 5) and the voided and
infrastructure-failed runs. It makes NO significance claim and no hypothesis test: six instances
cannot support one. A gate failure is printed first. Nothing is read from model text. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_v4 as x4  # noqa: E402

A, B = x4.A, x4.B


def load_records(out: Path) -> list[dict[str, Any]]:
    recs = []
    for p in sorted((out / "runs").glob("*.json")):
        try:
            recs.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return recs


def final_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last attempt of each (scenario, arm, seed); earlier attempts stay on disk."""
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in records:
        if "tag" in r:
            best[(r["scenario"], r["arm"], r["seed"])] = r
    return list(best.values())


def usable(r: dict[str, Any]) -> bool:
    return "aborted" not in r and not r.get("infra_failure") and not r.get("void")


def rate(k: int, n: int) -> str:
    return f"{k}/{n}" if n else "0/0"


def arm_table(recs: list[dict[str, Any]], scenario: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for arm in x4.ARMS:
        rs = [r for r in recs if r["scenario"] == scenario and r["arm"] == arm
              and "aborted" not in r]
        u = [r for r in rs if usable(r)]
        ttr = [r["time_to_recovery_s"] for r in u if r.get("time_to_recovery_s") is not None]
        out[arm] = {
            "runs": len(rs), "usable": len(u),
            "void": [r["tag"] for r in rs if r.get("void")],
            "infra": [r["tag"] for r in rs if r.get("infra_failure")],
            "lost_update": sum(1 for r in u if r.get("lost_update")),
            "duplicate_work": sum(1 for r in u if r.get("duplicate_work")),
            "collision": sum(1 for r in u if r.get("collision")),
            "final_ok": sum(1 for r in u if r.get("final_ok")),
            "recovered": sum(1 for r in u if r.get("recovered")),
            "median_time_to_recovery_s": statistics.median(ttr) if ttr else None,
            "rejected_edits": sum(sum(r.get("rejected_edits", {}).values()) for r in u),
            "manual_interventions": sum(r.get("manual_nudges", 0) for r in rs),
            "channel_denials": sum(r.get("channel_denials", 0) for r in rs),
            "notional_cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in rs), 4),
            "median_wall_s": statistics.median([r["wall_s"] for r in u]) if u else None,
            "turns_total": sum(s["turns"] or 0 for r in rs for s in r.get("sessions", []))}
    return out


def gates(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """Validity gates (amended 2026-10-15): scenario 2 is the primary scenario. The old gate 1 (N0
    must collide in 30% of scenario-1 instances) is no longer a validity gate; it is reported as an
    OBSERVATION (`observation_scenario_1`) whose result is a finding about the host."""
    s1 = [r for r in recs if r["scenario"] == "S1" and "aborted" not in r]
    n0 = [r for r in s1 if r["arm"] == "N0" and usable(r)]
    n0_coll = sum(1 for r in n0 if r.get("collision"))
    o = [r for r in recs if r["arm"] == "O" and "aborted" not in r and usable(r)]
    o_ok = sum(1 for r in o if r.get("final_ok"))
    operable = {}
    for arm in ("S", "L", "P"):
        runs = [r for r in recs if r["arm"] == arm and "aborted" not in r]
        done = [r for r in runs if not r.get("infra_failure") and not r.get("void")
                and all(s["result_seen"] or s.get("killed_by_harness") for s in r["sessions"])
                and len(r["sessions"]) == 2]
        refused = sum(r.get("channel_denials", 0) for r in runs)
        operable[arm] = {"completed_runs": len(done), "channel_refusals": refused,
                         "pass": bool(done) and refused == 0}
    sessions = [s for r in recs if "aborted" not in r for s in r.get("sessions", [])
                if not s.get("killed_by_harness")]
    good = [s for s in sessions if not s.get("infra")]
    s2 = [r for r in recs if r["scenario"] == "S2" and r["arm"] != "O" and "aborted" not in r]
    voided = [r["tag"] for r in s2 if r.get("void")]
    return {
        "2_oracle_solvable": {"o_final_ok": rate(o_ok, len(o)),
                              "pass": bool(o) and o_ok / len(o) >= x4.ORACLE_GATE},
        "3_arms_operable": operable | {"pass": all(v["pass"] for v in operable.values())},
        "4_prompts_equal_within_10pct": {"chars": x4.paragraph_chars(),
                                         "pass": x4.lengths_within()},
        "5_usable_sessions": {"usable": rate(len(good), len(sessions)),
                              "pass": bool(sessions) and len(good) / len(sessions) >= x4.USABLE_GATE},
        "6_kill_after_claim": {"scenario2_runs": len(s2), "void": voided,
                               "verified": sum(1 for r in s2 if r.get("kill", {}).get("verified")),
                               "pass": bool(s2) and all(
                                   r.get("kill", {}).get("verified") or r.get("void") for r in s2)},
        "observation_scenario_1": {
            "n0_collisions": rate(n0_coll, len(n0)),
            "n0_rejected_edits": sum(sum(r.get("rejected_edits", {}).values()) for r in n0),
            "reading": ("the host already prevented lost updates and duplicates in these N0 runs"
                        if n0 and n0_coll == 0 else
                        "N0 collided in some runs" if n0 else "no usable N0 runs"),
            "pass": True}}


def summary(out: Path) -> dict[str, Any]:
    allrecs = load_records(out)
    recs = final_records(allrecs)
    return {"runs_on_disk": len(allrecs), "final_runs": len(recs),
            "simulation": any(r.get("simulation") for r in recs),
            "aborted": [r for r in allrecs if "aborted" in r],
            "gates": gates(recs),
            "survivors": {r["tag"]: {**r.get("survivor", {}), "final_ok": r.get("final_ok"),
                                     "ttr_s": r.get("time_to_recovery_s")}
                          for r in recs if r.get("survivor")},
            "scenario_1": arm_table(recs, "S1"), "scenario_2": arm_table(recs, "S2")}


def show(s: dict[str, Any]) -> None:
    if s["simulation"]:
        print("SIMULATION data (fake agents): plumbing only, no behaviour is observed\n")
    g = s["gates"]
    failed = [k for k, v in g.items() if not v.get("pass")]
    print("GATES" + (f"  FAILED: {', '.join(failed)}" if failed else "  all pass"))
    for k, v in g.items():
        print(f"  {'PASS' if v.get('pass') else 'FAIL'}  {k}: "
              + json.dumps({a: b for a, b in v.items() if a != "pass"}, default=str))
    for key, title in (("scenario_2", "SCENARIO 2 (interrupted holder) - PRIMARY"),
                       ("scenario_1", "SCENARIO 1 (contested scope) - OBSERVATION: does the host "
                                      "already prevent lost updates?")):
        print(f"\n{title}")
        print(f"  {'arm':<4} {'runs':>4} {'usable':>6} {'lost':>5} {'dup':>4} {'coll':>5} "
              f"{'ok':>4} {'recov':>5} {'ttr_s':>7} {'rej':>4} {'nudge':>5} {'deny':>4} "
              f"{'usd':>7} {'turns':>5}")
        for arm, t in s[key].items():
            ttr = "-" if t["median_time_to_recovery_s"] is None else f"{t['median_time_to_recovery_s']:.0f}"
            print(f"  {arm:<4} {t['runs']:>4} {t['usable']:>6} {t['lost_update']:>5} "
                  f"{t['duplicate_work']:>4} {t['collision']:>5} {t['final_ok']:>4} "
                  f"{t['recovered']:>5} {ttr:>7} {t['rejected_edits']:>4} "
                  f"{t['manual_interventions']:>5} {t['channel_denials']:>4} "
                  f"{t['notional_cost_usd']:>7.3f} {t['turns_total']:>5}")
            if t["void"]:
                print(f"       void: {t['void']}")
            if t["infra"]:
                print(f"       infrastructure failure: {t['infra']}")
    if s.get("survivors"):
        print("\nSURVIVOR BEHAVIOUR (scenario 2, descriptive)")
        for tag, sv in s["survivors"].items():
            print(f"  {tag}: " + json.dumps(sv, default=str))
    if s["aborted"]:
        print(f"\nABORTED (not results): {[a.get('aborted') for a in s['aborted']]}")
    print("\nDescriptive counts only. No test and no significance claim; see docs/experiment-v4.md "
          "for what each result is called.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Experiment v4 pilot summary (descriptive)")
    ap.add_argument("out", help="the run directory written by experiment_v4.py")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if not (out / "runs").is_dir():
        print(f"no runs/ in {out}", file=sys.stderr)
        return 2
    s = summary(out)
    print(json.dumps(s, indent=2, sort_keys=True, default=str)) if args.json else show(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
