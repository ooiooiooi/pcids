#!/usr/bin/env python3
"""PC/SC transport for the HDSC CCID Writer.

The HDSC CCID Prog V6.04 application talks to the programmer as a Windows
smart-card reader (PC/SC), not as a serial port or CMSIS-DAP probe.  This
module deliberately keeps the transport separate from MCU algorithms: a
validated V6.04 operation profile supplies the APDUs for one target, while
this program owns reader selection, response checking and machine-readable
output for PCIDS.

No target-writing APDU is inferred here.  ``flash`` requires an explicit,
reviewed operation profile.  This prevents a newly connected programmer from
being sent guessed erase/write commands merely because its reader name
matches.  ``preflight`` is read-only and can be used on any connected writer.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCARD_S_SUCCESS = 0
SCARD_SCOPE_USER = 0
SCARD_SHARE_SHARED = 2
SCARD_PROTOCOL_T0 = 0x0001
SCARD_PROTOCOL_T1 = 0x0002
SCARD_LEAVE_CARD = 0
SCARD_E_NO_READERS_AVAILABLE = 0x8010002E
SCARD_E_NO_SMARTCARD = 0x8010000C
SCARD_E_UNKNOWN_READER = 0x80100009

HDSC_READER_TOKEN = "hdsc ccid writer"
PROFILE_FORMAT = "hdsc-ccid-v604-operation-v1"
SCRIPT_DIR = Path(__file__).resolve().parent
V604_HOST = SCRIPT_DIR / "hdsc_v604_host.ps1"
DEFAULT_V604_EXE = Path(r"D:\HDSC CCID\HDSC CCID在线离线编程器Rev6.04\HDSC+CCID+Prog+REV6.04.exe")
V604_EXE_NAME = "HDSC+CCID+Prog+REV6.04.exe"


class PcscError(RuntimeError):
    """A Windows PC/SC operation returned an error status."""

    def __init__(self, operation: str, status: int) -> None:
        super().__init__(f"{operation} failed: 0x{status & 0xFFFFFFFF:08X}")
        self.operation = operation
        self.status = status


class ProfileError(ValueError):
    """The operation profile is incomplete or unsafe to execute."""


@dataclass(frozen=True)
class Operation:
    name: str
    apdu: bytes
    expect_status: tuple[int, ...] = (0x9000,)


@dataclass(frozen=True)
class OperationProfile:
    target: str
    source: Path
    operations: tuple[Operation, ...]


class _ScardIoRequest(ctypes.Structure):
    _fields_ = [("dwProtocol", ctypes.c_ulong), ("cbPciLength", ctypes.c_ulong)]


class PcscSession:
    """Small stdlib-only wrapper around winscard.dll."""

    def __init__(self, reader: str) -> None:
        if os.name != "nt":
            raise PcscError("PC/SC is only available on Windows", 0)
        self._winscard = ctypes.WinDLL("winscard.dll")
        self._configure_prototypes()
        self._context = ctypes.c_void_p()
        self._card = ctypes.c_void_p()
        self._protocol = ctypes.c_ulong()
        self.reader = reader

    def _configure_prototypes(self) -> None:
        self._winscard.SCardEstablishContext.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self._winscard.SCardEstablishContext.restype = ctypes.c_long
        self._winscard.SCardReleaseContext.argtypes = [ctypes.c_void_p]
        self._winscard.SCardReleaseContext.restype = ctypes.c_long
        self._winscard.SCardConnectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_ulong)]
        self._winscard.SCardConnectW.restype = ctypes.c_long
        self._winscard.SCardDisconnect.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._winscard.SCardDisconnect.restype = ctypes.c_long
        self._winscard.SCardTransmit.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ScardIoRequest), ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        self._winscard.SCardTransmit.restype = ctypes.c_long

    @staticmethod
    def _check(operation: str, status: int) -> None:
        if status != SCARD_S_SUCCESS:
            raise PcscError(operation, status)

    def __enter__(self) -> "PcscSession":
        self._check("SCardEstablishContext", self._winscard.SCardEstablishContext(SCARD_SCOPE_USER, None, None, ctypes.byref(self._context)))
        try:
            self._check(
                "SCardConnectW",
                self._winscard.SCardConnectW(
                    self._context,
                    self.reader,
                    SCARD_SHARE_SHARED,
                    SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1,
                    ctypes.byref(self._card),
                    ctypes.byref(self._protocol),
                ),
            )
        except Exception:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._card.value:
            self._winscard.SCardDisconnect(self._card, SCARD_LEAVE_CARD)
            self._card = ctypes.c_void_p()
        if self._context.value:
            self._winscard.SCardReleaseContext(self._context)
            self._context = ctypes.c_void_p()

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def transmit(self, apdu: bytes) -> bytes:
        if not apdu:
            raise ValueError("APDU must not be empty")
        request = (ctypes.c_ubyte * len(apdu)).from_buffer_copy(apdu)
        response = (ctypes.c_ubyte * 65536)()
        response_length = ctypes.c_ulong(len(response))
        send_pci = _ScardIoRequest(self._protocol.value, ctypes.sizeof(_ScardIoRequest))
        self._check(
            "SCardTransmit",
            self._winscard.SCardTransmit(
                self._card,
                ctypes.byref(send_pci),
                request,
                len(apdu),
                None,
                response,
                ctypes.byref(response_length),
            ),
        )
        return bytes(response[: response_length.value])


def _winscard() -> Any:
    if os.name != "nt":
        raise PcscError("PC/SC is only available on Windows", 0)
    library = ctypes.WinDLL("winscard.dll")
    library.SCardEstablishContext.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    library.SCardEstablishContext.restype = ctypes.c_long
    library.SCardReleaseContext.argtypes = [ctypes.c_void_p]
    library.SCardReleaseContext.restype = ctypes.c_long
    library.SCardListReadersW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    library.SCardListReadersW.restype = ctypes.c_long
    return library


def list_readers() -> list[str]:
    library = _winscard()
    context = ctypes.c_void_p()
    status = library.SCardEstablishContext(SCARD_SCOPE_USER, None, None, ctypes.byref(context))
    if status != SCARD_S_SUCCESS:
        raise PcscError("SCardEstablishContext", status)
    try:
        length = ctypes.c_ulong()
        status = library.SCardListReadersW(context, None, None, ctypes.byref(length))
        if status == SCARD_E_NO_READERS_AVAILABLE:
            return []
        if status != SCARD_S_SUCCESS:
            raise PcscError("SCardListReadersW", status)
        buffer = ctypes.create_unicode_buffer(length.value)
        status = library.SCardListReadersW(context, None, buffer, ctypes.byref(length))
        if status != SCARD_S_SUCCESS:
            raise PcscError("SCardListReadersW", status)
        return [item for item in buffer[: length.value].split("\0") if item]
    finally:
        library.SCardReleaseContext(context)


def find_hdsc_reader(requested_reader: str = "") -> str:
    readers = list_readers()
    if requested_reader:
        if requested_reader not in readers:
            raise PcscError(f"HDSC reader {requested_reader!r}", SCARD_E_UNKNOWN_READER)
        return requested_reader
    matches = [reader for reader in readers if HDSC_READER_TOKEN in reader.casefold()]
    if not matches:
        raise PcscError("find HDSC CCID Writer", SCARD_E_NO_READERS_AVAILABLE)
    if len(matches) > 1:
        raise ProfileError(f"found multiple HDSC CCID readers: {matches}; select one with --reader")
    return matches[0]


def _parse_hex(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise ProfileError(f"{field} must be a hexadecimal string")
    normalized = "".join(value.split())
    try:
        result = bytes.fromhex(normalized)
    except ValueError as exc:
        raise ProfileError(f"{field} is not valid hex") from exc
    if not result:
        raise ProfileError(f"{field} must not be empty")
    return result


def load_operation_profile(path: Path, target: str) -> OperationProfile:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"cannot read profile: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(f"profile is not JSON: {path}") from exc
    if not isinstance(raw, dict) or raw.get("format") != PROFILE_FORMAT:
        raise ProfileError(f"profile format must be {PROFILE_FORMAT!r}")
    profile_target = str(raw.get("target") or "").strip()
    if not profile_target:
        raise ProfileError("profile target is required")
    if target and profile_target.casefold() != target.casefold():
        raise ProfileError(f"profile target {profile_target!r} does not match requested target {target!r}")
    raw_operations = raw.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ProfileError("profile operations must be a non-empty list")
    operations: list[Operation] = []
    for index, item in enumerate(raw_operations, start=1):
        if not isinstance(item, dict):
            raise ProfileError(f"operations[{index}] must be an object")
        name = str(item.get("name") or f"operation-{index}")
        expected = item.get("expect_status", ["9000"])
        if not isinstance(expected, list) or not expected:
            raise ProfileError(f"operations[{index}].expect_status must be a non-empty list")
        try:
            statuses = tuple(int(str(value), 16) for value in expected)
        except ValueError as exc:
            raise ProfileError(f"operations[{index}].expect_status is invalid") from exc
        operations.append(Operation(name=name, apdu=_parse_hex(item.get("apdu"), f"operations[{index}].apdu"), expect_status=statuses))
    return OperationProfile(target=profile_target, source=path, operations=tuple(operations))


def _status_word(response: bytes) -> int:
    if len(response) < 2:
        raise ProfileError("CCID response has no ISO 7816 status word")
    return int.from_bytes(response[-2:], "big")


def preflight(reader: str) -> dict[str, Any]:
    with PcscSession(reader) as session:
        response = session.transmit(bytes.fromhex("F9"))
    status = _status_word(response)
    if status != 0x9000:
        raise ProfileError(f"HDSC firmware query returned status {status:04X}")
    return {"reader": reader, "firmware_response": response[:-2].hex(" ").upper(), "status": f"{status:04X}"}


def execute_profile(reader: str, profile: OperationProfile) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    with PcscSession(reader) as session:
        for operation in profile.operations:
            response = session.transmit(operation.apdu)
            status = _status_word(response)
            if status not in operation.expect_status:
                raise ProfileError(f"{operation.name} returned {status:04X}, expected one of {[f'{item:04X}' for item in operation.expect_status]}")
            results.append({"name": operation.name, "status": f"{status:04X}", "response": response[:-2].hex(" ").upper()})
    return results


def _v604_powershell() -> Path:
    preferred = Path(os.environ.get("HDSC_CCID_POWERSHELL", r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"))
    if preferred.is_file():
        return preferred
    fallback = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if fallback.is_file():
        return fallback
    raise ProfileError("未找到运行 HDSC CCID Prog V6.04 所需的 Windows PowerShell。")


def _resolve_v604_exe() -> Path:
    configured = os.environ.get("HDSC_CCID_V604_EXE", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        raise ProfileError(f"HDSC_CCID_V604_EXE does not exist: {candidate}")

    bundled_tools_value = os.environ.get("PCIDS_BUNDLED_TOOLS_DIR", "").strip()
    bundled_tools_dir = Path(bundled_tools_value).expanduser() if bundled_tools_value else None
    bundled_search_roots = (
        [bundled_tools_dir / "HDSC", bundled_tools_dir / "HDSC_CCID"]
        if bundled_tools_dir is not None
        else []
    )

    direct_candidates = [
        *(root / V604_EXE_NAME for root in bundled_search_roots),
        DEFAULT_V604_EXE,
        Path.home() / "Desktop" / "HDSC CCID" / "HDSC CCID在线离线编程器Rev6.04" / V604_EXE_NAME,
    ]
    for candidate in direct_candidates:
        if candidate.is_file():
            return candidate.resolve()

    search_roots = [
        *bundled_search_roots,
        Path(r"D:\HDSC CCID"),
        Path.home() / "Desktop" / "HDSC CCID",
    ]
    matches: list[Path] = []
    for root in search_roots:
        if root.is_dir():
            matches.extend(item.resolve() for item in root.rglob(V604_EXE_NAME) if item.is_file())
    unique_matches = list(dict.fromkeys(matches))
    if len(unique_matches) == 1:
        return unique_matches[0]
    if len(unique_matches) > 1:
        raise ProfileError(
            "found multiple HDSC CCID Prog V6.04 executables; set HDSC_CCID_V604_EXE: "
            + ", ".join(str(item) for item in unique_matches)
        )
    raise ProfileError("HDSC CCID Prog V6.04 was not found; set HDSC_CCID_V604_EXE")


def execute_v604(reader: str, target: str, firmware: Path | None, erase_mode: str, completion_action: str, baud_index: int, baud_khz: int = 0, baud_rate: int = 0) -> dict[str, Any]:
    if os.name != "nt":
        raise ProfileError("HDSC CCID Prog V6.04 算法只能在 Windows 上运行")
    vendor_exe = _resolve_v604_exe()
    command = [
        str(_v604_powershell()), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(V604_HOST),
        "-Command", "flash" if firmware else "preflight",
        "-Reader", reader,
        "-VendorExe", str(vendor_exe),
    ]
    if firmware:
        command.extend([
            "-TargetChip", target,
            "-FirmwarePath", str(firmware),
            "-EraseMode", erase_mode,
            "-CompletionAction", completion_action,
            "-BaudIndex", str(baud_index),
            "-BaudKHz", str(baud_khz),
            "-BaudRate", str(baud_rate),
        ])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        detail = (completed.stderr or completed.stdout).strip()
        raise ProfileError(f"V6.04 算法主机未返回有效 JSON：{detail}") from exc
    if completed.returncode or not payload.get("ok"):
        raise ProfileError(str(payload.get("error") or "V6.04 芯片算法执行失败"))
    return dict(payload.get("data") or {})


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PC/SC agent for HDSC CCID Writer")
    parser.add_argument("command", choices=("list-readers", "preflight", "flash"))
    parser.add_argument("--reader", default="", help="Exact PC/SC reader name; auto-selects one HDSC CCID Writer when omitted")
    parser.add_argument("--target-chip", default="", help="Target MCU name; required by flash")
    parser.add_argument("--algorithm-profile", type=Path, help="Reviewed HDSC CCID Prog V6.04 operation profile JSON")
    parser.add_argument("--firmware", type=Path, help="Firmware file for the built-in HDSC CCID Prog V6.04 algorithm")
    parser.add_argument("--erase-mode", default="chip", choices=("chip", "all", "none", "no-erase"))
    parser.add_argument("--completion-action", default="reset-run", choices=("reset-run", "run", "none", "off"))
    parser.add_argument("--baud-index", type=int, default=0, choices=range(0, 9), help="V6.04 programmer baud-rate index (0 is 115200)")
    parser.add_argument("--baud-khz", type=int, default=0, help="Requested target ISP rate in kHz; resolved against the selected V6.04 algorithm")
    parser.add_argument("--baud-rate", type=int, default=0, help="Requested target ISP baud rate, for example 115200")
    args = parser.parse_args(argv)
    try:
        if args.command == "list-readers":
            readers = list_readers()
            _emit({"readers": readers, "hdsc_readers": [item for item in readers if HDSC_READER_TOKEN in item.casefold()]})
            return 0
        reader = find_hdsc_reader(args.reader)
        if args.command == "preflight":
            _emit({"ok": True, "command": "preflight", **execute_v604(reader, "", None, "none", "none", args.baud_index)})
            return 0
        if not args.target_chip:
            raise ProfileError("flash requires --target-chip")
        if args.firmware:
            if not args.firmware.is_file():
                raise ProfileError(f"firmware does not exist: {args.firmware}")
            result = execute_v604(reader, args.target_chip, args.firmware, args.erase_mode, args.completion_action, args.baud_index, args.baud_khz, args.baud_rate)
            _emit({"ok": True, "command": "flash", **result})
            return 0
        if args.algorithm_profile is None:
            raise ProfileError("flash requires --firmware for built-in V6.04 algorithms or --algorithm-profile for a reviewed raw profile")
        profile = load_operation_profile(args.algorithm_profile, args.target_chip)
        _emit({"ok": True, "command": "flash", "reader": reader, "target": profile.target, "profile": str(profile.source), "operations": execute_profile(reader, profile)})
        return 0
    except (PcscError, ProfileError) as exc:
        _emit({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
