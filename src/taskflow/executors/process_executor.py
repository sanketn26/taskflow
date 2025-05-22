import concurrent.futures
from typing import Callable, Any, Iterable, Iterator, Optional, TypeVar
from concurrent.futures import ProcessPoolExecutor, Future

from .base import BaseExecutor

T = TypeVar('T')

class ProcessExecutor(BaseExecutor[Future]):
    """Process-based executor implementation using ProcessPoolExecutor.
    
    This executor is suitable for CPU-bound tasks that benefit from true
    parallelism and isolation provided by separate processes.
    """
    
    def __init__(self, max_workers: Optional[int] = None):
        """Initialize a new ProcessExecutor.
        
        Args:
            max_workers: The maximum number of worker processes.
                        If None, defaults to os.cpu_count()
        """
        self._executor = ProcessPoolExecutor(max_workers=max_workers)

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        """Submit a callable for execution.
        
        Args:
            fn: The callable to execute (must be picklable)
            *args: Arguments to pass to the callable (must be picklable)
            **kwargs: Keyword arguments to pass to the callable (must be picklable)
            
        Returns:
            A Future representing the execution
        """
        return self._executor.submit(fn, *args, **kwargs)

    def map(self, fn: Callable[[Any], T], *iterables, 
            timeout: Optional[float] = None, chunksize: int = 1) -> Iterator[T]:
        """Map a function over iterables.
        
        Args:
            fn: The callable to execute on each element (must be picklable)
            *iterables: The iterables to map over (items must be picklable)
            timeout: Maximum time to wait for each result
            chunksize: Size of chunks for processing, can significantly impact performance
            
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
