# Inviting the first five developers

Purpose: learn whether five people other than the author can get from clone to a message
picked up by a second session, and where they stall. This is a private ask, not a launch.

## Verdict as of 2026-10-13

Inviting five people by direct message is reasonable once the must-fix items below are done.
A public Show HN or Reddit post is not: it needs the demo recording and the trial results.

## Blockers

Must fix before any invitation (owner-edited files):

1. The v0.5.0 release page is a raw changelog with checksums. It has no opening that says
   what the project is or what to try, and the README's install section still shows
   `verify_release.py v0.4.0`. A visitor arriving from the Releases page learns nothing to do.
2. The README's first screen does not mention what the hook risks. The edit to
   `.claude/settings.json`, and "anyone who can write a file into the mailbox can put text in
   front of your agent", are in SECURITY.md and why-postbox.md but not where a skeptical
   reader looks first. One sentence and a link in the Quickstart is enough.
3. Invitees must be told that Codex has never run in the real host and that real-host
   evidence is headless Claude Code on one small model. The message below does.

Must fix before any public post:

4. The demo recording does not exist. Both drafts depend on it.
5. No trial reports yet. Five is the minimum for a post to say what others found.

Should fix: no pinned "start here / tried it" issue; blank issues are enabled, so "Tried it"
reports arrive unstructured (acceptable); no repository homepage; no CODE_OF_CONDUCT (GitHub
community health 85 percent); Discussions off.

Nice: a "Tried it" issue form; a short GIF in the README.

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
arrive or was ignored goes in as a **field report** issue; everything else as a `Tried it:`
issue, or a private reply the owner transcribes into a table (who, host, OS, furthest step
reached, minutes, stuck point). Record the table in `docs/`, not in chat.

## Success and failure after five trials

Success: at least 4 of 5 reach "message exchanged" without help from the owner; at least 3
of 5 find removal clear; no report of a lost, corrupted or hidden message; and at least 2
who run parallel agents say they would use it again next week.

Failure, needing work before wider sharing: 2 or more stall at the same step (fix that
step); any message lost or silently hidden (stop, fix, tell the others); a hook that blocks
prompts; or nobody running parallel agents sees a use. A null on the last point is a finding
to publish, consistent with the pilot.
