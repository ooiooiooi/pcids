from __future__ import annotations

import ftplib
import io
import os
import posixpath
import sys
from pathlib import Path


def ensure_directory(client: ftplib.FTP, directory: str) -> None:
    client.cwd("/")
    for segment in (part for part in directory.split("/") if part):
        try:
            client.cwd(segment)
        except ftplib.error_perm:
            client.mkd(segment)
            client.cwd(segment)


def upload(passive: bool) -> str:
    artifact = Path(os.environ.get("PCIDS_ARTIFACT_PATH") or os.environ.get("FIRMWARE_PATH") or "")
    host = os.environ.get("PCIDS_TARGET_HOST", "")
    port = int(os.environ.get("PCIDS_TARGET_PORT") or "21")
    username = os.environ.get("PCIDS_TARGET_USERNAME", "")
    password = os.environ.get("PCIDS_TARGET_PASSWORD", "")
    install_dir = os.environ.get("INSTALL_DIR") or "/apps"
    if not artifact.is_file():
        raise RuntimeError(f"package does not exist: {artifact}")
    if not host or not username:
        raise RuntimeError("FTP target host/username is missing")

    with ftplib.FTP(timeout=60) as client:
        client.connect(host, port, timeout=60)
        client.login(username, password)
        client.set_pasv(passive)
        ensure_directory(client, install_dir)
        with artifact.open("rb") as handle:
            client.storbinary(f"STOR {artifact.name}", handle)
        remote_path = posixpath.join(install_dir.rstrip("/") or "/", artifact.name)
        try:
            client.voidcmd(f"SITE CHMOD 755 {artifact.name}")
        except ftplib.all_errors:
            pass
        if os.environ.get("PCIDS_CONFIG_BOOT_AUTOSTART", "").lower() == "true":
            startup_path = "/etc/startup.sh"
            chunks: list[bytes] = []
            try:
                client.retrbinary(f"RETR {startup_path}", chunks.append)
                startup = b"".join(chunks).decode("utf-8", errors="ignore")
            except ftplib.error_perm:
                startup = "#!/bin/sh\n"
            if remote_path not in {line.strip() for line in startup.splitlines()}:
                startup = startup.rstrip() + "\n" + remote_path + "\n"
                ensure_directory(client, posixpath.dirname(startup_path))
                client.storbinary("STOR startup.sh", io.BytesIO(startup.encode("utf-8")))
        client.quit()
        return remote_path


def main() -> int:
    errors: list[str] = []
    for passive in (True, False):
        try:
            remote_path = upload(passive)
            print(f"[INSTALL] FTP {'PASV' if passive else 'PORT'} upload={remote_path}")
            print("[INSTALL] completed")
            return 0
        except Exception as exc:
            errors.append(f"{'PASV' if passive else 'PORT'}: {exc}")
    print("[ERROR] " + " | ".join(errors))
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
