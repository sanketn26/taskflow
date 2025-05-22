from typing import Any, Optional, TypeVar
from .base import BaseExecutor
import importlib.util

T = TypeVar('T')

class GeventExecutor(BaseExecutor[Any]):
    """Greenlet-based executor implementation using gevent.
    
    This executor is suitable for I/O-bound tasks that benefit from
    cooperative multitasking provided by greenlets.
    """
    
    def __init__(self, max_workers: Optional[int] = None):
        """Initialize a new GeventExecutor.
        
        Args:
            max_workers: The maximum number of concurrent greenlets. 
                        If None, no limit is applied.
        """
        if importlib.util.find_spec("gevent") is None:
            raise ImportError(
                "gevent is required for GeventExecutor. "
                "Install it with 'pip install gevent'."
            )
        
        import gevent
        import gevent.pool
        import gevent.monkey
        
        self.gevent = gevent
        self._pool = gevent.pool.Pool(max_workers)
        
        # Check monkey patching
        if not gevent.monkey.is_module_patched('socket'):
            import warnings
            warnings.warn(
                "Using GeventExecutor without monkey patching. "
                "Call gevent.monkey.patch_all() at the start of your program "
                "for best performance."
            )

    def submit(self, fn, *args, **kwargs):
        """Submit a callable for execution.
        
        Args:
            fn: The callable to execute
            *args: Arguments to pass to the callable
            **kwargs: Keyword arguments to pass to the callable
            
        Returns:
            A Greenlet representing the execution
        """
        greenlet = self._pool.spawn(fn, *args, **kwargs)
        
        # Add result() method for compatibility with concurrent.futures.Future
        if not hasattr(greenlet, 'result'):
            original_get = greenlet.get
            
            def result_method(timeout=None):
                return original_get(timeout=timeout)
            
            # Use setattr instead of direct assignment to avoid type checking issues
            setattr(greenlet, 'result', result_method)
        
        return greenlet

    def map(self, fn, *iterables, timeout=None, chunksize=1):
        """Map a function over iterables.
        
        Args:
            fn: The callable to execute on each element
            *iterables: The iterables to map over
            timeout: Maximum time to wait for each result
            chunksize: Size of chunks for processing (ignored in this implementation)
            
        Returns:
            An iterator over the results
        """
        # Gevent's pool.imap is similar to map, but doesn't use chunksize or timeout
        return self._pool.imap(fn, *iterables)

    def shutdown(self, wait=True):
        """Clean up resources by shutting down the executor.
        
        Args:
            wait: If True, wait for all pending tasks to complete
                 before killing the pool. If False, kill immediately.
        """
        if wait:
            self._pool.join(timeout=None)
        self._pool.kill()
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown(wait=True)
        return False
