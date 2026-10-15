# Test and CI inventory (phase 9)

Evidence date 2026-10-15, commit `ce46252`. Local timings: macOS arm64, 16 cores, Homebrew Python 3.13.7
first on PATH, machine shared (load average 2 to 4.5 during the runs), each script run twice
(`run 1 / run 2`, seconds, one at a time). CI timings are from the last three successful `ci` runs on
`main` (38745546481, 38742633474, 38724992406).

## 1. Scripts, purpose, what each protects, wall clock

| Script | Purpose | Protects (demonstrated failure) | Local s |
|---|---|---|---|
| `selftest.py` (291 checks) | Core CLI/hook behaviour with positive controls | A hook that "printed nothing" passing as healthy (the silent-hook bug, PROTOCOL.md section 8) | 17.7 / 18.4 |
| `stress_test.py` | Parallel senders vs polling readers | Lost or overwritten messages, half-written reads | 0.8 / 0.9 |
| `scripts/scenario.py --quick --seed 1` | Reliability/benchmark harness, tiny config | Threshold regressions in delivery/claims | 5.8 / 6.0 |
| `scripts/team_sim.py --quick --seed 1` | Task-level team simulation | Arms diverging, rigged comparison | 1.7 / 1.7 |
| `test_hardening.py` (215) | Claim/lease validation, idempotent send, SIGKILL/SIGTERM at chokepoints | Duplicate sends after a killed writer, orphan idempotency markers, lock handling | 20.6 / 20.1 (after the fix in section 2) |
| `test_field.py` (91) | Four agents, one mailbox, timed phases with adaptive budgets | Races under realistic mixed use | 10.0 / 9.4 |
| `test_adversarial.py` (159) | Hostile text/files/concurrency, EPIPE, FIFOs, 10k-message list | Envelope forgery, hangs on FIFOs, SIGPIPE/ENOSPC mishandling | 36.1 / 35.9 |
| `test_handoff.py` (7) | `check_handoff.py` | Handoff validator regressions | 0.2 / 0.2 |
| `test_readme_examples.py` (8) | README bash blocks run, section references resolve | Docs that no longer work | 1.5 / 1.7 |
| `test_publication.py` (97) | `check_publication.py` scanner with planted violations; also pins workflow shape (pinned actions, required check names, matrix) | A scanner that finds nothing; CI drift | 7.2 / 6.8 |
| `test_cli.py` (202) | Help, refusal table, empty/quarantined mailbox UX | First-user-visible output regressions | 32.4 / 31.9 |
| `test_scenario.py` (6) | `scenario.py` as CI runs it | Harness exiting 0 on failure | 6.4 / 6.8 |
| `test_team_sim.py` (11) | `team_sim.py` trustworthiness | Harness rigged for one arm | 12.1 / 11.9 |
| `test_adapters.py` (47) | Host adapters vs documented shapes; installer | Installer writing outside project, symlinks, perms | 14.6 / 14.7 |
| `test_examples.py` (8) | Two-agents demo | Demo passing when it did nothing | 3.3 / 3.4 |
| `test_e2e_script.py` (7) | Offline test of the real-session script | Call budget, report fields | 0.8 / 0.6 |
| `test_live_demo.py` (24) | Offline recording kit | Kit judging missing artifacts as pass | 5.4 / 5.5 |
| `test_micro_experiment.py` (9) | Offline micro-experiment harness | Outcome computed from wrong evidence | 0.2 / 0.2 |
| `test_experiment_v2.py` (48) | Offline v2 harness and analysis | As above | 11.6 / 10.8 |
| `test_packaging.py` (31) | Build wheel/sdist, inspect, install, round-trip, `verify_release.py` | Stray files in artifacts, non-reproducible build | 0.1 / 0.1: all 31 SKIPPED here (no `build` module); CI `packaging` job 36 s |
| `check_publication.py` | Tool, not a test: scan tracked files, dist, git log, tags | Leaking forbidden names | CI 7-10 s |
| `scripts/repro_build.py` | Tool: reproducible ownerless build | Byte drift between builds/OS | CI 8 s (`--check`) |
| `scripts/preflight.sh` | Runs all tests, build, scan locally | n/a | about 3.5 min sequential |

`test_packaging.py` is in preflight but skips without `build` (the skip is printed, exit 0); CI sets
`POSTBOX_REQUIRE_BUILD_TESTS=1` only on `publication-build`.

## 2. Flakiness

Hard-coded timing in tests (excerpt):

| Where | Assumption | Risk |
|---|---|---|
| `test_hardening.py` 669-740, 1311 | real SIGINT to a child, `communicate(timeout=60)` | **Reproduced failure, see below** |
| `test_hardening.py` 191 | legacy takeover `5.0 <= t < 30.0` | Lower bound is the intended 5 s wait; the 30 s cap is generous. Low |
| `test_hardening.py` 648, 755 | "at once" means `< 3.0 s` | Low to medium on a starved runner |
| `test_hardening.py` 546-552, 649 | `0.5 <= took < 4.0` | Low to medium |
| `test_adversarial.py` 1211, 1235 | FIFO list `< 30 s`; 10k-file list `< 30 s` | Low (CI takes a fraction) |
| `test_adapters.py` 1074 | real `time.sleep(4)` for a lease to expire | Principled but fixed; fails only if `soon` is more than 4 s away |
| `test_field.py` 95-101 | `STEP_TIMEOUT=60` per CLI call, `RETRY_LIMIT=15`, phase budgets `1 s + waits + 4 x sends x slowest pace` | Budgets scale with measured pace: principled. 60 s per call is a hang detector, not a performance claim |
| other `STEP_TIMEOUT`/`TIMEOUT` | 120 (hardening, cli, adapters, e2e), 180 (live_demo), 300 (stress, examples, scenario, team_sim), 60 (`test_experiment_v2.py` 725) | Hang detectors; generous |
| `test_adversarial.py` 1435 | busy-wait up to 0.09 s for a kill point | Bounded, fine |

Load experiments (20 busy-loop processes on 16 cores, load average rising from 27 to 140; then 64 loops for
`test_field.py`), 5 runs each: `test_field.py` 5/5 pass (12-13 s; with 64 loops 5/5, 13-19 s),
`test_hardening.py` 5/5 pass (25-26 s), `test_adversarial.py` 5/5 pass (48-51 s). **No timing flake
reproduced**, so no timing budget was changed.

**The 60 s timeout.** The `60 s` timeout that failed here is a literal `communicate(timeout=60)` in
`test_hardening.py` (section 7, line 707 before the fix), not a load effect. It failed 2/2 when the test was
started as a background job of a non-interactive shell (`cmd &`) and passed when started in the foreground,
at the same load. Cause: a non-interactive shell starts background jobs with SIGINT ignored; Python then
installs no `default_int_handler`, so `send_signal(SIGINT)` to the parked child is ignored and the harness
waits out the 60 s. `agent_mail.py` turns SIGTERM into KeyboardInterrupt and relies on Python's SIGINT
default (lines 2427, 2463-2494). Any harness that ignores SIGINT (some cron/nohup/CI wrappers, agent tool
runners) hits it. `test_field.py` has its own `STEP_TIMEOUT = 60` (`FIELD_STEP_TIMEOUT`); it did not time
out in 10 loaded runs, so I could not attribute a load-9 timeout to it. If that case was a background
launch of `test_hardening.py`, the cause above is the likely one. The `test_field` budget itself is
principled (per-call hang detector, per-phase budgets scaled from the measured pace).

**Fixed** (`test_hardening.py`, `blocked_send` child prefix): reset SIGINT to `signal.default_int_handler`
in the child before importing `agent_mail`. Verified: the background run now passes (rc 0, "ALL PASS"). Power
kept: with `agent_mail.py` line 2427 mutated to `except ZeroDivisionError:` (SIGINT/SIGTERM handling
broken) the same background run FAILS 9 checks (rc 1), including "SIGINT behaves the same: exit 130";
mutation reverted. `test_adversarial.py` SIGINT cases raise KeyboardInterrupt directly and are unaffected.

## 3. Redundancy

| Behaviour | Place A | Place B | Verdict |
|---|---|---|---|
| Invalid `--key-wait` values refused, exit 2, nothing written | `selftest.py` 1285-1290 (`-1 60.5 abc nan inf ""`) | `test_hardening.py` 756-760 (`-1 60.01 1e1 five nan`) | Same behaviour, near-identical values; B adds `1e1`, A adds `inf`, `""` and the env var. Merge into one list |
| FIFO at the scope lock name: exit 2 `cannot lock scope`, FIFO untouched | `test_adversarial.py` 1153-1164 | `test_hardening.py` 1425-1445 (also simulates flock accepting a FIFO) | Overlap; B adds the flock variant. Keep both until A is shown a subset |
| `scenario.py --quick --seed 1` | CI step in `ci.yml` | `test_scenario.py` runs it again as a subprocess (6 s) | Run twice per cell; the CI step proves the entry point, the test pins the output |
| `team_sim.py --quick --seed 1` | CI step | `test_team_sim.py` (12 s) | Same |
| `test_publication.py` | all 10 `selftest` cells | `publication-build` (adds real-build tests) | 10 identical platform-independent runs, 7 s each |
| `repro_build.py` | `packaging` (`--check`), `publication-build`, `reproducible` x2 | | Built 4 times per run (8-24 s each); two are cross-OS by intent |

## 4. CI

Jobs (`ci.yml`; seconds across the three runs): `selftest` 10 cells (ubuntu 232-466, macOS 266-562),
`packaging` 41-52, `lint` 17-20, `publication-build` 35-39, `publication` 7-10 (needs build),
`reproducible` x2 11-24, `reproducible-compare` 4-6 (needs reproducible). `codeql.yml` (2 languages, about
100 s, not required) and `release.yml` are separate.

Whole-workflow wall clock: 496 s, 511 s, 608 s. The critical path is the slowest `selftest` cell, always
macOS (3.12: 273/562/496 s; macOS cells also start 40+ s late in two runs, runner queueing). Everything else
finishes in under 60 s. Inside a cell the 19 steps run serially: `test_adversarial` 96-103 s, `test_cli`
80 s, `selftest` 43-47 s, `test_hardening` 38-45 s, `test_team_sim` 32 s, `test_field` 28-31 s,
`test_adapters` 30-34 s, `test_experiment_v2` 25-29 s, `scenario` 15-18 s. Step times are 3 to 5 times
my local times (2-4 vCPU runners).

- **Caching:** none (`setup-python` has no `cache:`, no `actions/cache`). The selftest cells install
  nothing; only `lint`, `packaging`, `publication-build`, `reproducible` pip-install, a few seconds each.
  Caching would save little.
- **Duplicated work:** section 3, plus the 6 platform-independent tests running in all ten cells.
- **Permissions:** `ci.yml` has `permissions: contents: read` at workflow level only. `codeql.yml` and
  `release.yml` use `permissions: {}` and grant per job (`security-events: write` only in `analyze`).
  Least privilege holds. `publication` is the only job that references a secret and it installs nothing.
- **Action pins:** every `uses:` is a full commit SHA with a version comment (enforced by
  `test_publication.py` `test_every_action_is_github_owned_and_pinned_to_a_commit`). `lint` tools are
  version-pinned, not hash-pinned (documented).
- **Artifact retention:** `publication-dist` 3 days, `repro-*` 7 days, release artifacts 14 days.
- **Other:** `concurrency` cancels superseded PR runs only; every job has `timeout-minutes` (selftest 15 min
  vs a 466-562 s worst case, about 1.6x headroom).

## 5. Python 3.10 vs 3.14

- `test_adversarial.py` 815-819: the 3 MB fractional `expires` parses only on 3.11+ (3.10
  `fromisoformat` takes at most 6 fraction digits), so the expected count of listed ids is
  `3 if >= (3,11) else 2`. The test is correct; the product consequence (a CLAIM that is a live hold on 3.11+
  is not on 3.10) is an interpreter-dependent behaviour, see recommendation 6.
- `test_readme_examples.py` 92, 172: TOML read with `tomllib` on 3.11+, a regex on 3.10; both run on
  3.11+ so drift shows there.
- `test_hardening.py` 361-373: refusal on Python 3.9/3.8 is simulated with a fake `sys.version_info`; never
  run on a real old interpreter.
- `agent_mail.py` 3934: a 3.10 `argparse` quirk with a `--` value, handled in code.
- `lint` (ruff, mypy), `packaging`, `publication-build` run only on 3.12: a 3.10-only or 3.14-only typing
  error would surface only as a runtime failure.
- No other version gates in the tests; both OSes run all five versions.

## 6. macOS vs Linux assumptions

- `test_adversarial.py` 336: the `/dev/full` ENOSPC test is Linux only (guarded by `os.path.exists`).
- `test_hardening.py` 1433: flock on a FIFO is granted on Linux, refused on macOS; a simulated variant makes
  both platforms run both paths.
- `test_adapters.py` 562: `cd` is a file on macOS, not on Linux; temp paths are `.resolve()`d for
  `/var -> /private/var`; `test_packaging.py` 180 uses `realpath` for the same reason.
- `test_publication.py` 1144 flags a darwin-made archive member; `reproducible-compare` checks Linux/macOS
  byte equality.
- Many tests `skipUnless(os.name == "posix")`; `geteuid() == 0` tests skip as root. No Windows runner; the
  scenario and team-sim scripts state POSIX only.
- The macOS cells are the slowest and start latest, so a latent timing flake would show there first.

## 7. Ranked improvements

| # | Change | Evidence | Expected benefit | Class |
|---|---|---|---|---|
| 1 | Reset SIGINT in the `test_hardening.py` child (DONE here) | 2/2 failures in a background launch, 60 s hang; fixed, mutation-checked | Removes a deterministic failure when a harness ignores SIGINT; no CI cost | safe (done) |
| 2 | Split the CI `selftest` job into parallel jobs, or run the slowest steps (`test_adversarial`, `test_cli`, `selftest`) in parallel inside a cell | macOS cell is the critical path (500-560 s vs under 60 s for everything else); steps are independent | Wall clock from about 8.5-10 min toward 5 min | needs owner decision (job split changes required-check names `selftest (os, py)`; parallel steps add timing risk; not tried) |
| 3 | Run platform- and version-independent tests (`test_publication`, `test_handoff`, `test_readme_examples`, `test_e2e_script`, `test_live_demo`, `test_micro_experiment`) in one cell instead of ten | section 3 | About 20 s per cell (about 4 percent); little critical-path gain | needs owner decision (`test_publication` pins the matrix shape) |
| 4 | Drop the standalone CI steps `scenario.py --quick` and `team_sim.py --quick` (covered by `test_scenario`, `test_team_sim`) | same command executed twice per cell (about 24 s of a 460 s cell) | About 5 percent of a cell | needs owner decision (`ci.yml`, and `test_publication` pins the steps) |
| 5 | Make a skipped `test_packaging.py` loud in `preflight.sh` (fail when `build` is absent) | 31/31 skipped locally, exit 0, 0.1 s | Prevents a green preflight that never built anything | safe |
| 6 | Decide whether fractional `expires` with more than 6 digits is accepted on 3.10 as on 3.11+ | `test_adversarial.py` 815-819 encodes the difference | Removes a per-interpreter behaviour difference | needs owner decision |
| 7 | Merge the `--key-wait` invalid-value lists of `selftest.py` and `test_hardening.py`, keeping every value | section 3 | Clearer ownership, marginal time | safe (low value) |
| 8 | Run `lint`/mypy on 3.10 and 3.14 as well | mypy only on 3.12 | Earlier detection of version-specific typing errors | needs owner decision |
| 9 | pip caching | installs take a few seconds | negligible | not recommended |

## Unverified

The load-9 `STEP_TIMEOUT` 60 s case in `test_field.py`: not reproduced in 10 loaded runs. CI per-step times
come from one cell each (3.14 ubuntu, 3.12 macOS). Redundancy beyond the pairs cited was not audited line
by line. `test_packaging.py` was not run here (no `build` module); its CI time is from the `packaging` job.
