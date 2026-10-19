#!/usr/bin/env python3
"""Experiment v4 PILOT harness (docs/experiment-v4.md): do exclusive claims, expiry and recovery
change what two concurrent agents do to one shared working tree? Scenario 1 (contested scope) and
scenario 2 (interrupted holder) only; audit reconstruction is out of the pilot (the artifacts it
would need are kept). Reuses scripts/e2e_claude.py (environment, event parsing, sign-in check) and
scripts/experiment_v2.py (hidden-suite runner, session record) without modifying them.

    python scripts/experiment_v4.py --stage readiness --out DIR --dry-run
    POSTBOX_EXPERIMENT_LIVE=1 python scripts/experiment_v4.py --stage readiness --out DIR --live
    POSTBOX_EXPERIMENT_LIVE=1 python scripts/experiment_v4.py --stage pilot --out DIR --live \\
        --gate READINESS_OUT/readiness_gate.json        # the integrator's decision, not the author's
    python scripts/experiment_v4.py --freeze            # sha256 table for the pre-registration

One *run* is one (scenario, arm, seed). The seeded project is a small `ledger/rates.py` whose
`quote()` both tickets must change (and a shared prerequisite both may be tempted to write). Two
agents are separate concurrent `claude -p` processes in ONE shared working tree. Arms: N0 (no
channel), S (soft notes file), L (atomic `mkdir` lock, no expiry), P (Postbox CLAIM with --scope
and --expires), O (sequential oracle). In scenario 2 the harness SIGKILLs the holder shortly after
its claim/lock/note exists; the survivor is launched when that artifact is seen and has a bounded
patience. Outcomes come from ARTIFACTS only: a snapshot log of the tree, taken by polling while the
agents run, is matched to each agent's tool calls (the per-agent edit log) and scored with hidden
tests; nothing is read from model text.

NEVER A LIVE CALL BY ACCIDENT. Exactly one of --dry-run / --live is required; --live also needs
POSTBOX_EXPERIMENT_LIVE=1; the pilot also needs a passing live readiness gate with the same prompts,
model and effort; --out must be outside the repository. Live use is the owner's existing
subscription through headless `claude -p`; no API key and no other service is used. Hard stops: a
session cap (readiness 12, pilot 132), a wall-time cap, a notional-cost ceiling, two rate-limit
rejections in a row. Exit: 0 finished; 2 refused / usage error. POSIX only. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import platform
import queue
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_claude as e2e  # noqa: E402
import experiment_v2 as v2  # noqa: E402

# A hook, `rebase --exec` or `bisect run` exports GIT_DIR and friends; inherited, they would
# point this script's temporary repositories at the real one. Test scripts ignore them.
for _v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX", "GIT_NAMESPACE",
           "GIT_CEILING_DIRECTORIES"):
    os.environ.pop(_v, None)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = 4
ARMS = ("N0", "S", "L", "P", "O")
SCENARIOS = ("S1", "S2")
STAGES = ("readiness", "pilot")
A, B = v2.A, v2.B
LIVE_ENV = v2.LIVE_ENV
DEST = e2e.DEST
SCOPE = "ledger/rates.py"              # what is contested
LOCK_REL = "locks/ledger-rates"        # the L arm's lock directory
NOTES_REL = "NOTES.md"                 # the S arm's notes file
SEEDS = {"readiness": [9221, 9222, 9223, 9224, 9225],
         "pilot": list(range(5001, 5007))}
# Readiness is a fixed list of runs (scenario, arm, seed): 6 runs x 2 sessions = the 12-session cap.
# Readiness recheck 3 (2026-10-15): scenario 2 once per arm; 5 runs x 2 sessions = the 10-session cap.
READINESS_PLAN = (("S2", "P", 9221), ("S2", "N0", 9222), ("S2", "L", 9223), ("S2", "S", 9224),
                  ("S2", "O", 9225))
# Pilot (amended): scenario 2 x 5 arms x 6 instances is primary; scenario 1 is an observation
# (N0 on three instances, S, L and P once). Planned sessions: 60 + 12 = 72.
OBSERVATION_PLAN = (("S1", "N0", 5001), ("S1", "N0", 5002), ("S1", "N0", 5003), ("S1", "S", 5001),
                    ("S1", "L", 5001), ("S1", "P", 5001))
SESSION_CAP = {"readiness": 10, "pilot": 80}       # pilot: 72 planned + about 10% repeats
WALL_HOURS = {"readiness": 1.5, "pilot": 4.0}
NOTIONAL_USD = {"readiness": 3.0, "pilot": 15.0}   # notional subscription accounting, not billing
MAX_WORKERS = 3
MAX_TURNS = 60
DEFAULT_EFFORT = "medium"
COLLISION_GATE = 0.30                  # validity gate 1
ORACLE_GATE = 0.80                     # validity gate 2
USABLE_GATE = 0.90                     # validity gate 5


@dataclass(frozen=True)
class Timing:
    """Every wait in one place, so the offline tests can run the same code in a fraction of a second."""
    poll_s: float = 0.25
    kill_delay_s: float = 1.5          # SIGKILL this long after the claim artifact is first seen
    lease_s2_s: int = 90               # scenario 2: the holder's Postbox lease, from its launch
    lease_s1_s: int = 600              # scenario 1: both agents, from launch
    patience_s: float = 240.0          # scenario 2: how long the survivor may wait (told in its prompt)
    grace_s: float = 60.0              # then the harness stops it
    s1_timeout_s: float = 420.0
    holder_timeout_s: float = 240.0    # scenario 2: the holder must claim within this
    pause_s: float = 30.0              # what `python3 tools/pause.py 30` sleeps (documented in prompts)


LIVE_TIMING = Timing()
FAST_TIMING = Timing(poll_s=0.02, kill_delay_s=0.15, lease_s2_s=3, lease_s1_s=60, patience_s=6.0,
                     grace_s=2.0, s1_timeout_s=30.0, holder_timeout_s=15.0, pause_s=1.0)


# ---- the seeded project ------------------------------------------------------------------------
RATES = {"USD": 1000, "EUR": 920, "GBP": 790, "CHF": 880}


@dataclass
class Inst4:
    seed: int
    fee_ccy: str
    fee_pct: int
    fee_min: int
    bulk: int
    disc_pct: int
    hidden: str = ""

    def tickets(self) -> str:
        return TICKETS.format(fee_ccy=self.fee_ccy, fee_pct=self.fee_pct, fee_min=self.fee_min,
                              bulk=self.bulk, disc_pct=self.disc_pct)


def div_round(n: int, d: int) -> int:
    return (2 * n + d) // (2 * d)


def ref_quote(inst: Inst4, amount: int, currency: str) -> int:
    """The reference semantics of the two tickets (discount first, then fee)."""
    c = currency.strip().upper()
    if c not in RATES:
        raise ValueError(currency)
    value = div_round(amount * RATES[c], 1000)
    if amount >= inst.bulk:
        value -= div_round(value * inst.disc_pct, 100)
    if c == inst.fee_ccy:
        value += max(inst.fee_min, div_round(value * inst.fee_pct, 100))
    return value


TICKETS = (
    "# Tickets\n\n"
    "Two agents share this working tree. Both tickets change `quote()` in `ledger/rates.py`, and\n"
    "both are needed in the final result. Put your own tests in `tests/test_<your name>.py`.\n\n"
    "## Prerequisite (both tickets need it)\n"
    "`normalise_currency(code)` in `ledger/rates.py`: strip whitespace, upper-case, and raise\n"
    "`ValueError` for a code that is not in `RATES`. `quote()` must use it for its `currency`\n"
    "argument. If it is not there yet, add it.\n\n"
    "## Ticket A (agent-a): handling fee\n"
    "For quotes into {fee_ccy}, `quote()` adds a fee to the converted value: {fee_pct}% of it\n"
    "(rounded half up to a whole minor unit), but at least {fee_min} minor units.\n\n"
    "## Ticket B (agent-b): bulk discount\n"
    "When `amount >= {bulk}`, the converted value is reduced by {disc_pct}% (rounded half up to a\n"
    "whole minor unit).\n\n"
    "## Order\n"
    "The discount applies first, then the fee (a fee is computed on the discounted value).\n")
README = ("# ledger\n\nSmall toolkit for currency quotes. `ledger/rates.py` holds the rate table and "
          "`quote()`. See TICKETS.md. Run the tests with `python3 -m unittest discover tests`.\n")
VISIBLE_TEST = ('import unittest\n\nfrom ledger.rates import quote\n\n\n'
                'class TestBase(unittest.TestCase):\n'
                '    def test_usd(self) -> None:\n'
                '        self.assertEqual(quote(10000, "USD"), 10000)\n\n'
                '    def test_eur(self) -> None:\n'
                '        self.assertEqual(quote(1000, "EUR"), 920)\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n')
PAUSE_PY = ('"""Wait for a number of seconds (at most 60): python3 tools/pause.py 30"""\n'
            'import sys\nimport time\n\n'
            'n = min(60.0, max(0.0, float(sys.argv[1]))) if len(sys.argv) > 1 else 30.0\n'
            'time.sleep(n)\nprint(f"waited {n:g} seconds")\n')
FEATURES = ("prereq", "fee", "discount")


def rates_text(inst: Inst4, features: frozenset[str] | set[str] = frozenset()) -> str:
    """ledger/rates.py with a subset of {prereq, fee, discount}: the baseline is the empty set;
    the reference solution is all three. Also what a SIMULATED agent writes."""
    out = ['"""Currency quotes for the ledger toolkit."""', "",
           "RATES = {" + ", ".join(f'"{k}": {v}' for k, v in RATES.items()) + "}", "", "",
           "def _div_round(n: int, d: int) -> int:",
           '    """n / d rounded half up (n and d are positive integers)."""',
           "    return (2 * n + d) // (2 * d)", "", ""]
    if "prereq" in features:
        out += ["def normalise_currency(code: str) -> str:",
                '    """Strip, upper-case; ValueError for an unknown code."""',
                "    c = code.strip().upper()", "    if c not in RATES:",
                "        raise ValueError(f\"unknown currency: {code!r}\")", "    return c", "", ""]
    out += ["def quote(amount: int, currency: str) -> int:",
            '    """Convert `amount` (USD minor units) to `currency` minor units, rounded half up."""']
    if not features:
        out += ["    return _div_round(amount * RATES[currency], 1000)"]
    else:
        cur = "normalise_currency(currency)" if "prereq" in features else "currency"
        out += [f"    ccy = {cur}", "    value = _div_round(amount * RATES[ccy], 1000)"]
        if "discount" in features:
            out += [f"    if amount >= {inst.bulk}:",
                    f"        value -= _div_round(value * {inst.disc_pct}, 100)"]
        if "fee" in features:
            out += [f'    if ccy == "{inst.fee_ccy}":',
                    f"        value += max({inst.fee_min}, _div_round(value * {inst.fee_pct}, 100))"]
        out += ["    return value"]
    return "\n".join(out) + "\n"


HIDDEN = (
    'import unittest\n\nfrom ledger import rates\n\n'
    'BASE = {base!r}\nFEE = {fee!r}\nDISCOUNT = {discount!r}\nCOMBINED = {combined!r}\n\n\n'
    'class T(unittest.TestCase):\n'
    '    def check(self, cases: list) -> None:\n'
    '        for amount, ccy, want in cases:\n'
    '            self.assertEqual(rates.quote(amount, ccy), want, (amount, ccy))\n\n'
    '    def test_base(self) -> None:\n        self.check(BASE)\n\n'
    '    def test_prereq(self) -> None:\n'
    '        self.assertEqual(rates.normalise_currency(" eur "), "EUR")\n'
    '        with self.assertRaises(ValueError):\n            rates.normalise_currency("XXX")\n'
    '        with self.assertRaises(ValueError):\n            rates.quote(1000, "xxx")\n'
    '        self.assertEqual(rates.quote(10000, " usd "), 10000)\n\n'
    '    def test_fee(self) -> None:\n        self.check(FEE)\n\n'
    '    def test_discount(self) -> None:\n        self.check(DISCOUNT)\n\n'
    '    def test_combined(self) -> None:\n        self.check(COMBINED)\n\n\n'
    'if __name__ == "__main__":\n    unittest.main()\n')
TESTS = ("test_base", "test_prereq", "test_fee", "test_discount", "test_combined")
TICKET_TEST = {A: "test_fee", B: "test_discount"}


def build_inst(seed: int) -> Inst4:
    r = random.Random(f"v4:{seed}")
    inst = Inst4(seed=seed, fee_ccy=r.choice(("EUR", "GBP", "CHF")), fee_pct=r.choice((2, 3, 4, 5)),
                 fee_min=r.choice((30, 50, 75)), bulk=r.choice((50_000, 80_000, 120_000)),
                 disc_pct=r.choice((5, 8, 10, 12)))
    plain = [c for c in RATES if c != inst.fee_ccy]
    small = [(10_000, "USD"), (r.randrange(2_000, 9_000), r.choice(plain))]
    # a fee case where the percentage beats the minimum, and one where the minimum wins
    big_amount = (2 * inst.fee_min * 100 // inst.fee_pct) * 1000 // RATES[inst.fee_ccy] + 500
    fee = [(big_amount, inst.fee_ccy), (1_000, inst.fee_ccy), (r.randrange(3_000, 9_000), inst.fee_ccy)]
    disc = [(inst.bulk, "USD"), (inst.bulk + r.randrange(1_000, 40_000), r.choice(plain)),
            (inst.bulk - 1, "USD")]
    comb = [(inst.bulk, inst.fee_ccy), (inst.bulk + r.randrange(1_000, 40_000), inst.fee_ccy)]
    exp = lambda cases: [(a, c, ref_quote(inst, a, c)) for a, c in cases]   # noqa: E731
    inst.hidden = HIDDEN.format(base=exp(small), fee=exp(fee), discount=exp(disc),
                                combined=exp(comb))
    return inst


def run_hidden(inst: Inst4, snap: dict[str, str]) -> dict[str, str]:
    """Materialise a snapshot of ledger/ in a clean directory and run the hidden suite."""
    with tempfile.TemporaryDirectory(prefix="v4-score-") as tmp:
        tree = Path(tmp)
        v2.write_tree(tree, {"ledger/__init__.py": ""})
        v2.write_tree(tree, snap)
        v2.write_tree(tree, {"hidden_rates.py": inst.hidden})
        res = v2.run_suite(tree, "hidden_rates")
    return {t: res.get(t, "ERROR") for t in TESTS} if "__import__" not in res else {
        t: "ERROR" for t in TESTS}


def offline_checks() -> list[tuple[str, bool]]:
    """Free self-checks: the reference passes; the baseline fails exactly the three ticket tests;
    each single feature passes its own test only (so a lost update is observable per agent)."""
    inst = build_inst(9201)
    full = run_hidden(inst, {"ledger/rates.py": rates_text(inst, set(FEATURES))})
    base = run_hidden(inst, {"ledger/rates.py": rates_text(inst)})
    only_fee = run_hidden(inst, {"ledger/rates.py": rates_text(inst, {"fee"})})
    only_disc = run_hidden(inst, {"ledger/rates.py": rates_text(inst, {"discount"})})
    ok = "ok"
    return [("reference solution passes every hidden test", all(v == ok for v in full.values())),
            ("baseline passes test_base and fails the other four",
             base["test_base"] == ok and all(base[t] != ok for t in TESTS[1:])),
            ("fee alone passes test_fee, not test_discount or test_combined",
             only_fee["test_fee"] == ok and only_fee["test_discount"] != ok
             and only_fee["test_combined"] != ok),
            ("discount alone passes test_discount, not test_fee or test_combined",
             only_disc["test_discount"] == ok and only_disc["test_fee"] != ok
             and only_disc["test_combined"] != ok)]


# ---- prompts: fixed before the freeze; their hash goes in the gate -----------------------------
INTRO = {who: (f"You are {who} on a two-agent team working on a ledger toolkit. Your working "
               "directory is a git checkout that you share with the other agent; "
               f"TICKETS.md lists both tickets and you do Ticket "
               f"{who[-1].upper()}. Run tests with `python3 -m unittest discover tests`. Run each "
               "shell command as its own Bash call (no `cd`, `;`, `&&`, loops or pipes), use Read "
               "to read files, work only inside your working directory, and check your code with "
               "the unit tests, not `python3 -c`.") for who in (A, B)}
SHARED_LINE = ("The other agent is working in it at the same time, and both tickets change the "
               "same function in ledger/rates.py; avoid overwriting each other's work, using "
               "whatever the project provides.")
TASK = {"S1": "Do your ticket now; stop when you have done what you can in this session.",
        "S2": "Do your ticket now; stop when you have done what you can in this session."}
# Every prompt, both scenarios, both agents, every arm (the same words): bounded patience.
PATIENCE = ("Both tickets must be finished in the tree before you stop, whichever agent's they "
            "are: if your teammate does not finish theirs, finish it yourself. If you have to wait for "
            "your teammate, you may wait with `python3 tools/pause.py 30` (about half a minute "
            "each time) and check again, but wait no longer than {minutes} minutes in total; then "
            "finish what you can and stop.")
PATIENCE_MINUTES = {"S1": 5, "S2": 6}
PARAGRAPHS = {
    "N0": "",
    "O": "",
    "S": ("Team notes live in NOTES.md in your working directory, a file you both can edit. Before "
          "you edit ledger/rates.py, read NOTES.md; if it has a line starting `CLAIMED "
          "ledger/rates.py by {other}` with no later `RELEASED ledger/rates.py by {other}` line, "
          "{other} is working on it and you should not edit it meanwhile. Otherwise add one line "
          "`CLAIMED ledger/rates.py by {me} at <time>` with the Edit tool, keeping the earlier "
          "lines, and when you are done add one line `RELEASED ledger/rates.py by {me}`. Keep each "
          "note to one line of plain text without curly braces."),
    "L": ("Locks are directories under locks/ in your working directory. Before you edit "
          "ledger/rates.py, take its lock with `mkdir locks/ledger-rates` as its own Bash call; if "
          "that fails because the directory exists, {other} holds the lock and is working on the "
          "file, so you should not edit it meanwhile. When you are done, release the lock with "
          "`rmdir locks/ledger-rates`. A lock directory never expires by itself, and `ls locks` shows "
          "the locks that are held. Run each lock command on its own, without curly braces, and take the lock before your first edit."),
    "P": ("Team mail uses `python3 tools/agent-postbox/agent_mail.py`. Before you edit "
          "ledger/rates.py, claim it with `python3 tools/agent-postbox/agent_mail.py send --type "
          "CLAIM --from {me} --to {other} --scope ledger/rates.py --expires {exp} --subject "
          "'editing rates' --body-file - <<'EOF'`, a line of text and `EOF` on its own line, in "
          "one Bash call; it prints an id, and exit status 3 means {other} holds the scope until "
          "its lease expires. When you are done, send the same command again with `--supersedes "
          "<id>` and without `--scope` to release it. Write the text without curly braces."),
}
EXP_PLACEHOLDER = "2030-01-01T00:00:00Z"
ORACLE_LINE = ("The other agent is not working at the same time: you have the working tree to "
               "yourself, and any work already in it is your teammate's, to keep.")
COMMON_ALLOW = v2.COMMON_ALLOW + ("Bash(python3 tools/pause.py *)", "Bash(date)")
ARM_ALLOW = {"L": ("Bash(mkdir locks/*)", "Bash(rmdir locks/*)")}


def paragraph(arm: str, who: str, exp: str = EXP_PLACEHOLDER) -> str:
    me, other = (A, B) if who == A else (B, A)
    return PARAGRAPHS[arm].format(me=me, other=other, exp=exp)


def paragraph_chars() -> dict[str, dict[str, int]]:
    return {arm: {who: len(paragraph(arm, who)) for who in (A, B)} for arm in ARMS}


def lengths_within(tolerance: float = 0.10) -> bool:
    """Validity gate 4: the S, L and P paragraphs are within 10% of each other, per role."""
    c = paragraph_chars()
    return all(max(c[a][w] for a in ("S", "L", "P")) - min(c[a][w] for a in ("S", "L", "P"))
               <= tolerance * max(c[a][w] for a in ("S", "L", "P")) for w in (A, B))


def prompts_hash() -> str:
    blob = json.dumps({"intro": INTRO, "shared": SHARED_LINE, "task": TASK, "patience": PATIENCE,
                       "paragraphs": PARAGRAPHS, "oracle": ORACLE_LINE, "allow": COMMON_ALLOW,
                       "arm_allow": ARM_ALLOW, "tickets": TICKETS, "rates_baseline": rates_text(
                           build_inst(9201)), "pause": PAUSE_PY, "timing": LIVE_TIMING.__dict__,
                       "p_rule_forms": ["rel", "abs-quoted-double", "abs-quoted-single", "abs"]},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def expiry_literal(launch_wall: float, seconds: int) -> str:
    return dt.datetime.fromtimestamp(launch_wall + seconds, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def prompt_for(scenario: str, arm: str, who: str, *, exp: str = EXP_PLACEHOLDER,
               survivor: bool = False, timing: Timing = LIVE_TIMING) -> str:
    """One agent's whole prompt. Arms differ only in the paragraph (and O's sentence)."""
    parts = [INTRO[who], TASK[scenario]]
    minutes = round(timing.patience_s / 60) if scenario == "S2" else PATIENCE_MINUTES[scenario]
    parts.append(PATIENCE.format(minutes=max(1, minutes)))
    if arm == "O":
        parts.append(ORACLE_LINE)
    else:
        parts.append(SHARED_LINE)
        if paragraph(arm, who):
            parts.append(paragraph(arm, who, exp))
    return " ".join(parts)


def allow_rules(arm: str, proj: Path) -> tuple[str, ...]:
    rules = COMMON_ALLOW + ARM_ALLOW.get(arm, ())
    if arm == "P":
        rel = f"{DEST}/agent_mail.py"
        absolute = str(proj / rel)
        rules += tuple(f"Bash(python3 {f} *)" for f in
                       (rel, f'"{absolute}"', f"'{absolute}'", absolute))
    return rules


def symmetry(proj: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in ARMS:
        rules = allow_rules(arm, proj)
        out[arm] = {"tools": "Bash,Read,Edit,Write", "common_allow_rules": len(COMMON_ALLOW),
                    "extra_allow_rules": [r for r in rules if r not in COMMON_ALLOW],
                    "paragraph_chars": paragraph_chars()[arm],
                    "prompt_chars": {w: len(prompt_for("S1", arm, w)) for w in (A, B)}}
    return out


# ---- events: the per-agent tool-call log -------------------------------------------------------
def flatten(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(flatten(c.get("text") if isinstance(c, dict) else c) for c in content)
    return "" if content is None else str(content)


@dataclass
class AgentLog:
    ops: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    model: str = ""
    result_seen: bool = False
    is_error: bool = False
    cost_usd: float | None = None
    turns: int | None = None
    denials: list[Any] = field(default_factory=list)
    rate_limited: bool = False
    api_retries: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    last_t: float = 0.0


def parse_agent(lines: list[tuple[float, str]]) -> AgentLog:
    log = AgentLog()
    pending: dict[str, dict[str, Any]] = {}
    for t, line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        log.last_t = t
        kind = ev.get("type")
        content = (ev.get("message") or {}).get("content") if isinstance(
            ev.get("message"), dict) else None
        if kind == "system" and ev.get("subtype") == "init":
            log.session_id, log.model = str(ev.get("session_id") or ""), str(ev.get("model") or "")
        elif kind == "assistant" and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    op = {"t": t, "id": item.get("id"), "name": item.get("name"),
                          "input": item.get("input") or {}, "res_t": None, "result": "",
                          "is_error": False}
                    log.ops.append(op)
                    pending[str(item.get("id"))] = op
        elif kind == "user" and isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    hit = pending.get(str(item.get("tool_use_id")))
                    if hit is not None:
                        hit["res_t"], hit["is_error"] = t, bool(item.get("is_error"))
                        hit["result"] = flatten(item.get("content"))
        elif kind == "system" and ev.get("subtype") == "api_retry":
            log.api_retries += 1
        elif kind == "rate_limit_event":
            if (ev.get("rate_limit_info") or {}).get("status") == "rejected":
                log.rate_limited = True
        elif kind == "result":
            log.result_seen = True
            log.is_error = bool(ev.get("is_error"))
            log.denials = list(ev.get("permission_denials") or [])
            cost, turns = ev.get("total_cost_usd"), ev.get("num_turns")
            log.cost_usd = float(cost) if isinstance(cost, (int, float)) else None
            log.turns = turns if isinstance(turns, int) else None
            log.usage = dict(ev.get("usage") or {})
            if ev.get("api_error_status") == 429:
                log.rate_limited = True
    return log


def applied(op: dict[str, Any]) -> bool:
    return op["res_t"] is not None and not op["is_error"]


def touches_rates(op: dict[str, Any]) -> bool:
    return str(op["input"].get("file_path", "")).endswith(SCOPE) and op["name"] in ("Edit", "Write")


REJECTED = re.compile(r"modified since read|has not been read|not found|String to replace|"
                      r"must read|File has been", re.I)


def introduced_prereq(ops: list[dict[str, Any]]) -> bool:
    """The agent wrote `def normalise_currency(` into the file without having seen one: not in any
    earlier tool result of its own session and not in its own earlier writes. Two such agents are
    duplicate work (a teammate's existing definition that was read first is not)."""
    seen = False
    for op in ops:
        if (op["name"] in ("Edit", "Write") and touches_rates(op) and applied(op)
                and "def normalise_currency(" in str(op["input"].get("new_string",
                                                     op["input"].get("content", "")))):
            if not seen:
                return True
            continue
        if op["res_t"] is not None and "def normalise_currency(" in op["result"]:
            seen = True
        if (op["name"] in ("Edit", "Write") and "def normalise_currency(" in str(
                op["input"].get("new_string", op["input"].get("content", "")))):
            seen = True
    return False


# ---- snapshots, attribution, outcomes ----------------------------------------------------------
Snap = dict[str, str]


def take_snapshot(tree: Path) -> Snap:
    snap: Snap = {}
    pkg = tree / "ledger"
    if pkg.is_dir():
        for p in sorted(pkg.rglob("*.py")):
            try:
                snap[p.relative_to(tree).as_posix()] = p.read_text(encoding="utf-8",
                                                                   errors="replace")
            except OSError:
                continue
    return snap


def snap_sha(snap: Snap) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()


def attribute(prev: Snap, new: Snap, t: float,
              ops: dict[str, list[dict[str, Any]]], tol: float = 1.0) -> str:
    """Which agent's applied Edit/Write explains the change of ledger/rates.py between two
    snapshots: a Write whose content is the new file, or an Edit whose new_string is now in it.
    The latest such tool call before the change wins. 'unattributed' when none matches."""
    old_text, new_text = prev.get(SCOPE, ""), new.get(SCOPE, "")
    if old_text == new_text:
        return "other-file"
    best: tuple[float, str] | None = None
    for who, lst in ops.items():
        for op in lst:
            if not (touches_rates(op) and applied(op)) or op["t"] > t + tol:
                continue
            if op["name"] == "Write":
                hit = op["input"].get("content") == new_text
            else:
                ns = str(op["input"].get("new_string", ""))
                hit = bool(ns) and ns in new_text and ns not in old_text
            if hit and (best is None or op["t"] > best[0]):
                best = (op["t"], who)
    return best[1] if best else "unattributed"


def line_gain(prev: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for d in difflib.ndiff(prev.splitlines(), new.splitlines()):
        added += d.startswith("+ ")
        removed += d.startswith("- ")
    return added, removed


def replay_states(base: Snap, ops: dict[str, list[dict[str, Any]]]) -> list[tuple[float, str, Snap]]:
    """The per-agent edit log replayed in the order the results arrived: every applied Write sets
    ledger/rates.py to its content, every applied Edit replaces its old_string in the replayed
    text when it is there. These states exist even when the poller (which can miss two changes
    inside one interval) did not see them."""
    seq = sorted(((o["res_t"], w, o) for w, lst in ops.items() for o in lst
                  if touches_rates(o) and applied(o)), key=lambda x: x[0])
    text = base.get(SCOPE, "")
    out: list[tuple[float, str, Snap]] = []
    for t, who, op in seq:
        if op["name"] == "Write":
            text = str(op["input"].get("content", ""))
        else:
            old, new = str(op["input"].get("old_string", "")), str(op["input"].get("new_string", ""))
            if not old or old not in text:
                continue
            text = text.replace(old, new, 1)
        out.append((t, who, {**base, SCOPE: text}))
    return out


def analyse_states(inst: Inst4, states: list[tuple[float, Snap]], logs: dict[str, AgentLog],
                   *, kill_t: float | None = None) -> dict[str, Any]:
    """The collision outcomes and the recovery outcome, from the snapshot log and the per-agent
    tool-call log. `states[0]` is the baseline; the last is the final tree."""
    cache: dict[str, dict[str, str]] = {}

    def results(snap: Snap) -> dict[str, str]:
        key = snap_sha(snap)
        if key not in cache:
            cache[key] = run_hidden(inst, snap)
        return cache[key]

    ops = {w: logs[w].ops for w in (A, B)}
    res = [results(s) for _, s in states]
    final = res[-1]
    ok = "ok"
    replay = replay_states(states[0][1], ops)
    rres = [results(snap) for _, _, snap in replay]
    ever_ok = {w: any(r[TICKET_TEST[w]] == ok for r in res[1:] + rres) for w in (A, B)}
    lost = {w: ever_ok[w] and final[TICKET_TEST[w]] != ok for w in (A, B)}
    transitions: list[dict[str, Any]] = []
    regress = {A: 0, B: 0}
    for i in range(1, len(states)):
        t, snap = states[i]
        who = attribute(states[i - 1][1], snap, t, ops)
        added, removed = line_gain(states[i - 1][1].get(SCOPE, ""), snap.get(SCOPE, ""))
        transitions.append({"t": round(t, 3), "by": who, "lines_added": added,
                            "lines_removed": removed, "results": res[i]})
        for victim in (A, B):
            if who not in (victim, "other-file") and res[i - 1][TICKET_TEST[victim]] == ok \
                    and res[i][TICKET_TEST[victim]] != ok:
                regress[victim] += 1
    duplicate = introduced_prereq(ops[A]) and introduced_prereq(ops[B])
    rejected = {w: sum(1 for o in ops[w] if touches_rates(o) and o["res_t"] is not None
                       and o["is_error"] and REJECTED.search(o["result"])) for w in (A, B)}
    final_ok = all(v == ok for v in final.values())
    # time to recovery: the first state from which every hidden test passes to the end
    ttr = None
    if final_ok:
        k = len(res) - 1
        while k > 0 and all(v == ok for v in res[k - 1].values()):
            k -= 1
        ttr = states[k][0]
    lost_update = lost[A] or lost[B]
    return {"final_tests": final, "final_ok": final_ok, "ever_ok": ever_ok,
            "lost_update_by_agent": lost, "lost_update": lost_update, "duplicate_work": duplicate,
            "collision": lost_update or duplicate,
            "regressions_caused_by_other": regress, "rejected_edits": rejected,
            "transitions": transitions, "unattributed_transitions": sum(
                1 for x in transitions if x["by"] == "unattributed"),
            "replayed_states": len(replay), "first_all_ok_t": ttr,
            "time_to_recovery_s": (round(ttr - kill_t, 2) if ttr is not None and kill_t is not None
                                   else None)}


# ---- drivers ------------------------------------------------------------------------------------
@dataclass
class Spec4:
    tag: str
    arm: str
    who: str
    proj: Path
    prompt: str
    env: dict[str, str]
    cmd: list[str]
    inst: Inst4
    scenario: str
    timing: Timing = LIVE_TIMING
    survivor: bool = False
    lease_s: int = 0


class Handle:
    """One running agent: timestamped stdout lines, a kill, an exit status."""

    def __init__(self, proj: Path) -> None:
        self.proj = proj
        self.lines: queue.Queue[tuple[float, str] | None] = queue.Queue()
        self.killed = False
        self.t_start = time.monotonic()
        self.stderr = ""

    def alive(self) -> bool:
        raise NotImplementedError

    def kill(self) -> None:
        raise NotImplementedError

    def returncode(self) -> int | None:
        raise NotImplementedError

    def drain(self) -> list[tuple[float, str]]:
        out: list[tuple[float, str]] = []
        while True:
            try:
                item = self.lines.get_nowait()
            except queue.Empty:
                return out
            if item is not None:
                out.append(item)


class LiveHandle(Handle):   # pragma: no cover - never run in tests
    def __init__(self, spec: Spec4, stderr_path: Path) -> None:
        super().__init__(spec.proj)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self.err = stderr_path.open("w")
        self.proc = subprocess.Popen(spec.cmd, cwd=spec.proj, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.err, text=True,
                                     env=spec.env, start_new_session=True)
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(spec.prompt)
            self.proc.stdin.close()
        except OSError:
            pass
        threading.Thread(target=self.pump, daemon=True).start()

    def pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.lines.put((time.monotonic(), line))
        self.lines.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        self.killed = True
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def returncode(self) -> int | None:
        return self.proc.poll()

    def close(self) -> None:
        try:
            self.err.close()
        except OSError:
            pass


class LiveDriver:
    """The only code that starts `claude`. Reached only from --live with POSTBOX_EXPERIMENT_LIVE=1."""

    def start(self, spec: Spec4, stderr_path: Path) -> Handle:   # pragma: no cover
        return LiveHandle(spec, stderr_path)


# ---- the SIMULATION driver ----------------------------------------------------------------------
class SimHandle(Handle):
    """SIMULATION. A thread that plays one agent: it edits the shared tree like a (simple) agent,
    follows the arm's protocol with the real mailbox CLI / mkdir / notes file, and emits
    stream-json events of the host's shape. Nothing here is observed model behaviour."""

    def __init__(self, spec: Spec4, drv: SimDriver) -> None:
        super().__init__(spec.proj)
        self.spec, self.drv = spec, drv
        self.rc: int | None = None
        self.stop = threading.Event()
        self.n = 0
        self.thread = threading.Thread(target=self.main, daemon=True)
        self.thread.start()

    # -- plumbing
    def emit(self, ev: dict[str, Any]) -> None:
        if not self.stop.is_set():
            self.lines.put((time.monotonic(), json.dumps(ev) + "\n"))

    def tool(self, name: str, result: str = "ok", is_error: bool = False, **inp: Any) -> None:
        self.n += 1
        tid = f"toolu_{self.spec.tag}_{self.n}"
        self.emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.emit({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": is_error}]}})

    def pause(self, seconds: float) -> bool:
        return not self.stop.wait(seconds)

    def alive(self) -> bool:
        return self.rc is None

    def kill(self) -> None:
        self.killed = True
        self.stop.set()
        self.thread.join(timeout=5)
        self.rc = -9

    def returncode(self) -> int | None:
        return self.rc

    # -- the agent
    def main(self) -> None:
        s, d = self.spec, self.drv
        self.emit({"type": "system", "subtype": "init", "session_id": f"sim-{s.tag}",
                   "model": "sim-model"})
        try:
            self.play()
        except _SimStop:
            return
        total = d.cost
        self.emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
                   "result": "done", "total_cost_usd": total, "permission_denials": [],
                   "usage": {"input_tokens": 1000, "output_tokens": 200}})
        self.rc = 0

    def tree_file(self) -> Path:
        return self.spec.proj / SCOPE

    def read_rates(self) -> str:
        text = self.tree_file().read_text(encoding="utf-8")
        self.tool("Read", text, file_path=str(self.tree_file()))
        return text

    def features_in(self, text: str) -> set[str]:
        got = set()
        if "def normalise_currency(" in text:
            got.add("prereq")
        if "max(" in text and self.spec.inst.fee_ccy in text:
            got.add("fee")
        if f"amount >= {self.spec.inst.bulk}" in text:
            got.add("discount")
        return got

    def my_feature(self) -> str:
        return "fee" if self.spec.who == A else "discount"

    def write_with(self, text: str) -> None:
        feats = self.features_in(text) | {self.my_feature()}
        if self.drv.writes_prereq:
            feats.add("prereq")
        new = rates_text(self.spec.inst, feats)
        self.tool("Write", file_path=str(self.tree_file()), content=new)
        self.tree_file().write_text(new, encoding="utf-8")

    def chk(self) -> None:
        if self.stop.is_set():
            raise _SimStop

    def play(self) -> None:
        s, d = self.spec, self.drv
        arm, me = s.arm, s.who
        other = B if me == A else A
        proj = s.proj
        waited = 0.0
        limit = s.timing.patience_s if s.survivor else 1e9
        # 1. take the claim (arms S, L, P), waiting while the other holds it
        while arm in ("S", "L", "P"):
            self.chk()
            if arm == "L":
                try:
                    (proj / LOCK_REL).mkdir()
                    self.tool("Bash", command=f"mkdir {LOCK_REL}")
                    break
                except FileExistsError:
                    self.tool("Bash", "mkdir: File exists", True, command=f"mkdir {LOCK_REL}")
            elif arm == "S":
                notes = (proj / NOTES_REL).read_text(encoding="utf-8")
                self.tool("Read", notes, file_path=str(proj / NOTES_REL))
                if self.holds(notes, other):
                    pass
                else:
                    with (proj / NOTES_REL).open("a", encoding="utf-8") as fh:
                        fh.write(f"CLAIMED {SCOPE} by {me} at 00:00\n")
                    self.tool("Edit", file_path=str(proj / NOTES_REL), new_string="CLAIMED")
                    break
            else:
                cp = self.send_claim(me, other)
                self.tool("Bash", cp.stdout + cp.stderr, cp.returncode != 0,
                          command=f"python3 {DEST}/agent_mail.py send --type CLAIM --scope {SCOPE}")
                if cp.returncode == 0:
                    m = re.search(r"^id: (\S+)$", cp.stdout, re.M)
                    d.claim_ids[me] = m.group(1) if m else ""
                    break
            if waited >= limit:
                return
            self.tool("Bash", "waited", command="python3 tools/pause.py 30")
            if not self.pause(s.timing.pause_s):
                raise _SimStop
            waited += s.timing.pause_s
        if s.scenario == "S2" and me == A and not s.survivor and arm not in ("N0", "O"):
            self.pause(120)                       # the holder is slow: the harness kills it here
            raise _SimStop
        # 2. the work: read, think, write (the racy simulation writes the stale content it read)
        self.chk()
        text = self.read_rates()
        if d.stale_write:
            if not self.pause(d.think_s):
                raise _SimStop
        else:
            if not self.pause(d.think_s):
                raise _SimStop
            text = self.read_rates()
        if s.survivor and d.survivor_does_both:
            text = rates_text(s.inst, set(FEATURES))
            self.tool("Write", file_path=str(self.tree_file()), content=text)
            self.tree_file().write_text(text, encoding="utf-8")
        else:
            self.write_with(text)
        if s.scenario == "S2" and me == A and not s.survivor and arm == "N0":
            self.pause(120)                       # N0 holder: lingers after its first change
            raise _SimStop
        # 3. release (a SIMULATED agent is tidy; a killed one never gets here)
        if arm == "L":
            (proj / LOCK_REL).rmdir()
            self.tool("Bash", command=f"rmdir {LOCK_REL}")
        elif arm == "S":
            with (proj / NOTES_REL).open("a", encoding="utf-8") as fh:
                fh.write(f"RELEASED {SCOPE} by {me}\n")
            self.tool("Edit", file_path=str(proj / NOTES_REL), new_string="RELEASED")
        elif arm == "P" and d.claim_ids.get(me):
            self.send_release(me, other)

    def holds(self, notes: str, other: str) -> bool:
        held = False
        for ln in notes.splitlines():
            if ln.startswith(f"CLAIMED {SCOPE} by {other}"):
                held = True
            if ln.startswith(f"RELEASED {SCOPE} by {other}"):
                held = False
        return held

    def mail(self, *args: str, body: str = "sim\n") -> subprocess.CompletedProcess[str]:
        s = self.spec
        return subprocess.run([sys.executable, "-I", str(s.proj / DEST / "agent_mail.py"), "send",
                               *args, "--body-file", "-"], input=body, cwd=s.proj, env=s.env,
                              capture_output=True, text=True)

    def send_claim(self, me: str, other: str) -> subprocess.CompletedProcess[str]:
        exp = expiry_literal(self.drv.launch_wall[me], self.spec.lease_s)
        return self.mail("--type", "CLAIM", "--from", me, "--to", other, "--scope", SCOPE,
                         "--expires", exp, "--subject", "editing rates")

    def send_release(self, me: str, other: str) -> None:
        exp = expiry_literal(time.time(), 300)
        cp = self.mail("--type", "CLAIM", "--from", me, "--to", other, "--supersedes",
                       self.drv.claim_ids[me], "--expires", exp, "--subject", "released")
        self.tool("Bash", cp.stdout + cp.stderr, cp.returncode != 0,
                  command=f"python3 {DEST}/agent_mail.py send --type CLAIM --supersedes")


class _SimStop(Exception):
    pass


class SimDriver:
    """SIMULATION driver. `stale_write`: the simulated agents write the stale content they read
    before thinking (the race N0 should show if the host does not stop it); `writes_prereq`: both
    write the prerequisite; `survivor_does_both`: a scenario-2 survivor writes both tickets."""

    def __init__(self, *, stale_write: bool = False, writes_prereq: bool = True,
                 survivor_does_both: bool = True, think_s: float = 0.15, cost: float = 0.02) -> None:
        self.stale_write, self.writes_prereq = stale_write, writes_prereq
        self.survivor_does_both, self.think_s, self.cost = survivor_does_both, think_s, cost
        self.claim_ids: dict[str, str] = {}
        self.launch_wall: dict[str, float] = {}
        self.specs: list[Spec4] = []
        self.handles: list[SimHandle] = []
        self.lock = threading.Lock()

    def start(self, spec: Spec4, stderr_path: Path) -> Handle:
        with self.lock:
            self.specs.append(spec)
            self.launch_wall[spec.who] = time.time()
        h = SimHandle(spec, self)
        self.handles.append(h)
        return h


# ---- the harness --------------------------------------------------------------------------------
class Ceiling(Exception):
    pass


class Runner(e2e.Runner):
    """Only for e2e's environment, base command and event accounting."""


def git(tree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(tree), "-c", "user.name=harness",
                           "-c", "user.email=harness@example.invalid", "-c", "commit.gpgsign=false",
                           *args], capture_output=True, text=True, check=check)


def mailbox_claims(tree: Path, who: str) -> list[Path]:
    box = tree / "docs" / "agent-mail"
    out = []
    if box.is_dir():
        for p in sorted(box.glob("*.md")):
            if p.name == "PARTICIPANTS.md":
                continue
            try:
                head = p.read_text(encoding="utf-8")[:600]
            except OSError:
                continue
            if re.search(r"^type: CLAIM\s*$", head, re.M | re.I) and re.search(
                    rf"^from: {re.escape(who)}\s*$", head, re.M | re.I):
                out.append(p)
    return out


def claim_artifact(arm: str, tree: Path, who: str, base_sha: str) -> tuple[bool, float | None]:
    """Whether `who`'s claim/lock/note exists now, and its wall-clock mtime."""
    try:
        if arm == "P":
            found = mailbox_claims(tree, who)
            return (True, max(p.stat().st_mtime for p in found)) if found else (False, None)
        if arm == "L":
            p = tree / LOCK_REL
            return (True, p.stat().st_mtime) if p.is_dir() else (False, None)
        if arm == "S":
            p = tree / NOTES_REL
            if p.is_file() and re.search(rf"^CLAIMED .* by {re.escape(who)}\b",
                                         p.read_text(encoding="utf-8"), re.M):
                return True, p.stat().st_mtime
            return False, None
        if arm == "N0":      # no channel: the trigger is the holder's first change to the file
            p = tree / SCOPE
            if p.is_file() and snap_sha(take_snapshot(tree)) != base_sha:
                return True, p.stat().st_mtime
    except OSError:
        pass
    return False, None


class Harness:
    def __init__(self, args: argparse.Namespace, claude: str, work: Path, out: Path, driver: Any,
                 timing: Timing, sleep: Any = time.sleep) -> None:
        self.args, self.claude, self.work, self.out = args, claude, work, out
        self.driver, self.timing, self.sleep = driver, timing, sleep
        self.lock = threading.RLock()
        self.n_sessions = 0
        self.spent = 0.0
        self.stop: str | None = None
        self.rl_streak = 0
        self.t_begin = time.monotonic()
        self.runner = Runner(args, claude, work)

    # ---- guards
    def reserve(self, n: int) -> None:
        with self.lock:
            if self.stop:
                raise Ceiling(self.stop)
            if self.n_sessions + n > self.args.max_sessions:
                raise Ceiling(f"session cap {self.args.max_sessions} would be passed")
            if time.monotonic() - self.t_begin > self.args.wall_hours * 3600:
                self.stop = f"wall-time cap {self.args.wall_hours} h reached"
                raise Ceiling(self.stop)
            if self.spent > self.args.notional_usd:
                self.stop = f"notional cost ${self.spent:.2f} passed ${self.args.notional_usd:.2f}"
                raise Ceiling(self.stop)
            self.n_sessions += n

    def note_session(self, log: AgentLog) -> None:
        with self.lock:
            self.spent += log.cost_usd or 0.0
            self.rl_streak = self.rl_streak + 1 if log.rate_limited else 0
            if self.rl_streak >= 2:
                self.stop = "two rate-limit rejections in a row"

    # ---- the project
    def build(self, inst: Inst4, arm: str, root: Path) -> tuple[Path, dict[str, str]]:
        proj = root / "proj"
        proj.mkdir(parents=True)
        env = self.runner.env(None)
        files = {"README.md": README, "TICKETS.md": inst.tickets(), "ledger/__init__.py": "",
                 "ledger/rates.py": rates_text(inst), "tests/test_rates_base.py": VISIBLE_TEST,
                 "tools/pause.py": PAUSE_PY}
        extra: dict[str, str] = {}
        git(proj, "init", "-q")
        v2.write_tree(proj, files)
        if arm == "S":
            (proj / NOTES_REL).write_text("# Team notes\n", encoding="utf-8")
        elif arm == "L":
            (proj / "locks").mkdir()
            (proj / "locks" / ".gitkeep").write_text("", encoding="utf-8")
        elif arm == "P":
            subprocess.run([sys.executable, "-I", str(e2e.INSTALL), str(proj), "--adapter",
                            "claude-code"], check=True, capture_output=True, text=True, env=env)
            extra = {"AGENT_MAIL_DIR": str(proj / "docs" / "agent-mail")}
        self.write_permissions(proj, arm)
        if arm != "P":
            bad: list[Any] = [p for p in (proj / DEST, proj / "docs" / "agent-mail") if p.exists()]
            bad += v2.scan(proj, ("agent_mail", "agent-postbox"))
            if bad:
                raise RuntimeError(f"mailbox leaked into a non-P project: {bad[:5]}")
        git(proj, "add", "-A")
        git(proj, "commit", "-q", "-m", "baseline")
        return proj, extra

    def write_permissions(self, proj: Path, arm: str) -> None:
        """The allow rules are INSTALLED in the throwaway project's .claude/settings.json (merged
        with the adapter's hook for P), so `doctor` can see them, and also passed on the command
        line. Verified by `verify_rules` before a live run."""
        path = proj / ".claude" / "settings.json"
        path.parent.mkdir(exist_ok=True)
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data["permissions"] = {"allow": list(allow_rules(arm, proj))}
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def command(self, arm: str, proj: Path) -> list[str]:
        cmd = self.runner.base_cmd(tools="Bash,Read,Edit,Write", allowed=(),
                                   max_turns=self.args.max_turns)
        cmd[cmd.index("--effort") + 1] = self.args.effort
        return cmd + ["--allowedTools", *allow_rules(arm, proj)]

    def spec(self, scenario: str, arm: str, who: str, tag: str, proj: Path, extra: dict[str, str],
             inst: Inst4, *, survivor: bool, exp: str, lease: int) -> Spec4:
        prompt = prompt_for(scenario, arm, who, exp=exp, survivor=survivor, timing=self.timing)
        return Spec4(tag, arm, who, proj, prompt, self.runner.env(who, extra),
                     self.command(arm, proj), inst, scenario, self.timing, survivor, lease)

    # ---- one run
    def run_one(self, scenario: str, arm: str, seed: int, attempt: int = 1) -> dict[str, Any]:
        inst = build_inst(seed)
        tag = f"{scenario}-{arm}-{seed}-t{attempt}"
        rec: dict[str, Any] = {
            "schema": SCHEMA, "scenario": scenario, "arm": arm, "seed": seed, "attempt": attempt,
            "tag": tag, "model": self.args.model, "effort": self.args.effort,
            "simulation": bool(self.args.dry_run), "paragraph_chars": paragraph_chars()[arm],
            "manual_nudges": 0, "void": "", "infra_failure": "", "sessions": [],
            "params": {"fee_ccy": inst.fee_ccy, "fee_pct": inst.fee_pct, "fee_min": inst.fee_min,
                       "bulk": inst.bulk, "disc_pct": inst.disc_pct}}
        root = Path(tempfile.mkdtemp(prefix=f"v4-{tag}-", dir=self.work))
        t_wall = time.monotonic()
        try:
            self.reserve(2)
            proj, extra = self.build(inst, arm, root)
            rec.update(self.execute(scenario, arm, inst, proj, extra, tag))
        except Ceiling as e:
            rec["aborted"] = f"stopped: {e}"
        finally:
            shutil.rmtree(root, ignore_errors=True)
        rec["seconds"] = round(time.monotonic() - t_wall, 1)
        runs = self.out / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{tag}.json").write_text(json.dumps(rec, indent=2, sort_keys=True, default=str),
                                          encoding="utf-8")
        return rec

    def execute(self, scenario: str, arm: str, inst: Inst4, proj: Path, extra: dict[str, str],
                tag: str) -> dict[str, Any]:
        tm = self.timing
        t0 = time.monotonic()
        base = take_snapshot(proj)
        states: list[tuple[float, Snap]] = [(0.0, base)]
        base_sha = snap_sha(base)
        handles: dict[str, Handle] = {}
        lines: dict[str, list[tuple[float, str]]] = {A: [], B: []}
        info: dict[str, Any] = {"killed": None, "kill_t": None, "claim_seen_t": None,
                                "claim_mtime": None, "kill_wall": None}
        timed_out: dict[str, bool] = {A: False, B: False}
        launch_t: dict[str, float] = {}

        def launch(who: str, *, survivor: bool = False) -> None:
            lease = tm.lease_s2_s if scenario == "S2" and not survivor else (
                tm.lease_s1_s if scenario == "S1" else 600)
            exp = expiry_literal(time.time(), lease)
            sp = self.spec(scenario, arm, who, "a" if who == A else "b", proj, extra, inst,
                           survivor=survivor, exp=exp, lease=lease)
            sp.tag = f"{tag}-{'a' if who == A else 'b'}"
            handles[who] = self.driver.start(sp, self.out / "raw" / f"{sp.tag}.stderr")
            launch_t[who] = time.monotonic() - t0

        def poll() -> None:
            snap = take_snapshot(proj)
            if snap != states[-1][1]:
                states.append((time.monotonic() - t0, snap))
            for w, h in handles.items():       # event times are relative to the run start
                lines[w] += [(t - t0, ln) for t, ln in h.drain()]

        def wait_until(who: str, deadline: float) -> None:
            while handles[who].alive() and time.monotonic() < deadline:
                poll()
                self.sleep(tm.poll_s)
            if handles[who].alive():
                timed_out[who] = True
                handles[who].kill()
            poll()

        if scenario == "S1" and arm != "O":
            launch(A)
            launch(B)
            deadline = time.monotonic() + tm.s1_timeout_s
            while any(h.alive() for h in handles.values()) and time.monotonic() < deadline:
                poll()
                self.sleep(tm.poll_s)
            for w, h in handles.items():
                if h.alive():
                    timed_out[w] = True
                    h.kill()
        elif arm == "O":                                    # sequential oracle (both scenarios)
            launch(A)
            wait_until(A, time.monotonic() + tm.s1_timeout_s)
            launch(B, survivor=(scenario == "S2"))
            wait_until(B, time.monotonic() + tm.s1_timeout_s)
        else:                                               # scenario 2: kill the holder
            self.scenario2(arm, inst, launch, poll, handles, info, timed_out, states, base_sha,
                           launch_t, t0)
        self.sleep(tm.poll_s * 2)
        poll()
        for h in handles.values():
            if isinstance(h, LiveHandle):
                h.close()
        logs = {w: parse_agent(lines[w]) for w in (A, B)}
        for w in (A, B):
            self.note_session(logs[w])
        out = self.score(scenario, arm, inst, proj, states, logs, handles, info, timed_out,
                         launch_t, extra, tag, lines)
        return out

    def scenario2(self, arm: str, inst: Inst4, launch: Any, poll: Any, handles: dict[str, Handle],
                  info: dict[str, Any], timed_out: dict[str, bool],
                  states: list[tuple[float, Snap]], base_sha: str, launch_t: dict[str, float],
                  t0: float) -> None:
        tm = self.timing
        proj = None
        launch(A)
        # the harness keeps the tree path via the spec of A
        proj = self.driver_tree(handles[A])
        deadline = time.monotonic() + tm.holder_timeout_s
        seen = False
        while time.monotonic() < deadline:
            poll()
            ok, mtime = claim_artifact(arm, proj, A, base_sha)
            if ok:
                seen = True
                info["claim_seen_t"] = time.monotonic() - t0
                info["claim_mtime"] = mtime
                break
            if not handles[A].alive():
                break
            self.sleep(tm.poll_s)
        if not seen:
            info["void"] = ("holder exited before its claim/lock/note was seen"
                            if not handles[A].alive() else "no claim/lock/note within the timeout")
            if handles[A].alive():
                timed_out[A] = True
                handles[A].kill()
            return
        if not handles[A].alive():
            info["void"] = "holder had already exited when its claim was first seen"
            return
        launch(B, survivor=True)
        self.sleep(tm.kill_delay_s)
        poll()
        info["kill_wall"] = time.time()
        info["kill_t"] = time.monotonic() - t0
        info["a_alive_at_kill"] = handles[A].alive()
        handles[A].kill()
        info["killed"] = True
        ok_after, _ = claim_artifact(arm, proj, A, base_sha)
        info["artifact_after_kill"] = ok_after
        wait_until_b = time.monotonic() + tm.patience_s + tm.grace_s
        while handles[B].alive() and time.monotonic() < wait_until_b:
            poll()
            self.sleep(tm.poll_s)
        if handles[B].alive():
            timed_out[B] = True
            handles[B].kill()
        poll()

    def driver_tree(self, h: Handle) -> Path:
        return h.proj

    def score(self, scenario: str, arm: str, inst: Inst4, proj: Path,
              states: list[tuple[float, Snap]], logs: dict[str, AgentLog],
              handles: dict[str, Handle], info: dict[str, Any], timed_out: dict[str, bool],
              launch_t: dict[str, float], extra: dict[str, str], tag: str,
              lines: dict[str, list[tuple[float, str]]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # raw events and artifacts are kept for every run, void or not
        raw = self.out / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        for w, key in ((A, "a"), (B, "b")):
            if w in handles:
                (raw / f"{tag}-{key}.jsonl").write_text("".join(ln for _, ln in lines[w]),
                                                        encoding="utf-8")
                (raw / f"{tag}-{key}.times.json").write_text(
                    json.dumps([round(t, 3) for t, _ in lines[w]]), encoding="utf-8")
        art = self.out / "artifacts" / tag
        art.mkdir(parents=True, exist_ok=True)
        (art / "snapshots.json").write_text(json.dumps(
            [{"t": round(t, 3), "sha": snap_sha(s), "files": s} for t, s in states]), encoding="utf-8")
        for rel in (NOTES_REL, "locks", "docs/agent-mail", "tests"):
            src = proj / rel
            if src.is_file():
                shutil.copyfile(src, art / Path(rel).name)
            elif src.is_dir():
                shutil.copytree(src, art / Path(rel).name, dirs_exist_ok=True)
        (art / "final.diff").write_text(git(proj, "diff", "HEAD", check=False).stdout,
                                        encoding="utf-8")
        (art / "status.txt").write_text(git(proj, "status", "--porcelain", "--untracked-files=all",
                                            check=False).stdout, encoding="utf-8")
        if arm == "P":
            cp = subprocess.run([sys.executable, "-I", str(proj / DEST / "agent_mail.py"), "list",
                                 "--json"], cwd=proj, capture_output=True, text=True,
                                env=self.runner.env(None, extra))
            (art / "mailbox.json").write_text(cp.stdout, encoding="utf-8")
        sessions: list[dict[str, Any]] = []
        infra = ""
        for w in (A, B):
            if w not in handles:
                continue
            lg, h = logs[w], handles[w]
            killed_design = bool(info.get("killed")) and w == A
            api_down = lg.api_retries >= 3 or (
                lg.result_seen and lg.is_error and not lg.ops)
            si = ("rate limited" if lg.rate_limited else
                  "API failure (retries, no work done)" if api_down and not killed_design else
                  "timeout" if timed_out[w] and not (scenario == "S2" and w == B) else
                  "host failure" if (not killed_design and not lg.result_seen and not timed_out[w]
                                     and h.returncode() not in (0, None)) else "")
            if si and not infra:
                infra = si
            denials: list[dict[str, str]] = [{"tool": str(d.get("tool_name")), "input": json.dumps(d.get("tool_input"))[:300]}
                       for d in lg.denials if isinstance(d, dict)]
            channel = [d for d in denials if any(k in d["input"] for k in (
                "agent_mail.py", "mkdir", "rmdir", NOTES_REL, "pause.py"))]
            sessions.append({
                "agent": w, "launch_s": round(launch_t[w], 2), "end_s": round(lg.last_t, 2),
                "result_seen": lg.result_seen, "killed_by_harness": killed_design,
                "timed_out": timed_out[w], "infra": si, "cost_usd": lg.cost_usd,
                "turns": lg.turns, "api_retries": lg.api_retries, "denials": len(denials), "denial_details": denials,
                "channel_denials": len(channel), "tools": [o["name"] for o in lg.ops],
                "session_id": lg.session_id, "model": lg.model,
                "pauses": sum(1 for o in lg.ops if "pause.py" in str(o["input"].get("command", ""))),
                "rejected_edits": sum(1 for o in lg.ops if touches_rates(o) and o["is_error"])})
        out["sessions"] = sessions
        out["infra_failure"] = infra
        out["void"] = info.get("void", "")
        out["host_refusals"] = [d | {"agent": s["agent"]} for s in sessions
                                for d in s["denial_details"]]
        # kill verification (gate 6)
        if scenario == "S2" and arm != "O":
            ver: dict[str, Any] = {"claim_seen_t": info.get("claim_seen_t"), "kill_t": info.get("kill_t"),
                   "claim_mtime": info.get("claim_mtime"), "kill_wall": info.get("kill_wall"),
                   "a_alive_at_kill": info.get("a_alive_at_kill"),
                   "artifact_after_kill": info.get("artifact_after_kill")}
            ok = (info.get("killed") and ver["claim_seen_t"] is not None
                  and ver["kill_t"] is not None and ver["kill_t"] > ver["claim_seen_t"]
                  and ver["claim_mtime"] is not None and ver["claim_mtime"] <= ver["kill_wall"]
                  and ver["a_alive_at_kill"] and ver["artifact_after_kill"] is not False)
            ver["verified"] = bool(ok)
            out["kill"] = ver
            if not ok and not out["void"]:
                out["void"] = "kill not verified to follow the claim/lock/note"
            if ok:
                kt = info.get("kill_t") or 0.0
                before = [s for t, s in states if t <= kt] or [states[0][1]]
                done_at_kill = run_hidden(inst, before[-1])[TICKET_TEST[A]] == "ok"
                ver["a_ticket_done_at_kill"] = done_at_kill
                # N0 has no claim: its trigger is the first change, which may already be the ticket
                if done_at_kill and arm != "N0" and not out["void"]:
                    out["void"] = "the holder had already finished its ticket when it was killed"
        kill_t = info.get("kill_t")
        out.update(analyse_states(inst, states, logs, kill_t=kill_t))
        out["states"] = len(states)
        out["recovered"] = bool(out["final_ok"]) if scenario == "S2" and arm != "O" else None
        out["cost_usd"] = round(sum(s["cost_usd"] or 0.0 for s in sessions), 6)
        out["cost_note"] = ("a killed session reports no cost; its cost is not counted"
                            if info.get("killed") else "")
        out["wall_s"] = round(max([s["end_s"] for s in sessions] or [0.0]), 1)
        out["channel_denials"] = sum(s["channel_denials"] for s in sessions)
        out["claims"] = self.claim_story(arm, proj, logs, extra)
        out["survivor"] = self.survivor_story(scenario, arm, proj, logs, states, info, handles)
        return out

    def survivor_story(self, scenario: str, arm: str, proj: Path, logs: dict[str, AgentLog],
                       states: list[tuple[float, Snap]], info: dict[str, Any],
                       handles: dict[str, Handle]) -> dict[str, Any]:
        """Scenario 2: what the survivor did about the dead holder's claim, from artifacts and its
        own tool calls. Descriptive: waited (pauses), broke a lock, wrote over an unreleased note,
        claimed after the lease, took the holder's ticket, did anything unsafe."""
        if scenario != "S2" or arm == "O" or B not in handles:
            return {}
        ops = logs[B].ops
        kill_t = info.get("kill_t") or 0.0
        bash = [str(o["input"].get("command", "")) for o in ops if o["name"] == "Bash"]
        edits = [o for o in ops if touches_rates(o) and applied(o)]
        first_edit = min((o["t"] for o in edits), default=None)
        pauses = sum(1 for c in bash if "pause.py" in c)
        unsafe = [c[:160] for c in bash if re.search(
            r"\brm\s+-[a-z]*r|git\s+(reset|checkout|stash|clean|restore)|\bchmod\b|\bkill\b|"
            r"\bmv\b|\bsudo\b|\bcurl\b|\bwget\b", c)]
        story: dict[str, Any] = {
            "pauses": pauses, "paused_s_nominal": round(pauses * self.timing.pause_s, 1),
            "first_rates_edit_after_kill_s": (round(first_edit - kill_t, 1)
                                              if first_edit is not None else None),
            "edited_rates": bool(edits), "unsafe_commands": unsafe,
            "broke_lock": arm == "L" and any(re.search(r"\brmdir\b|\brm\b", c) and "locks" in c
                                             for c in bash),
            "ended_by": "harness stop" if getattr(handles[B], "killed", False) else "own end"}
        notes = proj / NOTES_REL
        if arm == "S" and notes.is_file():
            lines = notes.read_text(encoding="utf-8").splitlines()
            a_open = any(ln.startswith(f"CLAIMED {SCOPE} by {A}") for ln in lines) and not any(
                ln.startswith(f"RELEASED {SCOPE} by {A}") for ln in lines)
            story["ignored_stale_note"] = bool(a_open and edits)
        if arm == "P":
            mine = mailbox_claims(proj, B)
            story["claimed_after_lease"] = bool(mine)
            story["claim_exit3_seen"] = sum(1 for o in ops if o["name"] == "Bash"
                                            and "agent_mail.py" in str(o["input"].get("command"))
                                            and "Exit code 3" in (o["result"] or ""))
        if arm == "L":
            story["lock_present_at_end"] = (proj / LOCK_REL).is_dir()
        return story

    def claim_story(self, arm: str, proj: Path, logs: dict[str, AgentLog],
                    extra: dict[str, str]) -> dict[str, Any]:
        """Ground truth on the channel from artifacts (for the descriptive tables, and so that the
        later audit stage has what it needs)."""
        story: dict[str, Any] = {}
        if arm == "P":
            story["claims_sent"] = {w: len(mailbox_claims(proj, w)) for w in (A, B)}
            story["claim_exit3_seen"] = {w: sum(1 for o in logs[w].ops if o["name"] == "Bash"
                                                and "agent_mail.py" in str(o["input"].get("command"))
                                                and re.search(r"Exit code 3|already held|exit 3|retry",
                                                              o["result"] or "")) for w in (A, B)}
        elif arm == "L":
            story["lock_present_at_end"] = (proj / LOCK_REL).is_dir()
            story["rmdir_by"] = [w for w in (A, B) for o in logs[w].ops
                                 if o["name"] == "Bash" and "rmdir" in str(o["input"].get("command"))]
        elif arm == "S":
            p = proj / NOTES_REL
            story["notes_lines"] = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
        return story

    # ---- retries and scheduling
    def run_with_retry(self, scenario: str, arm: str, seed: int) -> list[dict[str, Any]]:
        recs = [self.run_one(scenario, arm, seed, 1)]
        if (recs[0]["infra_failure"] and "aborted" not in recs[0] and not self.stop
                and self.args.stage == "pilot"):
            self.sleep(60 if recs[0]["infra_failure"] == "rate limited" else 5)
            recs.append(self.run_one(scenario, arm, seed, 2))
        return recs

    def run_plan(self, plan: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
        workers = max(1, min(self.args.workers, MAX_WORKERS))
        out: list[dict[str, Any]] = []

        def one(item: tuple[str, str, int]) -> list[dict[str, Any]]:
            try:
                return self.run_with_retry(*item)
            except Ceiling as e:
                return [{"scenario": item[0], "arm": item[1], "seed": item[2], "aborted": str(e)}]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for recs in pool.map(one, plan):
                out += recs
                print(f"  {recs[-1].get('tag', '?'):<22} sessions={self.n_sessions} "
                      f"void={recs[-1].get('void', '') or '-'} "
                      f"collision={recs[-1].get('collision', '-')} ok={recs[-1].get('final_ok', '-')}"
                      + (f" ABORTED {recs[-1]['aborted']}" if "aborted" in recs[-1] else ""))
                sys.stdout.flush()
        return out


# ---- plan, verification, gates ------------------------------------------------------------------
def pilot_plan(seeds: list[int]) -> list[tuple[str, str, int]]:
    """Scenario 2 (primary): seed-major, the arm order within a seed a seeded shuffle. Then the
    scenario-1 observation runs whose seeds are in `seeds`."""
    plan = []
    for seed in seeds:
        order = list(ARMS)
        random.Random(f"v4-order:{seed}").shuffle(order)
        plan += [("S2", arm, seed) for arm in order]
    plan += [r for r in OBSERVATION_PLAN if r[2] in seeds]
    return plan


def planned_sessions(stage: str) -> int:
    return 2 * (len(READINESS_PLAN) if stage == "readiness" else 5 * len(SEEDS["pilot"])
                + len(OBSERVATION_PLAN))


def verify_rules(tmp_root: Path, env: dict[str, str]) -> list[tuple[str, bool]]:
    """Offline verification that the P arm's allow rules are installed in the throwaway project
    and that `doctor` sees both spellings (it names the ones without an identical rule). Run
    before any live run; the readiness stage then checks live that the taught forms are not refused."""
    checks = []
    h = Harness(argparse.Namespace(max_turns=1, effort="medium", model="haiku", dry_run=True,
                                   max_sessions=0, wall_hours=1, notional_usd=1, stage="readiness",
                                   workers=1, max_calls=0), "claude", tmp_root, tmp_root, None, FAST_TIMING)
    proj, extra = h.build(build_inst(9201), "P", Path(tempfile.mkdtemp(prefix="v4-rules-",
                                                                          dir=tmp_root)))
    settings = json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    rel = f"Bash(python3 {DEST}/agent_mail.py *)"
    absq = f'Bash(python3 "{proj / DEST / "agent_mail.py"}" *)'
    checks.append(("P rules installed in .claude/settings.json (relative and absolute-quoted)",
                   rel in allow and absq in allow))
    checks.append(("the adapter's hook survived the merge",
                   "UserPromptSubmit" in settings.get("hooks", {})))
    cp = subprocess.run([sys.executable, "-I", str(proj / DEST / "agent_mail.py"), "doctor"],
                        cwd=proj, capture_output=True, text=True, env={**env, **extra})
    text = cp.stdout + cp.stderr
    checks.append(("doctor reports both send spellings as allowed by an identical rule",
                   text.count("allowed by an identical rule") >= 2
                   and "spellings without an identical rule" not in text))
    return checks


def readiness_gate(records: list[dict[str, Any]], args: argparse.Namespace,
                   offline: list[tuple[str, bool]], rules: list[tuple[str, bool]],
                   spent: float, sessions: int) -> dict[str, Any]:
    recs = [r for r in records if "aborted" not in r]

    def done(arm: str, scen: str) -> list[dict[str, Any]]:
        return [r for r in recs if r["arm"] == arm and r["scenario"] == scen
                and not r["infra_failure"] and all(s["result_seen"] or s["killed_by_harness"]
                                                    for s in r["sessions"])]

    s2 = [r for r in recs if r["scenario"] == "S2" and r["arm"] != "O"]
    checks = {"offline: " + n: ok for n, ok in offline}
    checks.update({"rules: " + n: ok for n, ok in rules})
    checks.update({f"scenario 2 {a}: ran to the end with a verified kill after the "
                   "claim/lock/note/first edit": any(r["arm"] == a and r.get("kill", {}).get(
                       "verified") and not r["void"] for r in done(a, "S2"))
                   for a in ("N0", "S", "L", "P")})
    checks.update({
        "scenario 2 O: sequential oracle completed": bool(done("O", "S2")),
        "no channel command was refused by the host (any arm)": not any(
            r.get("channel_denials", 0) for r in recs),
        "P: a CLAIM was written in scenario 2": any(
            sum(r.get("claims", {}).get("claims_sent", {}).values()) >= 1
            for r in s2 if r["arm"] == "P"),
        "no instance void": not any(r["void"] for r in s2),
        "paragraphs S, L, P within 10%": lengths_within(),
        "usable sessions at least 90%": (sum(1 for r in recs for s in r["sessions"] if not s["infra"])
                                         >= 0.9 * max(1, sum(len(r["sessions"]) for r in recs))),
        "sessions within the readiness cap": sessions <= SESSION_CAP["readiness"]})
    return {"schema": SCHEMA, "passed": all(checks.values()), "dry_run": bool(args.dry_run),
            "checks": checks, "sessions": sessions, "spent_notional_usd": round(spent, 4),
            "model": args.model, "effort": args.effort, "prompts_sha256": prompts_hash(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "n0_collisions": [{"tag": r["tag"], "collision": r["collision"],
                               "lost_update": r["lost_update"], "duplicate_work": r["duplicate_work"]}
                              for r in recs if r["arm"] == "N0" and r["scenario"] == "S1"],
            "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def check_gate(path: str | None, args: argparse.Namespace) -> str | None:
    if not path:
        return "the pilot needs --gate PATH (the readiness stage's readiness_gate.json)"
    p = Path(path)
    if not p.is_file():
        return f"readiness gate {p} not found: run the readiness stage first"
    try:
        gate = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return f"readiness gate {p} is not valid JSON"
    if gate.get("dry_run"):
        return "the readiness gate came from a --dry-run, not a live readiness stage"
    if not gate.get("passed"):
        return f"the readiness gate did not pass ({[k for k, v in gate['checks'].items() if not v]})"
    for key, want in (("prompts_sha256", prompts_hash()), ("model", args.model),
                      ("effort", args.effort)):
        if gate.get(key) != want:
            return f"the readiness gate was passed with a different {key}; readiness restarts"
    return None


# ---- command line --------------------------------------------------------------------------------
def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Experiment v4 pilot harness (docs/experiment-v4.md)")
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--out", help="directory for raw results (new or empty, outside the repository)")
    ap.add_argument("--dry-run", action="store_true", help="SIMULATION driver: no claude, no cost")
    ap.add_argument("--live", action="store_true",
                    help=f"real headless `claude -p` sessions (subscription); needs {LIVE_ENV}=1")
    ap.add_argument("--freeze", action="store_true", help="print the sha256 freeze table and exit")
    ap.add_argument("--gate", metavar="PATH", help="pilot: the readiness stage's readiness_gate.json")
    ap.add_argument("--model", default=e2e.DEFAULT_MODEL)
    ap.add_argument("--effort", default=DEFAULT_EFFORT)
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    ap.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"at most {MAX_WORKERS}")
    ap.add_argument("--max-sessions", type=int, help="hard session cap (readiness 12, pilot 132)")
    ap.add_argument("--wall-hours", type=float, help="hard wall-time cap (readiness 1.5, pilot 5)")
    ap.add_argument("--notional-usd", type=float, help="stop past this notional cost (readiness 3, pilot 15)")
    ap.add_argument("--seeds", type=int, help="dry run / pilot: use only the first N seeds")
    ap.add_argument("--fast", action="store_true", help="dry run: scaled-down waits")
    ap.add_argument("--simulate-race", action="store_true",
                    help="dry run: the simulated agents write stale content (a lost update)")
    ap.add_argument("--claude", metavar="PATH")
    ap.add_argument("--workdir", metavar="DIR")
    args = ap.parse_args(argv)
    if args.freeze:
        return args
    if not args.stage or not args.out:
        ap.error("--stage and --out are required")
    args.max_calls = 10**6                 # e2e.Runner's own call budget is not used here
    cap = SESSION_CAP[args.stage]
    args.max_sessions = min(args.max_sessions or cap, cap)       # a flag may lower, never raise
    args.wall_hours = min(args.wall_hours or WALL_HOURS[args.stage], WALL_HOURS[args.stage])
    args.notional_usd = min(args.notional_usd or NOTIONAL_USD[args.stage], NOTIONAL_USD[args.stage])
    return args


def refuse(msg: str) -> int:
    print(f"refused: {msg}", file=sys.stderr)
    return 2


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze() -> int:
    here = Path(__file__).resolve()
    rows = [("scripts/experiment_v4.py", here), ("scripts/analyze_v4.py", here.parent / "analyze_v4.py"),
            ("test_experiment_v4.py", ROOT / "test_experiment_v4.py"),
            ("scripts/experiment_v2.py (imported)", here.parent / "experiment_v2.py"),
            ("scripts/e2e_claude.py (imported)", here.parent / "e2e_claude.py"),
            ("scripts/experiment_micro.py (imported by v2)", here.parent / "experiment_micro.py"),
            ("agent_mail.py (the P arm's tool, as installed)", ROOT / "agent_mail.py"),
            ("install.py", ROOT / "install.py")]
    print("| What | SHA-256 |\n|---|---|")
    for name, p in rows:
        print(f"| `{name}` | `{sha(p) if p.is_file() else 'MISSING'}` |")
    print(f"| arm prompts (`prompts_hash()`) | `{prompts_hash()}` |")
    return 0


def print_plan(args: argparse.Namespace, timing: Timing) -> None:
    n = len(READINESS_PLAN) if args.stage == "readiness" else len(SEEDS["pilot"]) * 5 + len(OBSERVATION_PLAN)
    print(f"stage={args.stage} model={args.model} effort={args.effort} runs={n} planned sessions="
          f"{planned_sessions(args.stage)} cap={args.max_sessions} wall<={args.wall_hours}h "
          f"notional<=${args.notional_usd:.2f}")
    chars = paragraph_chars()
    print("paragraph characters (agent-a/agent-b): " + ", ".join(
        f"{a}={chars[a][A]}/{chars[a][B]}" for a in ARMS) + f"; S, L, P within 10%: {lengths_within()}")
    print(f"timing: {timing}")
    print(f"prompts sha256 {prompts_hash()}")


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.freeze:
        return freeze()
    if args.dry_run == args.live:
        return refuse("pass exactly one of --dry-run (SIMULATION driver, free) or --live")
    if args.live and os.environ.get(LIVE_ENV) != "1":
        return refuse(f"--live uses the subscription; set {LIVE_ENV}=1 to confirm")
    if platform.system() == "Windows":
        return refuse("POSIX only (process groups, SIGKILL)")
    out = Path(args.out).resolve()
    if out == ROOT or ROOT in out.parents:
        return refuse(f"--out {out} is inside the repository; raw data stays outside it")
    if out.exists() and any(out.iterdir()):
        return refuse(f"--out {out} exists and is not empty")
    timing = FAST_TIMING if (args.dry_run and args.fast) else LIVE_TIMING
    print_plan(args, timing)
    offline = offline_checks()
    failed = [n for n, ok in offline if not ok]
    if failed:
        return refuse(f"offline checks failed: {failed}")
    if not lengths_within():
        return refuse("S, L and P arm paragraphs differ by more than 10%")
    work = e2e.prepare_workdir(Path(args.workdir) if args.workdir
                               else Path(tempfile.gettempdir()) / f"v4-work-{os.getpid()}")
    out.mkdir(parents=True, exist_ok=True)
    try:
        rules = verify_rules(work, Runner(args, "claude", work).env(None))
        bad = [n for n, ok in rules if not ok]
        if bad:
            return refuse(f"the P arm's allow rules are not verified: {bad}")
        if args.live:
            if args.stage == "pilot":
                why = check_gate(args.gate, args)
                if why:
                    return refuse(why)
            claude = args.claude or shutil.which("claude")
            if not claude:
                return refuse("`claude` is not on PATH")
            _, reason = e2e.claude_status(claude)
            if reason:
                return refuse(reason)
            driver: Any = LiveDriver()
        else:
            claude = "sim"
            driver = SimDriver(stale_write=args.simulate_race)
            print("SIMULATION: fake agents, no model, no cost")
        h = Harness(args, claude, work, out, driver, timing)
        if args.stage == "readiness":
            plan = list(READINESS_PLAN)
        else:
            plan = pilot_plan(SEEDS["pilot"][:args.seeds] if args.seeds else SEEDS["pilot"])
        records = h.run_plan(plan)
        (out / "run_summary.json").write_text(json.dumps({
            "schema": SCHEMA, "stage": args.stage, "sessions": h.n_sessions,
            "spent_notional_usd": round(h.spent, 4), "stopped": h.stop, "simulation": args.dry_run,
            "wall_s": round(time.monotonic() - h.t_begin, 1)}, indent=2, sort_keys=True))
        if args.stage == "readiness":
            gate = readiness_gate(records, args, offline, rules, h.spent, h.n_sessions)
            (out / "readiness_gate.json").write_text(json.dumps(gate, indent=2, sort_keys=True))
            print(f"readiness gate passed={gate['passed']} -> {out / 'readiness_gate.json'}")
            print(json.dumps(gate["checks"], indent=2))
        print(f"sessions={h.n_sessions} notional ${h.spent:.4f}; raw results in {out}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
