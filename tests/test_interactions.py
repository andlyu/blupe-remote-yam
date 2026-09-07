import importlib.util
import json
import io
import zipfile
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from remote_yam.interactions import InteractionLog, read_recording, list_recordings, recording_directory
from remote_yam.credentials import CredentialVault
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI
from test_robocurve_policy import Cameras, observation, response
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.providers import PolicyComplete
from remote_yam.artifacts import log_archive, video_archive, run_artifacts
from remote_yam.robocurve_policy import CallRecorder
from test_trajectory_transport import TinyTrajectoryProvider


class InteractionTests(unittest.TestCase):
    def test_save_log_has_exact_model_bodies_images_and_errors_but_no_extra_files(self):
        import base64
        with tempfile.TemporaryDirectory() as root:
            recorder = CallRecorder(root)
            image = b'recorded-camera-image'
            request = {'model': 'astra', 'input': [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'place green block on plate'},
                {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,'+base64.b64encode(image).decode()}]}]}
            recorder.write('request', call=1, request=request)
            recorder.write('response', call=1, response={'output': [{'type': 'function_call', 'name': 'done', 'arguments': '{}'}]})
            log = InteractionLog(recorder.path)
            log.add('lifecycle', 'running', episode_id='ep_selected')
            log.add('model_error', 'Server contact issue')
            (recorder.path/'.env').write_text('SECRET_KEY=never-export')
            with (recorder.path/'calls.jsonl').open('a') as stream:
                stream.write('{"incomplete"')
            with log_archive(root, recorder.path.name) as archive, zipfile.ZipFile(archive) as zipped:
                rows = [json.loads(line) for line in zipped.read('calls.jsonl').splitlines()]
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]['request']['model'], 'astra')
                ref = rows[0]['request']['input'][0]['content'][1]['image_url'].split('$blob:')[1]
                self.assertEqual(zipped.read('blobs/'+ref), image)
                self.assertIn(b'Server contact issue', zipped.read('interactions.jsonl'))
                self.assertEqual(json.loads(zipped.read('run.json'))['episode_id'], 'ep_selected')
                self.assertNotIn('.env', zipped.namelist())

    def test_video_resolves_selected_episode_and_never_falls_back_to_latest_run(self):
        with tempfile.TemporaryDirectory() as root:
            names = ['run_'+'a'*32, 'run_'+'b'*32]
            for i, name in enumerate(names):
                InteractionLog(Path(root)/name).add('lifecycle', 'running', episode_id=f'ep_{i}')
            video = b'\x00\x00\x00\x18ftypisom'+b'fixture'*8
            with patch('remote_yam.artifacts.urlopen', return_value=io.BytesIO(video)) as remote:
                with video_archive(root, names[0]) as f:
                    self.assertEqual(f.read(), video)
                self.assertIn('/videos/ep_0.mp4', remote.call_args.args[0])
            legacy = 'run_'+'c'*32
            InteractionLog(Path(root)/legacy).add('joined', 'Old run without an episode ID')
            with self.assertRaisesRegex(ValueError, 'No episode ID'):
                video_archive(root, legacy)
            self.assertIsNone(run_artifacts(root, legacy)['video_url'])

    def test_log_export_refuses_symlinked_model_images(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            rec = CallRecorder(root)
            blob = b'private'; digest = hashlib.sha256(blob).hexdigest()
            outside = Path(root)/'private'; outside.write_bytes(blob)
            (rec.path/'blobs'/digest).symlink_to(outside)
            rec.write('request', request={'image': '$blob:'+digest})
            with self.assertRaisesRegex(ValueError, 'missing'):
                log_archive(root, rec.path.name)

    def test_disk_failure_preserves_visible_event_without_interrupting_runner(self):
        with tempfile.TemporaryDirectory() as root:
            log=InteractionLog(Path(root)/('run_'+'e'*32))
            with patch.object(Path, 'open', side_effect=OSError('disk full')):
                log.add('packet_completed', 'Motion completed', waypoint_count=4)
            snapshot=log.snapshot()
            self.assertIn('OSError',snapshot['error'])
            self.assertEqual(snapshot['events'][0]['details']['waypoint_count'],4)

    def test_durable_progress_is_complete_but_ui_is_compact(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/('run_'+'a'*32)
            log=InteractionLog(directory)
            log.add('packet_submit','submitted',waypoints=[{'step_id':0},{'step_id':1}])
            for i in range(3):log.add('packet_progress',f'progress {i}',trajectory_id='t',step_id=i)
            log.add('packet_completed','completed')
            rows=[json.loads(line) for line in (directory/'interactions.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),5)
            self.assertEqual(rows[0]['details']['waypoints'],[{'step_id':0},{'step_id':1}])
            public=log.snapshot()['events']
            self.assertEqual(len(public),3)
            self.assertEqual(public[1]['details']['step_id'],2)
            self.assertNotIn('waypoints',public[0]['details'])
            self.assertEqual(read_recording(root,directory.name)['events'],public)
            self.assertEqual(list_recordings(root)[0]['run_id'],directory.name)

    def test_local_error_and_give_up_are_visible_and_distinct(self):
        provider=OpenAIAdapter('SECRET', 'astra', camera_source=Cameras())
        log=InteractionLog(); provider.interaction_sink=log.add
        replies=[response('move_to',{'targets':{'left_x':99},'note':'bad'}),
                 response('give_up',{'reason':'Remaining budget seems insufficient.','hindsight':'none'},'c2')]
        with patch.object(provider,'_post_json',side_effect=replies):
            with self.assertRaises(PolicyComplete):provider.build_trajectory('test',observation(),0)
        events=log.snapshot()['events']
        error=next(e for e in events if e['kind']=='tool_error')
        self.assertEqual(error['details']['result']['code'],'target_out_of_bounds')
        self.assertEqual(error['details']['result']['stage'],'local_validation')
        self.assertEqual(error['details']['result']['steps'],0)
        self.assertIn('Astra gave up',events[-1]['message'])
        self.assertNotIn('SECRET',json.dumps(events))
        self.assertEqual(sum(e['kind']=='model_request' for e in events),2)

    def test_logging_does_not_mask_malformed_model_response(self):
        for output in (None, [None]):
            provider=OpenAIAdapter('SECRET', 'astra', camera_source=Cameras())
            log=InteractionLog(); provider.interaction_sink=log.add
            with patch.object(provider,'_post_json',return_value={'output':output}):
                with self.assertRaisesRegex(RuntimeError, 'malformed Responses output'):
                    provider.build_trajectory('test',observation(),0)
            self.assertEqual(log.snapshot()['events'][-1]['kind'],'model_response')

    def test_queue_packet_acceptance_completion_and_stop_journal(self):
        session=MockSessionAPI()
        with tempfile.TemporaryDirectory() as root:
            c=RunnerController(session,recording_root=Path(root))
            c.update_monitor_observation(session.get_robot_observation('yam-1'))
            c.join(TinyTrajectoryProvider(),'test')
            c._run_loop(None)
            state=c.status()
            kinds=[e['kind'] for e in state['interactions']['events']]
            for k in ('joined','observation','packet_submit','packet_dispatched','packet_accepted','packet_progress','packet_completed','stopped'):
                self.assertIn(k,kinds)
            self.assertLess(kinds.index('packet_dispatched'),kinds.index('packet_accepted'))
            self.assertLess(kinds.index('packet_progress'),kinds.index('packet_completed'))
            self.assertIsNone(state['error'])
            self.assertEqual(run_artifacts(root, state['interactions']['run_id'])['episode_id'], state['episode_id'])

    def test_legacy_wire_recording_shows_rejection_and_outcome(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/('robocurve_'+'b'*32); p.mkdir()
            rows=[{'kind':'request','call':1,'time':1,'request':{'model':'astra','input':[]}},
                  {'kind':'response','call':1,'time':2,'response':response('move_to',{'targets':{'left_z':.2},'note':'Raise.'})},
                  {'kind':'tool_result','time':3,'item':{'call_id':'c','output':json.dumps({'ok':False,'steps':0,'error':'Joint-paced path exceeds the waypoint budget'})}}]
            (p/'calls.jsonl').write_text('\n'.join(map(json.dumps,rows)))
            data=read_recording(root,p.name)
            self.assertIn('Raise.',data['events'][1]['message'])
            self.assertIn('waypoint budget',data['events'][2]['message'])
            with self.assertRaises(ValueError):recording_directory(root,'../secret')
            linked=Path(root)/('run_'+'c'*32); linked.symlink_to(p,target_is_directory=True)
            with self.assertRaises(ValueError):recording_directory(root,linked.name)

    def test_recording_routes_require_token_and_serve_readable_archive(self):
        spec=importlib.util.spec_from_file_location('interaction_ui',Path(__file__).resolve().parents[1]/'run.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as root:
            name='run_'+'d'*32; log=InteractionLog(Path(root)/name);log.add('error','Gateway rejected: left_tip_outside_workspace',stage='gateway',code='left_tip_outside_workspace')
            c=RunnerController(MockSessionAPI())
            server=ThreadingHTTPServer(('127.0.0.1',0),module.build_handler(c,CredentialVault(),'local-test','auto',False,'http://127.0.0.1:1',recording_root=Path(root)))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with self.assertRaises(HTTPError) as error:urlopen(base+'/api/recordings')
                self.assertEqual(error.exception.code,403)
                error.exception.close()
                for suffix in ('/api/recordings',f'/api/recordings/{name}/interactions',f'/api/recordings/{name}/interactions.jsonl', f'/api/recordings/{name}/log.zip', f'/api/recordings/{name}/artifacts'):
                    with urlopen(Request(base+suffix,headers={'X-YAM-Runner-Token':'local-test'})) as result:
                        self.assertEqual(result.status,200)
                for suffix in ('log.zip', 'video.mp4', 'artifacts', 'calls.jsonl', 'blobs/'+'a'*64):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base+f'/api/recordings/{name}/{suffix}')
                    self.assertEqual(error.exception.code, 403)
                    error.exception.close()
                with urlopen(base) as result:html=result.read().decode()
                self.assertIn('Interaction log',html)
                self.assertIn('interactionStage',html)
                self.assertIn('Open runner conversation',html)
                self.assertIn('/static/conversation.js',html)
                with urlopen(base+'/static/interactions.js') as result:
                    self.assertIn('Waiting for Astra',result.read().decode())
            finally:server.shutdown();server.server_close();thread.join()
