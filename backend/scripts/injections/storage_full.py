from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from backend.utils.network_injection import (
    NetworkInjectionConfigError,
    normalize_network_error_config,
    quote_remote,
    run_remote_shell_command,
)


def build_remote_script(config: dict, run_marker: str) -> str:
    location = str(config.get("location") or "/tmp").strip()
    if location == "custom":
        location = str(config.get("custom_location") or "").strip()
    if not location.startswith("/"):
        raise ValueError("填充位置必须是目标服务器上的绝对路径")

    method = str(config.get("method") or "single").strip().lower()
    if method not in {"single", "multi"}:
        raise ValueError("填充方式仅支持 single 或 multi")

    try:
        size_percent = int(config.get("size", 50))
    except Exception as exc:
        raise ValueError("填充大小必须是整数百分比") from exc
    if size_percent < 1 or size_percent > 99:
        raise ValueError("填充大小需在 1-99 之间")

    strategy = str(config.get("strategy") or "auto").strip().lower()
    if strategy not in {"auto", "manual"}:
        raise ValueError("清理策略仅支持 auto 或 manual")

    location_q = quote_remote(location)
    prefix_q = quote_remote(f".pcids_storage_full_{run_marker}")
    return f"""
set -e
LOCATION={location_q}
PREFIX={prefix_q}
METHOD={quote_remote(method)}
SIZE_PERCENT={size_percent}
STRATEGY={quote_remote(strategy)}

mkdir -p "$LOCATION"
if [ ! -d "$LOCATION" ] || [ ! -w "$LOCATION" ]; then
  echo "__PCIDS_STORAGE_ERROR__:目标路径不存在或当前用户不可写: $LOCATION"
  exit 21
fi

AVAILABLE_KB="$(df -Pk "$LOCATION" | awk 'NR==2 {{print $4}}')"
case "$AVAILABLE_KB" in
  ''|*[!0-9]*)
    echo "__PCIDS_STORAGE_ERROR__:无法读取目标路径可用空间: $LOCATION"
    exit 22
    ;;
esac
AVAILABLE_MB=$((AVAILABLE_KB / 1024))
TARGET_MB=$((AVAILABLE_KB * SIZE_PERCENT / 100 / 1024))
if [ "$TARGET_MB" -lt 1 ]; then TARGET_MB=1; fi

cleanup_files() {{
  rm -f -- "$LOCATION/$PREFIX".*.bin
}}
trap 'if [ "$STRATEGY" = "auto" ]; then cleanup_files; fi' EXIT INT TERM

echo "[INFO] 目标服务器路径: $LOCATION"
echo "[INFO] 注入前可用空间: $AVAILABLE_MB MB，计划占用: $TARGET_MB MB ($SIZE_PERCENT%)"
echo "[EXEC] 正在创建存储占用文件..."

if [ "$METHOD" = "multi" ]; then
  EACH_MB=$((TARGET_MB / 5))
  if [ "$EACH_MB" -lt 1 ]; then EACH_MB=1; fi
  INDEX=1
  while [ "$INDEX" -le 5 ]; do
    dd if=/dev/zero of="$LOCATION/$PREFIX.$INDEX.bin" bs=1M count="$EACH_MB" status=none
    INDEX=$((INDEX + 1))
  done
else
  dd if=/dev/zero of="$LOCATION/$PREFIX.1.bin" bs=1M count="$TARGET_MB" status=none
fi

AFTER_KB="$(df -Pk "$LOCATION" | awk 'NR==2 {{print $4}}')"
AFTER_MB=$((AFTER_KB / 1024))
echo "[SUCCESS] 存储占用文件创建完成，注入后可用空间: $AFTER_MB MB"
if [ "$STRATEGY" = "auto" ]; then
  cleanup_files
  trap - EXIT INT TERM
  FINAL_KB="$(df -Pk "$LOCATION" | awk 'NR==2 {{print $4}}')"
  FINAL_MB=$((FINAL_KB / 1024))
  echo "[SUCCESS] 临时占用文件已自动清理，恢复后可用空间: $FINAL_MB MB"
else
  trap - EXIT INT TERM
  echo "[INFO] 手动清理模式：已保留 $LOCATION/$PREFIX.*.bin"
fi
""".strip()


def main() -> None:
    if len(sys.argv) < 3:
        print("用法: python storage_full.py <target> <config_json>")
        raise SystemExit(1)

    target = sys.argv[1]
    try:
        raw_config = json.loads(sys.argv[2])
    except json.JSONDecodeError:
        raw_config = {}

    try:
        ssh_config = normalize_network_error_config(
            raw_config,
            target=target,
            require_interface=False,
        )
        remote_script = build_remote_script(raw_config, str(raw_config.get("run_marker") or os.getpid()))
        return_code, output = run_remote_shell_command(
            ssh_config,
            remote_script,
            timeout_seconds=3600,
        )
    except (NetworkInjectionConfigError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"[ERROR] 存储注入执行异常: {exc}")
        raise SystemExit(1) from exc

    if output:
        print(output)
    raise SystemExit(return_code)


if __name__ == "__main__":
    main()
