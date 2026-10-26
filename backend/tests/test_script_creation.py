from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, Script
from backend.routers import scripts
from backend.routers.auth import get_current_user
from backend.utils.db import get_db


@pytest.fixture
def script_context(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scripts.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(scripts.router, prefix="/api/scripts")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        username="admin", get_permissions=lambda: ["all"],
    )
    monkeypatch.setattr(scripts, "ensure_schema", lambda: None)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, sessions=sessions)
    engine.dispose()


@pytest.mark.parametrize("content", [None, "", "echo ready"])
def test_create_script_then_edit_content_and_basic_information(script_context, content):
    payload = {
        "name": "新建脚本", "type": "shell", "task_type": "board",
        "associated_board": "STM32", "associated_burner": "STLINK",
    }
    if content is not None:
        payload["content"] = content
    response = script_context.client.post("/api/scripts", json=payload)
    assert response.status_code == 200, response.text
    script_id = response.json()["data"]["id"]
    with script_context.sessions() as db:
        assert db.get(Script, script_id).content == (content or "")

    response = script_context.client.put(
        f"/api/scripts/{script_id}", json={"content": "echo updated"},
    )
    assert response.status_code == 200, response.text
    response = script_context.client.put(
        f"/api/scripts/{script_id}", json={"name": "修改基本信息"},
    )
    assert response.status_code == 200, response.text
    with script_context.sessions() as db:
        script = db.get(Script, script_id)
        assert script.name == "修改基本信息"
        assert script.content == "echo updated"
