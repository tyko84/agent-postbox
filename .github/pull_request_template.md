## What and why

## Checks
- [ ] `python selftest.py`, `python stress_test.py`, `python test_hardening.py`, `python test_adversarial.py`, `python test_field.py`, `python test_handoff.py`, `python test_readme_examples.py`, `python test_publication.py` and `python test_packaging.py` and `python test_cli.py` pass
- [ ] `ruff check .` and `mypy agent_mail.py install.py hooks/agent_mail_check.py check_handoff.py check_publication.py test_publication.py` pass
- [ ] No private name, address, host or path in the diff or the commit metadata (`scripts/preflight.sh`)
- [ ] A change under `.github/workflows/` keeps the required check names, and a change to `release.yml` was dry-run (`docs/release-process.md`, Changing the release workflow)
- [ ] New behavior has a check that asserts on content that must appear (not on absence)
- [ ] Protocol changes are a numbered `PROTOCOL.md` section, not an edit to an old one
