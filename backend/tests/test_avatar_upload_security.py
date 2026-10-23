from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, close_all_sessions, sessionmaker

import backend.models  # noqa: F401
from backend.models.base import Base
from backend.models.user import User
from backend.routers import auth
from backend.utils.db import get_db
from PIL import Image


_CORRUPT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2pK7sAAAAASUVORK5CYII="
)
_PNG_BUFFER = BytesIO()
Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(_PNG_BUFFER, format="PNG")
_PNG_1X1 = _PNG_BUFFER.getvalue()


def _make_client(tmp_path: Path):
    database_path = tmp_path / "avatar-test.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    seed_session = SessionLocal()
    user = User(username="tester", password_hash="hashed", status=1)
    seed_session.add(user)
    seed_session.commit()
    seed_session.refresh(user)
    user_id = user.id
    seed_session.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user(db: Session = Depends(get_db)):
        current_user = db.query(User).filter(User.id == user_id).first()
        assert current_user is not None
        return current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[auth.get_current_user] = override_get_current_user

    upload_root = tmp_path / "uploads"
    avatar_root = tmp_path / "avatars"
    avatar_root.mkdir(parents=True, exist_ok=True)

    patches = [
        patch("backend.routers.auth.get_upload_root", return_value=upload_root),
        patch("backend.routers.auth.get_avatar_upload_root", return_value=avatar_root),
    ]
    for item in patches:
        item.start()

    client = TestClient(app)
    return client, SessionLocal, user_id, patches, avatar_root, upload_root


def test_avatar_upload_rejects_html_disguised_as_png(tmp_path: Path):
    client, SessionLocal, _user_id, patches, _avatar_root, _upload_root = _make_client(tmp_path)
    try:
        response = client.post(
            "/api/auth/avatar",
            files={"file": ("avatar.png", b"<html><body>xss</body></html>", "image/png")},
        )
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()

    assert response.status_code == 400
    assert response.json()["detail"] == "上传内容不是合法图片"


def test_avatar_upload_rejects_corrupt_png(tmp_path: Path):
    client, _, _, patches, _, _ = _make_client(tmp_path)
    try:
        response = client.post("/api/auth/avatar", files={"file": ("avatar.png", _CORRUPT_PNG, "image/png")})
        assert response.status_code == 400
        assert "安全解码" in response.json()["detail"]
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()


def test_avatar_upload_reencodes_and_strips_metadata(tmp_path: Path):
    from PIL.PngImagePlugin import PngInfo
    output = BytesIO()
    metadata = PngInfo()
    metadata.add_text("Comment", "<script>marker</script>")
    Image.new("RGB", (2, 2)).save(output, format="PNG", pnginfo=metadata)
    client, _, _, patches, avatar_root, _ = _make_client(tmp_path)
    try:
        response = client.post("/api/auth/avatar", files={"file": ("avatar.png", output.getvalue(), "image/png")})
        assert response.status_code == 200
        assert b"<script>" not in next(avatar_root.iterdir()).read_bytes()
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()


def test_avatar_upload_rejects_file_over_2mb(tmp_path: Path):
    client, SessionLocal, _user_id, patches, _avatar_root, _upload_root = _make_client(tmp_path)
    try:
        response = client.post(
            "/api/auth/avatar",
            files={"file": ("avatar.png", b"0" * (2 * 1024 * 1024 + 1), "image/png")},
        )
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()

    assert response.status_code == 400
    assert response.json()["detail"] == "文件过大（最大 2MB）"


def test_avatar_upload_serves_png_with_nosniff_header(tmp_path: Path):
    client, SessionLocal, user_id, patches, avatar_root, _upload_root = _make_client(tmp_path)
    try:
        upload_response = client.post(
            "/api/auth/avatar",
            files={"file": ("avatar.png", _PNG_1X1, "image/png")},
        )
        assert upload_response.status_code == 200

        payload = upload_response.json()
        avatar_url = payload["data"]
        avatar_path = Path(avatar_url.replace("http://testserver", ""))

        fetch_response = client.get(str(avatar_path))
        stored_files = list(avatar_root.iterdir())
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()

    verify = SessionLocal()
    try:
        user = verify.query(User).filter(User.id == user_id).first()
        assert user is not None
        stored_avatar_url = user.avatar_url
    finally:
        verify.close()

    assert fetch_response.status_code == 200
    assert fetch_response.headers["content-type"].startswith("image/png")
    assert fetch_response.headers["x-content-type-options"] == "nosniff"
    assert stored_avatar_url == avatar_url
    assert len(stored_files) == 1
    assert stored_files[0].suffix.lower() == ".png"


def test_sanitize_stored_avatars_removes_invalid_legacy_html_file(tmp_path: Path):
    database_path = tmp_path / "avatar-cleanup.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = Session()
    user = User(username="cleanup", password_hash="hashed", status=1, avatar_url="/uploads/bad.png")
    db.add(user)
    db.commit()
    db.close()

    upload_root = tmp_path / "uploads"
    avatar_root = upload_root / "avatars"
    upload_root.mkdir(parents=True, exist_ok=True)
    avatar_root.mkdir(parents=True, exist_ok=True)
    bad_file = upload_root / "bad.png"
    bad_file.write_bytes(b"<script>alert(1)</script>")

    with patch("backend.routers.auth.SessionLocal", Session), patch(
        "backend.routers.auth.get_upload_root", return_value=upload_root
    ), patch(
        "backend.routers.auth.get_avatar_upload_root", return_value=avatar_root
    ):
        summary = auth.sanitize_stored_avatars()

    verify = Session()
    try:
        refreshed = verify.query(User).filter(User.username == "cleanup").first()
        assert refreshed is not None
        assert refreshed.avatar_url is None
    finally:
        verify.close()

    assert summary["removed"] == 1
    assert bad_file.exists() is False


def test_legacy_api_avatar_migrates_from_old_directory(tmp_path: Path):
    client, sessions, user_id, patches, avatar_root, upload_root = _make_client(tmp_path)
    legacy_root = upload_root / "avatars"
    legacy_root.mkdir(parents=True)
    legacy = legacy_root / "old.png"
    legacy.write_bytes(_PNG_1X1)
    try:
        with sessions() as db:
            db.get(User, user_id).avatar_url = "/api/auth/avatar/old.png"
            db.commit()
        with patch("backend.routers.auth.SessionLocal", sessions):
            auth.sanitize_stored_avatars()
        with sessions() as db:
            url = db.get(User, user_id).avatar_url
        assert url != "/api/auth/avatar/old.png"
        assert client.get(url).status_code == 200
        assert not legacy.exists()
        assert len(list(avatar_root.iterdir())) == 1
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()


def test_avatar_symlink_is_never_served_or_reencoded(tmp_path: Path):
    client, sessions, user_id, patches, avatar_root, _ = _make_client(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_1X1)
    link = avatar_root / "linked.png"
    link.symlink_to(outside)
    try:
        assert client.get("/api/auth/avatar/linked.png").status_code == 404
        with sessions() as db:
            db.get(User, user_id).avatar_url = "/api/auth/avatar/linked.png"
            db.commit()
        with patch("backend.routers.auth.SessionLocal", sessions):
            auth.sanitize_stored_avatars()
        assert not link.is_symlink()
        assert outside.read_bytes() == _PNG_1X1
        with sessions() as db:
            assert db.get(User, user_id).avatar_url is None
    finally:
        for item in reversed(patches):
            item.stop()
        close_all_sessions()


def test_nosniff_covers_unhandled_errors_and_cors_preflight():
    from fastapi.middleware.cors import CORSMiddleware
    from backend.main import SecurityHeadersMiddleware
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["http://frontend"], allow_methods=["*"])
    app.add_middleware(SecurityHeadersMiddleware)
    @app.get("/error")
    def unexpected_error():
        raise RuntimeError("private internal information")
    with TestClient(app) as client:
        error = client.get("/error")
        assert error.status_code == 500
        assert error.headers["x-content-type-options"] == "nosniff"
        assert "private internal information" not in error.text
        preflight = client.options("/error", headers={
            "Origin": "http://frontend", "Access-Control-Request-Method": "GET",
        })
        assert preflight.status_code == 200
        assert preflight.headers["x-content-type-options"] == "nosniff"
