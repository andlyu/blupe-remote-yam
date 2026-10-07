"""Small local timing receipts; callers provide only non-secret stage labels."""
from contextlib import contextmanager
import time


@contextmanager
def timed_span(spans, stage, **fields):
    started_at, started = time.time(), time.perf_counter()
    status = 'ok'
    try:
        yield
    except BaseException:
        status = 'error'
        raise
    finally:
        spans.append(dict(stage=stage, started_at=started_at,
                          duration_s=time.perf_counter()-started, status=status, **fields))
