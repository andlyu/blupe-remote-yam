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
