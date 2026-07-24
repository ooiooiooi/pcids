import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.routers import burners


class _FakeQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []


class _FakeDB:
    def query(self, *_args, **_kwargs):
        return _FakeQuery()


class BurnerServerNodeDisplayTests(unittest.TestCase):
    def test_resolve_node_display_marks_configured_server_as_server(self):
        burner = SimpleNamespace(
            host_type="local",
            agent_url=None,
            host_name=None,
            host_address="192.168.0.117",
        )
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.0.50"))

        with patch(
            "backend.routers.burners._get_repository_server_transport_config",
            return_value={"host": "192.168.0.117"},
        ):
            display = burners._resolve_node_display(burner, request)

        self.assertEqual(display["label"], "服务器")
        self.assertFalse(display["is_local"])

    def test_discover_scan_nodes_exposes_service_node_as_server(self):
        fake_db = _FakeDB()

        with (
            patch(
                "backend.routers.burners._get_repository_server_transport_config",
                return_value={"host": "192.168.0.117"},
            ),
            patch("backend.routers.burners._get_service_node_address", return_value="192.168.0.117"),
            patch("backend.routers.burners._discover_lan_agent_urls", return_value=[]),
        ):
            nodes = burners._discover_scan_nodes(fake_db, "all")

        self.assertEqual(nodes[0]["node_type"], "server")
        self.assertEqual(nodes[0]["node_label"], "服务器")

    def test_burner_to_dict_reports_server_host_type_for_configured_server_ip(self):
        burner = SimpleNamespace(
            id=1,
            name="J-LINK",
            type="J-LINK",
            sn="123",
            port="USB1",
            location="USB1",
            host_type="local",
            host_name=None,
            host_address="192.168.0.117",
            agent_url=None,
            strategy=1,
            is_enabled=True,
            status=0,
            description=None,
            config_json=None,
            modified_by=None,
            created_at=None,
            updated_at=None,
        )

        with patch(
            "backend.routers.burners._get_repository_server_transport_config",
            return_value={"host": "192.168.0.117"},
        ):
            payload = burners.burner_to_dict(burner)

        self.assertEqual(payload["host_type"], "server")
        self.assertEqual(payload["node_display_label"], "服务器")

    def test_resolve_node_display_uses_non_server_ip_even_for_agent(self):
        burner = SimpleNamespace(
            host_type="agent",
            agent_url="http://192.168.0.50:8000",
            host_name="旧节点名称",
            host_address="192.168.0.50",
        )
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.0.10"))

        with patch(
            "backend.routers.burners._get_repository_server_transport_config",
            return_value={"host": "192.168.0.117"},
        ):
            display = burners._resolve_node_display(burner, request)

        self.assertEqual(display["label"], "192.168.0.50")

    def test_resolve_node_display_marks_current_machine_as_local_before_server_label(self):
        burner = SimpleNamespace(
            host_type="server",
            agent_url=None,
            host_name=None,
            host_address="192.168.0.117",
        )
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.0.117"))

        with patch(
            "backend.routers.burners._get_repository_server_transport_config",
            return_value={"host": "192.168.0.117"},
        ):
            display = burners._resolve_node_display(burner, request)

        self.assertEqual(display["label"], "本地")
        self.assertTrue(display["is_local"])


if __name__ == "__main__":
    unittest.main()
