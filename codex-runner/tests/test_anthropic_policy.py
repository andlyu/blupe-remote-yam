import io
import json
from unittest.mock import patch
from urllib.error import HTTPError
import pytest
from remote_yam.anthropic_policy import AnthropicAdapter, messages_payload, responses_output


def payload():
    return {'model':'claude-opus-5-5','tools':[{'name':'done','description':'Finish','parameters':{'type':'object'}}],
            'input':[{'role':'system','content':'Be precise'}, {'role':'user','content':[
                {'type':'input_text','text':'Move block'},
                {'type':'input_image','image_url':'data:image/jpeg;base64,YWJj'}]}]}


def test_images_and_tool_feedback_round_trip():
    p=payload()
    raw={'stop_reason':'tool_use','content':[{'type':'text','text':'Done'},
          {'type':'tool_use','id':'toolu_1','name':'done','input':{'summary':'Done','hindsight':'OK'}}]}
    p['input'] += responses_output(raw)['output'] + [
        {'type':'function_call_output','call_id':'toolu_1','output':'{"ok":true}'},
        {'role':'user','content':[{'type':'input_text','text':'Next observation'}]}]
    wire=messages_payload(p)
    assert wire['system']=='Be precise'
    assert [m['role'] for m in wire['messages']]==['user','assistant','user']
    assert wire['messages'][0]['content'][1]['source']=={'type':'base64','media_type':'image/jpeg','data':'YWJj'}
    assert wire['messages'][1]['content'][1]['id']=='toolu_1'
    assert wire['messages'][2]['content'][0]['tool_use_id']=='toolu_1'
    assert wire['messages'][2]['content'][1]['text']=='Next observation'
    assert wire['tool_choice']['disable_parallel_tool_use'] is True
    assert wire['tools'][0]['input_schema']=={'type':'object'}


@pytest.mark.parametrize('reason',['max_tokens','refusal','pause_turn',None])
def test_incomplete_responses_never_become_motion(reason):
    with pytest.raises(RuntimeError,match='incomplete'):
        responses_output({'stop_reason':reason,'content':[{'type':'tool_use','id':'x','name':'move_to','input':{}}]})


def test_fixed_endpoint_headers_and_response_conversion():
    adapter=AnthropicAdapter('private-key')
    calls=[]
    def send(req):
        calls.append(req)
        return io.BytesIO(json.dumps({'stop_reason':'tool_use','content':[
            {'type':'tool_use','id':'toolu_1','name':'done','input':{'summary':'OK','hindsight':'OK'}}]}).encode())
    adapter._urlopen=lambda req,timeout: send(req)
    result=adapter._post_json(payload())
    req=calls[0]
    assert req.full_url=='https://api.anthropic.com/v1/messages'
    assert req.get_header('Anthropic-version')=='2023-06-01'
    assert req.get_header('Authorization')=='Bearer private-key'
    assert result['output'][0]['call_id']=='toolu_1'
    assert 'private-key' not in repr(adapter)
    adapter.cancelled=lambda:True
    with pytest.raises(RuntimeError,match='cancelled'): adapter._post_json(payload())
    assert len(calls)==1


def test_failed_request_redacts_key_after_revocation():
    adapter=AnthropicAdapter('private-key')
    def send(req,timeout):
        adapter._api_key=''
        raise HTTPError(req.full_url,401,'Unauthorized',{},io.BytesIO(b'{"error":{"message":"bad private-key"}}'))
    adapter._urlopen=send
    with pytest.raises(RuntimeError) as exc: adapter._post_json(payload())
    assert 'private-key' not in str(exc.value)
    assert '[REDACTED]' in str(exc.value)
