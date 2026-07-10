/**
 * 权限服务 API
 */
import request from './api'

// 获取用户权限
export const permissionApi = {
  // 获取当前用户权限
  getMyPermissions: () => request.get('/permissions/my'),

  // 获取菜单树
  getMenus: () => request.get('/permissions/menus'),

  // 获取所有权限点
  getPermissions: (menuId?: number) =>
    request.get('/permissions/permissions', { params: { menu_id: menuId } }),

  // 创建权限点
  createPermission: (data: {
    name: string
    code: string
    type: string
    menu_id?: number
    api_path?: string
    api_method?: string
  }) => request.post('/permissions/permissions', data),

  // 删除权限点
  deletePermission: (id: number) =>
    request.delete(`/permissions/permissions/${id}`),

  // 获取角色权限
  getRolePermissions: (roleId: number) =>
    request.get(`/permissions/roles/${roleId}/permissions`),

  // 分配权限给角色
  assignRolePermissions: (roleId: number, permissionIds: number[]) =>
    request.post(`/permissions/roles/${roleId}/permissions`, {
      permission_ids: permissionIds,
    }),

  // 创建菜单
  createMenu: (data: {
    name: string
    path: string
    icon?: string
    parent_id?: number
    sort_order?: number
    is_hidden?: boolean
  }) => request.post('/permissions/menus', data),

  // 更新菜单
  updateMenu: (
    id: number,
    data: {
      name?: string
      path?: string
      icon?: string
      sort_order?: number
      is_hidden?: boolean
    }
  ) => request.put(`/permissions/menus/${id}`, data),

  // 删除菜单
  deleteMenu: (id: number) => request.delete(`/permissions/menus/${id}`),
}
