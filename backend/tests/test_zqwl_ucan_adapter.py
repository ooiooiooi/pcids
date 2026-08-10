from __future__ import annotations

import types
import unittest
from unittest.mock import patch

from backend.utils import can_adapters
from backend.utils.can_adapters import (
    CanAdapterConnection,
    CanAdapterError,
    CanFrame,
    ZqwlUcanCdcBackend,
    _ZqwlChannelHandle,
    close_can_adapter_connection,
    open_can_adapter_connection,
)


def make_command_response(function_code: int, rw_flag: int, data: bytes = b"") -> bytes:
    payload = bytes(data or b"").ljust(16, b"\x00")[:16]
    return b"\x49\x3B" + bytes((function_code & 0xFF, rw_flag & 0xFF)) + payload + b"\x45\x2E"


def make_can_frame(
    *,
    channel_index: int = 0,
    frame_id: int,
    data: bytes = b"",
    is_extended: bool = False,
    is_fd: bool = False,
    bitrate_switch: bool = False,
    is_remote: bool = False,
    declared_length: int | None = None,
) -> bytes:
    dlc = len(data) if declared_length is None else int(declared_length)
    byte1 = ((channel_index & 0x01) << 7) | (dlc & 0x7F)
    byte2 = (((channel_index >> 1) & 0x03) << 3) | (0x04 if is_extended else 0) | (0x02 if is_remote else 0) | (0x01 if bitrate_switch else 0)
    payload = b"" if is_remote else bytes(data)
    raw_can_id = int(frame_id) | (0x80000000 if is_fd else 0)
    return bytes((0x5A, byte1, byte2)) + raw_can_id.to_bytes(4, byteorder="big") + payload + b"\xA5"


class FakePortInfo:
    def __init__(self, *, device: str, vid: int, pid: int, serial_number: str, description: str = "ZQWL USB-CAN"):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.serial_number = serial_number
        self.description = description
        self.product = description
        self.manufacturer = "ZQWL"
        self.hwid = f"USB VID:PID={vid:04X}:{pid:04X} SER={serial_number}"


class FakeSerialConnection:
    def __init__(self, read_chunks=None, command_responses=None, write_results=None, **kwargs):
        self.kwargs = kwargs
        self.read_chunks = [bytes(item) for item in (read_chunks or [])]
        self.command_responses = [bytes(item) for item in (command_responses or [])]
        self.write_results = list(write_results or [])
        self.writes: list[bytes] = []
        self.is_open = True
        self.flush_count = 0

    @property
    def in_waiting(self) -> int:
        return len(self.read_chunks[0]) if self.read_chunks else 0

    def write(self, payload: bytes) -> int:
        self.writes.append(bytes(payload))
        if self.command_responses:
            self.read_chunks.append(self.command_responses.pop(0))
        if self.write_results:
            return self.write_results.pop(0)
        return len(payload)

    def read(self, size: int = 1) -> bytes:
        if not self.read_chunks:
            return b""
        chunk = self.read_chunks[0]
        if size >= len(chunk):
            self.read_chunks.pop(0)
            return chunk
        self.read_chunks[0] = chunk[size:]
        return chunk[:size]

    def flush(self) -> None:
        self.flush_count += 1
        return None

    def reset_input_buffer(self) -> None:
        self.read_chunks.clear()

    def reset_output_buffer(self) -> None:
        return None

    def close(self) -> None:
        self.is_open = False


class ZqwlUcanAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        can_adapters._ACTIVE_CAN_CHANNELS.clear()

    def test_enumerate_devices_only_accepts_supported_vid_pid(self):
        backend = ZqwlUcanCdcBackend()
        ports = [
            FakePortInfo(device="COM8", vid=0x3562, pid=0x0103, serial_number="953DAD95240A"),
            FakePortInfo(device="COM9", vid=0x3562, pid=0x0104, serial_number="ABCDEF123456"),
            FakePortInfo(device="COM10", vid=0x3068, pid=0x0009, serial_number="SHOULD_SKIP"),
            FakePortInfo(device="COM11", vid=0x3562, pid=0x9999, serial_number="SHOULD_SKIP"),
        ]
        with patch("backend.utils.can_adapters.list_ports", types.SimpleNamespace(comports=lambda: ports)):
            devices = backend.enumerate_devices()

        self.assertEqual([item.adapter_device for item in devices], ["COM8", "COM9"])
        self.assertEqual([item.backend_key for item in devices], ["zqwl_ucan_cdc", "zqwl_ucan_cdc"])

    def test_pid_0103_only_exposes_can0(self):
        backend = ZqwlUcanCdcBackend()
        with patch(
            "backend.utils.can_adapters.list_ports",
            types.SimpleNamespace(comports=lambda: [FakePortInfo(device="COM8", vid=0x3562, pid=0x0103, serial_number="953DAD95240A")]),
        ):
            devices = backend.enumerate_devices()

        self.assertEqual(len(devices), 1)
        self.assertEqual([item.name for item in devices[0].channels], ["CAN0"])

    def test_init_channel_sends_500kbps_and_can0_enable_commands(self):
        backend = ZqwlUcanCdcBackend()
        connection = FakeSerialConnection(
            command_responses=[
                make_command_response(0x40, 0x52, b"DEVICEINFO"),
                make_command_response(0x41, 0x52, b"953DAD95240A"),
                make_command_response(0x42, 0x57),
                make_command_response(0x44, 0x57),
            ]
        )
        serial_module = types.SimpleNamespace(
            Serial=lambda **kwargs: connection,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        device = can_adapters.CanAdapterDevice(
            adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
            backend_key=backend.backend_key,
            adapter_name=backend.adapter_name,
            serial_number="953DAD95240A",
            pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
            hardware_ids=["USB\\VID_3562&PID_0103"],
            description="ZQWL USB-CAN",
            channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
            adapter_device="COM8",
            vid=0x3562,
            pid=0x0103,
        )

        with patch("backend.utils.can_adapters.serial", serial_module):
            handle = backend.open_device(device)
            backend.init_channel(handle, "CAN0", protocol="can", config={"baud_rate": "500kbps"})

        self.assertEqual(connection.writes[2], make_command_response(0x42, 0x57, bytes((0x00, 0x00, 0x20))))
        self.assertEqual(connection.writes[3], make_command_response(0x44, 0x57, bytes((0x00, 0x00, 0x01))))

    def test_init_channel_accepts_binary_serial_payload_and_keeps_scanned_serial(self):
        backend = ZqwlUcanCdcBackend()
        connection = FakeSerialConnection(
            command_responses=[
                make_command_response(0x40, 0x52, b"UCAN-101K\x00\x00\x00V2.5"),
                make_command_response(0x41, 0x52, bytes.fromhex("06 50 F5 18 A1 11 AD BD")),
                make_command_response(0x42, 0x57),
                make_command_response(0x44, 0x57),
            ]
        )
        serial_module = types.SimpleNamespace(
            Serial=lambda **kwargs: connection,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        device = can_adapters.CanAdapterDevice(
            adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
            backend_key=backend.backend_key,
            adapter_name=backend.adapter_name,
            serial_number="953DAD95240A",
            pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
            hardware_ids=["USB\\VID_3562&PID_0103"],
            description="ZQWL USB-CAN",
            channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
            adapter_device="COM8",
            vid=0x3562,
            pid=0x0103,
        )

        with patch("backend.utils.can_adapters.serial", serial_module):
            handle = backend.open_device(device)
            backend.init_channel(handle, "CAN0", protocol="can", config={"baud_rate": "500kbps"})

        self.assertEqual(handle.serial_number, "953DAD95240A")
        self.assertEqual(handle.serial_response_payload, bytes.fromhex("06 50 F5 18 A1 11 AD BD 00 00 00 00 00 00 00 00"))

    def test_transmit_encodes_standard_extended_and_remote_frames(self):
        backend = ZqwlUcanCdcBackend()
        serial_connection = FakeSerialConnection()
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=can_adapters.CanAdapterDevice(
                adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                backend_key=backend.backend_key,
                adapter_name=backend.adapter_name,
                serial_number="953DAD95240A",
                pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                hardware_ids=["USB\\VID_3562&PID_0103"],
                description="ZQWL USB-CAN",
                channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                adapter_device="COM8",
            ),
            device_handle=types.SimpleNamespace(connection=serial_connection),
            channel_handle=None,
            channel_name="CAN0",
            protocol="can",
        )

        backend.transmit(connection, CanFrame(frame_id=0x123, is_extended_id=False, is_fd=False, bitrate_switch=False, is_remote_frame=False, data=b"\x01\x02", declared_data_length=2, channel_name="CAN0"))
        backend.transmit(connection, CanFrame(frame_id=0x1ABCDE, is_extended_id=True, is_fd=False, bitrate_switch=False, is_remote_frame=False, data=b"\xAA", declared_data_length=1, channel_name="CAN0"))
        backend.transmit(connection, CanFrame(frame_id=0x321, is_extended_id=False, is_fd=False, bitrate_switch=False, is_remote_frame=True, data=b"", declared_data_length=8, channel_name="CAN0"))

        self.assertEqual(serial_connection.writes[0], make_can_frame(frame_id=0x123, data=b"\x01\x02"))
        self.assertEqual(serial_connection.writes[1], make_can_frame(frame_id=0x1ABCDE, data=b"\xAA", is_extended=True))
        self.assertEqual(serial_connection.writes[2], make_can_frame(frame_id=0x321, data=b"", is_remote=True, declared_length=8))

    def test_transmit_requires_full_write_before_flush(self):
        backend = ZqwlUcanCdcBackend()
        serial_connection = FakeSerialConnection(write_results=[10])
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=can_adapters.CanAdapterDevice(
                adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                backend_key=backend.backend_key,
                adapter_name=backend.adapter_name,
                serial_number="953DAD95240A",
                pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                hardware_ids=["USB\\VID_3562&PID_0103"],
                description="ZQWL USB-CAN",
                channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                adapter_device="COM8",
            ),
            device_handle=types.SimpleNamespace(connection=serial_connection),
            channel_handle=None,
            channel_name="CAN0",
            protocol="can",
        )

        backend.transmit(connection, CanFrame(frame_id=0x123, is_extended_id=False, is_fd=False, bitrate_switch=False, is_remote_frame=False, data=b"\x01\x02", declared_data_length=2, channel_name="CAN0"))

        self.assertEqual(serial_connection.flush_count, 1)

    def test_transmit_rejects_zero_partial_or_none_write_results(self):
        backend = ZqwlUcanCdcBackend()
        for write_result in (0, 3, None):
            serial_connection = FakeSerialConnection(write_results=[write_result])
            connection = CanAdapterConnection(
                backend_key=backend.backend_key,
                device=can_adapters.CanAdapterDevice(
                    adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                    backend_key=backend.backend_key,
                    adapter_name=backend.adapter_name,
                    serial_number="953DAD95240A",
                    pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                    hardware_ids=["USB\\VID_3562&PID_0103"],
                    description="ZQWL USB-CAN",
                    channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                    adapter_device="COM8",
                ),
                device_handle=types.SimpleNamespace(connection=serial_connection),
                channel_handle=None,
                channel_name="CAN0",
                protocol="can",
            )

            with self.assertRaises(CanAdapterError) as context:
                backend.transmit(connection, CanFrame(frame_id=0x123, is_extended_id=False, is_fd=False, bitrate_switch=False, is_remote_frame=False, data=b"\x01\x02", declared_data_length=2, channel_name="CAN0"))

            self.assertIn("发送不完整", str(context.exception))
            self.assertEqual(serial_connection.flush_count, 0)

    def test_receive_handles_fragmented_and_combined_frames_and_heartbeats(self):
        backend = ZqwlUcanCdcBackend()
        frame_a = make_can_frame(frame_id=0x321, data=b"\x11\x22")
        frame_b = make_can_frame(frame_id=0x1ABCDE, data=b"\xAA", is_extended=True)
        heartbeat_short = b"\x5A\xFF" + (b"\x00" * 15)
        heartbeat_long = b"\x5A\xFE" + (b"\x00" * 30)
        serial_connection = FakeSerialConnection(
            read_chunks=[
                heartbeat_short + frame_a[:5],
                frame_a[5:] + heartbeat_long + frame_b,
            ]
        )
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=can_adapters.CanAdapterDevice(
                adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                backend_key=backend.backend_key,
                adapter_name=backend.adapter_name,
                serial_number="953DAD95240A",
                pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                hardware_ids=["USB\\VID_3562&PID_0103"],
                description="ZQWL USB-CAN",
                channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                adapter_device="COM8",
            ),
            device_handle=types.SimpleNamespace(connection=serial_connection),
            channel_handle=_ZqwlChannelHandle(channel_index=0, channel_name="CAN0", protocol="can"),
            channel_name="CAN0",
            protocol="can",
        )

        frames = backend.receive(connection, timeout_ms=100)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].frame_id, 0x321)
        self.assertEqual(frames[0].data, b"\x11\x22")
        self.assertEqual(frames[1].frame_id, 0x1ABCDE)
        self.assertTrue(frames[1].is_extended_id)

    def test_receive_keeps_classical_can_when_only_brs_bit_is_set(self):
        backend = ZqwlUcanCdcBackend()
        serial_connection = FakeSerialConnection(
            read_chunks=[make_can_frame(frame_id=0x123, data=b"\x11\x22", bitrate_switch=True)]
        )
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=can_adapters.CanAdapterDevice(
                adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                backend_key=backend.backend_key,
                adapter_name=backend.adapter_name,
                serial_number="953DAD95240A",
                pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                hardware_ids=["USB\\VID_3562&PID_0103"],
                description="ZQWL USB-CAN",
                channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                adapter_device="COM8",
            ),
            device_handle=types.SimpleNamespace(connection=serial_connection),
            channel_handle=_ZqwlChannelHandle(channel_index=0, channel_name="CAN0", protocol="can"),
            channel_name="CAN0",
            protocol="can",
        )

        frames = backend.receive(connection, timeout_ms=100)
        self.assertEqual(len(frames), 1)
        self.assertFalse(frames[0].is_fd)
        self.assertEqual(frames[0].frame_id, 0x123)

    def test_receive_discards_can_fd_frames_for_classical_can_validation(self):
        backend = ZqwlUcanCdcBackend()
        serial_connection = FakeSerialConnection(
            read_chunks=[
                make_can_frame(frame_id=0x321, data=b"\x01\x02\x03\x04\x05\x06\x07\x08", is_fd=True, bitrate_switch=True, declared_length=8)
                + make_can_frame(frame_id=0x456, data=b"\xAA\xBB")
            ]
        )
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=can_adapters.CanAdapterDevice(
                adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                backend_key=backend.backend_key,
                adapter_name=backend.adapter_name,
                serial_number="953DAD95240A",
                pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                hardware_ids=["USB\\VID_3562&PID_0103"],
                description="ZQWL USB-CAN",
                channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                adapter_device="COM8",
            ),
            device_handle=types.SimpleNamespace(connection=serial_connection),
            channel_handle=_ZqwlChannelHandle(channel_index=0, channel_name="CAN0", protocol="can"),
            channel_name="CAN0",
            protocol="can",
        )

        frames = backend.receive(connection, timeout_ms=100)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_id, 0x456)
        self.assertFalse(frames[0].is_fd)

    def test_receive_skips_invalid_frame_without_stopping_following_frames(self):
        backend = ZqwlUcanCdcBackend()
        invalid_frame = bytearray(make_can_frame(frame_id=0x123, data=b"\x11\x22"))
        invalid_frame[-1] = 0x00
        serial_connection = FakeSerialConnection(
            read_chunks=[bytes(invalid_frame) + make_can_frame(frame_id=0x456, data=b"\xAA\xBB")]
        )
        connection = CanAdapterConnection(
            backend_key=backend.backend_key,
            device=can_adapters.CanAdapterDevice(
                adapter_key="zqwl_ucan_cdc:COM8:953DAD95240A",
                backend_key=backend.backend_key,
                adapter_name=backend.adapter_name,
                serial_number="953DAD95240A",
                pnp_device_id="USB\\VID_3562&PID_0103\\953DAD95240A",
                hardware_ids=["USB\\VID_3562&PID_0103"],
                description="ZQWL USB-CAN",
                channels=[can_adapters.CanChannelDescriptor(name="CAN0", index=0)],
                adapter_device="COM8",
            ),
            device_handle=types.SimpleNamespace(connection=serial_connection),
            channel_handle=_ZqwlChannelHandle(channel_index=0, channel_name="CAN0", protocol="can"),
            channel_name="CAN0",
            protocol="can",
        )

        frames = backend.receive(connection, timeout_ms=100)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].frame_id, 0x456)

    def test_same_com_port_allows_only_one_active_connection_and_releases_after_close(self):
        responses = [
            make_command_response(0x40, 0x52, b"DEVICEINFO"),
            make_command_response(0x41, 0x52, b"953DAD95240A"),
            make_command_response(0x42, 0x57),
            make_command_response(0x44, 0x57),
        ]
        created_connections: list[FakeSerialConnection] = []

        def serial_factory(**kwargs):
            connection = FakeSerialConnection(command_responses=list(responses), **kwargs)
            created_connections.append(connection)
            return connection

        serial_module = types.SimpleNamespace(
            Serial=serial_factory,
            EIGHTBITS=8,
            PARITY_NONE="N",
            STOPBITS_ONE=1,
        )
        ports = [FakePortInfo(device="COM8", vid=0x3562, pid=0x0102, serial_number="953DAD95240A")]
        config_can0 = {
            "adapter_key": "zqwl_ucan_cdc:COM8:953DAD95240A",
            "adapter_serial": "953DAD95240A",
            "adapter_device": "COM8",
            "physical_channel": "CAN0",
            "baud_rate": "500kbps",
        }
        config_can1 = {**config_can0, "physical_channel": "CAN1"}

        with patch("backend.utils.can_adapters.serial", serial_module), patch(
            "backend.utils.can_adapters.list_ports",
            types.SimpleNamespace(comports=lambda: ports),
        ):
            first = open_can_adapter_connection("can", config_can0)
            with self.assertRaises(CanAdapterError):
                open_can_adapter_connection("can", config_can1)
            close_can_adapter_connection(first)
            reopened = open_can_adapter_connection("can", config_can0)
            close_can_adapter_connection(reopened)

        self.assertEqual(len(created_connections), 2)
        self.assertFalse(created_connections[0].is_open)
        self.assertFalse(created_connections[1].is_open)


if __name__ == "__main__":
    unittest.main()
