import { Outlet, useNavigate } from 'react-router-dom'
import { useState, useEffect, useMemo } from 'react'
import { Layout, Menu } from 'antd'
import type { MenuProps } from 'antd'
import {
  DesktopOutlined,
  DatabaseOutlined,
  InboxOutlined,
  FireOutlined,
  FileTextOutlined,
  BugOutlined,
  WifiOutlined,
  SettingOutlined,
  TeamOutlined,
  FileProtectOutlined,
  CodeOutlined,
  BarChartOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons'
import { usePermission } from './hooks'
import { permissionApi } from './services/permission'

const { Header, Sider, Content } = Layout

type MenuItem = Required<MenuProps>['items'][number]

const iconMap: Record<string, React.ReactNode> = {
  DesktopOutlined: <DesktopOutlined />,
  DatabaseOutlined: <DatabaseOutlined />,
  InboxOutlined: <InboxOutlined />,
  FireOutlined: <FireOutlined />,
  FileTextOutlined: <FileTextOutlined />,
  BugOutlined: <BugOutlined />,
  WifiOutlined: <WifiOutlined />,
  SettingOutlined: <SettingOutlined />,
  TeamOutlined: <TeamOutlined />,
  FileProtectOutlined: <FileProtectOutlined />,
  CodeOutlined: <CodeOutlined />,
  BarChartOutlined: <BarChartOutlined />,
}

function getIcon(iconName?: string): React.ReactNode {
  if (!iconName) return null
  return iconMap[iconName] || null
}

function getItem(
  label: string,
  key: string,
  icon?: React.ReactNode,
  children?: MenuItem[],
): MenuItem {
  return { key, icon, children, label } as MenuItem
}

const pathIconMap: Record<string, React.ReactNode> = {
  '/workbench': <DesktopOutlined />,
  '/repository': <DatabaseOutlined />,
  '/product': <CodeOutlined />,
  '/burner': <FireOutlined />,
  '/script': <FileProtectOutlined />,
  '/burning': <FireOutlined />,
  '/record': <FileTextOutlined />,
  '/injection': <BugOutlined />,
  '/protocol': <WifiOutlined />,
  '/user': <TeamOutlined />,
  '/role': <TeamOutlined />,
  '/log/login': <BarChartOutlined />,
  '/log/operation': <FileTextOutlined />,
}

const App: React.FC = () => {
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)
  const [selectedKey, setSelectedKey] = useState('/workbench')
  const { menus, hasPermission, setPermissions, setMenus } = usePermission()

  // Build menu items from stored menu data + permission filtering
  const menuItems: MenuItem[] = useMemo(() => {
    // If we have backend menu data, use it
    if (menus && menus.length > 0) {
      const buildMenuItems = (menuList: any[]): MenuItem[] => {
        return menuList
          .filter((menu) => {
            // Parent folders with empty path are always included
            if (!menu.path) return true
            const pathKey = menu.path.replace(/^\//, '')
            const permCode = `${pathKey}:view`
            return hasPermission(permCode)
          })
          .map((menu) => {
            const children = menu.children ? buildMenuItems(menu.children) : []
            // Only return null if the menu HAD children but ALL were filtered out
            if (menu.children.length > 0 && children.length === 0) return null
            return getItem(
              menu.name,
              menu.path,
              getIcon(menu.icon) || pathIconMap[menu.path],
              children.length > 0 ? children : undefined,
            )
          })
          .filter(Boolean)
      }
      return buildMenuItems(menus)
    }

    // Fallback to hardcoded menu matching prototype
    return [
      getItem('工作台', '/workbench', <DesktopOutlined />),
      getItem('制品仓库', '/repository', <DatabaseOutlined />),
      getItem('资产管理', 'asset', <InboxOutlined />, [
        getItem('产品管理', '/product', <CodeOutlined />),
        getItem('烧录器管理', '/burner', <FireOutlined />),
        getItem('脚本管理', '/script', <FileProtectOutlined />),
      ]),
      getItem('烧录安装管理', '/burning', <FireOutlined />),
      getItem('履历记录', '/record', <FileTextOutlined />),
      getItem('异常注入', '/injection', <BugOutlined />),
      getItem('通信协议验证', '/protocol', <WifiOutlined />),
      getItem('系统管理', 'system', <SettingOutlined />, [
        getItem('用户管理', '/user', <TeamOutlined />),
        getItem('角色管理', '/role', <TeamOutlined />),
        getItem('登录日志', '/log/login', <BarChartOutlined />),
        getItem('操作日志', '/log/operation', <FileTextOutlined />),
      ]),
    ]
  }, [menus, hasPermission])

  useEffect(() => {
    const token = localStorage.getItem('token')
    if (!token) {
      navigate('/login')
      return
    }
    // Always fetch fresh permissions and menus from backend
    Promise.all([
      permissionApi.getMyPermissions().catch(() => ({ code: 1, data: [] })),
      permissionApi.getMenus().catch(() => ({ code: 1, data: [] })),
    ]).then(([permsRes, menusRes]) => {
      if ((permsRes as any).code === 0 && (permsRes as any).data?.length > 0) {
        setPermissions((permsRes as any).data)
      }
      if ((menusRes as any).code === 0 && (menusRes as any).data?.length > 0) {
        setMenus((menusRes as any).data)
      }
    })
    const path = window.location.hash.replace('#', '') || '/workbench'
    setSelectedKey(path)
  }, [navigate, setPermissions, setMenus])

  const handleMenuClick: MenuProps['onClick'] = (e) => {
    setSelectedKey(e.key)
    navigate(e.key)
  }

  const handleLogout = () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    navigate('/login')
  }

  const userStr = localStorage.getItem('user')
  const username = userStr ? JSON.parse(userStr).username : '管理员'

  return (
    <Layout style={{ height: '100%' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        theme="light"
        onCollapse={(value) => setCollapsed(value)}
        style={{ borderRight: '1px solid #f0f0f0' }}
      >
        <div
          style={{
            height: 32,
            margin: 16,
            background: 'rgba(64, 69, 214, 0.08)',
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'rgba(0, 0, 0, 0.88)',
            fontWeight: 'bold',
            fontSize: collapsed ? 12 : 14,
          }}
        >
          {collapsed ? 'PCIDS' : '程控安装部署系统'}
        </div>
        <Menu
          theme="light"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{ background: '#F8F3F0' }}
        />
        {!collapsed && (
          <div style={{ position: 'absolute', bottom: 16, left: 0, right: 0, textAlign: 'center', color: 'rgba(0,0,0,0.35)', fontSize: 12 }}>
            v1.0.0
          </div>
        )}
      </Sider>
      <Layout>
        <Header
          style={{
            padding: '0 24px',
            background: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
          }}
        >
          <span style={{ marginRight: 16 }}>欢迎，{username}</span>
          <span style={{ marginRight: 8, cursor: 'pointer' }}><QuestionCircleOutlined style={{ fontSize: 14 }} title="帮助" />帮助</span>
          <a onClick={handleLogout}>退出登录</a>
        </Header>
        <Content
          style={{
            margin: 16,
            padding: 24,
            background: '#F5F7FA',
            borderRadius: 6,
            overflow: 'auto',
          }}
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}

export default App
