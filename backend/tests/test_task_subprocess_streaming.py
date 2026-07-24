import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.routers.tasks import (
    _build_script_exec_command,
    _decorate_timeout_log,
    _build_local_script_execution_log,
    _build_task_exception_log,
    _decode_mixed_subprocess_output,
    _decode_subprocess_output,
    _execute_script_content_locally,
    _resolve_subprocess_output_decoder,
    _run_subprocess_command,
    _script_output_failure_reason,
    _should_restore_burner_environment,
)
from backend.utils.burner_automation import build_system_script_content


class TaskSubprocessStreamingTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    def test_windows_batch_compat_command_is_limited_to_three_debuggers(self):
        path = r"C:\Temp\pcids-test.bat"
        for script_name in (
            "stlink_stm32_mcu_flash",
            "jlink_v4_arm_mcu_flash",
            "gdlink_arm_mcu_flash",
        ):
            self.assertEqual(
                _build_script_exec_command("bat", path, script_name),
                ["cmd.exe", "/d", "/s", "/c", "call", path],
            )

        for script_name in (
            "gowin_usb_cable_fpga_flash",
            "pwlink_v2_arm_mcu_flash",
            "hdsc_ccid_arm_mcu_flash",
        ):
            self.assertEqual(_build_script_exec_command("bat", path, script_name), ["cmd", "/c", path])

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_three_debugger_batches_keep_header_and_variable_names_intact(self):
        script = "\n".join(
            [
                "@echo off",
                "setlocal EnableExtensions EnableDelayedExpansion",
                'set "SCRIPT_NAME=debugger_batch_test"',
                'set "BURNER_NAME=debugger"',
                "echo [CONFIG] SCRIPT_NAME=!SCRIPT_NAME!",
                "echo [CONFIG] BURNER_NAME=!BURNER_NAME!",
                "exit /b 0",
                "",
            ]
        )
        with patch("backend.routers.tasks.ensure_burner_environment", return_value=""):
            for script_name in (
                "stlink_stm32_mcu_flash",
                "jlink_v4_arm_mcu_flash",
                "gdlink_arm_mcu_flash",
            ):
                success, log_text, failure_reason = await _execute_script_content_locally(
                    script,
                    "bat",
                    {},
                    10,
                    script_name,
                )
                self.assertTrue(success, failure_reason)
                self.assertIn("[CONFIG] SCRIPT_NAME=debugger_batch_test", log_text)
                self.assertIn("[CONFIG] BURNER_NAME=debugger", log_text)
                self.assertNotIn("is not recognized as an internal or external command", log_text)

    async def test_subprocess_output_callback_receives_stdout_and_stderr(self):
        chunks: list[tuple[str, str]] = []

        async def on_output(stream_name: str, text: str) -> None:
            chunks.append((stream_name, text))

        ok, stdout, stderr, reason = await _run_subprocess_command(
            [
                sys.executable,
                "-c",
                "import sys; print('out-line', flush=True); print('err-line', file=sys.stderr, flush=True)",
            ],
            timeout_seconds=10,
            output_callback=on_output,
        )

        self.assertTrue(ok, reason)
        self.assertIn("out-line", stdout)
        self.assertIn("err-line", stderr)
        self.assertTrue(any(name == "stdout" and "out-line" in text for name, text in chunks))
        self.assertTrue(any(name == "stderr" and "err-line" in text for name, text in chunks))

    async def test_timeout_keeps_captured_stdout_and_stderr_for_task_log(self):
        ok, stdout, stderr, reason = await _run_subprocess_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys,time; "
                    "print('before-timeout', flush=True); "
                    "print('stderr-before-timeout', file=sys.stderr, flush=True); "
                    "time.sleep(2)"
                ),
            ],
            timeout_seconds=0.3,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "脚本执行超时")
        log_text = _build_local_script_execution_log("timeout-script.py", stdout, stderr)
        self.assertIn("before-timeout", log_text)
        self.assertIn("stderr-before-timeout", log_text)

    async def test_non_streaming_subprocess_captures_output_without_live_callback(self):
        chunks: list[tuple[str, str]] = []

        async def on_output(stream_name: str, text: str) -> None:
            chunks.append((stream_name, text))

        ok, stdout, stderr, reason = await _run_subprocess_command(
            [
                sys.executable,
                "-c",
                "import sys; print('batch-out'); print('batch-err', file=sys.stderr)",
            ],
            timeout_seconds=10,
            output_callback=on_output,
            stream_output=False,
        )

        self.assertTrue(ok, reason)
        self.assertEqual(chunks, [])
        self.assertIn("batch-out", stdout)
        self.assertIn("batch-err", stderr)

    async def test_non_zero_script_uses_last_stderr_line_as_failure_reason(self):
        success, _log_text, failure_reason = await _execute_script_content_locally(
            "import sys\nprint('device permission denied', file=sys.stderr)\nsys.exit(7)\n",
            "python",
            {},
            10,
            "permission-error.py",
        )

        self.assertFalse(success)
        self.assertIn("device permission denied", failure_reason)
        self.assertIn("退出码 7", failure_reason)

    def test_mplab_ipecmd_raw_address_mismatch_beats_disconnect_tail_line(self):
        reason = _script_output_failure_reason(
            "\n".join(
                [
                    "[IPECMD] Programming Target Failed.",
                    "[IPECMD-RAW] 地址0  期望数值40100  收到数值0",
                    "[IPECMD-RAW] 编程器件失败",
                    "[IPECMD-RAW] ICD3移除",
                ]
            ),
            "",
        )

        self.assertIn("MPLAB 写入校验失败", reason)
        self.assertIn("地址0", reason)
        self.assertNotIn("ICD3移除", reason)

    def test_mplab_protected_memory_error_beats_generic_programming_failure(self):
        reason = _script_output_failure_reason(
            "\n".join(
                [
                    "[IPECMD] Programming Target Failed.",
                    "[IPECMD-RAW] 您试图更改受保护的引导和安全存储器要执行该操作必须在调试工具安全段属性页上选择引导段安全段和通用段选项",
                    "[IPECMD-RAW] 编程器件失败",
                    "[IPECMD-RAW] ICD3移除",
                ]
            ),
            "",
        )

        self.assertIn("受保护的引导/安全存储器", reason)
        self.assertNotIn("ICD3移除", reason)
        self.assertNotIn("目标器件未能正确写入", reason)

    async def test_zero_exit_with_cmd_fatal_stderr_is_treated_as_failure(self):
        success, log_text, failure_reason = await _execute_script_content_locally(
            "import sys\nprint(\"'tool' is not recognized as an internal or external command,\", file=sys.stderr)\nprint('operable program or batch file.', file=sys.stderr)\nsys.exit(0)\n",
            "python",
            {},
            10,
            "fake-success.py",
        )

        self.assertFalse(success)
        self.assertIn("not recognized as an internal or external command", failure_reason)
        self.assertIn("operable program or batch file", failure_reason)
        self.assertIn("operable program or batch file", log_text)

    async def test_zero_exit_with_quartus_error_output_is_treated_as_failure(self):
        success, log_text, failure_reason = await _execute_script_content_locally(
            "import sys\nprint('Error (213013): Programming hardware cable not detected')\nsys.exit(0)\n",
            "python",
            {},
            10,
            "quartus-fake-success.py",
        )

        self.assertFalse(success)
        self.assertIn("Error (213013): Programming hardware cable not detected", failure_reason)
        self.assertIn("Programming hardware cable not detected", log_text)

    async def test_quartus_chain_broken_output_beats_generic_vendor_wrapper_error(self):
        success, _log_text, failure_reason = await _execute_script_content_locally(
            "import sys\nprint('Unable to read device chain (JTAG chain broken)')\nprint('[ERROR] Altera Blaster II operation failed; inspect task parameters and preceding output.')\nsys.exit(2)\n",
            "python",
            {},
            10,
            "quartus-chain-broken.py",
        )

        self.assertFalse(success)
        self.assertIn("Unable to read device chain", failure_reason)
        self.assertIn("JTAG chain broken", failure_reason)
        self.assertNotEqual(failure_reason, "Altera Blaster II operation failed; inspect task parameters and preceding output.")

    async def test_missing_executable_reports_tool_and_configuration_hint(self):
        ok, _stdout, _stderr, reason = await _run_subprocess_command(
            ["pcids-command-that-does-not-exist"],
            timeout_seconds=1,
        )

        self.assertFalse(ok)
        self.assertIn("pcids-command-that-does-not-exist", reason)
        self.assertIn("工具路径配置", reason)

    def test_exception_log_keeps_existing_output_and_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            log_text = _build_task_exception_log(
                "脚本执行异常: boom",
                existing_log="=== 执行脚本 ===\na.py",
                live_output="实时输出第一行",
                exc=exc,
                include_traceback=True,
            )

        self.assertIn("=== 执行脚本 ===", log_text)
        self.assertIn("实时输出第一行", log_text)
        self.assertIn("=== 异常详情 ===", log_text)
        self.assertIn("boom", log_text)
        self.assertIn("=== 异常堆栈 ===", log_text)
        self.assertIn("ValueError", log_text)

    async def test_non_zero_script_with_explicit_timeout_marker_is_treated_as_timeout(self):
        success, log_text, failure_reason = await _execute_script_content_locally(
            "@echo off\r\necho [ERROR] 脚本执行超时\r\nexit /b 124\r\n",
            "bat",
            {"TIMEOUT_SECONDS": "120"},
            120,
            "timeout-marker.bat",
        )

        self.assertFalse(success)
        self.assertEqual(failure_reason, "脚本执行超时")
        self.assertIn("[ERROR] 脚本执行超时", log_text)

        self.assertIn("=== Exit Code ===\n124", log_text)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_batch_script_accepts_unicode_outside_gbk(self):
        success, log_text, failure_reason = await _execute_script_content_locally(
            "@echo off\r\necho [ERROR] unicode replacement: �\r\nexit /b 2\r\n",
            "bat",
            {},
            10,
            "unicode-batch.bat",
        )

        self.assertFalse(success)
        self.assertIn("unicode replacement: �", failure_reason)
        self.assertIn("unicode replacement: �", log_text)

    def test_timeout_summary_preserves_existing_log_output(self):
        log_text = _decorate_timeout_log("=== 执行脚本 ===\nfoo.bat\n=== 脚本输出 ===\nstep-1", 12)

        self.assertIn("脚本执行超时：已超过任务超时时间 12 秒", log_text)
        self.assertIn("=== 执行脚本 ===", log_text)
        self.assertIn("step-1", log_text)

    def test_pwlink_mixed_output_decoder_keeps_gbk_and_utf8_lines(self):
        raw = b"\r\n".join(
            [
                "[ERROR] 未发现指定 probe: BURNER_SN=PW-001".encode("gb18030"),
                "[INFO] pyOCD helper: 烧录完成".encode("utf-8"),
            ]
        )

        decoded = _decode_mixed_subprocess_output(raw)

        self.assertIn("未发现指定 probe", decoded)
        self.assertIn("烧录完成", decoded)
        self.assertIn("BURNER_SN=PW-001", decoded)

    def test_only_pwlink_uses_mixed_output_decoder(self):
        self.assertIs(_resolve_subprocess_output_decoder("pwlink_v2_arm_mcu_flash"), _decode_mixed_subprocess_output)
        self.assertIs(_resolve_subprocess_output_decoder("stlink_arm_mcu_flash"), _decode_subprocess_output)

    def test_al321_flash_retry_keeps_driver_mode_between_attempts(self):
        self.assertFalse(
            _should_restore_burner_environment(
                "al321_fpga_mcu_flash",
                {"EXECUTION_OPERATION_MODE": "flash", "PCIDS_FINAL_ATTEMPT": "0"},
                script_succeeded=False,
                script_started=True,
            )
        )

    def test_al321_flash_restores_after_final_attempt_or_success(self):
        self.assertTrue(
            _should_restore_burner_environment(
                "al321_fpga_mcu_flash",
                {"EXECUTION_OPERATION_MODE": "flash", "PCIDS_FINAL_ATTEMPT": "1"},
                script_succeeded=False,
                script_started=True,
            )
        )
        self.assertTrue(
            _should_restore_burner_environment(
                "al321_fpga_mcu_flash",
                {"EXECUTION_OPERATION_MODE": "flash", "PCIDS_FINAL_ATTEMPT": "0"},
                script_succeeded=True,
                script_started=True,
            )
        )

    def test_non_al321_or_non_flash_keeps_existing_restore_behavior(self):
        self.assertTrue(
            _should_restore_burner_environment(
                "pwlink_v2_arm_mcu_flash",
                {"EXECUTION_OPERATION_MODE": "flash", "PCIDS_FINAL_ATTEMPT": "0"},
                script_succeeded=False,
                script_started=True,
            )
        )
        self.assertTrue(
            _should_restore_burner_environment(
                "al321_fpga_mcu_flash",
                {"EXECUTION_OPERATION_MODE": "sram", "PCIDS_FINAL_ATTEMPT": "0"},
                script_succeeded=False,
                script_started=True,
            )
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_pwlink_script_header_tolerates_usb_binding_metacharacters(self):
        script = build_system_script_content("pwlink_v2_arm_mcu_flash", "PWLINK2")
        success, log_text, failure_reason = await _execute_script_content_locally(
            script,
            "bat",
            {
                "TASK_ID": "pwlink-header-metachar",
                "TASK_TYPE": "board",
                "BURNER_NAME": "PWLINK2",
                "BURNER_TYPE": "PWLINK2",
                "BURNER_SN": "427427618AA11689D7012DB4818082D1",
                "BURNER_PORT": "0000.0014.0000.014.004.004.003.000.000",
                "BURNER_LOCATION": r"USB\\VID_0D28&PID_0204\\9&C61236F&0",
                "TARGET_CHIP": "STM32F107VCT6",
                "FIRMWARE_PATH": __file__,
                "IDE_NAME": "Keil uVision",
                "INTERFACE_TYPE": "SWD",
                "WRITE_SPEED_KHZ": "1000",
                "ERASE_MODE": "全片擦除",
                "COMPLETION_ACTION": "复位运行",
                "WRITE_VERIFY": "1",
                "TIMEOUT_SECONDS": "10",
                "PYOCD_EXE": sys.executable,
                "PYOCD_PYTHON": sys.executable,
            },
            10,
            "pwlink_v2_arm_mcu_flash",
        )

        self.assertFalse(success)
        self.assertNotIn("'burner' is not recognized", log_text)
        self.assertNotIn("if was unexpected at this time.", log_text)
        self.assertNotIn("unexpected at this time", failure_reason)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_hdsc_script_uses_clean_failure_when_agent_path_is_missing(self):
        script = build_system_script_content("hdsc_ccid_arm_mcu_flash", "HDSC CCID")
        success, log_text, failure_reason = await _execute_script_content_locally(
            script,
            "bat",
            {
                "TASK_ID": "hdsc-missing-agent",
                "TASK_TYPE": "board",
                "BURNER_NAME": "HDSC CCID Writer 0",
                "BURNER_TYPE": "HDSC CCID",
                "BURNER_PORT": "Port_#0003.Hub_#0002",
                "TARGET_CHIP": "HC32L130J8TA",
                "FIRMWARE_PATH": __file__,
                "IDE_NAME": "HDSC ISP",
                "INTERFACE_TYPE": "UART",
                "WRITE_SPEED_KHZ": "115200",
                "ERASE_MODE": "全片擦除",
                "COMPLETION_ACTION": "复位运行",
                "HDSC_ERASE_MODE_KEY": "chip",
                "HDSC_COMPLETION_ACTION_KEY": "reset-run",
                "WRITE_VERIFY": "1",
                "TIMEOUT_SECONDS": "10",
                "HDSC_CCID_AGENT": r"C:\pcids\missing\hdsc_ccid_agent.py",
            },
            10,
            "hdsc_ccid_arm_mcu_flash",
        )

        self.assertFalse(success)
        self.assertNotIn("not recognized as an internal or external command", log_text)
        self.assertNotIn("或批处理文件", failure_reason)
        self.assertIn("HDSC CCID agent not found", log_text)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_altera_script_is_ascii_safe_before_quartus_executes(self):
        script = build_system_script_content("altera_blaster_ii_cpld_flash", "Altera Blaster II")
        success, log_text, failure_reason = await _execute_script_content_locally(
            script,
            "bat",
            {
                "TASK_ID": "altera-ascii-safety",
                "TASK_TYPE": "board",
                "BURNER_NAME": "Altera USB-Blaster II (JTAG interface)",
                "BURNER_TYPE": "Altera Blaster II",
                "BURNER_SN": "UBII-000010460",
                "BURNER_PORT": "0000.0014.0000.014.004.004.004.000.000",
                "BURNER_LOCATION": "0000.0014.0000.014.004.003.000.000.000",
                "TARGET_CHIP": "EPM7064AE",
                "FIRMWARE_PATH": __file__,
                "IDE_NAME": "Intel Quartus Programmer",
                "INTERFACE_TYPE": "JTAG",
                "TCK_FREQUENCY": "2.5MHz",
                "WRITE_VERIFY": "1",
                "TIMEOUT_SECONDS": "10",
                "QUARTUS_PGM": r"C:\missing\quartus_pgm.exe",
            },
            10,
            "altera_blaster_ii_cpld_flash",
        )

        self.assertFalse(success)
        self.assertNotIn("not recognized as an internal or external command", log_text)
        self.assertNotIn("不是内部或外部命令", failure_reason)
        self.assertIn("Altera Blaster II operation failed", log_text)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_xds_script_is_ascii_safe_before_dss_validation_runs(self):
        script = build_system_script_content("xds510plus_dsp_flash", "XDS510plus")
        with tempfile.TemporaryDirectory() as temp_dir:
            ccxml_path = Path(temp_dir) / "seed_xds510plus_f28335.ccxml"
            ccxml_path.write_text(
                "<config>SEED-XDS510PLUS_Connection.xml seedxds510plusc28x.xml</config>",
                encoding="utf-8",
            )
            success, log_text, failure_reason = await _execute_script_content_locally(
                script,
                "bat",
                {
                    "TASK_ID": "xds-ascii-safety",
                    "TASK_TYPE": "board",
                    "BURNER_NAME": "SEED USB2.0 PLUS Emulator",
                    "BURNER_TYPE": "XDS510plus",
                    "BURNER_PORT": "Port_#0001.Hub_#0003",
                    "TARGET_CHIP": "TMS320F28335PGFA",
                    "FIRMWARE_PATH": __file__,
                    "IDE_NAME": "Code Composer Studio",
                    "INTERFACE_TYPE": "JTAG",
                    "WRITE_VERIFY": "1",
                    "TIMEOUT_SECONDS": "10",
                    "TARGET_CONFIG_FILE": str(ccxml_path),
                    "DSS_BAT": r"C:\missing\dss.bat",
                },
                10,
                "xds510plus_dsp_flash",
            )

        self.assertFalse(success)
        self.assertNotIn("not recognized as an internal or external command", log_text)
        self.assertNotIn("不是内部或外部命令", failure_reason)
        self.assertIn("DSS launcher not found", failure_reason)

    @unittest.skipUnless(sys.platform == "win32", "Windows batch behavior")
    async def test_mplab_script_is_ascii_safe_before_ipecmd_validation_runs(self):
        script = build_system_script_content("mplab_icd3_pic_flash", "MPLAB ICD 3 DV164035")
        success, log_text, failure_reason = await _execute_script_content_locally(
            script,
            "bat",
            {
                "TASK_ID": "mplab-ascii-safety",
                "TASK_TYPE": "board",
                "BURNER_NAME": "MPLAB ICD 3 DV164035",
                "BURNER_TYPE": "MPLAB ICD 3 DV164035",
                "BURNER_SN": "20220127",
                "BURNER_PORT": "Port_#0001.Hub_#0001",
                "BURNER_LOCATION": r"USB\\VID_04D8&PID_9009\\BUR184572334",
                "TARGET_CHIP": "30F6011A",
                "FIRMWARE_PATH": __file__,
                "IDE_NAME": "MPLAB",
                "ERASE_MODE": "全片擦除",
                "MPLAB_ERASE_MODE_KEY": "chip",
                "EEPROM_WRITE": "否",
                "MPLAB_EEPROM_WRITE_KEY": "no",
                "BLANK_CHECK": "否",
                "MPLAB_BLANK_CHECK_KEY": "no",
                "EXECUTE_PROGRAM": "是",
                "MPLAB_EXECUTE_PROGRAM_KEY": "yes",
                "COMPLETION_ACTION": "编程复位后运行",
                "MPLAB_COMPLETION_ACTION_KEY": "reset-run",
                "WRITE_VERIFY": "1",
                "TIMEOUT_SECONDS": "10",
                "IPECMD_EXE": r"C:\missing\ipecmd.exe",
            },
            10,
            "mplab_icd3_pic_flash",
        )

        self.assertFalse(success)
        self.assertNotIn("not recognized as an internal or external command", log_text)
        self.assertNotIn("不是内部或外部命令", failure_reason)
        self.assertIn("MPLAB IPE ipecmd.exe was not found", failure_reason)


if __name__ == "__main__":
    unittest.main()
