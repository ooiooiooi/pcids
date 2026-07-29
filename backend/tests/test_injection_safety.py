import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.models import Injection, InjectionRun
from backend.routers import injections
from backend.routers.injections import injection_run_to_dict, injection_to_dict


class InjectionSafetyTests(unittest.TestCase):
    def test_injection_response_redacts_ssh_credentials(self):
        injection = SimpleNamespace(
            id=1,
            type="network_error",
            target="10.0.0.8",
            config=json.dumps(
                {
                    "login_username": "root",
                    "login_password": "secret",
                    "private_key_content": "pem-data",
                    "network_interface": "eth0",
                    "proxy": {"password": "nested-secret"},
                }
            ),
            status=0,
            result=None,
            created_at=None,
            updated_at=None,
        )

        config = json.loads(injection_to_dict(injection)["config"])

        self.assertEqual(config["login_password"], "******")
        self.assertEqual(config["private_key_content"], "******")
        self.assertEqual(config["proxy"]["password"], "******")
        self.assertEqual(config["network_interface"], "eth0")

    def test_malformed_config_is_not_echoed(self):
        injection = SimpleNamespace(
            id=1,
            type="network_error",
            target="10.0.0.8",
            config="login_password=plain-secret",
            status=0,
            result=None,
            created_at=None,
            updated_at=None,
        )

        self.assertEqual(injection_to_dict(injection)["config"], "******")

    def test_injection_run_response_redacts_ssh_credentials(self):
        run = SimpleNamespace(
            id=2,
            injection_id=1,
            task_no="20260729001",
            type="permission_error",
            target="10.0.0.8",
            config=json.dumps({"password": "secret", "ssh_private_key": "pem-data"}),
            exec_status=1,
            result=None,
            executor="operator",
            ip_address="127.0.0.1",
            exec_time=None,
        )

        config = json.loads(injection_run_to_dict(run)["config"])

        self.assertEqual(config["password"], "******")
        self.assertEqual(config["ssh_private_key"], "******")


class InjectionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _session_for(run, injection):
        db = MagicMock()

        def query(model):
            result = MagicMock()
            result.filter.return_value = result
            result.order_by.return_value = result
            result.all.return_value = [run] if model is InjectionRun else []
            result.first.return_value = injection if model is Injection else None
            return result

        db.query.side_effect = query
        return db

    async def test_interrupted_power_injection_is_restored_and_closed(self):
        run = SimpleNamespace(
            id=11,
            injection_id=3,
            type="power_off",
            target="board",
            config=json.dumps({"power_port": "COM8"}),
            exec_status=1,
            result="running",
            exec_time=None,
        )
        injection = SimpleNamespace(id=3, config=run.config, status=1, result="running")
        db = self._session_for(run, injection)

        with (
            patch.object(injections, "SessionLocal", return_value=db),
            patch.object(injections, "power_on") as power_on,
        ):
            await injections.recover_interrupted_injections()

        power_on.assert_called_once_with("COM8")
        self.assertEqual(run.exec_status, 4)
        self.assertEqual(injection.status, 2)
        db.commit.assert_called_once_with()
        db.close.assert_called_once_with()

    async def test_interrupted_storage_injection_removes_persisted_marker_files(self):
        config = {
            "location": "/tmp",
            "run_marker": "12",
            "login_username": "root",
        }
        run = SimpleNamespace(
            id=12,
            injection_id=4,
            type="storage_full",
            target="10.0.0.8",
            config=json.dumps(config),
            exec_status=1,
            result="running",
            exec_time=None,
        )
        injection = SimpleNamespace(id=4, config=run.config, status=1, result="running")
        db = self._session_for(run, injection)

        with (
            patch.object(injections, "SessionLocal", return_value=db),
            patch.object(injections, "normalize_network_error_config", return_value=config),
            patch.object(injections, "run_remote_shell_command", return_value=(0, "")) as run_remote,
        ):
            await injections.recover_interrupted_injections()

        command = run_remote.call_args.args[1]
        self.assertIn(".pcids_storage_full_12", command)
        self.assertIn("-delete", command)
        self.assertEqual(run.exec_status, 4)


if __name__ == "__main__":
    unittest.main()
