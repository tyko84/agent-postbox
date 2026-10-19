# Documentation

Start with [../README.md](../README.md) (what this is, quickstart, CLI usage), then
[onboarding-first-exchange.md](onboarding-first-exchange.md). Nothing here claims a measured
benefit: see [Evidence](#evidence) for what was and was not shown.

## Using it

* [onboarding-first-exchange.md](onboarding-first-exchange.md): the tested path from a clone to a first exchange, with timings.
* [troubleshooting.md](troubleshooting.md): symptoms and fixes (including a denied `send`), and removing the install.
* [hosts.md](hosts.md): which hosts can run the pickup adapter (Claude Code; Codex, never run in the real host; Grok, polling through scheduled routines; Cursor, polling only), what each mechanism guarantees, and exactly what was verified.
* [grok-integration.md](grok-integration.md): how a Grok agent picks up mail (scheduled routines, no prompt hook), with every figure labelled measured, estimated, lab-verified or local pilot, and what was not shown.
* [codex-investigation.md](codex-investigation.md): what the Codex documentation says about hooks, trust, sandbox and sessions versus what the Codex adapter assumes; documentation read only, Codex never run; an 8-step validation plan for issue #21.
* [first-use-findings.md](first-use-findings.md): a timed README Quickstart run (machine time only) and the friction it found.
* [install-reference.md](install-reference.md): the pre-0.3.0 distribution-name note and verifying a release download.
* [windows.md](windows.md): Windows is unsupported; what was observed running the CLI and installer by hand on native Windows 11 (PowerShell 5.1, Git Bash, cmd.exe), the traps (UTF-16 `>` redirects, stdin encoding, the POSIX-syntax hook command), and what was never run in a real Claude Code session.
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
* [product-direction.md](product-direction.md): what the three experiments measured and could not, what the protocol adds beyond delivery, which outcomes would measure it, and the positioning the evidence supports.
* [positioning-proposal.md](positioning-proposal.md): a proposed tagline, repository description, README intro and comparison table that match product-direction.md, each sentence tagged evidence or belief; a README patch for the owner to apply later (nothing applied).
* [experiment-v4-design.md](experiment-v4-design.md): a design (not built, not approved beyond a small pilot) for an experiment on what the protocol adds: contested claims, recovery after a killed holder and audit reconstruction, against soft-notes and atomic-lock baselines.
* [experiment-v4.md](experiment-v4.md) and [experiment-v4-readiness.md](experiment-v4-readiness.md): the pre-registration of a small pilot on interrupted holders (arms: nothing, soft notes, atomic lock directory, Postbox claims, sequential oracle) and its readiness check; contested-scope is an observation only because the host's own stale-file check kept the baseline from colliding.
* [experiment-v4-results.md](experiment-v4-results.md): the v4 pilot failed its validity gates (a host refusal in two arms), so no claim; descriptive counts: the host already prevented lost updates, blocking arms strand or delay a survivor, the lease let the Postbox survivor through.
* [e2e-real-sessions.md](e2e-real-sessions.md): runs inside a real host (Claude Code, headless `claude -p`) with real model sessions. `scripts/e2e_claude.py` re-runs it; it spends usage and is not in CI.
* [reliability-evidence.md](reliability-evidence.md): twelve failure scenarios, each with its mechanism, the test or run that covers it, the gaps and the signal a person gets.
* [benchmarks-summary.md](benchmarks-summary.md) and [benchmarking.md](benchmarking.md): the simulated pickup-versus-polling benchmark (no model) and how to run it.
* [coordination-experiment.md](coordination-experiment.md): the design of a team-outcome experiment and the offline simulation `scripts/team_sim.py` (simulated agents; says nothing about real models).
* [performance.md](performance.md): latency and memory against mailbox size.
* [adapter-contract.md](adapter-contract.md): a proposal for what the core owns and what a host adapter owns, how hosts really differ, a zero-model preflight and what it can and cannot save; with measured unprompted-pickup results (including a failure).
* [context-cost.md](context-cost.md): how much text the pickup hook puts in front of a model (bytes, real input tokens, a replay of a real task), what shipped, what was rejected and why. Scripts and raw token data: [data/context-cost/](data/context-cost/context_cost.py).
* [coordination-audit.md](coordination-audit.md), [install-audit.md](install-audit.md), [first-use-audit.md](first-use-audit.md): defect hunts and what they found. The second pass, [coordination-audit-2.md](coordination-audit-2.md), checks claims, takeover after a killed holder and audit reconstruction with real processes; its stdlib reconstruction script is in `docs/data/audit-reconstruction/`.
* [cross-host-scenarios.md](cross-host-scenarios.md): Claude Code and Codex on one mailbox, run with two identities and no model or host; says which steps were not run in a real host.
* [host-findings-3.md](host-findings-3.md): round 3 on Claude Code 2.1.300, headless only: cases the earlier host pages did not cover.
* [fault-injection-results.md](fault-injection-results.md): a real `send` killed at every write step, and what was left on disk.
* [developer-needs.md](developer-needs.md): what public sources show developers running several AI tools want, separated from guesses, and what Postbox may and may not claim.
* [continuity-audit.md](continuity-audit.md): restarts, participants edits, discovery after renames and moves, kill -9 at random instants and concurrent startup, with real processes: two defects fixed, clean areas and reported gaps.
* [test-inventory.md](test-inventory.md): every test and CI job with its time and flake findings.

## Security and publication

* [threat-model.md](threat-model.md): assets, adversaries, trust assumptions, accepted risks.
* [publication-safety.md](publication-safety.md): the pre-publication scanner and how to configure it.
* [agent-intake.md](agent-intake.md): a clean-room procedure for adding agents, models, and reusable workflows from another repository without carrying private context into this public project.

## Releasing and distribution

* [packaging.md](packaging.md): building and checking the wheel and sdist.
* [release-process.md](release-process.md): how a release is cut and verified, immutable releases, rollback.
* [publication-readiness.md](publication-readiness.md): a read-only check of what a first PyPI release would ship and the owner-only steps; nothing uploaded.
* [pypi-publishing.md](pypi-publishing.md): the project is **not on PyPI**; the checks done, the blockers and the owner's numbered checklist. Nothing in it has been carried out.

## Adoption and contributing

* [adoption/](adoption/why-postbox.md): [why-postbox.md](adoption/why-postbox.md) (what it is and is not), [invite-five.md](adoption/invite-five.md) (the private five-tester beta), [trial-log.md](adoption/trial-log.md), [feedback-process.md](adoption/feedback-process.md), [launch-drafts.md](adoption/launch-drafts.md) (not posted), [communities.md](adoption/communities.md).
* [demo-script.md](demo-script.md): a scripted claim, expiry, takeover and audit walkthrough with output from real runs (no model, no recording).
* [adoption/beta-measurements.md](adoption/beta-measurements.md) and [adoption/feedback-questionnaire.md](adoption/feedback-questionnaire.md): what a beta would measure and the questions testers answer; no beta has run.
* [liveness-design-note.md](liveness-design-note.md): why there is no "holder is dead" signal and what would change that (decision: not built).
* [issues-to-open.md](issues-to-open.md): starter issues drafted for the tracker.
* [../CONTRIBUTING.md](../CONTRIBUTING.md): field-report walkthrough and the checks a change must pass.
