"""Read-only bindings to the installed ASPIRE skill library, without source copies.

NVlabs/ASPIRE f4c8939: skill_library/grasp_geometry.py and pick_place.py.
Detection is supplied by the station's calibrated RGB-D perception adapter;
the original geometric sampler and transport-pose helpers remain upstream.
"""
import hashlib
import importlib
from pathlib import Path
import threading
from types import SimpleNamespace
from .aspire_code_runtime import plain

_lock=threading.RLock()
_missing=object()


def bind_upstream_geometry(record=lambda label,value:None):
    grasp=importlib.import_module('skill_library.grasp_geometry')
    pick=importlib.import_module('skill_library.pick_place')

    def call(module,name,arguments,detection=None,object_name=None):
        with _lock:
            previous=getattr(module,'detect_objects_oneshot',_missing)
            if detection is not None:
                item=SimpleNamespace(**dict(detection))
                module.detect_objects_oneshot=lambda *args,**kwargs:{object_name:[item]}
            try:
                result=getattr(module,name)(**arguments)
            finally:
                if detection is not None:
                    if previous is _missing:delattr(module,'detect_objects_oneshot')
                    else:module.detect_objects_oneshot=previous
        path=Path(module.__file__).resolve()
        record('upstream_skill_call',plain(dict(module=module.__name__,function=name,
            source=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            arguments=arguments,detection=detection,
            result=[x._asdict() for x in result] if name=='sample_topdown_geometric' else result,
            physical_motion_calls=0,perception='supplied measured station detection; upstream candidate/pose code')))
        return result

    def sample_topdown_geometric(object_name,*,detection,camera='top',**kwargs):
        return call(grasp,'sample_topdown_geometric',
            dict(object_name=object_name,camera=camera,**kwargs),detection,object_name)

    def estimate_drop(target_name,*,detection,z_offset,camera='top'):
        return call(pick,'estimate_drop',dict(target_name=target_name,z_offset=z_offset,camera=camera),
            detection,target_name)

    def birdseye_pose(side,*,left_home_xyz,right_home_xyz,home_view_z_offset,
                      left_birdeye_view_rpy,right_birdeye_view_rpy):
        return call(pick,'birdseye_pose',dict(side=side,left_home_xyz=left_home_xyz,
            right_home_xyz=right_home_xyz,home_view_z_offset=home_view_z_offset,
            left_birdeye_view_rpy=left_birdeye_view_rpy,right_birdeye_view_rpy=right_birdeye_view_rpy))

    return dict(sample_topdown_geometric=sample_topdown_geometric,
        estimate_drop=estimate_drop,birdseye_pose=birdseye_pose)
