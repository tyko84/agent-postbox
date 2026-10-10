# Packaging

`pyproject.toml` is the only packaging config. The version has a single
source, `__version__` in `agent_mail.py`; setuptools reads it statically.
The wheel contains `agent_mail.py` and metadata only. The sdist is an explicit
allow-list in `MANIFEST.in`; anything not listed does not ship (apart from the
metadata setuptools itself generates: `PKG-INFO`, `setup.cfg` and
`agent_postbox.egg-info/`).

## This project is not the PyPI distribution of the same name

This project is **not published on PyPI**. The PyPI distribution named
[`agent-postbox`](https://pypi.org/project/agent-postbox/) is a separate,
unrelated project by a different author, so `pip install agent-postbox`
installs that project, not this one. Always install from a clone
(`python -m pip install ./agent-postbox`) or from a wheel built from a clone, as
below. Nothing in this repository uploads anything to PyPI: that is a maintainer
decision, and it would need a different distribution name (the `agent-postbox`
command and the `agent_mail` module could stay as they are).

The two projects do not share a module or a command (this one installs the
module `agent_mail` and the command `agent-postbox`), but they do share the
distribution name, and pip keys on that: in one environment, installing either
removes the other, and `pip install --upgrade agent-postbox` replaces this
project with the PyPI one. Install and upgrade this project by path.

## Quick build

Build and check, in a throwaway virtualenv (never your project's):

```bash
python -m venv /tmp/pb-build && /tmp/pb-build/bin/python -m pip install build
/tmp/pb-build/bin/python -m build --outdir /tmp/pb-out .
python -m venv /tmp/pb-run && /tmp/pb-run/bin/pip install /tmp/pb-out/*.whl
/tmp/pb-run/bin/agent-postbox --version
```

That is enough to try a wheel locally. Do not hand its sdist to anyone else: a
plain `python -m build` builds whatever is in the directory (tracked or not),
and the sdist it writes records who built it. Each tar header carries the
builder's numeric uid and gid and their login and group names, generated
members carry the wall-clock time of the build, and the gzip header carries a
timestamp. Setting `SOURCE_DATE_EPOCH` fixes the wheel but not the sdist.

## Reproducible build (use this for anything you hand to someone else)

```bash
python -m pip install -r requirements-dev.txt     # provides `build`
python scripts/repro_build.py --check --outdir /tmp/pb-out
```

This leaves three files in the output directory: the wheel, the sdist and
`SHA256SUMS`.

`scripts/repro_build.py` (stdlib only; it drives `python -m build`):

1. takes `SOURCE_DATE_EPOCH` from the environment or, when unset, uses the
   committer timestamp of the commit being built (`git log -1 --format=%ct`);
2. exports that commit with `git archive` into a temporary directory and builds
   there, so untracked and ignored files cannot ship and the checkout is left
   alone (`--ref REF` builds another commit; the default is `HEAD`, and
   uncommitted changes are reported and left out);
3. runs `python -m build` with a fixed umask and explicit file modes, and with
   the build backend pinned by the `build-constraints.txt` of the commit being
   built (see "The pinned build backend" below);
4. rewrites the sdist deterministically: members sorted by name, every mtime
   equal to `SOURCE_DATE_EPOCH`, `uid = gid = 0`, empty user and group names,
   mode 0644 (0755 for directories and executables), no PAX records, and a
   gzip stream with mtime 0, no file name and a fixed header. The rewrite is
   refused unless member names and contents are exactly what setuptools built;
5. prints the SHA-256 of the wheel and the sdist, then a `BUILDINFO` block with
   everything the bytes depend on;
6. writes `SHA256SUMS` next to the artifacts (`--sums FILE` puts it elsewhere);
7. with `--check`, does all of that twice in two separate temporary directories
   and exits 1 if the two runs differ;
8. with `--verify DIR`, compares the rebuild with what is in `DIR` (see
   "Verifying a release download" below).

Exit status: 0 built (and, with `--check`, reproduced; with `--verify`,
identical), 1 not reproducible or not identical, 2 could not build. The script
needs a git checkout, so it is not part of the sdist.

### `SHA256SUMS`

One line per artifact, `<64 hex digits>  <file name>` (two spaces), sorted by
file name, LF line ends, file names only. That is the format `sha256sum -c`
and `shasum -a 256 -c` read. Its content is a function of the two artifacts, so
it is exactly as reproducible as they are, and its mtime is
`SOURCE_DATE_EPOCH` like theirs.

### `BUILDINFO`

```text
BUILDINFO
  commit: <40 hex digits>
  source_date_epoch: <integer>
  python: CPython 3.12.10
  platform: darwin arm64
  zlib: 1.2.12 (built against 1.2.12)
  deflate_probe: f7e2574c17c9cb89
  setuptools: 84.0.0
  build: 1.6.1 (requirements-dev.txt pins 1.6.1)
  constraints: build-constraints.txt sha256 <64 hex digits>
END BUILDINFO
```

It names no user, host or directory (tested). `setuptools` is read back from
the wheel that was just built, not from the request. `deflate_probe` is a
short hash of a fixed input compressed with the two settings the artifacts
use: two environments that print the same value compress alike, whatever
their zlib calls itself. When two machines disagree about a hash, compare
their blocks line by line: the line that differs is the cause. Keep the block
with any hash you publish.

### The pinned build backend

The bytes of both artifacts depend on the setuptools version: the wheel's
`WHEEL` file names it, and the generated metadata can change between
releases. `build-constraints.txt` at the repository root holds one line,
`setuptools==<version>`. `repro_build.py` reads it **from the commit it is
building** (not from the working tree) and hands it to `build` as
`--dependency-constraints-txt`, so for a given tag the toolchain is named by a
file in that tag. Constraint settings in the environment (`PIP_CONSTRAINT` and
the like) are removed for that build so they cannot add to it.

* `pyproject.toml` still says `setuptools>=77`. That is the floor for anyone
  building from a clone with plain `pip` or `build`, and it is not raised or
  pinned. The constraints file is used by `repro_build.py` only.
* The file is not in the sdist: it means something only to `repro_build.py`,
  which needs a git checkout and is not in the sdist either.
* To move the pin, change the version, run
  `python scripts/repro_build.py --check`, and record the new hashes. To try a
  version before committing it, `--constraints FILE` overrides the commit's
  file (the output says the hashes are then not the commit's).
* The line carries no `--hash=`. With one, pip before version 26 refuses the
  build ("all requirements must have their versions pinned with =="), because
  `pyproject.toml` asks for a range. PyPI never lets a released file be
  replaced, so the version alone fixes the bytes when the index is PyPI; it
  does not protect against a mirror that serves something else.
* A commit that has no `build-constraints.txt` (anything before this file was
  added) is built unpinned, and the output says so.

What else was checked when this was written, on one macOS machine: the hashes
for one commit were identical under CPython 3.11, 3.12 and 3.13, under pip
25.2 and 26.2, and under `build` 1.5.0 and 1.6.1. So neither pip nor the
`build` front end appears to reach the bytes; only setuptools, zlib and the
commit do. `build` older than the version that has
`--dependency-constraints-txt` is refused (exit 2).

## Verifying a release download

Put the downloaded wheel, sdist and `SHA256SUMS` in one directory.

1. **The files are the ones the checksums describe** (no clone needed):

   ```bash
   cd /path/to/download && sha256sum -c SHA256SUMS      # macOS: shasum -a 256 -c SHA256SUMS
   ```

   This shows the download is intact. It does not show where the files came
   from: whoever can replace a file can replace `SHA256SUMS` too.

2. **The files are what the tagged source builds** (needs a clone, `build`
   and network access for the build backend):

   ```bash
   git clone https://github.com/tyko84/agent-postbox && cd agent-postbox
   python3.12 -m pip install -r requirements-dev.txt
   python3.12 scripts/repro_build.py --ref <tag> --verify /path/to/download
   ```

   The script rebuilds `<tag>` and compares: each wheel or sdist found in the
   directory byte for byte, and each line of `SHA256SUMS` if the file is
   there. It exits 0 and prints `verified: identical to a rebuild of <commit>`
   only if both artifacts are accounted for (by a file or by a `SHA256SUMS`
   line) and everything found is identical. Otherwise it exits 1 and names
   each file, with the rebuilt hash and the one found:

   ```text
   DIFFERENT  agent_postbox-<version>-py3-none-any.whl
     rebuilt  <sha256>
     found    <sha256>
   ```

   `MISSING` (neither the file nor its line is there), `UNEXPECTED` (a wheel,
   sdist or `SHA256SUMS` line this commit does not produce) and `MALFORMED`
   (a `SHA256SUMS` line that is not in the format above) also fail. With
   `--verify` nothing is written unless `--outdir` or `--sums` is given.

   A difference does not by itself mean tampering. Compare the `BUILDINFO`
   block with the one published with the release first: a different zlib is
   the expected innocent cause (see below).

There is no supported setuptools or `build` option that does the same. The
wheel is reproducible from `SOURCE_DATE_EPOCH` alone. For the sdist, distutils
has `owner`/`group` options (settable through `[tool.distutils.sdist]`, which
setuptools labels experimental); they replace the owner names with those of an
existing local account, which is not the same on every system, and they leave
the timestamps and the gzip header as they were. So the script rewrites the
archive instead, and `pyproject.toml` is unchanged.

### What is guaranteed, and what is not

* **Guaranteed, and tested** (`test_packaging.py`, class `ReproducibleBuild`):
  the sdist names no builder (uid and gid 0, empty user and group names), every
  timestamp in it is `SOURCE_DATE_EPOCH`, its gzip header has no timestamp and
  no file name, and no member of either artifact contains a path of the build
  machine. Contents are the same allow-lists as for a plain build.
* **Guaranteed on one operating system, and tested there:** the same tag (so
  the same `build-constraints.txt` and the same committer timestamp), the
  reference interpreter and the same zlib give the same bytes, whatever the
  user, directory, umask, time zone or time of day. `--check` verifies this on
  the machine it runs on, and `test_packaging.py` checks that `--verify`
  passes on a second, independent build and fails, naming the file, when one
  byte of a copy is flipped.
* **Reference interpreter: CPython 3.12** (what CI's `packaging` job uses).
  CPython 3.11 and 3.13 produced the same bytes when this was written, but only
  3.12 is the reference: compare hashes made with 3.12.
* **Expected, not yet verified: the same bytes on Linux and macOS.** Nothing in
  either artifact is taken from the operating system, and both platforms'
  CPython normally link the classic zlib, whose output for a given level is
  expected to be the same across its 1.2 and 1.3 releases. But this has only been run on
  macOS (zlib 1.2.12). Until CI has built one commit on both and compared
  `SHA256SUMS`, treat cross-OS equality as a prediction. Where the interpreter
  is linked against zlib-ng or another replacement (some Linux distributions
  do this), the compressed bytes, and so both hashes, are expected to differ
  while every member inside stays identical; `deflate_probe` will differ too.
* **Not guaranteed across pins.** A commit that changes `build-constraints.txt`
  changes the wheel hash (the `WHEEL` file names the setuptools version) and
  may change generated metadata in both artifacts. That is a change in the
  repository, visible in the diff, not drift.
* **Not covered:** the reproducibility of the tools themselves (pip,
  setuptools and `build` are downloaded at build time; setuptools is pinned by
  version, not by hash), a package index other than PyPI, and Windows (POSIX
  only).

A hash is only meaningful with what it was built from: publish `SHA256SUMS`
together with the `BUILDINFO` block.

## Tests

`python test_packaging.py` automates the quick build (on a copy of the tree, so
the checkout stays clean) and fails if the artifacts contain anything that is
not on the allow-list, anything resembling a private file, or a path of the
build machine. From a git checkout it also builds once through
`scripts/repro_build.py` and checks the properties listed above, the
`SHA256SUMS` format, `--verify` in both directions, that the pinned setuptools
is the one that generated the wheel (and that another pin gives another
wheel), and that `BUILDINFO` names no builder. It needs the
`build` package and network access to fetch the build backend; without `build`
it skips with a message.

Releasing is a maintainer decision: a pushed version tag makes
`.github/workflows/release.yml` build and attach the artifacts to a draft GitHub
release, which a maintainer publishes; see [release-process.md](release-process.md).
POSIX only: the packaging is not tested on Windows.
