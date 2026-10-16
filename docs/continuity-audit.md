# Continuity audit: session boundaries and unusual filesystems

Scope: what the mailbox does when sessions start, stop, restart and die, and when the
filesystem is not the tidy one the tests assume. Method: real processes (the CLI and
`hooks/agent_mail_check.py`) in throwaway directories, no model, no network; each
scenario compared with PROTOCOL.md. Run on macOS (APFS, case-insensitive), Python 3.13,
against `main` at 618a818. Throwaway scripts, not committed. Two defects with a
real consequence and a third, smaller one were fixed; the rest held or is reported
below as not fixed.

## Defects fixed

### 1. A hand-edited PARTICIPANTS.md silently loses recipients and capabilities (medium)

- Scenario: a table edited between sends, as in "added a second row for the alias
  `team`" or "a second row for an identity".
- Spec: section 27 (every alias maps to writers; an alias must not collide with an
  Identity row), section 21 (undeclared is `CANNOT_VERIFY`, never an implied yes).
- Observed, each reproduced with the real CLI:
  - Two rows for one alias: the later replaced the earlier. `send --to team` filed
    one message and `c1` (named only in the first row) got nothing, no warning.
  - `| team | b, b |`: `b` got two copies.
  - Two rows for one identity (`can: review` then `cannot: review`): the last row won
    outright, so reordering the rows flipped `capability` from `cannot` to `can`.
  - The Aliases table was parsed as Identity rows: its header word `alias` and each alias
    counted as identities. A send `--to alias` raised no unknown-recipient warning and
    the warning's "Known:" list printed `alias`.
  - An alias equal to an Identity row (section 27 forbids it): `send --to bob` fanned out
    to the alias writers and `bob` received nothing, silently.
- Fix (`load_participants`, `_parse_aliases_text`, `cmd_send` fan-out): duplicate alias
  rows are united and de-duplicated; duplicate identity rows are united with `cannot`
  outranking `can`; the Aliases table is skipped when reading Identity rows; the
  alias/identity collision still fans out (unchanged) but prints one stderr warning.
  Exit statuses and stdout are unchanged.
- Regression: `test_cli.py` `test_participants_table`; against the unfixed
  `agent_mail.py` its alias, duplicate-writer, collision and capability checks fail.
  PROTOCOL.md sections 21 and 27 updated.

### 2. `AGENT_MAIL_DIR=~name` for an unknown user is a traceback (low)

- Scenario: `AGENT_MAIL_DIR='~nobody-here/mail'`, or `~` in a container with no home
  directory; any command.
- Spec: section 38, no traceback whatever the environment contains, refusals are exit 2.
- Observed: `RuntimeError: Could not determine home directory`, exit 1, from `send`,
  `list`, `status`, `doctor`, `inbox` and `canary`.
- Fix: `_expand()` keeps the text as written when `expanduser()` cannot expand it, so it is
  an absent mailbox: `NO MAILBOX at ~nobody-here/mail`, exit 2 (the hook was already silent).
- Regression: `test_cli.py`, "inputs that are not arguments" (four commands). PROTOCOL.md
  section 38 table gets a row.

## Reported, not fixed (outside my regions, or a design question)

1. `doctor` PASSes (exit 0) on a mailbox this user cannot write to; the next `send`
   exits 1 with `Permission denied (EACCES); nothing written`. `doctor` checks
   `R_OK | X_OK` only. An `os.access(box, W_OK)` line would be cheap. `doctor` is
   agent A's region.
2. `doctor` with `AGENT_MAIL_DIR` set and a deleted working directory prints most of its
   report, then dies with `refusing: the working directory cannot be read ... or set
   AGENT_MAIL_DIR`, exit 2, although the variable is set. The cause is `project_root()` in
   the pickup-hook, worktree and permission-line helpers at the end of `cmd_doctor`. Every
   other command works in that situation.
3. A message whose `to:` is an alias that was not declared when it was sent is never shown
   to the writers once the alias is declared (`hook` and `list --to b` match `to:` against
   one name; only `list --to <alias>` expands). `send` since section 27 fans out, so this
   needs a hand-written file or a sender whose PARTICIPANTS.md was not found. Changing it
   changes the pickup hook (not in scope).
4. A rename (`old` to `new` in PARTICIPANTS.md) strands mail already addressed to `old`.
   `send --to old` warns; no view reports "live mail addressed to a name with no row".
5. A message dated in the far future (`date: 2099-...`) stays "fresh" and makes its sender
   `active` with `age=0h00m` in `status` until that date. Already known as clock skew
   (docs/coordination-audit.md); unchanged.

## Held, no defect found

| Area | What was run | Result |
| --- | --- | --- |
| Discovery | send from a nested directory with spaces; from `.git/hooks`; via a symlinked project path; after `mv repo repo2`; two `docs/agent-mail` (nested repo with its own `.git`, and a non-git subdirectory box); no `.git` anywhere | nearest box wins, repo root otherwise; paths are not stored in messages, so a moved or renamed tree keeps its mail. A subdirectory box without `.git` shadows the outer one (by design, section 9) |
| Env | `AGENT_MAIL_DIR` relative, absolute, symlink, empty (= unset), a file, missing; `AGENT_MAIL_PROJECT` absolute, relative, nonexistent; `CLAUDE_PROJECT_DIR` | as specified; missing or file: `NO MAILBOX`, exit 2; a nonexistent project is ignored (section 38) |
| Deleted working directory | send, list, status, inbox, hook with and without `AGENT_MAIL_DIR`, `AGENT_MAIL_PROJECT`, `CLAUDE_PROJECT_DIR` | one-line refusal exit 2 when nothing names the box; works when something does; hook silent by design |
| Box deleted under use | list, status, doctor, hook, then send | `NO MAILBOX` plus hint, doctor `store: MISSING`, hook silent; a send re-mints the box at the walk-up default |
| Before, during, after | message sent before the recipient has a row, while it runs, after it exits, and read after restart | no read state; every pickup shows the live mail again; delivered exactly as in reliability-evidence 2 to 4 |
| Kill -9 mid-send | 80 trials at random 0 to 90 ms: plain, keyed, scoped CLAIM, keyed alias fan-out; then two keyed retries each | always exactly one message per recipient, both retries exit 0; the leftover hidden temp file is the only residue and `doctor` reports it once stale |
| Concurrent start | 20 hooks, 20 keyed sends (10 ASK, 10 NOTICE) and 20 more hooks, all at once | every send exit 0, no stderr from any hook, no id twice in any hook output, header count equals rows, final pickup lists all 20 once |
| Partial files | stale `.tmp`; a truncated final `.md` | temp hidden then counted by `doctor`; truncated file quarantined (`REJECT`, hint, exit 2), rest still listed and delivered |
| Read-only box | send, list, status, doctor, canary | send exit 1 with a clear line; readers fine |
| Paths | spaces, non-ASCII, quotes, `$` and backticks, a leading dash (relative), 200-character component | all work |
| Names | 500-character subject, CJK subject, 128-character `to`, 4096-character key and scope; 4097 | slug is capped at 48, an empty slug becomes `message`; longer fields refused at the stated limit |
| Case | subjects, `to`, keys and identities differing only in case, on a case-insensitive volume | file names start with a ULID, key markers and scope locks are hashed, so nothing collides; identities fold, keys and scopes are exact (section 37) |
| Ordering | equal dates, hand-made out-of-order ids, a 2099 date | ordered by id; consistent across `list`, hook and `status` |
| Expiry | `--expires` already past, on NOTICE and on CLAIM | refused, exit 2, one line |
| Exit codes | every refusal above against section 38 | agree |

## Not covered

Power loss, network or synced filesystems, case-sensitive volumes (only the hashing
argument above), a filesystem without `flock`, and a system clock that steps backwards
between two sends (ids would then sort by clock, not by order sent).
