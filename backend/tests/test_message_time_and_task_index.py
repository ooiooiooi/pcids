import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from backend.models import Base
from backend.models.message import Message
from backend.models.repository import Repository
from backend.models.task import BurningTask
from backend.routers.messages import (
    MESSAGE_EVENT_TIME_BASIS_KEY,
    MESSAGE_TIME_BASIS_KEY,
    MESSAGE_TIME_BASIS_LOCAL,
    MESSAGE_TIME_BASIS_UTC,
    enrich_task_message_payloads,
    get_messages,
    resolve_message_local_datetime,
)
from backend.utils import db as db_utils
from backend.utils.notifications import create_structured_message


UTC_PLUS_EIGHT = timezone(timedelta(hours=8))


class MessageTimeTests(unittest.TestCase):
    def test_utc_naive_message_is_converted_to_local_once(self):
        item = Message(created_at=datetime(2026, 7, 30, 1, 2, 3))
        payload = {MESSAGE_TIME_BASIS_KEY: MESSAGE_TIME_BASIS_UTC}

        with patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT):
            local_value = resolve_message_local_datetime(item, payload)

        self.assertEqual(local_value, datetime(2026, 7, 30, 9, 2, 3))

    def test_legacy_structured_local_message_is_not_shifted_again(self):
        item = Message(created_at=datetime(2026, 7, 30, 9, 2, 3))
        payload = {
            "category": "protocol",
            "status": "success",
            "primary_text": "completed",
        }

        with patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT):
            local_value = resolve_message_local_datetime(item, payload)

        self.assertEqual(local_value, datetime(2026, 7, 30, 9, 2, 3))

    def test_local_event_time_override_is_not_shifted_again(self):
        item = Message(created_at=datetime(2026, 7, 30, 1, 2, 3))
        payload = {
            "event_time": "2026-07-30T09:10:11",
            MESSAGE_EVENT_TIME_BASIS_KEY: MESSAGE_TIME_BASIS_LOCAL,
        }

        with patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT):
            local_value = resolve_message_local_datetime(item, payload)

        self.assertEqual(local_value, datetime(2026, 7, 30, 9, 10, 11))

    def test_new_structured_messages_store_utc_naive_with_marker(self):
        class SessionStub:
            added = None

            def add(self, value):
                self.added = value

        db = SessionStub()
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        create_structured_message(
            db,
            user_id=7,
            category="system",
            status="success",
            status_label="done",
            primary_text="completed",
        )
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        self.assertIsNotNone(db.added)
        self.assertIsNone(db.added.created_at.tzinfo)
        self.assertLessEqual(before, db.added.created_at)
        self.assertLessEqual(db.added.created_at, after)
        payload = json.loads(db.added.content)
        self.assertEqual(payload[MESSAGE_TIME_BASIS_KEY], MESSAGE_TIME_BASIS_UTC)


class MessageTimeMigrationTests(unittest.TestCase):
    @staticmethod
    def _legacy_payload(label):
        return json.dumps(
            {
                "category": "system",
                "status": "success",
                "primary_text": label,
            }
        )

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_migration_converts_only_legacy_local_rows_and_is_idempotent(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO messages "
                    "(id, user_id, title, content, is_read, created_at, updated_at) "
                    "VALUES "
                    "(1, 1, 'legacy', :legacy, 0, '2026-07-30 09:00:00', '2026-07-30 09:00:00'), "
                    "(2, 1, 'task', :task, 0, '2026-07-30 02:00:00', '2026-07-30 02:00:00'), "
                    "(3, 1, 'marked', :marked, 0, '2026-07-30 03:00:00', '2026-07-30 03:00:00')"
                ),
                {
                    "legacy": self._legacy_payload("legacy"),
                    "task": json.dumps(
                        {
                            "category": "task",
                            "status": "success",
                            "primary_text": "task",
                            "task_no": "TASK-1",
                        }
                    ),
                    "marked": json.dumps(
                        {
                            "category": "system",
                            "status": "success",
                            "primary_text": "marked",
                            MESSAGE_TIME_BASIS_KEY: MESSAGE_TIME_BASIS_UTC,
                        }
                    ),
                },
            )

        with (
            patch.object(db_utils, "engine", self.engine),
            patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT),
        ):
            db_utils._migrate_legacy_message_times_to_utc()
            db_utils._migrate_legacy_message_times_to_utc()

        with self.engine.connect() as conn:
            rows = {
                row["id"]: row
                for row in conn.execute(
                    text(
                        "SELECT id, content, created_at FROM messages ORDER BY id"
                    )
                ).mappings()
            }
            sorted_ids = [
                row[0]
                for row in conn.execute(
                    text("SELECT id FROM messages ORDER BY created_at DESC, id DESC")
                )
            ]

        self.assertTrue(str(rows[1]["created_at"]).startswith("2026-07-30 01:00:00"))
        self.assertEqual(json.loads(rows[1]["content"])[MESSAGE_TIME_BASIS_KEY], MESSAGE_TIME_BASIS_UTC)
        self.assertTrue(str(rows[2]["created_at"]).startswith("2026-07-30 02:00:00"))
        self.assertNotIn(MESSAGE_TIME_BASIS_KEY, json.loads(rows[2]["content"]))
        self.assertTrue(str(rows[3]["created_at"]).startswith("2026-07-30 03:00:00"))
        self.assertEqual(sorted_ids, [3, 2, 1])

    def test_migration_scans_and_updates_in_bounded_keyset_batches(self):
        batch_size = db_utils._MESSAGE_TIME_MIGRATION_BATCH_SIZE
        legacy_count = batch_size + 3
        task_message_id = legacy_count + 1
        marked_message_id = legacy_count + 2
        insert_message = text(
            "INSERT INTO messages "
            "(id, user_id, title, content, is_read, created_at, updated_at) "
            "VALUES "
            "(:id, 1, :title, :content, 0, :created_at, :created_at)"
        )
        legacy_rows = [
            {
                "id": message_id,
                "title": f"legacy-{message_id}",
                "content": self._legacy_payload(f"legacy-{message_id}"),
                "created_at": "2026-07-30 09:00:00",
            }
            for message_id in range(1, legacy_count + 1)
        ]
        non_target_rows = [
            {
                "id": task_message_id,
                "title": "task",
                "content": json.dumps(
                    {
                        "category": "task",
                        "status": "success",
                        "primary_text": "task",
                        "task_no": "TASK-BATCH",
                    }
                ),
                "created_at": "2026-07-30 02:00:00",
            },
            {
                "id": marked_message_id,
                "title": "marked",
                "content": json.dumps(
                    {
                        "category": "system",
                        "status": "success",
                        "primary_text": "marked",
                        MESSAGE_TIME_BASIS_KEY: MESSAGE_TIME_BASIS_UTC,
                    }
                ),
                "created_at": "2026-07-30 03:00:00",
            },
        ]
        with self.engine.begin() as conn:
            conn.execute(insert_message, legacy_rows + non_target_rows)

        first_run_statements = []

        def capture_first_run(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ):
            first_run_statements.append(" ".join(statement.split()))

        event.listen(self.engine, "before_cursor_execute", capture_first_run)
        try:
            with (
                patch.object(db_utils, "engine", self.engine),
                patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT),
            ):
                db_utils._migrate_legacy_message_times_to_utc()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_first_run)

        message_selects = [
            statement
            for statement in first_run_statements
            if statement.startswith(
                "SELECT id, content, created_at FROM messages WHERE id >"
            )
        ]
        message_updates = [
            statement
            for statement in first_run_statements
            if statement.startswith("UPDATE messages SET created_at")
        ]
        self.assertEqual(len(message_selects), 3)
        self.assertEqual(len(message_updates), 2)
        self.assertTrue(
            first_run_statements[-1].startswith("INSERT INTO app_metadata")
        )

        with self.engine.connect() as conn:
            rows_after_first_run = conn.execute(
                text(
                    "SELECT id, content, created_at "
                    "FROM messages ORDER BY id"
                )
            ).fetchall()
            marker_after_first_run = conn.execute(
                text(
                    "SELECT value FROM app_metadata "
                    "WHERE key = 'message_time_basis_utc_v2'"
                )
            ).scalar_one()

        self.assertEqual(marker_after_first_run, "1")
        for row in rows_after_first_run[:legacy_count]:
            self.assertTrue(str(row.created_at).startswith("2026-07-30 01:00:00"))
            self.assertEqual(
                json.loads(row.content)[MESSAGE_TIME_BASIS_KEY],
                MESSAGE_TIME_BASIS_UTC,
            )
        task_row = rows_after_first_run[legacy_count]
        self.assertTrue(str(task_row.created_at).startswith("2026-07-30 02:00:00"))
        self.assertNotIn(MESSAGE_TIME_BASIS_KEY, json.loads(task_row.content))
        marked_row = rows_after_first_run[legacy_count + 1]
        self.assertTrue(str(marked_row.created_at).startswith("2026-07-30 03:00:00"))

        second_run_statements = []

        def capture_second_run(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ):
            second_run_statements.append(" ".join(statement.split()))

        event.listen(self.engine, "before_cursor_execute", capture_second_run)
        try:
            with (
                patch.object(db_utils, "engine", self.engine),
                patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT),
            ):
                db_utils._migrate_legacy_message_times_to_utc()
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_second_run)

        with self.engine.connect() as conn:
            rows_after_second_run = conn.execute(
                text(
                    "SELECT id, content, created_at "
                    "FROM messages ORDER BY id"
                )
            ).fetchall()

        self.assertEqual(rows_after_second_run, rows_after_first_run)
        self.assertFalse(
            any("FROM messages" in statement for statement in second_run_statements)
        )
        self.assertFalse(
            any(
                statement.startswith(("UPDATE messages", "INSERT INTO app_metadata"))
                for statement in second_run_statements
            )
        )

    def test_migration_rolls_back_all_rows_when_an_update_fails(self):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO messages "
                    "(id, user_id, title, content, is_read, created_at, updated_at) "
                    "VALUES "
                    "(1, 1, 'first', :first, 0, '2026-07-30 09:00:00', '2026-07-30 09:00:00'), "
                    "(2, 1, 'second', :second, 0, '2026-07-30 10:00:00', '2026-07-30 10:00:00')"
                ),
                {
                    "first": self._legacy_payload("first"),
                    "second": self._legacy_payload("second"),
                },
            )
            conn.execute(
                text(
                    "CREATE TRIGGER reject_second_message_update "
                    "BEFORE UPDATE ON messages WHEN OLD.id = 2 "
                    "BEGIN SELECT RAISE(ABORT, 'test rollback'); END"
                )
            )

        with (
            patch.object(db_utils, "engine", self.engine),
            patch.object(db_utils, "_MESSAGE_TIME_MIGRATION_BATCH_SIZE", 1),
            patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT),
            self.assertRaises(Exception),
        ):
            db_utils._migrate_legacy_message_times_to_utc()

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT content, created_at FROM messages ORDER BY id")
            ).fetchall()
            marker = conn.execute(
                text(
                    "SELECT 1 FROM app_metadata "
                    "WHERE key = 'message_time_basis_utc_v2'"
                )
            ).first()

        self.assertTrue(str(rows[0][1]).startswith("2026-07-30 09:00:00"))
        self.assertTrue(str(rows[1][1]).startswith("2026-07-30 10:00:00"))
        self.assertNotIn(MESSAGE_TIME_BASIS_KEY, json.loads(rows[0][0]))
        self.assertNotIn(MESSAGE_TIME_BASIS_KEY, json.loads(rows[1][0]))
        self.assertIsNone(marker)

        with self.engine.begin() as conn:
            conn.execute(text("DROP TRIGGER reject_second_message_update"))

        with (
            patch.object(db_utils, "engine", self.engine),
            patch.object(db_utils, "_MESSAGE_TIME_MIGRATION_BATCH_SIZE", 1),
            patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT),
        ):
            db_utils._migrate_legacy_message_times_to_utc()
            db_utils._migrate_legacy_message_times_to_utc()

        with self.engine.connect() as conn:
            migrated_rows = conn.execute(
                text("SELECT content, created_at FROM messages ORDER BY id")
            ).fetchall()
        self.assertTrue(str(migrated_rows[0][1]).startswith("2026-07-30 01:00:00"))
        self.assertTrue(str(migrated_rows[1][1]).startswith("2026-07-30 02:00:00"))
        self.assertEqual(
            json.loads(migrated_rows[0][0])[MESSAGE_TIME_BASIS_KEY],
            MESSAGE_TIME_BASIS_UTC,
        )
        self.assertEqual(
            json.loads(migrated_rows[1][0])[MESSAGE_TIME_BASIS_KEY],
            MESSAGE_TIME_BASIS_UTC,
        )


class MessageBatchEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()

        repo = Repository(id=101, name="firmware.bin", version="v1.2.3")
        self.db.add(repo)
        self.db.add_all(
            [
                BurningTask(
                    id=201,
                    task_no="TASK-201",
                    repository_id=repo.id,
                    software_name="alpha.bin",
                    status=2,
                    created_at=datetime(2026, 7, 29, 1, 0, 0),
                    finished_at=datetime(2026, 7, 30, 1, 0, 0),
                ),
                BurningTask(
                    id=202,
                    task_no="TASK-202",
                    repository_id=repo.id,
                    software_name="beta.bin",
                    status=3,
                    created_at=datetime(2026, 7, 29, 2, 0, 0),
                    finished_at=datetime(2026, 7, 30, 2, 0, 0),
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_batch_enrichment_uses_two_queries_and_preserves_result_order(self):
        statements = []

        def before_cursor_execute(
            _conn,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", before_cursor_execute)
        try:
            with patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT):
                results = enrich_task_message_payloads(
                    self.db,
                    [
                        {"task_no": "TASK-202", "project_name": "demo"},
                        {"category": "system"},
                        {"task_no": "TASK-201", "project_name": "demo"},
                    ],
                )
        finally:
            event.remove(self.engine, "before_cursor_execute", before_cursor_execute)

        self.assertEqual(len(statements), 2)
        self.assertEqual(results[0]["software_name"], "beta.bin")
        self.assertEqual(results[1], {"category": "system"})
        self.assertEqual(results[2]["software_name"], "alpha.bin")
        self.assertEqual(results[2]["software_version"], "v1.2.3")
        self.assertEqual(results[2]["event_time"], "2026-07-30T09:00:00")

    def test_messages_endpoint_uses_same_resolved_time_for_event_and_sort_field(self):
        payload = json.dumps({"task_no": "TASK-201", "project_name": "demo"})
        self.db.add(
            Message(
                id=301,
                user_id=9,
                title="task",
                content=payload,
                is_read=False,
                created_at=datetime(2026, 7, 30, 1, 0, 1),
            )
        )
        self.db.commit()

        with patch("backend.utils.datetime_utils.LOCAL_TIMEZONE", UTC_PLUS_EIGHT):
            result = get_messages(
                page=1,
                page_size=10,
                is_read=None,
                db=self.db,
                current_user=SimpleNamespace(
                    id=9,
                    role=SimpleNamespace(data_scope="all"),
                ),
            )

        item = result["data"][0]
        self.assertEqual(item["event_time"], "2026-07-30T09:00:00")
        self.assertEqual(item["created_at"], item["event_time"])


class TaskCreatedAtIndexTests(unittest.TestCase):
    def test_model_creates_index_and_range_query_uses_it(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            with engine.connect() as conn:
                index_names = {
                    row[1] for row in conn.execute(text("PRAGMA index_list(tasks)"))
                }
                plan_rows = conn.execute(
                    text(
                        "EXPLAIN QUERY PLAN "
                        "SELECT COUNT(*) FROM tasks "
                        "WHERE created_at >= :start_time AND created_at < :end_time"
                    ),
                    {
                        "start_time": "2026-07-01 00:00:00",
                        "end_time": "2026-08-01 00:00:00",
                    },
                ).fetchall()
        finally:
            engine.dispose()

        self.assertIn("ix_tasks_created_at", index_names)
        plan_text = " ".join(str(row) for row in plan_rows)
        self.assertIn("ix_tasks_created_at", plan_text)

    def test_sqlite_upgrade_recreates_missing_index_idempotently(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.db"
            engine = db_utils.create_sqlite_engine(database_path)
            try:
                Base.metadata.create_all(engine)
                with engine.begin() as conn:
                    conn.execute(text("DROP INDEX ix_tasks_created_at"))

                with patch.object(db_utils, "engine", engine):
                    db_utils._ensure_schema_uncached()
                    db_utils._ensure_schema_uncached()

                with engine.connect() as conn:
                    matching_indexes = [
                        row
                        for row in conn.execute(text("PRAGMA index_list(tasks)"))
                        if row[1] == "ix_tasks_created_at"
                    ]
            finally:
                engine.dispose()

        self.assertEqual(len(matching_indexes), 1)


if __name__ == "__main__":
    unittest.main()
