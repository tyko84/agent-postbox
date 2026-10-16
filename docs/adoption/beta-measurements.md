# Beta measurements: what is measured, by whom, with what command

Status: a definition, written before any tester has tried anything. **No beta has been run and no
number in this file comes from one.** The two numbers that do appear are machine time from one
run on the author's laptop, labelled as such. Anything a tester or an agent will measure later is
a `[PLACEHOLDER]` here and a row in [trial-log.md](trial-log.md).

Why these five: the trial ([invite-five.md](invite-five.md)) asks whether it installs, whether
pickup reaches the agent, where permissions get in the way, and, since the direction note
([../product-direction.md](../product-direction.md)), whether the claim and expiry rules hold up in
someone else's use. It does **not** ask whether Postbox makes agents better; five testers cannot
answer that, and no experiment has.

Conventions. **Tester** means the person trying it. **Operator** means whoever runs the checks for
the owner (an agent or the owner), only on a mailbox the tester has agreed to share or on pasted
command output, never on real mailbox contents. Every number is reported with its denominator
("3 of 5"), never as a percentage of five. `agent-postbox` below is the pip-installed command; in an
installed project use `python3 tools/agent-postbox/agent_mail.py`.

## 1. Permission friction

| | |
|---|---|
| Question | How often did the host stop or ask about a mail command, and what resolved it? |
| Unit | count of prompts or denials per tester session, with the command and the resolution (allowed once, allow rule added, abandoned) |
| Measured by | the tester, from what the host showed. Headless runs can also be counted from the host's own result (`permission_denials` in `claude -p --output-format json`; `scripts/e2e_claude.py` and `live_demo.py rehearse` already read it) |
| Command | `agent-postbox doctor` and copy its `send_permission:` block, once at the start and once at the end. It lists which `permissions.allow` rules it can see and whether the spelling Postbox prints has one |
| Recorded in | trial-log.md column "Permission friction (count, cause)" |
| Known cause to check first | a `#` line inside a quoted `--body`: refused by Claude Code even with an allow rule (11 of 11 in the probe, `docs/data/permission-probe/`); the fix is `--body-file -` |
| Not measured | friction in interactive Claude Code: not observed by anyone yet ([../demo-script.md](../demo-script.md)) |

Reading it: a count of zero with `send_permission` saying "no rule allows `send`" means the tester
approved by hand, not that no prompt appeared; ask which.

## 2. Time to first message

| | |
|---|---|
| Question | How long from "I have the clone" to "a message exists", on a human clock? |
| Unit | minutes, and the step the time went into (reading, install, naming agents, first `send`) |
| Measured by | the tester, with their own clock, started when they run `git clone`; the Tried it form asks for it |
| Cross-check command | the first message's own stamp: `agent-postbox list --json \| python3 -c 'import json,sys; m=json.load(sys.stdin)["messages"]; print(min(x["date"] for x in m))'`. Subtract the clone time the tester noted. On macOS `stat -f %SB agent-postbox/.git` prints the clone's birth time; it is a cross-check, not a replacement |
| Recorded in | trial-log.md column "Minutes to first message" |
| Machine-time baseline (not human time) | the scripted example, `python3 examples/two-agents/run.py`: 0.41 s; `--takeover`: 4.14 s; `selftest.py`: 19.5 s (one run, macOS, Python 3.13.7, 2026-10-16) |
| First-exchange machine timing | `[PLACEHOLDER: Agent C's measured first-exchange timing, machine time, with the command and the date; fill in from that report, do not estimate]` |

The README and release notes must not say "five minutes" or any figure as a human time until
testers have reported one. A machine time is a lower bound on nothing a person experiences.

## 3. Delivery reliability

| | |
|---|---|
| Question | When a message was addressed to a session, did that session see it without being told to look? |
| Unit | per message: seen without prompting / seen only after the tester pointed at it / never seen; reported as x of N messages per host |
| Measured by | the tester answers yes, no or only-when-told (Tried it form); the operator confirms from the mailbox |
| Commands | `agent-postbox doctor` (the `canary:` line; a canary `OK` shows the pickup path was exercised within 24 hours, not that every message arrived); `agent-postbox list --to <recipient> --json` for the messages and `status --json` for `asks.open`; `agent-postbox latency` for the time from each answered ASK to its first ANSWER, from file dates only |
| Hook evidence (Claude Code) | the host's transcript holds the injected `Agent mail - ` block when the hook ran: `grep -l 'Agent mail - ' ~/.claude/projects/*/*.jsonl` (read what it lists first; it holds the tester's prompts). `live_demo.py check` beat 4 reads transcripts the same way. **Not yet tested against a real interactive transcript** |
| Recorded in | trial-log.md column "Pickup reached the agent?" |
| Limit | an `ANSWER` in the mailbox shows the recipient replied, not how it learned of the ASK (hook, poll, or the tester). Without the transcript or the tester's word the cause is unknown, so report that case separately |

## 4. Coordination correctness

Only the part that files can show is measured. Whether the work turned out better is not.

| Check | Command | Expected if the rules hold | Measured by |
|---|---|---|---|
| No scope held by two senders | `agent-postbox status --json` and read `contested_scopes` and `problems` | `contested_scopes` is 0 and `CONTESTED_SCOPE` is absent. `send` cannot create it; only a hand-written file can | operator |
| A rival claim was refused | the tester reports exit 3 and the line `scope '<x>' is already held by <holder>` | exit 3, nothing written | tester (a refused claim leaves no record, so only the tester can say it happened) |
| A lapsed lease stopped counting | `agent-postbox list --type CLAIM --status expired` and `status` after the expiry time | the claim is `expired`, not in `held claims` | operator |
| A second claim after expiry succeeded | `agent-postbox status --scope <x>` | one held row, naming the new holder | operator |
| Owed replies closed | `agent-postbox status --json` read `asks.open`; `agent-postbox inbox --to <recipient>` | open ASKs are exactly the ones nobody answered | operator |
| Silent failures | `status --json` `problems`: `SILENT_RECIPIENT`, `ALIAS_MAIL_UNDELIVERED`, `ANSWER_FROM_THIRD_PARTY`, `DANGLING_REPLY`... | each present code is explained or filed as an issue | operator |
| Rebuild the history from the mailbox | `python3 docs/data/audit-reconstruction/reconstruct.py --tool <agent_mail.py> --mailbox <dir>` | claim intervals match the tester's account; the two gaps it prints are known | operator |
| Lost updates in the project's files | the tester's judgement, from `git log` and the diff of the claimed files | tester states whether two agents edited the same file at once | tester |

Record per tester: scopes claimed, rivals refused (tester count), claims that lapsed, takeovers,
open ASKs left at the end. Claims are advisory, so "an agent ignored a claim" is also a finding:
the tester notes it; the tool cannot see it. No threshold is set here; it is a table of counts.

## 5. Manual interventions

| | |
|---|---|
| Question | How many times did a person have to do what the tool is meant to spare them, or repair the tool's state? |
| Counted | (a) relays: copying a message between sessions by hand; (b) repairs: editing, moving or deleting a mailbox file; re-running `install.py`; adding an allow rule; restarting a session so the hook runs; (c) nudges: telling an agent to check its mail |
| Measured by | the tester keeps a tally (three counters) while working and reports it at the end |
| Command | have the tester file anything they write by hand under the identity `human` (`send --from human ...`), then `agent-postbox list --from human --json \| python3 -c 'import json,sys; print(json.load(sys.stdin)["matched"])'` counts those messages. Repairs and relays leave no record; for them the tally is the only source. `git status` on the mailbox directory shows edited files only if the project commits them |
| Recorded in | trial-log.md column "Needed owner help?" and the intervention counts |

Reading it: compare the count with what the tester said they did before using Postbox, if they
will say; without a baseline the count describes the beta, it does not show a saving.

## What goes where

| Measure | Form field or source | Trial-log column |
|---|---|---|
| Permission friction | Tried it form, "Permission prompts or denials" + `doctor` | Permission friction |
| Time to first message | Tried it form, "Minutes from clone" | Minutes to first message |
| Delivery reliability | Tried it form, pickup dropdown + operator commands | Pickup reached the agent? |
| Coordination correctness | questionnaire questions 7 to 9 + operator table | Claims, refusals, takeovers |
| Manual interventions | questionnaire question 10 | Interventions (relays/repairs/nudges) |

The questions to ask are in [feedback-questionnaire.md](feedback-questionnaire.md).

## What this file does not authorise

Contacting anyone, posting anywhere, or running a beta. Those are the owner's decisions
([invite-five.md](invite-five.md)). It also sets no pass mark beyond the success and failure
criteria already fixed in invite-five.md.
