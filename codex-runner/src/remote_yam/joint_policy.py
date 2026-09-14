"""Joint-space policy for explicitly configured non-YAM robots."""
import json
import math
from .providers import OpenAIAdapter, PolicyComplete, _response_text
from .ik import IKCommand, ArmIKCommand

class JointPolicy(OpenAIAdapter):
    def __init__(self, *args, joint_counts, **kwargs):
        super().__init__(*args, **kwargs)
        self.joint_counts = joint_counts
        self.interaction_sink = None

    def infer(self, prompt, observation):
        robot_context = (
            'You are controlling a physical SO101 follower robot: one arm with five arm joints and one gripper. '
            'The API calls this single arm left; left does not imply a second arm exists. '
            'Send its five absolute joint angles in the left array, in this order: '
            'shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll. '
            'Control its gripper separately with left_gripper. Always send right=[] and right_gripper=null. '
            'The attached front image is the camera view of this SO101 workspace. '
            'Use the measured SO101 joint positions and that image to decide the next action. '
            if tuple(self.joint_counts) == (5,0) else ''
        )
        text = (robot_context + 'Control the selected robot using absolute joint targets in degrees. '
                f'It has {self.joint_counts[0]} left joints and {self.joint_counts[1]} right joints. '
                'There is no Cartesian IK. Inspect the camera and feedback. Use small deliberate motions, '
                'at most 3 degrees per joint per response and 0.03 gripper change. Never assume a YAM configuration. '
                'Return only JSON: {"left": [angles], "right": [angles], "left_gripper": 0.0, "right_gripper": null}. '
                'An absent arm must be []. Grippers are normalized 0..1. '
                'When finished return {"done": true}; if unable to proceed return {"error": "reason"}. Task: '+prompt)
        result=json.loads(_response_text(self._post_json({'model':self.model,'instructions':text,
            'input':self._observation_input(observation),'store':False})))
        if self.interaction_sink:
            self.interaction_sink('model_response', 'Joint decision received', response=json.dumps(result))
        if result.get('error'): raise RuntimeError('Policy could not complete the task')
        if result.get('done') is True: raise PolicyComplete('done')
        arms=[]; grips=[]
        for arm,count in zip(('left','right'),self.joint_counts):
            q=result.get(arm)
            if not isinstance(q,list) or len(q)!=count or any(type(v) not in (int,float) or not math.isfinite(v) for v in q):
                raise ValueError('Policy returned invalid joint targets')
            if any(abs(a-b)>3 for a,b in zip(q,observation[arm+'_joints_deg'])):
                raise ValueError('Policy target exceeds 3 degree motion bound')
            arms.append(ArmIKCommand('joints',tuple(q)))
            g=result.get(arm+'_gripper')
            if g is not None:
                if not count or type(g) not in (int,float) or not math.isfinite(g) or not 0<=g<=1 or abs(g-observation[arm+'_gripper'])>.03:
                    raise ValueError('Policy returned invalid gripper target')
            grips.append(g)
        return IKCommand(*arms,*grips)


class CodexJointPolicy(JointPolicy):
    provider_name = 'codex'

    def __init__(self, model='gpt-6-astra', *, joint_counts, camera_source):
        from .codex_policy import codex_status, codex_binary
        import tempfile
        setup=codex_status()
        if not setup['ready']: raise RuntimeError(setup['message'])
        self.model=model
        self.joint_counts=joint_counts
        self.interaction_sink=None
        self._initialize_observation(camera_source)
        self._binary=codex_binary()
        self._workspace=tempfile.TemporaryDirectory(prefix='blupe-joint-codex-')
        self._thread_id=None
        self._sent_items=0
        self._timeout_s=120
        self._incremental_history=False
        self._expected_camera_count=len(camera_source.camera_names)
        self._decision_instructions=('You are the joint decision component of the BluPe local runner. '
            'Return only JSON matching the schema. Do not use shell, web, files or other tools. '
            'Only the runner executes movement. Images are labeled by camera. '
            'For a move use done=false and error=""; for completion use done=true. '
            'Keep current joint values in unused completion fields.')
        fields={name:{'type':'array','items':{'type':'number'},'minItems':count,'maxItems':count}
                for name,count in zip(('left','right'),joint_counts)}
        fields.update({name:{'type':['number','null']} for name in ('left_gripper','right_gripper')})
        fields.update(done={'type':'boolean'},error={'type':'string'})
        self._decision_schema={'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}

    @staticmethod
    def _decision_response(value):
        return {'output_text':json.dumps(value,allow_nan=False)}

    def public_config(self):
        return {'provider':'codex','model':self.model,'api_key_configured':False,
                'authentication':'chatgpt','transport':'codex_exec','codex_connected':True}

    from .codex_policy import CodexAdapter
    _post_json=CodexAdapter._post_json
    _execute=CodexAdapter._execute
