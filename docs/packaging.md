# Packaging

`pyproject.toml` is the only packaging config. The version has a single
source, `__version__` in `agent_mail.py`; setuptools reads it statically.
The wheel contains `agent_mail.py` and metadata only. The sdist is an explicit
allow-list in `MANIFEST.in`; anything not listed does not ship (apart from the
metadata setuptools itself generates: `PKG-INFO`, `setup.cfg` and
`tyko84_agent_postbox.egg-info/`).

## The distribution is `tyko84-agent-postbox`; `agent-postbox` on PyPI is someone else's

This project is **not published on PyPI**. Its distribution name (what pip
installs, lists and uninstalls) is `tyko84-agent-postbox`. The command is still
`agent-postbox` and the module is still `agent_mail`; only the distribution name
differs from them.

The PyPI distribution named
[`agent-postbox`](https://pypi.org/project/agent-postbox/) is a separate,
unrelated project by a different author, so `pip install agent-postbox` installs
that project, not this one. Always install from a clone
(`python -m pip install ./agent-postbox`, where `./agent-postbox` is the
directory) or from a wheel attached to a release or built from a clone, as
below. Nothing in this repository uploads anything to PyPI: that is a maintainer
decision.

Releases up to 0.3.0 used the distribution name `agent-postbox`, which pip
cannot tell apart from the PyPI project. If an environment has one of those,
remove it by its old name before installing this one, because both own
`agent_mail.py` and the `agent-postbox` command:

```sh
python -m pip uninstall agent-postbox          # the old distribution name
python -m pip install ./agent-postbox          # the clone, or a release wheel by path
python -m pip show -f tyko84-agent-postbox     # Home-page is this repository; one module
```

The order matters. Installed over the old distribution, this one works until the
old name is uninstalled; that removes `agent_mail.py` and the `agent-postbox`
command, which both distributions list, and pip reports no problem. If that has
happened, `python -m pip install --force-reinstall ./agent-postbox` restores them.

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
python -m pip install --require-hashes -r requirements-build.txt   # `build`, by hash
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
3. fetches the setuptools wheel that the `build-constraints.txt` of the commit
   being built records, refuses it unless its SHA-256 is the recorded one, and
   runs `python -m build` with a fixed umask and explicit file modes, pinned to
   that version and able to install that one file only (see "The pinned build
   backend" below);
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
  setuptools_wheel: setuptools-84.0.0-py3-none-any.whl sha256 <64 hex digits> (verified: the one the constraints file records)
  build: 1.6.1 (requirements-build.txt pins 1.6.1)
  constraints: build-constraints.txt sha256 <64 hex digits>
END BUILDINFO
```

It names no user, host or directory (tested). `setuptools` is read back from
the wheel that was just built, not from the request. `setuptools_wheel` is the
file the build backend was installed from and the SHA-256 the script computed
for it before the build; for a commit whose constraints file records no hash it
reads `not verified (...)`. `deflate_probe` is a
short hash of a fixed input compressed with the two settings the artifacts
use: two environments that print the same value compress alike, whatever
their zlib calls itself. When two machines disagree about a hash, compare
their blocks line by line: the line that differs is the cause. Keep the block
with any hash you publish.

### The pinned build backend

The bytes of both artifacts depend on the setuptools version: the wheel's
`WHEEL` file names it, and the generated metadata can change between
releases. `build-constraints.txt` at the repository root holds one
requirement, `setuptools==<version>`, and one comment line that records the
wheel of that release:

```text
# sha256: 51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670  setuptools-84.0.0-py3-none-any.whl
setuptools==84.0.0
```

`repro_build.py` reads the file **from the commit it is building** (not from
the working tree) and then:

1. runs `pip download --no-deps --only-binary :all:` for that one requirement
   into a temporary directory;
2. refuses to go on (exit 2) unless exactly one file arrived, it has the
   recorded name, and its SHA-256 is the recorded one;
3. hands the constraints to `build` as `--dependency-constraints-txt` and runs
   the build with `PIP_NO_INDEX=1` and `PIP_FIND_LINKS=<that directory>`, so
   the isolated build environment can install that file and nothing else.

So for a given tag the build backend is named by a file in that tag, by
version and by content. Constraint and index settings in the environment
(`PIP_CONSTRAINT`, `PIP_INDEX_URL` and the like) are removed for that build so
they cannot add to it. The hash above is the one PyPI lists for the wheel
(`https://pypi.org/pypi/setuptools/84.0.0/json`, the `digests.sha256` of the
`.whl` entry).

* `pyproject.toml` still says `setuptools>=77`. That is the floor for anyone
  building from a clone with plain `pip` or `build`, and it is not raised or
  pinned. The constraints file is used by `repro_build.py` only.
* The file is not in the sdist: it means something only to `repro_build.py`,
  which needs a git checkout and is not in the sdist either.
* To move the pin, change the version **and** the `# sha256:` line, run
  `python scripts/repro_build.py --check`, and record the new artifact hashes.
  Dependabot does not touch this file's pin (`.github/dependabot.yml` ignores
  setuptools): a new setuptools changes the artifact hashes, so it is moved by
  hand. To try a version before committing it, `--constraints FILE` overrides
  the commit's file (the output says the hashes are then not the commit's); a
  file without a `# sha256:` line builds unverified and BUILDINFO says so.
* The requirement line carries no `--hash=`. With one, pip before version 26
  refuses the build ("all requirements must have their versions pinned with
  =="), because `pyproject.toml` asks for a range. That is why the script does
  the checking itself.
* The check needs `pip` for the interpreter that runs the script. Without it
  the script stops (exit 2) rather than build with a backend it did not check.
* A commit that has no `build-constraints.txt` (anything before this file was
  added) is built unpinned, and a commit whose file has no `# sha256:` line
  (v0.3.1 and earlier) is built pinned by version only; the output says which.

What else was checked when this was written, on one macOS machine: the hashes
for one commit were identical under CPython 3.11, 3.12 and 3.13, under pip
25.2 and 26.2, and under `build` 1.5.0 and 1.6.1, and identical with and
without the pre-fetched wheel. So neither pip nor the `build` front end appears
to reach the bytes; only setuptools, zlib and the commit do. `build` older than
the version that has `--dependency-constraints-txt` is refused (exit 2).

### The build tools

Everything that is installed before the release artifacts are written is
pinned by hash, in two files:

| What | Pinned in | Checked by |
|---|---|---|
| `build`, `packaging`, `pyproject_hooks` | `requirements-build.txt`: one version and the SHA-256 of the wheel each, `--only-binary :all:` | pip (`--require-hashes`): a file with another hash, or a package that is not listed, stops the install |
| `setuptools`, the build backend | `build-constraints.txt`: one version and the SHA-256 of its wheel | `scripts/repro_build.py`, as above |

The three build tools are pure Python: each is a single `py3-none-any` wheel,
the same file on Linux and macOS, so one hash per package covers every
platform. On Python 3.10 `build` also needs `tomli`, which the file does not
list: it is for Python 3.11 and newer (CI builds on 3.12); on 3.10 use
`requirements-dev.txt`.

`requirements-dev.txt` is the other file: `ruff`, `mypy`, `build` and `twine`,
pinned by version, without hashes, and with dependencies of their own that are
not pinned at all (about thirty packages on macOS, a few more on Linux, where
`twine` pulls in `keyring`'s Linux back end and with it `cryptography`). They
are deliberately **not** locked by hash:

* several ship one wheel per platform and Python version, and some are needed
  on Linux only, so a complete lock cannot be install-tested on a single
  machine, and a hash that is missing for one platform fails only there;
* Dependabot's documentation promises updates to "any `.txt` file" and says
  nothing about hashes. Its source does rewrite the `--hash=` values of a line
  it bumps, but a plain requirements file gives it nothing to add or remove
  the lines that a new version's changed dependencies need, so with some
  thirty-five lines to track such a pull request would be repaired by hand
  often;
* nothing installed from that file can reach what a hash would protect. The
  jobs that install it (`lint` and `publication-build` in `ci.yml`, `test` in
  `release.yml`) hold no secret and a read-only token, and none of them writes
  a release artifact: the job that holds the forbidden list installs nothing
  ([publication-safety.md](publication-safety.md)), and the release's `build`
  job installs `requirements-build.txt` only and runs no test (some tests build
  with a plain `python -m build` and an unpinned, unchecked setuptools).

Follow-up, if the trade changes: generate a fully hashed lock for
`requirements-dev.txt` from the PyPI JSON API (every file of every pinned
release, with `sys_platform` markers for the Linux-only packages), prove it on
both CI platforms in a pull request, and switch `lint` and the `twine check`
jobs to `--require-hashes`.

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
   git checkout <tag>                      # the tag's own build script and pins
   python3.12 -m pip install --require-hashes -r requirements-build.txt
   python3.12 scripts/repro_build.py --ref <tag> --verify /path/to/download
   ```

   (v0.3.1 and earlier have no `requirements-build.txt`: install
   `requirements-dev.txt` there.) The script rebuilds `<tag>` and compares: each wheel or sdist found in the
   directory byte for byte, and each line of `SHA256SUMS` if the file is
   there. It exits 0 and prints `verified: identical to a rebuild of <commit>`
   only if both artifacts are accounted for (by a file or by a `SHA256SUMS`
   line) and everything found is identical. Otherwise it exits 1 and names
   each file, with the rebuilt hash and the one found:

   ```text
   DIFFERENT  tyko84_agent_postbox-<version>-py3-none-any.whl
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
* **Verified in CI: the same bytes on Linux and macOS.** The `reproducible`
  jobs build each commit on both and `reproducible-compare` fails if the two
  `SHA256SUMS` differ. Nothing in either artifact is taken from the operating
  system; the compression comes from zlib, and the classic zlib gives the same
  output across its 1.2 and 1.3 releases. Where the interpreter is linked against
  zlib-ng or another replacement (some Linux distributions do this), the
  compressed bytes, and so both hashes, are expected to differ while the contents
  stay identical: compare the unpacked files instead.
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
