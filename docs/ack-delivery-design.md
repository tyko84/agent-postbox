# Acknowledgment-based pickup: a design, a prototype and what it measured

Status 2026-10-17: **a proposal. Nothing here changes the shipped hook.** The owner ruled out once-only
delivery if any message could be permanently skipped after compaction, interruption or failed processing
([context-cost.md](context-cost.md), idea 22). This page tests an alternative that keeps every message in
the mailbox and lets the recipient, not the hook, decide when a message stops being listed.

## The idea

Today a NOTICE or ANSWER is listed on every prompt for seven days. Proposal: the pickup stops *listing*
a NOTICE, ANSWER or DISPUTE for an identity once that identity has itself filed a message whose
`reply_to:` cites it. Nothing new in the protocol or on disk:

* **Persistent history.** Nothing is deleted or moved; `list`, `inbox`, `status`, `show` still see everything.
* **Explicit acknowledgment.** The recipient files it, after acting. A self-addressed message
  (`--from me --to me --reply-to <id>`) is invisible to every other pickup, so an ack costs the sender
  nothing in anyone else's context. (Verified: a self-ack hides the message for its author only; `doctor`
  reports no dangling reply.)
* **Recoverable.** No state is kept by the hook. A message is hidden only by a file the recipient wrote.
  Interruption, compaction or a failed turn before the ack leaves it listed, so the next prompt shows it
  again. A fresh session sees every unacknowledged message with no handover.
* **Bounded, not silent.** An ASK still stays listed until answered, a CLAIM until it lapses; neither is
  ever hidden by this rule. A line says how many acknowledged messages are not listed and how to list them.
* **On-demand full text.** `show <id>` (already shipped).
* **Dedup after acknowledgment.** That is the rule itself.

## Prototype and measurement (bytes, replay; not tokens, not a session)

Prototype: a 9-line change to `hooks/agent_mail_check.py`
([patch](data/context-cost/ack-pickup-prototype.patch), not applied anywhere). Replay:
[replay_ack.py](data/context-cost/replay_ack.py) runs the real hook after each of 47 messages of the real
Claude/Codex mailbox, and after every prompt the identity files an ack for every NOTICE, ANSWER or
DISPUTE it was just shown. **That is the best case**: it assumes one prompt per message, an agent that
acknowledges everything immediately, and one `send` per ack.

| Identity | Hook bytes now | With acks (incl. the "N not listed" line) | Ack commands the agent writes | Net |
|---|---|---|---|---|
| collaborator | 57,500 | 30,290 (-47%) | 13 acks, 5,083 B | 35,373 (-38%) |
| codex-coordinator | 53,009 | 43,580 (-18%) | 5 acks, 2,005 B | 45,585 (-14%) |

Bytes are exact. Tokens are not measured here; the 0.55 tokens per byte of context-cost.md is a
measured input-token ratio and does not apply to ack commands, which are model *output* and tool
round-trips. An ack costs about 390 B of command plus a turn; a listed notice costs 110 to 190 B per
prompt, so an ack pays for itself only if the message would otherwise be listed in about three or more
later prompts.

## What is not shown, and what must be before any merge

* **No real model has acknowledged anything.** Whether an agent acks after acting (right) or by habit
  before acting (the failure: a hidden message it never processed) is unmeasured. The mailbox keeps the
  message, so it is recoverable by `list`, but the pickup would no longer prompt for it.
* **The saving depends on ack discipline and prompt count**, and is an upper bound here.
* One ack per message: a multi-id ack would be a protocol change and is not proposed.
* **Required before merging (owner's rule):** independent review (Codex, asked in the private mailbox);
  tests that an unacknowledged message is listed after a simulated compaction, interruption and
  fresh session; a real-host run in which a model acknowledges and a later session still finds the
  message by `list`; and a decision on whether `doctor` should flag a message acknowledged but never
  cited in any reply or action.

Reproduce: `python3 docs/data/context-cost/replay_ack.py <checkout with the patch applied> <mailbox dir>`.
