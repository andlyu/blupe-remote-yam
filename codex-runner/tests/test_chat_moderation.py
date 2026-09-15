import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_moderation as chat


class ChatModerationTests(unittest.IsolatedAsyncioTestCase):
    def test_removal_is_exact_and_preserves_every_other_message_and_visitor(self):
        target = {'id': 12, 'at': 100, 'text': 'Remove this message', 'name': 'Andrew'}
        others = [dict(target, id=11), dict(target, at=200), dict(target, text='Keep this')]
        original = {'messages': [*others, target], 'visitor': 'current-visitor'}
        result = chat.remove_messages(original, [chat.message_key(target)])
        self.assertEqual(result, {'messages': others, 'visitor': 'current-visitor'})
        self.assertEqual(len(original['messages']), 4)

    async def test_proxy_preserves_auth_rejections_and_cannot_forward_actions(self):
        response = MagicMock(status=401)
        response.read.return_value = b'{"error":"Session ended"}'
        connection = MagicMock()
        connection.getresponse.return_value = response
        with patch.object(chat, 'HTTPConnection', return_value=connection):
            status, body = chat.read_chat([(b'host', b'runner.example'), (b'cookie', b'session=test'),
                                           (b'sec-fetch-site', b'cross-site'), (b'x-blupe-robot', b'robot-bimanual'),
                                           (b'x-forwarded-host', b'attacker.example')])
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)['error'], 'Session ended')
        self.assertEqual(connection.request.call_args.args, ('GET', '/api/chat'))
        self.assertEqual(connection.request.call_args.kwargs['headers']['cookie'], 'session=test')
        self.assertEqual(connection.request.call_args.kwargs['headers']['x-blupe-robot'], 'robot-bimanual')
        self.assertNotIn('x-forwarded-host', connection.request.call_args.kwargs['headers'])
        connection.close.assert_called_once()
        events = []
        async def send(event): events.append(event)
        with patch.object(chat, 'read_chat') as upstream:
            await chat.app({'type': 'http', 'method': 'POST', 'path': '/api/run'}, None, send)
            upstream.assert_not_called()
        self.assertEqual(events[0]['status'], 404)

    async def test_invalid_removal_file_fails_closed(self):
        response = MagicMock(status=200)
        response.read.return_value = b'{"messages":[],"visitor":"v"}'
        connection = MagicMock()
        connection.getresponse.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'removals.json'
            path.write_text('broken')
            events = []
            async def send(event): events.append(event)
            with patch.object(chat, 'HTTPConnection', return_value=connection), patch.dict(os.environ, YAM_CHAT_REMOVALS=str(path)):
                await chat.app({'type': 'http', 'method': 'GET', 'path': '/api/chat'}, None, send)
        self.assertEqual(events[0]['status'], 503)
