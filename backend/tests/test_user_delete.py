import asyncio
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, BurningTask, User
from backend.routers.users import delete_user


class UserDeleteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.admin = User(username="admin", password_hash="unused", status=1)
        self.target = User(username="terminated-user", password_hash="unused", status=1)
        self.db.add_all([self.admin, self.target])
        self.db.flush()
        self.task = BurningTask(
            software_name="firmware.bin",
            status=0,
            created_by_user_id=self.target.id,
            terminated_by_user_id=self.target.id,
        )
        self.db.add(self.task)
        self.db.commit()
        self.target_user_id = int(self.target.id)
        self.task_id = int(self.task.id)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_delete_user_clears_task_creator_and_terminator_foreign_keys(self):
        result = asyncio.run(
            delete_user(
                self.target_user_id,
                db=self.db,
                current_user=self.admin,
                _=None,
            )
        )

        self.db.expire_all()
        persisted_task = self.db.query(BurningTask).filter_by(id=self.task_id).one()
        self.assertEqual(result["code"], 0)
        self.assertIsNone(self.db.query(User).filter_by(id=self.target_user_id).first())
        self.assertIsNone(persisted_task.created_by_user_id)
        self.assertIsNone(persisted_task.terminated_by_user_id)


if __name__ == "__main__":
    unittest.main()
