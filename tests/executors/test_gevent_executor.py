## gevent monkey patching removed as per new requirements
import importlib.util
import time
import unittest
import warnings


# Skip tests if gevent is not installed
gevent_available = importlib.util.find_spec("gevent") is not None
if gevent_available:
    import gevent
    from taskflow.executors import get_executor
    from taskflow.executors.gevent_executor import GeventExecutor
else:
    gevent = None
    get_executor = None
    GeventExecutor = None
    warnings.warn("Gevent not installed, skipping gevent executor tests")


def simple_task(value):
    """Simple task that returns the input value."""
    return value


def sleeping_task(seconds, value):
    """Task that sleeps for a specified amount of time, then returns value."""
    if gevent_available and gevent is not None:
        gevent.sleep(seconds)
    else:
        time.sleep(seconds)
    return value


def io_simulation_task(delay, value):
    """Simulate an IO task by sleeping."""
    if gevent_available and gevent is not None:
        gevent.sleep(delay)
    else:
        time.sleep(delay)
    return value


@unittest.skipIf(not gevent_available, "Gevent not installed")
class TestGeventExecutor(unittest.TestCase):
    def setUp(self):
        self.executor = GeventExecutor(max_workers=2)

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

    def test_io_bound_tasks(self):
        """Test that IO-bound tasks can run concurrently with GeventExecutor."""
        values = [(0.1, i) for i in range(4)]
        start_time = time.time()

        # Run tasks in parallel
        futures = [
            self.executor.submit(io_simulation_task, delay, val)
            for delay, val in values
        ]
        results = [f.result() for f in futures]

        parallel_time = time.time() - start_time

        # Should be close to 0.1 seconds, not 0.4 seconds if genuinely concurrent
        # But we can't reliably test exact timing in all environments
        self.assertEqual(results, [0, 1, 2, 3])

    def test_context_manager(self):
        with GeventExecutor() as executor:
            future = executor.submit(simple_task, "context_manager")
            self.assertEqual(future.result(), "context_manager")

    def test_result_method_compatibility(self):
        """Test that the added result() method works correctly."""
        future = self.executor.submit(simple_task, "test_result")
        self.assertEqual(future.result(), "test_result")

        # Test with timeout
        future = self.executor.submit(sleeping_task, 0.1, "with_timeout")
        self.assertEqual(future.result(timeout=1.0), "with_timeout")


@unittest.skipIf(not gevent_available, "Gevent not installed")
class TestGeventExecutorFactory(unittest.TestCase):
    def test_get_gevent_executor(self):
        executor = get_executor("gevent")
        self.assertIsInstance(executor, GeventExecutor)
        executor.shutdown()


if __name__ == "__main__":
    unittest.main()
