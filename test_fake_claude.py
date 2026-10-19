#!/usr/bin/env python3
"""A stand-in `claude` that fails CLOSED, and the tests that prove it. Stdlib only.

Several suites drive scripts that start `claude` sessions (scripts/e2e_claude.py,
examples/two-agents/live_demo.py). The suites put a stand-in `claude` first on PATH. If that
stand-in cannot be launched (a shebang script on Windows, a lost executable bit, a full disk),
a PATH lookup falls through to the next `claude` -- the real, billable CLI -- and an automated
test starts a paid model session. That must be impossible, not merely unlikely.

This module is both the helper those suites import and its own regression test:

* `fake_claude_path(bin_dir, body)` writes the stand-in, then returns a PATH that
    - contains `bin_dir` first,
    - contains NO directory that holds any other `claude`, `claude.exe`, `claude.cmd`,
      `claude.bat` or `claude.ps1` (the real CLI is not reachable by name at all), and
    - has been checked: the `claude` that PATH resolves to is the stand-in, and running it
      answers the marker `POSTBOX-FAKE-CLAUDE`.
  Otherwise it raises `FakeClaudeUnavailable`, and the suite fails (or skips) instead of
  running.
* `python test_fake_claude.py` proves that with a canary standing in for the real CLI: a
  stand-in that cannot launch never lets the canary run.

No test here (or anywhere) may build PATH from `os.environ["PATH"]` for a session-starting
script; `test_suites_do_not_extend_the_inherited_path` enforces that.
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

HERE = Path(__file__).resolve().parent
MARKER = "POSTBOX-FAKE-CLAUDE"
_NAMES = ("claude", "claude.exe", "claude.cmd", "claude.bat", "claude.ps1")
# What a launchable stand-in answers before it does anything else, whatever the arguments.
_PREAMBLE = (f"import sys\nif sys.argv[1:] == ['--postbox-fake-claude']:\n"
             f"    print({MARKER!r})\n    sys.exit(0)\n")


class FakeClaudeUnavailable(RuntimeError):
    """The stand-in cannot be launched here; nothing was run."""


def dirs_with_a_claude(path: str) -> list[str]:
    """Directories on `path` that hold anything a lookup of `claude` could resolve to."""
    found = []
    for d in path.split(os.pathsep):
        if d and any(os.path.lexists(os.path.join(d, n)) for n in _NAMES):
            found.append(d)
    return found


def _write_stub(bin_dir: Path, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    code = _PREAMBLE + body
    if os.name == "nt":
        # Windows cannot launch a shebang script: a .cmd wrapper next to the Python file can.
        (bin_dir / "claude_stub.py").write_text(code, encoding="utf-8")
        (bin_dir / "claude.cmd").write_text(
            f'@"{sys.executable}" "%~dp0claude_stub.py" %*\r\n', encoding="utf-8")
    else:
        exe = bin_dir / "claude"
        exe.write_text(f"#!{sys.executable}\n{code}", encoding="utf-8")
        exe.chmod(0o755)


def verify_stand_in(bin_dir: Path, inherited: str | None = None) -> str:
    """Return a PATH on which `claude` is the stand-in in `bin_dir` and nothing else, or raise
    FakeClaudeUnavailable. Writes nothing."""
    inherited = os.environ.get("PATH", "") if inherited is None else inherited
    keep = [d for d in inherited.split(os.pathsep)
            if d and os.path.normcase(os.path.abspath(d)) != os.path.normcase(str(bin_dir))]
    keep = [d for d in keep if d not in dirs_with_a_claude(os.pathsep.join(keep))]
    path = os.pathsep.join([str(bin_dir), *keep])
    resolved = shutil.which("claude", path=path)
    if resolved is None or Path(resolved).resolve().parent != Path(bin_dir).resolve():
        raise FakeClaudeUnavailable(f"`claude` resolves to {resolved!r}, not the stand-in in {bin_dir}")
    try:
        cp = subprocess.run([resolved, "--postbox-fake-claude"], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "PATH": path})
    except (OSError, subprocess.SubprocessError) as exc:
        raise FakeClaudeUnavailable(f"the stand-in did not launch: {exc}") from exc
    if cp.returncode != 0 or cp.stdout.strip() != MARKER:
        raise FakeClaudeUnavailable(f"the stand-in answered rc={cp.returncode} {cp.stdout[:80]!r}")
    return path


def fake_claude_path(bin_dir: Path, body: str, inherited: str | None = None) -> str:
    """Install the stand-in in `bin_dir` and return its verified PATH (see verify_stand_in)."""
    _write_stub(bin_dir, body)
    return verify_stand_in(bin_dir, inherited)


# ---------------------------------------------------------------------------------------- tests

# What a "real claude" looks like to the tests: it records that it ran. If this ever runs, the
# harness failed. (Real sessions are never started here.)
def _write_canary(directory: Path, flag: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    code = f"open({str(flag)!r}, 'w').close()\nprint('REAL-CLAUDE-RAN')\n"
    if os.name == "nt":
        (directory / "claude_canary.py").write_text(code, encoding="utf-8")
        (directory / "claude.cmd").write_text(
            f'@"{sys.executable}" "%~dp0claude_canary.py" %*\r\n', encoding="utf-8")
    else:
        exe = directory / "claude"
        exe.write_text(f"#!{sys.executable}\n{code}", encoding="utf-8")
        exe.chmod(0o755)


class FailClosed(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="test-fake-claude-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.flag = self.dir / "REAL_CLAUDE_RAN"
        self.real = self.dir / "real-bin"
        _write_canary(self.real, self.flag)
        self.inherited = os.pathsep.join([str(self.real), os.environ.get("PATH", "")])

    def test_control_the_canary_is_what_a_plain_path_lookup_finds(self) -> None:
        # Positive control: without the harness the "real" claude IS reachable, so the tests
        # below prove something.
        found = shutil.which("claude", path=self.inherited)
        self.assertIsNotNone(found)
        self.assertEqual(Path(found).resolve().parent, self.real)  # type: ignore[arg-type]
        subprocess.run([found, "x"], capture_output=True, text=True, timeout=30)  # type: ignore[list-item]
        self.assertTrue(self.flag.exists(), "the canary must run when nothing stops it")
        self.flag.unlink()

    def test_a_working_stand_in_is_first_and_the_real_one_is_unreachable(self) -> None:
        path = fake_claude_path(self.dir / "bin", "print('stub')\n", inherited=self.inherited)
        self.assertEqual(Path(shutil.which("claude", path=path)).resolve().parent,  # type: ignore[arg-type]
                         self.dir / "bin")
        self.assertNotIn(str(self.real), path.split(os.pathsep))
        self.assertEqual(dirs_with_a_claude(path), [str(self.dir / "bin")])
        self.assertFalse(self.flag.exists())

    def test_a_stand_in_that_cannot_launch_is_refused_and_the_real_one_never_runs(self) -> None:
        # Break the stand-in the way each platform does: unlaunchable bytes where it should be.
        bin_dir = self.dir / "bin"
        _write_stub(bin_dir, "print('stub')\n")
        for f in bin_dir.iterdir():
            f.write_bytes(b"\x00\x01not a program")
            if os.name == "posix":
                f.chmod(0o644)       # no executable bit: exec fails, a lookup skips it
        with self.assertRaises(FakeClaudeUnavailable):
            verify_stand_in(bin_dir, inherited=os.pathsep.join([str(bin_dir), self.inherited]))
        self.assertFalse(self.flag.exists(), "the real claude ran: the harness is not fail-closed")

    def test_no_stand_in_at_all_leaves_no_claude_to_find(self) -> None:
        keep = [d for d in self.inherited.split(os.pathsep) if d not in dirs_with_a_claude(self.inherited)]
        self.assertIsNone(shutil.which("claude", path=os.pathsep.join(keep)))

    def test_the_demo_script_with_a_dead_stand_in_skips_and_runs_nothing(self) -> None:
        # End to end through the real script: a PATH with only a broken stand-in dir and no
        # other claude gives the script's own "not on PATH" SKIP (exit 2); the canary never runs.
        script = HERE / "examples" / "two-agents" / "live_demo.py"
        if not script.is_file():
            self.skipTest("examples/two-agents/live_demo.py is not here (an unpacked sdist?)")
        dead = self.dir / "dead"
        dead.mkdir()
        keep = [d for d in self.inherited.split(os.pathsep) if d not in dirs_with_a_claude(self.inherited)]
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env["PATH"] = os.pathsep.join([str(dead), *keep])
        cp = subprocess.run([sys.executable, "-I", str(script), "rehearse", "--dir",
                             str(self.dir / "demo")], capture_output=True, text=True, timeout=120,
                            env=env)
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertIn("SKIP", cp.stdout)
        self.assertFalse(self.flag.exists())

    def test_suites_do_not_extend_the_inherited_path(self) -> None:
        # A suite that starts a claude-launching script must take its PATH from
        # fake_claude_path, never from os.environ["PATH"] (which can hold the real CLI).
        pattern = re.compile(r"""os\.environ\.get\(\s*['"]PATH['"]|os\.environ\[\s*['"]PATH['"]""")
        offenders = []
        for name in ("test_e2e_script.py", "test_live_demo.py"):
            text = (HERE / name).read_text(encoding="utf-8")
            if pattern.search(text):
                offenders.append(name)
        self.assertEqual(offenders, [], "build PATH with test_fake_claude.fake_claude_path")


if __name__ == "__main__":
    unittest.main()
