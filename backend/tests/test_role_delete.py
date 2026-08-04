import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Permission, Role, RolePermission, User
from backend.routers import auth as _auth  # noqa: F401
from backend.routers.roles import delete_role


class RoleDeleteTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        admin_role = Role(name="admin-role", data_scope="all")
        target_role = Role(name="temporary-role", data_scope="all")
        permission = Permission(name="role-view", code="role:view", type="button")
        self.db.add_all([admin_role, target_role, permission])
        self.db.flush()
        self.db.add(RolePermission(role_id=target_role.id, permission_id=permission.id))
        self.admin = User(
            username="admin",
            password_hash="unused",
            role_id=admin_role.id,
            status=1,
        )
        self.db.add(self.admin)
        self.db.commit()
        self.db.refresh(self.admin)
        self.target_role_id = target_role.id

    def tearDown(self):
        self.db.close()

    def test_delete_role_with_loaded_permissions_removes_associations_once(self):
        result = asyncio.run(
            delete_role(
                self.target_role_id,
                db=self.db,
                current_user=self.admin,
                _=None,
            )
        )

        self.assertEqual(result["code"], 0)
        self.assertIsNone(self.db.query(Role).filter(Role.id == self.target_role_id).first())
        self.assertEqual(
            self.db.query(RolePermission)
            .filter(RolePermission.role_id == self.target_role_id)
            .count(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
