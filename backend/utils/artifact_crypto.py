from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.utils.key_management import MasterKeyError, describe_artifact_master_key_source, get_artifact_master_key


FILE_MAGIC = b"PCIDSENC1"
FILE_VERSION = 1
FORMAT_NAME = "pcids-aes256gcm-chunked-v1"
DEFAULT_CHUNK_SIZE = 1024 * 1024
_WRAP_AAD = b"pcids-artifact-master-key-wrap-v1"


class ArtifactSecurityError(RuntimeError):
    pass


class ArtifactEncryptionError(ArtifactSecurityError):
    pass


class ArtifactDecryptionError(ArtifactSecurityError):
    pass


class ArtifactKeyValidationError(ArtifactSecurityError):
    pass


class ArtifactPermissionDeniedError(ArtifactSecurityError):
    pass


class ArtifactFormatError(ArtifactSecurityError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    path: str
    plaintext_size: int
    encrypted_size: int
    md5: str
    sha256: str
    original_name: str
    storage_format: str
    key_source: str

    def to_storage_metadata(self) -> dict[str, object]:
        return {
            "enabled": True,
            "format": self.storage_format,
            "original_name": self.original_name,
            "plaintext_size": self.plaintext_size,
            "encrypted_size": self.encrypted_size,
            "key_source": self.key_source,
        }


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sanitize_filename(filename: Optional[str], fallback: str = "artifact.bin") -> str:
    raw = str(filename or "").strip() or fallback
    safe = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in raw).strip(" .")
    return safe or fallback


def build_encrypted_artifact_path(root_dir: str, preferred_name: Optional[str] = None) -> str:
    root = Path(root_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    original_name = _sanitize_filename(preferred_name)
    suffix = "".join(Path(original_name).suffixes)
    filename = f"{uuid.uuid4().hex}{suffix}.pcenc" if suffix else f"{uuid.uuid4().hex}.pcenc"
    return str((root / filename).resolve())


def _safe_remove(file_path: str) -> None:
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass


def is_encrypted_artifact(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as handle:
            return handle.read(len(FILE_MAGIC)) == FILE_MAGIC
    except Exception:
        return False


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise ArtifactFormatError("加密文件格式损坏，读取长度不足")
    return data


def _load_encrypted_header(handle: BinaryIO) -> dict[str, object]:
    magic = handle.read(len(FILE_MAGIC))
    if magic != FILE_MAGIC:
        raise ArtifactFormatError("不是受支持的加密制品格式")
    header_length = struct.unpack(">I", _read_exact(handle, 4))[0]
    if header_length <= 0 or header_length > 1024 * 1024:
        raise ArtifactFormatError("加密文件头长度无效")
    try:
        header = json.loads(_read_exact(handle, header_length).decode("utf-8"))
    except Exception as exc:
        raise ArtifactFormatError("加密文件头解析失败") from exc
    if not isinstance(header, dict):
        raise ArtifactFormatError("加密文件头格式无效")
    if int(header.get("version") or 0) != FILE_VERSION:
        raise ArtifactFormatError("不支持的加密文件版本")
    if str(header.get("algorithm") or "") != "AES-256-GCM":
        raise ArtifactFormatError("不支持的加密算法")
    return header


def _unwrap_data_key(header: dict[str, object]) -> bytes:
    try:
        master_key = get_artifact_master_key()
    except MasterKeyError as exc:
        raise ArtifactKeyValidationError(f"主密钥不可用: {str(exc)}") from exc
    try:
        wrap_nonce = _b64decode(str(header.get("wrap_nonce") or ""))
        wrapped_key = _b64decode(str(header.get("wrapped_data_key") or ""))
    except Exception as exc:
        raise ArtifactFormatError("加密文件头中的密钥包装信息无效") from exc
    try:
        return AESGCM(master_key).decrypt(wrap_nonce, wrapped_key, _WRAP_AAD)
    except InvalidTag as exc:
        raise ArtifactKeyValidationError("密钥校验失败，无法解密制品文件") from exc
    except Exception as exc:
        raise ArtifactKeyValidationError(f"解包制品数据密钥失败: {str(exc)}") from exc


def store_encrypted_artifact(
    source: BinaryIO,
    destination_path: str,
    *,
    original_name: Optional[str] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> StoredArtifact:
    destination = Path(destination_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_name(f"{destination.name}.tmp.{uuid.uuid4().hex}")
    try:
        key_info = describe_artifact_master_key_source()
        master_key = get_artifact_master_key()
        data_key = secrets.token_bytes(32)
        wrap_nonce = secrets.token_bytes(12)
        nonce_prefix = secrets.token_bytes(8)
        wrapped_data_key = AESGCM(master_key).encrypt(wrap_nonce, data_key, _WRAP_AAD)
        header = {
            "version": FILE_VERSION,
            "format": FORMAT_NAME,
            "algorithm": "AES-256-GCM",
            "chunk_size": int(chunk_size),
            "nonce_prefix": _b64encode(nonce_prefix),
            "wrap_nonce": _b64encode(wrap_nonce),
            "wrapped_data_key": _b64encode(wrapped_data_key),
            "original_name": _sanitize_filename(original_name),
        }
        header_bytes = json.dumps(header, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        cipher = AESGCM(data_key)
        md5 = hashlib.md5()
        sha256 = hashlib.sha256()
        plaintext_size = 0
        chunk_index = 0

        with open(temp_destination, "wb") as encrypted_file:
            encrypted_file.write(FILE_MAGIC)
            encrypted_file.write(struct.pack(">I", len(header_bytes)))
            encrypted_file.write(header_bytes)
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ArtifactEncryptionError("读取到的文件块不是字节数据")
                plaintext = bytes(chunk)
                md5.update(plaintext)
                sha256.update(plaintext)
                plaintext_size += len(plaintext)
                nonce = nonce_prefix + struct.pack(">I", chunk_index)
                aad = f"chunk:{chunk_index}".encode("ascii")
                encrypted_chunk = cipher.encrypt(nonce, plaintext, aad)
                encrypted_file.write(struct.pack(">I", len(encrypted_chunk)))
                encrypted_file.write(encrypted_chunk)
                chunk_index += 1

        os.replace(temp_destination, destination)
        encrypted_size = os.path.getsize(destination)
        return StoredArtifact(
            path=str(destination),
            plaintext_size=plaintext_size,
            encrypted_size=encrypted_size,
            md5=md5.hexdigest(),
            sha256=sha256.hexdigest(),
            original_name=_sanitize_filename(original_name),
            storage_format=FORMAT_NAME,
            key_source=str(key_info.get("source") or ""),
        )
    except PermissionError as exc:
        _safe_remove(str(temp_destination))
        raise ArtifactPermissionDeniedError(f"加密落盘失败，权限不足: {destination}") from exc
    except MasterKeyError as exc:
        _safe_remove(str(temp_destination))
        raise ArtifactKeyValidationError(f"主密钥初始化失败: {str(exc)}") from exc
    except ArtifactSecurityError:
        _safe_remove(str(temp_destination))
        raise
    except Exception as exc:
        _safe_remove(str(temp_destination))
        raise ArtifactEncryptionError(f"加密落盘失败: {str(exc)}") from exc


def iter_decrypted_artifact(file_path: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
    target = Path(file_path).expanduser().resolve()
    if not target.exists() or not target.is_file():
        raise ArtifactDecryptionError(f"制品文件不存在: {target}")
    try:
        with open(target, "rb") as encrypted_file:
            if encrypted_file.read(len(FILE_MAGIC)) != FILE_MAGIC:
                encrypted_file.seek(0)
                while True:
                    chunk = encrypted_file.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
                return
            encrypted_file.seek(0)
            header = _load_encrypted_header(encrypted_file)
            data_key = _unwrap_data_key(header)
            nonce_prefix = _b64decode(str(header.get("nonce_prefix") or ""))
            if len(nonce_prefix) != 8:
                raise ArtifactFormatError("加密文件头中的随机前缀无效")
            cipher = AESGCM(data_key)
            chunk_index = 0
            while True:
                length_bytes = encrypted_file.read(4)
                if not length_bytes:
                    break
                if len(length_bytes) != 4:
                    raise ArtifactFormatError("加密文件块长度损坏")
                encrypted_length = struct.unpack(">I", length_bytes)[0]
                if encrypted_length <= 0:
                    raise ArtifactFormatError("加密文件块长度无效")
                encrypted_chunk = _read_exact(encrypted_file, encrypted_length)
                nonce = nonce_prefix + struct.pack(">I", chunk_index)
                aad = f"chunk:{chunk_index}".encode("ascii")
                try:
                    yield cipher.decrypt(nonce, encrypted_chunk, aad)
                except InvalidTag as exc:
                    raise ArtifactKeyValidationError("密钥校验失败或加密文件已损坏") from exc
                chunk_index += 1
    except ArtifactSecurityError:
        raise
    except PermissionError as exc:
        raise ArtifactPermissionDeniedError(f"无权限读取制品文件: {target}") from exc
    except Exception as exc:
        raise ArtifactDecryptionError(f"解密制品文件失败: {str(exc)}") from exc


def decrypt_artifact_to_path(file_path: str, destination_path: str) -> str:
    destination = Path(destination_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_name(f"{destination.name}.tmp.{uuid.uuid4().hex}")
    try:
        with open(temp_destination, "wb") as plaintext_file:
            for chunk in iter_decrypted_artifact(file_path):
                plaintext_file.write(chunk)
        os.replace(temp_destination, destination)
        return str(destination)
    except PermissionError as exc:
        _safe_remove(str(temp_destination))
        raise ArtifactPermissionDeniedError(f"写入临时解密文件失败，权限不足: {destination}") from exc
    except ArtifactSecurityError:
        _safe_remove(str(temp_destination))
        raise
    except Exception as exc:
        _safe_remove(str(temp_destination))
        raise ArtifactDecryptionError(f"生成临时解密文件失败: {str(exc)}") from exc


@contextmanager
def materialize_artifact_for_execution(
    file_path: str,
    *,
    work_dir: Optional[str] = None,
    preferred_name: Optional[str] = None,
) -> Iterator[str]:
    target_dir = Path(work_dir).expanduser().resolve() if work_dir else Path(tempfile.mkdtemp(prefix="pcids_artifact_exec_"))
    target_dir.mkdir(parents=True, exist_ok=True)
    output_name = _sanitize_filename(preferred_name)
    temp_path = target_dir / output_name
    try:
        yield decrypt_artifact_to_path(file_path, str(temp_path))
    finally:
        _safe_remove(str(temp_path))
        if work_dir is None:
            try:
                if target_dir.exists() and not any(target_dir.iterdir()):
                    target_dir.rmdir()
            except Exception:
                pass
