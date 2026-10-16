# Design note: a liveness signal for a dead claim holder

Status: **decision recorded, nothing built.** Roadmap packet B7 asked for a note first and a
build only if it was lightweight. It is not worth building, for the reasons below.

## The gap

A holder that crashed and a holder that stopped renewing look the same: both end as `EXPIRED`
(`docs/coordination-audit-2.md`). Until `expires`, a dead holder blocks its scope (up to 168 h).
A survivor who is refused sees:

```
scope 'file:x.py' is already held by alice (01M5..., until 2026-10-16T08:51:57Z)
```

so it already knows who holds the scope and the latest moment it can be free. It does not know
whether alice is alive.

## What "alive" could mean here

A holder is an agent session, not a process. Each tool call in a host such as Claude Code runs in
its own short-lived process, and a session may sit idle between turns for hours while still
intending to continue. There is no long-lived pid to probe. The scope lock of §30 is held for
milliseconds, during the send only, and says nothing about the lease that follows. So "alive" can
only be inferred from activity in the mailbox.

## Options

| Option | What it is | Verdict |
|---|---|---|
| A. Nothing | Short leases plus renewal; the refusal names the expiry | Current behaviour |
| B. Derive silence | Join a held claim to its sender's last message (the `agents` row already has it) and report "holder silent for N h while holding" | Cheap and pure, but see below |
| C. Heartbeat | Holders send a periodic message to show life | A heartbeat is a renewal of a short lease: already possible, and it costs a message and a `supersedes` link each time. A separate heartbeat type adds a mechanism for nothing |
| D. Process probe | A pid or `flock` held by the session | Rejected: no stable process (above) |
| E. Override | Let a survivor break a lease it judges dead | Rejected: it removes the guarantee that makes a claim worth reading (every claim expires, and nobody else's lease is ever voided). A wrong guess takes a scope from a live agent that is mid-edit |

## Why B is not worth building

A signal is only useful if someone can act on it. With E rejected, the only action available to a
survivor is to wait until `expires` or work on something else, and the refusal already gives the
expiry. "alice looks dead" changes neither choice. B would also be wrong often: an agent can edit
for hours without writing a message and still be the right owner of the scope. A noisy hint that
cannot change anything is a worse report than none.

The measured evidence points the same way. In the v4 pilot (`docs/experiment-v4-results.md`; no
claim, it failed its validity gate) the one blocking mechanism that unblocked a survivor was the
expiring lease, and survivors never overrode a stale note or lock. The lever that works is the
lease length the holder chose.

## What would change this

* A takeover rule someone is willing to defend (for example an explicit, audited `--takeover`
  that voids only a lease whose holder has been silent longer than a stated bound, written as a
  new message so the history shows who broke it). That is a protocol change, not a report, and it
  needs an owner decision; nothing here proposes it.
* Evidence that survivors waste time because they cannot tell a long wait from a dead one. None
  has been seen.

## Cheaper things that are already true

* Take short leases and renew them. `send --supersedes <id> --expires ...` is the heartbeat.
* `status --json` `claims_held` has `seconds_left` for every hold; `doctor` reports an expired,
  unrenewed claim as an orphan.
* `agents[].state` shows who has gone quiet (`--stalled-hours`), for a person to read next to the
  held claims.

## Not tested

No experiment measured whether a silence hint changes what a survivor does. This note argues from
the design (nothing to act on) and the v4 pilot's descriptive counts, not from a trial of option B.
