# Gemini 4 Argon: native history and local calling modes

Provider: Gemini API v1beta REST, `gemini-4-argon`; no SDK dependency.
References fetched October 13, 2026 (America/Los_Angeles). Source URL, API
version and fetch time are recorded in each file. Google docs are CC BY 4.0;
examples are Apache 2.0.

The saved pages include Google DevSite scripts. Public API keys embedded in those
scripts have been replaced with `REDACTED_GOOGLE_DEVSITE_API_KEY` to avoid secret
scanner alerts. Apply the same redaction when refreshing these snapshots; preserve
the reference text and source metadata.

| Use case | Authoritative reference |
| --- | --- |
| Stateless storage and system instructions | `interactions.txt`: https://ai.google.dev/gemini-api/docs/interactions |
| Exact raw native steps and matching tool results | `function-calling.txt`, stateless REST example: https://ai.google.dev/gemini-api/docs/function-calling |
| Native thought signatures; output/thinking limits | `thinking.html`: https://ai.google.dev/gemini-api/docs/thinking |
| Explicit image resolution and image token budget | `media-resolution.html`: https://ai.google.dev/gemini-api/docs/media-resolution |
| Counting interface and measured response usage | `tokens.html`: https://ai.google.dev/gemini-api/docs/tokens |
| Images and model capabilities | `image-understanding.txt`, `model.txt` |

## Compatibility finding

The thinking guide's Stateless mode section requires all thought blocks to be
resent exactly and says they should not be removed. The function-calling example
replays initial input, all returned model steps and matching results. The docs
do **not** promise that noncontiguous complete exchanges can be dropped. Thus
Sparse calling is experimental, despite the API accepting the probe below.
Normal calling preserves the documented full-history behavior. No checkpoint or
summary is substituted for the user's exact pattern.

The live, no-motion probe in [check_gemini_sparse_history.py](../../../tools/check_gemini_sparse_history.py) sent twelve calls
on October 13. All twelve were accepted by `gemini-4-argon`, using medium thinking,
real native thought signatures, synthetic top/left/right image inputs, and a
single `acknowledge` function that cannot move a robot. It exercised age-four
omission, age-five reentry, age-ten retention, and dropping beyond age ten.
`sparse-probe.json` contains sanitized usage/timing/selection receipts. Twelve
calls took 138.369 seconds; the final request had 23,558 reported input tokens.
This verifies protocol acceptance for those inputs, not guaranteed provider
support, preservation of reasoning quality, or physical task success. No real
camera image, robot API, or robot action was used by the probe.

## Implementation contract

- Sparse calling is the requested default. Completed call ages `{1,2,3,5,7,10}`
  are selected relative to the newest completed call, then sent chronologically.
  Ages 4, 6, 8, 9 and everything beyond 10 are omitted. Recompute from the full
  transcript each time so omitted calls can reenter at later ages.
- Each retained call includes its complete observation (all cameras), exact
  native thought/model/tool output and matching tool results. System/safety,
  original task, all bounded owner corrections and fresh observation are retained
  independently. Pending or unmatched tools prevent inference rather than being
  dropped. No inference happens before pending motion is acknowledged.
- Full per-run history, requests, outputs, feedback and image recordings remain
  intact. `gemini_request` records the actual selected wire input and call numbers;
  `gemini-config.json` records the selected mode. New runs use fresh adapter state.
- Sparse input uses a **65,536-token conservative estimate**, not an exact Google
  token count: one unit per UTF-8 serialized JSON byte excluding image data,
  1,120 units per explicit `resolution: high` image, plus 1,024 framing units.
  Google's Contents-based countTokens interface does not document native opaque
  Interactions steps, so we do not change their representation to count them.
  The image figure follows Google's Gemini 4 table; text-byte sizing deliberately
  budgets more than ordinary prose tokenization. Opaque native state and provider
  framing mean this must not be described as an exact billed-token upper bound.
- Six retained calls plus the new observation means at most seven observation
  sets. The client also enforces an 8 MiB serialized-request limit. If the exact
  selection exceeds either client budget it fails before sending, retaining the
  original transcript. It does not silently trim extra calls or user instructions.
- Normal calling sends full accumulated history and does not apply the sparse
  client budget. Both modes retain the existing 32,768 output-token cap (including
  thinking), medium thinking by default, fixed endpoint, no redirects and no
  automatic retries. Incomplete output never becomes a robot motion.

Gotcha: a fresh worktree's pinned runner need not have newer uncommitted launcher
APIs from another checkout. Port Gemini files narrowly and test the CLI, not just
adapter construction. Local preview cookies also need distinct names because
browser cookies are shared across localhost ports.
