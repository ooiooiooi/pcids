import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class GowinPackagingTests(unittest.TestCase):
    def _assert_burner_and_protocol_tools_external(self, resources):
        for item in resources:
            source = str(item.get("from") or "").replace("\\", "/")
            destination = str(item.get("to") or "").replace("\\", "/")
            self.assertFalse(source.startswith("tools/burners/"))
            self.assertFalse(source.startswith("tools/protocol_adapters/"))
            self.assertFalse(destination.startswith("tools/burners/"))
            self.assertFalse(destination.startswith("tools/protocol_adapters/"))

    def test_standard_package_keeps_burner_and_protocol_tools_external(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self._assert_burner_and_protocol_tools_external(package["build"]["extraResources"])

    def test_win7_web_package_only_bundles_codearts_runtime(self):
        package = json.loads((ROOT / "electron-builder.win7-private.json").read_text(encoding="utf-8"))
        resources = package["extraResources"]
        self._assert_burner_and_protocol_tools_external(resources)
        web_runtime = [
            item
            for item in resources
            if str(item.get("to") or "").replace("\\", "/") == "tools/codearts_browser_runtime"
        ]
        self.assertEqual(len(web_runtime), 1)
        self.assertEqual(
            web_runtime[0].get("from"),
            "tools/codearts_release_debugger/browser_runtime",
        )


if __name__ == "__main__":
    unittest.main()
