import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.routers.repositories import _require_project_permission


class RepositoryProjectPermissionTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(
            username="project-member",
            role=SimpleNamespace(data_scope="all"),
        )

    def test_all_data_scope_does_not_bypass_project_permission(self):
        with (
            patch("backend.routers.repositories._get_current_user_project_role", return_value="member"),
            patch(
                "backend.routers.repositories._get_project_permissions_by_group",
                return_value={"member": {"download_file": False}},
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                _require_project_permission(object(), "proj_demo", self.user, "download_file")

        self.assertEqual(context.exception.status_code, 403)

    def test_enabled_project_permission_is_allowed(self):
        with (
            patch("backend.routers.repositories._get_current_user_project_role", return_value="member"),
            patch(
                "backend.routers.repositories._get_project_permissions_by_group",
                return_value={"member": {"mark_flash_file": True}},
            ),
        ):
            _require_project_permission(object(), "proj_demo", self.user, "mark_flash_file")

    def test_non_member_is_rejected(self):
        with patch("backend.routers.repositories._get_current_user_project_role", return_value=None):
            with self.assertRaises(HTTPException) as context:
                _require_project_permission(object(), "proj_demo", self.user, "delete_file")

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, "无项目权限")


if __name__ == "__main__":
    unittest.main()
