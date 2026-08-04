"""Scripted OS application installer for CodeArts Build Windows agents.

The adapter intentionally keeps credentials out of command-line arguments.
Kylin and UOS scripts execute on the target through SSH/SFTP. Harmony and
SylixOS scripts execute on the Windows Agent and use HDC/FTP respectively.
"""
from __future__ import annotations

import argparse
import json
import locale
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.utils.app_paths import get_app_data_root
from backend.utils.runtime_dependencies import configure_bundled_tools
from backend.utils.ssh_client import SSHClientSession, remote_shell_command


EXIT_INVALID_REQUEST = 2
EXIT_EXECUTION_FAILED = 10
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_CONFIG_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SECRET_CONFIG_KEYS = {"ftp_login_password", "serial_login_password", "system_password"}

INSTALLER_CATALOG: tuple[dict[str, str], ...] = (
    {
        "os": "kylin",
        "name": "kylin_ssh_package_install",
        "transport": "SSH/SFTP",
        "scope": "remote",
        "script": "linux-package-install.sh",
    },
    {
        "os": "uos",
        "name": "uos_ssh_package_install",
        "transport": "SSH/SFTP",
        "scope": "remote",
        "script": "linux-package-install.sh",
    },
    {
        "os": "harmony",
        "name": "harmony_hdc_package_install",
        "transport": "HDC",
        "scope": "agent",
        "script": "harmony-package-install.ps1",
    },
    {
        "os": "yinghui",
        "name": "sylixos_ftp_package_install",
        "transport": "FTP",
        "scope": "agent",
        "script": "sylixos-ftp-install.py",
    },
)

_OS_ALIASES = {
    "kylin": "kylin",
    "麒麟": "kylin",
    "uos": "uos",
    "统信": "uos",
    "harmony": "harmony",
    "harmonyos": "harmony",
    "鸿蒙": "harmony",
    "yinghui": "yinghui",
    "sylixos": "yinghui",
    "翼辉": "yinghui",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _console_json(payload: dict[str, Any]) -> None:
    readable = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        readable.encode(encoding, errors="strict")
        encoded = readable
    except (LookupError, UnicodeEncodeError):
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    print(encoded, flush=True)


def _decode_output(raw: bytes) -> str:
    preferred = locale.getpreferredencoding(False)
    for encoding in ("utf-8-sig", "gb18030", preferred):
        try:
            return raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="backslashreplace")


class EventLogger:
    def __init__(self, log_dir: Path, run_id: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.text_path = log_dir / f"pcids-install-{run_id}.log"
        self.jsonl_path = log_dir / f"pcids-install-{run_id}.jsonl"

    def emit(self, event: str, *, status: str = "info", message: str = "", **details: Any) -> None:
        payload = {
            "time": _utc_now(),
            "event": event,
            "status": status,
            "message": message,
            **{key: value for key, value in details.items() if value not in (None, "")},
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        _console_json(payload)
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        with self.text_path.open("a", encoding="utf-8") as handle:
            detail = " ".join(
                f"{key}={value}"
                for key, value in payload.items()
                if key not in {"time", "event", "status", "message"}
            )
            handle.write(f"[{payload['time']}] [{status}] {event}: {message}{(' | ' + detail) if detail else ''}\n")


def _normalize_os(value: str) -> str:
    normalized = _OS_ALIASES.get(str(value or "").strip().lower())
    if not normalized:
        choices = ", ".join(item["os"] for item in INSTALLER_CATALOG)
        raise ValueError(f"unsupported os '{value}'. Choices: {choices}")
    return normalized


def _catalog_item(os_type: str) -> dict[str, str]:
    return next(item for item in INSTALLER_CATALOG if item["os"] == os_type)


def _default_script(item: dict[str, str]) -> Path:
    candidates = (
        Path(__file__).resolve().parent / "codearts-install" / item["script"],
        Path(__file__).resolve().parent / "examples" / item["script"],
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def _parse_config(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"--config-json must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("--config-json must be a JSON object")
    for key, item in value.items():
        if not _SAFE_CONFIG_KEY.fullmatch(str(key)):
            raise ValueError(f"invalid config key: {key}")
        if str(key).lower() in _SECRET_CONFIG_KEYS:
            raise ValueError(f"secret field {key} is not allowed in --config-json; use a CodeArts private environment variable")
        if isinstance(item, (dict, list)):
            raise ValueError(f"config value must be scalar: {key}")
    return value


def _config_env(config: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in config.items():
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif value is None:
            text = ""
        else:
            text = str(value)
        result[f"PCIDS_CONFIG_{str(key).upper()}"] = text
    return result


def _password_from_env(name: str, *, required: bool) -> str:
    env_name = str(name or "PCIDS_TARGET_PASSWORD").strip()
    if not _SAFE_ENV_NAME.fullmatch(env_name):
        raise ValueError("--password-env must be a valid environment variable name")
    value = os.environ.get(env_name, "")
    if required and not value:
        raise ValueError(f"target password is missing; configure CodeArts private variable {env_name}")
    return value


def _config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _local_script_command(script: Path) -> list[str]:
    suffix = script.suffix.lower()
    if suffix == ".ps1":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if suffix in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", "call", str(script)]
    if suffix == ".py":
        if getattr(sys, "frozen", False) or Path(sys.executable).name.lower() == "pcids_backend.exe":
            return [sys.executable, "--run-script", str(script)]
        return [sys.executable, str(script)]
    raise ValueError("Agent-local install script must be .ps1, .cmd, .bat, or .py")


def _run_local_script(script: Path, env: dict[str, str], timeout: int, logger: EventLogger) -> int:
    command = _local_script_command(script)
    logger.emit("script-start", script=str(script), scope="agent")
    process = subprocess.Popen(
        command,
        cwd=str(script.parent),
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output, _unused = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _unused = process.communicate()
        for line in _decode_output(output or b"").splitlines():
            if line:
                logger.emit("tool-output", message=line)
        logger.emit("tool-output", status="error", message=f"install script timed out after {timeout} seconds")
        return 124
    for line in _decode_output(output or b"").splitlines():
        if line:
            logger.emit("tool-output", message=line)
    return process.returncode


def _remote_exports(env: dict[str, str]) -> str:
    return "\n".join(f"export {key}={shlex.quote(value)}" for key, value in sorted(env.items()))


def _run_remote_script(
    args: argparse.Namespace,
    artifact: Path,
    script: Path,
    env: dict[str, str],
    password: str,
    logger: EventLogger,
) -> int:
    install_dir = str(args.install_dir or "/opt/pcids-app").strip() or "/opt/pcids-app"
    remote_artifact = f"{install_dir.rstrip('/')}/{artifact.name}"
    remote_script = f"/tmp/pcids-install-{args.run_id}.sh"
    remote_env = {
        **env,
        "PCIDS_ARTIFACT_PATH": remote_artifact,
        "FIRMWARE_PATH": remote_artifact,
        "INSTALL_DIR": install_dir,
    }
    logger.emit("ssh-connect", host=args.target_host, port=args.target_port, username=args.username)
    with SSHClientSession(
        args.target_host,
        args.target_port,
        args.username,
        password,
        args.auth_type,
        private_key_path=args.private_key,
    ) as session:
        prepare = session.run(remote_shell_command(f"mkdir -p {shlex.quote(install_dir)} /tmp"), timeout=30)
        if not prepare.success:
            logger.emit("tool-output", status="error", message=prepare.reason)
            return EXIT_EXECUTION_FAILED
        session.upload(str(artifact), remote_artifact)
        session.upload(str(script), remote_script)
        logger.emit("artifact-uploaded", artifact=remote_artifact, script=remote_script)
        command = "\n".join(
            [
                _remote_exports(remote_env),
                f"chmod 700 {shlex.quote(remote_script)}",
                f"sh {shlex.quote(remote_script)}",
                "status=$?",
                f"rm -f {shlex.quote(remote_script)}",
                "exit $status",
            ]
        )
        result = session.run(remote_shell_command(command), timeout=args.timeout_seconds)
        for text in (result.stdout, result.stderr):
            for line in str(text or "").splitlines():
                if line:
                    logger.emit("tool-output", message=line)
        return 0 if result.success else EXIT_EXECUTION_FAILED


def _resolve_request(args: argparse.Namespace) -> tuple[dict[str, str], Path, Path, dict[str, str], str]:
    os_type = _normalize_os(args.os)
    item = _catalog_item(os_type)
    artifact = Path(args.artifact).expanduser().resolve(strict=False)
    if not artifact.is_file():
        raise ValueError(f"artifact not found: {artifact}")
    script = Path(args.install_script).expanduser().resolve(strict=False) if args.install_script else _default_script(item)
    if not script.is_file():
        raise ValueError(f"install script not found: {script}")
    config = _parse_config(args.config_json)
    if args.boot_autostart:
        config["boot_autostart"] = True
    configured_os = str(config.get("os_type") or "").strip()
    if configured_os and _normalize_os(configured_os) != os_type:
        raise ValueError(f"--os {os_type} does not match config os_type {configured_os}")

    args.target_host = str(args.target_host or config.get("target_ip") or "").strip()
    args.username = str(args.username or config.get("login_username") or "").strip()
    args.device_id = str(args.device_id or config.get("harmony_device_id") or "").strip()
    args.auth_type = str(args.auth_type or config.get("auth_type") or "key").strip().lower()
    if args.auth_type not in {"key", "password"}:
        raise ValueError("auth_type must be key or password")
    args.private_key = str(args.private_key or config.get("private_key_path") or "").strip()
    if not args.target_port:
        args.target_port = int(config.get("ftp_port") or config.get("target_port") or (21 if os_type == "yinghui" else 22))
    if not 1 <= args.target_port <= 65535:
        raise ValueError("target port must be between 1 and 65535")
    if not args.install_dir:
        args.install_dir = str(config.get("install_dir") or "").strip()
    if not args.install_dir:
        args.install_dir = "/data/local/tmp" if os_type == "harmony" else "/apps" if os_type == "yinghui" else "/opt/pcids-app"
    if not args.timeout_seconds:
        args.timeout_seconds = int(config.get("timeout_seconds") or 600)
    if not 1 <= args.timeout_seconds <= 7200:
        raise ValueError("timeout_seconds must be between 1 and 7200")

    connection_protocol = str(config.get("connection_protocol") or "").strip().upper()
    deployment_mode = str(config.get("deployment_mode") or "").strip().upper()
    if os_type in {"kylin", "uos"} and connection_protocol not in {"", "SSH"}:
        raise ValueError(f"{os_type} installation only supports connection_protocol=SSH")
    if os_type == "harmony" and connection_protocol not in {"", "HDC"}:
        raise ValueError("Harmony installation only supports connection_protocol=HDC")
    if os_type == "yinghui" and deployment_mode not in {"", "FTP", "FTP+TELNET"}:
        raise ValueError("SylixOS installation only supports deployment_mode=FTP")
    env = {
        **_config_env(config),
        "PCIDS_OS_TYPE": os_type,
        "PCIDS_INSTALLER": item["name"],
        "PCIDS_RUN_ID": args.run_id,
        "PCIDS_ARTIFACT_PATH": str(artifact),
        "FIRMWARE_PATH": str(artifact),
        "INSTALL_DIR": args.install_dir,
        "PCIDS_TARGET_HOST": args.target_host,
        "PCIDS_TARGET_PORT": str(args.target_port),
        "PCIDS_TARGET_USERNAME": args.username,
        "PCIDS_DEVICE_ID": args.device_id,
    }
    env.update({key: str(value) for key, value in configure_bundled_tools().items()})

    if item["scope"] == "remote":
        if not args.target_host or not args.username:
            raise ValueError("SSH installation requires --target-host and --username")
        if args.auth_type == "key" and args.private_key and not Path(args.private_key).expanduser().is_file():
            raise ValueError(f"private key not found: {args.private_key}")
        configured_password = str(args.password or config.get("login_password") or "")
        password = configured_password or _password_from_env(
            args.password_env,
            required=args.auth_type == "password" and not args.dry_run,
        )
    elif os_type == "harmony":
        if not args.device_id:
            raise ValueError("Harmony installation requires --device-id")
        password = ""
    else:
        if not args.target_host or not args.username:
            raise ValueError("SylixOS FTP installation requires --target-host and --username")
        configured_password = str(args.password or config.get("login_password") or "")
        password = configured_password or _password_from_env(
            args.password_env,
            required=not args.dry_run and not _config_bool(config.get("login_passwordless")),
        )
    env["PCIDS_TARGET_PASSWORD"] = password
    return item, artifact, script, env, password


def _run(args: argparse.Namespace) -> int:
    run_id = str(args.run_id or uuid.uuid4().hex).strip()
    if not _SAFE_RUN_ID.fullmatch(run_id):
        _console_json({"event": "failed", "status": "error", "code": "INVALID_RUN_ID", "message": "run-id allows only letters, numbers, hyphen and underscore"})
        return EXIT_INVALID_REQUEST
    args.run_id = run_id
    logger = EventLogger(Path(args.log_dir or (get_app_data_root() / "logs" / "codearts")).expanduser(), run_id)
    try:
        item, artifact, script, env, password = _resolve_request(args)
    except Exception as exc:
        logger.emit("failed", status="error", code="INVALID_REQUEST", message=str(exc))
        return EXIT_INVALID_REQUEST
    logger.emit(
        "started",
        installer=item["name"],
        os=item["os"],
        transport=item["transport"],
        artifact=str(artifact),
        script=str(script),
        run_id=run_id,
        text_log=str(logger.text_path),
        json_log=str(logger.jsonl_path),
    )
    if args.dry_run:
        logger.emit("completed", status="success", message="dry run completed", installer=item["name"])
        return 0
    try:
        if item["scope"] == "remote":
            return_code = _run_remote_script(args, artifact, script, env, password, logger)
        else:
            return_code = _run_local_script(script, env, args.timeout_seconds, logger)
    except Exception as exc:
        logger.emit("failed", status="error", code="INSTALLER_EXCEPTION", message=str(exc))
        return EXIT_EXECUTION_FAILED
    if return_code == 0:
        logger.emit("completed", status="success", message="installation completed", exit_code=0)
        return 0
    logger.emit("failed", status="error", code="INSTALL_FAILED", message="install script returned non-zero", exit_code=return_code)
    return return_code if 0 < return_code < 256 else EXIT_EXECUTION_FAILED


def _list_installers() -> int:
    for item in INSTALLER_CATALOG:
        _console_json(item)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PCIDS scripted OS application installer for CodeArts Build Windows agents")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-installers", help="list supported OS installer selectors")
    run_parser = subparsers.add_parser("run", help="run an OS application install script")
    run_parser.add_argument("--os", required=True, help="kylin, uos, harmony, or yinghui")
    run_parser.add_argument("--artifact", required=True, help="package downloaded by the CodeArts Artifact step")
    run_parser.add_argument("--install-script", default="", help="optional custom script; otherwise use the bundled example")
    run_parser.add_argument("--target-host", default="", help="SSH/FTP target address")
    run_parser.add_argument("--target-port", type=int, default=0, help="SSH/FTP port; defaults to 22 or 21")
    run_parser.add_argument("--username", default="", help="SSH/FTP login user")
    run_parser.add_argument("--auth-type", choices=("key", "password"), default="", help="SSH authentication type")
    run_parser.add_argument("--private-key", default="", help="Agent-local SSH private key path")
    run_parser.add_argument("--password", default="", help="SSH/FTP plaintext password")
    run_parser.add_argument("--password-env", default="PCIDS_TARGET_PASSWORD", help="CodeArts private variable containing the target password")
    run_parser.add_argument("--device-id", default="", help="Harmony HDC device identifier")
    run_parser.add_argument("--install-dir", default="", help="target installation directory")
    run_parser.add_argument("--boot-autostart", action="store_true", help="add the SylixOS artifact to /etc/startup.sh")
    run_parser.add_argument("--config-json", default="{}", help="scalar script options exposed as PCIDS_CONFIG_* variables")
    run_parser.add_argument("--timeout-seconds", type=int, default=0, help="script timeout, 1-7200 seconds")
    run_parser.add_argument("--run-id", default="", help="CodeArts build/run identifier")
    run_parser.add_argument("--log-dir", default="", help="directory for .log and .jsonl output")
    run_parser.add_argument("--dry-run", action="store_true", help="validate without connecting to a target")
    args = parser.parse_args(argv)
    if args.command == "list-installers":
        return _list_installers()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
