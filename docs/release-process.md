# Release process

A release is decided and published by a maintainer; the building is done by a
workflow. In one line: merge the release pull request, push an annotated tag
`vX.Y.Z`, and `.github/workflows/release.yml` builds the wheel and the sdist
reproducibly, scans them in a separate job that installs nothing, and attaches
both with a `SHA256SUMS` file to a
**draft** GitHub release (or to the release that already exists for the tag). A
person reviews the draft and publishes it.

The package is **not published on PyPI** and the workflow uploads nothing
there. Users install from a clone (`python -m pip install ./agent-postbox`),
from the wheel attached to a release, or from a wheel they build themselves
(see [packaging.md](packaging.md)).
What publishing there would take (accounts, a trusted publisher, one more job
in the release workflow, and steps that cannot be undone) is written down in
[pypi-publishing.md](pypi-publishing.md). None of it has been done, and it is
the owner's decision.

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
3. Run the whole suite locally on one supported interpreter (3.10 to 3.14):
   `selftest.py`, `stress_test.py`, `test_hardening.py`, `test_adversarial.py`,
   `test_handoff.py`, `test_readme_examples.py`, `test_publication.py`,
   `test_packaging.py`, `test_field.py`, `ruff check .` and the `mypy` command from `ci.yml`.
4. Run the privacy preflight below.
5. Open a pull request into `main`. `main` is protected: the pull request must be
   green on every required check (see Required checks below) and is merged
   without a force push. Do not merge while `publication` or any other CI job is
   red or still running, required or not. Do not tag before the merge.

### Required checks

Branch protection on `main` requires a pull request (no approving review is
required, and the history must be linear), applies to administrators as well,
and requires these thirteen status checks, each bound to GitHub Actions as the
reporting app:

* `lint`
* `packaging`
* `publication`
* `selftest (ubuntu-latest, 3.10)`, `selftest (ubuntu-latest, 3.11)`,
  `selftest (ubuntu-latest, 3.12)`, `selftest (ubuntu-latest, 3.13)`,
  `selftest (ubuntu-latest, 3.14)`
* `selftest (macos-latest, 3.10)`, `selftest (macos-latest, 3.11)`,
  `selftest (macos-latest, 3.12)`, `selftest (macos-latest, 3.13)`,
  `selftest (macos-latest, 3.14)`

A check is named after the job id in `ci.yml` plus, for `selftest`, the matrix
values in order. Renaming a job, giving it a `name:`, or changing the `os` or
`python` lists therefore changes the names and leaves the old ones waiting
forever: change the required list in the same pull request. This is also why
`runs-on` stays `ubuntu-latest` and `macos-latest` and is not pinned to an image
version such as `ubuntu-24.04`: for `selftest` the label is part of the ten
required names. The price is that a new runner image arrives without a commit;
the `reproducible` job and the BUILDINFO block show what it changed.
`test_publication.py` (class `Workflows`) fails if the ids, the matrix or the
shape described below change.

`publication` is the job that runs `check_publication.py` with the forbidden list
from the repository secret; if it were not in the required list, a pull request
could be merged with that scan failing. GitHub treats a **skipped** required job
as passed, and skips a job when a job it needs has failed. `publication` needs
`publication-build` (which installs the dev tools and builds, and is not itself
a required check), so it runs under `if: always()` and its first step fails
unless `publication-build` succeeded: a broken build makes the required check
red, never grey. None of the other required jobs has a `needs:` or an `if:`.

Not required, and why: `publication-build` (covered by `publication`),
`reproducible` and `reproducible-compare` (they exist to show where the
byte-for-byte claim stops holding across operating systems), and the `codeql`
workflow (its findings arrive as code scanning alerts).

What runs where: only `lint` and `publication-build` install the dev tools
(`requirements-dev.txt`), and `packaging` and `reproducible` install the
hash-pinned build tools (`requirements-build.txt`), all on Python 3.12. The ten `selftest`
cells install nothing, so `test_packaging.py` is not in their list and the
real-build tests of `test_publication.py` skip there. The test that installs the
built wheel into a fresh environment and runs the installed `agent-postbox`
script is in `test_packaging.py`, and so runs in `packaging` only: Ubuntu,
Python 3.12 (and again in the release workflow's `test` job).

This is the expected configuration, not a guarantee: it lives in the repository
settings, which this document cannot enforce and anyone with admin rights can
change. Whoever cuts a release checks it first:

```sh
gh api repos/tyko84/agent-postbox/branches/main/protection/required_status_checks --jq '.checks[] | "\(.context) app=\(.app_id)"'
gh api repos/tyko84/agent-postbox/branches/main/protection/enforce_admins --jq '.enabled'
gh api repos/tyko84/agent-postbox/branches/main/protection/required_pull_request_reviews --jq '.url'
```

The first must print the thirteen names above, each with `app=15368` (GitHub
Actions; a check bound to an app cannot be satisfied by a status that some other
integration posts under the same name); the second must print `true` (required
checks then bind administrators too); the third must print a URL and not fail
with 404 (a pull request is required). Pull
requests from forks and from Dependabot do not receive the secret: there the job
runs the generic detectors only and still reports success, so requiring it does
not block them, and the full scan of their content first happens on the push to
`main` after the merge. Run `scripts/preflight.sh` on such a branch before merging
it.

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
nothing else; it has no manual trigger. It has four jobs, and each job has one
kind of power:

| Job | Can | Cannot |
|---|---|---|
| `build` | read the repository; install the three build tools, each pinned by hash, and the hash-checked build backend; run the build | read a secret (it references none); write anywhere; install anything that is not pinned by hash |
| `scan` | read the repository and the one secret, the forbidden list | install anything: no `pip`, no build, no package from PyPI |
| `test` | install the dev tools and what they depend on (pinned by version only); run the tests; read the files `build` produced | change those files: they left `build` before this job started; read a secret |
| `publish` | write the draft release and the provenance | run code from the repository: it checks out nothing |

`scan` and `test` both wait for `build` and run side by side; `publish` waits
for all three. Each gate stops its job, and a job that fails
skips every job that needs it.

`build`, in this order:

1. the ref is a tag named exactly `vX.Y.Z` (no suffix), the tag object is
   **annotated**, it is the same object the remote has, the checkout is the
   tagged commit, and that commit is an ancestor of `origin/main`;
2. `agent_mail.__version__` equals the tag without its `v`;
3. `CHANGELOG.md` has a `## [X.Y.Z]` heading with a non-empty section, which
   becomes the release notes;
4. `pip install --require-hashes -r requirements-build.txt` succeeds: `build`,
   `packaging` and `pyproject_hooks` are the files whose hashes the tagged
   commit lists ([packaging.md](packaging.md), The build tools);
5. `python scripts/repro_build.py --check --outdir dist` builds twice and the two
   results are byte-identical; before each build the setuptools wheel is checked
   against the SHA-256 in `build-constraints.txt`;
6. `dist` holds exactly `tyko84_agent_postbox-X.Y.Z-py3-none-any.whl`,
   `tyko84_agent_postbox-X.Y.Z.tar.gz` and `SHA256SUMS`;
7. `SHA256SUMS` is what `sha256sum` computes for the two files, passes
   `sha256sum --check`, and equals the hashes the reproducible build printed.

No test runs in `build`: some tests build the project with a plain
`python -m build` and whatever setuptools the index serves that day, which is
exactly what this job must not execute.

The three files and the notes leave `build` as two workflow artifacts,
`release-dist` and `release-notes` (kept 14 days).

`scan` then checks out the tagged commit with its whole history, fetches every
tag object again (so annotated tags are scanned with their tagger and message),
checks that the tag is annotated and is the commit it checked out, downloads
`release-dist` to a directory outside the checkout, and runs the scanner as
`python -I -S check_publication.py` (see
[publication-safety.md](publication-safety.md), In CI):

8. `--require-patterns --allowlist <empty file> --dist <release-dist> --git-log`
   passes: tracked files, both artifacts and `SHA256SUMS` (forbidden list,
   generic detectors, builder-identity rules, checksums that match), the history
   of the tagged commit, and every tag. The forbidden list comes from the
   repository secret `POSTBOX_FORBIDDEN`; without the secret the step fails;
9. the tree of **every tag**, the new one included, is archived with
   `git archive` and scanned the same way (`--no-tracked --dist`), so a file
   that an earlier release shipped and a later commit removed is still found.

`test` checks out the tagged commit and installs `requirements-dev.txt`:

10. the whole test list passes (the same scripts as `ci.yml`, with
    `test_field.py` and `test_packaging.py`), and the real-build tests of
    `test_publication.py` are not allowed to skip;
11. `twine check` passes on the wheel and the sdist from `release-dist`,
    downloaded to a directory outside the checkout.

`publish` runs only if `build`, `scan` and `test` all succeeded. It checks out
nothing
and runs no code from the repository. It verifies the three files against
`SHA256SUMS` again, records build provenance for the wheel and the sdist
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

Permissions: the workflow has none by default (`permissions: {}`); `build`,
`scan` and `test` have `contents: read`; `publish` has `contents: write` for the release and
`id-token: write` plus `attestations: write` for the provenance. Every action is
pinned to a full commit SHA, all of them published by GitHub (`actions/*`);
credentials are not persisted in the checkout; one run per tag at a time, never
cancelled part-way.

A failed gate leaves no release, no provenance and no assets: `build`, `scan`
and `test` cannot write, and `publish` does not start. On the run
page a failed `build` shows the three later jobs as skipped, and a failed `scan`
or `test` shows `publish` as skipped ("This job was skipped", a grey
icon); the run as a whole is red. The
only thing a failed run leaves behind is its workflow artifacts: the files
`build` made from the public tag, visible to signed-in users for 14 days unless
deleted from the run page. A run that failed for a transient reason (a network
error) can be re-run from the Actions page; it runs the workflow file of the
tagged commit again. A run that failed because of what is in the tag cannot be
repaired by a re-run: fix `main` and release the next patch version.

### Changing the release workflow

Release tags are permanent (a tag ruleset refuses to move or delete `v*`), and a
run always uses the workflow file of its tag. A mistake in `release.yml` is
therefore found only after a tag exists and cannot be fixed for that tag: the
next patch version is the only way out. No pull request runs this workflow.
Before merging a change to it:

* parse it (`python -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))'
  .github/workflows/release.yml` with PyYAML installed) and run
  `python test_publication.py Workflows`, which checks that the job holding the
  secret installs nothing, that `build` installs by hash only, that `publish`
  waits for the other three jobs, and that every action is GitHub-owned and
  pinned;
* in a scratch clone that is never pushed, make a commit whose `__version__` and
  `CHANGELOG.md` heading name a version that does not exist, tag it with an
  annotated tag, and run every `run:` block of every job by hand under
  `bash --noprofile --norc -eo pipefail` with `GITHUB_REF_TYPE=tag`,
  `GITHUB_REF_NAME`, `GITHUB_REPOSITORY`, `GITHUB_ENV`, `GITHUB_STEP_SUMMARY`,
  `RUNNER_TEMP` and a synthetic `POSTBOX_FORBIDDEN` set, `gh` replaced by a stub,
  and the artifact steps replaced by a copy. Run each gate once so that it
  passes and once so that it must fail;
* what this cannot show, and the first real tag will: that the pinned actions
  start, that the artifact passes between jobs, the provenance step, and
  `gh release create` with the job's token.

### Rehearsing in a scratch repository

What the dry run cannot show, a scratch repository can, and this is what was
done before v0.4.0 (the first tag under the restructured workflow):

1. Create an empty repository under the same account (not a fork: a fork
   receives no secret, and `publish` must run) and copy the tree in from a
   clean clone of the commit to be rehearsed.
2. Replace the repository guard in **both** workflow files with the scratch
   name: `github.repository == 'tyko84/agent-postbox'` on the four jobs of
   `release.yml`, and `[ "$GITHUB_REPOSITORY" = tyko84/agent-postbox ]` in the
   two scan steps of `ci.yml`. Change nothing else.
3. Give the scratch repository the same Actions policy as this one, and the
   repository secret `POSTBOX_FORBIDDEN` with the same list, set by hand.
4. Push `main`, then an annotated tag of a version that does not exist here.
   Verify the draft as in "Verifying the published release" below. The hashes
   differ from production's by design: `SOURCE_DATE_EPOCH` is the committer
   time of the tagged commit, and the scratch commit is another commit. The
   provenance step writes an attestation naming the scratch repository to the
   public transparency log; that cannot be recalled, like any other.
5. Afterwards: delete the secret, make the repository private, and archive it.
   Do not publish the draft.

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

### Verifying the published release

What was run for v0.4.0, on the draft before pressing Publish and again on the
published release. Each command is run from a clean clone of the tagged commit;
`DIR` is an empty directory outside the clone and `X.Y.Z` the version.

`scripts/verify_release.py vX.Y.Z [--dir DIR] [--rebuild]` (stdlib only) is
the automated form of the download, the checksum, provenance, contents and
builder-identity checks below, and with `--rebuild` of the rebuild: it
downloads the assets with `gh release download` into an empty directory (or
takes `--dir DIR --no-download`), prints one `OK`/`FAIL` line per check and
exits 1 if any failed. The publication scan and the install matrix that follow
stay manual. The commands are kept here so that each check can be run, and
read, on its own.

```sh
gh release download vX.Y.Z -R tyko84/agent-postbox -D DIR   # a draft: needs a token that can read it
(cd DIR && shasum -a 256 -c SHA256SUMS)                       # Linux: sha256sum -c SHA256SUMS
```

Rebuild the tag and compare (`build` is needed, pinned by hash, in a venv):

```sh
python3.12 -m venv /tmp/pb-verify && /tmp/pb-verify/bin/python -m pip install --require-hashes -r requirements-build.txt
/tmp/pb-verify/bin/python scripts/repro_build.py --ref vX.Y.Z --verify DIR
```

Provenance, for the wheel and for the sdist:

```sh
gh attestation verify DIR/tyko84_agent_postbox-X.Y.Z-py3-none-any.whl -R tyko84/agent-postbox
gh attestation verify DIR/tyko84_agent_postbox-X.Y.Z.tar.gz -R tyko84/agent-postbox
```

Contents and builder identity. The wheel holds `agent_mail.py` and the
`dist-info` directory, nothing else. Every sdist member is ownerless (uid and
gid 0, no user or group name), every mtime is `SOURCE_DATE_EPOCH` (one value,
the one in the BUILDINFO block of the notes), and the gzip header's mtime is 0:

```sh
unzip -l DIR/tyko84_agent_postbox-X.Y.Z-py3-none-any.whl
python3 - DIR/tyko84_agent_postbox-X.Y.Z.tar.gz <<'EOF'
import struct, sys, tarfile
path = sys.argv[1]
with open(path, "rb") as f:
    print("gzip mtime:", struct.unpack("<I", f.read(10)[4:8])[0])             # 0
with tarfile.open(path) as t:
    members = t.getmembers()
    print("owners:", {(m.uid, m.gid, m.uname, m.gname) for m in members})     # {(0, 0, '', '')}
    print("mtimes:", {m.mtime for m in members})                               # {SOURCE_DATE_EPOCH}
EOF
```

The publication scan, over the clone, the downloaded files and the history of
every ref, with the forbidden list from the owner's copy (it goes into the
environment and is never printed or passed as an argument):

```sh
POSTBOX_FORBIDDEN="$(cat ~/.config/agent-postbox/forbidden)" \
  python3 -I -S check_publication.py --require-patterns --root TREE --dist DIR --git-log=--all
```

Install and run, on every supported interpreter, from the file alone:

```sh
for py in python3.10 python3.11 python3.12 python3.13 python3.14; do
  "$py" -m venv "/tmp/pb-$py" || continue
  "/tmp/pb-$py/bin/python" -m pip install --no-index --no-deps DIR/tyko84_agent_postbox-X.Y.Z-py3-none-any.whl
  (cd "$(mktemp -d)" && mkdir mail && export AGENT_MAIL_DIR="$PWD/mail" \
     && "/tmp/pb-$py/bin/agent-postbox" --version \
     && printf 'rehearsal\n' > body.txt \
     && "/tmp/pb-$py/bin/agent-postbox" send --type NOTICE --from a --to b --subject "install check" --body-file body.txt \
     && "/tmp/pb-$py/bin/agent-postbox" list --json \
     && "/tmp/pb-$py/bin/agent-postbox" list | head -1)
done
```

`list | head -1` on a large mailbox ends with exit 141: `head` closes the pipe
and the command stops on SIGPIPE without a traceback, which is the behaviour
`test_cli.py` requires. The sdist is installed the same way, with a links
directory that holds only the setuptools wheel `build-constraints.txt` records
(check its SHA-256 against the `# sha256:` line first) so that the isolated
build can install nothing else:

```sh
python3.12 -m pip download --no-deps --only-binary :all: -d /tmp/pb-links "$(grep -v '^#' build-constraints.txt)"
(cd /tmp/pb-links && shasum -a 256 setuptools-*.whl) && grep '^# sha256:' build-constraints.txt
python3.12 -m venv /tmp/pb-sdist && /tmp/pb-sdist/bin/python -m pip install --no-index --find-links /tmp/pb-links DIR/tyko84_agent_postbox-X.Y.Z.tar.gz
/tmp/pb-sdist/bin/agent-postbox --version
```

Then publish, and mark the release as the latest:

```sh
gh release edit vX.Y.Z -R tyko84/agent-postbox --draft=false --latest
```

Finally download the three files again **without a token**, as a user would,
and compare them byte for byte with the files that were verified:

```sh
mkdir DIR2 && for f in tyko84_agent_postbox-X.Y.Z-py3-none-any.whl tyko84_agent_postbox-X.Y.Z.tar.gz SHA256SUMS; do
  curl -sSL -o "DIR2/$f" "https://github.com/tyko84/agent-postbox/releases/download/vX.Y.Z/$f" && cmp "DIR/$f" "DIR2/$f"
done
```

### Immutable releases

**Not yet enabled; the owner decides.** GitHub can make a repository's
releases immutable. The feature is currently disabled for this repository:

```sh
gh api repos/tyko84/agent-postbox/immutable-releases      # read `enabled` in the body
```

What enabling does, per the GitHub documentation
([concept](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases),
[enabling](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes),
[verifying](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity),
[REST endpoints](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository)):

* once a release is published, its tag is locked to its commit and cannot be
  moved or deleted while the release exists;
* its assets cannot be added, replaced or deleted after publication;
* the title and the notes stay editable, as does the pre-release and latest
  marking;
* it applies to future releases only: v0.2.0 to v0.4.0 stay as they are;
* a tag name that an immutable release used can never be used again, even if
  the release is deleted, and even if the repository is deleted and created
  again under the same name;
* a release attestation is generated at publication, naming the tag, the
  commit and the assets; `gh release verify vX.Y.Z -R tyko84/agent-postbox`
  checks it, and `gh release verify-asset vX.Y.Z FILE` checks a local file
  against it. The build provenance of section 7 is a separate attestation and
  is unchanged.

How it fits this repository's flow. Immutability attaches at publication, so
the draft that the workflow creates is still verified and completed as above:
GitHub's own recommendation is exactly this draft, attach, publish order. The
one path that stops working is the `publish` job's second branch, "one release
exists for the tag": it uploads into an existing release, and once that release
is published the upload is refused. That branch exists for a re-run after a
partial upload; with immutability on, a published release with a missing asset
can only be followed by the next patch version, which is this project's rule
anyway. The tag ruleset ("release tags are permanent") already forbids moving
or deleting `v*`; immutability adds the asset lock and the burned name.

Enable, check, disable (admin on the repository; each is one API call and is
not done by any workflow):

```sh
gh api -X PUT repos/tyko84/agent-postbox/immutable-releases      # enable (204)
gh api repos/tyko84/agent-postbox/immutable-releases             # check: "enabled": true
gh api -X DELETE repos/tyko84/agent-postbox/immutable-releases   # disable (204); releases already published stay immutable
```

Recovery rules once a release is immutable: a defect is fixed by the next patch
version, never by replacing an asset; deleting the release is allowed, but the
tag stays and its name is burned for good, so delete only to hide, never to
redo. Nothing here changes section 8: what could not be undone before still
cannot be.

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
gh attestation verify tyko84_agent_postbox-X.Y.Z-py3-none-any.whl -R tyko84/agent-postbox
gh attestation verify tyko84_agent_postbox-X.Y.Z.tar.gz -R tyko84/agent-postbox
```

**Rebuild and compare.** The build is reproducible, so the same commit and the
same toolchain give the same bytes. From a clone:

```sh
git checkout vX.Y.Z                                   # the tag's own build script and pins
python3.12 -m venv /tmp/pb-verify && /tmp/pb-verify/bin/python -m pip install --require-hashes -r requirements-build.txt
/tmp/pb-verify/bin/python scripts/repro_build.py --ref vX.Y.Z --verify /path/to/downloaded-files
```

The hashes are tied to the toolchain, and the release notes record it:

* **Interpreter: CPython 3.12**, the reference interpreter; the workflow asks
  `actions/setup-python` for `3.12`.
* **setuptools.** The wheel's `WHEEL` file names the setuptools version that
  generated it, so another setuptools gives another hash. The version and the
  SHA-256 of its wheel are pinned in `build-constraints.txt`, which
  `scripts/repro_build.py` reads from the commit being built and checks before
  it builds, so the release and anyone rebuilding the tag use the same file.
  The BUILDINFO block in the release notes records both. (Tags up to v0.3.1
  pin the version only and have no `requirements-build.txt`: install
  `requirements-dev.txt` to rebuild those.)
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
