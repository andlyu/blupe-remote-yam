import io
import json
from unittest.mock import patch

from remote_yam.operator_status import read_auto_queue, fresh_auto_queue


def test_auto_queue_flag_is_allowlisted_and_expires():
    for enabled in (True, False):
        response = io.BytesIO(json.dumps({'auto_queue': {'enabled': enabled},
                                         'private': 'not public'}).encode())
        with patch('remote_yam.operator_status.urlopen', return_value=response), \
             patch('remote_yam.operator_status.time.monotonic', return_value=10):
            snapshot = read_auto_queue()
            assert snapshot == {'enabled': enabled, 'received_at': 10}
            assert fresh_auto_queue(snapshot) is enabled
        with patch('remote_yam.operator_status.time.monotonic', return_value=14):
            assert fresh_auto_queue(snapshot) is None


def test_unavailable_or_invalid_flag_stays_unknown():
    with patch('remote_yam.operator_status.urlopen', side_effect=OSError):
        assert read_auto_queue() is None
    for payload in ({}, {'auto_queue': {'enabled': 'true'}}):
        with patch('remote_yam.operator_status.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            assert read_auto_queue() is None
