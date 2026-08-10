#!/usr/bin/env python3
"""PCIDS offline license issuer. Build and distribute separately from the app."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.utils.license_manager import (  # noqa: E402
    LICENSE_FILE_NAME,
    LICENSE_PRODUCT_CODE,
    LICENSE_SCHEMA_VERSION,
    canonical_json,
    encode_signature,
    format_utc,
    get_license_dir,
    get_machine_identity,
    utc_now,
)


ISSUER_PRIVATE_KEY_NAME = "issuer_private_key.pem"
ISSUER_PUBLIC_KEY_NAME = "license_public_key.pem"
ISSUER_PASSWORD_FILE_NAME = "issuer_password.txt"
ISSUER_LEDGER_NAME = "issuer_ledger.db"
WINDOWS_PRIVATE_KEY_NAME = "issuer_private_key.pcissuer"
WINDOWS_LEDGER_NAME = "issuer_ledger.json"
WINDOWS_KEY_CONTEXT = b"PCIDS-License-Issuer-v1"
WINDOWS_KEY_ITERATIONS = 300_000


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return ROOT


def parse_expiration(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed = datetime.combine(datetime.fromisoformat(raw).date(), datetime_time(23, 59, 59))
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed.astimezone(timezone.utc) <= utc_now():
            raise ValueError("授权截止日期必须晚于当前时间")
        return format_utc(parsed)
    except ValueError as exc:
        if str(exc) == "授权截止日期必须晚于当前时间":
            raise
        raise ValueError("授权截止日期格式应为 YYYY-MM-DD") from exc


def read_password(password_file: Optional[Path], prompt: bool = True) -> bytes:
    if password_file and password_file.is_file():
        password = password_file.read_text(encoding="utf-8").strip()
    elif prompt:
        password = getpass.getpass("签发私钥密码: ").strip()
    else:
        password = ""
    if len(password) < 16:
        raise ValueError("签发私钥密码至少需要 16 个字符")
    return password.encode("utf-8")


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(content)
    try:
        os.chmod(temporary, mode)
    except OSError:
        pass
    os.replace(temporary, path)


def export_windows_private_key(
    issuer_dir: Path,
    output_path: Optional[Path] = None,
) -> Path:
    """Export the existing issuer key in the native Windows tool format."""
    issuer_dir = issuer_dir.expanduser().resolve(strict=False)
    password = read_password(issuer_dir / ISSUER_PASSWORD_FILE_NAME, prompt=False)
    private_key = load_private_key(issuer_dir / ISSUER_PRIVATE_KEY_NAME, password)
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    salt = os.urandom(16)
    nonce = os.urandom(12)
    derived_key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=WINDOWS_KEY_ITERATIONS,
    ).derive(password)
    ciphertext = AESGCM(derived_key).encrypt(nonce, seed, WINDOWS_KEY_CONTEXT)
    container = {
        "version": 1,
        "product": LICENSE_PRODUCT_CODE,
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": WINDOWS_KEY_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    target = (output_path or issuer_dir / WINDOWS_PRIVATE_KEY_NAME).expanduser().resolve(strict=False)
    _atomic_write(
        target,
        (json.dumps(container, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return target


def initialize_issuer(
    issuer_dir: Path,
    public_key_output: Path,
    force: bool = False,
) -> Dict[str, str]:
    issuer_dir = issuer_dir.expanduser().resolve(strict=False)
    issuer_dir.mkdir(parents=True, exist_ok=True)
    private_path = issuer_dir / ISSUER_PRIVATE_KEY_NAME
    password_path = issuer_dir / ISSUER_PASSWORD_FILE_NAME
    ledger_path = issuer_dir / ISSUER_LEDGER_NAME
    windows_private_path = issuer_dir / WINDOWS_PRIVATE_KEY_NAME
    windows_ledger_path = issuer_dir / WINDOWS_LEDGER_NAME
    public_key_output = public_key_output.expanduser().resolve(strict=False)

    protected_paths = (private_path, password_path, windows_private_path, public_key_output)
    if not force and any(path.exists() for path in protected_paths):
        raise FileExistsError("签发密钥已存在；如需重建请显式使用 --force")

    password_text = secrets.token_urlsafe(36)
    password = password_text.encode("ascii")
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    password_path.write_text(password_text, encoding="utf-8")
    public_key_output.parent.mkdir(parents=True, exist_ok=True)
    public_key_output.write_bytes(public_pem)
    try:
        os.chmod(private_path, 0o600)
        os.chmod(password_path, 0o600)
    except OSError:
        pass
    initialize_ledger(ledger_path)
    export_windows_private_key(issuer_dir, windows_private_path)
    if force or not windows_ledger_path.exists():
        _atomic_write(windows_ledger_path, b'{\n  "version": 1,\n  "issues": []\n}\n')
    return {
        "issuer_dir": str(issuer_dir),
        "private_key": str(private_path),
        "password_file": str(password_path),
        "ledger": str(ledger_path),
        "windows_private_key": str(windows_private_path),
        "windows_ledger": str(windows_ledger_path),
        "public_key": str(public_key_output),
    }


def initialize_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS license_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                machine_fingerprint TEXT NOT NULL,
                machine_code TEXT NOT NULL,
                installation_no INTEGER NOT NULL,
                installation_limit INTEGER NOT NULL,
                license_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT,
                license_path TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(customer_id, machine_fingerprint)
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_license_issues_customer_slot
            ON license_issues(customer_id, installation_no)
            """
        )


def load_private_key(private_path: Path, password: bytes) -> Ed25519PrivateKey:
    if not private_path.is_file():
        raise FileNotFoundError(f"未找到签发私钥: {private_path}")
    try:
        loaded = serialization.load_pem_private_key(private_path.read_bytes(), password=password)
    except Exception as exc:
        raise ValueError("签发私钥或密码不正确") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ValueError("签发私钥类型不是 Ed25519")
    return loaded


def reserve_installation_number(
    ledger_path: Path,
    customer_id: str,
    customer_name: str,
    machine_fingerprint: str,
    machine_code: str,
    installation_limit: int,
) -> int:
    if installation_limit < 1:
        raise ValueError("授权机器总数必须大于 0")
    initialize_ledger(ledger_path)
    with sqlite3.connect(str(ledger_path), timeout=15) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT installation_no
            FROM license_issues
            WHERE customer_id = ? AND machine_fingerprint = ?
            """,
            (customer_id, machine_fingerprint),
        ).fetchone()
        if existing:
            installation_no = int(existing[0])
            if installation_limit < installation_no:
                raise ValueError(f"授权机器总数不能小于当前机器序号 {installation_no}")
            return installation_no

        issued_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM license_issues WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()[0]
        )
        if issued_count >= installation_limit:
            raise ValueError(f"客户 {customer_name} 已达到 {installation_limit} 台机器的授权上限")
        maximum = connection.execute(
            "SELECT COALESCE(MAX(installation_no), 0) FROM license_issues WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()[0]
        return int(maximum) + 1


def save_issue(ledger_path: Path, payload: Dict[str, Any], license_path: Path) -> None:
    with sqlite3.connect(str(ledger_path), timeout=15) as connection:
        connection.execute(
            """
            INSERT INTO license_issues (
                customer_id, customer_name, machine_fingerprint, machine_code,
                installation_no, installation_limit, license_id, issued_at,
                expires_at, license_path, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id, machine_fingerprint) DO UPDATE SET
                customer_name = excluded.customer_name,
                installation_limit = excluded.installation_limit,
                license_id = excluded.license_id,
                issued_at = excluded.issued_at,
                expires_at = excluded.expires_at,
                license_path = excluded.license_path,
                payload_json = excluded.payload_json
            """,
            (
                payload["customer_id"],
                payload["customer_name"],
                payload["machine_fingerprint"],
                payload["machine_code"],
                payload["installation_no"],
                payload["installation_limit"],
                payload["license_id"],
                payload["issued_at"],
                payload["expires_at"],
                str(license_path),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )


def issue_license(
    issuer_dir: Path,
    data_root: Path,
    customer_id: str,
    customer_name: str,
    installation_limit: int,
    expires_at: str = "",
    features: Optional[list[str]] = None,
    password: Optional[bytes] = None,
) -> Dict[str, Any]:
    issuer_dir = issuer_dir.expanduser().resolve(strict=False)
    data_root = data_root.expanduser().resolve(strict=False)
    customer_id = customer_id.strip()
    customer_name = customer_name.strip()
    if not customer_id or not customer_name:
        raise ValueError("客户编号和客户名称不能为空")

    password = password or read_password(issuer_dir / ISSUER_PASSWORD_FILE_NAME)
    private_key = load_private_key(issuer_dir / ISSUER_PRIVATE_KEY_NAME, password)
    identity = get_machine_identity(data_root)
    ledger_path = issuer_dir / ISSUER_LEDGER_NAME
    installation_no = reserve_installation_number(
        ledger_path,
        customer_id,
        customer_name,
        identity["fingerprint"],
        identity["machine_code"],
        installation_limit,
    )
    issued_at = format_utc(utc_now())
    parsed_expiration = parse_expiration(expires_at)
    payload: Dict[str, Any] = {
        "license_id": f"LIC-{utc_now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:12].upper()}",
        "customer_id": customer_id,
        "customer_name": customer_name,
        "product": LICENSE_PRODUCT_CODE,
        "machine_fingerprint": identity["fingerprint"],
        "machine_code": identity["machine_code"],
        "installation_no": installation_no,
        "installation_limit": int(installation_limit),
        "issued_at": issued_at,
        "not_before": issued_at,
        "expires_at": parsed_expiration,
        "features": features or ["core", "business_sync", "hardware_test"],
    }
    envelope = {
        "schema_version": LICENSE_SCHEMA_VERSION,
        "signature_algorithm": "Ed25519",
        "payload": payload,
    }
    document = {
        **envelope,
        "signature": encode_signature(private_key.sign(canonical_json(envelope))),
    }
    license_path = get_license_dir(data_root) / LICENSE_FILE_NAME
    temporary = license_path.with_name(f".{license_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        save_issue(ledger_path, payload, license_path)
        os.replace(temporary, license_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"license_path": str(license_path), "payload": payload}


def launch_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("PCIDS 离线授权签发工具")
    root.geometry("720x560")
    root.minsize(680, 520)

    default_dir = application_dir()
    variables = {
        "issuer_dir": tk.StringVar(value=str(default_dir)),
        "data_root": tk.StringVar(value=str(default_dir / "data")),
        "customer_id": tk.StringVar(),
        "customer_name": tk.StringVar(),
        "limit": tk.StringVar(value="1"),
        "expires": tk.StringVar(),
        "machine_code": tk.StringVar(value="尚未读取"),
    }

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="PCIDS 离线授权", font=("Microsoft YaHei UI", 18, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
    )
    ttk.Label(frame, text="签发文件仅绑定当前计算机；私钥、密码和台账请从目标机移除。", foreground="#667085").grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(0, 20)
    )

    def directory_row(row: int, label: str, key: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        ttk.Entry(frame, textvariable=variables[key]).grid(row=row, column=1, sticky="ew", pady=8)
        ttk.Button(
            frame,
            text="选择",
            command=lambda: variables[key].set(filedialog.askdirectory(initialdir=variables[key].get()) or variables[key].get()),
        ).grid(row=row, column=2, padx=(10, 0), pady=8)

    directory_row(2, "签发资料目录", "issuer_dir")
    directory_row(3, "软件 data 目录", "data_root")

    field_rows = (
        (4, "客户编号", "customer_id"),
        (5, "客户名称", "customer_name"),
        (6, "授权机器总数", "limit"),
        (7, "授权截止日期", "expires"),
    )
    for row, label, key in field_rows:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        ttk.Entry(frame, textvariable=variables[key]).grid(row=row, column=1, columnspan=2, sticky="ew", pady=8)
    ttk.Label(frame, text="留空表示长期有效；填写格式 YYYY-MM-DD", foreground="#667085").grid(
        row=8, column=1, columnspan=2, sticky="w"
    )

    ttk.Label(frame, text="本机机器码").grid(row=9, column=0, sticky="w", padx=(0, 12), pady=(20, 8))
    machine_entry = ttk.Entry(frame, textvariable=variables["machine_code"], state="readonly")
    machine_entry.grid(row=9, column=1, sticky="ew", pady=(20, 8))

    def refresh_machine_code() -> None:
        try:
            identity = get_machine_identity(Path(variables["data_root"].get()))
            variables["machine_code"].set(identity["machine_code"])
        except Exception as exc:
            messagebox.showerror("读取失败", str(exc))

    ttk.Button(frame, text="读取", command=refresh_machine_code).grid(row=9, column=2, padx=(10, 0), pady=(20, 8))

    result_text = tk.Text(frame, height=6, wrap="word", state="disabled", background="#f7f8fa")
    result_text.grid(row=10, column=0, columnspan=3, sticky="nsew", pady=(16, 16))
    frame.rowconfigure(10, weight=1)

    def set_result(value: str) -> None:
        result_text.configure(state="normal")
        result_text.delete("1.0", "end")
        result_text.insert("1.0", value)
        result_text.configure(state="disabled")

    def generate() -> None:
        try:
            result = issue_license(
                issuer_dir=Path(variables["issuer_dir"].get()),
                data_root=Path(variables["data_root"].get()),
                customer_id=variables["customer_id"].get(),
                customer_name=variables["customer_name"].get(),
                installation_limit=int(variables["limit"].get()),
                expires_at=variables["expires"].get(),
            )
            payload = result["payload"]
            variables["machine_code"].set(payload["machine_code"])
            set_result(
                f"License 已生成并写入：\n{result['license_path']}\n\n"
                f"授权编号：{payload['license_id']}\n"
                f"机器序号：{payload['installation_no']} / {payload['installation_limit']}"
            )
            messagebox.showinfo("签发成功", "License 已写入软件 data\\license 目录")
        except Exception as exc:
            messagebox.showerror("签发失败", str(exc))

    ttk.Button(frame, text="生成并安装 License", command=generate).grid(
        row=11, column=1, columnspan=2, sticky="e", pady=(0, 4)
    )
    refresh_machine_code()
    root.mainloop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PCIDS 离线机器授权签发工具")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="初始化签发密钥与台账")
    init_parser.add_argument("--issuer-dir", type=Path, required=True)
    init_parser.add_argument("--public-key", type=Path, required=True)
    init_parser.add_argument("--force", action="store_true")

    issue_parser = subparsers.add_parser("issue", help="为本机签发 License")
    issue_parser.add_argument("--issuer-dir", type=Path, required=True)
    issue_parser.add_argument("--data-dir", type=Path, required=True)
    issue_parser.add_argument("--customer-id", required=True)
    issue_parser.add_argument("--customer-name", required=True)
    issue_parser.add_argument("--limit", type=int, required=True)
    issue_parser.add_argument("--expires", default="")

    machine_parser = subparsers.add_parser("machine-code", help="读取本机机器码")
    machine_parser.add_argument("--data-dir", type=Path, required=True)

    export_parser = subparsers.add_parser("export-windows-key", help="导出 Windows 原生工具加密私钥")
    export_parser.add_argument("--issuer-dir", type=Path, required=True)
    export_parser.add_argument("--output", type=Path)
    subparsers.add_parser("gui", help="打开图形界面")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command in (None, "gui"):
            launch_gui()
            return 0
        if args.command == "init":
            result = initialize_issuer(args.issuer_dir, args.public_key, force=args.force)
        elif args.command == "machine-code":
            result = get_machine_identity(args.data_dir)
        elif args.command == "export-windows-key":
            result = {"windows_private_key": str(export_windows_private_key(args.issuer_dir, args.output))}
        elif args.command == "issue":
            result = issue_license(
                issuer_dir=args.issuer_dir,
                data_root=args.data_dir,
                customer_id=args.customer_id,
                customer_name=args.customer_name,
                installation_limit=args.limit,
                expires_at=args.expires,
            )
        else:
            raise ValueError("未知命令")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
