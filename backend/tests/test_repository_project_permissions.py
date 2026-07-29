import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.routers.repositories import _ensure_project_member_seed, _require_project_permission


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

    def test_non_admin_cannot_claim_an_unseeded_existing_project(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        self.user.id = 9

        with self.assertRaises(HTTPException) as context:
            _ensure_project_member_seed(db, "proj_existing", self.user)

        self.assertEqual(context.exception.status_code, 403)
        db.add.assert_not_called()

    def test_new_project_creator_can_seed_membership_explicitly(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        self.user.id = 9

        _ensure_project_member_seed(
            db,
            "proj_new",
            self.user,
            allow_creator=True,
        )

        db.add.assert_called_once()
        db.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
