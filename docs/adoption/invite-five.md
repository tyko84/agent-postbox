# Inviting the first five testers

Purpose: learn, from five people other than the author, three things. **Does it install?**
**Does a message reach the receiving agent without anyone telling the agent to look?**
**Where does permission friction hit?** This is a private ask, not a launch. It is not a
test of whether Postbox makes agents better: the project's experiments did not show that
(see below), and five testers cannot.

Nothing in this file has been sent. Sending is the owner's decision and happens outside the
repository.

## What the evidence says, which the invitation must repeat

* The pilot ([micro-experiment.md](../micro-experiment.md), 20 paired instances, one small
  model) found **no advantage** over a shared notes file the agents were told to read.
* The pre-registered second experiment ([experiment-v2-results.md](../experiment-v2-results.md),
  60 paired instances) came out **against** Postbox on its task: 51 of 60 completed against
  60 of 60 for a notes file, because the host refused the agent's `send` command in 9 runs.
  The cause was a `#` line inside a quoted `--body` (a Claude Code check), not the protocol,
  but it was a real failure of the whole arrangement.
* The hook has been run in real Claude Code only in headless sessions, on one small model.
  Codex has never been run in the real host. Cursor is documented, not run.
* So we are **not** asking you to confirm that it helps. We want to know whether it installs,
  whether the pickup reaches the agent, and where permissions get in the way.

## Verdict as of 2026-10-15

Ready to invite five people by direct message once the owner has edited the v0.5.0 release
notes (blocker 1). A public Show HN or Reddit post is not appropriate: see
[launch-drafts.md](launch-drafts.md).

### Blockers

Must fix before any invitation (owner action):

1. The v0.5.0 release page is a raw changelog with checksums and no opening that says what the
   project is or what to try. Add a short summary on top that links
   `docs/onboarding-first-exchange.md` and says the experiments did not show an advantage.
   (Release notes are edited on GitHub by the owner; the release itself is immutable but its
   notes are not.)
2. Create the `tried-it` label, or the issue form cannot apply it (see below).
3. Decide the support channel (issues only, or also a private reply address) and put it in the
   invitation. Do not use an address in the repository.

Must fix before any public post:

4. The demo recording does not exist. Both drafts depend on it.
5. No tester reports yet. Five is the minimum for a post to say what others found.

Should fix: no pinned "start here" issue; no repository homepage; no CODE_OF_CONDUCT;
Discussions off.

### The `tried-it` label

The `Tried it` issue form (`.github/ISSUE_TEMPLATE/tried-it.yml`) applies the label `tried-it`,
which does not exist yet; GitHub drops a label that does not exist. Create it once, as the
repository owner (this has not been run):

```sh
gh label create tried-it -R tyko84/agent-postbox --color 0e8a16 \
  --description "A first-use report from someone who tried agent-postbox"
```

## The five tester profiles

Types, not names. Do not ask close friends who will be polite, and do not ask anyone to use it
on a repository that matters.

| ID | Profile | Environment | What they teach us |
|---|---|---|---|
| T1 | Runs two or more Claude Code sessions on one repository today and complains about relaying between them | macOS or Linux, Claude Code, interactive | The intended user: does pickup reach the agent in interactive use, which we have not run |
| T2 | Same as T1, with a stricter permission setup (default mode with prompts, or `dontAsk` with allow rules) | macOS or Linux, Claude Code | Where permission friction hits: the `send_permission:` line of `doctor`, denied commands |
| T3 | Runs Codex CLI (or Cursor) | macOS or Linux, Codex or Cursor | What happens off the verified path; expected to be rougher. Codex has never run the hook in the real host |
| T4 | A skeptical engineer who reviews tools for supply-chain and safety risk | any POSIX | Reads `install.py`, `SECURITY.md` and the hook, and says what worries them |
| T5 | Someone who has never seen the project, comfortable with git and Python | any POSIX | Measures first-use friction in the documentation |

## Supported environments

* Linux or macOS, Python 3.10 to 3.14, git. **Windows and WSL are unsupported.**
* Claude Code: the adapter is tested headless (2.1.300). Interactive mode has not been run.
* Codex: experimental; the hook must be trusted once in `/hooks`.
* Cursor, anything else: polling only (`list --live`).
* A throwaway git project, never one that matters. The installer edits `.claude/settings.json`
  (or `.codex/hooks.json`) in that project only, keeps a `.bak`, and touches nothing global.
  Removal is in [troubleshooting.md](../troubleshooting.md#removing-it).

## The message

> I have a small open-source tool, agent-postbox, that lets coding agents in one repository
> send each other messages (Markdown files) and, with a Claude Code hook, shows incoming mail
> to the receiving session at its next prompt. I would like to know whether it works for
> someone other than me. Could you try it in a throwaway project and tell me where it broke?
>
> Please read this first: my own experiments did **not** show that it makes agents better.
> A small pilot found no advantage over a shared notes file the agents were told to read, and
> a larger pre-registered experiment came out against it (the host refused the agents' `send`
> command in some runs). I am not asking you to confirm a benefit. I want to learn three
> things: does it install, does a message reach the other agent's context, and where do
> permission prompts or denials get in the way.
>
> It is POSIX only (Linux/macOS). The installer edits `.claude/settings.json` in the target
> project (backup kept, removal steps documented) and nothing global. Codex support has never
> been run in the real host, and the Claude Code hook has only been run headless.
>
> https://github.com/tyko84/agent-postbox. Start at `docs/onboarding-first-exchange.md` and
> follow `docs/adoption/invite-five.md` ("The first-use task"). A reply of "I got stuck at X"
> is the most useful answer; the `Tried it` issue form takes it, or reply to me directly.

## The first-use task

Budget about 30 minutes. Time yourself where the log below asks; these timings are your own
wall-clock, which is exactly what we have not measured.

1. Clone and run `python3 agent-postbox/examples/two-agents/run.py`. Note any confusion.
2. In a throwaway git project, run `install.py --adapter claude-code` (or `codex`), then the
   README's first-exchange commands. **Note the time of the first message you sent (T-send).**
3. Run `doctor`. Note anything it said that you did not understand, and the
   `send_permission:` line if present.
4. With your agent host: start two sessions with different `AGENT_MAIL_IDENTITY` values, ask
   one to send the other a question, and see whether the other shows it at its next prompt
   without being told to look. **Note whether the pickup reached the agent (yes, no, only
   after you told it to look).**
5. Note every permission prompt or denial on a mail command: what the command was, what you
   chose, and what the agent did next.
6. Follow the removal steps and say whether the project is back to how it was.

### What we measure

| Measure | Definition | Where it comes from |
|---|---|---|
| Install success | `install.py` exits 0 and `doctor` has no FAIL other than the missing canary | tester report |
| Time to first message | minutes from the clone to the first message accepted by `send` (human wall-clock) | tester report. The machine time of the scripted example, about a second, is not this |
| Pickup reached the agent | the receiving session showed the mail without being told to look: yes, no, only when told | tester report, optionally `doctor` output |
| Permission friction | count of permission prompts or denials on mail commands, and what resolved each | tester report |
| Removal clear | the project is back to its previous state | tester report |

## Collecting feedback

Use the four questions and the summary format in [feedback-process.md](feedback-process.md).
A message that failed to arrive or was ignored goes in as a **field report**; everything else
as a `Tried it` issue, or a private reply the owner transcribes into
[trial-log.md](trial-log.md). Record the table in `docs/`, not in chat.

## Success and failure after five trials

Fixed now, before the first reply, and not changed afterwards.

Success (of the testers who tried; report with the denominator):

* at least 4 of 5 reach "a message exchanged" without help from the owner;
* at least 3 of 5 find removal clear;
* in every host run, the pickup either reached the agent or the failure is explained and
  filed;
* no report of a lost, corrupted or hidden message.

Failure, needing work before wider sharing: 2 or more stall at the same step (fix that step);
any message lost or silently hidden (stop, fix, tell the others); a hook that blocks prompts;
two or more permission denials with the same cause (fix the docs or the adapter before
anything else).

Neither outcome says anything about whether Postbox helps agents work. A tester saying "I
would not use this" is a finding to publish, like the experiments' null and negative results.
