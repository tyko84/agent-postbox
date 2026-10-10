# Publication safety

`check_publication.py` is a stdlib-only scanner that answers one question before
anything is published: does this repository, its built artifacts or its history
contain something that must not be public?

It scans three surfaces:

| Surface | Flag | What is checked |
|---|---|---|
| Tracked files | on by default (`--no-tracked` to skip) | every `git ls-files` path and file content |
| Built artifacts | `--dist DIR` | member names and contents of every sdist (`.tar.gz`) and wheel (`.whl`) in `DIR` |
| Git history | `--git-log [REV]` | author and committer names and emails, and full messages, of every commit reachable from `REV` (default `HEAD`; `--git-log=--all` covers every ref) |
| Tags | with `--git-log` | the name of every tag, and for an annotated tag its tagger name and email and its message (`git for-each-ref refs/tags`, then `git cat-file -p`); every tag is scanned whatever `REV` is |

## Forbidden patterns: supplied at run time, never committed

The list of names that must not appear (an employer, a private project, a person)
is itself sensitive, so it is **never stored in a tracked file**. Supply it one of
two ways:

* `POSTBOX_FORBIDDEN="name1,name2"` in the environment (comma list, case-insensitive
  literal substrings).
* `--patterns-file FILE`, one literal per line, `#` comments. Keep that file outside
  the repository.

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

`scripts/preflight.sh` (POSIX sh) runs the tests, builds the sdist and wheel, and
scans tracked files, the built artifacts and the git log. It reads the list from
`POSTBOX_FORBIDDEN` or, if unset, from `~/.config/agent-postbox/forbidden` (one
literal per line, `#` comments; the file lives outside the repository, keep it
mode 600). It refuses to run without a list. Needs `python3` with `build`
installed.

## Supported platforms

POSIX only: Linux and macOS are tested in CI. Windows is unsupported and has no
CI. The code uses no `fcntl`; it relies on POSIX file semantics (`os.link` for atomic
publish, `O_EXCL` marker/lock files, a directory `fsync` that is best effort, and
`O_NOFOLLOW` where available), which are untested on Windows.

## Generic detectors (always on)

| Rule id | Shape |
|---|---|
| `private-key` | a PEM private-key header |
| `github-token` | `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, `github_pat_` followed by a long token |
| `aws-access-key` | `AKIA` or `ASIA` followed by 16 upper-case alphanumerics |
| `home-path` | an absolute home directory (`/home/<name>/`, `/Users/<name>/`, `C:\Users\<name>\`); the placeholders `user`, `you`, `name`, `username`, `runner` are ignored |

## Output contract

Failures print only `<location>  <rule-id>`, one per line, plus a summary on
stderr. **The matched text and the forbidden pattern are never printed**, and rule
ids for supplied patterns are positional (`forbidden-1`, `forbidden-2`, ...) in the
order supplied, so a public CI log does not reveal what was being looked for. A
location is a path (`tracked:PATH`, `dist:ARTIFACT:MEMBER`, `git:SHA12`, `tag:NAME`);
a name match is suffixed `#name`. A path or tag name that itself contains a forbidden
word is therefore visible in the log: rename it rather than allowlisting it.

Exit status: `0` clean, `1` findings, `2` the scan could not run (missing patterns
file, empty dist directory, unreadable git). Inability to scan is never reported
as success.

## Allowlist

`.publication-allowlist` (or `--allowlist FILE`): lines of `<rule-id> <path-glob>`
with an optional `# reason`. A finding is suppressed only when the rule id and the
path both match. For sdist members the glob is matched against the path without
the top directory; commits match `git/SHA12` and tags `tag/NAME`. Prefer fixing the
content to allowlisting it.

## Limits

* `--git-log` reads commit and tag **metadata** only: names, emails and messages.
  It never reads file contents in old commits, so a term that was committed and
  later removed from the tree is still in the history and is not found by it. The
  tracked-file scan sees only the current work tree. The remedy is to rewrite the
  history (or recreate it in a fresh repository) so the old blob is gone, and to
  rotate anything that was a secret: once a term has reached a public remote it has
  to be treated as published, whatever the scanner says afterwards. To audit what a
  release exposed, run `--git-log=--all` for every ref and tag, then build from each
  release tag and scan that build with `--dist`; that covers what a user of the
  release actually received.
* Matching is literal substring, case-insensitive. Text is NFKC-normalised and
  stripped of zero-width characters first, so full-width letters and invisible
  joiners do not hide a term. A second pass with quotes, `+` and whitespace removed
  catches a literal split across string pieces (`"fo" + "o"`) and reports it as
  `forbidden-N-split`; allowlisting `forbidden-N` covers the split form too.
* Content is decoded as UTF-8, or as UTF-16 when the file or archive member starts
  with a UTF-16 byte-order mark (`FF FE` or `FE FF`); undecodable bytes are replaced,
  never fatal. Homoglyphs, encodings such as base64, UTF-16 without a byte-order
  mark and other encodings, and other obfuscation are not detected.
* Members over 8 MB are reported as `unscanned-too-large`, and a tracked file that
  is missing from the work tree as `unscanned-missing`; neither is skipped silently.
* A `.publication-allowlist` committed in the repository is honoured by local runs.
  The CI job passes an empty allowlist from outside the checkout, so a pull request
  cannot silence the scan by adding one.

## Running it

```sh
POSTBOX_FORBIDDEN="name1,name2" python check_publication.py --git-log
python -m build --outdir /tmp/dist . && python check_publication.py --dist /tmp/dist
python test_publication.py      # includes positive controls: the scanner must fire on planted violations
```
