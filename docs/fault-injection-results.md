# Fault injection: what happens when a send is killed at every write step

Written 2026-10-16 against `main` at c832fe1. Crash and interleaving safety had been checked
by example (`test_hardening.py`, `test_field.py`, `stress_test.py`: SIGKILL at four chosen
points). This pass enumerates every write step of `send` from the code and kills a real
process at each one. Everything below was run; where something was not, the last section says so.

Rerun it: `python3 stress_test.py --faults full` (about 6 minutes; every number below comes
from one such run, exit 0). The default `python3 stress_test.py --faults ci` runs a fixed
subset in about 30 to 50 seconds and is part of CI through `stress_test.py`.

## Method

* **Child.** The child runs the real `agent_mail.main(argv)` with the write-side primitives
  wrapped when called from `agent_mail` or `pathlib`: `os.open` for writing, `os.write`,
  `os.fsync`, `os.ftruncate`, `os.link`, `os.replace`, `os.rename`, `os.unlink`,
  `fcntl.flock`, `os.close` of a descriptor the tool opened, and `open()` for writing. Each
  call is two points, **pre** (the call has not happened) and **post** (it has, and nothing
  after it). The child numbers the points and, at the one it was asked for, creates a
  ready-file and blocks. This is the `BEFORE_PUBLISH` technique of `test_field.py`, applied
  to every step.
* **Points.** A dry run of the same send lists its points in order. The matrix then runs
  one fresh mailbox per point: set up, spawn, wait for the ready-file, **SIGKILL** (or, in the
  second pass, **SIGTERM**), check, run the recovery, check again.
* **Reader of the result.** The mailbox is inspected by code that does not use
  `agent_mail` (it parses the header and checks that the body ends intact), so a bug in the
  tool's own reader cannot hide a torn file.
* **Checks after every kill.** (1) No message file is torn or truncated. (2) `list --json`,
  `status --json` (valid JSON), the pickup hook and, in the full run, `list`, `inbox`,
  `status` and `doctor` finish with no traceback and exit 0 or 2. (3) Those readers leave every
  file, size, mtime and inode of the mailbox unchanged (read-only). (4) A later correct
  send succeeds, never with "being claimed right now" from a dead holder (PROTOCOL.md section
  30), and a keyed retry takes over at once (under 3 s with `--key-wait 5`; section 31).
  (5) A keyed retry yields exactly one message per recipient, a second retry is a no-op, and
  the same key with other content is refused (exit 2). (6) Exactly one holder of a scope: a
  rival is refused (exit 3) when a claim landed or the owner retried, and admitted (exit 0)
  when nothing landed. (7) A renewal or release killed part-way never frees the scope by
  accident: the old claim holds until the replacement lands (for an alias fan-out, until every
  copy is superseded). (8) After recovery `status --json` has no problem code and no
  `.scope.*` lock file is left.
* **Positive controls.** The harness was run against deliberately broken copies of the tool
  and failed each time: a non-atomic publish (the plain, keyed and keyed-claim scenarios fail), a retry that
  never takes over a dead holder's key marker (five keyed scenarios and 4 of the 24 `key-same` and `fanout-key` seeds fail),
  and a scope lock that does not call `flock` (5 of 12 seeds of `scope-keyed` and 3 of 12 of
  `scope` report "two winners"). A fourth mutant (the marker kept after an orderly give-up) is
  **not** caught by the kill matrix; it affects only a send that gives up by itself, which
  `test_hardening.py` covers.

## The ordered steps of `send`, from the code

Traced undisturbed (a "step" is one wrapped call; points are pre and post of each):

| Send | Steps in order |
| --- | --- |
| plain | open temp for write, fsync, link into place, fsync directory, unlink temp (10 points) |
| `--key` | **reserve:** open marker temp, flock, write, fsync, link marker into place, unlink marker temp; **publish** as above; release closes the marker descriptor (24 points) |
| scoped CLAIM | **lock:** open lock file, flock, ftruncate, write pid; check rivals; **publish** as above; close; unlink lock, close lock descriptor (22 points) |
| scoped CLAIM with `--key` | lock, reserve, publish, release marker, unlock (36 points) |
| alias fan-out | the above once per writer, in order; 3 writers: 72 points keyed, 108 keyed and scoped |
| `--supersedes` renewal or release | same as a keyed scoped CLAIM (renewal 36), release has no scope (24); through an alias the same per copy (108 and 72) |
| takeover after expiry | a lapsed lease is not a lock, so it is a keyed scoped claim by another sender (36, unkeyed 22) |
| takeover of a dead sender's leftovers | the retry replaces the orphan marker with `rename` over it and reuses the orphan scope lock; killed at each of its own steps (key 42, scope 22, both 54) |

## Results: SIGKILL at every point (688 points, 15 scenarios)

Each row: points killed, points where the child was reached and parked, and the result of
every check above. No row failed; no point was "not reached".

| Scenario | Points | Reached | Result |
| --- | --- | --- | --- |
| plain send | 10 | 10 | pass |
| keyed send | 24 | 24 | pass |
| scoped CLAIM | 22 | 22 | pass |
| scoped CLAIM, keyed | 36 | 36 | pass |
| keyed send to an alias of 3 | 72 | 72 | pass |
| keyed scoped CLAIM to an alias of 3 | 108 | 108 | pass |
| renewal (`--supersedes`, new expiry), keyed | 36 | 36 | pass |
| release (`--supersedes`, no scope), keyed | 24 | 24 | pass |
| renewal through an alias of 3 | 108 | 108 | pass |
| release through an alias of 3 | 72 | 72 | pass |
| takeover of a lapsed lease, keyed | 36 | 36 | pass |
| takeover of a lapsed lease, unkeyed | 22 | 22 | pass |
| retry killed while taking over a dead sender's key marker | 42 | 42 | pass |
| claim killed while taking over a dead sender's scope lock | 22 | 22 | pass |
| keyed claim killed while taking over both | 54 | 54 | pass |

What the kill leaves, as seen at these points: before the link a message does not exist and
the hidden temp file stays (`stale_tmp` once old, never read as mail); after the link the
message is whole and a keyed retry answers `duplicate of`; a dead holder's `.scope.*.lock`
and `.idem.*` marker are inert and cleaned by the next claim or retry. A killed unkeyed send
leaves nothing to retry against, so retrying it publishes a second message by design
(section 5a); that is not a failure and is not counted as one.

## Results: SIGTERM at every point (666 points, 14 scenarios)

`send` turns SIGTERM into an orderly give-up (section 31). The same matrix with SIGTERM
(`orphan-scope-takeover` omitted: it only parks a claimant) gives, in every one of the 666
points, the same invariants (all pass: the recovery works, nothing torn, readers clean). In
addition:

* **86 of 666** signals were absorbed, correctly: they arrived while the release in
  `finally` was running, where the handler is told not to interrupt (the harness then killed
  the child).
* **231 of 666** points left **inert residue** after an orderly exit, because a Python signal
  handler can run between a successful call and the line that records it: a `.scope.*.lock`
  file when the signal lands inside `_scope_lock` before it returns (the kernel lock is gone
  with the process; the file is removed by the next claim, section 30); a key marker (or its
  temp) when it lands after the `link` and before `_idem_claim` returns, which is exactly
  the marker a SIGKILL leaves and which the next retry takes over at once; a hidden temp
  file hard-linked to a message already published, when it lands inside `_publish`'s own
  cleanup. None blocked a later send, none produced a duplicate. Section 31's sentence "temp
  files are removed" is therefore true of an orderly cancel except in that microsecond
  window; this is recorded here, not changed, because closing it needs the signal masked
  around each step and the residue is already handled by the documented recovery.

## Results: seeded interleavings (600 runs)

Three or four real senders started together, each with random stalls (5, 20 or 60 ms at up to
three random points) and, for half of them, a random kill point; survivors run to the end,
parked ones are killed. The seeds are `random.Random(f"{variant}:{seed}")` for seeds 1 to 150.

| Variant | Runs | Processes killed | Invariants | Result |
| --- | --- | --- | --- | --- |
| 3-4 senders claim one scope (`scope`) | 150 | 78 | at most one holder; at most one survivor exited 0; a later claim is refused iff someone holds; no scope lock file left; no traceback | pass |
| same, each keyed (`scope-keyed`) | 150 | 88 | same | pass |
| 3-4 processes send one keyed message (`key-same`) | 150 | 116 | at most one message; after a final retry exactly one | pass |
| 3-4 processes send one keyed message to an alias of 3 (`fanout-key`) | 150 | 257 | at most one per recipient; after a final retry exactly one per recipient | pass |

In all four, the torn-file check and the readers (no traceback, read-only) ran on the result.
The interleavings are narrow: process start-up dominates the few milliseconds a send takes, so
most runs are close to serial; the stalls and kills are what widen the window. The mutant
that removes `flock` shows the harness can see two winners.

## Results: hostile files on the read side (39 cases)

One good message plus one hostile entry in the same mailbox, then `list`, `list --json`,
`list --live`, `inbox`, `inbox --json`, `status`, `status --json`, `doctor`, `show`,
`show --json` and the pickup hook (10 readers and the hook = 429 runs). Required: finishes
(25 s limit per run in the first sweep, 60 s in the repository run), no traceback, no raw
escape byte, valid JSON where promised, the mailbox byte-identical afterwards, and the good
message delivered by the hook. All 39 pass:

malformed header (no closing fence, empty file, a header line without a colon, duplicate
keys), a 3 MB header line, a 20 MB body, a 2 GB sparse file, non-UTF-8 body and subject, NUL in
the body and in a header, a BOM, CRLF line ends, terminal escapes and bidi/zero-width
characters in the subject, mode 000, a symlink to a file outside, to `/dev/zero` and
`/dev/random`, a dangling symlink, a symlink loop, a symlink to a directory, a FIFO, a
directory named `*.md`, two files with one id, an id that differs from the file name,
`date` in year 9999 and year 1, an unparseable date, a zone offset `+99:99`, no date, a CLAIM
with a garbage `expires` and one expiring in year 9999, a `supersedes` cycle and
self-reference, an ASK replying to itself, junk dotfiles at lock, marker and temp names,
hostile key markers (a `*` id, binary junk, a symlink), a symlink and a FIFO at a scope-lock
name, and 1000 garbage files. The first sweep also covered a socket, a lower-case id, a CLAIM
that expires before its date and a 200-character file name; all passed.

The FIFO and `/dev/zero` cases never hang a reader: they are quarantined without being opened
(`REJECT ... not a regular file`, `symlink (never followed)`); `list` exits 2 for any
quarantined file by design.

Planted files at the names `send` itself uses (key marker, scope lock, takeover mutex,
11 kinds each, a scratch script run once, **not** in the repository harness: FIFO, directory, three symlinks, empty, binary, mode 000, a `*` id, 5 MB, `/dev/zero`),
then a send: every case ends in a documented outcome (exit 1 naming the errno for a link, a
directory or an unreadable file; exit 2 where `flock` is refused on a FIFO; a takeover after the
wait for junk), with no hang, no traceback and no write through a link, except the one
defect below.

## Results: PARTICIPANTS.md edge cases (25 cases)

`send` and `ask` to an alias, `send` to a writer, a scoped CLAIM to the alias, `list`,
`inbox`, `status`, `status --json`, `doctor`, `capability`, `list --to <alias>` (11
commands) and the pickup hook, per case: no traceback, no hang, readers leave the mailbox
unchanged, no message filed to a malformed recipient, one copy per writer. All 25 pass:
duplicate identity rows, re-registration with conflicting capabilities and with two uids, an
alias naming an unknown writer (one copy to the ghost and the section 41 warning), an alias
with only unknown writers, an alias naming itself, naming another alias, an alias cycle, an
empty writers cell, repeated alias rows and writers, capital letters, a 200-character alias,
writers with spaces, zero-width characters and `../` (the whole send refused, nothing
written), an alias naming the sender, an alias that is also an identity row, a table without a
header, separator rows only, 30000 rows, a NUL byte, non-UTF-8, an unreadable file, a
directory, a symlink and a FIFO named `PARTICIPANTS.md`, an empty file, and only an
`## Aliases` heading. "Pass" means no crash and a consistent result, not that every
configuration is sensible; the one that is silently wrong is the defect below.

## Defects found

Two, each reproduced, each with a regression check in `test_hardening.py` that fails on the
unfixed code and a focused fix.

1. **A fan-out copy to a writer that is itself an alias reaches nobody and `send` said
   nothing (PR #77).** *Expected:* section 41 warns when a message goes to a name nobody
   has. *Actual:* an Aliases row naming another alias (or its own name) as a writer files
   a copy to that alias name; aliases expand at send time only and do not nest, so nobody
   reads it, and `send` exited 0 with an empty stderr because the check counted every alias
   name as known; `status` reported it later as `ALIAS_MAIL_UNDELIVERED`. *Repro:*
   rows `alice`, `bob`; `crew -> bob, crew`; `send --from alice --to crew`. *Root cause:*
   `unknown_recipient_note` adds all alias names to the known set. *Fix:* one stderr
   warning per such copy; exit status, stdout and the copies filed unchanged.
2. **A hand-written key marker longer than 4096 bytes refused its key for good (PR #82).**
   *Expected:* section 31 step 4: an unreadable marker is taken over after the key wait.
   *Actual:* up to 4096 bytes is taken over (exit 0 after about 1 s at `--key-wait 1`); 4097
   bytes or more makes every `send --key` exit 3 ("is being sent by another process right
   now") after eight rounds of the wait (40 s at the default), until someone deletes the
   file. *Repro:* 4097 bytes of any junk at `.idem.<sha256("alice\0bob\0k1")[:24]>`, then a send
   with that key. *Root cause:* `_idem_claim` reads the first 4096 bytes; the legacy takeover
   compared the whole file with them. *Fix:* compare the same 4096 bytes. Severity low: only a
   hand-written file is that long.

## Observed, not defects, not changed

* **A future-dated message never goes stale.** A NOTICE, ANSWER or DISPUTE whose `date` is in
  the future is `fresh` until that date plus 7 days; one dated year 9999 is listed by the hook
  at every prompt, and `status` shows its sender's last activity as year 9999 at age 0, so that
  sender never reads as stalled. `send` stamps the sender's own clock, so for mail it filed the
  excess is the clock skew; only a hand-written file makes it unbounded. (`latency` already
  treats a negative delta as `CANNOT_VERIFY`, section 23; `live` does not.) A cheap fix would
  treat a date more than a day ahead as unparseable; it changes what the hook shows, so it
  is left for the owner.
* **A claim to a large alias is quadratic.** Each copy re-reads the mailbox: 10, 50, 100, 200
  writers took 0.1, 0.4, 1.0, 3.0 s on this machine for a scoped claim (a plain send: 0.1 to
  0.4 s). A 1000-writer alias exceeded 25 s in the first sweep. Not a realistic table.
* **A planted `.idem.<h>.takeover` file delays the takeover of an old-format orphan marker by up
  to 30 s** (the mutex goes stale at 30 s): the legacy path, by design, reported as
  `STALE_TAKEOVER_MUTEX` by `status`.
* **A hand-written duplicate id wins by name order.** A file `<id>-clone.md` sorts before the
  real `<id>-good.md`; both are named for the id, so the first in file-name order is admitted
  and the other is a REJECT line (section 36(b), "not coverable").
* `doctor` exits 2 in every one of these mailboxes because no canary was filed; that is its
  normal answer for a mailbox without one.

## Not covered

* `ask` and `canary` (they use the same publish; only `send` was enumerated). A killed `ask`
  fan-out retried by hand duplicates for the recipients already served (documented).
* A power loss; a network, synced or FUSE filesystem; a filesystem that lies about `flock` or
  hard links. A kill is a process kill: the page cache survives it.
* A real model, or a real host, being killed during a send.
* Kill points inside `load_messages`, `print` and the interpreter itself; only the write-side
  primitives listed are wrapped. A kill at a read step cannot change the mailbox.
* Windows (unsupported). Python versions other than 3.13 (the full matrix ran only on this
  machine; CI runs the CI-sized subset on its own Python versions).
* More than 150 seeds, or more than four contenders.
