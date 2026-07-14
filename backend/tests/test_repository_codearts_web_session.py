import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.routers.repositories import _list_codearts_web_private_files, _sanitize_codearts_web_diagnostics


class CodeartsWebSessionAdapterTests(unittest.TestCase):
    def test_runtime_uses_project_scoped_template_session_and_full_recursion(self):
        script = (Path(__file__).resolve().parents[2] / "tools" / "codearts_release_debugger" / "browser_runtime" / "codearts_web_session.js")
        source = script.read_text(encoding="utf-8")
        self.assertIn("String(payload.projectId || '') !== String(config.projectId || '')", source)
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

    def test_normalizes_web_details_and_retains_directory_metadata(self):
        response = {
            "response": {"ok": True, "body": {"result": {"data": [
                {"id": "dir", "name": "empty", "type": "folder", "_list": {"id": "dir", "name": "empty", "type": "folder"}},
                {"id": "file", "name": "boot.bin", "type": "file", "_detail": {
                    "name": "boot.bin", "repoFilePath": "firmware/boot.bin", "size": 12,
                    "md5": "a" * 32, "sha256": "b" * 64,
                    "downloadUrl": "https://example/download", "downloadUrlWithId": "https://example/download?id=file",
                }},
            ]}}},
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
        self.assertEqual(meta["folders"][0]["name"], "empty")

    def test_diagnostic_persistence_redacts_credential_values(self):
        safe = _sanitize_codearts_web_diagnostics({
            "cookie": "secret-cookie", "cftk": "secret-csrf", "authorization": "Bearer secret",
            "request_debug": {"session_cookie_names": ["SESSION"], "csrf_header_fingerprint": "a1b2c3d4e5f6"},
        })
        self.assertEqual(safe["cookie"], "***")
        self.assertEqual(safe["cftk"], "***")
        self.assertEqual(safe["authorization"], "***")
        self.assertEqual(safe["request_debug"]["session_cookie_names"], ["SESSION"])
