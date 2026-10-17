#!/usr/bin/env python3
"""Safety probes for two PROPOSED pickup changes (nothing here changes the shipped hook).

  python3 docs/data/context-cost/safety_probe.py --base DIR --ack DIR --overflow DIR

Each DIR is a checkout (a copy is fine) whose hooks/agent_mail_check.py is measured: --base is
main, --ack has docs/data/context-cost/ack-pickup-prototype.patch applied (acknowledgment-based
pickup), --overflow has docs/data/context-cost/owed-overflow-prototype.patch applied (owed ASKs
bounded to 20 with an explicit overflow line). Synthetic mailboxes in temp dirs only; stdlib only;
never calls a model. Prints one line per probe: what was tried and whether the pickup still listed the message
("listed") or stopped listing it ("HIDDEN"). For a DISPUTE, an ASK or an owed ASK, HIDDEN is
actionable work leaving the pickup.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run(root: Path, args: list[str], box: Path, ident: str = "me") -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env.update(AGENT_MAIL_DIR=str(box), AGENT_MAIL_IDENTITY=ident)
    p = subprocess.run([sys.executable, *args], env=env, capture_output=True, text=True, check=False)
    return p.stdout + p.stderr


def send(root: Path, box: Path, **kw: str) -> str:
    argv = [str(root / "agent_mail.py"), "send"]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", v]
    out = run(root, argv, box)
    m = re.search(r"id: ([0-9A-Z]{26})", out)
    assert m, out
    return m.group(1)


def hook(root: Path, box: Path) -> str:
    return run(root, [str(root / "hooks" / "agent_mail_check.py")], box)


results: list[tuple[str, str, str]] = []


def verdict(name: str, seen: bool, note: str, want_seen: bool = True) -> None:
    """Record what the pickup showed: "listed" or "HIDDEN". `want_seen` is ignored; whether hidden
    is acceptable is judged in docs/context-cost.md, not here (kept for call-site readability)."""
    results.append((name, "listed" if seen else "HIDDEN", note))


def ack_probes(root: Path, label: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        box = Path(d)
        n = send(root, box, type="NOTICE", **{"from": "peer"}, to="me", subject="deploy window moved", body="x")
        dsp = send(root, box, type="DISPUTE", **{"from": "peer"}, to="me", subject="your patch is wrong", body="x")
        verdict(f"{label}: interruption BEFORE ack (nothing filed)", n in hook(root, box),
                "unacknowledged NOTICE still listed on the next prompt")
        send(root, box, type="NOTICE", **{"from": "me"}, to="me", reply_to=n, subject="ack", body="x")
        verdict(f"{label}: after the ack", n in hook(root, box), "acknowledged NOTICE not listed (by design)",
                want_seen=False)
        verdict(f"{label}: context loss AFTER ack (fresh process, fresh session)", n in hook(root, box),
                "the pickup cannot tell a fresh session that this was acknowledged; the model has no record "
                "of it either unless it runs `list --live` (a count line is the only trace)", want_seen=False)
        send(root, box, type="NOTICE", **{"from": "me"}, to="me", reply_to=dsp, result="DONE",
             subject="ack dispute", body="x")
        verdict(f"{label}: self-ack of a DISPUTE with --result DONE (no work done)", dsp in hook(root, box),
                "a one-line assertion removes a DISPUTE from the pickup; nothing verifies the dispute was acted on")
        # a courtesy reply to someone else that happens to cite the id
        n2 = send(root, box, type="NOTICE", **{"from": "peer"}, to="me", subject="please also rotate keys", body="x")
        send(root, box, type="NOTICE", **{"from": "me"}, to="peer2", reply_to=n2, subject="fyi forwarding", body="x")
        verdict(f"{label}: ANY message from me citing the id (even to a third party, even a forward)",
                n2 in hook(root, box), "forwarding or quoting a notice counts as acknowledging it")
        n3 = send(root, box, type="NOTICE", **{"from": "peer"}, to="me", subject="third notice", body="x")
        # from: is free text (authorship CANNOT_VERIFY): any writer can file as me
        send(root, box, type="NOTICE", **{"from": "me"}, to="me", reply_to=n3, subject="forged ack", body="x")
        verdict(f"{label}: ack filed by another writer claiming from: me", n3 in hook(root, box),
                "authorship is CANNOT_VERIFY, so a forged ack hides it from me")
        a = send(root, box, type="ASK", **{"from": "peer"}, to="me", subject="please decide", body="x")
        send(root, box, type="NOTICE", **{"from": "me"}, to="me", reply_to=a, subject="ack of an ask", body="x")
        verdict(f"{label}: self-ack citing an ASK", a in hook(root, box),
                "citing an ASK closes it (owes_reply is any citation); true on main too")
        a2 = send(root, box, type="ASK", **{"from": "peer"}, to="me", subject="third-party closes this", body="x")
        send(root, box, type="NOTICE", **{"from": "eve"}, to="eve", reply_to=a2, subject="unrelated", body="x")
        verdict(f"{label}: a THIRD PARTY (eve) cites my owed ASK", a2 in hook(root, box),
                "any message citing an ASK closes it for the pickup, whoever wrote it (pre-existing)")


def overflow_probes(root: Path, label: str) -> None:
    with tempfile.TemporaryDirectory() as d:
        box = Path(d)
        ids = [send(root, box, type="ASK", **{"from": "peer"}, to="me", subject=f"ask {i}", body="x")
               for i in range(60)]
        out = hook(root, box)
        shown = [i for i in ids if i in out]
        verdict(f"{label}: 60 owed ASKs, count still stated", "owe a reply to 60 open ASK(s)" in out,
                f"{len(shown)} spelled out")
        if len(shown) < 60:
            verdict(f"{label}: overflow line names an exact command", "inbox --to me --type ASK" in out,
                    "retrieval command printed")
            inbox = run(root, [str(root / "agent_mail.py"), "inbox", "--to", "me", "--type", "ASK"], box)
            verdict(f"{label}: that command lists every owed ASK", all(i in inbox for i in ids),
                    f"{sum(i in inbox for i in ids)}/60 ids")
            verdict(f"{label}: the oldest owed ASK is among those spelled out", ids[0] in out,
                    "oldest first, so the longest-waiting ask is never the hidden one")
        # answer the oldest ask: the hook must surface the next one (stateless reconstruction)
        send(root, box, type="ANSWER", **{"from": "me"}, to="peer", reply_to=ids[0], result="DONE",
             subject="done", body="x")
        out2 = hook(root, box)
        verdict(f"{label}: after answering one, the count falls and the next ask appears",
                "owe a reply to 59 open ASK(s)" in out2 and (len(shown) == 60 or ids[len(shown)] in out2),
                "reconstructed from files; no state")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--ack", required=True)
    ap.add_argument("--overflow", required=True)
    a = ap.parse_args()
    ack_probes(Path(a.base), "main")
    ack_probes(Path(a.ack), "ack-prototype")
    overflow_probes(Path(a.base), "main")
    overflow_probes(Path(a.overflow), "overflow-prototype")
    w = max(len(r[0]) for r in results)
    for name, v, note in results:
        print(f"{v:6}  {name.ljust(w)}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
