"""Offline, machine-bound PCIDS license verification."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from backend.utils.app_paths import get_app_data_root


LICENSE_SCHEMA_VERSION = 1
LICENSE_PRODUCT_CODE = "PCIDS"
LICENSE_FILE_NAME = "pcids.lic"
LICENSE_PUBLIC_KEY_FILE_NAME = "license_public_key.pem"
LICENSE_MAX_FILE_BYTES = 256 * 1024
_DISABLED_VALUES = {"0", "false", "no", "off"}


class LicenseError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LicenseError("License 时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def encode_signature(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_signature(value: Any) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise LicenseError("License 缺少签名")
    try:
        padded = (text + "=" * (-len(text) % 4)).encode("ascii")
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except Exception as exc:
        raise LicenseError("License 签名编码无效") from exc


def get_license_dir(data_root: Optional[Path] = None) -> Path:
    root = Path(data_root) if data_root is not None else get_app_data_root()
    path = root.expanduser().resolve(strict=False) / "license"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_license_file_path(data_root: Optional[Path] = None) -> Path:
    configured = str(os.environ.get("PCIDS_LICENSE_FILE") or "").strip()
    if configured and data_root is None:
        return Path(configured).expanduser().resolve(strict=False)
    return get_license_dir(data_root) / LICENSE_FILE_NAME


def get_license_public_key_path() -> Path:
    configured = str(os.environ.get("PCIDS_LICENSE_PUBLIC_KEY_FILE") or "").strip()
    if configured and not getattr(sys, "frozen", False):
        return Path(configured).expanduser().resolve(strict=False)
    return Path(__file__).resolve().parents[1] / "config" / LICENSE_PUBLIC_KEY_FILE_NAME


def is_license_enforcement_enabled() -> bool:
    if getattr(sys, "frozen", False):
        return True
    raw = str(os.environ.get("PCIDS_LICENSE_ENFORCEMENT", "1")).strip().lower()
    return raw not in _DISABLED_VALUES


def _read_windows_machine_guid() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        flags = winreg.KEY_READ
        if hasattr(winreg, "KEY_WOW64_64KEY"):
            flags |= winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            flags,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value or "").strip()
    except Exception:
        return ""


def _read_windows_hardware() -> Dict[str, str]:
    if os.name != "nt":
        return {}
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$system = Get-CimInstance Win32_ComputerSystemProduct
$bios = Get-CimInstance Win32_BIOS
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
@{
  smbios_uuid = [string]$system.UUID
  bios_serial = [string]$bios.SerialNumber
  volume_serial = [string]$drive.VolumeSerialNumber
} | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = json.loads(result.stdout.strip() or "{}")
        return {str(key): str(value or "").strip() for key, value in payload.items()}
    except Exception:
        return {}


def _read_non_windows_machine_id() -> str:
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    return ""


def get_or_create_installation_id(data_root: Optional[Path] = None) -> str:
    path = get_license_dir(data_root) / "installation_id"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = uuid.uuid4().hex
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)
    return value


def collect_machine_components(data_root: Optional[Path] = None) -> Dict[str, str]:
    components = {
        "installation_id": get_or_create_installation_id(data_root),
    }
    if os.name == "nt":
        machine_guid = _read_windows_machine_guid()
        if machine_guid:
            components["machine_guid"] = machine_guid
        else:
            components.update(_read_windows_hardware())
    else:
        components["node"] = platform.node()
        components["platform"] = platform.system()
        components["machine_id"] = _read_non_windows_machine_id()
    return {
        key: str(value or "").strip().lower()
        for key, value in components.items()
        if str(value or "").strip()
    }


def calculate_machine_fingerprint(components: Dict[str, str]) -> str:
    normalized = {
        str(key).strip().lower(): str(value or "").strip().lower()
        for key, value in components.items()
        if str(value or "").strip()
    }
    return hashlib.sha256(canonical_json(normalized)).hexdigest()


def format_machine_code(fingerprint: str) -> str:
    compact = str(fingerprint or "").strip().upper()
    visible = compact[:24]
    return "PCIDS-" + "-".join(visible[index:index + 4] for index in range(0, len(visible), 4))


@lru_cache(maxsize=16)
def _get_machine_identity_cached(data_root_text: str) -> Dict[str, Any]:
    components = collect_machine_components(Path(data_root_text))
    fingerprint = calculate_machine_fingerprint(components)
    return {
        "fingerprint": fingerprint,
        "machine_code": format_machine_code(fingerprint),
        "component_count": len(components),
    }


def get_machine_identity(data_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(data_root) if data_root is not None else get_app_data_root()
    return dict(_get_machine_identity_cached(str(root.expanduser().resolve(strict=False))))


def _license_envelope(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": document.get("schema_version"),
        "signature_algorithm": document.get("signature_algorithm"),
        "payload": document.get("payload"),
    }


@lru_cache(maxsize=8)
def _load_public_key(path_text: str, modified_ns: int, size: int) -> Ed25519PublicKey:
    del modified_ns, size
    path = Path(path_text)
    try:
        loaded = serialization.load_pem_public_key(path.read_bytes())
    except Exception as exc:
        raise LicenseError("License 公钥不可用，请重新安装正式授权版本") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise LicenseError("License 公钥类型不是 Ed25519")
    return loaded


def load_license_public_key(path: Optional[Path] = None) -> Ed25519PublicKey:
    resolved = (path or get_license_public_key_path()).resolve(strict=False)
    if not resolved.is_file():
        raise LicenseError("License 公钥未配置，请联系软件供应方")
    stat = resolved.stat()
    return _load_public_key(str(resolved), stat.st_mtime_ns, stat.st_size)


def load_license_document(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise LicenseError("未找到 License 文件")
    if path.stat().st_size > LICENSE_MAX_FILE_BYTES:
        raise LicenseError("License 文件过大")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LicenseError("License 文件不是有效的 JSON") from exc
    if not isinstance(document, dict):
        raise LicenseError("License 文件结构无效")
    return document


def verify_license_document(
    document: Dict[str, Any],
    machine_fingerprint: str,
    public_key: Ed25519PublicKey,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    if document.get("schema_version") != LICENSE_SCHEMA_VERSION:
        raise LicenseError("License 版本不受支持")
    if document.get("signature_algorithm") != "Ed25519":
        raise LicenseError("License 签名算法不受支持")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise LicenseError("License 缺少授权内容")
    try:
        public_key.verify(
            decode_signature(document.get("signature")),
            canonical_json(_license_envelope(document)),
        )
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise LicenseError("License 签名无效或文件已被篡改") from exc

    if str(payload.get("product") or "").strip().upper() != LICENSE_PRODUCT_CODE:
        raise LicenseError("License 不适用于当前软件")
    expected_fingerprint = str(payload.get("machine_fingerprint") or "").strip().lower()
    if not expected_fingerprint or expected_fingerprint != machine_fingerprint.strip().lower():
        raise LicenseError("License 与当前计算机不匹配")

    current = (now or utc_now()).astimezone(timezone.utc)
    not_before = parse_utc(payload.get("not_before"))
    expires_at = parse_utc(payload.get("expires_at"))
    if not_before and current < not_before:
        raise LicenseError("License 尚未生效")
    if expires_at and current > expires_at:
        raise LicenseError("License 已过期")

    license_id = str(payload.get("license_id") or "").strip()
    if not license_id:
        raise LicenseError("License 缺少授权编号")
    try:
        installation_no = int(payload.get("installation_no") or 0)
        installation_limit = int(payload.get("installation_limit") or 0)
    except (TypeError, ValueError) as exc:
        raise LicenseError("License 安装授权序号无效") from exc
    if installation_no < 1 or installation_limit < 1 or installation_no > installation_limit:
        raise LicenseError("License 安装授权序号无效")
    return payload


def _public_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "license_id",
            "customer_id",
            "customer_name",
            "product",
            "installation_no",
            "installation_limit",
            "issued_at",
            "not_before",
            "expires_at",
            "features",
        )
    }


def get_license_status(
    data_root: Optional[Path] = None,
    license_path: Optional[Path] = None,
    public_key_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    identity = get_machine_identity(data_root)
    resolved_license_path = (license_path or get_license_file_path(data_root)).resolve(strict=False)
    base = {
        "valid": False,
        "state": "missing",
        "message": "尚未安装本机 License",
        "machine_code": identity["machine_code"],
        "machine_fingerprint": identity["fingerprint"],
        "license_path": str(resolved_license_path),
        "license": None,
    }
    if not is_license_enforcement_enabled():
        return {
            **base,
            "valid": True,
            "state": "disabled",
            "message": "开发或测试环境已关闭 License 校验",
        }
    if not resolved_license_path.is_file():
        return base
    try:
        document = load_license_document(resolved_license_path)
        public_key = load_license_public_key(public_key_path)
        payload = verify_license_document(document, identity["fingerprint"], public_key, now=now)
        return {
            **base,
            "valid": True,
            "state": "valid",
            "message": "License 有效",
            "license": _public_payload(payload),
        }
    except LicenseError as exc:
        message = str(exc)
        state = "invalid"
        if "过期" in message:
            state = "expired"
        elif "当前计算机不匹配" in message:
            state = "machine_mismatch"
        elif "公钥" in message:
            state = "public_key_error"
        return {**base, "state": state, "message": message}


def validate_license_file(
    path: Path,
    data_root: Optional[Path] = None,
    public_key_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    identity = get_machine_identity(data_root)
    payload = verify_license_document(
        load_license_document(path),
        identity["fingerprint"],
        load_license_public_key(public_key_path),
        now=now,
    )
    return {
        "valid": True,
        "state": "valid",
        "message": "License 有效",
        "machine_code": identity["machine_code"],
        "machine_fingerprint": identity["fingerprint"],
        "license_path": str(path.resolve(strict=False)),
        "license": _public_payload(payload),
    }


def require_valid_license() -> Dict[str, Any]:
    status = get_license_status()
    if not status["valid"]:
        raise LicenseError(status["message"])
    return status


def install_license_bytes(
    content: bytes,
    data_root: Optional[Path] = None,
    public_key_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if not content:
        raise LicenseError("License 文件为空")
    if len(content) > LICENSE_MAX_FILE_BYTES:
        raise LicenseError("License 文件过大")
    target = get_license_file_path(data_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".pcids-license-",
            suffix=".tmp",
            dir=str(target.parent),
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        validate_license_file(
            temporary_path,
            data_root=data_root,
            public_key_path=public_key_path,
        )
        os.replace(temporary_path, target)
        temporary_path = None
        return validate_license_file(
            target,
            data_root=data_root,
            public_key_path=public_key_path,
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_machine_request(data_root: Optional[Path] = None) -> Dict[str, Any]:
    identity = get_machine_identity(data_root)
    return {
        "schema_version": LICENSE_SCHEMA_VERSION,
        "request_id": uuid.uuid4().hex,
        "product": LICENSE_PRODUCT_CODE,
        "machine_code": identity["machine_code"],
        "machine_fingerprint": identity["fingerprint"],
        "created_at": format_utc(utc_now()),
    }
