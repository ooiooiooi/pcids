import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.utils.burner_automation import SYSTEM_SCRIPT_CATALOG, build_system_script_content
from scripts.pcids_flash import EventLogger, _CODEARTS_RUNTIME_ENV_ALIASES, _adapter_validation_defaults, _adapter_working_directory, _apply_adapter_defaults, _batch_command, _decode_tool_output, _resolve_request


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "pcids_flash.py"
ENTRYPOINT = ROOT / "scripts" / "pcids-flash.cmd"


class CodeArtsFlashAdapterTests(unittest.TestCase):
    def test_windows_entrypoint_exposes_packaged_burner_tools_to_one_shot_backend(self):
        content = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("PCIDS_BUNDLED_TOOLS_DIR", content)
        self.assertIn("%PCIDS_ROOT%\\tools\\burners", content)

    def test_event_logger_falls_back_to_ascii_json_for_unencodable_console_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_stdout = io.BytesIO()
            gbk_stdout = io.TextIOWrapper(raw_stdout, encoding="gbk", errors="strict")
            logger = EventLogger(Path(temp_dir), "encoding-test")
            with mock.patch("scripts.pcids_flash.sys.stdout", gbk_stdout):
                logger.emit("tool-output", message="invalid replacement: \ufffd")
                gbk_stdout.flush()
            event = json.loads(raw_stdout.getvalue().decode("gbk"))
            self.assertEqual(event["message"], "invalid replacement: \ufffd")

    def test_tool_output_decoder_accepts_utf8_and_gb18030(self):
        self.assertEqual(_decode_tool_output("烧录完成".encode("utf-8")), "烧录完成")
        self.assertEqual(_decode_tool_output("烧录失败".encode("gb18030")), "烧录失败")

    def test_codearts_keeps_ui_values_for_validation_and_has_ascii_runtime_aliases(self):
        args = type("Args", (), {"burner": "PW-LINK", "script": "", "target_chip": "STM32F107VCT6", "board": "board", "burner_sn": "sn", "burner_port": ""})()
        item, config = _resolve_request(args, {})
        self.assertEqual(item["name"], "pwlink_v2_arm_mcu_flash")
        self.assertEqual(config["erase_mode"], "全片擦除")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["ERASE_MODE"]["全片擦除"], "chip")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["ERASE_MODE"]["不擦除直接编程"], "no-erase")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["COMPLETION_ACTION"]["复位运行"], "reset-run")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["COMPLETION_ACTION"]["编程复位后运行"], "reset-run")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["EEPROM_WRITE"]["否"], "no")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["BLANK_CHECK"]["是"], "yes")
        self.assertEqual(_CODEARTS_RUNTIME_ENV_ALIASES["EXECUTE_PROGRAM"]["否"], "no")

    def test_xds510plus_uses_deployed_default_ccxml_when_pipeline_leaves_it_empty(self):
        item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "xds510plus_dsp_flash")
        self.assertEqual(item["default_config"]["timeout_seconds"], 600)
        with tempfile.TemporaryDirectory() as temp_dir:
            tools_root = Path(temp_dir) / "tools" / "burners"
            ccxml = tools_root / "XDS510plus" / "targets" / "seed_xds510plus_f28335.ccxml"
            ccxml.parent.mkdir(parents=True)
            ccxml.write_text("<config />", encoding="utf-8")
            config = {"target_config_file": ""}
            with mock.patch.dict("os.environ", {"PCIDS_BUNDLED_TOOLS_DIR": str(tools_root)}, clear=False):
                _apply_adapter_defaults(item, config)
            self.assertEqual(Path(config["target_config_file"]), ccxml)

    def test_hdsc_adapter_validation_uses_uart_baud_options(self):
        item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "hdsc_ccid_arm_mcu_flash")
        defaults = _adapter_validation_defaults(item)
        self.assertEqual(defaults["speed_label"], "波特率")
        self.assertIn(115200, defaults["speed_options"])

    def test_gowin_adapter_uses_the_cli_directory_without_changing_normal_scripts(self):
        item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "gowin_usb_cable_fpga_flash")
        with tempfile.TemporaryDirectory() as temp_dir:
            cli = Path(temp_dir) / "bin" / "programmer_cli.exe"
            cli.parent.mkdir()
            cli.write_bytes(b"")
            self.assertEqual(_adapter_working_directory(item, {"GOWIN_PROGRAMMER_CLI": str(cli)}), cli.parent)
        bundled_cli = ROOT / "tools" / "burners" / "GOWIN" / "bin" / "programmer_cli.exe"
        expected_directory = bundled_cli.parent if bundled_cli.is_file() else ROOT
        self.assertEqual(_adapter_working_directory(item, {}), expected_directory)

    def test_gowin_adapter_falls_back_to_its_installed_tools_directory(self):
        item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "gowin_usb_cable_fpga_flash")
        with tempfile.TemporaryDirectory() as temp_dir:
            cli = Path(temp_dir) / "GOWIN" / "bin" / "programmer_cli.exe"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"")
            with mock.patch("scripts.pcids_flash.PROJECT_ROOT", Path(temp_dir).parent):
                with mock.patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": temp_dir}, clear=False):
                    self.assertEqual(_adapter_working_directory(item, {}), cli.parent)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch invocation contract")
    def test_batch_command_executes_generated_script_and_preserves_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="pcids batch path ") as temp_dir:
            script = Path(temp_dir) / "generated burner.bat"
            script.write_text("@echo off\r\necho PCIDS_BATCH_OK\r\nexit /b 7\r\n", encoding="utf-8")
            result = subprocess.run(
                _batch_command(script),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(result.returncode, 7, result.stdout)
            self.assertIn(b"PCIDS_BATCH_OK", result.stdout)

    def test_lists_every_generic_burner_workflow(self):
        result = subprocess.run(
            [sys.executable, str(ADAPTER), "list-burners"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        workflows = [json.loads(line)["script"] for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(set(workflows), {item["name"] for item in SYSTEM_SCRIPT_CATALOG})

    def test_each_windows_system_burner_has_a_generated_batch_script(self):
        for item in SYSTEM_SCRIPT_CATALOG:
            if item.get("task_type") == "hybrid" or item.get("type") != "bat":
                continue
            with self.subTest(profile=item["name"]):
                content = build_system_script_content(item["name"], item["burner"])
                self.assertTrue(content.strip())
                self.assertIn("FIRMWARE_PATH", content)
            self.assertIn("exit /b", content)

    def test_generated_batch_scripts_are_gbk_compatible_without_codepage_switch(self):
        for script_name, burner in (
            ("pwlink_v2_arm_mcu_flash", "PWLINK2"),
            ("mplab_icd3_pic_flash", "MPLAB ICD 3 DV164035"),
        ):
            with self.subTest(profile=script_name):
                content = build_system_script_content(script_name, burner)
                content.encode("gb18030")
                self.assertNotIn("chcp 65001", content)

    def test_dry_run_writes_machine_readable_logs_without_running_hardware(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            firmware = root / "firmware.hex"
            firmware.write_text(":00000001FF\n", encoding="ascii")
            log_dir = root / "logs"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ADAPTER),
                    "run",
                    "--burner",
                    "MPLAB ICD 3 DV164035",
                    "--target-chip",
                    "PIC32MZ2048EFM144",
                    "--firmware",
                    str(firmware),
                    "--run-id",
                    "codearts-test",
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
            self.assertEqual(events[-1]["event"], "completed")
            self.assertTrue((log_dir / "pcids-flash-codearts-test.log").is_file())
            self.assertTrue((log_dir / "pcids-flash-codearts-test.jsonl").is_file())

    def test_generic_pwlink_alias_does_not_need_a_profile_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            firmware = root / "firmware.hex"
            firmware.write_text(":00000001FF\n", encoding="ascii")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ADAPTER),
                    "run",
                    "--burner",
                    "PW-LINK",
                    "--target-chip",
                    "STM32F107",
                    "--burner-sn",
                    "PW-001",
                    "--firmware",
                    str(firmware),
                    "--dry-run",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            event = json.loads(result.stdout.splitlines()[0])
            self.assertEqual(event["script"], "pwlink_v2_arm_mcu_flash")
            self.assertEqual(event["burner_sn"], "PW-001")


if __name__ == "__main__":
    unittest.main()
