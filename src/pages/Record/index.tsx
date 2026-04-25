import { Card, Table, Input, DatePicker, Button, Tabs, message } from 'antd'
import { useState, useEffect } from 'react'
import { recordApi } from '../../services/api'
import { Permission } from '../../hooks'

const { RangePicker } = DatePicker

const Record: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [activeTab, setActiveTab] = useState('burn')
  const [params, setParams] = useState({
    page: 1, page_size: 10,
    serial_number: '', software_name: '', operator: '', result: '',
  })

  useEffect(() => {
    fetchRecords()
  }, [params, activeTab])

  const fetchRecords = async () => {
    setLoading(true)
    try {
      const res: any = await recordApi.getList({
        ...params,
        type: activeTab === 'burn' ? 'burn' : 'install',
      } as any)
      if (res.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
      }
    } catch {
      // error handled by interceptor
    } finally {
      setLoading(false)
    }
  }

  const renderResult = (result: string) => (
    <span style={{ color: result?.includes('成功') || result === '成功' ? '#3DD07B' : '#F53F3F' }}>
      {result || '-'}
    </span>
  )

  const burnColumns = [
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time', width: 160 },
    { title: '板卡序列号', dataIndex: 'serial_number', key: 'serial_number', width: 150 },
    { title: '操作者', dataIndex: 'operator', key: 'operator', width: 100 },
    { title: '板卡名称', dataIndex: 'board_name', key: 'board_name', width: 150 },
    { title: '软件名称及版本', dataIndex: 'software_name', key: 'software_name', width: 180 },
    { title: '烧录结果', dataIndex: 'result', key: 'result', width: 80, render: renderResult },
    { title: '备注', dataIndex: 'remark', key: 'remark', ellipsis: true },
  ]

  const installColumns = [
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time', width: 160 },
    { title: '操作者', dataIndex: 'operator', key: 'operator', width: 100 },
    { title: '操作系统', dataIndex: 'os_name', key: 'os_name', width: 130 },
    { title: '软件名称及版本', dataIndex: 'software_name', key: 'software_name', width: 180 },
    { title: 'IP地址', dataIndex: 'ip_address', key: 'ip_address', width: 150 },
    { title: '安装结果', dataIndex: 'result', key: 'result', width: 80, render: renderResult },
    { title: '备注', dataIndex: 'remark', key: 'remark', ellipsis: true },
  ]

  const columns = activeTab === 'burn' ? burnColumns : installColumns

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 16, margin: 0 }}>履历记录</h1>
        <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>查看所有烧录和安装历史记录</p>
      </div>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setParams({ ...params, page: 1 }) }}
          items={[
            { key: 'burn', label: '烧录记录' },
            { key: 'install', label: '安装记录' },
          ]}
        />

        <div style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 16 }}>
          <Input placeholder="请输入序列号" style={{ width: 200 }}
            onChange={(e) => setParams({ ...params, serial_number: e.target.value })} />
          <Input placeholder="请输入软件名称" style={{ width: 200 }}
            onChange={(e) => setParams({ ...params, software_name: e.target.value })} />
          <Input placeholder="请输入操作者" style={{ width: 150 }}
            onChange={(e) => setParams({ ...params, operator: e.target.value })} />
          <RangePicker />
          <Permission code="record:export">
            <Button onClick={() => message.info('导出功能开发中')}>导出</Button>
          </Permission>
          <Button type="primary" onClick={() => setParams({ ...params, page: 1 })}>搜索</Button>
        </div>

        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>

        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>
    </div>
  )
}

export default Record
