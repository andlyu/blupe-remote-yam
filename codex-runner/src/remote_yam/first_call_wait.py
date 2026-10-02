"""Optional authenticated first-call animation, independent of model commands."""
import time
import uuid
from urllib.parse import quote


class FirstCallWait:
    def __init__(self, api, context, cancelled):
        self.api, self.context, self.cancelled = api, context, cancelled
        self.started = False
        self.finished = False

    def _call(self, operation):
        context = self.context()
        sid = context['session_id']
        route = '/v1/sessions/'+quote(sid,safe='')+'/executions'
        rid = uuid.uuid4().hex
        payload = dict(schema_version=1,episode_id=context['episode_id'],lease_id=context['lease_id'],
                       request_id=rid,executor='yam_first_call',operation=operation,payload={})
        # No write retry. An ambiguous start/finish response stops the run.
        result = self.api._request('POST',route,payload,bearer=self.api._capability(sid))
        deadline = time.monotonic()+40
        while result.get('status') == 'pending':
            if self.cancelled():
                raise RuntimeError('First-call wait cancelled')
            if time.monotonic() >= deadline:
                raise RuntimeError('First-call return timed out; no policy motion sent')
            time.sleep(.1)
            result = self.api._request('GET',route+'/'+rid,bearer=self.api._capability(sid))
        if result.get('status') != 'completed' or not isinstance(result.get('result'),dict):
            raise RuntimeError(result.get('error') or 'First-call controller request failed')
        return result['result']

    def interaction(self, kind, details):
        if details.get('call') != 1:
            return
        if kind == 'model_request' and not self.started:
            self.started = True
            result = self._call('start')
            self.finished = result.get('state') == 'skipped' and result.get('settled') is True
        elif kind == 'model_timing' and self.started and not self.finished:
            if self.cancelled():
                raise RuntimeError('First-call wait cancelled')
            result = self._call('finish')
            if result.get('settled') is not True or result.get('state') not in {'returned','skipped'}:
                raise RuntimeError('First-call return not verified; no policy motion sent')
            self.finished = True
