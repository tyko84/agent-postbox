# Identity binding: what the file owner proves

`--from` and `AGENT_MAIL_IDENTITY` are labels. Anyone who can run the tool
can type any identity, so a message's `from:` is a claim the writer made
about itself (PROTOCOL.md section 0: evidence, never authority; SECURITY.md,
"Not authenticated"). This page describes an **optional** second piece of
evidence: the owner the kernel records for the message file. It is not
authentication. It is one more thing a reader can compare, with limits that
this page states precisely so nobody mistakes it for more.

The whole feature is opt-in. A mailbox whose `PARTICIPANTS.md` declares no
binding is served byte for byte as before: same lines, same `--json` keys,
same exit statuses (`test_field.py` phase 5b and `test_adversarial.py` pin
this against `status`, `doctor`, `inbox`, `list` and `latency`).

## Vocabulary

* **Self-declared sender**: the `from:` field. Metadata; typed by whoever
  wrote the file.
* **Observed OS owner**: the uid in `st_uid` of the message file, read with
  `lstat` (never following a link). The kernel set it when the file was
  created and only root can change it. It is printed as a number.
* **Bound uid**: the uid a row of `PARTICIPANTS.md` declares for an identity.
* **Reader uid**: the effective uid of the process running `status`, `doctor`
  or the pickup hook. It says nothing about who wrote anything; it is
  reported only to recognise the case where bindings cannot distinguish
  anyone (below).

## Declaring a binding

Add a `uid` column to the Identity table of `PARTICIPANTS.md` and fill the
cell for the rows you want checked:

| Identity | ... | can | cannot | uid |
|---|---|---|---|---|
| `agent-a` | ... | `review` | `press` | `1000` |
| `agent-b` | ... |  | `press` | `deploy` |
| `agent-c` | ... |  |  |  |

* A cell of digits is a numeric uid. Anything shaped like a user name
  (`[A-Za-z_][A-Za-z0-9_.@-]{0,63}`) is resolved through the password
  database (`pwd.getpwnam`) **each time the file is read**, so a renumbered
  account follows automatically. Backticks around the cell are ignored.
* An empty cell, or no `uid` column at all, declares nothing for that row.
* The column is read from the same file the capability columns are read
  from: the first of `<mailbox>/PARTICIPANTS.md`, `<mailbox>/../PARTICIPANTS.md`,
  `./PARTICIPANTS.md`, `<tool dir>/PARTICIPANTS.md` that exists.
* `pwd` is POSIX-only. Where it is missing, a name is **unresolvable** (see
  below) and a numeric uid still works. Windows is unsupported anyway.
* A cell that is neither (`4294967295`, `-1`, `0x1F`, a path, 300 digits, a
  control character) is unresolvable. It is echoed quoted and escaped, in
  one line, never raw.

A binding is to a **uid**, not to a person: `sudo -u deploy`, a setuid
helper, or anyone else who can run as that uid satisfies it.

## What is checked, and where

**Readers (`status`, `doctor`, `verify`), read-only.** For every admitted
message whose self-declared sender has a resolved binding, the observed OS
owner of its file is compared with the bound uid. Nothing is written, no
link is followed, and no name from the password database is ever printed:
the only name you will see is the one your own row declares.

| Where | What appears, only when at least one binding is declared |
|---|---|
| `status` (text) | `identity_bindings: N  reader uid R  mailbox owner uid U mode MMMM`, one line per binding, `identity_check: checked= matched= mismatched= shared_inode= unobserved=`, then one row per finding |
| `status --json` | an `identity` object (schema stays 1; the key is absent without bindings): `reader_uid`, `mailbox {owner_uid, mode, shared_writable, is_symlink}`, `bindings [{identity, declared, uid, problem}]`, `unresolved`, `checked`, `matched`, `unobserved`, `mismatched`, `shared_inode`, `attention [{id, sender, path, reason, observed_uid, bound_uid, links}]` (at most 200, then `attention_truncated`), `distinguishing`; and the problem codes below in `problems` |
| `doctor` | the same lines with `  - ` bullets, then `FAIL:` lines for a mismatch or an unresolvable binding (exit 2); one `note:` line when bindings do not distinguish participants (not a failure) |
| `verify <id>` | one `sender_owner:` line for a bound sender: `self-declared sender X bound to uid B; observed OS owner uid O: MATCH` / `MISMATCH` (or `CANNOT_VERIFY` when unresolvable). Reports only; the exit status stays the fingerprints' |

Problem codes (PROTOCOL.md section 33 table; new codes, existing ones
unchanged):

| Code | Count is | `doctor` |
|---|---|---|
| `IDENTITY_MISMATCH` | messages whose observed owner is not the bound uid of their self-declared sender | FAIL |
| `IDENTITY_UNRESOLVED` | bindings that resolve to no uid (unknown name, malformed cell, no `pwd`) | FAIL |
| `IDENTITY_SHARED_INODE` | checked files with more than one name (`st_nlink > 1`) | reported, not a failure |

`doctor` is the **fail-closed diagnostic**: a binding asks for a check, and
a check that was asked for and did not pass, or could not run, is a failure.
`status` reports and exits 0 as it always has. `list`, `inbox`, `show` and
the pickup hook do not change at all: a mismatched message is still
delivered, as the ordinary mail it is, because a label is a label either way.

**Writer (`send`, and `ask` and `canary` through it).** If the `--from`
identity has a binding, the process's effective uid must be the bound uid:

```
refusing to send: --from bob is bound to uid 1001 in PARTICIPANTS.md and this process runs as uid 1000; remove the uid cell of that row to send without the binding
```

One line on stderr, exit 2, nothing written. A binding that cannot be
resolved refuses too. This is not configurable: a binding that exists is
enforced for its writer, and the way out is to edit or remove the cell.
The file `send` would have written is owned by the process's effective uid,
so this is the same comparison the readers make, made before the write.

**Shared accounts and containers.** Where every agent runs as one uid, a
binding is meaningless, and the check must degrade to *no information*,
never to a pass. When every resolved binding equals the reader's own uid,
`doctor` prints one informational line (`bindings do not distinguish
participants here`) and `status --json` reports `distinguishing: false`.
Everything else is unchanged: nothing fails, and nothing is called verified.

## What the owner proves on a local POSIX filesystem

The kernel records the creating process's effective uid in the inode. Only
a process with `CAP_CHOWN` (root) can set it to anything else. Renaming or
hard-linking the file keeps the inode and therefore the owner. So on a
local filesystem, with no hostile root, an observed owner of `U` proves:

> a process running as uid U created this inode, somewhere.

It does **not** prove that U chose this filename, this directory, or the
text in the file after creation (a group-writable file can be edited by the
group), and it does not prove that U is the person behind the identity.

## What it does not prove: the limits, one by one

* **NFS, SMB and other network mounts with id mapping.** The owner is what
  the server reports after squashing or mapping. With `root_squash` root
  becomes `nobody`; with `all_squash` *everyone* becomes one uid; an SMB
  mount usually shows every file as owned by the mounting user. Bindings
  written for the client's uids are then wrong or uninformative. The lock
  code already trusts network filesystems for nothing (SECURITY.md); treat
  owners there the same way.
* **Containers with uid remapping.** With user namespaces, the uid a process
  sees for a file differs from the host's. A binding written from one view
  mismatches in the other. Declare bindings in the uids the *reader* sees.
* **Shared service accounts and one-uid containers.** No information, as
  above. The note in `doctor` is the only output that changes.
* **`chown`, root and `sudo`.** Root can set any owner, so root can forge a
  match and erase a mismatch. A process that can run `sudo` or escalate is
  root for this purpose. The check assumes the root of the machine is not
  the adversary; if it is, nothing in a filesystem proves anything.
* **setuid helpers and delegated uids.** A setuid program creates files
  owned by its effective uid, not its invoker's; `sudo -u U` does the same
  on purpose. Any route to "a process running as U" satisfies the binding.
  That is what a uid binding means, and why it binds to a uid.
* **A group- or world-writable mailbox directory.** Anyone can create a
  file there, but the owner the kernel records is still the creator, so the
  mismatch still shows, unless they can `chown`. What a writable directory
  does allow is deleting or renaming other people's files (without the
  sticky bit), which this check does not detect. `status --json` reports
  `shared_writable` so an operator can see the exposure.
* **The reader's uid is irrelevant to authorship.** The pickup hook and
  `doctor` run as whoever invoked them. That uid is reported as `reader uid`
  solely to recognise the degenerate case; it is never compared with a
  file's owner to decide anything about the file.
* **Identities without a row, or a renamed row.** Only bound identities are
  checked. Rename `alice` to `alicia` in the table and alice's existing mail
  is unbound, and unchecked, from then on; `identity_check` says how many
  files were checked so the gap is visible, but nothing fails.
* **An unobservable file.** A message that vanishes between the scan and the
  `lstat` counts as `unobserved`: no information, not a pass.

## Links and names

**A symbolic link at a message name** is never admitted as mail: it is
quarantined with a `REJECT ... symlink` line before any owner check runs
(PROTOCOL.md sections 5a, 29 and 36), so no uid is reported for it. If one
were examined, `lstat` would report the *link's* owner, which is whoever
planted the link; the target's owner says nothing about who put the name in
the mailbox. That is why the check uses `lstat` and not `stat`, and why the
quarantine stands.

**A hard link** keeps its inode's owner, so ownership cannot be forged this
way. What a hard link can do is give a file the bound user created elsewhere
a name in the mailbox, making them "author" mail they never filed there (on
Linux, `fs.protected_hardlinks=1`, the default on most distributions, limits
this to files the linker can already read and write). The check reports
`IDENTITY_SHARED_INODE` for a checked file with `st_nlink > 1`. The signal
exists only while the other name exists, and backup tools make hard links
on purpose, so it is reported and not a failure.

**A mailbox directory that is a symbolic link** is the operator's choice
and is followed, as it always was; `status --json` says `is_symlink: true`
and reports the owner and mode of the directory the files are in.

## Attack evaluation

| Attack | What the check sees | Outcome |
|---|---|---|
| `send --from alice` as another uid | effective uid is not alice's bound uid | refused, exit 2, nothing written |
| `AGENT_MAIL_IDENTITY=alice` with the hook or `inbox` | nothing: reading is not writing | unchanged (reading as alice was never restricted) |
| hand-written file with `from: alice`, created by another uid | observed owner is the creator's uid | `IDENTITY_MISMATCH`; `doctor` FAIL |
| the same, by root or after `chown` | the owner root chose | undetected; out of the model |
| symlink at a message name to alice's file | quarantined before the check | `REJECT`, no uid reported |
| hard link to a file alice created elsewhere | owner is alice, `st_nlink > 1` | admitted, matches, `IDENTITY_SHARED_INODE` reported while both names exist |
| mailbox path replaced by a symlink to the attacker's directory | files owned by the attacker | mismatches show; `is_symlink: true` reported |
| group/world-writable mailbox | creator still recorded as owner | mismatch shows; `shared_writable: true` reported; deletions not detected |
| all agents on one account, bindings to that uid | every binding equals the reader's uid | `distinguishing: false`, one note, no failure, no pass |
| binding to a name that does not exist, or a malformed cell | unresolvable | `IDENTITY_UNRESOLVED`; `doctor` FAIL; that sender's `send` refused |
| renaming a participant's row | the old identity has no binding | its mail is no longer checked; counts show it |
| a `PARTICIPANTS.md` that is not UTF-8 | unreadable | no bindings, no traceback (the pre-existing `capability` path is unchanged) |
| network mount with id squashing | the server's mapped owner | wrong or uninformative; declared out of scope |

## What this cannot guarantee

A binding never proves who a message is from. It proves, on a local
filesystem with a trustworthy root, which uid created the file. It cannot
distinguish agents that share a uid, cannot survive root, cannot see
through id mapping, and cannot tell a file the bound user wrote here from
one they wrote elsewhere once its other name is gone. Keep reading messages
as evidence; the binding is one more piece of it.
