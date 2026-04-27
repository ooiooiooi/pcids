import { Outlet, useNavigate } from 'react-router-dom'
import { useState, useEffect, useMemo } from 'react'
import { Layout, Menu, Badge, Drawer, Modal, Tabs, Form, Input, Button, Upload, message, List } from 'antd'
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
  BellOutlined,
  UserOutlined,
  MinusOutlined,
  CloseOutlined,
} from '@ant-design/icons'
import { usePermission } from './hooks'
import { permissionApi } from './services/permission'
import { authApi, messageApi } from './services/api'

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
  const [userInfo, setUserInfo] = useState<any>({})
  
  // Profile Modal State
  const [isProfileOpen, setIsProfileOpen] = useState(false)
  const [profileTab, setProfileTab] = useState('info')
  const [infoForm] = Form.useForm()
  const [pwdForm] = Form.useForm()

  // Messages Drawer State
  const [isMessageOpen, setIsMessageOpen] = useState(false)
  const [messages, setMessages] = useState<any[]>([])
  const [unreadCount, setUnreadCount] = useState(0)

  const userStr = localStorage.getItem('user')
  const username = userStr ? JSON.parse(userStr).username : '管理员'

  const fetchUserInfo = async () => {
    try {
      const res: any = await authApi.getMe()
      if (res.code === 0) {
        setUserInfo(res.data)
        infoForm.setFieldsValue({ username: res.data.username })
      }
    } catch {
      // ignore
    }
  }

  const fetchMessages = async () => {
    try {
      const res: any = await messageApi.getList({ page: 1, page_size: 100 })
      if (res.code === 0) {
        setMessages(res.data || [])
        setUnreadCount(res.data?.filter((m: any) => !m.is_read).length || 0)
      }
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    fetchUserInfo()
    fetchMessages()
  }, [])

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
    localStorage.removeItem('username')
    navigate('/login')
  }

  const handleUpdateProfile = async (values: any) => {
    try {
      const res: any = await authApi.updateMe(values)
      if (res.code === 0) {
        message.success('修改成功')
        fetchUserInfo()
        setIsProfileOpen(false)
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '修改失败')
    }
  }

  const handleUpdatePassword = async (values: any) => {
    if (values.newPassword !== values.confirmPassword) {
      message.error('两次输入密码不一致')
      return
    }
    try {
      const res: any = await authApi.updatePassword({
        old_password: values.oldPassword,
        new_password: values.newPassword
      })
      if (res.code === 0) {
        message.success('密码修改成功，请重新登录')
        handleLogout()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '密码修改失败')
    }
  }

  const handleReadAll = async () => {
    try {
      await messageApi.readAll()
      fetchMessages()
    } catch {
      // ignore
    }
  }

  const profileModalContent = (
    <Tabs activeKey={profileTab} onChange={setProfileTab} items={[
      {
        key: 'info',
        label: '基本信息',
        children: (
          <div style={{ display: 'flex', gap: 40, marginTop: 16 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 120 }}>
              <div style={{ width: 80, height: 80, borderRadius: '50%', backgroundColor: '#4f46e5', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24, marginBottom: 16, overflow: 'hidden' }}>
                {userInfo.avatar_url ? <img src={userInfo.avatar_url} alt="avatar" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : (userInfo.username || username || '管理').substring(Math.max(0, (userInfo.username || username || '管理').length - 2))}
              </div>
              <Upload
                showUploadList={false}
                customRequest={async (options) => {
                  const formData = new FormData()
                  formData.append('file', options.file as File)
                  try {
                    const res: any = await authApi.uploadAvatar(formData)
                    if (res.code === 0) {
                      message.success('头像上传成功')
                      fetchUserInfo()
                    }
                  } catch (e: any) {
                    message.error(e?.response?.data?.detail || '上传失败')
                  }
                }}
              >
                <Button size="small" type="dashed">更换头像</Button>
              </Upload>
              <div style={{ fontSize: 12, color: '#999', marginTop: 8, textAlign: 'center' }}>
                支持 JPG / PNG 格式，大小不超过 2MB
              </div>
            </div>
            <div style={{ flex: 1 }}>
              <Form form={infoForm} layout="vertical">
                <Form.Item label="用户账户" name="username">
                  <Input disabled prefix={<UserOutlined style={{ color: '#ccc' }} />} />
                </Form.Item>
                <Form.Item label="邮箱" name="email">
                  <Input placeholder="请输入邮箱" />
                </Form.Item>
              </Form>
            </div>
          </div>
        )
      },
      {
        key: 'pwd',
        label: '修改密码',
        children: (
          <Form form={pwdForm} layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item label="原密码" name="oldPassword" rules={[{ required: true, message: '请输入原密码' }]}>
              <Input.Password placeholder="请输入原密码" />
            </Form.Item>
            <Form.Item label="新密码" name="newPassword" rules={[{ required: true, message: '请输入新密码' }]}>
              <Input.Password placeholder="请输入新密码" />
            </Form.Item>
            <Form.Item label="确认新密码" name="confirmPassword" rules={[{ required: true, message: '请确认新密码' }]}>
              <Input.Password placeholder="请确认新密码" />
            </Form.Item>
          </Form>
        )
      }
    ]} />
  )

  const handleProfileOk = () => {
    if (profileTab === 'info') {
      infoForm.validateFields().then(handleUpdateProfile)
    } else {
      pwdForm.validateFields().then(handleUpdatePassword)
    }
  }

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
            gap: 20
          }}
        >
          <span style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }} onClick={() => setIsProfileOpen(true)}>
            <div style={{ width: 24, height: 24, borderRadius: '50%', backgroundColor: '#4f46e5', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, marginRight: 8, overflow: 'hidden' }}>
              {userInfo.avatar_url ? <img src={userInfo.avatar_url} alt="avatar" style={{ width: '100%', height: '100%', objectFit: 'cover' }} /> : (userInfo.username || username || '管理').substring(Math.max(0, (userInfo.username || username || '管理').length - 2))}
            </div>
          </span>
          <Badge count={unreadCount} size="small">
            <BellOutlined style={{ fontSize: 18, cursor: 'pointer' }} onClick={() => setIsMessageOpen(true)} />
          </Badge>
          <div style={{ width: 1, height: 16, background: '#e8e8e8', margin: '0 8px' }}></div>
          <MinusOutlined style={{ fontSize: 16, color: '#666', cursor: 'pointer' }} />
          <CloseOutlined style={{ fontSize: 16, color: '#666', cursor: 'pointer' }} />
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
      <Modal
        title="个人中心"
        open={isProfileOpen}
        onCancel={() => setIsProfileOpen(false)}
        onOk={handleProfileOk}
        width={500}
        destroyOnClose
        footer={[
          <Button key="logout" danger onClick={handleLogout} style={{ float: 'left' }}>
            退出登录
          </Button>,
          <Button key="cancel" onClick={() => setIsProfileOpen(false)}>
            取消
          </Button>,
          <Button key="submit" type="primary" onClick={handleProfileOk}>
            确定
          </Button>,
        ]}
      >
        {profileModalContent}
      </Modal>
      <Drawer
        title="消息中心"
        placement="right"
        onClose={() => setIsMessageOpen(false)}
        open={isMessageOpen}
        width={400}
        extra={
          <Button type="link" onClick={handleReadAll}>
            全部已读
          </Button>
        }
      >
        <List
          itemLayout="horizontal"
          dataSource={messages}
          renderItem={(item) => (
            <List.Item style={{ opacity: item.is_read ? 0.6 : 1 }}>
              <List.Item.Meta
                title={<span style={{ fontWeight: item.is_read ? 'normal' : 'bold' }}>{item.title}</span>}
                description={
                  <div>
                    <div style={{ color: '#666' }}>{item.content}</div>
                    <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>{item.created_at}</div>
                  </div>
                }
              />
            </List.Item>
          )}
        />
      </Drawer>
    </Layout>
  )
}

export default App
