import copy
import io
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from remote_yam.cameras import CameraUnavailable
from remote_yam.private_rgb import PrivateRgbSource, SERIALS


def fixture():
    source = PrivateRgbSource('http://127.0.0.1:18090', clock=lambda: 101.)
    source.set_calibration(dict(schema_version=1, robot_id='yam-1', cameras={
        name: dict(image_size=[8, 6]) for name in SERIALS}))
    data = io.BytesIO(); Image.new('RGB', (8, 6)).save(data, format='JPEG')
    headers = {'content-type': 'image/jpeg', 'x-robot-id': 'yam-1',
               'x-camera-role': 'left', 'x-camera-serial': SERIALS['left'],
               'x-captured-at': '100', 'content-length': str(len(data.getvalue()))}
    return source, data.getvalue(), headers


def test_capture_preserves_source_timestamp_and_checks_identity_size_freshness():
    source, data, headers = fixture()
    frame = source._decode('left', data, headers)
    assert frame.summary()['captured_at'] == 100
    assert frame.summary()['serial'] == SERIALS['left']
    for key, value in [('x-camera-role', 'right'), ('x-camera-serial', SERIALS['right']),
                       ('x-robot-id', 'other'), ('content-type', 'text/html')]:
        with pytest.raises(ValueError): source._decode('left', data, dict(headers, **{key: value}))
    for captured in ('98', '102', 'nan', 'inf'):
        with pytest.raises(CameraUnavailable): source._decode('left', data, dict(headers, **{'x-captured-at': captured}))
    source.calibration['cameras']['left']['image_size'] = [9, 6]
    with pytest.raises(ValueError): source._decode('left', data, headers)


def test_policy_uses_fixed_routes_and_never_fetches_observation_supplied_urls():
    source, data, left_headers = fixture()
    for name, connection in source.connections.items():
        response = MagicMock(status=200)
        headers = dict(left_headers, **{'x-camera-role': name, 'x-camera-serial': SERIALS[name]})
        response.getheaders.return_value = list(headers.items())
        response.read1.return_value = data
        connection.request = MagicMock()
        connection.getresponse = MagicMock(return_value=response)
    frames = source.capture({'robot_id': 'yam-1', 'images': {'left': {'url': 'https://untrusted.example/secret'}}})
    assert {f.name for f in frames} == set(SERIALS)
    for name, connection in source.connections.items():
        assert connection.request.call_args.args == ('GET', '/cameras/' + name + '.jpg')
    with pytest.raises(ValueError): source.capture({'robot_id': 'other'})
    for origin in ('http://user:secret@localhost', 'http://localhost/path', 'http://localhost?secret=1'):
        with pytest.raises(ValueError): PrivateRgbSource(origin)


def test_truncated_frame_closes_connection_and_stops_capture():
    source, data, headers = fixture()
    response = MagicMock(status=200)
    response.getheaders.return_value = list(headers.items())
    response.read1.side_effect = [data[:10], b'']
    with patch.object(source.connections['left'], 'request'), \
            patch.object(source.connections['left'], 'getresponse', return_value=response), \
            patch.object(source.connections['left'], 'close') as closed:
        with pytest.raises(CameraUnavailable) as failure: source._capture_one('left')
        assert failure.value.retryable is False
        closed.assert_called_once()


def test_depth_adapter_attaches_paired_top_and_local_wrists_in_order():
    from remote_yam.codex_depth_policy import CodexDepthAdapter
    from remote_yam.robocurve_policy import RoboCurveResponsesAdapter
    source, _, _ = fixture()
    provider = CodexDepthAdapter.__new__(CodexDepthAdapter)
    provider._camera_source = source
    provider._vision_frames = []
    provider._recorder = None
    provider.cancelled = lambda: False
    snapshot = MagicMock(calibration=source.calibration, metadata={'captured_at': 100, 'calibration_id': 'test'})
    snapshot.image.return_value = b'paired-png'
    provider.depth_api = MagicMock(); provider.depth_api.capture.return_value = snapshot
    parent_message = {'content': [{'type': 'input_text', 'text': 'state'},
                      {'type': 'input_image', 'image_url': 'left'},
                      {'type': 'input_image', 'image_url': 'right'}]}
    def parent(*args):
        assert provider._camera_names == ('left', 'right')
        return copy.deepcopy(parent_message)
    with patch.object(RoboCurveResponsesAdapter, '_observation_message', side_effect=parent):
        message = provider._observation_message('place', {'robot_id': 'yam-1'}, 1)
    images = [x['image_url'] for x in message['content'] if x['type'] == 'input_image']
    assert len(images) == 4
    assert images[0].startswith('data:image/png;base64,')
    assert images[1:3] == ['left', 'right']
    assert provider._camera_names == ('top', 'left', 'right')
