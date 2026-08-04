import asyncio
import json
import os
import socket
import tempfile
import threading
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.routers.burners import (
    DEVICE_SCAN_PRIORITY,
    LOCATION_PROBE_CANDIDATE_TYPE,
    _build_discovery_payload,
    _build_scan_result,
    _candidate_matches_registered_identity,
    _compute_burner_cached_status,
    _discover_lan_agent_urls,
    _discover_scan_nodes,
    _find_discovery_binding_candidate,
    _get_lan_agent_scan_networks,
    _normalize_agent_url,
    _probe_stlink_serials,
    _refresh_registered_burner_statuses,
    _resolve_ambiguous_candidate_types,
    _resolve_discovery_status_updates,
    _scan_discovery_nodes_async,
)


class BurnerDiscoverySelectionTests(unittest.TestCase):
    def setUp(self):
        self.burners = [
            SimpleNamespace(
                id=index,
                name=f"{device_type} #{index}",
                type=device_type,
                sn=f"SN{index:03d}",
                port=f"USB\\DEVICE{index:03d}",
                strategy=1,
                agent_url=None,
                host_address="127.0.0.1",
                host_name=None,
            )
            for index, device_type in enumerate(DEVICE_SCAN_PRIORITY, start=1)
        ]
        self.candidates = [
            {
                "candidate_id": f"candidate-{burner.id}",
                "type": burner.type,
                "device_category": "burner",
                "sn": burner.sn,
                "port": burner.port,
                "node_key": "127.0.0.1",
                "node_type": "local",
                "node_label": "本地",
            }
            for burner in self.burners
        ]
        self.db = MagicMock()
        self.db.query.return_value.all.return_value = self.burners

    def _payload(self, editing_burner_id=None):
        with (
            patch("backend.routers.burners._discover_scan_nodes", return_value=[{"node_type": "local"}]),
            patch("backend.routers.burners._discover_local_candidates", return_value=self.candidates),
        ):
            return _build_discovery_payload(self.db, "local", editing_burner_id=editing_burner_id)

    def test_create_mode_excludes_every_registered_device_type(self):
        payload = self._payload()

        self.assertEqual(payload["selectable_devices"], [])
        self.assertEqual(payload["unregistered_devices"], [])

    def test_edit_mode_only_allows_the_current_device_for_every_type(self):
        for burner in self.burners:
            with self.subTest(device_type=burner.type):
                payload = self._payload(editing_burner_id=burner.id)
                selectable_ids = [item["candidate_id"] for item in payload["selectable_devices"]]
                self.assertEqual(selectable_ids, [f"candidate-{burner.id}"])

    def test_leading_zero_serial_difference_is_not_reported_as_binding_change(self):
        burner = self.burners[0]
        burner.type = "J-LINK"
        burner.sn = "941000029"
        burner.port = r"USB\VID_1366&PID_0105\000941000029"
        candidate = self.candidates[0]
        candidate["type"] = "J-LINK"
        candidate["sn"] = "000941000029"
        candidate["port"] = r"usb\vid_1366&pid_0105\000941000029"

        payload = self._payload()

        changed_ids = [item["burner_id"] for item in payload["changed_bindings"]]
        self.assertNotIn(burner.id, changed_ids)
        self.assertEqual(payload["unregistered_devices"], [])

    def test_non_sn_strategy_still_reports_binding_change_when_port_changes(self):
        burner = self.burners[0]
        burner.type = "PWLINK2"
        burner.strategy = 2
        burner.sn = "42742761BAA116B9D7012DB4810082D1"
        burner.port = r"USB\VID_0D28&PID_0204\OLD-PORT"
        burner.config_json = json.dumps({"supported_interfaces": ["SWD"]})
        candidate = self.candidates[0]
        candidate["type"] = "PWLINK2"
        candidate["sn"] = "42742761BAA116B9D7012DB4810082D1"
        candidate["port"] = r"USB\VID_0D28&PID_0204\NEW-PORT"

        payload = self._payload()

        self.assertEqual(len(payload["changed_bindings"]), 1)
        self.assertEqual(payload["changed_bindings"][0]["burner_id"], burner.id)
        self.assertEqual(payload["changed_bindings"][0]["burner_config_json"], burner.config_json)
        self.assertEqual(payload["changed_bindings"][0]["original_binding"]["port"], r"USB\VID_0D28&PID_0204\OLD-PORT")
        self.assertEqual(payload["changed_bindings"][0]["current_binding"]["port"], r"USB\VID_0D28&PID_0204\NEW-PORT")

    def test_strategy_two_probe_only_same_port_same_node_stays_offline(self):
        burner = self.burners[0]
        burner.type = "Gowin USB Cable"
        burner.strategy = 2
        burner.sn = "REGISTERED-SN"
        burner.port = r"USB\VID_1234&PID_ABCD\PORT-1"
        burner.host_address = "192.168.0.18"
        burner.is_enabled = True
        candidate = {
            "candidate_id": "unknown-on-bound-port",
            "type": LOCATION_PROBE_CANDIDATE_TYPE,
            "device_category": "probe_only",
            "detected_name": "USB Composite Device",
            "sn": None,
            "port": r"USB\VID_1234&PID_ABCD\PORT-1",
            "node_key": "192.168.0.18",
            "node_type": "local",
            "node_label": "local",
            "probe_only": True,
        }

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())

        self.assertEqual(updates, [{"id": burner.id, "status": 1}])

    def test_strategy_two_probe_only_exact_scanned_identity_is_online(self):
        burner = self.burners[0]
        burner.type = "Gowin USB Cable"
        burner.strategy = 2
        burner.sn = ""
        burner.port = r"USB\VID_1234&PID_ABCD\6&123ABC&0&3"
        burner.host_address = "192.168.0.18"
        burner.is_enabled = True
        burner.config_json = json.dumps(
            {
                "usb_binding": {
                    "pnp_device_id": burner.port,
                    "container_id": "{B64FBDBE-6EBD-48BA-8BE8-E6957653C04B}",
                    "vendor_id": "1234",
                    "product_id": "ABCD",
                }
            }
        )
        candidate = {
            "candidate_id": "explicitly-bound-generic-device",
            "type": LOCATION_PROBE_CANDIDATE_TYPE,
            "device_category": "probe_only",
            "detected_name": "USB Composite Device",
            "sn": None,
            "port": burner.port,
            "node_key": "192.168.0.18",
            "node_type": "local",
            "node_label": "local",
            "probe_only": True,
            "usb_binding": json.loads(burner.config_json)["usb_binding"],
        }

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())

        self.assertEqual(updates, [{"id": burner.id, "status": 0}])

    def test_registered_probe_only_exact_identity_is_not_offered_as_new_device(self):
        burner = self.burners[0]
        burner.type = "Gowin USB Cable"
        burner.strategy = 2
        burner.sn = ""
        burner.port = r"USB\VID_1234&PID_ABCD\6&123ABC&0&3"
        burner.host_address = "127.0.0.1"
        burner.config_json = json.dumps(
            {
                "usb_binding": {
                    "pnp_device_id": burner.port,
                    "container_id": "{B64FBDBE-6EBD-48BA-8BE8-E6957653C04B}",
                    "vendor_id": "1234",
                    "product_id": "ABCD",
                }
            }
        )
        self.candidates = [
            {
                "candidate_id": "already-bound-generic-device",
                "type": LOCATION_PROBE_CANDIDATE_TYPE,
                "device_category": "probe_only",
                "detected_name": "USB Composite Device",
                "sn": None,
                "port": burner.port,
                "node_key": "127.0.0.1",
                "node_type": "local",
                "node_label": "local",
                "probe_only": True,
                "usb_binding": json.loads(burner.config_json)["usb_binding"],
            }
        ]

        payload = self._payload()

        self.assertEqual(payload["probe_only_devices"], [])

    def test_unplugged_al321_does_not_inherit_ch340_on_shared_parent_hub(self):
        burner = self.burners[0]
        burner.type = "AL321"
        burner.strategy = 2
        burner.sn = "210512180081"
        burner.port = "Port_#0001.Hub_#0005"
        burner.host_address = "192.168.137.2"
        burner.is_enabled = True
        burner.config_json = json.dumps(
            {
                "usb_binding": {
                    "location_info": "Port_#0001.Hub_#0005",
                    "pnp_device_id": r"USB\VID_0403&PID_6014\210512180081",
                    "parent_id": r"USB\VID_2DC0&PID_2041\6&21F9C627&0&4",
                    "container_id": "{ACE66FC9-631E-11F1-AB90-806E6F6E6963}",
                    "vendor_id": "0403",
                    "product_id": "6014",
                }
            }
        )
        candidate = {
            "candidate_id": "ch340-on-shared-parent",
            "type": LOCATION_PROBE_CANDIDATE_TYPE,
            "device_category": "probe_only",
            "detected_name": "USB-SERIAL CH340 (COM18)",
            "sn": None,
            "port": "Port_#0004.Hub_#0005",
            "alternative_ports": [r"USB\VID_2DC0&PID_2041\6&21F9C627&0&4"],
            "usb_binding": {
                "pnp_device_id": r"USB\VID_1A86&PID_7523\7&1ED2188&0&4",
                "parent_id": r"USB\VID_2DC0&PID_2041\6&21F9C627&0&4",
                "container_id": "{ACE66FC9-631E-11F1-AB90-806E6F6E6963}",
                "vendor_id": "1A86",
                "product_id": "7523",
            },
            "node_key": "192.168.137.2",
            "node_type": "local",
            "node_label": "local",
            "host_address": "192.168.137.2",
            "probe_only": True,
        }
        self.db.query.return_value.all.return_value = [burner]

        with (
            patch("backend.routers.burners._discover_scan_nodes", return_value=[{"node_type": "local"}]),
            patch("backend.routers.burners._discover_local_candidates", return_value=[candidate]),
        ):
            payload = _build_discovery_payload(self.db, "local")

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())
        self.assertEqual(payload["changed_bindings"], [])
        self.assertEqual(updates, [{"id": burner.id, "status": 1}])

    def test_physical_port_strategy_ignores_serial_metadata_at_same_port(self):
        burner = self.burners[0]
        burner.type = "J-LINK"
        burner.strategy = 2
        burner.sn = "000941000029"
        burner.port = "Port_#0001.Hub_#0001"
        burner.host_address = "192.168.137.2"
        burner.is_enabled = True
        candidate = {
            "candidate_id": "different-jlink",
            "type": "J-LINK",
            "device_category": "burner",
            "detected_name": "J-Link",
            "sn": "000941000030",
            "port": "Port_#0001.Hub_#0001",
            "vendor_id": "1366",
            "product_id": "0105",
            "node_key": "192.168.137.2",
            "node_type": "local",
            "node_label": "local",
            "probe_only": False,
        }

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())
        self.assertEqual(updates, [{"id": burner.id, "status": 0}])

    def test_serial_mismatch_is_rejected_before_node_resolution(self):
        burner = self.burners[0]
        candidate = {
            "candidate_id": "wrong-serial",
            "type": burner.type,
            "sn": "DIFFERENT-SERIAL",
            "port": burner.port,
            "node_key": "slow-hostname.example.invalid",
            "probe_only": False,
        }

        with patch(
            "backend.routers.burners._is_same_burner_candidate_node",
            side_effect=AssertionError("node resolution should not run for a serial mismatch"),
        ):
            self.assertFalse(
                _candidate_matches_registered_identity(
                    burner,
                    candidate,
                    require_same_node=True,
                )
            )
            self.assertIsNone(_find_discovery_binding_candidate(burner, [candidate]))

    def test_serialless_same_type_on_shared_hub_but_different_port_does_not_match(self):
        shared_parent = r"USB\VID_0BDA&PID_5411\8&SHARED&0&4"
        burner = self.burners[0]
        burner.type = "XDS510plus"
        burner.strategy = 2
        burner.sn = ""
        burner.port = "Port_#0002.Hub_#0002"
        burner.host_address = "192.168.137.2"
        burner.is_enabled = True
        burner.config_json = json.dumps(
            {
                "usb_binding": {
                    "location_info": "Port_#0002.Hub_#0002",
                    "pnp_device_id": r"USB\VID_0547&PID_1020\OLD",
                    "parent_id": shared_parent,
                }
            }
        )
        candidate = {
            "candidate_id": "other-xds-on-shared-hub",
            "type": "XDS510plus",
            "device_category": "burner",
            "detected_name": "SEED USB2.0 PLUS Emulator",
            "sn": None,
            "port": "Port_#0003.Hub_#0002",
            "alternative_ports": [shared_parent],
            "usb_binding": {
                "location_info": "Port_#0003.Hub_#0002",
                "pnp_device_id": r"USB\VID_0547&PID_1020\NEW",
                "parent_id": shared_parent,
            },
            "node_key": "192.168.137.2",
            "node_type": "local",
            "node_label": "local",
            "probe_only": False,
        }

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())
        self.assertEqual(updates, [{"id": burner.id, "status": 1}])

    def test_same_short_port_on_another_node_remains_unregistered_and_selectable(self):
        burner = self.burners[0]
        burner.type = "XDS510plus"
        burner.strategy = 2
        burner.sn = ""
        burner.port = "Port_#0002.Hub_#0002"
        burner.host_address = "10.0.0.8"
        burner.agent_url = "http://10.0.0.8:8000"
        burner.config_json = "{}"
        candidate = {
            "candidate_id": "same-port-other-node",
            "type": "XDS510plus",
            "device_category": "burner",
            "detected_name": "SEED USB2.0 PLUS Emulator",
            "sn": None,
            "port": "Port_#0002.Hub_#0002",
            "node_key": "http://10.0.0.9:8000",
            "node_type": "agent",
            "node_label": "10.0.0.9",
            "agent_url": "http://10.0.0.9:8000",
            "host_address": "10.0.0.9",
            "probe_only": False,
        }
        self.db.query.return_value.all.return_value = [burner]

        with (
            patch(
                "backend.routers.burners._discover_scan_nodes",
                return_value=[{"node_type": "agent", "agent_url": "http://10.0.0.9:8000"}],
            ),
            patch("backend.routers.burners._scan_discovery_node", return_value=[candidate]),
        ):
            payload = _build_discovery_payload(self.db, "all")

        self.assertEqual(payload["unregistered_devices"], [candidate])
        self.assertEqual(payload["selectable_devices"], [candidate])

    def test_probe_only_candidate_is_not_selectable_or_unregistered(self):
        burner = self.burners[0]
        burner.type = "Gowin USB Cable"
        burner.strategy = 2
        burner.sn = "OLD-SN"
        burner.port = r"USB\VID_1234&PID_ABCD\PORT-1"
        burner.host_address = "192.168.0.18"
        burner.is_enabled = True
        self.db.query.return_value.all.return_value = [burner]
        candidate = {
            "candidate_id": "unknown-on-bound-port",
            "type": LOCATION_PROBE_CANDIDATE_TYPE,
            "device_category": "probe_only",
            "detected_name": "USB Composite Device",
            "sn": "NEW-SN",
            "port": r"USB\VID_1234&PID_ABCD\PORT-1",
            "node_key": "192.168.0.18",
            "node_type": "local",
            "node_label": "local",
            "probe_only": True,
        }

        with (
            patch("backend.routers.burners._discover_scan_nodes", return_value=[{"node_type": "local"}]),
            patch("backend.routers.burners._discover_local_candidates", return_value=[candidate]),
        ):
            payload = _build_discovery_payload(self.db, "local")

        self.assertEqual(payload["selectable_devices"], [])
        self.assertEqual(payload["unregistered_devices"], [])
        self.assertEqual(payload["probe_only_devices"], [candidate])
        self.assertEqual(payload["total_probe_only"], 1)
        self.assertEqual(payload["changed_bindings"], [])

    def test_strategy_two_same_port_same_node_ignores_serial_change(self):
        burner = self.burners[0]
        burner.type = "PWLINK2"
        burner.strategy = 2
        burner.sn = "OLD-SN"
        burner.port = r"USB\VID_0D28&PID_0204\PORT-1"
        candidate = self.candidates[0]
        candidate["type"] = "PWLINK2"
        candidate["sn"] = "NEW-SN"
        candidate["port"] = r"USB\VID_0D28&PID_0204\PORT-1"

        payload = self._payload()

        changed_ids = [item["burner_id"] for item in payload["changed_bindings"]]
        self.assertNotIn(burner.id, changed_ids)

    def test_strategy_two_same_local_port_ignores_stale_local_node_address(self):
        burner = self.burners[0]
        burner.type = "XDS510plus"
        burner.strategy = 2
        burner.sn = ""
        burner.port = "Port_#0002.Hub_#0002"
        burner.agent_url = None
        burner.host_address = "172.20.10.7"
        candidate = self.candidates[0]
        candidate["type"] = "XDS510plus"
        candidate["sn"] = ""
        candidate["port"] = "Port_#0002.Hub_#0002"
        candidate["node_key"] = "127.0.0.1"
        candidate["node_type"] = "local"
        candidate["node_label"] = "本地"
        candidate["agent_url"] = None
        candidate["host_address"] = "127.0.0.1"

        payload = self._payload()

        changed_ids = [item["burner_id"] for item in payload["changed_bindings"]]
        self.assertNotIn(burner.id, changed_ids)

    def test_strategy_one_known_type_and_serial_still_matches(self):
        burner = self.burners[0]
        burner.type = "J-LINK"
        burner.strategy = 1
        burner.sn = "000941000029"
        burner.port = r"USB\VID_1366&PID_0105\000941000029"
        burner.is_enabled = True
        candidate = {
            "candidate_id": "known-jlink",
            "type": "J-LINK",
            "device_category": "burner",
            "sn": "941000029",
            "port": r"USB\VID_1366&PID_0105\000941000029",
            "node_key": "127.0.0.1",
            "node_type": "local",
            "node_label": "local",
        }

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())

        self.assertEqual(updates, [{"id": burner.id, "status": 0}])

    def test_strategy_one_probe_only_same_serial_same_node_is_online(self):
        burner = self.burners[0]
        burner.type = "AL321"
        burner.strategy = 1
        burner.sn = "210512180081"
        burner.port = r"USB\VID_0403&PID_6014\210512180081"
        burner.host_address = "192.168.0.18"
        burner.is_enabled = True
        candidate = {
            "candidate_id": "unknown-al321-by-sn",
            "type": LOCATION_PROBE_CANDIDATE_TYPE,
            "device_category": "probe_only",
            "detected_name": "USB Serial Converter",
            "sn": "210512180081",
            "port": r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)",
            "node_key": "192.168.0.18",
            "node_type": "local",
            "node_label": "local",
            "probe_only": True,
        }

        updates = _resolve_discovery_status_updates([burner], [candidate], "all", set())

        self.assertEqual(updates, [{"id": burner.id, "status": 0}])

    def test_binding_change_is_reported_when_same_device_moves_to_another_node(self):
        burner = self.burners[0]
        burner.type = "PWLINK2"
        burner.strategy = 1
        burner.sn = "42742761BAA116B9D7012DB4810082D1"
        burner.port = r"USB\VID_0D28&PID_0204\42742761BAA116B9D7012DB4810082D1"
        burner.agent_url = "http://192.168.0.107:8000"
        burner.host_address = "192.168.0.107"
        candidate = self.candidates[0]
        candidate["type"] = "PWLINK2"
        candidate["sn"] = "42742761BAA116B9D7012DB4810082D1"
        candidate["port"] = r"USB\VID_0D28&PID_0204\42742761BAA116B9D7012DB4810082D1"
        candidate["node_key"] = "127.0.0.1"
        candidate["node_type"] = "local"
        candidate["node_label"] = "本地"
        candidate["agent_url"] = None
        candidate["host_address"] = "127.0.0.1"

        payload = self._payload()

        self.assertEqual(len(payload["changed_bindings"]), 1)
        self.assertEqual(payload["changed_bindings"][0]["burner_id"], burner.id)
        self.assertEqual(payload["changed_bindings"][0]["original_binding"]["node_key"], "http://192.168.0.107:8000")
        self.assertEqual(payload["changed_bindings"][0]["current_binding"]["node_key"], "127.0.0.1")
        self.assertEqual(payload["unregistered_devices"], [])

    def test_explicit_agent_is_scanned_before_any_remote_burner_is_saved(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        with patch("backend.routers.burners._discover_lan_agent_urls", return_value=[]):
            nodes = _discover_scan_nodes(db, "all", explicit_agent_url="http://192.168.1.20:8000/")

        agent_nodes = [item for item in nodes if item["node_type"] == "agent"]
        self.assertEqual(len(agent_nodes), 1)
        self.assertEqual(agent_nodes[0]["agent_url"], "http://192.168.1.20:8000")
        self.assertEqual(agent_nodes[0]["host_address"], "192.168.1.20")

    def test_explicit_agent_requires_http_url(self):
        with self.assertRaisesRegex(ValueError, "Agent 地址格式不正确"):
            _normalize_agent_url("192.168.1.20:8000")

    def test_explicit_agent_connection_failure_is_reported(self):
        node = {
            "node_key": "http://192.168.1.20:8000",
            "node_type": "agent",
            "node_label": "192.168.1.20",
            "agent_url": "http://192.168.1.20:8000",
        }
        with (
            patch("backend.routers.burners._discover_scan_nodes", return_value=[node]),
            patch("backend.routers.burners._remote_discover_devices", side_effect=OSError("unreachable")),
        ):
            with self.assertRaisesRegex(ValueError, "无法连接下位机 Agent"):
                _build_discovery_payload(
                    self.db,
                    "all",
                    explicit_agent_url="http://192.168.1.20:8000",
                )

    @patch("backend.routers.burners._get_lan_agent_scan_networks")
    @patch("backend.routers.burners._probe_pcids_agent_url")
    def test_lan_discovery_finds_running_pcids_nodes(self, probe_mock, networks_mock):
        import ipaddress
        from backend.routers import burners as burners_module

        networks_mock.return_value = [ipaddress.ip_network("192.168.50.0/30")]
        probe_mock.side_effect = lambda url: url if url.endswith(".2:8000") else None
        burners_module._LAN_AGENT_CACHE["expires_at"] = 0.0
        burners_module._LAN_AGENT_CACHE["urls"] = []
        with patch("backend.routers.burners._get_service_node_addresses", return_value={"192.168.50.1"}):
            urls = _discover_lan_agent_urls(cache_ttl_seconds=1)

        self.assertEqual(urls, ["http://192.168.50.2:8000"])

    @patch.dict("os.environ", {"PCIDS_AGENT_DISCOVERY_CIDRS": ""}, clear=False)
    @patch("backend.routers.burners.psutil")
    def test_lan_discovery_uses_active_physical_networks_without_override(self, psutil_mock):
        psutil_mock.net_if_stats.return_value = {
            "Ethernet": SimpleNamespace(isup=True),
            "VMware Network Adapter VMnet8": SimpleNamespace(isup=True),
            "Disconnected Wi-Fi": SimpleNamespace(isup=False),
        }
        psutil_mock.net_if_addrs.return_value = {
            "Ethernet": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.100", netmask="255.255.255.0")],
            "VMware Network Adapter VMnet8": [SimpleNamespace(family=socket.AF_INET, address="192.168.182.1", netmask="255.255.255.0")],
            "Disconnected Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.2", netmask="255.255.255.0")],
        }
        with patch("backend.routers.burners.os.environ.get", return_value=""):
            networks = _get_lan_agent_scan_networks()

        self.assertEqual(
            {str(network) for network in networks},
            {"192.168.1.0/24"},
        )

    def test_lan_discovery_uses_external_yaml_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agent-discovery.yaml"
            config_path.write_text("discovery_cidrs:\n  - 192.168.1.0/24\n  - 10.20.30.0/24\nport: 8000\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "PCIDS_AGENT_DISCOVERY_CONFIG": str(config_path),
                    "PCIDS_AGENT_DISCOVERY_CIDRS": "",
                },
                clear=False,
            ):
                networks = _get_lan_agent_scan_networks()

        self.assertEqual({str(network) for network in networks}, {"192.168.1.0/24", "10.20.30.0/24"})

    def test_discovery_refreshes_registered_online_and_offline_statuses(self):
        online = self.burners[0]
        offline = self.burners[1]
        online.status = 1
        offline.status = 0
        online.is_enabled = True
        offline.is_enabled = True

        updates = _resolve_discovery_status_updates(
            [online, offline],
            [self.candidates[0]],
            "all",
            set(),
        )

        self.assertEqual(updates, [{"id": online.id, "status": 0}, {"id": offline.id, "status": 1}])

    def test_discovery_persists_refreshed_statuses_to_database(self):
        online = self.burners[0]
        offline = self.burners[1]
        online.status = 1
        offline.status = 0
        online.is_enabled = True
        offline.is_enabled = True

        burner_query = MagicMock()
        burner_query.all.return_value = self.burners
        task_query = MagicMock()
        task_query.filter.return_value.all.return_value = []
        self.db.query.side_effect = [burner_query, task_query]

        updates = _refresh_registered_burner_statuses(self.db, "all", [self.candidates[0]])

        status_by_id = {item["id"]: item["status"] for item in updates}
        self.assertEqual(status_by_id[online.id], 0)
        self.assertEqual(status_by_id[offline.id], 1)
        self.assertEqual(online.status, 0)
        self.assertEqual(offline.status, 1)
        self.db.commit.assert_called_once()

    def test_discovery_failed_agent_marks_same_node_burners_offline_without_runtime_probe(self):
        remote = self.burners[0]
        remote.agent_url = "http://192.168.0.107:8000"
        remote.host_address = "192.168.0.107"
        remote.status = 0
        remote.is_enabled = True

        burner_query = MagicMock()
        burner_query.all.return_value = [remote]
        task_query = MagicMock()
        task_query.filter.return_value.all.return_value = []
        self.db.query.side_effect = [burner_query, task_query]

        updates = _refresh_registered_burner_statuses(
            self.db,
            "all",
            [],
            failed_node_keys=["http://192.168.0.107:8000"],
        )

        self.assertEqual(updates, [{"id": remote.id, "status": 1}])
        self.assertEqual(remote.status, 1)
        self.db.commit.assert_called_once()

    def test_cached_status_keeps_online_zero_value(self):
        burner = self.burners[0]
        burner.status = 0
        burner.is_enabled = True

        status = _compute_burner_cached_status(burner, set())

        self.assertEqual(status, 0)

    def test_cached_status_does_not_keep_stale_busy_or_disabled_values(self):
        burner = self.burners[0]
        burner.is_enabled = True

        burner.status = 2
        self.assertEqual(_compute_burner_cached_status(burner, set()), 0)

        burner.status = 3
        self.assertEqual(_compute_burner_cached_status(burner, set()), 1)

        burner.is_enabled = None
        self.assertEqual(_compute_burner_cached_status(burner, set()), 1)

    def test_local_scan_keeps_remote_state_without_preserving_stale_busy_status(self):
        burner = self.burners[0]
        burner.agent_url = "http://192.168.0.107:8000"
        burner.host_address = "192.168.0.107"
        burner.status = 2
        burner.is_enabled = True

        updates = _resolve_discovery_status_updates([burner], [], "local", set())

        self.assertEqual(updates, [{"id": burner.id, "status": 0}])

    def test_physical_port_strategy_never_falls_back_to_location(self):
        burner = self.burners[0]
        burner.strategy = 2
        burner.sn = ""
        burner.port = ""
        burner.location = "OLD-LOCATION"
        burner.config_json = "{}"

        with patch("backend.routers.burners._match_usb_device") as match_mock:
            result = _build_scan_result(
                burner.type,
                burner.location,
                burner.strategy,
                burner,
                allow_fallback=False,
                usb_devices=[],
            )

        self.assertIsNone(result)
        match_mock.assert_not_called()

    def test_serial_strategy_follows_same_probe_after_usb_port_change(self):
        burner = self.burners[0]
        burner.strategy = 1
        burner.sn = "000941000029"
        burner.port = "OLD-PORT"
        burner.location = "OLD-PORT"
        burner.config_json = "{}"

        with patch(
            "backend.routers.burners._match_usb_device",
            return_value={"sn": "941000029", "port": "NEW-PORT", "source": "usb_probe", "name": "J-LINK"},
        ) as match_mock:
            result = _build_scan_result(
                burner.type,
                burner.location,
                burner.strategy,
                burner,
                allow_fallback=False,
                usb_devices=[],
            )

        self.assertTrue(result["online"])
        self.assertIsNone(match_mock.call_args.args[1])

    def test_ambiguous_ftdi_is_resolved_by_unique_registered_serial(self):
        al321 = self.burners[-1]
        al321.id = 100
        al321.type = "AL321"
        al321.sn = "210512180081"
        al321.port = r"USB\VID_0403&PID_6014\210512180081"
        al321.host_address = "192.168.0.18"
        gowin = SimpleNamespace(
            id=101,
            type="Gowin USB Cable",
            sn="GOWIN-NOT-CONNECTED",
            port=r"USB\OTHER",
            agent_url=None,
            host_address="192.168.0.18",
        )
        candidate = {
            "candidate_id": "one-physical-ftdi-device",
            "type": "FTDI JTAG Cable",
            "sn": "210512180081",
            "port": r"USB\VID_0403&PID_6014\210512180081",
            "node_key": "192.168.0.18",
            "possible_types": ["AL321", "Gowin USB Cable", "XDS510plus"],
            "requires_type_resolution": True,
        }

        resolved = _resolve_ambiguous_candidate_types([candidate], [al321, gowin])

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["type"], "AL321")
        self.assertFalse(resolved[0]["requires_type_resolution"])
        self.assertEqual(resolved[0]["resolved_burner_id"], 100)

    @patch("backend.routers.burners.os.path.isfile", return_value=True)
    @patch("backend.routers.burners.subprocess.run")
    def test_stlink_official_cli_serial_is_parsed_for_old_firmware(self, run_mock, _isfile_mock):
        run_mock.return_value = SimpleNamespace(
            stdout=b"ST-LINK Probe 0:\n     SN: 51FF6F067182525607321487\n     FW: V2J27S6\n",
            stderr=b"",
        )
        with patch.dict("backend.routers.burners.os.environ", {"STLINK_UTILITY_CLI": "ST-LINK_CLI.exe"}):
            serials = _probe_stlink_serials(cache_ttl_seconds=0)

        self.assertEqual(serials, ["51FF6F067182525607321487"])
        run_mock.assert_called_once_with(
            ["ST-LINK_CLI.exe", "-List"],
            capture_output=True,
            timeout=12,
            check=False,
        )


class BurnerDiscoveryAsyncOffloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_node_scan_runs_in_worker_thread(self):
        scan_thread_ids: list[int] = []

        def slow_scan(_node):
            scan_thread_ids.append(threading.get_ident())
            return []

        with patch("backend.routers.burners._scan_discovery_node", side_effect=slow_scan):
            candidates, failed_node_keys = await _scan_discovery_nodes_async(
                [{"node_key": "local", "node_type": "local", "agent_url": None}],
                "local",
                None,
                "test-trace",
            )

        self.assertEqual(candidates, [[]])
        self.assertEqual(failed_node_keys, set())
        self.assertEqual(len(scan_thread_ids), 1)
        self.assertNotEqual(scan_thread_ids[0], threading.get_ident())

    async def test_all_nodes_begin_scanning_concurrently_and_keep_input_order(self):
        both_started = threading.Event()
        start_lock = threading.Lock()
        started: list[str] = []

        def coordinated_scan(node):
            with start_lock:
                started.append(node["node_key"])
                if len(started) == 2:
                    both_started.set()
            if not both_started.wait(timeout=1):
                raise AssertionError("node scans were executed serially")
            return [{"candidate_id": node["node_key"]}]

        nodes = [
            {"node_key": "node-a", "node_type": "local", "agent_url": None},
            {"node_key": "node-b", "node_type": "agent", "agent_url": "http://node-b:8000"},
        ]
        with patch("backend.routers.burners._scan_discovery_node", side_effect=coordinated_scan):
            candidates, failed_node_keys = await _scan_discovery_nodes_async(
                nodes,
                "all",
                None,
                "test-concurrent",
            )

        self.assertEqual(
            candidates,
            [[{"candidate_id": "node-a"}], [{"candidate_id": "node-b"}]],
        )
        self.assertEqual(failed_node_keys, set())


if __name__ == "__main__":
    unittest.main()
