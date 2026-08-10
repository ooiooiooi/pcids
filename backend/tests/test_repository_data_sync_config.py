import asyncio
import json
import os
import unittest
import urllib.error
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import (
    Base,
    Repository,
    RepositorySyncChange,
    RepositorySyncCursor,
    RepositorySyncInstance,
    RepositorySyncPeer,
    RepositorySyncReceipt,
    RepositorySyncState,
)
from backend.utils.repository_data_sync import (
    build_repository_sync_headers,
    get_repository_sync_node_id,
    get_repository_sync_server_epoch,
    normalize_repository_data_sync_config,
    require_repository_sync_request,
)
from backend.routers import repositories as repositories_module


class RepositoryDataSyncConfigTests(unittest.TestCase):
    def test_auto_role_without_server_is_standalone(self):
        config = normalize_repository_data_sync_config({})

        self.assertTrue(config["enabled"])
        self.assertEqual(config["configured_role"], "auto")
        self.assertEqual(config["role"], "standalone")
        self.assertEqual(config["server_base_url"], "")

    def test_auto_role_with_remote_server_is_client(self):
        with patch("backend.utils.repository_data_sync._is_self_target", return_value=False):
            config = normalize_repository_data_sync_config(
                {
                    "server_ip": "192.0.2.20",
                    "server_port": 9000,
                    "server_transport": "ssh",
                    "server_ssh_port": 22,
                }
            )

        self.assertEqual(config["role"], "client")
        self.assertEqual(config["server_host"], "192.0.2.20")
        self.assertEqual(config["server_port"], 9000)
        self.assertEqual(config["server_base_url"], "http://192.0.2.20:9000")

    def test_self_target_is_server_even_if_client_was_requested(self):
        config = normalize_repository_data_sync_config(
            {
                "server_ip": "127.0.0.1",
                "server_port": 8000,
                "repository_data_sync_role": "client",
            }
        )

        self.assertTrue(config["is_self_target"])
        self.assertEqual(config["configured_role"], "client")
        self.assertEqual(config["role"], "server")

    def test_same_host_different_backend_port_can_be_a_client(self):
        with (
            patch("backend.utils.repository_data_sync._is_self_target", return_value=True),
            patch.dict(os.environ, {"PCIDS_BACKEND_PORT": "18001"}, clear=False),
        ):
            config = normalize_repository_data_sync_config(
                {
                    "server_ip": "127.0.0.1",
                    "server_port": 18000,
                    "repository_data_sync_role": "client",
                }
            )
        self.assertEqual(config["role"], "client")
        self.assertFalse(config["is_self_target"])

    def test_explicit_server_and_disabled_roles_are_safe(self):
        server = normalize_repository_data_sync_config(
            {"repository_data_sync_role": "server"}
        )
        disabled = normalize_repository_data_sync_config(
            {
                "repository_data_sync_enabled": False,
                "repository_data_sync_role": "client",
                "server_ip": "192.0.2.20",
            }
        )

        self.assertEqual(server["role"], "server")
        self.assertEqual(disabled["role"], "standalone")

    def test_scheme_limits_and_batch_settings_are_normalized(self):
        config = normalize_repository_data_sync_config(
            {
                "server_ip": "https://sync.example.test:9443/ignored",
                "server_port": "8443",
                "repository_data_sync_scheme": "https",
                "repository_data_sync_interval_seconds": 45,
                "repository_data_sync_connect_timeout_seconds": 4.5,
                "repository_data_sync_request_timeout_seconds": 120,
                "repository_data_sync_batch_size": 250,
            }
        )

        self.assertEqual(config["scheme"], "https")
        self.assertEqual(config["server_host"], "sync.example.test")
        self.assertEqual(config["server_port"], 8443)
        self.assertEqual(config["server_base_url"], "https://sync.example.test:8443")
        self.assertEqual(config["interval_seconds"], 45.0)
        self.assertEqual(config["connect_timeout_seconds"], 4.5)
        self.assertEqual(config["request_timeout_seconds"], 120.0)
        self.assertEqual(config["batch_size"], 250)

    def test_invalid_tuning_values_fall_back_to_defaults(self):
        config = normalize_repository_data_sync_config(
            {
                "server_port": "not-a-port",
                "repository_data_sync_scheme": "file",
                "repository_data_sync_interval_seconds": 0,
                "repository_data_sync_connect_timeout_seconds": 999,
                "repository_data_sync_request_timeout_seconds": "bad",
                "repository_data_sync_batch_size": 0,
            }
        )

        self.assertEqual(config["scheme"], "http")
        self.assertEqual(config["server_port"], 8000)
        self.assertEqual(config["interval_seconds"], 30.0)
        self.assertEqual(config["connect_timeout_seconds"], 3.0)
        self.assertEqual(config["request_timeout_seconds"], 30.0)
        self.assertEqual(config["batch_size"], 500)

    def test_default_json_contains_peer_sync_defaults(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "repository_download.json"
        payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(payload["repository_data_sync_enabled"])
        self.assertEqual(payload["repository_data_sync_role"], "auto")
        self.assertEqual(payload["repository_data_sync_scheme"], "http")
        self.assertEqual(payload["repository_data_sync_batch_size"], 500)


class RepositorySyncNodeIdentityTests(unittest.TestCase):
    def test_environment_node_id_takes_priority(self):
        with patch.dict(
            os.environ,
            {
                "PCIDS_REPOSITORY_SYNC_NODE_ID": "workstation-01",
                "PCIDS_SYNC_NODE_ID": "",
            },
            clear=False,
        ):
            self.assertEqual(get_repository_sync_node_id(), "workstation-01")

    def test_node_id_is_persisted_in_data_root(self):
        with TemporaryDirectory() as temp:
            with patch.dict(
                os.environ,
                {
                    "PCIDS_DATA_DIR": temp,
                    "PCIDS_REPOSITORY_SYNC_NODE_ID": "",
                    "PCIDS_SYNC_NODE_ID": "",
                },
                clear=False,
            ):
                first = get_repository_sync_node_id()
                second = get_repository_sync_node_id()

            self.assertEqual(first, second)
            self.assertEqual(str(uuid.UUID(first)), first)
            self.assertEqual(
                (Path(temp) / "repository-sync-node-id").read_text(encoding="utf-8").strip(),
                first,
            )

    def test_outgoing_headers_reuse_agent_token_and_add_loop_metadata(self):
        with patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"):
            headers = build_repository_sync_headers(
                origin_node_id="workstation-01",
                hop=1,
            )

        self.assertEqual(headers["X-PCIDS-Agent-Token"], "shared-secret")
        self.assertEqual(headers["X-PCIDS-Sync-Origin"], "workstation-01")
        self.assertEqual(headers["X-PCIDS-Sync-Hop"], "1")

    def test_valid_inbound_request_is_accepted(self):
        request = SimpleNamespace(
            headers={
                "X-PCIDS-Agent-Token": "shared-secret",
                "X-PCIDS-Sync-Origin": "workstation-01",
                "X-PCIDS-Sync-Hop": "1",
            }
        )
        with (
            patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"),
            patch("backend.utils.repository_data_sync.get_repository_sync_node_id", return_value="server-01"),
        ):
            result = require_repository_sync_request(request)

        self.assertEqual(result, {"origin_node_id": "workstation-01", "hop": 1})

    def test_request_from_same_node_is_rejected_as_loop(self):
        request = SimpleNamespace(
            headers={
                "X-PCIDS-Agent-Token": "shared-secret",
                "X-PCIDS-Sync-Origin": "server-01",
                "X-PCIDS-Sync-Hop": "1",
            }
        )
        with (
            patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"),
            patch("backend.utils.repository_data_sync.get_repository_sync_node_id", return_value="server-01"),
        ):
            with self.assertRaises(HTTPException) as raised:
                require_repository_sync_request(request)

        self.assertEqual(raised.exception.status_code, 508)
    def test_forwarded_request_is_rejected_as_loop(self):
        request = SimpleNamespace(
            headers={
                "X-PCIDS-Agent-Token": "shared-secret",
                "X-PCIDS-Sync-Origin": "workstation-01",
                "X-PCIDS-Sync-Hop": "2",
            }
        )
        with (
            patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"),
            patch("backend.utils.repository_data_sync.get_repository_sync_node_id", return_value="server-01"),
        ):
            with self.assertRaises(HTTPException) as raised:
                require_repository_sync_request(request)

        self.assertEqual(raised.exception.status_code, 508)

    def test_missing_origin_is_rejected_after_agent_authentication(self):
        request = SimpleNamespace(
            headers={
                "X-PCIDS-Agent-Token": "shared-secret",
                "X-PCIDS-Sync-Hop": "1",
            }
        )
        with patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"):
            with self.assertRaises(HTTPException) as raised:
                require_repository_sync_request(request)

        self.assertEqual(raised.exception.status_code, 400)


class RepositorySyncServerEpochTests(unittest.TestCase):
    def test_sidecar_is_restart_stable_and_rotates_on_backup_revision_regression(self):
        with TemporaryDirectory() as temp:
            sidecar = Path(temp) / "repository-sync-server-epoch.json"
            database_instance_id = "database-instance-01"

            first = get_repository_sync_server_epoch(
                database_instance_id=database_instance_id,
                project_revisions={"proj_sensitive-name": 8},
                sidecar_path=sidecar,
            )
            restarted = get_repository_sync_server_epoch(
                database_instance_id=database_instance_id,
                project_revisions={"proj_sensitive-name": 8},
                sidecar_path=sidecar,
            )
            advanced = get_repository_sync_server_epoch(
                database_instance_id=database_instance_id,
                project_revisions={"proj_sensitive-name": 12},
                sidecar_path=sidecar,
            )
            restored = get_repository_sync_server_epoch(
                database_instance_id=database_instance_id,
                project_revisions={"proj_sensitive-name": 8},
                sidecar_path=sidecar,
            )
            restarted_after_restore = get_repository_sync_server_epoch(
                database_instance_id=database_instance_id,
                project_revisions={"proj_sensitive-name": 8},
                sidecar_path=sidecar,
            )

            self.assertNotEqual(first, database_instance_id)
            self.assertEqual(len(first), 32)
            self.assertEqual(restarted, first)
            self.assertEqual(advanced, first)
            self.assertNotEqual(restored, first)
            self.assertEqual(restarted_after_restore, restored)
            self.assertNotIn("proj_sensitive-name", sidecar.read_text(encoding="utf-8"))

    def test_replaced_database_marker_rotates_epoch_without_revision_regression(self):
        with TemporaryDirectory() as temp:
            sidecar = Path(temp) / "repository-sync-server-epoch.json"
            first = get_repository_sync_server_epoch(
                database_instance_id="database-instance-01",
                project_revisions={"proj_01": 3},
                sidecar_path=sidecar,
            )
            replaced = get_repository_sync_server_epoch(
                database_instance_id="database-instance-02",
                project_revisions={"proj_01": 3},
                sidecar_path=sidecar,
            )

            self.assertNotEqual(replaced, first)


class RepositoryDataSyncYamlTests(unittest.TestCase):
    def test_external_yaml_drives_peer_address_but_not_ssh_port(self):
        with TemporaryDirectory() as temp:
            config_path = Path(temp) / "repository_download.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "server_transport: ssh",
                        "server_ip: 192.0.2.25",
                        "server_port: 8123",
                        "server_ssh_port: 2222",
                        "repository_data_sync_role: auto",
                        "repository_data_sync_scheme: http",
                    ]
                ),
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"PCIDS_REPOSITORY_DOWNLOAD_CONFIG": str(config_path)},
                    clear=False,
                ),
                patch("backend.utils.repository_data_sync._is_self_target", return_value=False),
            ):
                config = repositories_module._get_repository_data_sync_config()

        self.assertEqual(config["role"], "client")
        self.assertEqual(config["server_host"], "192.0.2.25")
        self.assertEqual(config["server_port"], 8123)
        self.assertEqual(config["server_base_url"], "http://192.0.2.25:8123")

    def test_external_yaml_self_target_overrides_client_role(self):
        with TemporaryDirectory() as temp:
            config_path = Path(temp) / "repository_download.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "server_ip: localhost",
                        "server_port: 8000",
                        "repository_data_sync_role: client",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"PCIDS_REPOSITORY_DOWNLOAD_CONFIG": str(config_path)},
                clear=False,
            ):
                config = repositories_module._get_repository_data_sync_config()

        self.assertEqual(config["configured_role"], "client")
        self.assertTrue(config["is_self_target"])
        self.assertEqual(config["role"], "server")


class _JsonResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class RepositoryPeerProtocolTests(unittest.TestCase):
    def test_peer_request_uses_configured_base_url_and_sync_headers(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _JsonResponse({"code": 0, "data": {"node_id": "server-01"}})

        config = {
            "server_base_url": "http://192.0.2.25:8123",
            "request_timeout_seconds": 9,
        }
        with (
            patch(
                "backend.routers.repositories.build_repository_sync_headers",
                return_value={
                    "X-PCIDS-Agent-Token": "shared-secret",
                    "X-PCIDS-Sync-Origin": "workstation-01",
                    "X-PCIDS-Sync-Hop": "1",
                },
            ),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            result = repositories_module._repository_peer_request_json(
                config,
                "/api/repositories/peer-sync/v1/health",
                method="GET",
            )

        request = captured["request"]
        self.assertEqual(
            request.full_url,
            "http://192.0.2.25:8123/api/repositories/peer-sync/v1/health",
        )
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.get_header("X-pcids-agent-token"), "shared-secret")
        self.assertEqual(request.get_header("X-pcids-sync-origin"), "workstation-01")
        self.assertEqual(request.get_header("X-pcids-sync-hop"), "1")
        self.assertEqual(captured["timeout"], 9)
        self.assertEqual(result, {"node_id": "server-01"})

    def test_peer_health_rejects_remote_instance_with_same_node_id(self):
        with (
            patch(
                "backend.routers.repositories._repository_peer_request_json",
                return_value={"node_id": "node-01", "protocol_version": 1},
            ),
            patch(
                "backend.routers.repositories.get_repository_sync_node_id",
                return_value="node-01",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "当前实例"):
                repositories_module._repository_peer_health(
                    {"server_base_url": "http://127.0.0.1:8000"}
                )

    def test_peer_health_endpoint_requires_agent_authentication(self):
        request = SimpleNamespace(
            headers={
                "X-PCIDS-Sync-Origin": "workstation-01",
                "X-PCIDS-Sync-Hop": "1",
            }
        )
        with patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(repositories_module.repository_peer_sync_health(request))

        self.assertEqual(raised.exception.status_code, 401)

    def test_peer_health_endpoint_rejects_loop_before_serving(self):
        request = SimpleNamespace(
            headers={
                "X-PCIDS-Agent-Token": "shared-secret",
                "X-PCIDS-Sync-Origin": "server-01",
                "X-PCIDS-Sync-Hop": "1",
            }
        )
        with (
            patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"),
            patch(
                "backend.utils.repository_data_sync.get_repository_sync_node_id",
                return_value="server-01",
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(repositories_module.repository_peer_sync_health(request))

        self.assertEqual(raised.exception.status_code, 508)


class RepositoryPeerStateProtocolTests(unittest.TestCase):
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
        self.project_key = "proj_peer-protocol"

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    @staticmethod
    def _upsert_change(
        change_uuid="change-01",
        *,
        base_revision=0,
        parent_change_uuid=None,
        name="BOOT.bin",
    ):
        return {
            "change_uuid": change_uuid,
            "parent_change_uuid": parent_change_uuid,
            "sync_uuid": "artifact-01",
            "operation": "upsert",
            "base_revision": base_revision,
            "changed_at": "2026-08-04T10:00:00Z",
            "payload": {
                "name": name,
                "source_type": "local_upload",
                "updated_at": "2026-08-04T10:00:00Z",
            },
        }

    def _apply(self, origin, changes):
        with (
            patch(
                "backend.routers.repositories.get_repository_sync_node_id",
                return_value="server-01",
            ),
            patch(
                "backend.routers.repositories.get_repository_sync_server_epoch",
                return_value="server-epoch",
            ),
        ):
            result = repositories_module._apply_repository_peer_changes(
                self.db,
                project_key=self.project_key,
                origin_node_id=origin,
                changes=changes,
            )
            self.db.commit()
            return result

    def test_first_push_creates_authoritative_state_and_local_repository(self):
        result = self._apply("workstation-01", [self._upsert_change()])

        self.assertEqual(result["server_node_id"], "server-01")
        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(result["results"][0]["outcome"], "applied")
        state = self.db.query(RepositorySyncState).one()
        receipt = self.db.query(RepositorySyncReceipt).one()
        repository = self.db.query(Repository).one()
        self.assertEqual(state.revision, 1)
        self.assertEqual(state.origin_node_id, "workstation-01")
        self.assertEqual(state.origin_change_uuid, "change-01")
        self.assertEqual(receipt.server_revision, 1)
        self.assertEqual(repository.name, "BOOT.bin")
        self.assertEqual(repository.sync_uuid, "artifact-01")

    def test_applied_push_persists_latest_revision_to_external_epoch_watermark(self):
        with (
            patch(
                "backend.routers.repositories.get_repository_sync_node_id",
                return_value="server-01",
            ),
            patch(
                "backend.routers.repositories.get_repository_sync_server_epoch",
                return_value="server-epoch",
            ) as epoch_mock,
        ):
            repositories_module._apply_repository_peer_changes(
                self.db,
                project_key=self.project_key,
                origin_node_id="workstation-01",
                changes=[self._upsert_change()],
            )

        self.assertGreaterEqual(epoch_mock.call_count, 2)
        self.assertEqual(
            epoch_mock.call_args.kwargs["project_revisions"][self.project_key],
            1,
        )

    def test_retried_change_is_idempotent_and_does_not_advance_revision(self):
        self._apply("workstation-01", [self._upsert_change()])
        retry = self._apply("workstation-01", [self._upsert_change()])

        self.assertEqual(retry["server_revision"], 1)
        self.assertEqual(retry["results"][0]["outcome"], "already_applied")
        self.assertEqual(self.db.query(RepositorySyncReceipt).count(), 1)
        self.assertEqual(self.db.query(RepositorySyncState).one().revision, 1)

    def test_change_uuid_cannot_be_replayed_with_different_payload(self):
        self._apply("workstation-01", [self._upsert_change()])
        changed_payload = self._upsert_change(name="MUTATED.bin")

        retry = self._apply("workstation-01", [changed_payload])

        self.assertEqual(retry["server_revision"], 1)
        self.assertEqual(retry["results"][0]["outcome"], "invalid")
        self.assertEqual(self.db.query(Repository).one().name, "BOOT.bin")
        self.assertTrue(self.db.query(RepositorySyncReceipt).one().request_hash)

    def test_state_uses_server_epoch_not_node_identity(self):
        result = self._apply("workstation-01", [self._upsert_change()])

        self.assertTrue(result["server_instance_id"])
        self.assertNotEqual(result["server_instance_id"], result["server_node_id"])
        self.assertEqual(
            self.db.query(RepositorySyncState).one().server_instance_id,
            result["server_instance_id"],
        )

    def test_server_epoch_rotates_when_same_database_marker_revision_rolls_back(self):
        self.db.add(RepositorySyncInstance(id=1, instance_uuid="database-instance-01"))
        cursor = RepositorySyncCursor(project_key=self.project_key, current_revision=12)
        self.db.add(cursor)
        self.db.commit()

        with TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"PCIDS_REPOSITORY_SYNC_EPOCH_PATH": str(Path(temp) / "server-epoch.json")},
            clear=False,
        ):
            first = repositories_module._get_repository_server_instance_id(self.db)
            cursor.current_revision = 5
            self.db.add(cursor)
            self.db.commit()
            restored = repositories_module._get_repository_server_instance_id(self.db)

        self.assertNotEqual(restored, first)

    def test_change_uuid_cannot_be_reused_by_another_node(self):
        self._apply("workstation-01", [self._upsert_change()])
        reused = self._apply("workstation-02", [self._upsert_change()])

        self.assertEqual(reused["server_revision"], 1)
        self.assertEqual(reused["results"][0]["outcome"], "invalid")
        self.assertEqual(self.db.query(RepositorySyncReceipt).count(), 1)

    def test_stale_delete_cannot_overwrite_newer_server_state(self):
        self._apply("workstation-01", [self._upsert_change()])
        stale_delete = {
            "change_uuid": "change-delete-stale",
            "sync_uuid": "artifact-01",
            "operation": "delete",
            "base_revision": 0,
            "changed_at": "2026-08-04T09:00:00Z",
            "payload": {"updated_at": "2026-08-04T09:00:00Z"},
        }

        result = self._apply("workstation-02", [stale_delete])

        self.assertEqual(result["server_revision"], 1)
        self.assertEqual(result["results"][0]["outcome"], "conflict_server_wins")
        state = self.db.query(RepositorySyncState).one()
        self.assertFalse(state.deleted)
        self.assertEqual(self.db.query(Repository).one().name, "BOOT.bin")

    def test_parent_change_chain_can_apply_next_local_edit(self):
        self._apply("workstation-01", [self._upsert_change()])
        chained = self._upsert_change(
            "change-02",
            base_revision=0,
            parent_change_uuid="change-01",
            name="BOOT-v2.bin",
        )

        result = self._apply("workstation-01", [chained])

        self.assertEqual(result["server_revision"], 2)
        self.assertEqual(result["results"][0]["outcome"], "applied")
        state = self.db.query(RepositorySyncState).one()
        self.assertEqual(state.revision, 2)
        self.assertEqual(state.origin_change_uuid, "change-02")
        self.assertEqual(self.db.query(Repository).one().name, "BOOT-v2.bin")

    def test_changed_server_instance_forces_zero_based_local_bootstrap(self):
        self.db.add(
            Repository(
                name="BOOT.bin",
                project_key=self.project_key,
                sync_uuid="artifact-01",
                source_type="local_upload",
            )
        )
        self.db.add(
            RepositorySyncPeer(
                project_key=self.project_key,
                server_base_url="http://server.test:8000",
                server_instance_id="old-instance",
                pulled_revision=99,
                bootstrap_completed_at=datetime.utcnow(),
            )
        )
        self.db.commit()

        with patch(
            "backend.routers.repositories.get_repository_sync_node_id",
            return_value="workstation-01",
        ):
            peer = repositories_module._seed_repository_peer_snapshot(
                self.db,
                self.project_key,
                {"server_base_url": "http://server.test:8000"},
                force=True,
                server_instance_id="new-instance",
            )
            self.db.commit()

        change = self.db.query(RepositorySyncChange).one()
        self.assertEqual(peer.server_instance_id, "new-instance")
        self.assertEqual(peer.pulled_revision, 0)
        self.assertIsNone(peer.bootstrap_completed_at)
        self.assertEqual(change.base_revision, 0)
        self.assertEqual(change.status, "pending")

    def test_canonical_revision_rebase_is_safe_across_batches(self):
        self.db.add_all(
            [
                RepositorySyncState(
                    project_key=self.project_key,
                    sync_uuid="artifact-a",
                    revision=1,
                    deleted=False,
                    payload_json=json.dumps({"name": "A-old.bin"}),
                    server_instance_id="old-instance",
                ),
                RepositorySyncState(
                    project_key=self.project_key,
                    sync_uuid="artifact-b",
                    revision=2,
                    deleted=False,
                    payload_json=json.dumps({"name": "B-old.bin"}),
                    server_instance_id="old-instance",
                ),
            ]
        )
        self.db.commit()

        with patch(
            "backend.routers.repositories._upsert_repository_sync_state",
            side_effect=RuntimeError("projection failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "projection failed"):
                repositories_module._apply_peer_canonical_states(
                    self.db,
                    project_key=self.project_key,
                    states=[
                        {
                            "sync_uuid": "artifact-a",
                            "revision": 2,
                            "deleted": False,
                            "server_instance_id": "new-instance",
                            "payload": {"name": "A.bin", "source_type": "local_upload"},
                        }
                    ],
                    fallback_user_id=None,
                )
        self.db.rollback()
        original_revisions = {
            row.sync_uuid: row.revision
            for row in self.db.query(RepositorySyncState).all()
        }
        self.assertEqual(original_revisions, {"artifact-a": 1, "artifact-b": 2})

        # A@2 would previously collide immediately with the still-present B@2,
        # even though B receives its authoritative revision in the next batch.
        repositories_module._apply_peer_canonical_states(
            self.db,
            project_key=self.project_key,
            states=[
                {
                    "sync_uuid": "artifact-a",
                    "revision": 2,
                    "deleted": False,
                    "server_instance_id": "new-instance",
                    "payload": {"name": "A.bin", "source_type": "local_upload"},
                }
            ],
            fallback_user_id=None,
        )
        self.db.commit()

        staged_b = self.db.query(RepositorySyncState).filter_by(sync_uuid="artifact-b").one()
        self.assertLess(staged_b.revision, 0)

        repositories_module._apply_peer_canonical_states(
            self.db,
            project_key=self.project_key,
            states=[
                {
                    "sync_uuid": "artifact-b",
                    "revision": 3,
                    "deleted": False,
                    "server_instance_id": "new-instance",
                    "payload": {"name": "B.bin", "source_type": "local_upload"},
                }
            ],
            fallback_user_id=None,
        )
        self.db.commit()

        revisions = {
            row.sync_uuid: row.revision
            for row in self.db.query(RepositorySyncState)
            .filter(RepositorySyncState.project_key == self.project_key)
            .all()
        }
        self.assertEqual(revisions, {"artifact-a": 2, "artifact-b": 3})

    def test_instance_bootstrap_tombstone_supersedes_retained_local_upsert(self):
        sync_uuid = "artifact-deleted"
        repo = Repository(
            name="KEEP-LOCAL.bin",
            project_key=self.project_key,
            sync_uuid=sync_uuid,
            source_type="local_upload",
            file_detail_json=json.dumps(
                {
                    "local_exists": True,
                    "local_path": "D:/cache/KEEP-LOCAL.bin.pcenc",
                    "server_exists": False,
                    "sync_deleted_on_server": True,
                }
            ),
        )
        self.db.add_all(
            [
                repo,
                RepositorySyncState(
                    project_key=self.project_key,
                    sync_uuid=sync_uuid,
                    revision=7,
                    deleted=True,
                    payload_json=json.dumps(
                        {"sync_uuid": sync_uuid, "name": "KEEP-LOCAL.bin", "deleted": True}
                    ),
                    server_instance_id="old-instance",
                ),
                RepositorySyncPeer(
                    project_key=self.project_key,
                    server_base_url="http://server.test:8000",
                    server_instance_id="old-instance",
                    pulled_revision=7,
                    bootstrap_completed_at=datetime.utcnow(),
                ),
                RepositorySyncChange(
                    change_uuid="stale-bootstrap-upsert",
                    project_key=self.project_key,
                    repo_sync_uuid=sync_uuid,
                    change_type="upsert",
                    status="pending",
                    payload_json=json.dumps({"name": "KEEP-LOCAL.bin"}),
                    base_revision=7,
                ),
            ]
        )
        self.db.commit()

        repositories_module._seed_repository_peer_snapshot(
            self.db,
            self.project_key,
            {"server_base_url": "http://server.test:8000"},
            force=True,
            server_instance_id="new-instance",
        )
        self.db.commit()

        pending = (
            self.db.query(RepositorySyncChange)
            .filter_by(project_key=self.project_key, repo_sync_uuid=sync_uuid, status="pending")
            .all()
        )
        self.assertTrue(pending)
        self.assertTrue(all(change.change_type == "delete_server" for change in pending))
        self.assertNotIn("stale-bootstrap-upsert", {change.change_uuid for change in pending})
        self.assertTrue(
            all(
                repositories_module._repository_change_wire_payload(change)["operation"] == "delete"
                for change in pending
            )
        )
        self.assertTrue(all(json.loads(change.payload_json)["deleted"] for change in pending))

    def test_server_upgrade_baseline_blocks_zero_based_client_overwrite(self):
        sync_uuid = "artifact-server-owned"
        self.db.add(
            Repository(
                name="SERVER.bin",
                project_key=self.project_key,
                sync_uuid=sync_uuid,
                source_type="local_upload",
            )
        )
        self.db.commit()

        with patch("backend.routers.repositories.SessionLocal", self.SessionLocal):
            self.assertTrue(
                repositories_module._repository_sync_project_needs_run(
                    self.project_key,
                    {"role": "server"},
                    became_online=False,
                )
            )

        uploaded, conflicts, failed = repositories_module._publish_local_repository_changes(
            self.db,
            project_key=self.project_key,
        )
        self.assertEqual((uploaded, conflicts, failed), (1, 0, 0))
        baseline = self.db.query(RepositorySyncState).filter_by(sync_uuid=sync_uuid).one()
        self.assertEqual(baseline.revision, 1)
        self.assertEqual(self.db.query(RepositorySyncChange).one().source, "server_upgrade_baseline")

        result = self._apply(
            "workstation-01",
            [
                {
                    "change_uuid": "client-zero-base",
                    "sync_uuid": sync_uuid,
                    "operation": "upsert",
                    "base_revision": 0,
                    "changed_at": "2026-08-04T12:00:00Z",
                    "payload": {
                        "name": "CLIENT.bin",
                        "source_type": "local_upload",
                        "updated_at": "2026-08-04T12:00:00Z",
                    },
                }
            ],
        )

        self.assertEqual(result["results"][0]["outcome"], "conflict_server_wins")
        self.assertEqual(self.db.query(Repository).filter_by(sync_uuid=sync_uuid).one().name, "SERVER.bin")
        self.assertEqual(self.db.query(RepositorySyncState).filter_by(sync_uuid=sync_uuid).one().revision, 1)


class RepositoryCodeArtsProbeTests(unittest.TestCase):
    @staticmethod
    def _service_defaults(devops_url="https://trusted.example.test/cloudartifact"):
        return {
            "iam_token_url": "https://iam.{region}.example.test/v3/auth/tokens",
            "base_url": "https://api.{region}.example.test/service/path",
            "private_iam_token_url": "https://private-iam.example.test/v3/auth/tokens",
            "private_base_url": "https://private-api.example.test/service/path",
            "devops_url": devops_url,
        }

    def test_web_probe_uses_trusted_devops_origin_not_project_url(self):
        requests = []

        class RedirectingOpener:
            def open(self, request, timeout):
                requests.append((request, timeout))
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "login redirect",
                    hdrs=None,
                    fp=None,
                )

        with (
            patch(
                "backend.routers.repositories._get_repository_codearts_service_config",
                return_value=self._service_defaults(),
            ),
            patch("urllib.request.build_opener", return_value=RedirectingOpener()),
        ):
            reachable = repositories_module._probe_codearts_repository_domain(
                {
                    "enabled": True,
                    "repository_mode": "private",
                    "private_source": "web",
                    "private_repository_url": "http://127.0.0.1:9999/private/repo",
                    "project_id": "project-01",
                },
                timeout_seconds=4,
            )

        self.assertTrue(reachable)
        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "https://trusted.example.test/")
        self.assertEqual(request.method, "HEAD")
        self.assertEqual(timeout, 4)

    def test_api_probe_formats_region_but_only_requests_origin(self):
        requests = []

        class SuccessfulOpener:
            def open(self, request, timeout):
                requests.append(request)
                return _JsonResponse({}, status=403)

        with (
            patch(
                "backend.routers.repositories._get_repository_codearts_service_config",
                return_value=self._service_defaults(),
            ),
            patch("urllib.request.build_opener", return_value=SuccessfulOpener()),
        ):
            reachable = repositories_module._probe_codearts_repository_domain(
                {
                    "enabled": True,
                    "repository_mode": "release",
                    "region": "cn-cq-1",
                }
            )

        self.assertTrue(reachable)
        self.assertEqual(requests[0].full_url, "https://api.cn-cq-1.example.test/")
        self.assertEqual(requests[0].method, "HEAD")


if __name__ == "__main__":
    unittest.main()
