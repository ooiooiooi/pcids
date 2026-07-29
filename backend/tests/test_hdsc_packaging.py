import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HdscPackagingTests(unittest.TestCase):
    def _assert_hdsc_runtime_external(self, resources):
        destinations = {str(item.get("to") or "").replace("\\", "/") for item in resources}
        sources = {str(item.get("from") or "").replace("\\", "/") for item in resources}
        self.assertIn("backend", destinations)
        self.assertTrue(all(not destination.startswith("tools/burners/") for destination in destinations))
        self.assertTrue(all(not source.startswith("tools/burners/HDSC") for source in sources))

    def test_standard_package_does_not_embed_hdsc_runtime(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self._assert_hdsc_runtime_external(package["build"]["extraResources"])

    def test_win7_private_package_does_not_embed_hdsc_runtime(self):
        package = json.loads((ROOT / "electron-builder.win7-private.json").read_text(encoding="utf-8"))
        self._assert_hdsc_runtime_external(package["extraResources"])


if __name__ == "__main__":
    unittest.main()
