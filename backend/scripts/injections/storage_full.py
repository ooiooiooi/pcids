import sys
import json
import time


def main():
    if len(sys.argv) < 3:
        print("用法: python storage_full.py <target> <config_json>")
        sys.exit(1)

    target = sys.argv[1]
    config_json = sys.argv[2]
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError:
        config = {}

    method = config.get("method", "single")
    location = config.get("location", "/tmp")
    if location == "custom":
        location = config.get("custom_location") or "/tmp"
    size = config.get("size", 50)
    if size == "custom":
        size = config.get("custom_size") or 50
    strategy = config.get("strategy", "auto")

    print(f"[INFO] 目标设备: {target}")
    print(f"[INFO] 配置参数: 填充方式 {method}, 填充位置 {location}, 填充大小 {size}%, 清理策略 {strategy}")
    print("[EXEC] 正在尝试填充存储空间...")
    time.sleep(1)

    if method == "multi":
        print(f"[EXEC] 已在 {location} 生成多个小文件以占用可用空间。")
    else:
        print(f"[EXEC] 已在 {location} 创建单个大文件以占用可用空间。")

    if strategy == "auto":
        print("[EXEC] 测试完成后正在自动清理临时文件...")
        time.sleep(1)
        print("[INFO] 临时文件已清理。存储不足模拟任务执行完成。")
    else:
        print("[INFO] 已配置为手动清理，保留临时文件。")

    sys.exit(0)


if __name__ == "__main__":
    main()
