# Publication readiness (2026-10-16)

**Nothing was uploaded, tagged or released for this page.** It records a read-only readiness
check of what a first PyPI release would ship, which parts pass, which do not exist yet, and
the steps only the account owner can take. The plan and the drafted upload job are in
[pypi-publishing.md](pypi-publishing.md); the build design is in [packaging.md](packaging.md).

Checked on `main` at `c832fe1` (plus the one-line `pyproject.toml` fix described under
"Found and fixed"), macOS, CPython 3.13.7 for the builds, `build` 1.6.1, setuptools 84.0.0
(the pinned backend), `twine` 7.0.0 with `readme_renderer[md]`. Every row below was run in this
pass unless it says otherwise.

## What passes

| Check | Command | Result |
|---|---|---|
| Reproducible build, one process | `python scripts/repro_build.py --check --outdir DIR` | two independent builds byte-identical (wheel and sdist) |
| Reproducible build, two processes | `repro_build.py --outdir D1`, then `--outdir D2`, `diff D1/SHA256SUMS D2/SHA256SUMS` | identical |
| Rebuild matches what is on disk | `repro_build.py --verify D1` | both files identical to a rebuild of the commit |
| `twine check` | `twine check --strict *.whl *.tar.gz` | PASSED for both |
| Wheel contents | `unzip -l` | `agent_mail.py`, `LICENSE` under `dist-info/licenses/`, `METADATA`, `WHEEL`, `entry_points.txt`, `top_level.txt`, `RECORD`; nothing else; no dependencies |
| sdist contents | `tar tzf` | 105 members: the allow-list in `MANIFEST.in` (sources, `LICENSE`, `PKG-INFO`, `egg-info`, tests, `docs/**/*.md`, `examples/`, `hooks/`); no `scripts/`, no `.github/`, no `.bak`, no `__pycache__` |
| Fresh-venv install of the wheel | `pip install --no-index --no-deps *.whl` in a new venv and a temporary `HOME` | `agent-postbox --version` prints `agent-postbox 0.5.0`; `import agent_mail` resolves to site-packages; `python -m agent_mail --version` works; `send`, `list`, `status`, `doctor` run against a scratch `AGENT_MAIL_DIR`; `pip uninstall` leaves only `pip` |
| Python versions | wheel installed on CPython 3.11, 3.12 and 3.13 (`agent-postbox --version` each) | works. 3.10 and 3.14 are not installed on this machine: **not verified locally** |
| Classifiers against CI | `pyproject.toml` lists 3.10 to 3.14; `ci.yml` selftest matrix is `3.10 3.11 3.12 3.13 3.14` on ubuntu and macOS | they agree (`test_readme_examples.py` pins this). CI installs the **built wheel** only on 3.12 (the `packaging` job), so the wheel itself is exercised on 3.10 and 3.14 by nobody |
| Metadata | `METADATA` of the wheel | `Metadata-Version 2.4`, `Name tyko84-agent-postbox`, `License-Expression MIT`, `License-File LICENSE`, `Requires-Python >=3.10`, five `Project-URL`s, eleven classifiers, `Description-Content-Type text/markdown` |
| Published release still verifies | `python scripts/verify_release.py v0.5.0` | 13 OK, 1 skipped (`--rebuild` not asked) |
| Published release rebuilds | `verify_release.py v0.5.0 --rebuild` (needs `build` and the tag in the checkout) | 14 OK: the published wheel and sdist are byte-identical to a rebuild of the tag (`d469ce9`) |
| The names | `curl` on `pypi.org/pypi/tyko84-agent-postbox/json`, `pypi.org/simple/...`, `test.pypi.org/pypi/tyko84-agent-postbox/json` | HTTP 404 each: free today, not reserved |
| The other name | `pypi.org/pypi/agent-postbox/json` | HTTP 200: the unrelated `agent-board` project by Ben Ballintyn (0.2.0 to 0.3.1). `test.pypi.org`: 404. The README's "never `pip install agent-postbox`" stays true |

Two builds of the tree at `c832fe1` (not a release; it carries unreleased changes under the
version string `0.5.0`):

```text
2f0c35614ade5494f8d0cada1c6286338fe587b83a1483e657c6a01028d7a5a3  tyko84_agent_postbox-0.5.0-py3-none-any.whl
59131234e8d70606e629044fbdfbbd5ba63bd97f3cd8f0a26f3342ce98b7e541  tyko84_agent_postbox-0.5.0.tar.gz
```

These are not the hashes of v0.5.0 and must never be uploaded under that version. The sdist
carries `docs/*.md`, so its hash changes with every commit that touches a doc; only the hashes
of the commit that is actually tagged mean anything.

## Found and fixed in this pass

* The wheel's `Summary` (the first line of the PyPI page and of `pip show`) still read "Inter-agent
  mail for a code repo, with delivery that does not depend on the agent remembering to look". That
  is the delivery premise the README no longer leads with and that
  [product-direction.md](product-direction.md) says was not shown to beat a notes file. It now
  follows the README's tagline (a coordination ledger: exclusive claims with mandatory expiry, owed
  replies, an inspectable history). Metadata of an uploaded file cannot be edited, so this had to
  change before any upload, not after.
* The README link count in `pypi-publishing.md` was out of date: 35 relative links, 24 distinct,
  4 fragment-only (it said 33, 23, 3).

## What is missing (nothing here can be done by an agent)

| # | Missing | Evidence | Why it blocks |
|---|---|---|---|
| 1 | A decision to publish, and the `Author` string | `Author: TyKo Sales LLC` in the metadata | A file name can never be reused, even after deletion; metadata cannot be edited |
| 2 | A new version | `agent_mail.__version__` is `0.5.0`; the GitHub release `v0.5.0` is immutable and its tag permanent (`gh release view v0.5.0`: `isImmutable true`) | Main differs from the tag (see `CHANGELOG.md`, Unreleased). PyPI would otherwise carry the old README and the same version number as different bytes |
| 3 | Absolute README links and reworded "Not on PyPI" text | 35 relative links; the text appears in three places in `README.md` (quickstart comment, Install note, Status) | PyPI does not resolve relative links; `twine check` does not catch them |
| 4 | An upload job | `grep -c pypi .github/workflows/release.yml` is 0; the draft is in `pypi-publishing.md` | Nothing would publish |
| 5 | The actions policy allowing the publish action | `allowed_actions: selected`, `patterns_allowed: []`, `sha_pinning_required: true`, `verified_allowed: false` | The PyPA action is refused today |
| 6 | A PyPI account, a pending publisher, a TestPyPI account | none exists | Trusted Publishing needs them |
| 7 | A GitHub environment `pypi` and the variable `PYPI_PUBLISH` | `environments` total 0, `variables` total 0 | The drafted job is gated on both |
| 8 | A TestPyPI rehearsal | not done | The only way to see the action start under this repository's policy |
| 9 | Python 3.10 and 3.14 wheel install | not available here; CI installs the wheel on 3.12 only | A small gap: the code runs on both in the selftest matrix, the wheel is the same pure-Python file |

## Owner checklist

Steps marked (PR) are pull requests an agent can prepare for review; the rest are yours. Do
them in this order; `pypi-publishing.md` has the exact settings for each.

1. Decide whether to publish at all, knowing file names are permanent.
2. Decide the `Author` string and confirm it in `pyproject.toml` (or tell us the change).
3. Choose the first PyPI version number and approve a release (a new tag; v0.5.0 cannot be used).
4. (PR) Version bump, changelog section, absolute README links, and "Not on PyPI" wording
   (list under "Wording to change" in `pypi-publishing.md`). Review and merge.
5. Create a PyPI account with two-factor authentication, and a TestPyPI account.
6. On PyPI, add a pending publisher: repository `tyko84/agent-postbox`, workflow `release.yml`,
   environment `pypi`. On TestPyPI, add one for the scratch repository used in step 10 (a rehearsal
   needs a tag, and a tag here is permanent).
7. Create the GitHub environment `pypi` with you as required reviewer, restricted to release tags.
8. Allow the PyPA publish action, pinned by commit SHA, in the repository's Actions policy.
9. (PR) Add the upload job to `release.yml` and merge it; then set the repository variable
   `PYPI_PUBLISH` to `true` only when you are ready.
10. Rehearse on TestPyPI in a scratch repository; check the project page renders and the links work.
11. Push the release tag, approve the `pypi` environment when asked, then run
    `python scripts/verify_release.py <tag> --rebuild` and install from PyPI into a fresh venv
    (`pip install tyko84-agent-postbox`, never the bare name).
12. Check that nothing still says "Not on PyPI" (`grep -rn 'Not on PyPI' .`) if step 4 missed one.

## What this page does not claim

That PyPI would accept the name (it may refuse it as too similar to another; known only at the
first upload); that the wheel works on CPython 3.10 or 3.14 (not run); that anyone will find or
want the package; or anything about how agents behave with it. Machine checks only.
