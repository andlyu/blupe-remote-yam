import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from remote_yam.aspire_codex_policy import AspireCodexPolicy, validate_program
from remote_yam.aspire_executable_skills import ExecutableSkillLibrary, pick_place_inputs


ROOT = Path(os.environ.get('YAM_ASPIRE_TEST_STATION_ROOT', Path(__file__).resolve().parents[4]))
MANIFEST = ROOT/'integrations/aspire/skills/pick_place.json'


class RequestSelectionTests(unittest.TestCase):
    def test_countermands_and_unsupported_conditions_are_not_discarded(self):
        for text in ('Move the red block onto the green towel. Do not execute.',
                'Move the red block onto the green towel. Plan only.',
                'Move the red block onto the green towel without moving the robot.',
                'Move the red block onto the green towel. Instead use the blue chip.',
                'Move the red block onto the green towel with the left arm.',
                'Move two red blocks onto the green towel.',
                'Move the red block from the blue chip onto the green towel.'):
            with self.subTest(text=text):
                self.assertIsNone(pick_place_inputs(text))

    def test_supported_variants_bind_actual_descriptions(self):
        for source in ('red', 'green', 'black'):
            for target in ('red', 'green', 'blue'):
                value = pick_place_inputs(f'Pick up the {source} block and place it on the {target} poker chip.')
                self.assertEqual(value['queries']['block'], source+' rectangular block')
                self.assertEqual(value['queries']['chip'], target+' round poker chip')
                self.assertEqual(value['target_kind'], 'chip')
        self.assertEqual(pick_place_inputs('Move the red block onto a clear patch of the green towel.')['target_kind'], 'fabric')


@unittest.skipUnless(MANIFEST.exists(), 'Station executable is external to shared runner')
class ExecutableReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = ExecutableSkillLibrary(MANIFEST)

    def policy(self, task, outcomes, generator):
        self.calls = []
        def harness(**request):
            self.calls.append(request)
            if request['mode'] == 'observe':
                return dict(status='OBSERVED', context={'fixture': True}, images={}, snapshot='fresh-fixture')
            validate_program(json.loads((Path(request['program']).parent/'coding-response.json').read_text()))
            return outcomes.pop(0)
        return AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture', task=task,
            directory=self.root/(hashlib.sha256(task.encode()).hexdigest()[:10]),
            instructions='Station contract', skill_directory=self.root/'saved',
            plan_only=True, generator=generator, executable_skills=self.library)

    def test_color_variants_use_one_core_and_never_request_code_generation(self):
        def unexpected(**request):
            self.fail('A compatible variant requested code generation')
        for task in ('Move the red block onto the blue chip.',
                'Move the green block onto the red chip.',
                'Move the black block onto the green chip.',
                'Move the red block onto a clear patch of the green towel.'):
            with self.subTest(task=task):
                policy = self.policy(task, [dict(planning_success=True, status='PLAN_ONLY')], unexpected)
                self.assertTrue(policy._coding_loop()['planning_success'])
                self.assertEqual([c['mode'] for c in self.calls], ['observe', 'plan'])
                record = json.loads((policy.directory/'executable-reuse.json').read_text())
                self.assertFalse(record['code_generation_requested'])
                self.assertEqual(record['core_source_sha256'], self.library.digest)
                self.assertEqual(Path(self.calls[-1]['program']).read_text().split('\n\n', 1)[1], self.library.source)

    def test_native_failure_allows_repair_with_exact_executable_source(self):
        requests = []
        def repair(**request):
            requests.append(request)
            return dict(action='program', source='def build_task(tools): return {}\ndef evaluate(tools, task): return {}\n',
                summary='Fixture repair', lesson='Actual native failure', queries=[dict(name='block', query='red block')])
        policy = self.policy('Move the red block onto the green towel.',
            [dict(status='PLAN_FAILED', planning_success=False, failing_stage='carry', reason='Actual IK failure'),
             dict(status='PLAN_ONLY', planning_success=True)], repair)
        self.assertTrue(policy._coding_loop()['planning_success'])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['feedback']['failing_stage'], 'carry')
        self.assertIn(self.library.source, requests[0]['feedback']['previous_source'])
        self.assertEqual([c['mode'] for c in self.calls], ['observe', 'plan', 'plan'])

    def test_unsupported_constraint_reaches_generator_in_full(self):
        task = 'Move the red block onto the green towel. Do not execute.'
        seen = []
        def blocked(**request):
            seen.append(request['prompt'])
            return dict(action='give_up', source='', summary='No physical execution requested', lesson='', queries=[])
        policy = self.policy(task, [], blocked)
        self.assertEqual(policy._coding_loop()['status'], 'BLOCKED')
        self.assertIn(task, seen[0])
        self.assertEqual([c['mode'] for c in self.calls], ['observe'])
        self.assertFalse((policy.directory/'executable-reuse.json').exists())

    def test_hash_mismatch_rejects_an_unreviewed_source_change(self):
        manifest = json.loads(MANIFEST.read_text())
        (self.root/'pick_place.py').write_text(self.library.source+'\n# unexpected edit\n')
        (self.root/'pick_place.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'recorded hash'):
            ExecutableSkillLibrary(self.root/'pick_place.json')

    def test_local_context_is_labelled_and_follows_current_image(self):
        import numpy as np
        selected = self.library.select('Move the red block onto the red chip.')
        if not selected['provenance']['inputs'].get('local_context_objects'):
            self.skipTest('Station does not configure a local context supplement')
        module = {}; exec(selected['response']['source'], module)
        for column in (10, 45):
            image = np.zeros((100, 100, 3), np.uint8)
            image[15:85, column:column+8] = [50, 160, 60]
            image[10:30, 75:95] = [50, 160, 60]  # Compact green block.
            evidence = []
            masks = module['local_context_masks']({'rgb':image}, evidence)
            rod = masks['occupied_local_green_rod']
            self.assertEqual(int(rod.sum()), 70*8)
            self.assertEqual(np.where(rod)[1].min(), column)
            self.assertFalse(rod[10:30, 75:95].any())
            self.assertTrue(evidence[0]['not_a_SAM3_detection'])

    def test_chip_search_preserves_support_when_an_occupant_covers_every_pose(self):
        import numpy as np
        from scipy.spatial.transform import Rotation
        selected = self.library.select('Move the red block onto the red chip.')
        module = {}; exec(selected['response']['source'], module)
        module['log'] = lambda *args:None
        geometry = {'target_xy':np.array([.3, 0.]), 'axes':np.eye(2),
            'dimensions':np.array([.04, .06]), 'chip_radius':.02,
            'chip_plane':np.array([0., 0., .01]), 'height':.03, 'top':.04,
            'obstacle_top_z':.03, 'targets':[{'xy':np.array([.3, 0.]), 'z':.01}],
            'occupied_objects':[{'name':'blocking_context', 'z_range_m':[.005, .03],
                'footprint_xy':np.array([[.2,-.1], [.4,-.1], [.4,.1], [.2,.1]])}]}
        def unexpected(*args):
            self.fail('A footprint blocked at every supported pose reached a native finger check')
        module['finger_sweep_clear'] = unexpected
        self.assertEqual(module['chip_clearance_options'](geometry, {}, 'right',
            Rotation.identity(), np.zeros(3)), [])

    def test_measurement_uses_new_source_size_and_optional_occupancy(self):
        import numpy as np
        results = []
        for width in (8, 14):
            selected = self.library.select('Move the red block onto the green towel.')
            module = {};exec(selected['response']['source'], module)
            module['log'] = lambda *args:None
            h, w = 100, 120
            v, u = np.indices((h, w));depth = np.ones((h, w))
            block = (u >= 16)&(u < 16+width)&(v >= 45)&(v < 58)
            towel = (u >= 58)&(u < 112)&(v >= 12)&(v < 88)
            depth[block] = 1.025;depth[towel] = 1.001
            p = np.stack((u*depth/1000, v*depth/1000, depth), -1)
            frame = {'rgb':np.zeros((h, w, 3), np.uint8), 'depth_m':depth,
                'K':np.diag([1000., 1000., 1.]), 'T_world_camera':np.eye(4),
                'metadata':{'depth_scale_m':.001, 'captured_at':1}}
            module['observe'] = lambda tools:(frame, p, np.ones((h, w), bool))
            masks = {'red rectangular block':block, 'large teal fabric towel':towel,
                'white tabletop':~(block|towel)}
            def segment(rgb, query):
                return {'mask':masks.get(query, np.zeros((h,w),bool)), 'status':'Success'}
            measured = module['measure']({'segment_camera_rgb':segment})
            self.assertEqual(measured['occupied_objects'], [])
            self.assertEqual(measured['source_kind'], 'source_table')
            self.assertFalse(measured['height_provenance']['transferred_object_dimensions'])
            self.assertTrue(measured['targets'])
            results.append(measured['dimensions'])
        self.assertGreater(np.prod(results[1]), np.prod(results[0]))

    def test_chip_color_selects_fresh_circle_without_a_color_specific_program(self):
        import numpy as np
        for color in ('red', 'green', 'blue'):
            selected=self.library.select(f'Move the black block onto the {color} chip.')
            module={};exec(selected['response']['source'],module)
            module['log']=lambda *args:None
            h,w=100,120;v,u=np.indices((h,w));depth=np.ones((h,w))
            block=(u>=16)&(u<30)&(v>=44)&(v<58)
            circle=(u-85)**2+(v-45)**2<=10**2
            depth[block]=1.025;depth[circle]=1.004
            p=np.stack((u*depth/1000,v*depth/1000,depth),-1)
            frame={'rgb':np.zeros((h,w,3),np.uint8),'depth_m':depth,
                'K':np.diag([1000.,1000.,1.]),'T_world_camera':np.eye(4),'metadata':{'depth_scale_m':.001}}
            module['observe']=lambda tools:(frame,p,np.ones((h,w),bool))
            masks={'black rectangular block':block,color+' round poker chip':circle,'white tabletop':~(block|circle)}
            def segment(rgb,query):
                return {'mask':masks.get(query,np.zeros((h,w),bool)),'status':'Success'}
            g=module['measure']({'segment_camera_rgb':segment})
            self.assertEqual(g['target_kind'],'chip')
            self.assertEqual(g['target_identity'],color+' round poker chip')
            self.assertAlmostEqual(g['chip_radius'],.01,delta=.002)
            self.assertAlmostEqual(g['chip_z'],1.004,places=6)
            self.assertFalse(g['height_provenance']['transferred_object_dimensions'])

    def test_grasp_axis_failure_tries_the_other_measured_span_in_same_executable(self):
        import numpy as np
        selected=self.library.select('Move the red block onto the green towel.')
        module={};exec(selected['response']['source'],module)
        module['log']=lambda *args:None
        geometry={'dimensions':np.array([.02,.04]),'axes':np.eye(2),'source_xy':np.array([.3,.1]),
            'source_plane':np.zeros(3),'base':0.,'top':.03,'height':.03,'obstacle_top_z':.03,
            'targets':[{'xy':np.array([.4,-.1]),'z':0.}]}
        module['measure']=lambda tools:geometry
        widths=[]
        def contact(station,side,width):
            widths.append(width)
            return np.array([0.,0.,.04]),{'open_gap_m':.095,'contact_points_native':np.array([[-width/2,0,.04],[width/2,0,.04]])}
        module['contact']=contact
        state={'arms':{side:{'ee_pos':[.3,.3 if side=='left' else -.3,.18],
            'ee_quat':[0,0,0,1],'joint_pos':[0]*6,'gripper_pos':1.} for side in ('left','right')}}
        def sample(description,**kwargs):
            return [SimpleNamespace(position=kwargs['detection']['position_3d'],rpy=[0.,180.,kwargs['yaws'][0]])]
        def plan(segments):
            self.assertTrue(all(set(s['gripper_state'])=={'left','right'} for s in segments))
            return {'success':widths[-1]==.02,'failing_stage':'carry','reason':'fixture axis failure'}
        tools={'get_station_info':lambda:{'gripper_geometry':{'fixture':True}},
            'get_planning_state':lambda:state,'sample_topdown_geometric':sample,
            'estimate_drop':lambda description,**kw:np.array(kw['detection']['position_3d'])+[0,0,kw['z_offset']],
            'plan_freespace_sequence':plan}
        task=module['build_task'](tools)
        self.assertEqual(set(widths),{.02,.04})
        self.assertEqual(task['geometry']['grasp_span_m'],.02)
        self.assertEqual(task['geometry']['opening_axis_index'],0)
        self.assertTrue(task['steps'])
