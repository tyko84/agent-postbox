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


        print("\nscope lock is kernel-held: reclaimed, never broken (PROTOCOL.md §30)")
        import fcntl
        import hashlib
        import json
        import time
        from concurrent.futures import ThreadPoolExecutor
        SCOPE = "file:contested.py"

        def lock_box(name: str) -> tuple[Path, Path]:
            b = Path(tmp) / f"scope-lock-{name}"
            b.mkdir()
            return b, b / (".scope." + hashlib.sha256(SCOPE.encode()).hexdigest()[:24] + ".lock")

        def claim(b: Path, who: str) -> subprocess.CompletedProcess:
            return send(b, "--type", "CLAIM", "--from", who, "--to", "all",
                        "--subject", f"claim by {who}", "--body", "b",
                        "--scope", SCOPE, "--expires", iso(1))

        def held_claims(b: Path) -> int:
            env = {**os.environ, "AGENT_MAIL_DIR": str(b)}
            r = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "status", "--json"],
                               capture_output=True, text=True, env=env)
            return int(json.loads(r.stdout)["claims"]["held"])

        long_ago = time.time() - 120

        # (a) a lock with a LIVE holder is never broken, however old the file is.
        # This process is the holder: it plants the lock file, ages it past any
        # staleness threshold, and holds flock on it while rivals try.
        abox, alock = lock_box("live")
        alock.write_text("0\n", encoding="utf-8")
        os.utime(alock, (long_ago, long_ago))
        holder = os.open(alock, os.O_RDWR)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rivals = [claim(abox, f"rival-{i}") for i in range(3)]
        check("a lock held by a live process is not broken even when 120 s old: rivals exit 3",
              all(r.returncode == 3 and "being claimed right now" in r.stderr for r in rivals),
              repr([(r.returncode, r.stderr.strip()[:80]) for r in rivals]))
        check("control: rivals wrote nothing and the holder's lock file is still there",
              not list(abox.glob("*.md")) and alock.exists() and held_claims(abox) == 0)
        os.close(holder)  # the holder exits: the kernel releases its lock
        r = claim(abox, "after-holder")
        check("control: once the holder is gone the scope is claimable and the lock file is removed",
              r.returncode == 0 and not alock.exists() and held_claims(abox) == 1, r.stderr)

        # (b) a lock file whose holder DIED is reclaimed at once, however fresh:
        # a crashed claimant must not block its scope for 60 s.
        bbox, block = lock_box("dead")
        block.write_text("0\n", encoding="utf-8")  # mtime is now; nobody holds it
        r = claim(bbox, "survivor")
        check("a fresh lock file with no living holder is reclaimed at once (no 60 s wait)",
              r.returncode == 0 and not block.exists() and held_claims(bbox) == 1, r.stderr)

        # (c) the documented race: N simultaneous claimants all find a dead
        # holder's 120 s old lock. Exactly one may proceed into check-and-publish.
        cbox, clock = lock_box("race")
        clock.write_text("0\n", encoding="utf-8")
        os.utime(clock, (long_ago, long_ago))
        with ThreadPoolExecutor(12) as pool:
            res = list(pool.map(lambda i: claim(cbox, f"racer-{i}"), range(12)))
        rcs = [r.returncode for r in res]
        check("12 racing claimants on a dead holder's 120 s old lock: exactly one wins, the rest exit 3",
              rcs.count(0) == 1 and all(rc in (0, 3) for rc in rcs), repr(rcs))
        check("losers are refused with a lock or a held-rival message, nothing else",
              all("being claimed right now" in r.stderr or "already held by" in r.stderr
                  for r in res if r.returncode == 3),
              repr([r.stderr.strip()[:80] for r in res if r.returncode != 0]))
        check("control: exactly one claim file and one held claim on the scope",
              len(list(cbox.glob("*.md"))) == 1 and held_claims(cbox) == 1,
              repr(sorted(p.name for p in cbox.iterdir())))
        stray = [p.name for p in cbox.iterdir() if p.name.startswith(".scope.")]
        check("no .scope.* file is left behind after the race", not stray, repr(stray))
        r = claim(cbox, "latecomer")
        check("control: a later rival is refused by the winner's held claim", r.returncode == 3
              and "already held by" in r.stderr, r.stderr)

        print("\nkeyed sends are kernel-held: orphan taken over at once, live never (PROTOCOL.md §31)")
        KEY_ULID = "01ARZ3NDEKTSV4RRFFQ69G5KEY"

        def key_box(name: str, fmt: str, key: str = "K") -> tuple[Path, Path]:
            """A mailbox holding a planted reservation for alice -> bob, key `key`,
            content NOTICE/s/b, in the locked ("flock") or the old two-line format."""
            b = Path(tmp) / f"key-{name}"
            b.mkdir()
            m = b / (".idem." + hashlib.sha256(f"alice\0bob\0{key}".encode()).hexdigest()[:24])
            content = hashlib.sha256(b"NOTICE\0s\0b").hexdigest()
            m.write_text(f"{KEY_ULID}\n{content}\n" + ("flock\n" if fmt == "flock" else ""),
                         encoding="utf-8")
            return b, m

        def keyed(b: Path, *extra: str, body: str = "b", wait_env: str | None = None,
                  key: str = "K") -> tuple[subprocess.CompletedProcess, float]:
            env = {**os.environ, "AGENT_MAIL_DIR": str(b)}
            env.pop("AGENT_MAIL_IDEM_WAIT", None)
            if wait_env is not None:
                env["AGENT_MAIL_IDEM_WAIT"] = wait_env
            t0 = time.monotonic()
            r = subprocess.run(
                [sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
                 "--from", "alice", "--to", "bob", "--subject", "s", "--body", body,
                 "--key", key, *extra], capture_output=True, text=True, env=env)
            return r, time.monotonic() - t0

        # (a) a marker whose holder is ALIVE is never taken over. This process is
        # the holder: it keeps flock on the planted marker while retries run.
        kbox, kmark = key_box("live", "flock")
        planted = kmark.read_text(encoding="utf-8")
        kfd = os.open(kmark, os.O_RDONLY)
        fcntl.flock(kfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r, took = keyed(kbox, "--key-wait", "0")
        check("live holder, key wait 0: retry exits 3 'being sent ... retry' and does not take over",
              r.returncode == 3 and "is being sent by another process right now" in r.stderr
              and "retry" in r.stderr, repr((r.returncode, r.stdout, r.stderr)))
        check("control: nothing written, the live holder's marker is untouched",
              not list(kbox.glob("*.md")) and kmark.read_text(encoding="utf-8") == planted,
              repr(sorted(p.name for p in kbox.iterdir())))
        r, took = keyed(kbox, wait_env="0.4")
        check("live holder, AGENT_MAIL_IDEM_WAIT=0.4: waits the 0.4 s, then exit 3, still no takeover",
              r.returncode == 3 and 0.4 <= took < 4.0 and not list(kbox.glob("*.md"))
              and kmark.read_text(encoding="utf-8") == planted, repr((r.returncode, took, r.stderr)))
        r, _ = keyed(kbox, "--key-wait", "0", body="DIFFERENT")
        check("live holder, different content: exit 2 (conflict), not exit 3",
              r.returncode == 2 and "different content" in r.stderr, repr((r.returncode, r.stderr)))
        # the holder "publishes": its message appears, then it exits
        (kbox / f"{KEY_ULID}-s.md").write_text(
            f"---\nid: {KEY_ULID}\ntype: NOTICE\nfrom: alice\nto: bob\n"
            f"date: {iso(0)}\nsubject: s\nidem: K\n---\n\nb\n", encoding="utf-8")
        r, _ = keyed(kbox, "--key-wait", "0")
        check("once the holder's message exists a retry is a duplicate of it (exit 0), lock still held",
              r.returncode == 0 and f"duplicate of {KEY_ULID}" in r.stdout
              and f"id: {KEY_ULID}" in r.stdout, repr((r.returncode, r.stdout, r.stderr)))
        os.close(kfd)
        check("control: exactly one message for the key after all of that",
              len(list(kbox.glob("*.md"))) == 1, repr(sorted(p.name for p in kbox.iterdir())))

        # (b) a locked-format marker with NO living holder and no message is an
        # exact orphan: taken over without waiting, by exactly one of N retries.
        obox, omark = key_box("orphan", "flock")
        r, took = keyed(obox)  # default key wait (5 s) must not be spent
        check("dead holder: a retry takes over at once under the default wait (no 5 s)",
              r.returncode == 0 and "wrote " in r.stdout and took < 3.0,
              repr((r.returncode, round(took, 2), r.stdout, r.stderr)))
        nbox, nmark = key_box("orphan-race", "flock")
        t0 = time.monotonic()
        with ThreadPoolExecutor(8) as pool:
            res = list(pool.map(lambda _i: keyed(nbox)[0], range(8)))
        took = time.monotonic() - t0
        wrote = [r for r in res if r.returncode == 0 and "wrote " in r.stdout]
        dups = [r for r in res if r.returncode == 0 and "duplicate of" in r.stdout]
        ids = {ln.split(": ", 1)[1] for r in res for ln in r.stdout.splitlines()
               if ln.startswith("id: ")}
        check("8 simultaneous retries of an orphaned key: one wrote, seven 'duplicate of' the same id",
              len(wrote) == 1 and len(dups) == 7 and len(ids) == 1 and KEY_ULID not in ids,
              repr([(r.returncode, r.stdout.strip()[:60], r.stderr.strip()[:80]) for r in res]))
        check("control: one message on disk, marker names it, no temp or takeover file left, no 5 s wait",
              len(list(nbox.glob("*.md"))) == 1 and took < 4.0
              and sorted(p.name for p in nbox.iterdir() if p.name.startswith(".")) == [nmark.name]
              and nmark.read_text(encoding="utf-8").split("\n")[0] in ids
              and nmark.read_text(encoding="utf-8").split("\n")[2] == "flock",
              repr((round(took, 2), sorted(p.name for p in nbox.iterdir()))))
        r, _ = keyed(key_box("orphan-diff", "flock")[0], body="DIFFERENT")
        check("orphaned key, different content: still exit 2, nothing written",
              r.returncode == 2 and "different content" in r.stderr, repr((r.returncode, r.stderr)))

        # (c) the old two-line marker carries no liveness signal: honoured as
        # before, and the key wait (not a hard-coded 5 s) decides its takeover.
        lbox, lmark = key_box("legacy", "old")
        r, took = keyed(lbox, "--key-wait", "0.6")
        check("old-format orphan: taken over after the key wait and not before",
              r.returncode == 0 and "wrote " in r.stdout and 0.6 <= took < 4.0,
              repr((r.returncode, round(took, 2), r.stdout, r.stderr)))
        r, took = keyed(key_box("legacy-zero", "old")[0], "--key-wait", "0")
        check("old-format orphan, key wait 0: taken over immediately",
              r.returncode == 0 and "wrote " in r.stdout and took < 3.0, repr((r.returncode, took)))
        dbox, dmark = key_box("legacy-done", "old")
        (dbox / f"{KEY_ULID}-s.md").write_text(
            f"---\nid: {KEY_ULID}\ntype: NOTICE\nfrom: alice\nto: bob\n"
            f"date: {iso(0)}\nsubject: s\nidem: K\n---\n\nb\n", encoding="utf-8")
        r, _ = keyed(dbox)
        check("old-format marker whose message landed: duplicate of the original id, marker untouched",
              r.returncode == 0 and f"duplicate of {KEY_ULID}" in r.stdout
              and len(list(dbox.glob("*.md"))) == 1 and dmark.read_text(encoding="utf-8").count("\n") == 2,
              repr((r.returncode, r.stdout, r.stderr)))

        # the key wait is bounded and refused, never clamped
        vbox = Path(tmp) / "key-bounds"
        vbox.mkdir()
        for flag in ("-1", "60.5", "abc", "nan", "inf", ""):
            r, _ = keyed(vbox, f"--key-wait={flag}", key=f"bad{flag}")
            check(f"--key-wait {flag!r} refused: exit 2, one line, nothing written",
                  r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1
                  and "key wait" in r.stderr and not list(vbox.iterdir()),
                  repr((r.returncode, r.stderr)))
        for val in ("-0.1", "61", "soon"):
            r, _ = keyed(vbox, wait_env=val, key=f"env{val}")
            check(f"AGENT_MAIL_IDEM_WAIT={val!r} refused: exit 2, one line, nothing written",
                  r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1
                  and "AGENT_MAIL_IDEM_WAIT" in r.stderr and not list(vbox.iterdir()),
                  repr((r.returncode, r.stderr)))
        env = {**os.environ, "AGENT_MAIL_DIR": str(vbox)}
        r = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
                            "--from", "alice", "--to", "bob", "--subject", "s", "--body", "b",
                            "--key-wait", "1"], capture_output=True, text=True, env=env)
        check("--key-wait without --key refused (exit 2)", r.returncode == 2
              and "--key-wait" in r.stderr and not list(vbox.iterdir()), repr((r.returncode, r.stderr)))
        r = subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "send", "--type", "NOTICE",
                            "--from", "alice", "--to", "bob", "--subject", "s", "--body", "b"],
                           capture_output=True, text=True,
                           env={**env, "AGENT_MAIL_IDEM_WAIT": "garbage"})
        check("control: the variable is only read by a keyed send (unkeyed send unaffected)",
              r.returncode == 0, repr((r.returncode, r.stderr)))
        for ok_val in ("0", "60"):
            r, _ = keyed(vbox, "--key-wait", ok_val, key=f"ok{ok_val}")
            check(f"control: --key-wait {ok_val} (a bound) is accepted and the send publishes",
                  r.returncode == 0 and "wrote " in r.stdout, repr((r.returncode, r.stderr)))
        r, _ = keyed(vbox, "--key-wait", "0", wait_env="garbage", key="flagwins")
        check("--key-wait takes precedence over the variable", r.returncode == 0, repr(r.stderr))

        print("\nownership, id case and failure reporting at read time (PROTOCOL.md §32)")

        def tool(b: Path, *argv: str) -> subprocess.CompletedProcess:
            env = {**os.environ, "AGENT_MAIL_DIR": str(b)}
            return subprocess.run([sys.executable, str(HERE / "agent_mail.py"), *argv],
                                  capture_output=True, text=True, env=env)

        def new_id(r: subprocess.CompletedProcess) -> str:
            return next((ln.split(": ", 1)[1] for ln in r.stdout.splitlines()
                         if ln.startswith("id: ")), "")

        def row(b: Path, subject: str) -> str:
            return next((ln for ln in tool(b, "list").stdout.splitlines()
                         if ln.rstrip().endswith("  " + subject)), "")

        # (a) only the claim's own sender's CLAIM supersedes it, at read time too
        obox = Path(tmp) / "own"
        obox.mkdir()
        mine = send(obox, "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "mine",
                    "--body", "b", "--scope", "res", "--expires", iso(1))
        cid = new_id(mine)
        check("control: alice's claim is held", "[held" in row(obox, "mine"), row(obox, "mine"))
        for n, (kind, extra) in enumerate([("NOTICE", ""), ("CLAIM", f"expires: {iso(1)}\n")]):
            fid = f"01ARZ3NDEKTSV4RRFFQ69GFRG{n}"
            (obox / f"{fid}-void.md").write_text(
                f"---\nid: {fid}\ntype: {kind}\nfrom: mallory\nto: all\ndate: {iso(0)}\n"
                f"subject: void{n}\nsupersedes: {cid.lower()}\n{extra}---\n\nhand-written\n",
                encoding="utf-8")
        check("a hand-written NOTICE and CLAIM from another sender citing it leave the claim held",
              "[held" in row(obox, "mine"), row(obox, "mine"))
        r = send(obox, "--type", "CLAIM", "--from", "bob", "--to", "all", "--subject", "rival",
                 "--body", "b", "--scope", "res", "--expires", iso(1))
        check("so a rival's claim on the scope is still refused (exit 3, held by alice)",
              r.returncode == 3 and "already held by alice" in r.stderr, repr((r.returncode, r.stderr)))
        check("the foreign files are still ordinary mail, not quarantined",
              "void0" in tool(obox, "list").stdout and "REJECT" not in tool(obox, "list").stderr)
        r = send(obox, "--type", "NOTICE", "--from", "alice", "--to", "all", "--subject", "done",
                 "--body", "b", "--supersedes", cid)
        check("there is no release message: the owner's NOTICE superseding its claim is refused (exit 2)",
              r.returncode == 2 and "[held" in row(obox, "mine"), repr((r.returncode, r.stderr)))
        r = send(obox, "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "renewed",
                 "--body", "b", "--scope", "res", "--expires", iso(1), "--supersedes", cid.lower())
        check("control: the owner's own CLAIM does supersede it (id cited in lower case)",
              r.returncode == 0 and "[superseded" in row(obox, "mine") and "[held" in row(obox, "renewed"),
              repr((r.returncode, r.stderr, row(obox, "mine"))))

        r = send(obox, "--type", "CLAIM", "--from", "bob", "--to", "all", "--subject", "rival2",
                 "--body", "b", "--scope", "res", "--expires", iso(1))
        check("renewed with the same --scope the owner keeps it: the rival is still refused",
              r.returncode == 3 and "already held by alice" in r.stderr, repr((r.returncode, r.stderr)))
        rid = next((p.name[:26] for p in obox.glob("*-renewed.md")), "NOSUCHID")
        r = send(obox, "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "given up",
                 "--body", "b", "--expires", iso(1), "--supersedes", rid)
        check("superseding it with a claim that names no scope releases the scope, and send says so",
              r.returncode == 0 and "this one names no scope: 'res' is released" in r.stderr,
              repr((r.returncode, r.stderr)))
        r = send(obox, "--type", "CLAIM", "--from", "bob", "--to", "all", "--subject", "rival3",
                 "--body", "b", "--scope", "res", "--expires", iso(1))
        check("control: the rival can then claim the scope at once", r.returncode == 0, r.stderr)

        # (b) (c) replies: case, twice, dangling
        qbox = Path(tmp) / "ack"
        qbox.mkdir()
        ask = new_id(send(qbox, "--type", "ASK", "--from", "alice", "--to", "bob",
                          "--subject", "question", "--body", "b"))
        send(qbox, "--type", "ASK", "--from", "alice", "--to", "bob",
             "--subject", "untouched", "--body", "b")
        r = send(qbox, "--type", "ANSWER", "--from", "bob", "--to", "alice", "--subject", "dangling",
                 "--body", "b", "--reply-to", "01ARZ3NDEKTSV4RRFFQ69GNONE")
        check("a reply to a nonexistent id is written (exit 0) and closes nothing",
              r.returncode == 0 and "[open" in row(qbox, "question") and "[open" in row(qbox, "untouched"),
              repr((r.returncode, r.stderr)))
        r = send(qbox, "--type", "ANSWER", "--from", "bob", "--to", "alice", "--subject", "answer",
                 "--body", "b", "--reply-to", ask.lower(), "--key", "ans")
        check("an ANSWER citing the ASK's id in lower case closes it",
              r.returncode == 0 and "[answered" in row(qbox, "question"), row(qbox, "question"))
        check("control: the other ASK is still open and still delivered to bob",
              "[open" in row(qbox, "untouched") and "untouched" in pickup(qbox, "bob")
              and "question" not in pickup(qbox, "bob"), pickup(qbox, "bob"))
        r2 = send(qbox, "--type", "ANSWER", "--from", "bob", "--to", "alice", "--subject", "answer",
                  "--body", "b", "--reply-to", ask.lower(), "--key", "ans")
        r3 = send(qbox, "--type", "ANSWER", "--from", "bob", "--to", "alice", "--subject", "again",
                  "--body", "b", "--reply-to", ask)
        check("answering twice is harmless: a keyed retry writes nothing, a second answer changes nothing",
              r2.returncode == 0 and "duplicate of" in r2.stdout and r3.returncode == 0
              and "[answered" in row(qbox, "question") and len(list(qbox.glob("*.md"))) == 5,
              repr((r2.stdout, r3.stderr, len(list(qbox.glob("*.md"))))))
        r = tool(qbox, "show", ask.lower())
        check("show accepts the id in lower case", r.returncode == 0 and "subject: question" in r.stdout,
              repr((r.returncode, r.stderr)))

        # (d) a send that cannot write says so in one line and leaves nothing
        if os.geteuid() == 0:
            skip("unwritable mailbox: one line, exit 1, nothing left", "running as root")
        else:
            wbox = Path(tmp) / "readonly"
            wbox.mkdir()
            os.chmod(wbox, 0o555)
            try:
                for label, extra in [("plain", []), ("keyed", ["--key", "k"]),
                                     ("scoped claim", ["--scope", "s", "--expires", iso(1)])]:
                    r = send(wbox, "--type", "CLAIM" if "claim" in label else "NOTICE",
                             "--from", "alice", "--to", "bob", "--subject", "s", "--body", "b", *extra)
                    check(f"read-only mailbox, {label} send: exit 1, exactly one line, no traceback",
                          r.returncode == 1 and r.stdout == "" and len(r.stderr.strip().splitlines()) == 1
                          and r.stderr.startswith("send failed: cannot write to the mailbox: ")
                          and "nothing written" in r.stderr and "EACCES" in r.stderr,
                          repr((r.returncode, r.stderr[-300:])))
            finally:
                os.chmod(wbox, 0o755)
            check("nothing was left behind (no temp, marker or lock)", not list(wbox.iterdir()),
                  repr(sorted(p.name for p in wbox.iterdir())))
            r = send(wbox, "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "s",
                     "--body", "b", "--key", "k")
            check("control: the same send succeeds once the mailbox is writable",
                  r.returncode == 0 and "wrote " in r.stdout, r.stderr)

        print("\nstatus and doctor name what a crash leaves behind (PROTOCOL.md §33)")
        gbox = Path(tmp) / "diag"
        gbox.mkdir()

        def status_json(b: Path) -> dict:
            r = tool(b, "status", "--json")
            return json.loads(r.stdout) if r.returncode == 0 else {}

        def snapshot(b: Path) -> dict:
            return {f.name: (f.lstat().st_mtime_ns, f.lstat().st_size,
                             hashlib.sha256(f.read_bytes()).hexdigest())
                    for f in sorted(b.iterdir())}

        def plant(name: str, text: str, old: bool) -> Path:
            f = gbox / name
            f.write_text(text, encoding="utf-8")
            if old:
                os.utime(f, (time.time() - 120, time.time() - 120))
            return f

        def scope_lock_name(scope: str) -> str:
            return ".scope." + hashlib.sha256(scope.encode()).hexdigest()[:24] + ".lock"

        def marker_name(key: str) -> str:
            return ".idem." + hashlib.sha256(f"alice\0bob\0{key}".encode()).hexdigest()[:24]

        send(gbox, "--type", "NOTICE", "--from", "alice", "--to", "bob", "--subject", "s", "--body", "b",
             "--key", "done")  # a real keyed send: one message, one resolved marker
        st = status_json(gbox)
        zero_locks = {"total": 0, "in_flight": 0, "held": 0, "orphaned": 0}
        check("clean mailbox: problems is [], nothing orphaned, the one marker is resolved; schema still 1",
              st.get("problems") == [] and st.get("scope_locks") == zero_locks
              and st.get("idem_markers") == {"total": 1, "resolved": 1, "in_flight": 0, "held": 0, "orphaned": 0}
              and st.get("takeover_mutexes") == {"total": 0, "stale": 0}
              and st.get("foreign_supersedes") == 0 and st.get("dangling_replies") == 0
              and st.get("schema") == 1, repr({k: st.get(k) for k in (
                  "problems", "scope_locks", "idem_markers", "takeover_mutexes", "schema")}))

        chash = hashlib.sha256(b"NOTICE\0s\0b").hexdigest()
        plant(scope_lock_name("orphan-scope"), "0\n", old=True)
        held_lock = plant(scope_lock_name("held-scope"), "0\n", old=True)
        plant(scope_lock_name("fresh-scope"), "0\n", old=False)
        plant(marker_name("old-format"), f"01ARZ3NDEKTSV4RRFFQ69GD1A0\n{chash}\n", old=True)
        plant(marker_name("locked-orphan"), f"01ARZ3NDEKTSV4RRFFQ69GD1A1\n{chash}\nflock\n", old=True)
        held_mark = plant(marker_name("locked-held"), f"01ARZ3NDEKTSV4RRFFQ69GD1A2\n{chash}\nflock\n", old=True)
        plant(marker_name("fresh"), f"01ARZ3NDEKTSV4RRFFQ69GD1A3\n{chash}\nflock\n", old=False)
        plant(marker_name("old-format") + ".takeover", "", old=True)
        plant(marker_name("fresh") + ".takeover", "", old=False)
        tmp_old = plant(".01ARZ3NDEKTSV4RRFFQ69GD1A4-x.md.1.tmp", "partial", old=False)
        os.utime(tmp_old, (time.time() - 700, time.time() - 700))
        plant("junk.md", "no frontmatter\n", old=False)
        lease = new_id(send(gbox, "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "lease",
                            "--body", "b", "--expires", iso(1)))
        plant("01ARZ3NDEKTSV4RRFFQ69GD1A5-void.md",
              f"---\nid: 01ARZ3NDEKTSV4RRFFQ69GD1A5\ntype: NOTICE\nfrom: mallory\nto: all\ndate: {iso(0)}\n"
              f"subject: void\nsupersedes: {lease}\n---\n\nb\n", old=False)
        send(gbox, "--type", "ANSWER", "--from", "bob", "--to", "alice", "--subject", "typo", "--body", "b",
             "--reply-to", "01ARZ3NDEKTSV4RRFFQ69GN0NE")
        fd_lock = os.open(held_lock, os.O_RDWR)
        fcntl.flock(fd_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd_mark = os.open(held_mark, os.O_RDWR)
        fcntl.flock(fd_mark, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(gbox)
        st = status_json(gbox)
        check("status --json wrote nothing: every file, mtime and hash unchanged, nothing created",
              snapshot(gbox) == before)
        check("scope locks: one orphaned, one held by a live process, one in flight (fresh, not probed)",
              st.get("scope_locks") == {"total": 3, "in_flight": 1, "held": 1, "orphaned": 1},
              repr(st.get("scope_locks")))
        check("key markers: resolved 1, in flight 1, held 1, orphaned 2 (old-format and locked-format)",
              st.get("idem_markers") == {"total": 5, "resolved": 1, "in_flight": 1, "held": 1, "orphaned": 2},
              repr(st.get("idem_markers")))
        check("takeover mutexes: 2, of which 1 stale; foreign supersede 1; dangling reply 1",
              st.get("takeover_mutexes") == {"total": 2, "stale": 1}
              and st.get("foreign_supersedes") == 1 and st.get("dangling_replies") == 1,
              repr((st.get("takeover_mutexes"), st.get("foreign_supersedes"), st.get("dangling_replies"))))
        check("problems: fixed codes with counts, in the documented order, zero-count codes omitted",
              st.get("problems") == [
                  {"code": "QUARANTINED", "count": 1}, {"code": "STALE_TMP", "count": 1},
                  {"code": "ORPHAN_IDEM_MARKER", "count": 2}, {"code": "ORPHAN_SCOPE_LOCK", "count": 1},
                  {"code": "STALLED_LOCK_HOLDER", "count": 2}, {"code": "STALE_TAKEOVER_MUTEX", "count": 1},
                  {"code": "FOREIGN_SUPERSEDE", "count": 1}, {"code": "DANGLING_REPLY", "count": 1}],
              repr(st.get("problems")))
        check("the keys of schema 1 are all still there", {
            "schema", "now", "mailbox", "messages", "asks", "claims", "quarantined", "stale_tmp",
            "stalled_hours", "agents", "stalled_agents"} <= set(st), repr(sorted(st)))
        r = tool(gbox, "status")
        check("text status prints a PROBLEMS line with the same codes, and still exits 0",
              r.returncode == 0 and "PROBLEMS: QUARANTINED=1 STALE_TMP=1 ORPHAN_IDEM_MARKER=2 "
              "ORPHAN_SCOPE_LOCK=1 STALLED_LOCK_HOLDER=2" in r.stdout, r.stdout)
        r = tool(gbox, "doctor")
        check("doctor names the orphaned files and never prints a body",
              "scope_locks: 3 (in_flight 1, held 1, orphaned 1)" in r.stdout
              and f"  - {scope_lock_name('orphan-scope')} (orphaned)" in r.stdout
              and "idem_markers: 5 (resolved 1, in_flight 1, held 1, orphaned 2)" in r.stdout
              and f"  - {marker_name('locked-orphan')} (orphaned)" in r.stdout
              and "takeover_mutexes: 2 (stale 1)" in r.stdout
              and "foreign_supersedes: 1\n  - 01ARZ3NDEKTSV4RRFFQ69GD1A5" in r.stdout
              and "dangling_replies: 1" in r.stdout and "Traceback" not in r.stderr
              and snapshot(gbox) == before, r.stdout + r.stderr)
        os.close(fd_lock)
        os.close(fd_mark)
        st = status_json(gbox)
        check("once the holders are gone the same files are orphaned, not held",
              st.get("scope_locks") == {"total": 3, "in_flight": 1, "held": 0, "orphaned": 2}
              and st.get("idem_markers", {}).get("held") == 0 and st["idem_markers"]["orphaned"] == 3,
              repr(st.get("scope_locks")))
        check("and STALLED_LOCK_HOLDER is gone from problems",
              all(pr["code"] != "STALLED_LOCK_HOLDER" for pr in st.get("problems", [{"code": "STALLED_LOCK_HOLDER"}])),
              repr(st.get("problems")))
        r = send(gbox, "--type", "CLAIM", "--from", "carol", "--to", "all", "--subject", "after probe",
                 "--body", "b", "--scope", "orphan-scope", "--expires", iso(1))
        check("a probed orphan lock is not left locked: the scope is claimable and the file is removed",
              r.returncode == 0 and not (gbox / scope_lock_name("orphan-scope")).exists(), r.stderr)
        r, _ = keyed(gbox, key="locked-orphan")
        check("a probed orphan marker is not left locked: the retry takes it over at once",
              r.returncode == 0 and "wrote " in r.stdout, repr((r.returncode, r.stderr)))
        hbox = Path(tmp) / "diag-doctor"
        hbox.mkdir()
        subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "canary", "--from", "alice",
                        "--to", "alice", "--hours", "1"], capture_output=True, text=True,
                       env={**os.environ, "AGENT_MAIL_DIR": str(hbox)})
        lk = hbox / scope_lock_name("x")
        lk.write_text("0\n", encoding="utf-8")
        os.utime(lk, (time.time() - 120, time.time() - 120))
        r = tool(hbox, "doctor")
        check("an orphaned lock is reported by doctor but is not a new reason to FAIL (exit 0, PASS)",
              r.returncode == 0 and "scope_locks: 1 (in_flight 0, held 0, orphaned 1)" in r.stdout
              and r.stdout.rstrip().endswith("PASS"), r.stdout + r.stderr)

        print("\nstatus names who holds what (PROTOCOL.md §34)")
        NOW34 = "2030-01-01T12:00:00Z"
        P34 = "01ARZ3NDEKTSV4RRFFQ69G5F"

        def claim34(b: Path, tail: str, sender: str, expires: str | None, scope: str | None = None,
                    date: str | None = "2030-01-01T09:00:00Z", supersedes: str = "",
                    mtype: str = "CLAIM", mid: str = "", subject: str = "SUBJ34-TOKEN") -> str:
            mid = mid or P34 + tail
            lines = ["---", f"id: {mid}", f"type: {mtype}", f"from: {sender}", "to: all"]
            if date is not None:
                lines.append(f"date: {date}")
            lines.append(f"subject: {subject}")
            if supersedes:
                lines.append(f"supersedes: {supersedes}")
            if expires is not None:
                lines.append(f"expires: {expires}")
            if scope is not None:
                lines.append(f"scope: {scope}")
            (b / f"{mid.upper()}-x.md").write_text(
                "\n".join(lines + ["---", "", "BODY34-TOKEN", ""]), encoding="utf-8")
            return mid

        def st34(b: Path, *extra: str) -> dict:
            r = tool(b, "status", "--json", "--now", NOW34, *extra)
            return json.loads(r.stdout) if r.returncode == 0 else {}

        def box34(name: str) -> Path:
            b = Path(tmp) / name
            b.mkdir()
            return b

        NEW34 = ["claims_attention", "claims_attention_truncated", "claims_held",
                 "claims_held_truncated", "contested_scopes", "scope_filter"]
        ebox = box34("s34-example")
        claim34(ebox, "A0", "alice", "2030-01-01T18:00:00Z", "file:s.py")
        claim34(ebox, "A1", "bob", "2030-01-01T20:00:00Z", "docs/guide.md", date="2030-01-01T08:00:00Z")
        claim34(ebox, "A2", "bob", "2030-01-02T12:00:00+02:00", date="2030-01-01T10:00:00Z",
                supersedes=P34 + "A1")
        claim34(ebox, "A3", "carol", "2029-12-31T12:00:00Z", "file:t.py", date="2029-12-31T09:00:00Z")
        claim34(ebox, "A4", "carol", "tomorrow", "db:schema")
        before34 = snapshot(ebox)
        r = tool(ebox, "status", "--json", "--now", NOW34)
        st = json.loads(r.stdout) if r.returncode == 0 else {}
        check("the §34 example: the six added keys have exactly the documented values",
              {k: st.get(k, "MISSING") for k in NEW34} == {
                  "claims_attention": [
                      {"id": P34 + "A4", "reason": "MALFORMED_EXPIRES", "scope": "db:schema", "sender": "carol"},
                      {"id": P34 + "A3", "reason": "EXPIRED", "scope": "file:t.py", "sender": "carol"}],
                  "claims_attention_truncated": 0,
                  "claims_held": [
                      {"date": "2030-01-01T09:00:00Z", "expires": "2030-01-01T18:00:00Z", "id": P34 + "A0",
                       "scope": "file:s.py", "seconds_left": 21600, "sender": "alice", "supersedes": None,
                       "to": "all"},
                      {"date": "2030-01-01T10:00:00Z", "expires": "2030-01-02T10:00:00Z", "id": P34 + "A2",
                       "scope": None, "seconds_left": 79200, "sender": "bob", "supersedes": P34 + "A1",
                       "to": "all"}],
                  "claims_held_truncated": 0, "contested_scopes": 0, "scope_filter": None},
              repr({k: st.get(k, "MISSING") for k in NEW34}))
        check("control: the counts of §29 are unchanged beside them, schema still 1, problems as before",
              st.get("claims") == {"held": 2, "expired": 1, "malformed_expiry": 1, "superseded": 1}
              and st.get("schema") == 1
              and st.get("problems") == [{"code": "MALFORMED_CLAIM_EXPIRY", "count": 1}],
              repr((st.get("claims"), st.get("problems"))))
        check("status --json is deterministic for a fixed --now and wrote nothing",
              r.stdout == tool(ebox, "status", "--json", "--now", NOW34).stdout and snapshot(ebox) == before34)
        txt = tool(ebox, "status", "--now", NOW34)
        check("text status: a held-claims table with scope, holder, expiry and time left",
              txt.returncode == 0 and "claims: held=2  expired=1  malformed_expiry=1  superseded=1" in txt.stdout
              and "held claims (scope, holder, expires, left):" in txt.stdout
              and any(ln.split() == ["file:s.py", "alice", "2030-01-01T18:00:00Z", "6h00m"]
                      for ln in txt.stdout.splitlines())
              and any(ln.split() == ["-", "bob", "2030-01-02T10:00:00Z", "22h00m"]
                      for ln in txt.stdout.splitlines()), txt.stdout)
        doc = tool(ebox, "doctor")
        check("no status or doctor output carries a subject or a body (control: the tokens are on disk)",
              all("SUBJ34-TOKEN" not in o and "BODY34-TOKEN" not in o
                  for o in (r.stdout, r.stderr, txt.stdout, txt.stderr, doc.stdout, doc.stderr))
              and "SUBJ34-TOKEN" in tool(ebox, "list").stdout
              and any(b"BODY34-TOKEN" in f.read_bytes() for f in ebox.iterdir()),
              txt.stdout)
        check("doctor prints contested_scopes: 0 for it", "contested_scopes: 0" in doc.stdout, doc.stdout)
        empty34 = box34("s34-empty")
        st = st34(empty34)
        check("an empty mailbox has all six keys, empty", {k: st.get(k, "MISSING") for k in NEW34} == {
            "claims_attention": [], "claims_attention_truncated": 0, "claims_held": [],
            "claims_held_truncated": 0, "contested_scopes": 0, "scope_filter": None}, repr(st))
        check("and its text status prints no held-claims table",
              "held claims" not in tool(empty34, "status", "--now", NOW34).stdout)

        obox = box34("s34-odd")
        FUT = "2030-01-02T12:00:00Z"
        claim34(obox, "B0", "alice", FUT, "file:s.py")                      # held, then superseded by B1
        claim34(obox, "B1", "alice", FUT, "file:s.py", supersedes=P34 + "B0")  # the renewal: held
        claim34(obox, "B2", "bob", "2029-12-31T00:00:00Z", "old")              # expired
        claim34(obox, "B3", "bob", None, "noexp")                              # no expires
        claim34(obox, "B4", "bob", "soon", "badexp")                           # unreadable expires
        claim34(obox, "B5", "carol", FUT, "kept")                              # held: only foreign cites
        claim34(obox, "B6", "mallory", None, supersedes=P34 + "B5", mtype="NOTICE")
        claim34(obox, "B7", "mallory", FUT, "other", supersedes=P34 + "B5")    # foreign CLAIM: itself held
        claim34(obox, "B8", "carol", FUT)                                      # no scope
        claim34(obox, "", "carol", FUT, "lower", mid=(P34 + "B9").lower())     # lower-case id
        claim34(obox, "BA", "Carol", "2030-01-01T13:00:00", "h\u00e9llo/\u30d5\u30a1\u30a4\u30eb.py", date=None)
        claim34(obox, "BB", "team", FUT, "alias-scope")                        # from is an alias token (§27)
        claim34(obox, "BC", "dave", "2030-01-09T12:00:01Z", "long-lease")      # held, over 168 h
        claim34(obox, "BD", "erin", FUT, "X" * 5000)                           # scope longer than send allows
        r = tool(obox, "status", "--json", "--now", NOW34)
        st = json.loads(r.stdout) if r.returncode == 0 else {}
        held = {h["id"].upper()[-2:]: h for h in st.get("claims_held", [])}
        check("listed as held: renewal, foreign-cited claim, the foreign CLAIM itself, no scope, lower-case id, "
              "non-ASCII scope, alias sender, long lease, oversize scope",
              r.returncode == 0 and sorted(held) == ["B1", "B5", "B7", "B8", "B9", "BA", "BB", "BC", "BD"]
              and len(st["claims_held"]) == st["claims"]["held"] == 9, repr(sorted(held)) + r.stderr[-300:])
        check("not listed as held: the superseded claim, the expired one, the two without a usable expiry",
              not {"B0", "B2", "B3", "B4"} & set(held) and st.get("claims", {}).get("superseded") == 1
              and st["claims"]["expired"] == 1 and st["claims"]["malformed_expiry"] == 2, repr(st.get("claims")))
        check("every held row has exactly the documented keys (plus scope_oversize on the oversize one only)",
              bool(held) and all(sorted(h) == ["date", "expires", "id", "scope", "seconds_left", "sender",
                                               "supersedes", "to"]
                                 for k, h in held.items() if k != "BD")
              and sorted(held.get("BD", {})) == ["date", "expires", "id", "scope", "scope_oversize",
                                                 "seconds_left", "sender", "supersedes", "to"],
              repr([sorted(h) for h in held.values()][:2]))
        check("a claim cited only by a foreign supersede is still held by its own sender; supersedes is as stored",
              held.get("B5", {}).get("sender") == "carol" and held["B5"]["supersedes"] is None
              and held.get("B7", {}).get("supersedes") == P34 + "B5" and st.get("foreign_supersedes") == 2,
              repr((held.get("B5"), st.get("foreign_supersedes"))))
        check("no scope is null; a lower-case id is reported as stored",
              held.get("B8", {"scope": 0})["scope"] is None and held.get("B9", {}).get("id") == (P34 + "B9").lower(),
              repr((held.get("B8"), held.get("B9"))))
        check("non-ASCII scope as stored; zone-less expiry reads as UTC; missing date is null; from is lower-cased",
              held.get("BA") == {"date": None, "expires": "2030-01-01T13:00:00Z", "id": P34 + "BA",
                                 "scope": "h\u00e9llo/\u30d5\u30a1\u30a4\u30eb.py", "seconds_left": 3600,
                                 "sender": "carol", "supersedes": None, "to": "all"}, repr(held.get("BA")))
        check("an alias token in from is reported as the writer token it is, not expanded",
              held.get("BB", {}).get("sender") == "team" and "agent-a" not in r.stdout, repr(held.get("BB")))
        check("an oversize scope is never printed or shortened: null plus scope_oversize",
              held.get("BD", {"scope": 0})["scope"] is None and held["BD"].get("scope_oversize") is True
              and "XXXX" not in r.stdout and len(r.stdout) < 20000, repr(len(r.stdout)))
        order = [h["id"].upper()[-2:] for h in st.get("claims_held", [])]
        check("claims_held is sorted by scope (code point), then id; no-scope and oversize rows last",
              order == ["BB", "B1", "BA", "B5", "BC", "B9", "B7", "B8", "BD"], repr(order))
        att = [(a["id"].upper()[-2:], a["reason"], a["scope"]) for a in st.get("claims_attention", [])]
        check("claims_attention: reasons in the documented order, each row id/sender/scope/reason",
              att == [("B4", "MALFORMED_EXPIRES", "badexp"), ("B3", "NO_EXPIRES", "noexp"),
                      ("BC", "TOO_LONG", "long-lease"), ("B2", "EXPIRED", "old")]
              and all(sorted(a) == ["id", "reason", "scope", "sender"] for a in st["claims_attention"])
              and st.get("claims_attention_truncated") == 0 and st.get("contested_scopes") == 0,
              repr(att))
        check("control: a lease of exactly 168 hours is not TOO_LONG",
              claim34(obox, "BE", "dave", "2030-01-08T12:00:00Z", "week") != ""
              and all(a["id"] != P34 + "BE" for a in st34(obox).get("claims_attention", [{"id": P34 + "BE"}])))
        txt = tool(obox, "status", "--now", NOW34)
        check("text status with odd records: exit 0, no traceback, the oversize scope is not dumped",
              txt.returncode == 0 and "Traceback" not in txt.stderr and "X" * 60 not in txt.stdout
              and "h\u00e9llo/\u30d5\u30a1\u30a4\u30eb.py" in txt.stdout, txt.stdout[-600:] + txt.stderr[-300:])
        r = tool(obox, "status", "--json", "--now", NOW34, "--scope", "kept")
        fs = json.loads(r.stdout) if r.returncode == 0 else {}
        check("--scope keeps exactly the matching rows and says so; the counts still cover the whole mailbox",
              [h["id"] for h in fs.get("claims_held", [])] == [P34 + "B5"] and fs.get("claims_attention") == []
              and fs.get("scope_filter") == "kept" and fs.get("claims") == st34(obox).get("claims")
              and sorted(fs) == sorted(st34(obox)), repr(fs.get("claims_held")))
        fs = st34(obox, "--scope", "old")
        check("--scope filters claims_attention too",
              fs.get("claims_held") == [] and [a["reason"] for a in fs.get("claims_attention", [])] == ["EXPIRED"],
              repr(fs.get("claims_attention")))
        r = tool(obox, "status", "--json", "--now", NOW34, "--scope", "no-such-scope")
        check("--scope with no match is not an error: exit 0, empty lists",
              r.returncode == 0 and json.loads(r.stdout)["claims_held"] == []
              and json.loads(r.stdout)["claims_attention"] == [], r.stderr)
        check("--scope is exact and case-sensitive (control: 'kept' matched above)",
              st34(obox, "--scope", "KEPT").get("claims_held") == []
              and st34(obox, "--scope", "kep").get("claims_held") == [])
        r = tool(obox, "status", "--scope", "")
        check("an empty --scope is refused with exit 2", r.returncode == 2 and "--scope" in r.stderr, r.stderr)
        r = tool(obox, "status", "--now", NOW34, "--scope", "kept")
        check("text status --scope prints only that scope's row",
              r.returncode == 0 and sum(1 for ln in r.stdout.splitlines() if ln.startswith("  ") and FUT in ln) == 1
              and any(ln.split()[:2] == ["kept", "carol"] for ln in r.stdout.splitlines()), r.stdout)

        cbox = box34("s34-contested")
        claim34(cbox, "C0", "alice", FUT, "file:s.py")
        claim34(cbox, "C1", "alice", FUT, "file:s.py")   # the same sender twice: allowed, not a contest
        claim34(cbox, "C2", "carol", FUT, "file:u.py")
        st = st34(cbox)
        check("control: one sender holding its own scope twice is not contested, and no problem is reported",
              st.get("contested_scopes") == 0 and st.get("claims_attention") == [] and st.get("problems") == []
              and st.get("claims", {}).get("held") == 3, repr((st.get("contested_scopes"), st.get("problems"))))
        claim34(cbox, "C3", "bob", FUT, "file:s.py")     # hand-written rival on the same scope
        st = st34(cbox)
        check("two senders holding one scope: contested_scopes 1, a CONTESTED_SCOPE row per claim on it",
              st.get("contested_scopes") == 1
              and [(a["id"][-2:], a["sender"], a["scope"], a["reason"]) for a in st.get("claims_attention", [])]
              == [("C0", "alice", "file:s.py", "CONTESTED_SCOPE"), ("C1", "alice", "file:s.py", "CONTESTED_SCOPE"),
                  ("C3", "bob", "file:s.py", "CONTESTED_SCOPE")], repr(st.get("claims_attention")))
        check("problems gains CONTESTED_SCOPE with the number of scopes; all four claims are still held",
              st.get("problems") == [{"code": "CONTESTED_SCOPE", "count": 1}] and st["claims"]["held"] == 4
              and len(st["claims_held"]) == 4, repr(st.get("problems")))
        r = tool(cbox, "status", "--now", NOW34)
        check("text status PROBLEMS line names it and status still exits 0",
              r.returncode == 0 and "PROBLEMS: CONTESTED_SCOPE=1" in r.stdout, r.stdout)
        later = json.loads(tool(cbox, "status", "--json", "--now", "2030-01-03T00:00:00Z").stdout)
        check("once the leases have expired the scope is no longer contested",
              later.get("contested_scopes") == 0 and later.get("problems") == []
              and all(a["reason"] == "EXPIRED" for a in later.get("claims_attention", [{"reason": ""}])),
              repr(later.get("claims_attention")))
        dbox = box34("s34-doctor")
        subprocess.run([sys.executable, str(HERE / "agent_mail.py"), "canary", "--from", "alice",
                        "--to", "alice", "--hours", "1"], capture_output=True, text=True,
                       env={**os.environ, "AGENT_MAIL_DIR": str(dbox)})
        claim34(dbox, "D0", "alice", iso(1), "file:s.py")
        r = tool(dbox, "doctor")
        check("control: doctor on an uncontested mailbox prints contested_scopes: 0 and PASSes",
              r.returncode == 0 and "contested_scopes: 0" in r.stdout and r.stdout.rstrip().endswith("PASS"),
              r.stdout)
        claim34(dbox, "D1", "bob", iso(1), "file:s.py")
        r = tool(dbox, "doctor")
        check("doctor names the contested scope and both holders with their ids; reported, not a reason to FAIL",
              r.returncode == 0 and "contested_scopes: 1" in r.stdout
              and f"  - 'file:s.py': alice ({P34}D0), bob ({P34}D1)" in r.stdout
              and r.stdout.rstrip().endswith("PASS"), r.stdout)

        xbox = box34("s34-limits")
        big = "file:" + "s" * 4091   # 4096 characters: the longest --scope `send` accepts (§29)
        r = send(xbox, "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "max scope",
                 "--body", "b", "--scope", big, "--expires", iso(1))
        r2 = send(xbox, "--type", "CLAIM", "--from", "alice", "--to", "all", "--subject", "too long",
                  "--body", "b", "--scope", big + "s", "--expires", iso(1))
        st = status_json(xbox)   # a real send is dated now, so this one is read at the real now
        check("a claim written by send with the longest scope send accepts (4096) is reported in full",
              r.returncode == 0 and r2.returncode == 2 and "--scope is longer than 4096" in r2.stderr
              and len(st.get("claims_held", [])) == 1
              and st["claims_held"][0]["scope"] == big and "scope_oversize" not in st["claims_held"][0]
              and st["claims_held"][0]["id"] == new_id(r) and st["claims_held"][0]["sender"] == "alice",
              repr((r.returncode, r2.returncode, r.stderr[-200:])))
        claim34(xbox, "E0", "bob", "9999-12-31T23:59:59-12:00", "far")
        claim34(xbox, "E1", "carol", "2030-01-02T12:00:00Z", "early", date="0001-01-01T00:00:00+14:00")
        r = tool(xbox, "status", "--json", "--now", NOW34)
        st = json.loads(r.stdout) if r.returncode == 0 else {}
        far = {h["scope"]: h for h in st.get("claims_held", [])}
        check("an expiry or date UTC cannot represent is clamped to the nearest second, never a traceback",
              r.returncode == 0 and far.get("far", {}).get("expires") == "9999-12-31T23:59:59Z"
              and far.get("early", {}).get("date") == "0001-01-01T00:00:00Z"
              and tool(xbox, "status", "--now", NOW34).returncode == 0, r.stderr[-300:])

        kbox = box34("s34-cap")
        for n in range(201):
            claim34(kbox, "", "alice" if n % 2 else "bob", FUT, f"file:m{n:03d}.py",
                    mid=f"01ARZ3NDEKTSV4RRFFQ69H{n:04d}")
        st = st34(kbox)
        check("201 held claims: 200 rows, claims_held_truncated 1, the count is still 201",
              len(st.get("claims_held", [])) == 200 and st.get("claims_held_truncated") == 1
              and st.get("claims", {}).get("held") == 201
              and st["claims_held"][0]["scope"] == "file:m000.py"
              and st["claims_held"][-1]["scope"] == "file:m199.py",
              repr((len(st.get("claims_held", [])), st.get("claims_held_truncated"))))
        fs = st34(kbox, "--scope", "file:m200.py")
        check("--scope is applied before the cap: the 201st claim is reachable",
              [h["scope"] for h in fs.get("claims_held", [])] == ["file:m200.py"]
              and fs.get("claims_held_truncated") == 0, repr(fs.get("claims_held")))
        r = tool(kbox, "status", "--now", NOW34)
        check("text status caps the table at 20 rows and says how many more",
              sum(1 for ln in r.stdout.splitlines() if ln.startswith("  file:m")) == 20
              and "  ... and 181 more" in r.stdout, r.stdout[-400:])
        later = json.loads(tool(kbox, "status", "--json", "--now", "2030-01-03T00:00:00Z").stdout)
        check("201 expired claims: claims_attention holds 200 rows and claims_attention_truncated is 1",
              len(later.get("claims_attention", [])) == 200 and later.get("claims_attention_truncated") == 1
              and later.get("claims_held") == [] and later["claims"]["expired"] == 201,
              repr(later.get("claims_attention_truncated")))

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
