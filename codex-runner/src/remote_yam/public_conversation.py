"""Explicit public projection of model messages; never publish wire payloads."""
import json
import re


def request_images(content, recorder):
    if recorder is None:
        return []
    images = []
    name = 'Camera'
    for part in content:
        if part.get('type') == 'input_text':
            match = re.search(r"camera '(top|overhead|front|left|right)_cam'", part.get('text', ''))
            if match:
                name = match.group(1)
        elif part.get('type') == 'input_image':
            packed = recorder._pack(part['image_url'])
            digest = packed.rsplit('$blob:', 1)[-1]
            if re.fullmatch('[a-f0-9]{64}', digest):
                images.append({'name': name, 'digest': digest})
    return images


def response_text(raw):
    text = []
    output = raw.get('output') if isinstance(raw, dict) else None
    if not isinstance(output, list):
        return ''
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'message':
            content = item.get('content')
            if isinstance(content, list):
                text.extend(part['text'] for part in content
                            if isinstance(part, dict) and part.get('type') == 'output_text'
                            and isinstance(part.get('text'), str))
        elif item.get('type') == 'function_call':
            text.append(f"{item.get('name', 'Tool call')}: {item.get('arguments', '')}")
    return '\n\n'.join(text)


def project_events(events, run_id):
    output = []
    for event in events:
        if event.get('kind') not in {'model_request', 'model_response', 'model_progress'}:
            continue
        details = event.get('details') or {}
        message = (details.get('request_text') or details.get('observation') or details.get('prompt')) if event['kind'] == 'model_request' else details.get('response')
        row = {key: event.get(key) for key in ('id', 'timestamp', 'kind')}
        row['message'] = message or event.get('message', '')
        if event['kind'] == 'model_request' and details.get('request_display'):
            row['request'] = details['request_display']
        if event['kind'] == 'model_progress':
            category = details.get('progress_type')
            if category not in {'summary', 'status'}:
                continue
            row['message'] = str(event.get('message', ''))[:4000]
            row['progress_type'] = category
        row['images'] = []
        for image in details.get('images', []) if event['kind'] == 'model_request' else []:
            digest = image.get('digest', '')
            if re.fullmatch('[a-f0-9]{64}', digest) and image.get('name') in {'top', 'overhead', 'front', 'left', 'right'}:
                row['images'].append({'name': image['name'], 'url': f'/api/public-images/{run_id}/{digest}'})
        output.append(row)
    return output


def request_display(payload, recorder):
    """Copy actual outbound fields; replace image bytes with recorded blob refs.

    Opaque encrypted reasoning is not conversation text and is excluded here.
    The original payload and exact private wire recording remain untouched.
    """
    import copy
    visible = copy.deepcopy(payload)
    visible['input'] = [item for item in visible.get('input', []) if item.get('type') != 'reasoning']
    if recorder is None:
        return None
    return recorder._pack(visible)


def request_text(payload):
    sections = []
    for item in payload.get('input', []):
        if item.get('role') not in {'system', 'developer', 'user'}:
            continue
        content = item.get('content', '')
        if isinstance(content, list):
            content = '\n'.join(part.get('text', '') for part in content if part.get('type') == 'input_text')
        sections.append(item['role'].capitalize() + ':\n' + str(content))
    return '\n\n'.join(sections)
