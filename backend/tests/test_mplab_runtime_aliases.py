import unittest

from backend.utils.task_execution import build_runtime_env


class MplabRuntimeAliasTests(unittest.TestCase):
    def test_runtime_env_exposes_ascii_aliases_for_mplab_batch_control_flow(self):
        env = _runtime_env_for("全片擦除", "否", "是", "是", "编程复位后运行")
        self.assertEqual(env["MPLAB_ERASE_MODE_KEY"], "chip")
        self.assertEqual(env["MPLAB_EEPROM_WRITE_KEY"], "no")
        self.assertEqual(env["MPLAB_BLANK_CHECK_KEY"], "yes")
        self.assertEqual(env["MPLAB_EXECUTE_PROGRAM_KEY"], "yes")
        self.assertEqual(env["MPLAB_COMPLETION_ACTION_KEY"], "reset-run")

        env = _runtime_env_for("不擦除直接编程", "是", "否", "否", "编程后保持复位")
        self.assertEqual(env["MPLAB_ERASE_MODE_KEY"], "no-erase")
        self.assertEqual(env["MPLAB_EEPROM_WRITE_KEY"], "yes")
        self.assertEqual(env["MPLAB_BLANK_CHECK_KEY"], "no")
        self.assertEqual(env["MPLAB_EXECUTE_PROGRAM_KEY"], "no")
        self.assertEqual(env["MPLAB_COMPLETION_ACTION_KEY"], "hold-reset")

    def test_runtime_env_derives_mplab_sn_from_usb_location_when_sn_is_missing(self):
        env = _runtime_env_for(
            "全片擦除",
            "否",
            "否",
            "是",
            "编程复位后运行",
            burner_sn="",
            burner_location=r"USB\VID_04D8&PID_9009\BUR184572334",
        )

        self.assertEqual(env["BURNER_SN"], "BUR184572334")


def _runtime_env_for(
    erase_mode: str,
    eeprom_write: str,
    blank_check: str,
    execute_program: str,
    completion_action: str,
    burner_sn: str = "20220127",
    burner_location: str = r"USB\VID_04D8&PID_9009\BUR184572334",
):
    class Task:
        id = 1
        target_ip = None
        target_port = None
        repository_id = None
        board_name = None
        product_id = None
        burner_id = None

    class Script:
        id = 21
        name = "mplab_icd3_pic_flash"
        type = "bat"

    class Burner:
        name = "MPLAB ICD 3 DV164035"
        type = "MPLAB ICD 3 DV164035"
        sn = burner_sn
        port = "Port_#0001.Hub_#0001"
        location = burner_location

    return build_runtime_env(
        Task(),
        {
            "erase_mode": erase_mode,
            "eeprom_write": eeprom_write,
            "blank_check": blank_check,
            "execute_program": execute_program,
            "completion_action": completion_action,
            "write_verify": True,
        },
        None,
        Burner(),
        Script(),
        __file__,
    )


if __name__ == "__main__":
    unittest.main()
