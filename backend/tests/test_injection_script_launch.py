from pathlib import Path

from backend.routers import injections


def test_source_backend_uses_python_script_directly(monkeypatch):
    monkeypatch.setattr(injections.sys, "executable", r"C:\Python312\python.exe")
    monkeypatch.delattr(injections.sys, "frozen", raising=False)

    command = injections._build_injection_script_command(
        Path(r"D:\pcids\storage_full.py"),
        "192.168.137.162",
        '{"size":1}',
    )

    assert command == [
        r"C:\Python312\python.exe",
        r"D:\pcids\storage_full.py",
        "192.168.137.162",
        '{"size":1}',
    ]


def test_frozen_backend_uses_run_script_entry_mode(monkeypatch):
    monkeypatch.setattr(
        injections.sys,
        "executable",
        r"C:\Program Files\pcids\resources\backend\pcids_backend.exe",
    )
    monkeypatch.setattr(injections.sys, "frozen", True, raising=False)

    command = injections._build_injection_script_command(
        Path(r"C:\Program Files\pcids\resources\backend\scripts\injections\storage_full.py"),
        "192.168.137.162",
        '{"size":1}',
    )

    assert command == [
        r"C:\Program Files\pcids\resources\backend\pcids_backend.exe",
        "--run-script",
        r"C:\Program Files\pcids\resources\backend\scripts\injections\storage_full.py",
        "192.168.137.162",
        '{"size":1}',
    ]
