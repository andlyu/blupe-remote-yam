"""Versioned JPEG + native Z16 PNG transport with exact optical-depth recovery.

See docs/native-depth-contract.md and librealsense v2.58.1 rs.cpp:4294–4339.
SDK/compiler FMA differences are carried as bounded sparse float-bit patches;
the decoded optical-depth SHA256 must match the original capture. No SDK,
camera ownership, robot state or network access is required by this module.
"""
from functools import lru_cache
import hashlib
import io
import json
import math
import re
import struct
import zipfile

import numpy as np
from PIL import Image

FORMAT = 'rgbd-jpeg-z16-png-v1'
SUFFIX = '.rgbd-native.zip'
MAX_BYTES = 16_000_000
MAX_PIXELS = 2_000_000
MEMBERS = frozenset({'rgb.jpg', 'depth.png', 'metadata.json', 'depth-patches.bin'})


def _dimensions(size):
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or any(type(n) is not int or n <= 0 for n in size)
            or math.prod(size) > MAX_PIXELS):
        raise ValueError('Invalid native RGB-D dimensions')
    return tuple(size)


def _geometry_key(meta, size):
    w,h = _dimensions(size)
    p = meta['factory_color_intrinsics']
    if (p['width'],p['height']) != (w,h):
        raise ValueError('Native factory dimensions differ from calibration')
    scalars = np.asarray([p[k] for k in ('fx','fy','ppx','ppy')],dtype=float)
    coeffs = np.asarray(p['coeffs'],dtype=float)
    rotation = np.asarray(meta['T_color_depth']['rotation'],dtype=float)
    translation = np.asarray(meta['T_color_depth']['translation_m'],dtype=float)
    scale = meta['depth_scale_m']
    if (not np.isfinite(scalars).all() or np.any(scalars[:2] <= 0)
            or coeffs.shape != (5,) or not np.isfinite(coeffs).all()
            or p['model'] not in ('distortion.none','distortion.inverse_brown_conrady','distortion.brown_conrady')
            or (p['model']=='distortion.none' and np.any(coeffs != 0))
            or rotation.shape != (3,3) or not np.isfinite(rotation).all()
            or not np.allclose(rotation.T@rotation,np.eye(3),atol=2e-5)
            or not np.isclose(np.linalg.det(rotation),1.,atol=2e-5)
            or translation.shape != (3,) or not np.isfinite(translation).all()
            or np.any(abs(translation)>1)
            or type(scale) not in (int,float) or not math.isfinite(scale) or not 0 < scale <= .1):
        raise ValueError('Invalid native depth geometry')
    return json.dumps({k:meta[k] for k in ('factory_color_intrinsics','T_color_depth','depth_scale_m')},
                      sort_keys=True,allow_nan=False)


@lru_cache(maxsize=6)
def _geometry(key):
    meta = json.loads(key)
    p = meta['factory_color_intrinsics']; f = np.float32
    yy,xx = np.indices((p['height'],p['width']),dtype=np.float32)
    x=(xx-f(p['ppx']))/f(p['fx']); y=(yy-f(p['ppy']))/f(p['fy'])
    xo,yo=x.copy(),y.copy()
    k1,k2,p1,p2,k3=np.asarray(p['coeffs'],dtype=np.float32)
    if np.any(abs(np.asarray(p['coeffs'],dtype=np.float32))>=np.finfo(np.float32).eps):
        for _ in range(10):
            r2=x*x+y*y
            inverse=f(1)/(f(1)+((k3*r2+k2)*r2+k1)*r2)
            xq,yq=(x/inverse,y/inverse) if p['model']=='distortion.inverse_brown_conrady' else (x,y)
            dx=f(2)*p1*xq*yq+p2*(r2+f(2)*xq*xq)
            dy=f(2)*p2*xq*yq+p1*(r2+f(2)*yq*yq)
            x,y=(xo-dx)*inverse,(yo-dy)*inverse
    axis=np.asarray(meta['T_color_depth']['rotation'],dtype=np.float32)[:,2]
    t=np.asarray(meta['T_color_depth']['translation_m'],dtype=np.float32)
    # Separate float32 operations are part of the wire contract. No BLAS/FMA.
    denominator=(x*axis[0]+y*axis[1])+axis[2]
    offset=(axis[0]*t[0]+axis[1]*t[1])+axis[2]*t[2]
    if not np.isfinite(denominator).all() or np.any(denominator <= 0) or not np.isfinite(offset):
        raise ValueError('Invalid native depth reconstruction rays')
    return f(meta['depth_scale_m']),denominator,offset


def reconstruct(native, meta):
    scale,denominator,offset=_geometry(_geometry_key(meta,(native.shape[1],native.shape[0])))
    out=np.zeros(native.shape,dtype='<f4')
    valid=native>0
    out[valid]=(native[valid].astype(np.float32)*scale+offset)/denominator[valid]
    out[out<=0]=0
    return out


def _depth_valid(depth, shape):
    if (depth.dtype != np.dtype('<f4') or depth.shape != shape
            or not np.isfinite(depth).all() or np.any(depth<0)):
        raise ValueError('Native RGB-D requires finite nonnegative float32 optical depth')


def encode(rgb, native, depth, metadata):
    """Encode original aligned Z16 and original optical float32 from one capture."""
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Invalid native RGB input')
    h,w=rgb.shape[:2]; _dimensions((w,h))
    if native.dtype != np.uint16 or native.shape != (h,w):
        raise ValueError('Native depth must be original aligned uint16 samples')
    _depth_valid(depth,(h,w))
    if np.any((native==0)&(depth!=0)):
        raise ValueError('Missing native depth cannot have a metric measurement')
    restored=reconstruct(native,metadata)
    bits=depth.view('<u4').ravel()
    changed=np.flatnonzero(restored.view('<u4').ravel()!=bits)
    patches=np.column_stack((changed,bits[changed])).astype('<u4').tobytes()
    meta={**metadata,'transport_format':FORMAT,'rgb_encoding':'jpeg','rgb_quality':90,
          'depth_encoding':'png_z16','depth_dtype':'<f4',
          'depth_reconstruction':'float32-separate-ops-v1',
          'depth_sha256':hashlib.sha256(depth.tobytes()).hexdigest()}
    meta_bytes=json.dumps(meta,allow_nan=False,separators=(',',':')).encode()
    if len(meta_bytes)>65536: raise ValueError('Native RGB-D metadata too large')
    color,depth_png=io.BytesIO(),io.BytesIO()
    Image.fromarray(rgb).save(color,format='JPEG',quality=90)
    Image.fromarray(native).save(depth_png,format='PNG',compress_level=1)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
        for name,body in [('rgb.jpg',color.getvalue()),('depth.png',depth_png.getvalue()),
                          ('metadata.json',meta_bytes),('depth-patches.bin',patches)]:
            z.writestr(name,body)
    body=out.getvalue()
    if len(body)>MAX_BYTES: raise ValueError('Native RGB-D bundle too large')
    return body


def decode(data, size, *, expected=None, factory=None, serial=None):
    """Bounded public decoder. Validate identity before allocating image geometry."""
    w,h=_dimensions(size)
    if len(data)>MAX_BYTES: raise ValueError('Native RGB-D bundle too large')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        entries=z.infolist()
        if (len(entries)!=4 or {x.filename for x in entries}!=MEMBERS
                or any(x.compress_type!=zipfile.ZIP_STORED or x.flag_bits&1 for x in entries)
                or sum(x.file_size for x in entries)>MAX_BYTES):
            raise ValueError('Invalid native RGB-D archive')
        limits={'metadata.json':65536,'rgb.jpg':4_000_000,'depth.png':4_000_000,'depth-patches.bin':8*w*h}
        if any(x.file_size>limits[x.filename] for x in entries):
            raise ValueError('Native RGB-D member too large')
        meta=json.loads(z.read('metadata.json'))
        if (not isinstance(meta,dict) or meta.get('transport_format')!=FORMAT
                or meta.get('rgb_encoding')!='jpeg' or meta.get('rgb_quality')!=90
                or meta.get('depth_encoding')!='png_z16' or meta.get('depth_dtype')!='<f4'
                or meta.get('depth_reconstruction')!='float32-separate-ops-v1'
                or not isinstance(meta.get('depth_sha256'),str)
                or not re.fullmatch('[0-9a-f]{64}',meta['depth_sha256'])
                or any(k in meta for k in ('depth_quantization_m','depth_max_error_m'))):
            raise ValueError('Invalid native RGB-D contract')
        if expected and any(meta.get(k)!=v for k,v in expected.items()):
            raise ValueError('Native RGB-D identity/calibration mismatch')
        if (factory is not None and meta.get('factory_color_intrinsics')!=factory
                or serial is not None and meta.get('sensor_serial')!=serial):
            raise ValueError('Native sensor/factory profile differs from calibration')
        _geometry_key(meta,(w,h))
        png=z.read('depth.png')
        if (len(png)<33 or png[:8]!=b'\x89PNG\r\n\x1a\n' or png[8:12]!=b'\0\0\0\r'
                or png[12:16]!=b'IHDR'
                or struct.unpack('>IIBBBBB',png[16:29])!=(w,h,16,0,0,0,0)):
            raise ValueError('Native depth PNG must be calibrated-size uint16 grayscale')
        with Image.open(io.BytesIO(png)) as im:
            if im.format!='PNG' or im.size!=(w,h): raise ValueError('Invalid native depth PNG')
            native=np.asarray(im,dtype=np.uint16).copy()
        with Image.open(io.BytesIO(z.read('rgb.jpg'))) as im:
            if im.format!='JPEG' or im.size!=(w,h): raise ValueError('Native JPEG differs from calibration')
            rgb=np.asarray(im.convert('RGB')).copy()
        patches=z.read('depth-patches.bin')
    if len(patches)%8: raise ValueError('Invalid native depth patches')
    pairs=np.frombuffer(patches,dtype='<u4').reshape(-1,2)
    if len(pairs):
        indices=pairs[:,0]
        if np.any(indices>=w*h) or np.any(indices[1:]<=indices[:-1]):
            raise ValueError('Native depth patches must have unique ordered in-range indices')
    depth=reconstruct(native,meta)
    depth.view('<u4').ravel()[pairs[:,0]]=pairs[:,1]
    _depth_valid(depth,(h,w))
    if np.any((native==0)&(depth!=0)) or hashlib.sha256(depth.tobytes()).hexdigest()!=meta['depth_sha256']:
        raise ValueError('Native depth does not match source float32 bits')
    return rgb,depth,meta


def legacy_bundle(rgb, depth, meta):
    """Compatibility NPZ: exact optical depth, explicitly JPEG-derived RGB."""
    meta={**meta,'source_transport_format':FORMAT,'transport_format':'rgbd-npz-v1'}
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for name,array in [('rgb',rgb),('depth_m',depth),('metadata',np.array(json.dumps(meta)))]:
            with z.open(name+'.npy','w') as member:
                np.lib.format.write_array(member,array,allow_pickle=False)
    return out.getvalue()
