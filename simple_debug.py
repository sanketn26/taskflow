#!/usr/bin/env python3
"""
Simple debug test without executors.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def simple_task(x):
    return x * 2

def test_basic_task():
    from taskflow.task import CallableTask, TaskState
    from taskflow.context import TaskContext
    
    print("Creating task...")
    task = CallableTask(
        simple_task, 42,
        task_id="test_task",
        context=TaskContext.with_timeout(10.0)
    )
    
    print(f"Initial state: {task.state}")
    
    print("Validating task...")
    validation_result = task.validate_for_execution()
    print(f"Validation result: {validation_result}")
    print(f"State after validation: {task.state}")
    
    if task.exception:
        print(f"Task exception: {task.exception}")
    
    validation_errors = task.get_validation_errors()
    if validation_errors:
        print(f"Validation errors: {validation_errors}")
    
    return task

def test_resource_validator():
    from taskflow.validation import ResourceValidator
    from taskflow.task import Task
    
    print("\nTesting ResourceValidator...")
    validator = ResourceValidator()
    task = Task()
    
    try:
        result = validator.validate_system_resources(task)
        print(f"Resource validation result: {result}")
    except Exception as e:
        print(f"Resource validation failed: {e}")

if __name__ == '__main__':
    print("Starting basic task test...")
    task = test_basic_task()
    test_resource_validator()
