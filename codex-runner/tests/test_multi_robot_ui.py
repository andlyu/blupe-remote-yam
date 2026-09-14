import asyncio
import json
import unittest
from unittest.mock import Mock
from hosted import HostedRunner
from multi_robot import fleet
from remote_yam.session import HttpSessionAPI, MockSessionAPI
from remote_yam.ik import IKSolver, IKCommand, ArmIKCommand, parse_observation
from remote_yam.model_trajectory import build_model_trajectory

class MultiRobotTests(unittest.IsolatedAsyncioTestCase):
    async def test_sessions_and_status_are_isolated_and_switchable(self):
        app=fleet(HostedRunner,dict(public_origin='https://test.example',session_api='https://api.example',camera_origin='https://api.example',api_factory=lambda:MockSessionAPI(auto_activate=False)),[
            {'id':'yam-1','name':'YAM'}, {'id':'so101','name':'SO101','cameras':['front'],'joint_counts':[5,0]}])
        async def call(robot,path,method='GET',cookie=''):
            events=[]
            scope={'type':'http','method':method,'path':path,'query_string':b'', 'headers':[(b'host',b'test.example'),(b'origin',b'https://test.example'),(b'content-type',b'application/json'),(b'x-blupe-robot',robot.encode()),(b'cookie',cookie.encode())]}
            async def receive():return {'type':'http.request','body':b'{}'}
            async def send(event):events.append(event)
            await app(scope,receive,send)
            return events[0],json.loads(events[1]['body'])
        a,sa=await call('yam-1','/api/session','POST')
        b,sb=await call('so101','/api/session','POST')
        self.assertNotEqual(sa['csrf'],sb['csrf']);self.assertEqual(sb['cameras'],['front'])
        ca=dict(a['headers'])[b'set-cookie'].decode().split(';')[0]
        cb=dict(b['headers'])[b'set-cookie'].decode().split(';')[0]
        self.assertNotEqual(ca.split('=')[0],cb.split('=')[0])
        self.assertEqual((await call('so101','/api/status',cookie=ca))[0]['status'],401)
        self.assertEqual((await call('so101','/api/status',cookie=cb))[0]['status'],200)
        self.assertEqual((await call('missing','/api/status'))[0]['status'],404)
        self.assertEqual(app.apps['so101'].new_visitor()[1].controller._robot_id,'so101')

    def test_api_targets_selected_robot(self):
        api=HttpSessionAPI('https://api.example',robot_id='so101',websocket_factory=Mock())
        api._request=Mock(return_value={'session_id':'s','session_capability':'cap'})
        api.create_session('task')
        self.assertEqual(api._request.call_args.args[2]['robot_id'],'so101')
        api.get_queue_snapshot()
        self.assertEqual(api._request.call_args.args[1],'/v1/robots/so101/queue')

    def test_so101_joint_dimensions_and_ramp(self):
        obs={'left_joints_deg':[0]*5,'right_joints_deg':[],'left_gripper':.1}
        parse_observation(obs,(5,0))
        with self.assertRaises(ValueError):parse_observation(obs)
        points=build_model_trajectory(IKCommand(ArmIKCommand('joints',(2,)*5),ArmIKCommand('joints',()),.12),obs,0,IKSolver(joint_counts=(5,0)))
        self.assertEqual(points[-1]['left_joints_deg'],[2]*5)
        self.assertEqual(points[-1]['right_joints_deg'],[])
        self.assertLessEqual(points[0]['left_joints_deg'][0],.573)
