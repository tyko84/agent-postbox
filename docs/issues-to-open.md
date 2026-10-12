# Starter issues, drafted

Five issues the owner can paste into GitHub, one per heading. None is open
yet; this file is the draft, not the tracker. Each names a known gap, what
was observed, what "done" means, which files it touches and how to check it.
Delete an entry here once its issue exists.

Every one of them follows the project's rules (CONTRIBUTING.md): standard
library only; a change in behaviour is a new numbered `PROTOCOL.md` section,
never an edit that changes what an old one means; a new check asserts on
content that must appear; `CHANGELOG.md` gets a line.

Observations below were made on 2026-10-12 against the tree at version 0.4.0
(unreleased), with Python 3.12 on macOS, using only the shipped CLI.

---

## 1. `status --json`: report ask results as keys, not only as problem codes

**Title:** `status --json` should carry `ask_results` and `results_attention`

**Context.** An ANSWER can carry `--result` (PROTOCOL.md section 39). Text
`status` prints one line for it:

```text
ask results (claimed, not proven): with_result=1 (DONE=1)  answered=0  unanswered=0
```

and, when something needs a look, a `results attention` table. `status
--json` has neither. Section 39 says so on purpose ("No other key is added"):
the JSON only gains four codes in `problems`, and a consumer is told to
derive the rest from `list --json --type ASK`. So a tool that wants the
counts the text view already prints has to parse prose or re-derive them.
Adding the keys is blocked by a deliberate pin in `test_hardening.py`
("documented key set is exactly stable"), which compares the top-level keys
of `status --json` with an exact list.

**Proposal.** Add two keys, and relax the pin deliberately rather than by
accident:

* `ask_results`: `{"with_result": N, "by_value": {"DONE": n, ...},
  "answered_without_result": N, "unanswered": N}`, the numbers of the text
  line, present (with zeros) whether or not any ANSWER carries a result;
* `results_attention`: the rows of the text table (`reason`, `ask`, `answer`,
  `sender`, `result`), capped like `claims_attention` with a
  `results_attention_truncated` count.

**Acceptance criteria.**

* A new numbered `PROTOCOL.md` section defines both keys and states that
  section 39's "No other key is added" described 0.4.0; sections 29, 33 and 34
  already promise that keys are only ever added.
* `test_hardening.py`'s key-set check lists the new keys explicitly, with a
  comment naming the new section. It stays an exact comparison: the point of
  the pin is that a key cannot appear unannounced.
* Text `status` is byte-for-byte unchanged, for mailboxes with and without
  results.
* A mailbox with one `DONE` result reports `with_result: 1` and
  `by_value.DONE: 1`; a contested or third-party result appears in
  `results_attention` with the same reason code `problems` counts.
* Subjects and bodies never appear in either key.

**Files to touch.** `agent_mail.py` (`build_status`, next to
`_results_lines`), `PROTOCOL.md` (new section), `README.md` (Observability:
`status`), `test_hardening.py` (the pin), `selftest.py` or `test_cli.py` (the
new assertions), `CHANGELOG.md`.

**How to test.** `python selftest.py`, `python test_hardening.py`,
`python test_cli.py`, and by hand: the fourth runnable block of README.md
(Using the CLI), then `agent-postbox status --json`.

---

## 2. `inbox` exits 2 while a quarantined file is present, although the admitted mail is listed

**Title:** Decide what `inbox` / `list` exit when mail was listed in full but a quarantined file exists

**Context.** A file the reader refuses (unknown `type`, malformed id, and so
on) is quarantined: reported, never delivered, never deleted. While one is
in the mailbox, `list`, `inbox` and `latency` exit 2. This is documented
(README.md, Exit statuses; PROTOCOL.md sections 33, 37 and 38) and the
command says so on stderr. The open question is whether it is the right
contract for the caller, because the listing itself is complete and correct.

**Observed.** A mailbox with one real ASK to `agent-b` and one planted file
with `type: ACK`:

```text
$ agent_mail.py inbox --to agent-b
REJECT 01ARZ3NDEKTSV4RRFFQ69G5FAV-planted.md: unknown type 'ACK' (CANNOT_VERIFY; closed set ASK/ANSWER/NOTICE/CLAIM/DISPUTE)
hint: 1 quarantined file(s) (REJECT above) are never delivered and never deleted; the admitted mail is still listed in full, and the exit status is 2 until they are repaired or moved out of the mailbox
# inbox: agent-b  mailbox: <mailbox>  (AGENT_MAIL_DIR)
! ASK     [open      ] agent-a -> agent-b  real question
    01M4R38Y0FRZ5PR9R60TMQC243
[exit 2]
```

`inbox --to agent-b --json` printed the complete document (`"matched": 1`,
`"quarantined": 1`, the ASK's row) and also exited 2. `list --to agent-b
--live` behaved the same. `status` exited 0 with `PROBLEMS: QUARANTINED=1`.
The pickup adapter exited 0 and surfaced the ASK. `doctor` exited 2 with
`FAIL: reject envelopes present`.

**Why it matters.** A polling host that treats a non-zero exit as "the poll
failed" discards a listing that was complete, so one stray file in a shared
mailbox makes every poller deaf while the adapter keeps working. Anyone who
can write a file into the mailbox can cause it. `scripts/scenario.py` has to
count exit 2 from a poll as a success for this reason
(docs/benchmarking.md, "pickup success rate").

**Decide one of:**

1. Keep exit 2, and document the contract where a wrapper author will find
   it: "exit 2 with a complete listing on stdout means quarantine; read
   stdout anyway". The `--json` document's `quarantined` count already lets a
   tool tell this case from a refusal with empty stdout.
2. Give the case its own exit status, distinct from "refused, nothing
   listed".
3. Exit 0 from `list` and `inbox` when the listing is complete, keep the
   `REJECT` and `hint:` lines on stderr and the `quarantined` count in JSON,
   and leave the non-zero exit to `doctor`.

**Acceptance criteria.**

* The decision is written as a new numbered `PROTOCOL.md` section (options 2
  and 3 change section 33's table, which is a behaviour change; option 1 is a
  clarification and may live in the README and `--help` alone).
* The README's Exit statuses table, `agent-postbox --help` and
  `list`/`inbox --help` say the same thing.
* A test plants an `ACK` file next to one real ASK and asserts the listing on
  stdout, the `REJECT` line on stderr and the chosen exit status, for `list`,
  `inbox`, `inbox --json` and `latency`.
* The adapter still exits 0 and still surfaces the admitted mail.

**Files to touch.** `agent_mail.py` (`cmd_list`, `cmd_inbox`, `cmd_latency`,
the help epilog), `PROTOCOL.md`, `README.md`, `test_cli.py` or `selftest.py`;
for options 2 and 3 also `scripts/scenario.py` (how the baseline classifies a
poll) and `docs/benchmarking.md`.

**How to test.** Reproduce with:

```sh
export AGENT_MAIL_DIR="$PWD/box"; mkdir -p "$AGENT_MAIL_DIR"
python agent_mail.py send --type ASK --from agent-a --to agent-b --subject "real question" --body hello
ID=01ARZ3NDEKTSV4RRFFQ69G5FAV
printf -- '---\nid: %s\ntype: ACK\nfrom: agent-a\nto: agent-b\nsubject: planted\ndate: 2026-10-12T00:00:00Z\n---\nbody\n' "$ID" > "$AGENT_MAIL_DIR/$ID-planted.md"
python agent_mail.py inbox --to agent-b; echo "exit $?"
```

then `python selftest.py`, `python test_cli.py` and
`python scripts/scenario.py --quick --seed 1`.

---

## 3. A key marker stays orphaned when the retry of a killed keyed CLAIM is refused at the scope stage

**Title:** Keyed CLAIM killed mid-send: a retry refused with exit 3 never clears its own orphaned key marker

**Context.** `send --key K` reserves a marker file `.idem.<hash>` and holds a
kernel lock on it while it publishes (PROTOCOL.md section 31). A sender that
dies leaves the marker; a retry with the same key takes it over at once. For
a scoped `CLAIM` the order is: take the scope lock, check for a rival holder,
then handle the key. If, between the death and the retry, another agent
claimed the scope, the retry is refused at the rival check and never reaches
the key stage, so nothing takes the marker over. docs/benchmarking.md names
this as the one fault `scripts/scenario.py` deliberately does not inject.

**Observed.** With the same technique `test_field.py` and
`scripts/scenario.py` use (the real `agent_mail.main` in a child, `os.link`
made to block, then SIGKILL):

1. `agent-a` sends `CLAIM --scope src/x.py --key k-a-1` and is killed after
   reserving the key and writing its temp file. Left behind: one
   `.<id>-a-claims.md.<pid>.tmp`, one `.idem.<hash>`, one
   `.scope.<hash>.lock`.
2. `agent-b` sends a `CLAIM` on `src/x.py`: exit 0, written. The scope lock
   file is gone afterwards (reclaimed, as section 30 says).
3. `agent-a` retries the identical keyed claim:
   `scope 'src/x.py' is already held by agent-b (..., until ...)`, exit 3.
   The `.idem.<hash>` file is still there.
4. `status` right away: `idem_markers: total=1 resolved=0 in_flight=1 held=0
   orphaned=0`, no problem reported. `status --now` 61 seconds later:
   `idem_markers: total=1 resolved=0 in_flight=0 held=0 orphaned=1` and
   `PROBLEMS: ORPHAN_IDEM_MARKER=1`.

Control: a keyed `CLAIM` that is simply refused at the scope stage, with no
earlier crash, left no hidden file at all. The marker comes from the killed
sender; what is missing is that its own refused retry does not clear it.

The marker is inert (it blocks nobody but a retry of the same key, which
would take it over), and `status` and `doctor` report it. But it stays until
a retry with the same content gets past the scope check, which may be never
if the agent gives up once the scope is taken, and until then an operator
sees `ORPHAN_IDEM_MARKER` with no command that clears it.

**Proposal.** When a keyed send is refused at the scope stage, look at its
own key: if a marker for that key exists, names no landed message, and no
live process holds its lock, remove it under the same takeover rules section
31 already defines. A marker held by a live sender, or one whose message
landed, is left alone.

**Acceptance criteria.**

* After steps 1 to 3 above, no `.idem.*` file for that key remains, and
  `status --now` 61 seconds later reports `idem_markers.orphaned == 0` and no
  `ORPHAN_IDEM_MARKER`.
* A marker whose holder is alive is never removed by a refused retry (the
  retry still exits 3 and the live sender still publishes exactly once).
* A later retry of the same key, after the rival claim has expired, still
  writes exactly one message.
* The refusal still writes no message and still exits 3.
* A new numbered `PROTOCOL.md` section states the rule; section 31 is not
  rewritten.
* `scripts/scenario.py` can then inject this fault instead of avoiding it,
  and the paragraph in docs/benchmarking.md that explains the omission is
  updated (may be a follow-up).

**Files to touch.** `agent_mail.py` (the scope refusal in `cmd_send` and the
key takeover helpers), `PROTOCOL.md`, `test_field.py` (it already has the
kill-at-`os.link` harness), `CHANGELOG.md`; optionally `scripts/scenario.py`
and `docs/benchmarking.md`.

**How to test.** `python test_field.py`, `python stress_test.py`,
`python selftest.py`, `python scripts/scenario.py --seed 1`.

---

## 4. `status --json`: let a tool see a dead holder without shifting the clock by 61 seconds

**Title:** `status --json` hides whether a young lock or key marker has a live holder; tools pass `--now` +61 s to find out

**Context.** `status` classifies each scope lock and key marker as in flight,
held by a live process, or orphaned (PROTOCOL.md section 33). A file younger
than 60 seconds is counted `in_flight` and is not probed at all. That is
right for a person (a send in progress is not a problem), but a tool that
has just killed or lost a sender cannot ask "is anyone holding this?" for a
minute. `scripts/scenario.py` works around it by reading `status --json`
twice at every boundary, the second time with `--now` set 61 seconds ahead
(docs/benchmarking.md, "The workload and its determinism" and the
recovery entry under "How to read the output"), and `status --help` describes `--now` as
being for tests. Shifting `--now` also shifts everything else `status`
derives at that instant (claim expiry, stalled agents), so the second
reading is not the same picture with one difference.

**Observed.** In the reproduction of issue 3, immediately after the sender
was killed `status` reported the marker as `in_flight=1` with no problem;
the same mailbox read with `--now` 61 seconds ahead reported `orphaned=1`
and `ORPHAN_IDEM_MARKER=1`. Nothing in the first document says that the
holder is already dead.

**Proposal (either, or both).**

* `status --no-grace`: probe every lock and marker regardless of age, so
  `held` and `orphaned` are current and `in_flight` is 0. The 60 second
  window exists for a reason (a sender may have created its file and not yet
  locked it), so the new section must say what a tool may conclude from
  `orphaned` on a very young file, and the default must not change.
* A per-file list in `status --json`, for example `hidden_files`: one row per
  lock and marker with `kind`, `age_seconds` and `holder` (`held`, `free` or
  `unknown`, straight from the read-only probe), capped and with a
  `_truncated` count like the other lists. The file name is a hash and
  carries no scope text, but confirm that before exposing it.

**Acceptance criteria.**

* Default `status` and `status --json` output is unchanged except for keys
  that are added (and `test_hardening.py`'s exact key-set pin is updated
  deliberately, as in issue 1).
* With the new flag or field, a marker whose sender was SIGKILLed is
  reported as having no live holder in the first reading, with the real
  clock.
* A marker or lock whose sender is alive (parked, not killed) is reported as
  held, never as orphaned.
* `status` still writes nothing and still exits 0; the probe stays read-only.
* `scripts/scenario.py` can drop its second, shifted reading (may be a
  follow-up).
* A new numbered `PROTOCOL.md` section; `README.md` (Observability) and
  `status --help` mention it.

**Files to touch.** `agent_mail.py` (`scan_hidden`, `build_status`, the
`status` parser), `PROTOCOL.md`, `README.md`, `test_hardening.py`,
`test_field.py` (live versus dead holder), `CHANGELOG.md`; optionally
`scripts/scenario.py` and `docs/benchmarking.md`.

**How to test.** `python test_field.py`, `python test_hardening.py`,
`python selftest.py`, `python scripts/scenario.py --seed 1`.

---

## 5. Confirm the pickup adapter inside a real second host

**Title:** Field check: run `hooks/agent_mail_check.py` from a host other than Claude Code and record what happened

**Context.** The adapter is host-neutral by design: it reads
`AGENT_MAIL_IDENTITY`, prints live mail on stdout and always exits 0
(PROTOCOL.md sections 9 and 33). `install.py --adapter claude-code` registers it for
one host. Every automated test, `scripts/scenario.py` and
`examples/two-agents/run.py` run the adapter as a subprocess, which shows
what it prints, not that a given host puts that text in front of its model.
For any host other than Claude Code the repository has no recorded
confirmation at all. `docs/hosts.md` holds the per-host wiring notes and the
checklist to follow; if that file is not in the tree yet, this issue waits
for it.

**What to do.** Pick one host that has a prompt-submit (or equivalent) hook,
follow the checklist in `docs/hosts.md` from start to finish, and report.
This is a field report, not a code change, unless the check finds a bug.

**Acceptance criteria.**

* The report names the host and its version, the operating system, the
  Python version and `agent_mail.py --version`.
* It shows the hook registration used (the host's own configuration, with
  private paths replaced).
* It shows, for one ASK sent from another identity: the adapter's output as
  the host captured it, and evidence that the text reached the model's
  context (the host's transcript or debug view, or the model quoting the
  message id back). A screenshot of the adapter run by hand does not count.
* It states what happens when `AGENT_MAIL_IDENTITY` is unset in the host's
  hook environment, since a silent adapter looks like an empty inbox.
* It follows CONTRIBUTING.md's field report walkthrough: no message bodies,
  no private names, paths or hosts.
* `docs/hosts.md` records what was observed for that host, with the date and
  the versions; a host that does not work is recorded as such, with the
  reason.

**Files to touch.** `docs/hosts.md`; `PARTICIPANTS.template.md` only if the
host needs a mechanism wording that is not there; `hooks/` only if a bug is
found (then with a test in `selftest.py`).

**How to test.** By hand, in the host. Before and after:
`python tools/agent-postbox/selftest.py` in the project, and
`python examples/two-agents/run.py` in the clone to confirm the adapter
itself behaves on that machine.
