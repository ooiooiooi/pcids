import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

from backend import main


def test_lifespan_exposes_health_before_slow_hardware_diagnostics_finish():
    diagnostics_started = threading.Event()
    release_diagnostics = threading.Event()

    def slow_diagnostics():
        diagnostics_started.set()
        release_diagnostics.wait(timeout=5)

    async def exercise_lifespan():
        fake_app = SimpleNamespace(state=SimpleNamespace())
        with (
            patch.object(main, "describe_artifact_master_key_source", return_value={"source": "test"}),
            patch.object(main, "init_db"),
            patch.object(main.tasks, "recover_interrupted_tasks"),
            patch.object(main.repositories, "recover_repository_auto_sync_jobs"),
            patch.object(main.injections, "recover_interrupted_injections"),
            patch.object(main.injections, "shutdown_active_injections"),
            patch.object(main.protocol_tests, "cleanup_protocol_session_resources"),
            patch.object(main, "_run_startup_diagnostics", side_effect=slow_diagnostics),
        ):
            async with main.lifespan(fake_app):
                started = await asyncio.to_thread(diagnostics_started.wait, 1)
                assert started is True
                assert release_diagnostics.is_set() is False
                release_diagnostics.set()
                await asyncio.wait_for(fake_app.state.startup_diagnostics_task, timeout=2)

    asyncio.run(exercise_lifespan())


def test_noncritical_startup_diagnostics_do_not_raise():
    with (
        patch.object(main, "configure_bundled_tools", side_effect=RuntimeError("vendor probe failed")),
        patch.object(main.logger, "exception") as exception_log,
    ):
        main._run_startup_diagnostics()

    exception_log.assert_called_once_with("startup.diagnostics.failed")


def test_operation_log_sanitizes_nested_injection_config_secrets():
    sanitized = main._sanitize_log_data(
        {
            "config": '{"login_username":"root","login_password":"secret","auth":{"private_key":"pem"}}',
        }
    )

    assert "secret" not in sanitized["config"]
    assert "pem" not in sanitized["config"]
    assert "***" in sanitized["config"]


def test_operation_log_does_not_echo_malformed_config_text():
    sanitized = main._sanitize_log_data({"config": "login_password=secret"})

    assert sanitized["config"] == "***"
