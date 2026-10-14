#!/usr/bin/env python3
"""Experiment v2 harness (docs/experiment-v2.md). Built and tested OFFLINE; a live run spends usage.

    python scripts/experiment_v2.py --stage smoke --out DIR --dry-run
    python scripts/experiment_v2.py --stage smoke --out DIR --live      # POSTBOX_EXPERIMENT_LIVE=1
    python scripts/experiment_v2.py --stage main  --out DIR --live --gate SMOKE_OUT/smoke_gate.json

What it does. One *instance* is a seeded `ledger/` project (D drawn per seed, a `VOID-xxxx`
token, a vendor sample that only agent-a's checkout holds). Every arm (P, N2, N1, N0, O)
runs on every instance in two separate checkouts with a shared channel directory. S1
agent-b, S2 agent-a, S3 agent-b; the hidden suites then score A's `ledger/parse.py` and B's
`ledger/report.py` from files only; a failing b_ok gets one scripted S4 nudge (never part
of the primary outcome). One record per run goes to OUT/runs, every session's raw event
stream to OUT/raw. The analysis is a separate, hashed script (scripts/analyze_v2.py).

NEVER A LIVE CALL BY ACCIDENT. Without `--dry-run` or `--live` the script refuses. `--live`
also needs POSTBOX_EXPERIMENT_LIVE=1. `--dry-run` uses a fake session driver: no `claude`,
no account, no cost. Tests use only the fake driver.

SPEND. The host-reported cost of every session is summed with scripts/experiment_micro.py's
guard: a session is not started if spent plus 1.5 times the largest session so far (times
the sessions in flight, with workers) would pass --ceiling-usd (default 4 for smoke, 30 for
main). The smoke stage writes OUT/smoke_gate.json; the main stage refuses without a passing
gate whose arm prompts, model and effort match. Exit status: 0 finished; 2 refused or
usage error. POSIX only. Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2e_claude as e2e  # noqa: E402
import experiment_micro as micro  # noqa: E402

ARMS = ("P", "N2", "N1", "N0", "O")
A, B = micro.A, micro.B
DEST = e2e.DEST
SCHEMA = 2
MAX_TURNS = 30
DEFAULT_EFFORT = "medium"
CEILING = {"smoke": 4.0, "main": 30.0}
SEEDS = {"smoke": [9001, 9002, 9003], "main": list(range(3001, 3061))}
ORACLE_REPEATS = {"smoke": 3, "main": 1}
BLOCK = 10
MAX_WORKERS = 4
GATE_MEAN_USD = 0.034
GATE_SESSIONS_MAIN = 990              # main sessions plus 10% repeats (doc section 11)
GATE_FACTOR = 1.5
DS = (250, 400, 640, 800, 1250, 2000)
LIVE_ENV = "POSTBOX_EXPERIMENT_LIVE"
NOTES_REL = "NOTES.md"
# Each agent's checkout sits in its own container directory, so a `find ..` shows only its own
# tree; the first live smoke run showed agent-b listing its parent and reading agent-a's sample.
TREE = {"agent-a": "checkout_of_agent_a", "agent-b": "checkout_of_agent_b"}
COST_SCENARIOS = {"low": 0.0075, "likely": 0.020, "high": 0.060}   # USD per session (doc 11)
S4_PER_INSTANCE = 2                                                  # assumed average (doc 11)
BACKOFF = (60, 180)                                                  # seconds, per rate limit

# ---- the prompts: one author, fixed before the freeze; their hash goes in the gate ------
SHARED_LINE = ("You and the other agent are building this together. Tell a teammate what "
               "they will need, using whatever the project provides.")
INTRO = {A: "You are agent-a on a two-agent team building a ledger toolkit. Your checkout is "
            "your current directory; TICKETS.md lists both tickets and you do Ticket A. "
            "Run tests with `python3 -m unittest discover tests`. Run each shell command as its own "
            "Bash call (no `cd`, `;`, `&&`, loops or pipes) and use Read to read files.",
         B: "You are agent-b on a two-agent team building a ledger toolkit. Your checkout is "
            "your current directory; TICKETS.md lists both tickets and you do Ticket B. "
            "Run tests with `python3 -m unittest discover tests`. Run each shell command as its own "
            "Bash call (no `cd`, `;`, `&&`, loops or pipes) and use Read to read files."}
TASK = {"s1": "Do your ticket now; stop when you have done what you can in this session.",
        "s2": "Do your ticket now; stop when you have done what you can in this session.",
        "s3": "Continue your ticket and finish it.",
        "s4": "Check whether a teammate has left you information, then finish your ticket."}
ORACLE_FACT = ("Facts from the vendor's sample: amounts are integers in units of 1/{d} of the "
               "currency, and voided rows have status {token}.")
PARAGRAPHS = {
    "P": ("Team mail uses `python3 tools/agent-postbox/agent_mail.py`. To tell {other} "
          "something, run `python3 tools/agent-postbox/agent_mail.py send --type NOTICE "
          "--from {me} --to {other} --subject '<subject>' --body '<text>'`. To answer a "
          "message, use the Reply command it shows. Run each mail command as its own Bash call."),
    "N2": ("Team notes live in ../../shared/NOTES.md, a file you both can edit. To tell {other} "
           "something, add one line to it with the Edit or Write tool, keeping the earlier lines. "
           "Add one line for each thing a teammate will need, and keep each note to one line of "
           "plain text. Leave the heading '# Team notes' as it is."),
    "N1": ("Team notes live in ../../shared/NOTES.md, a file you both can edit. To tell {other} "
           "something, add one line to it with the Edit or Write tool, keeping the earlier lines. "
           "Add one line for each thing a teammate will need, and keep each note to one line of "
           "plain text. Leave the heading '# Team notes' as it is. At the start of each session, "
           "read it."),
    "N0": "",
    "O": "",
}
# One allow-list for every arm (the P-only addition is the mail command itself).
COMMON_ALLOW = (
    "Read", "Edit", "Write",
    "Bash(python3 -m unittest *)", "Bash(python3 -m unittest)", "Bash(python3 tests/*)",
    "Bash(ls *)", "Bash(ls)", "Bash(pwd)", "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
    "Bash(grep *)", "Bash(find *)", "Bash(wc *)", "Bash(sed -n *)", "Bash(echo *)")
NOTES_PATH = "../../shared/NOTES.md"      # from either checkout: root/<container>/<a|b> -> root/shared
README_NOTES_LINE = f"team notes: {NOTES_PATH}\n"


def arm_order(seed: int) -> list[str]:
    order = list(ARMS)
    random.Random(f"order:{seed}").shuffle(order)
    return order


def paragraph(arm: str, who: str) -> str:
    me, other = (A, B) if who == A else (B, A)
    return PARAGRAPHS[arm].format(me=me, other=other)


def paragraph_chars() -> dict[str, dict[str, int]]:
    """Recorded character counts of the arm paragraphs (token counts only if the host
    reports them: it reports usage per session, not a tokenizer, so none is invented)."""
    return {arm: {who: len(paragraph(arm, who)) for who in (A, B)} for arm in ARMS}


def lengths_within(tolerance: float = 0.10) -> bool:
    """Validity gate 4: the P and N2 paragraphs are within 10% of each other, per role."""
    return all(abs(p - n) <= tolerance * max(p, n) for p, n in
               ((paragraph_chars()["P"][w], paragraph_chars()["N2"][w]) for w in (A, B)))


def prompts_hash() -> str:
    blob = json.dumps({"shared": SHARED_LINE, "intro": INTRO, "task": TASK,
                       "paragraphs": PARAGRAPHS, "oracle": ORACLE_FACT, "allow": COMMON_ALLOW},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def session_prompt(arm: str, who: str, stage: str, inst: Instance) -> str:
    parts = [INTRO[who], TASK[stage]]
    if arm == "O":
        parts.append(ORACLE_FACT.format(d=inst.d, token=inst.token))
    else:
        parts.append(SHARED_LINE)
        para = paragraph(arm, who)
        if para:
            parts.append(para)
    return " ".join(parts)


def allowed(arm: str, proj: Path) -> tuple[str, ...]:
    if arm != "P":
        return COMMON_ALLOW
    rel = f"{DEST}/agent_mail.py"
    absolute = str(proj / rel)
    forms = [rel, f'"{absolute}"', f"'{absolute}'", absolute]
    return COMMON_ALLOW + tuple(f"Bash(python3 {f} *)" for f in forms)


# ---- the instance: project, vendor sample, reference solutions, hidden suites ---------------
README = ("# ledger\n\nSmall toolkit for vendor ledger exports. `ledger/models.py` holds `Row`; "
          "`ledger/parse.py` reads an export; `ledger/report.py` summarises rows. The vendor's "
          "sample export is too large to commit; it is `vendor/sample_export.csv` in the "
          "checkout of whoever received it.\n")
TICKETS = (
    "# Tickets\n\n"
    "## Ticket A (agent-a)\n"
    "Implement `ledger/parse.py: parse_export(path)` for the vendor's CSV export: return a list\n"
    "of `Row(ref, account, amount, status)` from `ledger/models.py`, one per data row, in file\n"
    "order, with `amount` an `int` exactly as the file states it and `status` the raw string.\n"
    "The export starts with a comment header block (lines beginning `#`), then a column\n"
    "header line. `vendor/sample_export.csv` (your checkout only) is a sample.\n\n"
    "## Ticket B (agent-b)\n"
    "Implement `ledger/report.py: format_total(rows)` and `summarise(rows)`.\n"
    "`format_total(rows)` returns the total of the live rows in the currency's own unit as a\n"
    "string with two decimals (round half up); voided rows are skipped. `summarise(rows)`\n"
    "returns `{'count': live rows, 'voided': voided rows, 'total': format_total(rows)}`.\n"
    "Your work depends on the vendor's unit for `amount` and on the marker the vendor puts on\n"
    "voided rows. Only the vendor's sample, which is in agent-a's checkout, states them.\n")
MODELS = ('from dataclasses import dataclass\n\n\n@dataclass(frozen=True)\nclass Row:\n'
          '    ref: str\n    account: str\n    amount: int\n    status: str\n')
ACCOUNTS = ('ACCOUNTS = ("ops", "sales", "infra", "legal")\n\n\n'
            'def known(account: str) -> bool:\n    return account in ACCOUNTS\n')
STUB_PARSE = ('from pathlib import Path\n\nfrom ledger.models import Row\n\n\n'
              'def parse_export(path: str | Path) -> list[Row]:\n    raise NotImplementedError\n')
STUB_REPORT = ('from ledger.models import Row\n\n\n'
               'def format_total(rows: list[Row]) -> str:\n    raise NotImplementedError\n\n\n'
               'def summarise(rows: list[Row]) -> dict[str, object]:\n'
               '    raise NotImplementedError\n')
VISIBLE_TEST = ('import unittest\n\nfrom ledger.accounts import known\nfrom ledger.models '
                'import Row\n\n\nclass TestBasics(unittest.TestCase):\n'
                '    def test_row(self) -> None:\n'
                '        self.assertEqual(Row("r1", "ops", 5, "OK").amount, 5)\n\n'
                '    def test_known(self) -> None:\n'
                '        self.assertTrue(known("ops"))\n\n\n'
                'if __name__ == "__main__":\n    unittest.main()\n')
REF_PARSE = (
    'import csv\nfrom pathlib import Path\n\nfrom ledger.models import Row\n\n\n'
    'def parse_export(path: str | Path) -> list[Row]:\n'
    '    lines = [ln for ln in Path(path).read_text().splitlines()\n'
    '             if ln.strip() and not ln.startswith("#")]\n'
    '    return [Row(r["ref"].strip(), r["account"].strip(), int(r["amount"]),\n'
    '                r["status"].strip()) for r in csv.DictReader(lines)]\n')
REF_REPORT = (
    'from decimal import ROUND_HALF_UP, Decimal\n\nfrom ledger.models import Row\n\n'
    'D = {d}\nVOID = "{token}"\n\n\n'
    'def _live(rows: list[Row]) -> list[Row]:\n'
    '    return [r for r in rows if r.status != VOID]\n\n\n'
    'def format_total(rows: list[Row]) -> str:\n'
    '    total = Decimal(sum(r.amount for r in _live(rows))) / Decimal(D)\n'
    '    return str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))\n\n\n'
    'def summarise(rows: list[Row]) -> dict[str, object]:\n'
    '    live = _live(rows)\n'
    '    return {{"count": len(live), "voided": len(rows) - len(live),\n'
    '            "total": format_total(rows)}}\n')
# The natural guess B makes without the vendor's facts: cents, nothing voided.
WRONG_REPORT = REF_REPORT.replace("D = {d}", "D = 100").replace(
    'r.status != VOID]', 'True]')
HIDDEN_PARSE = (
    'import tempfile\nimport unittest\nfrom pathlib import Path\n\n'
    'from ledger.models import Row\nfrom ledger.parse import parse_export\n\n'
    'TEXT = {text!r}\nROWS = {rows!r}\n\n\n'
    'class TestParse(unittest.TestCase):\n'
    '    def load(self) -> list[Row]:\n'
    '        with tempfile.TemporaryDirectory() as d:\n'
    '            p = Path(d) / "export.csv"\n'
    '            p.write_text(TEXT)\n'
    '            return parse_export(p)\n\n'
    '    def test_parse_rows(self) -> None:\n'
    '        self.assertEqual(self.load(), [Row(*r) for r in ROWS])\n\n'
    '    def test_parse_types(self) -> None:\n'
    '        got = self.load()\n'
    '        self.assertTrue(got and all(type(r.amount) is int for r in got))\n\n\n'
    'if __name__ == "__main__":\n    unittest.main()\n')
HIDDEN_REPORT = (
    'import unittest\n\nfrom ledger.models import Row\n'
    'from ledger.report import format_total, summarise\n\n'
    'LIVE = [Row(*r) for r in {live!r}]\nVOIDED = [Row(*r) for r in {voided!r}]\n'
    'TOTAL = {total!r}\n\n\n'
    'class TestReport(unittest.TestCase):\n'
    '    def test_shape(self) -> None:\n'
    '        self.assertEqual(format_total([]), "0.00")\n'
    '        self.assertEqual(sorted(summarise([])), ["count", "total", "voided"])\n\n'
    '    def test_unit_total(self) -> None:\n'
    '        self.assertEqual(format_total(LIVE), TOTAL)\n'
    '        self.assertEqual(summarise(LIVE)["total"], TOTAL)\n\n'
    '    def test_void_skipped(self) -> None:\n'
    '        self.assertEqual(format_total(VOIDED), "0.00")\n'
    '        s = summarise(LIVE + VOIDED)\n'
    '        self.assertEqual((s["count"], s["voided"]), (len(LIVE), len(VOIDED)))\n\n\n'
    'if __name__ == "__main__":\n    unittest.main()\n')

Row4 = tuple[str, str, int, str]


def render_export(d: int, token: str, rows: list[Row4]) -> str:
    head = ("# VENDOR EXPORT v3 (ledger feed)\n"
            f"# amounts are integers in units of 1/{d} of the currency\n"
            f"# voided rows carry status {token}\n")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["ref", "account", "amount", "status"])
    w.writerows(rows)
    return head + buf.getvalue()


def make_rows(rnd: random.Random, n: int, voids: int, token: str, prefix: str) -> list[Row4]:
    accounts = ("ops", "sales", "infra", "legal")
    rows: list[Row4] = []
    for i in range(n):
        status = token if i < voids else rnd.choice(("OK", "PENDING", "SETTLED"))
        rows.append((f"{prefix}{i + 1:04d}", rnd.choice(accounts), rnd.randrange(1_000, 90_000),
                     status))
    rnd.shuffle(rows)
    return rows


def exact_total(live: list[Row4], d: int) -> str:
    total = Decimal(sum(r[2] for r in live)) / Decimal(d)
    return str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass
class Instance:
    seed: int
    d: int
    token: str
    sample: str
    hidden_parse: str
    hidden_report: str

    def base_files(self) -> dict[str, str]:
        return {"README.md": README, "TICKETS.md": TICKETS, "ledger/__init__.py": "",
                "ledger/models.py": MODELS, "ledger/accounts.py": ACCOUNTS,
                "ledger/parse.py": STUB_PARSE, "ledger/report.py": STUB_REPORT,
                "tests/test_models.py": VISIBLE_TEST}

    def ref_parse(self) -> str:
        return REF_PARSE

    def ref_report(self) -> str:
        return REF_REPORT.format(d=self.d, token=self.token)

    def wrong_report(self) -> str:
        return WRONG_REPORT.format(d=self.d, token=self.token)


def build_instance(seed: int) -> Instance:
    rnd = random.Random(f"ledger:{seed}")
    d = random.Random(f"d:{seed}").choice(DS)
    token = "VOID-" + hashlib.sha256(f"void:{seed}".encode()).hexdigest()[:4]
    sample = render_export(d, token, make_rows(rnd, 40, 6, token, "S"))
    fixture = make_rows(rnd, 12, 3, token, "H")
    live = [r for r in make_rows(rnd, 9, 0, token, "L")]
    while True:   # no total that sits on a rounding tie
        frac = (Decimal(sum(r[2] for r in live)) / Decimal(d) * 100) % 1
        if abs(frac - Decimal("0.5")) > Decimal("0.05") and frac != 0:
            break
        live = make_rows(rnd, 9, 0, token, "L")
    voided = [(f"V{i:04d}", "ops", rnd.randrange(1_000, 90_000), token) for i in range(1, 4)]
    return Instance(
        seed=seed, d=d, token=token, sample=sample,
        hidden_parse=HIDDEN_PARSE.format(text=render_export(d, token, fixture), rows=fixture),
        hidden_report=HIDDEN_REPORT.format(live=live, voided=voided, total=exact_total(live, d)))


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


# ---- scoring: assemble A's and B's files, run the hidden suites ------------------------------
RESULT_LINE = re.compile(r"^(test_\w+) \(.*\) \.\.\. (ok|FAIL|ERROR)", re.M)


def run_suite(tree: Path, module: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "TMPDIR", "SYSTEMROOT", "LANG")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        cp = subprocess.run([sys.executable, "-m", "unittest", "-v", module], cwd=tree, env=env,
                            capture_output=True, text=True, timeout=60)
        out = cp.stderr + cp.stdout
    except subprocess.TimeoutExpired:
        return {"__timeout__": "ERROR"}
    found = dict(RESULT_LINE.findall(out))
    if not found:                                   # the module did not even import
        found["__import__"] = "ERROR"
    return found


def score(inst: Instance, a_dir: Path, b_dir: Path) -> dict[str, Any]:
    """Assemble A's parse.py and B's report.py into a clean tree (the shared files are
    pristine) and run the hidden suites. Nothing is read from model text."""
    with tempfile.TemporaryDirectory(prefix="v2-score-") as tmp:
        tree = Path(tmp)
        write_tree(tree, inst.base_files())
        for src, rel in ((a_dir, "ledger/parse.py"), (b_dir, "ledger/report.py")):
            if (src / rel).is_file():
                shutil.copyfile(src / rel, tree / rel)
        write_tree(tree, {"hidden_parse.py": inst.hidden_parse,
                          "hidden_report.py": inst.hidden_report})
        pa, pb = run_suite(tree, "hidden_parse"), run_suite(tree, "hidden_report")
    a_ok = bool(pa) and all(v == "ok" for v in pa.values())
    unit, void = pb.get("test_unit_total") == "ok", pb.get("test_void_skipped") == "ok"
    shape = pb.get("test_shape") == "ok"
    b_ok = shape and unit and void
    return {"a_ok": a_ok, "b_ok": b_ok, "b_unit_ok": unit, "b_void_ok": void,
            "completed": a_ok and b_ok,
            "b_wrong_default": (not b_ok) and shape and not (unit and void),
            "b_missing": (not b_ok) and not shape,
            "suites": {"parse": pa, "report": pb}}


def offline_checks() -> list[tuple[str, bool]]:
    """Section 11, smoke item 1 (free): reference solutions pass; the wrong default fails; a
    leak is detected. Used by the tests, by --dry-run and as part of the smoke gate."""
    inst = build_instance(9001)
    with tempfile.TemporaryDirectory(prefix="v2-self-") as tmp:
        a, b = Path(tmp, "a"), Path(tmp, "b")
        write_tree(a, {"ledger/parse.py": inst.ref_parse()})
        write_tree(b, {"ledger/report.py": inst.ref_report()})
        ref = score(inst, a, b)
        write_tree(b, {"ledger/report.py": inst.wrong_report()})
        wrong = score(inst, a, b)
        leaked = Path(tmp, "leak")
        write_tree(leaked, {"notes.txt": f"see {inst.token}"})
        try:
            assert_no_leak(leaked, inst.token)
            detected = False
        except RuntimeError:
            detected = True
    return [("reference solutions pass the hidden suites", bool(ref["completed"])),
            ("wrong default (D=100, no void handling) fails", wrong["b_wrong_default"]
             and not wrong["b_unit_ok"] and not wrong["b_void_ok"] and wrong["a_ok"]),
            ("a leaked token in B's tree is detected", detected)]


def scan(root: Path, needles: tuple[str, ...]) -> list[str]:
    hits = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            text = p.read_text(encoding="utf-8", errors="replace")
            hits += [f"{p.relative_to(root)}: {n}" for n in needles if n in text]
    return hits


def assert_no_leak(b_tree: Path, token: str) -> None:
    hits = scan(b_tree, (token, "sample_export.csv headers"))
    if (b_tree / "vendor").exists():
        hits.append("vendor/ exists in B's checkout")
    if hits:
        raise RuntimeError(f"B's checkout leaks the vendor facts: {hits}")


def assert_mailbox_omitted(tree: Path) -> None:
    """Non-P arms are built without the mailbox tool or mailbox; fail loudly otherwise."""
    bad = [str(p) for p in (tree / DEST, tree / "docs" / "agent-mail", tree / ".claude")
           if p.exists()]
    bad += scan(tree, ("agent_mail", "agent-postbox"))
    if bad:
        raise RuntimeError(f"mailbox leaked into a non-P project {tree}: {bad[:5]}")


# ---- sessions: records, events, drivers ------------------------------------------------------
@dataclass
class XSession(e2e.Session):
    tools: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    model_usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class Spec:
    tag: str
    arm: str
    who: str
    stage: str
    proj: Path
    prompt: str
    env: dict[str, str]
    cmd: list[str]
    inst: Instance
    shared: Path


@dataclass
class Raw:
    stdout: str
    stderr: str = ""
    rc: int | None = 0
    timed_out: bool = False


class Driver(Protocol):
    def run(self, spec: Spec) -> Raw: ...


class LiveDriver:
    """The only code that starts `claude`. Reached only from --live with POSTBOX_EXPERIMENT_LIVE=1."""

    def run(self, spec: Spec) -> Raw:   # pragma: no cover - never run in tests
        try:
            cp = subprocess.run(spec.cmd, input=spec.prompt, cwd=spec.proj, capture_output=True,
                                text=True, env=spec.env, timeout=e2e.TIMEOUT)
            return Raw(cp.stdout, cp.stderr, cp.returncode)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            return Raw(out, "", None, True)


def tool_events(text: str) -> list[dict[str, Any]]:
    out = []
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "assistant":
            for item in (ev.get("message") or {}).get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    out.append({"name": item.get("name"), "input": item.get("input") or {}})
    return out


def reads_notes(tools: list[dict[str, Any]]) -> bool:
    """A Read of NOTES.md, or a shell read of it (a pure `>>` append is not a read)."""
    for t in tools:
        blob = json.dumps(t.get("input"))
        if NOTES_REL not in blob:
            continue
        if t.get("name") == "Read":
            return True
        cmd = str((t.get("input") or {}).get("command") or "")
        if t.get("name") == "Bash" and re.search(r"\b(cat|head|tail|grep|sed|awk|less|more)\b", cmd):
            return True
    return False


def runs_show(tools: list[dict[str, Any]]) -> bool:
    return any(t.get("name") == "Bash" and "agent_mail.py" in str(t["input"].get("command"))
               and re.search(r"agent_mail\.py['\"]?\s+show\b", str(t["input"].get("command")))
               for t in tools)


def isolation_rules(root: Path, other: str) -> tuple[str, ...]:
    """Deny rules that keep a session out of the OTHER agent's checkout: the Read, Edit and
    Write tools on its container directory, and any Bash command that names it. Checked live
    (2026-10-14): the file tools and `cat`/`find` naming the container are refused."""
    c = (root / TREE[other]).resolve()
    return (f"Read(/{c}/**)", f"Edit(/{c}/**)", f"Write(/{c}/**)", f"Bash(*{TREE[other]}*)")


def crossed(tools: list[dict[str, Any]], other: str) -> bool:
    """Whether any tool call of this session named the other agent's container (an attempt;
    the deny rules refuse it, and the flag records that it was tried)."""
    return any(TREE[other] in json.dumps(t.get("input")) for t in tools)


class Harness(micro.Experiment):
    """Reuses the micro-experiment's guard, Ceiling and the e2e Runner's environment and
    event parsing; adds the instance, the scoring and the scheduling."""

    def __init__(self, args: argparse.Namespace, claude: str, work: Path, out: Path,
                 driver: Driver, sleep: Any = time.sleep) -> None:
        super().__init__(args, claude, work, out)
        self.driver = driver
        self.sleep = sleep
        self.lock = threading.RLock()
        self.inflight = 0
        self.rl_streak = 0
        self.stop: str | None = None
        self.n_sessions = 0

    # reserve/guard under the lock: a session in flight counts as a worst case too
    def reserve(self) -> None:
        with self.lock:
            if self.stop:
                raise micro.Ceiling(self.stop)
            self.guard()
            worst = (self.biggest or micro.FIRST_SESSION_ESTIMATE) * 1.5
            if self.spent + worst * (self.inflight + 1) > self.args.ceiling_usd:
                raise micro.Ceiling(f"spent ${self.spent:.4f} + {self.inflight + 1} in flight "
                                    f"x ${worst:.4f} would pass ${self.args.ceiling_usd:.2f}")
            self.inflight += 1

    def absorb(self, s: e2e.Session, ev: dict[str, Any]) -> None:
        e2e.Runner.absorb(self, s, ev)
        if isinstance(s, XSession) and ev.get("type") == "result":
            s.usage = dict(ev.get("usage") or {})
            s.model_usage = dict(ev.get("modelUsage") or {})

    def command(self, arm: str, proj: Path, shared: Path,
                deny: tuple[str, ...] = ()) -> list[str]:
        cmd = self.base_cmd(tools="Bash,Read,Edit,Write", allowed=(), max_turns=self.args.max_turns)
        cmd[cmd.index("--effort") + 1] = self.args.effort
        if arm in ("N2", "N1"):
            cmd += ["--add-dir", str(shared)]
        if deny:
            cmd += ["--disallowedTools", *deny]
        return cmd + ["--allowedTools", *allowed(arm, proj)]

    def one_session(self, spec: Spec, tag_dir: str) -> tuple[XSession, dict[str, Any]]:
        self.reserve()
        s = XSession()
        t0 = time.monotonic()
        try:
            raw = self.driver.run(spec)
        finally:
            with self.lock:
                self.inflight -= 1
        secs = time.monotonic() - t0
        s.rc, s.stderr, s.timed_out = raw.rc, raw.stderr, raw.timed_out
        for line in raw.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                self.absorb(s, ev)
        s.tools = tool_events(raw.stdout)
        raw_path = self.out / "raw" / f"{tag_dir}-{spec.tag}.jsonl"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw.stdout, encoding="utf-8")
        cost = s.cost_usd or 0.0
        with self.lock:
            self.n_sessions += 1
            self.spent += cost
            self.biggest = max(self.biggest, cost)
            self.rl_streak = self.rl_streak + 1 if s.rate_limited else 0
            if self.rl_streak >= 2:
                self.stop = "two rate-limit rejections in a row"
        infra = ("rate limited" if s.rate_limited else "timeout" if s.timed_out else
                 "host failure" if (s.rc not in (0, None) and not s.result) else "")
        rec = {"tag": spec.tag, "agent": spec.who, "arm": spec.arm,
               "seconds": round(secs, 1), "cost_usd": s.cost_usd, "turns": s.num_turns,
               "rc": s.rc, "timed_out": s.timed_out, "is_error": s.is_error,
               "rate_limited": s.rate_limited, "infra": infra, "denials": len(s.denials),
               "model": s.model, "usage": s.usage, "model_usage": s.model_usage,
               "raw_events": str(raw_path), "prompt_chars": len(spec.prompt),
               "result": s.result[:400], "tools": [t["name"] for t in s.tools],
               "reads_notes": reads_notes(s.tools), "runs_show": runs_show(s.tools),
               "cross_checkout": crossed(s.tools, A if spec.who == B else B),
               "mentions_mailbox": "agent_mail" in raw.stdout or "tools/agent-postbox" in raw.stdout,
               "hook_stdout": s.hook_stdout()[:2000]}
        return s, rec

    # ---- one run (one arm on one instance) ---------------------------------------------
    def build(self, inst: Instance, arm: str, root: Path) -> tuple[Path, Path, Path, dict[str, str]]:
        a, b, shared = root / TREE[A] / "a", root / TREE[B] / "b", root / "shared"
        for d in (a, b, shared):
            d.mkdir(parents=True)
        files = inst.base_files()
        if arm in ("N2", "N1"):
            files["README.md"] += README_NOTES_LINE
        extra: dict[str, str] = {}
        for tree in (a, b):
            if arm == "P":
                cmd = [sys.executable, "-I", str(e2e.INSTALL), str(tree), "--adapter", "claude-code"]
                subprocess.run(cmd, check=True, capture_output=True, text=True, env=self.env(None))
            write_tree(tree, files)
        if arm == "P":
            shutil.move(str(a / "docs" / "agent-mail"), str(shared / "agent-mail"))
            shutil.rmtree(b / "docs" / "agent-mail")
            for tree in (a, b):
                if not any((tree / "docs").iterdir()):
                    (tree / "docs").rmdir()
            extra = {"AGENT_MAIL_DIR": str(shared / "agent-mail")}
        else:
            for tree in (a, b):
                assert_mailbox_omitted(tree)
        if arm in ("N2", "N1"):
            # a real file in the shared directory, reached by the relative path in the arm
            # paragraph: the host's Edit tool refuses a symlink, and shell redirects (`>>`) are
            # refused in this permission mode (both found by the smoke stage)
            (shared / NOTES_REL).write_text("# Team notes\n", encoding="utf-8")
        write_tree(a, {"vendor/sample_export.csv": inst.sample})
        assert_no_leak(b, inst.token)
        return a, b, shared, extra

    def mailbox(self, a: Path, extra: dict[str, str]) -> list[dict[str, Any]]:
        cp = subprocess.run([sys.executable, "-I", str(a / DEST / "agent_mail.py"), "list",
                             "--json"], cwd=a, capture_output=True, text=True,
                            env=self.env(None, extra))
        try:
            return list(json.loads(cp.stdout)["messages"])
        except (ValueError, KeyError):
            return []

    def run_arm(self, seed: int, arm: str, rep: int = 0, attempt: int = 1) -> dict[str, Any]:
        inst = build_instance(seed)
        rec: dict[str, Any] = {
            "schema": SCHEMA, "seed": seed, "arm": arm, "rep": rep, "attempt": attempt,
            "d": inst.d, "token": inst.token, "sessions": [], "manual_nudges": 0,
            "paragraph_chars": paragraph_chars()[arm], "infra_failure": "",
            "model": self.args.model, "effort": self.args.effort}
        tag_dir = f"{seed}-{arm}-r{rep}-t{attempt}"
        root = Path(tempfile.mkdtemp(prefix=f"v2-{tag_dir}-", dir=self.work))
        t0 = time.monotonic()
        try:
            a, b, shared, extra = self.build(inst, arm, root)
            plan = ([("o", B)] if arm == "O" else [("s1", B), ("s2", A), ("s3", B)])

            def go(stage: str, who: str, proj: Path) -> XSession:
                other = A if who == B else B
                spec = Spec(stage, arm, who, "s1" if stage == "o" else stage, proj,
                            session_prompt(arm, who, "s1" if stage == "o" else stage, inst),
                            self.env(who, extra),
                            self.command(arm, proj, shared, isolation_rules(root, other)), inst,
                            shared)
                s, srec = self.one_session(spec, tag_dir)
                rec["sessions"].append(srec)
                if srec["infra"] and not rec["infra_failure"]:
                    rec["infra_failure"] = srec["infra"]
                return s

            for stage, who in plan:
                go(stage, who, a if who == A else b)
                if rec["infra_failure"]:
                    break
            if not rec["infra_failure"]:
                rec.update(self.score_run(inst, arm, a, b, shared, extra, rec))
                if arm != "O" and not rec["b_ok"]:
                    go("s4", B, b)
                    after = score(inst, a, b)
                    rec["s4"] = {"ran": True, "b_ok_after": after["b_ok"],
                                 "cost_usd": rec["sessions"][-1]["cost_usd"],
                                 "seconds": rec["sessions"][-1]["seconds"]}
                else:
                    rec["s4"] = {"ran": False, "b_ok_after": None, "cost_usd": 0.0, "seconds": 0.0}
        except micro.Ceiling as e:
            rec["aborted"] = f"stopped: {e}"
        finally:
            shutil.rmtree(root, ignore_errors=True)
        rec["seconds"] = round(time.monotonic() - t0, 1)
        rec["cost_usd"] = round(sum(s["cost_usd"] or 0.0 for s in rec["sessions"]), 6)
        rec["denials"] = sum(s["denials"] for s in rec["sessions"])
        rec["cross_checkout"] = any(s["cross_checkout"] for s in rec["sessions"])
        rec["mailbox_mention"] = arm != "P" and any(s["mentions_mailbox"] for s in rec["sessions"])
        runs = self.out / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"{tag_dir}.json").write_text(json.dumps(rec, indent=2, sort_keys=True),
                                              encoding="utf-8")
        return rec

    def score_run(self, inst: Instance, arm: str, a: Path, b: Path, shared: Path,
                extra: dict[str, str], rec: dict[str, Any]) -> dict[str, Any]:
        out = score(inst, a, b)
        s3 = next((s for s in rec["sessions"] if s["tag"] in ("s3", "o")), None)
        gen = 0
        a_wrote = False
        delivered: bool | None = None
        picked: bool | None = None
        ack: bool | None = None
        if arm == "P":
            msgs = self.mailbox(a, extra)
            gen = len(msgs)
            asks = {m["id"] for m in msgs if m.get("type") == "ASK"}
            from_a = [m for m in msgs if m.get("from") == A]
            a_wrote = bool(from_a)
            hook = s3["hook_stdout"] if s3 else ""
            delivered = any(m.get("subject") and m["subject"] in hook for m in from_a)
            picked = bool(s3 and s3["runs_show"])
            ack = any((m.get("type") == "ANSWER" and m.get("reply_to") in asks)
                      or (m.get("from") == B and m.get("reply_to") in {x["id"] for x in from_a})
                      for m in msgs)
        elif arm in ("N2", "N1"):
            notes = (shared / NOTES_REL).read_text(encoding="utf-8")
            gen = max(0, len([ln for ln in notes.splitlines() if ln.strip()]) - 1)
            picked = bool(s3 and s3["reads_notes"])
            a_wrote = gen > 0
        out.update(generated=gen, delivered=delivered, picked_up=picked, acknowledged=ack)
        out["missed"] = a_wrote and not out["b_ok"]
        return out

    # ---- blocks, workers, retries --------------------------------------------------------
    def run_with_retry(self, seed: int, arm: str, rep: int) -> list[dict[str, Any]]:
        recs = [self.run_arm(seed, arm, rep, 1)]
        if recs[0]["infra_failure"] and "aborted" not in recs[0] and not self.stop:
            self.sleep(BACKOFF[0] if recs[0]["infra_failure"] == "rate limited" else 5)
            recs.append(self.run_arm(seed, arm, rep, 2))
        return recs

    def run_instance(self, seed: int) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        reps = ORACLE_REPEATS[self.args.stage]
        for arm in arm_order(seed):
            for rep in range(reps if arm == "O" else 1):
                try:
                    recs = self.run_with_retry(seed, arm, rep)
                except micro.Ceiling as e:
                    records.append({"seed": seed, "arm": arm, "rep": rep, "aborted": str(e)})
                    return records
                records += recs
                if "aborted" in recs[-1] or self.stop:
                    return records
        return records

    def run_block(self, seeds: list[int]) -> list[dict[str, Any]]:
        workers = max(1, min(self.args.workers, MAX_WORKERS, len(seeds)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(self.run_instance, seeds))
        return [r for part in parts for r in part]


def blocks(seeds: list[int], size: int = BLOCK) -> list[list[int]]:
    return [seeds[i:i + size] for i in range(0, len(seeds), size)]


def final_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last attempt of each (seed, arm, rep); earlier attempts stay listed on disk."""
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in records:
        best[(r["seed"], r["arm"], r.get("rep", 0))] = r
    return list(best.values())


def stop_reason(records: list[dict[str, Any]], seeds_done: list[int]) -> str | None:
    """Main-stage stop rules evaluated on the first 20 instances (doc section 11)."""
    first = set(sorted(seeds_done)[:20])
    if len(first) < 20:
        return None
    recs = [r for r in final_records(records) if r["seed"] in first and "aborted" not in r]
    infra = {r["seed"] for r in recs if r.get("infra_failure")}
    if len(infra) > 0.10 * 20:
        return f"{len(infra)} of the first 20 instances ended in infrastructure failure"
    o = [r for r in recs if r["arm"] == "O" and not r.get("infra_failure")]
    n0 = [r for r in recs if r["arm"] == "N0" and not r.get("infra_failure")]
    if o and sum(r["b_ok"] for r in o) / len(o) < 0.80:
        return "validity gate 1 failed on the first 20 instances (O b_ok below 80%)"
    if n0 and sum(r["b_ok"] for r in n0) / len(n0) > 0.15:
        return "validity gate 2 failed on the first 20 instances (N0 b_ok above 15%)"
    return None


# ---- plan, projection, smoke gate ------------------------------------------------------------
def plan_sessions(stage: str) -> dict[str, int]:
    n = len(SEEDS[stage])
    per = 12 + ORACLE_REPEATS[stage]
    s4 = S4_PER_INSTANCE * n
    base = per * n + s4
    repeats = 0 if stage == "smoke" else round(0.10 * base)
    return {"instances": n, "per_instance": per, "s4_assumed": s4, "sessions": base,
            "infra_repeats_assumed": repeats, "total": base + repeats}


def projection(stage: str) -> dict[str, float]:
    total = plan_sessions(stage)["total"]
    return {k: round(v * total, 2) for k, v in COST_SCENARIOS.items()}


def smoke_gate(records: list[dict[str, Any]], spent: float, args: argparse.Namespace,
               offline: list[tuple[str, bool]]) -> dict[str, Any]:
    recs = [r for r in final_records(records) if "aborted" not in r]
    sess = [s for r in recs for s in r["sessions"]]
    costs = [s["cost_usd"] for s in sess if s["cost_usd"] is not None]
    mean = sum(costs) / len(costs) if costs else float("inf")
    proj = mean * GATE_SESSIONS_MAIN * GATE_FACTOR
    o = [r for r in recs if r["arm"] == "O"]
    n0 = [r for r in recs if r["arm"] == "N0"]
    by_arm = {arm: [r["denials"] for r in recs if r["arm"] == arm] for arm in ARMS}
    p_runs = [r for r in recs if r["arm"] == "P"]
    checks = {
        "offline: " + name: ok for name, ok in offline}
    checks.update({
        "pipeline: every session has cost, turns, usage, model": bool(sess) and all(
            s["cost_usd"] is not None and s["turns"] is not None and s["usage"]
            and s["model"] for s in sess),
        "pipeline: P delivered/picked-up/acknowledged are readable": bool(p_runs) and all(
            r.get("delivered") is not None and r.get("picked_up") is not None
            and r.get("acknowledged") is not None for r in p_runs),
        "O reaches b_ok in at least 8 of 9": len(o) >= 9 and sum(r["b_ok"] for r in o) >= 8,
        "N0 reaches b_ok in at most 1 of 3": len(n0) >= 1 and sum(r["b_ok"] for r in n0) <= 1,
        "no mailbox mention in any non-P run": not any(r["mailbox_mention"] for r in recs),
        "P and N2 paragraphs within 10%": lengths_within(),
        "denials average at most 1 per run in every arm": all(
            sum(v) / len(v) <= 1 for v in by_arm.values() if v),
        f"cost gate: mean per session <= ${GATE_MEAN_USD}": mean <= GATE_MEAN_USD,
        f"projection (mean x {GATE_SESSIONS_MAIN} x {GATE_FACTOR}) <= main ceiling "
        f"${args.projection_cap}": proj <= args.projection_cap})
    return {"schema": SCHEMA, "passed": all(checks.values()), "dry_run": bool(args.dry_run),
            "checks": checks, "mean_cost_per_session": round(mean, 6) if costs else None,
            "projected_main_usd": round(proj, 2) if costs else None, "sessions": len(sess),
            "spent_usd": round(spent, 4), "model": args.model, "effort": args.effort,
            "max_turns": args.max_turns, "prompts_sha256": prompts_hash(),
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def check_gate(path: str | None, args: argparse.Namespace) -> str | None:
    """Why the main stage may not start, or None."""
    if not path:
        return "the main stage needs --gate PATH (the smoke stage's smoke_gate.json)"
    p = Path(path)
    if not p.is_file():
        return f"smoke gate {p} not found: run the smoke stage first"
    try:
        gate = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return f"smoke gate {p} is not valid JSON"
    if gate.get("dry_run"):
        return "the smoke gate came from a --dry-run, not a live smoke stage"
    if not gate.get("passed"):
        failed = [k for k, v in (gate.get("checks") or {}).items() if not v]
        return f"the smoke gate did not pass ({failed}); the owner decides, N is not reduced"
    for key, want in (("prompts_sha256", prompts_hash()), ("model", args.model),
                      ("effort", args.effort)):
        if gate.get(key) != want:
            return (f"the smoke gate was passed with a different {key}; a change to arm "
                    "prompts, model or effort restarts smoke")
    return None


# ---- the fake driver: scripted behaviour, real files, the real mail tool and hook ------------
class FakeDriver:
    """A stand-in for the model. It edits files like a (simple) agent, uses the real mailbox CLI
    and the real hook, and emits stream-json events of the host's shape. No model, no cost."""

    def __init__(self, *, pickup: dict[str, bool] | None = None, cost: float = 0.02,
                 fail_first: set[tuple[int, str]] | None = None,
                 rate_limit: set[tuple[int, str]] | None = None,
                 mention_mailbox: set[str] | None = None) -> None:
        self.pickup = {"P": True, "N2": False, "N1": True, "N0": False, "O": True}
        self.pickup.update(pickup or {})
        self.cost = cost
        self.fail_first = set(fail_first or ())
        self.rate_limit = set(rate_limit or ())
        self.mention = set(mention_mailbox or ())
        self.calls: list[Spec] = []
        self.lock = threading.Lock()

    def run(self, spec: Spec) -> Raw:
        with self.lock:
            self.calls.append(spec)
        key = (spec.inst.seed, spec.arm)
        events: list[dict[str, Any]] = [{"type": "system", "subtype": "init",
                                         "session_id": f"fake-{spec.tag}", "model": "fake-model"}]
        limited = key in self.rate_limit
        if limited:
            events.append({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
        elif key in self.fail_first and spec.tag in ("s1", "o"):
            self.fail_first.discard(key)
            return Raw("", "host crashed", 1)
        else:
            self.behave(spec, events)
        events.append({
            "type": "result", "subtype": "success", "is_error": False, "num_turns": 6,
            "result": "done", "total_cost_usd": 0.0 if limited else self.cost,
            "permission_denials": [],
            "usage": {"input_tokens": 1000, "output_tokens": 200,
                      "cache_creation_input_tokens": 300, "cache_read_input_tokens": 4000},
            "modelUsage": {"fake-model": {"inputTokens": 1000, "outputTokens": 200,
                                          "costUSD": self.cost}}})
        return Raw("\n".join(json.dumps(e) for e in events) + "\n")

    @staticmethod
    def use(events: list[dict[str, Any]], name: str, **inp: Any) -> None:
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "input": inp}]}})

    def facts(self, text: str) -> tuple[int, str] | None:
        d = re.search(r"units of 1/(\d+)", text)
        t = re.search(r"VOID-[0-9a-f]{4}", text)
        return (int(d.group(1)), t.group(0)) if d and t else None

    def write_report(self, spec: Spec, events: list[dict[str, Any]], d: int, token: str) -> None:
        fixed = REF_REPORT.format(d=d, token=token)
        self.use(events, "Write", file_path=str(spec.proj / "ledger/report.py"))
        (spec.proj / "ledger" / "report.py").write_text(fixed, encoding="utf-8")

    def behave(self, spec: Spec, events: list[dict[str, Any]]) -> None:
        proj, inst = spec.proj, spec.inst
        if spec.arm in self.mention:
            self.use(events, "Bash", command="python3 tools/agent-postbox/agent_mail.py list")
        if spec.arm == "P":
            hook = subprocess.run(
                [sys.executable, "-I", str(proj / DEST / "hooks" / "agent_mail_check.py")],
                cwd=proj, capture_output=True, text=True, env={
                    **spec.env, "CLAUDE_PROJECT_DIR": str(proj)}).stdout
            events.append({"type": "system", "subtype": "hook_response",
                           "hook_event": "UserPromptSubmit", "stdout": hook})
        if spec.who == A:
            self.use(events, "Read", file_path=str(proj / "vendor/sample_export.csv"))
            self.use(events, "Write", file_path=str(proj / "ledger/parse.py"))
            (proj / "ledger" / "parse.py").write_text(REF_PARSE, encoding="utf-8")
            fact = (f"vendor amounts are integers in units of 1/{inst.d}; "
                    f"voided rows have status {inst.token}")
            if spec.arm == "P":
                self.use(events, "Bash", command=f"python3 {DEST}/agent_mail.py send --type "
                         f"NOTICE --from agent-a --to agent-b --subject 'vendor facts' "
                         f"--body '{fact}'")
                subprocess.run([sys.executable, "-I", str(proj / DEST / "agent_mail.py"), "send",
                                "--type", "NOTICE", "--from", A, "--to", B, "--subject",
                                "vendor facts", "--body", fact], cwd=proj, env=spec.env,
                               capture_output=True, check=True)
            elif spec.arm in ("N2", "N1"):
                self.use(events, "Edit", file_path=NOTES_PATH)
                with (spec.shared / NOTES_REL).open("a", encoding="utf-8") as fh:
                    fh.write(f"agent-a: {fact}\n")
            return
        # agent-b
        if spec.arm == "O":
            got = self.facts(spec.prompt)
            if got:
                self.write_report(spec, events, *got)
            return
        if spec.stage == "s1":
            self.use(events, "Write", file_path=str(proj / "ledger/report.py"))
            (proj / "ledger" / "report.py").write_text(inst.wrong_report(), encoding="utf-8")
            return
        looks = spec.stage == "s4" or self.pickup[spec.arm]
        if not looks:
            return
        text = ""
        if spec.arm == "P":
            msgs = subprocess.run(
                [sys.executable, "-I", str(proj / DEST / "agent_mail.py"), "list", "--json",
                 "--to", B], cwd=proj, env=spec.env, capture_output=True, text=True).stdout
            for m in (json.loads(msgs)["messages"] if msgs else []):
                self.use(events, "Bash", command=f"python3 {DEST}/agent_mail.py show {m['id']}")
                text += subprocess.run(
                    [sys.executable, "-I", str(proj / DEST / "agent_mail.py"), "show", m["id"]],
                    cwd=proj, env=spec.env, capture_output=True, text=True).stdout
        elif spec.arm in ("N2", "N1"):
            self.use(events, "Read", file_path=NOTES_PATH)
            text = (spec.shared / NOTES_REL).read_text(encoding="utf-8")
        got = self.facts(text)
        if got:
            self.write_report(spec, events, *got)


# ---- command line ---------------------------------------------------------------------------
def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Experiment v2 harness (docs/experiment-v2.md)")
    ap.add_argument("--stage", choices=("smoke", "main"), required=True)
    ap.add_argument("--out", required=True, help="directory for raw results (new or empty)")
    ap.add_argument("--dry-run", action="store_true", help="fake driver: plan, projection, no cost")
    ap.add_argument("--live", action="store_true",
                    help=f"real `claude` sessions (spends usage); needs {LIVE_ENV}=1")
    ap.add_argument("--gate", metavar="PATH", help="main stage: the smoke stage's smoke_gate.json")
    ap.add_argument("--ceiling-usd", type=float, help="hard spend ceiling (smoke 4, main 30)")
    ap.add_argument("--projection-cap", type=float,
                    help="smoke gate: projected main cost must not pass this (default: main ceiling)")
    ap.add_argument("--model", default=e2e.DEFAULT_MODEL)
    ap.add_argument("--effort", default=DEFAULT_EFFORT)
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    ap.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"at most {MAX_WORKERS}")
    ap.add_argument("--blocks", type=int, help="main stage: run only this many 10-instance blocks")
    ap.add_argument("--dry-instances", type=int, default=3, help="dry run: instances to exercise")
    ap.add_argument("--fake-cost", type=float, default=0.02, help="dry run: fake USD per session")
    ap.add_argument("--claude", metavar="PATH")
    ap.add_argument("--workdir", metavar="DIR")
    args = ap.parse_args(argv)
    if args.ceiling_usd is None:
        args.ceiling_usd = CEILING[args.stage]
    if args.projection_cap is None:
        args.projection_cap = CEILING["main"]
    args.max_calls = 10**6
    return args


def refuse(msg: str) -> int:
    print(f"refused: {msg}", file=sys.stderr)
    return 2


def print_plan(args: argparse.Namespace) -> None:
    plan = plan_sessions(args.stage)
    print(f"stage={args.stage} model={args.model} effort={args.effort} "
          f"max_turns={args.max_turns} ceiling=${args.ceiling_usd:.2f}")
    print(f"instances={plan['instances']} (seeds {SEEDS[args.stage][0]}..{SEEDS[args.stage][-1]}), "
          f"{plan['per_instance']} sessions each + S4 ~{S4_PER_INSTANCE}/instance")
    print(f"sessions: {plan['sessions']} + {plan['infra_repeats_assumed']} assumed repeats "
          f"= {plan['total']}")
    for name, usd in projection(args.stage).items():
        note = "  OVER the ceiling" if usd > args.ceiling_usd else ""
        print(f"  projected cost {name:<6} ${usd:>6.2f}{note}")
    print(f"  (per-session assumptions {COST_SCENARIOS}; doc section 11, replaced by the smoke mean)")
    chars = paragraph_chars()
    print("arm paragraph characters (agent-a/agent-b): "
          + ", ".join(f"{a}={chars[a][A]}/{chars[a][B]}" for a in ARMS)
          + f"; P vs N2 within 10%: {lengths_within()}")
    print(f"prompts sha256 {prompts_hash()}")


def execute(args: argparse.Namespace, driver: Driver, claude: str, out: Path,
            seeds: list[int]) -> tuple[Harness, list[dict[str, Any]]]:
    work = e2e.prepare_workdir(Path(args.workdir) if args.workdir
                               else Path(tempfile.gettempdir()) / f"v2-work-{os.getpid()}")
    h = Harness(args, claude, work, out, driver)
    records: list[dict[str, Any]] = []
    done: list[int] = []
    try:
        for block in blocks(seeds):
            records += h.run_block(block)
            done += block
            print(f"block of {len(block)} done: sessions={h.n_sessions} spent=${h.spent:.4f} "
                  f"(ceiling ${args.ceiling_usd:.2f})")
            why = h.stop or (stop_reason(records, done) if args.stage == "main" else None)
            if why or any("aborted" in r for r in records):
                h.stop = h.stop or why
                print(f"STOP: {h.stop or 'spend ceiling'}; instances completed or started: "
                      f"{len(done)}; a stop leaves whole 10-instance blocks (report their N)")
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return h, records


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.dry_run == args.live:
        return refuse("pass exactly one of --dry-run (fake driver, free) or --live")
    if args.live and os.environ.get(LIVE_ENV) != "1":
        return refuse(f"--live spends usage; set {LIVE_ENV}=1 to confirm")
    if args.ceiling_usd <= 0:
        return refuse("--ceiling-usd must be positive")
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        return refuse(f"--out {out} exists and is not empty")
    print_plan(args)
    seeds = SEEDS[args.stage]
    if args.stage == "main" and args.blocks:
        seeds = seeds[:args.blocks * BLOCK]
    if args.dry_run:
        seeds = seeds[:max(1, args.dry_instances)]
        failed = [n for n, ok in offline_checks() if not ok]
        print("offline checks:", "ok" if not failed else f"FAILED {failed}")
        out.mkdir(parents=True, exist_ok=True)
        h, records = execute(args, FakeDriver(cost=args.fake_cost), "fake", out, seeds)
        print(f"dry run: {len(final_records(records))} runs, {h.n_sessions} fake sessions, "
              f"spent ${h.spent:.4f} (fake costs; nothing was spent)")
        if args.stage == "smoke":
            gate = smoke_gate(records, h.spent, args, offline_checks())
            gate["dry_run"] = True
            (out / "smoke_gate.json").write_text(json.dumps(gate, indent=2, sort_keys=True))
            print("dry-run gate written (marked dry_run: the main stage refuses it)")
        return 1 if failed else 0
    if args.stage == "main":
        why = check_gate(args.gate, args)
        if why:
            return refuse(why)
    claude = args.claude or shutil.which("claude")
    if not claude:
        return refuse("`claude` is not on PATH")
    failed = [n for n, ok in offline_checks() if not ok]
    if failed:
        return refuse(f"offline checks failed: {failed}")
    if not lengths_within():
        return refuse("P and N2 arm paragraphs differ by more than 10%")
    _, reason = e2e.claude_status(claude)
    if reason:
        return refuse(reason)
    out.mkdir(parents=True, exist_ok=True)
    h, records = execute(args, LiveDriver(), claude, out, seeds)
    (out / "run_summary.json").write_text(json.dumps({
        "schema": SCHEMA, "stage": args.stage, "sessions": h.n_sessions,
        "spent_usd": round(h.spent, 4), "ceiling_usd": args.ceiling_usd,
        "models_reported": sorted(h.models), "stopped": h.stop}, indent=2, sort_keys=True))
    if args.stage == "smoke":
        gate = smoke_gate(records, h.spent, args, offline_checks())
        (out / "smoke_gate.json").write_text(json.dumps(gate, indent=2, sort_keys=True))
        print(f"smoke gate passed={gate['passed']} -> {out / 'smoke_gate.json'}")
        print(json.dumps(gate["checks"], indent=2))
    print(f"sessions={h.n_sessions} spent=${h.spent:.4f}; raw results in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
