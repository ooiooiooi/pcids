import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.models.product import Product
from backend.models.repository import Repository
from backend.models.script import Script
from backend.models.task import BurningTask
from backend.routers.tasks import (
    _EmbeddedTftpServer,
    _classify_serial_console_state,
    _execute_hybrid_task,
    _execute_hybrid_tftp_via_serial,
    _execute_os_task_via_sylix,
    _looks_like_existing_directory_listing,
    _interrupt_pmon_auto_boot,
    _validate_task_creation_payload,
    _probe_serial_port_access,
    _wait_for_stable_pmon_console,
    test_hybrid_connection as _test_hybrid_connection,
    test_os_connection as _test_os_connection,
)
from backend.utils.task_execution import ExecutionMonitor


class _FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.value


class _FakeDb:
    def __init__(self):
        self.repo = Repository(id=1, name="demo.bin", version="1.0.0")
        self.product = Product(id=2, name="demo-board")
        self.script = Script(id=3, name="hybrid_script", type="shell", content="echo ok", task_type="hybrid")

    def query(self, model):
        if model is Repository:
            return _FakeQuery(self.repo)
        if model is Product:
            return _FakeQuery(self.product)
        if model is Script:
            return _FakeQuery(self.script)
        return _FakeQuery(None)


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _FakeSerialConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class HybridTaskLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_ftp_passwordless_connection_test_fails(self):
        payload = {
            "transfer_protocol": "FTP+串口",
            "target_ip": "192.0.2.10",
            "server_port": 21,
            "serial_port": "/dev/ttyUSB0",
            "ftp_login_user": "root",
            "ftp_passwordless": True,
        }

        with patch("backend.routers.tasks.socket.create_connection", return_value=_FakeSocket()), patch(
            "backend.routers.tasks.os.path.exists", return_value=True
        ):
            response = await _test_hybrid_connection(payload)

        self.assertFalse(response["data"]["success"])
        self.assertFalse(response["data"]["ftp_login_ok"])
        self.assertIn("FTP", response["data"]["message"])

    def test_serial_access_probe_reports_occupied_port(self):
        with patch("backend.routers.tasks._is_serial_port_available", return_value=True), patch(
            "backend.routers.tasks.serial.Serial", side_effect=PermissionError("Access is denied")
        ):
            ok, message = _probe_serial_port_access("COM2", "115200")

        self.assertFalse(ok)
        self.assertIn("占用", message)

    def test_create_rejects_ftp_mode(self):
        task = BurningTask(
            id=1,
            repository_id=1,
            product_id=2,
            task_type="hybrid",
            software_name="demo.bin",
            target_ip="192.0.2.10",
            target_port=21,
            script_id=3,
        )
        config = {
            "task_type": "hybrid",
            "script_id": 3,
            "burn_mode": "FTP+串口",
            "transfer_protocol": "FTP+串口",
            "server_port": 21,
            "serial_port": "/dev/ttyUSB0",
            "baud_rate": "9600",
            "serial_login_user": "root",
            "serial_passwordless": True,
            "ftp_login_user": "root",
            "ftp_passwordless": True,
            "configured_board_address": "192.0.2.10",
            "board_target_address": "192.0.2.10",
            "local_ip": "192.0.2.1",
            "target_path": "/opt/control-app",
            "timeout_seconds": 120,
            "retries": 1,
        }

        fake_db = _FakeDb()
        with self.assertRaises(HTTPException) as context:
            _validate_task_creation_payload(fake_db, task, config, selected_burner=None, resolved_script=fake_db.script)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("烧录模式", context.exception.detail)

    async def test_ftp_upload_then_serial_script_execution(self):
        task = BurningTask(
            id=7,
            repository_id=1,
            product_id=2,
            task_type="hybrid",
            software_name="demo.bin",
            target_ip="192.0.2.10",
            target_port=21,
            script_id=3,
        )
        config = {
            "transfer_protocol": "FTP+串口",
            "server_port": 21,
            "serial_port": "/dev/ttyUSB0",
            "baud_rate": "9600",
            "serial_login_user": "root",
            "serial_passwordless": True,
            "ftp_login_user": "root",
            "ftp_login_password": "secret",
            "configured_board_address": "192.0.2.10",
            "board_target_address": "192.0.2.10",
            "target_path": "/opt/control-app",
            "target_filename": "bspls2kpcm2k01.elf",
        }
        script = Script(id=3, name="hybrid_script", type="shell", content="echo \"$FIRMWARE_PATH\"", task_type="hybrid")

        class FakeFtp:
            def connect(self, *_args, **_kwargs):
                return None

            def login(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def mkd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/opt/control-app"

            def storbinary(self, *_args, **_kwargs):
                return None

            def quit(self):
                return None

        with patch("backend.routers.tasks.os.path.exists", return_value=True), patch(
            "backend.routers.tasks.ftplib.FTP", return_value=FakeFtp()
        ), patch(
            "backend.routers.tasks._execute_hybrid_script_via_serial", return_value=(True, "serial ok", "")
        ) as serial_exec:
            ok, log, reason = await _execute_hybrid_task(
                task,
                config,
                __file__,
                script,
                {},
                timeout_seconds=120,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIn("FTP", log)
        self.assertIn("serial ok", log)
        self.assertEqual(serial_exec.call_args.kwargs["remote_env"]["FIRMWARE_PATH"], "/opt/control-app/bspls2kpcm2k01.elf")

    async def test_sylix_os_ftp_reset_reports_actionable_failure(self):
        task = BurningTask(
            id=17,
            repository_id=1,
            product_id=2,
            task_type="os",
            software_name="demo.elf",
            target_ip="192.168.1.230",
            target_port=21,
        )
        config = {
            "deployment_mode": "FTP+Telnet",
            "ftp_port": 21,
            "login_username": "root",
            "login_password": "secret",
            "install_dir": "/opt/control-app",
        }
        monitor = ExecutionMonitor(task_id=task.id)

        class ResettingFtp:
            def connect(self, *_args, **_kwargs):
                return None

            def login(self, *_args, **_kwargs):
                return None

            def mkd(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/opt/control-app"

            def set_pasv(self, *_args, **_kwargs):
                return None

            def storbinary(self, *_args, **_kwargs):
                raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")

            def close(self):
                return None

        with patch("backend.routers.tasks.os.path.exists", return_value=True), patch(
            "backend.routers.tasks.ftplib.FTP", return_value=ResettingFtp()
        ):
            ok, log, reason = await _execute_os_task_via_sylix(
                task,
                config,
                __file__,
                timeout_seconds=120,
                monitor=monitor,
            )

        self.assertFalse(ok)
        self.assertIn("FTP 控制连接被目标主机重置", reason)
        self.assertIn("目标板 FTP 服务", log)
        self.assertEqual(monitor.events[-1].status, "failed")
        self.assertEqual(monitor.events[-1].message, "翼辉 FTP 下发失败")
        self.assertIn("FTP 控制连接被目标主机重置", monitor.events[-1].details["reason"])

    async def test_sylix_os_upload_retries_port_mode_when_pasv_upload_resets(self):
        task = BurningTask(
            id=18,
            repository_id=1,
            product_id=2,
            task_type="os",
            software_name="demo.elf",
            target_ip="192.168.1.230",
            target_port=21,
        )
        config = {
            "deployment_mode": "FTP+Telnet",
            "ftp_port": 21,
            "login_username": "root",
            "login_password": "secret",
            "install_dir": "/opt/control-app",
        }
        pasv_modes = []

        class FallbackFtp:
            def __init__(self):
                self.passive = True

            def connect(self, *_args, **_kwargs):
                return None

            def login(self, *_args, **_kwargs):
                return None

            def mkd(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/opt/control-app"

            def set_pasv(self, passive):
                self.passive = passive
                pasv_modes.append(passive)

            def storbinary(self, *_args, **_kwargs):
                if self.passive:
                    raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")
                return None

            def quit(self):
                return None

            def close(self):
                return None

        with patch("backend.routers.tasks.os.path.exists", return_value=True), patch(
            "backend.routers.tasks.ftplib.FTP", side_effect=lambda *args, **kwargs: FallbackFtp()
        ):
            ok, log, reason = await _execute_os_task_via_sylix(
                task,
                config,
                __file__,
                timeout_seconds=120,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(pasv_modes, [True, False])
        self.assertIn("PASV 被动模式 上传失败", log)
        self.assertIn("PORT 主动模式 上传成功", log)

    async def test_sylix_os_upload_can_append_startup_autostart_entry(self):
        task = BurningTask(
            id=19,
            repository_id=1,
            product_id=2,
            task_type="os",
            software_name="demo.elf",
            target_ip="192.168.1.230",
            target_port=21,
        )
        config = {
            "deployment_mode": "FTP",
            "ftp_port": 21,
            "login_username": "root",
            "login_password": "secret",
            "install_dir": "/apps",
            "boot_autostart": True,
        }

        class StartupFtp:
            def __init__(self):
                self.uploaded_artifact = None
                self.uploaded_startup = None

            def connect(self, *_args, **_kwargs):
                return None

            def login(self, *_args, **_kwargs):
                return None

            def set_pasv(self, *_args, **_kwargs):
                return None

            def mkd(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/apps"

            def storbinary(self, command, fileobj):
                if command == "STOR startup.sh":
                    self.uploaded_startup = fileobj.read().decode("utf-8")
                else:
                    self.uploaded_artifact = command
                return None

            def retrbinary(self, command, callback):
                self.retr_command = command
                callback(b"#!/bin/sh\n/apps/oldApp\n")

            def voidcmd(self, *_args, **_kwargs):
                return None

            def quit(self):
                return None

            def close(self):
                return None

        ftp = StartupFtp()
        monitor = ExecutionMonitor(task_id=task.id)
        with patch("backend.routers.tasks.os.path.exists", return_value=True), patch(
            "backend.routers.tasks.ftplib.FTP", return_value=ftp
        ):
            ok, log, reason = await _execute_os_task_via_sylix(
                task,
                config,
                __file__,
                timeout_seconds=120,
                monitor=monitor,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(ftp.retr_command, "RETR /etc/startup.sh")
        self.assertEqual(ftp.uploaded_artifact, "STOR test_hybrid_task_logic.py")
        self.assertIn("/apps/test_hybrid_task_logic.py", ftp.uploaded_startup)
        self.assertIn("已写入开机自启", log)
        autostart_events = [event for event in monitor.events if event.stage == "sylix-autostart"]
        self.assertEqual(len(autostart_events), 1)
        self.assertEqual(autostart_events[0].message, "开机自启写入完成")
        self.assertEqual(autostart_events[0].details["startup_file"], "/etc/startup.sh")
        self.assertEqual(autostart_events[0].details["command"], "/apps/test_hybrid_task_logic.py")

    async def test_sylix_os_connection_test_checks_ftp_username_and_password(self):
        login_calls = []

        class CredentialCheckingFtp:
            def connect(self, host, port, timeout=0):
                self.connected = (host, port, timeout)

            def login(self, username, password):
                login_calls.append((username, password))
                if (username, password) != ("root", "secret"):
                    raise RuntimeError("530 Login incorrect")

            def mkd(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/opt/control-app"

            def storbinary(self, *_args, **_kwargs):
                return None

            def delete(self, *_args, **_kwargs):
                return None

            def quit(self):
                return None

        payload = {
            "os_type": "yinghui",
            "target_ip": "192.168.1.230",
            "ftp_port": 21,
            "telnet_port": 23,
            "deployment_mode": "FTP+Telnet",
            "login_username": "root",
            "login_password": "secret",
        }

        with patch("backend.routers.tasks.ftplib.FTP", return_value=CredentialCheckingFtp()), patch(
            "backend.routers.tasks.socket.create_connection", return_value=_FakeSocket()
        ):
            response = await _test_os_connection(payload)

        self.assertTrue(response["data"]["success"])
        self.assertIn("FTP 登录及写入验证成功", response["data"]["message"])
        self.assertEqual(login_calls, [("root", "secret")])

    async def test_sylix_os_connection_test_rejects_wrong_ftp_password(self):
        class CredentialCheckingFtp:
            def connect(self, *_args, **_kwargs):
                return None

            def login(self, username, password):
                if (username, password) != ("root", "secret"):
                    raise RuntimeError("530 Login incorrect")

            def close(self):
                return None

        payload = {
            "os_type": "yinghui",
            "target_ip": "192.168.1.230",
            "ftp_port": 21,
            "telnet_port": 23,
            "deployment_mode": "FTP+Telnet",
            "login_username": "root",
            "login_password": "wrong",
        }

        with patch("backend.routers.tasks.ftplib.FTP", return_value=CredentialCheckingFtp()):
            response = await _test_os_connection(payload)

        self.assertFalse(response["data"]["success"])
        self.assertIn("530 Login incorrect", response["data"]["message"])

    async def test_sylix_os_connection_test_rejects_unwritable_install_dir(self):
        class UnwritableFtp:
            def connect(self, *_args, **_kwargs):
                return None

            def login(self, *_args, **_kwargs):
                return None

            def mkd(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/opt/control-app"

            def storbinary(self, *_args, **_kwargs):
                raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接。")

            def close(self):
                return None

        payload = {
            "os_type": "yinghui",
            "target_ip": "192.168.1.230",
            "ftp_port": 21,
            "telnet_port": 23,
            "deployment_mode": "FTP+Telnet",
            "login_username": "root",
            "login_password": "secret",
            "install_dir": "/opt/control-app",
        }

        with patch("backend.routers.tasks.ftplib.FTP", return_value=UnwritableFtp()):
            response = await _test_os_connection(payload)

        self.assertFalse(response["data"]["success"])
        self.assertIn("登录或写入验证失败", response["data"]["message"])
        self.assertIn("FTP 控制连接被目标主机重置", response["data"]["message"])

    async def test_sylix_os_connection_test_supports_passwordless_ftp_login(self):
        login_calls = []

        class EmptyPasswordFtp:
            def connect(self, *_args, **_kwargs):
                return None

            def login(self, username, password):
                login_calls.append((username, password))
                if (username, password) != ("root", ""):
                    raise RuntimeError("expected empty password")

            def mkd(self, *_args, **_kwargs):
                return None

            def cwd(self, *_args, **_kwargs):
                return None

            def pwd(self):
                return "/opt/control-app"

            def storbinary(self, *_args, **_kwargs):
                return None

            def delete(self, *_args, **_kwargs):
                return None

            def quit(self):
                return None

        payload = {
            "os_type": "yinghui",
            "target_ip": "192.168.1.230",
            "ftp_port": 21,
            "telnet_port": 23,
            "deployment_mode": "FTP+Telnet",
            "login_username": "root",
            "login_passwordless": True,
        }

        with patch("backend.routers.tasks.ftplib.FTP", return_value=EmptyPasswordFtp()), patch(
            "backend.routers.tasks.socket.create_connection", return_value=_FakeSocket()
        ):
            response = await _test_os_connection(payload)

        self.assertTrue(response["data"]["success"])
        self.assertIn("FTP 免密登录及写入验证成功", response["data"]["message"])
        self.assertEqual(login_calls, [("root", "")])

    async def test_tftp_mode_stages_artifact_and_runs_pmon_serial_flow(self):
        task = BurningTask(
            id=8,
            repository_id=1,
            product_id=2,
            task_type="hybrid",
            software_name="demo.elf",
            target_ip="192.168.1.230",
            target_port=69,
            script_id=3,
        )
        config = {
            "burn_mode": "TFTP+串口",
            "transfer_protocol": "TFTP+串口",
            "server_port": 69,
            "serial_port": "/dev/ttyUSB0",
            "baud_rate": "115200",
            "serial_login_user": "root",
            "serial_passwordless": True,
            "configured_board_address": "192.168.1.230",
            "board_target_address": "192.168.1.230",
            "local_ip": "192.168.1.100",
            "target_path": "/media/hdd0",
        }
        script = Script(id=3, name="hybrid_script", type="shell", content="echo ok", task_type="hybrid")

        class FakeTftpServer:
            def __init__(self):
                self.port = 69
                self._events = ["[INFO] 内置 TFTP 服务已启动：0.0.0.0:69，根目录：D:/workspace/pcids/.runtime/tftp"]

            def snapshot_events(self):
                return list(self._events)

            def stop(self):
                self._events.append("[INFO] 内置 TFTP 服务已停止")

        with patch("backend.routers.tasks.os.path.exists", return_value=True), patch(
            "backend.routers.tasks._prepare_hybrid_tftp_artifact",
            return_value=("D:/workspace/pcids/.runtime/tftp/demo.elf", "demo.elf"),
        ) as prepare, patch(
            "backend.routers.tasks._start_embedded_tftp_server",
            return_value=FakeTftpServer(),
        ) as start_tftp, patch(
            "backend.routers.tasks._self_test_embedded_tftp_server", return_value=(True, "selftest ok")
        ), patch(
            "backend.routers.tasks._probe_sylixos_partitioned_board_via_serial", return_value=(True, "board info ok", "")
        ) as probe, patch(
            "backend.routers.tasks._execute_hybrid_tftp_via_serial", return_value=(True, "pmon ok", "")
        ) as serial_exec, patch(
            "backend.routers.tasks._wait_for_tcp_service", return_value=(True, "ftp ready")
        ), patch(
            "backend.routers.tasks._upload_sylixos_partition_files_via_ftp", return_value=["ftp ok"]
        ) as ftp_upload:
            ok, log, reason = await _execute_hybrid_task(
                task,
                config,
                __file__,
                script,
                {},
                timeout_seconds=120,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIn("TFTP", log)
        self.assertIn("pmon ok", log)
        self.assertIn("ftp ok", log)
        self.assertIn("内置 TFTP 服务已启动", log)
        prepare.assert_called_once()
        start_tftp.assert_called_once()
        probe.assert_not_called()
        serial_exec.assert_called_once()
        self.assertEqual(ftp_upload.call_args.args[3], "demo.elf")

    def test_tftp_serial_flow_runs_partition_initialization_after_load(self):
        written_commands = []

        def capture_write(_connection, text):
            written_commands.append(text.strip())

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text", side_effect=capture_write
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", return_value="Version: PMON2000 3.3\n"
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console",
            return_value=("pmon", "Version: PMON2000 3.3"),
        ), patch(
            "backend.routers.tasks._wait_for_sylixos_command_channel",
            return_value=("[root@sylixos:/root]# ", True),
        ), patch(
            "backend.routers.tasks.time.monotonic",
            side_effect=list(range(1000)),
        ), patch("backend.routers.tasks.time.sleep"):
            ok, log, reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=120,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(
            [item for item in written_commands if item],
            [
                "reboot",
                "ifconfig syn0 192.168.1.230",
                "load tftp://192.168.1.100/demo.elf",
                "set al1 /dev/fs/fat@wd0/demo.elf",
                "g",
                "ifconfig eth0 inet 192.168.1.230 netmask 255.255.255.0",
            ],
        )
        self.assertIn("hdd0", log)
        self.assertIn("hdd1", log)
        self.assertIn("启动项", log)

    def test_tftp_serial_flow_does_not_send_q_when_initial_state_is_sylixos_shell(self):
        written_commands = []
        read_values = iter(
            [
                "[root@sylixos:/root]# ",
                "[root@sylixos:/root]# ",
                "PMON boot",
                "SylixOS license\n[root@sylixos:/root]# ",
                "",
                "",
                "",
            ]
        )

        def capture_write(_connection, text):
            written_commands.append(text.strip())

        def fake_read(_connection, _timeout):
            return next(read_values, "")

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text", side_effect=capture_write
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", side_effect=fake_read
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console",
            return_value=("pmon", "PMON> "),
        ), patch(
            "backend.routers.tasks._wait_for_sylixos_command_channel",
            return_value=("[root@sylixos:/root]# ", True),
        ), patch(
            "backend.routers.tasks._is_pmon_load_complete_output",
            return_value=True,
        ), patch("backend.routers.tasks.time.monotonic", side_effect=list(range(1000))), patch("backend.routers.tasks.time.sleep"):
            ok, _log, _reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=120,
                tftp_events_getter=lambda: [
                    "[INFO] TFTP RRQ：demo.elf，模式：octet，客户端：('192.168.1.230', 1025)",
                    "[INFO] TFTP 发送完成：demo.elf -> ('192.168.1.230', 1025)",
                ],
            )

        self.assertTrue(ok)
        self.assertNotIn("q", [item for item in written_commands if item])
        self.assertIn("reboot", [item for item in written_commands if item])

    def test_classify_serial_console_state(self):
        self.assertEqual(_classify_serial_console_state("Version: PMON2000 3.3"), "pmon_boot")
        self.assertEqual(_classify_serial_console_state("PMON> "), "pmon")
        self.assertEqual(_classify_serial_console_state("[root@sylixos:/root]#"), "sylixos")
        self.assertEqual(
            _classify_serial_console_state("Invalid command. Use 'f' for frequency, 'm' for mode, 'q' to quit."),
            "interactive_app",
        )
        self.assertEqual(_classify_serial_console_state("plain boot log"), "unknown")

    def test_pmon_load_complete_requires_entry_or_prompt_after_load(self):
        from backend.routers.tasks import _is_pmon_load_complete_output

        self.assertTrue(_is_pmon_load_complete_output("Entry address is 80200000\nPMON> "))
        self.assertTrue(_is_pmon_load_complete_output("load tftp://192.168.1.100/demo.elf\nPMON> "))
        self.assertFalse(_is_pmon_load_complete_output("Loading file: tftp://192.168.1.100/demo.elf"))

    def test_tftp_serial_flow_stops_when_serial_has_no_echo(self):
        written_commands = []

        def capture_write(_connection, text):
            written_commands.append(text.strip())

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text", side_effect=capture_write
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", return_value=""
        ), patch(
            "backend.routers.tasks._interrupt_pmon_auto_boot", return_value=""
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console", return_value=("unknown", "")
        ), patch("backend.routers.tasks.time.sleep"):
            ok, log, reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=120,
            )

        self.assertFalse(ok)
        self.assertIn("未识别到 PMON", reason)
        self.assertIn("q", [item for item in written_commands if item])
        self.assertIn("reboot", [item for item in written_commands if item])
        self.assertIn("串口暂无有效回显", log)

    def test_tftp_serial_flow_accepts_late_board_rrq(self):
        tftp_poll_count = 0

        def fake_events():
            nonlocal tftp_poll_count
            tftp_poll_count += 1
            if tftp_poll_count < 40:
                return []
            return [
                "[INFO] TFTP RRQ：demo.elf，模式：octet，客户端：('192.168.1.230', 12000)",
                "[INFO] TFTP 发送完成：demo.elf -> ('192.168.1.230', 12000)",
            ]

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text"
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", return_value="Version: PMON2000 3.3\n"
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console",
            return_value=("pmon", "PMON> "),
        ), patch(
            "backend.routers.tasks._wait_for_sylixos_command_channel",
            return_value=("[root@sylixos:/root]# ", True),
        ), patch(
            "backend.routers.tasks._is_pmon_load_complete_output",
            return_value=True,
        ), patch(
            "backend.routers.tasks.time.monotonic",
            side_effect=list(range(4000)),
        ), patch("backend.routers.tasks.time.sleep"):
            ok, log, reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=120,
                tftp_events_getter=fake_events,
            )

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertIn("TFTP+串口流程已执行 g", log)

    def test_tftp_serial_flow_reports_unexpected_rrq_client(self):
        def fake_events():
            return [
                "[INFO] TFTP RRQ：demo.elf，模式：octet，客户端：('192.168.1.100', 12001)",
                "[INFO] TFTP 发送完成：demo.elf -> ('192.168.1.100', 12001)",
            ]

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text"
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", return_value="Version: PMON2000 3.3\n"
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console",
            return_value=("pmon", "PMON> "),
        ), patch(
            "backend.routers.tasks.time.monotonic",
            side_effect=list(range(1000)),
        ), patch("backend.routers.tasks.time.sleep"):
            ok, log, reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=20,
                tftp_events_getter=fake_events,
            )

        self.assertFalse(ok)
        self.assertIn("客户端地址与设置的板卡地址不一致", reason)
        self.assertIn("192.168.1.100", log)

    def test_tftp_serial_flow_stops_when_pmon_probe_falls_back_to_interactive_app(self):
        written_commands = []

        def capture_write(_connection, text):
            written_commands.append(text.strip())

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text", side_effect=capture_write
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", return_value="Version: PMON2000 3.3\n"
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console",
            return_value=(
                "interactive_app",
                "Version: PMON2000 3.3\n[PROBE] devls\nInvalid command. Use 'f' for frequency, 'm' for mode, 'q' to quit.",
            ),
        ), patch(
            "backend.routers.tasks.time.monotonic",
            side_effect=list(range(1000)),
        ), patch("backend.routers.tasks.time.sleep"):
            ok, log, reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=120,
            )

        self.assertFalse(ok)
        self.assertIn("未进入 PMON", reason)
        self.assertNotIn("ifconfig syn0 192.168.1.230", [item for item in written_commands if item])

    def test_wait_for_stable_pmon_console_uses_probe_output(self):
        class ProbeConnection:
            def __init__(self):
                self.reads = [
                    "Version: PMON2000 3.3\n",
                    "syn0 syn1 usb0 wd0\n",
                ]

            def write(self, _payload):
                return None

            def flush(self):
                return None

            @property
            def in_waiting(self):
                return 1

            def read(self, _size):
                if self.reads:
                    return self.reads.pop(0).encode("utf-8")
                return b""

        with patch("backend.routers.tasks.time.sleep"):
            state, output = _wait_for_stable_pmon_console(
                ProbeConnection(),
                timeout_seconds=5,
                stabilize_seconds=2,
                probe_interval_seconds=1,
            )

        self.assertEqual(state, "pmon")
        self.assertIn("syn0 syn1 usb0 wd0", output)

    def test_interrupt_pmon_auto_boot_sends_ctrl_u_on_abort_prompt(self):
        class AutoBootConnection:
            def __init__(self):
                self.reads = [
                    "AUTO\nPress <Enter> to execute loading image:/dev/fs/fat@wd0/bspls2kpcm2k01.elf\nPress 'ctrl-u' to abort.\n",
                    "PMON> ",
                ]
                self.writes = []

            def write(self, payload):
                self.writes.append(payload)

            def flush(self):
                return None

            @property
            def in_waiting(self):
                return 1

            def read(self, _size):
                if self.reads:
                    return self.reads.pop(0).encode("utf-8")
                return b""

        conn = AutoBootConnection()
        with patch("backend.routers.tasks.time.sleep"):
            output = _interrupt_pmon_auto_boot(
                conn,
                timeout_seconds=10,
                abort_burst_seconds=1,
            )

        self.assertIn("Press 'ctrl-u' to abort", output)
        self.assertIn("PMON> ", output)
        self.assertTrue(any(payload == b"\x15" for payload in conn.writes))

    def test_tftp_serial_flow_stops_when_reboot_enters_sylixos(self):
        written_commands = []

        def capture_write(_connection, text):
            written_commands.append(text.strip())

        with patch("backend.routers.tasks._open_hybrid_serial_connection", return_value=_FakeSerialConnection()), patch(
            "backend.routers.tasks._serial_write_text", side_effect=capture_write
        ), patch("backend.routers.tasks._serial_write_bytes"), patch(
            "backend.routers.tasks._serial_read_text", return_value="Version: PMON2000 3.3\n"
        ), patch(
            "backend.routers.tasks._wait_for_stable_pmon_console",
            return_value=("sylixos", "[root@sylixos:/root]# "),
        ), patch(
            "backend.routers.tasks.time.monotonic",
            side_effect=list(range(1000)),
        ), patch("backend.routers.tasks.time.sleep"):
            ok, log, reason = _execute_hybrid_tftp_via_serial(
                serial_port="COM3",
                baud_rate="115200",
                board_target_address="192.168.1.230",
                local_ip="192.168.1.100",
                tftp_filename="demo.elf",
                sylixos_netmask="255.255.255.0",
                timeout_seconds=120,
            )

        self.assertFalse(ok)
        self.assertIn("未成功抢占到 PMON", reason)
        self.assertNotIn("load tftp://192.168.1.100/demo.elf", [item for item in written_commands if item])
        self.assertIn("[root@sylixos:/root]#", log)

    def test_invalid_interactive_app_output_is_not_partition_listing(self):
        output = """ls -la /media/hdd0
Invalid command. Use 'f' for frequency, 'm' for mode, 'q' to quit.
Enter 'f' to change frequency (1-10000ms), 'm' to show mode, 'q' to quit:
"""

        self.assertFalse(_looks_like_existing_directory_listing(output))

    def test_embedded_tftp_server_serves_single_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            demo_path = f"{temp_dir}/demo.elf"
            with open(demo_path, "wb") as file_obj:
                file_obj.write(b"pcids-tftp-demo")

            server = _EmbeddedTftpServer(root_dir=temp_dir, bind_host="127.0.0.1", port=0).start()
            try:
                client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                client.settimeout(2)
                request = b"\x00\x01demo.elf\x00octet\x00"
                client.sendto(request, ("127.0.0.1", server.port))
                packet, transfer_addr = client.recvfrom(1024)
                self.assertEqual(packet[:2], b"\x00\x03")
                self.assertEqual(packet[2:4], b"\x00\x01")
                self.assertEqual(packet[4:], b"pcids-tftp-demo")
                client.sendto(b"\x00\x04\x00\x01", transfer_addr)
                time.sleep(0.1)
                self.assertTrue(any("TFTP RRQ" in entry for entry in server.snapshot_events()))
            finally:
                client.close()
                server.stop()


if __name__ == "__main__":
    unittest.main()
