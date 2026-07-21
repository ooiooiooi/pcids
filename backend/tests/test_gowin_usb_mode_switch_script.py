import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tools" / "burners" / "GOWIN" / "drivers" / "switch-gowin-usb-mode.ps1"


class GowinUsbModeSwitchScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_targets_only_the_configured_binding_without_a_hardware_id_fallback(self):
        self.assertNotIn('VID_33AA&PID_0120', self.content)
        self.assertIn('BURNER_SN or BURNER_LOCATION binding', self.content)
        self.assertIn('matching the configured BURNER_SN/BURNER_LOCATION binding', self.content)
        self.assertIn('if ($Serial)', self.content)
        self.assertIn('if ($devices.Count -ne 1 -and $InstanceAnchor)', self.content)
        self.assertIn('DEVPKEY_Device_LocationInfo', self.content)
        self.assertIn('$InstanceAnchor -like "Port_#*"', self.content)

    def test_skips_a_valid_usb_mode_and_switches_an_invalid_mode(self):
        self.assertIn('$current.Service -ieq "WinUSB"', self.content)
        self.assertIn('already uses USB mode (WinUSB); no switch is required', self.content)
        self.assertIn('VID_0403&PID_6014', self.content)
        self.assertIn('FT2CH cable already uses the required FTDI driver (FTDIBUS)', self.content)
        self.assertIn('Gowin device detected:', self.content)
        self.assertIn('Switching Gowin cable to USB mode', self.content)
        self.assertIn('GOWIN_USB_DRIVER_INF', self.content)
        self.assertIn('-Pattern "Gowin" -Quiet', self.content)
        self.assertIn('-Pattern "WinUSB" -Quiet', self.content)

    def test_persists_interrupted_switch_state_and_rechecks_usb_mode(self):
        self.assertIn('State = "pending_usb"', self.content)
        self.assertIn('[ValidateSet("usb", "recover-pending")]', self.content)
        self.assertIn('Set-GowinUsbMode', self.content)


if __name__ == "__main__":
    unittest.main()
