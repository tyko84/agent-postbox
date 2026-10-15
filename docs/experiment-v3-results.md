# Experiment v3: results (stopped at the pre-registered ceiling gate, 2026-10-15)

**Result: uninformative.** The notes-file control completed 20 of 20, so by the gate fixed in
[experiment-v3.md](experiment-v3.md) section 8 the run stopped after 20 instances and the
contrast is declared uninformative, **not a tie**: a control at the ceiling leaves no room for
Postbox to show an advantage. Nothing here shows that Postbox helps, and nothing shows that it
does not.

| | |
|---|---|
| Design | multi-prompt consumer (B works through 4 prompts in one live session; the fact arrives after prompt 1), arms P, N2, N0, O; 20 instances started, 80 runs, 380 sessions |
| Code | `main` at `bf612be`; harness and analysis hashes as in the freeze table; analysis by the frozen `scripts/analyze_v3.py` |
| Host / model | Claude Code 2.1.300, haiku (`claude-haiku-5-5`), effort medium, `--permission-mode dontAsk` |
| Spend | $1.05 for this stage (sum of the 80 run records); $1.48 including the $0.43 readiness spend, which is what the harness's ceiling counter shows (cap $15; readiness cap $1) |
| Raw data | outside the repository: `~/Desktop/Agent-Postbox/experiment-data/v3-main-2026-10-15/` (all runs, raw event streams, spend log) |
| Validity gates | 1 to 5 pass (O 100%, N0 0%, no mailbox mention outside P, paragraphs 344 vs 329 characters, 0 infrastructure failures); **gate 6 (N2 below 95%) fails: N2 = 100%** |

## Numbers (descriptive; 20 paired instances)

| Arm | `b_ok` | cost (20 runs) | seconds per run | notes |
|---|---|---|---|---|
| O (fact in B's first prompt) | 20 / 20 | $0.127 | 33 | ceiling check passes |
| N0 (no channel) | 0 / 20 | $0.249 | 62 | floor check passes: the task needs a channel |
| N2 (notes file, not told to read it) | **20 / 20** | $0.275 | 65 | ceiling |
| P (Postbox) | 20 / 20 | $0.400 | 78 | message delivered by the hook in 20 / 20; 7 / 20 acknowledged; 2 host refusals of a channel command, neither fatal |

P minus N2 = 0 points (95% interval -16.1 to +16.1; exact McNemar p = 1.0; no discordant pair).
In every arm that had the fact, B used it correctly from prompt 2 on and not at prompt 1
(`b_ok_by_prompt` = false, true, true, true in all 40 runs of P and N2).

Descriptively and without a test: P cost about 45% more per run than N2 and took about 12
seconds longer, for the same outcome. That is two small numbers from 20 instances on one task
and says nothing about a general cost of Postbox.

## What this does and does not show

* **The notes file was not "left unread".** In v2 and here, haiku under Claude Code found and
  used a shared notes file without being told to. In N2, B's first look at the notes is recorded
  at prompt 2 in 7 runs, prompt 3 in 1, prompt 4 in 5, and **never recorded in 7 runs in which B
  still used the fact**. One possible mechanism for those seven is a host notification that a
  file the agent had touched changed; that is a hypothesis from the readiness report and was
  not verified. If true, then for Claude Code an edited notes file is partly self-delivering,
  which would explain the ceiling.
* So the premise behind Postbox (an agent that is not looking will not see a message) was
  **not borne out for this host, model and task**: the control looked often enough to complete
  every time. It is a result about haiku in Claude Code on a short two-ticket task, not about
  longer sessions, bigger teams, other models, or hosts without such notifications.
* Pickup worked: the hook surfaced the message at prompt 2 in 20 / 20 P runs. Postbox did what
  it is built to do; the control simply did not need it here.
* Together with v2 (against) and the micro-experiment (null), **no measured advantage of
  Postbox over a shared notes file exists**, in three experiments with one small model. The
  honest reading is that on short, two-agent tasks in Claude Code a notes file is enough, and
  the case for Postbox rests on things these experiments did not test (hosts without push-style
  notification, many agents, longer sessions, structured claims and answers, auditability).

## Deviations and caveats

* The readiness gate was regenerated once with the frozen files after annotation-only edits
  (see the freeze table). Smokes 1 to 4 and the readiness probes are not part of these results.
* The stop at 20 instances is the pre-registered early stop (a look at the control arm only),
  not an interim look at P against N2.
* 20 pairs cannot detect differences below roughly 30 points; the interval above says so.
* Two host refusals of a channel command occurred in P (counted, neither changed an outcome).

## Decision

Do not extend this design to 60 instances, change the task to make the notes file fail, or
re-run it: that would be tuning an experiment until the product wins, which section 14 of the v2
pre-registration rules out for v2 and which would be no more honest here. Any further experiment
should ask a different question, for example whether the mechanism that made the notes file
self-delivering exists in other hosts (Codex has not been run), and should be proposed and
approved separately. Spending stops here: $1.48 of the $15 authorised.
