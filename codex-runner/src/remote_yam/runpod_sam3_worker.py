"""GPU-only SAM 3 RunPod handler using ASPIRE's Meta/Hugging Face model path.

Imports and weights are loaded once per worker. Image embeddings are shared
by all text prompts in a request, and outputs retain the supplied pixel grid.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
import hashlib
import io
import math
import os
import threading
import time

import numpy as np
from PIL import Image

MODEL = 'facebook/sam3'
MAX_PIXELS = 4_000_000


class TransformersSam3Engine:
    def __init__(self):
        import torch
        from transformers import Sam3Model, Sam3Processor
        if not torch.cuda.is_available():
            raise RuntimeError('SAM 3 worker requires an NVIDIA CUDA GPU')
        self.torch = torch
        reference = os.environ.get('SAM3_MODEL_PATH') or ('/models/sam3' if os.path.isdir('/models/sam3') else MODEL)
        local = os.path.isdir(reference)
        kwargs = {'local_files_only': True} if local else {}
        revision = os.environ.get('SAM3_MODEL_REVISION')
        if revision and not local:
            kwargs['revision'] = revision
        self.processor = Sam3Processor.from_pretrained(reference, **kwargs)
        self.model = Sam3Model.from_pretrained(reference, attn_implementation='sdpa', **kwargs).to('cuda').eval()
        self._bf16 = torch.cuda.is_bf16_supported()

    def infer(self, rgb, queries, threshold):
        torch = self.torch
        # Follow HF's efficient multi-prompt API: one image encoder pass.
        with torch.inference_mode(), (torch.autocast('cuda', dtype=torch.bfloat16) if self._bf16 else nullcontext()):
            image_inputs = self.processor(images=Image.fromarray(rgb), return_tensors='pt').to('cuda')
            vision = self.model.get_vision_features(pixel_values=image_inputs.pixel_values)
            predictions = {}
            for name, text in queries.items():
                text_inputs = self.processor(text=text, return_tensors='pt').to('cuda')
                output = self.model(vision_embeds=vision, **text_inputs)
                result = self.processor.post_process_instance_segmentation(output,
                    threshold=threshold, mask_threshold=.5, target_sizes=[rgb.shape[:2]])[0]
                scores = result['scores'].detach().float().cpu().numpy()
                if len(scores):
                    best = int(np.argmax(scores))
                    mask = result['masks'][best].detach().cpu().numpy().astype(bool)
                    predictions[name] = (mask, float(scores[best]), len(scores))
                else:
                    predictions[name] = (np.zeros(rgb.shape[:2], bool), 0., 0)
        torch.cuda.synchronize()
        return predictions

    def warmup(self):
        self.infer(np.zeros((480, 640, 3), np.uint8), {'warmup': 'object'}, .2)


def encode_object(mask, score, count, shape):
    mask = np.asarray(mask)
    if mask.shape != shape or not np.isin(mask, [0, 1]).all():
        raise ValueError('SAM 3 produced an invalid image-grid mask')
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('SAM 3 produced an invalid confidence score')
    mask = mask.astype(bool)
    ys, xs = np.where(mask)
    box = [int(xs.min()), int(ys.min()), int(xs.max()-xs.min()+1), int(ys.max()-ys.min()+1)] if len(xs) else [0, 0, 0, 0]
    buffer = io.BytesIO()
    Image.fromarray(mask.astype(np.uint8)*255).save(buffer, format='PNG')
    return dict(status='found' if len(xs) else 'not_found', score=score,
        bbox_xywh=box, detection_count=int(count),
        mask_png_base64=base64.b64encode(buffer.getvalue()).decode())


class Sam3Worker:
    def __init__(self, engine_factory=TransformersSam3Engine):
        self._factory = engine_factory
        self._engine = None
        self._lock = threading.Lock()
        self._startup_timing = {}

    def _ready(self):
        if self._engine is None:
            started = time.monotonic()
            engine = self._factory()
            loaded = time.monotonic()
            engine.warmup()
            self._startup_timing = dict(model_load_s=round(loaded-started, 4),
                model_warmup_s=round(time.monotonic()-loaded, 4))
            # Never claim readiness after a failed load or warmup.
            self._engine = engine

    def handle(self, job):
        payload = job.get('input') if isinstance(job, dict) else None
        if not isinstance(payload, dict):
            raise ValueError('SAM 3 job input must be an object')
        operation = payload.get('operation', 'segment')
        if operation not in ('warmup', 'segment'):
            raise ValueError('Unknown SAM 3 operation')
        if operation == 'warmup':
            with self._lock:
                self._ready()
                return dict(ready=True, model=MODEL, timing=dict(self._startup_timing))
        queries = payload.get('queries')
        if (not isinstance(queries, dict) or not 1 <= len(queries) <= 16
                or any(not isinstance(k, str) or not k.isidentifier()
                       or not isinstance(v, str) or not 0 < len(v.strip()) <= 500 for k, v in queries.items())):
            raise ValueError('SAM 3 requires 1 to 16 named text queries')
        threshold = payload.get('score_threshold', .2)
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('Invalid SAM 3 score threshold')
        encoded = payload.get('image_png_base64')
        if not isinstance(encoded, str) or not encoded or len(encoded) > 8_000_000:
            raise ValueError('Invalid SAM 3 RGB image encoding')
        raw = base64.b64decode(encoded, validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if payload.get('png_sha256') != digest:
            raise ValueError('SAM 3 RGB image integrity mismatch')
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if image.format != 'PNG' or image.mode != 'RGB' or width*height > MAX_PIXELS:
                raise ValueError('SAM 3 input must be bounded RGB PNG')
            rgb = np.asarray(image)
        with self._lock:
            self._ready()
            started = time.monotonic()
            predictions = self._engine.infer(rgb, queries, threshold)
            if set(predictions) != set(queries):
                raise ValueError('SAM 3 did not return all requested queries')
            inference_s = time.monotonic()-started
            objects = {name: encode_object(*prediction, rgb.shape[:2]) for name, prediction in predictions.items()}
            return dict(model=MODEL, width=width, height=height, png_sha256=digest,
                objects=objects, timing=dict(self._startup_timing, inference_s=round(inference_s, 4)))


def main():
    import runpod
    worker = Sam3Worker()
    # Startup and a real dummy inference complete before accepting jobs.
    worker.handle({'input': {'operation': 'warmup'}})
    runpod.serverless.start({'handler': worker.handle})


if __name__ == '__main__':
    main()
