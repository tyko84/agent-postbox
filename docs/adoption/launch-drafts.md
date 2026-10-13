# Launch drafts (not posted)

These are drafts for the owner to edit and post. Every claim in them is one this repository
can back up; the experiment result is stated rather than hidden. Replace `<demo link>` after
the recording exists.

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

Honest status: tests are extensive and it works in real Claude Code sessions, but a small
pilot I ran (20 paired instances, a small model) did **not** show it beating a shared notes
file the agents were told to read, and Codex support has never run in the real host. I'm
looking for people who've watched agents miss each other's notes to try it and tell me where
it breaks. About five minutes from clone to a first exchange; POSIX only.

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
a `doctor` command tells you if that hook can't run, since silence otherwise looks like an
empty inbox.

What I can and can't claim: it passes its own stress, adversarial and real-session tests, and
I can demo two real Claude Code sessions exchanging a message. I ran a small pre-registered
experiment on whether it improves outcomes and the result was null against a notes-file
control (small N, one small model, known confounds, all written up with the per-run data in
the repo). I would rather hear that it doesn't help your setup than not hear anything.

It is not an orchestration framework. It doesn't start, plan or supervise agents.
Repo and five-minute walkthrough: https://github.com/tyko84/agent-postbox · demo: <demo link>

Questions I'd love answers to: do your agents actually miss each other's notes? What would you
need before trusting it in a real workflow?

## Short caption (for the demo video)

Two real Claude Code sessions exchanging a message through Postbox: automatic pickup by a hook,
an acknowledgement, and code that uses the shared fact. Mechanism demo only, not a benchmark.
