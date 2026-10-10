#!/usr/bin/env python3
"""Publication-safety scanner. Stdlib only.

Scans, for configurable forbidden patterns plus generic secret/path shapes:
  * every tracked file (names and contents)         --tracked (default on)
    The work-tree copy is read; where it differs from the index, or the index from HEAD
    (a dirty tree, a staged deletion), the staged and committed blobs are scanned too,
    because the commit is what gets published. A tracked symlink is never followed: its
    name and its link text are scanned.
  * every file in a directory of built artifacts    --dist DIR
    (file name, member names, contents, link targets, tar owner names, zip comments)
  * git history: author/committer name+email, messages  --git-log
    plus every tag name and, for annotated tags, tagger name+email and message

Forbidden patterns are NEVER stored in the repo. Supply them at run time:
  POSTBOX_FORBIDDEN="word1,word2"      comma list, case-insensitive literals
  --patterns-file FILE                 one literal per line, '#' comments

Output rule: only "<location>  <rule-id>" lines are printed. The matched text and
the pattern itself are never printed. Rule ids for supplied patterns are
positional (forbidden-1, forbidden-2, ...), so a report does not reveal them. Content
is decoded as UTF-8, or as UTF-16 when it starts with a UTF-16 byte-order mark; content
holding NUL bytes is scanned a second time with them removed. Text and patterns are
NFKC-normalised, stripped of zero-width characters and casefolded first; a second pass
with quotes, '+' and whitespace removed catches a literal split across string pieces
and is reported as forbidden-N-split.

Anything the scanner was asked to cover but could not look inside is a finding named
unscanned-<reason> (exit 1), never a silent pass; see docs/publication-safety.md.

Allowlist (--allowlist FILE, default .publication-allowlist if present): lines of
"<rule-id> <path-glob>   # reason". A finding is suppressed only when both match; the
number suppressed is reported.

Exit status: 0 clean, 1 findings, 2 usage/environment error (the scan could not run).
Without a forbidden list the generic detectors still run and a clean result exits 0 with a
warning; --require-patterns turns a missing list into exit 2.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import stat
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
_INVISIBLE = re.compile("[​‌‍⁠﻿­]")
# Removed for the second, "joined" pass so a literal split across string pieces
# ("fo" + "o", "fo" "o", fo-\n-o) is still seen as one word. Reported as forbidden-N-split.
_JOIN = re.compile("[\"'+\\s]")
_ZIP_SUFFIXES = (".whl", ".zip", ".egg")
_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
_PAX_SEEN = ("path", "linkpath", "uname", "gname")  # reported as #name, #target, #owner
# Leading bytes of containers whose payload a plain text scan cannot see.
_ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00",
                  b"\x28\xb5\x2f\xfd", b"7z\xbc\xaf\x27\x1c")

# Generic detectors. Source is assembled from pieces so this file does not match itself.
_GENERIC: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile("-----BEGIN (?:[A-Z]+ )*PRIV" + "ATE KEY")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_|github_" + "pat_)[A-Za-z0-9_]{20,}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("home-path", re.compile(r"(?:/home/|/Users/)(?!(?:user|you|name|username|runner)\b)"
                             r"[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\(?!(?:user|you|name)\b)\w+")),
)

Finding = tuple[str, str]  # (location, rule-id)


class ScanError(Exception):
    """The scan cannot run, or cannot be complete. main() reports it and exits 2.

    The message is printed, so it must never contain a pattern or scanned text.
    """


def _fold(text: str) -> str:
    """The one normal form both scanned text and forbidden patterns are compared in."""
    return unicodedata.normalize("NFKC", _INVISIBLE.sub("", text)).casefold()


def _show(loc: str) -> str:
    """A location safe to print on one line (control characters and lone surrogates escaped)."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
                   for c in loc)


def _git(root: Path, *args: str) -> bytes:
    # --no-replace-objects: a replace ref rewrites what `git log` shows locally, but the
    # original object is what a push publishes. Optional locks off: the scan never writes.
    # Only the first line of git's own complaint is relayed: it names a path or a revision,
    # never file content or a pattern.
    r = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                       capture_output=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if r.returncode:
        why = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["no message"])[0]
        raise ScanError(f"git {args[0]} failed: {_show(why)[:200]}")
    return r.stdout


def _blob_id(data: bytes | None, width: int) -> str:
    """The git object id of `data` as a blob (SHA-1, or SHA-256 for 64-digit ids)."""
    if data is None:
        return ""
    h = hashlib.sha1(usedforsecurity=False) if width == 40 else hashlib.sha256()
    h.update(b"blob %d\0" % len(data) + data)
    return h.hexdigest()


def _zsplit(raw: bytes) -> list[str]:
    return [s for s in raw.decode("utf-8", "surrogateescape").split("\0") if s]


def load_patterns(env: dict[str, str], patterns_file: str | None) -> list[str]:
    pats: list[str] = []
    raw = env.get(ENV_VAR, "")
    pats += [p.strip() for p in raw.split(",") if p.strip()]
    if patterns_file:
        n = len(pats)
        for line in Path(patterns_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
        if len(pats) == n:
            raise ScanError("the patterns file holds no patterns")
    return pats


def load_allowlist(path: str | None) -> list[tuple[str, str]]:
    p = Path(path or DEFAULT_ALLOWLIST)
    if not p.is_file():
        if path:
            raise ScanError("cannot read the allowlist file")
        return []
    out: list[tuple[str, str]] = []
    for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) == 2:
            if not parts[1].strip("*"):
                raise ScanError(f"allowlist line {n} matches every path; name the path")
            out.append((parts[0], parts[1]))
    return out


class Scanner:
    def __init__(self, patterns: list[str], allow: list[tuple[str, str]]) -> None:
        self.forbidden: list[tuple[str, str, str]] = []  # (rule-id, folded, folded and joined)
        for i, p in enumerate(patterns, 1):
            f = _fold(p).strip()
            if not f:  # e.g. only zero-width characters: it could never match anything
                raise ScanError(f"forbidden pattern {i} is empty after normalisation")
            self.forbidden.append((f"forbidden-{i}", f, _JOIN.sub("", f)))
        self.allow = allow
        self.findings: list[Finding] = []
        self.suppressed: set[Finding] = set()
        self.counts: dict[str, int] = {}

    def _count(self, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1

    def _add(self, loc: str, rule: str, allow_loc: str | None = None) -> None:
        """Record a finding unless the allowlist covers it (`allow_loc` is the path matched).

        Allowlisting forbidden-N covers forbidden-N-split too.
        """
        al = allow_loc or loc
        rules = (rule, rule[:-len("-split")]) if rule.endswith("-split") else (rule,)
        if any(r in rules and fnmatch.fnmatch(al, g) for r, g in self.allow):
            self.suppressed.add((loc, rule))
        elif (loc, rule) not in self.findings:
            self.findings.append((loc, rule))

    def scan_text(self, loc: str, text: str, allow_loc: str | None = None) -> None:
        """Scan text; `allow_loc` is the path the allowlist is matched against."""
        folded = _fold(text)
        joined = _JOIN.sub("", folded)
        for rule, pat, pat_joined in self.forbidden:
            if pat in folded:
                self._add(loc, rule, allow_loc)
            elif pat_joined and pat_joined in joined:
                self._add(loc, rule + "-split", allow_loc)
        for rule, rx in _GENERIC:
            if rx.search(text):
                self._add(loc, rule, allow_loc)

    def scan_bytes(self, loc: str, data: bytes, allow_loc: str | None = None) -> None:
        if len(data) > MAX_BYTES:
            self._add(loc, "unscanned-too-large", allow_loc)
            return
        if data.startswith(_ARCHIVE_MAGIC) or data[257:262] == b"ustar":
            # A nested archive or compressed stream: the text passes below cannot see inside.
            self._add(loc, "unscanned-archive", allow_loc)
        # A UTF-16 byte-order mark (FF FE or FE FF) selects UTF-16; the codec consumes it.
        enc = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
        self.scan_text(loc, data.decode(enc, "replace"), allow_loc)
        if enc == "utf-8" and b"\0" in data:
            # Wide (UTF-16/32 without a byte-order mark) ASCII text inside a binary file.
            self.scan_text(loc, data.replace(b"\0", b"").decode("utf-8", "replace"), allow_loc)

    def scan_name(self, loc: str, name: str) -> None:
        self.scan_text(loc + "#name", name, allow_loc=loc)

    # ---- sources -------------------------------------------------------
    def scan_tracked(self, root: Path) -> None:
        if _git(root, "rev-parse", "--show-prefix").strip():
            # In a subdirectory `git ls-files` lists only that part of the repository.
            raise ScanError("--root is not the top level of a git work tree")
        entries: dict[str, list[tuple[str, str]]] = {}  # path -> [(mode, blob sha)], per stage
        for rec in _zsplit(_git(root, "ls-files", "-s", "-z")):
            meta, rel = rec.split("\t", 1)
            mode, sha, _stage = meta.split()
            entries.setdefault(rel, []).append((mode, sha))
        if not entries:
            raise ScanError("no tracked files: nothing would be scanned")
        real_root = os.path.realpath(root)
        for rel, staged in entries.items():
            loc = f"tracked:{rel}"
            self._count("tracked path(s)")
            self.scan_text(loc + "#name", rel, allow_loc=rel)
            if any(mode == "160000" for mode, _ in staged):
                self._add(loc, "unscanned-submodule", rel)  # another repository's content
                continue
            f = root / rel
            work: bytes | None = None  # what the work tree holds for this path, once read
            try:
                st = os.lstat(f)
            except OSError:
                st = None
            if st is None:
                self._add(loc, "unscanned-missing", rel)  # tracked but absent: never silent
            elif os.path.realpath(f.parent) != os.path.normpath(
                    os.path.join(real_root, os.path.dirname(rel))):
                # A parent directory is a symlink: the path would resolve outside the tree.
                self._add(loc, "unscanned-symlinked-path", rel)
            elif stat.S_ISLNK(st.st_mode):
                # Never followed. Git stores the link text, so that is what is scanned.
                work = os.fsencode(os.readlink(f))
                self.scan_text(loc + "#target", os.fsdecode(work), allow_loc=rel)
            elif not stat.S_ISREG(st.st_mode):
                self._add(loc, "unscanned-not-a-file", rel)
            elif st.st_size > MAX_BYTES:
                self._add(loc, "unscanned-too-large", rel)
                continue
            else:
                try:
                    work = f.read_bytes()
                except OSError:
                    self._add(loc, "unscanned-unreadable", rel)
                else:
                    self.scan_bytes(loc, work, allow_loc=rel)
            # The commit is what is published, not the work tree. Where the work-tree copy
            # is not byte for byte the staged blob (edited, replaced, absent, or hidden from
            # git by an assume-unchanged or skip-worktree bit), scan the staged blob too.
            for mode, sha in staged:
                if _blob_id(work, len(sha)) != sha:
                    self._blob(root, f"index:{rel}", sha, mode, rel)
        # Likewise the HEAD blob of every path whose index entry differs from HEAD, which
        # includes a staged deletion.
        has_head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "-q", "--verify", "HEAD^{commit}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if has_head:
            recs = _zsplit(_git(root, "diff", "--cached", "--raw", "-z", "--no-renames",
                                "--no-abbrev", "HEAD"))
            for meta, rel in zip(recs[0::2], recs[1::2], strict=False):
                mode, _new_mode, sha = meta.lstrip(":").split(" ")[:3]
                if mode not in ("000000", "160000"):
                    self.scan_text(f"head:{rel}#name", rel, allow_loc=rel)
                    self._blob(root, f"head:{rel}", sha, mode, rel)

    def _blob(self, root: Path, loc: str, sha: str, mode: str, rel: str) -> None:
        data = _git(root, "cat-file", "blob", sha)
        self._count("staged or committed blob(s)")
        if mode == "120000":
            self.scan_text(loc + "#target", data.decode("utf-8", "replace"), allow_loc=rel)
        else:
            self.scan_bytes(loc, data, allow_loc=rel)

    def scan_dist(self, d: Path) -> None:
        """Every entry of the directory is either opened as an archive or reported."""
        archives = 0
        for art in sorted(d.iterdir()):
            loc = f"dist:{art.name}"
            self.scan_text(loc + "#name", art.name, allow_loc=art.name)
            if art.is_dir():
                self._add(loc, "unscanned-directory", art.name)
            elif art.name.endswith(_ZIP_SUFFIXES):
                self._zip(art)
                archives += 1
            elif art.name.endswith(_TAR_SUFFIXES):
                self._tar(art)
                archives += 1
            else:
                self._add(loc, "unscanned-unknown-artifact", art.name)
        if not archives:
            raise ScanError("--dist holds no sdist or wheel")

    def _zip(self, art: Path) -> None:
        with zipfile.ZipFile(art) as z:
            self.scan_text(f"dist:{art.name}#comment", z.comment.decode("utf-8", "replace"),
                           allow_loc=art.name)
            # infolist(), not namelist(): two members may share a name, and reading by name
            # returns only the last of them.
            for info in z.infolist():
                loc = f"dist:{art.name}:{info.filename}"
                self._count("archive member(s)")
                self.scan_text(loc + "#name", info.filename, allow_loc=info.filename)
                self.scan_text(loc + "#comment", info.comment.decode("utf-8", "replace"),
                               allow_loc=info.filename)
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    self._add(loc, "unscanned-encrypted", info.filename)
                elif info.file_size > MAX_BYTES:
                    self._add(loc, "unscanned-too-large", info.filename)
                else:
                    self.scan_bytes(loc, z.read(info), allow_loc=info.filename)

    def _tar(self, art: Path) -> None:
        with tarfile.open(art) as t:
            for m in t:
                # allowlist globs match the member path with the sdist top directory stripped
                rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
                loc = f"dist:{art.name}:{m.name}"
                self._count("archive member(s)")
                self.scan_text(loc + "#name", m.name, allow_loc=rel)
                # Header fields a build fills in from the machine it ran on.
                self.scan_text(f"dist:{art.name}#owner", f"{m.uname}\n{m.gname}",
                               allow_loc=art.name)
                self.scan_text(loc + "#header", "\n".join(
                    f"{k}={v}" for k, v in m.pax_headers.items() if k not in _PAX_SEEN), allow_loc=rel)
                if m.issym() or m.islnk():
                    self.scan_text(loc + "#target", m.linkname, allow_loc=rel)  # not followed
                elif m.isdir():
                    continue
                elif not m.isfile():
                    self._add(loc, "unscanned-special-member", rel)  # device, fifo, ...
                elif m.size > MAX_BYTES:
                    self._add(loc, "unscanned-too-large", rel)
                else:
                    fobj = t.extractfile(m)
                    self.scan_bytes(loc, fobj.read() if fobj else b"", allow_loc=rel)
            # tarfile stops quietly at a damaged header, so anything after the last member
            # it understood must be padding. Reading to the end also checks the gzip CRC.
            raw = t.fileobj
            if raw is None:
                raise ScanError("archive stream unavailable")
            raw.seek(t.offset)
            while chunk := raw.read(1 << 20):
                if chunk.strip(b"\0"):
                    self._add(f"dist:{art.name}", "unscanned-trailing-data", art.name)
                    break

    def scan_git_log(self, root: Path, rev: str) -> None:
        if _git(root, "rev-parse", "--is-shallow-repository").strip() == b"true":
            # History is cut off: commits beyond the boundary are not here to be scanned.
            self._add("git:history", "unscanned-shallow-clone")
        grafts = root / _git(root, "rev-parse", "--git-path", "info/grafts").decode().strip()
        if grafts.is_file() and grafts.stat().st_size:
            self._add("git:history", "unscanned-grafts")  # local parent rewrites hide commits
        out = _git(root, "log", rev, "-z", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B")
        commits = 0
        for rec in out.decode("utf-8", "replace").split("\0"):
            rec = rec.lstrip("\n")
            if not rec:
                continue
            sha, *rest = rec.split("\x1f", 5)
            commits += 1
            self._count("commit(s)")
            self.scan_text(f"git:{sha[:12]}", "\n".join(rest), allow_loc=f"git/{sha[:12]}")
        if not commits:
            raise ScanError("git log selected no commits: nothing would be scanned")
        self.scan_tags(root)

    def scan_tags(self, root: Path) -> None:
        """Every tag name, plus tagger name/email and message of every annotated tag.

        Tags are refs, not ancestors of a commit, so all of them are scanned whatever REV
        `--git-log` was given. Old file contents are still out of scope (see the docs).
        """
        out = _git(root, "for-each-ref", "refs/tags",
                   "--format=%(objectname) %(objecttype) %(refname:short)")
        for line in out.decode("utf-8", "replace").splitlines():
            sha, kind, name = line.split(" ", 2)
            self._count("tag(s)")
            self.scan_text(f"tag:{name}#name", name, allow_loc=f"tag/{name}")
            if kind == "tag":
                obj = _git(root, "cat-file", "-p", sha).decode("utf-8", "replace")
                self.scan_text(f"tag:{name}", obj, allow_loc=f"tag/{name}")
                kind = obj.split("\ntype ", 1)[-1].split("\n", 1)[0]
            # A lightweight tag is a name pointing straight at a commit: no object to read.
            if kind not in ("commit", "tag"):
                # A tag on a blob or tree publishes content that no commit scan reaches.
                self._add(f"tag:{name}", "unscanned-tag-target", f"tag/{name}")


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publication-safety scanner (see module docstring).",
                                 allow_abbrev=False)
    ap.add_argument("--root", default=".", help="repository root (default .)")
    ap.add_argument("--dist", help="directory of built artifacts (sdist, wheel) to scan; "
                                   "anything else found there is reported")
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
        if args.no_tracked and not args.dist and not args.git_log:
            raise ScanError("nothing to scan (--no-tracked without --dist or --git-log)")
        try:
            pats = load_patterns(environ, args.patterns_file)
            root = Path(args.root)
            allow = load_allowlist(args.allowlist if args.allowlist else
                                   (str(root / DEFAULT_ALLOWLIST)
                                    if (root / DEFAULT_ALLOWLIST).is_file() else None))
        except (OSError, UnicodeDecodeError):
            raise ScanError("cannot read patterns/allowlist file") from None
        if args.require_patterns and not pats:
            raise ScanError("no forbidden patterns supplied")
        sc = Scanner(pats, allow)
    except ScanError as e:
        print(f"check_publication: {e}", file=sys.stderr)
        return 2
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
    except ScanError as e:
        print(f"check_publication: scan could not run: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # any failure is "could not scan": never a pass, never a traceback
        # Only the exception type is printed: its message could carry scanned text.
        print("check_publication: scan could not run (git/dist unreadable): "
              f"{type(e).__name__}", file=sys.stderr)
        return 2
    for loc, rule in sorted(sc.findings):
        line = f"{_show(loc)}  {rule}"
        try:
            print(line)
        except UnicodeEncodeError:  # a stdout that cannot show the path: escape, never crash
            print(line.encode("ascii", "backslashreplace").decode("ascii"))
    scanned = ", ".join(f"{n} {what}" for what, n in sc.counts.items())
    print(f"check_publication: scanned {scanned}", file=sys.stderr)
    if sc.suppressed:
        print(f"check_publication: {len(sc.suppressed)} finding(s) suppressed by the allowlist",
              file=sys.stderr)
    if sc.findings:
        print(f"check_publication: FAIL ({len(sc.findings)} finding(s))", file=sys.stderr)
        return 1
    print("check_publication: ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
