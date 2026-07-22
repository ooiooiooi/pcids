from __future__ import annotations

import base64
import json
from textwrap import dedent


COMMON_ACTIONS = ["复位运行", "仅复位", "不处理"]
ERASE_OPTIONS = ["全片擦除", "扇区擦除", "不擦除"]
FPGA_ERASE_OPTIONS = ["默认自动擦除", "全片擦除", "扇区擦除", "不擦除"]
SPEED_OPTIONS = [500, 1000, 2000, 4000, 5000, 10000]
HDSC_BAUD_OPTIONS = [115200, 128000, 230400, 256000, 1000000]
PYOCD_TARGET_ALIASES = {
    "LPC55S69": "lpc55s69",
    "LPC5526": "lpc5526",
    "LPC55S16": "lpc55s16",
    "NRF52832": "nrf52832",
    "NRF52833": "nrf52833",
    "NRF52840": "nrf52840",
    "RP2040": "rp2040",
}
PYOCD_TARGET_REGEX_RULES = [
    (r"^STM32F051[A-Z0-9]+$", "stm32f051"),
    (r"^STM32F103RC[A-Z0-9]*$", "stm32f103rc"),
    (r"^STM32F10[57][A-Z0-9]+$", "stm32f103rc"),
    (r"^STM32F412[A-Z]E[A-Z0-9]*$", "stm32f412xe"),
    (r"^STM32F412[A-Z]G[A-Z0-9]*$", "stm32f412xg"),
    (r"^STM32F429[A-Z]G[A-Z0-9]*$", "stm32f429xg"),
    (r"^STM32F429[A-Z]I[A-Z0-9]*$", "stm32f429xi"),
    (r"^STM32F439[A-Z]G[A-Z0-9]*$", "stm32f439xg"),
    (r"^STM32F439[A-Z]I[A-Z0-9]*$", "stm32f439xi"),
    (r"^STM32F767[A-Z0-9]+$", "stm32f767zi"),
    (r"^STM32L031[A-Z]6[A-Z0-9]*$", "stm32l031x6"),
    (r"^STM32L432[A-Z]C[A-Z0-9]*$", "stm32l432kc"),
    (r"^STM32L475[A-Z]C[A-Z0-9]*$", "stm32l475xc"),
    (r"^STM32L475[A-Z]E[A-Z0-9]*$", "stm32l475xe"),
    (r"^STM32L475[A-Z]G[A-Z0-9]*$", "stm32l475xg"),
    (r"^STM32H723[A-Z0-9]+$", "stm32h723xx"),
    (r"^STM32H743[A-Z0-9]+$", "stm32h743xx"),
    (r"^STM32H750[A-Z0-9]+$", "stm32h750xx"),
    (r"^STM32H7B0[A-Z0-9]+$", "stm32h7b0xx"),
    (r"^LPC55S69[A-Z0-9]*$", "lpc55s69"),
    (r"^LPC5526[A-Z0-9]*$", "lpc5526"),
    (r"^LPC55S16[A-Z0-9]*$", "lpc55s16"),
    (r"^LPC55S36[A-Z0-9]*$", "lpc55s36"),
    (r"^LPC54(114|XXX|[A-Z0-9]+)$", "lpc54114"),
    (r"^LPC5460[0-9][A-Z0-9]*$", "lpc54608"),
    (r"^LPC82[0-9][A-Z0-9]*$", "lpc824"),
    (r"^LPC84[0-9][A-Z0-9]*$", "lpc845"),
    (r"^LPC80[0-9][A-Z0-9]*$", "lpc800"),
    (r"^NRF52832[A-Z0-9]*$", "nrf52832"),
    (r"^NRF52833[A-Z0-9]*$", "nrf52833"),
    (r"^NRF52840[A-Z0-9]*$", "nrf52840"),
    (r"^NRF52[A-Z0-9]*$", "nrf52"),
    (r"^NRF91[A-Z0-9]*$", "nrf91"),
    (r"^RP2040([A-Z0-9_]+)?$", "rp2040"),
    (r"^MIMXRT101[0-5][A-Z0-9_]*$", "mimxrt1010"),
    (r"^MIMXRT102[0-9][A-Z0-9_]*$", "mimxrt1020"),
    (r"^MIMXRT105[0-9][A-Z0-9_]*$", "mimxrt1050"),
    (r"^MIMXRT106[0-9][A-Z0-9_]*$", "mimxrt1060"),
    (r"^MIMXRT117[0-9][A-Z0-9_]*CM7$", "mimxrt1170_cm7"),
    (r"^MIMXRT117[0-9][A-Z0-9_]*CM4$", "mimxrt1170_cm4"),
    (r"^MIMXRT117[0-9][A-Z0-9_]*$", "mimxrt1170_cm7"),
    (r"^HC32F003[A-Z0-9]*$", "hc32f003"),
    (r"^HC32F005[A-Z0-9]*$", "hc32f005"),
    (r"^HC32F030[A-Z0-9]*$", "hc32f030"),
    (r"^HC32F072[A-Z0-9]*$", "hc32f072"),
    (r"^HC32F115[A-Z0-9]*$", "hc32f115"),
    (r"^HC32F120[A-Z0-9]*$", "hc32f120"),
    (r"^HC32F155[A-Z0-9]*$", "hc32f155"),
    (r"^HC32F160[A-Z0-9]*$", "hc32f160"),
    (r"^HC32F190[A-Z0-9]*$", "hc32f190"),
    (r"^HC32F196[A-Z0-9]*$", "hc32f196"),
    (r"^HC32F334[A-Z0-9]*$", "hc32f334"),
    (r"^HC32F448[A-Z0-9]*$", "hc32f448"),
    (r"^HC32F451[A-Z0-9]*$", "hc32f451"),
    (r"^HC32F452[A-Z0-9]*$", "hc32f452"),
    (r"^HC32F460[A-Z0-9]*$", "hc32f460"),
    (r"^HC32F467[A-Z0-9]*$", "hc32f467"),
    (r"^HC32F472[A-Z0-9]*$", "hc32f472"),
    (r"^HC32F4A0[A-Z0-9]*$", "hc32f4a0"),
    (r"^HC32F4A2[A-Z0-9]*$", "hc32f4a2"),
    (r"^HC32L072[A-Z0-9]*$", "hc32l072"),
    (r"^HC32L073[A-Z0-9]*$", "hc32l073"),
    (r"^HC32L110[A-Z0-9]*$", "hc32l110"),
    (r"^HC32L130[A-Z0-9]*$", "hc32l130"),
    (r"^HC32L136[A-Z0-9]*$", "hc32l136"),
    (r"^HC32L190[A-Z0-9]*$", "hc32l190"),
    (r"^HC32L196[A-Z0-9]*$", "hc32l196"),
    (r"^HC32M120[A-Z0-9]*$", "hc32m120"),
    (r"^HC32M423[A-Z0-9]*$", "hc32m423xa"),
]


def arm_config(ide_name: str, interface_options: list[str] | None = None, interface_type: str = "SWD") -> dict:
    return {
        "ide_name": ide_name,
        "interface_type": interface_type,
        "interface_type_label": "接口类型",
        "interface_type_options": interface_options or ["SWD", "JTAG", "CJTAG"],
        "erase_mode": "全片擦除",
        "erase_mode_label": "擦除方式",
        "erase_mode_options": ERASE_OPTIONS,
        "write_speed_khz": 1000,
        "speed_label": "频率(kHz)",
        "speed_options": SPEED_OPTIONS,
        "start_address": "",
        "start_address_label": "起始地址",
        "completion_action": "复位运行",
        "completion_action_label": "完成后动作",
        "completion_action_options": COMMON_ACTIONS,
        "options": ["local", "integrity", "writeVerify"],
        "retry_count": 1,
        "timeout_minutes": 120,
    }


def stlink_config() -> dict:
    return {
        **arm_config("STM32CubeIDE"),
        "qspi_flash_model": "W25Q128",
        "qspi_flash_model_label": "QSPI Flash",
        "qspi_flash_model_options": ["W25Q64", "W25Q128", "W25Q256"],
        "loader_type": "Internal Flash",
        "loader_type_label": "Loader",
        "loader_type_options": ["Internal Flash", "External Loader"],
    }


SYSTEM_SCRIPT_CATALOG = [
    {"name": "stlink_stm32_mcu_flash", "burner": "ST-LINK", "type": "bat", "default_config": stlink_config()},
    {"name": "jlink_v4_arm_mcu_flash", "burner": "J-LINK", "type": "bat", "default_config": arm_config("Keil uVision")},
    {"name": "pwlink_v2_arm_mcu_flash", "burner": "PWLINK2", "type": "bat", "default_config": arm_config("Keil uVision", ["SWD", "JTAG"])},
    {
        "name": "gdlink_arm_mcu_flash",
        "burner": "GDLINK",
        "type": "bat",
        "default_config": {
            **arm_config("Keil uVision", ["SWD", "JTAG"]),
            "bichina_burn_mode": "单烧",
            "bichina_burn_mode_label": "Bichina烧录参数",
            "bichina_burn_mode_options": ["单烧", "量产烧录", "擦除后烧录"],
        },
    },
    {"name": "swd_downloader_arm_mcu_flash", "burner": "SWD下载器", "type": "bat", "default_config": arm_config("Keil uVision")},
    {
        "name": "al321_fpga_mcu_flash",
        "burner": "AL321",
        "type": "bat",
        "default_config": {
            "ide_name": "Vivado/ISE",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "execution_operation": "SRAM下载",
            "execution_operation_label": "执行操作",
            "execution_operation_options": ["SRAM下载", "Flash固化"],
            "qspi_flash_model": "qspi-x8-dual_parallel",
            "qspi_flash_model_label": "QSPI连接方式",
            "qspi_flash_model_options": [
                "qspi-x1-single",
                "qspi-x2-single",
                "qspi-x4-single",
                "qspi-x8-dual_parallel",
                "qspi-x1-dual_stacked",
                "qspi-x2-dual_stacked",
                "qspi-x4-dual_stacked",
            ],
            "target_config_file": "",
            "target_config_file_label": "ZynqMP ELF文件(.elf)",
            "start_address": "0x0",
            "start_address_label": "Flash偏移地址",
            "erase_mode": "默认自动擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": FPGA_ERASE_OPTIONS,
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "hdsc_ccid_arm_mcu_flash",
        "burner": "HDSC CCID",
        "type": "bat",
        "default_config": {
            **arm_config("HDSC ISP", ["UART"], "UART"),
            # Keep the legacy storage key for task compatibility; HDSC uses
            # it as an actual UART baud value, not a frequency in kHz.
            "write_speed_khz": 115200,
            "speed_label": "波特率",
            "speed_options": HDSC_BAUD_OPTIONS,
        },
    },
    {
        "name": "xds510plus_dsp_flash",
        "burner": "XDS510plus",
        "type": "bat",
        "default_config": {
            "ide_name": "Code Composer Studio",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "target_config_file": "",
            "target_config_file_label": "目标配置文件（.ccxml）",
            "target_config_file_placeholder": "可留空，默认使用内置 SEED F28335 配置",
            "target_config_file_hint": "默认配置适用于 SEED XDS510Plus + TMS320F28335；连接其他受支持的 TI DSP 时，请指定由 CCS 导出的 .ccxml 文件。",
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除"],
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "mplab_icd3_pic_flash",
        "burner": "MPLAB ICD 3 DV164035",
        "type": "bat",
        "default_config": {
            "ide_name": "MPLAB",
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "不擦除直接编程"],
            "eeprom_write": "否",
            "eeprom_write_label": "EEPROM是否写入",
            "eeprom_write_options": ["是", "否"],
            "blank_check": "否",
            "blank_check_label": "空白检查",
            "blank_check_options": ["是", "否"],
            "execute_program": "是",
            "execute_program_label": "执行编程",
            "execute_program_options": ["是", "否"],
            "completion_action": "编程复位后运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["编程复位后运行", "编程后保持复位"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "altera_blaster_ii_fpga_flash",
        "burner": "Altera Blaster II",
        "type": "bat",
        "default_config": {
            "ide_name": "Intel Quartus Programmer",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "erase_mode": "默认自动擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": FPGA_ERASE_OPTIONS,
            "completion_action": "不处理",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["不处理"],
            "cable_index": 0,
            "cable_index_label": "Cable Index",
            "cable_index_options": [0, 1, 2, 3],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "altera_blaster_ii_cpld_flash",
        "burner": "Altera Blaster II",
        "type": "bat",
        "default_config": {
            "ide_name": "Intel Quartus Programmer",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "pre_erase": "默认是",
            "pre_erase_label": "擦除器件",
            "pre_erase_options": ["默认是", "否"],
            "completion_action": "不处理",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["不处理"],
            "tck_frequency": "2.5MHz",
            "tck_frequency_label": "TCK频率",
            "tck_frequency_options": ["1MHz", "2.5MHz", "5MHz", "10MHz"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "gowin_usb_cable_fpga_flash",
        "burner": "Gowin USB Cable",
        "type": "bat",
        "default_config": {
            "ide_name": "Gowin Programmer",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "execution_operation": "SRAM下载",
            "execution_operation_label": "执行操作",
            "execution_operation_options": ["SRAM下载", "Flash固化"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ERASE_OPTIONS,
            "completion_action": "不处理",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "sd_card_zynq7000_boot_update",
        "burner": "SD卡文件写入",
        "type": "bat",
        "default_config": {
            "ide_name": "",
            "interface_type": "SD卡",
            "interface_type_options": ["SD卡"],
            "sd_target_path": "",
            "sd_target_path_label": "目标SD卡位置",
            "format_sd_card": "否",
            "format_sd_card_options": ["是", "否"],
            "completion_action": "自动弹出SD卡",
            "completion_action_options": ["自动弹出SD卡", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "sylixos_ls2k_ftp_serial_flash",
        "burner": "TFTP+串口",
        "type": "shell",
        "task_type": "hybrid",
        "default_config": {
            "burn_mode": "TFTP+串口",
            "transfer_protocol": "TFTP+串口",
            "server_port": 69,
            "serial_port": "",
            "baud_rate": "115200",
            "serial_login_user": "root",
            "serial_passwordless": True,
            "ftp_login_user": "root",
            "ftp_login_password": "root",
            "ftp_passwordless": False,
            "configured_board_address": "192.168.1.230",
            "board_target_address": "192.168.1.230",
            "local_ip": "192.168.1.100",
            "target_path": "/media/hdd0",
            "sylixos_netmask": "255.255.255.0",
            "hdd0_source_path": "",
            "hdd1_source_path": "",
            "hdd0_remote_path": "/media/hdd0",
            "hdd1_remote_path": "/media/hdd1",
            "options": ["integrity"],
            "retry_count": 1,
            "timeout_seconds": 300,
        },
    },
]


SYSTEM_SCRIPT_BINDINGS = {
    "stlink_stm32_mcu_flash": {"ide_name": "STM32CubeIDE", "associated_ide": "STM32CubeIDE", "associated_board": "STM32F407VGT6开发板", "associated_burner": "ST-LINK"},
    "jlink_v4_arm_mcu_flash": {"ide_name": "Keil uVision", "associated_ide": "Keil uVision", "associated_board": "LPC55S69评估板,STM32F407VGT6开发板", "associated_burner": "J-LINK"},
    "pwlink_v2_arm_mcu_flash": {"ide_name": "Keil uVision", "associated_ide": "Keil uVision", "associated_board": "STM32F407VGT6开发板,LPC55S69评估板", "associated_burner": "PWLINK2"},
    "gdlink_arm_mcu_flash": {"ide_name": "Keil uVision", "associated_ide": "Keil uVision", "associated_board": "GD32开发板,STM32F407VGT6开发板", "associated_burner": "GDLINK"},
    "swd_downloader_arm_mcu_flash": {"ide_name": "Keil uVision", "associated_ide": "Keil uVision", "associated_board": "ARM Cortex-M开发板", "associated_burner": "SWD下载器"},
    "al321_fpga_mcu_flash": {"ide_name": "Vivado/ISE", "associated_ide": "Vivado/ISE", "associated_board": "Xilinx FPGA开发板", "associated_burner": "AL321"},
    "hdsc_ccid_arm_mcu_flash": {"ide_name": "HDSC ISP", "associated_ide": "HDSC ISP", "associated_board": "HDSC MCU开发板", "associated_burner": "HDSC CCID"},
    "xds510plus_dsp_flash": {"ide_name": "Code Composer Studio", "associated_ide": "Code Composer Studio", "associated_board": "TI系列板卡,TMS320F28335,DSP", "associated_burner": "XDS510plus"},
    "mplab_icd3_pic_flash": {"ide_name": "MPLAB", "associated_ide": "MPLAB", "associated_board": "PIC32MZ开发板", "associated_burner": "MPLAB ICD 3 DV164035"},
    "altera_blaster_ii_fpga_flash": {"ide_name": "Intel Quartus Programmer", "associated_ide": "Intel Quartus Programmer", "associated_board": "CycloneV FPGA板", "associated_burner": "Altera Blaster II"},
    "altera_blaster_ii_cpld_flash": {"ide_name": "Intel Quartus Programmer", "associated_ide": "Intel Quartus Programmer", "associated_board": "EPM240T100开发板", "associated_burner": "Altera Blaster II"},
    "gowin_usb_cable_fpga_flash": {"ide_name": "Gowin Programmer", "associated_ide": "Gowin Programmer", "associated_board": "Gowin FPGA", "associated_burner": "Gowin USB Cable"},
    "sd_card_zynq7000_boot_update": {"associated_board": "Zynq7000/RK3568核心板", "associated_burner": "SD卡文件写入"},
    "sylixos_ls2k_ftp_serial_flash": {"associated_board": "翼辉SylixOS,LS2K,龙芯2K,bspls2kpcm2k01", "associated_burner": "TFTP+串口"},
}


TOOL_REQUIREMENTS = [
    {"burner": "ST-LINK", "tool": "STM32CubeProgrammer CLI", "env": "STM32_PROGRAMMER_CLI", "download": "https://www.st.com/en/development-tools/stm32cubeprog.html"},
    {"burner": "J-LINK", "tool": "SEGGER J-Link Software", "env": "JLINK_EXE", "download": "https://www.segger.com/downloads/jlink/"},
    {"burner": "PWLINK2", "tool": "PowerWriter/PWLINK 官方工具", "env": "PWLINK2_CMD_TEMPLATE 或 POWERWRITER_CLI", "download": "https://docs.powerwriter.com/"},
    {"burner": "GDLINK", "tool": "GigaDevice/GD-Link Programmer", "env": "GDLINK_CMD_TEMPLATE 或 GDLINK_CLI", "download": "https://www.gigadevice.com/"},
    {"burner": "SWD下载器", "tool": "pyOCD / OpenOCD / 厂商 SWD CLI", "env": "SWD_CMD_TEMPLATE / PYOCD_EXE / OPENOCD_EXE", "download": "https://pyocd.io/"},
    {"burner": "AL321", "tool": "Bundled openFPGALoader", "env": "AL321_CMD_TEMPLATE 或 OPENFPGALOADER_EXE", "download": "https://github.com/trabucayre/openFPGALoader"},
    {"burner": "HDSC CCID", "tool": "Bundled HDSC CCID V6.04 agent", "env": "HDSC_CCID_V604_EXE（可选覆盖）", "download": "https://www.hdsc.com.cn/"},
    {"burner": "XDS510plus", "tool": "CCS 5.5 Legacy UniFlash + SEED XDS510Plus plugin", "env": "DSS_BAT", "download": "https://www.ti.com/tool/CCSTUDIO"},
    {"burner": "MPLAB ICD 3 DV164035", "tool": "MPLAB X IPE ipecmd", "env": "IPECMD_EXE", "download": "https://www.microchip.com/en-us/tools-resources/develop/mplab-x-ide"},
    {"burner": "Altera Blaster II", "tool": "Intel Quartus Programmer CLI", "env": "QUARTUS_PGM", "download": "https://www.intel.com/content/www/us/en/software-kit/795188/intel-quartus-prime-lite-edition-design-software-version-23-1-1-for-windows.html"},
    {"burner": "Gowin USB Cable", "tool": "Gowin Programmer CLI", "env": "GOWIN_PROGRAMMER_CLI", "download": "https://www.gowinsemi.com/en/support/download_eda/"},
    {"burner": "SD卡文件写入", "tool": "Windows PowerShell/Robocopy", "env": "SD_TARGET_PATH", "download": "Windows 10 built-in"},
]


for _tool_requirement in TOOL_REQUIREMENTS:
    if _tool_requirement.get("burner") == "PWLINK2":
        _tool_requirement.update({"tool": "pyOCD", "env": "PYOCD_EXE", "download": "https://pyocd.io/"})


def _batch_header(script_name: str, burner_name: str) -> str:
    return dedent(
        f"""\
        @echo off
        setlocal EnableExtensions EnableDelayedExpansion
        set "SCRIPT_NAME={script_name}"
        set "BURNER_NAME={burner_name}"
        echo [INFO] PCIDS burner script started: %SCRIPT_NAME%
        echo [INFO] Associated burner type: %BURNER_NAME%
        echo [CONFIG] ===== Effective script parameters =====
        echo [CONFIG] TASK_ID=%TASK_ID%
        echo [CONFIG] TASK_TYPE=%TASK_TYPE%
        echo [CONFIG] SCRIPT_NAME=%SCRIPT_NAME%
        echo [CONFIG] BURNER_NAME=%BURNER_NAME%
        echo [CONFIG] BURNER_SN=%BURNER_SN%
        rem USB instance paths may contain cmd metacharacters such as '&'. Keep the
        rem expanded values quoted so diagnostic output cannot be executed as a command.
        echo [CONFIG] BURNER_PORT="%BURNER_PORT%"
        echo [CONFIG] BURNER_LOCATION="%BURNER_LOCATION%"
        echo [CONFIG] TARGET_CHIP=%TARGET_CHIP%
        echo [CONFIG] FIRMWARE_PATH=%FIRMWARE_PATH%
        echo [CONFIG] IDE_NAME=%IDE_NAME%
        echo [CONFIG] INTERFACE_TYPE=%INTERFACE_TYPE%
        echo [CONFIG] TCK_FREQUENCY=%TCK_FREQUENCY%
        echo [CONFIG] WRITE_SPEED_KHZ=%WRITE_SPEED_KHZ%
        echo [CONFIG] START_ADDRESS=%START_ADDRESS%
        echo [CONFIG] ERASE_MODE=%ERASE_MODE%
        echo [CONFIG] WRITE_VERIFY=%WRITE_VERIFY%
        echo [CONFIG] COMPLETION_ACTION=%COMPLETION_ACTION%
        echo [CONFIG] CABLE_INDEX=%CABLE_INDEX%
        echo [CONFIG] QSPI_FLASH_MODEL=%QSPI_FLASH_MODEL%
        echo [CONFIG] TARGET_CONFIG_FILE=%TARGET_CONFIG_FILE%
        echo [CONFIG] GEL_INIT_SCRIPT=%GEL_INIT_SCRIPT%
        echo [CONFIG] JTAG_CHAIN_INDEX=%JTAG_CHAIN_INDEX%
        echo [CONFIG] PROGRAM_VOLTAGE=%PROGRAM_VOLTAGE%
        echo [CONFIG] EEPROM_WRITE=%EEPROM_WRITE%
        echo [CONFIG] WRITE_CONFIG_BITS=%WRITE_CONFIG_BITS%
        echo [CONFIG] PRE_ERASE=%PRE_ERASE%
        echo [CONFIG] BLANK_CHECK=%BLANK_CHECK%
        echo [CONFIG] EXECUTE_PROGRAM=%EXECUTE_PROGRAM%
        echo [CONFIG] SD_TARGET_PATH=%SD_TARGET_PATH%
        echo [CONFIG] TIMEOUT_SECONDS=%TIMEOUT_SECONDS%
        echo [CONFIG] =======================================
        if "%FIRMWARE_PATH%"=="" (
          echo [ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH。
          exit /b 2
        )
        if not exist "%FIRMWARE_PATH%" (
          echo [ERROR] 固件文件不存在: %FIRMWARE_PATH%
          exit /b 2
        )
        """
    )


def _compose_batch_script(*parts: str) -> str:
    content = "".join(part for part in parts if part)
    trailing_newline = content.endswith("\n")
    cleaned_lines = [line for line in content.splitlines() if line.strip() != "\\"]
    cleaned = "\n".join(cleaned_lines)
    if trailing_newline:
        cleaned += "\n"
    return cleaned


def _pyocd_preflight_helper_source() -> str:
    alias_json = json.dumps(PYOCD_TARGET_ALIASES, sort_keys=True)
    regex_rules_json = json.dumps(PYOCD_TARGET_REGEX_RULES)
    return dedent(
        f"""\
        import argparse
        import difflib
        import json
        import re

        from pyocd import __version__ as pyocd_version
        from pyocd.core.helpers import ConnectHelper
        from pyocd.target import TARGET, normalise_target_type_name

        TARGET_ALIASES = {alias_json}
        TARGET_REGEX_RULES = {regex_rules_json}


        def _resolve_target(target_chip):
            raw = str(target_chip or "").strip()
            normalized_input = normalise_target_type_name(raw) if raw else ""
            candidates = []
            matched_by = ""
            target_keys = sorted(TARGET.keys())

            def _append_candidate(candidate):
                normalized_candidate = normalise_target_type_name(candidate) if candidate else ""
                if normalized_candidate and normalized_candidate not in candidates:
                    candidates.append(normalized_candidate)
                return normalized_candidate

            exact_candidate = _append_candidate(normalized_input)
            if exact_candidate and exact_candidate in TARGET:
                matched_by = "exact"
                resolved = exact_candidate
            else:
                resolved = ""
            alias_target = TARGET_ALIASES.get(raw.upper())
            if not resolved and alias_target:
                normalized_alias = _append_candidate(alias_target)
                if normalized_alias and normalized_alias in TARGET:
                    matched_by = "alias"
                    resolved = normalized_alias
            if not resolved:
                for pattern, regex_target in TARGET_REGEX_RULES:
                    if raw and re.fullmatch(pattern, raw.upper()):
                        normalized_regex_target = _append_candidate(regex_target)
                        if normalized_regex_target and normalized_regex_target in TARGET:
                            matched_by = "regex"
                            resolved = normalized_regex_target
                        break
            similarity_seed = candidates[-1] if candidates else (normalized_input or normalise_target_type_name(raw) or raw.lower())
            close_matches = difflib.get_close_matches(similarity_seed, target_keys, n=5) if similarity_seed else []
            return {{
                "input": raw,
                "normalized_input": normalized_input,
                "candidates": candidates,
                "resolved_target": resolved,
                "target_supported": bool(resolved),
                "matched_by": matched_by,
                "resolution_reason": matched_by,
                "close_matches": close_matches,
            }}


        def _collect_probes(expected_unique_id):
            probes = []
            for probe in ConnectHelper.get_all_connected_probes(blocking=False):
                probes.append(
                    {{
                        "unique_id": str(getattr(probe, "unique_id", "") or ""),
                        "description": str(getattr(probe, "description", "") or ""),
                        "vendor_name": str(getattr(probe, "vendor_name", "") or ""),
                        "product_name": str(getattr(probe, "product_name", "") or ""),
                    }}
                )
            matches = [probe for probe in probes if probe["unique_id"] == expected_unique_id]
            return probes, matches


        def main():
            parser = argparse.ArgumentParser()
            parser.add_argument("command", choices=["preflight"])
            parser.add_argument("--target-chip", required=True)
            parser.add_argument("--probe-unique-id", required=True)
            args = parser.parse_args()

            target_info = _resolve_target(args.target_chip)
            probes, matches = _collect_probes(str(args.probe_unique_id or ""))
            payload = {{
                "pyocd_version": pyocd_version,
                "target": target_info,
                "resolved_target": target_info["resolved_target"],
                "target_supported": target_info["target_supported"],
                "probe_unique_id": str(args.probe_unique_id or ""),
                "exact_match_count": len(matches),
                "matched_probes": matches,
                "probes": probes,
            }}
            print(json.dumps(payload, ensure_ascii=False))
            return 0


        if __name__ == "__main__":
            raise SystemExit(main())
        """
    )


def _pyocd_preflight_script_setup() -> str:
    helper_base64 = base64.b64encode(_pyocd_preflight_helper_source().encode("utf-8")).decode("ascii")
    return dedent(
        f"""\
        if "%PYOCD_PYTHON%"=="" (
          for %%I in ("%PYOCD_EXE%") do (
            if exist "%%~dpIpython.exe" if "%PYOCD_PYTHON%"=="" set "PYOCD_PYTHON=%%~dpIpython.exe"
            if exist "%%~dpI..\\python.exe" if "%PYOCD_PYTHON%"=="" set "PYOCD_PYTHON=%%~dpI..\\python.exe"
          )
        )
        if "%PYOCD_PYTHON%"=="" (
          echo [ERROR] pyOCD Python runtime not found. Configure PYOCD_PYTHON or bundle tools\\burners\\SWD_Downloader\\pyocd-runtime.
          exit /b 127
        )
        if not exist "%PYOCD_PYTHON%" (
          echo [ERROR] PYOCD_PYTHON 指向的文件不存在: %PYOCD_PYTHON%
          exit /b 127
        )
        set "PYOCD_HELPER=%TEMP%\\pcids_pyocd_preflight_%TASK_ID%.py"
        if "%TASK_ID%"=="" set "PYOCD_HELPER=%TEMP%\\pcids_pyocd_preflight.py"
        powershell -NoProfile -Command "$bytes=[System.Convert]::FromBase64String('{helper_base64}'); [System.IO.File]::WriteAllBytes($env:PYOCD_HELPER, $bytes)"
        set "PYOCD_HELPER_WRITE_EXIT=!ERRORLEVEL!"
        if not "!PYOCD_HELPER_WRITE_EXIT!"=="0" (
          echo [ERROR] 无法生成 pyOCD 安全预检辅助脚本。
          exit /b !PYOCD_HELPER_WRITE_EXIT!
        )
        """
    )


def _template_runner(template_env: str, cli_env: str, missing_hint: str) -> str:
    return dedent(
        f"""\
        if not "%{template_env}%"=="" (
          set "PCIDS_CMD=%{template_env}%"
          set "PCIDS_CMD=!PCIDS_CMD:{{firmware}}=%FIRMWARE_PATH%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{target}}=%TARGET_CHIP%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{probe}}=%BURNER_SN%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{interface}}=%INTERFACE_TYPE%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{speed}}=%WRITE_SPEED_KHZ%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{address}}=%START_ADDRESS%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{erase}}=%ERASE_MODE%!"
          set "PCIDS_CMD=!PCIDS_CMD:{{action}}=%COMPLETION_ACTION%!"
          {_stream_command_helper_setup().rstrip()}
          {_stream_command_runner().rstrip()}
          set "PCIDS_EXIT=!PCIDS_STREAM_EXIT!"
          exit /b !PCIDS_EXIT!
        )
        if "%{cli_env}%"=="" (
          echo [ERROR] 未配置 {cli_env} 或 {template_env}，无法执行官方工具自动化。
          echo [ERROR] {missing_hint}
          exit /b 127
        )
        """
    )


def _stream_command_helper_source() -> str:
    return dedent(
        """\
        param()

        $ErrorActionPreference = "Stop"
        $commandLine = [string]$env:PCIDS_STREAM_CMD
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            [Console]::Error.WriteLine("[ERROR] 未提供 PCIDS_STREAM_CMD，无法执行流式命令。")
            exit 2
        }

        $timeoutSeconds = 0
        $timeoutRaw = [string]$env:PCIDS_STREAM_TIMEOUT_SECONDS
        if ([string]::IsNullOrWhiteSpace($timeoutRaw)) {
            $timeoutRaw = "0"
        }
        [void][int]::TryParse($timeoutRaw, [ref]$timeoutSeconds)
        $cmdExe = [string]$env:ComSpec
        if ([string]::IsNullOrWhiteSpace($cmdExe)) {
            $cmdExe = "cmd.exe"
        }

        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $cmdExe
        $psi.Arguments = "/d /s /c $commandLine"
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true

        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi

        try {
            $null = $proc.Start()
            $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
            $stderrTask = $proc.StandardError.ReadToEndAsync()

            $timedOut = $false
            if ($timeoutSeconds -gt 0) {
                $timedOut = -not $proc.WaitForExit($timeoutSeconds * 1000)
            } else {
                $proc.WaitForExit()
            }

            if ($timedOut) {
                try {
                    & taskkill /PID $proc.Id /T /F | Out-Null
                } catch {
                }
                [Console]::Error.WriteLine("[ERROR] 脚本执行超时")
                Start-Sleep -Milliseconds 500
                exit 124
            }

            $proc.WaitForExit()
            $stdout = $stdoutTask.GetAwaiter().GetResult()
            $stderr = $stderrTask.GetAwaiter().GetResult()
            if (-not [string]::IsNullOrWhiteSpace($stdout)) {
                [Console]::Out.Write($stdout)
            }
            if (-not [string]::IsNullOrWhiteSpace($stderr)) {
                [Console]::Error.Write($stderr)
            }
            exit $proc.ExitCode
        } finally {
            if ($proc) {
                $proc.Dispose()
            }
        }
        """
    )


def _stream_command_helper_setup() -> str:
    # Windows PowerShell 5.1 reads script files using the active ANSI code page
    # when no BOM is present.  The helper includes Chinese diagnostics, so emit a
    # UTF-8 BOM to keep it valid on every supported Windows locale.
    helper_base64 = base64.b64encode(_stream_command_helper_source().encode("utf-8-sig")).decode("ascii")
    return dedent(
        f"""\
        set "PCIDS_STREAM_HELPER=%TEMP%\\pcids_stream_cmd_%TASK_ID%.ps1"
        if "%TASK_ID%"=="" set "PCIDS_STREAM_HELPER=%TEMP%\\pcids_stream_cmd.ps1"
        powershell -NoProfile -Command "$bytes=[System.Convert]::FromBase64String('{helper_base64}'); [System.IO.File]::WriteAllBytes($env:PCIDS_STREAM_HELPER, $bytes)"
        set "PCIDS_STREAM_HELPER_WRITE_EXIT=!ERRORLEVEL!"
        if not "!PCIDS_STREAM_HELPER_WRITE_EXIT!"=="0" (
          echo [ERROR] 无法生成命令实时流包装脚本。
          exit /b !PCIDS_STREAM_HELPER_WRITE_EXIT!
        )
        """
    )


def _stream_command_runner(command_var: str = "PCIDS_CMD", exit_var: str = "PCIDS_STREAM_EXIT") -> str:
    return dedent(
        f"""\
        echo [EXEC] !{command_var}!
        set "PCIDS_STREAM_CMD=!{command_var}!"
        set "PCIDS_STREAM_TIMEOUT_SECONDS=%TIMEOUT_SECONDS%"
        powershell -NoProfile -ExecutionPolicy Bypass -File "%PCIDS_STREAM_HELPER%"
        set "{exit_var}=!ERRORLEVEL!"
        del /f /q "%PCIDS_STREAM_HELPER%" >nul 2>nul
        """
    )


def _xds510plus_usb_preflight_source() -> str:
    return dedent(
        r"""
        $ErrorActionPreference = "Continue"
        $expectedPhysicalLocation = [string]$env:BURNER_PORT
        $expectedInstanceAnchor = [string]$env:BURNER_LOCATION
        if ([string]::IsNullOrWhiteSpace($expectedPhysicalLocation) -and $expectedInstanceAnchor -like "Port_#*") {
            $expectedPhysicalLocation = $expectedInstanceAnchor
        }
        if ([string]::IsNullOrWhiteSpace($expectedInstanceAnchor) -and -not [string]::IsNullOrWhiteSpace($expectedPhysicalLocation) -and $expectedPhysicalLocation -notlike "Port_#*") {
            $expectedInstanceAnchor = $expectedPhysicalLocation
        }
        $knownHardwareIds = @("USB\VID_0547&PID_1020")
        $driverPatterns = @("SEED International", "EZUSBPLUS", "SEED USB2.0 PLUS Emulator")

        function Write-Line([string]$Text) {
            [Console]::Out.WriteLine($Text)
        }

        function Join-Values($Value) {
            if ($null -eq $Value) { return "" }
            if ($Value -is [array]) { return (($Value | ForEach-Object { [string]$_ }) -join ", ") }
            return [string]$Value
        }

        function Get-ProblemName($Code) {
            if ($null -eq $Code -or $Code -eq "") { return "" }
            $map = @{
                0 = "CM_PROB_NONE";
                10 = "CM_PROB_FAILED_START";
                14 = "CM_PROB_NEED_RESTART";
                18 = "CM_PROB_REINSTALL";
                22 = "CM_PROB_DISABLED";
                28 = "CM_PROB_FAILED_INSTALL";
                31 = "CM_PROB_FAILED_ADD";
                39 = "CM_PROB_DRIVER_FAILED_LOAD";
                43 = "CM_PROB_FAILED_POST_START";
            }
            $intCode = -1
            if ([int]::TryParse([string]$Code, [ref]$intCode) -and $map.ContainsKey($intCode)) {
                return $map[$intCode]
            }
            return "CM_PROB_$Code"
        }

        function Get-DevicePropertyValue([string]$InstanceId, [string]$KeyName) {
            try {
                $prop = Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName $KeyName -ErrorAction Stop
                return $prop.Data
            } catch {
                return $null
            }
        }

        function Test-ContainsAny([string]$Text, [string[]]$Patterns) {
            foreach ($pattern in $Patterns) {
                if ($Text -like "*$pattern*") {
                    return $true
                }
            }
            return $false
        }

        Write-Line "[INFO] XDS510plus Windows USB/driver precheck"
        if ([string]::IsNullOrWhiteSpace($expectedPhysicalLocation)) {
            Write-Line "[WARN] 当前 burner 未配置物理位置，改按 XDS510plus 已知 USB Hardware ID 扫描。"
        } else {
            Write-Line "[INFO] 当前 burner 绑定的物理位置: $expectedPhysicalLocation"
        }
        if (-not [string]::IsNullOrWhiteSpace($expectedInstanceAnchor)) {
            Write-Line "[INFO] 当前 burner 绑定的实例锚点: $expectedInstanceAnchor"
        }

        $queries = @(
            "PNPDeviceID LIKE 'USB\\VID_0547%'"
        )
        $entities = @()
        foreach ($query in $queries) {
            try {
                $entities += @(Get-CimInstance Win32_PnPEntity -Filter $query -ErrorAction Stop)
            } catch {
                Write-Line "[WARN] Win32_PnPEntity 查询失败: $($_.Exception.Message)"
            }
        }

        $records = @()
        foreach ($entity in $entities) {
            $instanceId = [string]$entity.PNPDeviceID
            if ([string]::IsNullOrWhiteSpace($instanceId)) { continue }
            $hardwareIds = Get-DevicePropertyValue $instanceId "DEVPKEY_Device_HardwareIds"
            if ($null -eq $hardwareIds -and $entity.HardwareID) {
                $hardwareIds = @($entity.HardwareID)
            }
            $locationInfo = [string](Get-DevicePropertyValue $instanceId "DEVPKEY_Device_LocationInfo")
            $driverInf = [string](Get-DevicePropertyValue $instanceId "DEVPKEY_Device_DriverInfPath")
            $driverProvider = [string](Get-DevicePropertyValue $instanceId "DEVPKEY_Device_DriverProvider")
            $service = [string](Get-DevicePropertyValue $instanceId "DEVPKEY_Device_Service")
            if ([string]::IsNullOrWhiteSpace($service)) {
                $service = [string]$entity.Service
            }
            $problemCode = Get-DevicePropertyValue $instanceId "DEVPKEY_Device_ProblemCode"
            if ($null -eq $problemCode -or $problemCode -eq "") {
                $problemCode = $entity.ConfigManagerErrorCode
            }
            $hardwareText = Join-Values $hardwareIds
            $driverText = @(
                [string]$entity.Name,
                [string]$entity.Manufacturer,
                [string]$entity.Service,
                $service,
                $driverInf,
                $driverProvider,
                $hardwareText
            ) -join " | "
            $matchesLocation = -not [string]::IsNullOrWhiteSpace($expectedPhysicalLocation) -and $locationInfo -ieq $expectedPhysicalLocation
            $matchesInstance = -not [string]::IsNullOrWhiteSpace($expectedInstanceAnchor) -and (
                $instanceId -ieq $expectedInstanceAnchor -or
                $locationInfo -ieq $expectedInstanceAnchor
            )
            $matchesKnownHardware = Test-ContainsAny $hardwareText $knownHardwareIds
            $boundToSeed = Test-ContainsAny $driverText $driverPatterns
            $records += [pscustomobject]@{
                InstanceId = $instanceId
                Location = $locationInfo
                HardwareIds = $hardwareText
                Status = [string]$entity.Status
                ProblemCode = $problemCode
                ProblemName = Get-ProblemName $problemCode
                Service = $service
                DriverInf = $driverInf
                DriverProvider = $driverProvider
                BoundToSeed = $boundToSeed
                MatchesLocation = $matchesLocation
                MatchesInstance = $matchesInstance
                MatchesKnownHardware = $matchesKnownHardware
            }
        }

        $locationRecords = @($records | Where-Object { $_.MatchesLocation })
        $instanceRecords = @($records | Where-Object { $_.MatchesInstance })
        if (-not [string]::IsNullOrWhiteSpace($expectedPhysicalLocation)) {
            if ($locationRecords.Count -gt 0) {
                Write-Line "[INFO] 在该物理位置检测到 USB 设备: 是"
            } else {
                Write-Line "[INFO] 在该物理位置检测到 USB 设备: 否"
            }
        }
        if (-not [string]::IsNullOrWhiteSpace($expectedInstanceAnchor)) {
            if ($instanceRecords.Count -gt 0) {
                Write-Line "[INFO] 在实例锚点命中 XDS510plus 设备: 是"
            } else {
                Write-Line "[INFO] 在实例锚点命中 XDS510plus 设备: 否"
            }
        }

        $selected = @()
        if ($locationRecords.Count -gt 0) {
            $selected = @($locationRecords)
        } elseif ($instanceRecords.Count -gt 0) {
            $selected = @($instanceRecords)
        } else {
            $selected = @($records | Where-Object { $_.MatchesKnownHardware })
        }

        if ($selected.Count -eq 0) {
            Write-Line "[ERROR] 未检测到 XDS510plus USB 设备。"
            if (-not [string]::IsNullOrWhiteSpace($expectedPhysicalLocation)) {
                Write-Line "[ERROR] 物理位置: $expectedPhysicalLocation"
            }
            if (-not [string]::IsNullOrWhiteSpace($expectedInstanceAnchor)) {
                Write-Line "[ERROR] 实例锚点: $expectedInstanceAnchor"
            }
            Write-Line "[ERROR] 处理建议: 请确认 XDS510plus 已插入当前 Windows 主机，并检查 USB 线缆/Hub/端口。"
            exit 3
        }

        $hasBoundDevice = $false
        $hasDriverFailure = $false
        foreach ($item in $selected) {
            Write-Line "[INFO] XDS510plus 设备实例ID: $($item.InstanceId)"
            Write-Line "[INFO] 物理位置: $($item.Location)"
            Write-Line "[INFO] Hardware IDs: $($item.HardwareIds)"
            Write-Line "[INFO] 当前状态: $($item.ProblemName) ($($item.Status))"
            Write-Line "[INFO] Driver INF: $($item.DriverInf)"
            Write-Line "[INFO] Driver Provider: $($item.DriverProvider)"
            Write-Line "[INFO] Service: $($item.Service)"
            Write-Line "[INFO] 已绑定 SEED/EZUSBPLUS 驱动: $($item.BoundToSeed)"
            if ($item.BoundToSeed -and (
                [string]$item.ProblemName -eq "CM_PROB_NONE" -or
                [string]$item.ProblemCode -eq "0" -or
                [string]::IsNullOrWhiteSpace([string]$item.ProblemCode) -or
                [string]$item.Status -eq "OK"
            )) {
                $hasBoundDevice = $true
            }
            if (-not $item.BoundToSeed -or ([string]$item.ProblemName -ne "CM_PROB_NONE" -and -not [string]::IsNullOrWhiteSpace([string]$item.ProblemName))) {
                $hasDriverFailure = $true
            }
        }

        if ($hasBoundDevice) {
            Write-Line "[INFO] SEED XDS510Plus 已绑定 EZUSBPLUS 驱动，继续执行烧录命令。"
            exit 0
        }

        if ($hasDriverFailure) {
            $first = $selected[0]
            Write-Line "[ERROR] 检测到 XDS510plus 物理在线，但 Windows 未正确绑定驱动。"
            Write-Line "[ERROR] 物理位置: $($first.Location)"
            Write-Line "[ERROR] 实例ID: $($first.InstanceId)"
            Write-Line "[ERROR] Hardware IDs: $($first.HardwareIds)"
            Write-Line "[ERROR] 当前状态: $($first.ProblemName) ($($first.Status))"
            Write-Line "[ERROR] 失败原因: Windows 未正确绑定 SEED EZUSBPLUS 驱动。"
            Write-Line "[ERROR] 处理建议: 请安装 SEED XDS510Plus 官方驱动后重新插拔烧录器。"
            exit 2
        }

        Write-Line "[ERROR] XDS510plus USB 预检查无法确认驱动绑定状态，已停止以避免继续产生模糊连接失败。"
        exit 2
        """
    )


def _xds510plus_usb_preflight_setup() -> str:
    helper_base64 = base64.b64encode(_xds510plus_usb_preflight_source().encode("utf-8")).decode("ascii")
    return dedent(
        f"""\
        set "XDS510_PREFLIGHT_HELPER=%TEMP%\\pcids_xds510plus_usb_preflight_%TASK_ID%.ps1"
        if "%TASK_ID%"=="" set "XDS510_PREFLIGHT_HELPER=%TEMP%\\pcids_xds510plus_usb_preflight.ps1"
        powershell -NoProfile -Command "$bytes=[System.Convert]::FromBase64String('{helper_base64}'); $bom=[byte[]](0xEF,0xBB,0xBF); [System.IO.File]::WriteAllBytes($env:XDS510_PREFLIGHT_HELPER, $bom + $bytes)"
        set "XDS510_PREFLIGHT_WRITE_EXIT=!ERRORLEVEL!"
        if not "!XDS510_PREFLIGHT_WRITE_EXIT!"=="0" (
          echo [ERROR] 无法生成 XDS510plus USB 预检查脚本。
          exit /b !XDS510_PREFLIGHT_WRITE_EXIT!
        )
        powershell -NoProfile -ExecutionPolicy Bypass -File "%XDS510_PREFLIGHT_HELPER%"
        set "XDS510_PREFLIGHT_EXIT=!ERRORLEVEL!"
        del /f /q "%XDS510_PREFLIGHT_HELPER%" >nul 2>nul
        if not "!XDS510_PREFLIGHT_EXIT!"=="0" (
          exit /b !XDS510_PREFLIGHT_EXIT!
        )
        """
    )


def _xds510plus_dss_source() -> str:
    return dedent(
        r"""
        importPackage(Packages.com.ti.debug.engine.scripting);
        importPackage(Packages.com.ti.ccstudio.scripting.environment);
        importPackage(Packages.java.lang);

        function envValue(name, fallback) {
            var value = System.getenv(name);
            if (value == null || String(value).length == 0) {
                return fallback;
            }
            return String(value);
        }

        var configFile = envValue("TARGET_CONFIG_FILE", "");
        var firmwareFile = envValue("FIRMWARE_PATH", "");
        var eraseMode = envValue("XDS510_ERASE_MODE", "full");
        var completionAction = envValue("XDS510_COMPLETION_ACTION", "reset-run");
        var writeVerify = envValue("WRITE_VERIFY", "1") == "1";
        var scripting = null;
        var server = null;
        var session = null;
        var workflowError = null;

        try {
            scripting = ScriptingEnvironment.instance();
            server = scripting.getServer("DebugServer.1");
            print("SEED_XDS510_DSS_CONFIG_BEGIN");
            server.setConfig(configFile.replace(/\\/g, "/"));
            session = server.openSession("*", "*");

            print("SEED_XDS510_CONNECT_BEGIN");
            session.target.connect();
            print("SEED_XDS510_CONNECT_OK");

            session.options.setBoolean("AddCIOBreakpointAfterLoad", false);
            session.options.setBoolean("AutoRunToLabelOnRestart", false);
            if (writeVerify) {
                session.options.setString("VerifyAfterProgramLoad", "Full verification");
            }

            if (eraseMode == "full") {
                print("SEED_XDS510_FULL_ERASE_BEGIN");
                // Use the same operation API as CCS5's bundled UniFlash
                // runner. On this legacy F28335/SEED connection, halting the
                // target then using the shortcut erase API can wait forever.
                session.flash.performOperation("Erase");
                print("SEED_XDS510_FULL_ERASE_OK");
            }

            print("SEED_XDS510_PROGRAM_BEGIN");
            // Mirror CCS5 UniFlash's program path. multiloadStart/End lets
            // Flash Manager initialise and close its loader correctly.
            session.flash.multiloadStart();
            try {
                session.memory.loadProgram(firmwareFile.replace(/\\/g, "/"));
            } finally {
                session.flash.multiloadEnd();
            }
            print(writeVerify ? "SEED_XDS510_PROGRAM_VERIFY_OK" : "SEED_XDS510_PROGRAM_OK");

            if (completionAction == "reset-run") {
                print("SEED_XDS510_RESET_RUN_BEGIN");
                session.target.restart();
                print("SEED_XDS510_RESET_RUN_OK");
            }
            print("SEED_XDS510_WORKFLOW_OK");
        } catch (err) {
            print("SEED_XDS510_WORKFLOW_FAILED: " + err);
            workflowError = err;
        } finally {
            if (session != null) {
                try { session.terminate(); } catch (ignore) {}
            }
            if (server != null) {
                try { server.stop(); } catch (ignore) {}
            }
        }
        if (workflowError != null) {
            System.exit(2);
        }
        """
    )


def _xds510plus_dss_setup() -> str:
    helper_base64 = base64.b64encode(_xds510plus_dss_source().encode("utf-8")).decode("ascii")
    return dedent(
        f"""\
        set "XDS510_DSS_HELPER=%TEMP%\\pcids_seed_xds510plus_%TASK_ID%.js"
        if "%TASK_ID%"=="" set "XDS510_DSS_HELPER=%TEMP%\\pcids_seed_xds510plus.js"
        powershell -NoProfile -Command "$bytes=[System.Convert]::FromBase64String('{helper_base64}'); [System.IO.File]::WriteAllBytes($env:XDS510_DSS_HELPER, $bytes)"
        set "XDS510_DSS_WRITE_EXIT=!ERRORLEVEL!"
        if not "!XDS510_DSS_WRITE_EXIT!"=="0" (
          echo [ERROR] 无法生成 SEED XDS510Plus DSS 脚本。
          exit /b !XDS510_DSS_WRITE_EXIT!
        )
        """
    )


def _xds510plus_runner() -> str:
    return dedent(
        r"""
        if "%TARGET_CONFIG_FILE%"=="" (
          echo [ERROR] XDS510plus 烧录需要目标配置文件 .ccxml，请填写 TARGET_CONFIG_FILE。
          exit /b 2
        )
        if not exist "%TARGET_CONFIG_FILE%" (
          echo [ERROR] XDS510plus 目标配置文件不存在: %TARGET_CONFIG_FILE%
          exit /b 2
        )
        findstr /I /C:"SEED-XDS510PLUS_Connection.xml" "%TARGET_CONFIG_FILE%" >nul
        if not "!ERRORLEVEL!"=="0" (
          echo [ERROR] 目标配置不是 SEED XDS510Plus 配置: %TARGET_CONFIG_FILE%
          exit /b 2
        )
        findstr /I /C:"seedxds510plusc28x.xml" "%TARGET_CONFIG_FILE%" >nul
        if not "!ERRORLEVEL!"=="0" (
          echo [ERROR] 目标配置未使用 SEED C28x 驱动: %TARGET_CONFIG_FILE%
          exit /b 2
        )
        if "%DSS_BAT%"=="" (
          echo [ERROR] 未找到 CCS DSS 启动器 DSS_BAT。
          echo [ERROR] 请安装包含 SEED XDS510Plus 插件的 Code Composer Studio 5.5。
          exit /b 127
        )
        if not exist "%DSS_BAT%" (
          echo [ERROR] CCS DSS 启动器不存在: %DSS_BAT%
          exit /b 127
        )
        """
    ) + _xds510plus_usb_preflight_setup() + dedent(
        r"""
        for %%I in ("%DSS_BAT%") do set "XDS510_UNIFLASH=%%~dpI..\examples\uniflash\cmdLine\uniflash.bat"
        if not exist "%XDS510_UNIFLASH%" (
          echo [ERROR] CCS 5.5 Legacy UniFlash not found: %XDS510_UNIFLASH%
          exit /b 127
        )
        echo [INFO] XDS510plus runner: CCS 5.5 Legacy UniFlash (validated SEED/F28335 path)
        echo [EXEC] "%XDS510_UNIFLASH%" -ccxml "%TARGET_CONFIG_FILE%" -operation Erase -program "%FIRMWARE_PATH%" -targetOp restart
        call "%XDS510_UNIFLASH%" -ccxml "%TARGET_CONFIG_FILE%" -operation Erase -program "%FIRMWARE_PATH%" -targetOp restart
        exit /b !ERRORLEVEL!
        """
    )


def _al321_xsdb_scan_script_source() -> str:
    return dedent(
        """\
        proc pcids_value {item key} {
          if {[dict exists $item $key]} {
            return [dict get $item $key]
          }
          return ""
        }

        proc pcids_escape {value} {
          return [string map [list "\\\\" "\\\\\\\\" "|" "/" "\\r" " " "\\n" " "] $value]
        }

        set targetsHelp [help targets]
        puts "__PCIDS_HELP_TARGETS_BEGIN__"
        puts $targetsHelp
        puts "__PCIDS_HELP_TARGETS_END__"
        if {[string first "-target-properties" $targetsHelp] < 0} {
          puts stderr "Installed xsdb does not support targets -target-properties."
          exit 2
        }

        set connectHelp [help connect]
        puts "__PCIDS_HELP_CONNECT_BEGIN__"
        puts $connectHelp
        puts "__PCIDS_HELP_CONNECT_END__"

        connect -url TCP:127.0.0.1:3121
        set targetList [targets -target-properties]
        foreach item $targetList {
          puts "__PCIDS_TARGET__serial=[pcids_escape [pcids_value $item jtag_cable_serial]]|cable_name=[pcids_escape [pcids_value $item jtag_cable_name]]|target_name=[pcids_escape [pcids_value $item name]]|device_name=[pcids_escape [pcids_value $item jtag_device_name]]|target_ctx=[pcids_escape [pcids_value $item target_ctx]]|target_id=[pcids_escape [pcids_value $item target_id]]"
        }
        disconnect
        exit 0
        """
    )


def _al321_xsdb_scan_script_setup() -> str:
    helper_base64 = base64.b64encode(_al321_xsdb_scan_script_source().encode("utf-8")).decode("ascii")
    return dedent(
        f"""\
        set "AL321_XSDB_SCRIPT=%TEMP%\\pcids_al321_xsdb_%TASK_ID%.tcl"
        if "%TASK_ID%"=="" set "AL321_XSDB_SCRIPT=%TEMP%\\pcids_al321_xsdb.tcl"
        powershell -NoProfile -Command "$bytes=[System.Convert]::FromBase64String('{helper_base64}'); [System.IO.File]::WriteAllBytes($env:AL321_XSDB_SCRIPT, $bytes)"
        set "AL321_XSDB_SCRIPT_WRITE_EXIT=!ERRORLEVEL!"
        if not "!AL321_XSDB_SCRIPT_WRITE_EXIT!"=="0" (
          echo [ERROR] 无法生成 AL321 xsdb 只读枚举脚本。
          exit /b !AL321_XSDB_SCRIPT_WRITE_EXIT!
        )
        """
    )


def _strict_flash_parameter_guards() -> str:
    return dedent(
        r"""\
        if "%TARGET_CHIP%"=="" (
          echo [ERROR] 未配置 TARGET_CHIP，禁止猜测目标芯片。
          exit /b 2
        )
        if "%BURNER_SN%"=="" (
          echo [ERROR] 未配置 BURNER_SN，禁止自动选择烧录器。
          exit /b 2
        )
        set "PCIDS_FIRMWARE_EXT="
        for %%I in ("%FIRMWARE_PATH%") do set "PCIDS_FIRMWARE_EXT=%%~xI"
        if /I "!PCIDS_FIRMWARE_EXT!"==".bin" (
          if "%START_ADDRESS%"=="" (
            echo [ERROR] .bin 固件必须提供 START_ADDRESS。
            exit /b 2
          )
        )
        """
    )

def _strict_pyocd_runner(probe_label: str) -> str:
    return dedent(
        f"""\
        if "%PYOCD_EXE%"=="" for /f "delims=" %%I in ('where pyocd.exe 2^>nul') do if "%PYOCD_EXE%"=="" set "PYOCD_EXE=%%I"
        if "%PYOCD_EXE%"=="" for /f "delims=" %%I in ('where pyocd 2^>nul') do if "%PYOCD_EXE%"=="" set "PYOCD_EXE=%%I"
        if "%PYOCD_EXE%"=="" (
          echo [ERROR] pyOCD executable not found. Configure PYOCD_EXE or bundle tools\\burners\\SWD_Downloader\\pyocd-runtime.
          exit /b 127
        )
        {_strict_flash_parameter_guards().rstrip()}
        {_pyocd_preflight_script_setup().rstrip()}
        set "PYOCD_PREFLIGHT_LOG=%TEMP%\\pcids_pyocd_preflight_%TASK_ID%.json"
        if "%TASK_ID%"=="" set "PYOCD_PREFLIGHT_LOG=%TEMP%\\pcids_pyocd_preflight.json"
        echo [EXEC] "%PYOCD_PYTHON%" "%PYOCD_HELPER%" preflight --target-chip "%TARGET_CHIP%" --probe-unique-id "%BURNER_SN%"
        call "%PYOCD_PYTHON%" "%PYOCD_HELPER%" preflight --target-chip "%TARGET_CHIP%" --probe-unique-id "%BURNER_SN%" >"!PYOCD_PREFLIGHT_LOG!" 2>&1
        set "PYOCD_PREFLIGHT_EXIT=!ERRORLEVEL!"
        if not "!PYOCD_PREFLIGHT_EXIT!"=="0" (
          type "!PYOCD_PREFLIGHT_LOG!"
          del /f /q "!PYOCD_PREFLIGHT_LOG!" >nul 2>nul
          del /f /q "%PYOCD_HELPER%" >nul 2>nul
          echo [ERROR] pyOCD 安全预检失败，禁止进入擦除或写入。
          exit /b !PYOCD_PREFLIGHT_EXIT!
        )
        set "PYOCD_TARGET="
        set "PYOCD_TARGET_SUPPORTED="
        set "PYOCD_PROBE_MATCH_COUNT="
        set "PYOCD_RUNTIME_VERSION="
        for /f "usebackq tokens=1,* delims==" %%A in (`powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $data=Get-Content -Raw -LiteralPath $env:PYOCD_PREFLIGHT_LOG | ConvertFrom-Json -ErrorAction Stop; Write-Output ('PYOCD_TARGET=' + [string]$data.resolved_target); Write-Output ('PYOCD_TARGET_SUPPORTED=' + $(if ($data.target_supported) {{ '1' }} else {{ '0' }})); Write-Output ('PYOCD_PROBE_MATCH_COUNT=' + [string]$data.exact_match_count); Write-Output ('PYOCD_RUNTIME_VERSION=' + [string]$data.pyocd_version)"`) do set "%%A=%%B"
        set "PYOCD_PREFLIGHT_PARSE_EXIT=!ERRORLEVEL!"
        del /f /q "!PYOCD_PREFLIGHT_LOG!" >nul 2>nul
        del /f /q "%PYOCD_HELPER%" >nul 2>nul
        if not "!PYOCD_PREFLIGHT_PARSE_EXIT!"=="0" (
          echo [ERROR] pyOCD 安全预检输出解析失败，禁止进入擦除或写入。
          exit /b !PYOCD_PREFLIGHT_PARSE_EXIT!
        )
        echo [INFO] pyOCD runtime version: !PYOCD_RUNTIME_VERSION!
        if not "!PYOCD_TARGET_SUPPORTED!"=="1" (
          echo [ERROR] 不支持的 pyOCD target: %TARGET_CHIP%
          exit /b 2
        )
        if "!PYOCD_PROBE_MATCH_COUNT!"=="0" (
          echo [ERROR] 未发现指定 probe: BURNER_SN=%BURNER_SN%
          exit /b 2
        )
        if not "!PYOCD_PROBE_MATCH_COUNT!"=="1" (
          echo [ERROR] BURNER_SN=%BURNER_SN% 在 pyOCD probe JSON 中的 unique_id 精确匹配数量为 !PYOCD_PROBE_MATCH_COUNT!，已拒绝执行。
          exit /b 2
        )
        echo [INFO] Resolved pyOCD target from TARGET_CHIP: %TARGET_CHIP% ^> !PYOCD_TARGET!
        echo [INFO] 已完成 pyOCD 只读预检：target Python API 校验和 probe unique_id 精确匹配均通过。
        set "PYOCD_ERASE=sector"
        if "%ERASE_MODE%"=="全片擦除" set "PYOCD_ERASE=chip"
        if /I "%ERASE_MODE%"=="chip" set "PYOCD_ERASE=chip"
        if /I "%ERASE_MODE%"=="all" set "PYOCD_ERASE=chip"
        if /I "%ERASE_MODE%"=="sector" set "PYOCD_ERASE=sector"
        set "PYOCD_FREQ=%WRITE_SPEED_KHZ%k"
        if "%WRITE_SPEED_KHZ%"=="" set "PYOCD_FREQ=1000k"
        set "PYOCD_RETRY_FREQ=50k"
        set "PYOCD_OPTIONS="
        if /I "%INTERFACE_TYPE%"=="JTAG" set "PYOCD_OPTIONS=-O dap_protocol=jtag"
        set "PYOCD_STABILITY_OPTIONS=-O reset_type=hardware -O cmsis_dap.limit_packets=true -O jlink.non_interactive=false"
        set "PYOCD_FLASH_RESET_OPTION="
        if "%COMPLETION_ACTION%"=="不处理" set "PYOCD_FLASH_RESET_OPTION=--no-reset"
        if /I "%COMPLETION_ACTION%"=="none" set "PYOCD_FLASH_RESET_OPTION=--no-reset"
        if /I "%COMPLETION_ACTION%"=="off" set "PYOCD_FLASH_RESET_OPTION=--no-reset"
        set "PYOCD_ADDRESS_ARG="
        if /I "!PCIDS_FIRMWARE_EXT!"==".bin" set "PYOCD_ADDRESS_ARG=-a %START_ADDRESS%"
        call "%PYOCD_EXE%" flash -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_FREQ% -M halt -e %PYOCD_ERASE% %PYOCD_OPTIONS% %PYOCD_STABILITY_OPTIONS% %PYOCD_FLASH_RESET_OPTION% %PYOCD_ADDRESS_ARG% "%FIRMWARE_PATH%"
        set "PYOCD_FLASH_EXIT=!ERRORLEVEL!"
        if not "!PYOCD_FLASH_EXIT!"=="0" (
          echo [WARN] pyOCD flash failed with !PYOCD_FLASH_EXIT!, retrying same target and BURNER_SN under-reset at %PYOCD_RETRY_FREQ%.
          call "%PYOCD_EXE%" flash -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M under-reset -e %PYOCD_ERASE% %PYOCD_OPTIONS% %PYOCD_STABILITY_OPTIONS% %PYOCD_FLASH_RESET_OPTION% %PYOCD_ADDRESS_ARG% "%FIRMWARE_PATH%"
          set "PYOCD_FLASH_EXIT=!ERRORLEVEL!"
        )
        if not "!PYOCD_FLASH_EXIT!"=="0" exit /b !PYOCD_FLASH_EXIT!
        set "PYOCD_COMPLETE_EXIT=0"
        if "%COMPLETION_ACTION%"=="复位运行" (
          echo [INFO] Completion action: reset and run.
          call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
          set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          if not "!PYOCD_COMPLETE_EXIT!"=="0" (
            echo [WARN] pyOCD reset-run failed, retrying same target and BURNER_SN with under-reset.
            call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
            set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          )
        )
        if /I "%COMPLETION_ACTION%"=="reset-run" (
          echo [INFO] Completion action: reset and run.
          call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
          set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          if not "!PYOCD_COMPLETE_EXIT!"=="0" (
            echo [WARN] pyOCD reset-run failed, retrying same target and BURNER_SN with under-reset.
            call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
            set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          )
        )
        if /I "%COMPLETION_ACTION%"=="run" (
          echo [INFO] Completion action: reset and run.
          call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
          set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          if not "!PYOCD_COMPLETE_EXIT!"=="0" (
            echo [WARN] pyOCD reset-run failed, retrying same target and BURNER_SN with under-reset.
            call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
            set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          )
        )
        if "%COMPLETION_ACTION%"=="仅复位" (
          echo [INFO] Completion action: reset and halt.
          call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset halt" -c "exit"
          set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          if not "!PYOCD_COMPLETE_EXIT!"=="0" (
            echo [WARN] pyOCD reset-halt failed, retrying same target and BURNER_SN with under-reset.
            call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset halt" -c "exit"
            set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          )
        )
        if /I "%COMPLETION_ACTION%"=="reset-halt" (
          echo [INFO] Completion action: reset and halt.
          call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset halt" -c "exit"
          set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          if not "!PYOCD_COMPLETE_EXIT!"=="0" (
            echo [WARN] pyOCD reset-halt failed, retrying same target and BURNER_SN with under-reset.
            call "%PYOCD_EXE%" commander -u "%BURNER_SN%" -t "%PYOCD_TARGET%" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset halt" -c "exit"
            set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
          )
        )
        exit /b !PYOCD_COMPLETE_EXIT!
        """
    )


def _legacy_pyocd_runner(probe_label: str, require_probe: bool = True) -> str:
    probe_guard = ""
    probe_arg = ""
    if require_probe:
        probe_guard = dedent(
            f"""\
            if "%BURNER_SN%"=="" (
              echo [ERROR] {probe_label} pyOCD flash requires BURNER_SN. Please bind the exact probe serial number to avoid selecting the wrong device.
              exit /b 2
            )
            """
        )
        probe_arg = '-u "%BURNER_SN%" '
    return dedent(
        f"""\
        if "%PYOCD_EXE%"=="" for /f "delims=" %%I in ('where pyocd.exe 2^>nul') do if "%PYOCD_EXE%"=="" set "PYOCD_EXE=%%I"
        if "%PYOCD_EXE%"=="" for /f "delims=" %%I in ('where pyocd 2^>nul') do if "%PYOCD_EXE%"=="" set "PYOCD_EXE=%%I"
        if "%PYOCD_EXE%"=="" (
          echo [ERROR] pyOCD executable not found. Configure PYOCD_EXE or bundle tools\\burners\\SWD_Downloader\\pyocd-runtime.
          exit /b 127
        )
        {probe_guard.rstrip()}
        set "PYOCD_TARGET=%TARGET_CHIP%"
        if "%PYOCD_TARGET%"=="" set "PYOCD_TARGET=stm32f103c8"
        set "PYOCD_TARGET=%PYOCD_TARGET: =%"
        set "PYOCD_TARGET=%PYOCD_TARGET:-=%"
        set "PYOCD_TARGET=%PYOCD_TARGET:_=%"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32[A-Z][0-9][0-9][0-9][A-Z][0-9A-Z][A-Z][0-9]$" >nul
        if not errorlevel 1 set "PYOCD_TARGET=%PYOCD_TARGET:~0,-2%"
        set "PYOCD_TARGETS=%PYOCD_TARGET%"
        echo [INFO] Resolved pyOCD target from TARGET_CHIP: %TARGET_CHIP% ^> %PYOCD_TARGET%
        if not "%PYOCD_TARGET_CANDIDATES%"=="" set "PYOCD_TARGETS=%PYOCD_TARGETS% %PYOCD_TARGET_CANDIDATES%"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32F0" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32f051 stm32f072rb"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32F103" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32f103c8 stm32f103rc"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32F4" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32f407vg stm32f407vgtx stm32f411re stm32f429zi"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32F7" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32f746zg stm32f767zi"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32G0" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32g071rb stm32g0b1re"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32G4" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32g431rb stm32g474re"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32H7" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32h743zi stm32h750xb"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32L4" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32l432kc stm32l476rg"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32WB" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32wb55rg"
        echo %PYOCD_TARGET%| findstr /R /I "^STM32WL" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% stm32wl55jc"
        echo %PYOCD_TARGET%| findstr /R /I "^LPC55S69" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% lpc55s69"
        echo %PYOCD_TARGET%| findstr /R /I "^LPC5526" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% lpc5526"
        echo %PYOCD_TARGET%| findstr /R /I "^LPC55S16" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% lpc55s16"
        echo %PYOCD_TARGET%| findstr /R /I "^NRF52832" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% nrf52832 nrf52"
        echo %PYOCD_TARGET%| findstr /R /I "^NRF52833" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% nrf52833 nrf52"
        echo %PYOCD_TARGET%| findstr /R /I "^NRF52840" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% nrf52840 nrf52"
        echo %PYOCD_TARGET%| findstr /R /I "^RP2040" >nul
        if not errorlevel 1 set "PYOCD_TARGETS=%PYOCD_TARGETS% rp2040 rp2040_core0"
        set "PYOCD_ERASE=sector"
        if "%ERASE_MODE%"=="全片擦除" set "PYOCD_ERASE=chip"
        if /I "%ERASE_MODE%"=="chip" set "PYOCD_ERASE=chip"
        if /I "%ERASE_MODE%"=="all" set "PYOCD_ERASE=chip"
        if /I "%ERASE_MODE%"=="sector" set "PYOCD_ERASE=sector"
        set "PYOCD_FREQ=%WRITE_SPEED_KHZ%k"
        if "%WRITE_SPEED_KHZ%"=="" set "PYOCD_FREQ=1000k"
        set "PYOCD_RETRY_FREQ=50k"
        set "PYOCD_OPTIONS="
        if /I "%INTERFACE_TYPE%"=="JTAG" set "PYOCD_OPTIONS=-O dap_protocol=jtag"
        set "PYOCD_STABILITY_OPTIONS=-O reset_type=hardware -O cmsis_dap.limit_packets=true -O jlink.non_interactive=false"
        set "PYOCD_FLASH_RESET_OPTION="
        if "%COMPLETION_ACTION%"=="不处理" set "PYOCD_FLASH_RESET_OPTION=--no-reset"
        if /I "%COMPLETION_ACTION%"=="none" set "PYOCD_FLASH_RESET_OPTION=--no-reset"
        if /I "%COMPLETION_ACTION%"=="off" set "PYOCD_FLASH_RESET_OPTION=--no-reset"
        set "PYOCD_EXIT=1"
        for %%T in (%PYOCD_TARGETS%) do (
          echo [INFO] Trying pyOCD target: %%T
          if "%START_ADDRESS%"=="" (
            "%PYOCD_EXE%" flash {probe_arg}-t "%%T" -f %PYOCD_FREQ% -M halt -e %PYOCD_ERASE% %PYOCD_OPTIONS% %PYOCD_STABILITY_OPTIONS% %PYOCD_FLASH_RESET_OPTION% "%FIRMWARE_PATH%"
            set "PYOCD_EXIT=!ERRORLEVEL!"
            if not "!PYOCD_EXIT!"=="0" (
              echo [WARN] pyOCD flash failed with !PYOCD_EXIT!, retrying %%T with under-reset at %PYOCD_RETRY_FREQ%.
              "%PYOCD_EXE%" flash {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset -e %PYOCD_ERASE% %PYOCD_OPTIONS% %PYOCD_STABILITY_OPTIONS% %PYOCD_FLASH_RESET_OPTION% "%FIRMWARE_PATH%"
              set "PYOCD_EXIT=!ERRORLEVEL!"
            )
          ) else (
            "%PYOCD_EXE%" flash {probe_arg}-t "%%T" -f %PYOCD_FREQ% -M halt -e %PYOCD_ERASE% %PYOCD_OPTIONS% %PYOCD_STABILITY_OPTIONS% %PYOCD_FLASH_RESET_OPTION% -a "%START_ADDRESS%" "%FIRMWARE_PATH%"
            set "PYOCD_EXIT=!ERRORLEVEL!"
            if not "!PYOCD_EXIT!"=="0" (
              echo [WARN] pyOCD flash failed with !PYOCD_EXIT!, retrying %%T with under-reset at %PYOCD_RETRY_FREQ%.
              "%PYOCD_EXE%" flash {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset -e %PYOCD_ERASE% %PYOCD_OPTIONS% %PYOCD_STABILITY_OPTIONS% %PYOCD_FLASH_RESET_OPTION% -a "%START_ADDRESS%" "%FIRMWARE_PATH%"
              set "PYOCD_EXIT=!ERRORLEVEL!"
            )
          )
          if "!PYOCD_EXIT!"=="0" (
            set "PYOCD_COMPLETE_EXIT=0"
            if "%COMPLETION_ACTION%"=="复位运行" (
              echo [INFO] Completion action: reset and run.
              "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
              if not !ERRORLEVEL! == 0 (
                echo [WARN] pyOCD reset-run failed, retrying with under-reset.
                "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
              )
              set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
            )
            if /I "%COMPLETION_ACTION%"=="reset-run" (
              echo [INFO] Completion action: reset and run.
              "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
              if not !ERRORLEVEL! == 0 (
                echo [WARN] pyOCD reset-run failed, retrying with under-reset.
                "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
              )
              set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
            )
            if /I "%COMPLETION_ACTION%"=="run" (
              echo [INFO] Completion action: reset and run.
              "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
              if not !ERRORLEVEL! == 0 (
                echo [WARN] pyOCD reset-run failed, retrying with under-reset.
                "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset" -c "sleep 100" -c "go" -c "exit"
              )
              set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
            )
            if "%COMPLETION_ACTION%"=="仅复位" (
              echo [INFO] Completion action: reset and halt.
              "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset halt" -c "exit"
              if not !ERRORLEVEL! == 0 (
                echo [WARN] pyOCD reset-halt failed, retrying with under-reset.
                "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset halt" -c "exit"
              )
              set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
            )
            if /I "%COMPLETION_ACTION%"=="reset-halt" (
              echo [INFO] Completion action: reset and halt.
              "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M attach %PYOCD_OPTIONS% -c "reset halt" -c "exit"
              if not !ERRORLEVEL! == 0 (
                echo [WARN] pyOCD reset-halt failed, retrying with under-reset.
                "%PYOCD_EXE%" commander {probe_arg}-t "%%T" -f %PYOCD_RETRY_FREQ% -M under-reset %PYOCD_OPTIONS% -c "reset halt" -c "exit"
              )
              set "PYOCD_COMPLETE_EXIT=!ERRORLEVEL!"
            )
            exit /b !PYOCD_COMPLETE_EXIT!
          )
        )
        exit /b !PYOCD_EXIT!
        """
    )


def _pyocd_runner(probe_label: str, require_probe: bool = True, strict: bool = False) -> str:
    if strict:
        return _strict_pyocd_runner(probe_label)
    return _legacy_pyocd_runner(probe_label, require_probe=require_probe)


def _swd_template_runner() -> str:
    return _compose_batch_script(
        dedent(
            """\
            if not "%SWD_CMD_TEMPLATE%"=="" (
              echo %SWD_CMD_TEMPLATE%| findstr /C:"{firmware}" >nul
              if errorlevel 1 (
                echo [ERROR] SWD_CMD_TEMPLATE 缺少 {firmware} 占位符，已拒绝执行。
                exit /b 2
              )
              echo %SWD_CMD_TEMPLATE%| findstr /C:"{target}" >nul
              if errorlevel 1 (
                echo [ERROR] SWD_CMD_TEMPLATE 缺少 {target} 占位符，已拒绝执行。
                exit /b 2
              )
              echo %SWD_CMD_TEMPLATE%| findstr /C:"{probe}" >nul
              if errorlevel 1 (
                echo [ERROR] SWD_CMD_TEMPLATE 缺少 {probe} 占位符，已拒绝执行。
                exit /b 2
              )
            """
        ),
        _strict_flash_parameter_guards(),
        dedent(
            """\
              set "PCIDS_CMD=%SWD_CMD_TEMPLATE%"
              set "PCIDS_CMD=!PCIDS_CMD:{firmware}=%FIRMWARE_PATH%!"
              set "PCIDS_CMD=!PCIDS_CMD:{target}=%TARGET_CHIP%!"
              set "PCIDS_CMD=!PCIDS_CMD:{interface}=%INTERFACE_TYPE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{speed}=%WRITE_SPEED_KHZ%!"
              set "PCIDS_CMD=!PCIDS_CMD:{probe}=%BURNER_SN%!"
              set "PCIDS_CMD=!PCIDS_CMD:{address}=%START_ADDRESS%!"
              set "PCIDS_CMD=!PCIDS_CMD:{erase}=%ERASE_MODE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{action}=%COMPLETION_ACTION%!"
            """
        ),
        _stream_command_helper_setup(),
        _stream_command_runner(),
        dedent(
            """\
              set "PCIDS_EXIT=!PCIDS_STREAM_EXIT!"
              exit /b !PCIDS_EXIT!
            )
            """
        ),
    )


def _al321_openfpgaloader_runner() -> str:
    runner = _compose_batch_script(
        dedent(
            r'''
            set "AL321_OPERATION=%EXECUTION_OPERATION%"
            set "AL321_OPERATION_MODE=%EXECUTION_OPERATION_MODE%"
            if /I not "%AL321_OPERATION_MODE%"=="flash" set "AL321_OPERATION_MODE=sram"
            if not "%AL321_CMD_TEMPLATE%"=="" (
              set "PCIDS_CMD=%AL321_CMD_TEMPLATE%"
              set "PCIDS_CMD=!PCIDS_CMD:{firmware}=%FIRMWARE_PATH%!"
              set "PCIDS_CMD=!PCIDS_CMD:{target}=%TARGET_CHIP%!"
              set "PCIDS_CMD=!PCIDS_CMD:{probe}=%BURNER_SN%!"
              set "PCIDS_CMD=!PCIDS_CMD:{interface}=%INTERFACE_TYPE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{speed}=%WRITE_SPEED_KHZ%!"
              set "PCIDS_CMD=!PCIDS_CMD:{address}=%START_ADDRESS%!"
              set "PCIDS_CMD=!PCIDS_CMD:{fsbl}=%TARGET_CONFIG_FILE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{flash_type}=%QSPI_FLASH_MODEL%!"
              set "PCIDS_CMD=!PCIDS_CMD:{erase}=%ERASE_MODE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{action}=%COMPLETION_ACTION%!"
            '''
        ).lstrip(),
        _stream_command_helper_setup(),
        _stream_command_runner(),
        dedent(
            r'''
              exit /b !PCIDS_STREAM_EXIT!
            )
            '''
        ).lstrip(),
        dedent(
        r'''
        if /I "%AL321_OPERATION_MODE%"=="flash" (
          if "%PROGRAM_FLASH_EXE%"=="" for /f "delims=" %%I in ('where program_flash.bat 2^>nul') do if "%PROGRAM_FLASH_EXE%"=="" set "PROGRAM_FLASH_EXE=%%I"
          if "%PROGRAM_FLASH_EXE%"=="" for /f "delims=" %%I in ('where program_flash.exe 2^>nul') do if "%PROGRAM_FLASH_EXE%"=="" set "PROGRAM_FLASH_EXE=%%I"
          if "%XSDB_EXE%"=="" for /f "delims=" %%I in ('where xsdb.bat 2^>nul') do if "%XSDB_EXE%"=="" set "XSDB_EXE=%%I"
          if "%XSDB_EXE%"=="" for /f "delims=" %%I in ('where xsdb.exe 2^>nul') do if "%XSDB_EXE%"=="" set "XSDB_EXE=%%I"
          if "%HW_SERVER_EXE%"=="" for /f "delims=" %%I in ('where hw_server.bat 2^>nul') do if "%HW_SERVER_EXE%"=="" set "HW_SERVER_EXE=%%I"
          if "%HW_SERVER_EXE%"=="" for /f "delims=" %%I in ('where hw_server.exe 2^>nul') do if "%HW_SERVER_EXE%"=="" set "HW_SERVER_EXE=%%I"
          if "%PROGRAM_FLASH_EXE%"=="" (
            echo [ERROR] ZynqMP Flash固化需要 AMD program_flash。请将 Vitis program_flash 和配套驱动安装到 tools\burners\AL321\Vitis，或安装在 D:\vitis\Vitis / D:\vitis\Vivado，或设置 PROGRAM_FLASH_EXE。
            exit /b 127
          )
          if not exist "%PROGRAM_FLASH_EXE%" (
            echo [ERROR] PROGRAM_FLASH_EXE 指向的文件不存在: %PROGRAM_FLASH_EXE%
            exit /b 127
          )
          if "%XSDB_EXE%"=="" (
            echo [ERROR] ZynqMP Flash固化需要 AMD xsdb。请将 Vitis xsdb 安装到 tools\burners\AL321\Vitis，或安装在 D:\vitis\Vitis / D:\vitis\Vivado，或设置 XSDB_EXE。
            exit /b 127
          )
          if not exist "%XSDB_EXE%" (
            echo [ERROR] XSDB_EXE 指向的文件不存在: %XSDB_EXE%
            exit /b 127
          )
          if "%HW_SERVER_EXE%"=="" (
            echo [ERROR] ZynqMP Flash固化需要 AMD hw_server。请将 Vitis hw_server 安装到 tools\burners\AL321\Vitis，或安装在 D:\vitis\Vitis / D:\vitis\Vivado，或设置 HW_SERVER_EXE。
            exit /b 127
          )
          if not exist "%HW_SERVER_EXE%" (
            echo [ERROR] HW_SERVER_EXE 指向的文件不存在: %HW_SERVER_EXE%
            exit /b 127
          )
          echo [INFO] AL321 Flash固化预检通过:
          echo [INFO]   PROGRAM_FLASH_EXE=%PROGRAM_FLASH_EXE%
          echo [INFO]   XSDB_EXE=%XSDB_EXE%
          echo [INFO]   HW_SERVER_EXE=%HW_SERVER_EXE%
          echo %PROGRAM_FLASH_EXE% | findstr /I /C:"D:\vitis\" >nul && echo [INFO] 已从 D:\vitis 发现 program_flash: %PROGRAM_FLASH_EXE%
          echo %XSDB_EXE% | findstr /I /C:"D:\vitis\" >nul && echo [INFO] 已从 D:\vitis 发现 xsdb: %XSDB_EXE%
          echo %HW_SERVER_EXE% | findstr /I /C:"D:\vitis\" >nul && echo [INFO] 已从 D:\vitis 发现 hw_server: %HW_SERVER_EXE%
          for %%I in ("%FIRMWARE_PATH%") do set "AL321_EXT=%%~xI"
          if /I not "!AL321_EXT!"==".bin" (
            echo [ERROR] ZynqMP Flash固化需要 BOOT.bin 文件。
            exit /b 2
          )
          if "%BURNER_SN%"=="" (
            echo [ERROR] ZynqMP Flash固化必须配置 BURNER_SN，禁止默认选择第一个 cable。
            exit /b 2
          )
          if "%TARGET_CONFIG_FILE%"=="" (
            echo [ERROR] ZynqMP Flash固化需要选择与目标板硬件设计匹配的 FSBL ELF 文件。
            exit /b 2
          )
          if not exist "%TARGET_CONFIG_FILE%" (
            echo [ERROR] FSBL 文件不存在: %TARGET_CONFIG_FILE%
            exit /b 2
          )
          for %%I in ("%TARGET_CONFIG_FILE%") do set "AL321_FSBL_EXT=%%~xI"
          if /I not "!AL321_FSBL_EXT!"==".elf" (
            echo [ERROR] FSBL 文件必须是 .elf 格式。
            exit /b 2
          )
          if "!QSPI_FLASH_MODEL!"=="" set "QSPI_FLASH_MODEL=qspi-x8-dual_parallel"
          set "AL321_PROGRAM_FLASH_TYPE=!QSPI_FLASH_MODEL!"
          if /I "!AL321_PROGRAM_FLASH_TYPE!"=="qspi-x4-dual-parallel" set "AL321_PROGRAM_FLASH_TYPE=qspi-x8-dual_parallel"
          if /I "!AL321_PROGRAM_FLASH_TYPE!"=="qspi-x4-dual-stacked" set "AL321_PROGRAM_FLASH_TYPE=qspi-x4-dual_stacked"
          echo [INFO] 当前请求的 QSPI flash_type: !QSPI_FLASH_MODEL!
          if /I not "!AL321_PROGRAM_FLASH_TYPE!"=="!QSPI_FLASH_MODEL!" (
            echo [INFO] 已兼容映射 QSPI flash_type: !AL321_PROGRAM_FLASH_TYPE!
          )
          if "%START_ADDRESS%"=="" set "START_ADDRESS=0x0"
          set "AL321_DRIVER_STATE_FILE=%TEMP%\pcids_al321_driver_state.json"
          for %%I in ("%AL321_DRIVER_SWITCH_SCRIPT%") do set "AL321_DRIVER_SWITCH_LOG_DIR=%%~dpI..\driver-switch-logs"
          for %%I in ("%AL321_DRIVER_SWITCH_SCRIPT%") do set "AL321_PROGRAM_FLASH_STREAM_SCRIPT=%%~dpI..\run-program-flash-stream.ps1"
          set "AL321_DRIVER_SWITCH_STDOUT_LOG=%TEMP%\pcids_al321_driver_switch_%TASK_ID%.log"
          if "%TASK_ID%"=="" set "AL321_DRIVER_SWITCH_STDOUT_LOG=%TEMP%\pcids_al321_driver_switch.log"
          set "AL321_PROGRAM_FLASH_HELP_LOG=%TEMP%\pcids_al321_program_flash_help_%TASK_ID%.log"
          if "%TASK_ID%"=="" set "AL321_PROGRAM_FLASH_HELP_LOG=%TEMP%\pcids_al321_program_flash_help.log"
          if not exist "!AL321_PROGRAM_FLASH_STREAM_SCRIPT!" (
            echo [ERROR] program_flash 实时日志包装脚本不存在: !AL321_PROGRAM_FLASH_STREAM_SCRIPT!
            exit /b 127
          )
          echo [EXEC] "%PROGRAM_FLASH_EXE%" -help
          call "%PROGRAM_FLASH_EXE%" -help >"!AL321_PROGRAM_FLASH_HELP_LOG!" 2>&1
          set "AL321_PROGRAM_FLASH_HELP_EXIT=!ERRORLEVEL!"
          if not "!AL321_PROGRAM_FLASH_HELP_EXIT!"=="0" (
            type "!AL321_PROGRAM_FLASH_HELP_LOG!"
            echo [ERROR] 当前安装的 program_flash 不支持 -help 或帮助输出不可用，已拒绝执行。
            exit /b !AL321_PROGRAM_FLASH_HELP_EXIT!
          )
          findstr /I /C:"-cable" /C:"-fsbl" /C:"-flash_type" /C:"-offset" "!AL321_PROGRAM_FLASH_HELP_LOG!" >nul
          if errorlevel 1 (
            type "!AL321_PROGRAM_FLASH_HELP_LOG!"
            echo [ERROR] 当前安装的 program_flash 帮助中未找到所需参数 ^(-cable/-fsbl/-flash_type/-offset^)，已拒绝执行。
            exit /b 2
          )
          __PCIDS_AL321_XSDB_SETUP__
          set "AL321_XSDB_LOG=%TEMP%\pcids_al321_xsdb_%TASK_ID%.log"
          if "%TASK_ID%"=="" set "AL321_XSDB_LOG=%TEMP%\pcids_al321_xsdb.log"
          set "AL321_DRIVER_SWITCHED=0"
          if exist "!AL321_DRIVER_STATE_FILE!" (
            echo [WARN] 检测到上次遗留的 AL321 驱动恢复状态文件，正在尝试恢复原驱动。
            powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode recover-pending -StateFile "!AL321_DRIVER_STATE_FILE!"
            set "AL321_PREVIOUS_RECOVERY_EXIT=!ERRORLEVEL!"
            if not "!AL321_PREVIOUS_RECOVERY_EXIT!"=="0" (
              call :PCIDS_PRINT_AL321_DRIVER_SWITCH_LOG
              echo [ERROR] AL321 遗留驱动恢复失败，已拒绝继续执行 Flash固化。
              exit /b 2
            )
          )
          if not "%AL321_AUTO_DRIVER_SWITCH%"=="0" (
            if "%AL321_DRIVER_SWITCH_SCRIPT%"=="" (
              echo [ERROR] 未找到 AL321 自动驱动切换脚本 switch-al321-driver.ps1。
              exit /b 127
            )
            echo [INFO] 正在为 ZynqMP Flash固化切换 AMD/Digilent cable 驱动，Windows 可能显示 UAC 确认。
            powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode amd -Serial "%BURNER_SN%" -StateFile "!AL321_DRIVER_STATE_FILE!" >"!AL321_DRIVER_SWITCH_STDOUT_LOG!" 2>&1
            if not "!ERRORLEVEL!"=="0" (
              type "!AL321_DRIVER_SWITCH_STDOUT_LOG!"
              call :PCIDS_PRINT_AL321_DRIVER_SWITCH_LOG
              echo [ERROR] 当前 AL321 是 FTDI/WinUSB 型设备。Vitis 自带 xpcwinusb.inf 是 03FD Xilinx Cable 驱动，不能用于该设备。如 hw_server 能识别当前设备，可设置 AL321_AUTO_DRIVER_SWITCH=0 跳过切换。
              echo [ERROR] AL321 驱动切换失败，为避免操作错误设备，已取消 Flash固化。
              exit /b 2
            )
            set "AL321_DRIVER_SWITCHED=1"
          )
          set "AL321_STARTED_HW_SERVER=0"
          set "AL321_HW_SERVER_PID="
          for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$client=New-Object System.Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1',3121); '1' } catch { '0' } finally { $client.Dispose() }"`) do set "AL321_HW_SERVER_READY=%%I"
          if "!AL321_HW_SERVER_READY!"=="1" (
            echo [INFO] 已确认 hw_server 正在监听 TCP:127.0.0.1:3121
          ) else (
            echo [INFO] hw_server 未运行，正在启动并等待就绪。
            for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$proc=Start-Process -FilePath $env:HW_SERVER_EXE -PassThru -WindowStyle Hidden; [Console]::Out.WriteLine([string]$proc.Id)"`) do set "AL321_HW_SERVER_PID=%%I"
            if "!AL321_HW_SERVER_PID!"=="" (
              echo [ERROR] 启动 hw_server 失败，未获得进程 ID。
              if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
              exit /b 2
            )
            set "AL321_STARTED_HW_SERVER=1"
            set "AL321_HW_SERVER_READY=0"
            for /L %%S in (1,1,20) do if "!AL321_HW_SERVER_READY!"=="0" (
              timeout /t 1 >nul
              for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "$client=New-Object System.Net.Sockets.TcpClient; try { $client.Connect('127.0.0.1',3121); '1' } catch { '0' } finally { $client.Dispose() }"`) do set "AL321_HW_SERVER_READY=%%I"
            )
            if not "!AL321_HW_SERVER_READY!"=="1" (
              echo [ERROR] hw_server 启动后仍未在 TCP:127.0.0.1:3121 就绪。
              if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
              if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
              exit /b 2
            )
            echo [INFO] 已启动并确认 hw_server 就绪: PID=!AL321_HW_SERVER_PID!
          )
          echo [EXEC] "%XSDB_EXE%" "!AL321_XSDB_SCRIPT!"
          call "%XSDB_EXE%" "!AL321_XSDB_SCRIPT!" >"!AL321_XSDB_LOG!" 2>&1
          set "AL321_XSDB_EXIT=!ERRORLEVEL!"
          if not "!AL321_XSDB_EXIT!"=="0" (
            type "!AL321_XSDB_LOG!"
            echo [ERROR] xsdb 只读枚举失败，禁止执行 program_flash。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b !AL321_XSDB_EXIT!
          )
          set "AL321_ALL_CABLE_COUNT="
          set "AL321_CABLE_MATCH_COUNT="
          set "AL321_MATCHED_CABLE_NAME="
          for /f "usebackq tokens=1,* delims==" %%A in (`powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $targets=@(); foreach ($line in Get-Content -LiteralPath $env:AL321_XSDB_LOG) { if (-not $line.StartsWith('__PCIDS_TARGET__')) { continue }; $map=@{}; foreach ($pair in $line.Substring(16).Split('|')) { $parts=$pair.Split('=',2); if ($parts.Count -eq 2) { $map[$parts[0]]=$parts[1] } }; function V($key) { if ($map.ContainsKey($key)) { return [string]$map[$key] }; return '' }; $targets += [pscustomobject]@{ serial=(V 'serial'); cable_name=(V 'cable_name'); target_name=(V 'target_name'); device_name=(V 'device_name') } }; $allCableCount=@($targets | Where-Object { $_.serial } | Select-Object -ExpandProperty serial -Unique).Count; $matched=@($targets | Where-Object { $_.serial -eq $env:BURNER_SN }); $cableMatchCount=@($matched | Select-Object -ExpandProperty serial -Unique).Count; Write-Output ('AL321_ALL_CABLE_COUNT=' + [string]$allCableCount); Write-Output ('AL321_CABLE_MATCH_COUNT=' + [string]$cableMatchCount); if ($matched.Count -gt 0) { Write-Output ('AL321_MATCHED_CABLE_NAME=' + [string]$matched[0].cable_name) }"`) do set "%%A=%%B"
          set "AL321_XSDB_PARSE_EXIT=!ERRORLEVEL!"
          if not "!AL321_XSDB_PARSE_EXIT!"=="0" (
            type "!AL321_XSDB_LOG!"
            echo [ERROR] xsdb 只读枚举输出解析失败，禁止执行 program_flash。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b !AL321_XSDB_PARSE_EXIT!
          )
          if not "!AL321_CABLE_MATCH_COUNT!"=="1" (
            echo [ERROR] AMD 官方工具只读枚举结果中，BURNER_SN=%BURNER_SN% 的 cable 精确匹配数量为 !AL321_CABLE_MATCH_COUNT!，已拒绝执行。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b 2
          )
          if not "!AL321_ALL_CABLE_COUNT!"=="1" (
            echo [ERROR] 当前 hw_server 下检测到 !AL321_ALL_CABLE_COUNT! 条 cable。由于当前 program_flash 命令未验证支持按序列号精确传参，已拒绝执行。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b 2
          )
          set "AL321_JTAGTARGETS_LOG=%TEMP%\pcids_al321_jtagtargets_%TASK_ID%.log"
          if "%TASK_ID%"=="" set "AL321_JTAGTARGETS_LOG=%TEMP%\pcids_al321_jtagtargets.log"
          set "AL321_JTAGTARGETS_READY=0"
          for /L %%R in (1,1,3) do if "!AL321_JTAGTARGETS_READY!"=="0" (
            echo [EXEC] "%PROGRAM_FLASH_EXE%" -jtagtargets -url TCP:127.0.0.1:3121
            call "%PROGRAM_FLASH_EXE%" -jtagtargets -url TCP:127.0.0.1:3121 >"!AL321_JTAGTARGETS_LOG!" 2>&1
            findstr /I /C:"name xczu" "!AL321_JTAGTARGETS_LOG!" >nul
            if errorlevel 1 (
              echo [WARN] program_flash 暂未枚举到 xczu 设备，等待 JTAG 链路稳定后重试 ^(%%R/3^)。
              powershell -NoProfile -Command "Start-Sleep -Seconds 2"
            ) else (
              set "AL321_JTAGTARGETS_READY=1"
            )
          )
          set "AL321_JTAGTARGETS_EXIT=!ERRORLEVEL!"
          if not "!AL321_JTAGTARGETS_EXIT!"=="0" (
            type "!AL321_JTAGTARGETS_LOG!"
            echo [ERROR] program_flash 官方只读枚举失败，禁止执行 Flash固化。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b !AL321_JTAGTARGETS_EXIT!
          )
          set "AL321_PROGRAM_TARGET_COUNT="
          set "AL321_PROGRAM_TARGET_NAME="
          set "AL321_PROGRAM_TARGET_ID="
          set "AL321_MATCHED_DEVICE_NAME="
          for /f "usebackq tokens=1,* delims==" %%A in (`powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $targets=@(); $currentSerial=''; foreach ($line in Get-Content -LiteralPath $env:AL321_JTAGTARGETS_LOG) { if ($line -match '^\s*(\d+)\s+(.+?)\s+([A-Za-z0-9_-]+)\s*$' -and $line -notmatch '\(') { $currentSerial=[string]$matches[3]; continue }; if ($line -match '^\s*(\d+)\s+(\S+)\s+\(name\s+([^\s\)]+)\s+idcode\s+([^\s\)]+)\)') { $targets += [pscustomobject]@{ serial=$currentSerial; target_id=[string]$matches[1]; target_name=[string]$matches[2]; device_name=[string]$matches[3] } } }; $matched=@($targets | Where-Object { $_.serial -eq $env:BURNER_SN -and $_.device_name -eq 'arm_dap' }); Write-Output ('AL321_PROGRAM_TARGET_COUNT=' + [string]$matched.Count); if ($matched.Count -gt 0) { Write-Output ('AL321_PROGRAM_TARGET_NAME=' + [string]$matched[0].target_name); Write-Output ('AL321_PROGRAM_TARGET_ID=' + [string]$matched[0].target_id); Write-Output ('AL321_MATCHED_DEVICE_NAME=' + [string]$matched[0].device_name) }"`) do set "%%A=%%B"
          set "AL321_JTAGTARGETS_PARSE_EXIT=!ERRORLEVEL!"
          if not "!AL321_JTAGTARGETS_PARSE_EXIT!"=="0" (
            type "!AL321_JTAGTARGETS_LOG!"
            echo [ERROR] program_flash 官方只读枚举输出解析失败，禁止执行 Flash固化。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b !AL321_JTAGTARGETS_PARSE_EXIT!
          )
          if not "!AL321_PROGRAM_TARGET_COUNT!"=="1" (
            type "!AL321_JTAGTARGETS_LOG!"
            echo [ERROR] program_flash 官方只读枚举结果中，期望 ZynqMP arm_dap 目标精确匹配数量为 !AL321_PROGRAM_TARGET_COUNT!，已拒绝执行。
            if "!AL321_STARTED_HW_SERVER!"=="1" powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
            if "!AL321_DRIVER_SWITCHED!"=="1" powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!"
            exit /b 2
          )
          echo [INFO] 已通过 AMD 官方工具只读枚举精确选择 cable: !AL321_MATCHED_CABLE_NAME!
          echo [INFO] 已通过 program_flash 官方只读枚举精确选择 target_name: !AL321_PROGRAM_TARGET_NAME!
          echo [INFO] 已通过 program_flash 官方只读枚举精确选择 target_id: !AL321_PROGRAM_TARGET_ID!
          echo [INFO] 已验证目标为预期 ZynqMP Flash 访问目标: !AL321_MATCHED_DEVICE_NAME!
          echo [INFO] 使用 ZynqMP PSU/FSBL 路径烧写 QSPI Flash。
          set "AL321_PROGRAM_FLASH_LOG=%TEMP%\pcids_al321_program_flash_%TASK_ID%.log"
          if "%TASK_ID%"=="" set "AL321_PROGRAM_FLASH_LOG=%TEMP%\pcids_al321_program_flash.log"
          echo [EXEC] "%PROGRAM_FLASH_EXE%" -f "%FIRMWARE_PATH%" -offset "%START_ADDRESS%" -flash_type "!AL321_PROGRAM_FLASH_TYPE!" -fsbl "%TARGET_CONFIG_FILE%" -target_id "!AL321_PROGRAM_TARGET_ID!" -url TCP:127.0.0.1:3121
          powershell -NoProfile -ExecutionPolicy Bypass -File "!AL321_PROGRAM_FLASH_STREAM_SCRIPT!" -ProgramFlashExe "%PROGRAM_FLASH_EXE%" -FirmwarePath "%FIRMWARE_PATH%" -StartAddress "%START_ADDRESS%" -FlashType "!AL321_PROGRAM_FLASH_TYPE!" -FsblPath "%TARGET_CONFIG_FILE%" -TargetId "!AL321_PROGRAM_TARGET_ID!" -Url "TCP:127.0.0.1:3121" -LogPath "!AL321_PROGRAM_FLASH_LOG!"
          set "AL321_PROGRAM_FLASH_EXIT=!ERRORLEVEL!"
          if not "!AL321_PROGRAM_FLASH_EXIT!"=="0" (
            findstr /I /C:"Given target do not exist" "!AL321_PROGRAM_FLASH_LOG!" >nul
            if not errorlevel 1 if not "!AL321_PROGRAM_TARGET_NAME!"=="" (
              echo [WARN] program_flash 拒绝了 target_id，正在回退为 target_name=!AL321_PROGRAM_TARGET_NAME! 重试一次。
              echo [EXEC] "%PROGRAM_FLASH_EXE%" -f "%FIRMWARE_PATH%" -offset "%START_ADDRESS%" -flash_type "!AL321_PROGRAM_FLASH_TYPE!" -fsbl "%TARGET_CONFIG_FILE%" -target_name "!AL321_PROGRAM_TARGET_NAME!" -url TCP:127.0.0.1:3121
              powershell -NoProfile -ExecutionPolicy Bypass -File "!AL321_PROGRAM_FLASH_STREAM_SCRIPT!" -ProgramFlashExe "%PROGRAM_FLASH_EXE%" -FirmwarePath "%FIRMWARE_PATH%" -StartAddress "%START_ADDRESS%" -FlashType "!AL321_PROGRAM_FLASH_TYPE!" -FsblPath "%TARGET_CONFIG_FILE%" -TargetName "!AL321_PROGRAM_TARGET_NAME!" -Url "TCP:127.0.0.1:3121" -LogPath "!AL321_PROGRAM_FLASH_LOG!"
              set "AL321_PROGRAM_FLASH_EXIT=!ERRORLEVEL!"
            )
          )
          findstr /I /C:"ERROR:" /C:"Flash Operation Failed" /C:"Failed to" /C:"Wrong flash_type specified" "!AL321_PROGRAM_FLASH_LOG!" >nul
          if not errorlevel 1 (
            if "!AL321_PROGRAM_FLASH_EXIT!"=="0" set "AL321_PROGRAM_FLASH_EXIT=1"
          )
          if "!AL321_STARTED_HW_SERVER!"=="1" (
            echo [INFO] 正在停止本次启动的 hw_server ^(PID=!AL321_HW_SERVER_PID!^)
            powershell -NoProfile -Command "Stop-Process -Id !AL321_HW_SERVER_PID! -Force -ErrorAction SilentlyContinue; Get-Process hw_server -ErrorAction SilentlyContinue | Stop-Process -Force"
          )
          if "!AL321_DRIVER_SWITCHED!"=="1" (
            echo [INFO] 正在将 AL321 恢复为 WinUSB 驱动。
            powershell -NoProfile -ExecutionPolicy Bypass -File "%AL321_DRIVER_SWITCH_SCRIPT%" -Mode winusb -StateFile "!AL321_DRIVER_STATE_FILE!" >"!AL321_DRIVER_SWITCH_STDOUT_LOG!" 2>&1
            set "AL321_DRIVER_RESTORE_EXIT=!ERRORLEVEL!"
            if not "!AL321_DRIVER_RESTORE_EXIT!"=="0" (
              type "!AL321_DRIVER_SWITCH_STDOUT_LOG!"
              call :PCIDS_PRINT_AL321_DRIVER_SWITCH_LOG
              echo [ERROR] Flash 命令已结束，但 AL321 恢复 WinUSB 驱动失败；这是收尾恢复步骤失败，不是 Flash 信息读取失败的根因。
              echo [ERROR] 如 Windows 弹出 UAC，请允许恢复驱动；否则后续 openFPGALoader/SRAM 下载可能无法识别 AL321。
              if "!AL321_PROGRAM_FLASH_EXIT!"=="0" set "AL321_PROGRAM_FLASH_EXIT=!AL321_DRIVER_RESTORE_EXIT!"
            )
          )
          if not "!AL321_PROGRAM_FLASH_EXIT!"=="0" (
            findstr /I /C:"Wrong flash_type specified" "!AL321_PROGRAM_FLASH_LOG!" >nul
            if not errorlevel 1 (
              echo [ERROR] 当前 program_flash 不支持 flash_type=!AL321_PROGRAM_FLASH_TYPE!。
              echo [ERROR] Vitis 2020.2 支持的 ZynqMP QSPI 选项包括: qspi-x1-single, qspi-x2-single, qspi-x4-single, qspi-x8-dual_parallel, qspi-x1-dual_stacked, qspi-x2-dual_stacked, qspi-x4-dual_stacked。
              echo [ERROR] 请在任务参数里改为上述原始值；例如 stacked 场景应使用 qspi-x4-dual_stacked，不要使用 qspi-x8-dual_stacked。
            )
            findstr /I /C:"Initialization done, programming the memory" "!AL321_PROGRAM_FLASH_LOG!" >nul
            if not errorlevel 1 (
              findstr /I /C:"Problem in Connecting to Target" /C:"Flash programming initialization failed" /C:"Error getting stream information for target node" "!AL321_PROGRAM_FLASH_LOG!" >nul
              if not errorlevel 1 (
                echo [ERROR] program_flash 已成功通过 FSBL 初始化并读取到 Flash 信息，但在加载/连接 mini u-boot 阶段失败。
                echo [ERROR] 失败位置: mini u-boot target stream。此阶段通常需要 FSBL 已正确初始化 DDR、APU/PS target 可被 hw_server 稳定访问。
                echo [ERROR] 本次使用参数: flash_type=!AL321_PROGRAM_FLASH_TYPE!, fsbl=%TARGET_CONFIG_FILE%, target=!AL321_PROGRAM_TARGET_NAME!。
                echo [ERROR] 请优先核对: 1^) FSBL 是否与当前 BOOT.bin/XSA 完全同源; 2^) DDR 初始化是否匹配当前板卡; 3^) 当前 Vitis 2020.2 的 mini u-boot 是否支持该 ZynqMP/DDR 配置。
              )
            )
            findstr /I /C:"Retrieving Flash info" "!AL321_PROGRAM_FLASH_LOG!" >nul
            if not errorlevel 1 (
              findstr /I /C:"Flash Operation Failed" "!AL321_PROGRAM_FLASH_LOG!" >nul
              if not errorlevel 1 (
                echo [ERROR] program_flash 已连接到 ZynqMP arm_dap/PS 访问目标并开始读取 QSPI Flash 信息，说明 cable、target、BOOT.bin 路径已通过基本检查。
                echo [ERROR] 失败位置: Retrieving Flash info。此阶段通常由 FSBL 初始化 QSPI 控制器后读取 Flash ID；失败说明 FSBL/启动模式/QSPI 拓扑至少有一项不匹配。
                echo [ERROR] 本次使用参数: flash_type=!AL321_PROGRAM_FLASH_TYPE!, fsbl=%TARGET_CONFIG_FILE%, offset=%START_ADDRESS%。
                echo [ERROR] 下一步排查顺序: 1^) 确认板卡拨码/启动模式处于 JTAG 或厂商要求的烧录模式并重新上电; 2^) 使用与当前 BOOT.bin/XSA 同源生成的 FSBL.elf; 3^) 按板卡原理图选择 QSPI 拓扑。
                echo [ERROR] 常见 ZynqMP QSPI 拓扑候选: 单颗x4=qspi-x4-single; 双颗并行=qspi-x8-dual_parallel; 双颗堆叠=qspi-x4-dual_stacked。
                echo [ERROR] 如果 qspi-x4-single 和 qspi-x8-dual_parallel 都已在同一块板上失败，请优先尝试 qspi-x4-dual_stacked，并确认 FSBL 来自同一硬件工程。
              )
            )
            echo [ERROR] ZynqMP QSPI Flash固化失败。请按上方具体错误类型处理；若没有额外错误类型，请保存完整日志给硬件/FPGA同事核对 FSBL、QSPI拓扑和启动拨码。
          ) else (
            echo [INFO] ZynqMP QSPI Flash固化完成，请切换为 QSPI Boot 模式后重新上电。
          )
          exit /b !AL321_PROGRAM_FLASH_EXIT!
        )
        if "%OPENFPGALOADER_EXE%"=="" for /f "delims=" %%I in ('where openFPGALoader.exe 2^>nul') do if "%OPENFPGALOADER_EXE%"=="" set "OPENFPGALOADER_EXE=%%I"
        if "%OPENFPGALOADER_EXE%"=="" for /f "delims=" %%I in ('where openFPGALoader 2^>nul') do if "%OPENFPGALOADER_EXE%"=="" set "OPENFPGALOADER_EXE=%%I"
        if "%OPENFPGALOADER_EXE%"=="" (
          echo [ERROR] 未找到 openFPGALoader.exe，请检查 tools\burners\AL321\openFPGALoader 或设置 OPENFPGALOADER_EXE。
          exit /b 127
        )
        if not exist "%OPENFPGALOADER_EXE%" (
          echo [ERROR] OPENFPGALOADER_EXE 指向的文件不存在: %OPENFPGALOADER_EXE%
          exit /b 127
        )
        set "AL321_MODE_FLAG=-m"
        set "AL321_DETECT_FLASH_FLAG="
        set "AL321_VERIFY_FLAG="
        set "AL321_RESET_FLAG="
        if "%COMPLETION_ACTION%"=="复位运行" set "AL321_RESET_FLAG=-r"
        if /I "%COMPLETION_ACTION%"=="reset-run" set "AL321_RESET_FLAG=-r"
        if /I "%COMPLETION_ACTION%"=="run" set "AL321_RESET_FLAG=-r"
        if "%COMPLETION_ACTION%"=="仅复位" set "AL321_RESET_FLAG=-r"
        if /I "%COMPLETION_ACTION%"=="reset-halt" (
          echo [WARN] openFPGALoader 不支持 reset-halt，将退化为复位。
          set "AL321_RESET_FLAG=-r"
        )
        set "AL321_FREQ_ARG="
        if not "%WRITE_SPEED_KHZ%"=="" set "AL321_FREQ_ARG=--freq %WRITE_SPEED_KHZ%000"
        set "AL321_OFFSET_ARG="
        if not "%START_ADDRESS%"=="" set "AL321_OFFSET_ARG=-o %START_ADDRESS%"
        set "AL321_ERASE_ARG="
        if /I "%AL321_OPERATION_MODE%"=="flash" if "%ERASE_MODE%"=="全片擦除" set "AL321_ERASE_ARG=--bulk-erase"
        if /I "%AL321_OPERATION_MODE%"=="flash" if /I "%ERASE_MODE%"=="chip" set "AL321_ERASE_ARG=--bulk-erase"
        if /I "%AL321_OPERATION_MODE%"=="flash" if /I "%ERASE_MODE%"=="all" set "AL321_ERASE_ARG=--bulk-erase"
        for %%I in ("%FIRMWARE_PATH%") do set "AL321_FIRMWARE_EXT=%%~xI"
        if /I not "%AL321_OPERATION_MODE%"=="flash" if /I "!AL321_FIRMWARE_EXT!"==".bin" (
          echo [ERROR] ZynqMP SRAM下载需要 FPGA bitstream ^(.bit^) 文件，不能使用 BOOT.bin/.bin。
          echo [ERROR] BOOT.bin 是启动镜像/Flash固化文件；请改选 Flash固化，或为 SRAM下载选择 Vivado 生成的 .bit 文件。
          exit /b 2
        )
        for %%I in ("%OPENFPGALOADER_EXE%") do set "AL321_OPENFPGALOADER_DIR=%%~dpI"
        set "AL321_DEVICE_COUNT=0"
        set "AL321_MATCHED_COUNT=0"
        set "AL321_ONLY_PID="
        set "AL321_ONLY_INSTANCE="
        for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $prefixes=@('USB\VID_0403&PID_6014','USB\VID_03FD&PID_0007','USB\VID_03FD&PID_0008','USB\VID_03FD&PID_000F','USB\VID_03FD&PID_0013','USB\VID_03FD&PID_000D'); if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) { $devices=Get-PnpDevice -PresentOnly | Where-Object { $id=[string]$_.InstanceId; $id -and (($prefixes | Where-Object { $id.StartsWith($_) }).Count -gt 0) } | Select-Object -ExpandProperty InstanceId } else { $devices=Get-CimInstance Win32_PnPEntity | Where-Object { $id=[string]$_.PNPDeviceID; $id -and (($prefixes | Where-Object { $id.StartsWith($_) }).Count -gt 0) } | Select-Object -ExpandProperty PNPDeviceID }; foreach ($id in $devices | Select-Object -Unique) { if ($id -match 'PID_([0-9A-F]{4})') { Write-Output ($Matches[1] + ';' + $id) } }"`) do (
          set /a AL321_DEVICE_COUNT+=1
          for /f "tokens=1,* delims=;" %%A in ("%%L") do (
            if "%BURNER_SN%"=="" (
              set "AL321_ONLY_PID=%%A"
              set "AL321_ONLY_INSTANCE=%%B"
            ) else (
              echo "%%B" | findstr /I /C:"%BURNER_SN%" >nul
              if not errorlevel 1 (
                set /a AL321_MATCHED_COUNT+=1
                set "AL321_ONLY_PID=%%A"
                set "AL321_ONLY_INSTANCE=%%B"
              )
            )
          )
        )
        if "!AL321_DEVICE_COUNT!"=="0" (
          echo [ERROR] 未检测到 AL321 设备，请确认设备已连接且 VID/PID 为 0403:6014 或受支持的 03FD 型号。
          exit /b 2
        )
        if not "%BURNER_SN%"=="" (
          if not "!AL321_MATCHED_COUNT!"=="1" (
            echo [ERROR] 在 !AL321_DEVICE_COUNT! 个 AL321 设备中，序列号 %BURNER_SN% 的精确匹配数量为 !AL321_MATCHED_COUNT!，已拒绝执行。
            exit /b 2
          )
          echo [INFO] 已从 !AL321_DEVICE_COUNT! 个设备中精确选择 BURNER_SN=%BURNER_SN%。
        ) else (
          if not "!AL321_DEVICE_COUNT!"=="1" (
            echo [ERROR] 检测到 !AL321_DEVICE_COUNT! 个 AL321 设备但未配置 BURNER_SN，禁止猜测。
            exit /b 2
          )
          echo [INFO] 未配置 BURNER_SN，当前仅发现 1 个匹配设备: !AL321_ONLY_INSTANCE!
        )
        set "AL321_CABLE="
        set "AL321_PROBE_FW_ARG="
        set "AL321_DETECT_LOG=%TEMP%\pcids_al321_detect_%TASK_ID%.log"
        if "%TASK_ID%"=="" set "AL321_DETECT_LOG=%TEMP%\pcids_al321_detect.log"
        if /I "!AL321_ONLY_PID!"=="6014" (
          if not "!AL321_DEVICE_COUNT!"=="1" (
            echo [ERROR] 检测到 !AL321_DEVICE_COUNT! 个 AL321 设备；0403:6014 场景下 openFPGALoader 不支持安全按序列号锁定，禁止执行。
            exit /b 2
          )
          if not "%AL321_OPENFPGALOADER_CABLE%"=="" set "AL321_CABLE=%AL321_OPENFPGALOADER_CABLE%"
          if not defined AL321_CABLE (
            echo [INFO] 检测到 FTDI 0403:6014，正在只读探测兼容 cable 类型。
            for %%C in (digilent_hs2 digilent_hs3 digilent_ad jtag-smt2-nc gatemate_pgm) do if not defined AL321_CABLE (
              del /f /q "!AL321_DETECT_LOG!" >nul 2>nul
              "%OPENFPGALOADER_EXE%" -c %%C !AL321_FREQ_ARG! --detect -v >"!AL321_DETECT_LOG!" 2>&1
              if "!ERRORLEVEL!"=="0" (
                findstr /I /C:"found 0 devices" /C:"Error:" "!AL321_DETECT_LOG!" >nul
                if errorlevel 1 set "AL321_CABLE=%%C"
              )
            )
          )
          if not defined AL321_CABLE (
            echo [ERROR] 已发现 AL321 ^(0403:6014^)，但 openFPGALoader 未在 JTAG 链上发现任何目标器件。
            echo [ERROR] 请检查目标板上电、JTAG 连接、拨码模式以及 FPGA 是否真实挂载在该链路上。
            echo [ERROR] 请使用 tools\burners\AL321\drivers 中的工具为该设备安装 WinUSB；如已知 cable 类型，可设置 AL321_OPENFPGALOADER_CABLE。
            exit /b 2
          )
          echo [INFO] 已识别 FTDI AL321 cable: !AL321_CABLE!
        ) else if /I "!AL321_ONLY_PID!"=="0008" (
          set "AL321_CABLE=xilinxPlatformCableUsb"
        ) else if /I "!AL321_ONLY_PID!"=="0013" (
          set "AL321_CABLE=xilinxPlatformCableUsb"
          set "AL321_PROBE_FIRMWARE=!AL321_OPENFPGALOADER_DIR!share\openFPGALoader\compat\firmware\xusb_xp2.hex"
          if not exist "!AL321_PROBE_FIRMWARE!" (
            echo [ERROR] 检测到未初始化 Xilinx Platform Cable USB ^(03FD:0013^)，但缺少合法的 xusb_xp2.hex: !AL321_PROBE_FIRMWARE!
            echo [ERROR] 请从 AMD/Xilinx 官方驱动或 Vivado Lab 安装包中提取后放入上述位置，系统不会使用伪造固件。
            exit /b 2
          )
          set "AL321_PROBE_FW_ARG=--probe-firmware ""!AL321_PROBE_FIRMWARE!"""
        ) else if /I "!AL321_ONLY_PID!"=="000D" (
          set "AL321_CABLE=xilinxPlatformCableUsb_alt"
          set "AL321_PROBE_FIRMWARE=!AL321_OPENFPGALOADER_DIR!share\openFPGALoader\compat\firmware\xusb_emb.hex"
          if not exist "!AL321_PROBE_FIRMWARE!" (
            echo [ERROR] 检测到未初始化 Xilinx Platform Cable USB ^(03FD:000D^)，但缺少合法的 xusb_emb.hex: !AL321_PROBE_FIRMWARE!
            echo [ERROR] 请从 AMD/Xilinx 官方驱动或 Vivado Lab 安装包中提取后放入上述位置，系统不会使用伪造固件。
            exit /b 2
          )
          set "AL321_PROBE_FW_ARG=--probe-firmware ""!AL321_PROBE_FIRMWARE!"""
        ) else (
          echo [ERROR] 当前 PID !AL321_ONLY_PID! 尚未验证，请配置 AL321_CMD_TEMPLATE
          exit /b 2
        )
        if /I "%AL321_OPERATION_MODE%"=="flash" (
          echo [INFO] 尝试检测 AL321 cable: !AL321_CABLE! 检测目标 Flash
          echo [EXEC] "%OPENFPGALOADER_EXE%" -c !AL321_CABLE! !AL321_PROBE_FW_ARG! !AL321_FREQ_ARG! --detect !AL321_DETECT_FLASH_FLAG! -v
          del /f /q "!AL321_DETECT_LOG!" >nul 2>nul
          "%OPENFPGALOADER_EXE%" -c !AL321_CABLE! !AL321_PROBE_FW_ARG! !AL321_FREQ_ARG! --detect !AL321_DETECT_FLASH_FLAG! -v >"!AL321_DETECT_LOG!" 2>&1
          set "AL321_DETECT_EXIT=!ERRORLEVEL!"
          type "!AL321_DETECT_LOG!"
          findstr /I /C:"SPI Flash access is only available from PSU side" /C:"can't flash non-volatile memory for ZynqMP devices" "!AL321_DETECT_LOG!" >nul
          if not errorlevel 1 (
            echo [ERROR] 已检测到 ZynqMP JTAG 器件，但其 SPI Flash 仅支持从 PSU/启动侧访问，无法通过 AL321/openFPGALoader 的当前 JTAG 路径执行 Flash 固化。
            echo [ERROR] SRAM 下载仍可使用；如需固化，请改用 PSU/启动侧烧写方案。
            exit /b 2
          )
          if not "!AL321_DETECT_EXIT!"=="0" (
            echo [ERROR] 无法检测目标 Flash，请检查驱动、连接状态或 probe firmware。
            exit /b 2
          )
          findstr /I /C:"found 0 devices" /C:"Error:" "!AL321_DETECT_LOG!" >nul
          if not errorlevel 1 (
            echo [ERROR] 预检测显示未发现目标 Flash 或 JTAG 链为空，请检查目标板上电、JTAG 连接、拨码模式以及 probe firmware。
            exit /b 2
          )
          echo [INFO] 已通过 !AL321_CABLE! 检测到目标 Flash，开始执行固化。
        ) else (
          echo [INFO] 尝试检测 AL321 cable: !AL321_CABLE! 检测目标 FPGA
          echo [EXEC] "%OPENFPGALOADER_EXE%" -c !AL321_CABLE! !AL321_PROBE_FW_ARG! !AL321_FREQ_ARG! --detect -v
          del /f /q "!AL321_DETECT_LOG!" >nul 2>nul
          "%OPENFPGALOADER_EXE%" -c !AL321_CABLE! !AL321_PROBE_FW_ARG! !AL321_FREQ_ARG! --detect -v >"!AL321_DETECT_LOG!" 2>&1
          set "AL321_DETECT_EXIT=!ERRORLEVEL!"
          type "!AL321_DETECT_LOG!"
          if not "!AL321_DETECT_EXIT!"=="0" (
            echo [ERROR] 无法检测目标 FPGA，请检查驱动、连接状态或 probe firmware。
            exit /b 2
          )
          findstr /I /C:"found 0 devices" /C:"Error:" "!AL321_DETECT_LOG!" >nul
          if not errorlevel 1 (
            echo [ERROR] 预检测显示未发现目标 FPGA，请检查目标板上电、JTAG 连接、拨码模式以及 FPGA 是否处于可下载状态。
            exit /b 2
          )
          echo [INFO] 已通过 !AL321_CABLE! 检测到目标 FPGA，开始执行 SRAM 下载。
        )
        echo [EXEC] "%OPENFPGALOADER_EXE%" -c !AL321_CABLE! !AL321_PROBE_FW_ARG! !AL321_FREQ_ARG! !AL321_ERASE_ARG! !AL321_OFFSET_ARG! !AL321_MODE_FLAG! !AL321_VERIFY_FLAG! !AL321_RESET_FLAG! "%FIRMWARE_PATH%"
        "%OPENFPGALOADER_EXE%" -c !AL321_CABLE! !AL321_PROBE_FW_ARG! !AL321_FREQ_ARG! !AL321_ERASE_ARG! !AL321_OFFSET_ARG! !AL321_MODE_FLAG! !AL321_VERIFY_FLAG! !AL321_RESET_FLAG! "%FIRMWARE_PATH%"
        exit /b !ERRORLEVEL!
        '''
        ).lstrip(),
    )
    helper = r"""

        goto :PCIDS_AL321_DRIVER_LOG_HELPER_END
        :PCIDS_PRINT_AL321_DRIVER_SWITCH_LOG
        set "AL321_DRIVER_SWITCH_LAST_LOG="
        for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "$dir=$env:AL321_DRIVER_SWITCH_LOG_DIR; if ($dir -and (Test-Path -LiteralPath $dir)) { $f=Get-ChildItem -LiteralPath $dir -Filter 'al321-driver-switch-*.log' -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($f) { [Console]::Out.WriteLine($f.FullName) } }"`) do set "AL321_DRIVER_SWITCH_LAST_LOG=%%L"
        if not "!AL321_DRIVER_SWITCH_LAST_LOG!"=="" (
          echo === AL321驱动切换详细日志 ===
          echo !AL321_DRIVER_SWITCH_LAST_LOG!
          type "!AL321_DRIVER_SWITCH_LAST_LOG!"
          echo === AL321驱动切换详细日志结束 ===
        ) else (
          echo [WARN] 未找到 AL321 驱动切换详细日志。
        )
        exit /b 0
        :PCIDS_AL321_DRIVER_LOG_HELPER_END
    """
    return (runner + helper).replace("__PCIDS_AL321_XSDB_SETUP__", _al321_xsdb_scan_script_setup().rstrip())


def build_system_script_content(script_name: str, burner_name: str) -> str:
    header = _batch_header(script_name, burner_name)
    if script_name == "stlink_stm32_mcu_flash":
        return _compose_batch_script(
            header,
            _strict_flash_parameter_guards(),
            dedent(
                r"""\
                if "%STM32_PROGRAMMER_CLI%"=="" set "STM32_PROGRAMMER_CLI=%ProgramFiles%\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
                if not exist "%STM32_PROGRAMMER_CLI%" for /f "delims=" %%I in ('where STM32_Programmer_CLI.exe 2^>nul') do if "%STM32_PROGRAMMER_CLI%"=="" set "STM32_PROGRAMMER_CLI=%%I"
                if exist "%STM32_PROGRAMMER_CLI%" goto stlink_official
                echo [INFO] STM32CubeProgrammer CLI not found, falling back to pyOCD.
                """
            ),
            _pyocd_runner("ST-LINK", strict=True),
            dedent(
                """\
                exit /b !ERRORLEVEL!
                :stlink_official
                set "CONNECT=port=%INTERFACE_TYPE% freq=%WRITE_SPEED_KHZ% sn=%BURNER_SN%"
                if "%ERASE_MODE%"=="全片擦除" (
                  "%STM32_PROGRAMMER_CLI%" -c %CONNECT% -e all
                  set "STLINK_ERASE_EXIT=!ERRORLEVEL!"
                  if not "!STLINK_ERASE_EXIT!"=="0" exit /b !STLINK_ERASE_EXIT!
                )
                if /I "!PCIDS_FIRMWARE_EXT!"==".bin" (
                  "%STM32_PROGRAMMER_CLI%" -c %CONNECT% -w "%FIRMWARE_PATH%" %START_ADDRESS% -v
                  set "STLINK_FLASH_EXIT=!ERRORLEVEL!"
                ) else (
                  "%STM32_PROGRAMMER_CLI%" -c %CONNECT% -w "%FIRMWARE_PATH%" -v
                  set "STLINK_FLASH_EXIT=!ERRORLEVEL!"
                )
                if not "!STLINK_FLASH_EXIT!"=="0" exit /b !STLINK_FLASH_EXIT!
                if not "%COMPLETION_ACTION%"=="不处理" (
                  "%STM32_PROGRAMMER_CLI%" -c %CONNECT% -rst
                  set "STLINK_RESET_EXIT=!ERRORLEVEL!"
                  exit /b !STLINK_RESET_EXIT!
                )
                exit /b 0
                """
            ),
        )
    if script_name == "jlink_v4_arm_mcu_flash":
        return header + dedent(
            r"""
            if "%JLINK_EXE%"=="" set "JLINK_EXE=%ProgramFiles%\SEGGER\JLink\JLink.exe"
            if not exist "%JLINK_EXE%" if exist "%ProgramFiles%\SEGGER\JLink\JLinkExe.exe" set "JLINK_EXE=%ProgramFiles%\SEGGER\JLink\JLinkExe.exe"
            if not exist "%JLINK_EXE%" for /f "delims=" %%I in ('where JLink.exe 2^>nul') do if "%JLINK_EXE%"=="" set "JLINK_EXE=%%I"
            if not exist "%JLINK_EXE%" (
              echo [INFO] SEGGER J-Link CLI not found, falling back to pyOCD.
            """
        ) + _pyocd_runner("J-LINK", strict=True) + dedent(
            r"""
            )
            set "JLINK_DEVICE=%TARGET_CHIP%"
            if "%JLINK_DEVICE%"=="" set "JLINK_DEVICE=%BOARD_NAME%"
            echo %JLINK_DEVICE%| findstr /R /I "^STM32[A-Z][0-9][0-9][0-9][A-Z][0-9A-Z][A-Z][0-9]$" >nul
            if not errorlevel 1 (
              set "JLINK_DEVICE=%JLINK_DEVICE:~0,-2%"
              echo [INFO] Resolved SEGGER device from TARGET_CHIP: %TARGET_CHIP% ^> !JLINK_DEVICE!
            )
            if "%JLINK_DEVICE%"=="" (
              echo [ERROR] J-Link requires TARGET_CHIP or BOARD_NAME.
              exit /b 2
            )
            if "%BURNER_SN%"=="" (
              echo [ERROR] J-Link official CLI requires BURNER_SN. Bind the exact probe serial number to avoid selecting the wrong device.
              exit /b 2
            )
            if "%WRITE_SPEED_KHZ%"=="" set "WRITE_SPEED_KHZ=1000"
            set "JLINK_CMD=%TEMP%\pcids_jlink_%TASK_ID%.jlink"
            >"%JLINK_CMD%" echo ExitOnError 1
            >>"%JLINK_CMD%" echo SelectEmuBySN %BURNER_SN%
            >>"%JLINK_CMD%" echo si SWD
            if /I "%INTERFACE_TYPE%"=="JTAG" >>"%JLINK_CMD%" echo si JTAG
            >>"%JLINK_CMD%" echo speed %WRITE_SPEED_KHZ%
            >>"%JLINK_CMD%" echo device %JLINK_DEVICE%
            >>"%JLINK_CMD%" echo connect
            if "%ERASE_MODE%"=="全片擦除" >>"%JLINK_CMD%" echo erase
            if /I "%ERASE_MODE%"=="chip" >>"%JLINK_CMD%" echo erase
            if /I "%ERASE_MODE%"=="all" >>"%JLINK_CMD%" echo erase
            if "%START_ADDRESS%"=="" (
              >>"%JLINK_CMD%" echo loadfile "%FIRMWARE_PATH%"
            ) else (
              >>"%JLINK_CMD%" echo loadfile "%FIRMWARE_PATH%" %START_ADDRESS%
            )
            set "JLINK_DO_RESET=1"
            if "%COMPLETION_ACTION%"=="不处理" set "JLINK_DO_RESET=0"
            if /I "%COMPLETION_ACTION%"=="none" set "JLINK_DO_RESET=0"
            if /I "%COMPLETION_ACTION%"=="off" set "JLINK_DO_RESET=0"
            if "!JLINK_DO_RESET!"=="1" >>"%JLINK_CMD%" echo r
            if "%COMPLETION_ACTION%"=="复位运行" >>"%JLINK_CMD%" echo g
            if "%COMPLETION_ACTION%"=="reset-run" >>"%JLINK_CMD%" echo g
            if /I "%COMPLETION_ACTION%"=="run" >>"%JLINK_CMD%" echo g
            >>"%JLINK_CMD%" echo q
            "%JLINK_EXE%" -ExitOnError 1 -CommanderScript "%JLINK_CMD%"
            set "EXIT_CODE=!ERRORLEVEL!"
            del "%JLINK_CMD%" >nul 2>nul
            exit /b !EXIT_CODE!
            """
        )
    if script_name == "pwlink_v2_arm_mcu_flash":
        return _compose_batch_script(header, _pyocd_runner("PWLINK2", strict=True))
    if script_name == "gdlink_arm_mcu_flash":
        return header + dedent(
            r"""
            if not "%GDLINK_CMD_TEMPLATE%"=="" (
              set "PCIDS_CMD=%GDLINK_CMD_TEMPLATE%"
              set "PCIDS_CMD=!PCIDS_CMD:{firmware}=%FIRMWARE_PATH%!"
              set "PCIDS_CMD=!PCIDS_CMD:{target}=%TARGET_CHIP%!"
              set "PCIDS_CMD=!PCIDS_CMD:{interface}=%INTERFACE_TYPE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{speed}=%WRITE_SPEED_KHZ%!"
              set "PCIDS_CMD=!PCIDS_CMD:{address}=%START_ADDRESS%!"
              set "PCIDS_CMD=!PCIDS_CMD:{erase}=%ERASE_MODE%!"
              set "PCIDS_CMD=!PCIDS_CMD:{action}=%COMPLETION_ACTION%!"
              set "PCIDS_CMD=!PCIDS_CMD:{probe}=%BURNER_SN%!"
            """
        ) + _stream_command_helper_setup() + _stream_command_runner() + dedent(
            r"""
              exit /b !PCIDS_STREAM_EXIT!
            )
            if not "%GDLINK_CLI%"=="" if exist "%GDLINK_CLI%" if /I "%TARGET_CHIP:~0,4%"=="GD32" (
              if "%WRITE_SPEED_KHZ%"=="" set "WRITE_SPEED_KHZ=1000"
              set "GDLINK_CMD=%TEMP%\pcids_gdlink_%TASK_ID%.gdlink"
              if "%BURNER_SN%"=="" (
                echo [ERROR] GDLINK official CLI requires BURNER_SN.
                exit /b 2
              )
              if "%TARGET_CHIP%"=="" (
                echo [ERROR] GDLINK official CLI requires TARGET_CHIP.
                exit /b 2
              )
              >"!GDLINK_CMD!" echo c %BURNER_SN%
              >>"!GDLINK_CMD!" echo si 1
              if /I "%INTERFACE_TYPE%"=="JTAG" >>"!GDLINK_CMD!" echo si 0
              >>"!GDLINK_CMD!" echo sd %TARGET_CHIP%
              >>"!GDLINK_CMD!" echo Connect
              if /I "%ERASE_MODE%"=="chip" >>"!GDLINK_CMD!" echo erase
              if /I "%ERASE_MODE%"=="all" >>"!GDLINK_CMD!" echo erase
              if "%ERASE_MODE%"=="全片擦除" >>"!GDLINK_CMD!" echo erase
              if "%START_ADDRESS%"=="" (
                >>"!GDLINK_CMD!" echo load "%FIRMWARE_PATH%"
              ) else (
                >>"!GDLINK_CMD!" echo load "%FIRMWARE_PATH%", %START_ADDRESS%
              )
              set "GDLINK_DO_RESET=1"
              if /I "%COMPLETION_ACTION%"=="none" set "GDLINK_DO_RESET=0"
              if /I "%COMPLETION_ACTION%"=="off" set "GDLINK_DO_RESET=0"
              if "%COMPLETION_ACTION%"=="不处理" set "GDLINK_DO_RESET=0"
              if "!GDLINK_DO_RESET!"=="1" >>"!GDLINK_CMD!" echo r
              if /I "%COMPLETION_ACTION%"=="reset-run" >>"!GDLINK_CMD!" echo g
              if /I "%COMPLETION_ACTION%"=="run" >>"!GDLINK_CMD!" echo g
              if "%COMPLETION_ACTION%"=="复位运行" >>"!GDLINK_CMD!" echo g
              >>"!GDLINK_CMD!" echo q
              echo [INFO] Using official GigaDevice GDLink_CLI for %TARGET_CHIP%.
              "%GDLINK_CLI%" -speed %WRITE_SPEED_KHZ% -commandfile -e "!GDLINK_CMD!"
              set "GDLINK_EXIT=!ERRORLEVEL!"
              del "!GDLINK_CMD!" >nul 2>nul
              exit /b !GDLINK_EXIT!
            )
            echo [INFO] Official GDLink_CLI is unavailable or target is not GD32; falling back to pyOCD CMSIS-DAP.
            """
        ) + _pyocd_runner("GDLINK", strict=True)
    if script_name == "gdlink_arm_mcu_flash":
        return header + _template_runner("GDLINK_CMD_TEMPLATE", "GDLINK_CLI", "请按已安装的 GigaDevice/GD-Link Programmer 版本配置 GDLINK_CMD_TEMPLATE。") + dedent(
            r"""
            "%GDLINK_CLI%" "%FIRMWARE_PATH%"
            exit /b !ERRORLEVEL!
            """
        )
    if script_name == "swd_downloader_arm_mcu_flash":
        return _compose_batch_script(header, _swd_template_runner(), _pyocd_runner("SWD Downloader", strict=True))
    if script_name == "al321_fpga_mcu_flash":
        return header + _al321_openfpgaloader_runner()
    if script_name == "hdsc_ccid_arm_mcu_flash":
        return header + dedent(
            r"""
            rem HDSC native-agent parameter contract: {firmware} {target} {probe} {interface} {speed} {address} {erase} {action}
            set "HDSC_INTERFACE=%INTERFACE_TYPE%"
            echo %TARGET_CHIP% | findstr /I /R "^HC32L13" >nul
            if not errorlevel 1 if /I "%HDSC_INTERFACE%"=="SWD" (
              echo [WARN] 旧任务将 HC32L130 标记为 SWD；已按 V6.04 L006 算法自动改用 UART/ISP。
              set "HDSC_INTERFACE=UART"
            )
            if /I not "!HDSC_INTERFACE!"=="UART" (
              echo [ERROR] HDSC CCID V6.04 L006 为 HC32L130 使用 UART/ISP：RXD=PA9、TXD=PA10、BOOT=BOOT。
              echo [ERROR] 当前任务接口为 %INTERFACE_TYPE%，请将任务接口改为 UART 后重试。
              exit /b 2
            )
            if "%TARGET_CHIP%"=="" (
              echo [ERROR] HDSC CCID 烧录需要 TARGET_CHIP。
              exit /b 2
            )
            if "%HDSC_CCID_AGENT%"=="" if not "%PCIDS_BUNDLED_TOOLS_DIR%"=="" if exist "%PCIDS_BUNDLED_TOOLS_DIR%\HDSC\hdsc_ccid_agent.py" set "HDSC_CCID_AGENT=%PCIDS_BUNDLED_TOOLS_DIR%\HDSC\hdsc_ccid_agent.py"
            if "%HDSC_CCID_AGENT%"=="" set "HDSC_CCID_AGENT=%CD%\tools\burners\HDSC\hdsc_ccid_agent.py"
            if not exist "%HDSC_CCID_AGENT%" (
              echo [ERROR] 未找到内置 HDSC CCID agent: %HDSC_CCID_AGENT%
              echo [ERROR] 请检查 PCIDS_BUNDLED_TOOLS_DIR，或设置 HDSC_CCID_AGENT。
              exit /b 127
            )
            if "%HDSC_CCID_PYTHON%"=="" set "HDSC_CCID_PYTHON=python"
            set "HDSC_BAUD_ARGS=--baud-rate "%WRITE_SPEED_KHZ%""
            set "HDSC_ERASE_MODE=chip"
            if /I "%ERASE_MODE%"=="none" set "HDSC_ERASE_MODE=none"
            if /I "%ERASE_MODE%"=="no-erase" set "HDSC_ERASE_MODE=none"
            if "%ERASE_MODE%"=="不擦除直接编程" set "HDSC_ERASE_MODE=none"
            set "HDSC_COMPLETION_ACTION=reset-run"
            if /I "%COMPLETION_ACTION%"=="none" set "HDSC_COMPLETION_ACTION=none"
            if /I "%COMPLETION_ACTION%"=="off" set "HDSC_COMPLETION_ACTION=none"
            if "%COMPLETION_ACTION%"=="不处理" set "HDSC_COMPLETION_ACTION=none"
            echo [EXEC] "%HDSC_CCID_PYTHON%" "%HDSC_CCID_AGENT%" flash --target-chip "%TARGET_CHIP%" --firmware "%FIRMWARE_PATH%" --erase-mode !HDSC_ERASE_MODE! --completion-action !HDSC_COMPLETION_ACTION! !HDSC_BAUD_ARGS!
            "%HDSC_CCID_PYTHON%" "%HDSC_CCID_AGENT%" flash --target-chip "%TARGET_CHIP%" --firmware "%FIRMWARE_PATH%" --erase-mode !HDSC_ERASE_MODE! --completion-action !HDSC_COMPLETION_ACTION! !HDSC_BAUD_ARGS!
            exit /b !ERRORLEVEL!
            """
        )
    if script_name == "xds510plus_dsp_flash":
        return header + _xds510plus_runner()
    if script_name == "mplab_icd3_pic_flash":
        return header + dedent(
            r"""
            if "%IPECMD_EXE%"=="" set "IPECMD_EXE=%ProgramFiles%\Microchip\MPLABX\v6.20\mplab_platform\mplab_ipe\ipecmd.exe"
            if not exist "%IPECMD_EXE%" for /f "delims=" %%I in ('where ipecmd.exe 2^>nul') do if "%IPECMD_EXE%"=="" set "IPECMD_EXE=%%I"
            if not exist "%IPECMD_EXE%" (
              echo [ERROR] 未找到 MPLAB IPE ipecmd.exe，请安装 MPLAB X IPE 后配置 IPECMD_EXE。
              exit /b 127
            )
            for %%I in ("%IPECMD_EXE%") do set "MPLABX_ROOT=%%~dpI..\.."
            set "MPLAB_JAVA_BIN="
            for /d %%J in ("%MPLABX_ROOT%\sys\java\*\bin") do if exist "%%~fJ\java.exe" set "MPLAB_JAVA_BIN=%%~fJ"
            if not "%MPLAB_JAVA_BIN%"=="" (
              set "PATH=%MPLAB_JAVA_BIN%;%PATH%"
              echo [INFO] Using MPLAB bundled Java: %MPLAB_JAVA_BIN%
            )
            if "%TARGET_CHIP%"=="" (
              echo [ERROR] MPLAB ICD3 烧录需要 TARGET_CHIP。
              exit /b 2
            )
            set "IPECMD_LOG=%TEMP%\pcids_ipecmd_%TASK_ID%.log"
            if "%TASK_ID%"=="" set "IPECMD_LOG=%TEMP%\pcids_ipecmd.log"
            del /f /q "%IPECMD_LOG%" >nul 2>nul

            set "IPECMD_NO_ERASE_ARG="
            if "%ERASE_MODE%"=="不擦除直接编程" set "IPECMD_NO_ERASE_ARG=-OH"
            if /I "%ERASE_MODE%"=="no-erase" set "IPECMD_NO_ERASE_ARG=-OH"

            set "IPECMD_PRESERVE_EE_ARG="
            if "%EEPROM_WRITE%"=="否" set "IPECMD_PRESERVE_EE_ARG=-Z"
            if /I "%EEPROM_WRITE%"=="no" set "IPECMD_PRESERVE_EE_ARG=-Z"
            if /I "%EEPROM_WRITE%"=="false" set "IPECMD_PRESERVE_EE_ARG=-Z"
            if "%EEPROM_WRITE%"=="0" set "IPECMD_PRESERVE_EE_ARG=-Z"

            set "IPECMD_VERIFY_ARG="
            if "%WRITE_VERIFY%"=="1" set "IPECMD_VERIFY_ARG=-YP -YC"
            if /I "%WRITE_VERIFY%"=="true" set "IPECMD_VERIFY_ARG=-YP -YC"
            if /I "%WRITE_VERIFY%"=="yes" set "IPECMD_VERIFY_ARG=-YP -YC"
            if not "%IPECMD_VERIFY_ARG%"=="" (
              if "%EEPROM_WRITE%"=="是" set "IPECMD_VERIFY_ARG=!IPECMD_VERIFY_ARG! -YE"
              if /I "%EEPROM_WRITE%"=="yes" set "IPECMD_VERIFY_ARG=!IPECMD_VERIFY_ARG! -YE"
              if /I "%EEPROM_WRITE%"=="true" set "IPECMD_VERIFY_ARG=!IPECMD_VERIFY_ARG! -YE"
              if "%EEPROM_WRITE%"=="1" set "IPECMD_VERIFY_ARG=!IPECMD_VERIFY_ARG! -YE"
            )

            set "IPECMD_RUN_ARG="
            if "%COMPLETION_ACTION%"=="编程复位后运行" set "IPECMD_RUN_ARG=-OL"
            if "%COMPLETION_ACTION%"=="复位运行" set "IPECMD_RUN_ARG=-OL"
            if /I "%COMPLETION_ACTION%"=="reset-run" set "IPECMD_RUN_ARG=-OL"
            if /I "%COMPLETION_ACTION%"=="run" set "IPECMD_RUN_ARG=-OL"

            set "IPECMD_DO_BLANK=0"
            if "%BLANK_CHECK%"=="是" set "IPECMD_DO_BLANK=1"
            if /I "%BLANK_CHECK%"=="yes" set "IPECMD_DO_BLANK=1"
            if /I "%BLANK_CHECK%"=="true" set "IPECMD_DO_BLANK=1"
            if "%BLANK_CHECK%"=="1" set "IPECMD_DO_BLANK=1"

            set "IPECMD_DO_PROGRAM=1"
            if "%EXECUTE_PROGRAM%"=="否" set "IPECMD_DO_PROGRAM=0"
            if /I "%EXECUTE_PROGRAM%"=="no" set "IPECMD_DO_PROGRAM=0"
            if /I "%EXECUTE_PROGRAM%"=="false" set "IPECMD_DO_PROGRAM=0"
            if "%EXECUTE_PROGRAM%"=="0" set "IPECMD_DO_PROGRAM=0"

            set "IPECMD_EXIT=0"
            if "%ERASE_MODE%"=="全片擦除" (
              echo [EXEC] "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -E
              "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -E >>"%IPECMD_LOG%" 2>&1
              set "IPECMD_EXIT=!ERRORLEVEL!"
              if not "!IPECMD_EXIT!"=="0" goto PCIDS_IPECMD_DONE
              set "IPECMD_NO_ERASE_ARG=-OH"
            )

            if "!IPECMD_DO_BLANK!"=="1" (
              echo [EXEC] "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -C
              "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -C >>"%IPECMD_LOG%" 2>&1
              set "IPECMD_EXIT=!ERRORLEVEL!"
              if not "!IPECMD_EXIT!"=="0" goto PCIDS_IPECMD_DONE
            )

            if "!IPECMD_DO_PROGRAM!"=="1" (
              set "IPECMD_PROGRAM_RUN_ARG=!IPECMD_RUN_ARG!"
              if not "!IPECMD_VERIFY_ARG!"=="" set "IPECMD_PROGRAM_RUN_ARG="
              echo [EXEC] "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -F"%FIRMWARE_PATH%" -M !IPECMD_NO_ERASE_ARG! !IPECMD_PRESERVE_EE_ARG! !IPECMD_PROGRAM_RUN_ARG!
              "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -F"%FIRMWARE_PATH%" -M !IPECMD_NO_ERASE_ARG! !IPECMD_PRESERVE_EE_ARG! !IPECMD_PROGRAM_RUN_ARG! >>"%IPECMD_LOG%" 2>&1
              set "IPECMD_EXIT=!ERRORLEVEL!"
              if not "!IPECMD_EXIT!"=="0" goto PCIDS_IPECMD_DONE
              if not "!IPECMD_VERIFY_ARG!"=="" (
                echo [EXEC] "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -F"%FIRMWARE_PATH%" !IPECMD_VERIFY_ARG! !IPECMD_RUN_ARG!
                "%IPECMD_EXE%" -TPICD3 -P%TARGET_CHIP% -F"%FIRMWARE_PATH%" !IPECMD_VERIFY_ARG! !IPECMD_RUN_ARG! >>"%IPECMD_LOG%" 2>&1
                set "IPECMD_EXIT=!ERRORLEVEL!"
              )
            ) else (
              echo [INFO] 已按配置跳过编程步骤。
            )

            :PCIDS_IPECMD_DONE
            powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; $path=$env:IPECMD_LOG; if (-not (Test-Path -LiteralPath $path)) { Write-Output '[WARN] IPECMD 未生成输出日志。'; exit 0 }; $bytes=[IO.File]::ReadAllBytes($path); $encodings=@([Text.Encoding]::GetEncoding('GB18030'),[Text.Encoding]::UTF8,[Text.Encoding]::Default); $text=''; $bestScore=[int]::MaxValue; foreach ($enc in $encodings) { $candidate=$enc.GetString($bytes); $score=([regex]::Matches($candidate,'\uFFFD|锟斤拷|���')).Count; if ($candidate -match 'Target device|DFP Version|连接到MPLAB|编程|核实|擦除') { $score -= 10 }; if ($score -lt $bestScore) { $bestScore=$score; $text=$candidate } }; $lines=$text -split \"`r?`n\"; Write-Output '--- IPECMD 关键日志摘要 ---'; $patterns=@('Currently loaded firmware','Target device .* found','Device Erased','Erase Succeeded','擦除成功','Programming','Programmed','Program Succeeded','编程/验证完成','Verify','Verified','核实中','核实失败','The operation completed','Operation Succeeded','passed','failed','Error','Could not','Unable','地址'); $matched=@(); foreach ($line in $lines) { $clean=($line -replace '[^\u0009\u000A\u000D\u0020-\u007E\u4E00-\u9FFF]', '').Trim(); if (-not $clean) { continue }; foreach ($pattern in $patterns) { if ($clean -match $pattern) { $matched += $clean; break } } }; $matched | Select-Object -Unique | ForEach-Object { Write-Output ('[IPECMD] ' + $_) }; if ($matched.Count -eq 0) { Write-Output '[IPECMD] 未匹配到关键阶段日志，下面显示最后 40 行。'; $lines | Select-Object -Last 40 | ForEach-Object { $clean=($_ -replace '[^\u0009\u000A\u000D\u0020-\u007E\u4E00-\u9FFF]', '').Trim(); if ($clean) { Write-Output ('[IPECMD] ' + $clean) } } }"
            exit /b !IPECMD_EXIT!
            """
        )
    if script_name in {"altera_blaster_ii_fpga_flash", "altera_blaster_ii_cpld_flash"}:
        return header + dedent(
            r"""
            if "%QUARTUS_PGM%"=="" for /f "delims=" %%I in ('where quartus_pgm.exe 2^>nul') do if "%QUARTUS_PGM%"=="" set "QUARTUS_PGM=%%I"
            if "%QUARTUS_PGM%"=="" (
              echo [ERROR] 未找到 quartus_pgm.exe，请安装 Intel Quartus Programmer 后配置 QUARTUS_PGM。
              exit /b 127
            )
            set "CABLE_NAME=USB-BlasterII"
            if not "%CABLE_INDEX%"=="" if not "%CABLE_INDEX%"=="0" set "CABLE_NAME=USB-BlasterII [USB-%CABLE_INDEX%]"
            set "QUARTUS_CHAIN_LOG=%TEMP%\pcids_quartus_chain_%TASK_ID%.log"
            if "%TASK_ID%"=="" set "QUARTUS_CHAIN_LOG=%TEMP%\pcids_quartus_chain.log"
            echo [INFO] 使用 Quartus cable: %CABLE_NAME%
            echo [EXEC] "%QUARTUS_PGM%" -c "%CABLE_NAME%" -a
            "%QUARTUS_PGM%" -c "%CABLE_NAME%" -a >"%QUARTUS_CHAIN_LOG%" 2>&1
            set "QUARTUS_CHAIN_EXIT=!ERRORLEVEL!"
            type "%QUARTUS_CHAIN_LOG%"
            if not "!QUARTUS_CHAIN_EXIT!"=="0" (
              echo [ERROR] Quartus JTAG 链路枚举失败，禁止烧录。
              exit /b !QUARTUS_CHAIN_EXIT!
            )
            findstr /I /C:"Unable to read device chain" /C:"JTAG chain broken" /C:"No JTAG devices available" "%QUARTUS_CHAIN_LOG%" >nul
            if "!ERRORLEVEL!"=="0" (
              echo [ERROR] 未读取到有效 JTAG 器件链，请检查目标板供电、排线方向和 TCK/TMS/TDI/TDO/GND/VTref。
              exit /b 2
            )
            echo [EXEC] "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "p;%FIRMWARE_PATH%"
            "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "p;%FIRMWARE_PATH%"
            set "QUARTUS_PROGRAM_EXIT=!ERRORLEVEL!"
            if not "!QUARTUS_PROGRAM_EXIT!"=="0" exit /b !QUARTUS_PROGRAM_EXIT!
            if "%WRITE_VERIFY%"=="1" (
              echo [EXEC] "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "v;%FIRMWARE_PATH%"
              "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "v;%FIRMWARE_PATH%"
              exit /b !ERRORLEVEL!
            )
            if /I "%WRITE_VERIFY%"=="true" (
              echo [EXEC] "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "v;%FIRMWARE_PATH%"
              "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "v;%FIRMWARE_PATH%"
              exit /b !ERRORLEVEL!
            )
            if /I "%WRITE_VERIFY%"=="yes" (
              echo [EXEC] "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "v;%FIRMWARE_PATH%"
              "%QUARTUS_PGM%" -m jtag -c "%CABLE_NAME%" -o "v;%FIRMWARE_PATH%"
              exit /b !ERRORLEVEL!
            )
            echo [INFO] WRITE_VERIFY is off; Quartus verify step skipped.
            exit /b 0
            """
        )
    if script_name == "gowin_usb_cable_fpga_flash":
        return header + _template_runner("GOWIN_CMD_TEMPLATE", "GOWIN_PROGRAMMER_CLI", "请安装 Gowin Programmer 并配置 GOWIN_PROGRAMMER_CLI 或 GOWIN_CMD_TEMPLATE。") + dedent(
            r"""
            if "%TARGET_CHIP%"=="" (
              echo [ERROR] Gowin 烧录必须配置 TARGET_CHIP，例如 GW1N-4。
              exit /b 2
            )
            if /I not "%INTERFACE_TYPE%"=="JTAG" (
              echo [ERROR] 当前 Gowin 脚本仅支持 JTAG 接口。
              exit /b 2
            )
            set "GOWIN_OPERATION=2"
            if "%WRITE_VERIFY%"=="1" set "GOWIN_OPERATION=4"
            if /I "%WRITE_VERIFY%"=="yes" set "GOWIN_OPERATION=4"
            if /I "%WRITE_VERIFY%"=="true" set "GOWIN_OPERATION=4"
            if /I "%GOWIN_OPERATION_MODE%"=="flash" set "GOWIN_OPERATION=8"
            if /I "%GOWIN_OPERATION_MODE%"=="flash" if "%WRITE_VERIFY%"=="1" set "GOWIN_OPERATION=9"
            if /I "%GOWIN_OPERATION_MODE%"=="flash" if /I "%WRITE_VERIFY%"=="yes" set "GOWIN_OPERATION=9"
            if /I "%GOWIN_OPERATION_MODE%"=="flash" if /I "%WRITE_VERIFY%"=="true" set "GOWIN_OPERATION=9"
            set "GOWIN_CABLE_INDEX=%CABLE_INDEX%"
            rem programmer_cli index 1 is Gowin USB Cable(FT2CH); 0 is GWU2X.
            if "%GOWIN_CABLE_INDEX%"=="" set "GOWIN_CABLE_INDEX=1"
            set "GOWIN_FREQUENCY=%TCK_FREQUENCY%"
            if "%GOWIN_FREQUENCY%"=="" set "GOWIN_FREQUENCY=1MHz"
            rem The physical package name in TARGET_CHIP is a board property, while
            rem programmer_cli requires a family alias.  Resolve that alias from the
            rem connected JTAG device instead of changing the board configuration.
            set "GOWIN_SCAN_LOG=%TEMP%\pcids_gowin_scan_%TASK_ID%.log"
            "%GOWIN_PROGRAMMER_CLI%" --scan --cable-index !GOWIN_CABLE_INDEX! --frequency "!GOWIN_FREQUENCY!" >"!GOWIN_SCAN_LOG!" 2>&1
            set "GOWIN_SCAN_EXIT=!ERRORLEVEL!"
            type "!GOWIN_SCAN_LOG!"
            if not "!GOWIN_SCAN_EXIT!"=="0" (
              for /F "usebackq delims=" %%L in (`findstr /I /C:"Error:" "!GOWIN_SCAN_LOG!"`) do echo [ERROR] Gowin JTAG scan: %%L
              del /f /q "!GOWIN_SCAN_LOG!" >nul 2>nul
              echo [ERROR] Gowin JTAG 扫描失败，无法确定目标器件。
              exit /b !GOWIN_SCAN_EXIT!
            )
            set "GOWIN_DEVICE="
            for /F "tokens=2" %%A in ('findstr /C:"Name:" "!GOWIN_SCAN_LOG!"') do if "!GOWIN_DEVICE!"=="" set "GOWIN_DEVICE=%%A"
            del /f /q "!GOWIN_SCAN_LOG!" >nul 2>nul
            if "!GOWIN_DEVICE!"=="" (
              echo [ERROR] Gowin JTAG 扫描未返回可用器件型号。
              exit /b 2
            )
            rem GW1N-4D SRAM verification requires the IEEE 1149 variant.
            rem operation 4 reports a false mismatch at address 0 on this device;
            rem operation 17 has been validated against the same bitstream.
            if /I "%GOWIN_OPERATION_MODE%"=="sram" if /I "!GOWIN_DEVICE:~-1!"=="D" if "%WRITE_VERIFY%"=="1" (
              set "GOWIN_OPERATION=17"
              echo [INFO] Gowin SRAM JTAG 1149 verification selected for !GOWIN_DEVICE!.
            )
            if /I "%GOWIN_OPERATION_MODE%"=="sram" if /I "!GOWIN_DEVICE:~-1!"=="D" if /I "%WRITE_VERIFY%"=="yes" (
              set "GOWIN_OPERATION=17"
              echo [INFO] Gowin SRAM JTAG 1149 verification selected for !GOWIN_DEVICE!.
            )
            if /I "%GOWIN_OPERATION_MODE%"=="sram" if /I "!GOWIN_DEVICE:~-1!"=="D" if /I "%WRITE_VERIFY%"=="true" (
              set "GOWIN_OPERATION=17"
              echo [INFO] Gowin SRAM JTAG 1149 verification selected for !GOWIN_DEVICE!.
            )
            rem GW1N-4D is an embedded-Flash part.  Its Flash persistence path is
            rem embFlash (5/6), not an external SPI exFlash (8/9).
            if /I "%GOWIN_OPERATION_MODE%"=="flash" if /I "!GOWIN_DEVICE:~-1!"=="D" (
              set "GOWIN_OPERATION=5"
              if "%WRITE_VERIFY%"=="1" set "GOWIN_OPERATION=6"
              if /I "%WRITE_VERIFY%"=="yes" set "GOWIN_OPERATION=6"
              if /I "%WRITE_VERIFY%"=="true" set "GOWIN_OPERATION=6"
              echo [INFO] Gowin embedded Flash selected for !GOWIN_DEVICE!.
            )
            echo [INFO] Gowin operation=!GOWIN_OPERATION! target=!GOWIN_DEVICE! cable=FT2CH/!GOWIN_CABLE_INDEX! frequency=!GOWIN_FREQUENCY!
            if /I "%GOWIN_OPERATION_MODE%"=="flash" (
              echo [WARN] Flash固化将写入外接配置 Flash；请确认板卡硬件已接入对应 Flash。
            )
            rem Gowin cable name contains parentheses.  Invoke the vendor CLI directly
            rem so cmd.exe does not reinterpret it while forwarding a command string.
            echo [EXEC] "%GOWIN_PROGRAMMER_CLI%" --device "!GOWIN_DEVICE!" --operation_index !GOWIN_OPERATION! --fsFile "%FIRMWARE_PATH%" --cable "Gowin USB Cable(FT2CH)" --cable-index !GOWIN_CABLE_INDEX! --frequency "!GOWIN_FREQUENCY!"
            set "GOWIN_RUN_LOG=%TEMP%\pcids_gowin_run_%TASK_ID%.log"
            "%GOWIN_PROGRAMMER_CLI%" --device "!GOWIN_DEVICE!" --operation_index !GOWIN_OPERATION! --fsFile "%FIRMWARE_PATH%" --cable "Gowin USB Cable(FT2CH)" --cable-index !GOWIN_CABLE_INDEX! --frequency "!GOWIN_FREQUENCY!" >"!GOWIN_RUN_LOG!" 2>&1
            set "GOWIN_EXIT=!ERRORLEVEL!"
            type "!GOWIN_RUN_LOG!"
            rem Some Programmer versions print an error but return exit code 0.
            findstr /I /C:"Error:" /C:"Verify failed" "!GOWIN_RUN_LOG!" >nul
            if not errorlevel 1 (
              for /F "usebackq delims=" %%L in (`findstr /I /C:"Error:" /C:"Verify failed" "!GOWIN_RUN_LOG!"`) do echo [ERROR] Gowin Programmer: %%L
              set "GOWIN_EXIT=64"
            )
            del /f /q "!GOWIN_RUN_LOG!" >nul 2>nul
            exit /b !GOWIN_EXIT!
            """
        )
    if script_name == "sd_card_zynq7000_boot_update":
        return header + dedent(
            r"""
            if "%SD_TARGET_PATH%"=="" (
              echo [ERROR] 未配置 SD_TARGET_PATH，无法写入 SD 卡。
              exit /b 2
            )
            if not exist "%SD_TARGET_PATH%" (
              echo [ERROR] SD 卡目标路径不存在: %SD_TARGET_PATH%
              exit /b 2
            )
            copy /Y "%FIRMWARE_PATH%" "%SD_TARGET_PATH%\" >nul
            if errorlevel 1 exit /b !ERRORLEVEL!
            if "%COMPLETION_ACTION%"=="自动弹出SD卡" echo [INFO] 已写入，请在系统托盘安全弹出 SD 卡。
            exit /b 0
            """
        )
    if script_name == "sylixos_ls2k_ftp_serial_flash":
        return dedent(
            """\
            #!/bin/sh
            set -eu

            echo "[INFO] PCIDS SylixOS LS2K FTP+serial flash script started"
            echo "[INFO] Artifact path on board: ${FIRMWARE_PATH:-}"
            echo "[INFO] Target directory: ${TARGET_PATH:-/media/hdd0}"
            echo "[INFO] Board address: ${BOARD_TARGET_ADDRESS:-${TARGET_IP:-}}"
            echo "[INFO] Local TFTP address: ${LOCAL_IP:-192.168.1.100}"

            if [ -z "${FIRMWARE_PATH:-}" ]; then
              echo "[ERROR] FIRMWARE_PATH is empty. Please select an artifact from repository."
              exit 1
            fi

            if [ ! -f "${FIRMWARE_PATH}" ]; then
              echo "[ERROR] Uploaded artifact not found on board: ${FIRMWARE_PATH}"
              exit 1
            fi

            artifact_name="${REMOTE_ARTIFACT_NAME:-$(basename "${FIRMWARE_PATH}")}"
            target_file="${TARGET_PATH:-/media/hdd0}/${artifact_name}"
            if [ "${FIRMWARE_PATH}" != "${target_file}" ]; then
              echo "[INFO] Normalizing artifact path to ${target_file}"
              mkdir -p "${TARGET_PATH:-/media/hdd0}"
              cp -f "${FIRMWARE_PATH}" "${target_file}"
            fi

            if [ ! -s "${target_file}" ]; then
              echo "[ERROR] Target ELF is missing or empty: ${target_file}"
              exit 1
            fi

            ls -l "${target_file}"
            echo "[INFO] FTP replacement completed: ${target_file}"
            echo "[INFO] Serial check step: reboot the board and observe boot logs to confirm the replaced ELF starts normally."
            echo "[INFO] If PMON boot entry must be updated, run: set al1 /dev/fs/fat@wd0/${artifact_name}"
            echo "[INFO] If network boot fallback is needed, use PMON: ifconfig syn0 ${BOARD_TARGET_ADDRESS:-192.168.1.230}; load tftp://${LOCAL_IP:-192.168.1.100}/${artifact_name}; set al1 /dev/fs/fat@wd0/${artifact_name}; g"
            exit 0
            """
        )
    return header + dedent(
        """
        echo [ERROR] 未实现当前烧录器的系统脚本。
        exit /b 127
        """
    )
