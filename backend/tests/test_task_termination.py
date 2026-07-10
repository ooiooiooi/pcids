import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, OperationLog, Record, User
from backend.models.task import BurningTask, TaskStatus
from backend.routers.auth import get_current_user
from backend.routers.tasks import recover_interrupted_tasks, router as tasks_router
from backend.utils.db import get_db


class StubCurrentUser:
    def __init__(self, user_id: int, username: str, permissions: set[str]):
        self.id = user_id
        self.username = username
        self._permissions = permissions

    def get_permissions(self):
        return sorted(self._permissions)


class TaskTerminationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        self.db = self.SessionLocal()
        self.user = User(username="tester", password_hash="x", status=1)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.permissions: set[str] = {"burning:terminate"}

        app = FastAPI()
        app.include_router(tasks_router, prefix="/tasks")

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        def override_get_current_user():
            return StubCurrentUser(self.user.id, self.user.username, self.permissions)

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_get_current_user
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _create_task(self, status: TaskStatus) -> BurningTask:
        task = BurningTask(
            task_no="TASK-001",
            software_name="BOOT.bin",
            created_by_user_id=self.user.id,
            status=int(status),
            progress_percent=35,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def test_terminate_task_requires_permission(self):
        task = self._create_task(TaskStatus.RUNNING)
        self.permissions = set()

        response = self.client.post(f"/tasks/{task.id}/terminate", json={"reason": "用户取消"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("burning:terminate", response.json()["detail"])

    def test_terminate_task_finalizes_and_writes_audit_log(self):
        task = self._create_task(TaskStatus.RUNNING)

        response = self.client.post(f"/tasks/{task.id}/terminate", json={"reason": "人工终止验证"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["status"], int(TaskStatus.TERMINATED))
        self.assertIn("runtime_cleanup", body["data"])

        refreshed = self.SessionLocal().query(BurningTask).filter(BurningTask.id == task.id).first()
        self.assertEqual(refreshed.status, int(TaskStatus.TERMINATED))
        self.assertEqual(refreshed.terminated_by_user_id, self.user.id)
        self.assertEqual(refreshed.termination_reason, "人工终止验证")
        self.assertIsNotNone(refreshed.termination_requested_at)
        self.assertEqual(refreshed.last_error, "任务已终止")
        self.assertIsNotNone(refreshed.finished_at)

        operation_logs = self.SessionLocal().query(OperationLog).all()
        self.assertEqual(len(operation_logs), 1)
        self.assertIn("终止烧录安装任务", operation_logs[0].action or "")
        self.assertIn("人工终止验证", operation_logs[0].content or "")

    def test_terminate_task_is_idempotent_when_already_terminating(self):
        task = self._create_task(TaskStatus.TERMINATING)

        response = self.client.post(f"/tasks/{task.id}/terminate", json={"reason": "重复点击"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "任务正在终止中")

    def test_terminate_task_rejects_non_running_status(self):
        task = self._create_task(TaskStatus.SUCCESS)

        response = self.client.post(f"/tasks/{task.id}/terminate", json={"reason": "不应成功"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("不是执行中状态", response.json()["detail"])

    def test_recover_interrupted_tasks_finalizes_terminating_task(self):
        task = self._create_task(TaskStatus.TERMINATING)
        task.termination_reason = "服务重启前已提交终止"
        task.result = "任务终止请求已提交。终止原因：服务重启前已提交终止"
        self.db.commit()

        with patch("backend.utils.db.SessionLocal", self.SessionLocal):
            recovered = recover_interrupted_tasks()

        self.assertEqual(recovered, 1)
        refreshed = self.SessionLocal().query(BurningTask).filter(BurningTask.id == task.id).first()
        self.assertEqual(refreshed.status, int(TaskStatus.TERMINATED))
        self.assertEqual(refreshed.last_error, "任务已终止")
        self.assertIsNotNone(refreshed.finished_at)

        records = self.SessionLocal().query(Record).all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].result, "终止")
        log_data = json.loads(records[0].log_data or "{}")
        self.assertEqual(log_data.get("execution_result"), "终止")


if __name__ == "__main__":
    unittest.main()
