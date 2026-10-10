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
below. Nothing in this repository uploads anything: publishing is a maintainer
decision, and it would need a different distribution name (the `agent-postbox`
command and the `agent_mail` module could stay as they are).

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

`scripts/repro_build.py` (stdlib only; it drives `python -m build`):

1. takes `SOURCE_DATE_EPOCH` from the environment or, when unset, uses the
   committer timestamp of the commit being built (`git log -1 --format=%ct`);
2. exports that commit with `git archive` into a temporary directory and builds
   there, so untracked and ignored files cannot ship and the checkout is left
   alone (`--ref REF` builds another commit; the default is `HEAD`, and
   uncommitted changes are reported and left out);
3. runs `python -m build` with a fixed umask and explicit file modes;
4. rewrites the sdist deterministically: members sorted by name, every mtime
   equal to `SOURCE_DATE_EPOCH`, `uid = gid = 0`, empty user and group names,
   mode 0644 (0755 for directories and executables), no PAX records, and a
   gzip stream with mtime 0, no file name and a fixed header. The rewrite is
   refused unless member names and contents are exactly what setuptools built;
5. prints the SHA-256 of the wheel and the sdist, with the versions the bytes
   depend on (Python, zlib, and the setuptools that generated the wheel);
6. with `--check`, does all of that twice in two separate temporary directories
   and exits 1 if the two runs differ.

Exit status: 0 built (and, with `--check`, reproduced), 1 not reproducible,
2 could not build. The script needs a git checkout, so it is not part of the
sdist.

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
* **Guaranteed for one toolchain:** the same commit, the same
  `SOURCE_DATE_EPOCH`, the same setuptools version and the same zlib give the
  same bytes, whatever the user, directory, umask, time zone or time of day.
  `--check` verifies this on the machine it runs on.
* **Reference interpreter: CPython 3.12** (what CI's `packaging` job uses).
  CPython 3.11 and 3.13 produced the same bytes when this was written, but only
  3.12 is the reference: compare hashes made with 3.12.
* **Not guaranteed across toolchains.** `pyproject.toml` asks for
  `setuptools>=77` without pinning it, and the wheel's `WHEEL` file names the
  setuptools version that generated it, so a new setuptools release changes
  the wheel hash (and may change the metadata in both artifacts). The
  compressed bytes of both artifacts come from zlib, so a different zlib (or a
  zlib-compatible replacement) can change both hashes while every member stays
  identical. The script prints all three versions next to the hashes: record
  them with any hash you publish. To hold setuptools still, pass a pip
  constraints file through the environment, for example
  `PIP_CONSTRAINT=/path/to/constraints.txt` containing `setuptools==<version>`.
* **Not covered:** the reproducibility of the tools themselves (pip, setuptools
  and `build` are downloaded at build time), and Windows (POSIX only).

A hash is only meaningful with the commit it was built from: record the
commit, `SOURCE_DATE_EPOCH`, the three versions and both SHA-256 values
together.

## Tests

`python test_packaging.py` automates the quick build (on a copy of the tree, so
the checkout stays clean) and fails if the artifacts contain anything that is
not on the allow-list, anything resembling a private file, or a path of the
build machine. From a git checkout it also builds once through
`scripts/repro_build.py` and checks the properties listed above. It needs the
`build` package and network access to fetch the build backend; without `build`
it skips with a message.

Releasing (tagging, publishing) is a maintainer decision and is not
automated here; the steps are in [release-process.md](release-process.md).
POSIX only: the packaging is not tested on Windows.
