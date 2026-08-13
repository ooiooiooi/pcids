import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import (
    Base,
    Burner,
    BurningTask,
    Message,
    Repository,
    RepositoryProjectMember,
)
from backend.models.task import TaskStatus
from backend.routers.dashboard import (
    _build_local_day_window,
    _build_month_windows,
    _build_task_preview,
    _calculate_task_growth,
    _query_monthly_success_trend,
    _query_target_counts,
    _query_task_window_metrics,
    _query_task_windows_metrics,
    get_dashboard_stats,
)
from backend.utils.datetime_utils import local_time_to_database


class _DashboardUser(SimpleNamespace):
    def get_permissions(self):
        return list(getattr(self, "permission_codes", ["all"]))


class _FixedDateTime(datetime):
    fixed_now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls.fixed_now.astimezone(tz)
        return cls.fixed_now


class DashboardStatsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.local_timezone = timezone(timedelta(hours=8))
        self.local_now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=self.local_timezone)
        self.database_now = local_time_to_database(self.local_now)

        self.repo_member = Repository(
            name="member-repo",
            project_key="project-member",
            tenant="tenant-a",
        )
        self.repo_outside = Repository(
            name="outside-repo",
            project_key="project-outside",
            tenant="tenant-b",
        )
        self.db.add_all([self.repo_member, self.repo_outside])
        self.db.flush()
        self.db.add(
            RepositoryProjectMember(
                project_key="project-member",
                user_id=1,
                role="member",
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _user(data_scope):
        return _DashboardUser(
            id=1,
            username="dashboard-user",
            display_name="Dashboard User",
            role=SimpleNamespace(data_scope=data_scope),
            permission_codes=["all"],
        )

    def _add_task(
        self,
        task_no,
        *,
        owner_id=1,
        repository_id=None,
        created_at,
        status=TaskStatus.PENDING,
        task_type="board",
        board_name=None,
        serial_number=None,
        target_ip=None,
    ):
        task = BurningTask(
            task_no=task_no,
            software_name=f"software-{task_no}",
            created_by_user_id=owner_id,
            repository_id=repository_id,
            created_at=created_at,
            updated_at=created_at,
            status=int(status),
            task_type=task_type,
            board_name=board_name,
            serial_number=serial_number,
            target_ip=target_ip,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def test_local_day_and_month_boundaries_are_utc_naive(self):
        today = _build_local_day_window(self.local_now)
        self.assertEqual(today.start_time, datetime(2026, 7, 14, 16, 0, 0))
        self.assertEqual(today.end_time, datetime(2026, 7, 15, 16, 0, 0))
        self.assertIsNone(today.start_time.tzinfo)
        self.assertIsNone(today.end_time.tzinfo)

        windows = _build_month_windows(self.local_now, 6)
        self.assertEqual(windows[0].label, "二月")
        self.assertEqual(windows[0].start_time, datetime(2026, 1, 31, 16, 0, 0))
        self.assertEqual(windows[-1].label, "七月")
        self.assertEqual(windows[-1].end_time, datetime(2026, 7, 31, 16, 0, 0))

    def test_task_growth_is_unavailable_when_baseline_is_zero(self):
        self.assertEqual(_calculate_task_growth(3, 0), (None, False))
        self.assertEqual(_calculate_task_growth(0, 0), (None, False))
        self.assertEqual(_calculate_task_growth(3, 2), (50.0, True))

    def test_window_metrics_use_canonical_self_and_project_scope(self):
        today = _build_local_day_window(self.local_now)
        own_success = today.start_time + timedelta(hours=1)
        self._add_task("OWN-S", created_at=own_success, status=TaskStatus.SUCCESS)
        self._add_task(
            "OWN-F",
            created_at=own_success + timedelta(minutes=1),
            status=TaskStatus.FAILED,
        )
        self._add_task(
            "OWN-P",
            created_at=own_success + timedelta(minutes=2),
            status=TaskStatus.PENDING,
        )
        self._add_task(
            "MEMBER-S",
            owner_id=2,
            repository_id=self.repo_member.id,
            created_at=own_success + timedelta(minutes=3),
            status=TaskStatus.SUCCESS,
        )
        self._add_task(
            "OUTSIDE-S",
            owner_id=2,
            repository_id=self.repo_outside.id,
            created_at=own_success + timedelta(minutes=4),
            status=TaskStatus.SUCCESS,
        )
        self.db.commit()

        self_metrics = _query_task_window_metrics(self.db, self._user("self"), today)
        self.assertEqual(self_metrics, {"total": 3, "completed": 2, "success": 1})

        project_metrics = _query_task_window_metrics(self.db, self._user("project"), today)
        self.assertEqual(project_metrics, {"total": 4, "completed": 3, "success": 2})

        denied_metrics = _query_task_window_metrics(self.db, self._user("unknown"), today)
        self.assertEqual(denied_metrics, {"total": 0, "completed": 0, "success": 0})

    def test_two_day_metrics_are_loaded_with_one_select(self):
        yesterday = _build_local_day_window(self.local_now, day_offset=-1)
        today = _build_local_day_window(self.local_now)
        self._add_task(
            "YESTERDAY",
            created_at=yesterday.start_time + timedelta(hours=1),
            status=TaskStatus.SUCCESS,
        )
        self._add_task(
            "TODAY",
            created_at=today.start_time + timedelta(hours=1),
            status=TaskStatus.FAILED,
        )
        self.db.commit()
        statements = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            metrics = _query_task_windows_metrics(
                self.db,
                self._user("self"),
                [yesterday, today],
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(len(statements), 1)
        self.assertEqual(metrics[0], {"total": 1, "completed": 1, "success": 1})
        self.assertEqual(metrics[1], {"total": 1, "completed": 1, "success": 0})

    def test_monthly_trend_is_one_query_and_excludes_pending_and_future_tasks(self):
        windows = _build_month_windows(self.local_now, 6)
        before_now = self.database_now - timedelta(hours=1)
        self._add_task("SUCCESS", created_at=before_now, status=TaskStatus.SUCCESS)
        self._add_task(
            "FAILED",
            created_at=before_now + timedelta(minutes=1),
            status=TaskStatus.FAILED,
            task_type="os",
        )
        self._add_task(
            "PENDING",
            created_at=before_now + timedelta(minutes=2),
            status=TaskStatus.PENDING,
        )
        self._add_task(
            "FUTURE",
            created_at=self.database_now + timedelta(hours=1),
            status=TaskStatus.SUCCESS,
        )
        self._add_task(
            "OUTSIDE-SUCCESS",
            owner_id=2,
            repository_id=self.repo_outside.id,
            created_at=before_now + timedelta(minutes=3),
            status=TaskStatus.SUCCESS,
        )
        self.db.commit()

        statements = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            trend = _query_monthly_success_trend(
                self.db,
                self._user("self"),
                windows,
                self.database_now,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(len(statements), 1)
        self.assertIn("WHERE", statements[0].upper())
        self.assertFalse(trend[0]["rateAvailable"])
        self.assertIsNone(trend[0]["rate"])
        self.assertEqual(
            trend[-1],
            {
                "month": "七月",
                "rate": 50.0,
                "rateAvailable": True,
                "completedCount": 2,
                "successCount": 1,
                "burnCount": 2,
                "installCount": 1,
            },
        )

    def test_target_top_five_uses_board_serial_ip_fallback_and_natural_window(self):
        windows = _build_month_windows(self.local_now, 6)
        start = windows[0].start_time
        self._add_task("BOARD-1", created_at=start, board_name=" Target-A ")
        self._add_task(
            "BOARD-2",
            created_at=start + timedelta(seconds=1),
            board_name="Target-A",
        )
        self._add_task(
            "SERIAL",
            created_at=start + timedelta(seconds=2),
            serial_number=" SN-1 ",
        )
        self._add_task(
            "IP",
            created_at=start + timedelta(seconds=3),
            target_ip=" 10.0.0.8 ",
            task_type="os",
        )
        self._add_task(
            "EMPTY",
            created_at=start + timedelta(seconds=4),
            board_name=" ",
            serial_number=" ",
            target_ip=" ",
        )
        self._add_task(
            "BEFORE",
            created_at=start - timedelta(microseconds=1),
            board_name="Before",
        )
        self._add_task(
            "FUTURE",
            created_at=self.database_now + timedelta(seconds=1),
            target_ip="10.0.0.9",
            task_type="os",
        )
        self._add_task(
            "MEMBER",
            owner_id=2,
            repository_id=self.repo_member.id,
            created_at=start + timedelta(seconds=5),
            target_ip="10.0.0.10",
            task_type="os",
        )
        self._add_task(
            "OUTSIDE",
            owner_id=2,
            repository_id=self.repo_outside.id,
            created_at=start + timedelta(seconds=6),
            board_name="Secret-Target",
        )
        self.db.commit()

        target_data = _query_target_counts(
            self.db,
            self._user("project"),
            windows,
            self.database_now,
        )
        values = {item["name"]: item["value"] for item in target_data}
        self.assertEqual(
            values,
            {
                "Target-A": 2,
                "SN-1": 1,
                "10.0.0.8": 1,
                "10.0.0.10": 1,
            },
        )
        self.assertNotIn("Secret-Target", values)

    def test_task_preview_maps_all_states_and_task_types(self):
        cases = [
            (TaskStatus.PENDING, "board", "待执行", "info"),
            (TaskStatus.RUNNING, "board", "执行中", "info"),
            (TaskStatus.SUCCESS, "board", "烧录成功", "success"),
            (TaskStatus.FAILED, "os", "安装失败", "error"),
            (TaskStatus.SUCCESS, "hybrid", "混合部署成功", "success"),
            (TaskStatus.FAILED, "unknown", "任务失败", "error"),
            (TaskStatus.TERMINATING, "board", "终止中", "warning"),
            (TaskStatus.TERMINATED, "board", "已终止", "warning"),
        ]
        for status, task_type, expected_label, expected_status in cases:
            with self.subTest(status=status, task_type=task_type):
                task = BurningTask(
                    id=99,
                    task_no="TASK-99",
                    software_name="software",
                    board_name="target",
                    status=int(status),
                    task_type=task_type,
                )
                preview = _build_task_preview(task)
                self.assertEqual(preview["status_label"], expected_label)
                self.assertEqual(preview["status"], expected_status)
                self.assertIn(expected_label, preview["primary_text"])

    def test_endpoint_exposes_availability_and_uses_cached_burner_status(self):
        local_today = _build_local_day_window(self.local_now)
        local_yesterday = _build_local_day_window(self.local_now, day_offset=-1)
        self._add_task(
            "TODAY-S",
            created_at=local_today.start_time + timedelta(hours=1),
            status=TaskStatus.SUCCESS,
        )
        self._add_task(
            "TODAY-F",
            created_at=local_today.start_time + timedelta(hours=2),
            status=TaskStatus.FAILED,
        )
        self._add_task(
            "TODAY-P",
            created_at=local_today.start_time + timedelta(hours=3),
            status=TaskStatus.PENDING,
        )
        self._add_task(
            "YESTERDAY-S",
            created_at=local_yesterday.start_time + timedelta(hours=1),
            status=TaskStatus.SUCCESS,
        )
        self._add_task(
            "OTHER-TODAY",
            owner_id=2,
            created_at=local_today.start_time + timedelta(hours=1),
            status=TaskStatus.SUCCESS,
        )
        self._add_task(
            "FUTURE-TODAY",
            created_at=self.database_now + timedelta(minutes=1),
            status=TaskStatus.SUCCESS,
        )
        idle_burner = Burner(
            name="remote-idle",
            type="ST-LINK",
            host_type="agent",
            agent_url="http://192.0.2.10:8000",
            is_enabled=1,
            status=0,
            sn="IDLE-1",
        )
        busy_burner = Burner(
            name="remote-busy",
            type="J-LINK",
            host_type="agent",
            agent_url="http://192.0.2.11:8000",
            is_enabled=1,
            status=0,
        )
        offline_burner = Burner(
            name="remote-offline",
            type="PWLINK2",
            host_type="agent",
            agent_url="http://192.0.2.12:8000",
            is_enabled=1,
            status=1,
        )
        self.db.add_all([idle_burner, busy_burner, offline_burner])
        self.db.flush()
        occupied_task = self._add_task(
            "OLD-RUNNING",
            created_at=datetime(2025, 1, 1),
            status=TaskStatus.RUNNING,
        )
        occupied_task.burner_id = busy_burner.id
        self.db.commit()
        self.repo_member.name = "uncommitted-change"

        seen_burner_ids = set()

        def cached_after_transaction(burner, occupied_ids):
            self.assertFalse(self.db.in_transaction())
            self.assertTrue(inspect(burner).detached)
            self.assertEqual(occupied_ids, {busy_burner.id})
            seen_burner_ids.add(burner.id)
            return {
                idle_burner.id: 0,
                busy_burner.id: 2,
                offline_burner.id: 1,
            }[burner.id]

        with (
            patch("backend.routers.dashboard.datetime", _FixedDateTime),
            patch(
                "backend.routers.dashboard._compute_burner_cached_status",
                side_effect=cached_after_transaction,
            ),
        ):
            response = get_dashboard_stats(
                trend_months=6,
                target_months=6,
                db=self.db,
                current_user=self._user("self"),
                _=None,
            )

        stats = response["data"]["stats"]
        self.assertEqual(stats["todayTasks"], 3)
        self.assertEqual(stats["todayCompletedTasks"], 2)
        self.assertEqual(stats["todaySuccessfulTasks"], 1)
        self.assertEqual(stats["taskGrowth"], 200.0)
        self.assertTrue(stats["taskGrowthAvailable"])
        self.assertEqual(stats["successRate"], 50.0)
        self.assertTrue(stats["successRateAvailable"])
        self.assertEqual(stats["rateGrowth"], -50.0)
        self.assertTrue(stats["rateGrowthAvailable"])
        self.assertEqual(stats["burnerIdle"], 1)
        self.assertEqual(stats["burnerInUse"], 1)
        self.assertEqual(stats["burnerOffline"], 1)
        self.assertEqual(
            seen_burner_ids,
            {idle_burner.id, busy_burner.id, offline_burner.id},
        )
        self.assertEqual(
            self.db.get(Repository, self.repo_member.id).name,
            "member-repo",
        )

    def test_explicit_project_and_tenant_scopes_hide_outside_recent_tasks_and_messages(self):
        today = _build_local_day_window(self.local_now)
        allowed_task = self._add_task(
            "ALLOWED",
            repository_id=self.repo_member.id,
            created_at=today.start_time + timedelta(hours=1),
            status=TaskStatus.SUCCESS,
            board_name="Allowed-Target",
        )
        allowed_task.finished_at = today.start_time + timedelta(hours=1, minutes=1)
        outside_task = self._add_task(
            "OUTSIDE",
            repository_id=self.repo_outside.id,
            created_at=today.start_time + timedelta(hours=2),
            status=TaskStatus.SUCCESS,
            board_name="Outside-Target",
        )
        outside_task.finished_at = today.start_time + timedelta(hours=2, minutes=1)
        allowed_message = Message(
            user_id=1,
            title="allowed",
            content=json.dumps(
                {
                    "task_no": "ALLOWED",
                    "category": "task",
                    "status": "success",
                    "primary_text": "Allowed-Message",
                }
            ),
            created_at=today.start_time + timedelta(hours=3),
            updated_at=today.start_time + timedelta(hours=3),
        )
        outside_message = Message(
            user_id=1,
            title="outside",
            content=json.dumps(
                {
                    "task_no": "OUTSIDE",
                    "category": "task",
                    "status": "success",
                    "primary_text": "Outside-Message",
                }
            ),
            created_at=today.start_time + timedelta(hours=4),
            updated_at=today.start_time + timedelta(hours=4),
        )
        generic_message = Message(
            user_id=1,
            title="Generic-Message",
            content="generic",
            created_at=today.start_time + timedelta(hours=5),
            updated_at=today.start_time + timedelta(hours=5),
        )
        extra_outside_messages = [
            Message(
                user_id=1,
                title=f"outside-{index}",
                content=json.dumps(
                    {
                        "task_no": "OUTSIDE",
                        "category": "task",
                        "status": "success",
                        "primary_text": f"Outside-Message-{index}",
                    }
                ),
                created_at=today.start_time + timedelta(hours=6 + index),
                updated_at=today.start_time + timedelta(hours=6 + index),
            )
            for index in range(4)
        ]
        older_generic_messages = [
            Message(
                user_id=1,
                title=f"Generic-Old-{index}",
                content=f"generic-old-{index}",
                created_at=today.start_time + timedelta(minutes=10 + index),
                updated_at=today.start_time + timedelta(minutes=10 + index),
            )
            for index in range(3)
        ]
        self.db.add_all(
            [
                allowed_message,
                outside_message,
                generic_message,
                *extra_outside_messages,
                *older_generic_messages,
            ]
        )
        self.db.commit()

        for data_scope in ("project:project-member", "tenant:tenant-a"):
            with self.subTest(data_scope=data_scope):
                with patch("backend.routers.dashboard.datetime", _FixedDateTime):
                    response = get_dashboard_stats(
                        trend_months=6,
                        target_months=6,
                        db=self.db,
                        current_user=self._user(data_scope),
                        _=None,
                    )

                notifications = response["data"]["notifications"]
                rendered = json.dumps(notifications, ensure_ascii=False)
                self.assertIn("Allowed-Message", rendered)
                self.assertIn("Generic-Message", rendered)
                self.assertIn("Generic-Old-0", rendered)
                self.assertNotIn("Outside-Message", rendered)
                self.assertNotIn("Outside-Target", rendered)
                self.assertEqual(len(notifications), 5)
                self.assertEqual(response["data"]["stats"]["todayTasks"], 1)

    def test_endpoint_marks_rate_unavailable_without_completed_samples(self):
        today = _build_local_day_window(self.local_now)
        self._add_task(
            "TODAY-PENDING",
            created_at=today.start_time + timedelta(hours=1),
            status=TaskStatus.PENDING,
        )
        self.db.commit()

        with patch("backend.routers.dashboard.datetime", _FixedDateTime):
            response = get_dashboard_stats(
                trend_months=6,
                target_months=6,
                db=self.db,
                current_user=self._user("self"),
                _=None,
            )

        stats = response["data"]["stats"]
        self.assertIsNone(stats["taskGrowth"])
        self.assertFalse(stats["taskGrowthAvailable"])
        self.assertEqual(stats["successRate"], 0.0)
        self.assertFalse(stats["successRateAvailable"])
        self.assertIsNone(stats["rateGrowth"])
        self.assertFalse(stats["rateGrowthAvailable"])


if __name__ == "__main__":
    unittest.main()
