"""Finite matrix and rigid-transform validation for API camera geometry."""
import numpy as np


def matrix(value, shape, label):
    a=np.asarray(value,dtype=float)
    if a.shape!=shape or not np.isfinite(a).all():
        raise ValueError(f'Invalid calibration {label}')
    return a


def rigid(value):
    a=matrix(value,(4,4),'transform')
    r=a[:3,:3]
    if not np.allclose(a[3],[0,0,0,1]) or not np.allclose(r.T@r,np.eye(3),atol=1e-5) or not np.isclose(np.linalg.det(r),1,atol=1e-5):
        raise ValueError('Calibration transform must be rigid')
    return a
