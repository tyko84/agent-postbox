# Windows

**Windows is not a supported platform.** The README says so and this page does not change that. It
records what was observed when the tool and installer were run by hand on native Windows 11 (Python
3.12 from python.org and Python 3.13 from the Microsoft Store, Windows PowerShell 5.1, cmd.exe, Git
for Windows with Git Bash, a non-elevated user, no model, no network, no real Claude Code session).
It claims nothing beyond that. No person was timed; the timings are machine time.

Each line carries one status label:

* **Verified**: run on Windows as written, with the output described.
* **Works but unverified in a real host**: the tool side works; the host (Claude Code) side was not run.
* **Unsupported**: known not to work, or not intended to.
* **Untested**: not tried.

Paths below use `<project>` for your project directory and `%TEMP%` for the temporary directory.
Use `python3` as the README does; on Windows it resolves to the Store build or to a `python3.exe`
you provide, and `py -3` (the launcher) is a working substitute for the CLI. The registered hook
command, however, calls `python3` by name (see [Real Claude Code hook](#real-claude-code-hook)).

## Native CLI

* Verified: `agent_mail.py` `doctor`, `canary`, `send` (NOTICE, ASK, ANSWER with `--reply-to`, CLAIM),
  `inbox`, `list --live`, `show`, `status` and the pickup hook script all run on Python 3.12 and 3.13.
* Verified: a first exchange (install, ASK, `inbox`, ANSWER, `inbox`) took about 1 second of machine
  time in Git Bash and the whole PowerShell walkthrough (install through hook preview, about 20 commands)
  about 3.4 seconds. A person needs longer.
* Verified: a second CLAIM on a scope held by another sender is refused: `scope 'src/x' is already held by
  agent-a (<id>, until <time>)`, exit 3, nothing written.
* Verified: nothing needed elevation. Everything ran in a non-elevated shell and wrote only inside the
  project (the tool copy, the mailbox, `.claude/settings.json` and its `.bak`).
* Verified: a project path containing a space works for the installer, the CLI and the hook script.
* Verified: `selftest.py` passes on Windows (`all checks passed (6 spec-checkout-only check(s) skipped)`
  in an installed copy). It took about 50 seconds, not the 20 the other docs state for POSIX.
* Verified: files are written with LF line endings and UTF-8.
* Verified: a `send` killed (TerminateProcess) while waiting on stdin left nothing behind: `doctor`
  reported `stale_tmp: 0` and the mailbox held no partial file. A kill during the final write was not
  exercised.

## Installer

* Verified: `python3 <clone>\install.py <project> --adapter claude-code` takes about 0.3 s and prints
  the files it created. It copies the tool to `tools/agent-postbox/`, creates `docs/agent-mail/` and
  writes `.claude/settings.json`.
* Verified: re-running is idempotent. The second run says `claude-code adapter already registered (no
  change)` and `kept existing docs/agent-mail/PARTICIPANTS.md`, and writes no new `.bak`.
* Verified: where a `.claude/settings.json` already exists the installer keeps your keys, adds the
  hook and leaves the original as `settings.json.bak`.
* Verified: upgrade is the same command: a locally modified `tools/agent-postbox/agent_mail.py` was
  replaced by the clone's copy; the mailbox and `PARTICIPANTS.md` are kept.
* Verified: with no `python3` or `python` on PATH the installer refuses before changing anything:
  `ABORT: no Python 3.10+ interpreter on PATH for the committed hook command (python3: not found;
  python: not found); install Python 3.10+ or put it on PATH, then re-run. Nothing was changed.`
  (exit 2). A `python3` that cannot be run gives `python3: cannot run` in the same message.
* Untested: the Microsoft Store "python.exe" stub that opens the Store when Python is not installed
  (it is found on PATH, so the installer would probe it; the expected result is the
  `cannot determine version` refusal).
* Untested: an interpreter older than 3.10 on Windows (none installed here). The version check is
  the same code on every platform.
* Verified: git is not needed to install or to use the CLI. With no `git` on PATH and no `.git` in the
  project, the installer warns that mailbox discovery stops at the nearest mailbox or `.git`
  and carries on; `send` and `doctor` worked.
* Verified: the installer's printed next steps now give a PowerShell, a cmd.exe and a Git Bash line for
  the identity variable, put a space-containing interpreter path in double quotes, and print the
  selftest `cd` and command on separate lines (no `&&`, no single quotes) when run on Windows. On
  POSIX the output is unchanged.
* Unsupported: the installer's own warning text uses the section sign (`PROTOCOL.md §8`); when its
  output is piped into Windows PowerShell 5.1 it can show as a replacement character. Cosmetic.

## Git Bash

* Verified: the whole documented flow works as written: `export AGENT_MAIL_IDENTITY=agent-b`,
  `AGENT_MAIL_IDENTITY=agent-b python3 ... inbox`, `--body-file body.txt`, `--body "one line"` and
  heredoc bodies.
* Verified: the registered hook command runs through Git Bash (`bash -c 'python3
  "$CLAUDE_PROJECT_DIR/tools/agent-postbox/hooks/agent_mail_check.py" || true'`) when
  `CLAUDE_PROJECT_DIR` is a Windows path.
* Verified: the removal steps in [troubleshooting.md](troubleshooting.md#removing-it) run unchanged in Git
  Bash and restored a prior `settings.json` byte for byte from its `.bak`. Nothing else remained.
* Verified: `python3` here is whatever Git Bash finds first on PATH (the Store build on the machine used).
* Verified: (a trap) a body piped on stdin (`cat file | ... send --body-file -`) is decoded with the
  Windows ANSI code page, not UTF-8. UTF-8 text such as an accented letter or an em dash is stored as
  garbled characters (double-encoded), silently. `--body-file PATH` reads UTF-8 correctly, and so does
  `PYTHONUTF8=1 python3 ... --body-file -`. Use one of those for any non-ASCII body. Plain ASCII is not
  affected.
* Verified: a UTF-8 BOM at the start of a `--body-file` is accepted and kept as the first character of
  the stored body.

## PowerShell 5.1

* Verified: the flow works in Windows PowerShell 5.1. Set the identity with
  `$env:AGENT_MAIL_IDENTITY = 'agent-a'` (the session's variable, not a persistent one; the POSIX
  `export` and the inline `NAME=value command` form do not exist here). `setx` would change future shells,
  not the current one.
* Verified: pipe a short ASCII body with `'text' | python3 tools\agent-postbox\agent_mail.py send ... --body-file -`.
* Verified: (a trap) `echo "text" > body.txt` in PowerShell 5.1 writes UTF-16 with a byte-order mark.
  `--body-file body.txt` then refuses: `cannot read --body-file 'body.txt': 'utf-8' codec can't decode
  byte 0xff in position 0: invalid start byte` (exit 2). Nothing is written. The first-exchange page
  uses `echo ... > body.txt`; that line fails in PowerShell 5.1.
* Verified: safe ways to make the file are `Set-Content -Encoding UTF8 body.txt 'text'` (writes a UTF-8
  BOM, accepted, kept in the body) and `'text' | Out-File -Encoding ascii body.txt`. Best of all, avoid
  the file: `--body-file PATH` for a file you made with an editor saved as UTF-8.
* Verified: (a trap) non-ASCII text piped from PowerShell 5.1 (`'caf'+[char]0xE9 | ... --body-file -`)
  arrives as `?`. The default pipe encoding is ASCII, so the character is lost before the tool sees it;
  the message is still written, with no warning. Use `--body-file PATH` for non-ASCII.
* Verified: `--body "text"` with a newline is refused (`refusing multi-line --body; pass --body-file PATH
  or --body-file - (stdin)`), the same as on POSIX.
* Verified: the printed `Full text of one:` and `Reply:` lines from the hook single-quote the
  tool path (`python3 'C:\...\agent_mail.py' show <id>`). PowerShell accepts that.
* Verified: `&&` does not exist in Windows PowerShell 5.1; use `;` or separate lines.
* Untested: PowerShell 7 (`pwsh`) was not present.

## cmd.exe

* Verified: `set AGENT_MAIL_IDENTITY=agent-b`, then `python3 tools\agent-postbox\agent_mail.py inbox`
  works, as do `send --body-file body.txt` (a file made by `echo ... > body.txt`; ASCII) and an
  `echo text| python3 ... send --body-file -` pipe (no space before the `|`, or the body gets one).
* Verified: the hook's `Full text of one:` and `Reply:` lines single-quote the tool path. cmd.exe does
  not treat single quotes as quoting, so copy those lines into PowerShell or Git Bash, or replace the
  quotes with double quotes.
* Verified: the registered hook command does not work in cmd.exe or PowerShell 5.1. In cmd.exe `$CLAUDE_PROJECT_DIR`
  is a literal, so Python is asked for `...\<project>\$CLAUDE_PROJECT_DIR\tools\...` and reports
  `can't open file ... No such file or directory`.
* Verified: the cmd.exe spelling of the same call, `python3 "%CLAUDE_PROJECT_DIR%/tools/agent-postbox/hooks/agent_mail_check.py"`
  with `CLAUDE_PROJECT_DIR` set, prints the pickup text. The installer does not write that form.
* Unsupported: inline `NAME=value command`, `export`, single-quoted strings, `&&` after a single-quoted `cd`.

## Real Claude Code hook

* Untested: **no real Claude Code session was started, so nothing about the hook running inside the host
  has been verified on Windows.** Everything above runs the hook script by hand.
* Works but unverified in a real host: the installer registers this command in `.claude/settings.json`:
  `python3 "$CLAUDE_PROJECT_DIR/tools/agent-postbox/hooks/agent_mail_check.py" || true`. That is POSIX
  shell syntax (`$VAR`, `||`, `true`). It runs correctly through Git Bash and fails under cmd.exe and
  PowerShell 5.1 as described above.
* Untested: what the Claude Code documentation says (read, not exercised here): hook commands run in Git Bash when
  Git for Windows is installed and in PowerShell otherwise, and a hook entry may carry a `"shell"` field such
  as `"powershell"`. Re-check the current Claude Code documentation; this page cannot vouch for it.
* Works but unverified in a real host: if Git Bash is installed, the registered command should be correct as written. This is unverified in a
  real session: do the positive control from [hosts.md](hosts.md) and look for the mail in the session.
* Untested: if PowerShell is the only shell: the registered command will not work there, and a failed hook looks
  like an empty inbox, so use polling instead (it needs no hook): set `$env:AGENT_MAIL_IDENTITY` and run
  `python3 tools\agent-postbox\agent_mail.py inbox` at the start of each turn. Or hand-edit the entry to a
  PowerShell form, for example `python3 "$env:CLAUDE_PROJECT_DIR/tools/agent-postbox/hooks/agent_mail_check.py"`
  with `"shell": "powershell"`; that spelling is **untested**, and `|| true` has no PowerShell 5.1 equivalent.
  Verified: after a hand-edit of that kind, re-running `install.py` printed `claude-code adapter already
  registered (no change)` and left `settings.json` byte for byte unchanged.
* Unsupported: the pickup adapter for Codex and Cursor on Windows (never run in those hosts anywhere).
* Verified: `doctor` reports `pickup_hook: registered` as soon as the settings entry and script exist. It
  does not run the hook, so it says nothing about the host's shell.

## Mailbox on a local NTFS volume vs network shares and OneDrive

* Works but unverified in a real host: a mailbox on a local NTFS volume, which is where every run above
  happened (the system temporary directory).
* Untested: a mailbox under a OneDrive, Dropbox or other synced folder. A sync client may hold or
  rewrite files, and the tool relies on exclusive creates and lock files that such clients were not
  checked against.
* Untested: a network share (SMB) or any shared filesystem. The
  README states that flock-style locks are not reliable on network filesystems and that claims are
  advisory; nothing here changes that.
* Untested: antivirus or indexer interference. Windows can keep a just-written file open briefly.

## Identity binding

* Unsupported: the optional owner-based identity binding ([identity.md](identity.md)) needs a Unix user id.
  Python on Windows has no `os.getuid` and reports `st_uid` as 0 for every file, so the binding cannot
  tell users apart; do not add a `uid` column on Windows. Without one, `doctor` reports
  `authorship: CANNOT_VERIFY`, as it does on POSIX without binding. A `from:` name is a claim anyone
  can write.

## Known limitations

* Unsupported: `doctor` cannot see a mailbox it is not allowed to read (an ACL deny); expect an unreadable
  directory, not a report. Not exercised here.
* Unsupported: no SIGINT or SIGTERM delivery to a Python child the way POSIX does. A process ended with
  `Stop-Process` or `taskkill` runs no cleanup; the tool's leases, expiry and reclaim logic are what
  recover (see the killed-`send` line above, and the CLAIM text in the README).
* Unsupported: FIFOs and `/dev/stdin`-style body paths. Use `--body-file -` or a real file.
* Verified: Python 3.12 and 3.13 only. 3.10, 3.11 and 3.14 are listed in the README but were not run
  on Windows.
* Untested: macOS is not covered by this page.
* Untested: WSL. It is expected to behave as Linux for a project in the Linux filesystem. A project on the
  Windows drive (`/mnt/c/...`) is Unsupported for locking: the lock semantics there are not those of a Linux
  filesystem.
* Untested: a Windows long path (over 260 characters); message file names embed the
  subject, so keep the project path short.
* Verified: the mailbox files are plain Markdown; `git status` shows them untracked until you add them.
  `tools/agent-postbox/__pycache__/` is created when you run the tool and is in this repository's
  `.gitignore`, not in your project's.
