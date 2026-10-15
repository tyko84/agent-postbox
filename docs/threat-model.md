# Threat model

What this tool defends, against whom, on which assumptions, and what it
deliberately does not. `SECURITY.md` is the short version for someone about to
install; this is the reasoning behind it, with the code each claim rests on
(file:line at the commit this was written; the test that would catch a
regression is named where one exists). Findings of the phase 6 review are in
the last two sections.

## Assets

1. **The agent's context.** The prompt hook prints mailbox text before the
   model sees the user's prompt (`hooks/agent_mail_check.py`). Whatever reaches
   that output is read as if the user had typed it nearby.
2. **Project files and the agent's actions.** What an agent can be talked into
   doing with its tools after reading a message.
3. **Other agents' messages.** Their integrity (not forged, not silently
   hidden), and their confidentiality only as far as file permissions go.
4. **The user's terminal** and **the host's config** (`.claude/settings.json`).

## Adversaries (in scope)

* **A malicious message author who can write files into the mailbox
  directory:** a hostile branch, pull request or checkout, or another agent
  with write access. They control file *names and contents*, including
  `PARTICIPANTS.md`, which lives in the same directory.
* **A hostile repository** the operator opens, runs `install.py` in, or whose
  working directory the hook starts in.
* **A co-located unprivileged user** on the same machine: can read what file
  modes allow, can create files in directories that are world-writable, and
  cannot write the victim's mailbox.

## Out of scope

Local root; a compromised host or interpreter; the host application's own
trust prompts; the build and release pipeline (`SECURITY.md`, last section);
network attackers (the tool makes no network calls).

## Trust assumptions

Each was checked in the code.

| # | Assumption | Where |
|---|---|---|
| T1 | **The sender is not authenticated.** `from:` is text; anyone who can write a file can claim any identity. Optional uid binding is evidence, not proof (`docs/identity.md`). | `agent_mail.py:1294` (`binding_refusal` only guards `send`, not a hand-written file) |
| T2 | **Prompt injection is inherent.** The hook prints the sender, subject and id of live mail; the model may follow a persuasive subject. The mitigation is the stated rule that a message is evidence, never authority, restated in every delivery. | `hooks/agent_mail_check.py:152-156` |
| T3 | **Anyone who controls the mailbox directory controls `PARTICIPANTS.md`** (aliases, capability claims, uid bindings), which is read from the mailbox first. | `agent_mail.py:1070` (`participants_paths`) |
| T4 | **Whoever can write a message can supersede or answer any non-CLAIM message**, which stops it being live, so mail can be hidden from the hook by a writer. A CLAIM can only be superseded by its own sender. | `agent_mail.py:705` (`may_supersede`), `:855` (`owes_reply`) |
| T5 | **The project's tool directory is trusted code.** The hook imports `agent_mail.py` from beside it or one level up; a repo that supplies those files supplies the code that runs. | `hooks/agent_mail_check.py:118-124` |
| T6 | **Environment variables are the operator's:** `AGENT_MAIL_DIR`, `AGENT_MAIL_IDENTITY`, `AGENT_MAIL_PROJECT`, `CLAUDE_PROJECT_DIR`. | `agent_mail.py:237-316` |
| T7 | **Operator-supplied paths are followed:** `--body-file` (any readable file, symlinks included) and a symlinked `AGENT_MAIL_DIR`/`docs/agent-mail`. | `agent_mail.py:945` (`resolve_body`) |
| T8 | **Local POSIX filesystem** with atomic rename, exclusive create and `flock`. | `SECURITY.md` |

## What holds (verified by attack, not only by reading)

* **Nothing in the mailbox is executed or passed to a shell.** Mail is parsed
  as text and printed. `--body` with a backtick or `$` is refused.
* **Path traversal through ids, subjects and identities fails.** `--from`,
  `--to` match `[a-z0-9][a-z0-9._@+-]*` without `..` (`agent_mail.py:461`,
  checked at send `:2099`); `--reply-to`/`--supersedes`/`--re` match a plain
  token of at most 64 characters; the file name is `<ULID>-<slug>` with the
  slug reduced to `[a-z0-9-]` (`:324`). Probed with `../x`, `a/b`, `..`,
  `a..b`, absolute paths, NUL, newlines, `$(...)`, full-width letters: all
  refused, nothing written.
* **Message files are never followed through links.** `lstat` refuses
  symlinks and non-regular files, the open uses `O_NOFOLLOW|O_NONBLOCK` and
  re-checks with `fstat`, size is capped at 32 MiB (`:501-537`). A symlink
  planted at a message name is a REJECT; scope locks and key markers are
  opened `O_NOFOLLOW`/`O_EXCL` (`:2038`, `:1847`).
* **Headers cannot forge framing.** A header with a control or format
  character (newline, CR, ESC, NUL, bidi overrides, zero-width, line/paragraph
  separators, tag characters) quarantines the whole file (`:540-567`), so a
  subject cannot start a new line in the hook output, fake a `Reply:` line or
  another message, or carry a terminal sequence. Duplicate frontmatter keys
  quarantine too. Probed with each of these in subject and sender.
* **Bodies** are checked for C0/C1 controls (ESC, NUL) on read and send
  (`:481`). The hook never prints a body; `show` does.
* **Hook time is bounded in practice:** 20 000 messages took 0.8 s, ten
  31 MiB files 1 s, a 30 MiB header 1.4 s (macOS, Python 3.13). The hook
  lists at most 20 messages plus unanswered ASKs and 5 rows per holdings list
  (`hooks/agent_mail_check.py:48-49`, `:160`).
* **Files are published atomically** (hidden temp, fsync, `os.link`, never
  overwriting; `agent_mail.py:1710`); the temp name carries a 128-bit random
  id, so it cannot be pre-planted. Readers never see a partial message.
* **The hook only reads and fails silent** (`hooks/agent_mail_check.py:181`);
  there is no logging, so no log can disclose message content.
* **The registered command ends in `|| true`**, so a missing script cannot
  block a prompt (`install.py:106-117`). Settings are merged, never
  overwritten, backed up first, and unparseable JSON aborts.
* **`--dest`/`--mailbox` cannot escape the project** (absolute, `..`, or a
  link out are refused; `install.py:182`).

## Findings of the phase 6 review

All were reproduced with a throwaway PoC before being fixed, and each fix has
a regression check in `test_adversarial.py` (`hostile_output`) that fails on
the unfixed code.

1. **Unbounded hook output (medium).** `send` caps subject (500), identities
   (128) and other headers, but reading does not: the cap is not applied to a
   file written by hand. Twenty-five 1 MB subjects and senders made the hook
   print 40 MB into the prompt in 2.2 s; a CLAIM whose `expires` is
   `2099-01-01T00:00:00.` plus a million zeros parses and was printed whole.
   Fixed in the hook: sender, subject, `expires` and dates are clipped to the
   limits `send` enforces, with the cut stated
   (`hooks/agent_mail_check.py:56`, `MAX_*`). Admission is unchanged, so
   existing mail still loads.
2. **Terminal escapes and forged lines through file names (medium).**
   `REJECT <name>: ...` lines (stderr of `list`, `inbox`, `status`, `doctor`; `show` for an unreadable file) and
   stale-temp names in `doctor` printed the file name raw. A file named with
   ESC, BEL, a bidi override or a newline (any checkout can contain one) put
   a terminal sequence, or a line starting `FAKE: all clear`, into the output
   the agent reads. Fixed: names are escaped by `_shown` (`agent_mail.py:489`).
   The hook was never affected (it does not print rejects).
3. **Terminal escapes through `PARTICIPANTS.md` (low).** An identity cell
   with ESC reached the terminal in `status`, `doctor` and the
   unknown-recipient warning of `send`. Fixed: a row whose identity has a
   control or format character is ignored (`load_participants`,
   `_parse_bindings_text`).
4. **Shell text in the registered hook command (low).** `install.py
   --adapter claude-code --dest 'x$(cmd)y'` (or a backtick, or a `"`) wrote
   `python3 "$CLAUDE_PROJECT_DIR/x$(cmd)y/hooks/..."`, which runs `cmd` on
   every prompt (a `"` breaks the command). The codex adapter already refused
   such names. Fixed: `claude_dest_refusal` (`install.py:169`). Only an
   operator-supplied `--dest` can reach this.
5. **Installer writes through a `.claude` link that leaves the project
   (low).** A checkout whose `.claude` links to `~/.claude` got the hook merged
   into the user-level settings, which the host does not ask to trust. Fixed:
   refused like `--dest`/`--mailbox` (`install.py:151`).

## Accepted risks (by design, not changed)

* **Prompt injection in subjects (T2) and in bodies read with `show`.** The
  mitigation is the evidence-not-authority rule, not code.
* **Identity spoofing (T1).** Includes homoglyph and invisible-character
  senders on the read side: `send` accepts ASCII identities only, but a
  hand-written `from:` may use any character that is not a control or format
  character (for example a full-width letter, U+00A0, U+3164). Since any
  sender name can be claimed anyway, escaping them would add nothing.
* **A body may contain bidi controls and invisible tag characters** (U+E0000
  block). Only headers reject format characters; `test_adversarial.py` pins
  that a body may carry them (legitimate right-to-left text, emoji
  sequences). The hook does not print bodies; `show` does. A reviewer reading
  a diff will not see such text.
* **Mailbox writers can hide or falsely close mail (T4)** and squat claim
  scopes; claims are advisory leases.
* **A hostile `PARTICIPANTS.md` (T3)** can add aliases, change fan-out
  targets inside the same mailbox, and, with a `uid` column, make `send`
  refuse. Nothing leaves the mailbox.
* **A symlinked settings *file* is written through** (a dotfiles manager's
  layout; pinned by `test_adapters.py`). A hostile checkout that links
  `.claude/settings.json` to a user-level file can still receive the hook
  entry if the operator runs `install.py --adapter` there. Review the output
  of the installer, and do not run it in a checkout you do not trust.
* **Mailbox discovery walks up from the working directory.** In a tree with
  no `.git`, a `docs/agent-mail` that another user created in an ancestor
  directory (for example under a shared `/tmp`) is adopted (reproduced: the
  hook printed its mail). With a `.git` at or above the project, the walk
  stops there. Set `AGENT_MAIL_DIR`, and keep projects in git.
* **A symlinked mailbox directory is followed** by `send` and the hook (a
  shared inbox across worktrees is a supported layout). A hostile repo whose
  `docs/agent-mail` links elsewhere makes `send` create files in that
  directory (named `<ULID>-<slug>.md`) and the hook read valid mail from it.
* **Files are created with the process umask** (messages 0644). A co-located
  user can read a mailbox they can reach; use directory permissions as the
  access control.
* **`show` re-reads the file after it was vetted.** A writer able to swap the
  file inside that window could have unvetted text printed. The window is
  milliseconds and needs mailbox write access.
* **Cost of reading is linear** in the number of files and bytes (up to
  32 MiB each, every prompt). A hostile branch can make the hook slow only by
  committing that much data.
* **`--body-file` reads any file the operator can read** and follows links
  (T7); an injected instruction to send a secret file as a body is a prompt
  injection, not a tool defect.

## Not verified

* Windows and network filesystems (unsupported).
* Concurrency attacks on the idempotency-marker takeover beyond what
  `test_adversarial.py` and `test_hardening.py` already drive.
* Behaviour on Python versions other than 3.13 for the timing figures.
