import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Repository, User
from backend.models.log import Record
from backend.models.repository import RepositoryProjectSetting
from backend.models.task import BurningTask
from backend.routers.repositories import sync_codearts_project


class RepositoryCodeartsSyncTests(unittest.TestCase):
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
        self.user = User(username="admin", password_hash="x", status=1)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_full_refresh_keeps_sync_working_when_local_cleanup_fails(self):
        project_id = "project-1"
        project_key = f"proj_{project_id}"
        self.db.add(
            RepositoryProjectSetting(
                project_key=project_key,
                updated_by_user_id=self.user.id,
                codearts_config_json=json.dumps(
                    {
                        "enabled": True,
                        "domain_name": "tenant",
                        "username": "demo-user",
                        "password": "demo-password",
                        "region": "cn-east-3",
                        "tenant_id": "tenant-id",
                        "project_id": project_id,
                        "base_url": "https://cloudartifacts-ext.{region}.myhuaweicloud.com",
                        "repo_ids": [],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        self.db.add(
            Repository(
                name="old.bin",
                project_key=project_key,
                created_by_user_id=self.user.id,
                source_type="codearts_sync",
                file_url="D:/workspace/pcids/uploads/repositories/bad.pcenc",
                download_uri="https://example.com/download/old.bin",
                display_path="/old.bin",
            )
        )
        self.db.commit()

        with (
            patch("backend.routers.repositories.ensure_schema"),
            patch("backend.routers.repositories._get_iam_token", return_value="token"),
            patch(
                "backend.routers.repositories._get_codearts_project_list",
                return_value=[{"project_id": project_id, "name": "Demo Project", "repo_name": "demo-repo"}],
            ),
            patch("backend.routers.repositories._list_codearts_project_files", return_value=[]),
            patch("backend.routers.repositories._ensure_project_member_seed"),
            patch("backend.routers.repositories._list_running_tasks_for_project", return_value=[]),
            patch("backend.routers.repositories._remove_repository_local_file", side_effect=RuntimeError("cleanup failed")),
        ):
            result = asyncio.run(
                sync_codearts_project(
                    {"project_id": project_id, "full_refresh": True},
                    self.db,
                    self.user,
                    None,
                )
            )

        self.assertEqual(result["code"], 0)
        remaining = (
            self.db.query(Repository)
            .filter(
                Repository.project_key == project_key,
                Repository.created_by_user_id == self.user.id,
                Repository.source_type == "codearts_sync",
            )
            .count()
        )
        self.assertEqual(remaining, 0)

    def test_full_refresh_detaches_historical_foreign_key_references_before_delete(self):
        project_id = "project-2"
        project_key = f"proj_{project_id}"
        self.db.add(
            RepositoryProjectSetting(
                project_key=project_key,
                updated_by_user_id=self.user.id,
                codearts_config_json=json.dumps(
                    {
                        "enabled": True,
                        "domain_name": "tenant",
                        "username": "demo-user",
                        "password": "demo-password",
                        "region": "cn-east-3",
                        "tenant_id": "tenant-id",
                        "project_id": project_id,
                        "base_url": "https://cloudartifacts-ext.{region}.myhuaweicloud.com",
                        "repo_ids": [],
                    },
                    ensure_ascii=False,
                ),
            )
        )
        repo = Repository(
            name="BOOT.bin",
            project_key=project_key,
            created_by_user_id=self.user.id,
            source_type="codearts_sync",
            download_uri="https://example.com/download/BOOT.bin",
            display_path="/BOOT.bin",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)

        self.db.add(
            BurningTask(
                created_by_user_id=self.user.id,
                repository_id=repo.id,
                software_name="BOOT.bin",
                task_type="board",
                status=3,
            )
        )
        self.db.add(
            Record(
                created_by_user_id=self.user.id,
                repository_id=repo.id,
                project_key=None,
                software_name="BOOT.bin",
                operation_time=datetime.now(),
                type="burn",
            )
        )
        self.db.commit()

        with (
            patch("backend.routers.repositories.ensure_schema"),
            patch("backend.routers.repositories._get_iam_token", return_value="token"),
            patch(
                "backend.routers.repositories._get_codearts_project_list",
                return_value=[{"project_id": project_id, "name": "Demo Project", "repo_name": "demo-repo"}],
            ),
            patch("backend.routers.repositories._list_codearts_project_files", return_value=[]),
            patch("backend.routers.repositories._ensure_project_member_seed"),
            patch("backend.routers.repositories._list_running_tasks_for_project", return_value=[]),
        ):
            result = asyncio.run(
                sync_codearts_project(
                    {"project_id": project_id, "full_refresh": True},
                    self.db,
                    self.user,
                    None,
                )
            )

        self.assertEqual(result["code"], 0)
        self.assertEqual(self.db.query(Repository).filter(Repository.id == repo.id).count(), 0)

        task = self.db.query(BurningTask).one()
        record = self.db.query(Record).one()
        self.assertIsNone(task.repository_id)
        self.assertIsNone(record.repository_id)
        self.assertEqual(record.project_key, project_key)


if __name__ == "__main__":
    unittest.main()
