"""Shared synchronous program-to-RunnerController packet handoff.

Programs never receive a session capability. RunnerController owns admission,
dispatch, correlated progress/completion, and cancellation through the API.
"""
from copy import deepcopy
from queue import Empty, Queue
import time


class ProgramPacketBridge:
    def _init_packet_bridge(self):
        self._packets = Queue(maxsize=1)
        self._replies = Queue(maxsize=1)
        self._thread = None
        self._completed = None
        self._failure = None
        self._pending = None

    def _check_cancelled(self):
        if self._failure:
            raise RuntimeError(self._failure)
        if self.cancelled():
            raise RuntimeError('Code program cancelled')

    def _execute(self, points):
        self._check_cancelled()
        if not self._packets.empty() or self._pending is not None:
            raise RuntimeError('Only one code-program packet may be outstanding')
        self._packets.put_nowait(deepcopy(points))
        while True:
            self._check_cancelled()
            try:
                measured = self._replies.get(timeout=.05)
                self._check_cancelled()
                if isinstance(measured, BaseException):
                    raise measured
                return measured
            except Empty:
                pass

    def _wait(self, seconds):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            self._check_cancelled()
            time.sleep(min(.05, end-time.monotonic()))

    def trajectory_completed(self, observation, waypoint_count):
        pending = self._pending
        if (pending is None or pending['waypoint_count'] != waypoint_count or
                observation.get('step_id') != pending['last_step_id']+1 or
                observation.get('settled') is not True):
            raise RuntimeError('Completion does not match the code-program packet')
        self._pending = None
        self._completed = deepcopy(observation)
        # The worker resumes only after build_trajectory validates the next
        # observation and RunnerController has installed its refresh callback.
