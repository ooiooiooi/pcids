import unittest

from backend.utils.task_execution import build_runtime_env


class HdscRuntimeAliasTests(unittest.TestCase):
    def test_runtime_env_exposes_ascii_aliases_for_hdsc_batch_control_flow(self):
        env = _runtime_env_for("全片擦除", "复位运行")
        self.assertEqual(env["HDSC_ERASE_MODE_KEY"], "")
        self.assertEqual(env["HDSC_COMPLETION_ACTION_KEY"], "")

        env = _runtime_env_for("不擦除直接编程", "不处理")
        self.assertEqual(env["HDSC_ERASE_MODE_KEY"], "none")
        self.assertEqual(env["HDSC_COMPLETION_ACTION_KEY"], "none")


def _runtime_env_for(erase_mode: str, completion_action: str):
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
        name = "hdsc_ccid_arm_mcu_flash"
        type = "bat"

    class Burner:
        name = "HDSC CCID Writer 0"
        type = "HDSC CCID"
        sn = None
        port = None
        location = None

    return build_runtime_env(
        Task(),
        {"erase_mode": erase_mode, "completion_action": completion_action, "write_verify": True},
        None,
        Burner(),
        Script(),
        None,
    )


if __name__ == "__main__":
    unittest.main()
