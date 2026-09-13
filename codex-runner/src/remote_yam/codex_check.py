"""Real Codex subscription checks with synthetic images and mock robot IO only."""
import base64
import json
import math
import struct
import time
import zlib

from .codex_policy import CodexAdapter
from .controller import RunnerController
from .providers import PolicyComplete
from .robocurve_trajectory import NAMES
from .session import MockSessionAPI


def color_png(rgb):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    pixels = (b'\0' + bytes(rgb) * 64) * 64
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 64, 64, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def check_observation():
    return {'source': 'simulation', 'episode_id': 'ep_codex_check', 'lease_id': 'lease_check',
            'step_id': 0, 'settled': True, 'left_gripper': .9, 'right_gripper': .9,
            'left_joints_deg': [math.degrees(v) for v in [-.0311, .794, .6167, -.3748, -.0364, -.0246]],
            'right_joints_deg': [math.degrees(v) for v in [-.0227, .7929, .6193, -.3794, -.033, -.028]]}


class CheckAdapter(CodexAdapter):
    """Images are explicit transport markers, never represented as a robot scene."""
    def _observation_message(self, prompt, observation, first_step_id):
        _, state, _ = self._geometry.observe(observation)
        content = [{'type': 'input_text', 'text': prompt + '\nMeasured mock state: ' +
                    json.dumps(dict(zip(NAMES, map(float, state))))}]
        self._vision_frames = []
        for name, rgb in [('top', (255, 0, 0)), ('left', (0, 255, 0)), ('right', (0, 0, 255))]:
            content += [{'type': 'input_text', 'text': name + ': synthetic color marker, not a robot camera'},
                        {'type': 'input_image', 'image_url': 'data:image/png;base64,' +
                         base64.b64encode(color_png(rgb)).decode()}]
            self._vision_frames.append({'camera': name, 'synthetic': True})
        return {'role': 'user', 'content': content}


def run_check(*, simulation=False):
    """No address argument or physical transport exists in this check."""
    provider = CheckAdapter()
    instruction = (
        'This is a transport check with mock robot IO only. Images are synthetic color markers. '
        'In your final summary identify the color of each labeled image. '
    )
    if not simulation:
        try:
            provider.build_trajectory(instruction + 'Immediately return done. Do not request any movement.', check_observation(), 0)
        except PolicyComplete:
            if provider._outcome['status'] != 'done':
                raise RuntimeError('Codex connection check did not report done')
        else:
            raise RuntimeError('Codex connection check unexpectedly requested a movement; nothing was executed')
        summary = {'passed': True, 'model_calls': provider._calls,
                   'summary': provider._outcome['summary'], 'physical_hardware_used': False}
    else:
        session = MockSessionAPI(observations=[check_observation()])
        runner = RunnerController(session)
        try:
            runner.update_monitor_observation(session.get_robot_observation('yam-1'))
            runner.join(provider, instruction +
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
            summary = {'passed': passed, 'model_calls': provider._calls,
                       'packets_submitted': len(session.trajectory_log),
                       'status': status['status'], 'physical_hardware_used': False,
                       'simulation': 'in-process Session API mock with real MuJoCo IK',
                       'summary': (provider._outcome or {}).get('summary')}
            if not passed:
                raise RuntimeError('Codex simulation check failed: ' + json.dumps(summary))
        finally:
            runner.disconnect('codex_check_complete')
    print(json.dumps(summary, indent=2), flush=True)
    return summary
