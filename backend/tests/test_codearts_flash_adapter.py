import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.utils.burner_automation import SYSTEM_SCRIPT_CATALOG, build_system_script_content


ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "scripts" / "pcids_flash.py"


class CodeArtsFlashAdapterTests(unittest.TestCase):
    def test_lists_every_system_burner_profile(self):
        result = subprocess.run(
            [sys.executable, str(ADAPTER), "list-profiles"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        profiles = [json.loads(line)["profile"] for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(set(profiles), {item["name"] for item in SYSTEM_SCRIPT_CATALOG})

    def test_each_windows_system_burner_has_a_generated_batch_script(self):
        for item in SYSTEM_SCRIPT_CATALOG:
            if item.get("task_type") == "hybrid" or item.get("type") != "bat":
                continue
            with self.subTest(profile=item["name"]):
                content = build_system_script_content(item["name"], item["burner"])
                self.assertTrue(content.strip())
                self.assertIn("FIRMWARE_PATH", content)
                self.assertIn("exit /b", content)

    def test_dry_run_writes_machine_readable_logs_without_running_hardware(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            firmware = root / "firmware.hex"
            firmware.write_text(":00000001FF\n", encoding="ascii")
            log_dir = root / "logs"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ADAPTER),
                    "run",
                    "--profile",
                    "mplab_icd3_pic_flash",
                    "--firmware",
                    str(firmware),
                    "--run-id",
                    "codearts-test",
                    "--log-dir",
                    str(log_dir),
                    "--dry-run",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(events[0]["event"], "started")
            self.assertEqual(events[-1]["event"], "completed")
            self.assertTrue((log_dir / "pcids-flash-codearts-test.log").is_file())
            self.assertTrue((log_dir / "pcids-flash-codearts-test.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
