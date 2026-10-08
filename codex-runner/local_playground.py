"""The shared playground UI and queue backend, served on localhost with Codex."""
import asyncio
from urllib import request, error
from urllib.parse import urlsplit, parse_qs

from playground import HostedRunner, RequestError

PLAYGROUND = 'https://playground.blupe.io'


class LocalPlayground(HostedRunner):
    """Bridge the public viewer routes normally supplied by remote Caddy."""

    def __init__(self, **kwargs):
        from remote_yam.aspire_recovery_flow import RecoveryManager
        self.recovery_manager=RecoveryManager(self)
        super().__init__(**kwargs)
        if (not self.api_depth_provider_factory and self.local_codex
                and self.hardware == 'yam' and kwargs.get('session_api')):
            self.api_depth_provider_factory = self.api_depth_provider(kwargs['session_api'])

    def refresh_monitor(self):
        super().refresh_monitor()
        self.recovery_manager.tick()

    def launch(self,visitor,payload,**kwargs):
        with self.recovery_manager.lock:
            from remote_yam.aspire_recovery_flow import retry_limit
            import json
            try:limit=retry_limit(payload.get('automatic_retry_limit',1))
            except ValueError as exc:raise RequestError(400,str(exc)) from None
            for prior in self.visitors.values():
                if not prior.controller.busy():self.recovery_manager.cancel(prior)
            transform=kwargs.pop('provider_transform',None)
            def record_setting(policy):
                if getattr(policy,'directory',None) and getattr(policy,'repair_outcomes',False):
                    policy.automatic_retry_limit=limit
                    (policy.directory/'recovery-settings.json').write_text(json.dumps(dict(retry_limit=limit))+'\n')
                return transform(policy) if transform else policy
            kwargs['provider_transform']=record_setting
            return super().launch(visitor,payload,**kwargs)

    def control(self,visitor,path):
        with self.recovery_manager.lock:
            self.recovery_manager.cancel(visitor)
            return super().control(visitor,path)

    async def action_extension(self,path,visitor,payload,cookies,send):
        if path!='/api/aspire-recover':return await super().action_extension(path,visitor,payload,cookies,send)
        from pathlib import Path
        import re
        config=getattr(self,'aspire_station_config',None) or {}
        identity=payload.get('run_id','')
        if not isinstance(identity,str) or not re.fullmatch(r'aspire-[0-9a-f]{32}',identity):
            raise RequestError(400,'Choose a recorded ASPIRE task.')
        root=Path(config.get('run_directory',Path(config.get('skill_directory','.')).parent/'runs')).resolve()
        folder=root/identity
        records=sorted(root.glob('aspire-*/task-completion.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        if folder.is_symlink() or not records or records[0].parent!=folder:
            raise RequestError(409,'Recovery must target the latest recorded task; older evidence is retained separately.')
        from remote_yam.aspire_recovery_flow import retry_limit
        try:limit=retry_limit(payload.get('automatic_retry_limit',1))
        except ValueError as exc:raise RequestError(400,str(exc)) from None
        result=await asyncio.to_thread(self.recovery_manager.start,visitor,folder,limit=limit)
        await self.json(send,200,result)
        return True

    def api_depth_provider(self, origin):
        import json
        import os
        robot_id = getattr(self, 'robot_id', 'yam-1')
        self.aspire_station_config = None
        aspire_config_path = os.environ.get('YAM_ASPIRE_CONFIG')
        if aspire_config_path:
            from pathlib import Path
            from remote_yam.aspire_station import load_station_config
            config = load_station_config(aspire_config_path)
            if config.get('robot_id') == robot_id:
                self.use_api_depth = True
                config['execution_environment']='local'
                self.aspire_station_config = config
                def aspire_factory(name, key, model, directory, **selection):
                    if name != 'codex':
                        raise RequestError(400, 'This ASPIRE station uses the Codex provider')
                    from remote_yam.aspire_codex_policy import configured_aspire_policy
                    from remote_yam.codex_policy import CodexAdapter
                    return configured_aspire_policy(config, origin=config.get('origin', origin),
                        robot_id=robot_id, model=model, directory=directory,execution_environment='local',
                        recovery_factory=lambda:CodexAdapter(model or 'gpt-6-astra',
                            camera_source=self.camera_source, recording_root=directory), **selection)
                return aspire_factory
        def factory(name, key, model, directory):
            from remote_yam.codex_depth_policy import CodexDepthAdapter
            from remote_yam.depth_preflight import capture_before_queue
            provider = CodexDepthAdapter(origin, model or 'gpt-6-astra', all_depth_origin=origin,
                                         recording_root=directory)
            try:
                capture_before_queue(provider._camera_source.capture_preflight,
                    lambda: self.monitor_api.get_robot_observation('yam-1'),
                    cancelled=provider.cancelled)
                provider.set_depth_calibration(provider._camera_source.snapshots['top'].calibration)
            except Exception as exc:
                provider._workspace.cleanup()
                raise RequestError(503, str(exc)) from None
            return provider
        return factory

    def resolve_api_depth_launch(self, prompt):
        config = getattr(self, 'aspire_station_config', None)
        if not config:
            return super().resolve_api_depth_launch(prompt)
        from remote_yam.aspire_executable_skills import select_aspire_launch
        return select_aspire_launch(config, prompt,execution_environment='local')

    async def session_extension(self, cookies, visitor):
        setup, headers = await super().session_extension(cookies, visitor)
        config = getattr(self, 'aspire_station_config', None)
        if config:
            from remote_yam.runpod_sam3 import selected_backend
            backend = selected_backend(config.get('segmentation_backend', 'astra'))
            model = {'runpod_sam3':'facebook/sam3', 'astra':'gpt-6-astra'}.get(backend, 'SAM3')
            setup = dict(setup, segmentation={'backend':backend, 'model':model})
            if config.get('skill_learning', {}).get('enabled'):
                timing=config['skill_learning'].get('review_timing','before_launch')
                setup['skill_learning'] = {'enabled': True,
                    'workflow': 'upstream_topic_promotion',
                    'coordinator': 'codex_subscription_after_run' if timing=='after_run' else 'codex_subscription_before_next_task',
                    'review_timing': timing}
            if config.get('executable_skills', {}).get('enabled'):
                setup['executable_skills'] = {'enabled': True,
                    'selection': 'bind_compatible_inputs_before_code_generation'}
        return setup, headers

    async def http(self, scope, receive, send):
        path, method = scope['path'], scope['method']
        if path == '/api/aspire-lineage' or path.startswith('/api/aspire-lineage/artifacts/'):
            headers = dict(scope.get('headers', []))
            if headers.get(b'host', b'').decode() != self.authority:
                raise RequestError(421, 'Unrecognized website host')
            if (method != 'GET' or headers.get(b'sec-fetch-site') == b'cross-site'
                    or headers.get(b'origin',self.origin.encode()).decode() != self.origin):
                raise RequestError(403, 'Open the local lineage view directly')
            config = getattr(self, 'aspire_station_config', None)
            if not config:
                raise RequestError(404, 'No ASPIRE lineage configured for this robot')
            from remote_yam.aspire_lineage_catalog import LineageCatalog
            catalog = LineageCatalog(config)
            data = await asyncio.to_thread(catalog.build)
            if path == '/api/aspire-lineage':
                prompt=parse_qs(scope.get('query_string',b'').decode()).get('prompt',[''])[0]
                if prompt:
                    if len(prompt)>16_000:raise RequestError(413,'Prompt preview is too large')
                    # Use the actual Run selection path, without constructing a
                    # provider, starting perception or admitting a robot session.
                    selection=await asyncio.to_thread(self.resolve_api_depth_launch,prompt)
                    selected=selection['program']
                    data['next_run_binding']=dict(route=selection['route'],
                        provenance=selected['provenance'] if selected else None,
                        source=catalog.artifact(selected['provenance']['core_source']) if selected else None,
                        physical_success=None,queue_session_created=False,physical_motion_calls=0)
                return await self.json(send, 200, data)
            key = path.removeprefix('/api/aspire-lineage/artifacts/')
            artifact = catalog.artifacts.get(key)
            if not artifact:
                raise RequestError(404, 'Unknown recorded lineage artifact')
            import mimetypes
            if artifact.stat().st_size > 64_000_000:
                raise RequestError(413, 'Open this large recorded artifact from its local source path')
            return await self.respond(send, 200, await asyncio.to_thread(artifact.read_bytes),
                mimetypes.guess_type(artifact.name)[0] or 'text/plain',
                [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff')])
        streams = [getattr(app, 'video_stream', None) for app in getattr(self, '_fleet_apps', {'self': self}).values()]
        prefixes = tuple('/' + stream['path'] + '/' for stream in streams if stream)
        media = path.startswith(('/synchronized/', '/live-video/')) or bool(prefixes and path.startswith(prefixes))
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
