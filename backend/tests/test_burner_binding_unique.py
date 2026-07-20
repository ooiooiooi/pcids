import json
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from backend.routers.burners import _clear_conflicting_burner_port, _validate_burner_binding_unique


class _FakeQuery:
    def __init__(self, burners):
        self._burners = burners

    def all(self):
        return self._burners


class _FakeDB:
    def __init__(self, burners):
        self._burners = burners

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._burners)


class BurnerBindingUniqueTests(unittest.TestCase):
    def test_strategy_two_conflict_message_contains_existing_burner_name(self):
        db = _FakeDB(
            [
                SimpleNamespace(
                    id=1,
                    name="XDS510plus",
                    type="XDS510plus",
                    port="Port_#0002.Hub_#0002",
                    sn="",
                    host_name=None,
                )
            ]
        )

        with self.assertRaises(HTTPException) as context:
            _validate_burner_binding_unique(
                db,
                {
                    "strategy": 2,
                    "port": "Port_#0002.Hub_#0002",
                },
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("XDS510plus", context.exception.detail)
        self.assertIn("当前物理位置已被设备", context.exception.detail)

    def test_strategy_one_conflict_message_contains_existing_burner_name(self):
        db = _FakeDB(
            [
                SimpleNamespace(
                    id=2,
                    name="J-LINK #1",
                    type="J-LINK",
                    port="USB1",
                    sn="SERIAL-001",
                    host_name="服务器",
                )
            ]
        )

        with self.assertRaises(HTTPException) as context:
            _validate_burner_binding_unique(
                db,
                {
                    "strategy": 1,
                    "sn": "SERIAL-001",
                },
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("J-LINK #1", context.exception.detail)
        self.assertIn("服务器", context.exception.detail)
        self.assertIn("当前 SN 标识码已被设备", context.exception.detail)

    def test_strategy_two_force_rebind_returns_conflict_burner_and_can_clear_old_port(self):
        conflict_burner = SimpleNamespace(
            id=3,
            name="XDS510plus-旧设备",
            type="XDS510plus",
            port="Port_#0003.Hub_#0001",
            sn="",
            host_name=None,
            modified_by=None,
        )
        db = _FakeDB([conflict_burner])

        matched = _validate_burner_binding_unique(
            db,
            {
                "strategy": 2,
                "port": "Port_#0003.Hub_#0001",
            },
            force_rebind_port=True,
        )

        self.assertIs(matched, conflict_burner)
        _clear_conflicting_burner_port(conflict_burner, "tester")
        self.assertEqual(conflict_burner.port, "")
        self.assertEqual(conflict_burner.modified_by, "tester")

    def test_strategy_two_uses_full_usb_binding_on_same_node(self):
        conflict_burner = SimpleNamespace(
            id=4,
            name="Existing burner",
            type="XDS510plus",
            port="Port_#0001.Hub_#0003",
            sn="",
            host_name=None,
            host_address="10.0.0.8",
            agent_url=None,
            config_json=json.dumps({"usb_binding": {"location_path": "PCIROOT(0)#USBROOT(0)#USB(4)#USB(1)"}}),
        )
        db = _FakeDB([conflict_burner])

        with self.assertRaises(HTTPException):
            _validate_burner_binding_unique(
                db,
                {
                    "strategy": 2,
                    "port": "Port_#9999.Hub_#9999",
                    "host_address": "10.0.0.8",
                    "config_json": json.dumps({"usb_binding": {"location_path": "PCIROOT(0)#USBROOT(0)#USB(4)#USB(1)"}}),
                },
            )

    def test_same_short_port_is_allowed_on_different_nodes(self):
        existing = SimpleNamespace(
            id=5,
            name="Remote burner",
            type="J-LINK",
            port="Port_#0001.Hub_#0003",
            sn="",
            host_name=None,
            host_address="10.0.0.8",
            agent_url=None,
            config_json="{}",
        )
        db = _FakeDB([existing])

        result = _validate_burner_binding_unique(
            db,
            {
                "strategy": 2,
                "port": "Port_#0001.Hub_#0003",
                "host_address": "10.0.0.9",
                "config_json": "{}",
            },
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
