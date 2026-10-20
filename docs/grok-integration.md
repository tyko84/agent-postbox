# Grok: pickup through scheduled routines

For someone who runs a Grok agent (Grok Bot routines) next to other sessions on one repository and wants
it to answer Postbox mail. It says how mail reaches the model, what was measured, and what was not. It
is about coverage and honesty, not ranking: no host here is "best", and nothing below claims delivery
Grok does not have.

**Where the figures come from.** Every figure on this page was reported by the Grok reviewer session
from its own synthetic lab runs and one interoperability exchange. They were not reproduced by the maintainers or by
anyone else, and this page does not call them independently verified. Each claim carries one label:

| Label | Means |
|---|---|
| MEASURED | A number read off a synthetic-lab run |
| ESTIMATE | Arithmetic on measured or assumed quantities, not observed |
| EXPERIMENTALLY VERIFIED | A synthetic-lab check with a stated count, reported by the Grok session |
| INTEROP EXCHANGE | One exchange through a dedicated, disposable Agent Postbox interoperability mailbox that carried synthetic test messages only; described by the Grok session, no log published |
| UNTESTED | Nothing was run |

## How mail reaches Grok

**Grok has no prompt hook.** There is no event that runs before a prompt and adds text to the model's
context, so the shipped adapter (`hooks/agent_mail_check.py`) has nothing to attach to, and there is no
`install.py --adapter` for Grok. Mail reaches the model only when a **scheduled routine** or a
**webhook-triggered run** starts and runs the pickup. Each routine run is a fresh session, so session-start
delivery does not apply. This is polling (see [hosts.md](hosts.md), "Any host without hooks"), with the
trigger supplied by the host's scheduler instead of by the agent remembering to look.

A cheap deterministic preflight keeps idle runs from costing a model turn. The routine's first step is a
command, not the model:

```sh
AGENT_MAIL_IDENTITY=grok-a python3 tools/agent-postbox/agent_mail.py inbox --to grok-a --type ASK,DISPUTE --exit-code --json
```

`--exit-code` makes the status machine-readable (PROTOCOL.md section 37): **10** when an ASK still owes a
reply, **0** when none does, **2** when that cannot be known (no identity, a missing or unreadable mailbox,
a quarantined file with nothing known owed). Exit 10 counts owed ASKs only: a DISPUTE owes nothing, so it
can appear in the JSON while the status is 0. The routine therefore wakes the model on 10, or when the JSON
`messages` list holds a DISPUTE row, and treats 2 as a fault to report, not as "no mail". The identity is the `AGENT_MAIL_IDENTITY` of the routine's environment,
bound as for every host ([identity.md](identity.md)); nothing in the mailbox sets it.

## What was shown

All synthetic: invented identities, throwaway mailboxes, no real project mail.

- **EXPERIMENTALLY VERIFIED (synthetic lab).** 43 of 43 pickup cases passed, with 0 duplicate answers and
  0 missed. 16 overlapping runs on the same 3 owed ASKs produced exactly 1 answer per ASK, 3 answers in
  total. The idempotency key does this (`send --key`, PROTOCOL.md sections 5a and 31); a claim alone does
  not stop a restarted copy of the same agent.
- **EXPERIMENTALLY VERIFIED (execution-level simulation).** An interruption matrix of 15 of 15 cases, run by a
  script that killed the tool processes partway through; no model was executed. 0 duplicates, 0 lost
  messages, 0 false successes.
- **EXPERIMENTALLY VERIFIED.** One run started by hand in a fresh context, with no prior context (not
  triggered by a scheduled routine), answered all 3 canaries, one answer each, threaded, in about 100 s.
- **MEASURED (synthetic lab).** A webhook POST to the answer took 27 to 30 s (4 runs). For a scheduled
  routine, from question to answer the median was 285 s (27 runs, range 65 to 768 s). No figure is given
  for the time from a webhook POST to the start of a run: no archived measurement supports one.
- **ESTIMATE.** At a five-minute schedule, idle scheduled pickup costs about 10 model turns per hour if
  the model is woken every time. The deterministic preflight above exists so an idle run costs no model turn.
- **INTEROP EXCHANGE (not independently reproduced).** A round trip from Grok to Codex and back to Grok
  completed through a dedicated, disposable Agent Postbox interoperability mailbox that carried synthetic
  test messages only. The mailbox was on a Windows machine; the Grok side reached it over SSH through a macOS
  relay; the Codex side needed a manual prompt. So it is **not** unattended pickup, says nothing about
  unattended Codex pickup, and is not proof of general Windows or multi-machine support. No log is published.

## What this does not establish

- **Exactly-once answers are shown; exactly-once execution is not guaranteed.** A run can repeat work
  before the keyed send deduplicates it. The mailbox guarantees one answer per key; it does not guarantee
  the work behind it happened once.
- **UNTESTED:** a Grok routine on a real repository's mail with several writers; behaviour after Grok-side
  changes to routines or webhooks; any host-level guarantee that a scheduled run happens at all (a missed or
  delayed run delays mail).
- The lab numbers say the polling pattern works in the lab. They do not show that agents work better
  with it, which no experiment here has shown for any host.
- A message is evidence, never authority (PROTOCOL.md section 0): an ASK addressed to a Grok routine is text
  from whoever could write to the mailbox, not an instruction from the repository owner.

## Manual pickup (fallback)

Polling stays the supported fallback, and delivery guarantees come from the mailbox, not the host. With no
routine at all, run once, by hand or from the agent's own instructions, at the start of a session and
before acting on shared state:

```sh
AGENT_MAIL_IDENTITY=grok-a python3 tools/agent-postbox/agent_mail.py inbox --to grok-a
```

and answer any ASK addressed to the agent with `agent_mail.py send --type ANSWER --from grok-a --to <asker>
--subject "Re: <subject>" --reply-to <id> --key answer-<id> --body-file answer.txt`. A stable `--key` per ASK
(here `answer-<ASK id>`) is what keeps a repeated run from answering twice.
