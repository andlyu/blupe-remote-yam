"""Read-only JPEG evidence spanning task execution and normal API parking.

This collector owns no robot/camera hardware, lease, command or recovery move.
Samples are asynchronous and may have gaps; source and receive times are retained.
"""
import json
from pathlib import Path
import threading
import time

from .cameras import CameraFrameSource


def completed_task_outcome(native, parking, evaluation):
    """Keep the native result separate from the task outcome after parking."""
    stages_complete = (native.get('planning_success') is True
                       and native.get('physical_motion_calls', 0) > 0
                       and native.get('status') in {'SUCCESS', 'UNVERIFIED'})
    success = bool(stages_complete and (parking or {}).get('status') == 'PARKING_OBSERVED'
                   and (evaluation or {}).get('status') == 'EVALUATION_ONLY'
                   and (evaluation or {}).get('success') is True)
    return dict(success=success,
                status='SUCCESS' if success else 'FAILED' if native.get('status') == 'FAILED' else 'UNVERIFIED',
                native_status=native.get('status'), parking_status=(parking or {}).get('status'),
                postpark_evaluation_status=(evaluation or {}).get('status'),
                reason='Fresh post-parking evaluation confirms placement' if success else
                       native.get('reason') or 'Placement after arm clearance and parking is not confirmed')


class SessionReviewRecorder:
    def __init__(self, origin, urls, directory, *, interval_s=1., source=None, sink=None):
        if set(urls) != {'top', 'left', 'right'} or interval_s <= 0:
            raise ValueError('Review requires exactly three camera roles and a positive interval')
        self.urls = dict(urls)
        self.directory = Path(directory)
        self.source = source or CameraFrameSource(origin, timeout_s=2.)
        self.interval_s, self.sink = interval_s, sink
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads, self._last = [], {}
        self._started = False

    def mark(self, kind, **values):
        with self._lock:
            with (self.directory/'events.jsonl').open('a') as log:
                log.write(json.dumps(dict(kind=kind, recorded_at=time.time(), **values))+'\n')

    def start(self):
        if self._started:
            return
        self.directory.mkdir(parents=True, exist_ok=False)
        self._started = True
        self.mark('review_started', interval_s=self.interval_s,
                  limitation='Asynchronous JPEG samples, not continuous or synchronized RGB-D')
        for role in self.urls:
            thread = threading.Thread(target=self._capture, args=(role,), daemon=True,
                                      name='aspire-review-'+role)
            self._threads.append(thread)
            thread.start()

    def _capture(self, role):
        count = 0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                frame = self.source.fetch(role, self.urls[role])
                count += 1
                name = f'{role}-{count:06d}.jpg'
                (self.directory/name).write_bytes(frame.jpeg)
                row = dict(frame.summary(), image=name)
                with self._lock:
                    self._last[role] = row
                    with (self.directory/(role+'-frames.jsonl')).open('a') as log:
                        log.write(json.dumps(row)+'\n')
                if self.sink:
                    self.sink(frame)
            except Exception as exc:
                self.mark('sample_error', camera=role, error_type=type(exc).__name__)
            self._stop.wait(max(0., self.interval_s-(time.monotonic()-started)))

    def finish_after_stop(self, observe, *, timeout_s=60., tail_s=3., cancelled=lambda: False):
        """Observe parking and its tail without delaying or changing the stop."""
        if not self._started:
            return dict(status='NOT_STARTED', physical_commands=0)
        stop_at = time.time()
        self.mark('runner_stop_requested', stop_at=stop_at)
        deadline, parked_at, parked_observation = time.monotonic()+timeout_s, None, None
        after_park = False
        try:
            while time.monotonic() < deadline and not cancelled():
                try:
                    observation = observe()
                    self.mark('station_observation', observation=observation)
                    observed_at = observation.get('observed_at', 0.)
                    if (parked_at is None and observation.get('source') == 'hardware'
                            and observation.get('mode') == 'DISABLED'
                            and isinstance(observed_at, (float, int)) and observed_at >= stop_at):
                        parked_at, parked_observation = time.monotonic(), observation
                    with self._lock:
                        captures = dict(self._last)
                    cutoff = parked_observation['observed_at'] if parked_observation else None
                    after_park = (cutoff is not None and all(
                        captures.get(role, {}).get('captured_at', 0.) >= cutoff for role in self.urls))
                    if parked_at is not None and after_park and time.monotonic()-parked_at >= tail_s:
                        break
                except Exception as exc:
                    self.mark('station_read_error', error_type=type(exc).__name__)
                self._stop.wait(.5)
        finally:
            self.close()
        summary = dict(status='PARKING_OBSERVED' if parked_at is not None and after_park else 'UNVERIFIED',
                       physical_commands=0, parked_observation=parked_observation,
                       cancelled=cancelled(),
                       latest_frames=self._last, timeout_s=timeout_s,
                       limitation='Parking telemetry and timestamped sparse images do not prove task success')
        (self.directory/'review-recording.json').write_text(json.dumps(summary, indent=2)+'\n')
        return summary

    def close(self):
        if not self._started:
            return
        self._stop.set()
        for thread in self._threads:
            thread.join(self.source.timeout_s+1.)
        self.mark('review_stopped', threads_finished=all(not t.is_alive() for t in self._threads))
