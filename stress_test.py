#!/usr/bin/env python3
"""Concurrency stress test: parallel senders vs. a reader that polls raw files.

Two properties, both asserted on content that must appear (PROTOCOL.md §8):

1. No message is lost or overwritten: N sends -> N files with N distinct ids.
2. No reader ever observes a half-written message. The reader here is
   deliberately independent of agent_mail.py: it globs ``*.md`` and requires
   every file it opens to end with the sentinel the body carries. A torn
   write (truncated body, unclosed frontmatter) fails this.

Usage: python stress_test.py [--senders 16] [--per-sender 6] [--kb 2048]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SENTINEL = "END-OF-BODY-SENTINEL"


def sender(box: Path, body_file: Path, n: int, tag: str, errors: list) -> None:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    for i in range(n):
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
             "--from", f"agent-{tag}", "--to", "all", "--subject", f"stress {tag} {i}",
             "--body-file", str(body_file)],
            capture_output=True, text=True, env=env)
        if r.returncode != 0:
            errors.append(f"send failed rc={r.returncode}: {r.stderr.strip()[:200]}")


def reader(box: Path, stop: threading.Event, torn: list, seen: set) -> None:
    while not stop.is_set():
        for p in box.glob("*.md"):
            try:
                text = p.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            seen.add(p.name)
            if not text.rstrip().endswith(SENTINEL) or text.count("\n---\n") < 1:
                torn.append((p.name, len(text)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--senders", type=int, default=8)
    ap.add_argument("--per-sender", type=int, default=4)
    ap.add_argument("--kb", type=int, default=2048)
    a = ap.parse_args()
    expect = a.senders * a.per_sender

    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp) / "mailbox"
        box.mkdir()
        body = Path(tmp) / "body.txt"
        body.write_text(("filler line to make the body large\n" * (a.kb * 1024 // 36))
                        + SENTINEL + "\n", encoding="utf-8")
        errors: list = []
        torn: list = []
        seen: set = set()
        stop = threading.Event()
        rd = threading.Thread(target=reader, args=(box, stop, torn, seen))
        rd.start()
        t0 = time.time()
        ts = [threading.Thread(target=sender, args=(box, body, a.per_sender, str(i), errors))
              for i in range(a.senders)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        stop.set()
        rd.join()

        files = sorted(box.glob("*.md"))
        ids = {f.name.split("-", 1)[0] for f in files}
        leftovers = [p.name for p in box.iterdir() if p.suffix != ".md"]
        results = [
            ("every send exited 0", not errors, errors[:3]),
            (f"{expect} sends -> {expect} files", len(files) == expect, f"got {len(files)}"),
            (f"{expect} distinct ids", len(ids) == expect, f"got {len(ids)}"),
            ("reader observed messages (positive control)", len(seen) > 0, f"saw {len(seen)}"),
            ("reader never saw a torn message", not torn, torn[:3]),
            ("no temp files left behind", not leftovers, leftovers[:3]),
        ]
        bad = 0
        for name, ok, detail in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  -- {detail}"))
            bad += 0 if ok else 1
        print(f"\n{expect} messages in {time.time() - t0:.1f}s; reader saw {len(seen)} files")
        print("all checks passed" if not bad else f"{bad} check(s) FAILED")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
