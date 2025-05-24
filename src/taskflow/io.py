from .executors import get_executor
from .executors.validating_executor import ValidatingTaskExecutor, ComprehensiveTaskExecutor
from .task import CallableTask
from .context import TaskContext

# Example usage for users:
# executor = get_executor('thread')
# future = executor.submit(some_io_task, arg1, arg2)
# result = future.result()

# Enhanced usage with validation:
# validating_executor = ValidatingTaskExecutor(get_executor('thread'))
# task = validating_executor.execute_callable(some_io_task, arg1, arg2, 
#                                           context=TaskContext.with_timeout(30.0))

default_executor = get_executor("thread")
default_validating_executor = ValidatingTaskExecutor(get_executor("thread"))
default_comprehensive_executor = ComprehensiveTaskExecutor(get_executor("thread"))
