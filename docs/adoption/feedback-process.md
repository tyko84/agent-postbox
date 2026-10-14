# Collecting and prioritising feedback

Postbox has few users, so the process is deliberately small. The goal is to learn whether it
works for people other than its author and where it breaks.

## Where feedback comes in

* **Field report** (issue template): a message did not arrive, arrived and was ignored, or
  was believed when it should not have been. This is the most valuable kind.
* **Bug** (issue template): the tool did something other than `PROTOCOL.md` says.
* **Questions and "I tried it"**: use the `Tried it` issue form, or title an issue `Question:`. A
  first-use report that says where you got stuck is worth more than a feature request.
* **Security**: privately, through the repository's security advisory link, never as an issue.

The field-report template asks for a pickup mechanism and evidence. Someone who only tried
it and got stuck is not a field report; use the questions below for them.

## What to ask someone who tried it

Copy this into a reply or a new issue:

```text
Thanks for trying agent-postbox. Four questions, answer any of them:

1. Which host(s) and OS? (Claude Code / Codex / other; macOS / Linux)
2. How far did you get, and how long did it take? (cloned / installed / first message sent /
   message picked up by the other session / used in real work)
3. Where did you get stuck or confused? Exact command and output if you have it.
4. Did a message ever not arrive, or arrive and get ignored? What did `doctor` say?

Please do not paste real mailbox contents.
```

## Triage order

1. **Anything that loses, corrupts or silently hides a message**, or a security issue.
2. **First-use failures**: a documented command that does not work, or a step where people
   stall. These come before features because adoption stops there.
3. **Host compatibility reports** (especially Codex, which has never been run in the real
   host): verify, then record in `docs/hosts.md`.
4. **Requests for new behaviour.** Only when at least two people hit the same gap, or one
   person shows a failure that cannot be worked around, and only if it fits the constraints
   (standard library, no daemon, files first, backward compatible). Otherwise label
   `wontfix` and say why.

## Rhythm

* Reply to every issue and PR within a day or two, even with only "seen, will look".
* Label small, well-defined tasks `good first issue` and say which file and which test.
* Once a month, read the open field reports together and write down what recurred. If
  nothing recurs, say that too.
* Record what was learned in `docs/` or the changelog, not in chat.
