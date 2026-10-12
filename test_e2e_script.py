#!/usr/bin/env python3
"""Offline tests of scripts/e2e_claude.py, the real-session test of the Claude
Code adapter. No model is called and no account is needed: the real run spends
usage and is not part of CI (docs/e2e-real-sessions.md).

Checked here: argument parsing; the call budget (a session beyond --max-calls
is refused, never started, and reported as SKIP); exit 2 with every check
SKIPped when `claude` is not on PATH or not signed in; the JSON report's
fields. A stand-in `claude` executable records every invocation so the budget
test can count what would have reached a model. Skipped where scripts/ is not
present (an unpacked sdist ships the tests, not the scripts). Stdlib only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "scripts" / "e2e_claude.py"
TIMEOUT = 120
KEYS = {"schema", "date", "host", "host_version", "model", "models_reported", "tests",
        "max_calls", "calls", "skipped_reason", "checks", "tallies", "notes", "exit"}
CHECK_KEYS = {"test", "label", "kind", "status", "detail"}

# A stand-in for the CLI: `--version`, `auth status` (logged in unless FAKE_LOGGED_OUT),
# and a print-mode session that logs its argv and answers NONE.
FAKE = """\
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("0.0.0 (Claude Code)")
elif args[:2] == ["auth", "status"]:
    out = not os.environ.get("FAKE_LOGGED_OUT")
    print(json.dumps({"loggedIn": out}))
    sys.exit(0 if out else 1)
elif "-p" in args:
    with open(os.environ["FAKE_LOG"], "a") as fh:
        fh.write(json.dumps(args) + "\\n")
    sys.stdin.read()
    print(json.dumps({"type": "result", "result": "NONE", "is_error": False}))
else:
    sys.exit(3)
"""


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_claude", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["e2e_claude"] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(SCRIPT.is_file(), "scripts/e2e_claude.py is not here (an unpacked sdist?)")
class E2EScript(unittest.TestCase):
    mod: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="test-e2e-")
        self.dir = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, *args: str, path: str, extra: dict[str, str] | None = None
                   ) -> tuple[int, str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL_")}
        env["PATH"] = path
        env.update(extra or {})
        cp = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                            text=True, timeout=TIMEOUT, env=env)
        return cp.returncode, cp.stdout, cp.stderr

    def fake_claude(self) -> Path:
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        exe = bin_dir / "claude"
        exe.write_text(f"#!{sys.executable}\n{FAKE}", encoding="utf-8")
        exe.chmod(0o755)
        return bin_dir

    def check_schema(self, data: dict) -> None:
        self.assertEqual(set(data), KEYS)
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["host"], "claude-code")
        for c in data["checks"]:
            self.assertEqual(set(c), CHECK_KEYS)
            self.assertIn(c["status"], {"OK", "FAIL", "SKIP", "INFO"})
            self.assertIn(c["kind"], {"delivery", "behaviour"})

    # -- argument parsing ---------------------------------------------------------
    def test_defaults(self) -> None:
        a = self.mod.parse_args([])
        self.assertEqual(a.tests, list(self.mod.TESTS))
        self.assertEqual((a.model, a.max_calls, a.keep), ("haiku", 12, False))
        self.assertEqual(sum(self.mod.CALLS.values()), 21)

    def test_tests_are_normalised_and_kept_in_suite_order(self) -> None:
        self.assertEqual(self.mod.parse_args(["--tests", "t3, T1"]).tests, ["T1", "T3"])

    def test_bad_arguments_exit_2(self) -> None:
        for argv in (["--tests", "T9"], ["--tests", ","], ["--max-calls", "-1"],
                     ["--max-calls", "x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as cm:
                with open(os.devnull, "w") as null:
                    stderr, sys.stderr = sys.stderr, null
                    try:
                        self.mod.parse_args(argv)
                    finally:
                        sys.stderr = stderr
            self.assertEqual(cm.exception.code, 2)

    # -- the call budget ------------------------------------------------------------
    def test_budget_refuses_beyond_the_limit(self) -> None:
        b = self.mod.Budget(3)
        self.assertTrue(b.take())
        self.assertTrue(b.take(2))
        self.assertFalse(b.take())
        self.assertEqual(b.used, 3)
        zero = self.mod.Budget(0)
        self.assertFalse(zero.take())
        self.assertEqual(zero.used, 0)

    def test_a_session_beyond_max_calls_is_never_started(self) -> None:
        bin_dir = self.fake_claude()
        log = self.dir / "calls.log"
        report = self.dir / "r.json"
        rc, out, err = self.run_script(
            "--tests", "T1", "--max-calls", "1", "--json", str(report),
            "--workdir", str(self.dir / "work"),
            path=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            extra={"FAKE_LOG": str(log)})
        self.assertEqual(rc, 1, out + err)   # the stand-in answers NONE: positive fails
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1, calls)
        argv = json.loads(calls[0])
        for flag in ("--tools", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertRegex(out, r"(?m)^FAIL +T1 positive ")
        self.assertRegex(out, r"(?m)^SKIP +T1 no-identity +call budget: 1/1 used")
        self.assertRegex(out, r"(?m)^SKIP +T1 no-adapter +call budget")
        data = json.loads(report.read_text(encoding="utf-8"))
        self.check_schema(data)
        self.assertEqual((data["calls"], data["max_calls"], data["exit"]), (1, 1, 1))
        self.assertFalse((self.dir / "work").exists(), "the work dir is removed without --keep")

    # -- SKIP when the host is missing or signed out --------------------------------
    def test_no_claude_on_path_skips_everything_and_exits_2(self) -> None:
        report = self.dir / "r.json"
        rc, out, _ = self.run_script("--json", str(report), path="")
        self.assertEqual(rc, 2)
        lines = out.splitlines()
        self.assertEqual(len(lines), len(self.mod.TESTS))
        for line in lines:
            self.assertRegex(line, r"^SKIP +T\d all +`claude` is not on PATH$")
        data = json.loads(report.read_text(encoding="utf-8"))
        self.check_schema(data)
        self.assertEqual((data["calls"], data["exit"]), (0, 2))
        self.assertEqual(data["skipped_reason"], "`claude` is not on PATH")
        self.assertEqual({c["status"] for c in data["checks"]}, {"SKIP"})

    def test_signed_out_skips_everything_and_exits_2(self) -> None:
        bin_dir = self.fake_claude()
        log = self.dir / "calls.log"
        rc, out, _ = self.run_script("--tests", "T2", path=str(bin_dir),
                                     extra={"FAKE_LOGGED_OUT": "1", "FAKE_LOG": str(log)})
        self.assertEqual(rc, 2)
        self.assertRegex(out, r"(?m)^SKIP +T2 all +`claude` is not signed in")
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
