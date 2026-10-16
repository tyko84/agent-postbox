# Publishing to PyPI: prepared, not enabled

**This project is not on PyPI.** Nothing in this repository uploads anything to
PyPI or TestPyPI, no PyPI account, project or publisher exists for it, and the
maintainer has not decided to publish there. This page is the plan for the day
that changes: what was checked, what the upload job would look like, and every
step the account owner has to take, in order. Until those steps are done,
install from a clone or from the wheel attached to a GitHub release
([packaging.md](packaging.md)).

## State on 2026-10-15 (re-verified, nothing uploaded)

| Check | Result |
|---|---|
| `GET https://pypi.org/pypi/tyko84-agent-postbox/json` | **HTTP 404**, no such project; `https://pypi.org/simple/tyko84-agent-postbox/` also 404 |
| `GET https://pypi.org/pypi/agent-postbox/json` | **HTTP 200**: an unrelated project (its page is titled `agent-board`, author Ben Ballintyn, repository `benballintyn/agent-board`, releases 0.2.0 to 0.3.1 from June 2026). Not ours, not contactable by us |
| Version | `agent_mail.__version__` = `0.5.0`; `pyproject.toml` reads it (`dynamic`); `CHANGELOG.md` has `## [0.5.0]` and an `## [Unreleased]` section with changes made after the tag. The built files are named `tyko84_agent_postbox-0.5.0-*` |
| Reproducible build (`scripts/repro_build.py --check`, CPython 3.12.10, setuptools 84.0.0, build 1.6.1, on `main` at `283ce7b`) | two independent builds byte-identical. Because `main` has moved since the tag, its 0.5.0 files differ from the released ones (expected; see Blockers) |
| Wheel contents | `agent_mail.py`, `LICENSE` (under `dist-info/licenses/`), `METADATA`, `WHEEL`, `entry_points.txt`, `top_level.txt`, `RECORD`; nothing else, no dependencies |
| sdist contents | 88 members: `LICENSE`, `PKG-INFO`, sources, tests, `docs/*.md`, `examples/`, `hooks/`; no `scripts/`, no `.github/` |
| Install the wheel into a fresh venv (CPython 3.13, `--no-index --no-deps`) | `agent-postbox --version` prints `agent-postbox 0.5.0`; `pip show -f` lists `agent_mail.py`, the dist-info and the `agent-postbox` entry point |
| Uninstall | `pip uninstall` removes every file; the venv is back to `pip` only |
| `scripts/verify_release.py v0.5.0` | 13 checks OK, 1 skipped (`--rebuild` not requested): assets, `SHA256SUMS`, both hashes, both attestations, wheel and sdist members, builder identity |
| GitHub side (read-only `gh api`) | no environment `pypi` (0 environments), no repository variable `PYPI_PUBLISH`, `patterns_allowed` is `[]`, SHA pinning required, so the PyPA action is not allowed yet |
| `twine check` | not run in this pass (`twine` is not installed in the build venv); `test_packaging.py` and the release workflow's `test` job run it |

## Blockers, and who does what

Nothing below is done. **A** is the account owner (Tyler), **R** is a repository change a
maintainer reviews, **C** is the owner of the CI workflows.

| # | Blocker | Who | Notes |
|---|---|---|---|
| 1 | Decide to publish at all, and the `Author` string (`TyKo Sales LLC` today) | A | Uploaded metadata cannot be edited, and a file name can never be reused |
| 2 | No upload job in `release.yml`, and the PyPA action is not allowed by the actions policy | C, then A for the policy | The job is drafted below; the policy change is checklist step 6 |
| 3 | No PyPI account, no pending publisher, no GitHub environment `pypi`, no `PYPI_PUBLISH` variable | A | Checklist steps 3 to 5, 8 |
| 4 | The first PyPI release must be a **new version**, not v0.5.0: v0.5.0's tag is permanent, its README says "Not on PyPI" and its relative links would be dead on PyPI | A (decides), R (version and changelog) | `Unreleased` holds changes made since. Uploading the v0.5.0 files by hand would put an out-of-date README on the project page |
| 5 | README links are relative (33 links, 23 distinct, 3 of them fragment-only, as of 2026-10-16) and PyPI does not resolve them | R | Before the first published release, make every relative link absolute (`https://github.com/tyko84/agent-postbox/blob/main/<path>`), review the diff, and re-run `test_readme_examples.py` and `test_publication.py`. Fragment-only links such as `#install` also break on PyPI; reword them. `twine check` does not catch this |
| 6 | README and docs say "Not on PyPI" | R | List under "Wording to change", in the same release as the first upload |
| 7 | Rehearsal on TestPyPI not done | A | Optional but recommended: it is the only way to see the action start under the actions policy |

PyPI may refuse the name `tyko84-agent-postbox` as too similar to an existing one; that is
known only at the first upload. The rule "never write `pip install agent-postbox`" holds
whatever happens.

First checked on 2026-10-11 (same result again on 2026-10-15) through the JSON API (`https://<index>/pypi/<name>/json`):

| Name | pypi.org | test.pypi.org |
|---|---|---|
| `tyko84-agent-postbox` (also `tyko84_agent_postbox`, `tyko84.agent.postbox`: one name after normalisation) | 404, no such project | 404, no such project |
| `agent-postbox` (also `agent_postbox`) | **an unrelated project by another author**, with releases of its own | 404 |

A name that is free today is not reserved. PyPI may also refuse a new name that
it considers too similar to an existing one; whether it accepts
`tyko84-agent-postbox` is known only when the first upload is made.

The rule that follows from the second row does not change with publication:
never write `pip install agent-postbox`. This project's name on any index is
`tyko84-agent-postbox`.

## What is ready

Verified on the current tree with `scripts/repro_build.py` (CPython 3.12,
setuptools 84.0.0):

* `twine check --strict` passes for the wheel and the sdist.
* Core metadata, as a reader of the PyPI page would see it:

  | Field | Value |
  |---|---|
  | `Metadata-Version` | 2.4 |
  | `Name` | `tyko84-agent-postbox` |
  | `Version` | from `agent_mail.__version__` |
  | `Summary` | the one-line description in `pyproject.toml` |
  | `License-Expression` | `MIT` (and `License-File: LICENSE`) |
  | `Requires-Python` | `>=3.10` |
  | `Description-Content-Type` | `text/markdown` (the README) |
  | `Project-URL` | `Homepage`, `Documentation`, `Changelog`, `Source`, `Issues` |
  | `Classifier` | ten, each present in PyPI's list of valid classifiers |
  | `Author` | the `authors` entry of `pyproject.toml` |

* The wheel and the sdist each install into a fresh environment on CPython
  3.11, 3.12 and 3.13 with `--no-index --no-deps` (the sdist with the pinned
  setuptools wheel as its only source); `import agent_mail` and
  `agent-postbox --version` work in all six. Those were the interpreters on
  the machine when this was checked; 3.10 and 3.14 were not tried.
* The files a release attaches to GitHub are the files that would go to PyPI,
  byte for byte: same build, same `SHA256SUMS`.

## Decisions that are the owner's, before anything else

1. **Whether to publish at all.** A file uploaded to PyPI can be yanked or
   deleted, but its file name can never be used again, by anyone, even after
   the project is deleted, and copies stay on mirrors. The project page, the
   `Author` field and the README become a second public face of the project.
2. **The `Author` string.** The metadata carries the `authors` name from
   `pyproject.toml`, the same name that is already in `LICENSE`. On PyPI it is
   shown on the project page and returned by the JSON API. Change it in
   `pyproject.toml` before the first upload if another name should appear;
   metadata of an uploaded file cannot be edited afterwards.
3. **The README as a project page.** PyPI renders the README but does not
   resolve relative links. The README has 33 of them (23 distinct, 3 fragment-only) as of 2026-10-16, among them `SECURITY.md`,
   `PROTOCOL.md`, `docs/packaging.md`, `CONTRIBUTING.md`, `CHANGELOG.md`: on
   PyPI they would lead nowhere. Make them absolute
   (`https://github.com/tyko84/agent-postbox/blob/main/...`) in the release that
   is published first. The README also says the project is not on PyPI; see
   Wording to change, below.
4. **Which upload mechanism** (next but one heading). This page recommends the
   PyPA action and is written for it.

## How the upload would work: Trusted Publishing

From PyPI's documentation (page titles at the end of this page):

* A **trusted publisher** is a configuration on PyPI that says: a GitHub
  Actions run of *this workflow file* in *this repository* (optionally in *this
  environment*) may upload to *this project*. The run asks GitHub for a
  short-lived OIDC token, PyPI checks the token's claims against the
  configuration and returns an upload token that is valid for at most 15
  minutes. No long-lived PyPI token is stored anywhere.
* A **pending publisher** is the same thing for a project that does not exist
  yet. It binds five values: the PyPI project name, the repository owner, the
  repository name, the workflow file name and the environment name. The first
  upload creates the project and turns the pending publisher into a normal
  one. **It does not reserve the name**: if someone else registers the name
  first, the pending publisher is void.
* The job needs `id-token: write`, set on the job, not on the workflow.
  Nothing else: no `contents` permission, no secret.
* With the PyPA action, **PEP 740 attestations** are generated and uploaded by
  default for every file: a signed statement, served by PyPI next to the file,
  that this workflow of this repository published it.
* A workflow that is called as a reusable workflow cannot be a trusted
  publisher. The upload job has to be in `release.yml` itself.
* PyPI requires two-factor authentication on every account.
* The token identifies a workflow *file name*. Anyone who can change
  `release.yml` on a ref that the environment accepts can publish, which is why
  the environment below accepts release tags only and asks a person first.

## The action, or a token exchange by hand

The publish action `pypa/gh-action-pypi-publish` is not published by GitHub.
This repository allows GitHub's own actions only, each pinned to a commit, so
today that action is **blocked**. Three ways forward:

| | (a) the PyPA action, pinned to a commit | (b1) OIDC exchange by hand, then `twine upload` | (b2) OIDC exchange by hand, then an uploader written here with the standard library |
|---|---|---|---|
| Policy change | one more allowed action, by exact commit | none | none |
| Third-party code in the job that holds the upload token | the action and its container image (built by PyPA from version-pinned requirements; the image is fetched by a tag named after the commit) | `twine` and about thirty packages it depends on, installed from PyPI at run time, pinned by version at best | none |
| Interface with PyPI | the one PyPI documents as stable | the token endpoint, which PyPI documents as an internal detail that may change at any time | the token endpoint as in (b1), plus the upload API |
| PEP 740 attestations | yes, by default | only with a further third-party package (`pypi-attestations`) | no: producing them needs a Sigstore client |
| Maintenance | bump one commit id, and the allow-list entry with it | follow twine and its dependencies | own and test an upload client and a multipart encoder against a live index |
| What a compromise could do | upload a file under this project's name for up to 15 minutes | the same | the same, but there is no third party to compromise |

**Recommendation: (a).** It is the only one of the three whose interface PyPI
promises to keep, and the only one that gives attestations without more
third-party code. (b1) is weaker than (a) on every row: more unpinned code next
to the token, an interface that is explicitly not stable. (b2) is the strictest
on third-party code and the weakest on everything a user can verify, and a
home-made uploader that is first exercised against the real index on a
permanent tag is the wrong place to learn. The cost of (a) is one deliberate
exception to the actions policy, limited to one commit of one repository, in a
job that has no other permission, runs no code from this repository, and waits
for a person.

What (a) does not give: the container image is selected by a tag, not by a
digest, and its Python dependencies are pinned by version, not by hash. The
environment's reviewer and the provenance PyPI records are the checks on that.

## Why the job is not in `release.yml` yet

Three facts decide it:

1. Release tags are permanent, and a release run uses the workflow file of its
   tag. A `release.yml` that cannot start is discovered only after a tag has
   been pushed, and that tag can never be released.
2. A workflow that references an action the repository's policy does not allow
   is refused. GitHub's documentation does not say whether that is decided per
   job when the job starts or for the whole file when the run starts. If it is
   the whole file, an upload job that is switched off would still stop `build`,
   `scan`, `test` and `publish`.
3. The job is useless until the owner has done the steps below anyway.

So the job is kept here, ready to paste, and goes into `release.yml` in the same
pull request that follows the policy change. An undefined repository variable
evaluates to an empty string, a job whose `if:` is false is skipped, and a
skipped job reports success: with the switch in the job's `if:`, the job is
inert until `PYPI_PUBLISH` is set to `true`, and it can be turned off again
without a commit.

### The job

Append to `.github/workflows/release.yml`, as the last job:

```yaml
  pypi:
    # Uploads the wheel and the sdist to PyPI by Trusted Publishing: no stored token.
    # Runs only when the repository VARIABLE PYPI_PUBLISH is 'true' (Settings > Secrets and
    # variables > Actions > Variables); otherwise it is skipped and the run stays green.
    # Last in the chain: nothing goes to PyPI unless the draft GitHub release was created.
    # The environment `pypi` accepts release tags only and waits for a reviewer.
    # An upload cannot be undone: PyPI never lets a file name be used twice.
    needs: [build, scan, test, publish]
    if: github.repository == 'tyko84/agent-postbox' && vars.PYPI_PUBLISH == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment:
      name: pypi
      url: https://pypi.org/project/tyko84-agent-postbox/
    permissions:
      id-token: write # the OIDC token PyPI exchanges for a 15-minute upload token; nothing else
    steps:
      # No checkout: this job runs no code from the repository.
      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
        with:
          name: release-dist
          path: dist

      - name: the three files, intact; only the wheel and the sdist go on
        run: |
          fail() { echo "::error::$1"; exit 1; }
          tag="$GITHUB_REF_NAME"
          [ "$GITHUB_REF_TYPE" = tag ] || fail "not a tag push"
          [[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
            || fail "the tag is not of the form vX.Y.Z"
          version="${tag#v}"
          want="SHA256SUMS tyko84_agent_postbox-$version-py3-none-any.whl tyko84_agent_postbox-$version.tar.gz"
          got="$(ls -A dist | LC_ALL=C sort | tr '\n' ' ')"
          [ "${got% }" = "$want" ] || fail "unexpected files from the build job: $got"
          [ "$(wc -l < dist/SHA256SUMS)" -eq 2 ] || fail "SHA256SUMS does not list two files"
          (cd dist && sha256sum --check --strict SHA256SUMS)
          # PyPI takes distributions only: SHA256SUMS stays behind
          mkdir upload
          mv "dist/tyko84_agent_postbox-$version-py3-none-any.whl" "dist/tyko84_agent_postbox-$version.tar.gz" upload/
          got="$(ls -A upload | LC_ALL=C sort | tr '\n' ' ')"
          [ "${got% }" = "${want#SHA256SUMS }" ] || fail "unexpected files to upload: $got"

      - name: upload to PyPI, with PEP 740 attestations
        uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
        with:
          packages-dir: upload
```

The commit id is the one the annotated tag `v1.14.2` of
`pypa/gh-action-pypi-publish` points at (the latest release on 2026-10-11).
Resolve it again when the job is added:

```sh
gh api repos/pypa/gh-action-pypi-publish/releases/latest --jq .tag_name
gh api repos/pypa/gh-action-pypi-publish/git/ref/tags/<tag> --jq '.object.type + " " + .object.sha'
gh api repos/pypa/gh-action-pypi-publish/git/tags/<sha of the tag object> --jq '.object.sha'   # if the type was "tag"
```

The same pull request also has to:

* allow that one action in `test_publication.py`: the `Workflows` test accepts
  the owners `actions` and `github` only, on purpose. Add the exact reference
  `pypa/gh-action-pypi-publish@<commit>` as the single exception, and extend the
  test so that the `pypi` job has `id-token: write` as its only permission, no
  checkout, and the two conditions in its `if:`;
* add `pypa/gh-action-pypi-publish` to the `ignore` list of the
  `github-actions` entry in `.github/dependabot.yml`. The allow-list names one
  commit. A pull request that moves the pin without the allow-list would pass
  every check (no pull request runs `release.yml`) and then stop the next
  release at a permanent tag. The pin and the allow-list are moved together, by
  hand;
* describe the job in [release-process.md](release-process.md) (a fifth job, a
  further gate, the approval step, what cannot be undone);
* be dry-run as that page describes under Changing the release workflow. The
  verification step of the job above was run that way when this page was
  written: it passes on the three files of a build, and fails on a fourth file,
  on a missing file and on a changed byte.

## Rehearsals

### Without credentials, any time

```sh
python scripts/repro_build.py --check --outdir /tmp/pb-pypi
python -m twine check --strict /tmp/pb-pypi/*.whl /tmp/pb-pypi/*.tar.gz
python -m venv /tmp/pb-pypi-venv
/tmp/pb-pypi-venv/bin/python -m pip install --no-index --no-deps /tmp/pb-pypi/*.whl
/tmp/pb-pypi-venv/bin/agent-postbox --version
python -m pip download --no-deps -d /tmp/pb-pypi-dl tyko84-agent-postbox     # must FAIL until the first upload
```

The last command is the check that nobody else has taken the name: today it
ends with `No matching distribution found`. If it ever succeeds before this
project has published, stop: the name belongs to someone else.

### With TestPyPI, in a scratch repository

TestPyPI (`test.pypi.org`) is a separate index with its own accounts, its own
projects and its own trusted publishers; nothing carries over to PyPI in either
direction. A rehearsal there shows what no local run can: that the action
starts under the actions policy, that the OIDC exchange is accepted, and what
the result looks like.

Do it in a scratch repository, not here: a rehearsal needs a release tag, and
in this repository a release tag is permanent.

1. Create a TestPyPI account with two-factor authentication.
2. Create a scratch repository under the same owner, for example
   `tyko84/agent-postbox-rehearsal`, and push a copy of `main` to it. Give it the
   same actions policy as this repository plus the one allowed action (the
   command is in the checklist below, with the scratch repository's name), a
   secret `POSTBOX_FORBIDDEN` holding an invented word, and an environment
   `testpypi`.
3. On TestPyPI, under Account settings, Publishing, add a pending publisher:
   project name `tyko84-agent-postbox`, owner `tyko84`, repository name
   `agent-postbox-rehearsal`, workflow name `release.yml`, environment name
   `testpypi`.
4. In the scratch copy only: replace `tyko84/agent-postbox` by the scratch
   repository's name in every `if:` of `release.yml`, add the job above with
   `environment: testpypi` (and no `url`), and add to the action's `with:`

   ```yaml
          repository-url: https://test.pypi.org/legacy/
   ```

   Set the variable `PYPI_PUBLISH` to `true` there, bump `__version__` and the
   changelog to a version that will never be released, merge, and push an
   annotated tag.
5. Watch the whole run: this is also the only way to see `build`, `scan`,
   `test` and `publish` of `release.yml` run for real without spending a
   permanent tag. Then check the upload:

   ```sh
   python -m pip download --no-deps -d /tmp/pb-testpypi --index-url https://test.pypi.org/simple/ tyko84-agent-postbox
   ```

6. Delete the scratch repository. The TestPyPI project and its file names stay
   used on TestPyPI; that costs nothing.

## The checklist, in order

Steps are marked **A** (the account owner, on pypi.org or in GitHub settings), **R** (a pull request) or **C** (the owner of the workflows). Nothing here has been done.

1. **Decide** (A+R) the four points under Decisions above; fix the `Author` string
   and make the README links absolute in a normal pull request. Also choose the version
   (blocker 4): the first PyPI release is a new version with its own changelog section.
2. **Rehearse** (A) on TestPyPI in a scratch repository, as above.
3. **Create the PyPI account** (A) at `https://pypi.org/account/register/`, verify
   the address, and enable two-factor authentication (PyPI requires it) with
   the recovery codes stored offline.
4. **Create the GitHub environment** (A) `pypi`: one required reviewer, release tags
   only. With `gh` (not run by anyone yet):

   ```sh
   uid="$(gh api users/tyko84 --jq .id)"
   gh api -X PUT repos/tyko84/agent-postbox/environments/pypi --input - <<JSON
   {"wait_timer": 0,
    "prevent_self_review": false,
    "reviewers": [{"type": "User", "id": $uid}],
    "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
   JSON
   gh api -X POST repos/tyko84/agent-postbox/environments/pypi/deployment-branch-policies \
     -f name='v*.*.*' -f type=tag
   gh api repos/tyko84/agent-postbox/environments/pypi \
     --jq '{name, rules: [.protection_rules[].type], policy: .deployment_branch_policy}'
   gh api repos/tyko84/agent-postbox/environments/pypi/deployment-branch-policies \
     --jq '.branch_policies[] | "\(.type) \(.name)"'
   ```

   `prevent_self_review` stays `false` while there is one maintainer: the person
   who pushes the tag is the only one who can approve. With a second maintainer,
   set it to `true`. The last command must print `tag v*.*.*` and nothing else.
5. **Register the pending publisher** (A) on PyPI: Account settings, Publishing,
   Add a new pending publisher, GitHub tab, with exactly:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `tyko84-agent-postbox` |
   | Owner | `tyko84` |
   | Repository name | `agent-postbox` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   From this moment until the first upload the name is still not reserved. Do
   not leave a long gap.
6. **Allow the action** (A), by exact commit, keeping everything else as it is:

   ```sh
   gh api repos/tyko84/agent-postbox/actions/permissions/selected-actions      # before: patterns_allowed is []
   gh api -X PUT repos/tyko84/agent-postbox/actions/permissions/selected-actions --input - <<'JSON'
   {"github_owned_allowed": true,
    "verified_allowed": false,
    "patterns_allowed": ["pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"]}
   JSON
   gh api repos/tyko84/agent-postbox/actions/permissions --jq '.allowed_actions, .sha_pinning_required'   # selected, true
   ```

   The commit in this pattern and the commit in `release.yml` must be the same
   string. Check it before every tag from now on:

   ```sh
   grep -o 'pypa/gh-action-pypi-publish@[0-9a-f]*' .github/workflows/release.yml
   gh api repos/tyko84/agent-postbox/actions/permissions/selected-actions --jq '.patterns_allowed[]'
   ```
7. **Add the job** (C+R) to `release.yml` in a pull request, with the test, Dependabot
   and documentation changes listed under The job. Merge it. Every check on the
   pull request passes without proving anything about the new job: no pull
   request runs `release.yml`.
8. **Set the switch** (A): `gh variable set PYPI_PUBLISH --repo tyko84/agent-postbox --body true`
   (a repository variable, not a secret: it is not sensitive, and a job's `if:`
   cannot read secrets). To turn publishing off again:
   `gh variable delete PYPI_PUBLISH --repo tyko84/agent-postbox`.
9. **Cut the new release** (A) exactly as [release-process.md](release-process.md)
   says and push its tag. `build`, `scan`, `test` and `publish` run as before and
   leave a draft GitHub release. The `pypi` job then waits: the run page shows
   "Waiting for review".
10. **Review, then approve.** (A) Check the draft release first (notes, three
    assets, checksums). Then approve the deployment on the run page. Approving
    is the step that cannot be taken back: within a minute the two files are on
    PyPI under a file name that can never be used again. Rejecting the
    deployment instead leaves the draft release and uploads nothing; the run is
    then marked failed, which is correct.
11. **Verify** (A) from a clean directory:

    ```sh
    python -m pip download --no-deps --no-cache-dir -d /tmp/pb-pypi-check tyko84-agent-postbox==X.Y.Z
    python -m pip download --no-deps --no-cache-dir --no-binary :all: -d /tmp/pb-pypi-check tyko84-agent-postbox==X.Y.Z
    cd /tmp/pb-pypi-check && sha256sum -c /path/to/SHA256SUMS            # the one attached to the GitHub release
    gh attestation verify tyko84_agent_postbox-X.Y.Z-py3-none-any.whl -R tyko84/agent-postbox
    gh attestation verify tyko84_agent_postbox-X.Y.Z.tar.gz -R tyko84/agent-postbox
    curl -fsS https://pypi.org/integrity/tyko84-agent-postbox/X.Y.Z/tyko84_agent_postbox-X.Y.Z-py3-none-any.whl/provenance | head -c 300
    ```

    The files from PyPI must be the files of the GitHub release, byte for byte,
    so the release's `SHA256SUMS` and the GitHub build provenance (recorded by
    the `publish` job) both hold for them. The last command returns the PEP 740
    provenance PyPI serves for the file; the project page shows the same under
    the file's details as the publisher `release.yml` of this repository. On
    PyPI, Account settings, Publishing, the pending publisher is gone and the
    project lists a normal one.
12. **Publish the draft GitHub release.** (A)
13. **Change the wording** (R) in the next pull request (see below). Until then the
    documentation says the project is not on PyPI, which has stopped being true.

If the upload fails half-way (one file accepted, one not), the accepted file
name is used for good. Do not try to repair that tag: release the next patch
version.

### Wording to change once it is true

* `README.md`: the "Not on PyPI" note and the install instructions around it.
  Keep the warning about the bare name `agent-postbox`: it stays someone else's.
* `docs/packaging.md`: the paragraph that says the project is not published on
  PyPI and that nothing uploads there.
* `docs/release-process.md`: the opening paragraphs, the job table and the
  rollback list (add: an uploaded file name cannot be reused; a release can be
  yanked, which hides it from resolvers without freeing the name).
* `.github/workflows/release.yml`: the header comment and the last two lines
  the `build` job appends to the release notes ("This project is not on PyPI").
* `docs/README.md` and this page: the title and the first paragraph.

## Re-check, 2026-10-13 (on 0.5.0, commit 954fb0b)

Nothing was uploaded. Results of a second local dry run:

* `scripts/repro_build.py --check` produced a byte-identical wheel and sdist.
* `twine check --strict` passes on both, also with `readme_renderer[md]` installed
  (a plain `pip install twine` has no Markdown renderer and only warns, so install it).
* The wheel holds `agent_mail.py`, `LICENSE` and dist-info only; no dependencies.
* Wheel and sdist install into fresh environments on CPython 3.11, 3.12 and 3.13, and
  `agent-postbox --version`, send, inbox, list, status and doctor work from the installed
  wheel. CPython 3.10 and 3.14 were not available locally; nothing in CI installs the built
  wheel on them.
* `tyko84-agent-postbox` still returns 404 on pypi.org and test.pypi.org; `agent-postbox` is
  an unrelated project.
* A pip install carries only the tool: `doctor`'s "see `install.py --adapter`" hint refers to
  a file a wheel does not contain.
* The sdist ships the `test_*.py` files but not `scripts/` or `.github/`, so
  `test_readme_examples.py` cannot run from an sdist.

Still open, all owner decisions: no upload job exists, v0.5.0's tag is permanent (so the first
PyPI release is either a hand upload of the existing files or a later version), no PyPI or
TestPyPI accounts or publishers exist, and the README notice and relative links must change
in the release that is published first.

## Re-check, 2026-10-16 (on `main` at `618a818`; read-only, nothing uploaded)

* `GET` on `https://pypi.org/pypi/tyko84-agent-postbox/json`, on `https://pypi.org/simple/tyko84-agent-postbox/`
  and on `https://test.pypi.org/pypi/tyko84-agent-postbox/json`: HTTP 404 each. `https://pypi.org/pypi/agent-postbox/json`:
  HTTP 200, still the unrelated `agent-board` project (author Ben Ballintyn, releases 0.0.1 to 0.3.1);
  `https://test.pypi.org/pypi/agent-postbox/json`: 404.
* GitHub, read-only: no environments (`total_count` 0), no repository variables,
  `patterns_allowed` is `[]`, `allowed_actions` is `selected`, SHA pinning is required.
  `.github/workflows/release.yml` still has no upload job ("Nothing is uploaded to PyPI").
* `agent_mail.__version__` is still `0.5.0`; the GitHub release `v0.5.0` is the latest and immutable, with
  `SHA256SUMS`, the wheel and the sdist.
* README relative links: 33 (23 distinct, 3 fragment-only), up from 29 (19 distinct); the count above
  was corrected. The README still says "Not on PyPI", as the wording list expects.
* Not repeated in this pass: the reproducible build, `twine check`, the sdist member count (88) and the
  installs into fresh environments. Those rows describe the tree of 2026-10-13 to 2026-10-15.

## Sources

PyPI documentation at `docs.pypi.org`, read on 2026-10-11, by page title:

* "Publishing to PyPI with a Trusted Publisher" (`/trusted-publishers/`): the
  exchange of an OIDC token for an upload token valid for 15 minutes.
* "Creating a PyPI Project with a Trusted Publisher"
  (`/trusted-publishers/creating-a-project-through-oidc/`): pending publishers,
  and that they do not reserve a name.
* "Adding a Trusted Publisher to an Existing PyPI Project"
  (`/trusted-publishers/adding-a-publisher/`): the owner, repository, workflow
  file name and optional environment a GitHub publisher binds.
* "Publishing with a Trusted Publisher" (`/trusted-publishers/using-a-publisher/`):
  `id-token: write` at job level; the manual token exchange and the warning that
  it is not a public interface.
* "Security Model and Considerations" (`/trusted-publishers/security-model/`):
  dedicated environment, required reviewers, tag protection, a publish job with
  nothing in it but the download and the upload.
* "Troubleshooting" (`/trusted-publishers/troubleshooting/`): reusable workflows
  are not supported; the meaning of `invalid-publisher`.
* "Producing attestations" (`/attestations/producing-attestations/`): the PyPA
  action attests by default; what doing it by hand requires.
* PyPI Help (`https://pypi.org/help/`): two-factor authentication is required;
  a file name is never reusable; why a new name can be refused.

GitHub documentation at `docs.github.com`: "Contexts reference" (an unset
configuration variable is an empty string; `vars` is available in a job's
`if:`), "Using conditions to control job execution" (a skipped job reports
success), "Managing GitHub Actions settings for a repository" (allowing a
specific action by `OWNER/REPOSITORY@SHA`), "Deployments and environments"
(required reviewers, deployment branches and tags), and the REST reference for
environments, deployment branch policies and Actions permissions.
