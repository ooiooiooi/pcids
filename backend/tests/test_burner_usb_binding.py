import json
import unittest

from backend.routers.burners import _normalize_burner_payload


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


if __name__ == "__main__":
    unittest.main()
