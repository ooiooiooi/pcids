import { Card, Row, Col, Statistic, Progress, Tag } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
} from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { taskApi, burnerApi, productApi } from '../../services/api'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts'

const statusMap: Record<number, { color: string; text: string }> = {
  0: { color: 'default', text: '等待' },
  1: { color: 'processing', text: '进行中' },
  2: { color: 'success', text: '完成' },
  3: { color: 'error', text: '失败' },
}

const PIE_COLORS = ['#4045D6', '#3DD07B', '#F5C400', '#F53F3F', '#722ed1', '#13c2c2', '#eb2f96', '#fa8c16']

const Workbench: React.FC = () => {
  const [stats, setStats] = useState({
    totalTasks: 0,
    successRate: 0,
    failedTasks: 0,
    burnerCount: 0,
    burnerOnlineCount: 0,
  })
  const [trendData, setTrendData] = useState<any[]>([])
  const [chipTypeData, setChipTypeData] = useState<any[]>([])
  const [recentTasks, setRecentTasks] = useState<any[]>([])

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      const [tasksRes, burnersRes, productsRes]: any[] = await Promise.all([
        taskApi.getList({ page: 1, page_size: 100 }),
        burnerApi.getList({ page: 1, page_size: 100 }),
        productApi.getList({ page: 1, page_size: 100 }),
      ])

      const allTasks = tasksRes?.data || []
      const allBurners = burnersRes?.data || []
      const allProducts = productsRes?.data || []
      const completed = allTasks.filter((t: any) => t.status === 2)
      const failed = allTasks.filter((t: any) => t.status === 3)
      const successCount = completed.filter((t: any) => t.result?.includes('成功') || t.result?.includes('Success')).length
      const onlineBurners = allBurners.filter((b: any) => b.status === 1)

      setStats({
        totalTasks: allTasks.length,
        successRate: completed.length > 0 ? Number(((successCount / completed.length) * 100).toFixed(1)) : 0,
        failedTasks: failed.length,
        burnerCount: allBurners.length,
        burnerOnlineCount: onlineBurners.length,
      })

      setRecentTasks(allTasks.slice(0, 5))

      // Trend data: group tasks by date and calculate success rate
      const dateMap: Record<string, { total: number; success: number }> = {}
      allTasks.forEach((t: any) => {
        const date = t.created_at?.substring(0, 10) || 'unknown'
        if (!dateMap[date]) dateMap[date] = { total: 0, success: 0 }
        dateMap[date].total++
        if (t.status === 2) dateMap[date].success++
      })
      const trend = Object.entries(dateMap)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([date, data]) => ({
          date,
          rate: data.total > 0 ? Math.round((data.success / data.total) * 100) : 0,
        }))
      setTrendData(trend.length > 0 ? trend : [{ date: '暂无数据', rate: 0 }])

      // Chip type distribution
      const chipMap: Record<string, number> = {}
      allProducts.forEach((p: any) => {
        const ct = p.chip_type || '未分类'
        chipMap[ct] = (chipMap[ct] || 0) + 1
      })
      const chipData = Object.entries(chipMap).map(([name, value]) => ({ name, value }))
      setChipTypeData(chipData.length > 0 ? chipData : [{ name: '暂无数据', value: 0 }])
    } catch {
      // ignore
    }
  }

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 16, margin: 0 }}>工作台</h1>
        <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>欢迎回来，祝您工作愉快！</p>
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总烧录任务"
              value={stats.totalTasks}
              prefix={<ClockCircleOutlined />}
              valueStyle={{ color: '#4045D6' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="成功率"
              value={stats.successRate}
              precision={1}
              suffix="%"
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#3DD07B' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="失败任务"
              value={stats.failedTasks}
              prefix={<CloseCircleOutlined />}
              valueStyle={{ color: '#F53F3F' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="注册烧录器"
              value={stats.burnerCount}
              prefix={<DatabaseOutlined />}
              valueStyle={{ color: '#722ed1' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 图表区域 */}
      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={16}>
          <Card title="安装成功率趋势">
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={trendData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis domain={[0, 100]} />
                <Tooltip formatter={(value: any) => [`${value}%`, '成功率']} />
                <Line type="monotone" dataKey="rate" stroke="#4045D6" strokeWidth={2} dot={{ r: 4 }} />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="芯片类型分布">
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={chipTypeData}
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  dataKey="value"
                  label={({ name, percent }: any) => `${name} ${(percent * 100).toFixed(0)}%`}
                >
                  {chipTypeData.map((_entry: any, index: number) => (
                    <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>

      {/* 最近任务和系统状态 */}
      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={12}>
          <Card title="最近任务">
            <div style={{ padding: '8px 0' }}>
              {recentTasks.length === 0 && <p style={{ color: '#999' }}>暂无任务</p>}
              {recentTasks.map((task: any) => (
                <div key={task.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                  <span>{task.software_name || task.board_name || '-'}</span>
                  <Tag color={statusMap[task.status]?.color}>{statusMap[task.status]?.text}</Tag>
                </div>
              ))}
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="系统状态">
            <div style={{ padding: '12px 0' }}>
              <div style={{ marginBottom: 16 }}>
                <span style={{ marginRight: 16 }}>数据库状态</span>
                <Progress percent={100} status="active" size="small" style={{ width: '60%' }} />
              </div>
              <div style={{ marginBottom: 16 }}>
                <span style={{ marginRight: 16 }}>烧录器在线</span>
                <Progress
                  percent={stats.burnerCount > 0 ? Math.round((stats.burnerOnlineCount / stats.burnerCount) * 100) : 0}
                  strokeColor="#3DD07B"
                  size="small"
                  style={{ width: '60%' }}
                />
              </div>
              <div>
                <span style={{ marginRight: 16 }}>存储空间</span>
                <Progress percent={67} size="small" style={{ width: '60%' }} />
              </div>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  )
}

export default Workbench
