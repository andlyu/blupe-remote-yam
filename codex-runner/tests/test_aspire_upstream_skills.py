from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from remote_yam.aspire_upstream_skills import bind_upstream_geometry


class BindingTests(unittest.TestCase):
    def modules(self):
        original=lambda *args,**kwargs:None
        grasp=SimpleNamespace(__name__='skill_library.grasp_geometry',__file__=__file__,detect_objects_oneshot=original)
        pick=SimpleNamespace(__name__='skill_library.pick_place',__file__=__file__,detect_objects_oneshot=original)
        return grasp,pick,original

    def test_original_callable_receives_detection_and_callback_is_restored(self):
        grasp,pick,original=self.modules();records=[]
        def sample(object_name,camera,**kwargs):
            detection=grasp.detect_objects_oneshot(object_name,camera=camera)[object_name][0]
            self.assertEqual(detection.position_3d,[.1,.2,.3])
            self.assertEqual(kwargs['pitches'],[180.])
            return []
        grasp.sample_topdown_geometric=sample
        with patch('remote_yam.aspire_upstream_skills.importlib.import_module',side_effect=[grasp,pick]):
            tools=bind_upstream_geometry(lambda label,value:records.append(value))
        tools['sample_topdown_geometric']('block',detection={'position_3d':[.1,.2,.3]},pitches=[180.])
        self.assertIs(grasp.detect_objects_oneshot,original)
        self.assertEqual(records[0]['function'],'sample_topdown_geometric')
        self.assertEqual(records[0]['physical_motion_calls'],0)
        self.assertEqual(records[0]['source'],str(Path(__file__).resolve()))

    def test_upstream_exception_restores_original_detector(self):
        grasp,pick,original=self.modules()
        def fail(**kwargs):raise RuntimeError('original helper failed')
        pick.estimate_drop=fail
        with patch('remote_yam.aspire_upstream_skills.importlib.import_module',side_effect=[grasp,pick]):
            tools=bind_upstream_geometry()
        with self.assertRaisesRegex(RuntimeError,'original helper failed'):
            tools['estimate_drop']('chip',detection={'position_3d':[0.,0.,0.]},z_offset=.01)
        self.assertIs(pick.detect_objects_oneshot,original)


if __name__=='__main__':unittest.main()
