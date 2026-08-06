import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import (
    Base,
    RepositorySyncChange,
    RepositorySyncPeer,
)
from backend.routers import repositories as repositories_module


class RepositoryDataSyncCoordinatorTests(unittest.TestCase):
    def setUp(self):
        repositories_module._SYNC_CODEARTS_ONLINE_PROJECTS.clear()
        repositories_module._SYNC_MONITOR_ERROR_LOGGED_AT.clear()

    def tearDown(self):
        repositories_module._SYNC_CODEARTS_ONLINE_PROJECTS.clear()
        repositories_module._SYNC_MONITOR_ERROR_LOGGED_AT.clear()
        repositories_module._SYNC_MONITOR_WAKE_EVENT = None

    @staticmethod
    def _candidate(project_key="proj_monitor"):
        return {
            "project_key": project_key,
            "codearts_config": {
                "enabled": True,
                "repository_mode": "private",
                "private_source": "web",
                "project_id": "monitor",
            },
            "triggered_by_user_id": 7,
        }

    def test_codearts_unreachable_does_not_create_job(self):
        needs_run = Mock(return_value=True)
        enqueue = Mock()
        with (
            patch(
                "backend.routers.repositories._probe_codearts_repository_domain",
                return_value=False,
            ),
            patch(
                "backend.routers.repositories._repository_sync_project_needs_run",
                needs_run,
            ),
            patch(
                "backend.routers.repositories._enqueue_repository_data_sync_job",
                enqueue,
            ),
        ):
            asyncio.run(
                repositories_module._monitor_repository_sync_project(
                    self._candidate(),
                    {"role": "client", "connect_timeout_seconds": 2},
                )
            )

        self.assertNotIn("proj_monitor", repositories_module._SYNC_CODEARTS_ONLINE_PROJECTS)
        needs_run.assert_not_called()
        enqueue.assert_not_called()

    def test_offline_to_reachable_transition_triggers_job(self):
        needs_run = Mock(return_value=True)
        enqueue = Mock(return_value=101)
        with (
            patch(
                "backend.routers.repositories._probe_codearts_repository_domain",
                side_effect=[False, True],
            ),
            patch(
                "backend.routers.repositories._repository_sync_project_needs_run",
                needs_run,
            ),
            patch(
                "backend.routers.repositories._enqueue_repository_data_sync_job",
                enqueue,
            ),
        ):
            asyncio.run(
                repositories_module._monitor_repository_sync_project(
                    self._candidate(),
                    {"role": "client", "connect_timeout_seconds": 2},
                )
            )
            self.assertNotIn("proj_monitor", repositories_module._SYNC_CODEARTS_ONLINE_PROJECTS)

            asyncio.run(
                repositories_module._monitor_repository_sync_project(
                    self._candidate(),
                    {"role": "client", "connect_timeout_seconds": 2},
                )
            )

        self.assertIn("proj_monitor", repositories_module._SYNC_CODEARTS_ONLINE_PROJECTS)
        needs_run.assert_called_once_with(
            "proj_monitor",
            {"role": "client", "connect_timeout_seconds": 2},
            became_online=True,
        )
        enqueue.assert_called_once_with(
            "proj_monitor",
            triggered_by_user_id=7,
            trigger_source="codearts_connection_monitor",
        )

    def test_coordinator_task_can_be_cancelled_and_cleans_runtime_state(self):
        async def scenario():
            with (
                patch(
                    "backend.routers.repositories._get_repository_data_sync_config",
                    return_value={"enabled": False, "interval_seconds": 5},
                ),
                patch(
                    "backend.routers.repositories._list_enabled_repository_sync_projects",
                    side_effect=AssertionError("disabled coordinator must not query projects"),
                ),
            ):
                task = asyncio.create_task(
                    repositories_module.run_repository_data_sync_coordinator()
                )
                for _ in range(100):
                    if repositories_module._SYNC_MONITOR_WAKE_EVENT is not None:
                        break
                    await asyncio.sleep(0)
                self.assertIsNotNone(repositories_module._SYNC_MONITOR_WAKE_EVENT)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(scenario())

        self.assertIsNone(repositories_module._SYNC_MONITOR_WAKE_EVENT)
        self.assertEqual(repositories_module._SYNC_CODEARTS_ONLINE_PROJECTS, set())


class RepositoryDataSyncNeedsRunTests(unittest.TestCase):
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

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_client_without_pending_changes_runs_when_remote_has_changes(self):
        project_key = "proj_remote-change"
        server_base_url = "http://192.0.2.25:8000"
        self.db.add(
            RepositorySyncPeer(
                project_key=project_key,
                server_base_url=server_base_url,
                server_instance_id="server-01",
                pulled_revision=9,
                bootstrap_completed_at=datetime(2026, 8, 4, 10, 0, 0),
            )
        )
        self.db.commit()
        status_request = Mock(
            return_value={
                "server_node_id": "server-01",
                "server_instance_id": "server-01",
                "has_changes": True,
            }
        )
        config = {"role": "client", "server_base_url": server_base_url}

        with (
            patch("backend.routers.repositories.SessionLocal", self.SessionLocal),
            patch(
                "backend.routers.repositories._repository_peer_health",
                return_value={
                    "node_id": "server-01",
                    "server_instance_id": "server-01",
                },
            ),
            patch(
                "backend.routers.repositories._repository_peer_request_json",
                status_request,
            ),
        ):
            needs_run = repositories_module._repository_sync_project_needs_run(
                project_key,
                config,
                became_online=False,
            )

        self.assertTrue(needs_run)
        requested_path = status_request.call_args.args[1]
        self.assertIn(
            "/api/repositories/peer-sync/v1/projects/proj_remote-change/status?",
            requested_path,
        )
        self.assertIn("after_revision=9", requested_path)
        self.assertEqual(status_request.call_args.kwargs["method"], "GET")

    def test_server_and_standalone_roles_only_run_for_pending_changes(self):
        self.db.add(
            RepositorySyncChange(
                project_key="proj_pending",
                repo_sync_uuid="artifact-01",
                change_type="upsert",
                status="pending",
                payload_json="{}",
            )
        )
        self.db.commit()

        with (
            patch("backend.routers.repositories.SessionLocal", self.SessionLocal),
            patch(
                "backend.routers.repositories._repository_peer_health",
                side_effect=AssertionError("non-client role must not contact a peer"),
            ),
        ):
            server_pending = repositories_module._repository_sync_project_needs_run(
                "proj_pending",
                {"role": "server"},
                became_online=True,
            )
            server_clean = repositories_module._repository_sync_project_needs_run(
                "proj_clean",
                {"role": "server"},
                became_online=True,
            )
            standalone_pending = repositories_module._repository_sync_project_needs_run(
                "proj_pending",
                {"role": "standalone"},
                became_online=True,
            )
            standalone_clean = repositories_module._repository_sync_project_needs_run(
                "proj_clean",
                {"role": "standalone"},
                became_online=True,
            )

        self.assertTrue(server_pending)
        self.assertFalse(server_clean)
        self.assertTrue(standalone_pending)
        self.assertFalse(standalone_clean)


class RepositoryCodeArtsWebStatusProbeTests(unittest.TestCase):
    def test_web_status_calls_reachability_probe(self):
        project_config = {
            "enabled": True,
            "repository_mode": "private",
            "private_source": "web",
            "project_id": "project-01",
            "domain_name": "tenant",
            "username": "operator",
            "password": "secret",
        }
        probe = Mock(return_value=True)
        db = SimpleNamespace()
        user = SimpleNamespace(id=7)

        with (
            patch("backend.routers.repositories._require_project_membership"),
            patch(
                "backend.routers.repositories._get_project_codearts_config",
                return_value=project_config,
            ),
            patch(
                "backend.routers.repositories._get_repository_data_sync_config",
                return_value={"connect_timeout_seconds": 6},
            ),
            patch(
                "backend.routers.repositories._probe_codearts_repository_domain",
                probe,
            ),
        ):
            result = asyncio.run(
                repositories_module.get_codearts_status("proj_project-01", db, user)
            )

        self.assertTrue(result["data"]["connected"])
        probe.assert_called_once_with(project_config, 6)


if __name__ == "__main__":
    unittest.main()
