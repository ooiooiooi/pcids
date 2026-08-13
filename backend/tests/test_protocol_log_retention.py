from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, ProtocolLog, ProtocolSession, User
from backend.routers import protocol_tests


class ProtocolLogRetentionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(username="protocol-log-user", password_hash="x", status=1)
        self.db.add(self.user)
        self.db.flush()
        self.session = ProtocolSession(
            created_by_user_id=self.user.id,
            task_no="PT202608130001",
            target="Board-A",
            protocol="serial",
            config_json="{}",
            status=1,
            tx_count=1,
            rx_count=1,
            executor=self.user.username,
            ip_address="127.0.0.1",
            created_at=datetime.now(),
        )
        self.db.add(self.session)
        self.db.flush()
        now = datetime.now()
        self.db.add_all(
            [
                ProtocolLog(
                    session_id=self.session.id,
                    protocol="serial",
                    timestamp=now,
                    direction="Tx",
                    dlc=1,
                    data="AA",
                ),
                ProtocolLog(
                    session_id=self.session.id,
                    protocol="serial",
                    timestamp=now + timedelta(milliseconds=1),
                    direction="Rx",
                    dlc=1,
                    data="验证未通过：响应超时",
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_clear_only_returns_frontend_cutoff_and_keeps_history_counts(self):
        result = asyncio.run(
            protocol_tests.clear_session_logs(
                self.session.id,
                db=self.db,
                current_user=self.user,
                _=None,
            )
        )

        cutoff = result["data"]["cleared_through_log_id"]
        self.assertGreater(cutoff, 0)
        self.assertEqual(
            self.db.query(ProtocolLog).filter(ProtocolLog.session_id == self.session.id).count(),
            2,
        )
        self.db.refresh(self.session)
        self.assertEqual((self.session.tx_count, self.session.rx_count), (1, 1))

        page = asyncio.run(
            protocol_tests.get_session_logs(
                self.session.id,
                page=1,
                page_size=1,
                include_summary=True,
                db=self.db,
                current_user=self.user,
                _=None,
            )
        )
        self.assertEqual(page["history_total"], 2)
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["data"]), 1)
        self.assertEqual(page["anomaly_count"], 1)
        self.assertEqual(page["summary"], {"tx": 1, "rx": 1, "total": 2, "anomaly": 1})

        hidden_page = asyncio.run(
            protocol_tests.get_session_logs(
                self.session.id,
                page=1,
                page_size=50,
                after_id=cutoff,
                db=self.db,
                current_user=self.user,
                _=None,
            )
        )
        self.assertEqual(hidden_page["history_total"], 2)
        self.assertEqual(hidden_page["total"], 0)
        self.assertEqual(hidden_page["data"], [])

        new_log = ProtocolLog(
            session_id=self.session.id,
            protocol="serial",
            timestamp=datetime.now() + timedelta(seconds=1),
            direction="Tx",
            dlc=1,
            data="BB",
        )
        self.db.add(new_log)
        self.db.commit()

        visible_page = asyncio.run(
            protocol_tests.get_session_logs(
                self.session.id,
                page=1,
                page_size=50,
                after_id=cutoff,
                db=self.db,
                current_user=self.user,
                _=None,
            )
        )
        self.assertEqual(visible_page["history_total"], 3)
        self.assertEqual(visible_page["total"], 1)
        self.assertEqual([item["id"] for item in visible_page["data"]], [new_log.id])


if __name__ == "__main__":
    unittest.main()
