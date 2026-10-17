#!/usr/bin/env python3
"""PROTOTYPE tests for acknowledgment-based pickup (docs/ack-delivery-design.md). Not in CI.

usage: test_ack_pickup.py <checkout with ack-pickup-prototype.patch applied>

Pins the recoverability claims: nothing is hidden except by a meaningful acknowledgment the recipient
itself filed, and every other path (no ack, interrupted ack, someone else's ack, a bare note, a new
session, a replay) leaves the message listed."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]
TOOL = str(HERE / "agent_mail.py")
HOOK = str(HERE / "hooks" / "agent_mail_check.py")
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"\n        {detail}"))
    if not ok:
        FAILED.append(name)


def send(box: Path, *argv: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, TOOL, "send", *argv], capture_output=True, text=True,
                       env={**os.environ, "AGENT_MAIL_DIR": str(box)}, cwd=box)
    out = [ln[4:] for ln in r.stdout.splitlines() if ln.startswith("id: ")]
    return r.returncode, out[0] if out else r.stdout + r.stderr


def hook(box: Path, who: str) -> str:
    return subprocess.run([sys.executable, HOOK], input="{}", capture_output=True, text=True,
                          env={**os.environ, "AGENT_MAIL_DIR": str(box), "AGENT_MAIL_IDENTITY": who},
                          cwd=box).stdout


def fresh() -> Path:
    box = Path(tempfile.mkdtemp()) / "box"
    box.mkdir()
    (box / "PARTICIPANTS.md").write_text(
        "| Identity | Reachable | Mechanism | Verified | can | cannot |\n|---|---|---|---|---|---|\n"
        "| `alice` | yes | x | 2026-01-01 | | |\n| `bob` | yes | x | 2026-01-01 | | |\n"
        "| `carol` | yes | x | 2026-01-01 | | |\n", encoding="utf-8")
    return box


def main() -> int:
    box = fresh()
    _, n1 = send(box, "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "finding one", "--body", "x")
    check("unacknowledged: listed", n1 in hook(box, "bob"))
    check("replay: the same input lists it identically (no hook state)", hook(box, "bob") == hook(box, "bob"))
    _, _ = send(box, "--type", "NOTICE", "--from", "bob", "--to", "bob", "--reply-to", n1,
                "--subject", "bare note", "--body", "seen")
    check("a bare self-addressed note does not hide it", n1 in hook(box, "bob"))
    rc, a1 = send(box, "--type", "NOTICE", "--from", "carol", "--to", "carol", "--reply-to", n1,
                  "--result", "DONE", "--subject", "someone else acks", "--body", "x")
    check("an ack by a different identity does not hide it for bob", rc == 0 and n1 in hook(box, "bob"))
    rc, a2 = send(box, "--type", "NOTICE", "--from", "bob", "--to", "bob", "--reply-to", n1,
                  "--result", "DONE", "--subject", f"ack {n1}", "--body", "acted: fixed", "--key", "ack-n1")
    out = hook(box, "bob")
    check("a self-ack that states a result hides it from bob's list", rc == 0 and f"id: {n1}" not in out, out)
    check("and the hook says one acknowledged message is not listed, and how to list it",
          "1 message(s) you acknowledged are not listed" in out and "list --to bob --live" in out, out)
    check("when every live message is acknowledged the hook still speaks (never silent)",
          out.strip() != "", out)
    rc2, dup = send(box, "--type", "NOTICE", "--from", "bob", "--to", "bob", "--reply-to", n1,
                    "--result", "DONE", "--subject", f"ack {n1}", "--body", "acted: fixed", "--key", "ack-n1")
    check("duplicate suppression: a retried keyed ack writes nothing new",
          rc2 == 0 and len(list(box.glob("0*.md"))) == 4, str(sorted(p.name for p in box.glob("0*.md"))))
    shown = subprocess.run([sys.executable, TOOL, "list", "--to", "bob", "--live"], capture_output=True,
                           text=True, env={**os.environ, "AGENT_MAIL_DIR": str(box)}, cwd=box).stdout
    check("recovery: the acknowledged message is still in the mailbox and `list --live` shows it",
          n1 in shown)
    shown = subprocess.run([sys.executable, TOOL, "show", n1], capture_output=True, text=True,
                           env={**os.environ, "AGENT_MAIL_DIR": str(box)}, cwd=box).stdout
    check("recovery: `show` returns the full original", "finding one" in shown)

    box2 = fresh()
    _, ask = send(box2, "--type", "ASK", "--from", "alice", "--to", "bob", "--subject", "q?", "--body", "x")
    send(box2, "--type", "NOTICE", "--from", "bob", "--to", "bob", "--reply-to", ask,
         "--result", "DONE", "--subject", "ack of an ask", "--body", "x")
    check("HAZARD (existing section 29 rule): any message citing an ASK closes it, so an ack must never "
          "cite an ASK; it is for NOTICE, ANSWER and DISPUTE only",
          ask not in hook(box2, "bob"))
    check("and the hook never hides an owed ASK for any other reason: a different ASK stays listed",
          "q2?" in (lambda b: (send(b, "--type", "ASK", "--from", "alice", "--to", "bob", "--subject", "q2?",
                                    "--body", "x"), hook(b, "bob"))[1])(fresh()))
    _, n2 = send(box2, "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "later", "--body", "x")
    send(box2, "--type", "ANSWER", "--from", "bob", "--to", "alice", "--reply-to", n2,
         "--subject", "re: later", "--body", "handled")
    check("a real reply (a message to someone else citing it) acknowledges it", n2 not in hook(box2, "bob"))

    box3 = fresh()
    _, n3 = send(box3, "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "interrupted", "--body", "x")
    pre = Path(tempfile.mkdtemp()) / "k.py"
    pre.write_text("import os,signal\nos.link=lambda *a,**k: os.kill(os.getpid(),signal.SIGKILL)\n"
                   "import runpy,sys\nsys.argv=[sys.argv[1]]+sys.argv[2:]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n",
                   encoding="utf-8")
    r = subprocess.run([sys.executable, str(pre), TOOL, "send", "--type", "NOTICE", "--from", "bob", "--to", "bob",
                        "--reply-to", n3, "--result", "DONE", "--subject", "ack", "--body", "x"],
                       capture_output=True, text=True, env={**os.environ, "AGENT_MAIL_DIR": str(box3)}, cwd=box3)
    check("interruption: an ack killed before it is linked leaves the message listed",
          r.returncode != 0 and n3 in hook(box3, "bob"), f"rc={r.returncode} {r.stderr[-200:]}")
    check("compaction/new session analogue: a process with no memory lists exactly the unacknowledged set",
          n3 in hook(box3, "bob"))

    print(f"\n{'ALL PASS' if not FAILED else 'FAILED: ' + str(len(FAILED))}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
