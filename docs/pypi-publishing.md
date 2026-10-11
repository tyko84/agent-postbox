# Publishing to PyPI: prepared, not enabled

**This project is not on PyPI.** Nothing in this repository uploads anything to
PyPI or TestPyPI, no PyPI account, project or publisher exists for it, and the
maintainer has not decided to publish there. This page is the plan for the day
that changes: what was checked, what the upload job would look like, and every
step the account owner has to take, in order. Until those steps are done,
install from a clone or from the wheel attached to a GitHub release
([packaging.md](packaging.md)).

Checked on 2026-10-11 through the JSON API (`https://<index>/pypi/<name>/json`):

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
  `agent-postbox --version` work in all six.
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
   resolve relative links. The README has eight of them (`SECURITY.md`,
   `PROTOCOL.md`, `docs/packaging.md`, `CONTRIBUTING.md`, `CHANGELOG.md`): on
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

Every step is an action of the account owner. Nothing here has been done.

1. **Decide** the four points under Decisions above; fix the `Author` string
   and the README links in a normal pull request if needed.
2. **Rehearse** on TestPyPI in a scratch repository, as above.
3. **Create the PyPI account** at `https://pypi.org/account/register/`, verify
   the address, and enable two-factor authentication (PyPI requires it) with
   the recovery codes stored offline.
4. **Create the GitHub environment** `pypi`: one required reviewer, release tags
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
5. **Register the pending publisher** on PyPI: Account settings, Publishing,
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
6. **Allow the action**, by exact commit, keeping everything else as it is:

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
7. **Add the job** to `release.yml` in a pull request, with the test, Dependabot
   and documentation changes listed under The job. Merge it. Every check on the
   pull request passes without proving anything about the new job: no pull
   request runs `release.yml`.
8. **Set the switch**: `gh variable set PYPI_PUBLISH --repo tyko84/agent-postbox --body true`
   (a repository variable, not a secret: it is not sensitive, and a job's `if:`
   cannot read secrets). To turn publishing off again:
   `gh variable delete PYPI_PUBLISH --repo tyko84/agent-postbox`.
9. **Cut the next release** exactly as [release-process.md](release-process.md)
   says and push its tag. `build`, `scan`, `test` and `publish` run as before and
   leave a draft GitHub release. The `pypi` job then waits: the run page shows
   "Waiting for review".
10. **Review, then approve.** Check the draft release first (notes, three
    assets, checksums). Then approve the deployment on the run page. Approving
    is the step that cannot be taken back: within a minute the two files are on
    PyPI under a file name that can never be used again. Rejecting the
    deployment instead leaves the draft release and uploads nothing; the run is
    then marked failed, which is correct.
11. **Verify** from a clean directory:

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
12. **Publish the draft GitHub release.**
13. **Change the wording** in the next pull request (see below). Until then the
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
