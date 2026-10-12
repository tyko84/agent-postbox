#!/usr/bin/env python3
"""Verify a published (or draft) release the way a consumer can.

    python scripts/verify_release.py vX.Y.Z [--repo OWNER/NAME] [--dir DIR]
                                     [--no-download] [--rebuild] [--skip-attestation]

The automated form of the checks listed under "Verifying the published
release" in docs/release-process.md. By default the release assets are
downloaded with `gh release download` into an empty directory (DIR, which must
not exist or must be empty; a temporary directory that is deleted afterwards
when --dir is not given). With --no-download, DIR is verified as it is.

Each check prints one line, `OK`, `FAIL` or `SKIP` followed by a label and the
detail, in the style of `repro_build.py --verify`:

1. assets: exactly the wheel, the sdist and SHA256SUMS are there, named for
   the version the tag carries, and SHA256SUMS lists exactly those two files;
2. sha256: each SHA256SUMS line matches its file;
3. attestation: `gh attestation verify FILE -R REPO` succeeds for the wheel
   and for the sdist (the exit status is all that is read; SKIP with
   --skip-attestation; FAIL with the reason when `gh` is not installed);
4. wheel-members: the wheel holds agent_mail.py and the six dist-info entries,
   nothing else, nothing twice;
5. sdist-layout, sdist-owners, sdist-mtime, sdist-gzip: every sdist member is
   a regular file or directory under `tyko84_agent_postbox-X.Y.Z/`, ownerless
   (uid = gid = 0, no user or group name, no PAX records), with one mtime
   across all members, and the gzip header's mtime field is 0;
6. wheel-metadata, sdist-metadata: METADATA and PKG-INFO say
   `Name: tyko84-agent-postbox` and `Version: X.Y.Z`;
7. rebuild (with --rebuild only): `scripts/repro_build.py --ref vX.Y.Z
   --verify DIR` rebuilds the tag and compares byte for byte (it needs the
   `build` package and a git checkout that has the tag; FAIL with the reason
   otherwise).

The files in DIR are untrusted: nothing from DIR is imported or executed, the
archives are opened read-only and inspected in memory (member names, headers
and the two metadata files, capped at 1 MiB), nothing is extracted to disk.
Only file names and hashes are printed, never a directory. There is no network
access except through `gh`.

Exit status: 0 every check passed (SKIP does not count); 1 at least one FAIL;
2 could not start (tag not vX.Y.Z, DIR not empty, --no-download without
--dir, `gh` missing or the download failed).

Stdlib only, Python 3.10+. POSIX only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPRO = ROOT / "scripts" / "repro_build.py"
DEFAULT_REPO = "tyko84/agent-postbox"
DIST = "tyko84_agent_postbox"           # the file-name form of the distribution name
NAME = "tyko84-agent-postbox"           # the `Name:` metadata field
SUMS_NAME = "SHA256SUMS"
DIST_INFO_FILES = ("METADATA", "RECORD", "WHEEL", "entry_points.txt", "licenses/LICENSE",
                   "top_level.txt")
_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
_SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *]([^/\\\s][^/\\]*)$")   # as repro_build.parse_sums
_META_FIELD = re.compile(r"^(Name|Version): (.*)$", re.M)
METADATA_CAP = 1 << 20                  # the most of a metadata member that is read
GH_TIMEOUT = 600                        # seconds, for each gh call


class UsageError(Exception):
    """Anything that stops the run before the checks; reported as one line, exit 2."""


class Report:
    """The check lines, printed as they come; the counts for the summary."""

    def __init__(self) -> None:
        self.passed = self.failed = self.skipped = 0

    def _line(self, status: str, label: str, detail: str) -> None:
        print(f"{status:<10} {label:<15} {detail}".rstrip())
        sys.stdout.flush()

    def ok(self, label: str, detail: str) -> None:
        self.passed += 1
        self._line("OK", label, detail)

    def fail(self, label: str, detail: str) -> None:
        self.failed += 1
        self._line("FAIL", label, detail)

    def skip(self, label: str, detail: str) -> None:
        self.skipped += 1
        self._line("SKIP", label, detail)

    def result(self, label: str, problems: list[str], detail: str) -> None:
        """OK with `detail` when `problems` is empty, else FAIL naming them."""
        if problems:
            self.fail(label, "; ".join(problems))
        else:
            self.ok(label, detail)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_assets(version: str) -> tuple[str, str]:
    """(wheel, sdist) file names for `version`."""
    return f"{DIST}-{version}-py3-none-any.whl", f"{DIST}-{version}.tar.gz"


def parse_sums(text: str) -> tuple[dict[str, str], list[str]]:
    """({name: hex}, problems): strict `<sha256>  <filename>` lines, as repro_build writes them."""
    out: dict[str, str] = {}
    problems: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = _SUMS_LINE.match(line)
        if m is None:
            problems.append(f"line {n} is not '<sha256>  <filename>'")
        elif m.group(2) in out:
            problems.append(f"line {n} lists a file twice")
        else:
            out[m.group(2)] = m.group(1)
    return out, problems


# ---- the checks: each prints its line(s) through `rep` and never raises ----

def check_assets(rep: Report, directory: Path, wheel: str, sdist: str) -> dict[str, str]:
    """Exactly the three expected files; SHA256SUMS lists exactly the two artifacts.
    Returns the parsed SHA256SUMS ({name: hex}), empty when it could not be read."""
    want = sorted([wheel, sdist, SUMS_NAME])
    try:
        present = sorted(p.name for p in directory.iterdir())
    except OSError as exc:
        rep.fail("assets", f"cannot list the directory: {exc.strerror}")
        return {}
    problems = []
    missing = [n for n in want if n not in present]
    extra = [n for n in present if n not in want]
    if missing:
        problems.append("missing " + ", ".join(missing))
    if extra:
        problems.append("unexpected " + ", ".join(repr(n) for n in extra))
    not_files = [n for n in want if n in present and not (directory / n).is_file()]
    if not_files:
        problems.append("not a regular file: " + ", ".join(not_files))
    rep.result("assets", problems, ", ".join(want))
    listed: dict[str, str] = {}
    sums = directory / SUMS_NAME
    if not sums.is_file():
        rep.fail("sums-list", f"{SUMS_NAME} is not there")
        return {}
    try:
        listed, bad = parse_sums(sums.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        rep.fail("sums-list", f"{SUMS_NAME} is not readable as text")
        return {}
    if sorted(listed) != sorted([wheel, sdist]):
        bad.append(f"{SUMS_NAME} lists {sorted(listed)}, expected the wheel and the sdist only")
    rep.result("sums-list", bad, f"{SUMS_NAME} lists exactly the wheel and the sdist")
    return listed


def check_sums(rep: Report, directory: Path, listed: dict[str, str], names: tuple[str, ...]) -> None:
    for name in names:
        path = directory / name
        if name not in listed:
            rep.fail("sha256", f"{name}  no {SUMS_NAME} line")
        elif not path.is_file():
            rep.fail("sha256", f"{name}  file is not there")
        else:
            have = sha256(path)
            if have == listed[name]:
                rep.ok("sha256", f"{name}  {have}")
            else:
                rep.fail("sha256", f"{name}  {SUMS_NAME} says {listed[name]}, file is {have}")


def check_attestations(rep: Report, directory: Path, repo: str, names: tuple[str, ...],
                       skip: bool) -> None:
    gh = shutil.which("gh")
    for name in names:
        path = directory / name
        if skip:
            rep.skip("attestation", f"{name}  --skip-attestation")
            continue
        if gh is None:
            rep.fail("attestation", f"{name}  gh is not installed (https://cli.github.com)")
            continue
        if not path.is_file():
            rep.fail("attestation", f"{name}  file is not there")
            continue
        try:
            r = subprocess.run([gh, "attestation", "verify", str(path), "-R", repo],
                               text=True, capture_output=True, timeout=GH_TIMEOUT)
        except subprocess.TimeoutExpired:
            rep.fail("attestation", f"{name}  gh attestation verify did not finish in {GH_TIMEOUT}s")
            continue
        if r.returncode == 0:
            rep.ok("attestation", f"{name}  gh attestation verify -R {repo}")
        else:
            why = " ".join((r.stderr or r.stdout).split())[-300:] or f"exit {r.returncode}"
            rep.fail("attestation", f"{name}  gh attestation verify failed: {why}")


def check_wheel(rep: Report, path: Path, version: str) -> None:
    """Member names (exact list, so nothing twice) and METADATA."""
    info = f"{DIST}-{version}.dist-info"
    want = sorted(["agent_mail.py", *(f"{info}/{f}" for f in DIST_INFO_FILES)])
    if not path.is_file():
        rep.fail("wheel-members", f"{path.name}  file is not there")
        rep.fail("wheel-metadata", f"{path.name}  file is not there")
        return
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(z.namelist())
            problems = []
            missing = [n for n in want if n not in names]
            extra = [n for n in names if n not in want]
            if missing:
                problems.append("missing " + ", ".join(missing))
            if extra:
                problems.append("unexpected " + ", ".join(repr(n) for n in extra))
            if len(names) != len(set(names)):
                problems.append("a member name appears twice")
            rep.result("wheel-members", problems,
                       f"{path.name}  agent_mail.py + {info}/ ({len(DIST_INFO_FILES)} files)")
            meta_name = f"{info}/METADATA"
            if meta_name in names:
                with z.open(meta_name) as fh:
                    data = fh.read(METADATA_CAP)
                check_metadata(rep, "wheel-metadata", "METADATA", data, version)
            else:
                rep.fail("wheel-metadata", f"{meta_name} is not in the wheel")
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as exc:
        rep.fail("wheel-members", f"{path.name}  cannot be read as a zip: {exc}")
        rep.fail("wheel-metadata", f"{path.name}  cannot be read as a zip")


def check_metadata(rep: Report, label: str, what: str, data: bytes, version: str) -> None:
    header = data.split(b"\n\n", 1)[0].decode("utf-8", "replace")
    fields = {k: v.strip() for k, v in _META_FIELD.findall(header)}
    problems = []
    if fields.get("Name") != NAME:
        problems.append(f"Name is {fields.get('Name')!r}, expected {NAME!r}")
    if fields.get("Version") != version:
        problems.append(f"Version is {fields.get('Version')!r}, expected {version!r}")
    rep.result(label, problems, f"{what}: Name {NAME}, Version {version}")


def gzip_mtime(path: Path) -> int:
    """The mtime field of the gzip header (tarfile has already accepted the file as gzip)."""
    with open(path, "rb") as fh:
        return int.from_bytes(fh.read(10)[4:8], "little")


def check_sdist(rep: Report, path: Path, version: str) -> None:
    """Layout, owners, mtimes, gzip header and PKG-INFO, from the headers alone."""
    top = f"{DIST}-{version}"
    labels = ("sdist-layout", "sdist-owners", "sdist-mtime", "sdist-gzip", "sdist-metadata")
    if not path.is_file():
        for label in labels:
            rep.fail(label, f"{path.name}  file is not there")
        return
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = tar.getmembers()
            layout: list[str] = []
            owners: list[str] = []
            if not members:
                layout.append("the archive is empty")
            for m in members:
                parts = m.name.split("/")
                if (m.name != top and not m.name.startswith(top + "/")) or m.name.startswith("/") \
                        or any(p in ("", ".", "..") for p in parts) or "\\" in m.name:
                    layout.append(f"outside {top}/: {m.name!r}")
                elif not (m.isfile() or m.isdir()):
                    layout.append(f"not a regular file or directory: {m.name!r}")
                if m.uid != 0 or m.gid != 0 or m.uname != "" or m.gname != "" or m.pax_headers:
                    owners.append(m.name)   # the name only; a user name is never printed
            names = [m.name for m in members]
            if len(names) != len(set(names)):
                layout.append("a member name appears twice")
            rep.result("sdist-layout", layout[:5],
                       f"{path.name}  {len(members)} members, all under {top}/")
            rep.result("sdist-owners", ["uid/gid, user or group name, or PAX record set on "
                                        + ", ".join(owners[:5])] if owners else [],
                       "uid = gid = 0, no user or group name, no PAX records")
            mtimes = sorted({int(m.mtime) for m in members})
            rep.result("sdist-mtime", [f"{len(mtimes)} distinct values: {mtimes[:5]}"]
                       if len(mtimes) != 1 else [], f"one value, {mtimes[0] if mtimes else '-'}")
            mtime = gzip_mtime(path)
            rep.result("sdist-gzip", [f"gzip header mtime is {mtime}, expected 0"]
                       if mtime != 0 else [], f"{path.name}  gzip header mtime 0")
            pkg = f"{top}/PKG-INFO"
            found = next((m for m in members if m.name == pkg and m.isfile()), None)
            if found is None:
                rep.fail("sdist-metadata", f"{pkg} is not in the sdist")
            else:
                fh = tar.extractfile(found)
                data = fh.read(METADATA_CAP) if fh is not None else b""
                check_metadata(rep, "sdist-metadata", "PKG-INFO", data, version)
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        for label in labels:
            rep.fail(label, f"{path.name}  cannot be read as a tar.gz: {exc}")


def check_rebuild(rep: Report, directory: Path, tag: str) -> None:
    """scripts/repro_build.py --ref TAG --verify DIR, as a subprocess; its result relayed."""
    if not REPRO.is_file():
        rep.fail("rebuild", "scripts/repro_build.py is not next to this script")
        return
    if importlib.util.find_spec("build") is None:
        rep.fail("rebuild", "the 'build' package is not installed for this interpreter "
                            "(python -m pip install --require-hashes -r requirements-build.txt)")
        return
    r = subprocess.run([sys.executable, "-I", str(REPRO), "--ref", tag, "--verify", str(directory)],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=ROOT)
    for line in r.stdout.splitlines():
        print(f"  | {line}")
    m = re.search(r"^verified: identical to a rebuild of ([0-9a-f]{40})$", r.stdout, re.M)
    if r.returncode == 0 and m:
        rep.ok("rebuild", f"identical to a rebuild of {m.group(1)} (repro_build.py --ref {tag})")
    elif r.returncode == 1:
        rep.fail("rebuild", "differs from a rebuild of the tag (see the DIFFERENT lines above)")
    else:
        last = r.stdout.strip().splitlines()
        rep.fail("rebuild", f"repro_build.py exited {r.returncode}: {last[-1] if last else ''}")


# ---- download and main ----

def download(tag: str, repo: str, directory: Path) -> None:
    gh = shutil.which("gh")
    if gh is None:
        raise UsageError("gh is not installed (https://cli.github.com); "
                         "or download the assets yourself and pass --dir DIR --no-download")
    try:
        r = subprocess.run([gh, "release", "download", tag, "-R", repo, "-D", str(directory)],
                           text=True, capture_output=True, timeout=GH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise UsageError(f"gh release download did not finish in {GH_TIMEOUT}s") from None
    if r.returncode != 0:
        why = " ".join((r.stderr or r.stdout).split())[-500:]
        raise UsageError(f"gh release download {tag} -R {repo} failed: {why}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify a release's assets as a consumer can (docs/release-process.md, "
                    "'Verifying the published release').")
    ap.add_argument("tag", help="the release tag, vX.Y.Z")
    ap.add_argument("--repo", default=DEFAULT_REPO, metavar="OWNER/NAME",
                    help=f"the GitHub repository (default: {DEFAULT_REPO})")
    ap.add_argument("--dir", default=None, metavar="DIR",
                    help="where the assets go (must not exist or be empty; default: a temporary "
                         "directory that is deleted afterwards), or where they are with --no-download")
    ap.add_argument("--no-download", action="store_true",
                    help="verify the files already in --dir DIR instead of downloading")
    ap.add_argument("--rebuild", action="store_true",
                    help="also rebuild the tag with scripts/repro_build.py and compare byte for byte "
                         "(needs the 'build' package and a checkout that has the tag)")
    ap.add_argument("--skip-attestation", action="store_true",
                    help="do not run gh attestation verify (reported as SKIP)")
    args = ap.parse_args(argv)
    m = _TAG.match(args.tag)
    if m is None:
        print(f"verify_release: the tag must be vX.Y.Z, got {args.tag!r}", file=sys.stderr)
        return 2
    version = m.group(1)
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", args.repo):
        print(f"verify_release: --repo must be OWNER/NAME, got {args.repo!r}", file=sys.stderr)
        return 2
    if args.no_download and args.dir is None:
        print("verify_release: --no-download needs --dir DIR", file=sys.stderr)
        return 2
    wheel, sdist = expected_assets(version)
    rep = Report()
    tmp: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.dir is not None:
            directory = Path(args.dir)
            if args.no_download:
                if not directory.is_dir():
                    raise UsageError("--dir: no such directory")
            elif directory.exists():
                if not directory.is_dir():
                    raise UsageError("--dir: not a directory")
                if any(directory.iterdir()):
                    raise UsageError("--dir: the directory is not empty (verify what is there "
                                     "with --no-download, or name an empty one)")
            else:
                directory.mkdir(parents=True)
        else:
            tmp = tempfile.TemporaryDirectory(prefix="verify-release-")
            directory = Path(tmp.name)
        print(f"release: {args.repo} {args.tag}  (version {version})")
        if not args.no_download:
            download(args.tag, args.repo, directory)
            print(f"downloaded: gh release download {args.tag} -R {args.repo}")
        listed = check_assets(rep, directory, wheel, sdist)
        check_sums(rep, directory, listed, (wheel, sdist))
        check_attestations(rep, directory, args.repo, (wheel, sdist), args.skip_attestation)
        check_wheel(rep, directory / wheel, version)
        check_sdist(rep, directory / sdist, version)
        if args.rebuild:
            check_rebuild(rep, directory, args.tag)
        else:
            rep.skip("rebuild", "not requested (--rebuild)")
    except UsageError as exc:
        print(f"verify_release: {exc}", file=sys.stderr)
        return 2
    finally:
        if tmp is not None:
            tmp.cleanup()
    total = rep.passed + rep.failed
    tail = f" ({args.repo} {args.tag})"
    if rep.failed:
        print(f"NOT VERIFIED: {rep.failed} of {total} check(s) failed, {rep.skipped} skipped{tail}",
              file=sys.stderr)
        return 1
    print(f"verified: {rep.passed} check(s) passed, {rep.skipped} skipped{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
