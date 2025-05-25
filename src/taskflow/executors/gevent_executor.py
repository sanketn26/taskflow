import importlib.util
from typing import Any, Optional, TypeVar
from .base import BaseExecutor

T = TypeVar("T")

class GeventExecutor(BaseExecutor[Any]):
    """Greenlet-based executor implementation using gevent."""
    def __init__(self, max_workers: Optional[int] = None):
        if importlib.util.find_spec("gevent") is None:
            raise ImportError(
                "gevent is required for GeventExecutor. "
                "Install it with 'pip install gevent'."
            )
        import gevent
        import gevent.pool
        self.gevent = gevent
        self._pool = gevent.pool.Pool(max_workers)

    def submit(self, fn, *args, **kwargs):
        greenlet = self._pool.spawn(fn, *args, **kwargs)
        if not hasattr(greenlet, "result"):
            original_get = greenlet.get
            def result_method(timeout=None):
                return original_get(timeout=timeout)
            setattr(greenlet, "result", result_method)
        return greenlet

    def map(self, fn, *iterables, timeout=None, chunksize=1):
        return self._pool.imap(fn, *iterables)

    def shutdown(self, wait=True):
        if wait:
            self._pool.join(timeout=None)
        self._pool.kill()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(wait=True)
        return False
