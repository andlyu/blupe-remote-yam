"""Validated Astra contour proposals; these are not native dense segmentation.

Vision/structured output: developers.openai.com/api/docs/models/gpt-6-astra
Transport: existing codex_policy.CodexAdapter, codex-cli >=0.154.0.
Coordinates always refer to the exact original RGB pixel grid, never world XYZ.
"""
from __future__ import annotations

import math
import base64
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np

MODEL = 'gpt-6-astra'


def contour_schema(targets):
    point = dict(type='object', additionalProperties=False,
        properties={key: dict(type='number') for key in ('x', 'y')}, required=['x', 'y'])
    ring = dict(type='array', items=point, minItems=3)
    polygon = dict(type='object', additionalProperties=False,
        properties=dict(outer=ring, holes=dict(type='array', items=ring)),
        required=['outer', 'holes'])
    obj = dict(type='object', additionalProperties=False, properties=dict(
        status=dict(type='string', enum=['found', 'not_found', 'ambiguous']),
        confidence=dict(type='number'), explanation=dict(type='string'),
        polygons=dict(type='array', items=polygon)),
        required=['status', 'confidence', 'explanation', 'polygons'])
    properties = dict(image_width=dict(type='integer'), image_height=dict(type='integer'),
        coordinate_system=dict(type='string', enum=['pixel_xy_top_left']))
    properties.update({target: obj for target in targets})
    return dict(type='object', additionalProperties=False, properties=properties, required=list(properties))


def codex_contour_request(image_png, schema, prompt, timeout_s):
    """Reuse existing authorized subscription transport, with all agent tools disabled."""
    from .codex_policy import CodexAdapter
    provider = CodexAdapter(model=MODEL, timeout_s=timeout_s)
    provider._expected_camera_count = 1
    provider._decision_schema = schema
    provider._decision_instructions = ('You are a vision contour proposal component. Return only the requested '
        'JSON. Do not use shell, web, files, other tools or robot actions.')
    provider._decision_response = lambda value: value
    provider.reasoning_effort = 'medium'
    provider.response_speed = 'standard'
    try:
        response = provider._post_json(dict(instructions=prompt, tools=[], input=[dict(role='user', content=[
            dict(type='input_text', text=prompt), dict(type='input_image', detail='high',
                image_url='data:image/png;base64,'+base64.b64encode(image_png).decode())])]))
        return response, dict(model=MODEL, transport='runner_codex_subscription',
            model_conversation_id=provider._thread_id, reasoning_effort='medium', response_speed='standard')
    finally:
        provider._workspace.cleanup()


def immutable_scene_contour_request(roots, frames, queries, *, fallback=codex_contour_request):
    """Reuse original proposals only within one exact recorded capture.

    Capture metadata separates a new observation even if its RGB is identical.
    Each query is matched literally; changed queries/pixels call the model.
    Callers must enable this only for motion-disabled recorded snapshots.
    """
    roots = [Path(root) for root in roots]

    def request(image_png, schema, prompt, timeout_s):
        png_digest = hashlib.sha256(image_png).hexdigest()
        cached, origins = {}, {}
        paths = sorted({path for root in roots for path in root.glob('**/astra/**/request.json')})
        for path in paths:
            info = json.loads(path.read_text())
            if (info.get('status') != 'VALID_PROPOSALS' or info.get('png_sha256') != png_digest
                    or info.get('model') != MODEL):
                continue
            snapshot = path.parents[3]/'scene/snapshot.json'
            if not snapshot.is_file() or json.loads(snapshot.read_text()).get('frames') != frames:
                continue
            if hashlib.sha256(path.with_name('image.png').read_bytes()).hexdigest() != png_digest:
                raise ValueError('Recorded contour image integrity mismatch')
            previous_queries = info['queries']
            response = json.loads(path.with_name('response.json').read_text())
            rasterize_response(response, response['image_width'], response['image_height'], previous_queries)
            for name, query in queries.items():
                previous = next((key for key, value in previous_queries.items() if value == query), None)
                if name not in cached and previous is not None:
                    cached[name] = response[previous]
                    origins[name] = dict(request=str(path), original_target=previous,
                        image_sha256=info['image_sha256'], png_sha256=png_digest,
                        model_conversation_id=info.get('model_conversation_id'))
        missing = {key: value for key, value in queries.items() if key not in cached}
        raw, transport = None, {}
        if missing:
            requested_prompt = prompt.rsplit('Targets: ', 1)[0]+'Targets: '+json.dumps(missing)
            raw, transport = fallback(image_png, contour_schema(missing), requested_prompt, timeout_s)
            rasterize_response(raw, raw['image_width'], raw['image_height'], missing)
        response = {}
        if raw is not None:
            response.update({key: raw[key] for key in ('image_width', 'image_height', 'coordinate_system')})
        else:
            from PIL import Image
            width, height = Image.open(io.BytesIO(image_png)).size
            response.update(image_width=width, image_height=height, coordinate_system='pixel_xy_top_left')
        response.update({name: cached[name] if name in cached else raw[name] for name in queries})
        if origins:
            transport = dict(transport,
                transport='validated_exact_capture_query_replay' if not missing else 'validated_exact_capture_partial_replay',
                model=MODEL, cached_query_provenance=origins, fresh_queries=missing,
                raw_model_response=raw)
        return response, transport
    return request


class AstraContourSegmenter:
    """One bounded model call per exact RGB image; all registered objects proposed together."""
    def __init__(self, queries, directory, *, request=None, timeout_s=120):
        if (not isinstance(queries, dict) or not queries
                or any(not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str)
                       or not v.strip() for k, v in queries.items()) or len(set(queries.values())) != len(queries)):
            raise ValueError('Astra needs a unique, explicit target query registry')
        self.queries, self.directory, self.request = dict(queries), Path(directory), request or codex_contour_request
        self.timeout_s, self._cache, self._sequence = timeout_s, None, 0

    def segment(self, image, query):
        from PIL import Image
        rgb = np.asarray(image)
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.size > 12_000_000:
            raise ValueError('Astra segmentation requires bounded uint8 HxWx3 RGB')
        target = next((key for key, value in self.queries.items() if value == query), None)
        if target is None:
            if not isinstance(query, str) or not query.strip():
                raise ValueError('Astra query must be a nonempty string')
            target = 'query_'+hashlib.sha256(query.encode()).hexdigest()[:16]
            self.queries[target] = query
            self._cache = None
        height, width = rgb.shape[:2]
        digest = hashlib.sha256(rgb.tobytes()+str(rgb.shape).encode()).hexdigest()
        if self._cache is None or self._cache[0] != digest:
            self._sequence += 1
            directory = self.directory/f'{self._sequence:03d}-{digest[:12]}'
            directory.mkdir(parents=True, exist_ok=False)
            buffer = io.BytesIO()
            Image.fromarray(rgb).save(buffer, format='PNG')
            image_png = buffer.getvalue()
            (directory/'image.png').write_bytes(image_png)
            schema = contour_schema(self.queries)
            prompt = (f'Propose visible-pixel segmentation on this exact {width}x{height} RGB image. '
                f'Original pixel coordinates: x columns [0,{width-1}], y rows [0,{height-1}], origin top left. '
                'Do not resize, normalize or repeat the closing vertex. Trace detailed simple polygon rings. '
                'Holes must be wholly inside their outer ring; components must be disjoint. Exclude occluders '
                'with holes or concavities. Do not infer hidden pixels, robot actions or world positions. '
                'Confidence is an uncalibrated proposal score, not ground truth. '
                'Report absent/ambiguous objects instead of guessing. Follow each target query\'s spatial scope; '
                'a source support patch and a destination support patch may be in different places. For a '
                'queried support region, include the visible surface adjacent to the specified object while '
                'excluding objects and grippers. Targets: '+json.dumps(self.queries))
            (directory/'schema.json').write_text(json.dumps(schema, indent=2)+'\n')
            (directory/'prompt.txt').write_text(prompt+'\n')
            info = dict(model=MODEL, image_sha256=digest, png_sha256=hashlib.sha256(image_png).hexdigest(),
                image_width=width, image_height=height, queries=self.queries, started_at=time.time(),
                robot_commands_sent=0, proposals_not_ground_truth=True)
            (directory/'request.json').write_text(json.dumps(info, indent=2)+'\n')
            try:
                response, transport = self.request(image_png, schema, prompt, self.timeout_s)
                raw_response = transport.pop('raw_model_response', None)
                if raw_response is not None:
                    (directory/'model-response.json').write_text(json.dumps(raw_response, indent=2, allow_nan=False)+'\n')
                (directory/'response.json').write_text(json.dumps(response, indent=2, allow_nan=False)+'\n')
                masks = rasterize_response(response, width, height, self.queries)
                save_masks_and_overlay(rgb, masks, directory)
                info.update(transport, status='VALID_PROPOSALS', elapsed_s=round(time.time()-info['started_at'], 3),
                    objects={name: dict(confidence=item['score'], mask_area=int(item['mask'].sum()),
                        bbox_xywh=item['bbox_xywh']) for name, item in masks.items()})
                self._cache = (digest, masks)
            except Exception as exc:
                info.update(status='REJECTED', reason=str(exc), elapsed_s=round(time.time()-info['started_at'], 3))
                raise
            finally:
                (directory/'request.json').write_text(json.dumps(info, indent=2)+'\n')
        item = self._cache[1][target]
        return dict(mask=item['mask'].copy(), score=item['score'], bbox_xywh=list(item['bbox_xywh']),
            status=item['status'], explanation=item['explanation'],
            segmentation_backend='astra_contour_proposals', model=MODEL, image_sha256=digest,
            proposals_not_ground_truth=True)


def _cross(a, b, c):
    return float((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))


def _on_segment(a, b, p):
    return (abs(_cross(a, b, p)) <= 1e-7
            and np.all(p >= np.minimum(a, b)-1e-7)
            and np.all(p <= np.maximum(a, b)+1e-7))


def _touch(a, b, c, d):
    x, y, z, w = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    return ((x*y < 0 and z*w < 0) or _on_segment(a, b, c) or _on_segment(a, b, d)
            or _on_segment(c, d, a) or _on_segment(c, d, b))


def _edges(ring):
    return list(zip(ring, np.roll(ring, -1, axis=0)))


def _rings_touch(first, second):
    return any(_touch(a, b, c, d) for a, b in _edges(first) for c, d in _edges(second))


def _inside(point, ring):
    import cv2
    return cv2.pointPolygonTest(ring.astype(np.float32), tuple(map(float, point)), False) > 0


def _ring(value, width, height):
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError('Contour must contain at least three vertices')
    for point in value:
        if not isinstance(point, dict) or set(point) != {'x', 'y'}:
            raise ValueError('Contour vertices must be pixel x/y objects')
        if any(type(point[k]) not in (int, float) or not math.isfinite(point[k]) for k in ('x', 'y')):
            raise ValueError('Contour coordinates must be finite numbers')
    ring = np.array([[p['x'], p['y']] for p in value], dtype=float)
    if (np.any(ring < 0) or np.any(ring[:, 0] > width-1) or np.any(ring[:, 1] > height-1)):
        raise ValueError('Contour leaves the original image pixel grid')
    if len(set(map(tuple, ring))) != len(ring):
        raise ValueError('Contour contains duplicate vertices')
    area = abs(np.sum(ring[:, 0]*np.roll(ring[:, 1], -1)-ring[:, 1]*np.roll(ring[:, 0], -1)))/2
    if area == 0:
        raise ValueError('Contour is degenerate')
    edges = _edges(ring)
    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges):
            if j > i+1 and not (i == 0 and j == len(edges)-1) and _touch(a, b, c, d):
                raise ValueError('Contour self-intersects or touches itself')
    return ring


def rasterize_response(response, width, height, targets):
    """Rasterize structurally valid proposals; confidence/status remain evidence."""
    import cv2
    expected = {'image_width', 'image_height', 'coordinate_system', *targets}
    if not isinstance(response, dict) or set(response) != expected:
        raise ValueError('Astra contour response has incorrect target/schema keys')
    if (type(response['image_width']) is not int or type(response['image_height']) is not int
            or response['image_width'] != width or response['image_height'] != height
            or response['coordinate_system'] != 'pixel_xy_top_left'):
        raise ValueError('Astra contour image dimensions/coordinate convention mismatch')
    results = {}
    for name in targets:
        obj = response[name]
        if not isinstance(obj, dict) or set(obj) != {'status', 'confidence', 'explanation', 'polygons'}:
            raise ValueError('Invalid object proposal: '+name)
        score = obj['confidence']
        if (obj['status'] not in ('found', 'not_found', 'ambiguous') or type(score) not in (int, float)
                or not math.isfinite(score) or not 0 <= score <= 1
                or not isinstance(obj['explanation'], str)):
            raise ValueError('Invalid Astra proposal status/score: '+name)
        polygons = obj['polygons']
        if not isinstance(polygons, list):
            raise ValueError('Invalid polygon component count: '+name)
        mask = np.zeros((height, width), np.uint8)
        outers = []
        for polygon in polygons:
            if not isinstance(polygon, dict) or set(polygon) != {'outer', 'holes'}:
                raise ValueError('Invalid polygon schema')
            outer = _ring(polygon['outer'], width, height)
            if any(_rings_touch(outer, previous) or _inside(outer[0], previous)
                   or _inside(previous[0], outer) for previous in outers):
                raise ValueError('Polygon components overlap or nest')
            outers.append(outer)
            holes = polygon['holes']
            if not isinstance(holes, list):
                raise ValueError('Invalid hole count')
            component = np.zeros_like(mask)
            cv2.fillPoly(component, [np.rint(outer).astype(np.int32)], 1)
            checked_holes = []
            for value in holes:
                hole = _ring(value, width, height)
                if not all(_inside(p, outer) for p in hole) or _rings_touch(hole, outer):
                    raise ValueError('Hole leaves or touches outer contour')
                if any(_rings_touch(hole, previous) or _inside(hole[0], previous)
                       or _inside(previous[0], hole) for previous in checked_holes):
                    raise ValueError('Holes overlap or nest')
                checked_holes.append(hole)
                cv2.fillPoly(component, [np.rint(hole).astype(np.int32)], 0)
            if np.any(mask & component):
                raise ValueError('Rasterized polygon components overlap')
            mask |= component
        y, x = np.where(mask)
        result = dict(mask=mask.astype(bool), score=float(score),
            status=obj['status'], explanation=obj['explanation'],
            bbox_xywh=[int(x.min()), int(y.min()), int(x.max()-x.min()+1), int(y.max()-y.min()+1)]
                if len(x) else [0, 0, 0, 0])
        results[name] = result
    return results


def save_masks_and_overlay(image, masks, directory):
    """Inspection artifacts only; no claim of pixel-accurate ground truth."""
    import cv2
    from PIL import Image
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(image)
    overlay = rgb.copy()
    palette = [(255, 100, 0), (0, 180, 255), (190, 0, 230)]
    for index, (name, item) in enumerate(masks.items()):
        mask = item['mask']
        color = np.array(palette[index % len(palette)], dtype=np.uint8)
        overlay[mask] = np.rint(.75*overlay[mask]+.25*color).astype(np.uint8)
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(overlay, contours, -1, tuple(map(int, color)), 2)
        x, y, _, _ = item['bbox_xywh']
        cv2.putText(overlay, name, (x, max(15, y-6)), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 3)
        cv2.putText(overlay, name, (x, max(15, y-6)), cv2.FONT_HERSHEY_SIMPLEX, .5, tuple(map(int, color)), 1)
        np.save(directory/(name+'.mask.npy'), mask)
        Image.fromarray(mask.astype(np.uint8)*255).save(directory/(name+'.mask.png'))
    Image.fromarray(overlay).save(directory/'overlay.png')
