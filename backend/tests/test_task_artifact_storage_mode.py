import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.routers.tasks import (
    _ensure_repository_local_file_available_for_runtime,
    _refresh_repository_artifact_after_key_failure,
    _resolve_artifact_storage_mode,
)


class TaskArtifactStorageModeTests(unittest.TestCase):
    def test_codearts_uses_local_storage_for_local_burner(self):
        self.assertEqual(_resolve_artifact_storage_mode("codearts", "local", None), "local")

    def test_codearts_uses_server_storage_for_agent_burner(self):
        self.assertEqual(_resolve_artifact_storage_mode("codearts", "agent", "http://192.168.1.20:8000"), "server")

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
