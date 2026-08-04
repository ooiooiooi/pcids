import csv
import html
import io
import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import (
    Base,
    Burner,
    BurningTask,
    Repository,
    RepositoryProjectMember,
    Role,
    Script,
    User,
)
from backend.routers.tasks import (
    _apply_task_scope,
    _build_consistency_report_csv,
    _build_consistency_report_html,
)


class TaskScopeTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        role = Role(name="任务范围测试角色", data_scope="all")
        self.user = User(
            username="scope-user",
            password_hash="unused",
            role=role,
            status=1,
        )
        self.other_user = User(
            username="other-user",
            password_hash="unused",
            role=role,
            status=1,
        )
        repo_a = Repository(name="repo-a", project_key="project-a", tenant="tenant-a")
        repo_b = Repository(name="repo-b", project_key="project-b", tenant="tenant-b")
        self.db.add_all([role, self.user, self.other_user, repo_a, repo_b])
        self.db.flush()

        self.own_task = BurningTask(
            task_no="OWN",
            software_name="own",
            created_by_user_id=self.user.id,
            repository_id=repo_b.id,
        )
        self.project_task = BurningTask(
            task_no="PROJECT",
            software_name="project",
            created_by_user_id=self.other_user.id,
            repository_id=repo_a.id,
        )
        self.outside_task = BurningTask(
            task_no="OUTSIDE",
            software_name="outside",
            created_by_user_id=self.other_user.id,
            repository_id=repo_b.id,
        )
        self.db.add_all(
            [
                self.own_task,
                self.project_task,
                self.outside_task,
                RepositoryProjectMember(
                    project_key="project-a",
                    user_id=self.user.id,
                    role="member",
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _task_numbers_for_scope(self, data_scope):
        current_user = SimpleNamespace(
            id=self.user.id,
            role=SimpleNamespace(data_scope=data_scope),
        )
        query = _apply_task_scope(self.db.query(BurningTask), self.db, current_user)
        return {task.task_no for task in query.all()}

    def test_explicit_all_scope_keeps_existing_full_visibility(self):
        self.assertEqual(
            self._task_numbers_for_scope("all"),
            {"OWN", "PROJECT", "OUTSIDE"},
        )

    def test_self_scope_keeps_only_tasks_created_by_current_user(self):
        self.assertEqual(self._task_numbers_for_scope("self"), {"OWN"})

    def test_member_project_scope_keeps_own_and_member_project_tasks(self):
        self.assertEqual(self._task_numbers_for_scope("project"), {"OWN", "PROJECT"})

    def test_fixed_tenant_and_project_scopes_keep_their_existing_filters(self):
        self.assertEqual(self._task_numbers_for_scope("tenant: tenant-a"), {"PROJECT"})
        self.assertEqual(self._task_numbers_for_scope("project: project-b"), {"OWN", "OUTSIDE"})

    def test_empty_unknown_or_non_string_scope_is_denied(self):
        invalid_scopes = [
            None,
            "",
            "tenant:",
            "tenant: \t",
            "project:",
            "project: , ",
            "unknown",
            123,
        ]
        for data_scope in invalid_scopes:
            with self.subTest(data_scope=data_scope):
                self.assertEqual(self._task_numbers_for_scope(data_scope), set())

    def test_error_while_reading_scope_is_denied(self):
        class BrokenCurrentUser:
            id = self.user.id

            @property
            def role(self):
                raise RuntimeError("broken role relation")

        query = _apply_task_scope(
            self.db.query(BurningTask),
            self.db,
            BrokenCurrentUser(),
        )
        self.assertEqual(query.all(), [])


class ConsistencyReportSafetyTests(unittest.TestCase):
    @staticmethod
    def _task(**overrides):
        values = {
            "id": 7,
            "task_no": "TASK-7",
            "software_name": "firmware",
            "serial_number": "SN-7",
            "history_checksum": "history",
            "current_sha256": "current",
            "consistency_passed": 1,
            "attempt_count": 1,
            "max_retries": 2,
            "rollback_count": 0,
            "rollback_result": "正常",
        }
        values.update(overrides)
        return BurningTask(**values)

    def test_html_report_escapes_every_user_controlled_value(self):
        malicious_values = [
            '<img src=x onerror="alert(1)">',
            "</p><script>alert(2)</script>",
            "<b>executor</b>",
            "<svg/onload=alert(3)>",
            "<iframe srcdoc=bad>",
            "<script>alert(4)</script>",
            "<a href=javascript:alert(5)>checksum</a>",
            "<details open ontoggle=alert(6)>",
            "<math href=javascript:alert(7)>",
        ]
        task = self._task(
            task_no=malicious_values[0],
            serial_number=malicious_values[1],
            history_checksum=malicious_values[6],
            current_sha256=malicious_values[7],
            rollback_result=malicious_values[8],
        )
        repo = Repository(name=malicious_values[3], version="v1")
        burner = Burner(name=malicious_values[4], type="test")
        script = Script(name=malicious_values[5], type="test", content="")

        rendered = _build_consistency_report_html(
            task,
            repo,
            burner,
            script,
            malicious_values[2],
            False,
        )

        for value in malicious_values:
            self.assertNotIn(value, rendered)
            expected = html.escape(
                f"{value} v1" if value == malicious_values[3] else value,
                quote=True,
            )
            self.assertIn(expected, rendered)
        self.assertNotIn("<script>", rendered.lower())

    def test_csv_report_neutralizes_formulas_after_leading_whitespace(self):
        task = self._task(
            task_no="=CMD()",
            serial_number=" \t+SUM(1,1)",
            history_checksum="\r\n@danger",
            current_sha256="正常校验码",
            rollback_result="   -10+20",
        )
        repo = Repository(name="-恶意制品", version="v1")
        burner = Burner(name="普通烧录器", type="test")
        script = Script(name="\t=HYPERLINK(\"x\")", type="test", content="")

        rendered = _build_consistency_report_csv(
            task,
            repo,
            burner,
            script,
            "@executor",
        )
        rows = list(csv.reader(io.StringIO(rendered)))
        values = rows[1]

        for index in (0, 1, 2, 3, 5, 6, 11):
            self.assertTrue(values[index].startswith("'"), values[index])
        self.assertEqual(values[4], "普通烧录器")
        self.assertEqual(values[7], "正常校验码")

    def test_csv_report_preserves_normal_chinese_values(self):
        task = self._task(serial_number="设备一", rollback_result="无需回滚")
        rendered = _build_consistency_report_csv(
            task,
            Repository(name="正式制品", version="版本一"),
            Burner(name="一号烧录器", type="test"),
            Script(name="正式脚本", type="test", content=""),
            "张三",
        )

        values = next(csv.reader(io.StringIO(rendered).read().splitlines()[1:]))
        self.assertEqual(values[1:6], ["设备一", "张三", "正式制品 版本一", "一号烧录器", "正式脚本"])
        self.assertEqual(values[11], "无需回滚")


if __name__ == "__main__":
    unittest.main()
