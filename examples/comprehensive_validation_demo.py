"""
Comprehensive demonstration of the enhanced taskflow validation system.

This example showcases:
1. Basic task creation and validation
2. Task context with timeouts and cancellation
3. Task groups with collective validation
4. Policy-based validation
5. Resource and dependency validation
6. Error handling and callbacks
"""
import logging
import time
from taskflow import (
    Task, CallableTask, TaskGroup, TaskContext,
    ValidatingTaskExecutor, ComprehensiveTaskExecutor,
    ValidationPolicy, ResourceValidator, DependencyValidator,
    get_executor, TaskState
)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def demo_task(task_name: str, duration: float = 1.0, should_fail: bool = False):
    """Demo task that optionally fails."""
    logger.info(f"Starting {task_name}, duration: {duration}s")
    time.sleep(duration)
    
    if should_fail:
        raise ValueError(f"Task {task_name} was configured to fail")
    
    result = f"Completed {task_name} successfully"
    logger.info(result)
    return result

def cpu_intensive_task(iterations: int = 1000000):
    """CPU-intensive task for demonstration."""
    logger.info(f"Starting CPU task with {iterations} iterations")
    result = sum(i * i for i in range(iterations))
    logger.info(f"CPU task completed, result: {result}")
    return result

def io_simulation_task(delay: float = 0.5, data_size: str = "1MB"):
    """Simulates I/O operation."""
    logger.info(f"Starting I/O simulation: {data_size}, delay: {delay}s")
    time.sleep(delay)
    result = f"Processed {data_size} of data"
    logger.info(result)
    return result

def main():
    """Main demonstration function."""
    print("=" * 60)
    print("TASKFLOW COMPREHENSIVE VALIDATION SYSTEM DEMO")
    print("=" * 60)
    
    # 1. Basic Task Creation and Validation
    print("\n1. BASIC TASK CREATION AND VALIDATION")
    print("-" * 40)
    
    # Create a simple task with timeout context
    basic_task = CallableTask(
        demo_task, "basic_demo", 0.5,
        task_id="basic_task_001",
        context=TaskContext.with_timeout(10.0),
        estimated_duration=1.0
    )
    
    # Add callbacks to monitor task lifecycle
    basic_task.on_pre_execution(lambda t: print(f"✓ Task {t.id} passed validation"))
    basic_task.on_success(lambda t: print(f"✓ Task {t.id} completed successfully"))
    basic_task.on_error(lambda t, e: print(f"✗ Task {t.id} failed: {e}"))
    
    # Validate manually
    if basic_task.validate_for_execution():
        print(f"✓ Basic task validation passed")
        print(f"  - State: {basic_task.state}")
        print(f"  - Remaining context time: {basic_task.context.get_remaining_time():.2f}s")
    else:
        print(f"✗ Basic task validation failed: {basic_task.get_validation_errors()}")
    
    # 2. Validating Task Executor
    print("\n2. VALIDATING TASK EXECUTOR")
    print("-" * 40)
    
    base_executor = get_executor('thread')
    validating_executor = ValidatingTaskExecutor(base_executor)
    
    try:
        # Execute the validated task
        result_task = validating_executor.execute_task(basic_task)
        print(f"✓ Task executed via ValidatingExecutor")
        print(f"  - Final state: {result_task.state}")
        print(f"  - Result: {result_task.result}")
        print(f"  - Execution time: {result_task.get_execution_time():.3f}s")
        
        # Execute a callable directly
        direct_task = validating_executor.execute_callable(
            demo_task, "direct_execution", 0.3,
            task_id="direct_task_001",
            context=TaskContext.with_timeout(5.0),
            estimated_duration=0.5
        )
        print(f"✓ Direct callable execution completed: {direct_task.result}")
        
        # Show validation stats
        stats = validating_executor.get_validation_stats()
        print(f"✓ Validation stats: {stats}")
        
    except Exception as e:
        print(f"✗ Execution failed: {e}")
    
    # 3. Task Group Validation
    print("\n3. TASK GROUP VALIDATION")
    print("-" * 40)
    
    # Create a task group
    task_group = TaskGroup("demo_group", max_concurrent=3)
    
    # Add multiple tasks to the group
    group_tasks = []
    for i in range(5):
        task = CallableTask(
            demo_task, f"group_task_{i}", 0.2,
            task_id=f"group_task_{i:03d}",
            context=TaskContext.with_timeout(15.0),
            estimated_duration=0.3
        )
        group_tasks.append(task)
        task_group.add_task(task)
    
    # Add group-level callbacks
    task_group.on_group_validated(lambda g: print(f"✓ Group '{g.name}' validation passed"))
    task_group.on_all_complete(lambda g: print(f"✓ All tasks in group '{g.name}' completed"))
    task_group.on_progress(lambda g, p: print(f"  Group progress: {p*100:.1f}%"))
    
    # Validate and execute the group
    try:
        result_group = validating_executor.execute_group(task_group)
        print(f"✓ Task group execution initiated")
        
        # Wait a bit for tasks to complete and show progress
        time.sleep(1.5)
        
        summary = result_group.get_validation_summary()
        print(f"✓ Group summary:")
        print(f"  - Total tasks: {summary['total_tasks']}")
        print(f"  - Completed: {result_group.get_completed_count()}")
        print(f"  - Failed: {result_group.get_failed_count()}")
        print(f"  - Overall progress: {result_group.get_overall_progress()*100:.1f}%")
        
    except Exception as e:
        print(f"✗ Group execution failed: {e}")
    
    # 4. Comprehensive Validation with Policies
    print("\n4. COMPREHENSIVE VALIDATION WITH POLICIES")
    print("-" * 40)
    
    comprehensive_executor = ComprehensiveTaskExecutor(get_executor('thread'))
    
    # Test valid task with good naming and duration
    valid_policy_task = CallableTask(
        demo_task, "policy_compliant", 0.3,
        task_id="well_named_task_001",
        context=TaskContext.with_timeout(10.0),
        estimated_duration=0.5
    )
    
    try:
        result = comprehensive_executor.execute_task_with_policy(valid_policy_task)
        print(f"✓ Policy-compliant task executed: {result.state}")
    except Exception as e:
        print(f"✗ Policy-compliant task failed: {e}")
    
    # Test invalid task (bad naming)
    invalid_policy_task = CallableTask(
        demo_task, "policy_violation", 0.1,
        task_id="x",  # Too short, violates naming policy
        context=TaskContext.with_timeout(10.0)
    )
    
    try:
        result = comprehensive_executor.execute_task_with_policy(invalid_policy_task)
        print(f"✗ Policy-violating task unexpectedly succeeded")
    except Exception as e:
        print(f"✓ Policy-violating task correctly failed validation")
    
    # 5. Dependency Management
    print("\n5. DEPENDENCY MANAGEMENT")
    print("-" * 40)
    
    # Create tasks with dependencies
    dependency_group = TaskGroup("dependency_demo")
    
    # Task A - no dependencies
    task_a = CallableTask(
        demo_task, "foundation_task", 0.2,
        task_id="task_a",
        context=TaskContext.with_timeout(10.0)
    )
    
    # Task B - depends on A
    task_b = CallableTask(
        cpu_intensive_task, 100000,  # Smaller number for demo
        task_id="task_b",
        context=TaskContext.with_timeout(10.0)
    )
    
    # Task C - depends on A and B
    task_c = CallableTask(
        io_simulation_task, 0.1, "500KB",
        task_id="task_c", 
        context=TaskContext.with_timeout(10.0)
    )
    
    dependency_group.add_tasks([task_a, task_b, task_c])
    
    # Set up dependencies
    comprehensive_executor.add_dependency("task_b", ["task_a"])
    comprehensive_executor.add_dependency("task_c", ["task_a", "task_b"])
    
    try:
        print(f"✓ Executing tasks with dependencies...")
        result_group = comprehensive_executor.execute_group_with_dependencies(dependency_group)
        
        # Wait for completion
        time.sleep(2.0)
        
        print(f"✓ Dependency execution results:")
        for task in result_group.tasks:
            print(f"  - {task.id}: {task.state}")
            
    except Exception as e:
        print(f"✗ Dependency execution failed: {e}")
    
    # 6. Error Handling and Edge Cases
    print("\n6. ERROR HANDLING AND EDGE CASES")
    print("-" * 40)
    
    # Test timeout scenario
    timeout_task = CallableTask(
        demo_task, "timeout_test", 2.0,  # Long task
        task_id="timeout_task",
        context=TaskContext.with_timeout(0.5),  # Short timeout
        estimated_duration=2.0
    )
    
    timeout_task.on_validation_failed(lambda t, e: print(f"✓ Timeout task correctly failed validation: {e}"))
    
    try:
        result = validating_executor.execute_task(timeout_task)
        if result.state == TaskState.FAILED:
            print(f"✓ Timeout validation working correctly")
        else:
            print(f"✗ Timeout task should have failed validation")
    except Exception as e:
        print(f"✓ Timeout handling: {e}")
    
    # Test task that fails during execution
    failing_task = CallableTask(
        demo_task, "failing_test", 0.1, should_fail=True,
        task_id="failing_task",
        context=TaskContext.with_timeout(10.0)
    )
    
    failing_task.on_error(lambda t, e: print(f"✓ Failing task error handled: {type(e).__name__}"))
    
    try:
        result = validating_executor.execute_task(failing_task)
        print(f"✓ Task failure handling: {result.state}")
    except Exception as e:
        print(f"✓ Exception properly propagated: {type(e).__name__}")
    
    # 7. Context Cancellation
    print("\n7. CONTEXT CANCELLATION")
    print("-" * 40)
    
    # Create a long-running task
    long_task = CallableTask(
        demo_task, "cancellation_test", 3.0,
        task_id="long_running_task",
        context=TaskContext.with_timeout(10.0)
    )
    
    long_task.on_cancel(lambda t: print(f"✓ Task {t.id} was cancelled"))
    
    # Start the task but cancel it quickly
    import threading
    def cancel_after_delay():
        time.sleep(0.2)
        long_task.cancel()
        print("✓ Cancellation signal sent")
    
    cancel_thread = threading.Thread(target=cancel_after_delay)
    cancel_thread.start()
    
    try:
        result = validating_executor.execute_task(long_task)
        print(f"✓ Cancellation test result: {result.state}")
    except Exception as e:
        print(f"✓ Cancellation exception: {type(e).__name__}")
    
    cancel_thread.join()
    
    # Clean up executors
    validating_executor.shutdown()
    comprehensive_executor.shutdown()
    
    print("\n" + "=" * 60)
    print("DEMONSTRATION COMPLETED")
    print("=" * 60)
    print("\nKey features demonstrated:")
    print("✓ Pre-execution validation with context timeouts")
    print("✓ Task lifecycle management with callbacks")
    print("✓ Task group collective validation")
    print("✓ Policy-based validation rules")
    print("✓ Dependency management and ordering")
    print("✓ Error handling and timeout scenarios")
    print("✓ Task cancellation and cleanup")

if __name__ == "__main__":
    main()
