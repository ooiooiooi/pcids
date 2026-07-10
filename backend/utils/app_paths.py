import os
from pathlib import Path


def get_app_data_root() -> Path:
    configured = str(os.environ.get("PCIDS_DATA_DIR") or "").strip()
    if configured:
        root = Path(configured).expanduser()
    elif os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "PCIDS"
    else:
        root = Path.home() / ".local" / "share" / "PCIDS"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=False)


def get_upload_root() -> Path:
    root = get_app_data_root() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_repository_download_root_path() -> Path:
    root = get_upload_root() / "repositories"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_product_upload_root() -> Path:
    root = get_upload_root() / "products"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_task_runs_root() -> Path:
    root = get_upload_root() / "task_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root
