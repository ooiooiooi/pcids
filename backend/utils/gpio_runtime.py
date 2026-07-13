from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional


class GpioRuntimeConfigError(RuntimeError):
    pass


_DEFAULT_GPIO_LEVEL_LABELS = {
    "高电平": "HIGH",
    "低电平": "LOW",
}

_DEFAULT_GPIO_TRIGGER_LABELS = {
    "上升沿": "RISING",
    "下降沿": "FALLING",
    "双边沿": "BOTH",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_runtime_paths() -> list[Path]:
    env_value = str(os.environ.get("PCIDS_GPIO_RUNTIME_CONFIG") or "").strip()
    paths: list[Path] = []
    if env_value:
        paths.append(Path(env_value).expanduser())
    project_root = _project_root()
    paths.append(project_root / "backend" / "config" / "gpio_runtime.json")
    paths.append(project_root / "config" / "gpio_runtime.json")
    return paths


def _deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return deepcopy(override)
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_gpio_runtime_profile() -> dict[str, Any]:
    for path in _candidate_runtime_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GpioRuntimeConfigError(f"GPIO 真实业务配置文件解析失败：{path}，{exc}") from exc
        if not isinstance(data, dict):
            raise GpioRuntimeConfigError(f"GPIO 真实业务配置文件格式无效：{path}")
        enabled = data.get("enabled")
        if enabled is False:
            raise GpioRuntimeConfigError(f"GPIO 真实业务配置已禁用：{path}")
        data["_config_path"] = str(path)
        return data
    raise GpioRuntimeConfigError(
        "未找到 GPIO 真实业务配置，请提供 PCIDS_GPIO_RUNTIME_CONFIG，或创建 backend/config/gpio_runtime.json"
    )


def _normalize_pin_options(profile: dict[str, Any]) -> list[str]:
    candidates = profile.get("channel_options") or profile.get("pins_order")
    normalized: list[str] = []
    if isinstance(candidates, list):
        for item in candidates:
            text = str(item or "").strip()
            if text:
                normalized.append(text)
    if normalized:
        return normalized
    pins = profile.get("pins")
    if isinstance(pins, dict):
        keys = [str(key).strip() for key in pins.keys() if str(key or "").strip()]
        if keys:
            return sorted(keys, key=lambda item: (len(item), item))
    return [f"GPIO{index}" for index in range(16)]


def build_gpio_auto_config(profile: dict[str, Any]) -> dict[str, Any]:
    defaults = profile.get("defaults") if isinstance(profile.get("defaults"), dict) else {}
    transport = profile.get("transport") if isinstance(profile.get("transport"), dict) else {}
    return {
        "method": "gpio",
        "pin": str(defaults.get("pin") or "GPIO0"),
        "channel_options": _normalize_pin_options(profile),
        "mode": str(defaults.get("mode") or "输出"),
        "target_level": str(defaults.get("target_level") or "高电平"),
        "pull_mode": str(defaults.get("pull_mode") or "无 (浮空)"),
        "expected_level": str(defaults.get("expected_level") or "高电平"),
        "current_level": str(defaults.get("current_level") or ""),
        "trigger_type": str(defaults.get("trigger_type") or "上升沿"),
        "timeout_ms": int(defaults.get("timeout_ms") or 5000),
        "gpio_runtime_profile": profile,
        "gpio_transport_kind": str(transport.get("kind") or "").strip().lower(),
        "gpio_transport_config": deepcopy(transport),
        "supports_readback": bool(profile.get("supports_readback", True)),
    }


def build_gpio_action_context(pin: str, runtime_config: dict[str, Any]) -> dict[str, Any]:
    pin_text = str(pin or "").strip()
    match = re.search(r"(\d+)$", pin_text)
    pin_number = int(match.group(1)) if match else 0
    target_level = str(runtime_config.get("target_level") or "高电平").strip()
    trigger_type = str(runtime_config.get("trigger_type") or "上升沿").strip()
    return {
        "pin": pin_text,
        "pin_number": pin_number,
        "target_level": target_level,
        "target_level_en": _DEFAULT_GPIO_LEVEL_LABELS.get(target_level, target_level.upper()),
        "target_level_key": "high" if target_level == "高电平" else "low",
        "expected_level": str(runtime_config.get("expected_level") or "不判定").strip(),
        "pull_mode": str(runtime_config.get("pull_mode") or "无 (浮空)").strip(),
        "trigger_type": trigger_type,
        "trigger_type_en": _DEFAULT_GPIO_TRIGGER_LABELS.get(trigger_type, trigger_type.upper()),
        "timeout_ms": int(runtime_config.get("timeout_ms") or 5000),
    }


def resolve_gpio_action_profile(profile: dict[str, Any], pin: str, action: str, context: dict[str, Any]) -> dict[str, Any]:
    shared_actions = profile.get("actions") if isinstance(profile.get("actions"), dict) else {}
    pins = profile.get("pins") if isinstance(profile.get("pins"), dict) else {}
    pin_config = pins.get(pin) if isinstance(pins.get(pin), dict) else {}
    pin_actions = pin_config.get("actions") if isinstance(pin_config.get("actions"), dict) else {}
    resolved = _deep_merge(shared_actions.get(action, {}) if isinstance(shared_actions.get(action), dict) else {}, pin_actions.get(action, {}) if isinstance(pin_actions.get(action), dict) else {})
    levels = resolved.pop("levels", None)
    if action == "set_level" and isinstance(levels, dict):
        resolved = _deep_merge(resolved, levels.get(str(context.get("target_level_key") or ""), {}) if isinstance(levels.get(str(context.get("target_level_key") or "")), dict) else {})
    triggers = resolved.pop("triggers", None)
    if action == "listen" and isinstance(triggers, dict):
        trigger_value = str(context.get("trigger_type_en") or "").strip().lower()
        resolved = _deep_merge(resolved, triggers.get(trigger_value, {}) if isinstance(triggers.get(trigger_value), dict) else {})
    return resolved


def render_gpio_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**context)
        except Exception:
            return value
    if isinstance(value, list):
        return [render_gpio_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_gpio_template(item, context) for key, item in value.items()}
    return value


def gpio_pattern_matches(pattern: Any, text: str) -> bool:
    if pattern is None:
        return True
    if isinstance(pattern, str) and not pattern.strip():
        return True
    if isinstance(pattern, list) and not pattern:
        return True
    content = str(text or "")
    if isinstance(pattern, list):
        return any(gpio_pattern_matches(item, content) for item in pattern)
    candidate = str(pattern or "").strip()
    if not candidate:
        return True
    try:
        return re.search(candidate, content, flags=re.IGNORECASE) is not None
    except re.error:
        return candidate.lower() in content.lower()


def detect_gpio_level_from_text(text: str, reply: Optional[dict[str, Any]] = None) -> Optional[str]:
    content = str(text or "")
    reply_cfg = reply if isinstance(reply, dict) else {}
    high_pattern = reply_cfg.get("high_pattern") or ["高电平", r"\bHIGH\b"]
    low_pattern = reply_cfg.get("low_pattern") or ["低电平", r"\bLOW\b"]
    if gpio_pattern_matches(high_pattern, content):
        return "高电平"
    if gpio_pattern_matches(low_pattern, content):
        return "低电平"
    return None
