import unittest
from unittest.mock import MagicMock, patch

from src.taskflow.flow_control.flow_control_policy import (
    FlowControlPolicy,
    CompositeFlowControlPolicy,
    ConcurrencyLimitPolicy,
    RateLimitPolicy,
    RetryPolicy,
    ExponentialBackoffRetryPolicy,
)


class TestFlowControlPolicy(unittest.TestCase):
    def test_flow_control_policy_acquire(self):
        class TestPolicy(FlowControlPolicy):
            def should_execute(self) -> bool:
                return True

        policy = TestPolicy()
        with policy.acquire() as allowed:
            self.assertTrue(allowed)

    def test_composite_flow_control_policy_and(self):
        policy1 = MagicMock(spec=FlowControlPolicy)
        policy1.should_execute.return_value = True
        policy2 = MagicMock(spec=FlowControlPolicy)
        policy2.should_execute.return_value = True

        composite_policy = CompositeFlowControlPolicy([policy1, policy2], "and")
        with composite_policy.acquire() as allowed:
            self.assertTrue(allowed)

    def test_composite_flow_control_policy_or(self):
        policy1 = MagicMock(spec=FlowControlPolicy)
        policy1.should_execute.return_value = False
        policy2 = MagicMock(spec=FlowControlPolicy)
        policy2.should_execute.return_value = True

        composite_policy = CompositeFlowControlPolicy([policy1, policy2], "or")
        with composite_policy.acquire() as allowed:
            self.assertTrue(allowed)

    def test_concurrency_limit_policy(self):
        policy = ConcurrencyLimitPolicy(max_concurrent_tasks=1)
        with policy.acquire() as allowed:
            self.assertTrue(allowed)
        with policy.acquire() as allowed:
            self.assertFalse(allowed)

    def test_rate_limit_policy(self):
        policy = RateLimitPolicy(max_tasks=1, time_window=1)
        with policy.acquire() as allowed:
            self.assertTrue(allowed)
        with policy.acquire() as allowed:
            self.assertFalse(allowed)

    def test_retry_policy(self):
        policy = RetryPolicy(max_attempts=3)
        with policy.acquire() as allowed:
            self.assertTrue(allowed)
        policy._current_attempt = 3
        with policy.acquire() as allowed:
            self.assertFalse(allowed)

    @patch("time.sleep", return_value=None)
    def test_exponential_backoff_retry_policy(self, _):
        policy = ExponentialBackoffRetryPolicy(max_attempts=3, base_delay=0.1)
        with policy.acquire() as allowed:
            self.assertTrue(allowed)
        policy._current_attempt = 3
        with policy.acquire() as allowed:
            self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()