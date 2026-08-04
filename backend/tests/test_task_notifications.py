import json
import unittest
from datetime import datetime

from backend.models.message import Message
from backend.models.repository import Repository
from backend.models.task import BurningTask
from backend.routers.messages import _enrich_task_message_payload, _parse_message_content
from backend.routers.tasks import _build_task_notice_payload
from backend.utils.datetime_utils import database_time_to_local


class TaskNotificationTests(unittest.TestCase):
    def test_build_task_notice_payload_contains_structured_fields(self):
        task = BurningTask(
            id=12,
            task_no="202606160001",
            created_by_user_id=1,
            repository_id=3,
            software_name="DemoSoft",
            serial_number="SN-001",
            board_name="STM32F407VGT6开发板",
            status=2,
            started_at=datetime(2026, 6, 16, 8, 0, 0),
            finished_at=datetime(2026, 6, 16, 8, 0, 14),
        )
        repo = Repository(
            id=3,
            project_key="proj_demo",
            name="demo.bin",
            version="v1.2.3",
            repo_detail_json=json.dumps({"project_name": "示例项目"}, ensure_ascii=False),
        )

        payload = _build_task_notice_payload(task, repo, "示例项目", "burn", "成功", "烧录完成，校验通过")

        self.assertEqual(payload["target"], "SN-001")
        self.assertEqual(payload["software_name"], "DemoSoft")
        self.assertEqual(payload["software_version"], "v1.2.3")
        self.assertEqual(payload["event_time"], "2026-06-16T08:00:14")
        self.assertIn("软件名称：DemoSoft", payload["meta_text"])
        self.assertEqual(payload["task_no"], "202606160001")
        self.assertEqual(payload["project_name"], "示例项目")
        self.assertEqual(payload["execution_result"], "成功")
        self.assertEqual(payload["detail_text"], "总耗时 14 秒")

    def test_parse_message_content_returns_structured_payload(self):
        payload = {
            "category": "烧录安装",
            "status": "error",
            "status_label": "失败",
            "primary_text": "安装任务失败：192.168.0.10（鸿蒙）",
            "meta_text": "任务编号：202606160002 | 项目名称：示例项目 | 软件版本：v2.0.0",
            "detail_text": "目标：192.168.0.10（鸿蒙）\n详细内容：超时",
            "target": "192.168.0.10（鸿蒙）",
            "software_version": "v2.0.0",
            "task_no": "202606160002",
            "project_name": "示例项目",
            "execution_result": "失败",
            "detail_content": "超时",
        }
        message = Message(
            id=1,
            user_id=1,
            title="安装任务通知",
            content=json.dumps(payload, ensure_ascii=False),
            is_read=False,
        )

        parsed = _parse_message_content(message.content)

        self.assertEqual(parsed["target"], payload["target"])
        self.assertEqual(parsed["software_version"], payload["software_version"])
        self.assertEqual(parsed["task_no"], payload["task_no"])
        self.assertEqual(parsed["project_name"], payload["project_name"])
        self.assertEqual(parsed["execution_result"], payload["execution_result"])
        self.assertEqual(parsed["detail_content"], payload["detail_content"])

    def test_enrich_legacy_task_message_uses_task_name_and_finished_time(self):
        task = BurningTask(
            id=13,
            task_no="202606160003",
            repository_id=4,
            software_name="legacy.bin",
            status=3,
            finished_at=datetime(2026, 6, 16, 9, 30, 5),
        )
        repo = Repository(id=4, name="repo.bin", version="v3.0.0")

        class QueryStub:
            def __init__(self, value):
                self.value = value

            def filter(self, *_args):
                return self

            def first(self):
                return self.value

        class DbStub:
            def query(self, model):
                return QueryStub(task if model is BurningTask else repo)

        payload = _enrich_task_message_payload(DbStub(), {
            "task_no": "202606160003",
            "project_name": "示例项目",
            "software_version": "v3.0.0",
        })

        self.assertEqual(payload["software_name"], "legacy.bin")
        expected_time = database_time_to_local(datetime(2026, 6, 16, 9, 30, 5))
        self.assertEqual(payload["event_time"], expected_time.isoformat(timespec="seconds"))
        self.assertIn("软件名称：legacy.bin", payload["meta_text"])

    def test_enrich_legacy_task_message_overrides_wrong_utc_suffix_event_time(self):
        task = BurningTask(
            id=14,
            task_no="202606230210",
            repository_id=5,
            software_name="legacy.bin",
            status=2,
            finished_at=datetime(2026, 6, 23, 23, 14, 0),
        )
        repo = Repository(id=5, name="repo.bin", version="v4.0.0")

        class QueryStub:
            def __init__(self, value):
                self.value = value

            def filter(self, *_args):
                return self

            def first(self):
                return self.value

        class DbStub:
            def query(self, model):
                return QueryStub(task if model is BurningTask else repo)

        payload = _enrich_task_message_payload(DbStub(), {
            "task_no": "202606230210",
            "event_time": "2026-06-23T23:14:00Z",
        })

        expected_time = database_time_to_local(datetime(2026, 6, 23, 23, 14, 0))
        self.assertEqual(payload["event_time"], expected_time.isoformat(timespec="seconds"))


if __name__ == "__main__":
    unittest.main()
