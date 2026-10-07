"""Historical topic learning, separate from a run's planning and motion."""
import fcntl
import json
import threading
import time

from .aspire_skill_learning import COORDINATOR_INSTRUCTIONS, PROMOTION_SCHEMA
from .codex_policy import CodexAdapter


def promotion_knowledge(library, task):
    """Keep exact recipes and caveats once; full history remains on disk."""
    view = library.retrieve(task)
    entries = {}
    for item in view.get('entries', []) + view.get('failure_findings', []):
        if item['id'] in entries:
            continue
        compact = {key: item[key] for key in ('id', 'version', 'topic', 'title',
            'trigger', 'why', 'scope', 'limits', 'keywords', 'statuses', 'snippets') if key in item}
        compact['evidence'] = [{key: evidence[key] for key in ('task_id', 'task',
            'finding_status', 'finding_scope', 'native_status', 'postpark_automatic_success',
            'postpark_checks', 'review_scope', 'evidence_role', 'receipt', 'review') if key in evidence}
            for evidence in item.get('evidence', [])]
        entries[item['id']] = compact
    return {'library_sha256': view.get('library_sha256'),
            'semantics': view.get('semantics'), 'entries': list(entries.values())}


class SubscriptionSkillCoordinator:
    """Own a text-only transport independent of a stopped robot policy."""
    def __init__(self, library, model, *, timeout_s=180, response_speed='standard'):
        self.library, self.model = library, model
        self.timeout_s, self.response_speed = timeout_s, response_speed
        self.provider = None

    def __call__(self, *, packet, sources, feedback):
        if self.provider is None:
            self.provider = CodexAdapter(self.model, timeout_s=self.timeout_s)
            self.provider._decision_schema = PROMOTION_SCHEMA
            self.provider._decision_response = lambda value: value
            self.provider._decision_instructions = COORDINATOR_INSTRUCTIONS
            self.provider._expected_camera_count = 0
            self.provider.cancelled = lambda: False
            self.provider.reasoning_effort = 'low'
            self.provider.response_speed = self.response_speed
        # Each finding gets one bounded context. No earlier whole-program
        # conversations are replayed into the next finding's model request.
        body = dict(findings=packet,
            numbered_source_programs={path: '\n'.join(str(number)+' '+line
                for number, line in enumerate(source.splitlines(), 1))
                for path, source in sources.items()},
            existing_topic_knowledge=promotion_knowledge(self.library, packet['task']),
            promotion_feedback=feedback)
        self.provider._calls += 1
        self.provider._thread_id = None
        self.provider._sent_items = 0
        reply = self.provider._post_json(dict(instructions=COORDINATOR_INSTRUCTIONS, tools=[],
            input=[dict(role='user', content=[dict(type='input_text', text=json.dumps(body))])]))
        for finding in reply['findings']:
            for snippet in finding['snippets']:
                snippet['generalize'] = {item['original']: item['replacement']
                    for item in snippet['generalize']}
        return reply

    def close(self):
        if self.provider:
            self.provider._workspace.cleanup()


class DeferredSkillReview:
    _guard = threading.Lock()
    _jobs = {}

    def __init__(self, library, coordinator_factory):
        self.library, self.coordinator_factory = library, coordinator_factory
        self.done = threading.Event()
        self.again = threading.Event()
        self.status = dict(status='SCHEDULED', physical_commands_sent=0)
        self.thread = threading.Thread(target=self._run, name='aspire-skill-review', daemon=True)

    @classmethod
    def schedule(cls, library, coordinator_factory):
        key = (str(library.root), library.suite)
        with cls._guard:
            previous = cls._jobs.get(key)
            if previous and not previous.done.is_set():
                previous.again.set()
                return previous
            job = cls(library, coordinator_factory)
            cls._jobs[key] = job
            job.thread.start()
            return job

    def _save(self, **values):
        self.status.update(values)
        self.library.campaign.mkdir(parents=True, exist_ok=True)
        path = self.library.campaign/'background-review.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.status, indent=2)+'\n')
        temporary.replace(path)

    def _run(self):
        coordinator = None
        try:
            self.library.root.mkdir(parents=True, exist_ok=True)
            # A separate nonblocking lock prevents another playground process
            # from paying for the same review. The library's promotion lock is
            # held only for local reads/commits, never during model inference.
            with (self.library.root/('.review-worker-'+self.library.suite+'.lock')).open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.status.update(status='BUSY_OTHER_PROCESS')
                    return
                self._save(status='RUNNING', started_at=time.time())
                coordinator = self.coordinator_factory()
                records = []
                while True:
                    self.again.clear()
                    records.extend(self.library.review_pending(coordinator, current_task=None))
                    with self._guard:
                        if self.again.is_set():
                            continue
                        self._save(status='COMPLETE', finished_at=time.time(),
                            reviewed_tasks=sorted({record['task'] for record in records}))
                        close = getattr(coordinator, 'close', None)
                        if callable(close):
                            close()
                        coordinator = None
                        fcntl.flock(lock, fcntl.LOCK_UN)
                        self.done.set()
                        return
        except Exception as exc:
            self._save(status='FAILED', finished_at=time.time(),
                error_type=type(exc).__name__, error=str(exc))
        finally:
            try:
                close = getattr(coordinator, 'close', None)
                if callable(close):
                    close()
            finally:
                with self._guard:
                    self.done.set()
