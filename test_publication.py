#!/usr/bin/env python3
"""Tests for check_publication.py, with positive controls.

Every "must be clean" assertion is paired with a planted violation that the same
scanner, in the same mode, must flag; a scanner that finds nothing proves nothing
until it has been seen to find something. All sensitive-looking strings are
assembled at run time so this file never matches its own detectors. Stdlib only.
"""
from __future__ import annotations

import bz2
import codecs
import contextlib
import grp
import gzip
import importlib.util
import hashlib
import io
import lzma
import os
import pwd
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import unittest
import warnings
import zipfile
import zlib
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

import check_publication as cp

WORD = "zq" + "xjv"            # stand-in forbidden word (not a real name)
WORD2 = "plug" + "hxy"
KEY = "-----BEGIN " + "RSA PRIV" + "ATE KEY-----"
GHP = "gh" + "p_" + "A" * 36
AWS = "AK" + "IA" + "B" * 16
HOME = "/ho" + "me/" + "alice" + "/proj"


def run(args: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cp.main(args, env={} if env is None else env)
    return rc, out.getvalue(), err.getvalue()


# A temporary repository must never start a background `git gc`: a test that makes many
# commits can trigger `gc --auto`, which detaches and is still writing into
# .git/objects/pack while the temporary directory is being removed.
NO_GC = ("gc.auto=0", "gc.autoDetach=false", "maintenance.auto=false")


def git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def git_out(repo: Path, *a: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, input=stdin).stdout.strip()


def no_gc(repo: Path) -> None:
    for setting in NO_GC:
        git(repo, "config", *setting.split("=", 1))


def clone(cwd: Path, *a: str) -> None:
    """`git clone` whose result has background gc switched off from its first command."""
    git(cwd, "clone", "-q", *(x for setting in NO_GC for x in ("-c", setting)), *a)


def gz(raw: bytes) -> bytes:
    """gzip with the header a clean build has: no file name, no timestamp."""
    return gzip.compress(raw, mtime=0)


TarMember = tarfile.TarInfo | tuple[str, bytes] | tuple[tarfile.TarInfo, bytes]


def tar_bytes(members: Sequence[TarMember], comp: str = "",
              fmt: int = tarfile.PAX_FORMAT) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        for m in members:
            if isinstance(m, tarfile.TarInfo):
                t.addfile(m)
                continue
            ti = m[0] if isinstance(m[0], tarfile.TarInfo) else tarfile.TarInfo(m[0])
            ti.size = len(m[1])
            t.addfile(ti, io.BytesIO(m[1]))
    raw = buf.getvalue()
    if comp == "gz":
        return gz(raw)
    if comp == "bz2":
        return bz2.compress(raw)
    return lzma.compress(raw) if comp == "xz" else raw


def zip_bytes(members: Sequence[tuple[str | zipfile.ZipInfo, bytes]], comment: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")            # duplicate member names are deliberate
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.comment = comment
            for n, b in members:
                z.writestr(n, b)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        # ignore_cleanup_errors is the second line of defence only; NO_GC is the fix.
        self._t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._t.cleanup)
        self.repo = Path(self._t.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        no_gc(self.repo)
        git(self.repo, "config", "user.email", "dev@example.invalid")
        git(self.repo, "config", "user.name", "Dev")

    def commit(self, files: dict[str, str], msg: str = "init", author: str | None = None) -> None:
        for n, c in files.items():
            p = self.repo / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c, encoding="utf-8")
        git(self.repo, "add", "-A")
        extra = ["--author", author] if author else []
        git(self.repo, "commit", "-q", "-m", msg, *extra)

    def scan(self, *args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        return run(["--root", str(self.repo), *args], env)


class Tracked(Base):
    def test_clean_repo_passes(self) -> None:
        self.commit({"a.txt": "hello\n"})
        self.assertEqual(self.scan(env={cp.ENV_VAR: WORD})[0], 0)

    def test_forbidden_in_content_fires_and_never_prints_text(self) -> None:
        self.commit({"a.txt": f"x {WORD.upper()} y\n"})
        rc, out, err = self.scan(env={cp.ENV_VAR: f"{WORD2},{WORD}"})
        self.assertEqual(rc, 1)
        self.assertIn("tracked:a.txt  forbidden-2", out)
        self.assertNotIn(WORD.casefold(), (out + err).casefold())

    def test_forbidden_in_filename(self) -> None:
        self.commit({f"{WORD}.txt": "ok\n"})
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("forbidden-1", out)

    def test_untracked_file_is_not_scanned(self) -> None:
        self.commit({"a.txt": "ok\n"})
        (self.repo / "u.txt").write_text(WORD)
        self.assertEqual(self.scan(env={cp.ENV_VAR: WORD})[0], 0)

    def test_patterns_file(self) -> None:
        self.commit({"a.txt": WORD})
        pf = Path(self._t.name) / "pats"
        pf.write_text(f"# comment\n\n{WORD}\n")
        rc, out, _ = self.scan("--patterns-file", str(pf))
        self.assertEqual((rc, "forbidden-1" in out), (1, True))

    def test_generic_detectors_each_fire(self) -> None:
        for rule, sample in (("private-key", KEY), ("github-token", GHP),
                             ("aws-access-key", AWS), ("home-path", HOME)):
            with self.subTest(rule=rule):
                self.commit({"s.txt": f"v = {sample}\n"}, msg=rule)
                rc, out, err = self.scan()
                self.assertEqual(rc, 1)
                self.assertIn(f"tracked:s.txt  {rule}", out)
                self.assertNotIn(sample, out + err)
                self.commit({"s.txt": "clean\n"}, msg="fix")

    def test_placeholder_home_paths_are_not_flagged(self) -> None:
        self.commit({"s.txt": "/home/user/x /Users/you/y /home/runner/work\n"})
        self.assertEqual(self.scan()[0], 0)

    def test_allowlist_suppresses_only_matching_rule_and_path(self) -> None:
        self.commit({"a.txt": WORD, "b.txt": WORD, ".publication-allowlist":
                     "forbidden-1 a.txt   # reason\n"})
        rc, out, _ = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("tracked:b.txt  forbidden-1", out)
        self.assertNotIn("tracked:a.txt", out)

    def test_require_patterns_fails_closed(self) -> None:
        self.commit({"a.txt": "ok\n"})
        self.assertEqual(self.scan("--require-patterns")[0], 2)
        self.assertEqual(self.scan("--require-patterns", env={cp.ENV_VAR: WORD})[0], 0)

    def test_missing_patterns_file_is_error_not_pass(self) -> None:
        self.commit({"a.txt": "ok\n"})
        self.assertEqual(self.scan("--patterns-file", "/nonexistent/x")[0], 2)

    def test_patterns_file_bom_crlf_and_bad_encoding(self) -> None:
        self.commit({"a.txt": WORD})
        pf = Path(self._t.name) / "pats"
        pf.write_bytes(b"\xef\xbb\xbf" + WORD.encode() + b"\r\n")  # BOM + CRLF still one literal
        self.assertEqual(self.scan("--patterns-file", str(pf))[0], 1)
        pf.write_bytes(b"\xff\xfe" + WORD.encode())
        self.assertEqual(self.scan("--patterns-file", str(pf))[0], 2)  # unreadable: error, not pass

    def test_split_literal_invisible_and_fullwidth_forms_fire(self) -> None:
        half = len(WORD) // 2
        split = f'x = "{WORD[:half]}" + "{WORD[half:]}"\n'
        zw = WORD[:half] + "\u200b" + WORD[half:] + "\n"
        wide = "".join(chr(ord(c) + 0xFEE0) for c in WORD) + "\n"  # fullwidth letters
        for name, body, rule in (("split.py", split, "forbidden-1-split"),
                                 ("zw.txt", zw, "forbidden-1"), ("wide.txt", wide, "forbidden-1")):
            with self.subTest(name=name):
                self.commit({name: body}, msg="plant")
                rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
                self.assertEqual(rc, 1)
                self.assertIn(f"tracked:{name}  {rule}", out)
                self.assertNotIn(WORD, out + err)
                self.commit({name: "clean\n"}, msg="fix")

    def test_utf16_bom_content_is_decoded_and_fires(self) -> None:
        # Without the BOM-driven decode the NUL-interleaved bytes would never match.
        self.commit({"a.txt": "ok\n"})
        planted = {"le.txt": codecs.BOM_UTF16_LE + f"x {WORD} y\n".encode("utf-16-le"),
                   "be.txt": codecs.BOM_UTF16_BE + f"x {WORD} y\n".encode("utf-16-be"),
                   "clean.txt": codecs.BOM_UTF16_LE + "nothing here\n".encode("utf-16-le")}
        for name, body in planted.items():
            (self.repo / name).write_bytes(body)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "utf16")
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("tracked:le.txt  forbidden-1", out)
        self.assertIn("tracked:be.txt  forbidden-1", out)
        self.assertNotIn("tracked:clean.txt", out)  # a clean UTF-16 file is not a finding
        self.assertNotIn(WORD, out + err)

    def test_tracked_file_missing_from_worktree_is_reported(self) -> None:
        self.commit({"a.txt": "ok\n"})
        (self.repo / "a.txt").unlink()
        rc, out, _ = self.scan()
        self.assertEqual((rc, "tracked:a.txt  unscanned-missing" in out), (1, True))

    def test_git_log_all_refs_is_spellable(self) -> None:
        self.commit({"a": "1"})
        git(self.repo, "checkout", "-q", "-b", "side")
        self.commit({"b": "2"}, msg=f"side {WORD}")
        git(self.repo, "checkout", "-q", "-")
        self.assertEqual(self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})[0], 0)
        self.assertEqual(self.scan("--no-tracked", "--git-log=--all", env={cp.ENV_VAR: WORD})[0], 1)


class GitLog(Base):
    def test_clean_history_passes(self) -> None:
        self.commit({"a": "1"}, msg="fine")
        git(self.repo, "tag", "-a", "v1", "-m", "release one")   # annotated, clean
        git(self.repo, "tag", "v1-light")                          # lightweight, clean
        self.assertEqual(self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})[0], 0)

    def test_annotated_tag_message_tagger_and_name_fire(self) -> None:
        self.commit({"a": "1"}, msg="fine")
        git(self.repo, "tag", "-a", "v1", "-m", f"release mentions {WORD}")
        git(self.repo, "-c", f"user.email=z@{WORD}.example", "tag", "-a", "v2", "-m", "ok")
        git(self.repo, "tag", f"v3-{WORD}")  # lightweight: only the name can match
        rc, out, err = self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("tag:v1  forbidden-1", out)
        self.assertIn("tag:v2  forbidden-1", out)
        self.assertIn(f"tag:v3-{WORD}#name  forbidden-1", out)
        # Content lines never carry the matched text; a tag NAME is a location, like a path.
        self.assertNotIn(WORD, "".join(ln for ln in out.splitlines() if "#name" not in ln))
        self.assertNotIn(WORD, err)
        # Tags are history metadata: the tracked-only scan does not look at them.
        self.assertEqual(self.scan(env={cp.ENV_VAR: WORD})[0], 0)

    def test_tag_allowlist_uses_tag_slash_name(self) -> None:
        self.commit({"a": "1", ".publication-allowlist": "forbidden-1 tag/v1\n"}, msg="fine")
        git(self.repo, "tag", "-a", "v1", "-m", f"allowed {WORD}")
        git(self.repo, "tag", "-a", "v2", "-m", f"not allowed {WORD}")
        rc, out, _ = self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual((rc, "tag:v1" in out, "tag:v2  forbidden-1" in out), (1, False, True))

    def test_message_author_and_committer_fire(self) -> None:
        self.commit({"a": "1"}, msg=f"mentions {WORD}")
        self.commit({"a": "2"}, msg="ok", author=f"Z <z@{WORD}.example>")
        rc, out, err = self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("forbidden-1"), 2)  # two distinct commits
        self.assertNotIn(WORD, out + err)

    def test_deleted_secret_still_in_history_is_not_in_log_scan(self) -> None:
        # Documented limit: the log scan covers metadata only, not old blobs.
        self.commit({"a": WORD}, msg="add")
        self.commit({"a": "x"}, msg="remove")
        self.assertEqual(self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})[0], 0)


class Dist(Base):
    def make(self, tar_members: dict[str, str], whl_members: dict[str, str]) -> Path:
        d = Path(self._t.name) / "dist"
        d.mkdir()
        (d / "p-1.tar.gz").write_bytes(
            tar_bytes([(n, c.encode()) for n, c in tar_members.items()], "gz"))
        with zipfile.ZipFile(d / "p-1-py3-none-any.whl", "w") as z:
            for n, c in whl_members.items():
                z.writestr(n, c)
        return d

    def test_clean_artifacts_pass(self) -> None:
        d = self.make({"p-1/a.py": "ok"}, {"a.py": "ok"})
        self.commit({"a": "1"})
        self.assertEqual(self.scan("--no-tracked", "--dist", str(d), env={cp.ENV_VAR: WORD})[0], 0)

    def test_content_and_member_name_in_both_formats_fire(self) -> None:
        d = self.make({"p-1/a.py": WORD, f"p-1/{WORD}.txt": "ok"},
                      {"a.py": WORD, f"{WORD}.txt": "ok"})
        self.commit({"a": "1"})
        rc, out, err = self.scan("--no-tracked", "--dist", str(d), env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("forbidden-1"), 4)
        self.assertIn("dist:p-1.tar.gz:p-1/a.py", out)
        self.assertIn("dist:p-1-py3-none-any.whl:a.py", out)
        # Paths are printed by design (a member NAME that matches shows as path#name);
        # matched CONTENT never is. Check the content-only lines.
        self.assertNotIn(WORD, "".join(ln for ln in out.splitlines() if "#name" not in ln))

    def test_utf16_member_content_fires_in_both_formats(self) -> None:
        d = Path(self._t.name) / "dist"
        d.mkdir()
        body = codecs.BOM_UTF16_LE + f"v = {WORD}\n".encode("utf-16-le")
        (d / "p-1.tar.gz").write_bytes(tar_bytes([("p-1/u.txt", body)], "gz"))
        with zipfile.ZipFile(d / "p-1-py3-none-any.whl", "w") as z:
            z.writestr("u.txt", body)
        self.commit({"a": "1"})
        rc, out, err = self.scan("--no-tracked", "--dist", str(d), env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("dist:p-1.tar.gz:p-1/u.txt  forbidden-1", out)
        self.assertIn("dist:p-1-py3-none-any.whl:u.txt  forbidden-1", out)
        self.assertNotIn(WORD, out + err)

    def test_dist_allowlist_uses_path_without_sdist_top_dir(self) -> None:
        d = self.make({"p-1/a.py": WORD}, {"b.py": "ok"})
        self.commit({"a": "1", ".publication-allowlist": "forbidden-1 a.py\n"})
        self.assertEqual(self.scan("--no-tracked", "--dist", str(d), env={cp.ENV_VAR: WORD})[0], 0)

    def test_empty_dist_dir_is_error_not_pass(self) -> None:
        d = Path(self._t.name) / "empty"
        d.mkdir()
        self.commit({"a": "1"})
        self.assertEqual(self.scan("--no-tracked", "--dist", str(d))[0], 2)


class Surfaces(Base):
    """One planted violation plus a clean control for each surface not covered above."""

    def test_no_list_warns_and_runs_generic_detectors_only(self) -> None:
        self.commit({"a.txt": f"{WORD}\n"})
        rc, out, err = self.scan()                       # no list: the planted word is invisible
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("WARNING", err)
        self.assertIn("0 forbidden pattern(s)", err)
        self.commit({"a.txt": f"{GHP}\n"}, msg="token")  # ...but a generic shape still fires
        self.assertEqual(self.scan()[0], 1)

    def test_exit_codes_of_the_real_process(self) -> None:
        self.commit({"a.txt": f"{WORD}\n"})
        script = str(Path(cp.__file__).resolve())
        for extra, env_val, want in ((["--root", str(self.repo)], WORD2, 0),
                                     (["--root", str(self.repo)], WORD, 1),
                                     (["--root", str(self.repo), "--require-patterns"], "", 2),
                                     (["--root", str(self.repo / "nope")], WORD, 2)):
            with self.subTest(want=want, extra=extra[2:]):
                r = subprocess.run([sys.executable, script, *extra], capture_output=True,
                                   text=True, env={**os.environ, cp.ENV_VAR: env_val})
                self.assertEqual(r.returncode, want)
                self.assertNotIn(WORD, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_error_messages_never_echo_a_pattern(self) -> None:
        self.commit({"a.txt": "ok\n"})
        al = Path(self._t.name) / "al"
        al.write_text(f"forbidden-1 *  # {WORD}\n")
        for args, env in ((["--allowlist", str(al)], {cp.ENV_VAR: WORD}),
                          (["--no-tracked"], {cp.ENV_VAR: WORD}),
                          (["--root", str(self.repo / "nope")], {cp.ENV_VAR: WORD}),
                          (["--git-log=no-such-rev"], {cp.ENV_VAR: WORD}),
                          ([], {cp.ENV_VAR: f"{WORD},​"})):
            with self.subTest(args=args[:1]):
                rc, out, err = run(["--root", str(self.repo), *args], env)
                self.assertEqual(rc, 2)
                self.assertNotIn(WORD, out + err)

    def test_committer_and_trailer_fire_separately(self) -> None:
        self.commit({"a": "1"}, msg="clean")
        me = "Dev <dev@example.invalid>"
        git(self.repo, "-c", f"user.name={WORD}", "commit", "-q", "--allow-empty", "-m", "c",
            "--author", me)                                         # committer name only
        git(self.repo, "-c", f"user.email=x@{WORD}.example", "commit", "-q", "--allow-empty",
            "-m", "c", "--author", me)                              # committer email only
        git(self.repo, "commit", "-q", "--allow-empty", "--author", me,
            "-m", "subject", "-m", f"Co-authored-by: Q <q@{WORD}.example>")   # trailer only
        git(self.repo, "commit", "-q", "--allow-empty", "--author", f"{WORD} <a@b.example>",
            "-m", "c")                                              # author name only
        rc, out, err = self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(len(out.splitlines()), 4)   # four dirty commits; the clean one is silent
        self.assertNotIn(WORD, out + err)
        self.assertIn("5 commit(s)", err)

    def test_binary_content_plain_and_wide_fires(self) -> None:
        noise = bytes(range(1, 256)) * 3 + b"\xff\xfe\xfd"
        self.commit({"a.txt": "ok\n"})
        planted = {"plain.bin": noise + WORD.encode() + noise,
                   "wide.bin": noise + WORD.encode("utf-16-le") + noise,      # no byte-order mark
                   "wide32.bin": noise + WORD.encode("utf-32-be") + noise,
                   "clean.bin": noise + b"\x00" + noise}
        for name, body in planted.items():
            (self.repo / name).write_bytes(body)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "bin")
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out.splitlines()), [f"tracked:{n}  forbidden-1" for n in
                                                    ("plain.bin", "wide.bin", "wide32.bin")])
        self.assertNotIn(WORD, out + err)

    def test_non_ascii_term_in_any_normal_form(self) -> None:
        word = WORD + "é"                                   # composed e-acute
        nfd = unicodedata.normalize("NFD", word)
        self.assertNotEqual(word, nfd)
        self.commit({"nfc.txt": f"x {word} y\n", "nfd.txt": f"x {nfd} y\n", "clean.txt": "e\n"})
        pf = Path(self._t.name) / "pats"
        wide = "".join(chr(ord(c) + 0xFEE0) for c in WORD)       # pattern typed in fullwidth
        for label, pat in (("nfc", word), ("nfd", nfd), ("zero-width", word[:2] + "‍" + word[2:]),
                           ("upper", word.upper()), ("fullwidth", wide)):
            with self.subTest(pattern=label):
                pf.write_text(pat + "\n", encoding="utf-8")
                rc, out, err = self.scan("--patterns-file", str(pf))
                self.assertEqual(rc, 1)
                self.assertEqual(sorted(out.splitlines()),
                                 ["tracked:nfc.txt  forbidden-1", "tracked:nfd.txt  forbidden-1"])
                self.assertNotIn(word, out + err)

    def test_tracked_symlink_name_and_link_text_scanned_target_never_read(self) -> None:
        outside = Path(self._t.name) / "outside.txt"
        outside.write_text(f"{WORD}\n{GHP}\n")
        self.commit({"a.txt": "ok\n"})
        os.symlink("../outside.txt", self.repo / "link")     # points outside the repository
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "link")
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual((rc, out), (0, ""))                  # the target's content was not read
        os.symlink(f"../{WORD}/x", self.repo / "dangling")    # git stores this text: published
        os.symlink("a.txt", self.repo / f"{WORD}-link")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "links")
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out.splitlines()), ["tracked:dangling#target  forbidden-1",
                                                    f"tracked:{WORD}-link#name  forbidden-1"])
        self.assertNotIn(WORD, err)

    def test_file_swapped_for_a_symlink_is_not_followed_and_the_blob_is_scanned(self) -> None:
        outside = Path(self._t.name) / "outside"
        outside.mkdir()
        (outside / "dirty.txt").write_text(f"{GHP}\n")
        (outside / "clean.txt").write_text("ok\n")
        (outside / "b.txt").write_text(f"{GHP}\n")
        self.commit({"clean.txt": "ok\n", "dirty.txt": f"{WORD}\n", "d/b.txt": "ok\n"})
        # A clean tracked file now links to an outside file holding a token: must not be read.
        (self.repo / "clean.txt").unlink()
        os.symlink(outside / "dirty.txt", self.repo / "clean.txt")
        # A dirty tracked file now links to a clean outside file: the committed blob still counts.
        (self.repo / "dirty.txt").unlink()
        os.symlink(outside / "clean.txt", self.repo / "dirty.txt")
        # A parent directory replaced by a link out of the tree.
        (self.repo / "d" / "b.txt").unlink()
        (self.repo / "d").rmdir()
        os.symlink(outside, self.repo / "d")
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("index:dirty.txt  forbidden-1", out)
        self.assertIn("tracked:d/b.txt  unscanned-symlinked-path", out)
        self.assertNotIn("github-token", out)                 # nothing outside the tree was read
        self.assertNotIn(WORD, out + err)


class SilentSkips(Base):
    """Every way of reporting "ok" without having scanned what was claimed."""

    def test_root_that_is_not_a_work_tree_top_is_an_error(self) -> None:
        plain = Path(self._t.name) / "plain"
        plain.mkdir()
        self.assertEqual(run(["--root", str(plain)])[0], 2)             # not a repository
        self.assertEqual(run(["--root", str(plain), "--no-tracked", "--git-log"])[0], 2)
        self.assertEqual(self.scan()[0], 2)                             # nothing tracked yet
        self.commit({"sub/a.txt": "ok\n", "top.txt": f"{WORD}\n"})
        (self.repo / "inner").mkdir()                                   # untracked directory
        for sub in ("sub", "inner"):   # ls-files there would list a part of the tree, or nothing
            self.assertEqual(run(["--root", str(self.repo / sub)], {cp.ENV_VAR: WORD})[0], 2)
        self.assertEqual(self.scan(env={cp.ENV_VAR: WORD})[0], 1)       # control

    def test_nothing_to_scan_is_an_error(self) -> None:
        self.commit({"a.txt": "ok\n"})
        rc, _, err = self.scan("--no-tracked", env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 2)
        self.assertNotIn("ok", err.split("check_publication:")[-1])
        self.assertEqual(self.scan("--no-tracked", "--git-log=--max-count=0")[0], 2)
        rc, _, err = self.scan("--git-log")
        self.assertEqual(rc, 0)
        self.assertIn("scanned 1 tracked path(s), 1 commit(s)", err)   # a pass says what it covered

    def test_dirty_tree_committed_and_staged_content_is_scanned(self) -> None:
        self.commit({"a.txt": f"{WORD}\n", "b.txt": f"{WORD2}\n", "c.txt": "ok\n"})
        env = {cp.ENV_VAR: f"{WORD},{WORD2}"}
        (self.repo / "a.txt").write_text("clean now\n")       # fixed in the work tree only
        rc, out, _ = self.scan(env=env)
        self.assertIn("index:a.txt  forbidden-1", out)
        git(self.repo, "add", "a.txt")                         # fixed in the index, not committed
        rc, out, _ = self.scan(env=env)
        self.assertIn("head:a.txt  forbidden-1", out)
        self.assertNotIn("index:a.txt", out)
        git(self.repo, "rm", "-q", "--cached", "b.txt")       # staged deletion: HEAD still has it
        (self.repo / "b.txt").unlink()
        rc, out, err = self.scan(env=env)
        self.assertEqual(sorted(out.splitlines()),
                         ["head:a.txt  forbidden-1", "head:b.txt  forbidden-2"])
        self.assertNotIn(WORD, out + err)
        git(self.repo, "commit", "-q", "-m", "fix")
        self.assertEqual(self.scan(env=env)[0:2], (0, ""))

    def test_change_hidden_from_git_does_not_hide_the_staged_blob(self) -> None:
        self.commit({"a.txt": f"{WORD}\n", "b.txt": f"{WORD}\n", "c.txt": "ok\n"})
        git(self.repo, "update-index", "--assume-unchanged", "a.txt")
        git(self.repo, "update-index", "--skip-worktree", "b.txt")
        (self.repo / "a.txt").write_text("clean now\n")       # git diff no longer reports these
        (self.repo / "b.txt").write_text("clean now\n")
        self.assertEqual(git_out(self.repo, "status", "--porcelain"), "")
        rc, out, err = self.scan(env={cp.ENV_VAR: WORD})
        self.assertEqual(sorted(out.splitlines()),
                         ["index:a.txt  forbidden-1", "index:b.txt  forbidden-1"])
        self.assertNotIn(WORD, err)

    @unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")
    def test_unreadable_tracked_file_is_reported(self) -> None:
        self.commit({"a.txt": f"{WORD}\n", "b.txt": "ok\n"})
        os.chmod(self.repo / "a.txt", 0)
        self.addCleanup(os.chmod, self.repo / "a.txt", 0o600)
        rc, out, _ = self.scan()
        self.assertEqual((rc, out.strip()), (1, "tracked:a.txt  unscanned-unreadable"))

    def test_gitlink_and_directory_entries_are_reported(self) -> None:
        self.commit({"a.txt": "ok\n", "f.txt": "ok\n"})
        sha = git_out(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
        git(self.repo, "commit", "-q", "-m", "gitlink")
        (self.repo / "f.txt").unlink()
        (self.repo / "f.txt").mkdir()
        rc, out, _ = self.scan()
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out.splitlines()), ["tracked:f.txt  unscanned-not-a-file",
                                                    "tracked:sub  unscanned-submodule"])

    def test_odd_filenames_are_scanned_and_reported_on_one_line(self) -> None:
        self.commit({"a.txt": "ok\n"})
        names = [f"nl\n{WORD}.txt".encode(), b"tab\tx.txt", b"bad\xff" + WORD.encode() + b".txt"]
        made = []
        for raw in names:
            try:
                with open(os.path.join(os.fsencode(self.repo), raw), "wb") as fh:
                    fh.write(f"{WORD2}\n".encode())
                made.append(raw)
            except OSError:
                pass   # this filesystem refuses the name (non-UTF-8 on APFS)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "names")
        rc, out, err = self.scan(env={cp.ENV_VAR: f"{WORD},{WORD2}"})
        self.assertEqual(rc, 1)
        lines = out.splitlines()
        # a name hit for each planted name, a content hit for every file, one line each
        self.assertEqual(len([ln for ln in lines if ln.endswith("#name  forbidden-1")]),
                         len([r for r in made if WORD.encode() in r]))
        self.assertEqual(len([ln for ln in lines if ln.endswith("  forbidden-2")]), len(made))
        self.assertEqual(len(lines), len(made) + len([r for r in made if WORD.encode() in r]))
        self.assertTrue(all(ln.startswith("tracked:") and ln.isprintable() for ln in lines))

    def test_oversize_file_and_members_are_reported_without_reading(self) -> None:
        self.commit({"big.txt": "x" * 200 + WORD, "ok.txt": "ok\n"})
        d = Path(self._t.name) / "dist"
        d.mkdir()
        body = b"y" * 200
        (d / "p-1.tar.gz").write_bytes(tar_bytes([("p-1/big.txt", body)], "gz"))
        with zipfile.ZipFile(d / "p-1-py3-none-any.whl", "w") as z:
            z.writestr("big.txt", body)
        with mock.patch.object(cp, "MAX_BYTES", 100):
            rc, out, _ = self.scan("--dist", str(d), env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out.splitlines()),
                         ["dist:p-1-py3-none-any.whl:big.txt  unscanned-too-large",
                          "dist:p-1.tar.gz:p-1/big.txt  unscanned-too-large",
                          "tracked:big.txt  unscanned-too-large"])

    def test_shallow_clone_is_reported(self) -> None:
        self.commit({"a": "1"}, msg=f"old {WORD}")
        self.commit({"a": "2"}, msg="new")
        tmp = Path(self._t.name)
        clone(tmp, "--depth", "1", self.repo.as_uri(), "shallow")
        clone(tmp, self.repo.as_uri(), "full")
        rc, out, _ = run(["--root", str(tmp / "shallow"), "--git-log"], {cp.ENV_VAR: WORD})
        self.assertEqual((rc, out.strip()), (1, "git:history  unscanned-shallow-clone"))
        rc, out, _ = run(["--root", str(tmp / "full"), "--git-log"], {cp.ENV_VAR: WORD})
        self.assertEqual((rc, out.count("forbidden-1")), (1, 1))     # full history: found

    def test_replace_refs_and_grafts_do_not_hide_a_commit(self) -> None:
        self.commit({"a": "1"}, msg=f"mentions {WORD}")
        dirty = git_out(self.repo, "rev-parse", "HEAD")
        clean = git_out(self.repo, "commit-tree", "HEAD^{tree}", "-m", "clean")
        git(self.repo, "replace", dirty, clean)
        self.assertNotIn(WORD, git_out(self.repo, "log", "--format=%B"))   # git itself is fooled
        rc, out, _ = self.scan("--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual((rc, out.strip()), (1, f"git:{dirty[:12]}  forbidden-1"))
        git(self.repo, "replace", "-d", dirty)
        self.commit({"a": "2"}, msg="second")
        info = Path(git_out(self.repo, "rev-parse", "--absolute-git-dir")) / "info"
        info.mkdir(exist_ok=True)
        (info / "grafts").write_text(git_out(self.repo, "rev-parse", "HEAD") + "\n")
        rc, out, _ = self.scan("--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertIn("git:history  unscanned-grafts", out)

    def test_tag_on_a_blob_is_reported(self) -> None:
        self.commit({"a": "1"})
        blob = git_out(self.repo, "hash-object", "-w", "--stdin", stdin=f"{WORD}\n")
        git(self.repo, "tag", "light-blob", blob)
        git(self.repo, "tag", "-a", "ann-blob", "-m", "x", blob)
        git(self.repo, "tag", "-a", "fine", "-m", "x")
        rc, out, _ = self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out.splitlines()), ["tag:ann-blob  unscanned-tag-target",
                                                    "tag:light-blob  unscanned-tag-target"])

    def test_pattern_lists_that_cannot_match_are_errors(self) -> None:
        self.commit({"a.txt": f"{WORD}\n"})
        pf = Path(self._t.name) / "pats"
        # An empty environment list is "no list": warn, generic detectors only, or exit 2.
        for empty in ("", " ", ",", " , ,\t, "):
            with self.subTest(env=empty):
                rc, _, err = self.scan(env={cp.ENV_VAR: empty})
                self.assertEqual((rc, "WARNING" in err), (0, True))
                self.assertEqual(self.scan("--require-patterns", env={cp.ENV_VAR: empty})[0], 2)
        # A patterns file was named but holds nothing: an error even without --require-patterns.
        for body in ("", "\n\n", "   \n\t\n", "# only a comment\n"):
            with self.subTest(file=body):
                pf.write_text(body)
                self.assertEqual(self.scan("--patterns-file", str(pf))[0], 2)
        # A pattern of invisible characters only would be counted but could never match.
        for dead in ("​", "​‍", "﻿­"):
            with self.subTest(dead=ascii(dead)):
                self.assertEqual(self.scan(env={cp.ENV_VAR: dead})[0], 2)
                self.assertEqual(self.scan(env={cp.ENV_VAR: f"{WORD},{dead}"})[0], 2)

    def test_long_patterns_and_commas(self) -> None:
        long = (WORD + "k") * 2000                                   # 12 000 characters
        self.commit({"long.txt": f"a {long} b\n", "short.txt": f"{long[:-1]}\n",
                     "pair.txt": f"{WORD},{WORD2}\n", "one.txt": f"{WORD2}\n"})
        rc, out, err = self.scan(env={cp.ENV_VAR: long})
        self.assertEqual((rc, out.strip()), (1, "tracked:long.txt  forbidden-1"))
        self.assertNotIn(WORD, out + err)
        # The environment list splits on commas, so "a,b" there is two patterns...
        rc, out, _ = self.scan(env={cp.ENV_VAR: f"{WORD},{WORD2}"})
        self.assertIn("tracked:one.txt  forbidden-2", out)
        self.assertIn("tracked:pair.txt  forbidden-1", out)
        # ...and only a patterns file can hold a literal that contains one.
        pf = Path(self._t.name) / "pats"
        pf.write_text(f"{WORD},{WORD2}\n")
        rc, out, _ = self.scan("--patterns-file", str(pf))
        self.assertEqual((rc, out.strip()), (1, "tracked:pair.txt  forbidden-1"))

    def test_allowlist_cannot_match_everything_and_suppression_is_counted(self) -> None:
        self.commit({"a.txt": WORD, "b.txt": WORD})
        al = Path(self._t.name) / "al"
        for glob in ("*", "**", "***"):
            al.write_text(f"forbidden-1 {glob}\n")
            self.assertEqual(self.scan("--allowlist", str(al), env={cp.ENV_VAR: WORD})[0], 2)
        al.write_text("forbidden-1 a.txt\nforbidden-1 b.txt\n")
        rc, out, err = self.scan("--allowlist", str(al), env={cp.ENV_VAR: WORD})
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("2 finding(s) suppressed by the allowlist", err)
        al.write_text("")
        rc, _, err = self.scan("--allowlist", str(al), env={cp.ENV_VAR: WORD})
        self.assertEqual((rc, "suppressed" in err), (1, False))


class Checksums(Base):
    """A SHA256SUMS file beside the artifacts is read and checked, never passed unread."""

    def setUp(self) -> None:
        super().setUp()
        self.commit({"a": "1"})
        self.d = Path(self._t.name) / "dist"
        self.d.mkdir()
        self.whl = self.d / "p-1-py3-none-any.whl"
        with zipfile.ZipFile(self.whl, "w") as z:
            z.writestr("a.py", "ok")
        self.tgz = self.d / "p-1.tar.gz"
        self.tgz.write_bytes(tar_bytes([("p-1/a.py", b"ok")], "gz"))

    def line(self, p: Path) -> str:
        return f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"

    def sums(self, text: str, word: str = WORD) -> tuple[int, list[str], str]:
        (self.d / "SHA256SUMS").write_text(text, encoding="utf-8")
        rc, out, err = self.scan("--no-tracked", "--dist", str(self.d), env={cp.ENV_VAR: word})
        return rc, sorted(out.splitlines()), err

    def test_matching_checksum_file_passes_and_is_counted(self) -> None:
        rc, lines, err = self.sums(self.line(self.whl) + self.line(self.tgz))
        self.assertEqual((rc, lines), (0, []), err)
        self.assertIn("1 checksum file(s)", err)

    def test_wrong_hash_unlisted_artifact_and_bad_lines_are_findings(self) -> None:
        good = self.line(self.whl) + self.line(self.tgz)
        for text, rule in ((good.replace(good[:4], "0000" if good[:4] != "0000" else "1111", 1),
                            "checksum-mismatch"),
                           (self.line(self.whl), "checksum-unlisted"),
                           (good + "not a checksum line\n", "checksum-malformed"),
                           (good + self.line(self.whl), "checksum-malformed"),          # listed twice
                           (good + "0" * 64 + "  ../outside\n", "checksum-malformed"),  # not a bare name
                           (good + "0" * 64 + "  absent.whl\n", "checksum-malformed")):  # no such file
            with self.subTest(rule=rule, text=text[-30:]):
                rc, lines, err = self.sums(text)
                self.assertEqual((rc, lines), (1, [f"dist:SHA256SUMS  {rule}"]), err)

    def test_forbidden_word_in_the_checksum_file_fires_without_being_printed(self) -> None:
        rc, lines, err = self.sums(self.line(self.whl) + self.line(self.tgz) + f"# {WORD}\n")
        self.assertEqual(rc, 1)
        self.assertIn("dist:SHA256SUMS  forbidden-1", lines)
        self.assertNotIn(WORD, "\n".join(lines) + err)

    def test_other_stray_files_are_still_reported(self) -> None:
        (self.d / "SHA512SUMS").write_text("x")
        rc, lines, _ = self.sums(self.line(self.whl) + self.line(self.tgz))
        self.assertEqual((rc, lines), (1, ["dist:SHA512SUMS  unscanned-unknown-artifact"]))


class DistSkips(Base):
    def dist(self, files: dict[str, bytes]) -> Path:
        d = Path(self._t.name) / "dist"
        d.mkdir(exist_ok=True)
        for n, b in files.items():
            (d / n).write_bytes(b)
        if not (self.repo / "a").exists():
            self.commit({"a": "1"})
        return d

    def dscan(self, d: Path, word: str = WORD) -> tuple[int, list[str], str]:
        rc, out, err = self.scan("--no-tracked", "--dist", str(d), env={cp.ENV_VAR: word})
        self.assertNotIn(word, "".join(ln for ln in out.splitlines() if "#name" not in ln) + err)
        return rc, sorted(out.splitlines()), err

    def test_every_file_in_the_directory_is_opened_or_reported(self) -> None:
        w = WORD.encode()
        d = self.dist({"p-1-py3-none-any.whl": zip_bytes([("a.py", b"ok")]),
                       "p-1.zip": zip_bytes([("p-1/a.py", w)]),
                       "p-1.egg": zip_bytes([("a.py", w)]),
                       "p-1.tar.bz2": tar_bytes([("p-1/a.py", w)], "bz2"),
                       "p-1.tar.xz": tar_bytes([("p-1/a.py", w)], "xz"),
                       "p-1.tar": tar_bytes([("p-1/a.py", w)]),
                       "p-1.7z": b"7z\xbc\xaf\x27\x1c" + w,
                       "notes.txt": w})
        (d / "sub").mkdir()
        (d / "sub" / "p-2.whl").write_bytes(zip_bytes([("a.py", w)]))
        rc, lines, err = self.dscan(d)
        self.assertEqual(rc, 1)
        self.assertEqual(lines, ["dist:notes.txt  unscanned-unknown-artifact",
                                 "dist:p-1.7z  unscanned-unknown-artifact",
                                 "dist:p-1.egg:a.py  forbidden-1",
                                 "dist:p-1.tar.bz2:p-1/a.py  forbidden-1",
                                 "dist:p-1.tar.xz:p-1/a.py  forbidden-1",
                                 "dist:p-1.tar:p-1/a.py  forbidden-1",
                                 "dist:p-1.zip:p-1/a.py  forbidden-1",
                                 "dist:sub  unscanned-directory"])
        self.assertIn("6 archive member(s)", err)

    def test_directory_without_a_known_archive_is_an_error(self) -> None:
        d = self.dist({"notes.txt": b"ok"})
        self.assertEqual(self.dscan(d)[0], 2)
        self.assertEqual(self.scan("--no-tracked", "--dist", str(d / "missing"))[0], 2)

    def test_artifact_file_name_is_scanned(self) -> None:
        d = self.dist({f"{WORD}-1-py3-none-any.whl": zip_bytes([("a.py", b"ok")])})
        rc, lines, _ = self.dscan(d)
        self.assertEqual((rc, lines), (1, [f"dist:{WORD}-1-py3-none-any.whl#name  forbidden-1"]))

    def test_corrupt_and_truncated_archives_are_errors(self) -> None:
        good_tgz = tar_bytes([("p-1/a.py", os.urandom(4096))], "gz")
        good_whl = zip_bytes([("a.py", b"ok" * 500)])
        flipped = bytearray(good_whl)
        flipped[40] ^= 0xFF                                   # inside the compressed data
        for name, body in (("p-1.tar.gz", b"not a tarball at all"),
                           ("p-1.tar.gz", good_tgz[:len(good_tgz) // 2]),
                           ("p-1.tar.gz", good_tgz[:-6]),     # gzip trailer cut: CRC unverifiable
                           ("p-1.whl", b"not a zip"),
                           ("p-1.whl", good_whl[:-30]),
                           ("p-1.whl", bytes(flipped))):
            with self.subTest(name=name, size=len(body)):
                d = self.dist({name: body})
                rc, lines, err = self.dscan(d)
                self.assertEqual((rc, lines), (2, []))
                self.assertNotIn("Traceback", err)
                (d / name).unlink()
        self.assertEqual(self.dscan(self.dist({"p-1.tar.gz": good_tgz, "p-1.whl": good_whl}))[0], 0)

    def test_members_after_a_damaged_tar_header_are_not_lost(self) -> None:
        raw = bytearray(tar_bytes([("p-1/a.py", b"ok"), ("p-1/b.py", WORD.encode())]))
        clean = gz(bytes(raw))
        raw[1024 + 148:1024 + 156] = b"zzzzzzzz"             # second header: checksum destroyed
        with tarfile.open(fileobj=io.BytesIO(bytes(raw))) as t:
            self.assertEqual(t.getnames(), ["p-1/a.py"])      # tarfile stops there without error
        d = self.dist({"p-1.tar.gz": gz(bytes(raw))})
        rc, lines, _ = self.dscan(d)
        self.assertEqual((rc, lines), (1, ["dist:p-1.tar.gz  unscanned-trailing-data"]))
        self.assertEqual(self.dscan(self.dist({"p-1.tar.gz": clean}))[1],
                         ["dist:p-1.tar.gz:p-1/b.py  forbidden-1"])

    def test_tar_links_devices_owner_and_headers(self) -> None:
        def info(name: str, kind: bytes, link: str = "") -> tarfile.TarInfo:
            ti = tarfile.TarInfo(name)
            ti.type, ti.linkname = kind, link
            return ti
        owner = info("p-1/owned.py", tarfile.REGTYPE)
        owner.uname = WORD                # the builder's login name: also builder-owner
        pax = info("p-1/pax.py", tarfile.REGTYPE)
        pax.pax_headers = {"comment": f"built by {WORD}"}
        d = self.dist({"p-1.tar.gz": tar_bytes([
            ("p-1/a.py", b"ok"),
            info("p-1/sym", tarfile.SYMTYPE, f"/srv/{WORD}/x"),
            info("p-1/hard", tarfile.LNKTYPE, f"p-1/{WORD}"),
            info("p-1/dev", tarfile.CHRTYPE), info("p-1/fifo", tarfile.FIFOTYPE),
            info("p-1/dir", tarfile.DIRTYPE), owner, pax], "gz")})
        rc, lines, _ = self.dscan(d)
        self.assertEqual(rc, 1)
        self.assertEqual(lines, ["dist:p-1.tar.gz#owner  builder-owner",
                                 "dist:p-1.tar.gz#owner  forbidden-1",
                                 "dist:p-1.tar.gz:p-1/dev  unscanned-special-member",
                                 "dist:p-1.tar.gz:p-1/fifo  unscanned-special-member",
                                 "dist:p-1.tar.gz:p-1/hard#target  forbidden-1",
                                 "dist:p-1.tar.gz:p-1/pax.py#header  forbidden-1",
                                 "dist:p-1.tar.gz:p-1/sym#target  forbidden-1"])
        ok = self.dist({"p-1.tar.gz": tar_bytes([
            ("p-1/a.py", b"ok"), info("p-1/sym", tarfile.SYMTYPE, "a.py"),
            info("p-1/hard", tarfile.LNKTYPE, "p-1/a.py"), info("p-1/dir", tarfile.DIRTYPE)],
            "gz")})
        self.assertEqual(self.dscan(ok)[0:2], (0, []))

    def test_zip_duplicate_names_comments_and_encrypted_members(self) -> None:
        w = WORD.encode()
        dup = zip_bytes([("a.py", w), ("a.py", b"ok")])       # by name, only the last is read
        note = zip_bytes([("a.py", b"ok")], comment=b"packed at " + w)
        enc = bytearray(zip_bytes([("a.py", b"ok")]))
        enc[6] |= 1                                            # local header: encrypted flag
        enc[enc.index(b"PK\x01\x02") + 8] |= 1                 # central directory: the same
        for label, body, want in (
                ("duplicate", dup, ["dist:p-1.whl:a.py  forbidden-1"]),
                ("comment", note, ["dist:p-1.whl#comment  builder-zip-comment",
                                   "dist:p-1.whl#comment  forbidden-1"]),
                ("encrypted", bytes(enc), ["dist:p-1.whl:a.py  unscanned-encrypted"])):
            with self.subTest(case=label):
                rc, lines, _ = self.dscan(self.dist({"p-1.whl": body}))
                self.assertEqual((rc, lines), (1, want))

    def test_nested_archives_are_reported_not_passed(self) -> None:
        w = WORD.encode() * 40
        inner_zip, inner_tgz = zip_bytes([("x.py", w)]), tar_bytes([("x.py", w)], "gz")
        self.assertNotIn(WORD.encode(), inner_zip + inner_tgz)   # compressed: a text scan is blind
        d = self.dist({"p-1.tar.gz": tar_bytes([("p-1/data/inner.zip", inner_zip),
                                                 ("p-1/data/inner.tar", tar_bytes([("x", b"ok")])),
                                                 ("p-1/a.py", b"ok")], "gz"),
                       "p-1.whl": zip_bytes([("data/inner.tgz", inner_tgz), ("a.py", b"ok")])})
        (self.repo / "bundle.zip").write_bytes(inner_zip)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "bundle")
        rc, out, _ = self.scan("--dist", str(d), env={cp.ENV_VAR: WORD})
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(out.splitlines()),
                         ["dist:p-1.tar.gz:p-1/data/inner.tar  unscanned-archive",
                          "dist:p-1.tar.gz:p-1/data/inner.zip  unscanned-archive",
                          "dist:p-1.whl:data/inner.tgz  unscanned-archive",
                          "tracked:bundle.zip  unscanned-archive"])


BUILDER_NAME = "builder" + "zq"            # invented account and group names
BUILDER_GROUP = "staff" + "zq"
BUILDER_UID = 54321
BUILDER_HOST = "buildhost" + "zq"


def owned(name: str, body: bytes = b"ok\n", **fields: object) -> tuple[tarfile.TarInfo, bytes]:
    """A tar member with the given header fields set (uname, uid, pax_headers, ...)."""
    ti = tarfile.TarInfo(name)
    for k, v in fields.items():
        setattr(ti, k, v)
    return ti, body


def zinfo(name: str, mode: int = 0o100644, system: int = 3, dos: int = 0, extra: bytes = b"",
          comment: bytes = b"") -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    zi.create_system, zi.external_attr, zi.extra, zi.comment = system, mode << 16 | dos, extra, comment
    return zi


def extra_field(field: int, payload: bytes) -> bytes:
    return struct.pack("<HH", field, len(payload)) + payload


def gzip_stream(raw: bytes, flags: int = 0, mtime: int = 0, name: bytes = b"",
                comment: bytes = b"", extra: bytes = b"") -> bytes:
    """One gzip member with a hand-written header, so every header field can be planted."""
    head = b"\x1f\x8b\x08" + bytes([flags]) + struct.pack("<I", mtime) + b"\x02\xff"
    if flags & 0x04:
        head += struct.pack("<H", len(extra)) + extra
    if flags & 0x08:
        head += name + b"\0"
    if flags & 0x10:
        head += comment + b"\0"
    if flags & 0x02:
        head += struct.pack("<H", zlib.crc32(head) & 0xFFFF)
    deflate = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    return (head + deflate.compress(raw) + deflate.flush()
            + struct.pack("<II", zlib.crc32(raw), len(raw) & 0xFFFFFFFF))


class BuilderIdentity(DistSkips):
    """An artifact must not say who built it, whatever the forbidden list holds.

    Each rule is planted in an in-memory archive and paired with a clean control. The
    planted values are invented, and none of them may appear in the output.
    """

    PLANTED = (BUILDER_NAME, BUILDER_GROUP, str(BUILDER_UID), BUILDER_HOST)

    def bscan(self, files: dict[str, bytes], *extra: str,
              env: dict[str, str] | None = None) -> tuple[int, list[str], str]:
        d = self.dist(files)
        rc, out, err = self.scan("--no-tracked", "--dist", str(d), *extra, env=env)
        for name in files:
            (d / name).unlink()
        for value in self.PLANTED:
            self.assertNotIn(value, out + err)
        return rc, sorted(out.splitlines()), err

    def fires(self, files: dict[str, bytes], want: list[str]) -> None:
        rc, lines, err = self.bscan(files)                    # no forbidden list at all
        self.assertEqual((rc, lines), (1, sorted(want)))
        self.assertIn("rebuild with scripts/repro_build.py", err)

    def clean(self, files: dict[str, bytes]) -> None:
        rc, lines, err = self.bscan(files)
        self.assertEqual((rc, lines), (0, []))
        self.assertNotIn("builder-", err)

    # ---- tar: owner names, ids, pax records ----------------------------------
    def test_tar_owner_names(self) -> None:
        want = ["dist:p-1.tar.gz#owner  builder-owner"]
        for label, fields in (("user", {"uname": BUILDER_NAME}),
                              ("group", {"gname": BUILDER_GROUP}),
                              ("both", {"uname": BUILDER_NAME, "gname": BUILDER_GROUP}),
                              ("system group", {"uname": "root", "gname": "wheel"}),
                              ("case", {"uname": "Root"})):
            with self.subTest(case=label):
                self.fires({"p-1.tar.gz": tar_bytes(
                    [("p-1/a.py", b"ok"), owned("p-1/b.py", b"ok", **fields)], "gz",
                    tarfile.USTAR_FORMAT)}, want)
        for label, fields in (("empty", {}), ("root", {"uname": "root", "gname": "root"})):
            with self.subTest(control=label):
                self.clean({"p-1.tar.gz": tar_bytes([owned("p-1/b.py", b"ok", **fields)], "gz")})

    def test_tar_owner_name_encodings(self) -> None:
        latin = (BUILDER_NAME + "é").encode("latin-1").decode("utf-8", "surrogateescape")
        for label, name, fmt, want in (
                ("non-ASCII, ustar field", BUILDER_NAME + "é", tarfile.USTAR_FORMAT,
                 ["dist:p-1.tar#owner  builder-owner"]),
                ("not UTF-8, gnu field", latin, tarfile.GNU_FORMAT,
                 ["dist:p-1.tar#owner  builder-owner"]),
                # A pax archive moves a non-ASCII name into a pax record: both are refused.
                ("non-ASCII, pax record", BUILDER_NAME + "é", tarfile.PAX_FORMAT,
                 ["dist:p-1.tar#header  builder-pax", "dist:p-1.tar#owner  builder-owner"])):
            with self.subTest(case=label):
                self.fires({"p-1.tar": tar_bytes([owned("p-1/a.py", uname=name)], "", fmt)}, want)

    def test_tar_uid_and_gid(self) -> None:
        for label, fields, fmt in (("uid", {"uid": BUILDER_UID}, tarfile.USTAR_FORMAT),
                                   ("gid", {"gid": 20}, tarfile.USTAR_FORMAT),
                                   ("both", {"uid": 501, "gid": 20}, tarfile.PAX_FORMAT),
                                   ("large, base-256", {"uid": 3_000_000}, tarfile.GNU_FORMAT)):
            with self.subTest(case=label):
                self.fires({"p-1.tar.gz": tar_bytes(
                    [("p-1/a.py", b"ok"), owned("p-1/b.py", b"ok", **fields)], "gz", fmt)},
                    ["dist:p-1.tar.gz#owner  builder-uid"])
        self.fires({"p-1.tar.gz": tar_bytes([owned("p-1/b.py", uid=3_000_000)], "gz")},
                   ["dist:p-1.tar.gz#header  builder-pax",    # too large for ustar: a pax record
                    "dist:p-1.tar.gz#owner  builder-uid"])
        self.clean({"p-1.tar.gz": tar_bytes([owned("p-1/b.py", uid=0, gid=0)], "gz")})

    def test_tar_pax_records(self) -> None:
        want = ["dist:p-1.tar.gz#header  builder-pax"]
        for key in ("atime", "ctime", "SCHILY.xattr.com.apple.quarantine", "SCHILY.dev",
                    "LIBARCHIVE.creationtime", "LIBARCHIVE.xattr.com.apple.provenance",
                    "GNU.sparse.major", "com.apple.metadata", "vendorzq.note"):
            with self.subTest(key=key):
                self.fires({"p-1.tar.gz": tar_bytes(
                    [owned("p-1/a.py", pax_headers={key: "1700000000.5"})], "gz")}, want)
        for key in ("uname", "gname"):                        # tarfile applies them to the member
            with self.subTest(key=key):
                self.fires({"p-1.tar.gz": tar_bytes(
                    [owned("p-1/a.py", pax_headers={key: BUILDER_NAME})], "gz")},
                    [*want, "dist:p-1.tar.gz#owner  builder-owner"])
        for key in ("uid", "gid"):
            with self.subTest(key=key):
                self.fires({"p-1.tar.gz": tar_bytes(
                    [owned("p-1/a.py", pax_headers={key: str(BUILDER_UID)})], "gz")},
                    [*want, "dist:p-1.tar.gz#owner  builder-uid"])
        long_name = "p-1/" + "d" * 120 + "/ä.py"          # path record, non-ASCII
        self.clean({"p-1.tar.gz": tar_bytes([
            owned(long_name, mtime=1700000000.25),            # mtime record
            owned("p-1/b.py", pax_headers={"comment": "0" * 40, "hdrcharset": "BINARY"}),
            owned("p-1/dir", b"", type=tarfile.DIRTYPE)], "gz")})

    # ---- gzip header -------------------------------------------------------------
    def test_gzip_header(self) -> None:
        raw = tar_bytes([("p-1/a.py", b"ok")])
        named = io.BytesIO()
        with gzip.GzipFile(BUILDER_HOST + ".tar", "wb", fileobj=named, mtime=0) as g:
            g.write(raw)                                      # what `tarfile.open(.., "w:gz")` does
        half = len(raw) // 2
        want = ["dist:p-1.tar.gz#gzip  builder-gzip-header"]
        for label, body in (
                ("file name", gzip_stream(raw, 0x08, name=BUILDER_HOST.encode() + b".tar")),
                ("file name, stdlib writer", named.getvalue()),
                ("comment", gzip_stream(raw, 0x10, comment=b"built on " + BUILDER_HOST.encode())),
                ("extra field", gzip_stream(raw, 0x04, extra=BUILDER_NAME.encode())),
                ("timestamp", gzip_stream(raw, mtime=1700000000)),
                ("timestamp, stdlib writer", gzip.compress(raw, mtime=1700000000)),
                ("reserved flag", gzip_stream(raw, 0x20)),
                ("second member of the stream",
                 gz(raw[:half]) + gzip_stream(raw[half:], 0x08, name=BUILDER_HOST.encode()))):
            with self.subTest(case=label):
                self.fires({"p-1.tar.gz": body}, want)
        self.fires({"p-1.tgz": gzip_stream(raw, mtime=1)}, ["dist:p-1.tgz#gzip  builder-gzip-header"])
        # a gzip stream under a plain .tar name is still a gzip stream
        self.fires({"p-1.tar": gzip_stream(raw, mtime=1)}, ["dist:p-1.tar#gzip  builder-gzip-header"])
        for label, body in (("fixed header", gzip_stream(raw)), ("stdlib, mtime 0", gz(raw)),
                            ("header checksum", gzip_stream(raw, 0x02)),
                            ("two clean members", gz(raw[:half]) + gzip_stream(raw[half:])),
                            ("zero padding", gz(raw) + b"\0" * 512)):
            with self.subTest(control=label):
                self.clean({"p-1.tar.gz": body})
        self.clean({"p-1.tar": raw, "p-1.tar.bz2": bz2.compress(raw), "p-1.tar.xz": lzma.compress(raw)})

    # ---- zip: extra fields, comments, attributes ---------------------------------
    def test_zip_extra_fields(self) -> None:
        want = ["dist:p-1-py3-none-any.whl#extra  builder-zip-extra"]
        unix_new = extra_field(0x7875, b"\x01\x04" + struct.pack("<I", BUILDER_UID) + b"\x04"
                               + struct.pack("<I", 20))
        fields = (("unix uid/gid 0x7875", unix_new),
                  ("unix 0x5855", extra_field(0x5855, struct.pack("<IIHH", 1, 2, 501, 20))),
                  ("unix 0x7855", extra_field(0x7855, struct.pack("<HH", 501, 20))),
                  ("timestamps 0x5455", extra_field(0x5455, b"\x03" + struct.pack("<II", 1, 2))),
                  ("pkware unix 0x000d", extra_field(0x000D, struct.pack("<IIHH", 1, 2, 501, 20))),
                  ("ntfs 0x000a", extra_field(0x000A, b"\0" * 32)),
                  ("stray bytes", b"\x01"),
                  ("after a zip64 field", extra_field(0x0001, b"\0" * 16) + unix_new))
        for label, extra in fields:
            with self.subTest(case=label):
                self.fires({"p-1-py3-none-any.whl": zip_bytes(
                    [("a.py", b"ok"), (zinfo("b.py", extra=extra), b"ok")])}, want)
        # A field longer than the block it sits in: zipfile refuses the archive (exit 2).
        overlong = struct.pack("<HH", 0x0001, 99) + b"\0" * 8
        rc, lines, _ = self.bscan(
            {"p-1-py3-none-any.whl": zip_bytes([(zinfo("b.py", extra=overlong), b"ok")])})
        self.assertEqual((rc, lines), (2, []))
        # The two copies of a member's extra field are read separately: plant in one only.
        body = zip_bytes([(zinfo("b.py", extra=unix_new), b"ok")])
        local, central = body.index(unix_new), body.rindex(unix_new)
        self.assertLess(local, body.index(b"PK\x01\x02"))
        self.assertGreater(central, body.index(b"PK\x01\x02"))
        zip64_id = struct.pack("<H", 0x0001)
        for label, at in (("local header only", central), ("central directory only", local)):
            with self.subTest(case=label):
                self.fires({"p-1-py3-none-any.whl": body[:at] + zip64_id + body[at + 2:]}, want)
        self.clean({"p-1-py3-none-any.whl":
                    body[:local] + zip64_id + body[local + 2:central] + zip64_id + body[central + 2:]})
        big = io.BytesIO()
        with zipfile.ZipFile(big, "w") as z, z.open(zinfo("a.py"), "w", force_zip64=True) as f:
            f.write(b"ok")                                    # a real zip64 field
        self.clean({"p-1-py3-none-any.whl": big.getvalue(), "p-1.zip": zip_bytes([("a.py", b"ok")])})

    def test_zip_comments(self) -> None:
        want = ["dist:p-1-py3-none-any.whl#comment  builder-zip-comment"]
        self.fires({"p-1-py3-none-any.whl": zip_bytes(
            [("a.py", b"ok")], comment=b"packed on " + BUILDER_HOST.encode())}, want)
        self.fires({"p-1-py3-none-any.whl": zip_bytes(
            [(zinfo("a.py", comment=BUILDER_NAME.encode()), b"ok")])}, want)
        self.fires({"p-1-py3-none-any.whl": zip_bytes([("a.py", b"ok")], comment=b" ")}, want)
        self.clean({"p-1-py3-none-any.whl": zip_bytes([("a.py", b"ok")])})

    def test_zip_attributes(self) -> None:
        want = ["dist:p-1-py3-none-any.whl#attr  builder-zip-attr"]
        for label, info in (
                ("made on darwin", zinfo("a.py", system=19)),
                ("made on ntfs", zinfo("a.py", mode=0, system=10, dos=0x20)),
                ("setuid", zinfo("a.py", mode=0o104755)),
                ("sticky directory", zinfo("d/", mode=0o041777, dos=0x10)),
                ("device", zinfo("a.py", mode=stat.S_IFCHR | 0o644)),
                ("hidden and system bits", zinfo("a.py", dos=0x06)),
                ("high attribute byte", zinfo("a.py", dos=0x4000)),
                ("mode bits on FAT", zinfo("a.py", mode=0o100644, system=0))):
            with self.subTest(case=label):
                self.fires({"p-1-py3-none-any.whl": zip_bytes([("ok.py", b"ok"), (info, b"")])}, want)
        self.clean({"p-1-py3-none-any.whl": zip_bytes([
            (zinfo("a.py"), b"ok"), (zinfo("tool.sh", mode=0o100755), b"ok"),
            (zinfo("RECORD", mode=0o100664), b"ok"), (zinfo("d/", mode=0o040755, dos=0x10), b""),
            (zinfo("bare", mode=0o644), b"ok"), (zinfo("link", mode=stat.S_IFLNK | 0o777), b"a.py"),
            (zinfo("fat.txt", mode=0, system=0, dos=0x21), b"ok"), ("plain.py", b"ok")])})

    # ---- operating-system litter ---------------------------------------------------
    def test_os_junk_members(self) -> None:
        junk = ("__MACOSX/._a.py", "__MACOSX/", ".DS_Store", "pkg/.DS_Store", "pkg/._a.py",
                "pkg/Thumbs.db", "pkg/desktop.ini", ".AppleDouble/a.py", "pkg/.ds_store")
        fine = ("pkg/a._b.py", "pkg/_.py", "pkg/DS_Store.py", "pkg/x__MACOSX/a.py",
                "pkg/.DS_Store.md", "pkg/thumbs.db.py", "pkg/.hidden")
        for name in junk:
            with self.subTest(member=name):
                self.fires({"p-1.tar.gz": tar_bytes([("p-1/a.py", b"ok"), (f"p-1/{name}", b"")], "gz"),
                            "p-1.zip": zip_bytes([("a.py", b"ok"), (name, b"")])},
                           [f"dist:p-1.tar.gz:p-1/{name}#name  builder-os-junk",
                            f"dist:p-1.zip:{name}#name  builder-os-junk"])
        self.clean({"p-1.tar.gz": tar_bytes([(f"p-1/{n}", b"ok") for n in fine], "gz"),
                    "p-1.zip": zip_bytes([(n, b"ok") for n in fine])})

    # ---- text members ----------------------------------------------------------------
    def test_direct_url_json_with_a_local_url(self) -> None:
        local = ('{"url": "file:///srv/' + BUILDER_HOST + '/src", "dir_info": {}}').encode()
        for label, body in (("utf-8", local), ("upper case", local.replace(b"file:", b"FILE:")),
                            ("utf-16", codecs.BOM_UTF16_LE + local.decode().encode("utf-16-le")),
                            ("utf-16, no mark", local.decode().encode("utf-16-be"))):
            with self.subTest(case=label):
                self.fires({"p-1-py3-none-any.whl": zip_bytes(
                    [("p-1.dist-info/direct_url.json", body)]),
                    "p-1.tar.gz": tar_bytes([("p-1/src/direct_url.json", body)], "gz")},
                    ["dist:p-1-py3-none-any.whl:p-1.dist-info/direct_url.json  builder-local-url",
                     "dist:p-1.tar.gz:p-1/src/direct_url.json  builder-local-url"])
        remote = b'{"url": "https://example.invalid/p.git", "vcs_info": {"vcs": "git"}}'
        self.clean({"p-1-py3-none-any.whl": zip_bytes([
            ("p-1.dist-info/direct_url.json", remote),
            ("p-1.dist-info/METADATA", b"see file:///usr/share/doc for the format\n")])})

    def test_home_path_in_generated_metadata(self) -> None:
        """The existing home-path rule reaches every text file a build tool generates."""
        utf16 = codecs.BOM_UTF16_BE + f"src = {HOME}/x\n".encode("utf-16-be")
        whl = {"p-1.dist-info/RECORD": f"{HOME}/a.py,sha256=x,1\n".encode(),
               "p-1.dist-info/METADATA": f"Home-page: {HOME}/site\n".encode(),
               "p-1.dist-info/direct_url.json": ('{"url": "file://' + HOME + '"}').encode(),
               "p-1.dist-info/WHEEL": utf16}
        sdist = {"p-1/PKG-INFO": f"Description: built in {HOME}/x\n".encode(),
                 "p-1/p.egg-info/SOURCES.txt": f"{HOME}/a.py\n".encode(),
                 "p-1/setup.cfg": utf16}
        rc, lines, _ = self.bscan({"p-1-py3-none-any.whl": zip_bytes(list(whl.items())),
                                   "p-1.tar.gz": tar_bytes(list(sdist.items()), "gz")})
        self.assertEqual(rc, 1)
        self.assertEqual(lines, sorted(
            [f"dist:p-1-py3-none-any.whl:{n}  home-path" for n in whl]
            + [f"dist:p-1.tar.gz:{n}  home-path" for n in sdist]
            + ["dist:p-1-py3-none-any.whl:p-1.dist-info/direct_url.json  builder-local-url"]))

    def test_legitimate_metadata_passes_untouched(self) -> None:
        meta = (b"Metadata-Version: 2.4\nName: p\nVersion: 1\nAuthor: Example Author\n"
                b"Author-email: Example Author <author@example.invalid>\n"
                b"Maintainer: Example Maintainer\nMaintainer-email: team@example.invalid\n"
                b"License-Expression: MIT\nLicense-File: LICENSE\n"
                b"Project-URL: Homepage, https://example.invalid/p\n")
        wheel = (b"Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\nRoot-Is-Purelib: true\n"
                 b"Tag: py3-none-any\nBuild: 1\n")
        self.clean({
            "p-1-py3-none-any.whl": zip_bytes([
                (zinfo("p.py"), b"ok"), (zinfo("p-1.dist-info/METADATA"), meta),
                (zinfo("p-1.dist-info/WHEEL"), wheel),
                (zinfo("p-1.dist-info/licenses/LICENSE"), b"Copyright (c) Example Author\n"),
                (zinfo("p-1.dist-info/RECORD", mode=0o100664), b"p.py,sha256=x,2\n")]),
            "p-1.tar.gz": tar_bytes([
                owned("p-1", b"", type=tarfile.DIRTYPE, mode=0o755, mtime=1700000000),
                owned("p-1/PKG-INFO", meta, mode=0o644, mtime=1700000000),
                owned("p-1/tool.sh", mode=0o755, mtime=1700000000),
                ("p-1/p.egg-info/SOURCES.txt", b"PKG-INFO\np.py\n")], "gz")})

    # ---- every archive type the scanner opens -----------------------------------------
    def test_generic_tar_and_zip_archives(self) -> None:
        bad_tar: list[TarMember] = [owned("p-1/a.py", uname=BUILDER_NAME, uid=BUILDER_UID,
                                          pax_headers={"atime": "1.5"}), ("p-1/.DS_Store", b"")]
        bad_zip: list[tuple[str | zipfile.ZipInfo, bytes]] = [
            (zinfo("a.py", system=19, extra=extra_field(0x7875, b"\x01\x00\x00")), b"ok"),
            (".DS_Store", b"")]
        self.fires({"p-1.tar": tar_bytes(bad_tar), "p-1.tar.bz2": tar_bytes(bad_tar, "bz2"),
                    "p-1.tar.xz": tar_bytes(bad_tar, "xz"), "p-1.tgz": tar_bytes(bad_tar, "gz"),
                    "p-1.zip": zip_bytes(bad_zip, comment=b"x"),
                    "p-1.egg": zip_bytes(bad_zip, comment=b"x")},
                   [f"dist:{a}{tail}" for a in ("p-1.tar", "p-1.tar.bz2", "p-1.tar.xz", "p-1.tgz")
                    for tail in ("#owner  builder-owner", "#owner  builder-uid", "#header  builder-pax",
                                 ":p-1/.DS_Store#name  builder-os-junk")]
                   + [f"dist:{a}{tail}" for a in ("p-1.zip", "p-1.egg")
                      for tail in ("#extra  builder-zip-extra", "#attr  builder-zip-attr",
                                   "#comment  builder-zip-comment", ":.DS_Store#name  builder-os-junk")])
        ok_tar = [("p-1/a.py", b"ok")]
        self.clean({"p-1.tar": tar_bytes(ok_tar), "p-1.tar.bz2": tar_bytes(ok_tar, "bz2"),
                    "p-1.tar.xz": tar_bytes(ok_tar, "xz"), "p-1.tgz": tar_bytes(ok_tar, "gz"),
                    "p-1.zip": zip_bytes([("a.py", b"ok")]), "p-1.egg": zip_bytes([("a.py", b"ok")])})

    def test_git_archive_tarball_passes(self) -> None:
        """`git archive` writes the fixed owner root/root, uid 0 and a comment record."""
        self.commit({"a.txt": "ok\n", "tool.sh": "#!/bin/sh\n"})
        d = Path(self._t.name) / "dist"
        d.mkdir()
        for fmt in ("tar", "tar.gz"):
            git(self.repo, "archive", f"--format={fmt}", "--prefix=p-1/", "-o", str(d / f"p-1.{fmt}"), "HEAD")
        with tarfile.open(d / "p-1.tar") as t:
            first = t.next()
            assert first is not None
            self.assertEqual((first.uname, first.gname, first.uid, "comment" in first.pax_headers),
                             ("root", "root", 0, True))
        rc, out, _ = self.scan("--no-tracked", "--dist", str(d))
        self.assertEqual((rc, out), (0, ""))

    # ---- independence from the forbidden list, allowlisting, output ---------------------
    def test_rules_do_not_depend_on_the_forbidden_list(self) -> None:
        files = {"p-1.tar.gz": tar_bytes([owned("p-1/a.py", uname=BUILDER_NAME)], "gz")}
        want = ["dist:p-1.tar.gz#owner  builder-owner"]
        rc, lines, err = self.bscan(files)                    # no list at all
        self.assertEqual((rc, lines), (1, want))
        self.assertIn("WARNING: no forbidden list", err)
        rc, lines, _ = self.bscan(files, "--require-patterns", env={cp.ENV_VAR: WORD})
        self.assertEqual((rc, lines), (1, want))              # a list that does not name the owner
        rc, lines, _ = self.bscan(files, env={cp.ENV_VAR: BUILDER_NAME})
        self.assertEqual((rc, lines), (1, [*want, "dist:p-1.tar.gz#owner  forbidden-1"]))

    def test_allowlist_takes_an_exact_path_never_a_glob(self) -> None:
        files = {"p-1.tar.gz": tar_bytes([owned("p-1/a.py", uname=BUILDER_NAME, uid=BUILDER_UID),
                                          ("p-1/pkg/.DS_Store", b"")], "gz")}
        al = Path(self._t.name) / "allow"
        al.write_text("builder-owner p-1.tar.gz   # reason\nbuilder-os-junk pkg/.DS_Store\n")
        rc, lines, err = self.bscan(files, "--allowlist", str(al))
        self.assertEqual((rc, lines), (1, ["dist:p-1.tar.gz#owner  builder-uid"]))   # not listed
        self.assertIn("2 finding(s) suppressed by the allowlist", err)
        al.write_text("builder-owner p-1.tar.gz\nbuilder-uid p-1.tar.gz\nbuilder-os-junk pkg/.DS_Store\n")
        rc, lines, err = self.bscan(files, "--allowlist", str(al))
        self.assertEqual((rc, lines), (0, []))
        self.assertIn("3 finding(s) suppressed by the allowlist", err)
        al.write_text("builder-owner p-2.tar.gz\nforbidden-1 p-1.tar.gz\nunscanned-archive p-1.tar.gz\n")
        self.assertEqual(self.bscan(files, "--allowlist", str(al), env={cp.ENV_VAR: WORD})[0], 1)
        for glob in ("*", "**", "*.tar.gz", "p-1.tar.g?", "p-[0-9].tar.gz", "pkg/*"):
            for rule in ("builder-owner", "builder-uid", "builder-os-junk", "builder-gzip-header"):
                with self.subTest(glob=glob, rule=rule):
                    al.write_text(f"{rule} {glob}\n")
                    rc, lines, err = self.bscan(files, "--allowlist", str(al))
                    self.assertEqual((rc, lines), (2, []))
                    self.assertNotIn("Traceback", err)
        al.write_text("forbidden-1 *.py\n")                   # other rules keep their globs
        self.assertEqual(self.bscan(files, "--allowlist", str(al))[0], 1)

    def test_one_line_per_artifact_and_rule(self) -> None:
        members = [owned(f"p-1/m{i}.py", uname=BUILDER_NAME, gid=20) for i in range(40)]
        self.fires({"p-1.tar.gz": tar_bytes(members, "gz")},
                   ["dist:p-1.tar.gz#owner  builder-owner", "dist:p-1.tar.gz#owner  builder-uid"])


def _real_build_skip() -> str | None:
    root = Path(__file__).resolve().parent
    if not (root / "scripts" / "repro_build.py").is_file() or not (root / ".git").exists():
        return "not a git checkout with scripts/repro_build.py (an unpacked sdist?)"
    if importlib.util.find_spec("build") is None:
        return "the 'build' package is not installed (pip install -r requirements-dev.txt)"
    if shutil.which("git") is None or shutil.which("tar") is None:
        return "git or tar is not installed"
    return None


class RealBuilds(unittest.TestCase):
    """The real artifacts: scripts/repro_build.py must pass, a plain `python -m build` must not.

    Needs git, the `build` package and network access for the build backend. Without them
    the class is skipped with a message; POSTBOX_REQUIRE_BUILD_TESTS=1 (set where the tools
    are known to be installed, as in CI) turns that skip into a failure.
    """

    ROOT = Path(__file__).resolve().parent
    work: Path

    @classmethod
    def setUpClass(cls) -> None:
        why = _real_build_skip()
        if why:
            cls.unavailable(why)
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.work = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if k != cp.ENV_VAR}
        r = subprocess.run([sys.executable, str(cls.ROOT / "scripts" / "repro_build.py"),
                            "--outdir", str(cls.work / "repro")], env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cls.check_build(r)
        src = cls.work / "src"
        src.mkdir()
        tree = subprocess.run(["git", "-C", str(cls.ROOT), "archive", "--format=tar", "HEAD"],
                              check=True, stdout=subprocess.PIPE).stdout
        subprocess.run(["tar", "-x", "-f", "-", "-C", str(src)], input=tree, check=True)
        r = subprocess.run([sys.executable, "-m", "build", "--outdir", str(cls.work / "plain"),
                            str(src)], env=env, text=True, cwd=cls.work,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cls.check_build(r)

    @classmethod
    def unavailable(cls, why: str) -> None:
        msg = f"real builds NOT scanned: {why}"
        if os.environ.get("POSTBOX_REQUIRE_BUILD_TESTS") == "1":
            raise AssertionError(msg)
        print(f"\nSKIPPED: {msg}", file=sys.stderr)
        raise unittest.SkipTest(msg)

    @classmethod
    def check_build(cls, r: subprocess.CompletedProcess[str]) -> None:
        if r.returncode == 0:
            return
        if any(s in r.stdout for s in ("Could not find a version", "ConnectionError",
                                       "Temporary failure in name resolution", "network")):
            cls.unavailable("cannot fetch the build backend (no network)")
        raise AssertionError(f"build failed ({r.returncode}):\n{r.stdout[-3000:]}")

    def scan(self, d: Path) -> tuple[int, list[str], str]:
        rc, out, err = run(["--root", str(self.ROOT), "--no-tracked", "--dist", str(d)])
        return rc, sorted(out.splitlines()), err

    def test_repro_build_artifacts_have_no_builder_findings(self) -> None:
        names = sorted(p.name for p in (self.work / "repro").iterdir())
        self.assertEqual([n.rsplit(".", 1)[-1] for n in names], ["SHA256SUMS", "whl", "gz"], names)
        rc, lines, err = self.scan(self.work / "repro")
        self.assertEqual((rc, lines), (0, []))
        self.assertNotIn("builder-", err)

    def test_plain_build_sdist_is_rejected(self) -> None:
        rc, lines, err = self.scan(self.work / "plain")
        self.assertEqual(rc, 1)
        rules = {ln.rsplit("  ", 1)[1] for ln in lines}
        sdists = [ln for ln in lines if ".tar.gz#" in ln]
        self.assertEqual(len(sdists), len(lines), "only the sdist is at fault, never the wheel")
        self.assertIn("builder-gzip-header", rules)           # file name and build time
        user, group = pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
        if os.getuid() or os.getgid():
            self.assertIn("builder-uid", rules)
        if user not in ("", "root") or group not in ("", "root"):
            self.assertIn("builder-owner", rules)
        self.assertLessEqual(rules, {"builder-gzip-header", "builder-uid", "builder-owner"})
        # The account that ran this test is in that sdist; it must not be in the report.
        for name in {user, group} - {"", "root"}:
            for line in lines + err.splitlines():
                self.assertNotIn(name, line)


class Preflight(unittest.TestCase):
    """scripts/preflight.sh, driven with a stand-in interpreter that records its arguments."""

    SCRIPT = Path(__file__).resolve().parent / "scripts" / "preflight.sh"

    def setUp(self) -> None:
        if not self.SCRIPT.is_file():
            self.skipTest("scripts/preflight.sh is not shipped here (e.g. unpacked sdist)")
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.home = Path(self._t.name) / "home dir"            # a space: arguments must be quoted
        self.home.mkdir()
        self.log = Path(self._t.name) / "argv.log"
        self.stub = Path(self._t.name) / "fakepython"
        self.stub.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done >> "$LOG"\n'
                             'printf -- "--END--\\n" >> "$LOG"\n')
        self.stub.chmod(0o755)

    def preflight(self, **env: str) -> tuple[int, str, list[list[str]]]:
        r = subprocess.run(["sh", str(self.SCRIPT), str(self.stub)], capture_output=True,
                           text=True, env={"PATH": os.environ.get("PATH", ""),
                                           "HOME": str(self.home), "LOG": str(self.log), **env})
        raw = self.log.read_text() if self.log.exists() else ""
        calls = [c.splitlines() for c in raw.split("--END--\n") if c]
        return r.returncode, r.stdout + r.stderr + raw, calls

    def test_fails_closed_without_a_list_before_running_anything(self) -> None:
        rc, _, calls = self.preflight()
        self.assertEqual((rc, calls), (2, []))
        rc, _, calls = self.preflight(**{cp.ENV_VAR: ""})
        self.assertEqual((rc, calls), (2, []))

    def test_scans_every_ref_requires_patterns_and_never_shows_the_list(self) -> None:
        rc, text, calls = self.preflight(**{cp.ENV_VAR: WORD})
        self.assertEqual(rc, 0)
        self.assertNotIn(WORD, text)                           # not echoed, not an argument
        scan = [c for c in calls if c[0] == "check_publication.py"]
        self.assertEqual(len(scan), 1)
        self.assertIn("--require-patterns", scan[0])
        self.assertIn("--git-log=--all", scan[0])              # all refs, not just HEAD
        self.assertIn("--dist", scan[0])
        self.assertNotIn("--no-tracked", scan[0])
        self.assertNotIn("--patterns-file", scan[0])

    def test_list_file_is_passed_by_path_only(self) -> None:
        cfg = self.home / ".config" / "agent-postbox"
        cfg.mkdir(parents=True)
        (cfg / "forbidden").write_text(f"{WORD}\n")
        rc, text, calls = self.preflight()
        self.assertEqual(rc, 0)
        self.assertNotIn(WORD, text)
        scan = [c for c in calls if c[0] == "check_publication.py"][0]
        self.assertEqual(scan[scan.index("--patterns-file") + 1], str(cfg / "forbidden"))


class OwnRepo(unittest.TestCase):
    def test_this_checkout_has_no_generic_findings(self) -> None:
        root = Path(__file__).resolve().parent
        if not (root / ".git").exists():
            self.skipTest("not a git checkout (e.g. unpacked sdist)")
        rc, out, _ = run(["--root", str(root)])
        self.assertEqual((rc, out), (0, ""))


if __name__ == "__main__":
    unittest.main()
