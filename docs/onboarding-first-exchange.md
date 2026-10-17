# First exchange

About a minute of machine time (measured below). A person should allow longer: trusting the
project in your host and getting the identity variable into each session's shell are not measured here.

From nothing to two agents exchanging a message in a project of yours. Every
command below was run on 2026-10-13 from a fresh copy of the repository in a
throwaway git project (macOS, Python 3.13, no model, no host, no network after
the clone), and again on 2026-10-16 (see "Timings" at the end). The timings are
machine time on one laptop, not the time a person takes.

Requirements: Linux or macOS, Python 3.10-3.14, git. **Windows is unsupported
and untested**; so is WSL (not tried). Use `python3`, not `python`: stock macOS
has no `python`. Run `python3 --version` first: it must say 3.10 or newer, and stock
macOS `python3` can be 3.9 (3.9.6 observed), which the tool refuses.

## 1. Get it and watch it work (under 5 seconds)

```sh
git clone https://github.com/tyko84/agent-postbox
python3 agent-postbox/examples/two-agents/run.py
```

The second command runs the real installer, CLI and pickup adapter in a
temporary directory it removes afterwards, with two scripted "agents". It ends
in `All 7 steps behaved as described.` (0.4 s). It touches nothing of yours.

Not on PyPI: do not `pip install agent-postbox` (a different project).

## 2. Install into your project (under 1 second)

```sh
python3 agent-postbox/install.py /path/to/your/project --adapter claude-code
```

If the project path contains a space, quote it (`install.py "/path/to/my project"`); unquoted,
the shell passes two arguments and `install.py` stops with `unrecognized arguments`, exit 2,
changing nothing. The installed tool, the hook command and the printed `cd` line all handle a
space (measured below).

Use `--adapter codex` for Codex, or leave `--adapter` out to poll. The project
should be a git repository (the Codex registration requires it). Preview with
`--check`: it changes nothing.

This creates `tools/agent-postbox/` (a copy of the tool), `docs/agent-mail/`
(the mailbox) and, for an adapter, `.claude/settings.json` or
`.codex/hooks.json`. If that file already existed, your original is kept next
to it as `<file>.bak`. Nothing outside the project is touched. Commit
`.claude/settings.json` / `.codex/hooks.json` so the hook reaches the next
clone; the `.bak` files are your pre-install originals, scratch if you do not
want them.

## 3. Name your two agents

Everything from here runs in the project root.

1. Optional for a trial: the shipped `agent-a` and `agent-b` rows work as they are. To use
   your own names, open `docs/agent-mail/PARTICIPANTS.md` and rename those two rows.
2. In the shell each session starts from, one name per agent:

   ```sh
   export AGENT_MAIL_IDENTITY=agent-a      # in agent B's shell: agent-b
   ```

   `send` always takes `--from` explicitly. `AGENT_MAIL_IDENTITY` is read by
   `inbox` and by the pickup adapter, not by `send`.

## 4. File the first canary, then `doctor`

```sh
python3 tools/agent-postbox/agent_mail.py doctor    # healthy, "canary: UNVERIFIED", exit 0
python3 tools/agent-postbox/agent_mail.py canary --from agent-a --to agent-a
python3 tools/agent-postbox/agent_mail.py doctor    # ends in PASS
```

The first `doctor` on a fresh install is healthy and reports `canary: UNVERIFIED` (exit 0):
the install is fine, and pickup has not been verified yet. A canary is a real test (file it, then
confirm the agent's hook shows it), not a box to tick. It lasts 24 hours; after that `doctor`
reports `canary: MISSING_OR_STALE` and exits 2, naming the lapsed canary, until `canary` is re-run. `doctor` also reports whether the hook is registered
(`pickup_hook: registered`) and whether `AGENT_MAIL_IDENTITY` is set in the
shell you ran it from.

## 5. Send and receive

Agent A (the body files are scratch: delete them, or write them outside the project, so
`git status` stays clean):

```sh
echo "who owns the billing client?" > body.txt
python3 tools/agent-postbox/agent_mail.py send --type ASK --from agent-a --to agent-b \
    --subject "billing client owner" --body-file body.txt
```

Agent B. This is what the hook runs before every prompt (silent without an
identity):

```sh
AGENT_MAIL_IDENTITY=agent-b python3 tools/agent-postbox/hooks/agent_mail_check.py
```

It prints `Agent mail - 1 live message(s) addressed to 'agent-b'.`, the ASK, how
to read it and how to answer. In a real session the host does this for you; you
do not type it. Agent B answers by citing the id:

```sh
echo "Mine. Safe to edit." > answer.txt
python3 tools/agent-postbox/agent_mail.py send --type ANSWER --from agent-b --to agent-a \
    --reply-to <id from the ASK> --subject "re: billing client" --body-file answer.txt
```

(The id is the line under the ASK in `list --to agent-b --live` or `inbox`, and the `id:` line
in the hook's output.)

Without a hook, B polls: `python3 tools/agent-postbox/agent_mail.py list --to agent-b --live`.

A recipient name must match the other agent's identity exactly (`agent-b` is
not `agent_b`): a mistyped `--to` still prints `wrote ...` and exits 0.

## 6. Check that the host really runs the hook

`python3 tools/agent-postbox/selftest.py` takes about 20 seconds and proves the
adapter script works; it does not prove your host runs it. Start the real
session with `AGENT_MAIL_IDENTITY` exported and ask it, without running any
command, whether it has mail. The positive control for each host is in
[hosts.md](hosts.md): Claude Code was run in a real host (headless); Codex is
experimental and needs the hook trusted in `/hooks`.

## Timings

**Machine time only: the seconds a command takes to run, not the time a person needs to read,
type or decide. No human was timed.** Measured 2026-10-16 on an Apple M4 Max, macOS 27.0,
Python 3.13.7, git 2.51.0, three runs (each in a fresh temp HOME and a fresh git project whose
path contains a space), the README Quickstart followed literally. The repository was cloned
from a local path, so network time for `git clone` is not included. The wheel was built with
`python3 -m build --wheel` (0.9 s, one run, needs `build` installed) and installed
offline (`pip install --no-index`) into a fresh venv.

| Step | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Quickstart 1: `git clone` (local) | 0.63 s | 0.61 s | 0.59 s |
| Quickstart 2: `examples/two-agents/run.py` | 0.38 s | 0.41 s | 0.39 s |
| Quickstart 3: `install.py <project> --adapter claude-code` | 0.02 s | 0.03 s | 0.03 s |
| Quickstart 4: `send` ASK, `inbox`, `send` ANSWER, `inbox` (4 commands) | 0.20 s | 0.20 s | 0.20 s |
| Quickstart 5: hook preview | 0.06 s | 0.06 s | 0.06 s |
| **Quickstart steps 1-5, total** | **1.29 s** | **1.31 s** | **1.27 s** |
| `doctor` (exit 2), `canary`, `doctor` (PASS) | 0.15 s | 0.16 s | 0.15 s |
| `selftest.py` | 21.31 s | 19.13 s | 19.13 s |
| Everything above | 22.75 s | 20.60 s | 20.55 s |
| Optional: venv + `pip install` of the wheel + `agent-postbox --version` | 1.50 s | 1.54 s | 1.40 s |

By commands alone a first exchange takes about 1.3 seconds (about 20 seconds with
`selftest.py`); reading takes the rest. "Five minutes" in this page's title is a claim about a
person, and nothing here measures it.

### Docs and reality, 2026-10-16

Every Quickstart and walkthrough command ran with the output the docs describe, with these
differences (each now fixed or noted above):

| Where | Command | Docs said | Observed | Status |
|---|---|---|---|---|
| This page, step 5 | `send --type ANSWER ... --body-file answer.txt` | Run as written | `cannot read --body-file 'answer.txt': No such file or directory`, exit 2: the file was never created | Fixed: the page now creates it |
| This page, step 5 | `echo ... > body.txt` in the project root | (silent) | Leaves an untracked `body.txt` in the project | Noted in the page |
| README Quickstart 3, this page step 2 | `install.py /path/to/your/project` with a space in the path | `/path/to/your/project` | Unquoted, two arguments: `unrecognized arguments: project`, exit 2, nothing changed | Noted in the page (the README line itself is not changed here) |
| README Install, "pip" | `pip install ./agent-postbox` then `install.py` | Says pip does not copy `install.py` | Confirmed: the wheel holds `agent_mail.py` and metadata only; the quickstart needs the clone | No mismatch |
| `selftest.py` in a project | the printed command | Ends `all checks passed` | Ends `all checks passed (3 spec-checkout-only check(s) skipped)` in an installed copy | No mismatch (prefix matches) |

Something wrong? [troubleshooting.md](troubleshooting.md). Removing it:
[troubleshooting.md#removing-it](troubleshooting.md#removing-it).
