#!/usr/bin/env python3
"""Concurrency stress test: parallel senders vs. readers that poll while they write.

Four properties, all asserted on content that must appear (PROTOCOL.md §8):

1. No message is lost or overwritten: N sends -> N files with N distinct ids.
2. No reader ever observes a half-written message. The reader here is
   deliberately independent of agent_mail.py: it globs ``*.md`` and requires
   every file it opens to end with the sentinel the body carries. A torn
   write (truncated body, unclosed frontmatter) fails this.
3. The tool's own readers stay clean while senders run: `list` never reports
   a REJECT or exits non-zero, and `status --json` and `doctor` never crash on
   a temp file that appears or vanishes mid-scan.
4. Every sender also fires one keyed send with the same key: exactly one of
   them writes, the rest answer "duplicate of" (PROTOCOL.md §31).

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


def tool_reader(box: Path, stop: threading.Event, bad: list, runs: list) -> None:
    """The tool's own read paths, in a loop, for as long as the senders run."""
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    while not stop.is_set():
        for argv in (["list"], ["status", "--json"], ["doctor"]):
            r = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), *argv],
                               capture_output=True, text=True, env=env)
            runs.append(argv[0])
            if "Traceback" in r.stderr or "REJECT" in r.stderr + r.stdout:
                bad.append(f"{argv[0]}: {(r.stderr or r.stdout).strip()[-200:]}")
            elif argv[0] != "doctor" and r.returncode != 0:  # doctor exits 2 without a canary
                bad.append(f"{argv[0]} rc={r.returncode}: {r.stderr.strip()[-200:]}")


def keyed(box: Path, results: list) -> None:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    env.pop("AGENT_MAIL_IDEM_WAIT", None)
    r = subprocess.run(
        [sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
         "--from", "agent-keyed", "--to", "all", "--subject", "keyed burst",
         "--body", "one of many " + SENTINEL, "--key", "burst"],
        capture_output=True, text=True, env=env)
    results.append((r.returncode, r.stdout, r.stderr))


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
    expect = a.senders * a.per_sender + 1  # + the one message of the keyed burst

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
        tool_bad: list = []
        tool_runs: list = []
        keyed_res: list = []
        rd = threading.Thread(target=reader, args=(box, stop, torn, seen))
        rd.start()
        trd = threading.Thread(target=tool_reader, args=(box, stop, tool_bad, tool_runs))
        trd.start()
        t0 = time.time()
        ts = [threading.Thread(target=sender, args=(box, body, a.per_sender, str(i), errors))
              for i in range(a.senders)]
        ts += [threading.Thread(target=keyed, args=(box, keyed_res)) for _ in range(a.senders)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        stop.set()
        rd.join()
        trd.join()

        files = sorted(box.glob("*.md"))
        ids = {f.name.split("-", 1)[0] for f in files}
        leftovers = [p.name for p in box.iterdir()
                     if p.suffix != ".md" and not (p.name.startswith(".idem.") and "." not in p.name[6:])]
        markers = [p.name for p in box.iterdir() if p.name.startswith(".idem.")]
        k_wrote = sum(1 for rc, out, _ in keyed_res if rc == 0 and "wrote " in out)
        k_dup = sum(1 for rc, out, _ in keyed_res if rc == 0 and "duplicate of" in out)
        k_ids = {ln.split(": ", 1)[1] for _, out, _ in keyed_res for ln in out.splitlines()
                 if ln.startswith("id: ")}
        results = [
            ("every send exited 0", not errors, errors[:3]),
            (f"{expect} sends -> {expect} files", len(files) == expect, f"got {len(files)}"),
            (f"{expect} distinct ids", len(ids) == expect, f"got {len(ids)}"),
            ("reader observed messages (positive control)", len(seen) > 0, f"saw {len(seen)}"),
            ("reader never saw a torn message", not torn, torn[:3]),
            ("no temp files left behind", not leftovers, leftovers[:3]),
            ("the tool's own readers ran while senders wrote (positive control)",
             {"list", "status", "doctor"} <= set(tool_runs), f"ran {sorted(set(tool_runs))}"),
            ("list/status/doctor never saw a REJECT, a traceback or a failure", not tool_bad, tool_bad[:3]),
            (f"{a.senders} simultaneous keyed sends: one wrote, the rest 'duplicate of' one id",
             k_wrote == 1 and k_dup == a.senders - 1 and len(k_ids) == 1,
             f"wrote={k_wrote} dup={k_dup} ids={len(k_ids)} {[e[-120:] for _, _, e in keyed_res if e][:2]}"),
            ("exactly one key marker remains, and it names the published message",
             len(markers) == 1 and len(k_ids) == 1
             and (box / markers[0]).read_text(encoding="utf-8").split("\n")[0] in k_ids, markers[:3]),
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
