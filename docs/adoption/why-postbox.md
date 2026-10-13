# Why Postbox exists

Postbox is a small communication layer for coding agents that run as separate sessions in one
repository. It is not an orchestration framework: it does not plan, assign, schedule or
supervise anything. It carries messages between agents that someone else is already running,
and it makes sure the receiver actually sees them.

## The problem

When two or three agent sessions share a repository, the person running them becomes the
router. One session learns something the other needs (an endpoint moved, a file is being
rewritten, a decision was made), and the person copies it across by hand.

Agents can write that down themselves in a notes file. The weak point is the *reading*:
an agent that has to remember to look will sometimes not look, and an inbox nobody read
looks exactly like an empty one. In the repository this tool came from, a clear written
instruction to leave notes for the next agent produced no notes in 22 hours (one anecdote, not
a study). Postbox is built around that failure.

## What it does

* A message is one Markdown file in a directory of your repository. Five message types
  (ASK, ANSWER, NOTICE, CLAIM, DISPUTE) with a small fixed shape.
* History is immutable. Status ("open", "answered", "claimed") is derived from the files, not
  stored, so there is no state to corrupt and nothing to repair.
* Pickup is part of the design. For Claude Code, and experimentally Codex, an installed
  adapter prints waiting mail into the agent's context before it sees the prompt, so there
  is no decision to look. Hosts without a hook poll with one command.
* Claims are advisory leases that expire, so a crashed session does not hold a task forever.
* One standard-library Python file. No daemon, no database, no network, no accounts.

## What it deliberately does not do

* No task planning, routing, retries or agent lifecycle. Use whatever runs your agents.
* No network or cross-machine delivery. Local filesystems only.
* No Windows support (untested, unsupported; WSL not tried).
* No guarantee that a model acts on a message. The tool can show that a message arrived
  and was acknowledged; what the model does next is the model's choice.

## What has and has not been shown

Shown, by tests that run in CI and by sessions with a real host:

* Messages are written atomically and are never silently lost or overwritten; concurrent
  sends and claims behave (stress and adversarial tests).
* The hook delivers mail into a real Claude Code session, the receiver can answer, and the
  mailbox records it ([e2e-real-sessions.md](../e2e-real-sessions.md)).

Not shown:

* That Postbox improves a team's outcome. A pilot with real agents (20 paired instances, one
  small model) found **no advantage** over a shared notes file the agents were told to read,
  and the controls were confounded
  ([micro-experiment.md](../micro-experiment.md)). The case Postbox is built for, an agent
  that does not read a shared file unprompted over a long session, was not tested.
* Anything about Codex, Cursor or other hosts beyond what [hosts.md](../hosts.md) says was
  and was not verified.

If the pilot's premise holds for your setup (your agents reliably read a notes file), you may
not need Postbox. If you have watched agents miss each other's notes, it is aimed at you, and
a field report either way is the most useful thing you can send.
