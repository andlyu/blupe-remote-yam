"""RunPod Serverless SAM 3 transport, exact-image masks, and session warmup.

Only the hosted worker needs torch/transformers. Credentials stay in the
runner environment; requests contain an RGB image and text, never robot access.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib import error, request

import numpy as np
from PIL import Image
from .latency_spans import timed_span

MODEL = 'facebook/sam3'
MAX_PIXELS = 4_000_000
MAX_RESPONSE_BYTES = 16_000_000


def selected_backend(default='bundlesdf'):
    """Explicit environment override also reaches external ASPIRE subprocesses."""
    return os.environ.get('YAM_SEGMENTATION_BACKEND', default)


def segmentation_environment(config=None, *, environ=None):
    """Only the selected segmentation provider crosses station subprocesses.

    Config stores an endpoint and a key-file path; secret values never enter
    config, commands, public status, or saved launch receipts.
    """
    config = config or {}
    source = os.environ if environ is None else environ
    backend = source.get('YAM_SEGMENTATION_BACKEND', config.get('segmentation_backend'))
    if backend is None:
        return {}
    if backend not in ('astra', 'bundlesdf', 'runpod_sam3'):
        raise ValueError('Unknown segmentation backend; no silent fallback')
    result = {'YAM_SEGMENTATION_BACKEND': backend}
    if backend != 'runpod_sam3':
        return result
    settings = config.get('runpod_sam3', {})
    result['RUNPOD_SAM3_ENDPOINT_ID'] = source.get('RUNPOD_SAM3_ENDPOINT_ID', settings.get('endpoint_id', ''))
    result['RUNPOD_SAM3_TIMEOUT_S'] = str(source.get('RUNPOD_SAM3_TIMEOUT_S', settings.get('timeout_s', 600)))
    key = source.get('RUNPOD_API_KEY', '')
    if not key:
        key_path = Path(source.get('RUNPOD_API_KEY_FILE', settings.get('api_key_file', '~/.config/runpod/api-key'))).expanduser()
        if key_path.is_file():
            key = key_path.read_text().strip()
    if key:
        result['RUNPOD_API_KEY'] = key
    return result


def configured_vision_session(config):
    environment = segmentation_environment(config)
    if environment.get('YAM_SEGMENTATION_BACKEND') != 'runpod_sam3':
        return None
    return RunpodVisionSession(RunpodSam3Client.from_env(environment=environment))


class RunpodSam3Client:
    def __init__(self, endpoint_id, api_key, *, timeout_s=300, poll_s=.5,
                 cancelled=lambda: False, opener=None):
        if not isinstance(endpoint_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', endpoint_id):
            raise ValueError('Invalid RunPod SAM 3 endpoint identifier')
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('Set RUNPOD_API_KEY in the runner environment')
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 1800 or not 0 < poll_s <= 10:
            raise ValueError('Invalid RunPod SAM 3 timeout or polling interval')
        self.endpoint_id, self._api_key = endpoint_id, api_key
        self.timeout_s, self.poll_s = timeout_s, poll_s
        self.cancelled = cancelled
        self._opener = opener or request.urlopen
        self._url = 'https://api.runpod.ai/v2/'+endpoint_id
        self.last_timing = {}

    @classmethod
    def from_env(cls, *, environment=None, **kwargs):
        environment = os.environ if environment is None else environment
        kwargs.setdefault('timeout_s', float(environment.get('RUNPOD_SAM3_TIMEOUT_S', 600)))
        return cls(environment.get('RUNPOD_SAM3_ENDPOINT_ID', ''),
                   environment.get('RUNPOD_API_KEY', ''), **kwargs)

    def _http(self, operation, body=None, *, timeout_s=10):
        spans = getattr(self, '_http_spans', [])
        started_at, started = time.time(), time.perf_counter()
        encoded = None if body is None else json.dumps(body, allow_nan=False).encode()
        encoded_at = time.perf_counter()
        timing = dict(stage='http', operation=operation.split('/')[0], started_at=started_at,
            encode_s=encoded_at-started, request_bytes=len(encoded or b''), status='error')
        req = request.Request(self._url+'/'+operation,
            data=encoded,
            headers={'Authorization': 'Bearer '+self._api_key, 'Content-Type': 'application/json',
                     'User-Agent': 'Mozilla/5.0 (Robo-house SAM3 client)'},
            method='GET' if body is None else 'POST')
        try:
            with self._opener(req, timeout=timeout_s) as response:
                headers_at = time.perf_counter()
                timing['headers_s'] = headers_at-encoded_at
                raw = response.read(MAX_RESPONSE_BYTES+1)
                body_at = time.perf_counter()
                timing.update(body_s=body_at-headers_at, response_bytes=len(raw))
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('Oversized RunPod SAM 3 response')
            value = json.loads(raw)
            timing['decode_s'] = time.perf_counter()-body_at
            if not isinstance(value, dict):
                raise ValueError('RunPod SAM 3 response must be an object')
            timing['status'] = 'ok'
            return value
        except error.HTTPError as exc:
            raise RuntimeError(f'RunPod SAM 3 HTTP {exc.code}') from None
        except (error.URLError, OSError) as exc:
            # Arbitrary transport errors can contain URLs or headers.
            raise RuntimeError('RunPod SAM 3 connection failed: '+type(exc).__name__) from None
        finally:
            timing['duration_s'] = time.perf_counter()-started
            spans.append(timing)

    def call(self, payload):
        started = time.monotonic()
        deadline = started+self.timeout_s
        job_id = None
        result, status = {}, 'error'
        spans = self._http_spans = []
        def check_cancelled():
            with timed_span(spans, 'cancel_check'):
                if self.cancelled():
                    raise RuntimeError('RunPod SAM 3 request cancelled')
        try:
            check_cancelled()
            result = self._http('run', dict(input=payload,
                policy={'ttl': int(self.timeout_s*1000), 'executionTimeout': int(self.timeout_s*1000)}),
                timeout_s=min(10, self.timeout_s))
            job_id = result.get('id')
            if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', job_id):
                raise ValueError('RunPod SAM 3 did not return a valid job identifier')
            while True:
                check_cancelled()
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('RunPod SAM 3 job timed out')
                state = result.get('status')
                if state == 'COMPLETED':
                    output = result.get('output')
                    if not isinstance(output, dict) or 'error' in output:
                        raise ValueError('RunPod SAM 3 job returned invalid output')
                    status = 'ok'
                    return output
                if state not in ('IN_QUEUE', 'IN_PROGRESS'):
                    raise RuntimeError('RunPod SAM 3 job ended with status '+str(state))
                with timed_span(spans, 'poll_wait'):
                    time.sleep(min(self.poll_s, remaining))
                check_cancelled()
                result = self._http('status/'+job_id, timeout_s=min(10, max(.01, deadline-time.monotonic())))
        except BaseException:
            if job_id is not None:
                try:
                    self._http('cancel/'+job_id, {}, timeout_s=2)
                except Exception:
                    pass
            raise
        finally:
            self.last_timing = dict(elapsed_s=round(time.monotonic()-started, 4), status=status,
                delay_ms=result.get('delayTime'), execution_ms=result.get('executionTime'),
                worker_id=result.get('workerId'), spans=spans)

    def warmup(self):
        output = self.call({'operation': 'warmup'})
        if output.get('ready') is not True or output.get('model') != MODEL:
            raise ValueError('RunPod SAM 3 worker did not confirm model readiness')
        return output


class RunpodVisionSession:
    """One keeper per runner session, never one per camera or harness process."""
    def __init__(self, client=None, *, interval_s=20):
        if not 0 < interval_s <= 300:
            raise ValueError('Invalid RunPod keepalive interval')
        self.client = client or RunpodSam3Client.from_env()
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._failure = None
        self._cancelled = lambda: False
        self._emit = lambda *args, **kwargs: None
        self.ready_result = None
        self._started_at = self._ready_at = self._ended_at = None

    def public_status(self):
        state = ('stopped' if self._stop.is_set() else 'failed' if self._failure is not None
                 else 'ready' if self.ready_result is not None
                 else 'starting' if self._thread is not None else 'idle')
        return dict(model=MODEL, state=state, started_at=self._started_at,
                    ready_at=self._ready_at, ended_at=self._ended_at)

    def start(self, *, cancelled=lambda: False, emit=lambda *args, **kwargs: None):
        if self._thread is not None:
            return
        self._cancelled, self._emit = cancelled, emit
        self.client.cancelled = lambda: self._stop.is_set() or cancelled()
        self._started_at = time.time()
        emit('tool_request', 'Starting SAM 3 vision worker')
        self._thread = threading.Thread(target=self._keep_warm, name='runpod-sam3-session', daemon=True)
        self._thread.start()

    def _keep_warm(self):
        try:
            self.ready_result = self.client.warmup()
            self._ready_at = time.time()
            self._emit('tool_result', 'SAM 3 vision ready', timing=self.client.last_timing,
                       worker_timing=self.ready_result.get('timing'))
            self._ready.set()
            while not self._stop.wait(self.interval_s):
                if self._cancelled():
                    return
                self.client.warmup()
        except Exception as exc:
            self._failure = exc
            self._ended_at = time.time()
            if not self._stop.is_set() and not self._cancelled():
                self._emit('tool_result', 'SAM 3 vision worker unavailable', reason=str(exc))
        finally:
            self._ready.set()

    def wait_ready(self):
        if self._thread is None:
            raise RuntimeError('SAM 3 warmup has not started')
        while not self._ready.wait(.05):
            if self._cancelled() or self._stop.is_set():
                raise RuntimeError('SAM 3 warmup cancelled')
        if self._cancelled() or self._stop.is_set():
            raise RuntimeError('SAM 3 warmup cancelled')
        if self._failure is not None:
            raise self._failure
        return self.ready_result

    def close(self):
        if self._ended_at is None:
            self._ended_at = time.time()
        self._stop.set()


def decode_object(value, width, height):
    if not isinstance(value, dict) or value.get('status') not in ('found', 'not_found'):
        raise ValueError('Invalid SAM 3 object status')
    score = value.get('score')
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Invalid SAM 3 confidence')
    encoded = value.get('mask_png_base64')
    if not isinstance(encoded, str) or len(encoded) > 8_000_000:
        raise ValueError('Invalid SAM 3 mask encoding')
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != 'PNG' or image.size != (width, height) or image.mode != 'L':
            raise ValueError('SAM 3 mask does not match the original image grid')
        mask = np.asarray(image)
    if not np.isin(mask, [0, 255]).all():
        raise ValueError('SAM 3 mask must be binary')
    mask = mask == 255
    ys, xs = np.where(mask)
    box = [int(xs.min()), int(ys.min()), int(xs.max()-xs.min()+1), int(ys.max()-ys.min()+1)] if len(xs) else [0, 0, 0, 0]
    if value.get('bbox_xywh') != box or (value['status'] == 'found') != bool(len(xs)):
        raise ValueError('SAM 3 mask bounds/status mismatch')
    return dict(mask=mask, score=score, bbox_xywh=box, status=value['status'],
                explanation='SAM 3 highest-scoring matching instance')


class RunpodSam3Segmenter:
    def __init__(self, queries, directory, *, client=None, cancelled=lambda: False):
        queries = dict(queries or {})
        if (len(queries) > 16 or any(not isinstance(k, str) or not k.isidentifier()
                or not isinstance(v, str) or not v.strip() for k, v in queries.items())):
            raise ValueError('SAM 3 needs at most 16 named, explicit text queries')
        self.queries, self.directory = queries, Path(directory)
        self.cancelled = cancelled
        self.client = client or RunpodSam3Client.from_env(cancelled=cancelled)
        self._cache = None
        self._sequence = 0
        self._lock = threading.Lock()

    def segment(self, image, query):
        with self._lock:
            started_at, started = time.time(), time.perf_counter()
            self._call_spans = []
            self._request_receipt = None
            self._call_cache_hit = False
            try:
                result = self._segment(image, query)
            finally:
                self.last_call_timing = dict(started_at=started_at,
                    duration_s=time.perf_counter()-started, cache_hit=self._call_cache_hit,
                    spans=self._call_spans)
                if self._request_receipt is not None:
                    path, info = self._request_receipt
                    info['call_timing'] = self.last_call_timing
                    path.write_text(json.dumps(info, allow_nan=False, indent=2)+'\n')
            return dict(result, call_timing=self.last_call_timing)

    def _segment(self, image, query):
        with timed_span(self._call_spans, 'initial_cancel_check'):
            if self.cancelled():
                raise RuntimeError('SAM 3 segmentation cancelled')
        rgb = np.asarray(image)
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or not rgb.size or rgb.shape[0]*rgb.shape[1] > MAX_PIXELS:
            raise ValueError('SAM 3 requires bounded uint8 HxWx3 RGB')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('SAM 3 query must be a nonempty string')
        target = next((k for k, v in self.queries.items() if v == query), None)
        if target is None:
            if len(self.queries) >= 16:
                raise ValueError('SAM 3 query registry exceeds 16 targets')
            target = 'query_'+hashlib.sha256(query.encode()).hexdigest()[:16]
            self.queries[target] = query
            self._cache = None
        digest = hashlib.sha256(rgb.tobytes()+str(rgb.shape).encode()).hexdigest()
        if self._cache is None or self._cache[0] != digest:
            self._sequence += 1
            directory = self.directory/f'{self._sequence:03d}-{digest[:12]}'
            directory.mkdir(parents=True, exist_ok=False)
            buffer = io.BytesIO()
            with timed_span(self._call_spans, 'encode_image_png'):
                Image.fromarray(rgb).save(buffer, format='PNG')
            png = buffer.getvalue()
            (directory/'image.png').write_bytes(png)
            png_digest = hashlib.sha256(png).hexdigest()
            height, width = rgb.shape[:2]
            info = dict(model=MODEL, image_sha256=digest, png_sha256=png_digest,
                image_width=width, image_height=height, queries=dict(self.queries),
                started_at=time.time(), robot_commands_sent=0, proposals_not_ground_truth=True)
            self._request_receipt = (directory/'request.json', info)
            try:
                with timed_span(self._call_spans, 'remote_request'):
                    output = self.client.call(dict(operation='segment', image_png_base64=base64.b64encode(png).decode(),
                        png_sha256=png_digest, queries=self.queries, score_threshold=.2))
                (directory/'response.json').write_text(json.dumps(output, allow_nan=False, indent=2)+'\n')
                if (output.get('model') != MODEL or output.get('png_sha256') != png_digest
                        or type(output.get('width')) is not int or output['width'] != width
                        or type(output.get('height')) is not int or output['height'] != height
                        or not isinstance(output.get('objects'), dict)
                        or set(output['objects']) != set(self.queries)):
                    raise ValueError('SAM 3 response image/query identity mismatch')
                with timed_span(self._call_spans, 'decode_masks'):
                    masks = {name: decode_object(value, width, height) for name, value in output['objects'].items()}
                with timed_span(self._call_spans, 'save_masks'):
                    for name, item in masks.items():
                        Image.fromarray(item['mask'].astype(np.uint8)*255).save(directory/(name+'-mask.png'))
                info.update(status='VALID_MASKS', timing=self.client.last_timing, worker_timing=output.get('timing'))
                self._cache = (digest, masks)
            except Exception as exc:
                info.update(status='REJECTED', reason=str(exc), timing=self.client.last_timing)
                raise
            finally:
                info['elapsed_s'] = round(time.time()-info['started_at'], 4)
        else:
            self._call_cache_hit = True
        with timed_span(self._call_spans, 'final_cancel_check'):
            if self.cancelled():
                raise RuntimeError('SAM 3 segmentation cancelled')
        item = self._cache[1][target]
        return dict(item, mask=item['mask'].copy(), bbox_xywh=list(item['bbox_xywh']),
            segmentation_backend='runpod_sam3', model=MODEL, image_sha256=digest,
            proposals_not_ground_truth=True)
