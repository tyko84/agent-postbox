# Benchmarking and reliability: `scripts/scenario.py`

`scripts/scenario.py` runs a reproducible workload of simulated agents against
the real tool and measures delivery, latency, claims, recovery after injected
faults, and the difference between the shipped pickup adapter and an agent
that merely polls. It is stdlib-only, needs no service, runs locally and in
CI, and prints `OK`/`FAIL` lines for the reliability invariants the adapter
configuration must hold.

```sh
python scripts/scenario.py --quick --seed 1             # the CI step, well under a minute
python scripts/scenario.py --seed 1 --json out.json     # 8 agents, 400 messages, all faults
python scripts/scenario.py --agents 12 --messages 1000 --rounds 60 --faults crashes,malformed
```

Exit status: 0 every check `OK`; 1 any `FAIL`, an aborted run (time budget)
or a failure inside the harness itself; 2 a usage or tooling error.

## What is simulated, and what is real

**Real.** Every message is written by `agent_mail.py send`, run as a
subprocess exactly as an agent's shell would run it (keyed with `--key`, scoped
and leased for a `CLAIM`, `--reply-to` for an `ANSWER`). Every pickup in the
`hook` configuration is the shipped adapter `hooks/agent_mail_check.py`, run
with `AGENT_MAIL_IDENTITY` set, the way a host's prompt-submit event runs it;
the harness parses what it printed. Every pickup in the `baseline`
configuration is `agent_mail.py inbox --to <me> --json`. The operator's view at
every boundary is `agent_mail.py status --json`. Scope locks, key markers,
quarantine, derived status and liveness are the tool's own; nothing of the
protocol is reimplemented in the harness, which only schedules, invokes, parses
and counts. The crashes are real: a sender is SIGKILLed mid-write by running
the real `agent_mail.main` with one function (`os.link`, or the scope-lock
function) made to block at the point the fault names, the same technique
`test_field.py` uses. The faults of a round are injected one at a time before
the round's other sends start, so nothing else holds the scope lock at the
instant a claimant must be parked on it.

**Simulated.** The agents. There is no model and no host: an "agent" is a
name, a set of ids it has already seen, and a rule (answer every `ASK` that
was surfaced to it, at its next turn). A "prompt boundary" is the harness
invoking the pickup; a real host would invoke the adapter on its own
prompt-submit event, which this harness stands in for. Nothing here measures
how a language model behaves once mail is in its context; it measures whether
the mail gets there, when, and whether the store stays consistent while agents
crash.

**The baseline is an assumption, not a measurement.** The `baseline`
configuration models an agent that has no adapter and checks its inbox only
when it remembers to: at every boundary it polls with probability `--poll-p`
(default 0.5) and forgets with probability `1 - p`, from a seeded table that
is printed as `poll_hash`. That number is a modelling choice about agent
behaviour. The repository's own evidence (README, "The problem it solves") is
that the real rate was zero over 22 hours; `--poll-p 0.5` is deliberately
generous to the baseline. The baseline's numbers are reported next to the
hook's for comparison and are never thresholded: it is the comparison, not the
system under test. Change `--poll-p` to see how the comparison moves.

## The workload and its determinism

A run is `--rounds` rounds of three phases, then `--drain` extra boundaries
with no scheduled sends so that late mail can still be picked up:

1. **send**: every agent performs the sends the schedule gives it for this
   round, concurrently (`--concurrency` subprocesses at a time);
2. **boundary**: every awake agent runs its pickup, concurrently; then
   `status --json` is read, on the real clock and once more with `--now` 61 s
   ahead (see "Recovery");
3. **react**: every agent answers the `ASK`s its pickup just surfaced, with
   `send --type ANSWER --reply-to <id> --key <key>`.

The schedule is a pure function of `--seed` and the parameters (`random.Random`
seeded with the seed and a per-stream label, so switching one fault on or off
does not reshuffle the others): the same values give the same senders,
recipients, types (`ASK` 35 %, `NOTICE` 40 %, `CLAIM` 20 %, `DISPUTE` 5 %),
subjects, keys, scopes, leases (a quarter of the claims take a 4 s lease and
lapse during the run; the rest 1 h) and fault schedule. The SHA-256 of the
canonical JSON of that schedule is printed as `schedule_hash`, once at the top
and once per configuration; **the `hook` and `baseline` configurations run the
same bytes**, and the first `OK`/`FAIL` line (`schedule-hash`) checks it.

What the schedule does not fix: the `ANSWER`s. They are reactions to delivery,
so a configuration that delivers late answers late, and "ASKs unanswered at
end" measures exactly that. Timing is not deterministic either: latencies,
which configuration wins a race for a scope, and whether a retried claim is
refused vary between runs; the counts of what was scheduled do not.

## Faults

Each is switchable with `--faults all|none|NAME,NAME` and scheduled by the seed.

| Fault | What the harness does | What must hold |
|---|---|---|
| `interrupted-writes` | SIGKILLs a sender after its temp file is written and before it is linked: one hidden `.tmp` and a locked key marker with no message are left. The sender retries the same keyed send next round. | The retry takes the key over and writes exactly one message; the recipient surfaces one id for that subject; the `.tmp` is never surfaced |
| `abandoned-locks` | SIGKILLs a claimant the instant it holds the kernel scope lock, before it has checked rivals or reserved its key. Next round the claimant retries and another agent's `CLAIM` on that scope (added to the schedule) contends with it. | The orphan lock is reclaimed by one of them; the other is refused with exit 3 or wins; no `.scope.*.lock` file remains; `status` never reports a contested scope |
| `crashes` | An agent stops for K rounds (1 to 3): no sends, no pickup, no answers; its sends are deferred to the round it resumes. | Mail addressed to it is surfaced once it is back; nothing is lost |
| `malformed` | Plants a file with `type: ACK` addressed to an agent, with a well-formed id. | `status` counts it as quarantined; no pickup ever surfaces it; `inbox` exits 2 while it is there (the admitted mail is still listed), the adapter still exits 0 |
| `delayed-polling` | An agent keeps sending but its pickup is not run for K rounds (the host did not invoke it). | Mail is surfaced at the next pickup; nothing is lost |

Crash and delay windows end before the drain, so every message gets a chance.

## How to read the output

The summary has one line per metric with the configurations side by side:

```text
metric                                      hook      baseline
pickup mechanism                            hook    inbox-poll
...
deliverable messages                         466           469
delivered                                    466           467
missed                                         0             2
delivered at first chance                    325           165
latency p50                              0.1368s       0.3388s
latency (boundaries) p50                       0             1
latency (boundaries) p95                       1             5
...
```

(seed 2, 8 agents, 400 messages, all faults, `--poll-p 0.5`, a laptop; the
two configurations see slightly different deliverable counts because which
claim wins a scope race is decided at run time, not by the schedule.)

* **deliverable**: messages that were written (exit 0, `wrote`) and live for a
  whole boundary that began after the send returned, at which the recipient
  was awake (not crashed, not in a delayed-polling window). A reactive
  `ANSWER` is sent after its round's boundary, so its first chance is the
  next one; an answer sent after the last boundary has none. A 4 s claim that lapsed before the recipient's
  next boundary is not deliverable and is not counted against anyone. For the
  baseline a "forgot" boundary still counts as a chance, which is the point.
* **delivered / missed**: surfaced to the recipient at least once before the
  run ended, or never. **delivered at first chance**: surfaced at the first
  boundary at which it was deliverable.
* **latency**: from the moment the sender's `send` returned to the moment the
  pickup that first surfaced the message returned, in seconds and in prompt
  boundaries (0 means the first boundary that began after the send). Percentiles are nearest-rank and
  printed only when the sample size justifies them: `p50` needs n >= 10,
  `p95` n >= 20, `p99` n >= 100, otherwise `n/a (n=...)`; min, max, mean and
  n are always printed. Seconds depend on the machine and on `--concurrency`;
  boundaries do not.
* **duplicate ids per message**: a subject surfaced under two different ids
  (a keyed retry that wrote twice). **stale surfacings**: a closed message
  shown again (an `ASK` after its recipient's `ANSWER` landed, a `CLAIM` after
  its lease ended). **re-surfaced live mail** is live mail shown again at a
  later boundary: the adapter is stateless and shows everything live at every
  prompt by design (PROTOCOL.md section 9), so this is informational.
* **conflicting live claims**: the maximum of `status`'s `contested_scopes`
  over all boundaries, with the harness's own count (two claims it wrote,
  from different senders, on one scope, both live over a whole boundary)
  beside it.
* **pickup success rate**: pickups that ran and exited as expected, over
  boundaries at which the agent was not crashed. The hook's is below 1 only
  through the `delayed-polling` fault; the baseline's is further lowered by
  forgetting. A poll that exits 2 because a planted file is quarantined
  counts as a success (it listed the admitted mail), and is counted under
  "pickup exit nonzero" so the difference is visible.
* **recovery**: for every injected crash, the number of boundaries from the
  fault until the system was consistent again: for an interrupted write,
  until the retry had landed; for an abandoned lock, until no lock file
  remained for that scope after a claim attempt on it. "boundaries with
  problems" and "orphan key markers at end" come from `status --now` shifted
  61 s ahead, past the 60 s in-flight grace that `status` gives a young lock
  or marker (PROTOCOL.md section 33): nothing is in flight at a boundary, so
  anything still there is an orphan and should be reported now, not a minute
  later. `final status clean` is that shifted list minus what is excused (see
  the thresholds).

## Thresholds, and why

Checked for the `hook` configuration only; each prints one line in the style
of `scripts/verify_release.py`.

| Check | Threshold | Why |
|---|---|---|
| `schedule-hash` | identical across the configurations run | otherwise the comparison compares nothing |
| `positive-control` | at least one message surfaced | silence proves nothing (PROTOCOL.md section 8): a broken adapter prints nothing, like an empty inbox |
| `missed` | 0 | delivery that does not depend on the agent remembering is the product |
| `duplicates` | 0 | a keyed retry must write once (section 31); a closed message must not come back (section 19) |
| `conflicting-claims` | 0 | one winner per scope (sections 5, 30) |
| `malformed` | every planted file quarantined, none surfaced | a file nobody handles must be loud, never delivered (sections 5a, 22) |
| `recovery` | every event recovered within `--recovery-bound` (default 2) boundaries | a dead holder's lock is reclaimed, never broken (section 30); an orphaned key is taken over at once (section 31) |
| `send-exit` | every send exits 0, or 3 for a `CLAIM` on a scope another agent holds, or was killed when the fault schedule says so | anything else is a refusal the schedule did not ask for |
| `status-clean` | the shifted final `status` reports nothing but what is excused | an operator must be able to trust `problems: []` |
| `leftovers` | no `.scope.*.lock` file; exactly one `.tmp` per sender killed mid-write | nothing else may leave a file behind |
| `pickup-exit` | every adapter run exited 0 | the adapter must never block a prompt (section 9) |

**Excused codes.** `QUARANTINED` when files were planted: quarantine never
deletes, so the count is the right answer. The `OK` line names what was
excused and why, so an excused run is not a silent one. Nothing else is
excused: an orphaned key marker, an orphaned or held lock, a stale takeover
mutex, a contested scope, a dangling reply or a stale temp file in the
shifted final `status` fails the run.

One thing the harness deliberately does not inject, because it was found and
then designed out of the fault: a claimant killed *after* reserving its key
and before publishing leaves a key marker that a retry refused at the scope
stage (exit 3, another agent holds the scope now) never reaches, so the
marker stays orphaned until a retry with the same content gets past the
scope check. It is inert and `status`/`doctor` report it after 60 s
(PROTOCOL.md sections 31, 33), but it is a leftover that only a later retry
clears. The fault here kills the claimant while it holds the lock and before
the key stage, which is the case section 30 describes.

## Running it

`--quick` (4 agents, 40 messages, 10 rounds, 2 drain) takes a few seconds and
is a step of the `selftest` job in CI on every supported Python. The default
profile (8 agents, 400 messages, 40 rounds, 3 drain, all faults) takes about
a minute on a laptop; each simulated send or pickup is a Python subprocess of
roughly 40 ms, run `--concurrency` at a time (default: one per agent).
`--max-seconds` (default 900) aborts a run that takes longer, with a `FAIL`.
`--json PATH` writes the report whose schema is in the module docstring:
parameters, seed, hashes, per-configuration metrics, thresholds, the checks
with their verdicts, and the environment (Python version, platform). Nothing
is written anywhere else; the mailboxes live in a temporary directory that is
removed afterwards.

`test_scenario.py` runs a tiny configuration twice (same `schedule_hash`),
plants a miss with `--self-test-miss` (exit 1 and a `FAIL missed` line),
checks the JSON fields, a usage error (exit 2) and an exception inside the
harness (exit 1, never 0).

## Known limitations

* The agents are scripted. Whether a model acts on mail that is in its
  context is outside this harness; what it shows is that the mail is there.
* The baseline's forgetting rate is a parameter, not data. With `--poll-p
  0.5` and a drain of three boundaries the baseline usually misses nothing by
  the end of the run and loses on latency and on answers still owed; with a
  lower `p`, or no drain, it misses. Report the `p` with the numbers.
* Latency in seconds is dominated by subprocess start-up and the round
  structure (sends in one phase, pickups in the next), so it says "same
  boundary or not" more than it says anything about the filesystem. The
  boundary count is the portable number.
* A kernel-held lock cannot be abandoned by a process that is still alive;
  the harness kills the holder, which is the case the protocol covers. A
  holder that hangs alive is `STALLED_LOCK_HOLDER` in `status` and is not
  injected here.
* `.tmp` files left by a killed sender are counted as stale by `status` only
  after 600 s and are never deleted by the tool; a run shorter than that
  reports them under "leftover .tmp files" from a directory listing, not from
  `status`.
* Claims are never renewed with `--supersedes` here, and `DISPUTE`s cite
  nothing; both paths are covered by `selftest.py` and `test_field.py`, not by
  this workload.
* The harness runs on one machine against a local filesystem, like the tool.
  Network filesystems, clock skew between hosts and more than one host
  writing the same mailbox are out of scope.
