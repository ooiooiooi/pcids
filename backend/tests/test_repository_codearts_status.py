import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from backend.routers.repositories import _get_project_codearts_sync_config, get_codearts_status


class RepositoryCodeartsStatusTests(unittest.TestCase):
    def test_sync_uses_saved_project_config_instead_of_request_values(self):
        stored = {
            "enabled": True,
            "domain_name": "saved-domain",
            "username": "saved-user",
            "password": "saved-password",
            "region": "cn-north-4",
            "project_id": "project-b",
            "repo_ids": ["saved-repo"],
        }
        with patch("backend.routers.repositories._get_project_codearts_config_raw", return_value=stored):
            result = _get_project_codearts_sync_config(Mock(), "project-b", Mock())

        self.assertEqual(result["username"], "saved-user")
        self.assertEqual(result["region"], "cn-north-4")
        self.assertEqual(result["repo_ids"], ["saved-repo"])

    def test_sync_rejects_project_without_saved_config(self):
        with patch("backend.routers.repositories._get_project_codearts_config_raw", return_value={}):
            with self.assertRaisesRegex(HTTPException, "尚未保存 CodeArts 配置"):
                _get_project_codearts_sync_config(Mock(), "project-b", Mock())

    def test_connected_requires_successful_codearts_request(self):
        config = {
            "region": "cn-east-3",
            "base_url": "https://cloudartifacts-ext.{region}.myhuaweicloud.com",
        }

        with (
            patch("backend.routers.repositories._build_codearts_download_context", return_value=(config, "token")),
            patch("backend.routers.repositories._get_codearts_project_list", return_value=[]),
        ):
            result = asyncio.run(get_codearts_status("proj_test", Mock(), Mock()))

        self.assertTrue(result["data"]["connected"])
        self.assertEqual(result["data"]["detail"], "")

    def test_auth_or_network_error_returns_offline_mode(self):
        with patch(
            "backend.routers.repositories._build_codearts_download_context",
            side_effect=HTTPException(status_code=502, detail="无法连接 CodeArts"),
        ):
            result = asyncio.run(get_codearts_status("proj_test", Mock(), Mock()))

        self.assertFalse(result["data"]["connected"])
        self.assertEqual(result["data"]["detail"], "无法连接 CodeArts")


if __name__ == "__main__":
    unittest.main()
