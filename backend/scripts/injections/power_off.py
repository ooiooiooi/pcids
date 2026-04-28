import sys
import json
import time

def main():
    if len(sys.argv) < 3:
        print("用法: python power_off.py <target> <config_json>")
        sys.exit(1)
    
    target = sys.argv[1]
    config_json = sys.argv[2]
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError:
        config = {}
    
    duration = config.get('duration', 5)
    strategy = config.get('strategy', 'auto')
    
    print(f"[INFO] 目标设备: {target}")
    print(f"[INFO] 配置参数: 持续时长 {duration}秒, 恢复策略: {strategy}")
    print(f"[EXEC] 正在连接程控电源/继电器以切断 {target} 的供电...")
    
    time.sleep(1)
    print("[EXEC] 断电指令发送成功，设备已断电。")
    
    if strategy == 'auto':
        try:
            wait_time = int(duration)
        except ValueError:
            wait_time = 5
        print(f"[WAIT] 等待 {wait_time} 秒后自动恢复供电...")
        time.sleep(wait_time)
        
        print("[EXEC] 正在发送恢复供电指令...")
        time.sleep(1)
        print("[INFO] 供电已恢复。断电模拟任务执行完成。")
    else:
        print("[INFO] 已配置为手动恢复，系统保持断电状态。")

    sys.exit(0)

if __name__ == '__main__':
    main()
