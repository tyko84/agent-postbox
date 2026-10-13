# PyPI publication: the ordered checklist

Nothing here has been done. **This project is not on PyPI**, nothing in the
repository uploads anything there, and whether to publish is the owner's
decision. This page is the checklist for the day that decision is made, and the
record of the dry run that can be done without publishing. The reasoning behind
the upload mechanism (Trusted Publishing, which action, why the upload job is
not in `release.yml` yet) is in [pypi-publishing.md](pypi-publishing.md); the
release itself is in [release-process.md](release-process.md).

## What the local dry run showed (2026-10-13)

Run from commit `954fb0b` of `main` (version 0.5.0) with
`scripts/repro_build.py --check` (CPython 3.12.10, setuptools 84.0.0, build
1.6.1). The checksums below are those of that build, not of the published
v0.5.0 release files (a different commit gives different bytes).

| Step | Result |
|---|---|
| two independent builds | byte-identical |
| `twine check --strict` on wheel and sdist (twine 7.0.0, throwaway venv) | PASSED, also with `readme_renderer[md]` installed |
| README rendered with `readme_renderer.markdown` | renders; 18 links, 14 of them relative (see item 6) |
| wheel contents | `agent_mail.py`, `LICENSE`, `dist-info` only; no hooks, no `install.py`, no docs, no tests |
| install the wheel, `--no-index --no-deps`, fresh venv | CPython 3.11, 3.12, 3.13: `agent-postbox --version` prints `agent-postbox 0.5.0`; `pip list` shows nothing but pip, setuptools where the venv brings it, and this package |
| install the sdist (pinned setuptools wheel as the only source), fresh venv | the same three interpreters, same result |
| send, inbox, doctor from the installed wheel in a temp directory | work; `doctor` ends `FAIL: pickup canary missing or stale` (exit 2) until a canary is filed, which is its designed answer for a fresh mailbox |
| CPython 3.10 and 3.14 | **not tried**: neither is installed on the machine. CI runs both (ubuntu and macOS) |

`Requires-Python: >=3.10`, five Python classifiers 3.10 to 3.14, which is
what the CI matrix runs (`test_readme_examples` checks the two agree).
`Operating System :: POSIX` is the only OS classifier, matching the README
("Windows is not supported or tested").

## The checklist

Do these in order. Steps 1 to 10 are reversible (step 8 puts files on TestPyPI
only). The first irreversible act is the approval in step 11.

1. **Decide whether to publish at all, and which version.** The v0.5.0 tag is
   permanent and a release run uses the workflow file of its own tag, so the
   existing v0.5.0 run can never gain an upload job. Either (a) upload the
   already-attached v0.5.0 wheel and sdist by hand, from a token, or (b) publish
   a later version (0.5.1 or 0.6.0) whose tag carries the new workflow. (b) is
   the only route to Trusted Publishing and attestations.
2. **Check the name is free.** A read-only GET; nothing is sent:
   `curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/tyko84-agent-postbox/json`
   prints `404` when the name is free and `200` when it is taken. Checked
   today: `404` on pypi.org and `404` on test.pypi.org. The bare name
   `agent-postbox` returns `200`: it is an unrelated project and must never
   appear in an install instruction here. A free name is not reserved, and
   PyPI may still refuse one it finds too similar to an existing name; that is
   known only at the first upload.
3. **Create the accounts.** One account on test.pypi.org and one on pypi.org
   (separate accounts; PyPI requires two-factor authentication).
4. **Decide the metadata that becomes public and cannot be edited after
   upload.** `Author` ("TyKo Sales LLC" from `pyproject.toml`), the five
   `Project-URL`s, the classifiers, the license expression (`MIT`), and the
   README as the project page.
5. **Make the README render as a project page.** PyPI shows the README but does
   not resolve relative links. The README has 14 relative links (`CHANGELOG.md`,
   `CONTRIBUTING.md`, `PROTOCOL.md`, `SECURITY.md`, `docs/*.md`,
   `examples/two-agents/README.md`); on PyPI they lead nowhere. Make them
   absolute (`https://github.com/tyko84/agent-postbox/blob/main/...`) in the
   release that is published first. Also reword the README's "not published on
   PyPI" notice and the "never install by bare name" advice to match what is
   true afterwards. (`pypi-publishing.md` counts eight links; the README now
   has 14.)
6. **Re-run the local dry run on the release commit**, in a throwaway venv, not
   the project's:
   `python scripts/repro_build.py --check --outdir OUT`, then
   `python -m venv tw && tw/bin/pip install twine 'readme_renderer[md]'`, then
   `tw/bin/twine check --strict OUT/*.whl OUT/*.tar.gz`, then install the wheel
   into a second fresh venv with `pip install --no-index --no-deps OUT/*.whl`
   and run `agent-postbox --version`. Install `readme_renderer[md]` explicitly:
   without it `readme_renderer` warns that no Markdown renderer is available,
   and a check that passes in that state is not evidence about the README.
7. **TestPyPI trusted publisher (or token).** On test.pypi.org, add a *pending
   publisher* (Account settings, Publishing) with: project `tyko84-agent-postbox`,
   owner `tyko84`, repository `agent-postbox`, workflow file `release.yml`,
   environment `testpypi`. A pending publisher does not reserve the name. With
   a token instead: create a TestPyPI API token scoped to the project after the
   first upload (the first upload needs an account-wide token).
8. **TestPyPI dry run.** Upload the exact files that will go to PyPI (same
   bytes, same `SHA256SUMS`): either through the workflow job in step 9 aimed at
   TestPyPI, or by hand with
   `twine upload --repository-url https://test.pypi.org/legacy/ OUT/*`. A
   file name burned on TestPyPI is harmless to PyPI. Then, in a fresh venv:
   `pip install --index-url https://test.pypi.org/simple/ --no-deps tyko84-agent-postbox==X.Y.Z`
   (`--no-deps` and the exact version: TestPyPI carries other people's
   packages), `agent-postbox --version`, a send and an inbox in a temp directory,
   and open the TestPyPI project page to look at the rendered README, the
   classifiers and the links.
9. **Change `release.yml` (described, not enabled).** Add one job, `pypi`:
   `needs: [build, scan, test, publish]`; `environment: pypi`;
   `permissions: { id-token: write }` and nothing else; no checkout; download
   the `release-dist` artifact; upload only the wheel and the sdist (not
   `SHA256SUMS`) with `pypa/gh-action-pypi-publish` pinned to a commit
   (attestations on by default); `if:` guarded by a repository variable such as
   `PYPI_PUBLISH == 'true'` so an unset variable skips it. Keep every existing
   pin and the presence of every step `test_publication.py` checks, and add the
   action's commit to the repository's allowed-actions list first (the
   repository allows GitHub's own actions only today; see
   [pypi-publishing.md](pypi-publishing.md)). Dry-run the edit as that page and
   `release-process.md` ("Changing the release workflow") describe: a mistake
   in this file is found only after a tag exists and cannot be fixed for that
   tag. Do not change it as part of this checklist's earlier steps.
10. **Create the GitHub environments.** `pypi` (and `testpypi`): deployment
    branches/tags limited to `v*.*.*` tags, required reviewer = the owner, so a
    person approves every upload.
11. **PyPI trusted publisher, then tag and approve.** On pypi.org add the pending
    publisher with the same five values (environment `pypi`). Push the annotated
    tag. The workflow builds, scans, tests and attaches to the draft release as
    before; the `pypi` job then waits for the reviewer. **Approving it is the
    irreversible step** (next section).
12. **After the upload.** Check `https://pypi.org/pypi/tyko84-agent-postbox/json`
    (now `200`), verify the file hashes against `SHA256SUMS`, install from PyPI
    in a fresh venv with the exact version, and verify attestations. Only then
    update the README and `docs/packaging.md` install instructions, and publish
    the draft GitHub release.

## What cannot be undone

* A file name `tyko84_agent_postbox-X.Y.Z-py3-none-any.whl` or `...tar.gz` can be
  yanked or deleted, but never uploaded again with different bytes, by anyone,
  even after the project is deleted. A mistake in 0.5.1 costs the number 0.5.1.
* Deleting a project does not free the name for a different owner in a way you
  can rely on, and copies survive on mirrors and in caches.
* The `Author`, classifiers, license and URLs of an uploaded version are fixed
  for that version.
* The README of an uploaded version is its project page forever; a fix is a new
  version.
* A PyPI publisher binds the workflow *file name* and environment: anyone who can
  change `release.yml` on a ref the environment accepts can publish. That is why
  step 10 limits the environment to release tags and a reviewer.

## Rollback options

* **Before step 11:** nothing public exists. Remove the workflow change, delete
  the pending publishers, delete the TestPyPI project.
* **After a bad upload:** *yank* the release on PyPI (it stays installable by
  exact pin, but resolvers skip it), then publish a fixed higher version.
  Deleting a release is possible but frees nothing.
* **Wrong or leaked token:** revoke it in the account settings; with Trusted
  Publishing there is none to leak, which is why it is preferred.
* **Compromised workflow file:** remove the trusted publisher on PyPI at once;
  the next upload then needs a person to re-add it.

## Decisions only the owner can make

1. Publish at all, and under which name (`tyko84-agent-postbox` is what the
   metadata says; renaming is cheaper before the first upload than after).
2. Which version first: hand-upload of the existing v0.5.0 files (token, no
   attestation) or a new version through the new workflow.
3. The `Author` string and whether PyPI should show it.
4. Trusted Publishing (recommended) or an API token.
5. Allowing the third-party publish action by commit in the repository's
   actions policy, or a different mechanism (see `pypi-publishing.md`).
6. Who is the required reviewer on the `pypi` environment.
7. Whether the README, which currently tells readers the project is not on PyPI,
   is rewritten for a PyPI audience in the same release.
8. Whether TestPyPI is a required rehearsal (this page assumes yes).
