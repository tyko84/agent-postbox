# Hosts: where the pickup adapter can run

The protocol does not depend on a host (PROTOCOL.md sections 7 to 9, 15). What
depends on the host is one thing only: whether live mail is put in front of the
model **without the agent deciding to look**. This page says, per host, which
mechanism does that, what it guarantees, and exactly how it was checked.

Vendor documentation was read on **2026-10-12**. Event and field names are
quoted from it. Nothing below was observed in a running Codex or Cursor; where
the documentation is silent, this page says "not stated" instead of guessing.

## Compatibility matrix

| Host | Automatic per-prompt delivery | Session-start delivery | Explicit polling | Identity binding | How verified |
| --- | --- | --- | --- | --- | --- |
| Claude Code | Yes: `UserPromptSubmit` command hook, `install.py --adapter claude-code` | Covered by the per-prompt hook (it runs before the first prompt too) | Yes | Applies (host-independent) | Adapter tested in CI (`selftest.py`, `test_field.py`, `scripts/scenario.py`). Run inside the real host on 2026-10-12 (Claude Code 2.1.296, headless `claude -p`, `scripts/e2e_claude.py`): delivery, no cross-talk, next prompt, resume and recovery passed; a missing hook script **blocked the prompt** with the registration of that date; the registered command now ends in `|| true` and `install.py` upgrades an older registration in place ([e2e-real-sessions.md](e2e-real-sessions.md)). Interactive mode not run |
| Codex (CLI) | Yes: `UserPromptSubmit` command hook, `install.py --adapter codex`, **after the user trusts the hook in `/hooks`** | Covered by the per-prompt hook | Yes | Applies (host-independent) | wrapper tested against the documented payload shapes; not run inside the real host (`test_adapters.py`, `scripts/scenario.py --pickup codex`). Confirm with the checklist below |
| Cursor (IDE agent, `cursor-agent` CLI) | **No.** `beforeSubmitPrompt` can allow or block a prompt; it has no field that reaches the model | Possible in principle (`sessionStart` returns `additional_context`), not shipped and not tested | Yes: put the polling instruction in a rule or `AGENTS.md` | Applies (host-independent) | untested; documentation read only |
| Any host without hooks | No | No | Yes: `agent_mail.py list --to <you> --live` at session start and before acting on shared state | Applies (host-independent) | Polling is the tool itself, tested in CI; whether the agent remembers to poll is not something a test can show |

Identity binding (PROTOCOL.md section 40, [identity.md](identity.md)) is a
property of the mailbox directory and the operating-system user that writes a
message file. No host, hook or adapter takes part in it, so it applies the same
way in every row. Likewise `AGENT_MAIL_IDENTITY` is read from the environment of
the adapter process in every host; nothing on a hook's stdin can set it.

Polling is not second-class as a transport: the same files, the same `live`
rule. It is weaker only in the one respect this project exists for: a busy or
new agent has to decide to look (`docs/benchmarking.md` measures that gap).

## Codex

Source: <https://learn.chatgpt.com/docs/hooks> (`developers.openai.com/codex/hooks`
redirects there) and <https://learn.chatgpt.com/docs/agent-configuration/agents-md>.

| Mechanism | Event | Text into the model's context | When | Failure behaviour | Stability |
| --- | --- | --- | --- | --- | --- |
| Command hook | `UserPromptSubmit` | Yes. "Plain text on `stdout` is added as extra developer context." JSON `hookSpecificOutput.additionalContext` does the same | Per prompt, before it is sent (`prompt`: "User prompt that's about to be sent") | "Exit `0` with no output is treated as success and Codex continues." Exit `2` blocks the prompt ("You can also use exit code `2` and write the blocking reason to `stderr`"). Other exit codes: not stated for this event | Documented; "Hooks are enabled by default"; `[features] hooks = false` turns them off |
| Command hook | `SessionStart` (`source`: `startup`, `resume`, `clear`, `compact`) | Yes, same two forms | Per session start, and again after compaction | As above | Documented |
| Command hook | `PostToolUse`, `Stop`, others | Feedback or continuation, not a pre-prompt channel | Per tool call / per turn end | `Stop`: "Plain text output is invalid for this event" | Documented |
| `AGENTS.md` | none (a file) | Only as a file: static instructions | "once per run; in the TUI this usually means once per launched session". **Not re-read per turn** | A missing file is skipped | Documented |

Config: `hooks.json` or inline `[hooks]` tables in `config.toml`, next to an
active config layer (`~/.codex/`, `<repo>/.codex/`). Schema: event name, then a
list of matcher groups, each with a `hooks` list of handlers
(`type: "command"`, `command`, optional `timeout` in seconds with a default of
`600`, `statusMessage`, `additionalContextLimit`, `async`, `commandWindows`).
The hook receives "one JSON object on `stdin`" (`session_id`,
`transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`, and
for this event `turn_id` and `prompt`). "Commands run with the session `cwd` as
their working directory."

**The adapter for Codex is the shipped `hooks/agent_mail_check.py`, unchanged.**
It already prints plain text and exits 0, which is the first of the two output
forms Codex documents for `UserPromptSubmit`, and it does not read stdin. So
there is no Codex-specific script; there is a registration:

```sh
python agent-postbox/install.py /path/to/your/project --adapter codex
```

which merges this into the project's `.codex/hooks.json` (a backup is written
first; a second run changes nothing):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$(git rev-parse --show-toplevel)/tools/agent-postbox/hooks/agent_mail_check.py\" || true"
          }
        ]
      }
    ]
  }
}
```

To register by hand, add that one matcher group to the `UserPromptSubmit` list
of your own `hooks.json` (or the equivalent `[[hooks.UserPromptSubmit]]` table
in `config.toml`). The path is resolved from the git root because Codex "may be
started from a subdirectory". `|| true` is there because the interpreter exits
2 when it cannot open the script, and exit 2 on this event blocks the prompt: a
moved tool directory must cost you the mail, never the prompt.

The installer refuses, with one line and before copying anything, when it
cannot register safely: the project is not a git repository, `--dest` contains
characters a shell would re-interpret, `.codex/hooks.json` is not JSON of the
documented nesting, or `.codex/config.toml` already defines hooks inline
("If a single layer contains both `hooks.json` and inline `[hooks]`, Codex
merges them and warns at startup").

### What this guarantees, compared with Claude Code's `UserPromptSubmit`

The same event at the same point: a command runs before every submitted prompt
and its plain-text stdout becomes context for that turn. The differences, all
from the documentation:

* **Trust gate.** "Before a non-managed hook can run, Codex requires you to
  review and trust the exact hook definition... new or changed hooks are marked
  for review and skipped until trusted." And "Project-local hooks load only
  when the project `.codex/` layer is trusted." Until someone runs `/hooks` and
  trusts it, the registered adapter delivers **nothing**, and that looks
  exactly like an empty inbox. A repaired command (new hash) needs trusting
  again.
* **Size limit.** "Codex limits each model-visible hook-output message to
  roughly 2,500 tokens"; beyond that the model gets "a head-and-tail preview
  with the saved-file path". One live message is a few hundred characters, so a
  few dozen live messages can cross the limit. The header and the first
  messages (head) and the end of the output (tail) survive; the middle is in
  the saved file. Raise `additionalContextLimit` on the handler if your inbox runs
  that deep.
* **Timeout.** 600 seconds by default (Claude Code: 30 for this event, per
  <https://code.claude.com/docs/en/hooks>). The
  adapter takes well under a second on a normal mailbox; set `"timeout"` if you
  want a bound.
* **Role.** The text arrives as "developer context", not as part of the user's
  message.
* **Not stated:** whether the hook process inherits the environment of the
  shell that started `codex` (the adapter needs `AGENT_MAIL_IDENTITY`), whether
  the event fires in non-interactive runs (`codex exec`), in the IDE extension
  or in cloud tasks, what a non-zero exit other than 2 does on this event, and
  which shell runs the command on Windows (the registered command is POSIX
  shell).

### What was verified, and what was not

Verified here (`test_adapters.py`, run in CI): the adapter, started through a
shell with the registered command shape and the documented stdin object, prints
a live message as plain text that is not JSON and exits 0; with no identity,
another identity, malformed or oversized stdin, or a broken mailbox it prints
nothing and exits 0; a missing script cannot produce exit 2; the installer
merges, is idempotent, and refuses instead of overwriting; the registered
string itself delivers from a subdirectory of a real git repository.
`scripts/scenario.py --pickup codex` runs the reliability harness with every
pickup started that way. **Not verified:** anything inside Codex. The stdin
object is assembled from the documented field tables (the page prints no full
example for this event).

Confirm it in the real host (five steps, with a positive control):

1. `python install.py <project> --adapter codex`, then in the project start
   Codex with an identity: `AGENT_MAIL_IDENTITY=bob codex`.
2. Run `/hooks`; review and trust the `UserPromptSubmit` entry (trust the
   project too if Codex asks).
3. From another terminal, as another identity, send the control:
   `echo control | python3 tools/agent-postbox/agent_mail.py send --type NOTICE --from alice --to bob --subject "control 4417" --body-file -`
4. In Codex, submit: `Without running any command: is there agent mail for you? Quote its subject.`
   **Pass** only if the answer quotes `control 4417`. An answer that runs
   `agent_mail.py` first is polling, not delivery: repeat with tools denied.
5. Negative and edge: start `codex` with `AGENT_MAIL_IDENTITY` unset and repeat
   step 4 (it must not know the subject); then start it from a subdirectory
   with the identity set (it must). If step 4 fails with the identity set,
   check `/hooks` for an untrusted or failing entry and `[features] hooks`.

## Cursor

Source: <https://cursor.com/docs/hooks>, <https://cursor.com/docs/reference/third-party-hooks>,
<https://cursor.com/docs/rules>, <https://cursor.com/docs/cli/changelog>.

| Mechanism | Event | Text into the model's context | When | Failure behaviour | Stability |
| --- | --- | --- | --- | --- | --- |
| Command hook (`.cursor/hooks.json`, `"version": 1`) | `beforeSubmitPrompt` | **No.** Output is `continue` ("Whether to allow the prompt submission to proceed") and `user_message` ("Message shown to the user when the prompt is blocked"). It is listed as "Validate prompts before submission" | Per prompt | Exit `2` blocks; "Other exit codes - Hook failed, action proceeds (fail-open by default)" | Documented |
| Command hook | `sessionStart` | Yes: `additional_context`, "Additional context to add to the conversation's initial system context" | Once, "when a new composer conversation is created" | "This hook runs as fire-and-forget; the agent loop does not wait for or enforce a blocking response." Not run in cloud agents | Documented |
| Command hook | `postToolUse` | Yes: `additional_context`, "Extra context injected into the conversation after the tool result" | Per tool call, mid-turn; never before a prompt | Fail-open | Documented |
| Command hook | `stop` | `followup_message` starts another turn; `loop_limit` default `5` | Per turn end | Fail-open | Documented |
| Rules (`.cursor/rules/*.mdc` with `alwaysApply: true`), `AGENTS.md` | none (files) | Only as a file: "rule contents are included at the start of the model context" | Per chat session | n/a | Documented |

Cursor has **no documented way to put a command's output in front of the model
before each prompt**. The honest integration is therefore not an adapter:

* Put the polling instruction in an always-applied rule or `AGENTS.md`: run
  `agent_mail.py list --to <you> --live` at the start of a session and before
  acting on shared state. That is explicit polling, with its known weakness.
* A `sessionStart` hook returning the adapter's text as `additional_context`
  would give once-per-conversation delivery. It is not shipped: it is not
  per-prompt (mail sent after the conversation starts is never surfaced), the
  event is fire-and-forget, and none of it could be run here.
* The CLI: the hooks page describes agent hooks as applying to "Cmd+K and Agent
  Chat operations" and names the CLI only for `workspaceOpen`; the CLI
  changelog mentions hooks, including `beforeSubmitPrompt`, running in the CLI.
  Per-event CLI coverage is not tabulated.

**A caution for projects that registered the Claude Code adapter.** Cursor loads
`.claude/settings.json` hooks by default ("The setting is on by default") and
maps `UserPromptSubmit` to `beforeSubmitPrompt`. So Cursor will run
`hooks/agent_mail_check.py` before each prompt in such a project, and the
adapter will print plain text where Cursor expects a JSON object with
`continue`. What Cursor does with that is not stated (invalid JSON is
documented to block only for the permission hooks, which this is not). Per the
documentation the text does not reach the model either way. If prompts are
blocked or errors appear in Cursor's Hooks output channel, turn off
Settings, Agents, Third-Party Imports for that project. Untested.

## Any host without hooks

Poll. Put this where the host loads standing instructions:

```text
At the start of every session, and before you push, delete, or edit shared
state, run: python3 tools/agent-postbox/agent_mail.py list --to <you> --live
```

and run `agent_mail.py doctor` and `selftest.py` once to see that the mailbox
itself works. Everything else in the protocol is unchanged.
