import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from passlib.hash import md5_crypt

from backend.routers.tasks import (
    _stage_hdd1_with_system_account,
    _upload_sylixos_partition_files_via_ftp,
    _validate_sylixos_partition_upload_config,
    _validate_sylixos_system_account_values,
)


class SylixOSSystemAccountTests(unittest.TestCase):
    def test_root_short_password_is_supported(self):
        _validate_sylixos_system_account_values("root", "root")

    def test_partial_or_shadow_unsafe_credentials_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be provided together|必须同时填写"):
            _validate_sylixos_system_account_values("root", "")
        with self.assertRaisesRegex(ValueError, "cannot contain|不能包含"):
            _validate_sylixos_system_account_values("root", "bad:password")

    def test_stage_hdd1_updates_root_shadow_with_short_password(self):
        with tempfile.TemporaryDirectory() as source_dir:
            etc_dir = Path(source_dir) / "etc"
            etc_dir.mkdir(parents=True)
            (etc_dir / "passwd").write_text("root:x:0:0:root:/root:/bin/sh\n", encoding="utf-8")
            (etc_dir / "shadow").write_text("root:old:0:0:99999:7:::\n", encoding="utf-8")

            staged_path, staged_temp = _stage_hdd1_with_system_account(source_dir, "root", "root")
            self.assertIsNotNone(staged_temp)
            try:
                shadow_entry = (Path(staged_path) / "etc" / "shadow").read_text(encoding="utf-8").strip()
                password_hash = shadow_entry.split(":", 2)[1]
                self.assertTrue(md5_crypt.verify("root", password_hash))
            finally:
                staged_temp.cleanup()

    def test_partition_preflight_fails_before_board_operations(self):
        with self.assertRaisesRegex(ValueError, "hdd1"):
            _validate_sylixos_partition_upload_config(
                {
                    "hdd1_source_path": str(Path(tempfile.gettempdir()) / "missing-pcids-hdd1"),
                    "system_username": "root",
                    "system_password": "root",
                }
            )

    def test_ftp_upload_uses_staged_hdd1_with_configured_password(self):
        class FakeFtp:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self, *args, **kwargs):
                return None

            def login(self, *args, **kwargs):
                return None

            def set_pasv(self, *args, **kwargs):
                return None

            def quit(self):
                return None

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hdd0 = root / "hdd0"
            hdd1 = root / "hdd1"
            artifact = root / "app.bin"
            hdd0.mkdir()
            (hdd1 / "etc").mkdir(parents=True)
            artifact.write_bytes(b"firmware")
            (hdd1 / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/sh\n", encoding="utf-8")
            (hdd1 / "etc" / "shadow").write_text("root:old:0:0:99999:7:::\n", encoding="utf-8")
            uploaded_sources: list[str] = []

            def capture_upload(_ftp, local_root, _remote_root):
                uploaded_sources.append(str(local_root))
                if Path(local_root).name == "hdd1":
                    shadow_entry = (Path(local_root) / "etc" / "shadow").read_text(encoding="utf-8").strip()
                    password_hash = shadow_entry.split(":", 2)[1]
                    self.assertTrue(md5_crypt.verify("new-root-password", password_hash))
                return 1

            config = {
                "hdd0_source_path": str(hdd0),
                "hdd1_source_path": str(hdd1),
                "system_username": "root",
                "system_password": "new-root-password",
                "ftp_login_user": "root",
                "ftp_login_password": "root",
            }
            with patch("backend.routers.tasks.ftplib.FTP", FakeFtp), patch(
                "backend.routers.tasks._ftp_upload_tree",
                side_effect=capture_upload,
            ):
                logs = _upload_sylixos_partition_files_via_ftp(
                    config,
                    "192.168.1.230",
                    21,
                    artifact.name,
                    str(artifact),
                )

            self.assertEqual(len(uploaded_sources), 2)
            self.assertNotEqual(Path(uploaded_sources[1]).resolve(), hdd1.resolve())
            self.assertIn("系统账户：root", "\n".join(logs))
            self.assertIn("重启板卡并从 hdd1 正常启动", "\n".join(logs))

    def test_reburn_uses_current_ftp_password_not_desired_system_password(self):
        login_attempts: list[tuple[str, str]] = []

        class PasswordAwareFtp:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self, *args, **kwargs):
                return None

            def login(self, username, password):
                login_attempts.append((username, password))
                if password != "current-password":
                    raise RuntimeError("530 Login failed")

            def set_pasv(self, *args, **kwargs):
                return None

            def quit(self):
                return None

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hdd0 = root / "hdd0"
            hdd1 = root / "hdd1"
            artifact = root / "app.bin"
            hdd0.mkdir()
            (hdd1 / "etc").mkdir(parents=True)
            artifact.write_bytes(b"firmware")
            (hdd1 / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/sh\n", encoding="utf-8")
            (hdd1 / "etc" / "shadow").write_text("root:old:0:0:99999:7:::\n", encoding="utf-8")
            config = {
                "hdd0_source_path": str(hdd0),
                "hdd1_source_path": str(hdd1),
                "ftp_login_user": "root",
                "ftp_login_password": "current-password",
                "system_username": "root",
                "system_password": "desired-new-password",
            }

            with patch("backend.routers.tasks.ftplib.FTP", PasswordAwareFtp), patch(
                "backend.routers.tasks._ftp_upload_tree",
                return_value=1,
            ):
                logs = _upload_sylixos_partition_files_via_ftp(
                    config,
                    "192.168.1.230",
                    21,
                    artifact.name,
                    str(artifact),
                )

        self.assertEqual(login_attempts, [("root", "current-password")])
        self.assertNotIn(("root", "desired-new-password"), login_attempts)
        self.assertIn("FTP 登录成功：当前 FTP 账户 user=root", "\n".join(logs))


if __name__ == "__main__":
    unittest.main()
