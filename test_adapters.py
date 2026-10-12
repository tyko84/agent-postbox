#!/usr/bin/env python3
"""Host adapters, tested against the hosts' DOCUMENTED shapes. Stdlib only.

What this file is and is not: no Codex and no Cursor runs here. Every test
starts the shipped pickup adapter (`hooks/agent_mail_check.py`, unchanged) the
way a host's documentation says the host starts a command hook, feeds it the
stdin the documentation describes, and asserts on the stdout and exit status
the documentation says the host acts on. docs/hosts.md says the same thing in
its "how verified" column, and gives the checklist a person runs in the real
host.

Codex (the adapter `install.py --adapter codex` registers). Source, read
2026-10-12: https://learn.chatgpt.com/docs/hooks (developers.openai.com/codex/hooks
redirects there). The sentences these tests rest on:
  * "Every command hook receives one JSON object on `stdin`."
  * UserPromptSubmit: "Plain text on `stdout` is added as extra developer context."
  * "Exit `0` with no output is treated as success and Codex continues."
  * UserPromptSubmit: "You can also use exit code `2` and write the blocking
    reason to `stderr`."  (so the registered command must never exit 2)
  * "By default, Codex limits each model-visible hook-output message to roughly
    2,500 tokens."
  * "Commands run with the session `cwd` as their working directory."

Cursor is NOT an adapter target; one test pins why. Source, read 2026-10-12:
https://cursor.com/docs/hooks : `beforeSubmitPrompt` has exactly two output
fields, `continue` and `user_message` ("Message shown to the user when the
prompt is blocked"); neither reaches the model.

Silence is both the empty-inbox case and the symptom of total breakage, so
every silent case here sits next to a POSITIVE control on the same mailbox.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOOL = HERE / "agent_mail.py"
HOOK = HERE / "hooks" / "agent_mail_check.py"
INSTALL = HERE / "install.py"
HOSTS_DOC = HERE / "docs" / "hosts.md"
TIMEOUT = 120

# The UserPromptSubmit handler of the documented `hooks.json` example, copied from
# https://learn.chatgpt.com/docs/hooks ("Config shape"), read 2026-10-12.
CODEX_DOC_CONFIG: dict[str, Any] = {
    "hooks": {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "/usr/bin/python3 \"$(git rev-parse --show-toplevel)/.codex/hooks/user_prompt_submit_data_flywheel.py\"",
                    }
                ]
            }
        ]
    }
}

# The same page prints no complete UserPromptSubmit input object, only the field
# tables ("Common input fields": session_id, transcript_path, cwd, hook_event_name,
# model; permission_mode for this event; "UserPromptSubmit": turn_id, prompt). This
# object has exactly those fields; the values are invented.
CODEX_STDIN: dict[str, Any] = {
    "session_id": "0199a213-81c0-7800-8aa1-bbab2a035a53",
    "transcript_path": None,
    "cwd": "/tmp/project",
    "hook_event_name": "UserPromptSubmit",
    "model": "gpt-5-codex",
    "permission_mode": "default",
    "turn_id": "turn-1",
    "prompt": "what changed since yesterday?",
}

# https://cursor.com/docs/hooks ("beforeSubmitPrompt", Output), read 2026-10-12.
CURSOR_BEFORE_SUBMIT_OUTPUT_FIELDS = {"continue", "user_message"}

# The shape of the command `install.py --adapter codex` registers, with the
# interpreter and the script path as arguments instead of `python3` and
# `$(git rev-parse --show-toplevel)/...` (the test of the installed command
# below runs the registered string itself).
CODEX_COMMAND_SHAPE = '"$1" "$2" || true'


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env.update(extra)
    return env


def as_codex(script: Path, stdin: bytes, cwd: Path, **env: str) -> subprocess.CompletedProcess[bytes]:
    """Run `script` the way Codex is documented to run a command hook."""
    return subprocess.run(["/bin/sh", "-c", CODEX_COMMAND_SHAPE, "sh", sys.executable, str(script)],
                          input=stdin, capture_output=True, cwd=cwd, env=clean_env(**env),
                          timeout=TIMEOUT)


def send(box: Path, tool: Path = TOOL, /, **fields: str) -> str:
    args = [sys.executable, "-I", str(tool), "send", "--body-file", "-"]
    for k, v in fields.items():
        args += [f"--{k.replace('_', '-')}", v]
    cp = subprocess.run(args, input="body\n", capture_output=True, text=True, timeout=TIMEOUT,
                        env=clean_env(AGENT_MAIL_DIR=str(box)))
    assert cp.returncode == 0, cp.stderr
    return next(ln.split()[1] for ln in cp.stdout.splitlines() if ln.startswith("id: "))


def is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


PAYLOAD = json.dumps(CODEX_STDIN).encode()


@unittest.skipUnless(os.name == "posix", "the registered command is a POSIX shell command")
class CodexPickup(unittest.TestCase):
    """The adapter, started as Codex's UserPromptSubmit command hook."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="test-adapters-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.box = self.tmp / "mail"
        self.box.mkdir()
        self.mid = send(self.box, type="NOTICE", **{"from": "alice"}, to="bob",
                        subject="positive control 7f3a")

    def codex(self, stdin: bytes = PAYLOAD, /, **env: str) -> subprocess.CompletedProcess[bytes]:
        env.setdefault("AGENT_MAIL_DIR", str(self.box))
        return as_codex(HOOK, stdin, self.tmp, **env)

    def assert_delivered(self, cp: subprocess.CompletedProcess[bytes]) -> str:
        out = cp.stdout.decode()
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(cp.stderr, b"")
        self.assertIn("  NOTICE from alice\n    positive control 7f3a\n"
                      f"    id: {self.mid}\n", out)
        return out

    def test_positive_control_a_live_message_is_plain_text_on_stdout(self) -> None:
        out = self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))
        self.assertTrue(out.startswith("Agent mail - 1 live message(s) addressed to 'bob'.\n"))
        # Codex adds PLAIN TEXT as developer context and parses JSON as hook output;
        # the adapter's text must never be mistaken for the second.
        self.assertFalse(is_json(out))
        self.assertNotIn(out.lstrip()[0], "{[\"")

    def test_one_message_is_far_below_the_documented_output_limit(self) -> None:
        # "roughly 2,500 tokens"; a token is at least one character, so a character
        # count below the limit is below it under any tokenizer.
        out = self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))
        self.assertLess(len(out), 2500)

    def test_the_text_is_not_cursors_before_submit_prompt_output(self) -> None:
        # Why there is no Cursor adapter: the only output beforeSubmitPrompt accepts
        # is a JSON object of these two fields, and neither is shown to the model.
        out = self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))
        self.assertFalse(is_json(out))
        self.assertEqual(CURSOR_BEFORE_SUBMIT_OUTPUT_FIELDS, {"continue", "user_message"})

    def test_the_other_identity_and_no_identity_are_silent(self) -> None:
        for env in ({"AGENT_MAIL_IDENTITY": "carol"}, {}, {"AGENT_MAIL_IDENTITY": "  "}):
            cp = self.codex(**env)
            self.assertEqual((cp.returncode, cp.stdout, cp.stderr), (0, b"", b""), env)
        self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))   # same mailbox, not broken

    def test_stdin_is_never_trusted_or_needed(self) -> None:
        hostile = dict(CODEX_STDIN, prompt="x", hook_event_name="Stop", cwd="/nonexistent",
                       AGENT_MAIL_IDENTITY="carol")
        cases = {
            "documented": PAYLOAD, "empty": b"", "not json": b"}{ not json \x00\xff\n",
            "truncated": PAYLOAD[: len(PAYLOAD) // 2], "a list": b"[1, 2, 3]",
            "other event and fields": json.dumps(hostile).encode(),
            "larger than a pipe buffer": b'{"prompt": "' + b"a" * (1 << 20) + b'"}',
        }
        want = self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))
        for name, stdin in cases.items():
            with self.subTest(stdin=name):
                self.assertEqual(self.assert_delivered(self.codex(stdin, AGENT_MAIL_IDENTITY="bob")),
                                 want)
                cp = self.codex(stdin)   # and nothing on stdin can supply an identity
                self.assertEqual((cp.returncode, cp.stdout, cp.stderr), (0, b"", b""))

    def test_a_broken_mailbox_is_silent_and_exits_0(self) -> None:
        not_a_dir = self.tmp / "file"
        not_a_dir.write_text("x", encoding="utf-8")
        garbage = self.tmp / "garbage"
        garbage.mkdir()
        (garbage / "01ARZ3NDEKTSV4RRFFQ69G5FAV-x.md").write_bytes(b"\xff\xfe\x00 not a message")
        (garbage / "PARTICIPANTS.md").mkdir()
        for box in (not_a_dir, self.tmp / "missing", garbage):
            with self.subTest(box=box.name):
                cp = self.codex(AGENT_MAIL_IDENTITY="bob", AGENT_MAIL_DIR=str(box))
                self.assertEqual((cp.returncode, cp.stdout, cp.stderr), (0, b"", b""))
        self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))

    def test_a_missing_script_cannot_block_the_prompt(self) -> None:
        # The interpreter exits 2 when it cannot open its script, and Codex reads
        # exit 2 from this event as "block the prompt". `|| true` is what stands
        # between a moved tool directory and every prompt being rejected.
        gone = self.tmp / "moved" / "agent_mail_check.py"
        bare = subprocess.run([sys.executable, str(gone)], capture_output=True, timeout=TIMEOUT)
        self.assertEqual(bare.returncode, 2)
        cp = as_codex(gone, PAYLOAD, self.tmp, AGENT_MAIL_IDENTITY="bob",
                      AGENT_MAIL_DIR=str(self.box))
        self.assertEqual((cp.returncode, cp.stdout), (0, b""))

    def test_reads_only(self) -> None:
        def snapshot() -> dict[str, tuple[int, int]]:
            return {str(p.relative_to(self.tmp)): (p.stat().st_size, p.stat().st_mtime_ns)
                    for p in sorted(self.tmp.rglob("*"))}
        before = snapshot()
        self.assert_delivered(self.codex(AGENT_MAIL_IDENTITY="bob"))
        self.assertEqual(snapshot(), before)


def install(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(INSTALL), str(project), *args],
                          capture_output=True, text=True, timeout=TIMEOUT, env=clean_env())


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


class CodexInstall(unittest.TestCase):
    """`install.py --adapter codex`: merge or refuse, never overwrite."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="test-adapters-")
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name).resolve() / "project"
        (self.project / ".git").mkdir(parents=True)   # install.py only asks whether it exists
        self.config = self.project / ".codex" / "hooks.json"

    def handlers(self) -> list[dict[str, Any]]:
        data = json.loads(self.config.read_text(encoding="utf-8"))
        return [h for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]]

    def test_a_fresh_registration_has_the_documented_shape(self) -> None:
        cp = install(self.project, "--adapter", "codex")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("registered the codex prompt-submit adapter", cp.stdout)
        self.assertIn("/hooks", cp.stdout)            # the trust step is named, not implied
        data = json.loads(self.config.read_text(encoding="utf-8"))
        doc = CODEX_DOC_CONFIG
        self.assertEqual(set(data), set(doc))
        self.assertEqual(set(data["hooks"]), set(doc["hooks"]))
        (group,), (doc_group,) = data["hooks"]["UserPromptSubmit"], doc["hooks"]["UserPromptSubmit"]
        self.assertEqual(set(group), set(doc_group))  # no matcher: "isn't currently used"
        (handler,), (doc_handler,) = group["hooks"], doc_group["hooks"]
        self.assertEqual(set(handler), set(doc_handler))
        self.assertEqual(handler["type"], "command")
        interpreter, rest = handler["command"].split(" ", 1)
        self.assertIsNotNone(shutil.which(interpreter) or (Path(interpreter).is_file() or None))
        self.assertEqual(rest, '"$(git rev-parse --show-toplevel)/tools/agent-postbox/hooks/'
                               'agent_mail_check.py" || true')
        self.assertTrue((self.project / "tools/agent-postbox/hooks/agent_mail_check.py").is_file())
        self.assertFalse((self.project / ".claude").exists())

    def test_registering_twice_changes_nothing(self) -> None:
        install(self.project, "--adapter", "codex")
        before = tree(self.project)
        cp = install(self.project, "--adapter", "codex")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("codex adapter already registered (no change)", cp.stdout)
        self.assertEqual(tree(self.project), before)
        self.assertEqual(len(self.handlers()), 1)

    def test_an_existing_config_is_merged_and_backed_up(self) -> None:
        theirs: dict[str, Any] = {
            "description": "ours",
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "true",
                                                 "timeout": 5}]}],
                "Stop": [{"hooks": [{"type": "command", "command": "echo '{}'"}]}],
            },
        }
        self.config.parent.mkdir()
        raw = json.dumps(theirs, indent=4)
        self.config.write_text(raw, encoding="utf-8")
        cp = install(self.project, "--adapter", "codex")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["description"], "ours")
        self.assertEqual(data["hooks"]["Stop"], theirs["hooks"]["Stop"])
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0],
                         theirs["hooks"]["UserPromptSubmit"][0])     # theirs, first, untouched
        self.assertEqual(len(self.handlers()), 2)
        self.assertIn("agent_mail_check.py", self.handlers()[1]["command"])
        self.assertEqual(self.config.with_name("hooks.json.bak").read_text(encoding="utf-8"), raw)

    def test_refusals_change_nothing_and_say_why_in_one_line(self) -> None:
        def unmergeable(text: str) -> None:
            self.config.parent.mkdir()
            self.config.write_text(text, encoding="utf-8")

        def inline_toml() -> None:
            self.config.parent.mkdir()
            (self.config.parent / "config.toml").write_text(
                'model = "x"\n\n[[hooks.UserPromptSubmit]]\n\n[[hooks.UserPromptSubmit.hooks]]\n'
                'type = "command"\ncommand = "true"\n', encoding="utf-8")

        cases: dict[str, tuple[Any, tuple[str, ...], str]] = {
            "not json": (lambda: unmergeable('{"hooks": {'), (), "is not readable JSON"),
            "a list": (lambda: unmergeable("[]"), (), "is not a JSON object"),
            "hooks is a list": (lambda: unmergeable('{"hooks": []}'), (), "not an object"),
            "event is an object": (lambda: unmergeable('{"hooks": {"UserPromptSubmit": {}}}'), (),
                                   "not a list"),
            "group is a string": (lambda: unmergeable('{"hooks": {"UserPromptSubmit": ["x"]}}'), (),
                                  "not a matcher group"),
            "inline toml hooks": (inline_toml, (), "already defines hooks inline"),
            "no git": (lambda: (self.project / ".git").rmdir(), (), "needs a git repository"),
            "unquotable dest": (lambda: None, ("--dest", 'tools/a"$(id)'), "cannot quote --dest"),
        }
        for name, (arrange, extra, why) in cases.items():
            with self.subTest(case=name):
                shutil.rmtree(self.project)
                (self.project / ".git").mkdir(parents=True)
                arrange()
                before = tree(self.project)
                cp = install(self.project, "--adapter", "codex", *extra)
                self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
                self.assertEqual(cp.stdout, "")
                lines = [ln for ln in cp.stderr.splitlines() if ln.startswith("ABORT")]
                self.assertEqual(len(lines), 1, cp.stderr)
                self.assertIn(why, lines[0])
                self.assertTrue(lines[0].endswith("Nothing was changed."))
                self.assertEqual(tree(self.project), before)
                check = install(self.project, "--adapter", "codex", "--check", *extra)
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertIn("WOULD REFUSE", check.stdout)
                self.assertIn(why, check.stdout)
                self.assertEqual(tree(self.project), before)

    def test_a_config_toml_without_hook_tables_does_not_refuse(self) -> None:
        self.config.parent.mkdir()
        (self.config.parent / "config.toml").write_text(
            'model = "x"\nnotes = "[hooks] are described below"\n\n[features]\nhooks = true\n',
            encoding="utf-8")
        cp = install(self.project, "--adapter", "codex")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(len(self.handlers()), 1)

    def test_check_reports_and_writes_nothing(self) -> None:
        before = tree(self.project)
        cp = install(self.project, "--adapter", "codex", "--check")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("WOULD register the codex prompt-submit adapter", cp.stdout)
        self.assertEqual(tree(self.project), before)

    def test_the_claude_code_adapter_is_registered_as_before_and_both_can_coexist(self) -> None:
        cp = install(self.project, "--adapter", "claude-code")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("registered the claude-code prompt-submit adapter", cp.stdout)
        self.assertNotIn("/hooks", cp.stdout)
        self.assertFalse(self.config.exists())
        settings = self.project / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        (group,) = data["hooks"]["UserPromptSubmit"]
        (handler,) = group["hooks"]
        self.assertEqual(handler["command"].split(" ", 1)[1],
                         '"$CLAUDE_PROJECT_DIR/tools/agent-postbox/hooks/agent_mail_check.py"')
        claude_bytes = settings.read_bytes()
        cp = install(self.project, "--adapter", "codex")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(len(self.handlers()), 1)
        self.assertEqual(settings.read_bytes(), claude_bytes)
        again = install(self.project, "--adapter", "claude-code")
        self.assertIn("claude-code adapter already registered (no change)", again.stdout)

    @unittest.skipUnless(os.name == "posix" and shutil.which("git"), "needs a POSIX shell and git")
    def test_the_registered_command_delivers_from_a_subdirectory(self) -> None:
        # The string in hooks.json itself, run by a shell from a subdirectory of a
        # real git repository ("Commands run with the session `cwd`"), with the
        # documented payload on stdin and no AGENT_MAIL_DIR: the mailbox is found
        # by walking up from the cwd, as it would be in a session.
        (self.project / ".git").rmdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, capture_output=True,
                       timeout=TIMEOUT)
        self.assertEqual(install(self.project, "--adapter", "codex").returncode, 0)
        (command,) = [h["command"] for h in self.handlers()]
        mid = send(self.project / "docs" / "agent-mail",
                   self.project / "tools" / "agent-postbox" / "agent_mail.py",
                   type="ASK", **{"from": "alice"}, to="bob", subject="positive control 91c2")
        sub = self.project / "src" / "deep"
        sub.mkdir(parents=True)

        def prompt(**env: str) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(["/bin/sh", "-c", command], input=PAYLOAD, capture_output=True,
                                  cwd=sub, env=clean_env(**env), timeout=TIMEOUT)
        cp = prompt(AGENT_MAIL_IDENTITY="bob")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn(f"  ASK from alice [awaiting your reply]\n    positive control 91c2\n"
                      f"    id: {mid}\n", cp.stdout.decode())
        silent = prompt()
        self.assertEqual((silent.returncode, silent.stdout), (0, b""))
        # the tool directory moves: still exit 0, still no stdout, prompt not blocked
        (self.project / "tools").rename(self.project / "tools-moved")
        moved = prompt(AGENT_MAIL_IDENTITY="bob")
        self.assertEqual((moved.returncode, moved.stdout), (0, b""))


@unittest.skipUnless(HOSTS_DOC.is_file(), "docs/hosts.md is not here")
class HostsDoc(unittest.TestCase):
    """docs/hosts.md shows the config the installer writes, and claims no more than was done."""

    def setUp(self) -> None:
        self.text = HOSTS_DOC.read_text(encoding="utf-8")

    def test_the_printed_hooks_json_is_what_the_installer_registers(self) -> None:
        blocks = [b.split("```", 1)[0] for b in self.text.split("```json\n")[1:]]
        configs = [json.loads(b) for b in blocks if '"UserPromptSubmit"' in b]
        self.assertEqual(len(configs), 1)
        with tempfile.TemporaryDirectory(prefix="test-adapters-") as tmp:
            project = Path(tmp).resolve() / "project"
            (project / ".git").mkdir(parents=True)
            self.assertEqual(install(project, "--adapter", "codex").returncode, 0)
            written = json.loads((project / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        handler = written["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        handler["command"] = "python3 " + handler["command"].split(" ", 1)[1]
        self.assertEqual(configs[0], written)

    def test_the_matrix_says_what_was_and_was_not_verified(self) -> None:
        for phrase in ("wrapper tested against the documented payload shapes; not run inside "
                       "the real host", "https://learn.chatgpt.com/docs/hooks",
                       "https://cursor.com/docs/hooks", "2026-10-12", "/hooks",
                       "positive control"):
            self.assertIn(phrase, self.text)
        rows = [ln for ln in self.text.splitlines() if ln.startswith("| ")]
        for host in ("Claude Code", "Codex", "Cursor", "Any host without hooks"):
            self.assertTrue(any(ln.startswith(f"| {host}") for ln in rows), host)


if __name__ == "__main__":
    unittest.main()
