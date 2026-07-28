import json
import unittest
from unittest.mock import MagicMock, patch

from backend.routers.burners import (
    LOCATION_PROBE_CANDIDATE_TYPE,
    _build_discovery_candidates,
    _classify_probe_items,
    _device_scan_priority,
    _match_usb_device,
    _probe_windows_usb_devices,
    _remote_discover_devices,
)


class BurnerScanPriorityTests(unittest.TestCase):
    def setUp(self):
        self.ftdi_al321 = {
            "_name": "USB Serial Converter",
            "location_id": r"USB\VID_0403&PID_6014\210512180081",
            "vendor_id": "0403",
            "product_id": "6014",
            "serial_num": "210512180081",
            "source": "windows_pnp",
        }

    def test_stlink_is_prioritized_over_cmsis_dap_family(self):
        self.assertGreater(_device_scan_priority("ST-LINK"), _device_scan_priority("PWLINK2"))

    def test_unknown_device_has_no_priority(self):
        self.assertEqual(_device_scan_priority("unknown"), 0)

    def test_generic_scan_ignores_unrecognized_usb_devices(self):
        devices = [
            {
                "_name": "USB Composite Device",
                "location_id": r"USB\VID_046D&PID_C34B\1",
                "vendor_id": "046D",
                "product_id": "C34B",
                "serial_num": "1",
                "source": "windows_pnp",
            }
        ]
        self.assertIsNone(_match_usb_device(None, None, usb_devices=devices))

    def test_generic_scan_prefers_stlink_over_cmsis_dap(self):
        devices = [
            {
                "_name": "CMSIS-DAP",
                "location_id": r"USB\VID_0D28&PID_0204\DAP1",
                "vendor_id": "0D28",
                "product_id": "0204",
                "serial_num": "DAP1",
                "source": "windows_pnp",
            },
            {
                "_name": "STM32 STLink",
                "location_id": r"USB\VID_0483&PID_3748\ST1",
                "vendor_id": "0483",
                "product_id": "3748",
                "serial_num": "ST1",
                "source": "windows_pnp",
            },
        ]
        with patch("backend.routers.burners._probe_usb_devices", return_value=devices):
            result = _match_usb_device(None, None, usb_devices=devices)
        self.assertEqual(result["name"], "STM32 STLink")

    def test_device_name_alias_matching_is_case_and_separator_tolerant(self):
        classified = _classify_probe_items(
            {
                "_name": "SEGGER JLink OB-SAM3U detailed probe",
                "product_name": "JLink OB-SAM3U",
                "manufacturer": "SEGGER",
            }
        )

        self.assertEqual(classified[0]["type"], "J-LINK")

    def test_device_name_alias_matching_accepts_detailed_stlink_model(self):
        classified = _classify_probe_items(
            {
                "_name": "STM32 ST LINK V2-1 debug interface",
                "product_name": "ST LINK V2-1",
                "manufacturer": "STMicroelectronics",
            }
        )

        self.assertEqual(classified[0]["type"], "ST-LINK")

    def test_cmsis_dap_is_not_misclassified_as_sd_reader(self):
        classified = _classify_probe_items(
            {
                "_name": "CMSIS-DAP V2",
                "product_name": "CMSIS-DAP V2",
                "manufacturer": "WinUSB Device",
            }
        )

        self.assertEqual(classified, [])

    def test_pyocd_identity_classifies_generic_cmsis_dap_as_gdlink(self):
        parent_id = r"USB\VID_28E9&PID_0698\3835388D0655"
        container_id = "{BD11BA2D-789B-5B07-80E9-7BA3E8F3FF87}"
        usb_devices = [
            {
                "_name": "CMSIS-DAP V2",
                "product_name": "CMSIS-DAP V2",
                "manufacturer": "WinUSB Device",
                "serial_num": "8&3645FEEE&0&0000",
                "location_id": "Port_#0001.Hub_#0007",
                "pnp_device_id": r"USB\VID_28E9&PID_0698&MI_00\8&3645FEEE&0&0000",
                "parent_id": parent_id,
                "container_id": container_id,
                "vendor_id": "28E9",
                "product_id": "0698",
                "source": "windows_pnp",
            },
            {
                "_name": "USB Composite Device",
                "serial_num": "3835388D0655",
                "location_id": "Port_#0001.Hub_#0007",
                "pnp_device_id": parent_id,
                "container_id": container_id,
                "vendor_id": "28E9",
                "product_id": "0698",
                "source": "windows_pnp",
            },
        ]
        pyocd_probes = [
            {
                "unique_id": "3835388D0655\x00\x00",
                "description": "GigaDevice GDLinker_V3",
                "vendor_name": "GigaDevice",
                "product_name": "GDLinker_V3",
            }
        ]

        candidates = _build_discovery_candidates(
            usb_devices[0],
            agent_url=None,
            host_address="192.168.137.2",
            usb_devices=usb_devices,
            pyocd_probes=pyocd_probes,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["type"], "GDLINK")
        self.assertEqual(candidates[0]["device_category"], "burner")
        self.assertEqual(candidates[0]["sn"], "3835388D0655")

    @patch(
        "backend.routers.burners._probe_stlink_serials",
        return_value=["51FF6F067182525607321487"],
    )
    def test_stlink_scan_uses_unique_official_cli_serial_for_location_only_pnp(self, _serials_mock):
        device = {
            "_name": "STM32 STLink",
            "product_name": "STM32 STLink",
            "manufacturer": "STMicroelectronics",
            "serial_num": "9&8BD070F&0&3",
            "location_id": "Port_#0003.Hub_#0007",
            "location_info": "Port_#0003.Hub_#0007",
            "pnp_device_id": r"USB\VID_0483&PID_3748\9&8BD070F&0&3",
            "vendor_id": "0483",
            "product_id": "3748",
            "source": "windows_pnp",
        }

        result = _match_usb_device(
            "ST-LINK",
            None,
            usb_devices=[device],
            expected_sn="51FF6F067182525607321487",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["sn"], "51FF6F067182525607321487")
        self.assertEqual(result["port"], "Port_#0003.Hub_#0007")

    @patch(
        "backend.routers.burners._probe_stlink_serials",
        return_value=["51FF6F067182525607321487"],
    )
    def test_stlink_scan_does_not_assign_one_cli_serial_to_multiple_pnp_devices(self, _serials_mock):
        devices = [
            {
                "_name": "STM32 STLink",
                "serial_num": f"9&8BD070F&0&{index}",
                "location_id": f"Port_#000{index}.Hub_#0007",
                "vendor_id": "0483",
                "product_id": "3748",
                "source": "windows_pnp",
            }
            for index in (3, 4)
        ]

        result = _match_usb_device(
            "ST-LINK",
            None,
            usb_devices=devices,
            expected_sn="51FF6F067182525607321487",
        )

        self.assertIsNone(result)

    def test_unrecognized_usb_with_port_creates_probe_only_candidate(self):
        candidates = _build_discovery_candidates(
            self.ftdi_al321,
            agent_url=None,
            host_address="192.168.0.18",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["type"], LOCATION_PROBE_CANDIDATE_TYPE)
        self.assertTrue(candidates[0]["probe_only"])
        self.assertEqual(candidates[0]["device_category"], "probe_only")

    def test_fixed_pid_no_longer_decides_registered_model(self):
        al321 = _match_usb_device(
            "AL321",
            None,
            usb_devices=[self.ftdi_al321],
        )
        wrong_gowin = _match_usb_device(
            "Gowin USB Cable",
            None,
            usb_devices=[self.ftdi_al321],
            expected_sn="G8FA71064E573436F2FC1PQR",
        )
        wrong_xds = _match_usb_device(
            "XDS510plus",
            None,
            usb_devices=[self.ftdi_al321],
            expected_sn="C4FA71064E573436F2FC1DEF",
        )

        self.assertIsNone(al321)
        self.assertIsNone(wrong_gowin)
        self.assertIsNone(wrong_xds)

    def test_strategy_one_expected_serial_can_match_unrecognized_usb(self):
        result = _match_usb_device(
            "AL321",
            None,
            usb_devices=[self.ftdi_al321],
            expected_sn="210512180081",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["sn"], "210512180081")

    def test_strategy_two_expected_port_cannot_claim_unrecognized_usb(self):
        self.assertIsNone(
            _match_usb_device(
                "Gowin USB Cable",
                None,
                usb_devices=[self.ftdi_al321],
                expected_port=r"USB\VID_0403&PID_6014\210512180081",
            )
        )

    def test_windows_device_manager_location_is_primary_physical_port(self):
        device = {
            "_name": "Gowin USB Cable",
            "location_id": "Port_#0011.Hub_#0001",
            "device_manager_location": "Port_#0011.Hub_#0001",
            "location_info": "Port_#0011.Hub_#0001",
            "location_path": r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)",
            "pnp_device_id": r"USB\VID_1234&PID_ABCD\6&123ABC&0&3",
            "vendor_id": "1234",
            "product_id": "ABCD",
            "serial_num": "6&123ABC&0&3",
            "source": "windows_pnp",
        }

        candidates = _build_discovery_candidates(device, agent_url=None, host_address="127.0.0.1")
        self.assertEqual(candidates[0]["port"], "Port_#0011.Hub_#0001")
        self.assertIn(r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)", candidates[0]["alternative_ports"])
        self.assertIn(r"USB\VID_1234&PID_ABCD\6&123ABC&0&3", candidates[0]["alternative_ports"])
        self.assertEqual(candidates[0]["usb_binding"]["location_info"], "Port_#0011.Hub_#0001")
        self.assertEqual(candidates[0]["usb_binding"]["location_path"], r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)")
        self.assertEqual(candidates[0]["usb_binding"]["pnp_device_id"], r"USB\VID_1234&PID_ABCD\6&123ABC&0&3")

        legacy_match = _match_usb_device(
            "Gowin USB Cable",
            None,
            usb_devices=[device],
            expected_port=r"USB\VID_1234&PID_ABCD\6&123ABC&0&3",
        )
        self.assertIsNotNone(legacy_match)
        self.assertEqual(legacy_match["port"], "Port_#0011.Hub_#0001")

    @patch("backend.routers.burners.subprocess.run")
    def test_windows_probe_uses_location_info_for_ok_devices(self, run_mock):
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        completed.stdout = json.dumps(
            [
                {
                    "Name": "SEED USB2.0 PLUS Emulator",
                    "Manufacturer": "SEED International Ltd.",
                    "DeviceID": r"USB\VID_0547&PID_1020\6&23A967E&0&2",
                    "PNPClass": "USBDevice",
                    "Status": "OK",
                    "LocationInformation": "",
                    "LocationInfo": "Port_#0002.Hub_#0002",
                    "LocationPaths": "",
                    "Parent": "",
                    "ContainerId": "",
                    "Service": "EZUSBPLUS",
                }
            ]
        ).encode("utf-8")
        run_mock.return_value = completed

        devices = _probe_windows_usb_devices()

        self.assertEqual(devices[0]["location_id"], "Port_#0002.Hub_#0002")
        self.assertEqual(devices[0]["device_manager_location"], "Port_#0002.Hub_#0002")
        self.assertEqual(devices[0]["pnp_device_id"], r"USB\VID_0547&PID_1020\6&23A967E&0&2")
        self.assertEqual(devices[0]["driver_service"], "EZUSBPLUS")

    def test_remote_discovery_uses_configurable_timeout(self):
        response = MagicMock()
        response.read.return_value = b'{"code":0,"data":{"items":[]}}'
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with patch("backend.routers.burners.urllib.request.urlopen", return_value=response) as urlopen_mock:
            _remote_discover_devices("http://192.168.1.20:8000", timeout_seconds=1.5)

        self.assertEqual(urlopen_mock.call_args.kwargs["timeout"], 1.5)


if __name__ == "__main__":
    unittest.main()
