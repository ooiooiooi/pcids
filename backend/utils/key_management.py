from __future__ import annotations

import base64
import ctypes
import os
import secrets
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
from backend.utils.app_paths import get_app_data_root


ARTIFACT_MASTER_KEY_ENV = "PCIDS_ARTIFACT_MASTER_KEY"
_WINDOWS_MASTER_KEY_FILE = "artifact_master_key.dpapi"
_MASTER_KEY_LENGTH = 32


class MasterKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class MasterKeyRecord:
    key: bytes
    source: str
    location: Optional[str] = None


def _decode_env_key(raw_value: str) -> bytes:
    try:
        padded = raw_value + "=" * (-len(raw_value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise MasterKeyError(f"环境变量 {ARTIFACT_MASTER_KEY_ENV} 不是合法的 Base64URL 编码") from exc
    if len(decoded) != _MASTER_KEY_LENGTH:
        raise MasterKeyError(
            f"环境变量 {ARTIFACT_MASTER_KEY_ENV} 解码后长度不正确，期望 {_MASTER_KEY_LENGTH} 字节"
        )
    return decoded


def _load_key_from_env() -> Optional[MasterKeyRecord]:
    raw = str(os.environ.get(ARTIFACT_MASTER_KEY_ENV) or "").strip()
    if not raw:
        return None
    return MasterKeyRecord(
        key=_decode_env_key(raw),
        source="env",
        location=ARTIFACT_MASTER_KEY_ENV,
    )


def get_artifact_secure_data_dir() -> Path:
    return (
        Path(str(os.environ.get("PCIDS_SECURE_DATA_DIR") or "").strip()).expanduser()
        if str(os.environ.get("PCIDS_SECURE_DATA_DIR") or "").strip()
        else get_app_data_root() / "secure"
    )


def _get_windows_master_key_path() -> Path:
    base_dir = get_artifact_secure_data_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / _WINDOWS_MASTER_KEY_FILE


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _bytes_to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _dpapi_protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    input_blob, input_buffer = _bytes_to_blob(data)
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "PCIDS Artifact Master Key",
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise MasterKeyError(f"Windows DPAPI 加密主密钥失败，错误码: {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    input_blob, input_buffer = _bytes_to_blob(data)
    output_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise MasterKeyError(f"Windows DPAPI 解密主密钥失败，错误码: {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def _load_or_create_windows_key() -> MasterKeyRecord:
    key_path = _get_windows_master_key_path()
    if key_path.exists():
        try:
            protected_payload = key_path.read_bytes()
            key = _dpapi_unprotect(protected_payload)
        except Exception as exc:
            raise MasterKeyError(f"读取受保护主密钥失败: {key_path}") from exc
        if len(key) != _MASTER_KEY_LENGTH:
            raise MasterKeyError(f"受保护主密钥长度无效: {key_path}")
        return MasterKeyRecord(key=key, source="windows_dpapi", location=str(key_path))

    key = secrets.token_bytes(_MASTER_KEY_LENGTH)
    protected_payload = _dpapi_protect(key)
    try:
        key_path.write_bytes(protected_payload)
    except PermissionError as exc:
        raise MasterKeyError(f"无权限写入主密钥文件: {key_path}") from exc
    except Exception as exc:
        raise MasterKeyError(f"写入主密钥文件失败: {key_path}") from exc
    return MasterKeyRecord(key=key, source="windows_dpapi", location=str(key_path))


@lru_cache(maxsize=1)
def get_artifact_master_key_record() -> MasterKeyRecord:
    env_record = _load_key_from_env()
    if env_record:
        return env_record
    if os.name == "nt":
        return _load_or_create_windows_key()
    raise MasterKeyError(
        f"未配置 {ARTIFACT_MASTER_KEY_ENV}，且当前系统不是 Windows，无法安全初始化制品主密钥"
    )


def get_artifact_master_key() -> bytes:
    return get_artifact_master_key_record().key


def describe_artifact_master_key_source() -> dict[str, Optional[str]]:
    record = get_artifact_master_key_record()
    return {
        "source": record.source,
        "location": record.location,
    }


def reset_artifact_master_key_cache() -> None:
    get_artifact_master_key_record.cache_clear()
