#!/usr/bin/env python3
"""
Comprehensive demonstration of the taskflow validation system.

This example shows:
1. Basic task validation with timeout contexts
2. Resource validation and monitoring
3. Policy-based validation with custom rules
4. Task group validation and execution
5. Dependency validation and circular dependency detection
6. Comprehensive executor with full validation pipeline
"""
import sys
import os
import time
import logging

# Add the src directory to the path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from taskflow.task import CallableTask, TaskState
from taskflow.context import TaskContext
from taskflow.task_group import TaskGroup
from taskflow.validation import ValidationPolicy, ResourceValidator, DependencyValidator, ValidationSeverity
from taskflow.executors import get_executor
from taskflow.executors.validating_executor import ValidatingTaskExecutor, ComprehensiveTaskExecutor
from taskflow.exceptions import PreExecutionValidationError

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def simple_task(x):
    """A simple task that doubles its input."""
    time.sleep(0.1)  # Simulate some work
    return x * 2

def slow_task(x, duration=1.0):
    """A task that takes some time to complete."""
    time.sleep(duration)
    return x

def memory_intensive_task(size_mb=100):
    """A task that uses memory."""
    data = [0] * (size_mb * 1024 * 256)  # Rough MB allocation
    time.sleep(0.1)
    return len(data)

def demo_basic_validation():
    """Demonstrate basic task validation with contexts."""
    print("\n=== Basic Task Validation Demo ===")
    
    # Create a task with timeout context
    context = TaskContext.with_timeout(5.0)
    task = CallableTask(
        simple_task, 42,
        task_id="basic_validation_task",
        context=context,
        estimated_duration=2.0
    )
    
    # Validate the task
    print(f"Task ID: {task.id}")
    print(f"Initial state: {task.state}")
    
    try:
        validation_result = task.validate_for_execution()
        print(f"Validation result: {validation_result}")
        print(f"State after validation: {task.state}")
        
        if task.state == TaskState.VALIDATED:
            # Execute the task
            result = task.execute()
            print(f"Task completed with result: {result}")
            print(f"Final state: {task.state}")
        
    except PreExecutionValidationError as e:
        print(f"Validation failed: {e}")
        print(f"Validation errors: {task.get_validation_errors()}")

def demo_timeout_validation():
    """Demonstrate timeout validation."""
    print("\n=== Timeout Validation Demo ===")
    
    # Create a task with very short timeout
    context = TaskContext.with_timeout(0.5)  # 500ms timeout
    task = CallableTask(
        slow_task, 10, duration=1.0,  # Task needs 1 second but timeout is 500ms
        task_id="timeout_task",
        context=context,
        estimated_duration=1.0
    )
    
    print(f"Task timeout: 0.5s, estimated duration: 1.0s")
    
    try:
        validation_result = task.validate_for_execution()
        print(f"Validation result: {validation_result}")
    except PreExecutionValidationError as e:
        print(f"Expected validation failure: {e}")

def demo_resource_validation():
    """Demonstrate resource validation."""
    print("\n=== Resource Validation Demo ===")
    
    # Create a resource validator
    resource_validator = ResourceValidator(max_cpu_percent=80.0)
    
    # Create a task with memory requirements
    task = CallableTask(
        memory_intensive_task, size_mb=50,
        task_id="memory_task",
        memory_requirement_mb=50.0
    )
    
    print(f"Task memory requirement: {task.memory_requirement_mb}MB")
    
    try:
        result = resource_validator.validate_system_resources(task)
        print(f"Resource validation passed: {result}")
    except Exception as e:
        print(f"Resource validation failed: {e}")

def demo_policy_validation():
    """Demonstrate policy-based validation."""
    print("\n=== Policy-Based Validation Demo ===")
    
    # Create a validation policy with custom rules
    policy = ValidationPolicy()
    
    # Add naming convention rule
    policy.add_rule(
        lambda task: len(task.id) >= 5,
        severity=ValidationSeverity.ERROR,
        description="Task ID must be at least 5 characters"
    )
    
    # Add duration reasonableness rule
    policy.add_rule(
        lambda task: not task.estimated_duration or task.estimated_duration <= 10.0,
        severity=ValidationSeverity.WARNING,
        description="Task duration should be reasonable"
    )
    
    # Test with valid task
    valid_task = CallableTask(
        simple_task, 42,
        task_id="valid_task_name",
        estimated_duration=2.0
    )
    
    print(f"Validating task: {valid_task.id}")
    passed, issues = policy.validate(valid_task)
    print(f"Validation passed: {passed}")
    for issue in issues:
        print(f"  {issue['severity'].value}: {issue['description']} - {issue['error']}")
    
    # Test with invalid task
    invalid_task = CallableTask(
        simple_task, 42,
        task_id="bad",  # Too short
        estimated_duration=15.0  # Too long
    )
    
    print(f"\nValidating task: {invalid_task.id}")
    passed, issues = policy.validate(invalid_task)
    print(f"Validation passed: {passed}")
    for issue in issues:
        print(f"  {issue['severity'].value}: {issue['description']} - {issue['error']}")

def demo_task_group_validation():
    """Demonstrate task group validation."""
    print("\n=== Task Group Validation Demo ===")
    
    # Create a task group
    group = TaskGroup("demo_group", max_concurrent=2)
    
    # Add tasks to the group
    for i in range(3):
        task = CallableTask(
            simple_task, i,
            task_id=f"group_task_{i}",
            context=TaskContext.with_timeout(10.0),
            estimated_duration=1.0
        )
        group.add_task(task)
    
    print(f"Group: {group.name} with {len(group.tasks)} tasks")
    print(f"Max concurrent: {group.max_concurrent}")
    
    # Validate the group
    try:
        validation_result = group.validate_group_for_execution()
        print(f"Group validation result: {validation_result}")
        
        if validation_result:
            print("All tasks in group are validated and ready for execution")
            for task in group.tasks:
                print(f"  Task {task.id}: {task.state}")
        
    except Exception as e:
        print(f"Group validation failed: {e}")

def demo_dependency_validation():
    """Demonstrate dependency validation."""
    print("\n=== Dependency Validation Demo ===")
    
    # Create dependency validator
    dep_validator = DependencyValidator()
    
    # Set up dependencies: task_c depends on task_b, task_b depends on task_a
    dep_validator.add_dependency("task_c", ["task_b"])
    dep_validator.add_dependency("task_b", ["task_a"])
    
    # Create tasks
    tasks = []
    for task_id in ["task_a", "task_b", "task_c"]:
        task = CallableTask(
            simple_task, 1,
            task_id=task_id
        )
        tasks.append(task)
    
    print("Tasks:", [t.id for t in tasks])
    print("Dependencies: task_c -> task_b -> task_a")
    
    try:
        # Validate dependencies
        dep_validator.validate_dependencies(tasks)
        print("Dependency validation passed")
        
        # Get execution order
        execution_order = dep_validator.get_execution_order(tasks)
        print("Execution order:", [t.id for t in execution_order])
        
    except Exception as e:
        print(f"Dependency validation failed: {e}")

def demo_circular_dependency():
    """Demonstrate circular dependency detection."""
    print("\n=== Circular Dependency Detection Demo ===")
    
    dep_validator = DependencyValidator()
    
    # Create circular dependency: A -> B -> C -> A
    dep_validator.add_dependency("task_a", ["task_c"])
    dep_validator.add_dependency("task_b", ["task_a"])
    dep_validator.add_dependency("task_c", ["task_b"])
    
    tasks = []
    for task_id in ["task_a", "task_b", "task_c"]:
        task = CallableTask(simple_task, 1, task_id=task_id)
        tasks.append(task)
    
    print("Tasks:", [t.id for t in tasks])
    print("Dependencies: task_a -> task_c -> task_b -> task_a (circular)")
    
    try:
        dep_validator.validate_dependencies(tasks)
        print("Dependency validation passed (unexpected!)")
    except Exception as e:
        print(f"Expected circular dependency error: {e}")

def demo_comprehensive_executor(executor_kind="thread", max_workers=2, max_processes=None):
    """Demonstrate the comprehensive executor with full validation."""
    print(f"\n=== Comprehensive Executor Demo ({executor_kind}) ===")
    base_executor = get_executor(executor_kind, max_workers=max_workers, max_processes=max_processes)
    executor = ComprehensiveTaskExecutor(base_executor)

    # Execute valid task
    print("Executing valid task...")
    valid_task = CallableTask(
        simple_task, 100,
        task_id="comprehensive_valid_task",
        context=TaskContext.with_timeout(10.0),
        estimated_duration=2.0
    )
    result_task = executor.execute_task_with_policy(valid_task)
    print(f"Result: {result_task.state}, value: {result_task.result}")

    # Try to execute invalid task
    print("\nExecuting invalid task...")
    invalid_task = CallableTask(
        simple_task, 200,
        task_id="x",  # Too short
        context=TaskContext.with_timeout(10.0)
    )
    result_task = executor.execute_task_with_policy(invalid_task)
    print(f"Result: {result_task.state}")
    if result_task.exception:
        print(f"Exception: {result_task.exception}")

    # Get validation statistics
    stats = executor.get_validation_stats()
    print(f"\nValidation statistics: {stats}")
    executor.shutdown()

import argparse
def main():
    """Run all validation system demonstrations."""
    parser = argparse.ArgumentParser(description="Taskflow Validation System Demonstration")
    parser.add_argument(
        "--executor", type=str, default="thread",
        choices=["thread", "thread+process", "greenlet", "greenlet+process"],
        help="Execution mode for the comprehensive demo"
    )
    parser.add_argument("--max-workers", type=int, default=2, help="Max workers (threads/greenlets)")
    parser.add_argument("--max-processes", type=int, default=None, help="Max processes (for process/hybrid modes)")
    args = parser.parse_args()

    print("Taskflow Validation System Demonstration")
    print("=" * 50)

    try:
        demo_basic_validation()
        demo_timeout_validation()
        demo_resource_validation()
        demo_policy_validation()
        demo_task_group_validation()
        demo_dependency_validation()
        demo_circular_dependency()
        demo_comprehensive_executor(
            executor_kind=args.executor,
            max_workers=args.max_workers,
            max_processes=args.max_processes
        )

        print("\n" + "=" * 50)
        print("All validation system demonstrations completed!")

    except Exception as e:
        logger.error(f"Demo failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
