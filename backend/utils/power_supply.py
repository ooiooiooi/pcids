from __future__ import annotations

import time
from typing import Any, Callable, Optional

try:
    import serial  # type: ignore
    from serial.tools import list_ports  # type: ignore
except Exception:  # pragma: no cover
    serial = None
    list_ports = None


POWER_ON_FRAME_HEX = "01 10 0A 00 00 01 02 00 06 8C 52"
POWER_OFF_FRAME_HEX = "01 10 0A 00 00 01 02 00 07 4D 92"


class PowerSupplyError(RuntimeError):
    pass


def hex_to_bytes(hex_text: str) -> bytes:
    return bytes.fromhex("".join(str(hex_text or "").split()))


def bytes_to_hex(raw: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in raw)


def get_serial_factory(serial_factory: Optional[Callable[..., Any]] = None) -> Callable[..., Any]:
    if serial_factory:
        return serial_factory
    if serial is None:
        raise PowerSupplyError("当前环境缺少 pyserial，无法执行 RS485 串口控制，请先安装 pyserial")
    return serial.Serial


def list_candidate_ports() -> list[dict[str, str]]:
    if list_ports is None:
        return []
    ports: list[dict[str, str]] = []
    for item in list_ports.comports():
        ports.append(
            {
                "port": str(getattr(item, "device", "") or ""),
                "description": str(getattr(item, "description", "") or ""),
                "hwid": str(getattr(item, "hwid", "") or ""),
            }
        )
    return ports


def send_modbus_frame(
    port: str,
    frame: bytes,
    *,
    baudrate: int = 9600,
    timeout: float = 0.5,
    serial_factory: Optional[Callable[..., Any]] = None,
) -> bytes:
    if not str(port or "").strip():
        raise PowerSupplyError("缺少电源控制串口")

    factory = get_serial_factory(serial_factory)
    connection = None
    try:
        connection = factory(
            port=port,
            baudrate=baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=timeout,
            write_timeout=timeout,
        )
        if hasattr(connection, "reset_input_buffer"):
            connection.reset_input_buffer()
        if hasattr(connection, "reset_output_buffer"):
            connection.reset_output_buffer()
        connection.write(frame)
        if hasattr(connection, "flush"):
            connection.flush()
        time.sleep(0.08)
        if hasattr(connection, "in_waiting"):
            waiting = int(getattr(connection, "in_waiting", 0) or 0)
            if waiting > 0:
                return bytes(connection.read(waiting))
        return bytes(connection.read(64))
    except PowerSupplyError:
        raise
    except Exception as exc:
        raise PowerSupplyError(str(exc)) from exc
    finally:
        try:
            if connection is not None and hasattr(connection, "close"):
                connection.close()
        except Exception:
            pass


def blind_scan_power_ports(
    *,
    candidate_ports: Optional[list[str]] = None,
    serial_factory: Optional[Callable[..., Any]] = None,
) -> list[dict[str, str]]:
    candidates = candidate_ports or [item["port"] for item in list_candidate_ports() if item.get("port")]
    matched: list[dict[str, str]] = []
    probe_frame = hex_to_bytes(POWER_ON_FRAME_HEX)

    descriptions = {item["port"]: item for item in list_candidate_ports()}
    for port in candidates:
        try:
            response = send_modbus_frame(port, probe_frame, serial_factory=serial_factory)
        except Exception:
            continue
        if not response:
            continue
        port_meta = descriptions.get(port, {})
        matched.append(
            {
                "port": port,
                "description": str(port_meta.get("description") or ""),
                "hwid": str(port_meta.get("hwid") or ""),
                "label": f"DPS1816S直流电源 · {port}",
                "status": "online",
                "response_hex": bytes_to_hex(response),
            }
        )
    return matched


def power_on(port: str, *, serial_factory: Optional[Callable[..., Any]] = None) -> bytes:
    return send_modbus_frame(port, hex_to_bytes(POWER_ON_FRAME_HEX), serial_factory=serial_factory)


def power_off(port: str, *, serial_factory: Optional[Callable[..., Any]] = None) -> bytes:
    return send_modbus_frame(port, hex_to_bytes(POWER_OFF_FRAME_HEX), serial_factory=serial_factory)
