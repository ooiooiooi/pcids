import unittest

from backend.utils.task_execution import build_runtime_env


class GowinOperationModeTests(unittest.TestCase):
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


def _runtime_env_for(operation: str):
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
        {"execution_operation": operation, "write_verify": True},
        None,
        Burner(),
        Script(),
        None,
    )


if __name__ == "__main__":
    unittest.main()
