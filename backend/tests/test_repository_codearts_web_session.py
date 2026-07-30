import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from starlette.responses import FileResponse

from backend.routers.repositories import (
    _build_local_tree,
    _codearts_web_click_target,
    _codearts_web_download_candidates,
    _codearts_web_child_env,
    _codearts_web_runtime_script,
    _codearts_web_snapshot_status,
    _encrypt_codearts_web_download,
    _list_codearts_web_private_files,
    _normalize_codearts_web_display_text,
    _sanitize_codearts_web_diagnostics,
    download_codearts_artifact_to_local,
    download_codearts_artifact_to_server,
)


class CodeartsWebSessionAdapterTests(unittest.TestCase):
    def test_runtime_prefers_desktop_electron_node_for_windows_7(self):
        with TemporaryDirectory() as temp:
            runtime = Path(temp) / "runtime"
            bundled_node = runtime / "node_modules" / "node" / "bin" / (
                "node.exe" if __import__("os").name == "nt" else "node"
            )
            bundled_node.parent.mkdir(parents=True)
            bundled_node.write_bytes(b"bundled")
            (runtime / "codearts_web_session.js").write_text("// test", encoding="utf-8")
            desktop_node = Path(temp) / "pcids-desktop.exe"
            desktop_node.write_bytes(b"electron")

            with patch.dict(
                "os.environ",
                {
                    "PCIDS_CODEARTS_WEB_RUNTIME": str(runtime),
                    "PCIDS_NODE_BIN": str(desktop_node),
                    "ELECTRON_RUN_AS_NODE": "1",
                },
                clear=False,
            ):
                node, script, selected_runtime = _codearts_web_runtime_script()
                env = _codearts_web_child_env(selected_runtime)

        self.assertEqual(node, desktop_node)
        self.assertEqual(script.name, "codearts_web_session.js")
        self.assertEqual(env["ELECTRON_RUN_AS_NODE"], "1")
        self.assertEqual(env["NODE_SKIP_PLATFORM_CHECK"], "1")
        self.assertEqual(env["NODE_PATH"], str(runtime / "node_modules"))

    def test_runtime_uses_project_scoped_template_session_and_full_recursion(self):
        script = (Path(__file__).resolve().parents[2] / "tools" / "codearts_release_debugger" / "browser_runtime" / "codearts_web_session.js")
        source = script.read_text(encoding="utf-8")
        self.assertIn("isRootFilesListPayload(payload, config.projectId)", source)
        self.assertIn("if (capturedListTemplate) return", source)
        self.assertIn("delete capturedPayload.parentId", source)
        self.assertIn("snapshotComplete", source)
        self.assertIn("type || '').toLowerCase() === 'project'", source)
        self.assertIn("const templateCftk = capturedListTemplate.headers.cftk", source)
        self.assertIn("rawHeaders: headers", source)
        self.assertIn("context.request.fetch(url, options)", source)
        self.assertNotIn("page.evaluate(", source)
        self.assertIn("delete headers['content-length']", source)
        self.assertIn("delete headers.host", source)
        self.assertIn("session_cookie_names", source)
        self.assertIn("csrf_header_fingerprint", source)
        self.assertIn("safeResponseHeaders", source)
        self.assertIn("html_title", source)
        self.assertIn("payload.parentId = parentId", source)
        self.assertIn("pageNo <= totalPages", source)
        self.assertIn("bundled Playwright Chromium", source)
        self.assertIn("'Microsoft', 'Edge', 'Application', 'msedge.exe'", source)
        self.assertIn("PCIDS_BROWSER_EXECUTABLE", source)
        self.assertLess(
            source.index("'Google', 'Chrome', 'Application', 'chrome.exe'"),
            source.index("{ name: 'bundled Playwright Chromium'"),
        )
        self.assertNotIn("未找到 Google Chrome，请先安装 Chrome", source)
        self.assertIn("config.downloadTarget && config.downloadOutputPath", source)
        self.assertIn("folderSegments", source)
        self.assertIn("下载地址", source)
        self.assertIn("expandRepositoryFolder", source)
        self.assertIn("selectRepositoryFile", source)
        self.assertIn("findDownloadAddressLink", source)
        self.assertNotIn("context.request.fetch(config.downloadUrl", source)
        self.assertNotIn("page.goto(config.downloadUrl", source)
        self.assertIn("projectMetadataFromResponse", source)
        self.assertIn("page_breadcrumb", source)
        self.assertIn("files_list_project", source)
        self.assertIn("page.waitForEvent('download'", source)
        self.assertIn("nativeDownload.saveAs(config.downloadOutputPath)", source)

    def test_partial_web_snapshot_is_not_safe_for_full_refresh(self):
        complete, reasons = _codearts_web_snapshot_status(
            {
                "summary": {
                    "snapshotComplete": False,
                    "directoryErrors": [{"path": "/firmware"}],
                    "detailErrors": [],
                }
            }
        )

        self.assertFalse(complete)
        self.assertIn("browser_runtime_marked_snapshot_incomplete", reasons)
        self.assertIn("directory_errors=1", reasons)

    def test_normalizes_web_details_and_retains_directory_metadata(self):
        response = {
            "response": {"ok": True, "body": {"result": {"data": [
                {"id": "dir", "name": "empty", "type": "folder", "_list": {"id": "dir", "name": "empty", "type": "folder"}},
                {"id": "file", "name": "boot.bin", "type": "file", "_detail": {
                    "name": "boot.bin", "repoFilePath": "firmware/boot.bin", "size": 12,
                    "md5": "a" * 32, "sha256": "b" * 64,
                    "metadata": {
                        "versionName": "latest",
                        "created": "2026/07/28 16:45:04 GMT+08:00",
                        "modified": "2026/07/28 16:48:06 GMT+08:00",
                    },
                    "downloadUrl": "https://example/download", "downloadUrlWithId": "https://example/download?id=file",
                }},
            ]}}},
            "project": {"id": "p", "name": "远端项目名称", "source": "files_list_project"},
            "summary": {"folderCount": 1, "fileCount": 1}, "requestRecords": [{"url": "https://example/list"}],
        }
        with TemporaryDirectory() as temp:
            result_path = Path(temp) / "result.json"
            def fake_run(args, **kwargs):
                result_path.write_text(json.dumps(response), encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stderr": ""})()
            with patch("backend.routers.repositories.tempfile.TemporaryDirectory") as directory, patch("backend.routers.repositories.subprocess.run", side_effect=fake_run):
                directory.return_value.__enter__.return_value = temp
                directory.return_value.__exit__.return_value = False
                files, meta = _list_codearts_web_private_files({"project_id": "p", "devops_url": "https://devops.example"})
        self.assertEqual(files[0]["display_path"], "/firmware/boot.bin")
        self.assertEqual(files[0]["download_uri"], "https://example/download?id=file")
        self.assertEqual(files[0]["file_detail"]["md5"], "a" * 32)
        self.assertEqual(files[0]["project_name"], "远端项目名称")
        self.assertEqual(files[0]["file_detail"]["version"], "latest")
        self.assertEqual(files[0]["file_detail"]["created_time"], "2026/07/28 16:45:04 GMT+08:00")
        self.assertEqual(files[0]["file_detail"]["modified_time"], "2026/07/28 16:48:06 GMT+08:00")
        self.assertEqual(
            files[0]["file_detail"]["web_click_target"]["folderSegments"],
            ["firmware"],
        )
        self.assertEqual(meta["remote_project"]["name"], "远端项目名称")
        self.assertEqual(meta["folders"][0]["name"], "empty")

    def test_repairs_percent_encoded_and_latin1_mojibake_web_names(self):
        broken = "烧录固件.bin".encode("utf-8").decode("latin1")
        self.assertEqual(
            _normalize_codearts_web_display_text("%E4%B8%AD%E6%96%87/%E5%9B%BA%E4%BB%B6.bin"),
            "中文/固件.bin",
        )
        self.assertEqual(_normalize_codearts_web_display_text(broken), "烧录固件.bin")

    def test_download_candidates_reject_null_and_keep_fallback_url(self):
        candidates = _codearts_web_download_candidates(
            "https://devops.example.com/requested?id=3",
            {
                "download_url_with_id": "null",
                "download_url": "/artifact/download?id=2&amp;type=file",
            },
            base_url="https://devops.example.com",
        )
        self.assertEqual(
            candidates,
            [
                "https://devops.example.com/artifact/download?id=2&type=file",
                "https://devops.example.com/requested?id=3",
            ],
        )

    def test_click_target_uses_synced_directory_hierarchy_and_file_identity(self):
        repo = type(
            "Repository",
            (),
            {
                "name": "BOOT_with_bit.bin",
                "display_path": "/鸿蒙/AL321/BOOT_with_bit.bin",
            },
        )()
        target = _codearts_web_click_target(
            repo,
            {"id": "file-123", "parentId": "folder-321"},
        )
        self.assertEqual(target["artifactName"], "BOOT_with_bit.bin")
        self.assertEqual(target["folderSegments"], ["鸿蒙", "AL321"])
        self.assertEqual(target["artifactId"], "file-123")
        self.assertEqual(target["parentId"], "folder-321")

    def test_diagnostic_persistence_redacts_credential_values(self):
        safe = _sanitize_codearts_web_diagnostics({
            "cookie": "secret-cookie", "cftk": "secret-csrf", "authorization": "Bearer secret",
            "url": "https://devops.example/download?id=file&signature=secret",
            "request_debug": {"session_cookie_names": ["SESSION"], "csrf_header_fingerprint": "a1b2c3d4e5f6"},
        })
        self.assertEqual(safe["cookie"], "***")
        self.assertEqual(safe["cftk"], "***")
        self.assertEqual(safe["authorization"], "***")
        self.assertEqual(safe["url"], "https://devops.example/download")
        self.assertEqual(safe["request_debug"]["session_cookie_names"], ["SESSION"])

    def test_configured_web_project_remains_visible_before_first_file_sync(self):
        tree = _build_local_tree(
            [],
            project_configs=[
                {
                    "enabled": True,
                    "repository_mode": "private",
                    "private_source": "web",
                    "project_id": "web-project",
                    "project_name": "Web Project",
                    "region": "cn-cq-1",
                    "devops_url": "https://devops.example.com",
                    "web_folder_paths": ["/empty/nested"],
                }
            ],
        )

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["key"], "proj_web-project")
        self.assertEqual(tree[0]["repository_mode"], "private")
        self.assertEqual(tree[0]["private_source"], "web")
        self.assertEqual(tree[0]["children"][0]["title"], "empty")
        self.assertEqual(tree[0]["children"][0]["children"][0]["title"], "nested")

    def test_web_download_runtime_output_enters_encrypted_storage_pipeline(self):
        stored_result = type(
            "StoredArtifact",
            (),
            {"plaintext_size": 8, "md5": "m", "sha256": "s"},
        )()
        with TemporaryDirectory() as temp:
            def fake_run(args, **kwargs):
                config = json.loads(Path(args[2]).read_text(encoding="utf-8"))
                self.assertEqual(config["downloadTarget"]["artifactName"], "firmware.bin")
                self.assertEqual(config["downloadTarget"]["folderSegments"], ["firmware"])
                Path(config["downloadOutputPath"]).write_bytes(b"firmware")
                Path(args[3]).write_text(
                    json.dumps({
                        "response": {"ok": True, "status": 200},
                        "requestRecords": [{"interface": "GET webpage download"}],
                    }),
                    encoding="utf-8",
                )
                return type("Result", (), {"returncode": 0, "stderr": ""})()

            with (
                patch("backend.routers.repositories.tempfile.TemporaryDirectory") as directory,
                patch("backend.routers.repositories.subprocess.run", side_effect=fake_run),
                patch(
                    "backend.routers.repositories.store_encrypted_artifact",
                    return_value=stored_result,
                ) as encrypt,
            ):
                directory.return_value.__enter__.return_value = temp
                directory.return_value.__exit__.return_value = False
                stored, diagnostics = _encrypt_codearts_web_download(
                    cfg={
                        "project_id": "p",
                        "region": "cn-cq-1",
                        "devops_url": "https://devops.example.com",
                    },
                    download_uri="https://devops.example.com/download?id=file",
                    destination_path=str(Path(temp) / "artifact.pcenc"),
                    original_name="firmware.bin",
                    click_target={
                        "artifactName": "firmware.bin",
                        "displayPath": "/firmware/firmware.bin",
                        "folderSegments": ["firmware"],
                        "artifactId": "file",
                    },
                )

        self.assertIs(stored, stored_result)
        self.assertEqual(diagnostics[0]["interface"], "GET webpage download")
        self.assertEqual(encrypt.call_args.kwargs["original_name"], "firmware.bin")

    def test_local_download_endpoint_uses_web_browser_session_and_cleans_temp_file(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        user = type("User", (), {"id": 1, "username": "tester"})()
        cfg = {
            "enabled": True,
            "repository_mode": "private",
            "private_source": "web",
            "project_id": "p",
            "domain_name": "tenant",
            "username": "iam-user",
            "password": "secret",
            "devops_url": "https://devops.example.com",
        }

        def fake_web_download(**kwargs):
            Path(kwargs["output_path"]).write_bytes(b"firmware")
            return [{
                "interface": "GET webpage download",
                "response": {"headers": {"content-type": "application/octet-stream"}},
            }]

        with (
            patch("backend.routers.repositories._require_project_permission"),
            patch("backend.routers.repositories._build_codearts_download_context", return_value=(cfg, "")),
            patch(
                "backend.routers.repositories._resolve_codearts_download_auth",
                side_effect=AssertionError("Web 下载不应调用 API 下载鉴权"),
            ) as resolve_auth,
            patch(
                "backend.routers.repositories._run_codearts_web_download_to_path",
                side_effect=fake_web_download,
            ) as web_download,
        ):
            response = asyncio.run(download_codearts_artifact_to_local(
                project_id="p",
                download_uri="https://devops.example.com/download?id=file",
                name="固件.bin",
                id=None,
                db=db,
                current_user=user,
                _=None,
            ))

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(Path(response.path).read_bytes(), b"firmware")
        self.assertEqual(web_download.call_count, 1)
        resolve_auth.assert_not_called()
        asyncio.run(response.background())
        self.assertFalse(Path(response.path).exists())

    def test_server_download_endpoint_uses_web_session_before_any_api_auth(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        user = type("User", (), {"id": 1, "username": "tester"})()
        cfg = {
            "enabled": True,
            "repository_mode": "private",
            "private_source": "web",
            "project_id": "p",
            "domain_name": "tenant",
            "username": "iam-user",
            "password": "secret",
            "devops_url": "https://devops.example.com",
        }
        stored = type(
            "StoredArtifact",
            (),
            {
                "plaintext_size": 8,
                "md5": "m",
                "sha256": "s",
                "to_storage_metadata": lambda self: {"encrypted": True},
            },
        )()

        with TemporaryDirectory() as temp:
            encrypted_path = str(Path(temp) / "firmware.bin.pcenc")
            with (
                patch("backend.routers.repositories._ensure_project_member_seed"),
                patch("backend.routers.repositories._require_project_permission"),
                patch("backend.routers.repositories._build_codearts_download_context", return_value=(cfg, "")),
                patch(
                    "backend.routers.repositories._resolve_codearts_download_auth",
                    side_effect=AssertionError("Web 下载不应调用 API 下载鉴权"),
                ) as resolve_auth,
                patch(
                    "backend.routers.repositories._encrypt_codearts_web_download",
                    return_value=(stored, []),
                ) as web_download,
                patch("backend.routers.repositories._get_repository_download_root", return_value=temp),
                patch("backend.routers.repositories.build_encrypted_artifact_path", return_value=encrypted_path),
                patch(
                    "backend.routers.repositories._get_repository_server_transport_config",
                    return_value={
                        "transport": "local",
                        "host": "",
                        "port": 0,
                        "username": "",
                        "password": "",
                        "auth_type": "password",
                        "private_key_path": "",
                        "storage_root": temp,
                        "server_os": "windows",
                    },
                ),
                patch("backend.routers.repositories._get_repository_download_config", return_value={}),
                patch("backend.routers.repositories._get_repository_server_storage_root", return_value=temp),
            ):
                result = asyncio.run(download_codearts_artifact_to_server(
                    {
                        "project_id": "p",
                        "download_uri": "https://devops.example.com/download?id=file",
                        "name": "固件.bin",
                        "target": "local",
                    },
                    db,
                    user,
                    None,
                ))

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["location_state"]["storage_location"], "local")
        self.assertEqual(web_download.call_count, 1)
        resolve_auth.assert_not_called()
