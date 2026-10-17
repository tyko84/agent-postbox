#!/usr/bin/env python3
"""Reproducible synthetic-mailbox benchmark: bytes and wall time of what Postbox puts in front
of a model and of the commands that scan the mailbox. Stdlib only; deterministic (seeded) mailbox
content; writes only to a temp dir; never calls a model.

Usage: python3 scripts/context_bench.py [--root DIR] [--reps 3] [--sizes 100,1000,10000] [--seed 7]
  --root   checkout whose agent_mail.py and hooks/ are measured (default: this checkout), so a
           candidate hook can be compared with main at equal tool-path length.

BYTES are exact (UTF-8 length of stdout). Times are the median of --reps subprocess runs, in ms,
on this machine; they include interpreter start-up. Tokens are NOT measured here; multiply bytes
by 0.55 (docs/context-cost.md, one measurement on one small model) for an ESTIMATE.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ME = "me"
BASE = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def ulid(rng: random.Random, when: dt.datetime) -> str:
    ms = int(when.timestamp() * 1000)
    ts = ""
    for _ in range(10):
        ts = CROCKFORD[ms & 31] + ts
        ms >>= 5
    return ts + "".join(rng.choice(CROCKFORD) for _ in range(16))


class Box:
    """A mailbox writer. Messages are valid files (the bytes `send` writes), dated within the
    last day so none is stale; `age` orders them (larger = older)."""

    def __init__(self, path: Path, seed: int) -> None:
        self.path, self.rng, self.n = path, random.Random(seed), 0
        path.mkdir(parents=True, exist_ok=True)

    def put(self, typ: str, frm: str, to: str, subject: str, body: str = "short note.\n",
            reply_to: str | None = None, extra: str = "", age: int = 0) -> str:
        when = BASE - dt.timedelta(seconds=age * 7 + 60)
        mid = ulid(self.rng, when)
        head = (f"---\nid: {mid}\ntype: {typ}\nfrom: {frm}\nto: {to}\n"
                f"date: {when.strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        if reply_to:
            head += f"reply_to: {reply_to}\n"
        head += f"{extra}subject: {subject}\n---\n\n{body}"
        (self.path / f"{mid}-m{self.n}.md").write_text(head, encoding="utf-8")
        self.n += 1
        return mid


def claim(box: Box, frm: str, scope: str, age: int = 0) -> str:
    exp = (BASE + dt.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return box.put("CLAIM", frm, ME, f"claim {scope}", extra=f"scope: {scope}\nexpires: {exp}\n", age=age)


def scen_zero(box: Box) -> None:
    for i in range(30):                      # mail exists, none is addressed to ME
        box.put("NOTICE", "a", "b", f"background {i}", age=i)


def scen_asks(box: Box, k: int) -> None:
    for i in range(k):
        box.put("ASK", f"peer{i % 7}", ME, f"please review change {i}", age=k - i)


def scen_mixed(box: Box) -> None:
    """15 NOTICE, 6 ANSWER (to ME's own earlier ASKs; 6 more ASKs from ME), 5 ASK owed, 3 CLAIM, 2 DISPUTE, all to ME."""
    ages = iter(range(100, 0, -1))
    for i in range(15):
        box.put("NOTICE", f"peer{i % 3}", ME, f"status update {i}", age=next(ages))
    for i in range(6):
        a = box.put("ASK", ME, f"peer{i % 3}", f"my question {i}", age=next(ages))
        box.put("ANSWER", f"peer{i % 3}", ME, f"re: my question {i}", reply_to=a,
                extra="result: DONE\n", age=next(ages))
    for i in range(5):
        box.put("ASK", f"peer{i % 3}", ME, f"please decide {i}", age=next(ages))
    for i in range(3):
        claim(box, f"peer{i}", f"src/area{i}", age=next(ages))
    for i in range(2):
        box.put("DISPUTE", f"peer{i}", ME, f"I disagree with item {i}", age=next(ages))


def scen_scan(box: Box, n: int) -> None:
    """70% NOTICE, 20% ASK to ME (half answered), 10% ANSWER; same shape as perf_audit.py."""
    open_asks: list[str] = []
    for i in range(n):
        k = i % 10
        age = n - i
        if k in (0, 5):
            mid = box.put("ASK", "a", ME, f"ask {i}", age=age)
            if k == 0:
                open_asks.append(mid)
        elif k == 9 and open_asks:
            box.put("ANSWER", ME, "a", f"answer {i}", reply_to=open_asks.pop(), extra="result: DONE\n", age=age)
        else:
            box.put("NOTICE", ("a", "b")[i % 2], ("b", ME)[i % 2], f"note {i}", age=age)


def scen_big(box: Box, size: int) -> None:
    box.put("ASK", "peer", ME, "please read the attached analysis", body="x" * size + "\n", age=1)
    box.put("NOTICE", "peer", ME, "big notice", body="y" * size + "\n", age=0)


def scen_people(box: Box, k: int) -> None:
    for i in range(k):
        box.put("NOTICE", f"agent-{i:04d}", f"agent-{(i + 1) % k:04d}", f"hello {i}", age=k - i)
    box.put("ASK", "agent-0001", ME, "one ask among many participants", age=0)


class Runner:
    def __init__(self, root: Path, reps: int) -> None:
        self.tool, self.hook, self.reps = str(root / "agent_mail.py"), str(root / "hooks" / "agent_mail_check.py"), reps

    def run(self, argv: list[str], box: Path, identity: str | None = ME) -> tuple[int, float]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env["AGENT_MAIL_DIR"] = str(box)
        if identity:
            env["AGENT_MAIL_IDENTITY"] = identity
        t = time.perf_counter()
        p = subprocess.run([sys.executable, *argv], env=env, capture_output=True, check=False)
        ms = (time.perf_counter() - t) * 1000
        return len(p.stdout), ms

    def measure(self, argv: list[str], box: Path) -> tuple[int, float]:
        runs = [self.run(argv, box) for _ in range(self.reps)]
        sizes = {b for b, _ in runs}
        assert len(sizes) == 1, f"non-deterministic output size {sizes}"
        return runs[0][0], statistics.median(ms for _, ms in runs)

    def hook_(self, box: Path) -> tuple[int, float]:
        return self.measure([self.hook], box)

    def tool_(self, box: Path, *args: str) -> tuple[int, float]:
        return self.measure([self.tool, *args], box)


def table(title: str, head: list[str], rows: list[list[object]]) -> None:
    print(f"\n## {title}")
    cols = [head] + [[str(c) for c in r] for r in rows]
    w = [max(len(r[i]) for r in cols) for i in range(len(head))]
    for j, r in enumerate(cols):
        print("| " + " | ".join(c.rjust(w[i]) if i else c.ljust(w[i]) for i, c in enumerate(r)) + " |")
        if j == 0:
            print("|" + "|".join("-" * (x + 2) for x in w) + "|")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--sizes", default="100,1000,10000")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    R = Runner(Path(a.root).resolve(), a.reps)
    print(f"# context_bench  root=<checkout>/{os.path.basename(R.tool)}  seed={a.seed}  reps={a.reps}  python={sys.version.split()[0]}")
    print("# bytes exact; tokens ESTIMATED at 0.55 tokens/byte (docs/context-cost.md), not measured here")
    with tempfile.TemporaryDirectory(prefix="postbox-ctxbench-") as d:
        tmp = Path(d)
        seed = a.seed

        def mk(name: str, fill: object, *args: object) -> Path:
            b = Box(tmp / name, seed)
            fill(b, *args)  # type: ignore[operator]
            return b.path

        scen = [("no mail for ME", mk("zero", scen_zero)),
                ("1 owed ASK", mk("a1", scen_asks, 1)),
                ("50 owed ASKs", mk("a50", scen_asks, 50)),
                ("200 owed ASKs", mk("a200", scen_asks, 200)),
                ("mixed (37 msgs: 15N 6ANS 5ASK 3C 2D)", mk("mixed", scen_mixed))]
        rows = []
        for name, p in scen:
            hb, hm = R.hook_(p)
            ib, im = R.tool_(p, "inbox", "--to", ME, "--type", "ASK,DISPUTE", "--json")
            rows.append([name, hb, f"{hb * 0.55:.0f}", f"{hm:.0f}", ib, f"{im:.0f}"])
        table("Hook output and idle inbox per scenario",
              ["scenario", "hook B", "est tok", "hook ms", "inbox --json B", "inbox ms"], rows)

        rows = []
        for name, p in scen:
            outs = []
            for _ in range(10):
                outs.append(R.run([R.hook], p)[0])
            rows.append([name, outs[0], outs[-1], sum(outs), f"{sum(outs) * 0.55:.0f}", len(set(outs))])
        table("10 simulated prompts, no new mail (hook is stateless: same text every turn)",
              ["scenario", "turn 1 B", "turn 10 B", "10-turn B", "est tok", "distinct sizes"], rows)

        rows = []
        for n in [int(s) for s in a.sizes.split(",")]:
            p = mk(f"scan{n}", scen_scan, n)
            hb, hm = R.hook_(p)
            ib, im = R.tool_(p, "inbox", "--to", ME)
            sb, sm = R.tool_(p, "status")
            db, dm = R.tool_(p, "doctor")
            rows.append([n, f"{hm:.0f}", hb, f"{im:.0f}", ib, f"{sm:.0f}", sb, f"{dm:.0f}", db])
        table("Scan cost by mailbox size (ms median incl. interpreter start-up; B = stdout)",
              ["messages", "hook ms", "hook B", "inbox ms", "inbox B", "status ms", "status B",
               "doctor ms", "doctor B"], rows)

        rows = []
        for size in (10_000, 100_000, 1_000_000):
            p = mk(f"big{size}", scen_big, size)
            hb, hm = R.hook_(p)
            wb, wm = R.tool_(p, "inbox", "--to", ME, "--json")
            rows.append([f"2 x {size:,} B bodies", hb, f"{hm:.0f}", wb, f"{wm:.0f}"])
        table("Large bodies (the hook never prints a body; `inbox --json` is checked for body echo)",
              ["mailbox", "hook B", "hook ms", "inbox --json B", "inbox ms"], rows)

        rows = []
        for k in (10, 100, 1000):
            p = mk(f"people{k}", scen_people, k)
            hb, hm = R.hook_(p)
            sb, sm = R.tool_(p, "status")
            rows.append([k, hb, f"{hm:.0f}", sb, f"{sm:.0f}"])
        table("Many participants", ["participants", "hook B", "hook ms", "status B", "status ms"], rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
