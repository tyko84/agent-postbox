# Positioning proposal (README patch applied 2026-10-16; the GitHub repository description is not changed)

Status: the README patch in section 7 was applied (with one added caveat about worktrees and
`AGENT_MAIL_DIR`, section 43); **the GitHub repository description has not been changed** (the
proposed text below is for the owner to set). It follows the
maintainers' assessment in `docs/product-direction.md` (pending on the branch
`docs/product-direction`, pull request #57 when this was written): three experiments measured
delivery and did not show an advantage; the defensible value is exclusive claims with mandatory
expiry and recovery, owed-reply tracking, derived status and an inspectable history, with pickup as
a convenience. The README should say the first part and refuse the second until a
coordination-specific experiment says otherwise.

How to read the tags. **[E: ref]** means a sentence a reader can check against the named
PROTOCOL.md section, test file or experiment document in this checkout. **[B]** means a belief:
reasonable, untested, and written as one. A sentence with neither tag is a definition or a
restatement of the sentence before it. Section numbers are PROTOCOL.md's.

Contents: 1. current wording; 2. proposed tagline, repository description, intro and table;
3. explanations; 4. differences from four alternatives; 5. comparison table; 6. claims refused;
7. README patch.

## 1. Current wording (README.md on `main`, quoted)

Tagline (line 5):

> **Mail between coding agents that run in the same repository.**

Repository description (GitHub, read 2026-10-16):

> Mail between AI coding agents in one repo: messages appear in the receiving agent's next prompt, no polling, no daemon. Python stdlib only.

Intro (lines 7 to 25):

> If you run two or three coding-agent sessions on one repository (Claude Code, Codex, ...),
> you end up as the router: a question copied out of one session, pasted into another, the
> answer carried back. agent-postbox lets the agents write to each other instead. A message is
> a Markdown file in a directory of your repository. With a small hook installed, the receiving
> session sees waiting mail in its context at its next prompt, so it does not have to remember
> to look; hosts without a hook can poll with one command.
>
> It is for developers who already run several agent sessions themselves and want them to talk.
> It is **not** an orchestrator: it does not start, plan, assign or supervise anything, and it
> cannot make an agent act on a message. It is a mailbox plus a way to put mail in front of the
> reader.
>
> No database, no daemon, no dependencies, no network. The tool is one Python file (standard
> library only), and identity is a name you choose.
>
> **What has and has not been shown.** The mechanism works and is heavily tested. A benefit has
> **not** been demonstrated: a pilot found no advantage over a shared notes file, and a larger
> pre-registered experiment went against Postbox under a restrictive permission mode (see
> [Evidence so far](#evidence-so-far)). ...

What is wrong with it, given the assessment: it leads with delivery ("mail", "next prompt, no
polling"), which is the one property the experiments tested and a notes file also provides
[E: micro-experiment.md, experiment-v2-results.md, experiment-v3-results.md]; the claims and
history are mentioned only further down. It also stops at two experiments; a third exists and was
uninformative.

## 2. Proposed wording

**Tagline.**

> A small file-based coordination ledger for AI coding agents that share a repository: exclusive
> claims with mandatory expiry, owed-reply tracking, and a history you can inspect afterwards.

Checks: claims are exclusive per scope, refused with exit 3 [E: section 5, README runnable block];
expiry is mandatory (`send` refuses a CLAIM without `--expires`) [E: section 5]; an open ASK is derived
as owing a reply [E: sections 4, 19]; the history is the immutable message files and `list`
[E: sections 2, 19]. "Ledger" is a description of the files, not a claim about integrity (see
the tamper note in 3.2).

**Repository description** (one line, 299 characters including spaces; the limit is 350):

> File-based coordination ledger for AI coding agents sharing one repo: exclusive claims with mandatory expiry, owed-reply tracking, status derived from immutable message files, an inspectable history. Optional hook shows mail at the next prompt. Stdlib Python, POSIX. No outcome benefit demonstrated.

**README intro and "when to use" table.** The exact text is in the patch in section 7, so that
nothing is stated twice in different words. Summary of the changes: the first screen now says what
the protocol defines, each bullet cites its sections, pickup moves to the last bullet and is called
a convenience, "What has and has not been shown" now covers all three experiments and says what
they did not test, and a table says when it fits, when a shared notes file is enough, and when it is
the wrong tool.

## 3. How to explain the three ideas

### 3.1 Claims and leases

Proposed text for a docs page or the README: "A `CLAIM` says 'I am working on X until T'. It is a
lease, not a lock and not a permission [E: section 5]. `send` refuses one without an expiry, and
refuses an expiry more than 168 hours away [E: section 5]. With `--scope`, a second held claim on
the same scope from another sender is refused with exit 3 and nothing is written; simultaneous
claimants produce exactly one winner [E: sections 5, 30; selftest.py pins it with real
subprocesses]. A claimant that dies mid-`send` leaves nothing to break: the kernel releases the
`flock` when the process exits [E: section 30]. A claim that was published and whose session then
died stops counting as held when its expiry passes, and `status` / `doctor` say what is
left [E: sections 5, 33, 34; `examples/two-agents/run.py --takeover` shows it, scripted, once the
`--takeover` change is merged]. Nothing stops an agent from ignoring a claim [E: README, Security
and limitations]."

Two things that must stay in the text: the lease is advisory, and the lock is only trusted on local
filesystems [E: section 30, "Assumed"].

### 3.2 Auditability

"Every message is a file the tool never rewrites; a reply is a new file that cites the old id
[E: sections 2, 4]. `list` prints the full chronology, including answered, expired and superseded
messages; `inbox` is the actionable view [E: section 19]. `status` and `doctor` report holders,
expired claims, crash leftovers and replies that cite a missing id [E: sections 33, 34]. A listing
never contains a body [E: section 37]."

Limits to state beside it: `from:` is self-declared, and the optional uid binding compares the file's
OS owner, which is evidence and not authentication [E: section 40]; a person with write access can edit
or delete files, so the history is inspectable, not tamper-proof [E: section 0 and README Security;
nothing in the repository claims otherwise]. Whether a team gets value from reconstructing events from the
files is [B]; the product-direction assessment rates it "strong, and cheap to test" and nobody has tested it.

### 3.3 Cross-session continuity

Say little. "Messages persist as files across sessions: an ASK stays live until answered and a CLAIM
until it expires, with no per-agent read state to drift [E: section 9]. A session that starts later sees
what it still owes, `status` names what it still holds [E: sections 9, 34]." The assessment rates this a weak differentiator
because a notes file also persists [E: product-direction.md, section 3]; do not present it as a reason to
choose Postbox.

## 4. How it differs from four alternatives

Each paragraph states what the alternative does, what Postbox adds, and which part is a belief.
Descriptions of other tools are general and were not tested here.

**(a) Orchestration frameworks.** They start, plan, assign or supervise agents; Postbox does none of
that and cannot make an agent act on a message [E: README intro, why-postbox.md]. It carries messages
and claims between sessions that something else (usually you) runs. They are not alternatives
so much as different layers; whether the two compose is [B], untested. Postbox says nothing about
any particular framework's features.

**(b) A shared notes or task file.** A notes file delivers a fact as well: in the micro-experiment and
experiments v2 and v3 the consumer found the fact under a notes file at least as often as under
Postbox [E: micro-experiment.md; experiment-v2-results.md (60 of 60 against 51 of 60);
experiment-v3-results.md (20 of 20 each)]. What a notes file cannot do is refuse a second writer or lapse
a stale line by itself; Postbox's claim refusal and mandatory expiry do [E: sections 5, 30]. It also
tracks which questions still owe an answer [E: sections 4, 19]. Whether those differences change what real
agents do is untested [E: product-direction.md, section 2]. Cost: in v3 Postbox cost about 45% more
per run than the second notes arm, descriptively and for one model [E: experiment-v3-results.md].

**(c) Git branches, pull requests, CODEOWNERS.** These separate or review work: branches and worktrees
give each agent its own copy, and CODEOWNERS (a GitHub feature, not examined here) names reviewers per path at
pull-request time. Postbox acts earlier and on a shared working tree: it records who is working on what
*before* a conflict exists, and it records questions between sessions, which none of the three does.
If you can give every agent its own branch or worktree, you may not need claims at all [B]; the two are
compatible (a claim can name a branch or a path) [B, untested].

**(d) An atomic `mkdir` lock.** `mkdir` is atomic, simple and needs no code, and it is the baseline the
assessment says Postbox must be compared with [E: product-direction.md, section 3]. What it lacks
by itself is an expiry: a holder that dies leaves the directory, and a lock broken by age has a race
(PROTOCOL.md section 30 describes exactly that failure in this project's own earlier exclusive-create
lock, where two agents could both remove an old lock and both proceed) [E: section 30]. Postbox
separates the two jobs: a kernel `flock` serialises check-and-publish for milliseconds and is released
by the kernel on exit, and the published claim carries a mandatory expiry and a holder name
[E: sections 5, 30]. Whether an agent that sees a stale `mkdir` lock gets itself out of it without
Postbox is [B], and no experiment has compared the two [E: product-direction.md, section 5, item 2,
describes the planned comparison].

## 5. Short honest comparison

| | Postbox | Shared notes file | Atomic `mkdir` lock | Branches, PRs, CODEOWNERS | Orchestration framework |
|---|---|---|---|---|---|
| Gets a fact to another session | Yes (file; hook or poll) | Yes (file) | No | Via the PR text | Depends on the framework |
| Refuses a second claimant | Yes, per `--scope`, exit 3 | No | Yes, while the directory exists | Not at edit time | Depends [B] |
| Claim lapses by itself | Yes, expiry is mandatory | No | No | Not applicable | Depends [B] |
| Tracks who still owes an answer | Yes (open ASK) | No | No | PR review state, a different thing | Depends [B] |
| History of who held / asked / answered | Yes, files plus `list` | Only if the file is versioned | No | Yes, in git and the PR | Depends [B] |
| Enforced against an agent that ignores it | No, advisory | No | No | Branch protection can enforce a merge rule (not examined here) | Depends [B] |
| **Measured with real agents** | **Delivery: no advantage over a notes file (micro, v2, v3). Claims, expiry, owed replies, history: not measured** | Delivery: as good or better | Not measured | Not measured | Not measured |
| **Verified by tests** | Yes: simulated agents and real processes (selftest, stress, hardening, field, scenario) | Not applicable | Not applicable | Not applicable | Not applicable |

"Depends [B]" means this document makes no claim about any specific framework.

## 6. Claims this proposal refuses to make

* That agents coordinate better, finish more work, waste less effort or produce fewer conflicts with
  Postbox [E: product-direction.md, section 4; no experiment tested it].
* That it saves time or tokens. The one cost observation points the other way: in v3 Postbox cost about 45%
  more per run, descriptively, one model, and that document says it is no general cost figure
  [E: experiment-v3-results.md].
* That pickup is "reliable" or "automatic" in interactive Claude Code (headless only,
  [E: docs/e2e-real-sessions.md]); that Codex works (never run in the real host
  [E: docs/hosts.md]); anything about Cursor.
* That a claim protects a file. It is advisory [E: README, Security and limitations].
* That a `--result DONE` means the work was done [E: section 39].
* That the history is tamper-proof, or that a sender is who it says [E: section 40].
* That it is better than an atomic `mkdir` lock. It has more features; the comparison has not been run
  [E: product-direction.md].

## 7. README patch

A unified diff against `README.md` on `main` at `618a818`. It replaces the tagline, the intro and the
"What has and has not been shown" paragraph, and adds the "When to use it, and when not to" table
before the Quickstart. It was applied to a scratch copy of the tree: `test_readme_examples.py` (8 tests) and
`test_publication.py` (106 tests) passed on that copy, and `git apply --check` accepts it on `main`. The
table's reference to `examples/two-agents/run.py` step 6 holds today. Nothing else in the README changes;
the "Evidence so far" section already lists the three experiments.

```diff
--- a/README.md
+++ b/README.md
@@ -2,28 +2,54 @@
 
 [![ci](https://github.com/tyko84/agent-postbox/actions/workflows/ci.yml/badge.svg)](https://github.com/tyko84/agent-postbox/actions/workflows/ci.yml)
 
-**Mail between coding agents that run in the same repository.**
+**A small file-based coordination ledger for AI coding agents that share a repository: exclusive claims with mandatory expiry, owed-reply tracking, and a history you can inspect afterwards.**
 
-If you run two or three coding-agent sessions on one repository (Claude Code, Codex, ...),
-you end up as the router: a question copied out of one session, pasted into another, the
-answer carried back. agent-postbox lets the agents write to each other instead. A message is
-a Markdown file in a directory of your repository. With a small hook installed, the receiving
-session sees waiting mail in its context at its next prompt, so it does not have to remember
-to look; hosts without a hook can poll with one command.
+If you run two or three coding-agent sessions on one repository (Claude Code, Codex, ...), you end
+up as the router and the referee: a question copied out of one session and pasted into another, a
+guess about who is editing which file. agent-postbox gives the sessions a shared ledger instead.
+Every entry is a Markdown file in a directory of your repository, and the tool derives what is
+open, held or expired from those files. What the protocol defines, and the tests check
+(PROTOCOL.md sections in brackets):
 
-It is for developers who already run several agent sessions themselves and want them to talk.
-It is **not** an orchestrator: it does not start, plan, assign or supervise anything, and it
-cannot make an agent act on a message. It is a mailbox plus a way to put mail in front of the
-reader.
-
-No database, no daemon, no dependencies, no network. The tool is one Python file (standard
-library only), and identity is a name you choose.
+* **Exclusive claims.** A `CLAIM` on a `--scope` is a lease. A second claim on the same scope from
+  another sender is refused with exit 3 and nothing is written (sections 5, 30).
+* **Mandatory expiry.** `send` refuses a `CLAIM` without an expiry (at most 168 hours out), so a
+  session that dies cannot hold a scope forever: a lapsed claim stops counting as held, and a lock
+  left by a crashed claimant is reclaimed (sections 5, 30).
+* **Owed replies.** An `ASK` stays open, and visible to its recipient, until a later message cites
+  its id (sections 4, 19).
+* **Derived status, an inspectable history.** Status is computed from files the tool never edits.
+  `list` shows the full chronology, and `status` names who holds what and what a crash left
+  behind (sections 4, 19, 33, 34).
+* **Pickup, as a convenience.** With a small hook installed, the receiving session sees waiting
+  mail in its context at its next prompt; hosts without a hook poll with one command.
 
-**What has and has not been shown.** The mechanism works and is heavily tested. A benefit has
-**not** been demonstrated: a pilot found no advantage over a shared notes file, and a larger
-pre-registered experiment went against Postbox under a restrictive permission mode (see
+It is for developers who already run several agent sessions themselves. It is **not** an
+orchestrator: it does not start, plan, assign or supervise anything, and it cannot make an agent
+act on a message or honour a claim. No database, no daemon, no dependencies, no network. The tool is
+one Python file (standard library only), and identity is a name you choose.
+
+**What has and has not been shown.** Tests (simulated agents and real processes, in CI) show that
+the tool behaves as listed above. No experiment with real agents has shown that these properties
+change what agents do or how well their work turns out. Three experiments measured something
+narrower, whether a fact reaches another session, and a shared notes file did as well or better:
+a pilot found no advantage, a pre-registered run went against Postbox (51 of 60 against 60 of 60,
+under a restrictive permission mode), and a third stopped at its ceiling and was uninformative (see
 [Evidence so far](#evidence-so-far)). Why it exists, and the design:
 [docs/adoption/why-postbox.md](docs/adoption/why-postbox.md).
+
+## When to use it, and when not to
+
+| Your situation | Verdict | What backs it |
+|---|---|---|
+| Several agent sessions share one working tree and you want a second claim on a file refused, with leases that expire | Fits | PROTOCOL.md sections 5 and 30; tests with real processes; step 6 of `examples/two-agents/run.py`. Claims are advisory, and no experiment has measured the effect on real agents |
+| You want to reconstruct afterwards who asked, held and answered what | Fits | Sections 4, 19, 34: status is derived from files; `list` is the chronology. It is not tamper-proof: a person can edit the files |
+| You only need a fact to reach another session ("the endpoint moved") | A shared notes file did as well in every experiment we ran | [Evidence so far](#evidence-so-far) |
+| Each agent already works on its own branch or worktree | Probably not needed | Belief, untested: separate checkouts avoid shared files |
+| You need agents started, planned, assigned or supervised | Not this | It is not an orchestrator |
+| You need something that stops an agent that ignores a claim, or that authenticates who sent a message | Not this | Claims are advisory; `from:` is self-declared (Security and limitations; PROTOCOL.md section 40) |
+| You need proof that a requested action was done | Not this | `--result` is the sender's claim, never proof (PROTOCOL.md section 39) |
+| Several machines, a network filesystem, or Windows | Not supported | Local filesystems and POSIX only (Security and limitations) |
 
 ## Quickstart
 
```

To apply: save the block above as a file, check it with `git apply --check <file>`, then `git apply <file>`.
Do this only after the coordination-specific experiment the assessment asks
for, or sooner if the owner accepts the wording as it stands. The repository description and the
tagline are separate decisions from the intro.
