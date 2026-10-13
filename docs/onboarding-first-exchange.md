# First exchange in five minutes

From nothing to two agents exchanging a message in a project of yours. Every
command below was run on 2026-10-13 from a fresh copy of the repository in a
throwaway git project (macOS, Python 3.13, no model, no host, no network after
the clone). The timings are one run on one laptop.

Requirements: Linux or macOS, Python 3.10-3.14, git. **Windows is unsupported
and untested**; so is WSL (not tried). Use `python3`, not `python`: stock macOS
has no `python`.

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

1. Open `docs/agent-mail/PARTICIPANTS.md` and rename the example rows
   `agent-a` and `agent-b` to your agents' names (or keep those names for the
   trial).
2. In the shell each session starts from, one name per agent:

   ```sh
   export AGENT_MAIL_IDENTITY=agent-a      # in agent B's shell: agent-b
   ```

   `send` always takes `--from` explicitly. `AGENT_MAIL_IDENTITY` is read by
   `inbox` and by the pickup adapter, not by `send`.

## 4. File the first canary, then `doctor`

```sh
python3 tools/agent-postbox/agent_mail.py doctor    # ends in FAIL, exit 2
python3 tools/agent-postbox/agent_mail.py canary --from agent-a --to agent-a
python3 tools/agent-postbox/agent_mail.py doctor    # ends in PASS
```

The first `doctor` on a fresh install reports `FAIL: pickup canary missing or
stale`. That is expected: nothing is wrong, no canary has been filed yet. The
canary lasts 24 hours, so a working install fails `doctor` again the next day
until `canary` is re-run. `doctor` also reports whether the hook is registered
(`pickup_hook: registered`) and whether `AGENT_MAIL_IDENTITY` is set in the
shell you ran it from.

## 5. Send and receive

Agent A:

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
python3 tools/agent-postbox/agent_mail.py send --type ANSWER --from agent-b --to agent-a \
    --reply-to <id from the ASK> --subject "re: billing client" --body-file answer.txt
```

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

## Timings, this run

| Step | Wall clock |
|---|---|
| `git clone` | under 1 s |
| demo (`run.py`) | 0.4 s |
| `install.py` | 0.03 s |
| `canary`, `doctor`, `send`, hook pickup | about 0.05 s each |
| `selftest.py` | 19 s |
| `python3 -m venv` + `pip install ./agent-postbox` (optional: puts `agent-postbox` on PATH) | 1.1 s + 1.4 s |

By commands alone a first exchange takes a few seconds; reading takes the rest.

Something wrong? [troubleshooting.md](troubleshooting.md). Removing it:
[troubleshooting.md#removing-it](troubleshooting.md#removing-it).
