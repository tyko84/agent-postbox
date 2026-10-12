# Two agents exchange a message

One command, a few seconds, nothing to sign up for:

```sh
python3 examples/two-agents/run.py
```

It needs Python 3.10 or newer and this clone, on Linux or macOS. No model, no
account, no network, no packages.

## What it shows

Seven steps, each printed as the command that runs and the output it gave:

1. **Install.** `install.py` copies the tool into a throwaway project
   (`tools/agent-postbox/`), creates its mailbox (`docs/agent-mail/`) and
   registers the pickup adapter in the project's `.claude/settings.json`.
2. **agent-a asks.** `send --type ASK` writes one Markdown file into the
   mailbox and prints its id.
3. **agent-b's next prompt.** The pickup adapter runs with
   `AGENT_MAIL_IDENTITY=agent-b` and prints the ASK. Nobody polled, and
   agent-b was not told to look: this is the text a host puts into the
   model's context before the prompt.
4. **agent-b claims the file and answers.** A `CLAIM` with a scope and an
   expiry, then an `ANSWER` that cites the ASK and carries `--result DONE`.
5. **agent-a's next prompt.** The adapter, now as agent-a, prints the answer
   and the claim.
6. **agent-a tries to claim the same file.** Refused, exit status 3, nothing
   written.
7. **`status`.** No open ASK, one held claim with its holder and expiry, and
   the result reported as *claimed, not proven*.

After each command the script checks what it just printed (the exit status,
the lines that must be there, the files in the mailbox) and prints a
`[checked]` line. If a step does not behave as described, it stops, says
which step and what it expected, and exits 1. `test_examples.py` runs it in CI
on every supported Python, and also runs it against a copy of the tool with
the adapter silenced and against one that grants a second claim, to confirm
that it fails when it should.

Options:

| Flag | Effect |
|---|---|
| `--keep` | Leave the throwaway project behind and print its path, so you can read the three message files and run the CLI in it yourself |
| `--pause` | Wait for Enter between steps (for reading along, or for recording: see [RECORDING.md](RECORDING.md)) |

Without `--keep` the directory is removed when the script ends, whether it
passed or failed.

## What is real and what is scripted

**Real.** The installer is this clone's `install.py`. The CLI is the
`agent_mail.py` it copied into the project, run as a subprocess for every
`send` and for `status`. The pickup is the `hooks/agent_mail_check.py` it
copied, run the way a host's prompt-submit hook runs it: with
`AGENT_MAIL_IDENTITY` set, its standard output being what the model would
see. The messages are files; with `--keep` you can open them. The refusal in
step 6 is the tool's own.

**Scripted.** The two "agents". There is no LLM and no host. An agent here is
a name and a fixed list of commands, and a "prompt" is the script running the
adapter. The demo therefore shows that mail reaches the other session's
context without anyone polling, that a claim is exclusive, and that status is
derived from the files. It does not show what a model does once the mail is
in its context; nothing in this repository measures that.

**Two things the output rewrites, both named in its header.** `python` stands
for the interpreter that runs the script, and the project's path is printed
as `<project>` so that lines stay short. Nothing else is edited.

Any `AGENT_MAIL_*` variable in your environment is dropped for the run, so the
demo never reads or writes a mailbox of yours.

## The same thing with two real Claude Code sessions

The demo runs the adapter itself. In a real project the host does that. For
Claude Code, from the directory that holds your clone:

```sh
python3 agent-postbox/install.py /path/to/your/project                        # 1. install the tool and the mailbox
export AGENT_MAIL_IDENTITY=agent-a                                            # 2. say who this session is
python3 agent-postbox/install.py /path/to/your/project --adapter claude-code  # 3. register the pickup hook
```

Command 3 adds one `UserPromptSubmit` entry to the project's
`.claude/settings.json` (backed up first, safe to re-run); it also does what
command 1 does, so the two can be one. Command 2 belongs in the shell each
session is started from, with a different name in each: `agent-a` in one
terminal, `agent-b` in the other. There is no default identity, and an
adapter without one prints nothing.

Then:

* give each identity a row in `docs/agent-mail/PARTICIPANTS.md` saying how
  mail reaches it;
* run `python3 tools/agent-postbox/selftest.py` in the project, because an
  adapter that is not wired looks exactly like an empty inbox;
* start one Claude Code session per terminal in the project, and ask one of
  them to send the other a question with
  `python3 tools/agent-postbox/agent_mail.py send ...`. The other session
  sees it at its next prompt.

This script does not start Claude Code and has not checked the hook inside
it; the main [README](../../README.md) (Install) and
[SECURITY.md](../../SECURITY.md) are the reference for a real install. Other
hosts wire `hooks/agent_mail_check.py` to their own prompt-submit event, or
poll with `agent_mail.py list --to <you> --live`.
