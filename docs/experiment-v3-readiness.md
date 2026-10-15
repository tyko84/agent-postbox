# Experiment v3: readiness report (2026-10-15)

**RECOMMENDATION: do not run the main stage as designed.** In every live instance the control
(N2, a notes file nobody is told to read) reached `b_ok`: 6 of 6. By the pre-registered ceiling
gate ([experiment-v3.md](experiment-v3.md) section 8, gate 6) a main run would very probably end
"uninformative", and would spend about USD 3.5 to learn that. The multi-prompt design does not
remove the ceiling. If the owner still wants the data, the harness is ready and every other check
passes (section 3); the cheaper step is to redesign N2 first (section 5).

This is a readiness check on 4 live instances, not a result. 1 to 6 instances cannot estimate a
rate; the 6 of 6 is a reason to doubt the design, not a measurement of N2.

## 1. Setup and exact commands

* Claude Code **2.1.300** (`claude` on PATH), model `claude-haiku-5-5`
  (`haiku`), effort medium, `--permission-mode dontAsk`; Python 3.13.7; PATH with
  `/opt/homebrew/bin` first. v2's published probe used 2.1.296.
* Raw data (outside the repository): `~/Desktop/Agent-Postbox/experiment-data/v3-readiness-2026-10-15/`
  (`probe`, `probe2`, `probe3`, `smoke1` to `smoke4`, each with `raw/` event streams, `runs/`
  records, `manifest.json`, `spend.json`).
* Offline: `python3 -m unittest test_experiment_v3` (46 tests, simulated agents) and
  `python3 scripts/experiment_v3.py --stage readiness --dry-run --out <dir> --instances 2`
  (the whole pipeline with the fake driver; the gate it writes is marked `dry_run` and the main
  stage refuses it).
* Probe: `POSTBOX_EXPERIMENT_LIVE=1 python3 scripts/experiment_v3.py --stage readiness --probe
  --live --out <dir> --ceiling-usd 1.0 --prior-spend-usd <spent so far>`
* Smoke: `POSTBOX_EXPERIMENT_LIVE=1 python3 scripts/experiment_v3.py --stage readiness --live
  --instances 2 --workers 2 [--skip-instances K] --out <dir> --ceiling-usd 1.0 --prior-spend-usd
  <spent so far>`

## 2. Cost spent (cap USD 1.00)

About **USD 0.31** in total by the harness's own count (a conservative figure: the first smoke
counted the host's running totals twice before the correction in section 3 existed). Probes:
USD 0.004 + 0.005 + 0.009; smoke 1 about 0.08; smoke 2 0.11; smoke 3 and 4 about 0.05 each.
Stopped well under the cap; nothing else was spent.

## 3. Checks

| # | Check | Result |
|---|---|---|
| a | Offline dry run of the whole pipeline, 46 offline tests | **PASS** |
| b | P's allow rules let its taught send forms through under `dontAsk` (probe, one run per case) | **PASS** for the scored cases, with one finding below |
| c | Each arm completes an instance end to end live | **PASS** (P, N2, N0, O; 4 instances, final prompts on 2) |
| d | Symmetry numbers; no non-P run mentions the mailbox | **PASS** |
| e | Is the ceiling plausibly avoidable? | **FAIL, see section 5** |

**(b) in detail** (`probe.json` in `probe2`, `probe3`; the rules are the harness's own; each case
checks denials, that a message was written, and that the body arrived intact):
relative heredoc with `#` lines, absolute-quoted heredoc with `#` lines, plain heredoc: all allowed,
bodies intact. Negative control (heredoc followed by `&& python3 -c 1`): refused, so the probe can
see a refusal. Extra, unscored cases: heredoc bodies with backticks, a blank line plus a dash
list, and `$5 (approx) $(not run)` were allowed; **a heredoc body containing `{'count': live,
'total': x}` was refused by the host although the rule matches** (denied=1, nothing written). It
surfaced in a smoke run: agent-b's courtesy NOTICE with such a body was refused (1 channel denial
in the P arm, smoke 2). Both arm paragraphs therefore now say "without curly braces" (lengths 344
and 329, within 10%); the later smokes (3 and 4) had 0 channel denials. The inline multi-line
`--body` with a `#` line was **not** host-refused on 2.1.300 (the tool itself now rejects a
multi-line `--body`), so v2's `#` refusal could not be reproduced on this version; it is
informational only. Open: the brace refusal is observed on one body; other characters may exist.

**(c) live tally** (final plus earlier prompt versions; usable runs only): O 6/6, N0 0/5, P 5/5,
N2 6/6. Four run records (two runs, each repeated once) hit infrastructure timeouts (sessions that
emitted `init` and hung 240 s) during a stretch matching the network outage, and were repeated once per the rule; both
attempts failed for two of them (P on 9101, N0 on 9102), and those instances were completed by
other smokes. One smoke instance (9102, O) was aborted by the spend guard because its worst-case
estimate before any measurement (USD 0.30 per turn, from v2) times two workers exceeded the
cap; the guard worked, the estimate was wrong for this design and is now a per-turn 0.05
(`FIRST_TURN_ESTIMATE`). Cost accounting: the host's `total_cost_usd` is a **running total per
live process**; the harness now bills the difference (`COST_CUMULATIVE`), recorded with the
reported value. Per instance: about USD 0.05 (0.04 to 0.06); projected main
60 x 0.05 x 1.1 = USD 3.5 (margin 1.5: 5.3), under the USD 15 hard stop.

**(d) symmetry** (numbers from `symmetry()` and the records): tools `Bash,Read,Edit,Write` in all
four arms; 17 common allow rules in all, plus 4 mail-command rules in P only; `--add-dir shared`
in N2 only; B prompt 1 lengths 949 (P), 934 (N2), 604 (N0), 605 (O) characters; paragraphs 344 vs
329; prompts 2 to 4 identical (35, 72, 34 characters) and free of channel words (tested). Denied
commands across the usable live runs: P 1 (the brace NOTICE), N0 1 (an unrelated `find` on a
checkout path), N2 0, O 0. Mailbox mention in non-P runs: 0 of 17. Cross-checkout attempts: 0.

## 4. Gate output

The final smokes (3 and 4, current prompts hash
`a86caa21ffdd274738a4f67075aa1d8256db4fdfaca9bf04e6255325b7b7ec06`) wrote `readiness_gate.json`
with `passed=true` (all 14 checks); smoke 2, before the brace wording, failed exactly one check
(channel call refused), which is what the check is for. A passing readiness gate does not mean the
design is informative; it checks plumbing.

## 5. Check (e): the ceiling

* N2 reached `b_ok` in 6 of 6. In the run I inspected B opened `NOTES.md` itself in prompt 1
  (to say what it needed); in 5 of the 6 it read it by prompt 2 to 4 (first look at prompt 2 in two runs, prompt 4 in
  three). So an unprompted notes file is looked at within four prompts. The multi-prompt design
  delayed the look (in v2 it came at once) but did not stop it, and by prompt 4 it had happened.
* In one run (smoke 4, 9102) B used the answer **without any Read or shell read of the notes in
  prompts 2 to 4** (it wrote the right `D` and cited "agent-a's reply in the shared notes"). The
  most plausible cause is that the host reports to the model that a file it had already read or
  edited changed on disk, which would make a once-touched notes file a push channel. **This is
  not verified**; I did not inspect the model context. If true, the N2 arm is at the ceiling by
  construction whenever B touches the file in prompt 1, which the paragraph and the README line
  invite. (The harness's `picked_up` for N2 also does not count an Edit/Write of the file; it
  would report "never" in such a run.)
* One to six instances cannot tell whether N2 would be 90% or 100%. They do not show it can be
  below 95%.

**What would make v3 informative** (not built; each needs the owner's decision and a new
pre-registration): make the fact arrive in a situation where B has not already opened the notes
file (for example B's prompt 1 not leading it to the file, or A's answer being a different fact
from the one B asked about), or add a distractor workload that makes looking costly. The edits
should be justified by the question, not tuned until N2 drops.

## 6. Projected main-stage cost and decision

Projected cost USD 3.5 expected, 5.3 with margin; hard stop 15. Cost is not the objection. The
objection is that the likeliest outcome is the "uninformative" row, and that the harness's early
stop (N2 at or above 95% on the first 20 instances) would end the run after about USD 1.2.
If the integrator wants the answer to "is the ceiling real" it is cheaper to run that first
block (20 instances, about USD 1.2) than to decide from 4 instances; that is a decision for the
integrator, within the owner's USD 15 approval, and not made here.

## 7. Not verified

Other brace-like host refusals; the file-change notification mechanism; N2's true rate; behaviour
at 60 instances or under 4 parallel workers (readiness used 2); that a different Claude Code
version keeps these results; whether the multi-prompt B process stays healthy across a long idle
gap while A runs (the timeouts coincided with an outage and were not diagnosed further).
