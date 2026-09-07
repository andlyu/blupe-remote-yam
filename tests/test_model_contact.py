import io
import json
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from remote_yam.providers import OpenAIAdapter
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI


class ModelContactTests(unittest.TestCase):
    def test_recovery_keeps_same_prompt_and_shows_status(self):
        provider = OpenAIAdapter('private-key', 'gpt-6-astra')
        runner = RunnerController(MockSessionAPI())
        runner._provider = provider
        states = []
        payload = {'input': [{'role': 'user', 'content': 'same prompt and images'}]}
        with patch('remote_yam.providers.request.urlopen', side_effect=[
            ssl.SSLError('private-key'), io.BytesIO(b'{"output":[]}')
        ]) as send, patch.object(provider, '_wait_before_retry', side_effect=lambda _: states.append(runner.status()['server_contact_issue'])):
            self.assertEqual(provider._post_json(payload), {'output': []})
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[0].data, send.call_args_list[1].args[0].data)
        self.assertEqual(json.loads(send.call_args.args[0].data), payload)
        self.assertEqual(states[0]['state'], 'retrying')
        self.assertNotIn('private-key', str(states))
        self.assertIsNone(runner.status()['server_contact_issue'])

    def test_exhaustion_stops_at_three(self):
        provider = OpenAIAdapter('key', 'gpt-6-astra')
        with patch('remote_yam.providers.request.urlopen', side_effect=URLError(TimeoutError())) as send, patch.object(provider, '_wait_before_retry'):
            with self.assertRaisesRegex(RuntimeError, 'Server contact issue.*3 attempts'):
                provider._post_json({})
        self.assertEqual(send.call_count, 3)
        self.assertEqual(provider.public_config()['server_contact_issue']['state'], 'failed')

    def test_stop_during_backoff_prevents_next_request(self):
        provider = OpenAIAdapter('key', 'gpt-6-astra')
        def stop(_):
            provider.cancelled = lambda: True
        with patch('remote_yam.providers.request.urlopen', side_effect=ssl.SSLError()) as send, patch('remote_yam.providers.time.sleep', side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                provider._post_json({})
        self.assertEqual(send.call_count, 1)

    def test_late_response_after_stop_is_discarded(self):
        provider = OpenAIAdapter('key', 'gpt-6-astra')
        def late(*args, **kwargs):
            provider.cancelled = lambda: True
            return io.BytesIO(b'{"output":[]}')
        with patch('remote_yam.providers.request.urlopen', side_effect=late):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                provider._post_json({})

    def test_permanent_errors_do_not_retry(self):
        for failure in [URLError(ssl.SSLCertVerificationError()),
                        HTTPError('https://api.test', 401, '', {}, io.BytesIO(b'{}')),
                        HTTPError('https://api.test', 429, '', {}, io.BytesIO(b'{"error":{"code":"credit_balance_exhausted"}}'))]:
            provider = OpenAIAdapter('key', 'gpt-6-astra')
            with self.subTest(error=type(failure).__name__), patch('remote_yam.providers.request.urlopen', side_effect=failure) as send:
                with self.assertRaises(RuntimeError):
                    provider._post_json({})
                self.assertEqual(send.call_count, 1)

    def test_service_unavailable_can_recover(self):
        provider = OpenAIAdapter('key', 'gpt-6-astra')
        with patch('remote_yam.providers.request.urlopen', side_effect=[
            HTTPError('https://api.test', 503, '', {}, io.BytesIO(b'{}')),
            io.BytesIO(b'{}')
        ]) as send, patch.object(provider, '_wait_before_retry'):
            self.assertEqual(provider._post_json({}), {})
        self.assertEqual(send.call_count, 2)
