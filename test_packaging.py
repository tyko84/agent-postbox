#!/usr/bin/env python3
"""Packaging test: build wheel + sdist, inspect them, install, round-trip.

Builds a COPY of the tree (the checkout stays clean) with `python -m build`,
then:
  * wheel holds agent_mail.py + dist-info only; sdist holds only allow-listed
    files (no __pycache__, .git, .github, stray/private files);
  * no artifact member name or content trips check_publication.py: the generic
    detectors always, a built-in SYNTHETIC term always (so the forbidden-pattern path
    is exercised on every run, not only when a list is configured), plus any forbidden
    list supplied at run time through the same mechanism (POSTBOX_FORBIDDEN); no real
    name is stored in this file. Findings name the member and a positional rule id;
    matched text is never printed;
  * the single version source (agent_mail.__version__) is what the metadata says;
  * the wheel installs into a clean venv, `agent-postbox --version` works and a
    real send/list/canary/doctor round trip succeeds with AGENT_MAIL_DIR in a
    temp dir.
A second build goes through scripts/repro_build.py (from a git checkout only;
skipped with a message elsewhere, e.g. in an unpacked sdist) and pins what that
script exists for: the sdist's tar headers name no builder (uid = gid = 0, empty
user and group names), every timestamp is SOURCE_DATE_EPOCH, the gzip header
has no timestamp and no file name, and no member of either artifact contains a
path of the machine that built it. It also pins the release side of that script:
SHA256SUMS (format, content, determinism), `--verify` (passes on its own output,
names the file when one byte of a copy is flipped), the pinned build backend
(build-constraints.txt is honoured, and is deliberately not in the sdist) and a
BUILDINFO block that names no builder.
Over the same artifacts, scripts/verify_release.py (the consumer-side release
check) is run with --no-download: it must pass on them and name the right FAIL
line on copies broken one way at a time (a SHA256SUMS line, an extra wheel
member, an sdist member with an owner or another mtime or escaping the top
directory, a wrong Version). Its attestation check is SKIPped here
(--skip-attestation); the published release is what `gh attestation verify`
is for.
Positive controls: the same inspectors must FAIL on a tampered sdist and on
planted bad names/text, owners, timestamps and paths. Skips with a message if
`build` (or network access for the build backend) is unavailable. POSIX only.
Stdlib + `build`.
"""
from __future__ import annotations

import fnmatch
import getpass
import gzip
import hashlib
import importlib.util
import io
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import venv
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))
import check_publication  # noqa: E402  (the scanner is the single source of the matching rules)

# Synthetic stand-ins used by the positive controls. Real forbidden names are never stored
# here; they arrive at run time via POSTBOX_FORBIDDEN, exactly as for check_publication.py.
SYNTHETIC = ("acme-private", "widget corp")
# An invented word that is ALWAYS scanned for, so the real artifact scan is never a no-op.
# Stored reversed so this file, which ships in the sdist, does not match itself (a plain
# split literal would be caught by the scanner's own join pass).
BUILTIN = ("talbrovqx"[::-1],)

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
    pats = list(BUILTIN + tuple(check_publication.load_patterns(dict(os.environ), None))
                if patterns is None else patterns)
    sc = check_publication.Scanner(pats, [])
    sc.scan_name(name, name)  # a member NAME hit is reported as "<name>#name"
    sc.scan_text(name, data.decode("utf-8", "replace"))
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
        hits += private_mentions(n, d, patterns)
    return hits


def sdist_owner_leaks(path: Path) -> list[str]:
    """Tar members whose header identifies a builder: a non-zero uid/gid or a user/group
    name other than "" or root. Reported as "<member>  <field>"; the value is never printed
    (it would be somebody's login name)."""
    out: list[str] = []
    with tarfile.open(path) as t:
        for m in t.getmembers():
            for field, bad in (("uid", m.uid != 0), ("gid", m.gid != 0),
                               ("uname", m.uname not in ("", "root")),
                               ("gname", m.gname not in ("", "root")),
                               ("pax", bool(m.pax_headers))):
                if bad:
                    out.append(f"{m.name}  {field}")
    return out


def sdist_mtimes(path: Path) -> set[float]:
    with tarfile.open(path) as t:
        return {m.mtime for m in t.getmembers()}


def gzip_header(path: Path) -> tuple[int, int]:
    """(flags, mtime) of a gzip file. Flag bit 3 (0x08) means a file name is stored."""
    with open(path, "rb") as fh:
        head = fh.read(10)
    _magic, _method, flags, mtime = struct.unpack("<HBBI", head[:8])
    return flags, mtime


def build_paths(members: dict[str, bytes], needles: list[str]) -> list[str]:
    """Members whose name or content contains one of `needles` (paths of the build machine).
    Reported as "<member>  build-path-<n>": positional, the path itself is not printed."""
    out: list[str] = []
    for name, data in members.items():
        for i, needle in enumerate(needles, 1):
            if needle and (needle in name or needle.encode() in data):
                out.append(f"{name}  build-path-{i}")
    return out


def _needles(*paths: Path | str) -> list[str]:
    """Each path as given and fully resolved (macOS temp dirs are reached through a symlink)."""
    out: list[str] = []
    for p in paths:
        for s in (str(p), os.path.realpath(p)):
            if s not in out and len(s) > 4:
                out.append(s)
    return out


def _skip_reason() -> str | None:
    if importlib.util.find_spec("build") is None:
        return "the 'build' package is not installed (pip install build); packaging NOT verified"
    return None


@unittest.skipIf(_skip_reason() is not None, _skip_reason() or "")
class Packaging(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    wheel: Path
    sdist: Path
    venv_ready = False

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

    def test_no_build_paths_in_either_artifact(self) -> None:
        needles = _needles(self.tmp.name, ROOT)
        self.assertEqual(build_paths(wheel_members(self.wheel), needles), [])
        self.assertEqual(build_paths(sdist_members(self.sdist), needles), [])

    def test_version_has_one_source(self) -> None:
        spec = importlib.util.spec_from_file_location("agent_mail_v", ROOT / "agent_mail.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertIn(f"tyko84_agent_postbox-{mod.__version__}", self.wheel.name)
        meta = next(d for n, d in wheel_members(self.wheel).items() if n.endswith("METADATA"))
        self.assertIn(f"Version: {mod.__version__}", meta.decode())
        self.assertIsNone(re.search(r'^version\s*=\s*"', (ROOT / "pyproject.toml").read_text(),
                                    re.M), "version must be dynamic, not duplicated")

    def installed(self) -> tuple[Path, str]:
        """The wheel installed into a clean venv (once): its python and its console script."""
        cls = type(self)
        env_dir = Path(self.tmp.name) / "venv"
        py = env_dir / "bin" / "python"
        if not cls.venv_ready:
            venv.create(env_dir, with_pip=True, clear=True)
            r = subprocess.run([str(py), "-m", "pip", "install", "--no-index", "--no-deps",
                                "--disable-pip-version-check", str(self.wheel)],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            cls.venv_ready = True
        return py, str(env_dir / "bin" / "agent-postbox")

    def test_installed_console_script_when_its_reader_goes_away(self) -> None:
        """PROTOCOL.md section 35 on the real `agent-postbox` that pip generates: the 0.3.0
        fix lived in agent_mail.py's `__main__` block, which this script never runs."""
        py, exe = self.installed()
        wrapper = Path(exe).read_text(encoding="utf-8")
        # test_adversarial.py and selftest.py run exactly these two statements in every CI
        # cell; if a new setuptools generates something else, they must change with it
        self.assertIn("from agent_mail import main", wrapper)
        self.assertIn("sys.exit(main())", wrapper)
        base = Path(self.tmp.name)
        box = base / "pipe-mailbox"
        box.mkdir()
        for i in range(1500):   # a listing far larger than a pipe buffer
            mid = f"01ARZ3NDEKTSV4RRFFQ6{i:06d}"
            (box / f"{mid}-p.md").write_text(
                f"---\nid: {mid}\ntype: NOTICE\nfrom: alice\nto: bob\n"
                f"date: 2099-01-01T00:00:00Z\nsubject: pipe {i}\n---\n\nbody\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("AGENT_MAIL", "PYTHON"))}
        env["AGENT_MAIL_DIR"] = str(box)

        def gone(*a: str) -> tuple[int, str]:
            r_fd, w_fd = os.pipe()
            os.close(r_fd)          # the reader has left before the first byte
            try:
                r = subprocess.run([exe, *a], cwd=base, env=env, stdout=w_fd,
                                   stderr=subprocess.PIPE, text=True, timeout=120)
            finally:
                os.close(w_fd)
            return r.returncode, r.stderr

        full = subprocess.run([exe, "list"], cwd=base, env=env, capture_output=True, timeout=120)
        self.assertEqual((full.returncode, full.stderr), (0, b""))   # control: a reader that stays
        self.assertGreater(len(full.stdout), 100_000)
        for argv in (["list"], ["list", "--live"], ["inbox", "--to", "bob"], ["status"],
                     ["status", "--json"], ["doctor"], ["latency"], ["--help"], ["--version"]):
            with self.subTest(argv=argv):
                self.assertEqual(gone(*argv), (141, ""))
        # the shell's own view, with real head/grep as the reader
        for reader, want in (("head -1", full.stdout.split(b"\n")[0] + b"\n"),
                             ("head -c 1", full.stdout[:1]),
                             ("grep -m1 'pipe 0$'", b"  NOTICE  [fresh     ] alice -> bob  pipe 0\n")):
            with self.subTest(reader=reader):
                r = subprocess.run(["bash", "-c", f'set -o pipefail; "$0" list | {reader}', exe],
                                   cwd=base, env=env, capture_output=True, timeout=120)
                self.assertEqual((r.returncode, r.stderr, r.stdout), (141, b"", want))
        # a writer finishes and keeps its own status; a keyed retry does not file a second copy
        send = ["send", "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject",
                "no reader", "--body", "b", "--key", "pipe-key"]
        self.assertEqual(gone(*send), (0, ""))
        self.assertEqual(gone(*send), (0, ""))
        again = subprocess.run([exe, *send], cwd=base, env=env, capture_output=True, text=True,
                               timeout=120)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(again.stdout.startswith("duplicate of "), again.stdout)
        self.assertEqual(sum("subject: no reader\n" in f.read_text(encoding="utf-8")
                             for f in box.glob("*.md")), 1)
        self.assertEqual(gone("show", "NOSUCHID"), (1, "no message with id NOSUCHID\n"))
        self.assertIn("site-packages", subprocess.run(
            [str(py), "-c", "import agent_mail;print(agent_mail.__file__)"],
            cwd=base, env=env, text=True, capture_output=True).stdout)

    def test_install_and_round_trip(self) -> None:
        base = Path(self.tmp.name)
        py, exe = self.installed()
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
        self.assertEqual(private_mentions("x", b"WIDGET CORP internal", SYNTHETIC),
                         ["x  forbidden-2"])  # positional id only, never the text
        self.assertEqual(private_mentions("x", b"ACME-PRIVATE", SYNTHETIC), ["x  forbidden-1"])
        self.assertEqual(private_mentions("LICENSE", b"Copyright (c) 2026 Example LLC",
                                          SYNTHETIC), [])
        # the built-in term is active with no list configured, in content and in a member name
        self.assertEqual(private_mentions("x", f"see {BUILTIN[0].upper()}".encode()),
                         ["x  forbidden-1"])
        self.assertEqual(scan({f"pkg-1/{BUILTIN[0]}.txt": b"clean"}),
                         [f"pkg-1/{BUILTIN[0]}.txt#name  forbidden-1"])
        # the generic detectors fire even when no list is supplied
        self.assertTrue(private_mentions("x", b"see /ho" + b"me/someone/project/", ()))
        # a pattern supplied through the environment mechanism is honoured
        saved = os.environ.get(check_publication.ENV_VAR)
        os.environ[check_publication.ENV_VAR] = "acme-private"
        try:
            self.assertEqual(private_mentions("x", b"ACME-PRIVATE"), ["x  forbidden-2"])
        finally:
            if saved is None:
                del os.environ[check_publication.ENV_VAR]
            else:
                os.environ[check_publication.ENV_VAR] = saved



REPRO = ROOT / "scripts" / "repro_build.py"
VERIFY = ROOT / "scripts" / "verify_release.py"


def _repro_skip_reason() -> str | None:
    if _skip_reason():
        return _skip_reason()
    if not REPRO.is_file():
        return "scripts/repro_build.py is not here (an unpacked sdist?); reproducible build NOT verified"
    try:
        inside = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                                text=True, capture_output=True)
    except FileNotFoundError:
        return "git is not installed; reproducible build NOT verified"
    if inside.returncode != 0 or os.path.realpath(inside.stdout.strip()) != os.path.realpath(ROOT):
        return "not a git checkout of this project; reproducible build NOT verified"
    return None


@unittest.skipIf(_repro_skip_reason() is not None, _repro_skip_reason() or "")
class ReproducibleBuild(unittest.TestCase):
    """The artifacts scripts/repro_build.py makes (it builds the HEAD commit, not the work tree)."""
    tmp: tempfile.TemporaryDirectory[str]
    wheel: Path
    sdist: Path
    epoch: int
    log: str
    out: Path
    env: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        scratch, out = base / "scratch", base / "out"
        scratch.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"}
        env["TMPDIR"] = str(scratch)  # the script builds under here, so the path is known
        r = subprocess.run([sys.executable, str(REPRO), "--outdir", str(out)], cwd=base, env=env,
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cls.log = r.stdout
        cls.out, cls.env = out, env
        if r.returncode != 0:
            net = re.search(r"Connection|Temporary failure|No matching distribution|"
                            r"Could not find a version|network", r.stdout, re.I)
            cls.tmp.cleanup()
            if r.returncode == 2 and net:
                raise unittest.SkipTest("cannot fetch the build backend (no network); "
                                        "reproducible build NOT verified")
            raise AssertionError(f"repro_build.py failed ({r.returncode}):\n{r.stdout[-3000:]}")
        cls.wheel = next(out.glob("*.whl"))
        cls.sdist = next(out.glob("*.tar.gz"))
        m = re.search(r"^SOURCE_DATE_EPOCH: (\d+)$", r.stdout, re.M)
        assert m, r.stdout
        cls.epoch = int(m.group(1))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_contents_are_the_allow_lists(self) -> None:
        wheel, sdist = wheel_members(self.wheel), sdist_members(self.sdist)
        self.assertIn("agent_mail.py", wheel)
        self.assertEqual(unexpected(list(wheel), WHEEL_ALLOWED, strip_top=False), [])
        self.assertTrue(any(n.endswith("/agent_mail.py") for n in sdist))
        self.assertEqual(unexpected(list(sdist), SDIST_ALLOWED, strip_top=True), [])
        self.assertEqual(scan(wheel), [])
        self.assertEqual(scan(sdist), [])

    def test_sdist_names_no_builder(self) -> None:
        self.assertEqual(sdist_owner_leaks(self.sdist), [])

    def test_timestamps_are_source_date_epoch(self) -> None:
        self.assertEqual(sdist_mtimes(self.sdist), {self.epoch})
        self.assertEqual(gzip_header(self.sdist), (0, 0), "gzip header: no file name, mtime 0")
        with tarfile.open(self.sdist) as t:
            names = t.getnames()
        self.assertEqual(names, sorted(names))
        with zipfile.ZipFile(self.wheel) as z:
            stamps = {i.date_time for i in z.infolist()}
        self.assertEqual(len(stamps), 1, stamps)

    def test_no_build_paths_in_either_artifact(self) -> None:
        needles = _needles(self.tmp.name, ROOT)
        self.assertEqual(build_paths(wheel_members(self.wheel), needles), [])
        self.assertEqual(build_paths(sdist_members(self.sdist), needles), [])

    def test_hashes_are_printed(self) -> None:
        for art in (self.wheel, self.sdist):
            self.assertIn(f"{hashlib.sha256(art.read_bytes()).hexdigest()}  {art.name}", self.log)

    # ---- release side: SHA256SUMS, --verify, the pinned backend, BUILDINFO ----

    def _repro(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(REPRO), *argv], cwd=self.tmp.name, env=self.env,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def _skip_if_offline(self, r: subprocess.CompletedProcess[str]) -> None:
        if r.returncode == 2 and re.search(r"Connection|Temporary failure|No matching distribution|"
                                           r"Could not find a version|network", r.stdout, re.I):
            self.skipTest("cannot fetch the build backend (no network); NOT verified")

    def _digests(self) -> dict[str, str]:
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.wheel, self.sdist)}

    def test_sha256sums_format(self) -> None:
        raw = (self.out / "SHA256SUMS").read_bytes()
        want = "".join(f"{h}  {n}\n" for n, h in sorted(self._digests().items()))
        self.assertEqual(raw, want.encode("ascii"), "exactly '<hex>  <name>' per artifact, sorted by name")
        self.assertNotIn(b"\r", raw)
        self.assertNotIn(b"/", raw, "file names only, never a directory")
        self.assertEqual(len(raw.splitlines()), 2)
        self.assertEqual(int((self.out / "SHA256SUMS").stat().st_mtime), self.epoch)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         sorted(["SHA256SUMS", self.wheel.name, self.sdist.name]))

    def test_verify_passes_on_its_own_output_and_sums_are_deterministic(self) -> None:
        again = Path(self.tmp.name) / "again"
        r = self._repro("--verify", str(self.out), "--outdir", str(again))
        self._skip_if_offline(r)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("verified: identical to a rebuild of ", r.stdout)
        self.assertNotIn("DIFFERENT", r.stdout)
        for name in ("SHA256SUMS", self.wheel.name, self.sdist.name):  # a second, independent build
            self.assertEqual((again / name).read_bytes(), (self.out / name).read_bytes(), name)

    def test_verify_names_the_file_when_one_byte_is_flipped(self) -> None:
        base = Path(self.tmp.name)
        bad = base / "flipped"
        shutil.copytree(self.out, bad)
        blob = bytearray((bad / self.wheel.name).read_bytes())
        blob[len(blob) // 2] ^= 0x01
        (bad / self.wheel.name).write_bytes(bytes(blob))
        r = self._repro("--verify", str(bad))
        self._skip_if_offline(r)
        self.assertEqual(r.returncode, 1, r.stdout[-3000:])
        self.assertIn(f"DIFFERENT  {self.wheel.name}\n", r.stdout)
        self.assertIn(f"  rebuilt  {self._digests()[self.wheel.name]}\n", r.stdout)
        self.assertIn(f"  found    {hashlib.sha256(bytes(blob)).hexdigest()}\n", r.stdout)
        self.assertIn(f"OK         {self.sdist.name}  {self._digests()[self.sdist.name]}", r.stdout,
                      "the untouched sdist is still reported identical")
        self.assertNotIn(f"DIFFERENT  {self.sdist.name}", r.stdout)
        self.assertIn("NOT VERIFIED: 1 difference(s)", r.stdout)
        self.assertFalse((base / "dist").exists(), "--verify alone writes nothing")

    def test_verify_dir_cases(self) -> None:
        """verify_dir() itself, against the artifacts already built (no rebuild needed)."""
        spec = importlib.util.spec_from_file_location("repro_build_t", REPRO)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        built = self._digests()
        base = Path(self.tmp.name) / "cases"

        def case(name: str, files: tuple[str, ...], sums: str | None) -> list[str]:
            d = base / name
            d.mkdir(parents=True)
            for f in files:
                shutil.copy2(self.out / f, d / f)
            if sums is not None:
                (d / "SHA256SUMS").write_bytes(sums.encode())
            return list(mod.verify_dir(d, built)[1])

        good = (self.out / "SHA256SUMS").read_text()
        w, t = self.wheel.name, self.sdist.name
        self.assertEqual(case("control", (w, t), good), [], "control: an exact copy has no difference")
        self.assertEqual(case("sums-only", (), good), [], "SHA256SUMS alone accounts for both")
        self.assertEqual(case("files-only", (w, t), None), [], "the two files alone account for both")
        self.assertEqual(case("crlf", (), good.replace("\n", "\r\n")), [], "CRLF is tolerated on input")
        swapped = good.replace(built[w], "0" * 64)
        bad = case("sums-edited", (w, t), swapped)
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith(f"DIFFERENT  {w} (its SHA256SUMS line)"), bad)
        self.assertEqual([b.split()[0] for b in case("empty", (), None)], ["MISSING", "MISSING"])
        self.assertEqual([b.split()[0] for b in case("one-missing", (w,), None)], ["MISSING"])
        self.assertTrue(case("extra-line", (w, t), good + "0" * 64 + "  other-9.9.9.tar.gz\n")[0]
                        .startswith("UNEXPECTED other-9.9.9.tar.gz"))
        d = base / "extra-file"
        d.mkdir()
        # a version no release can have (the release gate wants a plain X.Y.Z), so this is
        # an unexpected file and never the wheel of the commit under test
        (d / "tyko84_agent_postbox-0.0.0.dev0-py3-none-any.whl").write_bytes(b"x")
        shutil.copy2(self.out / "SHA256SUMS", d / "SHA256SUMS")
        self.assertEqual([b.split()[0] for b in mod.verify_dir(d, built)[1]], ["UNEXPECTED"])
        for label, text in (("path", good.replace("  ", "  dist/", 1)),
                            ("upper", good.replace(built[w], built[w].upper())),
                            ("twice", good + good)):
            got = case("malformed-" + label, (w, t), text)
            self.assertTrue(any(g.startswith("MALFORMED") for g in got), (label, got))
        self.assertEqual(mod.sums_text(built), good)
        self.assertEqual(mod.parse_sums(good), (built, []))

    def test_constraints_are_honoured(self) -> None:
        cons = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:build-constraints.txt"],
                              capture_output=True).stdout
        pin = re.search(rb"^setuptools==([0-9][0-9A-Za-z.]*)$", cons, re.M)
        self.assertIsNotNone(pin, "build-constraints.txt pins exactly one setuptools version")
        self.assertEqual([ln for ln in cons.decode().splitlines() if ln and not ln.startswith("#")],
                         [pin.group(0).decode() if pin else ""], "and nothing else")
        assert pin is not None
        version = pin.group(1).decode()
        wheel_file = next(d for n, d in wheel_members(self.wheel).items() if n.endswith("/WHEEL"))
        self.assertIn(f"Generator: setuptools ({version})", wheel_file.decode())
        self.assertIn(f"  setuptools: {version}\n", self.log)
        self.assertIn(f"  constraints: build-constraints.txt sha256 {hashlib.sha256(cons).hexdigest()}\n",
                      self.log)
        # pyproject.toml's floor is a promise to people building from a clone: still a floor
        self.assertIn('requires = ["setuptools>=77"]', (ROOT / "pyproject.toml").read_text())
        # Control: it is the constraints file that decides. Another pin (the floor itself)
        # gives another generator and another wheel hash, so the first result was no accident.
        base = Path(self.tmp.name)
        other = base / "floor-constraints.txt"
        other.write_text("setuptools==77.0.3\n")
        self.assertNotEqual(version, "77.0.3")
        r = self._repro("--constraints", str(other), "--outdir", str(base / "floor"))
        self._skip_if_offline(r)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        floor_wheel = next((base / "floor").glob("*.whl"))
        floor_file = next(d for n, d in wheel_members(floor_wheel).items() if n.endswith("/WHEEL"))
        self.assertIn("Generator: setuptools (77.0.3)", floor_file.decode())
        self.assertIn("  constraints: --constraints override sha256 ", r.stdout)
        self.assertIn("not the commit's reference hashes", r.stdout)
        self.assertNotEqual(hashlib.sha256(floor_wheel.read_bytes()).hexdigest(),
                            self._digests()[self.wheel.name])
        self.assertEqual(sdist_owner_leaks(next((base / "floor").glob("*.tar.gz"))), [])

    def test_backend_wheel_is_the_recorded_one(self) -> None:
        """build-constraints.txt records the SHA-256 of the setuptools wheel; the script
        fetches that wheel, checks it and lets the build install nothing else."""
        cons = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:build-constraints.txt"],
                              capture_output=True).stdout.decode()
        pin = re.search(r"^setuptools==([0-9][0-9A-Za-z.]*)$", cons, re.M)
        rec = re.findall(r"^# sha256: ([0-9a-f]{64})  (\S+)$", cons, re.M)
        assert pin is not None
        self.assertEqual([name for _, name in rec], [f"setuptools-{pin.group(1)}-py3-none-any.whl"],
                         "one recorded wheel, of the pinned version")
        self.assertIn(f"  setuptools_wheel: {rec[0][1]} sha256 {rec[0][0]} (verified: ", self.log)
        base = Path(self.tmp.name)
        # a wheel with another hash is refused before anything is built
        wrong = base / "wrong-hash.txt"
        wrong.write_text(cons.replace(rec[0][0], "0" * 64))
        r = self._repro("--constraints", str(wrong), "--outdir", str(base / "wrong"))
        self._skip_if_offline(r)
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("is not the recorded one", r.stdout)
        self.assertFalse((base / "wrong").exists(), "nothing was written")
        # two recorded wheels: refused, the file must name exactly one
        twice = base / "twice.txt"
        twice.write_text(cons + f"# sha256: {'1' * 64}  other-1.0-py3-none-any.whl\n")
        r = self._repro("--constraints", str(twice), "--outdir", str(base / "twice"))
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("more than one backend wheel", r.stdout)
        # Control for "nothing else": the isolated build does obey PIP_NO_INDEX. With no
        # wheel recorded nothing is fetched beforehand, so the same setting leaves the
        # build with no source for its backend and it fails. The successful build above
        # ran under that setting plus the one checked wheel.
        bare = base / "no-record.txt"
        bare.write_text(f"setuptools=={pin.group(1)}\n")
        env = {**self.env, "PIP_NO_INDEX": "1"}
        for name in ("PIP_FIND_LINKS", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
            env.pop(name, None)
        r = subprocess.run([sys.executable, str(REPRO), "--constraints", str(bare),
                            "--outdir", str(base / "bare")], cwd=self.tmp.name, env=env,
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("python -m build failed", r.stdout)
        # and without it the same file builds, unverified, and says so
        r = self._repro("--constraints", str(bare), "--outdir", str(base / "bare"))
        self._skip_if_offline(r)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("  setuptools_wheel: not verified (the constraints file records no sha256)\n",
                      r.stdout)

    def test_build_tools_are_pinned_by_hash(self) -> None:
        """requirements-build.txt: every requirement has one exact version and a sha256,
        wheels only, and its `build` is the one requirements-dev.txt pins."""
        text = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
        logical = [ln for ln in text.replace("\\\n", " ").splitlines()
                   if ln.strip() and not ln.startswith("#")]
        self.assertEqual(logical[0].split(), ["--only-binary", ":all:"])
        names = []
        for line in logical[1:]:
            m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][0-9A-Za-z.]*)((?:\s+--hash=sha256:[0-9a-f]{64})+)",
                             line.strip())
            self.assertIsNotNone(m, line)
            assert m is not None
            names.append(m.group(1))
        self.assertEqual(names, ["build", "packaging", "pyproject_hooks"])
        dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        pins = [re.search(r"^build==(\S+)", t, re.M) for t in (text, dev)]
        self.assertTrue(all(pins))
        self.assertEqual(len({p.group(1) for p in pins if p}), 1, "the two build pins differ")
        self.assertIn("  build: ", self.log)
        self.assertIn(f"(requirements-build.txt pins {pins[0].group(1) if pins[0] else ''})", self.log)
        # control: the line pattern does reject a requirement without a hash
        self.assertIsNone(re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][0-9A-Za-z.]*)((?:\s+--hash=sha256:[0-9a-f]{64})+)",
                                       "build==1.6.1"))

    def test_constraints_file_is_not_in_the_sdist(self) -> None:
        # Deliberate: it only means something to scripts/repro_build.py, which needs a git
        # checkout and is not in the sdist either. MANIFEST.in stays an allow-list without it.
        self.assertTrue((ROOT / "build-constraints.txt").is_file(), "control: the file exists")
        self.assertEqual([n for n in sdist_members(self.sdist) if "constraints" in n], [])
        self.assertNotIn("constraints", (ROOT / "MANIFEST.in").read_text())

    def test_buildinfo_names_no_builder(self) -> None:
        m = re.search(r"^BUILDINFO\n(.*?)^END BUILDINFO$", self.log, re.M | re.S)
        self.assertIsNotNone(m, self.log[-2000:])
        assert m is not None
        block = m.group(1)
        keys = [ln.split(":", 1)[0].strip() for ln in block.splitlines()]
        self.assertEqual(keys, ["commit", "source_date_epoch", "python", "platform", "zlib",
                                "deflate_probe", "setuptools", "setuptools_wheel", "build",
                                "constraints"])
        self.assertRegex(block, r"(?m)^  commit: [0-9a-f]{40}$")
        self.assertIn(f"  source_date_epoch: {self.epoch}\n", block)
        self.assertRegex(block, r"(?m)^  deflate_probe: [0-9a-f]{16}$")
        for needle in _needles(self.tmp.name, ROOT, Path.home()):
            self.assertNotIn(needle, block, "no directory of the build machine")
        for who in (getpass.getuser(), socket.gethostname(), socket.gethostname().split(".")[0]):
            if len(who) >= 3:  # the value is never printed, only whether it is there
                self.assertIsNone(re.search(rf"(?<![A-Za-z0-9]){re.escape(who)}(?![A-Za-z0-9])", block, re.I),
                                  "BUILDINFO names the user or the host")
        # control: the same search does find a name that is planted
        self.assertIsNotNone(re.search(r"(?<![A-Za-z0-9])builder(?![A-Za-z0-9])",
                                       block + "  built-by: builder\n", re.I))

    # ---- scripts/verify_release.py: the consumer-side checks, over these artifacts ----

    def _tag(self) -> str:
        """The tag these artifacts would be released under (the script wants plain vX.Y.Z)."""
        m = re.fullmatch(r"tyko84_agent_postbox-(\d+\.\d+\.\d+)-py3-none-any\.whl", self.wheel.name)
        if m is None:
            self.skipTest(f"{self.wheel.name}: not a plain X.Y.Z version; verify_release.py NOT tested")
        return f"v{m.group(1)}"

    def _verify(self, directory: Path, *extra: str, tag: str | None = None,
                env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, "-I", str(VERIFY), tag or self._tag(), "--no-download",
                "--skip-attestation", "--dir", str(directory), *extra]
        return subprocess.run(argv, cwd=self.tmp.name, env=self.env if env is None else env,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)

    def _broken(self, name: str, mutate: Callable[[Path], object], resum: bool = True) -> Path:
        """A copy of the release directory with one thing changed. SHA256SUMS is rewritten for
        the changed files unless `resum` is False, so only the intended line can fail."""
        d = Path(self.tmp.name) / "verify" / name
        shutil.copytree(self.out, d)
        mutate(d)
        if resum:
            (d / "SHA256SUMS").write_bytes("".join(
                f"{hashlib.sha256((d / n).read_bytes()).hexdigest()}  {n}\n"
                for n in sorted((self.wheel.name, self.sdist.name))).encode("ascii"))
        return d

    def _rewrite_sdist(self, path: Path,
                       tweak: Callable[[tarfile.TarInfo, bytes], tuple[tarfile.TarInfo, bytes] | None],
                       extra: list[tuple[tarfile.TarInfo, bytes]] | None = None) -> None:
        """Rewrite the sdist at `path` member by member through `tweak` (None drops a member),
        then append `extra`, with a gzip header like repro_build's (mtime 0, no file name) so
        that only the intended check can fail."""
        members: list[tuple[tarfile.TarInfo, bytes]] = []
        with tarfile.open(path) as src:
            for m in src.getmembers():
                f = src.extractfile(m) if m.isfile() else None
                got = tweak(m, f.read() if f is not None else b"")
                if got is not None:
                    members.append(got)
        buf = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as dst:
            for ti, data in members + (extra or []):
                ti.size = len(data)
                dst.addfile(ti, io.BytesIO(data) if ti.isfile() else None)
        path.write_bytes(buf.getvalue())

    def _rewrite_wheel(self, path: Path, replace: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path) as z:
            items = [(i, z.read(i.filename)) for i in z.infolist()]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as out:
            for info, data in items:
                out.writestr(info, replace.get(info.filename, data))

    def test_verify_release_passes_on_the_release_directory(self) -> None:
        tag = self._tag()
        r = self._verify(self.out)
        self.assertEqual(r.returncode, 0, r.stdout)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], f"release: tyko84/agent-postbox {tag}  (version {tag[1:]})")
        self.assertEqual(lines[-1], f"verified: 11 check(s) passed, 3 skipped (tyko84/agent-postbox {tag})")
        self.assertEqual([ln.split()[1] for ln in lines if ln.startswith("OK")],
                         ["assets", "sums-list", "sha256", "sha256", "wheel-members", "wheel-metadata",
                          "sdist-layout", "sdist-owners", "sdist-mtime", "sdist-gzip", "sdist-metadata"])
        self.assertEqual([ln for ln in lines if ln.startswith("SKIP")],
                         [f"SKIP       attestation     {self.wheel.name}  --skip-attestation",
                          f"SKIP       attestation     {self.sdist.name}  --skip-attestation",
                          "SKIP       rebuild         not requested (--rebuild)"])
        self.assertNotIn("FAIL", r.stdout)
        for name, digest in self._digests().items():
            self.assertIn(f"OK         sha256          {name}  {digest}\n", r.stdout)
        self.assertIn(f"OK         sdist-mtime     one value, {self.epoch}\n", r.stdout)
        self.assertNotIn(self.tmp.name, r.stdout, "no directory is printed")
        self.assertNotIn(os.path.realpath(self.tmp.name), r.stdout)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         sorted(["SHA256SUMS", self.wheel.name, self.sdist.name]), "it wrote nothing")

    def test_verify_release_names_a_tampered_sha256sums_line(self) -> None:
        w, t = self.wheel.name, self.sdist.name
        digests = self._digests()

        def mutate(d: Path) -> None:
            text = (d / "SHA256SUMS").read_text()
            (d / "SHA256SUMS").write_text(text.replace(digests[w], "0" * 64))
        r = self._verify(self._broken("sums", mutate, resum=False))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"FAIL       sha256          {w}  SHA256SUMS says {'0' * 64}, file is {digests[w]}\n",
                      r.stdout)
        self.assertIn(f"OK         sha256          {t}  {digests[t]}\n", r.stdout, "the other line still holds")
        self.assertIn("NOT VERIFIED: 1 of 11 check(s) failed, 3 skipped", r.stdout)
        self.assertNotIn("verified:", r.stdout)
        # a SHA256SUMS that lists a third file, or a malformed line, fails the listing check
        r = self._verify(self._broken("sums-extra", lambda d: (d / "SHA256SUMS").write_text(
            (d / "SHA256SUMS").read_text() + "0" * 64 + "  other-9.9.9.tar.gz\n"), resum=False))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"(?m)^FAIL       sums-list       SHA256SUMS lists \[.*other-9\.9\.9")
        r = self._verify(self._broken("sums-path", lambda d: (d / "SHA256SUMS").write_text(
            (d / "SHA256SUMS").read_text().replace("  ", "  dist/", 1)), resum=False))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL       sums-list       line 1 is not '<sha256>  <filename>'", r.stdout)

    def test_verify_release_names_an_extra_wheel_member(self) -> None:
        w = self.wheel.name

        def mutate(d: Path) -> None:
            with zipfile.ZipFile(d / w, "a") as z:
                z.writestr("extra.txt", b"x")
        r = self._verify(self._broken("wheel-extra", mutate))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL       wheel-members   unexpected 'extra.txt'\n", r.stdout)
        self.assertIn(f"OK         sha256          {w}  ", r.stdout, "SHA256SUMS was rewritten: only the member check fails")
        self.assertIn("NOT VERIFIED: 1 of 11 check(s) failed", r.stdout)
        # a member twice is not hidden by set arithmetic; an absolute name is unexpected

        def twice(d: Path) -> None:
            with zipfile.ZipFile(d / w, "a") as z, warnings.catch_warnings():
                warnings.simplefilter("ignore")   # zipfile warns about the duplicate, on purpose here
                z.writestr("agent_mail.py", b"x")
                z.writestr("/etc/evil", b"x")
        r = self._verify(self._broken("wheel-twice", twice))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"(?m)^FAIL       wheel-members   unexpected '/etc/evil'; a member name appears twice$")
        self.assertIn("OK         wheel-metadata  METADATA: Name tyko84-agent-postbox, Version ", r.stdout)

    def test_verify_release_names_an_sdist_member_with_an_owner_or_another_mtime(self) -> None:
        t = self.sdist.name
        top = t[:-len(".tar.gz")]

        def owned(m: tarfile.TarInfo, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
            if m.name == f"{top}/agent_mail.py":
                m.uid, m.gid = 1000, 1000
            return m, data
        r = self._verify(self._broken("sdist-uid", lambda d: self._rewrite_sdist(d / t, owned)))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL       sdist-owners    uid/gid, user or group name, or PAX record set on "
                      f"{top}/agent_mail.py\n", r.stdout)
        for still in ("sdist-layout", "sdist-mtime", "sdist-gzip", "sdist-metadata"):
            self.assertRegex(r.stdout, rf"(?m)^OK         {still} ", still)
        self.assertIn("NOT VERIFIED: 1 of 11 check(s) failed", r.stdout)

        def named(m: tarfile.TarInfo, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
            if m.name == f"{top}/PKG-INFO":
                m.uname, m.gname = "builder", "builders"
            return m, data
        r = self._verify(self._broken("sdist-uname", lambda d: self._rewrite_sdist(d / t, named)))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"FAIL       sdist-owners    uid/gid, user or group name, or PAX record set on {top}/PKG-INFO\n",
                      r.stdout)
        self.assertNotIn("builder", r.stdout, "the name is never printed")

        def later(m: tarfile.TarInfo, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
            if m.name == f"{top}/README.md":
                m.mtime = self.epoch + 60
            return m, data
        r = self._verify(self._broken("sdist-mtime", lambda d: self._rewrite_sdist(d / t, later)))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"FAIL       sdist-mtime     2 distinct values: [{self.epoch}, {self.epoch + 60}]\n", r.stdout)
        self.assertRegex(r.stdout, r"(?m)^OK         sdist-owners ")
        # the gzip header is checked on its own: tarfile's writer stamps the wall clock
        def plain(d: Path) -> None:
            with tarfile.open(d / t) as src, tarfile.open(d / "re.tar.gz", "w:gz") as dst:
                for m in src.getmembers():
                    dst.addfile(m, src.extractfile(m) if m.isfile() else None)
            os.replace(d / "re.tar.gz", d / t)
        r = self._verify(self._broken("sdist-gzip", plain))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"(?m)^FAIL       sdist-gzip      gzip header mtime is [1-9]\d*, expected 0$")
        self.assertRegex(r.stdout, r"(?m)^OK         sdist-mtime ")

    def test_verify_release_names_an_sdist_member_outside_the_top_directory(self) -> None:
        t = self.sdist.name
        top = t[:-len(".tar.gz")]
        escaping = tarfile.TarInfo(f"{top}/../evil.py")
        other = tarfile.TarInfo("other-1.0/x")
        link = tarfile.TarInfo(f"{top}/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        r = self._verify(self._broken("sdist-escape", lambda d: self._rewrite_sdist(
            d / t, lambda m, data: (m, data), [(escaping, b"x"), (other, b"x"), (link, b"")])))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"FAIL       sdist-layout    outside {top}/: '{top}/../evil.py'; "
                      f"outside {top}/: 'other-1.0/x'; not a regular file or directory: '{top}/link'\n",
                      r.stdout)
        self.assertEqual(sorted(p.name for p in (Path(self.tmp.name) / "verify" / "sdist-escape").iterdir()),
                         sorted(["SHA256SUMS", self.wheel.name, t]), "nothing was extracted")
        self.assertFalse((Path(self.tmp.name) / "evil.py").exists())

    def test_verify_release_names_a_wrong_version(self) -> None:
        w, t = self.wheel.name, self.sdist.name
        tag = self._tag()
        version = tag[1:]
        info = f"{w.split('-py3')[0]}.dist-info/METADATA"
        meta = next(d for n, d in wheel_members(self.wheel).items() if n == info)
        self.assertIn(f"\nVersion: {version}\n", meta.decode())
        r = self._verify(self._broken("wheel-version", lambda d: self._rewrite_wheel(
            d / w, {info: meta.replace(f"Version: {version}".encode(), b"Version: 9.9.9", 1)})))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"FAIL       wheel-metadata  Version is '9.9.9', expected '{version}'\n", r.stdout)
        self.assertIn("OK         wheel-members   ", r.stdout)
        top = t[:-len(".tar.gz")]

        def renamed(m: tarfile.TarInfo, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
            if m.name == f"{top}/PKG-INFO":
                data = data.replace(b"Name: tyko84-agent-postbox", b"Name: agent-postbox", 1)
            return m, data
        r = self._verify(self._broken("sdist-name", lambda d: self._rewrite_sdist(d / t, renamed)))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL       sdist-metadata  Name is 'agent-postbox', expected 'tyko84-agent-postbox'\n",
                      r.stdout)
        # the tag decides the expected names: another tag and nothing matches
        r = self._verify(self.out, tag="v9.9.9")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAIL       assets          missing tyko84_agent_postbox-9.9.9-py3-none-any.whl, "
                      f"tyko84_agent_postbox-9.9.9.tar.gz; unexpected '{w}', '{t}'\n", r.stdout)
        self.assertIn("FAIL       sums-list       SHA256SUMS lists [", r.stdout)
        self.assertIn("FAIL       wheel-members   tyko84_agent_postbox-9.9.9-py3-none-any.whl  file is not there\n",
                      r.stdout)
        self.assertIn("NOT VERIFIED: 11 of 11 check(s) failed, 3 skipped", r.stdout)

    def test_verify_release_usage_and_missing_gh(self) -> None:
        tag = self._tag()
        env = {**self.env, "PATH": ""}   # no gh (and nothing else) on the path
        r = subprocess.run([sys.executable, "-I", str(VERIFY), tag, "--no-download", "--dir", str(self.out)],
                           cwd=self.tmp.name, env=env, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=300)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(f"FAIL       attestation     {self.wheel.name}  gh is not installed", r.stdout)
        self.assertIn(f"FAIL       attestation     {self.sdist.name}  gh is not installed", r.stdout)
        self.assertIn("NOT VERIFIED: 2 of 13 check(s) failed, 1 skipped", r.stdout)
        for argv, msg in (([tag[1:], "--no-download", "--dir", str(self.out)], "the tag must be vX.Y.Z"),
                          ([tag, "--no-download"], "--no-download needs --dir"),
                          ([tag, "--dir", str(self.out)], "the directory is not empty"),
                          ([tag, "--repo", "nope", "--no-download", "--dir", str(self.out)], "--repo must be OWNER/NAME"),
                          ([tag, "--no-download", "--dir", str(Path(self.tmp.name) / "nowhere")], "no such directory")):
            with self.subTest(argv=argv):
                r = subprocess.run([sys.executable, "-I", str(VERIFY), *argv], cwd=self.tmp.name, env=env,
                                   text=True, capture_output=True, timeout=300)
                self.assertEqual((r.returncode, r.stdout), (2, ""), r.stderr)
                self.assertIn(msg, r.stderr)
        # --rebuild without the tag in the checkout, or without `build`, is a FAIL line, not a crash
        r = self._verify(self.out, "--rebuild", tag="v9.9.9")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertRegex(r.stdout, r"(?m)^FAIL       rebuild         (repro_build\.py exited 2: repro_build: git rev-parse|the 'build' package is not installed)")

    # ---- positive controls: the inspectors must be able to fail ----

    def test_control_owner_timestamp_and_path_are_caught(self) -> None:
        base = Path(self.tmp.name)
        needles = _needles(self.tmp.name, ROOT)
        leaky = base / "leaky.tar.gz"
        planted = f"built in {needles[0]}/src".encode()
        with tarfile.open(self.sdist) as src, tarfile.open(leaky, "w:gz") as dst:
            for m in src.getmembers():
                f = src.extractfile(m) if m.isfile() else None
                dst.addfile(m, f)
            ti = tarfile.TarInfo(src.getnames()[0].split("/")[0] + "/docs/built.md")
            ti.size = len(planted)
            ti.uid, ti.gid, ti.uname, ti.gname = 1000, 1000, "builder", "builders"
            ti.mtime = self.epoch + 60
            dst.addfile(ti, io.BytesIO(planted))
        self.assertEqual(sorted(x.split("  ")[1] for x in sdist_owner_leaks(leaky)),
                         ["gid", "gname", "uid", "uname"])
        self.assertNotIn("builder", " ".join(sdist_owner_leaks(leaky)), "the name is never printed")
        self.assertEqual(sdist_mtimes(leaky), {self.epoch, self.epoch + 60})
        flags, mtime = gzip_header(leaky)   # tarfile's own gzip writer stamps the wall clock
        self.assertTrue(flags & 0x08 or mtime != 0)
        hits = build_paths(sdist_members(leaky), needles)
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].endswith("docs/built.md  build-path-1"), hits)
        self.assertNotIn(needles[0], hits[0], "the path is never printed")


if __name__ == "__main__":
    unittest.main()
