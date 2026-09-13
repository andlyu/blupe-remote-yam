from remote_yam.chat_history import ChatHistory


def test_restart_preserves_messages_and_sequence(tmp_path):
    path=tmp_path/'chat.sqlite3'
    first=ChatHistory(path)
    first.append('visitor', 'Andrew', '<hello>', at=123)
    saved=first.messages()
    restarted=ChatHistory(path)
    assert restarted.messages()==saved
    restarted.append('other','Sam','reply',at=124)
    assert restarted.messages()[-1]['id']>saved[-1]['id']


def test_retains_latest_hundred_across_restart(tmp_path):
    path=tmp_path/'chat.sqlite3'
    history=ChatHistory(path)
    for i in range(105):history.append('v','Name',str(i))
    messages=ChatHistory(path).messages()
    assert len(messages)==100
    assert messages[0]['text']=='5'
    assert messages[-1]['text']=='104'
