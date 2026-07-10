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


if __name__ == "__main__":
    unittest.main()
