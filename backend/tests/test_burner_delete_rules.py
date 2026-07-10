import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

from backend.routers.burners import delete_burner


class BurnerDeleteRuleTests(unittest.TestCase):
    def test_delete_burner_blocks_when_running_tasks_exist(self):
        db = MagicMock()

        burner_query = MagicMock()
        burner_query.filter.return_value.first.return_value = SimpleNamespace(id=3, name="ST-LINK #1")

        running_task_query = MagicMock()
        running_task_query.filter.return_value.count.return_value = 1

        db.query.side_effect = [burner_query, running_task_query]

        with self.assertRaises(HTTPException) as context:
            asyncio.run(delete_burner(3, db=db, _current_user=SimpleNamespace(), _=None))

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("执行中的任务", context.exception.detail)

    def test_delete_burner_blocks_when_history_tasks_reference_it(self):
        db = MagicMock()

        burner_query = MagicMock()
        burner_query.filter.return_value.first.return_value = SimpleNamespace(id=5, name="J-LINK #2")

        running_task_query = MagicMock()
        running_task_query.filter.return_value.count.return_value = 0

        referenced_task_query = MagicMock()
        referenced_task_query.filter.return_value.count.return_value = 2

        db.query.side_effect = [burner_query, running_task_query, referenced_task_query]

        with self.assertRaises(HTTPException) as context:
            asyncio.run(delete_burner(5, db=db, _current_user=SimpleNamespace(), _=None))

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("任务引用", context.exception.detail)

    def test_delete_burner_succeeds_when_not_referenced(self):
        db = MagicMock()

        burner = SimpleNamespace(id=8, name="PWLINK2 #1")
        burner_query = MagicMock()
        burner_query.filter.return_value.first.return_value = burner

        running_task_query = MagicMock()
        running_task_query.filter.return_value.count.return_value = 0

        referenced_task_query = MagicMock()
        referenced_task_query.filter.return_value.count.return_value = 0

        db.query.side_effect = [burner_query, running_task_query, referenced_task_query]

        result = asyncio.run(delete_burner(8, db=db, _current_user=SimpleNamespace(), _=None))

        db.delete.assert_called_once_with(burner)
        db.commit.assert_called_once()
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["message"], "删除成功")


if __name__ == "__main__":
    unittest.main()
