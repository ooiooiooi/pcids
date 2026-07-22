import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HdscPackagingTests(unittest.TestCase):
    def _assert_hdsc_resource(self, resources):
        matches = [item for item in resources if item.get("to") == "tools/burners/HDSC"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].get("from"), "tools/burners/HDSC")
        self.assertNotIn("filter", matches[0])
        self.assertTrue((ROOT / "tools" / "burners" / "HDSC" / "vendor" / "HDSC_CCID_Prog_Rev6.04" / "HDSC+CCID+Prog+REV6.04.exe").is_file())

    def test_standard_package_contains_hdsc_runtime(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self._assert_hdsc_resource(package["build"]["extraResources"])

    def test_win7_private_package_contains_hdsc_runtime(self):
        package = json.loads((ROOT / "electron-builder.win7-private.json").read_text(encoding="utf-8"))
        self._assert_hdsc_resource(package["extraResources"])


if __name__ == "__main__":
    unittest.main()
