import asyncio
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Repository, RepositorySyncChange, RepositorySyncJob, RepositorySyncState, User
from backend.models.repository import RepositoryProjectSetting
from backend.routers.repositories import (
    _SYNC_CHANGE_DELETE_SERVER,
    _SYNC_CHANGE_PENDING,
    _SYNC_CHANGE_RESOLVED_SERVER,
    _SYNC_CHANGE_SYNCED,
    _SYNC_CHANGE_UPSERT,
    _SYNC_JOB_FAILED,
    _SYNC_JOB_PENDING,
    _SYNC_JOB_SUCCESS,
    _apply_project_auto_sync_state_to_local,
    _apply_repository_location_state,
    create_repository,
    delete_repository_artifact,
    recover_repository_auto_sync_jobs,
    trigger_codearts_auto_sync,
    update_repository,
    _run_repository_auto_sync_job,
)
from backend.schemas import RepositoryCreate, RepositoryUpdate


class RepositoryAutoSyncTests(unittest.TestCase):
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

    def test_apply_server_delete_keeps_local_copy(self):
        project_key = "proj_sync-delete"
        repo = Repository(
            name="BOOT.bin",
            project_key=project_key,
            sync_uuid="sync-delete-1",
            created_by_user_id=self.user.id,
            source_type="local_upload",
            download_uri="https://example.com/download/BOOT.bin",
        )
        _apply_repository_location_state(
            repo,
            {},
            local_exists=True,
            local_path="Z:/pcids-test/BOOT.bin.pcenc",
            server_exists=True,
            server_path="C:/pcids-artifacts/BOOT.bin.pcenc",
            server_target="127.0.0.1:8000",
        )
        self.db.add(repo)
        self.db.commit()

        changed_count = _apply_project_auto_sync_state_to_local(
            self.db,
            project_key=project_key,
            state={"revision": 1, "entries": {"sync-delete-1": {"deleted": True}}},
            fallback_user_id=self.user.id,
        )
        self.db.commit()
        self.db.expire_all()

        self.assertEqual(changed_count, 1)
        kept_repo = self.db.query(Repository).filter(Repository.project_key == project_key).one()
        file_detail = json.loads(kept_repo.file_detail_json or "{}")
        self.assertIsNone(kept_repo.download_uri)
        self.assertTrue(file_detail.get("local_exists"))
        self.assertFalse(file_detail.get("server_exists"))
        self.assertTrue(file_detail.get("sync_deleted_on_server"))

    def test_auto_sync_job_prefers_server_state_when_server_is_newer(self):
        project_key = "proj_sync-conflict"
        sync_uuid = "sync-conflict-1"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            auto_sync_state_json=json.dumps(
                {
                    "revision": 3,
                    "entries": {
                        sync_uuid: {
                            "sync_uuid": sync_uuid,
                            "project_key": project_key,
                            "name": "Server.bin",
                            "description": "/server/Server.bin",
                            "updated_at": "2026-06-30T10:00:00",
                            "server_exists": True,
                            "server_path": "C:/pcids-artifacts/Server.bin.pcenc",
                            "server_target": "127.0.0.1:8000",
                            "download_uri": "https://example.com/download/Server.bin",
                            "source_type": "codearts_sync",
                        }
                    },
                },
                ensure_ascii=False,
            ),
        )
        repo = Repository(
            name="Local.bin",
            project_key=project_key,
            sync_uuid=sync_uuid,
            created_by_user_id=self.user.id,
            source_type="local_upload",
        )
        change = RepositorySyncChange(
            project_key=project_key,
            repo_sync_uuid=sync_uuid,
            repo_db_id=None,
            change_type=_SYNC_CHANGE_UPSERT,
            status=_SYNC_CHANGE_PENDING,
            payload_json=json.dumps(
                {
                    "sync_uuid": sync_uuid,
                    "project_key": project_key,
                    "name": "Local.bin",
                    "updated_at": "2026-06-30T09:00:00",
                    "source_type": "local_upload",
                },
                ensure_ascii=False,
            ),
            created_by_user_id=self.user.id,
        )
        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="auto_connection",
            status=_SYNC_JOB_PENDING,
        )
        self.db.add_all([setting, repo, change, job])
        self.db.commit()
        self.db.refresh(job)

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal):
            _run_repository_auto_sync_job(job.id, project_key)
        self.db.expire_all()

        synced_job = self.db.query(RepositorySyncJob).filter(RepositorySyncJob.id == job.id).one()
        synced_change = self.db.query(RepositorySyncChange).filter(RepositorySyncChange.id == change.id).one()
        synced_repo = self.db.query(Repository).filter(Repository.project_key == project_key).one()
        sync_state = self.db.query(RepositorySyncState).filter(RepositorySyncState.project_key == project_key).one()
        setting_after = self.db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).one()

        self.assertEqual(synced_job.status, _SYNC_JOB_SUCCESS)
        self.assertEqual(int(synced_job.conflict_count or 0), 1)
        self.assertEqual(int(synced_job.upload_count or 0), 0)
        self.assertEqual(synced_change.status, _SYNC_CHANGE_RESOLVED_SERVER)
        self.assertEqual(synced_repo.name, "Server.bin")
        self.assertEqual(synced_repo.download_uri, "https://example.com/download/Server.bin")
        self.assertEqual(sync_state.sync_uuid, sync_uuid)
        self.assertFalse(sync_state.deleted)
        self.assertIsNone(setting_after.auto_sync_state_json)

    def test_recover_interrupted_auto_sync_jobs_marks_failed(self):
        project_key = "proj_sync-recover"
        setting = RepositoryProjectSetting(project_key=project_key)
        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="auto_connection",
            status=_SYNC_JOB_PENDING,
        )
        self.db.add_all([setting, job])
        self.db.commit()

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal):
            recover_repository_auto_sync_jobs()
        self.db.expire_all()

        recovered_job = self.db.query(RepositorySyncJob).filter(RepositorySyncJob.id == job.id).one()
        recovered_setting = self.db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).one()

        self.assertEqual(recovered_job.status, _SYNC_JOB_FAILED)
        self.assertIn("服务重启导致同步作业中断", str(recovered_job.error_message or ""))
        self.assertIn("服务重启导致同步作业中断", str(recovered_setting.auto_sync_last_error or ""))

    def test_trigger_auto_sync_skips_when_previous_success_has_no_pending_changes(self):
        project_key = "proj_no-pending"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            codearts_config_json=json.dumps({"enabled": True}, ensure_ascii=False),
        )
        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="auto_connection",
            status=_SYNC_JOB_SUCCESS,
        )
        self.db.add_all([setting, job])
        self.db.commit()

        with patch("backend.routers.repositories._launch_repository_auto_sync_job") as launch:
            result = asyncio.run(trigger_codearts_auto_sync({"project_key": project_key}, self.db, self.user, None))

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["pending_change_count"], 0)
        launch.assert_not_called()
        self.assertEqual(self.db.query(RepositorySyncJob).filter(RepositorySyncJob.project_key == project_key).count(), 1)

    def test_trigger_auto_sync_does_not_leave_pending_job_when_runtime_lock_rejects_launch(self):
        project_key = "proj_lock-reject"
        self.db.add(
            RepositoryProjectSetting(
                project_key=project_key,
                codearts_config_json=json.dumps({"enabled": True}, ensure_ascii=False),
            )
        )
        self.db.add(
            RepositorySyncChange(
                project_key=project_key,
                repo_sync_uuid="sync-1",
                change_type=_SYNC_CHANGE_UPSERT,
                status=_SYNC_CHANGE_PENDING,
                payload_json=json.dumps({"sync_uuid": "sync-1", "name": "BOOT.bin"}, ensure_ascii=False),
                created_by_user_id=self.user.id,
            )
        )
        self.db.commit()

        with patch("backend.routers.repositories._launch_repository_auto_sync_job", return_value=False):
            result = asyncio.run(trigger_codearts_auto_sync({"project_key": project_key}, self.db, self.user, None))

        self.assertEqual(result["code"], 0)
        jobs = self.db.query(RepositorySyncJob).filter(RepositorySyncJob.project_key == project_key).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].status, _SYNC_JOB_FAILED)
        self.assertIn("duplicate trigger skipped", str(jobs[0].error_message or ""))

    def test_auto_sync_job_processes_pending_changes_in_bounded_batches(self):
        project_key = "proj_batch-limit"
        self.db.add(RepositoryProjectSetting(project_key=project_key))
        for index in range(3):
            sync_uuid = f"sync-{index}"
            self.db.add(
                RepositorySyncChange(
                    project_key=project_key,
                    repo_sync_uuid=sync_uuid,
                    change_type=_SYNC_CHANGE_UPSERT,
                    status=_SYNC_CHANGE_PENDING,
                    payload_json=json.dumps(
                        {
                            "sync_uuid": sync_uuid,
                            "project_key": project_key,
                            "name": f"BOOT-{index}.bin",
                            "updated_at": "2026-06-30T10:00:00",
                        },
                        ensure_ascii=False,
                    ),
                    created_by_user_id=self.user.id,
                )
            )
        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="auto_connection",
            status=_SYNC_JOB_PENDING,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal), patch(
            "backend.routers.repositories._SYNC_AUTO_BATCH_LIMIT",
            2,
        ):
            _run_repository_auto_sync_job(job.id, project_key)
        self.db.expire_all()

        synced_job = self.db.query(RepositorySyncJob).filter(RepositorySyncJob.id == job.id).one()
        statuses = [
            item.status
            for item in self.db.query(RepositorySyncChange)
            .filter(RepositorySyncChange.project_key == project_key)
            .order_by(RepositorySyncChange.id.asc())
            .all()
        ]

        self.assertEqual(synced_job.status, _SYNC_JOB_SUCCESS)
        self.assertEqual(synced_job.upload_count, 2)
        self.assertEqual(synced_job.pending_change_count, 1)
        self.assertEqual(statuses, [_SYNC_CHANGE_SYNCED, _SYNC_CHANGE_SYNCED, _SYNC_CHANGE_PENDING])
        self.assertEqual(self.db.query(RepositorySyncState).filter(RepositorySyncState.project_key == project_key).count(), 2)

    def test_repository_crud_writes_pending_sync_changes(self):
        project_key = "proj_sync-crud"

        with patch("backend.routers.repositories._ensure_project_member_seed"), patch(
            "backend.routers.repositories._require_project_permission"
        ), patch("backend.routers.repositories._remove_repository_server_artifact"), patch(
            "backend.routers.repositories._remove_repository_file_by_path"
        ):
            create_result = asyncio.run(
                create_repository(
                    RepositoryCreate(
                        name="boot.bin",
                        project_key=project_key,
                        description="v1",
                        file_url="D:/workspace/pcids/uploads/repositories/local/boot.bin.pcenc",
                    ),
                    self.db,
                    self.user,
                    None,
                )
            )
            self.assertEqual(create_result["code"], 0)

            repo = self.db.query(Repository).filter(Repository.project_key == project_key).one()
            changes = (
                self.db.query(RepositorySyncChange)
                .filter(RepositorySyncChange.project_key == project_key)
                .order_by(RepositorySyncChange.id.asc())
                .all()
            )
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].change_type, _SYNC_CHANGE_UPSERT)

            update_result = asyncio.run(
                update_repository(
                    repo.id,
                    RepositoryUpdate(description="v2"),
                    self.db,
                    self.user,
                    None,
                )
            )
            self.assertEqual(update_result["code"], 0)

            repo = self.db.query(Repository).filter(Repository.id == repo.id).one()
            file_detail = json.loads(repo.file_detail_json or "{}")
            _apply_repository_location_state(
                repo,
                file_detail,
                local_exists=True,
                local_path="D:/workspace/pcids/uploads/repositories/local/boot.bin.pcenc",
                server_exists=True,
                server_path="C:/pcids-artifacts/boot.bin.pcenc",
                server_target="127.0.0.1:8000",
            )
            self.db.add(repo)
            self.db.commit()

            delete_result = asyncio.run(
                delete_repository_artifact(
                    repo.id,
                    "server",
                    self.db,
                    self.user,
                    None,
                )
            )
            self.assertEqual(delete_result["code"], 0)

        final_changes = (
            self.db.query(RepositorySyncChange)
            .filter(RepositorySyncChange.project_key == project_key)
            .order_by(RepositorySyncChange.id.asc())
            .all()
        )
        self.assertEqual([item.change_type for item in final_changes], [_SYNC_CHANGE_UPSERT, _SYNC_CHANGE_UPSERT, _SYNC_CHANGE_DELETE_SERVER])


if __name__ == "__main__":
    unittest.main()
