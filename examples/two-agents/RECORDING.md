# Recording the demo (about 60 seconds)

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
