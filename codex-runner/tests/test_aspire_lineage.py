import json
from pathlib import Path
import tempfile
import unittest

from remote_yam.aspire_lineage import sha256, version, retrieval_catalog, record_lineage
from remote_yam.aspire_lineage_catalog import LineageCatalog


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.source = 'def grasp():\n    return 1'
        self.snippet = dict(source='/recorded/program.py', source_sha256='a'*64,
            start_line=3, end_line=4, code=self.source, original_code=self.source)
        self.entry = dict(id='grasp', snippets=[self.snippet], evidence=[])
        self.retrieved = retrieval_catalog({'entries':[self.entry]})
        self.ref = {k:self.snippet[k] for k in ('source','source_sha256','start_line','end_line')}
        self.ref.update(target_start_line=1,target_end_line=2)
        self.use = dict(id='grasp',version=version(self.entry),usage='unchanged',changed='',source_refs=[self.ref])

    def test_legacy_retrieval_never_becomes_use(self):
        trace = record_lineage({'source':self.source},self.retrieved)
        self.assertFalse(trace['usage_recorded'])
        self.assertEqual(trace['used'],[])
        self.assertEqual(trace['retrieved'][0]['id'],'grasp')

    def test_exact_reuse_checks_source_and_target_ranges(self):
        trace = record_lineage({'source':self.source,'lineage':{'used':[self.use],'new_skills':[]}},self.retrieved)
        self.assertEqual(trace['used'][0]['verification'],'exact_source_match')
        self.assertEqual(trace['used'][0]['source_refs'][0]['derived_code'],self.source)
        with self.assertRaises(ValueError):
            record_lineage({'source':self.source.replace('1','2'),'lineage':{'used':[self.use],'new_skills':[]}},self.retrieved)

    def test_adaptation_does_not_claim_exact_reuse(self):
        self.use.update(usage='adapted',changed='Return 2 for the new behavior')
        trace=record_lineage({'source':self.source.replace('1','2'), 'lineage':{'used':[self.use],'new_skills':[]}},self.retrieved)
        self.assertEqual(trace['used'][0]['verification'],'model_declared_adaptation')
        self.assertFalse(trace['used'][0]['source_refs'][0]['exact_match'])

    def _contained_fixture(self):
        original = '\n'.join(f'original_{n} = {n}' for n in range(530, 544))
        snippet = dict(source='/recorded/context.py', source_sha256='e'*64,
            start_line=530, end_line=543, code=original, original_code=original)
        entry = dict(id='measured-chip-context-placement', snippets=[snippet])
        retrieved = retrieval_catalog({'entries': [entry]})
        ref = {k: snippet[k] for k in ('source', 'source_sha256')}
        ref.update(start_line=530, end_line=539, target_start_line=1, target_end_line=10)
        use = dict(id=entry['id'], version=version(entry), usage='unchanged',
            changed='', source_refs=[ref])
        source = '\n'.join(original.splitlines()[:10])
        return source, retrieved, use

    def test_contained_original_range_records_only_the_cited_lines(self):
        source, retrieved, use = self._contained_fixture()
        trace = record_lineage({'source': source,
            'lineage': {'used': [use], 'new_skills': []}}, retrieved)
        ref = trace['used'][0]['source_refs'][0]
        self.assertEqual((ref['start_line'], ref['end_line']), (530, 539))
        self.assertEqual(ref['recipe_code'], source)
        self.assertEqual(ref['original_code'], source)
        self.assertEqual(ref['snippet_sha256'], sha256(source))
        self.assertEqual(trace['used'][0]['verification'], 'exact_source_match')

    def test_contained_range_still_enforces_unchanged_and_adapted_evidence(self):
        source, retrieved, use = self._contained_fixture()
        changed = source.replace('original_530 = 530', 'original_530 = 0')
        reply = {'source': changed, 'lineage': {'used': [use], 'new_skills': []}}
        with self.assertRaisesRegex(ValueError, 'Unchanged skill'):
            record_lineage(reply, retrieved)
        use.update(usage='adapted', changed='Use the current measured placement height')
        trace = record_lineage(reply, retrieved)
        self.assertEqual(trace['used'][0]['verification'], 'model_declared_adaptation')
        self.assertEqual(trace['used'][0]['source_refs'][0]['original_code'], source)

    def test_contained_range_rejects_unretrieved_or_invalid_bounds(self):
        source, retrieved, use = self._contained_fixture()
        ref = use['source_refs'][0]
        for change in ({'source_sha256': 'f'*64}, {'source': '/other.py'},
                {'start_line': 529}, {'end_line': 544}, {'end_line': 529},
                {'start_line': True}, {'target_end_line': 11}):
            altered = {**use, 'source_refs': [{**ref, **change}]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                record_lineage({'source': source,
                    'lineage': {'used': [altered], 'new_skills': []}}, retrieved)

    def test_contained_range_rejects_ambiguous_generalized_recipe_mapping(self):
        source, retrieved, use = self._contained_fixture()
        for field in ('code', 'original_code'):
            altered = [{**retrieved[0], 'snippets': [
                {**retrieved[0]['snippets'][0], field: 'generalized recipe'}]}]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'unambiguously'):
                record_lineage({'source': source,
                    'lineage': {'used': [use], 'new_skills': []}}, altered)

    def test_invented_version_and_source_are_rejected(self):
        for key in ('version','id'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                record_lineage({'source':self.source,'lineage':{'used':[{**self.use,key:'invented'}],'new_skills':[]}},self.retrieved)
        self.ref['source_sha256']='b'*64
        with self.assertRaises(ValueError):
            record_lineage({'source':self.source,'lineage':{'used':[self.use],'new_skills':[]}},self.retrieved)

    def test_new_code_is_recorded_without_physical_success(self):
        item=dict(id='new-grasp',title='New grasp',behavior='Select new grasp',reason='No compatible code',start_line=1,end_line=2)
        trace=record_lineage({'source':self.source,'lineage':{'used':[],'new_skills':[item]}})
        self.assertEqual(trace['new_skills'][0]['code_sha256'],sha256(self.source))
        self.assertIsNone(trace['new_skills'][0]['physical_success'])
        self.assertIsNone(trace['new_skills'][0]['planning_success'])

    def test_executable_proof_uses_saved_body_not_changed_core_file(self):
        source='TASK_INPUTS = {}\n\n'+self.source
        provenance=dict(skill='grasp',version=1,core_source='/now/revised.py',core_source_sha256=sha256(self.source),
            bound_source_sha256=sha256(source),inputs={},development_evidence=[],validation_scope='One episode')
        trace=record_lineage({'source':source},executable=provenance)
        self.assertEqual(trace['used'][0]['verification'],'exact_source_match')
        provenance['core_source_sha256']='a'*64
        self.assertEqual(record_lineage({'source':source},executable=provenance)['used'][0]['verification'],'hash_mismatch')

    def test_recipe_version_is_stable_across_full_and_compact_evidence(self):
        self.assertEqual(version(self.entry),version({**self.entry,'evidence':[{'full':'packet'}]}))

    def test_historical_executable_links_saved_bound_program_when_live_core_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);skills=root/'outputs/aspire-skills/robohouse';skills.mkdir(parents=True)
            run=root/'outputs/aspire-test/live';attempt=run/'policy/attempt-01';attempt.mkdir(parents=True)
            current=root/'skills/core.py';current.parent.mkdir();current.write_text('repaired core')
            bound='TASK_INPUTS = {}\n\n'+self.source
            (attempt/'generated_program.py').write_text(bound)
            (attempt/'coding-response.json').write_text(json.dumps({'source':bound}))
            provenance=dict(skill='grasp',version=1,core_source=str(current),core_source_sha256=sha256(self.source),
                bound_source_sha256=sha256(bound),inputs={},development_evidence=[],validation_scope='One episode')
            (run/'policy/coding-loop.json').write_text(json.dumps({'attempts':[{'attempt':1,'directory':str(attempt),'executable_reuse':provenance}]}))
            (run/'receipt.json').write_text(json.dumps({'task':'Fixture placement'}))
            catalog=LineageCatalog(dict(robot_id='fixture',skill_directory=str(skills),executable_skills={'manifest':str(current.parent/'manifest.json')}))
            item=catalog.episode(dict(task_id='historical',task='Fixture placement',receipt=str(run/'receipt.json'),policy_directory=str(run/'policy')))
            ref=item['attempts'][0]['lineage']['used'][0]['source_refs'][0]
            self.assertIsNone(ref['original_artifact'])
            self.assertEqual(ref['original_source_state'],'changed_or_unavailable')
            self.assertEqual(ref['recorded_program']['source'],str((attempt/'generated_program.py').resolve()))
            self.assertEqual(sha256(ref['derived_code']),provenance['core_source_sha256'])

    def test_new_saved_program_is_visible_without_fabricating_an_episode_review(self):
        with tempfile.TemporaryDirectory() as directory:
            skills=Path(directory)/'outputs/aspire-skills/robohouse';skills.mkdir(parents=True)
            source=skills/'fixture.py';source.write_text(self.source)
            metadata=skills/'fixture.json';metadata.write_text(json.dumps(dict(task='New behavior',
                source_sha256=sha256(self.source),validation='FULL_PLAN_ONLY',code_generation_kind='codex_subscription_transport')))
            catalog=LineageCatalog(dict(robot_id='fixture',skill_directory=str(skills)))
            item=catalog.build()['episodes'][0]
            self.assertIsNone(item['episode_id']);self.assertIsNone(item['after_parking_success'])
            self.assertTrue(item['attempts'][0]['lineage']['authorship']['generated_by_codex'])
            self.assertFalse(item['attempts'][0]['lineage']['usage_recorded'])
            self.assertEqual(item['attempts'][0]['code'],self.source)
            source.write_text('Changed source')
            self.assertEqual(catalog.build()['episodes'],[])

    def test_catalog_cannot_serve_unrelated_files_or_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);skills=root/'outputs/aspire-skills/robohouse';skills.mkdir(parents=True)
            catalog=LineageCatalog(dict(robot_id='fixture',skill_directory=str(skills)))
            unrelated=root/'private.json';unrelated.write_text('{}')
            symlink=skills/'link.json';symlink.symlink_to(unrelated)
            self.assertIsNone(catalog.artifact(unrelated))
            self.assertIsNone(catalog.artifact(symlink))
            recorded=skills/'recorded.json';recorded.write_text('{}')
            self.assertIsNotNone(catalog.artifact(recorded))


if __name__=='__main__':unittest.main()
