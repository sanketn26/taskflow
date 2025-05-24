"""
Enhanced task context with timeout, cancellation, and validation support.
"""
import threading
import time
import weakref
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field

from .exceptions import PreExecutionValidationError

@dataclass
class TaskContext:
    """Enhanced context with pre-execution validation and timeout management."""
    
    values: Dict[str, Any] = field(default_factory=dict)
    cancelled: bool = field(default=False, init=False)
    deadline: Optional[float] = field(default=None, init=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, init=False)
    timeout_timer: Optional[threading.Timer] = field(default=None, init=False)
    parent: Optional['TaskContext'] = field(default=None, init=False)
    children: set = field(default_factory=weakref.WeakSet, init=False)
    creation_time: float = field(default_factory=time.time, init=False)
    validation_callbacks: List[Callable] = field(default_factory=list, init=False)
    
    def __post_init__(self):
        # Set up timeout timer if deadline is set
        if self.deadline:
            timeout_duration = max(0, self.deadline - time.time())
            if timeout_duration > 0:
                self.timeout_timer = threading.Timer(timeout_duration, self._timeout_expired)
                self.timeout_timer.start()
            else:
                # Already expired at creation
                self.cancelled = True
    
    @classmethod
    def with_timeout(cls, timeout: float, parent: Optional['TaskContext'] = None) -> 'TaskContext':
        """Create a context that times out after specified seconds."""
        ctx = cls()
        ctx.parent = parent
        ctx.deadline = time.time() + timeout
        if parent:
            parent.children.add(ctx)
            ctx.values = parent.values.copy()
        
        # Set up timeout timer
        timeout_duration = max(0, ctx.deadline - time.time())
        if timeout_duration > 0:
            ctx.timeout_timer = threading.Timer(timeout_duration, ctx._timeout_expired)
            ctx.timeout_timer.start()
        else:
            # Already expired at creation
            ctx.cancelled = True
        
        return ctx
    
    @classmethod
    def with_deadline(cls, deadline: float, parent: Optional['TaskContext'] = None) -> 'TaskContext':
        """Create a context with absolute deadline."""
        ctx = cls()
        ctx.parent = parent
        ctx.deadline = deadline
        if parent:
            parent.children.add(ctx)
            ctx.values = parent.values.copy()
        
        # Set up timeout timer
        timeout_duration = max(0, ctx.deadline - time.time())
        if timeout_duration > 0:
            ctx.timeout_timer = threading.Timer(timeout_duration, ctx._timeout_expired)
            ctx.timeout_timer.start()
        else:
            # Already expired at creation
            ctx.cancelled = True
        
        return ctx
    
    @classmethod
    def with_value(cls, key: str, value: Any, parent: Optional['TaskContext'] = None) -> 'TaskContext':
        """Create a context with a key-value pair."""
        ctx = cls()
        ctx.parent = parent
        if parent:
            parent.children.add(ctx)
            ctx.values = parent.values.copy()
        ctx.values[key] = value
        return ctx
    
    def value(self, key: str) -> Any:
        """Get value from context."""
        return self.values.get(key)
    
    def with_value_chained(self, key: str, value: Any) -> 'TaskContext':
        """Create new context with additional value, chained from this one."""
        return self.with_value(key, value, self)
    
    def add_validation_callback(self, callback: Callable[['TaskContext'], None]):
        """Add custom validation logic."""
        self.validation_callbacks.append(callback)
    
    def validate_for_execution(self) -> bool:
        """Comprehensive pre-execution validation."""
        try:
            # 1. Check basic cancellation state
            if self.cancelled:
                raise PreExecutionValidationError("Context is already cancelled")
            
            # 2. Check parent cancellation
            if self.parent and self.parent.is_cancelled():
                self._cancel()
                raise PreExecutionValidationError("Parent context is cancelled")
            
            # 3. Check deadline
            current_time = time.time()
            if self.deadline:
                if current_time >= self.deadline:
                    self._cancel()
                    raise PreExecutionValidationError(
                        f"Context deadline ({self.deadline}) has already passed (current: {current_time})"
                    )
                
                remaining_time = self.deadline - current_time
                if remaining_time < 0.1:  # Less than 100ms remaining
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Context has very little time remaining: {remaining_time:.3f}s")
            
            # 4. Check context age (optional sanity check)
            context_age = current_time - self.creation_time
            if context_age > 3600:  # 1 hour
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Context is very old: {context_age:.1f} seconds")
            
            # 5. Run custom validation callbacks
            for callback in self.validation_callbacks:
                try:
                    callback(self)
                except Exception as e:
                    raise PreExecutionValidationError(f"Custom validation failed: {e}")
            
            return True
            
        except PreExecutionValidationError:
            raise
        except Exception as e:
            raise PreExecutionValidationError(f"Validation error: {e}")
    
    def get_remaining_time(self) -> Optional[float]:
        """Get remaining time before deadline."""
        if not self.deadline:
            return None
        remaining = self.deadline - time.time()
        return max(0, remaining)
    
    def is_cancelled(self) -> bool:
        """Check if context is cancelled."""
        if self.cancelled:
            return True
        if self.parent and self.parent.is_cancelled():
            self._cancel()
            return True
        if self.deadline and time.time() > self.deadline:
            self._cancel()
            return True
        return False
    
    def cancel(self):
        """Manually cancel the context."""
        self._cancel()
    
    def _cancel(self):
        """Internal cancellation implementation."""
        if self.cancelled:
            return
        
        self.cancelled = True
        self.cancel_event.set()
        
        # Cancel timeout timer
        if self.timeout_timer:
            self.timeout_timer.cancel()
        
        # Cancel all children
        for child in list(self.children):
            child._cancel()
    
    def _timeout_expired(self):
        """Called when timeout timer expires."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Context timeout expired at {time.time()}")
        self._cancel()
    
    def check_cancelled(self):
        """Raise appropriate exception if cancelled."""
        from .exceptions import TaskCancelledError, TaskTimeoutError
        
        if self.is_cancelled():
            if self.deadline and time.time() > self.deadline:
                raise TaskTimeoutError("Context deadline exceeded")
            raise TaskCancelledError("Context was cancelled")
