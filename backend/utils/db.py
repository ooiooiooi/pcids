"""
数据库配置和连接管理
"""
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool
from pathlib import Path
import os


def get_db_path() -> Path:
    """获取数据库文件路径"""
    db_path = os.environ.get('DB_PATH')
    if db_path:
        return Path(db_path)

    # 开发环境默认路径
    if os.name == 'nt':
        base = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
    else:
        base = Path.home() / '.local' / 'share'

    app_dir = base / 'PCIDS'
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / 'app_data.db'


def create_sqlite_engine(db_path: Path):
    """创建 SQLite 数据库引擎，针对 B/S 架构优化"""
    engine = create_engine(
        f'sqlite:///{db_path}',
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,  # B/S架构：连接池
        echo=False,
    )

    # 应用 SQLite PRAGMAs 优化性能
    def set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA cache_size=-20000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    event.listen(engine, "connect", set_pragmas)
    return engine


# 创建数据库引擎
db_path = get_db_path()
engine = create_sqlite_engine(db_path)

# 创建会话工厂
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# 创建线程安全的会话
db_session = scoped_session(lambda: SessionLocal())


def get_db():
    """获取数据库会话依赖"""
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


def init_menus_and_permissions(db):
    """初始化菜单和权限数据"""
    from backend.models.permission import Menu, Permission, RolePermission
    from backend.models.role import Role

    # 检查是否已初始化
    if db.query(Menu).count() > 0:
        return

    print("正在初始化菜单和权限...")

    # ============ 创建菜单 ============
    menus_data = [
        # 一级菜单
        {"id": 1, "name": "工作台", "path": "/workbench", "icon": "DesktopOutlined", "parent_id": None, "sort_order": 1},
        {"id": 2, "name": "制品仓库", "path": "/repository", "icon": "DatabaseOutlined", "parent_id": None, "sort_order": 2},
        {"id": 3, "name": "资产管理", "path": "", "icon": "InboxOutlined", "parent_id": None, "sort_order": 3},
        {"id": 4, "name": "烧录安装管理", "path": "/burning", "icon": "FireOutlined", "parent_id": None, "sort_order": 4},
        {"id": 5, "name": "履历记录", "path": "/record", "icon": "FileTextOutlined", "parent_id": None, "sort_order": 5},
        {"id": 6, "name": "异常注入", "path": "/injection", "icon": "BugOutlined", "parent_id": None, "sort_order": 6},
        {"id": 7, "name": "通信协议验证", "path": "/protocol", "icon": "WifiOutlined", "parent_id": None, "sort_order": 7},
        {"id": 8, "name": "系统管理", "path": "", "icon": "SettingOutlined", "parent_id": None, "sort_order": 8},

        # 二级菜单 - 资产管理下
        {"id": 31, "name": "产品管理", "path": "/product", "icon": "CodeOutlined", "parent_id": 3, "sort_order": 1},
        {"id": 32, "name": "烧录器管理", "path": "/burner", "icon": "FireOutlined", "parent_id": 3, "sort_order": 3},
        {"id": 33, "name": "脚本管理", "path": "/script", "icon": "FileProtectOutlined", "parent_id": 3, "sort_order": 4},

        # 二级菜单 - 系统管理下
        {"id": 81, "name": "用户管理", "path": "/user", "icon": "TeamOutlined", "parent_id": 8, "sort_order": 1},
        {"id": 82, "name": "角色管理", "path": "/role", "icon": "TeamOutlined", "parent_id": 8, "sort_order": 2},
        {"id": 83, "name": "权限管理", "path": "/permission", "icon": "SafetyOutlined", "parent_id": 8, "sort_order": 3},
        {"id": 84, "name": "登录日志", "path": "/log/login", "icon": "BarChartOutlined", "parent_id": 8, "sort_order": 4},
        {"id": 85, "name": "操作日志", "path": "/log/operation", "icon": "FileTextOutlined", "parent_id": 8, "sort_order": 5},
    ]

    for m in menus_data:
        menu = Menu(**m)
        db.add(menu)
    db.commit()

    # ============ 创建权限点 ============
    permissions_data = [
        # 工作台
        {"name": "工作台查看", "code": "workbench:view", "type": "menu", "menu_id": 1},

        # 制品仓库
        {"name": "制品仓库查看", "code": "repository:view", "type": "menu", "menu_id": 2},
        {"name": "新建项目", "code": "repository:add", "type": "button", "menu_id": 2},
        {"name": "编辑项目", "code": "repository:edit", "type": "button", "menu_id": 2},
        {"name": "删除项目", "code": "repository:delete", "type": "button", "menu_id": 2},
        {"name": "同步配置", "code": "repository:sync", "type": "button", "menu_id": 2},
        {"name": "邀请成员", "code": "repository:invite", "type": "button", "menu_id": 2},
        {"name": "权限变更", "code": "repository:perm_change", "type": "button", "menu_id": 2},

        # 产品管理
        {"name": "产品管理查看", "code": "product:view", "type": "menu", "menu_id": 31},
        {"name": "新增产品", "code": "product:add", "type": "button", "menu_id": 31},
        {"name": "编辑产品", "code": "product:edit", "type": "button", "menu_id": 31},
        {"name": "删除产品", "code": "product:delete", "type": "button", "menu_id": 31},

        # 烧录器管理
        {"name": "烧录器管理查看", "code": "burner:view", "type": "menu", "menu_id": 32},
        {"name": "新增烧录器", "code": "burner:add", "type": "button", "menu_id": 32},
        {"name": "编辑烧录器", "code": "burner:edit", "type": "button", "menu_id": 32},
        {"name": "删除烧录器", "code": "burner:delete", "type": "button", "menu_id": 32},
        {"name": "扫描烧录器", "code": "burner:scan", "type": "button", "menu_id": 32},

        # 脚本管理
        {"name": "脚本管理查看", "code": "script:view", "type": "menu", "menu_id": 33},
        {"name": "新增脚本", "code": "script:add", "type": "button", "menu_id": 33},
        {"name": "编辑脚本", "code": "script:edit", "type": "button", "menu_id": 33},
        {"name": "删除脚本", "code": "script:delete", "type": "button", "menu_id": 33},
        {"name": "执行脚本", "code": "script:execute", "type": "button", "menu_id": 33},

        # 烧录安装管理
        {"name": "烧录管理查看", "code": "burning:view", "type": "menu", "menu_id": 4},
        {"name": "创建烧录任务", "code": "burning:add", "type": "button", "menu_id": 4},
        {"name": "编辑烧录任务", "code": "burning:edit", "type": "button", "menu_id": 4},
        {"name": "删除烧录任务", "code": "burning:delete", "type": "button", "menu_id": 4},
        {"name": "执行烧录", "code": "burning:execute", "type": "button", "menu_id": 4},
        {"name": "查看一致性报告", "code": "burning:report", "type": "button", "menu_id": 4},
        {"name": "强制覆盖", "code": "burning:override", "type": "button", "menu_id": 4},

        # 履历记录
        {"name": "履历记录查看", "code": "record:view", "type": "menu", "menu_id": 5},
        {"name": "导出履历", "code": "record:export", "type": "button", "menu_id": 5},
        {"name": "删除履历", "code": "record:delete", "type": "button", "menu_id": 5},

        # 异常注入
        {"name": "异常注入查看", "code": "injection:view", "type": "menu", "menu_id": 6},
        {"name": "新增注入任务", "code": "injection:add", "type": "button", "menu_id": 6},
        {"name": "删除注入任务", "code": "injection:delete", "type": "button", "menu_id": 6},
        {"name": "执行注入", "code": "injection:execute", "type": "button", "menu_id": 6},
        {"name": "查看注入详情", "code": "injection:detail", "type": "button", "menu_id": 6},

        # 通信协议验证
        {"name": "协议验证查看", "code": "protocol:view", "type": "menu", "menu_id": 7},
        {"name": "新建协议测试", "code": "protocol:add", "type": "button", "menu_id": 7},
        {"name": "删除协议测试", "code": "protocol:delete", "type": "button", "menu_id": 7},
        {"name": "执行协议测试", "code": "protocol:execute", "type": "button", "menu_id": 7},

        # 系统管理 - 用户管理
        {"name": "用户管理查看", "code": "user:view", "type": "menu", "menu_id": 81},
        {"name": "新增用户", "code": "user:add", "type": "button", "menu_id": 81},
        {"name": "编辑用户", "code": "user:edit", "type": "button", "menu_id": 81},
        {"name": "删除用户", "code": "user:delete", "type": "button", "menu_id": 81},
        {"name": "重置密码", "code": "user:reset_pwd", "type": "button", "menu_id": 81},

        # 系统管理 - 角色管理
        {"name": "角色管理查看", "code": "role:view", "type": "menu", "menu_id": 82},
        {"name": "新增角色", "code": "role:add", "type": "button", "menu_id": 82},
        {"name": "编辑角色", "code": "role:edit", "type": "button", "menu_id": 82},
        {"name": "删除角色", "code": "role:delete", "type": "button", "menu_id": 82},
        {"name": "分配权限", "code": "role:assign", "type": "button", "menu_id": 82},

        # 系统管理 - 权限管理
        {"name": "权限管理查看", "code": "permission:view", "type": "menu", "menu_id": 83},
        {"name": "新增权限点", "code": "permission:add", "type": "button", "menu_id": 83},
        {"name": "删除权限点", "code": "permission:delete", "type": "button", "menu_id": 83},

        # 系统管理 - 日志
        {"name": "日志查看", "code": "log:view", "type": "menu", "menu_id": 84},
        {"name": "导出日志", "code": "log:export", "type": "button", "menu_id": 84},
        {"name": "清空日志", "code": "log:clear", "type": "button", "menu_id": 84},
    ]

    for p in permissions_data:
        permission = Permission(**p)
        db.add(permission)
    db.commit()

    # ============ 分配权限给管理员角色 ============
    admin_role = db.query(Role).filter(Role.name == "管理员").first()
    if admin_role:
        all_permissions = db.query(Permission).all()
        for perm in all_permissions:
            rp = RolePermission(role_id=admin_role.id, permission_id=perm.id)
            db.add(rp)
        db.commit()
        print(f"已为管理员角色分配 {len(all_permissions)} 个权限点")

    print("菜单和权限初始化完成")


def seed_mock_data():
    """填充模拟测试数据（开发环境）"""
    db = SessionLocal()
    try:
        from backend.models.product import Product
        from backend.models.burner import Burner
        from backend.models.script import Script
        from backend.models.task import BurningTask
        from backend.models.log import Record, Injection, ProtocolTest, LoginLog, OperationLog
        from backend.models.repository import Repository
        from backend.models.user import User
        from datetime import datetime, timedelta

        # 检查是否已填充
        if db.query(Product).count() > 0:
            return

        print("正在填充模拟测试数据...")
        now = datetime.utcnow()

        # Products (芯片/板卡)
        products = [
            Product(name="STM32F407VGT6开发板", chip_type="ARM", serial_number="LL6FAPCH6OSB", voltage="DC 12V", temp_range="5~50", interface="串口", config_description="STM32F4系列Cortex-M4内核，1MB Flash，192KB RAM", id=1),
            Product(name="LPC55S69评估板", chip_type="ARM", serial_number="K3JF8H2D1N", voltage="DC 5V", temp_range="0~70", interface="USB", config_description="NXP LPC55S69，双核Cortex-M33", id=2),
            Product(name="ESP32开发板", chip_type="ARM", serial_number="M7PQ4W9R2T", voltage="DC 3.3V", temp_range="-40~85", interface="WiFi/蓝牙", config_description="乐鑫ESP32-S3，支持WiFi和蓝牙", id=3),
            Product(name="TI系列板卡", chip_type="DSP", serial_number="XF3K9M1L5P", voltage="DC 12V", temp_range="0~70", interface="CAN", config_description="TMS320F28335，C2000系列数字控制", id=4),
            Product(name="PIC32MZ开发板", chip_type="PIC", serial_number="B2HN7D4S8W", voltage="DC 3.3V", temp_range="-40~85", interface="SPI", config_description="Microchip PIC32MZ，双精度FPU", id=5),
            Product(name="CycloneV FPGA板", chip_type="FPGA", serial_number="Q8RT5J3K6M", voltage="DC 5V", temp_range="0~70", interface="千兆以太网", config_description="Altera Cyclone V，FPGA+ARM双架构", id=6),
            Product(name="EPM240T100开发板", chip_type="Altera-CPLD", serial_number="W4LM9P2N7X", voltage="DC 3.3V", temp_range="0~70", interface="JTAG", config_description="Altera CPLD，240个LE", id=7),
            Product(name="RK3568核心板", chip_type="ARM", serial_number="Z5TY8K3J1R", voltage="DC 5V", temp_range="-20~70", interface="HDMI/以太网", config_description="Rockchip RK3568，四核Cortex-A55", id=8),
        ]
        for p in products:
            p.created_at = now - timedelta(days=30)
            p.updated_at = now - timedelta(days=1)
            db.add(p)
        db.commit()

        # Burners (烧录器)
        burners = [
            Burner(name="J-LINK V11", type="JTAG", sn="123FAS064E573436F2FC1003", port="USB", status=1, description="Segger J-LINK，支持ARM Cortex系列"),
            Burner(name="ST-LINK V2", type="SWD", sn="QQFA71064E573436F2FC1WEQ", port="USB", status=1, description="ST官方调试器，支持STM32系列"),
            Burner(name="MPLAB ICD 3", type="ICD", sn="B3FA71064E573436F2FC1ABC", port="USB", status=0, description="Microchip官方调试器"),
            Burner(name="TI XDS510 Plus", type="JTAG", sn="C4FA71064E573436F2FC1DEF", port="USB", status=1, description="TI官方仿真器，支持C2000系列"),
            Burner(name="PWLINK V2", type="SWD", sn="D5FA71064E573436F2FC1GHI", port="USB", status=0, description="适合PIC系列烧录"),
            Burner(name="GDLINK", type="SWD", sn="E6FA71064E573436F2FC1JKL", port="USB", status=1, description="国产GD-Link，支持GD32系列"),
            Burner(name="Altera Blaster II", type="JTAG", sn="F7FA71064E573436F2FC1MNO", port="USB", status=0, description="Intel/Altera FPGA调试器"),
            Burner(name="Gowin USB Cable", type="JTAG", sn="G8FA71064E573436F2FC1PQR", port="USB", status=1, description="高云FPGA下载器"),
        ]
        for b in burners:
            b.created_at = now - timedelta(days=25)
            b.updated_at = now - timedelta(days=2)
            db.add(b)
        db.commit()

        # Scripts (脚本)
        scripts = [
            Script(name="bench_test_v3.py", type="python", content="import time\ndef main():\n    print('Bench test started')\n    time.sleep(5)\n    print('Test complete')", ide_name="Keil", associated_board="STM32F407VGT6开发板", associated_burner="J-LINK V11"),
            Script(name="Code_Composer_Build.sh", type="shell", content="#!/bin/bash\nmake clean\nmake all", ide_name="Code Composer Studio", associated_board="TI系列板卡", associated_burner="TI XDS510 Plus"),
            Script(name="XDS51_Flash.ps1", type="Power Shell", content="$port = \"COM3\"\nWrite-Host \"Flashing...\"", ide_name="Code Composer Studio", associated_board="TI系列板卡", associated_burner="TI XDS510 Plus"),
            Script(name="stm32_verify.py", type="python", content="import serial\ndef verify():\n    ser = serial.Serial('/dev/ttyUSB0', 115200)\n    print('Verification done')", ide_name="STM32CubeIDE", associated_board="STM32F407VGT6开发板", associated_burner="ST-LINK V2"),
            Script(name="fpga_flash.tcl", type="TCL", content="open_device -id 1\nprogram_device -p 1", ide_name="Vivado", associated_board="CycloneV FPGA板", associated_burner="Altera Blaster II"),
            Script(name="esp32_burn.py", type="python", content="import esptool\nesptool.flash.read(0x1000, 0x10000)", ide_name="Keil", associated_board="ESP32开发板", associated_burner="J-LINK V11"),
        ]
        for s in scripts:
            s.created_at = now - timedelta(days=20)
            s.updated_at = now - timedelta(days=3)
            db.add(s)
        db.commit()

        # Repositories (制品仓库)
        repos = [
            Repository(name="核心库-main", repo_id="core-main", tenant="default", description="核心产品镜像仓库"),
            Repository(name="应用库-app", repo_id="app-releases", tenant="default", description="应用层软件仓库"),
            Repository(name="测试库-test", repo_id="test-builds", tenant="test", description="测试版本仓库"),
            Repository(name="固件库-firmware", repo_id="firmware-v1", tenant="default", description="固件镜像仓库"),
        ]
        for r in repos:
            r.created_at = now - timedelta(days=15)
            r.updated_at = now - timedelta(days=1)
            db.add(r)
        db.commit()

        # Users (额外用户)
        from backend.models.role import Role
        from passlib.context import CryptContext
        pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
        operator_role = db.query(Role).filter(Role.name == "操作员").first()
        viewer_role = db.query(Role).filter(Role.name == "观察员").first()

        extra_users = [
            User(username="zhangwei", password_hash=pwd.hash("admin123"), email="zhangwei@pcids.com", role_id=operator_role.id if operator_role else None, status=1),
            User(username="lina", password_hash=pwd.hash("admin123"), email="lina@pcids.com", role_id=viewer_role.id if viewer_role else None, status=1),
            User(username="wangfang", password_hash=pwd.hash("admin123"), email="wangfang@pcids.com", role_id=None, status=0),
        ]
        for u in extra_users:
            u.created_at = now - timedelta(days=10)
            u.updated_at = now - timedelta(days=1)
            db.add(u)
        db.commit()

        # Tasks (烧录任务)
        board_names = ["Cortex-A78板卡", "TMS320F2833", "CycloneV", "鸿蒙", "银河麒麟"]
        statuses = [0, 1, 2, 2, 2, 3]  # 等待/进行中/完成/失败
        results = ["success", "SUCCESS", "安装完成", "版本校验失败", "MD5校验通过"]
        for i in range(15):
            task = BurningTask(
                software_name=f"firmware_v{i+1}.bin",
                board_name=board_names[i % len(board_names)],
                status=statuses[i % len(statuses)],
                result=results[i % len(results)],
                target_ip=f"192.168.1.{10+i}",
                created_at=now - timedelta(days=10-i),
                updated_at=now - timedelta(days=10-i) + timedelta(hours=1),
            )
            db.add(task)
        db.commit()

        # Records (履历记录)
        op_results = ["烧录成功", "安装成功", "烧录成功", "安装失败", "烧录成功"]
        serials = ["SN20260424001", "SN20260423002", "SN20260422003", "SN20260421004", "SN20260420005"]
        software_names = ["firmware_v1.bin", "os_image_v2.img", "app_v3.bin", "bootloader_v4.bin", "kernel_v5.img"]
        operators = ["admin", "zhangwei", "lina", "wangfang", "admin"]
        record_types = ["burn", "install", "burn", "install", "burn"]
        for i in range(20):
            record = Record(
                serial_number=serials[i % len(serials)],
                software_name=software_names[i % len(software_names)],
                operator=operators[i % len(operators)],
                ip_address=f"192.168.1.{20+i}",
                operation_time=now - timedelta(days=5-i, hours=i),
                result=op_results[i % len(op_results)],
                type=record_types[i % len(record_types)],
            )
            db.add(record)
        db.commit()

        # Injections (异常注入)
        injection_configs = [
            Injection(type="断电模拟", target="STM32F407开发板", config='{"duration": 5, "recovery": "auto"}', status=2, result="测试完成"),
            Injection(type="存储不足", target="LPC55S69评估板", config='{"fill": "large_file", "size": "50%"}', status=2, result="测试完成"),
            Injection(type="网络中断", target="ESP32开发板", config='{"type": "full", "duration": 10}', status=1, result="执行中"),
            Injection(type="权限缺失", target="TI系列板卡", config='{"target": "flash_dir", "perm": "write"}', status=0, result="待执行"),
        ]
        for inj in injection_configs:
            inj.created_at = now - timedelta(days=3)
            inj.updated_at = now - timedelta(days=1)
            db.add(inj)
        db.commit()

        # ProtocolTests (通信协议测试)
        protocol_results = ["通过", "通过", "失败", "通过"]
        protocol_targets = ["STM32F407开发板", "LPC55S69评估板", "ESP32开发板", "TI系列板卡"]
        for i in range(10):
            pt = ProtocolTest(
                target=protocol_targets[i % len(protocol_targets)],
                address=f"0x{i:04X}",
                result=protocol_results[i % len(protocol_results)],
                created_at=now - timedelta(days=4-i),
            )
            db.add(pt)
        db.commit()

        # LoginLogs (登录日志)
        login_results = ["登录成功", "登录成功", "密码错误", "登录成功"]
        for i in range(15):
            ll = LoginLog(
                user_id=(i % 3) + 1,
                ip_address=f"192.168.1.{50+i}",
                log_type="login",
                login_time=now - timedelta(days=7-i, hours=i*2),
                result=login_results[i % len(login_results)],
            )
            db.add(ll)
        db.commit()

        # OperationLogs (操作日志)
        modules = ["用户管理", "角色管理", "烧录任务", "制品仓库", "脚本管理"]
        actions = ["创建用户", "分配权限", "创建烧录任务", "上传制品", "编辑脚本"]
        for i in range(15):
            ol = OperationLog(
                user_id=(i % 3) + 1,
                ip_address=f"192.168.1.{100+i}",
                module=modules[i % len(modules)],
                action=actions[i % len(actions)],
                operation_time=now - timedelta(days=6-i, hours=i),
                result="成功",
            )
            db.add(ol)
        db.commit()

        print("模拟测试数据填充完成")
    except Exception as e:
        db.rollback()
        print(f"填充模拟数据失败：{e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def ensure_schema():
    if engine.dialect.name != "sqlite":
        return

    def ensure_column(table: str, column: str, ddl_type: str):
        with engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {r[1] for r in rows}
            if column in existing:
                return
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            conn.commit()

    ensure_column("records", "created_by_user_id", "INTEGER")
    ensure_column("records", "repository_id", "INTEGER")
    ensure_column("records", "project_key", "VARCHAR(200)")
    ensure_column("records", "remark", "VARCHAR(500)")
    ensure_column("login_logs", "log_type", "VARCHAR(20)")
    ensure_column("operation_logs", "ip_address", "VARCHAR(50)")
    ensure_column("tasks", "repository_id", "INTEGER")
    ensure_column("tasks", "serial_number", "VARCHAR(100)")
    ensure_column("tasks", "keep_local", "INTEGER")
    ensure_column("tasks", "integrity", "INTEGER")
    ensure_column("tasks", "expected_checksum", "VARCHAR(128)")
    ensure_column("tasks", "current_md5", "VARCHAR(64)")
    ensure_column("tasks", "current_sha256", "VARCHAR(128)")
    ensure_column("tasks", "integrity_passed", "INTEGER")
    ensure_column("tasks", "version_check", "INTEGER")
    ensure_column("tasks", "history_checksum", "VARCHAR(128)")
    ensure_column("tasks", "consistency_passed", "INTEGER")
    ensure_column("tasks", "override_confirmed", "INTEGER")
    ensure_column("tasks", "created_by_user_id", "INTEGER")
    ensure_column("tasks", "attempt_count", "INTEGER")
    ensure_column("tasks", "max_retries", "INTEGER")
    ensure_column("tasks", "rollback_count", "INTEGER")
    ensure_column("tasks", "rollback_result", "TEXT")
    ensure_column("tasks", "last_error", "TEXT")
    ensure_column("tasks", "agent_url", "VARCHAR(500)")
    ensure_column("tasks", "script_id", "INTEGER")
    ensure_column("scripts", "status", "INTEGER")
    ensure_column("scripts", "result", "TEXT")
    ensure_column("scripts", "ide_name", "VARCHAR(100)")
    ensure_column("scripts", "associated_board", "VARCHAR(200)")
    ensure_column("scripts", "associated_burner", "VARCHAR(200)")
    ensure_column("burners", "location", "VARCHAR(100)")
    ensure_column("burners", "strategy", "INTEGER")
    ensure_column("burners", "is_enabled", "INTEGER")
    ensure_column("burners", "modified_by", "VARCHAR(50)")
    ensure_column("products", "usage_description", "TEXT")
    ensure_column("products", "board_image", "VARCHAR(500)")
    ensure_column("products", "created_by", "VARCHAR(50)")
    ensure_column("products", "modified_by", "VARCHAR(50)")
    ensure_column("repositories", "version", "VARCHAR(100)")
    ensure_column("repositories", "file_url", "VARCHAR(500)")
    ensure_column("repositories", "size", "INTEGER")
    ensure_column("repositories", "md5", "VARCHAR(64)")
    ensure_column("repositories", "sha256", "VARCHAR(128)")
    ensure_column("repositories", "download_count", "INTEGER")
    ensure_column("repositories", "last_download_time", "DATETIME")
    ensure_column("repositories", "created_by_user_id", "INTEGER")
    ensure_column("repositories", "project_key", "VARCHAR(200)")
    ensure_column("repositories", "permission_config_json", "TEXT")
    ensure_column("users", "codearts_config_json", "TEXT")


def init_db():
    """初始化数据库，创建所有表"""
    from backend.models.base import Base
    Base.metadata.create_all(bind=engine)
    ensure_schema()

    db = SessionLocal()

    try:
        # 创建默认角色
        from backend.models.role import Role
        admin_role = db.query(Role).filter(Role.name == "管理员").first()
        if not admin_role:
            admin_role = Role(
                name="管理员",
                description="系统管理员，拥有所有权限"
            )
            db.add(admin_role)
            db.commit()
            db.refresh(admin_role)
            print("已创建管理员角色")

        operator_role = db.query(Role).filter(Role.name == "操作员").first()
        if not operator_role:
            operator_role = Role(
                name="操作员",
                description="普通操作员，可执行烧录任务"
            )
            db.add(operator_role)
            db.commit()
            db.refresh(operator_role)
            print("已创建操作员角色")

        viewer_role = db.query(Role).filter(Role.name == "观察员").first()
        if not viewer_role:
            viewer_role = Role(
                name="观察员",
                description="只读权限，可查看记录和日志"
            )
            db.add(viewer_role)
            db.commit()
            print("已创建观察员角色")

        # 初始化菜单和权限
        init_menus_and_permissions(db)

        # 创建默认管理员账户
        from backend.models.user import User
        from passlib.context import CryptContext

        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            admin = User(
                username="admin",
                password_hash=pwd_context.hash("admin123"),
                email="admin@pcids.com",
                role_id=admin_role.id,
                status=1
            )
            db.add(admin)
            db.commit()
            print("已创建默认管理员账户：admin / admin123")

        print("数据库初始化完成")
    except Exception as e:
        db.rollback()
        print(f"初始化数据失败：{e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

    # 填充模拟数据
    seed_mock_data()
