# Experiment v2 (pre-registration, not run): does the pickup mechanism help on a realistic two-agent task?

Status: **a design, written before any model session**. Nothing here has been run and no
money has been spent; the harness exists (section 13) and is tested offline only. It follows
[micro-experiment.md](micro-experiment.md) (the pilot, a null result with confounds) and
the earlier design in [coordination-experiment.md](coordination-experiment.md). This page
does not claim that Postbox improves performance, and the experiment may show that it
does not. Approval to run is the owner's. The design is frozen by a commit that precedes
the first measured session; later changes are dated amendments, never edits.

## 1. What the pilot taught, and what changes

| Pilot defect (micro-experiment.md) | v2 change |
|---|---|
| The fact was handed to the scout as a hedged "observation"; the consumer was told not to guess. The outcome measured hedging. | The fact is **discovered by agent-a from a real file** in its checkout; nobody labels it unverified; no arm tells B "do not guess". A floor arm (N0) and a ceiling arm (O) show whether the task needs the channel and whether B can use a clean fact. |
| Mailbox tool present in control projects (18/20 C1, 14/20 C2 runs mention it). | Controls get **no mailbox tool**, checked from artifacts at the start of every run (section 8). |
| The notes arm was told to read the notes, which is the failure Postbox targets. | The primary control **N2 is told how to write notes, not to read them**, exactly as P is told how to send, not to read. N1 (told to read) stays as a secondary control. |
| P instruction longer (941 vs 604 characters) because restricted Bash needed extra rules. | Same Bash allow-list in every arm; arm paragraphs length-matched (within 10%, recorded). |
| Restricted Bash: 19 / 45 / 60 permission denials, mostly agents trying to run tests. | The allow-list covers the test runner and read-only shell for all arms; denials are a recorded metric. |
| One tiny ticket, 3 short sessions, one model, N = 20, no power calculation. | A small multi-file project, hidden tests, a mid-task discovery that invalidates work already done, N = 60 paired instances with a stated detectable effect. |
| No token counts recorded (only cost and turns). | Per-session usage from the host's result event is stored (section 11). |

## 2. The task (identical in every arm)

One *instance* is a seeded throwaway Python project, `ledger/`, about 8 files and 250
lines, with two tickets in `TICKETS.md` (present in both checkouts):

* **Ticket A (agent-a).** Implement `ledger/parse.py: parse_export(path)` for a vendor
  CSV export. A's checkout alone holds `vendor/sample_export.csv` (too large to commit,
  says the README). The sample's header block states two things: amounts are integers in
  units of `1/D` of the currency (D drawn per seed from {250, 400, 640, 800, 1250, 2000},
  never 100 or 1000), and voided rows carry a seeded token `VOID-xxxx`. A is not told to
  look for them; parsing the file correctly requires reading the header.
* **Ticket B (agent-b).** Implement `ledger/report.py: format_total(rows)` and
  `summarise(rows)`, which must convert amounts with the same `D` and skip voided rows.
  `TICKETS.md` says B's work depends on the vendor's unit and void marker, which only A's
  checkout has. B's checkout has no sample.

The agents work in separate checkouts and edit disjoint files, so conflicting edits are
**not exercised**. The discovery changes what B must do: with no information B's natural
move is a plausible default (cents, `D = 100`), which the seeded `D` makes wrong.

**Shared line in every prompt:** "You and the other agent are building this together.
Tell a teammate what they will need, using whatever the project provides." Nothing says
the fact is verified or unverified.

**Schedule (every arm, same fixed prompts).** S1 agent-b (starts, writes what it can),
S2 agent-a (does its ticket, discovers D and the token), S3 agent-b (prompt text: "Continue
your ticket and finish it."). The S1 default is what makes S3 a real test of looking:
B believes it is already done. S4 is the scripted nudge in section 6.

## 3. Arms

Every arm runs on every instance.

| Arm | Channel | What agents are told | Role |
|---|---|---|---|
| **P** Postbox | Real installer and adapter; the hook prints subjects at each prompt | How to `send`/`answer` (A, B). **Not** told to check mail | Treatment |
| **N2** notes, not told to read | `NOTES.md` in a shared dir; one README line "team notes: NOTES.md" | How to append a note. **Not** told to read it | **Primary control**: the failure mode the README describes |
| **N1** notes, told to read | As N2 | How to append **and** to read it at the start of each session | Secondary control (the pilot's C2) |
| **N0** no channel | None; no notes file | Only the shared line | Floor: shows the task needs a channel |
| **O** oracle | None | B alone, 1 session: D and the token are written plainly into its prompt | Ceiling: B can use a clean fact |

The pilot's scripted relay (C1) is dropped: delivery by the harness measures the harness.
P and N2 are symmetric on purpose: both are told how to write, neither is told to read,
so the contrast is what the adapter's automatic pickup adds. Any N2 pickup is the model's
own initiative. The arm paragraphs are written by one author and read by a second
reviewer before the freeze, hashed, and published with the results. Prompts written by the
author of the tool are a conflict of interest that cannot be removed; the published
prompts let anyone rerun with their own.

## 4. Success criteria and metrics (artifacts only)

After S3 (and before S4) the harness assembles A's `parse.py` and B's `report.py` into a
clean tree and runs hidden suites that the agents never see. Nothing is read from model
text.

| Metric | Definition (files, tests, the mailbox, or host events) |
|---|---|
| `a_ok` | Hidden parser suite passes (exit 0). |
| `b_ok` **(primary)** | Hidden report suite passes: correct `D` **and** void token handled. Per-fact `b_unit_ok`, `b_void_ok` are secondary. |
| `completed` | `a_ok` and `b_ok`. |
| `b_wrong_default` | B's code runs but fails only on `D` or the token (a guess), versus `b_missing` (does not import or run). |
| Delivered (P) | The subject of A's message appears in a UserPromptSubmit hook output event of B's S3. N2: n/a (nothing is pushed). |
| Picked up | P: the event stream shows B running `show` on the message, or the mailbox records it read, whichever the smoke stage shows is reliable. N2: a Read or shell call on `NOTES.md` by B in S3. |
| Acknowledged (P) | An ANSWER whose `reply_to` is B's ASK, or any message from B referencing A's. N1, N2: n/a. |
| Generated | P: files in the mailbox by type. N arms: lines appended to `NOTES.md`. |
| Missed communication | A wrote the fact to the channel and B's `b_ok` is false. |
| Wall-clock | Seconds per session and per run, measured by the harness around each `claude -p` call. |
| Cost and tokens | Section 11. |
| Permission denials | The result event's `permission_denials`, per arm. |
| Manual interventions | Section 6. |
| Conflicting operations | Not exercised (disjoint files); reported as such, never as zero. |

## 5. Randomization and ordering

* 60 instances, seeds fixed now: **3001 to 3060**. The seed draws `D`, the token and the
  arm order (a seeded shuffle per instance, as in `experiment_micro.py`). Smoke uses seeds
  9001 to 9003; the pilot's seeds (1, 101, 102, 201 to 220) are not reused.
* Instances run in blocks of 10 (all arms on all 10 before the next block), so a stop
  leaves whole paired blocks. Blocks may run in parallel in separate throwaway
  directories (at most 4 at once, fewer if rate-limited).
* Pairing: arms are compared within an instance (same `D`, token and project). Model
  sampling is not seeded and is part of the error.
* Not blinded: scoring is code, and the analysis script is committed and hashed before the
  first measured run.

## 6. Manual interventions, honestly

A headless run has **zero human nudges by construction**: nobody types anything, and the
harness reports `manual_nudges: 0` for every run. That is a property of the harness, not a
finding. For a measurable stand-in, **S4** gives every run whose `b_ok` is false at the end
of S3 one scripted nudge to agent-b: "Check whether a teammate has left you information,
then finish your ticket." The harness records, per arm, how many runs needed it, whether
`b_ok` became true afterwards, and the S4 cost. It is a proxy for "how often a person would
have to step in" and is labelled so. Primary outcomes are fixed **before** S4 and never
include it. At most one nudge per run.

## 6a. Sample size and power

N = **60 paired instances**, four arms plus the oracle. The primary contrast is P against
N2 on `b_ok`, tested with the exact McNemar test (two-sided, alpha 0.05) and reported with
a 95% interval on the paired difference. The numbers below come from a simulation of the
exact tests (600 to 1 500 draws per cell; no seed was recorded, so treat them as accurate
to about 3 points). `b` = share of instances where P is right and N2 wrong, `c` = the
reverse.

| Discordant rates (b, c) | Net difference | Power at N=40 | N=60 | N=80 |
|---|---|---|---|---|
| 0.30, 0.05 | +25 points | 0.76 | **0.92** | 0.98 |
| 0.20, 0.05 | +15 | 0.39 | 0.59 | 0.74 |
| 0.25, 0.10 | +15 | 0.27 | 0.43 | 0.57 |
| 0.15, 0.05 | +10 | 0.16 | 0.33 | 0.46 |

**Plainly:** at N = 60 the experiment can detect a net paired advantage of about **25
points** with 80% or better power. For +15 points power is roughly 40 to 60%, and for +10
about one in three: such a difference would most likely be reported as inconclusive. It
cannot detect anything smaller. For scale, an unpaired comparison of 30% against 60%
success is detected with power 0.89 at 60 per arm, 30% against 50% only 0.54.

Per-arm Wilson 95% intervals at N = 60: 15/60 gives 0.16 to 0.37; 30/60 gives 0.38 to
0.62; 45/60 gives 0.63 to 0.84. Half-widths are about 12 points, so two arms must be about
25 points apart before their intervals separate. The pilot at N = 20 (7/20 against 12/20,
0.18 to 0.57 against 0.39 to 0.78) was far inside that band.

The 25-point figure is a planning assumption, not an expectation. If Postbox's real effect
is smaller, the experiment will usually say "not enough" rather than "none".

## 7. Decision rule, written before running

**Validity gates** (computed first; if one fails, the contrast is void and is reported as
a failed experiment, not as a result):

1. O: `b_ok` at least 80% (B can use a clean fact; otherwise the task or model is broken).
2. N0: `b_ok` at most 15% (the task needs a channel; otherwise a default can pass).
3. No mailbox-tool mention or call in any non-P run (artifact grep); at most 2 runs may
   fail this, and they are listed.
4. P and N2 arm paragraphs within 10% in length.
5. At least 90% of sessions free of infrastructure failure after one repeat.

**Primary contrast, P versus N2 on `b_ok`, N = 60:**

| Result | Statement the project will make |
|---|---|
| Net P advantage, exact McNemar p < 0.05 and the 95% interval excludes 0 | "On this task, with this model and harness, Postbox's automatic pickup was associated with higher completion than an unprompted notes file." Limited to this setting; no general claim. |
| Not the row above, and the 95% interval for P minus N2 lies entirely below +15 points | "No advantage of 15 points or more" on this task. |
| P minus N2 negative with p < 0.05 | **Against Postbox on this task**, reported first, with the pickup and token cost. |
| None of the above | "Inconclusive at this sample size." |

**What counts as no advantage:** any outcome other than the first row. That includes the
likely one, in which N2 agents look at `NOTES.md` on their own.

**Secondary, descriptive only** (unadjusted, labelled, with the number of contrasts
stated): P versus N1 (the pilot's contrast), P versus N0, cost per completed task,
wall-clock, S4 recovery, delivery/pickup/acknowledgement. If P is *worse* than N1 by exact
test and costs at least 1.5 times as much per completed task, the report says so as a
finding against the current adapter design, whatever the primary result.

No run is excluded after the fact. Infrastructure failures (rate limit, host crash,
timeout) are repeated once and both runs are listed. One primary metric, one primary
contrast, one analysis script, no interim look at efficacy.

## 8. Confounds from the pilot and how each is removed

| Confound | Removal, and how it is checked |
|---|---|
| Hedged fact | The fact comes from a file A reads; no hedge in any prompt; O shows B uses a clean fact. |
| "Do not guess" | Removed. A wrong default is now a measurable failure (`b_wrong_default`). |
| Mailbox in controls | Control projects are built without `tools/agent-postbox` and `docs/agent-mail/`; the harness greps the tree and fails the run if either is present, and greps event streams for `agent_mail` in non-P arms (gate 3). |
| Notes arm told to read | N2 is not; N1 kept for comparison. |
| Instruction length | Length-matched within 10%, in characters and tokens, recorded. |
| Permissions | Same allow-list in all arms, denials counted. |
| Delivery scored from the wrong place | Delivery is scored from B's own hook event in S3. |
| Scout never discovering anything | A must read a real file to learn the fact. |
| The fact in B's repo | B's checkout holds no copy; the harness greps B's tree for the token at the start. |
| N = 20 | N = 60 with a stated detectable effect. |
| One model, one task shape, short sessions | **Not removed**; stated as scope (section 10). |

## 9. Models

* **Main run: `haiku`** (the pilot's model, reported as `claude-haiku-5-5`), effort
  `medium` for coding sessions (the pilot used `low`, which may under-do a multi-file
  task; the smoke stage fixes the level and it is then frozen). Cheap, and N = 60 is
  affordable.
* **A second model is not in the budget.** A model about five times the price would run P
  versus N2 only, 20 instances, about 130 sessions: an estimated USD 12 to 40 on top. It
  is worth buying only if the haiku result is not a floor or ceiling artifact (gates 1 and
  2 pass) and the owner wants to know whether a null is a small-model property. It needs
  separate approval and its own cap. A stronger model plausibly reads `NOTES.md`
  unprompted more often, which would shrink Postbox's room to help, so a second model more
  likely tests whether the effect disappears than whether it grows.

## 10. Simulated versus observed

Observed (real sessions): every session's files, tool calls, hook events, cost and turn
count, scored by hidden tests. Simulated: nothing; this experiment has no simulated
agents (`scripts/team_sim.py` remains a separate, labelled mechanism check). Constructed,
not natural: the project, the vendor sample, the tickets and the seeded fact. Headless,
sequential, one model, two agents, disjoint files, one discovered fact, sessions of tens
of turns rather than a working day. It does not test conflicting edits, long sessions,
many agents or a human in the loop.

## 11. Cost, tokens and the budget

**What is recorded today.** `scripts/e2e_claude.py` keeps only `num_turns` and
`total_cost_usd` per session. The pilot's records and `summary.json` contain **no token
counts** (nothing under `docs/data/` has them), so token figures cannot be quoted from the
pilot. The v2 harness is to store the result event's usage block (input, output, cache
creation and cache read tokens, per model) for every session. That the `stream-json`
result event carries it is believed, not verified here, and is a smoke check. Cost stays
the host's `total_cost_usd`, an API-equivalent estimate that may differ from a
subscription bill.

**Anchor.** Pilot: USD 0.4507 over 180 sessions, USD 0.0025 per session, about 5 turns per
session, so about **USD 0.0005 per turn** with three tiny files at effort low. Mean per
session by arm: P 0.0016, C1 0.0026, C2 0.0028.

**Extrapolation to the larger task** (an assumption, replaced by the smoke measurement):
cost per session = turns x cost per turn x context factor.

| Scenario | Turns per session | Context factor (bigger repo, longer history, effort medium) | USD per session |
|---|---|---|---|
| Low | 10 | 1.5 | 0.0075 |
| Likely | 15 | 2.7 | 0.020 |
| High | 30 | 4 | 0.060 |

**Sessions.** Per instance: P, N2, N1, N0 at 3 sessions each = 12, plus O at 1 = 13, plus
S4 nudges (assume 2 per instance on average) = 15. For 60 instances: 900 sessions.

| Stage | Sessions | Low | Likely | High |
|---|---|---|---|---|
| Smoke (3 instances, 3 oracle repeats each, S4 included) | about 55 | 0.4 | 1.1 | 3.3 |
| Main, 60 instances | 900 | 6.8 | 18 | 54 |
| Infrastructure repeats (10% of main) | 90 | 0.7 | 1.8 | 5.4 |
| **Total** | about 1 045 | **about 8** | **about 21** | **about 63** |

The high case is deliberately pessimistic. The design is about 6 times the pilot's 180
sessions at about 8 times the per-session cost (likely).

**Recommended hard budget: USD 55 in total** (smoke 4, main 51; amended 2026-10-14, see
section 13: the integrator lowered the main cap to **USD 30**, smoke stays 4). The likely total is about
USD 21, so 55 leaves a margin of about 2.5 times for a wrong extrapolation. The high case
would reach the cap, and the cap, not the estimate, then binds. A cap near USD 25 would stop
the run on a modestly wrong guess and waste what was spent; a larger one buys nothing the
design needs. Anything above is a new decision by the owner.

**Smoke stage (must pass before the main run), cap USD 4.** Three instances (seeds 9001 to
9003), all arms, S4 included. Pass requires **all** of:

1. Offline first, free: the reference solutions pass the hidden suites; a solution with
   `D = 100` or without void handling fails them; a leaked copy of the sample in B's tree is
   detected.
2. Pipeline: every session records cost, turns, usage tokens, denials and hook events;
   P's delivered, picked-up and acknowledged outcomes are readable from artifacts.
3. O reaches `b_ok` in at least 8 of 9 sessions; N0 in at most 1 of 3 runs.
4. The mailbox-tool leakage check passes in all non-P runs; prompt lengths within 10%.
5. Permission denials average at most 1 per run in every arm (else fix the allow-list and
   restart smoke).
6. **Cost gate:** mean cost per session at most USD 0.034. The main stage is projected as
   mean x 990 sessions (main plus repeats) x 1.5, which must be at most USD 51. If not,
   **the main run does not start** and the owner is asked; N is not silently reduced.

Smoke data are excluded from the results. Smoke may change only bugs, the allow-list and
the effort level; a change to arm prompts restarts smoke and is listed as an amendment.

**Stop rules in the main run.** The harness sums host-reported cost after every session
and refuses to start one if spent plus 1.5 times the largest session so far would pass the
cap. It also stops at: two rate-limit rejections in a row (wait; no blind retry); more than
10% of the first 20 instances ending in infrastructure failure; or validity gate 1 or 2
failing on the first 20 instances (an invalidity check on the O and N0 arms only, never an
efficacy look at P against N2). A stop leaves whole 10-instance blocks; the report then
states the completed N and makes no inferential claim below N = 60, only counts and
intervals.

**Wall-clock.** The pilot took about 10 s per session. The larger task is expected at 40 to
90 s per session, so 15 to 20 minutes per instance in series, 15 to 20 hours for 60
instances, or 4 to 6 hours with four workers. The owner's usage window, not dollars, may
be the binding constraint.

## 12. Ethics and safety

No external services except the model host the owner is already signed into. All work
happens in throwaway directories under a per-run temp root, deleted afterwards; the
mailbox and notes live inside them, and the real home directory and mailbox are not touched
(the harness isolates them as the pilot did). No `--dangerously-skip-permissions`:
`--permission-mode dontAsk` with an explicit allow-list and `--strict-mcp-config`. The
task data is synthetic with random values: no personal data, nothing posted by the
experiment. Raw event streams may contain model text; they stay out of the repository (as
in the pilot) and per-run summaries are published.

## 13. Harness changes (built offline in `scripts/experiment_v2.py`, `scripts/analyze_v2.py`, `test_experiment_v2.py`; no live run yet)

1. A new instance builder: the `ledger/` project, `TICKETS.md`, a per-seed vendor sample,
   two separate checkouts, a shared channel directory; reference solutions and two hidden
   suites (the `test_micro_experiment.py` pattern, offline).
2. Arms P, N2, N1, N0, O, with a project builder that can omit `tools/agent-postbox` and
   `docs/agent-mail/` for non-P arms, and asserts the omission.
3. A common Bash allow-list (test runner, read-only shell) replacing the restricted-Bash
   mode; length-matched arm paragraphs with recorded character and token counts.
4. The session record extended with the `usage` block, model, per-session wall-clock and
   the raw event stream path; the cost ceiling from `experiment_micro.py` reused.
5. A scorer that assembles A's and B's files and runs the hidden suites, plus pickup
   detection from tool-call events (`show`, Read or `cat` of `NOTES.md`) and from the
   mailbox.
6. The S4 scripted nudge, after scoring; block scheduling (10 instances per block), a
   worker pool with rate-limit backoff, and a `--stage smoke|main` switch whose smoke gate
   is written to a file the main stage must find.
7. An analysis script (exact McNemar, Wilson, paired-difference interval, cost per
   completed task) committed and hashed before the first measured run.

Reused as is: Wilson, seeded arm order, cost accounting and ceiling, the stream-json
session driver.

**Amendment 2026-10-14 (before any measured run).** The main-stage hard ceiling is **USD 30**
(a flag, default 30; smoke default 4), not 51. The smoke cost gate keeps its two parts: mean
cost per session at most USD 0.034, and a projection (mean x 990 x 1.5) that must not pass the
main ceiling; with a cap of 30 the second part needs a mean of about USD 0.020 or less, so it
is the binding one and a mean between 0.020 and 0.034 stops the main run for the owner to
decide. The harness refuses `--live` without `POSTBOX_EXPERIMENT_LIVE=1`, and the main stage
without a passing, non-dry-run smoke gate whose arm prompts, model and effort match. S4 is
given to arms P, N2, N1 and N0 only (O has a single session). Per-fact flags use test names
in the hidden suites; a token guess such as `startswith("VOID")` would pass the void test
(the doc fixes the token's form), so `b_void_ok` is weaker than `b_unit_ok`; `D` is the
discriminating fact. Character counts are recorded for the arm paragraphs; token counts are
not (the host reports usage per session, and no tokenizer is invented).

**Amendment 2026-10-14, after the first live smoke run (still before any measured run).** The
first smoke run (3 instances, 48 sessions, USD 0.24 of the 4 ceiling, mean USD 0.005 per session,
projected main cost USD 7.50) passed every check except "denials average at most 1 per run in
every arm": N0 3.3, N1 5.7, N2 4.3, P 3.3 (O 1.0). The denied calls were of two kinds, in all arms:
chained shell commands (`for` loops, `cd ... &&`, `;`) that the plain-command allow-rules reject,
and the Edit tool on `NOTES.md`, which the host refuses because the file is a symlink to the
shared directory (checked directly: an explicit `Edit(<real path>/**)` rule does not change it;
the agent falls back to a shell `echo >>`). Two prompt changes follow, both before any measured
run, so the smoke stage is repeated (the prompt hash restarts it): every agent's intro gains "Run
each shell command as its own Bash call (no `cd`, `;`, `&&`, loops or pipes) and use Read to
read files"; the notes arms say to append with a shell command "(the Edit tool cannot write this
file)" and the mail arm says to send mail "never through the Edit tool", which keeps the P and N2
paragraphs within 10% (346 and 363 characters). The first smoke run's data is kept outside the
repository and is not part of any result. The O arm also showed `a_ok` false by design (it has a
single session for agent-b).

**Amendment 2026-10-14, after the second live smoke run (still before any measured run).** The
repeat smoke (USD 0.23) failed two checks. Denials were still above 1 per run in the non-oracle
arms (N0 4.0, N1 5.3, N2 6.7, P 2.3), and **N0, which has no channel at all, reached `b_ok` in 2
of 3 instances (gate: at most 1)**. The raw events show why: agent-b ran `find` on its parent
directory (allowed by `Bash(find *)`), saw agent-a's checkout beside it, and used the Read tool
on `a/vendor/sample_export.csv` (instance 9001); in instance 9002 it listed that file. The hidden
fact was reachable through the filesystem layout, so the task could not separate any channel from
none, which is what the N0 floor gate exists to catch. Fix: each checkout now sits in its own
container directory (`checkout_of_agent_a`, `checkout_of_agent_b`), so listing a parent shows only
one's own tree; every session gets deny rules for the Read, Edit and Write tools and for any Bash
command that names the other agent's container (tested live: the file tools and `cat` and `find`
naming it are refused); and every session record carries `cross_checkout`, true if any tool call
named the other container, so an attempt is reported even though it is refused. The denial count
still includes refused attempts, so the gate "denials at most 1 per run" stays and now also
measures leakage attempts. The data of both earlier smoke runs is kept outside the repository and
is not part of any result.

**Amendment 2026-10-14, after the third live smoke run (still before any measured run).** With the
leak closed, the no-channel arm passed 0 of 3 and the oracle 9 of 9, but denials stayed high in the
notes arms (8.0 per run). The reason was worse than noise: **all 36 notes appends in the notes
arms were refused and none succeeded**, so in those arms nobody could write a note while the mail
arm worked. Direct tests (single sessions, about USD 0.03 in all) showed that no allow rule lets
the host's `dontAsk` mode run a shell command containing a `>>` redirect (`Bash(echo *)`,
`Bash(echo * >> NOTES.md)`, `Bash(echo:*)` and a redirect rule all refused it, even on a plain
file in the working directory), and that the Edit tool refuses a symlink. What works, with zero
denials: a real file in the shared directory, added with `--add-dir`, edited with the Edit or Write
tool by relative path. The amendments above that told the notes arms to append with `echo >>` are
therefore **superseded**. Now: the notes file is a real file `../../shared/NOTES.md`, reached by
that path from either checkout (no symlink); the notes arms are told to add a line to it with the
Edit or Write tool and to leave its heading alone (N1 is also told to read it at the start of a
session; N2 is not); the mail arm's wording drops "never through the Edit tool"; the P and N2
paragraphs are 317 and 308 characters, inside the 10% gate. Had the main stage been run on the
earlier design, the notes arms would have measured the harness, not the notes file, and the result
would have favoured Postbox. The data of all smoke runs is kept outside the repository and is not
part of any result.

**Amendment 2026-10-14, after the fourth live smoke run (still before any measured run).** The notes
arms now wrote notes (3 to 5 lines per run), the no-channel arm passed 0 of 3 and the oracle 9 of 9.
One check still failed: denials averaged 1.7 to 2.3 per run in the non-oracle arms (limit 1). The
denied calls were 12 ad-hoc `python3 -c` self-checks, spread across the arms (P 4, N0 3, N1 3, N2 2),
and about 11 attempts to list the work root or the other agent's checkout, which the isolation
rules refuse. None was a use of a channel. The limit of 1 is kept, not loosened. Instead the
common intro of every arm adds "work only inside your checkout, and check your code with the unit
tests, not `python3 -c`" (the same words for every arm, so it cannot favour one), and the smoke
gate gains a direct check, **no call to the mail command or the notes file was ever refused**,
which is what the denial count was standing in for. A refused channel call is recorded per run as
`channel_denials`. The smoke stage is repeated again.

**Freeze 2026-10-14, before the first measured run.** The fifth smoke run passed every gate check
(48 sessions, USD 0.23, mean USD 0.0048 per session, projected main cost USD 7.1, the original
limit of 1 denial per run kept). The main stage is run from exactly these files, and the analysis
is run only from the frozen script:

| What | SHA-256 |
|---|---|
| `scripts/analyze_v2.py` | `f73eaa2020433b9317477822fec0754d3f0689530ff816a27e4db38e06f9ac0c` |
| `scripts/experiment_micro.py` (its Wilson interval is imported) | `082d269a32a2e98f415378798e99fbe207153e59233e9e0f45d51a557887ea7c` |
| arm prompts (`prompts_hash()`, also in the smoke gate) | `c9b4536b485074e13677edb0fe51f40fa4521c5b199dd30603e496f9bbd44c8a` |
| `scripts/experiment_v2.py` (the smoke gate records it) | `9370ffbe7a8caedcf67cecc4f0ad926df75e68a88725bf3bb915ab6ec0858629` |

Main stage: 60 paired instances (seeds 3001 to 3060), model haiku, effort medium, hard ceiling
USD 30, `POSTBOX_EXPERIMENT_LIVE=1 python3 scripts/experiment_v2.py --stage main --live --gate
<smoke gate>`. The decision rule in section 7 is unchanged. If any of the files above changes, smoke
is repeated and this table gets a new dated entry; nothing here is edited.

## 14. What the project will say

This experiment may again show **no advantage**, and a null result is the likeliest single
outcome given the pilot and the simulation. If it does, the project will say so in the
README and the results page, with the numbers: "on this task, with this model, an
unprompted notes file did about as well as Postbox, and the experiment could not rule out
differences of up to 15 points." It will not conclude that Postbox does not help elsewhere,
and it will not re-run with a bigger N, a different prompt or a different task until the
result is favourable. If the result is against Postbox, it is reported with equal
prominence and the adapter's pickup design is examined before any further claim. A
favourable result would be stated as limited to this task, model and harness, with the cost
of the extra steps beside it.

## 15. Open questions for the owner

1. Approve the **USD 55** hard budget, or set another number (below about USD 25 the design
   does not fit).
2. Is a second, larger model worth a separate, capped run (section 9)?
3. Effort level: medium (assumed here) or `low`, which is cheaper but may under-do a
   multi-file task?
4. May the project author write the arm prompts with a second reader, or should someone else
   write the controls?
5. Is S4 (the scripted nudge, about 15% of sessions) wanted, or is "zero by construction"
   enough?
