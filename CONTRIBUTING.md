# Contributing

This is a small, stdlib-only tool plus a protocol spec. The most useful
contributions are **field reports**: where did a message not arrive, arrive
and get ignored, or get believed when it shouldn't have been? Open an issue
with what happened, what you expected, and the mechanism that was supposed to
deliver it (see `PARTICIPANTS.md`).

## Field report walkthrough

A field report is worth more than a guess at the cause. The tool is a single
file with no dependencies, so the report rarely needs more than what the CLI
already prints. Work from the agent's own environment: the same
`AGENT_MAIL_DIR` (or working directory) and the same `AGENT_MAIL_IDENTITY`
the agent had when the problem happened.

1. **Reproduce it with the CLI, not the hook.** Run the exact command again,
   as a subprocess would (`python -I agent_mail.py ...`), and note its exit
   status (`echo $?`). A pickup adapter that printed nothing is not a
   reproduction: silence is also what an empty inbox looks like (PROTOCOL.md
   §8). Re-run `python hooks/agent_mail_check.py` with the identity set, and
   if it is silent, run `list --to <identity> --live` to see whether there was
   anything to deliver.
2. **Collect, in this order:**
   * `python agent_mail.py --version`, the Python version (`python -V`) and
     the OS (`uname -sr`).
   * `python agent_mail.py doctor` (stdout, stderr and exit status).
   * `python agent_mail.py status --json`. It carries counts, ids, senders,
     scopes and expiries, no subjects and no bodies (§34).
   * The exact command, how it was invoked (shell, hook, subprocess), its
     stdout, its stderr and its exit status.
   * The relevant `list --json` or `inbox --json` rows if the problem is a
     message that did or did not arrive: an id, a type and a derived status
     say more than a description.
3. **Leave out, always:** a message body (it is untrusted text and may be
   someone else's; `show` output stays on your machine), and any path that
   reveals a private name, host or project. The only path in `status` and
   `list --json` is the `mailbox` key: replace it with `<mailbox>` before
   pasting. Subjects are the sender's text too; replace one if it is private.
   Rename identities consistently (`agent-a`, `agent-b`) unless they are
   already public.
4. **File it** as a GitHub issue on this repository. Title it with the
   symptom (`list --live omits an ASK that status counts as open`), state
   what you expected, what happened, and which mechanism in
   `PARTICIPANTS.md` was supposed to deliver it. Attach the collected text
   inline in fenced blocks; no archives, no mailbox copies. If you suspect a
   security problem, follow `SECURITY.md` instead of opening a public issue.

A report that follows this list can usually be turned into a failing check
in `selftest.py` without a follow-up question, which is the fastest route to
a fix.

Code changes:

* `python selftest.py`, `python stress_test.py`, `python test_hardening.py`,
  `python test_adversarial.py`, `python test_field.py`, `python test_handoff.py`,
  `python test_readme_examples.py`, `python test_publication.py`,
  `python test_packaging.py`, `python test_cli.py`, `python test_scenario.py`,
  `python test_adapters.py` and `python test_examples.py` must pass
  (`test_packaging.py` skips, loudly, if the `build` package is unavailable), and so must
  `ruff check .` and
  `mypy agent_mail.py install.py hooks/agent_mail_check.py check_handoff.py check_publication.py test_publication.py`
  (`ci.yml` is the source of truth for what CI runs). The pinned tool versions
  CI uses are in `requirements-dev.txt`:
  `python -m pip install -r requirements-dev.txt`. The tools that build a
  release are pinned by hash as well, in `requirements-build.txt` and
  `build-constraints.txt` (`docs/packaging.md`, The build tools).
* `python selftest.py` asserts on content that *must* appear, never on
  absence. A test that can only observe silence proves nothing
  (PROTOCOL.md §8), so give new checks a positive control.
* Standard library only. No network calls, no dependencies.
* A protocol change is a `PROTOCOL.md` change first, with a numbered section
  and a selftest that pins it. Do not edit old sections' meaning silently.
* Never file mail into this checkout. It is the spec, not a mailbox
  (see `SECURITY.md`).
* POSIX only. Do not add Windows claims or code paths without a CI job that
  proves them.
* Docs are tested: every `bash runnable` block in `README.md` is executed by
  `test_readme_examples.py`, and every "section N" / section-sign reference
  must resolve to a real `PROTOCOL.md` heading. Change an example or renumber a
  section and the test tells you what to fix.
* Packaging: the version lives only in `agent_mail.__version__`. See
  `docs/packaging.md`.
* Keep the repository free of anything private: no real names, hosts,
  credentials or internal project references in code, docs, tests or examples.
  Commit metadata is public too: author and committer addresses and commit
  trailers are part of the history, so commit with a GitHub `noreply` address
  if you do not want an address published, and do not add trailers that carry
  names or addresses. The pre-publication scanner, `scripts/preflight.sh` and
  the `POSTBOX_FORBIDDEN` list (an environment variable locally, a repository
  secret in CI; the list itself is never committed) are described in
  `docs/publication-safety.md`; the release steps are in
  `docs/release-process.md`.
