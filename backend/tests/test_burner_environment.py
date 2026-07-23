import unittest
from pathlib import Path
from unittest.mock import patch

from backend.utils.burner_environment import _decode_powershell_output, ensure_burner_environment, restore_burner_environment


class BurnerEnvironmentTests(unittest.TestCase):
    def test_powershell_output_decoder_keeps_gb18030_chinese_diagnostics(self):
        message = "Gowin USB 驱动切换失败"
        self.assertEqual(_decode_powershell_output(message.encode("gb18030")), message)

    def test_powershell_output_decoder_keeps_utf8_diagnostics(self):
        message = "烧录器环境检查完成"
        self.assertEqual(_decode_powershell_output(message.encode("utf-8")), message)

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

    @patch.dict("os.environ", {"PCIDS_BUNDLED_TOOLS_DIR": r"C:\\Program Files\\PCIDS\\resources\\tools\\burners"}, clear=False)
    @patch("backend.utils.burner_environment._run_powershell", return_value="gowin-ready")
    def test_gowin_uses_bundled_tool_script_in_packaged_app(self, runner):
        ensure_burner_environment(
            "gowin_usb_cable_fpga_flash",
            {"BURNER_NAME": "Gowin USB Cable", "BURNER_LOCATION": "registered-location"},
        )
        self.assertEqual(
            runner.call_args.args[0],
            Path(r"C:\\Program Files\\PCIDS\\resources\\tools\\burners\\GOWIN\\drivers\\switch-gowin-usb-mode.ps1"),
        )

    @patch("backend.utils.burner_environment._al321_environment", return_value="al321-ready")
    def test_al321_uses_existing_environment_switcher(self, switcher):
        result = ensure_burner_environment(
            "al321_fpga_mcu_flash",
            {"BURNER_NAME": "AL321", "EXECUTION_OPERATION": "Flash固化"},
        )
        self.assertIn("目标环境：AMD/JTAG 驱动环境", result)
        self.assertIn("al321-ready", result)
        switcher.assert_called_once()

    @patch("backend.utils.burner_environment._run_powershell", return_value="al321-ready")
    def test_al321_uses_ascii_operation_mode_for_driver_switch(self, runner):
        ensure_burner_environment(
            "al321_fpga_mcu_flash",
            {"BURNER_NAME": "AL321", "EXECUTION_OPERATION_MODE": "flash"},
        )
        self.assertIn("amd", runner.call_args.args[1])

    @patch("backend.utils.burner_environment._run_powershell", return_value="al321-ready")
    def test_al321_sram_uses_winusb_mode(self, runner):
        ensure_burner_environment(
            "al321_fpga_mcu_flash",
            {"BURNER_NAME": "AL321", "EXECUTION_OPERATION_MODE": "sram", "BURNER_SN": "210512180081"},
        )
        self.assertIn("winusb", runner.call_args.args[1])
        self.assertIn("210512180081", runner.call_args.args[1])

    def test_fixed_environment_burner_still_runs_preflight(self):
        result = ensure_burner_environment("pwlink_v2_arm_mcu_flash", {"BURNER_NAME": "PWLINK2"})
        self.assertIn("烧录器：PWLINK2", result)
        self.assertIn("当前环境：固定专用环境", result)
        self.assertIn("处理结果：环境匹配，无需切换", result)

    @patch("backend.utils.burner_environment._run_powershell", return_value="restored")
    def test_al321_is_restored_after_execution(self, runner):
        result = restore_burner_environment("al321_fpga_mcu_flash", {"TASK_ID": "42", "BURNER_SN": "210512180081"})
        self.assertEqual(result, "restored")
        self.assertIn("winusb", runner.call_args.args[1])
        self.assertIn("210512180081", runner.call_args.args[1])

    def test_fixed_environment_burner_needs_no_restore(self):
        self.assertEqual(restore_burner_environment("pwlink_v2_arm_mcu_flash", {"BURNER_NAME": "PWLINK2"}), "")


if __name__ == "__main__":
    unittest.main()
