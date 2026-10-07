import threading
import time
import json
from remote_yam.conversation_publisher import ConversationPublisher
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI, HttpSessionAPI
from remote_yam.providers import ScriptedAdapter
from remote_yam.public_task_progress import PREFIX


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


def shared_progress(api):
    for message in api.shared[-1][1]['messages'] if api.shared else []:
        if message['role'] == 'tool' and message['content'].startswith(PREFIX):
            return json.loads(message['content'][len(PREFIX):])['task_progress']
    return {}


def test_native_status_bootstraps_updates_and_flushes_terminal_evidence_without_model_calls():
    api = PublishingAPI()
    progress = {'task': 'Place green block', 'updates': [
        {'happened': 'Program found.', 'changed': 'Saved source.', 'next_action': 'Capture.'}],
        'lineage': {'usage_recorded': True, 'authorship': {'generated_by_codex': True, 'mode': 'reuse'},
                    'source': 'PRIVATE CODE', 'used': [{'id': 'pick_place', 'source_refs': [{'source': '/private/secret'}]}]},
        'wire_payload': 'PRIVATE WIRE', 'stage': {'stage': 'capture', 'active': True}}
    p = ConversationPublisher(api, 'native', 'ASPIRE', progress['task'],
        task_progress=progress, progress_source=lambda: progress, interval=.01)
    wait_for(lambda: shared_progress(api).get('stage', {}).get('stage') == 'capture')
    progress['stage'] = {'stage': 'segmentation', 'active': True, 'started_at': 100}
    wait_for(lambda: shared_progress(api).get('stage', {}).get('stage') == 'segmentation')
    # Close while the publisher is asleep/waiting: final review must still flush.
    progress['outcome'] = {'status': 'UNVERIFIED', 'success': False, 'reason': 'After parking did not confirm.'}
    progress['resolution'] = {'state': 'unresolved', 'title': 'Retry limit reached 2 of 2', 'retry_limit': 2}
    p.close(); p._thread.join(2)
    assert not p._thread.is_alive()
    final = shared_progress(api)
    assert final['outcome']['success'] is False
    assert final['resolution']['title'] == 'Retry limit reached 2 of 2'
    assert final['lineage']['authorship']['mode'] == 'reuse'
    assert 'PRIVATE' not in str(api.shared) and '/private/secret' not in str(api.shared)
    assert all(m['role'] != 'assistant' for m in api.shared[-1][1]['messages'])


def test_native_snapshot_is_valid_bounded_redacted_and_retained_with_long_model_history():
    api = PublishingAPI()
    progress = {'task': 'Task', 'updates': [dict(happened='x'*2000, changed='custom-secret '*400,
        next_action='x'*2000) for _ in range(90)],
        'outcome': {'status': 'UNVERIFIED', 'reason': 'custom-secret sk-secret Bearer token'}}
    p = ConversationPublisher(api, 'native', 'ASPIRE', 'Task', secrets=('custom-secret',),
        task_progress=progress, interval=.01)
    for _ in range(90): p.add('model_response', 'x'*8000)
    p.close(); p._thread.join(2)
    messages = api.shared[-1][1]['messages']
    assert len(messages) <= 64 and sum(len(m['content']) for m in messages) <= 64000
    assert all(len(m['content']) <= 8000 for m in messages)
    assert shared_progress(api)['outcome']['status'] == 'UNVERIFIED'
    assert 'custom-secret' not in str(messages) and 'Bearer token' not in str(messages)
    assert sum(m['role'] == 'tool' for m in messages) == 1


def test_final_retry_resolution_written_after_controller_exit_is_shared():
    api = PublishingAPI(); progress = {'task': 'Task', 'updates': [],
        'outcome': {'status': 'UNVERIFIED', 'success': False}}
    p = ConversationPublisher(api, 'native', 'ASPIRE', 'Task',
        task_progress=progress, progress_source=lambda: progress, interval=.05)
    wait_for(lambda: bool(shared_progress(api)))
    p.close()
    time.sleep(.06)  # Recovery-manager tick occurs after the controller exits.
    progress['resolution'] = {'state': 'unresolved', 'title': 'Retry limit reached 2 of 2', 'retry_count': 2}
    p._thread.join(2)
    assert shared_progress(api)['resolution']['retry_count'] == 2


def test_stop_remains_immediate_and_sharing_covers_native_after_parking_review():
    entered, release = threading.Event(), threading.Event()
    class Native(ScriptedAdapter):
        interaction_sink = None
        def __init__(self):
            super().__init__([])
            self.progress = {'task': 'Native fixture', 'updates': []}
        def public_config(self):
            return {**super().public_config(), 'task_progress': self.progress}
        def review_after_session_stop(self):
            entered.set(); release.wait(3)
            self.progress['outcome'] = {'status': 'UNVERIFIED', 'success': False, 'reason': 'Parked checks failed.'}
    api = PublishingAPI(); provider = Native(); c = RunnerController(api)
    c.join(provider, 'Native fixture')
    thread = threading.Thread(target=c._run_loop, args=(None,)); thread.start()
    wait_for(lambda: getattr(c, '_conversation_review_publisher', None) is c._conversation_publisher)
    start = time.monotonic(); c.stop(); assert time.monotonic()-start < .2
    assert entered.wait(2)
    assert c._conversation_publisher._thread.is_alive()
    release.set(); thread.join(2); c._conversation_publisher._thread.join(2)
    assert shared_progress(api)['outcome']['status'] == 'UNVERIFIED'


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
