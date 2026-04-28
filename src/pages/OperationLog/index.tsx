import { Table, Input, DatePicker, Button, message, Popconfirm } from 'antd'
import { SearchOutlined, DeleteOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { logApi } from '../../services/api'
import dayjs from 'dayjs'

const { RangePicker } = DatePicker

const OperationLog: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({
    page: 1,
    page_size: 10,
    keyword: '',
    start_date: '',
    end_date: '',
  })

  useEffect(() => { fetchLogs() }, [params])

  const fetchLogs = async () => {
    setLoading(true)
    try {
      const res: any = await logApi.getOperationLogs({
        page: params.page,
        page_size: params.page_size,
        module: undefined,
        start_date: params.start_date || undefined,
        end_date: params.end_date || undefined,
      })
      if (res.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
      }
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }

  const handleSearch = () => {
    setParams({ ...params, page: 1 })
  }

  const handleReset = () => {
    setParams({ page: 1, page_size: 10, keyword: '', start_date: '', end_date: '' })
  }

  const handleClear = async () => {
    try {
      await logApi.clearOperationLogs()
      message.success('操作日志清空成功')
      setParams({ ...params, page: 1 })
    } catch {
      // ignore
    }
  }

  const columns = [
    {
      title: '操作时间',
      dataIndex: 'operation_time',
      key: 'operation_time',
      width: 180,
      render: (val: string) => val ? dayjs(val).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    { title: '登录地址', dataIndex: 'ip_address', key: 'ip_address', width: 150 },
    { title: '用户', dataIndex: 'user_id', key: 'user_id', width: 100 },
    {
      title: '结果',
      dataIndex: 'result',
      key: 'result',
      width: 80,
      render: (val: string) => {
        const isSuccess = val?.includes('成功') || val?.includes('success')
        return (
          <span style={{ color: isSuccess ? '#3DD07B' : '#F53F3F' }}>
            {isSuccess ? '成功' : '失败'}
          </span>
        )
      },
    },
    {
      title: '序号',
      key: 'index',
      width: 60,
      render: (_: any, __: any, index: number) => index + 1,
    },
    { title: '操作模块', dataIndex: 'module', key: 'module', width: 120 },
    { title: '操作内容', dataIndex: 'action', key: 'action', ellipsis: true },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 16, margin: 0 }}>操作日志</h1>
        <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>查看系统操作记录</p>
      </div>

      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Input
            placeholder="请输入IP地址/用户/操作模块/操作内容"
            style={{ width: 360 }}
            prefix={<SearchOutlined />}
            value={params.keyword}
            onChange={(e) => setParams({ ...params, keyword: e.target.value })}
            onPressEnter={handleSearch}
          />
          <RangePicker
            showTime
            placeholder={['开始时间', '结束时间']}
            style={{ width: 380 }}
            onChange={(dates) => {
              setParams({
                ...params,
                start_date: dates?.[0] ? dates[0].format('YYYY-MM-DD HH:mm:ss') : '',
                end_date: dates?.[1] ? dates[1].format('YYYY-MM-DD HH:mm:ss') : '',
              })
            }}
          />
          <Button type="primary" onClick={handleSearch}>搜索</Button>
          <Button onClick={handleReset} type="link" style={{ fontSize: 12, color: '#1890ff' }}>重置</Button>
        </div>
        <div>
          <Popconfirm title="确定要清空所有操作日志吗？" onConfirm={handleClear} okText="确定" cancelText="取消">
            <Button danger icon={<DeleteOutlined />}>清空全部日志</Button>
          </Popconfirm>
        </div>
      </div>

      <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>
        共 {total} 条
      </div>

      <Table
        columns={columns}
        dataSource={dataSource}
        rowKey="id"
        loading={loading}
        pagination={{
          total,
          pageSize: params.page_size,
          current: params.page,
          onChange: (page) => setParams({ ...params, page }),
        }}
      />
    </div>
  )
}

export default OperationLog
