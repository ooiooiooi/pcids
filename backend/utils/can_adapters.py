import ctypes
import json
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:  # pragma: no cover
    serial = None
    list_ports = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_ADAPTERS_ROOT = PROJECT_ROOT / "tools" / "protocol_adapters"
USBCANFD_200U_ROOT = PROTOCOL_ADAPTERS_ROOT / "USBCANFD-200U"
USBCANFD_200U_MANIFEST = USBCANFD_200U_ROOT / "sdk-manifest.json"
USBCANFD_200U_HARDWARE_IDS = ("USB\\VID_3068&PID_0009",)
USBCANFD_200U_DEVICE_TYPE = 41
USBCANFD_200U_MAX_DEVICE_INDEX = 32
ZQWL_UCAN_CDC_VID = 0x3562
ZQWL_UCAN_CDC_PID_CHANNELS = {
    0x0102: ("CAN0", "CAN1"),
    0x0103: ("CAN0",),
    0x0104: ("CAN0", "CAN1", "CAN2", "CAN3"),
}
ZQWL_UCAN_CDC_COMMAND_HEADER = b"\x49\x3B"
ZQWL_UCAN_CDC_COMMAND_TAIL = b"\x45\x2E"
ZQWL_UCAN_CDC_CAN_FRAME_PREFIX = 0x5A
ZQWL_UCAN_CDC_HEARTBEAT_SHORT_MARKER = 0xFF
ZQWL_UCAN_CDC_HEARTBEAT_LONG_MARKER = 0xFE
ZQWL_UCAN_CDC_HEARTBEAT_SHORT_LENGTH = 17
ZQWL_UCAN_CDC_HEARTBEAT_LONG_LENGTH = 32
ZQWL_UCAN_CDC_SERIAL_BAUDRATE = 6_000_000
ZQWL_UCAN_CDC_SERIAL_TIMEOUT_SECONDS = 0.05
STATUS_OK = 1
TYPE_CAN = 0
TYPE_CANFD = 1
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ID_MASK = 0x1FFFFFFF
CANFD_BRS = 0x01
logger = logging.getLogger(__name__)

CLASSICAL_CAN_ALLOWED_LENGTHS = tuple(range(0, 9))
CAN_FD_ALLOWED_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
CAN_FD_LENGTH_TO_DLC = {
    0: 0,
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
    6: 6,
    7: 7,
    8: 8,
    12: 9,
    16: 10,
    20: 11,
    24: 12,
    32: 13,
    48: 14,
    64: 15,
}
CAN_FD_DLC_TO_LENGTH = {value: key for key, value in CAN_FD_LENGTH_TO_DLC.items()}


class CanAdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CanDependencyMissingError(CanAdapterError):
    def __init__(self, message: str):
        super().__init__("dependency_missing", message)


@dataclass
class CanFrame:
    frame_id: int
    is_extended_id: bool
    is_fd: bool
    bitrate_switch: bool
    is_remote_frame: bool
    data: bytes
    declared_data_length: int
    channel_name: str
    timestamp: Optional[float] = None

    def __post_init__(self) -> None:
        self.declared_data_length = validate_can_length("canfd" if self.is_fd else "can", self.declared_data_length)
        if self.is_fd and self.is_remote_frame:
            raise ValueError("CAN FD 不支持远程帧")
        if self.is_remote_frame:
            if self.data:
                raise ValueError("远程帧不能携带数据")
            return
        if len(self.data) != self.declared_data_length:
            raise ValueError("普通数据帧的数据长度必须与声明长度完全一致")

    @property
    def data_length(self) -> int:
        return len(self.data)

    @property
    def dlc(self) -> int:
        if self.is_fd:
            return can_fd_length_to_dlc(self.declared_data_length)
        return self.declared_data_length


@dataclass
class CanChannelDescriptor:
    name: str
    index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "index": self.index,
            "label": self.name,
        }


@dataclass
class CanAdapterDevice:
    adapter_key: str
    backend_key: str
    adapter_name: str
    serial_number: str
    pnp_device_id: str
    hardware_ids: list[str]
    description: str
    manufacturer: str = ""
    dependency_status: str = "ready"
    dependency_message: str = ""
    channels: list[CanChannelDescriptor] = field(default_factory=list)
    sdk_device_index: Optional[int] = None
    adapter_device: str = ""
    vid: Optional[int] = None
    pid: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_key": self.adapter_key,
            "backend_key": self.backend_key,
            "adapter_name": self.adapter_name,
            "label": self.adapter_name,
            "serial_number": self.serial_number,
            "device": self.adapter_device or self.pnp_device_id,
            "adapter_device": self.adapter_device or self.pnp_device_id,
            "description": self.description or self.adapter_name,
            "manufacturer": self.manufacturer,
            "source": "windows_pnp",
            "dependency_status": self.dependency_status,
            "dependency_message": self.dependency_message,
            "channels": [channel.to_dict() for channel in self.channels],
            "channel_count": len(self.channels),
            "hardware_ids": list(self.hardware_ids),
            "pnp_device_id": self.pnp_device_id,
            "sdk_device_index": self.sdk_device_index,
            "vid": self.vid,
            "pid": self.pid,
        }


@dataclass
class CanAdapterConnection:
    backend_key: str
    device: CanAdapterDevice
    device_handle: Any
    channel_handle: Any
    channel_name: str
    protocol: str
    channel_guard_key: Optional[str] = None


class CanAdapterBackend(ABC):
    backend_key: str
    adapter_name: str
    supported_protocols: frozenset[str] = frozenset({"can", "canfd"})

    @abstractmethod
    def enumerate_devices(self) -> list[CanAdapterDevice]:
        raise NotImplementedError

    @abstractmethod
    def open_device(self, device: CanAdapterDevice) -> Any:
        raise NotImplementedError

    @abstractmethod
    def init_channel(self, device_handle: Any, channel_name: str, *, protocol: str, config: dict[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    def start_channel(self, device_handle: Any, channel_handle: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def transmit(self, connection: CanAdapterConnection, frame: CanFrame) -> None:
        raise NotImplementedError

    @abstractmethod
    def receive(
        self,
        connection: CanAdapterConnection,
        *,
        timeout_ms: int,
        expected_rx_id: Optional[int] = None,
        expected_rx_mask: Optional[int] = None,
    ) -> list[CanFrame]:
        raise NotImplementedError

    @abstractmethod
    def stop_channel(self, device_handle: Any, channel_handle: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def close_device(self, device_handle: Any) -> None:
        raise NotImplementedError


class _ZCAN_VERSION(ctypes.Structure):
    _fields_ = [
        ("major_version", ctypes.c_ubyte),
        ("minor_version", ctypes.c_ubyte),
        ("patch_version", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
    ]


class _ZCAN_DEVICE_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("hardware_version", _ZCAN_VERSION),
        ("firmware_version", _ZCAN_VERSION),
        ("driver_version", _ZCAN_VERSION),
        ("library_version", _ZCAN_VERSION),
        ("device_name", ctypes.c_ubyte * 128),
        ("hardware_type", ctypes.c_ubyte * 40),
        ("serial_number", ctypes.c_ubyte * 20),
        ("can_channel_number", ctypes.c_ubyte),
        ("lin_channel_number", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte * 46),
        ("device_info_version", _ZCAN_VERSION),
    ]


class _ZCAN_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("hw_Version", ctypes.c_ushort),
        ("fw_Version", ctypes.c_ushort),
        ("dr_Version", ctypes.c_ushort),
        ("in_Version", ctypes.c_ushort),
        ("irq_Num", ctypes.c_ushort),
        ("can_Num", ctypes.c_ubyte),
        ("str_Serial_Num", ctypes.c_ubyte * 20),
        ("str_hw_Type", ctypes.c_ubyte * 40),
        ("reserved", ctypes.c_ushort * 4),
    ]


class _ZCAN_CHANNEL_CAN_INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("acc_code", ctypes.c_uint),
        ("acc_mask", ctypes.c_uint),
        ("reserved", ctypes.c_uint),
        ("filter", ctypes.c_ubyte),
        ("timing0", ctypes.c_ubyte),
        ("timing1", ctypes.c_ubyte),
        ("mode", ctypes.c_ubyte),
    ]


class _ZCAN_CHANNEL_CANFD_INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("acc_code", ctypes.c_uint),
        ("acc_mask", ctypes.c_uint),
        ("abit_timing", ctypes.c_uint),
        ("dbit_timing", ctypes.c_uint),
        ("brp", ctypes.c_uint),
        ("filter", ctypes.c_ubyte),
        ("mode", ctypes.c_ubyte),
        ("pad", ctypes.c_ushort),
        ("reserved", ctypes.c_uint),
    ]


class _ZCAN_CHANNEL_INIT_UNION(ctypes.Union):
    _fields_ = [
        ("can", _ZCAN_CHANNEL_CAN_INIT_CONFIG),
        ("canfd", _ZCAN_CHANNEL_CANFD_INIT_CONFIG),
    ]


class _ZCAN_CHANNEL_INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("can_type", ctypes.c_uint),
        ("config", _ZCAN_CHANNEL_INIT_UNION),
    ]


class _CAN_FRAME(ctypes.Structure):
    _fields_ = [
        ("can_id", ctypes.c_uint),
        ("can_dlc", ctypes.c_ubyte),
        ("__pad", ctypes.c_ubyte),
        ("__res0", ctypes.c_ubyte),
        ("__res1", ctypes.c_ubyte),
        ("data", ctypes.c_ubyte * 8),
    ]


class _CANFD_FRAME(ctypes.Structure):
    _fields_ = [
        ("can_id", ctypes.c_uint),
        ("len", ctypes.c_ubyte),
        ("flags", ctypes.c_ubyte),
        ("__res0", ctypes.c_ubyte),
        ("__res1", ctypes.c_ubyte),
        ("data", ctypes.c_ubyte * 64),
    ]


class _ZCAN_TRANSMIT_DATA(ctypes.Structure):
    _fields_ = [
        ("frame", _CAN_FRAME),
        ("transmit_type", ctypes.c_uint),
    ]


class _ZCAN_RECEIVE_DATA(ctypes.Structure):
    _fields_ = [
        ("frame", _CAN_FRAME),
        ("timestamp", ctypes.c_uint64),
    ]


class _ZCAN_TRANSMIT_FD_DATA(ctypes.Structure):
    _fields_ = [
        ("frame", _CANFD_FRAME),
        ("transmit_type", ctypes.c_uint),
    ]


class _ZCAN_RECEIVE_FD_DATA(ctypes.Structure):
    _fields_ = [
        ("frame", _CANFD_FRAME),
        ("timestamp", ctypes.c_uint64),
    ]


@dataclass
class _UsbcanfdDeviceHandle:
    api: "_ZlgCanApi"
    raw_handle: ctypes.c_void_p
    device_index: int


@dataclass
class _UsbcanfdChannelHandle:
    api: "_ZlgCanApi"
    raw_handle: ctypes.c_void_p
    channel_index: int
    channel_name: str
    protocol: str


@dataclass
class _UsbcanfdRuntimeDevice:
    device_index: int
    serial_number: str
    can_channel_count: int
    device_name: str
    hardware_type: str


@dataclass
class _ZqwlSerialHandle:
    connection: Any
    port: str
    serial_number: str
    device_info_payload: bytes = b""
    serial_response_payload: bytes = b""


@dataclass
class _ZqwlChannelHandle:
    channel_index: int
    channel_name: str
    protocol: str
    receive_buffer: bytearray = field(default_factory=bytearray)


class _ZlgCanApi:
    def __init__(self, dll_path: Path, dll_directory_handles: list[Any]):
        self.dll_path = dll_path
        self.dll_directory_handles = dll_directory_handles
        self.dll = ctypes.WinDLL(str(dll_path))
        self._bind_functions()

    def _bind_functions(self) -> None:
        self.ZCAN_OpenDevice = self.dll.ZCAN_OpenDevice
        self.ZCAN_OpenDevice.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        self.ZCAN_OpenDevice.restype = ctypes.c_void_p

        self.ZCAN_CloseDevice = self.dll.ZCAN_CloseDevice
        self.ZCAN_CloseDevice.argtypes = [ctypes.c_void_p]
        self.ZCAN_CloseDevice.restype = ctypes.c_uint

        self.ZCAN_GetDeviceInfoEx = self.dll.ZCAN_GetDeviceInfoEx
        self.ZCAN_GetDeviceInfoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZCAN_DEVICE_INFO_EX)]
        self.ZCAN_GetDeviceInfoEx.restype = ctypes.c_uint

        self.ZCAN_GetDeviceInf = self.dll.ZCAN_GetDeviceInf
        self.ZCAN_GetDeviceInf.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZCAN_DEVICE_INFO)]
        self.ZCAN_GetDeviceInf.restype = ctypes.c_uint

        self.ZCAN_InitCAN = self.dll.ZCAN_InitCAN
        self.ZCAN_InitCAN.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(_ZCAN_CHANNEL_INIT_CONFIG)]
        self.ZCAN_InitCAN.restype = ctypes.c_void_p

        self.ZCAN_StartCAN = self.dll.ZCAN_StartCAN
        self.ZCAN_StartCAN.argtypes = [ctypes.c_void_p]
        self.ZCAN_StartCAN.restype = ctypes.c_uint

        self.ZCAN_ResetCAN = self.dll.ZCAN_ResetCAN
        self.ZCAN_ResetCAN.argtypes = [ctypes.c_void_p]
        self.ZCAN_ResetCAN.restype = ctypes.c_uint

        self.ZCAN_Transmit = self.dll.ZCAN_Transmit
        self.ZCAN_Transmit.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZCAN_TRANSMIT_DATA), ctypes.c_uint]
        self.ZCAN_Transmit.restype = ctypes.c_uint

        self.ZCAN_Receive = self.dll.ZCAN_Receive
        self.ZCAN_Receive.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZCAN_RECEIVE_DATA), ctypes.c_uint, ctypes.c_int]
        self.ZCAN_Receive.restype = ctypes.c_uint

        self.ZCAN_TransmitFD = self.dll.ZCAN_TransmitFD
        self.ZCAN_TransmitFD.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZCAN_TRANSMIT_FD_DATA), ctypes.c_uint]
        self.ZCAN_TransmitFD.restype = ctypes.c_uint

        self.ZCAN_ReceiveFD = self.dll.ZCAN_ReceiveFD
        self.ZCAN_ReceiveFD.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ZCAN_RECEIVE_FD_DATA), ctypes.c_uint, ctypes.c_int]
        self.ZCAN_ReceiveFD.restype = ctypes.c_uint

        self.ZCAN_SetValue = self.dll.ZCAN_SetValue
        self.ZCAN_SetValue.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        self.ZCAN_SetValue.restype = ctypes.c_uint


def can_fd_length_to_dlc(length: int) -> int:
    try:
        normalized = int(length)
    except (TypeError, ValueError) as exc:
        raise ValueError("CAN FD 数据长度无效") from exc
    if normalized not in CAN_FD_LENGTH_TO_DLC:
        raise ValueError("CAN FD 数据长度仅支持 0~8、12、16、20、24、32、48、64 字节")
    return CAN_FD_LENGTH_TO_DLC[normalized]


def can_fd_dlc_to_length(dlc: int) -> int:
    try:
        normalized = int(dlc)
    except (TypeError, ValueError) as exc:
        raise ValueError("CAN FD DLC 无效") from exc
    if normalized not in CAN_FD_DLC_TO_LENGTH:
        raise ValueError("CAN FD DLC 必须在 0~15 范围内")
    return CAN_FD_DLC_TO_LENGTH[normalized]


def parse_can_frame_id(value: Any, *, is_extended: bool) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError("帧 ID 不能为空")
    try:
        frame_id = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError as exc:
        raise ValueError("帧 ID 必须是合法的十六进制或十进制数值") from exc
    if frame_id < 0:
        raise ValueError("帧 ID 不能为负数")
    max_value = 0x1FFFFFFF if is_extended else 0x7FF
    if frame_id > max_value:
        if is_extended:
            raise ValueError("扩展帧 ID 范围必须为 0~0x1FFFFFFF")
        raise ValueError("标准帧 ID 范围必须为 0~0x7FF")
    return frame_id


def validate_can_length(protocol: str, length_bytes: int) -> int:
    try:
        normalized = int(length_bytes)
    except (TypeError, ValueError) as exc:
        raise ValueError("数据长度必须是整数") from exc
    if protocol == "can":
        if normalized not in CLASSICAL_CAN_ALLOWED_LENGTHS:
            raise ValueError("Classical CAN 数据长度必须为 0~8 字节")
        return normalized
    if protocol == "canfd":
        if normalized not in CAN_FD_ALLOWED_LENGTHS:
            raise ValueError("CAN FD 数据长度仅支持 0~8、12、16、20、24、32、48、64 字节")
        return normalized
    raise ValueError("仅支持 CAN 或 CAN FD 协议长度校验")


def parse_can_mask(value: Any, *, is_extended: bool) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    return parse_can_frame_id(text, is_extended=is_extended)


def match_expected_rx_frame(
    frame: CanFrame,
    *,
    expected_rx_id: Optional[int],
    expected_rx_mask: Optional[int],
    expected_data: Optional[bytes],
    expected_is_extended_id: Optional[bool] = None,
    expected_is_fd: Optional[bool] = None,
    expected_is_remote_frame: Optional[bool] = None,
    expected_bitrate_switch: Optional[bool] = None,
) -> bool:
    if expected_is_extended_id is not None and frame.is_extended_id != expected_is_extended_id:
        return False
    if expected_is_fd is not None and frame.is_fd != expected_is_fd:
        return False
    if expected_is_remote_frame is not None and frame.is_remote_frame != expected_is_remote_frame:
        return False
    if expected_bitrate_switch is not None and frame.bitrate_switch != expected_bitrate_switch:
        return False
    if expected_rx_id is not None:
        mask = expected_rx_mask if expected_rx_mask is not None else (0x1FFFFFFF if frame.is_extended_id else 0x7FF)
        if (frame.frame_id & mask) != (expected_rx_id & mask):
            return False
    if expected_data is not None and frame.data != expected_data:
        return False
    return True


def _extract_serial_number(instance_id: str) -> str:
    parts = [part.strip() for part in str(instance_id or "").split("\\") if part.strip()]
    if not parts:
        return ""
    return parts[-1]


def _normalize_device_identifier(value: str) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _resolve_usbcanfd_runtime_device(
    serial_number: str,
    runtime_devices_by_serial: dict[str, "_UsbcanfdRuntimeDevice"],
) -> Optional["_UsbcanfdRuntimeDevice"]:
    if serial_number in runtime_devices_by_serial:
        return runtime_devices_by_serial[serial_number]

    normalized_serial = _normalize_device_identifier(serial_number)
    if not normalized_serial:
        return None

    for runtime_device in runtime_devices_by_serial.values():
        runtime_serial = _normalize_device_identifier(runtime_device.serial_number)
        if not runtime_serial:
            continue
        if runtime_serial == normalized_serial:
            return runtime_device
        if runtime_serial.endswith(normalized_serial) or normalized_serial.endswith(runtime_serial):
            return runtime_device
    return None


def _split_multi_sz(raw_buffer: str) -> list[str]:
    return [item.strip() for item in str(raw_buffer or "").split("\x00") if item.strip()]


def _decode_c_string(raw_value: Any) -> str:
    return bytes(raw_value).split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()


def _decode_zqwl_ascii(raw_value: bytes) -> str:
    return bytes(raw_value or b"").split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def _decode_zqwl_serial_number(raw_value: bytes) -> str:
    payload = bytes(raw_value or b"").split(b"\x00", 1)[0]
    if not payload:
        return ""
    if any(item < 0x20 or item > 0x7E for item in payload):
        return ""
    return payload.decode("ascii", errors="ignore").strip()


def _make_can_id(frame_id: int, *, is_extended: bool, is_remote: bool) -> int:
    value = int(frame_id) & CAN_ID_MASK
    if is_extended:
        value |= CAN_EFF_FLAG
    if is_remote:
        value |= CAN_RTR_FLAG
    return value


def _parse_received_can_id(can_id: int) -> tuple[int, bool, bool]:
    raw_value = int(can_id)
    return raw_value & CAN_ID_MASK, bool(raw_value & CAN_EFF_FLAG), bool(raw_value & CAN_RTR_FLAG)


def _channel_name_to_index(channel_name: str) -> int:
    normalized = str(channel_name or "").strip().upper()
    if normalized.startswith("CAN") and normalized[3:].isdigit():
        return int(normalized[3:])
    raise CanAdapterError("channel_not_found", f"无法识别物理通道 {channel_name}")


def _build_zqwl_command(function_code: int, rw_flag: int, data: bytes) -> bytes:
    payload = bytes(data or b"")
    if len(payload) != 16:
        raise CanAdapterError("config_invalid", "ZQWL 配置命令的数据区必须恰好为 16 字节")
    return (
        ZQWL_UCAN_CDC_COMMAND_HEADER
        + bytes((int(function_code) & 0xFF, int(rw_flag) & 0xFF))
        + payload
        + ZQWL_UCAN_CDC_COMMAND_TAIL
    )


def _extract_serial_number_from_port_info(port_info: Any) -> str:
    text = str(getattr(port_info, "serial_number", None) or "").strip()
    if text:
        return text
    hwid = str(getattr(port_info, "hwid", "") or "")
    match = re.search(r"SER=([A-Za-z0-9]+)", hwid)
    return match.group(1).strip() if match else ""


def _read_zqwl_serial_chunk(connection: Any, *, timeout_ms: int) -> bytes:
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    while time.monotonic() < deadline:
        waiting = int(getattr(connection, "in_waiting", 0) or 0)
        read_size = waiting or 1
        chunk = bytes(connection.read(read_size))
        if chunk:
            return chunk
    return b""


def _try_parse_zqwl_can_frame(buffer: bytearray) -> tuple[Optional[CanFrame], int]:
    if len(buffer) < 3 or buffer[0] != ZQWL_UCAN_CDC_CAN_FRAME_PREFIX:
        return None, 0
    marker = buffer[1]
    if marker == ZQWL_UCAN_CDC_HEARTBEAT_SHORT_MARKER:
        if len(buffer) < ZQWL_UCAN_CDC_HEARTBEAT_SHORT_LENGTH:
            return None, 0
        return None, ZQWL_UCAN_CDC_HEARTBEAT_SHORT_LENGTH
    if marker == ZQWL_UCAN_CDC_HEARTBEAT_LONG_MARKER:
        if len(buffer) < ZQWL_UCAN_CDC_HEARTBEAT_LONG_LENGTH:
            return None, 0
        return None, ZQWL_UCAN_CDC_HEARTBEAT_LONG_LENGTH

    byte1 = int(buffer[1])
    byte2 = int(buffer[2])
    dlc = byte1 & 0x7F
    channel_index = ((byte2 >> 3) & 0x03) << 1 | ((byte1 >> 7) & 0x01)
    is_extended = bool(byte2 & 0x04)
    is_remote = bool(byte2 & 0x02)
    raw_can_id = int.from_bytes(buffer[3:7], byteorder="big")
    is_fd = bool(raw_can_id & 0x80000000)
    frame_id = raw_can_id & CAN_ID_MASK
    bitrate_switch = bool(byte2 & 0x01)
    try:
        payload_length = 0 if is_remote else (can_fd_dlc_to_length(dlc) if is_fd else dlc)
    except ValueError as exc:
        raise CanAdapterError("rx_invalid_frame", f"ZQWL 返回了非法 DLC：{dlc}") from exc
    expected_length = 8 + payload_length
    if len(buffer) < expected_length:
        return None, 0
    if buffer[expected_length - 1] != 0xA5:
        raise CanAdapterError("rx_invalid_frame", "ZQWL 返回了非法经典 CAN 帧，结束字节不是 0xA5")
    payload = b"" if is_remote else bytes(buffer[7 : 7 + payload_length])
    try:
        return (
            CanFrame(
                frame_id=frame_id,
                is_extended_id=is_extended,
                is_fd=is_fd,
                bitrate_switch=bitrate_switch if is_fd else False,
                is_remote_frame=is_remote,
                data=payload,
                declared_data_length=dlc,
                channel_name=f"CAN{channel_index}",
            ),
            expected_length,
        )
    except ValueError as exc:
        raise CanAdapterError("rx_invalid_frame", f"ZQWL 返回了非法 CAN 帧：{exc}") from exc


def _drain_zqwl_can_frames(buffer: bytearray) -> list[CanFrame]:
    frames: list[CanFrame] = []
    while buffer:
        if buffer[0] != ZQWL_UCAN_CDC_CAN_FRAME_PREFIX:
            buffer.pop(0)
            continue
        try:
            frame, consumed = _try_parse_zqwl_can_frame(buffer)
        except CanAdapterError:
            logger.debug("Discarding invalid ZQWL CAN frame while draining receive buffer", exc_info=True)
            buffer.pop(0)
            continue
        if consumed <= 0:
            break
        del buffer[:consumed]
        if frame is not None:
            frames.append(frame)
    return frames


def _read_zqwl_command_response(connection: Any, *, function_code: int, rw_flag: int, timeout_ms: int) -> bytes:
    buffer = bytearray()
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    while time.monotonic() < deadline:
        chunk = _read_zqwl_serial_chunk(connection, timeout_ms=min(20, timeout_ms))
        if chunk:
            buffer.extend(chunk)
        while len(buffer) >= 22:
            if buffer[0:2] == ZQWL_UCAN_CDC_COMMAND_HEADER:
                candidate = bytes(buffer[:22])
                del buffer[:22]
                if candidate[-2:] != ZQWL_UCAN_CDC_COMMAND_TAIL:
                    raise CanAdapterError("config_invalid_response", "ZQWL 配置响应尾标识无效")
                if candidate[2] != (function_code & 0xFF) or candidate[3] != (rw_flag & 0xFF):
                    continue
                return candidate
            if buffer[0] == ZQWL_UCAN_CDC_CAN_FRAME_PREFIX:
                try:
                    _, consumed = _try_parse_zqwl_can_frame(buffer)
                except CanAdapterError:
                    consumed = 1
                if consumed <= 0:
                    break
                del buffer[:consumed]
                continue
            buffer.pop(0)
    raise CanAdapterError("config_timeout", f"等待 ZQWL 配置响应超时，功能码=0x{function_code:02X}")


def _transceive_zqwl_command(connection: Any, *, function_code: int, rw_flag: int, data: bytes, timeout_ms: int = 500) -> bytes:
    command = _build_zqwl_command(function_code, rw_flag, data)
    try:
        if hasattr(connection, "write_timeout"):
            connection.write_timeout = max(timeout_ms / 1000.0, 0.2)
        connection.write(command)
        if hasattr(connection, "flush"):
            connection.flush()
    except Exception as exc:
        raise CanAdapterError("config_io_error", f"ZQWL 配置命令发送失败：{exc}") from exc
    return _read_zqwl_command_response(connection, function_code=function_code, rw_flag=rw_flag, timeout_ms=timeout_ms)


def _normalize_zqwl_classic_can_bitrate_code(value: Any) -> int:
    bitrate = _normalize_can_bitrate(value, field_label="波特率")
    mapping = {
        "1000000": 0x00,
        "500000": 0x20,
        "250000": 0x40,
        "125000": 0x60,
    }
    if bitrate not in mapping:
        raise CanAdapterError("config_invalid", "ZQWL 经典 CAN 当前仅支持 1000kbps、500kbps、250kbps、125kbps")
    return mapping[bitrate]


_ACTIVE_CAN_CHANNELS: dict[str, str] = {}
_ACTIVE_CAN_CHANNELS_LOCK = threading.Lock()


def _build_channel_guard_key(adapter_key: str, physical_channel: str) -> str:
    return f"{str(adapter_key or '').strip()}::{str(physical_channel or '').strip().upper()}"


def _reserve_channel_guard(channel_guard_key: str) -> None:
    with _ACTIVE_CAN_CHANNELS_LOCK:
        owner = _ACTIVE_CAN_CHANNELS.get(channel_guard_key)
        if owner is not None:
            raise CanAdapterError("channel_busy", "该物理通道已被占用，请先断开现有连接后重试")
        _ACTIVE_CAN_CHANNELS[channel_guard_key] = channel_guard_key


def _release_channel_guard(channel_guard_key: Optional[str]) -> None:
    if not channel_guard_key:
        return
    with _ACTIVE_CAN_CHANNELS_LOCK:
        _ACTIVE_CAN_CHANNELS.pop(channel_guard_key, None)


def _get_channel_descriptor(device: CanAdapterDevice, channel_name: str) -> CanChannelDescriptor:
    for channel in device.channels:
        if channel.name == channel_name:
            return channel
    raise CanAdapterError("channel_not_found", f"{device.adapter_name} 未提供物理通道 {channel_name}")


def _normalize_can_bitrate(value: Any, *, field_label: str) -> str:
    if value is None:
        raise CanAdapterError("config_invalid", f"{field_label} 配置无效")
    if isinstance(value, bool):
        raise CanAdapterError("config_invalid", f"{field_label} 配置无效")
    if isinstance(value, int):
        bitrate = value
    else:
        text = str(value).strip()
        if not text:
            raise CanAdapterError("config_invalid", f"{field_label} 配置无效")
        if text.isdigit():
            bitrate = int(text)
        else:
            normalized_text = re.sub(r"\s+", "", text).lower()
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(k|m)bps", normalized_text)
            if not match:
                raise CanAdapterError("config_invalid", f"{field_label} 配置无效")
            magnitude = float(match.group(1))
            multiplier = 1_000 if match.group(2) == "k" else 1_000_000
            bitrate = int(magnitude * multiplier)
    if bitrate <= 0:
        raise CanAdapterError("config_invalid", f"{field_label} 必须大于 0")
    return str(bitrate)


def _normalize_bool_config(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _enumerate_windows_pnp_devices() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []

    from ctypes import wintypes

    setupapi = ctypes.WinDLL("setupapi")

    DIGCF_PRESENT = 0x2
    DIGCF_ALLCLASSES = 0x4
    SPDRP_DEVICEDESC = 0x00000000
    SPDRP_HARDWAREID = 0x00000001
    SPDRP_MFG = 0x0000000B
    SPDRP_FRIENDLYNAME = 0x0000000C
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    ERROR_NO_MORE_ITEMS = 259

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("ClassGuid", GUID),
            ("DevInst", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(SP_DEVINFO_DATA),
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(SP_DEVINFO_DATA),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_byte),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL

    def get_instance_id(device_info_set: Any, device_info_data: SP_DEVINFO_DATA) -> str:
        required_size = wintypes.DWORD(0)
        setupapi.SetupDiGetDeviceInstanceIdW(device_info_set, ctypes.byref(device_info_data), None, 0, ctypes.byref(required_size))
        if required_size.value <= 1:
            return ""
        buffer = ctypes.create_unicode_buffer(required_size.value)
        ok = setupapi.SetupDiGetDeviceInstanceIdW(
            device_info_set,
            ctypes.byref(device_info_data),
            buffer,
            required_size.value,
            ctypes.byref(required_size),
        )
        return buffer.value.strip() if ok else ""

    def get_registry_property_strings(device_info_set: Any, device_info_data: SP_DEVINFO_DATA, prop: int) -> list[str]:
        required_size = wintypes.DWORD(0)
        reg_type = wintypes.DWORD(0)
        setupapi.SetupDiGetDeviceRegistryPropertyW(
            device_info_set,
            ctypes.byref(device_info_data),
            prop,
            ctypes.byref(reg_type),
            None,
            0,
            ctypes.byref(required_size),
        )
        if required_size.value <= 2:
            return []
        buffer = (ctypes.c_byte * required_size.value)()
        ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
            device_info_set,
            ctypes.byref(device_info_data),
            prop,
            ctypes.byref(reg_type),
            buffer,
            required_size.value,
            ctypes.byref(required_size),
        )
        if not ok:
            return []
        raw_value = ctypes.wstring_at(ctypes.cast(buffer, wintypes.LPWSTR), required_size.value // ctypes.sizeof(ctypes.c_wchar))
        return _split_multi_sz(raw_value)

    device_info_set = setupapi.SetupDiGetClassDevsW(None, None, None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if device_info_set == INVALID_HANDLE_VALUE:
        return []

    items: list[dict[str, Any]] = []
    try:
        index = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            ok = setupapi.SetupDiEnumDeviceInfo(device_info_set, index, ctypes.byref(info))
            if not ok:
                if ctypes.GetLastError() == ERROR_NO_MORE_ITEMS:
                    break
                index += 1
                continue
            instance_id = get_instance_id(device_info_set, info)
            hardware_ids = get_registry_property_strings(device_info_set, info, SPDRP_HARDWAREID)
            friendly_names = get_registry_property_strings(device_info_set, info, SPDRP_FRIENDLYNAME)
            descriptions = get_registry_property_strings(device_info_set, info, SPDRP_DEVICEDESC)
            manufacturers = get_registry_property_strings(device_info_set, info, SPDRP_MFG)
            items.append(
                {
                    "instance_id": instance_id,
                    "hardware_ids": hardware_ids,
                    "friendly_name": friendly_names[0] if friendly_names else "",
                    "description": descriptions[0] if descriptions else "",
                    "manufacturer": manufacturers[0] if manufacturers else "",
                }
            )
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(device_info_set)
    return items


def _load_usbcanfd_verified_manifest() -> tuple[dict[str, Any], Optional[str]]:
    if not USBCANFD_200U_MANIFEST.exists():
        return {}, f"未找到 USBCANFD-200U SDK 清单：{USBCANFD_200U_MANIFEST}"
    try:
        manifest = json.loads(USBCANFD_200U_MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"USBCANFD-200U SDK 清单解析失败：{exc}"
    if not isinstance(manifest, dict):
        return {}, "USBCANFD-200U SDK 清单格式无效，必须为 JSON 对象"
    if str(manifest.get("status") or "").strip().lower() != "verified":
        return manifest, "USBCANFD-200U SDK 尚未完成验证，缺少已确认的 DLL/导出函数/文档信息"
    documentation_files = [str(item or "").strip() for item in manifest.get("documentation_files") or [] if str(item or "").strip()]
    library_files = [str(item or "").strip() for item in manifest.get("library_files") or [] if str(item or "").strip()]
    verified_exports = [str(item or "").strip() for item in manifest.get("verified_exports") or [] if str(item or "").strip()]
    channel_names = [str(item or "").strip() for item in manifest.get("channel_names") or [] if str(item or "").strip()]
    if not documentation_files:
        return manifest, "USBCANFD-200U SDK 清单缺少 documentation_files，无法确认厂家 API 文档"
    if not library_files:
        return manifest, "USBCANFD-200U SDK 清单缺少 library_files，无法确认 x64 SDK DLL"
    if not verified_exports:
        return manifest, "USBCANFD-200U SDK 清单缺少 verified_exports，无法确认 DLL 导出函数"
    if not channel_names:
        return manifest, "USBCANFD-200U SDK 清单缺少 channel_names，无法确认真实物理通道数量"
    missing_files = []
    for relative_path in [*documentation_files, *library_files]:
        candidate = USBCANFD_200U_ROOT / relative_path
        if not candidate.exists():
            missing_files.append(str(candidate))
    if missing_files:
        return manifest, f"USBCANFD-200U SDK 文件缺失：{'; '.join(missing_files)}"
    return manifest, None


def _load_usbcanfd_zlg_api(manifest: dict[str, Any]) -> _ZlgCanApi:
    if os.name != "nt":
        raise CanDependencyMissingError("USBCANFD-200U 仅支持在 Windows 环境下通过 ZLG SDK 访问")
    library_files = [str(item or "").strip() for item in manifest.get("library_files") or [] if str(item or "").strip()]
    if not library_files:
        raise CanDependencyMissingError("USBCANFD-200U SDK 清单缺少 zlgcan.dll 路径")
    dll_candidates = [USBCANFD_200U_ROOT / relative_path for relative_path in library_files]
    zlgcan_path = next((item for item in dll_candidates if item.name.lower() == "zlgcan.dll"), None)
    if zlgcan_path is None:
        zlgcan_path = dll_candidates[0]
    if not zlgcan_path.exists():
        raise CanDependencyMissingError(f"未找到 ZLG SDK DLL：{zlgcan_path}")
    dll_directories = [zlgcan_path.parent, zlgcan_path.parent / "kerneldlls"]
    directory_handles: list[Any] = []
    if hasattr(os, "add_dll_directory"):
        for directory in dll_directories:
            if directory.exists():
                directory_handles.append(os.add_dll_directory(str(directory)))
    try:
        return _ZlgCanApi(zlgcan_path, directory_handles)
    except OSError as exc:
        for handle in reversed(directory_handles):
            try:
                handle.close()
            except Exception:
                pass
        raise CanDependencyMissingError(
            f"无法加载 ZLG SDK 运行库 {zlgcan_path.name}：{exc}. "
            "请确认 zlgcan.dll、kerneldlls 目录以及 VC++ 2013 运行时依赖已就绪"
        ) from exc


def _probe_usbcanfd_runtime_devices(api: _ZlgCanApi, expected_count: int = USBCANFD_200U_MAX_DEVICE_INDEX) -> list[_UsbcanfdRuntimeDevice]:
    devices: list[_UsbcanfdRuntimeDevice] = []
    if expected_count <= 0:
        return devices
        
    for device_index in range(USBCANFD_200U_MAX_DEVICE_INDEX):
        legacy_handle = api.ZCAN_OpenDevice(USBCANFD_200U_DEVICE_TYPE, device_index, 0)
        if not legacy_handle:
            continue

        legacy_status: Optional[int] = None
        legacy_serial = ""
        legacy_channel_count = 0
        legacy_hardware_type = ""
        try:
            legacy_info = _ZCAN_DEVICE_INFO()
            legacy_status = int(api.ZCAN_GetDeviceInf(legacy_handle, ctypes.byref(legacy_info)))
            if legacy_status == STATUS_OK:
                legacy_serial = _decode_c_string(legacy_info.str_Serial_Num)
                legacy_channel_count = int(legacy_info.can_Num or 0)
                legacy_hardware_type = _decode_c_string(legacy_info.str_hw_Type)
                devices.append(
                    _UsbcanfdRuntimeDevice(
                        device_index=device_index,
                        serial_number=legacy_serial,
                        can_channel_count=legacy_channel_count,
                        device_name=legacy_hardware_type,
                        hardware_type=legacy_hardware_type,
                    )
                )
        finally:
            legacy_close_status = int(api.ZCAN_CloseDevice(legacy_handle))
            logger.warning(
                "USBCANFD-200U probe device_index=%s OpenDevice handle=%s GetDeviceInf=%s GetDeviceInfoEx=%s serial=%s channels=%s CloseDevice=%s",
                device_index,
                legacy_handle,
                legacy_status if legacy_status is not None else "<not_called>",
                "<not_called>",
                legacy_serial or "<empty>",
                legacy_channel_count,
                legacy_close_status,
            )

        if legacy_status == STATUS_OK:
            if len(devices) >= expected_count:
                break
            continue

        ex_handle = api.ZCAN_OpenDevice(USBCANFD_200U_DEVICE_TYPE, device_index, 0)
        if not ex_handle:
            continue

        ex_status: Optional[int] = None
        ex_serial = ""
        ex_channel_count = 0
        ex_hardware_type = ""
        ex_device_name = ""
        try:
            ex_info = _ZCAN_DEVICE_INFO_EX()
            ex_status = int(api.ZCAN_GetDeviceInfoEx(ex_handle, ctypes.byref(ex_info)))
            if ex_status == STATUS_OK:
                ex_serial = _decode_c_string(ex_info.serial_number)
                ex_channel_count = int(ex_info.can_channel_number or 0)
                ex_device_name = _decode_c_string(ex_info.device_name)
                ex_hardware_type = _decode_c_string(ex_info.hardware_type)
                devices.append(
                    _UsbcanfdRuntimeDevice(
                        device_index=device_index,
                        serial_number=ex_serial,
                        can_channel_count=ex_channel_count,
                        device_name=ex_device_name,
                        hardware_type=ex_hardware_type,
                    )
                )
        finally:
            ex_close_status = int(api.ZCAN_CloseDevice(ex_handle))
            logger.warning(
                "USBCANFD-200U probe device_index=%s OpenDevice handle=%s GetDeviceInf=%s GetDeviceInfoEx=%s serial=%s channels=%s CloseDevice=%s",
                device_index,
                ex_handle,
                "<not_called>",
                ex_status if ex_status is not None else "<not_called>",
                ex_serial or "<empty>",
                ex_channel_count,
                ex_close_status,
            )
            
        if len(devices) >= expected_count:
            break
            
    return devices


class Usbcanfd200UBackend(CanAdapterBackend):
    backend_key = "usbcanfd_200u"
    adapter_name = "USBCANFD-200U"
    supported_protocols = frozenset({"can", "canfd"})

    def __init__(self) -> None:
        self._api: Optional[_ZlgCanApi] = None

    def _get_api(self) -> _ZlgCanApi:
        if self._api is None:
            manifest, manifest_error = _load_usbcanfd_verified_manifest()
            if manifest_error is not None:
                raise CanDependencyMissingError(manifest_error)
            self._api = _load_usbcanfd_zlg_api(manifest)
        return self._api

    def _raise_dependency_missing(self) -> None:
        _, manifest_error = _load_usbcanfd_verified_manifest()
        if manifest_error is not None:
            raise CanDependencyMissingError(manifest_error)
        raise CanDependencyMissingError("USBCANFD-200U SDK 未就绪，缺少厂家验证后的 x64 DLL、导出函数确认或 API 文档")

    def enumerate_devices(self) -> list[CanAdapterDevice]:
        manifest, manifest_error = _load_usbcanfd_verified_manifest()
        channel_names = [str(item or "").strip() for item in manifest.get("channel_names") or [] if str(item or "").strip()]
        
        pnp_devices = []
        for item in _enumerate_windows_pnp_devices():
            hardware_ids = [str(value or "").strip().upper() for value in item.get("hardware_ids") or [] if str(value or "").strip()]
            if any(any(keyword in hardware_id for keyword in USBCANFD_200U_HARDWARE_IDS) for hardware_id in hardware_ids):
                pnp_devices.append((item, hardware_ids))

        runtime_devices_by_serial: dict[str, _UsbcanfdRuntimeDevice] = {}
        runtime_error: Optional[str] = None
        if manifest_error is None and pnp_devices:
            try:
                runtime_devices_by_serial = {
                    item.serial_number: item for item in _probe_usbcanfd_runtime_devices(self._get_api(), expected_count=len(pnp_devices)) if item.serial_number
                }
            except CanDependencyMissingError as exc:
                runtime_error = exc.message

        sole_runtime_device = None
        if len(runtime_devices_by_serial) == 1 and len(pnp_devices) == 1:
            sole_runtime_device = next(iter(runtime_devices_by_serial.values()))

        devices: list[CanAdapterDevice] = []
        for item, hardware_ids in pnp_devices:
            instance_id = str(item.get("instance_id") or "").strip()
            pnp_serial_number = _extract_serial_number(instance_id)
            runtime_device = _resolve_usbcanfd_runtime_device(pnp_serial_number, runtime_devices_by_serial)
            if runtime_device is None and sole_runtime_device is not None:
                runtime_device = sole_runtime_device
            serial_number = runtime_device.serial_number if runtime_device and runtime_device.serial_number else pnp_serial_number
            dependency_status = "ready"
            dependency_message = ""
            sdk_device_index = None
            channel_count = len(channel_names)

            if manifest_error is not None:
                dependency_status = "dependency_missing"
                dependency_message = manifest_error
            elif runtime_error is not None:
                dependency_status = "dependency_missing"
                dependency_message = runtime_error
            elif runtime_device is None:
                dependency_status = "device_busy"
                dependency_message = (
                    f"ZLG SDK 当前未能将序列号 {pnp_serial_number or '<unknown>'} 的设备映射到 device_index，"
                    "设备可能正被其他程序占用，或驱动/枚举状态异常；请关闭占用该设备的程序后重新扫描并重试"
                )
            else:
                sdk_device_index = runtime_device.device_index
                channel_count = int(runtime_device.can_channel_count or len(channel_names))
                if channel_count <= 0:
                    dependency_status = "dependency_missing"
                    dependency_message = f"ZLG SDK 未返回 {self.adapter_name} 的真实通道数量"
                elif channel_count > len(channel_names):
                    dependency_status = "dependency_missing"
                    dependency_message = (
                        f"ZLG SDK 返回 {channel_count} 个通道，但清单仅声明 {len(channel_names)} 个通道名称"
                    )

            channels = [CanChannelDescriptor(name=name, index=index) for index, name in enumerate(channel_names[:channel_count])]
            devices.append(
                CanAdapterDevice(
                    adapter_key=f"{self.backend_key}:{instance_id or serial_number or len(devices)}",
                    backend_key=self.backend_key,
                    adapter_name=self.adapter_name,
                    serial_number=serial_number,
                    pnp_device_id=instance_id,
                    hardware_ids=hardware_ids,
                    description=str(item.get("friendly_name") or item.get("description") or self.adapter_name).strip(),
                    manufacturer=str(item.get("manufacturer") or "").strip(),
                    dependency_status=dependency_status,
                    dependency_message=dependency_message,
                    channels=channels,
                    sdk_device_index=sdk_device_index,
                )
            )
        return devices

    def open_device(self, device: CanAdapterDevice) -> Any:
        if device.sdk_device_index is None:
            self._raise_dependency_missing()
        api = self._get_api()
        raw_handle = api.ZCAN_OpenDevice(USBCANFD_200U_DEVICE_TYPE, int(device.sdk_device_index), 0)
        if not raw_handle:
            raise CanAdapterError(
                "device_open_failed",
                f"{self.adapter_name} 打开失败，device_index={device.sdk_device_index}，序列号={device.serial_number or '-'}",
            )
        return _UsbcanfdDeviceHandle(api=api, raw_handle=raw_handle, device_index=int(device.sdk_device_index))

    def _set_channel_value(self, device_handle: _UsbcanfdDeviceHandle, channel_index: int, item_name: str, value: Any) -> None:
        path = f"{channel_index}/{item_name}".encode("ascii")
        if isinstance(value, str):
            payload = ctypes.c_char_p(value.encode("ascii"))
            raw_value = ctypes.cast(payload, ctypes.c_void_p)
        elif isinstance(value, int):
            payload = ctypes.c_int(value)
            raw_value = ctypes.cast(ctypes.byref(payload), ctypes.c_void_p)
        elif isinstance(value, bytes):
            payload = ctypes.create_string_buffer(value)
            raw_value = ctypes.cast(payload, ctypes.c_void_p)
        elif isinstance(value, ctypes.Array):
            payload = value
            raw_value = ctypes.cast(payload, ctypes.c_void_p)
        elif isinstance(value, ctypes.Structure):
            payload = value
            raw_value = ctypes.cast(ctypes.byref(payload), ctypes.c_void_p)
        else:
            raise CanAdapterError("config_invalid", f"不支持的 ZLG 配置类型：{item_name}")
        status = int(device_handle.api.ZCAN_SetValue(device_handle.raw_handle, path, raw_value))
        if status != STATUS_OK:
            raise CanAdapterError(
                "config_apply_failed",
                f"{self.adapter_name} 配置失败：{channel_index}/{item_name}，ZCAN_SetValue={status}",
            )

    def init_channel(self, device_handle: Any, channel_name: str, *, protocol: str, config: dict[str, Any]) -> Any:
        typed_device_handle = device_handle
        channel_index = _channel_name_to_index(channel_name)
        protocol_key = str(protocol or "").strip().lower()
        if protocol_key not in {"can", "canfd"}:
            raise CanAdapterError("protocol_unsupported", f"不支持的 CAN 协议类型：{protocol}")

        if protocol_key == "canfd":
            arbitration_bitrate_value = next(
                (
                    config.get(key)
                    for key in ("arb_baud_rate", "arb_bitrate", "baud_rate", "bitrate")
                    if config.get(key) is not None
                ),
                None,
            )
        else:
            arbitration_bitrate_value = config.get("baud_rate") if config.get("baud_rate") is not None else config.get("bitrate")
        bitrate = _normalize_can_bitrate(arbitration_bitrate_value, field_label="仲裁域波特率")
        tx_timeout = str(max(1, min(4000, int(config.get("tx_timeout_ms") or 1500))))
        enable_termination = _normalize_bool_config(config.get("termination_enabled"), default=False)
        canfd_standard = "1" if _normalize_bool_config(config.get("canfd_non_iso"), default=False) else "0"

        # USBCANFD-200U 的官方 XML 明确声明 protocol/canfd_* 均为 init 前属性。
        self._set_channel_value(typed_device_handle, channel_index, "protocol", "0" if protocol_key == "can" else "1")
        self._set_channel_value(typed_device_handle, channel_index, "canfd_abit_baud_rate", bitrate)
        if protocol_key == "canfd":
            self._set_channel_value(typed_device_handle, channel_index, "canfd_standard", canfd_standard)
            data_bitrate = _normalize_can_bitrate(config.get("data_baud_rate") or config.get("data_bitrate") or 2000000, field_label="数据域波特率")
            self._set_channel_value(typed_device_handle, channel_index, "canfd_dbit_baud_rate", data_bitrate)

        init_config = _ZCAN_CHANNEL_INIT_CONFIG()
        ctypes.memset(ctypes.byref(init_config), 0, ctypes.sizeof(init_config))
        if protocol_key == "canfd":
            init_config.can_type = TYPE_CANFD
            init_config.config.canfd.mode = 0
            init_config.config.canfd.acc_code = 0
            init_config.config.canfd.acc_mask = 0xFFFFFFFF
            init_config.config.canfd.filter = 0
            init_config.config.canfd.abit_timing = 0
            init_config.config.canfd.dbit_timing = 0
            init_config.config.canfd.brp = 0
        else:
            init_config.can_type = TYPE_CAN
            init_config.config.can.acc_code = 0
            init_config.config.can.acc_mask = 0xFFFFFFFF
            init_config.config.can.reserved = 0
            init_config.config.can.filter = 0
            init_config.config.can.timing0 = 0
            init_config.config.can.timing1 = 0
            init_config.config.can.mode = 0

        raw_channel_handle = typed_device_handle.api.ZCAN_InitCAN(typed_device_handle.raw_handle, channel_index, ctypes.byref(init_config))
        if not raw_channel_handle:
            init_status = 0 if raw_channel_handle in {None, 0} else int(raw_channel_handle)
            raise CanAdapterError(
                "channel_init_failed",
                f"{self.adapter_name} 初始化通道 {channel_name} 失败，ZCAN_InitCAN={init_status}",
            )

        self._set_channel_value(typed_device_handle, channel_index, "initenal_resistance", "1" if enable_termination else "0")
        self._set_channel_value(typed_device_handle, channel_index, "tx_timeout", tx_timeout)
        return _UsbcanfdChannelHandle(
            api=typed_device_handle.api,
            raw_handle=raw_channel_handle,
            channel_index=channel_index,
            channel_name=channel_name,
            protocol=protocol_key,
        )

    def start_channel(self, device_handle: Any, channel_handle: Any) -> None:
        typed_channel_handle = channel_handle
        status = int(typed_channel_handle.api.ZCAN_StartCAN(typed_channel_handle.raw_handle))
        if status != STATUS_OK:
            raise CanAdapterError(
                "channel_start_failed",
                f"{self.adapter_name} 启动通道 {typed_channel_handle.channel_name} 失败，ZCAN_StartCAN={status}",
            )

    def transmit(self, connection: CanAdapterConnection, frame: CanFrame) -> None:
        typed_channel_handle = connection.channel_handle
        protocol_key = str(connection.protocol or "").strip().lower()
        if protocol_key == "can" and frame.is_fd:
            raise CanAdapterError("protocol_frame_mismatch", f"{self.adapter_name} 经典 CAN 连接不能发送 CAN FD 帧")
        if protocol_key == "canfd" and not frame.is_fd:
            raise CanAdapterError("protocol_frame_mismatch", f"{self.adapter_name} CAN FD 连接不能发送经典 CAN 帧")
        if frame.is_fd:
            payload = _ZCAN_TRANSMIT_FD_DATA()
            ctypes.memset(ctypes.byref(payload), 0, ctypes.sizeof(payload))
            payload.frame.can_id = _make_can_id(frame.frame_id, is_extended=frame.is_extended_id, is_remote=False)
            payload.frame.len = frame.declared_data_length
            payload.frame.flags = CANFD_BRS if frame.bitrate_switch else 0
            payload.transmit_type = 0
            for index, value in enumerate(frame.data):
                payload.frame.data[index] = value
            transmitted = typed_channel_handle.api.ZCAN_TransmitFD(typed_channel_handle.raw_handle, ctypes.byref(payload), 1)
        else:
            payload = _ZCAN_TRANSMIT_DATA()
            ctypes.memset(ctypes.byref(payload), 0, ctypes.sizeof(payload))
            payload.frame.can_id = _make_can_id(frame.frame_id, is_extended=frame.is_extended_id, is_remote=frame.is_remote_frame)
            payload.frame.can_dlc = frame.declared_data_length
            payload.transmit_type = 0
            for index, value in enumerate(frame.data):
                payload.frame.data[index] = value
            transmitted = typed_channel_handle.api.ZCAN_Transmit(typed_channel_handle.raw_handle, ctypes.byref(payload), 1)
        if transmitted != 1:
            api_name = "ZCAN_TransmitFD" if frame.is_fd else "ZCAN_Transmit"
            raise CanAdapterError(
                "tx_failed",
                f"{self.adapter_name} 发送失败，通道 {typed_channel_handle.channel_name}，{api_name}={int(transmitted)}",
            )

    def receive(
        self,
        connection: CanAdapterConnection,
        *,
        timeout_ms: int,
        expected_rx_id: Optional[int] = None,
        expected_rx_mask: Optional[int] = None,
    ) -> list[CanFrame]:
        del expected_rx_id, expected_rx_mask
        typed_channel_handle = connection.channel_handle
        result: list[CanFrame] = []
        if connection.protocol == "canfd":
            buffer = (_ZCAN_RECEIVE_FD_DATA * 128)()
            received_count = int(typed_channel_handle.api.ZCAN_ReceiveFD(typed_channel_handle.raw_handle, buffer, 128, int(timeout_ms)))
            if received_count < 0 or received_count > len(buffer):
                raise CanAdapterError("rx_count_out_of_range", f"{self.adapter_name} 接收返回数量异常：{received_count}")
            for item in buffer[:received_count]:
                frame_id, is_extended_id, is_remote_frame = _parse_received_can_id(item.frame.can_id)
                declared_data_length = int(item.frame.len)
                result.append(
                    CanFrame(
                        frame_id=frame_id,
                        is_extended_id=is_extended_id,
                        is_fd=True,
                        bitrate_switch=bool(item.frame.flags & CANFD_BRS),
                        is_remote_frame=is_remote_frame,
                        data=bytes(item.frame.data[:declared_data_length]),
                        declared_data_length=declared_data_length,
                        channel_name=typed_channel_handle.channel_name,
                        timestamp=float(item.timestamp) / 1_000_000.0 if item.timestamp else None,
                    )
                )
            return result

        buffer = (_ZCAN_RECEIVE_DATA * 128)()
        received_count = int(typed_channel_handle.api.ZCAN_Receive(typed_channel_handle.raw_handle, buffer, 128, int(timeout_ms)))
        if received_count < 0 or received_count > len(buffer):
            raise CanAdapterError("rx_count_out_of_range", f"{self.adapter_name} 接收返回数量异常：{received_count}")
        for item in buffer[:received_count]:
            frame_id, is_extended_id, is_remote_frame = _parse_received_can_id(item.frame.can_id)
            declared_data_length = int(item.frame.can_dlc)
            result.append(
                CanFrame(
                    frame_id=frame_id,
                    is_extended_id=is_extended_id,
                    is_fd=False,
                    bitrate_switch=False,
                    is_remote_frame=is_remote_frame,
                    data=b"" if is_remote_frame else bytes(item.frame.data[:declared_data_length]),
                    declared_data_length=declared_data_length,
                    channel_name=typed_channel_handle.channel_name,
                    timestamp=float(item.timestamp) / 1_000_000.0 if item.timestamp else None,
                )
            )
        return result

    def stop_channel(self, device_handle: Any, channel_handle: Any) -> None:
        del device_handle
        typed_channel_handle = channel_handle
        if typed_channel_handle is None or not typed_channel_handle.raw_handle:
            return
        if typed_channel_handle.api.ZCAN_ResetCAN(typed_channel_handle.raw_handle) != STATUS_OK:
            raise CanAdapterError("channel_stop_failed", f"{self.adapter_name} 停止通道 {typed_channel_handle.channel_name} 失败")

    def close_device(self, device_handle: Any) -> None:
        typed_device_handle = device_handle
        if typed_device_handle is None or not typed_device_handle.raw_handle:
            return
        if typed_device_handle.api.ZCAN_CloseDevice(typed_device_handle.raw_handle) != STATUS_OK:
            raise CanAdapterError("device_close_failed", f"{self.adapter_name} 关闭设备失败")


class ZqwlUcanCdcBackend(CanAdapterBackend):
    backend_key = "zqwl_ucan_cdc"
    adapter_name = "ZQWL USB-CAN"
    supported_protocols = frozenset({"can"})

    def enumerate_devices(self) -> list[CanAdapterDevice]:
        if list_ports is None:
            raise CanDependencyMissingError("当前环境缺少 pyserial，无法扫描 ZQWL USB-CAN 串口设备")
        devices: list[CanAdapterDevice] = []
        for port_info in list_ports.comports():
            vid = getattr(port_info, "vid", None)
            pid = getattr(port_info, "pid", None)
            if int(vid or -1) != ZQWL_UCAN_CDC_VID:
                continue
            if int(pid or -1) not in ZQWL_UCAN_CDC_PID_CHANNELS:
                continue
            port_name = str(getattr(port_info, "device", "") or "").strip()
            if not port_name:
                continue
            serial_number = _extract_serial_number_from_port_info(port_info)
            channels = [
                CanChannelDescriptor(name=name, index=index)
                for index, name in enumerate(ZQWL_UCAN_CDC_PID_CHANNELS[int(pid)])
            ]
            hwid = str(getattr(port_info, "hwid", "") or "").strip()
            description = str(getattr(port_info, "description", "") or getattr(port_info, "product", "") or self.adapter_name).strip()
            devices.append(
                CanAdapterDevice(
                    adapter_key=f"{self.backend_key}:{port_name}:{serial_number or 'unknown'}",
                    backend_key=self.backend_key,
                    adapter_name=self.adapter_name,
                    serial_number=serial_number,
                    pnp_device_id=hwid or f"USB\\VID_{ZQWL_UCAN_CDC_VID:04X}&PID_{int(pid):04X}\\{serial_number or port_name}",
                    hardware_ids=[f"USB\\VID_{ZQWL_UCAN_CDC_VID:04X}&PID_{int(pid):04X}"],
                    description=description,
                    manufacturer=str(getattr(port_info, "manufacturer", "") or "").strip(),
                    channels=channels,
                    adapter_device=port_name,
                    vid=int(vid),
                    pid=int(pid),
                )
            )
        return devices

    def open_device(self, device: CanAdapterDevice) -> Any:
        if serial is None:
            raise CanDependencyMissingError("当前环境缺少 pyserial，无法打开 ZQWL USB-CAN 设备")
        port_name = str(device.adapter_device or "").strip()
        if not port_name:
            raise CanAdapterError("device_open_failed", "ZQWL USB-CAN 未提供可用的 COM 口号")
        try:
            connection = serial.Serial(
                port=port_name,
                baudrate=ZQWL_UCAN_CDC_SERIAL_BAUDRATE,
                bytesize=getattr(serial, "EIGHTBITS", 8),
                parity=getattr(serial, "PARITY_NONE", "N"),
                stopbits=getattr(serial, "STOPBITS_ONE", 1),
                timeout=ZQWL_UCAN_CDC_SERIAL_TIMEOUT_SECONDS,
                write_timeout=max(ZQWL_UCAN_CDC_SERIAL_TIMEOUT_SECONDS, 0.2),
                xonxoff=False,
                rtscts=False,
            )
        except Exception as exc:
            raise CanAdapterError("device_open_failed", f"ZQWL USB-CAN 打开串口 {port_name} 失败：{exc}") from exc
        try:
            if hasattr(connection, "reset_input_buffer"):
                connection.reset_input_buffer()
            if hasattr(connection, "reset_output_buffer"):
                connection.reset_output_buffer()
        except Exception:
            pass
        return _ZqwlSerialHandle(connection=connection, port=port_name, serial_number=device.serial_number)

    def init_channel(self, device_handle: Any, channel_name: str, *, protocol: str, config: dict[str, Any]) -> Any:
        typed_device_handle = device_handle
        if str(protocol or "").strip().lower() != "can":
            raise CanAdapterError("protocol_unsupported", "ZQWL USB-CAN 仅支持经典 CAN")
        channel_index = _channel_name_to_index(channel_name)
        info_response = _transceive_zqwl_command(typed_device_handle.connection, function_code=0x40, rw_flag=0x52, data=bytes(16))
        serial_response = _transceive_zqwl_command(typed_device_handle.connection, function_code=0x41, rw_flag=0x52, data=bytes(16))
        serial_payload = serial_response[4:20]
        device_serial = _decode_zqwl_serial_number(serial_payload)
        if typed_device_handle.serial_number and device_serial and typed_device_handle.serial_number != device_serial:
            raise CanAdapterError(
                "adapter_mismatch",
                f"ZQWL USB-CAN 读取到的序列号 {device_serial} 与扫描结果 {typed_device_handle.serial_number} 不一致",
            )
        typed_device_handle.serial_number = device_serial or typed_device_handle.serial_number
        typed_device_handle.device_info_payload = info_response[4:20]
        typed_device_handle.serial_response_payload = serial_payload

        bitrate_code = _normalize_zqwl_classic_can_bitrate_code(config.get("baud_rate") or config.get("bitrate"))
        baudrate_payload = bytearray(16)
        baudrate_payload[0] = channel_index & 0xFF
        baudrate_payload[1] = 0
        baudrate_payload[2] = bitrate_code & 0xF0
        _transceive_zqwl_command(
            typed_device_handle.connection,
            function_code=0x42,
            rw_flag=0x57,
            data=bytes(baudrate_payload),
        )

        open_payload = bytearray(16)
        open_payload[0] = 0
        open_payload[1] = 0
        open_payload[2 + channel_index] = 1
        _transceive_zqwl_command(
            typed_device_handle.connection,
            function_code=0x44,
            rw_flag=0x57,
            data=bytes(open_payload),
        )
        return _ZqwlChannelHandle(channel_index=channel_index, channel_name=channel_name, protocol="can")

    def start_channel(self, device_handle: Any, channel_handle: Any) -> None:
        del device_handle, channel_handle

    def transmit(self, connection: CanAdapterConnection, frame: CanFrame) -> None:
        typed_device_handle = connection.device_handle
        channel_index = _channel_name_to_index(connection.channel_name)
        payload = bytearray()
        payload.append(ZQWL_UCAN_CDC_CAN_FRAME_PREFIX)
        payload.append(((channel_index & 0x01) << 7) | (frame.declared_data_length & 0x7F))
        meta = (((channel_index >> 1) & 0x03) << 3) | (0x04 if frame.is_extended_id else 0) | (0x02 if frame.is_remote_frame else 0)
        payload.append(meta)
        payload.extend(int(frame.frame_id).to_bytes(4, byteorder="big"))
        if not frame.is_remote_frame:
            payload.extend(frame.data)
        payload.append(0xA5)
        raw_payload = bytes(payload)
        try:
            written = typed_device_handle.connection.write(raw_payload)
            if written is None or int(written) != len(raw_payload):
                raise CanAdapterError(
                    "tx_failed",
                    f"ZQWL USB-CAN发送不完整：期望{len(raw_payload)}字节，实际{written}字节",
                )
            if hasattr(typed_device_handle.connection, "flush"):
                typed_device_handle.connection.flush()
        except CanAdapterError:
            raise
        except Exception as exc:
            raise CanAdapterError("tx_failed", f"ZQWL USB-CAN 发送失败：{exc}") from exc

    def receive(
        self,
        connection: CanAdapterConnection,
        *,
        timeout_ms: int,
        expected_rx_id: Optional[int] = None,
        expected_rx_mask: Optional[int] = None,
    ) -> list[CanFrame]:
        del expected_rx_id, expected_rx_mask
        typed_device_handle = connection.device_handle
        typed_channel_handle = connection.channel_handle
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
        while time.monotonic() < deadline:
            try:
                waiting = int(getattr(typed_device_handle.connection, "in_waiting", 0) or 0)
                chunk = bytes(typed_device_handle.connection.read(waiting or 1))
            except Exception as exc:
                raise CanAdapterError("rx_failed", f"ZQWL USB-CAN 接收失败：{exc}") from exc
            if chunk:
                typed_channel_handle.receive_buffer.extend(chunk)
                frames = _drain_zqwl_can_frames(typed_channel_handle.receive_buffer)
                filtered_frames: list[CanFrame] = []
                for frame in frames:
                    if frame.is_fd:
                        logger.debug(
                            "Discarding ZQWL CAN FD frame on classical CAN backend: channel=%s frame_id=0x%X dlc=%s brs=%s",
                            frame.channel_name,
                            frame.frame_id,
                            frame.declared_data_length,
                            frame.bitrate_switch,
                        )
                        continue
                    filtered_frames.append(frame)
                if filtered_frames:
                    return filtered_frames
                continue
        return []

    def stop_channel(self, device_handle: Any, channel_handle: Any) -> None:
        typed_device_handle = device_handle
        try:
            close_payload = bytes(16)
            _transceive_zqwl_command(
                typed_device_handle.connection,
                function_code=0x44,
                rw_flag=0x57,
                data=close_payload,
            )
        except Exception:
            pass

    def close_device(self, device_handle: Any) -> None:
        typed_device_handle = device_handle
        try:
            typed_device_handle.connection.close()
        except Exception as exc:
            raise CanAdapterError("device_close_failed", f"ZQWL USB-CAN 关闭串口失败：{exc}") from exc


CAN_ADAPTER_BACKENDS: dict[str, CanAdapterBackend] = {
    ZqwlUcanCdcBackend.backend_key: ZqwlUcanCdcBackend(),
    Usbcanfd200UBackend.backend_key: Usbcanfd200UBackend(),
}


def _backend_supports_protocol(backend: CanAdapterBackend, protocol: Optional[str]) -> bool:
    if protocol is None:
        return True
    supported = getattr(backend, "supported_protocols", frozenset({"can", "canfd"}))
    return str(protocol or "").strip().lower() in supported


def list_can_adapter_devices(protocol: Optional[str] = None) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    normalized_protocol = str(protocol or "").strip().lower() or None
    for backend in CAN_ADAPTER_BACKENDS.values():
        if not _backend_supports_protocol(backend, normalized_protocol):
            continue
        devices.extend(device.to_dict() for device in backend.enumerate_devices())
    return devices


def resolve_can_adapter_device(
    adapter_key: str,
    *,
    protocol: Optional[str] = None,
    expected_serial_number: Optional[str] = None,
    expected_pnp_device_id: Optional[str] = None,
    expected_adapter_device: Optional[str] = None,
    expected_sdk_device_index: Optional[int] = None,
) -> tuple[CanAdapterBackend, CanAdapterDevice]:
    requested_key = str(adapter_key or "").strip()
    normalized_protocol = str(protocol or "").strip().lower() or None
    if expected_adapter_device is None:
        expected_adapter_device = expected_pnp_device_id
    for backend in CAN_ADAPTER_BACKENDS.values():
        if not _backend_supports_protocol(backend, normalized_protocol):
            continue
        for device in backend.enumerate_devices():
            if device.adapter_key == requested_key:
                if expected_serial_number and str(device.serial_number or "").strip() != str(expected_serial_number).strip():
                    raise CanAdapterError("adapter_mismatch", "所选适配器序列号与当前连接配置不一致，请重新扫描并重新连接")
                if expected_adapter_device:
                    actual_adapter_device = str(device.adapter_device or device.pnp_device_id or "").strip()
                    expected_adapter_device_text = str(expected_adapter_device).strip()
                    if actual_adapter_device != expected_adapter_device_text and str(device.pnp_device_id or "").strip() != expected_adapter_device_text:
                        raise CanAdapterError("adapter_mismatch", "所选适配器设备标识与当前连接配置不一致，请重新扫描并重新连接")
                if expected_sdk_device_index is not None:
                    current_index = -1 if device.sdk_device_index is None else int(device.sdk_device_index)
                    if current_index != int(expected_sdk_device_index):
                        raise CanAdapterError("adapter_mismatch", "所选适配器 device_index 与当前连接配置不一致，请重新扫描并重新连接")
                return backend, device
    if expected_serial_number or expected_adapter_device or expected_sdk_device_index is not None:
        raise CanAdapterError("adapter_offline", "所选适配器已离线，请重新扫描")
    raise CanAdapterError("adapter_not_found", f"未找到适配器 {requested_key}")


def _build_connection_guard_key(device: CanAdapterDevice, physical_channel: str) -> str:
    if str(device.backend_key or "").strip() == ZqwlUcanCdcBackend.backend_key:
        return str(device.adapter_key or "").strip()
    return _build_channel_guard_key(device.adapter_key, physical_channel)


def open_can_adapter_connection(protocol: str, config: dict[str, Any]) -> CanAdapterConnection:
    adapter_key = str(config.get("adapter_key") or "").strip()
    if not adapter_key:
        raise CanAdapterError("adapter_required", "请选择 CAN 适配器")
    physical_channel = str(config.get("physical_channel") or "").strip()
    if not physical_channel:
        raise CanAdapterError("channel_required", "请选择物理通道")
    backend = None
    device = None
    try:
        backend, device = resolve_can_adapter_device(
            adapter_key,
            protocol=protocol,
            expected_serial_number=str(config.get("adapter_serial") or "").strip() or None,
            expected_adapter_device=str(config.get("adapter_device") or config.get("com_port") or "").strip() or None,
        )
    except Exception:
        raise
    channel_guard_key = _build_connection_guard_key(device, physical_channel)
    _reserve_channel_guard(channel_guard_key)
    if device.dependency_status != "ready":
        _release_channel_guard(channel_guard_key)
        if str(device.dependency_status or "").strip() == "device_busy":
            raise CanAdapterError("device_busy", device.dependency_message or f"{device.adapter_name} 可能正被其他程序占用，请关闭占用后重试")
        raise CanDependencyMissingError(device.dependency_message or f"{device.adapter_name} 依赖未就绪")
    available_channels = {channel.name for channel in device.channels}
    if physical_channel not in available_channels:
        _release_channel_guard(channel_guard_key)
        raise CanAdapterError("channel_not_found", f"{device.adapter_name} 未提供物理通道 {physical_channel}")
    device_handle = None
    channel_handle = None
    try:
        device_handle = backend.open_device(device)
        channel_handle = backend.init_channel(device_handle, physical_channel, protocol=protocol, config=config)
        backend.start_channel(device_handle, channel_handle)
        return CanAdapterConnection(
            backend_key=backend.backend_key,
            device=device,
            device_handle=device_handle,
            channel_handle=channel_handle,
            channel_name=physical_channel,
            protocol=protocol,
            channel_guard_key=channel_guard_key,
        )
    except Exception:
        if channel_handle is not None:
            try:
                backend.stop_channel(device_handle, channel_handle)
            except Exception:
                pass
        if device_handle is not None:
            try:
                backend.close_device(device_handle)
            except Exception:
                pass
        _release_channel_guard(channel_guard_key)
        raise


def close_can_adapter_connection(connection: Optional[CanAdapterConnection]) -> None:
    if connection is None:
        return
    backend = CAN_ADAPTER_BACKENDS.get(connection.backend_key)
    try:
        if backend is not None:
            backend.stop_channel(connection.device_handle, connection.channel_handle)
    finally:
        try:
            if backend is not None:
                backend.close_device(connection.device_handle)
        finally:
            _release_channel_guard(connection.channel_guard_key)
