from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request


def _get_agent_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "agent.json"


def get_agent_shared_token() -> str:
    env_token = str(os.environ.get("PCIDS_AGENT_TOKEN") or "").strip()
    if env_token:
        return env_token
    try:
        payload = json.loads(_get_agent_config_path().read_text(encoding="utf-8"))
        return str(payload.get("shared_token") or "").strip()
    except Exception:
        return ""


def build_agent_headers() -> dict[str, str]:
    token = get_agent_shared_token()
    return {"X-PCIDS-Agent-Token": token} if token else {}


def require_agent_token(request: Request) -> None:
    expected = get_agent_shared_token()
    supplied = str(request.headers.get("X-PCIDS-Agent-Token") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="下位机 Agent 认证令牌未配置")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="下位机 Agent 认证失败")
