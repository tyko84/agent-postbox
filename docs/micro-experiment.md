# Micro-experiment: does a fact reach the agent that needs it?

A small, real-agent pilot of one mechanism: **a fact that one agent holds is
used by the agent that needs it**. It is the cross-dependency row of the design
in [coordination-experiment.md](coordination-experiment.md), cut down to two agents and
three headless Claude Code sessions per run. It is a **pilot**: it can show
whether the mechanism works with real agents and what it costs. It cannot show a
statistically meaningful improvement, and it does not claim one.

This page was written **before** the first model session was started (the commit that
adds it precedes the commit that adds the results). Everything under "Results" is
appended afterwards; the design is not edited to fit the results.

## Workloads (identical across arms)

One *instance* is a seeded, tiny Python project with two tickets. A seed fixes a random
six-character **window code** (`W-` plus four characters, not guessable).

* **Ticket A (agent-a, the scout).** Implement `is_weekend(day)` in `app/util.py`
  (a hidden check decides it). Its prompt also contains the *observation* "the output of
  `python3 probe.py` in your sandbox was `window code: <code>`". The code appears
  nowhere in the repository, only in agent-a's prompt.
* **Ticket B (agent-b, the consumer).** Set `WINDOW_CODE` in `app/config.py` to the
  team's window code. Its prompt says the code is not in the repository, that agent-a
  has it, and: "if you cannot get it, leave `WINDOW_CODE = None` and say so; do not
  guess".

The scout's fact is therefore given to the model as an observation, not discovered by
the model running a program. That is a simplification: it tests whether the fact moves,
not whether the scout notices it is worth sharing without being told (its prompt does
say to share what teammates will need).

## Schedule (identical across arms)

Three sessions per run, in this order, each a fresh headless session in the same
checkout: **B** (needs the fact; asks), **A** (does its ticket; may answer/share), **B**
again (finishes its ticket). B's two prompts are the same text.

## Arms

* **P, Postbox.** The real installer registers the adapter; identities `agent-a`,
  `agent-b`; Bash restricted to the mailbox CLI. B is told how to `ASK`; A how to
  `ANSWER` and `NOTICE`. The hook prints subjects at each prompt; the body needs `show`.
* **C1, relay.** No mailbox. Each agent may write lines starting with `TO TEAM:`; the
  harness (a fixed script, no judgement) puts the other agent's lines at the front of the
  next session's prompt. Strong control: delivery is guaranteed.
* **C2, notes.** No mailbox. A `NOTES.md` in the checkout; the prompt says to read it
  first and to append a line when asking, finding or finishing something. Nothing is
  injected: the agent must remember to read it. This is the failure the README describes.

Coordination instructions are about the same length in every arm (recorded per run).
Same model (`haiku`), `--effort low`, same tools besides the channel, same turn caps.

## Metrics (all computed from artifacts, never from what a model says)

1. `window_ok`: `app/config.py` has the exact code. `window_wrong`: any other non-`None`
   value (a guess or invention). `window_none`: left `None`.
2. `a_ok`: hidden check of `is_weekend`.
3. `completed`: `window_ok` and `a_ok`.
4. Messages **generated** / **delivered** / **acknowledged**: P: files in the mailbox by
   type; delivered = the subject appeared in a UserPromptSubmit hook's output;
   acknowledged = an ANSWER whose `reply_to` is an ASK. C1: `TO TEAM:` lines /
   relayed lines / n/a. C2: lines appended to `NOTES.md` / reads of `NOTES.md` by the
   other agent (a Read or `cat` tool call) / n/a.
5. **Missed communication**: the code was available to the team and B's result is not
   `window_ok`.
6. **Coordination failure**: B ends `window_wrong` or `window_none`, or A never shared.
7. **Conflicting operations**: not exercised by this design (the agents edit different
   files, in sequence). Reported as "not exercised", not as zero conflicts.
8. **Manual interventions**: none are possible in a run; the C1 relay is counted as
   `relayed`. Infrastructure failures (rate limit, host crash) are repeated once and
   counted.
9. Wall-clock seconds and `total_cost_usd` per session (as reported by the host; an
   API-equivalent estimate, which can differ from what a subscription is billed).

## Decision rule, written before running

No inference test. With this sample, differences are reported as counts with 95% Wilson
intervals and the sentence "not enough to claim an effect" unless the intervals are
disjoint, in which case the claim is limited to *this task, this model, this harness*.
A result that favours a control is reported with equal prominence. No run is excluded
after the fact; a run with an infrastructure failure is repeated once and both are
listed.

## Budget and stopping

Hard ceiling **USD 14** for this experiment (USD 20 was authorised in total, and the
live-demo rehearsal may use up to USD 2 of it). The harness sums the host-reported
cost after every session and refuses to start a session if the total plus the largest
session seen so far (times 1.5) would pass the ceiling. A pilot of one instance in
every arm (9 sessions) measures the real per-session cost first; the number of
instances is then the largest that fits with a 25% margin, capped at 10. If the pilot
shows even five instances cannot fit, the experiment is not run further and that is
reported.

## Threats to validity

* One model, one host, headless; small N; instances differ only by the random code.
* The fact is handed to A as an observation; the harness is sequential, so there is no
  true concurrency and no conflicting-edit exposure.
* B's two prompts being identical favours no arm, but the C1 relay is a stronger
  delivery mechanism than anything a person would reliably provide.
* The instance seed fixes the code and the arm order only; model sampling differs per
  run and is not seeded.

## Amendment 1 (2026-10-12, before any measured run)

The one-instance pilot (seed 1, nine sessions, about USD 0.022) found a **harness
defect**, not a result: in arm P agent-a's Bash permission rules did not cover the
single-quoted absolute path that the pickup hook prints in its `Reply:` line, so agent-a
was denied the command and could not answer (`permission_denials: 1`). The rules now cover
the relative, double-quoted, single-quoted and bare absolute forms. The pilot's outcomes
are reported as a shakedown in the results and are **not** part of the measured data;
measured instances use seeds 101 and up, so no seed is reused.

The pilot also measured cost: about USD 0.0025 per session, roughly a hundred times
below the assumption in coordination-experiment.md. The "capped at 10" instances rule
above was written against the old assumption; it is replaced by **20 instances (60 runs,
180 sessions)**, chosen from cost alone. The USD 14 ceiling and the stopping rule are
unchanged. Nothing else in the design changes.

## Amendment 2 (2026-10-12, before any measured run)

Two shakedown runs after Amendment 1 (seeds 1 and 101, arm P, one run each) were still
refused: the agents chained commands (`cd ... &&`, `...; echo exit=$?`) and the permission
rules only admit a plain `agent_mail.py` call, so the whole Bash call was denied
(`permission_denials` listed the commands). This is a property of the harness's
restricted-Bash mode, not of Postbox. The arm P instruction now adds one sentence: run
each mail command as its own Bash call, with no `cd`, `;`, `&&`, pipes or redirects. The
instruction in P is therefore longer than in C1 and C2; every run records its prompt
lengths. Measured seeds remain 101 and up; a third shakedown (seed 102, arm P) checks the fix.

**Seeds.** Seeds 101 and 102 were used by shakedown runs, so the measured instances are
seeds **201 to 220** (20 instances, 3 arms, 60 runs). Shakedown runs (seeds 1, 101, 102)
are listed in the results and excluded from the measured data, for the stated harness
reasons only.

# Results (2026-10-12)

Claude Code 2.1.296, model `claude-haiku-5-5`, headless, effort low. 20 instances (seeds
201 to 220) x 3 arms = 60 runs, 180 sessions, each arm run on every instance in a seeded
shuffled order, nothing excluded and nothing re-run. Spend by the host's `total_cost_usd`:
**USD 0.4507** (ceiling 14.00; the run was never near it). Wall-clock: P 483 s, C1 619 s,
C2 698 s of session time. Per-run records are in
[data/micro-experiment/](data/micro-experiment/) (`runs/`, `summary.json`; the
shakedown runs of seeds 1, 101 and 102 are in `shakedown/`, labelled by their seed). The raw
event streams (8 MB) are kept outside the repository and can be supplied on request.

## The pre-registered outcome

Did `app/config.py` end with the exact window code (`window_ok`)?

| Arm | `window_ok` | 95% Wilson | wrong code | left `None` | ticket A ok | completed |
|---|---|---|---|---|---|---|
| P, Postbox | 7 / 20 | 0.18 to 0.57 | 0 | 13 | 20 | 7 |
| C1, relay | 1 / 20 | 0.01 to 0.24 | 0 | 19 | 20 | 1 |
| C2, notes | 12 / 20 | 0.39 to 0.78 | 1 | 7 | 20 | 12 |

Messages (generated / delivered / acknowledged, per the definitions above): P 56 / 48 / 17;
C1 71 / 45 / n.a.; C2 60 / 60 / n.a. Missed communication equals the number of runs that
are not `window_ok` (P 13, C1 19, C2 8). Conflicting operations: not exercised by this
design. Manual interventions: none. Permission denials (a Bash call outside the allowed
mail command, mostly an attempt to test `is_weekend`): P 19, C1 45, C2 60.

**Reading it.** The decision rule allows no claim here: the P and C2 intervals overlap
(0.18 to 0.57 against 0.39 to 0.78). The point estimates favour the notes file, and
nothing favours Postbox. The outcome is a *negative or null result for Postbox on this
task*, and it is reported as such.

## What actually happened (post-hoc, labelled as such)

These were looked at after seeing the table, to understand it. They are not part of the
pre-registered metrics.

1. **The value usually reached agent-b, in every arm.** Counting a run if the code is in
   agent-b's final report or in its config: P 17 / 20, C1 18 / 20, C2 20 / 20. In all ten
   P runs where the ANSWER arrived and `window_ok` was false, the code appears in
   agent-b's own report. The failure is not delivery.
2. **Agent-b declined to use what it received.** Agent-a's text called the value
   unverified sandbox output (`probe.py` is not in the repository, which is true), and
   agent-b's instruction said not to guess. Haiku repeatedly chose to leave `None`. The
   task therefore measures how a consumer weighs a hedged fact as much as whether the fact
   moves. That is a design flaw of this pilot, not a finding about the tool.
3. **Both controls are confounded by the harness.** The control projects still contained
   `tools/agent-postbox` (installed without the adapter, which is how the shared project
   builder works). Agents mention the mailbox tool in 18 of 20 C1 runs and 14 of 20 C2 runs
   (counted in the first 400 characters of each session's report, so a lower bound). In C1
   they tried the mailbox CLI, were denied Bash, and wrote `TO TEAM:` lines about being
   blocked instead of relaying the code. The C1 number is not a fair measure of a relay and
   should not be quoted as one; C2 was less disturbed but is not clean either.
4. **The notes file was read in 60 of 60 sessions.** Every C2 agent read `NOTES.md` when
   told to. The premise that motivates the adapter (an agent that does not remember to
   look) did not occur when the instruction to look was explicit and the model was
   small. This agrees with `team_sim.py`, which found Postbox's advantage in missed facts
   vanishing once a control reads its notes reliably.
5. **Postbox took more steps.** P needs a send, a reply and a `show`; C2 needs a read and
   an append. P's delivered-to-B rate (17 / 20) is slightly below C2's (20 / 20). The P
   instruction was also longer (941 against 604 characters for agent-a), which this
   design disclosed.

## What this does and does not show

* It shows the harness works end to end with real sessions at about USD 0.0025 per
  session, that the Postbox chain (ASK, hook pickup, ANSWER, `show`) functions in the real
  host when the agent follows it (17 of 20 runs acknowledged), and that the real model
  behaves as the earlier simulation predicted when the control reliably reads its notes.
* It does **not** show that Postbox improves a team's outcome. On this task it did not,
  and the notes control, though not clean, did at least as well.
* It does not test the case the adapter exists for: an agent that does not read a shared
  file unprompted, over a long session, with several agents and conflicting edits.

## Threats specific to this run

One model (haiku), one task shape, headless sessions, sequential schedule, small N, a
scout whose fact is handed to it as an observation, a consumer told not to guess, a
longer instruction in P, and the C1 contamination above. Model sampling was not seeded.

## What would make a better second experiment (not run)

Remove the confounds first: no mailbox tool in the control projects; a scout told the value is
authoritative; the same instruction length in every arm; a control that does not tell the
agent to read the notes (the failure the README describes), run alongside one that does.
Re-use the harness; at this cost, 40 instances x 3 arms is about USD 1.
