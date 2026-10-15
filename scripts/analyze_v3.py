#!/usr/bin/env python3
"""Analysis for experiment v3 (docs/experiment-v3.md sections 7 and 8). Reads the per-run
records the harness wrote (RUNS/runs/*.json) and writes summary JSON. No model, no network.

    python scripts/analyze_v3.py --runs DIR [--out summary.json]
    python scripts/analyze_v3.py --freeze      # print this script's sha256 (and its imports')

Statistics are scripts/analyze_v2.py's (imported, not copied): exact two-sided McNemar on the
discordant pairs, Newcombe method 10 for the paired difference, Wilson per-arm intervals, cost
per completed task. Primary: P versus N2 on `b_ok` after prompt 4, paired by seed. What differs
from v2: four arms (no N1), validity gate 6 (the ceiling gate: N2 at or above 95% makes the
contrast UNINFORMATIVE, which is reported as that and never as a tie), `b_ok` by prompt as a
secondary, and a host-refusal report (refusals of the channel command are counted as failures of
that arm by the outcome AND listed here with their cause).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_v2 as a2  # noqa: E402
import experiment_micro as micro  # noqa: E402

ARMS = ("P", "N2", "N0", "O")
FULL_N = 60
MARGIN = a2.MARGIN
ALPHA = a2.ALPHA
CEILING_GATE = 0.95
B_PROMPTS = 4


def gates(recs: list[dict[str, Any]]) -> dict[str, Any]:
    by = {a: [r for r in recs if r["arm"] == a and a2.usable(r)] for a in ARMS}
    sess = [s for r in recs if "aborted" not in r for s in r["sessions"]]
    infra = [s for s in sess if s.get("infra")]
    leaks = [f"{r['seed']}-{r['arm']}" for r in recs if r.get("mailbox_mention")]
    pc: dict[str, Any] = {a: next((r["paragraph_chars"] for r in recs if r["arm"] == a), {}) for a in ("P", "N2")}
    pp, pn = pc["P"], pc["N2"]
    within = bool(pp and pn) and all(abs(pp[w] - pn[w]) <= 0.10 * max(pp[w], pn[w]) for w in pp)

    def rate(rs: list[dict[str, Any]]) -> float | None:
        return sum(1 for r in rs if r["b_ok"]) / len(rs) if rs else None

    o, n0, n2 = rate(by["O"]), rate(by["N0"]), rate(by["N2"])
    return {
        "1_O_b_ok_at_least_80pct": {"value": o, "pass": o is not None and o >= 0.80},
        "2_N0_b_ok_at_most_15pct": {"value": n0, "pass": n0 is not None and n0 <= 0.15},
        "3_no_mailbox_mention_in_non_P_runs": {"listed": leaks, "pass": len(leaks) <= 2},
        "4_P_N2_paragraphs_within_10pct": {"chars": pc, "pass": within},
        "5_sessions_free_of_infrastructure_failure_90pct": {
            "failed": len(infra), "sessions": len(sess),
            "pass": bool(sess) and len(infra) <= 0.10 * len(sess)},
        "6_ceiling_N2_b_ok_below_95pct": {"value": n2, "pass": n2 is not None and n2 < CEILING_GATE}}


def verdict(primary: dict[str, Any], gate: dict[str, Any], full_n: int = FULL_N) -> str:
    validity = {k: v for k, v in gate.items() if not k.startswith("6_")}
    if not all(g["pass"] for g in validity.values()):
        return "void: a validity gate failed; reported as a failed experiment, not a result"
    if not gate["6_ceiling_N2_b_ok_below_95pct"]["pass"]:
        return ("uninformative: the control arm (N2) is at or above 95%, so the notes file leaves "
                "no room for Postbox to show an advantage; this is not a tie")
    if primary["pairs"] < full_n:
        return (f"incomplete: {primary['pairs']} pairs of {full_n}; counts and intervals only, "
                "no inferential claim")
    d, (lo, hi), p = primary["difference"], primary["ci95"], primary["mcnemar_exact_p"]
    if p < ALPHA and d > 0 and lo > 0:
        return ("net P advantage: on this task, with this model and harness, Postbox's automatic "
                "pickup was associated with higher completion than an unprompted notes file")
    if p < ALPHA and d < 0:
        return "AGAINST POSTBOX on this task (P minus N2 negative, p < 0.05)"
    if hi < MARGIN:
        return "no advantage of 15 points or more on this task"
    return "inconclusive at this sample size"


def refusals(recs: list[dict[str, Any]]) -> dict[str, Any]:
    """Host refusals of a channel command (mail command or notes file), per arm, with cause.
    They are already failures of that arm in `b_ok`; this lists them separately."""
    out: dict[str, Any] = {}
    for arm in ("P", "N2"):
        rs = [r for r in recs if r["arm"] == arm and "aborted" not in r]
        hit = [r for r in rs if r.get("channel_denials", 0)]
        out[arm] = {
            "runs": len(rs), "runs_with_refusal": len(hit),
            "of_which_b_not_ok": sum(1 for r in hit if not r.get("b_ok")),
            "causes": [{"seed": r["seed"], "attempt": r.get("attempt", 1),
                        "refused": [h["input"][:200] for h in r.get("host_refusals", [])]}
                       for r in hit]}
    return out


def by_prompt(recs: list[dict[str, Any]]) -> dict[str, list[int | None]]:
    """b_ok after prompt k, per arm (secondary): count of usable runs where B was right."""
    out: dict[str, list[int | None]] = {}
    for arm in ARMS:
        rs = [r for r in recs if r["arm"] == arm and a2.usable(r)]
        out[arm] = [sum(1 for r in rs if len(r.get("b_ok_by_prompt", [])) > k
                        and r["b_ok_by_prompt"][k]) for k in range(B_PROMPTS)]
    return out


def first_look(recs: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for arm in ("P", "N2"):
        rs = [r for r in recs if r["arm"] == arm and a2.usable(r)]
        tally = {"prompt_2": 0, "prompt_3": 0, "prompt_4": 0, "never": 0}
        for r in rs:
            k = r.get("first_look_prompt")
            tally[f"prompt_{k}" if k in (2, 3, 4) else "never"] += 1
        out[arm] = tally
    return out


def summarise(records: list[dict[str, Any]], full_n: int = FULL_N) -> dict[str, Any]:
    recs, earlier = a2.split_attempts(records)
    primary = a2.contrast(recs, "P", "N2")
    gate = gates(recs)
    secondary = [a2.contrast(recs, "P", "N0"), a2.contrast(recs, "P", "N2", "b_unit_ok"),
                 a2.contrast(recs, "P", "N2", "b_void_ok")]
    arms = {a: a2.arm_summary(recs, a) for a in ARMS}
    return {"schema": 3, "runs": len(recs), "earlier_attempts": [
                f"{r['seed']}-{r['arm']}-t{r.get('attempt', 1)}" for r in earlier],
            "arms": arms, "validity_gates": gate, "primary_P_vs_N2": primary,
            "verdict": verdict(primary, gate, full_n),
            "b_ok_after_prompt_k_counts": by_prompt(recs),
            "first_look_prompt": first_look(recs),
            "host_refusals": refusals(recs),
            "secondary_descriptive": {"contrasts_stated": len(secondary), "adjusted": False,
                                      "items": secondary},
            "manual_nudges": 0}


def freeze() -> str:
    here = Path(__file__).resolve()
    deps = [Path(a2.__file__).resolve(), Path(micro.__file__).resolve()]
    return "\n".join([f"{a2.sha256_of(here)}  {here.name}"]
                     + [f"{a2.sha256_of(d)}  {d.name} (imported)" for d in deps])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analysis for experiment v3")
    ap.add_argument("--runs", help="the harness output directory (holds runs/*.json)")
    ap.add_argument("--out", help="summary JSON path (default RUNS/summary.json)")
    ap.add_argument("--freeze", action="store_true", help="print this script's sha256 and exit")
    ap.add_argument("--full-n", type=int, default=FULL_N)
    args = ap.parse_args(argv)
    if args.freeze:
        print(freeze())
        return 0
    if not args.runs:
        ap.error("--runs is required (or use --freeze)")
    recs = a2.load_records(Path(args.runs))
    if not recs:
        print(f"no records under {args.runs}/runs", file=sys.stderr)
        return 2
    summary = summarise(recs, args.full_n)
    out = Path(args.out) if args.out else Path(args.runs) / "summary.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"verdict": summary["verdict"], "primary": summary["primary_P_vs_N2"]},
                     indent=2, sort_keys=True))
    print(f"summary written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
