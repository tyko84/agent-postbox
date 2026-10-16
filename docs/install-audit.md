# Installer lifecycle audit (phase 3)

Date: 2026-10-15. Base: `main` at `27686ca`, `agent-postbox` 0.5.0, macOS (Darwin, arm64),
CPython 3.13.7 from Homebrew, zsh and bash. Every case ran for real in a throwaway directory
under `$TMPDIR` (`<T>` below), with the installer and the documented removal steps taken
from this checkout. Nothing was run against a real project. The adapter is called
`claude-code` (`--adapter claude` is rejected by argparse with the list of valid choices).

"Tree hash" means: the relative path and SHA-256 of every file, plus every directory, plus the
permission bits, of the project excluding `.git/`, compared before the install and after the
removal.

## Matrix

PASS = behaved as the safety rules in `install.py` and the docs say. "Note" = passes, with an
observation worth knowing (details under Observations). The two adapters are
`claude-code` (`.claude/settings.json`) and `codex` (`.codex/hooks.json`).

| # | Case | claude-code | codex | Evidence |
|---|------|-------------|-------|----------|
| 1 | Fresh project, no git, no agent config | PASS (warns "no .git", installs, exit 0) | PASS (refused before copying, exit 2, tree empty: the hook needs `git rev-parse`) | `install.py <T>/p --adapter codex` -> `ABORT: --adapter codex needs a git repository ... Nothing was changed.` |
| 2 | Fresh git project, no agent config | PASS, 1 hook | PASS, 1 hook | exit 0, `registered the ... prompt-submit adapter` |
| 3 | Existing config: unrelated `PreToolUse` and `UserPromptSubmit` hooks, `permissions.allow` rules and `defaultMode`, odd key order, unicode, `null` value | PASS | PASS | Key order `zeta, permissions, hooks, alpha, env, unicode` unchanged; other hooks and the `permissions` object equal to the original; our entry appended after theirs; `.bak` byte-identical to the original |
| 4 | Install never adds a `permissions` rule or changes the mode | PASS | PASS | Fresh install: `'permissions' in settings.json` False, `"allow"` False, `defaultMode` False (all four path variants). With a user file: the `permissions` object compared equal before and after |
| 5 | Existing `.codex/hooks.json` (also run for the claude file) with another `UserPromptSubmit` hook | PASS | PASS | 1 of ours, theirs kept, `.bak` equals the original |
| 6 | Path with spaces and unicode (`my proj ünï ☃ 日本`) | PASS | PASS | Command is `$CLAUDE_PROJECT_DIR/...` or `$(git rev-parse --show-toplevel)/...` (the path is never written into it); the command, run with `sh -c` from a subdirectory, delivered a real NOTICE (`hello`) to `agent-b`; the printed `cd '<path>' && python3 tools/agent-postbox/selftest.py` is shell-quoted |
| 7 | Single quote (`it's a proj`), also `a$b`c"d` and `a (b); c&d` | PASS | PASS | Same hook-delivery check; selftest line quoted as `'it'"'"'s a proj'` |
| 8 | Reinstall (same arguments, twice) | PASS: second run changes nothing (tree diff empty), still 1 hook | PASS | `claude-code adapter already registered (no change)` |
| 9 | Upgrade from an older `tools/agent-postbox` (v0.2.0, v0.3.1, v0.4.0 installed by their own `install.py`, a message filed, `PARTICIPANTS.md` edited) | PASS | n/a (no codex adapter before 0.5.0) | Payload files replaced; mailbox, message and the edited `PARTICIPANTS.md` kept; the old command (no `|| true`) upgraded in place with a `.bak`; a second run changes nothing; `doctor` runs (its only FAIL is the missing canary) |
| 10 | Interrupted: target not writable (`tools/` mode 555; `docs/` mode 555) | PASS (Note 2) | PASS (Note 2) | `ABORT: cannot write (<T>/p/tools/agent-postbox): Permission denied. Steps listed above may already be done ...`, exit 1, no traceback; config untouched; re-run after `chmod` completes the install |
| 11 | Interrupted: config directory not writable (`.claude/` mode 555) | PASS (Note 2) | PASS (Note 2) | Backup is written first and fails: `cannot write (.claude/.settings.json.bak.<pid>.tmp)`; config byte-identical; no stray temp; re-run completes |
| 12 | Interrupted: SIGKILL just before the atomic replace of the config | PASS (Note 3) | PASS (Note 3) | Exit -9; `settings.json` is still the original; `.bak` exists and equals it; re-run registers (1 hook) and writes `.bak.1`; `.bak` still the original |
| 13 | Interrupted: SIGKILL in the middle of the payload copy | PASS | PASS | Re-run exit 0 and registers; the copy is idempotent (`copy2` is not atomic, see Note 3) |
| 14 | Read-only settings file (mode 444) | Note 1 | Note 1 | Install succeeded, file replaced by rename, mode 444 kept, `.bak` made |
| 15 | Invalid JSON: garbage, `//` comments, trailing comma | PASS | PASS | exit 2 `ABORT: ... is not valid JSON (...). Refusing to touch it`; tree identical to before (diff empty), file byte-identical |
| 16 | Valid JSON that is not an object (`[1]`), `{"hooks": null}` | PASS | PASS | exit 2, refused, nothing changed |
| 17 | `--check` on invalid JSON, and on a fresh project | PASS | PASS | exit 0, tree diff empty |
| 18 | Symlinked `settings.json` | PASS | not run | Link kept, target got the hook, one `.bak` |
| 19 | Documented removal (`docs/troubleshooting.md`, "Removing it"), run in zsh, exact steps extracted from the doc | PASS in 7 scenarios | PASS | Tree hash and modes equal before and after in: empty project with claude-code, with codex, with both, with no adapter, a project with unusual existing `.claude/settings.json` and `.codex/hooks.json` plus `README.md` and `docs/notes.md`, reinstall twice, a spaces-and-unicode path. Existing files "restored ... byte for byte from `*.bak`"; files that held nothing else were removed |
| 20 | User files preserved byte for byte | Note 4 | Note 4 | Files the installer does not own: untouched. Settings: only through the removal restore. Existing `PARTICIPANTS.md` and messages: kept on reinstall and upgrade |
| 21 | Built artifacts: reproducible build (`scripts/repro_build.py`, hash-pinned build tools in a venv) | PASS | PASS | See below |
| 22 | README Quickstart steps 2-5 (copied literally, `/path/to/your/project` substituted, `<the ASK's id>` replaced by a command extracting the id), fresh directory, `set -e` | PASS in bash and zsh | | 0.75 s and 0.76 s wall for steps 2-5 together (step 2 is the demo run). Step 5's hook preview printed `Agent mail - 1 live message(s) addressed to 'agent-a'` with the ANSWER. Step 1 (`git clone`) skipped: no network was needed or used for it; a symlink to this checkout stood in |

### Built artifacts (row 21)

```
python3 -m venv <S>/bvenv
<S>/bvenv/bin/python -m pip install --require-hashes -r requirements-build.txt   # build 1.6.1
<S>/bvenv/bin/python scripts/repro_build.py --outdir <S>/pb-out
  -> tyko84_agent_postbox-0.5.0-py3-none-any.whl, tyko84_agent_postbox-0.5.0.tar.gz, SHA256SUMS
```

- The wheel contains `agent_mail.py` and metadata only, as documented: it has no `install.py`,
  so "install into a project" from a wheel is not a path (README says so). Installed into a
  fresh venv with `pip install --no-index`, `agent-postbox --version` prints `agent-postbox 0.5.0`.
- The sdist contains `install.py`, `hooks/`, the spec and the payload. Unpacked into its own
  directory and run from there for both adapters: the resulting project tree is identical to
  the one a run from this checkout produces (empty diff), `selftest.py` in the project exits 0
  (`all checks passed (3 spec-checkout-only check(s) skipped)`), and the documented removal
  restores the tree hash.
- This needed the network once, to install the hash-pinned `build` and fetch the pinned
  setuptools wheel; everything else was offline. The `--check` double build (reproducibility)
  was not run here; `repro_build.py` builds once without it.

## Observations (none is a safety-rule violation; one defect fixed, below)

1. **A read-only settings file is replaced, not refused.** The installer writes a sibling temp
   file and renames it, which needs write access to the directory, not the file. The mode (444)
   is kept and a `.bak` is made, so nothing is lost, but a file the user deliberately locked
   gets rewritten. Not changed: refusing would be a behaviour decision (some teams lock the
   file because a tool manages it), and the output names the `.bak`.
2. **A partial install is possible when a later step cannot write.** The payload is copied and
   the mailbox created before the config is touched, so with an unwritable `.claude/` the tool
   is installed and the hook is not. Exit 1 and the message say so and say to re-run; the
   re-run was verified to finish the job. `claude_refusal` could check writability up front;
   not done.
3. **SIGKILL leaves a temp file.** `write_atomic` unlinks its temp on any exception, but a kill
   cannot run that, so `.claude/.settings.json.<pid>.tmp` (or the `.codex` equivalent) stays,
   with the old file intact. A later install does not sweep it, so `git add .claude` would pick
   it up. The payload copy uses `shutil.copy2`, which is not atomic: a kill inside it can leave
   a truncated `agent_mail.py`, which a re-run overwrites. Also, a kill after the backup but
   before the write makes the re-run write `.bak.1` as a second copy of the original. All
   recoverable by re-running; not changed.
4. **Settings are re-serialised, not patched.** The merged file is `json.dumps(indent=2)`:
   tabs and 4-space indents become 2 spaces, CRLF becomes LF, a BOM is dropped, non-ASCII
   becomes `\uXXXX` escapes, `1e2` becomes `100.0`, and a duplicate key keeps only its last
   value. Data and key order survive (verified); the byte-exact original is the `.bak`, and the
   documented removal restores it byte for byte. Expect diff noise in a committed settings
   file.
5. **Local edits to installed tool files are overwritten without a backup.** Reinstall and
   upgrade replace `tools/agent-postbox/*` (`agent_mail.py`, `PROTOCOL.md`, ...). Intended
   ("install / update"), but only settings files get `.bak`.
6. **The removal snippet exits 1 when its last command has nothing to do.** In
   `docs/troubleshooting.md`, step 4 ends with `rmdir .claude .codex 2>/dev/null`, whose status
   is the exit status of the whole block. The state is correct in all seven scenarios; only the
   status is misleading (it shows as non-zero even when everything worked). Doc file not
   touched per the audit brief.
7. **The codex adapter reports bad JSON less specifically** than the claude-code one
   ("is not readable JSON (JSONDecodeError)" versus the parser's message with line and column).

## Defects

| # | Defect | Reproduced | Fixed |
|---|--------|------------|-------|
| D1 | Reinstalling with a different `--dest` copies the tool to the new place but leaves the registered hook pointing at the old one, and prints only `<adapter> adapter already registered (no change)`, which reads as "the new copy is wired". | `install.py P --adapter codex`, then `install.py P --adapter codex --dest vendor/pb`: hook command still `.../tools/agent-postbox/hooks/agent_mail_check.py`, output has no hint. Same for `claude-code`. | Yes. The message now appends `; NOTE: its command does not point at <dest>/, so the hook still runs the tool from where it was first installed -- edit the command in <file> if you moved the tool` when the registered command does not contain `<dest>/hooks/agent_mail_check.py`. The config is still not rewritten (a hand-edited command is the user's), and the text is unchanged when the destination matches. Regression test: `CodexInstall.test_a_different_dest_on_reinstall_is_reported_not_silently_ignored` in `test_adapters.py` (both adapters); it fails on the old `install.py` (2 failures) and passes with the fix. |

No other defect was reproduced. In particular: no case damaged an existing file, left a
half-written config, added a `permissions` entry, stacked duplicate hooks, or failed to
return to the original tree hash through the documented removal.

## Not verified

- Windows, Linux, and shells other than zsh and bash (the removal was run in zsh; the
  Quickstart in both). fish, PowerShell and cmd were not run.
- The hook inside a real Claude Code or Codex session (the hook command was run with `sh -c`
  and the host's environment variables, which is what the host does; trust prompts in Codex
  were not exercised).
- A concurrent second installer run, and a full disk.
- `python3 -m pip install` of the sdist into a venv (the sdist was unpacked and its
  `install.py` run instead).

## Lifecycle re-run, 2026-10-16 (cycle 4)

Throwaway projects under a scratch directory with `HOME` set to a temp dir; a tree snapshot
(path, mode, SHA-256 of every file, symlink targets; `.git/` excluded) before and after each
case. Main at `c832fe1`. Machine-run, macOS, Python 3.13; Linux not run here (CI covers it).
"v0.5.0" means `git archive v0.5.0` unpacked and its own `install.py` run.

| # | Case | Result | Tree and settings diff |
|---|------|--------|------------------------|
| L1 | Reinstall, same arguments (`--adapter claude-code`) | exit 0, `already registered (no change)` | Empty tree diff |
| L2 | Upgrade v0.5.0 to current over a `settings.json` with `permissions`, another `UserPromptSubmit` hook and a `Stop` hook | exit 0 | Only the four payload files that changed between the versions differ; `settings.json` byte-identical; `settings.json.bak` is the pre-install original; both other hooks and the `permissions` object kept in order |
| L3 | Documented removal (`troubleshooting.md`, zsh) after claude-code and codex on a project with its own `settings.json` | exit 0 | Snapshot equals the pre-install one; `settings.json` restored byte for byte from `.bak`; `.codex/hooks.json` removed |
| L4 | Read-only project dir; read-only `tools/` | exit 1, `ABORT: cannot write (<path>): Permission denied ...` | Empty diff (nothing was reached) |
| L5 | Read-only `.claude/` holding a `settings.json`; read-only `docs/` | Before the fix: exit 1, **half-installed** (tool copied; mailbox too for `.claude/`; no hook). Fixed: exit 2, `<dir> is not writable ... Nothing was changed.` | Before: tool and mailbox files added. After: empty diff, and a re-run after `chmod` completes |
| L6 | Malformed `settings.json` (`{"hooks": {oops`); `[1,2]` | exit 2, names the file, the parser's line and column, `fix it by hand, then re-run`, `Nothing was changed.`; `--check` exits 0 and prints `WOULD REFUSE` | Empty diff |
| L7 | `.claude` a symlink to a directory outside the project | exit 2, `is a symlink leading outside the project; remove it` | Empty diff; the outside directory untouched |
| L8 | `settings.json` itself a symlink to a file outside | exit 0 (accepted in `docs/threat-model.md`): written through, the link kept, `.bak` a regular file | The linked file gains the hook; nothing else |
| L9 | `tools/` a symlink out of the project / into the project | out: exit 2 `leads outside the project through a symlink`; in: installs through the link | Out: empty diff |
| L10 | `--adapter cursor` (unsupported host) | argparse exit 2, usage and `invalid choice: 'cursor' (choose from claude-code, codex)` | Nothing created |
| L11 | `--adapter codex` with no git repository | exit 2, `needs a git repository ... Nothing was changed.` | Empty diff |
| L12 | Nonexistent project path | exit 2, `not a directory: <path>` | n/a |
| L13 | Missing dependency: `install.py` run without `PARTICIPANTS.template.md`, `selftest.py` or the hook script beside it | Before the fix: exit 1, `cannot write (<its own source file>): No such file` (wrong cause, wrong advice) and a **partial** tool copy. Fixed: exit 2, the missing files named, nothing copied | Before: partial `tools/agent-postbox/`. After: empty diff |
| L14 | No `python3` on `PATH` (installer run by absolute path, `PATH=/nonexistent`) | exit 0; the hook command uses the installer's interpreter path | n/a |
| L15 | An older registration with bare `python` where only `python3` exists | `repaired the claude-code adapter (...)`, `.bak` made | Only that command changes |

Exit codes: a refusal is 2 in every case above; 1 is reserved for an OS-level write failure
the up-front checks cannot foresee (read-only mount, ACL, full disk). Both name the path and the
next step.

Two defects, each with a minimal repro, an expected/actual pair and a regression check
(`test_adversarial.py`, install section), each in its own PR: L5 (unwritable target leaves a
half install; PR #79) and L13 (a copy of `install.py` without its payload reports a write
failure and leaves a partial copy; PR #80). Not reproduced as defects: everything else in the
table.
