"""Peer API and background coordinator for complete business-data sync."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.parse
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.models import BusinessSyncChange, BusinessSyncPeer, BusinessSyncSnapshot, BusinessSyncState, User
from backend.routers.repositories import (
    _get_repository_data_sync_config,
    _get_repository_server_instance_id,
    _repository_peer_request_json,
)
from backend.routers.users import get_current_user
from backend.utils.business_data_sync import (
    apply_canonical_states,
    apply_changes_to_authority,
    business_sync_status,
    capture_local_business_changes,
    change_wire_payload,
    current_server_revision,
    mark_push_results,
    publish_authoritative_changes,
    state_wire_payload,
)
from backend.utils.db import SessionLocal, get_db
from backend.utils.license_manager import get_license_status
from backend.utils.repository_data_sync import (
    get_repository_sync_node_id,
    require_repository_sync_request,
)


router = APIRouter()
logger = logging.getLogger(__name__)
_PROTOCOL_VERSION = 1
_wake_event: asyncio.Event | None = None
_sync_lock = asyncio.Lock()
_server_lock = threading.RLock()
_last_server_capture_at = 0.0


def _business_batch_size(config: dict) -> int:
    # Script bodies and task logs can be large.  Keep HTTP batches bounded even
    # when repository metadata sync is configured for a larger batch.
    return min(max(int(config.get("batch_size") or 100), 1), 100)


def _publish_server_changes(db: Session, *, batch_size: int, force_capture: bool = False):
    global _last_server_capture_at
    now = time.monotonic()
    should_capture = force_capture or now - _last_server_capture_at >= 5.0
    result = publish_authoritative_changes(db, batch_size=batch_size, capture=should_capture)
    if should_capture:
        _last_server_capture_at = time.monotonic()
    return result


def _require_server() -> dict:
    config = _get_repository_data_sync_config()
    if not config.get("enabled") or config.get("role") != "server":
        raise HTTPException(status_code=409, detail="当前实例不是业务数据同步服务器")
    return config


def _peer_path(path: str) -> str:
    return f"/api/business-sync/v1{path}"


@router.get("/v1/health")
async def peer_health(request: Request, db: Session = Depends(get_db)):
    require_repository_sync_request(request)
    _require_server()
    instance_id = _get_repository_server_instance_id(db)
    db.commit()
    return {
        "code": 0,
        "message": "success",
        "data": {
            "protocol_version": _PROTOCOL_VERSION,
            "node_id": get_repository_sync_node_id(),
            "server_instance_id": instance_id,
            "server_revision": current_server_revision(db),
        },
    }


@router.post("/v1/push")
async def peer_push(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    context = require_repository_sync_request(request)
    config = _require_server()
    if int(payload.get("protocol_version") or 0) != _PROTOCOL_VERSION:
        raise HTTPException(status_code=409, detail="业务同步协议版本不兼容")
    if str(payload.get("node_id") or "") != str(context["origin_node_id"]):
        raise HTTPException(status_code=400, detail="同步节点标识与请求头不一致")
    changes = payload.get("changes") if isinstance(payload.get("changes"), list) else []
    batch_size = _business_batch_size(config)
    if not changes or len(changes) > batch_size:
        raise HTTPException(status_code=400, detail=f"同步批次必须包含 1 到 {batch_size} 条变更")
    try:
        with _server_lock:
            # Local server writes always become authoritative before peer writes.
            _publish_server_changes(db, batch_size=batch_size)
            results = apply_changes_to_authority(
                db,
                origin_node_id=str(context["origin_node_id"]),
                changes=changes,
            )
            canonical = [item["canonical"] for item in results if isinstance(item.get("canonical"), dict)]
            _, errors = apply_canonical_states(db, canonical)
            if errors:
                raise RuntimeError("；".join(errors))
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("business_sync.peer_push_failed")
        raise HTTPException(status_code=500, detail=f"应用业务数据失败：{exc}") from exc
    return {
        "code": 0,
        "message": "success",
        "data": {
            "protocol_version": _PROTOCOL_VERSION,
            "server_node_id": get_repository_sync_node_id(),
            "server_instance_id": _get_repository_server_instance_id(db),
            "server_revision": current_server_revision(db),
            "results": results,
        },
    }


@router.get("/v1/pull")
async def peer_pull(
    request: Request,
    after_revision: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    require_repository_sync_request(request)
    config = _require_server()
    effective_limit = min(limit, _business_batch_size(config))
    with _server_lock:
        _publish_server_changes(db, batch_size=effective_limit)
        rows = (
            db.query(BusinessSyncState)
            .filter(BusinessSyncState.revision > after_revision)
            .order_by(BusinessSyncState.revision.asc())
            .limit(effective_limit + 1)
            .all()
        )
        page = rows[:effective_limit]
    return {
        "code": 0,
        "message": "success",
        "data": {
            "protocol_version": _PROTOCOL_VERSION,
            "server_node_id": get_repository_sync_node_id(),
            "server_instance_id": _get_repository_server_instance_id(db),
            "server_revision": current_server_revision(db),
            "next_revision": max([int(row.revision) for row in page] or [after_revision]),
            "has_more": len(rows) > effective_limit,
            "states": [state_wire_payload(row) for row in page],
        },
    }


def run_sync_once() -> dict:
    config = _get_repository_data_sync_config()
    db = SessionLocal()
    try:
        if not config.get("enabled"):
            return {"role": "disabled"}
        if config.get("role") != "client":
            with _server_lock:
                uploaded, conflicts, failed = _publish_server_changes(
                    db,
                    batch_size=_business_batch_size(config),
                    force_capture=True,
                )
            return {"role": config.get("role"), "uploaded": uploaded, "conflicts": conflicts, "failed": failed}

        capture_local_business_changes(db)
        health = _repository_peer_request_json(
            config,
            _peer_path("/health"),
            method="GET",
            timeout_seconds=float(config.get("connect_timeout_seconds") or 3),
        )
        if int(health.get("protocol_version") or 0) != _PROTOCOL_VERSION:
            raise RuntimeError("业务同步协议版本不兼容")
        server_node_id = str(health.get("node_id") or "")
        server_instance_id = str(health.get("server_instance_id") or server_node_id)
        if not server_node_id or server_node_id == get_repository_sync_node_id():
            raise RuntimeError("业务同步服务器节点配置无效")
        server_url = str(config.get("server_base_url") or "")
        peer = db.query(BusinessSyncPeer).filter(BusinessSyncPeer.server_base_url == server_url).first()
        if not peer:
            peer = BusinessSyncPeer(server_base_url=server_url, pulled_revision=0)
            db.add(peer)
            db.flush()
        server_changed = bool(peer.server_instance_id and peer.server_instance_id != server_instance_id)
        if server_changed:
            peer.pulled_revision = 0
            # Re-bootstrap against the replacement/restored authority. Old
            # revisions are meaningful only inside the previous server epoch.
            db.query(BusinessSyncState).delete(synchronize_session=False)
            db.query(BusinessSyncSnapshot).delete(synchronize_session=False)
            db.query(BusinessSyncChange).filter(
                BusinessSyncChange.status == "pending"
            ).update({BusinessSyncChange.base_revision: 0}, synchronize_session=False)
        peer.server_instance_id = server_instance_id
        db.add(peer)
        db.commit()
        if server_changed:
            capture_local_business_changes(db)

        totals = [0, 0, 0]
        batch_size = _business_batch_size(config)
        while True:
            rows = (
                db.query(BusinessSyncChange)
                .filter(BusinessSyncChange.status == "pending")
                .order_by(BusinessSyncChange.id.asc())
                .limit(batch_size)
                .all()
            )
            if not rows:
                break
            response = _repository_peer_request_json(
                config,
                _peer_path("/push"),
                payload={
                    "protocol_version": _PROTOCOL_VERSION,
                    "node_id": get_repository_sync_node_id(),
                    "changes": [change_wire_payload(row) for row in rows],
                },
            )
            if str(response.get("server_instance_id") or "") != server_instance_id:
                raise RuntimeError("业务同步期间服务器实例发生变化")
            results = response.get("results") if isinstance(response.get("results"), list) else []
            if {item.change_uuid for item in rows} != {str(item.get("change_uuid") or "") for item in results}:
                raise RuntimeError("业务同步服务器未完整确认本批次")
            counts = mark_push_results(db, results)
            totals = [a + b for a, b in zip(totals, counts)]
            db.commit()

        downloaded = 0
        while True:
            after_revision = int(peer.pulled_revision or 0)
            query = urllib.parse.urlencode({"after_revision": after_revision, "limit": batch_size})
            response = _repository_peer_request_json(config, f"{_peer_path('/pull')}?{query}", method="GET")
            if str(response.get("server_instance_id") or "") != server_instance_id:
                raise RuntimeError("拉取期间业务同步服务器实例发生变化")
            states = response.get("states") if isinstance(response.get("states"), list) else []
            count, errors = apply_canonical_states(db, states)
            if errors:
                raise RuntimeError("；".join(errors))
            downloaded += count
            peer = db.query(BusinessSyncPeer).filter(BusinessSyncPeer.server_base_url == server_url).one()
            peer.pulled_revision = max(after_revision, int(response.get("next_revision") or after_revision))
            peer.last_success_at = datetime.utcnow()
            peer.last_error = None
            db.add(peer)
            db.commit()
            if not response.get("has_more"):
                break
        return {
            "role": "client",
            "uploaded": totals[0],
            "conflicts": totals[1],
            "failed": totals[2],
            "downloaded": downloaded,
            "server_revision": int(health.get("server_revision") or 0),
        }
    except Exception as exc:
        db.rollback()
        server_url = str(config.get("server_base_url") or "")
        if server_url:
            peer = db.query(BusinessSyncPeer).filter(BusinessSyncPeer.server_base_url == server_url).first()
            if peer:
                peer.last_error = str(exc)
                db.add(peer)
                db.commit()
        raise
    finally:
        db.close()


@router.get("/status")
async def get_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    del current_user
    config = _get_repository_data_sync_config()
    capture_local_business_changes(db)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "enabled": bool(config.get("enabled")),
            "role": config.get("role"),
            "server_url": config.get("server_base_url") or None,
            **business_sync_status(db, str(config.get("server_base_url") or "")),
        },
    }


@router.post("/trigger")
async def trigger_sync(current_user: User = Depends(get_current_user)):
    del current_user
    try:
        result = await asyncio.to_thread(run_sync_once)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"code": 0, "message": "同步完成", "data": result}


async def run_business_sync_coordinator() -> None:
    global _wake_event
    wake = asyncio.Event()
    _wake_event = wake
    try:
        while True:
            license_status = await asyncio.to_thread(get_license_status)
            if not license_status["valid"]:
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                continue
            config = await asyncio.to_thread(_get_repository_data_sync_config)
            interval = max(float(config.get("interval_seconds") or 30), 5.0)
            try:
                async with _sync_lock:
                    await asyncio.to_thread(run_sync_once)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Expected while a client is offline; avoid creating failed jobs.
                logger.warning("business_sync.cycle_unavailable | %s", json.dumps({"error": str(exc)}, ensure_ascii=False))
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        if _wake_event is wake:
            _wake_event = None


def wake_business_sync_coordinator() -> None:
    if _wake_event is not None:
        _wake_event.set()
