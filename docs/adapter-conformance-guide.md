# Adapter conformance guide

For someone who has written, or is writing, an adapter or a second implementation of the Postbox
command line (a wrapper in another language, a hook, a service that speaks the same files) and wants
to know whether it behaves the way `PROTOCOL.md` says. The runner is
`conformance/run_conformance.py`. It uses only the Python standard library, needs no network and no
model, and never touches a mailbox of yours: every run uses a throwaway directory that is removed
afterwards.

Passing is evidence of the listed behaviours and nothing more. It is not a certification, and it says
nothing about whether a model sees or obeys what your adapter shows it (for that, see the conformance
points in `docs/adapter-contract.md`).

## Run it

Against a copy of `agent_mail.py` (run with the Python that starts the runner):

    python conformance/run_conformance.py --tool path/to/agent_mail.py

Against anything else that has the same command line, give the program and its arguments after
`--cmd` (it must be last on the line):

    python conformance/run_conformance.py --cmd node adapter.js
    python conformance/run_conformance.py --cmd ./my-adapter --protocol-only

Your program is started once per command with its arguments appended, for example
`./my-adapter send --type ASK --from a --to b --subject s --body b`. The runner sets
`AGENT_MAIL_DIR` to the throwaway mailbox (it removes every other `AGENT_MAIL*` variable from the
environment), so your program must read the mailbox from that variable. It must accept the
same flags and subcommands as `agent_mail.py` for the commands it implements: `send` (`--type`,
`--from`, `--to`, `--subject`, `--body`, `--body-file -`, `--key`, `--scope`, `--expires`,
`--reply-to`), `list`, `inbox`, `status` and `show`, with `--json` and `--now`. Output is read as
UTF-8.

Useful options:

| Option | Meaning |
|---|---|
| `--protocol-only` | Run only the protocol-level group (below). Use this first, and use it alone if your program words its messages differently. |
| `--json-out FILE` | Also write every check, its group, its status and the failure detail as JSON. |
| `--tool PATH` | The `agent_mail.py` to test (default: the one next to the runner). |

Exit status: 0 when nothing that ran failed (skips do not fail), 1 when a check failed, 2 when the
program could not be started or the run stopped early before any check failed (a tool that starts but
exits non-zero for everything fails checks, exit 1). Python 3.10 or newer is required (macOS ships 3.9 as
`python3`: use an explicit interpreter). On Windows the tool under test must be a Windows-capable
`agent_mail.py` (the unmerged Windows port); main alone cannot start there. A run takes about 15 to 30 seconds, most of it
process start-up and one three-second wait for a short lease to lapse.

## The three groups

**Protocol-level.** What `PROTOCOL.md` requires of any implementation that offers these commands:

* the exit statuses (0 done, 1 not found, 2 refused or unhealthy, 3 contended) and that a refusal
  writes nothing;
* the message file: named `<ULID>-<slug>.md`, UTF-8, LF, begins with `---`, carries
  `id`, `type`, `from`, `to`, `date`, `subject`; non-ASCII text survives;
* `--json` schema 1 for `list`, `inbox`, `status` and `show`;
* ASK and ANSWER: an ASK owes a reply until a later message cites its id; the closing is derived, no
  file is edited; an ANSWER citing an id nobody has closes nothing;
* CLAIM: expiry mandatory and at most 168 hours; a second sender on a held scope is refused (exit 3);
  four simultaneous claims of one scope produce exactly one winner; a lapsed lease no longer blocks;
  the status `held` or `expired` is a function of the clock (`--now`);
* idempotent `--key`: the same content returns the original id (also when five processes send at
  once); different content is refused;
* malformed files (unknown type, duplicate key, unparseable date, missing recipient, id that is not a
  ULID, two files with one id, bytes that are not UTF-8) make `list` exit 2, are never delivered as
  mail, are never deleted, and do not hide the valid mail beside them.

These checks take the id of a sent message from the name of the new file (the `id:` line is only a
fallback), and never read the `--version` text or wording on stderr, so a faithful implementation with its
own prose passes. They do depend on the command-line surface above (flags, `--json` schema 1, exit
statuses) and on `<ULID>-<slug>.md` file names in the mailbox directory; an implementation that stores
mail under other names needs `PROTOCOL.md` section 2 ("transport only") to be read more loosely than this
runner does, and will fail the file-name checks. "Real mail is still delivered" next to a quarantined file is
judged from `list --json`, not from the plain text.

**Implementation-specific.** What this CLI happens to print: the `--version` string, the
`id: <ULID>` line `send` prints, the words `NO MAILBOX` and `REJECT` on stderr. If your adapter parses
output of the reference CLI, these matter to you; if it reimplements the protocol, a failure here is not
a protocol failure. They are listed so that a difference is visible, not so that it is fixed.

**Optional.** Features that are not in every release: `owed_truncated` in the `--json` of `list` and
`inbox` (issue 96), and an `inbox` option that gives an exit status for schedulers: 10 when something is
owed, 0 when nothing is, 2 when that cannot be known (issue 97). The runner finds out whether the tool has
the feature. If it does not, the check is reported as SKIP and does not affect the exit status; if it does,
the whole contract is checked, including that output and the default exit statuses are unchanged. A tool
with a feature that behaves differently from the contract fails, which is the point.

## What the runner does not cover

The host side (that a model sees or obeys anything); concurrency beyond the two races above (see
`selftest.py` and `stress_test.py`); `verify`, identity binding, aliases, capabilities, `doctor`, the
hook script; timing beyond `--now`. An adapter that reads the mailbox files itself, instead of calling
the CLI, should add its own tests for the file shape and the quarantine rules.

## Reporting a result

When you report that an adapter passes, or that a check seems wrong, give:

1. the group you ran (`--protocol-only` or full) and the final summary lines (three lines of counts);
2. the tool version (`--version` output) or the commit of the implementation;
3. the operating system and the Python version that ran the runner;
4. for a failure, the failing check name and its bracketed detail, or the file from `--json-out`
   (details replace the temporary directory with `<tmp>`; read the file before sharing it anyway).

State plainly which checks were skipped. "Passes the protocol-level group" is accurate; "conformant" or
"certified" is not. If you believe a check asserts something `PROTOCOL.md` does not say, that is a bug in
the runner: open an issue naming the check and the section.
