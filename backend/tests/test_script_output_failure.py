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

    def test_agent_json_error_is_decoded_for_task_failure_reason(self):
        reason = _script_output_failure_reason(
            '{"ok": false, "error": "PreISP target connect failed: \\u4e0b\\u8f7dRamCode\\u51fa\\u9519"}',
            "",
        )

        self.assertEqual(reason, "PreISP target connect failed: 下载RamCode出错")


if __name__ == "__main__":
    unittest.main()
