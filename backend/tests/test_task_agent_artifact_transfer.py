import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.routers.tasks import _execute_script_via_agent


class TaskAgentArtifactTransferTests(unittest.TestCase):
    def test_remote_burn_stages_artifact_and_rewrites_runtime_path(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".hex") as artifact:
            artifact.write(b"firmware")
            artifact_path = artifact.name
        self.addCleanup(lambda: os.path.exists(artifact_path) and os.remove(artifact_path))
        captured = {}

        def fake_post(_url, payload, timeout_seconds=10):
            captured.update(payload)
            return {"code": 0, "data": {"success": True, "log": "ok", "failure_reason": ""}}

        with (
            patch(
                "backend.routers.tasks._http_upload_file",
                return_value={"code": 0, "data": {"path": r"C:\Temp\pcids_agent_artifacts\run\firmware.hex"}},
            ),
            patch("backend.routers.tasks._http_post_json", side_effect=fake_post),
        ):
            success, log_text, failure_reason = asyncio.run(
                _execute_script_via_agent(
                    "http://192.168.1.20:8000",
                    "burn",
                    "echo burn",
                    "bat",
                    {"FIRMWARE_PATH": artifact_path, "REPOSITORY_FILE_URL": artifact_path},
                    30,
                    artifact_path=artifact_path,
                )
            )

        remote_path = r"C:\Temp\pcids_agent_artifacts\run\firmware.hex"
        self.assertTrue(success)
        self.assertEqual(log_text, "ok")
        self.assertEqual(failure_reason, "")
        self.assertEqual(captured["env"]["FIRMWARE_PATH"], remote_path)
        self.assertEqual(captured["env"]["REPOSITORY_FILE_URL"], remote_path)
        self.assertEqual(captured["cleanup_artifact_path"], remote_path)


if __name__ == "__main__":
    unittest.main()
