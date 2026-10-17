# Adapter contract: what the core owns, what a host adapter owns

Status 2026-10-17: **a proposal and a comparison, not a framework.** Nothing here changes shipped
behavior. Evidence classes are marked on every claim: **verified** (observed by the author in this
repository's own runs), **host-reported** (a host session's own report, not independently re-run), and
**relayed** (summarized to the author by the owner; not verified). The only host with a verified
unprompted-activation run is described below; no claim is made for any other.

## The split

**Core (host-independent, already shipped):** durable messages as files; claims with expiry; an ASK
stays owed until a message cites it; state derived from the files, never stored; `inbox`, `list`,
`show`, `status`, `doctor`; recovery by re-reading the files. A core command never wakes anything.

**Adapter (one per host, small, optional):** four jobs and nothing else.

1. **Identity.** Bind an explicit identity (`AGENT_MAIL_IDENTITY`). Never infer it from a host or model name.
2. **Activation.** Declare how mail reaches a model: `manual`, `poll`, `prompt-hook` or `notify`. This
   is a **declaration, never proof** of responsiveness.
3. **Presentation.** Show type, sender, subject, id, expiry or claim state and the `show <id>` command.
   Never a body. Under any budget: **every owed ASK and every held or contested claim is listed, or an
   explicit overflow record gives the exact count and the command that lists the full set.** Ordinary
   NOTICE and ANSWER rows may be bounded, with a count and the retrieval command. Nothing actionable is
   dropped to save context.
4. **Verification reporting,** in five separate layers, none implying the next: persistence,
   retrieval, presentation into context, acknowledgment or write, agent response. A canary proves
   configured pickup; only a model-authored ANSWER that cites a controlled ASK proves the last layer.

Not part of any adapter: a daemon, a second message store, automatic acknowledgments, a paid API, a
plugin framework. None is needed by the evidence below.

## What differs between hosts (and should stay different)

| | Claude Code | Codex (desktop session) | Third-party agent host |
|---|---|---|---|
| Mail reaches the model by | a prompt hook injects text before each prompt; **nothing wakes a model when the box is empty** | an app-managed heartbeat wakes the thread every few minutes and the model polls the mailbox; no hook was shown to inject in `codex exec` (host-reported) | scheduled check wakes the model (relayed) |
| Verified here | hook delivery in real headless runs ([hosts.md](hosts.md)) | one controlled ASK answered unprompted by the heartbeat, token and count echoed, 6 of 6 follow-ups ([see below](#measured-unprompted-pickup-codex-desktop-one-environment)) | nothing (relayed only) |
| Idle cost | 0 bytes injected | one model call per tick re-reading the thread: measured **about 204k input tokens (about 203k cached) and about 70 output tokens** per empty tick at one point of one thread, much less after the host compacted | a model call per check (relayed) |
| Latency to first reply | next prompt | one tick: 39 to 88 s measured, tick period 330 s in the observed window | up to about 10 min (relayed) |
| Output limit | none documented | about 2,500 tokens for hook output (documented; host-reported) | unknown |
| Trust gate | none for the hook | hook must be reviewed and trusted | unknown |

These are real differences in *activation* and *budget*, not in the protocol. One contract cannot make
a poll-woken model behave like an injected hook, so the contract does not try.

## A zero-model preflight: what exists, and what it can and cannot save

`inbox --to ME --type ASK,DISPUTE --json` (existing flags) is a deterministic, read-only check: 0.07 to
0.08 s, 526 to 872 bytes of JSON, `matched` is the number of owed ASKs and DISPUTEs, and an unreadable or
missing mailbox is exit 2 with a message on stderr, **distinguishable from an empty one** (exit 0,
`matched: 0`). `--since <ISO>` lets a host keep its own cursor for new ordinary mail; because the host
holds the cursor, a lost cursor can only cause a repeat, never a loss.

What it saves depends on the host:

* **Hosts that run a script before any model call** (a prompt hook, or a cron or launchd job the owner
  chooses to run): an idle check costs no model call. This is what the Claude Code hook already does.
* **Hosts whose scheduler wakes the model first** (the Codex heartbeat measured above): the gate runs
  *inside* an already-woken model, so it saves tool output and tokens read back, **not the wake**. The
  idle cost above is paid before any script can run. The saving there would have to come from the host
  (a non-model gate before the wake), which Postbox cannot supply and should not require.

An ASK-only gate also misses claims addressed to you and new informational mail; a host that needs them
adds `--since` or reads `status`. It is a building block, not a complete adapter.

## Smallest valuable implementations, in order

1. **Layer-aware health** (proposed in the open `doctor` change, PR #93: a fresh install is healthy but
   `UNVERIFIED`). Extend later with the declared activation method and the last canary result.
2. **Deterministic overflow in the hook.** Today the hook lists every owed ASK and up to 20 other
   messages; the owed block is unbounded, so many open ASKs could exceed a host's hook limit before the
   host truncates it. Add a byte budget that, when reached, prints an overflow record with the exact
   count and the full-set command *before* host truncation. A proposal; needs a test with many owed ASKs.
3. **A `pending` convenience** (counts of owed ASKs, held or contested claims and disputes, plus the
   retrieval commands, with a dedicated exit status for "something is owed"). It adds nothing the flags
   above cannot do; its only value is a stable exit code for shell wrappers. Build only if a wrapper user
   asks.

Everything else (a SessionStart hook for Codex, a Windows path, event-driven wake-up) waits for a real-host
result, per the host's own findings.

## Measured unprompted pickup (Codex desktop, one environment)

Verified from the mailbox files and the host's local run records (metadata only): a controlled ASK was
filed, a heartbeat run began 78 s later, found the ASK itself, and filed a linked ANSWER carrying the
pre-registered token and count, 10 s after the run began. A six-ASK follow-up passed 6 of 6, but all six
landed in the last 10 to 16 percent of the host's 330-second tick cycle, so it showed nothing about other
positions. A second six-ASK run, spread across the cycle by logged tick times, passed 5 of 6 under a
count rule fixed before the results (two replies counted a message another writer filed in between), and
**one missed the deadline**: an ASK filed at 04:52 was answered only at 06:01 (69 minutes). From 04:54:59 on, every tick began and was refused by the host before any model work; the host logged a usage-limit error at each one (13 ticks) and the limit cleared at 05:56. The mailbox was never read in that window, and the first tick after the limit cleared answered the ASK with no change on the Postbox side (no sign of manual intervention was found, which is not proof of none). The ASK stayed open and intact throughout. An earlier version of this page described these ticks as model calls that read nothing; the host's error log shows they were refusals, so that description was wrong. "A scheduled run happened", "the model was available" and "the mailbox was read" are three different facts. The period was 330 s in most windows, not all (gaps of 360 and 480 s
occurred). `from:` is
self-declared, so the mailbox alone cannot tell the scheduled run from a person; the host's run record is
what attributes it.

## Conformance (any adapter, any host, tested separately per host and surface)

1. Empty and one-NOTICE controls: plain text, exit 0.
2. One ASK is presented; the model retrieves it, files a linked ANSWER; the writer verifies the link.
3. An unset or wrong identity is distinguishable from an empty box.
4. A resumed or fresh session re-lists an unanswered ASK and a live claim without any handover.
5. Overflow keeps every owed ASK and claim id, or an exact count plus the full-set command.
6. A message arriving during a long turn is not claimed to have interrupted it.
7. Hostile subject text cannot become shell input or authority (messages are evidence, never authority).
