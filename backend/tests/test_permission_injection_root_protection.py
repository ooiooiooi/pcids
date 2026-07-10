import unittest

from backend.routers.injections import _build_permission_remote_scripts


class PermissionInjectionRootProtectionTests(unittest.TestCase):
    def test_root_protection_verifies_immutable_attribute_and_write_denial(self):
        setup_script, cleanup_script = _build_permission_remote_scripts(
            {
                "target_path": "/tmp/pcids-target",
                "change_type": "remove_write",
                "root_protect": True,
            },
            42,
        )

        self.assertIn('chattr +i -- "$TARGET_PATH"', setup_script)
        self.assertIn("针对 Root 生效需要使用 root 登录或为当前用户配置免密 sudo", setup_script)
        self.assertIn('CURRENT_ATTRS="$(lsattr -d "$TARGET_PATH"', setup_script)
        self.assertIn("目标文件系统未应用 immutable 属性", setup_script)
        self.assertIn('.pcids_root_write_probe_42', setup_script)
        self.assertIn("Root 写入验证失败：目标文件仍可写入", setup_script)
        self.assertIn('chattr -i -- "\\$TARGET_PATH"', setup_script)
        self.assertIn("pcids_permission_recover_42.sh", cleanup_script)

    def test_standard_write_removal_does_not_include_root_probe(self):
        setup_script, _cleanup_script = _build_permission_remote_scripts(
            {
                "target_path": "/tmp/pcids-target",
                "change_type": "remove_write",
                "root_protect": False,
            },
            43,
        )

        self.assertIn('chmod a-w -- "$TARGET_PATH"', setup_script)
        self.assertNotIn(".pcids_root_write_probe_43", setup_script)
        self.assertNotIn("Root 写入验证通过", setup_script)


if __name__ == "__main__":
    unittest.main()
