from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.models import Base, Burner
from backend.utils.meta_catalog import seed_meta_catalog, FIXED_BURNER_MODELS
from backend.main import MetaErrorsMiddleware

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
    app.add_middleware(MetaErrorsMiddleware)
    engine = create_engine(f"sqlite:///{tmp_path / 'meta.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_meta_catalog(db)
        db.add_all([Burner(name=model, type=model) for model in FIXED_BURNER_MODELS])
        db.commit()

    def override_get_db():
        with sessions() as db:
            yield db

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

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 400
    assert body["message"] == "请求参数不符合要求"
    assert isinstance(body["data"], list)


def test_meta_burner_is_persisted_in_existing_devices_table(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post("/api/meta/burners", json={"name": "Lab Probe", "type": "J-LINK"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["supported_interfaces"] == ["SWD", "JTAG", "CJTAG"]
    engine = create_engine(f"sqlite:///{tmp_path / 'meta.db'}")
    with sessionmaker(bind=engine)() as db:
        assert db.query(Burner).filter(Burner.id == data["id"]).one().name == "Lab Probe"
    duplicate = client.post("/api/meta/burners", json={"name": "lab probe", "type": "ST-LINK"})
    assert duplicate.status_code == 409


def test_meta_only_errors_and_missing_permission(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    for url, method, status in [("/api/meta/missing", "get", 404), ("/api/meta/ides", "delete", 405)]:
        response = getattr(client, method)(url)
        assert response.status_code == status
        assert set(response.json()) == {"code", "message", "data"}
    assert client.get("/elsewhere").json() == {"detail": "Not Found"}
    class Viewer(_AdminUser):
        def get_permissions(self):
            return ["meta:view"]
    client.app.dependency_overrides[get_current_user] = lambda: Viewer()
    assert client.get("/api/meta/ides").status_code == 200
    response = client.post("/api/meta/ides", json={"name": "New IDE"})
    assert response.status_code == 403
    assert set(response.json()) == {"code", "message", "data"}


def test_concurrent_meta_burner_creation_is_casefold_unique(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    client = _make_client(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: client.post("/api/meta/burners", json={
            "name": name, "type": "J-LINK",
        }).status_code, ["Probe-Ä", "probe-ä"]))
    assert sorted(results) == [200, 409]
