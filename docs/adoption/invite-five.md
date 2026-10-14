# Inviting the first five developers

Purpose: learn whether five people other than the author can get from clone to a message
picked up by a second session, and where they stall. This is a private ask, not a launch.

## Verdict as of 2026-10-14

Inviting five people by direct message is reasonable once the one remaining owner-edited item
below is done. A public Show HN or Reddit post is not: it needs the demo recording and the
trial results.

## Blockers

Done since the first review (2026-10-13): the README's Quickstart now says what the installer
changes, the mailbox-writer risk, how to remove it and that the evidence is thin; the verify
example names v0.5.0; the Quickstart has a first exchange you can paste; a `Tried it` issue form
and [trial-log.md](trial-log.md) exist.

Must fix before any invitation (owner action):

1. The v0.5.0 release page is a raw changelog with checksums and no opening that says what the
   project is or what to try. Add a short summary on top that links
   `docs/onboarding-first-exchange.md`. (Release notes are edited on GitHub by the owner; the
   release itself is immutable but its notes are not.)

The invitation text below already tells invitees that Codex has never run in the real host and
that real-host evidence is headless Claude Code on one small model.

Must fix before any public post:

2. The demo recording does not exist. Both drafts depend on it.
3. No trial reports yet. Five is the minimum for a post to say what others found.

Should fix: no pinned "start here / tried it" issue (the form now exists); blank issues are
enabled; no repository homepage; no CODE_OF_CONDUCT (GitHub community health 85 percent);
Discussions off.

Nice: a short GIF in the README.

## Who to ask (types, not names)

* Two people who run two or more Claude Code sessions on one repository today and complain
  about relaying between them (the intended user).
* One person who runs Codex or Cursor, to learn what happens off the verified path. Tell them
  it is expected to be rougher.
* One skeptical engineer who reviews tools for supply-chain and safety risk, to read
  `install.py` and SECURITY.md and say what worries them.
* One person who has never seen the project, comfortable with git and Python, to measure
  first-use friction.

Do not ask close friends who will be polite, and do not ask anyone to use it on a repository
that matters.

## The message

> I've built a small open-source tool, agent-postbox, that lets coding agents in one repo
> send each other messages and shows incoming mail to the receiving session automatically
> (Claude Code hook; Codex support untested in the real host). I'd like to know whether it
> works for someone other than me. Could you spend 15 minutes on it in a throwaway project?
>
> Honest status: a small pilot of mine did not show it beating a shared notes file, so I am
> asking about usability and failures, not selling it. It is POSIX only (Linux/macOS), the
> installer edits `.claude/settings.json` in the target project (backup kept, removal steps
> documented), and it does not touch anything global.
>
> https://github.com/tyko84/agent-postbox, start at `docs/onboarding-first-exchange.md`.
> Then reply with the four questions from `docs/adoption/feedback-process.md`, or open a
> field report if a message did not arrive. "I got stuck at X" is the most useful answer.

## The 15-minute task

1. Clone and run `examples/two-agents/run.py` (under a second). Note any confusion.
2. In a throwaway git project, run `install.py --adapter claude-code` (or `codex`), then the
   README's first-exchange commands.
3. Run `doctor`. Note anything it says that you did not understand.
4. Optional, with Claude Code: start a session with `AGENT_MAIL_IDENTITY` set, send it mail,
   and see whether it appears at its next prompt.
5. Follow the removal steps in `docs/troubleshooting.md` and say whether the project is back
   to how it was.

## Collecting feedback

Use the four questions in [feedback-process.md](feedback-process.md). A message that failed to
arrive or was ignored goes in as a **field report** issue; everything else as a `Tried it`
issue form, or a private reply the owner transcribes into [trial-log.md](trial-log.md). Record
the table in `docs/`, not in chat.

## Success and failure after five trials

Success: at least 4 of 5 reach "message exchanged" without help from the owner; at least 3
of 5 find removal clear; no report of a lost, corrupted or hidden message; and at least 2
who run parallel agents say they would use it again next week.

Failure, needing work before wider sharing: 2 or more stall at the same step (fix that
step); any message lost or silently hidden (stop, fix, tell the others); a hook that blocks
prompts; or nobody running parallel agents sees a use. A null on the last point is a finding
to publish, consistent with the pilot.
