import unittest

from backend.utils.burner_automation import (
    SYSTEM_SCRIPT_BINDINGS,
    SYSTEM_SCRIPT_CATALOG,
    _stream_command_helper_source,
    _xds510plus_dss_source,
    _xds510plus_usb_preflight_source,
    build_system_script_content,
)
from backend.utils.db import DEFAULT_PRODUCT_CATALOG


class SystemScriptParameterContractTests(unittest.TestCase):
    def test_parameterized_vendor_templates_receive_target_and_probe(self):
        for script_name, burner_name in [
            ("al321_fpga_mcu_flash", "AL321"),
            ("hdsc_ccid_arm_mcu_flash", "HDSC CCID"),
            ("gowin_usb_cable_fpga_flash", "Gowin USB Cable"),
        ]:
            with self.subTest(script_name=script_name):
                content = build_system_script_content(script_name, burner_name)
                self.assertIn("{target}", content)
                self.assertIn("TARGET_CHIP", content)
                self.assertIn("{probe}", content)
                self.assertIn("BURNER_SN", content)
                self.assertIn("INTERFACE_TYPE", content)
                self.assertIn("WRITE_SPEED_KHZ", content)
                self.assertIn("ERASE_MODE", content)
                self.assertIn("COMPLETION_ACTION", content)
                self.assertTrue(
                    "exit /b !ERRORLEVEL!" in content
                    or "exit /b !PCIDS_STREAM_EXIT!" in content
                    or "exit /b !GOWIN_EXIT!" in content
                )

    def test_vendor_templates_use_streaming_helper(self):
        for script_name, burner_name in [
            ("al321_fpga_mcu_flash", "AL321"),
            ("gdlink_arm_mcu_flash", "GDLINK"),
            ("swd_downloader_arm_mcu_flash", "SWD下载器"),
            ("hdsc_ccid_arm_mcu_flash", "HDSC CCID"),
            ("gowin_usb_cable_fpga_flash", "Gowin USB Cable"),
        ]:
            with self.subTest(script_name=script_name):
                content = build_system_script_content(script_name, burner_name)
                self.assertIn("PCIDS_STREAM_HELPER", content)
                self.assertIn("PCIDS_STREAM_CMD", content)
                self.assertIn("PCIDS_STREAM_TIMEOUT_SECONDS", content)
                self.assertIn('powershell -NoProfile -ExecutionPolicy Bypass -File "%PCIDS_STREAM_HELPER%"', content)
                self.assertNotIn('cmd /d /s /c "!PCIDS_CMD!"', content)

    def test_gowin_script_passes_real_programmer_cli_arguments(self):
        content = build_system_script_content("gowin_usb_cable_fpga_flash", "Gowin USB Cable")
        self.assertIn('"%GOWIN_PROGRAMMER_CLI%" --scan --cable-index !GOWIN_CABLE_INDEX!', content)
        self.assertIn('for /F "tokens=2" %%A in (\'findstr /C:"Name:" "!GOWIN_SCAN_LOG!"\')', content)
        self.assertIn('--device "!GOWIN_DEVICE!"', content)
        self.assertIn('--operation_index !GOWIN_OPERATION!', content)
        self.assertIn('--fsFile "%FIRMWARE_PATH%"', content)
        self.assertIn('--cable "Gowin USB Cable(FT2CH)"', content)
        self.assertIn('--cable-index !GOWIN_CABLE_INDEX!', content)
        self.assertIn('if "%GOWIN_CABLE_INDEX%"=="" set "GOWIN_CABLE_INDEX=1"', content)
        self.assertIn('--frequency "!GOWIN_FREQUENCY!"', content)
        self.assertIn('if /I "%EXECUTION_OPERATION%"=="Flash固化" set "GOWIN_OPERATION=8"', content)
        direct_gowin_section = content.split("rem Gowin cable name contains parentheses.", 1)[1]
        self.assertNotIn('set "PCIDS_STREAM_CMD=!PCIDS_CMD!"', direct_gowin_section)
        self.assertIn('if "%WRITE_VERIFY%"=="1" set "GOWIN_OPERATION=4"', content)
        self.assertIn('findstr /I /C:"Error:" /C:"Verify failed" "!GOWIN_RUN_LOG!" >nul', content)
        self.assertIn('set "GOWIN_OPERATION=6"', content)
        self.assertIn('set "GOWIN_OPERATION=17"', content)

    def test_stream_helper_is_written_as_utf8_with_bom_for_windows_powershell(self):
        content = build_system_script_content("gowin_usb_cable_fpga_flash", "Gowin USB Cable")
        self.assertIn("77u/", content)
        self.assertIn("ReadToEndAsync", _stream_command_helper_source())

    def test_xds510plus_runs_usb_driver_preflight_before_vendor_command(self):
        catalog_item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "xds510plus_dsp_flash")
        default_config = catalog_item["default_config"]
        self.assertEqual(default_config["target_config_file_label"], "目标配置文件（.ccxml）")
        self.assertIn("SEED F28335", default_config["target_config_file_placeholder"])
        self.assertIn("TMS320F28335", default_config["target_config_file_hint"])
        self.assertEqual(default_config["erase_mode"], "全片擦除")
        self.assertEqual(default_config["erase_mode_options"], ["全片擦除"])

        content = build_system_script_content("xds510plus_dsp_flash", "XDS510plus")
        preflight_source = _xds510plus_usb_preflight_source()

        self.assertIn("XDS510plus Windows USB/driver precheck", preflight_source)
        self.assertIn("BURNER_LOCATION", preflight_source)
        self.assertIn("BURNER_PORT", preflight_source)
        self.assertIn("当前 burner 绑定的实例锚点", preflight_source)
        self.assertIn("在实例锚点命中 XDS510plus 设备", preflight_source)
        self.assertIn("VID_0547&PID_1020", preflight_source)
        self.assertNotIn("VID_0C55", preflight_source)
        self.assertIn("CM_PROB_FAILED_INSTALL", preflight_source)
        self.assertIn("Windows 未正确绑定 SEED EZUSBPLUS 驱动", preflight_source)
        self.assertIn("EZUSBPLUS", preflight_source)
        self.assertIn('[string]$item.Status -eq "OK"', preflight_source)
        self.assertIn("XDS510_PREFLIGHT_EXIT", content)
        self.assertLess(content.index("XDS510_PREFLIGHT_EXIT"), content.index('set "XDS510_DSS_HELPER='))
        self.assertIn('call "%DSS_BAT%" "%XDS510_DSS_HELPER%"', content)
        self.assertIn('findstr /I /C:"SEED-XDS510PLUS_Connection.xml"', content)
        self.assertIn('findstr /I /C:"seedxds510plusc28x.xml"', content)
        self.assertNotIn("UNIFLASH_CLI", content)
        self.assertNotIn("Spectrum Digital", content)
        dss_source = _xds510plus_dss_source()
        self.assertIn('setString("FlashOperations", writeVerify ? "Program, Verify" : "Program")', dss_source)
        self.assertIn("session.flash.erase()", dss_source)
        self.assertIn("session.target.reset()", dss_source)
        self.assertIn("session.target.run()", dss_source)

    def test_sylixos_hybrid_script_uses_repository_artifact_upload_path(self):
        catalog_item = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "sylixos_ls2k_ftp_serial_flash")
        binding = SYSTEM_SCRIPT_BINDINGS["sylixos_ls2k_ftp_serial_flash"]
        content = build_system_script_content("sylixos_ls2k_ftp_serial_flash", "TFTP+串口")

        self.assertEqual(catalog_item["task_type"], "hybrid")
        self.assertEqual(catalog_item["type"], "shell")
        self.assertEqual(catalog_item["default_config"]["burn_mode"], "TFTP+串口")
        self.assertEqual(catalog_item["default_config"]["transfer_protocol"], "TFTP+串口")
        self.assertEqual(catalog_item["default_config"]["server_port"], 69)
        self.assertEqual(catalog_item["default_config"]["configured_board_address"], "192.168.1.230")
        self.assertEqual(catalog_item["default_config"]["board_target_address"], "192.168.1.230")
        self.assertEqual(catalog_item["default_config"]["local_ip"], "192.168.1.100")
        self.assertEqual(catalog_item["default_config"]["target_path"], "/media/hdd0")
        self.assertNotIn("target_filename", catalog_item["default_config"])
        self.assertNotIn("tftp_filename", catalog_item["default_config"])
        self.assertEqual(catalog_item["default_config"]["ftp_login_user"], "root")
        self.assertEqual(catalog_item["default_config"]["ftp_login_password"], "root")
        self.assertEqual(catalog_item["default_config"]["baud_rate"], "115200")
        self.assertIn("翼辉SylixOS", binding["associated_board"])
        self.assertIn("FIRMWARE_PATH", content)
        self.assertIn("/media/hdd0", content)
        self.assertIn("observe boot logs", content)
        self.assertIn("REMOTE_ARTIFACT_NAME", content)
        self.assertIn("set al1 /dev/fs/fat@wd0/${artifact_name}", content)

    def test_sylixos_associated_boards_are_seeded_as_default_products(self):
        products_by_name = {item["name"]: item for item in DEFAULT_PRODUCT_CATALOG}

        for product_name in ["翼辉SylixOS", "LS2K", "龙芯2K", "bspls2kpcm2k01"]:
            with self.subTest(product_name=product_name):
                product = products_by_name[product_name]
                self.assertEqual(product["chip_type"], "其他")
                self.assertIn("UART", product["burn_interface"])
                self.assertIn("以太网", product["interface"])


if __name__ == "__main__":
    unittest.main()
