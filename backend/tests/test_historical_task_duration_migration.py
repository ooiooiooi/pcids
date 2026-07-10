import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.utils.db import sync_historical_task_durations


class HistoricalTaskDurationMigrationTests(unittest.TestCase):
    def test_replaces_legacy_random_duration(self):
        started_at = datetime(2026, 6, 15, 8, 0, 0)
        task = SimpleNamespace(
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=10),
            result="安装完成，校验通过。总耗时 14 秒。\n日志",
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [task]

        sync_historical_task_durations(db)

        self.assertIn("总耗时 10 秒", task.result)
        db.commit.assert_called_once()

    def test_leaves_logs_without_duration_unchanged(self):
        task = SimpleNamespace(
            started_at=datetime(2026, 6, 15, 8, 0, 0),
            finished_at=datetime(2026, 6, 15, 8, 0, 5),
            result="执行失败",
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [task]

        sync_historical_task_durations(db)

        self.assertEqual(task.result, "执行失败")
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
