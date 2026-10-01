#!/usr/bin/env python3
"""Public API depth example. --probe captures observations without queueing/motion."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from remote_yam.api_depth import ApiDepth

API = 'https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com'
TASK = ('Pick up the red block and place it fully on the black towel using YAM (yam-1). '
        'Use the API depth maps and camera calibration supplied as inputs to Astra. '
        'First identify the red block and the black towel in '
        'the paired overhead RGB image and query measured depth at interior pixels '
        'of the red block and a clear placement area on the black towel. The black '
        'towel is the destination; no black block is required. If the red block or '
        'towel is absent, give up with that reason. '
        'Use depth and the API calibration, retain provisional extrinsics limits, '
        'and reobserve after every move. Confirm the red block is retained after '
        'lifting. Release only after observed support. Withdraw and verify the '
        'red block remains fully and stably on the black towel. Report success '
        'only after that final observation; do not claim metric accuracy.')


def probe(args):
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    if args.all_depth_origin:
        from remote_yam.api_depth_set import ApiDepthSet
        if args.model_probe:
            raise ValueError('Use the local UI for all-camera inference with measured arm poses')
        source = ApiDepthSet(args.api, args.all_depth_origin)
        source.capture_for_policy({'robot_id':'yam-1'})
        result = dict(depth_transport='PASS', physical_run='UNVERIFIED', task=TASK,
                      calibration_id=source.snapshots['top'].metadata['calibration_id'],
                      quality=source.snapshots['top'].calibration['quality'],
                      cameras={role:dict(metadata=s.metadata, age_upper_s=s.age())
                               for role,s in source.snapshots.items()})
        for role, snapshot in source.snapshots.items():
            for suffix, data in [('.rgbd.npz', snapshot.bundle),('.png',snapshot.image()),
                                 ('-depth.png',snapshot.image(True))]:
                path=folder/(role+suffix); path.write_bytes(data); path.chmod(0o600)
        (folder/'calibration.json').write_text(json.dumps(source.snapshots['top'].calibration,indent=2))
        (folder/'receipt.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2)); return
    try:
        snapshot = ApiDepth(args.api,depth_url=args.depth_url).capture()
        depth_received_at = time.time()
    except Exception as exc:
        (folder/'receipt.json').write_text(json.dumps(dict(depth_transport='FAIL',
            physical_run='UNVERIFIED',error=str(exc),checked_at=time.time()),indent=2))
        raise
    for name,data in [('top.rgbd.npz',snapshot.bundle),('top.png',snapshot.image()),
                      ('top-depth.png',snapshot.image(True))]:
        (folder/name).write_bytes(data)
        (folder/name).chmod(0o600)
    (folder/'calibration.json').write_text(json.dumps(snapshot.calibration,indent=2))
    wrist_frames = None
    if args.rgb_origin:
        from remote_yam.private_rgb import PrivateRgbSource
        source = PrivateRgbSource(args.rgb_origin)
        source.set_calibration(snapshot.calibration)
        wrist_frames = {f.name: f for f in source.capture_for_policy({'robot_id': 'yam-1'})}
        for name, frame in wrist_frames.items():
            (folder / (name + '.jpg')).write_bytes(frame.jpeg)
    result = dict(captured_at=snapshot.metadata['captured_at'],
                  age_s=depth_received_at-snapshot.metadata['captured_at'],
                  observation_ready_age_s=time.time()-snapshot.metadata['captured_at'],
                  calibration_id=snapshot.metadata['calibration_id'],
                  quality=snapshot.calibration['quality'], physical_run='UNVERIFIED',
                  task=TASK,depth_transport='PASS')
    if wrist_frames:
        result['local_wrist_rgb'] = {name: frame.summary() for name, frame in wrist_frames.items()}
    if args.model_probe:
        from remote_yam.codex_depth_policy import CodexDepthAdapter
        provider = CodexDepthAdapter(args.api,depth_url=args.depth_url,rgb_origin=args.rgb_origin,recording_root=folder)
        provider.reasoning_effort = 'high'
        provider._snapshot = snapshot
        content=[dict(type='input_text',text='Task reference: '+TASK+'\n'
                      'Observation-only probe: identify the red block and the black towel. '
                      'The black towel is the requested destination. '
                      'Query depth for the red block and a clear placement area on the towel if present. '
                      'Then return done summarizing observations and measurements, '
                      'or give_up if an object is missing. Do not propose any movement. '
                      'This probe has no controller, queue or motor access.\nCalibration: '
                      +json.dumps(snapshot.calibration)+'\nCapture: '+json.dumps(snapshot.metadata))]
        for role,image in [('top',snapshot.image()),
                           ('left',wrist_frames['left'].jpeg if wrist_frames else provider.depth_api.read('/cameras/left.jpg',4_000_000)),
                           ('right',wrist_frames['right'].jpeg if wrist_frames else provider.depth_api.read('/cameras/right.jpg',4_000_000)),
                           ('top aligned depth',snapshot.image(True))]:
            kind='png' if image.startswith(b'\x89PNG') else 'jpeg'
            content.extend([dict(type='input_text',text=role),dict(type='input_image',detail='high',
                            image_url='data:image/'+kind+';base64,'+base64.b64encode(image).decode())])
        provider._history=[dict(role='user',content=content)]
        try:
            response=provider._post_json(dict(input=list(provider._history),tools=[]))
            call=response['output'][0]
            if call['name'] not in ('done','give_up'):
                raise RuntimeError('Observation probe proposed movement; it was not executed')
            result['model_observation']='PASS'
            result['model_result']=dict(action=call['name'],**json.loads(call['arguments']))
        finally:
            provider._workspace.cleanup()
    (folder/'receipt.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


def serve(args):
    import uvicorn
    from local_playground import LocalPlayground
    from remote_yam.codex_depth_policy import CodexDepthAdapter
    def factory(name,key,model,directory):
        if name!='codex':
            raise ValueError('This example uses Astra with a Codex subscription')
        provider=CodexDepthAdapter(args.api,model or 'gpt-6-astra',depth_url=args.depth_url,rgb_origin=args.rgb_origin,
                                  active_arm=args.active_arm,
                                  all_depth_origin=args.all_depth_origin,recording_root=directory,
                                  camera_source=app.camera_source)
        # Check data before queue admission can trigger automatic hardware home.
        try:
            if args.all_depth_origin:
                provider._camera_source.capture_for_policy({'robot_id':'yam-1'},cancelled=provider.cancelled)
                report=provider._camera_source.snapshots['top'].calibration
            else:
                snapshot = provider.depth_api.capture(cancelled=provider.cancelled)
                report=snapshot.calibration
            if args.calibration_id and report['calibration_id'] != args.calibration_id:
                raise ValueError('Required camera calibration is not published yet; no session queued')
            provider.set_depth_calibration(report)
            if args.rgb_origin and not args.all_depth_origin:
                provider._camera_source.set_calibration(snapshot.calibration)
                provider._camera_source.capture_for_policy({'robot_id': 'yam-1'}, cancelled=provider.cancelled)
        except Exception as exc:
            from playground import RequestError
            provider._workspace.cleanup()
            raise RequestError(503,str(exc)) from None
        return provider
    class DepthPlayground(LocalPlayground):
        def page(self):
            # Existing UI and consent flow; only preset the task in this example.
            import html
            page=super().page().decode()
            import re
            page=re.sub(r'(<textarea\b[^>]*\bid="prompt"[^>]*>).*?(</textarea>)',
                        lambda m:m[1]+html.escape(TASK)+m[2],page,flags=re.S)
            page=page.replace('<h1>BluPe Playground</h1>',
                '<h1>BluPe Playground</h1><p class="eyebrow">Depth example · YAM</p>')
            page=page.replace(
                '<p><strong>Control real robot arms with AI.</strong> Watch the live cameras, '
                'tell the arms what to do, and let your chosen AI model try it. '
                'Each session lasts 5 minutes by default.</p>',
                '<p><strong>Place the red block on the black towel.</strong> '
                'Astra receives camera images, depth maps and API calibration.</p>')
            return page.encode()
    app=DepthPlayground(public_origin=f'http://127.0.0.1:{args.port}',session_api=args.api,
                        camera_origin=args.api,development=True,local_codex=True,
                        default_provider='codex',api_depth_provider_factory=factory,use_api_depth=False,
                        hardware_control=True,share_conversation=True,robot_id='yam-1',hardware='yam')
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--api',default=API)
    p.add_argument('--depth-url',help='Explicit paired RGB-D API URL, e.g. a loopback SSH camera tunnel')
    p.add_argument('--rgb-origin',help='Explicit local wrist RGB API origin, e.g. http://127.0.0.1:18090')
    p.add_argument('--all-depth-origin',help='API origin with paired top/left/right RGB-D (public API or explicit operator tunnel)')
    p.add_argument('--active-arm',choices=('left','right'),help='Optional explicit arm restriction; default allows either arm')
    p.add_argument('--calibration-id',help='Require this camera package before queue admission')
    p.add_argument('--port',type=int,default=8793)
    p.add_argument('--probe',action='store_true')
    p.add_argument('--model-probe',action='store_true',help='Read-only Astra image/depth query probe')
    p.add_argument('--output',default='outputs/astra-depth-example')
    args=p.parse_args()
    probe(args) if args.probe or args.model_probe else serve(args)
