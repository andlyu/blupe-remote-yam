"""Client for an administrator-configured policy service, using an existing lease."""
import json
import time
import uuid
from urllib import request
from urllib.parse import urlsplit

from remote_yam.past_runs import public_runner_diagnostic


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RemoteSessionPolicy:
    def __init__(self, origin, token, *, provider_name, display_name, model, robot_id):
        u = urlsplit(origin)
        if (u.username or u.password or u.query or u.fragment or u.path not in ('', '/')
                or not u.hostname or not (u.scheme == 'https' or
                    u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost'))):
            raise ValueError('Policy service requires HTTPS or a loopback origin')
        if len(token) < 32:
            raise ValueError('Policy service credential is missing')
        self.origin, self._token = origin.rstrip('/'), token
        self.provider_name, self.display_name = provider_name, display_name
        self.model, self.robot_id = model, robot_id
        self.share_conversation = False
        self.interaction_sink = None
        self.stage = 'queued'
        self._open = request.build_opener(request.ProxyHandler({}), NoRedirect()).open

    def public_config(self):
        return dict(provider=self.provider_name, model=self.display_name, display_name=self.display_name,
                    implementation='proprietary', phase=self.stage)

    def _request(self, path, payload=None):
        req = request.Request(self.origin + path,
            data=json.dumps(payload, allow_nan=False).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'})
        try:
            with self._open(req, timeout=5) as response:
                raw = response.read(128001)
            if len(raw) > 128000:
                raise ValueError('Policy response too large')
            return json.loads(raw)
        except Exception as exc:
            # Never include URLs, capabilities or proprietary response bodies.
            raise RuntimeError('Hosted policy service is unavailable') from None

    def run_session(self, prompt, observation, api, context, cancelled):
        run_id = uuid.uuid4().hex
        previous = None
        try:
            if cancelled():
                return
            self._request('/runs', dict(context, run_id=run_id, instruction=prompt,
                model=self.model, session_capability=api._capability(context['session_id'])))
            deadline = time.monotonic() + 610
            while not cancelled() and time.monotonic() < deadline:
                state = self._request('/runs/' + run_id)
                self.stage = state.get('stage', 'running')
                if self.stage != previous:
                    previous = self.stage
                    if self.interaction_sink:
                        self.interaction_sink('model_progress', self.display_name + ': ' + self.stage)
                if state.get('status') == 'completed':
                    return
                if state.get('status') in ('failed', 'stopped'):
                    diagnostic = public_runner_diagnostic({'error': state.get('error')})
                    raise RuntimeError(diagnostic or 'Hosted policy stopped: execution_failed')
                time.sleep(.2)
            if not cancelled():
                raise RuntimeError('Hosted policy runtime exceeded')
        finally:
            # An idempotent cancel also covers a lost start response.
            try:
                self._request('/runs/' + run_id + '/stop', {})
            except RuntimeError:
                pass
