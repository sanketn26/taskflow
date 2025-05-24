"""
Enhanced Task class with comprehensive validation and state management.
"""
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

from .context import TaskContext
from .exceptions import PreExecutionValidationError, TaskCancelledError, TaskTimeoutError

logger = logging.getLogger(__name__)

class TaskState(Enum):
    """Task execution states."""
    PENDING = "pending"
    VALIDATED = "validated"  # New state: passed pre-execution checks
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Task:
    """Task with comprehensive pre-execution validation."""
    
    def __init__(self, task_id: str = None, context: TaskContext = None, 
                 estimated_duration: Optional[float] = None,
                 memory_requirement_mb: Optional[float] = None):
        self.id = task_id or str(uuid.uuid4())
        self.context = context or TaskContext()
        self.state = TaskState.PENDING
        self.result = None
        self.exception = None
        self.estimated_duration = estimated_duration
        self.memory_requirement_mb = memory_requirement_mb
        self.actual_start_time = None
        self.actual_end_time = None
        
        self.callbacks = {
            'on_success': [],
            'on_error': [],
            'on_complete': [],
            'on_cancel': [],
            'on_progress': [],
            'on_validation_failed': [],  # New callback type
            'on_pre_execution': []       # Called after validation passes
        }
        
        self._progress = 0.0
        self._validation_errors = []
    
    def add_validation_rule(self, rule: Callable[['Task'], None], description: str = ""):
        """Add custom validation rule for this task."""
        def validation_callback(ctx):
            try:
                rule(self)
            except Exception as e:
                raise Exception(f"{description or 'Custom rule'}: {e}")
        
        self.context.add_validation_callback(validation_callback)
    
    def validate_for_execution(self) -> bool:
        """Comprehensive pre-execution validation for this task."""
        self._validation_errors = []
        
        try:
            logger.info(f"Validating task {self.id} for execution")
            
            # 1. Check task state
            if self.state != TaskState.PENDING:
                raise PreExecutionValidationError(
                    f"Task is not in PENDING state (current: {self.state})"
                )
            
            # 2. Validate context
            self.context.validate_for_execution()
            
            # 3. Check estimated duration against remaining time
            if self.estimated_duration and self.context.deadline:
                remaining_time = self.context.get_remaining_time()
                if remaining_time is not None and self.estimated_duration > remaining_time:
                    raise PreExecutionValidationError(
                        f"Estimated duration ({self.estimated_duration}s) exceeds "
                        f"remaining context time ({remaining_time:.2f}s)"
                    )
            
            # 4. Resource availability checks
            if not self._check_resource_availability():
                raise PreExecutionValidationError("Required resources not available")
            
            # 5. Dependency checks
            if not self._check_dependencies():
                raise PreExecutionValidationError("Task dependencies not met")
            
            # Mark as validated
            self.state = TaskState.VALIDATED
            self._trigger_callbacks('on_pre_execution', self)
            
            logger.info(f"Task {self.id} passed validation")
            return True
            
        except PreExecutionValidationError as e:
            self._validation_errors.append(str(e))
            self.state = TaskState.FAILED
            self.exception = e
            
            logger.error(f"Task {self.id} failed validation: {e}")
            self._trigger_callbacks('on_validation_failed', self, e)
            self._trigger_callbacks('on_error', self, e)
            self._trigger_callbacks('on_complete', self)
            
            return False
        except Exception as e:
            validation_error = PreExecutionValidationError(f"Unexpected validation error: {e}")
            self._validation_errors.append(str(validation_error))
            self.state = TaskState.FAILED
            self.exception = validation_error
            
            logger.error(f"Task {self.id} validation failed unexpectedly: {e}")
            self._trigger_callbacks('on_validation_failed', self, validation_error)
            self._trigger_callbacks('on_error', self, validation_error)
            self._trigger_callbacks('on_complete', self)
            
            return False
    
    def _check_resource_availability(self) -> bool:
        """Check if required resources are available. Override in subclasses."""
        # Basic checks - can be extended
        return True
    
    def _check_dependencies(self) -> bool:
        """Check if task dependencies are satisfied. Override in subclasses."""
        # Basic checks - can be extended
        return True
    
    def start_execution(self):
        """Mark task as starting execution (post-validation)."""
        if self.state == TaskState.VALIDATED:
            self.state = TaskState.RUNNING
            self.actual_start_time = time.time()
            logger.info(f"Task {self.id} started execution")
        else:
            raise RuntimeError(f"Cannot start execution - task not validated (state: {self.state})")
    
    def complete_execution(self, result: Any = None):
        """Mark task as completed with optional result."""
        if self.state == TaskState.RUNNING:
            self.state = TaskState.COMPLETED
            self.result = result
            self.actual_end_time = time.time()
            logger.info(f"Task {self.id} completed successfully")
            self._trigger_callbacks('on_success', self)
            self._trigger_callbacks('on_complete', self)
        else:
            raise RuntimeError(f"Cannot complete - task not running (state: {self.state})")
    
    def fail_execution(self, exception: Exception):
        """Mark task as failed with exception."""
        if self.state == TaskState.RUNNING:
            self.state = TaskState.FAILED
            self.exception = exception
            self.actual_end_time = time.time()
            logger.error(f"Task {self.id} failed: {exception}")
            self._trigger_callbacks('on_error', self, exception)
            self._trigger_callbacks('on_complete', self)
        else:
            raise RuntimeError(f"Cannot fail - task not running (state: {self.state})")
    
    def get_validation_errors(self) -> List[str]:
        """Get list of validation errors."""
        return self._validation_errors.copy()
    
    def cancel(self):
        """Cancel the task."""
        if self.state in [TaskState.PENDING, TaskState.VALIDATED, TaskState.RUNNING]:
            self.state = TaskState.CANCELLED
            self.context.cancel()
            self.actual_end_time = time.time()
            logger.info(f"Task {self.id} cancelled")
            self._trigger_callbacks('on_cancel', self)
            self._trigger_callbacks('on_complete', self)
    
    def is_done(self) -> bool:
        """Check if task is in a final state."""
        return self.state in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED]
    
    # Callback registration methods
    def on_validation_failed(self, callback: Callable[['Task', Exception], None]) -> 'Task':
        """Add callback for validation failures."""
        self.callbacks['on_validation_failed'].append(callback)
        return self
    
    def on_pre_execution(self, callback: Callable[['Task'], None]) -> 'Task':
        """Add callback called after successful validation."""
        self.callbacks['on_pre_execution'].append(callback)
        return self
    
    def on_success(self, callback: Callable[['Task'], None]) -> 'Task':
        """Add callback for successful completion."""
        self.callbacks['on_success'].append(callback)
        return self
    
    def on_error(self, callback: Callable[['Task', Exception], None]) -> 'Task':
        """Add callback for task errors."""
        self.callbacks['on_error'].append(callback)
        return self
    
    def on_complete(self, callback: Callable[['Task'], None]) -> 'Task':
        """Add callback for task completion (success or failure)."""
        self.callbacks['on_complete'].append(callback)
        return self
    
    def on_cancel(self, callback: Callable[['Task'], None]) -> 'Task':
        """Add callback for task cancellation."""
        self.callbacks['on_cancel'].append(callback)
        return self
    
    def on_progress(self, callback: Callable[['Task', float], None]) -> 'Task':
        """Add callback for progress updates."""
        self.callbacks['on_progress'].append(callback)
        return self
    
    def update_progress(self, progress: float):
        """Update task progress (0.0 to 1.0)."""
        self._progress = max(0.0, min(1.0, progress))
        self._trigger_callbacks('on_progress', self, self._progress)
    
    def get_progress(self) -> float:
        """Get current progress."""
        return self._progress
    
    def get_execution_time(self) -> Optional[float]:
        """Get actual execution time if available."""
        if self.actual_start_time and self.actual_end_time:
            return self.actual_end_time - self.actual_start_time
        return None
    
    def _trigger_callbacks(self, callback_type: str, *args):
        """Trigger callbacks of specified type."""
        for callback in self.callbacks[callback_type]:
            try:
                callback(*args)
            except Exception as e:
                logger.error(f"Callback error in {callback_type}: {e}")

class CallableTask(Task):
    """Task that wraps a callable function."""
    
    def __init__(self, func: Callable, *args, task_id: str = None, 
                 context: TaskContext = None, estimated_duration: Optional[float] = None,
                 memory_requirement_mb: Optional[float] = None, **kwargs):
        super().__init__(task_id, context, estimated_duration, memory_requirement_mb)
        self.func = func
        self.args = args
        self.kwargs = kwargs
    
    def execute(self) -> Any:
        """Execute the wrapped function."""
        if self.state != TaskState.VALIDATED:
            raise RuntimeError(f"Task must be validated before execution (current state: {self.state})")
        
        try:
            self.start_execution()
            
            # Check for cancellation before starting
            self.context.check_cancelled()
            
            # Execute the function
            result = self.func(*self.args, **self.kwargs)
            
            # Check for cancellation after execution
            self.context.check_cancelled()
            
            self.complete_execution(result)
            return result
            
        except (TaskCancelledError, TaskTimeoutError) as e:
            self.cancel()
            raise
        except Exception as e:
            self.fail_execution(e)
            raise
