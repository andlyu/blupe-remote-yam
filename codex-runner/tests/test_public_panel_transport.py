import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

from remote_yam.aspire_worker import RemoteAspirePolicy
from remote_yam.conversation_publisher import ConversationPublisher
from remote_yam.public_task_progress import MODEL_PREFIX, PREFIX, progress_message, public_text


def wait_for(check):
    deadline = time.monotonic() + 3
    while not check():
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_successive_public_summaries_reach_shared_viewer_before_final_response():
    class API:
        def __init__(self):
            self.payloads = []
        def publish_public_conversation(self, session_id, payload):
            assert session_id == 'fixture-session'
            self.payloads.append(payload)
    api = API()
    publisher = ConversationPublisher(api, 'fixture-session', 'Astra', 'Fixture task',
                                      secrets=('custom-secret',), interval=.01)
    publisher.add('model_request', 'Current observation')
    publisher.add('model_progress', 'Checking the chip.', progress_type='summary', timestamp=100,
                  private_reasoning='PRIVATE_WIRE')
    wait_for(lambda: any('Checking the chip.' in str(p) for p in api.payloads))
    first = api.payloads[-1]
    assert not any(m['role'] == 'assistant' for m in first['messages'])
    publisher.add('model_progress', 'Measuring the grasp.', progress_type='status', timestamp=101)
    publisher.add('model_progress', 'PRIVATE_REASONING', progress_type='raw_reasoning')
    publisher.add('model_error', 'Missing required mask: chip in /var/lib/private/run.py custom-secret', timestamp=102)
    wait_for(lambda: any('Missing required mask' in str(p) for p in api.payloads))
    second = api.payloads[-1]
    publisher.add('model_response', 'done: {"summary":"Task complete."}')
    publisher.close()
    publisher._thread.join(2)
    final = api.payloads[-1]
    assert not publisher._thread.is_alive()
    assert MODEL_PREFIX in str(first['messages']).replace('\\n', '\n')
    assert all(value not in str(api.payloads) for value in ('PRIVATE_WIRE', 'PRIVATE_REASONING',
                                                         '/var/lib/private', 'custom-secret'))
    # Decode the actual publisher payloads with the production browser helper,
    # using the same role conversion as the public conversation API.
    source = Path(__file__).parents[1] / 'static/hosted.js'
    code = r"""
const fs=require('node:fs'), vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8')), source=fs.readFileSync(input.source,'utf8');
const c=vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function publicModelRun('),source.indexOf('function aspireSharedRun(')),c);
const runs=input.payloads.map(payload=>c.publicModelRun({task:'Fixture task',events:payload.messages.map(m=>({
  kind:m.role==='assistant'?'model_response':'model_request',speaker:m.role==='tool'?'Tool':'User',message:m.content
}))}));
process.stdout.write(JSON.stringify(runs.map(r=>r.events)));
"""
    assert shutil.which('node'), 'Browser transport contract requires Node'
    result = subprocess.run(['node', '-e', code], input=json.dumps(dict(source=str(source),
                            payloads=[first, second, final])), text=True, capture_output=True, check=True)
    views = json.loads(result.stdout)
    assert [e['message'] for e in views[0] if e['kind'] == 'model_progress'] == ['Checking the chip.']
    assert [e['message'] for e in views[1] if e['kind'] == 'model_progress'] == ['Checking the chip.', 'Measuring the grasp.']
    assert any(e['kind'] == 'model_error' and 'Missing required mask: chip' in e['message'] for e in views[1])
    assert not any(e['kind'] == 'model_response' for e in views[1])
    assert any(e['kind'] == 'model_response' and 'Task complete.' in e['message'] for e in views[2])


def test_hosted_owner_progress_uses_same_public_projection_as_spectators():
    policy = RemoteAspirePolicy.__new__(RemoteAspirePolicy)
    policy._state_lock = threading.RLock()
    policy._config = {'provider':'codex', 'task_progress': {'task':'Task', 'updates':[],
        'lineage': {'program': {'source_code':'PRIVATE CODE'}, 'used':[], 'retrieved':[], 'new_skills':[]},
        'attempts':[{'id':'repair-1','diagnosis':'Alternative chip descriptions.',
            'result': {'reason':'RuntimeError: Missing required mask: chip in /home/private/plan.py'}}]}}
    config = policy.public_config()
    assert 'PRIVATE CODE' not in str(config) and '/home/private' not in str(config)
    assert config['task_progress']['attempts'][0]['diagnosis'] == 'Alternative chip descriptions.'
    assert 'Missing required mask: chip' in config['task_progress']['attempts'][0]['result']['reason']
    assert 'PRIVATE CODE' in str(policy._config)  # Internal state remains untouched.


def test_successive_finite_stage_elapsed_values_survive_projection():
    for elapsed in (12.4, 13.7):
        message = progress_message({'task':'Task','updates':[],
            'stage':{'stage':'planning','active':True,'elapsed_s':elapsed}}, public_text)
        assert json.loads(message[len(PREFIX):])['task_progress']['stage']['elapsed_s'] == elapsed
    message = progress_message({'stage':{'elapsed_s':float('inf')}}, public_text)
    assert 'elapsed_s' not in json.loads(message[len(PREFIX):])['task_progress']['stage']
