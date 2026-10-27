#!/usr/bin/env python3
"""Failure injection for install.py: what does an install killed part-way leave, and does a re-run recover?

Investigation test, not yet wired into ci.yml or scripts/ci_run.py. It exercises the EXISTING installer;
nothing here changes production code. POSIX only (it kills the installer with SIGKILL at a seam).

How: a small driver imports install.py, replaces one function at a seam (a file copy, the PARTICIPANTS
write, the settings backup, the settings rename) with a version that SIGKILLs its own process at the Nth
call (before, or half-way through the write), then calls install._run(). The parent inspects what is on
disk, then re-runs the real installer and checks recovery and idempotence.

Two scenarios per seam: a FRESH install, and an UPGRADE of a v0.5.0 install (built from the git tag, so it
is a genuinely older CLI and hook) whose settings.json carries user-owned keys.

Observation (printed, not asserted): whether the installed hook still delivers an owed ASK in the interrupted
state, which is the window where CLI and hook can be different versions.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(f"{name}: {detail[:400]}")


DRIVER = r'''
import importlib.util, os, signal, sys
from pathlib import Path
seam, n, install_py = os.environ["PB_SEAM"], int(os.environ.get("PB_N", "0")), sys.argv[1]
spec = importlib.util.spec_from_file_location("install_under_test", install_py)
inst = importlib.util.module_from_spec(spec); sys.modules["install_under_test"] = inst
spec.loader.exec_module(inst)
sys.argv = [install_py] + sys.argv[2:]
def die():
    os.kill(os.getpid(), signal.SIGKILL)
calls = {"copy": 0}
real_copy2 = inst.shutil.copy2
def copy2(src, dst, *a, **k):
    i = calls["copy"]; calls["copy"] += 1
    if seam == "copy-before" and i == n: die()
    if seam == "copy-mid" and i == n:
        data = Path(src).read_bytes()
        Path(dst).write_bytes(data[: len(data) // 2]); die()
    r = real_copy2(src, dst, *a, **k)
    if seam == "copy-after" and i == n: die()
    return r
inst.shutil.copy2 = copy2
real_wt = Path.write_text
def write_text(self, data, *a, **k):
    if self.name == "PARTICIPANTS.md":
        if seam == "participants-before": die()
        if seam == "participants-mid":
            real_wt(self, data[: len(data) // 2], *a, **k); die()
    return real_wt(self, data, *a, **k)
Path.write_text = write_text
real_back_up = inst.back_up
def back_up(path):
    r = real_back_up(path)
    if seam == "after-backup": die()
    return r
inst.back_up = back_up
real_replace = os.replace
def replace(src, dst, *a, **k):
    if seam == "before-replace" and str(dst).endswith("settings.json"): die()
    r = real_replace(src, dst, *a, **k)
    if seam == "after-replace" and str(dst).endswith("settings.json"): die()
    return r
inst.os.replace = replace
raise SystemExit(inst._run())
'''

PAYLOAD = ("agent_mail.py", "PROTOCOL.md", "SECURITY.md", "selftest.py", "hooks/agent_mail_check.py")
SEAMS: list[tuple[str, int, str]] = (
    [("copy-before", i, f"before copying {PAYLOAD[i]}") for i in range(5)]
    + [("copy-mid", 0, "half-way through writing agent_mail.py"),
       ("copy-mid", 4, "half-way through writing hooks/agent_mail_check.py"),
       ("participants-before", 0, "before writing PARTICIPANTS.md"),
       ("participants-mid", 0, "half-way through writing PARTICIPANTS.md"),
       ("after-backup", 0, "after the settings backup, before the settings write"),
       ("before-replace", 0, "settings temp file written, before the rename"),
       ("after-replace", 0, "after the settings rename")])
USER_SETTINGS = {"permissions": {"allow": ["Bash(ls:*)"]}, "model": "user-choice",
                 "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}}


def sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def digest_tree(root: Path) -> dict[str, str | None]:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def run_installer(project: Path, install_py: Path, *extra: str, env: dict[str, str] | None = None,
                  ) -> subprocess.CompletedProcess[str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MAIL")}
    e.update(env or {})
    argv = ([sys.executable, "-I", "-c", DRIVER, str(install_py), str(project), "--adapter", "claude-code", *extra]
            if env else [sys.executable, "-I", str(install_py), str(project), "--adapter", "claude-code", *extra])
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, env=e)


def old_release(dst: Path) -> Path | None:
    """A v0.5.0 checkout (git archive of the tag), or None when the tag is not available."""
    dst.mkdir()
    try:
        tar = subprocess.run(["git", "archive", "v0.5.0"], cwd=HERE, capture_output=True, timeout=60)
        if tar.returncode != 0:
            return None
        subprocess.run(["tar", "-x", "-C", str(dst)], input=tar.stdout, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return dst / "install.py" if (dst / "install.py").exists() else None


def hook_delivers(project: Path, tmp: Path) -> str:
    """Observe the installed hook in the current state: does it still show an owed ASK?"""
    box = tmp / "hook-box"
    shutil.rmtree(box, ignore_errors=True)
    box.mkdir()
    send = subprocess.run([sys.executable, "-I", str(HERE / "agent_mail.py"), "send", "--type", "ASK",
                           "--from", "alice", "--to", "bob", "--subject", "ping-for-hook", "--body", "b"],
                          capture_output=True, text=True, env={**os.environ, "AGENT_MAIL_DIR": str(box)})
    if send.returncode != 0:
        return f"setup-failed({send.stderr[:60]})"
    hook = project / "tools" / "agent-postbox" / "hooks" / "agent_mail_check.py"
    if not hook.exists():
        return "no-hook-file"
    r = subprocess.run([sys.executable, "-I", str(hook)], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "AGENT_MAIL_DIR": str(box), "AGENT_MAIL_IDENTITY": "bob"})
    if "ping-for-hook" in r.stdout:
        return "delivers"
    return f"SILENT(rc={r.returncode}, stderr={r.stderr.strip()[:80]!r})"


def recovery_checks(label: str, project: Path, new_files: dict[str, str | None], original_settings: str | None,
                    tmp: Path) -> None:
    """Re-run the real installer on the interrupted tree; it must finish, converge, and not lose user data."""
    r = subprocess.run([sys.executable, "-I", str(HERE / "install.py"), str(project), "--adapter", "claude-code"],
                       capture_output=True, text=True, timeout=120)
    check(f"{label}: re-run exits 0", r.returncode == 0, r.stdout + r.stderr)
    dest = project / "tools" / "agent-postbox"
    check(f"{label}: every payload file equals the current release after the re-run",
          all(sha(dest / f) == new_files[f] for f in PAYLOAD),
          str({f: sha(dest / f) == new_files[f] for f in PAYLOAD}))
    settings_path = project / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        parsed = True
    except (OSError, ValueError):
        settings, parsed = {}, False
    check(f"{label}: settings.json is valid JSON after the re-run", parsed)
    entries = [h for g in settings.get("hooks", {}).get("UserPromptSubmit", []) for h in g.get("hooks", [])
               if "agent_mail_check.py" in str(h.get("command", ""))]
    check(f"{label}: exactly one agent-postbox UserPromptSubmit hook is registered (no duplicates)",
          len(entries) == 1, str(len(entries)))
    if original_settings is not None:
        orig = json.loads(original_settings)
        check(f"{label}: user-owned settings keys are preserved",
              all(settings.get(k) == v for k, v in orig.items() if k != "hooks")
              and settings.get("hooks", {}).get("PreToolUse") == orig["hooks"]["PreToolUse"])
        bak = settings_path.with_name("settings.json.bak")
        check(f"{label}: the first backup still holds the user's original bytes",
              bak.is_file() and bak.read_text(encoding="utf-8") == original_settings)
    part = project / "docs" / "agent-mail" / "PARTICIPANTS.md"
    template = (HERE / "PARTICIPANTS.template.md").read_text(encoding="utf-8").replace("{{DEST}}", "tools/agent-postbox")
    check(f"{label}: PARTICIPANTS.md is the complete template after the re-run (or the user's own file)",
          part.is_file() and part.read_text(encoding="utf-8") == template,
          f"{part.stat().st_size if part.exists() else 'absent'} bytes vs template {len(template)}")
    before = digest_tree(project)
    r2 = subprocess.run([sys.executable, "-I", str(HERE / "install.py"), str(project), "--adapter", "claude-code"],
                        capture_output=True, text=True, timeout=120)
    after = digest_tree(project)
    check(f"{label}: a second re-run exits 0 and changes nothing (idempotent)",
          r2.returncode == 0 and before == after,
          str({k for k in set(before) | set(after) if before.get(k) != after.get(k)}))
    check(f"{label}: the installed hook delivers an owed ASK after recovery",
          hook_delivers(project, tmp) == "delivers", hook_delivers(project, tmp))


def litter(project: Path) -> list[str]:
    return sorted(str(p.relative_to(project)) for p in project.rglob("*")
                  if p.is_file() and (".tmp" in p.name or p.name.endswith(".bak") or ".bak." in p.name))


def one(seam: str, n: int, desc: str, scenario: str, root: Path, old_install: Path | None,
        new_files: dict[str, str | None]) -> None:
    label = f"{scenario} / {desc}"
    project = root / f"{scenario}-{seam}-{n}"
    (project / ".git").mkdir(parents=True)
    original_settings: str | None = None
    old_files: dict[str, str | None] = {}
    if scenario in ("adopt", "upgrade"):
        (project / ".claude").mkdir()
        original_settings = json.dumps(USER_SETTINGS, indent=2) + "\n"
        (project / ".claude" / "settings.json").write_text(original_settings, encoding="utf-8")
    if scenario == "upgrade":
        assert old_install is not None
        r0 = subprocess.run([sys.executable, "-I", str(old_install), str(project), "--adapter", "claude-code"],
                            capture_output=True, text=True, timeout=120)
        if r0.returncode != 0:
            check(f"{label}: v0.5.0 baseline install works", False, r0.stdout + r0.stderr)
            return
        dest = project / "tools" / "agent-postbox"
        old_files = {f: sha(dest / f) for f in PAYLOAD}
    killed = run_installer(project, HERE / "install.py", env={"PB_SEAM": seam, "PB_N": str(n)})
    if killed.returncode == 0:
        print(f"    seam not reached in this scenario (the installer finished: nothing to interrupt); {label}")
        check(f"{label}: control, the uninterrupted install exits 0", True)
        return
    check(f"{label}: the installer really died by SIGKILL here", killed.returncode == -9,
          f"rc={killed.returncode} {killed.stdout[-200:]} {killed.stderr[-200:]}")
    dest = project / "tools" / "agent-postbox"
    state = {f: ("new" if sha(dest / f) == new_files[f] else "old" if sha(dest / f) == old_files.get(f)
                 else "ABSENT" if not (dest / f).exists() else "TORN") for f in PAYLOAD}
    sp = project / ".claude" / "settings.json"
    if sp.exists():
        try:
            json.loads(sp.read_text(encoding="utf-8"))
            sstate = "valid"
        except ValueError:
            sstate = "CORRUPT"
    else:
        sstate = "absent"
    part = project / "docs" / "agent-mail" / "PARTICIPANTS.md"
    template = (HERE / "PARTICIPANTS.template.md").read_text(encoding="utf-8").replace("{{DEST}}", "tools/agent-postbox")
    pstate = "absent" if not part.exists() else "whole" if part.read_text(encoding="utf-8") == template else "TORN"
    mixed = len({v for v in state.values() if v in ("new", "old")}) > 1
    print(f"    state: files={state} mixed={mixed} settings={sstate} participants={pstate} "
          f"litter={litter(project)} hook-in-window={hook_delivers(project, root)}")
    check(f"{label}: settings.json is never left corrupt", sstate != "CORRUPT")
    recovery_checks(label, project, new_files, original_settings, root)


def main() -> int:
    if os.name != "posix":
        print("  SKIP  install interruption (SIGKILL at a seam is POSIX only)")
        return 0
    root = Path(tempfile.mkdtemp(prefix="pb-interrupt-"))
    try:
        new_files = {f: sha(HERE / f) for f in PAYLOAD}
        old_install = old_release(root / "v050")
        for seam, n, desc in SEAMS:
            print(f"\nfresh install killed {desc}")
            one(seam, n, desc, "fresh", root, old_install, new_files)
            print(f"\ninstall over existing user settings killed {desc}")
            one(seam, n, desc, "adopt", root, old_install, new_files)
        if old_install is None:
            print("  SKIP  upgrade scenarios (the v0.5.0 tag is not available here)")
        else:
            for seam, n, desc in SEAMS:
                print(f"\nupgrade from v0.5.0 killed {desc}")
                one(seam, n, desc, "upgrade", root, old_install, new_files)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
