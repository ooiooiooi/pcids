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


if __name__ == "__main__":
    unittest.main()
