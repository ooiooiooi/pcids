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

    def test_normal_output_is_not_failed(self):
        reason = _script_output_failure_reason(
            "[INFO] programming completed\n[success] verification passed",
            "",
        )

        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
