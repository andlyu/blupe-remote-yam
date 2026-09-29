import hashlib
import unittest
from unittest.mock import patch

from remote_yam.providers import PolicyComplete
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.robocurve_prompts import (
    DEFAULT_PROMPT_VERSION, ORIGINAL_ROBOCURVE_PROMPT, WITH_HASTE_PROMPT,
    prompt_identity, prompt_text,
)
from remote_yam.run_changes import snapshot
from test_robocurve_policy import Cameras, observation, response


class PromptVersionTests(unittest.TestCase):
    def test_preserved_prompts_match_audited_bytes(self):
        for text, expected in [
            (ORIGINAL_ROBOCURVE_PROMPT, 'cc3f1dade16066840ec62f5709286468f0b217f985290748a2abc6b9403e559e'),
            (WITH_HASTE_PROMPT, '724dba416a8a722f5a52dfe5f125a34e8eed5c4ebb4d7e5e74aed5a56559d559'),
        ]:
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), expected)
        self.assertEqual(DEFAULT_PROMPT_VERSION, 'with-haste')

    def test_selected_text_reaches_request_and_run_record_unchanged(self):
        for version in ('original-robocurve', 'with-haste'):
            provider = OpenAIAdapter('test-key', 'gpt-6-astra', camera_source=Cameras())
            provider.set_prompt_version(version)
            with patch.object(provider, '_post_json', return_value=response('done', {'summary': 'Done', 'hindsight': 'none'})) as post:
                with self.assertRaises(PolicyComplete):
                    provider.build_trajectory('Place the block', observation(), 0)
            text = post.call_args.args[0]['input'][0]['content']
            self.assertEqual(text, prompt_text(version))
            record = snapshot(provider, 'Place the block', 300)
            self.assertEqual(record['system_prompt'], text)
            self.assertEqual(record['prompt_version'], version)
            self.assertEqual(record['system_prompt_sha256'], hashlib.sha256(text.encode()).hexdigest())
            with self.assertRaisesRegex(ValueError, 'before starting'):
                provider.set_prompt_version('with-haste')

    def test_invalid_version_rejected_and_custom_text_not_mislabeled(self):
        for value in ('original', '', None, []):
            with self.assertRaises(ValueError):
                prompt_text(value)
        self.assertIsNone(prompt_identity(ORIGINAL_ROBOCURVE_PROMPT + '\nExtra instructions')['prompt_version'])
