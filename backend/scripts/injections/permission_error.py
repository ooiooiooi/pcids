import sys
import json
import time


def main():
    if len(sys.argv) < 3:
        print("用法: python permission_error.py <target> <config_json>")
        sys.exit(1)

    target = sys.argv[1]
    config_json = sys.argv[2]
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError:
        config = {}

    perm_target = config.get("target") or {}
    perm_type = config.get("type") or {}
    scope = config.get("scope") or {}
    duration = config.get("duration") or {}
    recovery = config.get("recovery") or {}
    backup = config.get("backup") or {}

    print(f"[INFO] 目标设备: {target}")
    print(f"[INFO] 配置参数: 变更对象 {perm_target}, 变更类型 {perm_type}, 影响范围 {scope}, 持续时长 {duration}, 恢复策略 {recovery}, 备份策略 {backup}")
    print("[EXEC] 正在执行权限变更...")
    time.sleep(1)
    print("[EXEC] 权限变更已生效。")

    if recovery.get("auto"):
        print("[EXEC] 操作完成后自动恢复原始权限...")
        time.sleep(1)
        print("[INFO] 权限已恢复。权限缺失模拟任务执行完成。")
        sys.exit(0)

    if recovery.get("manual"):
        print("[INFO] 已配置为手动恢复，保持权限变更状态。")
        sys.exit(0)

    print("[INFO] 未配置恢复策略，默认保持权限变更状态。")
    sys.exit(0)


if __name__ == "__main__":
    main()
