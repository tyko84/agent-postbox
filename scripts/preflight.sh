#!/bin/sh
# Local pre-publication gate: tests, build, then publication scan of tracked files,
# built artifacts and the git log of every ref. POSIX sh. See docs/publication-safety.md.
#
# The forbidden list comes from $POSTBOX_FORBIDDEN (comma list) or, if unset, from
# ~/.config/agent-postbox/forbidden (one literal per line). Keep that file OUTSIDE the
# repository. It is never printed or passed as an argument by this script.
# Usage: scripts/preflight.sh [python]      (default python: python3)
set -eu
cd "$(dirname "$0")/.."
PY="${1:-python3}"
LISTFILE="$HOME/.config/agent-postbox/forbidden"

# The positional parameters become the scanner's pattern arguments, so a list path with
# spaces in it stays one argument.
set --
if [ -z "${POSTBOX_FORBIDDEN:-}" ]; then
    if [ -f "$LISTFILE" ]; then
        set -- --patterns-file "$LISTFILE"
    else
        echo "preflight: no forbidden list (set POSTBOX_FORBIDDEN or create $LISTFILE)" >&2
        exit 2
    fi
fi

DIST="$(mktemp -d)"
trap 'rm -rf "$DIST"' EXIT INT TERM

for t in selftest.py stress_test.py test_hardening.py test_adversarial.py test_handoff.py \
         test_readme_examples.py test_publication.py test_packaging.py; do
    echo "preflight: $t" >&2
    "$PY" "$t" >/dev/null
done
echo "preflight: build" >&2
"$PY" -m build --outdir "$DIST" . >/dev/null
echo "preflight: check_publication (tracked, dist, git log of every ref and tag)" >&2
# --git-log=--all: every branch, remote-tracking ref and tag, not only the ancestors of HEAD.
"$PY" check_publication.py --require-patterns "$@" --dist "$DIST" --git-log=--all
echo "preflight: ok" >&2
