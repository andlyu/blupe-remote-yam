"""Same-origin robot routing with independent visitor credentials and runners."""
import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs


class MultiRobotPlayground:
    def __init__(self, apps, default='yam-1'):
        self.apps = apps
        self.default = default

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            incoming = [asyncio.Queue() for _ in self.apps]
            outgoing = [asyncio.Queue() for _ in self.apps]
            tasks = [asyncio.create_task(app(scope, iq.get, oq.put))
                     for app, iq, oq in zip(self.apps.values(), incoming, outgoing)]
            try:
                while True:
                    event = await receive()
                    for queue in incoming:
                        await queue.put(event)
                    replies = await asyncio.gather(*(q.get() for q in outgoing))
                    failure = next((r for r in replies if r['type'].endswith('failed')), None)
                    await send(failure or replies[0])
                    if event['type'] == 'lifespan.shutdown' or failure:
                        break
            finally:
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            return
        headers = dict(scope.get('headers', []))
        query = parse_qs(scope.get('query_string', b'').decode('utf-8', errors='replace'))
        selected = headers.get(b'x-blupe-robot', b'').decode('utf-8', errors='replace') or query.get('robot_id', [self.default])[0]
        app = self.apps.get(selected)
        if app is None:
            return await self.apps[self.default].json(send, 404, {'error':'Unknown robot'})
        await app(scope, receive, send)


def fleet(runner_type, options, robots=None):
    robots = robots or [{'id':'yam-1', 'name':'YAM', 'cameras':['left','top','right']}]
    catalog = [{**r, 'url':options['public_origin'] + '/'} for r in robots]
    apps = {}
    for robot in catalog:
        rid = robot['id']
        if rid in apps or not isinstance(rid, str) or not rid or len(rid)>64:
            raise ValueError('Robot IDs must be unique nonempty strings')
        settings = dict(options, robots=catalog, robot_id=rid,
                        hardware=robot.get('hardware'), policy_camera_names=robot.get('policy_cameras'), camera_names=robot.get('cameras', ['left','top','right']), joint_counts=tuple(robot.get('joint_counts', [6,6])))
        if rid != 'yam-1':
            if options.get('chat_database'):
                import hashlib
                path=Path(options['chat_database'])
                settings['chat_database']=str(path.with_name(path.stem+'-'+hashlib.sha256(rid.encode()).hexdigest()[:16]+path.suffix))
        apps[rid] = runner_type(**settings)
    for app in apps.values():
        app._fleet_apps = apps
    default = 'yam-1' if 'yam-1' in apps else catalog[0]['id']
    return MultiRobotPlayground(apps, default)
