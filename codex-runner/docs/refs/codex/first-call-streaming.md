# First-call public summaries

Sources checked 2026-09-25 for the runner's pinned codex-cli 0.154.0:
- https://learn.chatgpt.com/docs/non-interactive-mode — `codex exec --json` emits JSONL events while running; item types include reasoning and agent messages.
- https://learn.chatgpt.com/docs/config-file/config-reference — `model_reasoning_summary` accepts auto, concise, detailed, none.

The first robot decision requests `model_reasoning_summary="auto"`; subsequent
calls request none. Low reasoning effort is unchanged. This is the existing
subscription transport, with no additional SDK or API-key billing.

`FirstCallProgress` reads stdout using `os.pread` while the child runs. Never seek
stdout while the child is writing: inherited descriptors share a file offset.
Buffer incomplete JSONL and UTF-8 bytes until a newline; drain the final partial
line on exit. Retain the 8 MB output limit and existing cancellation/timeout
process-group termination, final event validation, and schema validation.

Only `turn.started` and the public `reasoning.text` from `item.started`,
`item.updated`, and `item.completed` are projected. Raw/encrypted reasoning,
stderr, tool arguments, and unvalidated decision JSON are not displayed.
Summary updates deduplicate identical item text and cap individual text at 4000
characters and item count at 32. `model_progress` journal events contain
`progress_type` (status/summary), item_id, and the public message. The local
public projection allowlists message/category; the existing status poll delivers
updates approximately once per second. The UI shows the latest summary during
the first pending decision, then replaces it with the validated response note.
No model event sends motion; execution still waits for the complete decision.

Tests cover real subprocess partial writes and early delivery, final JSONL
integrity, suppression on resumed calls, private-field exclusion, cancellation,
timeouts, public projection, and UI replacement on the completed response.
Summary emission can be sparse or absent; this is not a token-by-token transcript.
