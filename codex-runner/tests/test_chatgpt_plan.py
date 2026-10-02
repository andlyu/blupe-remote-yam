import copy
import io
import json
import unittest
from urllib import error

from remote_yam.chatgpt_plan import plan_payload, stream_response, use_chatgpt_plan, ChatGPTPlanError
from remote_yam.providers import OpenAIAdapter


def stream(*events):
    body = ''.join('data: '+json.dumps(event)+'\n\n' for event in events)
    result = io.BytesIO(body.encode())
    result.headers = {'x-request-id':'req_test'}
    return result


class ChatGPTPlanTests(unittest.TestCase):
    def test_wire_preserves_images_tool_ids_and_encrypted_reasoning(self):
        body = {'model':'gpt-6-astra','service_tier':'priority','store':True,
                'max_output_tokens':10,'previous_response_id':'must-not-use',
                'input':[{'role':'system','content':'Robot safety instructions'},
                         {'role':'user','content':[{'type':'input_image','image_url':'data:image/jpeg;base64,YWJj'}]},
                         {'type':'reasoning','encrypted_content':'opaque-reasoning'},
                         {'type':'function_call','name':'move_to','call_id':'call1','arguments':'{}'},
                         {'type':'function_call_output','call_id':'call1','output':'{}'}],
                'tools':[{'type':'function','name':'move_to','parameters':{'type':'object'}}]}
        saved = copy.deepcopy(body)
        result = plan_payload(body)
        self.assertEqual(body,saved)
        self.assertEqual(result['input'][0]['role'],'developer')
        self.assertEqual(result['input'][1:],body['input'][1:])
        self.assertFalse(result['store']); self.assertTrue(result['stream'])
        self.assertEqual(result['tools'][0]['type'],'namespace')
        for key in ('service_tier','max_output_tokens','previous_response_id'):
            self.assertNotIn(key,result)

    def test_partial_stream_and_failed_terminal_never_return_actions(self):
        partial = {'type':'response.output_item.added','item':{'type':'function_call','name':'move_to'}}
        for events in [(partial,), (partial,{'type':'response.incomplete','response':{'status':'incomplete'}}),
                       (partial,{'type':'response.failed','response':{'error':{'code':'subscription_sharing_usage_limit_exceeded'}}})]:
            with self.assertRaises(RuntimeError):
                stream_response(stream(*events),lambda:False)
        result={'status':'completed','output':[{'type':'function_call','name':'done','call_id':'abc'}]}
        self.assertEqual(stream_response(stream(partial,{'type':'response.completed','response':result}),lambda:False),result)

    def test_tokens_renew_per_request_stay_out_of_config_and_cancellation_stops_requests(self):
        provider = OpenAIAdapter('placeholder','gpt-6-astra')
        requests=[]
        tokens=iter(['account-a-token1','account-a-token2'])
        def open_response(req, timeout):
            requests.append(req)
            return stream({'type':'response.completed','response':{'status':'completed','output':[]}})
        use_chatgpt_plan(provider,lambda:next(tokens),urlopen=open_response)
        for _ in range(2):
            provider._post_json({'model':'gpt-6-astra','input':[]})
        self.assertEqual([r.get_header('Authorization') for r in requests],['Bearer account-a-token1','Bearer account-a-token2'])
        self.assertEqual(provider._api_key,'')
        self.assertFalse(provider.public_config()['api_key_configured'])
        provider.cancelled=lambda:True
        with self.assertRaises(RuntimeError): provider._post_json({'input':[]})
        self.assertEqual(len(requests),2)

    def test_usage_limit_is_not_retried_or_billed_with_another_credential(self):
        provider=OpenAIAdapter('never-use-this-key','gpt-6-astra')
        requests=[]
        def denied(req,timeout):
            requests.append(req)
            raise error.HTTPError(req.full_url,429,'quota',{'x-request-id':'req_quota'},
                io.BytesIO(json.dumps({'error':{'code':'subscription_sharing_usage_limit_exceeded','message':'private upstream text'}}).encode()))
        use_chatgpt_plan(provider,lambda:'own-account-token',urlopen=denied)
        with self.assertRaises(ChatGPTPlanError) as caught:
            provider._post_json({'model':'gpt-6-astra','input':[]})
        self.assertEqual(len(requests),1)
        self.assertEqual(caught.exception.request_id,'req_quota')
        self.assertIn('usage limit',str(caught.exception))
        self.assertNotIn('private upstream text',str(caught.exception))

    def test_separate_provider_instances_use_only_their_own_grants(self):
        requests=[]
        def opener(req,timeout):
            requests.append(req.get_header('Authorization'))
            return stream({'type':'response.completed','response':{'status':'completed','output':[]}})
        for token in ['visitor-a','visitor-b']:
            provider=OpenAIAdapter('placeholder','gpt-6-astra')
            use_chatgpt_plan(provider,lambda token=token:token,urlopen=opener)
            provider._post_json({'input':[]})
        self.assertEqual(requests,['Bearer visitor-a','Bearer visitor-b'])
