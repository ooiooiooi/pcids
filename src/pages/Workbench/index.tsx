import { Alert, Badge, Button, Card, Col, Empty, List, Row, Segmented, Skeleton, Space, Typography } from 'antd'
import {
  CaretUpOutlined,
  CaretDownOutlined
} from '@ant-design/icons'
import { useEffect, useReducer, useRef, useState } from 'react'
import apiRequest, { dashboardApi } from '../../services/api'
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
import DashboardIllustration from '../../assets/images/workbench-hero-illustration.svg'
import InstallIcon from '../../assets/images/workbench-install-count.svg'
import SuccessRateIcon from '../../assets/images/workbench-success-rate.svg'
import BurnerStatusIcon from '../../assets/images/workbench-burner-status.svg'
import {
  DASHBOARD_TARGET_TITLE,
  DASHBOARD_TREND_TITLE,
  buildDashboardRefreshRequest,
  createDashboardRefreshController,
  dashboardLoadReducer,
  formatDashboardRefreshTime,
  getDashboardGrowthPresentation,
  getDashboardTaskGrowthPresentation,
  initialDashboardLoadState,
  normalizeDashboardData,
  type DashboardData,
  type DashboardRefreshParams,
  type DashboardRefreshRequest,
} from './dashboardState'

const { Title, Text } = Typography

const DashboardTrendTooltip = ({ active, payload, label }: any) => {
  if (!active || !Array.isArray(payload) || !payload.length) return null
  const point = payload[0]?.payload || {}
  const rateText = point.rateAvailable && Number.isFinite(Number(point.rate))
    ? `${Number(point.rate)}%`
    : '暂无数据'
  return (
    <div style={{ minWidth: 150, padding: '10px 12px', border: '1px solid #e5e6eb', borderRadius: 6, background: '#fff', boxShadow: '0 6px 18px rgba(0, 0, 0, 0.1)' }}>
      <div style={{ marginBottom: 8, color: '#1d2129', fontWeight: 600 }}>{label}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto auto', gap: '5px 16px', color: '#4e5969', fontSize: 13 }}>
        <span>成功率</span><span style={{ color: '#4361ee', textAlign: 'right' }}>{rateText}</span>
        <span>烧录量</span><span style={{ color: '#1d2129', textAlign: 'right' }}>{Number(point.burnCount || 0)}</span>
        <span>安装量</span><span style={{ color: '#1d2129', textAlign: 'right' }}>{Number(point.installCount || 0)}</span>
      </div>
    </div>
  )
}

export interface WorkbenchProps {
  onOpenMessage?: () => void;
}

const getDashboardErrorMessage = (error: any) => {
  const detail = typeof error?.response?.data?.detail === 'string'
    ? error.response.data.detail.trim()
    : ''
  const responseMessage = typeof error?.response?.data?.message === 'string'
    ? error.response.data.message.trim()
    : ''
  const errorMessage = typeof error?.message === 'string' ? error.message.trim() : ''
  if (detail || responseMessage) return detail || responseMessage
  if (errorMessage && errorMessage !== 'Network Error') return errorMessage
  return '无法获取工作台数据，请检查服务连接后重试'
}

const redirectExpiredSession = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('username')
  if (!window.location.hash.startsWith('#/login')) {
    window.location.replace('#/login')
  }
}

const Workbench: React.FC<WorkbenchProps> = ({ onOpenMessage }) => {
  const navigate = useNavigate()
  const [loadState, dispatchLoadState] = useReducer(dashboardLoadReducer, initialDashboardLoadState)
  const [trendMonths, setTrendMonths] = useState<6 | 12>(6)
  const [targetMonths, setTargetMonths] = useState<6 | 12>(6)
  const paramsRef = useRef<DashboardRefreshParams>({ trendMonths: 6, targetMonths: 6 })
  const didObserveInitialParamsRef = useRef(false)
  const hasAttemptedRef = useRef(false)
  const refreshControllerRef = useRef<ReturnType<typeof createDashboardRefreshController<DashboardData>> | null>(null)
  const middlePanelMinHeight = 224

  const ensureRefreshController = () => {
    if (refreshControllerRef.current) return refreshControllerRef.current

    refreshControllerRef.current = createDashboardRefreshController<DashboardData>({
      execute: async (refreshRequest) => {
        const requestParams = {
          trend_months: refreshRequest.params.trendMonths,
          target_months: refreshRequest.params.targetMonths,
        }

        try {
          const res: any = refreshRequest.silent
            ? await apiRequest.get('/dashboard/stats', {
                params: requestParams,
                skipAutoErrorMessage: true,
                suppressBackendServiceError: true,
              } as any)
            : await dashboardApi.getStats(requestParams)

          if (Number(res?.code) !== 0) {
            throw new Error(String(res?.message || '工作台数据加载失败'))
          }
          const normalized = normalizeDashboardData(res?.data)
          if (!normalized) {
            throw new Error('工作台数据格式不正确')
          }
          return normalized
        } catch (error: any) {
          // Silent background polling bypasses the shared interceptor's
          // automatic message path, so retain the critical 401 behavior here.
          if (refreshRequest.silent && Number(error?.response?.status) === 401) {
            redirectExpiredSession()
          }
          throw error
        }
      },
      onStart: (request) => {
        hasAttemptedRef.current = true
        dispatchLoadState({ type: 'start', silent: request.silent })
      },
      onSuccess: (data) => {
        dispatchLoadState({ type: 'success', data, receivedAt: Date.now() })
      },
      onError: (error) => {
        dispatchLoadState({ type: 'failure', message: getDashboardErrorMessage(error) })
      },
    })
    return refreshControllerRef.current
  }

  const triggerRefresh = (
    reason: DashboardRefreshRequest['reason'],
    forceSilent?: boolean,
  ) => {
    const silent = forceSilent ?? hasAttemptedRef.current
    return ensureRefreshController().trigger(
      buildDashboardRefreshRequest(paramsRef.current, reason, silent),
    )
  }

  useEffect(() => {
    triggerRefresh('initial', false)

    const refreshTimer = window.setInterval(() => {
      triggerRefresh('interval', true)
    }, 15_000)
    const handleFocus = () => {
      triggerRefresh('focus', true)
    }
    window.addEventListener('focus', handleFocus)
    return () => {
      window.clearInterval(refreshTimer)
      window.removeEventListener('focus', handleFocus)
      refreshControllerRef.current?.dispose()
      // React StrictMode intentionally runs setup -> cleanup -> setup in
      // development. Clearing the disposed instance lets the second setup
      // create a fresh active controller without allowing the old request to
      // publish after its cleanup.
      refreshControllerRef.current = null
      didObserveInitialParamsRef.current = false
    }
  }, [])

  useEffect(() => {
    const nextParams: DashboardRefreshParams = { trendMonths, targetMonths }
    paramsRef.current = nextParams
    if (!didObserveInitialParamsRef.current) {
      didObserveInitialParamsRef.current = true
      return
    }
    triggerRefresh('parameters', true)
  }, [trendMonths, targetMonths])

  const dashboardData = loadState.data
  const welcome = dashboardData?.welcome || { displayName: '', dateLabel: '' }
  const stats = dashboardData?.stats || {
    todayTasks: 0,
    yesterdayTasks: null,
    taskGrowth: null,
    successRate: 0,
    successRateAvailable: false,
    rateGrowth: null,
    burnerIdle: 0,
    burnerInUse: 0,
    burnerOffline: 0,
  }
  const trendData = dashboardData?.trendData || []
  const targetData = dashboardData?.targetData || []
  const notifications = dashboardData?.notifications || []
  const shortcuts = dashboardData?.shortcuts || []
  const taskGrowthPresentation = getDashboardTaskGrowthPresentation(
    stats?.taskGrowth,
    stats?.todayTasks ?? 0,
    stats?.yesterdayTasks ?? null,
  )
  const rateGrowthPresentation = getDashboardGrowthPresentation(stats?.rateGrowth, 'percentage-point')
  const isInitialLoading = loadState.phase === 'idle' || loadState.phase === 'loading'
  const showInitialLoading = !dashboardData && isInitialLoading
  const isRefreshing = loadState.phase === 'refreshing'
  const isStale = loadState.phase === 'stale'
  const lastUpdatedText = formatDashboardRefreshTime(loadState.lastUpdatedAt)

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
      <div
        className="client-page-title"
        style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start' }}
      >
        <div>
          <Title level={4}>工作台</Title>
          <p className="client-page-subtitle">汇总今日任务、成功率、设备状态与快捷入口</p>
        </div>
        <Space size={10} wrap style={{ justifyContent: 'flex-end' }}>
          {isRefreshing ? (
            <Badge status="processing" text="正在刷新" data-dashboard-state="refreshing" />
          ) : null}
          {isStale ? (
            <Badge status="warning" text="数据可能已过期" data-dashboard-state="stale" />
          ) : null}
          {lastUpdatedText ? (
            <Text type="secondary" style={{ fontSize: 12 }} data-dashboard-state="last-updated">
              更新于 {lastUpdatedText}
            </Text>
          ) : null}
        </Space>
      </div>

      {loadState.errorMessage ? (
        <Alert
          showIcon
          type={dashboardData ? 'warning' : 'error'}
          message={dashboardData ? '工作台数据刷新失败，当前展示上次成功数据' : '工作台数据加载失败'}
          description={loadState.errorMessage}
          action={(
            <Button
              size="small"
              onClick={() => triggerRefresh('manual', false)}
              loading={isInitialLoading || isRefreshing}
            >
              重新加载
            </Button>
          )}
          style={{ marginBottom: 16 }}
          data-dashboard-state={dashboardData ? 'stale-error' : 'error'}
        />
      ) : null}

      {/* Top Stats Row */}
      <Row gutter={[16, 16]} data-dashboard-state={showInitialLoading ? 'loading' : undefined}>
        {/* Greeting Card */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#F8F9FE', position: 'relative', overflow: 'hidden' } }} variant="borderless">
            <Skeleton loading={showInitialLoading} active title={false} paragraph={{ rows: 2 }}>
              <div style={{ zIndex: 1 }}>
                <Title level={5} style={{ margin: '0 0 12px 0' }}>Hi，{welcome.displayName || '用户'}~</Title>
                <div>
                  <span style={{ background: '#4361ee', color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                    {welcome.dateLabel || '--'}
                  </span>
                </div>
              </div>
              <img src={DashboardIllustration} alt="Welcome" style={{ position: 'absolute', right: -20, bottom: -20, height: 140, zIndex: 0 }} />
            </Skeleton>
          </Card>
        </Col>

        {/* Today's Burn/Install Count */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Skeleton loading={showInitialLoading} active title={false} paragraph={{ rows: 2 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>今日烧录/安装量</Text>
              <div style={{ fontSize: 32, fontWeight: 'bold', margin: '4px 0', color: '#1d2129' }}>
                {dashboardData ? stats.todayTasks : '--'}
              </div>
              <div style={{ fontSize: 12, color: taskGrowthPresentation.color, display: 'flex', alignItems: 'center' }}>
                {taskGrowthPresentation.direction === 'up' ? <CaretUpOutlined style={{ marginRight: 4 }} /> : null}
                {taskGrowthPresentation.direction === 'down' ? <CaretDownOutlined style={{ marginRight: 4 }} /> : null}
                {dashboardData ? taskGrowthPresentation.text : '暂无数据'}
              </div>
              <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <img src={InstallIcon} alt="Install Count" style={{ width: 48, height: 48 }} />
              </div>
            </Skeleton>
          </Card>
        </Col>

        {/* Success Rate */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Skeleton loading={showInitialLoading} active title={false} paragraph={{ rows: 2 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>今日成功率</Text>
              <div style={{ fontSize: 32, fontWeight: 'bold', margin: '4px 0', color: '#1d2129' }}>
                {dashboardData && stats.successRateAvailable ? `${stats.successRate}%` : '--'}
              </div>
              <div style={{ fontSize: 12, color: rateGrowthPresentation.color, display: 'flex', alignItems: 'center' }}>
                {rateGrowthPresentation.direction === 'up' ? <CaretUpOutlined style={{ marginRight: 4 }} /> : null}
                {rateGrowthPresentation.direction === 'down' ? <CaretDownOutlined style={{ marginRight: 4 }} /> : null}
                {dashboardData ? rateGrowthPresentation.text : '暂无数据'}
              </div>
              <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <img src={SuccessRateIcon} alt="Success Rate" style={{ width: 48, height: 48 }} />
              </div>
            </Skeleton>
          </Card>
        </Col>

        {/* Burner Status */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Skeleton loading={showInitialLoading} active title={false} paragraph={{ rows: 2 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>烧录器状态</Text>
              <div style={{ display: 'flex', alignItems: 'center', marginTop: 16, gap: 16 }}>
                <div style={{ display: 'flex', alignItems: 'baseline' }}>
                  <Badge color="green" />
                  <span style={{ fontSize: 24, fontWeight: 'bold', marginLeft: 4 }}>{dashboardData ? stats.burnerIdle : '--'}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline' }}>
                  <Badge color="yellow" />
                  <span style={{ fontSize: 24, fontWeight: 'bold', marginLeft: 4 }}>{dashboardData ? stats.burnerInUse : '--'}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline' }}>
                  <Badge color="red" />
                  <span style={{ fontSize: 24, fontWeight: 'bold', marginLeft: 4 }}>{dashboardData ? stats.burnerOffline : '--'}</span>
                </div>
              </div>
              <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <img src={BurnerStatusIcon} alt="Burner Status" style={{ width: 48, height: 48 }} />
              </div>
            </Skeleton>
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
            {showInitialLoading ? (
              <Skeleton active title={false} paragraph={{ rows: 3 }} />
            ) : shortcuts.length > 0 ? (
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
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无快捷方式" />
            )}
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
            {showInitialLoading ? (
              <Skeleton active title={false} paragraph={{ rows: 4 }} />
            ) : (
              <List
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无动态通知" /> }}
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
            )}
          </Card>
        </Col>
      </Row>

      {/* Bottom Row: Charts */}
      <Card title={DASHBOARD_TREND_TITLE} variant="borderless" style={{ marginTop: 16 }} extra={<Segmented size="small" value={trendMonths} options={[{ label: '近半年', value: 6 }, { label: '近一年', value: 12 }]} onChange={(value) => setTrendMonths(value as 6 | 12)} />}>
        {showInitialLoading ? (
          <div style={{ minHeight: 300, paddingTop: 24 }}><Skeleton active title={false} paragraph={{ rows: 7 }} /></div>
        ) : trendData.length > 0 ? (
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
              <Tooltip content={<DashboardTrendTooltip />} />
              <Area type="monotone" dataKey="rate" stroke="#4361ee" strokeWidth={3} fillOpacity={1} fill="url(#colorRate)" activeDot={{ r: 6 }} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ minHeight: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势数据" />
          </div>
        )}
      </Card>

      <Card title={DASHBOARD_TARGET_TITLE} variant="borderless" style={{ marginTop: 16, marginBottom: 24 }} extra={<Segmented size="small" value={targetMonths} options={[{ label: '近半年', value: 6 }, { label: '近一年', value: 12 }]} onChange={(value) => setTargetMonths(value as 6 | 12)} />}>
        {showInitialLoading ? (
          <div style={{ minHeight: 300, paddingTop: 24 }}><Skeleton active title={false} paragraph={{ rows: 7 }} /></div>
        ) : targetData.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={targetData} layout="vertical" margin={{ top: 20, right: 30, left: 20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" axisLine={false} tickLine={false} />
              <YAxis dataKey="name" type="category" axisLine={false} tickLine={false} width={100} />
              <Tooltip cursor={{ fill: 'transparent' }} />
              <Bar dataKey="value" fill="#4361ee" barSize={20} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ minHeight: 300, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无目标任务数据" />
          </div>
        )}
      </Card>
    </div>
  )
}

export default Workbench
