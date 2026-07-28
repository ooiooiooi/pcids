import asyncio
import json
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, ProtocolLog, ProtocolSession
from backend.routers import protocol_tests
from backend.utils import can_adapters
from backend.utils.can_adapters import (
    CAN_ADAPTER_BACKENDS,
    CanAdapterConnection,
    CanAdapterDevice,
    CanAdapterError,
    CanChannelDescriptor,
    CanDependencyMissingError,
    CanFrame,
    _normalize_can_bitrate,
    can_fd_dlc_to_length,
    can_fd_length_to_dlc,
    list_can_adapter_devices,
    match_expected_rx_frame,
    open_can_adapter_connection,
    resolve_can_adapter_device,
)


class FakeCanBackend(can_adapters.CanAdapterBackend):
    backend_key = "fake_can"
    adapter_name = "USBCANFD-200U"

    def __init__(self, devices: list[CanAdapterDevice], *, transmit_error: Exception | None = None, received_frames: list[CanFrame] | None = None):
        self._devices = devices
        self.transmit_error = transmit_error
        self.received_frames = received_frames or []
        self.receive_batches: list[list[CanFrame]] = []
        self.receive_lock = threading.Lock()
        self.opened: list[str] = []
        self.open_device_indexes: list[int | None] = []
        self.inited: list[tuple[str, str]] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.closed: list[str] = []
        self.transmitted_frames: list[CanFrame] = []
        self.receive_calls = 0

    def enumerate_devices(self) -> list[CanAdapterDevice]:
        return self._devices

    def open_device(self, device: CanAdapterDevice) -> str:
        self.opened.append(device.adapter_key)
        self.open_device_indexes.append(device.sdk_device_index)
        return f"device:{device.adapter_key}"

    def init_channel(self, device_handle: str, channel_name: str, *, protocol: str, config: dict[str, object]) -> str:
        self.inited.append((device_handle, channel_name))
        return f"channel:{channel_name}"

    def start_channel(self, device_handle: str, channel_handle: str) -> None:
        self.started.append(channel_handle)

    def transmit(self, connection: CanAdapterConnection, frame: CanFrame) -> None:
        if self.transmit_error is not None:
            raise self.transmit_error
        self.transmitted_frames.append(frame)

    def receive(self, connection: CanAdapterConnection, *, timeout_ms: int, expected_rx_id: int | None = None, expected_rx_mask: int | None = None) -> list[CanFrame]:
        del connection, expected_rx_id, expected_rx_mask
        self.receive_calls += 1
        with self.receive_lock:
            if self.receive_batches:
                return list(self.receive_batches.pop(0))
        if self.received_frames:
            return list(self.received_frames)
        time.sleep(min(max(timeout_ms, 1), 20) / 1000.0)
        return []

    def stop_channel(self, device_handle: str, channel_handle: str) -> None:
        self.stopped.append(channel_handle)

    def close_device(self, device_handle: str) -> None:
        self.closed.append(device_handle)

    def enqueue_receive_batch(self, *frames: CanFrame) -> None:
        with self.receive_lock:
            self.receive_batches.append(list(frames))


def make_device(
    adapter_key: str,
    serial_number: str,
    channels: list[str],
    *,
    dependency_status: str = "ready",
    dependency_message: str = "",
    sdk_device_index: int | None = None,
    adapter_device: str = "",
) -> CanAdapterDevice:
    return CanAdapterDevice(
        adapter_key=adapter_key,
        backend_key="fake_can",
        adapter_name="USBCANFD-200U",
        serial_number=serial_number,
        pnp_device_id=f"USB\\VID_3068&PID_0009\\{serial_number}",
        hardware_ids=["USB\\VID_3068&PID_0009"],
        description="USBCANFD-200U",
        manufacturer="Vendor",
        dependency_status=dependency_status,
        dependency_message=dependency_message,
        channels=[CanChannelDescriptor(name=name, index=index) for index, name in enumerate(channels)],
        sdk_device_index=sdk_device_index,
        adapter_device=adapter_device,
    )


def make_frame(
    *,
    frame_id: int,
    is_extended_id: bool = False,
    is_fd: bool = False,
    bitrate_switch: bool = False,
    is_remote_frame: bool = False,
    data: bytes = b"",
    declared_data_length: int | None = None,
    channel_name: str = "CAN0",
) -> CanFrame:
    return CanFrame(
        frame_id=frame_id,
        is_extended_id=is_extended_id,
        is_fd=is_fd,
        bitrate_switch=bitrate_switch,
        is_remote_frame=is_remote_frame,
        data=data,
        declared_data_length=len(data) if declared_data_length is None else declared_data_length,
        channel_name=channel_name,
    )


class FakeZlgApi:
    def __init__(self):
        self.open_calls: list[tuple[int, int, int]] = []
        self.init_calls: list[tuple[int, int, int, int]] = []
        self.start_calls: list[int] = []
        self.reset_calls: list[int] = []
        self.close_calls: list[int] = []

    def ZCAN_OpenDevice(self, device_type: int, device_index: int, reserved: int) -> int:
        self.open_calls.append((device_type, device_index, reserved))
        return 0x1000 + device_index

    def ZCAN_CloseDevice(self, handle: int) -> int:
        self.close_calls.append(handle)
        return can_adapters.STATUS_OK

    def ZCAN_GetDeviceInfoEx(self, handle: int, info_ptr) -> int:
        return can_adapters.STATUS_OK

    def ZCAN_GetDeviceInf(self, handle: int, info_ptr) -> int:
        return can_adapters.STATUS_OK

    def ZCAN_InitCAN(self, device_handle: int, channel_index: int, config_ptr) -> int:
        config = config_ptr._obj
        self.init_calls.append((device_handle, channel_index, int(config.can_type), int(config.config.canfd.mode)))
        return 0x2000 + channel_index

    def ZCAN_StartCAN(self, channel_handle: int) -> int:
        self.start_calls.append(channel_handle)
        return can_adapters.STATUS_OK

    def ZCAN_ResetCAN(self, channel_handle: int) -> int:
        self.reset_calls.append(channel_handle)
        return can_adapters.STATUS_OK


class FakeProbeZlgApi:
    def __init__(
        self,
        *,
        open_handles: dict[int, int | list[int]],
        ex_results: dict[int, tuple[int, dict[str, object]]] | None = None,
        legacy_results: dict[int, tuple[int, dict[str, object]]] | None = None,
    ):
        self.open_handles = {
            device_index: list(handles) if isinstance(handles, list) else [handles]
            for device_index, handles in open_handles.items()
        }
        self.ex_results = ex_results or {}
        self.legacy_results = legacy_results or {}
        self.open_calls: list[tuple[int, int, int]] = []
        self.ex_calls: list[int] = []
        self.legacy_calls: list[int] = []
        self.close_calls: list[int] = []

    def ZCAN_OpenDevice(self, device_type: int, device_index: int, reserved: int) -> int:
        self.open_calls.append((device_type, device_index, reserved))
        handles = self.open_handles.get(device_index)
        if not handles:
            return 0
        return handles.pop(0)

    def ZCAN_CloseDevice(self, handle: int) -> int:
        self.close_calls.append(handle)
        return can_adapters.STATUS_OK

    def ZCAN_GetDeviceInfoEx(self, handle: int, info_ptr) -> int:
        self.ex_calls.append(handle)
        status, payload = self.ex_results.get(handle, (0, {}))
        info = info_ptr._obj
        if status == can_adapters.STATUS_OK:
            _write_c_bytes(info.serial_number, str(payload.get("serial_number") or ""))
            _write_c_bytes(info.device_name, str(payload.get("device_name") or ""))
            _write_c_bytes(info.hardware_type, str(payload.get("hardware_type") or ""))
            info.can_channel_number = int(payload.get("can_channel_count") or 0)
        return status

    def ZCAN_GetDeviceInf(self, handle: int, info_ptr) -> int:
        self.legacy_calls.append(handle)
        status, payload = self.legacy_results.get(handle, (0, {}))
        info = info_ptr._obj
        if status == can_adapters.STATUS_OK:
            _write_c_bytes(info.str_Serial_Num, str(payload.get("serial_number") or ""))
            _write_c_bytes(info.str_hw_Type, str(payload.get("hardware_type") or ""))
            info.can_Num = int(payload.get("can_channel_count") or 0)
        return status


def _write_c_bytes(target, value: str) -> None:
    raw = value.encode("utf-8")
    for index in range(len(target)):
        target[index] = 0
    for index, item in enumerate(raw[: len(target)]):
        target[index] = item


class CanProtocolBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._db_temp_dir = tempfile.TemporaryDirectory()
        self._engine = create_engine(
            f"sqlite:///{Path(self._db_temp_dir.name) / 'protocol-test.db'}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self._engine)
        self.Session = sessionmaker(bind=self._engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self) -> None:
        protocol_tests._close_all_can_session_connections()
        self.db.close()
        self._engine.dispose()
        self._db_temp_dir.cleanup()

    def _create_session(self, *, protocol: str, config: dict[str, object]) -> ProtocolSession:
        session = ProtocolSession(
            task_no="202606240001",
            target="Protocol Board",
            protocol=protocol,
            config_json=json.dumps(config, ensure_ascii=False),
            status=1,
            tx_count=0,
            rx_count=0,
            executor="tester",
            ip_address="127.0.0.1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def test_windows_vid_pid_enumeration_detects_usbcanfd_device(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._enumerate_windows_pnp_devices",
                    return_value=[
                        {
                            "instance_id": "USB\\VID_3068&PID_0009\\ABC123",
                            "hardware_ids": ["USB\\VID_3068&PID_0009"],
                            "friendly_name": "USBCANFD-200U",
                            "description": "USBCANFD-200U",
                            "manufacturer": "Vendor",
                        }
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._load_usbcanfd_verified_manifest",
                    return_value=(
                        {
                            "status": "verified",
                            "channel_names": ["CAN0", "CAN1"],
                        },
                        None,
                    ),
                )
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._load_usbcanfd_zlg_api", return_value=FakeZlgApi())
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._probe_usbcanfd_runtime_devices",
                    return_value=[
                        can_adapters._UsbcanfdRuntimeDevice(
                            device_index=3,
                            serial_number="ABC123",
                            can_channel_count=2,
                            device_name="USBCANFD-200U",
                            hardware_type="USBCANFD-200U",
                        )
                    ],
                )
            )
            devices = list_can_adapter_devices("canfd")

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["adapter_name"], "USBCANFD-200U")
        self.assertEqual(devices[0]["serial_number"], "ABC123")
        self.assertEqual([item["name"] for item in devices[0]["channels"]], ["CAN0", "CAN1"])
        self.assertEqual(devices[0]["dependency_status"], "ready")

    def test_windows_vid_pid_enumeration_requires_exact_runtime_mapping(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._enumerate_windows_pnp_devices",
                    return_value=[
                        {
                            "instance_id": "USB\\VID_3068&PID_0009\\ABC123",
                            "hardware_ids": ["USB\\VID_3068&PID_0009"],
                            "friendly_name": "USBCANFD-200U",
                            "description": "USBCANFD-200U",
                            "manufacturer": "Vendor",
                        }
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._load_usbcanfd_verified_manifest",
                    return_value=(
                        {
                            "status": "verified",
                            "channel_names": ["CAN0", "CAN1"],
                        },
                        None,
                    ),
                )
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._load_usbcanfd_zlg_api", return_value=FakeZlgApi())
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._probe_usbcanfd_runtime_devices", return_value=[])
            )
            devices = list_can_adapter_devices("canfd")

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["dependency_status"], "device_busy")
        self.assertIn("其他程序占用", devices[0]["dependency_message"])

    def test_windows_vid_pid_enumeration_falls_back_to_unique_runtime_device(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._enumerate_windows_pnp_devices",
                    return_value=[
                        {
                            "instance_id": "USB\\VID_3068&PID_0009\\6&123ABC&0&3",
                            "hardware_ids": ["USB\\VID_3068&PID_0009"],
                            "friendly_name": "USBCANFD-200U",
                            "description": "USBCANFD-200U",
                            "manufacturer": "Vendor",
                        }
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._load_usbcanfd_verified_manifest",
                    return_value=(
                        {
                            "status": "verified",
                            "channel_names": ["CAN0", "CAN1"],
                        },
                        None,
                    ),
                )
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._load_usbcanfd_zlg_api", return_value=FakeZlgApi())
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._probe_usbcanfd_runtime_devices",
                    return_value=[
                        can_adapters._UsbcanfdRuntimeDevice(
                            device_index=3,
                            serial_number="ABC123",
                            can_channel_count=2,
                            device_name="USBCANFD-200U",
                            hardware_type="USBCANFD-200U",
                        )
                    ],
                )
            )
            devices = list_can_adapter_devices("canfd")

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["dependency_status"], "ready")
        self.assertEqual(devices[0]["serial_number"], "ABC123")
        self.assertEqual([item["name"] for item in devices[0]["channels"]], ["CAN0", "CAN1"])

    def test_usbcanfd_backend_opens_exact_sdk_device_index(self):
        backend = can_adapters.Usbcanfd200UBackend()
        backend._api = FakeZlgApi()
        device = CanAdapterDevice(
            adapter_key="usbcanfd_200u:USB\\VID_3068&PID_0009\\ABC123",
            backend_key=backend.backend_key,
            adapter_name="USBCANFD-200U",
            serial_number="ABC123",
            pnp_device_id="USB\\VID_3068&PID_0009\\ABC123",
            hardware_ids=["USB\\VID_3068&PID_0009"],
            description="USBCANFD-200U",
            manufacturer="Vendor",
            channels=[CanChannelDescriptor(name="CAN0", index=0), CanChannelDescriptor(name="CAN1", index=1)],
            sdk_device_index=7,
        )

        handle = backend.open_device(device)

        self.assertEqual(backend._api.open_calls, [(can_adapters.USBCANFD_200U_DEVICE_TYPE, 7, 0)])
        self.assertEqual(handle.device_index, 7)

    def test_usbcanfd_backend_init_channel_applies_official_paths(self):
        backend = can_adapters.Usbcanfd200UBackend()
        backend._api = FakeZlgApi()
        device_handle = can_adapters._UsbcanfdDeviceHandle(api=backend._api, raw_handle=0x1000, device_index=0)
        set_value_calls: list[tuple[int, str, object]] = []

        def record_set_value(handle, channel_index, item_name, value):
            set_value_calls.append((channel_index, item_name, value))

        with patch.object(backend, "_set_channel_value", side_effect=record_set_value):
            channel_handle = backend.init_channel(
                device_handle,
                "CAN1",
                protocol="canfd",
                config={
                    "baud_rate": 500000,
                    "data_baud_rate": 2000000,
                    "termination_enabled": True,
                    "tx_timeout_ms": 100,
                },
            )

        self.assertEqual(
            set_value_calls,
            [
                (1, "protocol", "1"),
                (1, "canfd_abit_baud_rate", "500000"),
                (1, "canfd_standard", "0"),
                (1, "canfd_dbit_baud_rate", "2000000"),
                (1, "initenal_resistance", "1"),
                (1, "tx_timeout", "100"),
            ],
        )
        self.assertEqual(backend._api.init_calls, [(0x1000, 1, can_adapters.TYPE_CANFD, 0)])
        self.assertEqual(channel_handle.channel_name, "CAN1")

    def test_usbcanfd_backend_supports_classic_can_protocol_and_uses_classic_init(self):
        backend = can_adapters.Usbcanfd200UBackend()
        backend._api = FakeZlgApi()
        device_handle = can_adapters._UsbcanfdDeviceHandle(api=backend._api, raw_handle=0x1000, device_index=0)
        set_value_calls: list[tuple[int, str, object]] = []

        def record_set_value(handle, channel_index, item_name, value):
            set_value_calls.append((channel_index, item_name, value))

        with patch.object(backend, "_set_channel_value", side_effect=record_set_value):
            channel_handle = backend.init_channel(
                device_handle,
                "CAN0",
                protocol="can",
                config={
                    "baud_rate": "500kbps",
                    "termination_enabled": False,
                    "tx_timeout_ms": 120,
                },
            )

        self.assertEqual(backend.supported_protocols, frozenset({"can", "canfd"}))
        self.assertEqual(
            set_value_calls,
            [
                (0, "protocol", "0"),
                (0, "canfd_abit_baud_rate", "500000"),
                (0, "initenal_resistance", "0"),
                (0, "tx_timeout", "120"),
            ],
        )
        self.assertEqual(backend._api.init_calls, [(0x1000, 0, can_adapters.TYPE_CAN, 0)])
        self.assertEqual(channel_handle.protocol, "can")

    def test_usbcanfd_backend_init_channel_prioritizes_canfd_arbitration_bitrate_fields(self):
        backend = can_adapters.Usbcanfd200UBackend()
        backend._api = FakeZlgApi()
        device_handle = can_adapters._UsbcanfdDeviceHandle(api=backend._api, raw_handle=0x1000, device_index=0)
        set_value_calls: list[tuple[int, str, object]] = []

        def record_set_value(handle, channel_index, item_name, value):
            set_value_calls.append((channel_index, item_name, value))

        with patch.object(backend, "_set_channel_value", side_effect=record_set_value):
            backend.init_channel(
                device_handle,
                "CAN0",
                protocol="canfd",
                config={
                    "arb_baud_rate": "500kbps",
                    "arb_bitrate": "250kbps",
                    "baud_rate": 125000,
                    "bitrate": 100000,
                    "data_baud_rate": "2Mbps",
                    "termination_enabled": True,
                },
            )

        self.assertIn((0, "canfd_abit_baud_rate", "500000"), set_value_calls)
        self.assertIn((0, "canfd_dbit_baud_rate", "2000000"), set_value_calls)

    def test_runtime_probe_uses_legacy_device_info_first(self):
        api = FakeProbeZlgApi(
            open_handles={0: 0x1000},
            legacy_results={
                0x1000: (
                    can_adapters.STATUS_OK,
                    {
                        "serial_number": "LEGACY-ABC123",
                        "hardware_type": "USBCANFD-200U",
                        "can_channel_count": 2,
                    },
                )
            },
        )

        devices = can_adapters._probe_usbcanfd_runtime_devices(api)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial_number, "LEGACY-ABC123")
        self.assertEqual(devices[0].device_index, 0)
        self.assertEqual(devices[0].can_channel_count, 2)
        self.assertEqual(api.legacy_calls, [0x1000])
        self.assertEqual(api.ex_calls, [])
        self.assertEqual(api.close_calls, [0x1000])

    def test_runtime_probe_reopens_handle_before_falling_back_to_ex_device_info(self):
        api = FakeProbeZlgApi(
            open_handles={0: [0x1000, 0x2000]},
            legacy_results={0x1000: (4, {})},
            ex_results={
                0x2000: (
                    can_adapters.STATUS_OK,
                    {
                        "serial_number": "EX-ABC123",
                        "device_name": "USBCANFD-200U",
                        "hardware_type": "USBCANFD-200U",
                        "can_channel_count": 2,
                    },
                )
            },
        )

        devices = can_adapters._probe_usbcanfd_runtime_devices(api)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial_number, "EX-ABC123")
        self.assertEqual(devices[0].hardware_type, "USBCANFD-200U")
        self.assertEqual(devices[0].device_name, "USBCANFD-200U")
        self.assertEqual(devices[0].can_channel_count, 2)
        self.assertEqual(api.open_calls[:2], [(can_adapters.USBCANFD_200U_DEVICE_TYPE, 0, 0), (can_adapters.USBCANFD_200U_DEVICE_TYPE, 0, 0)])
        self.assertEqual(api.legacy_calls, [0x1000])
        self.assertEqual(api.ex_calls, [0x2000])
        self.assertEqual(api.close_calls, [0x1000, 0x2000])

    def test_runtime_probe_closes_reopened_handles_when_legacy_and_ex_both_fail(self):
        api = FakeProbeZlgApi(
            open_handles={0: [0x1000, 0x2000], 3: [0x1003, 0x2003]},
            legacy_results={0x1000: (4, {}), 0x1003: (5, {})},
            ex_results={0x2000: (0, {}), 0x2003: (0, {})},
        )

        devices = can_adapters._probe_usbcanfd_runtime_devices(api)

        self.assertEqual(devices, [])
        self.assertEqual(api.legacy_calls, [0x1000, 0x1003])
        self.assertEqual(api.ex_calls, [0x2000, 0x2003])
        self.assertEqual(api.close_calls, [0x1000, 0x2000, 0x1003, 0x2003])

    def test_runtime_probe_keeps_legacy_success_without_opening_ex_handle(self):
        api = FakeProbeZlgApi(
            open_handles={0: [0x1000, 0x2000]},
            legacy_results={
                0x1000: (
                    can_adapters.STATUS_OK,
                    {
                        "serial_number": "C8D8CEAB604602A4CF80",
                        "hardware_type": "USBCANFD-200U",
                        "can_channel_count": 2,
                    },
                )
            },
        )

        devices = can_adapters._probe_usbcanfd_runtime_devices(api)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial_number, "C8D8CEAB604602A4CF80")
        self.assertEqual(devices[0].hardware_type, "USBCANFD-200U")
        self.assertEqual(devices[0].device_name, "USBCANFD-200U")
        self.assertEqual(devices[0].can_channel_count, 2)
        self.assertEqual(api.open_calls[:1], [(can_adapters.USBCANFD_200U_DEVICE_TYPE, 0, 0)])
        self.assertEqual(api.legacy_calls, [0x1000])
        self.assertEqual(api.ex_calls, [])
        self.assertEqual(api.close_calls, [0x1000])

    def test_windows_vid_pid_enumeration_uses_exact_runtime_device_index_per_serial(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._enumerate_windows_pnp_devices",
                    return_value=[
                        {
                            "instance_id": "USB\\VID_3068&PID_0009\\SERIAL-A",
                            "hardware_ids": ["USB\\VID_3068&PID_0009"],
                            "friendly_name": "USBCANFD-200U A",
                            "description": "USBCANFD-200U",
                            "manufacturer": "Vendor",
                        },
                        {
                            "instance_id": "USB\\VID_3068&PID_0009\\SERIAL-B",
                            "hardware_ids": ["USB\\VID_3068&PID_0009"],
                            "friendly_name": "USBCANFD-200U B",
                            "description": "USBCANFD-200U",
                            "manufacturer": "Vendor",
                        },
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._load_usbcanfd_verified_manifest",
                    return_value=(
                        {
                            "status": "verified",
                            "channel_names": ["CAN0", "CAN1"],
                        },
                        None,
                    ),
                )
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._load_usbcanfd_zlg_api", return_value=FakeZlgApi())
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._probe_usbcanfd_runtime_devices",
                    return_value=[
                        can_adapters._UsbcanfdRuntimeDevice(
                            device_index=9,
                            serial_number="SERIAL-B",
                            can_channel_count=2,
                            device_name="USBCANFD-200U",
                            hardware_type="USBCANFD-200U",
                        ),
                        can_adapters._UsbcanfdRuntimeDevice(
                            device_index=4,
                            serial_number="SERIAL-A",
                            can_channel_count=2,
                            device_name="USBCANFD-200U",
                            hardware_type="USBCANFD-200U",
                        ),
                    ],
                )
            )

            devices = list_can_adapter_devices("canfd")

        devices_by_serial = {item["serial_number"]: item for item in devices}
        self.assertEqual(devices_by_serial["SERIAL-A"]["sdk_device_index"], 4)
        self.assertEqual(devices_by_serial["SERIAL-B"]["sdk_device_index"], 9)

    def test_windows_vid_pid_enumeration_marks_device_missing_when_probe_finds_no_runtime_device(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._enumerate_windows_pnp_devices",
                    return_value=[
                        {
                            "instance_id": "USB\\VID_3068&PID_0009\\C8D8CEAB604602A4CF80",
                            "hardware_ids": ["USB\\VID_3068&PID_0009"],
                            "friendly_name": "USBCANFD-200U",
                            "description": "USBCANFD-200U",
                            "manufacturer": "Vendor",
                        }
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "backend.utils.can_adapters._load_usbcanfd_verified_manifest",
                    return_value=(
                        {
                            "status": "verified",
                            "channel_names": ["CAN0", "CAN1"],
                        },
                        None,
                    ),
                )
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._load_usbcanfd_zlg_api", return_value=FakeZlgApi())
            )
            stack.enter_context(
                patch("backend.utils.can_adapters._probe_usbcanfd_runtime_devices", return_value=[])
            )

            devices = list_can_adapter_devices("canfd")

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["dependency_status"], "device_busy")
        self.assertIsNone(devices[0]["sdk_device_index"])

    def test_device_busy_status_surfaces_non_dependency_error(self):
        backend = FakeCanBackend(
            [make_device("fake_can:abc", "ABC123", [], dependency_status="device_busy", dependency_message="设备可能正被其他程序占用，请关闭占用后重试")],
        )
        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            with self.assertRaises(HTTPException) as context:
                protocol_tests._open_protocol_channel_resources(
                    "canfd",
                    {"adapter_key": "fake_can:abc", "physical_channel": "CAN0"},
                    [],
                )

        self.assertNotIn("dependency_missing", context.exception.detail)
        self.assertIn("其他程序占用", context.exception.detail)


    def test_probe_can_adapters_does_not_leak_com8_into_canfd_candidates(self):
        usbcan = {
            "adapter_key": "fake_can:abc",
            "adapter_name": "USBCANFD-200U",
            "serial_number": "ABC123",
            "device": "USB\\VID_3068&PID_0009\\ABC123",
            "channels": [{"name": "CAN0"}, {"name": "CAN1"}],
            "source": "windows_pnp",
        }
        with ExitStack() as stack:
            stack.enter_context(patch("backend.routers.protocol_tests._probe_can_network_interfaces", return_value=[]))
            stack.enter_context(patch("backend.routers.protocol_tests._probe_darwin_can_usb_devices", return_value=[]))
            stack.enter_context(patch("backend.routers.protocol_tests._probe_serial_devices", return_value=[{"device": "COM8", "name": "COM8"}]))
            stack.enter_context(patch("backend.routers.protocol_tests.list_can_adapter_devices", return_value=[usbcan]))
            devices = protocol_tests._probe_can_adapters()

        serialized = json.dumps(devices, ensure_ascii=False)
        self.assertIn("USBCANFD-200U", serialized)
        self.assertNotIn("COM8", serialized)

    def test_multi_adapter_and_physical_channel_selection_are_exact(self):
        backend = FakeCanBackend(
            [
                make_device("fake_can:first", "SERIAL-A", ["CAN0", "CAN1"]),
                make_device("fake_can:second", "SERIAL-B", ["CAN0", "CAN1"]),
            ]
        )
        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            connection = open_can_adapter_connection(
                "canfd",
                {
                    "adapter_key": "fake_can:second",
                    "physical_channel": "CAN1",
                },
            )

        self.assertEqual(connection.device.serial_number, "SERIAL-B")
        self.assertEqual(connection.channel_name, "CAN1")
        self.assertEqual(backend.opened, ["fake_can:second"])
        self.assertEqual(backend.inited, [("device:fake_can:second", "CAN1")])

    def test_can_fd_length_mapping(self):
        self.assertEqual(can_fd_length_to_dlc(8), 8)
        self.assertEqual(can_fd_length_to_dlc(12), 9)
        self.assertEqual(can_fd_length_to_dlc(64), 15)
        self.assertEqual(can_fd_dlc_to_length(9), 12)
        self.assertEqual(can_fd_dlc_to_length(15), 64)

    def test_normalize_can_bitrate_accepts_numeric_and_unit_values(self):
        self.assertEqual(_normalize_can_bitrate(500000, field_label="仲裁域波特率"), "500000")
        self.assertEqual(_normalize_can_bitrate("500000", field_label="仲裁域波特率"), "500000")
        self.assertEqual(_normalize_can_bitrate("500kbps", field_label="仲裁域波特率"), "500000")
        self.assertEqual(_normalize_can_bitrate("500 kbps", field_label="仲裁域波特率"), "500000")
        self.assertEqual(_normalize_can_bitrate("1Mbps", field_label="仲裁域波特率"), "1000000")
        self.assertEqual(_normalize_can_bitrate("2Mbps", field_label="仲裁域波特率"), "2000000")
        self.assertEqual(_normalize_can_bitrate("5Mbps", field_label="仲裁域波特率"), "5000000")
        self.assertEqual(_normalize_can_bitrate("8Mbps", field_label="仲裁域波特率"), "8000000")

    def test_normalize_can_bitrate_rejects_empty_invalid_or_non_positive_values(self):
        for invalid_value in ("", " ", None, "500k", "500gbps", "abc", 0, -1, "0", "0kbps"):
            with self.assertRaises(CanAdapterError):
                _normalize_can_bitrate(invalid_value, field_label="仲裁域波特率")

    def test_send_without_real_receive_passes_without_ack_or_rx_log(self):
        backend = FakeCanBackend(
            [make_device("fake_can:abc", "ABC123", ["CAN0", "CAN1"])],
            received_frames=[],
        )
        session = self._create_session(
            protocol="canfd",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN1",
                "id_format": "标准帧(11位)",
                "data_length": 12,
                "expected_rx_id": "0x123",
                "expected_data": "01 02",
                "rx_timeout_ms": 50,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN1",
            channel_name="CAN1",
            protocol="canfd",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x123", dlc=12, data="01 02 03 04 05 06 07 08 09 0A 0B 0C"),
                    protocol="canfd",
                    merged_config=json.loads(session.config_json),
                    payload_data="01 02 03 04 05 06 07 08 09 0A 0B 0C",
                )
            )

        self.assertIn("按发送成功判定本次验证通过", result["message"])
        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).order_by(ProtocolLog.id.asc()).all()
        self.assertEqual([item.direction for item in logs], ["Tx", "System", "System"])
        self.assertNotIn("ACK", " ".join(str(item.data or "") for item in logs))
        self.assertEqual(session.tx_count, 1)
        self.assertEqual(session.rx_count, 0)
        self.assertEqual(json.loads(session.config_json).get("validation_result"), "passed")
        self.assertEqual(json.loads(session.config_json).get("validation_code"), "canfd_tx_only_passed")

    def test_sdk_failure_does_not_write_success_record(self):
        backend = FakeCanBackend(
            [make_device("fake_can:abc", "ABC123", ["CAN0"])],
            transmit_error=CanDependencyMissingError("缺少厂商 SDK DLL"),
        )
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "expected_rx_id": "0x123",
                "rx_timeout_ms": 50,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    protocol_tests._send_can_protocol_frame(
                        db=self.db,
                        session=session,
                        payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02 03 04 05 06 07 08"),
                        protocol="can",
                        merged_config=json.loads(session.config_json),
                        payload_data="01 02 03 04 05 06 07 08",
                    )
                )

        self.assertIn("dependency_missing", context.exception.detail)
        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).all()
        self.assertFalse(any(item.direction == "Tx" for item in logs))
        self.assertEqual(session.tx_count, 0)
        self.assertEqual(json.loads(session.config_json).get("validation_result"), "failed")
        self.assertEqual(session.status, 2)
        self.assertIsNone(protocol_tests._get_can_session_runtime(session.id))

    def test_tx_failed_does_not_write_tx_log_or_increment_count(self):
        backend = FakeCanBackend(
            [make_device("fake_can:abc", "ABC123", ["CAN0"])],
            transmit_error=CanAdapterError("tx_failed", "ZQWL USB-CAN发送不完整：期望16字节，实际3字节"),
        )
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "expected_rx_id": "0x123",
                "rx_timeout_ms": 50,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    protocol_tests._send_can_protocol_frame(
                        db=self.db,
                        session=session,
                        payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02 03 04 05 06 07 08"),
                        protocol="can",
                        merged_config=json.loads(session.config_json),
                        payload_data="01 02 03 04 05 06 07 08",
                    )
                )

        self.assertIn("发送不完整", context.exception.detail)
        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).all()
        self.assertFalse(any(item.direction == "Tx" for item in logs))
        self.assertEqual(session.tx_count, 0)
        self.assertEqual(json.loads(session.config_json).get("validation_result"), "failed")
        self.assertEqual(session.status, 1)
        self.assertIsNotNone(protocol_tests._get_can_session_runtime(session.id))

    def test_adapter_offline_send_error_releases_can_runtime_and_marks_session_disconnected(self):
        backend = FakeCanBackend(
            [make_device("fake_can:abc", "ABC123", ["CAN0"])],
            transmit_error=CanAdapterError("adapter_offline", "所选适配器已离线，请重新扫描"),
        )
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "expected_rx_id": "0x123",
                "rx_timeout_ms": 50,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    protocol_tests._send_can_protocol_frame(
                        db=self.db,
                        session=session,
                        payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02 03 04 05 06 07 08"),
                        protocol="can",
                        merged_config=json.loads(session.config_json),
                        payload_data="01 02 03 04 05 06 07 08",
                    )
                )

        self.assertIn("已离线", context.exception.detail)
        self.assertEqual(session.status, 2)
        self.assertIsNone(protocol_tests._get_can_session_runtime(session.id))

    def test_disconnect_releases_can_resources(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(protocol="canfd", config={"adapter_key": "fake_can:abc", "physical_channel": "CAN0"})
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="canfd",
        )

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            protocol_tests._store_can_session_connection(session.id, connection)
            asyncio.run(protocol_tests.disconnect_device(session.id, db=self.db, current_user=None, _=None))

        self.assertEqual(backend.stopped, ["channel:CAN0"])
        self.assertEqual(backend.closed, ["device:fake_can:abc"])
        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).first()
        self.assertEqual(refreshed.status, 2)

    def test_reconnect_same_can_channel_releases_stale_runtime(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0", "CAN1"])])
        stale_session = self._create_session(protocol="can", config={"adapter_key": "fake_can:abc", "physical_channel": "CAN0"})
        stale_connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:stale",
            channel_handle="channel:stale",
            channel_name="CAN0",
            protocol="can",
            channel_guard_key="fake_can:abc|CAN0",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            protocol_tests._store_can_session_connection(stale_session.id, stale_connection)
            _, new_connection, _, _, config = protocol_tests._open_protocol_channel_resources(
                "can",
                {"adapter_key": "fake_can:abc", "physical_channel": "CAN0", "baud_rate": "500kbps"},
                [],
            )
            protocol_tests.close_can_adapter_connection(new_connection)

        self.db.expire_all()
        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == stale_session.id).first()
        self.assertEqual(refreshed.status, 2)
        self.assertEqual(backend.stopped[0], "channel:stale")
        self.assertEqual(backend.closed[0], "device:stale")
        self.assertEqual(config["physical_channel"], "CAN0")
        self.assertIn("fake_can:abc", backend.opened)

    def test_zqwl_style_reconnect_releases_stale_adapter_even_when_channel_differs(self):
        device = make_device("fake_can:abc", "ABC123", ["CAN0", "CAN1"])
        backend = FakeCanBackend([device])
        stale_session = self._create_session(protocol="can", config={"adapter_key": "fake_can:abc", "physical_channel": "CAN0"})
        stale_connection = CanAdapterConnection(
            backend_key="fake_can",
            device=device,
            device_handle="device:stale",
            channel_handle="channel:stale",
            channel_name="CAN0",
            protocol="can",
            channel_guard_key=device.adapter_key,
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            protocol_tests._store_can_session_connection(stale_session.id, stale_connection)
            protocol_tests._force_release_conflicting_can_session(
                {"adapter_key": "fake_can:abc"},
                "CAN1",
            )

        self.db.expire_all()
        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == stale_session.id).first()
        self.assertEqual(refreshed.status, 2)
        self.assertEqual(backend.stopped, ["channel:stale"])
        self.assertEqual(backend.closed, ["device:stale"])
        self.assertIsNone(protocol_tests._get_can_session_runtime(stale_session.id))

    def test_missing_sdk_surfaces_dependency_missing_error(self):
        backend = FakeCanBackend(
            [make_device("fake_can:abc", "ABC123", [], dependency_status="dependency_missing", dependency_message="未找到 USBCANFD-200U SDK DLL")],
        )
        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            with self.assertRaises(HTTPException) as context:
                protocol_tests._open_protocol_channel_resources(
                    "canfd",
                    {"adapter_key": "fake_can:abc", "physical_channel": "CAN0"},
                    [],
                )

        self.assertIn("dependency_missing", context.exception.detail)

    def test_invalid_adapter_key_does_not_fallback_to_first_device(self):
        backend = FakeCanBackend(
            [
                make_device("fake_can:first", "SERIAL-A", ["CAN0"]),
                make_device("fake_can:second", "SERIAL-B", ["CAN1"]),
            ]
        )
        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            with self.assertRaises(CanAdapterError) as context:
                open_can_adapter_connection("can", {"adapter_key": "fake_can:missing", "physical_channel": "CAN0"})

        self.assertEqual(context.exception.code, "adapter_not_found")
        self.assertEqual(backend.opened, [])

    def test_missing_selected_adapter_reports_offline(self):
        backend = FakeCanBackend([make_device("fake_can:first", "SERIAL-A", ["CAN0"])])
        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            with self.assertRaises(CanAdapterError) as context:
                resolve_can_adapter_device(
                    "fake_can:offline",
                    expected_serial_number="SERIAL-OFFLINE",
                    expected_pnp_device_id="USB\\VID_3068&PID_0009\\SERIAL-OFFLINE",
                )

        self.assertEqual(context.exception.code, "adapter_offline")
        self.assertIn("已离线", context.exception.message)

    def test_resolve_can_adapter_device_accepts_matching_sdk_device_index_zero(self):
        backend = FakeCanBackend([make_device("fake_can:first", "SERIAL-A", ["CAN0"], sdk_device_index=0)])

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            _, device = resolve_can_adapter_device("fake_can:first", protocol="canfd", expected_sdk_device_index=0)

        self.assertEqual(device.sdk_device_index, 0)

    def test_resolve_can_adapter_device_accepts_matching_nonzero_sdk_device_index(self):
        backend = FakeCanBackend([make_device("fake_can:first", "SERIAL-A", ["CAN0"], sdk_device_index=1)])

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            _, device = resolve_can_adapter_device("fake_can:first", protocol="canfd", expected_sdk_device_index=1)

        self.assertEqual(device.sdk_device_index, 1)

    def test_resolve_can_adapter_device_rejects_mismatched_sdk_device_index(self):
        backend = FakeCanBackend([make_device("fake_can:first", "SERIAL-A", ["CAN0"], sdk_device_index=0)])

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            with self.assertRaises(CanAdapterError) as context:
                resolve_can_adapter_device("fake_can:first", protocol="canfd", expected_sdk_device_index=1)

        self.assertEqual(context.exception.code, "adapter_mismatch")
        self.assertIn("device_index", context.exception.message)

    def test_resolve_can_adapter_device_requires_rescan_when_runtime_sdk_device_index_missing(self):
        backend = FakeCanBackend([make_device("fake_can:first", "SERIAL-A", ["CAN0"], sdk_device_index=None)])

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            with self.assertRaises(CanAdapterError) as context:
                resolve_can_adapter_device("fake_can:first", protocol="canfd", expected_sdk_device_index=0)

        self.assertEqual(context.exception.code, "adapter_mismatch")
        self.assertIn("重新扫描", context.exception.message)

    def test_open_can_adapter_connection_uses_live_sdk_device_index_when_request_omits_it(self):
        backend = FakeCanBackend(
            [
                make_device(
                    "fake_can:first",
                    "SERIAL-A",
                    ["CAN0"],
                    sdk_device_index=7,
                    adapter_device="USB\\VID_3068&PID_0009\\SERIAL-A",
                )
            ]
        )

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            connection = open_can_adapter_connection(
                "canfd",
                {
                    "adapter_key": "fake_can:first",
                    "adapter_serial": "SERIAL-A",
                    "adapter_device": "USB\\VID_3068&PID_0009\\SERIAL-A",
                    "physical_channel": "CAN0",
                },
            )
            try:
                self.assertEqual(connection.device.sdk_device_index, 7)
                self.assertEqual(backend.open_device_indexes, [7])
            finally:
                can_adapters.close_can_adapter_connection(connection)

    def test_can_runtime_config_defaults_termination_disabled(self):
        config = protocol_tests._normalize_can_runtime_config("can", {"adapter_key": "", "physical_channel": "CAN0"})
        self.assertFalse(config["termination_enabled"])

    def test_canfd_runtime_config_defaults_match_zcanpro(self):
        config = protocol_tests._normalize_can_runtime_config("canfd", {"adapter_key": "", "physical_channel": "CAN0"})
        self.assertTrue(config["termination_enabled"])
        self.assertFalse(config["brs"])
        self.assertFalse(config["canfd_non_iso"])

    def test_duplicate_channel_connection_is_rejected(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            connection = open_can_adapter_connection("can", {"adapter_key": "fake_can:abc", "physical_channel": "CAN0"})
            try:
                with self.assertRaises(CanAdapterError) as context:
                    open_can_adapter_connection("can", {"adapter_key": "fake_can:abc", "physical_channel": "CAN0"})
            finally:
                can_adapters.close_can_adapter_connection(connection)

        self.assertEqual(context.exception.code, "channel_busy")
        self.assertEqual(backend.opened, ["fake_can:abc"])

    def test_send_rejects_length_overflow_for_classic_can(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 2,
                "expected_rx_id": "0x456",
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    protocol_tests._send_can_protocol_frame(
                        db=self.db,
                        session=session,
                        payload=protocol_tests.SendRequest(frame_id="0x123", dlc=2, data="01 02 03"),
                        protocol="can",
                        merged_config=json.loads(session.config_json),
                        payload_data="01 02 03",
                    )
                )

        self.assertIn("不能超过配置的数据长度", context.exception.detail)
        self.assertEqual(backend.transmitted_frames, [])

    def test_remote_frame_uses_configured_dlc_without_payload(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "remote_frame": True,
                "data_length": 8,
                "expected_rx_id": "0x456",
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        def inject_rx() -> None:
            time.sleep(0.05)
            backend.enqueue_receive_batch(make_frame(frame_id=0x456, is_remote_frame=True, declared_data_length=8, channel_name="CAN0"))

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            injector = threading.Thread(target=inject_rx, daemon=True)
            injector.start()
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x321", dlc=8, data=None),
                    protocol="can",
                    merged_config=json.loads(session.config_json),
                    payload_data=None,
                )
            )
            injector.join(timeout=0.5)

        self.assertEqual(result["message"], "CAN协议验证通过")
        self.assertEqual(len(backend.transmitted_frames), 1)
        self.assertEqual(backend.transmitted_frames[0].declared_data_length, 8)
        self.assertEqual(backend.transmitted_frames[0].data, b"")

    def test_receive_count_out_of_range_is_rejected(self):
        class OverflowApi:
            def ZCAN_ReceiveFD(self, raw_handle, buffer, size, timeout_ms):
                del raw_handle, buffer, size, timeout_ms
                return 129

        backend = can_adapters.Usbcanfd200UBackend()
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=make_device("fake_can:abc", "ABC123", ["CAN0"]),
            device_handle=None,
            channel_handle=can_adapters._UsbcanfdChannelHandle(
                api=OverflowApi(),
                raw_handle=0x2000,
                channel_index=0,
                channel_name="CAN0",
                protocol="canfd",
            ),
            channel_name="CAN0",
            protocol="canfd",
        )

        with self.assertRaises(CanAdapterError) as context:
            backend.receive(connection, timeout_ms=10)

        self.assertEqual(context.exception.code, "rx_count_out_of_range")

    def test_usbcanfd_backend_uses_classic_transmit_and_receive_interfaces_for_can(self):
        class ClassicApi:
            def __init__(self):
                self.transmit_calls: list[int] = []
                self.receive_calls: list[int] = []

            def ZCAN_Transmit(self, raw_handle, payload_ptr, count):
                self.transmit_calls.append(raw_handle)
                payload = payload_ptr._obj
                self.last_can_id = int(payload.frame.can_id)
                self.last_dlc = int(payload.frame.can_dlc)
                self.last_data = bytes(payload.frame.data[: payload.frame.can_dlc])
                return count

            def ZCAN_TransmitFD(self, raw_handle, payload_ptr, count):
                raise AssertionError("classic CAN path must not use ZCAN_TransmitFD")

            def ZCAN_Receive(self, raw_handle, buffer, size, timeout_ms):
                del size, timeout_ms
                self.receive_calls.append(raw_handle)
                buffer[0].frame.can_id = can_adapters.CAN_EFF_FLAG | 0x1ABCDE
                buffer[0].frame.can_dlc = 2
                buffer[0].frame.data[0] = 0xAA
                buffer[0].frame.data[1] = 0xBB
                buffer[0].timestamp = 123000
                return 1

            def ZCAN_ReceiveFD(self, raw_handle, buffer, size, timeout_ms):
                raise AssertionError("classic CAN path must not use ZCAN_ReceiveFD")

        backend = can_adapters.Usbcanfd200UBackend()
        api = ClassicApi()
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=make_device("fake_can:abc", "ABC123", ["CAN0"]),
            device_handle=None,
            channel_handle=can_adapters._UsbcanfdChannelHandle(
                api=api,
                raw_handle=0x2000,
                channel_index=0,
                channel_name="CAN0",
                protocol="can",
            ),
            channel_name="CAN0",
            protocol="can",
        )

        backend.transmit(
            connection,
            make_frame(frame_id=0x123, is_extended_id=False, is_fd=False, data=b"\x01\x02", channel_name="CAN0"),
        )
        frames = backend.receive(connection, timeout_ms=10)

        self.assertEqual(api.transmit_calls, [0x2000])
        self.assertEqual(api.receive_calls, [0x2000])
        self.assertEqual(api.last_dlc, 2)
        self.assertEqual(api.last_data, b"\x01\x02")
        self.assertEqual(len(frames), 1)
        self.assertFalse(frames[0].is_fd)
        self.assertTrue(frames[0].is_extended_id)
        self.assertEqual(frames[0].frame_id, 0x1ABCDE)

    def test_usbcanfd_backend_transmit_rejects_protocol_frame_mismatch(self):
        backend = can_adapters.Usbcanfd200UBackend()

        class StrictApi:
            def ZCAN_Transmit(self, raw_handle, payload_ptr, count):
                raise AssertionError("unexpected ZCAN_Transmit call")

            def ZCAN_TransmitFD(self, raw_handle, payload_ptr, count):
                raise AssertionError("unexpected ZCAN_TransmitFD call")

        classic_connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=make_device("fake_can:abc", "ABC123", ["CAN0"]),
            device_handle=None,
            channel_handle=can_adapters._UsbcanfdChannelHandle(
                api=StrictApi(),
                raw_handle=0x2000,
                channel_index=0,
                channel_name="CAN0",
                protocol="can",
            ),
            channel_name="CAN0",
            protocol="can",
        )
        canfd_connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=make_device("fake_can:abc", "ABC123", ["CAN0"]),
            device_handle=None,
            channel_handle=can_adapters._UsbcanfdChannelHandle(
                api=StrictApi(),
                raw_handle=0x2001,
                channel_index=0,
                channel_name="CAN0",
                protocol="canfd",
            ),
            channel_name="CAN0",
            protocol="canfd",
        )

        with self.assertRaises(CanAdapterError) as classic_ctx:
            backend.transmit(
                classic_connection,
                make_frame(frame_id=0x123, is_fd=True, data=b"\x01\x02", declared_data_length=2, channel_name="CAN0"),
            )
        with self.assertRaises(CanAdapterError) as canfd_ctx:
            backend.transmit(
                canfd_connection,
                make_frame(frame_id=0x123, is_fd=False, data=b"\x01\x02", declared_data_length=2, channel_name="CAN0"),
            )

        self.assertEqual(classic_ctx.exception.code, "protocol_frame_mismatch")
        self.assertEqual(canfd_ctx.exception.code, "protocol_frame_mismatch")

    def test_match_expected_frame_rejects_extended_and_fd_cross_matches(self):
        standard_frame = make_frame(frame_id=0x123, is_extended_id=False, is_fd=False, data=b"\x01\x02")
        extended_frame = make_frame(frame_id=0x123, is_extended_id=True, is_fd=False, data=b"\x01\x02")
        fd_frame = make_frame(frame_id=0x123, is_extended_id=False, is_fd=True, data=b"\x01\x02")

        self.assertTrue(
            match_expected_rx_frame(
                standard_frame,
                expected_rx_id=0x123,
                expected_rx_mask=0x7FF,
                expected_data=b"\x01\x02",
                expected_is_extended_id=False,
                expected_is_fd=False,
            )
        )
        self.assertFalse(
            match_expected_rx_frame(
                extended_frame,
                expected_rx_id=0x123,
                expected_rx_mask=0x7FF,
                expected_data=b"\x01\x02",
                expected_is_extended_id=False,
                expected_is_fd=False,
            )
        )
        self.assertFalse(
            match_expected_rx_frame(
                fd_frame,
                expected_rx_id=0x123,
                expected_rx_mask=0x7FF,
                expected_data=b"\x01\x02",
                expected_is_extended_id=False,
                expected_is_fd=False,
            )
        )

    def test_can_listener_continuously_collects_rx_logs(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(protocol="canfd", config={"adapter_key": "fake_can:abc", "physical_channel": "CAN0", "data_type": "HEX"})
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="canfd",
        )
        backend.enqueue_receive_batch(
            make_frame(frame_id=0x123, is_fd=True, bitrate_switch=True, data=b"\x01\x02", channel_name="CAN0"),
            make_frame(frame_id=0x124, is_fd=True, data=b"\x03\x04", channel_name="CAN0"),
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            protocol_tests._store_can_session_connection(session.id, connection)
            time.sleep(0.2)

        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id, ProtocolLog.direction == "Rx").order_by(ProtocolLog.id.asc()).all()
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0].data, "01 02")
        self.assertEqual(logs[1].data, "03 04")
        self.db.expire_all()
        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).first()
        self.assertEqual(refreshed.rx_count, 2)

    def test_send_waits_for_listener_collected_real_rx_frame(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="canfd",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 12,
                "expected_rx_id": "0x456",
                "expected_data": "AA BB",
                "rx_timeout_ms": 500,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="canfd",
        )

        def inject_rx() -> None:
            time.sleep(0.05)
            backend.enqueue_receive_batch(make_frame(frame_id=0x456, is_fd=True, data=b"\xAA\xBB", channel_name="CAN0"))

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            injector = threading.Thread(target=inject_rx, daemon=True)
            injector.start()
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x123", dlc=12, data="01 02 03 04 05 06 07 08 09 0A 0B 0C"),
                    protocol="canfd",
                    merged_config=json.loads(session.config_json),
                    payload_data="01 02 03 04 05 06 07 08 09 0A 0B 0C",
                )
            )
            injector.join(timeout=0.5)
            deadline = time.time() + 0.5
            while time.time() < deadline:
                self.db.expire_all()
                current_logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).order_by(ProtocolLog.id.asc()).all()
                if any(item.direction == "Rx" for item in current_logs):
                    break
                time.sleep(0.02)

        self.assertIn("协议验证通过", result["message"])
        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).order_by(ProtocolLog.id.asc()).all()
        self.assertEqual(sum(1 for item in logs if item.direction == "Rx"), 1)
        self.assertTrue(any("收到真实回复帧" in str(item.data or "") for item in logs if item.direction == "System"))
        self.assertGreater(backend.receive_calls, 0)

    def test_close_can_session_connection_stops_listener_thread(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(protocol="can", config={"adapter_key": "fake_can:abc", "physical_channel": "CAN0"})
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        with patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True):
            protocol_tests._store_can_session_connection(session.id, connection)
            runtime = protocol_tests._get_can_session_runtime(session.id)
            self.assertIsNotNone(runtime)
            self.assertTrue(runtime.worker.is_alive())
            protocol_tests._close_can_session_connection(session.id)
            time.sleep(0.05)
            self.assertIsNone(protocol_tests._get_can_session_runtime(session.id))
            self.assertIn("channel:CAN0", backend.stopped)
            self.assertIn("device:fake_can:abc", backend.closed)

    def test_probe_can_adapters_filters_classic_can_and_canfd_devices(self):
        zqwl_devices = [
            {
                "backend_key": "zqwl_ucan_cdc",
                "adapter_key": "zqwl_ucan_cdc:COM8:953DAD95240A",
                "adapter_name": "ZQWL USB-CAN",
                "adapter_device": "COM8",
                "serial_number": "953DAD95240A",
                "vid": 13666,
                "pid": 259,
                "channels": [{"name": "CAN0", "index": 0}],
            }
        ]
        zlg_devices = [
            {
                "backend_key": "usbcanfd_200u",
                "adapter_key": "usbcanfd_200u:USB\\VID_3068&PID_0009\\ABC123",
                "adapter_name": "USBCANFD-200U",
                "serial_number": "ABC123",
                "pnp_device_id": "USB\\VID_3068&PID_0009\\ABC123",
                "sdk_device_index": 0,
                "channels": [{"name": "CAN0", "index": 0}, {"name": "CAN1", "index": 1}],
            }
        ]
        with patch(
            "backend.routers.protocol_tests.list_can_adapter_devices",
            side_effect=lambda protocol=None: [*zqwl_devices, *zlg_devices] if protocol == "can" else (zlg_devices if protocol == "canfd" else [*zqwl_devices, *zlg_devices]),
        ):
            self.assertEqual(protocol_tests._probe_can_adapters("can"), [*zqwl_devices, *zlg_devices])
            self.assertEqual(protocol_tests._probe_can_adapters("canfd"), zlg_devices)

    def test_merge_connect_request_config_rebinds_selected_adapter_from_current_scan(self):
        scanned_devices = [
            {
                "backend_key": "usbcanfd_200u",
                "adapter_key": "usbcanfd_200u:SERIAL-NEW",
                "adapter_name": "USBCANFD-200U",
                "serial_number": "SERIAL-NEW",
                "adapter_device": "USB\\VID_3068&PID_0009\\SERIAL-NEW",
                "sdk_device_index": 6,
                "vid": 0x3068,
                "pid": 0x0009,
                "channels": [{"name": "CAN0", "index": 0}, {"name": "CAN1", "index": 1}],
            }
        ]

        merged = protocol_tests._merge_connect_request_config(
            "canfd",
            {
                "detected_devices": scanned_devices,
                "adapter_options": [{"label": "USBCANFD-200U / SERIAL-NEW", "value": "usbcanfd_200u:SERIAL-NEW"}],
                "backend_key": "usbcanfd_200u",
                "adapter_key": "usbcanfd_200u:SERIAL-NEW",
                "adapter_device": "USB\\VID_3068&PID_0009\\SERIAL-NEW",
                "sdk_device_index": 6,
                "physical_channel": "CAN0",
                "physical_channel_options": ["CAN0", "CAN1"],
            },
            {
                "backend_key": "zqwl_ucan_cdc",
                "adapter_key": "usbcanfd_200u:SERIAL-NEW",
                "adapter_device": "COM7",
                "adapter_serial": "OLD",
                "sdk_device_index": 99,
                "detected_devices": [{"adapter_key": "stale"}],
                "physical_channel": "CAN1",
                "data_baud_rate": "4Mbps",
                "brs": False,
            },
        )

        self.assertEqual(merged["backend_key"], "usbcanfd_200u")
        self.assertEqual(merged["adapter_key"], "usbcanfd_200u:SERIAL-NEW")
        self.assertEqual(merged["adapter_device"], "USB\\VID_3068&PID_0009\\SERIAL-NEW")
        self.assertEqual(merged["sdk_device_index"], 6)
        self.assertEqual(merged["physical_channel"], "CAN1")
        self.assertEqual(merged["channel"], "CAN1")
        self.assertEqual(merged["physical_channel_options"], ["CAN0", "CAN1"])
        self.assertEqual(merged["detected_devices"], scanned_devices)
        self.assertEqual(merged["data_baud_rate"], "4Mbps")
        self.assertFalse(merged["brs"])

    def test_merge_connect_request_config_rejects_offline_selected_adapter(self):
        with self.assertRaises(HTTPException) as context:
            protocol_tests._merge_connect_request_config(
                "can",
                {
                    "detected_devices": [
                        {
                            "backend_key": "zqwl_ucan_cdc",
                            "adapter_key": "zqwl_ucan_cdc:COM8:LIVE",
                            "adapter_device": "COM8",
                            "channels": [{"name": "CAN0", "index": 0}],
                        }
                    ]
                },
                {
                    "adapter_key": "zqwl_ucan_cdc:COM7:OFFLINE",
                    "physical_channel": "CAN0",
                    "baud_rate": "500kbps",
                },
            )

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("已离线", str(context.exception.detail))

    def test_classic_can_send_pads_payload_before_transmit_and_requires_real_rx(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "expected_rx_id": "0x456",
                "rx_timeout_ms": 500,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        def inject_rx() -> None:
            time.sleep(0.05)
            backend.enqueue_receive_batch(make_frame(frame_id=0x456, data=b"\xAA\xBB", channel_name="CAN0"))

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            injector = threading.Thread(target=inject_rx, daemon=True)
            injector.start()
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02"),
                    protocol="can",
                    merged_config=json.loads(session.config_json),
                    payload_data="01 02",
                )
            )
            injector.join(timeout=0.5)

        self.assertEqual(result["message"], "CAN协议验证通过")
        self.assertEqual(len(backend.transmitted_frames), 1)
        self.assertEqual(backend.transmitted_frames[0].declared_data_length, 8)
        self.assertEqual(backend.transmitted_frames[0].data, b"\x01\x02\x00\x00\x00\x00\x00\x00")

    def test_classic_can_send_passes_without_matching_reply(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "expected_rx_id": "0x456",
                "rx_timeout_ms": 80,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02"),
                    protocol="can",
                    merged_config=json.loads(session.config_json),
                    payload_data="01 02",
                )
            )

        self.assertIn("按发送成功判定本次验证通过", result["message"])
        self.assertEqual(json.loads(session.config_json).get("validation_result"), "passed")
        self.assertEqual(json.loads(session.config_json).get("validation_code"), "can_tx_only_passed")

    def test_classic_can_send_without_validation_marks_session_passed_and_listener_continues_collecting_rx(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "rx_timeout_ms": 200,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        def inject_rx() -> None:
            time.sleep(0.05)
            backend.enqueue_receive_batch(make_frame(frame_id=0x456, data=b"\xAA\xBB", channel_name="CAN0"))

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            injector = threading.Thread(target=inject_rx, daemon=True)
            injector.start()
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02"),
                    protocol="can",
                    merged_config=json.loads(session.config_json),
                    payload_data="01 02",
                )
            )
            injector.join(timeout=0.5)
            deadline = time.time() + 0.5
            while time.time() < deadline:
                self.db.expire_all()
                current_logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).order_by(ProtocolLog.id.asc()).all()
                if any(item.direction == "Rx" for item in current_logs):
                    break
                time.sleep(0.02)

        self.assertEqual(result["message"], "CAN 帧发送成功，未配置接收验证条件，按发送成功判定本次验证通过")
        logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).order_by(ProtocolLog.id.asc()).all()
        self.assertTrue(any(item.direction == "Tx" for item in logs))
        self.assertTrue(any(item.direction == "Rx" for item in logs))
        self.assertTrue(any("未配置接收验证条件" in str(item.data or "") for item in logs if item.direction == "System"))
        self.db.refresh(session)
        session_config = json.loads(session.config_json)
        self.assertEqual(session_config.get("validation_result"), "passed")
        self.assertEqual(session_config.get("validation_code"), "can_tx_only_passed")
        self.assertFalse(session_config.get("reply_frame_received"))
        self.assertEqual(session.tx_count, 1)
        self.assertGreaterEqual(session.rx_count, 1)

    def test_classic_can_validation_does_not_force_reply_id_format_to_match_sent_frame(self):
        backend = FakeCanBackend([make_device("fake_can:abc", "ABC123", ["CAN0"])])
        session = self._create_session(
            protocol="can",
            config={
                "adapter_key": "fake_can:abc",
                "physical_channel": "CAN0",
                "id_format": "标准帧(11位)",
                "data_length": 8,
                "expected_rx_id": "0x456",
                "expected_data": "AA BB",
                "rx_timeout_ms": 300,
                "data_type": "HEX",
            },
        )
        connection = CanAdapterConnection(
            backend_key="fake_can",
            device=backend.enumerate_devices()[0],
            device_handle="device:fake_can:abc",
            channel_handle="channel:CAN0",
            channel_name="CAN0",
            protocol="can",
        )

        def inject_rx() -> None:
            time.sleep(0.05)
            backend.enqueue_receive_batch(make_frame(frame_id=0x456, is_extended_id=True, data=b"\xAA\xBB", channel_name="CAN0"))

        with ExitStack() as stack:
            stack.enter_context(patch.dict(CAN_ADAPTER_BACKENDS, {"fake_can": backend}, clear=True))
            stack.enter_context(patch("backend.routers.protocol_tests.SessionLocal", self.Session))
            stack.enter_context(patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None))
            protocol_tests._store_can_session_connection(session.id, connection)
            injector = threading.Thread(target=inject_rx, daemon=True)
            injector.start()
            result = asyncio.run(
                protocol_tests._send_can_protocol_frame(
                    db=self.db,
                    session=session,
                    payload=protocol_tests.SendRequest(frame_id="0x123", dlc=8, data="01 02"),
                    protocol="can",
                    merged_config=json.loads(session.config_json),
                    payload_data="01 02",
                )
            )
            injector.join(timeout=0.5)

        self.assertEqual(result["message"], "CAN协议验证通过")


if __name__ == "__main__":
    unittest.main()
