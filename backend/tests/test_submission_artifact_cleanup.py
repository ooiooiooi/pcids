import json

import pytest
from jose import jwt

from backend.models import BurningTask, Repository
from backend.routers import repositories, tasks
from backend.tests.test_task_confirmation_security import context, token


def prepare_download(context, monkeypatch, *, keep=False, shared=False):
    monkeypatch.setattr(repositories, "_repository_allowed_roots", lambda: [str(context.file.parent)])
    config = json.loads(context.payload["config_json"])
    config.update(install_source="codearts", keep_local=keep)
    context.payload["config_json"] = json.dumps(config)
    context.file.unlink()
    with context.sessions() as db:
        repo = db.get(Repository, context.repo_id)
        repo.file_url = None
        repo.file_detail_json = json.dumps({"local_exists": False})
        source = db.query(BurningTask).first()
        source.task_type = "os"
        source.config_json = context.payload["config_json"]
        source.integrity = source.version_check = 1
        if shared:
            db.add(BurningTask(software_name="shared", repository_id=repo.id, status=1))
        db.commit()
    def download(db, repo, user):
        context.file.write_bytes(b"new firmware")
        tasks._apply_repository_location_state(
            repo, {}, local_exists=True, local_path=str(context.file),
            server_exists=False, server_path=None, server_target=None,
        )
        db.commit()
        return repo
    monkeypatch.setattr(tasks, "_download_repository_artifact_to_local_storage", download)


@pytest.mark.parametrize("endpoint", ["create", "execute"])
@pytest.mark.parametrize("failure", ["hash", "baseline", "confirmation", "integrity", "expired"])
def test_rejected_submission_cleans_new_copy_and_preserves_original_error(context, monkeypatch, endpoint, failure):
    confirmation = token(context) if failure == "expired" else None
    if confirmation:
        claims = jwt.decode(confirmation, tasks.SECRET_KEY, algorithms=[tasks.ALGORITHM])
        claims["exp"] = 1
        confirmation = jwt.encode(claims, tasks.SECRET_KEY, algorithm=tasks.ALGORITHM)
    prepare_download(context, monkeypatch)
    if failure == "baseline":
        download = tasks._download_repository_artifact_to_local_storage
        def remove_baseline_after_download(db, repo, user):
            repo = download(db, repo, user)
            db.query(BurningTask).update({"status": 3})
            db.commit()
            return repo
        monkeypatch.setattr(tasks, "_download_repository_artifact_to_local_storage", remove_baseline_after_download)
    with context.sessions() as db:
        if failure == "integrity":
            db.get(Repository, context.repo_id).sha256 = "b" * 64
            config = json.loads(context.payload["config_json"])
            config["version_check"] = False
            context.payload["config_json"] = json.dumps(config)
            source = db.query(BurningTask).first()
            source.config_json = context.payload["config_json"]
            source.version_check = 0
        db.commit()
    if failure == "hash":
        def fail(*args):
            raise OSError("cannot read")
        monkeypatch.setattr(tasks, "_compute_decrypted_artifact_hashes", fail)
    path = "/api/tasks" if endpoint == "create" else "/api/tasks/1/execute"
    payload = {**context.payload, "override_token": confirmation} if endpoint == "create" else {"override_token": confirmation}
    response = context.client.post(path, json=payload)
    assert response.status_code == (400 if failure in {"hash", "integrity"} else 409), response.text
    assert not context.file.exists()
    with context.sessions() as db:
        repo = db.get(Repository, context.repo_id)
        state = tasks._get_repository_location_state(repo, json.loads(repo.file_detail_json))
        assert not state["local_exists"] and not state["local_path"]
        assert db.query(BurningTask).count() == 1


@pytest.mark.parametrize("protection", ["existing", "retained", "shared"])
def test_submission_failure_does_not_delete_protected_copy(context, monkeypatch, protection):
    if protection != "existing":
        prepare_download(context, monkeypatch, keep=protection == "retained", shared=protection == "shared")
    response = context.client.post("/api/tasks", json=context.payload)
    assert response.status_code == 409, response.text
    assert context.file.read_bytes() == b"new firmware"
    with context.sessions() as db:
        repo = db.get(Repository, context.repo_id)
        assert tasks._get_repository_location_state(repo, json.loads(repo.file_detail_json))["local_exists"]


@pytest.mark.parametrize("silent", [False, True])
def test_cleanup_failure_keeps_original_validation_error_and_location(context, monkeypatch, silent):
    prepare_download(context, monkeypatch)
    def fail(*args):
        if silent:
            return
        raise OSError("cannot remove")
    monkeypatch.setattr(tasks, "_remove_repository_file_by_path", fail)
    response = context.client.post("/api/tasks", json=context.payload)
    assert response.status_code == 409, response.text
    assert "版本一致性比对失败" in response.json()["detail"]
    assert context.file.exists()
    with context.sessions() as db:
        repo = db.get(Repository, context.repo_id)
        assert tasks._get_repository_location_state(repo, json.loads(repo.file_detail_json))["local_exists"]


def test_replaced_repository_copy_is_not_deleted(context, monkeypatch):
    prepare_download(context, monkeypatch)
    replacement = context.file.parent / "replacement.app"
    replacement.write_bytes(b"another submission")
    original = tasks._validate_submit_stage_consistency_override
    def reject(task, config, checks, user, confirmation):
        with context.sessions() as db:
            repo = db.get(Repository, context.repo_id)
            tasks._apply_repository_location_state(
                repo, {}, local_exists=True, local_path=str(replacement),
                server_exists=False, server_path=None, server_target=None,
            )
            db.commit()
        return original(task, config, checks, user, confirmation)
    monkeypatch.setattr(tasks, "_validate_submit_stage_consistency_override", reject)
    response = context.client.post("/api/tasks", json=context.payload)
    assert response.status_code == 409, response.text
    assert replacement.read_bytes() == b"another submission"
