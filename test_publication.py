#!/usr/bin/env python3
"""Tests for check_publication.py, with positive controls.

Every "must be clean" assertion is paired with a planted violation that the same
scanner, in the same mode, must flag; a scanner that finds nothing proves nothing
until it has been seen to find something. All sensitive-looking strings are
assembled at run time so this file never matches its own detectors. Stdlib only.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

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
        self.assertEqual(self.scan("--no-tracked", "--git-log", env={cp.ENV_VAR: WORD})[0], 0)

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

    def test_dist_allowlist_uses_path_without_sdist_top_dir(self) -> None:
        d = self.make({"p-1/a.py": WORD}, {"b.py": "ok"})
        self.commit({"a": "1", ".publication-allowlist": "forbidden-1 a.py\n"})
        self.assertEqual(self.scan("--no-tracked", "--dist", str(d), env={cp.ENV_VAR: WORD})[0], 0)

    def test_empty_dist_dir_is_error_not_pass(self) -> None:
        d = Path(self._t.name) / "empty"
        d.mkdir()
        self.commit({"a": "1"})
        self.assertEqual(self.scan("--no-tracked", "--dist", str(d))[0], 2)


class OwnRepo(unittest.TestCase):
    def test_this_checkout_has_no_generic_findings(self) -> None:
        root = Path(__file__).resolve().parent
        if not (root / ".git").exists():
            self.skipTest("not a git checkout (e.g. unpacked sdist)")
        rc, out, _ = run(["--root", str(root)])
        self.assertEqual((rc, out), (0, ""))


if __name__ == "__main__":
    unittest.main()
