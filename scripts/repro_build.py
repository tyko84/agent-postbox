#!/usr/bin/env python3
"""Reproducible, ownerless build of the wheel and the sdist.

    python scripts/repro_build.py [--outdir DIR] [--ref REF] [--check]

What it does, and why each step exists:

1. SOURCE_DATE_EPOCH is taken from the environment or, when unset, derived
   from the committer timestamp of the commit being built (`git log -1
   --format=%ct`). Every timestamp in both artifacts becomes that instant.
2. The tree is exported with `git archive` into a temporary directory and
   built THERE. Untracked and ignored files cannot ship, the checkout is not
   touched, and file modes are set explicitly (0644, or 0755 for files git
   tracks as executable) so the builder's umask plays no part.
3. `python -m build` runs in that export with a fixed umask (022).
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
5. The SHA-256 of both artifacts is printed, with the tool versions the bytes
   depend on.
6. `--check` builds twice, in two separate temporary directories, and exits 1
   if the two runs disagree.

Exit status: 0 built (and, with --check, reproduced); 1 --check found a
difference; 2 could not build (not a git checkout, `build` missing, no network
for the build backend, unexpected archive member).

Stdlib only, plus the `build` package it drives. POSIX only. See
docs/packaging.md for what is and is not guaranteed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import os
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
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_once(ref: str, epoch: int, work: Path) -> tuple[Path, Path]:
    """Export, build and normalise inside `work`; return (wheel, sdist)."""
    src, out = work / "src", work / "out"
    src.mkdir()
    export_tree(ref, src, epoch)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    old_umask = os.umask(BUILD_UMASK)
    try:
        r = subprocess.run([sys.executable, "-m", "build", "--outdir", str(out), str(src)],
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
    return wheels[0], sdists[0]


def generator(wheel: Path) -> str:
    """The `Generator:` line of the wheel's WHEEL file (names the setuptools version)."""
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.endswith(".dist-info/WHEEL"):
                for line in z.read(name).decode("utf-8", "replace").splitlines():
                    if line.startswith("Generator:"):
                        return line.split(":", 1)[1].strip()
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reproducible, ownerless wheel + sdist from a git commit (see docs/packaging.md).")
    ap.add_argument("--outdir", default="dist",
                    help="where to put the two artifacts (default: dist; created if missing)")
    ap.add_argument("--ref", default="HEAD", help="commit to build (default: HEAD)")
    ap.add_argument("--check", action="store_true",
                    help="build twice in separate temporary directories; exit 1 if the hashes differ")
    args = ap.parse_args(argv)
    try:
        if importlib.util.find_spec("build") is None:
            raise BuildError("the 'build' package is not installed for this interpreter "
                             "(python -m pip install -r requirements-dev.txt)")
        commit = git("rev-parse", "--verify", f"{args.ref}^{{commit}}").decode().strip()
        epoch = source_date_epoch(commit)
        if git("status", "--porcelain").strip():
            print("note: the working tree has uncommitted changes; they are NOT in this build "
                  f"(building commit {commit[:12]})", file=sys.stderr)
        runs: list[tuple[Path, Path]] = []
        with tempfile.TemporaryDirectory(prefix="repro-build-") as tmp:
            for n in range(2 if args.check else 1):
                work = Path(tmp) / f"run{n + 1}"
                work.mkdir()
                runs.append(build_once(commit, epoch, work))
            hashes = [(sha256(w), sha256(s)) for w, s in runs]
            wheel, sdist = runs[0]
            print(f"commit: {commit}")
            print(f"SOURCE_DATE_EPOCH: {epoch}")
            print(f"python: {sys.version.split()[0]}  zlib: {zlib.ZLIB_RUNTIME_VERSION}  "
                  f"generator: {generator(wheel)}")
            for path, digest in zip((wheel, sdist), hashes[0], strict=True):
                print(f"{digest}  {path.name}")
            if args.check:
                if hashes[0] != hashes[1]:
                    for path, a, b in zip((wheel, sdist), hashes[0], hashes[1], strict=True):
                        if a != b:
                            print(f"NOT REPRODUCIBLE: {path.name}\n  run 1 {a}\n  run 2 {b}",
                                  file=sys.stderr)
                    return 1
                print("reproducible: two independent builds are byte-identical")
            outdir = Path(args.outdir)
            outdir.mkdir(parents=True, exist_ok=True)
            for path in (wheel, sdist):
                shutil.copy2(path, outdir / path.name)
            print(f"wrote {outdir / wheel.name}\nwrote {outdir / sdist.name}")
    except BuildError as exc:
        print(f"repro_build: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
