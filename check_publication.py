#!/usr/bin/env python3
"""Publication-safety scanner. Stdlib only.

Scans, for configurable forbidden patterns plus generic secret/path shapes:
  * every tracked file (names and contents)         --tracked (default on)
    (a tracked file missing from the work tree is reported as unscanned-missing)
  * member names and contents of built artifacts    --dist DIR (sdist .tar.gz, wheel .whl)
  * git history: author/committer name+email, messages  --git-log
    plus every tag name and, for annotated tags, tagger name+email and message

Forbidden patterns are NEVER stored in the repo. Supply them at run time:
  POSTBOX_FORBIDDEN="word1,word2"      comma list, case-insensitive literals
  --patterns-file FILE                 one literal per line, '#' comments

Output rule: only "<location>  <rule-id>" lines are printed. The matched text and
the pattern itself are never printed. Rule ids for supplied patterns are
positional (forbidden-1, forbidden-2, ...), so a report does not reveal them. Content
is decoded as UTF-8, or as UTF-16 when it starts with a UTF-16 byte-order mark. Text is
NFKC-normalised, stripped of zero-width characters and casefolded first; a second pass
with quotes, '+' and whitespace removed catches a literal split across string pieces
and is reported as forbidden-N-split.

Allowlist (--allowlist FILE, default .publication-allowlist if present): lines of
"<rule-id> <path-glob>   # reason". A finding is suppressed only when both match.

Exit status: 0 clean, 1 findings, 2 usage/environment error. Without a forbidden list the
generic detectors still run and a clean result exits 0 with a warning; --require-patterns
turns a missing list into exit 2.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tarfile
import unicodedata
import zipfile
from pathlib import Path

ENV_VAR = "POSTBOX_FORBIDDEN"
DEFAULT_ALLOWLIST = ".publication-allowlist"
MAX_BYTES = 8_000_000  # larger members are reported unscanned, never silently skipped
# Invisible code points dropped before matching (zero-width space/joiners, BOM, soft hyphen).
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
# Removed for the second, "joined" pass so a literal split across string pieces
# ("fo" + "o", "fo" "o", fo-\n-o) is still seen as one word. Reported as forbidden-N-split.
_JOIN = re.compile("[\"'+\\s]")

# Generic detectors. Source is assembled from pieces so this file does not match itself.
_GENERIC: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile("-----BEGIN (?:[A-Z]+ )*PRIV" + "ATE KEY")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_|github_" + "pat_)[A-Za-z0-9_]{20,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("home-path", re.compile(r"(?:/home/|/Users/)(?!(?:user|you|name|username|runner)\b)"
                             r"[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\(?!(?:user|you|name)\b)\w+")),
)

Finding = tuple[str, str]  # (location, rule-id)


def load_patterns(env: dict[str, str], patterns_file: str | None) -> list[str]:
    pats: list[str] = []
    raw = env.get(ENV_VAR, "")
    pats += [p.strip() for p in raw.split(",") if p.strip()]
    if patterns_file:
        for line in Path(patterns_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def load_allowlist(path: str | None) -> list[tuple[str, str]]:
    p = Path(path or DEFAULT_ALLOWLIST)
    if not p.is_file():
        if path:
            raise SystemExit(2)
        return []
    out: list[tuple[str, str]] = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) == 2:
            out.append((parts[0], parts[1]))
    return out


class Scanner:
    def __init__(self, patterns: list[str], allow: list[tuple[str, str]]) -> None:
        self.forbidden = [(f"forbidden-{i}", p.casefold()) for i, p in enumerate(patterns, 1)]
        self.allow = allow
        self.findings: list[Finding] = []

    def _allowed(self, loc: str, rule: str) -> bool:
        return any(r == rule and fnmatch.fnmatch(loc, g) for r, g in self.allow)

    def _add(self, loc: str, rule: str) -> None:
        if not self._allowed(loc, rule) and (loc, rule) not in self.findings:
            self.findings.append((loc, rule))

    def scan_text(self, loc: str, text: str, allow_loc: str | None = None) -> None:
        """Scan text; `allow_loc` is the path the allowlist is matched against."""
        al = allow_loc or loc
        folded = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text)).casefold()
        joined = _JOIN.sub("", folded)
        for rule, pat in self.forbidden:
            if pat in folded:
                if not self._allowed(al, rule):
                    self._add(loc, rule)
            elif (_JOIN.sub("", pat) in joined and not self._allowed(al, rule)
                  and not self._allowed(al, rule + "-split")):
                self._add(loc, rule + "-split")  # allowlisting forbidden-N covers this too
        for rule, rx in _GENERIC:
            if rx.search(text) and not self._allowed(al, rule):
                self._add(loc, rule)

    def scan_bytes(self, loc: str, data: bytes, allow_loc: str | None = None) -> None:
        if len(data) > MAX_BYTES:
            self._add(loc, "unscanned-too-large")
            return
        # A UTF-16 byte-order mark (FF FE or FE FF) selects UTF-16; the codec consumes it.
        enc = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
        self.scan_text(loc, data.decode(enc, "replace"), allow_loc)

    def scan_name(self, loc: str, name: str) -> None:
        self.scan_text(loc + "#name", name, allow_loc=loc)

    # ---- sources -------------------------------------------------------
    def scan_tracked(self, root: Path) -> None:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                           stdout=subprocess.PIPE, check=True)
        for rel in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
            if not rel:
                continue
            self.scan_text(f"tracked:{rel}#name", rel, allow_loc=rel)
            f = root / rel
            if f.is_symlink():
                continue  # the link target is not published content; its name was scanned
            if not f.is_file():
                self._add(f"tracked:{rel}", "unscanned-missing")  # tracked but absent: never silent
                continue
            self.scan_bytes(f"tracked:{rel}", f.read_bytes(), allow_loc=rel)

    def scan_dist(self, d: Path) -> None:
        files = sorted(list(d.glob("*.tar.gz")) + list(d.glob("*.whl")))
        if not files:
            raise SystemExit(2)
        for art in files:
            if art.suffix == ".whl":
                with zipfile.ZipFile(art) as z:
                    for n in z.namelist():
                        self._member(art.name, n, z.read(n) if not n.endswith("/") else b"")
            else:
                with tarfile.open(art) as t:
                    for m in t.getmembers():
                        fobj = t.extractfile(m) if m.isfile() else None
                        self._member(art.name, m.name, fobj.read() if fobj else b"")

    def _member(self, art: str, name: str, data: bytes) -> None:
        # allowlist globs match the member path with the sdist top directory stripped
        rel = name.split("/", 1)[1] if art.endswith(".tar.gz") and "/" in name else name
        loc = f"dist:{art}:{name}"
        self.scan_text(loc + "#name", name, allow_loc=rel)
        self.scan_bytes(loc, data, allow_loc=rel)

    def scan_git_log(self, root: Path, rev: str) -> None:
        r = subprocess.run(
            ["git", "-C", str(root), "log", rev, "-z",
             "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B"],
            stdout=subprocess.PIPE, check=True)
        for rec in r.stdout.decode("utf-8", "replace").split("\0"):
            rec = rec.lstrip("\n")
            if not rec:
                continue
            sha, *rest = rec.split("\x1f", 5)
            self.scan_text(f"git:{sha[:12]}", "\n".join(rest), allow_loc=f"git/{sha[:12]}")
        self.scan_tags(root)

    def scan_tags(self, root: Path) -> None:
        """Every tag name, plus tagger name/email and message of every annotated tag.

        Tags are refs, not ancestors of a commit, so all of them are scanned whatever REV
        `--git-log` was given. Old file contents are still out of scope (see the docs).
        """
        r = subprocess.run(
            ["git", "-C", str(root), "for-each-ref", "refs/tags",
             "--format=%(objectname) %(objecttype) %(refname:short)"],
            stdout=subprocess.PIPE, check=True)
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            sha, kind, name = line.split(" ", 2)
            self.scan_text(f"tag:{name}#name", name, allow_loc=f"tag/{name}")
            if kind != "tag":
                continue  # lightweight tag: a name pointing straight at a commit, no object
            obj = subprocess.run(["git", "-C", str(root), "cat-file", "-p", sha],
                                 stdout=subprocess.PIPE, check=True)
            self.scan_text(f"tag:{name}", obj.stdout.decode("utf-8", "replace"),
                           allow_loc=f"tag/{name}")


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publication-safety scanner (see module docstring).",
                                 allow_abbrev=False)
    ap.add_argument("--root", default=".", help="repository root (default .)")
    ap.add_argument("--dist", help="directory holding built sdist/wheel to scan")
    ap.add_argument("--git-log", nargs="?", const="HEAD", metavar="REV",
                    help="scan authors/committers/messages of REV (default HEAD, all ancestors; "
                         "use --git-log=--all for every ref) and every tag's name, tagger "
                         "and message")
    ap.add_argument("--no-tracked", action="store_true", help="skip the tracked-file scan")
    ap.add_argument("--patterns-file", help="file with one forbidden literal per line")
    ap.add_argument("--allowlist", help=f"allowlist file (default {DEFAULT_ALLOWLIST} if present)")
    ap.add_argument("--require-patterns", action="store_true",
                    help="exit 2 if no forbidden patterns were supplied")
    args = ap.parse_args(argv)
    environ = dict(os.environ) if env is None else env
    try:
        pats = load_patterns(environ, args.patterns_file)
        root = Path(args.root)
        allow = load_allowlist(args.allowlist if args.allowlist else
                               (str(root / DEFAULT_ALLOWLIST)
                                if (root / DEFAULT_ALLOWLIST).is_file() else None))
    except (OSError, SystemExit, UnicodeDecodeError):
        print("check_publication: cannot read patterns/allowlist file", file=sys.stderr)
        return 2
    if args.require_patterns and not pats:
        print("check_publication: no forbidden patterns supplied", file=sys.stderr)
        return 2
    sc = Scanner(pats, allow)
    print(f"check_publication: {len(pats)} forbidden pattern(s) configured; "
          f"{len(_GENERIC)} generic detectors", file=sys.stderr)
    if not pats:
        print("check_publication: WARNING: no forbidden list supplied; only the generic "
              "detectors ran, so a pass is NOT a full publication check", file=sys.stderr)
    try:
        if not args.no_tracked:
            sc.scan_tracked(root)
        if args.dist:
            sc.scan_dist(Path(args.dist))
        if args.git_log:
            sc.scan_git_log(root, args.git_log)
    except (subprocess.CalledProcessError, OSError, SystemExit, tarfile.TarError,
            zipfile.BadZipFile):
        print("check_publication: scan could not run (git/dist unreadable)", file=sys.stderr)
        return 2
    for loc, rule in sorted(sc.findings):
        print(f"{loc}  {rule}")
    if sc.findings:
        print(f"check_publication: FAIL ({len(sc.findings)} finding(s))", file=sys.stderr)
        return 1
    print("check_publication: ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
