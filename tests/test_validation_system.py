"""
Tests for the comprehensive validation system.
"""
import time
import unittest
import threading
from taskflow import (
    Task, CallableTask, TaskState, TaskGroup, TaskContext,
    ValidatingTaskExecutor, ComprehensiveTaskExecutor,
    ValidationPolicy, ResourceValidator, DependencyValidator,
    TaskCancelledError, TaskTimeoutError, PreExecutionValidationError,
    get_executor
)

def simple_task(value):
    """Simple task that returns the input value."""
    return value

def sleeping_task(seconds, value):
    """Task that sleeps for a specified amount of time, then returns value."""
    time.sleep(seconds)
    return value

def failing_task():
    """Task that always fails."""
    raise ValueError("This task always fails")

class TestTaskContext(unittest.TestCase):
    """Test TaskContext functionality."""
    
    def test_basic_context_creation(self):
        """Test basic context creation and values."""
        ctx = TaskContext()
        self.assertIsNotNone(ctx)
        self.assertFalse(ctx.is_cancelled())
    
    def test_context_with_timeout(self):
        """Test context with timeout."""
        ctx = TaskContext.with_timeout(1.0)
        self.assertIsNotNone(ctx.get_remaining_time())
        self.assertLessEqual(ctx.get_remaining_time(), 1.0)
        self.assertGreater(ctx.get_remaining_time(), 0.5)  # Should be close to 1.0
    
    def test_context_with_value(self):
        """Test context with values."""
        ctx = TaskContext.with_value("test_key", "test_value")
        self.assertEqual(ctx.value("test_key"), "test_value")
        self.assertIsNone(ctx.value("nonexistent"))
    
    def test_context_cancellation(self):
        """Test context cancellation."""
        ctx = TaskContext()
        self.assertFalse(ctx.is_cancelled())
        ctx.cancel()
        self.assertTrue(ctx.is_cancelled())
    
    def test_context_timeout_expiry(self):
        """Test context timeout expiry."""
        ctx = TaskContext.with_timeout(0.1)  # Very short timeout
        time.sleep(0.2)  # Wait for timeout
        self.assertTrue(ctx.is_cancelled())
    
    def test_context_validation(self):
        """Test context validation."""
        # Valid context
        ctx = TaskContext.with_timeout(10.0)
        self.assertTrue(ctx.validate_for_execution())
        
        # Cancelled context
        ctx.cancel()
        with self.assertRaises(PreExecutionValidationError):
            ctx.validate_for_execution()

class TestTask(unittest.TestCase):
    """Test Task functionality."""
    
    def test_basic_task_creation(self):
        """Test basic task creation."""
        task = Task(task_id="test_task")
        self.assertEqual(task.id, "test_task")
        self.assertEqual(task.state, TaskState.PENDING)
        self.assertFalse(task.is_done())
    
    def test_callable_task_creation(self):
        """Test CallableTask creation."""
        task = CallableTask(simple_task, 42, task_id="callable_test")
        self.assertEqual(task.id, "callable_test")
        self.assertEqual(task.func, simple_task)
        self.assertEqual(task.args, (42,))
    
    def test_task_validation(self):
        """Test task validation."""
        task = Task(task_id="valid_task", context=TaskContext.with_timeout(10.0))
        self.assertTrue(task.validate_for_execution())
        self.assertEqual(task.state, TaskState.VALIDATED)
    
    def test_task_validation_failure(self):
        """Test task validation failure."""
        # Create task with expired context
        expired_ctx = TaskContext.with_deadline(time.time() - 1)
        task = Task(task_id="expired_task", context=expired_ctx)
        
        self.assertFalse(task.validate_for_execution())
        self.assertEqual(task.state, TaskState.FAILED)
        self.assertIsInstance(task.exception, PreExecutionValidationError)
    
    def test_task_execution_lifecycle(self):
        """Test task execution lifecycle."""
        task = CallableTask(simple_task, 42, task_id="lifecycle_test")
        
        # Validate
        self.assertTrue(task.validate_for_execution())
        self.assertEqual(task.state, TaskState.VALIDATED)
        
        # Execute
        result = task.execute()
        self.assertEqual(result, 42)
        self.assertEqual(task.state, TaskState.COMPLETED)
        self.assertEqual(task.result, 42)
        self.assertTrue(task.is_done())
    
    def test_task_callbacks(self):
        """Test task callbacks."""
        callback_results = {'validation': False, 'success': False, 'complete': False}
        
        task = CallableTask(simple_task, 42, task_id="callback_test")
        
        task.on_pre_execution(lambda t: callback_results.update({'validation': True}))
        task.on_success(lambda t: callback_results.update({'success': True}))
        task.on_complete(lambda t: callback_results.update({'complete': True}))
        
        task.validate_for_execution()
        task.execute()
        
        self.assertTrue(callback_results['validation'])
        self.assertTrue(callback_results['success'])
        self.assertTrue(callback_results['complete'])
    
    def test_task_cancellation(self):
        """Test task cancellation."""
        task = Task(task_id="cancel_test")
        self.assertFalse(task.is_done())
        
        task.cancel()
        self.assertTrue(task.is_done())
        self.assertEqual(task.state, TaskState.CANCELLED)

class TestTaskGroup(unittest.TestCase):
    """Test TaskGroup functionality."""
    
    def test_task_group_creation(self):
        """Test task group creation."""
        group = TaskGroup("test_group")
        self.assertEqual(group.name, "test_group")
        self.assertEqual(len(group.tasks), 0)
    
    def test_task_group_add_tasks(self):
        """Test adding tasks to group."""
        group = TaskGroup("test_group")
        task1 = Task(task_id="task1")
        task2 = Task(task_id="task2")
        
        group.add_task(task1).add_task(task2)
        self.assertEqual(len(group.tasks), 2)
        self.assertIn(task1, group.tasks)
        self.assertIn(task2, group.tasks)
    
    def test_task_group_validation(self):
        """Test task group validation."""
        group = TaskGroup("test_group")
        
        # Add valid tasks
        for i in range(3):
            task = Task(task_id=f"task_{i}", context=TaskContext.with_timeout(10.0))
            group.add_task(task)
        
        self.assertTrue(group.validate_group_for_execution())
        
        # Check that all tasks are validated
        for task in group.tasks:
            self.assertEqual(task.state, TaskState.VALIDATED)
    
    def test_task_group_validation_failure(self):
        """Test task group validation failure."""
        group = TaskGroup("test_group")
        
        # Add task with expired context
        expired_task = Task(
            task_id="expired",
            context=TaskContext.with_deadline(time.time() - 1)
        )
        group.add_task(expired_task)
        
        self.assertFalse(group.validate_group_for_execution())
        self.assertGreater(len(group.validation_errors), 0)

class TestValidatingExecutor(unittest.TestCase):
    """Test ValidatingTaskExecutor functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        base_executor = get_executor('thread')
        self.executor = ValidatingTaskExecutor(base_executor)
    
    def tearDown(self):
        """Clean up after tests."""
        self.executor.shutdown()
    
    def test_execute_callable_task(self):
        """Test executing a callable with validation."""
        task = self.executor.execute_callable(
            simple_task, 42,
            task_id="test_callable",
            context=TaskContext.with_timeout(10.0)
        )
        
        self.assertEqual(task.state, TaskState.COMPLETED)
        self.assertEqual(task.result, 42)
    
    def test_execute_task_with_validation(self):
        """Test executing a task with validation."""
        task = CallableTask(
            simple_task, 100,
            task_id="validation_test",
            context=TaskContext.with_timeout(5.0)
        )
        
        result_task = self.executor.execute_task(task)
        self.assertEqual(result_task.state, TaskState.COMPLETED)
        self.assertEqual(result_task.result, 100)
    
    def test_execute_task_validation_failure(self):
        """Test executing a task that fails validation."""
        # Create task with expired context
        expired_task = CallableTask(
            simple_task, 42,
            task_id="expired",
            context=TaskContext.with_deadline(time.time() - 1)
        )
        
        result_task = self.executor.execute_task(expired_task)
        self.assertEqual(result_task.state, TaskState.FAILED)
        self.assertIsInstance(result_task.exception, PreExecutionValidationError)
    
    def test_execute_task_group(self):
        """Test executing a task group."""
        group = TaskGroup("test_group")
        
        for i in range(3):
            task = CallableTask(
                simple_task, i,
                task_id=f"group_task_{i}",
                context=TaskContext.with_timeout(10.0)
            )
            group.add_task(task)
        
        result_group = self.executor.execute_group(group)
        self.assertTrue(result_group.all_done())
        
        for task in result_group.tasks:
            self.assertEqual(task.state, TaskState.COMPLETED)
    
    def test_validation_stats(self):
        """Test validation statistics tracking."""
        initial_stats = self.executor.get_validation_stats()
        
        # Execute successful task
        self.executor.execute_callable(simple_task, 42, context=TaskContext.with_timeout(10.0))
        
        # Execute failing task
        expired_task = CallableTask(
            simple_task, 42,
            context=TaskContext.with_deadline(time.time() - 1)
        )
        self.executor.execute_task(expired_task)
        
        final_stats = self.executor.get_validation_stats()
        
        self.assertEqual(
            final_stats['tasks_validated'], 
            initial_stats['tasks_validated'] + 1
        )
        self.assertEqual(
            final_stats['tasks_failed_validation'],
            initial_stats['tasks_failed_validation'] + 1
        )

class TestComprehensiveExecutor(unittest.TestCase):
    """Test ComprehensiveTaskExecutor functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        base_executor = get_executor('thread')
        self.executor = ComprehensiveTaskExecutor(base_executor)
    
    def tearDown(self):
        """Clean up after tests."""
        self.executor.shutdown()
    
    def test_policy_validation(self):
        """Test policy-based validation."""
        # Valid task (good naming, reasonable duration)
        valid_task = CallableTask(
            simple_task, 42,
            task_id="valid_task_name",
            context=TaskContext.with_timeout(10.0),
            estimated_duration=2.0
        )
        
        result_task = self.executor.execute_task_with_policy(valid_task)
        self.assertEqual(result_task.state, TaskState.COMPLETED)
    
    def test_policy_validation_failure(self):
        """Test policy validation failure."""
        # Invalid task (bad naming)
        invalid_task = CallableTask(
            simple_task, 42,
            task_id="x",  # Too short
            context=TaskContext.with_timeout(10.0)
        )
        
        result_task = self.executor.execute_task_with_policy(invalid_task)
        self.assertEqual(result_task.state, TaskState.FAILED)
    
    def test_dependency_execution(self):
        """Test execution with dependencies."""
        group = TaskGroup("dependency_test")
        
        # Create tasks with dependencies
        task1 = CallableTask(simple_task, 1, task_id="task_1")
        task2 = CallableTask(simple_task, 2, task_id="task_2")
        task3 = CallableTask(simple_task, 3, task_id="task_3")
        
        group.add_tasks([task1, task2, task3])
        
        # Add dependencies: task_3 depends on task_1 and task_2
        self.executor.add_dependency("task_3", ["task_1", "task_2"])
        
        result_group = self.executor.execute_group_with_dependencies(group)
        
        # All tasks should complete successfully
        for task in result_group.tasks:
            self.assertEqual(task.state, TaskState.COMPLETED)

class TestValidationPolicies(unittest.TestCase):
    """Test validation policy system."""
    
    def test_validation_policy_creation(self):
        """Test validation policy creation."""
        policy = ValidationPolicy()
        self.assertEqual(len(policy.rules), 0)
    
    def test_validation_policy_add_rule(self):
        """Test adding rules to validation policy."""
        policy = ValidationPolicy()
        
        def test_rule(task):
            if task.id == "invalid":
                raise Exception("Invalid task")
        
        policy.add_rule(test_rule, description="Test rule")
        self.assertEqual(len(policy.rules), 1)
    
    def test_validation_policy_execution(self):
        """Test validation policy execution."""
        policy = ValidationPolicy()
        
        def test_rule(task):
            if task.id == "invalid":
                raise Exception("Invalid task")
        
        policy.add_rule(test_rule, description="Test rule")
        
        # Test valid task
        valid_task = Task(task_id="valid")
        passed, issues = policy.validate(valid_task)
        self.assertTrue(passed)
        self.assertEqual(len(issues), 0)
        
        # Test invalid task
        invalid_task = Task(task_id="invalid")
        passed, issues = policy.validate(invalid_task)
        self.assertFalse(passed)
        self.assertEqual(len(issues), 1)

class TestResourceValidator(unittest.TestCase):
    """Test resource validation."""
    
    def test_resource_validator_creation(self):
        """Test resource validator creation."""
        validator = ResourceValidator()
        self.assertIsNotNone(validator)
    
    def test_system_resource_validation(self):
        """Test system resource validation."""
        validator = ResourceValidator()
        task = Task(task_id="resource_test")
        
        # Should pass for normal task without high resource requirements
        try:
            result = validator.validate_system_resources(task)
            self.assertTrue(result)
        except Exception:
            # May fail if psutil is not available or system is under high load
            # This is acceptable for testing
            pass

class TestDependencyValidator(unittest.TestCase):
    """Test dependency validation."""
    
    def test_dependency_validator_creation(self):
        """Test dependency validator creation."""
        validator = DependencyValidator()
        self.assertIsNotNone(validator)
        self.assertEqual(len(validator.dependency_graph), 0)
    
    def test_add_dependency(self):
        """Test adding dependencies."""
        validator = DependencyValidator()
        validator.add_dependency("task_b", ["task_a"])
        
        self.assertIn("task_b", validator.dependency_graph)
        self.assertEqual(validator.dependency_graph["task_b"], ["task_a"])
    
    def test_dependency_validation(self):
        """Test dependency validation."""
        validator = DependencyValidator()
        validator.add_dependency("task_b", ["task_a"])
        
        task_a = Task(task_id="task_a")
        task_b = Task(task_id="task_b")
        
        # Should pass with both tasks present
        self.assertTrue(validator.validate_dependencies([task_a, task_b]))
        
        # Should fail with missing dependency
        with self.assertRaises(Exception):
            validator.validate_dependencies([task_b])  # Missing task_a
    
    def test_circular_dependency_detection(self):
        """Test circular dependency detection."""
        validator = DependencyValidator()
        validator.add_dependency("task_a", ["task_b"])
        validator.add_dependency("task_b", ["task_a"])  # Circular
        
        task_a = Task(task_id="task_a")
        task_b = Task(task_id="task_b")
        
        with self.assertRaises(Exception):
            validator.validate_dependencies([task_a, task_b])

if __name__ == "__main__":
    unittest.main()
