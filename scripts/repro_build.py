#!/usr/bin/env python3
"""Reproducible, ownerless build of the wheel and the sdist.

    python scripts/repro_build.py [--outdir DIR] [--ref REF] [--check]
                                  [--sums FILE] [--verify DIR] [--constraints FILE]

What it does, and why each step exists:

1. SOURCE_DATE_EPOCH is taken from the environment or, when unset, derived
   from the committer timestamp of the commit being built (`git log -1
   --format=%ct`). Every timestamp in both artifacts becomes that instant.
2. The tree is exported with `git archive` into a temporary directory and
   built THERE. Untracked and ignored files cannot ship, the checkout is not
   touched, and file modes are set explicitly (0644, or 0755 for files git
   tracks as executable) so the builder's umask plays no part.
3. `python -m build` runs in that export with a fixed umask (022). The build
   backend is pinned by the `build-constraints.txt` OF THE COMMIT BEING BUILT
   (one exact setuptools version), handed to `build` as
   `--dependency-constraints-txt`, so the toolchain that writes the bytes
   is named by a file in the repository and not by the day of the build.
   `[build-system] requires` in pyproject.toml is untouched: it stays a floor
   for people building from a clone. A commit without that file is built
   unpinned, and the output says so.
4. The wheel needs nothing more: with SOURCE_DATE_EPOCH set, setuptools writes
   it byte-for-byte reproducibly. The sdist does not come out reproducible, and
   it records WHO built it: setuptools stamps wall-clock mtimes on the members
   it generates, the tar headers carry the builder's numeric uid/gid and login
   and group NAMES, and the gzip header carries a timestamp and a file name.
   So the sdist is rewritten: members sorted by name, mtime = SOURCE_DATE_EPOCH,
   uid = gid = 0, uname = gname = "", mode 0644 (0755 for directories and
   executables), no PAX records, and a gzip stream with mtime 0, no file name
   and a fixed header. The rewrite is refused unless the member names and
   contents are exactly those of the sdist setuptools produced.
5. The SHA-256 of both artifacts is printed, followed by a BUILDINFO block
   naming everything the bytes depend on (Python, zlib, setuptools, `build`,
   the constraints file), so two environments can be compared line by line.
   It names no user, host or directory.
6. `SHA256SUMS` is written next to the artifacts (or to `--sums FILE`) in the
   format `sha256sum -c` reads: `<hex>  <filename>`, sorted by file name, LF
   line ends, no directories. It is as reproducible as the artifacts.
7. `--check` builds twice, in two separate temporary directories, and exits 1
   if the two runs disagree.
8. `--verify DIR` rebuilds `--ref` and compares the result with what is in
   DIR: the wheel and sdist found there, byte for byte, and `SHA256SUMS` if
   it is there. It exits 0 only if both artifacts are accounted for (by a
   file or by a SHA256SUMS line) and everything found is identical; otherwise
   it names each file that differs, with both hashes, and exits 1. It writes
   nothing unless --outdir or --sums is given.

Exit status: 0 built (and, with --check, reproduced; with --verify, identical);
1 --check or --verify found a difference; 2 could not build (not a git
checkout, `build` missing or too old, no network for the build backend,
unexpected archive member, --verify DIR missing).

Stdlib only, plus the `build` package it drives. POSIX only. See
docs/packaging.md for what is and is not guaranteed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import io
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_UMASK = 0o022
# 10 fixed bytes: magic, deflate, no flags (so no file name), mtime 0, XFL 2
# ("maximum compression"), OS 255 ("unknown").
GZIP_HEADER = b"\x1f\x8b\x08\x00" + struct.pack("<I", 0) + b"\x02\xff"
CONSTRAINTS_NAME = "build-constraints.txt"   # read from the commit being built
DEV_REQUIREMENTS = "requirements-dev.txt"    # names the `build` front end the project pins
SUMS_NAME = "SHA256SUMS"
_SUMS_LINE = re.compile(r"^([0-9a-f]{64}) [ *]([^/\\\s][^/\\]*)$")


class BuildError(Exception):
    """Anything that stops a build; reported as one line, exit 2."""


def git(*argv: str) -> bytes:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *argv], capture_output=True)
    except FileNotFoundError:
        raise BuildError("git is not installed") from None
    if r.returncode != 0:
        raise BuildError(f"git {' '.join(argv)} failed: "
                         f"{r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout


def source_date_epoch(ref: str) -> int:
    """SOURCE_DATE_EPOCH from the environment, else the commit's committer time."""
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if not raw:
        raw = git("log", "-1", "--format=%ct", ref).decode().strip()
    try:
        epoch = int(raw)
    except ValueError:
        raise BuildError(f"SOURCE_DATE_EPOCH must be an integer, got {raw!r}") from None
    if epoch < 315532800:  # 1980-01-01: zip cannot represent anything earlier
        raise BuildError("SOURCE_DATE_EPOCH is before 1980-01-01 (zip cannot store it)")
    return epoch


def export_tree(ref: str, dest: Path, epoch: int) -> None:
    """`git archive REF` unpacked into `dest`: tracked files only, explicit modes."""
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", ref))) as tar:
        for m in tar:
            target = dest / m.name
            if not target.resolve().is_relative_to(dest.resolve()):
                raise BuildError(f"archive member escapes the export: {m.name!r}")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not m.isfile():
                raise BuildError(f"tracked path is not a regular file: {m.name!r}")
            fh = tar.extractfile(m)
            assert fh is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fh.read())
            target.chmod(0o755 if m.mode & 0o111 else 0o644)
            os.utime(target, (epoch, epoch))
    for d in [dest, *(p for p in dest.rglob("*") if p.is_dir())]:
        d.chmod(0o755)


def read_sdist(path: Path) -> list[tuple[tarfile.TarInfo, bytes]]:
    out: list[tuple[tarfile.TarInfo, bytes]] = []
    with tarfile.open(path, "r:gz") as tar:
        for m in tar:
            if m.isdir():
                out.append((m, b""))
            elif m.isfile():
                fh = tar.extractfile(m)
                assert fh is not None
                out.append((m, fh.read()))
            else:
                raise BuildError(f"sdist member is not a regular file or directory: {m.name!r}")
    return out


def normalize_sdist(path: Path, epoch: int) -> None:
    """Rewrite the sdist at `path` in place, deterministically and ownerless."""
    before = read_sdist(path)
    names = [m.name for m, _ in before]
    if len(set(names)) != len(names):
        raise BuildError("sdist has duplicate member names")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for old, data in sorted(before, key=lambda pair: pair[0].name):
            ti = tarfile.TarInfo(old.name)
            ti.type = tarfile.DIRTYPE if old.isdir() else tarfile.REGTYPE
            ti.mode = 0o755 if old.isdir() or old.mode & 0o111 else 0o644
            ti.mtime = epoch
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.size = len(data)
            ti.pax_headers = {}
            tar.addfile(ti, None if old.isdir() else io.BytesIO(data))
    tar_bytes = raw.getvalue()
    deflate = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    blob = (GZIP_HEADER + deflate.compress(tar_bytes) + deflate.flush()
            + struct.pack("<II", zlib.crc32(tar_bytes), len(tar_bytes) & 0xFFFFFFFF))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    after = read_sdist(tmp)
    if sorted((m.name, d) for m, d in before) != [(m.name, d) for m, d in after]:
        tmp.unlink()
        raise BuildError("normalised sdist does not match the built one; refusing to use it")
    os.replace(tmp, path)
    path.chmod(0o644)
    os.utime(path, (epoch, epoch))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# Environment settings that would add to or replace the commit's constraints.
_AMBIENT_CONSTRAINTS = ("PIP_CONSTRAINT", "PIP_BUILD_CONSTRAINT", "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT")


def build_once(ref: str, epoch: int, work: Path,
               constraints: Path | None = None) -> tuple[Path, Path, bytes | None]:
    """Export, build and normalise inside `work`.

    Returns (wheel, sdist, constraints bytes used or None when unpinned).
    `constraints` overrides the commit's own build-constraints.txt.
    """
    src, out = work / "src", work / "out"
    src.mkdir()
    export_tree(ref, src, epoch)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    cmd = [sys.executable, "-m", "build", "--outdir", str(out)]
    pinned: bytes | None = None
    cfile = constraints if constraints is not None else src / CONSTRAINTS_NAME
    if cfile.is_file():
        pinned = cfile.read_bytes()
        used = work / "constraints.txt"   # a private copy: the build cannot alter what was hashed
        used.write_bytes(pinned)
        cmd += ["--dependency-constraints-txt", str(used)]
        for name in _AMBIENT_CONSTRAINTS:
            env.pop(name, None)
    old_umask = os.umask(BUILD_UMASK)
    try:
        r = subprocess.run([*cmd, str(src)],
                           cwd=work, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    finally:
        os.umask(old_umask)
    if r.returncode != 0:
        raise BuildError("python -m build failed (it needs network access to fetch the "
                         f"build backend):\n{r.stdout[-3000:]}")
    wheels, sdists = sorted(out.glob("*.whl")), sorted(out.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise BuildError(f"expected one wheel and one sdist, got {sorted(p.name for p in out.iterdir())}")
    normalize_sdist(sdists[0], epoch)
    wheels[0].chmod(0o644)
    os.utime(wheels[0], (epoch, epoch))
    return wheels[0], sdists[0], pinned


def generator(wheel: Path) -> str:
    """The `Generator:` line of the wheel's WHEEL file (names the setuptools version)."""
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.endswith(".dist-info/WHEEL"):
                for line in z.read(name).decode("utf-8", "replace").splitlines():
                    if line.startswith("Generator:"):
                        return line.split(":", 1)[1].strip()
    return "unknown"


def sums_text(digests: dict[str, str]) -> str:
    """SHA256SUMS content: `<hex>  <name>` per file, sorted by name, LF ends."""
    return "".join(f"{digests[name]}  {name}\n" for name in sorted(digests))


def write_sums(path: Path, digests: dict[str, str], epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sums_text(digests).encode("ascii"))
    path.chmod(0o644)
    os.utime(path, (epoch, epoch))


def parse_sums(text: str) -> tuple[dict[str, str], list[str]]:
    """({name: hex}, problems) from SHA256SUMS text. Strict: lower-case hex, a
    bare file name (no directory), no duplicates. CRLF is tolerated on input."""
    out: dict[str, str] = {}
    problems: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = _SUMS_LINE.match(line)
        if m is None:
            problems.append(f"MALFORMED  {SUMS_NAME} line {n}: not '<sha256>  <filename>'")
        elif m.group(2) in out:
            problems.append(f"MALFORMED  {SUMS_NAME} line {n}: file listed twice")
        else:
            out[m.group(2)] = m.group(1)
    return out, problems


def verify_dir(directory: Path, built: dict[str, str]) -> tuple[list[str], list[str]]:
    """Compare `directory` with a rebuild. `built` is {artifact name: sha256}.

    Returns (lines for what matched, lines for what did not). Only file names
    and hashes appear in either; never a directory.
    """
    good: list[str] = []
    bad: list[str] = []
    listed: dict[str, str] = {}
    sums = directory / SUMS_NAME
    if sums.is_file():
        try:
            listed, bad = parse_sums(sums.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            bad.append(f"MALFORMED  {SUMS_NAME}: not readable as text")
    for name in sorted(built):
        found = directory / name
        covered = False
        if found.is_file():
            covered = True
            have = sha256(found)
            if have == built[name]:
                good.append(f"OK         {name}  {have}")
            else:
                bad.append(f"DIFFERENT  {name}\n  rebuilt  {built[name]}\n  found    {have}")
        if name in listed:
            covered = True
            if listed[name] == built[name]:
                good.append(f"OK         {name}  (its {SUMS_NAME} line)")
            else:
                bad.append(f"DIFFERENT  {name} (its {SUMS_NAME} line)\n  rebuilt  {built[name]}\n"
                           f"  listed   {listed[name]}")
        if not covered:
            bad.append(f"MISSING    {name}: neither the file nor a {SUMS_NAME} line is there")
    for name in sorted(set(listed) - set(built)):
        bad.append(f"UNEXPECTED {name}: listed in {SUMS_NAME}, not produced by this commit")
    try:
        present = sorted(p.name for p in directory.iterdir())
    except OSError:
        present = []
    for name in present:
        if name.endswith((".whl", ".tar.gz")) and name not in built:
            bad.append(f"UNEXPECTED {name}: not produced by this commit")
    return good, bad


def deflate_probe() -> str:
    """A short fingerprint of this interpreter's deflate: the hash of a fixed
    input compressed at the two settings the artifacts use (level 9 raw for the
    sdist, the zipfile default for the wheel). Two environments that print the
    same value compress alike, whatever their zlib calls itself."""
    data = b"".join(hashlib.sha256(str(n // 7).encode()).hexdigest().encode() + b"\n" + b"x" * (n % 61)
                    for n in range(3000))
    nine = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    six = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
    return hashlib.sha256(nine.compress(data) + nine.flush()
                          + six.compress(data) + six.flush()).hexdigest()[:16]


def pinned_build_version(commit: str) -> str:
    """The `build==X` pin in the commit's requirements-dev.txt, or ""."""
    try:
        text = git("show", f"{commit}:{DEV_REQUIREMENTS}").decode("utf-8", "replace")
    except BuildError:
        return ""
    m = re.search(r"^build==([^\s;#]+)", text, re.M)
    return m.group(1) if m else ""


def buildinfo(commit: str, epoch: int, wheel: Path, pinned: bytes | None, override: bool) -> list[str]:
    """Everything the artifact bytes depend on. No user, host or directory."""
    try:
        front = importlib.metadata.version("build")
    except importlib.metadata.PackageNotFoundError:
        front = "unknown"
    want = pinned_build_version(commit)
    gen = re.search(r"\(([^)]+)\)", generator(wheel))
    if pinned is None:
        cons = f"none (this commit has no {CONSTRAINTS_NAME}: setuptools is NOT pinned)"
    else:
        cons = (("--constraints override" if override else CONSTRAINTS_NAME)
                + f" sha256 {hashlib.sha256(pinned).hexdigest()}")
    zng = getattr(zlib, "ZLIBNG_VERSION", "")
    return [
        "BUILDINFO",
        f"  commit: {commit}",
        f"  source_date_epoch: {epoch}",
        f"  python: {platform.python_implementation()} {platform.python_version()}",
        f"  platform: {sys.platform} {platform.machine()}",
        f"  zlib: {zlib.ZLIB_RUNTIME_VERSION} (built against {zlib.ZLIB_VERSION})"
        + (f" zlib-ng {zng}" if zng else ""),
        f"  deflate_probe: {deflate_probe()}",
        f"  setuptools: {gen.group(1) if gen else 'unknown'}",
        f"  build: {front}" + (f" ({DEV_REQUIREMENTS} pins {want})" if want else ""),
        f"  constraints: {cons}",
        "END BUILDINFO",
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reproducible, ownerless wheel + sdist from a git commit (see docs/packaging.md).")
    ap.add_argument("--outdir", default=None,
                    help="where to put the two artifacts and SHA256SUMS (default: dist, or "
                         "nowhere with --verify; created if missing)")
    ap.add_argument("--ref", default="HEAD", help="commit to build (default: HEAD)")
    ap.add_argument("--check", action="store_true",
                    help="build twice in separate temporary directories; exit 1 if the hashes differ")
    ap.add_argument("--sums", default=None, metavar="FILE",
                    help="write SHA256SUMS here instead of next to the artifacts")
    ap.add_argument("--verify", default=None, metavar="DIR",
                    help="rebuild --ref and compare with the wheel, sdist and SHA256SUMS in DIR; "
                         "exit 1 and name the file if anything differs")
    ap.add_argument("--constraints", default=None, metavar="FILE",
                    help=f"pin the build backend with FILE instead of the commit's own {CONSTRAINTS_NAME} "
                         "(for trying a new pin; the hashes are then not the commit's)")
    args = ap.parse_args(argv)
    try:
        if importlib.util.find_spec("build") is None:
            raise BuildError("the 'build' package is not installed for this interpreter "
                             "(python -m pip install -r requirements-dev.txt)")
        probe = subprocess.run([sys.executable, "-m", "build", "--help"], capture_output=True, text=True)
        if "--dependency-constraints-txt" not in probe.stdout:
            raise BuildError("this 'build' is too old to pin the build backend "
                             "(python -m pip install -r requirements-dev.txt)")
        override: Path | None = None
        if args.constraints is not None:
            override = Path(args.constraints).resolve()
            if not override.is_file():
                raise BuildError("--constraints: no such file")
        verify: Path | None = None
        if args.verify is not None:
            verify = Path(args.verify)
            if not verify.is_dir():
                raise BuildError("--verify: no such directory")
        commit = git("rev-parse", "--verify", f"{args.ref}^{{commit}}").decode().strip()
        epoch = source_date_epoch(commit)
        if git("status", "--porcelain").strip():
            print("note: the working tree has uncommitted changes; they are NOT in this build "
                  f"(building commit {commit[:12]})", file=sys.stderr)
        runs: list[tuple[Path, Path, bytes | None]] = []
        with tempfile.TemporaryDirectory(prefix="repro-build-") as tmp:
            for n in range(2 if args.check else 1):
                work = Path(tmp) / f"run{n + 1}"
                work.mkdir()
                runs.append(build_once(commit, epoch, work, override))
            hashes = [(sha256(w), sha256(s)) for w, s, _ in runs]
            wheel, sdist, pinned = runs[0]
            digests = {wheel.name: hashes[0][0], sdist.name: hashes[0][1]}
            print(f"commit: {commit}")
            print(f"SOURCE_DATE_EPOCH: {epoch}")
            print(f"python: {sys.version.split()[0]}  zlib: {zlib.ZLIB_RUNTIME_VERSION}  "
                  f"generator: {generator(wheel)}")
            for path, digest in zip((wheel, sdist), hashes[0], strict=True):
                print(f"{digest}  {path.name}")
            print("\n".join(buildinfo(commit, epoch, wheel, pinned, override is not None)))
            if override is not None:
                print(f"note: built with --constraints, not the commit's {CONSTRAINTS_NAME}; "
                      "these are not the commit's reference hashes", file=sys.stderr)
            elif pinned is None:
                print(f"note: commit {commit[:12]} has no {CONSTRAINTS_NAME}; setuptools is not pinned "
                      "and the hashes depend on the day of the build", file=sys.stderr)
            if args.check:
                if hashes[0] != hashes[1]:
                    for path, a, b in zip((wheel, sdist), hashes[0], hashes[1], strict=True):
                        if a != b:
                            print(f"NOT REPRODUCIBLE: {path.name}\n  run 1 {a}\n  run 2 {b}",
                                  file=sys.stderr)
                    return 1
                print("reproducible: two independent builds are byte-identical")
            outdir: Path | None = None
            if args.outdir is not None or verify is None:
                outdir = Path(args.outdir if args.outdir is not None else "dist")
                outdir.mkdir(parents=True, exist_ok=True)
                for path in (wheel, sdist):
                    shutil.copy2(path, outdir / path.name)
                print(f"wrote {outdir / wheel.name}\nwrote {outdir / sdist.name}")
            sums = Path(args.sums) if args.sums is not None else (outdir / SUMS_NAME if outdir else None)
            if sums is not None:
                write_sums(sums, digests, epoch)
                print(f"wrote {sums}")
            if verify is not None:
                good, bad = verify_dir(verify, digests)
                print("\n".join(good + bad))
                if bad:
                    print(f"NOT VERIFIED: {len(bad)} difference(s) from a rebuild of {commit}",
                          file=sys.stderr)
                    return 1
                print(f"verified: identical to a rebuild of {commit}")
    except BuildError as exc:
        print(f"repro_build: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
