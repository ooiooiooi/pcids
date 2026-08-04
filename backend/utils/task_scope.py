from __future__ import annotations

from sqlalchemy import false, or_
from sqlalchemy.orm import Query, Session

from backend.models.repository import Repository, RepositoryProjectMember
from backend.models.task import BurningTask
from backend.models.user import User


def apply_task_scope(query: Query, db: Session, current_user: User) -> Query:
    """Apply the canonical role/project data scope to a task query.

    Unknown or malformed scopes deliberately fail closed.  Keeping this in a
    shared module prevents list, report, and aggregate endpoints from drifting
    into different authorization rules.
    """
    try:
        data_scope = getattr(getattr(current_user, "role", None), "data_scope", None)
    except Exception:
        return query.filter(false())
    if not isinstance(data_scope, str):
        return query.filter(false())

    data_scope = data_scope.strip()
    if data_scope == "all":
        return query
    if data_scope == "self":
        return query.filter(BurningTask.created_by_user_id == current_user.id)
    if data_scope == "project":
        member_project_keys = [
            row[0]
            for row in db.query(RepositoryProjectMember.project_key)
            .filter(RepositoryProjectMember.user_id == current_user.id)
            .all()
        ]
        return query.outerjoin(Repository, Repository.id == BurningTask.repository_id).filter(
            or_(
                BurningTask.created_by_user_id == current_user.id,
                Repository.project_key.in_(member_project_keys),
            )
        )
    if data_scope.startswith("tenant:"):
        tenant = data_scope.split(":", 1)[1].strip()
        if not tenant:
            return query.filter(false())
        return query.join(Repository, Repository.id == BurningTask.repository_id).filter(
            Repository.tenant == tenant
        )
    if data_scope.startswith("project:"):
        allowed = {item.strip() for item in data_scope.split(":", 1)[1].split(",") if item.strip()}
        if not allowed:
            return query.filter(false())
        return query.join(Repository, Repository.id == BurningTask.repository_id).filter(
            Repository.project_key.in_(sorted(allowed))
        )
    return query.filter(false())
