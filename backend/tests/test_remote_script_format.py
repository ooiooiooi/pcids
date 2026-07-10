import unittest

from backend.routers.tasks import _get_script_extension, _normalize_script_type


class RemoteScriptFormatTests(unittest.TestCase):
    def test_shell_scripts_keep_linux_extension_on_windows_host(self):
        self.assertEqual(_get_script_extension(_normalize_script_type("shell")), ".sh")
        self.assertEqual(_get_script_extension(_normalize_script_type("bash")), ".sh")

    def test_windows_batch_scripts_keep_batch_extension(self):
        self.assertEqual(_get_script_extension(_normalize_script_type("bat")), ".bat")


if __name__ == "__main__":
    unittest.main()
