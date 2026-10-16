# Experiment v4: a coordination-specific experiment (design, not approved to run beyond the pilot)

Status: **a design for review.** Nothing is built or run. It follows
[product-direction.md](product-direction.md): the first three experiments measured delivery and
hit a ceiling; this one measures what the protocol claims to add (exclusive claims, expiry and
recovery, an inspectable history). It is written so that Postbox can lose.

## 1. The question

When two or more agents work concurrently in one repository on overlapping work, does a
Postbox claim ledger change (a) how often they overwrite or duplicate each other's work, (b) how
well work resumes after one holder dies mid-task, and (c) whether an outsider can reconstruct
afterwards who held what and when, **compared with credible alternatives**?

It is not a question about whether a message arrives.

## 2. Baselines (credible, not crippled)

| Arm | Coordination method | What the agents are told |
|---|---|---|
| **N0** | none | the shared line only (floor: shows the task has real contention) |
| **S** soft notes | a shared `NOTES.md`; to take work, add a line `CLAIMED <scope> by <me> at <time>`; check it first | how to write and check |
| **L** atomic lock | `mkdir locks/<scope>` (atomic on POSIX; failure means someone holds it); remove the directory when done; the instruction names the command | exactly that; no expiry (this is the standard minimal lock, and the baseline Postbox must beat to be worth its weight) |
| **P** Postbox | `agent_mail.py send --type CLAIM --scope <scope> --expires <short lease>`; exit 3 means someone holds it; release by superseding | exactly that, plus the doctor-printed allow rules; heredoc form for bodies |
| **O** sequential oracle | the same two tickets run one after the other by the harness | ceiling: the tickets are solvable without contention |

Prompt length, tool allowance and permission mode are equal across arms; the P arm's allow rules
are verified before any measured run (lesson of v2).

## 3. Scenarios (the unit of an instance is one scenario run once per arm)

1. **Contested scope (shared ownership).** Two agents, launched at the same moment as separate
   concurrent `claude -p` processes in **one shared working tree**, each handed a ticket that
   requires editing the same function in the same file (e.g. add different behaviours to
   `ledger/rates.py: quote()`), each told to work for several steps (read, plan, edit, test).
   Contention is real only if both read before either writes, so the task needs a multi-step
   read-modify-write; the pilot checks that N0 actually collides.
2. **Interrupted holder (recovery).** As (1), but the harness kills agent A (SIGKILL) shortly
   after it has taken its claim/lock/note, and agent B, told the work must be finished, is given
   a bounded patience (it may wait and retry for a fixed number of minutes). Postbox lease: a
   short fixed expiry (e.g. 90 s). L: the lock is stuck; S: a stale note. The test is whether B
   finishes correctly and how long it took, not whether a protocol feature exists.
3. **Audit reconstruction.** After scenarios 1 and 2, give a blinded auditor (a fresh `claude -p`
   session, or a script where the arm's artifacts are machine-readable) **only the arm's
   artifacts** and fixed questions: who held scope X and when, in what order did the two edits
   land, who was interrupted, who took over. Scored against the harness's ground-truth log.

## 4. Outcomes (computed from artifacts, never from what a model says)

* **Lost update**: a change one agent made is absent from the final tree (diffed against the
  harness's per-agent edit log). Primary for scenario 1.
* **Duplicate work**: both agents implemented the same requirement.
* **Final correctness**: hidden test suite on the merged result passes (secondary; this is the
  ceiling outcome of v2/v3, kept for context).
* **Recovered**: in scenario 2, the interrupted ticket's behaviour is present and the suite passes
  within the patience window; **time to recovery**.
* **Manual interventions**: harness nudges (target zero; any nudge is logged).
* **Audit score**: fraction of ground-truth questions answered correctly from artifacts alone.
* **Cost/time/turns** per run (notional subscription accounting; reported, not a limit).

## 5. Validity gates (decided before any run)

1. N0 shows contention in scenario 1: at least 30% of N0 instances have a lost update or
   duplicate work. If not, the scenario cannot distinguish arms; the experiment is uninformative.
2. O reaches full correctness in at least 80% (the tickets are solvable).
3. Each of S, L, P is operable: at least one instance completes end to end in readiness, and no
   arm's channel command is host-refused in readiness.
4. Prompts equal in length within 10%; same tools and permissions.
5. Infrastructure failures repeated once, both listed; at least 90% of sessions usable.
6. In scenario 2, the kill is verified to have happened after the claim/lock/note existed
   (otherwise the instance is void and listed).

## 6. Hypotheses, stated so that they can fail

* H1 (contention): P and L have fewer lost updates and duplicates than S and N0. **Expected:
  P is about equal to L here**, because an atomic lock also prevents the race. A tie with L is a
  legitimate result and means Postbox's value is not in plain exclusion.
* H2 (recovery): P recovers more often and faster than L and S, because the lease expires.
  If agents in the L/S arms simply reason their way past a stale lock, H2 fails and the
  expiry mechanism has no measured value for capable agents.
* H3 (audit): P's reconstruction score is higher than L's and S's.
Any result, including "no difference to L", is reported with equal prominence.

## 7. Pilot (the only part authorised to run: existing subscription usage, no API billing)

Contested-scope and interrupted-holder, **6 instances x 5 arms** (N0, S, L, P, O), haiku, effort
medium, `--permission-mode dontAsk`, concurrent launch via a harness with a hard cap on sessions
(about 120 sessions) and wall time. The pilot cannot establish significance; its job is to check
gates 1 to 3 and 6, that the arms are operable and symmetric, and what the recovery behaviour of
S and L looks like in practice. A pilot that shows N0 does not collide ends the project of this
experiment with a clear explanation. Raw data stays outside the repository; failed runs and
refusals are kept.

## 8. Main stage (not authorised; only after the pilot is reviewed)

Sample size would be chosen from the pilot's observed rates, with the decision rule copied from
v2 (net advantage over the **strongest** baseline, exact paired test, interval excludes zero),
a ceiling gate on the best baseline, a spending note in notional units, and a freeze table.

## 9. What would make this misleading

* A prompt that explains Postbox's semantics better than the baselines' (hence the equal-length
  and equal-detail rule, and publishing the prompts).
* Agents that coordinate by luck of timing in a short task (hence gate 1).
* Counting a host refusal against one arm (hence readiness verification and separate refusal
  reporting).
* Choosing the lease so short that recovery is trivial, or so long that it cannot show
  (hence a single pre-registered lease, with the patience window longer than the lease).
* One model, one host, short tasks: no result transfers beyond them.
