# Starter issues, drafted

This file is a draft, not the tracker. Each entry below is ready to paste into
GitHub. Delete an entry once its issue exists. Entries under **Hold** must not
be opened until the question named for them is decided.

Every contribution follows CONTRIBUTING.md: standard library only; a change in
behaviour is a new numbered `PROTOCOL.md` section, never an edit that changes
what an old one means; a new check asserts on content that must appear; POSIX
only; `CHANGELOG.md` gets a line under `[Unreleased]`.

Every observation below was re-run on 2026-10-12 against the tree at version
0.5.0 (`agent_mail.py --version` prints `agent-postbox 0.5.0`), Python 3.12,
macOS, in a throwaway directory with `AGENT_MAIL_DIR` pointing at it. Line
numbers are for that tree and drift; the names beside them are stable.

## Ranked: open now / hold

| Rank | Entry | Decision | Labels | Why |
| --- | --- | --- | --- | --- |
| 1 | A. CONTRIBUTING.md names 6 of the 26 files CI type-checks | opened as #20 | `documentation`, `good first issue`, `help wanted` | A demonstrated gap (26 files in `ci.yml` against 6 in CONTRIBUTING.md), one file to edit, checkable by diffing two strings |
| 2 | B. Run the pickup adapter inside a real Codex and record the result | opened as #21 | `field-report`, `help wanted` | `docs/hosts.md` states that nothing was observed inside Codex and gives a five-step checklist with a positive control; needs only a Codex install |
| 3 | C. `status --json`: report ask results as keys | hold | `enhancement` | Reverses a choice section 39 made on purpose, and a consumer can already derive the numbers from `list --json`; no one has asked for it |
| 4 | D. `inbox` / `list` exit 2 while the listing is complete and a file is quarantined | hold | `enhancement` | The behaviour is documented and intended; whether to change an exit status is the owner's decision (three options, one is "do nothing") |
| 5 | E. `status`: see a dead holder inside the first 60 seconds | hold | `enhancement` | Reproduced, but the only consumer is this repo's own harness, and the fix is a protocol design choice (flag or field) not yet made |
| - | Former issue 3 (orphaned key marker after a refused retry) | **dropped** | - | Already fixed: PROTOCOL.md section 31 "Refused at the scope stage", `test_field.py` phase "orphan", CHANGELOG.md line 207 |

A and B are open (#20, #21); they do not depend on each other or on anything in Hold.
C, D and E are independent of each other, but C and E both add keys to
`status --json` and therefore both touch the same pin (see Traps); if both are
ever accepted, take them one after the other.

## Traps that apply to any code change (put a link to this list in the issue)

* `test_hardening.py` (the check named "documented key set is exactly stable",
  line 279) compares the top-level keys of `status --json`, and the keys of
  six of its sub-objects, with an exact list. Adding a key fails it until the
  list is edited on purpose, with a comment naming the new `PROTOCOL.md`
  section. Keep it an exact comparison.
* `test_publication.py` requires every `.py` file in the root, `hooks/`,
  `scripts/` and `examples/` to be on the one `mypy` line of
  `.github/workflows/ci.yml` (`mypy_files`, `python_files`, line 1652 and
  following), and a new test file to be on every list that names the test
  files: the steps of `ci.yml`, the `test` job of `release.yml`,
  `.github/pull_request_template.md`, `CONTRIBUTING.md` and
  `scripts/preflight.sh`. A new file added in one place only fails
  `python test_publication.py`; copy what `test_team_sim.py` did.
* `test_readme_examples.py` executes every `bash runnable` block of
  `README.md` and resolves every "section N" reference to a real `PROTOCOL.md`
  heading. Change an example or renumber nothing without running it.
* `PROTOCOL.md` section numbers are never reused. The next free number is the one after the last heading in `PROTOCOL.md`
  (the last heading is "40. Identity binding").
* Never run the CLI against the checkout as a mailbox (SECURITY.md): set
  `AGENT_MAIL_DIR` to a temporary directory, as the commands below do.

---

## Opened

* A (CONTRIBUTING.md's `mypy` command) is issue #20 (`documentation`, `good first issue`, `help wanted`).
* B (field check of the adapter inside the real Codex CLI) is issue #21 (`field-report`, `help wanted`).

# Hold

Do not open these. Each has been reproduced and corrected, so that if the owner
decides to proceed the text is ready, but each depends on a decision that has
not been made, or has no demonstrated user.

## C. `status --json`: report ask results as keys

**Labels if opened:** `enhancement`, `help wanted`. Not `good first issue`: it
adds a protocol section and edits an exact pin.

**Hold because.** Section 39 chose not to add these keys ("No other key is
added: which result stands on which ASK is a row of `list --json --type ASK`,
and a consumer that wants only the ASKs with results filters that document on
`result`", PROTOCOL.md lines 1922 to 1927). Reversing that is a design
decision, and the derivation it points to works today. Open it only when a
real consumer reports that deriving the numbers is a problem.

**Title:** `status --json` should carry `ask_results` and `results_attention`

**Evidence.** In a clean mailbox: one ASK from `agent-a` to `agent-b`, then
`send --type ANSWER --from agent-b --to agent-a --reply-to <ask id> --result
done --reason ok --subject re --body x`. Then `status`:

```text
messages: 2  (live ANSWER=1, ASK=0, CLAIM=0, DISPUTE=0, NOTICE=0)
asks: open=0  oldest_open_age=-
claims: held=0  expired=0  malformed_expiry=0  superseded=0
ask results (claimed, not proven): with_result=1 (DONE=1)  answered=0  unanswered=0
```

`status --json` on the same mailbox has 23 top-level keys (`agents`, `asks`,
`claims`, `claims_attention`, `claims_attention_truncated`, `claims_held`,
`claims_held_truncated`, `contested_scopes`, `dangling_replies`,
`foreign_supersedes`, `idem_markers`, `mailbox`, `messages`, `now`,
`problems`, `quarantined`, `schema`, `scope_filter`, `scope_locks`,
`stale_tmp`, `stalled_agents`, `stalled_hours`, `takeover_mutexes`), none of
which carries the line above; `problems` is `[]`. A planted unattached result
shows as a text table row (`UNATTACHED_RESULT  -  <answer id>  agent-b  DONE`)
and as `{"code": "UNATTACHED_RESULT", "count": 1}`, with no row in JSON. The
text comes from `_results_lines` (`agent_mail.py` line 3204), called from
`cmd_status` (line 3241).

**Proposal.** Two keys: `ask_results` (`with_result`, `by_value`,
`answered_without_result`, `unanswered`) and `results_attention` (rows of
`reason`, `ask`, `answer`, `sender`, `result`, capped like `claims_attention`
with a `results_attention_truncated` count).

**Acceptance criteria.**

- [ ] A new numbered `PROTOCOL.md` section (the next free number) defines both keys. Section 39
      is not edited; the new section says it supersedes the one sentence
      "No other key is added". Sections 29 (line 775) and 37 (line 1679)
      already say keys are only ever added within a schema.
- [ ] `test_hardening.py` line 279: the key-set list names the new keys
      explicitly, with a comment citing the new section. It stays an exact
      comparison.
- [ ] Text `status` is byte-for-byte unchanged, with and without results.
- [ ] A mailbox with one `DONE` result reports `with_result: 1` and
      `by_value.DONE: 1`; a third-party or contested result appears in
      `results_attention` with the reason code `problems` counts.
- [ ] Subjects and bodies appear in neither key.
- [ ] `python selftest.py`, `test_hardening.py`, `test_cli.py` and
      `test_readme_examples.py` pass; README.md "Observability: `status`" (line
      408) lists the keys; `CHANGELOG.md` has a line.

**Files.** `agent_mail.py` (`build_status`, line 3009), `PROTOCOL.md`,
`README.md`, `test_hardening.py`, `test_cli.py`, `CHANGELOG.md`.

**Out of scope.** Any change to `list`/`inbox`/`show` rows; any new result value.

**Dependencies.** Shares the `test_hardening.py` pin with E; land one at a time.

---

## D. `inbox` / `list` exit 2 although the listing is complete

**Labels if opened:** `enhancement`. Not `good first issue`: it changes an exit
status that wrappers depend on.

**Hold because.** The exit status is documented and deliberate (README.md
line 502, PROTOCOL.md lines 1142, 1642 and 1782, and the command says so on
stderr). Whether to change a documented exit status in a 0.x tool is the
owner's call. The only part that needs no decision, the documentation, already
exists. If the owner wants to keep exit 2, close this as "working as
documented".

**Title:** Decide what `inbox` / `list` exit when mail was listed in full but a quarantined file exists

**Evidence.** One real ASK to `agent-b` and one planted file with `type: ACK`:

```sh
export AGENT_MAIL_DIR="$(mktemp -d)/box"; mkdir -p "$AGENT_MAIL_DIR"
python agent_mail.py send --type ASK --from agent-a --to agent-b --subject "real question" --body hello
ID=01ARZ3NDEKTSV4RRFFQ69G5FAV
printf -- '---\nid: %s\ntype: ACK\nfrom: agent-a\nto: agent-b\nsubject: planted\ndate: 2026-10-12T00:00:00Z\n---\nbody\n' "$ID" > "$AGENT_MAIL_DIR/$ID-planted.md"
python agent_mail.py inbox --to agent-b; echo "exit $?"
```

Observed: stdout has the header and `! ASK [open] agent-a -> agent-b  real question`
with its id; stderr has `REJECT 01ARZ3NDEKTSV4RRFFQ69G5FAV-planted.md: unknown
type 'ACK' (CANNOT_VERIFY; closed set ASK/ANSWER/NOTICE/CLAIM/DISPUTE)` and a
`hint:` line saying the admitted mail "is still listed in full, and the exit
status is 2 until they are repaired or moved out of the mailbox"; exit 2.
`inbox --to agent-b --json` prints the complete document (`"matched": 1`,
`"quarantined": 1`) and exits 2; `list --to agent-b --live` and `latency` also
exit 2. `status` exits 0 (`PROBLEMS: QUARANTINED=1`); `doctor` exits 2
(`FAIL: reject envelopes present`); `hooks/agent_mail_check.py` with
`AGENT_MAIL_IDENTITY=agent-b` exits 0 and prints the ASK.

`docs/benchmarking.md` line 161 ("pickup success rate") says a poll that
exits 2 because of a planted file counts as a success and is shown under
"pickup exit nonzero", which is why `scripts/scenario.py` (lines 785, 1000)
has to treat it specially.

**Options.** (1) Keep exit 2 and say in the README that stdout is still
complete (a clarification; no new section). (2) Give the case its own exit
status. (3) Exit 0 from `list`/`inbox` when the listing is complete and leave
the non-zero exit to `doctor`. Options 2 and 3 change the PROTOCOL.md section 33
exit table (line 1142) and need a new section; they also touch
`scripts/scenario.py` and `docs/benchmarking.md`.

**Acceptance criteria (after a decision).**

- [ ] The decision is a new numbered section (option 1 may be a README
      clarification only).
- [ ] README.md "Exit statuses" (line 496), `agent-postbox --help` and
      `list`/`inbox --help` agree.
- [ ] A test plants the `ACK` file next to one real ASK and asserts stdout,
      the `REJECT` line on stderr and the exit status for `list`, `inbox`,
      `inbox --json` and `latency`; the adapter still exits 0.
- [ ] `python scripts/scenario.py --quick --seed 1` and `python test_scenario.py` pass.

**Files.** `agent_mail.py` (`cmd_list` line 2645, `cmd_inbox` line 2686,
`cmd_latency` line 1545), `PROTOCOL.md`, `README.md`, `test_cli.py`,
`scripts/scenario.py`, `docs/benchmarking.md`.

**Dependencies.** None.

---

## E. `status`: see a dead holder inside the first 60 seconds

**Labels if opened:** `enhancement`. Not `good first issue`.

**Hold because.** The behaviour is reproduced but intended (PROTOCOL.md
section 33: nothing younger than `PROBE_MIN_AGE_SECONDS`, which is
`IDEM_WAIT_MAX`, 60 s, is probed; `agent_mail.py` lines 2793 and 2902). The
only consumer that needs more is this repository's own harness, which already
works around it with a second reading at `--now` +61 s
(`GRACE_SHIFT`, `scripts/scenario.py` line 199). The fix is a design choice
(a flag, a per-file list, or neither) with a safety argument that has to be
made first: the grace exists because a sender may have created a file and not
yet locked it. Open it when someone other than the harness needs it.

**Title:** `status --json` cannot tell a dead holder from a live one for the first 60 seconds

**Evidence.** A sender parked after reserving its key, then SIGKILLed. This
is the technique `test_field.py` uses (`BEFORE_PUBLISH`, line 118): the real
`agent_mail.main` in a child with `_publish` replaced by a blocking function.

```python
import os, signal, subprocess, sys, tempfile, time
repo = "/path/to/agent-postbox"            # a clone; the mailbox below is a temp dir
tmp = tempfile.mkdtemp(); box = os.path.join(tmp, "box"); os.makedirs(box)
env = dict(os.environ, AGENT_MAIL_DIR=box); ready = os.path.join(tmp, "ready")
child = (f"import sys,time; sys.path.insert(0,{repo!r}); import agent_mail\n"
         f"agent_mail._publish = lambda *a, **k: (open({ready!r},'w').close(), time.sleep(600))\n"
         "sys.exit(agent_mail.main(['send','--type','ASK','--from','agent-a','--to','agent-b',"
         "'--subject','s','--body','b','--key','k1']))\n")
p = subprocess.Popen([sys.executable, "-c", child], env=env)
while not os.path.exists(ready): time.sleep(0.05)
status = lambda *a: subprocess.run([sys.executable, repo + "/agent_mail.py", "status", "--json", *a],
                                   env=env, capture_output=True, text=True).stdout
print("alive :", status())           # idem_markers: in_flight 1
p.send_signal(signal.SIGKILL); p.wait()
print("killed:", status())           # identical: in_flight 1, problems []
```

Observed (only the relevant keys):

```text
alive : idem_markers {held 0, in_flight 1, orphaned 0, resolved 0, total 1}  problems []
killed: idem_markers {held 0, in_flight 1, orphaned 0, resolved 0, total 1}  problems []
killed, status --now <real time + 61 s>:
        idem_markers {held 0, in_flight 0, orphaned 1, resolved 0, total 1}
        problems [{"code": "ORPHAN_IDEM_MARKER", "count": 1}]
```

`status --help` describes `--now` as "evaluate at this ISO-8601 instant
(tests)". `doctor` in the killed state prints `idem_markers: 1 (resolved 0,
in_flight 1, held 0, orphaned 0)`.

**Proposal (either, or both).** `status --no-grace`, which probes every lock
and marker regardless of age (default unchanged); or a capped per-file list
with `kind`, `age_seconds` and `holder` (`held` | `free` | `unknown`).

**Acceptance criteria (after a decision).**

- [ ] A new numbered section states what a tool may conclude from `orphaned`
      on a file younger than 60 s; the default output does not change.
- [ ] With the real clock, the SIGKILLed sender above reads as no live holder
      in the first reading; a parked, not killed, sender reads as `held`,
      never `orphaned`.
- [ ] `status` still writes nothing and exits 0 (the probe stays read-only).
- [ ] `test_hardening.py` line 279 (key-set pin) updated deliberately if a
      key is added; `test_field.py` covers live and dead holders.
- [ ] README.md (line 408) and `status --help` mention it.

**Files.** `agent_mail.py` (`scan_hidden` line 2839, `build_status`, `cmd_status`
line 3241 and its parser), `PROTOCOL.md`, `README.md`, `test_hardening.py`,
`test_field.py`, `CHANGELOG.md`; optionally `scripts/scenario.py` and
`docs/benchmarking.md`.

**Dependencies.** Shares the `test_hardening.py` pin with C.

---

# Dropped

**Former issue 3: "Keyed CLAIM killed mid-send: a retry refused with exit 3
never clears its own orphaned key marker".** Fixed and tested:
PROTOCOL.md section 31, "Refused at the scope stage" (line 949);
`agent_mail.py` line 1895 (the helper that clears it); `test_field.py` lines
570 to 640 (the same sequence, a live-holder control and the later retry);
CHANGELOG.md line 207; `docs/benchmarking.md` lines 203 to 211. Do not open.
