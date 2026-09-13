import sqlite3
import pytest
from remote_yam.chat_history import ChatHistory
from remote_yam.chat_alerts import initialize, deliver_one


def test_only_new_messages_and_restart(tmp_path):
    path=tmp_path/'chat.sqlite3'
    chat=ChatHistory(path)
    chat.append('old','Old','Historical')
    initialize(path)
    chat.append('private-session','Name','Hello',at=123)
    initialize(path)
    sent=[]
    def publish(**kwargs):
        sent.append(kwargs)
        return {'MessageId':'receipt'}
    assert deliver_one(path,publish,'topic')
    assert not deliver_one(path,publish,'topic')
    assert len(sent)==1
    assert 'Hello' in sent[0]['Message']
    assert 'private-session' not in sent[0]['Message']
    assert 'Historical' not in sent[0]['Message']


def test_retry_keeps_pending_and_history_pruning_does_not_lose_alerts(tmp_path):
    path=tmp_path/'chat.sqlite3'
    chat=ChatHistory(path)
    initialize(path)
    for i in range(105): chat.append('v','Name',str(i))
    def fail(**kwargs): raise OSError('unavailable')
    with pytest.raises(OSError): deliver_one(path,fail,'topic')
    with pytest.raises(RuntimeError): deliver_one(path,lambda **kw: {},'topic')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==100
        assert db.execute('SELECT COUNT(*) FROM chat_alert_outbox').fetchone()[0]==105
    sent=[]
    def publish(**kwargs):
        sent.append(kwargs)
        return {'MessageId':'receipt'}
    for i in range(105): assert deliver_one(path,publish,'topic')
    assert not deliver_one(path,publish,'topic')
    assert len(sent)==105
