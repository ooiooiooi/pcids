from __future__ import annotations

from datetime import datetime, timezone


LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc


def database_time_to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
    return value.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)


def local_time_to_database(value: datetime | None) -> datetime | None:
    """Convert a local wall-clock boundary to the UTC-naive database convention."""
    if value is None:
        return None
    local_value = value.astimezone() if value.tzinfo is None else value
    return local_value.astimezone(timezone.utc).replace(tzinfo=None)
