#!/usr/bin/env python3
"""How much text Postbox puts in front of a model, measured with the real pickup hook and CLI.

  python3 context_cost.py scenarios <checkout>             bytes per situation, throwaway mailbox
  python3 context_cost.py replay <checkout> <mailbox dir>  bytes if one prompt followed each message
  python3 context_cost.py tokens <checkout>                real model-input tokens (3 `claude -p` runs)

`HOOKFILE=<path>` measures another copy of the hook (it must sit in a tree whose agent_mail.py is
beside it or one level up). Bytes are UTF-8 stdout length; they are NOT tokens. `tokens` asks the host
for the usage it reports (input + cache creation + cache read) and subtracts a run with nothing
injected; it uses the owner's existing Claude Code subscription, three tiny haiku calls. Limits are
printed with each result. The tool path is printed in the hook's text, so absolute numbers depend on
where the tool is installed: compare variants only at the same path length.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# Variables that select which repository git acts on; a throwaway project must not inherit them.
GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
                 "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES")

GIT_CONFIG_ENV = re.compile(r"^(GIT_CONFIG_(PARAMETERS|COUNT|KEY_\d+|VALUE_\d+)|GIT_TEMPLATE_DIR)$")


def hook_file(repo: str) -> str:
    return os.environ.get("HOOKFILE", f"{repo}/hooks/agent_mail_check.py")


def run_hook(repo: str, box: str, who: str, cwd: str) -> str:
    env = {**os.environ, "AGENT_MAIL_DIR": box, "AGENT_MAIL_IDENTITY": who}
    done = subprocess.run([sys.executable, "-I", hook_file(repo)], capture_output=True, text=True,
                          env=env, cwd=cwd, input="", check=False)
    return done.stdout


def cli(repo: str, box: str, cwd: str, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AGENT_MAIL_DIR": box}
    return subprocess.run([sys.executable, "-I", f"{repo}/agent_mail.py", *argv],
                          capture_output=True, text=True, env=env, cwd=cwd, check=False)


def send(repo: str, box: str, cwd: str, kind: str, frm: str, to: str, subject: str,
         body: str = "Short body.", **extra: str) -> str:
    argv = ["send", "--type", kind, "--from", frm, "--to", to, "--subject", subject, "--body", body]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", value]
    done = cli(repo, box, cwd, *argv)
    if done.returncode != 0:
        raise SystemExit(done.stderr)
    return next(ln.split()[1] for ln in done.stdout.splitlines() if ln.startswith("id:"))


def soon(minutes: int) -> str:
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def nbytes(text: str) -> int:
    return len(text.encode())


def scenarios(repo: str) -> None:
    work = tempfile.mkdtemp()
    box = f"{work}/box"
    os.mkdir(box)

    def s(*a: str, **k: str) -> str:
        return send(repo, box, work, *a, **k)

    def row(label: str, size: int, note: str = "") -> None:
        print(f"{size:>6} B  {label}  {note}")

    row("pickup, no mail", nbytes(run_hook(repo, box, "bob", work)))
    ask = s("ASK", "alice", "bob", "Which file owns billing retries?", "Please name the module.")
    row("initial pickup: 1 owed ASK", nbytes(run_hook(repo, box, "bob", work)))
    row("repeated pickup, same ASK still owed", nbytes(run_hook(repo, box, "bob", work)),
        "same bytes on every prompt")
    s("ANSWER", "bob", "alice", "re: billing", "It is billing/retry.py.", reply_to=ask)
    row("after the ANSWER: the asker's pickup", nbytes(run_hook(repo, box, "alice", work)),
        "live for 7 days, repeated each prompt")
    claim = s("CLAIM", "alice", "bob", "editing billing/retry.py", scope="file:billing/retry.py",
              expires=soon(60))
    row("CLAIM: the recipient's pickup", nbytes(run_hook(repo, box, "bob", work)))
    s("CLAIM", "alice", "bob", "renewed", scope="file:billing/retry.py", expires=soon(120),
      supersedes=claim)
    row("after renewal: the recipient's pickup", nbytes(run_hook(repo, box, "bob", work)))
    for view in (("status",), ("status", "--json"), ("doctor",), ("list",), ("inbox", "--to", "bob"),
                 ("show", ask)):
        row(" ".join(view), nbytes(cli(repo, box, work, *view).stdout))
    for n in (5, 10, 20, 40):
        for i in range(n):
            s("NOTICE", "alice", "erin", f"FINDING {i}: the retry loop in module {i} never backs off",
              "Detail text for the finding. " * 3)
        row(f"pickup with {n} cumulative live NOTICEs", nbytes(run_hook(repo, box, "erin", work)))


def replay(repo: str, source: str) -> None:
    """After each message (in id order) run the hook on the mailbox as it was; sum the bytes.
    Limits: the hook uses the real clock, so a lapsed CLAIM is not live in the replay (claims are
    undercounted); one prompt per message is an assumption, not a measurement of any session."""
    files = sorted(glob.glob(f"{source}/0*.md"))
    for who in ("collaborator", "codex-coordinator"):
        work = tempfile.mkdtemp()
        box = f"{work}/box"
        os.mkdir(box)
        total = peak = shown = 0
        for path in files:
            shutil.copy(path, box)
            size = nbytes(run_hook(repo, box, who, box))
            total += size
            peak = max(peak, size)
            shown += 1 if size else 0
        print(f"{who}: {len(files)} messages; total {total} B over {len(files)} prompts "
              f"(largest {peak} B, {shown} non-empty)")


def tokens(repo: str) -> None:
    def injected(n_notices: int, ask: bool) -> str:
        work = tempfile.mkdtemp()
        box = f"{work}/box"
        os.mkdir(box)
        if ask:
            send(repo, box, work, "ASK", "alice", "bob", "Which file owns billing retries?")
        for i in range(n_notices):
            send(repo, box, work, "NOTICE", "alice", "bob",
                 f"FINDING {i}: the retry loop in module {i} never backs off", "Detail text. " * 3)
        return run_hook(repo, box, "bob", work)

    results = {}
    for name, text in (("A_none", ""), ("B_one_ask", injected(0, True)),
                       ("C_20_notices", injected(20, False))):
        proj = Path(tempfile.mkdtemp())
        (proj / ".claude").mkdir()
        (proj / "inject.txt").write_text(text, encoding="utf-8")
        hooks = [{"type": "command", "command": f"cat {proj}/inject.txt"}]
        (proj / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": hooks}]}}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("AGENT_MAIL") and k not in GIT_REPO_VARS
               and not GIT_CONFIG_ENV.match(k)}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "-q"], cwd=proj, check=False, env=env)
        done = subprocess.run(["claude", "-p", "Reply with the single word: ok", "--model", "haiku",
                               "--output-format", "json", "--max-turns", "1"], cwd=proj,
                              capture_output=True, text=True, timeout=180, env=env, check=False)
        use = json.loads(done.stdout).get("usage", {})
        total = (use.get("input_tokens", 0) + use.get("cache_creation_input_tokens", 0)
                 + use.get("cache_read_input_tokens", 0))
        results[name] = {"injected_bytes": nbytes(text), "total_input_tokens": total}
    base = results["A_none"]["total_input_tokens"]
    for name, got in results.items():
        added = got["total_input_tokens"] - base
        per = f"{added / got['injected_bytes']:.2f} tokens/byte" if got["injected_bytes"] else "baseline"
        print(f"{name}: {got['injected_bytes']} B injected, {got['total_input_tokens']} input tokens, "
              f"+{added} ({per})")
    print("one run per variant; counts as reported by the host, haiku; not a tokenizer")


def main(argv: list[str]) -> int:
    modes = {"scenarios": (scenarios, 1), "replay": (replay, 2), "tokens": (tokens, 1)}
    if len(argv) < 3 or argv[1] not in modes or len(argv) - 2 != modes[argv[1]][1]:
        print(__doc__, file=sys.stderr)
        return 2
    func, _ = modes[argv[1]]
    # the tool runs from throwaway directories, so every path must be absolute
    func(*(os.path.abspath(a) for a in argv[2:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
