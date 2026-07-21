import unittest
from unittest.mock import patch

from backend.utils.burner_environment import ensure_burner_environment, restore_burner_environment


class BurnerEnvironmentTests(unittest.TestCase):
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
        self.assertIn("当前模式：由烧录器厂商 CLI 在脚本阶段探测", result)
        self.assertIn("不执行盲目驱动切换", result)

    @patch("backend.utils.burner_environment._xds510plus_environment", return_value="xds510-ready")
    def test_xds510_uses_dedicated_driver_mode_checker(self, checker):
        result = ensure_burner_environment(
            "xds510plus_dsp_flash",
            {"BURNER_NAME": "XDS510plus", "BURNER_PORT": "Port_#0001.Hub_#0003"},
        )
        self.assertIn("目标环境：SEED EZUSBPLUS 驱动模式", result)
        self.assertIn("不匹配时停止并提示用户手动处理", result)
        self.assertIn("xds510-ready", result)
        checker.assert_called_once()

    @patch("backend.utils.burner_environment._run_powershell", return_value="restored")
    def test_al321_is_restored_after_execution(self, runner):
        result = restore_burner_environment("al321_fpga_mcu_flash", {"TASK_ID": "42"})
        self.assertEqual(result, "restored")
        self.assertIn("winusb", runner.call_args.args[1])

    def test_fixed_environment_burner_needs_no_restore(self):
        self.assertEqual(restore_burner_environment("pwlink_v2_arm_mcu_flash", {"BURNER_NAME": "PWLINK2"}), "")


if __name__ == "__main__":
    unittest.main()
