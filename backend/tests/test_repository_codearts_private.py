import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import HTTPException

from backend.routers.repositories import (
    _build_codearts_private_download_url,
    _build_codearts_download_context,
    _build_local_tree,
    _build_effective_codearts_config,
    _get_private_iam_token_context,
    _list_codearts_private_repository_files,
    _merge_codearts_config,
    _open_remote_download_stream,
    _repository_codearts_mode,
    _resolve_codearts_devops_url,
    _resolve_codearts_download_auth,
    get_codearts_status,
)


REPO_ID = "cn-cq-1_bf7bbb8002b04002bd78a65557e7b7e4_generic_0"
PROJECT_ID = "cf8f1be184bd4eb484b79139484b673a"
IAM_PROJECT_ID = "950354fc351247aa8c71adc95c9f73d2"
TENANT_ID = "bf7bbb8002b04002bd78a65557e7b7e4"
REPOSITORY_URL = f"https://devrepo.example.com/artgalaxy/{REPO_ID}/"


class RepositoryCodeartsPrivateTests(unittest.TestCase):
    def test_private_project_tree_node_exposes_repository_mode(self):
        repository = SimpleNamespace(
            id=1,
            source_type="codearts_sync",
            project_key=f"proj_{PROJECT_ID}",
            name="a.exe",
            repo_id="1",
            remote_repo_id=REPO_ID,
            file_detail_json=json.dumps({"download_url": "https://example.com/a.exe"}),
            repo_detail_json=json.dumps({"name": "private-project", "repository_mode": "private"}),
            file_url=None,
            size=100,
            version=None,
            md5=None,
            sha256=None,
            download_count=0,
            last_download_time=None,
            download_uri="https://example.com/a.exe",
            display_path="/a.exe",
        )

        tree = _build_local_tree([repository])

        self.assertEqual(tree[0]["repository_mode"], "private")

    def test_private_service_endpoints_do_not_replace_release_endpoints(self):
        defaults = {
            "iam_token_url": "https://release-iam.example/{region}",
            "base_url": "https://release-api.example/{region}",
            "private_iam_token_url": "https://private-iam.example/v3/auth/tokens",
            "private_base_url": "https://private-api.example/{region}",
        }
        with patch("backend.routers.repositories._get_repository_codearts_service_config", return_value=defaults):
            release = _build_effective_codearts_config({"repository_mode": "release"})
            private = _build_effective_codearts_config({"repository_mode": "private"})

        self.assertEqual(release["iam_token_url"], defaults["iam_token_url"])
        self.assertEqual(release["base_url"], defaults["base_url"])
        self.assertEqual(private["iam_token_url"], defaults["private_iam_token_url"])
        self.assertEqual(private["base_url"], defaults["private_base_url"])

    def test_private_download_context_scopes_token_to_configured_project_id(self):
        config = {
            "enabled": True,
            "repository_mode": "private",
            "domain_name": "CWGY",
            "username": "cwgy-user",
            "password": "password",
            "region": "cn-cq-1",
            "project_id": IAM_PROJECT_ID,
            "private_iam_token_url": "https://iam.example.com/v3/auth/tokens",
        }
        with patch(
            "backend.routers.repositories._get_project_codearts_config_raw",
            return_value=config,
        ), patch(
            "backend.routers.repositories._get_private_iam_token_context",
            return_value=("private-token", TENANT_ID),
        ) as private_token, patch("backend.routers.repositories._get_iam_token") as release_token:
            result_config, token = _build_codearts_download_context(Mock(), Mock(), "proj_private")

        self.assertEqual(token, "private-token")
        self.assertEqual(result_config["repository_mode"], "private")
        private_token.assert_called_once_with(
            "CWGY",
            "cwgy-user",
            "password",
            "cn-cq-1",
            IAM_PROJECT_ID,
            iam_token_url="https://iam.example.com/v3/auth/tokens",
        )
        release_token.assert_not_called()

    def test_private_artifact_override_recomputes_private_service_endpoints_after_mode_switch(self):
        config = {
            "enabled": True,
            "repository_mode": "release",
            "domain_name": "CWGY",
            "username": "cwgy-user",
            "password": "password",
            "region": "cn-cq-1",
            "project_id": IAM_PROJECT_ID,
            "iam_token_url": "https://release-iam.example/v3/auth/tokens",
            "base_url": "https://release-api.example.com",
            "private_iam_token_url": "https://private-iam.example/v3/auth/tokens",
            "private_base_url": "https://private-api.example.com",
        }
        with patch(
            "backend.routers.repositories._get_project_codearts_config_raw",
            return_value=config,
        ), patch(
            "backend.routers.repositories._get_private_iam_token_context",
            return_value=("private-token", TENANT_ID),
        ):
            result_config, token = _build_codearts_download_context(
                Mock(), Mock(), "proj_private", repository_mode="private"
            )

        self.assertEqual(token, "private-token")
        self.assertEqual(result_config["repository_mode"], "private")
        self.assertEqual(result_config["iam_token_url"], "https://private-iam.example/v3/auth/tokens")
        self.assertEqual(result_config["base_url"], "https://private-api.example.com")

    def test_private_token_context_uses_project_id_and_reads_tenant_id(self):
        response = MagicMock()
        response.headers = {"X-Subject-Token": "private-token"}
        response.read.return_value = json.dumps(
            {
                "token": {
                    "project": {"id": IAM_PROJECT_ID, "domain": {"id": TENANT_ID}},
                    "user": {"domain": {"id": TENANT_ID}},
                }
            }
        ).encode("utf-8")
        response.__enter__.return_value = response

        with patch("backend.routers.repositories._urlopen", return_value=response) as urlopen:
            token, tenant_id = _get_private_iam_token_context(
                "CWGY",
                "cwgy-user",
                "password",
                "cn-cq-1",
                IAM_PROJECT_ID,
                iam_token_url="https://iam.example.com/v3/auth/tokens",
            )

        request = urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(token, "private-token")
        self.assertEqual(tenant_id, TENANT_ID)
        self.assertEqual(request_payload["auth"]["scope"]["project"], {"id": IAM_PROJECT_ID})

    def test_effective_config_keeps_private_repo_id_separate_and_derives_tenant(self):
        config = _build_effective_codearts_config(
            {
                "repository_mode": "private",
                "repo_ids": ["release-repo-id"],
                "private_repo_id": REPO_ID,
                "private_repository_url": "https://devrepo.example.com",
            }
        )

        self.assertEqual(config["repository_mode"], "private")
        self.assertEqual(config["repo_ids"], ["release-repo-id"])
        self.assertEqual(config["private_repo_id"], REPO_ID)
        self.assertEqual(config["tenant_id"], TENANT_ID)

    def test_legacy_private_config_reads_repo_id_without_changing_release_storage(self):
        config = _build_effective_codearts_config(
            {"repository_mode": "private", "repo_ids": [REPO_ID]}
        )

        self.assertEqual(config["private_repo_id"], REPO_ID)
        self.assertEqual(config["repo_ids"], [REPO_ID])

    def test_private_config_merge_does_not_overwrite_release_repo_ids(self):
        merged = _merge_codearts_config(
            {"repo_ids": ["release-repo-id"], "private_repo_id": "old-private-id"},
            {"repository_mode": "private", "private_repo_id": REPO_ID},
        )

        self.assertEqual(merged["repo_ids"], ["release-repo-id"])
        self.assertEqual(merged["private_repo_id"], REPO_ID)

    def test_devops_url_is_only_read_from_trusted_backend_config(self):
        trusted_url = "https://trusted-devops.example.com"
        merged = _merge_codearts_config(
            {"devops_url": "https://legacy-db.example.com"},
            {"devops_url": "https://payload.example.com"},
        )

        self.assertNotIn("devops_url", merged)
        with patch(
            "backend.routers.repositories._get_repository_codearts_service_config",
            return_value={
                "iam_token_url": "https://iam.example.com",
                "base_url": "https://release.example.com",
                "private_iam_token_url": "https://private-iam.example.com",
                "private_base_url": "https://private.example.com",
                "devops_url": trusted_url,
            },
        ):
            effective = _build_effective_codearts_config(
                {
                    "repository_mode": "private",
                    "private_source": "web",
                    "devops_url": "https://legacy-db.example.com",
                }
            )

        self.assertEqual(effective["devops_url"], trusted_url)

    def test_devops_url_template_rejects_project_controlled_origin_injection(self):
        with self.assertRaises(HTTPException) as context:
            _resolve_codearts_devops_url(
                {
                    "devops_url": "https://devops.{region}.example.com",
                    "region": "attacker.example/path",
                }
            )

        self.assertEqual(context.exception.status_code, 400)

    def test_legacy_private_repo_id_survives_switch_to_release(self):
        merged = _merge_codearts_config(
            {"repository_mode": "private", "repo_ids": [REPO_ID]},
            {"repository_mode": "release"},
        )

        self.assertEqual(merged["repository_mode"], "release")
        self.assertEqual(merged["private_repo_id"], REPO_ID)
        self.assertEqual(merged["repo_ids"], [])

    def test_effective_config_does_not_take_repo_id_from_repository_url(self):
        config = _build_effective_codearts_config(
            {
                "repository_mode": "private",
                "private_repository_url": REPOSITORY_URL,
            }
        )

        self.assertFalse(config.get("repo_ids"))
        self.assertFalse(config.get("tenant_id"))

    def test_private_download_url_uses_configured_repo_id(self):
        value = _build_codearts_private_download_url(
            "https://devrepo.example.com",
            REPO_ID,
            "/folder/a file.exe",
        )

        self.assertEqual(
            value,
            f"https://devrepo.example.com/artgalaxy/{REPO_ID}/folder/a%20file.exe",
        )

    def test_private_repository_walk_uses_file_tree_and_file_detail(self):
        requested_urls = []

        def fake_get(url, token=None, timeout_seconds=10, retries=3):
            requested_urls.append(url)
            if url.endswith(f"/repositories/{REPO_ID}"):
                return {
                    "status": "success",
                    "result": {
                        "repositoryName": "测试",
                        "format": "generic",
                        "projectId": PROJECT_ID,
                        "url": REPOSITORY_URL,
                    },
                }
            if "file-tree" in url and "path=%2Ffolder" in url:
                return {
                    "status": "success",
                    "result": {
                        "children": [
                            {
                                "name": "a file.exe",
                                "path": f"{REPO_ID}/folder/a file.exe",
                                "folder": "false",
                                "display_size": "3.44 MB",
                                "modified": "2026-07-13 15:04:33",
                            }
                        ]
                    },
                }
            if "file-tree" in url:
                return {
                    "status": "success",
                    "result": {
                        "children": [
                            {"name": "folder", "path": f"{REPO_ID}/folder", "folder": "true"}
                        ]
                    },
                }
            if "file-detail" in url:
                return {
                    "status": "success",
                    "result": {
                        "name": "a file.exe",
                        "path": "/folder/a file.exe",
                        "size": "100",
                        "downloadUri": "https://internal.invalid/artgalaxy/file",
                        "checksums": {"md5": "abc", "sha256": "def"},
                    },
                }
            raise AssertionError(url)

        with patch("backend.routers.repositories._http_get_json", side_effect=fake_get):
            files = _list_codearts_private_repository_files(
                base_url="https://codearts.example.com",
                private_repository_url="",
                token="token",
                tenant_id=TENANT_ID,
                project_id=IAM_PROJECT_ID,
                repo_id=REPO_ID,
            )

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["repo_detail"]["project_id"], PROJECT_ID)
        self.assertEqual(files[0]["repo_detail"]["iam_project_id"], IAM_PROJECT_ID)
        self.assertEqual(files[0]["project_name"], "测试")
        self.assertEqual(files[0]["display_path"], "/folder/a file.exe")
        self.assertEqual(
            files[0]["download_uri"],
            f"https://devrepo.example.com/artgalaxy/{REPO_ID}/folder/a%20file.exe",
        )
        self.assertEqual(files[0]["file_detail"]["checksums"]["sha256"], "def")
        self.assertEqual(files[0]["file_detail"]["version"], "2026-07-13 15:04:33")
        self.assertTrue(any("path=%2Ffolder" in url for url in requested_urls))

    def test_download_auth_keeps_release_token_and_private_uses_basic(self):
        release_auth = _resolve_codearts_download_auth(
            {"repository_mode": "release"}, "https://codearts.example.com", "release-token"
        )
        self.assertEqual(release_auth["mode"], "token")
        self.assertEqual(release_auth["token"], "release-token")

        with patch(
            "backend.routers.repositories._get_codearts_private_download_credentials",
            return_value=("repo-user", "repo-password"),
        ):
            private_auth = _resolve_codearts_download_auth(
                {"repository_mode": "private"}, "https://codearts.example.com", "iam-token"
            )

        self.assertEqual(private_auth["mode"], "basic")
        self.assertIsNone(private_auth["token"])
        self.assertEqual(private_auth["username"], "repo-user")
        self.assertEqual(private_auth["password"], "repo-password")

    def test_private_download_stream_sends_basic_authorization(self):
        response = Mock()
        with patch("backend.routers.repositories._urlopen", return_value=response) as urlopen:
            result = _open_remote_download_stream(
                "https://devrepo.example.com/artgalaxy/repo/file.exe",
                token="must-not-be-used",
                username="repo-user",
                password="repo-password",
            )

        request = urlopen.call_args.args[0]
        expected = base64.b64encode(b"repo-user:repo-password").decode("ascii")
        self.assertIs(result, response)
        self.assertEqual(request.get_header("Authorization"), f"Basic {expected}")
        self.assertIsNone(request.get_header("X-auth-token"))

    def test_private_status_does_not_call_release_project_list(self):
        config = {
            "repository_mode": "private",
            "region": "cn-cq-1",
            "base_url": "https://codearts.example.com",
            "private_repo_id": REPO_ID,
        }
        with (
            patch("backend.routers.repositories._build_codearts_download_context_async", return_value=(config, "token")),
            patch("backend.routers.repositories._get_codearts_private_repository_info", return_value={"name": REPO_ID}) as private_info,
            patch("backend.routers.repositories._get_codearts_project_list") as release_list,
        ):
            result = asyncio.run(get_codearts_status("proj_test", Mock(), Mock()))

        self.assertTrue(result["data"]["connected"])
        private_info.assert_called_once()
        release_list.assert_not_called()

    def test_repository_snapshot_preserves_private_download_mode(self):
        repo = Mock(repo_detail_json=json.dumps({"repository_mode": "private"}))
        self.assertEqual(_repository_codearts_mode(repo), "private")


if __name__ == "__main__":
    unittest.main()
