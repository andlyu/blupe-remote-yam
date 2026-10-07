import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from remote_yam.aspire_codex_policy import AspireCodexPolicy, configured_aspire_policy
from remote_yam.codex_policy import CodexAdapter
from remote_yam.aspire_skill_learning import TopicSkillLibrary, configured_skill_library


ASPIRE = Path(os.environ.get('ASPIRE_TEST_CHECKOUT', '/private/tmp/aspire-yam-f4c8939'))
SOURCE = '''def build_task(tools):
    query = "green block"
    observation = tools["observe"]()
    points = observation["points"]
    center = points.mean(axis=0)
    return {"query": query, "center": center}

def evaluate(tools, task):
    return {"success": False}
'''


@unittest.skipUnless((ASPIRE/'aspire/sim/scripts/libero/record_skill_promotion.py').is_file(),
    'Set ASPIRE_TEST_CHECKOUT to the separate pinned ASPIRE checkout')
class TopicSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = TopicSkillLibrary(self.root/'topics', ASPIRE)

    def run_fixture(self, name='green', *, successful=True, parking=True, role='development', color='green'):
        run = self.root/name
        attempt = run/'policy/attempt-01'
        attempt.mkdir(parents=True)
        (attempt/'generated_program.py').write_text(SOURCE.replace('green block', color+' block'))
        (attempt/'coding-response.json').write_text(json.dumps({'lesson': 'fixture'}))
        (attempt/'queries.json').write_text(json.dumps({'block': color+' block'}))
        (run/'policy/coding-loop.json').write_text(json.dumps({'attempts': [
            {'directory': str(attempt), 'model_conversation_id': None}]}))
        (run/'receipt.json').write_text(json.dumps({'task': 'Pick '+color+' block on towel',
            'result': {'status': 'UNVERIFIED', 'success': False},
            'review_through_parking': {'status': 'PARKING_OBSERVED' if parking else 'TIMEOUT'},
            'postpark_evaluation': {'placement_evidence': {'success': False,
                'evidence': {'checks': {'supported_height': False, 'withdrawn': True}}}}}))
        (run/'review.json').write_text(json.dumps({'success': successful,
            'validation_scope': 'Visual placement only, contact height unverified',
            'coding_author': 'Supplied agent fixture, not configured model'}))
        sam = attempt/'execute/observations/runpod-sam3/001-fixture'
        sam.mkdir(parents=True)
        (sam/'request.json').write_text(json.dumps({'model': 'facebook/sam3',
            'queries': {'block': color+' block'}, 'image_sha256': 'image-fixture',
            'timing': {'worker_id': 'worker-fixture'}}))
        (sam/'response.json').write_text(json.dumps({'query_encoding': {'block': {
            'effective_text': color+' block', 'token_limit': 32, 'truncated': False}}}))
        staged = self.library.stage_run(run/'receipt.json', run/'review.json', evidence_role=role,
            provenance={'worker_revision': {'commit': 'worker-commit'}})
        return run, staged['task_id']

    def finding(self, run, *, status='observed_recipe'):
        return {'id': 'measured-block-center', 'topic': 'localize', 'title': 'Measure the block',
            'trigger': 'A block needs fresh observed geometry', 'why': 'Uses current sensor points',
            'scope': 'Single fixture scene, visual outcome only', 'status': status,
            'limits': ['Height and physical metrology remain unverified'], 'keywords': ['block', 'towel'],
            'snippets': [{'source': str(run/'policy/attempt-01/generated_program.py'),
                'start_line': 1, 'end_line': 6, 'generalize': {'"green block"': 'object_description'}}]}

    def test_real_upstream_recorder_preserves_patch_snapshots_and_ledger(self):
        run, task = self.run_fixture()
        record = self.library.promote(task, [self.finding(run)])
        self.assertFalse(record['no_op'])
        folder = self.library.recorder.completed_record(self.library.root, 'robohouse', task)[0]
        self.assertTrue((folder/'before/.claude/libero/skills/localize.md').exists())
        self.assertTrue((folder/'after/.claude/libero/skills/localize.md').exists())
        patch = (self.library.root/record['patch_path']).read_text()
        self.assertIn('object_description', patch)
        self.assertEqual(self.library.verify(task), record)
        self.assertEqual(len(self.library.recorder.read_ledger(self.library.root, 'robohouse')), 1)

    def test_generalized_variants_merge_one_section_with_both_evidence_scopes(self):
        first, task1 = self.run_fixture()
        self.library.promote(task1, [self.finding(first)])
        before_ledger = self.library.recorder.ledger_path(self.library.root, 'robohouse').read_bytes()
        second, task2 = self.run_fixture('black', color='black')
        finding = self.finding(second)
        finding['snippets'][0]['generalize'] = {'"black block"': 'object_description'}
        self.library.promote(task2, [finding])
        self.assertTrue(self.library.recorder.ledger_path(self.library.root, 'robohouse').read_bytes().startswith(before_ledger))
        index = json.loads((self.library.skills/'patterns.json').read_text())
        item = index['patterns'][0]
        self.assertEqual(len(index['patterns']), 1)
        self.assertEqual(len(item['snippets']), 1)
        self.assertEqual(len(item['evidence']), 2)
        self.assertEqual((self.library.skills/'localize.md').read_text().count('## Measure the block'), 1)
        self.assertEqual(len(self.library.recorder.read_ledger(self.library.root, 'robohouse')), 2)
        self.library.promote(task2, [finding])
        self.assertEqual(len(self.library.recorder.read_ledger(self.library.root, 'robohouse')), 2)

    def test_no_op_requires_reason_and_unlocks_later_task_dispatch(self):
        _, task = self.run_fixture()
        self.library.require_reviewed('Pick green block on towel')  # Same-task repair.
        with self.assertRaisesRegex(ValueError, 'promotion review required'):
            self.library.require_reviewed('Pick red block on chip')
        with self.assertRaisesRegex(ValueError, 'requires a concise reason'):
            self.library.promote(task, [])
        record = self.library.promote(task, [], reason='No supported generalizable finding')
        self.assertTrue(record['no_op'])
        self.assertEqual(record['reason'], 'No supported generalizable finding')
        self.assertEqual((self.library.root/record['patch_path']).read_text(), '')
        self.library.require_reviewed('Pick red block on chip')

    def test_held_out_observations_cannot_edit_shared_library(self):
        run, task = self.run_fixture(role='held_out')
        with self.assertRaisesRegex(ValueError, 'Held-out evidence'):
            self.library.promote(task, [self.finding(run)])
        record = self.library.promote(task, [], reason='Held-out outcomes retained separately')
        self.assertTrue(record['no_op'])

    def test_failure_is_retained_as_failure_not_successful_recipe(self):
        run, task = self.run_fixture(successful=False)
        with self.assertRaisesRegex(ValueError, 'Failed/unreviewed'):
            self.library.promote(task, [self.finding(run)])
        self.library.promote(task, [self.finding(run, status='failure')])
        entry = self.library.retrieve('Pick block')['entries'][0]
        self.assertEqual(entry['statuses'], ['failure'])
        self.assertFalse(entry['evidence'][0]['postpark_checks']['supported_height'])
        self.assertEqual(entry['evidence'][0]['native_status'], 'UNVERIFIED')

    def test_unparked_result_cannot_promote_observed_success(self):
        run, task = self.run_fixture(parking=False)
        with self.assertRaisesRegex(ValueError, 'Failed/unreviewed'):
            self.library.promote(task, [self.finding(run)])

    def test_source_hash_and_real_line_selection_are_required(self):
        run, task = self.run_fixture()
        source = run/'policy/attempt-01/generated_program.py'
        source.write_text(source.read_text()+'# modified\n')
        with self.assertRaisesRegex(ValueError, 'exact recorded program'):
            self.library.promote(task, [self.finding(run)])
        self.assertEqual(self.library.recorder.pending_promotions(self.library.root, 'robohouse'), [])

    def test_pending_review_survives_temporary_run_cleanup_with_exact_source(self):
        run, task = self.run_fixture()
        packet = json.loads((self.library.campaign/task/'findings.json').read_text())
        program = packet['programs'][0]
        retained = Path(program['retained_source'])
        self.assertEqual(retained.read_text(), SOURCE)
        self.assertEqual(program['path'], str((run/'policy/attempt-01/generated_program.py').resolve()))
        finding = self.finding(run)
        shutil.rmtree(run)
        received = []
        def coordinator(**request):
            received.append(request['sources'])
            return {'findings':[finding], 'reason':'Exact retained development source'}
        self.library.review_pending(coordinator, current_task='Pick red block on chip')
        self.assertEqual(received, [{program['path']:SOURCE}])
        self.assertFalse(self.library.verify(task)['no_op'])
        entry = self.library.retrieve('block')['entries'][0]
        self.assertEqual(entry['snippets'][0]['source_sha256'], program['sha256'])
        self.assertEqual(entry['evidence'][0]['programs'][0]['retained_source'], str(retained))

    def test_changed_retained_source_cannot_be_reviewed_after_cleanup(self):
        run, task = self.run_fixture()
        packet = json.loads((self.library.campaign/task/'findings.json').read_text())
        retained = Path(packet['programs'][0]['retained_source'])
        shutil.rmtree(run)
        retained.write_text(SOURCE+'# changed\n')
        def coordinator(**request):self.fail('Changed source reached the coordinator')
        with self.assertRaisesRegex(ValueError, 'Recorded program changed'):
            self.library.review_pending(coordinator,current_task='Pick red block on chip')
        self.assertIsNone(self.library.recorder.completed_record(self.library.root,'robohouse',task))

    def test_pending_legacy_source_can_be_restaged_from_hash_verified_archive(self):
        run, task = self.run_fixture()
        path = self.library.campaign/task/'findings.json'
        packet = json.loads(path.read_text())
        archive = self.root/'archive.py'
        archive.write_text(SOURCE)
        Path(packet['programs'][0]['retained_source']).unlink()
        packet['programs'][0]['retained_source'] = str(archive)
        shutil.rmtree(run)
        with self.library._locked():self.library._stage(packet)
        repaired = json.loads(path.read_text())
        retained = Path(repaired['programs'][0]['retained_source'])
        archive.unlink()
        self.assertEqual(retained.read_text(),SOURCE)
        self.assertEqual(repaired['programs'][0]['path'],packet['programs'][0]['path'])
        self.assertEqual(repaired['programs'][0]['sha256'],packet['programs'][0]['sha256'])

    def test_patch_and_current_library_tampering_block_retrieval(self):
        run, task = self.run_fixture()
        record = self.library.promote(task, [self.finding(run)])
        patch = self.library.root/record['patch_path']
        content = patch.read_text()
        patch.write_text(content+'tamper')
        with self.assertRaisesRegex(ValueError, 'patch integrity'):
            self.library.retrieve('block')
        patch.write_text(content)
        (self.library.skills/'localize.md').write_text('unrecorded change')
        with self.assertRaisesRegex(ValueError, 'last recorded promotion'):
            self.library.retrieve('block')

    def test_future_coding_prompt_contains_recipes_queries_limits_and_actual_authorship(self):
        run, task = self.run_fixture()
        self.library.promote(task, [self.finding(run)])
        captured = []
        def generate(**request):
            captured.append(request)
            return {'action': 'give_up', 'source': '', 'summary': 'fixture', 'lesson': '', 'queries': []}
        policy = AspireCodexPolicy(harness=lambda **kw: None, calibration={}, robot_id='fixture',
            task='Pick red block on towel', directory=self.root/'future', instructions='fixture',
            skill_directory=self.root/'whole-programs', generator=generate, skill_learning=self.library)
        policy._generate({'context': {'fixture': True}}, {'status': 'CURRENT_FIXTURE'})
        prompt = captured[0]['prompt']
        for phrase in ('RETRIEVED ASPIRE TOPIC SKILLS', 'object_description', 'supported_height',
                       'CURRENT_FIXTURE', 'effective_text', 'token_limit', 'worker-commit',
                       'not configured model', 'PREVIOUS DEVELOPMENT HISTORY', 'CURRENT RUN ACTIVITY'):
            self.assertIn(phrase, prompt)
        self.assertIn('unverified', prompt.lower())
        self.assertEqual(json.loads((run/'receipt.json').read_text())['result']['status'], 'UNVERIFIED')

    def test_policy_completion_stages_findings_then_automatic_no_op_allows_later_dispatch(self):
        reply = {'action': 'program', 'source': SOURCE, 'summary': 'fixture', 'lesson': 'fixture',
            'queries': [{'name': 'block', 'query': 'green block'}]}
        calls = []
        def harness(**kwargs):
            calls.append(kwargs['mode'])
            if kwargs['mode'] == 'observe':
                return {'status': 'OBSERVED', 'context': {}, 'images': {}, 'snapshot': 'fixture'}
            return {'planning_success': True, 'status': 'PLAN_ONLY', 'success': False}
        policy = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture',
            task='Pick green block', directory=self.root/'run/policy', instructions='fixture',
            skill_directory=self.root/'whole-programs', generator=lambda **kw: reply,
            skill_learning=self.library, plan_only=True)
        self.assertEqual(policy._coding_loop()['status'], 'PLAN_ONLY')
        staged = json.loads((policy.directory/'skill-learning.json').read_text())
        self.assertEqual(staged['status'], 'AWAITING_PROMOTION_REVIEW')
        self.assertEqual(calls, ['observe', 'plan'])
        handoffs = []
        def coordinator(**request):
            handoffs.append(request)
            return {'findings': [], 'reason': 'Only offline fixture planning; no generalizable physical finding'}
        later = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture',
            task='Pick red block', directory=self.root/'later/policy', instructions='fixture',
            skill_directory=self.root/'whole-programs', generator=lambda **kw: reply,
            skill_learning=self.library, skill_coordinator=coordinator, plan_only=True)
        later._coding_loop()
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(calls, ['observe', 'plan', 'observe', 'plan'])
        self.assertTrue(self.library.verify(staged['task_id'])['no_op'])

    def test_automatic_coordinator_promotes_prior_review_before_future_policy_receives_recipe(self):
        run, task = self.run_fixture()
        order, requests = [], []
        def coordinator(**request):
            order.append('coordinator')
            self.assertEqual(request['sources'][str((run/'policy/attempt-01/generated_program.py').resolve())], SOURCE)
            return {'findings': [self.finding(run)], 'reason': 'Supported fixture review'}
        def generate(**request):
            order.append('generate')
            requests.append(request)
            self.assertFalse(self.library.verify(task)['no_op'])
            return {'action': 'give_up', 'source': '', 'summary': 'fixture', 'lesson': '', 'queries': []}
        def harness(**request):
            order.append('observe')
            return {'status': 'OBSERVED', 'context': {}, 'images': {}, 'snapshot': 'fixture'}
        policy = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture',
            task='Pick red block on towel', directory=self.root/'future/policy', instructions='fixture',
            skill_directory=self.root/'whole-programs', generator=generate, plan_only=True,
            skill_learning=self.library, skill_coordinator=coordinator)
        policy._coding_loop()
        self.assertEqual(order, ['coordinator', 'observe', 'generate'])
        self.assertIn('object_description', requests[0]['prompt'])
        self.assertIn('supported_height', requests[0]['prompt'])

    def test_coordinator_repairs_rejected_success_claim_with_real_failure_scope(self):
        run, task = self.run_fixture(successful=False)
        requests = []
        def coordinator(**request):
            requests.append(request)
            return {'findings': [self.finding(run, status='observed_recipe' if len(requests)==1 else 'failure')],
                'reason': 'Preserve failure finding'}
        self.library.review_pending(coordinator, current_task='A later task')
        self.assertIn('Failed/unreviewed', requests[1]['feedback'])
        self.assertEqual(self.library.retrieve('block')['entries'][0]['statuses'], ['failure'])

    def test_background_review_cannot_promote_a_packet_changed_during_inference(self):
        run, task = self.run_fixture()
        packet_path = self.library.campaign/task/'findings.json'
        original = packet_path.read_text()
        def coordinator(**request):
            packet = json.loads(original)
            packet['retained_placement_observed'] = False
            packet['review_scope'] = 'New physical failure arrived during review'
            packet_path.write_text(json.dumps(packet))
            return {'findings': [self.finding(run)], 'reason': 'Old review must not apply'}
        self.assertEqual(self.library.review_pending(coordinator), [])
        self.assertIsNone(self.library.recorder.completed_record(self.library.root, 'robohouse', task))
        self.assertEqual(self.library.retrieve('block')['entries'], [])

    def test_background_review_waits_for_after_parking_findings(self):
        run = self.root/'ongoing'
        attempt = run/'attempt-01'
        attempt.mkdir(parents=True)
        (attempt/'generated_program.py').write_text(SOURCE)
        (attempt/'queries.json').write_text('{}')
        staged = self.library.stage_policy(run, 'Ongoing fixture',
            [{'directory': str(attempt), 'execution': {'physical_motion_calls': 1}}], review_ready=False)
        coordinator = Mock()
        self.assertEqual(self.library.review_pending(coordinator), [])
        coordinator.assert_not_called()
        self.assertIsNone(self.library.recorder.completed_record(self.library.root, 'robohouse', staged['task_id']))

    def test_after_run_learning_does_not_delay_coding_or_change_active_snapshot(self):
        run, task = self.run_fixture()
        entered, release = threading.Event(), threading.Event()
        def coordinator(**request):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('Fixture reviewer was not released')
            return {'findings': [self.finding(run)], 'reason': 'Reviewed after run'}
        def generate(**request):
            self.assertFalse(entered.is_set())
            self.assertIsNone(self.library.recorder.completed_record(self.library.root, 'robohouse', task))
            return {'action': 'give_up', 'source': '', 'summary': 'Fixture ends without motion',
                'lesson': '', 'queries': []}
        policy = AspireCodexPolicy(harness=lambda **kw: {'status': 'OBSERVED',
                'context': {}, 'images': {}, 'snapshot': 'fixture'},
            calibration={}, robot_id='fixture', task='Later fixture task',
            directory=self.root/'later/policy', instructions='', skill_directory=self.root/'programs',
            generator=generate, plan_only=True, skill_learning=self.library,
            skill_coordinator=coordinator, skill_review_timing='after_run')
        result = policy._coding_loop()
        try:
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertTrue(entered.wait(1))
            self.assertEqual(policy._topic_knowledge()['entries'], [])
            self.assertFalse(policy._deferred_review.done.is_set())
        finally:
            release.set()
            self.assertTrue(policy._deferred_review.done.wait(2))
        self.assertTrue(self.library.retrieve('block')['entries'])
        self.assertEqual(policy._topic_knowledge()['entries'], [])

    def test_clean_baseline_explicitly_excludes_accumulated_topics(self):
        config = {'aspire': str(ASPIRE), 'coding_baseline': 'upstream',
            'skill_learning': {'enabled': True, 'root': str(self.root/'topics')}}
        self.assertIsNone(configured_skill_library(config))

    def test_coordinator_uses_text_only_subscription_transport_without_robot_tools(self):
        run, task = self.run_fixture()
        requests = []
        provider = SimpleNamespace(_calls=0, _post_json=lambda payload:
            (requests.append(payload), {'findings': [], 'reason': 'No supported generalization'})[1])
        policy = AspireCodexPolicy(harness=lambda **kw: None, calibration={}, robot_id='fixture',
            task='Next task', directory=self.root/'future/policy', instructions='',
            skill_directory=self.root/'whole-programs', skill_learning=self.library)
        with patch('remote_yam.aspire_codex_policy.CodexAdapter', return_value=provider):
            policy._review_prior_skills(policy.task)
        self.assertEqual(provider._expected_camera_count, 0)
        self.assertEqual(requests[0]['tools'], [])
        body=json.loads(requests[0]['input'][0]['content'][0]['text'])
        self.assertIn('1 def build_task', next(iter(body['numbered_source_programs'].values())))
        self.assertTrue(self.library.verify(task)['no_op'])

    def test_review_amendment_preserves_prior_promotion_and_merges_comment_only_variant(self):
        run, task = self.run_fixture()
        finding = self.finding(run)
        record = self.library.promote(task, [finding])
        old_packet=(self.library.campaign/task/'findings.json').read_bytes()
        staged=self.library.stage_run(run/'receipt.json',run/'review.json',amendment='review-update')
        revised=self.library.promote(staged['task_id'],[finding],reason='Additional reviewed evidence')
        self.assertEqual((self.library.campaign/task/'findings.json').read_bytes(),old_packet)
        self.assertEqual(self.library.verify(task),record)
        self.assertEqual(self.library.verify(staged['task_id']),revised)
        self.assertEqual(self.library._code_key('a = 1\n# comment'),self.library._code_key('a=1'))

    def test_real_subscription_transport_keeps_second_coordinator_feedback_in_incremental_history(self):
        run,task=self.run_fixture(successful=False)
        provider=object.__new__(CodexAdapter)
        provider._workspace=tempfile.TemporaryDirectory()
        self.addCleanup(provider._workspace.cleanup)
        provider.model='fixture';provider._thread_id=None;provider._sent_items=0
        provider._binary='unused';provider._calls=0
        prompts=[]
        def executor(**request):
            prompts.append(request['prompt'])
            finding=self.finding(run,status='observed_recipe' if len(prompts)==1 else 'failure')
            for snippet in finding['snippets']:
                snippet['generalize']=[{'original':old,'replacement':new}
                    for old,new in snippet['generalize'].items()]
            final={'findings':[finding],'reason':'Retain actual failure'}
            return [{'type':'thread.started','thread_id':'12345678-1234-4234-8234-123456789abc'},
                {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}},
                {'type':'turn.completed'}]
        provider._decision_executor=executor
        policy=AspireCodexPolicy(harness=lambda **kw:None,calibration={},robot_id='fixture',
            task='Later task',directory=self.root/'future/policy',instructions='',
            skill_directory=self.root/'whole-programs',skill_learning=self.library)
        with patch('remote_yam.aspire_codex_policy.CodexAdapter',return_value=provider):
            policy._review_prior_skills(policy.task)
        self.assertEqual(len(prompts),2)
        self.assertIn('Failed/unreviewed',prompts[1])
        self.assertIn('1 def build_task',prompts[1])
        self.assertEqual(self.library.retrieve('block')['failure_findings'][0]['statuses'],['failure'])

    def test_configured_playground_policy_factory_enables_learning_without_model_or_robot_calls(self):
        module=self.root/'harness.py'
        module.write_text('def coding_instructions(config): return "fixture station contract"\n'
            'class HarnessClient:\n'
            ' def __init__(self, *args): pass\n'
            ' def __call__(self, **kwargs): raise AssertionError("No robot calls in factory test")\n')
        config={'aspire':str(ASPIRE),'harness_module':str(module),'skill_directory':str(self.root/'programs'),
            'skill_learning':{'enabled':True,'root':str(self.library.root),'review_timing':'after_run'},
            'segmentation_backend':'runpod_sam3'}
        with patch('remote_yam.api_depth.ApiDepth.read',return_value='{}') as calibration, \
                patch('remote_yam.runpod_sam3.configured_vision_session',return_value=None) as vision, \
                patch('remote_yam.aspire_codex_policy.CodexAdapter') as model:
            provider=configured_aspire_policy(config,origin='https://fixture.invalid',
                robot_id='fixture',model='fixture',directory=self.root/'factory')
        self.assertEqual(provider.skill_learning.root,self.library.root)
        self.assertEqual(provider.skill_coordinator.__name__,'_coordinate_skill_promotion')
        self.assertTrue(provider.public_config()['skill_learning']['enabled'])
        self.assertEqual(provider.public_config()['skill_learning']['review_timing'], 'after_run')
        calibration.assert_called_once_with('/calibration',256_000)
        vision.assert_called_once_with(config)
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
