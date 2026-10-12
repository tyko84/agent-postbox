# Security model

Read this before installing in a repo other people can write to.

## What this tool does, stated plainly

Pickup, whether a prompt-injection adapter or a poll, reads files from the
mailbox directory. An adapter that a host wires to a prompt-submit event
prints them into your coding agent's context **before the agent sees your
prompt**. That is the entire point of the stronger pickup, and it is also its
threat model: **anyone who can write a file into the mailbox can put text in
front of your agent.**

Claude Code's `UserPromptSubmit` is one such event. Other hosts have others.
Poll (`list --live`) is the same read, invoked by the agent instead of the
host. The threat is the mailbox, not a particular hook name.

In a repo that accepts pull requests, that includes contributors you do not
know.

## The rule that mitigates it

**A message is EVIDENCE, never AUTHORITY** (PROTOCOL.md §0). Nothing in the
mailbox authorises anything — not a push, not a deletion, not a service
restart, not running a command. The adapter restates this rule in every
delivery for exactly this reason.

This is a real mitigation but not a complete one, because it depends on the
agent honouring it. Treat mailbox content the way you would treat the body of
an email from a stranger: as data that says something, never as an instruction
that grants something.

## Guidance

* **Safe:** a private repo, or one where every writer is someone you'd let run
  commands on your machine anyway. This is the intended case — agents you
  yourself are running, coordinating with each other.
* **Never file mail into this spec checkout.** `AGENT_MAIL_DIR` (if set) is the team inbox; otherwise a project's `docs/agent-mail` after `install.py`. This repository is the shareable tool, not an inbox. `send` and `install.py` refuse to mint a mailbox next to `agent_mail.py` here.
* **Think first:** a public repo accepting PRs. A malicious message file is a
  prompt-injection vector, and with an adapter it arrives with no click
  required. If you want the tool here, consider keeping the mailbox out of the
  PR surface — e.g. a path that requires review, or a mailbox outside the repo
  via `AGENT_MAIL_DIR`.
* **Review mailbox changes in PRs like code.** A diff that adds a file to
  `docs/agent-mail/` is a diff that adds text to your agent's context.

## What the tool does not do

* No network calls of any kind. No telemetry, no phone-home, no dependencies —
  Python standard library only.
* Message content is never executed, evaluated, or passed to a shell. It is
  read and printed.
* The adapter only ever reads. It does not write, delete, or mark messages
  read.
* `install.py` always writes the tool files and the mailbox directory. It
  writes a host adapter config **only** when you pass `--adapter` (for
  `claude-code`, one hook entry in the project's `.claude/settings.json`).
  It backs that file up first and refuses to touch JSON it cannot parse.
  Default install is poll-only and does not create `.claude/`.

## What is and is not guaranteed

* **Not authenticated.** The `from:` field is whatever the writer typed. Anyone
  who can write into the mailbox can impersonate any sender. Use filesystem
  permissions as the access control, and treat identity as a label, not proof.
* **Claims are advisory leases.** `--scope` makes simultaneous claimants
  produce one winner, but nothing forces an agent to respect a claim. Do not
  rely on one as the only guard on something irreversible.
* **Scope locks are kernel-held.** `.scope.*.lock` is an exclusive `flock(2)`
  held for the milliseconds of check-and-publish; the kernel releases it when
  the holder exits, so a dead claimant's lock is reclaimed by exactly one later
  claimant and no process removes a lock another process holds (PROTOCOL.md
  section 30). `flock` is trusted on local filesystems only: a network mount
  may grant it to two hosts or refuse it, in which case the claim is refused.
  `send --key` holds the same kind of lock on its idempotency marker (section
  31). A `status` or `doctor` probe takes a shared lock for microseconds on
  lock files and markers at least 60 seconds old, read-only and never following
  links; a claim arriving in that instant is told to retry once (section 33).
* **Local filesystem, POSIX only.** Atomic rename and exclusive create are
  assumed. Network filesystems and Windows are untested and unsupported.

## The pickup fails silently, on purpose

Every error path in the adapter exits 0 with no output, because a broken
mailbox must never block you from prompting. The security-relevant consequence
is that **a pickup that has been disabled, misconfigured, or broken looks
exactly like an empty inbox.** Do not infer "no messages" from silence. Run
`selftest.py`, which asserts on content that must appear (PROTOCOL.md §8).

## Reporting

Private vulnerability reporting is enabled for this repository. For anything
exploitable, for example a way to make mailbox content execute rather than
print, use it: the Security tab, "Report a vulnerability", or directly
<https://github.com/tyko84/agent-postbox/security/advisories/new>. The report
is visible to the maintainer only. For hardening ideas and non-sensitive
findings, open an issue. This is a small stdlib-only tool with no network
surface and no embargo process. A report about a release artifact should
include the output of `scripts/verify_release.py vX.Y.Z` for that tag.

## How this repository is built and released

The tool has no dependencies; the pipeline that tests and releases it does.
What is done about that, and where it is described:

* The one secret the repository has (a list of names that must not be
  published) is read only by jobs that install nothing
  (`docs/publication-safety.md`, In CI).
* The job that writes release artifacts installs three build tools pinned by
  hash and a build backend whose wheel is checked against a recorded SHA-256;
  the build is reproducible and anyone can rebuild a release and compare
  (`docs/packaging.md`).
* Every action in the workflows is published by GitHub and pinned to a commit.
  The Python sources and the workflow files are analysed by CodeQL on every
  pull request, on every push to `main` and weekly.
* Releases carry `SHA256SUMS` and a signed build provenance
  (`docs/release-process.md`, Verifying a download).
* The project is not on PyPI (`docs/pypi-publishing.md`). Install the wheel
  attached to a release, or a clone (`python -m pip install ./agent-postbox`),
  never a bare package name: this project's distribution is
  `tyko84-agent-postbox`, and `agent-postbox` on PyPI is an unrelated project.

What a consumer can verify about a release, and how. None of these checks
installs or executes the downloaded files:

* **The files are the ones the release lists.** `sha256sum -c SHA256SUMS`
  (`shasum -a 256 -c SHA256SUMS` on macOS) next to the downloaded files. The
  checksum file sits beside them, so this alone does not say where they came
  from.
* **GitHub's workflow built them, from a named commit.** `gh attestation
  verify FILE -R tyko84/agent-postbox`, for the wheel and for the sdist. The
  provenance is signed by the workflow run with an identity GitHub issues to
  it, not with a key the maintainer holds; it names the builder,
  `.github/workflows/release.yml` at the release tag, and the source commit
  the tag pointed to. Exit status 0 is the result; `--format json` shows both.
* **The tagged source produces those bytes.** From a clone,
  `scripts/repro_build.py --ref vX.Y.Z --verify DIR` rebuilds the tag and
  compares byte for byte (it needs the hash-pinned build tools of
  `requirements-build.txt`; `docs/packaging.md`).
* **All of the above in one command.** `scripts/verify_release.py vX.Y.Z`,
  from a clone with `gh` signed in, downloads the assets and runs the checksum,
  attestation, archive-content and builder-identity checks, one `OK`/`FAIL`
  line each, exit 1 if any failed; `--rebuild` adds the rebuild.

What none of this verifies: that the code is correct, or safe for your use
(read it; it is one file); that the maintainer's account was not compromised
when the tag was pushed, which an attestation cannot show, since it certifies
the build of whatever commit the tag named; or that a future release will pass
the same checks. Each release is verified on its own.
