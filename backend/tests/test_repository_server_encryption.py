import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.routers.repositories import _retrieve_repository_artifact_via_ssh, _transfer_repository_artifact_via_ssh
from backend.utils.artifact_crypto import build_encrypted_artifact_path, store_encrypted_artifact
from backend.utils.key_management import ARTIFACT_MASTER_KEY_ENV, reset_artifact_master_key_cache


def _encode_key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(seed).decode("ascii").rstrip("=")


class _FakeSSHSession:
    uploaded_source = ""
    uploaded_target = ""
    commands = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def run(self, command, timeout=None):
        type(self).commands.append(command)
        return type("Result", (), {"success": True, "reason": ""})()

    def upload(self, source, target):
        type(self).uploaded_source = source
        type(self).uploaded_target = target

    def download(self, source, target):
        type(self).uploaded_source = source
        Path(target).write_bytes(b"PCIDSENC1" + b"server-copy")

    client = object()


class RepositoryServerEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env_patcher = patch.dict(
            os.environ,
            {ARTIFACT_MASTER_KEY_ENV: _encode_key(b"3" * 32)},
            clear=False,
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        reset_artifact_master_key_cache()
        self.addCleanup(reset_artifact_master_key_cache)

    def test_ssh_transfer_uploads_encrypted_file_without_materializing_plaintext(self):
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "firmware.bin")
        store_encrypted_artifact(io.BytesIO(b"secret-firmware"), encrypted_path, original_name="firmware.bin")
        config = {
            "host": "192.0.2.1",
            "port": 22,
            "username": "user",
            "password": "password",
            "auth_type": "password",
            "private_key_path": "",
            "storage_root": "/srv/artifacts",
        }

        with (
            patch("backend.routers.repositories.SSHClientSession", _FakeSSHSession),
            patch("backend.routers.repositories._verify_remote_artifact_via_sftp"),
            patch("backend.routers.repositories._remove_remote_artifact_via_sftp"),
        ):
            remote_path, target = _transfer_repository_artifact_via_ssh(encrypted_path, "firmware.bin", config)

        self.assertEqual(_FakeSSHSession.uploaded_source, encrypted_path)
        self.assertEqual(_FakeSSHSession.uploaded_target, "/srv/artifacts/firmware.bin.pcenc")
        self.assertEqual(remote_path, "/srv/artifacts/firmware.bin.pcenc")
        self.assertEqual(target, "192.0.2.1:22")
        self.assertEqual(Path(_FakeSSHSession.uploaded_source).read_bytes()[:9], b"PCIDSENC1")

    def test_windows_ssh_transfer_uses_windows_command_and_sftp_safe_path(self):
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "firmware.bin")
        store_encrypted_artifact(io.BytesIO(b"windows-firmware"), encrypted_path, original_name="firmware.bin")
        config = {
            "host": "192.0.2.2",
            "port": 22,
            "username": "user",
            "password": "password",
            "auth_type": "password",
            "private_key_path": "",
            "storage_root": "C:/pcids-artifacts",
            "server_os": "windows",
        }

        with (
            patch("backend.routers.repositories.SSHClientSession", _FakeSSHSession),
            patch("backend.routers.repositories._verify_remote_artifact_via_sftp"),
            patch("backend.routers.repositories._remove_remote_artifact_via_sftp"),
        ):
            remote_path, target = _transfer_repository_artifact_via_ssh(encrypted_path, "firmware.bin", config)

        self.assertEqual(_FakeSSHSession.uploaded_target, "C:/pcids-artifacts/firmware.bin.pcenc")
        self.assertEqual(remote_path, r"C:\pcids-artifacts\firmware.bin.pcenc")
        self.assertEqual(target, "192.0.2.2:22")
        self.assertTrue(any("powershell" in command.lower() for command in _FakeSSHSession.commands))

    def test_windows_server_artifact_can_be_retrieved_over_sftp(self):
        destination = os.path.join(self.temp_dir.name, "retrieved.pcenc")
        config = {
            "host": "192.0.2.2",
            "port": 22,
            "username": "user",
            "password": "password",
            "auth_type": "password",
            "private_key_path": "",
            "server_os": "windows",
        }

        with patch("backend.routers.repositories.SSHClientSession", _FakeSSHSession):
            result = _retrieve_repository_artifact_via_ssh(
                r"C:\pcids-artifacts\firmware.bin.pcenc",
                destination,
                config,
            )

        self.assertEqual(_FakeSSHSession.uploaded_source, "C:/pcids-artifacts/firmware.bin.pcenc")
        self.assertEqual(result, destination)
        self.assertTrue(Path(destination).read_bytes().startswith(b"PCIDSENC1"))

    def test_server_host_can_store_locally_without_ssh_loopback(self):
        encrypted_path = build_encrypted_artifact_path(self.temp_dir.name, "firmware.bin")
        store_encrypted_artifact(io.BytesIO(b"local-server"), encrypted_path, original_name="firmware.bin")
        storage_root = os.path.join(self.temp_dir.name, "server")
        config = {
            "host": "127.0.0.1",
            "port": 22,
            "username": "unused",
            "password": "",
            "auth_type": "password",
            "private_key_path": "",
            "storage_root": storage_root,
            "server_os": "windows",
        }

        remote_path, _target = _transfer_repository_artifact_via_ssh(encrypted_path, "firmware.bin", config)

        self.assertTrue(Path(remote_path).is_file())
        self.assertTrue(Path(remote_path).read_bytes().startswith(b"PCIDSENC1"))


if __name__ == "__main__":
    unittest.main()
