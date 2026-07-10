import json
import tempfile
import unittest

from backend.routers.repositories import _apply_repository_location_state, _get_repository_location_state


class _Repo:
    source_type = "codearts_sync"
    file_url = None
    file_detail_json = None


class RepositoryLocationStateTests(unittest.TestCase):
    def test_adding_server_copy_preserves_existing_local_copy(self):
        repo = _Repo()
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = f"{temp_dir}/local.pcenc"
            with open(local_path, "wb") as fp:
                fp.write(b"encrypted")
            self._assert_server_copy_preserves_local(repo, local_path)

    def _assert_server_copy_preserves_local(self, repo, local_path):
        detail = {
            "local_exists": True,
            "local_path": local_path,
            "storage_location": "local",
            "storage_path": local_path,
            "storage_target": "local",
        }

        _apply_repository_location_state(
            repo,
            detail,
            local_exists=True,
            local_path=detail["local_path"],
            server_exists=True,
            server_path="/home/user/pcids-artifacts/remote.pcenc",
            server_target="192.168.0.117:22",
        )

        state = _get_repository_location_state(repo, json.loads(repo.file_detail_json))
        self.assertTrue(state["local_exists"])
        self.assertTrue(state["server_exists"])
        self.assertEqual(state["local_path"], detail["local_path"])
        self.assertEqual(state["server_path"], "/home/user/pcids-artifacts/remote.pcenc")
        self.assertEqual(state["storage_location"], "both")

    def test_current_host_server_copy_is_not_misclassified_as_local_only(self):
        repo = _Repo()

        _apply_repository_location_state(
            repo,
            {},
            local_exists=False,
            local_path=None,
            server_exists=True,
            server_path="D:/workspace/pcids/uploads/repositories/server/server.pcenc",
            server_target="local",
        )

        state = _get_repository_location_state(repo, json.loads(repo.file_detail_json))
        self.assertFalse(state["local_exists"])
        self.assertTrue(state["server_exists"])
        self.assertEqual(state["server_target"], "local")
        self.assertEqual(state["storage_location"], "server")

    def test_server_storage_does_not_infer_local_from_file_url(self):
        repo = _Repo()
        repo.file_url = "D:/workspace/pcids/uploads/repositories/server/server.pcenc"
        detail = {
            "storage_location": "server",
            "storage_path": "/home/user/pcids-artifacts/server.pcenc",
            "storage_target": "192.168.0.117:22",
            "server_exists": True,
            "server_path": "/home/user/pcids-artifacts/server.pcenc",
        }

        state = _get_repository_location_state(repo, detail)

        self.assertFalse(state["local_exists"])
        self.assertTrue(state["server_exists"])
        self.assertEqual(state["server_path"], "/home/user/pcids-artifacts/server.pcenc")


if __name__ == "__main__":
    unittest.main()
