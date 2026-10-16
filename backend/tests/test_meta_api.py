from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import meta
from backend.routers.auth import get_current_user
from backend.utils.db import get_db


class _AdminUser:
    username = "admin"

    def get_permissions(self):
        return ["all"]


def _make_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCIDS_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(meta.router, prefix="/api/meta")

    def override_get_db():
        yield None

    def override_get_current_user():
        return _AdminUser()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    return TestClient(app)


def test_get_meta_ides_uses_unified_iar_name(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    response = client.get("/api/meta/ides")

    assert response.status_code == 200
    names = [str(item.get("name") or "") for item in response.json()["data"]]
    assert "IAR Embedded Workbench For Arm" in names
    assert "IAR Embedded Workbench" not in names


def test_post_meta_ide_rejects_case_insensitive_duplicate(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    response = client.post("/api/meta/ides", json={"name": "iar embedded workbench for arm"})

    assert response.status_code == 409
    assert response.json()["message"] == "IDE 名称已存在"


def test_get_meta_burners_exposes_fixed_ten_models_without_swd(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    response = client.get("/api/meta/burners")

    assert response.status_code == 200
    data = response.json()["data"]
    names = [str(item.get("name") or "") for item in data]
    assert len(data) == 10
    assert "SWD下载器" not in names
    assert "J-LINK" in names
    assert "Gowin USB Cable" in names


def test_post_meta_burner_rejects_invalid_type_with_standard_error_body(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    response = client.post("/api/meta/burners", json={"name": "legacy burner", "type": "SWD下载器"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert "固定 10 种" in body["message"]


def test_post_meta_burner_rejects_extra_fields_with_standard_error_body(tmp_path: Path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    response = client.post("/api/meta/burners", json={"name": "custom", "type": "J-LINK", "extra": "x"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert body["message"] == "请求参数不符合要求"
    assert isinstance(body["data"], list)
