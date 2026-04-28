import { Table, Input, Select, DatePicker, Button, Tag, message, Popconfirm } from 'antd'
import { SearchOutlined, DeleteOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { logApi } from '../../services/api'
import dayjs from 'dayjs'

const { RangePicker } = DatePicker

const LoginLog: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({
    page: 1,
    page_size: 10,
    keyword: '',
    type: '',
    start_date: '',
    end_date: '',
  })

  useEffect(() => { fetchLogs() }, [params])

  const fetchLogs = async () => {
    setLoading(true)
    try {
      const res: any = await logApi.getLoginLogs({
        page: params.page,
        page_size: params.page_size,
        user_id: undefined,
        keyword: params.keyword || undefined,
        type: params.type || undefined,
        start_date: params.start_date || undefined,
        end_date: params.end_date || undefined,
      } as any)
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
    setParams({ page: 1, page_size: 10, keyword: '', type: '', start_date: '', end_date: '' })
  }

  const handleClear = async () => {
    try {
      await logApi.clearLoginLogs()
      message.success('登录日志清空成功')
      setParams({ ...params, page: 1 })
    } catch {
      // ignore
    }
  }

  const columns = [
    {
      title: '登录时间',
      dataIndex: 'login_time',
      key: 'login_time',
      width: 180,
      render: (val: string) => val ? dayjs(val).format('YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: '日志类型',
      dataIndex: 'log_type',
      key: 'log_type',
      width: 100,
      render: (val: string) => {
        if (val === 'login' || val === '登录') return <Tag color="blue">登录</Tag>
        if (val === 'logout' || val === '登出') return <Tag color="orange">登出</Tag>
        return <Tag>{val || '-'}</Tag>
      },
    },
    { title: '登录地址', dataIndex: 'ip_address', key: 'ip_address', width: 150 },
    { title: '用户', dataIndex: 'username', key: 'username', width: 80 },
    {
      title: '结果',
      dataIndex: 'result',
      key: 'result',
      width: 100,
      render: (val: string) => {
        const text = val === 'success' || val === '成功' ? '成功' : val === 'fail' || val === '失败' ? '失败' : (val || '-')
        return <Tag color={text === '成功' ? 'success' : 'error'}>{text}</Tag>
      },
    },
    {
      title: '序号',
      key: 'index',
      width: 60,
      render: (_: any, _record: any, index: number) => (params.page - 1) * params.page_size + index + 1,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 16, margin: 0 }}>登录日志</h1>
        <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>查看用户登录历史记录</p>
      </div>

      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Input
            placeholder="请输入IP地址/用户"
            style={{ width: 200 }}
            prefix={<SearchOutlined />}
            value={params.keyword}
            onChange={(e) => setParams({ ...params, keyword: e.target.value })}
            onPressEnter={handleSearch}
          />
          <Select
            placeholder="全部类型"
            style={{ width: 122 }}
            allowClear
            value={params.type || undefined}
            onChange={(val) => setParams({ ...params, type: val || '' })}
            options={[
              { value: 'login', label: '登录' },
              { value: 'logout', label: '登出' },
            ]}
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
          <Popconfirm title="确定要清空所有登录日志吗？" onConfirm={handleClear} okText="确定" cancelText="取消">
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

export default LoginLog
