# A protocol identifier for Postbox (DRAFT, a proposal, nothing here is implemented)

Status: draft for the project owner's decision. Everything below is a proposal; no code, no
`PROTOCOL.md` text and no installed file changes until it is agreed.

## The problem

Today there is one number, the tool version (`agent-postbox 0.5.0`). The `schema` key in `--json`
output is `1` and has stayed `1` while keys were added, so it names a family of shapes, not a revision.
Message files carry no protocol marker. A second implementation, an adapter, or an installed copy
therefore cannot answer "can this reader safely share a mailbox with that writer?", and an unreleased
`main` can say `0.5.0` while differing from the `0.5.0` tag.

## Proposal

**Identifier.** A string `<major>.<minor>`, starting at `1.0`, meaning "the protocol as `PROTOCOL.md`
describes it at the release that adopts this". It is separate from the tool version: bug fixes,
performance, installer work and platform ports do not change it.

**Minor bump (`1.0` to `1.1`): additive only.** An existing reader and an existing writer keep working
in the same mailbox without change. Examples: a new optional key in `--json` (such as
`owed_truncated`, issue 96), a new opt-in flag, a new exit status reachable only through a new opt-in
flag (such as the `inbox` scheduler status of issue 97). Consumers already have to ignore unknown keys
(`docs/protocol-compatibility.md`, section 1).

**Major bump (`1.x` to `2.0`): anything an existing reader or writer could misread or mishandle.**
A new message type (every current reader quarantines unknown types and `list` exits 2); a changed
file-name or header grammar; changed default exit statuses; a changed meaning of a derived status; changed
lock or ordering semantics; a changed definition of "same scope".

**Clarifications are not bumps.** Documentation, and tightening of input that was already outside the
written contract (for example refusing non-UTF-8 stdin that some platforms used to mangle), are recorded
in the CHANGELOG as clarifications of the current protocol.

## Where it would appear (phase 1: no on-disk change)

| Place | What | Who reads it |
|---|---|---|
| `PROTOCOL.md`, one line near the top | `Protocol 1.0` | humans, the conformance runner's header |
| `agent_mail.py --version --verbose` and `doctor` | `protocol: 1.0` | adapters, support |
| `INSTALLED.json` | a `protocol` field next to the tool version | the installer's upgrade check |
| `conformance/run_conformance.py` | prints the protocol it checked | adapter authors |
| CHANGELOG | a heading per release that says "protocol 1.0 to 1.1" when it moves | upgraders |

Message files do **not** carry the identifier in phase 1. An unknown header key is ignored today, so a
`protocol:` key would be safe for readers, but it changes the on-disk format and needs its own decision
(phase 2, below).

## How an adapter or an installed copy reports compatibility

* An adapter states the protocol it implements (`1.0`) and the tool version it was tested with, and
  reports the runner's three summary lines (see `docs/adapter-conformance-guide.md`). "Passes the
  protocol-level group of protocol 1.0" is the claim; it is not a certification.
* An installed copy reports `protocol` from its manifest. The upgrader compares the installed value with
  the new copy's: same major, proceed; different major, print the CHANGELOG headings between them and
  stop unless the user confirms.
* A reader never infers the protocol of a mailbox from its files in phase 1; there is nothing to read.

## Mixed versions in one mailbox

* Within a major: supported. A `1.1` writer's files are readable by a `1.0` reader because the additions
  are optional keys and flags; a `1.0` reader simply does not use them. A `1.1` reader of `1.0` files
  must treat the new keys as absent.
* Across majors: not promised. A `2.0` would have to say whether it reads `1.x` files (recommended: yes,
  read-only, for one major). An upgrade never rewrites a mailbox.
* Phase 2 (a separate decision): senders write an optional `protocol:` header; old readers ignore it;
  a new reader that meets a higher major in a header reports it instead of guessing.

## Diagnosing a failure that may be a version mismatch

1. Ask each side for its identifier: `agent_mail.py --version --verbose` (phase 1), or `doctor`.
2. Same major: the difference is not a protocol break. Check the tool versions' CHANGELOG, then the
   host (hook installed, interpreter found).
3. Different major, or one side cannot report it: run the conformance runner's `--protocol-only` group
   against each; the first failing check names the behaviour that differs.
4. Symptoms that point at a major difference: `list` exits 2 with `REJECT ... unknown type`; a refusal
   that the other side does not give; a scope held on one side and free on the other.

## Decisions requested

1. Adopt `1.0` as the starting point and the minor/major rule above?
2. Phase 1 only (documentation, `--version --verbose`, manifest field), with no on-disk change?
3. Which changes count as `1.1`: suggested, `owed_truncated` and the `inbox` scheduler status, once both
   are merged and their final names are fixed.
4. Is a phase 2 `protocol:` header wanted at all, and if so before or after the first new message type?

## Open questions found in independent review (unresolved; this is a DRAFT)

1. Platform ports versus the identifier. The rule that a platform port does not change the identifier sits
   next to `docs/protocol-compatibility.md` section 5, which marks changing the Windows lock primitive or
   offset as versioned. A third-party author cannot tell which applies to a lock-layer change.
2. Tightening input. Refusing non-UTF-8 stdin is described as a clarification, yet an existing caller that
   relied on the old behaviour now gets exit 2, which the draft's own major rule would count as a change an
   existing reader could misread. Section 5 also calls read tightening "versioned in effect".
3. Software minor versus protocol minor. One part of the compatibility inventory suggests the protocol follows the
   tool's minor version; this draft says they are separate.
4. Not classified at all: a new derived status value, a renamed or removed CLI flag, and whether CLI flags are
   part of the protocol at all or only `PROTOCOL.md` and the message files are.
