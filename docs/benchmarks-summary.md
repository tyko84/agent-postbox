# Benchmark summary: pickup adapter versus polling

Moved from the README so that the README reaches its first command quickly.
Nothing here has changed.

What the adapter changes, from one run of `python3 scripts/scenario.py --seed 1`
(8 simulated agents, 400 scheduled messages plus their answers, crashes
injected, the same schedule for both columns):

| | Pickup adapter | Agent that polls when it remembers (half the time) |
|---|---|---|
| Messages in front of the recipient at its first awake prompt after the send | 492 of 492 | 270 of 493 |
| Messages in front of the recipient by the end of the run | 492 of 492 | 490 of 493 |
| Recipient's prompts between the send and the pickup, 95th percentile | 0 | 4 |

With the adapter every message was surfaced at the first prompt at which its
recipient was awake. An agent that remembers to poll half the time got about
half of them at that first chance, let four to five prompts go by at the 95th
percentile (4 with seed 1, 5 with seed 2), and still got over 99% by the end.
The agents are simulated and no LLM is involved; "half the time" is an
assumption, not a measurement; the totals differ by one, and move by one or
two from run to run, because races between claims are settled at run time;
and nothing here says what a model does with mail once it has it. How to run
it and how to read it: [benchmarking.md](benchmarking.md).

This is a property of the mechanism in a simulation, not a benefit shown with real agents.
The pilot with real agents did not show an advantage, and the pre-registered second experiment
went against Postbox on its task (the host refused the agent's `send` command in 9 of 60 runs).
They are summarised in the README ("Evidence so far") and written up in
[micro-experiment.md](micro-experiment.md) and
[experiment-v2-results.md](experiment-v2-results.md).
