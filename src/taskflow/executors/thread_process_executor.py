from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, Future
from typing import Any, Callable, Optional, Iterator, TypeVar
from .base import BaseExecutor

T = TypeVar("T")

class ThreadProcessExecutor(BaseExecutor[Future]):
    """Hybrid executor: threads submit to a process pool."""
    def __init__(self, max_workers: Optional[int] = None, max_processes: Optional[int] = None):
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self._process_pool = ProcessPoolExecutor(max_workers=max_processes)

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        # Submit to thread pool, which submits to process pool
        def process_task(*args, **kwargs):
            return self._process_pool.submit(fn, *args, **kwargs).result()
        return self._thread_pool.submit(process_task, *args, **kwargs)

    def map(self, fn: Callable[[Any], T], *iterables, timeout: Optional[float] = None, chunksize: int = 1) -> Iterator[T]:
        # Map using thread pool, each thread submits to process pool
        def process_task(*args):
            return self._process_pool.submit(fn, *args).result()
        return self._thread_pool.map(process_task, *iterables, timeout=timeout, chunksize=chunksize)

    def shutdown(self, wait: bool = True) -> None:
        self._thread_pool.shutdown(wait=wait)
        self._process_pool.shutdown(wait=wait)
