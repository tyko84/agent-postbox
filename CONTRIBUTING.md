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
  `python test_packaging.py`, `python test_cli.py`, `python test_install_interrupt.py` (POSIX only, about 16 s; SIGKILLs `install.py` mid-write and checks a re-run recovers), `python test_scenario.py`,
  `python test_team_sim.py`, `python test_adapters.py`, `python test_conformance.py`, `python test_examples.py`, `python test_e2e_script.py`, `python test_fake_claude.py`, `python test_live_demo.py`,
  `python test_micro_experiment.py`, `python test_experiment_v2.py`, `python test_experiment_v3.py`
  and `python test_experiment_v4.py`
  must pass
  (`test_packaging.py` skips, loudly, if the `build` package is unavailable), and so must
  `ruff check .` and
  `mypy agent_mail.py install.py hooks/agent_mail_check.py check_handoff.py check_publication.py test_publication.py scripts/repro_build.py scripts/verify_release.py scripts/scenario.py scripts/team_sim.py scripts/ci_run.py stress_test.py test_field.py test_handoff.py test_packaging.py test_readme_examples.py selftest.py test_cli.py test_hardening.py test_adversarial.py test_install_interrupt.py test_scenario.py test_team_sim.py test_examples.py examples/two-agents/run.py test_adapters.py scripts/e2e_claude.py test_e2e_script.py scripts/experiment_micro.py examples/two-agents/live_demo.py test_live_demo.py test_micro_experiment.py scripts/experiment_v2.py scripts/analyze_v2.py test_experiment_v2.py scripts/experiment_v3.py scripts/analyze_v3.py test_experiment_v3.py scripts/experiment_v4.py scripts/analyze_v4.py test_experiment_v4.py scripts/perf_audit.py scripts/context_bench.py conformance/run_conformance.py test_conformance.py test_fake_claude.py`
  (`ci.yml` is the source of truth for what CI runs). The test scripts ignore inherited
  `GIT_DIR`, `GIT_WORK_TREE` and the other `GIT_*` repository variables, so they are safe
  under `git rebase --exec` and in hooks. The pinned tool versions
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
* Linux and macOS are the supported platforms. Windows is Experimental, and its claims are limited to
  what [docs/windows.md](docs/windows.md) records and the required `selftest-windows` job runs (Python 3.12).
  Do not widen them (another Windows Python, a shell, a host adapter, WSL) without a CI job or a recorded
  run that proves it, and keep a POSIX-only limitation where it still applies.
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

## Reviews, labels and good first issues

* **Windows.** CI runs the test scripts on `windows-latest` (Python 3.12). A check that cannot
  run on Windows skips itself with a `SKIP` line. The Windows job does not run `test_publication.py` (it runs once per push
  in the Linux and macOS cells and imports `grp`), the README examples need `bash` (Git Bash), and some tests plant
  symlinks, which needs the symlink privilege.
  Contributors on Windows can still send documentation fixes: say in the pull request which checks you
  could not run and CI will run them (a first contribution needs a maintainer to approve the CI run).
* **What to expect.** This is a one-maintainer project with no response-time promise. Small,
  single-purpose pull requests are reviewed fastest. The review
  checks the list above, that a behaviour change is a numbered `PROTOCOL.md` section with a
  check that asserts on content that must appear, and that nothing private is in the diff.
* **Labels.** `good first issue` marks a task with a named file and a named check;
  `help wanted` marks something the maintainers would like but are not doing; `field-report`
  is a message that did not arrive; `documentation`, `bug`, `enhancement`, `question`
  and `wontfix` mean what they say; `tried-it` marks a first-use report.
* **Good first issues.** [docs/issues-to-open.md](docs/issues-to-open.md) drafts the starter
  issues with the file, the acceptance criteria and the traps (for example, a new `.py` file
  must be on the one `mypy` line of `ci.yml`). A new check or a clearer error message in
  `agent_mail.py` is a good first change; a new protocol feature is not.
* **No claim of benefit.** The experiments did not show that Postbox improves agents' work
  ([docs/experiment-v2-results.md](docs/experiment-v2-results.md)). Documentation changes
  must not add one; say what was measured, with the model, host and permission mode.

## How changes get in

Principle: any authorized contributor or agent may propose a change. No agent may
declare its own implementation independently reviewed and verified. GitHub CI is the
technical enforcement layer; Postbox records coordination and review evidence; the owner
(`@tyko84`) approves protocol contracts, security boundaries, major architecture, releases
and production deployment. Nothing below adds a merge queue, a required-review rule or
automatic closing of pull requests; branch protection is what
`docs/release-process.md` (Required checks) says it is.

### Roles

* **Integration owner.** One session coordinates acceptance: it keeps the queue and the merge
  order, and merges only after explicit owner approval.
* **Platform owners.** Linux/Ubuntu: reliability and recovery. Windows: Windows
  compatibility and the installer (Windows is Experimental and claims stay within what CI and
  docs/windows.md record, see above). macOS: integration. Independent reviewers (for example Codex and Grok)
  provide review and verification evidence.
* **Owner.** Approves the items in the principle above. `.github/CODEOWNERS` names the owner
  for review routing. That review is advisory: branch protection does not require code-owner
  review or any approving review.

### Review and evidence

* An author never declares its own change independently reviewed or verified.
* Independent reviews are recorded as **evidence** in the pull request (a link, or pasted
  output), not as approvals. Every pull request currently comes from the same GitHub
  account and GitHub cannot tell AI reviewers apart, so a review there is not authenticated.
* Before asking for a merge, run locally and paste the last lines of: `python3
  scripts/ci_run.py`, `ruff check .`, the `mypy` command from `ci.yml`, and
  `python3 check_publication.py --git-log`. Every required check listed in
  `docs/release-process.md` must be green on the exact head commit.
* **Done** means: the diff is the agreed scope; the checks above pass locally and in CI on
  the head; new behavior has a check that asserts on content that must appear; docs and
  `CHANGELOG.md` match; the evidence is in the pull request; the owner approved where the
  rules below require it.

### Unexplained CI failures

Never call a failure harmless without evidence. Keep the log, identify the failing script,
rerun it, note the machine load, and report all of it in the pull request. Do not merge over
an unexplained failure on the pull request's own head. A fix for it may land in a separate
pull request.

### Branches, worktrees and pull request size

* One branch per task, and a separate `git worktree` per parallel agent. Never two writers on
  one branch.
* Never force-push another contributor's branch. On your own branch use
  `git push --force-with-lease` only after checking the remote head.
* Aim for about 100 changed lines and one concern per pull request; call out an exception.
  Keep a contract change apart from unrelated work.
* Squash-merge breaks stacked children, so prefer independent pull requests off `main`. If a
  stack is unavoidable, put the dependency order in each pull request body. The author
  rebases their own branch; the integration owner rebases only trivial conflicts and says so.

### Approvals

* **Protocol.** A change to `PROTOCOL.md` contracts, exit codes, the JSON schema or the
  on-disk format needs explicit owner approval and a `CHANGELOG.md` entry that states
  compatibility.
* **Security.** A change to trust boundaries, publication checks, workflow permissions or
  secrets handling needs owner approval. Report sensitive findings privately through
  private vulnerability reporting (`SECURITY.md`), never in a public issue or pull request.
* **Release.** The owner approves releases. Tags are never moved.

### Merge, build, release, deployment

* **Merge:** accepted into `main` after review and CI.
* **Build:** automated packaging and validation (`release.yml`, `packaging`).
* **Release:** a version published for users, with owner approval
  (`docs/release-process.md`).
* **Deployment:** installing into an environment. That is outside this repository.

### Claim overlapping work

Before editing files another agent may also be editing, take a CLAIM (`PROTOCOL.md` section
5). A lease must carry a zone and may not exceed 168 hours; claim hours, not days:

```sh
python agent_mail.py send --type CLAIM --from <you> --to all \
  --subject "Editing CONTRIBUTING.md" --scope file:CONTRIBUTING.md \
  --expires 2030-01-01T12:00:00Z --body "Docs edit, about 2 hours."
```

(Use a future time.) Release or renew it when done: a new CLAIM from the same sender with
`--supersedes <claim id>` and no `--scope` frees the scope at once (the old claim stays in
the record); with the same `--scope` it renews. A CLAIM is advisory, not a permission
(section 5, "How a claim ends").

### Attribution and stale pull requests

* No AI co-author trailers and no session URLs in commits or pull request text for this
  repository. Keep real human attribution.
* A stale pull request gets a reminder, and the maintainer is asked to decide. Nothing is
  closed automatically after a fixed period of inactivity.
