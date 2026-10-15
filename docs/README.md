# Documentation

Start with [../README.md](../README.md) (what this is, quickstart, CLI usage), then
[onboarding-first-exchange.md](onboarding-first-exchange.md). Nothing here claims a measured
benefit: see [Evidence](#evidence) for what was and was not shown.

## Using it

* [onboarding-first-exchange.md](onboarding-first-exchange.md): the tested path from a clone to a first exchange, with timings.
* [troubleshooting.md](troubleshooting.md): symptoms and fixes (including a denied `send`), and removing the install.
* [hosts.md](hosts.md): which hosts can run the pickup adapter (Claude Code; Codex, never run in the real host; Cursor, polling only), what each mechanism guarantees, and exactly what was verified.
* [codex-investigation.md](codex-investigation.md): what the Codex documentation says about hooks, trust, sandbox and sessions versus what the Codex adapter assumes; documentation read only, Codex never run; an 8-step validation plan for issue #21.
* [first-use-findings.md](first-use-findings.md): a timed README Quickstart run (machine time only) and the friction it found.
* [install-reference.md](install-reference.md): the pre-0.3.0 distribution-name note and verifying a release download.
* [identity.md](identity.md): optional Unix-user identity binding (PROTOCOL.md section 40): what a file's owner proves, what it does not.
* [handoff-example.md](handoff-example.md): a filled-in handoff packet (validated by `test_handoff.py`).
* [../examples/two-agents/](../examples/two-agents/README.md): a runnable demo of two scripted sessions exchanging mail through the real tool.
* [demo-narration.md](demo-narration.md): narration for the recorded two-agent demo.
* [../PROTOCOL.md](../PROTOCOL.md): the whole spec. [../SECURITY.md](../SECURITY.md): the threat model.

## Evidence

* [micro-experiment.md](micro-experiment.md): the pilot (20 paired instances, one small model): null.
* [experiment-v2.md](experiment-v2.md) and [experiment-v2-results.md](experiment-v2-results.md): the pre-registered second experiment and its result, against Postbox on that task, with the corrected cause (a host refusal of a `#` line in a quoted `--body`).
* [experiment-v3-proposal.md](experiment-v3-proposal.md): a proposal, not approved and not run.
* [experiment-v3.md](experiment-v3.md) and [experiment-v3-readiness.md](experiment-v3-readiness.md): the v3 pre-registration (multi-prompt consumer) and its low-cost readiness check, whose recommendation was not to run the main stage as designed because the notes control looked to be at the ceiling.
* [experiment-v3-results.md](experiment-v3-results.md): v3 stopped at its pre-registered ceiling gate (notes control 20/20): uninformative, not a tie; costs, the one unverified mechanism, and why it was not extended.
* [e2e-real-sessions.md](e2e-real-sessions.md): runs inside a real host (Claude Code, headless `claude -p`) with real model sessions. `scripts/e2e_claude.py` re-runs it; it spends usage and is not in CI.
* [reliability-evidence.md](reliability-evidence.md): twelve failure scenarios, each with its mechanism, the test or run that covers it, the gaps and the signal a person gets.
* [benchmarks-summary.md](benchmarks-summary.md) and [benchmarking.md](benchmarking.md): the simulated pickup-versus-polling benchmark (no model) and how to run it.
* [coordination-experiment.md](coordination-experiment.md): the design of a team-outcome experiment and the offline simulation `scripts/team_sim.py` (simulated agents; says nothing about real models).
* [performance.md](performance.md): latency and memory against mailbox size.
* [coordination-audit.md](coordination-audit.md), [install-audit.md](install-audit.md), [first-use-audit.md](first-use-audit.md): defect hunts and what they found.
* [test-inventory.md](test-inventory.md): every test and CI job with its time and flake findings.

## Security and publication

* [threat-model.md](threat-model.md): assets, adversaries, trust assumptions, accepted risks.
* [publication-safety.md](publication-safety.md): the pre-publication scanner and how to configure it.

## Releasing and distribution

* [packaging.md](packaging.md): building and checking the wheel and sdist.
* [release-process.md](release-process.md): how a release is cut and verified, immutable releases, rollback.
* [pypi-publishing.md](pypi-publishing.md): the project is **not on PyPI**; the checks done, the blockers and the owner's numbered checklist. Nothing in it has been carried out.

## Adoption and contributing

* [adoption/](adoption/why-postbox.md): [why-postbox.md](adoption/why-postbox.md) (what it is and is not), [invite-five.md](adoption/invite-five.md) (the private five-tester beta), [trial-log.md](adoption/trial-log.md), [feedback-process.md](adoption/feedback-process.md), [launch-drafts.md](adoption/launch-drafts.md) (not posted), [communities.md](adoption/communities.md).
* [issues-to-open.md](issues-to-open.md): starter issues drafted for the tracker.
* [../CONTRIBUTING.md](../CONTRIBUTING.md): field-report walkthrough and the checks a change must pass.
