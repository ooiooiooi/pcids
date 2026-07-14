import sys
import unittest

from backend.routers.tasks import (
    _decorate_timeout_log,
    _build_local_script_execution_log,
    _build_task_exception_log,
    _execute_script_content_locally,
    _run_subprocess_command,
)


class TaskSubprocessStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_subprocess_output_callback_receives_stdout_and_stderr(self):
        chunks: list[tuple[str, str]] = []

        async def on_output(stream_name: str, text: str) -> None:
            chunks.append((stream_name, text))

        ok, stdout, stderr, reason = await _run_subprocess_command(
            [
                sys.executable,
                "-c",
                "import sys; print('out-line', flush=True); print('err-line', file=sys.stderr, flush=True)",
            ],
            timeout_seconds=10,
            output_callback=on_output,
        )

        self.assertTrue(ok, reason)
        self.assertIn("out-line", stdout)
        self.assertIn("err-line", stderr)
        self.assertTrue(any(name == "stdout" and "out-line" in text for name, text in chunks))
        self.assertTrue(any(name == "stderr" and "err-line" in text for name, text in chunks))

    async def test_timeout_keeps_captured_stdout_and_stderr_for_task_log(self):
        ok, stdout, stderr, reason = await _run_subprocess_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys,time; "
                    "print('before-timeout', flush=True); "
                    "print('stderr-before-timeout', file=sys.stderr, flush=True); "
                    "time.sleep(2)"
                ),
            ],
            timeout_seconds=0.3,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "脚本执行超时")
        log_text = _build_local_script_execution_log("timeout-script.py", stdout, stderr)
        self.assertIn("before-timeout", log_text)
        self.assertIn("stderr-before-timeout", log_text)

    async def test_non_streaming_subprocess_captures_output_without_live_callback(self):
        chunks: list[tuple[str, str]] = []

        async def on_output(stream_name: str, text: str) -> None:
            chunks.append((stream_name, text))

        ok, stdout, stderr, reason = await _run_subprocess_command(
            [
                sys.executable,
                "-c",
                "import sys; print('batch-out'); print('batch-err', file=sys.stderr)",
            ],
            timeout_seconds=10,
            output_callback=on_output,
            stream_output=False,
        )

        self.assertTrue(ok, reason)
        self.assertEqual(chunks, [])
        self.assertIn("batch-out", stdout)
        self.assertIn("batch-err", stderr)

    async def test_non_zero_script_uses_last_stderr_line_as_failure_reason(self):
        success, _log_text, failure_reason = await _execute_script_content_locally(
            "import sys\nprint('device permission denied', file=sys.stderr)\nsys.exit(7)\n",
            "python",
            {},
            10,
            "permission-error.py",
        )

        self.assertFalse(success)
        self.assertIn("device permission denied", failure_reason)
        self.assertIn("退出码 7", failure_reason)

    async def test_missing_executable_reports_tool_and_configuration_hint(self):
        ok, _stdout, _stderr, reason = await _run_subprocess_command(
            ["pcids-command-that-does-not-exist"],
            timeout_seconds=1,
        )

        self.assertFalse(ok)
        self.assertIn("pcids-command-that-does-not-exist", reason)
        self.assertIn("工具路径配置", reason)

    def test_exception_log_keeps_existing_output_and_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            log_text = _build_task_exception_log(
                "脚本执行异常: boom",
                existing_log="=== 执行脚本 ===\na.py",
                live_output="实时输出第一行",
                exc=exc,
                include_traceback=True,
            )

        self.assertIn("=== 执行脚本 ===", log_text)
        self.assertIn("实时输出第一行", log_text)
        self.assertIn("=== 异常详情 ===", log_text)
        self.assertIn("boom", log_text)
        self.assertIn("=== 异常堆栈 ===", log_text)
        self.assertIn("ValueError", log_text)

    async def test_non_zero_script_with_explicit_timeout_marker_is_treated_as_timeout(self):
        success, log_text, failure_reason = await _execute_script_content_locally(
            "@echo off\r\necho [ERROR] 脚本执行超时\r\nexit /b 124\r\n",
            "bat",
            {"TIMEOUT_SECONDS": "120"},
            120,
            "timeout-marker.bat",
        )

        self.assertFalse(success)
        self.assertEqual(failure_reason, "脚本执行超时")
        self.assertIn("[ERROR] 脚本执行超时", log_text)

        self.assertIn("=== Exit Code ===\n124", log_text)

    def test_timeout_summary_preserves_existing_log_output(self):
        log_text = _decorate_timeout_log("=== 执行脚本 ===\nfoo.bat\n=== 脚本输出 ===\nstep-1", 12)

        self.assertIn("脚本执行超时：已超过任务超时时间 12 秒", log_text)
        self.assertIn("=== 执行脚本 ===", log_text)
        self.assertIn("step-1", log_text)


if __name__ == "__main__":
    unittest.main()
