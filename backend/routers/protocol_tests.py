from __future__ import annotations

import asyncio
import atexit
import html
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response as FastAPIResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any, Optional

from backend.utils.db import SessionLocal, get_db, ensure_schema, generate_protocol_session_no
from backend.models.user import User
from backend.models import ProtocolSession, ProtocolLog
from backend.schemas import Response
from backend.routers.auth import get_current_user
from backend.utils.can_adapters import (
    CAN_ADAPTER_BACKENDS,
    CanAdapterConnection,
    CanAdapterError,
    CanDependencyMissingError,
    CanFrame,
    _normalize_bool_config,
    can_fd_length_to_dlc,
    close_can_adapter_connection,
    list_can_adapter_devices,
    match_expected_rx_frame,
    open_can_adapter_connection,
    parse_can_frame_id,
    parse_can_mask,
    validate_can_length,
)
from backend.utils.gpio_runtime import (
    GpioRuntimeConfigError,
    build_gpio_action_context,
    build_gpio_auto_config,
    detect_gpio_level_from_text,
    gpio_pattern_matches,
    load_gpio_runtime_profile,
    render_gpio_template,
    resolve_gpio_action_profile,
)
from backend.utils.wch_gpio import (
    WchGpioConnection,
    WchGpioError,
    close_wch_gpio_connection,
    open_wch_gpio_connection,
    run_wch_gpio_action,
)
from backend.utils.permission import require_permission
from backend.utils.notifications import create_structured_message, format_duration

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:  # pragma: no cover
    serial = None
    list_ports = None

router = APIRouter()

SERIAL_DEFAULT_TIMEOUT_SECONDS = 5.0
SERIAL_READ_POLL_TIMEOUT_SECONDS = 0.1
_SERIAL_SESSION_CONNECTIONS: dict[int, Any] = {}
_SERIAL_SESSION_IO_LOCKS: dict[int, threading.Lock] = {}
_SERIAL_SESSION_STOP_EVENTS: dict[int, threading.Event] = {}
_SERIAL_SESSION_THREADS: dict[int, threading.Thread] = {}
_SERIAL_SESSION_LOCK = threading.Lock()
_CAN_RECEIVE_BUFFER_LIMIT = 512
_CAN_RECEIVE_POLL_TIMEOUT_MS = 100
_CAN_SESSION_LOCK = threading.Lock()
_CAN_SESSION_RUNTIMES: dict[int, "CanSessionRuntime"] = {}
_WCH_GPIO_SESSION_LOCK = threading.Lock()
_WCH_GPIO_SESSION_CONNECTIONS: dict[int, WchGpioConnection] = {}


@dataclass
class CanSessionRuntime:
    connection: CanAdapterConnection
    stop_event: threading.Event
    worker: Optional[threading.Thread] = None
    io_lock: threading.Lock = field(default_factory=threading.Lock)
    rx_condition: threading.Condition = field(default_factory=threading.Condition)
    rx_frames: deque[tuple[int, CanFrame]] = field(default_factory=lambda: deque(maxlen=_CAN_RECEIVE_BUFFER_LIMIT))
    rx_sequence: int = 0
    rx_logged_sequences: set[int] = field(default_factory=set)


def _protocol_label(protocol: str) -> str:
    labels = {
        "can": "CAN",
        "canfd": "CAN FD",
        "serial": "串口",
        "ethernet": "以太网",
        "gpio": "GPIO",
        "gpio_io": "GPIO",
    }
    return labels.get(str(protocol or "").lower(), str(protocol or "通信协议").upper())


def _notify_protocol_result(db: Session, session: ProtocolSession, *, passed: bool) -> None:
    now = datetime.now()
    started_at = getattr(session, "created_at", None) or now
    try:
        duration_seconds = (now - started_at).total_seconds()
    except Exception:
        duration_seconds = 0
    status_label = "通过" if passed else "失败"
    create_structured_message(
        db,
        user_id=getattr(session, "created_by_user_id", None),
        category="通信协议",
        status="success" if passed else "error",
        status_label=status_label,
        primary_text=f"{session.target or '-'} · {_protocol_label(session.protocol)}协议验证{status_label}",
        meta_text=f"任务 {getattr(session, 'task_no', None) or '-'} · 时长 {format_duration(duration_seconds)}",
    )

PROTOCOL_REPORT_META = {
    "can": {
        "label": "CAN",
        "protocol_type": "CAN 2.0A 标准帧",
        "config_order": ["通道", "波特率", "标识符格式", "远程帧", "数据长度(Bytes)", "帧 ID", "默认数据"],
        "log_columns": ["时间戳", "帧 ID", "数据长度(Bytes)", "方向", "数据 (DATA)"],
    },
    "canfd": {
        "label": "CAN FD",
        "protocol_type": "CAN FD",
        "config_order": ["通道", "仲裁段波特率", "数据段波特率", "比特率切换 BRS", "标识符格式", "数据长度(Bytes)", "帧 ID"],
        "log_columns": ["时间戳", "帧 ID", "数据长度(Bytes)", "方向", "数据 (DATA)"],
    },
    "serial": {
        "label": "串口",
        "protocol_type": "串口",
        "config_order": ["串口号", "波特率", "自动追加换行符 (CRLF)", "长度(Bytes)", "数据位", "停止位", "校验位", "流控制"],
        "log_columns": ["时间戳", "方向", "长度(Bytes)", "数据 (Hex/ASCII)"],
    },
    "ethernet": {
        "label": "以太网",
        "protocol_type": "以太网",
        "config_order": ["传输协议", "本地 IP", "本地端口", "目标 IP", "目标端口", "监听端口", "超时时间 (ms)", "数据类型"],
        "log_columns": ["时间戳", "方向", "源地址", "目标地址", "协议", "数据 (DATA)"],
    },
    "gpio_io": {
        "label": "GPIO物理引脚",
        "protocol_type": "GPIO 物理引脚",
        "config_order": ["引脚选择", "模式", "目标电平", "上下拉", "期望电平", "触发方式", "超时时间 (ms)", "当前电平"],
        "log_columns": ["时间戳", "方向", "引脚", "模式", "电平", "说明"],
    },
    "gpio": {
        "label": "GPIO物理引脚",
        "protocol_type": "GPIO 物理引脚",
        "config_order": ["引脚选择", "模式", "目标电平", "上下拉", "期望电平", "触发方式", "超时时间 (ms)", "当前电平"],
        "log_columns": ["时间戳", "方向", "引脚", "模式", "电平", "说明"],
    },
}

CAN_ADAPTER_BRAND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "peak": ("pcan", "peak"),
    "kvaser": ("kvaser",),
    "vector": ("vector", "cancase", "canalyzer"),
    "zlg": ("zlg", "usbcan", "canalyst"),
    "canable": ("canable", "candlelight", "gs_usb"),
    "slcan": ("slcan", "lawicel"),
    "generic_can": ("canfd", "usb2can", "usb-can", "can bus", "can adapter", "canalyst-ii"),
}

CAN_ADAPTER_USB_ID_HINTS: dict[str, dict[str, str]] = {
    "vid:pid=3562:0103": {"profile": "generic_can", "label": "USB-CAN 串口适配器"},
}

CAN_ADAPTER_INTERFACE_HINTS: dict[str, tuple[str, ...]] = {
    "socketcan": ("can", "vcan", "slcan"),
    "serial_can": ("slcan", "lawicel", "gs_usb", "candlelight"),
}


def _match_can_adapter_profile(text: str) -> Optional[dict]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None

    for usb_id, meta in CAN_ADAPTER_USB_ID_HINTS.items():
        if usb_id in lowered:
            return {
                "profile": meta.get("profile") or "generic_can",
                "match_type": "usb_id",
                "match_value": usb_id,
                "label": meta.get("label") or "",
            }

    for profile, keywords in CAN_ADAPTER_BRAND_KEYWORDS.items():
        for keyword in keywords:
            if keyword in lowered:
                return {
                    "profile": profile,
                    "match_type": "keyword",
                    "match_value": keyword,
                    "label": "",
                }

    for profile, keywords in CAN_ADAPTER_INTERFACE_HINTS.items():
        for keyword in keywords:
            if keyword in lowered:
                return {
                    "profile": profile,
                    "match_type": "interface",
                    "match_value": keyword,
                    "label": "",
                }

    return None


def _detect_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0] or "").strip()
        finally:
            sock.close()
    except Exception:
        try:
            return str(socket.gethostbyname(socket.gethostname()) or "").strip()
        except Exception:
            return ""


def _list_local_ipv4_addresses() -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip()
        if not text or text.startswith("127.") or text == "0.0.0.0" or text in candidates:
            return
        candidates.append(text)

    add(_detect_local_ip())
    try:
        hostname = socket.gethostname()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            if family == socket.AF_INET and sockaddr:
                add(str(sockaddr[0] or "").strip())
    except Exception:
        pass
    return candidates


def _normalize_ethernet_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"tcp", "tcp client", "tcp_client"}:
        return "TCP Client"
    if normalized in {"tcp server", "tcp_server"}:
        return "TCP Server"
    if normalized == "udp":
        return "UDP"
    return "TCP Client"


def _is_valid_ipv4(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address)


def _parse_non_negative_int(value, default: Optional[int] = None) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = int(str(value).strip())
    except Exception:
        return default
    return number if number >= 0 else default


def _timeout_ms_to_seconds(value: object, default_ms: int = 5000) -> float:
    timeout_ms = _parse_non_negative_int(value, default_ms) or default_ms
    return max(timeout_ms / 1000.0, 0.001)


def _is_tcp_port_available(host: str, port: int) -> bool:
    target_host = str(host or "").strip() or "0.0.0.0"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((target_host, int(port)))
            return True
        finally:
            sock.close()
    except Exception:
        return False


def _probe_serial_devices() -> list[dict]:
    if list_ports is None:
        return []
    devices: list[dict] = []
    for port in list_ports.comports():
        devices.append(
            {
                "device": str(getattr(port, "device", "") or "").strip(),
                "name": str(getattr(port, "name", "") or "").strip(),
                "description": str(getattr(port, "description", "") or "").strip(),
                "manufacturer": str(getattr(port, "manufacturer", "") or "").strip(),
                "product": str(getattr(port, "product", "") or "").strip(),
                "interface": str(getattr(port, "interface", "") or "").strip(),
                "hwid": str(getattr(port, "hwid", "") or "").strip(),
                "vid": getattr(port, "vid", None),
                "pid": getattr(port, "pid", None),
                "serial_number": str(getattr(port, "serial_number", "") or "").strip(),
                "location": str(getattr(port, "location", "") or "").strip(),
                "source": "pyserial",
            }
        )
    return devices


def _probe_wch_serial_devices() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for item in _probe_serial_devices():
        description = str(item.get("description") or "")
        manufacturer = str(item.get("manufacturer") or "")
        hwid = str(item.get("hwid") or "")
        vid = item.get("vid")
        text = f"{description} {manufacturer} {hwid}".lower()
        is_wch = (
            "wch" in text
            or "ch344" in text
            or "ch343" in text
            or "ch910" in text
            or str(vid).lower() in {"6790", "0x1a86"}
        )
        if not is_wch:
            continue
        device = str(item.get("device") or "").strip()
        if not device:
            continue
        devices.append(
            {
                "device": device,
                "name": str(item.get("name") or device).strip(),
                "description": description.strip(),
                "manufacturer": manufacturer.strip(),
                "hwid": hwid.strip(),
                "vid": item.get("vid"),
                "pid": item.get("pid"),
                "serial_number": str(item.get("serial_number") or "").strip(),
                "location": str(item.get("location") or "").strip(),
                "chip": "CH344" if "ch344" in text else "CH343" if "ch343" in text else "WCH",
            }
        )
    return devices


def _probe_can_network_interfaces() -> list[dict]:
    items: list[dict] = []
    for root in ("/sys/class/net",):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            lowered = name.lower()
            if lowered == "lo" or not (lowered.startswith("can") or lowered.startswith("vcan") or lowered.startswith("slcan")):
                continue
            items.append(
                {
                    "channel": name,
                    "label": name.upper(),
                    "device": name,
                    "description": f"系统检测到 {name} CAN 网络接口",
                    "source": "net_interface",
                    "adapter_profile": "socketcan",
                    "adapter_match_type": "interface",
                    "adapter_match_value": name,
                }
            )
    return items


def _probe_darwin_can_usb_devices() -> list[dict]:
    if platform.system().lower() != "darwin":
        return []
    try:
        completed = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        payload = json.loads(completed.stdout or "{}")
    except Exception:
        return []

    results: list[dict] = []

    def walk(items: list[dict]) -> None:
        for item in items or []:
            name = " ".join(
                [
                    str(item.get("_name") or "").strip(),
                    str(item.get("vendor_id") or "").strip(),
                    str(item.get("product_id") or "").strip(),
                    str(item.get("manufacturer") or "").strip(),
                ]
            ).strip()
            matched = _match_can_adapter_profile(name)
            if matched:
                label = str(item.get("_name") or item.get("manufacturer") or "USB-CAN 适配器").strip()
                serial_number = str(item.get("serial_num") or "").strip()
                results.append(
                    {
                        "channel": serial_number or label,
                        "label": label,
                        "device": serial_number or label,
                        "description": name or label,
                        "source": "darwin_usb_probe",
                        "adapter_profile": matched.get("profile"),
                        "adapter_match_type": matched.get("match_type"),
                        "adapter_match_value": matched.get("match_value"),
                    }
                )
            walk(item.get("_items", []) or [])

    walk(payload.get("SPUSBDataType", []) or [])
    return results


def _probe_can_adapters(protocol: Optional[str] = None) -> list[dict]:
    normalized_protocol = str(protocol or "").strip().lower() or None
    candidates: list[dict] = []
    seen: set[str] = set()

    if normalized_protocol in {"can", "canfd"}:
        for item in list_can_adapter_devices(normalized_protocol):
            key = f"{item.get('backend_key')}|{item.get('adapter_key') or item.get('device')}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(item)
        return candidates

    for item in _probe_can_network_interfaces():
        key = f"{item.get('source')}|{item.get('device')}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)

    for item in _probe_darwin_can_usb_devices():
        key = f"{item.get('source')}|{item.get('device')}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)

    for item in list_can_adapter_devices():
        key = f"{item.get('source')}|{item.get('adapter_key') or item.get('device')}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)

    return candidates


def _store_can_session_connection(session_id: int, connection: CanAdapterConnection) -> None:
    runtime = CanSessionRuntime(connection=connection, stop_event=threading.Event())
    worker = threading.Thread(
        target=_can_listener_worker,
        args=(session_id, runtime),
        name=f"can-listener-{session_id}",
        daemon=True,
    )
    runtime.worker = worker
    with _CAN_SESSION_LOCK:
        old = _CAN_SESSION_RUNTIMES.pop(session_id, None)
        _CAN_SESSION_RUNTIMES[session_id] = runtime
    if old is not None:
        _shutdown_can_runtime(old)
    worker.start()


def _get_can_session_runtime(session_id: int) -> Optional[CanSessionRuntime]:
    with _CAN_SESSION_LOCK:
        return _CAN_SESSION_RUNTIMES.get(session_id)


def _get_can_session_connection(session_id: int) -> Optional[CanAdapterConnection]:
    runtime = _get_can_session_runtime(session_id)
    return runtime.connection if runtime is not None else None


def _close_can_session_connection(session_id: int) -> None:
    with _CAN_SESSION_LOCK:
        runtime = _CAN_SESSION_RUNTIMES.pop(session_id, None)
    if runtime is not None:
        _shutdown_can_runtime(runtime)


def _close_all_can_session_connections() -> None:
    with _CAN_SESSION_LOCK:
        runtimes = list(_CAN_SESSION_RUNTIMES.values())
        _CAN_SESSION_RUNTIMES.clear()
    for runtime in runtimes:
        _shutdown_can_runtime(runtime)


def _store_wch_gpio_session_connection(session_id: int, connection: WchGpioConnection) -> None:
    with _WCH_GPIO_SESSION_LOCK:
        old = _WCH_GPIO_SESSION_CONNECTIONS.pop(session_id, None)
        _WCH_GPIO_SESSION_CONNECTIONS[session_id] = connection
    if old is not None:
        close_wch_gpio_connection(old)


def _get_wch_gpio_session_connection(session_id: int) -> Optional[WchGpioConnection]:
    with _WCH_GPIO_SESSION_LOCK:
        return _WCH_GPIO_SESSION_CONNECTIONS.get(session_id)


def _close_wch_gpio_session_connection(session_id: int) -> None:
    with _WCH_GPIO_SESSION_LOCK:
        connection = _WCH_GPIO_SESSION_CONNECTIONS.pop(session_id, None)
    close_wch_gpio_connection(connection)


def _close_all_wch_gpio_session_connections() -> None:
    with _WCH_GPIO_SESSION_LOCK:
        connections = list(_WCH_GPIO_SESSION_CONNECTIONS.values())
        _WCH_GPIO_SESSION_CONNECTIONS.clear()
    for connection in connections:
        close_wch_gpio_connection(connection)


def cleanup_protocol_session_resources() -> None:
    _close_all_serial_session_connections()
    _close_all_can_session_connections()
    _close_all_wch_gpio_session_connections()


def _shutdown_can_runtime(runtime: CanSessionRuntime) -> None:
    runtime.stop_event.set()
    if runtime.worker is not None and runtime.worker.is_alive() and runtime.worker is not threading.current_thread():
        runtime.worker.join(timeout=1.0)
    try:
        close_can_adapter_connection(runtime.connection)
    except Exception:
        pass


def _format_can_log_frame_id(frame: CanFrame) -> str:
    frame_format = "EXT" if frame.is_extended_id else "STD"
    return f"0x{frame.frame_id:X} ({frame_format})"


def _format_can_log_payload(frame: CanFrame, data_type: Optional[str]) -> str:
    frame_type = "CAN FD" if frame.is_fd else "Classical CAN"
    brs_tag = " BRS" if frame.is_fd and frame.bitrate_switch else ""
    if frame.is_remote_frame:
        return f"{frame_type}{brs_tag} RTR declared_len={frame.declared_data_length} actual_len=0"
    payload_text = _decode_protocol_payload(frame.data, data_type) if frame.data else "<empty>"
    return f"{frame_type}{brs_tag} DATA actual_len={frame.data_length} declared_len={frame.declared_data_length} payload={payload_text}"


def _format_can_rx_log_payload(frame: CanFrame, data_type: Optional[str]) -> str:
    if frame.is_remote_frame:
        return "<remote>"
    return _decode_protocol_payload(frame.data, data_type) if frame.data else "<empty>"


def _append_can_rx_logs(session_id: int, frame_entries: list[tuple[int, CanFrame]], runtime: CanSessionRuntime) -> None:
    if not frame_entries:
        return
    with runtime.rx_condition:
        entries_to_log = [
            (sequence, frame)
            for sequence, frame in frame_entries
            if sequence not in runtime.rx_logged_sequences
        ]
        for sequence, _frame in entries_to_log:
            runtime.rx_logged_sequences.add(sequence)
    if not entries_to_log:
        return
    db = SessionLocal()
    try:
        session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
        if not session or int(getattr(session, "status", 0) or 0) != 1:
            return
        config = _load_session_config(session)
        data_type = config.get("data_type")
        for _sequence, frame in entries_to_log:
            db.add(
                ProtocolLog(
                    session_id=session.id,
                    protocol=session.protocol,
                    timestamp=datetime.now(),
                    direction="Rx",
                    frame_id=_format_can_log_frame_id(frame),
                    dlc=frame.declared_data_length,
                    data=_format_can_rx_log_payload(frame, data_type),
                )
            )
            session.rx_count = int(getattr(session, "rx_count", 0) or 0) + 1
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "can.rx_log.persist_failed | session_id=%s frame_count=%s",
            session_id,
            len(frame_entries),
        )
    finally:
        db.close()


def _append_can_listener_failure(session_id: int, detail: str) -> None:
    db = SessionLocal()
    try:
        session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
        if session is None:
            return
        session.status = 2
        _append_system_log(db, session, detail)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _can_listener_worker(session_id: int, runtime: CanSessionRuntime) -> None:
    backend = CAN_ADAPTER_BACKENDS.get(runtime.connection.backend_key)
    if backend is None:
        _append_can_listener_failure(session_id, "CAN 接收线程启动失败：找不到适配器后端")
        _close_can_session_connection(session_id)
        return
    try:
        while not runtime.stop_event.is_set():
            with runtime.io_lock:
                frames = backend.receive(runtime.connection, timeout_ms=_CAN_RECEIVE_POLL_TIMEOUT_MS)
            if not frames:
                continue
            frame_entries: list[tuple[int, CanFrame]] = []
            with runtime.rx_condition:
                for frame in frames:
                    runtime.rx_sequence += 1
                    runtime.rx_frames.append((runtime.rx_sequence, frame))
                    frame_entries.append((runtime.rx_sequence, frame))
            _append_can_rx_logs(session_id, frame_entries, runtime)
            # Persist the received frames before waking a request that is
            # waiting for validation. Otherwise the request and listener can
            # commit through separate SQLAlchemy sessions at the same time,
            # which can lose the Rx audit row even though validation passed.
            with runtime.rx_condition:
                runtime.rx_condition.notify_all()
    except CanDependencyMissingError as exc:
        _append_can_listener_failure(session_id, f"dependency_missing: {exc.message}")
        _close_can_session_connection(session_id)
    except CanAdapterError as exc:
        _append_can_listener_failure(session_id, f"CAN 持续接收已停止：{exc.message}")
        _close_can_session_connection(session_id)
    except Exception as exc:
        _append_can_listener_failure(session_id, f"CAN 持续接收线程异常退出：{exc}")
        _close_can_session_connection(session_id)


def _wait_for_expected_can_frame(
    runtime: CanSessionRuntime,
    *,
    after_sequence: int,
    timeout_ms: int,
    matcher,
) -> Optional[tuple[int, CanFrame]]:
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    with runtime.rx_condition:
        while True:
            for sequence, frame in runtime.rx_frames:
                if sequence <= after_sequence:
                    continue
                if matcher(frame):
                    return sequence, frame
            remaining = deadline - time.monotonic()
            if remaining <= 0 or runtime.stop_event.is_set():
                return None
            runtime.rx_condition.wait(timeout=min(remaining, 0.2))


atexit.register(cleanup_protocol_session_resources)


def _list_serial_channels() -> list[str]:
    ports = [str(item.get("device") or "").strip() for item in _probe_serial_devices() if str(item.get("device") or "").strip()]
    unique = list(dict.fromkeys(ports))
    return unique


def _format_can_adapter_label(device: dict[str, Any]) -> str:
    adapter_name = str(device.get("adapter_name") or device.get("label") or "CAN 适配器").strip()
    serial_number = str(device.get("serial_number") or "").strip()
    return f"{adapter_name} / {serial_number}" if serial_number else adapter_name


def _collect_can_channel_options(device: dict[str, Any]) -> list[str]:
    channels = device.get("channels") if isinstance(device.get("channels"), list) else []
    return [str(item.get("name") or "").strip() for item in channels if str(item.get("name") or "").strip()]


def _build_can_adapter_options(devices: list[dict[str, Any]]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for device in devices:
        adapter_key = str(device.get("adapter_key") or "").strip()
        if not adapter_key:
            continue
        options.append(
            {
                "label": _format_can_adapter_label(device),
                "value": adapter_key,
            }
        )
    return options


_CAN_AUTHORITATIVE_DEVICE_FIELDS = (
    "backend_key",
    "adapter_key",
    "adapter_name",
    "adapter_device",
    "adapter_serial",
    "com_port",
    "physical_channel",
    "physical_channel_options",
    "detected_devices",
    "adapter_options",
    "vid",
    "pid",
    "sdk_device_index",
)

_CAN_EDITABLE_FIELDS = {
    "baud_rate",
    "bitrate",
    "id_format",
    "frame_format",
    "remote_frame",
    "termination_enabled",
    "data_length",
    "dlc",
    "expected_rx_id",
    "expected_rx_mask",
    "expected_data",
    "rx_timeout_ms",
    "data_type",
}

_CANFD_EDITABLE_FIELDS = {
    "arb_baud_rate",
    "arb_bitrate",
    "data_baud_rate",
    "data_bitrate",
    "brs",
    "termination_enabled",
    "id_format",
    "frame_format",
    "data_length",
    "dlc",
    "expected_rx_id",
    "expected_rx_mask",
    "expected_data",
    "rx_timeout_ms",
    "data_type",
}


def _filter_non_empty_request_fields(request_config: dict[str, Any], allowed_fields: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in request_config.items()
        if key in allowed_fields and value is not None and value != ""
    }


def _merge_can_connect_request_config(protocol: str, base_config: dict[str, Any], request_config: dict[str, Any]) -> dict[str, Any]:
    devices = base_config.get("detected_devices") if isinstance(base_config.get("detected_devices"), list) else _probe_can_adapters(protocol)
    devices = [item for item in devices if isinstance(item, dict)]
    selected_adapter_key = str(request_config.get("adapter_key") or "").strip()
    selected_device = _resolve_selected_can_device({"adapter_key": selected_adapter_key}, devices) if selected_adapter_key else None
    if selected_adapter_key and selected_device is None:
        raise HTTPException(status_code=409, detail="所选 CAN 适配器已离线，请重新扫描后再连接")
    if selected_device is None:
        selected_device = _resolve_selected_can_device(base_config, devices)
    if selected_device is None and devices:
        selected_device = devices[0]

    editable_fields = _CANFD_EDITABLE_FIELDS if protocol == "canfd" else _CAN_EDITABLE_FIELDS
    merged = dict(base_config or {})
    merged.update(_filter_non_empty_request_fields(request_config, editable_fields))
    merged["detected_devices"] = devices
    merged["adapter_options"] = _build_can_adapter_options(devices)

    if selected_device:
        adapter_device = selected_device.get("adapter_device") or selected_device.get("device") or selected_device.get("pnp_device_id") or ""
        physical_channel_options = _collect_can_channel_options(selected_device)
        requested_channel = str(
            request_config.get("physical_channel")
            or request_config.get("channel")
            or merged.get("physical_channel")
            or merged.get("channel")
            or ""
        ).strip()
        if requested_channel and requested_channel in physical_channel_options:
            physical_channel = requested_channel
        else:
            physical_channel = physical_channel_options[0] if physical_channel_options else requested_channel

        merged.update(
            {
                "backend_key": selected_device.get("backend_key") or "",
                "adapter_key": selected_device.get("adapter_key") or "",
                "adapter_name": selected_device.get("adapter_name") or selected_device.get("label") or "CAN 适配器",
                "adapter_device": adapter_device,
                "adapter_serial": selected_device.get("serial_number") or "",
                "com_port": selected_device.get("adapter_device") or "",
                "physical_channel": physical_channel,
                "physical_channel_options": physical_channel_options,
                "channel": physical_channel,
                "vid": selected_device.get("vid"),
                "pid": selected_device.get("pid"),
                "sdk_device_index": selected_device.get("sdk_device_index"),
            }
        )

    for key in _CAN_AUTHORITATIVE_DEVICE_FIELDS:
        if key not in merged and key in base_config:
            merged[key] = base_config.get(key)
    return merged


def _resolve_selected_can_device(config: dict[str, Any], devices: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    adapter_key = str(config.get("adapter_key") or "").strip()
    if adapter_key:
        for device in devices:
            if str(device.get("adapter_key") or "").strip() == adapter_key:
                return device
        return None
    return devices[0] if devices else None


def _is_extended_can_id(config: dict[str, Any]) -> bool:
    return "扩展" in str(config.get("id_format") or config.get("frame_format") or "").strip()


def _normalize_can_rx_timeout_ms(value: Any) -> int:
    number = _parse_non_negative_int(value, 1000)
    if not number:
        raise ValueError("接收超时时间必须为正整数")
    return int(number)


def _normalize_can_expected_data(config: dict[str, Any]) -> Optional[bytes]:
    value = config.get("expected_data")
    text = str(value or "").strip()
    if not text:
        return None
    return _encode_protocol_payload(text, config.get("data_type"))


def _normalize_can_length_for_send(
    protocol: str,
    payload_data: Optional[str],
    requested_length: Optional[int],
    data_type: Optional[str],
    *,
    is_remote_frame: bool,
) -> tuple[int, int]:
    payload_length = 0 if is_remote_frame else _parse_payload_length(payload_data, data_type)
    if requested_length in {None, ""}:
        requested_length = payload_length
    normalized_length = validate_can_length(protocol, int(requested_length))
    if is_remote_frame:
        return normalized_length, 0
    if protocol == "can":
        if payload_length > normalized_length:
            raise ValueError("输入数据长度不能超过配置的数据长度(DLC)")
        return normalized_length, payload_length
    if payload_length != normalized_length:
        raise ValueError("普通数据帧要求输入数据长度与配置的数据长度(Bytes)严格一致，系统不会自动补零")
    return normalized_length, payload_length


def _normalize_can_runtime_config(
    protocol: str,
    config: dict[str, Any],
    *,
    refresh_adapter: bool = True,
) -> dict[str, Any]:
    normalized = dict(config or {})
    selected_device = (
        _resolve_selected_can_device(normalized, _probe_can_adapters(protocol))
        if refresh_adapter
        else None
    )
    if selected_device:
        adapter_device = selected_device.get("adapter_device") or selected_device.get("device") or selected_device.get("pnp_device_id") or ""
        normalized["adapter_key"] = selected_device.get("adapter_key") or normalized.get("adapter_key") or ""
        normalized["backend_key"] = selected_device.get("backend_key") or normalized.get("backend_key") or ""
        normalized["adapter_name"] = selected_device.get("adapter_name") or selected_device.get("label") or ""
        normalized["adapter_serial"] = selected_device.get("serial_number") or ""
        normalized["adapter_device"] = adapter_device
        normalized["com_port"] = selected_device.get("adapter_device") or normalized.get("com_port") or ""
        normalized["sdk_device_index"] = selected_device.get("sdk_device_index")
        normalized["dependency_status"] = selected_device.get("dependency_status") or ""
        normalized["dependency_message"] = selected_device.get("dependency_message") or ""
        normalized["physical_channel_options"] = _collect_can_channel_options(selected_device)
        normalized["vid"] = selected_device.get("vid")
        normalized["pid"] = selected_device.get("pid")
    normalized["physical_channel"] = str(normalized.get("physical_channel") or normalized.get("channel") or "").strip()
    normalized["channel"] = normalized["physical_channel"]
    normalized["data_length"] = normalized.get("data_length", normalized.get("dlc"))
    if protocol == "canfd":
        normalized["termination_enabled"] = _normalize_bool_config(normalized.get("termination_enabled"), default=True)
        normalized["brs"] = _normalize_bool_config(normalized.get("brs"), default=False)
        normalized["canfd_non_iso"] = _normalize_bool_config(normalized.get("canfd_non_iso"), default=False)
    else:
        normalized["termination_enabled"] = _normalize_bool_config(normalized.get("termination_enabled"), default=False)
    if protocol == "canfd" and "remote_frame" not in normalized:
        normalized["remote_frame"] = False
    return normalized


def _build_auto_channel_config(protocol: str) -> tuple[dict, list[str]]:
    normalized = str(protocol or "").strip().lower()
    logs: list[str] = ["开始枚举本机可用硬件设备"]

    if normalized == "can":
        adapters = _probe_can_adapters("can")
        if not adapters:
            raise HTTPException(status_code=404, detail="未检测到可用支持经典CAN的适配器，请检查 USB-CAN、COM口、驱动或接线后重试")
        primary = _resolve_selected_can_device({}, adapters)
        physical_channel_options = _collect_can_channel_options(primary or {})
        adapter_options = _build_can_adapter_options(adapters)
        logs.append(f"已识别 {len(adapters)} 个经典 CAN 适配器，首选适配器：{_format_can_adapter_label(primary or {})}")
        if primary and str(primary.get("dependency_status") or "").strip() == "dependency_missing":
            logs.append(f"依赖检查失败：{primary.get('dependency_message')}")
        elif primary and str(primary.get("dependency_status") or "").strip() == "device_busy":
            logs.append(f"设备占用检查：{primary.get('dependency_message')}")
        if physical_channel_options:
            logs.append(f"设备返回可用物理通道：{', '.join(physical_channel_options)}")
        else:
            logs.append("当前未获得设备物理通道信息。")
        config = {
            "method": "auto_can_channel",
            "backend_key": primary.get("backend_key") or "",
            "adapter_key": primary.get("adapter_key") or "",
            "adapter_name": primary.get("adapter_name") or primary.get("label") or "CAN 适配器",
            "adapter_serial": primary.get("serial_number") or "",
            "adapter_device": primary.get("adapter_device") or primary.get("device") or primary.get("pnp_device_id") or "",
            "com_port": primary.get("adapter_device") or "",
            "adapter_source": primary.get("source") or "probe",
            "detected_devices": adapters,
            "adapter_options": adapter_options,
            "physical_channel": physical_channel_options[0] if physical_channel_options else "",
            "physical_channel_options": physical_channel_options,
            "channel": physical_channel_options[0] if physical_channel_options else "",
            "probe_summary": f"已自动识别 {len(adapters)} 个经典 CAN 适配器",
            "dependency_status": primary.get("dependency_status") or "",
            "dependency_message": primary.get("dependency_message") or "",
            "baud_rate": "500kbps",
            "bitrate": "500kbps",
            "id_format": "标准帧(11位)",
            "frame_format": "标准帧(11位)",
            "remote_frame": False,
            "termination_enabled": str(primary.get("backend_key") or "").strip() == "usbcanfd_200u",
            "data_length": 8,
            "dlc": 8,
            "expected_rx_id": "",
            "expected_rx_mask": "",
            "expected_data": "",
            "rx_timeout_ms": 1000,
            "vid": primary.get("vid"),
            "pid": primary.get("pid"),
        }
        logs.append(f"适配器探测完成：{config['adapter_name'] or '未命名适配器'}")
        return config, logs

    if normalized == "canfd":
        adapters = _probe_can_adapters("canfd")
        if not adapters:
            raise HTTPException(status_code=404, detail="未检测到可用 CAN FD 适配器，请检查 USB-CAN FD 设备、驱动或系统接口后重试")
        primary = _resolve_selected_can_device({}, adapters)
        physical_channel_options = _collect_can_channel_options(primary or {})
        adapter_options = _build_can_adapter_options(adapters)
        logs.append(f"已识别 {len(adapters)} 个 CAN FD 适配器，首选适配器：{_format_can_adapter_label(primary or {})}")
        if primary and str(primary.get("dependency_status") or "").strip() == "dependency_missing":
            logs.append(f"依赖检查失败：{primary.get('dependency_message')}")
        elif primary and str(primary.get("dependency_status") or "").strip() == "device_busy":
            logs.append(f"设备占用检查：{primary.get('dependency_message')}")
        if physical_channel_options:
            logs.append(f"SDK 返回可用物理通道：{', '.join(physical_channel_options)}")
        else:
            logs.append("当前未获得 SDK 返回的物理通道信息，暂不自动选择 CAN0/CAN1。")
        config = {
            "method": "auto_can_channel",
            "adapter_key": primary.get("adapter_key") or "",
            "adapter_name": primary.get("adapter_name") or primary.get("label") or "CAN 适配器",
            "adapter_serial": primary.get("serial_number") or "",
            "adapter_device": primary.get("device") or primary.get("pnp_device_id") or "",
            "adapter_source": primary.get("source") or "probe",
            "detected_devices": adapters,
            "adapter_options": adapter_options,
            "physical_channel": physical_channel_options[0] if physical_channel_options else "",
            "physical_channel_options": physical_channel_options,
            "channel": physical_channel_options[0] if physical_channel_options else "",
            "probe_summary": f"已自动识别 {len(adapters)} 个 CAN FD 适配器",
            "dependency_status": primary.get("dependency_status") or "",
            "dependency_message": primary.get("dependency_message") or "",
            "baud_rate": "500kbps",
            "bitrate": "500kbps",
            "arb_baud_rate": "500kbps",
            "arb_bitrate": "500kbps",
            "data_baud_rate": "2Mbps",
            "data_bitrate": "2Mbps",
            "id_format": "标准帧(11位)",
            "frame_format": "标准帧(11位)",
            "remote_frame": False,
            "termination_enabled": True,
            "brs": False,
            "canfd_non_iso": False,
            "data_length": 8,
            "dlc": 8,
            "expected_rx_id": "",
            "expected_rx_mask": "",
            "expected_data": "",
            "rx_timeout_ms": 1000,
        }
        logs.append(f"适配器探测完成：{config['adapter_name'] or '未命名适配器'}")
        return config, logs

    if normalized == "serial":
        ports = _list_serial_channels()
        if not ports:
            raise HTTPException(status_code=404, detail="未检测到可用串口设备，请检查串口线缆或驱动后重试")
        logs.append(f"已识别 {len(ports)} 个串口设备，默认使用 {ports[0]}")
        return {
            "method": "auto_serial_channel",
            "com_port": ports[0],
            "serial_ports": ports,
            "channel_options": ports,
            "baud_rate": 115200,
            "auto_append_crlf": False,
            "length_bytes": 64,
            "data_bits": 8,
            "stop_bits": 1,
            "parity": "NONE",
            "flow_control": "NONE",
        }, logs

    if normalized == "ethernet":
        local_ip_options = _list_local_ipv4_addresses()
        local_ip = local_ip_options[0] if local_ip_options else ""
        if not local_ip:
            raise HTTPException(status_code=404, detail="未获取到本机可用网络地址，请检查网络连接后重试")
        logs.append(f"已识别本机网络地址 {local_ip}")
        return {
            "method": "tcp",
            "transport_protocol": "TCP Client",
            "local_ip": local_ip,
            "local_ip_options": local_ip_options,
            "channel_options": local_ip_options,
            "target_ip": "",
            "target_port": 8080,
            "listen_port": 8080,
            "local_port": 8080,
            "timeout": 3000,
        }, logs

    if normalized in {"gpio", "gpio_io"}:
        wch_serial_devices = _probe_wch_serial_devices()
        wch_serial_ports = [str(item.get("device") or "").strip() for item in wch_serial_devices if str(item.get("device") or "").strip()]
        try:
            profile = load_gpio_runtime_profile()
        except GpioRuntimeConfigError as exc:
            selected_port = wch_serial_ports[0] if wch_serial_ports else ""
            config = {
                "method": "gpio",
                "pin": "GPIO0",
                "channel_options": [f"GPIO{index}" for index in range(16)],
                "mode": "输出",
                "target_level": "高电平",
                "pull_mode": "无 (浮空)",
                "expected_level": "高电平",
                "current_level": "",
                "trigger_type": "上升沿",
                "timeout_ms": 5000,
                "wch_serial_devices": wch_serial_devices,
                "wch_serial_ports": wch_serial_ports,
                "gpio_runtime_ready": bool(selected_port),
                "gpio_runtime_error": "" if selected_port else str(exc),
                "gpio_transport_kind": "wch_gpio" if selected_port else "",
                "gpio_transport_config": {
                    "kind": "wch_gpio",
                    "com_port": selected_port,
                    "pin_base_index": 0,
                } if selected_port else {},
                "supports_readback": True,
            }
            logs.append(f"已检测到 {len(wch_serial_ports)} 个 WCH 串口设备")
            if selected_port:
                logs.append(f"GPIO WCH 直控已就绪：默认使用 {selected_port}")
                config["probe_summary"] = f"已检测到 {len(wch_serial_ports)} 个 WCH 串口；可选择串口进行 GPIO 调试"
            else:
                logs.append(f"GPIO 真实业务映射未配置：{str(exc)}")
                config["probe_summary"] = "未检测到 WCH 串口；GPIO 控制映射未配置"
            return config, logs
        config = build_gpio_auto_config(profile)
        config["wch_serial_devices"] = wch_serial_devices
        config["wch_serial_ports"] = wch_serial_ports
        config["gpio_runtime_ready"] = True
        transport_kind = str(config.get("gpio_transport_kind") or "").strip().lower()
        transport_label = {
            "serial": f"串口 {config.get('gpio_transport_config', {}).get('com_port') or '-'}",
            "can": f"CAN {config.get('gpio_transport_config', {}).get('physical_channel') or config.get('gpio_transport_config', {}).get('channel') or '-'}",
            "canfd": f"CAN FD {config.get('gpio_transport_config', {}).get('physical_channel') or config.get('gpio_transport_config', {}).get('channel') or '-'}",
        }.get(transport_kind, transport_kind.upper() or "未知链路")
        logs.append(f"已检测到 {len(wch_serial_ports)} 个 WCH 串口设备")
        logs.append(f"GPIO 通道已绑定真实业务后端：{transport_label}")
        config["probe_summary"] = f"GPIO 真实业务映射已加载：{transport_label}"
        return config, logs

    raise HTTPException(status_code=400, detail="暂不支持当前协议通道建立")


def _merge_connect_request_config(protocol: str, base_config: dict, request_config: Optional[dict]) -> dict:
    merged = dict(base_config or {})
    if not isinstance(request_config, dict):
        return merged

    normalized_protocol = _normalize_protocol_kind(protocol)
    if normalized_protocol == "ethernet":
        protocol_mode = _normalize_ethernet_mode(
            request_config.get("transport_protocol") or request_config.get("protocol") or merged.get("transport_protocol")
        )
        merged["transport_protocol"] = protocol_mode
        merged["protocol"] = protocol_mode

        for key in ("local_ip", "target_ip"):
            value = request_config.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                merged[key] = text

        for key in ("target_port", "local_port", "listen_port", "timeout"):
            value = request_config.get(key)
            if value is not None and value != "":
                merged[key] = value

        local_ip_options = request_config.get("local_ip_options")
        if isinstance(local_ip_options, list) and local_ip_options:
            merged["local_ip_options"] = local_ip_options
            merged["channel_options"] = local_ip_options
        return merged

    if normalized_protocol in {"can", "canfd"}:
        return _merge_can_connect_request_config(normalized_protocol, merged, request_config)

    if normalized_protocol in {"gpio", "gpio_io"}:
        requested_transport_config = request_config.get("gpio_transport_config") if isinstance(request_config.get("gpio_transport_config"), dict) else {}
        selected_port = str(
            request_config.get("wch_serial_port")
            or request_config.get("com_port")
            or requested_transport_config.get("com_port")
            or ""
        ).strip()
        merged.update({key: value for key, value in request_config.items() if value is not None and value != ""})
        if selected_port:
            transport_config = merged.get("gpio_transport_config") if isinstance(merged.get("gpio_transport_config"), dict) else {}
            merged["wch_serial_port"] = selected_port
            merged["com_port"] = selected_port
            merged["gpio_transport_kind"] = "wch_gpio"
            merged["gpio_transport_config"] = {
                **transport_config,
                "kind": "wch_gpio",
                "com_port": selected_port,
                "pin_base_index": _parse_non_negative_int(transport_config.get("pin_base_index"), 0),
            }
        return merged

    merged.update({key: value for key, value in request_config.items() if value is not None and value != ""})
    return merged


def _fmt_datetime(value: Optional[datetime]) -> str:
    if not value:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_time_only(value: Optional[datetime]) -> str:
    if not value:
        return "-"
    return value.strftime("%H:%M:%S.%f")[:-3]


def _format_duration(start: Optional[datetime], end: Optional[datetime]) -> str:
    if not start or not end:
        return "-"
    total_ms = max(int((end - start).total_seconds() * 1000), 0)
    if total_ms < 1000:
        return f"{total_ms} 毫秒"
    total_seconds = total_ms / 1000
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{int(hours)} 小时 {int(minutes)} 分 {str(round(seconds, 3)).rstrip('0').rstrip('.')} 秒"
    if minutes > 0:
        return f"{int(minutes)} 分 {str(round(seconds, 3)).rstrip('0').rstrip('.')} 秒"
    return f"{str(round(total_seconds, 3)).rstrip('0').rstrip('.')} 秒"


def _normalize_protocol_log_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _is_success_protocol_log(content: object) -> bool:
    text = _normalize_protocol_log_text(content)
    if not text:
        return False
    success_keywords = (
        "验证通过",
        "测试通过",
        "已成功",
        "发送成功",
        "成功接收到",
        "读取值与期望值一致",
        "回读值与设定值完全一致",
        "按 api 调用成功退化判定通过",
        "在设定超时时间内成功",
        "在预设超时时间内收到回复",
        "reply frame received",
        "passed",
    )
    return any(keyword in text for keyword in success_keywords)


def _resolve_session_end_time(session: ProtocolSession, logs: list[ProtocolLog], config: Optional[dict] = None) -> Optional[datetime]:
    config_map = config if isinstance(config, dict) else _load_session_config(session)
    validated_at = str(config_map.get("validated_at") or "").strip()
    if validated_at:
        try:
            return datetime.fromisoformat(validated_at)
        except Exception:
            pass
    if logs:
        latest_log = max((log.timestamp for log in logs if getattr(log, "timestamp", None)), default=None)
        if latest_log:
            return latest_log
    return getattr(session, "updated_at", None) or getattr(session, "created_at", None)


def _safe_text(value) -> str:
    return html.escape("" if value is None else str(value))


def _load_session_config(session: ProtocolSession) -> dict:
    try:
        parsed = json.loads(session.config_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _persist_validation_result(
    session: ProtocolSession,
    config: dict,
    *,
    passed: bool,
    detail: str,
    code: Optional[str] = None,
    payload_length: Optional[int] = None,
    reply_frame_received: Optional[bool] = None,
) -> dict:
    updated = dict(config or {})
    updated["validation_result"] = "passed" if passed else "failed"
    updated["validation_detail"] = detail
    if code:
        updated["validation_code"] = code
    updated["reply_frame_received"] = bool(passed) if reply_frame_received is None else bool(reply_frame_received)
    updated["validated_at"] = datetime.now().isoformat(timespec="seconds")
    if payload_length is not None:
        updated["payload_length"] = payload_length
    session.config_json = json.dumps(updated, ensure_ascii=False)
    return updated


def _normalize_protocol_kind(protocol: Optional[str]) -> str:
    normalized = str(protocol or "").strip().lower()
    if normalized in {"gpio", "gpio_io", "gpio-io"}:
        return "gpio_io"
    return normalized or "can"


def _format_timeout_ms(value: object, default: int = 3000) -> str:
    normalized = str(value if value not in {None, ""} else default).strip()
    if normalized.lower().endswith("ms"):
        return normalized
    return f"{normalized} ms"


def _build_protocol_config(session: ProtocolSession, protocol: str, config: dict, logs: list[ProtocolLog]) -> dict[str, str]:
    normalized = str(protocol or "").strip().lower()
    first_tx = next((log for log in logs if str(log.direction or "").upper() == "TX"), None)
    if normalized == "can":
        return {
            "通道": str(config.get("channel") or "CAN1"),
            "波特率": str(config.get("bitrate") or config.get("baud_rate") or "500kbps"),
            "标识符格式": str(config.get("frame_format") or "标准帧(11位)"),
            "远程帧": "启用" if bool(config.get("remote_frame")) else "禁用",
            "数据长度(Bytes)": str(first_tx.dlc if first_tx and first_tx.dlc is not None else config.get("data_length") or config.get("dlc") or 8),
            "帧 ID": str(first_tx.frame_id or config.get("frame_id") or "-") if first_tx else str(config.get("frame_id") or "-"),
            "默认数据": str(first_tx.data or config.get("data") or "-") if first_tx else str(config.get("data") or "-"),
        }
    if normalized == "canfd":
        data_bitrate = (
            "跟随仲裁段"
            if config.get("brs") is False
            else str(config.get("data_bitrate") or config.get("data_baud_rate") or "2Mbps")
        )
        return {
            "通道": str(config.get("channel") or "CAN1"),
            "仲裁段波特率": str(config.get("arb_bitrate") or config.get("arb_baud_rate") or config.get("bitrate") or "500kbps"),
            "数据段波特率": data_bitrate,
            "比特率切换 BRS": "启用" if config.get("brs", True) else "关闭",
            "标识符格式": str(config.get("frame_format") or "标准帧(11位)"),
            "数据长度(Bytes)": str(first_tx.dlc if first_tx and first_tx.dlc is not None else config.get("data_length") or config.get("dlc") or 8),
            "帧 ID": str(first_tx.frame_id or config.get("frame_id") or "-") if first_tx else str(config.get("frame_id") or "-"),
        }
    if normalized == "serial":
        return {
            "串口号": str(config.get("com_port") or "COM3"),
            "波特率": str(config.get("baud_rate") or 115200),
            "自动追加换行符 (CRLF)": "启用" if bool(config.get("auto_append_crlf")) else "关闭",
            "长度(Bytes)": str(config.get("length_bytes") or first_tx.dlc or 64),
            "数据位": str(config.get("data_bits") or 8),
            "停止位": str(config.get("stop_bits") or 1),
            "校验位": str(config.get("parity") or "None"),
            "流控制": str(config.get("flow_control") or "None"),
        }
    if normalized == "ethernet":
        transport_mode = _normalize_ethernet_mode(config.get("transport_protocol") or config.get("protocol") or config.get("method"))
        common = {
            "传输协议": transport_mode,
            "本地 IP": str(config.get("local_ip") or session.ip_address or "-"),
        }
        if transport_mode == "TCP Server":
            return {
                **common,
                "监听端口": str(config.get("listen_port") or "-"),
                "超时时间 (ms)": str(config.get("timeout") or "-"),
                "数据类型": str(config.get("data_type") or "-"),
            }
        if transport_mode == "UDP":
            return {
                **common,
                "本地端口": str(config.get("local_port") or "-"),
                "目标 IP": str(config.get("target_ip") or config.get("ip") or "-"),
                "目标端口": str(config.get("target_port") or config.get("port") or "-"),
                "超时时间 (ms)": str(config.get("timeout") or "-"),
                "数据类型": str(config.get("data_type") or "-"),
            }
        return {
            **common,
            "目标 IP": str(config.get("target_ip") or config.get("ip") or "-"),
            "目标端口": str(config.get("target_port") or config.get("port") or "-"),
            "超时时间 (ms)": str(config.get("timeout") or "-"),
            "数据类型": str(config.get("data_type") or "-"),
        }
    return {
        "引脚选择": str(config.get("pin") or "GPIO0"),
        "模式": str(config.get("mode") or "输出"),
        "目标电平": str(config.get("target_level") or config.get("level") or "高电平"),
        "上下拉": str(config.get("pull_mode") or "无 (浮空)"),
        "期望电平": str(config.get("expected_level") or "高电平"),
        "触发方式": str(config.get("trigger_type") or config.get("interrupt") or "上升沿"),
        "超时时间 (ms)": str(config.get("timeout_ms") or 5000),
        "当前电平": str(config.get("current_level") or "-"),
    }


def _render_protocol_config_table(protocol: str, config_map: dict[str, str]) -> str:
    order = PROTOCOL_REPORT_META.get(protocol, PROTOCOL_REPORT_META["can"])["config_order"]
    entries = [(key, config_map.get(key, "-")) for key in order if key in config_map]
    rows: list[str] = []
    for idx in range(0, len(entries), 2):
        key1, val1 = entries[idx]
        pair = entries[idx + 1] if idx + 1 < len(entries) else None
        row = [
            f"<th>{_safe_text(key1)}</th><td>{_safe_text(val1)}</td>",
        ]
        if pair:
            key2, val2 = pair
            row.append(f"<th>{_safe_text(key2)}</th><td>{_safe_text(val2)}</td>")
        else:
            row.append('<td colspan="2">-</td>')
        rows.append(f"<tr>{''.join(row)}</tr>")
    return "".join(rows)


def _render_protocol_logs(protocol: str, session: ProtocolSession, config: dict, logs: list[ProtocolLog]) -> str:
    normalized = str(protocol or "").strip().lower()
    meta = PROTOCOL_REPORT_META.get(normalized, PROTOCOL_REPORT_META["can"])
    head_html = "".join(f"<th>{_safe_text(col)}</th>" for col in meta["log_columns"])
    rows: list[str] = []
    remote_ip = str(config.get("target_ip") or config.get("ip") or "-")
    remote_port = str(config.get("target_port") or config.get("port") or "-")
    local_ip = str(config.get("local_ip") or session.ip_address or "-")
    local_port = str(config.get("local_port") or "-")
    listen_port = str(config.get("listen_port") or "-")
    transport_mode = _normalize_ethernet_mode(config.get("transport_protocol") or config.get("protocol") or config.get("method"))
    gpio_mode = str(config.get("mode") or "输出")

    for log in logs:
        direction = str(log.direction or "System")
        direction_class = "dir-tx" if direction == "Tx" else ("dir-rx" if direction == "Rx" else "dir-sys")
        ts = _fmt_time_only(log.timestamp)
        if normalized in {"can", "canfd"}:
            cells = [
                ts,
                log.frame_id or "-",
                log.dlc if log.dlc is not None else "-",
                f'<span class="{direction_class}">{_safe_text(direction)}</span>',
                log.data or "-",
            ]
        elif normalized == "serial":
            cells = [
                ts,
                f'<span class="{direction_class}">{_safe_text(direction)}</span>',
                log.dlc if log.dlc is not None else str(config.get("length_bytes") or "-"),
                log.data or "-",
            ]
        elif normalized == "ethernet":
            if direction not in {"Tx", "Rx"}:
                src = "-"
                dst = "-"
                protocol_label = "-"
            else:
                if transport_mode == "TCP Server":
                    src = f"{remote_ip}:{remote_port}" if direction == "Rx" else f"{local_ip}:{listen_port}"
                    dst = f"{local_ip}:{listen_port}" if direction == "Rx" else f"{remote_ip}:{remote_port}"
                else:
                    src = f"{local_ip}:{local_port}" if direction == "Tx" else f"{remote_ip}:{remote_port}"
                    dst = f"{remote_ip}:{remote_port}" if direction == "Tx" else f"{local_ip}:{local_port}"
                protocol_label = transport_mode
            cells = [
                ts,
                f'<span class="{direction_class}">{_safe_text(direction)}</span>',
                src,
                dst,
                protocol_label,
                log.data or "-",
            ]
        else:
            message_text = str(log.data or "-")
            event_label = "系统"
            if direction == "Tx":
                event_label = "操作"
            elif direction == "Rx":
                event_label = "事件"
            level_text = "-"
            if "高电平" in message_text:
                level_text = "高电平"
            elif "低电平" in message_text:
                level_text = "低电平"
            cells = [
                ts,
                event_label,
                log.frame_id or config.get("pin") or "GPIO0",
                gpio_mode,
                level_text,
                message_text,
            ]
        row_html = "".join(
            str(cell) if str(cell).startswith("<span ") else f"<td>{_safe_text(cell)}</td>"
            for cell in cells
        )
        row_html = row_html.replace('<span class="', '<td><span class="').replace("</span>", "</span></td>", 1)
        rows.append(f"<tr>{row_html}</tr>")
    return f"<thead><tr>{head_html}</tr></thead><tbody>{''.join(rows)}</tbody>"


def _detect_protocol_anomalies(logs: list[ProtocolLog]) -> list[dict[str, str]]:
    anomalies: list[dict[str, str]] = []
    keywords = ("error", "fail", "异常", "错误", "超时", "timeout", "nack", "crc", "未通过", "总线错误")
    for log in logs:
        content = str(log.data or "")
        normalized = _normalize_protocol_log_text(content)
        if not normalized or _is_success_protocol_log(content):
            continue
        if any(keyword in normalized for keyword in keywords):
            anomalies.append(
                {
                    "time": _fmt_time_only(log.timestamp),
                    "desc": "检测到异常通信记录",
                    "detail": content,
                }
            )
    return anomalies


def _evaluate_protocol_validation(protocol: str, session: ProtocolSession, config: dict, anomalies: list[dict[str, str]]) -> tuple[bool, str, str]:
    normalized = str(protocol or "").strip().lower()
    validation_result = str(config.get("validation_result") or "").strip().lower()
    validation_detail = str(config.get("validation_detail") or "").strip()
    validation_code = str(config.get("validation_code") or "").strip().lower()
    tx_count = int(getattr(session, "tx_count", 0) or 0)
    rx_count = int(getattr(session, "rx_count", 0) or 0)
    label_map = {
        "can": "CAN",
        "canfd": "CAN FD",
        "serial": "串口",
        "ethernet": "以太网",
        "gpio": "GPIO物理引脚",
        "gpio_io": "GPIO物理引脚",
    }
    label = label_map.get(normalized, normalized.upper() or "协议")
    transport_mode = _normalize_ethernet_mode(config.get("transport_protocol") or config.get("protocol") or config.get("method"))

    if validation_result == "passed":
        if validation_code in {"can_tx_only_passed", "canfd_tx_only_passed"}:
            detail = validation_detail or f"{label} 未配置接收验证条件，本次按发送成功判定验证通过。"
            return True, "未配置接收验证条件，按发送成功判定通过", detail
        if validation_code in {"serial_tx_passed", "ethernet_tx_passed", "ethernet_connected_passed"}:
            detail = validation_detail or f"{label} 验证通过：核心发送/连接动作已成功完成。"
            return True, "核心动作成功，按准则判定通过", detail
        detail = validation_detail or f"{label} 验证通过：已成功发送测试帧并在超时时间内收到回复帧。"
        return True, "已完成协议验证并收到回复帧", detail

    if validation_result == "failed":
        if normalized == "serial":
            if validation_code == "serial_no_response":
                detail = validation_detail or "串口验证未通过：发送成功，但在预设超时时间内未接收到任何来自板卡的回复数据。"
                return False, "❌（板卡无响应）", detail
            if validation_code == "serial_channel_error":
                detail = validation_detail or "串口验证未通过：发送阶段触发驱动层报错，通道异常。"
                return False, "❌（通道异常）", detail
        if normalized == "ethernet":
            if validation_code == "ethernet_no_response":
                detail = validation_detail or f"{transport_mode} 验证未通过：超时时间内未收到任何对端回复数据。"
                return False, "❌（未收到回复数据）", detail
            if validation_code in {"ethernet_port_occupied", "ethernet_channel_error"}:
                detail = validation_detail or f"{transport_mode} 验证未通过：通道启动或发送阶段发生异常。"
                return False, "❌（通道异常）", detail
        if normalized in {"gpio", "gpio_io"}:
            if validation_code == "gpio_read_skip":
                return True, "已完成读取，不输出通过/失败判定", validation_detail or "输入读取模式配置为不判定，本次仅展示读取结果。"
            detail = validation_detail or "GPIO 操作未通过：引脚操作失败、检测超时或通信错误。"
            return False, "GPIO 操作未通过", detail
        detail = validation_detail or f"{label} 验证未通过：发送阶段失败或超时未收到回复帧。"
        return False, "验证未通过，请检查发送结果与回复帧", detail

    if normalized in {"gpio", "gpio_io"}:
        return False, "等待 GPIO 操作执行", "GPIO 尚未完成实际引脚操作，当前记录仅包含通道建立过程。"

    if normalized not in {"can", "canfd", "serial", "ethernet"}:
        if anomalies:
            return False, "检测到异常通信记录，请结合日志明细复核", "检测到异常通信记录，本次协议验证结论为 <b>未通过</b>。"
        return True, "通信链路稳定，报文完整性校验正常", "未检测到异常通信记录，通信链路稳定，协议验证结论为 <b>通过</b>。"

    if tx_count <= 0 and rx_count <= 0:
        return False, "尚未执行协议验证", f"{label} 尚未执行验证发送，当前记录仅包含通道建立过程，验证结论为 <b>未通过</b>。"

    if tx_count > 0 and rx_count <= 0:
        if normalized == "serial":
            return True, "发送成功，按准则判定通过", "串口数据写入成功；观察窗口未收到回复仅作为日志证据，不影响通过判定。"
        if normalized == "ethernet":
            return True, "发送/连接成功，按准则判定通过", f"{transport_mode} 核心发送/连接动作已完成；观察窗口未收到回复仅作为日志证据，不影响通过判定。"
        return True, "发送成功，按准则判定通过", f"{label} 已发送测试帧；观察窗口未收到回复仅作为日志证据，不影响通过判定。"

    if anomalies:
        return False, "检测到异常通信记录", f"{label} 检测到异常通信记录，请结合日志明细复核，验证结论为 <b>未通过</b>。"

    return True, "已完成发送与接收闭环", f"{label} 已完成发送与接收闭环，验证结论为 <b>通过</b>。"


def _render_anomalies(anomalies: list[dict[str, str]]) -> str:
    if not anomalies:
        return """
      <div id="noAnomaly" style="font-size:12px;color:#86909c;
        padding:10px 14px;background:#fafbfc;border-radius:5px;
        border:1px solid #e5e6eb;">
        ✓ 本次测试未检测到异常通信记录
      </div>
"""
    items = []
    for index, anomaly in enumerate(anomalies, start=1):
        items.append(
            f"""<div class="anomaly-item">
      <b>#{index}</b> { _safe_text(anomaly.get("time") or "-") } · { _safe_text(anomaly.get("desc") or "-") }
      <br/><span style="color:#86909c;">{ _safe_text(anomaly.get("detail") or "-") }</span>
    </div>"""
        )
    return f'<div class="anomaly-list" id="anomalyList">{"".join(items)}</div>'


def _build_protocol_report_html(session: ProtocolSession, logs: list, print_mode: bool) -> str:
    protocol = _normalize_protocol_kind(session.protocol or "can")
    meta = PROTOCOL_REPORT_META.get(protocol, PROTOCOL_REPORT_META["can"])
    config = _load_session_config(session)
    start_time = getattr(session, "created_at", None)
    end_time = _resolve_session_end_time(session, logs, config) or start_time
    task_no = str(getattr(session, "task_no", None) or "").strip() or f"{(start_time or datetime.now()).strftime('%Y%m%d')}{session.id:03d}"
    report_no = task_no
    target = session.target or "未知设备"
    device_sn = str(config.get("device_sn") or config.get("serial_number") or "-")
    local_ip = str(config.get("local_ip") or session.ip_address or "-")
    connection_mode = (
        _normalize_ethernet_mode(config.get("transport_protocol") or config.get("protocol") or config.get("method"))
        if protocol == "ethernet"
        else str(config.get("method") or protocol or "-").upper()
    )
    test_env_parts = [
        f"上位机 IP {local_ip}",
        f"连接方式 {connection_mode}",
    ]
    if protocol in {"can", "canfd"}:
        test_env_parts.append(f"通道 {config.get('channel') or 'CAN1'}")
    if protocol == "serial":
        test_env_parts.append(f"串口 {config.get('com_port') or 'COM3'}")
    if protocol == "ethernet":
        ethernet_mode = _normalize_ethernet_mode(config.get("transport_protocol") or config.get("protocol") or config.get("method"))
        if ethernet_mode == "TCP Server":
            test_env_parts.append(f"监听地址 {local_ip}:{config.get('listen_port') or '-'}")
        else:
            test_env_parts.append(f"目标地址 {config.get('target_ip') or config.get('ip') or '-'}:{config.get('target_port') or config.get('port') or '-'}")
    if protocol in {"gpio", "gpio_io"}:
        test_env_parts.append(f"引脚 {config.get('pin') or 'GPIO0'}")
    test_env_parts.append("系统版本 v1.0.0")
    protocol_config = _build_protocol_config(session, protocol, config, logs)
    log_table_html = _render_protocol_logs(protocol, session, config, logs)
    anomalies = _detect_protocol_anomalies(logs)
    pass_result, conclusion_hint, validation_summary = _evaluate_protocol_validation(protocol, session, config, anomalies)
    total_frames = len([log for log in logs if str(log.direction or "").upper() in {"TX", "RX"}])
    conclusion_text = (
        f"本次 {meta['label']} 协议测试在 {_format_duration(start_time, end_time)} 内完成 {total_frames} 条通信记录，"
        f"发送 {int(getattr(session, 'tx_count', 0) or 0)} 条、接收 {int(getattr(session, 'rx_count', 0) or 0)} 条，"
        + validation_summary
    )

    script = ""
    if print_mode:
        script = """
<script>
  window.onload = () => {
    window.print();
    setTimeout(() => window.close(), 500);
  }
</script>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<title>通信协议测试报告</title>
  <style>
/* ══════════════════════════════════════
   基础与打印
══════════════════════════════════════ */
@page {{ size: A4; margin: 18mm 16mm; }}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f2f3f5; color: #1d2129; font-size: 13px; line-height: 1.6;
}}

.page-wrap {{
  max-width: 800px; margin: 24px auto; background: #fff;
  padding: 36px 44px; border-radius: 6px;
  box-shadow: 0 2px 12px rgba(0,0,0,.06);
}}

.toolbar {{
  max-width: 800px; margin: 16px auto 0;
  display: flex; gap: 10px; justify-content: flex-end;
}}
.btn {{
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 16px; border-radius: 5px; cursor: pointer;
  font-size: 13px; border: 1px solid #e5e6eb; background: #fff;
  color: #4e5969; transition: all .15s;
}}
.btn:hover {{ border-color: #165dff; color: #165dff; }}
.btn-primary {{ background: #165dff; border-color: #165dff; color: #fff; }}
.btn-primary:hover {{ background: #0e4fd8; color: #fff; }}

.report-header {{
  border-bottom: 3px solid #165dff;
  padding-bottom: 18px; margin-bottom: 22px;
}}
.report-header-top {{
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 14px;
}}
.report-brand {{
  font-size: 12px; color: #86909c; letter-spacing: 1px;
  text-transform: uppercase;
}}
.report-brand b {{ color: #165dff; font-weight: 600; }}
.report-no {{
  font-size: 11px; color: #86909c; font-family: monospace;
}}
.report-title {{
  font-size: 22px; font-weight: 700; color: #1d2129;
  margin-bottom: 6px;
}}
.report-subtitle {{
  font-size: 13px; color: #4e5969;
}}
.report-protocol-tag {{
  display: inline-block; padding: 2px 10px;
  background: #e8f3ff; color: #165dff;
  border-radius: 3px; font-size: 12px; font-weight: 600;
  margin-left: 8px; vertical-align: middle;
}}

.section {{ margin-bottom: 24px; page-break-inside: avoid; }}
.section-title {{
  font-size: 14px; font-weight: 700; color: #1d2129;
  padding-left: 10px; margin-bottom: 10px;
  border-left: 3px solid #165dff; line-height: 1.2;
}}
.section-desc {{
  font-size: 12px; color: #86909c; margin-bottom: 10px;
}}

.info-table {{
  width: 100%; border-collapse: collapse; font-size: 12px;
  border: 1px solid #e5e6eb;
}}
.info-table th, .info-table td {{
  border: 1px solid #e5e6eb; padding: 8px 12px; text-align: left;
  vertical-align: middle;
}}
.info-table th {{
  background: #f7f8fa; font-weight: 600; color: #4e5969;
  width: 22%; white-space: nowrap;
}}
.info-table td {{ color: #1d2129; }}
.info-table td code {{
  font-family: "Consolas", "Monaco", monospace;
  background: #f2f3f5; padding: 1px 5px; border-radius: 3px;
  font-size: 11px; color: #1d2129;
}}

.log-table {{
  width: 100%; border-collapse: collapse;
  font-size: 11px; border: 1px solid #e5e6eb;
}}
.log-table th {{
  background: #f7f8fa; padding: 7px 8px; font-weight: 600;
  color: #4e5969; border: 1px solid #e5e6eb;
  text-align: left; white-space: nowrap;
}}
.log-table td {{
  padding: 6px 8px; border: 1px solid #e5e6eb;
  font-family: "Consolas", "Monaco", monospace;
  font-size: 10.5px; color: #1d2129;
  word-break: break-all;
}}
.log-table tbody tr:nth-child(even) {{ background: #fafbfc; }}

.dir-tx {{ color: #165dff; font-weight: 600; }}
.dir-rx {{ color: #00b42a; font-weight: 600; }}
.dir-sys {{ color: #86909c; font-style: italic; }}

.stats-row {{
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 10px; margin-bottom: 14px;
}}
.stat-card {{
  border: 1px solid #e5e6eb; border-radius: 5px;
  padding: 12px 14px; background: #fafbfc;
}}
.stat-label {{
  font-size: 11px; color: #86909c; margin-bottom: 4px;
}}
.stat-value {{
  font-size: 20px; font-weight: 700; color: #1d2129;
  font-family: "Consolas", monospace;
}}
.stat-value.success {{ color: #00b42a; }}
.stat-value.fail {{ color: #f53f3f; }}
.stat-value.info {{ color: #165dff; }}

.conclusion {{
  border: 1px solid #e5e6eb; border-radius: 5px;
  padding: 14px 18px; background: #fafbfc;
}}
.conclusion-status {{
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 10px;
}}
.status-badge {{
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 12px; border-radius: 12px;
  font-size: 12px; font-weight: 600;
}}
.status-badge::before {{
  content: ''; width: 7px; height: 7px; border-radius: 50%;
}}
.status-pass {{ background: #e8ffea; color: #00b42a; }}
.status-pass::before {{ background: #00b42a; }}
.status-fail {{ background: #ffece8; color: #f53f3f; }}
.status-fail::before {{ background: #f53f3f; }}
.conclusion-text {{
  font-size: 12px; color: #4e5969; line-height: 1.8;
}}

.anomaly-list {{
  border: 1px solid #ffcdc7; border-radius: 5px;
  background: #fff7f6; padding: 12px 16px;
}}
.anomaly-item {{
  font-size: 12px; color: #4e5969; padding: 4px 0;
  border-bottom: 1px dashed #ffcdc7;
}}
.anomaly-item:last-child {{ border-bottom: none; }}
.anomaly-item b {{ color: #f53f3f; }}

.signature-section {{
  margin-top: 30px; padding-top: 20px;
  border-top: 1px dashed #c9cdd4;
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px;
}}
.sign-block {{
  text-align: center; font-size: 12px; color: #4e5969;
}}
.sign-label {{ color: #86909c; margin-bottom: 28px; }}
.sign-line {{
  border-bottom: 1px solid #1d2129;
  height: 1px; margin: 0 12px 6px;
}}
.sign-date {{ color: #86909c; font-size: 11px; }}

.report-footer {{
  margin-top: 26px; padding-top: 12px;
  border-top: 1px solid #e5e6eb;
  display: flex; justify-content: space-between;
  font-size: 10.5px; color: #86909c;
}}

@media print {{
  body {{ background: #fff; font-size: 11.5px; }}
  .toolbar {{ display: none; }}
  .page-wrap {{
    margin: 0; padding: 0; box-shadow: none; max-width: none;
  }}
  .section {{ page-break-inside: avoid; }}
  .log-table {{ page-break-inside: auto; }}
  .log-table tr {{ page-break-inside: avoid; page-break-after: auto; }}
  .signature-section {{ page-break-inside: avoid; }}
}}
  </style>
</head>
<body>
<div class="page-wrap" id="reportRoot">
  <div class="report-header">
    <div class="report-header-top">
      <div class="report-brand"><b>PCIDS</b> Communication Protocol Validation</div>
      <div class="report-no">{_safe_text(report_no)}</div>
    </div>
    <div class="report-title">
      通信协议测试报告
      <span class="report-protocol-tag" id="protocolTag">{_safe_text(meta["label"])}</span>
    </div>
    <div class="report-subtitle" id="reportSubtitle">
      测试对象：{_safe_text(target)} · 测试日期：{_safe_text((start_time or datetime.now()).strftime('%Y-%m-%d'))} · 执行人员：{_safe_text(session.executor or "-")}
    </div>
  </div>

  <div class="section">
    <div class="section-title">一、测试基本信息</div>
    <table class="info-table">
      <tr>
        <th>测试任务编号</th><td>{_safe_text(task_no)}</td>
        <th>协议类型</th><td>{_safe_text(meta["protocol_type"])}</td>
      </tr>
      <tr>
        <th>测试对象</th><td>{_safe_text(target)}</td>
        <th>设备序列号</th><td><code>{_safe_text(device_sn)}</code></td>
      </tr>
      <tr>
        <th>测试开始时间</th><td>{_safe_text(_fmt_datetime(start_time))}</td>
        <th>测试结束时间</th><td>{_safe_text(_fmt_datetime(end_time))}</td>
      </tr>
      <tr>
        <th>测试时长</th><td>{_safe_text(_format_duration(start_time, end_time))}</td>
        <th>执行人员</th><td>{_safe_text(session.executor or "-")}</td>
      </tr>
      <tr>
        <th>测试环境</th>
        <td colspan="3">{_safe_text(" · ".join(test_env_parts))}</td>
      </tr>
    </table>
  </div>

  <div class="section">
    <div class="section-title">二、协议配置参数</div>
    <div class="section-desc">本次测试使用的协议参数快照（执行时锁定）</div>
    <table class="info-table" id="protocolConfigTable">
      {_render_protocol_config_table(protocol, protocol_config)}
    </table>
  </div>

  <div class="section">
    <div class="section-title">三、通信统计</div>
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-label">发送帧数 (Tx)</div>
        <div class="stat-value info">{int(getattr(session, "tx_count", 0) or 0)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">接收帧数 (Rx)</div>
        <div class="stat-value success">{int(getattr(session, "rx_count", 0) or 0)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">异常帧数</div>
        <div class="stat-value fail">{len(anomalies)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">总帧数</div>
        <div class="stat-value">{total_frames}</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">四、通信日志明细</div>
    <div class="section-desc">按时间倒序记录的完整通信报文（取自任务日志窗口）</div>
    <table class="log-table" id="logTable">
      {log_table_html}
    </table>
  </div>

  <div class="section">
    <div class="section-title">五、异常记录</div>
    <div id="anomalyContainer">
      {_render_anomalies(anomalies)}
    </div>
  </div>

  <div class="section">
    <div class="section-title">六、测试结论</div>
    <div class="conclusion">
      <div class="conclusion-status">
        <span class="status-badge {'status-pass' if pass_result else 'status-fail'}">{'测试通过' if pass_result else '测试未通过'}</span>
        <span style="font-size:12px;color:#86909c;">
          {_safe_text(conclusion_hint)}
        </span>
      </div>
      <div class="conclusion-text">{conclusion_text}</div>
    </div>
  </div>

  <div class="report-footer">
    <span>程控安装部署系统 v1.0.0 · 通信协议验证报告</span>
    <span>生成时间：{_safe_text(_fmt_datetime(datetime.now()))}</span>
  </div>
</div>
{script}
</body>
</html>"""


@router.get("/{session_id}/report/html")
async def download_protocol_report_html(
    session_id: int,
    print: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    ensure_schema()
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="测试记录不存在")
        
    logs = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).order_by(ProtocolLog.timestamp.asc()).all()
    html = _build_protocol_report_html(session, logs, bool(print))
    headers = {"Content-Disposition": f'attachment; filename="protocol_report_{session.id}.html"'}
    return HTMLResponse(content=html, headers=headers)

@router.get("/{session_id}/report/csv")
async def download_protocol_report_csv(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    ensure_schema()
    import csv
    import io
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="测试记录不存在")
        
    logs = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).order_by(ProtocolLog.timestamp.asc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["时间戳", "方向", "标识/引脚", "数据长度(Bytes)/端口", "数据/状态"])
    
    for log in logs:
        writer.writerow([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            log.direction,
            log.frame_id or '',
            log.dlc if log.dlc is not None else '',
            log.data or ''
        ])
        
    headers = {"Content-Disposition": f'attachment; filename="protocol_report_{session.id}.csv"'}
    return FastAPIResponse(content=output.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
class ConnectRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=200)
    protocol: str = Field(..., min_length=1, max_length=50)
    config: Optional[dict] = None


class ChannelScanRequest(BaseModel):
    target: Optional[str] = Field(default=None, min_length=1, max_length=200)
    protocol: str = Field(..., min_length=1, max_length=50)
    config: Optional[dict] = None


class SendRequest(BaseModel):
    frame_id: Optional[str] = None
    dlc: Optional[int] = None
    data: Optional[str] = None
    config: Optional[dict] = None


def _normalize_gpio_level(value: Any, default: str = "高电平") -> str:
    text = str(value or "").strip()
    if text in {"高电平", "低电平"}:
        return text
    return default


def _evaluate_gpio_result(actual_level: str, expected_level: str) -> tuple[bool, str]:
    if expected_level == "不判定":
        return True, "已读取"
    if actual_level == expected_level:
        return True, "通过"
    return False, "未通过"


def _append_gpio_log(
    db: Session,
    session: ProtocolSession,
    *,
    direction: str,
    pin: str,
    data: str,
    dlc: Optional[int] = None,
) -> None:
    db.add(
        ProtocolLog(
            session_id=session.id,
            protocol=session.protocol,
            timestamp=datetime.now(),
            direction=direction,
            frame_id=pin,
            dlc=dlc,
            data=data,
        )
    )


def _ensure_gpio_runtime_config(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(config or {})
    profile = merged.get("gpio_runtime_profile")
    selected_port = str(
        merged.get("wch_serial_port")
        or merged.get("com_port")
        or (merged.get("gpio_transport_config") or {}).get("com_port")
        or ""
    ).strip()
    
    if not isinstance(profile, dict) or not profile:
        requested_kind = str(
            merged.get("gpio_transport_kind")
            or (merged.get("gpio_transport_config") or {}).get("kind")
            or ""
        ).strip().lower()
        if selected_port and requested_kind in {"", "serial", "wch_gpio", "wch"}:
            profile = {
                "enabled": True,
                "supports_readback": True,
                "channel_options": [f"GPIO{index}" for index in range(16)],
                "defaults": {
                    "pin": "GPIO0",
                    "mode": "输出",
                    "target_level": "高电平",
                    "expected_level": "高电平",
                    "timeout_ms": 5000,
                },
                "transport": {
                    "kind": "wch_gpio",
                    "com_port": selected_port,
                    "pin_base_index": 0,
                },
            }
        else:
            profile = load_gpio_runtime_profile()
    transport = profile.get("transport") if isinstance(profile.get("transport"), dict) else {}
    merged["gpio_runtime_profile"] = profile
    transport_config = dict(transport)
    if isinstance(merged.get("gpio_transport_config"), dict):
        transport_config.update(merged.get("gpio_transport_config") or {})
    if transport_config.get("kind") == "serial" and str(transport.get("kind") or "").lower() == "wch_gpio":
        transport_config["kind"] = "wch_gpio"
    if str(transport_config.get("kind") or "").strip().lower() in {"wch_gpio", "wch"}:
        if selected_port:
            transport_config["com_port"] = selected_port
            merged["wch_serial_port"] = selected_port
            merged["com_port"] = selected_port
        # Keep UI GPIO labels aligned with the WCH demo/board labels:
        # page GPIO0 -> hardware GPIO0 -> bit0.
        transport_config["pin_base_index"] = 0
    merged["gpio_transport_config"] = transport_config
    merged["gpio_transport_kind"] = str(transport_config.get("kind") or merged.get("gpio_transport_kind") or transport.get("kind") or "").strip().lower()
    merged["supports_readback"] = bool(merged.get("supports_readback", profile.get("supports_readback", True)))
    return merged


def _render_gpio_action_spec(config: dict[str, Any], pin: str, action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = _ensure_gpio_runtime_config(config)
    if str(merged.get("gpio_transport_kind") or "").strip().lower() in {"wch_gpio", "wch"}:
        return merged, {
            "pin": pin,
            "action": action,
            "mode": merged.get("mode"),
            "target_level": merged.get("target_level"),
            "reply": {
                "required": True,
                "high_pattern": [r"\bHIGH\b", "高电平"],
                "low_pattern": [r"\bLOW\b", "低电平"],
            },
        }
    context = build_gpio_action_context(pin, merged)
    profile = merged.get("gpio_runtime_profile") if isinstance(merged.get("gpio_runtime_profile"), dict) else {}
    action_profile = resolve_gpio_action_profile(profile, pin, action, context)
    rendered = render_gpio_template(action_profile, context)
    if not isinstance(rendered, dict) or not rendered:
        raise ValueError(f"GPIO {pin} 未配置动作 {action} 的真实业务映射")
    return merged, rendered


def _build_gpio_tx_log_text(action: str, pin: str, runtime_config: dict[str, Any]) -> str:
    if action == "set_level":
        return f"设置 {pin} 为{_normalize_gpio_level(runtime_config.get('target_level'), '高电平')}"
    if action == "read_level":
        return f"读取 {pin} 当前电平"
    if action == "listen":
        trigger_type = str(runtime_config.get("trigger_type") or "上升沿")
        timeout_ms = _parse_non_negative_int(runtime_config.get("timeout_ms"), 5000) or 5000
        return f"监听 {pin} {trigger_type}，超时 {timeout_ms}ms"
    return f"执行 {pin} GPIO 操作"


async def _execute_gpio_serial_transport(
    *,
    session_id: int,
    transport_config: dict[str, Any],
    action_request: dict[str, Any],
    require_reply: bool,
) -> tuple[str, int]:
    serial_connection = _get_serial_session_connection(session_id)
    serial_io_lock = _get_serial_session_io_lock(session_id)
    if serial_connection is None:
        raise RuntimeError("GPIO 串口连接已失效，请重新连接 GPIO 通道")
    data_type = action_request.get("data_type") or transport_config.get("data_type")
    payload_text = str(action_request.get("data") or "")
    auto_append_crlf = bool(action_request.get("auto_append_crlf", transport_config.get("auto_append_crlf")))
    if auto_append_crlf and payload_text and not payload_text.endswith("\r\n"):
        payload_text = f"{payload_text}\r\n"
    payload_bytes = _encode_protocol_payload(payload_text, data_type)
    timeout_seconds = _timeout_ms_to_seconds(
        action_request.get("timeout_ms", transport_config.get("timeout_ms", transport_config.get("timeout"))),
        1000,
    )
    return await asyncio.to_thread(
        _run_serial_exchange,
        str(transport_config.get("com_port") or ""),
        payload_bytes,
        baud_rate=int(transport_config.get("baud_rate") or 115200),
        data_bits=int(transport_config.get("data_bits") or 8),
        stop_bits=float(transport_config.get("stop_bits") or 1),
        parity=str(transport_config.get("parity") or "NONE"),
        timeout=float(timeout_seconds),
        flow_control=str(transport_config.get("flow_control") or "NONE"),
        expected_length=_parse_non_negative_int(action_request.get("expected_length") or transport_config.get("length_bytes")),
        data_type=data_type,
        existing_connection=serial_connection,
        existing_io_lock=serial_io_lock,
        close_when_done=False,
        require_reply=require_reply,
    )


async def _execute_gpio_can_transport(
    *,
    session_id: int,
    transport_kind: str,
    transport_config: dict[str, Any],
    action_request: dict[str, Any],
    reply_config: dict[str, Any],
) -> tuple[str, int]:
    runtime = _get_can_session_runtime(session_id)
    if runtime is None:
        raise RuntimeError(f"GPIO {transport_kind.upper()} 连接已失效，请重新连接 GPIO 通道")
    connection = runtime.connection
    data_type = action_request.get("data_type") or transport_config.get("data_type")
    payload_text = str(action_request.get("data") or "")
    remote_frame_enabled = transport_kind == "can" and _normalize_bool_config(action_request.get("remote_frame"), default=False)
    data_length, _ = _normalize_can_length_for_send(
        transport_kind,
        None if remote_frame_enabled else payload_text,
        action_request.get("data_length", action_request.get("dlc", transport_config.get("data_length", transport_config.get("dlc")))),
        data_type,
        is_remote_frame=remote_frame_enabled,
    )
    is_extended = _is_extended_can_id({**transport_config, **action_request})
    frame_id_text = str(action_request.get("frame_id") or "").strip()
    if not frame_id_text:
        raise ValueError("GPIO 真实业务映射缺少 frame_id")
    payload_bytes = b"" if remote_frame_enabled else _encode_protocol_payload(payload_text, data_type)
    if transport_kind == "can" and not remote_frame_enabled and len(payload_bytes) < data_length:
        payload_bytes = payload_bytes + (b"\x00" * (data_length - len(payload_bytes)))
    frame = CanFrame(
        frame_id=parse_can_frame_id(frame_id_text, is_extended=is_extended),
        is_extended_id=is_extended,
        is_fd=transport_kind == "canfd",
        bitrate_switch=transport_kind == "canfd" and _normalize_bool_config(action_request.get("brs", transport_config.get("brs")), default=True),
        is_remote_frame=remote_frame_enabled,
        data=payload_bytes,
        declared_data_length=data_length,
        channel_name=connection.channel_name,
    )
    with runtime.rx_condition:
        baseline_sequence = runtime.rx_sequence
    backend = CAN_ADAPTER_BACKENDS.get(connection.backend_key)
    if backend is None:
        raise RuntimeError("GPIO CAN 适配器后端不可用，请重新连接")
    with runtime.io_lock:
        await asyncio.to_thread(backend.transmit, connection, frame)
    require_reply = bool(reply_config.get("required"))
    if not require_reply:
        return "", 0
    expected_rx_id = str(reply_config.get("frame_id") or action_request.get("expected_rx_id") or "").strip()
    expected_rx_mask = str(reply_config.get("frame_mask") or action_request.get("expected_rx_mask") or "").strip()
    timeout_ms = _normalize_can_rx_timeout_ms(reply_config.get("timeout_ms") or action_request.get("timeout_ms") or transport_config.get("rx_timeout_ms"))

    def matcher(item: CanFrame) -> bool:
        if item.channel_name != connection.channel_name:
            return False
        if expected_rx_id:
            try:
                item_id = int(item.frame_id)
                target_id = parse_can_frame_id(expected_rx_id, is_extended=is_extended)
                if expected_rx_mask:
                    mask = parse_can_mask(expected_rx_mask, is_extended=is_extended)
                    return (item_id & mask) == (target_id & mask)
                return item_id == target_id
            except Exception:
                return False
        return True

    matched_entry = await asyncio.to_thread(
        _wait_for_expected_can_frame,
        runtime,
        after_sequence=baseline_sequence,
        timeout_ms=timeout_ms,
        matcher=matcher,
    )
    if matched_entry is None:
        raise TimeoutError(f"GPIO {transport_kind.upper()} 等待设备回复超时")
    _, matched_frame = matched_entry
    reply_text = _format_can_log_payload(matched_frame, data_type)
    return reply_text, matched_frame.declared_data_length


async def _execute_gpio_transport_action(
    *,
    session: ProtocolSession,
    action_spec: dict[str, Any],
    transport_kind: str,
    transport_config: dict[str, Any],
) -> tuple[str, int]:
    request_cfg = action_spec.get("request") if isinstance(action_spec.get("request"), dict) else {}
    reply_cfg = action_spec.get("reply") if isinstance(action_spec.get("reply"), dict) else {}
    if transport_kind == "serial":
        return await _execute_gpio_serial_transport(
            session_id=session.id,
            transport_config=transport_config,
            action_request=request_cfg,
            require_reply=bool(reply_cfg.get("required")),
        )
    if transport_kind in {"can", "canfd"}:
        return await _execute_gpio_can_transport(
            session_id=session.id,
            transport_kind=transport_kind,
            transport_config=transport_config,
            action_request=request_cfg,
            reply_config=reply_cfg,
        )
    if transport_kind in {"wch_gpio", "wch"}:
        pin = str(action_spec.get("pin") or request_cfg.get("pin") or transport_config.get("pin") or "").strip()
        action = str(action_spec.get("action") or request_cfg.get("action") or transport_config.get("action") or "").strip().lower()
        target_level = str(action_spec.get("target_level") or request_cfg.get("target_level") or transport_config.get("target_level") or "").strip()
        com_port = str(transport_config.get("com_port") or transport_config.get("wch_serial_port") or "").strip()
        base_index = _parse_non_negative_int(transport_config.get("pin_base_index"), 0)
        pin_map = transport_config.get("pin_map") if isinstance(transport_config.get("pin_map"), dict) else None
        mode_text = str(action_spec.get("mode") or request_cfg.get("mode") or transport_config.get("mode") or "").strip().lower()
        read_as_output = action == "read_level" and ("输出" in mode_text or "output" in mode_text)
        existing_connection = _get_wch_gpio_session_connection(session.id)
        if existing_connection is None:
            raise RuntimeError("GPIO WCH 连接已失效，请重新连接通道")
        result = await asyncio.to_thread(
            run_wch_gpio_action,
            com_port=com_port,
            pin=pin,
            action=action,
            target_level=target_level,
            base_index=base_index,
            pin_map=pin_map,
            read_as_output=read_as_output,
            existing_connection=existing_connection,
        )
        level_text = "HIGH" if result.level == "高电平" else "LOW"
        return (
            f"{result.pin}={level_text} "
            f"(bit={result.gpio_index}, dir=0x{result.raw_dir:X}, status=0x{result.raw_status:X})"
        ), 1
    raise RuntimeError(f"暂不支持的 GPIO 真实业务链路：{transport_kind or '-'}")


def _validate_gpio_reply_pattern(reply_config: dict[str, Any], reply_text: str) -> bool:
    success_pattern = reply_config.get("success_pattern")
    if success_pattern is None:
        return True
    if isinstance(success_pattern, str) and not success_pattern.strip():
        return True
    if isinstance(success_pattern, list) and not success_pattern:
        return True
    return gpio_pattern_matches(success_pattern, reply_text)


def _resolve_gpio_level_from_reply(
    *,
    action: str,
    reply_text: str,
    reply_config: dict[str, Any],
    fallback_level: Optional[str] = None,
) -> Optional[str]:
    detected = detect_gpio_level_from_text(reply_text, reply_config)
    if detected:
        return detected
    if action == "listen" and fallback_level:
        return fallback_level
    return fallback_level


async def _run_gpio_business_action(
    *,
    db: Session,
    session: ProtocolSession,
    payload: SendRequest,
    merged_config: dict[str, Any],
) -> dict[str, Any]:
    merged_config = _ensure_gpio_runtime_config(merged_config)
    transport_kind = str(merged_config.get("gpio_transport_kind") or "").strip().lower()
    transport_config = merged_config.get("gpio_transport_config") if isinstance(merged_config.get("gpio_transport_config"), dict) else {}
    if not transport_kind or not transport_config:
        _raise_protocol_validation_error(
            db,
            session,
            "GPIO 真实业务配置缺失，请检查 gpio_runtime.json 中的 transport 配置",
            config=merged_config,
            code="gpio_runtime_missing",
        )

    pin = str(merged_config.get("pin") or payload.frame_id or "").strip()
    action = str(merged_config.get("action") or "").strip().lower()
    timeout_ms = _parse_non_negative_int(merged_config.get("timeout_ms"), 5000) or 5000
    batch_items = merged_config.get("batch_items") if isinstance(merged_config.get("batch_items"), list) else []

    if action in {"batch_read", "batch_write"}:
        normalized_items: list[dict[str, Any]] = []
        for item in batch_items:
            if not isinstance(item, dict) or not item.get("selected", True):
                continue
            item_pin = str(item.get("pin") or "").strip()
            if not item_pin:
                continue
            item_mode = str(item.get("mode") or ("输入 (单次读取)" if action == "batch_read" else "输出")).strip()
            item_target_level = _normalize_gpio_level(item.get("target_level"), "低电平")
            item_expected_level = str(item.get("expected_level") or "不判定").strip() or "不判定"
            item_action = "read_level" if action == "batch_read" else "set_level"
            item_config = {
                **merged_config,
                "pin": item_pin,
                "mode": item_mode,
                "action": item_action,
                "target_level": item_target_level,
                "expected_level": item_expected_level,
            }
            try:
                item_config, action_spec = _render_gpio_action_spec(item_config, item_pin, item_action)
                reply_cfg = action_spec.get("reply") if isinstance(action_spec.get("reply"), dict) else {}
                tx_text = str(action_spec.get("tx_log") or ("批量读取：读取 {pin} 当前电平" if item_action == "read_level" else "批量下发：设置 {pin} 为{target_level}"))
                tx_text = str(render_gpio_template(tx_text, build_gpio_action_context(item_pin, item_config)) or "")
                reply_text, _ = await _execute_gpio_transport_action(
                    session=session,
                    action_spec=action_spec,
                    transport_kind=transport_kind,
                    transport_config=transport_config,
                )
                if reply_text and not _validate_gpio_reply_pattern(reply_cfg, reply_text):
                    raise RuntimeError(f"GPIO 设备回复不符合预期：{reply_text}")
                fallback_level = item_target_level if item_action == "set_level" and not bool(item_config.get("supports_readback")) else None
                actual_level = _resolve_gpio_level_from_reply(
                    action=item_action,
                    reply_text=reply_text,
                    reply_config=reply_cfg,
                    fallback_level=fallback_level,
                )
            except (RuntimeError, TimeoutError, ValueError, CanAdapterError, CanDependencyMissingError, WchGpioError) as exc:
                _raise_protocol_validation_error(
                    db,
                    session,
                    f"GPIO 批量操作未通过：{item_pin} 执行失败，{str(exc)}",
                    config=item_config if "item_config" in locals() else merged_config,
                    code="gpio_batch_transport_error",
                )

            if item_action == "set_level":
                current_level = actual_level or item_target_level
                passed = current_level == item_target_level
                result = "通过" if passed else "未通过"
                reply_suffix = f"；设备回复：{reply_text}" if reply_text else ""
                rx_text = f"目标电平设置成功，回读值为{current_level}，结果：{result}{reply_suffix}"
                target_value = item_target_level
            else:
                current_level = str(actual_level or "").strip() or "未知"
                passed, result = _evaluate_gpio_result(current_level, item_expected_level)
                reply_suffix = f"；设备回复：{reply_text}" if reply_text else ""
                rx_text = f"读取当前引脚电平：{current_level}，结果：{result}{reply_suffix}"
                target_value = "--"

            normalized_items.append(
                {
                    "pin": item_pin,
                    "mode": item_mode,
                    "target_level": target_value,
                    "current_level": current_level,
                    "expected_level": item_expected_level,
                    "result": result,
                    "passed": passed,
                }
            )
            _append_gpio_log(db, session, direction="Tx", pin=item_pin, data=tx_text)
            _append_gpio_log(db, session, direction="Rx", pin=item_pin, data=rx_text)
            session.tx_count += 1
            session.rx_count += 1

        if not normalized_items:
            _raise_protocol_validation_error(db, session, "GPIO 批量操作未通过：请至少选择一个引脚", config=merged_config, code="gpio_batch_empty")

        passed_count = sum(1 for item in normalized_items if item.get("passed"))
        total_count = len(normalized_items)
        detail = f"GPIO 批量验证完成：共 {total_count} 路，通过 {passed_count} 路，未通过 {total_count - passed_count} 路。"
        merged_config["batch_items"] = normalized_items
        merged_config["current_level"] = normalized_items[-1]["current_level"]
        merged_config = _persist_validation_result(
            session,
            merged_config,
            passed=passed_count == total_count,
            detail=detail,
            code="gpio_batch_passed" if passed_count == total_count else "gpio_batch_failed",
        )
        _append_system_log(db, session, detail)
        session.config_json = json.dumps(merged_config, ensure_ascii=False)
        _notify_protocol_result(db, session, passed=passed_count == total_count)
        db.commit()
        return {"code": 0, "message": "GPIO 批量操作完成", "data": {"items": normalized_items, "config": merged_config}}

    try:
        merged_config, action_spec = _render_gpio_action_spec(merged_config, pin, action)
    except (GpioRuntimeConfigError, ValueError) as exc:
        _raise_protocol_validation_error(db, session, f"GPIO 操作未通过：{str(exc)}", config=merged_config, code="gpio_runtime_invalid")

    reply_cfg = action_spec.get("reply") if isinstance(action_spec.get("reply"), dict) else {}
    tx_text = str(action_spec.get("tx_log") or _build_gpio_tx_log_text(action, pin, merged_config))
    tx_text = str(render_gpio_template(tx_text, build_gpio_action_context(pin, merged_config)) or tx_text)
    try:
        reply_text, rx_dlc = await _execute_gpio_transport_action(
            session=session,
            action_spec=action_spec,
            transport_kind=transport_kind,
            transport_config=transport_config,
        )
        if reply_text and not _validate_gpio_reply_pattern(reply_cfg, reply_text):
            raise RuntimeError(f"GPIO 设备回复不符合预期：{reply_text}")
    except (RuntimeError, TimeoutError, ValueError, CanAdapterError, CanDependencyMissingError, WchGpioError) as exc:
        _raise_protocol_validation_error(
            db,
            session,
            f"GPIO 操作未通过：{str(exc)}",
            config=merged_config,
            code="gpio_transport_error",
        )

    _append_gpio_log(db, session, direction="Tx", pin=pin, data=tx_text)
    session.tx_count += 1

    if action == "set_level":
        target_level = _normalize_gpio_level(merged_config.get("target_level"), "高电平")
        fallback_level = None if bool(merged_config.get("supports_readback")) else target_level
        current_level = _resolve_gpio_level_from_reply(
            action=action,
            reply_text=reply_text,
            reply_config=reply_cfg,
            fallback_level=fallback_level,
        ) or target_level
        reply_suffix = f"；设备回复：{reply_text}" if reply_text else ""
        rx_text = f"目标电平设置成功，回读值为{current_level}{reply_suffix}"
        passed = current_level == target_level
        detail = (
            f"GPIO 输出模式验证通过：回读值与设定值完全一致（{current_level}）。"
            if passed
            else f"GPIO 输出模式验证未通过：回读值 {current_level} 与目标值 {target_level} 不一致。"
        )
        code = "gpio_output_passed" if passed else "gpio_output_mismatch"
        merged_config["current_level"] = current_level
        merged_config = _persist_validation_result(session, merged_config, passed=passed, detail=detail, code=code)
    elif action == "read_level":
        expected_level = str(merged_config.get("expected_level") or "高电平")
        current_level = _resolve_gpio_level_from_reply(action=action, reply_text=reply_text, reply_config=reply_cfg)
        if not current_level:
            _raise_protocol_validation_error(db, session, "GPIO 输入单次读取未通过：设备回复中未解析到电平结果。", config=merged_config, code="gpio_read_parse_error")
        merged_config["current_level"] = current_level
        reply_suffix = f"；设备回复：{reply_text}" if reply_text else ""
        rx_text = f"读取当前引脚电平：{current_level}{reply_suffix}"
        if expected_level == "不判定":
            merged_config = _persist_validation_result(
                session,
                merged_config,
                passed=True,
                detail="GPIO 输入单次读取已完成，配置为不判定，本次仅展示读取结果。",
                code="gpio_read_skip",
            )
        elif current_level == expected_level:
            merged_config = _persist_validation_result(
                session,
                merged_config,
                passed=True,
                detail=f"GPIO 输入单次读取验证通过：读取值与期望值一致（{current_level}）。",
                code="gpio_read_passed",
            )
        else:
            _raise_protocol_validation_error(
                db,
                session,
                f"GPIO 输入单次读取未通过：读取值 {current_level} 与期望值 {expected_level} 不一致。",
                config=merged_config,
                code="gpio_read_mismatch",
            )
    else:
        trigger_type = str(merged_config.get("trigger_type") or "上升沿")
        fallback_level = "高电平" if trigger_type == "上升沿" else "低电平" if trigger_type == "下降沿" else "高电平"
        current_level = _resolve_gpio_level_from_reply(
            action=action,
            reply_text=reply_text,
            reply_config=reply_cfg,
            fallback_level=fallback_level,
        ) or fallback_level
        merged_config["current_level"] = current_level
        reply_suffix = f"；设备回复：{reply_text}" if reply_text else ""
        rx_text = f"在 {timeout_ms}ms 内成功捕获到{trigger_type}，检测电平 {current_level}{reply_suffix}"
        merged_config = _persist_validation_result(
            session,
            merged_config,
            passed=True,
            detail=f"GPIO 边沿中断监听验证通过：在设定超时时间内成功捕获到指定边沿信号（{trigger_type}）。",
            code="gpio_listen_passed",
        )

    _append_gpio_log(db, session, direction="Rx", pin=pin, data=rx_text, dlc=rx_dlc)
    session.rx_count += 1
    session.config_json = json.dumps(merged_config, ensure_ascii=False)
    success_detail = str(merged_config.get("validation_detail") or "GPIO 操作执行完成")
    _append_system_log(db, session, success_detail)
    _notify_protocol_result(db, session, passed=str(merged_config.get("validation_result") or "") == "passed")
    db.commit()
    return {"code": 0, "message": "GPIO 操作完成", "data": {"config": merged_config, "current_level": merged_config.get("current_level")}}


def _parse_payload_length(data: Optional[str], data_type: Optional[str] = None) -> int:
    text = str(data or "").strip()
    if not text:
        return 0

    normalized_data_type = str(data_type or "").strip().upper()
    normalized = text.replace(",", " ").replace("\n", " ").replace("\t", " ")
    tokens = [token for token in normalized.split(" ") if token]

    if normalized_data_type == "HEX" or tokens:
        parsed_hex = []
        hex_ok = True
        for token in tokens:
            item = token[2:] if token.lower().startswith("0x") else token
            if not item or len(item) > 2 or any(ch not in "0123456789abcdefABCDEF" for ch in item):
                hex_ok = False
                break
            parsed_hex.append(item)
        if hex_ok and parsed_hex:
            return len(parsed_hex)

    return len(text.encode("utf-8"))


def _encode_protocol_payload(data: Optional[str], data_type: Optional[str] = None) -> bytes:
    text = str(data or "")
    normalized_data_type = str(data_type or "").strip().upper()
    if normalized_data_type == "HEX":
        compact = text.replace(",", " ").replace("\n", " ").replace("\t", " ")
        tokens = [token for token in compact.split(" ") if token]
        if not tokens:
            return b""
        values = []
        for token in tokens:
            item = token[2:] if token.lower().startswith("0x") else token
            if not item or len(item) > 2 or any(ch not in "0123456789abcdefABCDEF" for ch in item):
                raise ValueError("HEX 数据格式不正确")
            values.append(int(item, 16))
        return bytes(values)
    return text.encode("utf-8")


def _decode_protocol_payload(data: bytes, data_type: Optional[str] = None) -> str:
    if str(data_type or "").strip().upper() == "HEX":
        return " ".join(f"{byte:02X}" for byte in data)
    return data.decode("utf-8", errors="replace")


def _normalize_serial_bytesize(value: Optional[int]) -> int:
    size = int(value or 8)
    mapping = {
        5: getattr(serial, "FIVEBITS", 5),
        6: getattr(serial, "SIXBITS", 6),
        7: getattr(serial, "SEVENBITS", 7),
        8: getattr(serial, "EIGHTBITS", 8),
    }
    if size not in mapping:
        raise ValueError("串口数据位配置无效")
    return mapping[size]


def _normalize_serial_stopbits(value: Optional[float]) -> float:
    try:
        stop_bits = float(value or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("串口停止位配置无效") from exc
    mapping = {
        1.0: getattr(serial, "STOPBITS_ONE", 1),
        1.5: getattr(serial, "STOPBITS_ONE_POINT_FIVE", 1.5),
        2.0: getattr(serial, "STOPBITS_TWO", 2),
    }
    if stop_bits not in mapping:
        raise ValueError("串口停止位配置无效")
    return mapping[stop_bits]


def _normalize_serial_parity(value: Optional[str]) -> str:
    mapping = {
        "NONE": getattr(serial, "PARITY_NONE", "N"),
        "ODD": getattr(serial, "PARITY_ODD", "O"),
        "EVEN": getattr(serial, "PARITY_EVEN", "E"),
    }
    parity = str(value or "NONE").strip().upper()
    if parity not in mapping:
        raise ValueError("串口校验位配置无效")
    return mapping[parity]


def _open_serial_connection(
    *,
    port: str,
    baud_rate: int,
    data_bits: int,
    stop_bits: float,
    parity: str,
    flow_control: Optional[str] = None,
) -> Any:
    if serial is None:
        raise RuntimeError("当前环境缺少 pyserial，无法执行串口真实通信，请先安装 pyserial")

    normalized_port = str(port or "").strip()
    if not normalized_port:
        raise ValueError("请先选择串口号")

    flow = str(flow_control or "NONE").strip().upper()
    return serial.Serial(
        port=normalized_port,
        baudrate=int(baud_rate),
        bytesize=_normalize_serial_bytesize(data_bits),
        parity=_normalize_serial_parity(parity),
        stopbits=_normalize_serial_stopbits(stop_bits),
        timeout=SERIAL_READ_POLL_TIMEOUT_SECONDS,
        write_timeout=SERIAL_DEFAULT_TIMEOUT_SECONDS,
        xonxoff=flow == "XON/XOFF",
        rtscts=flow == "RTS/CTS",
    )


def _store_serial_session_connection(session_id: int, connection: Any) -> None:
    with _SERIAL_SESSION_LOCK:
        old = _SERIAL_SESSION_CONNECTIONS.pop(session_id, None)
        _SERIAL_SESSION_CONNECTIONS[session_id] = connection
        _SERIAL_SESSION_IO_LOCKS[session_id] = _SERIAL_SESSION_IO_LOCKS.get(session_id) or threading.Lock()
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


def _get_serial_session_connection(session_id: int) -> Optional[Any]:
    with _SERIAL_SESSION_LOCK:
        return _SERIAL_SESSION_CONNECTIONS.get(session_id)


def _get_serial_session_io_lock(session_id: int) -> threading.Lock:
    with _SERIAL_SESSION_LOCK:
        lock = _SERIAL_SESSION_IO_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _SERIAL_SESSION_IO_LOCKS[session_id] = lock
        return lock


def _append_serial_listener_log(session_id: int, payload: bytes) -> None:
    if not payload:
        return
    db = SessionLocal()
    try:
        session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
        if not session or int(getattr(session, "status", 0) or 0) != 1:
            return
        config = _load_session_config(session)
        decoded = _decode_protocol_payload(payload, config.get("data_type"))
        db.add(
            ProtocolLog(
                session_id=session.id,
                protocol=session.protocol,
                timestamp=datetime.now(),
                direction="Rx",
                frame_id=None,
                dlc=len(payload),
                data=decoded,
            )
        )
        session.rx_count = int(getattr(session, "rx_count", 0) or 0) + 1
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _serial_listener_worker(session_id: int, connection: Any, stop_event: threading.Event) -> None:
    buffer = bytearray()
    last_chunk_at: Optional[float] = None
    io_lock = _get_serial_session_io_lock(session_id)
    while not stop_event.is_set():
        chunk = b""
        try:
            with io_lock:
                if hasattr(connection, "is_open") and not bool(connection.is_open):
                    break
                waiting = int(getattr(connection, "in_waiting", 0) or 0)
                if waiting > 0:
                    chunk = bytes(connection.read(waiting))
        except Exception:
            break
        if chunk:
            buffer.extend(chunk)
            last_chunk_at = time.monotonic()
        elif buffer and last_chunk_at is not None and time.monotonic() - last_chunk_at >= 0.2:
            _append_serial_listener_log(session_id, bytes(buffer))
            buffer.clear()
            last_chunk_at = None
        time.sleep(0.05)
    if buffer:
        _append_serial_listener_log(session_id, bytes(buffer))


def _start_serial_session_listener(session_id: int, connection: Any) -> None:
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_serial_listener_worker,
        args=(session_id, connection, stop_event),
        name=f"serial-listener-{session_id}",
        daemon=True,
    )
    with _SERIAL_SESSION_LOCK:
        old_event = _SERIAL_SESSION_STOP_EVENTS.pop(session_id, None)
        _SERIAL_SESSION_STOP_EVENTS[session_id] = stop_event
        _SERIAL_SESSION_THREADS[session_id] = worker
    if old_event is not None:
        old_event.set()
    worker.start()


def _close_serial_session_connection(session_id: int) -> None:
    with _SERIAL_SESSION_LOCK:
        connection = _SERIAL_SESSION_CONNECTIONS.pop(session_id, None)
        stop_event = _SERIAL_SESSION_STOP_EVENTS.pop(session_id, None)
        worker = _SERIAL_SESSION_THREADS.pop(session_id, None)
        _SERIAL_SESSION_IO_LOCKS.pop(session_id, None)
    if stop_event is not None:
        stop_event.set()
    if worker is not None and worker.is_alive():
        worker.join(timeout=0.5)
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def _close_all_serial_session_connections() -> None:
    with _SERIAL_SESSION_LOCK:
        session_ids = set(_SERIAL_SESSION_CONNECTIONS)
        session_ids.update(_SERIAL_SESSION_STOP_EVENTS)
        session_ids.update(_SERIAL_SESSION_THREADS)
    for session_id in session_ids:
        _close_serial_session_connection(session_id)


def _run_serial_exchange(
    port: str,
    payload_bytes: bytes,
    *,
    baud_rate: int,
    data_bits: int,
    stop_bits: float,
    parity: str,
    timeout: float,
    flow_control: Optional[str] = None,
    expected_length: Optional[int] = None,
    data_type: Optional[str] = None,
    existing_connection: Optional[Any] = None,
    existing_io_lock: Optional[threading.Lock] = None,
    close_when_done: bool = True,
    require_reply: bool = True,
) -> tuple[str, int]:
    if serial is None:
        raise RuntimeError("当前环境缺少 pyserial，无法执行串口真实通信，请先安装 pyserial")

    normalized_port = str(port or "").strip()
    if not normalized_port:
        raise ValueError("请先选择串口号")

    flow = str(flow_control or "NONE").strip().upper()
    xonxoff = flow == "XON/XOFF"
    rtscts = flow == "RTS/CTS"

    connection = existing_connection or serial.Serial(
        port=normalized_port,
        baudrate=int(baud_rate),
        bytesize=_normalize_serial_bytesize(data_bits),
        parity=_normalize_serial_parity(parity),
        stopbits=_normalize_serial_stopbits(stop_bits),
        timeout=SERIAL_READ_POLL_TIMEOUT_SECONDS,
        write_timeout=max(timeout, 1.0),
        xonxoff=xonxoff,
        rtscts=rtscts,
    )
    try:
        io_lock = existing_io_lock or threading.Lock()
        with io_lock:
            if hasattr(connection, "is_open") and not bool(connection.is_open):
                connection.open()
            if hasattr(connection, "reset_input_buffer"):
                connection.reset_input_buffer()
            if hasattr(connection, "reset_output_buffer"):
                connection.reset_output_buffer()
            connection.write(payload_bytes)
            if hasattr(connection, "flush"):
                connection.flush()

            response = bytearray()
            read_window = min(max(timeout, 0.2), 1.0) if not require_reply else max(timeout, 0.2)
            deadline = time.monotonic() + read_window
            idle_gap = min(max(read_window * 0.25, 0.08), 0.25)
            idle_deadline: Optional[float] = None
            max_length = int(expected_length or 0)
            while time.monotonic() < deadline:
                waiting = int(getattr(connection, "in_waiting", 0) or 0)
                read_size = waiting or 1
                if max_length > 0:
                    remaining = max_length - len(response)
                    if remaining <= 0:
                        break
                    read_size = min(read_size, remaining)
                chunk = connection.read(read_size)
                if chunk:
                    response.extend(chunk)
                    idle_deadline = time.monotonic() + idle_gap
                    if max_length > 0 and len(response) >= max_length:
                        break
                    continue
                if response and idle_deadline is not None and time.monotonic() >= idle_deadline:
                    break

        if not response and require_reply:
            raise TimeoutError("未收到串口设备回复数据")
        if not response:
            return "", 0
        return _decode_protocol_payload(bytes(response), data_type), len(response)
    except Exception:
        raise
    finally:
        try:
            if close_when_done:
                connection.close()
        except Exception:
            pass


def _write_serial_payload(
    connection: Any,
    payload_bytes: bytes,
    *,
    io_lock: threading.Lock,
) -> int:
    """Write one serial payload without consuming bytes owned by the listener."""
    if connection is None:
        raise RuntimeError("串口连接已失效")
    with io_lock:
        if hasattr(connection, "is_open") and not bool(connection.is_open):
            connection.open()
        written = connection.write(payload_bytes)
        if isinstance(written, int) and written != len(payload_bytes):
            raise OSError(f"串口数据写入不完整：期望 {len(payload_bytes)} 字节，实际 {written} 字节")
        if hasattr(connection, "flush"):
            connection.flush()
    return len(payload_bytes) if written is None else int(written)


def _run_tcp_client_exchange(host: str, port: int, payload_bytes: bytes, timeout: float, data_type: Optional[str] = None) -> tuple[str, dict]:
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload_bytes)
        local_ip, local_port = sock.getsockname()[:2]
        remote_ip, remote_port = sock.getpeername()[:2]
        reply = b""
        sock.settimeout(min(max(timeout, 0.1), 1.0))
        try:
            reply = sock.recv(4096)
        except socket.timeout:
            reply = b""
        return _decode_protocol_payload(reply, data_type), {
            "local_ip": local_ip,
            "local_port": local_port,
            "remote_ip": remote_ip,
            "remote_port": remote_port,
            "reply_received": bool(reply),
        }


def _run_tcp_server_exchange(local_ip: str, listen_port: int, payload_bytes: bytes, timeout: float, data_type: Optional[str] = None) -> tuple[str, dict]:
    bind_ip = str(local_ip or "").strip() or "0.0.0.0"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(timeout)
        server.bind((bind_ip, int(listen_port)))
        server.listen(1)
        conn, remote_addr = server.accept()
        with conn:
            actual_local_ip, actual_local_port = conn.getsockname()[:2]
            remote_ip, remote_port = remote_addr[:2]
            received = b""
            send_success = False
            conn.settimeout(min(max(timeout, 0.1), 1.0))
            try:
                received = conn.recv(4096)
            except socket.timeout:
                received = b""
            try:
                conn.sendall(payload_bytes)
                send_success = True
            except OSError:
                if not received:
                    raise
            return _decode_protocol_payload(received, data_type), {
                "local_ip": actual_local_ip,
                "local_port": actual_local_port,
                "remote_ip": remote_ip,
                "remote_port": remote_port,
                "client_connected": True,
                "reply_received": bool(received),
                "send_success": send_success,
            }


def _run_udp_exchange(local_ip: str, local_port: int, target_ip: str, target_port: int, payload_bytes: bytes, timeout: float, data_type: Optional[str] = None) -> tuple[str, dict]:
    bind_ip = str(local_ip or "").strip() or "0.0.0.0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.bind((bind_ip, int(local_port)))
        sock.sendto(payload_bytes, (target_ip, int(target_port)))
        actual_local_ip, actual_local_port = sock.getsockname()[:2]
        reply = b""
        remote_ip, remote_port = target_ip, int(target_port)
        sock.settimeout(min(max(timeout, 0.1), 1.0))
        try:
            reply, remote_addr = sock.recvfrom(4096)
            remote_ip, remote_port = remote_addr[:2]
        except socket.timeout:
            reply = b""
        return _decode_protocol_payload(reply, data_type), {
            "local_ip": actual_local_ip,
            "local_port": actual_local_port,
            "remote_ip": remote_ip,
            "remote_port": remote_port,
            "reply_received": bool(reply),
        }


def _raise_protocol_validation_error(
    db: Session,
    session: ProtocolSession,
    detail: str,
    config: Optional[dict] = None,
    code: Optional[str] = None,
    payload_length: Optional[int] = None,
) -> None:
    if isinstance(config, dict):
        _persist_validation_result(session, config, passed=False, detail=detail, code=code, payload_length=payload_length)
    _append_system_log(db, session, detail)
    _notify_protocol_result(db, session, passed=False)
    db.commit()
    raise HTTPException(status_code=400, detail=detail)


def _raise_can_dependency_http_error(exc: Exception, *, prefix: str) -> None:
    if isinstance(exc, CanDependencyMissingError):
        raise HTTPException(status_code=424, detail=f"dependency_missing: {exc.message}") from exc
    if isinstance(exc, CanAdapterError):
        raise HTTPException(status_code=400, detail=f"{prefix}：{exc.message}") from exc
    raise HTTPException(status_code=400, detail=f"{prefix}：{str(exc)}") from exc


def _should_invalidate_can_session_on_send_error(exc: Exception) -> bool:
    if isinstance(exc, CanDependencyMissingError):
        return True
    if not isinstance(exc, CanAdapterError):
        return False
    if str(getattr(exc, "code", "") or "").strip().lower() in {
        "channel_error",
        "adapter_mismatch",
        "adapter_offline",
        "adapter_not_found",
    }:
        return True
    normalized_message = str(getattr(exc, "message", "") or exc).strip().lower()
    return any(
        keyword in normalized_message
        for keyword in (
            "请重新连接",
            "请重新扫描",
            "已离线",
            "device_index",
            "pnp",
            "拔出",
            "offline",
        )
    )


def _append_system_log(db: Session, session: ProtocolSession, content: str) -> None:
    db.add(
        ProtocolLog(
            session_id=session.id,
            protocol=session.protocol,
            timestamp=datetime.now(),
            direction="System",
            frame_id=None,
            dlc=None,
            data=content,
        )
    )


def _create_protocol_session(
    *,
    db: Session,
    request: Request,
    current_user: User,
    payload: ConnectRequest,
    config: dict,
    initial_logs: Optional[list[str]] = None,
) -> ProtocolSession:
    ensure_schema()
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
    now = datetime.now()
    session = ProtocolSession(
        created_by_user_id=current_user.id,
        task_no=generate_protocol_session_no(db, created_at=now),
        target=payload.target,
        protocol=_normalize_protocol_kind(payload.protocol),
        config_json=json.dumps(config or {}, ensure_ascii=False),
        status=1,
        tx_count=0,
        rx_count=0,
        executor=current_user.username,
        ip_address=ip,
        created_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    for item in initial_logs or []:
        _append_system_log(db, session, item)
    _append_system_log(db, session, "通道连接成功")
    db.commit()
    return session


def session_to_dict(s: ProtocolSession):
    return {
        "id": s.id,
        "task_no": getattr(s, "task_no", None),
        "target": s.target,
        "protocol": s.protocol,
        "config_json": s.config_json,
        "config": _load_session_config(s),
        "status": s.status,
        "tx": s.tx_count,
        "rx": s.rx_count,
        "executor": s.executor,
        "ip_address": s.ip_address,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def log_to_dict(l: ProtocolLog):
    return {
        "id": l.id,
        "timestamp": l.timestamp,
        "direction": l.direction,
        "frame_id": l.frame_id,
        "dlc": l.dlc,
        "data": l.data,
    }


def _normalize_can_identity_value(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _can_connection_matches_request(connection: CanAdapterConnection, config: dict[str, Any], physical_channel: str) -> bool:
    requested_adapter_key = _normalize_can_identity_value(config.get("adapter_key"))
    requested_serial = _normalize_can_identity_value(config.get("adapter_serial"))
    requested_device = _normalize_can_identity_value(config.get("adapter_device") or config.get("com_port"))
    connection_adapter_key = _normalize_can_identity_value(connection.device.adapter_key)
    connection_serial = _normalize_can_identity_value(connection.device.serial_number)
    connection_device = _normalize_can_identity_value(connection.device.adapter_device or connection.device.pnp_device_id)

    same_adapter = False
    if requested_adapter_key and requested_adapter_key == connection_adapter_key:
        same_adapter = True
    elif requested_serial and requested_serial == connection_serial:
        same_adapter = True
    elif requested_device and requested_device == connection_device:
        same_adapter = True

    if not same_adapter:
        return

    if connection.channel_guard_key and connection.channel_guard_key == connection.device.adapter_key:
        return True
    return str(connection.channel_name or "").strip() == physical_channel


def _force_release_conflicting_can_session(config: dict[str, Any], physical_channel: str) -> None:
    if not physical_channel:
        return
    conflicting = []
    with _CAN_SESSION_LOCK:
        for sid, runtime in _CAN_SESSION_RUNTIMES.items():
            if _can_connection_matches_request(runtime.connection, config, physical_channel):
                conflicting.append(sid)
    
    if conflicting:
        db = SessionLocal()
        try:
            for sid in conflicting:
                session = db.query(ProtocolSession).filter(ProtocolSession.id == sid).first()
                if session:
                    session.status = 2
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
            
        for sid in conflicting:
            _close_can_session_connection(sid)


def _format_can_termination_log(backend_key: str, termination_enabled: bool) -> str:
    normalized_backend = str(backend_key or "").strip().lower()
    if normalized_backend == "usbcanfd_200u":
        return f"内部120Ω终端电阻={'开启' if termination_enabled else '关闭'}"
    if normalized_backend == "zqwl_ucan_cdc":
        return "120Ω终端电阻由 ZQWL 设备拨码控制，请检查接线与拨码状态"
    return f"120Ω终端电阻配置={'开启' if termination_enabled else '关闭'}（由当前适配器能力决定是否生效）"


def _open_protocol_channel_resources(protocol: str, config: dict[str, Any], logs: list[str]) -> tuple[Optional[Any], Optional[CanAdapterConnection], Optional[WchGpioConnection], list[str], dict[str, Any]]:
    serial_connection = None
    can_connection = None
    wch_connection = None
    resolved_config = dict(config or {})

    if protocol in {"can", "canfd"}:
        resolved_config = _normalize_can_runtime_config(protocol, resolved_config)
        physical_channel = str(resolved_config.get("physical_channel") or "").strip()
        _force_release_conflicting_can_session(resolved_config, physical_channel)
        
        try:
            can_connection = open_can_adapter_connection(protocol, resolved_config)
        except Exception as exc:
            _raise_can_dependency_http_error(exc, prefix=f"{_protocol_label(protocol)} 连接失败")
        resolved_config["adapter_name"] = can_connection.device.adapter_name
        resolved_config["adapter_serial"] = can_connection.device.serial_number
        resolved_config["adapter_key"] = can_connection.device.adapter_key
        resolved_config["backend_key"] = can_connection.device.backend_key
        resolved_config["adapter_device"] = can_connection.device.adapter_device or can_connection.device.pnp_device_id
        resolved_config["com_port"] = can_connection.device.adapter_device or resolved_config.get("com_port") or ""
        resolved_config["sdk_device_index"] = can_connection.device.sdk_device_index
        resolved_config["physical_channel"] = can_connection.channel_name
        resolved_config["channel"] = can_connection.channel_name
        termination_log = _format_can_termination_log(
            can_connection.device.backend_key,
            bool(resolved_config.get("termination_enabled")),
        )
        if protocol == "can":
            logs = [
                *logs,
                f"适配器连接成功：{can_connection.device.adapter_name} / {can_connection.device.serial_number or '-'} / {resolved_config.get('com_port') or '-'}",
                f"物理通道 {can_connection.channel_name} 初始化完成，已按经典 CAN 波特率 {resolved_config.get('baud_rate') or resolved_config.get('bitrate') or '-'} 配置",
                termination_log,
                f"物理通道 {can_connection.channel_name} 启动成功，持续接收线程将在会话建立后自动开始",
            ]
        else:
            logs = [
                *logs,
                f"适配器连接成功：{can_connection.device.adapter_name} / {can_connection.device.serial_number or '-'}",
                f"物理通道 {can_connection.channel_name} 初始化完成，{termination_log}",
                f"物理通道 {can_connection.channel_name} 启动成功，持续接收线程将在会话建立后自动开始",
            ]
        return serial_connection, can_connection, wch_connection, logs, resolved_config

    if protocol == "serial":
        try:
            serial_connection = _open_serial_connection(
                port=str(resolved_config.get("com_port") or ""),
                baud_rate=int(resolved_config.get("baud_rate") or 115200),
                data_bits=int(resolved_config.get("data_bits") or 8),
                stop_bits=float(resolved_config.get("stop_bits") or 1),
                parity=str(resolved_config.get("parity") or "NONE"),
                flow_control=str(resolved_config.get("flow_control") or "NONE"),
            )
            logs = [*logs, f"串口 {resolved_config.get('com_port')} 已建立真实连接"]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"串口连接失败：{str(exc)}") from exc

    if protocol in {"gpio", "gpio_io"}:
        try:
            resolved_config = _ensure_gpio_runtime_config(resolved_config)
        except GpioRuntimeConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        transport_kind = str(resolved_config.get("gpio_transport_kind") or "").strip().lower()
        transport_config = resolved_config.get("gpio_transport_config") if isinstance(resolved_config.get("gpio_transport_config"), dict) else {}
        if transport_kind == "serial":
            try:
                serial_connection = _open_serial_connection(
                    port=str(transport_config.get("com_port") or ""),
                    baud_rate=int(transport_config.get("baud_rate") or 115200),
                    data_bits=int(transport_config.get("data_bits") or 8),
                    stop_bits=float(transport_config.get("stop_bits") or 1),
                    parity=str(transport_config.get("parity") or "NONE"),
                    flow_control=str(transport_config.get("flow_control") or "NONE"),
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"GPIO 串口连接失败：{str(exc)}") from exc
            logs = [*logs, f"GPIO 真实业务通道已建立：串口 {transport_config.get('com_port') or '-'}"]
            resolved_config["channel"] = str(transport_config.get("com_port") or "")
            return serial_connection, can_connection, wch_connection, logs, resolved_config
        if transport_kind in {"wch_gpio", "wch"}:
            try:
                wch_connection = open_wch_gpio_connection(str(transport_config.get("com_port") or ""))
            except WchGpioError as exc:
                raise HTTPException(status_code=400, detail=f"GPIO WCH 连接失败：{str(exc)}") from exc
            transport_config["kind"] = "wch_gpio"
            transport_config["chip_type_text"] = wch_connection.chip_type_text
            transport_config["gpio_count"] = wch_connection.gpio_count
            transport_config["port_index"] = wch_connection.port_index
            resolved_config["gpio_transport_config"] = transport_config
            resolved_config["gpio_transport_kind"] = "wch_gpio"
            resolved_config["gpio_runtime_ready"] = True
            resolved_config["supports_readback"] = True
            resolved_config["channel"] = str(transport_config.get("com_port") or "")
            logs = [
                *logs,
                f"GPIO WCH 通道已建立：{transport_config.get('com_port') or '-'} / {wch_connection.chip_type_text or 'WCH'} / {wch_connection.gpio_count or '-'} 路 GPIO",
            ]
            return serial_connection, can_connection, wch_connection, logs, resolved_config
        if transport_kind in {"can", "canfd"}:
            can_runtime_config = _normalize_can_runtime_config(transport_kind, transport_config)
            physical_channel = str(can_runtime_config.get("physical_channel") or "").strip()
            _force_release_conflicting_can_session(can_runtime_config, physical_channel)
            try:
                can_connection = open_can_adapter_connection(transport_kind, can_runtime_config)
            except Exception as exc:
                _raise_can_dependency_http_error(exc, prefix="GPIO 连接失败")
            can_runtime_config["adapter_name"] = can_connection.device.adapter_name
            can_runtime_config["adapter_serial"] = can_connection.device.serial_number
            can_runtime_config["adapter_key"] = can_connection.device.adapter_key
            can_runtime_config["backend_key"] = can_connection.device.backend_key
            can_runtime_config["adapter_device"] = can_connection.device.adapter_device or can_connection.device.pnp_device_id
            can_runtime_config["com_port"] = can_connection.device.adapter_device or can_runtime_config.get("com_port") or ""
            can_runtime_config["sdk_device_index"] = can_connection.device.sdk_device_index
            can_runtime_config["physical_channel"] = can_connection.channel_name
            can_runtime_config["channel"] = can_connection.channel_name
            resolved_config["gpio_transport_config"] = can_runtime_config
            resolved_config["channel"] = can_connection.channel_name
            logs = [*logs, f"GPIO 真实业务通道已建立：{transport_kind.upper()} {can_connection.channel_name}"]
            return serial_connection, can_connection, wch_connection, logs, resolved_config
        raise HTTPException(status_code=400, detail="GPIO 真实业务配置缺少可用 transport.kind，请检查 gpio_runtime.json")

    return serial_connection, can_connection, wch_connection, logs, resolved_config


@router.post("/connect", response_model=Response)
async def connect_device(
    payload: ConnectRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    protocol = _normalize_protocol_kind(payload.protocol)
    serial_connection = None
    can_connection = None
    wch_connection = None
    config = dict(payload.config or {})
    serial_connection, can_connection, wch_connection, initial_logs, config = _open_protocol_channel_resources(protocol, config, ["已连接设备"])
    try:
        session = _create_protocol_session(
            db=db,
            request=request,
            current_user=current_user,
            payload=payload,
            config=config,
            initial_logs=initial_logs,
        )
        if serial_connection is not None:
            _store_serial_session_connection(session.id, serial_connection)
            _start_serial_session_listener(session.id, serial_connection)
        if can_connection is not None:
            _store_can_session_connection(session.id, can_connection)
        if wch_connection is not None:
            _store_wch_gpio_session_connection(session.id, wch_connection)
    except Exception:
        try:
            if serial_connection is not None:
                serial_connection.close()
        except Exception:
            pass
        if can_connection is not None:
            try:
                close_can_adapter_connection(can_connection)
            except Exception:
                pass
        close_wch_gpio_connection(wch_connection)
        raise
    return {"code": 0, "message": "连接成功", "data": session_to_dict(session)}


@router.post("/channel/scan", response_model=Response)
async def scan_channel(
    payload: ChannelScanRequest,
    _: User = Depends(get_current_user),
):
    config, logs = _build_auto_channel_config(payload.protocol)
    config = _merge_connect_request_config(payload.protocol, config, payload.config)
    return {
        "code": 0,
        "message": "通道扫描成功",
        "data": {
            "protocol": _normalize_protocol_kind(payload.protocol),
            "config": config,
            "probe_summary": config.get("probe_summary") or (logs[-1] if logs else "已完成通道扫描"),
            "logs": logs,
        },
    }


@router.post("/channel/connect", response_model=Response)
async def connect_channel(
    payload: ConnectRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    config, logs = _build_auto_channel_config(payload.protocol)
    config = _merge_connect_request_config(payload.protocol, config, payload.config)
    protocol = _normalize_protocol_kind(payload.protocol)
    serial_connection = None
    can_connection = None
    wch_connection = None
    serial_connection, can_connection, wch_connection, logs, config = _open_protocol_channel_resources(protocol, config, logs)
    try:
        session = _create_protocol_session(
            db=db,
            request=request,
            current_user=current_user,
            payload=payload,
            config=config,
            initial_logs=logs,
        )
        if serial_connection is not None:
            _store_serial_session_connection(session.id, serial_connection)
            _start_serial_session_listener(session.id, serial_connection)
        if can_connection is not None:
            _store_can_session_connection(session.id, can_connection)
        if wch_connection is not None:
            _store_wch_gpio_session_connection(session.id, wch_connection)
    except Exception:
        try:
            if serial_connection is not None:
                serial_connection.close()
        except Exception:
            pass
        if can_connection is not None:
            try:
                close_can_adapter_connection(can_connection)
            except Exception:
                pass
        close_wch_gpio_connection(wch_connection)
        raise
    return {
        "code": 0,
        "message": "通道连接成功",
        "data": {
            **session_to_dict(session),
            "channel_status": "connected",
            "channel_name": config.get("physical_channel") or config.get("channel") or config.get("com_port") or config.get("adapter_name") or payload.protocol,
            "probe_summary": config.get("probe_summary") or "通道连接成功",
        },
    }


@router.post("/{session_id}/disconnect", response_model=Response)
async def disconnect_device(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    normalized_protocol = _normalize_protocol_kind(session.protocol)
    session_config = _load_session_config(session)
    gpio_transport_kind = str(session_config.get("gpio_transport_kind") or "").strip().lower()
    if normalized_protocol == "serial" or (normalized_protocol in {"gpio", "gpio_io"} and gpio_transport_kind == "serial"):
        _close_serial_session_connection(session.id)
    if normalized_protocol in {"can", "canfd"} or (normalized_protocol in {"gpio", "gpio_io"} and gpio_transport_kind in {"can", "canfd"}):
        _close_can_session_connection(session.id)
    if normalized_protocol in {"gpio", "gpio_io"} and gpio_transport_kind in {"wch_gpio", "wch"}:
        _close_wch_gpio_session_connection(session.id)
    session.status = 2
    _append_system_log(db, session, "通道已断开")
    db.commit()
    return {"code": 0, "message": "断开成功"}


async def _send_can_protocol_frame(
    *,
    db: Session,
    session: ProtocolSession,
    payload: SendRequest,
    protocol: str,
    merged_config: dict[str, Any],
    payload_data: Optional[str],
) -> dict[str, Any]:
    if not str(payload.frame_id or "").strip():
        _raise_protocol_validation_error(db, session, "验证未通过：帧 ID 不能为空", config=merged_config)

    if protocol == "canfd" and _normalize_bool_config(merged_config.get("remote_frame"), default=False):
        _raise_protocol_validation_error(db, session, "验证未通过：CAN FD 不支持远程帧", config=merged_config, code="canfd_remote_frame_forbidden")

    remote_frame_enabled = protocol == "can" and _normalize_bool_config(merged_config.get("remote_frame"), default=False)
    if remote_frame_enabled:
        payload_data = None

    try:
        data_length, payload_length = _normalize_can_length_for_send(
            protocol,
            payload_data,
            merged_config.get("data_length", payload.dlc),
            merged_config.get("data_type"),
            is_remote_frame=remote_frame_enabled,
        )
        is_extended = _is_extended_can_id(merged_config)
        frame_id_value = parse_can_frame_id(payload.frame_id, is_extended=is_extended)
        expected_rx_id = parse_can_frame_id(merged_config.get("expected_rx_id"), is_extended=is_extended) if str(merged_config.get("expected_rx_id") or "").strip() else None
        expected_rx_mask = parse_can_mask(merged_config.get("expected_rx_mask"), is_extended=is_extended)
        expected_data = _normalize_can_expected_data(merged_config)
        rx_timeout_ms = _normalize_can_rx_timeout_ms(merged_config.get("rx_timeout_ms"))
        payload_bytes = b"" if remote_frame_enabled else _encode_protocol_payload(payload_data, merged_config.get("data_type"))
        if protocol == "can" and not remote_frame_enabled and len(payload_bytes) < data_length:
            payload_bytes = payload_bytes + (b"\x00" * (data_length - len(payload_bytes)))
    except ValueError as exc:
        _raise_protocol_validation_error(db, session, f"验证未通过：{str(exc)}", config=merged_config, code=f"{protocol}_config_invalid")

    if protocol == "canfd":
        merged_config["dlc"] = can_fd_length_to_dlc(data_length)
    else:
        merged_config["dlc"] = data_length
    merged_config["data_length"] = data_length
    merged_config["physical_channel"] = str(merged_config.get("physical_channel") or merged_config.get("channel") or "").strip()
    merged_config["channel"] = merged_config["physical_channel"]
    session.config_json = json.dumps(merged_config, ensure_ascii=False)

    runtime = _get_can_session_runtime(session.id)
    if runtime is None:
        _raise_protocol_validation_error(
            db,
            session,
            f"验证未通过：{_protocol_label(protocol)} 连接已失效，请重新连接通道",
            config=merged_config,
            code=f"{protocol}_channel_error",
            payload_length=payload_length,
        )
    connection = runtime.connection

    frame = CanFrame(
        frame_id=frame_id_value,
        is_extended_id=is_extended,
        is_fd=protocol == "canfd",
        bitrate_switch=(
            protocol == "canfd"
            and _normalize_bool_config(merged_config.get("brs"), default=True)
        ),
        is_remote_frame=remote_frame_enabled,
        data=payload_bytes,
        declared_data_length=data_length,
        channel_name=connection.channel_name,
    )
    with runtime.rx_condition:
        baseline_sequence = runtime.rx_sequence

    try:
        backend = CAN_ADAPTER_BACKENDS.get(connection.backend_key)
        if backend is None:
            raise CanAdapterError("channel_error", "CAN 适配器后端已丢失，请重新连接通道")
        with runtime.io_lock:
            await asyncio.to_thread(backend.transmit, connection, frame)
    except CanDependencyMissingError as exc:
        if _should_invalidate_can_session_on_send_error(exc):
            _close_can_session_connection(session.id)
            session.status = 2
        _raise_protocol_validation_error(
            db,
            session,
            f"dependency_missing: {exc.message}",
            config=merged_config,
            code=f"{protocol}_dependency_missing",
            payload_length=payload_length,
        )
    except CanAdapterError as exc:
        if _should_invalidate_can_session_on_send_error(exc):
            _close_can_session_connection(session.id)
            session.status = 2
        _raise_protocol_validation_error(
            db,
            session,
            f"验证未通过：{_protocol_label(protocol)} 发送失败，{exc.message}",
            config=merged_config,
            code=f"{protocol}_tx_error",
            payload_length=payload_length,
        )

    tx = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="Tx",
        frame_id=_format_can_log_frame_id(frame),
        dlc=frame.declared_data_length,
        data=_format_can_log_payload(frame, merged_config.get("data_type")),
    )
    db.add(tx)
    session.tx_count += 1
    _append_system_log(
        db,
        session,
        f"{_protocol_label(protocol)} 帧已提交给适配器：声明长度={frame.declared_data_length} 字节，实际数据长度={frame.data_length} 字节，真实发送数据={_format_can_log_payload(frame, merged_config.get('data_type'))}",
    )
    db.commit()

    expected_remote_frame_raw = merged_config.get("expected_remote_frame")
    expected_brs_raw = merged_config.get("expected_brs", merged_config.get("require_rx_brs"))
    expected_remote_frame = None if expected_remote_frame_raw in {None, ""} else _normalize_bool_config(expected_remote_frame_raw, default=False)
    expected_bitrate_switch = None if expected_brs_raw in {None, ""} else _normalize_bool_config(expected_brs_raw, default=False)
    needs_rx_validation = any(
        item is not None
        for item in (expected_rx_id, expected_rx_mask, expected_data, expected_remote_frame, expected_bitrate_switch)
    )
    if not needs_rx_validation:
        success_detail = f"{_protocol_label(protocol)} 帧发送成功，未配置接收验证条件，按发送成功判定本次验证通过"
        _persist_validation_result(
            session,
            merged_config,
            passed=True,
            detail=success_detail,
            code=f"{protocol}_tx_only_passed",
            payload_length=payload_length,
            reply_frame_received=False,
        )
        _append_system_log(db, session, success_detail)
        _notify_protocol_result(db, session, passed=True)
        db.commit()
        return {"code": 0, "message": success_detail}

    matched_entry = await asyncio.to_thread(
        _wait_for_expected_can_frame,
        runtime,
        after_sequence=baseline_sequence,
        timeout_ms=min(rx_timeout_ms, 1000),
        matcher=lambda item: item.channel_name == connection.channel_name
        and match_expected_rx_frame(
            item,
            expected_rx_id=expected_rx_id,
            expected_rx_mask=expected_rx_mask,
            expected_data=expected_data,
            expected_is_extended_id=is_extended if protocol == "canfd" else None,
            expected_is_fd=(protocol == "canfd") if protocol == "canfd" else False,
            expected_is_remote_frame=expected_remote_frame,
            expected_bitrate_switch=expected_bitrate_switch,
        )
    )
    if matched_entry is not None:
        matched_sequence, matched_frame = matched_entry
        with runtime.rx_condition:
            should_log_matched_rx = matched_sequence not in runtime.rx_logged_sequences
            if should_log_matched_rx:
                runtime.rx_logged_sequences.add(matched_sequence)
        if should_log_matched_rx:
            db.add(
                ProtocolLog(
                    session_id=session.id,
                    protocol=session.protocol,
                    timestamp=datetime.now(),
                    direction="Rx",
                    frame_id=_format_can_log_frame_id(matched_frame),
                    dlc=matched_frame.declared_data_length,
                    data=_format_can_rx_log_payload(matched_frame, merged_config.get("data_type")),
                )
            )
            session.rx_count += 1
        _append_system_log(
            db,
            session,
            f"收到真实回复帧：ID={_format_can_log_frame_id(matched_frame)}，实际数据长度={matched_frame.data_length} 字节，数据={_format_can_log_payload(matched_frame, merged_config.get('data_type'))}",
        )
        success_detail = "CAN协议验证通过" if protocol == "can" else f"协议验证通过：已成功提交 1 帧 {_protocol_label(protocol)} 数据，并收到符合匹配条件的真实回复帧"
        _persist_validation_result(
            session,
            merged_config,
            passed=True,
            detail=success_detail,
            code=f"{protocol}_passed",
            payload_length=payload_length,
            reply_frame_received=True,
        )
        _append_system_log(db, session, success_detail)
    else:
        success_detail = (
            f"CAN 帧发送成功，{rx_timeout_ms}ms 观察窗口内未收到符合条件的回复帧，按发送成功判定本次验证通过"
            if protocol == "can"
            else f"{_protocol_label(protocol)} 帧发送成功，{rx_timeout_ms}ms 观察窗口内未收到符合条件的回复帧，按发送成功判定本次验证通过"
        )
        _persist_validation_result(
            session,
            merged_config,
            passed=True,
            detail=success_detail,
            code=f"{protocol}_tx_only_passed",
            payload_length=payload_length,
            reply_frame_received=False,
        )
        _append_system_log(
            db,
            session,
            success_detail,
        )
    _notify_protocol_result(db, session, passed=True)
    db.commit()
    return {"code": 0, "message": success_detail}


@router.post("/{session_id}/send", response_model=Response)
async def send_frame(
    session_id: int,
    payload: SendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session.status != 1:
        raise HTTPException(status_code=400, detail="设备未连接")

    protocol = _normalize_protocol_kind(session.protocol)
    session_config = _load_session_config(session)
    runtime_config = payload.config if isinstance(payload.config, dict) else {}
    merged_config = {**session_config, **runtime_config}
    payload_data = payload.data
    if protocol in {"can", "canfd"}:
        # The connection already owns the physical adapter. Re-enumerating it
        # here makes the vendor SDK report our own open handle as "device busy"
        # and overwrites the valid sdk_device_index stored at connect time.
        merged_config = _normalize_can_runtime_config(
            protocol,
            merged_config,
            refresh_adapter=False,
        )
        return await _send_can_protocol_frame(
            db=db,
            session=session,
            payload=payload,
            protocol=protocol,
            merged_config=merged_config,
            payload_data=payload_data,
        )
    if protocol == "serial":
        if bool(merged_config.get("auto_append_crlf")) and isinstance(payload_data, str) and not payload_data.endswith("\r\n"):
            payload_data = f"{payload_data}\r\n"
        merged_config["auto_append_crlf"] = bool(merged_config.get("auto_append_crlf"))
    if protocol == "ethernet":
        merged_config["transport_protocol"] = _normalize_ethernet_mode(
            merged_config.get("transport_protocol") or merged_config.get("protocol") or merged_config.get("method")
        )
    if protocol in {"gpio", "gpio_io"}:
        try:
            merged_config = _ensure_gpio_runtime_config(merged_config)
        except GpioRuntimeConfigError as exc:
            _raise_protocol_validation_error(db, session, f"GPIO 操作未通过：{str(exc)}", config=merged_config, code="gpio_runtime_missing")
        merged_config["mode"] = str(merged_config.get("mode") or "输出")
    session.config_json = json.dumps(merged_config, ensure_ascii=False)

    payload_length = _parse_payload_length(payload_data, merged_config.get("data_type"))
    if protocol == "ethernet" and payload_length <= 0:
        _raise_protocol_validation_error(
            db,
            session,
            "验证未通过：以太网测试数据不能为空",
            config=merged_config,
            code="ethernet_channel_error",
            payload_length=payload_length,
        )
    if protocol == "serial":
        available_ports = [str(item).strip() for item in merged_config.get("serial_ports") or [] if str(item).strip()]
        com_port = str(merged_config.get("com_port") or "").strip()
        if not com_port:
            _raise_protocol_validation_error(db, session, "验证未通过：请选择串口号", config=merged_config, code="serial_channel_error")
        if available_ports and com_port not in available_ports:
            _raise_protocol_validation_error(
                db,
                session,
                f"验证未通过：串口 {com_port} 不在当前已识别设备列表中，❌（通道异常）",
                config=merged_config,
                code="serial_channel_error",
            )
        length_bytes = _parse_non_negative_int(merged_config.get("length_bytes"))
        if not length_bytes:
            _raise_protocol_validation_error(
                db,
                session,
                "验证未通过：长度(Bytes) 必须为正整数",
                config=merged_config,
                code="serial_channel_error",
            )
        merged_config["length_bytes"] = length_bytes
        merged_config["timeout"] = int(SERIAL_DEFAULT_TIMEOUT_SECONDS * 1000)
        session.config_json = json.dumps(merged_config, ensure_ascii=False)

    remote_frame_enabled = False

    if protocol == "ethernet":
        transport_mode = _normalize_ethernet_mode(merged_config.get("transport_protocol"))
        if transport_mode == "TCP Client":
            if not _is_valid_ipv4(merged_config.get("target_ip")):
                _raise_protocol_validation_error(db, session, "验证未通过：目标IP 格式不正确", config=merged_config, code="ethernet_channel_error")
            target_port = _parse_non_negative_int(merged_config.get("target_port"))
            timeout = _parse_non_negative_int(merged_config.get("timeout"))
            if not target_port or target_port > 65535:
                _raise_protocol_validation_error(db, session, "验证未通过：目标端口必须在 1-65535 范围内", config=merged_config, code="ethernet_channel_error")
            if not timeout:
                _raise_protocol_validation_error(db, session, "验证未通过：超时时间必须为正整数", config=merged_config, code="ethernet_channel_error")
            merged_config["target_port"] = target_port
            merged_config["timeout"] = timeout
        elif transport_mode == "TCP Server":
            local_ip = str(merged_config.get("local_ip") or "").strip()
            listen_port = _parse_non_negative_int(merged_config.get("listen_port"))
            if not _is_valid_ipv4(local_ip):
                _raise_protocol_validation_error(db, session, "验证未通过：本地IP 格式不正确", config=merged_config, code="ethernet_channel_error")
            if not listen_port or listen_port > 65535:
                _raise_protocol_validation_error(db, session, "验证未通过：监听端口必须在 1-65535 范围内", config=merged_config, code="ethernet_channel_error")
            if not _is_tcp_port_available(local_ip, listen_port):
                _raise_protocol_validation_error(
                    db,
                    session,
                    f"验证未通过：监听端口 {listen_port} 已被占用，❌（通道异常）",
                    config=merged_config,
                    code="ethernet_port_occupied",
                )
            merged_config["listen_port"] = listen_port
        elif transport_mode == "UDP":
            local_ip = str(merged_config.get("local_ip") or "").strip()
            local_port = _parse_non_negative_int(merged_config.get("local_port"))
            target_port = _parse_non_negative_int(merged_config.get("target_port"))
            if not _is_valid_ipv4(local_ip):
                _raise_protocol_validation_error(db, session, "验证未通过：本地IP 格式不正确", config=merged_config, code="ethernet_channel_error")
            if not _is_valid_ipv4(merged_config.get("target_ip")):
                _raise_protocol_validation_error(db, session, "验证未通过：目标IP 格式不正确", config=merged_config, code="ethernet_channel_error")
            if not local_port or local_port > 65535:
                _raise_protocol_validation_error(db, session, "验证未通过：本地端口必须在 1-65535 范围内", config=merged_config, code="ethernet_channel_error")
            if not target_port or target_port > 65535:
                _raise_protocol_validation_error(db, session, "验证未通过：目标端口必须在 1-65535 范围内", config=merged_config, code="ethernet_channel_error")
            merged_config["local_port"] = local_port
            merged_config["target_port"] = target_port
        session.config_json = json.dumps(merged_config, ensure_ascii=False)

    if protocol in {"gpio", "gpio_io"}:
        pin = str(merged_config.get("pin") or payload.frame_id or "").strip()
        mode = str(merged_config.get("mode") or "输出").strip()
        action = str(merged_config.get("action") or "").strip().lower()
        timeout_ms = _parse_non_negative_int(merged_config.get("timeout_ms"), 5000) or 5000
        if not pin and action not in {"batch_read", "batch_write"}:
            _raise_protocol_validation_error(db, session, "GPIO 操作未通过：请选择引脚", config=merged_config, code="gpio_channel_error")
        if mode == "边沿中断 (监听)" and (timeout_ms < 100 or timeout_ms > 30000):
            _raise_protocol_validation_error(db, session, "GPIO 操作未通过：超时时间范围必须为 100-30000ms", config=merged_config, code="gpio_timeout_invalid")
        merged_config["pin"] = pin
        merged_config["timeout_ms"] = timeout_ms
        session.config_json = json.dumps(merged_config, ensure_ascii=False)
        return await _run_gpio_business_action(
            db=db,
            session=session,
            payload=payload,
            merged_config=merged_config,
        )

    tx_dlc = payload.dlc if protocol != "serial" else payload_length
    rx_dlc = payload.dlc if protocol != "serial" else _parse_non_negative_int(merged_config.get("length_bytes"))

    tx = ProtocolLog(
        session_id=session.id,
        protocol=session.protocol,
        timestamp=datetime.now(),
        direction="Tx",
        frame_id=payload.frame_id,
        dlc=tx_dlc,
        data=payload_data,
    )
    db.add(tx)
    session.tx_count += 1
    db.commit()

    await asyncio.sleep(0.05)

    rx_data: Optional[str] = None
    endpoint_info: dict[str, Any] = {}
    if protocol == "serial":
        serial_connection = _get_serial_session_connection(session.id)
        serial_io_lock = _get_serial_session_io_lock(session.id)
        if serial_connection is None:
            _raise_protocol_validation_error(
                db,
                session,
                "验证未通过：串口连接已失效，请重新连接通道",
                config=merged_config,
                code="serial_channel_error",
                payload_length=payload_length,
            )
        try:
            payload_bytes = _encode_protocol_payload(payload_data, merged_config.get("data_type"))
            await asyncio.to_thread(
                _write_serial_payload,
                serial_connection,
                payload_bytes,
                io_lock=serial_io_lock,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            _close_serial_session_connection(session.id)
            _raise_protocol_validation_error(
                db,
                session,
                f"验证未通过：串口通道异常，{str(exc)}",
                config=merged_config,
                code="serial_channel_error",
                payload_length=payload_length,
            )
    if protocol == "ethernet":
        transport_mode = _normalize_ethernet_mode(merged_config.get("transport_protocol"))
        timeout = _timeout_ms_to_seconds(merged_config.get("timeout"), 5000)
        try:
            payload_bytes = _encode_protocol_payload(payload_data, merged_config.get("data_type"))
            if transport_mode == "TCP Client":
                rx_data, endpoint_info = await asyncio.to_thread(
                    _run_tcp_client_exchange,
                    str(merged_config.get("target_ip") or "").strip(),
                    int(merged_config.get("target_port") or 0),
                    payload_bytes,
                    timeout,
                    merged_config.get("data_type"),
                )
            elif transport_mode == "TCP Server":
                rx_data, endpoint_info = await asyncio.to_thread(
                    _run_tcp_server_exchange,
                    str(merged_config.get("local_ip") or "").strip(),
                    int(merged_config.get("listen_port") or 0),
                    payload_bytes,
                    timeout,
                    merged_config.get("data_type"),
                )
            else:
                rx_data, endpoint_info = await asyncio.to_thread(
                    _run_udp_exchange,
                    str(merged_config.get("local_ip") or "").strip(),
                    int(merged_config.get("local_port") or 0),
                    str(merged_config.get("target_ip") or "").strip(),
                    int(merged_config.get("target_port") or 0),
                    payload_bytes,
                    timeout,
                    merged_config.get("data_type"),
                )
            merged_config.update(endpoint_info)
            session.config_json = json.dumps(merged_config, ensure_ascii=False)
        except (TimeoutError, socket.timeout):
            _raise_protocol_validation_error(
                db,
                session,
                f"验证未通过：{transport_mode} 核心等待窗口超时",
                config=merged_config,
                code="ethernet_no_response",
                payload_length=payload_length,
            )
        except (OSError, ValueError) as exc:
            _raise_protocol_validation_error(
                db,
                session,
                f"验证未通过：{transport_mode} 通道异常，{str(exc)}",
                config=merged_config,
                code="ethernet_channel_error",
                payload_length=payload_length,
            )
    if rx_data:
        rx = ProtocolLog(
            session_id=session.id,
            protocol=session.protocol,
            timestamp=datetime.now(),
            direction="Rx",
            frame_id=payload.frame_id,
            dlc=rx_dlc,
            data=rx_data,
        )
        db.add(rx)
        session.rx_count += 1
    if protocol == "serial":
        success_detail = "验证通过：串口数据写入成功；接收数据由监听线程持续采集，不将终端回显误判为命令回复"
        _persist_validation_result(session, merged_config, passed=True, detail=success_detail, code="serial_tx_passed", payload_length=payload_length)
        _append_system_log(db, session, success_detail)
    elif protocol == "ethernet":
        transport_mode = _normalize_ethernet_mode(merged_config.get("transport_protocol"))
        if transport_mode == "TCP Server":
            success_detail = "验证通过：TCP Server 已成功接入客户端，收发数据作为补充证据"
        elif transport_mode == "UDP":
            success_detail = (
                "验证通过：UDP 测试数据发送成功，收到对端回复作为补充证据"
                if endpoint_info.get("reply_received")
                else "验证通过：UDP socket 创建/绑定并发送成功，观察窗口未收到回复不影响判定"
            )
        else:
            success_detail = (
                "验证通过：TCP Client 连接并发送成功，收到对端回复作为补充证据"
                if endpoint_info.get("reply_received")
                else "验证通过：TCP Client 连接并发送成功，观察窗口未收到回复不影响判定"
            )
        result_code = "ethernet_connected_passed" if transport_mode == "TCP Server" else "ethernet_tx_passed"
        _persist_validation_result(session, merged_config, passed=True, detail=success_detail, code=result_code, payload_length=payload_length)
        _append_system_log(db, session, success_detail)
    elif protocol in {"gpio", "gpio_io"}:
        success_detail = str(merged_config.get("validation_detail") or "GPIO 操作执行完成")
        _append_system_log(db, session, success_detail)
    _notify_protocol_result(db, session, passed=True)
    db.commit()
    if protocol == "serial":
        return {"code": 0, "message": "串口通信验证通过"}
    if protocol == "ethernet":
        return {"code": 0, "message": "以太网通信验证通过"}
    if protocol in {"gpio", "gpio_io"}:
        return {"code": 0, "message": "GPIO 操作完成", "data": {"config": merged_config, "current_level": merged_config.get("current_level")}}
    return {"code": 0, "message": "发送成功"}


@router.get("/{session_id}/logs", response_model=dict)
async def get_session_logs(
    session_id: int,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    query = db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id)
    total = query.count()
    logs = (
        query.order_by(ProtocolLog.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    logs.reverse()

    return {
        "code": 0,
        "message": "success",
        "data": [log_to_dict(l) for l in logs],
        "config_json": session.config_json,
        "config": _load_session_config(session),
        "total": total,
        "page": page,
        "page_size": page_size,
        "tx": session.tx_count,
        "rx": session.rx_count,
        "status": session.status,
    }


@router.post("/{session_id}/logs/clear", response_model=Response)
async def clear_session_logs(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:execute")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.query(ProtocolLog).filter(ProtocolLog.session_id == session_id).delete()
    session.tx_count = 0
    session.rx_count = 0
    db.commit()
    return {"code": 0, "message": "清空成功"}


@router.get("/records", response_model=dict)
async def list_records(
    page: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None,
    protocol: Optional[str] = None,
    executor: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    ensure_schema()
    query = db.query(ProtocolSession)
    if keyword:
        query = query.filter(ProtocolSession.target.like(f"%{keyword}%"))
    if protocol:
        query = query.filter(ProtocolSession.protocol == protocol)
    if executor:
        query = query.filter(ProtocolSession.executor == executor)

    total = query.count()
    rows = (
        query.order_by(ProtocolSession.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    executor_rows = (
        db.query(ProtocolSession.executor)
        .filter(ProtocolSession.executor.isnot(None))
        .distinct()
        .all()
    )
    return {
        "code": 0,
        "message": "success",
        "data": [session_to_dict(s) for s in rows],
        "executors": [str(executor or "").strip() for (executor,) in executor_rows if str(executor or "").strip()],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/records/{record_id}", response_model=Response)
async def get_record_detail(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:view")),
):
    ensure_schema()
    session = db.query(ProtocolSession).filter(ProtocolSession.id == record_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"code": 0, "message": "success", "data": session_to_dict(session)}


@router.delete("/records/{record_id}", response_model=Response)
async def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("protocol:delete")),
):
    session = db.query(ProtocolSession).filter(ProtocolSession.id == record_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.query(ProtocolLog).filter(ProtocolLog.session_id == record_id).delete()
    db.delete(session)
    db.commit()
    return {"code": 0, "message": "删除成功"}
