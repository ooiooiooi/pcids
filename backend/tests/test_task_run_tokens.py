import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.routers.tasks import (
    CURRENT_TASK_RUN_TOKEN,
    TASK_ACTIVE_RUN_TOKENS,
    _ensure_task_not_terminated,
)


class TaskRunTokenTests(unittest.TestCase):
    def tearDown(self):
        TASK_ACTIVE_RUN_TOKENS.clear()

    def test_stale_execution_cannot_continue_after_reexecute(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(status=1)
        TASK_ACTIVE_RUN_TOKENS[42] = "new-run"
        token = CURRENT_TASK_RUN_TOKEN.set("old-run")
        try:
            with self.assertRaisesRegex(RuntimeError, "task_stale"):
                _ensure_task_not_terminated(db, 42)
        finally:
            CURRENT_TASK_RUN_TOKEN.reset(token)

    def test_current_execution_can_continue(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(status=1)
        TASK_ACTIVE_RUN_TOKENS[42] = "current-run"
        token = CURRENT_TASK_RUN_TOKEN.set("current-run")
        try:
            _ensure_task_not_terminated(db, 42)
        finally:
            CURRENT_TASK_RUN_TOKEN.reset(token)

    def test_terminating_task_cannot_continue(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(status=4)
        TASK_ACTIVE_RUN_TOKENS[42] = "current-run"
        token = CURRENT_TASK_RUN_TOKEN.set("current-run")
        try:
            with self.assertRaisesRegex(RuntimeError, "task_terminated"):
                _ensure_task_not_terminated(db, 42)
        finally:
            CURRENT_TASK_RUN_TOKEN.reset(token)


if __name__ == "__main__":
    unittest.main()
