# Release process

Releases are cut by a maintainer, by hand. Nothing here is automated, and the
package is **not published on PyPI**: a release is a git tag plus a GitHub
release; users install from a clone (`python -m pip install ./agent-postbox`)
or from a wheel they build themselves (see [packaging.md](packaging.md)).

The name `agent-postbox` on PyPI belongs to an unrelated project. Release notes
and documentation must therefore never say `pip install agent-postbox`: without
the leading `./` that command installs somebody else's code.

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

Branch protection on `main` should require these status checks, all reported by
GitHub Actions from `ci.yml`: `lint`, `packaging`, `publication`, and the
`selftest` cells chosen as representative (at the time of writing
`selftest (ubuntu-latest, 3.10)`, `selftest (ubuntu-latest, 3.13)` and
`selftest (macos-latest, 3.12)`). `publication` is the job that runs
`check_publication.py` with the forbidden list from the repository secret; while it
is not in the required list, a pull request can be merged with that scan failing.
This document does not make it so: the list lives in the repository settings, and
whoever cuts a release checks it first:

```sh
gh api repos/tyko84/agent-postbox/branches/main/protection/required_status_checks --jq '.contexts'
```

The output must include `publication`. The check name is the job id in `ci.yml`,
so renaming that job, giving it a `name:` or a matrix changes the name and leaves
the old one waiting forever; update the required list in the same change. Pull
requests from forks and from Dependabot do not receive the secret: there the job
runs the generic detectors only and still reports success, so requiring it does
not block them, and the full scan of their content first happens on the push to
`main` after the merge. Run `scripts/preflight.sh` on such a branch before merging
it. Administrators can bypass required checks unless "Do not allow bypassing the
above settings" is enabled for the branch.

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
first; the release waits.

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

**Never move or re-point an existing tag.** A tag that was pushed with the wrong
commit, version or notes stays where it is; fix the problem on `main` and
release the next patch version. Clones, CI runs and release pages already refer
to the old tag object, so moving it would make the same name mean two things.

## 5. Publish the GitHub release

Create a GitHub release from the tag (web UI or `gh release create vX.Y.Z`),
titled `vX.Y.Z`, with notes in the same voice as the
[v0.2.0 release](https://github.com/tyko84/agent-postbox/releases/tag/v0.2.0):
a short summary of what changed, then anything a user must know (behaviour
changes, migration, platform support). The notes may be condensed from the
`CHANGELOG.md` section for that version; they should not say more than it.
Attach nothing: the sdist and wheel are not published, and the source archives
GitHub generates for the tag are the artifacts.

## 6. After the release

* Check that the tag resolves to the merge commit
  (`git rev-parse vX.Y.Z^{commit}` matches `git rev-parse main` at release
  time) and that the release page shows the right notes.
* `__version__` stays at the released value until the next release branch.
* Only the release notes and the changelog are updated for a fix to the notes;
  the tag is not touched.
