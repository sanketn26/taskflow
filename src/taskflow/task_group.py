"""
Task group management with collective validation and execution coordination.
"""
import logging
import time
import uuid
import weakref
from typing import Any, Callable, Dict, List, Optional

from .task import Task, TaskState
from .exceptions import PreExecutionValidationError, TaskGroupValidationError

logger = logging.getLogger(__name__)

class TaskGroup:
    """TaskGroup with collective pre-execution validation."""
    
    def __init__(self, name: str = None, max_concurrent: Optional[int] = None):
        self.name = name or str(uuid.uuid4())
        self.max_concurrent = max_concurrent
        self.tasks: List[Task] = []
        self.validation_errors = []
        
        self.callbacks = {
            'on_all_complete': [],
            'on_any_error': [],
            'on_progress': [],
            'on_validation_failed': [],
            'on_group_validated': []
        }
    
    def add_task(self, task: Task) -> 'TaskGroup':
        """Add task to group."""
        self.tasks.append(task)
        
        # Add group-level callbacks to task
        def on_task_complete(t):
            if self.all_done():
                self._trigger_callbacks('on_all_complete', self)
        
        def on_task_error(t, exc):
            self._trigger_callbacks('on_any_error', self, t, exc)
        
        def on_task_progress(t, progress):
            overall_progress = self.get_overall_progress()
            self._trigger_callbacks('on_progress', self, overall_progress)
        
        task.on_complete(on_task_complete)
        task.on_error(on_task_error)
        task.on_progress(on_task_progress)
        
        return self
    
    def add_tasks(self, tasks: List[Task]) -> 'TaskGroup':
        """Add multiple tasks to group."""
        for task in tasks:
            self.add_task(task)
        return self
    
    def validate_group_for_execution(self) -> bool:
        """Validate entire group before execution."""
        self.validation_errors = []
        logger.info(f"Validating task group '{self.name}' with {len(self.tasks)} tasks")
        
        try:
            # 1. Check if group is empty
            if not self.tasks:
                raise PreExecutionValidationError("Task group is empty")
            
            # 2. Check concurrency limits
            if self.max_concurrent and len(self.tasks) > self.max_concurrent:
                logger.warning(
                    f"Group has {len(self.tasks)} tasks but max_concurrent is {self.max_concurrent}"
                )
            
            # 3. Validate each task
            failed_tasks = []
            total_estimated_duration = 0
            
            for task in self.tasks:
                if not task.validate_for_execution():
                    failed_tasks.append(task)
                    self.validation_errors.extend(task.get_validation_errors())
                else:
                    if task.estimated_duration:
                        total_estimated_duration += task.estimated_duration
            
            if failed_tasks:
                raise PreExecutionValidationError(
                    f"{len(failed_tasks)} out of {len(self.tasks)} tasks failed validation"
                )
            
            # 4. Check collective resource requirements
            if not self._check_collective_resources():
                raise PreExecutionValidationError("Insufficient resources for task group")
            
            # 5. Check collective time requirements
            if not self._check_collective_timing(total_estimated_duration):
                raise PreExecutionValidationError("Insufficient time for task group completion")
            
            # 6. Check task dependencies within group
            if not self._check_task_dependencies():
                raise PreExecutionValidationError("Task dependencies within group not satisfied")
            
            logger.info(f"Task group '{self.name}' passed validation")
            self._trigger_callbacks('on_group_validated', self)
            return True
            
        except PreExecutionValidationError as e:
            logger.error(f"Task group '{self.name}' failed validation: {e}")
            self.validation_errors.append(str(e))
            self._trigger_callbacks('on_validation_failed', self, e)
            return False
        except Exception as e:
            error_msg = f"Unexpected validation error: {e}"
            logger.error(f"Task group '{self.name}' validation failed: {error_msg}")
            self.validation_errors.append(error_msg)
            validation_error = PreExecutionValidationError(error_msg)
            self._trigger_callbacks('on_validation_failed', self, validation_error)
            return False
    
    def _check_collective_resources(self) -> bool:
        """Check if group has sufficient collective resources."""
        # Example: Check memory, CPU, network connections, etc.
        # This is a placeholder - implement based on your resource model
        total_memory_required = sum(
            task.memory_requirement_mb or 0 for task in self.tasks
        )
        
        if total_memory_required > 0:
            try:
                import psutil
                available_memory = psutil.virtual_memory().available / 1024 / 1024  # MB
                
                if total_memory_required > available_memory:
                    logger.warning(
                        f"Group requires {total_memory_required}MB but only "
                        f"{available_memory:.0f}MB available"
                    )
                    return False
            except ImportError:
                # psutil not available, skip memory checking
                logger.warning("psutil not available, skipping memory validation")
        
        return True
    
    def _check_collective_timing(self, total_estimated_duration: float) -> bool:
        """Check if group can complete within available time."""
        if not self.tasks:
            return True
        
        # Find the most restrictive deadline among all task contexts
        earliest_deadline = None
        for task in self.tasks:
            if task.context.deadline:
                if earliest_deadline is None or task.context.deadline < earliest_deadline:
                    earliest_deadline = task.context.deadline
        
        if earliest_deadline:
            remaining_time = earliest_deadline - time.time()
            
            # For concurrent execution, use the longest single task duration
            if self.max_concurrent and self.max_concurrent > 1:
                max_task_duration = max(
                    (task.estimated_duration or 0 for task in self.tasks), 
                    default=0
                )
                required_time = max_task_duration
            else:
                # Sequential execution
                required_time = total_estimated_duration
            
            if required_time > remaining_time:
                logger.warning(
                    f"Estimated execution time ({required_time:.2f}s) exceeds "
                    f"remaining time ({remaining_time:.2f}s)"
                )
                return False
        
        return True
    
    def _check_task_dependencies(self) -> bool:
        """Check dependencies between tasks in the group."""
        # Placeholder for dependency checking logic
        # Could implement topological sort, dependency graph validation, etc.
        return True
    
    def get_validation_summary(self) -> Dict[str, Any]:
        """Get comprehensive validation summary."""
        return {
            'group_name': self.name,
            'total_tasks': len(self.tasks),
            'validated_tasks': len([t for t in self.tasks if t.state == TaskState.VALIDATED]),
            'failed_tasks': len([t for t in self.tasks if t.state == TaskState.FAILED]),
            'validation_errors': self.validation_errors,
            'task_errors': {
                task.id: task.get_validation_errors() 
                for task in self.tasks 
                if task.get_validation_errors()
            }
        }
    
    def cancel_all(self):
        """Cancel all tasks in group."""
        logger.info(f"Cancelling all tasks in group '{self.name}'")
        for task in self.tasks:
            task.cancel()
    
    def all_done(self) -> bool:
        """Check if all tasks are in final states."""
        return all(task.is_done() for task in self.tasks)
    
    def get_overall_progress(self) -> float:
        """Get overall progress of the group (0.0 to 1.0)."""
        if not self.tasks:
            return 1.0
        return sum(task.get_progress() for task in self.tasks) / len(self.tasks)
    
    def get_completed_count(self) -> int:
        """Get number of completed tasks."""
        return len([t for t in self.tasks if t.state == TaskState.COMPLETED])
    
    def get_failed_count(self) -> int:
        """Get number of failed tasks."""
        return len([t for t in self.tasks if t.state == TaskState.FAILED])
    
    def get_running_count(self) -> int:
        """Get number of running tasks."""
        return len([t for t in self.tasks if t.state == TaskState.RUNNING])
    
    def get_task_by_id(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None
    
    # Callback registration methods
    def on_validation_failed(self, callback: Callable[['TaskGroup', Exception], None]) -> 'TaskGroup':
        """Add callback for validation failures."""
        self.callbacks['on_validation_failed'].append(callback)
        return self
    
    def on_group_validated(self, callback: Callable[['TaskGroup'], None]) -> 'TaskGroup':
        """Add callback for successful group validation."""
        self.callbacks['on_group_validated'].append(callback)
        return self
    
    def on_all_complete(self, callback: Callable[['TaskGroup'], None]) -> 'TaskGroup':
        """Add callback for when all tasks are complete."""
        self.callbacks['on_all_complete'].append(callback)
        return self
    
    def on_any_error(self, callback: Callable[['TaskGroup', Task, Exception], None]) -> 'TaskGroup':
        """Add callback for when any task encounters an error."""
        self.callbacks['on_any_error'].append(callback)
        return self
    
    def on_progress(self, callback: Callable[['TaskGroup', float], None]) -> 'TaskGroup':
        """Add callback for progress updates."""
        self.callbacks['on_progress'].append(callback)
        return self
    
    def _trigger_callbacks(self, callback_type: str, *args):
        """Trigger callbacks of specified type."""
        for callback in self.callbacks[callback_type]:
            try:
                callback(*args)
            except Exception as e:
                logger.error(f"Group callback error in {callback_type}: {e}")
