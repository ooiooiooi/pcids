import { Card, Row, Col, Typography, Badge, Space, List } from 'antd'
import {
  DownloadOutlined,
  CheckSquareOutlined,
  AppstoreOutlined,
  SettingOutlined,
  ToolOutlined,
  SafetyCertificateOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
} from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { dashboardApi } from '../../services/api'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar
} from 'recharts'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'

const { Title, Text } = Typography

const Workbench: React.FC = () => {
  const navigate = useNavigate()
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

  const userStr = localStorage.getItem('user')
  const username = userStr ? JSON.parse(userStr).username : '管理员'

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      const res: any = await dashboardApi.getStats()
      if (res?.code === 0 && res.data) {
        const { stats: newStats, trendData: newTrendData, targetData: newTargetData, notifications: newNotifications } = res.data
        
        setStats({
          todayTasks: newStats.todayTasks || 0,
          taskGrowth: newStats.taskGrowth || 0,
          successRate: newStats.successRate || 0,
          rateGrowth: newStats.rateGrowth || 0,
          burnerIdle: newStats.burnerIdle || 0,
          burnerInUse: newStats.burnerInUse || 0,
          burnerOffline: newStats.burnerOffline || 0,
        })
        
        if (newTrendData && newTrendData.length > 0) {
          setTrendData(newTrendData)
        }
        
        if (newTargetData && newTargetData.length > 0) {
          setTargetData(newTargetData)
        }
        
        if (newNotifications && newNotifications.length > 0) {
          setNotifications(newNotifications)
        }
      }
    } catch {
      // ignore
    }
  }

  const currentDate = dayjs().format('YYYY/MM/DD dddd')

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>工作台</Title>
      </div>

      {/* Top Stats Row */}
      <Row gutter={[16, 16]}>
        {/* Greeting Card */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, display: 'flex', flexDirection: 'column', justifyContent: 'center', background: '#F8F9FE' } }} variant="borderless">
            <Title level={5} style={{ margin: '0 0 12px 0' }}>Hi，{username}~</Title>
            <div>
              <span style={{ background: '#4045D6', color: '#fff', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                {currentDate}
              </span>
            </div>
            {/* Optionally place an illustration image here on the right side if available */}
          </Card>
        </Col>

        {/* Today's Burn/Install Count */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Text type="secondary" style={{ fontSize: 12 }}>今日烧录/安装量</Text>
            <div style={{ fontSize: 32, fontWeight: 'bold', margin: '4px 0', color: '#1d2129' }}>
              {stats.todayTasks}
            </div>
            <div style={{ fontSize: 12, color: '#3DD07B', display: 'flex', alignItems: 'center' }}>
              <ArrowUpOutlined style={{ marginRight: 4 }} />
              {stats.taskGrowth}% 环比
            </div>
            <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, background: '#E8F3FF', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <DownloadOutlined style={{ fontSize: 24, color: '#4045D6' }} />
            </div>
          </Card>
        </Col>

        {/* Success Rate */}
        <Col xs={24} sm={12} lg={6}>
          <Card styles={{ body: { padding: 20, height: 120, position: 'relative' } }} variant="borderless">
            <Text type="secondary" style={{ fontSize: 12 }}>成功率</Text>
            <div style={{ fontSize: 32, fontWeight: 'bold', margin: '4px 0', color: '#1d2129' }}>
              {stats.successRate}%
            </div>
            <div style={{ fontSize: 12, color: '#F53F3F', display: 'flex', alignItems: 'center' }}>
              <ArrowDownOutlined style={{ marginRight: 4 }} />
              {Math.abs(stats.rateGrowth)}% 环比
            </div>
            <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, background: '#E8F3FF', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <CheckSquareOutlined style={{ fontSize: 24, color: '#4045D6' }} />
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
            <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', width: 48, height: 48, background: '#E8F3FF', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ToolOutlined style={{ fontSize: 24, color: '#4045D6' }} />
            </div>
          </Card>
        </Col>
      </Row>

      {/* Middle Row: Shortcuts & Notifications */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="快捷方式" variant="borderless" styles={{ body: { padding: '20px 24px' } }}>
            <Row gutter={[16, 16]}>
              <Col span={8}>
                <div 
                  onClick={() => navigate('/repository')}
                  style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '16px 0', textAlign: 'center', cursor: 'pointer', transition: 'all 0.3s' }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = '#4045D6')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = '#f0f0f0')}
                >
                  <AppstoreOutlined style={{ fontSize: 24, color: '#4045D6', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, color: '#1d2129' }}>制品仓库</div>
                </div>
              </Col>
              <Col span={8}>
                <div 
                  onClick={() => navigate('/burning')}
                  style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '16px 0', textAlign: 'center', cursor: 'pointer', transition: 'all 0.3s' }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = '#4045D6')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = '#f0f0f0')}
                >
                  <SettingOutlined style={{ fontSize: 24, color: '#4045D6', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, color: '#1d2129' }}>烧录安装管理</div>
                </div>
              </Col>
              <Col span={8}>
                <div 
                  onClick={() => navigate('/protocol')}
                  style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: '16px 0', textAlign: 'center', cursor: 'pointer', transition: 'all 0.3s' }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = '#4045D6')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = '#f0f0f0')}
                >
                  <SafetyCertificateOutlined style={{ fontSize: 24, color: '#4045D6', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, color: '#1d2129' }}>通信协议验证</div>
                </div>
              </Col>
            </Row>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="动态通知" extra={<span style={{ color: '#666' }}>最近更新</span>} variant="borderless" styles={{ body: { padding: '12px 24px' } }}>
            <List
              dataSource={notifications}
              renderItem={(item) => (
                <List.Item style={{ padding: '8px 0', border: 'none' }}>
                  <Space>
                    <Badge status={item.status as any} />
                    <Text>{item.text}</Text>
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      {/* Bottom Row: Charts */}
      <Card title="安装成功率趋势" variant="borderless" style={{ marginTop: 16 }} extra={<Space><span style={{ color: '#4045D6', borderBottom: '2px solid #4045D6', paddingBottom: 2 }}>近半年</span><span style={{ color: '#666' }}>近一年</span></Space>}>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={trendData} margin={{ top: 20, right: 30, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="month" axisLine={false} tickLine={false} />
            <YAxis axisLine={false} tickLine={false} />
            <Tooltip />
            <Line type="monotone" dataKey="rate" stroke="#4045D6" strokeWidth={3} dot={{ r: 4, fill: '#fff', stroke: '#4045D6', strokeWidth: 2 }} activeDot={{ r: 6 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <Card title="目标安装数量统计" variant="borderless" style={{ marginTop: 16, marginBottom: 24 }} extra={<Space><span style={{ color: '#4045D6', borderBottom: '2px solid #4045D6', paddingBottom: 2 }}>近半年</span><span style={{ color: '#666' }}>近一年</span></Space>}>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={targetData} layout="vertical" margin={{ top: 20, right: 30, left: 20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" axisLine={false} tickLine={false} />
            <YAxis dataKey="name" type="category" axisLine={false} tickLine={false} width={100} />
            <Tooltip cursor={{ fill: 'transparent' }} />
            <Bar dataKey="value" fill="#4045D6" barSize={20} radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>
    </div>
  )
}

export default Workbench
