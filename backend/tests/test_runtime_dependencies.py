import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.utils.runtime_dependencies import (
    COMMON_TOOL_SEARCH_PATHS,
    build_burner_tool_readiness,
    build_runtime_dependency_report,
    configure_bundled_tools,
    get_al321_driver_state_file,
    recover_pending_al321_driver_state,
)


class RuntimeDependenciesTest(unittest.TestCase):
    def tearDown(self):
        configure_bundled_tools.cache_clear()
        build_runtime_dependency_report.cache_clear()

    def test_bundled_tool_overrides_inherited_external_tool_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "bundled" / "ST-LINK_CLI.exe"
            external = root / "external" / "ST-LINK_CLI.exe"
            bundled.parent.mkdir(parents=True)
            external.parent.mkdir(parents=True)
            bundled.touch()
            external.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(root / "bundled"),
                    "STLINK_UTILITY_CLI": str(external),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                configured = configure_bundled_tools()

                self.assertEqual(configured["STLINK_UTILITY_CLI"], str(external.resolve()))
                self.assertEqual(os.environ["STLINK_UTILITY_CLI"], str(external.resolve()))

    def test_bundled_hdc_tool_is_discovered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            hdc = root / "burners" / "HDC" / "OpenHarmony-6.1" / "toolchains" / "hdc.exe"
            hdc.parent.mkdir(parents=True)
            hdc.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(root / "burners"),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                configured = configure_bundled_tools()

                self.assertEqual(configured["HDC_EXE"], str(hdc.resolve()))
                self.assertEqual(os.environ["HDC_EXE"], str(hdc.resolve()))

    def test_bundled_hdsc_runtime_is_discovered_for_script_initialization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "burners"
            agent = root / "HDSC" / "hdsc_ccid_agent.py"
            vendor_exe = root / "HDSC_CCID" / "HDSC+CCID+Prog+REV6.04.exe"
            agent.parent.mkdir(parents=True)
            vendor_exe.parent.mkdir(parents=True)
            agent.touch()
            vendor_exe.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(root),
                    "HDSC_CCID_AGENT": "",
                    "HDSC_CCID_V604_EXE": "",
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                configured = configure_bundled_tools()

                self.assertEqual(configured["HDSC_CCID_AGENT"], str(agent.resolve()))
                self.assertEqual(configured["HDSC_CCID_V604_EXE"], str(vendor_exe.resolve()))

    def test_burner_readiness_distinguishes_ready_and_docs_only_tools(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            stlink_dir = bundled_root / "ST-LINK"
            jlink_dir = bundled_root / "J-LINK"
            stlink_dir.mkdir(parents=True)
            jlink_dir.mkdir(parents=True)
            (stlink_dir / "ST-LINK_CLI.exe").touch()
            (jlink_dir / "README.md").write_text("docs only", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                readiness = build_burner_tool_readiness()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(readiness_by_name["ST-LINK"]["status"], "ok")
            self.assertTrue(str(readiness_by_name["ST-LINK"]["configured_path"]).endswith("ST-LINK_CLI.exe"))
            self.assertEqual(readiness_by_name["J-LINK"]["status"], "warn")
            self.assertTrue(readiness_by_name["J-LINK"]["bundled_dir_exists"])

    def test_burner_readiness_accepts_bundled_pyocd_for_pwlink2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            pyocd = bundled_root / "SWD_Downloader" / "pyocd-runtime" / "Scripts" / "pyocd.exe"
            pyocd.parent.mkdir(parents=True)
            pyocd.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                readiness = build_burner_tool_readiness()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(readiness_by_name["PWLINK2"]["status"], "ok")
            self.assertEqual(readiness_by_name["PWLINK2"]["configured_mode"], "file")
            self.assertTrue(str(readiness_by_name["PWLINK2"]["configured_path"]).endswith("pyocd.exe"))
            self.assertEqual(readiness_by_name["SWD Downloader"]["status"], "ok")

    def test_burner_readiness_accepts_bundled_pyocd_for_jlink_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            pyocd = bundled_root / "SWD_Downloader" / "pyocd-runtime" / "Scripts" / "pyocd.exe"
            pyocd.parent.mkdir(parents=True)
            pyocd.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                    "JLINK_EXE": "",
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                readiness = build_burner_tool_readiness()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(readiness_by_name["J-LINK"]["status"], "ok")
            self.assertEqual(readiness_by_name["J-LINK"]["configured_mode"], "file")
            self.assertTrue(str(readiness_by_name["J-LINK"]["configured_path"]).endswith("pyocd.exe"))

    def test_bundled_openfpgaloader_is_discovered_for_al321(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            openfpgaloader = bundled_root / "AL321" / "openFPGALoader" / "openFPGALoader.exe"
            driver_tool = bundled_root / "AL321" / "drivers" / "AL321_WinUSB_Driver_Tool.exe"
            switch_script = bundled_root / "AL321" / "drivers" / "switch-al321-driver.ps1"
            openfpgaloader.parent.mkdir(parents=True)
            driver_tool.parent.mkdir(parents=True)
            openfpgaloader.touch()
            driver_tool.touch()
            switch_script.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                build_runtime_dependency_report.cache_clear()
                readiness = build_burner_tool_readiness()
                configured = configure_bundled_tools()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(configured["OPENFPGALOADER_EXE"], str(openfpgaloader.resolve()))
            self.assertEqual(readiness_by_name["AL321"]["status"], "ok")
            self.assertEqual(readiness_by_name["AL321"]["configured_mode"], "file")
            self.assertTrue(str(readiness_by_name["AL321"]["configured_path"]).endswith("openFPGALoader.exe"))
            self.assertTrue(readiness_by_name["AL321"]["driver_ready"])
            self.assertTrue(
                any(str(item).endswith("AL321_WinUSB_Driver_Tool.exe") for item in readiness_by_name["AL321"]["driver_artifacts"])
            )

    def test_bundled_program_flash_is_discovered_for_al321(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            program_flash = bundled_root / "AL321" / "Vitis" / "bin" / "program_flash.bat"
            program_flash.parent.mkdir(parents=True)
            program_flash.touch()

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root)}, clear=False), patch.dict(
                COMMON_TOOL_SEARCH_PATHS, {"PROGRAM_FLASH_EXE": []}, clear=False
            ):
                configure_bundled_tools.cache_clear()
                configured = configure_bundled_tools()

            self.assertEqual(configured["PROGRAM_FLASH_EXE"], str(program_flash.resolve()))

    def test_bundled_xsdb_and_hw_server_are_discovered_for_al321(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            xsdb = bundled_root / "AL321" / "Vitis" / "bin" / "xsdb.bat"
            hw_server = bundled_root / "AL321" / "Vitis" / "bin" / "hw_server.bat"
            xsdb.parent.mkdir(parents=True)
            xsdb.touch()
            hw_server.touch()

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root)}, clear=False), patch.dict(
                COMMON_TOOL_SEARCH_PATHS, {"XSDB_EXE": [], "HW_SERVER_EXE": []}, clear=False
            ):
                configure_bundled_tools.cache_clear()
                configured = configure_bundled_tools()

            self.assertEqual(configured["XSDB_EXE"], str(xsdb.resolve()))
            self.assertEqual(configured["HW_SERVER_EXE"], str(hw_server.resolve()))

    def test_bundled_xds510plus_tools_and_driver_are_discovered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            dss = bundled_root / "XDS510plus" / "CCS" / "ccs_base" / "scripting" / "bin" / "dss.bat"
            driver_script = bundled_root / "XDS510plus" / "drivers" / "install-xds510plus-driver.ps1"
            driver_inf = bundled_root / "XDS510plus" / "drivers" / "seedxds510plus.inf"
            dss.parent.mkdir(parents=True)
            driver_script.parent.mkdir(parents=True)
            dss.touch()
            driver_script.touch()
            driver_inf.write_text("USB\\VID_0547&PID_1020", encoding="utf-8")

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root), "DSS_BAT": "", "XDS510_DRIVER_INSTALL_SCRIPT": ""}, clear=False), patch.dict(
                COMMON_TOOL_SEARCH_PATHS, {"DSS_BAT": []}, clear=False
            ):
                configure_bundled_tools.cache_clear()
                build_runtime_dependency_report.cache_clear()
                configured = configure_bundled_tools()
                readiness = build_burner_tool_readiness()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(configured["DSS_BAT"], str(dss.resolve()))
            self.assertEqual(configured["XDS510_DRIVER_INSTALL_SCRIPT"], str(driver_script.resolve()))
            self.assertEqual(readiness_by_name["XDS510plus"]["status"], "ok")
            self.assertEqual(readiness_by_name["XDS510plus"]["configured_mode"], "file")
            self.assertTrue(readiness_by_name["XDS510plus"]["driver_ready"])
            self.assertTrue(
                any(str(item).endswith("seedxds510plus.inf") for item in readiness_by_name["XDS510plus"]["driver_artifacts"])
            )

    def test_external_amd_install_can_be_discovered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            amd_root = root / "AMD"
            bundled_root.mkdir(parents=True)
            program_flash = amd_root / "2026.1" / "Vitis" / "bin" / "program_flash.bat"
            program_flash.parent.mkdir(parents=True)
            program_flash.touch()

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root)}, clear=False), patch.dict(
                "backend.utils.runtime_dependencies.COMMON_TOOL_SEARCH_PATHS",
                {"PROGRAM_FLASH_EXE": [str(amd_root)]},
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                configured = configure_bundled_tools()

            self.assertEqual(configured["PROGRAM_FLASH_EXE"], str(program_flash.resolve()))

    def test_d_vitis_bin_layout_can_be_auto_discovered_for_al321_flash_tools(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            vitis_bin = root / "d_vitis" / "Vitis" / "2020.2" / "bin"
            bundled_root.mkdir(parents=True)
            program_flash = vitis_bin / "program_flash.bat"
            xsdb = vitis_bin / "xsdb.bat"
            hw_server = vitis_bin / "hw_server.bat"
            vitis_bin.mkdir(parents=True)
            program_flash.touch()
            xsdb.touch()
            hw_server.touch()

            def fake_glob(pattern: str):
                if pattern == r"D:\vitis\Vitis\*\bin":
                    return [str(vitis_bin)]
                if pattern == r"D:\vitis\Vitis\*":
                    return [str(vitis_bin.parent)]
                return []

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root)}, clear=False), patch(
                "backend.utils.runtime_dependencies.glob.glob",
                side_effect=fake_glob,
            ):
                configure_bundled_tools.cache_clear()
                readiness = build_burner_tool_readiness()
                configured = configure_bundled_tools()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(configured["PROGRAM_FLASH_EXE"], str(program_flash.resolve()))
            self.assertEqual(configured["XSDB_EXE"], str(xsdb.resolve()))
            self.assertEqual(configured["HW_SERVER_EXE"], str(hw_server.resolve()))
            self.assertEqual(readiness_by_name["AL321"]["configured_paths"]["PROGRAM_FLASH_EXE"], str(program_flash.resolve()))
            self.assertEqual(readiness_by_name["AL321"]["configured_paths"]["XSDB_EXE"], str(xsdb.resolve()))
            self.assertEqual(readiness_by_name["AL321"]["configured_paths"]["HW_SERVER_EXE"], str(hw_server.resolve()))

    def test_common_install_directory_can_be_auto_discovered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            bundled_root.mkdir(parents=True)
            jlink_root = root / "ProgramFiles" / "SEGGER" / "JLink"
            jlink_root.mkdir(parents=True)
            executable = jlink_root / "JLink.exe"
            executable.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                    "ProgramFiles": str(root / "ProgramFiles"),
                    "ProgramFiles(x86)": str(root / "ProgramFilesX86"),
                    "LOCALAPPDATA": str(root / "LocalAppData"),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                readiness = build_burner_tool_readiness()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(readiness_by_name["J-LINK"]["status"], "ok")
            self.assertEqual(readiness_by_name["J-LINK"]["configured_source"], "system")
            self.assertTrue(str(readiness_by_name["J-LINK"]["configured_path"]).endswith("JLink.exe"))

    def test_pending_al321_driver_state_triggers_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_root = Path(temp_dir) / "burners"
            state_file = bundled_root / "AL321" / "driver-switch-logs" / "al321-driver-state.json"
            script_path = bundled_root / "AL321" / "drivers" / "switch-al321-driver.ps1"
            script_path.parent.mkdir(parents=True)
            state_file.parent.mkdir(parents=True)
            script_path.write_text("Write-Output 'ok'\n", encoding="utf-8")
            state_file.write_text('{"State":"pending_restore"}', encoding="utf-8")

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root)}, clear=False), patch(
                "backend.utils.runtime_dependencies.subprocess.run"
            ) as run_mock:
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = "recovered"
                run_mock.return_value.stderr = ""
                configure_bundled_tools.cache_clear()
                self.assertEqual(get_al321_driver_state_file(), state_file)
                recovered = recover_pending_al321_driver_state()

            self.assertTrue(recovered)
            args = run_mock.call_args.args[0]
            self.assertIn("-Mode", args)
            self.assertIn("recover-pending", args)
            self.assertIn(str(script_path), args)

    def test_al321_missing_driver_switch_script_is_reported_before_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_root = Path(temp_dir) / "burners"
            tool_path = bundled_root / "AL321" / "openFPGALoader.exe"
            tool_path.parent.mkdir(parents=True)
            tool_path.touch()

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                    "AL321_AUTO_DRIVER_SWITCH": "1",
                    "AL321_DRIVER_SWITCH_SCRIPT": "",
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                readiness = build_burner_tool_readiness()

            al321 = next(item for item in readiness if item["burner"] == "AL321")
            self.assertEqual(al321["status"], "warn")
            self.assertIn("switch-al321-driver.ps1", al321["message"])
            self.assertTrue(al321["support_issues"])

    def test_pending_al321_driver_state_failed_recovery_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled_root = Path(temp_dir) / "burners"
            state_file = bundled_root / "AL321" / "driver-switch-logs" / "al321-driver-state.json"
            script_path = bundled_root / "AL321" / "drivers" / "switch-al321-driver.ps1"
            script_path.parent.mkdir(parents=True)
            state_file.parent.mkdir(parents=True)
            script_path.write_text("Write-Output 'ok'\n", encoding="utf-8")
            state_file.write_text('{"State":"pending_restore"}', encoding="utf-8")

            with patch.dict(os.environ, {"PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root)}, clear=False), patch(
                "backend.utils.runtime_dependencies.subprocess.run"
            ) as run_mock:
                run_mock.return_value.returncode = 2
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = "restore failed"
                configure_bundled_tools.cache_clear()
                recovered = recover_pending_al321_driver_state()

            self.assertFalse(recovered)
            self.assertTrue(state_file.exists())

    def test_driver_only_directory_is_reported_as_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled_root = root / "burners"
            bundled_root.mkdir(parents=True)
            gdlink_dir = bundled_root / "GDLINK"
            driver_dir = gdlink_dir / "Drivers"
            driver_dir.mkdir(parents=True)
            (driver_dir / "gdlink_winusb.inf").write_text("driver", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "PCIDS_BUNDLED_TOOLS_DIR": str(bundled_root),
                },
                clear=False,
            ):
                configure_bundled_tools.cache_clear()
                build_runtime_dependency_report.cache_clear()
                readiness = build_burner_tool_readiness()
                report = build_runtime_dependency_report()

            readiness_by_name = {str(item["burner"]): item for item in readiness}
            self.assertEqual(readiness_by_name["GDLINK"]["status"], "warn")
            self.assertTrue(readiness_by_name["GDLINK"]["driver_ready"])
            self.assertTrue(
                any(str(item).endswith("gdlink_winusb.inf") for item in readiness_by_name["GDLINK"]["driver_artifacts"])
            )
            self.assertGreaterEqual(report["arm_burner_driver_ready_count"], 1)


if __name__ == "__main__":
    unittest.main()
