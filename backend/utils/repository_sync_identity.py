from __future__ import annotations

import uuid
import re
from typing import Any


def _identity_text(value: Any) -> str:
    return str(value or "").strip()


def build_repository_sync_identity_seed(
    *,
    project_key: Any,
    display_path: Any = None,
    download_uri: Any = None,
    name: Any = None,
) -> str | None:
    """Build the stable identity seed shared by runtime writes and migrations.

    Repository paths are preferred because CodeArts download URLs can contain
    short-lived query parameters.  Rows without a project and stable path do
    not have enough information for a deterministic cross-database identity.
    """

    normalized_project_key = _identity_text(project_key)
    stable_path = _identity_text(display_path) or _identity_text(download_uri)
    if not normalized_project_key or not stable_path:
        return None
    return f"{normalized_project_key}|{stable_path}|{_identity_text(name)}"


def generate_repository_sync_uuid(
    *,
    project_key: Any,
    display_path: Any = None,
    download_uri: Any = None,
    name: Any = None,
) -> str:
    """Return a deterministic UUID when a stable repository identity exists.

    Manual/local rows that do not have a stable path deliberately receive a
    random UUID.  This matches the existing runtime behavior while allowing
    independently upgraded copies of CodeArts-backed data to converge on the
    same sync identity.
    """

    seed = build_repository_sync_identity_seed(
        project_key=project_key,
        display_path=display_path,
        download_uri=download_uri,
        name=name,
    )
    if seed:
        return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex
    return uuid.uuid4().hex


def generate_codearts_repository_sync_uuid(
    *,
    project_key: Any,
    remote_repo_id: Any = None,
    display_path: Any = None,
    name: Any = None,
    repository_mode: Any = None,
) -> str:
    """Return the identity used by CodeArts snapshot synchronization.

    Keeping this seed in a dependency-free helper lets legacy database
    migrations and live CodeArts refreshes generate exactly the same UUID.
    """

    normalized_project_key = _identity_text(project_key)
    normalized_name = _identity_text(name) or "unknown"
    normalized_path = _identity_text(display_path)
    if not normalized_path or normalized_path == "/":
        normalized_path = f"/{normalized_name}"
    else:
        normalized_path = re.sub(r"^/+", "/", normalized_path)
    sync_key = f"{_identity_text(remote_repo_id)}|{normalized_path}".lower()
    seed = f"{normalized_project_key}|{sync_key}|codearts_sync"
    if _identity_text(repository_mode).lower() == "private":
        seed = f"{seed}|private"
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex
