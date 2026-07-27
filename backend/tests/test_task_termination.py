import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Burner, OperationLog, Record, User
from backend.models.task import BurningTask, TaskStatus
from backend.routers.auth import get_current_user
from backend.routers.tasks import (
    _ensure_task_not_terminated,
    _finalize_task_after_unhandled_exception,
    _get_burner_runtime_issue,
    _windows_task_cleanup_script,
    recover_interrupted_tasks,
    router as tasks_router,
)
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
        self.permissions: set[str] = {"burning:add", "burning:delete", "burning:terminate"}

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
        self.assertEqual(response.json()["message"], "任务已终止，残留进程已清理")
        self.assertEqual(response.json()["data"]["status"], int(TaskStatus.TERMINATED))
        self.assertIn("runtime_cleanup", response.json()["data"])

    def test_terminate_task_rechecks_cleanup_when_already_terminated(self):
        task = self._create_task(TaskStatus.TERMINATED)

        response = self.client.post(f"/tasks/{task.id}/terminate", json={"reason": "再次清理"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "任务已终止，残留进程已清理")
        self.assertEqual(response.json()["data"]["status"], int(TaskStatus.TERMINATED))
        self.assertIn("runtime_cleanup", response.json()["data"])

    def test_termination_check_bypasses_stale_sqlalchemy_identity_map(self):
        task = self._create_task(TaskStatus.RUNNING)
        worker_session = self.SessionLocal()
        cached = worker_session.query(BurningTask).filter(BurningTask.id == task.id).first()
        self.assertEqual(cached.status, int(TaskStatus.RUNNING))

        controller_session = self.SessionLocal()
        controller_session.query(BurningTask).filter(BurningTask.id == task.id).update(
            {"status": int(TaskStatus.TERMINATING)},
            synchronize_session=False,
        )
        controller_session.commit()
        controller_session.close()

        with self.assertRaisesRegex(RuntimeError, "task_terminated"):
            _ensure_task_not_terminated(worker_session, task.id)
        worker_session.close()

    def test_windows_cleanup_is_generic_task_scoped_and_descendant_aware(self):
        script = _windows_task_cleanup_script()

        self.assertIn("pcids_task_", script)
        self.assertIn("ParentProcessId", script)
        self.assertIn("$targetIds -contains $parentId", script)
        self.assertIn("Sort-Object { $depthById[$_] } -Descending", script)
        self.assertNotIn("$toolNames", script)
        self.assertNotIn("$forceHwServer", script)

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

    def test_terminating_task_keeps_burner_occupied(self):
        burner = Burner(name="test-burner", type="ST-LINK", is_enabled=1, status=0)
        self.db.add(burner)
        self.db.commit()
        task = self._create_task(TaskStatus.TERMINATING)
        task.burner_id = burner.id
        self.db.commit()

        issue = _get_burner_runtime_issue(self.db, burner, current_task_id=99999)

        self.assertIsNotNone(issue)
        self.assertIn("正在被其他任务占用", issue or "")

    def test_active_task_cannot_be_deleted(self):
        for status in (TaskStatus.RUNNING, TaskStatus.TERMINATING):
            task = self._create_task(status)
            response = self.client.delete(f"/tasks/{task.id}")
            self.assertEqual(response.status_code, 400)
            self.assertIn("不能删除", response.json()["detail"])

    def test_task_status_cannot_be_changed_through_generic_update(self):
        task = self._create_task(TaskStatus.PENDING)

        response = self.client.put(f"/tasks/{task.id}", json={"status": int(TaskStatus.SUCCESS)})

        self.assertEqual(response.status_code, 400)
        self.assertIn("不能手动修改", response.json()["detail"])
        refreshed = self.SessionLocal().query(BurningTask).filter(BurningTask.id == task.id).first()
        self.assertEqual(refreshed.status, int(TaskStatus.PENDING))

    def test_unhandled_exception_is_finalized_as_failed(self):
        task = self._create_task(TaskStatus.RUNNING)
        task.result = "正在执行"

        _finalize_task_after_unhandled_exception(task, RuntimeError("unexpected failure"))

        self.assertEqual(task.status, int(TaskStatus.FAILED))
        self.assertIsNotNone(task.finished_at)
        self.assertIn("unexpected failure", task.last_error or "")
        self.assertIn("未处理异常", task.result or "")


if __name__ == "__main__":
    unittest.main()
