# Documentation

* [../README.md](../README.md): what this is, quickstart, CLI usage, then install and reference.
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
* [reliability-evidence.md](reliability-evidence.md): twelve realistic failure scenarios (concurrency, restarts, busy or vanished recipients, claim holders, bad recipients, interrupted sends, duplicates, contention, acknowledgement, visibility to the human), each with its mechanism, the test or run that covers it (deterministic, simulated or observed), the gaps and the signal a person gets.
* [packaging.md](packaging.md): building and checking the wheel and sdist.
* [benchmarks-summary.md](benchmarks-summary.md): the pickup-versus-polling results table from one
  `scripts/scenario.py` run, with its caveats (moved from the README).
* [install-reference.md](install-reference.md): the pre-0.3.0 distribution-name note and
  verifying a release download (moved from the README).
* [benchmarking.md](benchmarking.md): `scripts/scenario.py`, the reproducible
  benchmark and reliability harness (hook pickup versus a polling baseline
  under injected faults): what is simulated, how to run it, how to read it.
* [coordination-experiment.md](coordination-experiment.md): the design of a
  repeatable experiment on whether Postbox improves team outcomes (task
  completion, conflicting edits, missed facts, recovery, false success) against
  two control arms, with its statistics, cost and limits, and the results of
  `scripts/team_sim.py`, the offline simulation of the mechanism (simulated
  agents; says nothing about real models).
* [../examples/two-agents/](../examples/two-agents/README.md): a runnable
  demo, two scripted sessions exchanging mail through the real tool
  (`python examples/two-agents/run.py`; tested by `test_examples.py`).
* [experiment-v2.md](experiment-v2.md): pre-registration of a second real-agent experiment: arms, power, decision rule, budget.
* [experiment-v2-results.md](experiment-v2-results.md): its main-stage results (against Postbox on this task, with the cause).
* [install-audit.md](install-audit.md): the installer lifecycle audit: every case run, with its result and the defects found.
* [performance.md](performance.md): measured latency and memory against mailbox size (linear; the prompt hook passes 200 ms near 4,000 messages), concurrency, and what was not measured.
* [issues-to-open.md](issues-to-open.md): five starter issues drafted for the
  tracker (known gaps, each with what was observed, acceptance criteria,
  files to touch and how to test).
* [handoff-example.md](handoff-example.md): a filled-in handoff packet
  (validated by `test_handoff.py`).
* [threat-model.md](threat-model.md): assets, adversaries, trust assumptions
* [coordination-audit.md](coordination-audit.md): a defect hunt over claims, ASK/ANSWER, delivery and the five status views: what was probed, two defects fixed, what was not found.
  (each cited to code), the phase 6 findings and the accepted risks.
* [publication-safety.md](publication-safety.md): the pre-publication scanner and how to configure it.
* [release-process.md](release-process.md): how a release is cut (version,
  changelog, tag, GitHub release, privacy preflight), how the published
  release is verified, immutable releases, rollback.
* [onboarding-first-exchange.md](onboarding-first-exchange.md): the tested path from a clone to a
  first exchange, with timings.
* [demo-narration.md](demo-narration.md): narration for the recorded two-agent demo.
* [troubleshooting.md](troubleshooting.md): symptoms and fixes, and removing the install.
* [adoption/](adoption/): why Postbox exists, launch drafts (not posted), where to share it, and
  how feedback is collected and prioritised.
* [pypi-publishing.md](pypi-publishing.md): the project is **not on PyPI**;
  this is the prepared plan for publishing there (checks done, the upload job,
  the owner's checklist). Nothing in it has been carried out.
