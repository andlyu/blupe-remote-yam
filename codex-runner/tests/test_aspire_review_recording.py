import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from remote_yam.cameras import CameraFrame
from remote_yam.aspire_review_recording import SessionReviewRecorder, completed_task_outcome


class ParkingReviewTests(unittest.TestCase):
    def test_postpark_evidence_can_resolve_an_occluded_native_evaluation(self):
        result=completed_task_outcome(dict(status='UNVERIFIED',planning_success=True,physical_motion_calls=11),
            dict(status='PARKING_OBSERVED'),dict(status='EVALUATION_ONLY',success=True))
        self.assertTrue(result['success'])

    def test_release_success_does_not_override_negative_postpark_evidence(self):
        result=completed_task_outcome(dict(status='SUCCESS',planning_success=True,physical_motion_calls=11),
            dict(status='PARKING_OBSERVED'),dict(status='EVALUATION_ONLY',success=False))
        self.assertFalse(result['success'])

    def test_failed_motion_cannot_be_resolved_by_a_later_positive_image(self):
        result=completed_task_outcome(dict(status='FAILED',planning_success=True,physical_motion_calls=7),
            dict(status='PARKING_OBSERVED'),dict(status='EVALUATION_ONLY',success=True))
        self.assertEqual(result['status'],'FAILED')
        self.assertFalse(result['success'])

    def recorder(self, sink=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)/'review'
        source=Mock(timeout_s=.1)
        source.fetch.side_effect=lambda role,url:CameraFrame(role,b'fixture jpeg',time.time(),
                                                              source_captured_at=time.time())
        review=SessionReviewRecorder('https://fixture.invalid',
            {role:'https://fixture.invalid/'+role for role in ['top','left','right']},
            root,source=source,sink=sink,interval_s=.01)
        self.addCleanup(review.close)
        return review,root,source

    def test_capture_continues_through_parking_and_stops_after_new_source_frames(self):
        review,root,source=self.recorder()
        review.start()
        observations=iter([
            dict(mode='API_ACTIVE',source='hardware',observed_at=time.time()),
            dict(mode='DISABLED',source='hardware',observed_at=1.)])
        def observe():
            return next(observations,dict(mode='DISABLED',source='hardware',observed_at=time.time()))
        result=review.finish_after_stop(observe,timeout_s=3.,tail_s=0.)
        self.assertEqual(result['status'],'PARKING_OBSERVED')
        self.assertEqual(result['physical_commands'],0)
        self.assertEqual(set(result['latest_frames']),{'top','left','right'})
        self.assertTrue(all(row['captured_at']>=result['parked_observation']['observed_at']
                            for row in result['latest_frames'].values()))
        events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
        self.assertTrue(events[-1]['threads_finished'])
        for role in ['top','left','right']:
            self.assertTrue((root/(role+'-frames.jsonl')).exists())
        self.assertEqual(set(source.method_calls[i][0] for i in range(len(source.method_calls))),{'fetch'})

    def test_stale_disabled_telemetry_does_not_establish_parking(self):
        review,root,source=self.recorder();review.start()
        result=review.finish_after_stop(lambda:dict(mode='DISABLED',source='hardware',observed_at=1.),
                                        timeout_s=.05,tail_s=0.)
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertIsNone(result['parked_observation'])

    def test_writer_error_preserves_actual_jpegs_and_records_gap(self):
        review,root,source=self.recorder(sink=Mock(side_effect=RuntimeError('fixture writer error')))
        review.start();time.sleep(.05);review.close()
        self.assertTrue(list(root.glob('*.jpg')))
        events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(row['kind']=='sample_error' for row in events))

    def test_absent_source_timestamps_do_not_prove_after_parking_scene(self):
        review,root,source=self.recorder()
        source.fetch.side_effect=lambda role,url:CameraFrame(role,b'fixture jpeg',time.time())
        review.start()
        result=review.finish_after_stop(lambda:dict(mode='DISABLED',source='hardware',observed_at=time.time()),
                                        timeout_s=.05,tail_s=0.)
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertIsNotNone(result['parked_observation'])
