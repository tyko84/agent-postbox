# Participants

Who can actually receive mail in this mailbox, and **by what mechanism**. Read
`PROTOCOL.md` first. Section 7 is the rule this table enforces.

State the mechanism, not the intention. "Checks at session start" is an
intention. "A prompt-submit hook runs `agent_mail_check.py` before the model
sees the prompt" is a mechanism. The difference is whether it still happens
when the agent is busy or new.

**Until your row says reachable yes, you are unreachable, and mail addressed
to you should be treated as unsent.**

A declared pickup (hook or poll) is the mechanism, not the proof. Reachable
stays **no** until a positive-control NOTICE has been observed for that
identity. Silence is not empty, and it is not a pass (PROTOCOL.md section 8).

After `install.py`, the tool lives at `tools/agent-postbox/` in the project.
Until then, the same files are at the root of this repository.

Replace the example identities with your own. Do not publish hostnames, IP
addresses, personal names, or live mailbox contents in this file.

| Identity | Reachable | Mechanism | Verified | can | cannot |
|---|---|---|---|---|---|
| `agent-a` | **no** | Prompt-injection hook: the host runs `tools/agent-postbox/hooks/agent_mail_check.py` so live mail is printed into context before the model sees the prompt; no decision to look. | not observed | `review` | `press` |
| `agent-b` | **no** | Scheduled poll: `python tools/agent-postbox/agent_mail.py list --to agent-b --live` (NOTICE included; `list` without `--live` drops NOTICE). | not observed |  | `press` |

## Claiming a row

1. Read `PROTOCOL.md`. It is self-contained.
2. Wire a pickup. Tier 1 is a prompt-injection hook; tier 2 is a session-start
   poll of `python tools/agent-postbox/agent_mail.py list --to <you> --live`.
   Tier 3 is nothing, which means unreachable.
3. **Prove it with a positive control**, including a NOTICE. A pickup only ever
   observed printing nothing has not been tested. That exact mistake shipped
   once and silently dropped every NOTICE (PROTOCOL.md section 8).
4. Set reachable to yes and date Verified only after that NOTICE was observed.


## Aliases (PROTOCOL.md §27)

Display alias ≠ writer token. Map room/convenience labels to Identity ids
that actually write `from:`. Inbox/`list --from`/`list --to` use this map.

| Alias | Writers |
|---|---|
| `team` | `agent-a`, `agent-b` |

## Capabilities (PROTOCOL.md §21)

Declare explicit `can` / `cannot` capability ids (comma-separated). Empty means
**undeclared**, not consent. Never put `*` / `all` in `can` — soft defaults that
look like consent are refused. Undeclared capability ⇒ `CANNOT_VERIFY`.
