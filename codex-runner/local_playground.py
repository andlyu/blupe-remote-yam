"""The shared playground UI and queue backend, served on localhost with Codex."""
import asyncio
from urllib import request, error
from urllib.parse import urlsplit

from playground import HostedRunner, RequestError

PLAYGROUND = 'https://playground.blupe.io'


class LocalPlayground(HostedRunner):
    """Bridge the public viewer routes normally supplied by remote Caddy."""

    async def http(self, scope, receive, send):
        path, method = scope['path'], scope['method']
        media = path.startswith(('/synchronized/', '/live-video/'))
        public_history = path == '/api/past-runs'
        if not media and not public_history:
            return await super().http(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        if headers.get('host') != self.authority:
            raise RequestError(421, 'Unrecognized website host')
        if '..' in path or '%' in path or '\\' in path or method not in ('GET', 'OPTIONS', 'POST', 'PATCH', 'DELETE'):
            raise RequestError(404, 'Unknown viewer route')
        if method != 'GET' and (public_history or '/whep' not in path or headers.get('origin') != self.origin):
            raise RequestError(403, 'Viewer request must come from this runner')
        body = bytearray()
        if method in ('POST', 'PATCH'):
            while True:
                part = await asyncio.wait_for(receive(), 5)
                if part['type'] == 'http.disconnect':
                    raise RequestError(400, 'Incomplete viewer request')
                body.extend(part.get('body', b''))
                if len(body) > 128_000:
                    raise RequestError(413, 'Viewer request too large')
                if not part.get('more_body'):
                    break
        query = scope.get('query_string', b'').decode('ascii')
        if public_history:
            from urllib.parse import parse_qsl, urlencode
            query = urlencode([(k,v) for k,v in parse_qsl(query) if k != 'robot_id'] + [('robot_id', self.robot_id)])
        url = PLAYGROUND + path + ('?' + query if query else '')
        forwarded = {key: value for key, value in headers.items() if key in ('content-type', 'if-match')}
        forwarded['Origin'] = PLAYGROUND

        def fetch():
            req = request.Request(url, data=bytes(body) if method in ('POST', 'PATCH') else None,
                                  headers=forwarded, method=method)
            try:
                response = request.urlopen(req, timeout=15)
            except error.HTTPError as exc:
                response = exc
            with response:
                data = response.read(8_000_001)
                if len(data) > 8_000_000:
                    raise ValueError('Viewer response too large')
                extra = []
                for key in ('Location', 'ETag', 'Accept-Patch', 'Link'):
                    value = response.headers.get(key)
                    if value:
                        if key == 'Location' and value.startswith(PLAYGROUND + '/'):
                            value = value[len(PLAYGROUND):]
                        extra.append((key.lower().encode(), value.encode()))
                return response.status, response.headers.get('Content-Type', 'application/octet-stream'), data, extra
        try:
            status, kind, data, extra = await asyncio.to_thread(fetch)
        except (OSError, ValueError):
            raise RequestError(503, 'Public viewer temporarily unavailable') from None
        await self.respond(send, status, data, kind, extra)

    async def start_response(self, send, status, content_type, size, extra=()):
        async def local_headers(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [(k, v) for k, v in message['headers'] if k != b'content-security-policy']
                message['headers'].append((b'content-security-policy',
                    b"default-src 'none'; script-src 'self' https://www.googletagmanager.com; "
                    b"style-src 'self'; connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://www.googletagmanager.com; "
                    b"img-src 'self' data: https://*.google-analytics.com https://www.googletagmanager.com; "
                    b"media-src 'self' blob: https://huggingface.co https://*.huggingface.co https://*.hf.co; "
                    b"frame-src https://lerobot-visualize-dataset.hf.space https://huggingface.co; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"))
            await send(message)
        await super().start_response(local_headers, status, content_type, size, extra)


def serve(args):
    import os
    import webbrowser
    import uvicorn
    origin = f'http://127.0.0.1:{args.port}'
    import json
    from pathlib import Path
    from multi_robot import fleet
    groot_key_file = os.environ.get('YAM_GROOT_KEY_FILE')
    if groot_key_file is None:
        saved_key = Path.home() / '.config/runpod/api-key'
        groot_key_file = str(saved_key) if saved_key.is_file() else ''
    app = fleet(LocalPlayground, dict(public_origin=origin, session_api=args.session_api,
        camera_origin=args.camera_origin or args.session_api or 'http://127.0.0.1:8089',
        astra_endpoint=os.environ.get('ASTRA_ENDPOINT', ''),
        groot_key_file=groot_key_file,
        hardware_control=args.allow_hardware_control, development=True,
        local_codex=True, local_claude=True,
        share_conversation=not getattr(args, "no_share_conversation", False),
        default_provider='codex' if args.provider == 'auto' else args.provider),
        json.loads(os.environ['YAM_DASHBOARD_ROBOTS']) if os.environ.get('YAM_DASHBOARD_ROBOTS') else None)
    config = uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=False)
    # Reserve the port before opening a URL that could belong to an older runner.
    sock = config.bind_socket()
    try:
        print(f'Local playground: {origin}', flush=True)
        if not args.no_browser:
            webbrowser.open(origin)
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        sock.close()
