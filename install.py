#!/usr/bin/env python3
"""Install agent-postbox into a project. Stdlib only.

    python install.py <project>            # install / update (poll pickup)
    python install.py <project> --check    # report, change nothing
    python install.py <project> --dest tools/agent-postbox
    python install.py <project> --adapter claude-code

Copies the tool + adapter + spec into the project and creates the mailbox
with a PARTICIPANTS table. Pickup is poll by default
(`agent_mail.py list --to <you> --live`). A hook is an adapter for a host,
not the protocol: `--adapter claude-code` optionally registers
`hooks/agent_mail_check.py` as UserPromptSubmit in the PROJECT's
`.claude/settings.json`. Other hosts copy that script and wire their own
prompt-submit event, or keep polling.

Project-level, not `~/.claude/`, deliberately: a user-level registration does
not survive a clone, so a fresh machine would be SILENTLY unreachable rather
than visibly so. Registering it in the repo means the mechanism travels with
the code -- which is the whole point of installing this per project.

Safety rules for a settings merge, in priority order:
  1. Never destroy an existing config. Unparseable JSON aborts the merge; it is
     someone's hand-edited file, and a settings file we corrupt breaks every
     future session in that project.
  2. Back up before writing.
  3. Be idempotent. Re-running must not stack duplicate hook entries.
  4. Do not create host config unless `--adapter` asked for it.
  5. Do not attach a second mailbox. If an ancestor already has docs/agent-mail,
     this tree is occupied -- refuse rather than mint a nested owner.
  6. Do not install a mailbox into this spec checkout. The public repo is the
     tool, not an inbox -- use AGENT_MAIL_DIR or install into a project.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK_MARKER = "agent_mail_check.py"
# SECURITY.md ships with every install on purpose: the pickup's threat model
# matters most in the repo it is actually running in, not in this one.
PAYLOAD = ("agent_mail.py", "PROTOCOL.md", "SECURITY.md", "selftest.py")
ADAPTERS = ("claude-code",)


def is_tool_checkout(root: Path) -> bool:
    return (root / "agent_mail.py").is_file() and (root / "PROTOCOL.md").is_file()


def occupied_ancestor(project: Path, mailbox_rel: str) -> Path | None:
    """Nearest mailbox above `project`. Installing here would be a second owner."""
    rel = Path(mailbox_rel)
    for parent in project.resolve().parents:
        box = parent / rel
        if box.is_dir():
            return box
        if (parent / ".git").exists():
            return None
    return None


def python_cmd() -> str:
    """`python3`, else `python`, else this interpreter's path -- whichever
    actually runs on this host. (Kept local: install.py runs from the spec
    checkout and does not import agent_mail.)"""
    for name in ("python3", "python"):
        if shutil.which(name):
            return name
    return sys.executable


def hook_command(dest_rel: str) -> str:
    # A bare `python` does not exist on stock macOS; a hook that cannot start
    # delivers nothing, and looks exactly like an empty inbox.
    return f'{python_cmd()} "$CLAUDE_PROJECT_DIR/{dest_rel}/hooks/{HOOK_MARKER}"'


def registered_hook(settings: dict) -> dict | None:
    """The registered adapter's hook entry, if any."""
    for group in settings.get("hooks", {}).get("UserPromptSubmit", []) or []:
        for hook in (group or {}).get("hooks", []) or []:
            if HOOK_MARKER in str(hook.get("command", "")):
                return hook
    return None


def interpreter_missing(command: str) -> bool:
    """True when the hook's interpreter (first word) can't be found here --
    e.g. an older install wrote bare `python` on a host that only has python3."""
    first = command.strip().split(" ", 1)[0].strip('"')
    return bool(first) and shutil.which(first) is None and not Path(first).is_file()


def already_registered(settings: dict) -> bool:
    for group in settings.get("hooks", {}).get("UserPromptSubmit", []) or []:
        for hook in (group or {}).get("hooks", []) or []:
            if HOOK_MARKER in str(hook.get("command", "")):
                return True
    return False


def merge_settings(path: Path, dest_rel: str, check: bool) -> str:
    settings: dict = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8-sig")  # tolerate a BOM
        try:
            settings = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            return (f"ABORT: {path} is not valid JSON ({e}). Refusing to touch it -- "
                    "fix it by hand, then re-run. Nothing else was changed.")
        if not isinstance(settings, dict):
            return f"ABORT: {path} is not a JSON object. Refusing to touch it."
        hook = registered_hook(settings)
        if hook is not None:
            old = str(hook.get("command", ""))
            if not interpreter_missing(old):
                return "claude-code adapter already registered (no change)"
            if check:
                return f"WOULD repair the adapter: `{old.split(' ', 1)[0]}` is not on PATH here"
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            hook["command"] = hook_command(dest_rel)
            path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            return (f"repaired the claude-code adapter (`{old.split(' ', 1)[0]}` was not on PATH; "
                    f"now `{python_cmd()}`; backup at {path.name}.bak)")

    if check:
        return "WOULD register the claude-code prompt-submit adapter"

    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    hooks = settings.setdefault("hooks", {})
    hooks.setdefault("UserPromptSubmit", []).append(
        {"hooks": [{"type": "command", "command": hook_command(dest_rel)}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return "registered the claude-code prompt-submit adapter" + (
        f" (backup at {path.name}.bak)" if path.with_suffix(path.suffix + ".bak").exists() else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Install agent-postbox into a project")
    ap.add_argument("project")
    ap.add_argument("--dest", default="tools/agent-postbox",
                    help="where the tool lives, relative to the project root")
    ap.add_argument("--mailbox", default="docs/agent-mail",
                    help="where messages live, relative to the project root")
    ap.add_argument("--adapter", choices=ADAPTERS, default=None,
                    help="optional host adapter to register (poll is the default pickup)")
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        print(f"not a directory: {project}", file=sys.stderr)
        return 2
    if not (project / ".git").exists():
        print(f"warning: {project} has no .git -- mailbox discovery walks up from cwd "
              "and stops at the nearest mailbox or `.git`", file=sys.stderr)

    if is_tool_checkout(project):
        print(f"ABORT: {project} looks like the spec checkout "
              "(agent_mail.py + PROTOCOL.md). The public repo is the spec, not the mailbox.",
              file=sys.stderr)
        return 2

    occupied = occupied_ancestor(project, args.mailbox)
    if occupied is not None:
        msg = (f"ABORT: mailbox already at {occupied}. This tree is occupied -- "
               "installing here would attach a second owner. Use that mailbox, "
               "or pass a project that is not nested under it.")
        print(msg, file=sys.stderr)
        return 2

    dest_rel = args.dest.replace("\\", "/").strip("/")
    dest = project / dest_rel
    print(f"agent-postbox -> {project}")

    if args.check:
        for name in PAYLOAD:
            print(f"  {'ok  ' if (dest / name).exists() else 'MISS'}  {dest_rel}/{name}")
        print(f"  {'ok  ' if (dest / 'hooks' / HOOK_MARKER).exists() else 'MISS'}  "
              f"{dest_rel}/hooks/{HOOK_MARKER}")
        print(f"  {'ok  ' if (project / args.mailbox).is_dir() else 'MISS'}  {args.mailbox}/")
        if args.adapter == "claude-code":
            print(f"  {merge_settings(project / '.claude' / 'settings.json', dest_rel, True)}")
        else:
            print("  pickup: poll (no host adapter requested)")
        return 0

    (dest / "hooks").mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        shutil.copy2(HERE / name, dest / name)
    shutil.copy2(HERE / "hooks" / HOOK_MARKER, dest / "hooks" / HOOK_MARKER)
    print(f"  copied tool + spec into {dest_rel}/")

    mailbox = project / args.mailbox
    mailbox.mkdir(parents=True, exist_ok=True)
    participants = mailbox / "PARTICIPANTS.md"
    if participants.exists():
        print(f"  kept existing {args.mailbox}/PARTICIPANTS.md")
    else:
        participants.write_text(
            (HERE / "PARTICIPANTS.template.md").read_text(encoding="utf-8")
            .replace("{{DEST}}", dest_rel), encoding="utf-8")
        print(f"  created {args.mailbox}/PARTICIPANTS.md")

    if args.adapter == "claude-code":
        print(f"  {merge_settings(project / '.claude' / 'settings.json', dest_rel, False)}")
    else:
        print(f"  pickup is poll: {python_cmd()} "
              f"{dest_rel}/agent_mail.py list --to <you> --live")
        print("  (pass --adapter claude-code to register a prompt-submit hook)")

    print("\nVerify pickup before trusting it -- silence proves nothing (PROTOCOL.md §8):")
    print(f"  {python_cmd()} {dest_rel}/selftest.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
