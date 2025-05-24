import concurrent.futures
import time
import unittest

from taskflow.executors import get_executor
from taskflow.executors.process_executor import ProcessExecutor
from taskflow.executors.thread_executor import ThreadExecutor


def simple_task(value):
    """Simple task that returns the input value."""
    return value


def sleeping_task(seconds, value):
    """Task that sleeps for a specified amount of time, then returns value."""
    time.sleep(seconds)
    return value


def cpu_bound_task(n):
    """A CPU-bound task that calculates the sum of squares."""
    return sum(i * i for i in range(n))


class TestThreadExecutor(unittest.TestCase):
    def setUp(self):
        self.executor = ThreadExecutor(max_workers=2)

    def tearDown(self):
        self.executor.shutdown(wait=True)

    def test_submit_and_result(self):
        future = self.executor.submit(simple_task, 42)
        self.assertEqual(future.result(), 42)

    def test_submit_with_args_kwargs(self):
        future = self.executor.submit(sleeping_task, seconds=0.1, value="test")
        self.assertEqual(future.result(), "test")

    def test_map(self):
        values = list(range(5))
        results = list(self.executor.map(simple_task, values))
        self.assertEqual(results, values)

    def test_multiple_futures(self):
        futures = [self.executor.submit(simple_task, i) for i in range(5)]
        results = [f.result() for f in futures]
        self.assertEqual(results, list(range(5)))

    def test_context_manager(self):
        with ThreadExecutor() as executor:
            future = executor.submit(simple_task, "context_manager")
            self.assertEqual(future.result(), "context_manager")


class TestProcessExecutor(unittest.TestCase):
    def setUp(self):
        self.executor = ProcessExecutor(max_workers=2)

    def tearDown(self):
        self.executor.shutdown(wait=True)

    def test_submit_and_result(self):
        future = self.executor.submit(simple_task, 42)
        self.assertEqual(future.result(), 42)

    def test_submit_with_args_kwargs(self):
        future = self.executor.submit(sleeping_task, seconds=0.1, value="test")
        self.assertEqual(future.result(), "test")

    def test_map(self):
        values = list(range(5))
        results = list(self.executor.map(simple_task, values))
        self.assertEqual(results, values)

    def test_multiple_futures(self):
        futures = [self.executor.submit(simple_task, i) for i in range(5)]
        results = [f.result() for f in futures]
        self.assertEqual(results, list(range(5)))

    def test_cpu_bound_tasks(self):
        """Test that CPU-bound tasks can run in parallel with ProcessExecutor."""
        values = [1000000 for _ in range(4)]
        start_time = time.time()

        # Run tasks in parallel
        futures = [self.executor.submit(cpu_bound_task, n) for n in values]
        results = [f.result() for f in futures]

        parallel_time = time.time() - start_time

        # Run tasks sequentially for comparison
        start_time = time.time()
        sequential_results = [cpu_bound_task(n) for n in values]
        sequential_time = time.time() - start_time

        # Verify results match
        self.assertEqual(results, sequential_results)

        # On a multi-core machine, parallel should be faster, but we can't guarantee it in all CI environments
        # So we just make sure the results are correct

    def test_context_manager(self):
        with ProcessExecutor() as executor:
            future = executor.submit(simple_task, "context_manager")
            self.assertEqual(future.result(), "context_manager")


class TestExecutorFactory(unittest.TestCase):
    def test_get_thread_executor(self):
        executor = get_executor("thread")
        self.assertIsInstance(executor, ThreadExecutor)
        executor.shutdown()

    def test_get_process_executor(self):
        executor = get_executor("process")
        self.assertIsInstance(executor, ProcessExecutor)
        executor.shutdown()

    def test_invalid_executor_type(self):
        with self.assertRaises(ValueError):
            get_executor("invalid_type")


if __name__ == "__main__":
    unittest.main()
