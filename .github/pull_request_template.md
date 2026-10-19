## What and why

## Checks
- [ ] `python selftest.py`, `python stress_test.py`, `python test_hardening.py`, `python test_adversarial.py`, `python test_field.py`, `python test_handoff.py`, `python test_readme_examples.py`, `python test_publication.py`, `python test_packaging.py`, `python test_cli.py`, `python test_scenario.py`, `python test_team_sim.py`, `python test_adapters.py`, `python test_examples.py` and `python test_e2e_script.py`, `python test_live_demo.py`, `python test_micro_experiment.py` and `python test_experiment_v2.py` pass
- [ ] `ruff check .` and `mypy agent_mail.py install.py hooks/agent_mail_check.py check_handoff.py check_publication.py test_publication.py scripts/repro_build.py scripts/verify_release.py scripts/scenario.py scripts/team_sim.py scripts/ci_run.py stress_test.py test_field.py test_handoff.py test_packaging.py test_readme_examples.py selftest.py test_cli.py test_hardening.py test_adversarial.py test_scenario.py test_team_sim.py test_examples.py examples/two-agents/run.py test_adapters.py scripts/e2e_claude.py test_e2e_script.py scripts/experiment_micro.py examples/two-agents/live_demo.py test_live_demo.py test_micro_experiment.py scripts/experiment_v2.py scripts/analyze_v2.py test_experiment_v2.py scripts/experiment_v3.py scripts/analyze_v3.py test_experiment_v3.py scripts/experiment_v4.py scripts/analyze_v4.py test_experiment_v4.py` pass
- [ ] No private name, address, host or path in the diff or the commit metadata (`scripts/preflight.sh`)
- [ ] A change under `.github/workflows/` keeps the required check names, and a change to `release.yml` was dry-run (`docs/release-process.md`, Changing the release workflow)
- [ ] New behavior has a check that asserts on content that must appear (not on absence)
- [ ] Protocol changes are a numbered `PROTOCOL.md` section, not an edit to an old one

## Evidence
An author does not mark its own change independently reviewed or verified. Reviews are
evidence (links or pasted output), not authenticated approvals (CONTRIBUTING.md, How changes
get in).

- Tests run (commands):
- Platform and Python version:
- Last lines of output (`python3 scripts/ci_run.py`, `ruff check .`, `mypy`, `python3 check_publication.py --git-log`):
- Independent review or verification (links or pasted output, by someone other than the author):
- Dependency order, if stacked:

## Impact
- [ ] Contract change (PROTOCOL.md, exit codes, JSON schema, on-disk format): owner approval linked, CHANGELOG entry states compatibility
- [ ] Security change (trust boundary, publication checks, workflow permissions, secrets): owner approval linked
- [ ] Release impact (version, tag, release workflow): owner approval linked
- [ ] No AI co-author trailers or session URLs in commits or this text
