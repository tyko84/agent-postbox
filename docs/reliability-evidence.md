# Reliability evidence: twelve realistic scenarios

What is known, and how it is known, about twelve things that go wrong when several
coding agents share a mailbox. Nothing here is a new claim about the protocol: each row
points at a test or a recorded run, names what it does not cover, and says what a human
would see if it failed. Written 2026-10-14 against `main` at 85b592f plus the fixes listed
below.

## Evidence types

| Label | Meaning |
| --- | --- |
| **deterministic** | A test in CI drives the real CLI (and, where named, the real hook) in a temporary directory and asserts on files, output and exit status. Scripted, repeatable, no model. |
| **simulated** | Scripted agents (no model) drive the real tool on a schedule: `scripts/scenario.py` (`test_scenario.py`), `scripts/team_sim.py` (`test_team_sim.py`), `test_field.py`. A "prompt boundary" is the harness calling the hook. |
| **observed** | A real Claude Code session (headless `claude -p`, `haiku`) did it, recorded in `docs/e2e-real-sessions.md` (T1 to T7) or `docs/micro-experiment.md`, or in the one new run below. Not in CI. |
| **not covered** | No test or run. Said so, not papered over. |

The `check` names below are the strings a test prints, shortened; `grep` for them.

## The table

| # | Scenario | Mechanism | Evidence | Where | Gaps | What the user sees if it fails |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Two agents at once | One message per file, published by hard link (never overwritten); scope claims and keyed sends under kernel `flock`; readers never write | deterministic; simulated | `stress_test.py` (16 senders x 6, torn-write detector, keyed sends, scoped claims, a killed sender); `test_hardening.py` "exactly one racer wins", "8 simultaneous retries -> one message"; `test_field.py` phase 4 (4 agents x 25 plus three live readers); `scripts/scenario.py` (`test_scenario.py`) | Local disk only. Two processes that both think they are agent-a are not detected (identity is a name, PROTOCOL.md section 40). No real-model run with two agents writing at once (the micro-experiment ran them one after another) | `send` exit 3 for a lost scope race, with who holds it; `list` REJECT lines and exit 2 for a quarantined file; `doctor` FAIL |
| 2 | An agent restarts unexpectedly | Mail is files; there is no per-session state to lose; a new session runs the hook at its first prompt | deterministic; observed | `test_hardening.py` "there is no read state: a second pickup delivers the same messages again"; `docs/e2e-real-sessions.md` T3 `--resume`, T6 (SIGKILL a running session, a fresh one reads the mail) | The restarted agent must have `AGENT_MAIL_IDENTITY` set again; a shell that lost it gets a silent hook. A restart that loses a half-finished task is not a mail matter | Hook is silent without the identity; `doctor` prints `AGENT_MAIL_IDENTITY: UNSET in this shell: ... looks exactly like an empty inbox` (PROTOCOL.md section 41, `test_cli.py` `test_visibility`) |
| 3 | Mail arrives while the recipient is busy | The hook runs once per submitted prompt (`UserPromptSubmit`). Nothing runs inside a turn. | deterministic; observed | `test_adapters.py` `DeliveryTimeline.test_mail_filed_after_the_hook_ran_...` (poll sees it at once, hook at the next run); `docs/e2e-real-sessions.md` T3 live-next-prompt; **new observed run (below)**: mail sent 8 s into a 29.5 s turn was not seen by that turn | A long autonomous run with no new prompt does not see the mail until it ends or the agent polls. There is no daemon and no polling loop, by design. `PostToolUse`-style mid-turn injection exists in some hosts (docs/hosts.md lists Cursor's) and is not shipped | Nothing at the time. The sender sees an open ASK get older in `status` (scenario 12). The limit is now stated in PROTOCOL.md section 7 and in the hook's docstring |
| 4 | The recipient's session ends for good | Messages persist; an ASK stays open until answered | deterministic; observed | `DeliveryTimeline.test_an_ask_nobody_has_read_...`; `docs/e2e-real-sessions.md` T6 | An ASK has no expiry and no "recipient gone" state; a recipient that has never written is not in `status`'s agents table, so nothing says it is silent | `status`: `asks: open=N oldest_open_age=...h` with the id; the sender's `list --from <me> --type ASK --status open` |
| 5 | Several messages arrive together | Pickup lists the newest 20 plus every ASK awaiting a reply, counts the rest, prints one `Reply:` line | deterministic; simulated | `test_adapters.py` `test_the_hook_spells_out_the_newest_20_live_messages...`, `test_the_cap_never_hides_an_ask...`; `test_hardening.py` "no loss and no duplicate: every live id appears exactly once"; `test_adversarial.py` BIG_HOOK hook-output bound; `test_field.py` burst | Output is not bounded when there are very many open ASKs (a probe with 1,000 open ASKs printed about 250 KB; the cap deliberately never hides an ASK). A real model with 20+ messages is not observed | `(N older live message(s) not listed; ...)` in the hook output; `list --to <me> --live` shows all |
| 6 | A claim holder disappears | A claim is a lease with a mandatory expiry; a lapsed lease holds nothing; a live holder's scope lock is kernel-held, a dead one's is reclaimed at once | deterministic | `test_hardening.py` "a claim is held one second before its expiry ... expired at the expiry instant", "dead holder: rival reclaims the lock at once", "owner giving a scope up"; `selftest.py` "expired CLAIM is not delivered", "a lapsed (unrenewed) claim is named as NOT live"; `test_field.py` phase 1 (lease expires, a rival wins); **new** `DeliveryTimeline.test_a_lapsed_claim_frees_the_scope_...` (real clock: refused with who and until when, then takeover after expiry) | No real-model run of a holder that dies. A rival may only wait for the lease (up to 168 h) or send a DISPUTE; nothing revokes a live lease. A lease is advice: an agent can edit the file anyway (section 5) | Rival: `scope 'x' is already held by alice (<id>, until <time>)`, exit 3. `status`: held-claims table with time left; `CONTESTED_SCOPE`, `TOO_LONG`, `EXPIRED` attention rows. The holder's own hook lists `NOT live (EXPIRED)` |
| 7 | Message to an invalid recipient | Identity charset is checked (exit 2); a well-formed name with no row warns on stderr, the message is still filed (section 41) | deterministic | `test_cli.py` `test_visibility` ("send --to <typo>: still exit 0 ... one stderr line ... names the near match"); `test_adapters.py` `test_a_padded_to_is_not_called_an_unknown_recipient`, `DeliveryTimeline.test_an_ask_to_a_name_nobody_has_stays_listed...`; `selftest.py` "ask with one invalid recipient: exit 2 ... nothing written" | The warning exists only when the mailbox has a `PARTICIPANTS.md` with rows. A mistyped **`AGENT_MAIL_IDENTITY`** (the receiving side) is not flagged by `doctor`. Not run in a real session | One stderr line at send time naming the near match. Afterwards only the sender's own `list --from` shows it. Broadcast words: see "Defects found and fixed" |
| 8 | Recovery after an interrupted operation | Temp file then `link()`; a dead writer's temp is hidden and counted; locks and key markers are `flock`ed, so death releases them | deterministic | `test_hardening.py`: SIGKILL at link, after link, key reserved, scope lock held; SIGTERM/SIGINT in the key wait, before and after publish; killed fan-out to three recipients and its keyed retry; killed canary; disk full, EACCES, no hard links, no `flock`; `stress_test.py` a SIGKILLed sender, and `--faults` (a real send killed at every write step: 688 SIGKILL and 666 SIGTERM points, 600 seeded interleavings; `docs/fault-injection-results.md`); `test_adapters.py` `SettingsWrite.test_a_write_killed_midway_leaves_the_users_file_whole`; `scripts/scenario.py` `interrupted-writes` | A power loss is covered by `fsync` ordering (asserted by a patched-`fsync` check) but not by cutting power. `ask` has no `--key`: a killed `ask` fan-out retried by hand duplicates for the recipients already served (documented, pinned). No real session killed a *send* | `status`/`doctor`: `stale_tmp`, `ORPHAN_*`, `STALLED_LOCK_HOLDER`; `send` prints `nothing written` or the id |
| 9 | Duplicate prevention | `send --key K`: a retry with the same content answers `duplicate of <id>` (exit 0); different content with the same key is exit 2; markers are per sender | deterministic; simulated | `test_hardening.py` "retry returns the original id", "same key, different content refused", "8 simultaneous retries", "orphaned marker + 8 retries -> one message", key-wait checks; `test_field.py` phase 2 and burst; `scripts/scenario.py` (reactive ANSWERs carry `--key`) | Duplicates are prevented only when the sender passes a key. An unkeyed `send` or any `ask` retried after a lost reply writes a second message; nothing detects it. Real models were not observed choosing to pass keys | `send` prints `duplicate of <id>` on a deduplicated retry; otherwise the user sees two messages in `list` |
| 10 | Filesystem contention | `flock` on scope locks and key markers; probing status never takes a lock it cannot release; readers tolerate files that appear or vanish | deterministic | `stress_test.py` (readers and `status`/`doctor` loops against 16 writers; slowest send bounded); `test_hardening.py` "a reader's shared lock on an orphaned scope lock", "{N} claims and {N} keyed retries over probed orphans all succeed", read-only mailbox, `flock` refused | Local filesystems only. NFS, SMB, a synced folder (Dropbox, iCloud) and a mailbox on a FUSE mount are not tested; `flock` and hard links may not behave there. Windows unsupported | `send`: one line naming the errno (`ENOSPC`, `EACCES`, `ELOOP`), exit 1, `nothing written`; exit 3 `being claimed right now; retry` |
| 11 | Acknowledgement and outcome | ASK -> ANSWER (`reply_to`) -> optional `result: DONE/PARTIAL/FAILED/DECLINED/CANNOT_VERIFY`, a claim and never proof (section 39). **Delivered** = a file exists. **Seen** = recorded nowhere (the hook writes nothing). **Acknowledged / done** = a durable ANSWER | deterministic; observed | `selftest.py` (ANSWER `--result` set and refusals, owed-ASK lists); `test_hardening.py` "the hook wrote nothing ... no read state"; `DeliveryTimeline.test_delivered_seen_acknowledged_done_are_not_all_visible_to_the_sender`; `docs/e2e-real-sessions.md` T4 (3 of 3: one ANSWER, right `reply_to`, `result: DONE`) | **A sender cannot tell "delivered" from "seen".** A recipient that read the ASK and ignored it looks identical to one that never ran its hook. Only the answerer's own `result` says "done", and it is a claim. A NOTICE has no acknowledgement at all | Open ASK in `status` and in `list --from <me> --status open`; `ANSWER` in the sender's next hook output; `status` counts `claimed result` |
| 12 | Failure visibility to the human | The hook is silent on every failure (it must never block a prompt); `doctor` and `status` are the human's window | deterministic; observed | `test_cli.py` `test_visibility` (hook MISSING, unparseable settings, identity unset), `test_linked_worktree`; `test_adapters.py` `test_doctor_does_not_call_an_unlistable_mailbox_healthy`; `test_hardening.py` status/doctor problem codes; `docs/e2e-real-sessions.md` T7 (a missing hook script blocked every prompt before the `|| true` fix; broken mailbox: silent exit 0) | Nothing pushes a failure at the human: no notification, no log. A human learns only by running `status` or `doctor`. `doctor` does not say "this ASK was delivered to nobody who ran a hook". The interactive UI was never driven | `status`: open-ASK count and age, `PROBLEMS` line, stalled agents (those that have sent). `doctor`: `store:`, `pickup_hook:`, identity, canary, and now the `worktree:` line; exit 2 plus `FAIL:` lines |

## Observed in a real session for this page (scenario 3)

One headless Claude Code 2.1.296 session, `haiku`, tools `Bash` allowed only for a
`python3 -c` command, run through the repository's own `scripts/e2e_claude.py` helpers
against a throwaway project installed with `install.py --adapter claude-code`. The prompt
told the model to run `python3 -c 'import time; time.sleep(25)'` and then answer with the
control token from agent mail in its context, or `NONE`. The harness filed a NOTICE
carrying a random token for that identity **8 s into the turn**.

| | |
| --- | --- |
| Turn length | 29.5 s |
| Hook events in the session | 1 (at the prompt, before the mail existed): exit 0, no output |
| Answer | `NONE`; the token was not seen |
| Cost | USD 0.00016 (this run). A first attempt used `sleep 25` as the command, which the host's permission rules refused, so the turn ended at once and proves nothing; it is not counted (USD 0.001). Total spend on real sessions for this page: about USD 0.002 |

It is one run on one model, so it shows the mechanism, not a rate. It agrees with what
the host documents (the event fires per prompt) and with `docs/e2e-real-sessions.md` T3,
which shows the same mail arriving at the **next** prompt of one live session.

What reduces the gap without a daemon: the agent running `agent_mail.py inbox --to <me>`
itself at the points where it would act on shared state (PROTOCOL.md section 7). That is
a habit, not a mechanism; the protocol says the same of every poll. Hosts that offer a
post-tool-call context hook could close it, but none is shipped and none was tested.

## Defects found and fixed

1. **A message to a broadcast word reached nobody and nothing said so.** `send --to all`
   (also `any`, `*`, `everything`) filed the message, but the hook and `list --to <name>`
   match `to` against one name, so no agent's context ever showed it. Section 41 even
   exempted those words from its recipient warning, so the one check that exists for
   "nobody will read this" stayed quiet for the commonest broadcast spelling. Reproduced:
   `send --type CLAIM --to all`, then `hooks/agent_mail_check.py` for another identity:
   no output; `list --to bob --live` did not list it. Fix: one stderr warning (exit
   status and stdout unchanged) saying it is a broadcast word and no pickup shows it,
   with the remedy (name each reader, or declare an alias). Regression:
   `test_cli.py` `test_visibility`, two checks; run against the unfixed code both fail
   (the `all` send printed nothing on stderr). `test_adversarial.py` had one send to `all`
   that asserted an empty stderr by accident; it now sends to `bob`.
2. **Two worktrees of one repository silently had two mailboxes.** `git worktree add`
   checks out its own `docs/agent-mail`; the walk-up default finds that one. Mail filed
   in the main checkout is invisible in the worktree, and `doctor` there PASSes once the
   agent files a canary to itself. Reproduced with a real `git worktree add`. Fix: `doctor`
   prints one informational `worktree:` line, only when `AGENT_MAIL_DIR` is unset and
   `.git` is a file; no new FAIL, no change elsewhere. Regression: `test_cli.py`
   `test_linked_worktree` (a real linked worktree not seeing the main checkout's mail;
   the line present for a linked worktree and absent for a main checkout and for a named
   `AGENT_MAIL_DIR`); run against the unfixed code two checks fail. The README does not
   mention worktrees; the integrator may want a sentence there.

Neither fix changes a message format, an exit status, a stdout line a script may parse,
or `status --json` (its key set is pinned by `test_hardening.py` and passes).

## Not covered

* A real session that is killed **during** a `send`, `ask` or `answer` (T6 kills a
  session that had no send in flight; the send-kill cases are deterministic only).
* A real model reading mail while another real model writes at the same moment.
* A holder of a claim that really disappears (real model), and a rival that waits it out.
* Network, synced or FUSE filesystems; power loss; `flock` on a filesystem that
  lies about it.
* The interactive Claude Code UI, Codex and Cursor in the real host (docs/hosts.md).
* A human noticing: no test checks that a person would see `status` or `doctor` output,
  only that it says the right thing when run.
* "Seen" and "acted on". There is no read state by design; a recipient that read an ASK
  and ignored it is indistinguishable from one that never saw it.
* A mistyped `AGENT_MAIL_IDENTITY` (reader side) is not flagged. A `doctor` line when the
  identity is not a row in a non-empty `PARTICIPANTS.md` would be cheap; it was left out
  as a protocol addition, not a defect fix.
* The hook repeats every live message at every prompt until it is answered or ages out
  (7 days for a NOTICE); on a long session that is repeated context. Pinned as intended
  by `test_hardening.py`; not measured for cost.

## What is simulated versus observed

* **Observed (a real model in the real host, headless, Claude Code 2.1.296, `haiku`)**:
  delivery at the prompt boundary, no cross-talk, next-prompt delivery, `--resume`,
  recovery after a killed session, answering an ASK (3 of 3), a broken mailbox, a missing
  hook script (`docs/e2e-real-sessions.md`); a 20-instance pilot of a fact moving between
  agents with no advantage shown (`docs/micro-experiment.md`); and the one mid-turn run
  above. Small n, one model.
* **Simulated (scripted agents, real tool)**: `scripts/scenario.py` with injected
  interrupted writes, abandoned locks, crashes, malformed files and delayed polling;
  `scripts/team_sim.py` (a model of the mechanism, not of the tool); `test_field.py`.
  "Agents" are Python loops; "a prompt boundary" is the harness calling the hook.
* **Deterministic tests** assert on the real CLI and hook in temporary directories, with
  a positive control next to each silent case. They say nothing about what a model does
  with the text.
* Everything in the table's "Evidence" column is exactly one of these, and the "Gaps"
  column is where that evidence stops.
