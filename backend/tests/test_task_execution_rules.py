import json
import unittest

from fastapi import HTTPException

from backend.models.burner import Burner
from backend.models.script import Script
from backend.models.task import BurningTask
from backend.utils.task_execution import build_execution_plan, get_task_timeout_seconds, validate_script_execution_config


def build_script(default_config: dict, name: str = "demo_script") -> Script:
    return Script(
        id=1,
        name=name,
        type="flash",
        content="echo demo",
        default_config_json=json.dumps(default_config, ensure_ascii=False),
    )


def build_task() -> BurningTask:
    return BurningTask(
        id=1,
        software_name="demo",
        task_type="board",
        burner_id=1,
    )


def build_burner(sn: str = "ST123456") -> Burner:
    return Burner(
        id=1,
        name="demo-burner",
        type="ST-LINK",
        sn=sn,
    )


class TaskExecutionRuleTests(unittest.TestCase):
    def test_timeout_minutes_are_converted_to_seconds(self):
        self.assertEqual(get_task_timeout_seconds({"timeout_minutes": 2}, default=120), 120)
        self.assertEqual(get_task_timeout_seconds({"timeout_minutes": 120}, default=120), 7200)

    def test_hex_artifact_allows_empty_start_address(self):
        script = build_script(
            {
                "interface_type": "SWD",
                "interface_type_options": ["SWD", "JTAG"],
                "start_address": "",
                "start_address_label": "起始地址",
            }
        )

        normalized = validate_script_execution_config({}, script, artifact_name="Project.hex")

        self.assertEqual(normalized.get("interface_type"), "SWD")
        self.assertIn(normalized.get("start_address"), {None, ""})

    def test_bin_artifact_requires_start_address(self):
        script = build_script(
            {
                "start_address": "",
                "start_address_label": "起始地址",
            }
        )

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config({}, script, artifact_name="firmware.bin")

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("请输入起始地址", context.exception.detail)

    def test_optional_select_field_can_be_empty_when_not_required(self):
        script = build_script(
            {
                "interface_type_options": ["SWD", "JTAG"],
            }
        )

        normalized = validate_script_execution_config({}, script, artifact_name="Project.hex")

        self.assertIsNone(normalized.get("interface_type"))

    def test_required_select_field_still_blocks_when_empty(self):
        script = build_script(
            {
                "interface_type_options": ["SWD", "JTAG"],
                "required_fields": ["interface_type"],
            }
        )

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config({}, script, artifact_name="Project.hex")

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("请选择接口类型", context.exception.detail)


    def test_invalid_numeric_option_is_rejected(self):
        script = build_script(
            {
                "write_speed_khz": 1000,
                "speed_options": [500, 1000, 2000],
            }
        )

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config(
                {"write_speed_khz": "not-a-number"},
                script,
                artifact_name="Project.hex",
            )

        self.assertEqual(context.exception.status_code, 400)

    def test_al321_flash_requires_boot_bin_and_elf(self):
        script = build_script(
            {
                "execution_operation": "SRAM下载",
                "execution_operation_options": ["SRAM下载", "Flash固化"],
                "target_config_file": "",
                "target_config_file_label": "ZynqMP ELF文件(.elf)",
                "start_address": "0x0",
            },
            name="al321_fpga_mcu_flash",
        )

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config(
                {"execution_operation": "Flash固化", "target_config_file": "fsbl.elf"},
                script,
                artifact_name="system.bit",
            )
        self.assertIn("BOOT.bin", context.exception.detail)

        normalized = validate_script_execution_config(
            {"execution_operation": "Flash固化", "target_config_file": "zynqmp_fsbl.elf"},
            script,
            artifact_name="BOOT.bin",
        )
        self.assertEqual(normalized["target_config_file"], "zynqmp_fsbl.elf")

        normalized = validate_script_execution_config(
            {"execution_operation": "Flash固化", "target_config_file": "CAN.elf"},
            script,
            artifact_name="BOOT.bin",
        )
        self.assertEqual(normalized["target_config_file"], "CAN.elf")

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config(
                {"execution_operation": "SRAM下载"},
                script,
                artifact_name="BOOT.bin",
            )
        self.assertIn("SRAM下载需要选择 FPGA bitstream", context.exception.detail)

    def test_xds510plus_requires_target_config_file(self):
        script = build_script(
            {
                "interface_type": "JTAG",
                "interface_type_options": ["JTAG"],
                "target_config_file": "",
                "target_config_file_label": "目标配置文件",
                "target_config_file_required": True,
                "required_fields": ["target_config_file"],
            },
            name="xds510plus_dsp_flash",
        )

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config({}, script, artifact_name="M405C_Control.out")

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("目标配置文件", context.exception.detail)

        normalized = validate_script_execution_config(
            {"target_config_file": r"D:\workspace\pcids\.dbg\xds510plus_f28335.ccxml"},
            script,
            artifact_name="M405C_Control.out",
        )
        self.assertEqual(normalized["target_config_file"], r"D:\workspace\pcids\.dbg\xds510plus_f28335.ccxml")

    def test_strict_swd_scripts_require_target_chip(self):
        script = build_script({}, name="stlink_stm32_mcu_flash")

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config({}, script, artifact_name="Project.hex")

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("未配置 TARGET_CHIP", context.exception.detail)

    def test_strict_swd_scripts_reject_unsafe_target_chip(self):
        script = build_script({}, name="stlink_stm32_mcu_flash")

        with self.assertRaises(HTTPException) as context:
            validate_script_execution_config({"target_chip": 'STM32H743ZIT6"&whoami'}, script, artifact_name="Project.hex")

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("TARGET_CHIP", context.exception.detail)

    def test_strict_swd_scripts_require_valid_start_address_for_bin(self):
        script = build_script({}, name="pwlink_v2_arm_mcu_flash")

        with self.assertRaises(HTTPException) as missing_context:
            validate_script_execution_config({"target_chip": "STM32F407VGT6"}, script, artifact_name="firmware.bin")
        self.assertIn("START_ADDRESS", missing_context.exception.detail)

        with self.assertRaises(HTTPException) as invalid_context:
            validate_script_execution_config(
                {"target_chip": "STM32F407VGT6", "start_address": "0xZZ"},
                script,
                artifact_name="firmware.bin",
            )
        self.assertIn("START_ADDRESS", invalid_context.exception.detail)

    def test_hex_and_elf_allow_empty_start_address_for_strict_swd_scripts(self):
        script = build_script({}, name="swd_downloader_arm_mcu_flash")

        hex_normalized = validate_script_execution_config(
            {"target_chip": "STM32F407VGT6"},
            script,
            artifact_name="firmware.hex",
        )
        elf_normalized = validate_script_execution_config(
            {"target_chip": "STM32F407VGT6"},
            script,
            artifact_name="firmware.elf",
        )

        self.assertEqual(hex_normalized["target_chip"], "STM32F407VGT6")
        self.assertEqual(elf_normalized["target_chip"], "STM32F407VGT6")
        self.assertIn(hex_normalized.get("start_address"), {None, ""})
        self.assertIn(elf_normalized.get("start_address"), {None, ""})

    def test_build_execution_plan_rejects_missing_burner_serial_for_strict_scripts(self):
        script = build_script({}, name="stlink_stm32_mcu_flash")

        with self.assertRaises(HTTPException) as context:
            build_execution_plan(
                build_task(),
                {"target_chip": "STM32F407VGT6", "start_address": "0x08000000"},
                None,
                build_burner(sn=""),
                script,
                used_file_path="firmware.bin",
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("未配置 BURNER_SN", context.exception.detail)

    def test_build_execution_plan_accepts_strict_scripts_with_probe_serial_and_valid_address(self):
        script = build_script({}, name="pwlink_v2_arm_mcu_flash")

        plan = build_execution_plan(
            build_task(),
            {"target_chip": "STM32F407VGT6", "start_address": "134217728"},
            None,
            build_burner(sn="PW-001"),
            script,
            used_file_path="firmware.bin",
        )

        self.assertEqual(plan.runtime_env["TARGET_CHIP"], "STM32F407VGT6")
        self.assertEqual(plan.runtime_env["BURNER_SN"], "PW-001")
        self.assertEqual(plan.runtime_env["START_ADDRESS"], "134217728")

    def test_build_execution_plan_rejects_unsafe_burner_serial_for_strict_scripts(self):
        script = build_script({}, name="pwlink_v2_arm_mcu_flash")

        with self.assertRaises(HTTPException) as context:
            build_execution_plan(
                build_task(),
                {"target_chip": "STM32H743ZIT6", "start_address": "0x08000000"},
                None,
                build_burner(sn='PW-001"&calc'),
                script,
                used_file_path="firmware.bin",
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("BURNER_SN", context.exception.detail)


if __name__ == "__main__":
    unittest.main()
