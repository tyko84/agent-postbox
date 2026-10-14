# Demo narration (two real Claude Code sessions, 60 to 90 seconds)

For the recording described in `examples/two-agents/RECORDING.md`. Read it
as spoken lines, not marketing copy. Every claim below is something the screen
or `check` shows. Nothing here says Postbox gets better results than a notes
file: a pilot (`docs/micro-experiment.md`) found no advantage with haiku, and
this demo shows only that the mechanism works.

| Time | Say | Show |
|---|---|---|
| 0:00 | "Two separate Claude Code sessions in one project. They share only a folder of files." | both panes |
| 0:08 | "Beta reads the client. It points at the old v1 ledger. No mail yet." | beta summary, no `Agent mail` block |
| 0:16 | "Alpha can see the service registry; beta cannot. The Payments endpoint moved." | alpha runs `discover.py` |
| 0:34 | "Alpha tells beta through Postbox. A message is one file in docs/agent-mail." | the `send` output, `wrote ...` |
| 0:42 | "Nobody told beta to check. Its host ran a hook before the model saw my prompt and put the mail in front of it." | the `Agent mail` block, if the screen shows it (see below) |
| 0:55 | "Beta acknowledges with an ANSWER, then writes the function against the new URL, which it had no other way to know." | `ANSWER ... DONE`, the `client.py` edit |
| 1:05 | "Six checks, read from the files and the code, not from what a model said." | `live_demo.py check`, six `OK` |
| 1:20 | "That shows delivery and a model acting on a message. It does not show this beats a shared notes file; a small pilot found no difference. And a model decides whether to act, so this can fail." | stop |

The times match the table in `examples/two-agents/RECORDING.md` (beta's
second prompt is pasted at 0:40, so the 0:42 line is spoken while it works).
They are targets: either say the lines over the waiting or record silent and
add the voice-over afterwards. The 0:42 line is only true if the hook's output
reached the model; whether the interactive screen shows the `Agent mail` block
(rather than a collapsed "hook ran" note) has not been seen. If the block is
not visible, say "its host ran a hook before the model saw my prompt" and
point at the ANSWER that follows, and do not say "you can see the mail".

If a beat fails, say so: "Beta did not use the new URL this time", show the
`FAIL` line, and re-record or publish it as a failure. Never narrate a result
the `check` output does not show.

Suggested caption: "Two real Claude Code sessions exchanging a message
through Postbox: automatic pickup by a hook, an acknowledgement, and code that
uses the shared fact. Prepared by a script; the evidence is read from files.
Mechanism demo only, not a benchmark."
