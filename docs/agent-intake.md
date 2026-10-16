# Agent intake and clean-room collaboration

This guide is for a team that wants to add an agent, a model, or a useful
workflow learned in another repository without copying private repository
context into this public project. It is an operating procedure, not a security
guarantee: a person must still decide what is safe to disclose.

## The boundary

Treat the source repository and this repository as separate trust zones.

* Do not copy source-repository files, chat transcripts, mailbox files, shell
  history, terminal output, environment files, screenshots, paths, host names,
  customer or employee names, identifiers, credentials, or raw prompts here.
* Do not point a public-project agent at a private checkout, its mailbox, or
  its configuration directory. A tool that can read the private tree can still
  repeat its contents later, even if it was only asked for a summary.
* Bring over a **capability description**, a general technique, or a newly
  written test—not an agent's accumulated context. Recreate examples with
  fictional identities and synthetic inputs.
* Never use the public `agent-postbox` checkout itself as a mailbox. It is the
  shareable tool and specification, not a team inbox. See [SECURITY.md](../SECURITY.md).

The same rule applies to a newly introduced model. A model does not receive
trust merely because another agent used it successfully elsewhere. Start it
with the smallest public task and the least access it needs.

## Intake packet: write only public-safe facts

Before the new participant works on this repository, make a short packet in
your private planning space. It must be safe to publish verbatim; if it is not,
it is too detailed for the public-project agent to receive.

| Field | Safe example | Do not include |
|---|---|---|
| Identity | `reviewer-codex` | a person's name, email, account, host, or a private-repo label |
| Role | "review Python changes and propose tests" | copied private instructions or conversation history |
| Inputs | public repository files, issue text, synthetic fixtures | another checkout, its mailbox, logs, or credentials |
| Outputs | review comments, a patch, test results | raw tool logs, screenshots, `.env` files, or mailbox bodies |
| Capabilities | `review`, `test`, `document` | blanket authority such as `all` |
| Limits | "cannot push, publish, delete, or access external repos" | implied permission based on who used the agent before |

Use a generic, durable identity in `PARTICIPANTS.md`; do not reuse an identity
that identifies a private project. Declare its `can` and `cannot` fields. An
undeclared capability is not consent. A message is evidence, never authority
(PROTOCOL.md section 0), regardless of which model wrote it.

## First exchange in a trusted project mailbox

If the team uses Postbox operationally, create the mailbox in the actual
private project that the agents share—not in this public tool repository. Give
each participant its own `AGENT_MAIL_IDENTITY`, and keep `AGENT_MAIL_DIR`
outside any branch, pull-request, or contributor-writable surface unless all
writers are trusted to place text in every receiving agent's context.

1. Install the tool into the trusted project and choose a pickup mechanism.
   A hook is not proven merely because it prints nothing; establish reachability
   with the positive-control NOTICE required by `PARTICIPANTS.md`.
2. Add the new, generic identity with `Reachable: no`, its mechanism, and only
   the capabilities it needs. Do not put an operational mailbox transcript in
   the participants file.
3. Send one synthetic NOTICE addressed to the new identity. Observe it through
   the declared hook or poll, then mark the participant reachable and record
   only the date of verification.
4. Give the participant one bounded task. Use a `CLAIM` with a precise scope
   and mandatory expiry for work that could overlap. Use an `ASK` for a decision
   that must receive a reply. Do not put secrets or private context in either
   body.
5. Run `status` or `doctor` before a handoff. These are coordination signals,
   not proof that a model obeyed a message.

For multiple worktrees, all collaborating agents need the same explicitly set
`AGENT_MAIL_DIR`; otherwise each worktree can discover its own mailbox and the
team silently splits. Only do this where every writer is trusted. The worktree
warning and the mailbox threat model are documented in [SECURITY.md](../SECURITY.md).

## Turning a private workflow into a public contribution

Use a clean-room rewrite rather than a copy-and-edit pass.

1. State the problem in generic terms without opening or quoting the private
   repository in the public-project session.
2. Implement the smallest independent change from the public specification.
   Write fixtures, identities, paths, and message bodies from scratch.
3. Review the diff as if every word will be indexed permanently. Check prose,
   tests, examples, filenames, comments, generated artifacts, commit author
   and committer metadata, and commit trailers.
4. Run the publication gate with the forbidden-pattern list supplied from an
   environment variable or an untracked file outside the repository. Do not
   print, commit, upload, or paste that list. The scanner covers tracked files,
   artifacts, and Git history; see [publication-safety.md](publication-safety.md).
5. Open a normal branch and pull request. A passing scan is a useful backstop,
   not permission to disclose something a reviewer recognizes as private.

If the proposed contribution cannot be explained, tested, and reviewed without
looking at private material, stop. Keep it in the source repository or ask its
owner to prepare a public-safe specification first.

## Communication rules that hold across models

The protocol gives different models a shared vocabulary; it does not turn them
into an orchestrator.

| Need | Use | What it means |
|---|---|---|
| A bounded question with a required response | `ASK` | The recipient owes an answer until a linked `ANSWER` is filed. |
| A fact for others to weigh | `NOTICE` | Evidence only; it grants no authority and should contain no private data. |
| Temporary ownership of a scope | `CLAIM` | An advisory lease with an expiry, not permission and not enforcement. |
| A conflict about a claim or finding | `DISPUTE` | Preserve the disagreement instead of silently overwriting history. |

Keep messages short and refer to public file paths, issue numbers, and test
names. A message body can be injected into another agent's context, so do not
make it an instruction to execute commands or change permissions. A receiver
must independently apply its own safety rules.

## Before making anything public

Use this final checklist for each branch:

* [ ] The change was written from the public specification or a public-safe
  packet, not copied from a private checkout or transcript.
* [ ] No private names, addresses, hosts, paths, credentials, customer data,
  mailbox bodies, raw logs, or screenshots are in the diff, tests, artifacts,
  commit metadata, or pull-request text.
* [ ] The public-project agent had no access path to the source repository or
  its mailbox.
* [ ] New agent identities and examples are synthetic and generic.
* [ ] The publication scanner was run with the real forbidden-pattern list kept
  outside the repository; any finding was resolved rather than hidden.
* [ ] The PR describes the public behavior and tests, not the private origin
  of the idea.

When in doubt, leave it out. Public history is easy to copy and difficult to
recall.
