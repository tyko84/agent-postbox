# Coordination audit 2: claims, owed replies, and reconstruction from the files

Scope: the properties the project can defend (exclusive claims with mandatory
expiry and recovery, owed-reply tracking, derived status, an inspectable
history), checked with real processes: separate `python3` processes driving the
real CLI, `kill -9`, SIGTERM/SIGINT, 16 concurrent starts, throwaway
directories, real clocks. No model calls, no network. Builds on
`docs/coordination-audit.md`. To hold a claim inside its critical section long
enough to kill it, the probes ran the real `main()` with `_publish` replaced by
a sleep (the only way to hit the millisecond window deterministically).

## Findings

| # | Scenario | Spec | Expected | Observed | Fixed? |
|---|---|---|---|---|---|
| 1 | 16 processes claim one scope | 5, 30 | one exit 0, rest exit 3, one file | 1 x 0, 15 x 3 ("being claimed right now" or "already held by"), exactly one message file, no lock left | clean |
| 2 | Owner re-claims, case-folded sender, rival, non-owner renew, NOTICE supersede (own and foreign) | 5, 32 | owner allowed, rival 3, non-owner 2 | as expected | clean |
| 3 | `kill -9` of the holder inside the claim critical section | 30, 33 | rival proceeds at once; inert lock; nothing published | rival exit 0 immediately; the dead holder's `.scope.*.lock` stays until the next claim on that scope removes it; `status` shows it as `in_flight` for its first 60 s (not an error, by spec); no message from the dead claim | clean |
| 4 | SIGTERM / SIGINT inside the critical section | 31 | exit 143 / 130, nothing written, no lock | as expected | clean |
| 5 | Holder dies after publishing (4 s lease), rival polls | 5, 34 | rival refused until expiry, then wins | refused (exit 3) until the second the lease ends; won at t+3.6 s; `status` then: old claim `EXPIRED` in `claims_attention`, new one in `claims_held`; `doctor` lists the old one as an orphan claim (EXPIRED) | clean (recovery is expiry only: a dead holder blocks its scope for the rest of its lease, up to 168 h; nothing says the holder is dead) |
| 6 | Expiry boundary at `expires-1s`, `expires`, `expires+1s` (`--now`) | 19, 34 | held until the instant | held at -1 s (`seconds_left: 1`); expired at 0 s in `list`, `status`, `inbox`, `list --live` alike | clean |
| 7 | Same identity re-claims its own scope after a "restart" (no `--supersedes`, the old id is unknown) | 5 | allowed | allowed, and files a SECOND held claim; `status` shows two held rows for one scope and one sender, not contested | by spec, see 8 |
| 8 | ...then releases the newest (supersede with no scope) | 32 | scope free, or the tool says it is not | tool printed "`f.py` is released" while the older own claim still held it; a rival got exit 3 for up to 168 h | **fixed** (medium): PROTOCOL.md section 42, `test_hardening.py` section 18 |
| 9 | Claim sent to an alias of two writers | 27 | siblings superseded together | two held rows (`claims.held: 2`) for one hold; release and renewal handled whole (earlier fix) | counts inflate, documented in 27 |
| 10 | Two linked git worktrees, default mailboxes | 41 | separate mailboxes | both claims on `S` succeed (no exclusion); with one shared `AGENT_MAIL_DIR` the second exits 3. Only `doctor` mentions it (`worktree:` line); `send --type CLAIM` says nothing | gap, below |
| 11 | ASK unanswered, sender/recipient "restart" | 4, 19 | still owed | derived from the files, so nothing to lose: `inbox --to X` and `list --status open` show it with age; `status asks.open` and `oldest_open_age_seconds` agree | clean |
| 12 | ASK to a participant who never appears; 10 days pass | 33 | reported | the ASK stays `open` and listed for the addressee, and the asker is `stalled` after 72 h; the addressee, having never written, is NOT in `agents`/`stalled_agents` (there is no row for a name nobody has written as). `list --to carol --status open` is the only per-recipient view | gap, below |
| 13 | Duplicate and conflicting ANSWERs (`DONE` then `FAILED`, same sender) | 39 | latest stands, contest reported | `CONTESTED_RESULT` in `problems`; ASK `answered` | clean |
| 14 | A bare ANSWER (no `--result`) from someone who is not the addressee | 4, 32 | closes the ASK (any citation does) | ASK closes, `owes_reply` false, addressee's inbox empty, `problems: []`, doctor silent. With `--result` it is flagged `RESULT_FROM_THIRD_PARTY`; without, not | by spec; gap, below |
| 15 | Sender answers its own ASK | 4 | closes it | closes it; no flag | by spec |

## Reconstruction from the files alone

`docs/data/audit-reconstruction/build_scenario.py` builds a 30-event mailbox in
about 12 s with real processes (a `kill -9` inside a claim send, a holder that
never renews, a takeover, a renewal, a release, a re-scope, a late refile, two
refused claims, an unanswered ASK, conflicting ANSWERs, an ASK closed by a
non-addressee, two NOTICEs in the same second).
`docs/data/audit-reconstruction/reconstruct.py` reads that mailbox ONLY through
`list --json` and `status --json` and prints the timeline, a claim interval
table with an end reason, and each ASK's lifecycle. Both are stdlib only and
are not wired into CI.

```text
python3 docs/data/audit-reconstruction/build_scenario.py --tool agent_mail.py --mailbox EMPTY_DIR
python3 docs/data/audit-reconstruction/reconstruct.py   --tool agent_mail.py --mailbox EMPTY_DIR [--json]
```

Result: every question in the brief is answerable except two, and those two
are not recoverable by any command.

| Question | Answerable? | How |
|---|---|---|
| Who held scope X, from when to when | yes | claim `date` to `min(expires, date of the superseding claim)` |
| Why the hold ended: renewed / released / re-scoped / expired | yes | invert `supersedes` among same-sender CLAIMs: same scope = renewed, no scope = released, other scope = re-scoped; none and past `expires` = expired |
| Who superseded whom | yes, but only by inverting `supersedes` yourself | `list --json` rows carry `supersedes`, never "superseded by" |
| Who took over after a lapse | yes | next claim on the scope by another sender with a later id |
| A lapsed claim refiled later | yes | superseding claim dated at or after the old `expires` (a gap in the hold is visible) |
| Who asked what, who answered, with which claimed result | yes | `reply_to`, `result`, `from`/`to` |
| Which ANSWER stands, who was not the addressee | yes | all answers are kept; the rule is in section 39 |
| Order of events with equal `date` | yes | the id (ULID, millisecond clock then random); `date` has one-second resolution, so sorting by `date` alone is ambiguous (in the scenario about 20 events share one second). Two sends in the same millisecond from different processes order randomly |
| A holder that CRASHED vs one that simply stopped renewing | **no** | both end as `EXPIRED`; a killed process leaves no record (a mid-send kill leaves only a lock file for about a minute) |
| Who TRIED to claim a scope and was refused | **no** | refused claims write nothing (section 5/30 invariant) |

Smallest change that would close the two gaps: none is worth making. A "refused"
record would break the invariant that losers write nothing (and would make the
mailbox grow under contention); a "holder alive" signal needs a heartbeat,
which is a new mechanism. The reconstruct script prints both as limits.
"Superseded by" in `list --json` would be additive but no consumer needs it
(the inversion is ten lines), so it was not added.

## Gaps documented, not changed

- A dead holder blocks its scope for the rest of its lease (up to 168 h). Short
  leases plus renewal are the only mitigation; this is the design.
- `send --type CLAIM` in a linked worktree on a default mailbox gives no
  warning that it excludes nobody outside that worktree (`doctor` does). A
  stderr note on CLAIM with `--scope` there would be additive; it is in the
  `send` region another change is editing, so it is left as a recommendation.
- A name that has never written is invisible to `status` (`agents`,
  `STALLED_AGENT`); an ASK owed to it is visible only per recipient in `inbox`/
  `list`. A per-recipient `asks.open_by_recipient` would be additive.
- A bare ANSWER from a non-addressee closes an ASK without any flag (section 4
  says any citation closes). Only `--result` answers are flagged.
- Alias fan-out doubles `claims.held` and `claims_held` rows for one hold.

## Verified clean

Exclusive acquisition under 16-way contention; owner/non-owner renew and
release rules; kill -9, SIGTERM and SIGINT inside the lock; lease expiry and
takeover to the second with all views agreeing; derived ASK state after
restarts; conflicting answers; shared `AGENT_MAIL_DIR` across linked worktrees.

## Not verified

Network filesystems; Python 3.10; clock skew between hosts (leases compare the
reader's clock to the sender's stamp); a kill -9 at arbitrary instants of a
claim send beyond the `_publish` window (section 31's suite covers those).
