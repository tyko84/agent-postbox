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

## Pre-flight checklist (Mac, 5 minutes, off camera)

1. `claude --version` works and `claude auth status` says logged in.
2. `python3 --version` is 3.9 or newer (the tool is stdlib only).
3. Two terminal windows (Terminal, iTerm, or Ghostty) side by side, or tmux
   (`brew install tmux`, then `tmux`, `Ctrl-b %`). Two plain windows are fine
   and are what the script below assumes; a tmux split is the same commands.
4. Clone somewhere whose path you are content to show: `cd /tmp/agent-postbox`.
5. `python3 examples/two-agents/live_demo.py setup` and keep its output open:
   it prints the two blocks of commands and the three prompts with your real
   paths. The commands below use `$D` for the demo dir (default
   `/tmp/agent-postbox-live-demo`; on macOS `tempfile` may give
   `/var/folders/...`, so copy the real `project:` path that `setup` prints).
6. In each terminal run the `cd`/`export`/`claude --model haiku` lines once,
   accept "trust this folder" (the hook is project configuration, so the host
   asks once), then `/exit`. Clear both terminals (`Cmd-K`).
7. Do one complete dry run (below) and run `check`. Then run `setup` again
   (fresh URL) before the take.
8. Quit notification apps; font large enough to read at half width; about 110
   columns per pane; shell prompt short (`PS1='$ '`). The hook's `Reply:` and
   `read:` lines are long and wrap; that is the tool's real output.

`setup` also installs this clone's `install.py --adapter claude-code` into the
throwaway project, registers the hook in `.claude/settings.json`, fills
`PARTICIPANTS.md` with the rows alpha and beta (stating the mechanism, still
`reachable: no`, `not observed`: nothing here ran a positive control) and
writes `.claude/settings.local.json` pre-approving only `Read`, `Edit`,
`Write`, `python3 discover.py` and the mailbox CLI, so no permission dialog
should appear. If one does, see "If a beat fails".

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

## Layout

Two panes, left = alpha, right = beta, both in the same project folder.
`check` needs no third window: when alpha has finished, type `/exit` in
the left pane and run it in that shell.

```
LEFT: alpha                                   RIGHT: beta (no registry variable)
cd <project>                                  cd <project>
export AGENT_MAIL_IDENTITY=alpha              export AGENT_MAIL_IDENTITY=beta
export PAYMENTS_REGISTRY=<demo>/registry/payments.json
claude --model haiku                          claude --model haiku
```

Paste these from the `setup` output (they contain your real paths).

## Recording script (about 80 seconds)

Start the screen recorder with both sessions already open at an empty prompt.
Prompts A, B1 and B2 are printed by `setup`; paste them, do not type them
(typing eats the time budget). Times are targets.

| Time | Pane | You do | On screen | Say (see `docs/demo-narration.md`) |
|---|---|---|---|---|
| 0:00 | both | nothing | two `claude` sessions, one folder | "Two separate Claude Code sessions, one project. They share only a folder of files." |
| 0:08 | right | paste **B1** (`Read client.py and summarise it in one line.`) | one line; no "Agent mail" block | "Beta reads the client. It points at the v1 ledger. No mail yet." |
| 0:16 | left | paste **A** | `python3 discover.py` prints the live URL, then the `send --type ASK` command, `wrote .../docs/agent-mail/<id>-....md`, `id: <id>`, "SENT" | "Alpha can see the service registry; beta cannot. The endpoint moved. Alpha sends beta a message." |
| 0:40 | right | paste **B2** | the `Agent mail - 1 live message(s)` block appears BEFORE beta acts; then an `ANSWER ... --result DONE` send; then an edit of `client.py` | "Nobody told beta to check. A hook put the mail in front of the model. Beta acknowledges, then writes the function." |
| 1:05 | left | `/exit`, then `python3 examples/two-agents/live_demo.py check` (same `--dir` if you set one) | six `OK` lines and `all 6 beats happened` | "Six checks, read from the files, not from what a model said." |
| 1:20 | left | `cat <project>/client.py` | `refund_url` using `v7-...` | "The URL beta used is one only alpha could find." Stop recording. |

Wait for each model to finish before pasting the next prompt (haiku takes
10 to 25 seconds per prompt). If the take runs past 90 seconds, cut the
`cat`, not the `check`.

## Evidence on camera: `check`

`check` is the proof, and it is what makes an honest recording honest. It
reads `docs/agent-mail/*` (via the tool's own `list --json`), imports
`client.py` in a fresh interpreter and calls `refund_url('ch_42')`, and
looks for the hook's output. Run exactly:

```sh
python3 examples/two-agents/live_demo.py check
```

Example from a headless rehearsal (the second line of each beat is the
evidence it printed):

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

Beat 4 after an INTERACTIVE take is looked up in Claude Code's own transcript
(`~/.claude/projects/<project path, non-alphanumerics as ->/*.jsonl`, or
`--transcripts DIR`): it passes if one line holds the `Agent mail - ` header
and the ASK's id. That lookup has never been run against a real interactive
transcript (see the limits below). If beat 4 shows FAIL while the hook block
was plainly on screen in beta's pane, say so on camera: it means "not verified
from a file", not "did not happen".

## If a beat fails

A FAIL is a result, not a reason to fake anything. Do not edit around it and
do not publish a failed take as a success: re-run `setup` and re-record, or
publish it labelled as a failure.

| Symptom | Likely cause | Do |
|---|---|---|
| A permission dialog appears in either pane | a command outside the pre-approved list (for example the model chained `; echo $?` onto the send, or put `AGENT_MAIL_IDENTITY=beta` in front) | Choose "no" or approve once; say so on camera. Re-run `setup` for a clean take; B2 already asks for one plain command. |
| Beat 5 FAIL, no ANSWER | beta's send was refused or beta skipped the mail | Re-run `setup`, re-record. In the rehearsals below this was the host refusing a chained command. |
| Beat 6 FAIL, `refund_url` uses `/v1/` | beta confirmed but did not use the new URL (a model choice; seen in earlier runs) | Re-record; if it keeps happening, report it, do not hand-edit `client.py`. |
| No `Agent mail` block when beta gets B2 | the hook did not fire: folder not trusted, wrong folder, or `AGENT_MAIL_IDENTITY` not exported in beta's terminal | `cat <project>/.claude/settings.json` shows the hook; check `echo $AGENT_MAIL_IDENTITY` in beta's shell before starting `claude`. Restart that session. |
| Alpha's `discover.py` says `PAYMENTS_REGISTRY is not set` | the export was not in alpha's terminal | `export PAYMENTS_REGISTRY=...` and restart `claude` there. |
| Beta answers with a path or URL that is not v7 | beta never saw the mail (hook silent) | as "no Agent mail block". |
| Beat 4 FAIL only | transcript lookup untested (above) | decide from the screen; say so. |

## Reset and re-record

`setup` rebuilds everything with a fresh random URL (it refuses to delete a
directory it did not create, identified by a marker file); re-run it, re-open
`claude` in both terminals, and record again. `--dir DIR` chooses the location
(pass the same `--dir` to `check`).

## Dry run: `rehearse`

```sh
python3 examples/two-agents/live_demo.py rehearse --max-calls 2 --max-cost 0.5
```

It runs alpha then beta as `claude -p` sessions (haiku, effort low,
`--permission-mode dontAsk`, `--setting-sources project`, `--strict-mcp-config`,
a short tool list, `--no-session-persistence`, a per-session
`--max-budget-usd`, never `--dangerously-skip-permissions`), refuses to start a
session past `--max-calls` or once the summed `total_cost_usd` would pass
`--max-cost` (default 1.50, never more than 2.00), prints any tool call the
host refused, and then runs `check`. Exit 0: all beats; 1: a beat failed; 2:
not run (no `claude`, not signed in, or a budget refusal), a SKIP and never a
pass. A rehearsal costs about USD 0.004 with haiku.

## What a headless rehearsal proves, and what it does not

* Proves: the project is installed the way a user installs it; the
  `UserPromptSubmit` hook registered in `.claude/settings.json` fires in print
  mode and its output reaches the model; the model can send an ANSWER with the
  real CLI; the prompts and scenario can produce all six beats. Beat 4 in a
  rehearsal is read from the hook events the host emitted
  (`--include-hook-events`), which is stronger than the model's account.
* Does not prove that an interactive TUI session behaves the same. The
  interactive path (hooks in the TUI, the trust dialog, permission prompts,
  the transcript lookup for beat 4) was NOT exercised: this kit's rehearsals
  ran inside an automated session with no terminal, and no tmux. Do your own
  complete dry run on the Mac before the take.
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
