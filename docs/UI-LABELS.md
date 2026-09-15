# Playground status labels

This reference covers the local Codex playground and the hosted playground.
Labels describe the selected robot and your browser's run. The conversation and
shared timer can also show someone else's run.

**“Ready to queue” means you can submit a task.** It does not confirm that motors
are enabled or that execution will begin immediately. A connected SO101 can
remain in `readonly` while your run enters and leaves the queue; changing the
label does not change the robot's control mode.

## Robot status below the cameras

The first matching condition in this table wins. Offline and fault messages take
priority over your queue/run state.

| Label | When it appears |
| --- | --- |
| Checking station availability… | Initial page load or switching robots, before status arrives. |
| Station availability is currently unavailable | No station entry is available for the selected robot. |
| Robot is offline | The selected station reports disconnected. |
| Robot fault — Operator attention needed. | The station reports a fault. A specific public fault reason replaces “Robot fault” when available; notification text can vary as listed below. |
| Your run is queued — waiting for your turn. | Your run is queued and the station reports queue readiness, availability, or a disabled YAM with a fresh enabled-auto-queue flag. |
| Your run is queued — waiting for robot readiness. | Your run is queued, but none of those readiness signals is present. This does not establish whether automatic wakeup or operator action will supply readiness. |
| The robot is preparing your run. | Your run's state is `preparing`. |
| Run active — waiting for robot control. | Your run's state is `running`, but the station still reports `readonly`. |
| Your run is running. | Your run's state is `running` and the station is not `readonly`. |
| Ready to queue your next run. | The station is connected and `readonly`, and your run is neither queued, preparing, nor running. Also appears after leaving the queue or ending a run when the station remains readonly. |
| Ready for the next run. Join the queue to start. | Outside the preceding cases, the station reports `queue_ready` or `available`, or a disabled YAM has a fresh enabled-auto-queue flag. |
| Robot stopped — waiting for operator readiness | Outside the preceding cases, the station is `DISABLED` or `STOPPED`. |
| Robot `<mode>` | Fallback for another reported mode, displayed in lowercase; missing mode becomes “Robot reserved”. |

Fault notification suffixes:

| Suffix | Notification state |
| --- | --- |
| Notifying the operator… | Notification pending. |
| The operator has been notified. | Notification submitted. The fault tooltip clarifies that delivery and acknowledgement are not confirmed. |
| Could not notify the operator. Please contact the operator. | Notification failed. |
| Operator attention needed. | Notification unavailable or unknown. |

## Queue position and owner banner

| Label | When it appears |
| --- | --- |
| Queue · connecting… | Initial queue status has not arrived. |
| Queue · `<N>` waiting | Queue snapshot is available; N counts entries other than preparing/running entries. |
| Queue · unavailable | Queue snapshot is unavailable. |
| Your place in the queue / #`<N>` | Your run is queued and has a position. |
| Joining… | Your run is queued but has no position yet. |
| Up next | Your run is preparing. |
| Your turn | Your run is running. |
| Your run is preparing | Owner banner above the cameras during preparation. Detail: “The robot is getting ready. You can stop your run here.” |
| Your run is live | Owner banner during running. Detail: “Watch your robot here. The video may follow with a short delay.” |

The owner banner and position block disappear when your run ends. Watching
someone else's run does not show their owner controls in your browser.

## Run controls and submission feedback

| Label | When it appears / what it means |
| --- | --- |
| Run | Normal submit button when Settings is open or required setup is complete, unless the paid provider is selected. Required fields are still validated. Disabled while submitting, queued, preparing, or running, and when the browser session is unavailable or ended. |
| Setup keys and run | Required setup is missing and Settings is closed. Clicking opens Settings. |
| Pay $2.50 & run | Paid provider selected after setup is available. |
| Run using Codex subscription | Launch instructions on the hosted page. Hidden in the local Codex runner. |
| Leave queue | Your run is queued. Cancels the queue entry. |
| Stop run | Your own run is preparing or running and your browser has a valid control session. |
| Joining the robot queue… | The submit request is in progress. |
| You’re in the queue. Your position is highlighted above. | The submit request succeeded. |
| You left the queue. Your key is saved for your next run. | Leave queue request succeeded. This is the current shared wording, including for Codex sessions that have no model API key. |
| Stop requested. Your key is saved for your next run. | Stop request succeeded. This confirms the request, not physical parking or next-run readiness. |

Request errors and execution-blocking messages appear in the same feedback area.
They use the specific error returned by the runner, rather than a fixed label.

## Countdown

| Display | When it appears |
| --- | --- |
| —:— | No valid elapsed time is available for the shared run, or its duration is unknown. |
| MM:SS | Remaining duration, rounded up to seconds and clamped at 00:00. |
| 00:00 | The reported duration has elapsed. This alone does not confirm physical cleanup has finished. |

Both countdowns use the same shared run timing. The owner countdown is visible
beside Stop during your preparation/running states. The main countdown remains
visible for spectators and freezes when the run ends.

The page also maintains a currently hidden timer-detail label:
`Waiting for a run`, `MM:SS remaining`, `Preparing · MM:SS remaining`,
`Run in progress` (optionally prefixed by `Preparing ·`), `Run ended`, or
`Reconnecting · timer paused`. The reconnecting label applies after more than
five seconds without a fresh timing update; local countdown extrapolation then
pauses until an update arrives.

## Camera labels

SO101 and bimanual SO101 use these labels for each named camera:

| Label | When it appears |
| --- | --- |
| `<camera>` · Connecting | No frame has loaded yet, including retries before the first successful frame. |
| `<camera>` · Live | A new camera image loaded and decoded successfully. |
| `<camera>` · Reconnecting (last frame) | A refresh failed after a prior successful image. The retained image is explicitly labeled as an old frame. |

The YAM video viewer additionally uses:

| Label | When it appears |
| --- | --- |
| Loading video | A video connection is being established. |
| Live / Live · synchronized | WebRTC playback is advancing; the synchronized source uses the second label. |
| Live · delayed | HLS playback is advancing. |
| Buffering | The video element is waiting for data. |
| Reconnecting | The viewer is retrying its connection. |
| Press play | Browser autoplay was blocked. |
| Video unsupported | The browser has no supported playback path. |
| Paused | The page is in the background. |
| Session ended | The browser session has ended. |

Camera liveness is independent of robot readiness: a Live camera does not mean
the arm is enabled or that a queued run has started.

The YAM stream-health badge separately describes frames presented in the browser:

| Label | When it appears |
| --- | --- |
| Checking stream | No presented frame yet, within the first eight seconds. |
| Live video | Recent frames with no excessive measured delay, using a non-HLS stream. |
| Video playing · delayed | Recent HLS frames with estimated delay no greater than 15 seconds, or delay unavailable. |
| Stream behind live | Estimated delay exceeds two seconds for non-HLS video or 15 seconds for HLS. |
| Stream stalled · reconnecting | No first frame after eight seconds, or no new presented frame for at least three seconds. |
| Stream paused in background | The browser document is hidden. |

## Expected SO101 sequence

| Event | Robot-status label |
| --- | --- |
| Connected and readonly, no active request | Ready to queue your next run. |
| You click Run; the request is accepted but readiness is not advertised | Your run is queued — waiting for robot readiness. |
| Readiness is advertised while your request remains queued | Your run is queued — waiting for your turn. |
| Your request is assigned | The robot is preparing your run. |
| Execution starts and control is active | Your run is running. |
| You leave the queue, or the run ends and the robot returns to readonly | Ready to queue your next run. |

## Source and regression checks

- [UI behavior](../codex-runner/static/hosted.js): `render`,
  `guideRunAttention`, `buttons`, `tickStopwatch`, and `selectedCameras`.
- [Page markup and default visibility](../codex-runner/static/hosted.html).
- [Stream-health badge](../codex-runner/static/stream-health.js).
- [Robot-status message tests](../codex-runner/tests/test_station_message.cjs).
- [Queue/owner-banner tests](../codex-runner/tests/test_run_attention.cjs).
- [Countdown tests](../codex-runner/tests/test_run_timer.cjs).

Update this document alongside changes to these labels or their conditions.
