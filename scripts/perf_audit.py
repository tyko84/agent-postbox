#!/usr/bin/env python3
"""Re-runnable performance audit (docs/performance.md). Stdlib only; writes only to a temp dir.

Usage: python3 scripts/perf_audit.py [--sizes 100,1000,5000,20000] [--reps 5]
Builds mailboxes of valid message files (the same bytes `send` writes), checks every file
loads with no reject, then times the CLI and the prompt hook as subprocesses (median of --reps).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = str(ROOT / "agent_mail.py")
HOOK = str(ROOT / "hooks" / "agent_mail_check.py")
sys.path.insert(0, str(ROOT))
import agent_mail  # noqa: E402


def env(box: Path, **extra: str) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    e["AGENT_MAIL_DIR"] = str(box)
    e.update(extra)
    return e


def build(box: Path, n: int) -> None:
    """70% NOTICE, 20% ASK (a->b), 10% ANSWER (b->a) to every other ASK. All within 24h."""
    box.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    asks: list[str] = []
    for i in range(n):
        when = now - dt.timedelta(seconds=(n - i) * (86400 / max(n, 1)))
        mid = agent_mail._new_ulid(when)
        k = i % 10
        extra = ""
        if k in (0, 5):
            typ, frm, to = "ASK", "a", "b"
            asks.append(mid)
        elif k == 9 and len(asks) >= 2:
            typ, frm, to = "ANSWER", "b", "a"
            extra = f"reply_to: {asks.pop(-2)}\n"
        else:
            typ, frm, to = "NOTICE", ("a", "b")[i % 2], ("b", "a")[i % 2]
        text = (f"---\nid: {mid}\ntype: {typ}\nfrom: {frm}\nto: {to}\n"
                f"date: {when.strftime('%Y-%m-%dT%H:%M:%SZ')}\n{extra}"
                f"subject: message {i}\n---\n\nbody of message {i}: a typical short note.\n")
        (box / f"{mid}-message-{i}.md").write_text(text)


def run(args: list[str], e: dict[str, str]) -> float:
    t = time.perf_counter()
    subprocess.run(args, env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return (time.perf_counter() - t) * 1000


def med(args: list[str], e: dict[str, str], reps: int) -> float:
    return statistics.median(run(args, e) for _ in range(reps))


def peak_mb(args: list[str], e: dict[str, str]) -> float:
    code = ("import resource,subprocess,sys;subprocess.run(sys.argv[1:],stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL);print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)")
    out = subprocess.run([sys.executable, "-c", code, *args], env=e, capture_output=True, text=True)
    return int(out.stdout.strip()) / (1024 * 1024 if sys.platform == "darwin" else 1024)


def send_cmd(subject: str) -> list[str]:
    return [sys.executable, TOOL, "send", "--type", "NOTICE", "--from", "a", "--to", "b",
            "--subject", subject, "--body", subject]


def sender(box: str, tag: str, count: int) -> None:
    for i in range(count):
        subprocess.run(send_cmd(f"{tag}-{i}"), env=env(Path(box)), stdout=subprocess.DEVNULL, check=True)


def concurrency(tmp: Path, procs: int = 8, per: int = 50) -> dict[str, object]:
    box = tmp / "conc"
    box.mkdir()
    t = time.perf_counter()
    ps = [subprocess.Popen([sys.executable, __file__, "--child", str(box), f"p{p}", str(per)])
          for p in range(procs)]
    rc = [p.wait() for p in ps]
    wall = time.perf_counter() - t
    files = list(box.glob("*.md"))
    subj = [next(ln for ln in f.read_text().splitlines() if ln.startswith("subject: ")) for f in files]
    return {"procs": procs, "per": per, "exit_codes_ok": all(c == 0 for c in rc), "files": len(files),
            "distinct_ids": len({f.name[:26] for f in files}), "distinct_subjects": len(set(subj)),
            "wall_s": round(wall, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="100,1000,5000,20000")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--child", nargs=3, metavar=("BOX", "TAG", "COUNT"))
    a = ap.parse_args()
    if a.child:
        sender(a.child[0], a.child[1], int(a.child[2]))
        return 0
    print(json.dumps({"python": sys.version.split()[0], "platform": platform.platform(),
                      "machine": platform.machine(), "cpus": os.cpu_count()}))
    with tempfile.TemporaryDirectory(prefix="postbox-perf-") as d:
        tmp = Path(d)
        print(json.dumps({"python_startup_ms": round(med([sys.executable, "-c", "pass"],
                                                         dict(os.environ), a.reps), 1)}))
        for n in [int(s) for s in a.sizes.split(",")]:
            box = tmp / f"m{n}"
            build(box, n)
            os.environ["AGENT_MAIL_DIR"] = str(box)
            _msgs, rejects = agent_mail.load_messages()
            e, e_me = env(box), env(box, AGENT_MAIL_IDENTITY="b")
            files = list(box.glob("*.md"))
            row = {
                "n": n, "rejects": len(rejects),
                "bytes_per_msg": round(sum(f.stat().st_size for f in files) / n),
                "disk_bytes_per_msg": round(sum(f.stat().st_blocks * 512 for f in files) / n),
                "list_ms": med([sys.executable, TOOL, "list"], e, a.reps),
                "list_to_live_ms": med([sys.executable, TOOL, "list", "--to", "b", "--live"], e, a.reps),
                "inbox_ms": med([sys.executable, TOOL, "inbox", "--to", "b"], e, a.reps),
                "status_ms": med([sys.executable, TOOL, "status"], e, a.reps),
                "hook_ms": med([sys.executable, HOOK], e_me, a.reps),
                "send_ms": statistics.median(run(send_cmd(f"bench {i}"), e) for i in range(a.reps)),
                "hook_peak_mb": peak_mb([sys.executable, HOOK], e_me),
                "status_peak_mb": peak_mb([sys.executable, TOOL, "status"], e),
            }
            print(json.dumps({k: (round(v, 1) if isinstance(v, float) else v) for k, v in row.items()}),
                  flush=True)
        print(json.dumps(concurrency(tmp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
