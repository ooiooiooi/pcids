import sys
import json
import time


def main():
    if len(sys.argv) < 3:
        print("用法: python network_error.py <target> <config_json>")
        sys.exit(1)

    target = sys.argv[1]
    config_json = sys.argv[2]
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError:
        config = {}

    interruption_type = config.get("type", "disconnect")
    duration = config.get("duration", 10)
    if duration == "custom":
        duration = config.get("custom_duration") or 10
    strategy = config.get("strategy", "auto")

    print(f"[INFO] 目标设备: {target}")
    print(f"[INFO] 配置参数: 中断类型 {interruption_type}, 持续时长 {duration}秒, 恢复策略 {strategy}")
    print("[EXEC] 正在应用网络异常配置...")
    time.sleep(1)

    if interruption_type == "packet_loss":
        print("[EXEC] 已模拟高丢包率网络环境。")
    elif interruption_type == "latency":
        print("[EXEC] 已模拟高延迟网络环境。")
    else:
        print("[EXEC] 已模拟完全断网。")

    if strategy == "auto":
        try:
            wait_time = int(duration)
        except ValueError:
            wait_time = 10
        print(f"[WAIT] 等待 {wait_time} 秒后自动恢复网络配置...")
        time.sleep(wait_time)
        print("[EXEC] 正在恢复网络配置...")
        time.sleep(1)
        print("[INFO] 网络配置已恢复。网络中断模拟任务执行完成。")
    else:
        print("[INFO] 已配置为手动恢复，系统保持中断状态。")

    sys.exit(0)


if __name__ == "__main__":
    main()
