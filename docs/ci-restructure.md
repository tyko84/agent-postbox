# CI restructure: run each selftest cell's scripts in parallel

PR-body draft. Branch `dev/ci-restructure`. Measured 2026-10-15 from the last five green `ci` runs on
`main` (38793463293, 38774286652, 38772496956, 38769202452, 38763549682); `gh run view --json jobs`,
read-only.

## What the measurements say

Job durations in seconds, five runs each:

| Job | Seconds |
|---|---|
| `selftest (macos-latest, 3.10 .. 3.14)` | 290-472, 420-488, 412-487, 275-465, 356-521 (median about 435) |
| `selftest (ubuntu-latest, 3.10 .. 3.14)` | 227-377, 297-349, 314-379, 263-379, 394-481 (median about 340) |
| `packaging` | 33-57 |
| `publication-build` | 27-44 |
| `lint` | 17-31 |
| `publication` / `reproducible` x2 / `reproducible-compare` | 6-16 / 11-19 / 3-6 |

The critical path is a `selftest` macOS cell. Everything else is under a minute. Inside a macOS 3.12 cell
(five-run step times; the 19 steps run one after another): `test_adversarial` 84-107, `test_cli` 71-95,
`selftest` 35-40, `test_hardening` 36-41, `test_team_sim` 29-40, `test_adapters` 23-28, `test_field` 20-25,
`test_experiment_v2` 19-26, `test_publication` 14-21, `scenario --quick` 11-18, `test_scenario` 14-16,
`test_live_demo` 8-10, the rest under 8 each. They sum to about 440 s. The longest single script is under
110 s.

`pip install` takes 7-21 s in `lint` and `publication-build`; the selftest cells install nothing.
Neither is on the critical path. **Pip caching is not added**: it would save
a few seconds in jobs that finish 5 minutes before the cells, and a cache restored on `pull_request` runs
is one more thing to trust. `setup-python` has no `cache:`; that stays.

## Why not split `selftest` into several jobs

It is the obvious design and it does not help here. The free plan allows 5 concurrent macOS jobs
(the limit is on jobs, not on CPU; this account's plan was not checked), and `ci` already asks for 6 (five cells and
`reproducible (macos-latest)`); the "start 40+ s late" cells in the inventory fit that. Splitting each
macOS cell into three jobs would queue 15 jobs behind 5 slots: the total macOS time is unchanged and the
wall clock with it. It would also rename every required check (below). **Parallelism inside the job**
uses the cores the runner already gives the job and no extra slot.

## The change

`scripts/ci_run.py` replaces the 19 serial steps with one step. It runs the same scripts, three at a time
(the macOS runner has 3 cores, Linux 4), longest first, with each script's output held and printed as one
log group, every script run to the end even after a failure, and the exit status nonzero if any failed.
`--jobs 1` is the old serial behaviour.

* `selftest` keeps its job id, `strategy.matrix` (`os` x `python`), `runs-on` and `timeout-minutes`. No
  required check changes name.
* `scripts/ci_run.py` `TESTS` is now the one list of what a cell runs. `test_publication.py` fails if a
  root `test_*.py` (or `selftest.py`, `stress_test.py`) is on neither `TESTS` nor `ELSEWHERE`
  (`test_packaging.py` runs in `packaging`), and checks that `release.yml`'s gate and `preflight.sh` name
  the same set.
* **Run once per OS, not once per cell:** `test_publication.py` only (about 15 s on macOS, 4 s on Linux
  per cell). It reads tracked text and git metadata, builds synthetic archives, and runs `preflight.sh`
  with a stand-in interpreter; none of that depends on the Python version, and `publication-build`
  already runs it on Python 3.12 with the real-build tests required. It now runs in the 3.12 cell of each
  OS (`--with-once`), so both OSes keep it and four of five version repeats go. Nothing else qualifies:
  `test_live_demo`, `test_experiment_v2`, `test_e2e_script` and `test_micro_experiment` start
  `agent_mail.py` as a subprocess, so they stay in every cell.
* **Correction to `docs/test-inventory.md` section 3 / recommendation 4:** the standalone `scenario.py
  --quick --seed 1` and `team_sim.py --quick --seed 1` are not duplicates of `test_scenario.py` and
  `test_team_sim.py`. The tests run a different configuration (`TINY`, `SMALL`) under `python -I`; the
  CI step is the only run of the shipped `--quick` profile. Both stay (about 20 s on macOS).
* Nothing is dropped from any cell except the four `test_publication` repeats per OS above.

## Required status checks

Today (`gh api repos/tyko84/agent-postbox/branches/main/protection/required_status_checks -q .contexts`,
read-only), 13 contexts:

`lint`, `packaging`, `publication`, and the ten `selftest (<os>, <python>)` for os in `ubuntu-latest`,
`macos-latest` and python in 3.10 to 3.14.

After this change: **identical, 13 names, none renamed, added or removed.** Branch protection needs no
edit. `test_publication.py` `test_required_check_names_are_unchanged` still pins the matrix lines, and a
new check pins that the job runs `ci_run.py` exactly once.

The alternative that does rename is a matrix over a `shard` axis (names `selftest (os, python, shard)`):
30 new contexts to require, the 10 old ones to delete, and by the slot limit above, no faster. A final
aggregate job named like the old cells would keep the names but would only restore them; it would not
fix the slot limit either. Not recommended.

## Expected effect (estimate; it can only be measured on this PR's own CI run)

Script time per macOS 3.12 cell is about 440 s serial. Three lanes bound the cell by the longest script
(`test_adversarial`, about 95 s) and by contention on 3 cores. Locally (16 cores, otherwise idle) the same
list took 72 s wall for 213 s of script time (3 at a time), no failures, every script's result unchanged,
which shows the scripts do not interfere (separate temp dirs, no fixed ports). On a 3-core runner the
speedup is smaller: **estimate 2.0x to 2.5x, a macOS cell of about 180 to 240 s (from about 435 s) and a
whole-workflow wall clock of about 250 to 300 s (from 485-608 s)**. The estimate assumes the scripts spend
about half of their time waiting on subprocess work; if the first CI run shows the cell above 300 s, set
the default to 2 (`--jobs`, one line) or open the PR's run log: the final summary lists each script's
time and the wall total.

Risks, stated: (1) the timing-sensitive assertions in `test_hardening` and `test_field` now share cores
with other scripts. `docs/test-inventory.md` section 2 saw no flake with load average up to 140 on 16
cores, and those budgets scale with the measured pace, but a slow macOS runner is where one would show
first. If a flake appears, run the offender alone by moving it to a second `ci_run.py` call, or use
`--jobs 2`. (2) Logs now arrive per script when it ends instead of live; a hung script is cut at 900 s and
named.

## Not changed

`release.yml` (its gate stays serial, a release is not time-critical, and a change to it needs a dry
run per `docs/release-process.md`), `preflight.sh` (sequential and quiet; the test list is unchanged, so
only `test_publication.py` guards its agreement with `TESTS`), the CONTRIBUTING test list (the list of
scripts is the same), the `reproducible` jobs, `codeql.yml`.

The integrator wires `scripts/experiment_v3.py`, `scripts/analyze_v3.py` and `test_experiment_v3.py` after
this merges: add `Test(("test_experiment_v3.py",), <seconds>)` to `TESTS`, the three files to the `mypy`
line of `ci.yml` and the PR template, and the test to `release.yml`'s gate and `preflight.sh`.

## Verified here

`python scripts/ci_run.py --with-once --jobs 3` (3.12): 19 scripts pass. `test_publication.py`,
`test_readme_examples.py`, `test_packaging.py` (31 tests, real build), `ruff`, `mypy` on the touched files.
`ci.yml` and `release.yml` parse as YAML (ruby `YAML.load_file`; no actionlint here). Not verified:
anything on GitHub-hosted runners, the real timing, or behaviour on Python 3.10 and 3.14 locally.
