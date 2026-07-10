import ctypes
import re
import threading
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any


class WchGpioError(RuntimeError):
    pass


CH910X_SUCCESS = 0x00
CH910X_ERROR_MESSAGES = {
    0x01: "无效句柄",
    0x02: "参数无效",
    0x03: "设备通信失败",
    0x04: "当前芯片不支持该 GPIO 函数",
    0x05: "GPIO 未初始化",
}


class ChipPropertyS(ctypes.Structure):
    _fields_ = [
        ("ChipType", ctypes.c_ubyte),
        ("ChipTypeStr", ctypes.c_char * 32),
        ("FwVerStr", ctypes.c_char * 32),
        ("GpioCount", ctypes.c_ubyte),
        ("IsEmbbedEeprom", wintypes.BOOL),
        ("IsSupportMcuBootCtrl", wintypes.BOOL),
        ("ManufacturerString", ctypes.c_char * 64),
        ("ProductString", ctypes.c_char * 64),
        ("bcdDevice", ctypes.c_ushort),
        ("PortIndex", ctypes.c_ubyte),
        ("IsSupportGPIOInit", wintypes.BOOL),
        ("PortName", ctypes.c_char * 32),
        ("ResvD", ctypes.c_ulong * 8),
    ]


@dataclass
class WchGpioResult:
    com_port: str
    chip_type: int
    chip_type_text: str
    gpio_count: int
    port_index: int
    pin: str
    gpio_index: int
    level: str
    raw_status: int
    raw_func: int
    raw_dir: int


@dataclass
class WchGpioConnection:
    com_port: str
    handle: Any
    prop: ChipPropertyS
    chip_type: int
    chip_type_text: str
    gpio_count: int
    port_index: int
    pin_output_modes: dict[int, bool] = field(default_factory=dict)


_dll_lock = threading.Lock()
_dll: Any = None
_kernel32: Any = None


def _decode_c_string(value: bytes) -> str:
    return value.split(b"\0")[0].decode("gbk", errors="ignore") or value.split(b"\0")[0].decode(errors="ignore")


def _load_kernel32() -> Any:
    global _kernel32
    if _kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        _kernel32 = kernel32
    return _kernel32


def _load_dll() -> Any:
    global _dll
    if _dll is None:
        dll = ctypes.WinDLL(r"C:\Windows\System32\CH343PTA64.DLL")
        dll.CH343PT_GetChipProperty.argtypes = [wintypes.HANDLE, ctypes.POINTER(ChipPropertyS)]
        dll.CH343PT_GetChipProperty.restype = ctypes.c_ubyte
        dll.CH910x_GetGpioConfig.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ChipPropertyS),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        dll.CH910x_GetGpioConfig.restype = ctypes.c_ubyte
        dll.CH910x_GpioConfig.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ChipPropertyS),
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        dll.CH910x_GpioConfig.restype = ctypes.c_ubyte
        dll.CH910x_GpioSet.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ChipPropertyS),
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        dll.CH910x_GpioSet.restype = ctypes.c_ubyte
        dll.CH910x_GpioGet.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ChipPropertyS),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        dll.CH910x_GpioGet.restype = ctypes.c_ubyte
        _dll = dll
    return _dll


def _raise_if_failed(function_name: str, code: int) -> None:
    if code == CH910X_SUCCESS:
        return
    detail = CH910X_ERROR_MESSAGES.get(int(code), f"错误码 {int(code)}")
    raise WchGpioError(f"{function_name} 失败：{detail}")


def _open_port(com_port: str) -> Any:
    port = str(com_port or "").strip().upper()
    if not re.fullmatch(r"COM\d+", port):
        raise WchGpioError("请选择有效的 WCH COM 口")
    kernel32 = _load_kernel32()
    handle = kernel32.CreateFileW(
        f"\\\\.\\{port}",
        0x80000000 | 0x40000000,
        0,
        None,
        3,
        0,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise WchGpioError(f"{port} 打开失败，Windows 错误码 {ctypes.get_last_error()}")
    return handle


def _close_port(handle: Any) -> None:
    try:
        _load_kernel32().CloseHandle(handle)
    except Exception:
        pass


def _pin_to_gpio_index(pin: str, base_index: int = 0, pin_map: dict[str, int] = None) -> int:
    text = str(pin or "").strip()
    if pin_map and isinstance(pin_map, dict):
        if text in pin_map:
            index = int(pin_map[text])
            if index < 0 or index > 63:
                raise WchGpioError(f"{pin} 映射的索引 {index} 超出 WCH GPIO 支持范围")
            return index
    
    match = re.search(r"(\d+)$", text)
    if not match:
        raise WchGpioError("请选择有效 GPIO 引脚")
    number = int(match.group(1))
    index = number + int(base_index or 0)
    if index < 0 or index > 63:
        raise WchGpioError(f"{pin} 超出 WCH GPIO 支持范围")
    return index


def _read_config(dll: Any, handle: Any, prop: ChipPropertyS) -> tuple[int, int, int]:
    func = ctypes.c_ulong(0)
    direction = ctypes.c_ulong(0)
    data = ctypes.c_ulong(0)
    ret = dll.CH910x_GetGpioConfig(handle, ctypes.byref(prop), ctypes.byref(func), ctypes.byref(direction), ctypes.byref(data))
    _raise_if_failed("CH910x_GetGpioConfig", ret)
    return int(func.value), int(direction.value), int(data.value)


def _configure_pin_mode(
    dll: Any,
    handle: Any,
    prop: ChipPropertyS,
    *,
    mask: int,
    func: int,
    direction: int,
    output_enabled: bool,
) -> tuple[int, int, int]:
    desired_func = func | mask
    desired_direction = (direction | mask) if output_enabled else (direction & ~mask)
    current_direction = direction & mask
    expected_direction = mask if output_enabled else 0
    ret = dll.CH910x_GpioConfig(
        handle,
        ctypes.byref(prop),
        ctypes.c_ulong(mask),
        ctypes.c_ulong(desired_func),
        ctypes.c_ulong(desired_direction),
    )
    _raise_if_failed("CH910x_GpioConfig", ret)
    next_func, next_direction, next_data = _read_config(dll, handle, prop)
    return next_func, next_direction, next_data


def _get_property(dll: Any, handle: Any) -> ChipPropertyS:
    prop = ChipPropertyS()
    chip_type = dll.CH343PT_GetChipProperty(handle, ctypes.byref(prop))
    if chip_type == 0xFF:
        raise WchGpioError("未识别到 WCH USB 串口芯片")
    if int(prop.GpioCount) <= 0:
        raise WchGpioError("当前 WCH 设备不支持 GPIO")
    return prop


def probe_wch_gpio_port(com_port: str) -> dict[str, Any]:
    connection = open_wch_gpio_connection(com_port)
    try:
        with _dll_lock:
            dll = _load_dll()
            func, direction, data = _read_config(dll, connection.handle, connection.prop)
        return {
            "com_port": connection.com_port,
            "chip_type": connection.chip_type,
            "chip_type_text": connection.chip_type_text,
            "firmware": _decode_c_string(bytes(connection.prop.FwVerStr)),
            "gpio_count": connection.gpio_count,
            "port_index": connection.port_index,
            "raw_func": func,
            "raw_dir": direction,
            "raw_data": data,
        }
    finally:
        close_wch_gpio_connection(connection)


def open_wch_gpio_connection(com_port: str) -> WchGpioConnection:
    with _dll_lock:
        dll = _load_dll()
        handle = _open_port(com_port)
        try:
            prop = _get_property(dll, handle)
            return WchGpioConnection(
                com_port=str(com_port or "").strip().upper(),
                handle=handle,
                prop=prop,
                chip_type=int(prop.ChipType),
                chip_type_text=_decode_c_string(bytes(prop.ChipTypeStr)),
                gpio_count=int(prop.GpioCount),
                port_index=int(prop.PortIndex),
            )
        except Exception:
            _close_port(handle)
            raise


def close_wch_gpio_connection(connection: WchGpioConnection | None) -> None:
    if connection is None:
        return
    with _dll_lock:
        _close_port(connection.handle)


def run_wch_gpio_action(
    *,
    com_port: str,
    pin: str,
    action: str,
    target_level: str = "",
    base_index: int = 0,
    pin_map: dict[str, int] = None,
    read_as_output: bool = False,
    existing_connection: WchGpioConnection | None = None,
) -> WchGpioResult:
    gpio_index = _pin_to_gpio_index(pin, base_index=base_index, pin_map=pin_map)
    mask = 1 << gpio_index
    normalized_action = str(action or "").strip().lower()
    wants_high = str(target_level or "").strip() in {"高电平", "HIGH", "high", "1", "true", "True"}

    with _dll_lock:
        dll = _load_dll()
        connection = existing_connection
        handle = connection.handle if connection is not None else _open_port(com_port)
        try:
            prop = connection.prop if connection is not None else _get_property(dll, handle)
            gpio_count = connection.gpio_count if connection is not None else int(prop.GpioCount)
            if gpio_index >= gpio_count:
                raise WchGpioError(f"{pin} 超出当前芯片 GPIO 数量 {gpio_count}")

            func, direction, data = _read_config(dll, handle, prop)
            if normalized_action == "set_level":
                desired_output_mode = True
                last_output_mode = connection.pin_output_modes.get(gpio_index) if connection is not None else None
                if last_output_mode is not True:
                    func, direction, data = _configure_pin_mode(
                        dll,
                        handle,
                        prop,
                        mask=mask,
                        func=func,
                        direction=direction,
                        output_enabled=True,
                    )
                    if connection is not None:
                        connection.pin_output_modes[gpio_index] = True
                write_data = mask if wants_high else 0
                ret = dll.CH910x_GpioSet(
                    handle,
                    ctypes.byref(prop),
                    ctypes.c_ulong(mask),
                    ctypes.c_ulong(write_data),
                )
                _raise_if_failed("CH910x_GpioSet", ret)
            elif normalized_action in {"read_level", "listen"}:
                desired_output_mode = read_as_output
                last_output_mode = connection.pin_output_modes.get(gpio_index) if connection is not None else None
                if last_output_mode is None or last_output_mode != desired_output_mode:
                    func, direction, data = _configure_pin_mode(
                        dll,
                        handle,
                        prop,
                        mask=mask,
                        func=func,
                        direction=direction,
                        output_enabled=desired_output_mode,
                    )
                    if connection is not None:
                        connection.pin_output_modes[gpio_index] = desired_output_mode
            else:
                raise WchGpioError(f"不支持的 GPIO 动作：{action}")

            status = ctypes.c_ulong(0)
            ret = dll.CH910x_GpioGet(handle, ctypes.byref(prop), ctypes.byref(status))
            _raise_if_failed("CH910x_GpioGet", ret)
            
            # Re-read configuration to ensure we return the absolute latest state
            func, direction, data = _read_config(dll, handle, prop)
            raw_status = int(status.value)
            
            # `set_level` reports the configured output level after CH910x_GpioSet.
            # Explicit read actions follow CH910x_GpioGet, which matches the official demo tool.
            is_output = bool(direction & mask)
            if is_output and normalized_action == "set_level":
                level = "高电平" if (data & mask) else "低电平"
            else:
                level = "高电平" if (raw_status & mask) else "低电平"
                
            return WchGpioResult(
                com_port=str(com_port or "").strip().upper(),
                chip_type=int(prop.ChipType),
                chip_type_text=_decode_c_string(bytes(prop.ChipTypeStr)),
                gpio_count=int(prop.GpioCount),
                port_index=int(prop.PortIndex),
                pin=str(pin or "").strip(),
                gpio_index=gpio_index,
                level=level,
                raw_status=raw_status,
                raw_func=func,
                raw_dir=direction,
            )
        finally:
            if connection is None:
                _close_port(handle)
