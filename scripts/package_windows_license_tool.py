#!/usr/bin/env python3
"""Build the self-contained Windows issuer delivery folder and zip archive."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import zipfile
from pathlib import Path

from license_issuer import (
    ISSUER_PASSWORD_FILE_NAME,
    WINDOWS_LEDGER_NAME,
    WINDOWS_PRIVATE_KEY_NAME,
    export_windows_private_key,
)


README = """PCIDS Windows 离线授权签发工具
==================================

本目录已经包含运行所需的全部文件，目标 Windows 电脑无需安装 Python、Node 或其他运行环境，也无需连接互联网。

文件说明
--------
1. PCIDS-License-Issuer.exe：Windows x64 图形化签发工具。
2. issuer_private_key.pcissuer：AES-GCM 加密后的 Ed25519 签发私钥。
3. issuer_password.txt：签发私钥密码。
4. issuer_ledger.json：授权机器台账；请始终随工具保存并做好备份。
5. license_public_key.pem：主程序内置公钥的备份，用于核对交付版本。

现场操作
--------
1. 把整个文件夹临时复制到需要授权的 Windows 电脑。
2. 先安装并至少启动一次“程控安装部署系统”。
3. 右键运行 PCIDS-License-Issuer.exe；一般不需要管理员权限。
4. “签发资料目录”选择本目录。
5. “软件 data 目录”选择 PCIDS 安装目录下的 data，例如：
   C:\\Program Files\\程控安装部署系统\\data
6. 填写客户编号、客户名称、该客户允许授权的机器总数；截止日期可留空。
7. 点击“生成并安装 License”。工具将写入：
   <软件 data 目录>\\license\\pcids.lic
8. 重新打开或刷新 PCIDS，确认授权状态有效。

数量规则
--------
- 限制的是已授权电脑数量，不限制软件中的活跃用户数。
- 同一客户、同一电脑重复签发不会重复占用数量。
- 新电脑达到“授权机器总数”后，工具会拒绝继续签发。
- issuer_ledger.json 丢失或被删除会失去历史计数，因此必须备份。

安全要求
--------
- 本目录包含签发能力，相当于授权印章，只能由授权管理员保管。
- 完成签发后，从客户电脑删除本工具整个目录；客户电脑仅保留 pcids.lic。
- 请不要通过聊天、邮件或公共网盘传播本目录，不要提交到 Git。
- 建议将本目录保存在加密移动介质中，并对 issuer_ledger.json 定期备份。
"""


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def package_delivery(
    executable: Path,
    issuer_dir: Path,
    public_key: Path,
    output_root: Path,
) -> dict[str, str]:
    executable = executable.expanduser().resolve(strict=True)
    issuer_dir = issuer_dir.expanduser().resolve(strict=True)
    public_key = public_key.expanduser().resolve(strict=True)
    output_root = output_root.expanduser().resolve(strict=False)
    delivery_dir = output_root / "PCIDS-Windows-License-Issuer"
    delivery_dir.mkdir(parents=True, exist_ok=True)

    encrypted_key = export_windows_private_key(issuer_dir)
    password_file = issuer_dir / ISSUER_PASSWORD_FILE_NAME
    if not password_file.is_file():
        raise FileNotFoundError(f"未找到签发密码: {password_file}")

    shutil.copy2(executable, delivery_dir / "PCIDS-License-Issuer.exe")
    shutil.copy2(encrypted_key, delivery_dir / WINDOWS_PRIVATE_KEY_NAME)
    shutil.copy2(password_file, delivery_dir / ISSUER_PASSWORD_FILE_NAME)
    shutil.copy2(public_key, delivery_dir / "license_public_key.pem")

    ledger_path = delivery_dir / WINDOWS_LEDGER_NAME
    if not ledger_path.exists():
        atomic_write(
            ledger_path,
            (json.dumps({"version": 1, "issues": []}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    atomic_write(delivery_dir / "使用说明.txt", ("\ufeff" + README).encode("utf-8"))

    archive_path = output_root / "PCIDS-Windows-License-Issuer-Complete.zip"
    temporary_archive = archive_path.with_name(f".{archive_path.name}.{os.getpid()}.tmp")
    with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(delivery_dir.iterdir()):
            archive.write(path, Path(delivery_dir.name) / path.name)
    os.replace(temporary_archive, archive_path)
    return {
        "delivery_dir": str(delivery_dir),
        "archive": str(archive_path),
        "executable": str(delivery_dir / "PCIDS-License-Issuer.exe"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 PCIDS Windows 离线授权签发工具")
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--issuer-dir", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package_delivery(args.exe, args.issuer_dir, args.public_key, args.output_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
