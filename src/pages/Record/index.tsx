import { Card, Table, Input, DatePicker, Tabs, Select, Tag } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { recordApi } from '../../services/api'
import dayjs from 'dayjs'

const { RangePicker } = DatePicker

const Record: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [activeTab, setActiveTab] = useState('burn')
  const [params, setParams] = useState({
    page: 1, page_size: 10,
    serial_number: '', software_name: '', operator: '', result: '',
    sort_field: 'operation_time', sort_order: 'desc',
    start_date: '', end_date: '', os_name: ''
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

  const renderResult = (result: string) => {
    const isSuccess = result?.includes('成功') || result === '成功'
    return (
      <Tag color={isSuccess ? 'success' : 'error'} style={{ borderRadius: 10, margin: 0 }}>
        {isSuccess ? '成功' : '失败'}
      </Tag>
    )
  }

  const renderTime = (t: string) => {
    if (!t) return '-'
    const d = dayjs(t)
    return (
      <div>
        <div>{d.format('YYYY-MM-DD')}</div>
        <div style={{ color: '#666' }}>{d.format('HH:mm:ss')}</div>
      </div>
    )
  }

  const renderSoftware = (text: string) => {
    if (!text) return '-'
    const parts = text.split(' ')
    const name = parts[0]
    const version = parts.length > 1 ? parts.slice(1).join(' ') : 'v1.0.1'
    return `${name} ${version}`
  }

  const burnColumns = [
    { title: '板卡序列号', dataIndex: 'serial_number', key: 'serial_number' },
    { title: '板卡名称', dataIndex: 'board_name', key: 'board_name' },
    { title: '软件名称及版本', dataIndex: 'software_name', key: 'software_name', render: renderSoftware },
    { 
      title: '可执行文件提取记录', 
      key: 'extract_record', 
      render: (_: any, record: any) => {
        const logData = record.log_data ? JSON.parse(record.log_data) : {}
        return renderTime(logData.extract_time || record.created_at)
      }
    },
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time', sorter: true, render: renderTime },
    { title: '操作者', dataIndex: 'operator', key: 'operator' },
    { title: '烧录结果', dataIndex: 'result', key: 'result', render: renderResult },
    { title: '备注', dataIndex: 'remark', key: 'remark', ellipsis: true },
  ]

  const installColumns = [
    { title: 'IP地址', dataIndex: 'ip_address', key: 'ip_address' },
    { title: '操作系统', dataIndex: 'os_name', key: 'os_name', render: (t: string) => t || '-' },
    { title: '软件名称及版本', dataIndex: 'software_name', key: 'software_name', render: renderSoftware },
    { 
      title: '可执行文件提取记录', 
      key: 'extract_record', 
      render: (_: any, record: any) => {
        const logData = record.log_data ? JSON.parse(record.log_data) : {}
        return renderTime(logData.extract_time || record.created_at)
      }
    },
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time', sorter: true, render: renderTime },
    { title: '操作者', dataIndex: 'operator', key: 'operator' },
    { title: '安装结果', dataIndex: 'result', key: 'result', render: renderResult },
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

        <div style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 16, justifyContent: 'space-between' }}>
          {activeTab === 'install' ? (
            <Select value={params.os_name || '全部操作系统'} onChange={(v) => setParams({ ...params, os_name: v === '全部操作系统' ? '' : v })} style={{ width: 150 }}>
              <Select.Option value="全部操作系统">全部操作系统</Select.Option>
              <Select.Option value="银河麒麟">银河麒麟</Select.Option>
              <Select.Option value="翼辉">翼辉</Select.Option>
              <Select.Option value="鸿蒙">鸿蒙</Select.Option>
              <Select.Option value="统信">统信</Select.Option>
            </Select>
          ) : <div></div>}
          <div style={{ display: 'flex', gap: 12, flex: 1, justifyContent: 'flex-end' }}>
            <RangePicker 
              showTime 
              onChange={(_, dateStrings) => setParams({ ...params, start_date: dateStrings[0] || '', end_date: dateStrings[1] || '' })} 
            />
            <Input 
              prefix={<SearchOutlined />} 
              placeholder={activeTab === 'burn' ? '请输入序列号' : '请输入IP地址/软件名称/操作者'} 
              style={{ width: 280 }}
              onChange={(e) => {
                setParams({ ...params, serial_number: e.target.value })
              }} 
            />
          </div>
        </div>

        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>

        <Table 
          columns={columns} 
          dataSource={dataSource} 
          rowKey="id" 
          loading={loading}
          onChange={(pagination, _filters, sorter: any) => {
            setParams({
              ...params,
              page: pagination.current || 1,
              page_size: pagination.pageSize || 10,
              sort_field: sorter.field || 'operation_time',
              sort_order: sorter.order === 'ascend' ? 'asc' : 'desc'
            })
          }}
          pagination={{ 
            total, 
            pageSize: params.page_size, 
            current: params.page,
            showTotal: (t) => `共 ${t} 条`
          }} 
        />
      </Card>
    </div>
  )
}

export default Record
