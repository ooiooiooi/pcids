import asyncio
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
    def test_service_node_address_prefers_configured_local_server_interface(self):
        with (
            patch(
                "backend.routers.burners._get_repository_server_transport_config",
                return_value={"host": "192.168.137.1"},
            ),
            patch(
                "backend.routers.burners._get_service_node_addresses",
                return_value={"127.0.0.1", "192.168.1.198", "192.168.137.1", "198.18.0.1"},
            ),
        ):
            address = burners._get_service_node_address()

        self.assertEqual(address, "192.168.137.1")

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

    def test_stale_server_host_type_uses_ip_when_address_is_not_configured_server(self):
        burner = SimpleNamespace(
            id=2,
            name="GDLINK",
            type="GDLINK",
            sn="",
            port="Port_#0001.Hub_#0004",
            location=None,
            host_type="server",
            host_name=None,
            host_address="192.168.0.50",
            agent_url=None,
            strategy=2,
            is_enabled=True,
            status=1,
            description=None,
            config_json=None,
            modified_by=None,
            created_at=None,
            updated_at=None,
        )
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.0.10"))

        with patch(
            "backend.routers.burners._get_repository_server_transport_config",
            return_value={"host": "192.168.0.117"},
        ):
            payload = burners.burner_to_dict(burner, request=request)

        self.assertEqual(payload["host_type"], "local")
        self.assertEqual(payload["node_display_label"], "192.168.0.50")

    def test_resolve_node_display_keeps_configured_server_label_on_current_machine(self):
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

        self.assertEqual(display["label"], "服务器")
        self.assertFalse(display["is_local"])

    def test_derive_node_label_keeps_configured_server_label_on_service_machine(self):
        with (
            patch(
                "backend.routers.burners._get_repository_server_transport_config",
                return_value={"host": "192.168.0.117"},
            ),
            patch("backend.routers.burners._get_service_node_address", return_value="192.168.0.117"),
        ):
            label = burners._derive_node_label(None, host_address="192.168.0.117")

        self.assertEqual(label, "服务器")

    def test_runtime_status_scans_self_agent_locally_without_recursive_http(self):
        burner = SimpleNamespace(
            id=7,
            name="GDLINK",
            type="GDLINK",
            sn="3835388D0655",
            port="Port_#0001.Hub_#0004",
            location="Port_#0001.Hub_#0004",
            strategy=1,
            is_enabled=True,
            status=1,
            agent_url="http://192.168.137.2:8000",
        )

        with (
            patch("backend.routers.burners._get_service_node_address", return_value="192.168.137.2"),
            patch("backend.routers.burners._remote_scan_burner") as remote_scan,
            patch(
                "backend.routers.burners._build_scan_result",
                return_value={"online": True, "sn": burner.sn, "port": burner.port},
            ) as local_scan,
        ):
            status = burners._compute_burner_runtime_status(burner, set(), [{"DeviceID": "USB"}])

        self.assertEqual(status, 0)
        remote_scan.assert_not_called()
        local_scan.assert_called_once()

    def test_consecutive_runtime_refreshes_do_not_reuse_business_state(self):
        burner = SimpleNamespace(
            id=8,
            name="J-LINK",
            type="J-LINK",
            sn="000941000029",
            port="Port_#0001.Hub_#0001",
            location="Port_#0001.Hub_#0001",
            host_address="192.168.137.2",
            strategy=1,
            is_enabled=True,
            status=1,
            agent_url="http://192.168.137.2:8000",
        )
        async def run_refreshes():
            first = await burners._compute_burner_runtime_statuses([burner], set())
            second = await burners._compute_burner_runtime_statuses([burner], set())
            return first, second

        with (
            patch("backend.routers.burners._get_service_node_address", return_value="192.168.137.2"),
            patch("backend.routers.burners._refresh_windows_pnp_state", return_value=True) as refresh,
            patch("backend.routers.burners._probe_usb_devices", return_value=[{"DeviceID": "USB"}]) as probe,
            patch("backend.routers.burners._safe_compute_burner_runtime_status", return_value=0) as compute,
        ):
            first, second = asyncio.run(run_refreshes())

        self.assertEqual(first, second)
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(compute.call_count, 2)
        for call in probe.call_args_list:
            self.assertTrue(call.kwargs.get("force_refresh"))


if __name__ == "__main__":
    unittest.main()
