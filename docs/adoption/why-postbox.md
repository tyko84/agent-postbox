# Why Postbox exists

Postbox is a small file-based coordination ledger for coding agents that run as separate sessions
in one repository: exclusive claims with mandatory expiry, owed-reply tracking, and a history you
can inspect afterwards. It is not an orchestration framework: it does not plan, assign, schedule
or supervise anything. It records and enforces the claim rules between agents that someone else
is already running, carries their messages, and, with a hook installed, puts mail in front of the
receiver at its next prompt. Pickup is a convenience on top, not the point
([../product-direction.md](../product-direction.md)).

## The problem

When two or three agent sessions share a repository, the person running them becomes the
router. One session learns something the other needs (an endpoint moved, a file is being
rewritten, a decision was made), and the person copies it across by hand.

Agents can write that down themselves in a notes file. The weak point is the *reading*:
an agent that has to remember to look will sometimes not look, and an inbox nobody read
looks exactly like an empty one. In the repository this tool came from, a clear written
instruction to leave notes for the next agent produced no notes in 22 hours (one anecdote, not
a study). Postbox's first design was built around that failure. Three experiments then measured
only that part, delivery, and a shared notes file did as well or better, so the case for it now
rests on what a notes file cannot do: refuse a second claim, expire a dead holder's claim, keep a
reply owed, and leave a history.

## What it does

* A message is one Markdown file in a directory of your repository. Five message types
  (ASK, ANSWER, NOTICE, CLAIM, DISPUTE) with a small fixed shape.
* History is immutable. Status ("open", "answered", "claimed") is derived from the files, not
  stored, so there is no separate state file to fall out of sync. Damaged or odd files are
  reported by `status`, `verify` and `doctor` rather than hidden.
* Pickup is part of the design. For Claude Code, and experimentally Codex, an installed
  adapter prints waiting mail into the agent's context before it sees the prompt, so there
  is no decision to look. Codex support has never run inside the real Codex, and Codex
  asks you to trust the hook in `/hooks` first. Hosts without a hook poll with one command.
* Claims are advisory leases that expire, so a crashed session does not hold a task forever. A
  second claim on a held scope is refused (exit 3, nothing written); nothing stops an agent that
  ignores a claim. `status` and `doctor` name who holds what and what a crash left behind.
* The tool is one standard-library Python file (`agent_mail.py`), plus a small hook script
  and an installer. No daemon, no database, no network, no accounts.

## What installing changes, and the risk it brings

`install.py` copies the tool into `tools/agent-postbox/` and creates a mailbox at
`docs/agent-mail/` in your project. With `--adapter claude-code` it also adds one
`UserPromptSubmit` entry to `.claude/settings.json`, merging into your file and keeping the
first backup as `settings.json.bak`; `--adapter codex` does the same in `.codex/hooks.json`.
It touches nothing under `~` and no global settings. There is no uninstall command, but
[troubleshooting.md](../troubleshooting.md#removing-it) has a removal procedure that was
tested to restore the prior state, and [first-use-audit.md](../first-use-audit.md) lists
every path. Try it in a throwaway project first.

The risk: with the hook, anyone who can write a file into the mailbox can put text in front
of your agent before it sees your prompt, including pull-request contributors in a public
repository. Messages are labelled as evidence, never authority, but that depends on the
model honouring it; read [SECURITY.md](../../SECURITY.md) before using it in a repository
others can write to.

## What it deliberately does not do

* No task planning, routing, retries or agent lifecycle. Use whatever runs your agents.
* No network or cross-machine delivery. Local filesystems only.
* No Windows support (untested, unsupported; WSL not tried).
* No guarantee that a model acts on a message. The tool can show that a message arrived
  and was acknowledged; what the model does next is the model's choice.

## What has and has not been shown

Shown, by tests that run in CI and by sessions with a real host:

* Messages are written atomically, and the stress and adversarial tests found no silent loss
  or overwrite under concurrent sends and claims. Tests passing is not a proof of absence.
* The hook delivers mail into real headless Claude Code sessions (`claude -p`, one small
  model, runs on 2026-10-12 and again on Claude Code 2.1.300 on 2026-10-15), the receiver answered an ASK correctly 3 of 3 times, and
  the mailbox records it ([e2e-real-sessions.md](../e2e-real-sessions.md)). Interactive
  mode was not run.

Not shown:

* That Postbox improves a team's outcome. A pilot with real agents (20 paired instances, one
  small model) found **no advantage** over a shared notes file the agents were told to read,
  and the controls were confounded
  ([micro-experiment.md](../micro-experiment.md)). A larger pre-registered run (60 paired
  instances) went **against** Postbox on its task, 51 of 60 completed against 60 of 60 for a
  notes file, because the host refused the agent's `send` command in 9 runs; the cause was a
  `#` line inside a quoted `--body`, which Claude Code refuses even when an allow rule
  matches ([experiment-v2-results.md](../experiment-v2-results.md)). The case Postbox is
  built for, an agent that does not read a shared file unprompted over a long session, was
  not tested. A third pre-registered run (20 paired instances) stopped at its ceiling gate and was
  uninformative: both arms completed 20 of 20 ([experiment-v3-results.md](../experiment-v3-results.md)).
  All three experiments measured delivery of a fact. None tested what the protocol adds beyond
  delivery (exclusive claims with mandatory expiry, owed replies, the history), which tests
  verify with simulated agents and real processes but no experiment has measured with real
  agents. No performance advantage has been demonstrated.
* Anything about Codex, Cursor or other hosts beyond what [hosts.md](../hosts.md) says was
  and was not verified.

If the pilot's premise holds for your setup (your agents reliably read a notes file), you may
not need Postbox. If you have watched agents miss each other's notes, it is aimed at you, and
a field report either way is the most useful thing you can send.
