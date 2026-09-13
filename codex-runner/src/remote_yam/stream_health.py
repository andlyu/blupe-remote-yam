"""Strictly bounded, anonymous browser stream-health event logging."""
import logging
import math

LOGGER = logging.getLogger(__name__)
STATES = {'connecting', 'live', 'delayed', 'behind', 'stalled', 'paused'}


def public_report(payload):
    if not isinstance(payload, dict) or set(payload) != {'state', 'transport', 'frame_age_ms', 'delay_ms', 'fps'}:
        raise ValueError('Invalid stream health report')
    if (not isinstance(payload['state'], str) or not isinstance(payload['transport'], str)
            or payload['state'] not in STATES or payload['transport'] not in {'unknown', 'webrtc', 'hls'}):
        raise ValueError('Invalid stream health state')
    for key, maximum in [('fps', 240), ('frame_age_ms', 86400000), ('delay_ms', 86400000)]:
        value = payload[key]
        if value is None and key != 'fps':
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError('Invalid stream health measurement')
    return dict(payload)


def log_report(payload):
    report = public_report(payload)
    LOGGER.warning('[stream-health] state=%s transport=%s fps=%s frame_age_ms=%s delay_ms=%s',
                   report['state'], report['transport'], report['fps'], report['frame_age_ms'], report['delay_ms'])
    return report
