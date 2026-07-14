import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.models.task import BurningTask
from backend.routers.tasks import (
    _ensure_unique_burner_serial_binding,
    _get_burner_runtime_issue,
    _hydrate_agent_jlink_serial,
    _resolve_missing_task_burner,
)


class TaskBurnerAutoBindingTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.burner = SimpleNamespace(
            id=1,
            name="J-LINK",
            type="J-LINK",
            strategy=1,
            sn="",
            port="",
            location="",
            status=1,
            agent_url=None,
        )

    def test_single_detected_probe_is_bound_before_execution(self):
        candidate = {
            "type": "J-LINK",
            "sn": "000941000029",
            "port": r"USB\VID_1366&PID_0105\000941000029",
        }
        with patch("backend.routers.tasks._discover_local_candidates", return_value=[candidate]):
            _ensure_unique_burner_serial_binding(self.db, self.burner)

        self.assertEqual(self.burner.sn, "000941000029")
        self.assertEqual(self.burner.port, candidate["port"])
        self.assertEqual(self.burner.status, 0)
        self.db.commit.assert_called_once()

    def test_multiple_detected_probes_require_manual_selection(self):
        candidates = [
            {"type": "J-LINK", "sn": "1001", "port": "USB1"},
            {"type": "J-LINK", "sn": "1002", "port": "USB2"},
        ]
        with patch("backend.routers.tasks._discover_local_candidates", return_value=candidates):
            with self.assertRaisesRegex(HTTPException, "检测到 2 台 J-LINK"):
                _ensure_unique_burner_serial_binding(self.db, self.burner)

        self.db.commit.assert_not_called()

    def test_physical_port_jlink_is_resolved_to_matching_serial(self):
        self.burner.strategy = 2
        self.burner.port = "USB-PORT-2"
        candidates = [
            {"type": "J-LINK", "sn": "1001", "port": "USB-PORT-1"},
            {"type": "J-LINK", "sn": "1002", "port": "USB-PORT-2"},
        ]
        with patch("backend.routers.tasks._discover_local_candidates", return_value=candidates):
            _ensure_unique_burner_serial_binding(self.db, self.burner)

        self.assertEqual(self.burner.sn, "1002")

    def test_physical_port_jlink_matches_device_manager_candidate_alias_port(self):
        self.burner.strategy = 2
        self.burner.port = r"USB\VID_1366&PID_0105\6&123ABC&0&3"
        candidates = [
            {
                "type": "J-LINK",
                "sn": "1001",
                "port": r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(2)",
                "alternative_ports": [r"USB\VID_1366&PID_0105\6&OTHER&0&2"],
            },
            {
                "type": "J-LINK",
                "sn": "1002",
                "port": r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)",
                "alternative_ports": [r"USB\VID_1366&PID_0105\6&123ABC&0&3"],
            },
        ]
        with patch("backend.routers.tasks._discover_local_candidates", return_value=candidates):
            _ensure_unique_burner_serial_binding(self.db, self.burner)

        self.assertEqual(self.burner.sn, "1002")
        self.assertEqual(self.burner.port, r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)")

    def test_agent_fills_missing_jlink_serial_from_local_probe(self):
        env = {"TASK_ID": "8", "BURNER_TYPE": "J-LINK", "BURNER_SN": ""}
        candidate = {"type": "J-LINK", "sn": "000941000029", "port": "USB-PORT-1"}
        with patch("backend.routers.tasks._discover_local_candidates", return_value=[candidate]):
            _hydrate_agent_jlink_serial(env)

        self.assertEqual(env["BURNER_SN"], "000941000029")
        self.assertEqual(env["BURNER_PORT"], "USB-PORT-1")

    def test_agent_jlink_serial_hydration_matches_alias_port(self):
        env = {
            "TASK_ID": "8",
            "BURNER_TYPE": "J-LINK",
            "BURNER_SN": "",
            "BURNER_PORT": r"USB\VID_1366&PID_0105\6&123ABC&0&3",
        }
        candidates = [
            {
                "type": "J-LINK",
                "sn": "1002",
                "port": r"PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)",
                "alternative_ports": [r"USB\VID_1366&PID_0105\6&123ABC&0&3"],
            }
        ]
        with patch("backend.routers.tasks._discover_local_candidates", return_value=candidates):
            _hydrate_agent_jlink_serial(env)

        self.assertEqual(env["BURNER_SN"], "1002")

    def test_remote_burner_runtime_check_uses_shared_scan_timeout(self):
        burner = SimpleNamespace(
            id=2,
            name="Remote J-LINK",
            type="J-LINK",
            strategy=2,
            sn="",
            port="USB-PORT-1",
            location="USB-PORT-1",
            status=0,
            is_enabled=True,
            agent_url="http://192.168.1.20:8000",
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backend.routers.tasks._http_post_json", return_value={"data": {"online": True}}) as post_mock:
            issue = _get_burner_runtime_issue(db, burner, current_task_id=123)

        self.assertIsNone(issue)
        self.assertEqual(post_mock.call_args.kwargs["timeout_seconds"], 6)

    def test_terminating_task_keeps_burner_reserved_until_cleanup_finishes(self):
        burner = SimpleNamespace(
            id=2,
            name="Remote J-LINK",
            type="J-LINK",
            strategy=2,
            sn="",
            port="USB-PORT-1",
            location="USB-PORT-1",
            status=0,
            is_enabled=True,
            agent_url="http://192.168.1.20:8000",
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("backend.routers.tasks._http_post_json", return_value={"data": {"online": True}}):
            issue = _get_burner_runtime_issue(db, burner, current_task_id=123)

        self.assertIsNone(issue)
        filter_args = db.query.return_value.filter.call_args.args
        self.assertTrue(any("IN" in str(arg).upper() and "status" in str(arg).lower() for arg in filter_args))

    def test_create_task_resolves_missing_burner_id_from_config(self):
        xds510plus = SimpleNamespace(
            id=20,
            name="XDS510plus",
            type="XDS510plus",
            host_type="local",
            port="Port_#0002.Hub_#0002",
            is_enabled=1,
        )
        other = SimpleNamespace(
            id=21,
            name="ST-LINK",
            type="ST-LINK",
            host_type="local",
            port="USB-OTHER",
            is_enabled=1,
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [other, xds510plus]
        task = BurningTask(software_name="M405C_Control.out", task_type="board")

        resolved = _resolve_missing_task_burner(
            db,
            task,
            {
                "burner_type": "XDS510plus",
                "burner_name": "Spectrum Digital XDS510USB-PLUS",
            },
        )

        self.assertIs(resolved, xds510plus)
        self.assertEqual(task.burner_id, 20)


if __name__ == "__main__":
    unittest.main()
