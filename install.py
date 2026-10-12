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
  2. Back up before writing. The first backup (`<name>.bak`) is the user's file as
     it was and is never overwritten; a later write goes to `<name>.bak.1`, `.bak.2`, ...
  3. Be idempotent. Re-running must not stack duplicate hook entries.
  4. Do not create host config unless `--adapter` asked for it.
  5. Do not attach a second mailbox. If an ancestor already has docs/agent-mail,
     this tree is occupied -- refuse rather than mint a nested owner.
  6. Do not install a mailbox into this spec checkout. The public repo is the
     tool, not an inbox -- use AGENT_MAIL_DIR or install into a project.
  7. A registered command must not be able to block a prompt. Claude Code and
     Codex both treat exit code 2 from a UserPromptSubmit hook as "block the
     prompt", and an interpreter that cannot open its script exits 2; so both
     commands end in `|| true`. An older claude-code registration without it
     (exactly the command this installer used to write) is upgraded in place,
     with a backup. `--adapter codex` is refused (before anything is copied)
     where the command could not resolve: no git repository, a destination the
     shell would re-interpret, or a `.codex/` this installer cannot merge safely.
     `--adapter claude-code` is refused the same way, exit 2 and before anything
     is copied, when `.claude/settings.json` cannot be merged safely.
  8. A file where a directory must go (the mailbox path, `--dest`, a parent of
     either, `<dest>/hooks`) is refused the same way, with any adapter or none.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
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
    # Claude Code: "exit 2 ... Blocks the prompt, so it never reaches Claude"
    # (https://code.claude.com/docs/en/hooks). `|| true`: see safety rule 7.
    return f'{python_cmd()} "$CLAUDE_PROJECT_DIR/{dest_rel}/hooks/{HOOK_MARKER}" || true'


# The claude-code command this installer wrote before it ended in `|| true`:
# `<interpreter> "$CLAUDE_PROJECT_DIR/<dest>/hooks/agent_mail_check.py"` and
# nothing else. Only this exact shape is upgraded; a hand-written command that
# merely mentions the adapter is the user's and is left alone.
LEGACY_CLAUDE_COMMAND = re.compile(
    r'\S+ "\$CLAUDE_PROJECT_DIR/[^"$`\\]+/hooks/' + re.escape(HOOK_MARKER) + '"')


def legacy_command(command: str, adapter: str) -> bool:
    """True for an older claude-code registration that can block a prompt."""
    return adapter == "claude-code" and LEGACY_CLAUDE_COMMAND.fullmatch(command) is not None


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


def not_a_config_dir(project: Path, adapter: str) -> str | None:
    """The host's config directory exists but is not a directory: no file can go in it."""
    folder = project / HOST_CONFIG[adapter][0]
    if folder.exists() and not folder.is_dir():
        return f"{folder} exists and is not a directory; move it, then re-run"
    return None


def escape_refusal(project: Path, dest_arg: str, mailbox_arg: str) -> str | None:
    """Why `--dest` or `--mailbox` could write outside the project, or None. Reads
    only. An absolute path, a `..` part, or a symlink (in the path, or at a file the
    installer would write) that leads out of the project is refused: both options are
    documented as relative to the project root."""
    root = project.resolve()
    for flag, arg in (("--dest", dest_arg), ("--mailbox", mailbox_arg)):
        text = arg.replace("\\", "/")
        if text.startswith("/") or (os.name == "nt" and re.match(r"^[A-Za-z]:", text)):
            return f"{flag} {arg!r} is an absolute path; give a path relative to the project"
        if ".." in text.split("/"):
            return f"{flag} {arg!r} contains '..'; give a path inside the project"
        rel = text.strip("/")
        if not (project / rel).resolve().is_relative_to(root):
            return f"{flag} {arg!r} leads outside the project through a symlink"
    return None


def path_refusal(project: Path, dest_rel: str, mailbox_rel: str) -> str | None:
    """Why the tool or the mailbox cannot be put where asked, or None. Reads only.
    A file (or dangling link) where a directory is needed -- the mailbox path
    itself, any parent of it or of `--dest`, `<dest>/hooks`, or a payload name that
    is a directory -- would otherwise surface as a traceback after the tool was
    copied."""
    def blocked(path: Path) -> bool:
        return (path.exists() or path.is_symlink()) and not path.is_dir()

    for rel in (dest_rel, mailbox_rel):
        here = project
        for part in Path(rel).parts:
            here = here / part
            if blocked(here):
                return f"{here} exists and is not a directory; move it, then re-run"
    dest = project / dest_rel
    if blocked(dest / "hooks"):
        return f"{dest / 'hooks'} exists and is not a directory; move it, then re-run"
    targets = [dest / n for n in PAYLOAD] + [dest / "hooks" / HOOK_MARKER,
                                              project / mailbox_rel / "PARTICIPANTS.md"]
    for target in targets:
        if not target.resolve().is_relative_to(project.resolve()):
            return f"{target} is a symlink leading outside the project; remove it, then re-run"
        if target.is_dir():
            return f"{target} is a directory, and a file belongs there; move it, then re-run"
    return None


def claude_refusal(project: Path) -> str | None:
    """Why `--adapter claude-code` must not be registered here, or None. Reads
    only, and runs before anything is copied (as `codex_refusal` does)."""
    problem = not_a_config_dir(project, "claude-code")
    path = project.joinpath(*HOST_CONFIG["claude-code"])
    if problem or not path.exists():
        return problem
    try:
        raw = path.read_text(encoding="utf-8-sig")  # tolerate a BOM
        settings = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        return (f"{path} is not valid JSON ({e}). Refusing to touch it -- "
                "fix it by hand, then re-run")
    except (OSError, UnicodeDecodeError) as e:
        return f"cannot read {path} ({e.__class__.__name__}). Refusing to touch it"
    problem = shape_problem(settings)
    return f"{path} {problem}. Refusing to touch it" if problem else None


def codex_refusal(project: Path, dest_rel: str) -> str | None:
    """Why `--adapter codex` must not be registered here, or None. Reads only."""
    if not (project / ".git").exists():
        return ("--adapter codex needs a git repository: the hook command finds the tool "
                "with `git rev-parse --show-toplevel`")
    if not re.fullmatch(r"[A-Za-z0-9._/ -]+", dest_rel):
        return (f"--adapter codex cannot quote --dest {dest_rel!r} safely in a shell command; "
                "use letters, digits, `.`, `_`, `-`, `/` and spaces")
    folder = not_a_config_dir(project, "codex")
    if folder:
        return folder
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


_PYTHON_NAME = re.compile(r"^python[0-9.]*$")


def interpreter_missing(command: str) -> bool:
    """True when the interpreter this installer would have written (a Python:
    bare `python`/`python3`, or a path) can't be found here -- e.g. an older
    install wrote bare `python` on a host that only has python3. Any other first
    word (a shell builtin such as `cd`, an `X=1` assignment, `env`, `sh`) is a
    command somebody wrote by hand: it is not ours to judge or to rewrite."""
    first = command.strip().split(" ", 1)[0].strip('"')
    if "/" in first:
        return not Path(first).is_file()
    return bool(_PYTHON_NAME.match(first)) and shutil.which(first) is None


def already_registered(settings: dict) -> bool:
    for group in settings.get("hooks", {}).get("UserPromptSubmit", []) or []:
        for hook in (group or {}).get("hooks", []) or []:
            if HOOK_MARKER in str(hook.get("command", "")):
                return True
    return False


def write_atomic(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace `path` with `data` so a reader, or a kill at any instant, sees the
    old file or the new one and never a prefix: write a temp file in the SAME
    directory (a rename across filesystems is a copy), fsync it, `os.replace` it.
    An existing file's permission bits are kept; a new one gets 0666 minus the
    umask, as `open()` would. A symlinked `path` is written through, not replaced."""
    target = Path(os.path.realpath(path))
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        pass    # a new file: `mode` as given, else 0666 minus the umask
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                 0o666 if mode is None else mode)
    try:
        with os.fdopen(fd, "wb") as f:
            if mode is not None:
                os.fchmod(f.fileno(), mode)     # the umask must not narrow it
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:   # make the rename itself durable; not every platform can open a directory
        dfd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def back_up(path: Path) -> str:
    """Copy `path` aside and return the backup's name (in the same directory). The
    first backup is `<name>.bak`; it is never overwritten, because it holds the file
    as the user wrote it. Later ones are `<name>.bak.1`, `.bak.2`, ... Written
    atomically too: a torn backup that a later run then kept would be worse than none."""
    n = 0
    while True:
        backup = path.with_name(f"{path.name}.bak" + (f".{n}" if n else ""))
        if not backup.exists() and not backup.is_symlink():
            break
        n += 1
    write_atomic(backup, path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    shutil.copystat(path, backup)
    return backup.name


def backup_note(path: Path, backup: str) -> str:
    """What the install output says about a backup; it states the keep-the-first rule."""
    first = f"{path.name}.bak"
    return f"backup at {backup}" + (
        "" if backup == first else f"; {first} still holds your original, which is never overwritten")


def write_settings(path: Path, settings: dict) -> None:
    write_atomic(path, (json.dumps(settings, indent=2) + "\n").encode("utf-8"))


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
            if not interpreter_missing(old) and legacy_command(old, adapter):
                if check:
                    return ("WOULD upgrade the adapter: its command does not end in `|| true`, "
                            "so a moved tool directory would block every prompt")
                backup = back_up(path)
                hook["command"] = old + " || true"
                write_settings(path, settings)
                return (f"upgraded the {adapter} adapter (the command now ends in `|| true`, so "
                        f"a moved tool directory cannot block a prompt; {backup_note(path, backup)})")
            if not interpreter_missing(old):
                return f"{adapter} adapter already registered (no change)"
            if check:
                return f"WOULD repair the adapter: `{old.split(' ', 1)[0]}` is not on PATH here"
            backup = back_up(path)
            hook["command"] = hook_command(dest_rel, adapter)
            write_settings(path, settings)
            return (f"repaired the {adapter} adapter (`{old.split(' ', 1)[0]}` was not on PATH; "
                    f"now `{python_cmd()}`; {backup_note(path, backup)})" + trust)

    if check:
        return f"WOULD register the {adapter} prompt-submit adapter"

    made = back_up(path) if path.exists() else None
    hooks = settings.setdefault("hooks", {})
    if hooks.get("UserPromptSubmit") is None:   # absent, or an explicit null
        hooks["UserPromptSubmit"] = []
    hooks["UserPromptSubmit"].append(
        {"hooks": [{"type": "command", "command": hook_command(dest_rel, adapter)}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    write_settings(path, settings)
    return f"registered the {adapter} prompt-submit adapter" + (
        f" ({backup_note(path, made)})" if made else "") + trust


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

    escaped = escape_refusal(project, args.dest, args.mailbox)
    if escaped and not args.check:
        print(f"ABORT: {escaped}. Nothing was changed.", file=sys.stderr)
        return 2

    occupied = None if escaped else occupied_ancestor(project, args.mailbox)
    if occupied is not None:
        msg = (f"ABORT: mailbox already at {occupied}. This tree is occupied -- "
               "installing here would attach a second owner. Use that mailbox, "
               "or pass a project that is not nested under it.")
        print(msg, file=sys.stderr)
        return 2

    dest_rel = args.dest.replace("\\", "/").strip("/")
    dest = project / dest_rel
    refusal = escaped or path_refusal(
        project, dest_rel, args.mailbox) or (
        codex_refusal(project, dest_rel) if args.adapter == "codex"
        else claude_refusal(project) if args.adapter == "claude-code" else None)
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
