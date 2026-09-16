import os
import tempfile
import unittest
from pathlib import Path

from backend.routers.repositories import _get_codearts_ca_bundle_path, _is_codearts_intranet_https_url, get_repository_download_config_summary


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

    def test_codearts_ca_bundle_uses_path_relative_to_external_yaml(self):
        certificate = self.config_path.parent / "codearts-ca.pem"
        certificate.write_text("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n", encoding="ascii")
        self.config_path.write_text("codearts_ca_bundle: codearts-ca.pem\n", encoding="utf-8")

        result = _get_codearts_ca_bundle_path("https://iam-apigateway-proxy.cqcloud.cwgy.com/v3/auth/tokens")

        self.assertEqual(result, certificate)

    def test_codearts_ca_bundle_is_not_used_for_other_hosts(self):
        self.config_path.write_text("codearts_ca_bundle: missing.pem\n", encoding="utf-8")

        self.assertIsNone(_get_codearts_ca_bundle_path("https://example.com/api"))

    def test_codearts_tls_verification_scope_is_limited_to_intranet_hosts(self):
        self.assertTrue(_is_codearts_intranet_https_url("https://iam-apigateway-proxy.cqcloud.cwgy.com/v3/auth/tokens"))
        self.assertFalse(_is_codearts_intranet_https_url("http://iam-apigateway-proxy.cqcloud.cwgy.com/v3/auth/tokens"))
        self.assertFalse(_is_codearts_intranet_https_url("https://iam.cn-north-4.myhuaweicloud.com/v3/auth/tokens"))


if __name__ == "__main__":
    unittest.main()
