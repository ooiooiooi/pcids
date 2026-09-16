import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.utils.key_management import (
    ARTIFACT_MASTER_KEY_AUTO_REPAIR_ENV,
    MasterKeyError,
    _load_or_create_windows_key,
)


class KeyManagementTests(unittest.TestCase):
    def test_invalid_dpapi_key_is_repaired_when_auto_repair_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "artifact_master_key.dpapi"
            key_path.write_bytes(b"old-protected-key")
            with (
                patch.dict(
                    os.environ,
                    {
                        ARTIFACT_MASTER_KEY_AUTO_REPAIR_ENV: "1",
                        "PCIDS_TEST_TIMESTAMP": "repairtest",
                    },
                    clear=False,
                ),
                patch("backend.utils.key_management._get_windows_master_key_path", return_value=key_path),
                patch(
                    "backend.utils.key_management._dpapi_unprotect",
                    side_effect=MasterKeyError("dpapi unprotect failed"),
                ),
                patch("backend.utils.key_management._dpapi_protect", return_value=b"new-protected-key"),
                patch("backend.utils.key_management.secrets.token_bytes", return_value=b"1" * 32),
            ):
                record = _load_or_create_windows_key()

            self.assertEqual(record.source, "windows_dpapi_repaired")
            self.assertIn(str(key_path), str(record.location))
            self.assertEqual(key_path.read_bytes(), b"new-protected-key")
            archived_path = key_path.with_name("artifact_master_key.dpapi.invalid-repairtest")
            self.assertTrue(archived_path.exists())
            self.assertEqual(archived_path.read_bytes(), b"old-protected-key")

    def test_invalid_dpapi_key_still_fails_without_auto_repair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "artifact_master_key.dpapi"
            key_path.write_bytes(b"old-protected-key")
            with (
                patch.dict(os.environ, {ARTIFACT_MASTER_KEY_AUTO_REPAIR_ENV: "0"}, clear=False),
                patch("backend.utils.key_management._get_windows_master_key_path", return_value=key_path),
                patch(
                    "backend.utils.key_management._dpapi_unprotect",
                    side_effect=MasterKeyError("dpapi unprotect failed"),
                ),
            ):
                with self.assertRaises(MasterKeyError):
                    _load_or_create_windows_key()


if __name__ == "__main__":
    unittest.main()
