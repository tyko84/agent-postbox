"""Replay a mailbox through a pickup hook that hides acknowledged messages (docs/ack-delivery-design.md).

usage: replay_ack.py <checkout with the prototype patch applied> <mailbox dir>. Stdlib only. Bytes, not tokens."""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
repo, source = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(f"{source}/0*.md"))
def hook(box, who):
    env = dict(os.environ, AGENT_MAIL_DIR=box, AGENT_MAIL_IDENTITY=who)
    r = subprocess.run([sys.executable, f"{repo}/hooks/agent_mail_check.py"], input="{}", capture_output=True, text=True, env=env, cwd=box)
    return r.stdout
for who in ("collaborator", "codex-coordinator"):
    box = tempfile.mkdtemp() + "/box"
    os.mkdir(box)
    shutil.copy(f"{source}/PARTICIPANTS.md", box)
    total = acks = ackbytes = peak = 0
    for p in files:
        shutil.copy(p, box)
        out = hook(box, who)
        total += len(out.encode())
        peak = max(peak, len(out.encode()))
        # ids listed as non-owed messages this prompt
        blocks = re.findall(r"^  (NOTICE|ANSWER|DISPUTE) from .*\n    .*\n    id: (\w+)", out, re.M)
        for _typ, mid in blocks:
            cmd = f'python3 "{repo}/agent_mail.py" send --type NOTICE --from {who} --to {who} --reply-to {mid} --result DONE --subject "ack {mid}" --body "processed"'
            env = dict(os.environ, AGENT_MAIL_DIR=box)
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=env, cwd=box)
            if r.returncode == 0:
                acks += 1
                ackbytes += len(cmd.encode()) + len(r.stdout.encode())
    print(f"{who}: hook total {total} B over {len(files)} prompts (largest {peak}); acks filed {acks}; ack command+reply bytes {ackbytes}")
