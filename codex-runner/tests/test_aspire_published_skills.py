import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from remote_yam.aspire_published_skills import PublishedSkillLibrary, merge_topic_knowledge
from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_executable_skills import ExecutableSkillLibrary


class PublishedSkillsTests(unittest.TestCase):
    def setUp(self):
        self.library = PublishedSkillLibrary()

    def test_recovered_catalog_has_real_hashed_programs_and_all_scoped_patterns(self):
        self.assertEqual(len(self.library._programs), 32)
        self.assertEqual(len(self.library.patterns), 13)
        for program in self.library._programs:
            source = self.library._source(program['source'])
            compile(source, program['source'], 'exec')
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), program['source_sha256'])
            self.assertIn(program['validation'], ('FULL_PLAN_ONLY', 'PHYSICAL_SUCCESS'))
        self.assertEqual(sum(p['validation'] == 'PHYSICAL_SUCCESS' for p in self.library._programs), 4)

    def test_tampered_source_is_rejected_before_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'catalog'
            shutil.copytree(self.library.root, root)
            source = root / self.library._programs[0]['source']
            source.write_text(source.read_text() + '\n# changed\n')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                PublishedSkillLibrary(root)

    def test_published_caveats_keep_failure_and_provisional_statuses(self):
        knowledge = self.library.retrieve('chip contact height collision grasp transport', limit=13)
        caveats = {p['id']: p for p in knowledge['failure_findings']}
        self.assertIn('failure', caveats['unresolved-contact-height']['statuses'])
        self.assertIn('provisional', caveats['bounded-chip-clearance']['statuses'])
        self.assertTrue(all(p['limits'] and p['scope'] for p in knowledge['entries']))

    def test_current_local_learning_overrides_same_published_pattern(self):
        old = {'id': 'a', 'status': 'provisional'}
        current = {'id': 'a', 'status': 'failure'}
        merged = merge_topic_knowledge({'entries': [current]}, {'entries': [old, {'id': 'b'}]})
        self.assertEqual(merged['entries'], [current, {'id': 'b'}])

    def test_configured_policy_can_retrieve_without_previous_local_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = AspireCodexPolicy(harness=Mock(), calibration={}, robot_id='fixture',
                directory=directory, instructions='fixture', skill_directory=Path(directory)/'new-programs',
                task='Pick up the green block and place it on the blue poker chip.',
                published_skills=self.library)
            self.assertTrue(policy._skills())
            self.assertTrue(policy._topic_knowledge()['entries'])
            self.assertFalse((Path(directory)/'new-programs').exists())

    def test_published_executable_preserves_hashed_body_and_binds_new_inputs(self):
        executable = ExecutableSkillLibrary(self.library.root / 'executable/pick_place.json')
        selected = executable.select('Move the red block onto the green towel.')
        self.assertEqual(selected['provenance']['core_source_sha256'], executable.digest)
        self.assertEqual(next(q['query'] for q in selected['response']['queries'] if q['name']=='block'), 'red rectangular block')


if __name__ == '__main__':
    unittest.main()
