# Release process

A release is decided and published by a maintainer; the building is done by a
workflow. In one line: merge the release pull request, push an annotated tag
`vX.Y.Z`, and `.github/workflows/release.yml` builds the wheel and the sdist
reproducibly, scans them, and attaches both with a `SHA256SUMS` file to a
**draft** GitHub release (or to the release that already exists for the tag). A
person reviews the draft and publishes it.

The package is **not published on PyPI** and the workflow uploads nothing
there. Users install from a clone (`python -m pip install ./agent-postbox`),
from the wheel attached to a release, or from a wheel they build themselves
(see [packaging.md](packaging.md)).

This project's distribution name is `tyko84-agent-postbox`. The name
`agent-postbox` on PyPI belongs to an unrelated project, so release notes and
documentation must never say `pip install agent-postbox`: without the leading
`./` that command installs somebody else's code.

## 1. Decide the version

Semantic versioning. While the project is 0.x, a minor bump may change
behaviour; a patch bump must not. The version has one source of truth:
`__version__` in `agent_mail.py`. Do not write it anywhere else (`pyproject.toml`
reads it statically; `agent-postbox --version` prints it).

## 2. Prepare the release branch

On a branch off `main`:

1. Bump `__version__` in `agent_mail.py` only.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
   (the date the release is published, UTC), leave a fresh empty
   `## [Unreleased]` above it, and add the `[X.Y.Z]` link at the bottom.
   Entries describe what the release contains; nothing that is not merged
   belongs in a dated version.
3. Run the whole suite locally on one supported interpreter (3.10 to 3.13):
   `selftest.py`, `stress_test.py`, `test_hardening.py`, `test_adversarial.py`,
   `test_handoff.py`, `test_readme_examples.py`, `test_publication.py`,
   `test_packaging.py`, `test_field.py`, `ruff check .` and the `mypy` command from `ci.yml`.
4. Run the privacy preflight below.
5. Open a pull request into `main`. `main` is protected: the pull request must be
   green on every required check (see Required checks below) and is merged
   without a force push. Do not merge while `publication` or any other CI job is
   red or still running, required or not. Do not tag before the merge.

### Required checks

Branch protection on `main` is expected to require these status checks, all
reported by GitHub Actions from `ci.yml`: `lint`, `packaging`, `publication`, and
the `selftest` cells chosen as representative (at the time of writing
`selftest (ubuntu-latest, 3.10)`, `selftest (ubuntu-latest, 3.13)` and
`selftest (macos-latest, 3.12)`), and to apply to administrators as well.
`publication` is the job that runs `check_publication.py` with the forbidden list
from the repository secret; if it were not in the required list, a pull request
could be merged with that scan failing. This is the expected configuration, not a
guarantee: it lives in the repository settings, which this document cannot
enforce and anyone with admin rights can change. Whoever cuts a release checks
it first:

```sh
gh api repos/tyko84/agent-postbox/branches/main/protection/required_status_checks --jq '.contexts'
gh api repos/tyko84/agent-postbox/branches/main/protection/enforce_admins --jq '.enabled'
```

The first must include `publication`; the second must print `true` (required
checks then bind administrators too). The check name is the job id in `ci.yml`,
so renaming that job, giving it a `name:` or a matrix changes the name and leaves
the old one waiting forever; update the required list in the same change. Pull
requests from forks and from Dependabot do not receive the secret: there the job
runs the generic detectors only and still reports success, so requiring it does
not block them, and the full scan of their content first happens on the push to
`main` after the merge. Run `scripts/preflight.sh` on such a branch before merging
it. Administrators can bypass required checks unless "Do not allow bypassing the
above settings" is enabled for the branch, which is what the second command
above reports.

## 3. Privacy preflight (before every tag)

The repository is public and history is permanent. Before tagging, from a
clean checkout of the exact commit to be released, run the local gate
(`scripts/preflight.sh`: every test file, a build, then
`check_publication.py --require-patterns` over tracked files, the built
artifacts and the git log of every ref and tag). The forbidden list is operator-supplied and never
committed: `POSTBOX_FORBIDDEN` in the environment, or
`~/.config/agent-postbox/forbidden` (see
[publication-safety.md](publication-safety.md)). The same steps by hand:

```sh
POSTBOX_FORBIDDEN="name1,name2" python check_publication.py --require-patterns --git-log=--all
python scripts/repro_build.py --outdir /tmp/pb-out && POSTBOX_FORBIDDEN="name1,name2" python check_publication.py --require-patterns --dist /tmp/pb-out
python test_packaging.py
unzip -l /tmp/pb-out/*.whl        # expect agent_mail.py plus dist-info only
tar tzf /tmp/pb-out/*.tar.gz      # expect the MANIFEST.in allow-list plus setuptools metadata
```

Also confirm the commit metadata is what you intend to publish
(`git log --format='%an <%ae>%n%cn <%ce>%n%B' <range>`): author and committer
addresses and trailers become part of the public history and cannot be
recalled by moving a tag. Anything that fails the preflight is fixed on `main`
first; the release waits. The release workflow runs the same scan again, but
only after the tag is public: the preflight is what keeps a bad tag from being
pushed at all.

## 4. Tag the merge commit

Tag the **exact merge commit on `main`**, not the branch tip, and use an
annotated tag named `vX.Y.Z`:

```sh
git fetch origin && git checkout main && git pull --ff-only
git log -1 --format='%H %s'                       # the commit you are tagging
git tag -a vX.Y.Z <merge-sha> -m "agent-postbox X.Y.Z"
git tag -v vX.Y.Z 2>/dev/null || git show --no-patch vX.Y.Z
git push origin vX.Y.Z
```

The tag name, the tagger name and address and the tag message are public and
are scanned like commit metadata. Pushing the tag starts the release workflow.

**Never move, delete or re-create an existing tag.** A tag that was pushed with
the wrong commit, version or notes stays where it is; fix the problem on `main`
and release the next patch version. Clones, CI runs and release pages already
refer to the old tag object, so moving it would make the same name mean two
things. The workflow never creates, moves or deletes a tag either.

## 5. What the release workflow does

`.github/workflows/release.yml` runs for a pushed tag matching `v*.*.*` and for
nothing else; it has no manual trigger. It has two jobs.

`build` can read the repository and nothing more. Every gate is here, and each
one stops the run:

1. the ref is a tag named exactly `vX.Y.Z` (no suffix), the tag object is
   **annotated**, it is the same object the remote has, the checkout is the
   tagged commit, and that commit is an ancestor of `origin/main`;
2. `agent_mail.__version__` equals the tag without its `v`;
3. `CHANGELOG.md` has a `## [X.Y.Z]` heading with a non-empty section, which
   becomes the release notes;
4. the whole test list passes (the same scripts as `ci.yml`, with
   `test_field.py` and `test_packaging.py`), and the real-build tests of
   `test_publication.py` are not allowed to skip;
5. `python scripts/repro_build.py --check --outdir dist` builds twice and the two
   results are byte-identical;
6. `twine check` passes;
7. `dist` holds exactly `tyko84_agent_postbox-X.Y.Z-py3-none-any.whl` and
   `tyko84_agent_postbox-X.Y.Z.tar.gz`;
8. `check_publication.py --require-patterns --allowlist <empty file> --dist dist
   --git-log` passes: tracked files, both artifacts (forbidden list, generic
   detectors and the builder-identity rules), the history of the tagged commit,
   and every tag. The forbidden list comes from the repository secret
   `POSTBOX_FORBIDDEN` and is visible to that one step only; without the secret
   the step fails;
9. `SHA256SUMS` is written with `sha256sum` over the two files, verified with
   `sha256sum --check`, and must equal the hashes the reproducible build printed.

The three files and the notes are handed to the second job as workflow
artifacts (kept 14 days).

`publish` runs only if `build` succeeded. It checks out nothing and runs no
code from the repository. It verifies the three files against `SHA256SUMS`
again, records build provenance for the wheel and the sdist
(`actions/attest-build-provenance`), and then:

* **no release exists for the tag:** `gh release create vX.Y.Z --verify-tag
  --draft` with the notes and the three files. A draft is visible to
  maintainers only. `--verify-tag` means the command can never create a tag.
* **one release exists for the tag** (draft or published): `gh release upload`
  **without `--clobber`**, after checking that none of the three file names is
  already attached. An asset is never replaced, so a second run for the same
  tag fails instead of changing what users may already have downloaded.
* more than one release for the tag (two drafts): the job fails.

The draft is the default on purpose: nothing becomes public without a person
looking at it. Creating a release for the tag by hand *before* the workflow
finishes selects the second path, and if that release is already published the
assets are public the moment they are uploaded, without the review step. Leave
the release to the workflow unless there is a reason not to.

Permissions: the workflow has none by default (`permissions: {}`); `build` has
`contents: read`; `publish` has `contents: write` for the release and
`id-token: write` plus `attestations: write` for the provenance. Every action is
pinned to a full commit SHA, all of them published by GitHub (`actions/*`);
credentials are not persisted in the checkout; one run per tag at a time, never
cancelled part-way.

A failed gate leaves no release and no assets: `build` cannot write, and
`publish` does not start. A run that failed for a transient reason (a network
error) can be re-run from the Actions page; it runs the workflow file of the
tagged commit again. A run that failed because of what is in the tag cannot be
repaired by a re-run: fix `main` and release the next patch version.

## 6. Review and publish the draft

Open the draft on the releases page and check, before pressing Publish:

* the tag is the one you pushed and the title is `vX.Y.Z`;
* the notes are the `CHANGELOG.md` section for the version, followed by the
  checksums and the build line (commit, `SOURCE_DATE_EPOCH`, Python, zlib,
  setuptools). Edit the summary if it helps, in the voice of the
  [v0.2.0 release](https://github.com/tyko84/agent-postbox/releases/tag/v0.2.0);
  the notes should not say more than the changelog, and must never say
  `pip install agent-postbox`;
* exactly three assets are attached: the wheel, the sdist and `SHA256SUMS`;
* the workflow run for the tag is green, and the commit in the notes is the
  merge commit you tagged.

Then publish it. Publishing is the only manual step that makes anything public.

## 7. Verifying a download

Anyone can check what they downloaded, in increasing order of effort.

**Checksums.** Download `SHA256SUMS` next to the files and run:

```sh
sha256sum -c SHA256SUMS          # macOS without coreutils: shasum -a 256 -c SHA256SUMS
```

This shows the files are the ones the release lists. On its own it does not
show where they came from: the checksum file sits beside them.

**Provenance.** The workflow records a signed build provenance attestation for
the wheel and the sdist (GitHub artifact attestations, available for public
repositories). It ties the file's hash to this repository, the workflow file and
the tag that built it:

```sh
gh attestation verify agent_postbox-X.Y.Z-py3-none-any.whl -R tyko84/agent-postbox
gh attestation verify agent_postbox-X.Y.Z.tar.gz -R tyko84/agent-postbox
```

**Rebuild and compare.** The build is reproducible, so the same commit and the
same toolchain give the same bytes. From a clone:

```sh
git checkout vX.Y.Z                                   # the tag's own build script and pins
python3.12 -m venv /tmp/pb-verify && /tmp/pb-verify/bin/python -m pip install -r requirements-dev.txt
/tmp/pb-verify/bin/python scripts/repro_build.py --ref vX.Y.Z --verify /path/to/downloaded-files
```

The hashes are tied to the toolchain, and the release notes record it:

* **Interpreter: CPython 3.12**, the reference interpreter; the workflow asks
  `actions/setup-python` for `3.12`.
* **setuptools.** The wheel's `WHEEL` file names the setuptools version that
  generated it, so another setuptools gives another hash. The version is pinned
  in `build-constraints.txt`, which `scripts/repro_build.py` reads from the
  commit being built, so the release and anyone rebuilding the tag use the same
  one. The BUILDINFO block in the release notes records it.
* **zlib.** Both files are compressed by zlib. A different zlib can produce
  different compressed bytes from identical content. If only that differs,
  compare the contents instead: unpack both wheels (`unzip`) and both sdists
  (`tar xzf`) and run `diff -r`.
* `SOURCE_DATE_EPOCH` is the committer time of the tagged commit; the script
  derives it, so it does not need to be set.

See [packaging.md](packaging.md) for what the reproducible build does and does
not guarantee.

## 8. Rollback: what can be undone and what cannot

Can be undone:

* **A draft release**: delete it (`gh release delete vX.Y.Z` while it is a
  draft, or the web UI). Nothing was public. The tag stays; re-running the
  tag's workflow run creates the draft again.
* **A single asset** on a draft, or one attached by mistake:
  `gh release delete-asset vX.Y.Z <name>`. The workflow will not replace an
  asset, so delete first, then re-run, if a draft has to be rebuilt.
* **A published release with a defect**: publish a new patch version. Mark the
  old release as superseded in its notes; deleting it is possible but breaks
  links and hides what people already have.
* **Workflow artifacts** expire after 14 days and can be deleted from the run.

Cannot be undone:

* **A pushed tag is never moved**, deleted or re-created, whatever went wrong.
* **Downloaded assets cannot be recalled.** Once a release was published,
  treat its files as public for good, like the source archives GitHub generates
  for the tag.
* **A provenance attestation** is written to a public transparency log when the
  `publish` job runs, also if the draft is deleted afterwards. It states that
  this repository built a file with that hash from that tag, which stays true.
* **Commit, tagger and tag-message metadata** in the pushed tag and its history.
* Anything that was a secret and reached an artifact: rotate it; removing the
  asset does not make it secret again.

## 9. After the release

* Check that the tag resolves to the merge commit
  (`git rev-parse vX.Y.Z^{commit}` matches `git rev-parse main` at release
  time), that the release page shows the right notes and the three assets, and
  that `sha256sum -c SHA256SUMS` passes on a fresh download.
* `__version__` stays at the released value until the next release branch.
* Only the release notes and the changelog are updated for a fix to the notes;
  the tag and the assets are not touched.
