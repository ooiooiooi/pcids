import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Repository, RepositorySyncChange, User
from backend.routers.tasks import _commit_repository_runtime_state_with_outbox


class TaskRepositorySyncOutboxTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self.db = self.SessionLocal()
        self.user = User(username="task-sync-user", password_hash="unused", status=1)
        self.repo = Repository(
            name="firmware.bin",
            project_key="proj_task-sync",
            sync_uuid="task-sync-artifact",
            created_by_user_id=None,
            source_type="codearts_sync",
        )
        self.db.add_all([self.user, self.repo])
        self.db.commit()
        self.db.refresh(self.user)
        self.db.refresh(self.repo)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_repository_runtime_update_commits_pending_outbox(self):
        self.repo.md5 = "updated-md5"
        self.repo.sha256 = "updated-sha256"
        self.repo.size = 4096
        self.repo.file_detail_json = json.dumps(
            {
                "server_exists": True,
                "server_path": "C:/pcids/artifacts/firmware.bin.pcenc",
                "server_target": "artifact-server",
            }
        )

        with patch(
            "backend.routers.repositories.get_repository_sync_node_id",
            return_value="task-test-node",
        ), patch(
            "backend.routers.tasks.wake_repository_data_sync_coordinator",
        ) as wake_coordinator:
            _commit_repository_runtime_state_with_outbox(
                self.db,
                self.repo,
                current_user=self.user,
                source="task_server_download",
            )

        verification_db = self.SessionLocal()
        try:
            persisted_repo = verification_db.query(Repository).filter_by(id=self.repo.id).one()
            change = verification_db.query(RepositorySyncChange).one()
            payload = json.loads(change.payload_json or "{}")
            self.assertEqual(persisted_repo.sha256, "updated-sha256")
            self.assertEqual(change.status, "pending")
            self.assertEqual(change.source, "task_server_download")
            self.assertEqual(payload["md5"], "updated-md5")
            self.assertEqual(payload["sha256"], "updated-sha256")
            self.assertEqual(payload["server_path"], "C:/pcids/artifacts/firmware.bin.pcenc")
        finally:
            verification_db.close()
        wake_coordinator.assert_called_once_with()

    def test_commit_failure_rolls_back_repository_and_outbox_together(self):
        self.repo.md5 = "must-not-commit"

        with patch(
            "backend.routers.repositories.get_repository_sync_node_id",
            return_value="task-test-node",
        ), patch.object(
            self.db,
            "commit",
            side_effect=RuntimeError("simulated commit failure"),
        ), patch(
            "backend.routers.tasks.wake_repository_data_sync_coordinator",
        ) as wake_coordinator:
            with self.assertRaisesRegex(RuntimeError, "simulated commit failure"):
                _commit_repository_runtime_state_with_outbox(
                    self.db,
                    self.repo,
                    current_user=self.user,
                    source="task_local_download",
                )
        self.db.rollback()

        verification_db = self.SessionLocal()
        try:
            persisted_repo = verification_db.query(Repository).filter_by(id=self.repo.id).one()
            self.assertIsNone(persisted_repo.md5)
            self.assertEqual(verification_db.query(RepositorySyncChange).count(), 0)
        finally:
            verification_db.close()
        wake_coordinator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
