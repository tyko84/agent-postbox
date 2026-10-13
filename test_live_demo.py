#!/usr/bin/env python3
"""Offline tests of examples/two-agents/live_demo.py, the kit for a recording with
REAL Claude Code sessions. No model is called and no account is needed.

Checked here: `setup` builds the project with the real install.py and prints the
terminal commands and prompts; `check` judges ARTIFACTS and fails the beat whose
artifact is missing (each case removes one artifact, and a complete set passes, so
the failures are not a broken fixture); `rehearse` exits 2 and says SKIP when
`claude` is not on PATH or not signed in; a session beyond --max-calls or past the
cost ceiling is refused, never started; --max-cost cannot exceed USD 2.00; the
sessions are started with the safety flags and never with
--dangerously-skip-permissions. A stand-in `claude` records every invocation.
Stdlib only.
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
from typing import Any

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "examples" / "two-agents" / "live_demo.py"
TIMEOUT = 180

FAKE = """\
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    out = not os.environ.get("FAKE_LOGGED_OUT")
    print(json.dumps({"loggedIn": out}))
    sys.exit(0 if out else 1)
elif "-p" in args:
    with open(os.environ["FAKE_LOG"], "a") as fh:
        fh.write(json.dumps(args) + "\\n")
    sys.stdin.read()
    print(json.dumps({"type": "result", "result": "NONE", "is_error": False,
                      "total_cost_usd": float(os.environ.get("FAKE_COST", "0.01"))}))
else:
    sys.exit(3)
"""


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("live_demo", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def run(*args: str, path: str | None = None,
        **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    if path is not None:
        base["PATH"] = path
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                          text=True, env={**base, **env}, timeout=TIMEOUT)


class Fixture(unittest.TestCase):
    """A demo directory from the real `setup`, plus helpers to write artifacts."""

    def setUp(self) -> None:
        self.mod = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.demo = Path(self.tmp.name) / "demo"
        cp = run("setup", "--dir", str(self.demo))
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.setup_out = cp.stdout
        self.state = json.loads((self.demo / "state.json").read_text())
        self.project = Path(self.state["project"])
        self.live = self.state["live_base"]
        self.empty = Path(self.tmp.name) / "no-transcripts"
        self.empty.mkdir()

    def send(self, *argv: str) -> str:
        cp = subprocess.run([sys.executable, "-I", str(self.project / self.mod.TOOL), "send",
                             *argv], cwd=self.project, capture_output=True, text=True,
                            timeout=60, env={k: v for k, v in os.environ.items()
                                             if not k.startswith("AGENT_MAIL")})
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        for line in cp.stdout.splitlines():
            if line.startswith("id: "):
                return line[4:].strip()
        raise AssertionError(cp.stdout)

    def artifacts(self, *, ask_url: bool = True, answer: bool = True, hook: bool = True,
                  code: bool = True) -> None:
        subject = f"live base is {self.live}" if ask_url else "the payments base moved"
        ask_id = self.send("--type", "ASK", "--from", "alpha", "--to", "beta",
                           "--subject", subject, "--body", "x")
        if answer:
            self.send("--type", "ANSWER", "--from", "beta", "--to", "alpha", "--reply-to",
                      ask_id, "--subject", "re", "--body", "ok", "--result", "DONE")
        else:                  # a beta message so that beat 1 is not what goes missing
            self.send("--type", "NOTICE", "--from", "beta", "--to", "alpha",
                      "--subject", "hello", "--body", "x")
        if hook:
            (self.demo / "logs" / "beta.hook.txt").write_text(
                f"Agent mail - 1 live message(s) addressed to 'beta'.\n  id: {ask_id}\n")
        if code:
            (self.project / "client.py").write_text(
                f'BASE = "{self.live}"\n\n\ndef refund_url(charge_id):\n'
                f'    return f"{{BASE}}/charges/{{charge_id}}/refunds"\n')

    def failed(self) -> set[int]:
        return {b.n for b in self.mod.check(self.demo, self.empty) if not b.ok}


class Setup(Fixture):
    def test_setup_prints_commands_prompts_and_builds_a_real_project(self) -> None:
        out = self.setup_out
        for text in (f"cd {self.project}", "export AGENT_MAIL_IDENTITY=alpha",
                     "export AGENT_MAIL_IDENTITY=beta", "export PAYMENTS_REGISTRY=",
                     "claude --model haiku", self.mod.PROMPT_A, self.mod.PROMPT_B1,
                     self.mod.PROMPT_B2, "EXPECTED ON SCREEN", "NARRATION", self.live):
            self.assertIn(text, out)
        settings = json.loads((self.project / ".claude" / "settings.json").read_text())
        self.assertIn("agent_mail_check.py", json.dumps(settings["hooks"]["UserPromptSubmit"]))
        self.assertTrue((self.project / self.mod.TOOL).is_file())
        client = (self.project / "client.py").read_text()
        self.assertIn("/v1/ledger", client)
        self.assertNotIn(self.state["token"], client)
        registry = Path(self.state["registry"])
        self.assertFalse(str(registry).startswith(str(self.project)))   # beta cannot find it
        self.assertIn(self.live, registry.read_text())

    def test_discover_needs_the_registry_variable(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PAYMENTS_REGISTRY"}
        no = subprocess.run([sys.executable, "-I", "discover.py"], cwd=self.project,
                            capture_output=True, text=True, env=env)
        self.assertNotEqual(no.returncode, 0)
        yes = subprocess.run([sys.executable, "-I", "discover.py"], cwd=self.project,
                             capture_output=True, text=True,
                             env={**env, "PAYMENTS_REGISTRY": self.state["registry"]})
        self.assertIn(self.live, yes.stdout)

    def test_setup_refuses_a_directory_that_is_not_its_own(self) -> None:
        other = Path(self.tmp.name) / "precious"
        other.mkdir()
        (other / "file").write_text("x")
        cp = run("setup", "--dir", str(other))
        self.assertEqual(cp.returncode, 2, cp.stdout)
        self.assertTrue((other / "file").exists())

    def test_the_prompts_do_not_carry_the_secret(self) -> None:
        for prompt in (self.mod.PROMPT_A, self.mod.PROMPT_B1, self.mod.PROMPT_B2):
            self.assertNotIn(self.state["token"], prompt)


class Check(Fixture):
    def test_nothing_done_fails_every_beat_and_exits_1(self) -> None:
        self.assertEqual(self.failed(), set(range(1, 7)))
        cp = run("check", "--dir", str(self.demo), "--transcripts", str(self.empty))
        self.assertEqual(cp.returncode, 1, cp.stdout)
        self.assertEqual(cp.stdout.count("FAIL"), 6)

    def test_complete_artifacts_pass_every_beat(self) -> None:
        self.artifacts()
        self.assertEqual(self.failed(), set())
        cp = run("check", "--dir", str(self.demo), "--transcripts", str(self.empty))
        self.assertEqual(cp.returncode, 0, cp.stdout)
        self.assertIn("all 6 beats happened", cp.stdout)

    def test_each_missing_artifact_fails_exactly_its_beat(self) -> None:
        cases: dict[str, tuple[dict[str, Any], set[int]]] = {
            "url missing from the message": ({"ask_url": False}, {2}),
            "no ANSWER": ({"answer": False}, {5}),
            "no hook output": ({"hook": False}, {4}),
            "client.py untouched": ({"code": False}, {6}),
        }
        for label, (kw, beats) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.artifacts(**kw)
                self.assertEqual(self.failed(), beats)

    def test_the_old_url_does_not_count_as_behaviour(self) -> None:
        self.artifacts(code=False)
        (self.project / "client.py").write_text(
            'BASE = "https://payments.internal.example/v1/ledger"\n\n\ndef refund_url(c):\n'
            '    return f"{BASE}/charges/{c}/refunds"\n')
        self.assertEqual(self.failed(), {6})

    def test_a_transcript_naming_the_ask_counts_as_pickup_evidence(self) -> None:
        self.artifacts(hook=False)
        self.assertEqual(self.failed(), {4})
        ask_id = next(r["id"] for r in self.mod.mailbox(self.state) if r["type"] == "ASK")
        (self.empty / "s.jsonl").write_text(
            json.dumps({"hook": f"Agent mail - 1 live message(s)\n id: {ask_id}"}) + "\n")
        self.assertEqual(self.failed(), set())

    def test_check_without_setup_exits_2(self) -> None:
        cp = run("check", "--dir", str(Path(self.tmp.name) / "nothing"))
        self.assertEqual(cp.returncode, 2, cp.stdout)


class Rehearse(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.log = self.dir / "calls.log"
        self.empty = self.dir / "empty"
        self.empty.mkdir()
        fake = self.bin / "claude"
        fake.write_text("#!" + sys.executable + "\n" + FAKE)
        fake.chmod(0o755)

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def rehearse(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return run("rehearse", "--dir", str(self.dir / "demo"), *args,
                   path=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                   FAKE_LOG=str(self.log), **env)

    def test_claude_absent_is_a_skip_with_exit_2_and_nothing_made(self) -> None:
        cp = run("rehearse", "--dir", str(self.dir / "demo"), path=str(self.empty))
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertIn("SKIP", cp.stdout)
        self.assertIn("not on PATH", cp.stdout)
        self.assertNotIn("OK", cp.stdout)
        self.assertFalse((self.dir / "demo").exists())

    def test_claude_signed_out_is_a_skip(self) -> None:
        cp = self.rehearse(FAKE_LOGGED_OUT="1")
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertIn("SKIP", cp.stdout)
        self.assertEqual(self.calls(), [])

    def test_a_model_that_does_nothing_fails_every_beat_never_fakes(self) -> None:
        cp = self.rehearse()
        self.assertEqual(cp.returncode, 1, cp.stdout + cp.stderr)
        self.assertEqual(cp.stdout.count("FAIL"), 6, cp.stdout)
        self.assertEqual(len(self.calls()), 2)

    def test_sessions_use_the_safety_flags(self) -> None:
        self.rehearse()
        self.assertEqual(len(self.calls()), 2)
        for argv in self.calls():
            self.assertNotIn("--dangerously-skip-permissions", argv)
            for flag in ("--strict-mcp-config", "--no-session-persistence", "--max-budget-usd"):
                self.assertIn(flag, argv)
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
            self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
            self.assertEqual(argv[argv.index("--model") + 1], "haiku")
            self.assertEqual(argv[argv.index("--effort") + 1], "low")

    def test_max_calls_below_two_refuses_before_any_session(self) -> None:
        cp = self.rehearse("--max-calls", "1")
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertIn("refusing", cp.stdout)
        self.assertEqual(self.calls(), [])

    def test_the_second_session_is_refused_once_the_cost_is_spent(self) -> None:
        cp = self.rehearse("--max-cost", "1.5", FAKE_COST="1.495")
        self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
        self.assertIn("refusing to start the beta session", cp.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_max_cost_above_the_hard_ceiling_is_rejected(self) -> None:
        cp = self.rehearse("--max-cost", "2.01")
        self.assertEqual(cp.returncode, 2, cp.stderr)
        self.assertEqual(self.calls(), [])

    def test_spend_counts_calls_and_cost(self) -> None:
        mod = load()
        s = mod.Spend(max_calls=2, max_cost=1.5)
        self.assertLessEqual(s.admit("alpha"), mod.PER_SESSION_CAP)
        s.record("alpha", 1.2)
        self.assertEqual(s.admit("beta"), 0.3)
        with self.assertRaises(mod.Usage):
            s.admit("third")                       # --max-calls
        t = mod.Spend(max_calls=9, max_cost=1.5)
        t.admit("a")
        t.record("a", 1.5)
        with self.assertRaises(mod.Usage):
            t.admit("b")                           # --max-cost


if __name__ == "__main__":
    unittest.main()
