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
location is a path (`tracked:PATH`, `dist:ARTIFACT:MEMBER`, `git:SHA12`); a name
match is suffixed `#name`. A path that itself contains a forbidden word is therefore
visible in the log: rename it rather than allowlisting it.

Exit status: `0` clean, `1` findings, `2` the scan could not run (missing patterns
file, empty dist directory, unreadable git). Inability to scan is never reported
as success.

## Allowlist

`.publication-allowlist` (or `--allowlist FILE`): lines of `<rule-id> <path-glob>`
with an optional `# reason`. A finding is suppressed only when the rule id and the
path both match. For sdist members the glob is matched against the path without
the top directory. Prefer fixing the content to allowlisting it.

## Limits

* The history scan covers commit metadata and messages, not old file contents. A
  secret committed and later deleted is not found by `--git-log`; rotate it, and
  rewrite history if needed.
* Matching is literal substring, case-insensitive. Text is NFKC-normalised and
  stripped of zero-width characters first, so full-width letters and invisible
  joiners do not hide a term. A second pass with quotes, `+` and whitespace removed
  catches a literal split across string pieces (`"fo" + "o"`) and reports it as
  `forbidden-N-split`; allowlisting `forbidden-N` covers the split form too.
  Homoglyphs, encodings such as base64 or UTF-16, and other obfuscation are not
  detected.
* Members over 8 MB are reported as `unscanned-too-large`, and a tracked file that
  is missing from the work tree as `unscanned-missing`; neither is skipped silently.
* A `.publication-allowlist` committed in the repository is honoured by local runs.
  The CI job passes an empty allowlist from outside the checkout, so a pull request
  cannot silence the scan by adding one.
* Annotated tag messages are not scanned.

## Running it

```sh
POSTBOX_FORBIDDEN="name1,name2" python check_publication.py --git-log
python -m build --outdir /tmp/dist . && python check_publication.py --dist /tmp/dist
python test_publication.py      # includes positive controls: the scanner must fire on planted violations
```
