# Launch drafts (not posted)

These are drafts for the owner to edit and post. Every claim in them was checked against the
repository on 2026-10-13 and the experiment wording was updated on 2026-10-15; the results (null, then
against) are stated rather than hidden.

**Do not post yet.** The gate is in [invite-five.md](invite-five.md): the demo recording does
not exist (`<demo link>` below is a placeholder), and five outside developers have not tried
it. A public post is stronger when it can say what N people found.

## Reddit (short)

**Title:** I built a stdlib-only "mailbox" so parallel coding agents (Claude Code / Codex) can
message each other without me copy-pasting

**Body:**

When I run two or three coding agents on one repo, I end up as the messenger: copy a question
out of one session, paste it into another, carry the answer back. Notes files help, but the
failure I kept seeing is agents not *looking*, and an unread inbox looks identical to an empty
one.

agent-postbox is a small tool for that. A message is a Markdown file in a folder in your repo;
the whole tool is one standard-library Python file (no daemon, no DB, no network). For Claude
Code (and experimentally Codex) an installed hook prints waiting mail into the agent's context
before it sees your prompt, so there's no "remember to check" step. Other hosts poll with one
command.

Honest status: it has an extensive test suite and passed scripted checks in real headless
Claude Code sessions (not interactive use), but my experiments did **not** show a benefit: a
small pilot (20 paired instances, a small model) found no advantage over a shared notes file
the agents were told to read, and a larger pre-registered run (60 pairs) came out against it
because the host refused the agents' `send` command in some runs (a Claude Code check on a
`#` line in a quoted argument; the docs now say how to avoid it). Codex support has never run
in the real host. I'm looking for people who've watched agents miss each other's notes to try
it and tell me whether it installs, whether the mail reaches the agent, and where permissions
get in the way. POSIX only. The installer edits
`.claude/settings.json` (backup kept) and there is a tested removal procedure, so try it in a
throwaway project first.

<demo link> · https://github.com/tyko84/agent-postbox

## Show HN

**Title:** Show HN: Agent-postbox – file-based mail between coding agents, with automatic pickup

**Text:**

Hi HN. If you run several coding agents on one repository you become the router between them.
Writing notes to a shared file works until an agent doesn't read it, and then the failure is
invisible.

agent-postbox is a message layer for that. Messages are immutable Markdown files; status is
derived from the files; claims are expiring advisory leases; the tool is one stdlib Python file
with no daemon or database. The part I think is different is pickup: for Claude Code (and
experimentally Codex) a prompt-submit hook injects waiting mail into the agent's context, and
a `doctor` command reports whether the hook is registered, whether its script exists and
whether your identity is set, since silence otherwise looks like an empty inbox.

What I can and can't claim: it passes its own stress, adversarial and headless real-session tests (Claude Code, one small
model), and the demo linked below shows two real Claude Code sessions exchanging a message. I ran two experiments on whether it
improves outcomes: a pilot that was null against a notes-file control, and a pre-registered
second run that came out against Postbox (51 of 60 completed versus 60 of 60), because the
host refused the agent's `send` command in 9 runs. One small model, a restrictive permission
mode, all written up in the repo. No benefit has been demonstrated. I would rather hear that it doesn't help your setup than not hear anything.

It is not an orchestration framework. It doesn't start, plan or supervise agents.
Repo: https://github.com/tyko84/agent-postbox (walkthrough of a first exchange:
docs/onboarding-first-exchange.md) · demo: <demo link>

Questions I'd love answers to: do your agents actually miss each other's notes? What would you
need before trusting it in a real workflow?

## Short caption (for the demo video)

Two real Claude Code sessions exchanging a message through Postbox: automatic pickup by a hook,
an acknowledgement, and code that uses the shared fact. Mechanism demo only, not a benchmark.
