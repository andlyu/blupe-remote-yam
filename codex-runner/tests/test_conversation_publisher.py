import threading
import time
from remote_yam.conversation_publisher import ConversationPublisher
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI, HttpSessionAPI
from remote_yam.providers import ScriptedAdapter


def wait_for(check):
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline
        time.sleep(.01)


class PublishingAPI(MockSessionAPI):
    def __init__(self):
        super().__init__(auto_activate=False)
        self.shared = []
    def publish_public_conversation(self, session_id, payload):
        self.shared.append((session_id, payload))


def test_controller_streams_prompt_and_new_messages_and_flushes_stop():
    api = PublishingAPI()
    provider = ScriptedAdapter([])
    provider.interaction_sink = None
    controller = RunnerController(api)
    controller.join(provider, 'Stack the block')
    provider.interaction_sink('model_request', 'waiting', observation='The block is on the table', request_display={'private': 'omit'})
    provider.interaction_sink('model_response', 'done', response='move: {"note":"Pick up the block"}')
    controller.stop()
    wait_for(lambda: api.shared and len(api.shared[-1][1]['messages']) == 3)
    assert api.shared[-1][1]['messages'][-1]['role'] == 'assistant'
    assert api.shared[-1][0] == controller._session_id
    assert 'omit' not in str(api.shared)
    controller._conversation_publisher._thread.join(2)
    assert not controller._conversation_publisher._thread.is_alive()


def test_opt_out_sends_nothing():
    api = PublishingAPI(); provider = ScriptedAdapter([]); provider.interaction_sink = None
    c = RunnerController(api, share_conversation=False)
    c.join(provider, 'Private task')
    provider.interaction_sink('model_response', 'Private answer', response='Private answer')
    c.stop()
    assert not api.shared
    assert c.status()['conversation_sharing'] == {'enabled': False}
    assert c._interactions.snapshot()['events'][-1]


def test_slow_upload_does_not_block_sink_or_close_and_redacts_and_bounds():
    entered, release = threading.Event(), threading.Event()
    class Slow(PublishingAPI):
        def publish_public_conversation(self, session_id, payload):
            entered.set(); release.wait(3)
            return super().publish_public_conversation(session_id, payload)
    api = Slow(); p = ConversationPublisher(api, 'session', 'Claude', 'Task', secrets=('custom-secret',), interval=.01)
    assert entered.wait(1)
    start = time.monotonic()
    for _ in range(90):
        p.add('model_response', 'response', response='custom-secret sk-ant-secret Bearer secret ' + 'x'*10000)
    p.close()
    assert time.monotonic() - start < .5
    release.set(); p._thread.join(2)
    messages = api.shared[-1][1]['messages']
    assert len(messages) <= 64 and sum(len(m['content']) for m in messages) <= 64000
    assert all(len(m['content']) <= 8000 for m in messages)
    assert messages[0]['content'] == 'Task'
    assert 'custom-secret' not in str(messages) and 'sk-ant-secret' not in str(messages)
    assert 'Bearer secret' not in str(messages)


def test_failed_upload_retries_final_snapshot_without_leaking_exception():
    class Flaky(PublishingAPI):
        attempts = 0
        def publish_public_conversation(self, session_id, payload):
            self.attempts += 1
            if self.attempts == 1: raise RuntimeError('SECRET transport data')
            super().publish_public_conversation(session_id, payload)
    api = Flaky(); p = ConversationPublisher(api, 'session', 'Model', 'Task', interval=.01)
    p.add('model_response', 'answer');p.close();p._thread.join(2)
    assert api.attempts >= 2 and api.shared[-1][1]['messages'][-1]['content'] == 'answer'
    assert p.status()['state'] == 'shared'


def test_transport_uses_only_session_capability_and_short_timeout(monkeypatch):
    import json
    from io import BytesIO
    from urllib import request
    calls = []
    class Opener:
        def open(self, req, timeout):
            calls.append((req, timeout));return BytesIO(b'{"shared":true}')
    handlers = []
    monkeypatch.setattr(request, 'build_opener', lambda handler: (handlers.append(handler) or Opener()))
    api = HttpSessionAPI('https://session.example')
    api._session_capabilities['own/session'] = 'session-capability'
    api.publish_public_conversation('own/session', {'model':'Model','messages':[]})
    req, timeout = calls[0]
    assert req.full_url == 'https://session.example/v1/sessions/own%2Fsession/public-conversation'
    assert req.get_header('Authorization') == 'Bearer session-capability'
    assert json.loads(req.data) == {'public_conversation': {'model':'Model','messages':[]}}
    assert timeout == 2 and api.contact_issue is None
    assert handlers[0]().redirect_request(None,None,302,None,None,'https://evil.example') is None
