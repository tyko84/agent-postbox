# Experiment v3 (pre-registration, main stage NOT run): does mail reach an agent that is working through several prompts?

Status: **a design, written before any measured data.** The owner approved preparing and
running v3 with a USD 15 hard stop; the main stage has **not** been run, and the readiness
check ([experiment-v3-readiness.md](experiment-v3-readiness.md)) recommends against running it
as designed (the control arm looks to be at the ceiling by construction; see section 9). This
page changes nothing in [experiment-v2.md](experiment-v2.md) or
[experiment-v2-results.md](experiment-v2-results.md). It follows
[experiment-v3-proposal.md](experiment-v3-proposal.md). The design is frozen by a commit that
precedes the first measured session; later changes are dated amendments, never edits. Readiness
sessions are not measured data and are excluded from every result.

## 1. Why v3 (what v2 could not show)

v2's control (N2) completed 60 of 60, so Postbox could only tie or lose; its P arm was told to
send with an inline `--body '<text>'`, and 9 of 60 runs hit a host refusal the notes arms could
not hit; and it tested one re-entry right after the other agent finished, the easiest case for a
notes file. v3 asks the question Postbox's mechanism is about: **when an agent works through
several prompts and is not reminded to look, does mail reach it more often than a notes file
does?**

## 2. Hypotheses

* **H1 (primary).** Agent B, one live session with four scripted prompts, ends with the fact
  correctly in its code (`b_ok` after prompt 4) in more instances with Postbox (arm P) than with
  a shared notes file it was told how to write but not to read (arm N2). Two-sided test; the
  alternative that P is worse is tested too and reported first if true.
* **H2 (floor).** Without a channel (N0) B does not get the fact (`b_ok` at most 15%).
* **H3 (ceiling check).** With the fact in its first prompt (O) B uses it (`b_ok` at least 80%).
* Secondary, descriptive: `b_ok` after prompts 2 and 3; at which prompt B first looked at the
  channel; cost and wall-clock per run; refusals of the channel command.

## 3. The task and the schedule (identical in every arm)

The v2 instance (`scripts/experiment_v2.py`: the seeded `ledger/` project, `TICKETS.md`, hidden
suites, the vendor sample only in agent-a's checkout, `D` drawn from {250, 400, 640, 800, 1250,
2000}, a `VOID-xxxx` token) is reused unchanged. Schedule per run:

1. **B prompt 1** (live session opened): intro, "Do your ticket now; stop when you have done what
   you can in this session.", the shared line, the arm's paragraph.
2. **A session** (separate `claude -p`; arms P, N2, N0 only): same wording for its ticket;
   discovers `D` and the token and, in P and N2, writes them to the channel.
3. **B prompts 2, 3, 4** into the same live session (`--input-format stream-json`, as
   `scripts/e2e_claude.py` T3 does): "Continue your ticket and finish it." / "Run the unit tests
   and fix anything that fails, then finish your ticket." / "Make sure your ticket is complete."
   None mentions a channel, a teammate or the facts, and they are identical in all arms.

Arm O has B's four prompts only, with the fact written into prompt 1. There is no S4 nudge
(v2's proxy is dropped); manual nudges are 0 by construction, a property of the harness, not a
finding. `claude -p`, haiku, effort medium, `--permission-mode dontAsk`, tools
`Bash,Read,Edit,Write`, `--setting-sources project`, `--strict-mcp-config`, v2's per-container
isolation (deny rules for the other agent's checkout).

## 4. Arms, and the exact paragraphs and rules

| Arm | Channel | Told | Role |
|---|---|---|---|
| **P** | Real installer and adapter; the hook prints subjects at each prompt | How to send with the heredoc form. Not told to read | Treatment |
| **N2** | `../../shared/NOTES.md`, a real file (not a symlink), plus a README line | How to add a line with Edit/Write. Not told to read | Primary control |
| **N0** | none | Only the shared line | Floor |
| **O** | none | The fact, in B's prompt 1 | Ceiling |

N1 (told to read) is dropped. Paragraphs, verbatim from `PARAGRAPHS` in `scripts/experiment_v3.py`
(`{me}`/`{other}` filled with agent-a/agent-b):

* **P:** "Team mail uses \`python3 tools/agent-postbox/agent_mail.py\`. To tell {other} something,
  run \`python3 tools/agent-postbox/agent_mail.py send --type NOTICE --from {me} --to {other}
  --subject '<subject>' --body-file - <<'EOF'\`, the text on the lines after it, and \`EOF\` on its
  own line, all in one Bash call. Write the text without curly braces." (344 characters)
* **N2:** "Team notes live in ../../shared/NOTES.md, a file you both can edit. To tell {other}
  something, add one line to it with the Edit or Write tool, keeping the earlier lines. Add one
  line for each thing a teammate will need, and keep each note to one line of plain text without
  curly braces. Leave the heading '# Team notes' as it is." (329 characters)

The "no curly braces" sentence in both paragraphs is there because the readiness probe found the
host refuses a heredoc `send` whose body contains `{'count': live, ...}` although an allow rule
matches it (readiness doc, check b); it is stated in both arms so it cannot favour either.

**Allow rules, the same in every arm** (`COMMON_ALLOW`): `Read`, `Edit`, `Write`,
`Bash(python3 -m unittest *)`, `Bash(python3 -m unittest)`, `Bash(python3 tests/*)`, `Bash(ls *)`,
`Bash(ls)`, `Bash(pwd)`, `Bash(cat *)`, `Bash(head *)`, `Bash(tail *)`, `Bash(grep *)`,
`Bash(find *)`, `Bash(wc *)`, `Bash(sed -n *)`, `Bash(echo *)`. **P adds exactly four rules** for the
mail command, one per spelling: `Bash(python3 tools/agent-postbox/agent_mail.py *)` and the
absolute path plain, double-quoted and single-quoted. N2 adds `--add-dir <shared dir>` (the notes
file is outside the checkout). Every session also gets deny rules for the other agent's container.
The readiness probe verified that these rules let the taught forms through (readiness doc).

## 5. Experimental unit, randomization, order

Unit: one *instance* (seed), all four arms run on it (paired). 60 instances, seeds **4001 to
4060** (readiness uses 9101 to 9104; v2's seeds are not reused). The seed draws `D`, the token
and the arm order (a seeded shuffle of the four arms, recorded as `order` and `order_index`).
Blocks of 10 instances (all arms on all 10 before the next block); up to 4 instances in parallel.
Model sampling is not seeded and is part of the error. Not blinded; scoring is code.

## 6. Symmetry table (computed by `symmetry()` and written to the manifest)

| | P | N2 | N0 | O |
|---|---|---|---|---|
| Tools | Bash,Read,Edit,Write | same | same | same |
| Common allow-list | 17 rules | same | same | same |
| Extra allow rules | 4 (mail command) | 0 | 0 | 0 |
| `--add-dir shared` | no | yes | no | no |
| Paragraph characters (B) | 344 | 329 (within 10%) | 0 | 0 |
| B prompts 2 to 4 | identical | identical | identical | identical |
| Channel words in prompts 2 to 4 | none | none | none | none |
| Mailbox tool in the project | yes | no (asserted) | no (asserted) | no (asserted) |

The asymmetries that remain are the channel itself (the mail command rules, the notes directory)
and the hook, which is the thing under test.

## 7. Outcome and scoring rubric (artifacts only)

After each B prompt the harness assembles A's `parse.py` and B's `report.py` into a clean tree and
runs the hidden suites (v2's `score`). **`b_ok` after prompt 4 is the primary outcome**: the
`format_total` test passes with the right `D` **and** voided rows are skipped (shape test
included). `b_ok` after prompts 1 to 3 are recorded. Also recorded: `a_ok`, `b_unit_ok`,
`b_void_ok`, `b_wrong_default`, `b_missing`; P delivered (A's subject appears in the hook output of
a B prompt 2 to 4), picked up (B ran `show`), acknowledged (a message from B referencing A's); N2
picked up (Read or shell read of `NOTES.md`; an Edit/Write of it is not counted as a read, a known
gap, see the readiness doc); missed communication (A wrote and `b_ok` false); per-session cost,
usage, wall-clock, permission denials (with the denied command text); the first prompt at which B
looked. Nothing is read from model text.

## 8. Validity gates, exclusions, repeats, refusals

Gates (computed first; 1 to 5 failing makes the contrast **void**, a failed experiment, not a
result):

1. O `b_ok` at least 80%. 2. N0 `b_ok` at most 15%. 3. No mailbox-tool mention or call in any
non-P run (artifact grep; at most 2 may fail, listed). 4. P and N2 paragraphs within 10% in
length. 5. At least 90% of sessions free of infrastructure failure after one repeat.
6. **Ceiling gate (new).** N2 `b_ok` below 95% of usable runs. If N2 is at or above 95% the
contrast is declared **uninformative, not a tie**: with the control at the ceiling Postbox could
only tie or lose, so "no difference" would say nothing. The main stage also stops early if N2 is
at or above 95% on the first 20 instances (a look at the control arm only; no look at P against
N2).

**Exclusions.** No run is excluded after the fact. An infrastructure failure (rate limit, host
crash, timeout) is repeated once (attempt 2); both attempts are kept and listed; the last attempt
is the run; an instance whose arm fails twice is unpaired for that contrast. Aborts by the spend
guard are listed and are not results.

**Host refusals of the channel command.** A run in which the host refuses the mail command or the
notes file is a failure of that arm by the outcome (it counts in `b_ok`), AND is reported
separately with the refused command text and the cause (`host_refusals`; `analyze_v3.py` prints,
per arm, runs with a refusal, how many of them ended with `b_ok` false, and the commands).
To keep refusals from being self-inflicted, the readiness check must show the P arm's allow rules
let its taught forms through, including a body with `#` lines (section 9 of the readiness doc).

## 9. Readiness (what has been checked, before any measured run)

See [experiment-v3-readiness.md](experiment-v3-readiness.md): the pipeline runs offline and live,
all four arms completed instances end to end, the allow rules let the taught forms through, and a
small tally of the control arm (N2 completed in every instance tried) points to the ceiling
problem the gate in section 8 exists to catch. That page's recommendation governs whether the
main stage is run.

## 10. Power and sample size, honestly

N = 60 paired instances, primary test exact McNemar (two-sided, alpha 0.05) with a 95% interval
(Newcombe method 10) on the paired difference. As in the v2 pre-registration (its sample-size subsection): net advantage about **25
points** detectable with 80% or better power; +15 points about 40 to 60%; +10 about one in
three; nothing smaller. If N2 is near 90% there is little room above it at all, and if it is at
95% or more the gate makes the contrast uninformative. A null, a tie, or "inconclusive" is the
likeliest report.

## 11. Cost

Measured in readiness, not assumed: an instance (4 arms, 19 turns) cost about USD 0.05 (range
0.04 to 0.06) with haiku at medium effort; v2's measured mean was USD 0.0048 per session.
Projection for the main stage: 60 x 0.05 x 1.1 (repeats) = about **USD 3.5**, with a margin of
1.5 about USD 5.3. **Hard ceiling USD 15** (flag `--ceiling-usd`, default 15): the harness
refuses to start a turn if spent plus 1.5 times the largest turn so far (times turns in flight)
would pass it, writes `spend.json` after every turn, and the readiness gate requires the margin
projection to fit. Cost accounting: the host's `total_cost_usd` is a running total per live
process, so each B prompt is billed as the difference (measured, readiness doc).

## 12. Decision rule (v2 section 7, copied unchanged) and what the project will say

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

v3 adds validity gate 6 (section 8) ahead of the primary contrast; nothing else in the rows above changes.

**What the project will say, by outcome** (mirrors v2 section 14; none of this is run):
* *Gate failure (1 to 5):* "the experiment failed its validity gates" and why; no claim.
* *Uninformative (gate 6):* "the notes file was at the ceiling in a multi-prompt session, so this
  design cannot show an advantage; this is not evidence for or against Postbox." No re-run with
  a different task or prompt until the owner decides in a new pre-registration.
* *Net P advantage:* the first row's sentence, limited to this task, model and harness, with the
  cost of the extra steps beside it.
* *No advantage of 15 points or more / inconclusive:* said plainly, with the numbers.
* *Against Postbox:* reported first, with the refusal report and cost; the adapter is examined
  before any further claim.
It will not conclude that Postbox does not help elsewhere, nor re-run until favourable.

## 13. Scope, simulated versus observed

Observed: every session's files, tool calls, hook events, cost and denials. Constructed: the
project and the seeded fact. Offline tests use simulated agents only (`FakeDriver3`, labelled
SIMULATION); they check plumbing, not behaviour. Not covered: other models, interactive use, hosts
other than Claude Code, conflicting edits, long sessions, many agents, humans in the loop, or
whether coordination outcomes improve in real teams. Prompts are written by the tool's author;
they are published (and hashed) so anyone can rerun with their own.

## 14. Freeze table (filled 2026-10-15, before the first measured run)

Frozen at `main` = the commit that merged PR #54 (`810e378`) plus this table. Produced with
`python3 scripts/experiment_v3.py --freeze`. A change to any file below restarts readiness and
gets a dated entry here.

| What | SHA-256 |
|---|---|
| `scripts/experiment_v3.py` | `0ee5731d2315369e5083e36b948abaf7dc86f679fa6c0c720e4c886f73fb0d1c` |
| `scripts/analyze_v3.py` | `3fe68c0a7d53c849e8028d96d8b9021aeb9971a170a5ec7626fa651b3f882d69` |
| `test_experiment_v3.py` | `88b2eb19079939ffce2f11320508254bfb9ea4238c3623bbd81cc6a69a414e6b` |
| `scripts/experiment_v2.py` (imported) | `9370ffbe7a8caedcf67cecc4f0ad926df75e68a88725bf3bb915ab6ec0858629` |
| `scripts/analyze_v2.py` (imported) | `f73eaa2020433b9317477822fec0754d3f0689530ff816a27e4db38e06f9ac0c` |
| `scripts/experiment_micro.py` (imported) | `082d269a32a2e98f415378798e99fbe207153e59233e9e0f45d51a557887ea7c` |
| `scripts/e2e_claude.py` (imported) | `99788b5fc83d6d3ff9e733154e6a4cd869b13f12e3f92ab467159a4e1c893237` |
| `agent_mail.py` (the P arm's tool, as installed) | `9e802a18fb0ce34f480fabab8c005c505a6d435aa4fbd31fa4bb5c715d9565ec` |
| `install.py` | `6ddab27ddb096092d1f90b7d9b303d8532f5f3242d26fb47862c7c3aa4963c32` |
| arm prompts (`prompts_hash()`) | `a86caa21ffdd274738a4f67075aa1d8256db4fdfaca9bf04e6255325b7b7ec06` |
| readiness gate file (smoke 5, `passed: true`) | `529c4befec8c8b4acbb7a9bd38a012bfc34c0746817f6d90ed72a9e9d9206694` |

Dated entries after the readiness report:

* 2026-10-15: after the readiness report the integrator (a) changed only type annotations and
  `# type: ignore` comments in `scripts/experiment_v3.py`, `scripts/analyze_v3.py` and
  `test_experiment_v3.py` so that the repository's mypy gate passes, and (b) merged unrelated
  changes to `agent_mail.py` (one timestamp parser on every Python, #51; `doctor` wording, #50).
  Because the harness's script hash therefore differs from the one in the readiness gates of smokes
  1 to 4, readiness was repeated once with the frozen files (smoke 5: 2 instances, 38 turns,
  all 13 checks true, about USD 0.12; total readiness spend USD 0.432 of the USD 1.00 cap). The
  ceiling observation in the readiness report is unchanged and is not part of the gate.

Main stage command:
`POSTBOX_EXPERIMENT_LIVE=1 python3 scripts/experiment_v3.py --stage main --live --out <dir outside
the repo> --gate <readiness_gate.json> --ceiling-usd 15 --prior-spend-usd 0.432`. It stops early,
as section 8 says, if N2 is at or above 95% on the first 20 instances.
