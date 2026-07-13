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
from backend.routers.repositories import _filter_repositories_for_active_codearts_mode, sync_codearts_project


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

    def test_release_refresh_preserves_private_artifact_and_pending_task_reference(self):
        project_id = "project-mode-isolation"
        project_key = f"proj_{project_id}"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            updated_by_user_id=self.user.id,
            codearts_config_json=json.dumps(
                {
                    "enabled": True,
                    "repository_mode": "release",
                    "domain_name": "tenant",
                    "username": "demo-user",
                    "password": "demo-password",
                    "region": "cn-east-3",
                    "project_id": project_id,
                    "base_url": "https://cloudartifacts-ext.{region}.myhuaweicloud.com",
                    "repo_ids": [],
                    "private_repo_id": "cn-east-3_0123456789abcdef0123456789abcdef_generic_0",
                },
                ensure_ascii=False,
            ),
        )
        private_repo = Repository(
            name="private.bin",
            project_key=project_key,
            created_by_user_id=self.user.id,
            source_type="codearts_sync",
            download_uri="https://private.example.com/private.bin",
            display_path="/private.bin",
            repo_detail_json=json.dumps({"repository_mode": "private"}),
        )
        self.db.add_all([setting, private_repo])
        self.db.commit()
        self.db.refresh(private_repo)
        task = BurningTask(
            created_by_user_id=self.user.id,
            repository_id=private_repo.id,
            software_name="private.bin",
            task_type="board",
            status=0,
        )
        self.db.add(task)
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
        self.assertIsNotNone(self.db.query(Repository).filter(Repository.id == private_repo.id).first())
        self.db.refresh(task)
        self.assertEqual(task.repository_id, private_repo.id)

    def test_repository_visibility_follows_active_mode_without_deleting_other_mode(self):
        project_key = "proj_visible-mode"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            codearts_config_json=json.dumps({"repository_mode": "release"}),
        )
        release_repo = Repository(
            name="release.bin",
            project_key=project_key,
            source_type="codearts_sync",
            repo_detail_json=json.dumps({"repository_mode": "release"}),
        )
        private_repo = Repository(
            name="private.bin",
            project_key=project_key,
            source_type="codearts_sync",
            repo_detail_json=json.dumps({"repository_mode": "private"}),
        )
        self.db.add_all([setting, release_repo, private_repo])
        self.db.commit()

        visible_release = _filter_repositories_for_active_codearts_mode(
            [release_repo, private_repo], self.db, self.user
        )
        self.assertEqual([repo.name for repo in visible_release], ["release.bin"])

        setting.codearts_config_json = json.dumps({"repository_mode": "private"})
        self.db.add(setting)
        self.db.commit()
        visible_private = _filter_repositories_for_active_codearts_mode(
            [release_repo, private_repo], self.db, self.user
        )
        self.assertEqual([repo.name for repo in visible_private], ["private.bin"])


if __name__ == "__main__":
    unittest.main()
