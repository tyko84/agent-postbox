# Performance audit (phase 10)

Result: cost is linear in mailbox size everywhere measured; no defect, no product-code change.
`send` does not grow with the mailbox. Reading commands and the prompt hook cost about
0.04 ms per message on top of a ~30 ms floor, so the hook passes 200 ms at roughly 4,000 messages.

## Method

`scripts/perf_audit.py` (stdlib only, re-runnable, writes to a temp dir). It writes valid message
files (the bytes `send` writes: 70% NOTICE, 20% ASK to `b`, 10% ANSWER, all dated within 24 h so
every NOTICE is live), checks that `load_messages` rejects none, then times each command as a
subprocess with `time.perf_counter` and reports the median of 5. The hook runs as the host runs
it: `python3 hooks/agent_mail_check.py` with `AGENT_MAIL_IDENTITY=b`. Peak memory is
`resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` of a fresh child.

Machine: Apple M4 Max, 16 CPUs, macOS 27.0 arm64, Python 3.13.7 (Homebrew), local APFS, warm
page cache. `python3 -c pass` startup is 16 ms.

## Results (median of 5, ms)

| messages | send | list | list --to --live | inbox | status | hook | hook peak MB | status peak MB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 48 | 51 | 51 | 51 | 52 | 37 | 22 | 34 |
| 1,000 | 49 | 84 | 84 | 85 | 86 | 71 | 24 | 36 |
| 5,000 | 50 | 239 | 236 | 236 | 242 | 233 | 34 | 46 |
| 20,000 | 49 | 921 | 979 | 911 | 925 | 879 | 72 | 83 |

- Growth: 5,000 to 20,000 messages is 3.8x in time for 4x in size: linear. Memory is about
  3 KB per message (hook) over a ~22 MB interpreter floor.
- Hook over 200 ms: between 1,000 (71 ms) and 5,000 (233 ms); linear fit puts the crossing at
  about 4,000 messages on this machine. Slower disks or CPUs cross earlier. The hook reads and
  validates every file on every prompt; there is no index, by design (no cache state).
- Worst case for open-ASK logic: 10,000 messages that are all open ASKs to `b` (an unanswered
  pile) measured hook 520 ms, inbox 520 ms, status 480 ms; 5,000 gave 231 / 238 / 283 ms.
  No quadratic term appeared (the hook computes `open_asks` once).
- Profile of `load_messages` at 5,000: dominated by `open()` per file, header character
  validation and frontmatter parse; nothing avoidable by more than a small factor.
- Concurrent senders: 8 processes x 50 sends into one empty mailbox: 400 files, 400 distinct
  ids, 400 distinct subjects, every exit 0, 2.9 s wall (about 7 ms of wall per send, 8x parallel).
- Disk: 159-164 bytes of content per message (short body); each file occupies 4,096 bytes on APFS,
  so the real footprint is block-bound: about 82 MB at 20,000 messages.

## Caveats and not measured

- One machine, one OS, warm cache; cold-cache first read, network or synced filesystems (where
  per-file `open` is far slower and the hook would cross 200 ms much earlier), Linux and Windows
  were not measured.
- Messages are ~160 bytes. Larger bodies were not swept; the loader reads each file in full.
- Mixed claims, scopes, idempotency keys, supersede chains and `--key` sends were not scaled.
- Send latency was measured into a populated mailbox only after the build; the first 5 sends per
  size were taken, so mailbox growth during the run is under 0.1%.
- Not measured: `doctor`, `show`, `verify`, `latency`, the other host adapters, install.
- `verify` is a per-message evidence check and was not used to validate the generated files;
  validity was checked by `load_messages` returning no rejects.

## When the mailbox gets large (observed 2026-10-15, not a feature)

Nothing in Postbox deletes or archives messages, and every read scans every file, so the
prompt hook slows as the directory grows (about 4,000 messages to pass 200 ms on the
machine above). What was checked by hand, on a two-message mailbox:

- Readers look only at `*.md` files directly in the mailbox directory. Moving a message into
  a subdirectory such as `archive/` removes it from `list`, `status`, `inbox` and the hook.
- Moving only one side of a thread is visible: with the ASK archived and its ANSWER left,
  `doctor` reported `dangling_replies: 1` (a report, not a FAIL). Move both sides of a
  finished thread together, and nothing that still holds a live CLAIM or an open ASK.
- This is a manual file move, not a supported operation. It was not tested with `verify`
  on archived messages, with claims, or at scale, and history that has been moved is no
  longer protected by the append-only checks. If mailboxes this large turn out to be
  common, a supported archive command is a design question for the maintainers, not
  something to add before there is a user who needs it.
