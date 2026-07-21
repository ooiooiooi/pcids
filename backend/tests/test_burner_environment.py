import unittest
from unittest.mock import patch

from backend.utils.burner_environment import ensure_burner_environment, restore_burner_environment


class BurnerEnvironmentTests(unittest.TestCase):
    @patch("backend.utils.burner_environment._gowin_environment", return_value="gowin-ready")
    def test_gowin_uses_code_level_environment_switcher(self, switcher):
        result = ensure_burner_environment(
            "gowin_usb_cable_fpga_flash",
            {"BURNER_NAME": "Gowin USB Cable", "BURNER_LOCATION": "registered-location"},
        )
        self.assertIn("=== 烧录器环境检查 ===", result)
        self.assertIn("目标环境：Gowin USB 模式（WinUSB）", result)
        self.assertIn("gowin-ready", result)
        switcher.assert_called_once()

    @patch("backend.utils.burner_environment._run_powershell", return_value="gowin-ready")
    def test_gowin_uses_port_when_location_is_a_placeholder(self, runner):
        ensure_burner_environment(
            "gowin_usb_cable_fpga_flash",
            {"BURNER_NAME": "Gowin USB Cable", "BURNER_LOCATION": "-", "BURNER_PORT": "Port_#0002.Hub_#0003"},
        )
        self.assertIn("Port_#0002.Hub_#0003", runner.call_args.args[1])

    @patch("backend.utils.burner_environment._al321_environment", return_value="al321-ready")
    def test_al321_uses_existing_environment_switcher(self, switcher):
        result = ensure_burner_environment(
            "al321_fpga_mcu_flash",
            {"BURNER_NAME": "AL321", "EXECUTION_OPERATION": "Flash固化"},
        )
        self.assertIn("目标环境：AMD/JTAG 驱动环境", result)
        self.assertIn("al321-ready", result)
        switcher.assert_called_once()

    def test_fixed_environment_burner_still_runs_preflight(self):
        result = ensure_burner_environment("pwlink_v2_arm_mcu_flash", {"BURNER_NAME": "PWLINK2"})
        self.assertIn("烧录器：PWLINK2", result)
        self.assertIn("当前环境：固定专用环境", result)
        self.assertIn("处理结果：环境匹配，无需切换", result)

    @patch("backend.utils.burner_environment._run_powershell", return_value="restored")
    def test_al321_is_restored_after_execution(self, runner):
        result = restore_burner_environment("al321_fpga_mcu_flash", {"TASK_ID": "42"})
        self.assertEqual(result, "restored")
        self.assertIn("winusb", runner.call_args.args[1])

    def test_fixed_environment_burner_needs_no_restore(self):
        self.assertEqual(restore_burner_environment("pwlink_v2_arm_mcu_flash", {"BURNER_NAME": "PWLINK2"}), "")


if __name__ == "__main__":
    unittest.main()
