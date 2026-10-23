import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, BurningTask, Repository
from backend.routers import tasks
from backend.routers.auth import get_current_user
from backend.utils.db import get_db


@pytest.fixture
def context(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    file = tmp_path / "firmware.app"
    file.write_bytes(b"new firmware")
    actual = hashlib.sha256(file.read_bytes()).hexdigest()
    with sessions() as db:
        repo = Repository(name=file.name, version="1.0", file_url=str(file), sha256=actual,
                          file_detail_json=json.dumps({"local_exists": True, "local_path": str(file)}))
        db.add(repo)
        db.flush()
        repo_id = repo.id
        db.add(BurningTask(software_name=file.name, repository_id=repo_id, status=2,
                           current_sha256="a" * 64, finished_at=datetime.utcnow()))
        db.commit()
    user = SimpleNamespace(id=1, username="admin", role=SimpleNamespace(data_scope="all"), get_permissions=lambda: ["all"])
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api/tasks")
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(tasks, "ensure_schema", lambda: None)
    config = {"task_type": "os", "os_type": "harmony", "connection_protocol": "HDC",
              "harmony_device_id": "test-device", "install_source": "local",
              "integrity": True, "version_check": True, "history_checksum": "forged"}
    payload = {"software_name": file.name, "repository_id": repo_id, "task_type": "os",
               "config_json": json.dumps(config), "auto_execute": False}
    return SimpleNamespace(client=TestClient(app), sessions=sessions, file=file,
                           payload=payload, user=user, repo_id=repo_id, actual=actual)


def token(context):
    response = context.client.post("/api/tasks/preflight", json=context.payload)
    assert response.status_code == 200, response.text
    checks = response.json()["data"]
    assert checks["sha256"] == context.actual
    assert checks["baseline"] == "a" * 64
    assert checks["version_mismatch"] and checks["integrity_passed"]
    return checks["override_token"]


def test_confirmation_creates_one_task_without_failed_draft(context):
    before = token(context)
    with context.sessions() as db:
        assert db.query(BurningTask).count() == 1
    response = context.client.post("/api/tasks", json={**context.payload, "override_token": before})
    assert response.status_code == 200, response.text
    with context.sessions() as db:
        assert db.query(BurningTask).count() == 2
        task = db.query(BurningTask).order_by(BurningTask.id.desc()).first()
        assert task.status == 0 and task.override_confirmed == 1
        assert task.history_checksum == "a" * 64


def test_client_override_flag_without_permission_cannot_bypass(context):
    context.user.get_permissions = lambda: ["burning:add"]
    response = context.client.post("/api/tasks", json={**context.payload, "override_confirmed": 1})
    assert response.status_code == 403
    with context.sessions() as db:
        assert db.query(BurningTask).count() == 1


def test_mismatch_without_confirmation_returns_409_and_no_draft(context):
    response = context.client.post("/api/tasks", json=context.payload)
    assert response.status_code == 409
    with context.sessions() as db:
        assert db.query(BurningTask).count() == 1


@pytest.mark.parametrize("change", ["file", "baseline", "configuration", "user"])
def test_confirmation_invalidates_when_bound_input_changes(context, change):
    confirmation = token(context)
    payload = dict(context.payload)
    if change == "file":
        context.file.write_bytes(b"modified firmware")
    elif change == "baseline":
        with context.sessions() as db:
            db.add(BurningTask(software_name=context.file.name, repository_id=context.repo_id,
                               status=2, current_sha256="b" * 64, finished_at=datetime.utcnow()))
            db.commit()
    elif change == "configuration":
        config = json.loads(payload["config_json"])
        config["harmony_device_id"] = "another-device"
        payload["config_json"] = json.dumps(config)
    else:
        context.user.id = 2
    response = context.client.post("/api/tasks", json={**payload, "override_token": confirmation})
    assert response.status_code == 409, response.text


def test_missing_offline_file_never_downloads_online(context, monkeypatch):
    confirmation = token(context)
    context.file.unlink()
    def forbidden(*args, **kwargs):
        pytest.fail("Offline submission attempted an online download")
    monkeypatch.setattr(tasks, "_download_repository_artifact_to_local_storage", forbidden)
    response = context.client.post("/api/tasks", json={**context.payload, "override_token": confirmation})
    assert response.status_code == 400
    assert "离线文件" in response.json()["detail"]


def test_again_uses_current_baseline_and_actual_hashes(context):
    confirmation = token(context)
    created = context.client.post("/api/tasks", json={**context.payload, "override_token": confirmation})
    task_id = created.json()["data"]["id"]
    response = context.client.post(f"/api/tasks/{task_id}/preflight")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["sha256"] == context.actual


@pytest.mark.parametrize("confirmation_state", ["missing", "stale", "valid"])
def test_legacy_override_requires_current_confirmation_without_mutating_history(context, confirmation_state):
    confirmation = token(context)
    created = context.client.post("/api/tasks", json={**context.payload, "override_token": confirmation})
    task_id = created.json()["data"]["id"]
    with context.sessions() as db:
        task = db.get(BurningTask, task_id)
        task.override_confirmed = 0
        config = json.loads(task.config_json)
        config.pop("_override_token", None)
        task.config_json = json.dumps(config)
        db.commit()
        original_config = task.config_json
    preflight = context.client.post(f"/api/tasks/{task_id}/preflight").json()["data"]
    if confirmation_state == "stale":
        context.file.write_bytes(b"changed after confirmation")
    response = context.client.post(f"/api/tasks/{task_id}/override", json=(
        {} if confirmation_state == "missing" else {"override_token": preflight["override_token"]}
    ))
    assert response.status_code == (200 if confirmation_state == "valid" else 409), response.text
    with context.sessions() as db:
        task = db.get(BurningTask, task_id)
        assert task.status == 0
        assert task.override_confirmed == int(confirmation_state == "valid")
        if confirmation_state != "valid":
            assert task.config_json == original_config
        assert db.query(BurningTask).count() == 2


def test_hash_failure_has_priority_over_version_and_integrity(context, monkeypatch):
    def cannot_hash(*args):
        raise OSError("unreadable")
    monkeypatch.setattr(tasks, "_compute_decrypted_artifact_hashes", cannot_hash)
    response = context.client.post("/api/tasks/preflight", json=context.payload)
    assert response.status_code == 400
    assert response.json()["detail"].startswith("制品校验值计算失败")


def test_double_failure_confirmation_records_hashes_without_device_execution(context, monkeypatch):
    with context.sessions() as db:
        db.query(Repository).filter(Repository.id == context.repo_id).first().sha256 = "b" * 64
        db.commit()
    def forbidden(*args, **kwargs):
        pytest.fail("Failed integrity must never schedule device execution")
    monkeypatch.setattr(tasks, "_start_task_execution", forbidden)
    preflight = context.client.post("/api/tasks/preflight", json=context.payload).json()["data"]
    assert preflight["version_mismatch"] and not preflight["integrity_passed"]
    assert preflight["override_token"]
    created = context.client.post("/api/tasks", json={
        **context.payload, "auto_execute": True, "override_token": preflight["override_token"],
    })
    assert created.status_code == 200, created.text
    assert created.json()["data"]["status"] == 3
    assert created.json()["data"]["auto_executed"] is False
    task_id = created.json()["data"]["id"]
    with context.sessions() as db:
        task = db.get(BurningTask, task_id)
        assert task.current_sha256 == context.actual
        assert task.current_md5 == hashlib.md5(context.file.read_bytes()).hexdigest()
        assert task.last_error == "完整性校验失败"
        assert not task.rollback_count and not task.attempt_count
    again_token = context.client.post(f"/api/tasks/{task_id}/preflight").json()["data"]["override_token"]
    again = context.client.post(f"/api/tasks/{task_id}/execute", json={"override_token": again_token})
    assert again.status_code == 200, again.text
    assert again.json()["data"]["id"] != task_id
    with context.sessions() as db:
        assert db.get(BurningTask, again.json()["data"]["id"]).status == 3


@pytest.mark.parametrize("change", ["expiry", "permission", "revision"])
def test_confirmation_rejects_expiry_permission_and_execution_revision(context, monkeypatch, change):
    confirmation = token(context)
    expected_status = 409
    if change == "expiry":
        from jose import jwt
        claims = jwt.decode(confirmation, tasks.SECRET_KEY, algorithms=[tasks.ALGORITHM])
        claims["exp"] = 1
        confirmation = jwt.encode(claims, tasks.SECRET_KEY, algorithm=tasks.ALGORITHM)
    elif change == "permission":
        context.user.get_permissions = lambda: ["burning:add"]
        expected_status = 403
    else:
        monkeypatch.setattr(tasks, "_execution_revision", lambda *_: "changed-script-content")
    response = context.client.post("/api/tasks", json={**context.payload, "override_token": confirmation})
    assert response.status_code == expected_status, response.text


def test_preflight_removes_only_newly_downloaded_copy(context, monkeypatch):
    from backend.routers import repositories
    monkeypatch.setattr(repositories, "_repository_allowed_roots", lambda: [str(context.file.parent)])
    config = json.loads(context.payload["config_json"])
    config.update(install_source="codearts", keep_local=True)
    context.payload["config_json"] = json.dumps(config)
    context.file.unlink()
    with context.sessions() as db:
        repo = db.get(Repository, context.repo_id)
        repo.file_detail_json = json.dumps({"local_exists": False})
        repo.file_url = None
        db.commit()
    def download(db, repo, user):
        context.file.write_bytes(b"new firmware")
        tasks._apply_repository_location_state(repo, {}, local_exists=True, local_path=str(context.file),
                                               server_exists=False, server_path=None, server_target=None)
        db.commit()
        return repo
    monkeypatch.setattr(tasks, "_download_repository_artifact_to_local_storage", download)
    response = context.client.post("/api/tasks/preflight", json=context.payload)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["sha256"] == context.actual
    assert not context.file.exists()
    with context.sessions() as db:
        state = tasks._get_repository_location_state(db.get(Repository, context.repo_id), {})
        assert not state["local_exists"]
        assert db.query(BurningTask).count() == 1


def test_missing_runtime_local_source_never_falls_back_to_server(context, monkeypatch):
    with context.sessions() as db:
        repo = db.get(Repository, context.repo_id)
        detail = json.loads(repo.file_detail_json)
        detail.update(server_exists=True, server_path="/remote/firmware.pcenc")
        repo.file_detail_json = json.dumps(detail)
        db.commit()
        context.file.unlink()
        def forbidden(*args, **kwargs):
            pytest.fail("Local offline source was replaced with another source")
        monkeypatch.setattr(tasks, "_download_repository_artifact_from_server_storage", forbidden)
        monkeypatch.setattr(tasks, "_download_repository_artifact_to_local_storage", forbidden)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            tasks._ensure_repository_local_file_available_for_runtime(
                db, repo, context.user, config={"install_source": "local"},
            )
        assert error.value.status_code == 400


@pytest.mark.parametrize("change", [
    "unchanged", "file", "baseline", "revision", "hash", "permission", "disabled", "token_version",
])
def test_execution_rechecks_confirmation_and_never_retries_deterministic_failure(context, monkeypatch, change):
    import asyncio
    from backend.models import User, Role, Permission
    from backend.utils import db as database
    config = json.loads(context.payload["config_json"])
    config["retries"] = 3
    context.payload["config_json"] = json.dumps(config)
    confirmation = token(context)
    created = context.client.post("/api/tasks", json={**context.payload, "override_token": confirmation})
    task_id = created.json()["data"]["id"]
    with context.sessions() as db:
        role = Role(name="ordinary-tester", status=1, permissions=[
            Permission(code="burning:override", name="Override", type="button"),
        ])
        user = User(id=1, username="ordinary-tester", password_hash="test-only", status=1, role=role)
        db.add(user)
        db.get(BurningTask, task_id).status = 1
        if change == "baseline":
            db.add(BurningTask(software_name="firmware", repository_id=context.repo_id,
                               status=2, current_sha256="b" * 64, finished_at=datetime.utcnow()))
        elif change == "permission":
            role.permissions = []
        elif change == "disabled":
            user.status = 0
        elif change == "token_version":
            user.token_version = 1
        db.commit()
    if change == "file":
        context.file.write_bytes(b"changed after submission")
    elif change == "revision":
        monkeypatch.setattr(tasks, "_execution_revision", lambda *_: "changed-after-submission")
    elif change == "hash":
        def fail_hash(*args):
            raise OSError("runtime hash failure")
        monkeypatch.setattr(tasks, "_compute_hashes", fail_hash)
    calls = []
    async def execution(*args, **kwargs):
        calls.append("execute")
        return True, "simulated installation", ""
    async def no_delay(*args):
        return None
    monkeypatch.setattr(tasks, "_execute_os_task", execution)
    monkeypatch.setattr(tasks.asyncio, "sleep", no_delay)
    monkeypatch.setattr(database, "SessionLocal", context.sessions)
    run_token = f"security-test-{task_id}"
    tasks.TASK_ACTIVE_RUN_TOKENS[task_id] = run_token
    try:
        asyncio.run(tasks.simulate_burning_process(task_id, 1, "admin", None, run_token))
    finally:
        tasks.TASK_ACTIVE_RUN_TOKENS.pop(task_id, None)
    with context.sessions() as db:
        task = db.get(BurningTask, task_id)
        assert task.status == (2 if change == "unchanged" else 3), task.result
        assert task.attempt_count == 1
        assert not task.rollback_count
        assert calls == (["execute"] if change == "unchanged" else [])
        if change != "unchanged":
            assert task.last_error == ("制品校验值计算失败" if change == "hash" else "版本一致性比对失败")
