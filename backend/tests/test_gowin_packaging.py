import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class GowinPackagingTests(unittest.TestCase):
    def _assert_gowin_resource(self, resources):
        matches = [item for item in resources if item.get("to") == "tools/burners/GOWIN"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].get("from"), "tools/burners/GOWIN")
        self.assertEqual(matches[0].get("filter"), ["bin/**/*", "driver/**/*", "drivers/**/*"])

    def _assert_al321_resource(self, resources):
        matches = [item for item in resources if item.get("to") == "tools/burners/AL321"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].get("from"), "tools/burners/AL321")
        self.assertEqual(matches[0].get("filter"), ["drivers/**/*", "openFPGALoader/**/*", "run-program-flash-stream.ps1"])

    def test_standard_package_contains_gowin_mode_switcher(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self._assert_gowin_resource(package["build"]["extraResources"])
        self._assert_al321_resource(package["build"]["extraResources"])

    def test_win7_private_package_contains_gowin_mode_switcher(self):
        package = json.loads((ROOT / "electron-builder.win7-private.json").read_text(encoding="utf-8"))
        self._assert_gowin_resource(package["extraResources"])
        self._assert_al321_resource(package["extraResources"])


if __name__ == "__main__":
    unittest.main()
