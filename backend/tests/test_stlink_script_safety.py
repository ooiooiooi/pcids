import unittest

from backend.utils.burner_automation import _pyocd_preflight_helper_source, build_system_script_content


class StlinkScriptSafetyTests(unittest.TestCase):
    def test_debugger_scripts_do_not_emit_standalone_backslash_commands(self):
        for script_name, burner_name in (
            ("stlink_stm32_mcu_flash", "ST-LINK"),
            ("jlink_v4_arm_mcu_flash", "J-LINK"),
            ("gdlink_arm_mcu_flash", "GDLINK"),
        ):
            with self.subTest(script_name=script_name):
                content = build_system_script_content(script_name, burner_name)
                self.assertFalse(
                    any(line.strip() in {"\\", "\\\\"} for line in content.splitlines()),
                    f"{script_name} generated a standalone backslash command",
                )

    def test_stlink_script_requires_probe_serial_target_chip_and_uses_fixed_probe(self):
        content = build_system_script_content("stlink_stm32_mcu_flash", "ST-LINK")

        self.assertIn("未配置 TARGET_CHIP，禁止猜测目标芯片。", content)
        self.assertIn("未配置 BURNER_SN，禁止自动选择烧录器。", content)
        self.assertIn("SN=%BURNER_SN%", content)
        self.assertIn("STLINK_UTILITY_CLI", content)
        self.assertIn("ST-LINK-Utility-CLI-3.6\\ST-LINK_CLI.exe", content)
        self.assertIn("pcids_stlink_preflight_%TASK_ID%.log", content)
        self.assertIn("retrying the same ST-LINK under reset", content)
        self.assertLess(
            content.index('call "%STLINK_UTILITY_CLI%" -c %CONNECT% -TVolt'),
            content.index('"%STLINK_UTILITY_CLI%" -c %CONNECT% !STLINK_CONNECT_MODE! -ME'),
        )
        self.assertIn('set "CONNECT=SN=%BURNER_SN% %INTERFACE_TYPE% FREQ=%STLINK_FREQ_KHZ%"', content)
        self.assertIn('if "%STLINK_FREQ_KHZ%"=="950" set "STLINK_FREQ_KHZ=900"', content)
        self.assertNotIn("STM32_PROGRAMMER_CLI", content)
        self.assertNotIn("PYOCD_", content)
        self.assertNotIn("pyOCD", content)

    def test_stlink_applies_configured_burn_parameters(self):
        content = build_system_script_content("stlink_stm32_mcu_flash", "ST-LINK")

        self.assertIn("%INTERFACE_TYPE%", content)
        self.assertIn("FREQ=%STLINK_FREQ_KHZ%", content)
        self.assertIn("SN=%BURNER_SN%", content)
        self.assertIn('if "%ERASE_MODE%"=="全片擦除"', content)
        self.assertIn(".bin 固件必须提供 START_ADDRESS", content)
        self.assertIn('-P "%FIRMWARE_PATH%" %START_ADDRESS% -V after_programming', content)
        self.assertIn('-P "%FIRMWARE_PATH%" -V after_programming', content)
        self.assertIn("-ME", content)
        self.assertIn("-Rst", content)
        self.assertIn('if not "%COMPLETION_ACTION%"=="不处理"', content)
        self.assertIn('if /I "!PCIDS_FIRMWARE_EXT!"==".bin"', content)

    def test_stlink_uses_only_stlink_utility_cli(self):
        content = build_system_script_content("stlink_stm32_mcu_flash", "ST-LINK")

        self.assertIn('"%STLINK_UTILITY_CLI%" -c %CONNECT% !STLINK_CONNECT_MODE! -ME', content)
        self.assertIn('"%STLINK_UTILITY_CLI%" -c %CONNECT% !STLINK_CONNECT_MODE! -P', content)
        self.assertIn('"%STLINK_UTILITY_CLI%" -c %CONNECT% !STLINK_CONNECT_MODE! -Rst', content)
        self.assertIn('set "STLINK_PREFLIGHT_EXIT=!ERRORLEVEL!"', content)
        self.assertIn('set "STLINK_ERASE_EXIT=!ERRORLEVEL!"', content)
        self.assertIn('set "STLINK_FLASH_EXIT=!ERRORLEVEL!"', content)
        self.assertIn('set "STLINK_RESET_EXIT=!ERRORLEVEL!"', content)
        self.assertNotIn("STM32_PROGRAMMER_CLI", content)
        self.assertNotIn("PYOCD_", content)
        self.assertNotIn("pyOCD", content)


    def test_pwlink2_uses_pyocd_instead_of_powerwriter(self):
        content = build_system_script_content("pwlink_v2_arm_mcu_flash", "PWLINK2")

        self.assertIn("未配置 TARGET_CHIP，禁止猜测目标芯片。", content)
        self.assertIn("未配置 BURNER_SN，禁止自动选择烧录器。", content)
        self.assertIn("Resolved pyOCD target from TARGET_CHIP", content)
        self.assertIn("PYOCD_PYTHON", content)
        self.assertIn("PYOCD_HELPER", content)
        self.assertIn("pyOCD runtime version", content)
        self.assertIn('"%PYOCD_PYTHON%" -m pyocd commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%"', content)
        self.assertIn('"%PYOCD_PYTHON%" -m pyocd flash -u "%BURNER_SN%" -t "%PYOCD_TARGET%"', content)
        self.assertNotIn("PYOCD_TARGET_CANDIDATES", content)
        self.assertNotIn("Trying pyOCD target", content)
        self.assertNotIn('-t "%%T"', content)
        self.assertNotIn("list --targets", content)
        self.assertNotIn("--json", content)
        self.assertIn("-M halt", content)
        self.assertIn("-M under-reset", content)
        self.assertIn("COMPLETION_ACTION", content)
        self.assertIn("PYOCD_FLASH_RESET_OPTION=--no-reset", content)
        self.assertIn("Completion action: reset and run", content)
        self.assertIn('"%PYOCD_PYTHON%" -m pyocd commander', content)
        self.assertIn('-c "go"', content)
        self.assertIn('-c "reset halt"', content)
        self.assertNotIn("cmsis_dap.allow_no_brm", content)
        self.assertIn('set "PYOCD_RETRY_FREQ=50k"', content)
        self.assertIn("-O reset_type=hardware", content)
        self.assertIn("-O cmsis_dap.limit_packets=true", content)
        self.assertNotIn("POWERWRITER_CLI", content)
        self.assertNotIn("PWLINK2_CMD_TEMPLATE", content)

    def test_pyocd_based_scripts_apply_configured_burn_parameters(self):
        for script_name, burner_name in [
            ("pwlink_v2_arm_mcu_flash", "PWLINK2"),
            ("swd_downloader_arm_mcu_flash", "SWD Downloader"),
            ("jlink_v4_arm_mcu_flash", "J-LINK"),
        ]:
            with self.subTest(script_name=script_name):
                content = build_system_script_content(script_name, burner_name)
                self.assertIn("Resolved pyOCD target from TARGET_CHIP", content)
                self.assertIn("PYOCD_HELPER", content)
                self.assertIn("preflight --target-chip", content)
                self.assertIn('set "PYOCD_FREQ=%WRITE_SPEED_KHZ%k"', content)
                self.assertIn("PYOCD_ERASE", content)
                self.assertIn('if /I "%INTERFACE_TYPE%"=="JTAG"', content)
                self.assertIn("%START_ADDRESS%", content)
                self.assertIn("PYOCD_FLASH_RESET_OPTION", content)
                self.assertIn('if "%COMPLETION_ACTION%"=="复位运行"', content)
                self.assertIn('if "%COMPLETION_ACTION%"=="不处理"', content)

    def test_swd_downloader_uses_safe_pyocd_runner(self):
        content = build_system_script_content("swd_downloader_arm_mcu_flash", "SWD Downloader")

        self.assertIn("SWD_CMD_TEMPLATE", content)
        self.assertIn("未配置 TARGET_CHIP，禁止猜测目标芯片。", content)
        self.assertIn("未配置 BURNER_SN，禁止自动选择烧录器。", content)
        self.assertIn("SWD_CMD_TEMPLATE 缺少 {probe} 占位符", content)
        self.assertIn("PYOCD_HELPER", content)
        self.assertIn("preflight --target-chip", content)
        self.assertNotIn("PYOCD_TARGET_CANDIDATES", content)
        self.assertNotIn("Trying pyOCD target", content)
        self.assertNotIn("list --targets", content)
        self.assertNotIn('list --probes --json', content)

    def test_strict_scripts_do_not_emit_standalone_backslash_lines(self):
        for script_name, burner_name in [
            ("stlink_stm32_mcu_flash", "ST-LINK"),
            ("pwlink_v2_arm_mcu_flash", "PWLINK2"),
            ("swd_downloader_arm_mcu_flash", "SWD Downloader"),
        ]:
            with self.subTest(script_name=script_name):
                content = build_system_script_content(script_name, burner_name)
                self.assertEqual([line for line in content.splitlines() if line.strip() == "\\"], [])

    def test_stlink_preflight_occurs_before_erase_or_program(self):
        content = build_system_script_content("stlink_stm32_mcu_flash", "ST-LINK")

        preflight = content.index('call "%STLINK_UTILITY_CLI%" -c %CONNECT% -TVolt')
        erase = content.index('"%STLINK_UTILITY_CLI%" -c %CONNECT% !STLINK_CONNECT_MODE! -ME')
        program = content.index('"%STLINK_UTILITY_CLI%" -c %CONNECT% !STLINK_CONNECT_MODE! -P')
        self.assertLess(preflight, erase)
        self.assertLess(preflight, program)

    def test_strict_pyocd_helper_uses_python_api_and_own_json_schema(self):
        helper_source = _pyocd_preflight_helper_source()

        self.assertIn("ConnectHelper.get_all_connected_probes", helper_source)
        self.assertIn("normalise_target_type_name", helper_source)
        self.assertIn("difflib", helper_source)
        self.assertIn("TARGET_REGEX_RULES", helper_source)
        self.assertIn("re.fullmatch", helper_source)
        self.assertIn("STM32F10[57]", helper_source)
        self.assertIn("LPC54", helper_source)
        self.assertIn("NRF91", helper_source)
        self.assertIn("MIMXRT", helper_source)
        self.assertIn("HC32", helper_source)
        self.assertIn("STM32F412", helper_source)
        self.assertIn("STM32L475", helper_source)
        self.assertIn("STM32H743", helper_source)
        self.assertIn('"matched_by"', helper_source)
        self.assertIn('"resolution_reason"', helper_source)
        self.assertIn('"close_matches"', helper_source)
        self.assertIn('"unique_id"', helper_source)
        self.assertIn('"description"', helper_source)
        self.assertIn('"vendor_name"', helper_source)
        self.assertIn('"product_name"', helper_source)
        self.assertIn("lpc55s69", helper_source)
        self.assertIn("nrf52840", helper_source)
        self.assertIn("rp2040", helper_source)

    def test_jlink_falls_back_to_pyocd_when_official_cli_is_missing(self):
        content = build_system_script_content("jlink_v4_arm_mcu_flash", "J-LINK")

        self.assertIn("SEGGER J-Link CLI not found, falling back to pyOCD.", content)
        self.assertIn(r'%ProgramFiles%\SEGGER\JLink*\JLink.exe', content)
        self.assertIn('if exist "%JLINK_EXE%" goto PCIDS_JLINK_OFFICIAL', content)
        self.assertIn(":PCIDS_JLINK_OFFICIAL", content)
        self.assertNotIn('if not exist "%JLINK_EXE%" (', content)
        self.assertIn("PYOCD_HELPER", content)
        self.assertIn("preflight --target-chip", content)
        self.assertIn("Resolved pyOCD target from TARGET_CHIP", content)
        self.assertIn("未配置 TARGET_CHIP，禁止猜测目标芯片。", content)
        self.assertIn("未配置 BURNER_SN，禁止自动选择烧录器。", content)
        self.assertNotIn("PYOCD_TARGET_CANDIDATES", content)
        self.assertNotIn("Trying pyOCD target", content)
        self.assertNotIn('if "%PYOCD_TARGET%"=="" set "PYOCD_TARGET=stm32f103c8"', content)
        self.assertNotIn('for %%T in (', content)
        self.assertIn("J-Link official CLI requires BURNER_SN", content)
        self.assertIn('findstr /R /I "^STM32[A-Z][0-9][0-9][0-9][A-Z][0-9A-Z][A-Z][0-9]$"', content)
        self.assertIn('set "JLINK_DEVICE=%JLINK_DEVICE:~0,-2%"', content)
        self.assertIn("Resolved SEGGER device from TARGET_CHIP", content)
        self.assertIn("SelectEmuBySN %BURNER_SN%", content)
        self.assertIn("echo si SWD", content)
        self.assertIn("echo si JTAG", content)
        self.assertIn("echo speed %WRITE_SPEED_KHZ%", content)
        self.assertIn("echo device %JLINK_DEVICE%", content)
        self.assertIn("echo erase", content)
        self.assertIn('if "%COMPLETION_ACTION%"=="复位运行" >>"%JLINK_CMD%" echo g', content)
        self.assertIn('if "%COMPLETION_ACTION%"=="不处理" set "JLINK_DO_RESET=0"', content)
        self.assertIn('echo loadfile "%FIRMWARE_PATH%"', content)
        self.assertIn('set "PYOCD_RETRY_FREQ=50k"', content)

    def test_gdlink_uses_official_cli_for_gd32_or_falls_back_to_pyocd(self):
        content = build_system_script_content("gdlink_arm_mcu_flash", "GDLINK")

        self.assertIn("GDLINK_CMD_TEMPLATE", content)
        self.assertIn("{firmware}", content)
        self.assertIn("{target}", content)
        self.assertIn("{interface}", content)
        self.assertIn("{speed}", content)
        self.assertIn("{address}", content)
        self.assertIn("{erase}", content)
        self.assertIn("{action}", content)
        self.assertIn("{probe}", content)
        self.assertIn('if /I "%TARGET_CHIP:~0,4%"=="GD32"', content)
        self.assertIn("GDLink_CLI", content)
        self.assertIn("pcids_gdlink_%TASK_ID%.gdlink", content)
        self.assertIn("echo c %BURNER_SN%", content)
        self.assertIn("echo si 1", content)
        self.assertIn("echo si 0", content)
        self.assertIn("echo sd %TARGET_CHIP%", content)
        self.assertIn("echo Connect", content)
        self.assertIn("echo erase", content)
        self.assertIn('echo load "%FIRMWARE_PATH%"', content)
        self.assertIn("echo r", content)
        self.assertIn("echo g", content)
        self.assertIn('-speed %WRITE_SPEED_KHZ% -commandfile -e', content)
        self.assertIn("target is not GD32; falling back to pyOCD CMSIS-DAP", content)
        self.assertIn("PYOCD_HELPER", content)
        self.assertIn("preflight --target-chip", content)
        self.assertIn("Resolved pyOCD target from TARGET_CHIP", content)
        self.assertIn("未配置 TARGET_CHIP，禁止猜测目标芯片。", content)
        self.assertIn("未配置 BURNER_SN，禁止自动选择烧录器。", content)
        self.assertNotIn("PYOCD_TARGET_CANDIDATES", content)
        self.assertNotIn("Trying pyOCD target", content)
        self.assertNotIn('set "PYOCD_TARGET=%TARGET_CHIP%"', content)
        self.assertNotIn('if "%PYOCD_TARGET%"=="" set "PYOCD_TARGET=stm32f103c8"', content)
        self.assertNotIn('for %%T in (', content)
        self.assertIn("PYOCD_ERASE", content)
        self.assertIn("COMPLETION_ACTION", content)


if __name__ == "__main__":
    unittest.main()
