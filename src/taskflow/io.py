from .executors import get_executor

# Example usage for users:
# executor = get_executor('thread')
# future = executor.submit(some_io_task, arg1, arg2)
# result = future.result()

default_executor = get_executor('thread')
