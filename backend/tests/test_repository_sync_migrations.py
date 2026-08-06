import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from backend.models import RepositorySyncInstance, RepositorySyncReceipt, RepositorySyncState
from backend.utils import db as db_utils
from backend.utils.repository_sync_identity import (
    generate_codearts_repository_sync_uuid,
    generate_repository_sync_uuid,
)


class RepositorySyncIdentityTests(unittest.TestCase):
    def test_stable_codearts_identity_is_deterministic_and_prefers_display_path(self):
        first = generate_repository_sync_uuid(
            project_key=" proj-01 ",
            display_path=" /firmware/BOOT.bin ",
            download_uri="https://example.test/file?token=one",
            name=" BOOT.bin ",
        )
        second = generate_repository_sync_uuid(
            project_key="proj-01",
            display_path="/firmware/BOOT.bin",
            download_uri="https://example.test/file?token=two",
            name="BOOT.bin",
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)

    def test_models_expose_instance_receipt_hash_and_revision_uniqueness(self):
        self.assertIn("instance_uuid", RepositorySyncInstance.__table__.c)
        self.assertIn("request_hash", RepositorySyncReceipt.__table__.c)
        revision_index = next(
            index
            for index in RepositorySyncState.__table__.indexes
            if index.name == "uq_repository_sync_state_project_revision"
        )
        self.assertTrue(revision_index.unique)
        self.assertEqual(
            tuple(column.name for column in revision_index.columns),
            ("project_key", "revision"),
        )


class RepositorySyncMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "legacy.db"
        self.engine = create_engine(f"sqlite:///{self.db_path}")

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_legacy_repository_backfill_uses_shared_deterministic_identity(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE repositories (
                        id INTEGER PRIMARY KEY,
                        project_key VARCHAR(200),
                        sync_uuid VARCHAR(64),
                        name VARCHAR(200),
                        repo_id VARCHAR(100),
                        description TEXT,
                        display_path VARCHAR(500),
                        download_uri TEXT,
                        source_type VARCHAR(30),
                        remote_repo_id VARCHAR(100),
                        repo_detail_json TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE repository_sync_changes (
                        id INTEGER PRIMARY KEY,
                        repo_db_id INTEGER,
                        repo_sync_uuid VARCHAR(64),
                        payload_json TEXT,
                        payload_hash VARCHAR(64)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO repositories
                        (id, project_key, sync_uuid, name, repo_id, description, display_path,
                         download_uri, source_type, remote_repo_id, repo_detail_json)
                    VALUES
                        (1, 'proj-01', NULL, 'BOOT.bin', 'repo-a', NULL, '/firmware/BOOT.bin', 'https://old/one', 'codearts_sync', 'repo-a', '{}'),
                        (2, 'proj-01', NULL, 'BOOT.bin', 'repo-a', NULL, '/firmware/BOOT.bin', 'https://old/two', 'codearts_sync', 'repo-a', '{}'),
                        (3, 'proj-02', NULL, 'BOOT.bin', 'repo-b', '/firmware/BOOT.bin', NULL, 'https://old/three', 'codearts_sync', 'repo-b', '{"repository_mode":"private"}'),
                        (4, 'proj-01', ' keep-existing ', 'APP.bin', NULL, NULL, '/firmware/APP.bin', NULL, 'local_upload', NULL, NULL),
                        (5, 'proj-01', NULL, 'manual.bin', NULL, NULL, NULL, NULL, 'local_upload', NULL, NULL)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO repository_sync_changes
                        (id, repo_db_id, repo_sync_uuid, payload_json, payload_hash)
                    VALUES
                        (1, 1, NULL, '{"name":"BOOT.bin"}', 'stale'),
                        (2, 4, ' keep-existing ', '{"name":"APP.bin"}', 'stale')
                    """
                )
            )

        with patch.object(db_utils, "engine", self.engine):
            db_utils._backfill_repository_sync_identifiers()

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, sync_uuid FROM repositories ORDER BY id")
            ).mappings().all()
            changes = conn.execute(
                text(
                    "SELECT repo_db_id, repo_sync_uuid, payload_json, payload_hash "
                    "FROM repository_sync_changes ORDER BY id"
                )
            ).mappings().all()

        by_id = {int(row["id"]): str(row["sync_uuid"]) for row in rows}
        expected = generate_codearts_repository_sync_uuid(
            project_key="proj-01",
            remote_repo_id="repo-a",
            display_path="/firmware/BOOT.bin",
            name="BOOT.bin",
        )
        self.assertEqual(by_id[1], expected)
        self.assertNotEqual(by_id[2], by_id[1])
        self.assertEqual(
            by_id[3],
            generate_codearts_repository_sync_uuid(
                project_key="proj-02",
                remote_repo_id="repo-b",
                display_path="/firmware/BOOT.bin",
                name="BOOT.bin",
                repository_mode="private",
            ),
        )
        self.assertEqual(by_id[4], "keep-existing")
        self.assertTrue(by_id[5])
        self.assertEqual(str(changes[0]["repo_sync_uuid"]), by_id[1])
        self.assertEqual(json.loads(changes[0]["payload_json"])["sync_uuid"], by_id[1])
        self.assertIsNone(changes[0]["payload_hash"])
        self.assertEqual(str(changes[1]["repo_sync_uuid"]), "keep-existing")

    def test_revision_normalization_preserves_valid_values_and_advances_cursor(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE repository_sync_states (
                        id INTEGER PRIMARY KEY,
                        project_key VARCHAR(200) NOT NULL,
                        sync_uuid VARCHAR(64) NOT NULL,
                        revision INTEGER,
                        updated_at DATETIME
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE repository_sync_cursors (
                        id INTEGER PRIMARY KEY,
                        project_key VARCHAR(200) NOT NULL UNIQUE,
                        current_revision INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO repository_sync_states
                        (id, project_key, sync_uuid, revision)
                    VALUES
                        (1, 'proj-01', 'one', 5),
                        (2, 'proj-01', 'two', 5),
                        (3, 'proj-01', 'three', NULL),
                        (4, 'proj-01', 'four', 9),
                        (5, 'proj-02', 'five', 0),
                        (6, 'proj-02', 'six', 2)
                    """
                )
            )
            conn.execute(
                text(
                    "INSERT INTO repository_sync_cursors "
                    "(project_key, current_revision) VALUES ('proj-01', 10)"
                )
            )

        with patch.object(db_utils, "engine", self.engine):
            db_utils._normalize_repository_sync_state_revisions()

        with self.engine.begin() as conn:
            revisions = conn.execute(
                text(
                    "SELECT id, project_key, revision "
                    "FROM repository_sync_states ORDER BY id"
                )
            ).mappings().all()
            cursors = dict(
                conn.execute(
                    text(
                        "SELECT project_key, current_revision "
                        "FROM repository_sync_cursors"
                    )
                ).all()
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_test_project_revision "
                    "ON repository_sync_states (project_key, revision)"
                )
            )

        by_id = {int(row["id"]): int(row["revision"]) for row in revisions}
        self.assertEqual(by_id[1], 5)
        self.assertEqual(by_id[4], 9)
        self.assertGreater(by_id[2], 10)
        self.assertGreater(by_id[3], by_id[2])
        self.assertEqual(len({by_id[1], by_id[2], by_id[3], by_id[4]}), 4)
        self.assertTrue(all(value > 0 for value in by_id.values()))
        self.assertEqual(int(cursors["proj-01"]), max(by_id[index] for index in (1, 2, 3, 4)))
        self.assertEqual(int(cursors["proj-02"]), max(by_id[index] for index in (5, 6)))

    def test_database_instance_identity_is_stable_until_database_is_rebuilt(self):
        create_table_sql = text(
            """
            CREATE TABLE repository_sync_instances (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                instance_uuid VARCHAR(64) NOT NULL UNIQUE,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        with self.engine.begin() as conn:
            conn.execute(create_table_sql)

        with patch.object(db_utils, "engine", self.engine):
            first = db_utils._ensure_repository_sync_instance()
            repeated = db_utils._ensure_repository_sync_instance()
            self.assertEqual(first, repeated)

            with self.engine.begin() as conn:
                conn.execute(text("DROP TABLE repository_sync_instances"))
                conn.execute(create_table_sql)

            rebuilt = db_utils._ensure_repository_sync_instance()

        self.assertNotEqual(first, rebuilt)
        self.assertEqual(len(rebuilt), 32)


if __name__ == "__main__":
    unittest.main()
