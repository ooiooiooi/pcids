import json
import unittest

from backend.routers.burners import _get_real_usb_serial, _normalize_burner_payload


class BurnerUsbBindingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
