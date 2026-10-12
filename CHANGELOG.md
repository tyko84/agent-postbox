# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, minor versions may change
behaviour).

## [Unreleased]

## [0.5.0] - 2026-10-12

### Added

- `scripts/team_sim.py`: an offline, deterministic simulation of task-level team outcomes
  (completion, conflicting edits, missed facts, crash recovery, overhead, false success)
  with bootstrap intervals over seeds and a sensitivity sweep over how often a control team
  reads its shared notes. Postbox's communication goes through the real CLI and adapter;
  agent behaviour is scripted, so it measures the effect of the mechanism under stated
  assumptions and says nothing about real models. `docs/coordination-experiment.md` is the
  pre-registered design for a real-agent experiment (arms, metrics computed from artifacts,
  sample size, decision rule, threats to validity, cost model, safety rules). `test_team_sim.py`
  pins determinism, equal workloads for both arms, the exit-status contract and that the
  harness cannot only favour Postbox.
- `doctor` prints `identity_bindings: UNREADABLE (...)` when PARTICIPANTS.md exists but
  cannot be read, so the identity checks silently not running is visible (additive; no
  exit-status change; nothing is printed for an absent or readable file).
- `scripts/e2e_claude.py`: real-session end-to-end tests of the Claude Code adapter in
  headless `claude -p`, with random tokens and a negative control for each check; it
  refuses sessions beyond a call budget (`--max-calls`, default 12), exits 2 when `claude`
  is missing or signed out, and is not run in CI (`test_e2e_script.py` covers the offline
  parts). `docs/e2e-real-sessions.md` records the first run (Claude Code 2.1.296, haiku,
  2026-10-12): hooks fire in headless mode; the prompt boundary, no cross-talk, the next
  prompt, resume, recovery after SIGKILL and a broken mailbox not blocking all passed;
  an ASK was answered correctly 3 of 3 and an instruction in a message subject was obeyed
  0 of 3 (small samples, one model).
- `examples/two-agents/run.py`: a demo that installs into a throwaway project with the
  real `install.py` and plays two scripted sessions through the real CLI and pickup
  adapter (an ASK surfaced with no polling, a CLAIM, an ANSWER with `--result DONE`
  surfaced back, a second claim refused with exit 3, `status`). It prints every command
  with its output, checks each step and exits 1 at the first that does not behave as
  described; `--keep` and `--pause`. No model, no account, no network. `test_examples.py`
  runs it, and runs it against a silenced adapter and a tool that grants a second claim to
  confirm it fails at the right step. The sdist now includes `examples/`.
- `install.py --adapter codex`: registers `hooks/agent_mail_check.py`, unchanged, as a
  Codex `UserPromptSubmit` command hook in the project's `.codex/hooks.json`; merges with
  a backup, is idempotent, and refuses before copying anything when it cannot register
  safely. `docs/hosts.md` is the host compatibility matrix (Claude Code, Codex, Cursor,
  hosts without hooks): the Codex path is tested against the documented payload shapes
  (`test_adapters.py`, `scripts/scenario.py --pickup codex`), not inside the real host;
  Cursor has no per-prompt injection and polls.
- `docs/issues-to-open.md`: five starter issues drafted for the tracker.
- `result:` on ANSWER has a fixed meaning (PROTOCOL.md section 39): one of DONE, PARTIAL,
  FAILED, DECLINED, CANNOT_VERIFY, with an optional `reason:`, applied only to the ASK the
  ANSWER cites. It is the sender's claim, never proof; nothing in the protocol can mark it
  verified. `send --type ANSWER --result` validates the value (exit 2, nothing written) and
  stores it upper-cased. `list --json` and `inbox --json` rows gain `result`; `show --json`
  gains `result` and `reason`. `status` prints an `ask results (claimed, not proven)` line
  and an attention table when any ANSWER carries a result, and `problems` gains the codes
  MALFORMED_RESULT, UNATTACHED_RESULT, CONTESTED_RESULT and RESULT_FROM_THIRD_PARTY.
  Mailboxes without results derive and print exactly as before.
- Optional Unix-user identity binding (PROTOCOL.md section 40, `docs/identity.md`): a
  `uid` cell in a PARTICIPANTS.md row binds an identity to a numeric uid or a user name
  resolved through the password database at read time. `status` and `doctor` compare each
  message file's observed OS owner (lstat, never following a link) with the bound uid of
  its self-declared sender; `status --json` gains an `identity` object and the problem
  codes IDENTITY_MISMATCH, IDENTITY_UNRESOLVED and IDENTITY_SHARED_INODE, present only
  when a binding is declared; `doctor` fails on a mismatch or an unresolvable binding and
  notes when every binding names the reader's own uid; `verify` prints a `sender_owner:`
  line; `send`, `ask` and `canary` refuse (exit 2, nothing written) when the process's
  effective uid is not the sender's bound uid. Evidence, not authentication: the docs list
  what the owner proves and what root, chown, sudo, id-squashing mounts, uid-remapped
  containers, shared accounts, symlinks and hard links do to it. With no binding declared
  every output and exit status is byte-identical to before.
- `scripts/scenario.py`: a reproducible benchmark and reliability harness
  (docs/benchmarking.md). Simulated agents drive the real `send`, the pickup adapter (or
  `inbox --json` as a polling baseline that remembers with probability `--poll-p`) and
  `status --json` under a seeded workload with a checked schedule hash; it injects
  interrupted writes, abandoned scope locks, agent crashes, planted malformed files and
  delayed polling; measures delivery latency in seconds and prompt boundaries (percentiles
  only when the sample justifies them), missed and duplicate deliveries, conflicting and
  expired claims, unanswered ASKs and recovery; prints OK/FAIL lines for the hook's
  invariants and writes a documented `--json` report. `--quick` runs in the `selftest` CI
  job; `test_scenario.py` pins determinism, a planted FAIL and the exit-status contract.
- `agent_mail.open_asks(everything, to)`: the ASKs addressed to an identity that still
  owe a reply, in mailbox order, with the citation index built once. The batch form of
  `owes_reply()`; `selftest.py` checks it against the slow definition on answered,
  unanswered, re-asked, superseded and quarantined cases, with a positive control.
- `scripts/verify_release.py`: the consumer-side check of a published (or draft) release
  in one command. It downloads the assets with `gh release download`, checks the asset
  names, every `SHA256SUMS` line, `gh attestation verify` for the wheel and the sdist,
  the exact wheel members, the sdist's layout, owners, single mtime and gzip header, and
  the Name and Version in `METADATA` and `PKG-INFO`; `--rebuild` adds the byte-for-byte
  comparison with `scripts/repro_build.py --verify`. One `OK`/`FAIL` line per check,
  exit 1 on any failure. Covered by `test_packaging.py` on the repro_build artifacts and
  on deliberately broken copies; `docs/release-process.md` points to it next to the
  manual commands.
- The pickup adapter (`hooks/agent_mail_check.py`) names, after the live mail, what
  `AGENT_MAIL_IDENTITY` holds and owes: its unsuperseded claims (scope, since when,
  live until / `NOT live` once lapsed) and the open ASKs addressed to it that still
  owe a reply. Five rows per list, then `+N more`; the rows are those of
  `status --json` (PROTOCOL.md section 34), never a subject or a body. Read-only,
  still silent on every error and when there is nothing to say (section 9).
- `CONTRIBUTING.md`: a field report walkthrough (what to collect from `doctor`,
  `status --json` and `--version`, and what never to paste).

### Changed

- README opens with the problem, a three-step quickstart (clone, run the demo, install)
  and one measured table from `scripts/scenario.py` with its limits stated; the command
  walkthrough formerly headed "Quickstart" is "Using the CLI", content unchanged.
- The section 37 row has fourteen keys (`result` added); the show document has `result`
  and `reason`. Consumers checking exact key sets must add them.
- `PARTICIPANTS.md` and `PARTICIPANTS.template.md` carry a `uid` column (empty by default).
- Every `.py` file in the tree type-checks under mypy and is on the CI lint job's list
  (`selftest.py`, `test_cli.py`, `test_hardening.py` and `test_adversarial.py` joined it):
  rebound locals were renamed, containers annotated and the hostile-field tables typed,
  with no check name, expected value or order changed. `test_publication.py` requires
  every `.py` at the root and under `hooks/` and `scripts/` to be on that line.
- SECURITY.md, "How this repository is built and released": what a consumer can verify
  about a release and how (`SHA256SUMS`, `gh attestation verify`, `scripts/repro_build.py
  --ref TAG --verify DIR`, `scripts/verify_release.py TAG`), what none of it shows (that
  the code is correct, that the maintainer's account was not compromised, that a future
  release will pass), and the install stance (not on PyPI; the attached wheel or a clone,
  never a bare name). "Reporting" asks for the `verify_release.py` output with any report
  about a release artifact.
- README ("Silence proves nothing") and PROTOCOL.md section 8 name `open_asks()` as the
  batch form of `owes_reply()`, answering that question and not `derived_status()`'s.
- The pickup adapter takes its owed ASKs, and the live test of an ASK, from `open_asks`
  instead of asking `owes_reply`/`live` without an index once per ASK. Same output; at
  100,000 messages the adapter runs in about 4.4 s instead of 19.7 s, at 10,000 in
  393 ms instead of 502 ms (the rest is reading the mailbox).
- README: the install section's "Verifying a release download" leads with
  `python scripts/verify_release.py v0.4.0` (what it checks, `gh` signed in,
  `--no-download --dir DIR`, `--rebuild` with the hash-pinned build tools, the manual
  checksum command) and points to docs/release-process.md for the manual commands; the
  30-second demo's adapter output block shows the `id:`, `read:` and `Reply:` lines and
  the "owe a reply" block the hook prints today.
- The CI lint job type-checks every file that passes mypy today, `scripts/repro_build.py`
  and `scripts/verify_release.py` among them (six files before, thirteen now).
  `selftest.py`, `test_adversarial.py`, `test_cli.py` and `test_hardening.py` are not yet
  clean and are not checked. `test_publication.py` requires every `scripts/*.py` to be on
  the lint job's mypy line, so a new script cannot be added without being type-checked.
- README states the supported interpreters as Python 3.10-3.14 (it still said 3.10-3.13
  after 3.14 joined the CI matrix and the classifiers). `test_readme_examples.py` derives
  the range from pyproject.toml's `Programming Language :: Python :: 3.X` classifiers
  (tomllib on 3.11+, a regex on 3.10) and fails if README's "Python 3.X-3.Y" wording,
  `requires-python` or the `selftest` matrix in `ci.yml` disagree with them.
  docs/packaging.md and docs/pypi-publishing.md say that their 3.11/3.12/3.13
  measurements did not include 3.10 or 3.14.
- The pickup adapter computes the open ASKs addressed to the identity once and reuses
  them for the `[awaiting your reply]` marker and the holdings block, instead of a
  full-mailbox pass per use. Same output; at 10,000 messages the adapter runs in about
  515 ms against 576 ms before and 490 ms in 0.4.0, and at 100,000 in about 18 s against
  25 s before.
- CPython 3.14 is a supported interpreter: the `selftest` matrix in `ci.yml` has a 3.14
  cell on Linux and macOS, and `pyproject.toml` carries the 3.14 classifier.
  `Requires-Python` stays `>=3.10`, and the release build stays on 3.12, the reference
  interpreter for the published hashes. Branch protection must require the two new
  `selftest (..., 3.14)` checks (docs/release-process.md, "Required checks").
- `docs/release-process.md`: how the published release is verified (the commands run for
  v0.4.0), what GitHub's immutable releases would change for this repository (not
  enabled; the owner decides), and how to rehearse the release workflow in a scratch
  repository.
- `test_publication.py` (`Workflows`) also checks that the release workflow's `publish`
  job creates the release with `--draft` and `--verify-tag` and never publishes, marks
  latest or edits a release itself: a person always publishes.

### Fixed

- `install.py` refuses a `--dest` or `--mailbox` that could write outside the project
  (exit 2, one `ABORT` line, nothing changed; `--check` prints `WOULD REFUSE`): an
  absolute path, a `..` part, a symlink in the path that leads out, or a payload file that
  is a symlink out (copying would have written through it). Before, `--dest ../x` and
  `--mailbox ../x` installed outside the project; a leading `/` was silently stripped.
  Relative paths and symlinks that stay inside the project are unchanged.
- `install.py` writes `.claude/settings.json` and `.codex/hooks.json` atomically (a temp
  file in the same directory, fsync, rename; the mode is kept), so an interrupted install
  can no longer leave a truncated settings file with the user's other settings in it.
- The first settings backup (`.bak`) is never overwritten: later upgrades and repairs write
  `.bak.1`, `.bak.2`, and the install output names the backup it wrote. Before, a second
  run replaced the backup of the user's original file.
- `install.py` refuses, before copying anything, when a file sits where the mailbox,
  `--dest`, `<dest>/hooks` or a payload file must go: one `ABORT` line on stderr, exit 2,
  and `--check` prints `WOULD REFUSE` (this was a traceback, in one case after the tool had
  been copied).
- `send` no longer reads an oversized `--body-file` or stdin body into memory: reading
  stops at the 16 MiB limit plus one character (a 1 GiB file went from about 2.2 GB of
  memory to under 100 MB). The refusal and its exit status are unchanged; a body over the
  limit that also holds a forbidden character past the stopping point now gets the size
  message instead of the control-character one.
- `install.py` no longer rewrites a hand-written hook command whose first word is not a
  Python interpreter. It judged the first word of any registered command to be the
  interpreter and, when it was not on PATH, "repaired" the command: on Linux that
  replaced `cd ... && python3 ...`, and on every platform it replaced
  `AGENT_MAIL_IDENTITY=agent-a python3 ...`, dropping the user's own setting (a backup
  was kept). Only a missing `python`/`python3` or a path that is not a file is repaired.
- `install.py --adapter claude-code` registers a command ending in `|| true`. With the
  hook script missing (a moved or deleted tool directory) Claude Code blocked every
  prompt, because Python exits 2 for a missing script and Claude Code reads exit 2 from
  `UserPromptSubmit` as a block (observed in the real host). An existing registration of
  the old command is upgraded in place with a backup; re-running adds no second hook.
- `install.py --adapter claude-code` refuses an unmergeable `.claude/settings.json`
  (invalid or non-UTF-8 JSON, wrong nesting, a directory, or a `.claude` that is a file)
  before copying anything: one ABORT line on stderr, exit 2 (was: exit 0 after the tool
  had been copied, or a traceback). `--adapter codex` refuses a `.codex` that is a file
  the same way.
- A keyed `send` refused at the scope stage (exit 3) removes its own key's orphaned marker
  left by a killed earlier attempt, so `status` no longer reports `ORPHAN_IDEM_MARKER`
  forever (PROTOCOL.md section 31). A live holder's marker, a resolved one, an old-format
  one and one for other content are never touched.
- A `PARTICIPANTS.md` that is not UTF-8 makes `send`, `list`, `inbox`, `ask` and
  `capability` refuse with one line naming the file, exit 2, instead of a traceback.
- `install.py`: a hook settings file whose `hooks` or `UserPromptSubmit` value has the
  wrong type is refused with one line instead of a traceback.
- `test_handoff.py` and `test_publication.py` run under `python -I` (isolated mode drops
  the script directory from `sys.path`, so their `check_*` imports failed); they insert
  their own directory first, as the other tests do. CI, which runs them without `-I`, is
  unchanged.
- `send` with `AGENT_MAIL_DIR` set to a path the OS refuses to stat (too long) printed a
  traceback; it is now `NO MAILBOX at <path>`, one line, exit 2, nothing written
  (PROTOCOL.md section 38).
- An `AGENT_MAIL_PROJECT` the OS refuses to stat printed a traceback from every command;
  it is now ignored, as a value naming no directory always was.
- The text views (`list`, `inbox`, `show`, `status`) printed a traceback, after a partial
  listing, for a subject, path or body in another alphabet when stdout could only carry
  ASCII (`PYTHONIOENCODING=ascii`, a C locale); each character stdout cannot carry is now
  printed as its `\x`, `\u` or `\U` escape, exit 0. `--json` output was ASCII already
  and is unchanged.
- `--help` with stdout closed printed the help on stderr; it now prints nothing and exits
  0, as every command does when stdout is gone (section 35).

## [0.4.0] - 2026-10-11

Two security fixes in the claim path, lost-mail fixes for fan-out, a faster send,
machine-readable listings for agents, complete help, and a release pipeline whose
scan job installs nothing. Existing exit statuses, message formats and
`status --json` keys are unchanged; `list`/`inbox` abbreviations `--l`, `--li` and
`--t` are now ambiguous and refused.

### Security

- A claim no longer follows a symbolic link planted at its scope lock name: the link's
  target was emptied and overwritten with a process id, or created. The claim now fails
  with one line (ELOOP, exit 1); a lock file with a second hard link is locked but never
  written to; a FIFO at the name is refused with exit 2 on every platform (PROTOCOL.md
  section 36, part a).
- A hand-written file reusing a message's id can no longer displace it: among files
  sharing an id, the one named `<id>-...` is admitted. Before, a held claim could be
  quarantined and a rival's claim on its scope succeed (36b).
- The CI and release jobs that read the forbidden-name secret install nothing: the
  publication scan runs in its own job (`publication` in `ci.yml`, `scan` in
  `release.yml`) with `python -I -S`, on artifacts built by a separate job.
- The scan also covers the tree of every tag, so a file that an earlier release shipped
  and a later commit removed is found.
- The job that writes release artifacts installs only `requirements-build.txt` (`build`,
  `packaging`, `pyproject_hooks`, each pinned by SHA-256) and runs no test;
  `scripts/repro_build.py` checks the setuptools wheel against the SHA-256 recorded in
  `build-constraints.txt` and lets the build install nothing else.
- CodeQL code scanning of the Python sources and the workflow files (`codeql.yml`).

### Added

- `list`: `--type`, `--status`, `--scope`, `--since`, `--limit N` (the newest N, still
  oldest first, with a `# N older message(s) not shown` line), `--now` and `--json`.
  `inbox`: `--type`, `--since`, `--limit`, `--now`, `--json`. `show <id> --json`, which
  carries the body under `body_untrusted`. A listing never contains a body (PROTOCOL.md
  section 37).
- `inbox` without `--to` shows the inbox of `AGENT_MAIL_IDENTITY`.
- `--help` lists every command (`send` and `show` were missing), the environment
  variables and the exit statuses; every command and flag has help text (section 38).
- A `hint:` line on stderr after `NO MAILBOX at <path>` and after `REJECT` lines;
  `doctor` names the next step under `canary: MISSING_OR_STALE` and `store: MISSING`.
- `docs/pypi-publishing.md`: the prepared plan for publishing to PyPI. The project is
  still not on PyPI and nothing uploads there.
- `test_cli.py`; `test_publication.py` checks the workflow files (no install in a job
  that references a secret, the required scan cannot be skipped, actions GitHub-owned
  and pinned).

### Changed

- `send` without `--supersedes` or `--scope` no longer reads the mailbox: 4.1 s to
  0.04 s at 100,000 messages (36d).
- `release.yml` has four jobs: `build`, `scan`, `test` (the test list and `twine check`),
  `publish`. `publish` runs only if the other three succeeded.
- BUILDINFO has a `setuptools_wheel` line with the wheel's SHA-256. Project URLs
  `Documentation`, `Changelog` and `Source` are added, which changes the artifact hashes
  of builds from this version on.
- `list --l`/`--li` and `list`/`inbox --t` (prefix abbreviations of `--live` and `--to`)
  are now ambiguous with `--limit` and `--type` and are refused. Full flag names are
  unaffected.
- `inbox requires --to <identity>` now ends ` (or set AGENT_MAIL_IDENTITY)`.

### Fixed

- `ask --to a,b` and `send --to <alias>` with the body on standard input deliver it to
  every recipient (before: the first only, or nobody). A fan-out checks every recipient
  before writing any, and stops when interrupted (36c).
- Two senders of one key with the same process id no longer share a marker temp file
  (36e).
- `send` with standard input closed, or carrying bytes that are not UTF-8, printed a
  traceback; it now refuses with one line and exit 2.
- Tracebacks from arguments: `canary --hours` that is not a finite number, `status --now`
  at an instant UTC cannot represent, `verify` with an unusable `--repo` or evidence
  path, and a read-only command whose `AGENT_MAIL_DIR` the OS refuses to stat.
- `status --json --stalled-hours nan|inf` printed `NaN`/`Infinity`, which is not JSON;
  now refused.
- `ask --to ","` exited 0 having sent nothing; now a usage error.
- `show`/`verify` echo a long or non-printable id quoted, on one line.
- The pickup adapter's reply hint names `--reply-to` and `--body-file`.
- Help typo "remasure".
- `test_hardening.py` scenario 10 no longer asserts that an owner's renewal wins a round
  it shares with rivals, which the lock does not guarantee.

## [0.3.1] - 2026-10-11

A new distribution name, and a correction to 0.3.0. The `agent-postbox` command,
the `agent_mail` module and mailboxes are unchanged.

### Packaging

- The distribution is now named `tyko84-agent-postbox` (was `agent-postbox`). An
  unrelated project owns `agent-postbox` on PyPI, and pip treats two distributions
  of one name as the same package, so `pip install --upgrade agent-postbox` could
  replace this tool with that one. Release files are now named
  `tyko84_agent_postbox-X.Y.Z-...`. This project is still not on PyPI.
- Upgrading from 0.3.0 or earlier: run `python -m pip uninstall agent-postbox`
  first, then install from a clone or a release wheel by path. Both names own
  `agent_mail.py` and the `agent-postbox` command, so uninstalling the old name
  after installing the new one deletes both; `python -m pip install
  --force-reinstall <wheel or clone path>` puts them back.

### Repository

- The repository's history was re-created on 2026-10-11 with one test file in the
  first commit corrected. Every commit id changed; file contents at every later
  commit, and the 0.3.0 release files and their checksums, are identical.

### Fixed

- Correction to 0.3.0: "`list | head` now exits 141 quietly" was true only for
  `python agent_mail.py` and `python -m agent_mail`. The installed `agent-postbox`
  command still printed a `BrokenPipeError` traceback and exited 120 (or 1). It is
  now quiet on every entry point, including `agent_mail.main(argv)` called from
  another program (PROTOCOL.md section 35).
- Read-only commands (`list`, `inbox`, `show`, `status`, `doctor`, `latency`,
  `verify`, `capability`, `--help`, `--version`) whose reader goes away stop at once
  and exit 141; what was delivered is always a prefix of the full output.
- `send`, `ask` and `canary` whose reader goes away finish and exit with the status
  of the send (0 once the mail is published) instead of 120 or 141. With unbuffered
  output, `ask` to several recipients used to stop after the first, and a keyed
  retry reported `send failed: cannot write to the mailbox: Broken pipe`.
- `python agent_mail.py send ... >&-` exited 1 with an `AttributeError` after a
  successful send (introduced in 0.3.0).
- The pickup adapter exited 120 with a line on stderr when its reader went away; it
  exits 0 silently, as its contract says.
- A diagnostic whose stderr reader is gone no longer turns the exit status into 120,
  and with stderr closed (`2>&-`) it is no longer printed on stdout.

### Changed

- Exit status 141 is documented for read-only commands (README table, PROTOCOL.md
  section 35).
- The `EXPECT_QUIET_SIGPIPE` test switch is gone: the check always runs, including
  in the release gate, and `test_packaging.py` exercises the installed command.

## [0.3.0] - 2026-10-10

Keyed sends and claim ownership are now exact, the publication scanner no longer
passes what it did not read, and builds are reproducible. Callers see new exit
statuses in a few cases (listed under Changed); mailboxes need no migration and
0.2.2 markers are still honoured.

### Added

- `send --key-wait SECONDS` and `AGENT_MAIL_IDEM_WAIT` (0 to 60, default 5; refused,
  never clamped): how long a keyed retry waits for a reservation it finds unresolved
  (PROTOCOL.md section 31).
- `status --json`: `scope_locks`, `idem_markers`, `takeover_mutexes`,
  `foreign_supersedes`, `dangling_replies` and a `problems` list of fixed codes with
  counts. Schema number stays 1 (keys are only added). Text `status` and `doctor`
  print the same counts; `doctor` names the files. None is a new reason for `doctor`
  to fail (PROTOCOL.md section 33).
- PROTOCOL.md sections 31 to 33, including one table of exit statuses for every
  command; the README carries the same table.
- `send` prints a note on stderr when a renewal drops or changes `--scope`, because
  that releases the old scope at once.
- `scripts/repro_build.py`: reproducible, ownerless builds. Exports a commit with
  `git archive`, builds with `SOURCE_DATE_EPOCH` (default: the commit's committer
  time) and rewrites the sdist so its tar headers carry no builder uid, gid, user or
  group name, every timestamp is `SOURCE_DATE_EPOCH` and the gzip header has no
  timestamp or file name. Prints the SHA-256 of both artifacts; `--check` builds
  twice and fails if the runs differ. CI runs it and scans its output.
- `test_field.py`: a synthetic four-agent end-to-end run through the CLI (claim
  race, renewal, expiry and takeover; keyed sends including a killed sender;
  ASK/ANSWER with a retried answer; 100 concurrent sends with two readers).
- `test_packaging.py` pins the reproducible-build properties and checks that no
  member of any built artifact contains a build-machine path.
- `status` names who holds what (PROTOCOL.md section 34): `status --json` gains
  `claims_held` and `claims_attention` (at most 200 rows each, with
  `claims_held_truncated` / `claims_attention_truncated`), `contested_scopes` and
  `scope_filter`; text `status` prints a held-claims table; `status --scope SCOPE`
  filters both lists; new problem code `CONTESTED_SCOPE`; `doctor` prints
  `contested_scopes`. Schema stays 1. A consumer that checks for an exact top-level
  key set must add the six keys.
- `scripts/repro_build.py` writes `SHA256SUMS` (`--sums FILE`), verifies a download
  by rebuilding (`--verify DIR`) and prints a `BUILDINFO` block; the build backend is
  pinned by `build-constraints.txt` (setuptools 84.0.0), read from the commit being
  built. CI builds the same commit on Linux and macOS and compares the bytes.
- `check_publication.py --dist` rejects builder identity in archives, with or without
  a forbidden list: tar owner names other than empty or `root`, non-zero uid/gid,
  unexpected pax records, a gzip header with a file name, comment, extra field or
  timestamp, zip extra fields other than zip64, zip comments, unusual zip attributes,
  operating-system litter such as `__MACOSX/` and `.DS_Store`, and a
  `direct_url.json` with a `file:` URL (rules `builder-*`). One line per artifact and
  rule, value never printed. A plain `python -m build` sdist now fails the scan;
  `scripts/repro_build.py` output passes.
- `check_publication.py --dist` reads a `SHA256SUMS` file beside the artifacts and
  verifies it against them (`checksum-malformed`, `checksum-mismatch`,
  `checksum-unlisted`).
- `.github/workflows/release.yml`: pushing an annotated tag `vX.Y.Z` on `main` runs
  the tests, builds the wheel and sdist reproducibly, runs the publication scan, and
  attaches both with `SHA256SUMS` and a build provenance attestation to a draft
  GitHub release. Assets are never replaced and the tag is never moved.
- Tests: `test_field.py` has bounded waits, a watchdog, `FIELD_DEBUG=1` and per-phase
  time budgets scaled to the machine (73 checks); `test_hardening.py` pins the
  `status` probe window; `test_adversarial.py` kills sixteen keyed writers at
  arbitrary moments and restarts them; `stress_test.py` kills a sender mid-publish.

### Changed

- `send --key` holds a kernel lock (`flock`) on its idempotency marker while it
  publishes. A retry after a sender that died is taken over immediately instead of
  after five seconds. A retry that outlasts its wait against a live sender exits 3
  ("being sent by another process right now; retry") and writes nothing.
- Markers have a third line (`flock`). Markers written by 0.2.2 are still honoured
  with the previous algorithm and default wait; 0.2.2 reads the new markers.
- SIGTERM and SIGINT during `send` release the marker, scope lock and temp file,
  print one line and exit 143 / 130.
- A CLAIM is superseded only by a CLAIM from its own sender when reading as well as
  at send. Ids are compared without regard to case everywhere (`reply_to`,
  `supersedes`, `show`, `latency`).
- A scope lock file that cannot be opened is reported as a write failure (exit 1),
  not as a missing `flock` (was exit 2).
- `check_publication.py` never reports a pass for content it did not read. Anything
  it was asked to cover but could not look inside is an `unscanned-<reason>` finding
  (exit 1): a gitlink, an unreadable file, a symlinked parent directory, a nested
  archive, an encrypted or special archive member, data after a damaged tar header,
  a shallow clone, a grafts file, a tag on a blob, and any file in the `--dist`
  directory that is not an sdist or wheel. A run that would scan nothing exits 2.
- The tracked scan also reads the staged and `HEAD` blobs where they differ from the
  work tree, scans the text of a tracked symlink without following it, and no longer
  reads through a symlink that replaced a tracked file or directory.
- `--dist` opens every wheel, zip, egg and tar variant in the directory and also
  scans artifact file names, tar link targets, owner and group names and pax
  headers, zip comments, and every member of a zip that holds duplicate names.
- `--git-log` ignores replace refs. Forbidden patterns are normalised like the
  scanned text; a pattern list that could never match exits 2. An allowlist glob of
  only `*` is rejected and the number of suppressed findings is printed. A pass
  prints what was scanned.
- `scripts/preflight.sh` scans the git log of every ref and tag (was `HEAD` only),
  builds through `scripts/repro_build.py`, runs `test_field.py`, and accepts a list
  path that contains spaces.
- CI: `test_field.py` joins the matrix; the packaging job checks the build is
  reproducible; on a push to this repository a missing forbidden list fails the
  publication job instead of downgrading it to the generic detectors.

### Fixed

- A keyed send whose original was alive but slower than five seconds was taken over
  and then published as well: two messages for one key.
- A sender that gave up removed whatever marker was at its key, including one a
  later sender had taken over.
- A hand-written message citing another agent's claim in `supersedes:` marked it
  superseded, after which a rival's `--scope` claim succeeded.
- An ANSWER citing an ASK's id in lower case left the ASK open with no error.
- `send` printed a traceback when the mailbox was read-only, full, or a lock or
  marker file was unreadable. It now prints one line and exits 1.
- `doctor` crashed when a hidden temp file vanished or was a dangling symlink.
- `status` raised OverflowError on a hand-written `date` or `expires` that UTC cannot
  represent (year 1 or 9999 with an offset); it is now clamped.
- `list | head` ended with a `BrokenPipeError` traceback; it now exits 141 quietly.
- `test_field.py`: a failed burst writer could leave the readers looping for ever, and
  the renewal check depended on a one-to-two-second wall-clock margin.
- `test_publication.py`: temporary repositories no longer run background `git gc`,
  which could fail a run with `Directory not empty: 'pack'` during cleanup.

### Security

- A plain `python -m build` sdist records the builder's uid, gid, login and group
  names in its tar headers. Build anything you hand to others with
  `scripts/repro_build.py`.

### Documentation

- README and `docs/packaging.md` state prominently that this project is not on PyPI
  and that the PyPI distribution named `agent-postbox` is a separate, unrelated
  project: install from a clone or a wheel built from a clone.
- `docs/publication-safety.md`: the `unscanned-*` rules, the exit-2 conditions and a
  per-tag audit recipe. `docs/release-process.md`: required checks and how to verify
  them.

## [0.2.2] - 2026-10-10

Atomic scope locks, recovery tests and scanner follow-ups. The only runtime
change is how a scope lock is held (PROTOCOL.md section 30); exit codes and
messages are unchanged and there is no migration.

### Added

- `test_hardening.py`: recovery after an interrupted write, with a writer killed at
  each dangerous point of the publish path (before link, after link before the temp
  is removed, after the idempotency marker, holding a scope lock) and a truncated
  final file. The mailbox stays usable and no partial message is ever listed (#11).
- `check_publication.py --git-log` also scans tags: every tag name, and the tagger
  name, email and message of every annotated tag (location `tag:NAME`, allowlist path
  `tag/NAME`), whatever `REV` is given (#10).
- `check_publication.py` decodes tracked files and archive members that start with a
  UTF-16 byte-order mark as UTF-16 before matching (#10).
- `requirements-dev.txt` pins ruff, mypy, build and twine in one place; CI installs
  from it, and Dependabot (new `pip` entry, weekly, grouped) keeps the pins current.
  The publication job runs `twine check` on the built sdist and wheel (#10).

### Changed

- Lint tools: ruff 0.17.0 and mypy 2.4.0; twine 7.0.0 added (#10).
- `docs/publication-safety.md` states that the history scan reads commit and tag
  metadata only, never file contents of old commits, and how to audit releases (#10).

### Fixed

- Breaking a stale scope lock was not atomic: two claimants finding the same
  `.scope.*.lock` older than 60 seconds could both remove it (the second
  removing the first one's fresh lock) and both publish a claim on the same
  scope. The lock is now an exclusive `flock(2)` on the lock file, released by
  the kernel when the holder exits, so a dead claimant's lock is reclaimed by
  exactly one of any number of simultaneous claimants and a live claimant's
  lock is never removed, whatever its age (PROTOCOL.md section 30). Exit codes
  and messages are unchanged; a claimant that dies mid-claim no longer blocks
  its scope for 60 seconds. `selftest.py` pins this with real subprocesses (#12, #14).
- `selftest.py`: an invalid `# noqa` directive on a fixture line that current ruff
  warns about is now a plain comment (#10).

## [0.2.1] - 2026-10-10

Publication safety, CI hardening and documentation. Nothing in this release
changes runtime behaviour; there is no breaking change and no migration.

### Added

- `check_publication.py` / `test_publication.py` and a `publication` CI job: scans tracked
  files, built artifacts and git history for operator-supplied forbidden patterns and generic
  secret/home-path shapes (docs/publication-safety.md).
- `scripts/preflight.sh`: local pre-publication gate (tests, build, publication scan of tracked
  files, dist and git log); reads the forbidden list from `POSTBOX_FORBIDDEN` or
  `~/.config/agent-postbox/forbidden`.
- `test_publication.py` joins the CI selftest matrix.
- `docs/release-process.md`: how a release is cut, including the pre-release
  privacy preflight.

### Changed

- The `publication` CI job reads the forbidden list from the repository **secret**
  `POSTBOX_FORBIDDEN` (log-masked) instead of a plain-text variable; a missing value warns and
  runs the generic detectors only.
- `test_packaging.py` no longer embeds any real name; it uses synthetic placeholder terms and the
  same run-time mechanism as `check_publication.py`.
- `check_publication.py` prints an explicit warning when no forbidden list was supplied.
- CI: `actions/setup-python` 5 -> 7 and `actions/checkout` 4 -> 7 (Dependabot, #1 and #2),
  then pinned to commit SHAs with version comments; every job has a timeout, superseded
  pull-request runs are cancelled, checkouts no longer persist the token, `build` is pinned,
  and Dependabot groups action bumps into one pull request (#8).
- `check_publication.py` normalises text (NFKC, zero-width characters stripped) before
  matching and reports a literal split across string pieces as `forbidden-N-split`; a tracked
  file missing from the work tree is reported as `unscanned-missing`; `--git-log=--all` is
  unambiguous. The `publication` CI job exposes the secret only to the steps that read it and
  passes an empty allowlist from outside the checkout (#6).
- `test_packaging.py` always scans the built artifacts with a built-in synthetic term, so the
  forbidden-pattern path runs on every build and positive controls assert the exact redacted
  finding (#6).

### Fixed

- `check_publication.py`: a patterns or allowlist file with a UTF-8 BOM no longer silently
  loses its first entry, and an unreadable one exits 2 instead of crashing (#6).
- `test_adversarial.py`: the quarantine listing check ignored the `# mailbox:` header line,
  whose random temp-directory name could contain the marker it searched for; it was flaky on
  macOS in CI (#7).
- `CHANGELOG.md` listed 0.2.0 as unreleased; it was released on 2026-10-10.
- `CONTRIBUTING.md` now lists every check `ci.yml` runs (`test_adversarial.py`,
  `mypy` on `check_handoff.py`); the README's 30-second demo shows the tool's
  actual output.

## [0.2.0] - 2026-10-10

Everything since the `v0.1.0` tag. History was re-created from a clean tree for
this release and the earlier `v0.1.0` tag was not carried over, so `v0.2.0` is
the first tag in the current repository.

### Added

- `status [--json]`: read-only, deterministic observability (PROTOCOL.md section 29).
- Input hardening: header injection, control characters, path-like ids,
  symlinks/FIFOs/non-UTF-8/oversized files, and duplicate frontmatter keys
  are refused at send or quarantined at read. `test_adversarial.py` (80 checks).
- A clear exit-2 message on Python older than 3.10.
- Lease validation on `send` for CLAIMs: `--expires` must be ISO-8601 with a
  time and an explicit zone, in the future, and at most 168 hours out
  (PROTOCOL.md section 5).
- `--scope` on CLAIM: one winner among simultaneous claimants for the same
  resource, decided atomically under an exclusive lock; a loser is refused with
  exit 3. The same sender may re-claim its own scope.
- `--key` on `send`: retry-safe, idempotent sends per (sender, recipient, key);
  identical retry returns the original id, same key with different content is
  refused (exit 2), simultaneous retries yield one message (section 5a).
- Quarantine: files with an unknown type, missing `from`/`to`, an unparseable
  `date`, or a duplicate `id` are reported (`doctor`, `REJECT` line) and never
  delivered as live mail. Nothing is deleted.
- `doctor` reports `NO_EXPIRES`, `MALFORMED_EXPIRES` and `TOO_LONG` leases and
  lists leftover `.*.tmp` files older than ten minutes (`stale_tmp`).
- `test_hardening.py`, run in CI, with positive controls and no sleeps.
- Packaging: `pyproject.toml` (installable with `pip`, console script
  `agent-postbox`, version read from `agent_mail.__version__`), an explicit
  sdist allow-list, and `test_packaging.py`.
- Handoff packet: PROTOCOL.md section 28, `handoff_template.md` and the
  stdlib-only validator `check_handoff.py`, with tests.
- `test_readme_examples.py`: executes every `bash runnable` example in the
  README in a scratch mailbox and checks that section cross-references in the
  docs resolve to real PROTOCOL.md headings.
- `CHANGELOG.md`, `docs/`.

### Changed

- Only a CLAIM's own sender can supersede it, and the superseded id must exist.
  To object to someone else's claim, send a DISPUTE.
- A CLAIM whose expiry is missing or unreadable is no longer a hold; it stays
  visible rather than being dropped.
- README rewritten: install options, quickstart, claim and finding semantics,
  security guarantees and limitations. Stale section references corrected.
- Documented that the tool is POSIX-only (Linux and macOS in CI).

### Fixed

- Header injection through newline in `--subject`; duplicate `from:` keys no longer
  let the last one win; non-UTF-8 and FIFO files no longer crash or hang `list`.
- `list --live` was quadratic (80s on 10k messages); now a few seconds.
- `send` fsyncs the directory after publishing (POSIX).
- `test_hardening.py` no longer uses a backslash inside an f-string expression
  (a syntax error before Python 3.12).

### Known limitations

- Breaking a stale (older than 60 seconds) scope lock is not atomic; see the
  README "Security and limitations".

[Unreleased]: https://github.com/tyko84/agent-postbox/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.5.0
[0.4.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.4.0
[0.3.1]: https://github.com/tyko84/agent-postbox/releases/tag/v0.3.1
[0.3.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.3.0
[0.2.2]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.2
[0.2.1]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.1
[0.2.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.0
