# Documentation

* [../README.md](../README.md): what this is, install, quickstart.
* [../PROTOCOL.md](../PROTOCOL.md): the whole spec.
* [../SECURITY.md](../SECURITY.md): threat model.
* [identity.md](identity.md): optional Unix-user identity binding (PROTOCOL.md
  section 40): what a message file's owner proves, what it does not, and the
  attacks it was checked against.
* [packaging.md](packaging.md): building and checking the wheel and sdist.
* [benchmarking.md](benchmarking.md): `scripts/scenario.py`, the reproducible
  benchmark and reliability harness (hook pickup versus a polling baseline
  under injected faults): what is simulated, how to run it, how to read it.
* [handoff-example.md](handoff-example.md): a filled-in handoff packet
  (validated by `test_handoff.py`).
* [publication-safety.md](publication-safety.md): the pre-publication scanner and how to configure it.
* [release-process.md](release-process.md): how a release is cut (version,
  changelog, tag, GitHub release, privacy preflight), how the published
  release is verified, immutable releases (not enabled), rollback.
* [pypi-publishing.md](pypi-publishing.md): the project is **not on PyPI**;
  this is the prepared plan for publishing there (checks done, the upload job,
  the owner's checklist). Nothing in it has been carried out.
