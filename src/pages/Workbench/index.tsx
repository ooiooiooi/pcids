import { Card, Row, Col, Typography, Badge, List, Segmented } from 'antd'
import {
  CaretUpOutlined,
  CaretDownOutlined
} from '@ant-design/icons'
import { useEffect, useRef, useState } from 'react'
import { dashboardApi } from '../../services/api'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar
} from 'recharts'
import { useNavigate } from 'react-router-dom'
import { ActionLinkButton } from '../../components/ActionButton'
import { getDateTimeSortValue } from '../../utils/dateTime'
import DashboardIllustration from '../../assets/images/workbench-hero-illustration.svg'
import InstallIcon from '../../assets/images/workbench-install-count.svg'
import SuccessRateIcon from '../../assets/images/workbench-success-rate.svg'
import BurnerStatusIcon from '../../assets/images/workbench-burner-status.svg'

const { Title, Text } = Typography

export interface WorkbenchProps {
  onOpenMessage?: () => void;
}

type ShortcutItem = {
  id: number
  name: string
  path: string
  permissionCode?: string
  iconKey?: string
}

const parseNotificationPayload = (value: any): Record<string, any> => {
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

const getNotificationField = (...values: any[]) => {
  for (const value of values) {
    const text = String(value || '').trim()
    if (text) return text
  }
  return ''
}

const extractNotificationDurationText = (value: any) => {
  const text = String(value || '').trim()
  if (!text) return ''
  const match = text.match(/(?:总耗时|已用时间)\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:秒|s|ms|毫秒|分钟|分)?/i)
  return match?.[0]?.trim() || ''
}

const normalizeNotification = (item: any) => {
  const payload = parseNotificationPayload(item?.text)
  const text = String(item?.text || '').trim()
  const textIsPayload = Object.keys(payload).length > 0
  const detailText = getNotificationField(item?.detail_text, payload.detail_text, payload.detail_content)
  return {
    ...item,
    status: getNotificationField(item?.status, payload.status) || 'info',
    primaryText: getNotificationField(item?.primary_text, payload.primary_text, textIsPayload ? '' : text, '系统动态更新'),
    metaText: getNotificationField(item?.meta_text, payload.meta_text),
    durationText: extractNotificationDurationText(detailText),
  }
}

const Workbench: React.FC<WorkbenchProps> = ({ onOpenMessage }) => {
  const navigate = useNavigate()
  const latestFetchIdRef = useRef(0)
  const [welcome, setWelcome] = useState({
    displayName: '用户',
    dateLabel: '',
  })
  const [stats, setStats] = useState({
    todayTasks: 0,
    taskGrowth: 0,
    successRate: 0,
    rateGrowth: 0,
    burnerIdle: 0,
    burnerInUse: 0,
    burnerOffline: 0,
  })
  const [trendData, setTrendData] = useState<any[]>([])
  const [targetData, setTargetData] = useState<any[]>([])
  const [notifications, setNotifications] = useState<any[]>([])
  const [shortcuts, setShortcuts] = useState<ShortcutItem[]>([])
  const [trendMonths, setTrendMonths] = useState<6 | 12>(6)
  const [targetMonths, setTargetMonths] = useState<6 | 12>(6)
  const middlePanelMinHeight = 224

  useEffect(() => {
    fetchData()
    const refreshTimer = window.setInterval(fetchData, 15_000)
    window.addEventListener('focus', fetchData)
    return () => {
      window.clearInterval(refreshTimer)
      window.removeEventListener('focus', fetchData)
    }
  }, [trendMonths, targetMonths])

  const fetchData = async () => {
    const fetchId = ++latestFetchIdRef.current
    try {
      const res: any = await dashboardApi.getStats({
        trend_months: trendMonths,
        target_months: targetMonths,
      })
      if (fetchId !== latestFetchIdRef.current) {
        return
      }
      if (res?.code === 0 && res.data) {
        const {
          welcome: newWelcome,
          shortcuts: newShortcuts,
          stats: newStats,
          trendData: newTrendData,
          targetData: newTargetData,
          notifications: newNotifications,
        } = res.data

        setWelcome({
          displayName: newWelcome?.displayName || '用户',
          dateLabel: newWelcome?.dateLabel || '',
        })
        setShortcuts(Array.isArray(newShortcuts) ? newShortcuts : [])
        setStats({
          todayTasks: newStats.todayTasks || 0,
          taskGrowth: newStats.taskGrowth || 0,
          successRate: newStats.successRate || 0,
          rateGrowth: newStats.rateGrowth || 0,
          burnerIdle: newStats.burnerIdle || 0,
          burnerInUse: newStats.burnerInUse || 0,
          burnerOffline: newStats.burnerOffline || 0,
        })
        setTrendData(Array.isArray(newTrendData) ? newTrendData : [])
        setTargetData(Array.isArray(newTargetData) ? newTargetData : [])
        const sortedNotifications = Array.isArray(newNotifications)
          ? [...newNotifications].map(normalizeNotification).sort((left, right) => {
              const leftTime = getDateTimeSortValue(left?.time)
              const rightTime = getDateTimeSortValue(right?.time)
              return rightTime - leftTime
            })
          : []
        setNotifications(sortedNotifications)
      }
    } catch {
      // ignore
    }
  }

  const shortcutCardStyle = {
    border: '1px solid #d9dde7',
    borderRadius: 10,
    height: 72,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    padding: '0 12px',
    cursor: 'pointer',
    transition: 'all 0.25s ease',
    background: 'linear-gradient(180deg, #ffffff 0%, #fbfcff 100%)',
    boxShadow: '0 6px 14px rgba(15, 23, 42, 0.04)',
  }
  const shortcutLabelStyle = {
    fontSize: 16,
    fontWeight: 600,
    color: '#1d2129',
    lineHeight: '22px',
  }

  const renderShortcutIcon = (iconKey?: string) => {
    switch (iconKey) {
      case 'repository':
        return (
          <svg viewBox="0 0 100 100" width="32" height="32" fill="none" stroke="#4361ee" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M50 15 L15 35 L15 65 L50 85 L85 65 L85 35 Z" />
            <path d="M50 85 L50 50" />
            <path d="M50 50 L15 35" />
            <path d="M50 50 L85 35" />
          </svg>
        )
      case 'burning':
        return (
          <svg viewBox="0 0 100 100" width="32" height="32" fill="#4361ee">
            <path fillRule="evenodd" clipRule="evenodd" d="M30 30C26.6863 30 24 32.6863 24 36V64C24 67.3137 26.6863 70 30 70H70C73.3137 70 76 67.3137 76 64V36C76 32.6863 73.3137 30 70 30H30ZM42 42C40.8954 42 40 42.8954 40 44V56C40 57.1046 40.8954 58 42 58H58C59.1046 58 60 57.1046 60 56V44C60 42.8954 59.1046 42 58 42H42Z" />
            <rect x="12" y="36" width="8" height="6" rx="2" />
            <rect x="12" y="47" width="8" height="6" rx="2" />
            <rect x="12" y="58" width="8" height="6" rx="2" />
            <rect x="80" y="36" width="8" height="6" rx="2" />
            <rect x="80" y="47" width="8" height="6" rx="2" />
            <rect x="80" y="58" width="8" height="6" rx="2" />
            <rect x="36" y="12" width="6" height="8" rx="2" />
            <rect x="47" y="12" width="6" height="8" rx="2" />
            <rect x="58" y="12" width="6" height="8" rx="2" />
            <rect x="36" y="80" width="6" height="8" rx="2" />
            <rect x="47" y="80" width="6" height="8" rx="2" />
            <rect x="58" y="80" width="6" height="8" rx="2" />
          </svg>
        )
      case 'protocol':
        return (
          <svg viewBox="0 0 100 100" width="32" height="32" fill="none" stroke="#4361ee" strokeWidth="6">
            <circle cx="50" cy="50" r="40" />
            <ellipse cx="50" cy="50" rx="15" ry="40" />
            <line x1="10" y1="50" x2="90" y2="50" />
            <line x1="20" y1="25" x2="80" y2="25" />
            <line x1="20" y1="75" x2="80" y2="75" />
          </svg>
        )
      default:
        return null
    }
  }

  return (
    <div>
      <div className="client-page-title" style={{ marginBottom: 24 }}>
        <Title level={4}>工作台</Title>
        <p className="client-page-subtitle">汇总今日任务、成功率、设备状态与快捷入口</p>
      </div>

      {/* Top Stats Row */}
      <Row gutter={[16, 16]}>
        {/* Greeting Card */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#F8F9FE', position: 'relative', overflow: 'hidden' } }} variant="borderless">
            <div style={{ zIndex: 1 }}>
              <Title level={5} style={{ margin: '0 0 12px 0' }}>Hi，{welcome.displayName}~</Title>
              <div>
                <span style={{ background: '#4361ee', color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                  {welcome.dateLabel || '--'}
                </span>
              </div>
            </div>
            <img src={DashboardIllustration} alt="Welcome" style={{ position: 'absolute', right: -20, bottom: -20, height: 140, zIndex: 0 }} />
          </Card>
        </Col>

        {/* Today's Burn/Install Count */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Text type="secondary" style={{ fontSize: 12 }}>今日烧录/安装量</Text>
            <div style={{ fontSize: 32, fontWeight: 'bold', margin: '4px 0', color: '#1d2129' }}>
              {stats.todayTasks}
            </div>
            <div style={{ fontSize: 12, color: stats.taskGrowth >= 0 ? '#F53F3F' : '#3DD07B', display: 'flex', alignItems: 'center' }}>
              {stats.taskGrowth >= 0 ? <CaretUpOutlined style={{ marginRight: 4 }} /> : <CaretDownOutlined style={{ marginRight: 4 }} />}
              {Math.abs(stats.taskGrowth)}% 环比
            </div>
            <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <img src={InstallIcon} alt="Install Count" style={{ width: 48, height: 48 }} />
            </div>
          </Card>
        </Col>

        {/* Success Rate */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Text type="secondary" style={{ fontSize: 12 }}>今日成功率</Text>
            <div style={{ fontSize: 32, fontWeight: 'bold', margin: '4px 0', color: '#1d2129' }}>
              {stats.successRate}%
            </div>
            <div style={{ fontSize: 12, color: stats.rateGrowth >= 0 ? '#F53F3F' : '#3DD07B', display: 'flex', alignItems: 'center' }}>
              {stats.rateGrowth >= 0 ? <CaretUpOutlined style={{ marginRight: 4 }} /> : <CaretDownOutlined style={{ marginRight: 4 }} />}
              {Math.abs(stats.rateGrowth)}% 环比
            </div>
            <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <img src={SuccessRateIcon} alt="Success Rate" style={{ width: 48, height: 48 }} />
            </div>
          </Card>
        </Col>

        {/* Burner Status */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Text type="secondary" style={{ fontSize: 12 }}>烧录器状态</Text>
            <div style={{ display: 'flex', alignItems: 'center', marginTop: 16, gap: 16 }}>
              <div style={{ display: 'flex', alignItems: 'baseline' }}>
                <Badge color="green" />
                <span style={{ fontSize: 24, fontWeight: 'bold', marginLeft: 4 }}>{stats.burnerIdle}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline' }}>
                <Badge color="yellow" />
                <span style={{ fontSize: 24, fontWeight: 'bold', marginLeft: 4 }}>{stats.burnerInUse}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline' }}>
                <Badge color="red" />
                <span style={{ fontSize: 24, fontWeight: 'bold', marginLeft: 4 }}>{stats.burnerOffline}</span>
              </div>
            </div>
            <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <img src={BurnerStatusIcon} alt="Burner Status" style={{ width: 48, height: 48 }} />
            </div>
          </Card>
        </Col>
      </Row>

      {/* Middle Row: Shortcuts & Notifications */}
      <Row gutter={[16, 16]} style={{ marginTop: 16, display: 'flex', alignItems: 'stretch' }}>
        <Col xs={24} lg={12} style={{ display: 'flex' }}>
          <Card
            title="快捷方式"
            variant="borderless"
            styles={{ body: { padding: '14px 18px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' } }}
            style={{ width: '100%', display: 'flex', flexDirection: 'column', minHeight: middlePanelMinHeight }}
          >
            <Row gutter={[12, 12]} style={{ width: '100%', margin: 0, alignContent: 'center' }}>
              {shortcuts.map((item) => (
                <Col span={8} key={item.id} style={{ display: 'flex' }}>
                  <div 
                    onClick={() => navigate(item.path)}
                    style={{ ...shortcutCardStyle, width: '100%' }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = '#4361ee'
                      e.currentTarget.style.transform = 'translateY(-2px)'
                      e.currentTarget.style.boxShadow = '0 12px 24px rgba(67, 97, 238, 0.12)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = '#d9dde7'
                      e.currentTarget.style.transform = 'translateY(0)'
                      e.currentTarget.style.boxShadow = '0 6px 14px rgba(15, 23, 42, 0.04)'
                    }}
                  >
                    {renderShortcutIcon(item.iconKey)}
                    <div style={shortcutLabelStyle}>{item.name}</div>
                  </div>
                </Col>
              ))}
            </Row>
          </Card>
        </Col>
        <Col xs={24} lg={12} style={{ display: 'flex' }}>
          <Card 
            title="动态通知" 
            extra={
              <ActionLinkButton onClick={(e) => { e.preventDefault(); onOpenMessage?.(); }}>
                更多&gt;&gt;
              </ActionLinkButton>
            } 
            variant="borderless" 
            styles={{ body: { padding: '8px 18px', flex: 1 } }}
            style={{ width: '100%', display: 'flex', flexDirection: 'column', minHeight: middlePanelMinHeight }}
          >
            <List
              dataSource={notifications.slice(0, 3)}
              renderItem={(item) => (
                <List.Item style={{ padding: '10px 0', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'flex-start' }}>
                  <Badge color={item.status === 'success' ? '#3DD07B' : item.status === 'error' ? '#F53F3F' : '#4361ee'} style={{ marginTop: 6, marginRight: 8 }} />
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, width: '100%', minWidth: 0 }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <Typography.Paragraph
                        style={{ margin: 0, fontSize: 13, color: '#4e5969', lineHeight: '20px' }}
                        ellipsis={{ rows: 2, tooltip: item.primaryText }}
                      >
                        {item.primaryText || '系统动态更新'}
                      </Typography.Paragraph>
                    </div>
                    <Text style={{ fontSize: 12, color: '#86909c', whiteSpace: 'nowrap', lineHeight: '20px' }}>{item.time || '--'}</Text>
                  </div>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      {/* Bottom Row: Charts */}
      <Card title="安装成功率趋势" variant="borderless" style={{ marginTop: 16 }} extra={<Segmented size="small" value={trendMonths} options={[{ label: '近半年', value: 6 }, { label: '近一年', value: 12 }]} onChange={(value) => setTrendMonths(value as 6 | 12)} />}>
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={trendData} margin={{ top: 20, right: 30, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="colorRate" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#4361ee" stopOpacity={0.3}/>
                <stop offset="95%" stopColor="#4361ee" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="month" axisLine={false} tickLine={false} />
            <YAxis axisLine={false} tickLine={false} domain={[0, 100]} ticks={[0, 20, 40, 60, 80, 100]} />
            <Tooltip />
            <Area type="monotone" dataKey="rate" stroke="#4361ee" strokeWidth={3} fillOpacity={1} fill="url(#colorRate)" activeDot={{ r: 6 }} />
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      <Card title="目标安装数量统计TOP5" variant="borderless" style={{ marginTop: 16, marginBottom: 24 }} extra={<Segmented size="small" value={targetMonths} options={[{ label: '近半年', value: 6 }, { label: '近一年', value: 12 }]} onChange={(value) => setTargetMonths(value as 6 | 12)} />}>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={targetData} layout="vertical" margin={{ top: 20, right: 30, left: 20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" axisLine={false} tickLine={false} />
            <YAxis dataKey="name" type="category" axisLine={false} tickLine={false} width={100} />
            <Tooltip cursor={{ fill: 'transparent' }} />
            <Bar dataKey="value" fill="#4361ee" barSize={20} radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

export default Workbench
