# Experiment v3: proposal (not approved, not run)

Status: **a proposal for the owner's approval.** Nothing here has been run, no harness has
been built, and no money has been spent. [experiment-v2-results.md](experiment-v2-results.md)
is unchanged by this page.

## What v2 can and cannot tell us

1. **Ceiling.** The notes arms completed 60 of 60. A design whose control is at the ceiling
   cannot show an advantage for the treatment; the best Postbox could do was tie. v2 could
   only lose or tie, so "against" was the likelier direction before it ran. The
   pre-registration's power section assumed the controls would sometimes miss the fact; they
   did not.
2. **A confounded treatment arm.** P agents were told to send with an inline `--body '<text>'`
   (the form `send --help` discourages), and 9 of 60 hit a host refusal that the notes arms
   could not hit. The arm measured the prompt and the host's permission check along with the
   protocol.
3. **One shape of task.** Two sessions, one hand-off, the second agent told where the notes
   are. That favours any channel the model is pointed at, and says little about the case
   Postbox's README claims: a fact that must reach an agent that is not looking.

## The question worth asking next

Postbox's only mechanism is that the hook puts a subject line in front of the agent at each
prompt, so the agent does not have to remember to look. The testable claim is therefore:
**when an agent works through several prompts and is not reminded to check, does mail
reach it more often than a notes file does?** v2 gave the notes arm a single re-entry
right after the other agent finished, which is the easiest case for a notes file.

## Proposed design (one candidate, for approval)

* **Multi-prompt consumer.** Agent B runs one live session with 4 scripted prompts
  (`claude -p --input-format stream-json`, as `scripts/e2e_claude.py` T3 already does). The
  fact arrives from A after prompt 1; B's prompts 2 to 4 are ordinary "continue" turns with no
  mention of the channel after the setup paragraph. Outcome: the fact appears correctly in
  B's code by prompt 4 (`b_ok`, from artifacts, as in v2).
* **Arms:** P (heredoc/`--body-file -` form taught, allow rule installed by the harness and
  stated in the pre-registration), N2 (notes, not told to read), N0, O. N1 is dropped.
* **Symmetry fixed in advance:** both channels get an equally permissive tool allowance and
  an equally long paragraph; harness refusals of the channel command are counted as failures
  of that arm AND reported separately, with the cause, rather than excluded.
* **Decision rule:** copy v2 section 7 unchanged, including "against Postbox" and "no
  advantage", with the control-ceiling check added as a validity gate: if N2 is at or above
  95% the contrast is declared uninformative rather than a tie.
* **Size and cost:** 60 instances at about 4 sessions per arm per instance; from v2's measured
  $0.0048 per session that is about $5 to $9 (ceiling $30). Haiku only, as in v2.
* **What it still cannot show:** other models, interactive use, hosts other than Claude Code,
  or that coordination outcomes improve in real teams.

## Decisions needed from the owner

1. Run it, or not. Likely outcomes are a tie or a loss again; the value is learning whether
   the ceiling was the reason.
2. If run: approve the spending ceiling (proposed $30, expected under $10).
3. Whether to extend the pre-registration by amendment or write a new document (proposed: a
   new `experiment-v3.md`, frozen before any run, with the same smoke-gate procedure).

Not proposed: re-running v2 with fixes, which section 14 of the v2 pre-registration rules out.
