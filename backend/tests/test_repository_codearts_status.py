import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from backend.routers.repositories import (
    _authenticate_codearts_download_config,
    _build_codearts_download_context_async,
    _get_project_codearts_sync_config,
    get_codearts_config,
    get_codearts_status,
)


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
            patch("backend.routers.repositories._build_codearts_download_context_async", return_value=(config, "token")),
            patch("backend.routers.repositories._get_codearts_project_list", return_value=[]),
        ):
            result = asyncio.run(get_codearts_status("proj_test", Mock(), Mock()))

        self.assertTrue(result["data"]["connected"])
        self.assertEqual(result["data"]["detail"], "")

    def test_auth_or_network_error_returns_offline_mode(self):
        with patch(
            "backend.routers.repositories._build_codearts_download_context_async",
            side_effect=HTTPException(status_code=502, detail="无法连接 CodeArts"),
        ):
            result = asyncio.run(get_codearts_status("proj_test", Mock(), Mock()))

        self.assertFalse(result["data"]["connected"])
        self.assertEqual(result["data"]["detail"], "无法连接 CodeArts")

    def test_config_response_does_not_expose_backend_devops_url(self):
        user = SimpleNamespace(id=1, username="admin")
        with patch(
            "backend.routers.repositories._get_project_codearts_config",
            return_value={
                "enabled": True,
                "devops_url": "https://trusted-devops.example.com",
                "password": "secret",
            },
        ):
            result = asyncio.run(get_codearts_config("proj_test", Mock(), user))

        self.assertNotIn("devops_url", result["data"])
        self.assertEqual(result["data"]["password"], "")
        self.assertTrue(result["data"]["password_present"])

    def test_async_context_keeps_database_session_out_of_worker_thread(self):
        db = Mock()
        user = SimpleNamespace(id=7, username="member")
        raw_config = {
            "enabled": True,
            "repository_mode": "release",
            "domain_name": "tenant",
            "username": "user",
            "password": "password",
            "region": "cn-east-3",
        }
        to_thread = AsyncMock(return_value="token")
        with (
            patch(
                "backend.routers.repositories._get_project_codearts_config_raw",
                return_value=raw_config,
            ) as get_raw,
            patch("backend.routers.repositories.asyncio.to_thread", to_thread),
        ):
            cfg, token = asyncio.run(
                _build_codearts_download_context_async(user, db, "proj_test")
            )

        get_raw.assert_called_once_with(db, "proj_test", user)
        self.assertEqual(token, "token")
        self.assertEqual(cfg["username"], "user")
        worker_args = to_thread.await_args.args
        self.assertIs(worker_args[0], _authenticate_codearts_download_config)
        self.assertIsInstance(worker_args[1], dict)
        self.assertIsNot(worker_args[1], db)


if __name__ == "__main__":
    unittest.main()
