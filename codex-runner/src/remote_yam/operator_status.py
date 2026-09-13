"""Read public readiness metadata through the existing local operator tunnel."""
import json
import time
from urllib.request import urlopen


def read_auto_queue():
    # Fixed loopback destination, never supplied by a visitor. No controls exposed.
    try:
        with urlopen('http://127.0.0.1:18096/api/status', timeout=1) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            return None
        status = json.loads(raw)
        enabled = status['auto_queue']['enabled']
        if type(enabled) is not bool:
            return None
        return {'enabled': enabled, 'received_at': time.monotonic()}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def fresh_auto_queue(snapshot):
    if not snapshot or not 0 <= time.monotonic() - snapshot['received_at'] <= 3:
        return None
    return snapshot['enabled']
