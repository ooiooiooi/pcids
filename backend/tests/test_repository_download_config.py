import os
import tempfile
import unittest
from pathlib import Path

from backend.routers.repositories import get_repository_download_config_summary


class RepositoryDownloadConfigSummaryTests(unittest.TestCase):
    def setUp(self):
        self.original_env = os.environ.get("PCIDS_REPOSITORY_DOWNLOAD_CONFIG")
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "repository_download.yaml"
        os.environ["PCIDS_REPOSITORY_DOWNLOAD_CONFIG"] = str(self.config_path)

    def tearDown(self):
        if self.original_env is None:
            os.environ.pop("PCIDS_REPOSITORY_DOWNLOAD_CONFIG", None)
        else:
            os.environ["PCIDS_REPOSITORY_DOWNLOAD_CONFIG"] = self.original_env
        self.temp_dir.cleanup()

    def test_summary_reports_external_yaml_after_auto_create(self):
        self.assertFalse(self.config_path.exists())

        summary = get_repository_download_config_summary()

        self.assertTrue(self.config_path.exists())
        self.assertTrue(summary["external_config_exists"])
        self.assertEqual(summary["effective_source"], "external_yaml")
        self.assertEqual(summary["external_config_path"], str(self.config_path))

    def test_summary_uses_default_port_when_yaml_port_is_invalid(self):
        self.config_path.write_text(
            "server_transport: ssh\nserver_ssh_port: abc\n",
            encoding="utf-8",
        )

        summary = get_repository_download_config_summary()

        self.assertEqual(summary["server_transport"], "ssh")
        self.assertEqual(summary["server_port"], 22)


if __name__ == "__main__":
    unittest.main()
