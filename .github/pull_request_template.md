## What and why

## Checks
- [ ] `python selftest.py`, `python stress_test.py`, `python test_hardening.py`, `python test_adversarial.py`, `python test_field.py`, `python test_handoff.py`, `python test_readme_examples.py`, `python test_publication.py`, `python test_packaging.py`, `python test_cli.py`, `python test_scenario.py`, `python test_adapters.py`, `python test_examples.py` and `python test_e2e_script.py` pass
- [ ] `ruff check .` and `mypy agent_mail.py install.py hooks/agent_mail_check.py check_handoff.py check_publication.py test_publication.py scripts/repro_build.py scripts/verify_release.py scripts/scenario.py stress_test.py test_field.py test_handoff.py test_packaging.py test_readme_examples.py selftest.py test_cli.py test_hardening.py test_adversarial.py test_scenario.py test_examples.py examples/two-agents/run.py test_adapters.py scripts/e2e_claude.py test_e2e_script.py` pass
- [ ] No private name, address, host or path in the diff or the commit metadata (`scripts/preflight.sh`)
- [ ] A change under `.github/workflows/` keeps the required check names, and a change to `release.yml` was dry-run (`docs/release-process.md`, Changing the release workflow)
- [ ] New behavior has a check that asserts on content that must appear (not on absence)
- [ ] Protocol changes are a numbered `PROTOCOL.md` section, not an edit to an old one
