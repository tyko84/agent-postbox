# Experiment v4 pilot: readiness checks (two rounds, 2026-10-15)

Status: **readiness only, not measured data.** The pilot was not run. Round 1: 12 live headless
`claude -p` sessions; round 2 (the recheck the integrator authorised): 16 more (28 in all), owner's
existing subscription, haiku, effort medium, `--permission-mode dontAsk`, no API key and no other
service; notional cost USD 0.068 and 0.036. Raw data outside the repository under
`~/Desktop/Agent-Postbox/experiment-data/v4-readiness-2026-10-16/` and `.../v4-readiness-2-2026-10-16/`.
Pre-registration (amended): [experiment-v4.md](experiment-v4.md).

## RECOMMENDATION (final, after round 3): RUN the re-scoped scenario-2 pilot

Round 3 (10 sessions, 5 scenario-2 runs, one per arm, network up, USD 0.036 notional) gave a clean,
void-free set and a passing live readiness gate with the final prompts hash (`d6623613...`) and the
final script (`fbabe693...`, commit `6ca3fe4`). Gate sha256 `6b886e1c...`; raw data in
`~/Desktop/Agent-Postbox/experiment-data/v4-readiness-3-2026-10-16/`.

| Arm (scenario 2, one run each) | What happened |
|---|---|
| N0 | kill after the first change; survivor finished both tickets in 5 s: recovered (nothing blocks it) |
| S | note seen, kill after it; survivor read the stale note, waited 6 pauses (180 s), did not edit, gave up: **not recovered** |
| L | lock seen, kill after it; survivor waited 8 pauses (240 s), did not break the lock (`lock_present_at_end`), gave up: **not recovered** |
| P | claim seen, kill after it; survivor got exit 3 three times, claimed after the lease (first edit 110 s after the kill), finished: **recovered** (227 s) |
| O | sequential oracle completed, suite passes |

Kill verified after the artifact in all four arms, no void, no permission denial, no unsafe command.
This is one run per arm and **a rate cannot be read from it**; it shows the arms are operable, the
triggers fire, the survivor behaviours differ in the way the design needs (not "every arm recovers at
once", not "none can"), and the harness measures them.

**Run it as amended:** scenario 2, 6 instances x 5 arms (60 sessions) + the scenario-1 observation
(6 runs, 12 sessions) = 72 sessions (cap 80), up to 3 runs in parallel; expected wall time about
1.5 to 2 h (S and L runs take the full 240 s patience plus 60 s grace; about 12 of 30), notional
cost about USD 1. Infrastructure failures (including network outages) are repeated once; a repeat
needs the network, so check it first. Remaining caveat: stuck lock/note arms may differ in other
seeds (a survivor may break a lock); the pilot is designed to record exactly that.

## Round 2: what was learned (16 sessions, 8 runs)

| Run | Outcome |
|---|---|
| S2 S seed 9211 | Note trigger fired (note seen 10.1 s, SIGKILL at 14.1 s, note still present: **kill verified after the note**). Survivor read the stale `CLAIMED` note, waited with `pause.py` (2 pauses) and was then cut by the network outage; **not recovered, not attributable** |
| S2 L seed 9212 | Lock trigger fired (lock seen 3.6 s, SIGKILL 7.6 s, lock still present: **verified after the lock**) but the holder had finished its ticket in 4 s, so the instance was void by rule. Fix: kill delay 4.0 s to **1.5 s**. Survivor waited (2 pauses), did not break the lock, then the outage |
| S2 P seed 9213, S2 N0 seed 9214 | Holder session stalled in API retries for 233 s, no claim: void, infrastructure |
| S1 N0 seed 9215, S1 S seed 9216 | Both agents got `API Error ... ENOTFOUND` after ~250 s: infrastructure |
| S1 L seed 9217 | Completed, hidden suite passes (waiting rule works: the blocked agent waited, then both finished); no lost update, no duplicate. Both agents ran `rmdir` on the lock (one released, one cleaned up) |
| S1 P seed 9218 | Completed, hidden suite passes; B hit exit 3 nine times and waited; A needed 415 s (its claim/lease handling, 31 turns); no collision |

So: the **amended waiting rule works** in S1 L and S1 P (round 1 had the blocked agents stop). The L
and S kill triggers fire after the lock/note exists. P and N0 scenario-2 triggers are unverified
in round 2 (P was verified in round 1: claim 4.3 s, kill 4.0 s later, survivor claimed after the
lease). The survivor in S and L waited and did not break the lock or override the note in the
visible part of the runs; whether it eventually breaks it within 240 s is exactly what the pilot
measures, not yet observed.

## Fixes made after round 2 (before any measured data)

* Kill delay 4.0 s to 1.5 s (the holder had already finished in the L run).
* Infrastructure classification: three or more `api_retry` events in a session, or an error result
  without any tool call, is "API failure" and is repeated once in the pilot.
* The survivor record (`survivor`): pauses, nominal wait, first edit after the kill, broke lock,
  ignored stale note, claimed after the lease, unsafe commands.
* Note: `.claude/settings.json` allow rules are **ignored** by the host in an untrusted workspace
  ("Ignoring 23 permissions.allow entries ... workspace has not been trusted"); the rules in force
  are the same list passed with `--allowedTools`. The installed copy only lets `doctor` check them.

The readiness gate file of round 2 (`passed: false`) carries the prompts hash `ed019edc...`; the
kill-delay change alters `prompts_hash()` (timing is part of it), so a new gate is needed in any
case. I did not hand-edit a gate.

# Round 1 (12 sessions; superseded by rounds 2 and 3)

## Checks

| | Check | Result |
|---|---|---|
| a | P allow rules installed in the throwaway project's `.claude/settings.json` and seen by `doctor` (offline, before any live session) | yes: relative and absolute-quoted spellings both "allowed by an identical rule"; the adapter hook survived the merge |
| b | P arm's send forms let through by the host | yes: heredoc `--body-file -` CLAIM (with `--scope`, `--expires`) written, exit 3 on a held scope seen repeatedly (5 times in S1, 4 in S2), a release by `--supersedes` written, an ASK with a heredoc written; **0 permission denials in any session** |
| c | Each arm completes one instance end to end | N0 yes, S/L/P: both sessions ran to a result, but **tree unfinished** (S1-S, S1-L, S1-P: the blocked agent saw the other's note/lock/claim, wrote its own tests and stopped; "final_ok" false). O not run live (see unverified) |
| d | Kill timing | S2-P: claim file seen 4.3 s after launch, B launched then, SIGKILL at 8.4 s (4.0 s after the claim was seen), claim file mtime before the kill, holder alive at the kill, claim still present after it: **verified** |
| e | Does N0 collide? | **0 of 2** (lost update 0, duplicate 0). Cannot establish a rate |
| f | Survivor recovery under P | B waited with `tools/pause.py`, was refused (exit 3) four times, claimed after the 120 s lease ran out (about 150 s) and wrote its own ticket; **but did not do the dead holder's ticket**, so "recovered" was false. Cause: the prompt said both tickets must be finished but B read that as its own. Amended: "whichever agent's they are: if your teammate does not finish theirs, finish it yourself" (not rechecked live) |

Run table (notional USD, seconds of wall clock):

| Run | Final tree passes all hidden tests | Lost update | Duplicate | Rejected edits | Wall s |
|---|---|---|---|---|---|
| S1 N0 seed 9201 | yes | no | no | 1 (A) | 30 |
| S1 N0 seed 9202 | yes | no | no | 1 (A) | 30 |
| S1 S seed 9203 | no (A stopped, wrote tests only) | no | no | 0 | 43 |
| S1 L seed 9204 | no (same) | no | no | 0 | 30 |
| S1 P seed 9205 | no (same; A asked B by mail and stopped) | no | no | 0 | 36 |
| S2 P seed 9206 | no (B did not do A's ticket) | no | no | 0 | 165 |

Other observations (descriptive; one run each, no rates): the agents try `git -C <dir> ...`, which
the allow list refuses (one denial in three N0/S sessions, harmless); the host's cost is per
process and a SIGKILLed holder reports none; with this task the work takes about 30 s, so a 120 s
lease is long relative to the work and a patience window of 6 minutes is ample.

## What was changed after readiness (dated 2026-10-15, before any measured session)

* Every prompt (both scenarios, both agents, every arm, O included) now has the same sentence:
  both tickets must be finished whichever agent's they are, with `python3 tools/pause.py 30` as the
  way to wait and a maximum wait (5 minutes in scenario 1, 6 in scenario 2). Previously only the
  scenario-2 survivor had a waiting rule.
* The intro no longer says the other agent is working at the same time (that moved to the shared
  line), so the O arm's prompt no longer contradicts itself.

The readiness gate file written by the run carries the old prompts hash, so `--stage pilot` refuses
it by design: readiness must be repeated with the amended prompts before the pilot can start.

## Unverified

* The amended prompts (the waiting rule, "finish it yourself") have not been run live.
* Arm O has not been run live (its machinery is the same sequential launcher; covered offline by a
  simulation).
* Kill timing was checked live for P only; the lock-directory and notes-line triggers are tested
  only against simulated agents.
* Whether any of the 2 N0 non-collisions is typical.
* Scenario 3 (audit) is out of the pilot; the artifacts it needs are kept, no auditor exists.
* Live runs create `~/.claude/projects/<path>` state for each throwaway project path (as v2/v3 did).
