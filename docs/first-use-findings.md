# First-use findings (for the README owner)

Run 2026-10-15, macOS, zsh, Python 3.13, `claude` 2.1.300. The README Quickstart was
copy-pasted as written into a fresh directory with an empty `git init` project, against a
fresh `git clone` of the public repository. **Machine time only.** Human reading time was not
measured and no person was timed, so the "five minutes" claim in
[onboarding-first-exchange.md](onboarding-first-exchange.md) stays unsupported by any human
trial.

## Timings (one run each, seconds)

| Step | Before | After my changes |
|---|---|---|
| `git clone` (network) | 2.06 | 2.32 (local clone; network not repeated) |
| step 2 `run.py` demo | 0.49 | 1.44 (machine noise; code untouched) |
| step 3 `install.py --adapter claude-code` | 0.03 | 0.05 |
| first send (ASK) | 0.06 | 0.07 |
| first pickup (`inbox`; hook preview) | 0.06; 0.06 | 0.08 |
| first acknowledgment (ANSWER, once the id was extracted by hand) | 0.07 | n/a |
| total of the above | about 2.9 | about 4.0 |

The after column is not an improvement: my changes do not touch this path (the README is not
mine). It shows only that nothing regressed. The time is dominated by the network clone.

## Friction hit, README items (not fixed here)

1. **Step 4 cannot be pasted.** `--reply-to <the ASK's id>` has an apostrophe: zsh stops with
   `unmatched '` and runs nothing; with the apostrophe removed, `<...>` is a redirection.
   Either print the id into a variable (`ID=$(... | sed -n 's/^id: //p')` from the `send`
   output) or write `--reply-to ID_FROM_ABOVE` and say so.
2. Step 3 needs `/path/to/your/project` to be an existing git repository; the text says "(a
   git repository)" but the commands to make one are not given. Without `.git` the claude-code
   install succeeds with a warning; `--adapter codex` aborts with a clear message.
3. Step 5 (hook preview) requires `AGENT_MAIL_IDENTITY` and `CLAUDE_PROJECT_DIR` inline;
   works as pasted. The output ends with a `Reply:` line holding an absolute path; nothing says
   that is the spelling a `dontAsk` session needs a rule for (now in
   [hosts.md](hosts.md) and [troubleshooting.md](troubleshooting.md)).
4. Nothing in the Quickstart says to start `claude` from the project root; started in a
   subdirectory, the hook did not run (headless, measured: [hosts.md](hosts.md)). `install.py`
   now prints that for the claude-code adapter.
5. Step 2 is not needed to install; it is a demo and can be skipped.

## Edge cases tried

| Case | Result |
|---|---|
| project with an existing `.claude/settings.json` (model, permissions, env, a `PostToolUse` hook) | all keys kept, hook merged, `.bak` written |
| reinstall | `already registered (no change)`, no second backup, PARTICIPANTS kept |
| path with spaces | install, send and the registered command all worked (`$CLAUDE_PROJECT_DIR` is quoted) |
| not a git repository | claude-code: installs with a warning; codex: refuses, nothing changed |
| documented removal, zsh | existing-settings project restored byte for byte; spaces project cleaned to nothing. The block exited 1 when `.claude` was not empty (final `rmdir`); fixed in [troubleshooting.md](troubleshooting.md) |
| missing troubleshooting entries | start-in-subdirectory and linked-worktree rows added |
