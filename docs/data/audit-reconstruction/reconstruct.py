#!/usr/bin/env python3
"""Rebuild "who held what, when; who asked what; who answered" from a mailbox.

Stdlib only. Reads the mailbox ONLY through documented commands
(`list --json`, `show --json`, `status --json`) of agent_mail.py, never the
files. Prints a timeline, claim intervals with an end reason, and ASK
lifecycles, plus a list of questions the mailbox cannot answer.

    python3 reconstruct.py --tool PATH/TO/agent_mail.py --mailbox DIR [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys


def call(tool: str, box: str, *args: str) -> dict:
    env = dict(os.environ, AGENT_MAIL_DIR=box)
    r = subprocess.run([sys.executable, tool, *args], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit(f"{' '.join(args)} failed ({r.returncode}): {r.stderr.strip()}")
    return json.loads(r.stdout)


def ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if s else None


def fmt(t: dt.datetime | None) -> str:
    return t.strftime("%H:%M:%S") if t else "-"


def reconstruct(tool: str, box: str) -> dict:
    rows = call(tool, box, "list", "--json")["messages"]
    now = ts(call(tool, box, "status", "--json")["now"])
    # Order: by id (a ULID: millisecond clock + random), the order `list` uses.
    # `date` has one-second resolution, so ties on date are common and the id
    # is the only tie-break the record offers.
    rows.sort(key=lambda m: m["id"].upper())
    superseders: dict[str, list[dict]] = {}
    for m in rows:
        if m["supersedes"]:
            superseders.setdefault(m["supersedes"].upper(), []).append(m)
    answers: dict[str, list[dict]] = {}
    for m in rows:
        if m["reply_to"]:
            answers.setdefault(m["reply_to"].upper(), []).append(m)

    # ---- claim intervals -------------------------------------------------
    holds = []
    for c in rows:
        if c["type"] != "CLAIM":
            continue
        start, exp = ts(c["date"]), ts(c["expires"])
        end, why, by = exp, "EXPIRED_UNRENEWED", None
        valid_sup = [s for s in superseders.get(c["id"].upper(), [])
                     if s["type"] == "CLAIM" and s["from"] == c["from"]]
        if c["scope"] is None:
            end, why = start, "NAMES_NO_SCOPE"   # holds nothing; a release marker when it supersedes
        elif exp is None:
            end, why = start, "NO_VALID_LEASE"
        elif valid_sup:
            s = valid_sup[0]
            by = s["id"]
            sd = ts(s["date"])
            if sd is not None and sd >= exp:
                # superseded after it had lapsed: the lease ended by expiry and
                # the later claim is a fresh one citing it, not a renewal
                why, end = "EXPIRED_THEN_REFILED", exp
            else:
                end = sd
                if s["scope"] == c["scope"]:
                    why = "RENEWED"
                elif s["scope"] is None:
                    why = "RELEASED"
                else:
                    why = f"RESCOPED_TO:{s['scope']}"
        elif exp > now:
            end, why = None, "HELD_NOW"
        holds.append({"scope": c["scope"], "holder": c["from"], "claim": c["id"], "start": c["date"],
                      "end": end.strftime("%Y-%m-%dT%H:%M:%SZ") if end else None,
                      "end_reason": why, "ended_by": by, "expires": c["expires"]})

    # takeovers: a different sender holds the scope after this hold ended
    for h in holds:
        h["taken_over_by"] = None
        if h["end_reason"] in ("EXPIRED_UNRENEWED", "EXPIRED_THEN_REFILED", "RELEASED") or \
                h["end_reason"].startswith("RESCOPED"):
            for o in holds:
                if o["scope"] == h["scope"] and o["scope"] and o["holder"] != h["holder"] \
                        and o["claim"].upper() > h["claim"].upper() and (h["end"] or "") <= o["start"]:
                    h["taken_over_by"] = o["claim"]
                    break

    # ---- ASK lifecycle ----------------------------------------------------
    asks = []
    for a in rows:
        if a["type"] != "ASK":
            continue
        ans = answers.get(a["id"].upper(), [])
        asks.append({
            "ask": a["id"], "asked_at": a["date"], "from": a["from"], "to": a["to"], "subject": a["subject"],
            "state": "ANSWERED" if ans else "OPEN",
            "age_seconds": None if ans else int((now - ts(a["date"])).total_seconds()),
            "answers": [{"id": x["id"], "from": x["from"], "type": x["type"], "result": x["result"],
                         "by_addressee": x["from"] == a["to"]} for x in ans]})

    gaps = []
    if any(len(k["answers"]) > 1 for k in asks):
        gaps.append("an ASK has several answers: the record keeps all, nothing says which one the asker accepted")
    if any(k["answers"] and not k["answers"][0]["by_addressee"] for k in asks):
        gaps.append("an ASK was closed by someone other than its addressee (status raises no flag without --result)")
    if any(h["end_reason"] == "EXPIRED_UNRENEWED" for h in holds):
        gaps.append("an unrenewed lease cannot be told from a crashed holder: both are just EXPIRED")
    gaps.append("refused claims (exit 3) and holders killed mid-send leave no record at all")
    return {"now": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "holds": holds, "asks": asks, "gaps": gaps,
            "timeline": [{"id": m["id"], "date": m["date"], "type": m["type"], "from": m["from"], "to": m["to"],
                          "scope": m["scope"], "supersedes": m["supersedes"], "reply_to": m["reply_to"],
                          "subject": m["subject"]} for m in rows]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tool", required=True)
    ap.add_argument("--mailbox", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = reconstruct(a.tool, a.mailbox)
    if a.json:
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    print(f"# timeline (by id) at {out['now']}")
    for e in out["timeline"]:
        ref = f" supersedes={e['supersedes'][-4:]}" if e["supersedes"] else ""
        ref += f" reply_to={e['reply_to'][-4:]}" if e["reply_to"] else ""
        print(f"{e['date'][11:19]} {e['id'][-4:]} {e['type']:<7} {e['from']}->{e['to']} "
              f"{e['scope'] or ''}{ref} | {e['subject']}")
    print("\n# claim intervals")
    for h in sorted(out["holds"], key=lambda h: (h["scope"] or "", h["claim"])):
        print(f"{h['scope'] or '(none)':<8} {h['holder']:<8} {fmt(ts(h['start']))}..{fmt(ts(h['end']))} "
              f"{h['end_reason']}" + (f" by {h['ended_by'][-4:]}" if h["ended_by"] else "")
              + (f" -> taken over by {h['taken_over_by'][-4:]}" if h["taken_over_by"] else ""))
    print("\n# asks")
    for k in out["asks"]:
        who = ", ".join(f"{x['from']}{'' if x['by_addressee'] else '(NOT addressee)'}:{x['result'] or x['type']}"
                        for x in k["answers"])
        print(f"{k['ask'][-4:]} {k['from']}->{k['to']} {k['state']}"
              + (f" age={k['age_seconds']}s" if k["state"] == "OPEN" else f" answered by {who}") + f" | {k['subject']}")
    print("\n# what the mailbox cannot say")
    for g in out["gaps"]:
        print("- " + g)
    return 0


if __name__ == "__main__":
    sys.exit(main())
