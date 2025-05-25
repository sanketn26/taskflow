import importlib.util
from typing import Any, Callable, Optional, Iterator, TypeVar
from .base import BaseExecutor

T = TypeVar("T")

class GeventProcessExecutor(BaseExecutor[Any]):
    """Hybrid executor: greenlets submit to a process pool."""
    def __init__(self, max_workers: Optional[int] = None, max_processes: Optional[int] = None):
        if importlib.util.find_spec("gevent") is None:
            raise ImportError("gevent is required for GeventProcessExecutor. Install it with 'pip install gevent'.")
        import gevent
        import gevent.pool
        self.gevent = gevent
        self._pool = gevent.pool.Pool(max_workers)
        from concurrent.futures import ProcessPoolExecutor
        self._process_pool = ProcessPoolExecutor(max_workers=max_processes)

    def submit(self, fn: Callable, *args, **kwargs):
        def process_task(*args, **kwargs):
            future = self._process_pool.submit(fn, *args, **kwargs)
            return future.result()
        return self._pool.spawn(process_task, *args, **kwargs)

    def map(self, fn: Callable[[Any], T], *iterables, timeout: Optional[float] = None, chunksize: int = 1) -> Iterator[T]:
        def process_task(*args):
            future = self._process_pool.submit(fn, *args)
            return future.result()
        return self._pool.imap(process_task, *iterables)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.join(timeout=None)
        self._pool.kill()
        self._process_pool.shutdown(wait=wait)
