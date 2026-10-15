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
Thanks for trying agent-postbox. Six questions, answer any of them:

1. Which host(s) and OS? (Claude Code / Codex / other; macOS / Linux)
2. How far did you get? (cloned / installed / first message sent / message picked up by the
   other session / used in real work / removed)
3. How many minutes from the clone to your first message, by your own clock?
4. Did the other agent show the mail without being told to look? (yes / no / only when told)
5. Did any permission prompt or denial hit a mail command? What was the command, and what
   happened next? What did `doctor` print on its `send_permission:` line?
6. Where did you get stuck or confused? Exact command and output if you have it.

Please do not paste real mailbox contents. Note that my own experiments did not show an
advantage over a notes file; I am asking about installation, pickup and permissions.
```

## Triage order

Label each issue with one of these and work in this order.

1. **Anything that loses, corrupts or silently hides a message**, or a security issue.
   (Security goes through the private advisory link.)
2. **Pickup did not reach the agent** where it should have (hook not firing, wrong identity,
   host change). A disabled adapter looks like an empty inbox, so these are easy to miss.
3. **Permission friction**: a mail command denied or prompted. Check whether
   `doctor`'s `send_permission:` line and [troubleshooting.md](../troubleshooting.md) already
   cover it; if not, that is a documentation or adapter defect.
4. **First-use failures**: a documented command that does not work, or a step where people
   stall. These come before features because adoption stops there.
5. **Host compatibility reports** (especially Codex, which has never been run in the real
   host): verify, then record in `docs/hosts.md`.
6. **Requests for new behaviour.** Only when at least two people hit the same gap, or one
   person shows a failure that cannot be worked around, and only if it fits the constraints
   (standard library, no daemon, files first, backward compatible). Otherwise label
   `wontfix` and say why.

## Summary format after the trial

Write it in `docs/` in a pull request, with the denominator on every number:

```text
Trial summary, <date>. Testers who tried: N of 5 invited.
Installed: x of N. Message exchanged without owner help: x of N. Median minutes to first
message (human clock): m (range a to b).
Pickup reached the agent: yes x, no x, only when told x (hosts: ...).
Permission friction: x testers hit a prompt or denial; causes: ...; fixed by: ...
Removal clear: x of N.
Stuck points by step: step -> count.
Issues opened: #..., #... (one per distinct problem).
What this does not show: whether Postbox improves agents' work.
```

## Rhythm

* Reply to every issue and PR within a day or two, even with only "seen, will look".
* Label small, well-defined tasks `good first issue` and say which file and which test.
* Once a month, read the open field reports together and write down what recurred. If
  nothing recurs, say that too.
* Record what was learned in `docs/` or the changelog, not in chat.
