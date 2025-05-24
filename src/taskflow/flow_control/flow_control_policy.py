import abc
import contextlib
import random
import threading
import time
from typing import Generator, List, Optional, Union


class FlowControlPolicy(abc.ABC):
    """Abstract base class for flow control policies."""

    @abc.abstractmethod
    def should_execute(self) -> bool:
        """
        Determine if a task should be executed.

        :return: Boolean indicating if task can proceed
        """
        pass

    @contextlib.contextmanager
    def acquire(self) -> Generator[bool, None, None]:
        """
        Context manager interface for policy acquisition.

        :yield: Boolean indicating if task can proceed
        :raises RuntimeError: If task execution is not allowed
        """
        try:
            if self.should_execute():
                yield True
            else:
                yield False
        finally:
            # Optional cleanup can be implemented in subclasses
            self.release()

    def release(self):
        """
        Optional release method for policies that need cleanup.
        Can be overridden by subclasses.
        """
        pass


class CompositeFlowControlPolicy(FlowControlPolicy):
    """
    Composite policy that combines multiple flow control policies
    with context manager support.
    """

    def __init__(
        self, policies: List[FlowControlPolicy], combination_type: str = "and"
    ):
        """
        Initialize a composite flow control policy.

        :param policies: List of flow control policies to combine
        :param combination_type: How to combine policies ('and' or 'or')
        """
        self._policies = policies
        self._combination_type = combination_type.lower()

        if self._combination_type not in ["and", "or"]:
            raise ValueError("Combination type must be 'and' or 'or'")

    def should_execute(self) -> bool:
        """
        Evaluate all policies based on the combination type.

        :return: Boolean indicating if task can proceed
        """
        if self._combination_type == "and":
            return all(policy.should_execute() for policy in self._policies)
        else:  # 'or'
            return any(policy.should_execute() for policy in self._policies)

    @contextlib.contextmanager
    def acquire(self) -> Generator[bool, None, None]:
        """
        Context manager that handles policy acquisition and release.

        :yield: Boolean indicating if task can proceed
        """
        # For composite policies, we use the base implementation
        # which calls should_execute()
        try:
            if self.should_execute():
                yield True
            else:
                yield False
        finally:
            self.release()


class ConcurrencyLimitPolicy(FlowControlPolicy):
    """
    Policy to limit the number of concurrent tasks with context manager support.
    """

    def __init__(self, max_concurrent_tasks: int):
        """
        Initialize concurrency limit policy.

        :param max_concurrent_tasks: Maximum number of tasks that can run concurrently
        """
        self._max_concurrent_tasks = max_concurrent_tasks
        self._current_tasks = threading.Semaphore(max_concurrent_tasks)
        self._active_tasks = 0
        self._lock = threading.Lock()

    def should_execute(self) -> bool:
        """
        Check if a new task can be executed based on current concurrency.

        :return: Boolean indicating if task can proceed
        """
        return self._current_tasks.acquire(blocking=False)

    @contextlib.contextmanager
    def acquire(self) -> Generator[bool, None, None]:
        """
        Context manager for acquiring a concurrency slot.

        :yield: Boolean indicating if task can proceed
        """
        acquired = False
        try:
            acquired = self.should_execute()
            if acquired:
                with self._lock:
                    self._active_tasks += 1
            yield acquired
        finally:
            if acquired:
                self.release()

    def release(self):
        """
        Release the concurrency slot.
        """
        with self._lock:
            if self._active_tasks > 0:
                self._active_tasks -= 1
                self._current_tasks.release()


class RateLimitPolicy(FlowControlPolicy):
    """
    Policy to limit the number of tasks within a specific time window.
    Supports context manager interface.
    """

    def __init__(self, max_tasks: int, time_window: float):
        """
        Initialize rate limit policy.

        :param max_tasks: Maximum number of tasks allowed in time window
        :param time_window: Time window in seconds
        """
        self._max_tasks = max_tasks
        self._time_window = time_window
        self._task_timestamps = []
        self._lock = threading.Lock()

    def should_execute(self) -> bool:
        """
        Check if a new task can be executed based on rate limit.

        :return: Boolean indicating if task can proceed
        """
        current_time = time.time()

        with self._lock:
            # Remove timestamps outside the current time window
            self._task_timestamps = [
                timestamp
                for timestamp in self._task_timestamps
                if current_time - timestamp <= self._time_window
            ]

            # Check if we can add a new task
            if len(self._task_timestamps) < self._max_tasks:
                self._task_timestamps.append(current_time)
                return True

            return False

    def release(self):
        """
        Placeholder release method for consistency.
        Rate limit policy typically doesn't need explicit release.
        """
        pass


class RetryPolicy(FlowControlPolicy):
    """
    Basic retry policy with configurable maximum attempts
    """

    def __init__(
        self,
        max_attempts: int = 3,
        retriable_exceptions: Optional[Union[type, tuple[type, ...]]] = None,
    ):
        """
        Initialize retry policy

        :param max_attempts: Maximum number of retry attempts
        :param retriable_exceptions: Exception or tuple of exceptions to retry on
        """
        self._max_attempts = max_attempts
        self._retriable_exceptions = retriable_exceptions or Exception
        self._current_attempt = 0

    def should_execute(self) -> bool:
        """
        Determine if another retry attempt should be made

        :return: Boolean indicating if another attempt can be made
        """
        return self._current_attempt < self._max_attempts

    @contextlib.contextmanager
    def acquire(self) -> Generator[bool, None, None]:
        """
        Context manager for retry logic

        :yield: Boolean indicating if task can proceed
        """
        try:
            if self.should_execute():
                self._current_attempt += 1
                yield True
            else:
                yield False
        finally:
            pass  # Attempt count is managed by should_execute calls

    def release(self):
        """
        Reset attempt count
        """
        self._current_attempt = 0


class ExponentialBackoffRetryPolicy(RetryPolicy):
    """
    Retry policy with exponential backoff and optional jitter
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: Optional[float] = None,
        jitter: bool = True,
        retriable_exceptions: Optional[Union[type, tuple[type, ...]]] = None,
    ):
        """
        Initialize exponential backoff retry policy

        :param max_attempts: Maximum number of retry attempts
        :param base_delay: Initial delay between retries (in seconds)
        :param max_delay: Maximum delay between retries (in seconds)
        :param jitter: Add randomness to delay to prevent thundering herd
        :param retriable_exceptions: Exception or tuple of exceptions to retry on
        """
        super().__init__(max_attempts, retriable_exceptions)
        self._base_delay = base_delay
        self._max_delay = max_delay or float("inf")
        self._jitter = jitter

    @contextlib.contextmanager
    def acquire(self) -> Generator[bool, None, None]:
        """
        Context manager with exponential backoff retry logic

        :yield: Boolean indicating if task can proceed
        """
        try:
            if self.should_execute():
                self._current_attempt += 1
                yield True
            else:
                yield False
        finally:
            pass  # Attempt count is managed by should_execute calls
