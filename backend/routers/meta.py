from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.models.user import User
from backend.routers.auth import get_current_user
from backend.schemas import Response
from backend.utils.db import get_db
from backend.utils.meta_catalog import (
    create_meta_burner,
    create_meta_ide,
    list_meta_burners,
    list_meta_ides,
)
from backend.utils.permission import require_permission


def _meta_error_response(status_code: int, message: str, data: Any = None, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": message, "data": data},
        headers=headers,
    )


class MetaRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original_handler = super().get_route_handler()

        async def custom_handler(request):
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                errors = exc.errors()
                status = 400 if any(item.get("type") == "extra_forbidden" for item in errors) else 422
                return _meta_error_response(status, "请求参数不符合要求", jsonable_encoder(errors))
            except HTTPException as exc:
                return _meta_error_response(int(exc.status_code or 400), str(exc.detail or "请求失败"), headers=exc.headers)

        return custom_handler


router = APIRouter(route_class=MetaRoute)


class MetaIdeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class MetaBurnerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str


@router.get("/ides", response_model=Response)
async def get_meta_ides(
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("meta:view")),
):
    return {"code": 0, "message": "success", "data": list_meta_ides(_db)}


@router.post("/ides", response_model=Response)
async def post_meta_ides(
    payload: MetaIdeCreateRequest,
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("meta:edit")),
):
    return {"code": 0, "message": "success", "data": create_meta_ide(_db, payload.name)}


@router.get("/burners", response_model=Response)
async def get_meta_burners(
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("meta:view")),
):
    return {"code": 0, "message": "success", "data": list_meta_burners(_db)}


@router.post("/burners", response_model=Response)
async def post_meta_burners(
    payload: MetaBurnerCreateRequest,
    _db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    _: None = Depends(require_permission("meta:edit")),
):
    return {"code": 0, "message": "success", "data": create_meta_burner(_db, payload.name, payload.type)}
