# Recording the demos

Two recordings are possible, and they must not be confused:

* **Part 1, real sessions** (`live_demo.py`): two actual Claude Code sessions
  in two terminals. What is on screen is what the host and the model did.
* **Part 2, scripted** (`run.py`): two scripted "agents", no model, no host.
  It is labelled as scripted and must stay labelled that way. Never present
  it as Claude Code behaviour.

# Part 1: two real Claude Code sessions (60 to 90 seconds)

What this shows: the mechanism works. One session sends a message, the other
is handed it automatically by a hook, acknowledges it, and writes code that
uses the fact in it. What it does NOT show: that Postbox gets better outcomes
than a shared notes file. A small pilot (`docs/micro-experiment.md`) found no
advantage for Postbox over a notes file with haiku. Do not say "better",
"faster" or "more reliable" in the video or the caption.

What is scripted and what is real:

| Part | Status |
|---|---|
| The project, the secret URL, the allow-list, the prompts you paste | scripted (prepared by `setup`, printed in full) |
| The hook output, the files in `docs/agent-mail/`, `client.py`, the `check` verdicts | real (made by the tool, the host and the model) |
| Whether beta acts on the mail, and how | the model's choice; it can fail (see the table of runs) |

## What has and has not been tested

Checked by the author of this script (2026-10-13, claude 2.1.296, macOS):
every command below that needs no model (setup, the install, the hook run by
hand, `check` on an empty, a half-built and a complete mailbox), and that the
`claude` flags used here exist. Headless rehearsals (`claude -p`) passed all
six beats in 5 of 5 runs after the last wording change.

NOT tested, because it needs an interactive terminal and a model:

* the workspace trust dialog (it is skipped in `-p`, so no rehearsal saw it);
* whether the interactive screen shows the hook's `Agent mail` block, or only
  a one-line "hook ran" note (the model gets the text either way, but you may
  have to look in the expanded view; see step 9);
* whether any permission dialog appears;
* whether `check` finds the hook output in a real interactive transcript
  (beat 4; a hand-made transcript line was found, a real one never has);
* `/exit` and the other keys named below.

Where this script says UNVERIFIED, that is the list above. If what you see
differs, stop and write down what you saw (a phone photo or screenshot is
enough). That is a result for the repo, not something to work around on
camera.

## The story

A project has `client.py`, whose `PAYMENTS_BASE` points at `.../v1/ledger`.
Agent **alpha** can run `python3 discover.py`, which reads a service registry
that only alpha's terminal can see (an environment variable set there; the
file is outside the project). It finds that the live URL is now
`.../v7-<random>/ledger`, a value nobody can guess. Alpha tells agent **beta**
through Postbox. Beta, asked to add `refund_url(charge_id)` to `client.py`,
has no other way to learn the new URL. The six beats `check` judges:

1. two sessions, two identities (alpha, beta);
2. alpha discovered the fact and put it in a message;
3. the message went through Postbox (an ASK file in `docs/agent-mail/`);
4. the hook handed it to beta at beta's next prompt (nobody polled);
5. beta acknowledged it with an ANSWER citing the ASK;
6. beta's code uses the new URL.

## The script

Use macOS Terminal (or iTerm). Two windows side by side: LEFT is alpha, RIGHT
is beta. No tmux needed. Wherever a line says "paste", paste it into the
window named and press Return. Lines starting with `$` are what you type
(without the `$`).

### A. Once: prerequisites (off camera, 5 minutes)

**Step 1. Check the tools.** In any Terminal window:

```sh
claude --version
claude auth status
python3 --version
```

Expected: a version line such as `2.1.296 (Claude Code)` (yours may be newer);
JSON containing `"loggedIn": true`; `Python 3.10.x` or newer (the repo
requires 3.10; the tool is stdlib only). If `claude` is not found or not
signed in, run `claude` once and sign in. If `python3` prints "You have not
agreed to the Xcode license agreements", run `sudo xcodebuild -license`, or
use a Homebrew Python (`brew install python`) and re-open Terminal.

**Step 2. Get the code and go to it.** Pick a location whose path you are
content to show (the header printed by some commands names it):

```sh
cd ~/agent-postbox
git pull
export CLONE="$PWD"
```

Adjust the `cd` to wherever the clone is. `CLONE` is only used in the LEFT
window at the end; set it again there if you open a new window.

**Step 3. Make the demo project.** One command builds a throwaway project
under `/tmp/postbox-demo` and prints everything below with your real paths:

```sh
python3 "$CLONE/examples/two-agents/live_demo.py" setup --dir /tmp/postbox-demo
```

Expected: it begins `LIVE DEMO READY (real Claude Code sessions; nothing here
is scripted)` and prints `project  : /private/tmp/postbox-demo/project`. The
`/private` is macOS resolving `/tmp`; use the paths it prints. Nothing in
`/tmp/postbox-demo` is precious: `setup` deletes and rebuilds it (it refuses
to delete a directory it did not create).

Leave that output on screen, or scroll back to it. It contains the exact
`cd` and `export` lines for both windows and the three prompts.

**Step 4. Look at the files the hook will use (no model).** In the RIGHT
window:

```sh
cd /private/tmp/postbox-demo/project
cat .claude/settings.json
export AGENT_MAIL_IDENTITY=beta
echo '{}' | CLAUDE_PROJECT_DIR="$PWD" python3 tools/agent-postbox/hooks/agent_mail_check.py; echo "exit $?"
```

Expected: `settings.json` shows a `UserPromptSubmit` hook running
`agent_mail_check.py`; then the line `exit 0` and nothing above it, because the
mailbox is empty and the hook is silent when there is nothing to say. (With
mail present it prints the `Agent mail - 1 live message(s) addressed to
'beta'.` block; that was confirmed by hand for this script on a copy.) Do not
send any mail now: the mailbox must be empty for the take.

Close nothing; go on to the dry run.

### B. Dry run (do this first, unrecorded)

**Do one complete unrecorded run of section C, end to end, including
`check`, before you ever press record.** It is the only way to find out what
the UNVERIFIED items above do on your machine. Expect to see a trust dialog
and possibly other first-run screens on this pass. Make notes of anything
unexpected. Then do step 12 (reset) and run section C again for the take.

### C. The run (record from step 8)

**Step 5. LEFT window (alpha).** Paste these four lines, from the `setup`
output ("TERMINAL 1"), with your real paths. Shape:

```sh
cd /private/tmp/postbox-demo/project
export AGENT_MAIL_IDENTITY=alpha
export PAYMENTS_REGISTRY=/private/tmp/postbox-demo/registry/payments.json
claude --model haiku
```

**Step 6. RIGHT window (beta).** If you did step 4 in this window, it already
has `AGENT_MAIL_IDENTITY=beta`; pasting again does no harm. Beta must NOT
have `PAYMENTS_REGISTRY`; a fresh window is safest:

```sh
cd /private/tmp/postbox-demo/project
export AGENT_MAIL_IDENTITY=beta
claude --model haiku
```

**Trust dialog (UNVERIFIED).** The first time `claude` starts in this folder
it should ask whether you trust the folder. The project's hook is project
configuration, which the host only honours after you trust the folder. Choose
the option that says yes / trust / proceed (Return on the highlighted "Yes"
is the usual default). Wording and layout may differ; read it. If it asks
about anything else (hooks, MCP servers, a theme), read it and decline
anything you do not understand. It should not ask again in this folder, even
after a reset, because `setup` recreates the same path (also UNVERIFIED).

**Permission dialogs (UNVERIFIED).** `setup` pre-approves only reading and
editing files, `python3 discover.py` and the mailbox tool, in
`.claude/settings.local.json`, so none should appear. If one does, on the
dry run: read the command shown, choose "no" (not "always"), press
Esc, and note the command. That tells us the allow list is incomplete. On
the take, see "If something goes wrong".

Both windows now show an empty `claude` prompt. Quiet things down first:
Do Not Disturb on, notification apps closed, font large enough to read at
half screen width (about 110 columns per window), window titles hidden, and,
before running `claude`, `PROMPT='$ '` in each window so the shell prompt is
short.

**Step 7. Prompts.** Do not retype them. Either copy from the `setup`
output, or put one on the clipboard from a third Terminal window with
`pbcopy < /private/tmp/postbox-demo/prompts/B1.txt` (then `A.txt`, `B2.txt`)
and Cmd-V into the session. The files hold the same text as the printed
prompts, with no trailing newline, so press Return yourself.

**Step 8. Start the screen recorder** (Cmd-Shift-5 on macOS, select the
region covering both windows, Record). Both sessions are open at an empty
prompt.

**Step 9. The take.** Wait for each model to finish (no spinner, prompt back)
before the next paste; haiku takes 10 to 25 seconds per prompt.

| Time | Window | You do | On screen (what to expect) | Say (`docs/demo-narration.md`) |
|---|---|---|---|---|
| 0:00 | both | nothing | two `claude` sessions, one folder | "Two separate Claude Code sessions in one project. They share only a folder of files." |
| 0:08 | RIGHT | paste **B1**, Return | one line about `client.py`; no `Agent mail` block (mailbox empty). It may ask nothing; reading a file is pre-approved | "Beta reads the client. It points at the old v1 ledger. No mail yet." |
| 0:16 | LEFT | paste **A**, Return | `python3 discover.py` runs and prints the live `v7-...` URL | "Alpha can see the service registry; beta cannot. The Payments endpoint moved." |
| 0:34 | LEFT | wait | the `send --type ASK` command, `wrote .../docs/agent-mail/<id>-....md`, `id: <id>`, then "SENT" | "Alpha tells beta through Postbox. A message is one file in docs/agent-mail." |
| 0:40 | RIGHT | paste **B2**, Return | see below | |
| 0:42 | RIGHT | wait | the hook's mail reaches beta before it acts | "Nobody told beta to check. Its host ran a hook before the model saw my prompt and put the mail in front of it." |
| 0:55 | RIGHT | wait | a `send --type ANSWER ... --result DONE` command, then an edit of `client.py` adding `refund_url` | "Beta acknowledges with an ANSWER, then writes the function against the new URL, which it had no other way to know." |
| 1:05 | LEFT | type `/exit`, Return (UNVERIFIED; if it does nothing press Ctrl-C twice), then `python3 "$CLONE/examples/two-agents/live_demo.py" check --dir /tmp/postbox-demo` | six `OK` lines and `all 6 beats happened` | "Six checks, read from the files and the code, not from what a model said." |
| 1:20 | LEFT | stop recording | | "That shows delivery and a model acting on a message. It does not show this beats a shared notes file; a small pilot found no difference. And a model decides whether to act, so this can fail." |

Timings are targets. Say the lines over the waiting, or record silent and
add the voice-over afterwards, which removes the timing pressure. If the take
runs long, shorten the talking, not the `check`. Only the verdict printed by
`check` counts as proof; if you show `cat /private/tmp/postbox-demo/project/client.py`
too, do it after `check`.

**What the hook looks like at 0:42 (UNVERIFIED).** In `-p` mode the hook's
output is visible as an event. In the interactive screen it may appear as the
block `Agent mail - 1 live message(s) addressed to 'beta'.` followed by
`ASK from alpha [awaiting your reply]`, or only as a short note such as
"UserPromptSubmit hook ... succeeded" that you can expand (Ctrl-O is the
transcript view in current Claude Code; also unverified here). Either way
the model has the text. What you can rely on: beta's first action is to send
an ANSWER to alpha, which it could not know to do without the mail, and
`check` beat 4 reads the host's transcript. If the block is not visible, do
the dry run again, find how to expand it, and note that for the take. Do not
paste or type the block in yourself.

**Step 10. After the take.** `check` exits 0 only if all six beats passed.
Keep both windows open until you have decided to keep the take.

**Step 11. End a session without running `check`:** `/exit` Return in each
window. UNVERIFIED alternatives if it does not respond: Ctrl-C twice, or
Ctrl-D.

**Step 12. Reset and re-run.** Re-run step 3's command (`setup`). It deletes
and rebuilds `/tmp/postbox-demo` with a fresh random URL and fresh prompts
text; exit both `claude` sessions first, then repeat steps 5 and 6 (cd, exports,
`claude --model haiku`) in both windows, since the old shells are fine but the
paths changed under them. The earlier take's transcripts stay in
`~/.claude/projects/`; they name old message ids, so they cannot satisfy
beat 4 for a new take. Delete them only if you are sure you want to.

## Keeping the take honest

* A FAIL is a result, not a reason to fake anything. Do not edit the video
  around a failed beat, do not hand-edit `client.py` or the mailbox, and do
  not publish a failed take as a success. Either reset and record again, or
  publish the failed take labelled as a failure (say which beat).
* Run `check` on camera, with the real command. Do not paste an earlier
  `check` result.
* If a permission dialog or trust dialog shows up during the take, show it
  and answer it plainly; say that it appeared. If you re-record after one,
  say nothing false in the caption ("took two attempts" is fine).
* If you show the same take twice (trimmed), keep every beat of one run. The
  mailbox and `client.py` come from one run only.
* Caption (also in `docs/demo-narration.md`): "Two real Claude Code
  sessions exchanging a message through Postbox ... Mechanism demo only, not a
  benchmark." Do not add claims the screen and `check` do not show.
* Your own `~/.claude/settings.json` and `CLAUDE.md` apply to these
  interactive sessions (the headless rehearsal ignores them). If you have
  other hooks, plugins or a status line, they will be on screen and may
  change what the model does. That is real behaviour and fine to record, but
  look at the dry run for anything you would not want shown.

## If something goes wrong

| Symptom | Likely cause | Do |
|---|---|---|
| `claude: command not found` | not installed or not on PATH | install Claude Code, open a new Terminal |
| `python3` complains about the Xcode license | macOS command-line tools not accepted | `sudo xcodebuild -license`, or use Homebrew python |
| `setup` says "exists, is not empty and is not a live-demo directory" | `/tmp/postbox-demo` is something else | pick another `--dir` and use it everywhere (setup prints it in the commands) |
| A trust or other first-run dialog differs from the description | UNVERIFIED part | read it, choose the safe option, write down the wording |
| A permission dialog appears in either window | a command outside the pre-approved list (the model chained `; echo $?` onto the send, or put `AGENT_MAIL_IDENTITY=beta` in front, or used a different path) | Dry run: choose no, note the command. Take: choose no or approve once, say so on camera; reset for a clean take |
| Beta's first reply has no sign of the mail (hook not firing) | folder not trusted, wrong folder, or `AGENT_MAIL_IDENTITY` not exported in beta's shell | `/exit` beta, `echo $AGENT_MAIL_IDENTITY` (must print `beta`), `pwd` (must be the project), `cat .claude/settings.json`, restart `claude`. Then reset and re-record |
| Alpha: `PAYMENTS_REGISTRY is not set` | export missing in LEFT window | export it, `/exit` and restart `claude` there (a running session does not see new variables) |
| Alpha says the message may never be read | `PARTICIPANTS.md` rows missing | `setup` writes them; reset |
| Beta answers or writes a path that is not v7 | beta never saw the mail | as "hook not firing" |
| Beat 5 FAIL, no ANSWER | beta's send was refused or beta skipped the mail | reset, re-record |
| Beat 6 FAIL, `refund_url` uses `/v1/` | beta confirmed but did not use the new URL (a model choice; seen in earlier runs) | re-record; if it keeps happening, report it |
| Beat 4 FAIL only, hook block was on screen | the transcript lookup is untested against a real transcript | the `check` line says which directory it looked in and how many files. Try `--transcripts DIR` with `ls -d ~/.claude/projects/*postbox-demo*`. If still FAIL, say on camera "not verified from a file", and send us the first lines of one `.jsonl` (with ids; it holds your prompts, so read it first) |
| `check` exits 2 and says "no state.json" | wrong `--dir` | use the same `--dir` as `setup` |
| Model takes over a minute or stalls | usage limit or network | wait, or `/exit`, reset and retry |

## Optional: two panes in one window with tmux

Not needed and not tested here (tmux was not available). If you like it:
`brew install tmux`, run `tmux`, `Ctrl-b %` for a second pane, `Ctrl-b`
then an arrow to switch. The commands in section C are identical in each
pane. Prefer two Terminal windows if anything is odd.

## Dry run without a terminal: `rehearse`

```sh
python3 examples/two-agents/live_demo.py rehearse --max-calls 2 --max-cost 0.5
```

Not a substitute for the interactive dry run in section B. It runs alpha then
beta as `claude -p` sessions (haiku, effort low, `--permission-mode dontAsk`,
`--setting-sources project`, `--strict-mcp-config`, a short tool list,
`--no-session-persistence`, a per-session `--max-budget-usd`, never
`--dangerously-skip-permissions`), refuses to start a session past
`--max-calls` or once the summed `total_cost_usd` would pass `--max-cost`
(default 1.50, never more than 2.00), prints any tool call the host refused,
and then runs `check`. Exit 0: all beats; 1: a beat failed; 2: not run (no
`claude`, not signed in, or a budget refusal), a SKIP and never a pass. It
uses its own default demo directory (add `--dir` to choose one; it resets
whatever directory it is given, so never give it `/tmp/postbox-demo` between
`setup` and the take). It costs about USD 0.004 with haiku.

## What a headless rehearsal proves, and what it does not

* Proves: the project is installed the way a user installs it; the
  `UserPromptSubmit` hook registered in `.claude/settings.json` fires in print
  mode and its output reaches the model; the model can send an ANSWER with the
  real CLI; the prompts and scenario can produce all six beats. Beat 4 in a
  rehearsal is read from the hook events the host emitted
  (`--include-hook-events`), which is stronger than the model's account.
* Does not prove that an interactive TUI session behaves the same (the list at
  the top of Part 1).
* Does not prove reliability: the model decides whether to act on the message.
* Does not show Postbox beats a notes file (`docs/micro-experiment.md`).

### Results of the author's runs (claude 2.1.296, haiku)

Earlier session (2026-10-12): run 1 with a first wording of prompt A ("ask
beta to confirm") gave beats 1-5 OK and beat 6 FAIL (beta confirmed but wrote
`refund_url` against v1), so prompt A now says the v1 URL is retired; with that
all 6 beats were OK once; control (beta only, no mail) used v1.

Hardening runs (2026-10-13, headless, 16 rehearsals plus one control):

| Change tried | Runs | Result |
|---|---|---|
| none (as shipped) | 2 | one run: beta's ANSWER and the `ls`/`python3 -c` checks were refused by the host (beats 1, 5, 6 FAIL); one run: all beats but 6 |
| PARTICIPANTS.md rows for alpha and beta (alpha had been warned "beta is not a row", and reported the message "may never be read") | 8 | beat 6 passed 7 of 8; beta's ANSWER was refused by the host in 5 of the 8 (it chained `; echo "exit $?"`, put `AGENT_MAIL_IDENTITY=beta` in front, or used `--to alice`) |
| prompt B2 says `--to alpha` and "one plain command" | 4 | beats 1-5 OK in 4 of 4 (the host refused only beta's own `python3 -c` self-check); beat 6 OK in 2 of 4 (the others used v1) |
| prompt B2 says "the live Payments service" instead of "the same" | 5 | all 6 beats in 5 of 5 |
| control: beta only, B2, no mail | 1 | v1 URL, as expected |

Total spend of everything above: about USD 0.09 (host-reported
`total_cost_usd`, USD 0.002 to 0.005 per session). Five out of five after the
last wording change is a small sample after several wording changes tuned on
the same scenario: it says the scenario can work, not that it will every time.
Headless runs used `dontAsk` mode, which refuses a tool call outright; an
interactive session shows a dialog instead, so the refusals above may appear
as permission dialogs in the TUI.

## Evidence on camera: `check`

`check` reads `docs/agent-mail/*` (via the tool's own `list --json`), imports
`client.py` in a fresh interpreter and calls `refund_url('ch_42')`, and looks
for the hook's output (a file captured by `rehearse`, or Claude Code's own
transcript: `~/.claude/projects/<project path, non-alphanumerics as ->/*.jsonl`,
or `--transcripts DIR`). Beat 4 passes if one line of a transcript holds the
`Agent mail - ` header and the ASK's id. It does not look at what a model said.
`check` can be run from any directory. Example from a headless rehearsal (the
second line of each beat is the evidence it printed):

```
OK   beat 1: two sessions wrote under two identities
       senders in the mailbox: alpha, beta
OK   beat 2: alpha's discovery is in its message (only alpha could know it)
       ASK subject: 'Payments live base URL is https://payments.internal.example/v7-084c73/ledger; old v1 URL is retired; ...'
OK   beat 3: the message went through Postbox
       ASK 01M4TWJZ... alpha -> beta
OK   beat 4: beta's prompt hook handed it the message
       hook output captured in beta.hook.txt names 01M4TWJZ...
OK   beat 5: beta acknowledged with an ANSWER citing the ASK
       ANSWER 01M4TWK... reply_to 01M4TWJZ..., ASK no longer owed
OK   beat 6: beta's code uses the fact alpha found
       refund_url('ch_42') = https://payments.internal.example/v7-084c73/ledger/charges/ch_42/refunds
all 6 beats happened
```

After an interactive take, beat 4's evidence line reads `transcript <file>
holds the hook output naming <id>` instead. If it fails it says how many
transcript files it looked at and where.

---


# Part 2: scripted run.py (about 60 seconds, no model)

A shot list for a terminal recording of `run.py --pause`. The script waits
for Enter between its seven steps, so the pace is yours. Nothing is staged:
what is on screen is what the tool printed, and the script exits non-zero if
a step did not happen as its narration says, so a recording that ends on
`All 7 steps behaved as described.` is a recording of a passing run.

## Before recording

* **Window.** 110 columns by 34 rows or larger, a font size readable at half
  width. The displayed commands are wrapped at 96 columns; a few lines of
  tool output are longer (the adapter's `read:` and `Reply:` lines, the
  `wrote` lines) and wrap in any window narrower than they are.
* **Paths.** The header prints the clone's path and the temporary project's
  path. Clone somewhere whose path you are content to publish, for example
  `/tmp/agent-postbox`, and check that header before sharing the recording.
  After the header, the project's path appears only as `<project>`.
* **Prompt.** A short shell prompt (`PS1='$ '`), no right-hand prompt, no
  notifications.
* **Dry run.** Run it once with `--pause` and press Enter through it, to
  settle the pace. Each run creates its own directory and removes it.
* **Recorder.** Anything that records a terminal. With asciinema:
  `asciinema rec demo.cast`, then type the command below, and end the
  recording with `exit` after the last line.

## The command

```sh
cd /tmp/agent-postbox
clear
python3 examples/two-agents/run.py --pause
```

## Shots

Times are targets, not requirements. Press Enter at the end of each shot.

| # | From | On screen | Hold it until the viewer has read | Say, if narrating |
|---|---|---|---|---|
| 0 | 0:00 | The command and the header: `python`, `<clone>`, `<project>` | "two scripted sessions, the real tool. No model, no network." | "Two terminals' worth of agents, played by a script. The tool is the real one." |
| 1 | 0:04 | `== 1. Install into a project` and `install.py`'s output | `registered the claude-code prompt-submit adapter` | "One command installs it into a project and registers the hook." |
| 2 | 0:11 | `== 2. agent-a asks agent-b a question`, the `send` command, `wrote ...` and `id: ...` | the `(stdin)` line and `wrote` | "Agent A asks a question. A message is one Markdown file." |
| 3 | 0:19 | `== 3. agent-b's next prompt`, the adapter's output | `ASK from agent-a [awaiting your reply]` and `You ('agent-b') owe a reply to 1 open ASK(s)` | "Agent B did not poll. This is what its host puts in front of the model before the next prompt." Longest hold: this is the point of the demo. |
| 4 | 0:31 | `== 4. agent-b claims the file, then answers`: two `send` commands | `--scope billing/client.py`, `--expires`, then `--result DONE` | "B takes a lease on the file, then answers. DONE is B's claim, not proof." |
| 5 | 0:40 | `== 5. agent-a's next prompt`, the adapter's output as agent-a | `CLAIM from agent-b`, `holds until:`, `ANSWER from agent-b` | "A did not poll either. The answer and the lease are there at its next prompt." |
| 6 | 0:48 | `== 6. agent-a tries to claim the same file` | `scope 'billing/client.py' is already held by agent-b` and `[exit 3]` | "A second claim on the same file is refused. Nothing is written." |
| 7 | 0:54 | `== 7. The picture: status` | `asks: open=0`, the `held claims` row, `ask results (claimed, not proven)` | "Status is derived from the files: nothing open, one lease, one claimed result." |
| 8 | 0:59 | `All 7 steps behaved as described.` and `Removed ...` | both lines | Stop the recording here. |

## After recording

* Watch it once at full size and once at the size it will be embedded at.
* Check the header and the last line for any path you did not mean to show.
* Keep the caption factual: the agents are scripted and no LLM runs. The
  recording shows delivery, a refused second claim and derived status; it
  does not show a model acting on mail.
* If a step printed `FAILED`, that is a bug report, not a retake: the output
  names the step and what was expected (see CONTRIBUTING.md, field reports).
