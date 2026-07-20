"""PCIDS local flash adapter for CodeArts Build Windows agents.

The adapter deliberately invokes only PCIDS system burner scripts from
``SYSTEM_SCRIPT_CATALOG``.  It is therefore a stable automation surface for
CI while preserving the existing PCIDS burner implementations and their
parameter validation inside the generated scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.utils.burner_automation import SYSTEM_SCRIPT_CATALOG, build_system_script_content
from backend.utils.app_paths import get_app_data_root
from backend.utils.task_execution import build_runtime_env, validate_script_execution_config


EXIT_INVALID_REQUEST = 2
EXIT_EXECUTION_FAILED = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventLogger:
    """Emit JSON Lines to stdout and keep both JSON and readable local logs."""

    def __init__(self, log_dir: Path, run_id: str) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.text_path = log_dir / f"pcids-flash-{run_id}.log"
        self.jsonl_path = log_dir / f"pcids-flash-{run_id}.jsonl"

    def emit(self, event: str, *, status: str = "info", message: str = "", **details: Any) -> None:
        payload = {
            "time": _utc_now(),
            "event": event,
            "status": status,
            "message": message,
            **{key: value for key, value in details.items() if value not in (None, "")},
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        print(encoded, flush=True)
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        with self.text_path.open("a", encoding="utf-8") as handle:
            detail = " ".join(f"{key}={value}" for key, value in payload.items() if key not in {"time", "event", "status", "message"})
            handle.write(f"[{payload['time']}] [{status}] {event}: {message}{(' | ' + detail) if detail else ''}\n")


def _catalog_by_name() -> dict[str, dict[str, Any]]:
    return {str(item["name"]): item for item in SYSTEM_SCRIPT_CATALOG}


def _read_profile_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"profile file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("profile file root must be a JSON object")
    profiles = value.get("profiles", value)
    if not isinstance(profiles, dict):
        raise ValueError("profiles must be a JSON object")
    return profiles


def _parse_json_object(raw: str, argument: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{argument} must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{argument} must be a JSON object")
    return value


def _resolve_profile(profile_name: str, profile_file: Path, overrides: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    catalog = _catalog_by_name()
    profiles = _read_profile_file(profile_file)
    raw_profile = profiles.get(profile_name, {})
    if raw_profile and not isinstance(raw_profile, dict):
        raise ValueError(f"profile '{profile_name}' must be a JSON object")
    raw_profile = dict(raw_profile or {})
    script_name = str(raw_profile.get("script") or profile_name).strip()
    item = catalog.get(script_name)
    if not item:
        raise ValueError(f"unknown profile/script '{profile_name}'. Use list-profiles to see supported system scripts.")
    config = dict(item.get("default_config") or {})
    profile_config = raw_profile.get("config", {})
    if profile_config and not isinstance(profile_config, dict):
        raise ValueError(f"profile '{profile_name}'.config must be a JSON object")
    config.update(profile_config or {})
    config.update(overrides)
    return item, config, str(raw_profile.get("description") or "")


def _build_env(item: dict[str, Any], config: dict[str, Any], firmware: Path, run_id: str) -> dict[str, str]:
    script = SimpleNamespace(
        id=0,
        name=item["name"],
        type=item.get("type", "bat"),
        default_config_json=json.dumps(item.get("default_config") or {}, ensure_ascii=False),
    )
    # Reuse PCIDS's current configuration contract.  The generated burner
    # script remains the single implementation of erase/write/verify/reset.
    normalized = validate_script_execution_config(config, script, artifact_name=firmware.name)
    burner_config = normalized.pop("burner", {}) if isinstance(normalized.get("burner"), dict) else {}
    burner = SimpleNamespace(
        id=burner_config.get("id", ""),
        name=str(burner_config.get("name") or item.get("burner") or ""),
        type=str(burner_config.get("type") or item.get("burner") or ""),
        sn=str(burner_config.get("sn") or normalized.pop("burner_sn", "") or ""),
        port=str(burner_config.get("port") or normalized.pop("burner_port", "") or ""),
        location=str(burner_config.get("location") or normalized.pop("burner_location", "") or ""),
    )
    task = SimpleNamespace(
        id=run_id,
        target_ip="",
        target_port="",
        repository_id="",
        board_name=str(normalized.get("board_name") or ""),
        product_id="",
        burner_id=burner.id,
        task_type=item.get("task_type", "board"),
    )
    env = build_runtime_env(task, normalized, None, burner, script, str(firmware))
    env["PCIDS_RUN_ID"] = run_id
    env["PCIDS_PROFILE"] = item["name"]
    return env


def _run(args: argparse.Namespace) -> int:
    firmware = Path(args.firmware).expanduser().resolve(strict=False)
    if not firmware.is_file():
        print(json.dumps({"event": "failed", "status": "error", "code": "FIRMWARE_NOT_FOUND", "message": f"firmware not found: {firmware}"}, ensure_ascii=False))
        return EXIT_INVALID_REQUEST

    run_id = str(args.run_id or uuid.uuid4().hex).strip()
    if not run_id.replace("-", "").replace("_", "").isalnum():
        print(json.dumps({"event": "failed", "status": "error", "code": "INVALID_RUN_ID", "message": "run-id allows only letters, numbers, hyphen and underscore"}, ensure_ascii=False))
        return EXIT_INVALID_REQUEST
    logger = EventLogger(Path(args.log_dir or (get_app_data_root() / "logs" / "codearts")).expanduser(), run_id)
    try:
        item, config, description = _resolve_profile(args.profile, Path(args.profile_file), _parse_json_object(args.config_json, "--config-json"))
        env = _build_env(item, config, firmware, run_id)
    except Exception as exc:
        logger.emit("failed", status="error", message=str(exc), code="INVALID_PROFILE")
        return EXIT_INVALID_REQUEST

    logger.emit("started", profile=item["name"], firmware=str(firmware), run_id=run_id, description=description, text_log=str(logger.text_path), json_log=str(logger.jsonl_path))
    if args.dry_run:
        logger.emit("completed", status="success", message="dry run completed", profile=item["name"])
        return 0

    # The CodeArts adapter is intentionally a local Windows burner surface.
    # Hybrid network/serial workflows have their own PCIDS task executor and
    # must not be accidentally fed to cmd.exe as if they were USB/JTAG tools.
    if str(item.get("task_type") or "board") == "hybrid" or str(item.get("type") or "").lower() != "bat":
        logger.emit(
            "failed",
            status="error",
            code="HYBRID_WORKFLOW_REQUIRED",
            message="this profile is a PCIDS hybrid workflow; use the PCIDS task API/executor instead of the local CodeArts burner adapter",
        )
        return EXIT_INVALID_REQUEST

    if os.name != "nt":
        logger.emit("failed", status="error", code="WINDOWS_REQUIRED", message="PCIDS CodeArts local burner adapter requires Windows")
        return EXIT_INVALID_REQUEST

    script_content = build_system_script_content(item["name"], str(item.get("burner") or ""))
    if not script_content.strip():
        logger.emit("failed", status="error", code="SCRIPT_UNAVAILABLE", message=f"no local system script for {item['name']}")
        return EXIT_INVALID_REQUEST

    with tempfile.TemporaryDirectory(prefix="pcids-codearts-") as temp_dir:
        script_path = Path(temp_dir) / f"{item['name']}.bat"
        script_path.write_text(script_content, encoding="utf-8-sig", newline="\r\n")
        process_env = os.environ.copy()
        process_env.update({key: str(value) for key, value in env.items()})
        logger.emit("script-start", profile=item["name"], script=str(script_path), burner=env.get("BURNER_NAME"))
        process = subprocess.Popen(
            ["cmd.exe", "/d", "/s", "/c", f'call "{script_path}"'],
            cwd=str(PROJECT_ROOT),
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if text:
                logger.emit("tool-output", message=text)
        return_code = process.wait()

    if return_code == 0:
        logger.emit("completed", status="success", message="flash completed", exit_code=0)
        return 0
    logger.emit("failed", status="error", code="BURNER_FAILED", message="burner script returned non-zero", exit_code=return_code)
    return return_code if 0 < return_code < 256 else EXIT_EXECUTION_FAILED


def _list_profiles(args: argparse.Namespace) -> int:
    try:
        profiles = _read_profile_file(Path(args.profile_file))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return EXIT_INVALID_REQUEST
    for item in SYSTEM_SCRIPT_CATALOG:
        name = str(item["name"])
        profile = profiles.get(name, {})
        print(json.dumps({"profile": name, "burner": item.get("burner"), "task_type": item.get("task_type", "board"), "configured": bool(profile)}, ensure_ascii=False))
    for name, profile in profiles.items():
        if name not in _catalog_by_name() and isinstance(profile, dict):
            print(json.dumps({"profile": name, "script": profile.get("script"), "configured": True}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PCIDS local flash adapter for CodeArts Build Windows agents")
    parser.add_argument(
        "--profile-file",
        default=os.environ.get("PCIDS_FLASH_PROFILE_FILE", str(get_app_data_root() / "codearts_flash_profiles.json")),
        help="JSON profile file path",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-profiles", help="list supported PCIDS system burner profiles")
    run_parser = subparsers.add_parser("run", help="run one PCIDS system burner profile")
    run_parser.add_argument("--profile", required=True, help="profile name or PCIDS system script name")
    run_parser.add_argument("--firmware", required=True, help="firmware file downloaded by CodeArts Artifact step")
    run_parser.add_argument("--config-json", default="{}", help="JSON object with profile runtime overrides")
    run_parser.add_argument("--run-id", default="", help="CodeArts build/run identifier")
    run_parser.add_argument("--log-dir", default="", help="directory for .log and .jsonl output")
    run_parser.add_argument("--dry-run", action="store_true", help="validate profile and emit logs without touching a burner")
    args = parser.parse_args(argv)
    return _list_profiles(args) if args.command == "list-profiles" else _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
