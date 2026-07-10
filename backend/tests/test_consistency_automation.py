import unittest

from backend.utils.task_execution import (
    ExecutionMonitor,
    evaluate_version_consistency,
    is_consistency_execution_allowed,
)


class ConsistencyAutomationTests(unittest.TestCase):
    def test_matching_checksums_pass(self):
        self.assertEqual(evaluate_version_consistency("ABC123", "abc123"), 1)

    def test_different_checksums_fail(self):
        self.assertEqual(evaluate_version_consistency("baseline", "current"), 0)

    def test_missing_baseline_is_not_compared(self):
        self.assertIsNone(evaluate_version_consistency("", "current"))
        self.assertIsNone(evaluate_version_consistency("baseline", None))

    def test_mismatch_blocks_execution_until_override(self):
        self.assertFalse(is_consistency_execution_allowed(0, 0))
        self.assertTrue(is_consistency_execution_allowed(0, 1))
        self.assertTrue(is_consistency_execution_allowed(1, 0))
        self.assertTrue(is_consistency_execution_allowed(None, 0))

    def test_execution_monitor_renders_real_timestamp(self):
        monitor = ExecutionMonitor(task_id=1)
        monitor.record("consistency", "success", "版本一致性校验通过")

        rendered = monitor.render()

        self.assertRegex(rendered, r"^\[\d{4}-\d{2}-\d{2}T.*Z\] \[success\] consistency:")


if __name__ == "__main__":
    unittest.main()
