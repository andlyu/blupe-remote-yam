"""Public viewer configuration; camera/model capture remains independent."""
import json
import os
import re


def video_stream(robot_id):
    streams = json.loads(os.environ.get('YAM_VIDEO_STREAMS', '{}'))
    if not isinstance(streams, dict):
        raise ValueError('YAM_VIDEO_STREAMS must map robot IDs to streams')
    value = streams.get(robot_id, {'path':'synchronized', 'cameras':['top','observer','left','right']} if robot_id == 'yam-1' else None)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Video stream must specify path and cameras')
    path, cameras = value.get('path'), value.get('cameras')
    if not isinstance(path, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', path):
        raise ValueError('Video stream path must be a single safe path segment')
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 4 or any(c not in ('top','observer','left','right') for c in cameras) or len(set(cameras)) != len(cameras):
        raise ValueError('Video stream must list one to four unique camera roles in tile order')
    return {'path': path, 'cameras': list(cameras)}
