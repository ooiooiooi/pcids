import { Card, Table, Input, Button, Tabs } from 'antd'
import { FileTextOutlined, BarChartOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { logApi } from '../../services/api'

const Log: React.FC = () => {
  const [loginLoading, setLoginLoading] = useState(false)
  const [opLoading, setOpLoading] = useState(false)
  const [loginData, setLoginData] = useState<any[]>([])
  const [opData, setOpData] = useState<any[]>([])
  const [loginTotal, setLoginTotal] = useState(0)
  const [opTotal, setOpTotal] = useState(0)
  const [loginPage, setLoginPage] = useState(1)
  const [opPage, setOpPage] = useState(1)
  const [loginSearch, setLoginSearch] = useState('')
  const [opSearch, setOpSearch] = useState('')
  const [activeTab, setActiveTab] = useState('login')

  useEffect(() => {
    if (activeTab === 'login') fetchLoginLogs()
  }, [loginPage, activeTab])

  useEffect(() => {
    if (activeTab === 'operation') fetchOpLogs()
  }, [opPage, activeTab])

  const fetchLoginLogs = async () => {
    setLoginLoading(true)
    try {
      const res: any = await logApi.getLoginLogs({ page: loginPage, page_size: 10 })
      if (res.code === 0) { setLoginData(res.data || []); setLoginTotal(res.total || 0) }
    } catch { /* handled by interceptor */ }
    finally { setLoginLoading(false) }
  }

  const fetchOpLogs = async () => {
    setOpLoading(true)
    try {
      const res: any = await logApi.getOperationLogs({ page: opPage, page_size: 10 })
      if (res.code === 0) { setOpData(res.data || []); setOpTotal(res.total || 0) }
    } catch { /* handled by interceptor */ }
    finally { setOpLoading(false) }
  }

  const loginColumns = [
    { title: '用户 ID', dataIndex: 'user_id', key: 'user_id' },
    { title: 'IP 地址', dataIndex: 'ip_address', key: 'ip_address' },
    { title: '登录时间', dataIndex: 'login_time', key: 'login_time',
      render: (v: string) => v?.replace('T', ' ').substring(0, 19),
    },
    { title: '结果', dataIndex: 'result', key: 'result' },
  ]

  const operationColumns = [
    { title: '用户 ID', dataIndex: 'user_id', key: 'user_id' },
    { title: '模块', dataIndex: 'module', key: 'module' },
    { title: '操作', dataIndex: 'action', key: 'action' },
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time',
      render: (v: string) => v?.replace('T', ' ').substring(0, 19),
    },
    { title: '结果', dataIndex: 'result', key: 'result' },
  ]

  const handleLoginSearch = () => { setLoginPage(1); fetchLoginLogs() }
  const handleOpSearch = () => { setOpPage(1); fetchOpLogs() }

  const items = [
    {
      key: 'login',
      label: '登录日志',
      icon: <BarChartOutlined />,
      children: (
        <>
          <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
            <Input placeholder="请输入 IP 地址/用户" style={{ width: 200 }} value={loginSearch}
              onChange={(e) => setLoginSearch(e.target.value)} onPressEnter={handleLoginSearch} />
            <Button type="primary" onClick={handleLoginSearch}>搜索</Button>
          </div>
          <Table columns={loginColumns} dataSource={loginData} rowKey="id" loading={loginLoading}
            pagination={{ total: loginTotal, pageSize: 10, current: loginPage, onChange: setLoginPage }} />
        </>
      ),
    },
    {
      key: 'operation',
      label: '操作日志',
      icon: <FileTextOutlined />,
      children: (
        <>
          <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
            <Input placeholder="请输入模块/操作内容" style={{ width: 300 }} value={opSearch}
              onChange={(e) => setOpSearch(e.target.value)} onPressEnter={handleOpSearch} />
            <Button type="primary" onClick={handleOpSearch}>搜索</Button>
          </div>
          <Table columns={operationColumns} dataSource={opData} rowKey="id" loading={opLoading}
            pagination={{ total: opTotal, pageSize: 10, current: opPage, onChange: setOpPage }} />
        </>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1>日志管理</h1>
        <p style={{ color: '#999' }}>查看系统登录日志和操作日志</p>
      </div>

      <Card>
        <Tabs items={items} defaultActiveKey="login" onChange={(key) => setActiveTab(key)} />
      </Card>
    </div>
  )
}

export default Log
