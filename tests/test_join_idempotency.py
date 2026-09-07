import pathlib, sys, unittest
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from remote_yam.controller import RunnerController
from remote_yam.providers import ScriptedAdapter
from remote_yam.session import MockSessionAPI

class JoinTests(unittest.TestCase):
    def test_concurrent_joins_keep_one_session_and_original_prompt(self):
        api = MockSessionAPI(auto_activate=False)
        c = RunnerController(api)
        original = ScriptedAdapter([])
        first = c.join(original, 'original')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: c.join(ScriptedAdapter([]), 'replacement'), range(8)))
        self.assertEqual(len(api.create_requests), 1)
        self.assertTrue(all(r['session_id'] == first['session_id'] for r in results))
        self.assertEqual(c._prompt, 'original')
        self.assertIs(c._provider, original)

    def test_join_and_run_keeps_one_worker(self):
        api = MockSessionAPI(auto_activate=False)
        c = RunnerController(api)
        first = c.join_and_run(ScriptedAdapter([]), 'wait')
        worker = c._worker
        try:
            result = c.join_and_run(ScriptedAdapter([]), 'different')
            self.assertEqual(result['session_id'], first['session_id'])
            self.assertIs(c._worker, worker)
            self.assertEqual(len(api.create_requests), 1)
        finally:
            c.stop()
            worker.join(timeout=2)

if __name__ == '__main__': unittest.main()
