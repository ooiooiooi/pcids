from __future__ import annotations

"""
设备管理路由
"""
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import yaml

try:
    import psutil
except ImportError:  # pragma: no cover - packaged runtime includes psutil
    psutil = None
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.utils.db import get_db, ensure_schema
from backend.utils.datetime_utils import database_time_to_local
from backend.models.user import User
from backend.models.burner import Burner
from backend.models.task import BurningTask, TaskStatus
from backend.schemas import BurnerCreate, BurnerUpdate, Response, PaginatedResponse
from backend.routers.auth import get_current_user
from backend.routers.repositories import _get_repository_server_transport_config
from backend.utils.permission import require_permission
from backend.utils.agent_security import build_agent_headers, require_agent_token
from backend.utils.app_paths import get_app_data_root

router = APIRouter()
logger = logging.getLogger(__name__)
_USB_PROBE_CACHE_LOCK = threading.Lock()
_USB_PROBE_CACHE: dict[str, object] = {"expires_at": 0.0, "devices": []}
_STLINK_SERIAL_CACHE: dict[str, object] = {"expires_at": 0.0, "serials": []}
_PYOCD_PROBE_CACHE_LOCK = threading.Lock()
_PYOCD_PROBE_CACHE: dict[str, object] = {"expires_at": 0.0, "probes": []}
_PNP_REFRESH_LOCK = threading.Lock()
_PNP_REFRESH_CACHE: dict[str, object] = {"refreshed_at": 0.0}
_RUNTIME_STATUS_REFRESH_LOCK = asyncio.Lock()
_LAN_AGENT_CACHE_LOCK = threading.Lock()
_LAN_AGENT_CACHE: dict[str, object] = {"expires_at": 0.0, "urls": []}
_AGENT_DISCOVERY_CONFIG_ENV = "PCIDS_AGENT_DISCOVERY_CONFIG"


# #region debug-point G:report-helper
def _debug_report_device_refresh_crash(hypothesis_id: str, location: str, msg: str, data: Optional[dict] = None, trace_id: Optional[str] = None) -> None:
    try:
        _env_path = os.path.join(".dbg", "device-status-refresh-crash.env")
        if not os.path.isfile(_env_path):
            return
        _url = "http://127.0.0.1:7779/event"
        _session_id = "device-status-refresh-crash"
        with open(_env_path, "r", encoding="utf-8") as _env_file:
            for _line in _env_file:
                _line = _line.strip()
                if _line.startswith("DEBUG_SERVER_URL="):
                    _url = _line.split("=", 1)[1] or _url
                elif _line.startswith("DEBUG_SESSION_ID="):
                    _session_id = _line.split("=", 1)[1] or _session_id
        urllib.request.urlopen(
            urllib.request.Request(
                _url,
                data=json.dumps(
                    {
                        "sessionId": _session_id,
                        "runId": "pre",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": f"[DEBUG] {msg}",
                        "data": data or {},
                        "traceId": trace_id,
                        "ts": int(time.time() * 1000),
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.4,
        ).read()
    except Exception:
        pass
# #endregion


# #region debug-point SJ:report-helper
def _debug_report_burner_scan_crash(hypothesis_id: str, location: str, msg: str, data: Optional[dict] = None, trace_id: Optional[str] = None) -> None:
    try:
        _env_path = os.path.join(".dbg", "burner-scan-crash.env")
        if not os.path.isfile(_env_path):
            return
        _url = "http://127.0.0.1:7777/event"
        _session_id = "burner-scan-crash"
        with open(_env_path, "r", encoding="utf-8") as _env_file:
            for _line in _env_file:
                _line = _line.strip()
                if _line.startswith("DEBUG_SERVER_URL="):
                    _url = _line.split("=", 1)[1] or _url
                elif _line.startswith("DEBUG_SESSION_ID="):
                    _session_id = _line.split("=", 1)[1] or _session_id
        urllib.request.urlopen(
            urllib.request.Request(
                _url,
                data=json.dumps(
                    {
                        "sessionId": _session_id,
                        "runId": "pre",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": f"[DEBUG] {msg}",
                        "data": data or {},
                        "traceId": trace_id,
                        "ts": int(time.time() * 1000),
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.4,
        ).read()
    except Exception:
        pass
# #endregion

DEVICE_TYPE_ALIASES: list[tuple[str, list[str]]] = [
    ("J-LINK", ["j-link", "jlink"]),
    ("PWLINK2", ["pwlink", "p-wlink", "pwlink2", "pw winusb", "pw_winusb", "#pw_winusb_cmsis-dap"]),
    ("GDLINK", ["gdlink", "gd-link"]),
    ("SWD下载器", ["swd"]),
    ("AL321", ["al321"]),
    ("ST-LINK", ["st-link", "stlink"]),
    ("HDSC CCID", ["hdsc", "ccid"]),
    ("XDS510plus", ["seed xds510", "seed usb2.0 plus emulator", "xds510plus"]),
    ("MPLAB ICD 3 DV164035", ["mplab", "icd 3", "dv164035"]),
    ("Altera Blaster II", ["altera", "blaster"]),
    ("Gowin USB Cable", ["gowin", "gowin usb"]),
    ("SD卡文件写入", ["sd", "card reader", "storage", "reader", "mass storage", "flash reader"]),
]
LOCATION_PROBE_CANDIDATE_TYPE = "__physical_location_probe__"

VALID_DEVICE_CATEGORIES = {"burner", "sd_reader"}
INTERFACE_ORDER = ["SWD", "JTAG", "CJTAG", "UART", "ICSP"]
CHIP_ORDER = ["ARM", "TI DSP", "FPGA", "PIC", "CPLD"]
DEVICE_SCAN_PRIORITY = [
    "ST-LINK",
    "J-LINK",
    "PWLINK2",
    "GDLINK",
    "HDSC CCID",
    "XDS510plus",
    "MPLAB ICD 3 DV164035",
    "Altera Blaster II",
    "Gowin USB Cable",
    "AL321",
]
def _get_env_float(name: str, default: float, min_value: float = 0.5, max_value: float = 60.0) -> float:
    try:
        value = float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        value = default
    return min(max(value, min_value), max_value)


REMOTE_SCAN_TIMEOUT_SECONDS = _get_env_float("PCIDS_REMOTE_BURNER_SCAN_TIMEOUT_SECONDS", 6)
REMOTE_DISCOVERY_TIMEOUT_SECONDS = _get_env_float("PCIDS_REMOTE_BURNER_DISCOVERY_TIMEOUT_SECONDS", 6)
BURNER_CAPABILITY_MAP = {
    "J_LINK": {"supported_interfaces": ["SWD", "JTAG", "CJTAG"], "supported_chips": ["ARM"]},
    "PWLINK2": {"supported_interfaces": ["SWD", "JTAG"], "supported_chips": ["ARM"]},
    "GDLINK": {"supported_interfaces": ["SWD", "JTAG"], "supported_chips": ["ARM"]},
    "SWD下载器": {"supported_interfaces": ["SWD"], "supported_chips": ["ARM"]},
    "AL321": {"supported_interfaces": ["JTAG"], "supported_chips": ["FPGA"]},
    "ST_LINK": {"supported_interfaces": ["SWD", "JTAG", "CJTAG"], "supported_chips": ["ARM"]},
    "HDSC_CCID": {"supported_interfaces": ["UART"], "supported_chips": ["ARM"]},
    "XDS510PLUS": {"supported_interfaces": ["JTAG"], "supported_chips": ["TI DSP"]},
    "MPLAB_ICD_3_DV164035": {"supported_interfaces": ["ICSP"], "supported_chips": ["PIC"]},
    "ALTERA_BLASTER_II": {"supported_interfaces": ["JTAG"], "supported_chips": ["FPGA", "CPLD"]},
    "GOWIN_USB_CABLE": {"supported_interfaces": ["JTAG"], "supported_chips": ["FPGA"]},
}
LEGACY_BURNER_NAME_MAP = {
    "J-LINK V11": "J-LINK",
    "J_LINK V11": "J-LINK",
    "PWLINK V2": "PWLINK2",
    "ST_LINK": "ST-LINK",
    "ST-LINK V2": "ST-LINK",
    "MPLAB ICD 3": "MPLAB ICD 3 DV164035",
    "TI XDS510 Plus": "XDS510plus",
}


def _stable_identifier(prefix: str, parts: list[str], length: int) -> str:
    seed = "|".join(parts).encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest().upper()
    return f"{prefix}{digest[:length]}"


def _is_test_online_burner(existing: Optional[Burner]) -> bool:
    return False


def _flatten_usb_items(items: list[dict]) -> list[dict]:
    results: list[dict] = []
    for item in items:
        name = item.get("_name") or item.get("product_name") or item.get("device_name")
        if name:
            results.append(item)
        for child in item.get("_items", []) or []:
            results.extend(_flatten_usb_items([child]))
    return results


def _probe_usb_devices_uncached() -> list[dict]:
    system_name = platform.system().lower()
    if system_name == "darwin":
        try:
            completed = subprocess.run(
                ["system_profiler", "SPUSBDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
            payload = json.loads(completed.stdout or "{}")
            return _flatten_usb_items(payload.get("SPUSBDataType", []) or [])
        except Exception as exc:
            logger.exception("burner.usb_probe.failed | %s", json.dumps({"platform": platform.system(), "error": str(exc)}, ensure_ascii=False))
            return []
    if system_name == "linux":
        return _probe_linux_devices()
    if system_name == "windows":
        return _probe_windows_usb_devices()
    return []


def _probe_usb_devices(
    cache_ttl_seconds: float = 3.0,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    now = time.monotonic()
    with _USB_PROBE_CACHE_LOCK:
        if not force_refresh and now < float(_USB_PROBE_CACHE.get("expires_at") or 0):
            return list(_USB_PROBE_CACHE.get("devices") or [])
        devices = _probe_usb_devices_uncached()
        _USB_PROBE_CACHE["devices"] = list(devices)
        _USB_PROBE_CACHE["expires_at"] = time.monotonic() + max(cache_ttl_seconds, 0.0)
        return list(devices)


def _invalidate_usb_probe_cache() -> None:
    with _USB_PROBE_CACHE_LOCK:
        _USB_PROBE_CACHE["devices"] = []
        _USB_PROBE_CACHE["expires_at"] = 0.0
    _STLINK_SERIAL_CACHE["serials"] = []
    _STLINK_SERIAL_CACHE["expires_at"] = 0.0
    with _PYOCD_PROBE_CACHE_LOCK:
        _PYOCD_PROBE_CACHE["probes"] = []
        _PYOCD_PROBE_CACHE["expires_at"] = 0.0


def _refresh_windows_pnp_state(min_interval_seconds: float = 1.5) -> bool:
    """Ask Windows to re-enumerate devices before a user-triggered scan.

    ``Get-PnpDevice -PresentOnly`` can retain an unplugged devnode until the
    Plug and Play manager performs another enumeration.  A short shared
    throttle prevents local-list, form-scan, and task-wizard refreshes from
    launching duplicate scans at the same time.
    """
    if platform.system().lower() != "windows":
        return False
    with _PNP_REFRESH_LOCK:
        now = time.monotonic()
        refreshed_at = float(_PNP_REFRESH_CACHE.get("refreshed_at") or 0.0)
        if now - refreshed_at < max(min_interval_seconds, 0.0):
            return True
        succeeded = False
        try:
            completed = subprocess.run(
                ["pnputil.exe", "/scan-devices"],
                capture_output=True,
                timeout=8,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            succeeded = completed.returncode == 0
            if not succeeded:
                logger.warning(
                    "burner.pnp_refresh.failed | %s",
                    json.dumps({"exit_code": completed.returncode}, ensure_ascii=False),
                )
        except Exception as exc:
            logger.warning(
                "burner.pnp_refresh.exception | %s",
                json.dumps({"error": str(exc)}, ensure_ascii=False),
            )
        finally:
            _PNP_REFRESH_CACHE["refreshed_at"] = time.monotonic()
            _invalidate_usb_probe_cache()
        return succeeded


def _probe_windows_usb_devices() -> list[dict]:
    ps_script = r"""
try {
  $baseItems = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object {
      $_.InstanceId -like 'USB\*' -or
      $_.Class -eq 'USBDevice' -or
      $_.Class -eq 'USB' -or
      $_.Class -eq 'Unknown' -or
      $_.FriendlyName -match '未知设备|Unknown device' -or
      $_.FriendlyName -match 'ST-?LINK|STM32|J-?LINK|PWLINK|GDLINK|CCID|XDS|MPLAB|Blaster|Gowin'
    })
  $propertyMap = @{}
  $instanceIds = @($baseItems | ForEach-Object { $_.InstanceId })
  if ($instanceIds.Count -gt 0) {
    $allProperties = $baseItems | Get-PnpDeviceProperty -KeyName @(
      'DEVPKEY_Device_LocationInfo',
      'DEVPKEY_Device_LocationPaths',
      'DEVPKEY_Device_Parent',
      'DEVPKEY_Device_ContainerId',
      'DEVPKEY_Device_Service'
    ) -ErrorAction SilentlyContinue
    foreach ($property in @($allProperties)) {
      $propertyMap["$($property.InstanceId)|$($property.KeyName)"] = $property.Data
    }
  }
  $items = $baseItems |
    ForEach-Object {
      $locationInfo = ''
      $locationPaths = ''
      $parent = ''
      $containerId = ''
      $service = ''
      $locationInfo = [string]$propertyMap["$($_.InstanceId)|DEVPKEY_Device_LocationInfo"]
      $locationPaths = @($propertyMap["$($_.InstanceId)|DEVPKEY_Device_LocationPaths"]) -join ';'
      $parent = [string]$propertyMap["$($_.InstanceId)|DEVPKEY_Device_Parent"]
      $containerId = [string]$propertyMap["$($_.InstanceId)|DEVPKEY_Device_ContainerId"]
      $service = [string]$propertyMap["$($_.InstanceId)|DEVPKEY_Device_Service"]
      [PSCustomObject]@{
        Name = $_.FriendlyName
        Manufacturer = $_.Manufacturer
        DeviceID = $_.InstanceId
        PNPClass = $_.Class
        Status = $_.Status
        LocationInformation = $locationInfo
        LocationPaths = $locationPaths
        LocationInfo = $locationInfo
        Parent = $parent
        ContainerId = $containerId
        Service = $service
      }
    }
  @($items) | ConvertTo-Json -Depth 3 -Compress
} catch {
  @() | ConvertTo-Json -Compress
}
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            timeout=_get_env_float("PCIDS_WINDOWS_USB_PROBE_TIMEOUT_SECONDS", 20, min_value=2, max_value=60),
            check=False,
        )
        stdout = (completed.stdout or b"").decode("utf-8", errors="ignore")
        if completed.returncode != 0 and not stdout.strip():
            stderr = (completed.stderr or b"").decode("utf-8", errors="ignore")
            raise RuntimeError(stderr.strip() or f"powershell exited {completed.returncode}")
        payload = json.loads(stdout or "[]")
        if isinstance(payload, dict):
            payload = [payload]
    except Exception as exc:
        logger.exception("burner.usb_probe.failed | %s", json.dumps({"platform": platform.system(), "error": str(exc)}, ensure_ascii=False))
        return []

    items: list[dict] = []
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        device_id = str(row.get("DeviceID") or "").strip()
        name = str(row.get("Name") or "").strip()
        manufacturer = str(row.get("Manufacturer") or "").strip()
        vendor_id = ""
        product_id = ""
        vid_match = re.search(r"VID_([0-9A-Fa-f]{4})", device_id)
        pid_match = re.search(r"PID_([0-9A-Fa-f]{4})", device_id)
        if vid_match:
            vendor_id = vid_match.group(1).upper()
        if pid_match:
            product_id = pid_match.group(1).upper()
        serial = device_id.split("\\")[-1] if "\\" in device_id else device_id
        location_path = str(row.get("LocationPaths") or "").strip()
        location_info = str(row.get("LocationInfo") or row.get("LocationInformation") or "").strip()
        device_manager_location = location_info or location_path
        items.append(
            {
                "_name": name or manufacturer,
                "product_name": name,
                "manufacturer": manufacturer,
                "serial_num": serial,
                "location_id": device_manager_location or device_id,
                "device_manager_location": device_manager_location or None,
                "location_path": location_path or None,
                "location_info": location_info or None,
                "pnp_device_id": device_id,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "pnp_class": str(row.get("PNPClass") or "").strip(),
                "status": str(row.get("Status") or "").strip(),
                "parent_id": str(row.get("Parent") or "").strip(),
                "container_id": str(row.get("ContainerId") or "").strip(),
                "driver_service": str(row.get("Service") or "").strip(),
                "source": "windows_pnp",
            }
        )
    return items


def _probe_stlink_serials(cache_ttl_seconds: float = 5.0) -> list[str]:
    now = time.monotonic()
    if now < float(_STLINK_SERIAL_CACHE.get("expires_at") or 0):
        return list(_STLINK_SERIAL_CACHE.get("serials") or [])
    cli_path = str(os.environ.get("STLINK_UTILITY_CLI") or "").strip()
    if not cli_path or not os.path.isfile(cli_path):
        return []
    try:
        completed = subprocess.run(
            [cli_path, "-List"],
            capture_output=True,
            timeout=12,
            check=False,
        )
        output = ((completed.stdout or b"") + b"\n" + (completed.stderr or b"")).decode("utf-8", errors="ignore")
        serials = list(
            dict.fromkeys(
                re.findall(r"(?:ST-LINK\s+)?SN\s*:\s*([0-9A-Za-z_-]+)", output, re.IGNORECASE)
            )
        )
    except Exception as exc:
        logger.warning("burner.stlink_serial_probe.failed | %s", str(exc))
        serials = []
    _STLINK_SERIAL_CACHE["serials"] = serials
    _STLINK_SERIAL_CACHE["expires_at"] = time.monotonic() + max(cache_ttl_seconds, 0.0)
    return list(serials)


def _read_linux_sysfs_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fp:
            return fp.read().strip()
    except Exception:
        return ""


def _probe_linux_usb_devices() -> list[dict]:
    root = "/sys/bus/usb/devices"
    if not os.path.isdir(root):
        return []

    items: list[dict] = []
    for entry in os.listdir(root):
        device_dir = os.path.join(root, entry)
        if not os.path.isdir(device_dir):
            continue
        busnum = _read_linux_sysfs_text(os.path.join(device_dir, "busnum"))
        devpath = _read_linux_sysfs_text(os.path.join(device_dir, "devpath"))
        product = _read_linux_sysfs_text(os.path.join(device_dir, "product"))
        manufacturer = _read_linux_sysfs_text(os.path.join(device_dir, "manufacturer"))
        serial = _read_linux_sysfs_text(os.path.join(device_dir, "serial"))
        vendor_id = _read_linux_sysfs_text(os.path.join(device_dir, "idVendor"))
        product_id = _read_linux_sysfs_text(os.path.join(device_dir, "idProduct"))
        if not (busnum or devpath or product or manufacturer or serial):
            continue

        name = " ".join(part for part in [manufacturer, product] if part).strip() or product or manufacturer
        location = "-".join(part for part in [busnum.zfill(3) if busnum else "", devpath] if part).strip("-")
        items.append(
            {
                "_name": name,
                "product_name": product,
                "manufacturer": manufacturer,
                "serial_num": serial,
                "location_id": location,
                "vendor_id": vendor_id,
                "product_id": product_id,
                "source": "linux_sysfs_usb",
            }
        )
    return items


def _probe_linux_block_devices() -> list[dict]:
    try:
        completed = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,PATH,SERIAL,MODEL,VENDOR,TRAN,HOTPLUG,RM"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        payload = json.loads(completed.stdout or "{}")
    except Exception as exc:
        logger.warning("burner.block_probe.failed | %s", json.dumps({"platform": platform.system(), "error": str(exc)}, ensure_ascii=False))
        return []

    items: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes or []:
            transport = str(node.get("tran") or "").strip().lower()
            hotplug = str(node.get("hotplug") or "").strip()
            removable = str(node.get("rm") or "").strip()
            model = str(node.get("model") or "").strip()
            vendor = str(node.get("vendor") or "").strip()
            serial = str(node.get("serial") or "").strip()
            path = str(node.get("path") or "").strip()
            if transport == "usb" or hotplug == "1" or removable == "1":
                name = " ".join(part for part in [vendor, model] if part).strip() or model or vendor or str(node.get("name") or "").strip()
                items.append(
                    {
                        "_name": name,
                        "product_name": model,
                        "manufacturer": vendor,
                        "serial_num": serial,
                        "location_id": path,
                        "source": "linux_lsblk",
                    }
                )
            walk(node.get("children") or [])

    walk(payload.get("blockdevices") or [])
    return items


def _probe_linux_devices() -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict] = []
    for item in _probe_linux_usb_devices() + _probe_linux_block_devices():
        key = (
            str(item.get("_name") or ""),
            str(item.get("serial_num") or ""),
            str(item.get("location_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _normalize_device_token(value: Optional[str]) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def _normalize_device_match_text(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _device_alias_matches(haystack: str, alias: str) -> bool:
    raw_haystack = str(haystack or "")
    raw_alias = str(alias or "").strip()
    if not raw_haystack or not raw_alias:
        return False
    normalized_alias = _normalize_device_match_text(raw_alias)
    # Very short aliases such as "sd" and "swd" must remain whole tokens.
    # Substring matching made "CMSIS-DAP V2" look like an SD card reader
    # because its normalized text contains the adjacent letters "sd".
    if len(normalized_alias) >= 4 and normalized_alias in _normalize_device_match_text(raw_haystack):
        return True
    escaped_parts = [re.escape(part) for part in re.split(r"[\s\-_]+", raw_alias.lower()) if part]
    if not escaped_parts:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\s\-_#./\\]*".join(escaped_parts) + r"(?![a-z0-9])"
    return bool(re.search(pattern, raw_haystack.lower(), re.IGNORECASE))


def _device_alias_candidates(device_type: Optional[str]) -> list[str]:
    normalized = _normalize_device_token(device_type)
    for canonical_name, aliases in DEVICE_TYPE_ALIASES:
        if _normalize_device_token(canonical_name) == normalized:
            return aliases
    return [normalized] if normalized else []


def _classify_probe_items(item: dict) -> list[dict]:
    haystack = " ".join(
        [
            str(item.get("_name") or ""),
            str(item.get("product_name") or ""),
            str(item.get("manufacturer") or ""),
        ]
    )
    for canonical_name, aliases in DEVICE_TYPE_ALIASES:
        if any(_device_alias_matches(haystack, alias) for alias in aliases):
            return [
                {
                    "type": canonical_name,
                    "device_category": "sd_reader" if canonical_name == "SD卡文件写入" else "burner",
                }
            ]
    return []


def _find_pyocd_runtime_python() -> str:
    configured = str(os.environ.get("PYOCD_PYTHON") or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    pyocd_exe = str(os.environ.get("PYOCD_EXE") or "").strip()
    if not pyocd_exe:
        return ""
    executable = Path(pyocd_exe)
    candidates = [
        executable.with_name("python.exe"),
        executable.parent.parent / "python.exe",
    ]
    return next((str(candidate) for candidate in candidates if candidate.is_file()), "")


def _probe_pyocd_probes(cache_ttl_seconds: float = 15.0) -> list[dict]:
    now = time.monotonic()
    with _PYOCD_PROBE_CACHE_LOCK:
        if now < float(_PYOCD_PROBE_CACHE.get("expires_at") or 0):
            return [dict(item) for item in (_PYOCD_PROBE_CACHE.get("probes") or [])]

    python_exe = _find_pyocd_runtime_python()
    if not python_exe:
        return []
    helper = (
        "import json;"
        "from pyocd.core.helpers import ConnectHelper;"
        "print(json.dumps(["
        "{'unique_id':str(getattr(p,'unique_id','') or '').rstrip(chr(0)).strip(),"
        "'description':str(getattr(p,'description','') or ''),"
        "'vendor_name':str(getattr(p,'vendor_name','') or ''),"
        "'product_name':str(getattr(p,'product_name','') or '')}"
        " for p in ConnectHelper.get_all_connected_probes(blocking=False)]))"
    )
    probes: list[dict] = []
    try:
        completed = subprocess.run(
            [python_exe, "-c", helper],
            capture_output=True,
            timeout=_get_env_float("PCIDS_PYOCD_PROBE_TIMEOUT_SECONDS", 6, min_value=2, max_value=15),
            check=False,
        )
        stdout = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        payload = json.loads(stdout.splitlines()[-1]) if stdout else []
        if isinstance(payload, list):
            probes = [dict(item) for item in payload if isinstance(item, dict)]
    except Exception as exc:
        logger.warning("burner.pyocd_probe.failed | %s", str(exc))

    with _PYOCD_PROBE_CACHE_LOCK:
        _PYOCD_PROBE_CACHE["probes"] = probes
        _PYOCD_PROBE_CACHE["expires_at"] = time.monotonic() + max(cache_ttl_seconds, 0.0)
    return [dict(item) for item in probes]


def _pyocd_probe_burner_type(serial: Optional[str], pyocd_probes: list[dict]) -> str:
    normalized_serial = _normalize_binding_sn(str(serial or "").rstrip("\x00"))
    if not normalized_serial:
        return ""
    for probe in pyocd_probes:
        probe_serial = _normalize_binding_sn(str(probe.get("unique_id") or "").rstrip("\x00"))
        if probe_serial != normalized_serial:
            continue
        identity = " ".join(
            str(probe.get(field) or "")
            for field in ("description", "vendor_name", "product_name")
        ).lower()
        if "gdlink" in identity or "gigadevice" in identity:
            return "GDLINK"
        if "pwlink" in identity or "pw-link" in identity:
            return "PWLINK2"
        if "stlink" in identity or "st-link" in identity:
            return "ST-LINK"
    return ""


def _usb_item_product_key(item: dict) -> tuple[str, str]:
    return (
        str(item.get("vendor_id") or "").strip().lower(),
        str(item.get("product_id") or "").strip().lower(),
    )


def _usb_item_instance_ids(item: dict) -> set[str]:
    return {
        str(value or "").strip().lower()
        for value in (
            item.get("pnp_device_id"),
            item.get("device_id"),
            item.get("location_id"),
            item.get("location_id_hex"),
            item.get("registry_id"),
        )
        if str(value or "").strip()
    }


def _usb_items_are_same_composite_device(left: dict, right: dict) -> bool:
    """Return whether two PnP rows represent interfaces of one USB device.

    Windows often reports a composite parent as ``USB Composite Device`` and
    exposes the useful burner name only on a child interface.  A shared
    container is the strongest signal.  Parent/child relationships are also
    accepted when both rows carry the same VID/PID, which avoids treating a
    USB hub and all of its attached devices as one physical burner.
    """

    if left is right:
        return True
    left_container = str(left.get("container_id") or "").strip().lower()
    right_container = str(right.get("container_id") or "").strip().lower()
    if left_container and right_container and left_container == right_container:
        return True

    left_product = _usb_item_product_key(left)
    right_product = _usb_item_product_key(right)
    if not all(left_product) or left_product != right_product:
        return False
    left_ids = _usb_item_instance_ids(left)
    right_ids = _usb_item_instance_ids(right)
    left_parent = str(left.get("parent_id") or "").strip().lower()
    right_parent = str(right.get("parent_id") or "").strip().lower()
    return bool((left_parent and left_parent in right_ids) or (right_parent and right_parent in left_ids))


def _classified_usb_device_types(
    item: dict,
    usb_devices: list[dict],
    pyocd_probes: Optional[list[dict]] = None,
) -> set[str]:
    """Collect confident burner types from a composite device and its interfaces."""

    classified_types: set[str] = set()
    for related in usb_devices:
        if not _usb_items_are_same_composite_device(item, related):
            continue
        classified_types.update(
            _normalize_device_token(classified.get("type"))
            for classified in _classify_probe_items(related)
            if classified.get("type")
        )
        if pyocd_probes:
            probe_type = _pyocd_probe_burner_type(
                _get_real_usb_serial(related, usb_devices, None),
                pyocd_probes,
            )
            if probe_type:
                classified_types.add(_normalize_device_token(probe_type))
    return classified_types


def _burner_node_key(burner: Burner) -> str:
    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    host_address = str(getattr(burner, "host_address", None) or "").strip()
    return agent_url or host_address or "local"


def _get_service_node_addresses() -> set[str]:
    addresses = {"127.0.0.1", "::1", "localhost"}
    try:
        hostname = socket.gethostname()
        addresses.add(hostname.lower())
        for item in socket.gethostbyname_ex(hostname)[2]:
            if item:
                addresses.add(item)
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            addresses.add(sock.getsockname()[0])
    except Exception:
        pass
    return {item for item in addresses if item}


def _get_service_node_address() -> str:
    local_addresses = _get_service_node_addresses()
    configured_server = str(
        _get_repository_server_transport_config().get("host") or ""
    ).strip()
    configured_host = (
        urlparse(configured_server).hostname or configured_server
    ).strip().lower()
    if configured_host:
        normalized_local_addresses = {str(item or "").strip().lower() for item in local_addresses}
        if configured_host in normalized_local_addresses:
            return configured_host
        try:
            configured_ips = {
                str(item or "").strip().lower()
                for item in socket.gethostbyname_ex(configured_host)[2]
                if str(item or "").strip()
            }
            matching_ips = sorted(configured_ips & normalized_local_addresses)
            if matching_ips:
                return matching_ips[0]
        except Exception:
            pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            route_address = str(sock.getsockname()[0] or "").strip()
            if route_address and route_address != "127.0.0.1":
                # A proxy/VPN adapter may own the default route. Only prefer
                # it when it is also exposed by a physical active interface.
                physical_addresses: set[str] = set()
                virtual_markers = (
                    "meta", "vmware", "virtual", "vbox", "hyper-v",
                    "docker", "wsl", "vpn", "tunnel", "teredo",
                )
                if psutil is not None:
                    interface_stats = psutil.net_if_stats()
                    for interface_name, addresses in psutil.net_if_addrs().items():
                        stats = interface_stats.get(interface_name)
                        if (
                            not stats
                            or not stats.isup
                            or any(marker in interface_name.lower() for marker in virtual_markers)
                        ):
                            continue
                        for item in addresses:
                            if item.family == socket.AF_INET and item.address:
                                physical_addresses.add(str(item.address).strip())
                if not physical_addresses or route_address in physical_addresses:
                    return route_address
    except Exception:
        pass
    addresses = sorted(
        item
        for item in local_addresses
        if item not in {"127.0.0.1", "::1", "localhost"}
    )
    if psutil is not None:
        virtual_markers = (
            "meta", "vmware", "virtual", "vbox", "hyper-v",
            "docker", "wsl", "vpn", "tunnel", "teredo",
        )
        try:
            interface_stats = psutil.net_if_stats()
            physical_ipv4 = sorted({
                str(item.address).strip()
                for interface_name, interface_addresses in psutil.net_if_addrs().items()
                if interface_stats.get(interface_name)
                and interface_stats[interface_name].isup
                and not any(marker in interface_name.lower() for marker in virtual_markers)
                for item in interface_addresses
                if item.family == socket.AF_INET
                and item.address
                and not ipaddress.ip_address(item.address).is_loopback
            })
            if physical_ipv4:
                return physical_ipv4[0]
        except Exception:
            pass
    ipv4_addresses = []
    for item in addresses:
        try:
            parsed = ipaddress.ip_address(item)
        except ValueError:
            continue
        if parsed.version == 4 and not parsed.is_loopback:
            ipv4_addresses.append(parsed.compressed)
    return ipv4_addresses[0] if ipv4_addresses else (addresses[0] if addresses else "127.0.0.1")


def _get_request_node_address(request: Optional[Request]) -> str:
    if not request:
        return ""
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "") or ""


@lru_cache(maxsize=128)
def _collect_node_address_aliases(value: Optional[str]) -> frozenset[str]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return frozenset()
    host = urlparse(raw_value).hostname or raw_value
    normalized = host.strip().lower()
    if not normalized:
        return frozenset()
    aliases = {normalized}
    try:
        parsed_ip = ipaddress.ip_address(normalized)
        aliases.add(parsed_ip.compressed.lower())
    except ValueError:
        pass
    try:
        resolved_host, host_aliases, resolved_ips = socket.gethostbyname_ex(normalized)
        aliases.add(str(resolved_host or "").strip().lower())
        aliases.update(str(item or "").strip().lower() for item in host_aliases or [])
        for resolved_ip in resolved_ips or []:
            resolved_text = str(resolved_ip or "").strip().lower()
            if not resolved_text:
                continue
            aliases.add(resolved_text)
            try:
                aliases.add(ipaddress.ip_address(resolved_text).compressed.lower())
            except ValueError:
                pass
    except Exception:
        pass
    return frozenset(item for item in aliases if item)


def _get_configured_server_addresses() -> frozenset[str]:
    """Resolve the configured server on every request.

    The server address is externally configurable at runtime.  Caching it made
    the device list continue to show the old IP after an administrator changed
    the YAML configuration.
    """
    host = str(_get_repository_server_transport_config().get("host") or "").strip()
    return frozenset(_collect_node_address_aliases(host))


def _is_configured_server_node(address: Optional[str]) -> bool:
    configured_addresses = _get_configured_server_addresses()
    if not configured_addresses:
        return False
    return bool(_collect_node_address_aliases(address) & set(configured_addresses))


def _is_same_node_address(left: str, right: str) -> bool:
    left_value = str(left or "").strip().lower()
    right_value = str(right or "").strip().lower()
    if not left_value or not right_value:
        return False
    if left_value == right_value:
        return True
    if _collect_node_address_aliases(left_value) & _collect_node_address_aliases(right_value):
        return True
    local_aliases = {"127.0.0.1", "::1", "localhost", "local"}
    service_addresses = _get_service_node_addresses()
    left_is_local = left_value in local_aliases or left_value in service_addresses
    right_is_local = right_value in local_aliases or right_value in service_addresses
    return left_is_local and right_is_local


def _is_agent_url_for_service_node(agent_url: Optional[str]) -> bool:
    value = str(agent_url or "").strip()
    if not value:
        return False
    try:
        host = str(urlparse(value).hostname or "").strip()
    except ValueError:
        return False
    return bool(host and _is_same_node_address(host, _get_service_node_address()))


def _is_local_burner_owner(burner: Burner) -> bool:
    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    host_type = str(getattr(burner, "host_type", None) or "").strip().lower()
    host_address = str(getattr(burner, "host_address", None) or "").strip()
    if agent_url or host_type in {"agent", "server"}:
        return False
    return not _is_configured_server_node(host_address)


def _is_burner_owned_by_service_node(burner: Burner) -> bool:
    """Return whether a non-Agent burner belongs to this backend machine."""
    if str(getattr(burner, "agent_url", None) or "").strip():
        return False
    host_address = str(getattr(burner, "host_address", None) or "").strip()
    if not host_address:
        # Legacy local/server rows did not persist host_address.
        return True
    return _is_same_node_address(host_address, _get_service_node_address())


def _is_local_candidate_node(candidate: dict) -> bool:
    return (
        str(candidate.get("node_type") or "").strip().lower() == "local"
        and not str(candidate.get("agent_url") or "").strip()
    )


def _is_same_burner_candidate_node(burner: Burner, candidate: dict) -> bool:
    if _is_same_node_address(str(candidate.get("node_key") or ""), _burner_node_key(burner)):
        return True
    return _is_local_burner_owner(burner) and _is_local_candidate_node(candidate)


def _ensure_burner_owner_node(payload: dict) -> dict:
    next_payload = dict(payload)
    agent_url = str(next_payload.get("agent_url") or "").strip()
    host_type = str(next_payload.get("host_type") or "").strip().lower()
    if host_type and host_type not in {"local", "server", "agent"}:
        raise HTTPException(status_code=422, detail="设备节点类型只能选择本地、服务器或局域网其他节点")
    if host_type == "agent" and not agent_url:
        raise HTTPException(status_code=422, detail="局域网其他节点必须填写有效的 Agent 地址")
    if agent_url:
        try:
            agent_url = _normalize_agent_url(agent_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        next_payload["agent_url"] = agent_url
        next_payload["host_type"] = "agent"
        if not str(next_payload.get("host_address") or "").strip():
            next_payload["host_address"] = urlparse(agent_url).hostname or ""
        return next_payload
    if host_type not in {"server", "local"}:
        host_type = "local"
    next_payload["host_type"] = host_type
    if not str(next_payload.get("host_address") or "").strip():
        next_payload["host_address"] = _get_service_node_address()
    return next_payload


def _resolve_node_display(burner: Burner, request: Optional[Request]) -> dict:
    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    host_name = str(getattr(burner, "host_name", None) or "").strip()
    host_address = str(getattr(burner, "host_address", None) or "").strip()
    owner_address = host_address or (urlparse(agent_url).hostname if agent_url else "") or _get_service_node_address()
    current_address = _get_request_node_address(request)
    is_local = _is_same_node_address(owner_address, current_address)
    if _is_configured_server_node(owner_address):
        return {"label": "服务器", "owner_address": owner_address, "current_address": current_address, "is_local": False}
    if is_local:
        return {"label": "本地", "owner_address": owner_address, "current_address": current_address, "is_local": True}
    if owner_address:
        # Node positions identify a machine.  For all non-server machines show
        # their address consistently, including an Agent node.
        return {"label": owner_address, "owner_address": owner_address, "current_address": current_address, "is_local": False}
    return {
        "label": host_name or "本地节点",
        "owner_address": owner_address,
        "current_address": current_address,
        "is_local": False,
    }


def _derive_node_label(agent_url: Optional[str], host_name: Optional[str] = None, host_address: Optional[str] = None) -> str:
    address = str(host_address or "").strip() or (urlparse(str(agent_url or "")).hostname or "").strip()
    if _is_configured_server_node(address):
        return "服务器"
    if _is_same_node_address(address, _get_service_node_address()):
        return "本地"
    if address:
        return address
    return "本地"


USB_BINDING_FIELDS = (
    "location_info",
    "location_path",
    "pnp_device_id",
    "parent_id",
    "container_id",
    "vendor_id",
    "product_id",
    "driver_service",
)
USB_BINDING_IDENTITY_FIELDS = (
    "location_info",
    "location_path",
    "pnp_device_id",
    "container_id",
)
USB_BINDING_DIRECT_PORT_FIELDS = (
    "location_info",
    "pnp_device_id",
)


def _normalize_usb_binding(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        field: str(value.get(field) or "").strip()
        for field in USB_BINDING_FIELDS
        if str(value.get(field) or "").strip()
    }


def _build_usb_binding(item: dict) -> dict:
    return _normalize_usb_binding({field: item.get(field) for field in USB_BINDING_FIELDS})


def _usb_bindings_match_device_identity(expected: object, current: object) -> bool:
    """Match the exact USB identity captured by a user-triggered scan.

    A physical port alone is not a device identity because another probe can
    later be inserted into the same port.  The full Windows PnP instance id is
    strong enough to confirm the device that was just scanned.  Container id
    is accepted only together with the same VID/PID and only when it is not the
    all-zero placeholder emitted by some drivers.
    """
    expected_binding = _normalize_usb_binding(expected)
    current_binding = _normalize_usb_binding(current)

    expected_pnp = str(expected_binding.get("pnp_device_id") or "").strip().casefold()
    current_pnp = str(current_binding.get("pnp_device_id") or "").strip().casefold()
    if expected_pnp and current_pnp and expected_pnp == current_pnp:
        return True

    expected_container = str(expected_binding.get("container_id") or "").strip().casefold()
    current_container = str(current_binding.get("container_id") or "").strip().casefold()
    container_token = re.sub(r"[{}\-]", "", expected_container)
    if (
        expected_container
        and current_container
        and expected_container == current_container
        and container_token
        and any(char != "0" for char in container_token)
    ):
        expected_product = (
            str(expected_binding.get("vendor_id") or "").strip().casefold(),
            str(expected_binding.get("product_id") or "").strip().casefold(),
        )
        current_product = (
            str(current_binding.get("vendor_id") or "").strip().casefold(),
            str(current_binding.get("product_id") or "").strip().casefold(),
        )
        return bool(all(expected_product) and expected_product == current_product)
    return False


def _burner_usb_binding(burner: Optional[Burner]) -> dict:
    if burner is None:
        return {}
    try:
        config = json.loads(getattr(burner, "config_json", None) or "{}") or {}
    except Exception:
        return {}
    return _normalize_usb_binding(config.get("usb_binding"))


def _usb_binding_port_values(binding: object) -> set[str]:
    normalized = _normalize_usb_binding(binding)
    return _port_match_values(*(normalized.get(field) for field in USB_BINDING_IDENTITY_FIELDS))


def _usb_binding_direct_port_values(binding: object) -> set[str]:
    normalized = _normalize_usb_binding(binding)
    return _port_match_values(*(normalized.get(field) for field in USB_BINDING_DIRECT_PORT_FIELDS))


def _build_discovery_candidates(
    item: dict,
    agent_url: Optional[str],
    host_name: Optional[str] = None,
    host_address: Optional[str] = None,
    *,
    usb_devices: Optional[list[dict]] = None,
    pyocd_probes: Optional[list[dict]] = None,
) -> list[dict]:
    classified_items = _classify_probe_items(item)
    node_key = str(agent_url or host_address or "").strip() or "local"
    node_label = _derive_node_label(agent_url, host_name=host_name, host_address=host_address)
    node_type = "agent" if str(agent_url or "").strip() else ("server" if _is_configured_server_node(host_address) else "local")
    raw_port = str(item.get("location_id") or item.get("location_id_hex") or item.get("registry_id") or "").strip() or None
    port = _get_physical_usb_port(item, raw_port) or raw_port
    raw_name = str(item.get("_name") or item.get("product_name") or item.get("manufacturer") or "").strip()
    if usb_devices is None and (classified_items or "dap" in raw_name.lower()) and not str(agent_url or "").strip():
        usb_devices = _probe_usb_devices()
    if not classified_items and usb_devices and pyocd_probes:
        probe_type = _pyocd_probe_burner_type(
            _get_real_usb_serial(item, usb_devices, None),
            pyocd_probes,
        )
        if probe_type:
            classified_items = [{"type": probe_type, "device_category": "burner"}]
    if not classified_items and not port:
        return []
    alternative_ports = [
        value
        for value in [
            item.get("pnp_device_id"),
            item.get("location_path"),
            item.get("location_info"),
            item.get("parent_id"),
            raw_port,
        ]
        if str(value or "").strip() and str(value or "").strip() != str(port or "").strip()
    ]
    usb_binding = _build_usb_binding(item)
    candidates: list[dict] = []
    for classified in classified_items:
        sn = _get_real_usb_serial(item, usb_devices, classified["type"]) or None
        candidate_id = _candidate_physical_id(item, classified["type"], node_key, sn, port)
        candidates.append(
            {
                "candidate_id": candidate_id,
                "type": classified["type"],
                "device_category": classified["device_category"],
                "detected_name": raw_name or classified["type"],
                "sn": sn,
                "port": port,
                "raw_port": raw_port,
                "alternative_ports": alternative_ports,
                "usb_binding": usb_binding,
                "node_key": node_key,
                "node_type": node_type,
                "node_label": node_label,
                "agent_url": str(agent_url or "").strip() or None,
                "host_name": host_name,
                "host_address": host_address,
                "vendor_id": str(item.get("vendor_id") or "").strip() or None,
                "product_id": str(item.get("product_id") or "").strip() or None,
                "possible_types": [],
                "requires_type_resolution": False,
                "source": str(item.get("source") or "usb_probe"),
                "probe_only": False,
            }
        )
    if not classified_items and port:
        sn = _get_real_usb_serial(item, usb_devices, None) or None
        candidate_id = _candidate_physical_id(item, LOCATION_PROBE_CANDIDATE_TYPE, node_key, None, port)
        candidates.append(
            {
                "candidate_id": candidate_id,
                "type": LOCATION_PROBE_CANDIDATE_TYPE,
                "device_category": "probe_only",
                "detected_name": raw_name or "USB Device",
                "sn": sn,
                "port": port,
                "raw_port": raw_port,
                "alternative_ports": alternative_ports,
                "usb_binding": usb_binding,
                "node_key": node_key,
                "node_type": node_type,
                "node_label": node_label,
                "agent_url": str(agent_url or "").strip() or None,
                "host_name": host_name,
                "host_address": host_address,
                "vendor_id": str(item.get("vendor_id") or "").strip() or None,
                "product_id": str(item.get("product_id") or "").strip() or None,
                "possible_types": [],
                "requires_type_resolution": False,
                "source": str(item.get("source") or "usb_probe"),
                "probe_only": True,
            }
        )
    return candidates


def _resolve_ambiguous_candidate_types(candidates: list[dict], burners: list[Burner]) -> list[dict]:
    """Resolve one generic candidate only from an existing immutable serial.

    A USB port, parent hub, container id, or VID/PID describes where/how a
    device is connected; none of them proves that it is the registered
    physical burner.  An unresolved cable therefore remains generic unless its
    real serial matches exactly one registered burner on the same node.
    """
    resolved: list[dict] = []
    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        possible_types = {
            str(item).strip()
            for item in candidate.get("possible_types") or []
            if str(item).strip()
        }
        if not possible_types:
            resolved.append(candidate)
            continue

        candidate_sn = _normalize_binding_sn(candidate.get("sn"))
        matches: list[Burner] = []
        if not candidate_sn:
            resolved.append(candidate)
            continue
        for burner in burners:
            if str(getattr(burner, "type", None) or "").strip() not in possible_types:
                continue
            if not _is_same_node_address(candidate.get("node_key"), _burner_node_key(burner)):
                continue
            burner_sn = _normalize_binding_sn(getattr(burner, "sn", None))
            if burner_sn and burner_sn == candidate_sn:
                matches.append(burner)

        if len(matches) == 1:
            candidate["type"] = str(matches[0].type)
            candidate["requires_type_resolution"] = False
            candidate["resolved_burner_id"] = matches[0].id
        resolved.append(candidate)
    return resolved


def _find_usb_parent_serial(usb_devices: list[dict], vendor_id: str, product_id: str) -> str:
    if not vendor_id or not product_id:
        return ""
    for item in usb_devices:
        item_vendor_id = str(item.get("vendor_id") or "").strip().lower()
        item_product_id = str(item.get("product_id") or "").strip().lower()
        location_id = str(item.get("location_id") or "").strip().upper()
        if item_vendor_id != vendor_id or item_product_id != product_id:
            continue
        if "&MI_" in location_id:
            continue
        serial = _get_real_usb_serial(item)
        if serial:
            return serial
    return ""


def _extract_usb_instance_serial(device_id: str, vendor_id: str, product_id: str) -> str:
    text = str(device_id or "").strip()
    if not text or not vendor_id or not product_id:
        return ""
    pattern = rf"^USB\\VID_{re.escape(vendor_id)}&PID_{re.escape(product_id)}\\(.+)$"
    match = re.match(pattern, text, re.IGNORECASE)
    if not match:
        return ""
    serial = match.group(1).strip()
    return "" if _looks_like_windows_location_instance_id(serial) else serial


def _looks_like_windows_location_instance_id(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"[0-9A-Fa-f]+&[0-9A-Fa-f]+(?:&[0-9A-Fa-f]+)+", text))


def _get_real_usb_serial(item: dict, usb_devices: Optional[list[dict]] = None, device_type: Optional[str] = None) -> str:
    serial = str(item.get("serial_num") or item.get("serial_number") or "").strip()
    source = str(item.get("source") or "").strip().lower()
    vendor_id = str(item.get("vendor_id") or "").strip().lower()
    product_id = str(item.get("product_id") or "").strip().lower()

    # FTDI's Windows PnP entry often exposes a blank serial_num even though
    # its InstanceId contains the immutable FT232 serial.  Preserve that
    # identity for AL321 registration/rebinding, while rejecting Windows'
    # location-derived instance suffixes in _extract_usb_instance_serial.
    if not serial and _normalize_device_token(device_type) == _normalize_device_token("AL321"):
        serial = _extract_usb_instance_serial(
            str(item.get("pnp_device_id") or item.get("device_id") or ""),
            "0403",
            "6014",
        )

    if _normalize_device_token(device_type) == _normalize_device_token("PWLINK2") and usb_devices:
        parent_serial = _extract_usb_instance_serial(str(item.get("parent_id") or ""), vendor_id, product_id)
        if parent_serial:
            return parent_serial
        parent_serial = _find_usb_parent_serial(usb_devices, vendor_id, product_id)
        if parent_serial:
            return parent_serial

    if source == "windows_pnp" and _looks_like_windows_location_instance_id(serial):
        if usb_devices and vendor_id and product_id:
            parent_serial = _extract_usb_instance_serial(str(item.get("parent_id") or ""), vendor_id, product_id)
            if parent_serial:
                return parent_serial
            parent_serial = _find_usb_parent_serial(usb_devices, vendor_id, product_id)
            if parent_serial:
                return parent_serial
        return ""
    return serial


def _normalize_windows_usb_physical_port(port: str) -> str:
    text = str(port or "").strip()
    if not text:
        return ""
    text = re.sub(r"&MI_[0-9A-Fa-f]{2}", "", text, count=1)
    match = re.match(r"^(USB\\VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4})\\(.+)$", text, re.IGNORECASE)
    if not match:
        return text
    instance = re.sub(r"&[0-9A-Fa-f]{4}$", "", match.group(2).strip())
    return f"{match.group(1).upper()}\\{instance.upper()}"


def _is_same_usb_product_path(path: str, vendor_id: str, product_id: str) -> bool:
    text = str(path or "").strip()
    if not text or not vendor_id or not product_id:
        return False
    pattern = rf"^USB\\VID_{re.escape(vendor_id)}&PID_{re.escape(product_id)}(?:\\|&)"
    return bool(re.match(pattern, text, re.IGNORECASE))


def _get_physical_usb_port(item: dict, raw_port: Optional[str] = None) -> str:
    device_manager_location = str(
        item.get("device_manager_location")
        or item.get("location_info")
        or item.get("location_path")
        or ""
    ).strip()
    if device_manager_location:
        return device_manager_location
    vendor_id = str(item.get("vendor_id") or "").strip()
    product_id = str(item.get("product_id") or "").strip()
    parent_id = str(item.get("parent_id") or "").strip()
    if _is_same_usb_product_path(parent_id, vendor_id, product_id) and "&MI_" not in parent_id.upper():
        return parent_id
    port = str(raw_port or item.get("location_id") or item.get("location_id_hex") or item.get("registry_id") or "").strip()
    return _normalize_windows_usb_physical_port(port) or port


def _normalize_binding_value(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _normalize_binding_sn(value: Optional[str]) -> str:
    normalized = _normalize_binding_value(value)
    if not normalized:
        return ""
    return normalized.lstrip("0") or "0"


def _normalize_binding_port(value: Optional[str]) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    return _normalize_windows_usb_physical_port(raw_value).strip().lower()


def _port_match_values(*values: Optional[str]) -> set[str]:
    results: set[str] = set()
    for value in values:
        raw_value = _normalize_binding_value(value)
        normalized_port = _normalize_binding_port(value)
        if raw_value:
            results.add(raw_value)
        if normalized_port:
            results.add(normalized_port)
    return results


def _candidate_port_values(candidate: dict) -> set[str]:
    values = [candidate.get("port"), candidate.get("raw_port")]
    values.extend(candidate.get("alternative_ports") or [])
    return _port_match_values(*values) | _usb_binding_port_values(candidate.get("usb_binding"))


def _candidate_direct_port_values(candidate: dict) -> set[str]:
    return _port_match_values(candidate.get("port"), candidate.get("raw_port")) | _usb_binding_direct_port_values(
        candidate.get("usb_binding")
    )


def _candidate_has_same_known_type(burner_type: Optional[str], candidate: dict) -> bool:
    if candidate.get("probe_only"):
        return False
    return _normalize_device_token(candidate.get("type")) == _normalize_device_token(burner_type)


def _candidate_matches_registered_identity(
    burner: Burner,
    candidate: dict,
    *,
    require_same_node: bool,
) -> bool:
    """Match a registered burner without treating connection metadata as identity.

    A reliable serial may follow the same device across USB ports.  A
    serial-less device can only be considered present at its recorded port
    when the scanner independently classified it as the same burner type.
    Generic/probe-only devices never inherit a registration by port.
    """
    burner_type = str(getattr(burner, "type", None) or "").strip()
    burner_sn = _normalize_binding_sn(getattr(burner, "sn", None))
    candidate_sn = _normalize_binding_sn(candidate.get("sn"))
    same_known_type = _candidate_has_same_known_type(burner_type, candidate)
    exact_scanned_identity = _usb_bindings_match_device_identity(
        _burner_usb_binding(burner),
        candidate.get("usb_binding"),
    )
    try:
        strategy = int(getattr(burner, "strategy", 1) or 1)
    except Exception:
        strategy = 1

    if strategy == 1 and burner_sn:
        if not candidate_sn or candidate_sn != burner_sn:
            return False
        # An unclassified candidate may be resolved by its exact real serial.
        # A candidate positively classified as another burner type may not.
        if not same_known_type and not candidate.get("probe_only"):
            return False
        return not require_same_node or _is_same_burner_candidate_node(burner, candidate)

    if not same_known_type and not (candidate.get("probe_only") and exact_scanned_identity):
        return False
    burner_ports = _port_match_values(getattr(burner, "port", None)) | _usb_binding_direct_port_values(_burner_usb_binding(burner))
    if not (burner_ports and _candidate_direct_port_values(candidate) & burner_ports):
        return False
    return not require_same_node or _is_same_burner_candidate_node(burner, candidate)


def _probe_item_port_values(item: dict, port: Optional[str], raw_port: Optional[str]) -> set[str]:
    return _port_match_values(
        port,
        raw_port,
        item.get("pnp_device_id"),
        item.get("device_manager_location"),
        item.get("location_path"),
        item.get("location_info"),
        item.get("parent_id"),
    )


def _probe_item_direct_port_values(item: dict, port: Optional[str], raw_port: Optional[str]) -> set[str]:
    return _port_match_values(
        port,
        raw_port,
        item.get("pnp_device_id"),
        item.get("device_manager_location"),
        item.get("location_info"),
    )


def _candidate_physical_id(item: dict, device_type: str, node_key: str, sn: Optional[str], port: Optional[str]) -> str:
    vendor_id = str(item.get("vendor_id") or "").strip().lower()
    product_id = str(item.get("product_id") or "").strip().lower()
    container_id = str(item.get("container_id") or "").strip().lower()
    parent_id = str(item.get("parent_id") or "").strip().lower()
    location_path = str(item.get("location_path") or "").strip().lower()
    normalized_port = _normalize_windows_usb_physical_port(str(port or "")).lower()
    if sn:
        identity_value = f"sn:{sn}"
    elif location_path:
        identity_value = f"location_path:{location_path}"
    elif container_id:
        identity_value = f"container:{container_id}"
    elif parent_id:
        identity_value = f"parent:{parent_id}"
    elif normalized_port:
        identity_value = f"port:{normalized_port}"
    else:
        raw_name = str(item.get("_name") or item.get("product_name") or item.get("manufacturer") or "").strip().lower()
        identity_value = f"name:{vendor_id}:{product_id}:{raw_name}"
    return hashlib.sha256("|".join([device_type, identity_value, node_key]).encode("utf-8")).hexdigest()


def _registered_binding_keys(burners: list[Burner], exclude_id: Optional[int] = None) -> dict[str, object]:
    sn_values: set[str] = set()
    sn_by_type: dict[str, set[str]] = {}
    port_bindings: list[tuple[str, Burner, set[str]]] = []
    for burner in burners:
        if exclude_id is not None and getattr(burner, "id", None) == exclude_id:
            continue
        burner_type = _normalize_device_token(getattr(burner, "type", None))
        sn = _normalize_binding_sn(getattr(burner, "sn", None))
        burner_ports = _port_match_values(getattr(burner, "port", None)) | _usb_binding_direct_port_values(_burner_usb_binding(burner))
        if sn:
            sn_values.add(sn)
            sn_by_type.setdefault(burner_type, set()).add(sn)
        if burner_ports:
            port_bindings.append((burner_type, burner, burner_ports))
    return {
        "sn": sn_values,
        "sn_by_type": sn_by_type,
        "port_bindings": port_bindings,
    }


def _candidate_matches_registered_binding(candidate: dict, registered_keys: dict[str, object]) -> bool:
    sn = _normalize_binding_sn(candidate.get("sn"))
    candidate_ports = _candidate_direct_port_values(candidate)
    if candidate.get("probe_only"):
        return bool(sn and sn in registered_keys["sn"])
    candidate_type = _normalize_device_token(candidate.get("type"))
    sn_by_type = registered_keys.get("sn_by_type") or {}
    port_bindings = registered_keys.get("port_bindings") or []
    if sn and sn in sn_by_type.get(candidate_type, set()):
        return True
    return any(
        candidate_type == registered_type
        and bool(candidate_ports & registered_ports)
        and _is_same_burner_candidate_node(registered_burner, candidate)
        for registered_type, registered_burner, registered_ports in port_bindings
    )


def _candidate_display_score(candidate: dict) -> int:
    name = str(candidate.get("detected_name") or "").strip().lower()
    source = str(candidate.get("source") or "").strip().lower()
    sn = str(candidate.get("sn") or "").strip().lower()
    port = str(candidate.get("port") or "").strip().lower()
    score = 0
    if sn and sn in port:
        score += 40
    if port and not _looks_like_windows_location_instance_id(port.split("\\")[-1]):
        score += 15
    if "pw" in name:
        score += 40
    if "cmsis" in name or "dap" in name:
        score += 20
    if "winusb" in name:
        score += 10
    if "serial" in name or "com" in name:
        score += 5
    if source:
        score += 1
    return score


def _device_scan_priority(device_type: Optional[str]) -> int:
    normalized = _normalize_device_token(device_type)
    for index, item in enumerate(DEVICE_SCAN_PRIORITY):
        if normalized == _normalize_device_token(item):
            return len(DEVICE_SCAN_PRIORITY) - index
    return 0


def _discover_local_candidates(refresh_hardware: bool = False) -> list[dict]:
    if refresh_hardware:
        _refresh_windows_pnp_state()
    by_id: dict[str, dict] = {}
    host_address = _get_service_node_address()
    usb_devices = _probe_usb_devices(force_refresh=refresh_hardware)
    has_generic_dap = any(
        "dap" in " ".join(
            str(item.get(field) or "")
            for field in ("_name", "product_name", "manufacturer")
        ).lower()
        for item in usb_devices
    )
    pyocd_probes = _probe_pyocd_probes() if has_generic_dap else []
    for item in usb_devices:
        for candidate in _build_discovery_candidates(
            item,
            agent_url=None,
            host_address=host_address,
            usb_devices=usb_devices,
            pyocd_probes=pyocd_probes,
        ):
            current = by_id.get(candidate["candidate_id"])
            if not current or _candidate_display_score(candidate) > _candidate_display_score(current):
                by_id[candidate["candidate_id"]] = candidate
    missing_stlink_serials = [
        candidate
        for candidate in by_id.values()
        if candidate.get("type") == "ST-LINK" and not str(candidate.get("sn") or "").strip()
    ]
    official_stlink_serials = _probe_stlink_serials() if missing_stlink_serials else []
    if len(missing_stlink_serials) == 1 and len(official_stlink_serials) == 1:
        missing_stlink_serials[0]["sn"] = official_stlink_serials[0]
    return sorted(
        by_id.values(),
        key=lambda item: (
            -_device_scan_priority(item.get("type")),
            -_candidate_display_score(item),
            str(item.get("detected_name") or "").lower(),
        ),
    )


def _match_usb_device(
    device_type: Optional[str],
    location: Optional[str],
    usb_devices: Optional[list[dict]] = None,
    expected_sn: Optional[str] = None,
    expected_port: Optional[str] = None,
    expected_usb_binding: Optional[dict] = None,
) -> Optional[dict]:
    if usb_devices is None:
        usb_devices = _probe_usb_devices()
    type_hint = _normalize_device_token(device_type)
    location_hint = (location or "").strip().lower()
    expected_sn_value = _normalize_binding_sn(expected_sn)
    expected_port_values = _port_match_values(expected_port) | _usb_binding_direct_port_values(expected_usb_binding)
    candidates = _device_alias_candidates(type_hint)
    best_match: Optional[dict] = None
    best_score = -1
    stlink_serial_override = ""
    stlink_item_id: Optional[int] = None
    has_generic_dap = any(
        "dap" in " ".join(
            str(item.get(field) or "")
            for field in ("_name", "product_name", "manufacturer")
        ).lower()
        for item in usb_devices
    )
    pyocd_probes = _probe_pyocd_probes() if has_generic_dap else []
    if type_hint == _normalize_device_token("ST-LINK"):
        stlink_items = [
            item
            for item in usb_devices
            if any(
                _normalize_device_token(classified.get("type")) == type_hint
                for classified in _classify_probe_items(item)
            )
        ]
        official_stlink_serials = _probe_stlink_serials()
        # Older ST-LINK/V2 firmware exposes only a Windows location-derived
        # instance suffix through PnP.  The official CLI still returns the
        # immutable probe SN.  Only combine those sources when both sides are
        # unique, so one probe can never borrow another probe's identity.
        if len(stlink_items) == 1 and len(official_stlink_serials) == 1:
            stlink_serial_override = official_stlink_serials[0]
            stlink_item_id = id(stlink_items[0])

    for item in usb_devices:
        name = " ".join(
            [
                str(item.get("_name") or ""),
                str(item.get("product_name") or ""),
                str(item.get("manufacturer") or ""),
            ]
        ).lower()
        raw_port = str(item.get("location_id") or item.get("location_id_hex") or item.get("registry_id") or "").strip()
        port = _get_physical_usb_port(item, raw_port) or raw_port
        item_port_values = _probe_item_port_values(item, port, raw_port)
        item_direct_port_values = _probe_item_direct_port_values(item, port, raw_port)
        if location_hint and not any(location_hint in value for value in item_port_values):
            continue
        classified_types = _classified_usb_device_types(item, usb_devices, pyocd_probes)
        exact_scanned_identity = _usb_bindings_match_device_identity(
            expected_usb_binding,
            _build_usb_binding(item),
        )
        if not type_hint and not classified_types:
            continue
        serial = _get_real_usb_serial(item, usb_devices, device_type)
        if not serial and stlink_item_id == id(item):
            serial = stlink_serial_override
        normalized_serial = _normalize_binding_sn(serial)
        normalized_ports = item_direct_port_values
        if expected_sn_value and normalized_serial == expected_sn_value:
            if type_hint and classified_types and type_hint not in classified_types:
                continue
            matched = {"sn": serial or None, "port": port or None, "usb_binding": _build_usb_binding(item), "source": "usb_probe", "name": item.get("_name")}
            score = _candidate_display_score(
                {
                    "detected_name": item.get("_name"),
                    "source": item.get("source"),
                    "port": port,
                    "sn": serial,
                }
            )
            if score > best_score:
                best_score = score
                best_match = matched
            continue
        if expected_port_values and item_direct_port_values & expected_port_values:
            # A port (and its parent/container metadata) is not a device
            # identity.  It may only confirm a serial-less device that the
            # scanner independently classified as the expected burner type,
            # or the exact PnP identity explicitly captured during binding.
            if expected_sn_value or (
                type_hint
                and type_hint not in classified_types
                and not (not classified_types and exact_scanned_identity)
            ):
                continue
            matched = {"sn": serial or None, "port": port or None, "usb_binding": _build_usb_binding(item), "source": "usb_probe", "name": item.get("_name")}
            score = _candidate_display_score(
                {
                    "detected_name": item.get("_name"),
                    "source": item.get("source"),
                    "port": port,
                }
            )
            if _is_same_node_address("local", "local"):
                score += 1
            if score > best_score:
                best_score = score
                best_match = matched
            continue
        if classified_types and type_hint in classified_types:
            pass
        elif candidates and not any(_device_alias_matches(name, alias) for alias in candidates):
            continue
        if expected_sn_value and normalized_serial != expected_sn_value:
            continue
        if expected_port_values and not (normalized_ports & expected_port_values):
            continue
        if serial or port:
            matched = {"sn": serial or None, "port": port or None, "source": "usb_probe", "name": item.get("_name")}
            score = _candidate_display_score({
                "detected_name": item.get("_name"),
                "source": item.get("source"),
                "port": port,
            })
            matched_types = classified_types
            score += max(
                (_device_scan_priority(matched_type) * 100 for matched_type in matched_types),
                default=0,
            )
            if score > best_score:
                best_score = score
                best_match = matched
    return best_match


def _build_scan_result(
    device_type: Optional[str],
    location: Optional[str],
    strategy: Optional[int],
    existing: Optional[Burner],
    allow_fallback: bool = True,
    usb_devices: Optional[list[dict]] = None,
    expected_sn: Optional[str] = None,
    expected_port: Optional[str] = None,
    expected_usb_binding: Optional[dict] = None,
) -> Optional[dict]:
    try:
        runtime_strategy = int(strategy or getattr(existing, "strategy", 1) if existing else strategy or 1)
    except Exception:
        runtime_strategy = 1
    expected_sn = expected_sn or (getattr(existing, "sn", None) if existing is not None else None)
    expected_port = expected_port or (getattr(existing, "port", None) if existing is not None else None)
    if runtime_strategy == 1 and not _normalize_binding_value(expected_port) and existing is not None:
        expected_port = getattr(existing, "location", None)
    if existing is not None and not allow_fallback:
        if not _normalize_binding_value(expected_sn) and not _port_match_values(expected_port):
            return None
    if runtime_strategy == 1 and _normalize_binding_sn(expected_sn):
        match_location = None
    else:
        match_location = expected_port if runtime_strategy == 2 else (expected_port or location)
    matched = _match_usb_device(
        device_type,
        match_location,
        usb_devices=usb_devices,
        expected_sn=expected_sn if runtime_strategy == 1 else None,
        expected_port=expected_port,
        expected_usb_binding=expected_usb_binding or _burner_usb_binding(existing),
    )
    if matched:
        return {
            **matched,
            "online": True,
        }

    if _is_test_online_burner(existing):
        seed_parts = [
            str(existing.id) if existing else "",
            existing.name if existing else "",
            device_type or "",
            location or "",
            str(strategy or existing.strategy if existing else strategy or 1),
            "test-online",
        ]
        return {
            "sn": _stable_identifier("SN", seed_parts, 24),
            "port": f"USB-{_stable_identifier('', seed_parts, 4)}-{_stable_identifier('', list(reversed(seed_parts)), 4)}",
            "source": "test_default_online",
            "name": existing.name if existing else device_type or "Unknown",
            "online": True,
        }

    if not allow_fallback:
        return None

    seed_parts = [
        str(existing.id) if existing else "",
        existing.name if existing else "",
        device_type or "",
        location or "",
        str(strategy or existing.strategy if existing else strategy or 1),
    ]
    return {
        "sn": _stable_identifier("SN", seed_parts, 24),
        "port": f"USB-{_stable_identifier('', seed_parts, 4)}-{_stable_identifier('', list(reversed(seed_parts)), 4)}",
        "source": "deterministic_fallback",
        "name": existing.name if existing else device_type or "Unknown",
        "online": False,
    }


def _build_agent_endpoint(base_url: str, path: str) -> str:
    normalized_base = str(base_url or "").strip().rstrip("/")
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    if normalized_base.endswith("/api"):
        return normalized_base + normalized_path
    return normalized_base + "/api" + normalized_path


def _remote_scan_burner(
    agent_url: str,
    device_type: Optional[str],
    location: Optional[str],
    strategy: Optional[int],
    sn: Optional[str] = None,
    port: Optional[str] = None,
    usb_binding: Optional[dict] = None,
    timeout_seconds: Optional[float] = None,
) -> dict:
    import urllib.request

    logger.info(
        "burner.remote_scan.start | %s",
        json.dumps(
            {
                "agent_url": agent_url,
                "device_type": device_type,
                "location": location,
                "strategy": strategy,
            },
            ensure_ascii=False,
        ),
    )
    payload = json.dumps({
        "type": device_type,
        "location": location,
        "strategy": strategy,
        "sn": sn,
        "port": port,
        "usb_binding": _normalize_usb_binding(usb_binding),
        "allow_fallback": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        _build_agent_endpoint(agent_url, "/burners/agent/scan"),
        data=payload,
        headers={"Content-Type": "application/json", **build_agent_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds or REMOTE_SCAN_TIMEOUT_SECONDS) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    parsed = json.loads(body) if body else {}
    logger.info(
        "burner.remote_scan.done | %s",
        json.dumps(
            {
                "agent_url": agent_url,
                "device_type": device_type,
                "strategy": strategy,
                "online": bool(parsed.get("data", {}).get("online")),
                "source": parsed.get("data", {}).get("source"),
                "message": parsed.get("message"),
            },
            ensure_ascii=False,
        ),
    )
    return parsed


def _remote_discover_devices(agent_url: str, timeout_seconds: Optional[float] = None) -> dict:
    import urllib.request

    req = urllib.request.Request(
        _build_agent_endpoint(agent_url, "/burners/agent/discovery"),
        data=json.dumps({}).encode("utf-8"),
        headers={"Content-Type": "application/json", **build_agent_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds or REMOTE_DISCOVERY_TIMEOUT_SECONDS) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    return json.loads(body) if body else {}


def _normalize_agent_url(value: Optional[str]) -> str:
    agent_url = str(value or "").strip().rstrip("/")
    if not agent_url:
        return ""
    parsed = urlparse(agent_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("下位机 Agent 地址格式不正确，请填写例如：http://192.168.1.20:8000")
    return agent_url


def _get_agent_discovery_config_path() -> Path:
    configured = str(os.environ.get(_AGENT_DISCOVERY_CONFIG_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return get_app_data_root() / "agent-discovery.yaml"
    return Path.home() / ".config" / "pcids" / "agent-discovery.yaml"


def _load_agent_discovery_config() -> dict:
    path = _get_agent_discovery_config_path()
    if not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning("burner.agent_discovery.config_invalid | path=%s error=%s", path, exc)
        return {}


def _get_agent_discovery_port(config: Optional[dict] = None) -> int:
    payload = config or {}
    port_raw = str(
        os.environ.get("PCIDS_AGENT_DISCOVERY_PORT")
        or payload.get("port")
        or "8000"
    ).strip()
    try:
        port = int(port_raw)
    except ValueError:
        return 8000
    return port if 1 <= port <= 65535 else 8000


def _get_lan_agent_scan_networks() -> list[ipaddress.IPv4Network]:
    config = _load_agent_discovery_config()
    configured = str(os.environ.get("PCIDS_AGENT_DISCOVERY_CIDRS") or "").strip()
    configured_networks = config.get("discovery_cidrs") if not configured else configured
    if isinstance(configured_networks, (list, tuple)):
        raw_networks = [str(item).strip() for item in configured_networks if str(item).strip()]
    else:
        raw_networks = [item.strip() for item in str(configured_networks or "").split(",") if item.strip()]
    if not raw_networks:
        # Scan every active physical LAN adapter, not merely the adapter used
        # for the Internet default route.  Virtual/VPN adapters are excluded:
        # otherwise each extra adapter adds another 254-host timeout window.
        virtual_markers = ("meta", "vmware", "virtual", "vbox", "hyper-v", "docker", "wsl", "vpn", "tunnel", "teredo")
        if psutil is not None:
            try:
                interface_stats = psutil.net_if_stats()
                for interface_name, addresses in psutil.net_if_addrs().items():
                    stats = interface_stats.get(interface_name)
                    normalized_name = interface_name.lower()
                    if not stats or not stats.isup or any(marker in normalized_name for marker in virtual_markers):
                        continue
                    for item in addresses:
                        if item.family != socket.AF_INET or not item.address or not item.netmask:
                            continue
                        try:
                            interface = ipaddress.ip_interface(f"{item.address}/{item.netmask}")
                        except ValueError:
                            continue
                        if interface.ip.is_private and not interface.ip.is_loopback:
                            raw_networks.append(str(interface.network))
            except Exception:
                logger.warning("burner.agent_discovery.interface_enumeration_failed", exc_info=True)
        if not raw_networks:
            # Keep a small fallback for constrained runtimes without psutil.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect(("8.8.8.8", 80))
                    raw_networks.append(f"{sock.getsockname()[0]}/24")
            except Exception:
                return []

    networks: list[ipaddress.IPv4Network] = []
    seen_networks: set[str] = set()
    for raw_network in raw_networks:
        try:
            network = ipaddress.ip_network(raw_network, strict=False)
        except ValueError:
            logger.warning("burner.agent_discovery.invalid_cidr | %s", raw_network)
            continue
        network_key = str(network)
        if (
            isinstance(network, ipaddress.IPv4Network)
            and network.num_addresses <= 1024
            and network_key not in seen_networks
        ):
            networks.append(network)
            seen_networks.add(network_key)
    return networks


def _probe_pcids_agent_url(url: str, timeout_seconds: float = 3.0) -> Optional[str]:
    import urllib.request

    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return None
        with socket.create_connection((parsed.hostname, parsed.port or 8000), timeout=0.3):
            pass
        with urllib.request.urlopen(f"{url}/health", timeout=timeout_seconds) as response:
            if getattr(response, "status", 200) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
        if payload.get("status") == "ok" and payload.get("version"):
            return url
    except Exception:
        return None
    return None


def _discover_lan_agent_urls(cache_ttl_seconds: float = 15.0) -> list[str]:
    now = time.monotonic()
    with _LAN_AGENT_CACHE_LOCK:
        if now < float(_LAN_AGENT_CACHE.get("expires_at") or 0.0):
            return list(_LAN_AGENT_CACHE.get("urls") or [])

    port = _get_agent_discovery_port(_load_agent_discovery_config())
    local_addresses = _get_service_node_addresses()
    targets: list[str] = []
    for network in _get_lan_agent_scan_networks():
        for address in network.hosts():
            address_text = str(address)
            if address_text in local_addresses:
                continue
            targets.append(f"http://{address_text}:{port}")

    discovered: list[str] = []
    with ThreadPoolExecutor(max_workers=min(64, max(1, len(targets)))) as executor:
        futures = {executor.submit(_probe_pcids_agent_url, target): target for target in targets}
        for future in as_completed(futures):
            result = future.result()
            if result:
                discovered.append(result)

    discovered.sort()
    with _LAN_AGENT_CACHE_LOCK:
        _LAN_AGENT_CACHE["urls"] = discovered
        _LAN_AGENT_CACHE["expires_at"] = time.monotonic() + max(1.0, cache_ttl_seconds)
    return discovered


def _discover_scan_nodes(
    db: Session,
    scope: str,
    explicit_agent_url: Optional[str] = None,
    discovered_agent_urls: Optional[list[str]] = None,
) -> list[dict]:
    service_address = _get_service_node_address()
    service_node_type = "server" if _is_configured_server_node(service_address) else "local"
    nodes = [
        {
            "node_key": service_address,
            "node_label": "服务器" if service_node_type == "server" else "本地",
            "node_type": service_node_type,
            "agent_url": None,
            "host_address": service_address,
        }
    ]
    if scope != "all":
        return nodes

    agent_map: dict[str, dict] = {}
    normalized_explicit_url = _normalize_agent_url(explicit_agent_url)
    if normalized_explicit_url:
        explicit_host = urlparse(normalized_explicit_url).hostname or ""
        agent_map[normalized_explicit_url] = {
            "node_key": normalized_explicit_url,
            "node_label": _derive_node_label(normalized_explicit_url, host_address=explicit_host),
            "node_type": "agent",
            "agent_url": normalized_explicit_url,
            "host_name": None,
            "host_address": explicit_host,
        }
    for discovered_url in (
        _discover_lan_agent_urls() if discovered_agent_urls is None else discovered_agent_urls
    ):
        if discovered_url in agent_map:
            continue
        discovered_host = urlparse(discovered_url).hostname or ""
        agent_map[discovered_url] = {
            "node_key": discovered_url,
            "node_label": _derive_node_label(discovered_url, host_address=discovered_host),
            "node_type": "agent",
            "agent_url": discovered_url,
            "host_name": None,
            "host_address": discovered_host,
        }
    for burner in db.query(Burner).filter(Burner.agent_url.isnot(None)).all():
        agent_url = str(getattr(burner, "agent_url", None) or "").strip().rstrip("/")
        if not agent_url or agent_url in agent_map:
            continue
        agent_map[agent_url] = {
            "node_key": agent_url,
            "node_label": _derive_node_label(
                agent_url,
                host_name=getattr(burner, "host_name", None),
                host_address=getattr(burner, "host_address", None),
            ),
            "node_type": "agent",
            "agent_url": agent_url,
            "host_name": getattr(burner, "host_name", None),
            "host_address": getattr(burner, "host_address", None),
        }
    nodes.extend(agent_map.values())
    return nodes


def _scan_discovery_node(node: dict) -> list[dict]:
    """Run one potentially slow discovery probe without touching the request DB session."""
    if node["node_type"] in {"local", "server"}:
        return _discover_local_candidates(refresh_hardware=True)

    remote_res = _remote_discover_devices(str(node["agent_url"]))
    raw_candidates = remote_res.get("data", {}).get("items") or []
    candidates: list[dict] = []
    for item in raw_candidates:
        candidate = dict(item)
        candidate["node_key"] = node["node_key"]
        candidate["node_type"] = node["node_type"]
        candidate["node_label"] = node["node_label"]
        candidate["agent_url"] = node["agent_url"]
        candidate["host_name"] = node.get("host_name")
        candidate["host_address"] = node.get("host_address")
        candidates.append(candidate)
    return candidates


async def _scan_discovery_nodes_async(
    nodes: list[dict],
    scope: str,
    normalized_explicit_agent_url: Optional[str],
    trace_id: str,
) -> tuple[list[list[dict]], set[str]]:
    """Offload blocking PnP, HTTP, and subprocess discovery work from the event loop."""
    node_candidates: list[list[dict]] = [[] for _node in nodes]
    failed_node_keys: set[str] = set()

    async def scan_one(index: int, node: dict) -> tuple[int, list[dict], Optional[Exception]]:
        try:
            return index, await asyncio.to_thread(_scan_discovery_node, node), None
        except Exception as exc:
            _debug_report_burner_scan_crash(
                "A",
                "burners.py:_scan_discovery_nodes_async:node_error",
                "burner discovery node failed",
                {"scope": scope, "agent_url": node.get("agent_url"), "node_label": node.get("node_label"), "error": str(exc)},
                trace_id,
            )
            logger.warning(
                "burner.discovery.agent_failed | %s",
                json.dumps({"agent_url": node.get("agent_url"), "error": str(exc)}, ensure_ascii=False),
            )
            return index, [], exc

    results = await asyncio.gather(*(scan_one(index, node) for index, node in enumerate(nodes)))
    explicit_error: Optional[Exception] = None
    for index, candidates, error in results:
        node_candidates[index] = candidates
        if error is None:
            continue
        node = nodes[index]
        failed_node_keys.add(str(node.get("node_key") or ""))
        if normalized_explicit_agent_url and str(node.get("agent_url") or "").rstrip("/") == normalized_explicit_agent_url:
            explicit_error = error
    if explicit_error is not None:
        raise ValueError(
            f"无法连接下位机 Agent（{normalized_explicit_agent_url}），请确认下位机程序正在运行、IP/端口正确且防火墙已放行"
        ) from explicit_error
    return node_candidates, failed_node_keys


def _find_matching_candidate(burner: Burner, candidates: list[dict]) -> Optional[dict]:
    for candidate in candidates:
        if _candidate_matches_registered_identity(burner, candidate, require_same_node=True):
            return candidate
    return None


def _find_discovery_binding_candidate(burner: Burner, candidates: list[dict]) -> Optional[dict]:
    burner_type = str(getattr(burner, "type", None) or "").strip()
    burner_sn = _normalize_binding_sn(getattr(burner, "sn", None))
    burner_port_values = _port_match_values(getattr(burner, "port", None)) | _usb_binding_direct_port_values(_burner_usb_binding(burner))
    best_match: Optional[dict] = None
    best_score = -1

    for candidate in candidates:
        candidate_sn = _normalize_binding_sn(candidate.get("sn"))
        candidate_port_values = _candidate_direct_port_values(candidate)
        same_known_type = _candidate_has_same_known_type(burner_type, candidate)
        exact_scanned_identity = _usb_bindings_match_device_identity(
            _burner_usb_binding(burner),
            candidate.get("usb_binding"),
        )
        score = 0
        if burner_sn:
            if not candidate_sn or candidate_sn != burner_sn:
                continue
            if not same_known_type and not candidate.get("probe_only"):
                continue
            same_node = _is_same_burner_candidate_node(burner, candidate)
            score += 40
            if same_known_type:
                score += 10
            if same_node:
                score += 5
        else:
            # Without an immutable serial there is no safe way to infer that a
            # device moved. Same-type/same-port may only confirm the existing
            # binding; an explicit user rebind is required for a new port.
            if not same_known_type and not (candidate.get("probe_only") and exact_scanned_identity):
                continue
            if not (burner_port_values and candidate_port_values & burner_port_values):
                continue
            if not _is_same_burner_candidate_node(burner, candidate):
                continue
            score += 30
        if score > best_score:
            best_score = score
            best_match = candidate
    return best_match


def _has_recorded_binding(burner: Burner) -> bool:
    return bool(
        _normalize_binding_value(getattr(burner, "sn", None))
        or _normalize_binding_port(getattr(burner, "port", None))
    )


def _build_discovery_payload(
    db: Session,
    scope: str,
    editing_burner_id: Optional[int] = None,
    explicit_agent_url: Optional[str] = None,
    nodes: Optional[list[dict]] = None,
    scanned_node_candidates: Optional[list[list[dict]]] = None,
    failed_node_keys: Optional[set[str]] = None,
) -> dict:
    trace_id = f"burner-discovery-build-{scope}-{int(time.time() * 1000)}"
    normalized_explicit_agent_url = _normalize_agent_url(explicit_agent_url)
    nodes = nodes or _discover_scan_nodes(db, scope, explicit_agent_url=normalized_explicit_agent_url)
    # #region debug-point SK:build-discovery-start
    _debug_report_burner_scan_crash(
        "A",
        "burners.py:_build_discovery_payload:start",
        "burner discovery payload build start",
        {
            "scope": scope,
            "node_count": len(nodes),
            "node_types": [str(item.get("node_type") or "") for item in nodes],
        },
        trace_id,
    )
    # #endregion
    candidates: list[dict] = []
    seen_candidate_ids: set[str] = set()
    failed_node_keys = set(failed_node_keys or ())

    for index, node in enumerate(nodes):
        if scanned_node_candidates is not None:
            node_candidates = scanned_node_candidates[index]
        elif node["node_type"] in {"local", "server"}:
            node_candidates = _discover_local_candidates()
        else:
            try:
                node_candidates = _scan_discovery_node(node)
            except Exception as exc:
                failed_node_keys.add(str(node.get("node_key") or ""))
                # #region debug-point SL:build-discovery-remote-error
                _debug_report_burner_scan_crash(
                    "A",
                    "burners.py:_build_discovery_payload:remote_error",
                    "burner discovery remote node failed",
                    {
                        "scope": scope,
                        "agent_url": node.get("agent_url"),
                        "node_label": node.get("node_label"),
                        "error": str(exc),
                    },
                    trace_id,
                )
                # #endregion
                logger.warning(
                    "burner.discovery.agent_failed | %s",
                    json.dumps({"agent_url": node["agent_url"], "error": str(exc)}, ensure_ascii=False),
                )
                if normalized_explicit_agent_url and str(node.get("agent_url") or "").rstrip("/") == normalized_explicit_agent_url:
                    raise ValueError(
                        f"无法连接下位机 Agent（{normalized_explicit_agent_url}），请确认下位机程序正在运行、IP/端口正确且防火墙已放行"
                    ) from exc
                node_candidates = []

        for candidate in node_candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            if not candidate_id or candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate_id)
            candidates.append(candidate)

    burners = db.query(Burner).all()
    candidates = _resolve_ambiguous_candidate_types(candidates, burners)
    matched_candidate_ids: set[str] = set()
    changed_bindings: list[dict] = []

    for burner in burners:
        matched = _find_discovery_binding_candidate(burner, candidates)
        if not matched:
            continue
        matched_candidate_ids.add(matched["candidate_id"])
        if not _has_recorded_binding(burner):
            continue
        old_node_key = _burner_node_key(burner)
        old_sn = str(getattr(burner, "sn", None) or "").strip()
        old_port = str(getattr(burner, "port", None) or "").strip()
        new_sn = str(matched.get("sn") or "").strip()
        new_port = str(matched.get("port") or "").strip()
        sn_unchanged = _normalize_binding_sn(old_sn) == _normalize_binding_sn(new_sn)
        port_unchanged = _normalize_binding_port(old_port) == _normalize_binding_port(new_port)
        if int(getattr(burner, "strategy", 1) or 1) == 2:
            sn_unchanged = True
        if _is_same_burner_candidate_node(burner, matched) and port_unchanged and sn_unchanged:
            continue
        changed_bindings.append(
            {
                "burner_id": burner.id,
                "burner_name": burner.name,
                "burner_type": burner.type,
                "strategy": burner.strategy,
                # The discovery dialog is independent of the current paginated
                # device list.  Preserve the full record configuration so a
                # binding update for an off-page burner only replaces
                # usb_binding instead of erasing its capability settings.
                "burner_config_json": getattr(burner, "config_json", None),
                "original_binding": {
                    "sn": getattr(burner, "sn", None),
                    "port": getattr(burner, "port", None),
                    "node_key": old_node_key,
                    "node_label": _derive_node_label(
                        getattr(burner, "agent_url", None),
                        host_name=getattr(burner, "host_name", None),
                        host_address=getattr(burner, "host_address", None),
                    ),
                },
                "current_binding": {
                    "sn": matched.get("sn"),
                    "port": matched.get("port"),
                    "usb_binding": matched.get("usb_binding") or {},
                    "node_key": matched.get("node_key"),
                    "node_label": matched.get("node_label"),
                    "node_type": matched.get("node_type"),
                    "agent_url": matched.get("agent_url"),
                    "host_name": matched.get("host_name"),
                    "host_address": matched.get("host_address"),
                },
                "scan_device": matched,
            }
        )

    registered_keys = _registered_binding_keys(burners)
    unregistered_devices = [
        candidate
        for candidate in candidates
        if not candidate.get("probe_only")
        if candidate["candidate_id"] not in matched_candidate_ids
        and not _candidate_matches_registered_binding(candidate, registered_keys)
    ]
    selectable_keys = _registered_binding_keys(burners, exclude_id=editing_burner_id)
    selectable_devices = [
        candidate
        for candidate in candidates
        if not candidate.get("probe_only")
        if not _candidate_matches_registered_binding(candidate, selectable_keys)
    ]
    probe_only_devices = [
        candidate
        for candidate in candidates
        if candidate.get("probe_only")
        if candidate["candidate_id"] not in matched_candidate_ids
    ]
    # #region debug-point SM:build-discovery-done
    _debug_report_burner_scan_crash(
        "A",
        "burners.py:_build_discovery_payload:done",
        "burner discovery payload build done",
        {
            "scope": scope,
            "candidate_count": len(candidates),
            "changed_binding_count": len(changed_bindings),
            "unregistered_count": len(unregistered_devices),
            "selectable_count": len(selectable_devices),
            "probe_only_count": len(probe_only_devices),
            "failed_node_count": len([item for item in failed_node_keys if item]),
        },
        trace_id,
    )
    # #endregion
    return {
        "scope": scope,
        "nodes": nodes,
        "changed_bindings": changed_bindings,
        "scanned_devices": candidates,
        "probe_only_devices": probe_only_devices,
        "selectable_devices": selectable_devices,
        "unregistered_devices": unregistered_devices,
        "total_scanned": len(candidates),
        "total_probe_only": len(probe_only_devices),
        "failed_node_keys": [item for item in failed_node_keys if item],
    }


def _resolve_discovery_status_updates(
    burners: list[Burner],
    candidates: list[dict],
    scope: str,
    occupied_burner_ids: set[int],
    failed_node_keys: Optional[set[str]] = None,
) -> list[dict]:
    failed_node_keys = failed_node_keys or set()
    updates: list[dict] = []
    for burner in burners:
        burner_node_key = _burner_node_key(burner)
        if not _is_burner_enabled(burner):
            status = 3
        elif burner.id in occupied_burner_ids:
            status = 2
        elif scope == "local" and str(getattr(burner, "agent_url", None) or "").strip():
            # A local-only scan must not rewrite a remote node based on missing
            # candidates, but it also must not preserve a stale persisted
            # busy/disabled value after the source task/state is gone.
            status = _compute_burner_cached_status(burner, occupied_burner_ids)
        elif burner_node_key and burner_node_key in failed_node_keys:
            status = 1
        else:
            status = 0 if _find_matching_candidate(burner, candidates) else 1
        updates.append({"id": burner.id, "status": status})
    return updates


def _refresh_registered_burner_statuses(
    db: Session,
    scope: str,
    candidates: list[dict],
    failed_node_keys: Optional[list[str]] = None,
) -> list[dict]:
    trace_id = f"burner-status-refresh-{scope}-{int(time.time() * 1000)}"
    burners = db.query(Burner).all()
    occupied_burner_ids = {
        int(item[0])
        for item in (
            db.query(BurningTask.burner_id)
            .filter(
                BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
                BurningTask.burner_id.isnot(None),
            )
            .all()
        )
        if item[0] is not None
    }
    failed_nodes = {
        str(item or "").strip()
        for item in (failed_node_keys or [])
        if str(item or "").strip()
    }
    # #region debug-point SN:refresh-status-start
    _debug_report_burner_scan_crash(
        "C",
        "burners.py:_refresh_registered_burner_statuses:start",
        "refresh registered burner statuses start",
        {
            "scope": scope,
            "burner_count": len(burners),
            "candidate_count": len(candidates),
            "failed_node_count": len(failed_nodes),
            "occupied_count": len(occupied_burner_ids),
        },
        trace_id,
    )
    # #endregion
    updates = _resolve_discovery_status_updates(
        burners,
        candidates,
        scope,
        occupied_burner_ids,
        failed_node_keys=failed_nodes,
    )
    # #region debug-point SO:refresh-status-done
    _debug_report_burner_scan_crash(
        "C",
        "burners.py:_refresh_registered_burner_statuses:done",
        "refresh registered burner statuses done",
        {
            "scope": scope,
            "update_count": len(updates),
            "sample_updates": updates[:10],
        },
        trace_id,
    )
    # #endregion
    status_by_id = {item["id"]: item["status"] for item in updates}
    changed = False
    for burner in burners:
        next_status = status_by_id.get(burner.id)
        stored_status = getattr(burner, "status", 1)
        current_status = int(stored_status if stored_status is not None else 1)
        # Busy is derived from active tasks and must not be persisted; doing so
        # leaves a burner stuck as busy after its task finishes.
        if next_status is not None and next_status != 2 and current_status != next_status:
            burner.status = next_status
            changed = True
    if changed:
        db.commit()
    return updates


def _is_burner_enabled(burner: Burner) -> bool:
    enabled_value = getattr(burner, "is_enabled", None)
    return enabled_value is None or bool(enabled_value)


def _compute_burner_runtime_status(burner: Burner, occupied_burner_ids: set[int], usb_devices: Optional[list[dict]] = None) -> int:
    trace_id = f"burner-runtime-{getattr(burner, 'id', 'na')}-{int(time.time() * 1000)}"
    if not _is_burner_enabled(burner):
        return 3
    if burner.id in occupied_burner_ids:
        return 2
    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    self_agent = _is_agent_url_for_service_node(agent_url)
    if agent_url and not self_agent:
        try:
            res = _remote_scan_burner(
                agent_url,
                burner.type,
                burner.port if int(getattr(burner, "strategy", 1) or 1) == 2 else burner.location,
                burner.strategy,
                sn=burner.sn,
                port=burner.port,
                usb_binding=_burner_usb_binding(burner),
            )
            return 0 if bool(res.get("data", {}).get("online")) else 1
        except Exception as exc:
            # #region debug-point H:runtime-status-remote-error
            _debug_report_device_refresh_crash(
                "H",
                "burners.py:_compute_burner_runtime_status:remote",
                "burner remote runtime scan failed",
                {
                    "burner_id": getattr(burner, "id", None),
                    "burner_name": getattr(burner, "name", None),
                    "agent_url": getattr(burner, "agent_url", None),
                    "error": str(exc),
                },
                trace_id,
            )
            # #endregion
            return 1
    if not self_agent and not _is_burner_owned_by_service_node(burner):
        return 1
    try:
        burner_strategy = int(getattr(burner, "strategy", 1) or 1)
        scan_anchor = burner.port if burner_strategy == 2 else burner.location
        scanned = _build_scan_result(
            burner.type,
            scan_anchor,
            burner_strategy,
            burner,
            allow_fallback=False,
            usb_devices=usb_devices,
        )
    except Exception as exc:
        # #region debug-point I:runtime-status-local-error
        _debug_report_device_refresh_crash(
            "I",
            "burners.py:_compute_burner_runtime_status:local",
            "burner local runtime scan crashed",
            {
                "burner_id": getattr(burner, "id", None),
                "burner_name": getattr(burner, "name", None),
                "burner_type": getattr(burner, "type", None),
                "location": getattr(burner, "location", None),
                "strategy": getattr(burner, "strategy", None),
                "error": str(exc),
            },
            trace_id,
        )
        # #endregion
        raise
    return 0 if scanned and scanned.get("online") else 1


def _safe_compute_burner_runtime_status(
    burner: Burner,
    occupied_burner_ids: set[int],
    usb_devices: Optional[list[dict]] = None,
) -> int:
    try:
        return _compute_burner_runtime_status(burner, occupied_burner_ids, usb_devices)
    except Exception as exc:
        logger.warning(
            "burner.runtime_status.failed | %s",
            json.dumps(
                {"burner_id": getattr(burner, "id", None), "error": str(exc)},
                ensure_ascii=False,
            ),
        )
        return 1


async def _compute_burner_runtime_statuses(
    burners: list[Burner],
    occupied_burner_ids: set[int],
) -> tuple[dict[int, int], int]:
    """Read a fresh runtime snapshot while serializing expensive PnP probes.

    Device presence is mutable system state.  The lock prevents overlapping
    hardware enumeration, but completed results must never be reused by a
    later request.
    """
    async with _RUNTIME_STATUS_REFRESH_LOCK:
        needs_local_probe = any(
            _is_burner_enabled(burner)
            and burner.id not in occupied_burner_ids
            and (
                _is_agent_url_for_service_node(getattr(burner, "agent_url", None))
                or (
                    not str(getattr(burner, "agent_url", None) or "").strip()
                    and _is_burner_owned_by_service_node(burner)
                )
            )
            for burner in burners
        )
        usb_devices: Optional[list[dict]] = None
        if needs_local_probe:
            await asyncio.to_thread(_refresh_windows_pnp_state)
            usb_devices = await asyncio.to_thread(_probe_usb_devices, force_refresh=True)

        runtime_values = await asyncio.gather(
            *(
                asyncio.to_thread(
                    _safe_compute_burner_runtime_status,
                    burner,
                    occupied_burner_ids,
                    usb_devices,
                )
                for burner in burners
            )
        )
        values = {
            burner.id: runtime_status
            for burner, runtime_status in zip(burners, runtime_values)
        }
        usb_device_count = len(usb_devices or [])
        return values, usb_device_count


def _compute_burner_cached_status(burner: Burner, occupied_burner_ids: set[int]) -> int:
    if not _is_burner_enabled(burner):
        return 3
    if burner.id in occupied_burner_ids:
        return 2
    try:
        stored_status = getattr(burner, "status", 1)
        status_value = int(stored_status if stored_status is not None else 1)
    except Exception:
        status_value = 1
    # Busy and disabled are derived states. Once their source condition is
    # gone, a persisted 2/3 must not keep the device stuck there.
    if status_value == 2:
        status_value = 0
    elif status_value == 3:
        status_value = 1
    if status_value == 0 and not (
        _normalize_binding_value(getattr(burner, "sn", None))
        or _port_match_values(getattr(burner, "port", None), getattr(burner, "location", None))
    ):
        return 1
    return status_value if status_value in (0, 1, 2, 3) else 1


async def _refresh_and_persist_single_burner_status(db: Session, burner: Burner) -> int:
    occupied = (
        db.query(BurningTask.id)
        .filter(
            BurningTask.burner_id == burner.id,
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
        )
        .first()
        is not None
    )
    occupied_ids = {burner.id} if occupied else set()
    usb_devices: Optional[list[dict]] = None
    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    if not agent_url or _is_agent_url_for_service_node(agent_url):
        await asyncio.to_thread(_refresh_windows_pnp_state)
        usb_devices = await asyncio.to_thread(_probe_usb_devices, force_refresh=True)
    try:
        runtime_status = await asyncio.to_thread(
            _safe_compute_burner_runtime_status,
            burner,
            occupied_ids,
            usb_devices,
        )
    except Exception as exc:
        logger.warning(
            "burner.saved_status_refresh.failed | %s",
            json.dumps(
                {"burner_id": getattr(burner, "id", None), "error": str(exc)},
                ensure_ascii=False,
            ),
        )
        runtime_status = 1
    if runtime_status != 2 and int(getattr(burner, "status", 1) or 0) != runtime_status:
        burner.status = runtime_status
        db.commit()
        db.refresh(burner)
    return runtime_status


def _infer_device_category(burner: Burner) -> str:
    config = {}
    try:
        config = json.loads(getattr(burner, "config_json", None) or "{}") or {}
    except Exception:
        config = {}
    if config.get("device_category") == "sd_reader":
        return "sd_reader"
    burner_type = str(getattr(burner, "type", None) or "").strip()
    burner_name = str(getattr(burner, "name", None) or "").strip()
    if burner_type == "SD卡文件写入" or "SD" in burner_name:
        return "sd_reader"
    return "burner"


def _infer_node_scope(burner: Burner) -> str:
    host_type = str(getattr(burner, "host_type", None) or "").strip().lower()
    agent_url = str(getattr(burner, "agent_url", None) or "").strip()
    host_address = str(getattr(burner, "host_address", None) or "").strip()
    owner_address = host_address or (
        _get_service_node_address()
        if not agent_url and host_type in {"local", "server"}
        else ""
    )
    if _is_configured_server_node(owner_address):
        return "server"
    if host_type == "agent":
        return "agent"
    if agent_url:
        return "agent"
    return "local"


def _normalize_burner_model_key(value: Optional[str]) -> str:
    raw_value = str(value or "").strip()
    canonical_value = LEGACY_BURNER_NAME_MAP.get(raw_value, raw_value)
    return (
        canonical_value
        .replace("Ⅱ", "II")
        .replace("-", "_")
        .replace(" ", "_")
        .upper()
    )


def _sort_by_preset_order(values: list[str], order: list[str]) -> list[str]:
    unique_values = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in unique_values and item in order:
            unique_values.append(item)
    return sorted(unique_values, key=lambda item: order.index(item))


def _burner_requires_physical_port(device_model: Optional[str]) -> bool:
    return _normalize_burner_model_key(device_model) == "XDS510PLUS"


def _backfill_al321_binding_from_usb_binding(normalized: dict, config: dict, resolved_type: str) -> None:
    if _normalize_burner_model_key(resolved_type) != "AL321":
        return
    usb_binding = _normalize_usb_binding(config.get("usb_binding"))
    if not usb_binding:
        return
    pnp_device_id = str(usb_binding.get("pnp_device_id") or "").strip()
    current_location = str(normalized.get("location") or "").strip()
    if (not current_location or current_location == "-") and pnp_device_id:
        normalized["location"] = pnp_device_id
    if int(normalized.get("strategy") or 1) != 1:
        return
    if normalized.get("sn"):
        return
    serial = _extract_usb_instance_serial(pnp_device_id, "0403", "6014")
    if serial:
        normalized["sn"] = serial


def _normalize_burner_payload(payload: dict, existing: Optional[Burner] = None) -> dict:
    normalized = dict(payload)
    resolved_type = str(normalized.get("type") or getattr(existing, "type", None) or "").strip()
    raw_config = normalized.get("config_json")
    if raw_config is None and existing is not None:
        raw_config = getattr(existing, "config_json", None)
    try:
        config = json.loads(raw_config or "{}") or {}
    except Exception:
        config = {}

    device_category = str(config.get("device_category") or "").strip() or ("sd_reader" if resolved_type == "SD卡文件写入" else "burner")
    if device_category == "sd_reader":
        config["device_category"] = "sd_reader"
        config["device_model"] = ""
        config["supported_interfaces"] = []
        config["supported_chips"] = []
        normalized["type"] = "SD卡文件写入"
        normalized["strategy"] = 1
        normalized["sn"] = ""
        normalized["port"] = ""
    else:
        capability = BURNER_CAPABILITY_MAP.get(_normalize_burner_model_key(resolved_type)) or {}
        config["device_category"] = "burner"
        config["device_model"] = resolved_type
        config["supported_interfaces"] = _sort_by_preset_order(list(capability.get("supported_interfaces") or []), INTERFACE_ORDER)
        config["supported_chips"] = _sort_by_preset_order(list(capability.get("supported_chips") or []), CHIP_ORDER)
        config["supported_card_types"] = []
        config["mount_path"] = ""
        usb_binding = _normalize_usb_binding(config.get("usb_binding"))
        explicit_port_values = _port_match_values(normalized.get("port"))
        if (
            usb_binding
            and explicit_port_values
            and not (explicit_port_values & _usb_binding_port_values(usb_binding))
        ):
            # A manually changed primary port must not keep identifying a
            # different USB device through stale form/config metadata.
            usb_binding = {}
        if usb_binding:
            config["usb_binding"] = usb_binding
        else:
            config.pop("usb_binding", None)
        if (
            existing is not None
            and "type" in normalized
            and _normalize_device_token(getattr(existing, "type", None))
            != _normalize_device_token(normalized.get("type"))
        ):
            config.pop("usb_binding", None)
        if _burner_requires_physical_port(resolved_type):
            normalized["strategy"] = 2
        _backfill_al321_binding_from_usb_binding(normalized, config, resolved_type)
        if int(normalized.get("strategy") or getattr(existing, "strategy", 1) or 1) == 2:
            normalized["sn"] = ""

    normalized["config_json"] = json.dumps(config, ensure_ascii=False)
    return normalized


def _validate_burner_payload_required(payload: dict) -> None:
    if not str(payload.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="设备名称不能为空")
    if not str(payload.get("type") or "").strip():
        raise HTTPException(status_code=422, detail="设备型号不能为空")
    if payload.get("is_enabled") is None:
        raise HTTPException(status_code=422, detail="启用状态不能为空")
    if payload.get("strategy") is None:
        raise HTTPException(status_code=422, detail="识别策略不能为空")
    try:
        config = json.loads(payload.get("config_json") or "{}") or {}
    except Exception:
        config = {}
    is_enabled = payload.get("is_enabled", True) not in {False, 0}
    if config.get("device_category") == "sd_reader":
        if not is_enabled:
            return
        if not str(config.get("mount_path") or "").strip():
            raise HTTPException(status_code=422, detail="SD 读卡器必须填写挂载路径")
        if not list(config.get("supported_card_types") or []):
            raise HTTPException(status_code=422, detail="SD 读卡器必须选择支持卡类型")
        return

    try:
        strategy = int(payload.get("strategy") or 1)
    except (TypeError, ValueError):
        strategy = 0
    if strategy not in {1, 2}:
        raise HTTPException(status_code=422, detail="识别策略只能选择按 SN 或按物理端口")
    if not is_enabled:
        return
    if strategy == 1 and _normalize_binding_sn(payload.get("sn")) in {"", "-"}:
        raise HTTPException(status_code=422, detail="按 SN 识别时必须填写 SN 标识码")
    if strategy == 2 and _normalize_binding_port(payload.get("port")) in {"", "-"}:
        raise HTTPException(status_code=422, detail="按物理端口识别时必须填写物理端口")


def _clear_conflicting_burner_port(conflict_burner: Burner, modified_by: Optional[str] = None) -> None:
    original_port_values = _port_match_values(getattr(conflict_burner, "port", None))
    original_direct_port_values = original_port_values | _usb_binding_direct_port_values(
        _burner_usb_binding(conflict_burner)
    )
    conflict_burner.port = ""
    if int(getattr(conflict_burner, "strategy", 1) or 1) == 2:
        conflict_burner.sn = ""
    if _port_match_values(getattr(conflict_burner, "location", None)) & original_direct_port_values:
        conflict_burner.location = ""
    try:
        config = json.loads(getattr(conflict_burner, "config_json", None) or "{}") or {}
    except Exception:
        config = {}
    config.pop("usb_binding", None)
    conflict_burner.config_json = json.dumps(config, ensure_ascii=False)
    conflict_burner.status = 1 if _is_burner_enabled(conflict_burner) else 3
    if modified_by:
        conflict_burner.modified_by = modified_by


def _ensure_burner_not_in_use(db: Session, burner: Burner, action: str) -> None:
    active_task = (
        db.query(BurningTask.id)
        .filter(
            BurningTask.burner_id == burner.id,
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
        )
        .first()
    )
    if active_task is not None:
        raise HTTPException(
            status_code=409,
            detail=f"设备「{getattr(burner, 'name', None) or burner.id}」正在执行任务，不能{action}，请等待任务结束后重试",
        )


def _validate_burner_binding_unique(
    db: Session,
    payload: dict,
    existing: Optional[Burner] = None,
    force_rebind_port: bool = False,
) -> Optional[Burner]:
    try:
        strategy = int(payload.get("strategy") or getattr(existing, "strategy", 1) or 1)
    except Exception:
        strategy = 1
    sn = _normalize_binding_sn(payload.get("sn"))
    port = _normalize_binding_value(payload.get("port"))
    normalized_port = _normalize_binding_port(payload.get("port"))
    try:
        payload_config = json.loads(payload.get("config_json") or "{}") or {}
    except Exception:
        payload_config = {}
    payload_ports = _port_match_values(payload.get("port")) | _usb_binding_port_values(payload_config.get("usb_binding"))
    payload_node_key = str(payload.get("agent_url") or payload.get("host_address") or "local").strip()
    if strategy == 1 and not sn:
        return None
    if strategy == 2 and not (port or normalized_port):
        return None

    def _build_conflict_burner_label(conflict_burner: Burner) -> str:
        burner_name = str(getattr(conflict_burner, "name", None) or "").strip()
        burner_type = str(getattr(conflict_burner, "type", None) or "").strip()
        node_name = str(getattr(conflict_burner, "host_name", None) or "").strip()

        label = burner_name or burner_type or f"ID {getattr(conflict_burner, 'id', '-')}"
        if node_name:
            return f"{label}（{node_name}）"
        return label

    exclude_id = getattr(existing, "id", None)
    for burner in db.query(Burner).all():
        if exclude_id is not None and burner.id == exclude_id:
            continue
        # A serial identifies one physical probe across all nodes. Moving that
        # probe must update its existing record instead of creating a duplicate.
        if strategy == 1 and sn and _normalize_binding_sn(getattr(burner, "sn", None)) == sn:
            conflict_label = _build_conflict_burner_label(burner)
            raise HTTPException(
                status_code=409,
                detail=f"当前 SN 标识码已被设备「{conflict_label}」登记，请编辑已有设备或选择其他设备",
            )
        if not _is_same_node_address(payload_node_key, _burner_node_key(burner)):
            continue
        if strategy == 2:
            existing_ports = _port_match_values(getattr(burner, "port", None)) | _usb_binding_port_values(_burner_usb_binding(burner))
            if payload_ports & existing_ports:
                if force_rebind_port:
                    return burner
                conflict_label = _build_conflict_burner_label(burner)
                raise HTTPException(
                    status_code=409,
                    detail=f"当前物理位置已被设备「{conflict_label}」绑定",
                    headers={"X-PCIDS-Error-Code": "BURNER_PORT_BOUND"},
                )
    return None


def _parse_device_categories_param(device_categories: Optional[str]) -> set[str]:
    raw_value = str(device_categories or "").strip()
    if not raw_value:
        return set()

    parsed_items = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not parsed_items:
        return set()

    invalid_items = [item for item in parsed_items if item not in VALID_DEVICE_CATEGORIES]
    if invalid_items:
        raise HTTPException(
            status_code=422,
            detail=f"设备类型筛选参数非法：{', '.join(invalid_items)}",
        )
    return set(parsed_items)


def burner_to_dict(b, status_override: Optional[int] = None, request: Optional[Request] = None):
    status_value = b.status if status_override is None else status_override
    node_display = _resolve_node_display(b, request)
    node_scope = _infer_node_scope(b)
    return {
        "id": b.id,
        "name": b.name,
        "type": b.type,
        "sn": b.sn,
        "port": b.port,
        "location": b.location,
        "host_type": node_scope,
        "host_name": getattr(b, "host_name", None),
        "host_address": getattr(b, "host_address", None),
        "agent_url": getattr(b, "agent_url", None),
        "node_display_label": node_display["label"],
        "node_owner_address": node_display["owner_address"],
        "node_current_address": node_display["current_address"],
        "node_is_local": node_display["is_local"],
        "strategy": b.strategy,
        "is_enabled": b.is_enabled,
        "status": status_value,
        "description": b.description,
        "config_json": getattr(b, "config_json", None),
        "modified_by": b.modified_by,
        "created_at": database_time_to_local(b.created_at),
        "updated_at": database_time_to_local(b.updated_at),
    }


@router.get("", response_model=PaginatedResponse)
async def get_burners(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=1000),
    keyword: Optional[str] = None,
    status: Optional[int] = None,
    burner_type: Optional[str] = None,
    device_categories: Optional[str] = None,
    node_scope: Optional[str] = None,
    ids: Optional[str] = None,
    include_runtime_status: bool = Query(True),
    sort_field: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:view")),
):
    """获取烧录器列表"""
    ensure_schema()
    trace_id = f"burner-list-{int(time.time() * 1000)}"
    # #region debug-point J:list-enter
    _debug_report_device_refresh_crash(
        "J",
        "burners.py:get_burners:enter",
        "burner list enter",
        {
            "page": page,
            "page_size": page_size,
            "keyword": keyword,
            "status": status,
            "device_categories": device_categories,
            "node_scope": node_scope,
            "ids": ids,
            "include_runtime_status": include_runtime_status,
        },
        trace_id,
    )
    # #endregion
    from sqlalchemy import desc, asc
    query = db.query(Burner)

    if keyword:
        query = query.filter(Burner.name.contains(keyword))
    if burner_type:
        query = query.filter(Burner.type == burner_type)
    selected_ids = {
        int(item)
        for item in re.split(r"[,，\s]+", str(ids or "").strip())
        if item.isdigit()
    }
    if selected_ids:
        query = query.filter(Burner.id.in_(selected_ids))

    if sort_field and hasattr(Burner, sort_field):
        order_func = desc if sort_order == "desc" else asc
        query = query.order_by(order_func(getattr(Burner, sort_field)))
    else:
        query = query.order_by(Burner.updated_at.desc())

    burners = query.all()
    selected_device_categories = _parse_device_categories_param(device_categories)
    if node_scope and node_scope not in {"all", "local", "server", "agent"}:
        raise HTTPException(status_code=422, detail="节点筛选参数非法")
    if status is not None and status not in {0, 1, 2, 3}:
        raise HTTPException(status_code=422, detail="设备状态筛选参数非法")
    runtime_burners = [
        burner
        for burner in burners
        if (
            not selected_device_categories
            or _infer_device_category(burner) in selected_device_categories
        )
        and (
            not node_scope
            or node_scope == "all"
            or _infer_node_scope(burner) == node_scope
        )
    ]
    occupied_burner_ids = {
        burner_id
        for (burner_id,) in db.query(BurningTask.burner_id)
        .filter(
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
            BurningTask.burner_id.isnot(None),
        )
        .all()
        if burner_id is not None
    }
    if include_runtime_status:
        # Release the read transaction before hardware/network I/O.  A single
        # probe can legitimately take seconds; retaining a pooled SQLite
        # connection for that entire period lets repeated UI refreshes starve
        # unrelated pages such as products and repositories.
        db.commit()
        runtime_status_by_id, usb_device_count = await _compute_burner_runtime_statuses(
            runtime_burners,
            occupied_burner_ids,
        )
    else:
        usb_device_count = 0
        runtime_status_by_id = {}
    # #region debug-point K:usb-probe-summary
    _debug_report_device_refresh_crash(
        "K",
        "burners.py:get_burners:usb_probe",
        "burner list usb probe summary",
        {
            "include_runtime_status": include_runtime_status,
            "usb_device_count": usb_device_count,
            "burner_count": len(runtime_burners),
        },
        trace_id,
    )
    # #endregion

    burner_rows = []
    for burner in runtime_burners:
        runtime_status = (
            runtime_status_by_id.get(burner.id, 1)
            if include_runtime_status
            else _compute_burner_cached_status(burner, occupied_burner_ids)
        )
        if status is not None and runtime_status != status:
            continue
        burner_rows.append((burner, runtime_status))

    if include_runtime_status:
        changed = False
        for burner in runtime_burners:
            runtime_status = runtime_status_by_id.get(burner.id)
            if runtime_status is None or runtime_status == 2:
                continue
            stored_status = int(getattr(burner, "status", 1) or 0)
            if stored_status != runtime_status:
                burner.status = runtime_status
                changed = True
        if changed:
            db.commit()

    if sort_field == "status":
        reverse = sort_order == "desc"
        burner_rows.sort(key=lambda item: item[1], reverse=reverse)

    total = len(burner_rows)
    page_rows = burner_rows[(page - 1) * page_size: page * page_size]
    modifier_names = sorted({
        str(getattr(b, "modified_by", None) or "").strip()
        for b, _runtime_status in page_rows
        if str(getattr(b, "modified_by", None) or "").strip()
    })
    users = (
        db.query(User)
        .filter((User.username.in_(modifier_names)) | (User.display_name.in_(modifier_names)))
        .all()
        if modifier_names
        else []
    )
    users_by_name: dict[str, User] = {}
    for user in users:
        username = str(getattr(user, "username", None) or "").strip()
        display_name = str(getattr(user, "display_name", None) or "").strip()
        if username:
            users_by_name[username] = user
        if display_name:
            users_by_name[display_name] = user

    def burner_row_to_dict(burner: Burner, runtime_status: int):
        payload = burner_to_dict(burner, runtime_status, request=request)
        modifier_name = str(getattr(burner, "modified_by", None) or "").strip()
        modifier_user = users_by_name.get(modifier_name) if modifier_name else None
        payload["modifier_user"] = {
            "id": getattr(modifier_user, "id", None),
            "username": getattr(modifier_user, "username", None),
            "display_name": getattr(modifier_user, "display_name", None),
            "avatar_url": getattr(modifier_user, "avatar_url", None),
        } if modifier_user else None
        return payload

    return {
        "code": 0,
        "message": "success",
        "data": [burner_row_to_dict(b, runtime_status) for b, runtime_status in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/discovery", response_model=Response)
async def discover_burners(
    discovery_request: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:scan")),
):
    payload = discovery_request or {}
    scope = str(payload.get("scope") or "local").strip().lower()
    trace_id = f"burner-discovery-route-{scope}-{int(time.time() * 1000)}"
    # #region debug-point SP:discovery-route-start
    _debug_report_burner_scan_crash(
        "B",
        "burners.py:discover_burners:start",
        "discover burners route start",
        {
            "scope": scope,
            "payload_keys": sorted(list(payload.keys())),
        },
        trace_id,
    )
    # #endregion
    if scope not in {"local", "all"}:
        scope = "local"
    editing_burner_id = payload.get("editing_burner_id")
    explicit_agent_url = payload.get("agent_url")
    try:
        editing_burner_id = int(editing_burner_id) if editing_burner_id is not None else None
    except (TypeError, ValueError):
        editing_burner_id = None
    ensure_schema()
    try:
        _collect_node_address_aliases.cache_clear()
        normalized_explicit_agent_url = _normalize_agent_url(explicit_agent_url)
        # LAN probing may fan out to many hosts and local PnP uses synchronous
        # PowerShell.  Keep both operations out of this worker's event loop so
        # unrelated requests (such as PUT /api/burners/{id}) stay responsive.
        discovered_agent_urls = (
            await asyncio.to_thread(_discover_lan_agent_urls)
            if scope == "all" and not normalized_explicit_agent_url
            else ([] if normalized_explicit_agent_url else None)
        )
        nodes = _discover_scan_nodes(
            db,
            scope,
            explicit_agent_url=normalized_explicit_agent_url,
            discovered_agent_urls=discovered_agent_urls,
        )
        scanned_node_candidates, failed_node_keys = await _scan_discovery_nodes_async(
            nodes,
            scope,
            normalized_explicit_agent_url,
            trace_id,
        )
        data = _build_discovery_payload(
            db,
            scope,
            editing_burner_id=editing_burner_id,
            explicit_agent_url=explicit_agent_url,
            nodes=nodes,
            scanned_node_candidates=scanned_node_candidates,
            failed_node_keys=failed_node_keys,
        )
    except ValueError as exc:
        # #region debug-point SQ:discovery-route-value-error
        _debug_report_burner_scan_crash(
            "A",
            "burners.py:discover_burners:value_error",
            "discover burners route value error",
            {
                "scope": scope,
                "error": str(exc),
            },
            trace_id,
        )
        # #endregion
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data["status_updates"] = _refresh_registered_burner_statuses(
        db,
        scope,
        list(data.get("scanned_devices") or []),
        failed_node_keys=list(data.get("failed_node_keys") or []),
    )
    # #region debug-point SR:discovery-route-done
    _debug_report_burner_scan_crash(
        "B",
        "burners.py:discover_burners:done",
        "discover burners route done",
        {
            "scope": scope,
            "total_scanned": data.get("total_scanned"),
            "status_update_count": len(list(data.get("status_updates") or [])),
        },
        trace_id,
    )
    # #endregion
    return {
        "code": 0,
        "message": "扫描完成，已更新设备真实状态",
        "data": data,
    }


@router.post("", response_model=Response)
async def create_burner(
    request: Request,
    burner_data: BurnerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:add")),
):
    """创建新烧录器"""
    ensure_schema()
    payload = burner_data.model_dump()
    force_rebind_port = bool(payload.pop("force_rebind_port", False))
    payload.pop("status", None)
    payload = _normalize_burner_payload(payload)
    payload = _ensure_burner_owner_node(payload)
    _validate_burner_payload_required(payload)
    conflict_burner = _validate_burner_binding_unique(db, payload, force_rebind_port=force_rebind_port)
    while conflict_burner is not None:
        _ensure_burner_not_in_use(db, conflict_burner, "换绑物理端口")
        _clear_conflicting_burner_port(conflict_burner, current_user.username)
        conflict_burner = _validate_burner_binding_unique(
            db,
            payload,
            force_rebind_port=force_rebind_port,
        )
    payload["modified_by"] = current_user.username
    payload["status"] = 1 if payload.get("is_enabled", True) else 3
    burner = Burner(**payload)
    db.add(burner)
    db.flush()
    db.commit()
    db.refresh(burner)
    runtime_status = await _refresh_and_persist_single_burner_status(db, burner)

    return {
        "code": 0,
        "message": "创建成功",
        "data": burner_to_dict(burner, runtime_status, request=request),
    }


@router.put("/{burner_id}", response_model=Response)
async def update_burner(
    burner_id: int,
    request: Request,
    burner_data: BurnerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:edit")),
):
    """更新烧录器"""
    ensure_schema()
    burner = db.query(Burner).filter(Burner.id == burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="烧录器不存在")
    _ensure_burner_not_in_use(db, burner, "编辑")

    raw_update_payload = burner_data.model_dump(exclude_unset=True)
    force_rebind_port = bool(raw_update_payload.pop("force_rebind_port", False))
    raw_update_payload.pop("status", None)
    update_payload = _normalize_burner_payload(raw_update_payload, existing=burner)
    update_payload = _ensure_burner_owner_node({
        "host_type": getattr(burner, "host_type", "local"),
        "host_address": getattr(burner, "host_address", None),
        "agent_url": getattr(burner, "agent_url", None),
        **update_payload,
    })
    resolved_payload = {
        "name": update_payload.get("name", burner.name),
        "type": update_payload.get("type", burner.type),
        "strategy": update_payload.get("strategy", burner.strategy),
        "sn": update_payload.get("sn", burner.sn),
        "port": update_payload.get("port", burner.port),
        "config_json": update_payload.get("config_json", burner.config_json),
        "agent_url": update_payload.get("agent_url", burner.agent_url),
        "host_address": update_payload.get("host_address", burner.host_address),
        "is_enabled": update_payload.get("is_enabled", burner.is_enabled),
    }
    _validate_burner_payload_required(resolved_payload)
    conflict_burner = _validate_burner_binding_unique(
        db,
        resolved_payload,
        existing=burner,
        force_rebind_port=force_rebind_port,
    )
    while conflict_burner is not None:
        _ensure_burner_not_in_use(db, conflict_burner, "换绑物理端口")
        _clear_conflicting_burner_port(conflict_burner, current_user.username)
        conflict_burner = _validate_burner_binding_unique(
            db,
            resolved_payload,
            existing=burner,
            force_rebind_port=force_rebind_port,
        )
    for key, value in update_payload.items():
        setattr(burner, key, value)

    burner.status = 1 if _is_burner_enabled(burner) else 3
    burner.modified_by = current_user.username

    db.commit()
    db.refresh(burner)
    runtime_status = await _refresh_and_persist_single_burner_status(db, burner)

    return {
        "code": 0,
        "message": "更新成功",
        "data": burner_to_dict(burner, runtime_status, request=request),
    }


@router.delete("/{burner_id}", response_model=Response)
async def delete_burner(
    burner_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:delete")),
):
    """删除烧录器"""
    ensure_schema()
    burner = db.query(Burner).filter(Burner.id == burner_id).first()
    if not burner:
        raise HTTPException(status_code=404, detail="烧录器不存在")

    running_task_count = (
        db.query(BurningTask.id)
        .filter(
            BurningTask.burner_id == burner_id,
            BurningTask.status.in_([int(TaskStatus.RUNNING), int(TaskStatus.TERMINATING)]),
        )
        .count()
    )
    if running_task_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"当前设备仍有 {running_task_count} 条执行中的任务，暂不能删除，请先停止相关任务后重试",
        )

    referenced_task_count = (
        db.query(BurningTask.id)
        .filter(BurningTask.burner_id == burner_id)
        .count()
    )
    if referenced_task_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"当前设备已被 {referenced_task_count} 条任务引用，暂不能删除，请先解除任务关联后重试",
        )

    db.delete(burner)
    db.commit()

    return {
        "code": 0,
        "message": "删除成功",
    }


@router.post("/scan", response_model=Response)
async def scan_burners(
    scan_request: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("burner:scan")),
):
    """
    扫描/获取当前物理硬件信息（SN/Port）
    """
    ensure_schema()
    payload = scan_request or {}
    burner_id = payload.get("burner_id")
    existing = db.query(Burner).filter(Burner.id == burner_id).first() if burner_id else None
    strategy = payload.get("strategy") or (existing.strategy if existing else 1)
    device_type = payload.get("type") or (existing.type if existing else None)
    location = payload.get("location") or (existing.location if existing else None)
    agent_url = payload.get("agent_url") or (getattr(existing, "agent_url", None) if existing else None)
    allow_fallback = bool(payload.get("allow_fallback", False))
    logger.info(
        "burner.scan.request | %s",
        json.dumps(
            {
                "burner_id": burner_id,
                "device_type": device_type,
                "location": location,
                "strategy": strategy,
                "has_agent_url": bool(str(agent_url or "").strip()),
                "allow_fallback": allow_fallback,
            },
            ensure_ascii=False,
        ),
    )

    if str(agent_url or "").strip():
        try:
            remote_res = _remote_scan_burner(
                str(agent_url),
                device_type,
                location,
                strategy,
                sn=getattr(existing, "sn", None) if existing else None,
                port=getattr(existing, "port", None) if existing else None,
                usb_binding=_burner_usb_binding(existing),
            )
            remote_data = remote_res.get("data") or {}
            logger.info(
                "burner.scan.result | %s",
                json.dumps(
                    {
                        "burner_id": burner_id,
                        "mode": "agent",
                        "agent_url": agent_url,
                        "online": bool(remote_data.get("online")),
                        "source": remote_data.get("source") or "agent_remote",
                    },
                    ensure_ascii=False,
                ),
            )
            return {
                "code": 0,
                "message": remote_res.get("message") or "已通过下位机完成远程检测",
                "data": {
                    "sn": remote_data.get("sn"),
                    "port": remote_data.get("port"),
                    "source": remote_data.get("source") or "agent_remote",
                    "device_name": remote_data.get("device_name") or (existing.name if existing else device_type),
                    "online": bool(remote_data.get("online")),
                }
            }
        except Exception as exc:
            logger.exception(
                "burner.scan.agent_failed | %s",
                json.dumps(
                    {
                        "burner_id": burner_id,
                        "agent_url": agent_url,
                        "device_type": device_type,
                        "location": location,
                        "strategy": strategy,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                ),
            )
            return {
                "code": 0,
                "message": "代理地址暂时不可达，请检查网络和代理服务状态后重试",
                "data": {
                    "sn": existing.sn if existing else None,
                    "port": existing.port if existing else None,
                    "source": "agent_unreachable",
                    "device_name": existing.name if existing else device_type,
                    "online": False,
                }
            }

    await asyncio.to_thread(_refresh_windows_pnp_state)
    usb_devices = await asyncio.to_thread(_probe_usb_devices, force_refresh=True)
    scanned = _build_scan_result(
        device_type,
        location,
        strategy,
        existing,
        allow_fallback=allow_fallback,
        usb_devices=usb_devices,
    )
    logger.info(
        "burner.scan.result | %s",
        json.dumps(
            {
                "burner_id": burner_id,
                "mode": "local",
                "device_type": device_type,
                "location": location,
                "strategy": strategy,
                "online": bool(scanned and scanned.get("online")),
                "source": scanned.get("source") if scanned else "not_found",
            },
            ensure_ascii=False,
        ),
    )

    if not scanned:
        return {
            "code": 0,
            "message": "未检测到对应烧录器，请检查设备连接、USB口和驱动是否正常后重试",
            "data": {
                "sn": None,
                "port": None,
                "source": "not_found",
                "device_name": existing.name if existing else device_type,
                "online": False,
            }
        }

    return {
        "code": 0,
        "message": "已检测到烧录器并读取识别信息" if scanned["source"] == "usb_probe" else "未检测到实物设备，已生成稳定识别信息",
        "data": {
            "sn": scanned["sn"],
            "port": scanned["port"],
            "source": scanned["source"],
            "device_name": scanned["name"],
            "online": bool(scanned.get("online")),
        }
    }


@router.post("/agent/scan", response_model=Response)
async def agent_scan_burners(request: Request, scan_request: Optional[dict] = Body(default=None)):
    require_agent_token(request)
    payload = scan_request or {}
    strategy = payload.get("strategy") or 1
    device_type = payload.get("type")
    location = payload.get("location")
    expected_sn = payload.get("sn")
    expected_port = payload.get("port")
    expected_usb_binding = _normalize_usb_binding(payload.get("usb_binding"))
    logger.info(
        "burner.agent_scan.request | %s",
        json.dumps(
            {"device_type": device_type, "location": location, "strategy": strategy},
            ensure_ascii=False,
        ),
    )
    await asyncio.to_thread(_refresh_windows_pnp_state)
    usb_devices = await asyncio.to_thread(_probe_usb_devices, force_refresh=True)
    scanned = _build_scan_result(
        device_type,
        location,
        strategy,
        existing=None,
        allow_fallback=False,
        usb_devices=usb_devices,
        expected_sn=expected_sn,
        expected_port=expected_port,
        expected_usb_binding=expected_usb_binding,
    )
    if not scanned:
        logger.warning(
            "burner.agent_scan.not_found | %s",
            json.dumps(
                {"device_type": device_type, "location": location, "strategy": strategy},
                ensure_ascii=False,
            ),
        )
        return {
            "code": 0,
            "message": "下位机未检测到对应烧录器，请检查设备连接、USB口和驱动是否正常后重试",
            "data": {
                "sn": None,
                "port": None,
                "source": "agent_not_found",
                "device_name": device_type,
                "online": False,
            }
        }
    logger.info(
        "burner.agent_scan.found | %s",
        json.dumps(
            {
                "device_type": device_type,
                "location": location,
                "strategy": strategy,
                "sn": scanned.get("sn"),
                "port": scanned.get("port"),
            },
            ensure_ascii=False,
        ),
    )
    return {
        "code": 0,
        "message": "下位机已检测到烧录器并读取识别信息",
        "data": {
            "sn": scanned["sn"],
            "port": scanned["port"],
            "source": "agent_usb_probe",
            "device_name": scanned["name"],
            "online": True,
        }
    }


@router.post("/agent/discovery", response_model=Response)
async def agent_discover_burners(request: Request, _: Optional[dict] = Body(default=None)):
    require_agent_token(request)
    items = await asyncio.to_thread(_discover_local_candidates, True)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "items": items,
            "total": len(items),
        },
    }
