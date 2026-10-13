# First-use audit (v0.5.0)

What it is like to meet agent-postbox as a new developer, measured from a
clean environment on 2026-10-12: macOS, Python 3.13 for the venvs (`python3`),
a fresh `git clone` of the public `main`, the v0.5.0 wheel downloaded from the
GitHub release, a throwaway git project for every install. No model, no `claude`
or `codex` process, no network use beyond the clone and the release download.

Scope: the journey from "what is this" to "my agents exchange mail and I know
whether it works", plus diagnosing failures and removing everything again.
Wall-clock times are one run each on one laptop; they show the order of
magnitude, not a benchmark.

## Timings

| Step | What | Wall clock |
|---|---|---|
| 1 | Understand the purpose from the README opening | about 15 lines to a full answer ("mail for agents; the other agent finds it in its context at its next prompt"); reading time not measured |
| 2a | `git clone` | 0.6 s |
| 2b | `python3 -m venv` | 1.0 s |
| 2c | `pip install ./agent-postbox` into the fresh venv | 1.1 s; `agent-postbox --version` 0.2 s |
| 2d | `gh release download v0.5.0` (wheel + `SHA256SUMS`), then `pip install` of the wheel | 0.7 s + 0.3 s |
| 2e | `python3 agent-postbox/examples/two-agents/run.py` | 0.5 s (README says "a few seconds") |
| 3a | `install.py <project> --check` | 0.05 s |
| 3b | `install.py <project>` | 0.06 s |
| 3c | `selftest.py` in the installed project | **19.5 s** |
| 4 | `install.py --adapter claude-code`, then `--adapter codex` | 0.05 s each |
| 5 | `send` (CLI) | 0.05 s |
| 6 | pickup through `hooks/agent_mail_check.py` (what the host runs) | 0.05 s |
| 7 | `status`, `doctor`, `list` | 0.05 s each |
| 8 | the failure cases below | 0.05 s each |
| 9 | the cleanup in this file, scripted | under 1 s |

The commands are fast. The time a new developer loses is not in the commands:
it is in the questions below that the output does not answer. Reaching "a
message was delivered" took, by the commands alone, about 4 s (steps 2a, 2c,
3b, 5, 6); the one slow command is `selftest.py`.

## What `install.py` creates and modifies

Given `python3 install.py <project> [--adapter claude-code] [--adapter codex]`
with the defaults, the complete list (verified by diffing a tree before and
after, and by the cleanup test below):

| Path (relative to the project) | Created by | Nature | What removing it costs |
|---|---|---|---|
| `tools/agent-postbox/agent_mail.py`, `PROTOCOL.md`, `SECURITY.md`, `selftest.py` | every run | copy of the tool; **overwritten on every re-run** (a hand edit there is lost on update) | nothing but the tool: messages are plain files and survive. Hooks that point at it then fail (see step 8); with `\|\| true` they cost the mail, never the prompt |
| `tools/agent-postbox/hooks/agent_mail_check.py` | every run | the pickup adapter | same: agents stop receiving mail automatically |
| `docs/agent-mail/` | first run | the mailbox: one `.md` file per message | **all message history and claims, irrecoverable unless committed.** Delete it last, and only on purpose |
| `docs/agent-mail/PARTICIPANTS.md` | first run, never overwritten | who is reachable, by what mechanism; seeded with example rows `agent-a` and `agent-b` | the declared pickup mechanisms, capabilities and identity bindings; `send` and `doctor` still run, minus the identity-binding checks |
| `.claude/settings.json` | `--adapter claude-code` | one `UserPromptSubmit` entry added (merged into your file if it exists) | the hook registration. Your other settings are not touched by the install or by removing the entry |
| `.claude/settings.json.bak` | `--adapter claude-code`, only when the file already existed | **your original file, never overwritten** | the only copy of the file as it was before the install |
| `.claude/settings.json.bak.1`, `.bak.2`, ... | a later upgrade or repair of the entry | the previous state each time | nothing needed |
| `.codex/hooks.json`, `.codex/hooks.json.bak[.N]` | `--adapter codex` | same, for Codex | same |

What it does not touch: anything under `~`, `.git/` (no ignore rules, no
hooks), `.gitignore`, global Claude Code or Codex settings, environment
variables, shell profiles. The shell's `AGENT_MAIL_IDENTITY` you exported
yourself is yours to unset. The Codex trust you give a hook in `/hooks` is
stored by Codex, outside the project, and this audit could not exercise it;
look there if you want it gone. A `pip install` adds only the `agent-postbox`
command and `agent_mail.py`, and is removed with `python -m pip uninstall
tyko84-agent-postbox`.

`git status` after the install shows `.claude/`, `.codex/`, `docs/` and
`tools/` as untracked, and the `.bak` file as untracked too. Nothing says which
of these to commit (the design intent is that the adapter registration travels
with the repository, so commit `.claude/settings.json` and `.codex/hooks.json`;
the `.bak` files are scratch).

## Verified cleanup

There is no uninstall command, and this audit does not add one. These steps
were run in three temporary git projects and the project came back to the
exact prior state in each (a SHA-256 of every file and the directory list
compared before the install and after the cleanup; `git status` empty):

1. a project with nothing of ours and no host config;
2. a project with an existing `.claude/settings.json` holding other keys
   (`model`, `permissions`), oddly indented on purpose;
3. a project with its own `UserPromptSubmit` hook, an unrelated `Stop` entry
   and an `env` block in `.claude/settings.json`, and its own `.codex/hooks.json`.

Run from the project root. Read each step first; step 3 deletes history.

```sh
# 1. The tool and the adapter script. Nothing else lives there by default.
rm -rf tools/agent-postbox
rmdir tools 2>/dev/null          # only removes it if now empty

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
rmdir docs 2>/dev/null

# 4. The numbered backups. The unnumbered .bak is consumed by step 2 when it
#    matches; if step 2 said "left for you to inspect", compare, then delete by hand.
rm -f .claude/settings.json.bak.[0-9]* .codex/hooks.json.bak.[0-9]*
```

Limits of what was verified:

* The snippet matches by the file name `agent_mail_check.py` in a hook's
  `command`, the same marker `install.py` uses. A hand-written entry that
  mentions that file would be removed too.
* If you edited `settings.json` after the install, the result no longer equals
  the `.bak`, so the file is rewritten (two-space indent, key order kept) and
  the `.bak` is left; nothing else in the file is lost.
* `rmdir` of `.claude`, `.codex`, `docs` and `tools` removes them only when
  empty, so a directory that was already there with other contents stays. A
  directory that was empty before the install is removed (git does not track
  empty directories).
* Not verified: Codex's own trust record for the hook, and any CI or other
  tooling of yours that learned the paths.
* If you used `--dest` or `--mailbox`, substitute those paths in steps 1 and 3.

An installed pip package is a separate matter: `python -m pip uninstall
tyko84-agent-postbox`.

## Friction points, ranked by impact

Impact is how likely a new user is to be stopped, or worse, to believe it works
when it does not.

1. **A dead pickup looks exactly like an empty inbox, and nothing in the
   installed project says whether the hook is registered.** `doctor` reports
   the store, the canary and the identity checks; it never looks at
   `.claude/settings.json` or `.codex/hooks.json`, so `doctor` prints `PASS`
   on a project where no host will ever run the adapter (shown: a project
   installed without `--adapter` passes `doctor` after a canary). The adapter
   itself is silent by design when `AGENT_MAIL_IDENTITY` is unset or the mailbox
   is unreadable (exit 0, no output); the same silence is what a healthy
   empty inbox prints. The project is honest about this in prose ("silence
   proves nothing") but gives the user no one command that answers "is my hook
   wired for the shell I am in?". `selftest.py` proves the adapter script works;
   it does not prove the host runs it. Code change (a read-only `doctor` line
   per host config) or doc fix (a manual check, proposed below).
2. **A recipient typo is a silent dead letter.** `send --to agent_b` and
   `send --to nobody` both exit 0 and print `wrote ...`; the message sits as an
   `open` ASK forever, and neither `status` nor `doctor` mentions that no
   `PARTICIPANTS.md` row matches the recipient (`status` lists only senders under
   `agents`). Hyphen versus underscore is an easy slip. Code change: a one-line
   stderr warning on `send` when `--to` is not a declared identity.
3. **After the installer finishes, the next step is not given.** It printed a
   path relative to the project and told you to run `selftest.py`; it did not say
   that each agent needs `AGENT_MAIL_IDENTITY`, that `PARTICIPANTS.md` ships with
   example rows (`agent-a`, `agent-b`, `reachable: no`) to rename, or that
   `doctor` will fail until a canary is filed. **Partly fixed in this change**
   (the verify command now includes `cd <project> &&`, and one line names
   `AGENT_MAIL_IDENTITY`); the PARTICIPANTS and canary steps remain. Doc fix.
4. **`doctor` goes red on a healthy install, on day one and again after 24
   hours.** A fresh mailbox prints `FAIL: pickup canary missing or stale`
   (exit 2) before anything is wrong; the canary expires after 24 h, so a
   working install fails `doctor` the next day until `canary` is re-run. The
   message is actionable (`file one with canary --from <you> --to <you>`), but
   the first `doctor` a user runs in the README order (`status`, `doctor`)
   looks like a failed install. Doc fix: say in the README that the first
   `doctor` fails until `canary` is run and that the canary is daily.
5. **`send` without `--from` prints a 20-line usage block**, the real error
   (`the following arguments are required: --from`) on the last line, and
   nothing connecting it to the `AGENT_MAIL_IDENTITY` the README told the user
   to export. A user who exported the variable reasonably expects `send` to use
   it; only `inbox` and the adapter do. Doc fix (state "`send --from` is always
   explicit; the variable is for `inbox` and the adapter" next to the export in
   Quickstart step 3). Code change if the maintainers want `--from` to default.
6. **`selftest.py` takes about 20 seconds**, and is named the verification
   step in three places. Not wrong, but a new user does not know whether to
   wait. Doc fix: say "about 20 seconds".
7. **`python` versus `python3` in the README.** The 30-second demo uses
   `python agent-postbox/install.py .` and `python tools/agent-postbox/...`; the
   Quickstart and the installer use `python3`; stock macOS has no `python`
   (the installer comments say so). On this machine `python` happened to be a
   shell alias. Doc fix: `python3` throughout.
8. **Wheel users have no installer.** The wheel contains `agent_mail.py` only
   (7 files including metadata). A developer who takes the release wheel, the
   obvious thing from a release page, has the CLI but not `install.py`, the hook
   or the spec; the README says so once ("keep the clone for those") in the
   middle of Option 1. After `pip install` of the wheel there is no message or
   `--help` line pointing at the clone. Doc fix.
9. **`.bak` files and the new directories appear untracked with no guidance**
   (see above). Doc fix.
10. **`PARTICIPANTS.md` unreadable** is handled well by `send` (`refusing:
    cannot read <path>: Permission denied`, exit 2) and `doctor`
    (`identity_bindings: UNREADABLE (PermissionError)`), but `status` and the
    adapter carry on without a word. Defensible (read-only and fail-silent by
    design); recorded, no change proposed.
11. **A moved or deleted tool directory** prints Python's `can't open file ...`
    to stderr from the hook and exits 0 (`|| true`), so the user sees a stray
    line and no mail. Correct for safety; the line does not say "agent-postbox".
    Code change, low priority (a wrapper message in the registered command).
12. **`agent_mail.py` in the install is not executable**, so
    `tools/agent-postbox/agent_mail.py doctor` fails with a shell "Permission
    denied". Every doc says `python3 ...`, so this only bites a user who types
    the path. Noted, no change.

Things that worked well and are worth keeping: the `NO MAILBOX` error names the
path it looked at and the two ways out (exit 2); `AGENT_MAIL_DIR` pointing at a
missing directory says it is never created and to `mkdir` it; `--check` shows
what would happen with `ok`/`MISS` per file; a bad `--type` lists the five valid
ones; a hand-corrupted `settings.json` is refused before anything is copied
(tested elsewhere); `doctor` after `canary` ends in a plain `PASS`.

## Failure diagnosis, as observed

| Situation | What the user sees | Actionable? |
|---|---|---|
| `AGENT_MAIL_IDENTITY` unset, adapter run | nothing, exit 0 | No (by design; item 1) |
| `AGENT_MAIL_IDENTITY` unset, `inbox` | `inbox requires --to <identity> (or set AGENT_MAIL_IDENTITY)` | Yes |
| `send` without `--from` | usage block, `--from` required, exit 2 | Partly (item 5) |
| `AGENT_MAIL_DIR=/nonexistent/x` | `NO MAILBOX at /nonexistent/x` + hint to `mkdir`, exit 2 | Yes |
| Outside any project | `NO MAILBOX at <cwd>/docs/agent-mail` + hint to set `AGENT_MAIL_DIR` or run `install.py`, exit 2 | Yes |
| `AGENT_MAIL_DIR` empty directory | `doctor`: `store: OK`, `messages: 0`, then the canary FAIL | Yes |
| Unreadable `PARTICIPANTS.md` | `send`: refusing line, exit 2; `doctor`: UNREADABLE line, FAIL | Yes |
| Hook not registered | nothing anywhere; `install.py --adapter ... --check` says `WOULD register` | Only if you know to run `--check` (item 1) |
| Hook script moved | one Python `can't open file` line on stderr, prompt not blocked | Partly (item 11) |
| `send --to` unknown or mistyped | `wrote ...`, exit 0 | No (item 2) |
| `send --type QUESTION` | `type must be one of ASK, ANSWER, NOTICE, CLAIM, DISPUTE` | Yes |

## Recommendations

| # | Change | Kind |
|---|---|---|
| 1 | Add a read-only check to `doctor` that reports, per host config, whether a `UserPromptSubmit` entry mentioning `agent_mail_check.py` exists and whether its script path resolves. Until then, add the manual check below to `docs/hosts.md` | code change; doc fix meanwhile |
| 2 | `send` warns on stderr (exit code unchanged) when `--to` matches no `PARTICIPANTS.md` identity | code change |
| 3 | README: say what follows the install: rename the example rows in `PARTICIPANTS.md`, export the identity, file the first canary | doc fix |
| 4 | README: the first `doctor` fails until `canary` is run; the canary lasts 24 h | doc fix |
| 5 | README Quickstart step 3: `send --from` is always explicit | doc fix (code change optional) |
| 6 | README: "`selftest.py` takes about 20 seconds" | doc fix |
| 7 | README 30-second demo: `python3` | doc fix |
| 8 | README Option 1: a release wheel installs the CLI only; the installer, hook and spec need the clone | doc fix |
| 9 | README Option 3: which files to commit and that `.bak` files are scratch | doc fix |
| 10 | Add a link to this file from `docs/README.md` and a "Removing it" pointer from README Install | doc fix |
| 11 | An `install.py --uninstall` implementing the cleanup above, if the maintainers want one | code change, not done here |
| 12 | Hook command that names agent-postbox when its script is missing | code change, low priority |

The installer wording change in this commit covers the printed verify command
and the identity line (recommendation 3, in part).

### Proposed README text (exact)

After the Option 3 code block:

> After the install: open `docs/agent-mail/PARTICIPANTS.md` and rename the two
> example rows (`agent-a`, `agent-b`) to your agents' names; give each agent's
> shell its own `AGENT_MAIL_IDENTITY`; then run `agent-postbox canary --from
> <you> --to <you>` and `agent-postbox doctor`. The first `doctor` reports `FAIL:
> pickup canary missing or stale` until a canary has been filed, and the canary
> lasts 24 hours, so file a new one to see `PASS` again. `selftest.py` takes about
> 20 seconds. Commit `.claude/settings.json` and `.codex/hooks.json` (that is how
> the adapter reaches the next clone); the `.bak` files next to them are your
> pre-install originals, kept in case you want them.

In Quickstart step 3, after the `export` line:

> `send` always takes `--from` explicitly. `AGENT_MAIL_IDENTITY` is read by
> `inbox` and by the pickup adapter.

In Install, after Option 1:

> A release wheel contains `agent_mail.py` and the `agent-postbox` command only.
> To register the pickup hook you also need `install.py` and `hooks/`, which are in
> the clone, not in the wheel.

A new "Removing it" paragraph:

> There is no uninstall command. To remove an install, follow the verified steps
> in [docs/first-use-audit.md](docs/first-use-audit.md#verified-cleanup).

### Manual hook check (for `docs/hosts.md`, until `doctor` does it)

From the project root, no model needed:

```sh
python3 -I -c "
import json, pathlib
for f in ('.claude/settings.json', '.codex/hooks.json'):
    p = pathlib.Path(f)
    if not p.is_file():
        print(f, 'absent'); continue
    s = json.loads(p.read_text(encoding='utf-8-sig'))
    hooks = [h.get('command', '') for g in s.get('hooks', {}).get('UserPromptSubmit', []) or []
             for h in g.get('hooks', [])]
    print(f, [c for c in hooks if 'agent_mail_check.py' in c] or 'NOT REGISTERED')
"
```

Then, in the shell the session will start from, `AGENT_MAIL_IDENTITY=<name>
python3 tools/agent-postbox/hooks/agent_mail_check.py` must print the mail
addressed to `<name>` (send yourself a NOTICE first). This proves the script,
the identity and the registration; whether the host runs the hook is checked
with the positive control in this directory's `hosts.md`.

## Method and limits

* Every install ran in a new temporary directory; none touched the repository.
* The comparison of the install and cleanup used SHA-256 of every file plus the
  directory list, excluding `.git`.
* `selftest.py` passed in the installed copy ("all checks passed (3
  spec-checkout-only check(s) skipped)").
* Not exercised: Claude Code or Codex themselves (no model, no host), Windows,
  Python versions other than 3.13, a slow or offline network, reading time for
  the README, and a first-time developer other than the author of this audit,
  who had read the code before measuring, so the "confusing" calls above lean
  toward what the text does not say rather than what it does.
