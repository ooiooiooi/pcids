import asyncio
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, ProtocolLog, ProtocolSession
from backend.routers import protocol_tests


class EthernetProtocolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._db_temp_dir = tempfile.TemporaryDirectory()
        self._engine = create_engine(
            f"sqlite:///{Path(self._db_temp_dir.name) / 'ethernet-test.db'}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self._engine)
        self.Session = sessionmaker(bind=self._engine, expire_on_commit=False)
        self.db = self.Session()
        self._session_local_patch = patch("backend.routers.protocol_tests.SessionLocal", self.Session)
        self._notify_patch = patch("backend.routers.protocol_tests._notify_protocol_result", return_value=None)
        self._session_local_patch.start()
        self._notify_patch.start()

    def tearDown(self) -> None:
        protocol_tests._close_all_ethernet_session_runtimes()
        self._notify_patch.stop()
        self._session_local_patch.stop()
        self.db.close()
        self._engine.dispose()
        self._db_temp_dir.cleanup()

    def _create_session(self, config: dict[str, object]) -> ProtocolSession:
        session = ProtocolSession(
            task_no="202608100001",
            target="Ethernet Board",
            protocol="ethernet",
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

    def _wait_for(self, predicate, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.db.expire_all()
            if predicate():
                return
            time.sleep(0.02)
        self.fail("condition was not satisfied before timeout")

    @staticmethod
    def _free_port(socket_type: int = socket.SOCK_STREAM) -> int:
        with socket.socket(socket.AF_INET, socket_type) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def test_tcp_client_connects_once_and_reuses_persistent_socket(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.2)
        port = int(listener.getsockname()[1])
        stop_event = threading.Event()
        accepted_count = []
        received = bytearray()

        def echo_worker() -> None:
            connection = None
            try:
                while not stop_event.is_set() and connection is None:
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                if connection is None:
                    return
                accepted_count.append(1)
                connection.settimeout(0.2)
                while not stop_event.is_set():
                    try:
                        payload = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not payload:
                        break
                    received.extend(payload)
                    connection.sendall(b"ACK:" + payload)
            finally:
                if connection is not None:
                    connection.close()

        worker = threading.Thread(target=echo_worker, daemon=True)
        worker.start()
        runtime = None
        session = None
        try:
            runtime, config, _ = protocol_tests._open_ethernet_channel(
                {
                    "transport_protocol": "TCP Client",
                    "target_ip": "127.0.0.1",
                    "target_port": port,
                    "timeout": 2000,
                    "data_type": "ASCII",
                }
            )
            session = self._create_session(config)
            protocol_tests._store_ethernet_session_runtime(session.id, runtime)

            first = asyncio.run(
                protocol_tests.send_frame(
                    session.id,
                    protocol_tests.SendRequest(data="ONE", config={"data_type": "ASCII"}),
                    db=self.db,
                    current_user=None,
                    _=None,
                )
            )
            second = asyncio.run(
                protocol_tests.send_frame(
                    session.id,
                    protocol_tests.SendRequest(data="TWO", config={"data_type": "ASCII"}),
                    db=self.db,
                    current_user=None,
                    _=None,
                )
            )
            self._wait_for(
                lambda: self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().rx_count >= 1
            )

            logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).all()
            self.assertEqual(len(accepted_count), 1)
            self.assertIn(b"ONE", bytes(received))
            self.assertIn(b"TWO", bytes(received))
            self.assertEqual(len([item for item in logs if item.direction == "Tx"]), 2)
            self.assertGreaterEqual(len([item for item in logs if item.direction == "Rx"]), 1)
            tx_payload = protocol_tests.log_to_dict(next(item for item in logs if item.direction == "Tx"))
            self.assertTrue(tx_payload["src_addr"].startswith("127.0.0.1:"))
            self.assertEqual(tx_payload["dst_addr"], f"127.0.0.1:{port}")
            self.assertIn("持久连接", first["message"])
            self.assertIn("持久连接", second["message"])
        finally:
            if session is not None:
                protocol_tests._close_ethernet_session_runtime(session.id)
            elif runtime is not None:
                protocol_tests._shutdown_ethernet_runtime(runtime)
            stop_event.set()
            listener.close()
            worker.join(timeout=1.0)

    def test_tcp_server_listens_at_connect_and_reaccepts_a_second_client(self):
        port = self._free_port()
        runtime, config, _ = protocol_tests._open_ethernet_channel(
            {
                "transport_protocol": "TCP Server",
                "local_ip": "127.0.0.1",
                "listen_port": port,
                "timeout": 2000,
                "data_type": "ASCII",
            }
        )
        session = self._create_session(config)
        protocol_tests._store_ethernet_session_runtime(session.id, runtime)

        client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        client.settimeout(2.0)
        second_client = None
        try:
            self._wait_for(
                lambda: bool(
                    json.loads(
                        self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().config_json
                    ).get("peer_connected")
                )
            )
            client.sendall(b"FROM-CLIENT")
            self._wait_for(
                lambda: self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().rx_count == 1
            )

            response = asyncio.run(
                protocol_tests.send_frame(
                    session.id,
                    protocol_tests.SendRequest(data="FROM-SERVER", config={"data_type": "ASCII"}),
                    db=self.db,
                    current_user=None,
                    _=None,
                )
            )

            self.assertEqual(client.recv(65536), b"FROM-SERVER")
            self.assertIn("当前客户端连接", response["message"])
            logs = self.db.query(ProtocolLog).filter(ProtocolLog.session_id == session.id).all()
            self.assertEqual([item.direction for item in logs if item.direction in {"Tx", "Rx"}], ["Rx", "Tx"])

            client.close()
            self._wait_for(
                lambda: not bool(
                    json.loads(
                        self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().config_json
                    ).get("peer_connected")
                )
            )
            second_client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            second_client.settimeout(2.0)
            self._wait_for(
                lambda: bool(
                    json.loads(
                        self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().config_json
                    ).get("peer_connected")
                )
            )
            second_client.sendall(b"SECOND-CLIENT")
            self._wait_for(
                lambda: self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().rx_count == 2
            )
            asyncio.run(
                protocol_tests.send_frame(
                    session.id,
                    protocol_tests.SendRequest(data="TO-SECOND", config={"data_type": "ASCII"}),
                    db=self.db,
                    current_user=None,
                    _=None,
                )
            )
            self.assertEqual(second_client.recv(65536), b"TO-SECOND")
        finally:
            client.close()
            if second_client is not None:
                second_client.close()
            asyncio.run(protocol_tests.disconnect_device(session.id, db=self.db, current_user=None, _=None))

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
            rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            rebound.bind(("127.0.0.1", port))

    def test_udp_binds_at_connect_and_receives_independently_of_send(self):
        runtime_port = self._free_port(socket.SOCK_DGRAM)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(("127.0.0.1", 0))
        peer.settimeout(2.0)
        peer_port = int(peer.getsockname()[1])
        runtime, config, _ = protocol_tests._open_ethernet_channel(
            {
                "transport_protocol": "UDP",
                "local_ip": "127.0.0.1",
                "local_port": runtime_port,
                "target_ip": "127.0.0.1",
                "target_port": peer_port,
                "timeout": 2000,
                "data_type": "ASCII",
            }
        )
        session = self._create_session(config)
        protocol_tests._store_ethernet_session_runtime(session.id, runtime)
        try:
            peer.sendto(b"UDP-IN", ("127.0.0.1", runtime_port))
            self._wait_for(
                lambda: self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one().rx_count == 1
            )
            result = asyncio.run(
                protocol_tests.send_frame(
                    session.id,
                    protocol_tests.SendRequest(data="UDP-OUT", config={"data_type": "ASCII"}),
                    db=self.db,
                    current_user=None,
                    _=None,
                )
            )

            self.assertEqual(peer.recvfrom(65536)[0], b"UDP-OUT")
            self.assertIn("不代表对端已经接收", result["message"])
        finally:
            peer.close()
            asyncio.run(protocol_tests.disconnect_device(session.id, db=self.db, current_user=None, _=None))

    def test_missing_runtime_fails_without_creating_tx_log(self):
        session = self._create_session(
            {
                "transport_protocol": "TCP Client",
                "target_ip": "127.0.0.1",
                "target_port": 65534,
                "timeout": 1000,
                "data_type": "ASCII",
                "channel_state": "connected",
            }
        )

        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                protocol_tests.send_frame(
                    session.id,
                    protocol_tests.SendRequest(data="MUST-NOT-BE-TX", config={"data_type": "ASCII"}),
                    db=self.db,
                    current_user=None,
                    _=None,
                )
            )

        self.assertIn("真实通道已失效", context.exception.detail)
        self.db.expire_all()
        refreshed = self.db.query(ProtocolSession).filter(ProtocolSession.id == session.id).one()
        self.assertEqual(refreshed.tx_count, 0)
        self.assertEqual(refreshed.status, 2)
        self.assertEqual(
            self.db.query(ProtocolLog)
            .filter(ProtocolLog.session_id == session.id, ProtocolLog.direction == "Tx")
            .count(),
            0,
        )

    def test_rejects_invalid_target_timeout_and_udp_loopback_endpoint(self):
        invalid_configs = [
            {
                "transport_protocol": "TCP Client",
                "target_ip": "0.0.0.0",
                "target_port": 8080,
                "timeout": 3000,
            },
            {
                "transport_protocol": "TCP Client",
                "target_ip": "127.0.0.1",
                "target_port": 8080,
                "timeout": 20,
            },
            {
                "transport_protocol": "UDP",
                "local_ip": "127.0.0.1",
                "local_port": 8080,
                "target_ip": "127.0.0.1",
                "target_port": 8080,
                "timeout": 3000,
            },
        ]
        for config in invalid_configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                protocol_tests._validate_ethernet_connection_config(config)


if __name__ == "__main__":
    unittest.main()
