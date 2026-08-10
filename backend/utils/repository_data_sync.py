from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from backend.utils.agent_security import build_agent_headers, require_agent_token
from backend.utils.app_paths import get_app_data_root


_NODE_ID_ENV = "PCIDS_REPOSITORY_SYNC_NODE_ID"
_LEGACY_NODE_ID_ENV = "PCIDS_SYNC_NODE_ID"
_NODE_ID_FILENAME = "repository-sync-node-id"
# Protocol/database columns reserve 64 characters for node identities.
_NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,62}$")

_SERVER_EPOCH_PATH_ENV = "PCIDS_REPOSITORY_SYNC_EPOCH_PATH"
_SERVER_EPOCH_FILENAME = "repository-sync-server-epoch.json"
_SERVER_EPOCH_FORMAT_VERSION = 1
_SERVER_EPOCH_LOCK = threading.Lock()

_DEFAULT_SERVER_PORT = 8000
_DEFAULT_INTERVAL_SECONDS = 30.0
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 3.0
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
_DEFAULT_BATCH_SIZE = 500

_VALID_ROLES = {"auto", "client", "server", "standalone"}
_VALID_SCHEMES = {"http", "https"}


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _normalize_server_host(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    # Existing deployments normally store an IP or hostname.  Accept an old
    # URL-shaped value for compatibility, but deliberately ignore its embedded
    # scheme, port, path, query and credentials: those must come from the
    # dedicated data-sync scheme and server_port fields.
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    host = str(parsed.hostname or "").strip().rstrip(".")
    if not host:
        return ""
    return host


def _host_aliases(host: str) -> set[str]:
    normalized = str(host or "").strip().lower().rstrip(".")
    if not normalized:
        return set()
    aliases = {normalized}
    try:
        aliases.add(ipaddress.ip_address(normalized).compressed.lower())
    except ValueError:
        pass
    try:
        for item in socket.getaddrinfo(normalized, None):
            address = str(item[4][0] or "").strip().lower()
            if not address:
                continue
            aliases.add(address)
            try:
                aliases.add(ipaddress.ip_address(address).compressed.lower())
            except ValueError:
                pass
    except (OSError, UnicodeError):
        pass
    return aliases


def _local_host_aliases() -> set[str]:
    names = {
        "localhost",
        "127.0.0.1",
        "::1",
        str(socket.gethostname() or "").strip().lower(),
        str(socket.getfqdn() or "").strip().lower(),
    }
    aliases: set[str] = set()
    for name in names:
        aliases.update(_host_aliases(name))
    return aliases


def _is_self_target(host: str) -> bool:
    if not host:
        return False
    if _host_aliases(host) & _local_host_aliases():
        return True
    # Hostname resolution on multi-NIC Windows machines does not always list
    # hotspot/VPN interface addresses.  Binding succeeds only when the target
    # address belongs to this machine, which reliably detects those aliases.
    try:
        address = ipaddress.ip_address(host)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
        return True
    except (ValueError, OSError):
        return False


def _format_server_base_url(scheme: str, host: str, port: int) -> str:
    if not host:
        return ""
    formatted_host = host
    try:
        if ipaddress.ip_address(host).version == 6:
            formatted_host = f"[{host}]"
    except ValueError:
        pass
    return f"{scheme}://{formatted_host}:{port}"


def normalize_repository_data_sync_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the safe, effective peer-sync configuration for this node.

    The peer address is intentionally derived only from ``server_ip`` and
    ``server_port``.  Artifact-transfer fields such as ``server_transport`` and
    ``server_ssh_port`` must not redirect repository database synchronization.
    """

    source = dict(raw or {})
    enabled = _as_bool(source.get("repository_data_sync_enabled"), default=True)

    configured_role = str(source.get("repository_data_sync_role") or "auto").strip().lower()
    if configured_role not in _VALID_ROLES:
        configured_role = "auto"

    scheme = str(source.get("repository_data_sync_scheme") or "http").strip().lower()
    if scheme not in _VALID_SCHEMES:
        scheme = "http"

    server_host = _normalize_server_host(source.get("server_ip"))
    server_port = _bounded_int(
        source.get("server_port"),
        default=_DEFAULT_SERVER_PORT,
        minimum=1,
        maximum=65535,
    )
    try:
        local_backend_port = int(str(os.environ.get("PCIDS_BACKEND_PORT") or _DEFAULT_SERVER_PORT).strip())
    except (TypeError, ValueError):
        local_backend_port = _DEFAULT_SERVER_PORT
    # Multiple PCIDS instances can legitimately run on one Windows host (and
    # the integration simulator does exactly that).  A local address is only a
    # self target when it also points at this process's listening port.
    is_self_target = _is_self_target(server_host) and server_port == local_backend_port

    if not enabled or configured_role == "standalone":
        role = "standalone"
    elif configured_role == "server":
        role = "server"
    elif is_self_target:
        # A node configured to call itself is the authoritative server, never
        # a client.  This protects same-code deployments from sync loops.
        role = "server"
    elif configured_role == "client":
        role = "client" if server_host else "standalone"
    else:  # auto
        role = "client" if server_host else "standalone"

    interval_seconds = _bounded_float(
        source.get("repository_data_sync_interval_seconds"),
        default=_DEFAULT_INTERVAL_SECONDS,
        minimum=5.0,
        maximum=3600.0,
    )
    connect_timeout_seconds = _bounded_float(
        source.get("repository_data_sync_connect_timeout_seconds"),
        default=_DEFAULT_CONNECT_TIMEOUT_SECONDS,
        minimum=0.5,
        maximum=60.0,
    )
    request_timeout_seconds = _bounded_float(
        source.get("repository_data_sync_request_timeout_seconds"),
        default=_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        minimum=1.0,
        maximum=1800.0,
    )
    batch_size = _bounded_int(
        source.get("repository_data_sync_batch_size"),
        default=_DEFAULT_BATCH_SIZE,
        minimum=1,
        maximum=5000,
    )

    return {
        "enabled": enabled,
        "configured_role": configured_role,
        "role": role,
        "scheme": scheme,
        "server_host": server_host,
        "server_port": server_port,
        "server_base_url": _format_server_base_url(scheme, server_host, server_port),
        "interval_seconds": interval_seconds,
        "connect_timeout_seconds": connect_timeout_seconds,
        "request_timeout_seconds": request_timeout_seconds,
        "batch_size": batch_size,
        "is_self_target": is_self_target,
    }


def _validate_node_id(value: Any, *, source: str) -> str:
    normalized = str(value or "").strip()
    if not _NODE_ID_PATTERN.fullmatch(normalized):
        raise RuntimeError(f"Invalid repository sync node id from {source}")
    return normalized


def get_repository_sync_node_id() -> str:
    """Return an environment-provided or data-root-persisted node identity."""

    env_value = str(
        os.environ.get(_NODE_ID_ENV)
        or os.environ.get(_LEGACY_NODE_ID_ENV)
        or ""
    ).strip()
    if env_value:
        return _validate_node_id(env_value, source=_NODE_ID_ENV)

    node_id_path = get_app_data_root() / _NODE_ID_FILENAME
    try:
        saved = node_id_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        saved = ""
    except OSError as exc:
        raise RuntimeError(f"Unable to read repository sync node id: {node_id_path}") from exc
    if saved:
        return _validate_node_id(saved, source=str(node_id_path))

    generated = str(uuid.uuid4())
    node_id_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            str(node_id_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        try:
            concurrent_value = node_id_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"Unable to read repository sync node id: {node_id_path}") from exc
        return _validate_node_id(concurrent_value, source=str(node_id_path))
    except OSError as exc:
        raise RuntimeError(f"Unable to persist repository sync node id: {node_id_path}") from exc

    try:
        os.write(descriptor, (generated + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    return generated


def _repository_sync_server_epoch_path() -> Path:
    configured = str(os.environ.get(_SERVER_EPOCH_PATH_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return get_app_data_root() / _SERVER_EPOCH_FILENAME


def _project_revision_watermarks(project_revisions: Mapping[str, Any] | None) -> dict[str, int]:
    """Hash project keys before persisting monotonic revision watermarks.

    Project keys are not credentials, but the sidecar only needs stable lookup
    keys.  Hashing keeps project names out of a small infrastructure metadata
    file that can be inspected by deployment tooling.
    """

    watermarks: dict[str, int] = {}
    for project_key, raw_revision in dict(project_revisions or {}).items():
        normalized_key = str(project_key or "").strip()
        if not normalized_key:
            continue
        try:
            revision = max(int(raw_revision or 0), 0)
        except (TypeError, ValueError):
            revision = 0
        key_hash = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
        watermarks[key_hash] = max(watermarks.get(key_hash, 0), revision)
    return watermarks


def _load_repository_sync_server_epoch(path: Path) -> tuple[dict[str, Any], bool]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, False
    except OSError as exc:
        raise RuntimeError(f"Unable to read repository sync server epoch: {path}") from exc

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}, True
    return (dict(payload), True) if isinstance(payload, dict) else ({}, True)


def _atomic_write_repository_sync_server_epoch(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            str(temporary_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, path)

        # Best-effort directory fsync makes the rename durable on POSIX.  It is
        # unsupported for directory handles on some Windows/Python builds.
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(str(path.parent), directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    except OSError as exc:
        raise RuntimeError(f"Unable to persist repository sync server epoch: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def get_repository_sync_server_epoch(
    *,
    database_instance_id: Any,
    project_revisions: Mapping[str, Any] | None,
    sidecar_path: Path | str | None = None,
) -> str:
    """Return the public server incarnation stored outside the database.

    The database singleton remains a useful marker for a rebuilt/replaced
    database, but an ordinary database backup also restores that marker.  This
    sidecar therefore keeps monotonic per-project revision high-water marks.  A
    restored database whose cursor goes backwards (or loses a known project)
    rotates the public epoch, causing clients to discard their saved pull
    cursors and perform a zero-based bootstrap.

    On first upgrade a fresh public epoch deliberately differs from the legacy
    database instance id. Existing clients therefore perform one safe
    bootstrap, including deployments where a database rollback happened before
    this sidecar feature was installed. Subsequent server restarts reuse it.
    """

    normalized_database_id = _validate_node_id(
        database_instance_id,
        source="repository sync database instance",
    )
    target_path = (
        Path(sidecar_path).expanduser().resolve(strict=False)
        if sidecar_path is not None
        else _repository_sync_server_epoch_path()
    )
    current_watermarks = _project_revision_watermarks(project_revisions)

    with _SERVER_EPOCH_LOCK:
        saved, existed = _load_repository_sync_server_epoch(target_path)
        saved_epoch = str(saved.get("epoch") or "").strip()
        try:
            saved_epoch = _validate_node_id(saved_epoch, source=str(target_path))
        except RuntimeError:
            saved_epoch = ""
        saved_database_id = str(saved.get("database_instance_id") or "").strip()
        saved_watermarks: dict[str, int] = {}
        raw_watermarks = saved.get("project_revision_watermarks")
        if isinstance(raw_watermarks, dict):
            for key, raw_revision in raw_watermarks.items():
                normalized_key = str(key or "").strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", normalized_key):
                    continue
                try:
                    saved_watermarks[normalized_key] = max(int(raw_revision or 0), 0)
                except (TypeError, ValueError):
                    continue

        database_replaced = bool(saved_epoch and saved_database_id != normalized_database_id)
        revision_regressed = any(
            current_watermarks.get(key, 0) < revision
            for key, revision in saved_watermarks.items()
        )
        invalid_existing_sidecar = bool(existed and not saved_epoch)

        if database_replaced or revision_regressed or invalid_existing_sidecar:
            epoch = uuid.uuid4().hex
            next_watermarks = dict(current_watermarks)
        elif saved_epoch:
            epoch = saved_epoch
            next_watermarks = dict(saved_watermarks)
            for key, revision in current_watermarks.items():
                next_watermarks[key] = max(next_watermarks.get(key, 0), revision)
        else:
            epoch = uuid.uuid4().hex
            next_watermarks = dict(current_watermarks)

        next_payload = {
            "version": _SERVER_EPOCH_FORMAT_VERSION,
            "epoch": epoch,
            "database_instance_id": normalized_database_id,
            "project_revision_watermarks": next_watermarks,
        }
        if saved != next_payload:
            _atomic_write_repository_sync_server_epoch(target_path, next_payload)
        return epoch


def build_repository_sync_headers(*, hop: int = 1, origin_node_id: str | None = None) -> dict[str, str]:
    normalized_hop = _bounded_int(hop, default=1, minimum=1, maximum=255)
    origin = _validate_node_id(
        origin_node_id or get_repository_sync_node_id(),
        source="outgoing request",
    )
    return {
        **build_agent_headers(),
        "X-PCIDS-Sync-Origin": origin,
        "X-PCIDS-Sync-Hop": str(normalized_hop),
    }


def require_repository_sync_request(request: Request) -> dict[str, Any]:
    """Authenticate an inbound peer request and reject obvious sync loops."""

    require_agent_token(request)

    origin = str(request.headers.get("X-PCIDS-Sync-Origin") or "").strip()
    if not origin:
        raise HTTPException(status_code=400, detail="Repository sync origin is required")
    try:
        origin = _validate_node_id(origin, source="request header")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raw_hop = str(request.headers.get("X-PCIDS-Sync-Hop") or "").strip()
    try:
        hop = int(raw_hop)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Repository sync hop must be an integer")
    if hop < 1:
        raise HTTPException(status_code=400, detail="Repository sync hop must be positive")

    own_node_id = get_repository_sync_node_id()
    if origin == own_node_id:
        raise HTTPException(status_code=508, detail="Repository sync loop detected: request originated from this node")
    if hop > 1:
        raise HTTPException(status_code=508, detail="Repository sync loop detected: hop limit exceeded")

    return {"origin_node_id": origin, "hop": hop}
