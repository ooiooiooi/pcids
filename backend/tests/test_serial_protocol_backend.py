import threading
import unittest

from backend.routers import protocol_tests


class FakeSerialConnection:
    def __init__(self, *, is_open=True, written=None):
        self.is_open = is_open
        self.written = written
        self.open_calls = 0
        self.write_calls = []
        self.flush_calls = 0
        self.read_calls = 0
        self.reset_input_calls = 0

    def open(self):
        self.open_calls += 1
        self.is_open = True

    def write(self, payload):
        self.write_calls.append(bytes(payload))
        return len(payload) if self.written is None else self.written

    def flush(self):
        self.flush_calls += 1

    def read(self, _size):
        self.read_calls += 1
        return b"unexpected"

    def reset_input_buffer(self):
        self.reset_input_calls += 1


class SerialProtocolBackendTests(unittest.TestCase):
    def test_serial_send_only_writes_and_leaves_receive_bytes_to_listener(self):
        connection = FakeSerialConnection()

        written = protocol_tests._write_serial_payload(
            connection,
            b"m",
            io_lock=threading.Lock(),
        )

        self.assertEqual(written, 1)
        self.assertEqual(connection.write_calls, [b"m"])
        self.assertEqual(connection.flush_calls, 1)
        self.assertEqual(connection.read_calls, 0)
        self.assertEqual(connection.reset_input_calls, 0)

    def test_serial_send_reopens_connection_before_write(self):
        connection = FakeSerialConnection(is_open=False)

        protocol_tests._write_serial_payload(
            connection,
            b"q\r\n",
            io_lock=threading.Lock(),
        )

        self.assertEqual(connection.open_calls, 1)
        self.assertEqual(connection.write_calls, [b"q\r\n"])

    def test_serial_send_rejects_partial_write(self):
        connection = FakeSerialConnection(written=1)

        with self.assertRaisesRegex(OSError, "写入不完整"):
            protocol_tests._write_serial_payload(
                connection,
                b"mode",
                io_lock=threading.Lock(),
            )


if __name__ == "__main__":
    unittest.main()
