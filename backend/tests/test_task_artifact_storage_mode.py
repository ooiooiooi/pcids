import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from backend.routers.tasks import (
    _build_task_codearts_download_auth,
    _cleanup_repository_artifacts_after_execution,
    _download_repository_artifact_to_local_storage,
    _download_repository_artifact_to_server_storage,
    _ensure_repository_local_file_available_for_runtime,
    _refresh_repository_artifact_after_key_failure,
    _resolve_artifact_storage_mode,
    _resolve_task_artifact_download_source,
)


class TaskArtifactStorageModeTests(unittest.TestCase):
    def test_task_download_source_reads_nested_web_repository_metadata(self):
        repo = SimpleNamespace(project_key="proj_web")
        serialized = {
            "repo_detail": {
                "repository_mode": "private",
                "private_source": "web",
            }
        }
        web_config = {
            "repository_mode": "private",
            "private_source": "web",
        }

        with patch(
            "backend.routers.tasks._get_project_codearts_config",
            return_value=web_config,
        ):
            source_mode, resolved_config = _resolve_task_artifact_download_source(
                MagicMock(), repo, SimpleNamespace(id=1), serialized
            )

        self.assertEqual(source_mode, "web_session")
        self.assertIs(resolved_config, web_config)

    def test_task_download_source_uses_api_for_explicit_api_repository(self):
        repo = SimpleNamespace(project_key="proj_private_api")
        serialized = {
            "repo_detail": {
                "repository_mode": "private",
                "private_source": "api",
            }
        }

        with patch(
            "backend.routers.tasks._get_project_codearts_config",
            return_value={
                "repository_mode": "private",
                "private_source": "api",
            },
        ) as config_mock:
            source_mode, resolved_config = _resolve_task_artifact_download_source(
                MagicMock(), repo, SimpleNamespace(id=1), serialized
            )

        self.assertEqual(source_mode, "codearts_api")
        self.assertIsNone(resolved_config)
        config_mock.assert_called_once()

    def test_current_web_project_config_overrides_stale_api_artifact_marker(self):
        repo = SimpleNamespace(project_key="proj_switched_to_web")
        serialized = {
            "repo_detail": {
                "repository_mode": "private",
                "private_source": "api",
            }
        }
        web_config = {
            "repository_mode": "private",
            "private_source": "web",
        }

        with patch(
            "backend.routers.tasks._get_project_codearts_config",
            return_value=web_config,
        ):
            source_mode, resolved_config = _resolve_task_artifact_download_source(
                MagicMock(), repo, SimpleNamespace(id=1), serialized
            )

        self.assertEqual(source_mode, "web_session")
        self.assertIs(resolved_config, web_config)

    def test_task_download_uses_mode_saved_on_repository(self):
        repo = SimpleNamespace(
            project_key="proj_private",
            repo_detail_json=json.dumps({"repository_mode": "private"}),
        )
        config = {"repository_mode": "private", "region": "cn-cq-1", "base_url": "https://codearts.example.com"}
        with patch(
            "backend.routers.tasks._build_codearts_download_context",
            return_value=(config, "private-token"),
        ) as context_mock, patch(
            "backend.routers.tasks._resolve_codearts_download_auth",
            return_value={"token": None, "username": "repo-user", "password": "repo-password", "mode": "basic"},
        ) as auth_mock:
            result = _build_task_codearts_download_auth(MagicMock(), repo, SimpleNamespace(id=1))

        self.assertEqual(result["mode"], "basic")
        context_mock.assert_called_once_with(
            ANY,
            ANY,
            "proj_private",
            repository_mode="private",
        )
        auth_mock.assert_called_once_with(config, "https://codearts.example.com", "private-token")

    def test_private_codearts_runtime_download_uses_basic_auth(self):
        repo = SimpleNamespace(
            id=21,
            project_key="proj_private",
            name="firmware.bin",
            download_uri="https://devrepo.example.com/artgalaxy/repo/firmware.bin",
            file_detail_json="{}",
            file_url=None,
            md5=None,
            sha256=None,
            size=None,
        )
        stored_artifact = SimpleNamespace(
            md5="md5-value",
            sha256="sha256-value",
            plaintext_size=128,
            to_storage_metadata=lambda: {"encrypted": True},
        )
        db = MagicMock()

        with patch(
            "backend.routers.tasks.repository_to_dict",
            return_value={"download_uri": repo.download_uri, "file_detail": {}},
        ), patch(
            "backend.routers.tasks._build_task_codearts_download_auth",
            return_value={"token": None, "username": "repo-user", "password": "repo-password", "mode": "basic"},
        ), patch(
            "backend.routers.tasks._get_repository_download_root",
            return_value=r"D:\cache",
        ), patch(
            "backend.routers.tasks.build_encrypted_artifact_path",
            return_value=r"D:\cache\firmware.bin.pcenc",
        ), patch(
            "backend.routers.tasks._encrypt_remote_artifact_to_storage",
            return_value=stored_artifact,
        ) as encrypt_mock, patch(
            "backend.routers.tasks._get_repository_location_state",
            return_value={"server_exists": False, "server_path": None, "server_target": None},
        ), patch("backend.routers.tasks._apply_repository_location_state"), patch(
            "backend.routers.tasks._commit_repository_runtime_state_with_outbox",
        ) as commit_with_outbox:
            result = _download_repository_artifact_to_local_storage(db, repo, SimpleNamespace(id=1))

        self.assertIs(result, repo)
        encrypt_mock.assert_called_once_with(
            download_uri=repo.download_uri,
            destination_path=r"D:\cache\firmware.bin.pcenc",
            original_name="firmware.bin",
            token=None,
            username="repo-user",
            password="repo-password",
            timeout_seconds=300,
        )
        commit_with_outbox.assert_called_once_with(
            db,
            repo,
            current_user=ANY,
            source="task_local_download",
        )

    def test_codearts_uses_local_storage_for_local_burner(self):
        self.assertEqual(_resolve_artifact_storage_mode("codearts", "local", None), "local")

    def test_codearts_uses_server_storage_for_agent_burner(self):
        self.assertEqual(_resolve_artifact_storage_mode("codearts", "agent", "http://192.168.1.20:8000"), "server")

    def test_web_codearts_server_storage_uses_browser_session_download(self):
        repo = SimpleNamespace(
            id=31,
            project_key="proj_web",
            name="firmware.bin",
            download_uri="https://devops.example.com/download?id=file&signature=secret",
            file_detail_json="{}",
            file_url=None,
            md5=None,
            sha256=None,
            size=None,
        )
        stored_artifact = SimpleNamespace(
            md5="web-md5",
            sha256="web-sha256",
            plaintext_size=256,
            to_storage_metadata=lambda: {"encrypted": True},
        )
        db = MagicMock()
        current_user = SimpleNamespace(id=1, username="admin")
        location_state = {
            "local_exists": False,
            "local_path": None,
            "server_exists": False,
            "server_path": None,
            "server_target": None,
        }

        with patch(
            "backend.routers.tasks.repository_to_dict",
            return_value={
                "download_uri": repo.download_uri,
                "repo_detail": {"private_source": "web"},
                "file_detail": {},
            },
        ), patch(
            "backend.routers.tasks._get_project_codearts_config",
            return_value={
                "enabled": True,
                "repository_mode": "private",
                "private_source": "web",
                "project_id": "web",
            },
        ), patch(
            "backend.routers.tasks._encrypt_codearts_web_download",
            return_value=(stored_artifact, []),
        ) as web_download, patch(
            "backend.routers.tasks._build_task_codearts_download_auth",
        ) as api_auth, patch(
            "backend.routers.tasks._get_repository_download_root",
            return_value=r"E:\PCIDS\data\repositories",
        ), patch(
            "backend.routers.tasks.build_encrypted_artifact_path",
            return_value=r"E:\PCIDS\data\repositories\firmware.bin.pcenc",
        ), patch(
            "backend.routers.tasks._get_repository_server_transport_config",
            return_value={
                "transport": "local",
                "host": "",
                "port": 0,
                "username": "",
                "password": "",
                "server_os": "windows",
            },
        ), patch(
            "backend.routers.tasks._get_repository_download_config",
            return_value={},
        ), patch(
            "backend.routers.tasks._get_repository_server_storage_root",
            return_value=r"E:\PCIDS\data\server",
        ), patch(
            "backend.routers.tasks._get_repository_location_state",
            return_value=location_state,
        ), patch("backend.routers.tasks._apply_repository_location_state"), patch(
            "backend.routers.tasks._commit_repository_runtime_state_with_outbox",
        ) as commit_with_outbox:
            result = _download_repository_artifact_to_server_storage(db, repo, current_user)

        self.assertIs(result, repo)
        self.assertEqual(repo.sha256, "web-sha256")
        api_auth.assert_not_called()
        self.assertEqual(web_download.call_args.kwargs["download_uri"], repo.download_uri)
        self.assertEqual(
            web_download.call_args.kwargs["click_target"]["artifactName"],
            "firmware.bin",
        )
        self.assertTrue(str(web_download.call_args.kwargs["trace_id"]).startswith("task-artifact-server-"))
        commit_with_outbox.assert_called_once_with(
            db,
            repo,
            current_user=current_user,
            source="task_server_download",
        )

    def test_server_artifact_cleanup_commits_repository_with_sync_outbox(self):
        repo = SimpleNamespace(
            id=44,
            file_detail_json=json.dumps(
                {
                    "local_exists": False,
                    "server_exists": True,
                    "server_path": "C:/pcids/artifacts/firmware.bin.pcenc",
                    "server_target": "artifact-server",
                }
            ),
        )
        task = SimpleNamespace(
            id=55,
            config_json=json.dumps(
                {
                    "cleanup_local_artifact_after_execution": False,
                    "cleanup_server_artifact_after_execution": True,
                }
            ),
        )
        db = MagicMock()
        location_state = {
            "local_exists": False,
            "local_path": None,
            "server_exists": True,
            "server_path": "C:/pcids/artifacts/firmware.bin.pcenc",
            "server_target": "artifact-server",
        }

        with patch(
            "backend.routers.tasks._get_repository_location_state",
            return_value=location_state,
        ), patch(
            "backend.routers.tasks._remove_repository_server_artifact",
        ) as remove_server_artifact, patch(
            "backend.routers.tasks._apply_repository_location_state",
        ), patch(
            "backend.routers.tasks._commit_repository_runtime_state_with_outbox",
        ) as commit_with_outbox:
            _cleanup_repository_artifacts_after_execution(
                db,
                repo,
                task,
                {"cleanup_server_artifact_after_execution": True},
            )

        remove_server_artifact.assert_called_once_with(
            "C:/pcids/artifacts/firmware.bin.pcenc",
            "artifact-server",
        )
        commit_with_outbox.assert_called_once_with(
            db,
            repo,
            current_user=None,
            source="task_artifact_cleanup",
        )
        self.assertFalse(json.loads(task.config_json)["cleanup_server_artifact_after_execution"])

    def test_server_source_keeps_server_storage(self):
        self.assertEqual(_resolve_artifact_storage_mode("server", "local", None), "server")

    def test_runtime_uses_location_state_local_path_when_file_url_missing(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pcenc") as artifact:
            artifact.write(b"encrypted")
            local_path = artifact.name
        self.addCleanup(lambda: os.path.exists(local_path) and os.remove(local_path))

        repo = SimpleNamespace(
            id=1,
            file_url=None,
            file_detail_json=json.dumps(
                {
                    "local_exists": True,
                    "local_path": local_path,
                    "storage_location": "local",
                    "storage_path": local_path,
                    "storage_target": "local",
                }
            ),
            download_uri="",
        )

        refreshed_repo, resolved_path = _ensure_repository_local_file_available_for_runtime(None, repo, None)

        self.assertIs(refreshed_repo, repo)
        self.assertEqual(resolved_path, local_path)

    def test_runtime_refreshes_local_cache_when_plaintext_checksum_mismatches(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pcenc") as artifact:
            artifact.write(b"stale-encrypted-cache")
            stale_path = artifact.name
        self.addCleanup(lambda: os.path.exists(stale_path) and os.remove(stale_path))

        repo = SimpleNamespace(
            id=11,
            project_key="proj_demo",
            name="BOOT.bin",
            file_url=stale_path,
            sha256="expected-sha256",
            md5="expected-md5",
            file_detail_json=json.dumps(
                {
                    "local_exists": True,
                    "local_path": stale_path,
                    "storage_location": "local",
                    "storage_path": stale_path,
                    "storage_target": "local",
                }
            ),
            download_uri="https://codearts.example/download/BOOT.bin",
        )
        refreshed_repo = SimpleNamespace(id=11, file_url="fresh-cache.pcenc")
        current_user = SimpleNamespace(id=7, username="tester")

        with patch(
            "backend.routers.tasks._compute_decrypted_artifact_hashes",
            return_value=("actual-md5", "actual-sha256", 123),
        ), patch(
            "backend.routers.tasks._refresh_repository_artifact_after_key_failure",
            return_value=refreshed_repo,
        ) as refresh_mock, patch(
            "backend.routers.tasks._resolve_existing_local_repository_artifact_path",
            side_effect=[stale_path, refreshed_repo.file_url],
        ):
            runtime_repo, resolved_path = _ensure_repository_local_file_available_for_runtime(
                None,
                repo,
                current_user,
                config={"install_source": "codearts"},
            )

        self.assertIs(runtime_repo, refreshed_repo)
        self.assertEqual(resolved_path, refreshed_repo.file_url)
        refresh_mock.assert_called_once_with(None, repo, current_user, stale_path)

    def test_agent_runtime_fetches_local_copy_from_server_storage_when_missing(self):
        server_only_repo = SimpleNamespace(
            id=2,
            file_url=None,
            file_detail_json=json.dumps(
                {
                    "server_exists": True,
                    "server_path": "/srv/pcids/firmware.pcenc",
                    "server_target": "192.168.0.12:22",
                    "storage_location": "server",
                    "storage_path": "/srv/pcids/firmware.pcenc",
                    "storage_target": "192.168.0.12:22",
                }
            ),
            download_uri="https://codearts.example/artifact",
        )
        refreshed_repo = SimpleNamespace(
            id=2,
            file_url=r"D:\workspace\pcids\uploads\repositories\firmware.pcenc",
            file_detail_json=json.dumps(
                {
                    "local_exists": True,
                    "local_path": r"D:\workspace\pcids\uploads\repositories\firmware.pcenc",
                    "server_exists": True,
                    "server_path": "/srv/pcids/firmware.pcenc",
                    "server_target": "192.168.0.12:22",
                    "storage_location": "both",
                    "storage_path": r"D:\workspace\pcids\uploads\repositories\firmware.pcenc",
                    "storage_target": "192.168.0.12:22",
                }
            ),
            download_uri="https://codearts.example/artifact",
        )
        burner = SimpleNamespace(id=9, host_type="agent", agent_url="http://192.168.0.88:8000")
        current_user = SimpleNamespace(id=7, username="tester")

        with patch(
            "backend.routers.tasks._download_repository_artifact_from_server_storage",
            return_value=refreshed_repo,
        ) as download_mock, patch(
            "backend.routers.tasks._resolve_existing_local_repository_artifact_path",
            side_effect=[None, refreshed_repo.file_url],
        ):
            runtime_repo, resolved_path = _ensure_repository_local_file_available_for_runtime(
                None,
                server_only_repo,
                current_user,
                burner=burner,
                config={"install_source": "codearts"},
            )

        self.assertIs(runtime_repo, refreshed_repo)
        self.assertEqual(resolved_path, refreshed_repo.file_url)
        download_mock.assert_called_once_with(None, server_only_repo)

    def test_key_mismatch_refresh_clears_bad_local_cache_and_redownloads(self):
        cache_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads", "repositories"))
        os.makedirs(cache_dir, exist_ok=True)
        fd, bad_path = tempfile.mkstemp(prefix="old-key-cache-", suffix=".pcenc", dir=cache_dir)
        with os.fdopen(fd, "wb") as artifact:
            artifact.write(b"old-key-cache")
        self.addCleanup(lambda: os.path.exists(bad_path) and os.remove(bad_path))

        repo = SimpleNamespace(
            id=3,
            project_key="proj_demo",
            name="BOOT.bin",
            file_url=bad_path,
            file_detail_json=json.dumps(
                {
                    "local_exists": True,
                    "local_path": bad_path,
                    "storage_location": "local",
                    "storage_path": bad_path,
                    "storage_target": "local",
                }
            ),
            download_uri="https://codearts.example/download/BOOT.bin",
        )
        refreshed_repo = SimpleNamespace(id=3, file_url="new-cache.pcenc")
        db = SimpleNamespace(add=lambda _repo: None, commit=lambda: None)
        current_user = SimpleNamespace(id=7, username="tester")

        with patch(
            "backend.routers.tasks._download_repository_artifact_to_local_storage",
            return_value=refreshed_repo,
        ) as download_mock:
            result = _refresh_repository_artifact_after_key_failure(db, repo, current_user, bad_path)

        self.assertIs(result, refreshed_repo)
        self.assertFalse(os.path.exists(bad_path))
        detail = json.loads(repo.file_detail_json)
        self.assertFalse(detail["local_exists"])
        self.assertIsNone(detail["local_path"])
        download_mock.assert_called_once_with(db, repo, current_user)


if __name__ == "__main__":
    unittest.main()
