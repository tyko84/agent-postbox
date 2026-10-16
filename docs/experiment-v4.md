# Experiment v4 PILOT (pre-registration): do claims, expiry and recovery change what concurrent agents do?

Status: **a pilot design, written before any measured data.** Readiness
([experiment-v4-readiness.md](experiment-v4-readiness.md)) is not measured data. The pilot has
**not been run**; the readiness recommendation (fix and recheck) governs whether it is. This page
implements the pilot of [experiment-v4-design.md](experiment-v4-design.md) (scenarios 1 and 2
only; scenario 3, audit reconstruction, is out of the pilot, its artifacts are kept so it can be
done later). It changes nothing in the v2 or v3 pages or scripts. The design is frozen by a commit
that precedes the first measured session; later changes are dated amendments, never edits.

## 0. Amendment, dated 2026-10-15 (before any measured data): scenario 2 is primary

Readiness found that N0 did not collide in scenario 1 (0 of 2; Claude Code's own stale-file Edit and
Write check absorbs lost updates, and each agent finishes in about 30 s). The integrator therefore
re-scoped the pilot:

* **Scenario 2 (interrupted holder, recovery) is the PRIMARY pilot scenario**, arms N0, S, L, P, O,
  6 instances each (30 runs, 60 sessions). Lease **90 s**, patience **240 s** (longer than the lease),
  kill **1.5 s** after the claim/lock/note/first edit is seen (4 s was too slow: in readiness the
  holder had already finished its ticket in the L arm, so the instance was void).
* **Scenario 1 is kept only as a small OBSERVATION**: N0 on 3 instances (seeds 5001 to 5003) and S, L,
  P once (seed 5001): 6 runs, 12 sessions. Its result is reported as a finding ("does the host
  already prevent lost updates and duplicates?"), not as a validity failure. Planned sessions: 72;
  hard cap 80; wall cap 4 h.
* **Gate 1 below is retained as originally written for the record but no longer fails the pilot**:
  it is computed on the observation runs and printed as `observation_scenario_1`.
* Recovery is defined as: the interrupted holder's ticket behaviour is present in the final tree and
  the whole hidden suite passes within the patience window (the survivor is stopped at patience plus
  grace). Also recorded per scenario-2 run (`survivor`): how long it waited (pauses), whether it
  broke the lock (`rmdir`), edited over an unreleased note, claimed only after the lease, or ran
  anything unsafe (`rm -r`, `git reset/checkout/stash/clean`, `chmod`, `kill`, `mv`, `curl`...).
* Infrastructure failures now include "API failure": retries without any work, or an error result
  with no tool call (readiness saw this in 5 of 8 runs in one period; those were not repeated in
  readiness and are repeated once in the pilot).

## 1. Question

When two agents work at the same moment in one working tree on overlapping work, does a Postbox
claim (`--scope`, mandatory `--expires`, exit 3 on conflict) change (a) how often one overwrites or
duplicates the other (scenario 1) and (b) whether the work is finished after the holder is killed
(scenario 2), compared with no coordination (N0), a notes file (S), an atomic `mkdir` lock with no
expiry (L) and a sequential oracle (O)? A pilot cannot establish significance; it checks that the
experiment can distinguish arms at all and that every arm is operable and symmetric.

## 2. Arms and the exact prompts

Every agent's prompt is: intro + task line + waiting rule + (not O) shared line + (S, L, P) the
arm's paragraph; O gets the oracle line instead of the shared line and paragraph. Verbatim from
`scripts/experiment_v4.py` (`{me}`/`{other}` are agent-a/agent-b; the P expiry is a literal
instant computed by the harness at each agent's launch):

* **Intro (agent-a):** "You are agent-a on a two-agent team working on a ledger toolkit. Your working directory is a git checkout that you share with the other agent; TICKETS.md lists both tickets and you do Ticket A. Run tests with `python3 -m unittest discover tests`. Run each shell command as its own Bash call (no `cd`, `;`, `&&`, loops or pipes), use Read to read files, work only inside your working directory, and check your code with the unit tests, not `python3 -c`."
* **Task line (both scenarios):** "Do your ticket now; stop when you have done what you can in this session."
* **Waiting rule (every arm, O included; 5 minutes in scenario 1, 4 in scenario 2):** "Both tickets must be finished in the tree before you stop, whichever agent's they are: if your teammate does not finish theirs, finish it yourself. If you have to wait for your teammate, you may wait with `python3 tools/pause.py 30` (about half a minute each time) and check again, but wait no longer than {minutes} minutes in total; then finish what you can and stop."
* **Shared line (N0, S, L, P):** "The other agent is working in it at the same time, and both tickets change the same function in ledger/rates.py; avoid overwriting each other's work, using whatever the project provides."
* **Oracle line (O only):** "The other agent is not working at the same time: you have the working tree to yourself, and any work already in it is your teammate's, to keep."
* **S (566 characters):** "Team notes live in NOTES.md in your working directory, a file you both can edit. Before you edit ledger/rates.py, read NOTES.md; if it has a line starting `CLAIMED ledger/rates.py by agent-b` with no later `RELEASED ledger/rates.py by agent-b` line, agent-b is working on it and you should not edit it meanwhile. Otherwise add one line `CLAIMED ledger/rates.py by agent-a at <time>` with the Edit tool, keeping the earlier lines, and when you are done add one line `RELEASED ledger/rates.py by agent-a`. Keep each note to one line of plain text without curly braces."
* **L (549 characters):** "Locks are directories under locks/ in your working directory. Before you edit ledger/rates.py, take its lock with `mkdir locks/ledger-rates` as its own Bash call; if that fails because the directory exists, agent-b holds the lock and is working on the file, so you should not edit it meanwhile. When you are done, release the lock with `rmdir locks/ledger-rates`. A lock directory never expires by itself, and `ls locks` shows the locks that are held. Run each lock command on its own, without curly braces, and take the lock before your first edit."
* **P (596 characters):** "Team mail uses `python3 tools/agent-postbox/agent_mail.py`. Before you edit ledger/rates.py, claim it with `python3 tools/agent-postbox/agent_mail.py send --type CLAIM --from agent-a --to agent-b --scope ledger/rates.py --expires <EXPIRY> --subject 'editing rates' --body-file - <<'EOF'`, a line of text and `EOF` on its own line, in one Bash call; it prints an id, and exit status 3 means agent-b holds the scope until its lease expires. When you are done, send the same command again with `--supersedes <id>` and without `--scope` to release it. Write the text without curly braces."
* **N0 and O:** no paragraph (0 characters).

The three paragraphs are within 10% of each other (validity gate 4; computed by `lengths_within()`
and refused at start if false). N0 has no paragraph by design (the floor, as in v3). The P arm uses
the heredoc `--body-file -` form, no curly braces and no `#`-leading lines
(`docs/data/permission-probe/`). A SIMULATED survivor is not used here; the real survivor in
scenario 2 has the same prompt in every arm.

**Allow rules.** Same tools (`Bash,Read,Edit,Write`), `--permission-mode dontAsk`,
`--setting-sources project`, `--strict-mcp-config`, model haiku, effort medium. Common allow-list
(18 rules, identical in every arm): `Read`, `Edit`, `Write`, `Bash(python3 -m unittest *)`, `Bash(python3 -m unittest)`, `Bash(python3 tests/*)`, `Bash(ls *)`, `Bash(ls)`, `Bash(pwd)`, `Bash(cat *)`, `Bash(head *)`, `Bash(tail *)`, `Bash(grep *)`, `Bash(find *)`, `Bash(wc *)`, `Bash(sed -n *)`, `Bash(echo *)`, `Bash(python3 tools/pause.py *)`, `Bash(date)`. **L adds**
`Bash(mkdir locks/*)` and `Bash(rmdir locks/*)`. **P adds four** rules, one per spelling of the mail
command (relative; absolute plain, double-quoted, single-quoted). The rules are written into the
throwaway project's `.claude/settings.json` (merged with the adapter's hook for P) and also passed
on the command line. **Verified before any live run** by `verify_rules()`: the rules are in the
file and `doctor` reports both send spellings as allowed by an identical rule; readiness then
checks live that the taught forms are not refused.

## 3. Scenarios, instances, order

The project is a seeded `ledger/rates.py` (seed draws the fee currency, fee percentage and minimum,
the bulk threshold and discount percentage): `quote()` is a one-line function; **Ticket A** (agent-a)
adds a handling fee for one currency, **Ticket B** (agent-b) adds a bulk discount, discount first;
a **prerequisite** (`normalise_currency`) is needed by both and "whoever finds it missing adds it".
Both change the same function, and both tickets are needed in the final tree. Each agent puts its
own tests in its own file, so only `ledger/rates.py` is contested.

1. **Contested scope (S1; observation since the amendment).** Two separate concurrent `claude -p` processes in **one shared working
   tree**, launched at the same moment. O: A runs to completion, then B.
2. **Interrupted holder (S2; primary since the amendment).** Agent-a is launched; when its claim/lock/note is first seen (polling
   every 0.25 s), agent-b is launched and **1.5 s after that agent-a is SIGKILLed**.
   Postbox lease: a single fixed literal expiry of **90 s after agent-a's launch**
   (scenario 1: 600 s, for both agents). Agent-b's patience is **240 s** (told as "4 minutes"), longer than the lease; the
   harness stops it 60 s later. N0 has no claim, so its trigger is agent-a's first change
   to `ledger/rates.py` (and the "holder had already finished" void rule is not applied to it). O
   in S2 is the sequential oracle again (no kill).

Unit: one (scenario, arm, seed). Pilot seeds **5001 to 5006** (readiness used 9201 to 9206 and 9211
to 9218). Plan (amended): scenario 2: 6 instances x 5 arms x 2 agents = 60 sessions; scenario 1
observation: 6 runs x 2 agents = 12 sessions; **72 planned sessions**; seed-major, the arm order
within a seed a seeded shuffle; up to 3 runs in parallel. Not blinded; scoring is code.

## 4. Outcomes (artifacts only; never model text)

The harness polls the tree every 0.25 s while agents run and stores every distinct snapshot of
`ledger/` with its time; each agent's tool calls come from its raw event stream (with receive
times). **Per-agent edit log** = the applied Edit/Write calls to `ledger/rates.py`, replayed in order
of result, plus the snapshot transitions, each attributed to the agent whose Write content or
Edit `new_string` explains it. The hidden suite (`test_base`, `test_prereq`, `test_fee` = A,
`test_discount` = B, `test_combined`) is run on every state.

* **Lost update**: an agent's own test passed in some state (polled or replayed) and fails in the
  final tree. Also recorded: regressions caused by the other agent's transition.
* **Duplicate work**: both agents wrote `def normalise_currency(` into the file without having seen
  one in any earlier tool result of their own session.
* **Collision** (gate 1) = lost update or duplicate work.
* **Rejected edits** (the host refused a stale Edit/Write), descriptive.
* **Hidden-suite correctness** of the final tree (`final_ok`).
* **Recovered** (S2, non-O): `final_ok` at the end, B being stopped at its patience window plus
  grace; **time to recovery** = from the kill to the first state from which every test passes to
  the end.
* **Manual interventions**: harness nudges, target zero (there are none by construction); the
  designed SIGKILL is logged separately.
* **Cost, wall time, turns**: notional subscription accounting, reported, not a limit except the
  caps below (a killed session reports no cost; stated).
* Channel-command refusals by the host, per arm, with the denied text.

## 5. Validity gates (computed first by `scripts/analyze_v4.py`)

1. (Original text, superseded by section 0: no longer a pilot-failing gate; reported as an
   observation.) N0 collides in at least 30% of usable scenario-1 instances; if not, scenario 1 is
   declared **unable to distinguish arms**. 2. O reaches `final_ok` in
   at least 80%. 3. Each of S, L, P is operable: at least one run (any scenario) with both sessions
   completed, and no channel command refused by the host. 4. Paragraphs within 10%; same tools and
   permissions. 5. At least 90% of sessions free of infrastructure failure after one repeat.
6. In scenario 2 the kill is verified to follow the claim/lock/note (harness times and the
   artifact's modification time, holder alive at the kill, artifact still present); otherwise the
   instance is **void and listed** (also void: holder never claimed, or had finished its ticket).

## 6. Exclusions, caps, stop rules

No run is excluded after the fact. An infrastructure failure (rate limit, host crash, timeout in
scenario 1) is repeated once; both attempts are kept and listed; the last is the run. Void runs
are listed with their reason and are not repeated. **Hard caps**: 80 sessions
(72 planned plus repeats), 4 h wall time, USD 15 notional (not a bill: subscription use
only, no API key, no other service), two rate-limit rejections in a row stop the run; flags may
lower these, never raise them. Raw data (events, snapshots, mailbox, notes, locks, diffs, per-run
records) outside the repository; failed runs and refusals are kept.

## 7. What each result will be called

| Result | Called |
|---|---|
| Scenario-1 observation: N0 does not collide | "the host already prevented lost updates and duplicates in these runs" (a finding about the host; scenario 1 cannot distinguish arms) |
| Any of gates 2 to 6 fails | "failed its validity gates", with the reason; no claim |
| Gates pass; P and L have no more collisions than S and N0 | "consistent with H1" and **no more than that**: n = 6, no test |
| P and L tie | reported with equal prominence: the value is not in plain exclusion |
| Scenario 2 (primary): P recovers where L and S do not (or sooner) | "recovery differed in the pilot, descriptively (6 instances)" |
| L or S agents reason past the stale lock/note | reported as such: the expiry has no measured value for capable agents here |
| Anything else | "inconclusive" |

No significance claim, no interval, no hypothesis test is made from the pilot. Decisions about a
main stage need a new pre-registration.

## 8. Scope, simulated versus observed

Readiness (two rounds, 28 sessions in all, [experiment-v4-readiness.md](experiment-v4-readiness.md))
is not measured data. Observed: every session's events, files, mailbox, cost and denials. Constructed: the project and
tickets. Offline tests (`test_experiment_v4.py`) use only simulated agents (`SimDriver`) and check
plumbing, the collision detector and the kill ordering, not behaviour. One model, one host, short
tasks (about 30 s of work per agent in readiness; an API stall can last minutes): nothing transfers beyond them.

## 9. Freeze table (values known at this writing; refill by `python3 scripts/experiment_v4.py --freeze` on the commit that precedes the first measured session)

| What | SHA-256 |
|---|---|
| `scripts/experiment_v4.py` (code commit `6ca3fe4`) | `fbabe69394a17a6e9831fa337d8ebd5defa9dff5fab6ed8a5278a73f8d3e810a` |
| `scripts/analyze_v4.py` | `e23d5da494bf1e37938f52046699e553e23c5ccd639b9634ac947e02c9066744` |
| `test_experiment_v4.py` | `b9a570094dfd0ec2988ebadd8a79aa5e34315da0041900ff9110f35f8bcead23` |
| imported `scripts/experiment_v2.py` | `9370ffbe7a8caedcf67cecc4f0ad926df75e68a88725bf3bb915ab6ec0858629` |
| imported `scripts/e2e_claude.py` | `99788b5fc83d6d3ff9e733154e6a4cd869b13f12e3f92ab467159a4e1c893237` |
| imported `scripts/experiment_micro.py` | `082d269a32a2e98f415378798e99fbe207153e59233e9e0f45d51a557887ea7c` |
| `agent_mail.py` (as installed) | `4efc7532e08ddbe3bcfa25877cf0fb99ab8483173aeeb4507511fcc99b95087a` |
| `install.py` | `c3a2dd450842b8e5e7690322f042729238d6b8ccf631e8a23ea061bbf5975d32` |
| arm prompts incl. timing (`prompts_hash()`) | `d6623613a67f5cacf44d6e5eb4d65e56ba70114d758a609023047448b2a2f9f7` |
| passing live readiness gate (round 3, `passed: true`, same prompts and script hash) | `6b886e1cb563d4b7e0e10917fcffc6f6ad36febf79a9cd6d9a4943274920d517` |

Pilot command: `POSTBOX_EXPERIMENT_LIVE=1 python3 scripts/experiment_v4.py --stage pilot --live
--out <dir outside the repo> --gate <readiness_gate.json>`; summary: `python3 scripts/analyze_v4.py <dir>`.

**Dated entry, 2026-10-16 (integrator), before any measured run.** `agent_mail.py` in the freeze
table above is the merged `main` version, not the one used in the readiness rounds (hash
`be522efbd8b0cec56d51f0ec5493605743e5a34a75c3c3dd6e8fe13c5291c8d8`). Between them four unrelated changes were merged: duplicate PARTICIPANTS.md rows are
merged and `~unknownuser` is `NO MAILBOX` (#61), `send` warns about a CLAIM filed in a linked
worktree (#63, stderr only), and `doctor` prints a `writable:` line (#65). None alters the claim,
lock, lease or pickup code the arms use; the pilot's own throwaway projects are plain
checkouts, not linked worktrees. The readiness gate was not regenerated.

