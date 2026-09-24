"""Bounded, best-effort public text snapshots; never block robot control."""
import re
import threading
import time


class ConversationPublisher:
    def __init__(self, api, session_id, model, prompt, *, secrets=(), interval=0.5):
        self._send = api.publish_public_conversation
        self._session_id = session_id
        self._secrets = tuple(s for s in secrets if isinstance(s, str) and s)
        self._model = self._text(model)[:80] or 'Model'
        self._messages = [{'role': 'user', 'content': self._text(prompt)[:8000] or 'Robot task'}]
        self._condition = threading.Condition()
        self._version, self._sent = 1, 0
        self._closing = False
        self._error = None
        self._interval = interval
        self._thread = threading.Thread(target=self._run, name='public-conversation', daemon=True)
        self._thread.start()

    def _text(self, value):
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, '[redacted]')
        text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
        return re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer [redacted]', text)

    def add(self, kind, message, **details):
        if kind not in {'model_request', 'model_response'}:
            return
        # Latest observation, not the entire provider history, system prompts,
        # image blobs, encrypted reasoning, wire headers or private diagnostics.
        content = (details.get('observation') or details.get('prompt') or message
                   if kind == 'model_request' else details.get('response') or message)
        content = self._text(content)[:8000]
        if not content:
            return
        with self._condition:
            if self._closing:
                return
            self._messages.append({'role': 'assistant' if kind == 'model_response' else 'user', 'content': content})
            # Retain the task and the newest complete messages within API limits.
            while len(self._messages) > 64 or sum(len(m['content']) for m in self._messages) > 64000:
                del self._messages[1]
            self._version += 1
            self._condition.notify()

    def close(self):
        """Request a final flush without joining or delaying Stop."""
        with self._condition:
            self._closing = True
            self._condition.notify()

    def status(self):
        with self._condition:
            return {'enabled': True, 'state': 'retrying' if self._error else 'pending' if self._sent < self._version else 'shared',
                    'error': self._error}

    def _run(self):
        failures = 0
        try:
            while True:
                with self._condition:
                    while self._sent == self._version and not self._closing:
                        self._condition.wait()
                    if self._sent == self._version and self._closing:
                        return
                    version = self._version
                    payload = {'model': self._model, 'messages': [dict(m) for m in self._messages]}
                try:
                    self._send(self._session_id, payload)
                except Exception as exc:
                    failures += 1
                    with self._condition:
                        self._error = 'Conversation sharing failed (' + type(exc).__name__ + ')'
                        if self._closing and failures >= 3:
                            return
                else:
                    failures = 0
                    with self._condition:
                        self._sent, self._error = version, None
                # Coalesce fast messages; bound retries independently of control.
                time.sleep(min(5, self._interval * (2 ** min(failures, 4))))
        finally:
            self._secrets = ()
