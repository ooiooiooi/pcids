import { Table, Input, Select, DatePicker, Button, Tag, message } from 'antd'
import { SearchOutlined, DeleteOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { logApi } from '../../services/api'
import dayjs from 'dayjs'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { createRequestDeduper } from '../../utils/requestDeduper'
import { Permission } from '../../hooks'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm from '../../components/ActionConfirm'

const { RangePicker } = DatePicker
const loginLogRequestDeduper = createRequestDeduper<any>()

type LoginLogParams = {
  page: number
  page_size: number
  keyword: string
  type: string
  start_date: string
  end_date: string
}

const LoginLog: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState<LoginLogParams>({
    page: 1,
    page_size: 10,
    keyword: '',
    type: '',
    start_date: '',
    end_date: '',
  })

  useEffect(() => {
    let cancelled = false

    const fetchLogs = async () => {
      const requestParams = {
        page: params.page,
        page_size: params.page_size,
        user_id: undefined,
        keyword: params.keyword || undefined,
        type: params.type || undefined,
        start_date: params.start_date || undefined,
        end_date: params.end_date || undefined,
      }

      setLoading(true)
      try {
        const res: any = await loginLogRequestDeduper.load(
          JSON.stringify(requestParams),
          () => logApi.getLoginLogs(requestParams as any),
        )
        if (cancelled) return
        if (res.code === 0) {
          setDataSource(res.data || [])
          setTotal(res.total || 0)
        }
      } catch {
        // ignore
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchLogs()

    return () => {
      cancelled = true
    }
  }, [params])

  const handleClear = async () => {
    try {
      await logApi.clearLoginLogs()
      loginLogRequestDeduper.clear()
      message.success('登录日志清空成功')
      setParams({ ...params, page: 1 })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '登录日志清空失败')
    }
  }

  const columns = [
    {
      title: '序号',
      key: 'index',
      width: 72,
      render: (_: any, _record: any, index: number) => (params.page - 1) * params.page_size + index + 1,
    },
    {
      title: '用户',
      dataIndex: 'username',
      key: 'username',
      width: 220,
      render: (_: string, record: any) => (
        <UserIdentity
          user={record}
          fallbackName={record.username || '未知用户'}
          avatarSize={23}
        />
      ),
    },
    {
      title: '登录时间',
      dataIndex: 'login_time',
      key: 'login_time',
      width: 180,
      render: (val: string) => formatDateTime(val),
    },
    {
      title: '日志类型',
      dataIndex: 'log_type',
      key: 'log_type',
      width: 120,
      render: (val: string) => {
        if (val === 'login' || val === '登录') return <Tag color="blue">登录</Tag>
        if (val === 'logout' || val === '登出') return <Tag color="orange">登出</Tag>
        return <Tag>{val || '-'}</Tag>
      },
    },
    { title: '登录地址', dataIndex: 'ip_address', key: 'ip_address', width: 160 },
    {
      title: '结果',
      dataIndex: 'result',
      key: 'result',
      width: 120,
      render: (val: string) => {
        const rawText = String(val || '').trim()
        const isSuccess = rawText === 'success' || rawText === '成功' || rawText.includes('成功')
        const isFailure =
          rawText === 'fail' ||
          rawText === '失败' ||
          rawText.includes('失败') ||
          rawText.includes('错误') ||
          rawText.includes('禁用') ||
          rawText.includes('已满')
        const text = rawText || '-'
        if (text === '-') return '-'
        return (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              minWidth: 72,
              padding: '2px 10px',
              borderRadius: 6,
              fontSize: 12,
              lineHeight: '20px',
              color: isSuccess ? '#34A853' : isFailure ? '#F53F3F' : '#1D2129',
              background: isSuccess
                ? 'rgba(52, 168, 83, 0.12)'
                : isFailure
                  ? 'rgba(245, 63, 63, 0.12)'
                  : 'rgba(29, 33, 41, 0.06)',
              border: `1px solid ${
                isSuccess
                  ? 'rgba(52, 168, 83, 0.24)'
                  : isFailure
                    ? 'rgba(245, 63, 63, 0.24)'
                    : 'rgba(29, 33, 41, 0.12)'
              }`,
            }}
          >
            {text}
          </span>
        )
      },
    },
  ]

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <div style={{ marginBottom: 16 }}>
        <div className="client-page-title">
          <h1>登录日志</h1>
          <p className="client-page-subtitle">查看用户登录历史、来源地址与认证结果</p>
        </div>
      </div>

      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'flex-end', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <RangePicker
          id={{ start: 'login-log-start-time', end: 'login-log-end-time' }}
          showTime
          placeholder={['开始时间', '结束时间']}
          style={{ width: 380 }}
          value={params.start_date && params.end_date ? [dayjs(params.start_date), dayjs(params.end_date)] : null}
          onChange={(dates) => {
            setParams((prev) => ({
              ...prev,
              page: 1,
              start_date: dates?.[0] ? dates[0].format('YYYY-MM-DD HH:mm:ss') : '',
              end_date: dates?.[1] ? dates[1].format('YYYY-MM-DD HH:mm:ss') : '',
            }))
          }}
        />
        <Select
          id="login-log-type"
          style={{ width: 122 }}
          value={params.type}
          onChange={(val) => setParams((prev) => ({ ...prev, page: 1, type: val }))}
          options={[
            { value: '', label: '全部类型' },
            { value: 'login', label: '登录' },
            { value: 'logout', label: '登出' },
          ]}
        />
        <Input
          id="login-log-keyword"
          name="loginKeyword"
          autoComplete="off"
          className="pcids-list-search"
          placeholder="请输入IP/用户"
          title="请输入IP地址/用户"
          allowClear
          prefix={<SearchOutlined />}
          value={params.keyword}
          onChange={(e) => setParams((prev) => ({ ...prev, page: 1, keyword: e.target.value }))}
        />
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
          showSizeChanger: false,
          showTotal: (t) =>
            renderListPaginationTotal(t, params.page_size, (pageSize) =>
              setParams({ ...params, page: 1, page_size: pageSize }),
            ),
        }}
      />
      <Permission code="log/login:clear">
        <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-start' }}>
          <ActionConfirm title="清空登录日志" description="确定要清空所有登录日志吗？" onConfirm={handleClear} okText="确定" cancelText="取消">
            <Button danger icon={<DeleteOutlined />}>清空全部日志</Button>
          </ActionConfirm>
        </div>
      </Permission>
    </div>
  )
}

export default LoginLog
