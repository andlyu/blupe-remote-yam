"""Exercise public probe entry points with synthetic data and no live API/model."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from test_api_depth import fixture, bundle
from remote_yam.api_depth import ApiDepth

SPEC = importlib.util.spec_from_file_location('astra_depth_example',
    Path(__file__).parents[1] / 'examples/astra_depth.py')
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


def args(folder, model_probe=False):
    return SimpleNamespace(output=str(folder), api='https://api.example', depth_url=None,
                           rgb_origin=None, all_depth_origin=None, model_probe=model_probe)


def snapshot():
    report, meta, depth = fixture()
    return ApiDepth('https://api.example', clock=lambda:101.).decode(bundle(meta, depth), report)


def test_capture_probe_writes_receipt_and_never_constructs_model_or_playground(tmp_path):
    with patch.object(example.ApiDepth, 'capture', return_value=snapshot()), \
            patch('remote_yam.codex_depth_policy.CodexDepthAdapter') as provider, \
            patch('local_playground.LocalPlayground') as playground:
        example.probe(args(tmp_path))
    provider.assert_not_called()
    playground.assert_not_called()
    receipt=json.loads((tmp_path/'receipt.json').read_text())
    assert receipt['depth_transport']=='PASS' and receipt['physical_run']=='UNVERIFIED'
    assert (tmp_path/'top.rgbd.npz').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('action',['done','move_to'])
def test_model_probe_accepts_observations_and_refuses_motion(tmp_path, action):
    provider=MagicMock()
    provider.depth_api.read.return_value=b'fixture-jpeg'
    provider._post_json.return_value={'output':[{'name':action,'arguments':'{}'}]}
    with patch.object(example.ApiDepth, 'capture', return_value=snapshot()), \
            patch('remote_yam.codex_depth_policy.CodexDepthAdapter', return_value=provider), \
            patch('local_playground.LocalPlayground') as playground:
        if action=='move_to':
            with pytest.raises(RuntimeError, match='not executed'):example.probe(args(tmp_path, True))
        else:
            example.probe(args(tmp_path, True))
            receipt=json.loads((tmp_path/'receipt.json').read_text())
            assert receipt['model_result']['action']=='done'
            assert receipt['physical_run']=='UNVERIFIED'
    assert provider._post_json.call_args.args[0]['tools']==[]
    provider._workspace.cleanup.assert_called_once()
    playground.assert_not_called()


@pytest.mark.parametrize('all_depth', [False, True])
def test_example_startup_uses_disabled_feedback_and_waits_for_missing_image(tmp_path, all_depth):
    from remote_yam.api_depth import NoDepthImage
    setup = SimpleNamespace(api='https://api.example', depth_url=None, rgb_origin=None,
        all_depth_origin='http://127.0.0.1:18090' if all_depth else None,
        active_arm='left', calibration_id='test', port=8793)
    report = {'calibration_id': 'test'}
    provider = MagicMock()
    provider.cancelled.return_value = False
    provider._camera_source.snapshots = {'top': SimpleNamespace(calibration=report)}
    capture = provider._camera_source.capture_preflight if all_depth else provider.depth_api.capture
    capture.side_effect = [NoDepthImage('top'), SimpleNamespace(calibration=report)]
    factories = []
    def init(app, **kwargs):
        app.monitor_api = MagicMock()
        app.monitor_api.get_robot_observation.return_value = dict(jetson_id='yam-1',
            source='hardware', mode='DISABLED', settled=False, observed_at=100.)
        factories.append(kwargs['api_depth_provider_factory'])
        app.camera_source = None
    with patch('local_playground.LocalPlayground.__init__', new=init), \
            patch('remote_yam.codex_depth_policy.CodexDepthAdapter', return_value=provider), \
            patch('uvicorn.run'), patch('remote_yam.depth_preflight.time.time', return_value=100.), \
            patch('remote_yam.depth_preflight.time.sleep'):
        example.serve(setup)
        assert factories[0]('codex', '', '', tmp_path) is provider
    assert capture.call_count == 2
    assert all(call.kwargs['stopped'] is True for call in capture.call_args_list)
    provider.set_depth_calibration.assert_called_once_with(report)
