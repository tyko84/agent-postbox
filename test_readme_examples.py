#!/usr/bin/env python3
"""Docs are tested: README examples run, and section references resolve.

* Every fenced block tagged ```bash runnable in README.md is executed, in
  order, as ONE bash script (so exports persist) in a scratch mailbox, with an
  `agent-postbox` command on PATH that runs this checkout's agent_mail.py.
* Every "section N" / section-sign N reference in the markdown docs must match
  a real "## N." heading in PROTOCOL.md.
* The supported interpreters are stated once, as pyproject.toml's
  `Programming Language :: Python :: 3.X` classifiers. README's
  "Python 3.X-3.Y" wording and ci.yml's selftest matrix must agree with them.

Positive controls: a planted failing example, a planted bad reference and a
planted stale version range must be caught by the same instruments. Stdlib
only.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# A hook, `rebase --exec` or `bisect run` exports GIT_DIR and friends; inherited, they would
# point this script's temporary repositories at the real one. Test scripts ignore them.
for _v in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX", "GIT_NAMESPACE",
           "GIT_CEILING_DIRECTORIES"):
    os.environ.pop(_v, None)

ROOT = Path(__file__).resolve().parent


def _bash() -> str:
    """The `bash` the README's shell blocks run under. On Windows a bare `bash` is found in
    System32 first, where it is WSL's launcher: it fails ("no installed distributions") on a
    machine without a WSL distribution. Git for Windows' bash sits next to git.exe, so use that."""
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            here = Path(git).resolve().parent
            for cand in (here / "bash.exe", here.parent / "bin" / "bash.exe"):
                if cand.is_file():
                    return str(cand)
    return "bash"


BASH = _bash()
_FENCE = re.compile(r"^```bash runnable[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
_HEADING = re.compile(r"^## (\d+[a-z]?)\.", re.M)
_SECT = re.compile(
    r"(?:§|\b[Ss]ections?\s+)(\d+[a-z]?)"
    r"(?:\s*(?:-|–|and|/|,)\s*§?(\d+[a-z]?))?"
)
_CLASSIFIER = re.compile(r'^\s*"Programming Language :: Python :: (3\.\d+)"', re.M)
_RANGE = re.compile(r"\bPython (3\.\d+)-(3\.\d+)\b")
_MATRIX = re.compile(r"^\s*python:\s*\[(.*)\]\s*$", re.M)


def runnable_blocks(markdown: str) -> list[str]:
    return _FENCE.findall(markdown)


def run_blocks(blocks: list[str]) -> subprocess.CompletedProcess[str]:
    """Run blocks as one `bash -e` script in a scratch dir; stdout+stderr merged."""
    with tempfile.TemporaryDirectory() as tmp:
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        shim = bindir / "agent-postbox"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "agent_mail.py"}" "$@"\n'
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        work = Path(tmp) / "work"
        work.mkdir()
        script = "set -e\n" + "".join(
            f"echo '### block {i}'\n{b}\n" for i, b in enumerate(blocks, 1)
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        return subprocess.run(
            [BASH, "-c", script], cwd=work, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
        )


def protocol_sections() -> set[str]:
    return set(_HEADING.findall((ROOT / "PROTOCOL.md").read_text(encoding="utf-8")))


def bad_references(text: str, sections: set[str]) -> list[str]:
    """Section numbers cited in `text` that PROTOCOL.md does not have."""
    bad: list[str] = []
    for m in _SECT.finditer(text):
        first, second = m.group(1), m.group(2)
        cited = [first] + ([second] if second else [])
        # "10-28" style ranges: every integer in between must exist too
        if second and second.isdigit() and first.isdigit() and "-" in m.group(0).replace("–", "-"):
            cited = [str(n) for n in range(int(first), int(second) + 1)]
        bad += [c for c in cited if c not in sections]
    return bad


def doc_files() -> list[Path]:
    files = [p for p in ROOT.glob("*.md")] + list((ROOT / "docs").glob("*.md"))
    return sorted(files)


def supported_pythons() -> list[str]:
    """pyproject.toml's `Programming Language :: Python :: 3.X` classifiers, in file order.

    tomllib on 3.11+; on 3.10 the stdlib has no TOML parser, so a line regex."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ImportError:
        return _CLASSIFIER.findall(text)
    prefix = "Programming Language :: Python :: "
    found = [c[len(prefix):] for c in tomllib.loads(text)["project"]["classifiers"]
             if c.startswith(prefix)]
    return [v for v in found if re.fullmatch(r"3\.\d+", v)]


def range_statements(text: str) -> list[str]:
    """Every "Python 3.X-3.Y" in `text`, as written."""
    return [f"Python {lo}-{hi}" for lo, hi in _RANGE.findall(text)]


def ci_matrix_pythons() -> list[str]:
    """The `python: [...]` matrix of ci.yml, read as text (no YAML parser in the stdlib).

    Only the selftest job has one; test_publication.py pins that."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lines = _MATRIX.findall(text)
    if len(lines) != 1:
        raise AssertionError(f"ci.yml has {len(lines)} `python:` matrix lines, expected 1")
    return re.findall(r'"(3\.\d+)"', lines[0])


class ReadmeExamples(unittest.TestCase):
    def test_readme_runnable_examples_execute(self) -> None:
        blocks = runnable_blocks((ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(blocks), 5, "README lost its runnable examples")
        res = run_blocks(blocks)
        self.assertEqual(res.returncode, 0, f"README example failed:\n{res.stdout}")
        # Content that MUST appear (silence would prove nothing):
        self.assertIn("wrote ", res.stdout)
        self.assertIn("! ASK", res.stdout)          # the ASK was live for agent-b
        # live for `list` + `inbox` in block 2, gone from block 3's list after the ANSWER
        self.assertEqual(res.stdout.count("! ASK"), 2)
        self.assertIn("already held by agent-a", res.stdout)  # rival claim refused
        self.assertIn("store: OK", res.stdout)      # doctor ran

    def test_readme_upgrade_instructions_are_true(self) -> None:
        """README's "Upgrading" section says re-running install.py from a newer clone replaces
        the copied tool and keeps the mailbox, its messages and PARTICIPANTS.md."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"^### Upgrading\n(.*?)(?=^#{2,3} )", readme, re.S | re.M)
        self.assertIsNotNone(m, "README has no 'Upgrading' section under Install")
        assert m is not None
        section = m.group(1)
        for needle in ("pull", "install.py", "PARTICIPANTS.md", "selftest.py"):
            self.assertIn(needle, section)
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
            env["HOME"] = tmp

            def run(*argv: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run([sys.executable, *argv], cwd=proj, env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)

            subprocess.run(["git", "init", "-q", str(proj)], check=True, env=env)
            self.assertEqual(run(str(ROOT / "install.py"), ".").returncode, 0)
            tool = proj / "tools" / "agent-postbox" / "agent_mail.py"
            sent = run(str(tool), "send", "--type", "NOTICE", "--from", "agent-a",
                       "--to", "agent-b", "--subject", "kept", "--body", "survives an upgrade")
            self.assertEqual(sent.returncode, 0, sent.stdout)
            people = proj / "docs" / "agent-mail" / "PARTICIPANTS.md"
            people.write_text(people.read_text(encoding="utf-8") + "\n<!-- mine -->\n", encoding="utf-8")
            mine = people.read_text(encoding="utf-8")
            # an older tool: put a stale marker in the copy
            tool.write_text(tool.read_text(encoding="utf-8") + "\n# STALE-COPY\n", encoding="utf-8")
            up = run(str(ROOT / "install.py"), ".")
            self.assertEqual(up.returncode, 0, up.stdout)
            self.assertNotIn("STALE-COPY", tool.read_text(encoding="utf-8"))   # tool replaced
            self.assertEqual(people.read_text(encoding="utf-8"), mine)          # participants kept
            listed = run(str(tool), "list")
            self.assertIn("kept", listed.stdout)                                # message kept

    def test_first_doctor_exit_status_matches_the_walkthrough(self) -> None:
        """The first `doctor` on a fresh install exits 0 and says UNVERIFIED; no page may
        describe that first run as an exit 2."""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
            env["HOME"] = tmp
            subprocess.run(["git", "init", "-q", str(proj)], check=True, env=env)
            inst = subprocess.run([sys.executable, str(ROOT / "install.py"), "."], cwd=proj,
                                  env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(inst.returncode, 0, inst.stdout)
            doc = subprocess.run([sys.executable, "tools/agent-postbox/agent_mail.py", "doctor"],
                                 cwd=proj, env=env, text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
            self.assertEqual(doc.returncode, 0, doc.stdout)
            self.assertIn("canary: UNVERIFIED", doc.stdout)
        page = (ROOT / "docs" / "onboarding-first-exchange.md").read_text(encoding="utf-8")
        self.assertNotIn("`doctor` (exit 2), `canary`", page)

    def test_positive_control_failing_example_is_caught(self) -> None:
        self.assertNotEqual(run_blocks(["false"]).returncode, 0)
        bad = run_blocks([
            'export AGENT_MAIL_DIR="$PWD/nobox"',
            "agent-postbox list --to agent-b --live",   # missing store: must exit 2
        ])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("NO MAILBOX", bad.stdout)

    def test_section_references_resolve(self) -> None:
        sections = protocol_sections()
        self.assertIn("27", sections)
        self.assertIn("5a", sections)
        for path in doc_files():
            bad = bad_references(path.read_text(encoding="utf-8"), sections)
            self.assertEqual(bad, [], f"{path.name} cites missing PROTOCOL sections {bad}")

    def test_positive_control_bad_reference_is_caught(self) -> None:
        sections = protocol_sections()
        self.assertEqual(bad_references("see §99 and sections 10-99", sections).count("99"), 2)
        self.assertEqual(bad_references("see §8, section 5a, sections 10-27", sections), [])


class SupportedPythons(unittest.TestCase):
    def test_readme_range_matches_pyproject_classifiers(self) -> None:
        versions = supported_pythons()
        self.assertGreaterEqual(len(versions), 2, "pyproject.toml lost its 3.X classifiers")
        minors = [int(v.split(".")[1]) for v in versions]
        self.assertEqual(minors, list(range(minors[0], minors[-1] + 1)),
                         f"classifiers {versions} skip a minor; a 3.X-3.Y range would lie")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'requires-python = ">={versions[0]}"', pyproject)
        expected = f"Python {versions[0]}-{versions[-1]}"
        found = range_statements((ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(found), 1, "README no longer states the supported range")
        self.assertEqual(set(found), {expected}, f"README says {found}; pyproject.toml says {expected}")

    def test_pyproject_regex_fallback_agrees_with_tomllib(self) -> None:
        # The 3.10 path is the regex; on 3.11+ both run, so a drift between them shows here.
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(_CLASSIFIER.findall(text), supported_pythons())

    def test_ci_selftest_matrix_matches_pyproject_classifiers(self) -> None:
        self.assertEqual(ci_matrix_pythons(), supported_pythons())

    def test_positive_control_stale_range_is_caught(self) -> None:
        versions = supported_pythons()
        stale = f"Python {versions[0]}-{versions[-2]}"
        expected = f"Python {versions[0]}-{versions[-1]}"
        self.assertEqual(range_statements(f"POSIX only. {stale}."), [stale])
        self.assertNotEqual(set(range_statements(f"{expected}, then {stale}.")), {expected})
        self.assertEqual(range_statements("Python 3.10+ is required"), [])
        self.assertEqual(re.findall(r'"(3\.\d+)"', 'python: ["3.10", "3.11"]'), ["3.10", "3.11"])


class FirstReaderPath(unittest.TestCase):
    """What a reader who has never seen the project meets before the first exchange."""

    README = (ROOT / "README.md").read_text(encoding="utf-8")

    @staticmethod
    def glossary_terms(text: str) -> set[str]:
        m = re.search(r"^## Words used below\n(.*?)(?=^## )", text, re.S | re.M)
        if not m:
            return set()
        return set(re.findall(r"^\| `?([A-Za-z]+)`? \|", m.group(1), re.M)) - {"Word"}

    def test_glossary_defines_every_message_type_and_the_unfamiliar_words(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_mail_for_docs", ROOT / "agent_mail.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        terms = self.glossary_terms(self.README)
        self.assertEqual(sorted(set(mod.TYPES) - terms), [], "README glossary omits a message type")
        for word in ("owed", "live", "quarantined", "canary", "pickup"):
            self.assertIn(word, terms)
        # the glossary comes before the first command a reader runs
        self.assertLess(self.README.index("## Words used below"), self.README.index("## Quickstart"))


    def test_positive_control_glossary_check_sees_a_missing_term(self) -> None:
        self.assertEqual(self.glossary_terms("## Words used below\n| Word | x |\n| `ASK` | a |\n\n## Next\n"),
                         {"ASK"})
        self.assertEqual(self.glossary_terms("no glossary here"), set())


    def test_pip_option_warns_about_externally_managed_pythons(self) -> None:
        """`pip install ./clone` on a distribution-managed python3 (PEP 668) installs nothing;
        the README must say so beside the command and give the venv form."""
        m = re.search(r"\*\*Option 1: pip.*?\*\*Option 2", self.README, re.S)
        self.assertIsNotNone(m, "README lost its pip option")
        assert m is not None
        for needle in ("externally-managed-environment", "PEP 668", "python3 -m venv",
                       ".venv/bin/agent-postbox"):
            self.assertIn(needle, m.group(0))


    def test_installed_copy_is_where_the_readme_says_and_runs(self) -> None:
        """The CLI section tells an install.py user to run tools/agent-postbox/agent_mail.py
        in place of the pip-only `agent-postbox` command."""
        self.assertIn("python3 tools/agent-postbox/agent_mail.py", self.README)
        self.assertIn("`command not found`", self.README)
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / ".git").mkdir(parents=True)
            env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
            env["HOME"] = tmp
            ins = subprocess.run([sys.executable, "-I", str(ROOT / "install.py"), str(proj)],
                                 env=env, text=True, capture_output=True, timeout=120)
            self.assertEqual(ins.returncode, 0, ins.stdout + ins.stderr)
            cp = subprocess.run([sys.executable, "tools/agent-postbox/agent_mail.py", "--version"],
                                cwd=proj, env=env, text=True, capture_output=True, timeout=60)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn("agent-postbox", cp.stdout)

    def test_pip_option_says_it_needs_a_package_index(self) -> None:
        """pip builds in an isolated environment and fetches setuptools from an index
        (measured with the index disabled), so the README must not imply pip works offline."""
        m = re.search(r"\*\*Option 1: pip.*?\*\*Option 2", self.README, re.S)
        self.assertIsNotNone(m, "README lost its pip option")
        assert m is not None
        self.assertIn("setuptools>=77", m.group(0))
        self.assertRegex(m.group(0), r"network access|package mirror")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires = ["setuptools>=77"]', pyproject)   # the claim names the real pin


class RemovalInstructions(unittest.TestCase):
    """docs/troubleshooting.md "Removing it" is copy-pasted by someone who wants the tool gone
    and the messages kept, so the block that deletes messages must not be in the first block."""

    DOC = ROOT / "docs" / "troubleshooting.md"

    @staticmethod
    def blocks(text: str) -> list[str]:
        m = re.search(r"^## Removing it\n(.*)", text, re.S | re.M)
        return re.findall(r"^```sh\n(.*?)^```", m.group(1), re.S | re.M) if m else []

    def test_mailbox_deletion_is_its_own_block_and_the_first_block_keeps_it(self) -> None:
        blocks = self.blocks(self.DOC.read_text(encoding="utf-8"))
        self.assertEqual(len(blocks), 2, "expected the keep-the-mailbox block and the delete block")
        self.assertNotIn("rm -rf docs", blocks[0])
        self.assertIn("rm -rf docs/agent-mail", blocks[1])
        self.assertIn("DESTRUCTIVE", blocks[1])

    def test_positive_control_block_splitter_sees_a_merged_block(self) -> None:
        merged = "## Removing it\n\n```sh\nrm -rf tools\nrm -rf docs/agent-mail\n```\n"
        self.assertEqual(len(self.blocks(merged)), 1)
        self.assertIn("rm -rf docs", self.blocks(merged)[0])
        self.assertEqual(self.blocks("no section"), [])

    def test_first_block_removes_the_tool_and_hook_and_keeps_the_messages(self) -> None:
        import json
        blocks = self.blocks(self.DOC.read_text(encoding="utf-8"))
        self.assertEqual(len(blocks), 2)
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            (proj / ".git").mkdir(parents=True)
            (proj / ".claude").mkdir()
            original = {"model": "x", "hooks": {"UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "echo mine"}]}]}}
            (proj / ".claude" / "settings.json").write_text(json.dumps(original, indent=2) + "\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
            env["HOME"] = tmp

            def sh(*argv: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(list(argv), cwd=proj, env=env, text=True,
                                      capture_output=True, timeout=120)

            ins = sh(sys.executable, "-I", str(ROOT / "install.py"), ".", "--adapter", "claude-code")
            self.assertEqual(ins.returncode, 0, ins.stdout + ins.stderr)
            sent = sh(sys.executable, "tools/agent-postbox/agent_mail.py", "send", "--type", "NOTICE",
                      "--from", "agent-a", "--to", "agent-b", "--subject", "keep me", "--body", "x")
            self.assertEqual(sent.returncode, 0, sent.stderr)
            script = Path(tmp) / "remove.sh"
            script.write_text(blocks[0], encoding="utf-8")
            done = sh(BASH, str(script))
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertFalse((proj / "tools").exists())
            self.assertEqual(len(list((proj / "docs" / "agent-mail").glob("*-keep-me.md"))), 1)
            self.assertEqual(json.loads((proj / ".claude" / "settings.json").read_text()), original)
            self.assertFalse((proj / ".claude" / "settings.json.bak").exists())
            # the second block is the one that deletes the messages
            script.write_text(blocks[1], encoding="utf-8")
            self.assertEqual(sh(BASH, str(script)).returncode, 0)
            self.assertFalse((proj / "docs").exists())


if __name__ == "__main__":
    unittest.main()
