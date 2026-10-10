# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, minor versions may change
behaviour).

## [Unreleased]

### Fixed

- Breaking a stale scope lock was not atomic: two claimants finding the same
  `.scope.*.lock` older than 60 seconds could both remove it (the second
  removing the first one's fresh lock) and both publish a claim on the same
  scope. The lock is now an exclusive `flock(2)` on the lock file, released by
  the kernel when the holder exits, so a dead claimant's lock is reclaimed by
  exactly one of any number of simultaneous claimants and a live claimant's
  lock is never removed, whatever its age (PROTOCOL.md section 30). Exit codes
  and messages are unchanged; a claimant that dies mid-claim no longer blocks
  its scope for 60 seconds. `selftest.py` pins this with real subprocesses.

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

[Unreleased]: https://github.com/tyko84/agent-postbox/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.1
[0.2.0]: https://github.com/tyko84/agent-postbox/releases/tag/v0.2.0
