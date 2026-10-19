# Protocol and CLI compatibility: what is stable, what is not

Status 2026-10-17: a reader's inventory of what a second implementation or an adapter can rely on, from
reading `PROTOCOL.md`, `agent_mail.py --help`, and running it on Linux (WSL, Python 3.12) and, against the Windows port
branches, on Windows 11 (Python 3.12). It is a proposal for what to call stable, not a
promise the project has made. Nothing here changes behaviour.

## 1. What is versioned today

| Thing | Where it lives | Value now | Notes |
|---|---|---|---|
| Tool version | `agent_mail.py --version`, `__version__` | `agent-postbox 0.5.0` | The only version number a reader can ask for. |
| Protocol version | nowhere | none | `PROTOCOL.md` has no revision number and message files carry no `protocol:` header. "The protocol" is whatever the tool version's `PROTOCOL.md` says. |
| JSON schema | `"schema"` key in `list`, `inbox`, `status` and `show --json` | `1` | Additive keys have been added without a bump (a row has "fourteen keys since section 39"), so `schema` means "the shape family", and consumers must ignore unknown keys. |
| Message type set | `TYPES` | ASK, ANSWER, NOTICE, CLAIM, DISPUTE | Closed. An unknown type is quarantined by every current reader (`REJECT ... unknown type`, `list` exits 2). |
| Message file | `<ULID>-<slug>.md`, `---` frontmatter, blank line, body | | Header keys are lower-case; unknown keys are ignored; a duplicate key quarantines the file. |

Consequence: there is no negotiation. Two tool versions sharing one mailbox rely on the additive rules in
section 5.

## 2. The contract an adapter can rely on (proposed as "stable")

These are backed by the spec and by tests, and `conformance/run_conformance.py` checks the ones marked (C).

1. `send` exits 0 and leaves one file `<id>-<slug>.md`, UTF-8, LF, beginning `---` (C). This CLI also prints
   `wrote <path>` and `id: <ULID>` on stdout: implementation-specific, and `wrote <path>` is not checked.
2. Exit codes (the `--help` table): 0 done; 1 not found or the OS refused; 2 refused or unhealthy (bad input,
   no mailbox, quarantined files in `list`/`inbox`/`latency`, `doctor` FAIL); 3 contended, nothing written,
   safe to retry; 130/143 interrupted; 141 the stdout reader went away. A refusal writes nothing (C for 1, 2, 3).
3. A missing mailbox exits 2, is never created, and is distinguishable from an empty one (C). The words
   `NO MAILBOX` (and `REJECT` for a quarantined file) are this CLI's own: implementation-specific.
4. `--json` on `list`/`inbox`: schema 1, the top-level keys and the row keys of section 37 (C); `status --json`
   schema 1 (C, three keys only).
5. Statuses are derived: `open`, `answered`, `held`, `expired`, `blocked`, `superseded`, `fresh`, `stale`
   (section 19); `--now ISO` makes the output a function of the files and the instant (C: expiry).
6. CLAIM: expiry mandatory, at most 168 h; a second sender on a held scope string is refused (exit 3); the holder may
   re-claim (C).
7. An ASK owes a reply until a later message cites its id in `reply_to` (C).
8. `--key` makes a send idempotent: same content returns the original id, exit 0, one file (C).
9. Input policy: control and format characters, non-ASCII identities and oversize fields are refused at `send`
   with exit 2 (C); on read, non-UTF-8, symlinks, non-regular files, duplicate keys and oversize files are
   quarantined with a `REJECT` line on stderr (C for non-UTF-8).
10. Body arrives as UTF-8 through `--body-file PATH` or `--body-file -` wherever the interpreter's stdin is UTF-8 (a
    POSIX locale; the Windows ANSI code page needs the strict-UTF-8 stdin change); a
    non-UTF-8 body is refused, not guessed (C: UTF-8 round trip).

## 3. Not a contract

Help wording ("not a contract; that it is complete is", section 38); the text of `list`, `inbox`, `status`
and `doctor` lines; the hook's prose; the slug in a file name; the order of keys inside `--json`; the exact
text of a refusal; the format of `mailbox` (a platform path, backslashes on Windows); the wait and retry
timings; the names of hidden files (`.scope.*.lock`, `.idem.*`) beyond "hidden files in the mailbox that
`status` and `doctor` account for".

## 4. Windows-specific deviations from the POSIX contract

| Area | POSIX | Windows (Windows port, pending) | Effect on an adapter |
|---|---|---|---|
| Locks | `flock(2)` | `LockFileEx` through `msvcrt.locking` on a byte at offset 2^40; exclusive only, a "shared" probe takes and drops the exclusive lock | Windows and POSIX processes must not share one mailbox through a network or `/mnt/c` mount; not tested and listed Unsupported in `docs/windows.md` |
| Rename over an open file | `os.replace` | `SetFileInformationByHandle` with POSIX-rename semantics, needs Windows 10 1809+ | Older Windows is unsupported; not tested |
| Exit 130/143 | SIGINT/SIGTERM | SIGTERM is not deliverable; `TerminateProcess` leaves whatever exit code the killer chose (1 in this repo's tests); Ctrl+C handling not verified here | Do not treat a kill as 143 |
| Exit 141 | SIGPIPE-style | synthesized from EPIPE/EINVAL on a closed pipe | Same number, different cause |
| Identity binding (section 40) | uid of file owner | unsupported: `st_uid` is 0 for every file | `authorship: CANNOT_VERIFY` always |
| Case | case-sensitive filenames | case-insensitive; the tool sorts names itself, not with `Path` order | A scope string is still case-sensitive: `SRC/X.py` and `src/x.py` are two scopes |
| Separators in scope | `/` | `\` also works for the file but is a different scope string | Agree on one spelling; a scope is a label, not a path |
| stdin default encoding | locale | forced strict UTF-8 (pending stdin change) | A POSIX caller on a non-UTF-8 locale who relied on the locale decoding now gets exit 2 for non-UTF-8 input; flag in CHANGELOG |
| Hook stdout | UTF-8 locale | ANSI code page, CRLF, non-representable characters backslash-escaped | Hook text is for a model, not parseable |
| Hook command | `python3 "$CLAUDE_PROJECT_DIR/..." \|\| true` | the same text; works under Git Bash, not cmd.exe or PowerShell 5.1 | A failed hook looks like an empty inbox; use the canary |
| FIFOs, `/dev/stdin` as body | accepted as `--body-file` paths where regular | unsupported | Use `--body-file -` |
| Directory fsync | yes | skipped | Durability of the directory entry after power loss unverified |

## 5. What would need a versioned contract change, and what stays compatible

| Change | Class | Why |
|---|---|---|
| New optional flag; new optional header key; new `--json` key; new line in `doctor` or `status` text | Compatible | Readers ignore unknown header keys; consumers are told to ignore unknown JSON keys. Precedent: section 39 added `result`. |
| New exit code for a new situation | Compatible if it only replaces a crash | Otherwise versioned: callers branch on 0/1/2/3. |
| New message type | Versioned | Old readers quarantine it and `list`/`inbox` exit 2. Needs a reader-first release or a protocol number. |
| Changing what "same scope" means (normalising case, separators, NFC) | Versioned | Lock names are `sha256(scope)`; two versions would disagree about who holds what. |
| Tightening the identity alphabet at read | Versioned in effect | Existing odd files would become REJECT lines and `list` would start exiting 2. |
| Changing the `id:` line, the file name pattern, or the ULID format | Versioned | Every adapter parses it. |
| Changing the Windows lock primitive or offset | Versioned | Old and new processes would no longer exclude each other. |
| Hook output encoding or wording | Compatible with the contract, but retest hosts | It is not parsed by the tool; a host may be. |

Recommendation (minimal): (a) state in PROTOCOL.md that the protocol revision is the tool's minor version and
that `--json` `schema` is the shape family; (b) change "exactly these thirteen keys" to "at least"; (c) add one
sentence that readers must ignore unknown header and JSON keys; (d) before the first new message type, decide
whether to add a `protocol:` header. None of this needs code.

## 6. Undocumented assumptions found

* ULID order is time order, so a skewed clock reorders ids; `fresh`/`expired` read the local clock.
* The mailbox is on a local filesystem with working locks; `status`/`doctor` do not detect a share.
* Two agents that choose the same identity are indistinguishable; nothing checks.
* A file name's first 27 characters decide which of two files with one id is the real one (section 36).
* Subjects are NFC-normalised; scopes, identities and keys are not.
* `python3` on PATH is the interpreter the committed hook will use on every machine that clones the repo.

## 7. Conformance suite for a third-party adapter author

`python conformance/run_conformance.py [--tool PATH | --cmd PROGRAM ...] [--protocol-only]` runs 49
always-on checks (plus up to 18 optional ones) in about 15 to 30 seconds against a throwaway mailbox (stdlib, no network, no model), in three
groups reported separately: 45 protocol-level (what `PROTOCOL.md` requires of any implementation), 4
implementation-specific (the `--version` string, the `id:` line, the words `NO MAILBOX` and `REJECT`),
and optional checks (`owed_truncated`, the `inbox` scheduler exit status) that are skipped when the tool lacks the feature (a tool that answers the flag in an unexpected way is treated as having it),
when the tool lacks the feature. `docs/adapter-conformance-guide.md` says how to run and report it. It
passed on Windows 11 (against the Windows port branches, not yet merged) and on Ubuntu 24.04 under WSL. A program that
exits 0 and does nothing fails 39 of the 45 protocol-level checks; the other 6 pass vacuously (they check only an exit status, such as `CLAIM` exiting 0, a lapsed lease being
reclaimable, or an absent reply), which is why a pass is evidence of these behaviours and
nothing more. The header of the runner lists what each group asserts, and two questions it deliberately
does not assert because the project has not decided them: how an unreadable mailbox is reported by
`list`/`inbox`, and which rows `inbox --limit` drops.

What it does not cover, deliberately: the host side (that a model sees or obeys anything; use the seven
conformance points of `docs/adapter-contract.md` for that), concurrency beyond two small races (selftest
and `stress_test.py`), `verify`, identity binding, aliases and capabilities, `doctor`, the hook script,
and time-dependent behaviour beyond `--now` and one short lease. It is not a certification and should
not be called one. An adapter that reimplements the protocol rather than calling the CLI should add:
the file shape in section 2, the quarantine rules, and a concurrent-claim test with real processes.

## 8. macOS: assumptions nobody has tested

Not run on macOS by anyone so far. Reading the code and the platform, these are the assumptions to check
first; none is a known failure.

* `python3` on PATH may be the system Python, which can be older than the 3.10 the tool needs; the
  committed hook and the installer's interpreter choice depend on it.
* Locks are `flock(2)` on a local volume. A mailbox inside an iCloud Drive, Dropbox or network-mounted
  folder (Desktop and Documents are synced by default for some users) has no reliable locks.
* `fsync` on macOS does not force the drive's cache to stable storage (that needs `F_FULLFSYNC`), so
  the durability the Linux notes describe is weaker after power loss.
* The default volume is case-insensitive (like Windows) and normalises Unicode in file names; scope
  strings, identities and keys stay case-sensitive and un-normalised, and the file-name pattern keeps a
  fixed ASCII ULID prefix, but a non-ASCII slug may come back from a listing in a different form.
* Temporary directories live under `/var/folders`, a symlink to `/private/var`; a mailbox path compared
  as text may differ from its resolved path.
* Hard links (`os.link`, used to publish a message without overwriting) are available on APFS and HFS+;
  other volume types (exFAT, some network shares) refuse them.
* Process limits: the default open-file limit is low (256 in a fresh shell on older releases), which a
  large fan-out test can reach.
