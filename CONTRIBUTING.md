# Contributing

This is a small, stdlib-only tool plus a protocol spec. The most useful
contributions are **field reports**: where did a message not arrive, arrive
and get ignored, or get believed when it shouldn't have been? Open an issue
with what happened, what you expected, and the mechanism that was supposed to
deliver it (see `PARTICIPANTS.md`).

Code changes:

* `python selftest.py`, `python stress_test.py`, `python test_hardening.py`,
  `python test_handoff.py`, `python test_readme_examples.py` and
  `python test_packaging.py` must pass (the last skips, loudly, if the `build`
  package is unavailable), and so must
  `ruff check .` and `mypy agent_mail.py install.py hooks/agent_mail_check.py`
  (`ci.yml` is the source of truth for what CI runs).
* `python selftest.py` asserts on content that must appear. It asserts on content that *must* appear,
  never on absence. A test that can only observe silence proves nothing
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
