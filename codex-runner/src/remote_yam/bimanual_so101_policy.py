"""Existing observation/tool loop adapted to two SO101 followers."""
import copy
from .robocurve_contract import SYSTEM_PROMPT as YAM_PROMPT, TOOLS as YAM_TOOLS
from .robocurve_policy import OpenAIAdapter
from .codex_policy import CodexAdapter, DECISION_SCHEMA, decision_response
from .claude_policy import ClaudeAdapter
from .anthropic_policy import AnthropicAdapter
from .bimanual_so101_trajectory import BimanualSO101Trajectory, NAMES, SIDES, ARM_NAMES

SYSTEM_PROMPT = YAM_PROMPT.split('Embodiment notes:')[0].replace("'yam_arms'", "'bimanual_so101'") + '''Embodiment notes:
Two SO101 follower arms, left and right, each with five arm joints and one gripper.
Use left_x, left_y, left_z and right_x, right_y, right_z for absolute grasp-frame
positions in meters. Each uses its OWN fixed robot base frame, with +z up.
The bases need not be parallel or share an origin. Identical coordinates do not
mean the same point in space. Camera axes are not robot base axes. Infer the
relationship from images and small observed movements; do not assume YAM geometry.
left_wrist_roll and right_wrist_roll are absolute wrist servo angles in radians,
not world yaw. Other orientation components are unconstrained; IK keeps them near
the measured pose. These five-joint arms cannot realize arbitrary six-axis poses.
left_gripper and right_gripper are independent normalized openings: 0 closed, 1 open.
No YAM jaw width applies. State joints are ordered left shoulder_pan, shoulder_lift,
elbow_flex, wrist_flex, wrist_roll, gripper, then the same for right. Angles are radians;
grippers are normalized. Cartesian state comes from FK of measured joint positions.
The left and right cameras are wrist views attached to their respective arms.
The overhead view shows the scene. Use their labels, not image order, to
identify each view. When supplied, the side view shows the scene from the side.
Unspecified arms hold their measured joints; unspecified target
dimensions on a requested arm retain measured values. Both IK paths are checked
before any motion is sent, then dispatched together at 10 Hz, at most 0.01 radians
per joint per waypoint. The shorter path holds its endpoint while the other finishes.
There is NO inter-arm or table collision checker. Keep clearance between arms and
objects, prefer moving one arm at a time, and inspect images after each movement.
Never report success just because a target was requested; check the new observation.
'''


class BimanualSO101PolicyMixin:
    final_joint_tolerance_deg = 5.0

    @staticmethod
    def model_camera_names(names):
        return tuple(names)

    def __init__(self, *args, calibration_path=None, joint_profile=None, camera_names=('overhead','side','left','right'), **kwargs):
        self._calibration_path = calibration_path
        self._joint_profile = joint_profile
        self._bimanual_cameras = self.model_camera_names(camera_names)
        if not self._bimanual_cameras or len(set(self._bimanual_cameras)) != len(self._bimanual_cameras):
            raise ValueError('Provide distinct camera roles')
        # Keep the shared viewer/recorder camera source unchanged.
        if kwargs.get('camera_source') is not None:
            kwargs['camera_source'] = copy.copy(kwargs['camera_source'])
            kwargs['camera_source'].camera_names = self._bimanual_cameras
        super().__init__(*args, **kwargs)

    def _make_geometry(self):
        return BimanualSO101Trajectory(self._calibration_path, joint_profile=self._joint_profile)

    def _initialize_policy(self, recording_root):
        super()._initialize_policy(recording_root)
        self._system_prompt = SYSTEM_PROMPT
        if "overhead" not in self._bimanual_cameras:
            self._system_prompt = self._system_prompt.replace(
                "The overhead view shows the scene. Use their labels, not image order, to",
                "No overhead view is supplied. Use the camera labels, not image order, to")
        self._tools = copy.deepcopy(YAM_TOOLS)
        properties = {f'{side}_{name}': {'type':'number', 'minimum':float(lo), 'maximum':float(hi)}
            for side in SIDES for name,lo,hi in zip(ARM_NAMES,self._geometry.arms[side].low,self._geometry.arms[side].high)}
        self._tools[0]['description'] = ('Move either or both SO101 grasp frames using IK. XYZ is in meters in each arm’s own base frame; '
            'wrist_roll is that wrist servo angle in radians, gripper is 0..1. Other orientations are unconstrained. '
            'Unspecified arms hold measured joints. Both paths are validated before dispatch; no inter-arm collision checking.')
        self._tools[0]['parameters']['properties']['targets'] = {'type':'object', 'additionalProperties':False,
            'properties':properties, 'description':'Nonempty map of left_ and/or right_ absolute targets.'}
        self._names = NAMES
        self._camera_names = self._bimanual_cameras
        self._expected_camera_count = len(self._camera_names)
        self._decision_schema = copy.deepcopy(DECISION_SCHEMA)
        self._decision_schema['properties']['targets']['properties'] = {n:{'type':['number','null']} for n in NAMES}
        self._decision_schema['properties']['targets']['required'] = list(NAMES)
        self._decision_instructions = ('You are the bimanual SO101 Cartesian decision component. Return only schema-matching JSON. '
            'Use null for unchanged targets; for done/give_up all targets must be null. Use empty strings for unused text. '
            'The runner executes move_to/done/give_up. Do not use shell, files, web, or other tools. '
            'Observe the labeled camera images and measured feedback before claiming success.')

    @staticmethod
    def _decision_response(value):
        return decision_response(value, names=NAMES)

    def public_config(self):
        return {**super().public_config(), 'policy':'bimanual_so101_cartesian', 'control_mode':'cartesian_ik'}


class BimanualSO101OpenAIAdapter(BimanualSO101PolicyMixin, OpenAIAdapter):
    pass


class BimanualSO101CodexAdapter(BimanualSO101PolicyMixin, CodexAdapter):
    pass


class BimanualSO101ClaudeAdapter(BimanualSO101PolicyMixin, ClaudeAdapter):
    pass


class BimanualSO101AnthropicAdapter(BimanualSO101PolicyMixin, AnthropicAdapter):
    pass
