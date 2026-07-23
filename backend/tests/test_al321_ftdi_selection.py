import unittest

from backend.utils.task_execution import build_runtime_env


class Al321FtdiSelectionTests(unittest.TestCase):
    def test_location_bound_ftdi_serial_is_not_inferred_for_al321_script(self):
        env = build_runtime_env(
            _Task(),
            {},
            None,
            _Burner(location=r"USB\VID_0403&PID_6014\210512180081"),
            _Script(),
            None,
        )

        self.assertEqual(env["BURNER_SN"], "")

    def test_supported_usb_location_is_not_limited_to_one_vid_pid(self):
        env = build_runtime_env(
            _Task(),
            {},
            None,
            _Burner(location=r"USB\VID_03FD&PID_0008\OTHER"),
            _Script(),
            None,
        )

        self.assertEqual(env["BURNER_SN"], "OTHER")

    def test_non_usb_location_does_not_create_a_serial(self):
        env = build_runtime_env(
            _Task(),
            {},
            None,
            _Burner(location="Port_#0002.Hub_#0004"),
            _Script(),
            None,
        )

        self.assertEqual(env["BURNER_SN"], "")


class _Task:
    id = 1
    target_ip = None
    target_port = None
    repository_id = None
    board_name = None
    product_id = None
    burner_id = 1


class _Script:
    id = 1
    name = "al321_fpga_mcu_flash"
    type = "bat"


class _Burner:
    name = "AL321"
    type = "AL321"
    sn = ""
    port = ""

    def __init__(self, location: str):
        self.location = location
