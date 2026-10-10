# Publication safety

`check_publication.py` is a stdlib-only scanner that answers one question before
anything is published: does this repository, its built artifacts or its history
contain something that must not be public?

It scans these surfaces:

| Surface | Flag | What is checked |
|---|---|---|
| Tracked files | on by default (`--no-tracked` to skip) | every path in the index (`git ls-files -s`) and the content of its work-tree file. Where the work tree differs from the index, or the index from `HEAD` (a dirty tree, a staged deletion), the staged and the committed blob are scanned as well: the commit is what gets published, not the work tree |
| Tracked symlinks | with the tracked scan | the name and the link text (which is what git stores). The link is never followed, so a target outside the repository is never read |
| Built artifacts | `--dist DIR` | every file in `DIR`: its file name, and for an sdist (`.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tar.xz`) or wheel/zip (`.whl`, `.zip`, `.egg`) every member name and content, link targets, tar owner and group names, pax headers and zip comments. Independently of the forbidden list, the metadata a build copies from the machine it ran on is refused (see Builder identity) |
| Git history | `--git-log [REV]` | author and committer names and emails, and full messages (trailers included), of every commit reachable from `REV` (default `HEAD`; `--git-log=--all` covers every ref). Replace refs are ignored, so the original commits are read |
| Tags | with `--git-log` | the name of every tag, and for an annotated tag its tagger name and email and its message (`git for-each-ref refs/tags`, then `git cat-file -p`); every tag is scanned whatever `REV` is |

`--root` must be the top level of a git work tree. A run that would look at nothing
(a subdirectory, an empty repository, `--no-tracked` with no other surface, a `REV`
that selects no commit) exits 2. A pass ends with a line saying what was covered,
for example `scanned 40 tracked path(s), 49 archive member(s), 17 commit(s), 3 tag(s)`.

## Forbidden patterns: supplied at run time, never committed

The list of names that must not appear (an employer, a private project, a person)
is itself sensitive, so it is **never stored in a tracked file**. Supply it one of
two ways:

* `POSTBOX_FORBIDDEN="name1,name2"` in the environment (comma list, case-insensitive
  literal substrings).
* `--patterns-file FILE`, one literal per line, `#` comments. Keep that file outside
  the repository.

The environment list is split on commas, so a term that contains a comma can only
be given in a patterns file (in the environment it would become two shorter terms,
each matched on its own). A line that starts with `#` is a comment, so a term cannot
start with `#`. Patterns are normalised exactly like the scanned text (see Limits),
so it does not matter in which Unicode form or letter case a term is typed. A
pattern that is empty after normalisation, for example one made only of zero-width
characters, could never match and is rejected with exit 2, as is a patterns file
that holds no pattern at all.

In CI the list comes from the repository **secret** `POSTBOX_FORBIDDEN`
(Settings, Secrets and variables, Actions, Secrets). Do **not** use a repository
*variable*: variables are plain text, readable by anyone with read access to the
repository, and are not masked in logs, so they are not secret storage. Secrets are
masked in logs but are not passed to pull requests from forks or to Dependabot
runs; there the job emits a warning annotation and runs the generic detectors only.
The job hands the value to the scanner solely through the environment: it is never
passed as an argument, echoed, traced with `set -x` or written to a step summary.
Masking is best effort (a transformed value is not masked), which is why the
scanner itself never prints matched text or patterns.

Missing list: the scanner warns, still runs the generic detectors, and exits 0 only
if those pass. `--require-patterns` makes a missing list exit 2 instead;
`scripts/preflight.sh` always uses it.

## Local preflight

`scripts/preflight.sh` (POSIX sh) runs the tests, builds the sdist and wheel with
`scripts/repro_build.py`, and
scans tracked files, the built artifacts and the git log of every ref and tag
(`--git-log=--all`). It reads the list from
`POSTBOX_FORBIDDEN` or, if unset, from `~/.config/agent-postbox/forbidden` (one
literal per line, `#` comments; the file lives outside the repository, keep it
mode 600). It refuses to run without a list. Needs `python3` with `build`
installed.

## Supported platforms

POSIX only: Linux and macOS are tested in CI. Windows is unsupported and has no
CI. The code relies on POSIX file semantics (`os.link` for atomic publish,
`O_EXCL` marker files, `flock(2)` via `fcntl` for scope locks, a directory `fsync`
that is best effort, and `O_NOFOLLOW` where available), which are unavailable or
untested on Windows.

## Generic detectors (always on)

| Rule id | Shape |
|---|---|
| `private-key` | a PEM private-key header |
| `github-token` | `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_` followed by a long token |
| `aws-access-key` | `AKIA` or `ASIA` followed by 16 upper-case alphanumerics |
| `home-path` | an absolute home directory (`/home/<name>/`, `/Users/<name>/`, `C:\Users\<name>\`); the placeholders `user`, `you`, `name`, `username`, `runner` are ignored |

## Builder identity in artifacts (always on for `--dist`)

An archive has room for facts about the machine and the account that produced
it. They are not content, no review of the source shows them, and a forbidden
list only catches them if it happens to contain the builder's own login name. So
for every file under `--dist` the scanner refuses them by shape, with or without
a forbidden list. "Unsafe builder-identity metadata" is exactly this:

| Rule id | Format | Reported when |
|---|---|---|
| `builder-owner` | tar | a member's user or group name is anything but empty or `root` |
| `builder-uid` | tar | a member's uid or gid is not 0 |
| `builder-pax` | tar | a pax record other than `path`, `linkpath`, `size`, `mtime`, `comment`, `charset`, `hdrcharset`: so `uname`, `gname`, `uid`, `gid`, `atime`, `ctime`, `SCHILY.*`, `LIBARCHIVE.*`, `GNU.*` and every vendor record or extended attribute (macOS `com.apple.*` among them) |
| `builder-gzip-header` | gzip | a member header of the gzip stream has a file name (FNAME), a comment (FCOMMENT), an extra field (FEXTRA), a reserved flag or a non-zero timestamp. Every member of the stream is checked, not only the first |
| `builder-zip-extra` | zip, wheel | a member has an extra field other than zip64 (id `0x0001`), in the central directory or in its local header: Info-ZIP unix uid/gid (`0x7875`, `0x5855`, `0x7855`), timestamps with access time (`0x5455`), NTFS times, or bytes that do not parse as a field |
| `builder-zip-comment` | zip, wheel | the archive comment or a member comment is not empty |
| `builder-zip-attr` | zip, wheel | a member is not marked as made on unix or FAT, or its external attributes hold more than a file type (regular file, directory, symlink), permission bits and the DOS read-only, directory and archive bits: setuid, setgid and sticky bits, device types, hidden and system flags |
| `builder-os-junk` | both | a member is operating-system litter: `__MACOSX/`, `.DS_Store`, AppleDouble `._*`, `.AppleDouble`, `.Spotlight-V100`, `.Trashes`, `.fseventsd`, `Thumbs.db`, `desktop.ini` (any directory level, any letter case) |
| `builder-local-url` | both | a member named `direct_url.json` holds a `file:` URL: an installer wrote it for a local install, and the URL is a directory on that machine |

Each is reported as `<location>  <rule-id>` and the value is never printed. The
header rules are reported **once per artifact and rule** (`dist:ARTIFACT#owner`,
`#header`, `#gzip`, `#extra`, `#comment`, `#attr`), because the remedy is to
rebuild the artifact, not to edit a member; `builder-os-junk` and
`builder-local-url` name the member.

**Why `root` passes and nothing else does.** An empty name with uid and gid 0 is
what `scripts/repro_build.py` writes. `root` with uid and gid 0 is the constant
that `git archive` and `tar --owner=root --group=root` write on every system, so
it names no account of the builder; the audit recipe under Limits depends on it.
Any other name does describe the build machine: a login is a person, and a group
such as `wheel` or `staff` (what a real root or user build records) tells Linux
from macOS. `root` beside `wheel` therefore fails.

**What passes untouched.** Author, maintainer, licence and URL fields in
`METADATA` and `PKG-INFO`, the `Generator:`, `Tag:` and `Build:` lines of a
wheel's `WHEEL` file, file modes such as 0644, 0664 and 0755, member timestamps,
long or non-ASCII member names (a pax `path` record), and a `git archive`
tarball. These are content or format, chosen by the project, not copied from the
builder. Timestamps are not an identity; that they are the same on every build
is what `scripts/repro_build.py --check` verifies.

**Absolute paths and host names.** A home directory in any text member
(`RECORD`, `METADATA`, `direct_url.json`, `PKG-INFO`, `SOURCES.txt`, also when
the member is UTF-16) is found by the generic `home-path` rule. No tar, gzip, zip
or wheel field is defined to hold a host name. The free-form fields that could
carry one are refused when present (the gzip file name and comment, zip
comments, extra fields, unknown pax records) or scanned as text against the
forbidden list (the pax `comment` record, every text member). The `Generator:`
line names the build tool and its version, and a wheel's `Tag:` names an
interpreter and, for a platform wheel, an operating-system version and CPU type;
neither holds a host name. A host name inside ordinary text has no recognisable
shape: put it on the forbidden list.

**How to fix a finding.** Do not hand out what `python -m build` writes: its
sdist records the builder's uid, gid, login and group name in every tar header
and a file name and the build time in the gzip header (`builder-owner`,
`builder-uid`, `builder-gzip-header`). Build with the reproducible, ownerless
build instead, whose artifacts have none of the above
([packaging.md](packaging.md)):

```sh
python scripts/repro_build.py --outdir /tmp/pb-out
python check_publication.py --dist /tmp/pb-out
```

`builder-os-junk` in an artifact means an untracked file was packed; the script
builds from `git archive`, so it cannot happen there.

**Allowlisting.** These rules go through the ordinary allowlist, with one
restriction: a `builder-*` line takes an exact path, never a glob (a pattern
with `*`, `?` or `[` is rejected with exit 2). The path is the artifact file name
(`builder-owner agent_postbox-1.2.3.tar.gz`), or the member path for
`builder-os-junk` and `builder-local-url`. An artifact name carries its version,
so an exemption covers one build and lapses with the next. The CI and release
jobs run with an empty allowlist.

**What these rules do not cover.** The operating-system byte of the gzip header
and the "version made by" of a zip member (they name a family such as unix, not
a machine); member timestamps; the mode bits of tar members; `git archive
--format=zip`, which fails by design (it writes a zip comment and a timestamp
extra field), so audit old tags with the tar format. An extra field whose
declared length runs past its block makes Python refuse the whole zip: exit 2,
not a finding.

## Output contract

Failures print only `<location>  <rule-id>`, one per line, plus a summary on
stderr. **The matched text and the forbidden pattern are never printed**, also not
in error messages, and rule ids for supplied patterns are positional
(`forbidden-1`, `forbidden-2`, ...) in the order supplied, so a public CI log does
not reveal what was being looked for.

| Location | Meaning |
|---|---|
| `tracked:PATH` | the work-tree copy of a tracked file |
| `index:PATH`, `head:PATH` | the staged blob, the blob in `HEAD` (only scanned where they differ from the work tree or from each other) |
| `dist:ARTIFACT`, `dist:ARTIFACT:MEMBER` | a file in the `--dist` directory, a member of it |
| `git:SHA12`, `git:history` | one commit's metadata, the history as a whole |
| `tag:NAME` | a tag |

A suffix says which part matched: `#name` (a path, member or tag name), `#target`
(the text of a symlink or tar link), `#owner` (tar user or group name), `#header`
(a pax header), `#comment` (a zip comment), `#gzip` (the gzip header), `#extra`
and `#attr` (zip extra fields and attributes). A path or tag name that itself contains
a forbidden word is therefore visible in the log: rename it rather than
allowlisting it. Control characters in a location are escaped, so one finding is
always one line.

Exit status: `0` clean, `1` findings, `2` the scan could not run. Inability to scan
is never reported as success.

## Never a silent pass

Whatever the scanner was asked to cover but could not look inside is a finding
(exit 1), named for the reason:

| Rule id | Reported when |
|---|---|
| `unscanned-missing` | a tracked file is absent from the work tree |
| `unscanned-unreadable` | a tracked file cannot be read (permissions) |
| `unscanned-not-a-file` | a tracked path is a directory, fifo or device in the work tree |
| `unscanned-symlinked-path` | a parent directory of a tracked path is a symlink, so the path would resolve outside the tree |
| `unscanned-submodule` | the index entry is a gitlink: another repository's content |
| `unscanned-too-large` | a file or archive member is over 8 MB (it is not read) |
| `unscanned-archive` | a tracked file or an archive member is itself an archive or compressed stream (zip, gzip, bzip2, xz, zstd, 7z, tar); nested archives are not opened |
| `unscanned-unknown-artifact`, `unscanned-directory` | a file in the `--dist` directory that is neither an sdist nor a wheel/zip, or a subdirectory |
| `unscanned-encrypted` | a zip member is encrypted |
| `unscanned-special-member` | a tar member is a device or fifo |
| `unscanned-trailing-data` | a tar archive holds data after the last member that could be read (a damaged header hides every later member) |
| `unscanned-shallow-clone` | `--git-log` in a shallow clone: commits beyond the boundary are not there to scan (CI uses `fetch-depth: 0`) |
| `unscanned-grafts` | `--git-log` with a non-empty `.git/info/grafts`, which hides commits |
| `unscanned-tag-target` | a tag points at a blob or tree, content that no commit scan reaches |

The scan exits 2, without a verdict, when: `--root` is not the top of a git work
tree or git fails; no file is tracked; nothing was selected to scan; the patterns
or allowlist file is missing, undecodable or (patterns) empty; a pattern is empty
after normalisation; `--require-patterns` is set and no list was supplied; the
`--dist` directory is missing or holds no sdist or wheel; an archive is corrupt or
truncated (including a gzip stream whose checksum cannot be verified); or anything
else fails unexpectedly.

A file named `SHA256SUMS` in the `--dist` directory (`scripts/repro_build.py`
writes one) is scanned as text and checked against what is beside it:
`checksum-malformed` for any line that is not `<64 hex>  <bare file name>` naming a
file of the directory once, `checksum-mismatch` when a hash does not match, and
`checksum-unlisted` when an archive of the directory is not listed. Any other
non-archive file is still `unscanned-unknown-artifact`.

## Allowlist

`.publication-allowlist` (or `--allowlist FILE`): lines of `<rule-id> <path-glob>`
with an optional `# reason`. A finding is suppressed only when the rule id and the
path both match. The path is the tracked path (also for `index:` and `head:`
findings); for sdist members it is the member path without the top directory; for
`dist:ARTIFACT` findings the artifact file name; commits match `git/SHA12` and tags
`tag/NAME`. A glob made only of `*` would silence a rule everywhere and is rejected
(exit 2), and a `builder-*` rule takes no glob at all (see Builder identity). The
number of suppressed findings is printed on every run, so an
allowlist never changes a result unnoticed. Prefer fixing the content to
allowlisting it.

## Limits

* `--git-log` reads commit and tag **metadata** only: names, emails and messages.
  It never reads file contents in old commits, so a term that was committed and
  later removed from the tree is still in the history and is not found by it. The
  tracked-file scan sees the work tree, the index and `HEAD`, nothing older. The remedy is to rewrite the
  history (or recreate it in a fresh repository) so the old blob is gone, and to
  rotate anything that was a secret: once a term has reached a public remote it has
  to be treated as published, whatever the scanner says afterwards. To audit what a
  release exposed, run `--git-log=--all` for every ref and tag, then scan the tree
  of each release tag, which is what the source archive of that tag contains:

  ```sh
  mkdir /tmp/pb-audit
  for t in $(git tag); do git archive --format=tar.gz -o "/tmp/pb-audit/$t.tar.gz" "$t"; done
  python check_publication.py --require-patterns --no-tracked --dist /tmp/pb-audit
  ```
* A hosting service keeps refs that a normal clone does not fetch. On GitHub the
  head of every pull request, merged or not, stays reachable as `refs/pull/N/head`
  for the life of the repository; `--git-log=--all` only sees them after
  `git fetch origin 'refs/pull/*/head:refs/remotes/pr/*'`.
* Matching is literal substring, case-insensitive. Text is NFKC-normalised and
  stripped of zero-width characters first, so full-width letters and invisible
  joiners do not hide a term. A second pass with quotes, `+` and whitespace removed
  catches a literal split across string pieces (`"fo" + "o"`) and reports it as
  `forbidden-N-split`; allowlisting `forbidden-N` covers the split form too.
* Content is decoded as UTF-8, or as UTF-16 when the file or archive member starts
  with a UTF-16 byte-order mark (`FF FE` or `FE FF`); undecodable bytes are replaced,
  never fatal. Content that holds NUL bytes is scanned a second time with them
  removed, which finds an ASCII term stored as UTF-16 or UTF-32 without a byte-order
  mark, for example inside a binary file. Homoglyphs, encodings such as base64,
  other character encodings and other obfuscation are not detected.
* Archives are recognised by their leading bytes. Compressed data in another
  container (an image, a PDF, a zlib stream) is scanned as raw bytes only. In a zip,
  only what the central directory lists is read: bytes placed before the first
  member or between members are not.
* An sdist written by a plain `python -m build` records the user and group name of
  the account that built it in every tar header. That fails `builder-owner` whatever
  the list says, and `forbidden-N` as well (`dist:ARTIFACT#owner`) when the list
  contains the name.
* Git objects that are not commits or tags reachable from the scanned refs are out
  of scope: notes, stashes, unreachable objects, and other repositories' objects
  behind a submodule.
* A `.publication-allowlist` committed in the repository is honoured by local runs.
  The CI job passes an empty allowlist from outside the checkout, so a pull request
  cannot silence the scan by adding one.

## Running it

```sh
POSTBOX_FORBIDDEN="name1,name2" python check_publication.py --git-log
python scripts/repro_build.py --outdir /tmp/dist && python check_publication.py --dist /tmp/dist
python test_publication.py      # includes positive controls: the scanner must fire on planted violations
```

With git and the `build` package available, `test_publication.py` also builds this
project twice, once through `scripts/repro_build.py` (must scan clean) and once
with a plain `python -m build` (its sdist must be rejected, and the report must
not contain the name of the account that ran the test). Without them that class
is skipped with a message; `POSTBOX_REQUIRE_BUILD_TESTS=1` turns the skip into a
failure, for places where the tools are known to be installed.
