# Acknowledgment-based pickup: a design, a prototype and what it measured

Status 2026-10-17: **a proposal. Nothing here changes the shipped hook.** The owner ruled out once-only
delivery if any message could be permanently skipped after compaction, interruption or failed processing
([context-cost.md](context-cost.md), idea 22). This page tests an alternative that keeps every message in
the mailbox and lets the recipient, not the hook, decide when a message stops being listed.

## The idea

Today a NOTICE or ANSWER is listed on every prompt for seven days. Proposal: the pickup stops *listing*
a NOTICE, ANSWER or DISPUTE for an identity once that identity has itself filed a **meaningful
acknowledgment** of it. Nothing new in the protocol or on disk. An acknowledgment is a message the
recipient filed, citing the message in `reply_to:`, that is either

* a **real reply**: addressed to someone else (an ANSWER to the sender, say), which other agents can
  see; or
* a **self-ack that states an outcome**: `--from me --to me --reply-to <id> --result DONE|...`. It is
  invisible to every other pickup, so it costs the sender nothing. A bare self-addressed note with no
  `result:` hides nothing, so a one-word "seen" is not enough.

Output being generated is not an event: nothing the model prints, and nothing the hook shows, hides
anything. Only a file the recipient wrote does, and the file is permanent.

* **Persistent history.** Nothing is deleted or moved; `list`, `inbox`, `status`, `show` see everything.
* **Recoverable.** The hook keeps no state. Interruption, compaction or a failed turn before the ack
  leaves the message listed; a fresh session lists exactly the unacknowledged set with no handover.
* **Bounded, not silent.** An ASK is listed until answered, a CLAIM until it lapses; this rule never
  touches them. A line says how many acknowledged messages are not listed and how to list them, and it
  is printed even when *every* live message is acknowledged (the first version of the prototype printed
  nothing in that case; the test below caught it).
* **On-demand full text.** `show <id>` (already shipped). **Dedup after acknowledgment:** the rule itself;
  a retried keyed ack writes nothing new.
* **Hazard, and it is existing behavior (section 29):** any message citing an ASK closes that ASK. An ack
  must therefore **never cite an ASK**; it is for NOTICE, ANSWER and DISPUTE only.

## Prototype, tests and measurement

Prototype: a small change to `hooks/agent_mail_check.py`
([patch](data/context-cost/ack-pickup-prototype.patch), applied nowhere). Tests:
[test_ack_pickup.py](data/context-cost/test_ack_pickup.py) (15 checks, all pass against the patched hook):
unacknowledged mail listed; replay gives identical output; a bare note, a different identity's ack and an
ack citing nothing hide nothing; a self-ack with `result:` hides it and the "not listed" line appears,
including when nothing else is live; a retried keyed ack writes nothing new; `list --live` and `show`
still return the original; a real reply acknowledges; the ASK-closing hazard; an ack **SIGKILLed before
it is linked** leaves the message listed; a process with no memory lists the same set.

Replay: [replay_ack.py](data/context-cost/replay_ack.py) runs the real hook after each of 48 messages of
the real Claude/Codex mailbox, and after every prompt the identity files a self-ack with `--result DONE`
for every NOTICE, ANSWER or DISPUTE it was just shown. **That is the best case**: one prompt per
message, an agent that acknowledges everything at once, one `send` per ack.

| Identity | Hook bytes now (main) | With acks (incl. the "not listed" line) | Acks the agent writes | Net |
|---|---|---|---|---|
| collaborator | 59,810 | 32,055 (-46%) | 14 acks, 5,684 B | 37,739 (-37%) |
| codex-coordinator | 54,492 | 45,296 (-17%) | 5 acks, 2,080 B | 47,376 (-13%) |

Bytes are exact. Tokens are not measured: the 0.55 input tokens per byte in context-cost.md applies to
injected text, not to ack commands, which are model *output* plus a tool round-trip. An ack costs about
400 B of command; a listed notice costs 110 to 190 B per prompt, so an ack pays for itself only if the
message would otherwise be listed in about three or more later prompts.

## What is not shown, and what must be before any merge

* **No real model has acknowledged anything.** Whether an agent acks after acting (right) or by habit
  before acting (the failure: a hidden message it never processed) is unmeasured. The mailbox keeps the
  message, so it is recoverable by `list`, but the pickup would no longer prompt for it.
* **The saving depends on ack discipline and prompt count**, and is an upper bound here.
* One ack per message: a multi-id ack would be a protocol change and is not proposed.
* **Simulated, not real-host:** interruption (SIGKILL before the link) and "new session" are process-level
  tests, not a real Claude Code or Codex session. A real model has not been run through it.
* **Required before merging (owner's rule):** independent review (Codex, asked in the private mailbox);
  a real-host run in which a model acknowledges and a later fresh session still finds the message by
  `list`; a measured rate of premature acknowledgment by real models (behaviour test, as in
  `scripts/e2e_claude.py`); and a decision on whether `doctor` should flag an acknowledged message that
  no later action cites. Not merged until all of these exist.

Reproduce: apply the patch to a checkout, then `python3 docs/data/context-cost/test_ack_pickup.py <checkout>` and `python3 docs/data/context-cost/replay_ack.py <checkout> <mailbox dir>`.
