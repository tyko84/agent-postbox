#!/usr/bin/env python3
"""`doctor --probe-hook` and the doctor crash fix (PROTOCOL.md section 50). Stdlib only.

The fault matrix: for each way the pickup hook can break, the hook itself stays silent with
exit 0 (that is its contract), `doctor` without the flag is unchanged, and `doctor --probe-hook`
says what is wrong, in a fixed vocabulary, without a message, a mailbox path or a temp path.
Every project here is a temporary directory; the checkout is read, never written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # `-I` drops the script dir
import agent_mail  # noqa: E402

PY = shutil.which("python3") and "python3" or shutil.which("python") and "python" or None
REAL_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
REAL_SUBJECT = "REAL-MAILBOX-SECRET-SUBJECT"
SCRIPT_REL = "tools/agent-postbox/hooks/agent_mail_check.py"


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    env.update(extra)
    return env


def command(host: str = "claude-code", py: str | None = None) -> str:
    py = py or PY or "python3"
    if host == "codex":
        return f'{py} "$(git rev-parse --show-toplevel)/{SCRIPT_REL}" || true'
    return f'{py} "$CLAUDE_PROJECT_DIR/{SCRIPT_REL}" || true'


class Project:
    """A project holding an installed hook, a registered hook command, and a real mailbox."""

    def __init__(self, tmp: Path, host: str = "claude-code", cmd: str | None = None) -> None:
        self.root = tmp / "proj"
        (self.root / ".git").mkdir(parents=True)
        tools = self.root / "tools" / "agent-postbox"
        (tools / "hooks").mkdir(parents=True)
        shutil.copy(HERE / "agent_mail.py", tools / "agent_mail.py")
        shutil.copy(HERE / "hooks" / "agent_mail_check.py", tools / "hooks" / "agent_mail_check.py")
        self.cli = tools / "agent_mail.py"
        self.script = tools / "hooks" / "agent_mail_check.py"
        folder = ".claude" if host == "claude-code" else ".codex"
        name = "settings.json" if host == "claude-code" else "hooks.json"
        (self.root / folder).mkdir()
        self.settings = self.root / folder / name
        self.register(cmd or command(host))
        self.box = self.root / "mailbox"
        self.box.mkdir()
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))
        (self.box / f"{REAL_ULID}-real.md").write_text(
            f"---\nid: {REAL_ULID}\ntype: NOTICE\nfrom: alice\nto: bob\ndate: {stamp}\n"
            f"subject: {REAL_SUBJECT}\n---\n\nREAL-BODY\n", encoding="utf-8")

    def register(self, cmd: str) -> None:
        self.settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": cmd}]}]}}), encoding="utf-8")

    def edit_cli(self, old: str, new: str) -> None:
        text = self.cli.read_text(encoding="utf-8")
        assert old in text, old
        self.cli.write_text(text.replace(old, new, 1), encoding="utf-8")

    def snapshot(self) -> dict[str, tuple[int, bytes]]:
        return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(self.box.iterdir())}

    def hook(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(self.script)], capture_output=True, text=True,
                              env=clean_env(AGENT_MAIL_DIR=str(self.box), AGENT_MAIL_IDENTITY="bob"),
                              timeout=60)

    def doctor(self, *flags: str, tool: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(tool or HERE / "agent_mail.py"), "doctor", *flags],
                              capture_output=True, text=True, cwd=self.root, timeout=120,
                              env=clean_env(AGENT_MAIL_DIR=str(self.box), AGENT_MAIL_IDENTITY="bob"))


def probe_text(r: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(line for line in r.stdout.splitlines() if line.startswith("hook_probe:") or line.startswith("  - "))


@unittest.skipIf(PY is None, "no python3 or python on PATH to register as the hook interpreter")
class HookProbe(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def assert_clean(self, r: subprocess.CompletedProcess[str], p: Project) -> None:
        """Nothing a hostile reader could use: no message text, no mailbox or temp path."""
        text = r.stdout + r.stderr
        probe = probe_text(r)
        self.assertNotIn("REAL-BODY", text)
        self.assertNotIn("agent-mail-probe-", text)
        self.assertNotIn(str(self.tmp), probe, probe)
        self.assertNotIn("Traceback", r.stderr)

    # ---- the healthy case, and what does not change without the flag

    def test_healthy_probe_passes_and_says_it_is_not_delivery(self) -> None:
        p = Project(self.tmp)
        before = p.snapshot()
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: the hook rendered the synthetic message", r.stdout)
        self.assertIn("does not show that Claude Code or Codex runs it", r.stdout)
        self.assertIn(f"running `{PY} {SCRIPT_REL}` once, against a synthetic mailbox", r.stdout)
        self.assertEqual(p.snapshot(), before, "the real mailbox changed")
        self.assertEqual(sorted(os.listdir(p.box)), [f"{REAL_ULID}-real.md"])
        self.assert_clean(r, p)

    def test_without_the_flag_nothing_is_probed(self) -> None:
        p = Project(self.tmp)
        r = p.doctor()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("hook_probe", r.stdout)
        self.assertIn("pickup_hook: registered", r.stdout)

    def test_codex_shape_is_probed_too(self) -> None:
        p = Project(self.tmp, host="codex")
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hook_probe: codex: running", r.stdout)
        self.assertIn("OK: the hook rendered", r.stdout)

    def test_no_hook_registered_is_not_a_failure(self) -> None:
        p = Project(self.tmp)
        p.settings.unlink()
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("nothing to probe: no hook is registered", r.stdout)

    # ---- the fault matrix: each fault is silent from the host's side, named by the probe

    def fault(self, name: str, mutate: str, expect: str) -> None:
        p = Project(self.tmp / name)
        getattr(self, "_" + mutate)(p)
        h = p.hook()
        self.assertEqual((h.returncode, h.stdout), (0, ""),
                         f"{name}: the hook must stay silent with exit 0 ({h.stdout!r} {h.stderr[-120:]!r})")
        quiet = p.doctor()
        self.assertEqual(quiet.returncode, 0, f"{name}: doctor without the flag is unchanged")
        before = p.snapshot()
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 2, f"{name}: {r.stdout}")
        self.assertIn("FAIL: pickup hook probe failed", r.stdout, name)
        self.assertIn(expect, r.stdout, f"{name}: {r.stdout}")
        self.assertEqual(p.snapshot(), before, f"{name}: the real mailbox changed")
        self.assert_clean(r, p)

    def _missing_cli(self, p: Project) -> None:
        # The hook only needs agent_mail.py; the doctor under test is the checkout's own.
        p.cli.unlink()

    def _truncated(self, p: Project) -> None:
        p.cli.write_bytes(p.cli.read_bytes()[:50000])

    def _api_mismatch(self, p: Project) -> None:
        p.edit_cli("def open_asks(", "def open_asks_renamed(")

    def _runtime_error(self, p: Project) -> None:
        p.edit_cli("def load_messages(", "def load_messages(*a, **k):\n    raise RuntimeError('boom')\n\n\n"
                                         "def _orig_load_messages(")

    def _script_missing(self, p: Project) -> None:
        p.script.unlink()

    def test_agent_mail_missing_beside_the_hook(self) -> None:
        self.fault("missing", "missing_cli", "agent_mail.py is not beside the hook")

    def test_agent_mail_truncated_by_an_interrupted_copy(self) -> None:
        self.fault("truncated", "truncated", "agent_mail.py cannot be imported: SyntaxError")

    def test_hook_and_cli_are_different_versions(self) -> None:
        self.fault("api", "api_mismatch", "lacks what the hook uses (open_asks)")

    def test_error_inside_pickup_is_swallowed_by_design(self) -> None:
        self.fault("runtime", "runtime_error", "an error inside pickup was swallowed by design")

    def test_script_missing(self) -> None:
        p = Project(self.tmp)
        self._script_missing(p)
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("FAIL: the script is MISSING at " + SCRIPT_REL, r.stdout)

    def test_interpreter_not_on_path(self) -> None:
        p = Project(self.tmp, cmd=command(py="python"))
        env = clean_env(AGENT_MAIL_DIR=str(p.box), AGENT_MAIL_IDENTITY="bob", PATH=str(self.tmp))
        r = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "doctor", "--probe-hook"],
                           capture_output=True, text=True, cwd=p.root, env=env, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("is not on PATH, so the registered command cannot start", r.stdout)

    # ---- what is never run, and the time bound

    def test_hand_written_command_is_never_run(self) -> None:
        p = Project(self.tmp)
        marker = self.tmp / "ran"
        p.register(f'{PY} -c "open(r\'{marker}\', \'w\')" agent_mail_check.py && echo x')
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("not probed: the command in .claude/settings.json is not the shape install.py writes",
                      r.stdout)
        self.assertFalse(marker.exists(), "a hand-written command was executed")

    def test_script_path_that_leaves_the_project_is_not_run(self) -> None:
        p = Project(self.tmp)
        p.register(f'{PY} "$CLAUDE_PROJECT_DIR/../x/hooks/agent_mail_check.py" || true')
        outside = self.tmp / "x" / "hooks"
        outside.mkdir(parents=True)
        marker = self.tmp / "ran-outside"
        (outside / "agent_mail_check.py").write_text(f"open(r'{marker}', 'w')\n", encoding="utf-8")
        r = p.doctor("--probe-hook")
        self.assertIn("not probed: the script path leaves the project", r.stdout)
        self.assertFalse(marker.exists())

    def test_unreadable_settings_are_not_probed(self) -> None:
        p = Project(self.tmp)
        p.settings.write_text("{not json", encoding="utf-8")
        r = p.doctor("--probe-hook")
        self.assertIn("not probed: .claude/settings.json is not valid JSON", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_hook_that_hangs_is_killed_and_reported(self) -> None:
        p = Project(self.tmp)
        p.script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        old = agent_mail.PROBE_TIMEOUT_SECONDS
        agent_mail.PROBE_TIMEOUT_SECONDS = 1
        self.addCleanup(setattr, agent_mail, "PROBE_TIMEOUT_SECONDS", old)
        start = time.monotonic()
        lines, failed = agent_mail.probe_hook_lines(p.root)
        self.assertLess(time.monotonic() - start, 20)
        self.assertTrue(failed)
        self.assertTrue(any("FAIL: TIMEOUT: the hook did not finish in 1 s and was killed" in x
                            for x in lines), lines)

    def test_a_hook_that_exits_nonzero_is_reported(self) -> None:
        p = Project(self.tmp)
        p.script.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
        r = p.doctor("--probe-hook")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("FAIL: the hook exited 7;", r.stdout)

    # ---- the doctor crash

    def test_a_runtime_error_inside_doctor_is_one_fail_line_not_a_traceback(self) -> None:
        p = Project(self.tmp)
        self._runtime_error(p)
        r = p.doctor(tool=p.cli)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("FAIL: doctor could not finish: RuntimeError at agent_mail.py:", r.stdout)
        self.assertIn("the report above is incomplete", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("boom", r.stdout + r.stderr, "the error text can carry a path or a message")

    def test_a_syntax_error_in_doctors_own_file_cannot_be_caught_and_is_not_hidden(self) -> None:
        p = Project(self.tmp)
        self._truncated(p)
        r = p.doctor(tool=p.cli)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SyntaxError", r.stderr, "the failure must stay visible")


if __name__ == "__main__":
    unittest.main()
