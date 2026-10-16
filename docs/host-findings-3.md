# Host findings, round 3: Claude Code 2.1.300, headless (2026-10-16)

Cases that `docs/hosts.md` and `docs/e2e-real-sessions.md` did not cover. **Real host, headless
`claude -p` only; interactive mode was not run.** Everything ran in throwaway projects under a
scratch directory, installed with this clone's `install.py --adapter claude-code`, on the signed-in
subscription. Model alias `haiku`, `--effort low`.

Totals: **19 `claude -p` sessions**; the host's own `total_cost_usd`
summed to about US$0.02 (about US$0.001 a session; subscription use, nothing separately billed).

Classes: **(A)** protocol defect, **(B)** Postbox host-integration defect, **(C)** host behaviour
outside Postbox's control (or a documented design choice).

## The base command

Every session used (prompt on stdin, `$ID` set as `AGENT_MAIL_IDENTITY`, cwd = the project unless
the row says otherwise, no `AGENT_MAIL_*` or parent `CLAUDE*` variable inherited):

```text
claude -p --model haiku --effort low --max-turns N --tools "<empty, or Bash>" \
  --setting-sources project --strict-mcp-config --permission-mode dontAsk \
  --output-format stream-json --verbose --include-hook-events --no-session-persistence \
  [--allowedTools 'Bash(<rule>)'] [--resume <id>]        # persistence only where a row says --resume
```

"Delivered" means the `UserPromptSubmit` hook event carried the text (its `stdout` byte count is
given); the model's answer is the weaker signal.

## Results

| # | Case | Exact command (delta from the base) | Observed | Class | Sessions | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| 1a | Project path with spaces and a quote: `.../it's a "q" dir  x`; hook command `python3 "$CLAUDE_PROJECT_DIR/tools/agent-postbox/hooks/agent_mail_check.py" \|\| true` | `--tools ""`, `ID=agent-b`, NOTICE `e2e control-3fa3011c`, prompt "reply with only the control token ... or NONE" | hook `exit=0 outcome=success stdout=800B`; answer `control-3fa3011c`. The registered command survives the path: the host expands `$CLAUDE_PROJECT_DIR` inside double quotes | none (works) | 1 | ~$0.001 |
| 1b | Same project: the `read:` and `Reply:` lines the hook prints | `--tools Bash --max-turns 4 --allowedTools` with four rules (absolute path double-quoted and bare, the relative path, and `python3 *agent_mail.py*`), prompt "answer the ASK using the Reply command shown" | The printed line was `python3 "<path with a " inside>/agent_mail.py" send ...`: **not a valid shell command** (the path's own `"` ends the quoting; `$` and a backtick would also be re-interpreted). The model recovered by another spelling, most likely the relative-path rule I had also allowed (ANSWER filed, 0 denials; the exact command it ran was not captured), so the harm is a broken command and a rule that cannot match, not lost mail. `doctor` prints the same unescaped form in its two suggested `Bash(...)` rules | **B** (hook, fixed here); the same form in `agent_mail.py`'s `doctor` rules, **routed to Agent A** | 1 | ~$0.001 |
| 1c | After the fix to `hooks/agent_mail_check.py` (path single-quoted only when it holds `"`, `$`, a backtick, a backslash or a newline) | `--allowedTools "Bash(python3 '<shlex-quoted tool>' *)"`, ASK `fixcheck2 ...` | Printed line is shell-valid; the session ran it as printed; 0 denials; ANSWER `reply_to` = the ASK, ASK `answered`. A normal path still prints `python3 "<path>"` (the form `doctor`'s rules match) | fixed, regression `test_adapters.PrintedCommandQuoting`. (The first run of this row used a rule I had spelled wrongly, `'...'` around an unescaped path: the host denied the `show` command, as it should; the second, with `shlex.quote`, passed) | 2 | ~$0.002 |
| 2a | Nested git repository inside the project (`vendor/inner/.git`), host started at the **project root** | `cwd=<project>`, NOTICE `control-91ec22ba` | hook ran, `stdout=776B`, answer `control-91ec22ba` | none (works) | 1 | ~$0.001 |
| 2b | Same project, host started **inside the nested repo** | `cwd=<project>/vendor/inner`, same prompt | `no UserPromptSubmit hook event`; answer `NONE`. Same as the subdirectory finding in `hosts.md`: the host loads project settings from the directory it starts in. Offline from there: `list` prints `NO MAILBOX at <inner>/docs/agent-mail`, `send` exits with `ABORT: ancestor mailbox exists. Refusing to mint a second owner` (PROTOCOL.md section 9: a nested `.git` under an ancestor mailbox is occupied). A deliberate refusal, not an empty inbox | **C** for the missing hook; the tool's refusal is documented design | 1 | ~$0.001 |
| 3a | Two simultaneous sessions, **same** identity `agent-b`, one owed ASK | two `claude -p` started together, `--tools Bash --max-turns 4 --allowedTools 'Bash(python3 "<abs>/agent_mail.py" *)'`, prompt "answer it using the Reply command, `--result DONE`; then say SENT" | each session had exactly 1 hook event and was shown the ASK once; **both answered**: mailbox has the ASK (`answered`) and **two ANSWERs** with the same `reply_to`. The spec says a second answer to an answered ASK changes nothing (PROTOCOL.md, "a second answer to an answered ASK changes nothing"); there is no read state, so two sessions with one identity are one agent owed twice | by design (spec); not a defect. Advice: one identity per concurrent session (`agent-b-1`, `agent-b-2`) | 2 | ~$0.003 |
| 3b | Two simultaneous sessions, **different** identities `agent-b`, `agent-c`, one ASK each | same | each session was shown its own ASK once (1 hook event each) and answered only it: one ANSWER from `agent-b` to the ASK for `agent-b`, one from `agent-c` to the ASK for `agent-c`; both ASKs `answered`; no cross-answer, no duplicate | none (works) | 2 | ~$0.003 |
| 4a | **Failed send** (host permission refusal): `--tools Bash`, `--permission-mode dontAsk`, only unrelated rule `Bash(ls *)`; ASK owed to `agent-b` | prompt "answer it using the Reply command ... say SENT or say what went wrong" | 1 denial (the `show` command). The model said plainly "not sent. The reply is still owed" and printed the commands to run. Mailbox: ASK still `open`; `status` shows `asks: open=1`. `doctor` says `send_permission: claude-code - no rule allows send. ... an unattended or dontAsk session refuses every send (the agent sees only 'permission denied')` and prints both rules to add | **C** (host refusal); Postbox's diagnostic (`doctor`) is accurate | 1 | ~$0.001 |
| 4b | Next session after 4a, still no rule | `--tools ""`, prompt "how many agent mail messages are awaiting your reply, which id?" | hook `stdout=904B`; the model named the ASK id and its subject. The refused send left no state to repair; the pickup keeps showing the owed ASK | none (works) | 1 | ~$0.001 |
| 4c | Then with the correct absolute rule | `--allowedTools 'Bash(python3 "<abs>/agent_mail.py" *)'` | SENT; 0 denials; ASK `answered`, one ANSWER | none (works) | 1 | ~$0.001 |
| 5a | Stale metadata: a session started as `agent-b` (persisted), then `--resume <id>` with `AGENT_MAIL_IDENTITY=agent-c` | `--resume 4f53857f-...`, persistence on | the hook ran for the **resuming process's** identity: `stdout=776B` with `agent-c`'s token. One resumed answer was `NONE` (the model, looking at a history that already held `agent-b`'s token, misread the prompt); a repeat named `control-51affc0f` correctly. Identity is taken from the environment at each prompt, never from session metadata | **C** (the transcript still holds the first identity's mail; model noise on resume) | 3 (first session, resume, repeat) | ~$0.002 |
| 5b | `--resume` with `AGENT_MAIL_IDENTITY` unset | same | no output (`stdout=0B`), answer `NONE`: silent, as designed (no default identity). Indistinguishable from an empty inbox without `doctor` (`AGENT_MAIL_IDENTITY` line) | documented design (hooks fail silent; section 8) | 1 | ~$0.001 |
| 5c | `--resume` with identity `'Agent-B '` (case and trailing space) | same | hook and CLI agree: both normalise to `agent-b` (`doctor` prints `AGENT_MAIL_IDENTITY: 'agent-b'`, `inbox` lists the mail); token delivered | none (works) | 1 | ~$0.001 |
| 5d | `--resume` as `agent-b` again | same | token delivered again (no read state; the same mail every prompt) | none (works) | 1 | ~$0.001 |

Session accounting: 1a 1, 1b 1, 1c 2, 2a 1, 2b 1, 3a 2, 3b 2, 4a-4c 3, 5a 3, 5b 1, 5c 1, 5d 1 = 19. Costs are
the host's reported `total_cost_usd`, a proxy: they do not model subscription usage.

## What was routed

* **(B) `agent_mail.py`, Agent A's file.** `doctor`'s `send_permission` lines and any printed
  `python3 "<path>"` command put the tool path inside double quotes without escaping. For a project
  path that contains `"`, `$`, a backtick or a backslash the printed command is wrong and the rule
  cannot match. Minimal repro: create a directory named `it's a "q" dir`, `install.py` into it, run
  `python3 tools/agent-postbox/agent_mail.py doctor` and read the second `Bash(python3 "..." *)`
  line. Proposed patch: reuse the hook's rule (`shlex.quote` only when the path holds `"`, `$`,
  a backtick, a backslash or a newline; otherwise keep the double quotes, which `doctor`'s rule form
  and the existing tests pin).
* Nothing else is a Postbox defect. The nested-repository and same-identity rows are documented
  behaviour; `docs/hosts.md` now carries the one-identity-per-concurrent-session advice.

## What this does not show

Interactive mode, other Claude Code versions, other operating systems, Windows paths, and any real
model other than `haiku`.
