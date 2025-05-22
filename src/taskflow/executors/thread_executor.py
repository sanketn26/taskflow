import concurrent.futures
from typing import Callable, Any, Iterable, Iterator, Optional, TypeVar
from concurrent.futures import ThreadPoolExecutor, Future

from .base import BaseExecutor

T = TypeVar('T')

class ThreadExecutor(BaseExecutor[Future]):
    """Thread-based executor implementation using ThreadPoolExecutor.
    
    This executor is suitable for I/O-bound tasks that spend most of their
    time waiting for external resources.
    """
    
    def __init__(self, max_workers: Optional[int] = None):
        """Initialize a new ThreadExecutor.
        
        Args:
            max_workers: The maximum number of worker threads.
                        If None, defaults to min(32, os.cpu_count() + 4)
        """
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        """Submit a callable for execution.
        
        Args:
            fn: The callable to execute
            *args: Arguments to pass to the callable
            **kwargs: Keyword arguments to pass to the callable
            
        Returns:
            A Future representing the execution
        """
        return self._executor.submit(fn, *args, **kwargs)

    def map(self, fn: Callable[[Any], T], *iterables, 
            timeout: Optional[float] = None, chunksize: int = 1) -> Iterator[T]:
        """Map a function over iterables.
        
        Args:
            fn: The callable to execute on each element
            *iterables: The iterables to map over
            timeout: Maximum time to wait for each result
            chunksize: Size of chunks for processing (ignored in this implementation)
            
        Returns:
            An iterator over the results
        """
        return self._executor.map(fn, *iterables, timeout=timeout, chunksize=chunksize)

    def shutdown(self, wait: bool = True) -> None:
        """Clean up resources by shutting down the executor.
        
        Args:
            wait: If True, wait for all pending tasks to complete
        """
        self._executor.shutdown(wait=wait)
