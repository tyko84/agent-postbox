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
import gzip
import io
import lzma
import os
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import unittest
import warnings
import zipfile
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


def git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def git_out(repo: Path, *a: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, input=stdin).stdout.strip()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.repo = Path(self._t.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
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
        with tarfile.open(d / "p-1.tar.gz", "w:gz") as t:
            for n, c in tar_members.items():
                b = c.encode()
                ti = tarfile.TarInfo(n)
                ti.size = len(b)
                t.addfile(ti, io.BytesIO(b))
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
        with tarfile.open(d / "p-1.tar.gz", "w:gz") as t:
            ti = tarfile.TarInfo("p-1/u.txt")
            ti.size = len(body)
            t.addfile(ti, io.BytesIO(body))
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
        with tarfile.open(d / "p-1.tar.gz", "w:gz") as t:
            ti = tarfile.TarInfo("p-1/big.txt")
            ti.size = len(body)
            t.addfile(ti, io.BytesIO(body))
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
        git(tmp, "clone", "-q", "--depth", "1", self.repo.as_uri(), "shallow")
        git(tmp, "clone", "-q", self.repo.as_uri(), "full")
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


def tar_bytes(members: list[tarfile.TarInfo | tuple[str, bytes]], comp: str = "") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for m in members:
            if isinstance(m, tarfile.TarInfo):
                t.addfile(m)
            else:
                ti = tarfile.TarInfo(m[0])
                ti.size = len(m[1])
                t.addfile(ti, io.BytesIO(m[1]))
    raw = buf.getvalue()
    if comp == "gz":
        return gzip.compress(raw)
    if comp == "bz2":
        return bz2.compress(raw)
    return lzma.compress(raw) if comp == "xz" else raw


def zip_bytes(members: list[tuple[str, bytes]], comment: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")            # duplicate member names are deliberate
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.comment = comment
            for n, b in members:
                z.writestr(n, b)
    return buf.getvalue()


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
        clean = gzip.compress(bytes(raw))
        raw[1024 + 148:1024 + 156] = b"zzzzzzzz"             # second header: checksum destroyed
        with tarfile.open(fileobj=io.BytesIO(bytes(raw))) as t:
            self.assertEqual(t.getnames(), ["p-1/a.py"])      # tarfile stops there without error
        d = self.dist({"p-1.tar.gz": gzip.compress(bytes(raw))})
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
        owner.uname = WORD                                     # the builder's login name
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
        self.assertEqual(lines, ["dist:p-1.tar.gz#owner  forbidden-1",
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
                ("comment", note, ["dist:p-1.whl#comment  forbidden-1"]),
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
