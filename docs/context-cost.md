# Context cost: what Postbox puts in front of a model

Measured 2026-10-16 on macOS, Python 3.13, Claude Code 2.1.300, against `main` at `c627cc4`.
Every number below comes from `docs/data/context-cost/context_cost.py` (`scenarios`, `replay`,
`tokens`) or from the raw file next to it. **Bytes are not tokens, and a byte reduction is not a
token saving until it is converted with a measured ratio.** This page says which is which.

## What is measured, and how

| Quantity | What it is | Source |
|---|---|---|
| Bytes | UTF-8 length of what the pickup hook or a CLI command prints | `scenarios`, `replay` (exact, no model) |
| Model-input tokens | Input tokens the host reported for a run, minus a run with nothing injected | `tokens` (3 `claude -p` runs, haiku, the owner's subscription, about US$0.008; raw: `docs/data/context-cost/tokens.json`) |
| Token estimate | Bytes times the measured ratio below | arithmetic, labelled "est." everywhere |

The tool path is part of the hook's text (the `show` command), so absolute bytes depend on the install
path. The comparisons below use the same 62-character path for every variant; compare variants only at
equal path length.

### Bytes to tokens: measured, once each, not the usual rule of thumb

| Injected by the hook | Input tokens added | Tokens per byte |
|---|---|---|
| 0 B (baseline, 21,995 input tokens: the host's own prompt and tools) | 0 | |
| 733 B (one owed ASK) | +391 | 0.53 |
| 5,090 B (twenty live notices) | +2,856 | 0.56 |

About 0.55 tokens per byte, so about **1.8 bytes per token**, not the common 4 characters per token.
Ids, absolute paths and punctuation tokenize badly. One run per variant: the count is deterministic
for a given text, but this is two points, not a fit, and it was measured on one small model. Use it as
an estimate. For scale: the hook's text for one owed ASK is about 1.8 percent of a minimal prompt's
input; twenty live messages, about 13 percent.

## Baseline (before this change)

| Situation | Hook bytes | Est. tokens |
|---|---|---|
| No mail | 0 | 0 |
| First pickup, one owed ASK | 737 | 405 |
| Next prompt, nothing new, ASK still owed | 737 (again) | 405 (again) |
| After the ANSWER: the asker's next prompts | 587, every prompt for up to 7 days | 323 |
| A CLAIM addressed to you | 635, until it expires or is renewed | 349 |
| After a renewal | 889 | 489 |
| 5 / 10 / 20 / 40 live messages | 1,573 / 3,934 / 5,296 / 5,316 (capped at 20 listed) | 865 / 2,164 / 2,913 / 2,924 |
| `status` / `status --json` / `doctor` / `list` / `show <id>` | 697 / 1,763 / 764 / 471 / 178 | not injected; the agent runs them |

Takeover is an ordinary CLAIM (the row above); a refused competing claim costs 99 B of stderr.
Interrupted-session recovery is the same pickup (a resumed session runs the hook on its first prompt),
so it costs one pickup, not more.

What drives the cost, from reading the output: each listed message carries a `read:` command with the
full tool path (about 120 B, repeated per message); the Reply template (195 B) printed whenever any
message was live, even when nothing is owed; and the whole text is **repeated on every prompt** while a
message is live, because the hook keeps no state.

### A complete collaborative task (replay, not a session)

The real Claude/Codex mailbox of the alias-registry task (31 messages, one private local mailbox) was
replayed through the real hook: after each message, the hook ran on the mailbox as it was. This assumes
one prompt per message for each identity, which is an assumption, not a measurement of any session; a
lapsed CLAIM is not live in the replay, so claims are undercounted.

| Identity | Messages addressed to it | Hook bytes over 31 prompts | Est. tokens |
|---|---|---|---|
| collaborator (Claude) | 14 | 48,345 | 26,590 |
| codex-coordinator | 13 | 40,078 | 22,043 |

Delivering each addressed message exactly once would have cost about 8 KB per identity, so roughly
five-sixths of those bytes were repetition. The repetition is a property of a stateless hook, not a bug.

## The change that shipped, and what it measured

`hooks/agent_mail_check.py`: one `Full text of one: ... show <id>` line for the whole list instead of
one `read:` line per message, and the Reply template only when a reply is owed. Nothing else changes:
every id, type, sender, subject, the owed-reply block, the held-claims block and the live cap are as
before, and the full original is one command away (`show <id>`).

| Situation | Before | After | Change |
|---|---|---|---|
| One owed ASK | 737 | 726 | -1.5% |
| After an ANSWER (asker) | 587 | 375 | -36% |
| A CLAIM to you | 635 | 425 | -33% |
| After a renewal | 889 | 556 | -37% |
| 5 / 10 / 20 live messages | 1,573 / 3,934 / 5,296 | 870 / 2,001 / 2,749 | -45% / -49% / -48% |
| Replay, collaborator | 48,345 | 30,606 | -36.7% |
| Replay, codex-coordinator | 40,078 | 31,400 | -21.7% |
| Replay, both | 88,423 | 62,006 | -29.9% |

Est. tokens saved per prompt at twenty live messages: 2,547 B, about 1,400 tokens. Est. over the
31-prompt replay: about 9,800 tokens for the collaborator. Against the host's own input of roughly 22,000
tokens per minimal prompt, this is a saving in the busy-mailbox case and invisible in the quiet one.

Honest reading: the saving scales with the number of live messages; with one owed ASK there is none.
The bar set before building (30 percent fewer bytes on both identities, for a larger package that
included the rejected idea below) is not met by what shipped: -36.7 and -21.7 percent, -29.9 percent
together. Whether that is meaningful is a judgment; for mailboxes with five or more live messages it is
roughly half the bytes at no loss of information.

Regression coverage: `PickupOutputShape` in `test_adapters.py` (three of its four checks fail on the
old hook; the fourth, an owed ASK surviving the live cap, is a control that passes on both), and
`PrintedCommandQuoting` follows the new line prefixes. `test_scenario.py` (the delivery harness) passes.

### Real-host check (Claude Code 2.1.300, haiku, `scripts/e2e_claude.py`)

Delivery (T1, T2, 5 sessions): 6 checks OK, 0 delivery checks failed; the host reported the hook's
output at 434 B. Behaviour (T4, a model answers an ASK from the hook's text, scored by what lands in
the mailbox): the new format 16 of 18 sessions correct, the original hook 17 of 18, over six runs of
three sessions each, same machine and model, runs interleaved. The failures were one omitted
`--result DONE` and one duplicate ANSWER on the new side, one omitted `--result DONE` on the old side:
the same kinds in both arms, a difference of one session in eighteen, which is noise at this size. It
does not show that the format is neutral beyond this model and task. 36 haiku sessions in all.

## A rejected idea: collapse what you have already had a turn after

Tried and removed. The rule: a NOTICE, ANSWER or CLAIM older than the identity's own latest message
was necessarily delivered at that later turn, so count it in one digest line instead of spelling it out.
Stateless, nothing stored, an owed ASK and a DISPUTE never collapsed. It cut the replay further
(collaborator -55%) and was rejected on evidence: `test_scenario.py`, the project's delivery harness,
fell from a delivery rate of 0.714 to 0.60 and "missed" rose from 4 to 6. The harness simulates skipped
pickups (a crash, a delayed poll) where an agent keeps working and sends mail without having seen a
message; the proxy "I sent something since, so I saw it" is false exactly then, the case the hook
exists to cover. With only the digest switched off the harness passes again. A stateful cursor would
fail differently: per-session state, and the model loses injected context when the host compacts.

## The ten ideas, evaluated

"Existing" means the tool already provides it. Savings are measured only where a number is given.

| # | Idea | Expected benefit | Complexity and risk | Measured | Verdict |
|---|---|---|---|---|---|
| 21 | Compact inbox summaries | The hook already prints subjects only, never bodies | Low; formatting only | -22% to -49% (above) | Shipped |
| 22 | Incremental pickup | Largest: once-only delivery is about 6x fewer bytes in the replay | Needs per-session state; compaction and skipped pickups can hide mail; breaks the "no stored read state" rule (PROTOCOL.md section 29) | Not built; stateless proxy tested and rejected | Not now; a design would need a per-session cursor plus a recovery digest at session start |
| 23 | Actionable-message filtering | Owed ASKs and held claims are already separated | Hiding NOTICE evidence is a semantic change | Not measured | Existing in part; not built |
| 24 | Token-budgeted pickup | The 20-message cap already bounds the worst case at about 5.3 KB (about 2,900 est. tokens) | A budget adds a second cap to explain | Not measured | Existing in part; low marginal value |
| 25 | Structured task handoffs | `handoff_template.md` and `check_handoff.py` exist | Already a documented structure | Not measured | Existing |
| 26 | On-demand expansion | `show <id>` returns the full message | None | Kept by the change (one line) | Existing |
| 27 | Compact claim-state reporting | Held claims are in `status` and the hook's holdings block | Low | Claim lines in the hook: 425 to 600 B | Existing; covered by the format change |
| 28 | Suppress already-delivered messages | See 22 | See 22 | Rejected (above) | Not built |
| 29 | Deterministic status summaries | `status` is already deterministic for a fixed `--now` | None | 697 B text, 1,763 B JSON | Existing |
| 30 | Recovery checkpoints | A resumed session gets the same pickup; `docs/hosts.md` records that a `SessionStart` marker did not survive compaction and that no owed ASK was lost, because the per-prompt hook runs again on the next prompt | A new mechanism with no demonstrated gap | Not measured | Not built |

## What remains

The largest remaining cost is repetition: an owed ASK costs about 405 est. tokens on every prompt until
answered, and an ANSWER or NOTICE keeps appearing for seven days. That is the only structural lever left,
and it needs per-session state; the evidence above says do not do it without a recovery path for a skipped
or lost delivery. Nothing here measures whether less injected text makes agents work better or worse.

Reproduce: `python3 docs/data/context-cost/context_cost.py scenarios <checkout>`, `replay <checkout>
<mailbox dir>`, `tokens <checkout>` (the last one runs `claude -p` three times on your subscription).
