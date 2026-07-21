import unittest
from pathlib import Path


class Xds510ModeScriptTests(unittest.TestCase):
    def setUp(self):
        self.content = (
            Path(__file__).resolve().parents[2]
            / "tools"
            / "burners"
            / "XDS510plus"
            / "drivers"
            / "ensure-xds510plus-mode.ps1"
        ).read_text(encoding="utf-8")

    def test_checks_seed_hardware_and_required_driver_service(self):
        self.assertIn('USB\\VID_0547&PID_1020', self.content)
        self.assertIn('Required driver mode: EZUSBPLUS', self.content)
        self.assertIn('Current driver mode:', self.content)

    def test_mismatched_mode_only_prompts_for_manual_recovery(self):
        self.assertIn('Action: no automatic driver rebinding', self.content)
        self.assertIn('Install or bind the approved driver manually', self.content)
        self.assertNotIn('install-xds510plus-driver.ps1', self.content)

    def test_multiple_seed_probes_require_an_exact_binding(self):
        self.assertIn('PCIDS_XDS510_LOCATION_MISMATCH', self.content)
        self.assertIn('MANUAL_UPDATE_REQUIRED', self.content)
        self.assertIn('PCIDS_XDS510_LOCATION_AMBIGUOUS', self.content)


if __name__ == "__main__":
    unittest.main()
