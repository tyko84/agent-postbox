#!/usr/bin/env python3
"""The two-agents demo runs, says what happened, and cannot pass when it did not.

examples/two-agents/run.py is run as a subprocess, the way a visitor runs it:

* it exits 0 and prints the lines the README promises (the ASK surfaced by the
  real adapter, the ANSWER surfaced back, the rival claim refused with exit 3,
  the status picture), and removes its throwaway directory;
* `--keep` leaves the project behind with the three message files in it, and
  `--pause` waits between steps and survives a closed stdin;
* an AGENT_MAIL_DIR inherited from the caller is ignored: the demo passes and
  writes nothing there, whether it names a directory or a file.

Positive controls, because a demo that only ever passes proves nothing: the
demo is copied next to a copy of the tool whose pickup adapter prints nothing
and exits 0 (what a broken adapter looks like, and what an empty inbox looks
like), and next to one whose CLI no longer refuses a second claim. Each copy
must exit 1 and name the step that did not behave; an unmodified copy must
pass, so the copying is not what fails. Stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO = Path("examples") / "two-agents" / "run.py"
# What install.py copies, and install.py itself: enough of a clone for the demo to run.
CLONE_FILES = ("install.py", "agent_mail.py", "PROTOCOL.md", "SECURITY.md", "selftest.py",
               "PARTICIPANTS.template.md", "hooks/agent_mail_check.py", DEMO.as_posix())
TIMEOUT = 300
_PROJECT = re.compile(r"^  <project> = (\S+)", re.M)
_REFUSAL = "            if rivals:\n"


def run_demo(*args: str, root: Path = ROOT, stdin: str = "",
             **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    return subprocess.run([sys.executable, "-I", str(root / DEMO), *args], input=stdin,
                          env={**base, **env}, capture_output=True, text=True, timeout=TIMEOUT)


def project_of(cp: subprocess.CompletedProcess[str]) -> Path:
    found = _PROJECT.search(cp.stdout)
    assert found, f"no `<project> = ` line in:\n{cp.stdout}"
    return Path(found.group(1))


def copy_clone(dest: Path) -> Path:
    for rel in CLONE_FILES:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest / rel)
    return dest


class Demo(unittest.TestCase):
    def test_the_demo_passes_and_prints_what_happened(self) -> None:
        cp = run_demo()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        out = cp.stdout
        for line in (
            "$ python <clone>/install.py . --adapter claude-code",
            "  registered the claude-code prompt-submit adapter",
            "$ AGENT_MAIL_IDENTITY=agent-b python tools/agent-postbox/hooks/agent_mail_check.py",
            "Agent mail - 1 live message(s) addressed to 'agent-b'.",
            "  ASK from agent-a [awaiting your reply]",
            "You ('agent-b') owe a reply to 1 open ASK(s):",
            "$ AGENT_MAIL_IDENTITY=agent-a python tools/agent-postbox/hooks/agent_mail_check.py",
            "Agent mail - 2 live message(s) addressed to 'agent-a'.",
            "  ANSWER from agent-b",
            "  CLAIM from agent-b",
            "[exit 3]",
            "asks: open=0  oldest_open_age=-",
            "claims: held=1  expired=0  malformed_expiry=0  superseded=0",
            "ask results (claimed, not proven): with_result=1 (DONE=1)  answered=0  unanswered=0",
            "All 7 steps behaved as described.",
        ):
            self.assertIn(line + "\n", out, line)
        self.assertRegex(out, r"(?m)^scope 'billing/client\.py' is already held by agent-b \(")
        self.assertRegex(out, r"(?m)^  billing/client\.py  agent-b  \S+Z  0h59m$")
        # the order is the story: ASK sent, surfaced, answered, surfaced back, refused, status
        marks = ["--type ASK", "ASK from agent-a", "--type ANSWER", "ANSWER from agent-b",
                 "[exit 3]", "# status  mailbox:"]
        places = [out.index(m) for m in marks]
        self.assertEqual(places, sorted(places), marks)
        self.assertEqual(out.count("[exit 0]"), 7)      # install, 3 sends, 2 prompts, status
        self.assertEqual(out.count("[exit "), 8)
        self.assertNotIn("FAILED", out + cp.stderr)
        self.assertEqual(cp.stderr, "")
        # it cleaned up after itself
        project = project_of(cp)
        self.assertFalse(project.exists(), project)
        self.assertFalse(project.parent.exists(), project.parent)
        self.assertIn(f"Removed {project.parent}.", out)

    def test_keep_leaves_the_project_and_its_three_messages(self) -> None:
        cp = run_demo("--keep")
        project = project_of(cp)
        self.addCleanup(shutil.rmtree, project.parent, True)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn(f"Kept: {project}\n", cp.stdout)
        box = project / "docs" / "agent-mail"
        mail = sorted(p.name for p in box.glob("*.md") if p.name != "PARTICIPANTS.md")
        self.assertEqual(len(mail), 3, mail)
        heads = [(box / n).read_text(encoding="utf-8") for n in mail]   # ULIDs sort by time
        for text, kind in zip(heads, ("ASK", "CLAIM", "ANSWER"), strict=True):
            self.assertIn(f"\ntype: {kind}\n", text)
        self.assertIn("\nresult: DONE\n", heads[2])
        self.assertTrue((project / ".claude" / "settings.json").is_file())
        self.assertEqual([p.name for p in box.iterdir() if p.name.startswith(".")], [],
                         "no lock, marker or temp file is left in the mailbox")

    def test_pause_waits_between_steps_and_survives_a_closed_stdin(self) -> None:
        cp = run_demo("--pause", stdin="\n" * 6)             # one Enter per gap between 7 steps
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(cp.stdout.count("[Enter for the next step]"), 6)
        cp = run_demo("--pause", stdin="\n")                 # stdin ends after the first gap
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertEqual(cp.stdout.count("[Enter for the next step]"), 2)
        self.assertIn("All 7 steps behaved as described.\n", cp.stdout)

    def test_an_inherited_mailbox_is_ignored_and_never_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            team = Path(tmp) / "team-mailbox"
            team.mkdir()
            cp = run_demo(AGENT_MAIL_DIR=str(team), AGENT_MAIL_IDENTITY="somebody-else")
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertEqual(list(team.iterdir()), [], "the demo wrote into the caller's mailbox")
            self.assertIn("Agent mail - 1 live message(s) addressed to 'agent-b'.\n", cp.stdout)
            self.assertNotIn(str(team), cp.stdout)
            not_a_dir = Path(tmp) / "a-file"
            not_a_dir.write_text("x\n", encoding="utf-8")
            cp = run_demo(AGENT_MAIL_DIR=str(not_a_dir))
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertEqual(not_a_dir.read_text(encoding="utf-8"), "x\n")

    def test_usage_error_exits_2(self) -> None:
        cp = run_demo("--no-such-flag")
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertNotIn("== 1.", cp.stdout)


class CannotLie(unittest.TestCase):
    """The demo run against a broken tool must fail, at the step that did not behave."""

    def test_control_an_unmodified_copy_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cp = run_demo(root=copy_clone(Path(tmp).resolve()))
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertIn("All 7 steps behaved as described.\n", cp.stdout)

    def test_a_silent_adapter_fails_the_demo_at_the_pickup_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = copy_clone(Path(tmp).resolve())
            # Exits 0 and prints nothing: a broken adapter, indistinguishable from an empty inbox.
            (clone / "hooks" / "agent_mail_check.py").write_text(
                "raise SystemExit(0)\n", encoding="utf-8")
            cp = run_demo(root=clone)
            self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
            self.assertIn("FAILED at step 3 (agent-b's next prompt): expected: the ASK was "
                          "surfaced to agent-b", cp.stderr)
            self.assertIn("(no output)\n[exit 0]\n   [checked] the adapter exited 0\n", cp.stdout)
            self.assertNotIn("== 4.", cp.stdout)
            self.assertNotIn("behaved as described", cp.stdout)
            self.assertFalse(project_of(cp).parent.exists(), "a failed run cleans up too")

    def test_a_tool_that_grants_a_second_claim_fails_the_demo_at_the_claim_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clone = copy_clone(Path(tmp).resolve())
            tool = clone / "agent_mail.py"
            text = tool.read_text(encoding="utf-8")
            self.assertEqual(text.count(_REFUSAL), 1, "the line this control disables has moved")
            tool.write_text(text.replace(_REFUSAL, "            if rivals and False:\n"), encoding="utf-8")
            cp = run_demo(root=clone)
            self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
            self.assertIn("FAILED at step 6 (agent-a tries to claim the same file): expected: "
                          "the second claim exited 3", cp.stderr)
            self.assertIn("== 5.", cp.stdout)
            self.assertNotIn("== 7.", cp.stdout)


if __name__ == "__main__":
    unittest.main()
