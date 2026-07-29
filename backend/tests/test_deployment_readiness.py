import base64
import os
import unittest
from unittest.mock import patch

from backend.utils.deployment_readiness import _build_check, build_windows_deployment_readiness
from backend.utils.key_management import ARTIFACT_MASTER_KEY_ENV, reset_artifact_master_key_cache


def _encode_key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(seed).decode("ascii").rstrip("=")


class DeploymentReadinessTests(unittest.TestCase):
    def tearDown(self):
        reset_artifact_master_key_cache()

    def test_readiness_is_ready_on_supported_windows_with_valid_key(self):
        with patch.dict(os.environ, {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"3" * 32)}, clear=False):
            with patch("backend.utils.deployment_readiness.platform.system", return_value="Windows"):
                with patch("backend.utils.deployment_readiness.platform.release", return_value="11"):
                    with patch("backend.utils.deployment_readiness.platform.version", return_value="10.0.22631"):
                        readiness = build_windows_deployment_readiness()

        self.assertTrue(readiness["overall_ready"])
        self.assertEqual(readiness["blocking_issue_count"], 0)
        master_key_check = next(item for item in readiness["checks"] if item["key"] == "artifact_master_key")
        self.assertEqual(master_key_check["status"], "ok")

    def test_invalid_master_key_marks_environment_not_ready(self):
        with patch.dict(os.environ, {ARTIFACT_MASTER_KEY_ENV: "bad-key"}, clear=False):
            with patch("backend.utils.deployment_readiness.platform.system", return_value="Windows"):
                with patch("backend.utils.deployment_readiness.platform.release", return_value="10"):
                    with patch("backend.utils.deployment_readiness.platform.version", return_value="10.0.19045"):
                        readiness = build_windows_deployment_readiness()

        self.assertFalse(readiness["overall_ready"])
        self.assertGreaterEqual(readiness["blocking_issue_count"], 1)
        master_key_check = next(item for item in readiness["checks"] if item["key"] == "artifact_master_key")
        self.assertEqual(master_key_check["status"], "error")
        self.assertTrue(master_key_check["blocking"])

    def test_future_windows_release_is_warning_not_blocking(self):
        ok_check = _build_check("probe", "ok", "ok")
        with patch.dict(os.environ, {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"4" * 32)}, clear=False):
            with patch("backend.utils.deployment_readiness.platform.system", return_value="Windows"):
                with patch("backend.utils.deployment_readiness.platform.release", return_value="12"):
                    with patch("backend.utils.deployment_readiness.platform.version", return_value="10.0.future"):
                        with patch("backend.utils.deployment_readiness._check_upload_root", return_value=ok_check):
                            with patch("backend.utils.deployment_readiness._check_temp_runtime_dir", return_value=ok_check):
                                with patch("backend.utils.deployment_readiness._check_secure_data_dir", return_value=ok_check):
                                    readiness = build_windows_deployment_readiness()

        self.assertTrue(readiness["overall_ready"])
        windows_check = next(item for item in readiness["checks"] if item["key"] == "windows_version")
        self.assertEqual(windows_check["status"], "warn")

    def test_windows_7_web_test_package_is_supported(self):
        ok_check = _build_check("probe", "ok", "ok")
        with patch.dict(os.environ, {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"7" * 32)}, clear=False):
            with patch("backend.utils.deployment_readiness.platform.system", return_value="Windows"):
                with patch("backend.utils.deployment_readiness.platform.release", return_value="7"):
                    with patch("backend.utils.deployment_readiness.platform.version", return_value="6.1.7601"):
                        with patch("backend.utils.deployment_readiness._check_upload_root", return_value=ok_check):
                            with patch("backend.utils.deployment_readiness._check_temp_runtime_dir", return_value=ok_check):
                                with patch("backend.utils.deployment_readiness._check_secure_data_dir", return_value=ok_check):
                                    readiness = build_windows_deployment_readiness()

        self.assertTrue(readiness["overall_ready"])
        windows_check = next(item for item in readiness["checks"] if item["key"] == "windows_version")
        self.assertEqual(windows_check["status"], "ok")

    def test_missing_burner_tools_are_reported_as_warnings(self):
        ok_check = _build_check("probe", "ok", "ok")
        burner_tools = [
            {"burner": "ST-LINK", "status": "ok", "message": "ready", "tool_label": "STM32 ST-LINK Utility CLI", "configured_path": "C:/tools/st.exe", "bundled_dir": "C:/tools/ST-LINK", "bundled_dir_exists": True, "env_names": ["STLINK_UTILITY_CLI"]},
            {"burner": "J-LINK", "status": "warn", "message": "docs only", "tool_label": "SEGGER J-Link CLI", "configured_path": "", "bundled_dir": "C:/tools/J-LINK", "bundled_dir_exists": True, "env_names": ["JLINK_EXE"]},
        ]
        with patch.dict(os.environ, {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"5" * 32)}, clear=False):
            with patch("backend.utils.deployment_readiness.platform.system", return_value="Windows"):
                with patch("backend.utils.deployment_readiness.platform.release", return_value="11"):
                    with patch("backend.utils.deployment_readiness.platform.version", return_value="10.0.22631"):
                        with patch("backend.utils.deployment_readiness._check_upload_root", return_value=ok_check):
                            with patch("backend.utils.deployment_readiness._check_temp_runtime_dir", return_value=ok_check):
                                with patch("backend.utils.deployment_readiness._check_secure_data_dir", return_value=ok_check):
                                    with patch("backend.utils.deployment_readiness.build_burner_tool_readiness", return_value=burner_tools):
                                        readiness = build_windows_deployment_readiness()

        burner_check = next(item for item in readiness["checks"] if item["key"] == "burner_tool_j_link")
        self.assertEqual(burner_check["status"], "warn")
        self.assertFalse(burner_check["blocking"])


if __name__ == "__main__":
    unittest.main()
