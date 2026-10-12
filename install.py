#!/usr/bin/env python3
"""Install agent-postbox into a project. Stdlib only.

    python install.py <project>            # install / update (poll pickup)
    python install.py <project> --check    # report, change nothing
    python install.py <project> --dest tools/agent-postbox
    python install.py <project> --adapter claude-code
    python install.py <project> --adapter codex

Copies the tool + adapter + spec into the project and creates the mailbox
with a PARTICIPANTS table. Pickup is poll by default
(`agent_mail.py list --to <you> --live`). A hook is an adapter for a host,
not the protocol: `--adapter claude-code` optionally registers
`hooks/agent_mail_check.py` as UserPromptSubmit in the PROJECT's
`.claude/settings.json`; `--adapter codex` registers the same script, unchanged,
as UserPromptSubmit in the PROJECT's `.codex/hooks.json` (Codex adds a hook's
plain-text stdout to the model's context; it runs a new or changed hook only
after the user has trusted it in `/hooks`). docs/hosts.md has the per-host
matrix and what was verified. Other hosts copy that script and wire their own
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
  7. A registered command must not be able to block a prompt. Codex treats exit
     code 2 from a UserPromptSubmit hook as "block the prompt", and an
     interpreter that cannot open its script exits 2; so the Codex command ends
     in `|| true`, and `--adapter codex` is refused (before anything is copied)
     where the command could not resolve: no git repository, a destination the
     shell would re-interpret, or a `.codex/` this installer cannot merge safely.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK_MARKER = "agent_mail_check.py"
# SECURITY.md ships with every install on purpose: the pickup's threat model
# matters most in the repo it is actually running in, not in this one.
PAYLOAD = ("agent_mail.py", "PROTOCOL.md", "SECURITY.md", "selftest.py")
# adapter -> the host's project-level hook file. Both hosts use the same shape:
# {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": ...}]}]}}
HOST_CONFIG = {"claude-code": (".claude", "settings.json"), "codex": (".codex", "hooks.json")}
ADAPTERS = tuple(HOST_CONFIG)
# An inline `[hooks]` / `[[hooks.Event]]` / `[hooks.Event]` table in .codex/config.toml.
CODEX_INLINE_HOOKS = re.compile(r"^\s*\[\[?\s*hooks\s*[.\]]", re.M)
CODEX_TRUST = ("Codex skips a new or changed hook until it is trusted: start codex in the "
               "project, run /hooks, review and trust it, then run the positive control in "
               "docs/hosts.md")


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


def hook_command(dest_rel: str, adapter: str = "claude-code") -> str:
    # A bare `python` does not exist on stock macOS; a hook that cannot start
    # delivers nothing, and looks exactly like an empty inbox.
    if adapter == "codex":
        # Codex runs a hook with the session's cwd, which may be a subdirectory,
        # and documents resolving repo-local hooks from the git root. `|| true`:
        # see safety rule 7.
        return (f'{python_cmd()} "$(git rev-parse --show-toplevel)/{dest_rel}/hooks/'
                f'{HOOK_MARKER}" || true')
    return f'{python_cmd()} "$CLAUDE_PROJECT_DIR/{dest_rel}/hooks/{HOOK_MARKER}"'


def shape_problem(settings: object) -> str | None:
    """Why this parsed hook file cannot be merged into, or None. Anything that
    is not the documented nesting is left alone rather than rewritten."""
    if not isinstance(settings, dict):
        return "is not a JSON object"
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return "has a `hooks` value that is not an object"
    groups = hooks.get("UserPromptSubmit")
    if groups is not None and not isinstance(groups, list):
        return "has a `hooks.UserPromptSubmit` value that is not a list"
    for group in groups or []:
        inner = (group or {}).get("hooks") or [] if isinstance(group or {}, dict) else None
        if not isinstance(inner, list) or not all(isinstance(h, dict) for h in inner):
            return "has a `hooks.UserPromptSubmit` entry that is not a matcher group"
    return None


def codex_refusal(project: Path, dest_rel: str) -> str | None:
    """Why `--adapter codex` must not be registered here, or None. Reads only."""
    if not (project / ".git").exists():
        return ("--adapter codex needs a git repository: the hook command finds the tool "
                "with `git rev-parse --show-toplevel`")
    if not re.fullmatch(r"[A-Za-z0-9._/ -]+", dest_rel):
        return (f"--adapter codex cannot quote --dest {dest_rel!r} safely in a shell command; "
                "use letters, digits, `.`, `_`, `-`, `/` and spaces")
    toml = project / ".codex" / "config.toml"
    try:
        if toml.is_file() and CODEX_INLINE_HOOKS.search(toml.read_text(encoding="utf-8-sig")):
            return (f"{toml} already defines hooks inline, and Codex warns when one layer has "
                    "both forms; add the UserPromptSubmit entry there by hand (docs/hosts.md)")
    except (OSError, UnicodeDecodeError) as e:
        return f"cannot read {toml} ({e.__class__.__name__})"
    path = project.joinpath(*HOST_CONFIG["codex"])
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8-sig")
            problem = shape_problem(json.loads(raw) if raw.strip() else {})
        except (OSError, UnicodeDecodeError, ValueError) as e:
            problem = f"is not readable JSON ({e.__class__.__name__})"
        if problem:
            return f"{path} {problem}; fix it by hand, then re-run"
    return None


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


def merge_settings(path: Path, dest_rel: str, check: bool, adapter: str = "claude-code") -> str:
    trust = f". {CODEX_TRUST}" if adapter == "codex" else ""
    settings: dict = {}
    if path.exists():
        raw = path.read_text(encoding="utf-8-sig")  # tolerate a BOM
        try:
            settings = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            return (f"ABORT: {path} is not valid JSON ({e}). Refusing to touch it -- "
                    "fix it by hand, then re-run. Nothing else was changed.")
        problem = shape_problem(settings)
        if problem:
            return f"ABORT: {path} {problem}. Refusing to touch it."
        hook = registered_hook(settings)
        if hook is not None:
            old = str(hook.get("command", ""))
            if not interpreter_missing(old):
                return f"{adapter} adapter already registered (no change)"
            if check:
                return f"WOULD repair the adapter: `{old.split(' ', 1)[0]}` is not on PATH here"
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            hook["command"] = hook_command(dest_rel, adapter)
            path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            return (f"repaired the {adapter} adapter (`{old.split(' ', 1)[0]}` was not on PATH; "
                    f"now `{python_cmd()}`; backup at {path.name}.bak)" + trust)

    if check:
        return f"WOULD register the {adapter} prompt-submit adapter"

    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    hooks = settings.setdefault("hooks", {})
    if hooks.get("UserPromptSubmit") is None:   # absent, or an explicit null
        hooks["UserPromptSubmit"] = []
    hooks["UserPromptSubmit"].append(
        {"hooks": [{"type": "command", "command": hook_command(dest_rel, adapter)}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return f"registered the {adapter} prompt-submit adapter" + (
        f" (backup at {path.name}.bak)" if path.with_suffix(path.suffix + ".bak").exists() else ""
    ) + trust


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
    refusal = codex_refusal(project, dest_rel) if args.adapter == "codex" else None
    if refusal and not args.check:   # before anything is copied: nothing was changed
        print(f"ABORT: {refusal}. Nothing was changed.", file=sys.stderr)
        return 2
    print(f"agent-postbox -> {project}")

    if args.check:
        for name in PAYLOAD:
            print(f"  {'ok  ' if (dest / name).exists() else 'MISS'}  {dest_rel}/{name}")
        print(f"  {'ok  ' if (dest / 'hooks' / HOOK_MARKER).exists() else 'MISS'}  "
              f"{dest_rel}/hooks/{HOOK_MARKER}")
        print(f"  {'ok  ' if (project / args.mailbox).is_dir() else 'MISS'}  {args.mailbox}/")
        if refusal:
            print(f"  WOULD REFUSE: {refusal}")
        elif args.adapter:
            config = project.joinpath(*HOST_CONFIG[args.adapter])
            print(f"  {merge_settings(config, dest_rel, True, args.adapter)}")
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

    if args.adapter:
        config = project.joinpath(*HOST_CONFIG[args.adapter])
        print(f"  {merge_settings(config, dest_rel, False, args.adapter)}")
    else:
        print(f"  pickup is poll: {python_cmd()} "
              f"{dest_rel}/agent_mail.py list --to <you> --live")
        print("  (pass --adapter claude-code or --adapter codex to register a prompt-submit hook)")

    print("\nVerify pickup before trusting it -- silence proves nothing (PROTOCOL.md §8):")
    print(f"  {python_cmd()} {dest_rel}/selftest.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
