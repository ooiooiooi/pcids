def user_identity(user):
    """Public identity only; never serialize account credentials or permissions."""
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "display_name": getattr(user, "display_name", None),
        "avatar_url": getattr(user, "avatar_url", None),
    }
