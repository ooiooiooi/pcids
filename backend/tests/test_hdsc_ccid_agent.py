import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


AGENT_PATH = Path(__file__).resolve().parents[2] / "tools" / "burners" / "HDSC" / "hdsc_ccid_agent.py"
SPEC = importlib.util.spec_from_file_location("hdsc_ccid_agent", AGENT_PATH)
assert SPEC and SPEC.loader
hdsc_ccid_agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hdsc_ccid_agent
SPEC.loader.exec_module(hdsc_ccid_agent)


class HdscCcidAgentTests(unittest.TestCase):
    def test_v604_host_uses_plaintext_intel_hex_converter(self):
        host = AGENT_PATH.with_name("hdsc_v604_host.ps1").read_text(encoding="utf-8")

        self.assertIn("DftFile_Convert2List", host)
        self.assertIn("$_.Name -eq 'DftFile_Convert2List'", host)
        self.assertIn("PowerOn_3V3", host)
        self.assertIn("PowerOff", host)
        self.assertIn("$operationSucceeded = $false", host)
        self.assertIn("if (-not $operationSucceeded)", host)
        self.assertIn("Start-Sleep -Milliseconds 500", host)
        self.assertIn("$effectiveBaud = [int]$supportedBauds[$effectiveBaudIndex]", host)
        self.assertIn("'ISP_ConnectAndPps' @($effectiveBaud)", host)
        self.assertNotIn("'ISP_ConnectAndPps' @($effectiveBaudIndex)", host)
        self.assertIn("PreISP_ConnectAndPps", host)
        self.assertIn("PreISP_Scripts_DownLoadRamcodeAndRun", host)
        self.assertIn("'PreISP_Script_Exe'", host)
        self.assertIn("'ISP_Script_Exe'", host)
        self.assertNotIn("Invoke-McuBool $mcu 'PreISP_ScriptList_Exe'", host)
        self.assertNotIn("Invoke-McuBool $mcu 'ISP_ScriptList_Exe'", host)
        self.assertLess(host.index("PreISP_ConnectAndPps"), host.index("'ISP_ConnectAndPps' @($effectiveBaud)"))

    def test_profile_rejects_a_target_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "hc32l130.json"
            profile.write_text(
                json.dumps(
                    {
                        "format": hdsc_ccid_agent.PROFILE_FORMAT,
                        "target": "HC32L130J8TA",
                        "operations": [{"name": "query", "apdu": "F9", "expect_status": ["9000"]}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(hdsc_ccid_agent.ProfileError):
                hdsc_ccid_agent.load_operation_profile(profile, "HC32L110")

    def test_profile_accepts_spaced_hex_and_expected_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "hc32l130.json"
            profile.write_text(
                json.dumps(
                    {
                        "format": hdsc_ccid_agent.PROFILE_FORMAT,
                        "target": "HC32L130J8TA",
                        "operations": [{"name": "firmware", "apdu": "F9", "expect_status": ["9000", "6A80"]}],
                    }
                ),
                encoding="utf-8",
            )
            loaded = hdsc_ccid_agent.load_operation_profile(profile, "hc32l130j8ta")
            self.assertEqual(loaded.operations[0].apdu, bytes.fromhex("F9"))
            self.assertEqual(loaded.operations[0].expect_status, (0x9000, 0x6A80))

    def test_status_word_requires_two_response_bytes(self):
        with self.assertRaises(hdsc_ccid_agent.ProfileError):
            hdsc_ccid_agent._status_word(b"\x90")


if __name__ == "__main__":
    unittest.main()
