"""Local run downloads. Model wire logs never leave this runner automatically."""
import hashlib
import json
import re
import tempfile
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .interactions import recording_directory

DATASET_URL = 'https://huggingface.co/datasets/andlyu/Public-YAM-runs'


def complete_rows(path):
    if not path.is_file() or path.is_symlink():
        return []
    result = []
    with path.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    result.append(row)
            except ValueError:
                continue  # A live writer may not have finished its last line.
    return result


def episode_for_run(root, run_id):
    directory = recording_directory(root, run_id)
    ids = {r.get('details', {}).get('episode_id')
           for r in complete_rows(directory / 'interactions.jsonl')}
    ids = {v for v in ids if isinstance(v, str) and re.fullmatch(r'ep_[A-Za-z0-9_-]+', v)}
    if len(ids) > 1:
        raise ValueError('Recording refers to multiple episodes')
    return next(iter(ids), None)


def run_artifacts(root, run_id):
    eid = episode_for_run(root, run_id)
    return {'episode_id': eid, 'dataset_url': DATASET_URL,
            'episode_url': f'{DATASET_URL}/blob/main/episodes/{eid}.json' if eid else None,
            'video_url': f'{DATASET_URL}/resolve/main/videos/{eid}.mp4?download=true' if eid else None}


def video_archive(root, run_id):
    url = run_artifacts(root, run_id)['video_url']
    if url is None:
        raise ValueError('No episode ID saved for this run. Older logs may not have an associated video.')
    target = tempfile.SpooledTemporaryFile(max_size=4*1024*1024)
    try:
        deadline = time.monotonic() + 120
        with urlopen(url, timeout=20) as response:
            size = 0
            while chunk := response.read(1024*1024):
                size += len(chunk)
                if size > 256*1024*1024 or time.monotonic() > deadline:
                    raise ValueError('Video download exceeded its size/time limit; use the HF dataset link.')
                target.write(chunk)
        target.seek(0)
        if b'ftyp' not in target.read(32):
            raise ValueError('Published file is not an MP4 video')
        target.seek(0)
        return target
    except HTTPError as exc:
        target.close()
        if exc.code == 404:
            raise ValueError('Video is not uploaded yet. It becomes available after the run ends and uploads; older runs may have no recording.') from exc
        raise ValueError('Video server contact issue. Try Save video again or use the HF dataset link.') from exc
    except (URLError, TimeoutError) as exc:
        target.close()
        raise ValueError('Video server contact issue. Try Save video again.') from exc
    except Exception:
        target.close()
        raise


def log_archive(root, run_id):
    """Export a consistent snapshot plus only the image blobs its wire rows reference."""
    directory = recording_directory(root, run_id)
    files = {name: complete_rows(directory/name) for name in ('interactions.jsonl', 'calls.jsonl')}
    if not any(files.values()):
        raise ValueError('No saved log for this run')
    archive = tempfile.SpooledTemporaryFile(max_size=4*1024*1024)
    try:
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            refs = set()
            for name, rows in files.items():
                if not rows:
                    continue
                text = ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows)
                output.writestr(name, text)
                refs.update(re.findall(r'\$blob:([a-f0-9]{64})', text))
            for digest in sorted(refs):
                blob = directory/'blobs'/digest
                if (blob.is_symlink() or blob.resolve().parent != (directory/'blobs').absolute()
                        or not blob.is_file()):
                    raise ValueError('A model input image is missing from this recording')
                data = blob.read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError('A recorded model image failed its checksum')
                output.writestr('blobs/'+digest, data)
            output.writestr('run.json', json.dumps({'run_id': run_id, **run_artifacts(root, run_id)}, indent=2))
            output.writestr('README.txt',
                'Saved run snapshot. calls.jsonl contains model request/response bodies and tool results; '
                'interactions.jsonl contains execution events and errors. Built-in runs have no model calls.\n'
                'Image strings ending in ;base64,$blob:<sha256> refer to files in blobs/. '
                'Replace $blob:<sha256> with the base64 encoding of that file to reconstruct the input.\n'
                'A snapshot taken during a run contains only completed log rows at download time. '
                'HTTP authorization headers and credentials are not recorded.\n')
        archive.seek(0)
        return archive
    except Exception:
        archive.close()
        raise


def recorded_image(root, run_id, digest):
    """Read only a verified JPEG/PNG blob inside this recording."""
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid image reference')
    directory = recording_directory(root, run_id)
    blobs = directory / 'blobs'
    path = blobs / digest
    if blobs.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError('Recorded image unavailable')
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Recorded image exceeds size limit')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Recorded image checksum mismatch')
    if data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
        return data, 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return data, 'image/png'
    raise ValueError('Unsupported recorded image')
