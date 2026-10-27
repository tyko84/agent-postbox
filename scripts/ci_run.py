#!/usr/bin/env python3
"""Run the test list of one `selftest` CI cell, a few scripts at a time.

    python scripts/ci_run.py [--jobs N] [--with-once] [--list]

`ci.yml` used to run the 19 scripts one after the other in 19 steps; on a 3-core
macOS runner that was 7 to 9 minutes, and the cell is the critical path of the
workflow. Most scripts spend their time waiting on subprocesses, so the same list
runs here N at a time (default 3) inside the one job. The job keeps its name, its
matrix and its place in branch protection; only the way it runs the list changed.

* TESTS is the list, the one place that names what a cell runs. test_publication.py
  checks that every test script at the repository root is on it (or is named in
  ELSEWHERE with the job that runs it).
* Longest first, so the slowest script starts at once and the short ones fill the gaps.
* Each script's output is held and printed as one `::group::` when it ends, so the log
  reads script by script; a failure prints the whole output under `::error::`.
* Any failing, timed-out or unrunnable script fails the run, after all of them have run.
  `--jobs 1` is the old serial behaviour.
* A script marked `once` is platform-independent and Python-version-independent and
  runs only in cells that pass `--with-once` (ci.yml: the 3.12 cell of each OS).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parent.parent
# Variables that select which repository a git command acts on. `git rebase --exec`, hooks
# and `bisect run` export them; a test that inherited them would run its temporary-repository
# git calls against the real repository. Every test child process gets an environment without them.
GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
                 "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES")
# The temporary repositories the tests build must not run the developer's own hooks
# (core.hooksPath, init.templateDir) or fail on their signing settings. Test children only:
# the tool under test, and anything a user runs, keeps reading the git configuration it finds.
GIT_ISOLATED_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
# Configuration passed through the environment overrides files, so those are dropped as well
# (GIT_CONFIG_COUNT/KEY_n/VALUE_n, GIT_CONFIG_PARAMETERS) and so is a template directory that
# could copy hooks into every new repository.
GIT_CONFIG_ENV = re.compile(r"^(GIT_CONFIG_(PARAMETERS|COUNT|KEY_\d+|VALUE_\d+)|GIT_TEMPLATE_DIR)$")
TIMEOUT = 900  # seconds, per script: a hang detector (the job limit is 15 minutes)


@dataclass(frozen=True)
class Test:
    argv: tuple[str, ...]   # after the interpreter
    weight: int             # rough seconds on a CI runner, for ordering only
    once: bool = False

    @property
    def name(self) -> str:
        return " ".join(self.argv)


# Weights are the macOS 3.12 step times of recent green runs, rounded. They only order the
# queue; a stale weight costs a little wall clock, never coverage.
TESTS: tuple[Test, ...] = (
    Test(("test_adversarial.py",), 95),
    Test(("test_cli.py",), 78),
    Test(("selftest.py",), 38),
    Test(("test_hardening.py",), 38),
    Test(("test_team_sim.py",), 33),
    Test(("test_adapters.py",), 25),
    Test(("test_conformance.py",), 4),
    Test(("test_field.py",), 23),
    Test(("test_experiment_v2.py",), 22),
    Test(("test_experiment_v3.py",), 20),
    Test(("test_experiment_v4.py",), 55),
    Test(("test_publication.py",), 16, once=True),
    Test(("scripts/scenario.py", "--quick", "--seed", "1"), 14),
    Test(("test_scenario.py",), 14),
    Test(("test_live_demo.py",), 10),
    Test(("test_examples.py",), 6),
    Test(("scripts/team_sim.py", "--quick", "--seed", "1"), 5),
    Test(("stress_test.py",), 4),
    Test(("test_readme_examples.py",), 3),
    Test(("test_handoff.py",), 1),
    Test(("test_e2e_script.py",), 1),
    Test(("test_fake_claude.py",), 1),
    Test(("test_micro_experiment.py",), 1),
    Test(("test_hook_probe.py",), 4),
)

# Test scripts at the root that a cell does not run, and the job that does.
ELSEWHERE = {"test_packaging.py": "packaging"}


def run_one(test: Test, python: str) -> tuple[Test, int, float, str]:
    start = time.monotonic()
    env = {k: v for k, v in os.environ.items()
           if k not in GIT_REPO_VARS and not GIT_CONFIG_ENV.match(k)}
    env.update(GIT_ISOLATED_CONFIG)
    try:
        cp = subprocess.run([python, *test.argv], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT)
        rc, out = cp.returncode, cp.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rc = 124
        out = (e.stdout or b"").decode("utf-8", "replace") + f"\n[ci_run: timed out after {TIMEOUT} s]\n"
    except OSError as e:
        rc, out = 127, f"[ci_run: cannot run {test.name}: {e}]\n"
    return test, rc, time.monotonic() - start, out


def run(tests: Sequence[Test], jobs: int, python: str = sys.executable,
        out: TextIO | None = None) -> int:
    """Run `tests` with `jobs` at a time; the number of failures (0 = all passed)."""
    stream = out or sys.stdout
    write = stream.write
    queue = sorted(tests, key=lambda t: -t.weight)
    lock = threading.Lock()
    results: list[tuple[Test, int, float]] = []
    begin = time.monotonic()

    def worker() -> None:
        while True:
            with lock:
                if not queue:
                    return
                test = queue.pop(0)
            t, rc, secs, text = run_one(test, python)
            with lock:
                results.append((t, rc, secs))
                write(f"::group::{'ok' if rc == 0 else 'FAILED'} {t.name} ({secs:.0f} s)\n{text}")
                if text and not text.endswith("\n"):
                    write("\n")
                write("::endgroup::\n")
                if rc:
                    write(f"::error::{t.name} exited {rc}\n")
                stream.flush()

    threads = [threading.Thread(target=worker) for _ in range(max(1, min(jobs, len(tests) or 1)))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    failed = [t.name for t, rc, _ in results if rc]
    spent = sum(s for _, _, s in results)
    write(f"ci_run: {len(results)} script(s), {len(failed)} failed, {time.monotonic() - begin:.0f} s "
          f"wall for {spent:.0f} s of script time ({jobs} at a time)\n")
    for t, rc, secs in sorted(results, key=lambda r: -r[2]):
        write(f"  {'ok  ' if rc == 0 else 'FAIL'} {secs:6.1f} s  {t.name}\n")
    return len(failed) + (len(tests) - len(results))


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--jobs", type=int, default=3, help="scripts to run at a time (default 3; 1 = serial)")
    ap.add_argument("--with-once", action="store_true",
                    help="also run the scripts that are independent of platform and Python version")
    ap.add_argument("--list", action="store_true", help="print the scripts this cell would run and stop")
    args = ap.parse_args(argv)
    if args.jobs < 1:
        ap.error("--jobs must be at least 1")
    tests = [t for t in TESTS if args.with_once or not t.once]
    if args.list:
        for t in tests:
            print(t.name)
        return 0
    return 1 if run(tests, args.jobs) else 0


if __name__ == "__main__":
    sys.exit(main())
