import threading
from remote_yam.controller import RunnerController

def test_queue_monitor_preserves_opted_in_public_conversation():
 c=RunnerController.__new__(RunnerController);c._lock=threading.RLock()
 shared={'run_id':'ep_test','model_name':'Test model','events':[{'kind':'model_response','message':'Visible text'}]}
 base={'schema_version':1,'type':'queue_snapshot','entries':[],'stations':[]}
 c.update_queue_snapshot({**base,'public_run':shared})
 assert c._queue_snapshot['public_run']==shared
 c.update_queue_snapshot(base)
 assert c._queue_snapshot['public_run'] is None
