import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base
from backend.models.message import Message
from backend.models.repository import Repository, RepositoryProjectMember
from backend.models.task import BurningTask
from backend.routers.messages import (
    TASK_SCOPE_SNAPSHOT_KEY,
    _iter_user_message_batches,
    enrich_task_message_payloads,
    filter_visible_task_messages,
    get_latest_visible_messages,
    get_messages,
    read_all_messages,
)
from backend.routers.tasks import _build_task_notice_payload
from backend.utils import db as db_utils


class MessageDataScopeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.user_id = 11
        self.other_user_id = 22
        self.base_time = datetime(2026, 7, 30, 1, 0, 0)

        self.member_repo = Repository(
            name="member",
            project_key="member-project",
            tenant="tenant-a",
        )
        self.allowed_repo = Repository(
            name="allowed",
            project_key="allowed-project",
            tenant="tenant-a",
        )
        self.outside_repo = Repository(
            name="outside",
            project_key="outside-project",
            tenant="tenant-b",
        )
        self.db.add_all([self.member_repo, self.allowed_repo, self.outside_repo])
        self.db.flush()
        self.db.add(
            RepositoryProjectMember(
                project_key="member-project",
                user_id=self.user_id,
                role="member",
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _user(self, data_scope):
        return SimpleNamespace(
            id=self.user_id,
            role=SimpleNamespace(data_scope=data_scope),
        )

    def _task(
        self,
        task_no,
        *,
        owner_id=None,
        repo=None,
        created_offset=0,
    ):
        task = BurningTask(
            task_no=task_no,
            software_name=f"software-{task_no}",
            created_by_user_id=self.user_id if owner_id is None else owner_id,
            repository_id=getattr(repo, "id", None),
            created_at=self.base_time + timedelta(seconds=created_offset),
            updated_at=self.base_time + timedelta(seconds=created_offset),
            status=2,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def _message(self, payload, *, created_offset=0, user_id=None, title="message"):
        item = Message(
            user_id=self.user_id if user_id is None else user_id,
            title=title,
            content=json.dumps(payload, ensure_ascii=False)
            if isinstance(payload, dict)
            else str(payload),
            is_read=False,
            created_at=self.base_time + timedelta(seconds=created_offset),
            updated_at=self.base_time + timedelta(seconds=created_offset),
        )
        self.db.add(item)
        self.db.flush()
        return item

    @staticmethod
    def _snapshot_payload(
        task_no,
        *,
        task_id,
        owner_user_id,
        project_key,
        tenant,
        task_created_at=None,
        secret="",
    ):
        snapshot = {
            "version": 1,
            "task_id": task_id,
            "owner_user_id": owner_user_id,
            "project_key": project_key,
            "tenant": tenant,
        }
        if task_created_at is not None:
            snapshot["task_created_at"] = (
                task_created_at.isoformat(timespec="microseconds")
                if isinstance(task_created_at, datetime)
                else task_created_at
            )
        return {
            "task_no": task_no,
            "primary_text": secret or task_no,
            TASK_SCOPE_SNAPSHOT_KEY: snapshot,
        }

    def _visible(self, data_scope, items, payloads):
        return filter_visible_task_messages(
            self.db,
            self._user(data_scope),
            items,
            payloads,
        )

    def test_generic_messages_remain_visible_even_when_task_scope_is_invalid(self):
        item = self._message({"category": "system", "primary_text": "generic"})
        self.db.commit()
        self.assertEqual(self._visible(None, [item], [{"category": "system"}]), [True])
        self.assertEqual(self._visible("unknown", [item], [{}]), [True])

    def test_live_tasks_follow_self_project_tenant_and_fixed_project_scope(self):
        own = self._task("OWN", repo=self.outside_repo)
        member = self._task(
            "MEMBER",
            owner_id=self.other_user_id,
            repo=self.member_repo,
        )
        allowed = self._task(
            "ALLOWED",
            owner_id=self.other_user_id,
            repo=self.allowed_repo,
        )
        outside = self._task(
            "OUTSIDE",
            owner_id=self.other_user_id,
            repo=self.outside_repo,
        )
        tasks = [own, member, allowed, outside]
        payloads = [
            self._snapshot_payload(
                task.task_no,
                task_id=task.id,
                owner_user_id=task.created_by_user_id,
                project_key=repo.project_key,
                tenant=repo.tenant,
            )
            for task, repo in zip(
                tasks,
                [
                    self.outside_repo,
                    self.member_repo,
                    self.allowed_repo,
                    self.outside_repo,
                ],
            )
        ]
        items = [
            self._message(payload, created_offset=index)
            for index, payload in enumerate(payloads)
        ]
        self.db.commit()

        self.assertEqual(self._visible("self", items, payloads), [True, False, False, False])
        self.assertEqual(self._visible("project", items, payloads), [True, True, False, False])
        self.assertEqual(self._visible("tenant:tenant-a", items, payloads), [False, True, True, False])
        self.assertEqual(self._visible("project:allowed-project", items, payloads), [False, False, True, False])
        self.assertEqual(self._visible("all", items, payloads), [True, True, True, True])

    def test_duplicate_legacy_task_number_fails_closed_except_for_all_scope(self):
        self._task("DUPLICATE", repo=self.allowed_repo)
        self._task(
            "DUPLICATE",
            owner_id=self.other_user_id,
            repo=self.outside_repo,
        )
        payload = {"task_no": "DUPLICATE", "primary_text": "ambiguous"}
        item = self._message(payload)
        self.db.commit()

        self.assertEqual(self._visible("self", [item], [payload]), [False])
        self.assertEqual(
            self._visible("project:allowed-project", [item], [payload]),
            [False],
        )
        self.assertEqual(self._visible("all", [item], [payload]), [True])

    def test_invalid_snapshot_id_and_mismatched_live_task_number_fail_closed(self):
        task = self._task("CANONICAL", repo=self.allowed_repo)
        invalid_payload = {
            "task_no": "CANONICAL",
            TASK_SCOPE_SNAPSHOT_KEY: {"task_id": "not-an-integer"},
        }
        mismatch_payload = self._snapshot_payload(
            "SPOOFED-NUMBER",
            task_id=task.id,
            owner_user_id=self.user_id,
            project_key="allowed-project",
            tenant="tenant-a",
        )
        invalid = self._message(invalid_payload)
        mismatch = self._message(mismatch_payload, created_offset=1)
        self.db.commit()

        self.assertEqual(
            self._visible(
                "project:allowed-project",
                [invalid, mismatch],
                [invalid_payload, mismatch_payload],
            ),
            [False, False],
        )

    def test_recycled_snapshot_identity_does_not_rebind_or_enrich_old_message(self):
        original = self._task("RECYCLED", repo=self.outside_repo)
        original_id = int(original.id)
        payload = self._snapshot_payload(
            "RECYCLED",
            task_id=original_id,
            task_created_at=original.created_at,
            owner_user_id=self.other_user_id,
            project_key="outside-project",
            tenant="tenant-b",
            secret="old-task-message",
        )
        payload["software_name"] = "old-software.bin"
        payload["event_time"] = "2026-07-30T01:00:05"
        message = self._message(payload, created_offset=10)
        self.db.commit()

        self.db.delete(original)
        self.db.commit()
        replacement = BurningTask(
            id=original_id,
            task_no="RECYCLED",
            software_name="new-software.bin",
            created_by_user_id=self.other_user_id,
            repository_id=self.allowed_repo.id,
            created_at=self.base_time + timedelta(seconds=20),
            updated_at=self.base_time + timedelta(seconds=20),
            finished_at=self.base_time + timedelta(seconds=30),
            status=2,
        )
        self.db.add(replacement)
        self.db.commit()

        self.assertEqual(
            self._visible("project:allowed-project", [message], [payload]),
            [False],
        )
        enriched = enrich_task_message_payloads(
            self.db,
            [payload],
            messages=[message],
        )[0]
        self.assertEqual(enriched["software_name"], "old-software.bin")
        self.assertEqual(enriched["event_time"], "2026-07-30T01:00:05")

    def test_recycled_legacy_task_number_must_predate_message(self):
        original = self._task("LEGACY-RECYCLED", repo=self.outside_repo)
        original_id = int(original.id)
        payload = {
            "task_no": "LEGACY-RECYCLED",
            "software_name": "legacy-old.bin",
            "event_time": "2026-07-30T01:00:05",
        }
        message = self._message(payload, created_offset=10)
        self.db.commit()

        self.db.delete(original)
        self.db.commit()
        self.db.add(
            BurningTask(
                id=original_id,
                task_no="LEGACY-RECYCLED",
                software_name="legacy-new.bin",
                created_by_user_id=self.other_user_id,
                repository_id=self.allowed_repo.id,
                created_at=self.base_time + timedelta(seconds=20),
                updated_at=self.base_time + timedelta(seconds=20),
                finished_at=self.base_time + timedelta(seconds=30),
                status=2,
            )
        )
        self.db.commit()

        self.assertEqual(
            self._visible("project:allowed-project", [message], [payload]),
            [False],
        )
        enriched = enrich_task_message_payloads(
            self.db,
            [payload],
            messages=[message],
        )[0]
        self.assertEqual(enriched["software_name"], "legacy-old.bin")
        self.assertEqual(enriched["event_time"], "2026-07-30T01:00:05")

    def test_deleted_messages_use_snapshot_and_legacy_owner_fallback_safely(self):
        legacy_payload = {"task_no": "DELETED-LEGACY"}
        legacy = self._message(legacy_payload)
        owner_payload = self._snapshot_payload(
            "DELETED-OWNER",
            task_id=9001,
            owner_user_id=self.user_id,
            project_key="outside-project",
            tenant="tenant-b",
        )
        owner = self._message(owner_payload, created_offset=1)
        member_payload = self._snapshot_payload(
            "DELETED-MEMBER",
            task_id=9002,
            owner_user_id=self.other_user_id,
            project_key="member-project",
            tenant="tenant-a",
        )
        member = self._message(member_payload, created_offset=2)
        allowed_payload = self._snapshot_payload(
            "DELETED-ALLOWED",
            task_id=9003,
            owner_user_id=self.other_user_id,
            project_key="allowed-project",
            tenant="tenant-a",
        )
        allowed = self._message(allowed_payload, created_offset=3)
        items = [legacy, owner, member, allowed]
        payloads = [legacy_payload, owner_payload, member_payload, allowed_payload]
        self.db.commit()

        self.assertEqual(self._visible("self", items, payloads), [True, True, False, False])
        self.assertEqual(self._visible("project", items, payloads), [True, True, True, False])
        self.assertEqual(
            self._visible("tenant:tenant-a", items, payloads),
            [False, False, True, True],
        )
        self.assertEqual(
            self._visible("project:allowed-project", items, payloads),
            [False, False, False, True],
        )
        self.assertEqual(self._visible("all", items, payloads), [True, True, True, True])

    def test_messages_endpoint_filters_before_exact_total_and_pagination(self):
        allowed_task = self._task(
            "VISIBLE",
            owner_id=self.other_user_id,
            repo=self.allowed_repo,
        )
        hidden_task = self._task(
            "HIDDEN",
            owner_id=self.other_user_id,
            repo=self.outside_repo,
        )
        allowed_payload = self._snapshot_payload(
            "VISIBLE",
            task_id=allowed_task.id,
            owner_user_id=self.other_user_id,
            project_key="allowed-project",
            tenant="tenant-a",
            secret="allowed-content",
        )
        hidden_payload = self._snapshot_payload(
            "HIDDEN",
            task_id=hidden_task.id,
            owner_user_id=self.other_user_id,
            project_key="outside-project",
            tenant="tenant-b",
            secret="TOP-SECRET-CONTENT",
        )
        self._message(allowed_payload, created_offset=1)
        self._message({"category": "system", "primary_text": "generic"}, created_offset=2)
        self._message(hidden_payload, created_offset=3)
        self._message(
            {"category": "system", "primary_text": "OTHER-USER-SECRET"},
            created_offset=4,
            user_id=999,
        )
        self.db.commit()
        current_user = self._user("project:allowed-project")

        first_page = get_messages(
            page=1,
            page_size=1,
            is_read=None,
            db=self.db,
            current_user=current_user,
        )
        second_page = get_messages(
            page=2,
            page_size=1,
            is_read=None,
            db=self.db,
            current_user=current_user,
        )

        self.assertEqual(first_page["total"], 2)
        self.assertEqual(second_page["total"], 2)
        self.assertEqual(first_page["data"][0]["primary_text"], "generic")
        self.assertEqual(second_page["data"][0]["primary_text"], "allowed-content")
        serialized = json.dumps(
            [first_page["data"], second_page["data"]],
            ensure_ascii=False,
        )
        self.assertNotIn("TOP-SECRET-CONTENT", serialized)
        self.assertNotIn("HIDDEN", serialized)
        self.assertNotIn("OTHER-USER-SECRET", serialized)

    def test_is_read_filter_applies_before_authorized_total_and_pagination(self):
        unread = self._message(
            {"category": "system", "primary_text": "unread"},
            created_offset=1,
        )
        read = self._message(
            {"category": "system", "primary_text": "read"},
            created_offset=2,
        )
        read.is_read = True
        self.db.commit()

        unread_result = get_messages(
            page=1,
            page_size=10,
            is_read=0,
            db=self.db,
            current_user=self._user("self"),
        )
        read_result = get_messages(
            page=1,
            page_size=10,
            is_read=1,
            db=self.db,
            current_user=self._user("self"),
        )

        self.assertEqual(unread_result["total"], 1)
        self.assertEqual(
            [item["primary_text"] for item in unread_result["data"]],
            ["unread"],
        )
        self.assertEqual(read_result["total"], 1)
        self.assertEqual(
            [item["primary_text"] for item in read_result["data"]],
            ["read"],
        )

    def test_read_all_only_marks_messages_visible_in_each_data_scope(self):
        own_task = self._task("READ-OWN", repo=self.outside_repo)
        member_task = self._task(
            "READ-MEMBER",
            owner_id=self.other_user_id,
            repo=self.member_repo,
        )
        allowed_task = self._task(
            "READ-ALLOWED",
            owner_id=self.other_user_id,
            repo=self.allowed_repo,
        )
        outside_task = self._task(
            "READ-OUTSIDE",
            owner_id=self.other_user_id,
            repo=self.outside_repo,
        )

        generic = self._message(
            {"category": "system", "primary_text": "generic"},
            created_offset=10,
        )
        task_entries = []
        for index, (task, repo) in enumerate(
            [
                (own_task, self.outside_repo),
                (member_task, self.member_repo),
                (allowed_task, self.allowed_repo),
                (outside_task, self.outside_repo),
            ],
            start=11,
        ):
            payload = self._snapshot_payload(
                task.task_no,
                task_id=task.id,
                task_created_at=task.created_at,
                owner_user_id=task.created_by_user_id,
                project_key=repo.project_key,
                tenant=repo.tenant,
            )
            task_entries.append((task.task_no, self._message(payload, created_offset=index)))
        pre_read = self._message(
            {"category": "system", "primary_text": "already-read"},
            created_offset=20,
        )
        pre_read.is_read = True
        other_user = self._message(
            {"category": "system", "primary_text": "other-user"},
            created_offset=21,
            user_id=999,
        )
        self.db.commit()

        current_message_ids = {
            "generic": int(generic.id),
            **{task_no: int(item.id) for task_no, item in task_entries},
            "pre_read": int(pre_read.id),
        }
        cases = {
            "all": {
                "generic",
                "READ-OWN",
                "READ-MEMBER",
                "READ-ALLOWED",
                "READ-OUTSIDE",
                "pre_read",
            },
            "self": {"generic", "READ-OWN", "pre_read"},
            "project": {
                "generic",
                "READ-OWN",
                "READ-MEMBER",
                "pre_read",
            },
            "tenant:tenant-a": {
                "generic",
                "READ-MEMBER",
                "READ-ALLOWED",
                "pre_read",
            },
            "project:allowed-project": {
                "generic",
                "READ-ALLOWED",
                "pre_read",
            },
        }

        for data_scope, expected_read_names in cases.items():
            with self.subTest(data_scope=data_scope):
                self.db.query(Message).filter(
                    Message.user_id == self.user_id
                ).update({"is_read": False}, synchronize_session=False)
                self.db.query(Message).filter(Message.id == pre_read.id).update(
                    {"is_read": True},
                    synchronize_session=False,
                )
                self.db.query(Message).filter(Message.id == other_user.id).update(
                    {"is_read": False},
                    synchronize_session=False,
                )
                self.db.commit()

                read_all_messages(
                    db=self.db,
                    current_user=self._user(data_scope),
                )
                read_ids = {
                    int(message_id)
                    for (message_id,) in self.db.query(Message.id)
                    .filter(
                        Message.user_id == self.user_id,
                        Message.is_read == True,
                    )
                    .all()
                }
                expected_ids = {
                    current_message_ids[name]
                    for name in expected_read_names
                }
                self.assertEqual(read_ids, expected_ids)
                self.assertFalse(
                    self.db.query(Message.is_read)
                    .filter(Message.id == other_user.id)
                    .scalar()
                )

    def test_batch_authorization_is_constant_query_count_not_n_plus_one(self):
        items = []
        payloads = []
        for index in range(20):
            task = self._task(f"TASK-{index}", repo=self.allowed_repo)
            payload = {"task_no": task.task_no}
            items.append(self._message(payload, created_offset=index))
            payloads.append(payload)
        self.db.commit()
        statements = []

        def capture(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            visible = self._visible("self", items, payloads)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(visible, [True] * len(items))
        self.assertEqual(len(statements), 2)

    def test_latest_visible_helper_stops_after_first_sufficient_batch(self):
        for index in range(20):
            self._message(
                {"category": "system", "primary_text": f"generic-{index}"},
                created_offset=index,
            )
        self.db.commit()
        statements = []

        def capture(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            items, payloads = get_latest_visible_messages(
                self.db,
                self._user("self"),
                limit=3,
                batch_size=5,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)

        self.assertEqual(len(items), 3)
        self.assertEqual(
            [payload["primary_text"] for payload in payloads],
            ["generic-19", "generic-18", "generic-17"],
        )
        self.assertEqual(len(statements), 1)

    def test_message_batch_cursor_handles_legacy_whole_second_timestamps(self):
        for index in range(5):
            self._message(
                {"category": "system", "primary_text": f"legacy-{index}"},
                created_offset=index,
            )
        self.db.commit()
        self.db.execute(
            text(
                "UPDATE messages "
                "SET created_at = substr(CAST(created_at AS TEXT), 1, 19) "
                "WHERE user_id = :user_id"
            ),
            {"user_id": self.user_id},
        )
        self.db.commit()
        self.db.expire_all()

        batches = list(
            _iter_user_message_batches(
                self.db,
                user_id=self.user_id,
                batch_size=2,
            )
        )
        ids = [item.id for batch in batches for item in batch]

        self.assertEqual(len(batches), 3)
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_new_task_payload_contains_immutable_scope_snapshot(self):
        task = self._task("SNAPSHOT", repo=self.allowed_repo)
        payload = _build_task_notice_payload(
            task,
            self.allowed_repo,
            "Allowed Project",
            "burn",
            "success",
            "done",
        )
        snapshot = payload[TASK_SCOPE_SNAPSHOT_KEY]
        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["task_id"], task.id)
        self.assertEqual(
            snapshot["task_created_at"],
            task.created_at.isoformat(timespec="microseconds"),
        )
        self.assertEqual(snapshot["owner_user_id"], self.user_id)
        self.assertEqual(snapshot["project_key"], "allowed-project")
        self.assertEqual(snapshot["tenant"], "tenant-a")

    def test_message_composite_scan_index_exists(self):
        index_names = {
            index["name"] for index in inspect(self.engine).get_indexes("messages")
        }
        self.assertIn("ix_messages_user_created_id", index_names)

    def test_sqlite_upgrade_recreates_message_scan_index_idempotently(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.db"
            engine = db_utils.create_sqlite_engine(database_path)
            try:
                Base.metadata.create_all(engine)
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "DROP INDEX ix_messages_user_created_id"
                    )
                with patch.object(db_utils, "engine", engine):
                    db_utils._ensure_schema_uncached()
                    db_utils._ensure_schema_uncached()
                index_names = {
                    index["name"]
                    for index in inspect(engine).get_indexes("messages")
                }
            finally:
                engine.dispose()

        self.assertIn("ix_messages_user_created_id", index_names)


if __name__ == "__main__":
    unittest.main()
