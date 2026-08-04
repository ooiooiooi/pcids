import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.pcids_install import INSTALLER_CATALOG, _config_env, _default_script, _remote_exports


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "pcids_install.py"
ENTRYPOINT = ROOT / "scripts" / "pcids-install.cmd"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


class CodeArtsInstallAdapterTests(unittest.TestCase):
    def test_windows_entrypoint_supports_source_and_packaged_backend(self):
        content = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("pcids_backend.exe", content)
        self.assertIn("--run-script", content)
        self.assertIn("pcids_install.py", content)

    def test_desktop_package_and_installer_publish_machine_entrypoint(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        resources = {item.get("to"): item.get("from") for item in package["build"]["extraResources"]}
        self.assertEqual(resources["install-adapter/pcids-install.cmd"], "scripts/pcids-install.cmd")
        self.assertEqual(resources["install-adapter/pcids_install.py"], "scripts/pcids_install.py")
        self.assertEqual(resources["install-adapter/examples"], "scripts/codearts-install")
        installer = (ROOT / "build" / "installer.nsh").read_text(encoding="utf-8")
        self.assertIn('"PCIDS_INSTALL_ADAPTER" "$INSTDIR\\resources\\install-adapter\\pcids-install.cmd"', installer)
        self.assertIn('DeleteRegValue HKLM "SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment" "PCIDS_INSTALL_ADAPTER"', installer)

    def test_lists_each_supported_os_installer(self):
        result = subprocess.run(
            [sys.executable, str(ADAPTER), "list-installers"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual({row["os"] for row in rows}, {item["os"] for item in INSTALLER_CATALOG})
        self.assertEqual({row["scope"] for row in rows}, {"remote", "agent"})

    def test_every_catalog_entry_has_a_bundled_install_script(self):
        for item in INSTALLER_CATALOG:
            with self.subTest(os=item["os"]):
                script = _default_script(item)
                self.assertTrue(script.is_file(), script)
                self.assertGreater(script.stat().st_size, 100)

    def test_harmony_script_uses_windows_powershell_compatible_syntax(self):
        content = (ROOT / "scripts" / "codearts-install" / "harmony-package-install.ps1").read_text(encoding="utf-8")
        self.assertNotIn(".Source", content)
        self.assertNotIn("pwsh", content.lower())
        self.assertIn("-ieq '.hap'", content)

    def test_config_is_exposed_under_namespaced_environment_variables(self):
        env = _config_env({"boot_autostart": True, "release_channel": "stable"})
        self.assertEqual(env["PCIDS_CONFIG_BOOT_AUTOSTART"], "true")
        self.assertEqual(env["PCIDS_CONFIG_RELEASE_CHANNEL"], "stable")
        exports = _remote_exports({"INSTALL_DIR": "/opt/app with spaces", **env})
        self.assertIn("export INSTALL_DIR='/opt/app with spaces'", exports)

    def test_kylin_dry_run_validates_script_and_writes_machine_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "demo.tar.gz"
            artifact.write_bytes(b"demo")
            log_dir = root / "logs"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ADAPTER),
                    "run",
                    "--os",
                    "kylin",
                    "--artifact",
                    str(artifact),
                    "--config-json",
                    json.dumps(
                        {
                            "task_type": "os",
                            "platform": "os",
                            "os_type": "kylin",
                            "connection_protocol": "SSH",
                            "auth_type": "password",
                            "target_ip": "192.0.2.10",
                            "target_port": 22,
                            "login_username": "pcids",
                            "install_dir": "/opt/pcids-app",
                            "timeout_seconds": 300,
                        }
                    ),
                    "--run-id",
                    "codearts-install-test",
                    "--log-dir",
                    str(log_dir),
                    "--dry-run",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(events[0]["event"], "started")
            self.assertEqual(events[0]["installer"], "kylin_ssh_package_install")
            self.assertEqual(events[-1]["message"], "dry run completed")
            self.assertTrue((log_dir / "pcids-install-codearts-install-test.log").is_file())
            self.assertTrue((log_dir / "pcids-install-codearts-install-test.jsonl").is_file())

    def test_harmony_dry_run_requires_device_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = Path(temp_dir) / "demo.hap"
            artifact.write_bytes(b"demo")
            result = subprocess.run(
                [sys.executable, str(ADAPTER), "run", "--os", "harmony", "--artifact", str(artifact), "--dry-run"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            event = json.loads(result.stdout.splitlines()[-1])
            self.assertIn("--device-id", event["message"])

    def test_harmony_accepts_the_existing_system_install_config_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = Path(temp_dir) / "demo.hap"
            artifact.write_bytes(b"demo")
            config = {
                "task_type": "os",
                "platform": "os",
                "os_type": "harmony",
                "connection_protocol": "HDC",
                "harmony_device_id": "system-flow-device",
                "install_dir": "/data/local/tmp",
            }
            result = subprocess.run(
                [sys.executable, str(ADAPTER), "run", "--os", "harmony", "--artifact", str(artifact), "--config-json", json.dumps(config), "--dry-run"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sylixos_accepts_ftp_system_config_and_plaintext_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = Path(temp_dir) / "control-app"
            artifact.write_bytes(b"demo")
            config = {
                "task_type": "os",
                "platform": "os",
                "os_type": "yinghui",
                "deployment_mode": "FTP",
                "target_ip": "192.0.2.12",
                "ftp_port": 21,
                "login_username": "root",
                "login_passwordless": True,
                "install_dir": "/apps",
                "boot_autostart": True,
            }
            base_command = [sys.executable, str(ADAPTER), "run", "--os", "yinghui", "--artifact", str(artifact), "--boot-autostart", "--dry-run"]
            result = subprocess.run(
                [*base_command, "--config-json", json.dumps(config)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            configured_password = subprocess.run(
                [*base_command, "--config-json", json.dumps({**config, "login_passwordless": False, "login_password": "plain-config-password"})],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(configured_password.returncode, 0, configured_password.stdout + configured_password.stderr)
            self.assertNotIn("plain-config-password", configured_password.stdout)

    def test_plaintext_password_is_a_copy_friendly_command_line_option(self):
        result = subprocess.run(
            [sys.executable, str(ADAPTER), "run", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--password ", result.stdout)
        self.assertIn("--password-env", result.stdout)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell install script contract")
    def test_agent_local_custom_script_receives_contract_and_returns_exit_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "demo.hap"
            artifact.write_bytes(b"demo")
            script = root / "install.ps1"
            script.write_text(
                "Write-Host \"OS=$env:PCIDS_OS_TYPE DEVICE=$env:PCIDS_DEVICE_ID\"\n"
                "if (-not (Test-Path -LiteralPath $env:PCIDS_ARTIFACT_PATH)) { exit 9 }\n"
                "exit 0\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ADAPTER),
                    "run",
                    "--os",
                    "harmony",
                    "--artifact",
                    str(artifact),
                    "--install-script",
                    str(script),
                    "--device-id",
                    "test-device",
                    "--log-dir",
                    str(root),
                ],
                cwd=ROOT,
                env={**os.environ, "PCIDS_TARGET_PASSWORD": "must-not-be-logged"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("OS=harmony DEVICE=test-device", result.stdout)
            self.assertNotIn("must-not-be-logged", result.stdout)
            self.assertIn('"event": "completed"', result.stdout)

    @unittest.skipUnless(sys.platform == "win32" and GIT_BASH.is_file(), "CodeArts Git Bash contract")
    def test_git_bash_can_call_cmd_entrypoint_with_plaintext_special_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "demo.bin"
            artifact.write_bytes(b"demo")
            command = (
                "PCIDS_TARGET_PASSWORD='P@ss&word$!' \"$PCIDS_INSTALL_ADAPTER\" run "
                "--os kylin --artifact \"$WORKSPACE/demo.bin\" --target-host '192.0.2.10' "
                "--username 'pcids' --auth-type password --install-dir '/opt/app' --dry-run "
                "--run-id 'git-bash-contract' --log-dir \"$WORKSPACE/logs\""
            )
            result = subprocess.run(
                [str(GIT_BASH), "-lc", command],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PCIDS_INSTALL_ADAPTER": str(ENTRYPOINT),
                    "WORKSPACE": str(root),
                    "BUILD_ID": "git-bash-contract",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"event": "completed"', result.stdout)
            self.assertNotIn("P@ss&word$!", result.stdout)


if __name__ == "__main__":
    unittest.main()
