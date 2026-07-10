import unittest

from backend.utils.burner_automation import (
    SYSTEM_SCRIPT_CATALOG,
    _al321_openfpgaloader_runner,
    build_system_script_content,
)


class Al321OpenFPGALoaderScriptTests(unittest.TestCase):
    def setUp(self):
        self.content = build_system_script_content("al321_fpga_mcu_flash", "AL321")

    def test_uses_bundled_openfpgaloader_before_any_vivado_dependency(self):
        self.assertIn("OPENFPGALOADER_EXE", self.content)
        self.assertIn("where openFPGALoader.exe", self.content)
        self.assertNotIn("VIVADO_BIN", self.content)

    def test_generated_runner_has_no_stray_leading_command(self):
        runner = _al321_openfpgaloader_runner()
        self.assertFalse(runner.startswith("\\"))
        self.assertTrue(runner.startswith('set "AL321_OPERATION=%EXECUTION_OPERATION%"'))
        self.assertNotIn("\n\\\n", self.content)

    def test_keeps_template_override_and_logs_real_command(self):
        self.assertIn("AL321_CMD_TEMPLATE", self.content)
        self.assertIn("echo [EXEC] !PCIDS_CMD!", self.content)
        self.assertIn("{probe}", self.content)
        self.assertIn("{action}", self.content)

    def test_ftdi_never_uses_unsupported_serial_selectors(self):
        self.assertNotIn("--usb-serial-num", self.content)
        self.assertNotIn("--ftdi-serial", self.content)

    def test_enforces_safe_probe_selection_and_pid_coverage(self):
        self.assertIn("USB\\VID_0403&PID_6014", self.content)
        self.assertIn("USB\\VID_03FD&PID_0007", self.content)
        self.assertIn("USB\\VID_03FD&PID_0008", self.content)
        self.assertIn("USB\\VID_03FD&PID_000F", self.content)
        self.assertIn("USB\\VID_03FD&PID_0013", self.content)
        self.assertIn("USB\\VID_03FD&PID_000D", self.content)
        self.assertIn("禁止猜测", self.content)

    def test_checks_for_legal_probe_firmware_when_pid_is_uninitialized(self):
        self.assertIn("xusb_xp2.hex", self.content)
        self.assertIn("xusb_emb.hex", self.content)
        self.assertIn("AMD/Xilinx 官方驱动或 Vivado Lab 安装包", self.content)
        self.assertIn("系统不会使用伪造固件", self.content)
        self.assertIn("--probe-firmware", self.content)

    def test_maps_sram_and_zynqmp_flash_to_separate_tools(self):
        self.assertIn('set "AL321_MODE_FLAG=-m"', self.content)
        self.assertIn('if "%AL321_OPERATION%"=="Flash固化"', self.content)
        self.assertIn("ZynqMP SRAM下载需要 FPGA bitstream", self.content)
        self.assertIn("不能使用 BOOT.bin/.bin", self.content)
        self.assertIn("PROGRAM_FLASH_EXE", self.content)
        self.assertIn("XSDB_EXE", self.content)
        self.assertIn("HW_SERVER_EXE", self.content)
        self.assertIn('-flash_type "!AL321_PROGRAM_FLASH_TYPE!"', self.content)
        self.assertIn('-fsbl "%TARGET_CONFIG_FILE%"', self.content)
        self.assertIn("ZynqMP QSPI Flash固化完成", self.content)
        self.assertIn("--detect -v", self.content)
        self.assertIn("预检测显示未发现目标 FPGA", self.content)

    def test_flash_mode_parameters_are_declared(self):
        default_config = next(item for item in SYSTEM_SCRIPT_CATALOG if item["name"] == "al321_fpga_mcu_flash")["default_config"]
        self.assertEqual(
            default_config["qspi_flash_model_options"],
            [
                "qspi-x1-single",
                "qspi-x2-single",
                "qspi-x4-single",
                "qspi-x8-dual_parallel",
                "qspi-x1-dual_stacked",
                "qspi-x2-dual_stacked",
                "qspi-x4-dual_stacked",
            ],
        )
        self.assertEqual(default_config["qspi_flash_model"], "qspi-x8-dual_parallel")
        self.assertIn("qspi-x4-single", self.content)
        self.assertIn("qspi-x8-dual_parallel", self.content)
        self.assertIn("qspi-x4-dual_stacked", self.content)
        self.assertIn("{fsbl}", self.content)
        self.assertIn("{flash_type}", self.content)
        self.assertIn('"%PROGRAM_FLASH_EXE%" -help', self.content)
        self.assertIn('"%PROGRAM_FLASH_EXE%" -jtagtargets -url TCP:127.0.0.1:3121', self.content)
        self.assertIn("AL321_PROGRAM_FLASH_STREAM_SCRIPT", self.content)
        self.assertIn("run-program-flash-stream.ps1", self.content)
        self.assertNotIn('type "!AL321_PROGRAM_FLASH_LOG!"', self.content)
        self.assertIn("AL321 Flash固化预检通过", self.content)
        self.assertIn("PROGRAM_FLASH_EXE=%PROGRAM_FLASH_EXE%", self.content)
        self.assertIn("已从 D:\\vitis 发现 program_flash", self.content)
        self.assertIn('"%XSDB_EXE%" "!AL321_XSDB_SCRIPT!"', self.content)
        self.assertIn("AL321_PROGRAM_FLASH_TYPE", self.content)
        self.assertIn('if "!QSPI_FLASH_MODEL!"=="" set "QSPI_FLASH_MODEL=qspi-x8-dual_parallel"', self.content)
        self.assertIn('set "AL321_PROGRAM_FLASH_TYPE=!QSPI_FLASH_MODEL!"', self.content)
        self.assertIn('if /I "!AL321_PROGRAM_FLASH_TYPE!"=="qspi-x4-dual-parallel" set "AL321_PROGRAM_FLASH_TYPE=qspi-x8-dual_parallel"', self.content)
        self.assertIn('if /I "!AL321_PROGRAM_FLASH_TYPE!"=="qspi-x4-dual-stacked" set "AL321_PROGRAM_FLASH_TYPE=qspi-x4-dual_stacked"', self.content)
        self.assertNotIn("__PCIDS_AL321_XSDB_SETUP__", self.content)
        self.assertNotIn("_al321_xsdb_scan_script_setup().rstrip()", self.content)

    def test_reports_zynqmp_psu_side_flash_limitation(self):
        self.assertIn("SPI Flash access is only available from PSU side", self.content)
        self.assertIn("can't flash non-volatile memory for ZynqMP devices", self.content)
        self.assertIn("已检测到 ZynqMP JTAG 器件", self.content)
        self.assertIn("请改用 PSU/启动侧烧写方案", self.content)

    def test_preserves_detect_exit_code_before_printing_log(self):
        self.assertIn('set "AL321_DETECT_EXIT=!ERRORLEVEL!"', self.content)
        self.assertIn('if not "!AL321_DETECT_EXIT!"=="0"', self.content)

    def test_pid_0008_does_not_pass_vid_pid_probe_firmware(self):
        self.assertIn('if /I "!AL321_ONLY_PID!"=="0008"', self.content)
        self.assertIn('set "AL321_CABLE=xilinxPlatformCableUsb"', self.content)
        self.assertNotIn('--vid', self.content)
        self.assertNotIn('--pid', self.content)

    def test_pid_6014_autodetects_one_compatible_ftdi_cable(self):
        self.assertIn('if /I "!AL321_ONLY_PID!"=="6014"', self.content)
        self.assertIn("AL321_OPENFPGALOADER_CABLE", self.content)
        self.assertIn("digilent_hs2 digilent_hs3 digilent_ad", self.content)
        self.assertNotIn("ft232", self.content)
        self.assertIn("正在只读探测兼容 cable 类型", self.content)
        self.assertIn("安装 WinUSB", self.content)
        self.assertIn("AL321_MATCHED_COUNT", self.content)
        self.assertIn("不支持安全按序列号锁定", self.content)
        self.assertIn("禁止执行", self.content)
        self.assertIn("found 0 devices", self.content)

    def test_pid_0013_uses_xilinxplatformcableusb_and_xusb_xp2(self):
        self.assertIn('if /I "!AL321_ONLY_PID!"=="0013"', self.content)
        self.assertIn('share\\openFPGALoader\\compat\\firmware\\xusb_xp2.hex', self.content)

    def test_pid_000d_uses_xilinxplatformcableusb_alt_and_xusb_emb(self):
        self.assertIn('if /I "!AL321_ONLY_PID!"=="000D"', self.content)
        self.assertIn('set "AL321_CABLE=xilinxPlatformCableUsb_alt"', self.content)
        self.assertIn('share\\openFPGALoader\\compat\\firmware\\xusb_emb.hex', self.content)

    def test_pid_0007_000f_explicitly_aborts(self):
        self.assertIn('当前 PID !AL321_ONLY_PID! 尚未验证，请配置 AL321_CMD_TEMPLATE', self.content)

    def test_burner_sn_executes_device_count_check(self):
        self.assertIn('if "!AL321_DEVICE_COUNT!"=="0"', self.content)
        self.assertIn('if not "!AL321_MATCHED_COUNT!"=="1"', self.content)
        self.assertIn('if not "%BURNER_SN%"==""', self.content)
        self.assertIn('echo "%%B" | findstr /I /C:"%BURNER_SN%" >nul', self.content)

    def test_flash_mode_requires_unique_amd_cable_and_expected_zynqmp_target(self):
        self.assertIn("BURNER_SN=%BURNER_SN% 的 cable 精确匹配数量", self.content)
        self.assertIn("当前 hw_server 下检测到 !AL321_ALL_CABLE_COUNT! 条 cable", self.content)
        self.assertIn("program_flash 官方只读枚举结果中，期望 ZynqMP arm_dap", self.content)
        self.assertIn("AL321_PROGRAM_TARGET_NAME", self.content)
        self.assertIn("AL321_PROGRAM_TARGET_ID", self.content)
        self.assertIn("arm_dap", self.content)

    def test_flash_mode_manages_hw_server_lifecycle_and_driver_state(self):
        self.assertIn("TCP:127.0.0.1:3121", self.content)
        self.assertIn("hw_server 未运行，正在启动并等待就绪", self.content)
        self.assertIn("正在停止本次启动的 hw_server", self.content)
        self.assertIn("pcids_al321_driver_state.json", self.content)
        self.assertIn("-Mode recover-pending", self.content)
        self.assertIn('-Mode amd -Serial "%BURNER_SN%" -StateFile', self.content)
        self.assertIn('-Mode winusb -StateFile', self.content)

    def test_flash_mode_prints_driver_switch_detail_log_on_failures(self):
        self.assertIn("call :PCIDS_PRINT_AL321_DRIVER_SWITCH_LOG", self.content)
        self.assertIn(":PCIDS_PRINT_AL321_DRIVER_SWITCH_LOG", self.content)
        self.assertIn("al321-driver-switch-*.log", self.content)
        self.assertIn("AL321驱动切换详细日志", self.content)
        self.assertIn("AL321_DRIVER_SWITCH_STDOUT_LOG", self.content)
        self.assertIn('type "!AL321_DRIVER_SWITCH_STDOUT_LOG!"', self.content)
        self.assertIn("当前 AL321 是 FTDI/WinUSB 型设备。Vitis 自带 xpcwinusb.inf 是 03FD Xilinx Cable 驱动，不能用于该设备。", self.content)

    def test_flash_driver_switch_stays_inside_flash_mode_branch(self):
        self.assertNotIn(')\n          )\n          if not "%AL321_AUTO_DRIVER_SWITCH%"=="0"', self.content)
        flash_guard_index = self.content.index('if "%AL321_OPERATION%"=="Flash固化"')
        switch_guard_index = self.content.index('if not "%AL321_AUTO_DRIVER_SWITCH%"=="0"')
        openfpgaloader_index = self.content.index('if "%OPENFPGALOADER_EXE%"==""')
        self.assertLess(flash_guard_index, switch_guard_index)
        self.assertLess(switch_guard_index, openfpgaloader_index)

    def test_flash_mode_keeps_cable_checks_even_when_auto_driver_switch_is_disabled(self):
        switch_guard_index = self.content.index('if not "%AL321_AUTO_DRIVER_SWITCH%"=="0"')
        cable_check_index = self.content.index('if not "!AL321_CABLE_MATCH_COUNT!"=="1"')
        self.assertLess(switch_guard_index, cable_check_index)
        self.assertIn('echo [ERROR] AMD 官方工具只读枚举结果中，BURNER_SN=%BURNER_SN% 的 cable 精确匹配数量为 !AL321_CABLE_MATCH_COUNT!，已拒绝执行。', self.content)

    def test_flash_mode_rejects_multi_cable_multi_target_and_program_flash_failures(self):
        self.assertIn('echo [ERROR] 当前 hw_server 下检测到 !AL321_ALL_CABLE_COUNT! 条 cable。由于当前 program_flash 命令未验证支持按序列号精确传参，已拒绝执行。', self.content)
        self.assertIn('echo [ERROR] program_flash 官方只读枚举结果中，期望 ZynqMP arm_dap 目标精确匹配数量为 !AL321_PROGRAM_TARGET_COUNT!，已拒绝执行。', self.content)
        self.assertIn('-target_id "!AL321_PROGRAM_TARGET_ID!" -url TCP:127.0.0.1:3121', self.content)
        self.assertIn('-target_name "!AL321_PROGRAM_TARGET_NAME!" -url TCP:127.0.0.1:3121', self.content)
        self.assertIn('run-program-flash-stream.ps1', self.content)
        self.assertIn('-TargetId "!AL321_PROGRAM_TARGET_ID!"', self.content)
        self.assertIn('-TargetName "!AL321_PROGRAM_TARGET_NAME!"', self.content)
        self.assertIn('program_flash 拒绝了 target_id，正在回退为 target_name=!AL321_PROGRAM_TARGET_NAME! 重试一次。', self.content)
        self.assertIn('findstr /I /C:"Given target do not exist" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('findstr /I /C:"Wrong flash_type specified" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('findstr /I /C:"Retrieving Flash info" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('findstr /I /C:"Flash Operation Failed" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('findstr /I /C:"Initialization done, programming the memory" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('findstr /I /C:"Problem in Connecting to Target" /C:"Flash programming initialization failed" /C:"Error getting stream information for target node" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('失败位置: mini u-boot target stream', self.content)
        self.assertIn('findstr /I /C:"ERROR:" /C:"Flash Operation Failed" /C:"Failed to" /C:"Wrong flash_type specified" "!AL321_PROGRAM_FLASH_LOG!" >nul', self.content)
        self.assertIn('if "!AL321_PROGRAM_FLASH_EXIT!"=="0" set "AL321_PROGRAM_FLASH_EXIT=1"', self.content)
        self.assertIn('当前 program_flash 不支持 flash_type=!AL321_PROGRAM_FLASH_TYPE!', self.content)
        self.assertIn('qspi-x4-dual_stacked，不要使用 qspi-x8-dual_stacked', self.content)
        self.assertIn('失败位置: Retrieving Flash info', self.content)
        self.assertIn('ZynqMP arm_dap/PS 访问目标', self.content)
        self.assertIn('常见 ZynqMP QSPI 拓扑候选', self.content)
        self.assertIn('如果 qspi-x4-single 和 qspi-x8-dual_parallel 都已在同一块板上失败', self.content)
        self.assertIn('这是收尾恢复步骤失败，不是 Flash 信息读取失败的根因', self.content)
        self.assertIn('set "AL321_PROGRAM_FLASH_EXIT=!ERRORLEVEL!"', self.content)
        self.assertIn('echo [ERROR] ZynqMP QSPI Flash固化失败。请按上方具体错误类型处理', self.content)


if __name__ == "__main__":
    unittest.main()
