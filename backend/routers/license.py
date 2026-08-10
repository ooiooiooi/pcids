from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response as FastAPIResponse

from backend.schemas import Response
from backend.utils.license_manager import (
    LICENSE_MAX_FILE_BYTES,
    LicenseError,
    build_machine_request,
    get_license_status,
    install_license_bytes,
)


router = APIRouter()
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _require_local_client(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if client_host not in _LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="License 管理仅允许在本机操作")


@router.get("/status", response_model=Response)
async def license_status(request: Request):
    _require_local_client(request)
    return {"code": 0, "message": "success", "data": get_license_status()}


@router.get("/machine-request", response_model=Response)
async def machine_request(request: Request):
    _require_local_client(request)
    return {"code": 0, "message": "success", "data": build_machine_request()}


@router.get("/machine-request/download")
async def download_machine_request(request: Request):
    _require_local_client(request)
    content = json.dumps(build_machine_request(), ensure_ascii=False, indent=2).encode("utf-8")
    return FastAPIResponse(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="pcids-machine-request.json"'},
    )


@router.post("/import", response_model=Response)
async def import_license(request: Request, file: UploadFile = File(...)):
    _require_local_client(request)
    content = await file.read(LICENSE_MAX_FILE_BYTES + 1)
    try:
        status = install_license_bytes(content)
    except LicenseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "message": "License 已安装并生效", "data": status}
