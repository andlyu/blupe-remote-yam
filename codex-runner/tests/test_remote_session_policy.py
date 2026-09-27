import unittest
from remote_yam.remote_session_policy import RemoteSessionPolicy


class PolicyTests(unittest.TestCase):
    def provider(self):
        return RemoteSessionPolicy('http://127.0.0.1:8798', 'secret'*8,
            provider_name='test', display_name='Private policy', model='right', robot_id='yam-1')

    def test_private_context_never_enters_public_configuration(self):
        p = self.provider()
        self.assertNotIn('secret', str(p.public_config()))
        self.assertNotIn('127.0.0.1', str(p.public_config()))
        self.assertFalse(p.share_conversation)

    def test_complete_run_and_cancel_on_lost_start_response(self):
        from types import SimpleNamespace
        api = SimpleNamespace(_capability=lambda sid:'lease-secret')
        context = dict(session_id='s', episode_id='e', lease_id='l', robot_id='yam-1')
        for fail in (False, True):
            p=self.provider(); calls=[]
            def request(path, payload=None):
                calls.append((path,payload))
                if path == '/runs' and fail: raise RuntimeError('Unavailable')
                return {'status':'completed', 'stage':'complete'}
            p._request=request
            if fail:
                with self.assertRaises(RuntimeError): p.run_session('task',{},api,context,lambda:False)
            else: p.run_session('task',{},api,context,lambda:False)
            self.assertTrue(calls[-1][0].endswith('/stop'))
            self.assertEqual(calls[0][1]['session_capability'],'lease-secret')

    def test_cancelled_session_does_not_start(self):
        p=self.provider(); calls=[]
        p._request=lambda path, body=None:calls.append(path)
        p.run_session('task',{},None,{},lambda:True)
        self.assertNotIn('/runs',calls)
