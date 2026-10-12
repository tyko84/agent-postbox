#!/usr/bin/env python3
"""Two scripted sessions exchange mail through the real agent-postbox tool.

    python examples/two-agents/run.py            # run it; a few seconds
    python examples/two-agents/run.py --pause    # wait for Enter between steps
    python examples/two-agents/run.py --keep     # leave the project directory behind

What is real: every command printed after a `$` is run as a subprocess, and
the text under it is what that command printed. The installer is this clone's
install.py, the CLI is the agent_mail.py it copied into the throwaway project,
the pickup is the hooks/agent_mail_check.py it copied, and the messages are
files in the project's docs/agent-mail/.

What is scripted: the two "agents". There is no model and no host. A "prompt"
is this script running the pickup adapter with AGENT_MAIL_IDENTITY set, which
is what a host's prompt-submit hook does before the model reads the prompt.

A demo that cannot lie: after every step the script checks that the command
exited as the narration says and printed what the narration says. The first
step that does not is reported and the exit status is 1. Any AGENT_MAIL_*
variable in the caller's environment is dropped, so the demo never reads or
writes a mailbox other than its own.

Exit status: 0 every step behaved as described; 1 a step did not; 2 usage, or
a Python older than 3.10. Stdlib only. POSIX only, like the tool.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the clone
TOOL = "tools/agent-postbox/agent_mail.py"          # where install.py puts the CLI
HOOK = "tools/agent-postbox/hooks/agent_mail_check.py"
MAILBOX = "docs/agent-mail"
SCOPE = "billing/client.py"
SUBJECT = "can you add a retry test for the billing client?"
TIMEOUT = 60
_ID = re.compile(r"^id: ([0-9A-HJKMNP-TV-Z]{26})$", re.M)


class StepFailed(Exception):
    """A step did not behave as its narration says."""


class Demo:
    def __init__(self, project: Path, pause: bool) -> None:
        self.project = project
        self.pause = pause
        self.step_no = 0
        self.title = ""
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}

    # -- printing -----------------------------------------------------------

    def step(self, title: str, *narration: str) -> None:
        if self.step_no and self.pause:
            try:
                input("\n[Enter for the next step] ")
            except EOFError:        # stdin closed: carry on without pausing
                self.pause = False
                print()
        self.step_no += 1
        self.title = title
        print(f"\n== {self.step_no}. {title}")
        for line in narration:
            print(f"# {line}")

    def check(self, ok: bool, what: str) -> None:
        if not ok:
            raise StepFailed(what)
        print(f"   [checked] {what}")

    # -- running ------------------------------------------------------------

    def run(self, shown: list[str], *, identity: str | None = None,
            stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run `python <shown...>` in the project directory and print what it printed.

        The line after `$` is the command that runs, with `python` standing for this
        interpreter and `<clone>` for the clone's path. In the output, the project's
        path is printed as `<project>`. The header names all three."""
        argv = [sys.executable] + [a.replace("<clone>", str(ROOT)) for a in shown]
        env = dict(self.env)
        prefix = ""
        if identity is not None:
            env["AGENT_MAIL_IDENTITY"] = identity
            prefix = f"AGENT_MAIL_IDENTITY={identity} "
        words = [a if a.startswith("<clone>/") else shlex.quote(a) for a in shown]
        print(wrapped(f"$ {prefix}python", words))
        if stdin is not None:
            print(f"  (stdin) {stdin.rstrip()}")
        cp = subprocess.run(argv, cwd=self.project, env=env, input=stdin if stdin is not None else "",
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=TIMEOUT)
        out = cp.stdout.rstrip("\n").replace(str(self.project), "<project>")
        print(out if out else "(no output)")
        print(f"[exit {cp.returncode}]")
        return cp

    def mail(self, *args: str, identity: str, stdin: str | None = None,
             ) -> subprocess.CompletedProcess[str]:
        return self.run([TOOL, *args], identity=identity, stdin=stdin)

    def prompt(self, identity: str) -> subprocess.CompletedProcess[str]:
        """What a host's prompt-submit hook does for this session: run the adapter."""
        return self.run([HOOK], identity=identity)

    def message_files(self) -> list[str]:
        return sorted(p.name for p in (self.project / MAILBOX).glob("*.md")
                      if p.name != "PARTICIPANTS.md")


def wrapped(head: str, words: list[str], width: int = 96) -> str:
    """One shell command, continued with a backslash where a line would pass `width`.
    A flag stays on the line of its value."""
    units: list[str] = []
    for word in words:
        if units and units[-1].startswith("--") and " " not in units[-1] \
                and not word.startswith("--"):
            units[-1] += " " + word
        else:
            units.append(word)
    lines = [head]
    for word in units:
        if len(lines[-1]) + 1 + len(word) > width and lines[-1].strip():
            lines[-1] += " \\"
            lines.append("      " + word)
        else:
            lines[-1] += " " + word
    return "\n".join(lines)


def new_id(cp: subprocess.CompletedProcess[str]) -> str:
    found = _ID.search(cp.stdout)
    if cp.returncode != 0 or not found or "wrote " not in cp.stdout:
        raise StepFailed("send did not report `wrote <file>` and `id: <id>` with exit 0")
    return found.group(1)


def play(d: Demo) -> None:
    project = d.project
    # install.py warns when the project has no .git, because mailbox discovery stops
    # at the nearest one. An empty directory is enough to mark the project root.
    (project / ".git").mkdir()

    d.step("Install into a project",
           "The project is an empty throwaway directory. install.py copies the tool into",
           "tools/agent-postbox/, creates the mailbox docs/agent-mail/, and registers the",
           "pickup adapter as a prompt-submit hook in the project's .claude/settings.json.")
    cp = d.run(["<clone>/install.py", ".", "--adapter", "claude-code"])
    d.check(cp.returncode == 0, "install.py exited 0")
    d.check((project / TOOL).is_file() and (project / HOOK).is_file(),
            f"{TOOL} and the adapter under hooks/ exist")
    d.check((project / MAILBOX / "PARTICIPANTS.md").is_file(), f"{MAILBOX}/ exists")
    settings = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = [h.get("command", "") for g in settings["hooks"]["UserPromptSubmit"]
             for h in g["hooks"]]
    d.check(any("agent_mail_check.py" in c for c in hooks),
            "the adapter is registered as UserPromptSubmit in .claude/settings.json")

    d.step("agent-a asks agent-b a question",
           "Session A is about to touch a file and wants to know who is in it.",
           "A message is one Markdown file in the mailbox; `send` prints its path and id.")
    cp = d.mail("send", "--type", "ASK", "--from", "agent-a", "--to", "agent-b",
                "--subject", SUBJECT, "--body-file", "-", identity="agent-a",
                stdin="I am not in billing/client.py. If you are, please add the test there.\n")
    ask = new_id(cp)
    d.check(True, f"send exited 0 and printed the id {ask}")
    d.check(len(d.message_files()) == 1 and d.message_files()[0].startswith(ask),
            "the mailbox holds exactly one message file, named after that id")

    d.step("agent-b's next prompt",
           "Session B did not poll and was not told to look. Its host runs the adapter",
           "before every prompt; this is the text the model would find in its context.")
    cp = d.prompt("agent-b")
    d.check(cp.returncode == 0, "the adapter exited 0")
    d.check("ASK from agent-a [awaiting your reply]" in cp.stdout and SUBJECT in cp.stdout
            and f"id: {ask}" in cp.stdout,
            "the ASK was surfaced to agent-b with its sender, subject and id")
    d.check("You ('agent-b') owe a reply to 1 open ASK(s):" in cp.stdout,
            "the adapter says agent-b owes one reply")

    d.step("agent-b claims the file, then answers",
           "A CLAIM is a lease with an expiry, not a lock: 'I am working on this until T'.",
           "The ANSWER cites the ASK's id and carries what agent-b claims happened.")
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
               ).strftime("%Y-%m-%dT%H:%M:%SZ")
    cp = d.mail("send", "--type", "CLAIM", "--from", "agent-b", "--to", "agent-a",
                "--subject", "editing the billing client", "--scope", SCOPE,
                "--expires", expires, "--body-file", "-", identity="agent-b",
                stdin="Adding the retry test now.\n")
    claim = new_id(cp)
    d.check(True, f"the claim on {SCOPE} was written ({claim})")
    cp = d.mail("send", "--type", "ANSWER", "--from", "agent-b", "--to", "agent-a",
                "--reply-to", ask, "--result", "DONE", "--subject", "re: retry test",
                "--body-file", "-", identity="agent-b",
                stdin="Added tests/test_retry.py; I hold billing/client.py for the next hour.\n")
    answer = new_id(cp)
    d.check(True, f"the answer was written ({answer})")

    d.step("agent-a's next prompt",
           "Session A did not poll either. The answer and the claim are in its context",
           "the next time its user types anything.")
    cp = d.prompt("agent-a")
    d.check(cp.returncode == 0, "the adapter exited 0")
    d.check("ANSWER from agent-b" in cp.stdout and f"id: {answer}" in cp.stdout,
            "the ANSWER was surfaced to agent-a")
    d.check("CLAIM from agent-b" in cp.stdout and f"holds until: {expires}" in cp.stdout,
            "agent-b's claim was surfaced to agent-a with its expiry")

    d.step("agent-a tries to claim the same file",
           "One holder per scope. The second claim is refused, exit status 3, and",
           "nothing is written.")
    before = d.message_files()
    cp = d.mail("send", "--type", "CLAIM", "--from", "agent-a", "--to", "agent-b",
                "--subject", "me too", "--scope", SCOPE, "--expires", expires,
                "--body-file", "-", identity="agent-a", stdin="I would like that file as well.\n")
    d.check(cp.returncode == 3, "the second claim exited 3")
    d.check(f"scope '{SCOPE}' is already held by agent-b" in cp.stdout,
            "the refusal names the scope and its holder")
    d.check(d.message_files() == before and len(before) == 3,
            "the mailbox still holds three message files: the ASK, the CLAIM and the ANSWER")

    d.step("The picture: status",
           "Status is derived from the files every time it is asked for; nothing stores it.",
           "The ASK is no longer open because an ANSWER cites it. DONE is what agent-b",
           "claims, not something the tool verified.")
    cp = d.mail("status", identity="agent-a")
    d.check(cp.returncode == 0, "status exited 0")
    d.check(re.search(r"^asks: open=0\b", cp.stdout, re.M) is not None, "no ASK is open")
    d.check(re.search(r"^claims: held=1\b", cp.stdout, re.M) is not None
            and re.search(rf"^  {re.escape(SCOPE)}\s+agent-b\s+{re.escape(expires)}\b",
                          cp.stdout, re.M) is not None,
            f"one claim is held: {SCOPE}, by agent-b, until {expires}")
    d.check("ask results (claimed, not proven): with_result=1 (DONE=1)" in cp.stdout,
            "the result is reported as claimed, not proven")
    d.check("PROBLEMS:" not in cp.stdout and "quarantined: 0" in cp.stdout,
            "status reports no problems")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Two scripted sessions exchange mail through the real agent-postbox "
                    "tool. No model, no account, no network.")
    ap.add_argument("--keep", action="store_true",
                    help="leave the throwaway project directory behind and print its path")
    ap.add_argument("--pause", action="store_true",
                    help="wait for Enter between steps (for reading along or recording)")
    args = ap.parse_args(argv)
    if sys.version_info < (3, 10):  # noqa: UP036  (say so, instead of a traceback)
        print("agent-postbox needs Python 3.10 or newer", file=sys.stderr)
        return 2

    try:
        tmp = Path(tempfile.mkdtemp(prefix="postbox-demo-")).resolve()
        project = tmp / "project"
        project.mkdir()
    except OSError as e:
        print(f"FAILED before step 1: no temporary directory: {e}", file=sys.stderr)
        return 1
    demo = Demo(project, args.pause)
    print("agent-postbox: two scripted sessions, the real tool. No model, no network.")
    print(f"  python    = {sys.executable}")
    print(f"  <clone>   = {ROOT}")
    print(f"  <project> = {project}   (every command below runs here)")
    status = 0
    try:
        play(demo)
        print(f"\nAll {demo.step_no} steps behaved as described.")
    except StepFailed as e:
        print(f"\nFAILED at step {demo.step_no} ({demo.title}): expected: {e}", file=sys.stderr)
        status = 1
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
        print(f"\nFAILED at step {demo.step_no} ({demo.title or 'setup'}): "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        status = 1
    finally:
        sys.stdout.flush()
        if args.keep:
            print(f"\nKept: {project}")
            print(f"  ls {project / MAILBOX}")
            print(f"  cd {project} && python3 {TOOL} list")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            gone = "Removed" if not tmp.exists() else "COULD NOT REMOVE"
            print(f"\n{gone} {tmp}. Run with --keep to look at the files.")
            if tmp.exists():
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
