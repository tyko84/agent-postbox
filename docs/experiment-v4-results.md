# Experiment v4 pilot: results (2026-10-16)

**Formal result: the pilot failed its validity gates (gate 3), so by
[experiment-v4.md](experiment-v4.md) section 7 there is no claim.** The descriptive counts are
reported below because they are informative about the mechanics, with that label attached. Nothing
was rerun and no gate was changed after the data were seen.

| | |
|---|---|
| Design | scenario 2 (interrupted holder) primary: arms N0, S, L, P, O x 6 instances; scenario 1 (contested scope) observation: N0 x 3, S, L, P x 1; 72 sessions, 36 runs |
| Code | `main` at `59fa84a`; analysis by the frozen `scripts/analyze_v4.py` (sha256 `e23d5da4…6744`); readiness gate of round 3 |
| Host / model | Claude Code 2.1.300, haiku, effort medium, `--permission-mode dontAsk`; headless sessions on the owner's subscription, no API billing |
| Cost | notional $0.28 (reported by the host; not a bill) |
| Raw data | outside the repository: `~/Desktop/Agent-Postbox/experiment-data/v4-pilot-2026-10-16/` |

## Gates

| Gate | Result |
|---|---|
| 2 oracle solvable (O final_ok >= 80%) | pass, 6/6 |
| **3 every arm operable, no channel command refused by the host** | **FAIL**: L 1 refusal, P 1 refusal (S 0) |
| 4 prompts equal within 10% | pass (S 566, L 549, P 596 characters) |
| 5 usable sessions >= 90% | pass, 48/48 |
| 6 kill verified after the claim/lock/note | pass, 24/24, no void runs |
| scenario-1 observation | N0 collided 0/3 |

The two refusals: in `S2-L-5001` the survivor ran `mkdir <absolute path>/locks/...` and the host
refused it because the arm's allow rule was written for the relative form (the lock already
existed, so the outcome was probably unaffected, but that is a judgement, not a measurement); in
`S2-P-5002` the survivor's wait helper was called by absolute path and refused, it did not wait
out the lease, and it ended without recovering. That run is the only P failure. A harness that
allows only some path spellings is the same class of defect as in experiment v2, and this one
was not caught by readiness.

## Descriptive counts (scenario 2, 6 instances per arm; no test, no rate claim)

| Arm | final_ok | recovered | time to recovery (median) | notes |
|---|---|---|---|---|
| N0 (nothing) | 6 | 6 | about 5 s | the survivor was never blocked |
| S (soft notes) | 0 | 0 | none | all 6 survivors waited the whole window and none overrode the stale note |
| L (mkdir lock) | 0 | 0 | none | all 6 waited, none broke the lock (`broke_lock` false in 6/6) |
| P (Postbox claim, 90 s lease) | 5 | 5 | 223 s | the survivor got exit 3, waited, claimed after the lease expired and finished; 1 run hit the refusal above |
| O (sequential oracle) | 6 | n/a | n/a | tickets are solvable |

Scenario 1 (observation): 0 of 3 N0 runs lost an update or duplicated work. In all three the
host rejected a stale edit (Claude Code refuses an edit when the file changed since it was read)
and the agent re-read and merged. S, L and P each completed their one run.

## What this does and does not show

* **Contested scope cannot show a benefit here.** The host's own stale-file check prevented the
  failure that a claim exists to prevent, in every N0 run (0/3), so there was nothing for
  Postbox, a lock or a note to improve. That is a finding about this host and model, and the
  reason this scenario was demoted to an observation.
* **When nothing collides, coordination costs time.** The arm with no coordination recovered
  fastest because nothing blocked the survivor; the arms that block (notes, lock, claim) all
  delayed or stranded it. This follows from the design and is not evidence against claims where
  collisions happen.
* **Among the arms that block, the expiry mattered, descriptively.** Capable agents did not
  reason past a stale note or a stale lock (0 of 12 overrode one), so those arms stayed stuck for
  the whole patience window, while the lease let the P survivor through after it lapsed
  (5 of 6; the sixth is the refusal run). With n = 6 per arm, one model and one task this is a
  count, not a rate, and it is not a pre-registered finding because gate 3 failed.
* **Not tested:** audit reconstruction (scenario 3), the claim's value where the host does not
  protect writes (other hosts, editors, or non-file resources), more than two agents, any other
  model, how often a holder really dies.

## Decision

No rerun of this pilot to repair gate 3: the data stand as a failed pilot with the descriptive
counts above. A future experiment would have to fix the path-spelling gap in the allow rules
(and verify it), pick a scenario in which the host does not already prevent the collision, and be
pre-registered afresh; it needs the owner's approval. The README's Evidence section records this
result as failed gates and claims nothing.
