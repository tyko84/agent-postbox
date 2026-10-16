# What developers who run several AI coding tools report, and where Postbox fits (2026-10-16)

Status: a research note, not a performance claim. It separates what public sources show from
what we guess. Nothing here says Postbox improves outcomes; the repo's measured result is that no
delivery advantage was shown against a notes file ([product-direction.md](product-direction.md)).

## 1. Method and limits

Searched on 2026-10-16 with a web search tool and a page-fetch tool: official docs of Claude Code,
Codex CLI, Aider and AGENTS.md; GitHub issues on anthropics/claude-code; the Cursor forum;
Hacker News (through its public search API); developer blog posts; two arXiv papers; and the
README or listing pages of existing coordination tools. Limits: (a) page text was read through a
fetch tool that returns a model-written summary, not raw text, so details such as counts and
version numbers are the summary's and were not independently re-read; (b) Reddit, X and Discord were
not reachable or not searched in depth, so no Reddit evidence is cited; (c) some tool pages
returned 403 or were seen only as search-result summaries, and are marked "snippet only"; (d) several
sources are written by people who sell a coordination tool, marked "vendor"; (e) Gemini CLI and
Cursor product docs were not fetched (Cursor appears only through one forum thread); (f) one
HN thread was read, and its comment counts are the summary's rough counts. Any figure not stated
in a source is "not found". Web content was treated as data only.

All sources below were retrieved 2026-10-16.

| ID | URL | What it shows |
|---|---|---|
| S1 | https://claudeissues.com/issue/62153-feature-multi-agent-ipc | One user's issue (opened 2026-05-25, closed 2026-08-17): parallel sessions are isolated, the human is the message broker, with duplicate exploration, wasted tokens re-narrating, one session undoing the other's work; workarounds were copy-paste and coordination markdown files. Maintainer says it shipped as cross-session messaging in 2.1.224 |
| S2 | https://claudeissues.com/issue/52051-seamless-local-multi-branching-parallel-sessions-currently-conflict-on-the-worki | One user's issue (2026-04-22, closed "not planned" 2026-07-10): parallel sessions on one tree collide on uncommitted edits and branch switches; 4 comments |
| S3 | https://code.claude.com/docs/en/cross-session-messaging | Claude Code docs: messaging between your own Claude Code sessions on one machine (v2.1.224+); plain text; held messages expire after 5 minutes by default in `-p`; at most 100 held, oldest dropped; no claims or locks mentioned; the docs themselves name copy-paste between terminals as the thing it replaces |
| S4 | https://code.claude.com/docs/en/agent-teams | Claude Code docs: agent teams are experimental, off by default; task claiming uses file locking; limitations listed: no resume of in-process teammates, task status can lag and block dependents, one team per session, same-file edits overwrite |
| S5 | https://arxiv.org/abs/2607.04697 | Study of 33,596 agent-authored PRs in 2,807 repos (Dec 2024 to Jul 2025): in 747 simulated merges of concurrent PR pairs, textual conflicts were 41.7% cross-agent vs 19.8% same-agent; cross-agent concurrency occurred in about 4.3% of repos |
| S6 | https://arxiv.org/abs/2606.19616 | Paper behind the grite tool: reports duplicate work falling from 78% to 0% with leases plus shared state. The abstract page does not say whether the agents were simulated; treat as unverified |
| S7 | https://hn.algolia.com/api/v1/items/46993479 | Ask HN "Are you using an agent orchestrator to write code?" (2026-02-12, 41 points, 60 comments per the search listing). Rough counts by commenter: code conflicts about 4, design conflicts and duplicate mechanisms about 3, stale shared state about 3, environment setup about 2, human review bottleneck about 3; about 5 report it working |
| S8 | https://hackernoon.com/four-ai-coding-agents-one-codebase-and-a-coordination-problem | Vendor (author sells Pawsly). Three incidents: incompatible API change, a decision that never reached three agents, an undocumented migration handoff. Not a controlled study |
| S9 | https://www.mejba.me/es/blog/claude-code-codex-side-by-side-same-repo | One developer running Claude Code and Codex in one repo: separate CLAUDE.md and AGENTS.md drifted ("npm" vs "pnpm"), agents do not share conversation state, about three weeks to a stable setup, weekly sync script |
| S10 | https://github.com/anthropics/claude-code/issues/43696 | One issue (2026-04-04, 8 events, closed as duplicate): `--continue` and `--resume` start with no prior context; it names three duplicate issues (#42376, #43303, #40319) |
| S11 | https://forum.cursor.com/t/is-it-possible-to-disable-usage-rules-from-agents-md-cursor-and-codex-needs-separete-rules-set/138971 | One forum post (2025-10-26, 4 replies): Cursor and Codex both read the same AGENTS.md and a rule for one slows the other; a Cursor staff reply confirms rules cannot be disabled selectively |
| S12 | https://code.claude.com/docs/en/memory | Claude Code docs: each session starts with a fresh context window; CLAUDE.md or AGENTS.md carry instructions across sessions |
| S13 | https://learn.chatgpt.com/docs/agent-configuration/agents-md | Codex docs: the AGENTS.md chain is rebuilt each run; 32 KiB default cap; no coordination between sessions mentioned |
| S14 | https://aider.chat/docs/usage/conventions.html | Aider docs page on conventions: loads files via `--read`; the page does not mention AGENTS.md or coordination |
| S15 | https://agents.md | AGENTS.md site: instruction format for one agent, 20+ tools listed; the page states "used by over 60k open-source projects" (self-reported, not checked); does not address coordination |
| S16 | https://a2a-protocol.org/latest/ | A2A: open agent-to-agent protocol (Linux Foundation); agent cards and HTTP; not aimed at local coding-agent coordination |
| S17 | https://github.com/Dicklesworthstone/mcp_agent_mail | MCP Agent Mail: FastMCP HTTP server, Python and `uv`, SQLite index plus git-stored Markdown messages; advisory file reservations with `ttl_seconds`, exclusive or shared, overlaps are reported but still granted, optional pre-commit guard; page showed 2.2k stars, 233 forks |
| S18 | https://www.producthunt.com/p/shepherd-9/shepherd-cd73e79b-36fc-4dbb-9c1c-07a63e746a8f | Shepherd launch page: self-hostable server, MCP clients (Claude Code, Codex, Cursor), leases and presence feed; expiry rules not stated; stars not found; maker's claims only |
| S19 | https://glama.ai/mcp/servers/s2hzbdblkq | agent-coord: Node 18+, shared JSON board file, file locks and task claims, default lock expiry 120 min, hard enforcement hook on Claude Code, voluntary elsewhere |
| S20 | https://github.com/polymatx/dibs | dibs: single Go binary, no daemon or database, claims with default 30 min and max 24 h TTL, enforcement hooks, handoff notes; page showed 2 stars |
| S21 | https://codex.danielvaughan.com/2026/06/20/before-the-pull-request-multi-agent-coordination-codex-cli-grite-duplicate-work-prevention/ | Describes grite: git-native append-only event log in `refs/grite/wal`, Rust, signed events with lock acquire, renew, expiry, denial; no server |
| S22 | https://github.com/clash-sh/clash | Clash: Rust CLI that simulates merges between git worktrees and warns before writes (Claude Code hook); read-only; 65 stars on the page |
| S23 | search-result summaries only (not fetched): Bothread (indiehackers.com/product/bothread, 403 on fetch), Befall (mcpmarket.com/server/befall), Coordinaut and dibs listings (glama.ai/mcp/servers/isnvvzzr02) | Each described as a coordination layer with claims, TTL or leases over MCP or a local app; no detail verified |
| S24 | https://dev.to/_3ffb5be7950c3a975b5d/how-to-prevent-multiple-ai-coding-agents-from-editing-the-same-files-4aib | Vendor (Vibsync founder), published July: argues git catches collisions after both agents did the work and proposes advisory claims checked before editing; an argument, not a report of incidents |

## 2. Needs

Classes: VERIFIED (a primary source shows developers report it), PLAUSIBLE (consistent with sources, no direct report), HYPOTHESIS (our guess). "Count" is independent sources; a vendor post or a doc statement counts but is flagged. Several sources are one person's report, so none of these is a measured prevalence; no user counts were found.

| # | Need | Class | Count | Sources and note |
|---|---|---|---|---|
| N1 | Manual relaying between sessions or tools (human as router, copy-paste) | VERIFIED | 3 (2 developer, 1 vendor doc) | S1 (reporter), S9 (blogger), S3 (docs describe the copy-paste it replaces) |
| N2 | Conflicting edits and collisions on shared files | VERIFIED | 5 | S1, S2, S7 (about 4 commenters), S4 (vendor warning), S5 (data: 41.7% vs 19.8% on simulated merges of PRs, not of live sessions); S24 is a vendor argument, not counted |
| N3 | Duplicate work and overlapping mechanisms | VERIFIED | 3 plus 1 unverified | S1 (duplicate exploration), S7 (about 3 commenters), S8 (vendor); S6 reports 78% duplicate work but its setup is unverified, not counted |
| N4 | Stale shared state and decisions that never reached another agent | VERIFIED | 3 | S7 (about 3), S8 (vendor), S1 |
| N5 | Repeated context loading and re-narrating | VERIFIED | 4 | S1 (wasted tokens), S10 (re-explain after failed resume), S9, S12 and S13 (docs: fresh context each session) |
| N6 | Interrupted or resumed sessions lose context | VERIFIED | 3 | S10 (plus three duplicates it names, not individually read), S4 (no resume of teammates), S9. That a resumed session also loses coordination state is not reported anywhere found |
| N7 | Instruction files drift or collide across hosts | VERIFIED | 3 | S9, S11, S14 (Aider page silent on AGENTS.md), S15 |
| N8 | No messaging or claims across different hosts | VERIFIED (by docs), no developer report | 2 | S3 (messaging reaches "your other Claude Code sessions"), S4 (one team per session, Claude only). No developer complaint specifically about cross-host messaging was found |
| N9 | Setup effort and environment overhead | VERIFIED, thin | 2 | S9 (about three weeks), S7 (about 2 commenters on isolated environments). The 60k-projects figure on S15 shows AGENTS.md adoption, not setup effort |
| N10 | Stuck or orphaned task ownership (a claim left behind) | PLAUSIBLE | 1 | S4: "task status can lag ... blocks dependent tasks". No report of a crashed holder found |
| N11 | Missed or unread messages | PLAUSIBLE | 2, indirect | S8 and S7 report state not reaching agents, not unread mail. Postbox's own "no notes in 22 hours" is one anecdote of ours ([why-postbox.md](adoption/why-postbox.md)) and is not counted |
| N12 | Unnecessary notifications or injected text | HYPOTHESIS | 0 fetched | Only search snippets about notification fatigue in unrelated products. The Claude docs add loop throttles, a hold and drop setting and an approval dialog (S3), which shows the vendor expects noise or risk, not that developers reported it |
| N13 | Someone needs to reconstruct afterwards who held, asked and answered what | HYPOTHESIS | 0 direct | S6 and S21 mine coordination logs, but no developer states this as a need |

Counter-evidence: about 5 commenters on S7 say multi-agent work succeeds with serial execution, planning agents and approval gates. S2 was closed "not planned", and the Claude Code maintainers shipped messaging (S1, S3) rather than claims. Separate worktrees remove shared-file collisions for many setups (S2; S3 links a worktrees page, not fetched); claims across worktrees need shared state anyway.

## 3. What already exists

"Claims, expiry, history" are as stated by each source; none was run by us.

| Alternative | What it does | Needs | Claims, expiry, history | What it does not do (per its source) | Src |
|---|---|---|---|---|---|
| Claude Code cross-session messaging | Claude messages your other Claude Code sessions on one machine, with idle notices | Claude Code 2.1.224+; sessions bind a socket | No claims or locks; held messages expire in 5 min in `-p`, 100 held max; message previews stay in the transcript | Does not reach Codex or Cursor; no owed-reply tracking; no ownership; plain text only | S3 |
| Claude Code agent teams | Lead plus teammates, shared task list, mailbox | Experimental flag, interactive session; tmux or iTerm2 for panes | Task claim uses file locking; task list stays on disk, config removed at session end | Claude only; one team per session; no resume; status can lag | S4 |
| Git worktrees | Separate checkout per agent | git | Not applicable | Do not carry messages or ownership between agents | S2, S3 |
| AGENTS.md | Static instructions for one agent | A file | None | No coordination between agents | S15 |
| A2A | Agent-to-agent protocol | HTTP, agent cards | Task model | Not aimed at local coding agents | S16 |
| mcp_agent_mail | Inboxes, search, file reservations | HTTP server, Python and `uv`, SQLite | Advisory leases with TTL; conflicts reported but still granted; git history | Needs a running server; claim refusal is not stated | S17 |
| Shepherd | Leases plus presence feed | Self-hosted server, MCP clients | Leases; expiry rules not stated | Not found: expiry, history | S18 |
| agent-coord | Locks, task claims, status messages | Node 18+, JSON board | Default 120 min expiry; enforcement hook on Claude Code only | Voluntary on other hosts; no per-message history | S19 |
| dibs | File claims, handoff notes | Go binary, git | 30 min default, 24 h max TTL; JSON and markdown files | Not found: owed replies | S20 |
| grite | Git-native event log of tasks and locks | Rust tool, git | Lock events with expiry; signed append-only log | Heavier model than a ledger; real-agent results unverified | S6, S21 |
| Clash | Warns of merge conflicts across worktrees | Rust, git | None | No claims, no messages | S22 |
| Bothread, Befall, Coordinaut | Claims or locks over MCP or a local app | Not verified | Not verified | Not verified | S23 |

Consequence: "exclusive claims with expiry for coding agents" is not a gap. At least dibs, agent-coord and mcp_agent_mail (S17, S19, S20) offer it, and Claude Code covers messaging for Claude-only users. Postbox must not claim to be the first.

## 4. What Postbox does today, per need

Postbox evidence is from the repo at origin/main c627cc4. `docs/context-cost.md` is not on main, so no context-cost measurement is cited here; none is claimed.

| Need | What Postbox verifiably does | Where | What Postbox does NOT do |
|---|---|---|---|
| N1 relay | One ledger of Markdown files any session can write and read; an optional hook prints waiting mail at the next prompt (Claude Code verified headless, 3 of 3 ASKs answered; Codex never run in the real host; Cursor has no hook) | README "What has and has not been shown"; [hosts.md](hosts.md) matrix; [why-postbox.md](adoption/why-postbox.md) | Nothing shows it reduces relaying by people; a notes file did as well in 3 experiments ([product-direction.md](product-direction.md) s1) |
| N2 conflicts | A second `CLAIM` on a held scope is refused, exit 3, nothing written | README (PROTOCOL.md sections 5, 30); `examples/two-agents/run.py` | Advisory only: stops no agent that ignores it; no real-agent measurement; across worktrees needs one shared `AGENT_MAIL_DIR`; no edit-time enforcement |
| N3 duplicates | Same refusal on a task scope | as N2 | Not measured; no check of semantic overlap |
| N4 stale state | NOTICE and ASK messages with derived open, answered, claimed status | README; PROTOCOL.md sections 4, 19 | No guarantee a model reads or acts on them |
| N5 repeated context | None | none | Postbox does not reduce context loading; pickup adds text to the prompt. Not measured (context-cost.md not on main) |
| N6 resume | Mail and claims persist in files across sessions; `doctor` and `status` name what a crash left behind; real-session resume check passed | README; [e2e-real-sessions.md](e2e-real-sessions.md) | Interactive mode not run; compaction loss of an owed ASK not demonstrated; mail to a renamed identity is stranded (ROADMAP section C, outside the repo) |
| N7 host drift | Identity and the ledger are host-independent | [hosts.md](hosts.md) | No instruction-file sync; not a replacement for AGENTS.md |
| N8 cross-host | Polling works on any host with a shell; hook on Claude Code and Codex | [hosts.md](hosts.md) | Codex unverified in the real host; Cursor documented only; single machine, local filesystem, POSIX only |
| N9 setup | One Python file, no daemon, database or network; installer; removal procedure | [why-postbox.md](adoption/why-postbox.md); [troubleshooting.md](troubleshooting.md) | "Five minute" first use has no human timing, machine time only (~3-4 s) (ROADMAP C); the hook is a prompt-injection surface (SECURITY.md) |
| N10 stuck ownership | Claims must carry an expiry (max 168 h); a lapsed claim stops counting; a dead holder's lock is reclaimed | README; PROTOCOL.md sections 5, 30, 33, 34 | A dead holder still blocks the scope until lease end; crashed and merely stopped are indistinguishable (ROADMAP B7, C) |
| N11 missed messages | Open ASK stays in the recipient's inbox and hook until answered; `list --live` polling | README; PROTOCOL.md sections 4, 19; [benchmarks-summary.md](benchmarks-summary.md): in a simulation, 492 of 492 messages surfaced at first awake prompt vs 270 of 493 for a poller who remembers half the time | Simulated agents; the "half the time" is an assumption; real agents showed no delivery advantage |
| N12 notifications | Nothing measured | none | No noise measurement; no per-message mute |
| N13 audit | Immutable files; `list` is a chronology; `status` derived | README; PROTOCOL.md sections 4, 19, 23 | Not tamper-proof; `from:` is self-declared; audit reconstruction under experiment v4 not tested (ROADMAP C) |

## 5. Three differentiators Postbox could realistically deliver

Each rests only on sections 2 to 4. None is shown to change agent outcomes.

1. **Claims that cannot be left forever, in a dependency-free tool that works on any host with a shell.** Basis: N2, N3, N10 are verified or plausible; Claude messaging has no claims (S3) and agent teams claims are Claude-only (S4); the other claim tools need a server, Node or a Go binary (S17, S19, S20). Postbox needs only Python's standard library. Falsified if: dibs or agent-coord users get the same behavior and prefer it; a real-process test finds the exit-3 refusal or lease reclaim failing; or the planned contested-scope experiment shows no fewer collisions than an atomic `mkdir` lock ([product-direction.md](product-direction.md) s3).
2. **Owed replies plus derived status in one ledger.** Basis: N4 and N11; the sources found offer messaging without owed-reply tracking (S3 drops held messages after 5 minutes by default in `-p`). Postbox keeps an ASK open until answered. Falsified if: a notes file with a checklist does equally well (the three experiments could not distinguish), or the open item of an ASK lost to compaction proves real (ROADMAP C).
3. **A history a person can read afterwards, stored in the repo as plain files.** Basis: N13 is only a HYPOTHESIS, so this is the weakest. S20 and S21 also keep history. Falsified if: a reader, or a script, cannot rebuild who held and answered what from the files alone (not yet tested), or developers say they never look.

Not a differentiator: pickup or delivery (a notes file matched it; Claude Code now ships messaging, S3), and cross-host reach alone (agent-coord and MCP tools already span hosts, S17 to S19; Postbox's own Codex support is unverified).

## 6. Target audience

Most compelling initial audience (HYPOTHESIS built on S9, S11 and N8): a developer who runs two or three sessions on one machine, mixing Claude Code with Codex or Cursor, in one working tree, who already keeps an AGENTS.md. Why: they hit instruction drift and manual relaying (S9, S11), native Claude messaging does not reach their other tools (S3), and they lack worktree isolation that would remove file collisions. Not the audience: Claude-only users with one worktree per agent, who are served by S3 and worktrees.

What might motivate install (HYPOTHESIS; no user research was found): seeing a second claim refused, and a dead session's claim expiring on its own, in the two-agent demo. What might make them abandon it: the hook refusing or blocking commands (9 of 60 runs in experiment v2); setup time; claims being advisory and ignored; added prompt text; the SECURITY.md risk of mailbox text reaching the agent in a repo others can write to; Claude Code's native messaging being enough; Codex hook never verified in the real host. A field report from this audience is the missing evidence.

## 7. Claims Postbox may and may not make today

May say:
* It is a small file-based ledger with exclusive claims (a second claim is refused, exit 3), mandatory expiry (at most 168 h), owed-reply tracking, derived status and an immutable history, and these are checked by tests with simulated agents and real processes.
* Polling and hooks are both supported; the hook was run in headless Claude Code (small model), not in interactive mode, and not in a real Codex or Cursor.
* In a simulation, an adapter surfaced 492 of 492 messages at the first awake prompt against 270 of 493 for a poller who remembers half the time; the agents were simulated.
* It has no daemon, database, network or dependency.
* Other tools offer claims with expiry (S17, S19, S20), and Claude Code has native session messaging (S3).

May not say:
* That agents coordinate better, finish more work, conflict less, waste fewer tokens, or that developers want it. No experiment with real agents shows it, and no prevalence figures were found.
* That pickup beats a notes file (three experiments: null, against, uninformative).
* That it reduces context loading or setup time, or that it is first, unique or "the" solution.
* That claims stop an agent that ignores them, or that history is tamper-proof.
* That Codex or Cursor are supported beyond what [hosts.md](hosts.md) says; that it runs on Windows or across machines.
* Any adoption, star or user count for Postbox.
