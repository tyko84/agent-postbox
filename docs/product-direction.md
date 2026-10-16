# Product-direction assessment (2026-10-16)

Status: an assessment from the maintainers' working notes, written after three experiments and
before any change to the README's positioning. It separates what was measured from what is
believed. Nothing here is a performance claim.

## 1. What the three experiments measured, and what they could not

| | Question asked | Outcome | Result | Why it could not show a benefit |
|---|---|---|---|---|
| Micro-experiment (20 pairs) | Does a fact reach the consumer under each arrangement? | Fact used correctly | Null | The consumer found the fact under every arrangement, including a notes file it was told to read |
| v2 (60 pairs) | Does automatic pickup beat an unprompted notes file? | Task completion | Against Postbox, 51/60 vs 60/60 | Control at the ceiling; 9 Postbox failures were a host refusal of the command text, caused partly by the arm's own prompt |
| v3 (20 pairs, stopped) | Same, with a multi-prompt consumer | Task completion | Uninformative | Control at 20/20 again |

All three used one small model, one host (Claude Code), short two-ticket tasks, and an outcome
(did the consumer end up with the fact) that a shared file already delivers. They tested
**delivery**. Delivery is the part of Postbox that a notes file also provides, and the part Claude
Code partly provides itself (the unverified file-change hypothesis in
[experiment-v3-results.md](experiment-v3-results.md)). They never tested what the protocol adds
beyond delivery.

## 2. What the protocol adds beyond delivery (from PROTOCOL.md, not from experiments)

* **Exclusive claims.** A `CLAIM` takes a scope lock held by the kernel (section 30); a rival gets
  exit 3 and nothing is written. A notes file cannot refuse a second writer.
* **Mandatory expiry and recovery.** Every claim expires; a dead holder's lock is reclaimed;
  `status` and `doctor` name what a crash left behind (sections 5, 30, 33, 34).
* **Owed replies.** An open `ASK` is derived as owing a reply and stays visible in the
  recipient's inbox and the hook until answered (sections 4, 19).
* **Derived status, immutable history.** Status is computed from files that are not edited;
  `list` is a forensic chronology (sections 4, 19, 23).
* **Evidence, not authority.** A message never authorises an action (section 0).

These are properties of the tool. Offline tests and simulated processes (stress, hardening,
coordination audit) show the tool behaves this way. **No experiment with real agents has shown
that these properties change what agents do or how well a team's work turns out.**

## 3. Which outcome would measure that value

| Candidate outcome | Fit to what Postbox claims to provide | Notes |
|---|---|---|
| Task completion | Weak | Ceiling in all three experiments; a shared file already completes |
| **Ownership conflicts / duplicate work** under contested scope | **Strong** | The one thing an unenforced notes file structurally cannot prevent; but a credible baseline (an atomic `mkdir` lock) can, so the fair comparison is against that |
| **Recovery after an interrupted holder** | **Strong** | A claim expires and is taken over; a plain lock directory or a notes line stays stuck unless an agent reasons its way out |
| **Auditability** (reconstruct who held/asked/answered what, from artifacts alone) | Strong, and cheap to test | Can be scored by a reader or script with no model in the loop for the reconstruction |
| Owed replies surfaced | Medium | Delivery again; the hook helps, notes also work |
| Manual intervention count | Medium | Useful secondary measure |
| Cross-session continuity | Weak as a differentiator | Notes files also persist |
| Developer visibility (`status`, `inbox`) | Medium | Needs humans to judge; defer |

## 4. The strongest defensible positioning

> A small, file-based coordination ledger for AI coding agents that share a repository:
> exclusive claims with mandatory expiry, owed-reply tracking, derived status and an
> append-only history you can inspect afterwards. Pickup into the agent's prompt is a
> convenience on top, not the point.

What can honestly be said today: the protocol defines these properties and tests verify them
(simulated agents, real processes); the delivery premise did not show an advantage over a notes
file for a small model in Claude Code. What cannot be said: that agents coordinate better, finish
more work, or waste less effort with Postbox. The README should say the first and refuse the
second until a coordination-specific experiment says otherwise.

## 5. What to do next

1. Verify the claim, recovery and audit properties against the protocol with adversarial
   real-process scenarios and fix demonstrated defects (workstream A).
2. Design, and pilot cheaply on existing subscription usage, a coordination-specific experiment
   with credible baselines (soft notes, atomic lock directory): contested scope, a holder killed
   mid-task, and audit reconstruction. Do not run a paid or large run.
3. Only after 1 and 2, reposition the README. A draft may be prepared now and not merged.
