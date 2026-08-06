import asyncio
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Repository, RepositorySyncChange, RepositorySyncCursor, RepositorySyncJob, RepositorySyncLease, RepositorySyncPeer, RepositorySyncState, User
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
    delete_project,
    delete_repository_artifact,
    recover_repository_auto_sync_jobs,
    trigger_codearts_auto_sync,
    update_repository,
    _run_repository_auto_sync_job,
    _migrate_legacy_auto_sync_state,
    _seed_repository_peer_snapshot,
    rollback_new_codearts_project,
)
from backend.schemas import RepositoryCreate, RepositoryUpdate


class RepositoryAutoSyncTests(unittest.TestCase):
    def setUp(self):
        self.sync_config_patcher = patch(
            "backend.routers.repositories._get_repository_data_sync_config",
            return_value={
                "enabled": True,
                "role": "standalone",
                "server_base_url": "",
                "batch_size": 500,
                "request_timeout_seconds": 30,
            },
        )
        self.sync_config_patcher.start()
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
        self.sync_config_patcher.stop()
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

    def test_legacy_snapshot_assigns_unique_revisions_and_cursor(self):
        project_key = "proj_legacy-multi"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            auto_sync_state_json=json.dumps(
                {
                    "revision": 3,
                    "entries": {
                        "sync-a": {"name": "A.bin"},
                        "sync-b": {"name": "B.bin"},
                    },
                },
                ensure_ascii=False,
            ),
        )
        self.db.add(setting)
        self.db.commit()

        migrated = _migrate_legacy_auto_sync_state(self.db, setting, project_key)
        self.db.commit()

        revisions = [
            row.revision
            for row in self.db.query(RepositorySyncState)
            .filter(RepositorySyncState.project_key == project_key)
            .order_by(RepositorySyncState.revision.asc())
            .all()
        ]
        cursor = self.db.query(RepositorySyncCursor).filter_by(project_key=project_key).one()
        self.assertEqual(migrated, 2)
        self.assertEqual(revisions, [2, 3])
        self.assertEqual(cursor.current_revision, 3)

    def test_legacy_snapshot_drops_database_local_user_id(self):
        project_key = "proj_legacy-user"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            auto_sync_state_json=json.dumps(
                {
                    "revision": 1,
                    "entries": {
                        "sync-user": {
                            "name": "BOOT.bin",
                            "created_by_user_id": 999999,
                        }
                    },
                },
                ensure_ascii=False,
            ),
        )
        self.db.add(setting)
        self.db.commit()

        _migrate_legacy_auto_sync_state(self.db, setting, project_key)
        self.db.commit()

        state = self.db.query(RepositorySyncState).filter_by(project_key=project_key).one()
        self.assertNotIn("created_by_user_id", json.loads(state.payload_json or "{}"))

    def test_peer_state_never_uses_unknown_remote_user_id(self):
        project_key = "proj_remote-user"
        changed_count = _apply_project_auto_sync_state_to_local(
            self.db,
            project_key=project_key,
            state={
                "revision": 1,
                "entries": {
                    "sync-remote-user": {
                        "name": "BOOT.bin",
                        "created_by_user_id": 999999,
                    }
                },
            },
            fallback_user_id=888888,
        )
        self.db.commit()

        repo = self.db.query(Repository).filter_by(project_key=project_key).one()
        self.assertEqual(changed_count, 1)
        self.assertIsNone(repo.created_by_user_id)

    def test_delete_project_keeps_durable_tombstone_until_sync(self):
        project_key = "proj_delete-durable"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            codearts_config_json=json.dumps(
                {"enabled": True, "project_id": "delete-durable"},
                ensure_ascii=False,
            ),
            updated_by_user_id=self.user.id,
        )
        repo = Repository(
            name="BOOT.bin",
            project_key=project_key,
            sync_uuid="sync-delete-durable",
            created_by_user_id=self.user.id,
            source_type="codearts_sync",
        )
        self.db.add_all([setting, repo])
        self.db.commit()

        asyncio.run(delete_project(project_key, self.db, self.user, None))
        self.db.expire_all()

        self.assertEqual(self.db.query(Repository).filter_by(project_key=project_key).count(), 0)
        change = self.db.query(RepositorySyncChange).filter_by(project_key=project_key).one()
        self.assertEqual(change.change_type, _SYNC_CHANGE_DELETE_SERVER)
        self.assertEqual(change.status, _SYNC_CHANGE_PENDING)
        kept_setting = self.db.query(RepositoryProjectSetting).filter_by(project_key=project_key).one()
        kept_config = json.loads(kept_setting.codearts_config_json or "{}")
        self.assertTrue(kept_config.get("_data_sync_project_delete_pending"))

    def test_project_setting_is_removed_after_delete_tombstone_publishes(self):
        project_key = "proj_delete-published"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            codearts_config_json=json.dumps(
                {"enabled": True, "project_id": "delete-published"},
                ensure_ascii=False,
            ),
            updated_by_user_id=self.user.id,
        )
        repo = Repository(
            name="BOOT.bin",
            project_key=project_key,
            sync_uuid="sync-delete-published",
            created_by_user_id=self.user.id,
            source_type="codearts_sync",
        )
        self.db.add_all([setting, repo])
        self.db.commit()
        asyncio.run(delete_project(project_key, self.db, self.user, None))

        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="project_delete_test",
            status=_SYNC_JOB_PENDING,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal):
            _run_repository_auto_sync_job(job.id, project_key)
        self.db.expire_all()

        finished_job = self.db.query(RepositorySyncJob).filter_by(id=job.id).one()
        change = self.db.query(RepositorySyncChange).filter_by(project_key=project_key).one()
        self.assertEqual(finished_job.status, _SYNC_JOB_SUCCESS)
        self.assertEqual(change.status, _SYNC_CHANGE_SYNCED)
        self.assertEqual(
            self.db.query(RepositoryProjectSetting).filter_by(project_key=project_key).count(),
            0,
        )

    def test_initial_sync_rollback_rejects_already_published_project(self):
        project_id = "rollback-published"
        project_key = f"proj_{project_id}"
        setting = RepositoryProjectSetting(
            project_key=project_key,
            codearts_config_json=json.dumps({"enabled": True, "project_id": project_id}),
            updated_by_user_id=self.user.id,
        )
        state = RepositorySyncState(
            project_key=project_key,
            sync_uuid="sync-published",
            revision=1,
            deleted=False,
            payload_json=json.dumps({"name": "BOOT.bin"}),
        )
        self.db.add_all([setting, state])
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(rollback_new_codearts_project(project_id, self.db, self.user, None))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.db.query(RepositoryProjectSetting).filter_by(project_key=project_key).count(), 1)

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
        self.db.add(
            RepositorySyncLease(
                project_key=project_key,
                owner_id="dead-worker",
                lease_until=datetime.utcnow() + timedelta(minutes=30),
            )
        )
        self.db.commit()

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal):
            recover_repository_auto_sync_jobs()
        self.db.expire_all()

        recovered_job = self.db.query(RepositorySyncJob).filter(RepositorySyncJob.id == job.id).one()
        recovered_setting = self.db.query(RepositoryProjectSetting).filter(RepositoryProjectSetting.project_key == project_key).one()

        self.assertEqual(recovered_job.status, _SYNC_JOB_FAILED)
        self.assertIn("服务重启导致同步作业中断", str(recovered_job.error_message or ""))
        self.assertIn("服务重启导致同步作业中断", str(recovered_setting.auto_sync_last_error or ""))
        self.assertEqual(self.db.query(RepositorySyncLease).count(), 0)

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

    def test_client_trigger_with_no_pending_still_launches_pull_job(self):
        project_key = "proj_client-pull"
        self.db.add(
            RepositoryProjectSetting(
                project_key=project_key,
                codearts_config_json=json.dumps({"enabled": True}, ensure_ascii=False),
            )
        )
        self.db.add(
            RepositorySyncJob(
                project_key=project_key,
                triggered_by_user_id=self.user.id,
                trigger_source="auto_connection",
                status=_SYNC_JOB_SUCCESS,
            )
        )
        self.db.commit()
        client_config = {
            "enabled": True,
            "role": "client",
            "server_base_url": "http://server.test:8000",
            "batch_size": 500,
            "request_timeout_seconds": 30,
        }

        with patch(
            "backend.routers.repositories._get_repository_data_sync_config",
            return_value=client_config,
        ), patch(
            "backend.routers.repositories._launch_repository_auto_sync_job",
            return_value=True,
        ) as launch:
            result = asyncio.run(
                trigger_codearts_auto_sync(
                    {"project_key": project_key},
                    self.db,
                    self.user,
                    None,
                )
            )

        self.assertEqual(result["code"], 0)
        launch.assert_called_once()
        self.assertEqual(
            self.db.query(RepositorySyncJob).filter_by(project_key=project_key).count(),
            2,
        )

    def test_client_job_always_pushes_before_pull_even_without_pending(self):
        project_key = "proj_client-order"
        self.db.add(RepositoryProjectSetting(project_key=project_key))
        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="codearts_connection_monitor",
            status=_SYNC_JOB_PENDING,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        events = []
        client_config = {
            "enabled": True,
            "role": "client",
            "server_base_url": "http://server.test:8000",
            "batch_size": 500,
            "request_timeout_seconds": 30,
        }

        def fake_push(*args, **kwargs):
            events.append("push")
            return 0, 0, 0, "server-node", "server-instance"

        def fake_pull(*args, **kwargs):
            events.append("pull")
            return 1

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal), patch(
            "backend.routers.repositories._get_repository_data_sync_config",
            return_value=client_config,
        ), patch(
            "backend.routers.repositories._repository_peer_health",
            return_value={
                "node_id": "server-node",
                "server_instance_id": "server-instance",
                "protocol_version": 1,
            },
        ), patch(
            "backend.routers.repositories._push_repository_changes_to_peer",
            side_effect=fake_push,
        ), patch(
            "backend.routers.repositories._pull_repository_states_from_peer",
            side_effect=fake_pull,
        ):
            _run_repository_auto_sync_job(job.id, project_key)

        self.db.expire_all()
        synced_job = self.db.query(RepositorySyncJob).filter_by(id=job.id).one()
        self.assertEqual(events, ["push", "pull"])
        self.assertEqual(synced_job.status, _SYNC_JOB_SUCCESS)
        self.assertEqual(synced_job.download_count, 1)

    def test_changed_health_epoch_forces_zero_cursor_bootstrap_before_push(self):
        project_key = "proj_restored-server"
        server_url = "http://server.test:8000"
        self.db.add(RepositoryProjectSetting(project_key=project_key))
        self.db.add(
            Repository(
                name="BOOT.bin",
                project_key=project_key,
                sync_uuid="artifact-01",
                source_type="local_upload",
            )
        )
        self.db.add(
            RepositorySyncPeer(
                project_key=project_key,
                server_base_url=server_url,
                server_instance_id="epoch-before-restore",
                pulled_revision=99,
                bootstrap_completed_at=datetime.utcnow(),
            )
        )
        job = RepositorySyncJob(
            project_key=project_key,
            triggered_by_user_id=self.user.id,
            trigger_source="codearts_connection_monitor",
            status=_SYNC_JOB_PENDING,
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        client_config = {
            "enabled": True,
            "role": "client",
            "server_base_url": server_url,
            "batch_size": 500,
            "request_timeout_seconds": 30,
        }
        observed = []

        def fake_push(db, *args, **kwargs):
            peer = db.query(RepositorySyncPeer).filter_by(project_key=project_key).one()
            change = db.query(RepositorySyncChange).filter_by(project_key=project_key).one()
            observed.append(("push", peer.pulled_revision, change.base_revision))
            change.status = _SYNC_CHANGE_SYNCED
            db.add(change)
            db.commit()
            return 1, 0, 0, "server-node", "epoch-after-restore"

        def fake_pull(*args, **kwargs):
            observed.append(("pull", kwargs.get("server_instance_id")))
            return 0

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal), patch(
            "backend.routers.repositories._get_repository_data_sync_config",
            return_value=client_config,
        ), patch(
            "backend.routers.repositories._repository_peer_health",
            return_value={
                "node_id": "server-node",
                "server_instance_id": "epoch-after-restore",
                "protocol_version": 1,
            },
        ), patch(
            "backend.routers.repositories.get_repository_sync_node_id",
            return_value="workstation-01",
        ), patch(
            "backend.routers.repositories._seed_repository_peer_snapshot",
            wraps=_seed_repository_peer_snapshot,
        ) as seed_snapshot, patch(
            "backend.routers.repositories._push_repository_changes_to_peer",
            side_effect=fake_push,
        ), patch(
            "backend.routers.repositories._pull_repository_states_from_peer",
            side_effect=fake_pull,
        ):
            _run_repository_auto_sync_job(job.id, project_key)

        self.db.expire_all()
        peer = self.db.query(RepositorySyncPeer).filter_by(project_key=project_key).one()
        synced_job = self.db.query(RepositorySyncJob).filter_by(id=job.id).one()
        self.assertTrue(seed_snapshot.call_args.kwargs["force"])
        self.assertEqual(seed_snapshot.call_args.kwargs["server_instance_id"], "epoch-after-restore")
        self.assertEqual(observed, [("push", 0, 0), ("pull", "epoch-after-restore")])
        self.assertEqual(peer.server_instance_id, "epoch-after-restore")
        self.assertEqual(peer.pulled_revision, 0)
        self.assertEqual(synced_job.status, _SYNC_JOB_SUCCESS)

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
        self.assertEqual(synced_job.upload_count, 3)
        self.assertEqual(synced_job.pending_change_count, 0)
        self.assertEqual(statuses, [_SYNC_CHANGE_SYNCED, _SYNC_CHANGE_SYNCED, _SYNC_CHANGE_SYNCED])
        self.assertEqual(self.db.query(RepositorySyncState).filter(RepositorySyncState.project_key == project_key).count(), 3)

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
