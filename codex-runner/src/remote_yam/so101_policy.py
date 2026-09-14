"""YAM observation/tool loop adapted to a single SO101 and its Cartesian IK."""
import copy
from .robocurve_contract import SYSTEM_PROMPT as YAM_PROMPT, TOOLS as YAM_TOOLS
from .robocurve_policy import OpenAIAdapter
from .codex_policy import CodexAdapter, DECISION_SCHEMA, decision_response
from .so101_trajectory import SO101Trajectory, NAMES, LOW, HIGH

SYSTEM_PROMPT = YAM_PROMPT.split('Embodiment notes:')[0].replace("'yam_arms'", "'so101'") + '''Embodiment notes:
One SO101 follower with five arm joints and one gripper. There is no second arm.
Use move_to to request absolute x, y, z in meters for the model's gripperframe
site near the fingertips, expressed in the fixed robot base frame; +z is up.
The camera's axes are not the robot base axes. Use current measured state and
images to establish how motion appears in the front camera; do not assume YAM
camera placement or geometry. The state reports FK from measured joint angles.
The tool's wrist_roll is an absolute wrist servo angle in radians, not world yaw.
Other tool orientation components are unconstrained: the IK solver chooses them
near the current pose. This five-joint arm cannot realize arbitrary six-axis poses.
The gripper is normalized: 0 closed, 1 open. Do not assume YAM's jaw width.
Joint state order (radians) is shoulder_pan, shoulder_lift, elbow_flex,
wrist_flex, wrist_roll, then normalized gripper. The transport labels the arm left.
Unspecified target dimensions retain their measured values. Cartesian paths are
interpolated at 10 Hz with at most 0.01 radians per joint per waypoint. IK failures
are rejected before sending any motion. Workspace bounds are not a collision
checker; avoid the table, objects, and self-contact using observations. Prefer
small moves and inspect the front image afterward. Never declare success merely
because a movement was requested; inspect the resulting observation.
'''
TOOLS = copy.deepcopy(YAM_TOOLS)
TOOLS[0]['description'] = ('Move the SO101 grasp-frame XYZ with IK; wrist_roll is its absolute wrist joint angle. '
    'Positions use meters, wrist_roll uses radians, gripper uses 0..1. Other orientations are unconstrained. '
    'Unspecified dimensions hold their measured value. Bounds: ' + ', '.join(f'{n}: [{lo}, {hi}]' for n, lo, hi in zip(NAMES, LOW, HIGH)))
TOOLS[0]['parameters']['properties']['targets'] = {
    'type': 'object', 'additionalProperties': False,
    'properties': {n: {'type': 'number', 'minimum': float(lo), 'maximum': float(hi)} for n,lo,hi in zip(NAMES, LOW,HIGH)},
    'description': 'Nonempty map of absolute SO101 target dimensions.'}

class SO101PolicyMixin:
    final_joint_tolerance_deg = 5.0
    def __init__(self, *args, calibration_path=None, **kwargs):
        self._calibration_path = calibration_path
        super().__init__(*args, **kwargs)

    def _make_geometry(self):
        return SO101Trajectory(self._calibration_path)

    def _initialize_policy(self, recording_root):
        super()._initialize_policy(recording_root)
        self._system_prompt = SYSTEM_PROMPT
        self._tools = copy.deepcopy(TOOLS)
        roll_low, roll_high = map(float, self._geometry.limits[4])
        self._tools[0]['parameters']['properties']['targets']['properties']['wrist_roll'].update(
            minimum=roll_low, maximum=roll_high)
        self._tools[0]['description'] = self._tools[0]['description'].replace(
            f'wrist_roll: [{LOW[3]}, {HIGH[3]}]', f'wrist_roll: [{roll_low}, {roll_high}]')
        self._names = NAMES
        self._camera_names = ('front',)
        self._expected_camera_count = 1
        self._decision_schema = copy.deepcopy(DECISION_SCHEMA)
        self._decision_schema['properties']['targets']['properties'] = {n: {'type': ['number', 'null']} for n in NAMES}
        self._decision_schema['properties']['targets']['required'] = list(NAMES)
        self._decision_instructions = ('You are the SO101 Cartesian decision component. Return only JSON matching the schema. '
            'The runner executes move_to/done/give_up. Use null for unchanged dimensions and empty strings for unused text. '
            'For done/give_up all targets must be null. Do not use shell, files, web, or other tools. '
            'The attached image is the current front camera observation. Wait for measured feedback before claiming arrival.')

    @staticmethod
    def _decision_response(value):
        return decision_response(value, names=NAMES)

    def public_config(self):
        return {**super().public_config(), 'policy': 'so101_cartesian', 'control_mode': 'cartesian_ik'}

class SO101OpenAIAdapter(SO101PolicyMixin, OpenAIAdapter):
    pass

class SO101CodexAdapter(SO101PolicyMixin, CodexAdapter):
    pass
