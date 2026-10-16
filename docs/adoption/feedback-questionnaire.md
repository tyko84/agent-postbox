# Feedback questionnaire for a tester

Not sent to anyone. Sending is the owner's decision ([invite-five.md](invite-five.md)). Use it as
the body of a reply, or let the tester fill in the `Tried it` issue form, which asks questions 1 to
6. Questions 7 to 10 are the part the form does not carry; paste them under the form's answers.

Each question says what it measures and where the answer goes, so the answers can be added up
later without guessing ([beta-measurements.md](beta-measurements.md)). Any question may be
skipped. Please do not paste real mailbox contents; command output with ids is fine.

```text
Thanks for trying agent-postbox. Ten questions, answer any of them. A reply of "I got stuck at X"
is the most useful one.

Setup
1. Host(s), OS and Python? (Claude Code / Codex / other; macOS / Linux)
2. How far did you get? (cloned / ran the example / installed / sent a first message / a second
   session picked it up / used for real work / removed)
3. Minutes from `git clone` to your first message, by your own clock? Where did the time go?

Pickup and permissions
4. Did the other agent show the mail without being told to look? (yes / no / only when told)
   If you can, say how you know (it replied by itself; you saw the block; you checked a transcript).
5. Did a permission prompt or denial hit a mail command? How many, which command, what did you
   choose, what did the agent do next? Paste the `send_permission:` lines of `doctor`.
6. Where did you get stuck or confused? The exact command and its output, if you have it.

Claims, expiry and history (the part the tool says it is for)
7. Did you use a CLAIM? If so: was a rival claim on the same scope refused (exit 3)? Did a lease
   run out and get taken over? Did anything behave differently from what the README says?
8. Did an agent ignore a claim or an ASK addressed to it? What did you do?
9. Afterwards, could you tell from the mailbox alone who held what and who asked or answered
   what? Run `status` and `list`; was anything missing that you wanted?

Effort
10. Count, roughly: times you copied a message between sessions yourself; times you edited,
    moved or deleted a mailbox file, re-ran the installer or added an allow rule; times you told an
    agent to check its mail. And: would you use it on parallel agents next week? Why or why not?

Please say so if you would not use it. My own experiments did not show an advantage over a notes
file; I am asking about installation, pickup, permissions and whether the claim rules held.
```

## How the answers are used

| Question | Measure | Recorded as |
|---|---|---|
| 1, 2 | environment, furthest step | trial-log.md "Host, OS", "Furthest step" |
| 3 | time to first message | "Minutes to first message" (human clock) |
| 4 | delivery reliability | "Pickup reached the agent?" |
| 5 | permission friction | "Permission friction (count, cause)" |
| 6 | stuck point | "Stuck point (exact step)"; one issue per distinct problem |
| 7, 8, 9 | coordination correctness | "Claims, refusals, takeovers" |
| 10 | manual interventions; would use again | "Interventions", "Would use again?" |

A reply that reports a message that was lost, corrupted or hidden is a field report, not a
questionnaire: open it with the field-report form and treat it first
([feedback-process.md](feedback-process.md), triage order 1).
