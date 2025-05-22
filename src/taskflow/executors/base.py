from abc import ABC, abstractmethod
from typing import Callable, Any, Iterable, TypeVar, Generic, List, Iterator, Optional
from concurrent.futures import Future

T = TypeVar('T')
R = TypeVar('R')

class BaseExecutor(ABC, Generic[T]):
    """Base Executor interface for all taskflow executors.
    
    This abstract base class defines the common interface that all executors
    must implement, allowing code to seamlessly switch between threads,
    processes, and gevent without changing the task code.
    """
    
    @abstractmethod
    def submit(self, fn: Callable[..., R], *args, **kwargs) -> T:
        """Submit a callable to be executed.
        
        Args:
            fn: The callable to execute
            *args: Arguments to pass to the callable
            **kwargs: Keyword arguments to pass to the callable
            
        Returns:
            A future-like object representing the execution
        """
        pass

    @abstractmethod
    def map(self, fn: Callable[[Any], R], *iterables, timeout: Optional[float] = None, 
            chunksize: int = 1) -> Iterator[R]:
        """Map a function over iterables.
        
        Args:
            fn: The callable to execute on each element
            *iterables: The iterables to map over
            timeout: Maximum time to wait for each result
            chunksize: Size of chunks for processing (where applicable)
            
        Returns:
            An iterator over the results
        """
        pass

    @abstractmethod
    def shutdown(self, wait: bool = True) -> None:
        """Clean up resources.
        
        Args:
            wait: If True, wait for all pending tasks to complete
        """
        pass
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(wait=True)
        return False
