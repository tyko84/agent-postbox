#!/usr/bin/env python3
"""Probe which `send` spellings Claude Code's `--permission-mode dontAsk` lets through.

Live (spends about $0.003 per case with haiku); run it by hand, never from CI:

    python3 docs/data/permission-probe/matrix.py WORKDIR [case ...]

WORKDIR is created and holds a throwaway project. Writes results.json next to this file.
Each case gives the model one exact command to run and records whether the host listed a
permission denial for it, and whether a message file then appeared (the command ran).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent.parent.parent / "agent_mail.py"
REL = "python3 tools/agent-postbox/agent_mail.py"
SEND = "send --type NOTICE --from a --to b --subject s"


def cases(proj):
    ab = str(proj / "tools/agent-postbox/agent_mail.py")
    rel_rule = f"Bash({REL} *)"
    abq_rule = f'Bash(python3 "{ab}" *)'
    abp_rule = f"Bash(python3 {ab} *)"
    broad = "Bash(python3 *agent_mail.py *)"
    all3 = [rel_rule, abq_rule, abp_rule]
    c = {}
    c["relative_oneline"] = (f"{REL} {SEND} --body 'hello'", all3)
    c["relative_multiline_nohash"] = (f"{REL} {SEND} --body 'one\ntwo'", all3)
    c["relative_multiline_hash"] = (f"{REL} {SEND} --body 'one\n# two'", all3)
    c["relative_multiline_indented_hash"] = (f"{REL} {SEND} --body 'one\n  # two'", all3)
    c["relative_multiline_hash_doublequoted"] = (f'{REL} {SEND} --body "one\n# two"', all3)
    c["relative_hash_midline"] = (f"{REL} {SEND} --body 'one # two'", all3)
    c["heredoc_stdin_with_hash"] = (f"{REL} {SEND} --body-file - <<'EOF'\none\n# two\nEOF", all3)
    c["heredoc_stdin_with_braces"] = (
        f"{REL} {SEND} --body-file - <<'EOF'\nstats {{'count': 3, 'total': 7}}\nEOF", all3)
    c["heredoc_stdin_with_backticks"] = (f"{REL} {SEND} --body-file - <<'EOF'\nrun `ls` now\nEOF", all3)
    c["body_file_path"] = (f"{REL} {SEND} --body-file body.txt", all3)
    c["abs_quoted_oneline"] = (f"python3 \"{ab}\" {SEND} --body 'hello'", all3)
    c["abs_plain_oneline"] = (f"python3 {ab} {SEND} --body 'hello'", all3)
    c["chain_echo"] = (f"{REL} {SEND} --body 'hi' && echo sent", all3)
    c["chain_unallowed_second_command"] = (f"{REL} {SEND} --body 'hi' && python3 -c 1", all3)
    c["only_relative_rule__abs_quoted"] = (f"python3 \"{ab}\" {SEND} --body 'hello'", [rel_rule])
    c["only_relative_rule__relative"] = (f"{REL} {SEND} --body 'hello'", [rel_rule])
    c["no_rule__relative"] = (f"{REL} {SEND} --body 'hello'", [])
    c["broad_rule__abs_quoted"] = (f"python3 \"{ab}\" {SEND} --body 'hello'", [broad])
    c["broad_rule__other_script_same_name"] = (
        f"python3 /tmp/elsewhere/agent_mail.py {SEND} --body 'x'", [broad])
    return c


def main():
    work = Path(sys.argv[1]).resolve()
    shutil.rmtree(work, ignore_errors=True)
    proj = work / "proj"
    (proj / "tools/agent-postbox").mkdir(parents=True)
    (proj / "docs/agent-mail").mkdir(parents=True)
    shutil.copy(TOOL, proj / "tools/agent-postbox/agent_mail.py")
    table = cases(proj)
    names = sys.argv[2:] or list(table)
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    out = {"claude_version": version, "model": "haiku", "permission_mode": "dontAsk", "cases": {}}
    for name in names:
        cmd, allow = table[name]
        shutil.rmtree(proj / "docs/agent-mail", ignore_errors=True)
        (proj / "docs/agent-mail").mkdir()
        (proj / "body.txt").write_text("# t\nx\n")
        prompt = "Run exactly this one Bash command, with no changes, then say done:\n" + cmd
        args = ["claude", "-p", prompt, "--model", "haiku", "--permission-mode", "dontAsk",
                "--output-format", "json", "--add-dir", str(proj)]
        if allow:
            args += ["--allowedTools", *allow]
        r = subprocess.run(args, cwd=proj, capture_output=True, text=True, timeout=170,
                           env={**os.environ})
        try:
            j = json.loads(r.stdout)
        except ValueError:
            out["cases"][name] = {"error": (r.stdout + r.stderr)[:200]}
            continue
        denied = len(j.get("permission_denials", []))
        wrote = len(list((proj / "docs/agent-mail").glob("*.md")))
        out["cases"][name] = {"allow_rules": len(allow), "denied": denied, "message_written": wrote,
                              "cost_usd": round(j.get("total_cost_usd", 0), 4)}
        print(f"{name:42} denied={denied} written={wrote}", flush=True)
    (HERE / "results.json").write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
