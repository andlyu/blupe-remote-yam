"""AGP-inspired RGB-D measurement loop using the existing validated YAM runner."""
import base64
import copy
import json
import uuid
import numpy as np

from .api_depth import ApiDepth
from .codex_policy import CodexAdapter, DECISION_SCHEMA, decision_response
from .robocurve_trajectory import RoboCurveTrajectory, InvalidMove
from .camera_calibration import rigid


class DepthTrajectory(RoboCurveTrajectory):
    """Allow either arm; hold measured joints for an arm with no requested target."""
    def build(self, targets, observation, first_step_id):
        points=super().build(targets,observation,first_step_id)
        for arm in ('left', 'right'):
            if any(k.startswith(arm+'_') and value is not None for k,value in targets.items()):
                continue
            # Bimanual IK can otherwise correct an unnamed arm's pose slightly.
            for point in points:
                point[arm+'_joints_deg']=list(observation[arm+'_joints_deg'])
                point[arm+'_gripper']=observation[arm+'_gripper']
        return points


class SingleArmTrajectory(DepthTrajectory):
    """Optional explicit arm restriction for callers that request it."""
    def __init__(self, arm, **kwargs):
        super().__init__(**kwargs)
        self.inactive = 'right' if arm == 'left' else 'left'

    def build(self, targets, observation, first_step_id):
        if isinstance(targets,dict) and any(k.startswith(self.inactive+'_') for k in targets):
            raise InvalidMove('Inactive-arm targets are forbidden in this example')
        return super().build(targets, observation, first_step_id)


class CodexDepthAdapter(CodexAdapter):
    share_conversation = True

    def __init__(self, api_origin, *args, active_arm=None, depth_url=None, rgb_origin=None,
                 all_depth_origin=None, **kwargs):
        if active_arm not in (None, 'left', 'right'):
            raise ValueError('Unknown active arm')
        self.active_arm = active_arm
        super().__init__(*args, **kwargs)
        self.depth_api = ApiDepth(api_origin,depth_url=depth_url)
        if rgb_origin:
            from .private_rgb import PrivateRgbSource
            self._camera_source = PrivateRgbSource(rgb_origin)
        self.all_depth_origin = all_depth_origin
        if all_depth_origin:
            from .api_depth_set import ApiDepthSet
            self._camera_source = ApiDepthSet(api_origin, all_depth_origin)
        self.active_arm = active_arm
        self._snapshot = None
        self._expected_camera_count = 4
        self._decision_schema = copy.deepcopy(DECISION_SCHEMA)
        self._decision_schema['properties']['action']['enum'].append('measure_depth')
        self._decision_schema['properties']['pixel'] = {
            'type': 'object', 'additionalProperties': False,
            'properties': {k: {'type': ['integer', 'null']} for k in ('u','v','radius')},
            'required': ['u','v','radius']}
        self._decision_schema['required'].append('pixel')
        self._decision_instructions = (
            'You are the YAM RGB-D example decision component. Return only the supplied JSON schema. '
            'Tools/shell/web are disabled. Four attached images: paired top RGB, left RGB, right RGB, '
            'then top aligned depth visualization. Depth preview uses 0–2 m, near white/far blue; '
            'magenta is missing and saturated blue may exceed 2 m. It is not a metric lookup. '
            'Use action=measure_depth with integer pixel u,v,radius (0–5) to query measured optical Z '
            'and surface points in both arm bases. All targets must be null for that action. '
            'Choose interior pixels in the TOP RGB image. Up to four queries per movement decision. '
            'For move_to/done/give_up pixel u,v,radius must all be null. Use null for unchanged target '
            'dimensions and empty strings for unused text fields. Choose an arm from the observed '
            'scene and reachability; no arm is prescribed. Measured surface points are not grasp '
            'targets: account for fingers, object height, lift clearance and the grasp frame. '
            'Keep provisional calibration warnings; do not claim metric accuracy. Query source and '
            'destination before manipulation, reobserve after every move, and verify retention, '
            'support, release and stable placement after withdrawal. Never infer contact from '
            'joint angles. If depth is missing/noisy, do not invent a point. The runner executes '
            'and validates actions. Never claim motion occurred without measured completion feedback.')
        if active_arm is not None:
            self._decision_instructions = self._decision_instructions.replace(
                'and surface points in both arm bases.', 'and a surface point in the acting arm base.').replace(
                'Choose an arm from the observed scene and reachability; no arm is prescribed. ', '')
            self._decision_instructions += (' The caller explicitly restricted motion to the '
                + active_arm + ' arm; all other-arm targets must be null. Measurements use that arm base.')
        if all_depth_origin:
            self._expected_camera_count = 6
            self._decision_schema['properties']['pixel']['properties']['camera'] = {
                'type': ['string', 'null'], 'enum': ['top', 'left', 'right', None]}
            self._decision_schema['properties']['pixel']['required'].append('camera')
            self._decision_instructions = self._decision_instructions.replace(
                'Four attached images: paired top RGB, left RGB, right RGB, then top aligned depth visualization.',
                'Six attached images: paired top RGB, left RGB, right RGB, then aligned top, left, right depth previews.')
            self._decision_instructions = self._decision_instructions.replace(
                'Choose interior pixels in the TOP RGB image.',
                'Set pixel.camera to top, left or right and choose an interior pixel in that camera RGB. '
                'For motion/terminal decisions camera must be null. Wrist measurements use the current '
                'measured settled arm pose and mounting calibration. Cross-camera captures are not synchronized.')

    def _make_geometry(self):
        spacing = getattr(self, '_depth_base_spacing', None)
        return (DepthTrajectory(base_spacing_m=spacing) if self.active_arm is None
                else SingleArmTrajectory(self.active_arm, base_spacing_m=spacing))

    def set_depth_calibration(self, report):
        """Bind geometry before admission; native hardware already applies offsets."""
        if self._calls or self._history:
            raise ValueError('Bind depth calibration before starting the model run')
        spacing = report.get('base_geometry', {}).get('spacing_m')
        self._depth_base_spacing = spacing
        self._geometry = self._make_geometry()

    def public_config(self):
        return {**super().public_config(), 'policy':'astra_api_depth_example',
                'depth_camera':(['top','left','right'] if self.all_depth_origin else 'top'),
                'active_arm':self.active_arm,
                'allowed_arms':(['left','right'] if self.active_arm is None else [self.active_arm]),
                'api_calibration_id':self.depth_api.calibration_id,
                'depth_queries_per_decision':4}

    def _observation_message(self, prompt, observation, first_step_id):
        if getattr(self, 'all_depth_origin', None):
            return self._all_depth_observation(prompt, observation, first_step_id)
        self._expected_camera_count = 4
        if observation.get('robot_id', observation.get('jetson_id')) not in (None, 'yam-1'):
            raise ValueError('Depth example is only configured for YAM (yam-1)')
        from .private_rgb import PrivateRgbSource
        private_rgb = isinstance(self._camera_source, PrivateRgbSource)
        if private_rgb:
            # Preflight pins the API report before any queue preparation. Direct
            # adapter users also obtain a report before their first wrist read.
            if self._camera_source.calibration is None:
                self._camera_source.set_calibration(self.depth_api.capture(cancelled=self.cancelled).calibration)
            self._camera_names = ('left', 'right')
        try:
            message = super()._observation_message(prompt, observation, first_step_id)
        finally:
            self._camera_names = ('top', 'left', 'right')
        snapshot = self.depth_api.capture(cancelled=self.cancelled)
        self._snapshot = snapshot
        content = message['content']
        # Replace the independent top JPEG with RGB from the exact depth frameset.
        top_rgb = {'type':'input_image','detail':'high',
                   'image_url':'data:image/png;base64,'+base64.b64encode(snapshot.image()).decode()}
        if private_rgb:
            content[1:1] = [{'type':'input_text','text':f"camera 'top_cam' (step {first_step_id}):"}, top_rgb]
        else:
            rgb_index = next(i+1 for i,p in enumerate(content) if "camera 'top_cam'" in p.get('text',''))
            content[rgb_index] = top_rgb
        self._vision_frames = [f for f in self._vision_frames if f['camera'] != 'top']
        self._vision_frames.insert(0, dict(camera='top',captured_at=snapshot.metadata['captured_at'],
                                        paired_with_depth=True,calibration_id=snapshot.metadata['calibration_id']))
        content.extend([
            {'type':'input_text','text':'API RGB-D capture metadata: '+json.dumps(snapshot.metadata,allow_nan=False)
             +'\nAPI calibration (raw RGB, OpenCV Brown-Conrady; arm-specific optical-to-base transforms): '
             +json.dumps(snapshot.calibration,allow_nan=False)
             +'\nThe following top depth preview is aligned pixel-for-pixel to the first RGB image. '
             'Request measure_depth for exact metric samples. Wrist images are independently captured RGB; '
             'T_grasp_camera is a mounting transform, not a current base pose. Only top depth is available.'},
            {'type':'input_image','detail':'high',
             'image_url':'data:image/png;base64,'+base64.b64encode(snapshot.image(depth=True)).decode()}])
        if self._recorder:
            path = self._recorder.path / f'depth-{self._calls:03d}.npz'
            path.write_bytes(snapshot.bundle)
            path.chmod(0o600)
        return message

    def _all_depth_observation(self, prompt, observation, first_step_id):
        self._expected_camera_count = 6
        message = super()._observation_message(prompt, observation, first_step_id)
        snapshots = self._camera_source.snapshots
        spacing = snapshots['top'].calibration.get('base_geometry', {}).get('spacing_m')
        if spacing is not None and self._geometry.ik.base_spacing_m != spacing:
            raise ValueError('Depth calibration base spacing differs from runner geometry')
        self._snapshots = snapshots
        self._snapshot = snapshots['top']
        self.depth_api.calibration_id = self._snapshot.metadata['calibration_id']
        # The base adapter labels CameraFrame bytes JPEG; this source supplies
        # original RGB PNGs, preserving the exact paired pixels and their hashes.
        for part in message['content']:
            if part['type'] == 'input_image':
                part['image_url'] = part['image_url'].replace('data:image/jpeg;', 'data:image/png;', 1)
        poses = self._geometry.ik._poses()
        self._depth_transforms = {}
        for index, role in enumerate(('left', 'right')):
            position, rotation = poses[index]
            grasp = np.eye(4)
            grasp[:3,:3] = rotation
            self._depth_transforms[role] = {}
            for base_index, arm in enumerate(('left', 'right')):
                grasp[:3,3] = position - self._geometry.bases[base_index]
                self._depth_transforms[role][arm] = grasp @ rigid(
                    snapshots[role].calibration['cameras'][role]['T_grasp_camera'])
        context = dict(calibration=self._snapshot.calibration,
                       captures={role:snapshot.metadata for role,snapshot in snapshots.items()},
                       wrist_T_base_camera={role:{arm:pose.tolist() for arm,pose in transforms.items()}
                                            for role,transforms in self._depth_transforms.items()},
                       allowed_arms=(['left','right'] if self.active_arm is None else [self.active_arm]),
                       pose_source='FK of current measured settled joints; captures not hardware synchronized',
                       warning='Use depth_intrinsics for aligned depth; K/distortion are separately '
                               'identified board-fitted RGB parameters. Retain provisional calibration '
                               'errors; reobserve after every motion.')
        message['content'].append({'type':'input_text', 'text':'All-camera API RGB-D geometry: '
                                  + json.dumps(context,allow_nan=False)})
        for role, snapshot in snapshots.items():
            message['content'].extend([
                {'type':'input_text','text':role+' aligned depth preview (same pixels as '+role+' RGB).'},
                {'type':'input_image','detail':'high','image_url':'data:image/png;base64,'
                 +base64.b64encode(snapshot.image(depth=True)).decode()}])
            if self._recorder:
                path = self._recorder.path / f'depth-{role}-{self._calls:03d}.npz'
                path.write_bytes(snapshot.bundle)
                path.chmod(0o600)
        self._camera_source.require_fresh(snapshots)
        return message

    def _decision_response(self, value):
        if not isinstance(value,dict) or set(value) != set(self._decision_schema['required']):
            raise RuntimeError('Invalid depth decision; no motion sent')
        value = copy.deepcopy(value)
        pixel = value.pop('pixel')
        fields = {'u','v','radius'} | ({'camera'} if self.all_depth_origin else set())
        if not isinstance(pixel,dict) or set(pixel) != fields:
            raise RuntimeError('Invalid pixel query')
        if value['action'] == 'measure_depth':
            # Validate the ordinary fields with the existing terminal validator.
            check = dict(value, action='give_up')
            decision_response(check)
            if any(type(pixel[k]) is not int for k in ('u','v','radius')):
                raise RuntimeError('Depth query requires integer pixels and radius')
            if self.all_depth_origin and pixel['camera'] not in ('top','left','right'):
                raise RuntimeError('Depth query requires a configured camera role')
            return {'output':[dict(type='function_call',call_id='depth_'+uuid.uuid4().hex,
                                   name='measure_depth',arguments=json.dumps(pixel))]}
        if any(x is not None for x in pixel.values()):
            raise RuntimeError('Motion/terminal decision must have null pixel fields')
        response = decision_response(value)
        if value['action'] == 'move_to' and self.active_arm is not None:
            other = 'right_' if self.active_arm == 'left' else 'left_'
            if any(v is not None for k,v in value['targets'].items() if k.startswith(other)):
                raise RuntimeError('Other-arm targets are forbidden in this example; no motion sent')
        return response

    def _post_json(self, payload):
        for count in range(5):
            if self.cancelled():
                raise RuntimeError('Depth decision cancelled')
            raw = super()._post_json(payload)
            call = raw['output'][0]
            if call['name'] != 'measure_depth':
                return raw
            if count == 4:
                raise RuntimeError('Depth query budget exceeded; no motion sent')
            try:
                query = json.loads(call['arguments'])
                query = dict(query)
                if self.all_depth_origin:
                    role = query.pop('camera')
                    snapshot = self._snapshots[role]
                else:
                    role, snapshot = 'top', self._snapshot
                measurements = {}
                for arm in (('left','right') if self.active_arm is None else (self.active_arm,)):
                    pose = self._depth_transforms[role][arm] if role != 'top' else None
                    measurements[arm] = snapshot.measure(**query,arm=arm,T_base_camera=pose)
                result = dict(ok=True, **next(iter(measurements.values())))
                if self.active_arm is None:
                    result['points_base_m'] = {arm:value['point_base_m'] for arm,value in measurements.items()}
                    del result['point_base_m'], result['arm']
            except ValueError as exc:
                result = dict(ok=False,error=str(exc))
            items = raw['output'] + [dict(type='function_call_output',call_id=call['call_id'],
                                         output=json.dumps(result,allow_nan=False))]
            self._history.extend(copy.deepcopy(items))
            payload['input'].extend(copy.deepcopy(items))
            self._record('depth_query',query=json.loads(call['arguments']),result=result)
            self._interaction('depth_query','Measured depth at requested camera pixel',query=json.loads(call['arguments']),result=result)
            # Resume has no new image items; the same paired capture is still in context.
            self._expected_camera_count = 0
        raise AssertionError('unreachable')

    def build_trajectory(self, *args, **kwargs):
        try:
            self._expected_camera_count = 6 if self.all_depth_origin else 4
            return super().build_trajectory(*args, **kwargs)
        finally:
            self._expected_camera_count = 6 if self.all_depth_origin else 4
