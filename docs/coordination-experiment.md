# Does Postbox improve team outcomes? An experiment design, and an offline simulation of its mechanism

[benchmarking.md](benchmarking.md) shows that mail is delivered and the store
stays consistent under faults. [e2e-real-sessions.md](e2e-real-sessions.md)
shows that a real host puts the text in a real model's context. Neither
measures what a team of agents is for: **did the work get done, correctly,
without stepping on each other?** This page does two things and keeps them
apart.

1. **A design** for a repeatable experiment on team outcomes, with real model
   agents, against two ordinary-coordination control arms. It is *pre-registered
   here*: metrics, decision rule, sample size and stopping rule are fixed before
   any run. **No real-agent run has been made.** It needs the owner's explicit
   approval and spends usage (see "Cost model").
2. **A simulation**, `scripts/team_sim.py`, that exercises the design's
   machinery offline and deterministically with *scripted* agents. Its results
   are at the end, labelled **SIMULATED**. They say what the *mechanism* does
   under stated behavioural assumptions. They say nothing about real models.

## What this experiment can and cannot show

**The simulation can show:**

* that the metrics below can be computed from artifacts (git-style edit logs,
  mailbox files, a notes file, call logs), not from what an agent says;
* that the real mechanism (atomic claim refusal, lease expiry, unprompted
  pickup by the shipped adapter) changes the *outcomes* of a scripted team in
  the planted scenarios, given the assumptions listed with the results;
* where that advantage disappears as the control's behaviour improves
  (probability *p* that a control agent reads the notes).

**The simulation cannot show:**

* anything about how a language model behaves: whether it reads mail that is
  in its context, claims before editing, honours a refusal, reports honestly,
  or recovers well. Those are *inputs* to the simulation (seeded rules), not
  outputs;
* that the planted scenarios occur at any rate in real work;
* that the effect size is right. The advantage reported below is a function of
  the assumptions (a same-step race window, a patience of three steps, a notes
  file read with probability *p*); change them and it moves.

**The real experiment (when approved) could show**, for one model, one host
and one task family: whether teams using Postbox complete more hidden-test-
verified work, with fewer conflicting edits and fewer missed facts, than teams
using ordinary coordination, and what it costs in tool calls and tokens.

**It cannot show**: that the result generalises to other models, hosts, task
families or team sizes; that Postbox helps on tasks without cross-agent
dependencies (the task family is built to contain them, so it measures the
mechanism where it should matter, not how often that is); or *why* a gain
occurred beyond the ablations it includes. With the planned sample (36 paired
task instances) it can only detect large effects (about 10 percentage points of
ticket completion); small effects will be reported as "not detected", never as
"none".

## The task family

One *task instance* is a small Python repository (stdlib only, about 500 lines,
a test suite) and **eight tickets** handed to a team of four agents (one is the
*lead*). Everything is a pure function of the instance seed, and the hidden
acceptance tests are held by the harness, never in the agents' checkout.

| Planted property | Tickets | What it tests |
|---|---|---|
| Cross-dependency (twice) | a *producer* discovers a fact (a constant that appears nowhere in the repo or the ticket text, found by running the code); its *consumer* needs exactly that value | Is a fact that one agent holds used by the agent that needs it? The consumer can guess; the hidden test knows the true value |
| Shared file | two tickets edit one file; line ranges overlap in three instances of four | Do agents order their edits (claim, wait) or overwrite each other? |
| Blocked ticket | one ticket cannot start until the lead answers a question (the answer is a value the lead alone holds) | Does a question reach the lead, and the answer come back? |
| Crash | one agent is killed (SIGKILL of its process at its *k*-th tool call, *k* fixed by the seed) while working a ticket that nobody else knows how far along it is | Does its claim lapse, and does another agent recover the ticket, without duplicating live work? |

Ticket-to-agent assignment, order, line ranges, the facts and answers, and *k*
come from the seed. The agents see the ticket text, the repository and their
arm's channel; they never see the hidden tests, the seed or which property a
ticket carries. A generator for such repositories does not exist yet (it is
the first piece of work if the experiment is approved); `team_sim.py` uses the
same eight-ticket structure in the abstract.

## The arms

All arms: same model and version, same sampling settings, same tools (below),
same repository, same tickets, same per-agent turn and budget caps, same
total prompt length for coordination instructions (the control gets
equal-length, equally careful instructions about its channel, so the mailbox
instructions do not simply add attention; see threats).

* **P, Postbox.** The shipped installer wires the adapter into each agent's
  host as in [hosts.md](hosts.md); each agent has an identity; the instruction
  text is the README's quickstart (claim a scope before editing, `ASK`,
  `ANSWER`, `NOTICE` a finding, release by superseding).
* **C1, human relay.** The agents share the repository and **a chat that a
  human relays**. In the experiment the "human" is a fixed script (no judgment,
  no cost to the outcome, but every relayed message is counted as overhead): at
  the end of each agent turn, the lines the agent wrote under `TO TEAM:` are
  delivered at the front of every other agent's next prompt, one round later. No
  claim, no refusal, no lease, no structured status.
* **C2, shared notes file.** The agents share the repository and a `NOTES.md`
  in the checkout. The instruction says to read it before starting a ticket and
  to append a line when starting, finishing, asking, answering or finding
  something. Nothing is injected: an agent must *remember* to read it. No
  refusal, no lease.
* **C3 (optional ablation).** `NOTES.md` injected at every prompt by the same
  host hook mechanism as the adapter. It isolates delivery from claims and
  leases (P minus C3 is what the claim machinery and structured status add).

**Which control is the fairer comparison.** C1 is the *stronger* control: it
delivers every message to every agent at the next prompt, which removes the
delivery failure that is Postbox's most obvious advantage, so a remaining gain
must come from claims, leases and structured status. It is only fair if the
relay's cost is counted (it is: messages and relays are overhead), because a
real human relay costs attention that the experiment does not price. C2 is the
*fairer in cost* and the most common practice, and it is the failure the README
documents (a notes file nobody reads), so it is the more *representative*
comparison. **Pre-registered primary contrast: P versus C1** (if Postbox does
not beat a push relay, the delivery story was most of the story). P versus C2
is the secondary contrast and is reported in full. `team_sim.py` implements
only the C2 family, as the control reading with probability *p*; its *p* = 1.0
cell is the nearest it gets to C1 and C3 (reliable delivery, one-step delay).

## Randomisation, seeds, pairing

* The unit is the **task instance** (seed *s*). *Every* arm runs *every*
  instance (a paired design): the paired difference removes most of the
  variance that comes from instance difficulty.
* Instances `s = 1 .. N` are fixed in advance (below). The *order* of arm runs
  within an instance and across instances is shuffled by a seeded RNG
  (`random.Random(f"order:{s}")`), interleaved (ABBA-style blocks) so that
  model-side drift during the experiment does not line up with an arm.
* Runs are not re-rolled: one run per (instance, arm). A run that dies from
  infrastructure causes (a rate limit, a host crash) is repeated *once* and
  the failure is reported; a run that dies because of the agents' behaviour
  counts as an outcome.
* Once a generator exists, the same seeds can drive the simulation, so that
  `team_sim.py` and the real experiment can be compared instance by instance.

## Metrics, with operational definitions

Every metric is computed by one analysis script from **artifacts**: the git
history of the shared repository (each agent works in its own clone and pushes
to a shared bare repository, so authorship, parents and timestamps are real),
the mailbox files or the notes file, the host's stream-json transcripts (tool
calls, token usage), the harness's fault log, and the hidden tests. **Nothing is
taken from an agent's own report of itself**, except where that report *is* the
measured quantity (false success).

**Primary: M1, task completion.** The fraction of the 8 tickets whose hidden
acceptance test passes on the final `main` of the shared repository at the end
of the run (after the run's turn or time budget, whichever comes first). Each
ticket has one test (or a small fixed group) that fails on the starting
repository, passes on the reference solution, and is unaffected by the other
tickets' correct solutions. Paired difference *P − C1*, mean over instances.

Secondary, each reported with its paired interval, none used for the primary claim:

* **M2, conflicting edits.** Pairs of commits (A, B) by different agents on the
  same file with *overlapping changed line ranges* (from `git diff -U0` hunks,
  mapped through renames) such that neither commit is an ancestor of the other
  and neither author had the other's commit when starting (the later commit's
  base does not contain the earlier one). Count per run. A merge or rebase
  conflict reported by git at integration counts too. Reading: two agents
  worked the same lines without being ordered.
* **M3, missed communications that mattered.** For each planted fact *f*: *f*
  was written into the arm's channel (a mailbox file, a notes line or a relayed
  chat line) with a timestamp before the consumer's **first** commit on its
  ticket, **and** that commit does not contain *f*'s value, **and** the consumer
  ticket's hidden test ends failing because of it (the test checks the value
  directly). Count per run (0 to 2). Also reported: facts never written at all
  (the producer did not share), because that is a failure of the sender, not of
  the channel.
* **M4, unanswered questions at the end.** Questions with no answer: an `ASK`
  with no `ANSWER` citing it (mailbox, `status`'s open ASKs); in C2 a `Q:` line
  with no `A:` line for the same question id (agents are told the format); in C1
  a `TO TEAM: ?` line with no reply line. Count per run.
* **M5, recovery time after the injected crash.** Tool calls (and, separately,
  wall seconds) made by the surviving agents from the kill to the first commit
  after which the crashed agent's ticket passes its hidden test and keeps
  passing; **censored** at the run's end (recorded as the remaining budget, with
  a censoring flag, so the median and the proportion recovered are both reported).
* **M6, coordination overhead.** (a) *Messages*: mailbox files, notes lines or
  relayed chat lines written; (b) *tool calls* from the transcripts; both per
  *passing* ticket (a team that fixes nothing spends infinitely much per ticket;
  runs with zero passing tickets are reported separately, not averaged in).
* **M7, total time and tokens.** Wall seconds from first prompt to last event;
  input plus output tokens summed from the host's usage events (cache reads
  reported apart).
* **M8, false success.** The agent's final report for a ticket claims it is done
  (every agent ends a ticket with a parseable `DONE <ticket>` line in the arm's
  channel and in its last message) while the ticket's hidden test fails.
  Reported as a count and as a share of `DONE` claims. This is the one metric
  that *is* an agent's statement; the hidden test decides whether it was true.

Secondary metrics are **descriptive**: they explain a primary result, they do
not rescue a null one.

## Decision rule (pre-registered)

* **Primary claim "Postbox improves team outcomes"** is made iff, for the
  paired difference *D = completion(P) − completion(C1)* over the *N* task
  instances, the **95% bootstrap interval for the mean of D has its lower bound
  above +0.05** (half a ticket of eight; below this it is not worth the
  operational cost) *and* the false-success count for P is not larger than for
  C1 by more than its interval allows (the upper bound of the interval of the
  paired difference in M8 count is below +0.5 per run).
* If the interval includes 0: **"not detected at this sample size"**. If it is
  entirely below 0: "Postbox did worse". Nothing else is concluded from a null.
* The same rule is applied, and reported, for P versus C2 as the secondary
  contrast. A claim is made about the contrast it was tested on, never the other.
* The secondary metrics get the same interval, flagged `+` (P better, interval
  excludes 0), `=` (it does not), or `-` (control better), exactly as
  `team_sim.py` prints. No metric is promoted to primary after the fact; no
  subgroup (a single ticket kind, a single seed) is reported as a finding unless
  listed here. Exploratory looks are labelled exploratory.

## Sample size and statistics

* **Planning.** The simulation's paired difference in completion has an SD of
  about 0.06 to 0.09 (30 seeds; scripted agents do not vary like models). A real
  team varies more; plan with SD of the paired difference = 0.20 (about 1.6
  tickets), i.e. more than twice the simulation's. For a mean difference of 0.10
  (0.8 ticket) with 5% two-sided error and 80% power, *n* ≈ (1.96 + 0.84)² ×
  (0.20 / 0.10)² ≈ 31 pairs; for 0.05 it is ≈ 125, which this design does not buy.
  **N = 36 task instances** (the planned 32 plus four spare for invalid runs),
  fixed now, with **every arm run on every instance**. Pilot: 6 instances of P
  and C1 to check the harness; pilot data are *excluded* and not used to choose
  anything but bugs.
* **Intervals.** For each metric and contrast, the mean of the paired
  differences over the instances with a **percentile bootstrap, 10 000
  resamples of instances, seeded** (the simulation does the same with 2 000, the
  default of `--seeds`/`BOOT`). Also reported: the median difference, the
  proportion of instances in which P was better, tied, worse (a sign summary
  that does not depend on the mean), and, for counts, the ratio of means.
* **No p-hacking.** One primary metric, one primary contrast, one analysis
  script committed *before* the first non-pilot run (its hash is recorded in the
  results), the full list of runs (including failed and repeated ones) published
  with the results. No interim efficacy look. No exclusion of an instance after
  seeing its outcome; exclusions only for pre-stated infrastructure failures.
  Secondary metrics are not adjusted for multiplicity; they are labelled
  descriptive and the report says how many were examined.
* **Missing and censored data.** A run with no output counts as 0 completion
  (the team produced nothing) only if the cause is behavioural; infrastructure
  failures are repeated once. Recovery time is censored (above); medians are
  reported with the proportion censored.

## Threats to validity

* **Model non-determinism.** The same prompt gives different runs. Paired
  instances, a fixed *N* and the bootstrap address sampling noise, not bias;
  one run per cell means within-instance variance is part of the error.
* **Prompt sensitivity.** The result may depend on instruction wording more than
  on the mechanism. Mitigation: the control prompts are written *by someone
  other than the author of the Postbox prompt* or, failing that, are the most
  careful versions the author can write, all prompts are frozen and hashed
  before any run and published, and a prompt-perturbation sub-study (two
  paraphrases per arm on 6 instances) is the first thing to run if the result
  looks fragile.
* **The experimenter writes all arms' prompts.** A conflict of interest, not
  removable here. The control gets equal length and care, a second reader
  reviews the pair, and the committed prompts let anyone rerun with their own.
* **Contamination.** The tasks are synthetic and seeded and the facts are random
  values, so a model cannot recall them from training. But the project's README
  and the adapter's own text are likely in the training data of the model;
  that favours P and cannot be removed. Nothing is carried over between runs
  (a fresh throwaway repository, no memory, no shared session).
* **Hawthorne-like effects of the mailbox instructions.** Telling an agent
  "coordinate through the mailbox" makes it more careful about coordination
  whatever the channel. Mitigation: the control arms receive equally long,
  equally explicit instructions to coordinate through *their* channel, and C3
  separates delivery from claims. It cannot separate "any structured protocol
  helps" from "this protocol helps".
* **Small *n*, one model, one host, one task family.** Stated at the top. Do not
  extrapolate.
* **Construct validity.** Hidden tests measure what the task author thought of;
  a ticket can pass for the wrong reason, or fail for a reason unrelated to
  coordination. Reference solutions are run through the same tests to keep the
  tests honest, and a failure is reviewed (blind to the arm) only to correct a
  test bug, never to change a verdict.
* **The simulation's model is the experimenter's too.** Its agents do what the
  rules say; its advantage on a metric is as large as the rules make the
  mechanism matter. See the assumption list with the results.

## Cost model

There is no per-session dollar figure recorded in
[e2e-real-sessions.md](e2e-real-sessions.md) (its sessions were one-turn
`haiku` calls with tools denied; `scripts/e2e_claude.py` records `cost_usd` per
session from the host but the page does not quote it). So the numbers here are
**assumptions, to be replaced by the first pilot's measured `total_cost_usd`**:

| Quantity | Assumption |
|---|---|
| Team size, tickets | 4 agents, 8 tickets |
| Model turns per agent per run | about 40 (read, edit, test, coordinate, finish) |
| Input tokens per turn | about 20 000 (repository excerpts, history, tool output), a large share cache reads |
| Output tokens per turn | about 1 000 |
| Per team run | 160 model calls; about 3.2 M input and 160 k output tokens |
| Price (an assumption; check the account's current rates) | USD 1 per million input, USD 5 per million output tokens, cache reads at a tenth |
| Per team run | about USD 3 to 5 without cache help, about USD 1.5 to 3 with it |

| Plan | Team runs | Model calls | Dollars (low to high) |
|---|---|---|---|
| Pilot: 6 instances x {P, C1} | 12 | about 1 900 | about 20 to 60 |
| Primary: 36 instances x {P, C1} | 72 | about 11 500 | about 110 to 360 |
| Plus C2 (secondary contrast) | +36 = 108 | about 17 300 | about 160 to 540 |
| Plus C3 ablation | +36 = 144 | about 23 000 | about 215 to 720 |
| Repeats of infrastructure failures (10%) | +10% | +10% | +10% |

The realistic budget for the pre-registered primary plus the secondary contrast
is therefore **about USD 160 to 540, 17 000 model calls**, with a pilot first.
The owner's usage window is the binding constraint, not dollars: the plan is to
run in blocks, each blocked on explicit approval, and to stop at the cap
(below).

## Safety rules

* Every run uses a **throwaway directory** (a fresh bare repository and one
  clone per agent under a new temporary directory), removed afterwards. Nothing
  in the project's own checkout, no real credentials, no real remote.
* Tools restricted as in the e2e script: the host's allow-list names only the
  file tools inside the throwaway clone, `git` against the throwaway remote, the
  task's test command, and (arm P only) `agent_mail.py`. `--permission-mode
  dontAsk`, never `--dangerously-skip-permissions`. `--strict-mcp-config` (no
  connectors), `--setting-sources project`, child environment stripped of the
  parent session's variables, as the e2e script does.
* **No network** for the agents' tools (sandboxed or a deny-all egress rule; the
  model API is reached by the host, not by the agent's tools). Hooks run
  repository code, so the throwaway repository is the only thing a hook can run
  (docs/e2e-real-sessions.md, "Hooks fire in print mode").
* **Budget caps**: a hard cap on model turns per agent, a cap on tokens and cost
  per team run (the host's `total_cost_usd` from each result event, summed by the
  harness) and a **global cap** on cumulative cost and calls, declared in the
  command line like `--max-calls` is for the e2e script. A step that would exceed
  a cap is skipped, never started.
* **Abort conditions** (the whole experiment stops and says why): the global cap
  is reached; the account is rate limited (as the e2e script, the remaining runs
  are marked skipped, not retried in a loop); any attempt to touch a path
  outside the throwaway directory or to use the network is observed in a
  transcript; a run exceeds twice the expected wall time; the analysis script
  or the hidden tests fail their own self-check (reference solution must pass,
  starting repository must fail).
* **Explicit owner approval** before the pilot and before each block. Nothing
  here runs in CI. `scripts/team_sim.py` and its test are the only parts of this
  page that run anywhere unattended.

## Stopping rule

The experiment ends when all *N* = 36 instances have run in all pre-registered
arms (with the one repeat per infrastructure failure), **or** an abort condition
is met, **or** the owner stops it. There is **no early stopping for efficacy and
none for futility** on outcome data: stopping on a significant interim result
inflates error, and this design cannot afford a second look. If it stops early,
the report states the instances completed, shows the intervals as computed on
them and labels the result **inconclusive**, not as a trend.

## The simulation: `scripts/team_sim.py`

```sh
python scripts/team_sim.py --quick --seed 1            # the CI step, a couple of seconds
python scripts/team_sim.py --seeds 30 --json out.json  # the sweep below, about 15 s
```

Exit status as [benchmarking.md](benchmarking.md): 0 every integrity check
`OK`; 1 any `FAIL`, an aborted run or an exception inside the harness; 2 a
usage or tooling error. `test_team_sim.py` pins determinism (same seed, same
workload and same counts), that both arms get the same workload (the schedule
hash is taken from the file each run leaves behind), that a planted divergence
exits 1, a usage error 2, the JSON fields, the replay of the edit logs, and a
*regression pin that the harness cannot be rigged to favour Postbox*: at
*p* = 1.0, in the seeds where the arms tie on outcome and the control had no
conflict, the control's channel cost (calls, messages) must not exceed
Postbox's, and the verdict logic must be able to say "control better".

**What is real.** Every claim, release, fact, question and answer of the
`postbox` arm is `agent_mail.py send` run as a subprocess; every pickup is the
shipped adapter `hooks/agent_mail_check.py`; claim refusal (exit 3), lease
expiry and the superseding release are the tool's own. The clock is shifted per
call (the harness patches `agent_mail._now` and `datetime.datetime.now` in the
child, as `status --now` does for status), so that a ten-step lease takes no
real ten minutes; the dates in the mailbox are simulated time.

**What is simulated.** The agents: when one starts, how long it works, what it
claims, whether it reads, what it does when a fact has not arrived, and whether
it reports a wrong result as done are rules and seeded tables, not a model. The
`control` arm is one `NOTES.md` file the agent appends to and reads with
probability *p* per step (a seeded table, with the same uniform draws for every
*p*, so a higher *p* reads a superset of the lower one's reads).

**Same workload.** The tickets (eight, as above), owners, queues, files, line
ranges, values, the crash and the false-success draws are a pure function of the
seed; their SHA-256 is `schedule_hash` and must agree across the arms (a checked
invariant, `schedule-hash`). The read table is hashed apart (`read_hash`).

**Outcomes from artifacts.** The simulated repository is one append-only edit
log per file. `measure()` replays those logs (a commit that had the previous
revision layers on top; an unordered commit overwrites the other's lines and is
counted as a conflict), runs each ticket's hidden test on the final state,
counts facts and questions from the *mailbox files* or the *notes file*, counts
calls from the call log, and takes the agents' "done" reports from their report
file. It does not read the engine's variables.

**False success.** A consumer that has waited `--patience` steps without its fact
commits a guess. It reports done with probability `--false-success` (seeded per
ticket, identical in both arms); otherwise it stays on the ticket and reworks it
when the fact arrives. An agent that was overwritten by an unordered edit does
not notice and reports done. The hidden test then decides what was false.

**Assumptions the results depend on** (also in the JSON, `assumptions`):

* agents are scripts; a consumer waits up to 3 steps for its fact and then
  commits a guess;
* a knowingly wrong commit is reported done with probability 0.3, in both arms;
* control: an agent reads the notes with probability *p*, defers a file that
  shows a recent `START` by another agent it has read, and takes over a ticket
  past due only if it has read no recent `START` for it;
* postbox: the adapter delivers all live mail at the start of every step; a claim
  on the file is taken before editing and refused while another holder's lease
  is live; the holder renews before the lease lapses;
* an idle agent takes over a ticket past its due step (planned start + lease +
  its own index, a backoff both arms share); a crashed agent never returns; the
  lease is 10 steps;
* mail and notes written in step *s* are visible from step *s* + 1 in both arms;
  agents within a step act in a fixed order, so a claim race is decided by the
  tool, whereas a notes race is not (two control agents that read before either
  has written both start).

### Results (SIMULATED)

`python scripts/team_sim.py --seeds 30`: seeds 1 to 30, 4 agents, horizon 30
steps, lease 10, patience 3, false-success 0.3, Python 3.12, offline. All four
integrity checks `OK`. *diff* = postbox minus control; 95% percentile bootstrap
interval of the paired mean difference over the 30 seeds; "+" the interval
excludes 0 in Postbox's favour, "=" it does not, "-" it excludes 0 in the
control's favour (lower is better except completion). Means, per run of 8
tickets.

| Metric | postbox | control p=0.25 | p=0.5 | p=0.75 | p=1.0 |
|---|---|---|---|---|---|
| completion (fraction) | 0.99 | 0.94 (0.05 [0.03, 0.09], P better) | 0.96 (0.03 [0.01, 0.05], P better) | 0.96 (0.03 [0.01, 0.05], P better) | 0.97 (0.03 [0.01, 0.05], P better) |
| conflicting edits | 0.00 | 0.77 (-0.77 [-1.03, -0.50], P better) | 0.47 (-0.47 [-0.67, -0.27], P better) | 0.40 (-0.40 [-0.67, -0.17], P better) | 0.27 (-0.27 [-0.50, -0.07], P better) |
| missed facts at commit | 0.00 | 0.53 (-0.53 [-0.77, -0.33], P better) | 0.17 (-0.17 [-0.30, -0.03], P better) | 0.03 (-0.03 [-0.10, 0.00], no detectable difference) | 0.00 (0.00 [0.00, 0.00], no detectable difference) |
| facts unused at end | 0.07 | 0.20 (-0.13 [-0.30, 0.00], no detectable difference) | 0.13 (-0.07 [-0.17, 0.00], no detectable difference) | 0.13 (-0.07 [-0.17, 0.00], no detectable difference) | 0.13 (-0.07 [-0.17, 0.00], no detectable difference) |
| unanswered questions | 0.00 | 0.00 (0.00 [0.00, 0.00], no detectable difference) | 0.00 (0.00 [0.00, 0.00], no detectable difference) | 0.00 (0.00 [0.00, 0.00], no detectable difference) | 0.00 (0.00 [0.00, 0.00], no detectable difference) |
| recovery after crash (steps) | 11.30 | 12.40 (-1.10 [-1.83, -0.50], P better) | 11.73 (-0.43 [-0.93, -0.10], P better) | 11.60 (-0.30 [-0.77, 0.00], no detectable difference) | 11.57 (-0.27 [-0.60, 0.00], no detectable difference) |
| channel calls per passing ticket | 7.95 | 4.71 (+3.25 [2.90, 3.57], C better) | 5.51 (+2.44 [2.21, 2.67], C better) | 6.50 (+1.45 [1.22, 1.66], C better) | 7.56 (+0.40 [0.15, 0.60], C better) |
| steps to finish (30 if any ticket fails) | 15.30 | 21.27 (-5.97 [-8.47, -3.53], P better) | 18.87 (-3.57 [-6.03, -1.50], P better) | 18.73 (-3.43 [-5.90, -1.33], P better) | 18.20 (-2.90 [-5.07, -1.07], P better) |
| false success (tickets reported done, test fails) | 0.07 | 0.50 (-0.43 [-0.73, -0.20], P better) | 0.30 (-0.23 [-0.43, -0.07], P better) | 0.30 (-0.23 [-0.43, -0.07], P better) | 0.27 (-0.20 [-0.40, -0.07], P better) |

(Each control cell is the control mean, then, in brackets, the paired mean
difference postbox minus control with its 95% interval and the verdict; "P
better" and "C better" mean the interval excludes 0 in that arm's favour. The
script prints the full per-*p* tables.)

**Where the advantage vanishes.** On these assumptions:

* **Missed facts at commit** and **recovery time after a crash**: the advantage
  is gone from *p* = 0.75 on (the intervals include 0). At
  *p* = 1.0 a reader of the notes gets a fact at the next step exactly as the
  mailbox delivers it: the control is exactly as good. Recovery is bounded by
  the lease in both arms (a ticket cannot be taken over earlier than the owner's
  lease or the control's patience), so the mechanism buys little there except
  protection from taking over a live owner's ticket.
* **Facts unused at the end** and **unanswered questions**: no advantage detected
  at any *p* (the interval includes 0). With a 30-step horizon every question is
  eventually answered even at *p* = 0.25; the metric does not discriminate in
  this setup, and it should not be read as "Postbox answers no better".
* **Conflicting edits, completion, steps, false success**: the advantage does
  **not** vanish over the swept range, **including at *p* = 1.0**. This is not
  about delivery. It is the claim: two control agents that both read the notes
  before either has written start the same file in the same step and one
  overwrites the other, whereas the tool refuses the second claim. If the
  control also had an atomic reservation (which is what C3 plus a lock would be),
  this advantage would shrink toward zero; the simulation does not model that
  control.
* **Coordination overhead**: the control is *cheaper* at every *p*, by 0.4
  channel calls per passing ticket at *p* = 1.0 up to 3.3 at *p* = 0.25 (the
  control spends little at low *p* because it reads rarely). Postbox pays a
  pickup per prompt and a claim plus a release per ticket. This is a cost of the
  mechanism, not an artefact, and it is the metric on which the control wins.
* **Sensitivity to the other assumptions** (30 seeds each, the same sweep, run
  once after the first sweep and not tuned): with patience 6 the missed-fact
  advantage survives only at *p* = 0.25 (a patient consumer gives the notes more
  chances); with lease 6 the recovery advantage appears at every *p* and the
  facts-unused advantage at *p* = 0.25; with false-success 0.0 the recovery
  advantage also appears at every *p*; with 6 agents the map is unchanged.
  Conflicts, completion, steps and false success stay in Postbox's favour and
  overhead in the control's in all four. This checks that the headline does not
  hang on one parameter. It does not check whether it hangs on the *rules* (for
  example, a control agent that re-reads the notes right before it starts would
  close part of the conflict gap).

**What these numbers are.** The size of a mechanism's effect in a scripted team
whose rules were written by the experimenter, who also decided that a notes file
has no atomic reservation. They are a check that the experiment's measurements
work and a map of *where* the mechanism can matter. They are not an estimate of
what Postbox does for real agents. That is what the pre-registered experiment
above is for.
