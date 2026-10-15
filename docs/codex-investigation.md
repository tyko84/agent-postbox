# Codex: static investigation and a bounded validation plan (issue #21)

**Documentation read only.** Codex CLI is not installed on the machine this was written on,
and nothing here was run in Codex. Read on 2026-10-15. Nothing below says the Codex adapter
works; it says what the documentation does and does not promise, and how to find out.

Sources (OpenAI's `developers.openai.com/codex/...` URLs permanently redirect to
`learn.chatgpt.com/docs/...`; the fetched pages were summarised by a fetch tool, so
sentences quoted are the tool's extraction and should be re-read at the source before
anything is built on them):

* Hooks: <https://learn.chatgpt.com/docs/hooks>
* Approvals and security: <https://learn.chatgpt.com/docs/agent-approvals-security>
* CLI: <https://learn.chatgpt.com/docs/codex/cli>
* Non-interactive mode: <https://developers.openai.com/codex/noninteractive.md> (seen only
  in a search result, not fetched; the page behind `learn.chatgpt.com/docs/codex/noninteractive`
  returned 404)

## What the documentation says

| Topic | Documentation read only |
|---|---|
| Prompt-submit hook that adds context | `UserPromptSubmit`: "Plain text on `stdout` is added as extra developer context"; JSON may block with `"decision": "block"`, or exit `2` |
| Other events listed | `SessionStart`, `SessionEnd`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Interrupt`, `Stop` (more than `hosts.md` recorded on 2026-10-12) |
| Config locations | `~/.codex/hooks.json` or `config.toml`; `<repo>/.codex/hooks.json` or `config.toml`; plugin-bundled `hooks/hooks.json`. "Higher-precedence config layers don't replace lower-precedence hooks" |
| Trust | "Non-managed hooks require explicit review"; `/hooks` inspects and trusts; trust is "against the hook's current hash", so a changed hook is "skipped until trusted"; "Project-local hooks load only when the project `.codex/` layer is trusted"; `--dangerously-bypass-hook-trust` exists for one-off automation |
| Working directory | Command hooks inherit the session `cwd` |
| stdin | `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `model`; `turn_id` on turn-scoped hooks; `permission_mode` on most; `prompt` for this event |
| Timeout, exit codes | Default 600 s; exit `2` blocks; other non-zero exits on this event: not stated |
| Output size | `additionalContextLimit`: beyond it the model gets a shorter preview and the full text is saved to disk |
| Disable | `[features] hooks = false` |
| Hooks in `codex exec`, IDE extension, cloud | Not stated |
| Environment of the hook process (does it see `AGENT_MAIL_IDENTITY`?) | Not stated |
| Operating systems | Hooks "work on macOS, Linux, Windows", with a `commandWindows` override. CLI: macOS/Linux installer; Windows via npm or Homebrew; WSL mentioned |
| Approvals | `on-request`, `never`; `untrusted` is deprecated in favour of a per-project `trust_level`. Other pages (a search summary, so lower confidence) give the non-interactive default as read-only with no prompts possible |
| Sandbox | `read-only`, `workspace-write` (the default), `danger-full-access`. Network off by default. macOS Seatbelt, Linux `bwrap` + seccomp |
| Protected paths | In `workspace-write`, `.git` (including gitdir pointers), `.codex`, `.agents`, `.aws` stay read-only, recursively |
| Sessions | `codex resume`; `session_id` is in the hook payload |
| Worktrees | Git worktrees are mentioned as a supported workflow; nothing about hook or `.codex` resolution inside one |

## The adapter's assumptions against that

`install.py --adapter codex` writes `.codex/hooks.json` with
`python3 "$(git rev-parse --show-toplevel)/tools/agent-postbox/hooks/agent_mail_check.py" || true`
under `UserPromptSubmit`; the script prints plain text, reads no stdin, needs
`AGENT_MAIL_IDENTITY`, exits 0.

| Assumption | Status |
|---|---|
| A `UserPromptSubmit` command hook can add context from plain stdout | **Supported** by the documentation |
| `<repo>/.codex/hooks.json` is read, in this nesting (event, matcher group, `hooks` list) | **Supported** (location named; nesting taken from `hosts.md`'s earlier reading, not re-verified here) |
| The user must trust the hook (and the project) before anything is delivered | **Supported**, and it is the main way a real install would silently deliver nothing |
| A moved script cannot block the prompt because of `|| true` | **Supported** (exit 2 blocks; `|| true` yields 0) |
| `git rev-parse --show-toplevel` resolves the tool when Codex starts in a subdirectory | **Unverifiable**: "cwd" is inherited, but the page does not say a subdirectory start is possible; `git` also has to exist in the hook's environment |
| The hook process inherits `AGENT_MAIL_IDENTITY` from the shell that started `codex` | **Unverifiable.** If it does not, the adapter is silent forever, which looks like an empty inbox. The single most likely failure |
| The hook runs in `codex exec` | **Unverifiable** |
| Linked worktrees: `.codex/hooks.json` is found in the worktree | **Unverifiable**; the file would have to be committed. Claude Code needs the same (see `docs/hosts.md`) |
| Windows is unsupported | Consistent with the project, but Codex hooks document Windows support; the registered command is POSIX shell, so a Windows run would need `commandWindows`. Not attempted |
| An agent's own `send` works under the sandbox | **Partly supported**: the default mailbox `docs/agent-mail/` is inside the workspace, so `workspace-write` allows it; a shared `AGENT_MAIL_DIR` outside the workspace (the documented way to join worktrees) would need approval or a wider sandbox. Under `read-only` (the documented non-interactive default) `send` cannot write at all. Unverifiable beyond the documentation |
| `install.py` itself | The installer is run by the human, outside the sandbox, so the protected `.codex` is not a problem; an agent asked to re-run it from inside `workspace-write` would be refused writing `.codex/` |

## Bounded validation procedure for issue #21

For Tyler or a CI job that has Codex and an account. It extends the five steps already in
[hosts.md](hosts.md) with the unverifiable items above.

Prerequisites: Codex CLI installed and signed in; a throwaway git repository; Python 3.10+;
no global config touched (use a scratch `CODEX_HOME` if Codex supports one: not confirmed).
Cost: a few model turns, expected under US$0.50 at a small model, 10 to 15 minutes by hand.
Not for CI on a shared account without a spend cap.

1. `python3 install.py <repo> --adapter codex`; commit nothing yet. Record `codex --version`.
2. Start `AGENT_MAIL_IDENTITY=bob codex` in the repo root. Run `/hooks`; record whether the
   entry is listed as needing review. Prompt once **before** trusting: ask "is there agent
   mail for you? quote its subject, run no command" with a control mail already filed
   (`NOTICE`, subject `control <random>`). **Expect no token** (trust gate works).
3. Trust it (and the project if asked). Repeat. **Pass** only if the answer quotes the token
   with tools denied or no command run (otherwise it polled).
4. Environment: repeat with `AGENT_MAIL_IDENTITY` unset: no token. Then start from a
   subdirectory with it set: token expected. Record what happens if the variable is only set
   by `env` in `config.toml` rather than the shell.
5. Non-interactive: `AGENT_MAIL_IDENTITY=bob codex exec "<same prompt>"`. Record token or not
   (documentation silent; either result is an answer).
6. Sandbox: in step 3's session ask the agent to answer the ASK with `send`. Record whether
   the default `workspace-write` allows it unprompted, and the approval text if not.
7. Worktree: commit the install, `git worktree add`, start Codex there, repeat step 3 with a
   mail in the worktree's own mailbox.
8. Break it: rename `tools/agent-postbox`; the next prompt must still go through.

Outcome that counts as **pass for issue #21**: steps 3, 4 (both parts) and 8 as stated.
Steps 5 to 7 are recorded observations, not gates. Any other result is a finding and
`docs/hosts.md` stays "experimental, not run in Codex". A CI job could automate steps 2 to 4
and 8 with `codex exec` only if step 5 shows hooks run there and a spend cap exists.

What this procedure cannot show: interactive-only UI behaviour of the trust prompt, other
operating systems, or the IDE extension and cloud tasks.
