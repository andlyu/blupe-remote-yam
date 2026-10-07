import copy
import io
import json
import struct
import unittest
import zipfile
from unittest.mock import patch

import numpy as np

from remote_yam.rgbd_native import encode, decode, reconstruct, legacy_bundle, FORMAT
from remote_yam.api_depth import ApiDepth, StaleDepth
from test_api_depth import fixture


def inputs():
    report, meta, _ = fixture()
    factory = dict(width=8, height=6, fx=100., fy=100., ppx=4., ppy=3.,
                   coeffs=[.01,-.001,.0002,.0003,0.], model='distortion.inverse_brown_conrady')
    meta.update(factory_color_intrinsics=factory, sensor_serial='sensor', depth_scale_m=.0001,
                T_color_depth=dict(rotation=np.eye(3).tolist(),translation_m=[.01,0,.0003]))
    report['cameras']['top'].update(depth_intrinsics=factory,depth_sensor_serial='sensor')
    native = np.arange(48,dtype=np.uint16).reshape(6,8)*1000
    native[-1,-1]=65535
    rgb=np.arange(144,dtype=np.uint8).reshape(6,8,3)
    depth=reconstruct(native,meta)
    # Exercise exact preservation of a compiler/SDK rounding difference.
    depth[2,3]=np.nextafter(depth[2,3],np.float32(np.inf))
    return report, meta, rgb, native, depth


def rewrite(body, **changes):
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        members={name:z.read(name) for name in z.namelist()}
    members.update(changes)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,data in members.items(): z.writestr(name,data)
    return out.getvalue()


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.report,self.meta,self.rgb,self.native,self.depth=inputs()
        self.body=encode(self.rgb,self.native,self.depth,self.meta)

    def test_exact_depth_with_source_scale_holes_patch_and_legacy_arrays(self):
        rgb,depth,meta=decode(self.body,[8,6])
        self.assertEqual(depth.tobytes(),self.depth.tobytes())
        self.assertEqual(meta['depth_scale_m'],.0001)
        self.assertEqual(meta['captured_at'],100.)
        with zipfile.ZipFile(io.BytesIO(self.body)) as z:
            self.assertEqual(len(z.read('depth-patches.bin')),8)
            self.assertEqual(struct.unpack('<II',z.read('depth-patches.bin'))[0],19)
        with np.load(io.BytesIO(legacy_bundle(rgb,depth,meta)),allow_pickle=False) as b:
            self.assertEqual(b['depth_m'].tobytes(),self.depth.tobytes())
            self.assertEqual(b['rgb'].tobytes(),rgb.tobytes())
            self.assertEqual(json.loads(b['metadata'].item())['rgb_encoding'],'jpeg')

    def test_bad_hash_patches_png_dimensions_and_geometry_rejected(self):
        with zipfile.ZipFile(io.BytesIO(self.body)) as z:
            meta=json.loads(z.read('metadata.json')); png=z.read('depth.png')
        cases=[{'depth-patches.bin':b'x'},
               {'depth-patches.bin':struct.pack('<II',48,0)},
               {'depth-patches.bin':struct.pack('<IIII',19,0,19,0)},
               {'depth-patches.bin':b''},
               {'depth.png':png[:16]+struct.pack('>II',100000,100000)+png[24:]}]
        for change in ({'depth_sha256':'0'*64}, {'depth_scale_m':0},
                       {'T_color_depth':dict(rotation=np.zeros((3,3)).tolist(),translation_m=[0,0,0])},
                       {'depth_quantization_m':.001}):
            cases.append({'metadata.json':json.dumps(dict(meta,**change)).encode()})
        for case in cases:
            with self.subTest(case=list(case)):
                with self.assertRaises(ValueError): decode(rewrite(self.body,**case),[8,6])

    def test_identity_profile_and_serial_must_match(self):
        for kwargs in (dict(expected={'camera':'left'}),dict(serial='wrong'),
                       dict(factory=dict(self.meta['factory_color_intrinsics'],fx=1))):
            with self.assertRaises(ValueError):decode(self.body,[8,6],**kwargs)

    def test_reader_uses_only_same_origin_advertised_path_and_keeps_age_limit(self):
        api=ApiDepth('https://api.example',clock=lambda:101.)
        url='/v1/robots/yam-1/cameras/top.rgbd-native.zip'
        self.report['cameras']['top']['depth']=dict(native_format=FORMAT,native_bundle_url=url)
        with patch.object(api,'read',return_value=self.body) as read:
            snap=api.capture(report=self.report)
        self.assertEqual(read.call_args.args[0],'/cameras/top.rgbd-native.zip')
        self.assertEqual(snap.depth.tobytes(),self.depth.tobytes())
        with np.load(io.BytesIO(snap.recording_bundle()),allow_pickle=False) as b:
            self.assertEqual(b['depth_m'].tobytes(),self.depth.tobytes())
        for url in ('https://evil.example/file','/v1/robots/other/cameras/top.rgbd-native.zip'):
            self.report['cameras']['top']['depth']['native_bundle_url']=url
            api._select_transport(self.report)
            self.assertEqual(api.depth_suffix,'/cameras/top.rgbd.npz')
        with self.assertRaises(StaleDepth):
            ApiDepth('https://api.example',clock=lambda:102.01).decode(self.body,self.report)

    def test_invalid_source_rejected_before_encoding(self):
        for value in (-1,np.inf,np.nan):
            depth=self.depth.copy(); depth[1,1]=value
            with self.assertRaises(ValueError):encode(self.rgb,self.native,depth,self.meta)


if __name__=='__main__':unittest.main()
