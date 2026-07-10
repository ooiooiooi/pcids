import os
import subprocess
import tempfile
import unittest
import json
from pathlib import Path

from backend.utils.burner_automation import _pyocd_preflight_helper_source, build_system_script_content


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_PYOCD_ROOT = PROJECT_ROOT / "tools" / "burners" / "SWD_Downloader" / "pyocd-runtime"


@unittest.skipUnless(os.name == "nt", "Windows batch runtime tests require cmd.exe")
@unittest.skipUnless(BUNDLED_PYOCD_ROOT.exists(), "Bundled pyOCD runtime is required for compatibility tests")
class StrictPyocdRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pyocd_exe = next(BUNDLED_PYOCD_ROOT.rglob("pyocd.exe"))
        cls.python_exe = next(BUNDLED_PYOCD_ROOT.rglob("python.exe"))
        target_result = subprocess.run(
            [str(cls.python_exe), "-c", "import json; from pyocd.target import TARGET; print(json.dumps(sorted(TARGET.keys())))"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if target_result.returncode != 0:
            raise RuntimeError(target_result.stdout + target_result.stderr)
        cls.bundled_targets = set(json.loads(target_result.stdout))

    def _run_process(self, args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            args,
            cwd=str(cwd or PROJECT_ROOT),
            env=merged_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def _write_batch_script(self, temp_dir: Path, script_name: str, burner_name: str) -> Path:
        script_path = temp_dir / "run_script.bat"
        script_path.write_text(build_system_script_content(script_name, burner_name), encoding="utf-8")
        return script_path

    def _write_preflight_helper(self, temp_dir: Path) -> Path:
        helper_path = temp_dir / "pyocd_preflight_helper.py"
        helper_path.write_text(_pyocd_preflight_helper_source(), encoding="utf-8")
        return helper_path

    def _run_batch(self, script_path: Path, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
        return self._run_process(["cmd.exe", "/d", "/c", str(script_path)], cwd=cwd, env=env)

    def test_bundled_pyocd_reports_expected_version(self):
        result = self._run_process([str(self.pyocd_exe), "--version"])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "0.44.1")

    def test_bundled_pyocd_list_help_shows_supported_flags_and_rejects_json_probe_flag(self):
        help_result = self._run_process([str(self.pyocd_exe), "list", "--help"])

        self.assertEqual(help_result.returncode, 0, help_result.stdout + help_result.stderr)
        self.assertIn("--probes", help_result.stdout)
        self.assertIn("--targets", help_result.stdout)
        self.assertNotIn("--json", help_result.stdout)

        unsupported_result = self._run_process([str(self.pyocd_exe), "list", "--probes", "--json"])
        output = unsupported_result.stdout + unsupported_result.stderr

        self.assertNotEqual(unsupported_result.returncode, 0, output)
        self.assertIn("--json", output)

    def test_strict_batches_stop_at_missing_probe_without_cli_parameter_error(self):
        supported_target_chip = "STM32H743ZIT6"
        scripts = [
            ("stlink_stm32_mcu_flash", "ST-LINK"),
            ("pwlink_v2_arm_mcu_flash", "PWLINK2"),
            ("swd_downloader_arm_mcu_flash", "SWD Downloader"),
        ]

        for script_name, burner_name in scripts:
            with self.subTest(script_name=script_name), tempfile.TemporaryDirectory() as temp_name:
                temp_dir = Path(temp_name)
                script_path = self._write_batch_script(temp_dir, script_name, burner_name)
                firmware_path = temp_dir / "firmware.hex"
                firmware_path.write_text(":00000001FF\n", encoding="utf-8")

                result = self._run_batch(
                    script_path,
                    {
                        "PYOCD_EXE": str(self.pyocd_exe),
                        "PYOCD_PYTHON": str(self.python_exe),
                        "STM32_PROGRAMMER_CLI": str(temp_dir / "missing_stm32_programmer.exe"),
                        "TASK_ID": f"runtime-{script_name}",
                        "FIRMWARE_PATH": str(firmware_path),
                        "TARGET_CHIP": supported_target_chip,
                        "BURNER_SN": "NO_SUCH_PROBE_001",
                        "INTERFACE_TYPE": "SWD",
                        "ERASE_MODE": "扇区擦除",
                        "WRITE_SPEED_KHZ": "1000",
                        "COMPLETION_ACTION": "不处理",
                    },
                    temp_dir,
                )

                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 2, output)
                self.assertIn("pyOCD runtime version: 0.44.1", output)
                self.assertIn("未发现指定 probe", output)
                self.assertIn("preflight --target-chip", output)
                self.assertNotIn("unrecognized arguments: --json", output)

    def test_pwlink_accepts_stm32f107_alias_and_fails_only_on_missing_probe(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            script_path = self._write_batch_script(temp_dir, "pwlink_v2_arm_mcu_flash", "PWLINK2")
            firmware_path = temp_dir / "firmware.hex"
            firmware_path.write_text(":00000001FF\n", encoding="utf-8")

            result = self._run_batch(
                script_path,
                {
                    "PYOCD_EXE": str(self.pyocd_exe),
                    "PYOCD_PYTHON": str(self.python_exe),
                    "TASK_ID": "runtime-pwlink-stm32f107",
                    "FIRMWARE_PATH": str(firmware_path),
                    "TARGET_CHIP": "STM32F107VCT6",
                    "BURNER_SN": "NO_SUCH_PROBE_001",
                    "INTERFACE_TYPE": "SWD",
                    "ERASE_MODE": "扇区擦除",
                    "WRITE_SPEED_KHZ": "1000",
                    "COMPLETION_ACTION": "不处理",
                },
                temp_dir,
            )

            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 2, output)
            self.assertIn('preflight --target-chip "STM32F107VCT6"', output)
            self.assertIn("pyOCD runtime version: 0.44.1", output)
            self.assertIn("未发现指定 probe", output)
            self.assertNotIn("不支持的 pyOCD target", output)

    def test_preflight_helper_maps_supported_stm32_series_to_bundled_targets(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            helper_path = self._write_preflight_helper(temp_dir)

            expected_targets = {
                "STM32F103RCT6": "stm32f103rc",
                "STM32F107VCT6": "stm32f103rc",
                "STM32F105RBT6": "stm32f103rc",
                "STM32F412VET6": "stm32f412xe",
                "STM32F412ZGT6": "stm32f412xg",
                "STM32F429VGT6": "stm32f429xg",
                "STM32F429ZIT6": "stm32f429xi",
                "STM32F439ZGT6": "stm32f439xg",
                "STM32F439BIT6": "stm32f439xi",
                "STM32F767ZIT6": "stm32f767zi",
                "STM32L031K6T6": "stm32l031x6",
                "STM32L432KCU6": "stm32l432kc",
                "STM32L475VCT6": "stm32l475xc",
                "STM32L475VET6": "stm32l475xe",
                "STM32L475VGT6": "stm32l475xg",
                "STM32H723ZGT6": "stm32h723xx",
                "STM32H743ZIT6": "stm32h743xx",
                "STM32H750XBT6": "stm32h750xx",
                "STM32H7B0VBT6": "stm32h7b0xx",
            }

            for target_chip, expected_target in expected_targets.items():
                with self.subTest(target_chip=target_chip):
                    result = self._run_process(
                        [
                            str(self.python_exe),
                            str(helper_path),
                            "preflight",
                            "--target-chip",
                            target_chip,
                            "--probe-unique-id",
                            "NO_SUCH_PROBE_001",
                        ],
                        cwd=temp_dir,
                    )

                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertTrue(payload["target_supported"], payload)
                    self.assertEqual(payload["resolved_target"], expected_target, payload)
                    self.assertIn(payload["target"]["matched_by"], {"exact", "alias", "regex"}, payload)

    def test_preflight_helper_maps_supported_non_stm_targets(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            helper_path = self._write_preflight_helper(temp_dir)

            expected_targets = {
                "LPC55S69": "lpc55s69",
                "LPC5526": "lpc5526",
                "LPC55S16": "lpc55s16",
                "NRF52832": "nrf52832",
                "NRF52840": "nrf52840",
                "RP2040": "rp2040",
            }
            if "mimxrt1060" in self.bundled_targets:
                expected_targets["MIMXRT1062DVJ6A"] = "mimxrt1060"
            if "hc32f448" in self.bundled_targets:
                expected_targets["HC32F448RCTA"] = "hc32f448"

            for target_chip, expected_target in expected_targets.items():
                with self.subTest(target_chip=target_chip):
                    result = self._run_process(
                        [
                            str(self.python_exe),
                            str(helper_path),
                            "preflight",
                            "--target-chip",
                            target_chip,
                            "--probe-unique-id",
                            "NO_SUCH_PROBE_001",
                        ],
                        cwd=temp_dir,
                    )

                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertTrue(payload["target_supported"], payload)
                    self.assertEqual(payload["resolved_target"], expected_target, payload)
                    self.assertIn(payload["target"]["matched_by"], {"exact", "alias", "regex"}, payload)


if __name__ == "__main__":
    unittest.main()
