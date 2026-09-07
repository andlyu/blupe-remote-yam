import io
import json
import ssl
import unittest
from unittest.mock import patch
from urllib.error import URLError, HTTPError

from remote_yam.controller import RunnerController
from remote_yam.session import HttpSessionAPI, SessionAPIError


class Response(io.BytesIO):
    pass


class ServerContactTests(unittest.TestCase):
    def test_handshake_retry_reports_issue_and_clears_after_recovery(self):
        api = HttpSessionAPI('https://api.test')
        runner = RunnerController(api)
        states = []
        with patch('remote_yam.session.request.urlopen', side_effect=[
            URLError(TimeoutError('secret-key must not be logged')),
            Response(b'{"status":"running"}'),
        ]) as send, patch('remote_yam.session.time.sleep', side_effect=lambda _: states.append(runner.status()['server_contact_issue'])):
            result = api._request('GET', '/v1/sessions/s')
        self.assertEqual(result['status'], 'running')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(states[0]['message'], 'Server contact issue')
        self.assertEqual(states[0]['state'], 'retrying')
        self.assertIsNone(runner.status()['server_contact_issue'])
        self.assertNotIn('secret-key', str(states))

    def test_exhaustion_is_bounded_and_preserves_clear_message(self):
        api = HttpSessionAPI('https://api.test')
        with patch('remote_yam.session.request.urlopen', side_effect=URLError(TimeoutError())) as send, patch('remote_yam.session.time.sleep'):
            with self.assertRaisesRegex(SessionAPIError, 'Server contact issue.*3 attempts'):
                api._request('GET', '/v1/sessions/s')
        self.assertEqual(send.call_count, 3)
        self.assertEqual(api.contact_issue['state'], 'failed')
        with patch('remote_yam.session.request.urlopen', return_value=Response(b'{}')):
            api._request('GET', '/v1/sessions/s')
        self.assertIsNone(api.contact_issue)

    def test_packet_retry_preserves_exact_id_body_and_authorization(self):
        api = HttpSessionAPI('https://api.test')
        payload = {'trajectory_id':'same-id','waypoints':[{'step_id':7}]}
        with patch('remote_yam.session.request.urlopen', side_effect=[URLError(TimeoutError()), Response(b'{"duplicate":true}')]) as send, patch('remote_yam.session.time.sleep'):
            result = api._request('POST', '/v1/sessions/s/trajectories', payload, bearer='private-capability')
        self.assertTrue(result['duplicate'])
        first, second = [c.args[0] for c in send.call_args_list]
        self.assertEqual(first.data, second.data)
        self.assertEqual(json.loads(second.data), payload)
        self.assertEqual(first.headers, second.headers)

    def test_ambiguous_create_and_permanent_http_errors_are_not_retried(self):
        api = HttpSessionAPI('https://api.test')
        cases = [
            ('POST', '/v1/sessions', {'prompt':'x'}, URLError(TimeoutError())),
            ('GET', '/v1/sessions/s', None, HTTPError('https://api.test',401,'Unauthorized',{},io.BytesIO(b'{"error":{"message":"Denied"}}'))),
            ('GET', '/v1/sessions/s', None, URLError(ssl.SSLCertVerificationError('invalid certificate'))),
        ]
        for method,path,body,error in cases:
            with self.subTest(method=method,error=type(error).__name__), patch('remote_yam.session.request.urlopen',side_effect=error) as send:
                with self.assertRaises(SessionAPIError): api._request(method,path,body)
                self.assertEqual(send.call_count,1)

    def test_gateway_unavailable_retries_but_invalid_payload_does_not(self):
        api = HttpSessionAPI('https://api.test')
        for status,expected in [(503,3),(422,1)]:
            with self.subTest(status=status), patch('remote_yam.session.request.urlopen', side_effect=lambda *a,**kw: (_ for _ in ()).throw(HTTPError('https://api.test',status,'Error',{},io.BytesIO(b'{}')))) as send, patch('remote_yam.session.time.sleep'):
                with self.assertRaises(SessionAPIError):api._request('GET','/v1/queue')
                self.assertEqual(send.call_count,expected)
