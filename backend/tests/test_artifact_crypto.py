import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.utils.artifact_crypto import (
    ArtifactKeyValidationError,
    build_encrypted_artifact_path,
    decrypt_artifact_to_path,
    is_encrypted_artifact,
    iter_decrypted_artifact,
    materialize_artifact_for_execution,
    store_encrypted_artifact,
)
from backend.utils.key_management import ARTIFACT_MASTER_KEY_ENV, reset_artifact_master_key_cache


def _encode_key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(seed).decode("ascii").rstrip("=")


class ArtifactCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env_patcher = patch.dict(
            os.environ,
            {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"1" * 32)},
            clear=False,
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        reset_artifact_master_key_cache()
        self.addCleanup(reset_artifact_master_key_cache)

    def test_store_encrypted_artifact_never_persists_plaintext(self):
        payload = b"firmware-binary-payload-1234567890"
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "firmware.bin")

        stored = store_encrypted_artifact(io.BytesIO(payload), encrypted_path, original_name="firmware.bin")

        self.assertTrue(is_encrypted_artifact(stored.path))
        self.assertEqual(stored.plaintext_size, len(payload))
        self.assertEqual(stored.original_name, "firmware.bin")
        raw_bytes = Path(stored.path).read_bytes()
        self.assertNotIn(payload, raw_bytes)
        self.assertNotEqual(raw_bytes, payload)
        self.assertEqual(b"".join(iter_decrypted_artifact(stored.path)), payload)

    def test_execution_materialization_cleans_up_plaintext_file(self):
        payload = b"execute-me"
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "tool.exe")
        stored = store_encrypted_artifact(io.BytesIO(payload), encrypted_path, original_name="tool.exe")
        work_dir = Path(self.temp_dir.name) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        with materialize_artifact_for_execution(stored.path, work_dir=str(work_dir), preferred_name="tool.exe") as exec_path:
            self.assertTrue(Path(exec_path).exists())
            self.assertEqual(Path(exec_path).read_bytes(), payload)

        self.assertFalse((work_dir / "tool.exe").exists())

    def test_decrypt_with_wrong_key_is_rejected(self):
        payload = b"signed-package"
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "package.tar")
        store_encrypted_artifact(io.BytesIO(payload), encrypted_path, original_name="package.tar")

        with patch.dict(
            os.environ,
            {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"2" * 32)},
            clear=False,
        ):
            reset_artifact_master_key_cache()
            with self.assertRaises(ArtifactKeyValidationError):
                b"".join(iter_decrypted_artifact(encrypted_path))

    def test_decrypt_to_path_supports_full_encrypt_then_execute_flow(self):
        payload = b"\x00\x01\x02artifact-content"
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "demo.bin")
        stored = store_encrypted_artifact(io.BytesIO(payload), encrypted_path, original_name="demo.bin")
        output_path = str(Path(self.temp_dir.name) / "decrypted.bin")

        decrypted_path = decrypt_artifact_to_path(stored.path, output_path)

        self.assertEqual(Path(decrypted_path).read_bytes(), payload)
        self.assertNotEqual(Path(stored.path).read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
