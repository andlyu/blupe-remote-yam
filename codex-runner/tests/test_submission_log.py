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
