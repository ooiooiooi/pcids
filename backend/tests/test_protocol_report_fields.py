import json
import unittest
from datetime import datetime

from backend.models import ProtocolSession
from backend.routers.protocol_tests import _build_protocol_config, _build_protocol_report_html


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

    def test_report_environment_uses_configured_local_ip_and_full_connection_mode(self):
        html = _build_protocol_report_html(self._build_ethernet_session(), [], False)

        self.assertIn("上位机 IP 192.168.0.113", html)
        self.assertIn("连接方式 TCP Client", html)
        self.assertNotIn("上位机 IP 127.0.0.1", html)

    def test_ethernet_timeout_includes_millisecond_unit(self):
        session = self._build_ethernet_session()
        config = json.loads(session.config_json)

        report_config = _build_protocol_config(session, "ethernet", config, [])

        self.assertEqual(
            list(report_config),
            ["传输协议", "本地 IP", "目标 IP", "目标端口", "超时时间 (ms)", "数据类型"],
        )
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


if __name__ == "__main__":
    unittest.main()
