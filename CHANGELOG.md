# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, minor versions may change
behaviour).

## [Unreleased]

Nothing yet.

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

[Unreleased]: https://github.com/tyko84/agent-postbox/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.3.0
[0.2.2]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.2
[0.2.1]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.1
[0.2.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.0
