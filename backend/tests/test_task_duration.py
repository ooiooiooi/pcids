import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from backend.routers.tasks import _task_display_time, _task_duration_seconds


class TaskDurationTests(unittest.TestCase):
    def test_uses_real_started_and_finished_time(self):
        started_at = datetime(2026, 6, 15, 8, 0, 0)
        task = SimpleNamespace(started_at=started_at, finished_at=started_at + timedelta(seconds=14))
        self.assertEqual(_task_duration_seconds(task), 14)

    def test_missing_or_negative_duration_is_zero(self):
        self.assertEqual(_task_duration_seconds(SimpleNamespace(started_at=None, finished_at=None)), 0)
        started_at = datetime(2026, 6, 15, 8, 0, 10)
        self.assertEqual(
            _task_duration_seconds(SimpleNamespace(started_at=started_at, finished_at=started_at - timedelta(seconds=5))),
            0,
        )

    def test_display_time_converts_naive_database_time_to_local_time(self):
        self.assertEqual(
            _task_display_time(datetime(2026, 6, 29, 8, 23, 9)),
            datetime(2026, 6, 29, 16, 23, 9),
        )


if __name__ == "__main__":
    unittest.main()
