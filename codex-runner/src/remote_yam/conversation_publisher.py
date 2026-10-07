"""Bounded, best-effort public text snapshots; never block robot control."""
import re
import json
import math
import threading
import time
from .public_task_progress import progress_message, public_text, PREFIX, MODEL_PREFIX


class ConversationPublisher:
    def __init__(self, api, session_id, model, prompt, *, secrets=(), interval=0.5,
                 task_progress=None, progress_source=None):
        self._send = api.publish_public_conversation
        self._session_id = session_id
        self._secrets = tuple(s for s in secrets if isinstance(s, str) and s)
        self._model = self._text(model)[:80] or 'Model'
        self._task = self._text(prompt)[:4000]
        self._messages = [{'role': 'user', 'content': self._text(prompt)[:8000] or 'Robot task'}]
        self._condition = threading.Condition()
        self._version, self._sent = 1, 0
        self._closing = False
        self._close_deadline = 0
        self._final_progress_pending = False
        self._error = None
        self._interval = interval
        self._progress_source = progress_source
        self._progress_content = None
        self._set_progress(task_progress)
        self._thread = threading.Thread(target=self._run, name='public-conversation', daemon=True)
        self._thread.start()

    def _set_progress(self, progress):
        content = progress_message(progress, self._text)
        if not content or content == self._progress_content:
            return
        self._progress_content = content
        self._messages = [m for m in self._messages if not
            (m['role'] == 'tool' and m['content'].startswith(PREFIX))]
        self._messages.insert(1, {'role': 'tool', 'content': content})
        self._bound_messages()
        self._version += 1

    def _bound_messages(self):
        while len(self._messages) > 64 or sum(len(m['content']) for m in self._messages) > 64000:
            # Keep the task and current native snapshot when trimming history.
            del self._messages[2 if self._progress_content else 1]

    def _text(self, value):
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, '[redacted]')
        text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)
        return public_text(text)

    def add(self, kind, message, **details):
        if kind not in {'model_request', 'model_response', 'model_progress', 'model_error'}:
            return
        if kind == 'model_progress' and details.get('progress_type') not in {'summary', 'status'}:
            return
        # Latest observation, not the entire provider history, system prompts,
        # image blobs, encrypted reasoning, wire headers or private diagnostics.
        content = (details.get('observation') or details.get('prompt') or message
                   if kind == 'model_request' else details.get('response') or message)
        content = self._text(content)[:8000]
        if not content:
            return
        role = 'assistant' if kind == 'model_response' else 'user'
        if kind in {'model_progress', 'model_error'}:
            timestamp = details.get('timestamp')
            if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                timestamp = time.time()
            value = dict(task=self._task, kind=kind,
                progress_type=details.get('progress_type') if kind == 'model_progress' else None,
                timestamp=timestamp, message=content[:4000])
            while True:
                content = MODEL_PREFIX + json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                if len(content) <= 8000:
                    break
                if not value['message']:
                    return
                value['message'] = value['message'][:-200]
            role = 'tool'
        with self._condition:
            if self._closing:
                return
            self._messages.append({'role': role, 'content': content})
            # Retain the task and the newest complete messages within API limits.
            self._bound_messages()
            self._version += 1
            self._condition.notify()

    def close(self):
        """Request a final flush without joining or delaying Stop."""
        with self._condition:
            if not self._closing and self._progress_source:
                # The local recovery manager writes its final retry resolution
                # just after the controller worker exits. Briefly drain those
                # receipt changes in this background thread, without holding Stop.
                self._close_deadline = time.monotonic() + max(.1, self._interval * 4)
            self._closing = True
            self._final_progress_pending = bool(self._progress_source)
            self._condition.notify()

    def status(self):
        with self._condition:
            return {'enabled': True, 'state': 'retrying' if self._error else 'pending' if self._sent < self._version else 'shared',
                    'error': self._error}

    def _run(self):
        failures = 0
        try:
            while True:
                # Provider status may read local receipts. Poll only on this
                # uploader thread, never on the robot-control or Stop path.
                if self._progress_source:
                    with self._condition:
                        final = self._closing
                    try:
                        progress = self._progress_source()
                    except Exception:
                        pass  # Status sharing must never fail a robot task.
                    else:
                        with self._condition:
                            self._set_progress(progress)
                    if final:
                        with self._condition:
                            self._final_progress_pending = False
                with self._condition:
                    while self._sent == self._version and not self._closing:
                        self._condition.wait(timeout=self._interval if self._progress_source else None)
                        if self._progress_source:
                            break
                    if self._final_progress_pending:
                        continue
                    if self._sent == self._version and self._closing:
                        remaining = self._close_deadline - time.monotonic()
                        if remaining > 0:
                            self._condition.wait(timeout=min(self._interval, remaining))
                            continue
                        return
                    if self._sent == self._version:
                        continue
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
