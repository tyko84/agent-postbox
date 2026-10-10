#!/usr/bin/env python3
"""Packaging test: build wheel + sdist, inspect them, install, round-trip.

Builds a COPY of the tree (the checkout stays clean) with `python -m build`,
then:
  * wheel holds agent_mail.py + dist-info only; sdist holds only allow-listed
    files (no __pycache__, .git, .github, stray/private files);
  * no artifact member name or content trips check_publication.py: the generic
    detectors always, plus any forbidden list supplied at run time through the same
    mechanism (POSTBOX_FORBIDDEN); no real name is stored in this file;
  * the single version source (agent_mail.__version__) is what the metadata says;
  * the wheel installs into a clean venv, `agent-postbox --version` works and a
    real send/list/canary/doctor round trip succeeds with AGENT_MAIL_DIR in a
    temp dir.
Positive controls: the same inspectors must FAIL on a tampered sdist and on
planted bad names/text. Skips with a message if `build` (or network access for
the build backend) is unavailable. POSIX only. Stdlib + `build`.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))
import check_publication  # noqa: E402  (the scanner is the single source of the matching rules)

# Synthetic stand-ins used by the positive controls. Real forbidden names are never stored
# here; they arrive at run time via POSTBOX_FORBIDDEN, exactly as for check_publication.py.
SYNTHETIC = ("acme-private", "widget corp")
# Generic by design: substrings stripped from content before scanning (e.g. an intended
# copyright holder). Empty here; real exceptions belong in the run-time list's allowlist.
ALLOWED_MENTIONS: tuple[str, ...] = ()

SDIST_ALLOWED = (
    "LICENSE", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "PROTOCOL.md", "SECURITY.md",
    "PARTICIPANTS.md", "PARTICIPANTS.template.md", "handoff_template.md",
    "install.py", "check_handoff.py", "check_publication.py", "selftest.py", "stress_test.py", "agent_mail.py",
    "test_*.py", "ruff.toml", "mypy.ini", "pyproject.toml", "MANIFEST.in",
    "PKG-INFO", "setup.cfg", "hooks/*.py", "docs/*.md", "*.egg-info/*",
)
WHEEL_ALLOWED = ("agent_mail.py", "*.dist-info/*")
BAD_PARTS = ("__pycache__", ".git/", ".github/", ".pyc", ".bak", ".env", ".DS_Store")


def unexpected(names: list[str], allowed: tuple[str, ...], strip_top: bool) -> list[str]:
    """Names not matching the allow-list, or containing a forbidden fragment."""
    out = []
    for n in names:
        if n.endswith("/"):
            continue
        rel = n.split("/", 1)[1] if strip_top and "/" in n else n
        if any(b in n for b in BAD_PARTS) or not any(fnmatch.fnmatch(rel, a) for a in allowed):
            out.append(n)
    return out


def private_mentions(name: str, data: bytes,
                     patterns: tuple[str, ...] | None = None) -> list[str]:
    """Findings as "<name>  <rule-id>" (never the matched text). `patterns` defaults to the
    run-time list (POSTBOX_FORBIDDEN); generic detectors always run."""
    pats = list(check_publication.load_patterns(dict(os.environ), None)
                if patterns is None else patterns)
    text = data.decode("utf-8", "replace")
    for ok in ALLOWED_MENTIONS:
        text = text.replace(ok, "")
    sc = check_publication.Scanner(pats, [])
    sc.scan_text(name, text)
    return [f"{loc}  {rule}" for loc, rule in sc.findings]


def sdist_members(path: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(path) as t:
        for m in t.getmembers():
            f = t.extractfile(m) if m.isfile() else None
            if f is not None:  # directories carry no content and are not files
                out[m.name] = f.read()
    return out


def wheel_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def scan(members: dict[str, bytes], patterns: tuple[str, ...] | None = None) -> list[str]:
    hits: list[str] = []
    for n, d in members.items():
        hits += private_mentions(n, d, patterns) + private_mentions(n, n.encode(), patterns)
    return hits


def _skip_reason() -> str | None:
    if importlib.util.find_spec("build") is None:
        return "the 'build' package is not installed (pip install build); packaging NOT verified"
    return None


@unittest.skipIf(_skip_reason() is not None, _skip_reason() or "")
class Packaging(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    wheel: Path
    sdist: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        src = base / "src"
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(
            ".git", "build", "dist", "*.egg-info", "__pycache__", ".venv", "venv"))
        out = base / "out"
        r = subprocess.run([sys.executable, "-m", "build", "--outdir", str(out), str(src)],
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            net = re.search(r"Connection|Temporary failure|No matching distribution|"
                            r"Could not find a version|network", r.stdout, re.I)
            cls.tmp.cleanup()
            if net:
                raise unittest.SkipTest("cannot fetch the build backend (no network); "
                                        "packaging NOT verified")
            raise AssertionError(f"build failed:\n{r.stdout[-3000:]}")
        cls.wheel = next(out.glob("*.whl"))
        cls.sdist = next(out.glob("*.tar.gz"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_wheel_contents(self) -> None:
        names = list(wheel_members(self.wheel))
        self.assertIn("agent_mail.py", names)
        self.assertEqual(unexpected(names, WHEEL_ALLOWED, strip_top=False), [])

    def test_sdist_contents(self) -> None:
        names = list(sdist_members(self.sdist))
        self.assertTrue(any(n.endswith("/agent_mail.py") for n in names))
        self.assertEqual(unexpected(names, SDIST_ALLOWED, strip_top=True), [])

    def test_no_private_mentions_in_either_artifact(self) -> None:
        self.assertEqual(scan(wheel_members(self.wheel)), [])
        self.assertEqual(scan(sdist_members(self.sdist)), [])

    def test_version_has_one_source(self) -> None:
        spec = importlib.util.spec_from_file_location("agent_mail_v", ROOT / "agent_mail.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIn(f"agent_postbox-{mod.__version__}", self.wheel.name)
        meta = next(d for n, d in wheel_members(self.wheel).items() if n.endswith("METADATA"))
        self.assertIn(f"Version: {mod.__version__}", meta.decode())
        self.assertIsNone(re.search(r'^version\s*=\s*"', (ROOT / "pyproject.toml").read_text(),
                                    re.M), "version must be dynamic, not duplicated")

    def test_install_and_round_trip(self) -> None:
        base = Path(self.tmp.name)
        env_dir = base / "venv"
        venv.create(env_dir, with_pip=True, clear=True)
        py = env_dir / "bin" / "python"
        r = subprocess.run([str(py), "-m", "pip", "install", "--no-index", "--no-deps",
                            "--disable-pip-version-check", str(self.wheel)],
                           text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        exe = str(env_dir / "bin" / "agent-postbox")
        box = base / "mailbox"
        box.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
        env["AGENT_MAIL_DIR"] = str(box)
        env["PYTHONPATH"] = ""  # prove it runs from the installed copy, not this checkout

        def run(*a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([exe, *a], cwd=base, env=env, text=True, capture_output=True)

        v = run("--version")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertRegex(v.stdout, r"^agent-postbox \d+\.\d+\.\d+")
        body = base / "b.txt"
        body.write_text("round trip\n")
        s = run("send", "--type", "ASK", "--from", "agent-a", "--to", "agent-b",
                "--subject", "packaging round trip", "--body-file", str(body))
        self.assertEqual(s.returncode, 0, s.stderr)
        li = run("list", "--to", "agent-b", "--live")
        self.assertIn("packaging round trip", li.stdout)
        # the installed tool ran from site-packages, not from the checkout
        self.assertIn("site-packages", subprocess.run(
            [str(py), "-c", "import agent_mail;print(agent_mail.__file__)"],
            cwd=base, env=env, text=True, capture_output=True).stdout)
        c = run("canary", "--from", "agent-a", "--to", "agent-b")
        self.assertEqual(c.returncode, 0, c.stderr)
        d = run("doctor")
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertIn("store: OK", d.stdout)

    # ---- positive controls: the inspectors must be able to fail ----

    def test_control_tampered_sdist_is_caught(self) -> None:
        tampered = Path(self.tmp.name) / "tampered.tar.gz"
        with tarfile.open(self.sdist) as src, tarfile.open(tampered, "w:gz") as dst:
            for m in src.getmembers():
                f = src.extractfile(m) if m.isfile() else None
                dst.addfile(m, f)
            top = src.getnames()[0].split("/")[0]
            for name, data in (("notes-internal.txt", b"x"),
                               ("__pycache__/agent_mail.cpython-312.pyc", b"x"),
                               ("docs/leak.md", b"internal notes from Acme-Private")):
                ti = tarfile.TarInfo(f"{top}/{name}")
                ti.size = len(data)
                dst.addfile(ti, io.BytesIO(data))
        members = sdist_members(tampered)
        bad = unexpected(list(members), SDIST_ALLOWED, strip_top=True)
        self.assertEqual(len(bad), 2, bad)  # docs/leak.md is allow-listed by name...
        self.assertTrue(scan(members, SYNTHETIC), "...so only the content scan can catch it")
        self.assertEqual(scan(members, ()), [], "control: no pattern supplied, nothing to find")

    def test_control_planted_names_and_text(self) -> None:
        self.assertEqual(unexpected(["pkg-1/secret.txt", "pkg-1/agent_mail.py"],
                                    SDIST_ALLOWED, True), ["pkg-1/secret.txt"])
        self.assertTrue(private_mentions("x", b"WIDGET CORP internal", SYNTHETIC))
        self.assertEqual(private_mentions("LICENSE", b"Copyright (c) 2026 Example LLC",
                                          SYNTHETIC), [])
        # the generic detectors fire even when no list is supplied
        self.assertTrue(private_mentions("x", b"see /ho" + b"me/someone/project/", ()))
        # a pattern supplied through the environment mechanism is honoured
        saved = os.environ.get(check_publication.ENV_VAR)
        os.environ[check_publication.ENV_VAR] = "acme-private"
        try:
            self.assertTrue(private_mentions("x", b"ACME-PRIVATE"))
        finally:
            if saved is None:
                del os.environ[check_publication.ENV_VAR]
            else:
                os.environ[check_publication.ENV_VAR] = saved


if __name__ == "__main__":
    unittest.main()
