import json
import sqlite3
from remote_yam.submission_log import SubmissionLog, LoggedSessionAPI


def test_submission_retained_when_event_stream_fails(tmp_path):
    class API:
        def create_session(self, *args, **kw):
            return {'session_id':'s1','session_capability':'SECRET','status':'queued'}
        def open_events(self, sid):
            raise ConnectionError('sensitive URL')
    path=tmp_path/'queue.db'
    api=LoggedSessionAPI(API(), SubmissionLog(path))
    api.setup({'runner_name':'Person','email':'person@example.com','provider':'openai',
               'model':'astra','prompt':'Stack blocks','run_duration_s':60,'api_key':'SECRET'})
    api.create_session('Stack blocks',60)
    import pytest
    with pytest.raises(ConnectionError):
        api.open_events('s1')
    with sqlite3.connect(path) as db:
        rows=db.execute('select event,session_id,details from submissions order by id').fetchall()
    assert [r[0] for r in rows]==['submitted','queue_request','queued','event_stream_failed']
    assert rows[2][1]=='s1'
    assert json.loads(rows[0][2])['fields']['email']=='person@example.com'
    assert 'SECRET' not in str(rows) and 'sensitive URL' not in str(rows)


def test_web_identity_reaches_http_creation_and_resets_between_submissions(tmp_path):
    from unittest.mock import Mock
    from remote_yam.session import HttpSessionAPI
    transport = HttpSessionAPI('https://api.example.test', websocket_factory=Mock())
    transport._request = Mock(return_value={'session_id': 's1', 'session_capability': 'capability'})
    api = LoggedSessionAPI(transport, SubmissionLog(tmp_path / 'queue.db'))
    api.setup({'runner_name': 'Alex Example', 'email': 'alex@example.com',
               'api_key': 'PRIVATE', 'provider': 'openai', 'model': 'private-model'})
    api.create_session('Stack blocks', 60)
    transport._request.assert_called_once_with('POST', '/v1/sessions', {
        'schema_version': 1, 'robot_id': 'yam-1', 'prompt': 'Stack blocks', 'run_duration_s': 60,
        'runner_name': 'Alex Example', 'email': 'alex@example.com'})
    # An absent identity on a later submission must not reuse earlier contact details.
    api.setup({'prompt': 'Next task'})
    api.create_session('Next task')
    assert transport._request.call_args.args[2] == {
        'schema_version': 1, 'robot_id': 'yam-1', 'prompt': 'Next task', 'run_duration_s': 300}


def test_mock_creation_keeps_contact_fields_out_of_public_state():
    from remote_yam.session import MockSessionAPI
    api = MockSessionAPI(auto_activate=False)
    created = api.create_session('Stack blocks', runner_name='Alex', email='alex@example.com')
    assert api.create_requests[0]['email'] == 'alex@example.com'
    assert 'alex@example.com' not in str([created, api.get_queue_snapshot(), api.get_session(created['session_id'])])
