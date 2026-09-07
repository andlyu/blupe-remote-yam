import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from remote_yam.providers import OpenAIAdapter


class ProviderErrorTests(unittest.TestCase):
    def test_error_retains_status_code_and_redacts_key(self):
        key = 'sk-test-secret'
        body = json.dumps({'error': {'code': 'invalid_api_key', 'message': f'Incorrect key {key} and sk-other***key'}}).encode()
        exc = HTTPError('https://api.openai.com/v1/responses', 401, 'Unauthorized', {}, io.BytesIO(body))
        with patch('remote_yam.providers.request.urlopen', side_effect=exc):
            with self.assertRaises(RuntimeError) as raised:
                OpenAIAdapter(key, 'astra')._post_json({'model': 'astra'})
        message = str(raised.exception)
        self.assertIn('HTTP 401', message)
        self.assertIn('invalid_api_key', message)
        self.assertNotIn(key, message)
        self.assertNotIn('sk-other', message)

    def test_non_json_error_keeps_status_without_raw_body(self):
        exc = HTTPError('https://api.openai.com/v1/responses', 502, 'Bad Gateway', {}, io.BytesIO(b'<html>proxy error</html>'))
        with patch('remote_yam.providers.request.urlopen', side_effect=exc):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 502'):
                OpenAIAdapter('test', 'astra')._post_json({})
