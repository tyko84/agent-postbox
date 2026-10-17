# Troubleshooting and removing it

Each symptom below was reproduced on 2026-10-13 (macOS, Python 3.13) unless it
says otherwise. Run `doctor` first: it is read-only, exits 2 on a problem and
tells you what to do.

```sh
python3 tools/agent-postbox/agent_mail.py doctor
```

## Common failures

| What you see | Why | What to do |
|---|---|---|
| `doctor` says `canary: UNVERIFIED` (exit 0) | The install is healthy; no canary has been filed, so pickup is unverified | `canary --from <you> --to <you>`, then confirm your agent's hook shows it |
| `doctor` ends `FAIL: pickup canary missing or stale` (exit 2) | A canary was filed and has lapsed (24 h by default); the line names it. Not a broken install | `canary --from <you> --to <you>`, then `doctor` again |
| `doctor`: `AGENT_MAIL_IDENTITY: UNSET in this shell` | The adapter prints nothing without an identity, which looks exactly like an empty inbox | `export AGENT_MAIL_IDENTITY=<name>` in the shell the session starts from |
| `doctor`: `pickup_hook: none registered` | `install.py` ran without `--adapter` | `python3 <clone>/install.py <project> --adapter claude-code` (or `codex`); safe to re-run |
| `send` prints a usage block ending `the following arguments are required: --from` | `send` always needs `--from`; `AGENT_MAIL_IDENTITY` is read by `inbox` and the adapter only | add `--from <you>` |
| `NO MAILBOX at <path>` (exit 2) | No `docs/agent-mail` above the current directory, or `AGENT_MAIL_DIR` points at a missing directory. Not an empty inbox | `cd` into the project, run `install.py`, or `mkdir` the `AGENT_MAIL_DIR` directory (it is never created for you) |
| `send --to agent_b` prints `wrote ...` but nobody gets it | The recipient is not an identity anyone uses (hyphen versus underscore); the message waits as an open ASK. If the mailbox has a `PARTICIPANTS.md` with rows, a `warning: --to 'agent_b' is not a row in PARTICIPANTS.md` line on stderr names the near match (exit is still 0); with no such table there is no warning | resend with the exact name; check `list --to <name> --live` |
| Mail was sent, the other session shows nothing | Hook not registered or not trusted (Codex: `/hooks`), identity unset, or the host does not run hooks | `doctor` in that shell; positive control in [hosts.md](hosts.md) |
| The agent says the `send` command was denied by the permission mode | Claude Code refuses a command that holds a line starting with `#` inside quotes (a markdown heading in `--body`) even when an allow rule matches; or no rule matches the spelling used (a rule for the relative path does not cover the absolute quoted path the pickup hook prints); or a chained `&& other-command` is not allowed. In [experiment v2](experiment-v2-results.md) 9 of 60 runs failed this way, all from the `#` line | `doctor` prints the exact rules to add (`send_permission:`); add the **absolute quoted** one at least, because it is the spelling the pickup hook's `Reply:` line prints (measured: with only the relative rule the agent's own `Reply:` command is denied, with only the absolute one it runs; see "Measured on Claude Code 2.1.300" in [hosts.md](hosts.md)); send multi-line bodies as `--body-file - <<'EOF' ... EOF`; keep each mail command its own call. Do not use `Bash(python3 *agent_mail.py *)`: it does not match the quoted path and does match any script of that name anywhere. Measured on Claude Code 2.1.296 and again on 2.1.300 (same results for these cases; the host's checks can change between versions): `docs/data/permission-probe/`. One more refusal seen on 2.1.300: a heredoc body containing a Python-dict-like `{'count': 3}` was refused although the rule matched (one body tested; backticks were allowed). If a body is refused, write it with `--body-file PATH` from a file the agent wrote with its file tool |
| Mail was sent, the hook never fires, and `claude` was started in a subdirectory of the project | Claude Code 2.1.300 (headless) did not load the project's `.claude/settings.json` from a parent directory, so the hook did not run at all (no hook event, nothing in context). Measured: [hosts.md](hosts.md) | start `claude` from the directory that holds `.claude/` |
| Agents in two `git worktree` checkouts of one repository do not see each other's mail | A linked worktree has its own copy of `docs/agent-mail/` (and of the tool, if committed). `doctor` says so (`worktree:` line) | point every agent's `AGENT_MAIL_DIR` at one directory, or use one checkout. If `tools/agent-postbox/` was not committed along with `.claude/settings.json` the worktree's hook finds no script (and `doctor` cannot be run there) (`can't open file` on stderr; the prompt still goes through) |
| `install.py` prints `ABORT: ... is not valid JSON` or `... is not a JSON object` (exit 2) | The project's `.claude/settings.json` (or `.codex/hooks.json`; Codex words it `is not readable JSON`) cannot be merged safely | Fix the file by hand, then re-run. It is never rewritten, and nothing else was changed |
| `python: command not found` | Stock macOS has no `python` | use `python3` |
| `Permission denied` running `tools/agent-postbox/agent_mail.py` directly | The copy is not executable | prefix with `python3` |
| `can't open file ... agent_mail_check.py` on stderr at each prompt | `tools/agent-postbox` was moved or deleted; the registered command ends in `|| true`, so the prompt still goes through, minus the mail | re-run `install.py <project> --adapter ...` |
| `selftest.py` takes about 20 s | It exercises crashes and races on purpose | wait; it ends `all checks passed` |
| `pip install agent-postbox` installed something else | That name on PyPI is an unrelated project | `python3 -m pip uninstall agent-postbox`, then `python3 -m pip install ./agent-postbox` from a clone. This project's distribution is `tyko84-agent-postbox` |
| `git status` shows `.claude/`, `.codex/`, `docs/`, `tools/` untracked | The install added them | commit `.claude/settings.json` and `.codex/hooks.json` (that is how the hook reaches the next clone) along with `docs/agent-mail/` and `tools/agent-postbox/` if the team shares them; `*.bak*` files are your pre-install originals |
| Windows | Unsupported and untested. WSL: not tried | use Linux or macOS |

A mailbox that is wrong in some other way: `agent_mail.py status` and
`agent_mail.py verify <id>` (recomputes the fingerprints one message cites; it needs a message
id) are read-only too.

## Upgrading

There is no upgrade command. Pull or clone the newer release and re-run the same
`python3 <clone>/install.py <project> [--adapter ...]`. Checked on one version only (a second run
of the same version, 2026-10-17, macOS, Python 3.13): it prints `copied tool + spec into
tools/agent-postbox/`, `kept existing docs/agent-mail/PARTICIPANTS.md` and, for an adapter,
`already registered (no change)`; the mailbox and its messages are not touched. The copy under
`tools/agent-postbox/` is overwritten without a prompt, so a local edit to it is lost. Upgrading
across versions was not tested here: read [CHANGELOG.md](../CHANGELOG.md) first and run `doctor`
afterwards.

## Removing it

There is no uninstall command. These steps were run in three throwaway git
projects (nothing of ours; an existing `.claude/settings.json` with other keys;
an existing `UserPromptSubmit` hook plus a `.codex/hooks.json` of its own) and
each came back to the exact prior state (SHA-256 of every file and the
directory list compared; `git status` empty). The one change since then: step 4
used a glob that stops with `no matches found` in zsh (the macOS default shell)
when no numbered backup exists; it now uses `find`. Re-run on 2026-10-13 in zsh,
on a project with both adapters. Not covered: Codex's own trust record for the
hook, and any tooling of yours that learned the paths.

Run from the project root. Read each step first; **step 3 deletes every
message and claim.**

```sh
# 1. The tool and the adapter script. Nothing else lives there by default.
rm -rf tools/agent-postbox
rmdir tools 2>/dev/null || true   # only removes it if now empty

# 2. The hook registrations. Removes only entries whose command mentions
#    agent_mail_check.py, keeps every other key and every other hook, and
#    restores your original file byte for byte from <file>.bak when the result
#    is identical to it. A file that holds nothing else is deleted.
python3 -I - .claude/settings.json .codex/hooks.json <<'EOF'
import json, shutil, sys
from pathlib import Path

MARK = "agent_mail_check.py"
for name in sys.argv[1:]:
    p = Path(name)
    if not p.is_file():
        print(f"skip   {p}: not there")
        continue
    cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    kept = []
    for group in cfg.get("hooks", {}).get("UserPromptSubmit") or []:
        group["hooks"] = [h for h in group.get("hooks", [])
                          if MARK not in str(h.get("command", ""))]
        if group["hooks"]:
            kept.append(group)
    if kept:
        cfg["hooks"]["UserPromptSubmit"] = kept
    else:
        cfg.get("hooks", {}).pop("UserPromptSubmit", None)
        if not cfg.get("hooks", True):
            cfg.pop("hooks")
    bak = p.with_name(p.name + ".bak")
    if not cfg:
        p.unlink()
        print(f"removed {p}: nothing else was in it")
    elif bak.is_file() and json.loads(bak.read_text(encoding="utf-8-sig")) == cfg:
        shutil.copy2(bak, p)
        bak.unlink()
        print(f"restored {p} byte for byte from {bak.name}")
    else:
        p.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"rewrote {p} without the adapter; {bak.name}* files are left for you to inspect")
    try:
        p.parent.rmdir()         # .claude / .codex, only if now empty
    except OSError:
        pass
EOF

# 3. OPTIONAL AND DESTRUCTIVE: the mailbox, which is every message and claim.
#    Skip it, or `git add` it somewhere first, if anyone might want the history.
rm -rf docs/agent-mail
rmdir docs 2>/dev/null || true

# 4. The numbered backups (.bak.1, .bak.2, ...). The unnumbered .bak is
#    consumed by step 2 when it matches; if step 2 said "left for you to
#    inspect", compare, then delete by hand.
find .claude .codex -maxdepth 1 -name '*.bak.[0-9]*' -delete 2>/dev/null
rmdir .claude .codex 2>/dev/null || true   # without this the block exits 1 when they are not empty
```

What it matches and what it leaves:

* Step 2 matches the file name `agent_mail_check.py` in a hook's `command`, the
  same marker `install.py` uses. A hand-written entry that mentions that file
  would be removed too.
* If you edited `settings.json` after the install, the result no longer equals
  the `.bak`, so the file is rewritten (two-space indent, key order kept) and
  the `.bak` is left; nothing else in the file is lost.
* `rmdir` removes `.claude`, `.codex`, `docs` and `tools` only when empty, so a
  directory that was already there with other contents stays.
* With `--dest` or `--mailbox`, substitute those paths in steps 1 and 3.
* Nothing under `~`, `.git/`, `.gitignore`, global Claude Code or Codex
  settings, environment variables or shell profiles was touched by the install,
  so nothing there needs undoing except an `AGENT_MAIL_IDENTITY` you exported
  (`unset AGENT_MAIL_IDENTITY`). Codex's hook trust lives in Codex; remove it
  there.

A `pip install` of this project adds only the `agent-postbox` command and the
`agent_mail` module: `python3 -m pip uninstall tyko84-agent-postbox`.

Background and the full list of what `install.py` creates:
[first-use-audit.md](first-use-audit.md).
