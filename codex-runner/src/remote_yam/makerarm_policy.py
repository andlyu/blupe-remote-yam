"""MakerArm's position-only Astra contract for OpenAI and local Codex."""
import copy
from .robocurve_contract import SYSTEM_PROMPT as BASE_PROMPT, TOOLS as BASE_TOOLS
from .robocurve_policy import OpenAIAdapter
from .codex_policy import CodexAdapter, DECISION_SCHEMA, decision_response
from .makerarm_trajectory import MakerArmTrajectory, NAMES

SYSTEM_PROMPT = BASE_PROMPT.split('Embodiment notes:')[0].replace("'yam_arms'", "'makerarm'") + '''Embodiment notes:
You control one MakerMods MakerArm: six arm joints and one gripper, no second arm.
move_to accepts absolute x, y, z in meters for the URDF grasp_center between the
fingers. Coordinates use the native CAD base_link frame, which is Y-up, NOT the
YAM or camera frame. Establish directions from measured FK and camera observations.
Orientation is unconstrained; nearby IK solutions may rotate the tool. There are
no roll/pitch/yaw controls. Do not attempt tasks requiring a fixed orientation.
Gripper 0 is closed and 1 is open in configured actuator travel. This is NOT a
calibrated jaw width; do not infer millimeters from it. Omitted dimensions retain
measured values. Joint state is six controller joint angles in radians followed
by normalized gripper; the transport calls this single arm left.
The runner interpolates XYZ and converts each waypoint to joint positions using
verified motor-to-model mapping, with at most 0.01 radians per joint per 100ms
waypoint. Entire trajectories are validated before transmission. Unreachable
moves return an error: choose a smaller target using fresh observations.
Workspace and joint limits do not provide collision checking. Use small moves,
avoid the table and self-contact, and inspect images before claiming success.
'''

class MakerArmPolicyMixin:
    final_joint_tolerance_deg = 1.0

    def __init__(self, *args, calibration_path=None, camera_names=('front',), **kwargs):
        self._mapping_path = calibration_path
        self._maker_cameras = tuple(camera_names)
        super().__init__(*args, **kwargs)

    def _make_geometry(self):
        return MakerArmTrajectory(self._mapping_path)

    def _initialize_policy(self, recording_root):
        super()._initialize_policy(recording_root)
        self._system_prompt = SYSTEM_PROMPT + '\nCamera roles: ' + ', '.join(self._maker_cameras) + '.\n'
        self._names = NAMES
        self._camera_names = self._maker_cameras
        self._expected_camera_count = len(self._maker_cameras)
        self._tools = copy.deepcopy(BASE_TOOLS)
        self._tools[0]['description'] = 'Move MakerArm grasp_center XYZ in native Y-up base coordinates (meters); orientation unconstrained. Gripper is normalized actuator travel.'
        self._tools[0]['parameters']['properties']['targets'] = {
            'type':'object', 'additionalProperties':False,
            'properties':{n:dict(type='number', minimum=float(lo), maximum=float(hi))
                          for n,lo,hi in zip(NAMES,self._geometry.low,self._geometry.high)}}
        self._decision_schema = copy.deepcopy(DECISION_SCHEMA)
        target = self._decision_schema['properties']['targets']
        target['properties'] = {n:{'type':['number','null']} for n in NAMES}
        target['required'] = list(NAMES)
        self._decision_instructions = ('You are the MakerMods MakerArm Cartesian decision component. Return only JSON matching the schema. '
            'Use null for unchanged dimensions, empty strings for unused text, and all-null targets for done/give_up. '
            'The runner executes move_to/done/give_up. Do not use shell, files, web, or other tools. '
            'Use the supplied camera images and measured feedback.')

    @staticmethod
    def _decision_response(value):
        return decision_response(value, names=NAMES)

    def public_config(self):
        return {**super().public_config(), 'policy':'makerarm_cartesian', 'control_mode':'cartesian_ik'}

class MakerArmOpenAIAdapter(MakerArmPolicyMixin, OpenAIAdapter):
    pass

class MakerArmCodexAdapter(MakerArmPolicyMixin, CodexAdapter):
    pass
