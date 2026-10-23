from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, BurningTask, Repository, Script, User
from backend.models.log import LoginLog, OperationLog, ProtocolSession
from backend.models.repository import RepositoryProjectMember
from backend.routers import logs, protocol_tests, repositories, scripts, tasks, users
from backend.routers.auth import get_current_user
from backend.utils.db import get_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = User(id=1, username="admin", display_name="Administrator", password_hash="never-public",
                    status=1, avatar_url="/api/auth/avatar/profile.png")
        db.add(user)
        repo = Repository(name="identity", project_key="qa-identity", created_by_user_id=1)
        db.add(repo)
        db.flush()
        db.add(BurningTask(software_name="identity", repository_id=repo.id, created_by_user_id=1, status=3))
        db.add(Script(name="identity", type="python", content="print('preserve')", modified_by="admin", task_type="board"))
        db.add(RepositoryProjectMember(project_key="qa-identity", user_id=1, inviter_user_id=1, role="admin"))
        db.add(LoginLog(user_id=1, log_type="login", login_time=datetime.now()))
        db.add(OperationLog(user_id=1, module="identity", operation_time=datetime.now()))
        db.add(ProtocolSession(target="identified", protocol="serial", executor="admin", created_by_user_id=1, status=2))
        db.add(ProtocolSession(target="legacy", protocol="serial", executor="admin", created_by_user_id=None, status=2))
        db.commit()
    app = FastAPI()
    for module, prefix in (
        (users, "users"), (scripts, "scripts"), (logs, "logs"),
        (tasks, "tasks"), (protocol_tests, "protocol-tests"), (repositories, "repositories"),
    ):
        app.include_router(module.router, prefix=f"/api/{prefix}")
        if hasattr(module, "ensure_schema"):
            monkeypatch.setattr(module, "ensure_schema", lambda: None)
    current = SimpleNamespace(id=1, username="admin", role=SimpleNamespace(data_scope="all"),
                              get_permissions=lambda: ["all"])
    app.dependency_overrides[get_current_user] = lambda: current
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    return TestClient(app)


@pytest.mark.parametrize("endpoint,identity_key", [
    ("/api/users", None),
    ("/api/scripts", "modifier_user"),
    ("/api/logs/login", None),
    ("/api/logs/operation", None),
    ("/api/tasks?project_key=qa-identity", "executor_user"),
    ("/api/protocol-tests/records", "executor_user"),
    ("/api/repositories/projects/qa-identity/members", None),
])
def test_all_person_lists_propagate_avatar_without_credentials(client, endpoint, identity_key):
    response = client.get(endpoint)
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert rows
    for row in rows:
        identity = row[identity_key] if identity_key else row
        assert identity["username"] == "admin"
        assert identity["display_name"] == "Administrator"
        assert identity["avatar_url"] == "/api/auth/avatar/profile.png"
        assert "password_hash" not in identity
        if "inviter_username" in row:
            assert row["inviter_avatar_url"] == identity["avatar_url"]
            assert row["inviter_display_name"] == identity["display_name"]


def test_protocol_record_detail_propagates_legacy_executor_identity(client):
    records = client.get("/api/protocol-tests/records").json()["data"]
    legacy = next(row for row in records if row["target"] == "legacy")
    response = client.get(f"/api/protocol-tests/records/{legacy['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["executor_user"]["avatar_url"] == "/api/auth/avatar/profile.png"
