# Experiment v2: results (main stage, 2026-10-14)

**Verdict, as pre-registered in [experiment-v2.md](experiment-v2.md) section 7: against
Postbox on this task.** Reported first, as section 14 requires.

| | |
|---|---|
| Design | 60 paired instances, 5 arms (P, N2, N1, N0, O), 849 sessions, `claude -p`, haiku, effort medium |
| Code | merged `main` at `cc68306`; `scripts/analyze_v2.py` SHA-256 `f73eaa20…9ac0c` (matches the freeze table) |
| Cost | $4.10 total (ceiling $30; projection from the smoke mean was about $7) |
| Validity gates | all 5 pass (O 100%, N0 0%, no mailbox mention outside P, paragraph lengths P 317 / N2 308, 0 infrastructure failures) |
| Raw data | kept outside the repository, as for the pilot |

## Primary contrast, P versus N2, `b_ok`

| Arm | `b_ok` | Wilson 95% |
|---|---|---|
| P (Postbox) | 51 / 60 (85%) | 0.74 to 0.92 |
| N2 (notes file, not told to read it) | 60 / 60 | 0.94 to 1.00 |
| N1 (notes file, told to read it) | 60 / 60 | 0.94 to 1.00 |
| N0 (no channel) | 0 / 60 | 0.00 to 0.06 |
| O (oracle) | 60 / 60 | 0.94 to 1.00 |

P minus N2 = **-15 points**, 95% interval -26.1 to -5.8, exact McNemar p = 0.0039
(P-only 0, N2-only 9). Same numbers for P versus N1. P versus N0 is +85 points: the task
does need a channel, and Postbox is a working one.

## Why P lost: what the artifacts show

All 9 P failures have the same cause. Agent A finished its ticket and tried to send the
ANSWER, and the host's permission layer **denied the `agent_mail.py send` command**
(`--permission-mode dontAsk` with an allow rule on the command prefix). Agent A reported
the denial and stopped; agent B then correctly reported itself blocked. In 8 of the 9 the
denied command used an absolute, quoted path; in 1 it was the documented relative form.
In the 51 successes the same command ran. The N arms use the Edit tool on a file, which
the harness allows without a prefix rule, so they were never exposed to this. P runs also
averaged 0.55 denials per run against 0.03 for N2.

So the measured gap is real for this harness, and it is a gap in **whether a shell command
survives the host's allow-rule matching**, not in whether the mailbox protocol delivers.
Every message that was actually sent was delivered and picked up by the hook
(51 of 51). This is a reading of the artifacts, not a pre-registered finding, and it does
not change the verdict. It does change what to do next.

## Secondary, descriptive (unadjusted; 4 contrasts stated)

- Cost per completed task: P $0.0211, N2 $0.0138, N1 $0.0143 (P about 1.47 times N1,
  just under the 1.5 threshold in section 7; the rule's cost clause is therefore not
  triggered, while its exact-test clause is).
- Wall-clock per run: P 83.9 s, N2 69.4 s, N1 71.2 s.
- P acknowledgement: 47 of 51 completed runs.
- N1 and N2 did not differ, so telling agents to read the notes file added nothing here.

## What this does and does not show

- On this task, with this model and harness, Postbox did worse than a notes file the
  agents could edit directly. It is not evidence that Postbox helps, and the earlier
  micro-experiment's null result stands.
- It is not evidence about other models, hosts, tasks, or interactive use. Haiku under
  `dontAsk` is the least forgiving setting for a tool that needs an approved shell
  command; a user who has approved the command once would not see this.
- The project will not re-run until the result is favourable (section 14). Any follow-up
  that changes the permission setup is a new, separately pre-registered experiment and
  would be reported as such, next to this one.

## Consequences for the project

1. The README and docs must not claim a measured benefit. Where they cite experiments,
   they should say: the micro-experiment was null and v2 was against Postbox under a
   restrictive permission mode, with the cause above.
2. Examine the adapter design, as section 14 requires: Postbox's write path is a shell
   command, so on hosts that gate shell commands by prefix it needs an allow rule written
   for it. `docs/hosts.md` and `docs/troubleshooting.md` should say so, with the exact
   rule and the symptom ("the agent says the mail command was denied").
3. A cheaper question for a later pre-registered run, if wanted: P with the allow rule
   written to match absolute and quoted forms. Not run, not planned until decided.
