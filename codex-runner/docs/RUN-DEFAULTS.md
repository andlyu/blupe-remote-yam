# YAM run defaults

The shared playground and hosted deployment use these defaults:

- **With Haste** system prompt, selected in code by
  `src/remote_yam/robocurve_prompts.py`. Original RoboCurve is preserved there
  as `original-robocurve`; no prompt selector is added to the UI.
- Cartesian target bounds use the selected station's saved controller workspace
  for `yam-1` and Robo-house (`robot-ba8413962083809c`). Both have maximum X
  0.746831991 m; Y and Z bounds differ by station and arm. See
  [controller workspace](CONTROLLER-WORKSPACE.md) for the full per-arm bounds
  and saved-profile caveats. Unknown stations and unbound direct adapters retain
  X 0.15–0.48 m, Y −0.50–+0.50 m, Z 0.03–0.40 m.
- **4× motion pacing** (`TRAJECTORY_SPEED = 4.0`). The existing 0.35 rad/s
  joint ceiling, gripper timing, reachability, gateway and settling checks
  remain. These can slow a particular path, so 4× is not a guaranteed
  end-to-end speedup. Other robot geometries retain their own motion limits.
- **Fast inference**, for OpenAI/Codex and Anthropic/Claude. Both the UI and
  omitted `response_speed` fields in `/api/run` default to Fast. Choose
  Standard explicitly to opt out. Unsupported providers remain Standard.
  Fixed-price hosted runs use the same defaults; their payment flow is unchanged.

Fast inference and reasoning effort are independent. This release preserves the
existing effort defaults and explicit per-run effort selections.

## Provider transport

- OpenAI Responses: `service_tier: priority` (Standard sends `default`).
- Codex subscription: session-only `service_tier="fast"` with Fast enabled.
- Claude API: `speed: fast` and `anthropic-beta: fast-mode-2026-02-01`.
- Claude Code: session-only `--settings` with `fastMode: true`.

Fast requires provider/account access and can consume more credits. Errors do
not automatically retry at a different speed. `response_speed` records the
request; `actual_response_speed` reports the returned API/Claude speed when
available. It remains null when the transport does not report it, including
Codex. A requested tier is not proof that the provider delivered that tier.

The audited With Haste behavioral text SHA-256 is
`724dba416a8a722f5a52dfe5f125a34e8eed5c4ebb4d7e5e74aed5a56559d559`.
The Original RoboCurve behavioral text SHA-256 is
`cc3f1dade16066840ec62f5709286468f0b217f985290748a2abc6b9403e559e`.
Known YAM stations append their active workspace to that behavioral text. Run
configuration saves the behavioral prompt version, complete effective system
text/hash, controller workspace profile, requested inference speed, and trajectory
pacing separately. Saved profiles do not verify the live controller configuration.

References checked September 28, 2026:
- https://developers.openai.com/api/docs/guides/fast-mode
- https://code.claude.com/docs/en/fast-mode
- https://platform.claude.com/docs/en/build-with-claude/fast-mode
