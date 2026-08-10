from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import LicenseEnforcementMiddleware
from backend.routers import business_sync, repositories


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(LicenseEnforcementMiddleware)

    @app.get("/api/example")
    async def protected_endpoint():
        return {"ok": True}

    @app.get("/api/license/status")
    async def license_endpoint():
        return {"ok": True}

    @app.get("/health")
    async def health_endpoint():
        return {"ok": True}

    return app


def test_missing_license_blocks_business_api_but_not_recovery_routes(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("PCIDS_LICENSE_ENFORCEMENT", "1")
    monkeypatch.setenv("PCIDS_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(_make_app())

    blocked = client.get("/api/example")
    license_route = client.get("/api/license/status")
    health = client.get("/health")

    assert blocked.status_code == 403
    assert blocked.json()["code"] == "LICENSE_REQUIRED"
    assert license_route.status_code == 200
    assert health.status_code == 200


def test_disabled_enforcement_allows_business_api(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PCIDS_LICENSE_ENFORCEMENT", "0")
    monkeypatch.setenv("PCIDS_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(_make_app())

    assert client.get("/api/example").status_code == 200


def test_invalid_license_pauses_background_sync_coordinators():
    async def run_scenario():
        invalid_status = {"valid": False, "state": "missing"}
        with patch.object(business_sync, "get_license_status", return_value=invalid_status), patch.object(
            business_sync, "run_sync_once"
        ) as business_cycle, patch.object(
            repositories, "get_license_status", return_value=invalid_status
        ), patch.object(
            repositories, "_list_enabled_repository_sync_projects"
        ) as repository_projects:
            tasks = [
                asyncio.create_task(business_sync.run_business_sync_coordinator()),
                asyncio.create_task(repositories.run_repository_data_sync_coordinator()),
            ]
            await asyncio.sleep(0.05)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        business_cycle.assert_not_called()
        repository_projects.assert_not_called()

    asyncio.run(run_scenario())
