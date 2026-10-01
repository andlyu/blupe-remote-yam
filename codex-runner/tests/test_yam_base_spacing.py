import numpy as np
import pytest
from remote_yam.robocurve_trajectory import RoboCurveTrajectory
from pathlib import Path
import xml.etree.ElementTree as ET


def test_spacing_changes_world_positions_but_not_arm_local_contract():
    model = Path(__file__).parents[1] / 'models/yam_bimanual/yam_bimanual.xml'
    home = list(map(float, ET.parse(model).find(".//keyframe/key[@name='home']").attrib['qpos'].split()))
    observation=dict(settled=True, left_gripper=1.,right_gripper=1.)
    for index, arm in enumerate(('left','right')):
        observation[arm+'_joints_deg']=np.rad2deg(home[index*6:(index+1)*6]).tolist()
    old,new=RoboCurveTrajectory(),RoboCurveTrajectory(base_spacing_m=.5)
    _,old_local,_=old.observe(observation)
    old_world=old.ik._poses()
    _,new_local,_=new.observe(observation)
    new_world=new.ik._poses()
    assert np.allclose(old_local,new_local,atol=1e-10)
    assert np.isclose(np.linalg.norm(new.bases[0]-new.bases[1]),.50)
    assert np.allclose(new_world[0][0]-old_world[0][0],[0,-.1,0])
    assert np.allclose(new_world[1][0]-old_world[1][0],[0,.1,0])
    points=new.build({'left_z':float(new_local[2]+.005)},observation,0)
    assert points and points[-1]['step_id']==len(points)-1


@pytest.mark.parametrize('spacing',[float('nan'),True,0.,-1.,5.])
def test_invalid_spacing_rejected(spacing):
    with pytest.raises(ValueError):RoboCurveTrajectory(base_spacing_m=spacing)
