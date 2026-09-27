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


    def test_failure_forwards_only_allowlisted_diagnostic_and_still_stops(self):
        from types import SimpleNamespace
        from remote_yam.run_errors import public_run_error
        message = 'Planned path error: 0.168 m > 0.150 m maximum (carry).'
        for error, expected in ((message,message), (message+' private-key','Hosted policy stopped: execution_failed'),
                                ({'token':'private-key'},'Hosted policy stopped: execution_failed')):
            p=self.provider(); calls=[]
            def request(path, payload=None):
                calls.append(path)
                return dict(status='failed', stage='error', error=error, error_code='private-key')
            p._request=request
            with self.assertRaises(RuntimeError) as raised:
                p.run_session('task',{},SimpleNamespace(_capability=lambda sid:'lease-secret'),
                    dict(session_id='s'),lambda:False)
            self.assertEqual(str(raised.exception), expected)
            self.assertTrue(calls[-1].endswith('/stop'))
            self.assertNotIn('private-key', str(raised.exception))
            if error == message:
                self.assertEqual(public_run_error({'error':'RuntimeError: '+str(raised.exception)}), message)
