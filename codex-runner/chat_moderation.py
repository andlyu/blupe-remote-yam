"""Read-only chat moderation proxy; preserves the running BYOK process and sessions.

Only Caddy's GET /api/chat route uses this process. Deletion records are managed
through SSH, never by a public endpoint. The original server still authenticates
every request. A restart of the original server clears its in-memory chat.
"""
import asyncio
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path


def message_key(message):
    return {
        'id': message['id'], 'at': message['at'],
        'sha256': hashlib.sha256(message['text'].encode()).hexdigest(),
    }


def remove_messages(payload, removals):
    return {**payload, 'messages': [message for message in payload['messages']
                                  if message_key(message) not in removals]}


def read_chat(headers):
    # Preserve the original Host, cookie, and browser-origin checks. This cannot
    # proxy robot actions, create sessions, post chat, or select an upstream URL.
    allowed = {'host', 'cookie', 'origin', 'sec-fetch-site'}
    forwarded = {key.decode().lower(): value.decode('latin1') for key, value in headers
                 if key.decode().lower() in allowed}
    forwarded['accept-encoding'] = 'identity'
    connection = HTTPConnection('127.0.0.1', 8790, timeout=5)
    try:
        connection.request('GET', '/api/chat', headers=forwarded)
        response = connection.getresponse()
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError('Chat response too large')
        if response.status == 200:
            removals = json.loads(Path(os.environ['YAM_CHAT_REMOVALS']).read_text())
            if not isinstance(removals, list):
                raise ValueError('Invalid moderation records')
            body = json.dumps(remove_messages(json.loads(body), removals)).encode()
        return response.status, body
    finally:
        connection.close()


async def app(scope, receive, send):
    if scope['type'] != 'http':
        return
    if scope['method'] != 'GET' or scope['path'] != '/api/chat':
        status, body = 404, b'{"error":"Not found"}'
    else:
        try:
            status, body = await asyncio.to_thread(read_chat, scope.get('headers', []))
        except Exception:
            # Do not leak a removed message when the moderation file is unavailable.
            status, body = 503, b'{"error":"Chat temporarily unavailable"}'
    await send({'type': 'http.response.start', 'status': status, 'headers': [
        (b'content-type', b'application/json'), (b'cache-control', b'no-store'),
        (b'x-content-type-options', b'nosniff'),
        (b'content-length', str(len(body)).encode()),
    ]})
    await send({'type': 'http.response.body', 'body': body})
