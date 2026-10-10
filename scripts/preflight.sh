#!/bin/sh
# Local pre-publication gate: tests, build, then publication scan of tracked files,
# built artifacts and the git log. POSIX sh. See docs/publication-safety.md.
#
# The forbidden list comes from $POSTBOX_FORBIDDEN (comma list) or, if unset, from
# ~/.config/agent-postbox/forbidden (one literal per line). Keep that file OUTSIDE the
# repository. It is never printed or passed as an argument by this script.
# Usage: scripts/preflight.sh [python]      (default python: python3)
set -eu
cd "$(dirname "$0")/.."
PY="${1:-python3}"
LISTFILE="$HOME/.config/agent-postbox/forbidden"

PATARGS=""
if [ -z "${POSTBOX_FORBIDDEN:-}" ]; then
    if [ -f "$LISTFILE" ]; then
        PATARGS="--patterns-file $LISTFILE"
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
echo "preflight: check_publication (tracked, dist, git log)" >&2
# shellcheck disable=SC2086  # PATARGS is intentionally word-split: one flag and one path
"$PY" check_publication.py --require-patterns $PATARGS --dist "$DIST" --git-log
echo "preflight: ok" >&2
