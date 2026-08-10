import json
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import (
    Base,
    Burner,
    BurningTask,
    BusinessSyncChange,
    BusinessSyncState,
    Menu,
    Permission,
    Product,
    ProtocolLog,
    ProtocolSession,
    ProtocolTest,
    Record,
    RepositoryProjectMember,
    RepositoryProjectSetting,
    Role,
    RolePermission,
    Script,
    User,
)
from backend.utils import business_data_sync as sync


class BusinessDataSyncTests(unittest.TestCase):
    def setUp(self):
        self.engines = []
        self.sessions = []
        for _ in range(3):
            engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(engine)
            self.engines.append(engine)
            self.sessions.append(
                sessionmaker(bind=engine, expire_on_commit=False)()
            )
        self.server, self.client_a, self.client_b = self.sessions
        self.node_patcher = patch.object(sync, "get_repository_sync_node_id", return_value="server-node")
        self.node_patcher.start()

    def tearDown(self):
        self.node_patcher.stop()
        for session in self.sessions:
            session.close()
        for engine in self.engines:
            Base.metadata.drop_all(engine)
            engine.dispose()

    @staticmethod
    def _states(db):
        return [
            sync.state_wire_payload(row)
            for row in db.query(BusinessSyncState).order_by(BusinessSyncState.revision.asc()).all()
        ]

    def _seed_full_business_graph(self):
        db = self.server
        role = Role(name="工程师", description="终端工程师", status=1, data_scope="all")
        db.add(role)
        db.flush()
        menu = Menu(name="部署", path="/burning", icon="tool", sort_order=1, is_hidden=False)
        db.add(menu)
        db.flush()
        permission = Permission(
            name="创建任务", code="task:add", type="button", menu_id=menu.id
        )
        db.add(permission)
        db.flush()
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
        user = User(
            username="operator-a",
            display_name="操作员A",
            password_hash="bcrypt-hash-for-test",
            role_id=role.id,
            status=1,
            token_version=2,
        )
        db.add(user)
        db.flush()
        setting = RepositoryProjectSetting(
            project_key="proj_demo",
            codearts_config_json=json.dumps(
                {"enabled": True, "project_id": "demo", "username": "codearts-user", "password": "secret"}
            ),
            updated_by_user_id=user.id,
        )
        db.add(setting)
        db.add(
            RepositoryProjectMember(
                project_key="proj_demo",
                user_id=user.id,
                role="admin",
                permissions_json='["repository:sync"]',
                inviter_user_id=user.id,
                joined_at=datetime(2026, 8, 10, 9, 0, 0),
            )
        )
        product = Product(
            name="业务测试板",
            chip_type="ARM",
            chip_model="STM32F407",
            serial_number="BOARD-SYNC-001",
        )
        burner = Burner(
            name="测试烧录器",
            type="ST-LINK",
            sn="BURNER-SYNC-001",
            port="USB#PORT_1",
            host_type="remote",
            host_address="10.0.0.21",
            status=1,
            is_enabled=1,
        )
        script = Script(
            name="同步烧录脚本",
            type="python",
            task_type="board",
            content="print('sync')",
            status=1,
            is_system=0,
        )
        db.add_all([product, burner, script])
        db.flush()
        task = BurningTask(
            task_no="TASK-SYNC-001",
            created_by_user_id=user.id,
            software_name="firmware.bin",
            task_type="board",
            status=2,
            progress_percent=100,
            product_id=product.id,
            burner_id=burner.id,
            script_id=script.id,
            result="success",
        )
        db.add(task)
        db.add(
            Record(
                created_by_user_id=user.id,
                project_key="proj_demo",
                serial_number="BOARD-SYNC-001",
                software_name="firmware.bin",
                operator="operator-a",
                operation_time=datetime(2026, 8, 10, 10, 0, 0),
                result="success",
                type="burn",
                log_data="burn completed",
            )
        )
        session = ProtocolSession(
            created_by_user_id=user.id,
            task_no="TASK-SYNC-001",
            target="BOARD-SYNC-001",
            protocol="canfd",
            config_json='{"bitrate":500000}',
            status=2,
            tx_count=1,
            rx_count=1,
        )
        db.add(session)
        db.flush()
        db.add(
            ProtocolLog(
                session_id=session.id,
                protocol="canfd",
                timestamp=datetime(2026, 8, 10, 10, 1, 0),
                direction="Tx",
                frame_id="123",
                dlc=2,
                data="AA BB",
            )
        )
        db.add(ProtocolTest(target="BOARD-SYNC-001", address="COM3", data="01 02", result="success"))
        db.commit()

    def test_complete_business_graph_replicates_to_two_clients(self):
        self._seed_full_business_graph()
        uploaded, conflicts, failed = sync.publish_authoritative_changes(self.server)
        self.assertGreater(uploaded, 10)
        self.assertEqual((conflicts, failed), (0, 0))

        states = self._states(self.server)
        for client in (self.client_a, self.client_b):
            applied, errors = sync.apply_canonical_states(client, states)
            client.commit()
            self.assertGreater(applied, 10)
            self.assertEqual(errors, [])
            user = client.query(User).filter(User.username == "operator-a").one()
            task = client.query(BurningTask).filter(BurningTask.task_no == "TASK-SYNC-001").one()
            self.assertEqual(task.created_by_user_id, user.id)
            self.assertEqual(client.query(Product).filter(Product.id == task.product_id).one().serial_number, "BOARD-SYNC-001")
            self.assertEqual(client.query(Burner).filter(Burner.id == task.burner_id).one().sn, "BURNER-SYNC-001")
            self.assertEqual(client.query(Script).filter(Script.id == task.script_id).one().name, "同步烧录脚本")
            self.assertEqual(client.query(ProtocolLog).count(), 1)
            config = json.loads(client.query(RepositoryProjectSetting).one().codearts_config_json)
            self.assertEqual(config["username"], "codearts-user")
            self.assertEqual(config["password"], "secret")

    def test_offline_change_reaches_server_then_other_client(self):
        self._seed_full_business_graph()
        sync.publish_authoritative_changes(self.server)
        states = self._states(self.server)
        sync.apply_canonical_states(self.client_a, states)
        sync.apply_canonical_states(self.client_b, states)
        self.client_a.commit()
        self.client_b.commit()

        product_a = self.client_a.query(Product).filter(Product.serial_number == "BOARD-SYNC-001").one()
        product_a.usage_description = "客户端A离线补充"
        task_a = self.client_a.query(BurningTask).filter(BurningTask.task_no == "TASK-SYNC-001").one()
        task_a.result = "offline-reviewed"
        self.client_a.commit()

        with patch.object(sync, "get_repository_sync_node_id", return_value="client-a"):
            sync.capture_local_business_changes(self.client_a)
        changes = [
            sync.change_wire_payload(row)
            for row in self.client_a.query(BusinessSyncChange)
            .filter(BusinessSyncChange.status == "pending")
            .order_by(BusinessSyncChange.id.asc())
            .all()
        ]
        results = sync.apply_changes_to_authority(self.server, origin_node_id="client-a", changes=changes)
        canonical = [result["canonical"] for result in results if result.get("canonical")]
        applied, errors = sync.apply_canonical_states(self.server, canonical)
        self.server.commit()
        self.assertGreaterEqual(applied, 2)
        self.assertEqual(errors, [])

        new_states = [state for state in self._states(self.server) if state["revision"] > len(states)]
        applied_b, errors_b = sync.apply_canonical_states(self.client_b, new_states)
        self.client_b.commit()
        self.assertGreaterEqual(applied_b, 2)
        self.assertEqual(errors_b, [])
        self.assertEqual(
            self.client_b.query(Product).filter(Product.serial_number == "BOARD-SYNC-001").one().usage_description,
            "客户端A离线补充",
        )
        self.assertEqual(
            self.client_b.query(BurningTask).filter(BurningTask.task_no == "TASK-SYNC-001").one().result,
            "offline-reviewed",
        )

    def test_concurrent_offline_edit_is_resolved_by_server_revision(self):
        self._seed_full_business_graph()
        sync.publish_authoritative_changes(self.server)
        baseline = self._states(self.server)
        for client in (self.client_a, self.client_b):
            sync.apply_canonical_states(client, baseline)
            client.commit()

        self.client_a.query(Product).filter(Product.serial_number == "BOARD-SYNC-001").one().config_description = "A版本"
        self.client_b.query(Product).filter(Product.serial_number == "BOARD-SYNC-001").one().config_description = "B版本"
        self.client_a.commit()
        self.client_b.commit()
        with patch.object(sync, "get_repository_sync_node_id", return_value="client-a"):
            sync.capture_local_business_changes(self.client_a)
        with patch.object(sync, "get_repository_sync_node_id", return_value="client-b"):
            sync.capture_local_business_changes(self.client_b)

        def pending(db):
            return [
                sync.change_wire_payload(row)
                for row in db.query(BusinessSyncChange)
                .filter(BusinessSyncChange.status == "pending", BusinessSyncChange.entity_type == "product")
                .all()
            ]

        result_a = sync.apply_changes_to_authority(self.server, origin_node_id="client-a", changes=pending(self.client_a))
        sync.apply_canonical_states(self.server, [item["canonical"] for item in result_a])
        self.server.commit()
        result_b = sync.apply_changes_to_authority(self.server, origin_node_id="client-b", changes=pending(self.client_b))
        self.assertEqual(result_b[0]["outcome"], "conflict_server_wins")
        self.assertEqual(result_b[0]["canonical"]["payload"]["fields"]["config_description"], "A版本")

    def test_parent_child_deletes_are_applied_in_foreign_key_safe_order(self):
        self._seed_full_business_graph()
        sync.publish_authoritative_changes(self.server)
        sync.apply_canonical_states(self.client_a, self._states(self.server))
        self.client_a.commit()

        session = self.client_a.query(ProtocolSession).one()
        self.client_a.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).delete()
        self.client_a.delete(session)
        self.client_a.commit()
        with patch.object(sync, "get_repository_sync_node_id", return_value="client-a"):
            sync.capture_local_business_changes(self.client_a)
        changes = [
            sync.change_wire_payload(row)
            for row in self.client_a.query(BusinessSyncChange)
            .filter(
                BusinessSyncChange.status == "pending",
                BusinessSyncChange.entity_type.in_(["protocol_session", "protocol_log"]),
            )
            .all()
        ]
        results = sync.apply_changes_to_authority(self.server, origin_node_id="client-a", changes=changes)
        canonical = [item["canonical"] for item in results if item.get("canonical")]
        _, errors = sync.apply_canonical_states(self.server, canonical)
        self.server.commit()
        self.assertEqual(errors, [])
        self.assertEqual(self.server.query(ProtocolLog).count(), 0)
        self.assertEqual(self.server.query(ProtocolSession).count(), 0)


if __name__ == "__main__":
    unittest.main()
