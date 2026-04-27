from backend.utils.db import SessionLocal
from backend.models.permission import Menu, Permission, RolePermission
from backend.models.role import Role

db = SessionLocal()
try:
    # 删除权限管理菜单及其关联权限
    menu = db.query(Menu).filter(Menu.path == "/permission").first()
    if menu:
        perms = db.query(Permission).filter(Permission.menu_id == menu.id).all()
        for p in perms:
            db.query(RolePermission).filter(RolePermission.permission_id == p.id).delete()
            db.delete(p)
        db.delete(menu)
        db.commit()
        print("已清理权限管理菜单和相关权限点。")
    else:
        print("未找到权限管理菜单。")
finally:
    db.close()
