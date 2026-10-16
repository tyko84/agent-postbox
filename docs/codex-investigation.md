# Codex: static investigation and a bounded validation plan (issue #21)

**Documentation read only.** Codex CLI is not installed on the machine this was written on
(`which codex`: not found, re-checked 2026-10-15), and nothing here was run in Codex. Read on
2026-10-15 and re-fetched the same day (see "Refresh" below). Nothing below says the Codex adapter
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
| Hooks in `codex exec`, IDE extension, cloud | `codex exec`: still not stated. Refresh: CLI/Desktop "full hook support"; IDE extension uses the local environment under the extension's sandbox; Work Cloud: command hooks unsupported (MCP hooks by admin policy only) |
| Environment of the hook process (does it see `AGENT_MAIL_IDENTITY`?) | Not stated |
| Operating systems | Hooks "work on macOS, Linux, Windows", with a `commandWindows` override. CLI: macOS/Linux installer; Windows via npm or Homebrew; WSL mentioned |
| Approvals | `on-request`, `never`; `untrusted` is deprecated in favour of a per-project `trust_level`. Other pages (a search summary, so lower confidence) give the non-interactive default as read-only with no prompts possible |
| Sandbox | `read-only`, `workspace-write` (the default), `danger-full-access`. Network off by default. macOS Seatbelt, Linux `bwrap` + seccomp |
| Protected paths | In `workspace-write`, `.git` (including gitdir pointers), `.codex`, `.agents`, `.aws` stay read-only, recursively |
| Sessions | `codex resume`; `session_id` is in the hook payload |
| Worktrees | Refresh: "Hooks resolve relative to the active worktree's git root". Whether an untracked `.codex/` or an uncommitted tool is found is not stated |

## Refresh (fetched 2026-10-15)

Pages: <https://learn.chatgpt.com/docs/hooks>, <https://learn.chatgpt.com/docs/non-interactive-mode>,
<https://learn.chatgpt.com/docs/agent-configuration/agents-md> (the `developers.openai.com/codex/...`
URLs answer 308 to these). Summaries come from a fetch tool, not verbatim page copies. Changes
against the earlier reading:

* `SessionStart` sources are now `startup`, `resume`, `clear`, `compact` and `fork`.
* New: handler type `mcp_tool` beside `command`; `async: true` hooks (output arrives "at the next safe
  turn boundary", cannot block); config precedence System, User, Cloud, Project.
* Worktrees: "Hooks resolve relative to the active worktree's git root" (was unstated).
* Surfaces: command hooks are unsupported in Work Cloud; the IDE extension follows its own sandbox.
* `codex exec`: read-only sandbox by default; `--full-auto` is gone (`--approve-for-me`); the page
  still does not say whether hooks run there. `--ignore-user-config` exists.
* Unchanged and still unstated: whether the hook process inherits the launching shell's environment
  (`AGENT_MAIL_IDENTITY`), and non-zero exit codes other than 2 on this event.
* Trust is per exact definition hash, so the command string (including `|| true`) is part of it: an
  installer upgrade of the command needs re-trusting.

## Re-confirmation, 2026-10-16 (Codex is still not installed; nothing was run)

Every row of this page and of the Codex section of [hosts.md](hosts.md) was re-checked against the
current public pages on **2026-10-16**. The pages were read through a fetch tool that summarises
(it is not a verbatim copy, and on one page its summary contradicted itself), so every row marked
"confirmed" is the tool's extraction, to be re-read at the source before anything is built on it.
**Not run in a real Codex.**

Sources fetched 2026-10-16: hooks <https://learn.chatgpt.com/docs/hooks>; AGENTS.md
<https://learn.chatgpt.com/docs/agent-configuration/agents-md>; approvals and security
<https://learn.chatgpt.com/docs/agent-approvals-security>; non-interactive mode
<https://learn.chatgpt.com/docs/non-interactive-mode>; config
<https://learn.chatgpt.com/docs/config-file/config-basic> (the `developers.openai.com/codex/local-config`
URL answers 308 to it).

| Claim in these docs | Status 2026-10-16 | Source |
|---|---|---|
| `UserPromptSubmit` plain stdout is added as developer context; exit 2 or `{"decision":"block"}` blocks | confirmed | hooks |
| Config in `~/.codex/hooks.json`, `~/.codex/config.toml`, `<repo>/.codex/hooks.json`, `<repo>/.codex/config.toml` | confirmed (plugin-bundled `hooks/hooks.json` was not in this pass: unverified) | hooks |
| JSON shape: event, matcher groups, handlers with `type`, `command`, `timeout`, `statusMessage` | confirmed (nesting by the page's own description) | hooks |
| New or changed hooks need review and trust in `/hooks`, recorded against the hook's hash, skipped until trusted | confirmed | hooks |
| Project-local hooks load only when the project `.codex/` layer is trusted | confirmed | hooks |
| Commands run with the session `cwd` | confirmed ("child processes with the session's working directory") | hooks |
| Hook process inherits `AGENT_MAIL_IDENTITY` | **still not stated**: the page says only "child processes". Unverified | hooks |
| Default timeout 600 s | confirmed ("for most hooks"; `SessionEnd` and `Interrupt` default to 1 s, 3 s maximum: new) | hooks |
| `additionalContextLimit`, about 2,500 tokens, overflow saved to disk | confirmed | hooks |
| `SessionStart` sources `startup`, `resume`, `clear`, `compact`, `fork` | confirmed | hooks |
| Hooks on by default; `[features] hooks = false` disables | confirmed | hooks |
| Exit codes other than 2 on this event | **still not stated** | hooks |
| Command hooks unsupported in Work Cloud | **wording changed**: the page now says "Work Cloud without local access ... do not use these enterprise hooks". Treat as unverified | hooks |
| IDE extension, Windows (`commandWindows`), worktree "resolve relative to the active worktree's git root", full stdin field list, the event list | **not re-fetched in this pass**; last read 2026-10-15; unverified today | |
| `codex exec` runs hooks | **still not stated** ("consult the hooks reference or test") | non-interactive |
| `codex exec` defaults to a read-only sandbox; `--full-auto` gone; `--approve-for-me`; `--ignore-user-config` | confirmed; `--ignore-rules` (skip execpolicy rules files) and `--sandbox workspace-write` are also listed | non-interactive |
| Default sandbox `workspace-write` with `on-request` approvals; network off | confirmed; also new: a folder that is not under version control is suggested `read-only` | approvals |
| Protected paths `.git` (and gitdir pointer targets), `.agents`, `.codex`, `.aws`, recursive | confirmed | approvals |
| An `AGENT_MAIL_DIR` outside the workspace needs approval under `workspace-write` | **refined**: the workspace is "the current directory and temporary directories like `/tmp`", so a mailbox under `/tmp` is writable; elsewhere the page names no setting (a `sandbox_workspace_write.writable_roots` key appears only in search snippets, not on a fetched page): unverified | approvals, search |
| "`untrusted` is deprecated in favour of a per-project `trust_level`" | **stale or unverified**: the approvals page now lists `on-request`, `never` and granular policies and does not mention `untrusted` or `trust_level` | approvals |
| AGENTS.md read once per run, 32 KiB combined cap (`project_doc_max_bytes`), global then root-to-cwd | confirmed; `AGENTS.override.md` takes precedence at both scopes (new); Codex home is `CODEX_HOME`, default `~/.codex` | AGENTS.md |
| `shell_environment_policy` controls the environment forwarded to spawned commands | stated by the config page; whether it governs the hook process is **unstated** (the fetch summary contradicted itself) | config |

Nothing in this table makes the Codex adapter "work"; two items the plan depends on (environment
inheritance, hooks in `codex exec`) are still unstated by the vendor.

### Static review of what `install.py --adapter codex` writes

Read, not edited (`install.py`, `hook_command`, `codex_refusal`, `CODEX_TRUST`; a project installed with
both adapters in a scratch directory produced the file below).

| Aspect | What it does | Verdict |
|---|---|---|
| Path | `<project>/.codex/hooks.json` | matches the documented repo location |
| Shape | `{"hooks":{"UserPromptSubmit":[{"hooks":[{"type":"command","command":"..."}]}]}}` | matches the documented nesting; no `timeout` (600 s default applies) and no `statusMessage` |
| Command | `python3 "$(git rev-parse --show-toplevel)/tools/agent-postbox/hooks/agent_mail_check.py" \|\| true` | quoting is sound: the substitution's output is not re-split, so a repository path with spaces or quotes is fine; `--dest` is limited to `[A-Za-z0-9._/ -]`, so nothing else can reach the shell |
| Refusals | not a git repository, unquotable `--dest`, `.codex` is a file, `config.toml` already defines hooks inline, unmergeable `hooks.json` | sensible; the git requirement matches the command |
| Trust | prints `CODEX_TRUST` (start codex, `/hooks`, review and trust, run the control) | accurate; a changed command needs re-trusting (hash) |

Gaps, none of them a defect in a file this task could fix:

1. **Identity binding is not installed.** Nothing sets or checks `AGENT_MAIL_IDENTITY` for Codex; if
   the hook process does not inherit it, delivery is silent forever. Mitigation today: the manual
   check in step 4 of the procedure below and `doctor`'s identity line.
2. **A nested git repository defeats the command.** Started inside a nested repo, `git rev-parse
   --show-toplevel` names the inner repo, the script is not found, python exits 2 and `|| true` hides
   it: silent. (Claude Code never runs the hook there at all; see [host-findings-3.md](host-findings-3.md).)
3. **No `AGENTS.md` fallback is written.** The installer does not add the polling instruction; the
   snippet is in [hosts.md](hosts.md) ("Codex: manual pickup").
4. **`|| true` makes every failure silent** by design (a moved tool must not block a prompt). The cost is
   that a hook that cannot start looks like an empty inbox; only the real-host control tells them apart.
5. **Windows**: the command is POSIX shell; `commandWindows` is not written.
6. **Protected `.codex`**: an agent inside `workspace-write` cannot edit its own hook file, which is
   the desired property; the human runs `install.py`.

### Smallest viable Codex integration

1. **Today, no code**: the `AGENTS.md` snippet plus `AGENT_MAIL_IDENTITY=<id> codex` (hosts.md). Works
   as polling, identity bound by the shell that starts Codex, needs no trust step.
2. **After one real run passes steps 3, 4 and 8** of the procedure below: the existing hook, unchanged.
3. Only if the real run shows the variable is not inherited: a handler or wrapper that sets it
   (for example `AGENT_MAIL_IDENTITY=<id> python3 ...` in the registered command, one identity per
   project clone), or a `[shell_environment_policy]` setting if Codex documents that it covers hooks.
   Not built: unobserved.

### Prerequisites for the real run (issue #21)

About 15 minutes by hand; needs the owner's OpenAI account (nothing here can supply one).

* Codex CLI installed and signed in with the owner's OpenAI account (subscription or API key; a few
  model turns, under about US$0.50); `codex --version` recorded.
* Python 3.10+ and `git`; a throwaway git repository; no global Codex config touched (a scratch
  `CODEX_HOME` if Codex honours it, which the AGENTS.md page implies but does not promise).
* This repository's `install.py` and the procedure below; two terminals (one to send the control).
* The owner to answer the `/hooks` trust prompt and any project-trust prompt interactively.
* Record: the Codex version, the exact `/hooks` text, whether `AGENT_MAIL_IDENTITY` reached the hook
  (the pass criterion in step 4), and whether `codex exec` runs the hook (step 5).

## Compatibility matrix

Status: S supported by the documentation, U unsupported by it, V unverifiable (not stated, or only
testable in the real host). Nothing is "works": nothing was run.

| Capability | Status | Source |
|---|---|---|
| Per-prompt context injection from a command hook (plain stdout) | S | hooks page, `UserPromptSubmit` |
| Same at session start, resume, after compaction | S | hooks page, `SessionStart` sources |
| Project-local registration in `<repo>/.codex/hooks.json` | S | hooks page, config locations |
| Delivery without user review | U | trust requires `/hooks` review of the exact hash |
| Hook finds the repo tool when Codex starts in a subdirectory | S for the `rev-parse` pattern (the page recommends it); `git` on the hook's PATH is V | hooks page |
| Linked worktrees | S for resolution; V for an uncommitted `.codex/` or tool | hooks page |
| `AGENT_MAIL_IDENTITY` visible to the hook | V | not stated; the most likely silent failure |
| Hook runs in `codex exec` | V | not stated |
| IDE extension | V | "uses local environment", hooks not itemised |
| Cloud tasks | U | "command hooks unsupported" in Work Cloud |
| Agent can `send` under the default sandbox | S inside the workspace; U for an external `AGENT_MAIL_DIR` and for `exec`'s read-only default | approvals and non-interactive pages |
| Mail arriving during a long turn | U | no prompt-boundary event fires mid-turn (`PostToolUse` is not shipped) |
| Standing instruction file read at session start | S | AGENTS.md: once per run, 32 KiB combined cap, root-to-cwd concatenation |
| Windows | V | `commandWindows` exists; our command is POSIX |

## Smallest useful Codex step: bounded proposal

Option A (documentation only): a short Codex paragraph in `docs/hosts.md` telling a Codex user to put in
`AGENTS.md`: "At the start of a session and before acting on shared state run
`python3 tools/agent-postbox/agent_mail.py inbox`", to set `AGENT_MAIL_IDENTITY` before `codex`, and the
sandbox caveat for `send`. No code, no trust step; it is explicit polling with its known weakness.
Complexity trivial; test effort a docs check (`test_adapters.py` already pins the hosts table).

Option B (exists): `install.py --adapter codex`. It earns the word "works" only after the procedure
below passes steps 3, 4 and 8. Real-run cost: 10 to 15 minutes by hand, under US$0.50, an OpenAI account.

Option C (new code): also register `SessionStart`. About 30 lines in `install.py` plus idempotence tests
(small to medium). Not justified before B passes: it adds a second trust entry with no evidence of need.

## Recommendation

Codex support work is **not justified now beyond Option A**. No Codex runtime exists here, and the three
decisive questions (environment inheritance, `codex exec`, trust-gate behaviour) can only be answered by
running Codex. Keep the host labelled "experimental, never run in Codex". If the owner has an account,
run the procedure below once; its outcome decides B and C.

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
