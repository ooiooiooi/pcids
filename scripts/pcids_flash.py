"""PCIDS local flash adapter for CodeArts Build Windows agents.

The adapter deliberately invokes only PCIDS system burner scripts from
``SYSTEM_SCRIPT_CATALOG``.  Its normal automation contract is generic:
select a burner (and, only when a burner has multiple workflows, a script),
then pass board/chip parameters with the request. A pipeline never needs a
pre-created per-board profile file.
"""
from __future__ import annotations

import argparse
import json
import locale
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
from backend.utils.runtime_dependencies import configure_bundled_tools
from backend.utils.task_execution import build_runtime_env, validate_script_execution_config


EXIT_INVALID_REQUEST = 2
EXIT_EXECUTION_FAILED = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _console_json(payload: dict[str, Any]) -> None:
    """Write one JSON event without trusting the Windows console code page.

    CodeArts launches the adapter through Git Bash -> cmd.exe -> the frozen
    backend.  In that chain stdout may still report GBK even after a generated
    burner batch switches its own code page to UTF-8.  Keep readable Unicode
    when the active stream can encode it; otherwise emit ASCII JSON escapes.
    Both forms describe exactly the same payload and remain valid JSON Lines.
    """
    readable = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        readable.encode(encoding, errors="strict")
        encoded = readable
    except (LookupError, UnicodeEncodeError):
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    print(encoded, flush=True)


def _decode_tool_output(raw: bytes) -> str:
    """Decode output from Windows burner tools that may mix UTF-8 and GBK."""
    preferred = locale.getpreferredencoding(False)
    encodings = ("utf-8-sig", "gb18030", preferred)
    tried: set[str] = set()
    for encoding in encodings:
        normalized = str(encoding or "").strip().lower()
        if not normalized or normalized in tried:
            continue
        tried.add(normalized)
        try:
            return raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="backslashreplace")


def _batch_command(script_path: Path) -> list[str]:
    """Build a cmd.exe invocation without embedding quotes in one argv item."""
    return ["cmd.exe", "/d", "/s", "/c", "call", str(script_path)]


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
        _console_json(payload)
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
        with self.text_path.open("a", encoding="utf-8") as handle:
            detail = " ".join(f"{key}={value}" for key, value in payload.items() if key not in {"time", "event", "status", "message"})
            handle.write(f"[{payload['time']}] [{status}] {event}: {message}{(' | ' + detail) if detail else ''}\n")


def _catalog_by_name() -> dict[str, dict[str, Any]]:
    return {str(item["name"]): item for item in SYSTEM_SCRIPT_CATALOG}


def _normalize_token(value: str) -> str:
    """Compare burner identifiers without imposing a UI-specific spelling."""
    return "".join(char for char in str(value or "").upper() if char.isalnum())


_BURNER_ALIASES = {
    "PWLINK": "PWLINK2",
    "PWLINKV2": "PWLINK2",
    "STLINKV2": "STLINK",
    "STLINKV3": "STLINK",
}

# CodeArts runs through several Windows console layers.  Keep the adapter's
# common runtime values ASCII; every existing system script already accepts
# these canonical spellings alongside the UI's Chinese labels.
_CODEARTS_RUNTIME_ENV_ALIASES = {
    "ERASE_MODE": {
        "全片擦除": "chip",
        "扇区擦除": "sector",
        "不擦除": "none",
    },
    "COMPLETION_ACTION": {
        "复位运行": "reset-run",
        "仅复位": "reset",
        "不处理": "none",
    },
}


def _resolve_item(burner_name: str, script_name: str) -> dict[str, Any]:
    """Resolve one catalog entry from the generic burner/workflow selector."""
    catalog = _catalog_by_name()
    selected_script = str(script_name or "").strip()
    if selected_script:
        item = catalog.get(selected_script)
        if not item:
            raise ValueError(f"unknown script '{selected_script}'. Use list-burners to see supported workflows.")
        if burner_name and _normalize_token(item.get("burner", "")) != _normalize_token(_BURNER_ALIASES.get(_normalize_token(burner_name), burner_name)):
            raise ValueError(f"script '{selected_script}' does not belong to burner '{burner_name}'")
        return item
    if not burner_name:
        raise ValueError("provide --burner or --script")
    normalized_burner = _normalize_token(burner_name)
    normalized_burner = _normalize_token(_BURNER_ALIASES.get(normalized_burner, normalized_burner))
    candidates = [item for item in SYSTEM_SCRIPT_CATALOG if _normalize_token(item.get("burner", "")) == normalized_burner]
    if not candidates:
        raise ValueError(f"unknown burner '{burner_name}'. Use list-burners to see supported burners.")
    if len(candidates) != 1:
        choices = ", ".join(str(item["name"]) for item in candidates)
        raise ValueError(f"burner '{burner_name}' has multiple workflows; add --script. Choices: {choices}")
    return candidates[0]


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


def _resolve_request(args: argparse.Namespace, overrides: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a request from generic CLI fields only."""
    item = _resolve_item(args.burner, args.script)
    config = dict(item.get("default_config") or {})
    config.update(overrides)
    if args.target_chip:
        config["target_chip"] = args.target_chip
    if args.board:
        config["board_name"] = args.board
    if args.burner_sn:
        config["burner_sn"] = args.burner_sn
    if args.burner_port:
        config["burner_port"] = args.burner_port
    return item, config


def _apply_adapter_defaults(item: dict[str, Any], config: dict[str, Any]) -> None:
    """Fill adapter-owned defaults without changing the original burner script.

    XDS510plus uses the SEED/F28335 ``.ccxml`` that PCIDS deploys alongside
    manually installed burner tools.  Pipelines deliberately leave this field
    blank; board-specific ``.ccxml`` files can still override it explicitly.
    """
    if item.get("name") != "xds510plus_dsp_flash" or str(config.get("target_config_file") or "").strip():
        return

    configured_root = str(os.environ.get("PCIDS_BUNDLED_TOOLS_DIR") or "").strip()
    roots = [Path(configured_root)] if configured_root else []
    install_tools_root = PROJECT_ROOT / "tools" / "burners"
    if install_tools_root not in roots:
        roots.append(install_tools_root)

    relative_path = Path("XDS510plus") / "targets" / "seed_xds510plus_f28335.ccxml"
    candidates = [root / relative_path for root in roots]
    # Preserve a deterministic error path if a deployment omitted the default
    # configuration file: the system script will report that exact path.
    selected = next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])
    config["target_config_file"] = str(selected.resolve(strict=False))


def _build_env(item: dict[str, Any], config: dict[str, Any], firmware: Path, run_id: str) -> dict[str, str]:
    # The CodeArts entrypoint starts the frozen backend in one-shot script mode,
    # so it must discover packaged/vendor tools itself instead of relying on the
    # long-running API service startup path.
    tool_env = configure_bundled_tools()
    _apply_adapter_defaults(item, config)
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
    env.update(tool_env)
    for field, aliases in _CODEARTS_RUNTIME_ENV_ALIASES.items():
        value = str(env.get(field) or "").strip()
        if value in aliases:
            env[field] = aliases[value]
    env["PCIDS_RUN_ID"] = run_id
    env["PCIDS_SCRIPT"] = item["name"]
    return env


def _run(args: argparse.Namespace) -> int:
    firmware = Path(args.firmware).expanduser().resolve(strict=False)
    if not firmware.is_file():
        _console_json({"event": "failed", "status": "error", "code": "FIRMWARE_NOT_FOUND", "message": f"firmware not found: {firmware}"})
        return EXIT_INVALID_REQUEST

    run_id = str(args.run_id or uuid.uuid4().hex).strip()
    if not run_id.replace("-", "").replace("_", "").isalnum():
        _console_json({"event": "failed", "status": "error", "code": "INVALID_RUN_ID", "message": "run-id allows only letters, numbers, hyphen and underscore"})
        return EXIT_INVALID_REQUEST
    logger = EventLogger(Path(args.log_dir or (get_app_data_root() / "logs" / "codearts")).expanduser(), run_id)
    try:
        item, config = _resolve_request(args, _parse_json_object(args.config_json, "--config-json"))
        env = _build_env(item, config, firmware, run_id)
    except Exception as exc:
        logger.emit("failed", status="error", message=str(exc), code="INVALID_REQUEST")
        return EXIT_INVALID_REQUEST

    logger.emit(
        "started",
        script=item["name"],
        burner=item.get("burner"),
        firmware=str(firmware),
        run_id=run_id,
        target_chip=config.get("target_chip") or config.get("chip_model"),
        board=config.get("board_name"),
        burner_sn=config.get("burner_sn") or (config.get("burner") or {}).get("sn"),
        text_log=str(logger.text_path),
        json_log=str(logger.jsonl_path),
    )
    if args.dry_run:
        logger.emit("completed", status="success", message="dry run completed", script=item["name"])
        return 0

    # The CodeArts adapter is intentionally a local Windows burner surface.
    # Hybrid network/serial workflows have their own PCIDS task executor and
    # must not be accidentally fed to cmd.exe as if they were USB/JTAG tools.
    if str(item.get("task_type") or "board") == "hybrid" or str(item.get("type") or "").lower() != "bat":
        logger.emit(
            "failed",
            status="error",
            code="HYBRID_WORKFLOW_REQUIRED",
            message="this workflow is a PCIDS hybrid workflow; use the PCIDS task API/executor instead of the local CodeArts burner adapter",
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
        # cmd.exe parses batch source using the active Windows ANSI code page
        # before it can execute a later ``chcp`` command.  GB18030 is compatible
        # with the common Chinese Windows code page and, unlike UTF-8 BOM, does
        # not turn ``@echo off`` into an unknown command.
        script_path.write_text(script_content, encoding="gb18030", newline="\r\n")
        process_env = os.environ.copy()
        process_env.update({key: str(value) for key, value in env.items()})
        logger.emit("script-start", workflow=item["name"], script=str(script_path), burner=env.get("BURNER_NAME"))
        process = subprocess.Popen(
            _batch_command(script_path),
            cwd=str(PROJECT_ROOT),
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        for line in process.stdout:
            text = _decode_tool_output(line).rstrip("\r\n")
            if text:
                logger.emit("tool-output", message=text)
        return_code = process.wait()

    if return_code == 0:
        logger.emit("completed", status="success", message="flash completed", exit_code=0)
        return 0
    logger.emit("failed", status="error", code="BURNER_FAILED", message="burner script returned non-zero", exit_code=return_code)
    return return_code if 0 < return_code < 256 else EXIT_EXECUTION_FAILED


def _list_burners() -> int:
    """Expose the generic selector contract."""
    for item in SYSTEM_SCRIPT_CATALOG:
        _console_json(
            {
                "burner": item.get("burner"),
                "script": item["name"],
                "task_type": item.get("task_type", "board"),
                "type": item.get("type"),
            }
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PCIDS local flash adapter for CodeArts Build Windows agents")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-burners", help="list generic burner and workflow selectors")
    run_parser = subparsers.add_parser("run", help="run a PCIDS burner workflow")
    run_parser.add_argument("--burner", default="", help="generic burner type, e.g. ST-LINK or PW-LINK")
    run_parser.add_argument("--script", default="", help="PCIDS workflow; required only if the burner has multiple workflows")
    run_parser.add_argument("--target-chip", default="", help="board/chip model for this run")
    run_parser.add_argument("--board", default="", help="optional board model or asset name for traceability")
    run_parser.add_argument("--burner-sn", default="", help="optional connected programmer serial number")
    run_parser.add_argument("--burner-port", default="", help="optional USB port/location selector")
    run_parser.add_argument("--firmware", required=True, help="firmware file downloaded by CodeArts Artifact step")
    run_parser.add_argument("--config-json", default="{}", help="JSON object with workflow-specific parameters")
    run_parser.add_argument("--run-id", default="", help="CodeArts build/run identifier")
    run_parser.add_argument("--log-dir", default="", help="directory for .log and .jsonl output")
    run_parser.add_argument("--dry-run", action="store_true", help="validate request and emit logs without touching a burner")
    args = parser.parse_args(argv)
    if args.command == "list-burners":
        return _list_burners()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
