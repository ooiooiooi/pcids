import unittest

from backend.utils.db import DEFAULT_ROLE_PERMISSION_CODES, _all_default_permission_definitions


class DefaultRolePermissionTests(unittest.TestCase):
    def test_operator_has_operational_permissions_without_system_admin(self):
        permissions = DEFAULT_ROLE_PERMISSION_CODES["操作员"]
        self.assertIn("workbench:view", permissions)
        self.assertIn("burning:add", permissions)
        self.assertIn("protocol:execute", permissions)
        self.assertNotIn("user:add", permissions)
        self.assertNotIn("role:assign", permissions)

    def test_observer_is_read_only(self):
        permissions = DEFAULT_ROLE_PERMISSION_CODES["观察员"]
        self.assertIn("workbench:view", permissions)
        self.assertIn("record:view", permissions)
        self.assertTrue(all(code.endswith(":view") for code in permissions))

    def test_role_permission_tree_uses_required_names(self):
        permissions = {item["code"]: item["name"] for item in _all_default_permission_definitions()}
        expected = {
            "repository:view": "制品仓库查看",
            "repository:add": "新建项目",
            "repository:delete": "删除项目",
            "repository:download": "下载制品",
            "repository:perm_change": "项目成员及权限",
            "burning:view": "烧录安装管理任务历史",
            "burning:add": "创建任务",
            "burning:delete": "删除任务",
            "burner:view": "设备管理查看",
            "burner:add": "新增设备",
            "burner:edit": "编辑设备",
            "burner:delete": "删除设备",
            "injection:view": "执行记录",
            "injection:add": "新增注入任务",
            "injection:delete": "删除注入任务",
            "protocol:view": "执行记录",
            "protocol:execute": "执行通信协议验证",
            "protocol:delete": "删除协议验证记录",
            "user:view": "用户管理查看",
            "user:add": "新增用户",
            "user:edit": "编辑用户",
            "user:delete": "删除用户",
            "role:view": "角色管理查看",
            "role:add": "新增角色",
            "role:edit": "编辑角色",
            "role:delete": "删除角色",
        }
        for code, name in expected.items():
            self.assertEqual(permissions.get(code), name)

    def test_removed_permissions_are_not_in_role_tree(self):
        permissions = {item["code"] for item in _all_default_permission_definitions()}
        self.assertNotIn("repository:invite", permissions)
        self.assertNotIn("burning:execute", permissions)
        self.assertNotIn("burner:scan", permissions)
        self.assertNotIn("injection:detail", permissions)
        self.assertNotIn("protocol:export", permissions)
        self.assertNotIn("user:reset_pwd", permissions)
        self.assertNotIn("role:assign", permissions)


if __name__ == "__main__":
    unittest.main()
