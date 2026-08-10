import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, LoginLog, OperationLog, User
from backend.routers.logs import get_login_logs, get_operation_logs


class LogQueryEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.user = User(username="operator", password_hash="unused", status=1)
        self.db.add(self.user)
        self.db.flush()
        now = datetime(2026, 8, 10, 12, 0, 0)
        self.db.add_all(
            [
                LoginLog(
                    user_id=self.user.id,
                    login_time=now + timedelta(seconds=index),
                    log_type="login",
                    result="成功",
                )
                for index in range(20)
            ]
        )
        self.db.add_all(
            [
                OperationLog(
                    user_id=self.user.id,
                    operation_time=now + timedelta(seconds=index),
                    module="任务",
                    action="测试",
                    result="成功",
                )
                for index in range(20)
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _count_selects(self, callback):
        statements = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            result = callback()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statement)
        return result, statements

    def test_operation_log_page_bulk_loads_users(self):
        result, statements = self._count_selects(
            lambda: get_operation_logs(
                page=1,
                page_size=20,
                user_id=None,
                keyword=None,
                module=None,
                result=None,
                start_date=None,
                end_date=None,
                db=self.db,
                current_user=self.user,
                _=None,
            )
        )
        self.assertEqual(len(result["data"]), 20)
        self.assertEqual(len(statements), 3)

    def test_login_log_page_bulk_loads_users(self):
        result, statements = self._count_selects(
            lambda: get_login_logs(
                page=1,
                page_size=20,
                user_id=None,
                keyword=None,
                log_type=None,
                result=None,
                start_date=None,
                end_date=None,
                db=self.db,
                current_user=self.user,
                _=None,
            )
        )
        self.assertEqual(len(result["data"]), 20)
        self.assertEqual(len(statements), 3)


if __name__ == "__main__":
    unittest.main()
