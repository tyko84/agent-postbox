#!/usr/bin/env python3
"""Self-test with POSITIVE CONTROLS. Stdlib only. `python selftest.py`

Read PROTOCOL.md §8 before changing this file. The delivery hook fails silently
by design, so "printed nothing" is both the healthy-empty-inbox case and the
totally-broken case. A suite that only asserts silence proves nothing, and that
exact mistake shipped once and dropped every NOTICE.

Every test therefore runs against a throwaway mailbox, and the delivery tests
assert on CONTENT that must appear -- never merely on absence.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "hooks" / "agent_mail_check.py"
# The spec checkout ships install.py; an installed copy (tools/agent-postbox/)
# does not. Checks about the spec checkout itself only mean something there --
# run from an installed copy, "send into the spec checkout" walks up to the
# project's LIVE mailbox and files real mail.
IS_SPEC_CHECKOUT = (HERE / "install.py").is_file()
failures: list[str] = []
skipped: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        failures.append(f"{name}: {detail}" if detail else name)
        if detail:
            print(f"        {detail}")


def skip(name: str, reason: str) -> None:
    # Printed, never silent: a skipped check must not read as a pass.
    print(f"  SKIP  {name} ({reason})")
    skipped.append(name)


def send(box: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
    return subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "send", *args],
                          capture_output=True, text=True, env=env)


def pickup(box: Path, identity: str) -> str:
    env = {**os.environ, "AGENT_MAIL_DIR": str(box), "AGENT_MAIL_IDENTITY": identity}
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env)
    return r.stdout


def iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp) / "mail"
        box.mkdir()

        print("\nnegative control (must be silent, and proves nothing on its own)")
        check("empty inbox delivers nothing", pickup(box, "agent-a").strip() == "")
        env_noid = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        env_noid.pop("AGENT_MAIL_IDENTITY", None)
        r_noid = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env_noid)
        check("unset identity is silent (no default)", r_noid.returncode == 0 and r_noid.stdout.strip() == "")

        print("\npositive controls -- the ones that actually matter (PROTOCOL.md §8)")
        send(box, "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
             "--subject", "UNIQUE-ASK-TOKEN", "--body", "b")
        out = pickup(box, "agent-a")
        check("ASK is delivered", "UNIQUE-ASK-TOKEN" in out, repr(out[:200]))
        check("open ASK is flagged as awaiting reply", "awaiting your reply" in out)

        # The regression that shipped: NOTICE owes no reply, so gating delivery
        # on owes_reply() drops it and the symptom is indistinguishable silence.
        send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
             "--subject", "UNIQUE-NOTICE-TOKEN", "--body", "b")
        out = pickup(box, "agent-a")
        check("NOTICE is delivered (the bug that shipped)",
              "UNIQUE-NOTICE-TOKEN" in out, repr(out[:200]))
        check("NOTICE is not flagged as awaiting reply",
              out.count("awaiting your reply") == 1)

        print("\naddressing")
        check("mail to someone else is not delivered", "UNIQUE-ASK-TOKEN" not in pickup(box, "agent-c"))
        send(box, "--type", "NOTICE", "--from", "agent-a", "--to", "agent-a",
             "--subject", "SELF-TOKEN", "--body", "b")
        check("own mail is not echoed back", "SELF-TOKEN" not in pickup(box, "agent-a"))

        print("\nderived status (PROTOCOL.md §4)")
        r = send(box, "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "CLOSED-ASK-TOKEN", "--body", "b")
        ask_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        check("ASK is live before an answer", "CLOSED-ASK-TOKEN" in pickup(box, "agent-a"))
        send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b", "--re", ask_id,
             "--subject", "re", "--body", "b")
        check("answered ASK stops being delivered",
              "CLOSED-ASK-TOKEN" not in pickup(box, "agent-a"))

        print("\nULID id + reply_to / supersedes")
        r = send(box, "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "ULID-ASK-TOKEN", "--body", "b")
        ask_ulid = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        check("new id is a 26-char ULID",
              len(ask_ulid) == 26 and ask_ulid.isalnum() and ask_ulid.upper() == ask_ulid,
              repr(ask_ulid))
        # filename may contain slug; frontmatter id is ULID
        written = list(box.glob(f"{ask_ulid}-*.md"))
        check("filename embeds ULID but is not the sole identity",
              len(written) == 1 and f"id: {ask_ulid}" in written[0].read_text(encoding="utf-8"))
        send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
             "--reply-to", ask_ulid, "--subject", "ulid-re", "--body", "b")
        check("reply_to closes ASK like legacy --re",
              "ULID-ASK-TOKEN" not in pickup(box, "agent-a"))
        r = send(box, "--type", "CLAIM", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "CLAIM-A", "--body", "first", "--expires", iso(1))
        a_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        r = send(box, "--type", "CLAIM", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "CLAIM-B", "--body", "corrected", "--expires", iso(1),
                 "--supersedes", a_id)
        b_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        b_text = Path(next(box.glob(f"{b_id}-*.md"))).read_text(encoding="utf-8")
        check("supersedes is recorded structurally",
              f"supersedes: {a_id}" in b_text, repr(b_text[:200]))
        # legacy --re still works
        r = send(box, "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "LEGACY-RE-TOKEN", "--body", "b")
        legacy_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
             "--re", legacy_id, "--subject", "legacy", "--body", "b")
        check("legacy --re still closes ASK",
              "LEGACY-RE-TOKEN" not in pickup(box, "agent-a"))

        print("\nCANNOT_VERIFY + scoped verify (PROTOCOL.md §16)")
        r = send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
                 "--subject", "CANNOT-VERIFY-TOKEN", "--body", "no census",
                 "--result", "CANNOT_VERIFY",
                 "--reason", "INSUFFICIENT_PRIVILEGE",
                 "--namespace", "db:example",
                 "--do-not-infer", "substitute census from another role")
        cv_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        vr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "verify", cv_id],
            capture_output=True, text=True, env=env)
        check("verify surfaces CANNOT_VERIFY without inventing a census",
              vr.returncode == 0 and "result: CANNOT_VERIFY" in vr.stdout
              and "INSUFFICIENT_PRIVILEGE" in vr.stdout
              and "do not invent" in vr.stdout.lower(),
              repr(vr.stdout[:400]))

        # HEAD-vs-WIP fingerprint in a throwaway git repo
        repo = Path(tmp) / "scope-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
        target = repo / "tracked.txt"
        target.write_text("HEAD content\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
        import hashlib
        head_blob = subprocess.run(
            ["git", "show", "HEAD:tracked.txt"], cwd=repo, capture_output=True, check=True).stdout
        head_fp = hashlib.sha256(head_blob).hexdigest()
        # dirty worktree differs
        target.write_text("WIP only repair\n", encoding="utf-8")
        wt_fp = hashlib.sha256(target.read_bytes()).hexdigest()
        body = (
            "verification:\n"
            "  kind: command\n"
            "  description: Count twins\n"
            "  command: echo 188\n"
            "  expected:\n"
            "    operator: eq\n"
            "    value: 188\n"
        )
        body_path = Path(tmp) / "verify-body.md"
        body_path.write_text(body, encoding="utf-8")
        r = send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
                 "--subject", "SCOPE-FP-TOKEN", "--body-file", str(body_path),
                 "--evidence-scope", "tracked.txt",
                 "--fingerprint-head", head_fp,
                 "--fingerprint-worktree", wt_fp,
                 "--artifact-class", "HEAD")
        sc_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        vr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "verify", sc_id, "--repo", str(repo)],
            capture_output=True, text=True, env=env)
        check("HEAD fingerprint MATCH while worktree dirty is HEAD_MATCH for HEAD class",
              vr.returncode == 0 and "fingerprint_head: MATCH" in vr.stdout
              and "claim_currentness: HEAD_MATCH" in vr.stdout
              and "verification: (recipe only" in vr.stdout
              and "expected.value: 188" in vr.stdout,
              repr(vr.stdout[:500]))
        # wrong HEAD fingerprint → DIVERGED / exit 1
        r = send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
                 "--subject", "WIP-ONLY-TOKEN", "--body", "tip still broken",
                 "--evidence-scope", "tracked.txt",
                 "--fingerprint-head", "0" * 64,
                 "--fingerprint-worktree", wt_fp,
                 "--artifact-class", "HEAD")
        wip_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        vr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "verify", wip_id, "--repo", str(repo)],
            capture_output=True, text=True, env=env)
        check("wrong HEAD fingerprint with matching worktree is WIP_ONLY",
              vr.returncode == 1 and "claim_currentness: WIP_ONLY" in vr.stdout,
              repr(vr.stdout[:400]))
        # short prefix show refused
        short = cv_id[:8]
        sr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "show", short],
            capture_output=True, text=True, env=env)
        check("short prefix show does not ambiguously match",
              sr.returncode == 1 and "no message" in sr.stderr,
              repr((sr.stdout + sr.stderr)[:200]))

        print("\nverify hardening (PROTOCOL.md §17)")
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_mail", HERE / "agent_mail.py")
        am = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(am)

        # 1) tautology-resistant control
        src = "WHERE scope=\'all_staff\' AND active"  # intentional fixture
        # use a needle that appears once
        src = "WHERE scope='" + "all_staff" + "' AND active"
        check("naive substring would stay green under OR TRUE mutant",
              "scope='all_staff'" in src.replace("scope='all_staff'", "(scope='all_staff' OR TRUE)", 1))
        check("tautology-resistant control fails after OR TRUE broaden",
              am.predicate_survives_broaden(src, "scope='all_staff'") is False,
              "expected False after mutant")
        check("unbroadened lines still pass on original source",
              am.predicate_lines_unbroadened(src, "scope='all_staff'") is True)

        # 2) derived census — never set(KNOWN)==hardcoded
        toy = "def reader_a():\n    pass\ndef reader_b():\n    pass\n"
        derived = am.derived_symbol_census(toy, r"def (reader_\w+)")
        check("derived census comes from source",
              derived == {"reader_a", "reader_b"},
              repr(derived))
        # change-detector anti-pattern documented by showing inequality after source edit
        toy2 = toy + "def reader_c():\n    pass\n"
        check("derived census moves when source gains a reader",
              am.derived_symbol_census(toy2, r"def (reader_\w+)") == {"reader_a", "reader_b", "reader_c"})

        # 3) re-resolve HEAD — verify prints repo_head
        repo = Path(tmp) / "head-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "c"], cwd=repo, check=True, capture_output=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
        r = send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
                 "--subject", "HEAD-RESOLVE", "--body", "check head",
                 "--evidence-scope", "f.txt")
        hid = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        vr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "verify", hid, "--repo", str(repo)],
            capture_output=True, text=True, env=env)
        check("verify re-resolves repo HEAD",
              vr.returncode == 0 and f"repo_head: {head}" in vr.stdout,
              repr(vr.stdout[:300]))

        print("\nephemeral body refs (PROTOCOL.md §17)")
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "EPHEMERAL-TMP", "--body", "/tmp/agent-mail-body.md")
        check("argv body that is a /tmp path is refused",
              r.returncode == 2 and "ephemeral" in (r.stdout + r.stderr).lower(),
              repr((r.stdout + r.stderr)[:300]))
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "EPHEMERAL-STDIN", "--body", "/dev/stdin")
        check("argv body that is /dev/stdin is refused",
              r.returncode == 2 and "ephemeral" in (r.stdout + r.stderr).lower(),
              repr((r.stdout + r.stderr)[:300]))
        eph = Path(tmp) / "eph-body.txt"
        eph.write_text("/tmp/other-ephemeral.md\n", encoding="utf-8")
        # strip: content after strip is path with newline - our checker rejects multiline as non-ephemeral
        # write without trailing issues - single line path only
        eph.write_text("/tmp/other-ephemeral.md", encoding="utf-8")
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "EPHEMERAL-FILE", "--body-file", str(eph))
        check("--body-file whose content is an ephemeral path is refused",
              r.returncode == 2 and "ephemeral" in (r.stdout + r.stderr).lower(),
              repr((r.stdout + r.stderr)[:300]))
        # materialize OK: body-file under tmp with real prose
        real = Path(tmp) / "real-body.txt"
        real.write_text("durable prose bytes for the envelope\n", encoding="utf-8")
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "MATERIALIZED-OK", "--body-file", str(real))
        check("materialized body-file content is accepted",
              r.returncode == 0,
              repr((r.stdout + r.stderr)[:300]))


        print("\npath-looking --body (PROTOCOL.md §25)")
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        for path_body in ("/var/mail/note.txt", "./local.md", "../x.md", "~/secret.md"):
            r = subprocess.run(
                [sys.executable, str(HERE / "agent_mail.py"), "send",
                 "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
                 "--subject", "path-body", "--body", path_body],
                capture_output=True, text=True, env=env)
            check(f"path-looking --body refused: {path_body}",
                  r.returncode == 2 and "path-looking" in (r.stdout + r.stderr).lower(),
                  repr((r.stdout + r.stderr)[:300]))
        r_ok = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
             "--subject", "real-bytes", "--body", "hello-not-a-path"],
            capture_output=True, text=True, env=env)
        check("non-path --body still accepted",
              r_ok.returncode == 0 and "id:" in r_ok.stdout,
              repr((r_ok.stdout + r_ok.stderr)[:300]))

        print("\nstructured block (PROTOCOL.md §18)")
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "BARE-PARTIAL", "--body", "waiting",
                 "--blocked-action", "merge pr")
        check("partial structured block is refused",
              r.returncode == 2 and "missing" in (r.stdout + r.stderr).lower(),
              repr((r.stdout + r.stderr)[:300]))
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "FULL-BLOCK", "--body", "waiting on pin",
                 "--blocked-action", "merge pr",
                 "--blocker", "reviewer SHA-pin outstanding",
                 "--not-blocked", "docs edits under CLAIM",
                 "--unblock-condition", "reviewer PASS on tip",
                 "--safe-parallel-work", "selftest and PROTOCOL drafts")
        check("full structured block is accepted",
              r.returncode == 0,
              repr((r.stdout + r.stderr)[:300]))
        bid = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        sh = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "show", bid],
            capture_output=True, text=True, env=env)
        text = sh.stdout
        check("blocked_action round-trips in frontmatter",
              "blocked_action: merge pr" in text, repr(text[:400]))
        check("blocker round-trips",
              "blocker: reviewer SHA-pin outstanding" in text)
        check("not_blocked round-trips",
              "not_blocked: docs edits under CLAIM" in text)
        check("unblock_condition round-trips",
              "unblock_condition: reviewer PASS on tip" in text)
        check("safe_parallel_work round-trips",
              "safe_parallel_work: selftest and PROTOCOL drafts" in text)
        # incomplete helper unit
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_mail_sb", HERE / "agent_mail.py")
        am = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(am)
        check("incomplete helper lists four missing when one set",
              am.structured_block_incomplete({"blocked_action": "x"}) == [
                  "blocker", "not_blocked", "unblock_condition", "safe_parallel_work"])
        check("complete helper returns empty",
              am.structured_block_incomplete({
                  "blocked_action": "a", "blocker": "b", "not_blocked": "c",
                  "unblock_condition": "d", "safe_parallel_work": "e"}) == [])


        print("\nderived status + actionable inbox (PROTOCOL.md §19)")
        import importlib.util
        spec = importlib.util.spec_from_file_location("agent_mail_si", HERE / "agent_mail.py")
        am = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(am)
        # open ASK
        r = send(box, "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "INBOX-OPEN", "--body", "need answer")
        ask_id = [l.split("id: ", 1)[1] for l in r.stdout.splitlines() if l.startswith("id: ")][0]
        # use CLI inbox
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        ir = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "inbox", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        check("inbox shows open ASK owed to me",
              ir.returncode == 0 and "INBOX-OPEN" in ir.stdout and "[open" in ir.stdout,
              repr(ir.stdout[:400]))
        # answered ASK drops from inbox
        send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
             "--re", ask_id, "--subject", "re-open", "--body", "done")
        ir2 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "inbox", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        check("answered ASK leaves actionable inbox",
              "INBOX-OPEN" not in ir2.stdout,
              repr(ir2.stdout[:400]))
        # forensic list still has it
        lr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        check("forensic list still shows answered ASK",
              "INBOX-OPEN" in lr.stdout and "[answered" in lr.stdout,
              repr(lr.stdout[:500]))
        # blocked NOTICE in inbox
        send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
             "--subject", "INBOX-BLOCK", "--body", "waiting",
             "--blocked-action", "merge pr",
             "--blocker", "reviewer pin outstanding",
             "--not-blocked", "docs under CLAIM",
             "--unblock-condition", "reviewer PASS on tip",
             "--safe-parallel-work", "selftest drafts")
        ir3 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "inbox", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        check("inbox shows structured blocked NOTICE",
              "INBOX-BLOCK" in ir3.stdout and "[blocked" in ir3.stdout,
              repr(ir3.stdout[:500]))
        # mail to someone else not in my inbox
        send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-c",
             "--subject", "OTHER-INBOX", "--body", "not yours")
        ir4 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "inbox", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        check("inbox excludes mail addressed elsewhere",
              "OTHER-INBOX" not in ir4.stdout)




        print("\npickup → reply latency (PROTOCOL.md §23)")
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        ra = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
             "--subject", "LAT-ASK", "--body", "q"],
            capture_output=True, text=True, env=env)
        lat_ask = [l.split(":",1)[1].strip() for l in ra.stdout.splitlines() if l.startswith("id:")][0]
        import time; time.sleep(1.05)
        subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "ANSWER", "--from", "agent-a", "--to", "agent-b",
             "--reply-to", lat_ask, "--subject", "LAT-ANS", "--body", "a"],
            capture_output=True, text=True, env=env)
        lr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "latency", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        lat_lines = [ln for ln in lr.stdout.splitlines() if lat_ask in ln]
        check("latency reports numeric for dated ASK→ANSWER",
              lr.returncode == 0 and lat_lines and lat_lines[0][0].isdigit(),
              repr(lr.stdout[:400]))
        rb = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "ASK", "--from", "agent-b", "--to", "agent-a",
             "--subject", "LAT-OPEN", "--body", "q"],
            capture_output=True, text=True, env=env)
        open_id = [l.split(":",1)[1].strip() for l in rb.stdout.splitlines() if l.startswith("id:")][0]
        lr2 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "latency", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        open_lines = [ln for ln in lr2.stdout.splitlines() if open_id in ln]
        check("open ASK has no latency number (falsifier)",
              open_lines == [],
              repr(lr2.stdout[:400]))
        import importlib.util
        spec = importlib.util.spec_from_file_location("am_lat", HERE / "agent_mail.py")
        aml = importlib.util.module_from_spec(spec); spec.loader.exec_module(aml)
        os.environ["AGENT_MAIL_DIR"] = str(box)
        ask_id2 = aml._new_ulid()
        ans_id2 = aml._new_ulid()
        ask_body = (
            "---\n"
            f"id: {ask_id2}\n"
            "type: ASK\n"
            "from: agent-b\n"
            "to: agent-a\n"
            "date: 2026-01-01T00:00:00Z\n"
            "subject: nodate-ask\n"
            "---\n\n"
            "q\n"
        )
        ans_body = (
            "---\n"
            f"id: {ans_id2}\n"
            "type: ANSWER\n"
            "from: agent-a\n"
            "to: agent-b\n"
            f"reply_to: {ask_id2}\n"
            "subject: nodate-ans\n"
            "---\n\n"
            "a\n"
        )
        (box / f"{ask_id2}-lat-nodate-ask.md").write_text(ask_body, encoding="utf-8")
        (box / f"{ans_id2}-lat-nodate-ans.md").write_text(ans_body, encoding="utf-8")
        lr3 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "latency", "--to", "agent-a"],
            capture_output=True, text=True, env=env)
        nodate_lines = [ln for ln in lr3.stdout.splitlines() if ask_id2 in ln]
        check("missing answer date ⇒ CANNOT_VERIFY",
              nodate_lines and nodate_lines[0].startswith("CANNOT_VERIFY"),
              repr(nodate_lines))

        print("\nload order + closed type set (PROTOCOL.md §22)")
        import importlib.util
        spec = importlib.util.spec_from_file_location("am_load", HERE / "agent_mail.py")
        am = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(am)
        os.environ["AGENT_MAIL_DIR"] = str(box)
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        # plant a valid ULID NOTICE via send
        ok = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
             "--subject", "valid-ulid", "--body", "ok"],
            capture_output=True, text=True, env=env)
        check("valid send for order baseline", ok.returncode == 0 and "id:" in ok.stdout)
        valid_id = None
        for line in ok.stdout.splitlines():
            if line.startswith("id:"):
                valid_id = line.split(":", 1)[1].strip()
        # malformed id that string-sorts AFTER any ULID (~~~~)
        bad = box / "~~~~-malformed-newest-trap.md"
        bad.write_text(
            "---\n"
            "id: ~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
            "type: NOTICE\n"
            "from: agent-a\n"
            "to: agent-b\n"
            "date: 2099-01-01T00:00:00Z\n"
            "subject: malformed-trap\n"
            "---\n\n"
            "should never sort as newest\n",
            encoding="utf-8",
        )
        msgs, rejects = am.load_messages()
        ids = [am.message_id_raw(x) for x in msgs]
        # Fix A: malformed not admitted (INVALID ≠ OLD/NEW) — also never newest
        check("malformed not admitted into pickup",
              all(i != "~~~~~~~~~~~~~~~~~~~~~~~~~~" for i in ids),
              repr(ids[-5:]))
        check("malformed never-as-newest vs valid ULID",
              ids and ids[-1] == valid_id,
              repr(ids[-3:]))
        check("malformed emits REJECT",
              any("malformed id" in r for r in rejects),
              repr(rejects))
        # missing id → older-than-all
        missing = box / "no-id-file.md"
        missing.write_text(
            "---\n"
            "type: NOTICE\n"
            "from: agent-a\n"
            "to: agent-b\n"
            "date: 2000-01-01T00:00:00Z\n"
            "subject: missing-id\n"
            "---\n\n"
            "no id\n",
            encoding="utf-8",
        )
        msgs2, rejects2 = am.load_messages()
        check("missing id not admitted (Fix A)",
              all(am.message_id_raw(x) for x in msgs2),
              repr([am.message_id_raw(x) for x in msgs2[:3]]))
        check("missing id emits REJECT",
              any("missing id" in r for r in rejects2),
              repr(rejects2))
        # Fix A: REPLACE_ID must not appear in list ordered by string-sort
        replace = box / "zzzz-replace-id-trap.md"
        replace.write_text(
            "---\n"
            "id: REPLACE_ID\n"
            "type: NOTICE\n"
            "from: agent-a\n"
            "to: agent-b\n"
            "date: 2099-12-31T23:59:59Z\n"
            "subject: replace-id-trap\n"
            "---\n\n"
            "must not be admitted\n",
            encoding="utf-8",
        )
        lr_fixa = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list", "--to", "agent-b"],
            capture_output=True, text=True, env=env)
        check("Fix A: REPLACE_ID not in list pickup rows",
              "replace-id-trap" not in lr_fixa.stdout
              and "REPLACE_ID" not in lr_fixa.stdout,
              repr(lr_fixa.stdout[:400]))
        check("Fix A: REPLACE_ID REJECT loud",
              lr_fixa.returncode == 2 and "REPLACE_ID" in lr_fixa.stderr,
              repr(lr_fixa.stderr[:400]))
        replace.unlink(missing_ok=True)

        # unknown type ACK — loud refuse, not silent omit
        ack = box / "ack-silent-drop-trap.md"
        # use a valid ULID so only type is the reject
        ack_id = am._new_ulid()
        ack.write_text(
            "---\n"
            f"id: {ack_id}\n"
            "type: ACK\n"
            "from: agent-a\n"
            "to: agent-b\n"
            "date: 2026-01-01T00:00:00Z\n"
            "subject: ack-trap\n"
            "---\n\n"
            "must not silently vanish\n",
            encoding="utf-8",
        )
        lr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list", "--to", "agent-b"],
            capture_output=True, text=True, env=env)
        check("list EXIT nonzero on unknown type",
              lr.returncode == 2, repr(lr.returncode))
        check("ACK quarantined (never listed as live mail)",
              "ack-trap" not in lr.stdout,
              repr(lr.stdout[:400]))
        check("ACK REJECT loud on stderr",
              "unknown type" in lr.stderr and "ACK" in lr.stderr,
              repr(lr.stderr[:400]))
        # cleanup traps so later tests (doctor canary etc.) see a clean-ish box
        bad.unlink(missing_ok=True)
        missing.unlink(missing_ok=True)
        ack.unlink(missing_ok=True)

        print("\nparticipant capabilities (PROTOCOL.md §21)")
        # Write a PARTICIPANTS.md next to the temp mailbox
        parts = box.parent / "PARTICIPANTS.md"
        parts.write_text(
            "# Participants\n\n"
            "| Identity | Reachable | Mechanism | Verified | can | cannot |\n"
            "|---|---|---|---|---|---|\n"
            "| `agent-a` | **yes** | poll | observed | `review`, `ro` | `press` |\n"
            "| `agent-b` | **yes** | poll | observed |  | `press` |\n"
            "| `agent-c` | **yes** | poll | observed | `review` |  |\n",
            encoding="utf-8",
        )
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        # cwd-relative discovery also works; capability looks at mailbox parent
        import importlib.util
        spec = importlib.util.spec_from_file_location("am_caps", HERE / "agent_mail.py")
        am = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(am)
        loaded = am.load_participants(parts)
        check("agent-a can review",
              am.capability_status("agent-a", "review", loaded) == "can")
        check("agent-a cannot press",
              am.capability_status("agent-a", "press", loaded) == "cannot")
        check("agent-b undeclared review ⇒ CANNOT_VERIFY",
              am.capability_status("agent-b", "review", loaded) == "CANNOT_VERIFY")
        check("unknown identity ⇒ CANNOT_VERIFY",
              am.capability_status("agent-z", "review", loaded) == "CANNOT_VERIFY")
        # soft default refused
        soft_fail = False
        try:
            am._split_cap_cell("*")
        except ValueError:
            soft_fail = True
        check("soft can:* refused", soft_fail)
        # ask --capability excludes undeclared (Ledger falsifier)
        ar = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "ask",
             "--from", "agent-a",
             "--to", "agent-a,agent-b,agent-c",
             "--capability", "review",
             "--subject", "cap-route",
             "--body", "ping"],
            capture_output=True, text=True, env=env,
            cwd=str(box.parent),
        )
        check("ask --capability review succeeds with declared can",
              ar.returncode == 0 and ar.stdout.count("id:") >= 2,
              repr((ar.stdout + ar.stderr)[:400]))
        check("stderr reports CANNOT_VERIFY for undeclared",
              "agent-b: CANNOT_VERIFY" in ar.stderr,
              repr(ar.stderr[:200]))
        # forensic list: agent-b must not receive the capability-routed ASK
        lr_a = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list",
             "--to", "agent-a"],
            capture_output=True, text=True, env=env, cwd=str(box.parent))
        lr_b = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list",
             "--to", "agent-b"],
            capture_output=True, text=True, env=env, cwd=str(box.parent))
        lr_c = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list",
             "--to", "agent-c"],
            capture_output=True, text=True, env=env, cwd=str(box.parent))
        check("ask landed for agent-a (declared can)",
              "cap-route" in lr_a.stdout, repr(lr_a.stdout[:300]))
        check("ask landed for agent-c (declared can)",
              "cap-route" in lr_c.stdout, repr(lr_c.stdout[:300]))
        check("ask excludes undeclared agent-b",
              "cap-route" not in lr_b.stdout, repr(lr_b.stdout[:300]))
        # all-undeclared refuse
        ar2 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "ask",
             "--from", "agent-a", "--to", "agent-b",
             "--capability", "review", "--subject", "nobody",
             "--body", "ping"],
            capture_output=True, text=True, env=env, cwd=str(box.parent),
        )
        check("ask refuses when no declared can remains",
              ar2.returncode == 2 and "CANNOT_VERIFY" in ar2.stderr,
              repr((ar2.stdout + ar2.stderr)[:300]))
        # CLI capability print
        cr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "capability",
             "--identity", "agent-b", "--capability", "review"],
            capture_output=True, text=True, env=env, cwd=str(box.parent),
        )
        check("capability CLI prints CANNOT_VERIFY",
              cr.returncode == 0 and cr.stdout.strip() == "CANNOT_VERIFY",
              repr(cr.stdout + cr.stderr))

        print("\ndoctor + pickup canary (PROTOCOL.md §20)")
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        dr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "doctor"],
            capture_output=True, text=True, env=env)
        check("doctor fails without live canary",
              dr.returncode == 2 and "canary: MISSING_OR_STALE" in dr.stdout
              and "authorship: CANNOT_VERIFY" in dr.stdout,
              repr(dr.stdout[:500]))
        check("doctor never claims a named author",
              "author:" not in dr.stdout.lower())
        cr = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "canary",
             "--from", "agent-a", "--to", "agent-a", "--hours", "24"],
            capture_output=True, text=True, env=env)
        check("canary send succeeds",
              cr.returncode == 0 and "id:" in cr.stdout,
              repr((cr.stdout + cr.stderr)[:300]))
        dr2 = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "doctor"],
            capture_output=True, text=True, env=env)
        check("doctor passes with live canary",
              dr2.returncode == 0 and "canary: OK" in dr2.stdout
              and "authorship: CANNOT_VERIFY" in dr2.stdout
              and dr2.stdout.rstrip().endswith("PASS"),
              repr(dr2.stdout[:500]))
        import importlib.util
        spec = importlib.util.spec_from_file_location("am_doc", HERE / "agent_mail.py")
        am = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(am)
        real = am.Message(Path("x.md"), {"from": "alice", "type": "NOTICE"}, "hi")
        check("unstamped from yields CANNOT_VERIFY",
              am.authorship_status(real) == "CANNOT_VERIFY")

        print("\nCLAIM expiry (PROTOCOL.md §5)")
        check("CLAIM without --expires is refused",
              send(box, "--type", "CLAIM", "--from", "agent-b", "--to", "agent-a",
                   "--subject", "x", "--body", "b").returncode != 0)
        send(box, "--type", "CLAIM", "--from", "agent-b", "--to", "agent-a",
             "--subject", "LIVE-CLAIM-TOKEN", "--body", "b", "--expires", iso(1))
        send(box, "--type", "CLAIM", "--from", "agent-b", "--to", "agent-a",
             "--subject", "DEAD-CLAIM-TOKEN", "--body", "b", "--expires", iso(-1))
        out = pickup(box, "agent-a")
        check("unexpired CLAIM is delivered", "LIVE-CLAIM-TOKEN" in out)
        check("expired CLAIM is not delivered", "DEAD-CLAIM-TOKEN" not in out)

        print("\nvalidation")
        check("unknown type is refused",
              send(box, "--type", "SHOUT", "--from", "a", "--to", "b",
                   "--subject", "x", "--body", "b").returncode != 0)
        check("empty body is refused",
              send(box, "--type", "NOTICE", "--from", "a", "--to", "b",
                   "--subject", "x", "--body", "  ").returncode != 0)
        check("unparseable --expires is refused",
              send(box, "--type", "CLAIM", "--from", "a", "--to", "b", "--subject", "x",
                   "--body", "b", "--expires", "next tuesday").returncode != 0)

        print("\nthe hook must never block a prompt (PROTOCOL.md §9)")
        env = {**os.environ, "AGENT_MAIL_DIR": str(Path(tmp) / "does-not-exist")}
        r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env)
        check("missing mailbox exits 0 and is silent", r.returncode == 0 and r.stdout.strip() == "")
        bad = box / "corrupt.md"
        bad.write_text("no front matter at all", encoding="utf-8")
        env = {**os.environ, "AGENT_MAIL_DIR": str(box), "AGENT_MAIL_IDENTITY": "agent-a"}
        r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env)
        check("a corrupt message does not break delivery of good ones",
              r.returncode == 0 and "LIVE-CLAIM-TOKEN" in r.stdout)
        bad.unlink(missing_ok=True)  # §22: corrupt envelopes REJECT list; do not leave them

        print("\nmailbox discovery (install/tooling, not protocol)")
        env_walk = {k: v for k, v in os.environ.items()
                    if k not in ("AGENT_MAIL_DIR", "AGENT_MAIL_PROJECT",
                                 "CLAUDE_PROJECT_DIR")}
        disc = Path(tmp) / "disc"
        outer = disc / "outer"
        inner = outer / "inner"
        (outer / "docs" / "agent-mail").mkdir(parents=True)
        (inner / "docs" / "agent-mail").mkdir(parents=True)
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
             "--subject", "NEAR-TOKEN", "--body", "b"],
            capture_output=True, text=True, env=env_walk, cwd=str(inner))
        inner_hits = list((inner / "docs" / "agent-mail").glob("*near-token*"))
        outer_hits = list((outer / "docs" / "agent-mail").glob("*near-token*"))
        check("nearest mailbox wins",
              r.returncode == 0 and bool(inner_hits) and not outer_hits,
              repr((r.stdout + r.stderr)[:300]))

        parent = disc / "parent"
        child = parent / "child"
        (parent / "docs" / "agent-mail").mkdir(parents=True)
        (child / ".git").mkdir(parents=True)
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
             "--subject", "OCCUPIED-TOKEN", "--body", "b"],
            capture_output=True, text=True, env=env_walk, cwd=str(child))
        child_hits = list((child / "docs" / "agent-mail").glob("*occupied-token*"))
        parent_hits = list((parent / "docs" / "agent-mail").glob("*occupied-token*"))
        check("nested git send does not mint a second mailbox",
              r.returncode != 0 and not child_hits and not parent_hits
              and "second owner" in (r.stdout + r.stderr).lower(),
              repr((r.stdout + r.stderr)[:300]))

        lone = disc / "lone"
        (lone / ".git").mkdir(parents=True)
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
             "--subject", "FIRST-OWNER-TOKEN", "--body", "b"],
            capture_output=True, text=True, env=env_walk, cwd=str(lone))
        lone_hits = list((lone / "docs" / "agent-mail").glob("*first-owner-token*"))
        check("git root with no ancestor mailbox may create first owner",
              r.returncode == 0 and bool(lone_hits),
              repr((r.stdout + r.stderr)[:300]))

        if IS_SPEC_CHECKOUT:
            nested = outer / "nested-no-box"
            nested.mkdir()
            r = subprocess.run(
                [sys.executable, str(HERE / "install.py"), str(nested)],
                capture_output=True, text=True, env=env_walk)
            check("install under an occupied tree aborts",
                  r.returncode != 0 and "occupied" in (r.stdout + r.stderr).lower(),
                  repr((r.stdout + r.stderr)[:300]))
        else:
            skip("install under an occupied tree aborts", "installed copy has no install.py")

        missing = Path(tmp) / "no-such-mailbox"
        env_miss = {**os.environ, "AGENT_MAIL_DIR": str(missing)}
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list", "--live"],
            capture_output=True, text=True, env=env_miss)
        check("missing store list exits 2 with NO MAILBOX",
              r.returncode == 2 and "NO MAILBOX" in (r.stdout + r.stderr)
              and str(missing) in (r.stdout + r.stderr),
              repr((r.stdout + r.stderr)[:300]))

        env_ok = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list", "--live"],
            capture_output=True, text=True, env=env_ok)
        check("list names AGENT_MAIL_DIR mailbox",
              r.returncode == 0 and "# mailbox:" in r.stdout and "AGENT_MAIL_DIR" in r.stdout,
              repr(r.stdout[:300]))

        if IS_SPEC_CHECKOUT:
            r = subprocess.run(
                [sys.executable, str(HERE / "agent_mail.py"), "send",
                 "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
                 "--subject", "SPEC-TOKEN", "--body", "b"],
                capture_output=True, text=True, env=env_walk, cwd=str(HERE))
            spec_dir = HERE / "docs" / "agent-mail"
            spec_hits = list(spec_dir.glob("*spec-token*")) if spec_dir.is_dir() else []
            check("send refuses to file into the spec checkout",
                  r.returncode == 2 and not spec_hits
                  and "spec" in (r.stdout + r.stderr).lower(),
                  repr((r.stdout + r.stderr)[:400]))

            r = subprocess.run(
                [sys.executable, str(HERE / "install.py"), str(HERE)],
                capture_output=True, text=True, env=env_walk)
            check("install into the spec checkout aborts",
                  r.returncode != 0 and "spec" in (r.stdout + r.stderr).lower(),
                  repr((r.stdout + r.stderr)[:400]))
        else:
            skip("send refuses to file into the spec checkout",
                 "installed copy; would file into the live project mailbox")
            skip("install into the spec checkout aborts", "installed copy has no install.py")

        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-a", "--to", "agent-b",
             "--subject", "TYPO-TOKEN", "--body", "b"],
            capture_output=True, text=True, env=env_miss)
        check("send with missing AGENT_MAIL_DIR does not mkdir",
              r.returncode == 2 and "NO MAILBOX" in (r.stdout + r.stderr)
              and not missing.exists(),
              repr((r.stdout + r.stderr)[:300]))

        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "show", "no-such-id"],
            capture_output=True, text=True, env=env_miss)
        check("show missing store exits 2 with NO MAILBOX",
              r.returncode == 2 and "NO MAILBOX" in (r.stdout + r.stderr),
              repr((r.stdout + r.stderr)[:300]))
        print("\nbody path — shell-meta-before-argv (PROTOCOL.md §26)")
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "MULTI-LINE-ARGV", "--body", "line1\nline2")
        check("multi-line --body is refused",
              r.returncode == 2 and not list(box.glob("*multi-line-argv*")),
              repr((r.stdout + r.stderr)[:300]))
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "BACKTICK-ARGV", "--body", "see `git merge` please")
        check("argv --body with backtick is refused",
              r.returncode == 2 and not list(box.glob("*backtick-argv*")),
              repr((r.stdout + r.stderr)[:300]))
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "DOLLAR-ARGV", "--body", "val=$HOME")
        check("argv --body with $ is refused",
              r.returncode == 2 and not list(box.glob("*dollar-argv*")),
              repr((r.stdout + r.stderr)[:300]))
        # PROTOCOL.md §26 Ledger falsifier control: refuse $() so EXIT 0 cannot
        # store a body stripped of shell-meta while the shell said not found.
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "DOLLAR-PAREN-ARGV", "--body", "run $(whoami) please")
        check("argv --body with $() is refused",
              r.returncode == 2 and not list(box.glob("*dollar-paren-argv*")),
              repr((r.stdout + r.stderr)[:300]))
        body_path = Path(tmp) / "safe-body.md"
        dangerous = "NOTICE: run `git merge worktree-some-feature-branch` then echo $HOME"
        body_path.write_text(dangerous, encoding="utf-8")
        before = set(box.glob("*.md"))
        r = send(box, "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
                 "--subject", "BODY-FILE-LITERAL", "--body-file", str(body_path))
        after = set(box.glob("*.md")) - before
        check("--body-file stores backticks and $ literally",
              r.returncode == 0 and len(after) == 1
              and dangerous in after.pop().read_text(encoding="utf-8"),
              repr((r.stdout + r.stderr)[:300]))
        # stdin via --body-file -
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "send",
             "--type", "NOTICE", "--from", "agent-b", "--to", "agent-a",
             "--subject", "STDIN-BODY", "--body-file", "-"],
            input="hello from stdin with `ticks` and $HOME\n",
            capture_output=True, text=True, env=env)
        hits = list(box.glob("*stdin-body*"))
        check("--body-file - reads stdin literally",
              r.returncode == 0 and hits
              and "`ticks`" in hits[0].read_text(encoding="utf-8")
              and "$HOME" in hits[0].read_text(encoding="utf-8"),
              repr((r.stdout + r.stderr)[:300]))

        print("\ndisplay alias ≠ writer token (PROTOCOL.md §27)")
        # Caps-only PARTICIPANTS may already sit at box.parent from §21 tests —
        # ensure Aliases table is present so fan-out is not shadowed.
        parts = box.parent / "PARTICIPANTS.md"
        alias_table = (
            "## Aliases (PROTOCOL.md §27)\n\n"
            "| Alias | Writers |\n|---|---|\n"
            "| `team` | `agent-a`, `agent-b` |\n"
        )
        if parts.is_file():
            cur = parts.read_text(encoding="utf-8")
            if "## Aliases" not in cur:
                parts.write_text(cur.rstrip() + "\n\n" + alias_table, encoding="utf-8")
        else:
            parts.write_text(
                "# Participants\n\n| Identity | Reachable | Mechanism | Verified | can | cannot |\n"
                "|---|---|---|---|---|---|\n"
                "| `agent-a` | **no** | poll | not observed | `review` | `press` |\n"
                "| `agent-b` | **no** | poll | not observed |  | `press` |\n\n"
                + alias_table,
                encoding="utf-8",
            )
        r = send(box, "--type", "ASK", "--from", "outsider", "--to", "team",
                 "--subject", "ALIAS-ASK", "--body", "ping team alias")
        check("ask --to alias fans out to writers",
              r.returncode == 0
              and list(box.glob("*alias-ask*")),
              repr((r.stdout + r.stderr)[:300]))
        asks = list(box.glob("*alias-ask*"))
        # Fan-out: at least one ASK per mapped writer (to: agent-a and agent-b)
        tos = set()
        for path in asks:
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.lower().startswith("to:"):
                    tos.add(line.split(":", 1)[1].strip().lower())
        check("fan-out writers are agent-a and agent-b",
              tos == {"agent-a", "agent-b"},
              repr(tos))
        # ANSWER under a writer token (not the alias)
        ask_id = None
        for path in asks:
            text = path.read_text(encoding="utf-8")
            if "to: agent-a" in text.lower() or "to:agent-a" in text.lower():
                for line in text.splitlines():
                    if line.lower().startswith("id:"):
                        ask_id = line.split(":", 1)[1].strip()
                        break
                break
        check("captured ask id for writer agent-a", bool(ask_id), repr(ask_id))
        send(box, "--type", "ANSWER", "--from", "agent-a", "--to", "outsider",
             "--re", ask_id, "--subject", "ALIAS-ANSWER", "--body", "acked by writer")
        env = {**os.environ, "AGENT_MAIL_DIR": str(box)}
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list",
             "--from", "team"],
            capture_output=True, text=True, env=env)
        check("list --from alias sees writer ANSWER (not alias-zero silent)",
              r.returncode == 0 and "ALIAS-ANSWER" in (r.stdout + r.stderr),
              repr((r.stdout + r.stderr)[:500]))
        r = subprocess.run(
            [sys.executable, str(HERE / "agent_mail.py"), "list",
             "--to", "team", "--live"],
            capture_output=True, text=True, env=env)
        # agent-b ASK may still be open; agent-a answered — must not be empty solely
        # because alias from: is zero. At least the open agent-b ASK should show,
        # and we must not claim unanswered for the answered ask id.
        check("list --to alias expands to writer inboxes",
              r.returncode == 0 and ("ALIAS-ASK" in r.stdout or "agent-b" in r.stdout.lower()),
              repr((r.stdout + r.stderr)[:500]))


    print("\nversion")
    rv = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "--version"],
                        capture_output=True, text=True)
    check("--version prints the tool name and a semantic version",
          rv.returncode == 0 and rv.stdout.startswith("agent-postbox ")
          and rv.stdout.strip().split()[-1].count(".") == 2, repr(rv.stdout))

    summary = 'FAILED: ' + '; '.join(failures) if failures else 'all checks passed'
    if skipped and not failures:
        summary += f" ({len(skipped)} spec-checkout-only check(s) skipped)"
    print(f"\n{summary}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
