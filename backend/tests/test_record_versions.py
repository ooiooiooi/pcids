import json
import unittest
from datetime import datetime
from unittest.mock import MagicMock

from backend.models.log import Record
from backend.models.repository import Repository
from backend.routers.records import record_to_dict


class RecordVersionResolutionTests(unittest.TestCase):
    def test_record_falls_back_to_repository_version_by_project_and_software_name(self):
        record = Record(
            id=1,
            repository_id=None,
            project_key="pcids-demo",
            serial_number="SN-001",
            software_name="BOOT.bin",
            operator="admin",
            operation_time=datetime(2026, 6, 23, 17, 41, 7),
            result="失败",
            type="burn",
            log_data=json.dumps({"task_id": 1001}, ensure_ascii=False),
        )
        repository = Repository(
            id=10,
            project_key="pcids-demo",
            name="BOOT.bin",
            version="latest",
        )

        payload = record_to_dict(
            record,
            MagicMock(),
            repository_by_id={},
            user_by_id={},
            users_by_name={},
            repository_by_project_key={"pcids-demo": repository},
            repository_by_project_and_name={("pcids-demo", "BOOT.bin"): repository},
        )

        self.assertEqual(payload["software_version"], "latest")

    def test_record_uses_file_detail_version_when_repository_version_is_empty(self):
        record = Record(
            id=2,
            repository_id=11,
            project_key="pcids-demo",
            serial_number="SN-002",
            software_name="CAN.elf",
            operator="admin",
            operation_time=datetime(2026, 6, 23, 17, 41, 7),
            result="成功",
            type="burn",
            log_data=json.dumps({}, ensure_ascii=False),
        )
        repository = Repository(
            id=11,
            project_key="pcids-demo",
            name="CAN.elf",
            version="",
            file_detail_json=json.dumps({"build_version": "v2.3.1"}, ensure_ascii=False),
        )

        payload = record_to_dict(
            record,
            MagicMock(),
            repository_by_id={11: repository},
            user_by_id={},
            users_by_name={},
            repository_by_project_key={"pcids-demo": repository},
            repository_by_project_and_name={("pcids-demo", "CAN.elf"): repository},
        )

        self.assertEqual(payload["software_version"], "v2.3.1")


if __name__ == "__main__":
    unittest.main()
