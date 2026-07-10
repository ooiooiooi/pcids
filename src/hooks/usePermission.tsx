/**
 * 权限管理 Hook
 */
import { useCallback } from 'react'
import { create } from 'zustand'

// 权限 Store
interface PermissionStore {
  permissions: string[]
  menus: any[]
  setPermissions: (permissions: string[]) => void
  setMenus: (menus: any[]) => void
  hasPermission: (code: string) => boolean
  hasMenu: (path: string) => boolean
  clear: () => void
}

const usePermissionStore = create<PermissionStore>((set, get) => ({
  permissions: [],
  menus: [],
  setPermissions: (permissions) => set({ permissions }),
  setMenus: (menus) => set({ menus }),
  hasPermission: (code) => {
    const { permissions } = get()
    // 管理员拥有所有权限
    if (permissions.includes('all')) return true
    return permissions.includes(code)
  },
  hasMenu: (path) => {
    const { menus } = get()
    const checkMenu = (menuList: any[]): boolean => {
      for (const menu of menuList) {
        if (menu.path === path) return true
        if (menu.children && checkMenu(menu.children)) return true
      }
      return false
    }
    return checkMenu(menus)
  },
  clear: () => set({ permissions: [], menus: [] }),
}))

// 权限 Hook
export const usePermission = () => {
  const store = usePermissionStore()

  const checkPermission = useCallback(
    (code: string): boolean => {
      return store.hasPermission(code)
    },
    [store]
  )

  const checkMenu = useCallback(
    (path: string): boolean => {
      return store.hasMenu(path)
    },
    [store]
  )

  return {
    permissions: store.permissions,
    menus: store.menus,
    hasPermission: checkPermission,
    hasMenu: checkMenu,
    setPermissions: store.setPermissions,
    setMenus: store.setMenus,
    clearPermissions: store.clear,
  }
}

// 权限组件 props
export interface PermissionProps {
  code: string
  children: React.ReactNode
  fallback?: React.ReactNode
}

// 权限组件
export const Permission: React.FC<PermissionProps> = ({
  code,
  children,
  fallback = null,
}) => {
  const { hasPermission } = usePermission()

  if (hasPermission(code)) {
    return <>{children}</>
  }

  return <>{fallback}</>
}

// 菜单过滤 Hook
export const useFilterMenus = () => {
  const { hasPermission } = usePermission()

  const filterMenus = useCallback(
    (menus: any[]) => {
      const filter = (menuList: any[]): any[] => {
        return menuList
          .filter((menu) => {
            const pathKey = menu.path?.replace(/^\//, '') || ''
            return hasPermission(`${pathKey}:view`)
          })
          .map((menu) => ({
            ...menu,
            children: menu.children ? filter(menu.children) : [],
          }))
          .filter((menu) => !menu.children || menu.children.length > 0)
      }
      return filter(menus)
    },
    [hasPermission]
  )

  return filterMenus
}

export default usePermissionStore
