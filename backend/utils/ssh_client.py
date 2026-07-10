from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import os
import posixpath
import shlex
import socket

import paramiko


@dataclass
class SSHCommandResult:
    success: bool
    stdout: str
    stderr: str
    reason: str


class SSHClientSession:
    """Project-owned SSH/SFTP client that does not depend on system ssh tools."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str = "",
        auth_type: str = "key",
        private_key_path: str = "",
        connect_timeout: int = 10,
    ) -> None:
        self.host = host
        self.port = port or 22
        self.username = username
        self.password = password
        self.auth_type = str(auth_type or "key").strip().lower()
        self.private_key_path = private_key_path
        self.connect_timeout = connect_timeout
        self.client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        use_password = self.auth_type == "password"
        if use_password and not self.password:
            raise RuntimeError("缺少登录密码，无法建立 SSH 连接")
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password if use_password else None,
                key_filename=self.private_key_path or None,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
                allow_agent=not use_password,
                look_for_keys=not use_password,
            )
        except (paramiko.SSHException, socket.error, OSError) as exc:
            raise RuntimeError(f"SSH 连接失败：{exc}") from exc
        self.client = client

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None

    def __enter__(self) -> "SSHClientSession":
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def run(self, command: str, timeout: Optional[int] = None) -> SSHCommandResult:
        if not self.client:
            raise RuntimeError("SSH 连接尚未建立")
        try:
            _stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            stdout_text = stdout.read().decode("utf-8", errors="replace").strip()
            stderr_text = stderr.read().decode("utf-8", errors="replace").strip()
            exit_code = stdout.channel.recv_exit_status()
            reason = "" if exit_code == 0 else stderr_text or stdout_text or f"远程命令退出码：{exit_code}"
            return SSHCommandResult(exit_code == 0, stdout_text, stderr_text, reason)
        except (paramiko.SSHException, socket.timeout, OSError) as exc:
            return SSHCommandResult(False, "", "", f"远程命令执行失败：{exc}")

    def upload(self, local_path: str, remote_path: str) -> None:
        if not self.client:
            raise RuntimeError("SSH 连接尚未建立")
        if not os.path.isfile(local_path):
            raise RuntimeError(f"待上传文件不存在：{local_path}")
        try:
            with self.client.open_sftp() as sftp:
                sftp.put(local_path, remote_path)
        except (paramiko.SSHException, OSError) as exc:
            raise RuntimeError(f"SFTP 文件上传失败：{exc}") from exc

    def download(self, remote_path: str, local_path: str) -> None:
        if not self.client:
            raise RuntimeError("SSH 连接尚未建立")
        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        try:
            with self.client.open_sftp() as sftp:
                sftp.get(remote_path, local_path)
        except (paramiko.SSHException, OSError) as exc:
            raise RuntimeError(f"SFTP 文件下载失败：{exc}") from exc


def remote_shell_command(command: str) -> str:
    return f"sh -lc {shlex.quote(command)}"


def remote_parent(path: str) -> str:
    return posixpath.dirname(path.rstrip("/")) or "/"
