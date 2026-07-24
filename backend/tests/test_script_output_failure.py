import unittest

from backend.routers.tasks import _script_output_failure_reason


class ScriptOutputFailureTests(unittest.TestCase):
    def test_explicit_error_line_marks_script_as_failed(self):
        reason = _script_output_failure_reason(
            "[INFO] start\n[ERROR] missing programmer CLI\n",
            "",
        )

        self.assertEqual(reason, "missing programmer CLI")

    def test_pcids_error_marker_marks_script_as_failed(self):
        reason = _script_output_failure_reason(
            "",
            "__PCIDS_FLASH_ERROR__:device verification failed",
        )

        self.assertEqual(reason, "device verification failed")

    def test_seed_xds510_failure_marker_marks_zero_exit_script_as_failed(self):
        reason = _script_output_failure_reason(
            "SEED_XDS510_CONNECT_BEGIN\n"
            "SEED_XDS510_WORKFLOW_FAILED: JavaException: Error connecting to the target: emulation failure occurred\n",
            "",
        )

        self.assertEqual(
            reason,
            "JavaException: Error connecting to the target: emulation failure occurred",
        )

    def test_seed_xds510_controller_connection_error_has_actionable_reason(self):
        reason = _script_output_failure_reason(
            "SEED_XDS510_WORKFLOW_FAILED: Error connecting\n",
            "SEVERE: C28xx: Error connecting to the target: "
            "(Error -342 @ 0x0) Failure due to the controller command-finish taking too long.",
        )

        self.assertIn("CCS Error -342", reason)
        self.assertIn("烧录尚未开始", reason)

    def test_multiple_error_lines_keep_reason_and_action(self):
        reason = _script_output_failure_reason(
            "[ERROR] 未检测到烧录器\n[ERROR] 请检查 USB 连接和驱动\n",
            "",
        )

        self.assertEqual(reason, "未检测到烧录器；请检查 USB 连接和驱动")

    def test_duplicate_error_lines_are_not_repeated(self):
        reason = _script_output_failure_reason(
            "[ERROR] 目标板未上电\n[ERROR] 目标板未上电\n",
            "",
        )

        self.assertEqual(reason, "目标板未上电")

    def test_normal_output_is_not_failed(self):
        reason = _script_output_failure_reason(
            "[INFO] programming completed\n[success] verification passed",
            "",
        )

        self.assertEqual(reason, "")

    def test_jlink_dap_failure_reports_actionable_error_instead_of_tail_line(self):
        reason = _script_output_failure_reason(
            "SEGGER J-Link Commander V9.52\n"
            "Connecting to J-Link ...O.K.\n"
            "VTref=3.417V\n"
            "J-Link>connect\n"
            "Error: Failed to initialize DAP.\n"
            "Can not attach to CPU.\n"
            "Error occurred: Could not connect to the target device.\n"
            "Script processing completed.\n",
            "",
        )

        self.assertIn("J-Link \u65E0\u6CD5\u521D\u59CB\u5316 SWD DAP", reason)
        self.assertIn("VTref=3.417V", reason)
        self.assertIn("SWDIO/SWCLK/NRST/GND", reason)
        self.assertNotIn("Script processing completed", reason)

    def test_stlink_idcode_failure_beats_unsupported_retry_frequency_tail(self):
        reason = _script_output_failure_reason(
            "[INFO] Associated burner type: ST-LINK\n"
            "0000425 C STLink error (9): Get IDCODE error [__main__]\n"
            "0000408 C Selected SWD frequency is too low [__main__]\n",
            "",
        )

        self.assertIn("ST-LINK \u65E0\u6CD5\u8BFB\u53D6\u76EE\u6807\u82AF\u7247 IDCODE", reason)
        self.assertIn("SWDIO/SWCLK/NRST/GND", reason)
        self.assertNotIn("\u65F6\u949F\u8FC7\u4F4E", reason)

    def test_stlink_utility_no_target_reports_wiring_and_target_power(self):
        reason = _script_output_failure_reason(
            "STM32 ST-LINK CLI v3.6.0.0\n"
            "STM32 ST-LINK Command Line Interface\n"
            "No target connected\n"
            "Unable to connect to ST-LINK!\n",
            "",
        )

        self.assertIn("烧录器已识别", reason)
        self.assertIn("未检测到目标 STM32 芯片", reason)
        self.assertIn("VREF", reason)
        self.assertIn("SWDIO", reason)
        self.assertIn("SWCLK", reason)
        self.assertIn("NRST", reason)

    def test_agent_json_error_is_decoded_for_task_failure_reason(self):
        reason = _script_output_failure_reason(
            '{"ok": false, "error": "PreISP target connect failed: \\u4e0b\\u8f7dRamCode\\u51fa\\u9519"}',
            "",
        )

        self.assertEqual(reason, "PreISP target connect failed: 下载RamCode出错")


if __name__ == "__main__":
    unittest.main()
