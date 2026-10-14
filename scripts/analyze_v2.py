#!/usr/bin/env python3
"""Analysis for experiment v2 (docs/experiment-v2.md sections 6a, 7). Reads the per-run
records the harness wrote (RUNS/runs/*.json) and writes summary JSON. No model, no network.

    python scripts/analyze_v2.py --runs DIR [--out summary.json]
    python scripts/analyze_v2.py --freeze      # print this script's sha256 (and Wilson's)

Freeze it BEFORE the first measured run: commit the file, run `--freeze`, record the printed
hash in the pre-registration. The hash covers this file and the `wilson` function's file
(scripts/experiment_micro.py), which this script imports.

Statistics. Primary: P versus N2 on `b_ok`, paired by instance (seed): exact two-sided
McNemar (binomial on the discordant pairs, alpha 0.05) and a 95% interval for the paired
difference by Newcombe's method 10 (Wilson intervals combined with the paired correlation).
Per-arm Wilson 95% intervals. Cost per completed task = summed cost / runs with
`completed`. Secondary contrasts are descriptive and unadjusted; their number is stated.
The last attempt of each (seed, arm, rep) is the run; earlier attempts are listed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import experiment_micro as micro  # noqa: E402

ARMS = ("P", "N2", "N1", "N0", "O")
FULL_N = 60
MARGIN = 0.15
ALPHA = 0.05


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: b and c are the discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_difference(b: int, c: int, both: int, neither: int) -> tuple[float, float, float]:
    """(difference, low, high): Newcombe's method 10 for paired proportions. `b` = first arm
    right and second wrong, `c` = the reverse, `both` right, `neither` right."""
    n = b + c + both + neither
    if n == 0:
        return (0.0, -1.0, 1.0)
    p1, p2 = (both + b) / n, (both + c) / n
    l1, u1 = micro.wilson(both + b, n)
    l2, u2 = micro.wilson(both + c, n)
    a_ = (both + b) * (c + neither) * (both + c) * (b + neither)
    bc = both * neither - b * c
    corr = bc - n / 2 if bc > n / 2 else 0.0 if bc >= 0 else float(bc)
    phi = corr / math.sqrt(a_) if a_ > 0 else 0.0
    d = p1 - p2
    lo_t = (p1 - l1) ** 2 + (u2 - p2) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2)
    hi_t = (u1 - p1) ** 2 + (p2 - l2) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2)
    return (d, d - math.sqrt(max(0.0, lo_t)), d + math.sqrt(max(0.0, hi_t)))


def load_records(runs_dir: Path) -> list[dict[str, Any]]:
    recs = []
    for p in sorted((runs_dir / "runs").glob("*.json")):
        try:
            recs.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            raise SystemExit(f"unreadable record {p}") from None
    return recs


def split_attempts(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(final run of each seed/arm/rep, earlier attempts)."""
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    earlier: list[dict[str, Any]] = []
    for r in sorted(records, key=lambda r: r.get("attempt", 1)):
        key = (r["seed"], r["arm"], r.get("rep", 0))
        if key in best:
            earlier.append(best[key])
        best[key] = r
    return list(best.values()), earlier


def usable(r: dict[str, Any]) -> bool:
    return "aborted" not in r and not r.get("infra_failure") and "b_ok" in r


def contrast(recs: list[dict[str, Any]], x: str, y: str, metric: str = "b_ok") -> dict[str, Any]:
    """Paired by seed: instances where both arms have a usable run."""
    by = {a: {r["seed"]: r for r in recs if r["arm"] == a and r.get("rep", 0) == 0
              and usable(r)} for a in (x, y)}
    seeds = sorted(set(by[x]) & set(by[y]))
    b = sum(1 for s in seeds if by[x][s][metric] and not by[y][s][metric])
    c = sum(1 for s in seeds if by[y][s][metric] and not by[x][s][metric])
    both = sum(1 for s in seeds if by[x][s][metric] and by[y][s][metric])
    neither = len(seeds) - b - c - both
    d, lo, hi = paired_difference(b, c, both, neither)
    return {"x": x, "y": y, "metric": metric, "pairs": len(seeds), "x_only": b, "y_only": c,
            "both": both, "neither": neither, "difference": round(d, 4),
            "ci95": [round(lo, 4), round(hi, 4)], "mcnemar_exact_p": round(mcnemar_exact(b, c), 6)}


def tokens(rs: list[dict[str, Any]]) -> dict[str, int]:
    out = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
           "cache_read_input_tokens": 0}
    for r in rs:
        for s in r["sessions"]:
            for k in out:
                v = (s.get("usage") or {}).get(k)
                out[k] += v if isinstance(v, int) else 0
    return out


def arm_summary(recs: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    rs = [r for r in recs if r["arm"] == arm and "aborted" not in r]
    ok = [r for r in rs if usable(r)]
    n = len(ok)

    def count(key: str) -> int:
        return sum(1 for r in ok if r.get(key))

    lo, hi = micro.wilson(count("b_ok"), n)
    cost = sum(r["cost_usd"] for r in rs)
    done = count("completed")
    sessions = [s for r in rs for s in r["sessions"]]
    s4 = [r["s4"] for r in ok if r.get("s4", {}).get("ran")]
    return {
        "runs": len(rs), "usable": n, "b_ok": count("b_ok"),
        "b_ok_wilson95": [round(lo, 3), round(hi, 3)], "a_ok": count("a_ok"),
        "completed": done, "b_unit_ok": count("b_unit_ok"), "b_void_ok": count("b_void_ok"),
        "b_wrong_default": count("b_wrong_default"), "b_missing": count("b_missing"),
        "delivered": count("delivered"), "picked_up": count("picked_up"),
        "acknowledged": count("acknowledged"), "missed": count("missed"),
        "generated": sum(r.get("generated", 0) for r in ok),
        "cost_usd": round(cost, 4),
        "cost_per_completed_usd": round(cost / done, 4) if done else None,
        "seconds_per_run": round(sum(r["seconds"] for r in rs) / len(rs), 1) if rs else None,
        "seconds_per_session": (round(sum(s["seconds"] for s in sessions) / len(sessions), 1)
                                if sessions else None),
        "denials_per_run": round(sum(r["denials"] for r in rs) / len(rs), 2) if rs else None,
        "s4_ran": len(s4), "s4_recovered": sum(1 for x in s4 if x["b_ok_after"]),
        "s4_cost_usd": round(sum(x["cost_usd"] or 0.0 for x in s4), 4),
        "manual_nudges": 0, "tokens": tokens(rs),
        "conflicting_operations": "not exercised (disjoint files)"}


def gates(recs: list[dict[str, Any]]) -> dict[str, Any]:
    by = {a: [r for r in recs if r["arm"] == a and usable(r)] for a in ARMS}
    sess = [s for r in recs if "aborted" not in r for s in r["sessions"]]
    infra = [s for s in sess if s.get("infra")]
    leaks = [f"{r['seed']}-{r['arm']}" for r in recs if r.get("mailbox_mention")]
    pc: dict[str, dict[str, int]] = {
        a: next((r["paragraph_chars"] for r in recs if r["arm"] == a), {}) for a in ("P", "N2")}
    pp, pn = pc["P"], pc["N2"]
    within = bool(pp and pn) and all(
        abs(pp[w] - pn[w]) <= 0.10 * max(pp[w], pn[w]) for w in pp)

    def rate(rs: list[dict[str, Any]]) -> float | None:
        return sum(1 for r in rs if r["b_ok"]) / len(rs) if rs else None

    o, n0 = rate(by["O"]), rate(by["N0"])
    return {
        "1_O_b_ok_at_least_80pct": {"value": o, "pass": o is not None and o >= 0.80},
        "2_N0_b_ok_at_most_15pct": {"value": n0, "pass": n0 is not None and n0 <= 0.15},
        "3_no_mailbox_mention_in_non_P_runs": {"listed": leaks, "pass": len(leaks) <= 2},
        "4_P_N2_paragraphs_within_10pct": {"chars": pc, "pass": within},
        "5_sessions_free_of_infrastructure_failure_90pct": {
            "failed": len(infra), "sessions": len(sess),
            "pass": bool(sess) and len(infra) <= 0.10 * len(sess)}}


def verdict(primary: dict[str, Any], gate: dict[str, Any], full_n: int = FULL_N) -> str:
    if not all(g["pass"] for g in gate.values()):
        return "void: a validity gate failed; reported as a failed experiment, not a result"
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


def summarise(records: list[dict[str, Any]], full_n: int = FULL_N) -> dict[str, Any]:
    recs, earlier = split_attempts(records)
    primary = contrast(recs, "P", "N2")
    gate = gates(recs)
    secondary = [contrast(recs, "P", "N1"), contrast(recs, "P", "N0"),
                 contrast(recs, "P", "N2", "b_unit_ok"), contrast(recs, "P", "N2", "b_void_ok")]
    arms = {a: arm_summary(recs, a) for a in ARMS}
    notes: list[str] = []
    n1c, pc = arms["N1"]["cost_per_completed_usd"], arms["P"]["cost_per_completed_usd"]
    n1 = secondary[0]
    if n1["mcnemar_exact_p"] < ALPHA and n1["difference"] < 0 and n1c and pc and pc >= 1.5 * n1c:
        notes.append("P is worse than N1 by exact test and costs at least 1.5x per completed "
                     "task: a finding against the current adapter design")
    return {"schema": 1, "runs": len(recs), "earlier_attempts": [
                f"{r['seed']}-{r['arm']}-t{r.get('attempt', 1)}" for r in earlier],
            "arms": arms, "validity_gates": gate, "primary_P_vs_N2": primary,
            "verdict": verdict(primary, gate, full_n),
            "secondary_descriptive": {"contrasts_stated": len(secondary), "adjusted": False,
                                      "items": secondary},
            "notes": notes}


def freeze() -> str:
    here = Path(__file__).resolve()
    return (f"{sha256_of(here)}  {here.name}\n"
            f"{sha256_of(Path(micro.__file__).resolve())}  {Path(micro.__file__).name} (wilson)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analysis for experiment v2")
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
    recs = load_records(Path(args.runs))
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
