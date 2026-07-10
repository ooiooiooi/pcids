import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, ProtocolLog, ProtocolSession, User
from backend.routers import protocol_tests


class FakePortInfo:
    def __init__(
        self,
        *,
        device: str,
        description: str,
        manufacturer: str,
        vid: int | None,
        pid: int | None,
        serial_number: str = "",
        hwid: str = "",
    ):
        self.device = device
        self.name = device
        self.description = description
        self.manufacturer = manufacturer
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number
        self.hwid = hwid
        self.product = ""
        self.interface = ""
        self.location = ""


class GpioProtocolBatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        self.db = self.SessionLocal()
        self.user = User(username="admin", password_hash="x", status=1)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.runtime_config_path = os.path.join(self._temp_dir.name, "gpio_runtime.json")
        with open(self.runtime_config_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "transport": {
                        "kind": "serial",
                        "com_port": "COM9",
                        "baud_rate": 115200,
                        "data_type": "ASCII",
                    },
                    "supports_readback": True,
                    "channel_options": ["GPIO0", "GPIO1", "GPIO2"],
                    "actions": {
                        "set_level": {
                            "levels": {
                                "high": {
                                    "request": {"data": "SET {pin} HIGH"},
                                    "reply": {"required": True, "high_pattern": "HIGH"},
                                },
                                "low": {
                                    "request": {"data": "SET {pin} LOW"},
                                    "reply": {"required": True, "low_pattern": "LOW"},
                                },
                            }
                        },
                        "read_level": {
                            "request": {"data": "READ {pin}"},
                            "reply": {"required": True, "high_pattern": "HIGH", "low_pattern": "LOW"},
                        },
                    },
                },
                fh,
                ensure_ascii=False,
            )
        self.env_patcher = patch.dict(os.environ, {"PCIDS_GPIO_RUNTIME_CONFIG": self.runtime_config_path}, clear=False)
        self.env_patcher.start()

    def tearDown(self):
        protocol_tests._close_all_serial_session_connections()
        protocol_tests._close_all_can_session_connections()
        self.env_patcher.stop()
        self._temp_dir.cleanup()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_gpio_session(self) -> ProtocolSession:
        session = ProtocolSession(
            created_by_user_id=self.user.id,
            task_no="PT202607070001",
            target="STM32M",
            protocol="gpio_io",
            config_json=json.dumps({"pin": "GPIO0", "mode": "输出"}, ensure_ascii=False),
            status=1,
            tx_count=0,
            rx_count=0,
            executor=self.user.username,
            ip_address="127.0.0.1",
            created_at=datetime.now(),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        protocol_tests._store_serial_session_connection(session.id, object())
        return session

    @staticmethod
    def _fake_serial_exchange(_port, payload_bytes, **_kwargs):
        payload_text = payload_bytes.decode("utf-8", errors="ignore")
        if payload_text == "SET GPIO0 LOW":
            return "LOW", 3
        if payload_text == "SET GPIO1 HIGH":
            return "HIGH", 4
        if payload_text == "READ GPIO0":
            return "LOW", 3
        raise AssertionError(f"unexpected payload: {payload_text}")

    def test_gpio_batch_write_records_each_pin_and_updates_summary(self):
        session = self._create_gpio_session()
        payload = protocol_tests.SendRequest(
            frame_id="GPIO-BATCH",
            config={
                "action": "batch_write",
                "mode": "输出",
                "batch_items": [
                    {"pin": "GPIO0", "selected": True, "mode": "输出", "target_level": "低电平"},
                    {"pin": "GPIO1", "selected": True, "mode": "输出", "target_level": "高电平"},
                    {"pin": "GPIO2", "selected": False, "mode": "输出", "target_level": "高电平"},
                ],
            },
        )

        with patch("backend.routers.protocol_tests._run_serial_exchange", side_effect=self._fake_serial_exchange), patch(
            "backend.routers.protocol_tests._notify_protocol_result", return_value=None
        ):
            result = asyncio.run(protocol_tests.send_frame(session.id, payload, self.db, self.user, None))

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["message"], "GPIO 批量操作完成")
        self.assertEqual(len(result["data"]["items"]), 2)

        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).first()
        config = json.loads(refreshed.config_json)
        self.assertEqual(refreshed.tx_count, 2)
        self.assertEqual(refreshed.rx_count, 2)
        self.assertEqual(config["validation_result"], "passed")
        self.assertEqual(config["validation_code"], "gpio_batch_passed")

        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).order_by(ProtocolLog.id.asc()).all()
        self.assertEqual([log.direction for log in logs[:4]], ["Tx", "Rx", "Tx", "Rx"])
        self.assertIn("批量下发", logs[0].data)
        self.assertIn("回读值", logs[1].data)

    def test_gpio_batch_read_can_return_mismatch_result(self):
        session = self._create_gpio_session()
        payload = protocol_tests.SendRequest(
            frame_id="GPIO-BATCH",
            config={
                "action": "batch_read",
                "mode": "输入 (单次读取)",
                "batch_items": [
                    {"pin": "GPIO0", "selected": True, "mode": "输入", "expected_level": "高电平"},
                ],
            },
        )

        with patch("backend.routers.protocol_tests._run_serial_exchange", side_effect=self._fake_serial_exchange), patch(
            "backend.routers.protocol_tests._notify_protocol_result", return_value=None
        ):
            result = asyncio.run(protocol_tests.send_frame(session.id, payload, self.db, self.user, None))

        self.assertEqual(result["data"]["items"][0]["current_level"], "低电平")
        self.assertEqual(result["data"]["items"][0]["result"], "未通过")
        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).first()
        config = json.loads(refreshed.config_json)
        self.assertEqual(config["validation_result"], "failed")
        self.assertEqual(config["validation_code"], "gpio_batch_failed")

    def test_gpio_channel_scan_returns_real_wch_serial_ports_without_runtime_config(self):
        ports = [
            FakePortInfo(
                device="COM11",
                description="USB-Enhanced-SERIAL-A CH344 (COM11)",
                manufacturer="wch.cn",
                vid=0x1A86,
                pid=0x55D5,
                serial_number="0123456789",
                hwid="USB VID:PID=1A86:55D5 SER=0123456789",
            ),
            FakePortInfo(
                device="COM1",
                description="Standard Serial Port",
                manufacturer="Microsoft",
                vid=None,
                pid=None,
            ),
        ]
        missing_path = os.path.join(self._temp_dir.name, "missing_gpio_runtime.json")
        with patch.dict(os.environ, {"PCIDS_GPIO_RUNTIME_CONFIG": missing_path}, clear=False), patch(
            "backend.routers.protocol_tests.list_ports"
        ) as fake_list_ports:
            fake_list_ports.comports.return_value = ports
            config, logs = protocol_tests._build_auto_channel_config("gpio_io")

        self.assertEqual(config["wch_serial_ports"], ["COM11"])
        self.assertEqual(config["wch_serial_devices"][0]["chip"], "CH344")
        self.assertTrue(config["gpio_runtime_ready"])
        self.assertEqual(config["gpio_transport_kind"], "wch_gpio")
        self.assertEqual(config["gpio_transport_config"]["com_port"], "COM11")
        self.assertEqual(config["gpio_transport_config"]["pin_base_index"], 0)
        self.assertIn("已检测到 1 个 WCH 串口设备", logs)

    def test_gpio_connect_request_defaults_wch_pin_base_index_to_zero(self):
        merged = protocol_tests._merge_connect_request_config(
            "gpio_io",
            {"gpio_transport_config": {"kind": "wch_gpio"}},
            {"wch_serial_port": "COM11"},
        )

        self.assertEqual(merged["gpio_transport_kind"], "wch_gpio")
        self.assertEqual(merged["gpio_transport_config"]["com_port"], "COM11")
        self.assertEqual(merged["gpio_transport_config"]["pin_base_index"], 0)

    def test_ensure_gpio_runtime_config_keeps_wch_zero_offset(self):
        merged = protocol_tests._ensure_gpio_runtime_config(
            {
                "wch_serial_port": "COM11",
                "gpio_transport_kind": "wch_gpio",
                "gpio_transport_config": {"kind": "wch_gpio", "com_port": "COM11", "pin_base_index": 0},
            }
        )

        self.assertEqual(merged["gpio_transport_kind"], "wch_gpio")
        self.assertEqual(merged["gpio_transport_config"]["pin_base_index"], 0)


if __name__ == "__main__":
    unittest.main()
