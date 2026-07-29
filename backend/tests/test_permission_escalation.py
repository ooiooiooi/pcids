import asyncio
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Permission, Role, RolePermission, User
# Load routers in the same order as the application entry point. The existing
# router package imports auth before modules that depend on permission helpers.
from backend.routers import auth as _auth  # noqa: F401
from backend.utils.permission import (
    ensure_data_scope_assignable,
    ensure_permission_ids_assignable,
    ensure_role_assignable,
    require_super_admin,
)


class PermissionEscalationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        self.role_view = Permission(name="角色查看", code="role:view", type="button")
        self.role_edit = Permission(name="角色编辑", code="role:edit", type="button")
        self.user_delete = Permission(name="删除用户", code="user:delete", type="button")
        self.db.add_all([self.role_view, self.role_edit, self.user_delete])
        self.db.flush()

        self.manager_role = Role(name="受限管理员", data_scope="project")
        self.powerful_role = Role(name="高级管理员", data_scope="all")
        self.db.add_all([self.manager_role, self.powerful_role])
        self.db.flush()
        self.db.add_all(
            [
                RolePermission(role_id=self.manager_role.id, permission_id=self.role_view.id),
                RolePermission(role_id=self.manager_role.id, permission_id=self.role_edit.id),
                RolePermission(role_id=self.powerful_role.id, permission_id=self.user_delete.id),
            ]
        )
        self.db.commit()

        self.manager = User(
            username="role-manager",
            password_hash="unused",
            role_id=self.manager_role.id,
            status=1,
        )
        self.admin = User(
            username="admin",
            password_hash="unused",
            role_id=self.manager_role.id,
            status=1,
        )
        self.db.add_all([self.manager, self.admin])
        self.db.commit()
        self.db.refresh(self.manager)
        self.db.refresh(self.admin)

    def tearDown(self):
        self.db.close()

    def test_authenticated_non_admin_cannot_maintain_permission_catalogue(self):
        checker = require_super_admin()
        with self.assertRaises(HTTPException) as context:
            asyncio.run(checker(current_user=self.manager))
        self.assertEqual(context.exception.status_code, 403)

    def test_builtin_admin_can_maintain_permission_catalogue(self):
        checker = require_super_admin()
        asyncio.run(checker(current_user=self.admin))

    def test_role_manager_cannot_delegate_permission_they_do_not_hold(self):
        with self.assertRaises(HTTPException) as context:
            ensure_permission_ids_assignable(self.db, self.manager, [self.user_delete.id])
        self.assertEqual(context.exception.status_code, 403)

    def test_role_manager_can_delegate_own_permissions(self):
        assigned = ensure_permission_ids_assignable(
            self.db,
            self.manager,
            [self.role_view.id, self.role_edit.id],
        )
        self.assertEqual(set(assigned), {self.role_view.id, self.role_edit.id})

    def test_role_manager_cannot_assign_more_powerful_role(self):
        with self.assertRaises(HTTPException) as context:
            ensure_role_assignable(self.db, self.manager, self.powerful_role)
        self.assertEqual(context.exception.status_code, 403)

    def test_project_scope_manager_cannot_grant_all_data_scope(self):
        with self.assertRaises(HTTPException) as context:
            ensure_data_scope_assignable(self.manager, "all")
        self.assertEqual(context.exception.status_code, 403)

    def test_dynamic_project_scope_manager_cannot_name_unverified_projects(self):
        with self.assertRaises(HTTPException) as context:
            ensure_data_scope_assignable(self.manager, "project:secret-project")
        self.assertEqual(context.exception.status_code, 403)

    def test_unknown_permission_id_is_rejected_as_bad_request(self):
        with self.assertRaises(HTTPException) as context:
            ensure_permission_ids_assignable(self.db, self.manager, [999999])
        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
