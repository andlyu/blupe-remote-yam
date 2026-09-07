#!/usr/bin/env python3
"""Launch the local Public YAM runner UI on http://127.0.0.1:8787."""

from __future__ import annotations

import argparse
import json
import pathlib
import os
import secrets
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import request as urlrequest
from urllib.parse import urlsplit

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from remote_yam.controller import RunnerController
from remote_yam.credentials import CredentialVault
from remote_yam.cameras import CameraFrameSource, NoRedirect as _NoRedirect, origin as _origin, trusted_origin as _trusted_origin
from remote_yam.providers import RepeatingRaiseLowerAdapter
from remote_yam.robocurve_policy import AstraAdapter, OpenAIAdapter
from remote_yam.session import HttpSessionAPI, MockSessionAPI
from remote_yam.viewer import YamBimanualViewer
from remote_yam.interactions import list_recordings, read_recording, recording_directory
from remote_yam.artifacts import log_archive, video_archive, run_artifacts, recorded_image


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BluPe Remote YAM</title>
<style>
:root{--ink:#12211b;--paper:#f5f1e6;--lime:#c6f432;--red:#e9593f;--amber:#f0a52b;--line:#b8b5a9}*{box-sizing:border-box}body{margin:0;min-height:100vh;color:var(--ink);background:radial-gradient(circle at 92% 8%,rgba(198,244,50,.52),transparent 27rem),linear-gradient(110deg,transparent 49.7%,rgba(18,33,27,.06) 50%,transparent 50.3%),var(--paper);font-family:'DM Mono',monospace}main{width:min(1080px,calc(100% - 32px));margin:0 auto;padding:42px 0 56px}header{display:grid;grid-template-columns:1fr auto;align-items:end;gap:24px;border-bottom:2px solid var(--ink);padding-bottom:20px}h1{font:700 clamp(2.8rem,8vw,6.8rem)/.84 'Syne',sans-serif;letter-spacing:-.065em;margin:0}.eyebrow{writing-mode:vertical-rl;text-transform:uppercase;font-size:12px;letter-spacing:.18em}.grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(260px,.6fr);gap:22px;margin-top:22px}.panel{border:1px solid var(--ink);background:rgba(245,241,230,.86);box-shadow:7px 7px 0 var(--ink);padding:22px}label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin:0 0 7px}input,select,textarea{width:100%;border:1px solid var(--ink);border-radius:0;background:#fffdf5;color:var(--ink);padding:12px;font:500 14px 'DM Mono',monospace}textarea{min-height:220px;resize:vertical;line-height:1.55}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}.field{margin-bottom:16px}.astra{display:none}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}button{appearance:none;border:1px solid var(--ink);padding:13px 17px;font:700 13px 'Syne',sans-serif;text-transform:uppercase;cursor:pointer;box-shadow:4px 4px 0 var(--ink)}button:active{transform:translate(3px,3px);box-shadow:1px 1px 0 var(--ink)}.run{flex:1;background:var(--lime)}.stop{background:var(--red);color:white}.operator{background:var(--amber)}.disconnect{background:transparent}.status-head{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:14px}.dot{width:14px;height:14px;border-radius:50%;background:#888}.dot.active{background:#4cba57;animation:pulse 1.8s infinite}dl{margin:20px 0 0}dt{color:#63685f;font-size:10px;text-transform:uppercase;letter-spacing:.12em;margin-top:18px}dd{margin:5px 0 0;overflow-wrap:anywhere;font-size:13px}.notice{margin-top:22px;padding:13px;border-left:5px solid var(--lime);font-size:11px;line-height:1.55;background:#fffdf5}@keyframes pulse{50%{box-shadow:0 0 0 8px rgba(76,186,87,.12)}}@media(max-width:760px){main{padding-top:24px}.grid,.row{grid-template-columns:1fr}header{align-items:start}.eyebrow{writing-mode:horizontal-tb}}</style></head>
<body><main><header><h1>BLUPE<br>REMOTE YAM</h1><div class="eyebrow">Running on this computer / 127.0.0.1</div></header><div class="notice">This is your runner UI. Your provider key stays in this local backend and is never sent to the YAM Session API or BluPe.</div><div class="grid"><section class="panel"><div class="row"><div><label for="provider">Provider</label><select id="provider"><option value="openai">OpenAI</option><option value="astra">Astra</option></select></div><div><label for="model">Model</label><input id="model" value="gpt-6-astra" list="models"><datalist id="models"><option value="gpt-6-astra"><option value="gpt-4.1-mini"><option value="astra-default"></datalist></div></div><div class="field"><label>Launch-time credential</label><div class="notice" id="keyStatus">checking secure local source</div><div class="notice">Launch with <code>./run.sh</code>. It creates an isolated environment and installs the control transport.<br>Provider alternative: prefix that command with <code>OPENAI_API_KEY=...</code> or <code>ASTRA_API_KEY=...</code>.</div></div><div class="field astra" id="astraField"><label for="endpoint">Astra Responses-compatible endpoint</label><input id="endpoint" placeholder="https://your-astra-endpoint/v1/responses"></div><div class="field"><label for="prompt">Prompt and output format</label><textarea id="prompt">Control both arms independently and conservatively. Return explicit left and right commands for every observation; never mirror or omit an arm.</textarea></div><div class="notice">Command format: {"left":{"mode":"joints"|"pose","values":[6]},"right":{"mode":"joints"|"pose","values":[6]},"left_gripper":0..1,"right_gripper":0..1}</div><div class="actions"><button class="run" id="run">Join Queue</button><button class="stop" id="stop">Leave Queue / Stop</button><button class="operator" id="operator" aria-expanded="false" aria-controls="operatorContact">Call Operator</button><button class="disconnect" id="disconnect">Disconnect</button></div><div id="operatorContact" class="notice" hidden><p>Please feel free to text/call <a id="operatorPhone" href="tel:+17033443837">7033443837</a>, or email us at <a href="mailto:andrew@blupe.io">andrew@blupe.io</a>.</p><a href="sms:+17033443837">Send a text</a></div></section><aside class="panel"><div class="status-head"><strong>Runner state</strong><span class="dot" id="dot"></span></div><dl><dt>Status</dt><dd id="status">idle</dd><dt>Episode</dt><dd id="episode">-</dd><dt>Next step</dt><dd id="step">0</dd><dt>Latest model command</dt><dd id="command">waiting</dd><dt>Model / IK latency</dt><dd id="modelLatency">-</dd><dt>Server action RTT</dt><dd id="serverLatency">-</dd><dt>Command settle</dt><dd id="settleLatency">-</dd><dt>Heartbeat</dt><dd id="heartbeat">not sent</dd><dt>Error</dt><dd id="error">-</dd></dl><div class="notice">Stop requests validated return to rest. Call Operator shows our phone number and email. Use Stop to end robot execution. Infrastructure and credentials stay behind the scenes.</div></aside></div></main>
<script>let CSRF='__YAM_CSRF_TOKEN__';const $=id=>document.getElementById(id);function keyStatus(c){const p=$('provider').value;$('keyStatus').textContent=c.providers[p]?'Key configured in this local runner':'No key configured. Join Queue & Run will request it securely in this terminal.'}async function config(){const c=await(await fetch('/api/config',{cache:'no-store'})).json();CSRF=c.local_control_token||CSRF;if(!cfg){$('provider').value=c.default_provider;$('prompt').value=c.default_prompt;$('endpoint').value=c.astra_endpoint;if(c.default_provider==='astra')$('model').value=c.astra_model;$('astraField').style.display=c.default_provider==='astra'?'block':'none'}keyStatus(c);return c}let cfg=null;$('provider').onchange=()=>{$('astraField').style.display=$('provider').value==='astra'?'block':'none';if(cfg){$('model').value=$('provider').value==='astra'?cfg.astra_model:'gpt-6-astra';keyStatus(cfg)}};async function post(path,body={},retried=false){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-YAM-Runner-Token':CSRF},body:JSON.stringify(body)});if(response.status===403&&!retried){cfg=await config();return post(path,body,true)}const data=await response.json();if(!response.ok)throw new Error(data.error||'Request failed');return data}function render(s){const l=s.latency||{},fmt=x=>x?`${x.last_ms.toFixed(1)} ms latest / ${x.avg_ms.toFixed(1)} ms avg`:'-';$('status').textContent=s.status;$('episode').textContent=s.episode_id||'-';$('step').textContent=s.next_step_id;$('command').textContent=s.last_model_command?JSON.stringify(s.last_model_command):'waiting';$('modelLatency').textContent=fmt(l.model);$('serverLatency').textContent=fmt(l.server_action_rtt);$('settleLatency').textContent=fmt(l.command_settle);$('heartbeat').textContent=s.heartbeat_sent?'seen':'not seen';$('error').textContent=s.error||s.execution_blocked_reason||'-';$('dot').className='dot '+(['queued','preparing','running'].includes(s.status)?'active':'')}$('run').onclick=async()=>{try{$('error').textContent='If needed, enter your provider key in the launch terminal.';render(await post('/api/run',{provider:$('provider').value,model:$('model').value,prompt:$('prompt').value,endpoint:$('endpoint').value}));cfg=await config()}catch(e){$('error').textContent=e.message}};$('stop').onclick=async()=>{try{render(await post('/api/stop'))}catch(e){$('error').textContent=e.message}};$('operator').onclick=()=>{$('operatorContact').hidden=false;$('operator').setAttribute('aria-expanded','true');$('operatorPhone').focus()};$('disconnect').onclick=async()=>{try{render(await post('/api/disconnect'))}catch(e){$('error').textContent=e.message}};config().then(c=>cfg=c);setInterval(async()=>{try{render(await(await fetch('/api/status')).json())}catch(_){}},750)</script></body></html>"""

MONITOR_HTML = r"""<section class="monitor-grid"><article class="panel monitor"><div class="monitor-head"><strong>YAM server cameras</strong><span class="feedback-badge" id="cameraState">NO SIGNAL</span></div><div class="camera-grid" id="cameraGrid"><div class="camera-empty">WAITING FOR SERVER FRAMES</div></div></article><article class="panel monitor"><div class="monitor-head"><strong>Canonical MuJoCo YAM twin</strong><span class="feedback-badge" id="simState">NO STATE</span></div><img id="armTwin" alt="Read-only canonical MuJoCo bimanual YAM state from Session API feedback"><canvas id="armSim" width="720" height="360" aria-hidden="true"></canvas></article></section>"""
PAGE = PAGE.replace("</head>", '<link rel="stylesheet" href="/static/monitor.css"></head>')
PAGE = PAGE.replace("</head>", '<link rel="stylesheet" href="/static/queue.css"></head>')
MONITOR_HTML += r"""<article class="panel queue-panel"><div class="monitor-head"><strong>Live FIFO queue</strong><span class="feedback-badge" id="queueState">CONNECTING</span></div><div class="station-readout" id="stationReadout">Waiting for station availability</div><ol class="queue-list" id="queueList"><li class="queue-empty">Loading queue</li></ol></article>"""
PAGE = PAGE.replace('<div class="grid">', MONITOR_HTML + '<div class="grid">', 1)
PAGE = PAGE.replace("</body>", '<script src="/static/monitor.js"></script></body>')
PAGE = PAGE.replace("</body>", '<script src="/static/queue.js"></script></body>')
PAGE = PAGE.replace("</body>", """<script>(function(){const image=document.getElementById('armTwin'),badge=document.getElementById('simState');function next(){image.src='/api/twin/frame.png?t='+Date.now()}image.onload=()=>{badge.textContent='MUJOCO LIVE';badge.className='feedback-badge live';setTimeout(next,125)};image.onerror=()=>{badge.textContent='NO TWIN STATE';badge.className='feedback-badge stale';setTimeout(next,750)};next()})()</script></body>""")
PAGE = PAGE.replace(
    '<option value="openai">OpenAI</option>',
    '<option value="local_raise_lower">Built-in policy: Raise / lower 20 cm — 3 cycles</option><option value="openai">OpenAI</option>',
)
PAGE = PAGE.replace(
    "This is your runner UI. Your provider key stays in this local backend and is never sent to the YAM Session API or BluPe.",
    "This is your runner UI. No BluPe credential is required. Live monitoring and the reviewed built-in policy need no provider key.",
)
PAGE = PAGE.replace(
    "c.providers[p]?'Key configured in this local runner':'No key configured. Join Queue & Run will request it securely in this terminal.'",
    "p==='local_raise_lower'?'No provider key required for this built-in policy':c.providers[p]?'Key configured in this local runner':'No key configured. Join Queue & Run will request it securely in this terminal.'",
)
PAGE = PAGE.replace(
    "$('error').textContent='If needed, enter your provider key in the launch terminal.';",
    "$('error').textContent=$('provider').value==='local_raise_lower'?'Joining queue with provider-free policy...':'If needed, enter your provider key in the launch terminal.';",
)
PAGE = PAGE.replace(
    '<dt>Error</dt><dd id="error">-</dd>',
    '<dt>Error</dt><dd id="error">-</dd><dt>Safety context</dt><dd id="safetyError">-</dd>',
)
PAGE = PAGE.replace(
    "$('error').textContent=s.error||s.execution_blocked_reason||'-';",
    "$('error').textContent=s.error||s.execution_blocked_reason||'-';$('safetyError').textContent=s.safety_error?JSON.stringify(s.safety_error):'-';",
)
STATIC_ROOT = PROJECT_ROOT / "static"

PAGE = PAGE.replace('<div class="actions">', '<p id="repeatHelp">Runs 3 cycles: up request → response → down request → response. Stop holds position.</p><div class="actions">', 1)
PAGE = PAGE.replace("$('step').textContent=s.next_step_id;", "$('step').textContent=s.next_step_id;$('repeatHelp').textContent=s.provider?.repeat?'Completed cycles: '+s.provider.completed_cycles+'/3 — '+(s.provider.completed_cycles>=3?'done':'moving '+s.provider.phase):'Runs 3 cycles: up request → response → down request → response. Stop holds position.';")

PAGE = PAGE.replace('<dt>Next step</dt>', '<dt>Waypoints submitted</dt>')
PAGE = PAGE.replace('<dt>Latest model command</dt>', '<dt>Packets submitted</dt><dd id="packets">0</dd><dt>Packet progress</dt><dd id="packetProgress">—</dd><dt>Latest model command</dt>')
PAGE = PAGE.replace("$('step').textContent=s.next_step_id;", "$('step').textContent=s.next_step_id;if(s.status==='stopped'&&!s.error&&s.provider?.repeat&&s.provider.completed_cycles===3)$('status').textContent='Done';$('packets').textContent=s.packets_submitted||0;$('packetProgress').textContent=s.trajectory?s.trajectory.progress_count+'/'+s.trajectory.waypoint_count+' at 10 Hz':'—';")


PAGE = PAGE.replace('<dt>Heartbeat</dt>', '<dt>Images sent to model</dt><dd id="modelImages">—</dd><dt>Heartbeat</dt>')
PAGE = PAGE.replace("$('heartbeat').textContent=", "$('modelImages').textContent=s.provider?.vision?((s.provider.vision.frames||[]).map(f=>f.camera).join(', ')||'none')+' / '+s.provider.vision.state:'not used by this policy';$('heartbeat').textContent=")

PAGE = PAGE.replace('Prompt and output format', 'Task for Astra')
PAGE = PAGE.replace('Command format: {"left":{"mode":"joints"|"pose","values":[6]},"right":{"mode":"joints"|"pose","values":[6]},"left_gripper":0..1,"right_gripper":0..1}',
                    'Astra uses RoboCurve’s no-demo policy: camera images and arm state → move_to → completed motion feedback → next decision.')
PAGE = PAGE.replace('<dt>Latest model command</dt>', '<dt>Astra calls</dt><dd id="modelCalls">0</dd><dt>Latest model note</dt><dd id="modelNote">—</dd><dt>Policy outcome</dt><dd id="modelOutcome">—</dd><dt>Local recording</dt><dd id="modelRecording">—</dd><dt>Latest model command</dt>')
PAGE = PAGE.replace("$('heartbeat').textContent=", "$('modelCalls').textContent=s.provider?.model_calls||0;$('modelNote').textContent=s.provider?.note||'—';$('modelOutcome').textContent=s.provider?.outcome?JSON.stringify(s.provider.outcome):'—';$('modelRecording').textContent=s.provider?.recording_path||'—';$('heartbeat').textContent=")
PAGE = PAGE.replace(":'Runs 3 cycles: up request → response → down request → response. Stop holds position.';", ":s.provider?.policy==='robocurve_no_demo'?'Astra observes, moves, and observes again until done, give up, or Stop.':'Built-in policy runs 3 raise/lower cycles. Stop holds position.';")

PAGE = PAGE.replace('</head>', '<link rel="stylesheet" href="/static/interactions.css"></head>')
PAGE = PAGE.replace('</main>', '''<section class="panel interaction-panel" aria-label="Interaction log">
<div class="interaction-head"><h2>Interaction log</h2><span id="interactionStage" role="status">No active run</span></div>
<p>Follow model decisions, requested motion, gateway checks, and measured feedback.</p>
<div class="interaction-controls"><label>Run<select id="interactionRun"><option value="">Current run</option></select></label>
<button id="conversationOpen" class="disconnect" aria-expanded="false" aria-controls="runnerConversation">Open runner conversation</button><button id="interactionDownload" class="disconnect">Save log</button><button id="wireDownload" class="disconnect" title="Play the selected run here: left | top | right">Watch replay</button>
<a id="datasetLink" href="https://huggingface.co/datasets/andlyu/Public-YAM-runs/tree/main" target="_blank" rel="noopener">HF dataset ↗</a></div>
<p>Save log includes model inputs, outputs, input images, and execution errors. Watch replay plays left | top | right here after the run uploads. Save video downloads a copy.</p>
<p id="interactionPath"></p><p id="interactionError" role="alert"></p>
<section id="runnerConversation" class="runner-conversation" tabindex="-1" aria-label="Runner conversation" hidden><h2>Runner conversation</h2><p>Follow the chat between your runner and the model. Messages, camera images, and tool results stay on your computer.</p><label>Show <select id="conversationCall" disabled></select></label><p id="conversationStatus" role="status">Choose a run to view its conversation.</p><div id="conversationContent"></div></section><ol id="interactionList"><li>No interactions yet. Join the queue to start a run, or choose a previous recording.</li></ol>
</section></main>''')
PAGE = PAGE.replace('</body>', '<script src="/static/interactions.js"></script></body>')
PAGE = PAGE.replace('</head>', '<link rel="stylesheet" href="/static/conversation.css"></head>')
PAGE = PAGE.replace('</body>', '<script src="/static/conversation.js"></script></body>')

PAGE = PAGE.replace("$('error').textContent=s.error||s.execution_blocked_reason||'-';", "$('error').textContent=s.server_contact_issue?(s.server_contact_issue.message+(s.server_contact_issue.state==='retrying'?' — retrying ('+s.server_contact_issue.attempt+'/'+s.server_contact_issue.max_attempts+')':'')):s.error||s.execution_blocked_reason||'-';")


def build_handler(
    controller: RunnerController,
    credentials: CredentialVault,
    csrf_token: str,
    default_provider: str,
    allow_key_prompt: bool,
    camera_origin: str,
    twin_viewer: YamBimanualViewer | None = None,
    recording_root: pathlib.Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    allowed_camera_origin = _trusted_origin(camera_origin)
    camera_source = CameraFrameSource(camera_origin)
    recordings = recording_root or PROJECT_ROOT / 'recordings'

    def selected_provider() -> str:
        if default_provider != "auto":
            return default_provider
        configured = credentials.public_status()
        if configured.get("openai"):
            return "openai"
        return "astra" if configured.get("astra") else "local_raise_lower"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data: http: https:; media-src 'self' blob: https://huggingface.co https://*.huggingface.co https://*.hf.co; base-uri 'none'; frame-ancestors 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/status":
                status = controller.status()
                observation = status.get("last_observation")
                images = observation.get("images") if isinstance(observation, dict) else None
                if isinstance(images, dict):
                    public_observation = dict(observation)
                    public_observation["images"] = {
                        name: {"url": f"/api/monitor/cameras/{name}"}
                        for name in ("left", "top", "right") if name in images
                    }
                    status["last_observation"] = public_observation
                self._json(HTTPStatus.OK, status)
            elif self.path == "/api/config":
                providers = credentials.public_status()
                providers["local_raise_lower"] = True
                self._json(HTTPStatus.OK, {
                    "providers": providers,
                    "default_provider": selected_provider(),
                    "default_prompt": "place green block on plate",
                    "astra_model": os.environ.get("ASTRA_MODEL", "astra-default"),
                    "astra_endpoint": os.environ.get("ASTRA_ENDPOINT", ""),
                    "local_control_token": csrf_token,
                })
            elif self.path == "/api/trace":
                if self.headers.get("X-YAM-Runner-Token") != csrf_token:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local control token"})
                    return
                try:
                    self._json(HTTPStatus.OK, controller.sanitized_episode_trace())
                except Exception as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": f"{type(exc).__name__}: {exc}"})
            elif self.path.startswith("/api/twin/frame.png"):
                frame = twin_viewer.frame() if twin_viewer is not None else None
                if frame is None:
                    self._no_signal(HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
            elif self.path.startswith("/api/monitor/cameras/"):
                name = self.path.removeprefix("/api/monitor/cameras/")
                if name not in {"left", "top", "right"}:
                    self._no_signal(HTTPStatus.NOT_FOUND)
                    return
                self._proxy_camera(name)
            elif self.path == '/api/recordings' or self.path.startswith('/api/recordings/'):
                if self.headers.get('X-YAM-Runner-Token') != csrf_token:
                    self._json(HTTPStatus.FORBIDDEN, {'error':'invalid local control token'})
                    return
                try:
                    parts = self.path.split('/')
                    if self.path == '/api/recordings':
                        self._json(HTTPStatus.OK, {'runs':list_recordings(recordings)})
                    elif len(parts)==6 and parts[4]=='blobs':
                        body, content_type = recorded_image(recordings, parts[3], parts[5])
                        self.send_response(HTTPStatus.OK)
                        self.send_header('Content-Type', content_type)
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    elif len(parts)==5 and parts[4]=='interactions':
                        self._json(HTTPStatus.OK, read_recording(recordings, parts[3]))
                    elif len(parts)==5 and parts[4]=='artifacts':
                        self._json(HTTPStatus.OK, run_artifacts(recordings, parts[3]))
                    elif len(parts)==5 and parts[4] in {'log.zip', 'video.mp4'}:
                        exporter = log_archive if parts[4]=='log.zip' else video_archive
                        try:
                            archive = exporter(recordings, parts[3])
                        except ValueError as exc:
                            self._json(HTTPStatus.NOT_FOUND, {'error': str(exc)})
                            return
                        with archive:
                            length = archive.seek(0, 2)
                            archive.seek(0)
                            self.send_response(HTTPStatus.OK)
                            self.send_header('Content-Type', 'application/zip' if parts[4]=='log.zip' else 'video/mp4')
                            self.send_header('Content-Disposition', f'attachment; filename="yam-run-{parts[3].split("_", 1)[1]}-{parts[4]}"')
                            self.send_header('Cache-Control', 'no-store')
                            self.send_header('X-Content-Type-Options', 'nosniff')
                            self.send_header('Content-Length', str(length))
                            self.end_headers()
                            while chunk := archive.read(1024*1024):
                                self.wfile.write(chunk)
                    elif len(parts)==5 and parts[4] in {'calls.jsonl','interactions.jsonl'}:
                        path = recording_directory(recordings,parts[3])/parts[4]
                        if path.is_symlink() or not path.is_file():
                            raise ValueError('Recording file unavailable')
                        body = path.read_bytes()
                        self.send_response(HTTPStatus.OK)
                        self.send_header('Content-Type','application/x-ndjson')
                        self.send_header('Content-Disposition',f'attachment; filename="yam-run-{parts[3].split("_", 1)[1]}-{parts[4]}"')
                        self.send_header('Cache-Control','no-store')
                        self.send_header('Content-Length',str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        raise ValueError('Unknown recording route')
                except (OSError, ValueError):
                    self._json(HTTPStatus.NOT_FOUND, {'error':'Recording unavailable'})
            elif self.path in {"/static/conversation.js", "/static/conversation.css", "/static/interactions.css", "/static/interactions.js", "/static/monitor.css", "/static/monitor.js", "/static/queue.css", "/static/queue.js", "/static/mock-camera-0.svg", "/static/mock-camera-1.svg", "/static/mock-camera-2.svg"}:
                path = STATIC_ROOT / self.path.rsplit("/", 1)[-1]
                body = path.read_bytes()
                content_type = "text/css; charset=utf-8" if path.suffix == ".css" else "text/javascript; charset=utf-8" if path.suffix == ".js" else "image/svg+xml"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _proxy_camera(self, name: str) -> None:
            upstream = None
            try:
                upstream_url = controller.monitor_camera_url(name)
                if not isinstance(upstream_url, str) or _origin(upstream_url) != allowed_camera_origin:
                    self._no_signal()
                    return
                parsed = urlsplit(upstream_url)
                if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith("/"):
                    self._no_signal()
                    return
                opener = urlrequest.build_opener(_NoRedirect)
                upstream = opener.open(
                    urlrequest.Request(upstream_url, headers={"Accept": "multipart/x-mixed-replace"}),
                    timeout=2.0,
                )
                content_type = upstream.headers.get("Content-Type", "")
                if not content_type.lower().startswith("multipart/x-mixed-replace"):
                    self._no_signal()
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while True:
                    chunk = upstream.read(16384)
                    if not chunk:
                        return
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError, ValueError):
                if upstream is None:
                    self._no_signal()
            finally:
                if upstream is not None:
                    upstream.close()

        def _no_signal(self, status: HTTPStatus = HTTPStatus.BAD_GATEWAY) -> None:
            body = b"NO SIGNAL\n"
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self) -> None:
            try:
                if self.headers.get("X-YAM-Runner-Token") != csrf_token:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local control token"})
                    return
                payload = self._body()
                if self.path == "/api/run":
                    name, model = str(payload.get("provider", selected_provider())), str(payload.get("model", ""))
                    if name == "local_raise_lower":
                        provider = RepeatingRaiseLowerAdapter(cycles=3)
                    elif not credentials.public_status().get(name, False):
                        if not allow_key_prompt or not sys.stdin.isatty():
                            raise RuntimeError(
                                f"{name.upper()} key is missing; set its environment variable and restart"
                            )
                        credentials.prompt_for(name)
                    if name != "local_raise_lower":
                        key = credentials.require(name)
                        if name == "openai":
                            provider = OpenAIAdapter(key, model or "gpt-6-astra", camera_source=camera_source, recording_root=PROJECT_ROOT / "recordings")
                        elif name == "astra":
                            provider = AstraAdapter(key, model or os.environ.get("ASTRA_MODEL", "astra-default"), str(payload.get("endpoint", os.environ.get("ASTRA_ENDPOINT", ""))), camera_source=camera_source, recording_root=PROJECT_ROOT / "recordings")
                        else:
                            raise ValueError("Unknown provider")
                    result = controller.join_and_run(provider, str(payload.get("prompt", "place green block on plate")))
                elif self.path == "/api/stop":
                    result = controller.stop()
                elif self.path == "/api/disconnect":
                    result = controller.disconnect()
                elif self.path == "/api/operator":
                    result = controller.call_operator()
                elif self.path == "/api/credentials/clear":
                    credentials.clear(str(payload.get("provider", selected_provider())))
                    result = {"providers": credentials.public_status(), "default_provider": default_provider}
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, result)
            except Exception as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": f"{type(exc).__name__}: {exc}"})

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 128_000:
                raise ValueError("Request body is too large")
            value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--session-api", default="https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com", help="Session API base URL; defaults to BluPe")
    parser.add_argument("--mock", action="store_true", help="Use the in-process mock instead of BluPe")
    parser.add_argument("--robot-id", default="yam-1", help="Robot whose read-only live feedback is shown")
    parser.add_argument("--camera-origin", default="http://10.1.10.185:8089", help="Exact trusted camera relay origin used by the read-only same-origin proxy")
    parser.add_argument("--provider", choices=("auto", "local_raise_lower", "openai", "astra"), default="auto", help="Default inference policy/provider")
    parser.add_argument("--no-key-prompt", action="store_true", help="Do not request a missing provider key when Join Queue is invoked")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--allow-hardware-control", action=argparse.BooleanOptionalAction, default=True, help="Allow queued hardware sessions; the station operator and gateway enforce readiness")
    parser.add_argument("--waypoint-packets", action=argparse.BooleanOptionalAction, default=True, help="Use the deployed trajectory API to execute policy waypoint packets at 10 Hz")
    parser.add_argument("--check-provider", action="store_true", help="Test OpenAI model access without connecting to the robot")
    args = parser.parse_args()
    if args.check_provider:
        credentials = CredentialVault.from_local_sources()
        if not credentials.public_status().get("openai"):
            credentials.prompt_for("openai")
        try:
            OpenAIAdapter(credentials.require("openai"), "gpt-6-astra")._post_json({
                "model": "gpt-6-astra", "input": "Reply with OK.", "store": False,
            })
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
        print("OpenAI model gpt-6-astra responded successfully. No robot connection was made.")
        return
    session_api = (
        HttpSessionAPI(args.session_api, supports_trajectories=args.waypoint_packets)
        if not args.mock
        else MockSessionAPI()
    )
    credentials = CredentialVault.from_local_sources()
    controller = RunnerController(session_api, robot_id=args.robot_id, hardware_control_enabled=args.allow_hardware_control, recording_root=PROJECT_ROOT / 'recordings')
    monitor_stop = threading.Event()

    def monitor_live_observation() -> None:
        while not monitor_stop.is_set():
            try:
                controller.update_monitor_observation(
                    session_api.get_robot_observation(args.robot_id)
                )
            except Exception:
                pass
            try:
                controller.update_queue_snapshot(session_api.get_queue_snapshot())
            except Exception:
                pass
            monitor_stop.wait(0.5)

    monitor_thread = threading.Thread(
        target=monitor_live_observation,
        name="public-yam-live-monitor",
        daemon=True,
    )
    monitor_thread.start()
    twin_viewer = YamBimanualViewer(lambda: controller.status().get("last_observation"))
    twin_viewer.start()
    csrf_token = secrets.token_urlsafe(32)
    page = PAGE.replace("__YAM_CSRF_TOKEN__", csrf_token)
    globals()["PAGE"] = page
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        build_handler(controller, credentials, csrf_token, args.provider, not args.no_key_prompt, args.camera_origin, twin_viewer),
    )
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Public YAM local runner: {url}")
    print("Session transport: " + ("in-process mock" if args.mock else args.session_api))
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor_stop.set()
        twin_viewer.stop()
        controller.disconnect("process_exit")
        credentials.clear()
        server.server_close()


if __name__ == "__main__":
    main()
