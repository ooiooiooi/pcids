import json
import unittest
from datetime import datetime

from backend.models import ProtocolLog, ProtocolSession
from backend.routers.protocol_tests import (
    _build_protocol_config,
    _build_protocol_report_html,
    _render_gpio_batch_items,
    _render_protocol_logs,
)


class ProtocolReportFieldsTests(unittest.TestCase):
    def _build_ethernet_session(self, transport_protocol: str = "TCP Client") -> ProtocolSession:
        timestamp = datetime(2026, 6, 12, 15, 13, 0)
        return ProtocolSession(
            id=10,
            task_no="202606121010",
            target="Protocol-Test-Board",
            protocol="ethernet",
            config_json=json.dumps(
                {
                    "transport_protocol": transport_protocol,
                    "local_ip": "192.168.0.113",
                    "local_port": 8080,
                    "target_ip": "192.168.0.18",
                    "target_port": 8080,
                    "timeout": 3000,
                    "data_type": "HEX",
                }
            ),
            status=2,
            tx_count=1,
            rx_count=0,
            executor="admin",
            ip_address="127.0.0.1",
            created_at=timestamp,
            updated_at=timestamp,
        )

    def test_tcp_client_report_hides_auto_detected_local_ip_and_keeps_full_mode(self):
        html = _build_protocol_report_html(self._build_ethernet_session(), [], False)

        self.assertIn("连接方式 TCP Client", html)
        self.assertNotIn("上位机 IP 192.168.0.113", html)
        self.assertNotIn("上位机 IP 127.0.0.1", html)

    def test_tcp_client_report_uses_whitelist_and_hides_auto_detected_local_ip(self):
        session = self._build_ethernet_session()
        config = json.loads(session.config_json)

        report_config = _build_protocol_config(session, "ethernet", config, [])

        self.assertEqual(
            list(report_config),
            ["传输协议", "目标 IP", "目标端口", "超时时间 (ms)", "数据类型"],
        )
        self.assertNotIn("本地 IP", report_config)
        self.assertEqual(report_config["超时时间 (ms)"], "3000")
        self.assertEqual(report_config["数据类型"], "HEX")

    def test_tcp_server_environment_uses_full_mode_and_local_listen_address(self):
        session = self._build_ethernet_session("TCP Server")
        config = json.loads(session.config_json)
        config["listen_port"] = 9000
        session.config_json = json.dumps(config)

        html = _build_protocol_report_html(session, [], False)

        self.assertIn("连接方式 TCP Server", html)
        self.assertIn("监听地址 192.168.0.113:9000", html)

        report_config = _build_protocol_config(session, "ethernet", config, [])
        self.assertEqual(
            list(report_config),
            ["传输协议", "本地 IP", "监听端口", "超时时间 (ms)", "数据类型"],
        )

    def test_udp_report_keeps_only_core_fields(self):
        session = self._build_ethernet_session("UDP")
        config = json.loads(session.config_json)

        report_config = _build_protocol_config(session, "ethernet", config, [])

        self.assertEqual(
            list(report_config),
            ["传输协议", "本地 IP", "本地端口", "目标 IP", "目标端口", "超时时间 (ms)", "数据类型"],
        )

    def test_can_report_excludes_values_that_change_between_multiple_sends(self):
        session = ProtocolSession(protocol="can", ip_address="127.0.0.1")
        config = {
            "physical_channel": "CAN0",
            "baud_rate": "500kbps",
            "id_format": "标准帧(11位)",
            "remote_frame": False,
            "termination_enabled": True,
            "data_type": "HEX",
            "frame_id": "0x999",
            "data_length": 8,
            "data": "FF",
        }
        logs = [
            ProtocolLog(direction="Tx", frame_id="0x123", dlc=1, data="AA"),
            ProtocolLog(direction="Tx", frame_id="0x456", dlc=2, data="BB CC"),
        ]

        report_config = _build_protocol_config(session, "can", config, logs)

        self.assertEqual(
            list(report_config),
            ["物理通道", "波特率", "标识符格式", "远程帧", "内部120Ω终端电阻", "数据类型"],
        )
        self.assertNotIn("帧 ID", report_config)
        self.assertNotIn("默认数据", report_config)

    def test_ethernet_report_logs_use_each_persisted_route(self):
        session = self._build_ethernet_session()
        config = json.loads(session.config_json)
        logs = [
            ProtocolLog(
                timestamp=datetime(2026, 6, 12, 15, 13, 1),
                direction="Tx",
                frame_id="192.168.0.113:53124>192.168.0.18:8080",
                dlc=1,
                data="AA",
            ),
            ProtocolLog(
                timestamp=datetime(2026, 6, 12, 15, 13, 2),
                direction="Rx",
                frame_id="192.168.0.18:8080>192.168.0.113:53124",
                dlc=1,
                data="BB",
            ),
        ]

        table_html = _render_protocol_logs("ethernet", session, config, logs)

        self.assertIn("192.168.0.113:53124", table_html)
        self.assertIn("192.168.0.18:8080", table_html)
        self.assertIn(">TCP<", table_html)
        self.assertNotIn(">TCP Client<", table_html)

    def test_gpio_batch_items_render_as_structured_report_table(self):
        html = _render_gpio_batch_items(
            {
                "mode": "输出",
                "batch_items": [
                    {
                        "pin": "GPIO0",
                        "mode": "输出",
                        "target_level": "高电平",
                        "current_level": "高电平",
                        "result": "通过",
                        "passed": True,
                    }
                ],
            }
        )

        self.assertIn("GPIO 批量验证明细", html)
        self.assertIn("GPIO0", html)
        self.assertIn("目标/期望电平", html)
        self.assertNotIn("batch_items", html)


if __name__ == "__main__":
    unittest.main()
