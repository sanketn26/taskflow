"""
Enhanced executors with comprehensive validation capabilities.
"""
import logging
import multiprocessing as mp
from typing import Any, Dict, List, Optional

from ..task import Task, CallableTask, TaskState
from ..task_group import TaskGroup
from ..validation import ValidationPolicy, ResourceValidator, DependencyValidator, ValidationSeverity
from ..context import TaskContext
from .base import BaseExecutor

logger = logging.getLogger(__name__)

class ValidatingTaskExecutor:
    """Task executor that validates all tasks before execution."""
    
    def __init__(self, executor: BaseExecutor, validate_by_default: bool = True):
        self.executor = executor
        self.validate_by_default = validate_by_default
        self.active_tasks: Dict[str, Task] = {}
        self.validation_stats = {
            'tasks_validated': 0,
            'tasks_failed_validation': 0,
            'groups_validated': 0,
            'groups_failed_validation': 0
        }
    
    def execute_task(self, task: Task, force_validation: bool = None) -> Task:
        """Execute a single task with optional pre-validation."""
        should_validate = force_validation if force_validation is not None else self.validate_by_default
        
        if should_validate:
            if not task.validate_for_execution():
                self.validation_stats['tasks_failed_validation'] += 1
                return task  # Task is already in FAILED state with validation errors
            
            self.validation_stats['tasks_validated'] += 1
        
        # Proceed with execution
        self.active_tasks[task.id] = task
        
        try:
            # Execute using the underlying executor
            if isinstance(task, CallableTask):
                future = self.executor.submit(task.execute)
                # Wait for completion and handle result
                try:
                    result = future.result()
                    if not task.is_done():  # Task might have been completed in execute()
                        task.complete_execution(result)
                except Exception as e:
                    if not task.is_done():  # Task might have been failed in execute()
                        task.fail_execution(e)
            else:
                # For non-callable tasks, just mark as completed
                task.start_execution()  # Only start execution for non-callable tasks
                task.complete_execution(f"Task {task.id} completed successfully")
            
        except Exception as e:
            if not task.is_done():
                task.fail_execution(e)
        finally:
            self.active_tasks.pop(task.id, None)
        
        return task
    
    def execute_callable(self, func, *args, task_id: str = None, 
                        context: TaskContext = None, estimated_duration: float = None,
                        memory_requirement_mb: float = None, **kwargs) -> Task:
        """Execute a callable function as a task."""
        task = CallableTask(
            func, *args, 
            task_id=task_id,
            context=context,
            estimated_duration=estimated_duration,
            memory_requirement_mb=memory_requirement_mb,
            **kwargs
        )
        return self.execute_task(task)
    
    def execute_group(self, group: TaskGroup, force_validation: bool = None) -> TaskGroup:
        """Execute a task group with optional pre-validation."""
        should_validate = force_validation if force_validation is not None else self.validate_by_default
        
        if should_validate:
            if not group.validate_group_for_execution():
                self.validation_stats['groups_failed_validation'] += 1
                return group  # Group validation failed
            
            self.validation_stats['groups_validated'] += 1
        
        # Execute all tasks in the group
        for task in group.tasks:
            if task.state == TaskState.VALIDATED:  # Only execute validated tasks
                self.execute_task(task, force_validation=False)  # Skip re-validation
        
        return group
    
    def get_validation_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return self.validation_stats.copy()
    
    def get_active_tasks(self) -> Dict[str, Task]:
        """Get currently active tasks."""
        return self.active_tasks.copy()
    
    def shutdown(self, wait: bool = True):
        """Shutdown the underlying executor."""
        self.executor.shutdown(wait=wait)

class ComprehensiveTaskExecutor(ValidatingTaskExecutor):
    """Task executor with comprehensive validation policies."""
    
    def __init__(self, executor: BaseExecutor):
        super().__init__(executor)
        self.validation_policy = ValidationPolicy()
        self.resource_validator = ResourceValidator()
        self.dependency_validator = DependencyValidator()
        self.setup_default_policies()
    
    def setup_default_policies(self):
        """Setup default validation policies."""
        # Add standard validation rules
        self.validation_policy.add_rule(
            lambda task: self.resource_validator.validate_system_resources(task),
            severity=ValidationSeverity.ERROR,
            description='System resource check'
        )
        
        self.validation_policy.add_rule(
            lambda task: self._validate_task_naming(task),
            severity=ValidationSeverity.ERROR, 
            description='Task naming convention'
        )
        
        self.validation_policy.add_rule(
            lambda task: self._validate_estimated_duration(task),
            severity=ValidationSeverity.WARNING,
            description='Estimated duration reasonableness'
        )
    
    def _validate_task_naming(self, task: Task) -> bool:
        """Validate task naming conventions."""
        if not task.id or len(task.id) < 3:
            raise Exception("Task ID too short (minimum 3 characters)")
        
        if not task.id.replace('_', '').replace('-', '').isalnum():
            raise Exception("Task ID contains invalid characters")
        
        return True
    
    def _validate_estimated_duration(self, task: Task) -> bool:
        """Validate estimated duration is reasonable."""
        if task.estimated_duration:
            if task.estimated_duration > 3600:  # 1 hour
                raise Exception(f"Estimated duration very long: {task.estimated_duration}s")
            
            if task.estimated_duration <= 0:
                raise Exception("Estimated duration must be positive")
        
        return True
    
    def execute_task_with_policy(self, task: Task) -> Task:
        """Execute task with comprehensive policy validation."""
        # Run policy validation
        passed, issues = self.validation_policy.validate(task)
        
        # Log issues
        for issue in issues:
            if issue['severity'] == ValidationSeverity.ERROR:
                logger.error(f"Validation error for {task.id}: {issue['error']}")
            elif issue['severity'] == ValidationSeverity.WARNING:
                logger.warning(f"Validation warning for {task.id}: {issue['error']}")
            else:
                logger.info(f"Validation info for {task.id}: {issue['error']}")
        
        # Fail task if there were errors
        if not passed:
            from ..exceptions import PreExecutionValidationError
            error_issues = [i for i in issues if i['severity'] == ValidationSeverity.ERROR]
            validation_error = PreExecutionValidationError(
                f"Policy validation failed: {[i['error'] for i in error_issues]}"
            )
            
            task.state = TaskState.FAILED
            task.exception = validation_error
            task._trigger_callbacks('on_validation_failed', task, validation_error)
            task._trigger_callbacks('on_error', task, validation_error)
            task._trigger_callbacks('on_complete', task)
            return task
        
        # Proceed with normal execution
        return super().execute_task(task)
    
    def add_dependency(self, task_id: str, depends_on: List[str]):
        """Add task dependency relationship."""
        self.dependency_validator.add_dependency(task_id, depends_on)
    
    def execute_group_with_dependencies(self, group: TaskGroup) -> TaskGroup:
        """Execute group with dependency validation."""
        try:
            # Validate dependencies
            self.dependency_validator.validate_dependencies(group.tasks)
            
            # Execute with topological ordering
            execution_order = self.dependency_validator.get_execution_order(group.tasks)
            
            for task in execution_order:
                self.execute_task_with_policy(task)
            
            return group
            
        except Exception as e:
            logger.error(f"Group execution failed: {e}")
            group.validation_errors.append(str(e))
            group._trigger_callbacks('on_validation_failed', group, e)
            return group
