# Documentation

* [../README.md](../README.md): what this is, install, quickstart.
* [../PROTOCOL.md](../PROTOCOL.md): the whole spec.
* [../SECURITY.md](../SECURITY.md): threat model.
* [identity.md](identity.md): optional Unix-user identity binding (PROTOCOL.md
  section 40): what a message file's owner proves, what it does not, and the
  attacks it was checked against.
* [hosts.md](hosts.md): which hosts can run the pickup adapter (Claude Code,
  Codex), which cannot (Cursor: polling only), what each mechanism guarantees,
  and exactly what was verified and what was not.
* [e2e-real-sessions.md](e2e-real-sessions.md): the first run inside a real
  host (Claude Code, headless `claude -p`, 2026-10-12) with real model
  sessions: delivery, no cross-talk, next-prompt and recovery checks, two
  behavioural observations, and what a missing hook script does (it blocks the
  prompt). `scripts/e2e_claude.py` re-runs it; it spends usage and is not in CI.
* [packaging.md](packaging.md): building and checking the wheel and sdist.
* [benchmarking.md](benchmarking.md): `scripts/scenario.py`, the reproducible
  benchmark and reliability harness (hook pickup versus a polling baseline
  under injected faults): what is simulated, how to run it, how to read it.
* [../examples/two-agents/](../examples/two-agents/README.md): a runnable
  demo, two scripted sessions exchanging mail through the real tool
  (`python examples/two-agents/run.py`; tested by `test_examples.py`).
* [issues-to-open.md](issues-to-open.md): five starter issues drafted for the
  tracker (known gaps, each with what was observed, acceptance criteria,
  files to touch and how to test).
* [handoff-example.md](handoff-example.md): a filled-in handoff packet
  (validated by `test_handoff.py`).
* [publication-safety.md](publication-safety.md): the pre-publication scanner and how to configure it.
* [release-process.md](release-process.md): how a release is cut (version,
  changelog, tag, GitHub release, privacy preflight), how the published
  release is verified, immutable releases (not enabled), rollback.
* [pypi-publishing.md](pypi-publishing.md): the project is **not on PyPI**;
  this is the prepared plan for publishing there (checks done, the upload job,
  the owner's checklist). Nothing in it has been carried out.
