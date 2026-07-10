from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import find_spec
from pathlib import Path
import os
import platform
import shutil
import tempfile
import uuid

from backend.utils.key_management import (
    MasterKeyError,
    describe_artifact_master_key_source,
    get_artifact_secure_data_dir,
)
from backend.utils.app_paths import get_repository_download_root_path
from backend.utils.runtime_dependencies import build_burner_tool_readiness


SUPPORTED_WINDOWS_RELEASES = {"10", "11"}


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    status: str
    message: str
    blocking: bool = False
    details: dict[str, object] | None = None


def _build_check(
    key: str,
    status: str,
    message: str,
    *,
    blocking: bool = False,
    **details: object,
) -> ReadinessCheck:
    return ReadinessCheck(
        key=key,
        status=status,
        message=message,
        blocking=blocking,
        details={name: value for name, value in details.items() if value not in {None, ""}} or None,
    )


def _check_python_dependency(module_name: str) -> ReadinessCheck:
    available = find_spec(module_name) is not None
    return _build_check(
        f"python_module_{module_name}",
        "ok" if available else "error",
        f"Python 模块 {module_name} {'可用' if available else '缺失'}",
        blocking=not available,
        module=module_name,
    )


def _check_windows_support() -> ReadinessCheck:
    system_name = platform.system()
    release = platform.release()
    supported = system_name == "Windows" and release in SUPPORTED_WINDOWS_RELEASES
    if supported:
        return _build_check(
            "windows_version",
            "ok",
            "当前系统版本在已验证的 Windows 支持矩阵内",
            system=system_name,
            release=release,
        )
    if system_name == "Windows":
        return _build_check(
            "windows_version",
            "warn",
            "当前系统为 Windows，但不在已验证版本矩阵内，需要补充兼容性验证",
            system=system_name,
            release=release,
        )
    return _build_check(
        "windows_version",
        "error",
        "当前系统不是 Windows，无法满足既定部署支持边界",
        blocking=True,
        system=system_name,
        release=release,
    )


def _check_directory_writable(key: str, path: Path, success_message: str, error_message: str) -> ReadinessCheck:
    target = path.expanduser().resolve()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / f".pcids_probe_{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        free_bytes = shutil.disk_usage(target).free
        return _build_check(
            key,
            "ok",
            success_message,
            path=str(target),
            free_mb=round(free_bytes / (1024 * 1024), 2),
        )
    except PermissionError:
        return _build_check(
            key,
            "error",
            error_message,
            blocking=True,
            path=str(target),
            reason="permission_denied",
        )
    except Exception as exc:
        return _build_check(
            key,
            "error",
            error_message,
            blocking=True,
            path=str(target),
            reason=str(exc),
        )


def _check_temp_runtime_dir() -> ReadinessCheck:
    return _check_directory_writable(
        "temp_runtime_dir",
        Path(tempfile.gettempdir()) / "pcids_runtime_probe",
        "临时运行目录可写，支持解密执行副本创建",
        "临时运行目录不可写，无法安全生成执行副本",
    )


def _check_upload_root() -> ReadinessCheck:
    return _check_directory_writable(
        "artifact_storage_dir",
        get_repository_download_root_path(),
        "制品加密存储目录可写",
        "制品加密存储目录不可写，无法完成仓库落盘",
    )


def _check_secure_data_dir() -> ReadinessCheck:
    return _check_directory_writable(
        "secure_data_dir",
        get_artifact_secure_data_dir(),
        "安全数据目录可写",
        "安全数据目录不可写，无法初始化或读取受保护密钥",
    )


def _check_master_key_ready() -> ReadinessCheck:
    try:
        details = describe_artifact_master_key_source()
        return _build_check(
            "artifact_master_key",
            "ok",
            "制品主密钥已就绪",
            **details,
        )
    except MasterKeyError as exc:
        return _build_check(
            "artifact_master_key",
            "error",
            f"制品主密钥不可用: {str(exc)}",
            blocking=True,
        )


def _build_burner_tool_checks() -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    for item in build_burner_tool_readiness():
        burner_name = str(item.get("burner") or "").strip() or "未知烧录器"
        status = "ok" if item.get("status") == "ok" else "warn"
        checks.append(
            _build_check(
                f"burner_tool_{burner_name.lower().replace('-', '_')}",
                status,
                str(item.get("message") or f"{burner_name} 工具状态待确认"),
                burner=burner_name,
                tool_label=item.get("tool_label"),
                configured_path=item.get("configured_path"),
                configured_mode=item.get("configured_mode"),
                configured_source=item.get("configured_source"),
                bundled_dir=item.get("bundled_dir"),
                bundled_dir_exists=item.get("bundled_dir_exists"),
                driver_ready=item.get("driver_ready"),
                driver_artifacts=",".join(item.get("driver_artifacts") or []),
                env_names=",".join(item.get("env_names") or []),
            )
        )
    return checks


def build_windows_deployment_readiness() -> dict[str, object]:
    checks = [
        _check_windows_support(),
        _check_python_dependency("cryptography"),
        _check_upload_root(),
        _check_temp_runtime_dir(),
        _check_secure_data_dir(),
        _check_master_key_ready(),
    ]
    checks.extend(_build_burner_tool_checks())
    blocking_checks = [check for check in checks if check.blocking]
    warning_checks = [check for check in checks if check.status == "warn"]
    return {
        "overall_ready": not blocking_checks,
        "blocking_issue_count": len(blocking_checks),
        "warning_count": len(warning_checks),
        "support_matrix": {
            "validated_windows_releases": sorted(SUPPORTED_WINDOWS_RELEASES),
            "current_system": platform.system(),
            "current_release": platform.release(),
            "current_version": platform.version(),
        },
        "checks": [asdict(check) for check in checks],
    }
