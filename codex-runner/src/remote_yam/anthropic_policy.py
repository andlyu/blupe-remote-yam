"""Claude Messages transport for the existing validated YAM trajectory policy.

Wire reference: docs/refs/anthropic/{messages,tools,models}.md.
No SDK, custom endpoints, or subscription credentials are used.
"""
import json

from .robocurve_policy import RoboCurveResponsesAdapter

DEFAULT_MODEL = 'claude-opus-5-5'


def messages_payload(payload):
    messages, system = [], []
    def append(role, content):
        if messages and messages[-1]['role'] == role:
            messages[-1]['content'].extend(content)
        else:
            messages.append({'role': role, 'content': content})
    for item in payload['input']:
        kind = item.get('type')
        if kind == 'function_call':
            append('assistant', [{'type': 'tool_use', 'id': item['call_id'],
                                 'name': item['name'], 'input': json.loads(item['arguments'])}])
        elif kind == 'function_call_output':
            append('user', [{'type': 'tool_result', 'tool_use_id': item['call_id'],
                            'content': item['output']}])
        elif item.get('role') == 'system':
            system.append(item['content'])
        elif item.get('role') in {'user', 'assistant'}:
            content = item['content']
            if isinstance(content, str):
                blocks = [{'type': 'text', 'text': content}]
            else:
                blocks = []
                for block in content:
                    if block['type'] in {'input_text', 'output_text'}:
                        blocks.append({'type': 'text', 'text': block['text']})
                    elif block['type'] == 'input_image':
                        prefix, data = block['image_url'].split(';base64,', 1)
                        if prefix not in {'data:image/jpeg', 'data:image/png'}:
                            raise ValueError('Claude requires an embedded camera image')
                        blocks.append({'type': 'image', 'source': {'type': 'base64',
                                      'media_type': prefix[5:], 'data': data}})
                    else:
                        raise ValueError('Unsupported Claude conversation content')
            append(item['role'], blocks)
        else:
            raise ValueError('Unsupported Claude conversation item')
    return {'model': payload['model'], 'max_tokens': 4096, 'system': '\n\n'.join(system),
            'messages': messages,
            'tools': [{'name': tool['name'], 'description': tool['description'],
                       'input_schema': tool['parameters']} for tool in payload['tools']],
            'tool_choice': {'type': 'any', 'disable_parallel_tool_use': True}}


def responses_output(response):
    if response.get('stop_reason') not in {'tool_use', 'end_turn'}:
        raise RuntimeError('Claude response incomplete; no motion submitted')
    if not isinstance(response.get('content'), list):
        raise RuntimeError('Claude returned malformed content')
    output = []
    for block in response['content']:
        if block.get('type') == 'tool_use':
            if not isinstance(block.get('input'), dict):
                raise RuntimeError('Claude tool input must be an object')
            output.append({'type': 'function_call', 'call_id': block['id'],
                           'name': block['name'], 'arguments': json.dumps(block['input'])})
        elif block.get('type') == 'text':
            output.append({'type': 'message', 'role': 'assistant',
                           'content': [{'type': 'output_text', 'text': block['text']}]})
        else:
            raise RuntimeError('Claude returned unsupported content')
    return {'output': output}


class AnthropicAdapter(RoboCurveResponsesAdapter):
    provider_name = 'anthropic'

    def __init__(self, api_key, model=DEFAULT_MODEL, **kwargs):
        super().__init__(api_key, model, 'https://api.anthropic.com/v1/messages', **kwargs)

    def _post_json(self, payload):
        return responses_output(super()._post_json(messages_payload(payload)))

    def _send_json(self, req):
        req.add_header('anthropic-version', '2023-06-01')
        # The documented Bearer authentication also preserves the shared
        # request-scoped key redaction when a session revokes an in-flight key.
        return super()._send_json(req)
