from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Injection, InjectionRun
from backend.routers import injections
from backend.routers.auth import get_current_user
from backend.utils.db import get_db


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'injection.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    user = SimpleNamespace(id=42, username="ordinary-tester", get_permissions=lambda: ["injection:add", "injection:delete"])
    app = FastAPI()
    app.include_router(injections.router, prefix="/api/injections")
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(injections, "ensure_schema", lambda: None)
    async def connection(*args, **kwargs):
        return {"success": True, "detail": "simulated connection"}
    monkeypatch.setattr(injections, "test_network_connection", connection)
    return SimpleNamespace(client=TestClient(app), sessions=sessions, user=user)


@pytest.mark.parametrize("kind", ["network-error", "storage-full", "permission-error"])
def test_ordinary_add_permission_can_test_connection_without_obsolete_permission(context, kind):
    response = context.client.post(f"/api/injections/{kind}/connection-test", json={"target_ip": "127.0.0.1"})
    assert response.status_code == 200, response.text
    context.user.get_permissions = lambda: ["injection:view", "injection:execute"]
    assert context.client.post(f"/api/injections/{kind}/connection-test", json={}).status_code == 403


@pytest.mark.parametrize("endpoint", ["999999/execute", "runs/999999/recover"])
def test_execute_and_recovery_use_add_permission(context, endpoint):
    response = context.client.post(f"/api/injections/{endpoint}")
    assert response.status_code in {404, 409}, response.text
    context.user.get_permissions = lambda: ["injection:view", "injection:execute"]
    assert context.client.post(f"/api/injections/{endpoint}").status_code == 403


@pytest.mark.parametrize("status", [0, 1, 2, 3, 4])
def test_running_record_rejects_delete_and_terminal_record_deletes(context, status):
    with context.sessions() as db:
        injection = Injection(type="network_error", target="simulated")
        db.add(injection)
        db.flush()
        run = InjectionRun(injection_id=injection.id, type="network_error", target="simulated", exec_status=status)
        db.add(run)
        db.commit()
        identifier = run.id
    response = context.client.delete(f"/api/injections/runs/{identifier}")
    assert response.status_code == (409 if status == 1 else 200), response.text
    with context.sessions() as db:
        assert (db.get(InjectionRun, identifier) is not None) == (status == 1)
