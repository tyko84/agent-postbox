# Coordination and automatic delivery audit (phases 4-5)

Scope: CLAIM lifecycle, ASK/ANSWER, hook delivery, and whether `list`, `inbox`,
`status`, the hook and `doctor` agree. Method: throwaway scripts in temp
directories (mailbox path containing spaces) driving the real CLI and hook as
separate processes; compared with PROTOCOL.md. Two defects were found and
fixed; everything else below held.

## Defects (fixed)

### 1. `doctor` lists renewed claims as orphans (low)

- Scenario: claim X, expires; its owner renews it (`--supersedes X`), or
  releases it by superseding with a different scope. After X's lease passes:
- Spec: section 20 check 3 lists orphan claims; section 34 says `EXPIRED` is the
  end of a claim "that was not renewed" and counts only unsuperseded claims.
- Observed: `status` says `claims: expired=0 superseded=1`; `doctor` says
  `orphan_claims: 1 (EXPIRED)`. Every renewal permanently adds an orphan row,
  so the count grows with every lease ever renewed. Found by a randomized
  five-view comparison (40 states in 140 seeds disagreed only on this).
- Fix: `cmd_doctor` skips superseded claims. Regression: `test_hardening.py`
  section 17. PROTOCOL.md section 20 updated.

### 2. A release or renewal of a claim sent to an alias frees only one copy (medium)

- Scenario: `send --type CLAIM --to team --scope x ...` where `team` is an alias
  of two writers (section 27) files two claims, each holding `x`. The owner
  releases (`--supersedes <printed id 1>` with a different or no scope) and the
  tool says "`x` is released".
- Spec: section 32: superseding with a claim that names another scope frees the
  old scope "at once and a rival may claim it".
- Observed: the other copy still holds `x` until its own expiry (up to 168 h); a
  rival's claim exits 3 `scope 'x' is already held by <owner>`.
- Fix: when a CLAIM to an alias cites `--supersedes`, each copy supersedes the
  sibling copy addressed to its own writer (same sender, scope, expiry, filed
  within 60 s). Regression: `test_hardening.py` section 17 (fails without the
  fix). PROTOCOL.md section 27 updated.

## Held, no defect found

- Concurrent claimants, same scope: exactly one winner, loser exit 3 (also pinned
  by existing suites). Non-owner renew and non-CLAIM supersede of a claim: refused,
  exit 2. Takeover of an expired claim: succeeds; the old owner's later renewal
  or fresh claim is refused (exit 3); hook shows the old owner `NOT live
  (EXPIRED)` and the new owner a live hold. Scope matching is exact and
  case-sensitive (section 37); sender case is folded. Fork renewal (two
  claims superseding one) leaves both held, same sender: allowed.
- `--expires`: past, date-only, zone-less, garbage, offset 25:00, year 9999, more
  than 168 h: all exit 2 with one line.
- ASK/ANSWER: a second or third-party answer changes nothing (section 32); a
  reply to an unknown id is written and reported as dangling; ids compare
  case-insensitively. Retried keyed sends under SIGKILL at 80 random instants
  (notices and scoped claims): always exactly one message, never a duplicate. A killed send
  can leave an inert hidden `.tmp` file (a later run of 60 kills left two): readers never see
  it, `doctor` reports it as `stale_tmp` after ten minutes, and nothing deletes it
  automatically (PROTOCOL.md section 33).
- Delivery: 40 concurrent keyed sends with the hook running alongside: no
  partial block, no duplicate, header count at least the listed rows, all 40
  counted in the final run. Hook, `list --to X --live` and `status` agree on
  the live set in every generated state (clock skew of -20 d to +40 d,
  malformed expiries, superseded claims, `re:` and `reply_to:`).
- Views: `status asks.open` equals ASKs no message cites;
  `claims.held` equals `list --type CLAIM --status held`;
  `live_by_type` total equals `list --live`.

## Odd but documented (not changed)

- A keyed send's content hash is type, subject and body only (section 31). Reusing
  a key for a CLAIM renewal with a new `--expires` or `--scope`, or for an
  ANSWER citing a different ASK, prints `duplicate of <id>` and writes nothing,
  so the lease is not extended and the second ASK stays open. Use a new key per
  renewal.
- `list --live` and the hook still deliver a superseded, unexpired claim
  (section 9: a claim is live until it expires); `inbox` does not (section 19).
  `inbox` also keeps a stale NOTICE that carries a structured block (`blocked`).
- A message `to` yourself is not delivered by the hook, but an ASK to yourself
  is counted in its "you owe a reply" block.
- The hook header can read "0 live message(s)" when only held claims are shown.

## Unverified

- Python 3.10 (supported, none installed here): `send` accepts `--expires` with
  `+0000` or one-digit fractional seconds (the regex allows them) which
  `datetime.fromisoformat` only parses from 3.11. Read back on 3.10 such a
  claim would be `MALFORMED_EXPIRES`, not a hold. Not reproduced.
- Kill -9 was timed in the first 90 ms of a send (mostly interpreter start-up
  and the marker/publish window); section 31's own suite covers the finer cases.
