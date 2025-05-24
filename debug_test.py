#!/usr/bin/env python3
"""
Debug script to test task execution.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from taskflow.task import CallableTask, TaskState
from taskflow.context import TaskContext
from taskflow.executors.validating_executor import ValidatingTaskExecutor
from taskflow.executors.thread_executor import ThreadExecutor

def simple_task(x):
    """Simple test function."""
    return x * 2

def main():
    print("Testing task execution...")
    
    # Create executor
    base_executor = ThreadExecutor(max_workers=1)
    executor = ValidatingTaskExecutor(base_executor)
    
    # Create task
    task = CallableTask(
        simple_task, 42,
        task_id="debug_task",
        context=TaskContext.with_timeout(10.0)
    )
    
    print(f"Initial task state: {task.state}")
    print(f"Task validation errors: {task.get_validation_errors()}")
    
    # Test validation
    validation_result = task.validate_for_execution()
    print(f"Validation result: {validation_result}")
    print(f"Task state after validation: {task.state}")
    print(f"Task validation errors: {task.get_validation_errors()}")
    print(f"Task exception: {task.exception}")
    
    if validation_result:
        print("Task validated successfully, executing...")
        try:
            result_task = executor.execute_task(task)
            print(f"Final task state: {result_task.state}")
            print(f"Task result: {result_task.result}")
            print(f"Task exception: {result_task.exception}")
            print(f"Task validation errors: {result_task.get_validation_errors()}")
        except Exception as e:
            print(f"Execution error: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("Task validation failed!")
    
    base_executor.shutdown()

if __name__ == '__main__':
    main()
