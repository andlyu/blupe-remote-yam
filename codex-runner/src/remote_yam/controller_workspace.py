"""Station-specific Cartesian envelopes from saved controller configuration.

These hash-pinned safety snapshots are not live configuration discovery. The
gateway still validates joints, swept poses, table clearance and collisions.
Guard common-frame origins must be removed before using per-arm move_to XYZ;
camera/IK base spacing can describe a different common frame.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np


PROFILE_ROOT = Path(__file__).resolve().parents[2] / 'config' / 'controller-workspaces'
YAM1_SAFETY_HASHES = frozenset({
    'fae92e6fc79b41dd0cc6226d90500767f72094d32279290424db1993e0e016f7',
    '9a305c10a44e1093ebd21d138e992443263edb8dc5c06c3004c62371f2d3c69f',
})
YAM1_CALIBRATION = '6b24647ab8fe4b87dcee2cfb0100b1df8a7b8133ac8839bb37b0ba1cf37260df'


@dataclass(frozen=True)
class ControllerWorkspace:
    robot_id: str
    profile: str
    safety_sha256: str
    guard_base_spacing_m: float
    model_include_sha256: str
    safety_hashes: frozenset

    def _safety(self):
        data = (PROFILE_ROOT / (self.profile + '.safety.json')).read_bytes()
        if hashlib.sha256(data).hexdigest() != self.safety_sha256:
            raise ValueError('Controller workspace safety snapshot hash mismatch')
        return json.loads(data)

    def bounds(self, arm):
        if arm not in ('left', 'right'):
            raise ValueError('Unknown workspace arm')
        cfg = self._safety()
        rest = np.array(cfg['rest_tip_m'][arm])
        w = cfg['workspace_m']
        left = arm == 'left'
        lower = rest - [w['backward'], w['inward'] if left else w['outward'], w['down']]
        upper = rest + [w['forward'], w['outward'] if left else w['inward'], w['up']]
        lower[2] = max(lower[2], cfg['table_z_m'] + w['minimum_table_clearance'])
        # YAM-1 guard bases are +/-0.35 m, even with camera/IK bases +/-0.25 m.
        # Robo-house guard bases are +/-0.31 m. Never use camera/IK origins here.
        origin = np.array([0., (1 if left else -1) * self.guard_base_spacing_m / 2, 0.])
        return np.array([lower - origin, upper - origin])

    def configure_geometry(self, geometry):
        for arm, start in (('left', 0), ('right', 7)):
            geometry.low[start:start+3], geometry.high[start:start+3] = self.bounds(arm)

    def validate_report(self, report):
        """Reject a different station or contradictory published provenance.

        Some camera reports (including Robo-house's) lack controller safety
        hashes. Such a report cannot verify the saved controller configuration.
        """
        if report.get('robot_id') != self.robot_id:
            raise ValueError('Workspace calibration robot mismatch')
        native = report.get('native_joint_calibration')
        if self.robot_id == 'yam-1' and native is not None:
            if (native.get('calibration_id') != YAM1_CALIBRATION
                    or native.get('coordinate_convention') != 'native_i2rt_updated'):
                raise ValueError('Workspace calibration does not match the known native joint convention')
        hashes = (report.get('provenance') or {}).get('selected_file_sha256') or {}
        if hashes and (hashes.get('safety') not in self.safety_hashes
                       or hashes.get('model') != '42d5f5b0213513e4bbda4645d4af513147ed492834fdb82ef5658ca3f05de18f'
                       or hashes.get('model_include') != self.model_include_sha256):
            raise ValueError('Workspace calibration does not match a known controller safety/model profile')

    def summary(self):
        return dict(profile=self.profile, robot_id=self.robot_id,
                    source='saved_controller_configuration', safety_sha256=self.safety_sha256,
                    model_include_sha256=self.model_include_sha256,
                    guard_base_spacing_m=self.guard_base_spacing_m,
                    live_configuration_verified=False, frame='per_arm_base', units='metres',
                    bounds={arm: self.bounds(arm).tolist() for arm in ('left', 'right')},
                    gateway_validation='Required for every hardware packet')


PROFILES = {
    'yam-1': ControllerWorkspace(
        'yam-1', 'native-both-20260929',
        '9a305c10a44e1093ebd21d138e992443263edb8dc5c06c3004c62371f2d3c69f', .70,
        'aaca9fb9a92583d10ea463359097439ab1e6eb9f445828cf85f488aa28b7e396',
        YAM1_SAFETY_HASHES),
    'robot-ba8413962083809c': ControllerWorkspace(
        'robot-ba8413962083809c', 'robohouse-updated-20261001',
        '172afc8a82d2b04fb03404e20ae5342a9272bdaae520b77e7caaa1e0c57bd965', .62,
        '22ed2648615c1e2fa8dfb0428e1b62d75a7a363374d6ce4b7bafe09ed5987edf',
        frozenset({'172afc8a82d2b04fb03404e20ae5342a9272bdaae520b77e7caaa1e0c57bd965'})),
}


def workspace_for_robot(robot_id):
    """Unknown stations keep the legacy conservative Cartesian contract."""
    return PROFILES.get(robot_id)
