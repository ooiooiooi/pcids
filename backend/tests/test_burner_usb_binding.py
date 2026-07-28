import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.routers import burners as burners_module
from backend.routers.burners import (
    _ensure_burner_owner_node,
    _get_real_usb_serial,
    _normalize_burner_payload,
    _validate_burner_payload_required,
)


class BurnerUsbBindingTests(unittest.TestCase):
    def test_scan_rejects_composite_parent_serial_when_child_is_another_burner_type(self):
        parent_id = r"USB\VID_1366&PID_0105\000941000029"
        container_id = "{91955D81-7A29-506B-B890-1B57C9672852}"
        usb_devices = [
            {
                "_name": "USB Composite Device",
                "serial_num": "000941000029",
                "vendor_id": "1366",
                "product_id": "0105",
                "pnp_device_id": parent_id,
                "container_id": container_id,
                "location_info": "Port_#0002.Hub_#0007",
            },
            {
                "_name": "J-Link driver",
                "vendor_id": "1366",
                "product_id": "0105",
                "pnp_device_id": r"USB\VID_1366&PID_0105&MI_02\A&1B9FBBA8&0&0002",
                "parent_id": parent_id,
                "container_id": container_id,
            },
        ]

        matched = burners_module._match_usb_device(
            "GDLINK",
            None,
            usb_devices=usb_devices,
            expected_sn="000941000029",
        )

        self.assertIsNone(matched)

    def test_scan_accepts_composite_parent_serial_for_classified_child_type(self):
        parent_id = r"USB\VID_1366&PID_0105\000941000029"
        container_id = "{91955D81-7A29-506B-B890-1B57C9672852}"
        usb_devices = [
            {
                "_name": "USB Composite Device",
                "serial_num": "000941000029",
                "vendor_id": "1366",
                "product_id": "0105",
                "pnp_device_id": parent_id,
                "container_id": container_id,
                "location_info": "Port_#0002.Hub_#0007",
            },
            {
                "_name": "J-Link driver",
                "vendor_id": "1366",
                "product_id": "0105",
                "pnp_device_id": r"USB\VID_1366&PID_0105&MI_02\A&1B9FBBA8&0&0002",
                "parent_id": parent_id,
                "container_id": container_id,
            },
        ]

        matched = burners_module._match_usb_device(
            "J-LINK",
            None,
            usb_devices=usb_devices,
            expected_sn="000941000029",
        )

        self.assertIsNotNone(matched)
        self.assertEqual(matched["sn"], "000941000029")

    def test_scan_keeps_manual_type_flexibility_for_unclassified_usb_device(self):
        usb_devices = [
            {
                "_name": "USB Composite Device",
                "serial_num": "CUSTOM-001",
                "vendor_id": "1234",
                "product_id": "5678",
                "pnp_device_id": r"USB\VID_1234&PID_5678\CUSTOM-001",
                "container_id": "{CUSTOM}",
                "location_info": "Port_#0001.Hub_#0002",
            }
        ]

        matched = burners_module._match_usb_device(
            "GDLINK",
            None,
            usb_devices=usb_devices,
            expected_sn="CUSTOM-001",
        )

        self.assertIsNotNone(matched)
        self.assertEqual(matched["sn"], "CUSTOM-001")

    def test_changed_port_drops_stale_usb_binding(self):
        payload = _normalize_burner_payload(
            {
                "type": "GDLINK",
                "strategy": 2,
                "port": "Port_#0002.Hub_#0007",
                "config_json": json.dumps(
                    {
                        "device_category": "burner",
                        "usb_binding": {
                            "location_info": "Port_#0001.Hub_#0004",
                            "pnp_device_id": r"USB\VID_31B2&PID_0022\OLD",
                        },
                    }
                ),
            }
        )

        self.assertNotIn("usb_binding", json.loads(payload["config_json"]))

    def test_matching_port_keeps_usb_binding(self):
        payload = _normalize_burner_payload(
            {
                "type": "GDLINK",
                "strategy": 2,
                "port": "Port_#0003.Hub_#0003",
                "config_json": json.dumps(
                    {
                        "device_category": "burner",
                        "usb_binding": {
                            "location_info": "Port_#0003.Hub_#0003",
                            "pnp_device_id": r"USB\VID_28E9&PID_0698\3835388D0655",
                        },
                    }
                ),
            }
        )

        self.assertEqual(
            json.loads(payload["config_json"])["usb_binding"]["pnp_device_id"],
            r"USB\VID_28E9&PID_0698\3835388D0655",
        )

    def test_xds510plus_forces_physical_port_and_clears_false_serial(self):
        payload = _normalize_burner_payload(
            {
                "type": "XDS510plus",
                "strategy": 1,
                "sn": "20220127",
                "port": "Port_#0003.Hub_#0002",
                "config_json": json.dumps(
                    {
                        "device_category": "burner",
                        "usb_binding": {
                            "location_info": "Port_#0003.Hub_#0002",
                            "location_path": "PCIROOT(0)#USBROOT(0)#USB(3)",
                            "unexpected": "discarded",
                        },
                    }
                ),
            }
        )

        self.assertEqual(payload["strategy"], 2)
        self.assertEqual(payload["sn"], "")
        config = json.loads(payload["config_json"])
        self.assertEqual(
            config["usb_binding"],
            {
                "location_info": "Port_#0003.Hub_#0002",
                "location_path": "PCIROOT(0)#USBROOT(0)#USB(3)",
            },
        )

    def test_al321_backfills_sn_and_location_from_usb_binding_pnp_device_id(self):
        payload = _normalize_burner_payload(
            {
                "type": "AL321",
                "strategy": 1,
                "sn": "",
                "location": "",
                "config_json": json.dumps(
                    {
                        "device_category": "burner",
                        "usb_binding": {
                            "pnp_device_id": r"USB\VID_0403&PID_6014\210512180081",
                            "location_info": "Port_#0011.Hub_#0001",
                        },
                    }
                ),
            }
        )

        self.assertEqual(payload["sn"], "210512180081")
        self.assertEqual(payload["location"], r"USB\VID_0403&PID_6014\210512180081")

    def test_al321_scan_recovers_stable_serial_from_pnp_instance_id(self):
        self.assertEqual(
            _get_real_usb_serial(
                {"pnp_device_id": r"USB\VID_0403&PID_6014\210512180081"},
                device_type="AL321",
            ),
            "210512180081",
        )

    def test_al321_scan_rejects_windows_location_instance_suffix(self):
        self.assertEqual(
            _get_real_usb_serial(
                {"pnp_device_id": r"USB\VID_0403&PID_6014\7&16B090BC&0&2"},
                device_type="AL321",
            ),
            "",
        )

    def test_explicit_windows_refresh_invalidates_cached_probe_results(self):
        burners_module._PNP_REFRESH_CACHE["refreshed_at"] = 0.0
        burners_module._USB_PROBE_CACHE["devices"] = [{"stale": True}]
        burners_module._USB_PROBE_CACHE["expires_at"] = 999999999.0
        burners_module._STLINK_SERIAL_CACHE["serials"] = ["STALE"]
        burners_module._STLINK_SERIAL_CACHE["expires_at"] = 999999999.0

        with (
            patch("backend.routers.burners.platform.system", return_value="Windows"),
            patch(
                "backend.routers.burners.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run_mock,
        ):
            refreshed = burners_module._refresh_windows_pnp_state(min_interval_seconds=0)

        self.assertTrue(refreshed)
        self.assertEqual(burners_module._USB_PROBE_CACHE["devices"], [])
        self.assertEqual(burners_module._STLINK_SERIAL_CACHE["serials"], [])
        run_mock.assert_called_once()

    def test_backend_rejects_missing_binding_for_each_strategy(self):
        with self.assertRaises(HTTPException):
            _validate_burner_payload_required(
                {
                    "name": "J-LINK",
                    "type": "J-LINK",
                    "strategy": 1,
                    "sn": "",
                    "port": "",
                    "is_enabled": True,
                    "config_json": '{"device_category":"burner"}',
                }
            )
        with self.assertRaises(HTTPException):
            _validate_burner_payload_required(
                {
                    "name": "GDLINK",
                    "type": "GDLINK",
                    "strategy": 2,
                    "sn": "",
                    "port": "",
                    "is_enabled": True,
                    "config_json": '{"device_category":"burner"}',
                }
            )
        _validate_burner_payload_required(
            {
                "name": "GDLINK",
                "type": "GDLINK",
                "strategy": 2,
                "sn": "",
                "port": "",
                "is_enabled": False,
                "config_json": '{"device_category":"burner"}',
            }
        )

    def test_backend_rejects_empty_identity_or_enabled_state(self):
        base_payload = {
            "name": "J-LINK",
            "type": "J-LINK",
            "strategy": 1,
            "sn": "000941000029",
            "port": "",
            "is_enabled": True,
            "config_json": '{"device_category":"burner"}',
        }
        for field, value in (("name", " "), ("type", None), ("is_enabled", None), ("strategy", None)):
            with self.subTest(field=field):
                with self.assertRaises(HTTPException):
                    _validate_burner_payload_required({**base_payload, field: value})

    def test_agent_owner_requires_valid_url_and_fills_host_address(self):
        with self.assertRaises(HTTPException):
            _ensure_burner_owner_node({"host_type": "agent", "agent_url": ""})
        with self.assertRaises(HTTPException):
            _ensure_burner_owner_node({"host_type": "unexpected", "agent_url": ""})

        normalized = _ensure_burner_owner_node(
            {"host_type": "agent", "agent_url": "http://192.168.1.20:8000/", "host_address": ""}
        )

        self.assertEqual(normalized["agent_url"], "http://192.168.1.20:8000")
        self.assertEqual(normalized["host_address"], "192.168.1.20")


if __name__ == "__main__":
    unittest.main()
