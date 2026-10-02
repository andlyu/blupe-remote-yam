"""Read-only depth admission checks, before queueing can initialize hardware."""
import math
import time

from .api_depth import NoDepthImage, depth_robot_stopped


def preflight_stopped(observation):
    observed = observation.get('observed_at')
    return (observation.get('source') == 'hardware'
            and observation.get('jetson_id', observation.get('robot_id')) == 'yam-1'
            and type(observed) in (int, float) and math.isfinite(observed)
            and -2 <= time.time() - observed <= 2
            and observation.get('mode') in ('DISABLED', 'STOPPED', 'READY', 'API_ACTIVE')
            and (observation.get('mode') == 'DISABLED' or depth_robot_stopped(observation)))


def capture_before_queue(capture, read_observation, *, cancelled=lambda: False,
                         recovery_s=15.0):
    """Retry absent depth with fresh stop evidence; never queue or command motion.

    DISABLED is the gateway's torque-disabled state, whose settled flag is false
    before initialization. It permits the five-second data check only here.
    Active policy observations still require explicitly settled feedback.
    """
    deadline = time.monotonic() + recovery_s
    def finished():
        return cancelled() or time.monotonic() >= deadline
    while not finished():
        try:
            return capture(stopped=preflight_stopped(read_observation()), cancelled=finished)
        except NoDepthImage:
            time.sleep(.1)
        except RuntimeError:
            if not finished():
                raise
    if cancelled():
        raise RuntimeError('Depth capture cancelled')
    raise NoDepthImage()
