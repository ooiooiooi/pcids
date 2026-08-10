import { useLocation, useNavigate } from 'react-router-dom'
import React, { useState, useEffect, useMemo } from 'react'
import { Layout, Menu, Badge, Drawer, Modal, Tabs, Form, Input, Button, Upload, message, List, Spin, Dropdown, ConfigProvider } from 'antd'
import type { MenuProps } from 'antd'
import {
  BellOutlined,
  UserOutlined,
  QuestionCircleOutlined,
  LeftOutlined,
  RightOutlined,
  LockOutlined
} from '@ant-design/icons'
import { usePermission } from './hooks'
import { permissionApi } from './services/permission'
import { authApi, dashboardApi, messageApi } from './services/api'
import { formatDateTime, getDateTimeSortValue } from './utils/dateTime'
import { UserAvatar } from './components/UserIdentity'
import EllipsisText from './components/EllipsisText'
import DesktopWindowControls from './components/DesktopWindowControls'
import Workbench from './pages/Workbench'
import Repository from './pages/Repository'
import Burning from './pages/Burning'
import Injection from './pages/Injection'
import Protocol from './pages/Protocol'
import Record from './pages/Record'
import Product from './pages/Product'
import Burner from './pages/Burner'
import Script from './pages/Script'
import LoginLog from './pages/LoginLog'
import OperationLog from './pages/OperationLog'
import User from './pages/User'
import SoftwareLogo from './assets/images/software-logo.svg'
import Role from './pages/Role'

const { Header, Sider, Content } = Layout

type MenuItem = Required<MenuProps>['items'][number]
type AppBootstrapData = {
  me: any
  permissions: string[]
  menus: any[]
  messages: any[]
}

type MessageCenterItem = {
  id: string | number
  title: string
  content: string
  created_at: string
  is_read: boolean
  status?: 'success' | 'error' | 'info'
  source?: 'message' | 'notification'
  category?: string
  status_label?: string
  primary_text?: string
  meta_text?: string
  detail_text?: string
}

const APP_BOOTSTRAP_CACHE_KEY = 'pcids.appBootstrap'
const MESSAGE_CENTER_PAGE_SIZE = 20
const MESSAGE_CENTER_READ_CACHE_KEY = 'pcids.messageCenterReadCache'

const readMessageCenterReadCache = (): string[] => {
  try {
    const raw = localStorage.getItem(MESSAGE_CENTER_READ_CACHE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.map((item) => String(item)) : []
  } catch {
    return []
  }
}

const writeMessageCenterReadCache = (ids: string[]) => {
  try {
    localStorage.setItem(MESSAGE_CENTER_READ_CACHE_KEY, JSON.stringify(Array.from(new Set(ids))))
  } catch {
    // ignore persistence failures
  }
}

const normalizeMessageCenterTime = (value: string) => {
  return getDateTimeSortValue(value)
}

const sortMessageCenterItems = (items: MessageCenterItem[]) =>
  [...items].sort((a, b) => normalizeMessageCenterTime(b.created_at) - normalizeMessageCenterTime(a.created_at))

const dedupeMessageCenterItems = (items: MessageCenterItem[]) => {
  const next: MessageCenterItem[] = []
  const seen = new Set<string>()
  for (const item of items) {
    // 同一条业务消息可能同时来自 /messages 与 /dashboard/stats.notifications。
    // 仪表盘侧会把 id 包装成 message-<id>，前面已归一为同一个 id，因此这里按 id + 时间去重，
    // 避免因为 source/title 不同而把同一条消息渲染两次。
    const dedupeKey = `${String(item.id)}|${item.created_at}`
    if (seen.has(dedupeKey)) continue
    seen.add(dedupeKey)
    next.push(item)
  }
  return sortMessageCenterItems(next)
}

const parseMessagePayload = (value: any): Record<string, any> => {
  const text = String(value || '').trim()
  if (!text) return {}
  const candidates = [text]
  const objectStart = text.indexOf('{')
  const objectEnd = text.lastIndexOf('}')
  if (objectStart >= 0 && objectEnd > objectStart) {
    candidates.push(text.slice(objectStart, objectEnd + 1))
  }
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed
    } catch {
      // try next candidate
    }
  }
  return {}
}

const getMessageField = (...values: any[]) => {
  for (const value of values) {
    const text = String(value || '').trim()
    if (text) return text
  }
  return ''
}

const normalizeServerMessages = (items: any[], readCache: string[]): MessageCenterItem[] =>
  (Array.isArray(items) ? items : []).map((item: any) => {
    const id = String(item?.id ?? '')
    const contentPayload = parseMessagePayload(item?.content)
    const titlePayload = parseMessagePayload(item?.title)
    const payload = { ...titlePayload, ...contentPayload }
    const contentText = String(item?.content || '').trim()
    const contentIsPayload = Object.keys(contentPayload).length > 0
    const rawStatus = getMessageField(item?.status, payload.status)
    return {
      id,
      title: String(item?.title || '').trim() || '系统消息',
      content: contentIsPayload ? '' : contentText,
      created_at: getMessageField(item?.event_time, payload.event_time, item?.created_at),
      is_read: Boolean(item?.is_read) || readCache.includes(id),
      status: rawStatus === 'success' || rawStatus === 'error' ? rawStatus : 'info',
      category: getMessageField(item?.category, payload.category),
      status_label: getMessageField(item?.status_label, payload.status_label),
      primary_text: getMessageField(item?.primary_text, payload.primary_text),
      meta_text: getMessageField(item?.meta_text, payload.meta_text),
      detail_text: getMessageField(item?.detail_text, payload.detail_text, item?.detail_content, payload.detail_content, contentIsPayload ? '' : contentText),
      source: 'message',
    }
  })

const mapDashboardNotificationsToMessages = (items: any[], readCache: string[]): MessageCenterItem[] =>
  (Array.isArray(items) ? items : []).map((item: any, index: number) => {
    const rawId = String(item?.id || `notification-${index}`)
    const id = rawId.startsWith('message-') ? rawId.slice('message-'.length) : rawId
    const textPayload = parseMessagePayload(item?.text)
    const text = String(item?.text || '').trim()
    const textIsPayload = Object.keys(textPayload).length > 0
    const rawStatus = getMessageField(item?.status, textPayload.status)
    return {
      id,
      title:
        rawStatus === 'success'
          ? '成功通知'
          : rawStatus === 'error'
            ? '异常通知'
            : '动态通知',
      content: textIsPayload ? '' : (text || '系统动态更新'),
      created_at: getMessageField(item?.event_time, textPayload.event_time, item?.time),
      is_read: readCache.includes(id) || readCache.includes(rawId),
      status: rawStatus === 'success' || rawStatus === 'error' ? rawStatus : 'info',
      category: getMessageField(item?.category, textPayload.category),
      status_label: getMessageField(item?.status_label, textPayload.status_label),
      primary_text: getMessageField(item?.primary_text, textPayload.primary_text, textIsPayload ? '' : text),
      meta_text: getMessageField(item?.meta_text, textPayload.meta_text),
      detail_text: getMessageField(item?.detail_text, textPayload.detail_text, textPayload.detail_content),
      source: 'notification',
    }
  })

const getMessageStatusLabel = (item: MessageCenterItem) => {
  if (item.status_label) return item.status_label
  if (item.status === 'success') return '成功'
  if (item.status === 'error') return '失败'
  return '通知'
}

const getMessageStatusColor = (item: MessageCenterItem) => {
  if (item.status === 'success') return '#34C759'
  if (item.status === 'error') return '#F53F3F'
  return '#4361ee'
}

const getMessageTagStyle = (item: MessageCenterItem): React.CSSProperties => {
  const isError = item.status === 'error'
  return {
    fontSize: 12,
    lineHeight: '18px',
    padding: '0 8px',
    borderRadius: 10,
    color: isError ? '#D9363E' : '#1677ff',
    background: isError ? '#fff1f0' : '#eef6ff',
    border: `1px solid ${isError ? '#ffccc7' : '#bae0ff'}`,
    whiteSpace: 'nowrap',
  }
}

const extractMessageDurationText = (value: string) => {
  const text = String(value || '').trim()
  if (!text) return ''
  const durationKeywords = ['总耗时', '已用时间']
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  for (const line of lines) {
      const durationKeyword = durationKeywords.find((keyword) => line.includes(keyword))
      if (durationKeyword) return line.slice(line.indexOf(durationKeyword)).trim()
  }
  const match = text.match(/(?:总耗时|已用时间)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:秒|s|ms|毫秒|分钟|分)?/i)
  return match?.[0]?.trim() || ''
}

const inferMessageCategory = (item: MessageCenterItem) => {
  if (item.category) return item.category
  const text = `${item.title} ${item.content} ${item.primary_text || ''}`.trim()
  if (text.includes('烧录/安装任务') || text.includes('烧录安装')) return '烧录安装'
  if (text.includes('异常注入')) return '异常注入'
  if (text.includes('通信协议')) return '通信协议'
  if (text.includes('用户') || text.includes('角色')) return '用户与角色'
  return ''
}

let appBootstrapCache: {
  token: string
  data?: AppBootstrapData
  promise?: Promise<AppBootstrapData | null>
} | null = null

const clearAppBootstrapCache = () => {
  appBootstrapCache = null
}

const readPersistedBootstrapData = (token: string): AppBootstrapData | null => {
  try {
    const raw = localStorage.getItem(APP_BOOTSTRAP_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.token !== token || !parsed.data?.me) return null
    return parsed.data as AppBootstrapData
  } catch {
    return null
  }
}

const persistBootstrapData = (token: string, data: AppBootstrapData) => {
  try {
    localStorage.setItem(APP_BOOTSTRAP_CACHE_KEY, JSON.stringify({ token, data }))
  } catch {
    // ignore persistence failures
  }
}

const clearPersistedBootstrapData = () => {
  try {
    localStorage.removeItem(APP_BOOTSTRAP_CACHE_KEY)
  } catch {
    // ignore cleanup failures
  }
}

const loadAppBootstrapData = async (): Promise<AppBootstrapData | null> => {
  const token = localStorage.getItem('token')
  if (!token) {
    clearAppBootstrapCache()
    return null
  }

  if (appBootstrapCache?.token === token) {
    if (appBootstrapCache.data) return appBootstrapCache.data
    if (appBootstrapCache.promise) return appBootstrapCache.promise
  }

  const promise = Promise.all([
    authApi.getMe(),
    permissionApi.getMyPermissions().catch(() => ({ code: 1, data: [] })),
    permissionApi.getMenus().catch(() => ({ code: 1, data: [] })),
    messageApi.getList({ page: 1, page_size: 100 }).catch(() => ({ code: 1, data: [] })),
  ])
    .then(([meRes, permsRes, menusRes, msgRes]: any[]) => {
      if (meRes?.code !== 0 || !meRes?.data) {
        return null
      }

      return {
        me: meRes.data,
        permissions: permsRes?.code === 0 ? (permsRes.data || []) : [],
        menus: menusRes?.code === 0 ? (menusRes.data || []) : [],
        messages: msgRes?.code === 0 ? (msgRes.data || []) : [],
      }
    })
    .finally(() => {
      if (appBootstrapCache?.token === token) {
        delete appBootstrapCache.promise
      }
    })

  appBootstrapCache = { token, promise }

  const data = await promise
  if (!data) {
    if (appBootstrapCache?.token === token) {
      clearAppBootstrapCache()
    }
    return null
  }

  appBootstrapCache = { token, data }
  persistBootstrapData(token, data)
  return data
}

const isAuthFailureError = (error: any) => {
  const status = Number(error?.response?.status || 0)
  return status === 401
}

const SvgIcon = ({ children }: { children: React.ReactNode }) => (
  <svg className="app-menu-svg-icon" viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" strokeWidth="2.05" strokeLinecap="round" strokeLinejoin="round">
    {children}
  </svg>
)

const customIcons: Record<string, React.ReactNode> = {
  '/workbench': (
    <SvgIcon>
      <rect x="2" y="3" width="20" height="14" rx="2" />
      <path d="M8 21h8" />
      <path d="M12 17v4" />
      <path d="M6 13l4-4 4 2 4-5" />
    </SvgIcon>
  ),
  '/repository': (
    <SvgIcon>
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
      <line x1="12" y1="22.08" x2="12" y2="12" />
    </SvgIcon>
  ),
  '/burning': (
    <SvgIcon>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <rect x="10" y="10" width="4" height="4" />
      <path d="M9 2v4M12 2v4M15 2v4M9 18v4M12 18v4M15 18v4M2 9h4M2 12h4M2 15h4M18 9h4M18 12h4M18 15h4" />
    </SvgIcon>
  ),
  '/record': (
    <SvgIcon>
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
      <path d="M9 14h6" />
      <path d="M9 18h6" />
      <path d="M9 10h6" />
    </SvgIcon>
  ),
  '资产管理': (
    <SvgIcon>
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 12 12 17 22 12" />
      <polyline points="2 17 12 22 22 17" />
    </SvgIcon>
  ),
  '/product': (
    <SvgIcon>
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
      <line x1="12" y1="22.08" x2="12" y2="12" />
    </SvgIcon>
  ),
  '/burner': (
    <SvgIcon>
      <rect x="6" y="8" width="12" height="14" rx="2" />
      <path d="M8 8V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v4" />
      <path d="M10 5v1" />
      <path d="M14 5v1" />
    </SvgIcon>
  ),
  '/script': (
    <SvgIcon>
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
      <line x1="14" y1="4" x2="10" y2="20" />
    </SvgIcon>
  ),
  '/injection': (
    <SvgIcon>
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </SvgIcon>
  ),
  '/protocol': (
    <SvgIcon>
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </SvgIcon>
  ),
  '系统管理': (
    <SvgIcon>
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <circle cx="12" cy="12" r="4" />
    </SvgIcon>
  ),
  '/user': (
    <SvgIcon>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </SvgIcon>
  ),
  '/role': (
    <SvgIcon>
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </SvgIcon>
  ),
  '/log/login': (
    <SvgIcon>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <polyline points="9 15 11 17 15 12" />
    </SvgIcon>
  ),
  '/log/operation': (
    <SvgIcon>
      <rect x="2" y="3" width="20" height="4" rx="1" />
      <rect x="2" y="10" width="20" height="4" rx="1" />
      <rect x="2" y="17" width="20" height="4" rx="1" />
      <line x1="6" y1="5" x2="6.01" y2="5" />
      <line x1="6" y1="12" x2="6.01" y2="12" />
      <line x1="6" y1="19" x2="6.01" y2="19" />
    </SvgIcon>
  )
}

function getIcon(menu: any): React.ReactNode {
  // Use custom path-based icons if available
  if (menu.path && customIcons[menu.path]) return customIcons[menu.path]
  // Fallback for parent nodes (e.g. 资产管理)
  if (!menu.path && menu.name && customIcons[menu.name]) return customIcons[menu.name]
  
  // Default to something generic if not found
  return <span className="anticon" />
}

function getItem(
  label: string,
  key: string,
  icon?: React.ReactNode,
  children?: MenuItem[],
): MenuItem {
  return { key, icon, children, label } as MenuItem
}

function normalizeRoutePath(path?: string) {
  const nextPath = String(path || '').split('?')[0].replace(/^#/, '') || '/workbench'
  return nextPath === '/' ? '/workbench' : nextPath
}

function readCurrentRoutePath() {
  if (typeof window === 'undefined') return '/workbench'
  return normalizeRoutePath(window.location.hash || window.location.pathname || '/workbench')
}

const App: React.FC = () => {
  const navigate = useNavigate()
  const location = useLocation()
  const [activePath, setActivePath] = useState(readCurrentRoutePath)
  const [collapsed, setCollapsed] = useState(false)
  const [selectedKey, setSelectedKey] = useState('/workbench')
  const [openKeys, setOpenKeys] = useState<string[]>([])
  const { menus, hasPermission, setPermissions, setMenus, clearPermissions } = usePermission()
  const [userInfo, setUserInfo] = useState<any>({})
  const [isInitializing, setIsInitializing] = useState(true)
  
  // Profile Modal State
  const [isProfileOpen, setIsProfileOpen] = useState(false)
  const [profileTab, setProfileTab] = useState('info')
  const [infoForm] = Form.useForm()
  const [pwdForm] = Form.useForm()

  // Messages Drawer State
  const [isMessageOpen, setIsMessageOpen] = useState(false)
  const [messageRecords, setMessageRecords] = useState<MessageCenterItem[]>([])
  const [messageNotifications, setMessageNotifications] = useState<MessageCenterItem[]>([])
  const [messagePage, setMessagePage] = useState(1)
  const [messageHasMore, setMessageHasMore] = useState(false)
  const [messageLoading, setMessageLoading] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [expandedMessageIds, setExpandedMessageIds] = useState<Set<string>>(new Set())
  const userStr = localStorage.getItem('user')
  const username = userStr ? JSON.parse(userStr).username : '管理员'

  useEffect(() => {
    if (localStorage.getItem('token')) {
      window.electronAPI?.windowControls.setMode('main')
    }
  }, [])

  const fetchUserInfo = async () => {
    try {
      const res: any = await authApi.getMe()
      if (res.code === 0) {
        setUserInfo(res.data)
        const token = localStorage.getItem('token')
        if (token && appBootstrapCache?.token === token && appBootstrapCache.data) {
          appBootstrapCache = {
            token,
            data: {
              ...appBootstrapCache.data,
              me: res.data,
            },
          }
        }
      }
    } catch {
      // ignore
    }
  }

  const messages = useMemo(
    () => dedupeMessageCenterItems([...messageRecords, ...messageNotifications]),
    [messageNotifications, messageRecords],
  )

  useEffect(() => {
    setUnreadCount(messages.filter((item) => !item.is_read).length)
  }, [messages])

  const fetchMessages = async (reset = false) => {
    try {
      setMessageLoading(true)
      const targetPage = reset ? 1 : messagePage
      const readCache = readMessageCenterReadCache()
      const [messageRes, dashboardRes]: any[] = await Promise.all([
        messageApi.getList({ page: targetPage, page_size: MESSAGE_CENTER_PAGE_SIZE }).catch(() => ({ code: 1, data: [], total: 0 })),
        reset ? dashboardApi.getStats().catch(() => ({ code: 1, data: {} })) : Promise.resolve({ code: 0, data: {} }),
      ])
      if (messageRes.code === 0 || dashboardRes.code === 0) {
        const nextMessages = messageRes?.code === 0 ? normalizeServerMessages(messageRes.data || [], readCache) : []
        const nextNotifications =
          reset && dashboardRes?.code === 0
            ? mapDashboardNotificationsToMessages(dashboardRes?.data?.notifications || [], readCache)
            : messageNotifications
        if (reset) {
          setMessageNotifications(nextNotifications)
          setMessageRecords(nextMessages)
        } else {
          setMessageRecords((prev) => dedupeMessageCenterItems([...prev, ...nextMessages]))
        }
        const currentLoadedCount = (reset ? 0 : messageRecords.length) + nextMessages.length
        const total = Number(messageRes?.total || 0)
        setMessagePage(targetPage + 1)
        setMessageHasMore(currentLoadedCount < total)
        const token = localStorage.getItem('token')
        if (token && appBootstrapCache?.token === token && appBootstrapCache.data) {
          const mergedMessages = dedupeMessageCenterItems([
            ...(reset ? nextMessages : [...messageRecords, ...nextMessages]),
            ...(reset ? nextNotifications : messageNotifications),
          ])
          appBootstrapCache = {
            token,
            data: {
              ...appBootstrapCache.data,
              messages: mergedMessages,
            },
          }
        }
      }
    } catch {
      // ignore
    } finally {
      setMessageLoading(false)
    }
  }

  // Build menu items from stored menu data + permission filtering
  const menuItems: MenuItem[] = useMemo(() => {
    if (isInitializing) return []

    const systemMenuOrder: Record<string, number> = {
      '用户管理': 1,
      '角色管理': 2,
      '登录日志': 3,
      '操作日志': 4,
    }

    const buildMenuItems = (menuList: any[]): MenuItem[] => {
      const normalizedMenus = [...menuList].sort((a, b) => {
        if (a?.name && b?.name && (a.parent_id || b.parent_id || a.name in systemMenuOrder || b.name in systemMenuOrder)) {
          const aOrder = systemMenuOrder[a.name as string]
          const bOrder = systemMenuOrder[b.name as string]
          if (aOrder || bOrder) return (aOrder || Number.MAX_SAFE_INTEGER) - (bOrder || Number.MAX_SAFE_INTEGER)
        }
        return 0
      })

      return normalizedMenus
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
          if (menu.children && menu.children.length > 0 && children.length === 0) return null
          return getItem(
            menu.name,
            menu.path || menu.name, // fallback key for parent folder
            getIcon(menu),
            children.length > 0 ? children : undefined,
          )
        })
        .filter(Boolean) as MenuItem[]
    }

    // If we have backend menu data, use it
    if (menus && menus.length > 0) {
      return buildMenuItems(menus)
    }

    // Fallback to hardcoded menu matching prototype
    const baseMenus = [
      { name: '工作台', path: '/workbench', icon: 'DesktopOutlined', children: [] },
      { name: '制品仓库', path: '/repository', icon: 'DatabaseOutlined', children: [] },
      { name: '烧录安装管理', path: '/burning', icon: 'FireOutlined', children: [] },
      { name: '履历记录', path: '/record', icon: 'FileTextOutlined', children: [] },
      { name: '资产管理', path: '', icon: 'InboxOutlined', children: [
        { name: '产品管理', path: '/product', icon: 'CodeOutlined', children: [] },
        { name: '设备管理', path: '/burner', icon: 'FireOutlined', children: [] },
        { name: '脚本管理', path: '/script', icon: 'FileProtectOutlined', children: [] },
      ]},
      { name: '异常注入', path: '/injection', icon: 'BugOutlined', children: [] },
      { name: '通信协议验证', path: '/protocol', icon: 'WifiOutlined', children: [] },
      { name: '系统管理', path: '', icon: 'SettingOutlined', children: [
        { name: '用户管理', path: '/user', icon: 'TeamOutlined', children: [] },
        { name: '角色管理', path: '/role', icon: 'TeamOutlined', children: [] },
        { name: '登录日志', path: '/log/login', icon: 'BarChartOutlined', children: [] },
        { name: '操作日志', path: '/log/operation', icon: 'FileTextOutlined', children: [] },
      ]},
    ]
    return buildMenuItems(baseMenus)
  }, [menus, hasPermission, isInitializing])

  const menuAncestorMap = useMemo(() => {
    const map = new Map<string, string[]>()

    const walk = (items: MenuItem[] | undefined, ancestors: string[] = []) => {
      ;(items || []).forEach((item) => {
        if (!item) return
        const key = String(item.key)
        const children = ('children' in item ? (item.children as MenuItem[] | undefined) : undefined) || []
        if (key.startsWith('/')) {
          map.set(key, ancestors)
        }
        if (children.length > 0) {
          walk(children, [...ancestors, key])
        }
      })
    }

    walk(menuItems)
    return map
  }, [menuItems])

  useEffect(() => {
    let cancelled = false

    const initializeApp = async () => {
      const token = localStorage.getItem('token')
      if (!token) {
        navigate('/login', { replace: true })
        return
      }

      try {
        const bootstrap = await loadAppBootstrapData()
        if (cancelled) return

        if (!bootstrap?.me) {
          clearAppBootstrapCache()
          clearPermissions()
          localStorage.removeItem('token')
          localStorage.removeItem('user')
          localStorage.removeItem('username')
          clearPersistedBootstrapData()
          navigate('/login', { replace: true })
          return
        }

        setUserInfo(bootstrap.me)

        setPermissions(Array.isArray(bootstrap.permissions) ? bootstrap.permissions : [])
        setMenus(Array.isArray(bootstrap.menus) ? bootstrap.menus : [])
        setMessageRecords(normalizeServerMessages(bootstrap.messages || [], readMessageCenterReadCache()))
        setMessageNotifications([])

        setIsInitializing(false)
      } catch (error: any) {
        if (cancelled) return
        clearAppBootstrapCache()

        if (isAuthFailureError(error)) {
          clearPermissions()
          localStorage.removeItem('token')
          localStorage.removeItem('user')
          localStorage.removeItem('username')
          clearPersistedBootstrapData()
          navigate('/login', { replace: true })
          return
        }

        const token = localStorage.getItem('token') || ''
        const fallbackBootstrap = token ? readPersistedBootstrapData(token) : null
        if (fallbackBootstrap?.me) {
          setUserInfo(fallbackBootstrap.me)
          setPermissions(fallbackBootstrap.permissions || [])
          setMenus(fallbackBootstrap.menus || [])
          setMessageRecords(normalizeServerMessages(fallbackBootstrap.messages || [], readMessageCenterReadCache()))
          setMessageNotifications([])
          setIsInitializing(false)
          return
        }

        try {
          const localUser = JSON.parse(localStorage.getItem('user') || '{}')
          if (localUser && Object.keys(localUser).length > 0) {
            setUserInfo((prev: any) => ({
              ...prev,
              ...localUser,
            }))
          }
        } catch {
          // ignore invalid user cache
        }

        setIsInitializing(false)
      }
    }

    initializeApp()

    return () => {
      cancelled = true
    }
  }, [clearPermissions, navigate, setPermissions, setMenus])

  useEffect(() => {
    const token = localStorage.getItem('token')
    if (isInitializing || !token || location.pathname === '/login') {
      return
    }

    authApi.getMe().catch(() => {
      // Global interceptor and backend error dialog handle the user-facing prompt.
    })
  }, [isInitializing, location.pathname])

  useEffect(() => {
    if (!isProfileOpen) return
    infoForm.setFieldsValue({
      username: userInfo.username || '',
      email: userInfo.email || '',
    })
    if (profileTab === 'pwd') {
      pwdForm.resetFields()
    }
  }, [infoForm, isProfileOpen, profileTab, pwdForm, userInfo])

  useEffect(() => {
    setActivePath(normalizeRoutePath(location.pathname))
  }, [location.pathname])

  useEffect(() => {
    const syncActivePath = () => setActivePath(readCurrentRoutePath())
    window.addEventListener('hashchange', syncActivePath)
    return () => window.removeEventListener('hashchange', syncActivePath)
  }, [])

  // Handle default route and access control based on menuItems
  useEffect(() => {
    if (isInitializing || menuItems.length === 0) return

    const getAccessiblePaths = (items: any[]): string[] => {
      let paths: string[] = []
      for (const item of items) {
        if (!item) continue
        if (item.key && !item.children) {
          paths.push(item.key as string)
        }
        if (item.children) {
          paths = [...paths, ...getAccessiblePaths(item.children)]
        }
      }
      return paths
    }

    const validPaths = getAccessiblePaths(menuItems)
    let targetPath = normalizeRoutePath(activePath)

    if (validPaths.length > 0 && !validPaths.includes(targetPath)) {
      targetPath = validPaths[0]
      navigate(targetPath, { replace: true })
      setActivePath(targetPath)
    }

    setSelectedKey(targetPath)
    setOpenKeys(menuAncestorMap.get(targetPath) || [])
  }, [isInitializing, menuItems, menuAncestorMap, navigate, activePath])

  useEffect(() => {
    if (isInitializing) return
    const nextPath = normalizeRoutePath(activePath)
    setSelectedKey(nextPath)
    setOpenKeys(menuAncestorMap.get(nextPath) || [])
  }, [isInitializing, activePath, menuAncestorMap])

  useEffect(() => {
    if (!isMessageOpen) return
    fetchMessages(true)
  }, [isMessageOpen])

  const handleMenuClick: MenuProps['onClick'] = (e) => {
    const nextPath = String(e.key)
    if (!nextPath.startsWith('/')) return

    setSelectedKey(nextPath)
    setOpenKeys(menuAncestorMap.get(nextPath) || [])
    setActivePath(nextPath)

    navigate(nextPath)
  }

  const handleLogout = async () => {
    try {
      await authApi.logout()
    } catch {
      // Ignore logout API failures and still clear local session.
    }
    clearAppBootstrapCache()
    clearPermissions()
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem('username')
    clearPersistedBootstrapData()
    navigate('/login')
  }

  const handleUpdateProfile = async (values: any) => {
    try {
      const res: any = await authApi.updateMe(values)
      if (res.code === 0) {
        message.success('修改成功')
        fetchUserInfo()
        setIsProfileOpen(false)
        setProfileTab('info')
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
        void handleLogout()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '密码修改失败')
    }
  }

  const handleReadAll = async () => {
    if (!unreadCount) return
    const readIds = messages.map((item) => String(item.id))
    writeMessageCenterReadCache([...readMessageCenterReadCache(), ...readIds])
    setMessageNotifications((prev) => prev.map((item) => ({ ...item, is_read: true })))
    setMessageRecords((prev) => prev.map((item) => ({ ...item, is_read: true })))
    try {
      await messageApi.readAll()
    } catch {
      // ignore
    }
  }

  const markMessageAsRead = (itemId: string) => {
    const existsUnread = messages.some((item) => String(item.id) === itemId && !item.is_read)
    if (!existsUnread) return
    writeMessageCenterReadCache([...readMessageCenterReadCache(), itemId])
    setMessageNotifications((prev) => prev.map((item) => (String(item.id) === itemId ? { ...item, is_read: true } : item)))
    setMessageRecords((prev) => prev.map((item) => (String(item.id) === itemId ? { ...item, is_read: true } : item)))
  }

  const renderMessageCenterItem = (item: MessageCenterItem) => {
    const itemId = String(item.id)
    const isExpanded = expandedMessageIds.has(itemId)
    const primaryText = item.primary_text || item.content || item.title || '系统消息'
    const metaText = item.meta_text || ''
    const detailText = extractMessageDurationText(item.detail_text || '')
    const category = inferMessageCategory(item)
    const plainFullText = [primaryText, metaText, detailText].filter(Boolean).join('\n')
    const shouldCollapse = plainFullText.length > 70 || detailText.length > 0
    const visibleDetail = shouldCollapse && !isExpanded ? '' : detailText
    const timeText = formatDateTime(item.created_at)
    const shortTime = timeText ? timeText.slice(0, 16) || timeText : ''

    const toggleExpanded = () => {
      markMessageAsRead(itemId)
      setExpandedMessageIds((prev) => {
        const next = new Set(prev)
        if (next.has(itemId)) next.delete(itemId)
        else next.add(itemId)
        return next
      })
    }

    return (
      <List.Item
        style={{
          opacity: item.is_read ? 0.62 : 1,
          padding: '12px 0',
          borderBlockEnd: '1px solid #f0f0f0',
          cursor: 'pointer',
        }}
        onClick={() => markMessageAsRead(itemId)}
      >
        <div style={{ width: '100%', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
              <Badge color={getMessageStatusColor(item)} />
              <span style={{ fontSize: 13, fontWeight: 600, color: getMessageStatusColor(item), whiteSpace: 'nowrap' }}>
                {getMessageStatusLabel(item)}
              </span>
              {category ? <span style={getMessageTagStyle(item)}>{category}</span> : null}
            </div>
            <span style={{ fontSize: 12, color: '#9aa0aa', whiteSpace: 'nowrap' }}>{shortTime || timeText}</span>
          </div>
          {isExpanded ? (
            <div style={{ color: '#1f2329', fontSize: 14, fontWeight: 600, lineHeight: '22px', whiteSpace: 'pre-wrap' }}>
              {primaryText}
            </div>
          ) : (
            <EllipsisText value={primaryText} style={{ color: '#1f2329', fontSize: 14, fontWeight: 600, lineHeight: '22px' }} />
          )}
          {metaText ? (
            isExpanded ? (
              <div style={{ color: '#86909c', fontSize: 13, lineHeight: '22px', marginTop: 2, whiteSpace: 'pre-wrap' }}>
                {metaText}
              </div>
            ) : (
              <EllipsisText value={metaText} style={{ color: '#86909c', fontSize: 13, lineHeight: '22px', marginTop: 2 }} />
            )
          ) : null}
          {visibleDetail ? (
            <div style={{ color: '#4e5969', fontSize: 13, lineHeight: '20px', marginTop: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {visibleDetail}
            </div>
          ) : null}
          {shouldCollapse ? (
            <Button
              type="link"
              size="small"
              onClick={(event) => {
                event.stopPropagation()
                toggleExpanded()
              }}
              style={{ padding: 0, height: 22, marginTop: 2 }}
            >
              {isExpanded ? '收起' : '更多'}
            </Button>
          ) : null}
        </div>
      </List.Item>
    )
  }

  const currentPage = useMemo(() => {
    switch (activePath) {
      case '/workbench':
        return <Workbench onOpenMessage={() => setIsMessageOpen(true)} />
      case '/repository':
        return <Repository />
      case '/burning':
        return <Burning />
      case '/injection':
        return <Injection />
      case '/protocol':
        return <Protocol />
      case '/record':
        return <Record />
      case '/product':
        return <Product />
      case '/burner':
        return <Burner />
      case '/script':
        return <Script />
      case '/log/login':
        return <LoginLog />
      case '/log/operation':
        return <OperationLog />
      case '/user':
        return <User />
      case '/role':
        return <Role />
      default:
        return <Workbench onOpenMessage={() => setIsMessageOpen(true)} />
    }
  }, [activePath])

  const profileModalContent = (
    <div style={{ display: 'flex', height: 400 }}>
      {/* Left Side: Avatar */}
      <div style={{ width: 220, borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 40, paddingRight: 24 }}>
        <div style={{ marginBottom: 24 }}>
          <UserAvatar
            user={{ avatar_url: userInfo.avatar_url, display_name: userInfo.username, username }}
            fallbackName={userInfo.username || username || '管理'}
            size={100}
          />
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
          <Button size="middle" style={{ borderStyle: 'dashed' }}>更换头像</Button>
        </Upload>
        <div style={{ fontSize: 12, color: '#86909c', marginTop: 16, textAlign: 'center', lineHeight: '20px' }}>
          支持 JPG / PNG 格式，<br />大小不超过 2MB
        </div>
      </div>

      {/* Right Side: Tabs and Forms */}
      <div style={{ flex: 1, paddingLeft: 24, paddingTop: 12 }}>
        <Tabs activeKey={profileTab} onChange={setProfileTab} items={[
          {
            key: 'info',
            label: '基本信息',
            children: (
              <Form form={infoForm} layout="vertical" style={{ marginTop: 16 }}>
                <Form.Item label="用户账户" name="username">
                  <Input id="profile-username" name="username" autoComplete="username" disabled prefix={<UserOutlined style={{ color: '#c9cdd4' }} />} size="large" />
                </Form.Item>
                <Form.Item label="用户名" name="email">
                  <Input id="profile-email" name="email" autoComplete="email" placeholder="请输入用户名" prefix={<UserOutlined style={{ color: '#c9cdd4' }} />} size="large" />
                </Form.Item>
              </Form>
            )
          },
          {
            key: 'pwd',
            label: '修改密码',
            children: (
              <Form form={pwdForm} layout="vertical" style={{ marginTop: 16 }}>
                <Form.Item label="原密码" name="oldPassword" rules={[{ required: true, message: '请输入原密码' }]}>
                  <Input.Password id="profile-old-password" name="oldPassword" autoComplete="current-password" placeholder="请输入原密码" size="large" prefix={<LockOutlined style={{ color: '#c9cdd4' }} />} />
                </Form.Item>
                <Form.Item label="新密码" name="newPassword" rules={[{ required: true, message: '请输入新密码' }]}>
                  <Input.Password id="profile-new-password" name="newPassword" autoComplete="new-password" placeholder="请输入新密码" size="large" prefix={<LockOutlined style={{ color: '#c9cdd4' }} />} />
                </Form.Item>
                <Form.Item label="确认新密码" name="confirmPassword" rules={[{ required: true, message: '请确认新密码' }]}>
                  <Input.Password id="profile-confirm-password" name="confirmPassword" autoComplete="new-password" placeholder="请确认新密码" size="large" prefix={<LockOutlined style={{ color: '#c9cdd4' }} />} />
                </Form.Item>
              </Form>
            )
          }
        ]} />
      </div>
    </div>
  )

  const handleProfileOk = () => {
    if (profileTab === 'info') {
      infoForm.validateFields().then(handleUpdateProfile)
    } else {
      pwdForm.validateFields().then(handleUpdatePassword)
    }
  }

  if (isInitializing) {
    return (
      <div className="desktop-app-loading">
        <div className="desktop-app-loading__titlebar">
          <div className="app-header-brand" style={{ display: 'flex', alignItems: 'center', color: 'var(--pcids-brand-color)', fontWeight: 800, fontSize: 18 }}>
            <img src={SoftwareLogo} alt="软件图标" style={{ width: 24, height: 24, marginRight: 8, objectFit: 'contain' }} />
            <span>程控安装部署系统</span>
          </div>
          <DesktopWindowControls />
        </div>
        <div className="desktop-app-loading__content">
          <Spin size="large" />
        </div>
      </div>
    )
  }

  return (
    <Layout style={{ height: '100%', minWidth: 0 }}>
      <Header
        className="app-header"
        style={{
          padding: '0 0 0 24px',
          background: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid #e8e8e8',
          zIndex: 10
        }}
      >
        <div className="app-header-brand" style={{ display: 'flex', alignItems: 'center', color: 'var(--pcids-brand-color)', fontWeight: 800, fontSize: 18, width: 216 }}>
          <img src={SoftwareLogo} alt="软件图标" style={{ width: 24, height: 24, marginRight: 8, objectFit: 'contain' }} />
          <span>程控安装部署系统</span>
        </div>
        <div className="app-header-actions">
          <div className="app-header-tools">
            <Dropdown
              menu={{
                items: [
                  { key: 'profile', label: '个人信息' },
                  { key: 'logout', label: '退出登录', danger: true }
                ],
                onClick: ({ key }) => {
                  if (key === 'profile') setIsProfileOpen(true)
                  if (key === 'logout') void handleLogout()
                }
              }}
              placement="bottomRight"
              trigger={['click']}
            >
              <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}>
                <UserAvatar
                  user={{ avatar_url: userInfo.avatar_url, display_name: userInfo.username, username }}
                  fallbackName={userInfo.username || username || '管理'}
                  size={28}
                />
              </div>
            </Dropdown>
            <Badge dot={unreadCount > 0} offset={[-2, 2]}>
              <BellOutlined style={{ fontSize: 20, cursor: 'pointer', color: '#86909c' }} onClick={() => setIsMessageOpen(true)} />
            </Badge>
          </div>
          <DesktopWindowControls />
        </div>
      </Header>
      <Layout>
        <Sider
          className="app-sider"
          trigger={null}
          collapsible
          collapsed={collapsed}
          collapsedWidth={64}
          theme="light"
          width={240}
          style={{ 
            background: '#f7f8fb',
            position: 'relative',
            zIndex: 2,
            flex: '0 0 auto',
          }}
        >
          {/* 自定义侧边栏折叠按钮（悬浮在侧边栏右侧边缘中间位置） */}
          <div 
            onClick={() => setCollapsed(!collapsed)}
            style={{
              position: 'absolute',
              top: '50%',
              right: -14,
              transform: 'translateY(-50%)',
              width: 14,
              height: 48,
              background: '#ffffff',
              border: '1px solid #e8e8e8',
              borderLeft: 'none',
              borderRadius: '0 8px 8px 0',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              zIndex: 10,
              boxShadow: '2px 0 4px rgba(0,0,0,0.05)',
              color: '#666'
            }}
          >
            {collapsed ? <RightOutlined style={{ fontSize: 10 }} /> : <LeftOutlined style={{ fontSize: 10 }} />}
          </div>

          <div style={{ padding: '16px 0' }}>
            <ConfigProvider
              theme={{
                components: {
                  Menu: {
                    itemSelectedBg: '#4361ee',
                    itemSelectedColor: '#ffffff',
                    itemHoverBg: 'rgba(67, 97, 238, 0.1)',
                    itemColor: '#1d2129',
                    itemMarginInline: 0,
                    itemBorderRadius: 0,
                  },
                },
              }}
            >
              <Menu
                className="app-side-menu"
                theme="light"
                mode="inline"
                inlineCollapsed={collapsed}
                selectedKeys={[selectedKey]}
                openKeys={collapsed ? [] : openKeys}
                items={menuItems}
                onClick={handleMenuClick}
                onOpenChange={(keys) => setOpenKeys(keys as string[])}
                style={{ background: 'transparent', borderRight: 'none' }}
              />
            </ConfigProvider>
          </div>
          {!collapsed && (
            <div style={{ position: 'absolute', bottom: 24, left: 24, right: 24, color: 'rgba(0,0,0,0.45)', fontSize: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>v1.0.0</div>
              <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 14 }}>
                <QuestionCircleOutlined /> 帮助
              </div>
            </div>
          )}
        </Sider>
        <Layout style={{ minWidth: 0 }}>
          <Content
            style={{
              margin: 16,
              overflow: 'auto',
              minWidth: 0,
            }}
          >
            <div key={activePath} style={{ minWidth: 0, minHeight: '100%' }}>
              {currentPage}
            </div>
          </Content>
        </Layout>
      </Layout>
      <Modal
        title="个人信息"
        className="pcids-modal pcids-modal--form pcids-modal--body-zero"
        open={isProfileOpen}
        onCancel={() => {
          setIsProfileOpen(false)
          setProfileTab('info')
        }}
        onOk={handleProfileOk}
        destroyOnHidden
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
          <Button type="link" onClick={handleReadAll} disabled={!unreadCount}>
            全部已读
          </Button>
        }
      >
        <div
          style={{ height: 'calc(100vh - 120px)', overflowY: 'auto', paddingRight: 4 }}
          onScroll={(event) => {
            const target = event.currentTarget
            const reachedBottom = target.scrollTop + target.clientHeight >= target.scrollHeight - 48
            if (reachedBottom && messageHasMore && !messageLoading) {
              fetchMessages(false)
            }
          }}
        >
          <List
            itemLayout="vertical"
            dataSource={messages}
            locale={{ emptyText: messageLoading ? '加载中...' : '暂无消息' }}
            split={false}
            style={{ marginTop: 0 }}
            renderItem={renderMessageCenterItem}
          />
          <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0 4px' }}>
            {messageHasMore ? (
              <Button type="link" loading={messageLoading} onClick={() => fetchMessages(false)}>
                加载更多
              </Button>
            ) : messages.length ? (
              <span style={{ fontSize: 12, color: '#999' }}>没有更多了</span>
            ) : null}
          </div>
        </div>
      </Drawer>
    </Layout>
  )
}

export default App
