# Trial log (five-developer trial)

One row per person who tried it. Fill it in from their replies or `Tried it` / field-report
issues; do not put names, emails or real mailbox content here, only a label (T1 to T5).
Update the table in a pull request, not in chat, so the evidence stays with the code.

| ID | Type (see invite-five.md) | Host, OS | Furthest step | Minutes | Needed owner help? | Stuck point (exact step) | Removal clear? | Would use again? | Issue links |
|---|---|---|---|---|---|---|---|---|---|
| T1 | | | | | | | | | |
| T2 | | | | | | | | | |
| T3 | | | | | | | | | |
| T4 | | | | | | | | | |
| T5 | | | | | | | | | |

"Furthest step" uses the choices in the `Tried it` form: cloned, ran the example, installed,
sent a first message, a second session picked it up, used for real work, removed.

## Reading the table

Apply the success and failure criteria in [invite-five.md](invite-five.md) to the finished
table; do not change them afterwards. Then:

1. Count how many reached "a second session picked it up" without owner help, and the median
   minutes. Report both with the denominator (for example "3 of 5"), never a percentage of five.
2. Group stuck points by step. A step where two people stall is the next fix, before any
   feature.
3. Open one issue per distinct problem, linking the rows, labelled by the triage order in
   [feedback-process.md](feedback-process.md).
4. Write the outcome, including a null ("nobody running parallel agents saw a use"), in
   `docs/` and the changelog. A negative finding is published like a positive one.
