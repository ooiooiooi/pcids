from __future__ import annotations

"""
数据库配置和连接管理
"""
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool
from pathlib import Path
from typing import Optional
import json
import os
import re
import uuid
from threading import Lock
from backend.utils.app_paths import get_app_data_root
from backend.utils.repository_sync_identity import (
    generate_codearts_repository_sync_uuid,
    generate_repository_sync_uuid,
)
from backend.utils.text_normalization import normalize_text, normalize_text_payload


def get_db_path() -> Path:
    """获取数据库文件路径"""
    db_path = os.environ.get('DB_PATH')
    if db_path:
        return Path(db_path)
    return get_app_data_root() / 'app_data.db'


def create_sqlite_engine(db_path: Path):
    """创建 SQLite 数据库引擎，针对 B/S 架构优化"""
    db_path = Path(db_path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f'sqlite:///{db_path}',
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=QueuePool,  # B/S架构：连接池
        pool_pre_ping=True,
        echo=False,
    )

    # 应用 SQLite PRAGMAs 优化性能
    def set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            cursor.execute("PRAGMA journal_mode=DELETE;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA cache_size=-20000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    event.listen(engine, "connect", set_pragmas)
    return engine


# 创建数据库引擎
db_path = get_db_path()
engine = create_sqlite_engine(db_path)

# 创建会话工厂
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# 创建线程安全的会话
db_session = scoped_session(lambda: SessionLocal())

# Schema compatibility checks are intentionally kept for upgrades, but running
# dozens of PRAGMA/CREATE statements before every request is very expensive on
# a WAL database.  Cache only a completed check; a failed migration is never
# cached and will be retried by the next request.
_schema_ready = False
_schema_ready_lock = Lock()
_MESSAGE_TIME_MIGRATION_BATCH_SIZE = 500


DEFAULT_BURNER_CATALOG = [
    {"name": "J-LINK", "type": "J-LINK", "description": "Segger J-LINK 系列烧录器"},
    {"name": "PWLINK2", "type": "PWLINK2", "description": "PWLINK 系列烧录器"},
    {"name": "GDLINK", "type": "GDLINK", "description": "GD 系列烧录器"},
    {"name": "SWD下载器", "type": "SWD下载器", "description": "通用 SWD 下载器"},
    {"name": "AL321", "type": "AL321", "description": "AL321 系列烧录器"},
    {"name": "ST-LINK", "type": "ST-LINK", "description": "ST-LINK 系列烧录器"},
    {"name": "HDSC CCID", "type": "HDSC CCID", "description": "HDSC CCID 烧录器"},
    {"name": "XDS510plus", "type": "XDS510plus", "description": "TI XDS510plus 仿真器"},
    {"name": "MPLAB ICD 3 DV164035", "type": "MPLAB ICD 3 DV164035", "description": "Microchip MPLAB ICD 3 调试器"},
    {"name": "Altera Blaster II", "type": "Altera Blaster II", "description": "Intel/Altera FPGA 下载器"},
    {"name": "Gowin USB Cable", "type": "Gowin USB Cable", "description": "高云 FPGA 下载线"},
    {"name": "SD卡文件写入", "type": "SD卡文件写入", "description": "SD 卡文件写入通道"},
]

DEFAULT_PRODUCT_CATALOG = [
    {
        "name": "STM32F407VGT6开发板",
        "chip_type": "ARM",
        "chip_model": "STM32F407VGT6",
        "serial_number": "LL6FAPCH6OSB",
        "voltage": "DC 12V",
        "temp_range": "5~50",
        "burn_interface": ["JTAG", "SWD"],
        "interface": ["USB"],
        "config_description": "STM32F4系列Cortex-M4内核，1MB Flash，192KB RAM",
    },
    {
        "name": "LPC55S69评估板",
        "chip_type": "ARM",
        "chip_model": "LPC55S69",
        "serial_number": "K3JF8H2D1N",
        "voltage": "DC 5V",
        "temp_range": "0~70",
        "burn_interface": ["JTAG", "SWD"],
        "interface": ["USB"],
        "config_description": "NXP LPC55S69，双核Cortex-M33",
    },
    {
        "name": "ESP32开发板",
        "chip_type": "ARM",
        "chip_model": "ESP32-S3",
        "serial_number": "M7PQ4W9R2T",
        "voltage": "DC 3.3V",
        "temp_range": "-40~85",
        "burn_interface": ["UART"],
        "interface": ["USB"],
        "config_description": "乐鑫ESP32-S3，支持WiFi和蓝牙",
    },
    {
        "name": "TI系列板卡",
        "chip_type": "DSP",
        "chip_model": "TMS320F28335",
        "serial_number": "XF3K9M1L5P",
        "voltage": "DC 12V",
        "temp_range": "0~70",
        "burn_interface": ["JTAG"],
        "interface": ["CAN"],
        "config_description": "TMS320F28335，C2000系列数字控制",
    },
    {
        "name": "PIC32MZ开发板",
        "chip_type": "PIC",
        "chip_model": "PIC32MZ",
        "serial_number": "B2HN7D4S8W",
        "voltage": "DC 3.3V",
        "temp_range": "-40~85",
        "burn_interface": ["ICSP"],
        "interface": ["SPI"],
        "config_description": "Microchip PIC32MZ，双精度FPU",
    },
    {
        "name": "CycloneV FPGA板",
        "chip_type": "FPGA",
        "chip_model": "Cyclone V",
        "serial_number": "Q8RT5J3K6M",
        "voltage": "DC 5V",
        "temp_range": "0~70",
        "burn_interface": ["JTAG"],
        "interface": ["以太网"],
        "config_description": "Altera Cyclone V，FPGA+ARM双架构",
    },
    {
        "name": "EPM240T100开发板",
        "chip_type": "Altera-CPLD",
        "chip_model": "EPM240T100",
        "serial_number": "W4LM9P2N7X",
        "voltage": "DC 3.3V",
        "temp_range": "0~70",
        "burn_interface": ["JTAG"],
        "interface": ["JTAG"],
        "config_description": "Altera CPLD，240个LE",
    },
    {
        "name": "RK3568核心板",
        "chip_type": "ARM",
        "chip_model": "RK3568",
        "serial_number": "Z5TY8K3J1R",
        "voltage": "DC 5V",
        "temp_range": "-20~70",
        "burn_interface": ["UART"],
        "interface": ["以太网"],
        "config_description": "Rockchip RK3568，四核Cortex-A55",
    },
    {
        "name": "GD32开发板",
        "chip_type": "ARM",
        "chip_model": "GD32F407",
        "serial_number": "G3D2L1N8K5A",
        "voltage": "DC 5V",
        "temp_range": "-40~85",
        "burn_interface": ["JTAG", "SWD"],
        "interface": ["USB"],
        "config_description": "兆易创新 GD32 Cortex-M4 开发板，兼容常见 SWD/JTAG 烧录流程",
    },
    {
        "name": "ARM Cortex-M开发板",
        "chip_type": "ARM",
        "chip_model": "Cortex-M",
        "serial_number": "ARMCORTEXM001",
        "voltage": "DC 5V",
        "temp_range": "-40~85",
        "burn_interface": ["JTAG", "SWD"],
        "interface": ["USB"],
        "config_description": "通用 ARM Cortex-M 系列开发板，用于适配 SWD 下载器等通用烧录流程",
    },
    {
        "name": "Xilinx FPGA开发板",
        "chip_type": "FPGA",
        "chip_model": "Artix-7",
        "serial_number": "XILINXFPGA001",
        "voltage": "DC 5V",
        "temp_range": "0~70",
        "burn_interface": ["JTAG"],
        "interface": ["USB"],
        "config_description": "Xilinx FPGA 开发板，适配 Vivado/ISE 与 AL321 烧录流程",
    },
    {
        "name": "HDSC MCU开发板",
        "chip_type": "ARM",
        "chip_model": "HDSC MCU",
        "serial_number": "HDSCMCU0001",
        "voltage": "DC 5V",
        "temp_range": "-40~85",
        "burn_interface": ["SWD"],
        "interface": ["USB"],
        "config_description": "HDSC MCU 开发板，适配 HDSC ISP/HDSC CCID 烧录流程",
    },
    {
        "name": "Gowin FPGA",
        "chip_type": "FPGA",
        "chip_model": "GW1N",
        "serial_number": "GOWINFPGA001",
        "voltage": "DC 5V",
        "temp_range": "0~70",
        "burn_interface": ["JTAG"],
        "interface": ["USB"],
        "config_description": "高云 FPGA 开发板，适配 Gowin Programmer 与 Gowin USB Cable 下载线",
    },
    {
        "name": "翼辉SylixOS",
        "chip_type": "其他",
        "chip_model": "SylixOS",
        "serial_number": "SYLIXOS-LS2K-001",
        "voltage": "DC 12V",
        "temp_range": "0~70",
        "burn_interface": ["UART"],
        "interface": ["以太网", "UART"],
        "config_description": "翼辉 SylixOS 系统板卡，默认通过 FTP 上传 bspls2kpcm2k01.elf，并通过串口协同执行确认流程",
    },
    {
        "name": "LS2K",
        "chip_type": "其他",
        "chip_model": "LS2K",
        "serial_number": "LS2K-SYLIXOS-001",
        "voltage": "DC 12V",
        "temp_range": "0~70",
        "burn_interface": ["UART"],
        "interface": ["以太网", "UART"],
        "config_description": "龙芯 LS2K 系列板卡，适配翼辉 SylixOS FTP+串口混合协同烧写流程",
    },
    {
        "name": "龙芯2K",
        "chip_type": "其他",
        "chip_model": "Loongson 2K",
        "serial_number": "LOONGSON-2K-001",
        "voltage": "DC 12V",
        "temp_range": "0~70",
        "burn_interface": ["UART"],
        "interface": ["以太网", "UART"],
        "config_description": "龙芯 2K 板卡，适配翼辉 SylixOS bspls2kpcm2k01.elf 烧写流程",
    },
    {
        "name": "bspls2kpcm2k01",
        "chip_type": "其他",
        "chip_model": "bspls2kpcm2k01",
        "serial_number": "BSPLS2KPCM2K01",
        "voltage": "DC 12V",
        "temp_range": "0~70",
        "burn_interface": ["UART"],
        "interface": ["以太网", "UART"],
        "config_description": "bspls2kpcm2k01 板卡，默认目标文件为 /media/hdd0/bspls2kpcm2k01.elf",
    },
]


LEGACY_BURNER_NAME_MAP = {
    "J-LINK V11": "J-LINK",
    "J_LINK V11": "J-LINK",
    "PWLINK V2": "PWLINK2",
    "PWLINK_2": "PWLINK2",
    "ST_LINK": "ST-LINK",
    "ST-LINK V2": "ST-LINK",
    "MPLAB ICD 3": "MPLAB ICD 3 DV164035",
    "TI XDS510 Plus": "XDS510plus",
}


LEGACY_SYSTEM_SCRIPT_NAME_MAP = {
    "jlink_v11_arm_mcu_flash": "jlink_v4_arm_mcu_flash",
    "gdlink_china_mcu_flash": "gdlink_arm_mcu_flash",
    "al321_china_mcu_flash": "al321_fpga_mcu_flash",
    "hdsc_ccid_china_mcu_flash": "hdsc_ccid_arm_mcu_flash",
}


def _build_product_from_catalog(item, product_id: Optional[int] = None):
    payload = dict(item)
    payload["burn_interface"] = json.dumps(payload.get("burn_interface") or [], ensure_ascii=False)
    payload["interface"] = json.dumps(payload.get("interface") or [], ensure_ascii=False)
    if product_id is not None:
        payload["id"] = product_id
    from backend.models.product import Product

    return Product(**payload)


ALLOWED_BURN_INTERFACES = ["SWD", "JTAG", "CJTAG", "UART", "ICSP"]


def _normalize_burn_interfaces(raw_value):
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        parsed = str(raw_value).split(",")
    if not isinstance(parsed, list):
        parsed = [parsed]

    alias_map = {
        "CJTAG": "CJTAG",
        "cJTAG": "CJTAG",
        "cjtag": "CJTAG",
        "JTAG": "JTAG",
        "jtag": "JTAG",
        "SWD": "SWD",
        "swd": "SWD",
        "UART": "UART",
        "uart": "UART",
        "ICSP": "ICSP",
        "icsp": "ICSP",
    }
    normalized = []
    for item in parsed:
        text = str(item or "").strip()
        mapped = alias_map.get(text)
        if mapped and mapped not in normalized:
            normalized.append(mapped)
    return normalized


DEFAULT_SYSTEM_SCRIPT_CATALOG = [
    {
        "name": "stlink_stm32_mcu_flash",
        "burner": "ST-LINK",
        "default_config": {
            "ide_name": "STM32CubeIDE",
            "interface_type": "SWD",
            "interface_type_label": "接口类型",
            "interface_type_options": ["SWD", "JTAG", "CJTAG"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "write_speed_khz": 900,
            "speed_label": "频率(kHz)",
            "speed_options": [125, 240, 480, 900, 1800, 4000],
            "start_address": "",
            "start_address_label": "起始地址",
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "仅复位", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "jlink_v4_arm_mcu_flash",
        "burner": "J-LINK",
        "default_config": {
            "ide_name": "Keil uVision",
            "interface_type": "SWD",
            "interface_type_label": "接口类型",
            "interface_type_options": ["SWD", "JTAG", "CJTAG"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "write_speed_khz": 1000,
            "speed_label": "频率(kHz)",
            "speed_options": [500, 1000, 2000, 4000, 5000, 10000],
            "start_address": "",
            "start_address_label": "起始地址",
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "仅复位", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "pwlink_v2_arm_mcu_flash",
        "burner": "PWLINK2",
        "default_config": {
            "ide_name": "Keil uVision",
            "interface_type": "SWD",
            "interface_type_label": "接口类型",
            "interface_type_options": ["SWD", "JTAG"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "write_speed_khz": 1000,
            "speed_label": "频率(kHz)",
            "speed_options": [500, 1000, 2000, 4000, 5000, 10000],
            "start_address": "",
            "start_address_label": "起始地址",
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "仅复位", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "gdlink_arm_mcu_flash",
        "burner": "GDLINK",
        "default_config": {
            "bichina_burn_mode": "单烧以上",
            "bichina_burn_mode_label": "Bichina烧录参数",
            "bichina_burn_mode_options": ["单烧以上"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "swd_downloader_arm_mcu_flash",
        "burner": "SWD下载器",
        "default_config": {
            "ide_name": "Keil uVision",
            "interface_type": "SWD",
            "interface_type_label": "接口类型",
            "interface_type_options": ["SWD", "JTAG", "CJTAG"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "write_speed_khz": 1000,
            "speed_label": "频率(kHz)",
            "speed_options": [500, 1000, 2000, 4000, 5000, 10000],
            "start_address": "",
            "start_address_label": "起始地址",
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "仅复位", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "al321_fpga_mcu_flash",
        "burner": "AL321",
        "default_config": {
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
            "erase_mode_options": ["默认自动擦除", "全片擦除", "扇区擦除", "不擦除"],
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_seconds": 1200,
        },
    },
    {
        "name": "stlink_stm32_mcu_flash",
        "burner": "ST-LINK",
        "default_config": {
            "ide_name": "STM32CubeIDE",
            "interface_type": "SWD",
            "interface_type_label": "接口类型",
            "interface_type_options": ["SWD", "JTAG", "CJTAG"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "write_speed_khz": 900,
            "speed_label": "频率(kHz)",
            "speed_options": [125, 240, 480, 900, 1800, 4000],
            "start_address": "",
            "start_address_label": "起始地址",
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "仅复位", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "hdsc_ccid_arm_mcu_flash",
        "burner": "HDSC CCID",
        "default_config": {
            "ide_name": "Keil uVision",
            "interface_type": "UART",
            "interface_type_label": "接口类型",
            "interface_type_options": ["UART"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "write_speed_khz": 115200,
            "speed_label": "波特率",
            "speed_options": [115200, 128000, 230400, 256000, 1000000],
            "start_address": "",
            "start_address_label": "起始地址",
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "仅复位", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "xds510plus_dsp_flash",
        "burner": "XDS510plus",
        "default_config": {
            "ide_name": "Code Composer Studio",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "target_config_file": "",
            "target_config_file_label": "目标配置文件",
            "target_config_file_required": True,
            "gel_init_script": "",
            "gel_init_script_label": "GEL 初始化脚本",
            "jtag_chain_index": 0,
            "jtag_chain_index_label": "JTAG链路序号",
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "required_fields": ["target_config_file"],
            "retry_count": 1,
            "timeout_seconds": 600,
        },
    },
    {
        "name": "mplab_icd3_pic_flash",
        "burner": "MPLAB ICD 3 DV164035",
        "default_config": {
            "ide_name": "MPLAB",
            "interface_type": "ICSP",
            "interface_type_label": "接口类型",
            "interface_type_options": ["ICSP"],
            "program_voltage": "3.3V",
            "program_voltage_label": "编程电压",
            "program_voltage_options": ["3.3V", "5V"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "仅擦除主存储器", "仅擦除辅助存储器"],
            "eeprom_write": "否",
            "eeprom_write_label": "EEPROM是否擦写",
            "eeprom_write_options": ["是", "否"],
            "write_config_bits": "是",
            "write_config_bits_label": "写入配置位",
            "write_config_bits_options": ["是", "否"],
            "completion_action": "编程复位后运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["编程复位后运行", "编程复位不运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "altera_blaster_ii_fpga_flash",
        "burner": "Altera Blaster II",
        "default_config": {
            "ide_name": "",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "erase_mode": "默认自动擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["默认自动擦除", "全片擦除", "扇区擦除", "不擦除"],
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "altera_blaster_ii_cpld_flash",
        "burner": "Altera Blaster II",
        "default_config": {
            "ide_name": "",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "pre_erase": "默认是",
            "pre_erase_label": "擦除器件",
            "pre_erase_options": ["默认是", "否"],
            "completion_action": "复位运行",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["复位运行", "不处理"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "gowin_usb_cable_fpga_flash",
        "burner": "Gowin USB Cable",
        "default_config": {
            "ide_name": "",
            "interface_type": "JTAG",
            "interface_type_label": "接口类型",
            "interface_type_options": ["JTAG"],
            "execution_operation": "SRAM下载",
            "execution_operation_label": "执行操作",
            "execution_operation_options": ["SRAM下载", "Flash固化"],
            "erase_mode": "全片擦除",
            "erase_mode_label": "擦除方式",
            "erase_mode_options": ["全片擦除", "扇区擦除"],
            "completion_action": "\u4e0d\u5904\u7406",
            "completion_action_label": "完成后动作",
            "completion_action_options": ["\u4e0d\u5904\u7406", "\u590d\u4f4d"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
    {
        "name": "sd_card_zynq7000_boot_update",
        "burner": "SD卡文件写入",
        "default_config": {
            "ide_name": "",
            "interface_type": "SD卡",
            "interface_type_options": ["SD卡"],
            "sd_target_path": "",
            "sd_target_path_label": "目标SD卡位置",
            "format_sd_card": "是",
            "format_sd_card_options": ["是", "否"],
            "completion_action": "自动弹出SD卡",
            "completion_action_options": ["自动弹出SD卡"],
            "options": ["local", "integrity", "writeVerify"],
            "retry_count": 1,
            "timeout_minutes": 120,
        },
    },
]


DEFAULT_SYSTEM_SCRIPT_BINDINGS = {
    "stlink_stm32_mcu_flash": {
        "ide_name": "STM32CubeIDE",
        "associated_ide": "STM32CubeIDE",
        "associated_board": "STM32F407VGT6开发板",
        "associated_burner": "ST-LINK",
    },
    "jlink_v4_arm_mcu_flash": {
        "ide_name": "Keil uVision",
        "associated_ide": "Keil uVision",
        "associated_board": "LPC55S69评估板,STM32F407VGT6开发板",
        "associated_burner": "J-LINK",
    },
    "pwlink_v2_arm_mcu_flash": {
        "ide_name": "Keil uVision",
        "associated_ide": "Keil uVision",
        "associated_board": "STM32F407VGT6开发板,LPC55S69评估板",
        "associated_burner": "PWLINK2",
    },
    "gdlink_arm_mcu_flash": {
        "ide_name": "Keil uVision",
        "associated_ide": "Keil uVision",
        "associated_board": "STM32F407VGT6开发板,LPC55S69评估板",
        "associated_burner": "GDLINK",
    },
    "swd_downloader_arm_mcu_flash": {
        "ide_name": "Keil uVision",
        "associated_ide": "Keil uVision",
        "associated_board": "STM32F407VGT6开发板,LPC55S69评估板",
        "associated_burner": "SWD下载器",
    },
    "al321_fpga_mcu_flash": {
        "ide_name": "Keil uVision",
        "associated_ide": "Keil uVision",
        "associated_board": "STM32F407VGT6开发板,LPC55S69评估板",
        "associated_burner": "AL321",
    },
    "hdsc_ccid_arm_mcu_flash": {
        "ide_name": "Keil uVision",
        "associated_ide": "Keil uVision",
        "associated_board": "STM32F407VGT6开发板,LPC55S69评估板",
        "associated_burner": "HDSC CCID",
    },
    "xds510plus_dsp_flash": {
        "ide_name": "Code Composer Studio",
        "associated_ide": "Code Composer Studio",
        "associated_board": "TI系列板卡,TMS320F28335,DSP",
        "associated_burner": "XDS510plus",
    },
    "mplab_icd3_pic_flash": {
        "ide_name": "MPLAB",
        "associated_ide": "MPLAB",
        "associated_board": "PIC32MZ开发板",
        "associated_burner": "MPLAB ICD 3 DV164035",
    },
    "altera_blaster_ii_fpga_flash": {
        "associated_board": "CycloneV FPGA板",
        "associated_burner": "Altera Blaster II",
    },
    "altera_blaster_ii_cpld_flash": {
        "associated_board": "EPM240T100开发板",
        "associated_burner": "Altera Blaster II",
    },
    "gowin_usb_cable_fpga_flash": {
        "associated_board": "Gowin FPGA",
        "associated_burner": "Gowin USB Cable",
    },
    "sd_card_zynq7000_boot_update": {
        "associated_board": "RK3568核心板",
        "associated_burner": "SD卡文件写入",
    },
}

from backend.utils.burner_automation import (
    SYSTEM_SCRIPT_BINDINGS as DEFAULT_AUTOMATION_SYSTEM_SCRIPT_BINDINGS,
    SYSTEM_SCRIPT_CATALOG as DEFAULT_AUTOMATION_SYSTEM_SCRIPT_CATALOG,
    TOOL_REQUIREMENTS as DEFAULT_BURNER_TOOL_REQUIREMENTS,
    build_system_script_content as _build_system_script_content,
)

DEFAULT_SYSTEM_SCRIPT_CATALOG = DEFAULT_AUTOMATION_SYSTEM_SCRIPT_CATALOG
DEFAULT_SYSTEM_SCRIPT_BINDINGS = DEFAULT_AUTOMATION_SYSTEM_SCRIPT_BINDINGS


def get_db():
    """获取数据库会话依赖"""
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


def _normalize_burner_name(value: str) -> str:
    return str(value or "").strip().lower()


def ensure_default_burners(db):
    """补齐默认烧录器清单，已存在的名称不重复插入"""
    from backend.models.burner import Burner

    existing_names = {
        str(name).strip().lower()
        for (name,) in db.query(Burner.name).all()
        if str(name or "").strip()
    }
    existing_types = {
        _normalize_burner_name(burner_type)
        for (burner_type,) in db.query(Burner.type).all()
        if str(burner_type or "").strip()
    }
    created = 0
    for item in DEFAULT_BURNER_CATALOG:
        burner_name = str(item["name"]).strip()
        burner_type = str(item.get("type") or burner_name).strip()
        if burner_name.lower() in existing_names or _normalize_burner_name(burner_type) in existing_types:
            continue
        burner = Burner(
            name=burner_name,
            type=burner_type,
            status=0,
            is_enabled=1,
            strategy=1,
            description=str(item.get("description") or ""),
            modified_by="system",
        )
        db.add(burner)
        existing_names.add(burner_name.lower())
        created += 1
    if created:
        db.commit()
        print(f"已补齐默认烧录器 {created} 个")


def _burner_has_runtime_binding(burner) -> bool:
    return any(
        str(getattr(burner, field, None) or "").strip()
        for field in ["sn", "port", "location", "host_name", "host_address", "agent_url", "config_json"]
    )


def sync_default_burners(db):
    """将数据库中的烧录器同步到默认清单，仅保留截图名单中的标准型号"""
    from backend.models.burner import Burner
    from backend.models.script import Script
    from backend.models.task import BurningTask

    desired_catalog = {
        _normalize_burner_name(item["name"]): item
        for item in DEFAULT_BURNER_CATALOG
    }
    desired_types = {
        _normalize_burner_name(str(item.get("type") or item["name"]))
        for item in DEFAULT_BURNER_CATALOG
    }

    changed = False
    removed = 0
    migrated = 0

    burners = db.query(Burner).all()
    burner_by_name = {_normalize_burner_name(item.name): item for item in burners}

    for legacy_name, canonical_name in LEGACY_BURNER_NAME_MAP.items():
        migrated_scripts = (
            db.query(Script)
            .filter(Script.associated_burner == legacy_name)
            .update({Script.associated_burner: canonical_name}, synchronize_session=False)
        )
        if migrated_scripts:
            changed = True

        legacy = burner_by_name.get(_normalize_burner_name(legacy_name))
        canonical = burner_by_name.get(_normalize_burner_name(canonical_name))
        if not legacy or not canonical or legacy.id == canonical.id:
            continue

        # 保留旧记录上的运行标识，避免用户已有配置被丢掉。
        for field in ["sn", "port", "location", "host_name", "host_address", "agent_url", "modified_by"]:
            if not getattr(canonical, field, None) and getattr(legacy, field, None):
                setattr(canonical, field, getattr(legacy, field))
                changed = True
        if int(getattr(legacy, "status", 0) or 0) and not int(getattr(canonical, "status", 0) or 0):
            canonical.status = legacy.status
            changed = True

        migrated_rows = (
            db.query(BurningTask)
            .filter(BurningTask.burner_id == legacy.id)
            .update({BurningTask.burner_id: canonical.id}, synchronize_session=False)
        )
        if migrated_rows:
            migrated += migrated_rows
            changed = True

        db.delete(legacy)
        removed += 1
        changed = True

    burners = db.query(Burner).all()
    for burner in burners:
        normalized_name = _normalize_burner_name(burner.name)
        catalog_item = desired_catalog.get(normalized_name)
        if catalog_item:
            expected_type = str(catalog_item.get("type") or burner.name)
            expected_description = str(catalog_item.get("description") or "")
            if burner.type != expected_type:
                burner.type = expected_type
                changed = True
            if (burner.description or "") != expected_description:
                burner.description = expected_description
                changed = True
            if int(getattr(burner, "is_enabled", 1) or 0) != 1:
                burner.is_enabled = 1
                changed = True
            continue

        migrated_rows = (
            db.query(BurningTask)
            .filter(BurningTask.burner_id == burner.id)
            .update({BurningTask.burner_id: None}, synchronize_session=False)
        )
        if migrated_rows:
            migrated += migrated_rows
        db.delete(burner)
        removed += 1
        changed = True

    if changed:
        db.commit()
        print(f"已同步默认烧录器，移除 {removed} 个，迁移任务 {migrated} 条")


def _build_legacy_system_script_content(script_name: str, burner_name: str) -> str:
    if script_name in ["jlink_v4_arm_mcu_flash", "jlink_v11_arm_mcu_flash"]:
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME=\"%s\"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-SWD}"
ERASE_MODE="${ERASE_MODE:-全片擦除}"
WRITE_SPEED_KHZ="${WRITE_SPEED_KHZ:-1000}"
START_ADDRESS="${START_ADDRESS:-}"
COMPLETION_ACTION="${COMPLETION_ACTION:-复位运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 擦除方式: ${ERASE_MODE}"
echo "[INFO] 烧录速度: ${WRITE_SPEED_KHZ} khz"
echo "[INFO] 起始地址: ${START_ADDRESS:-默认}"
echo "[INFO] 完成动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

JLINK_INTERFACE_CMD="si 1"
case "${INTERFACE_TYPE}" in
  SWD) JLINK_INTERFACE_CMD="si 1" ;;
  JTAG) JLINK_INTERFACE_CMD="si 0" ;;
  CJTAG) JLINK_INTERFACE_CMD="si 3" ;;
  *)
    echo "[ERROR] 不支持的接口类型: ${INTERFACE_TYPE}"
    exit 1
    ;;
esac

COMMAND_FILE="$(mktemp /tmp/jlink_cmd_XXXXXX.jlink)"
cleanup() {
  rm -f "${COMMAND_FILE}"
}
trap cleanup EXIT

{
  echo "${JLINK_INTERFACE_CMD}"
  echo "speed ${WRITE_SPEED_KHZ}"
  echo "connect"
  if [ "${ERASE_MODE}" = "全片擦除" ]; then
    echo "erase"
  fi
  if [ -n "${START_ADDRESS}" ]; then
    echo "loadfile ${FIRMWARE_PATH} ${START_ADDRESS}"
  else
    echo "loadfile ${FIRMWARE_PATH}"
  fi
  case "${COMPLETION_ACTION}" in
    "复位运行"|"复位后运行")
      echo "r"
      echo "g"
      ;;
    "仅复位")
      echo "r"
      ;;
    *)
      echo "r"
      echo "g"
      ;;
  esac
  echo "q"
} > "${COMMAND_FILE}"

echo "[EXEC] 已生成 J-Link 指令文件: ${COMMAND_FILE}"

if command -v JLinkExe >/dev/null 2>&1; then
  echo "[EXEC] 调用 JLinkExe 执行烧录..."
  JLinkExe -CommanderScript "${COMMAND_FILE}"
else
  echo "[WARN] 当前环境未安装 JLinkExe，输出模拟烧录日志用于联调。"
  cat "${COMMAND_FILE}"
  echo "[SIM] 模拟执行完成。"
fi

echo "[INFO] 系统脚本执行完成"
exit 0
""" % script_name
    if script_name in ["al321_fpga_mcu_flash", "al321_china_mcu_flash"]:
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME=\"%s\"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-JTAG}"
ERASE_MODE="${ERASE_MODE:-默认自动擦除}"
EXECUTION_OPERATION="${EXECUTION_OPERATION:-SRAM下载}"
COMPLETION_ACTION="${COMPLETION_ACTION:-复位运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 擦除方式: ${ERASE_MODE}"
echo "[INFO] 执行操作: ${EXECUTION_OPERATION}"
echo "[INFO] 完成动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 准备调用 AL321 FPGA 烧录流程..."
sleep 1
echo "[EXEC] 通过 ${INTERFACE_TYPE} 接口连接设备"
sleep 1
echo "[EXEC] 按 ${ERASE_MODE} 执行准备动作"
sleep 1
echo "[EXEC] 执行模式: ${EXECUTION_OPERATION}"
sleep 1
echo "[EXEC] 正在写入固件..."
sleep 1
echo "[EXEC] 完成后动作: ${COMPLETION_ACTION}"
sleep 1
echo "[INFO] 系统脚本执行完成"
exit 0
""" % script_name
    if script_name == "gdlink_arm_mcu_flash":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="gdlink_arm_mcu_flash"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
BICHINA_BURN_MODE="${BICHINA_BURN_MODE:-单烧以上}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] Bichina烧录参数: ${BICHINA_BURN_MODE}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 正在执行 GDLINK 烧录流程..."
sleep 1
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name in [
        "pwlink_v2_arm_mcu_flash",
        "swd_downloader_arm_mcu_flash",
        "hdsc_ccid_arm_mcu_flash"
    ]:
        return f"""#!/bin/bash
set -euo pipefail

SCRIPT_NAME="{script_name}"
FIRMWARE_PATH="${{FIRMWARE_PATH:-}}"
INTERFACE_TYPE="${{INTERFACE_TYPE:-SWD}}"
ERASE_MODE="${{ERASE_MODE:-全片擦除}}"
WRITE_SPEED_KHZ="${{WRITE_SPEED_KHZ:-1000}}"
START_ADDRESS="${{START_ADDRESS:-}}"
COMPLETION_ACTION="${{COMPLETION_ACTION:-复位运行}}"

echo "[INFO] 开始执行系统脚本: ${{SCRIPT_NAME}}"
echo "[INFO] 固件路径: ${{FIRMWARE_PATH:-未提供}}"
echo "[INFO] 接口类型: ${{INTERFACE_TYPE}}"
echo "[INFO] 擦除方式: ${{ERASE_MODE}}"
echo "[INFO] 烧录速度: ${{WRITE_SPEED_KHZ}} khz"
echo "[INFO] 起始地址: ${{START_ADDRESS:-默认}}"
echo "[INFO] 完成动作: ${{COMPLETION_ACTION}}"

if [ -z "${{FIRMWARE_PATH}}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${{FIRMWARE_PATH}}" ]; then
  echo "[ERROR] 固件文件不存在: ${{FIRMWARE_PATH}}"
  exit 1
fi

echo "[EXEC] 准备连接烧录器..."
sleep 1
echo "[EXEC] 接口类型: ${{INTERFACE_TYPE}}，速度: ${{WRITE_SPEED_KHZ}} khz"
echo "[EXEC] 擦除方式: ${{ERASE_MODE}}"
if [ -n "${{START_ADDRESS}}" ]; then
  echo "[EXEC] 使用起始地址: ${{START_ADDRESS}}"
fi
echo "[EXEC] 正在写入固件..."
sleep 1
echo "[EXEC] 写入完成，执行完成动作: ${{COMPLETION_ACTION}}"
sleep 1
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name == "xds510plus_dsp_flash":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="xds510plus_dsp_flash"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-JTAG}"
TARGET_CONFIG_FILE="${TARGET_CONFIG_FILE:-}"
GEL_INIT_SCRIPT="${GEL_INIT_SCRIPT:-}"
JTAG_CHAIN_INDEX="${JTAG_CHAIN_INDEX:-0}"
ERASE_MODE="${ERASE_MODE:-全片擦除}"
COMPLETION_ACTION="${COMPLETION_ACTION:-复位运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 目标配置文件: ${TARGET_CONFIG_FILE:-未提供}"
echo "[INFO] GEL 初始化脚本: ${GEL_INIT_SCRIPT:-未提供}"
echo "[INFO] JTAG 链路序号: ${JTAG_CHAIN_INDEX}"
echo "[INFO] 擦除方式: ${ERASE_MODE}"
echo "[INFO] 完成动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 准备调用 DSP 烧录流程..."
echo "[EXEC] 使用配置文件: ${TARGET_CONFIG_FILE}"
echo "[EXEC] 使用 GEL 脚本: ${GEL_INIT_SCRIPT}"
echo "[EXEC] 选择链路序号: ${JTAG_CHAIN_INDEX}"
sleep 1
echo "[EXEC] 正在加载程序..."
sleep 1
if [ "${COMPLETION_ACTION}" = "复位运行" ]; then
  echo "[EXEC] 程序加载完成，开始运行。"
else
  echo "[EXEC] 程序加载完成，保持当前状态。"
fi
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name == "mplab_icd3_pic_flash":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="mplab_icd3_pic_flash"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-JTAG}"
PROGRAM_VOLTAGE="${PROGRAM_VOLTAGE:-3.3V}"
ERASE_MODE="${ERASE_MODE:-全片擦除}"
EEPROM_WRITE="${EEPROM_WRITE:-否}"
WRITE_CONFIG_BITS="${WRITE_CONFIG_BITS:-是}"
COMPLETION_ACTION="${COMPLETION_ACTION:-编程复位后运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 编程电压: ${PROGRAM_VOLTAGE}"
echo "[INFO] 擦除方式: ${ERASE_MODE}"
echo "[INFO] EEPROM 是否烧写: ${EEPROM_WRITE}"
echo "[INFO] 写入配置位: ${WRITE_CONFIG_BITS}"
echo "[INFO] 完成后动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 准备调用 MPLAB ICD 3 烧录流程..."
echo "[EXEC] 使用电压 ${PROGRAM_VOLTAGE}，EEPROM=${EEPROM_WRITE}，配置位=${WRITE_CONFIG_BITS}"
sleep 1
echo "[EXEC] 正在擦除与写入程序..."
sleep 1
case "${COMPLETION_ACTION}" in
  "编程复位后运行")
    echo "[EXEC] 烧录完成，释放复位并运行。"
    ;;
  "编程复位不运行")
    echo "[EXEC] 烧录完成，执行复位。"
    ;;
  *)
    echo "[EXEC] 烧录完成，不做额外动作。"
    ;;
esac
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name == "altera_blaster_ii_fpga_flash":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="altera_blaster_ii_fpga_flash"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-JTAG}"
ERASE_MODE="${ERASE_MODE:-默认自动擦除}"
COMPLETION_ACTION="${COMPLETION_ACTION:-复位运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 擦除方式: ${ERASE_MODE}"
echo "[INFO] 完成后动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 准备调用 Altera Blaster II FPGA 烧录流程..."
echo "[EXEC] 接口=${INTERFACE_TYPE}"
sleep 1
echo "[EXEC] 正在执行 FPGA 烧录..."
sleep 1
echo "[EXEC] 完成后动作: ${COMPLETION_ACTION}"
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name == "altera_blaster_ii_cpld_flash":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="altera_blaster_ii_cpld_flash"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-JTAG}"
PRE_ERASE="${PRE_ERASE:-默认是}"
COMPLETION_ACTION="${COMPLETION_ACTION:-复位运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 擦除器件: ${PRE_ERASE}"
echo "[INFO] 完成后动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 准备调用 Altera Blaster II CPLD 烧录流程..."
echo "[EXEC] 接口=${INTERFACE_TYPE}，擦除器件=${PRE_ERASE}"
sleep 1
echo "[EXEC] 正在执行 CPLD 烧录..."
sleep 1
echo "[EXEC] 完成后动作: ${COMPLETION_ACTION}"
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name == "gowin_usb_cable_fpga_flash":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="gowin_usb_cable_fpga_flash"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
INTERFACE_TYPE="${INTERFACE_TYPE:-JTAG}"
EXECUTION_OPERATION="${EXECUTION_OPERATION:-SRAM下载(仅编程，掉电丢失)}"
ERASE_MODE="${ERASE_MODE:-全片擦除}"
COMPLETION_ACTION="${COMPLETION_ACTION:-复位运行}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 接口类型: ${INTERFACE_TYPE}"
echo "[INFO] 执行操作: ${EXECUTION_OPERATION}"
echo "[INFO] 擦除方式: ${ERASE_MODE}"
echo "[INFO] 完成后动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

echo "[EXEC] 准备调用 Gowin USB Cable FPGA 烧录流程..."
echo "[EXEC] 操作=${EXECUTION_OPERATION}，擦除方式=${ERASE_MODE}"
sleep 1
if [ "${EXECUTION_OPERATION}" = "Flash固化" ]; then
  echo "[EXEC] 正在执行 Flash 固化..."
else
  echo "[EXEC] 正在执行 SRAM 下载..."
fi
sleep 1
echo "[EXEC] 完成后动作: ${COMPLETION_ACTION}"
echo "[INFO] 系统脚本执行完成"
exit 0
"""
    if script_name == "sd_card_zynq7000_boot_update":
        return """#!/bin/bash
set -euo pipefail

SCRIPT_NAME="sd_card_zynq7000_boot_update"
FIRMWARE_PATH="${FIRMWARE_PATH:-}"
SD_TARGET_PATH="${SD_TARGET_PATH:-}"
FORMAT_SD_CARD="${FORMAT_SD_CARD:-是}"
COMPLETION_ACTION="${COMPLETION_ACTION:-自动弹出SD卡}"

echo "[INFO] 开始执行系统脚本: ${SCRIPT_NAME}"
echo "[INFO] 固件路径: ${FIRMWARE_PATH:-未提供}"
echo "[INFO] 目标 SD 卡位置: ${SD_TARGET_PATH:-未提供}"
echo "[INFO] 拷贝前格式化 SD 卡: ${FORMAT_SD_CARD}"
echo "[INFO] 完成后动作: ${COMPLETION_ACTION}"

if [ -z "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 未提供固件路径，请检查任务配置中的 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

if [ -z "${SD_TARGET_PATH}" ]; then
  echo "[ERROR] 未提供 SD 卡目标位置"
  exit 1
fi

if [ "${FORMAT_SD_CARD}" = "是" ]; then
  echo "[EXEC] 正在格式化 SD 卡: ${SD_TARGET_PATH}"
  sleep 1
fi

echo "[EXEC] 正在拷贝文件到 SD 卡..."
sleep 1
echo "[EXEC] 文件拷贝完成"

if [ "${COMPLETION_ACTION}" = "自动弹出SD卡" ]; then
  echo "[EXEC] 正在弹出 SD 卡..."
  sleep 1
fi

echo "[INFO] 系统脚本执行完成"
exit 0
"""
    return f"""#!/bin/bash
# 系统内置脚本：{script_name}
echo "[INFO] 开始执行系统脚本: {script_name}"
echo "[INFO] 关联烧录器: {burner_name}"
echo "[INFO] 固件路径: ${{FIRMWARE_PATH:-未提供}}"
echo "[INFO] 目标板卡: ${{BOARD_NAME:-未提供}}"
echo "[INFO] 接口类型: ${{INTERFACE_TYPE:-未提供}}"
sleep 1
echo "[EXEC] 正在执行预设烧录流程..."
sleep 1
echo "[INFO] 系统脚本执行完成"
exit 0
"""


def ensure_default_system_scripts(db):
    """补齐系统级烧录脚本，已存在同名脚本时更新为系统脚本并同步关联烧录器"""
    from backend.models.script import Script

    desired_script_names = {item["name"] for item in DEFAULT_SYSTEM_SCRIPT_CATALOG}
    preferred_script_types = {
        item["name"]: str(item.get("type") or "bat").strip() or "bat"
        for item in DEFAULT_SYSTEM_SCRIPT_CATALOG
    }
    for legacy_name, canonical_name in LEGACY_SYSTEM_SCRIPT_NAME_MAP.items():
        legacy_script = db.query(Script).filter(Script.name == legacy_name, Script.is_system == 1).first()
        if legacy_script and not db.query(Script).filter(Script.name == canonical_name).first():
            legacy_script.name = canonical_name

    deleted = 0
    obsolete_scripts = (
        db.query(Script)
        .filter(Script.is_system == 1)
        .all()
    )
    for script in obsolete_scripts:
        if str(getattr(script, "name", "") or "").strip() not in desired_script_names:
            db.delete(script)
            deleted += 1

    scripts_by_name: dict[str, list[Script]] = {}
    for script in db.query(Script).filter(Script.is_system == 1).all():
        script_name = str(getattr(script, "name", "") or "").strip()
        if script_name in desired_script_names:
            scripts_by_name.setdefault(script_name, []).append(script)
    for script_name, same_name_scripts in scripts_by_name.items():
        if len(same_name_scripts) <= 1:
            continue
        preferred_type = preferred_script_types.get(script_name, "bat")
        same_name_scripts.sort(
            key=lambda script: (
                str(getattr(script, "type", "") or "").strip() != preferred_type,
                int(getattr(script, "id", 0) or 0),
            )
        )
        for duplicate_script in same_name_scripts[1:]:
            db.delete(duplicate_script)
            deleted += 1

    created = 0
    updated = 0
    for item in DEFAULT_SYSTEM_SCRIPT_CATALOG:
        script_name = item["name"]
        burner_name = item["burner"]
        script_type = str(item.get("type") or "bat").strip() or "bat"
        task_type = str(item.get("task_type") or "board").strip().lower() or "board"
        binding = DEFAULT_SYSTEM_SCRIPT_BINDINGS.get(script_name, {})
        default_config = dict(item.get("default_config") or {})
        if binding.get("ide_name") and not str(default_config.get("ide_name") or "").strip():
            default_config["ide_name"] = binding["ide_name"]
        default_config_json = json.dumps(default_config, ensure_ascii=False)
        script_content = _build_system_script_content(script_name, burner_name)
        script = db.query(Script).filter(Script.name == script_name).first()
        if script:
            changed = False
            if getattr(script, "associated_burner", None) != burner_name:
                script.associated_burner = burner_name
                changed = True
            # 关联板卡 / 关联 IDE 一旦被用户改过，初始化不能再强行覆盖。
            # 仅在脚本刚创建（newly created 分支）时写入默认值；存在分支只补空字段，
            # 不覆盖用户实际维护过的值。
            for field_name in ["ide_name", "associated_ide", "associated_board"]:
                field_value = str(binding.get(field_name) or "").strip()
                if not field_value:
                    continue
                current = str(getattr(script, field_name, "") or "").strip()
                if not current:
                    setattr(script, field_name, field_value)
                    changed = True
            if int(getattr(script, "is_system", 0) or 0) != 1:
                script.is_system = 1
                changed = True
            if not getattr(script, "modified_by", None):
                script.modified_by = "system"
                changed = True
            if getattr(script, "type", None) != script_type:
                script.type = script_type
                changed = True
            if getattr(script, "task_type", None) != task_type:
                script.task_type = task_type
                changed = True
            if getattr(script, "content", None) != script_content:
                script.content = script_content
                changed = True
            if getattr(script, "default_config_json", None) != default_config_json:
                script.default_config_json = default_config_json
                changed = True
            if changed:
                updated += 1
            continue

        db.add(
            Script(
                name=script_name,
                type=script_type,
                content=_build_system_script_content(script_name, burner_name),
                associated_burner=burner_name,
                associated_board=str(binding.get("associated_board") or "") or None,
                associated_ide=str(binding.get("associated_ide") or "") or None,
                ide_name=str(binding.get("ide_name") or "") or None,
                task_type=task_type,
                status=0,
                is_system=1,
                description="系统内置烧录脚本，由系统初始化维护，用户不可编辑或删除。",
                default_config_json=default_config_json,
                modified_by="system",
            )
        )
        created += 1

    if created or updated or deleted:
        db.commit()
        print(f"已补齐系统脚本 {created} 个，更新 {updated} 个，清理 {deleted} 个")

    hybrid_script_name = "通用混合协同执行脚本"
    hybrid_script = db.query(Script).filter(Script.name == hybrid_script_name).first()
    hybrid_script_content = """#!/bin/sh
set -eu

echo "[INFO] 混合协同脚本开始执行"
echo "[INFO] 制品路径: ${FIRMWARE_PATH:-}"
echo "[INFO] 目标目录: ${TARGET_PATH:-}"
echo "[INFO] 制品文件名: ${REMOTE_ARTIFACT_NAME:-}"

if [ -z "${FIRMWARE_PATH:-}" ]; then
  echo "[ERROR] 未收到 FIRMWARE_PATH"
  exit 1
fi

if [ ! -f "${FIRMWARE_PATH}" ]; then
  echo "[ERROR] 制品文件不存在: ${FIRMWARE_PATH}"
  exit 1
fi

# TODO: 在这里替换为现场真实烧录/安装命令。
# 示例：
# chmod +x "${FIRMWARE_PATH}"
# "${FIRMWARE_PATH}" --install-dir "${TARGET_PATH:-/opt/control-app}"

echo "[INFO] 文件已下发，默认模板未执行额外烧录命令"
echo "[INFO] 如需真正烧录，请在脚本管理中编辑本脚本内容"
exit 0
"""
    hybrid_default_config = json.dumps(
        {
            "options": ["integrity"],
            "retry_count": 1,
            "timeout_seconds": 120,
        },
        ensure_ascii=False,
    )
    if not hybrid_script:
        db.add(
            Script(
                name=hybrid_script_name,
                type="shell",
                content=hybrid_script_content,
                associated_burner=None,
                associated_board=None,
                associated_ide=None,
                ide_name=None,
                task_type="hybrid",
                status=0,
                is_system=0,
                description="混合协同默认模板：文件下发后通过串口执行。可在脚本管理中改成现场真实命令。",
                default_config_json=hybrid_default_config,
                modified_by="system",
            )
        )
        db.commit()


DEFAULT_MENU_DEFINITIONS = [
    {"id": 1, "name": "工作台", "path": "/workbench", "icon": "DesktopOutlined", "parent_id": None, "sort_order": 1},
    {"id": 2, "name": "制品仓库", "path": "/repository", "icon": "DatabaseOutlined", "parent_id": None, "sort_order": 2},
    {"id": 4, "name": "烧录安装管理", "path": "/burning", "icon": "FireOutlined", "parent_id": None, "sort_order": 3},
    {"id": 5, "name": "履历记录", "path": "/record", "icon": "FileTextOutlined", "parent_id": None, "sort_order": 4},
    {"id": 3, "name": "资产管理", "path": "", "icon": "InboxOutlined", "parent_id": None, "sort_order": 5},
    {"id": 6, "name": "异常注入", "path": "/injection", "icon": "BugOutlined", "parent_id": None, "sort_order": 6},
    {"id": 7, "name": "通信协议验证", "path": "/protocol", "icon": "WifiOutlined", "parent_id": None, "sort_order": 7},
    {"id": 8, "name": "系统管理", "path": "", "icon": "SettingOutlined", "parent_id": None, "sort_order": 8},
    {"id": 31, "name": "产品管理", "path": "/product", "icon": "CodeOutlined", "parent_id": 3, "sort_order": 1},
    {"id": 32, "name": "设备管理", "path": "/burner", "icon": "FireOutlined", "parent_id": 3, "sort_order": 3},
    {"id": 33, "name": "脚本管理", "path": "/script", "icon": "FileProtectOutlined", "parent_id": 3, "sort_order": 4},
    {"id": 81, "name": "用户管理", "path": "/user", "icon": "TeamOutlined", "parent_id": 8, "sort_order": 1},
    {"id": 82, "name": "角色管理", "path": "/role", "icon": "TeamOutlined", "parent_id": 8, "sort_order": 2},
    {"id": 84, "name": "登录日志", "path": "/log/login", "icon": "BarChartOutlined", "parent_id": 8, "sort_order": 4},
    {"id": 85, "name": "操作日志", "path": "/log/operation", "icon": "FileTextOutlined", "parent_id": 8, "sort_order": 5},
]

DEFAULT_ACTION_PERMISSION_DEFINITIONS = [
    {"name": "新建项目", "code": "repository:add", "type": "button", "menu_id": 2},
    {"name": "删除项目", "code": "repository:delete", "type": "button", "menu_id": 2},
    {"name": "下载制品", "code": "repository:download", "type": "button", "menu_id": 2},
    {"name": "项目成员及权限", "code": "repository:perm_change", "type": "button", "menu_id": 2},
    {"name": "新增产品", "code": "product:add", "type": "button", "menu_id": 31},
    {"name": "编辑产品", "code": "product:edit", "type": "button", "menu_id": 31},
    {"name": "删除产品", "code": "product:delete", "type": "button", "menu_id": 31},
    {"name": "新增设备", "code": "burner:add", "type": "button", "menu_id": 32},
    {"name": "编辑设备", "code": "burner:edit", "type": "button", "menu_id": 32},
    {"name": "删除设备", "code": "burner:delete", "type": "button", "menu_id": 32},
    {"name": "新增脚本", "code": "script:add", "type": "button", "menu_id": 33},
    {"name": "编辑脚本", "code": "script:edit", "type": "button", "menu_id": 33},
    {"name": "删除脚本", "code": "script:delete", "type": "button", "menu_id": 33},
    {"name": "创建任务", "code": "burning:add", "type": "button", "menu_id": 4},
    {"name": "终止任务", "code": "burning:terminate", "type": "button", "menu_id": 4},
    {"name": "查看一致性报告", "code": "burning:report", "type": "button", "menu_id": 4},
    {"name": "强制覆盖执行", "code": "burning:override", "type": "button", "menu_id": 4},
    {"name": "删除任务", "code": "burning:delete", "type": "button", "menu_id": 4},
    {"name": "新增注入任务", "code": "injection:add", "type": "button", "menu_id": 6},
    {"name": "删除注入任务", "code": "injection:delete", "type": "button", "menu_id": 6},
    {"name": "执行通信协议验证", "code": "protocol:execute", "type": "button", "menu_id": 7},
    {"name": "删除协议验证记录", "code": "protocol:delete", "type": "button", "menu_id": 7},
    {"name": "新增用户", "code": "user:add", "type": "button", "menu_id": 81},
    {"name": "编辑用户", "code": "user:edit", "type": "button", "menu_id": 81},
    {"name": "删除用户", "code": "user:delete", "type": "button", "menu_id": 81},
    {"name": "新增角色", "code": "role:add", "type": "button", "menu_id": 82},
    {"name": "编辑角色", "code": "role:edit", "type": "button", "menu_id": 82},
    {"name": "删除角色", "code": "role:delete", "type": "button", "menu_id": 82},
    {"name": "清空登录日志", "code": "log/login:clear", "type": "button", "menu_id": 84},
    {"name": "清空操作日志", "code": "log/operation:clear", "type": "button", "menu_id": 85},
]

VIEW_PERMISSION_NAME_OVERRIDES = {
    4: "烧录安装管理任务历史",
    6: "执行记录",
    7: "执行记录",
}

LEGACY_PERMISSION_CODE_MIGRATIONS = {
    "log:view": {
        "primary": {"code": "log/login:view", "name": "登录日志查看", "type": "menu", "menu_id": 84},
        "extra_codes": ["log/operation:view"],
    },
    "log:clear": {
        "primary": {"code": "log/login:clear", "name": "清空登录日志", "type": "button", "menu_id": 84},
        "extra_codes": ["log/operation:clear"],
    },
}


def _build_menu_view_permission_definitions():
    permissions = []
    for menu in DEFAULT_MENU_DEFINITIONS:
        path = str(menu.get("path") or "").strip()
        if not path:
            continue
        path_key = path.lstrip("/")
        permissions.append(
            {
                "name": VIEW_PERMISSION_NAME_OVERRIDES.get(menu["id"], f"{menu['name']}查看"),
                "code": f"{path_key}:view",
                "type": "menu",
                "menu_id": menu["id"],
            }
        )
    return permissions


def _all_default_permission_definitions():
    return _build_menu_view_permission_definitions() + list(DEFAULT_ACTION_PERMISSION_DEFINITIONS)


def init_menus_and_permissions(db):
    """初始化并补齐菜单和权限数据"""
    from backend.models.permission import Menu, Permission, RolePermission
    from backend.models.role import Role

    print("正在初始化菜单和权限...")

    changed = False
    created_menu_count = 0
    created_permission_count = 0
    admin_assignment_count = 0

    for menu_data in DEFAULT_MENU_DEFINITIONS:
        menu = db.query(Menu).filter(Menu.id == menu_data["id"]).first()
        if not menu:
            db.add(Menu(**menu_data))
            created_menu_count += 1
            changed = True
            continue
        for field, value in menu_data.items():
            if getattr(menu, field) != value:
                setattr(menu, field, value)
                changed = True

    db.flush()

    legacy_role_assignments: dict[str, list[int]] = {}
    for legacy_code, migration in LEGACY_PERMISSION_CODE_MIGRATIONS.items():
        legacy_permission = db.query(Permission).filter(Permission.code == legacy_code).first()
        if not legacy_permission:
            continue
        role_ids = [
            row.role_id
            for row in db.query(RolePermission).filter(RolePermission.permission_id == legacy_permission.id).all()
        ]
        legacy_role_assignments[legacy_code] = role_ids
        primary = migration["primary"]
        for field, value in primary.items():
            if getattr(legacy_permission, field) != value:
                setattr(legacy_permission, field, value)
                changed = True

    # Flush legacy code renames first so later default-code lookups can see the updated codes
    # and avoid inserting duplicate permission rows for the migrated entries.
    db.flush()

    for permission_data in _all_default_permission_definitions():
        permission = db.query(Permission).filter(Permission.code == permission_data["code"]).first()
        if not permission:
            db.add(Permission(**permission_data))
            created_permission_count += 1
            changed = True
            continue
        for field, value in permission_data.items():
            if getattr(permission, field) != value:
                setattr(permission, field, value)
                changed = True

    db.flush()

    for legacy_code, migration in LEGACY_PERMISSION_CODE_MIGRATIONS.items():
        role_ids = legacy_role_assignments.get(legacy_code) or []
        if not role_ids:
            continue
        for extra_code in migration.get("extra_codes") or []:
            target_permission = db.query(Permission).filter(Permission.code == extra_code).first()
            if not target_permission:
                continue
            for role_id in role_ids:
                exists = (
                    db.query(RolePermission)
                    .filter(
                        RolePermission.role_id == role_id,
                        RolePermission.permission_id == target_permission.id,
                    )
                    .first()
                )
                if exists:
                    continue
                db.add(RolePermission(role_id=role_id, permission_id=target_permission.id))
                admin_assignment_count += 1
                changed = True

    admin_role = db.query(Role).filter(Role.name == "管理员").first()
    if admin_role:
        all_permission_ids = {perm.id for perm in db.query(Permission.id).all()}
        existing_permission_ids = {
            row.permission_id
            for row in db.query(RolePermission).filter(RolePermission.role_id == admin_role.id).all()
        }
        for permission_id in sorted(all_permission_ids - existing_permission_ids):
            db.add(RolePermission(role_id=admin_role.id, permission_id=permission_id))
            admin_assignment_count += 1
            changed = True

    burning_add_permission = db.query(Permission).filter(Permission.code == "burning:add").first()
    burning_extra_permissions = (
        db.query(Permission)
        .filter(Permission.code.in_(["burning:terminate", "burning:report", "burning:override"]))
        .all()
    )
    if burning_add_permission and burning_extra_permissions:
        role_ids = {
            row.role_id
            for row in db.query(RolePermission).filter(RolePermission.permission_id == burning_add_permission.id).all()
        }
        for role_id in sorted(role_ids):
            existing_permission_ids = {
                row.permission_id
                for row in db.query(RolePermission).filter(RolePermission.role_id == role_id).all()
            }
            for permission in burning_extra_permissions:
                if permission.id in existing_permission_ids:
                    continue
                db.add(RolePermission(role_id=role_id, permission_id=permission.id))
                admin_assignment_count += 1
                changed = True

    # Prune obsolete permission codes that are no longer backed by the
    # frontend and removed from DEFAULT_ACTION_PERMISSION_DEFINITIONS. This
    # avoids the menu displaying buttons that have no implementation behind
    # them (e.g. "导出登录日志" on a page that has no export action).
    obsolete_codes = {
        "script:execute",
        "repository:edit",
        "repository:sync",
        "repository:invite",
        "burner:scan",
        "burning:execute",
        "burning:edit",
        "protocol:add",
        "protocol:export",
        "log/login:export",
        "log/operation:export",
        "record:export",
        "record:delete",
        "injection:execute",
        "injection:detail",
        "user:reset_pwd",
        "role:assign",
    }
    obsolete_permissions = (
        db.query(Permission)
        .filter(Permission.code.in_(sorted(obsolete_codes)))
        .all()
    )
    for obsolete in obsolete_permissions:
        db.query(RolePermission).filter(RolePermission.permission_id == obsolete.id).delete()
        db.delete(obsolete)
        changed = True

    if changed:
        db.commit()
        print(
            f"菜单和权限已补齐：新增菜单 {created_menu_count} 个，新增权限 {created_permission_count} 个，新增角色权限关联 {admin_assignment_count} 条"
        )
    else:
        db.rollback()

    print("菜单和权限初始化完成")


DEFAULT_ROLE_PERMISSION_CODES = {
    "操作员": {
        "workbench:view",
        "repository:view",
        "repository:download",
        "product:view",
        "burner:view",
        "script:view",
        "burning:view",
        "burning:add",
        "burning:terminate",
        "burning:report",
        "burning:override",
        "record:view",
        "injection:view",
        "injection:add",
        "protocol:view",
        "protocol:execute",
    },
    "观察员": {
        "workbench:view",
        "repository:view",
        "product:view",
        "burner:view",
        "script:view",
        "burning:view",
        "record:view",
        "injection:view",
        "protocol:view",
    },
}


def ensure_default_role_permissions(db):
    """Assign usable baseline permissions to the built-in non-admin roles."""
    from backend.models.permission import Permission, RolePermission
    from backend.models.role import Role

    changed = False
    for role_name, permission_codes in DEFAULT_ROLE_PERMISSION_CODES.items():
        role = db.query(Role).filter(Role.name == role_name).first()
        if not role:
            continue
        permissions = db.query(Permission).filter(Permission.code.in_(sorted(permission_codes))).all()
        existing_ids = {
            row.permission_id
            for row in db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        }
        if existing_ids:
            continue
        for permission in permissions:
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))
            changed = True
    if changed:
        db.commit()


def sync_historical_task_durations(db):
    """Replace legacy random log durations with durations derived from task timestamps."""
    from backend.models.task import BurningTask

    changed = False
    tasks = (
        db.query(BurningTask)
        .filter(
            BurningTask.started_at.isnot(None),
            BurningTask.finished_at.isnot(None),
            BurningTask.result.isnot(None),
        )
        .all()
    )
    for task in tasks:
        result = str(task.result or "")
        if not re.search(r"总耗时\s*\d+\s*秒", result):
            continue
        duration = max(int((task.finished_at - task.started_at).total_seconds()), 0)
        corrected = re.sub(r"总耗时\s*\d+\s*秒", f"总耗时 {duration} 秒", result, count=1)
        if corrected != result:
            task.result = corrected
            changed = True
    if changed:
        db.commit()


def migrate_legacy_task_terminated_statuses(db):
    """把历史上 status=0 且已结束的终止任务迁移到显式终止状态。"""
    from backend.models.task import BurningTask, TaskStatus

    changed = False
    tasks = db.query(BurningTask).filter(BurningTask.status == int(TaskStatus.PENDING)).all()
    for task in tasks:
        last_error = str(getattr(task, "last_error", None) or "").strip()
        result = str(getattr(task, "result", None) or "").strip()
        looks_terminated = (
            getattr(task, "started_at", None)
            or getattr(task, "finished_at", None)
            or "终止" in last_error
            or "终止" in result
        )
        if not looks_terminated:
            continue
        task.status = int(TaskStatus.TERMINATED)
        if not getattr(task, "finished_at", None):
            task.finished_at = datetime.utcnow()
        if not last_error:
            task.last_error = "任务已终止"
        if "终止" not in result:
            task.result = result or "任务已由用户手动终止。"
        changed = True
    if changed:
        db.commit()


def sync_device_menu_labels(db):
    from backend.models.permission import Menu, Permission

    changed = False
    menu = db.query(Menu).filter(Menu.id == 32).first()
    if menu and (menu.name or "") != "设备管理":
        menu.name = "设备管理"
        changed = True

    code_name_pairs = [
        ("burner:view", "设备管理查看"),
        ("burner:add", "新增设备"),
        ("burner:edit", "编辑设备"),
        ("burner:delete", "删除设备"),
        ("burner:scan", "扫描设备"),
    ]
    for code, name in code_name_pairs:
        perm = db.query(Permission).filter(Permission.code == code).first()
        if perm and (perm.name or "") != name:
            perm.name = name
            changed = True

    if changed:
        db.commit()
        print("已同步设备管理菜单与权限名称")


def seed_mock_data():
    """填充模拟测试数据（开发环境）"""
    db = SessionLocal()
    try:
        from backend.models.product import Product
        from backend.models.burner import Burner
        from backend.models.script import Script
        from backend.models.task import BurningTask
        from backend.models.log import Record, Injection, ProtocolTest, LoginLog, OperationLog
        from backend.models.repository import Repository
        from backend.models.user import User
        from datetime import datetime, timedelta

        # 检查是否已填充
        if db.query(Product).count() > 0:
            return

        print("正在填充模拟测试数据...")
        now = datetime.utcnow()

        # Products (芯片/板卡)
        products = [
            _build_product_from_catalog(item, product_id=index)
            for index, item in enumerate(DEFAULT_PRODUCT_CATALOG, start=1)
        ]
        for p in products:
            p.created_at = now - timedelta(days=30)
            p.updated_at = now - timedelta(days=1)
            db.add(p)
        db.commit()

        # Burners (烧录器)
        burners = [
            Burner(name="J-LINK", type="JTAG", sn="123FAS064E573436F2FC1003", port="USB", status=1, description="Segger J-LINK，支持ARM Cortex系列"),
            Burner(name="ST-LINK", type="ST-LINK", sn="QQFA71064E573436F2FC1WEQ", port="USB", status=1, description="ST-LINK 系列烧录器"),
            Burner(name="MPLAB ICD 3 DV164035", type="MPLAB ICD 3 DV164035", sn="B3FA71064E573436F2FC1ABC", port="USB", status=0, description="Microchip MPLAB ICD 3 调试器"),
            Burner(name="XDS510plus", type="XDS510plus", sn="C4FA71064E573436F2FC1DEF", port="USB", status=1, description="TI XDS510plus 仿真器"),
            Burner(name="PWLINK2", type="SWD", sn="D5FA71064E573436F2FC1GHI", port="USB", status=0, description="适合PIC系列烧录"),
            Burner(name="GDLINK", type="SWD", sn="E6FA71064E573436F2FC1JKL", port="USB", status=1, description="国产GD-Link，支持GD32系列"),
            Burner(name="Altera Blaster II", type="JTAG", sn="F7FA71064E573436F2FC1MNO", port="USB", status=0, description="Intel/Altera FPGA调试器"),
            Burner(name="Gowin USB Cable", type="JTAG", sn="G8FA71064E573436F2FC1PQR", port="USB", status=1, description="高云FPGA下载器"),
            Burner(name="SD卡文件写入", type="SD卡文件写入", sn="H9FA71064E573436F2FC1SDC", port="USB", status=1, description="SD卡离线文件写入设备"),
        ]
        for b in burners:
            b.created_at = now - timedelta(days=25)
            b.updated_at = now - timedelta(days=2)
            db.add(b)
        db.commit()

        # Scripts (脚本)
        scripts = [
            Script(
                name="STLINK_Keil_STM32板卡脚本", 
                type="shell", 
                content='''#!/bin/bash
# ST-LINK CLI 自动化烧录脚本 (STM32)
echo "[INFO] 开始执行 STM32 烧录流程..."
echo "[INFO] 目标固件: ${FIRMWARE_PATH}"
echo "[INFO] 烧录地址: 0x08000000"

# 检查固件是否存在
if [ ! -f "${FIRMWARE_PATH}" ]; then
    echo "[ERROR] 固件文件不存在: ${FIRMWARE_PATH}"
    exit 1
fi

echo "[EXEC] 连接 ST-LINK..."
# 模拟执行: ST-LINK_CLI.exe -c SWD -ME
sleep 1
echo "连接成功。正在擦除全片..."

# 模拟执行: ST-LINK_CLI.exe -c SWD -P "${FIRMWARE_PATH}" 0x08000000 -V
sleep 2
echo "[EXEC] 正在烧录固件到 0x08000000..."
sleep 3
echo "烧录进度: 100%"

echo "[EXEC] 正在校验固件..."
sleep 1
echo "校验通过。"

# 模拟执行: ST-LINK_CLI.exe -c SWD -Rst
echo "[EXEC] 正在复位目标板卡..."
sleep 1
echo "[INFO] 烧录成功完成！"
exit 0''', 
                ide_name="Keil", 
                associated_board="STM32F407VGT6开发板", 
                associated_burner="ST-LINK"
            ),
            Script(
                name="JLINK_ARM_Flash", 
                type="shell", 
                content='''#!/bin/bash
# J-Link Commander 自动化烧录脚本
echo "[INFO] 开始 J-Link 烧录流程"
echo "[INFO] 目标固件: ${FIRMWARE_PATH}"

# 生成 J-Link 命令行指令文件
COMMAND_FILE="jlink_cmds.jlink"
cat << EOF > ${COMMAND_FILE}
device Cortex-M4
si 1
speed 4000
connect
erase
loadfile ${FIRMWARE_PATH}
r
g
q
EOF

echo "[EXEC] 启动 JLinkExe..."
# 模拟执行: JLinkExe -CommanderScript ${COMMAND_FILE}
sleep 1
echo "Connecting to J-Link via USB... O.K."
echo "Firmware: J-Link V11 compiled..."
sleep 1
echo "Erasing device... O.K."
sleep 2
echo "Downloading file [${FIRMWARE_PATH}]... O.K."
echo "Resetting target... O.K."
sleep 1

rm -f ${COMMAND_FILE}
echo "[INFO] J-Link 烧录完成！"
exit 0''', 
                ide_name="Keil", 
                associated_board="通用ARM Cortex-M系列", 
                associated_burner="J-LINK"
            ),
            Script(
                name="CCS_DSP_DSLite", 
                type="python", 
                content='''import sys
import time
import os

# Code Composer Studio DSLite 自动化烧录脚本 (DSP/TI板卡)
def main():
    firmware_path = os.environ.get("FIRMWARE_PATH", "unknown.out")
    print("[INFO] 开始执行 TI DSP 烧录流程 (DSLite)")
    print(f"[INFO] 目标固件: {firmware_path}")
    
    if firmware_path == "unknown.out":
        print("[ERROR] 未提供 FIRMWARE_PATH 环境变量")
        sys.exit(1)

    print("[EXEC] 初始化 Debug Server...")
    time.sleep(1.5)
    print("Loading target configuration: target.ccxml")
    
    print("[EXEC] 连接目标板卡 (XDS510plus)...")
    time.sleep(1)
    print("Connect successful.")
    
    print(f"[EXEC] 加载程序: {firmware_path} ...")
    time.sleep(3)
    print("Program load complete.")
    
    print("[EXEC] 运行程序...")
    time.sleep(1)
    print("Target running.")
    print("[INFO] DSP 烧录成功完成！")

if __name__ == "__main__":
    main()''', 
                ide_name="Code Composer Studio", 
                associated_board="TI系列板卡", 
                associated_burner="XDS510plus"
            ),
            Script(
                name="GDLINK_Keil脚本", 
                type="shell", 
                content='''#!/bin/bash
# 占位脚本：GDLINK
echo "[INFO] 开始 GD-Link 烧录流程..."
echo "[EXEC] 连接设备..."
sleep 1
echo "[EXEC] 正在烧录..."
sleep 2
echo "[INFO] 烧录成功！"
exit 0''', 
                ide_name="Keil", 
                associated_board="GD32系列", 
                associated_burner="GDLINK"
            ),
            Script(
                name="PWLINK_Keil脚本", 
                type="shell", 
                content='''#!/bin/bash
# 占位脚本：PWLINK
echo "[INFO] 开始 PWLINK 烧录流程..."
echo "[EXEC] 连接设备..."
sleep 1
echo "[EXEC] 正在烧录..."
sleep 2
echo "[INFO] 烧录成功！"
exit 0''', 
                ide_name="Keil", 
                associated_board="通用板卡", 
                associated_burner="PWLINK2"
            ),
        ]
        for s in scripts:
            s.created_at = now - timedelta(days=20)
            s.updated_at = now - timedelta(days=3)
            db.add(s)
        db.commit()

        # Users (额外用户)
        from backend.models.role import Role
        from passlib.context import CryptContext
        pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
        operator_role = db.query(Role).filter(Role.name == "操作员").first()
        viewer_role = db.query(Role).filter(Role.name == "观察员").first()

        extra_users = [
            User(username="zhangwei", password_hash=pwd.hash("admin123"), email="zhangwei@pcids.com", role_id=operator_role.id if operator_role else None, status=1),
            User(username="lina", password_hash=pwd.hash("admin123"), email="lina@pcids.com", role_id=viewer_role.id if viewer_role else None, status=1),
            User(username="wangfang", password_hash=pwd.hash("admin123"), email="wangfang@pcids.com", role_id=None, status=0),
        ]
        for u in extra_users:
            u.created_at = now - timedelta(days=10)
            u.updated_at = now - timedelta(days=1)
            db.add(u)
        db.commit()

        # Records (履历记录)
        op_results = ["烧录成功", "安装成功", "烧录成功", "安装失败", "烧录成功"]
        serials = ["SN20260424001", "SN20260423002", "SN20260422003", "SN20260421004", "SN20260420005"]
        software_names = ["firmware_v1.bin", "os_image_v2.img", "app_v3.bin", "bootloader_v4.bin", "kernel_v5.img"]
        operators = ["admin", "zhangwei", "lina", "wangfang", "admin"]
        record_types = ["burn", "install", "burn", "install", "burn"]
        for i in range(20):
            record = Record(
                serial_number=serials[i % len(serials)],
                software_name=software_names[i % len(software_names)],
                operator=operators[i % len(operators)],
                ip_address=f"192.168.1.{20+i}",
                operation_time=now - timedelta(days=5-i, hours=i),
                result=op_results[i % len(op_results)],
                type=record_types[i % len(record_types)],
            )
            db.add(record)
        db.commit()

        # Injections (异常注入)
        injection_configs = [
            Injection(type="断电模拟", target="STM32F407开发板", config='{"duration": 5, "recovery": "auto"}', status=2, result="测试完成"),
            Injection(type="存储不足", target="LPC55S69评估板", config='{"fill": "large_file", "size": "50%"}', status=2, result="测试完成"),
            Injection(type="网络中断", target="ESP32开发板", config='{"type": "full", "duration": 10}', status=1, result="执行中"),
            Injection(type="权限缺失", target="TI系列板卡", config='{"target": "flash_dir", "perm": "write"}', status=0, result="待执行"),
        ]
        for inj in injection_configs:
            inj.created_at = now - timedelta(days=3)
            inj.updated_at = now - timedelta(days=1)
            db.add(inj)
        db.commit()

        # ProtocolTests (通信协议测试)
        protocol_results = ["通过", "通过", "失败", "通过"]
        protocol_targets = ["STM32F407开发板", "LPC55S69评估板", "ESP32开发板", "TI系列板卡"]
        for i in range(10):
            pt = ProtocolTest(
                target=protocol_targets[i % len(protocol_targets)],
                address=f"0x{i:04X}",
                result=protocol_results[i % len(protocol_results)],
                created_at=now - timedelta(days=4-i),
            )
            db.add(pt)
        db.commit()

        # LoginLogs (登录日志)
        login_results = ["登录成功", "登录成功", "密码错误", "登录成功"]
        for i in range(15):
            ll = LoginLog(
                user_id=(i % 3) + 1,
                ip_address=f"192.168.1.{50+i}",
                log_type="login",
                login_time=now - timedelta(days=7-i, hours=i*2),
                result=login_results[i % len(login_results)],
            )
            db.add(ll)
        db.commit()

        # OperationLogs (操作日志)
        modules = ["用户管理", "角色管理", "烧录任务", "制品仓库", "脚本管理"]
        actions = ["创建用户", "分配权限", "创建烧录任务", "上传制品", "编辑脚本"]
        for i in range(15):
            ol = OperationLog(
                user_id=(i % 3) + 1,
                ip_address=f"192.168.1.{100+i}",
                module=modules[i % len(modules)],
                action=actions[i % len(actions)],
                operation_time=now - timedelta(days=6-i, hours=i),
                result="成功",
            )
            db.add(ol)
        db.commit()

        print("模拟测试数据填充完成")
    except Exception as e:
        db.rollback()
        print(f"填充模拟数据失败：{e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def _migrate_legacy_message_times_to_utc():
    """Normalize legacy structured-message wall-clock times exactly once.

    Older ``create_structured_message`` releases explicitly wrote local naive
    ``created_at`` values, while all other message writes used SQLite UTC.
    Payload shape identifies only those legacy rows. Adding the UTC marker in
    the same transaction makes this migration idempotent and restores a single
    sortable storage convention without guessing about task messages.
    """
    from datetime import datetime, timezone

    from backend.utils import datetime_utils

    legacy_required_keys = {"category", "status", "primary_text"}
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        migration_key = "message_time_basis_utc_v2"
        if conn.execute(
            text("SELECT 1 FROM app_metadata WHERE key = :key"),
            {"key": migration_key},
        ).first():
            return

        select_batch = text(
            "SELECT id, content, created_at FROM messages "
            "WHERE id > :last_message_id "
            "ORDER BY id "
            "LIMIT :batch_size"
        )
        update_message = text(
            "UPDATE messages "
            "SET created_at = :created_at, content = :content "
            "WHERE id = :message_id"
        )
        last_message_id = 0
        while True:
            rows = conn.execute(
                select_batch,
                {
                    "last_message_id": last_message_id,
                    "batch_size": _MESSAGE_TIME_MIGRATION_BATCH_SIZE,
                },
            ).mappings().fetchall()
            if not rows:
                break
            last_message_id = int(rows[-1]["id"])
            batch_updates = []

            for row in rows:
                try:
                    payload = json.loads(str(row["content"] or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if "_time_basis" in payload:
                    continue
                if str(payload.get("task_no") or "").strip():
                    continue
                if not legacy_required_keys.issubset(payload.keys()):
                    continue

                raw_created_at = row["created_at"]
                if isinstance(raw_created_at, datetime):
                    local_created_at = raw_created_at
                else:
                    try:
                        local_created_at = datetime.fromisoformat(
                            str(raw_created_at or "").strip()
                        )
                    except (TypeError, ValueError):
                        continue
                if local_created_at.tzinfo is None:
                    local_created_at = local_created_at.replace(
                        tzinfo=datetime_utils.LOCAL_TIMEZONE
                    )
                utc_created_at = (
                    local_created_at.astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )

                payload["_message_time_version"] = 2
                payload["_time_basis"] = "utc"
                batch_updates.append(
                    {
                        "message_id": int(row["id"]),
                        "created_at": utc_created_at.isoformat(sep=" "),
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )

            if batch_updates:
                conn.execute(update_message, batch_updates)
                batch_updates.clear()

        conn.execute(
            text(
                """
                INSERT INTO app_metadata(key, value, created_at, updated_at)
                VALUES(:key, '1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE
                SET value = '1', updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"key": migration_key},
        )


def _new_unique_sync_identifier(used_values: set[str]) -> str:
    while True:
        candidate = uuid.uuid4().hex
        if candidate not in used_values:
            used_values.add(candidate)
            return candidate


def _backfill_repository_sync_identifiers() -> None:
    """Make repository sync identities usable before the unique index is built.

    Older databases can contain empty identifiers or duplicate identifiers in a
    project.  Existing non-empty identifiers remain stable.  Missing identities
    use the same deterministic CodeArts path seed as runtime writes, allowing
    independently upgraded copies of the same repository data to converge.  A
    random identifier is used only when no stable seed exists or a duplicate
    row needs disambiguation.

    Any outbox rows tied to a repository database ID move with it, while an
    existing canonical state remains attached to the first repository carrying
    that identity.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, project_key, sync_uuid, name, description, display_path, download_uri, "
                "source_type, remote_repo_id, repo_id, repo_detail_json "
                "FROM repositories ORDER BY id"
            )
        ).mappings().fetchall()
        if not rows:
            return

        project_repository_modes: dict[str, str] = {}
        has_setting_table = bool(
            conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'repository_project_settings'"
                )
            ).first()
        )
        if has_setting_table:
            setting_rows = conn.execute(
                text(
                    "SELECT project_key, codearts_config_json "
                    "FROM repository_project_settings"
                )
            ).mappings().fetchall()
            for setting_row in setting_rows:
                try:
                    setting_config = json.loads(str(setting_row["codearts_config_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    setting_config = {}
                if isinstance(setting_config, dict):
                    project_repository_modes[str(setting_row["project_key"] or "")] = str(
                        setting_config.get("repository_mode") or ""
                    ).strip()

        used_values = {
            str(row["sync_uuid"] or "").strip()
            for row in rows
            if str(row["sync_uuid"] or "").strip()
        }
        reserved_pairs = {
            (
                None if row["project_key"] is None else str(row["project_key"]),
                str(row["sync_uuid"] or "").strip(),
            )
            for row in rows
            if str(row["sync_uuid"] or "").strip()
        }
        seen_pairs: set[tuple[Optional[str], str]] = set()
        updates: list[tuple[int, str, Optional[str]]] = []

        for row in rows:
            repository_id = int(row["id"])
            project_key = None if row["project_key"] is None else str(row["project_key"])
            raw_sync_uuid = str(row["sync_uuid"] or "")
            normalized_sync_uuid = raw_sync_uuid.strip()
            pair = (project_key, normalized_sync_uuid)
            needs_replacement = not normalized_sync_uuid or pair in seen_pairs

            if needs_replacement:
                deterministic_uuid = None
                if not normalized_sync_uuid:
                    if str(row["source_type"] or "").strip() == "codearts_sync":
                        repo_detail = {}
                        try:
                            repo_detail = json.loads(str(row["repo_detail_json"] or "{}"))
                        except (TypeError, ValueError, json.JSONDecodeError):
                            repo_detail = {}
                        deterministic_uuid = generate_codearts_repository_sync_uuid(
                            project_key=project_key,
                            remote_repo_id=row["remote_repo_id"] or row["repo_id"],
                            display_path=row["display_path"] or row["description"],
                            name=row["name"],
                            repository_mode=(
                                repo_detail.get("repository_mode")
                                or project_repository_modes.get(str(project_key or ""))
                                if isinstance(repo_detail, dict)
                                else project_repository_modes.get(str(project_key or ""))
                            ),
                        )
                    else:
                        deterministic_uuid = generate_repository_sync_uuid(
                            project_key=project_key,
                            display_path=row["display_path"] or row["description"],
                            download_uri=row["download_uri"],
                            name=row["name"],
                        )
                deterministic_pair = (project_key, str(deterministic_uuid or ""))
                if (
                    deterministic_uuid
                    and deterministic_pair not in reserved_pairs
                    and deterministic_pair not in seen_pairs
                ):
                    normalized_sync_uuid = deterministic_uuid
                    used_values.add(normalized_sync_uuid)
                else:
                    normalized_sync_uuid = _new_unique_sync_identifier(used_values)
                pair = (project_key, normalized_sync_uuid)

            seen_pairs.add(pair)
            if normalized_sync_uuid != raw_sync_uuid:
                updates.append((repository_id, normalized_sync_uuid, project_key))

        has_change_table = bool(
            conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'repository_sync_changes'"
                )
            ).first()
        )
        for repository_id, sync_uuid_value, project_key in updates:
            conn.execute(
                text("UPDATE repositories SET sync_uuid = :sync_uuid WHERE id = :repository_id"),
                {"sync_uuid": sync_uuid_value, "repository_id": repository_id},
            )
            if not has_change_table:
                continue
            change_rows = conn.execute(
                text(
                    "SELECT id, payload_json FROM repository_sync_changes "
                    "WHERE repo_db_id = :repository_id"
                ),
                {"repository_id": repository_id},
            ).mappings().fetchall()
            for change_row in change_rows:
                payload_json = change_row["payload_json"]
                normalized_payload_json = payload_json
                if payload_json:
                    try:
                        payload = json.loads(str(payload_json))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        payload = None
                    if isinstance(payload, dict):
                        payload["sync_uuid"] = sync_uuid_value
                        if project_key is not None:
                            payload["project_key"] = project_key
                        normalized_payload_json = json.dumps(payload, ensure_ascii=False)
                conn.execute(
                    text(
                        "UPDATE repository_sync_changes "
                        "SET repo_sync_uuid = :sync_uuid, payload_json = :payload_json, "
                        "payload_hash = NULL "
                        "WHERE id = :change_id"
                    ),
                    {
                        "sync_uuid": sync_uuid_value,
                        "payload_json": normalized_payload_json,
                        "change_id": int(change_row["id"]),
                    },
                )


def _backfill_repository_change_identifiers() -> None:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, change_uuid FROM repository_sync_changes "
                "ORDER BY id"
            )
        ).mappings().fetchall()
        used_values = {
            str(row["change_uuid"] or "").strip()
            for row in rows
            if str(row["change_uuid"] or "").strip()
        }
        seen_values: set[str] = set()
        for row in rows:
            raw_change_uuid = str(row["change_uuid"] or "")
            normalized_change_uuid = raw_change_uuid.strip()
            if not normalized_change_uuid or normalized_change_uuid in seen_values:
                normalized_change_uuid = _new_unique_sync_identifier(used_values)
            seen_values.add(normalized_change_uuid)
            if normalized_change_uuid != raw_change_uuid:
                conn.execute(
                    text(
                        "UPDATE repository_sync_changes "
                        "SET change_uuid = :change_uuid WHERE id = :change_id"
                    ),
                    {
                        "change_uuid": normalized_change_uuid,
                        "change_id": int(row["id"]),
                    },
                )
        conn.execute(
            text(
                "UPDATE repository_sync_changes "
                "SET base_revision = COALESCE(base_revision, 0), "
                "attempt_count = COALESCE(attempt_count, 0)"
            )
        )


def _ensure_repository_sync_instance() -> str:
    """Create and return the singleton marker owned by this database."""

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT instance_uuid FROM repository_sync_instances "
                "WHERE id = 1"
            )
        ).first()
        current = str(row[0] or "").strip() if row else ""
        if current:
            return current

        instance_uuid = uuid.uuid4().hex
        if row:
            conn.execute(
                text(
                    "UPDATE repository_sync_instances "
                    "SET instance_uuid = :instance_uuid, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = 1"
                ),
                {"instance_uuid": instance_uuid},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO repository_sync_instances "
                    "(id, instance_uuid, created_at, updated_at) "
                    "VALUES (1, :instance_uuid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"instance_uuid": instance_uuid},
            )
        return instance_uuid


def _normalize_repository_sync_state_revisions() -> None:
    """Make every state revision positive and unique within its project.

    Legacy JSON snapshots assigned one revision to every repository in a
    project.  Pull pagination advances with ``revision > cursor``; splitting
    such duplicates across pages would permanently skip the tail.  Preserve
    the first valid occurrence, move invalid/duplicate rows above both the
    project's maximum state revision and its saved cursor, then advance the
    cursor to the resulting maximum.
    """

    with engine.begin() as conn:
        state_rows = conn.execute(
            text(
                "SELECT id, project_key, revision "
                "FROM repository_sync_states "
                "ORDER BY project_key, id"
            )
        ).mappings().fetchall()
        if not state_rows:
            return

        cursor_rows = conn.execute(
            text(
                "SELECT project_key, current_revision "
                "FROM repository_sync_cursors"
            )
        ).mappings().fetchall()
        cursor_by_project: dict[str, int] = {}
        for row in cursor_rows:
            project_key = str(row["project_key"] or "")
            try:
                cursor_by_project[project_key] = max(int(row["current_revision"] or 0), 0)
            except (TypeError, ValueError):
                cursor_by_project[project_key] = 0

        rows_by_project: dict[str, list] = {}
        for row in state_rows:
            rows_by_project.setdefault(str(row["project_key"] or ""), []).append(row)

        revision_updates: list[dict[str, int]] = []
        cursor_updates: list[dict[str, object]] = []
        for project_key, project_rows in rows_by_project.items():
            parsed_revisions: list[int] = []
            for row in project_rows:
                try:
                    parsed_revisions.append(int(row["revision"] or 0))
                except (TypeError, ValueError):
                    parsed_revisions.append(0)

            next_revision = max(
                [value for value in parsed_revisions if value > 0]
                + [cursor_by_project.get(project_key, 0), 0]
            )
            seen_revisions: set[int] = set()
            for row, revision in zip(project_rows, parsed_revisions):
                normalized_revision = revision
                if normalized_revision <= 0 or normalized_revision in seen_revisions:
                    next_revision += 1
                    normalized_revision = next_revision
                    revision_updates.append(
                        {
                            "state_id": int(row["id"]),
                            "revision": normalized_revision,
                        }
                    )
                seen_revisions.add(normalized_revision)

            effective_cursor = max(
                seen_revisions or {0},
                default=0,
            )
            effective_cursor = max(
                effective_cursor,
                next_revision,
                cursor_by_project.get(project_key, 0),
            )
            cursor_updates.append(
                {
                    "project_key": project_key,
                    "current_revision": effective_cursor,
                }
            )

        if revision_updates:
            conn.execute(
                text(
                    "UPDATE repository_sync_states "
                    "SET revision = :revision, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :state_id"
                ),
                revision_updates,
            )
        if cursor_updates:
            conn.execute(
                text(
                    "INSERT INTO repository_sync_cursors "
                    "(project_key, current_revision, created_at, updated_at) "
                    "VALUES (:project_key, :current_revision, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(project_key) DO UPDATE SET "
                    "current_revision = MAX(repository_sync_cursors.current_revision, excluded.current_revision), "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                cursor_updates,
            )


def _ensure_schema_uncached():
    if engine.dialect.name != "sqlite":
        return

    def ensure_table(sql: str):
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()

    def ensure_column(table: str, column: str, ddl_type: str):
        with engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {r[1] for r in rows}
            if column in existing:
                return
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            conn.commit()

    ensure_column("records", "created_by_user_id", "INTEGER")
    ensure_column("records", "repository_id", "INTEGER")
    ensure_column("records", "project_key", "VARCHAR(200)")
    ensure_column("records", "remark", "VARCHAR(500)")
    ensure_column("login_logs", "log_type", "VARCHAR(20)")
    ensure_column("operation_logs", "ip_address", "VARCHAR(50)")
    ensure_column("tasks", "repository_id", "INTEGER")
    ensure_column("tasks", "task_no", "VARCHAR(20)")
    ensure_column("tasks", "progress_percent", "INTEGER")
    ensure_column("tasks", "started_at", "DATETIME")
    ensure_column("tasks", "finished_at", "DATETIME")
    ensure_column("protocol_sessions", "task_no", "VARCHAR(20)")
    ensure_column("injection_runs", "task_no", "VARCHAR(20)")
    ensure_column("tasks", "task_type", "VARCHAR(20)")
    ensure_column("tasks", "serial_number", "VARCHAR(100)")
    ensure_column("tasks", "keep_local", "INTEGER")
    ensure_column("tasks", "integrity", "INTEGER")
    ensure_column("tasks", "expected_checksum", "VARCHAR(128)")
    ensure_column("tasks", "current_md5", "VARCHAR(64)")
    ensure_column("tasks", "current_sha256", "VARCHAR(128)")
    ensure_column("tasks", "integrity_passed", "INTEGER")
    ensure_column("tasks", "version_check", "INTEGER")
    ensure_column("tasks", "history_checksum", "VARCHAR(128)")
    ensure_column("tasks", "consistency_passed", "INTEGER")
    ensure_column("tasks", "override_confirmed", "INTEGER")
    ensure_column("tasks", "created_by_user_id", "INTEGER")
    ensure_column("tasks", "attempt_count", "INTEGER")
    ensure_column("tasks", "max_retries", "INTEGER")
    ensure_column("tasks", "rollback_count", "INTEGER")
    ensure_column("tasks", "rollback_result", "TEXT")
    ensure_column("tasks", "last_error", "TEXT")
    ensure_column("tasks", "agent_url", "VARCHAR(500)")
    ensure_column("tasks", "script_id", "INTEGER")
    ensure_column("tasks", "termination_reason", "TEXT")
    ensure_column("tasks", "termination_requested_at", "DATETIME")
    ensure_column("tasks", "terminated_by_user_id", "INTEGER")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_tasks_created_at ON tasks (created_at)")
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_messages_user_created_id "
        "ON messages (user_id, created_at, id)"
    )
    ensure_column("scripts", "status", "INTEGER")
    ensure_column("scripts", "result", "TEXT")
    ensure_column("scripts", "ide_name", "VARCHAR(100)")
    ensure_column("scripts", "associated_board", "VARCHAR(200)")
    ensure_column("scripts", "associated_burner", "VARCHAR(200)")
    ensure_column("scripts", "is_system", "INTEGER")
    ensure_column("scripts", "default_config_json", "TEXT")
    ensure_column("burners", "location", "VARCHAR(100)")
    ensure_column("burners", "host_type", "VARCHAR(20)")
    ensure_column("burners", "host_name", "VARCHAR(100)")
    ensure_column("burners", "host_address", "VARCHAR(100)")
    ensure_column("burners", "agent_url", "VARCHAR(500)")
    ensure_column("burners", "strategy", "INTEGER")
    ensure_column("burners", "is_enabled", "INTEGER")
    ensure_column("burners", "config_json", "TEXT")
    ensure_column("burners", "modified_by", "VARCHAR(50)")
    ensure_column("products", "usage_description", "TEXT")
    ensure_column("products", "board_image", "VARCHAR(500)")
    ensure_column("products", "burn_interface", "TEXT")
    ensure_column("products", "chip_model", "VARCHAR(100)")
    ensure_column("scripts", "associated_ide", "VARCHAR(100)")
    ensure_column("scripts", "description", "TEXT")
    ensure_column("scripts", "task_type", "VARCHAR(30)")
    ensure_column("products", "created_by", "VARCHAR(50)")
    ensure_column("products", "modified_by", "VARCHAR(50)")
    ensure_column("repositories", "version", "VARCHAR(100)")
    ensure_column("repositories", "file_url", "VARCHAR(500)")
    ensure_column("repositories", "size", "INTEGER")
    ensure_column("repositories", "md5", "VARCHAR(64)")
    ensure_column("repositories", "sha256", "VARCHAR(128)")
    ensure_column("repositories", "download_count", "INTEGER")
    ensure_column("repositories", "last_download_time", "DATETIME")
    ensure_column("repositories", "created_by_user_id", "INTEGER")
    ensure_column("repositories", "project_key", "VARCHAR(200)")
    ensure_column("repositories", "sync_uuid", "VARCHAR(64)")
    ensure_column("repositories", "permission_config_json", "TEXT")
    ensure_column("repositories", "source_type", "VARCHAR(30)")
    ensure_column("repositories", "remote_repo_id", "VARCHAR(100)")
    ensure_column("repositories", "display_path", "VARCHAR(500)")
    ensure_column("repositories", "download_uri", "TEXT")
    ensure_column("repositories", "repo_detail_json", "TEXT")
    ensure_column("repositories", "file_detail_json", "TEXT")
    ensure_column("users", "codearts_config_json", "TEXT")
    ensure_column("users", "token_version", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("repository_project_settings", "codearts_config_json", "TEXT")
    ensure_column("repository_project_settings", "auto_sync_state_json", "TEXT")
    ensure_column("repository_project_settings", "auto_sync_last_job_id", "INTEGER")
    ensure_column("repository_project_settings", "auto_sync_last_success_at", "DATETIME")
    ensure_column("repository_project_settings", "auto_sync_last_error", "TEXT")

    ensure_column("repository_sync_changes", "change_uuid", "VARCHAR(64)")
    ensure_column("repository_sync_changes", "parent_change_uuid", "VARCHAR(64)")
    ensure_column("repository_sync_changes", "base_revision", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("repository_sync_changes", "payload_hash", "VARCHAR(64)")
    ensure_column("repository_sync_changes", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("repository_sync_changes", "next_attempt_at", "DATETIME")
    ensure_column("repository_sync_changes", "claim_token", "VARCHAR(64)")
    ensure_column("repository_sync_changes", "claim_expires_at", "DATETIME")
    ensure_column("repository_sync_changes", "server_revision", "INTEGER")
    ensure_column("repository_sync_changes", "origin_node_id", "VARCHAR(64)")

    _backfill_repository_sync_identifiers()
    _backfill_repository_change_identifiers()
    ensure_table(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_repositories_project_sync_uuid "
        "ON repositories (project_key, sync_uuid) "
        "WHERE project_key IS NOT NULL AND project_key <> '' "
        "AND sync_uuid IS NOT NULL AND sync_uuid <> ''"
    )
    ensure_table(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_repository_sync_changes_change_uuid "
        "ON repository_sync_changes (change_uuid) "
        "WHERE change_uuid IS NOT NULL AND change_uuid <> ''"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_changes_project_status_next "
        "ON repository_sync_changes (project_key, status, next_attempt_at, id)"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_changes_project_sync_status "
        "ON repository_sync_changes (project_key, repo_sync_uuid, status, id)"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_changes_claim_expiry "
        "ON repository_sync_changes (status, claim_expires_at, id)"
    )

    ensure_table(
        """
        CREATE TABLE IF NOT EXISTS repository_sync_states (
            id INTEGER NOT NULL PRIMARY KEY,
            project_key VARCHAR(200) NOT NULL,
            sync_uuid VARCHAR(64) NOT NULL,
            revision INTEGER,
            deleted BOOLEAN,
            payload_json TEXT,
            source_updated_at DATETIME,
            applied_change_id INTEGER,
            updated_by_job_id INTEGER,
            created_at DATETIME,
            updated_at DATETIME,
            UNIQUE(project_key, sync_uuid),
            FOREIGN KEY(applied_change_id) REFERENCES repository_sync_changes (id),
            FOREIGN KEY(updated_by_job_id) REFERENCES repository_sync_jobs (id)
        )
        """
    )
    ensure_column("repository_sync_states", "payload_hash", "VARCHAR(64)")
    ensure_column("repository_sync_states", "origin_node_id", "VARCHAR(64)")
    ensure_column("repository_sync_states", "origin_change_uuid", "VARCHAR(64)")
    ensure_column("repository_sync_states", "server_instance_id", "VARCHAR(64)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_project_key ON repository_sync_states (project_key)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_sync_uuid ON repository_sync_states (sync_uuid)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_revision ON repository_sync_states (revision)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_deleted ON repository_sync_states (deleted)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_origin_node_id ON repository_sync_states (origin_node_id)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_origin_change_uuid ON repository_sync_states (origin_change_uuid)")
    ensure_table("CREATE INDEX IF NOT EXISTS ix_repository_sync_states_server_instance_id ON repository_sync_states (server_instance_id)")
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_states_project_revision "
        "ON repository_sync_states (project_key, revision, id)"
    )

    ensure_table(
        """
        CREATE TABLE IF NOT EXISTS repository_sync_cursors (
            id INTEGER NOT NULL PRIMARY KEY,
            project_key VARCHAR(200) NOT NULL UNIQUE,
            current_revision INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_cursors_project_key "
        "ON repository_sync_cursors (project_key)"
    )
    _normalize_repository_sync_state_revisions()
    ensure_table(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_repository_sync_state_project_revision "
        "ON repository_sync_states (project_key, revision)"
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO repository_sync_cursors (
                    project_key, current_revision, created_at, updated_at
                )
                SELECT project_key, COALESCE(MAX(revision), 0),
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM repository_sync_states
                GROUP BY project_key
                ON CONFLICT(project_key) DO UPDATE SET
                    current_revision = MAX(
                        repository_sync_cursors.current_revision,
                        excluded.current_revision
                    ),
                    updated_at = CURRENT_TIMESTAMP
                """
            )
        )

    ensure_table(
        """
        CREATE TABLE IF NOT EXISTS repository_sync_instances (
            id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
            instance_uuid VARCHAR(64) NOT NULL UNIQUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_repository_sync_instance()

    ensure_table(
        """
        CREATE TABLE IF NOT EXISTS repository_sync_receipts (
            id INTEGER NOT NULL PRIMARY KEY,
            change_uuid VARCHAR(64) NOT NULL UNIQUE,
            node_id VARCHAR(64) NOT NULL,
            project_key VARCHAR(200) NOT NULL,
            sync_uuid VARCHAR(64) NOT NULL,
            outcome VARCHAR(30) NOT NULL,
            server_revision INTEGER,
            request_hash VARCHAR(64),
            result_json TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_column("repository_sync_receipts", "request_hash", "VARCHAR(64)")
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_receipts_request_hash "
        "ON repository_sync_receipts (request_hash)"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_receipts_project_revision "
        "ON repository_sync_receipts (project_key, server_revision, id)"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_receipts_node_project "
        "ON repository_sync_receipts (node_id, project_key, id)"
    )

    ensure_table(
        """
        CREATE TABLE IF NOT EXISTS repository_sync_peers (
            id INTEGER NOT NULL PRIMARY KEY,
            project_key VARCHAR(200) NOT NULL,
            server_base_url VARCHAR(1000) NOT NULL,
            server_instance_id VARCHAR(64),
            pulled_revision INTEGER NOT NULL DEFAULT 0,
            bootstrap_completed_at DATETIME,
            last_push_at DATETIME,
            last_pull_at DATETIME,
            last_success_at DATETIME,
            last_error TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_key, server_base_url)
        )
        """
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_peers_project_key "
        "ON repository_sync_peers (project_key)"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_peers_server_instance "
        "ON repository_sync_peers (server_instance_id, project_key)"
    )

    ensure_table(
        """
        CREATE TABLE IF NOT EXISTS repository_sync_leases (
            id INTEGER NOT NULL PRIMARY KEY,
            project_key VARCHAR(200) NOT NULL UNIQUE,
            owner_id VARCHAR(64) NOT NULL,
            lease_until DATETIME NOT NULL,
            heartbeat_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_leases_owner_id "
        "ON repository_sync_leases (owner_id)"
    )
    ensure_table(
        "CREATE INDEX IF NOT EXISTS ix_repository_sync_leases_expiry "
        "ON repository_sync_leases (lease_until, project_key)"
    )
    _migrate_legacy_message_times_to_utc()


def ensure_schema():
    """Run SQLite upgrade checks once per backend process.

    The lock ensures concurrent first requests share one migration pass.  The
    ready flag is set only after the pass returns successfully, so a transient
    database error remains recoverable on the next request.
    """
    global _schema_ready
    if _schema_ready or engine.dialect.name != "sqlite":
        return
    with _schema_ready_lock:
        if _schema_ready:
            return
        _ensure_schema_uncached()
        _schema_ready = True


def _is_valid_task_no(value: str) -> bool:
    normalized = str(value or "").strip()
    return len(normalized) == 11 and normalized.isdigit()


def generate_task_no(db, created_at=None, exclude_task_id: Optional[int] = None) -> str:
    from datetime import datetime
    from backend.models.task import BurningTask

    task_time = created_at or datetime.now()
    prefix = task_time.strftime("%Y%m%d")
    query = db.query(BurningTask.task_no).filter(BurningTask.task_no.like(f"{prefix}%"))
    if exclude_task_id is not None:
        query = query.filter(BurningTask.id != exclude_task_id)

    max_seq = 0
    for (task_no,) in query.all():
        current = str(task_no or "").strip()
        if not _is_valid_task_no(current) or not current.startswith(prefix):
            continue
        max_seq = max(max_seq, int(current[-3:]))
    return f"{prefix}{max_seq + 1:03d}"


def ensure_task_numbers(db):
    from backend.models.task import BurningTask

    tasks = db.query(BurningTask).order_by(BurningTask.created_at.asc(), BurningTask.id.asc()).all()
    used_sequences: dict[str, set[int]] = {}
    changed = False

    for task in tasks:
        current = str(getattr(task, "task_no", None) or "").strip()
        if not getattr(task, "created_at", None):
            continue
        prefix = task.created_at.strftime("%Y%m%d")
        if _is_valid_task_no(current) and current.startswith(prefix):
            used_sequences.setdefault(prefix, set()).add(int(current[-3:]))
            continue

        used = used_sequences.setdefault(prefix, set())
        sequence = 1
        while sequence in used:
            sequence += 1
        task.task_no = f"{prefix}{sequence:03d}"
        used.add(sequence)
        changed = True

    if changed:
        db.commit()


def generate_protocol_session_no(db, created_at=None, exclude_session_id: Optional[int] = None) -> str:
    from datetime import datetime
    from backend.models.log import ProtocolSession

    session_time = created_at or datetime.now()
    prefix = session_time.strftime("%Y%m%d")
    query = db.query(ProtocolSession.task_no).filter(ProtocolSession.task_no.like(f"{prefix}%"))
    if exclude_session_id is not None:
        query = query.filter(ProtocolSession.id != exclude_session_id)

    max_seq = 0
    for (task_no,) in query.all():
        current = str(task_no or "").strip()
        if not _is_valid_task_no(current) or not current.startswith(prefix):
            continue
        max_seq = max(max_seq, int(current[-3:]))
    return f"{prefix}{max_seq + 1:03d}"


def generate_injection_run_no(db, created_at=None, exclude_run_id: Optional[int] = None) -> str:
    from datetime import datetime
    from backend.models.log import InjectionRun

    run_time = created_at or datetime.now()
    prefix = run_time.strftime("%Y%m%d")
    query = db.query(InjectionRun.task_no).filter(InjectionRun.task_no.like(f"{prefix}%"))
    if exclude_run_id is not None:
        query = query.filter(InjectionRun.id != exclude_run_id)

    max_seq = 0
    for (task_no,) in query.all():
        current = str(task_no or "").strip()
        if not _is_valid_task_no(current) or not current.startswith(prefix):
            continue
        max_seq = max(max_seq, int(current[-3:]))
    return f"{prefix}{max_seq + 1:03d}"


def ensure_injection_run_numbers(db):
    from backend.models.log import InjectionRun

    runs = db.query(InjectionRun).order_by(InjectionRun.exec_time.asc(), InjectionRun.id.asc()).all()
    used_sequences: dict[str, set[int]] = {}
    changed = False

    for run in runs:
        current = str(getattr(run, "task_no", None) or "").strip()
        run_time = getattr(run, "exec_time", None)
        if not run_time:
            continue
        prefix = run_time.strftime("%Y%m%d")
        if _is_valid_task_no(current) and current.startswith(prefix):
            used_sequences.setdefault(prefix, set()).add(int(current[-3:]))
            continue

        used = used_sequences.setdefault(prefix, set())
        sequence = 1
        while sequence in used:
            sequence += 1
        run.task_no = f"{prefix}{sequence:03d}"
        used.add(sequence)
        changed = True

    if changed:
        db.commit()


def ensure_protocol_session_numbers(db):
    from backend.models.log import ProtocolSession

    sessions = db.query(ProtocolSession).order_by(ProtocolSession.created_at.asc(), ProtocolSession.id.asc()).all()
    used_sequences: dict[str, set[int]] = {}
    changed = False

    for session in sessions:
        current = str(getattr(session, "task_no", None) or "").strip()
        if not getattr(session, "created_at", None):
            continue
        prefix = session.created_at.strftime("%Y%m%d")
        if _is_valid_task_no(current) and current.startswith(prefix):
            used_sequences.setdefault(prefix, set()).add(int(current[-3:]))
            continue

        used = used_sequences.setdefault(prefix, set())
        sequence = 1
        while sequence in used:
            sequence += 1
        session.task_no = f"{prefix}{sequence:03d}"
        used.add(sequence)
        changed = True

    if changed:
        db.commit()


# 各种"经常被乱码写入"的中文列名集中在此表，初始化时统一过一遍 mojibake 反转。
_MOJIBAKE_SCAN_TARGETS: list[tuple[str, tuple[str, ...]]] = [
    ("products", ("name", "chip_type", "chip_model", "voltage", "temp_range", "interface", "burn_interface", "config_description", "usage_description")),
    ("burners", ("name", "type", "description", "sn", "port")),
    ("scripts", ("name", "ide_name", "associated_ide", "associated_burner", "associated_board", "description", "default_config_json", "content")),
    ("tasks", ("task_no", "board_name", "executable", "serial_number", "result", "rollback_result", "last_error", "software_name", "agent_url", "config_json")),
    ("injection_tasks", ("task_no", "board_name", "executable", "serial_number", "result", "last_error", "config_json")),
    ("protocol_sessions", ("task_no", "board_name", "executable", "serial_number", "result", "last_error")),
    ("repositories", ("name", "display_path", "file_url", "download_uri", "source_type", "version")),
    ("users", ("display_name", "email", "remark")),
    ("roles", ("name", "description", "data_scope", "remark")),
    ("operation_logs", ("action", "module", "detail", "ip_address", "user_agent", "result")),
    ("login_logs", ("username", "ip_address", "user_agent", "status", "failure_reason")),
]


def repair_stored_mojibake(db) -> int:
    """启动时对历史可能存在的乱码做一次主动反转。"""
    fixed = 0
    for table, columns in _MOJIBAKE_SCAN_TARGETS:
        try:
            rows = db.execute(text(f'SELECT id, {", ".join(columns)} FROM {table}')).fetchall()
        except Exception:
            continue
        if not rows:
            continue
        column_names = ["id"] + list(columns)
        for row in rows:
            payload = dict(zip(column_names, row))
            row_id = payload.pop("id", None)
            if row_id is None:
                continue
            update_values: dict[str, object] = {}
            for column in columns:
                original = payload.get(column)
                if not isinstance(original, str) or not original:
                    continue
                if column.endswith("_json") or column == "default_config_json":
                    try:
                        parsed = json.loads(original)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, (dict, list)):
                        repaired_payload = normalize_text_payload(parsed)
                        repaired_text = json.dumps(repaired_payload, ensure_ascii=False)
                    else:
                        repaired_text = normalize_text(original)
                else:
                    repaired_text = normalize_text(original)
                if repaired_text != original:
                    update_values[column] = repaired_text
            if update_values:
                set_clauses = ", ".join(f"{col} = :{col}" for col in update_values.keys())
                try:
                    db.execute(
                        text(f'UPDATE {table} SET {set_clauses} WHERE id = :row_id'),
                        {**update_values, "row_id": row_id},
                    )
                    fixed += 1
                except Exception:
                    continue
    if fixed:
        db.commit()
    return fixed


def ensure_script_task_types(db):
    from backend.models.script import Script

    scripts = db.query(Script).all()
    changed = False
    for script in scripts:
        if not str(getattr(script, "task_type", "") or "").strip():
            script.task_type = "board"
            changed = True
    if changed:
        db.commit()


def ensure_product_burn_interfaces(db):
    """为历史板卡数据补齐烧录接口与芯片型号，保证烧录流程优先使用板卡配置。"""
    from backend.models.product import Product

    default_burn_interfaces = {
        "ARM": ["JTAG", "SWD"],
        "DSP": ["JTAG"],
        "PIC": ["ICSP"],
        "FPGA": ["JTAG"],
        "Altera-CPLD": ["JTAG"],
        "其他": ["JTAG"],
    }
    default_comm_interfaces = {
        "ARM": ["USB"],
        "DSP": ["CAN"],
        "PIC": ["SPI"],
        "FPGA": ["以太网"],
        "Altera-CPLD": ["JTAG"],
        "其他": ["UART"],
    }
    changed = False
    products = db.query(Product).all()
    for product in products:
        normalized_burn_interfaces = _normalize_burn_interfaces(getattr(product, "burn_interface", None))
        if normalized_burn_interfaces:
            normalized_burn_interfaces = [item for item in normalized_burn_interfaces if item in ALLOWED_BURN_INTERFACES]
            burn_interface_json = json.dumps(normalized_burn_interfaces, ensure_ascii=False)
            if getattr(product, "burn_interface", None) != burn_interface_json:
                product.burn_interface = burn_interface_json
                changed = True
        else:
            mapped = default_burn_interfaces.get(product.chip_type)
            if mapped:
                product.burn_interface = json.dumps(mapped, ensure_ascii=False)
                changed = True
        if not getattr(product, "interface", None):
            mapped_interface = default_comm_interfaces.get(product.chip_type)
            if mapped_interface:
                product.interface = json.dumps(mapped_interface, ensure_ascii=False)
                changed = True
        if not getattr(product, "chip_model", None):
            product.chip_model = product.name.replace("开发板", "").replace("评估板", "").replace("核心板", "").replace("板", "").strip() or None
            if product.chip_model:
                changed = True

    if changed:
        db.commit()


def ensure_default_products(db):
    """补齐默认产品清单，避免脚本绑定的板卡在产品列表中缺失。"""
    from backend.models.product import Product

    existing_products = {str(product.name or "").strip(): product for product in db.query(Product).all()}
    created = 0
    updated = 0

    for item in DEFAULT_PRODUCT_CATALOG:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        product = existing_products.get(name)
        if not product:
            db.add(_build_product_from_catalog(item))
            created += 1
            continue

        changed = False
        for field_name in ["chip_type", "chip_model", "serial_number", "voltage", "temp_range", "config_description"]:
            field_value = item.get(field_name)
            if field_value and not getattr(product, field_name, None):
                setattr(product, field_name, field_value)
                changed = True

        burn_interface_json = json.dumps(item.get("burn_interface") or [], ensure_ascii=False)
        if burn_interface_json != "[]" and not getattr(product, "burn_interface", None):
            product.burn_interface = burn_interface_json
            changed = True

        interface_json = json.dumps(item.get("interface") or [], ensure_ascii=False)
        if interface_json != "[]" and not getattr(product, "interface", None):
            product.interface = interface_json
            changed = True

        if changed:
            updated += 1

    if created or updated:
        db.commit()
        print(f"已补齐默认产品 {created} 个，更新 {updated} 个")


def sync_menu_sort_order(db):
    from backend.models.permission import Menu
    updates = {
        4: 3, # 烧录安装管理
        5: 4, # 履历记录
        3: 5, # 资产管理
    }
    changed = False
    for menu_id, sort_order in updates.items():
        menu = db.query(Menu).filter(Menu.id == menu_id).first()
        if menu and menu.sort_order != sort_order:
            menu.sort_order = sort_order
            changed = True
    if changed:
        db.commit()

INIT_METADATA_KEY = "initial_seed_completed"


def _ensure_app_metadata_table():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS app_metadata (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))


def _is_initial_seed_completed() -> bool:
    _ensure_app_metadata_table()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM app_metadata WHERE key = :key"),
            {"key": INIT_METADATA_KEY},
        ).fetchone()
    return bool(row and str(row[0] or "").strip() == "1")


def _mark_initial_seed_completed():
    _ensure_app_metadata_table()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO app_metadata(key, value, created_at, updated_at)
                VALUES(:key, '1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP
            """),
            {"key": INIT_METADATA_KEY},
        )


def _has_existing_business_data() -> bool:
    with engine.connect() as conn:
        for table_name in ["users", "roles", "burners", "products", "scripts", "repositories", "tasks"]:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
            except Exception:
                count = 0
            if int(count) > 0:
                return True
    return False


def _sync_recurring_application_data(db):
    """Apply idempotent data migrations on every application startup."""
    init_menus_and_permissions(db)
    ensure_default_role_permissions(db)
    sync_historical_task_durations(db)
    migrate_legacy_task_terminated_statuses(db)
    sync_menu_sort_order(db)
    sync_device_menu_labels(db)
    ensure_task_numbers(db)
    ensure_injection_run_numbers(db)
    ensure_protocol_session_numbers(db)


def init_db():
    """初始化数据库，创建所有表"""
    from backend.models.base import Base
    Base.metadata.create_all(bind=engine)
    ensure_schema()

    if _is_initial_seed_completed():
        db = SessionLocal()
        try:
            _sync_recurring_application_data(db)
            ensure_default_system_scripts(db)
            ensure_script_task_types(db)
            ensure_default_products(db)
            ensure_product_burn_interfaces(db)
        finally:
            db.close()
        print("数据库已完成首次初始化，已同步系统脚本和默认板卡并跳过默认数据初始化")
        return

    if _has_existing_business_data():
        db = SessionLocal()
        try:
            _sync_recurring_application_data(db)
            # 旧版本数据库首次升级时也必须立即同步权威系统脚本。
            # 否则仅写入初始化标记会导致新脚本/新默认参数要到下次启动才生效。
            ensure_default_system_scripts(db)
            ensure_script_task_types(db)
            ensure_default_products(db)
            ensure_product_burn_interfaces(db)
        finally:
            db.close()
        _mark_initial_seed_completed()
        print("检测到已有业务数据，已同步系统脚本和默认板卡，写入初始化标记并跳过默认数据初始化")
        return

    db = SessionLocal()

    try:
        # 创建默认角色
        from backend.models.role import Role
        admin_role = db.query(Role).filter(Role.name == "管理员").first()
        if not admin_role:
            admin_role = Role(
                name="管理员",
                description="系统管理员，拥有所有权限"
            )
            db.add(admin_role)
            db.commit()
            db.refresh(admin_role)
            print("已创建管理员角色")

        operator_role = db.query(Role).filter(Role.name == "操作员").first()
        if not operator_role:
            operator_role = Role(
                name="操作员",
                description="普通操作员，可执行烧录任务"
            )
            db.add(operator_role)
            db.commit()
            db.refresh(operator_role)
            print("已创建操作员角色")

        viewer_role = db.query(Role).filter(Role.name == "观察员").first()
        if not viewer_role:
            viewer_role = Role(
                name="观察员",
                description="只读权限，可查看记录和日志"
            )
            db.add(viewer_role)
            db.commit()
            print("已创建观察员角色")

        # 初始化菜单和权限
        init_menus_and_permissions(db)
        ensure_default_role_permissions(db)
        sync_historical_task_durations(db)
        migrate_legacy_task_terminated_statuses(db)
        sync_menu_sort_order(db)
        sync_device_menu_labels(db)
        ensure_default_burners(db)
        sync_default_burners(db)
        ensure_default_system_scripts(db)
        ensure_script_task_types(db)
        ensure_default_products(db)
        ensure_product_burn_interfaces(db)
        ensure_task_numbers(db)
        ensure_injection_run_numbers(db)
        ensure_protocol_session_numbers(db)

        # 注意：不再主动反转历史字段。曾经根据 PowerShell GBK 渲染
        # 误报“乱码”修过 24 个干净字段，造成新乱码。如果以后真要
        # 启用，请确认 mojibake 判定严格（仅命中无中文字符的字符串）。

        # 创建默认管理员账户
        from backend.models.user import User
        from passlib.context import CryptContext

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                password_hash=pwd_context.hash("admin123"),
                email="admin@pcids.com",
                role_id=admin_role.id,
                status=1
            )
            db.add(admin)
            db.commit()
            print("已创建默认管理员账户：admin / admin123")

        print("数据库初始化完成")
        _mark_initial_seed_completed()
    except Exception as e:
        db.rollback()
        print(f"初始化数据失败：{e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

    # 仅在显式开启时填充模拟数据，默认保持真实数据环境。
    if str(os.environ.get("PCIDS_ENABLE_MOCK_SEED") or "").strip() == "1":
        seed_mock_data()
