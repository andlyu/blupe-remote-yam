"""Real Claude subscription checks with synthetic images and mock robot IO only."""
import json

from .claude_policy import ClaudeAdapter
from .codex_check import SyntheticObservation, check_observation
from .controller import RunnerController
from .providers import PolicyComplete
from .session import MockSessionAPI

INSTRUCTION = ('This is a transport check with mock robot IO only. Images are synthetic color markers. '
               'In your final summary identify the color of each labeled image. ')


class CheckAdapter(SyntheticObservation, ClaudeAdapter):
    """Images are explicit transport markers, never represented as a robot scene."""


def run_check(*, simulation=False):
    """No address argument or physical transport exists in this check."""
    provider = CheckAdapter()
    if not simulation:
        try:
            provider.build_trajectory(INSTRUCTION + 'Immediately return done. Do not request any movement.',
                                      check_observation(), 0)
        except PolicyComplete:
            if provider._outcome['status'] != 'done':
                raise RuntimeError('Claude connection check did not report done')
        else:
            raise RuntimeError('Claude connection check unexpectedly requested a movement; nothing was executed')
        summary = {'passed': True, 'model': provider.model, 'model_calls': provider._calls,
                   'summary': provider._outcome['summary'], 'usage': provider._usage,
                   'physical_hardware_used': False}
    else:
        session = MockSessionAPI(observations=[check_observation()])
        runner = RunnerController(session)
        try:
            runner.update_monitor_observation(session.get_robot_observation('yam-1'))
            runner.join(provider, INSTRUCTION +
                        'First request exactly one move_to with only left_gripper=0.8. '
                        'After its completed feedback, return done. Do not move any other dimension.')
            for _ in range(100):
                runner.update_monitor_observation(session.get_robot_observation('yam-1'))
                runner.process_next_event(.1)
                if runner.status()['status'] in ('stopped', 'error', 'safety_aborted'):
                    break
            status = runner.status()
            passed = (status['status'] == 'stopped' and not status['error'] and
                      len(session.trajectory_log) == 1 and provider._calls == 2 and
                      provider._outcome is not None and provider._outcome['status'] == 'done')
            summary = {'passed': passed, 'model': provider.model, 'model_calls': provider._calls,
                       'packets_submitted': len(session.trajectory_log),
                       'status': status['status'], 'physical_hardware_used': False,
                       'simulation': 'in-process Session API mock with real MuJoCo IK',
                       'usage': provider._usage,
                       'summary': (provider._outcome or {}).get('summary')}
            if not passed:
                raise RuntimeError('Claude simulation check failed: ' + json.dumps(summary))
        finally:
            runner.disconnect('claude_check_complete')
    print(json.dumps(summary, indent=2), flush=True)
    return summary
