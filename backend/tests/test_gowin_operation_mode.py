import unittest

from backend.utils.burner_automation import SYSTEM_SCRIPT_CATALOG
from backend.utils.task_execution import build_runtime_env


class GowinOperationModeTests(unittest.TestCase):
    def test_completion_action_is_selectable_between_none_and_reset(self):
        item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "gowin_usb_cable_fpga_flash")
        self.assertEqual(item["default_config"]["completion_action"], "不处理")
        self.assertEqual(item["default_config"]["completion_action_options"], ["不处理", "复位"])

    def test_runtime_env_uses_ascii_mode_for_sram_and_flash(self):
        self.assertEqual(
            _runtime_env_for("SRAM下载")["EXECUTION_OPERATION_MODE"],
            "sram",
        )
        self.assertEqual(
            _runtime_env_for("Flash固化")["EXECUTION_OPERATION_MODE"],
            "flash",
        )
        self.assertEqual(_runtime_env_for("Flash固化")["GOWIN_OPERATION_MODE"], "flash")

    def test_runtime_env_uses_ascii_completion_action_for_gowin(self):
        self.assertEqual(_runtime_env_for("SRAM下载", "不处理")["GOWIN_COMPLETION_ACTION_MODE"], "none")
        self.assertEqual(_runtime_env_for("SRAM下载", "复位")["GOWIN_COMPLETION_ACTION_MODE"], "reset")
        self.assertEqual(_runtime_env_for("SRAM下载", "reset")["GOWIN_COMPLETION_ACTION_MODE"], "reset")


def _runtime_env_for(operation: str, completion_action: str = "不处理"):
    class Task:
        id = 1
        target_ip = None
        target_port = None
        repository_id = None
        board_name = None
        product_id = None
        burner_id = None

    class Script:
        id = 12
        name = "gowin_usb_cable_fpga_flash"
        type = "bat"

    class Burner:
        name = "Gowin USB Cable"
        type = "Gowin USB Cable"
        sn = None
        port = None
        location = None

    return build_runtime_env(
        Task(),
        {"execution_operation": operation, "completion_action": completion_action, "write_verify": True},
        None,
        Burner(),
        Script(),
        None,
    )


if __name__ == "__main__":
    unittest.main()
