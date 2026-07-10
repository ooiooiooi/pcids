import { Table, Input, DatePicker, Button, message } from 'antd'
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
import EllipsisText from '../../components/EllipsisText'

const { RangePicker } = DatePicker
const operationLogRequestDeduper = createRequestDeduper<any>()

type OperationLogParams = {
  page: number
  page_size: number
  keyword: string
  start_date: string
  end_date: string
}

const OperationLog: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState<OperationLogParams>({
    page: 1,
    page_size: 10,
    keyword: '',
    start_date: '',
    end_date: '',
  })

  useEffect(() => {
    let cancelled = false

    const fetchLogs = async () => {
      const requestParams = {
        page: params.page,
        page_size: params.page_size,
        keyword: params.keyword || undefined,
        module: undefined,
        start_date: params.start_date || undefined,
        end_date: params.end_date || undefined,
      }

      setLoading(true)
      try {
        const res: any = await operationLogRequestDeduper.load(
          JSON.stringify(requestParams),
          () => logApi.getOperationLogs(requestParams),
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
      await logApi.clearOperationLogs()
      operationLogRequestDeduper.clear()
      message.success('操作日志清空成功')
      setParams({ ...params, page: 1 })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '操作日志清空失败')
    }
  }

  const columns = [
    {
      title: '序号',
      key: 'index',
      width: 72,
      render: (_: any, __: any, index: number) => (params.page - 1) * params.page_size + index + 1,
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
    { title: '登录地址', dataIndex: 'ip_address', key: 'ip_address', width: 170, render: (value: string) => <EllipsisText value={value} /> },
    {
      title: '操作时间',
      dataIndex: 'operation_time',
      key: 'operation_time',
      width: 180,
      render: (val: string) => formatDateTime(val),
    },
    { title: '操作模块', dataIndex: 'module', key: 'module', width: 160, render: (value: string) => <EllipsisText value={value} /> },
    {
      title: '操作内容',
      dataIndex: 'action',
      key: 'action',
      width: 320,
      render: (_: string, record: any) => <EllipsisText value={record.content || record.action} />,
    },
    {
      title: '结果',
      dataIndex: 'result',
      key: 'result',
      width: 100,
      render: (val: string) => {
        const isSuccess = val?.includes('成功') || val?.includes('success')
        return (
          <span style={{ color: isSuccess ? '#3DD07B' : '#F53F3F' }}>
            {isSuccess ? '成功' : '失败'}
          </span>
        )
      },
    },
  ]

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <div style={{ marginBottom: 16 }}>
        <div className="client-page-title">
          <h1>操作日志</h1>
          <p className="client-page-subtitle">审计系统操作行为、请求信息与执行结果</p>
        </div>
      </div>

      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'flex-end', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <RangePicker
          id={{ start: 'operation-log-start-time', end: 'operation-log-end-time' }}
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
        <Input
          id="operation-log-keyword"
          name="operationKeyword"
          autoComplete="off"
          className="pcids-list-search"
          placeholder="请输入IP/用户/模块/操作"
          title="请输入IP地址/用户/操作模块/操作内容"
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
        tableLayout="fixed"
        scroll={{ x: 1220 }}
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
      <Permission code="log/operation:clear">
        <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-start', width: '100%' }}>
          <ActionConfirm title="清空操作日志" description="确定要清空所有操作日志吗？" onConfirm={handleClear} okText="确定" cancelText="取消">
            <Button danger icon={<DeleteOutlined />}>清空全部日志</Button>
          </ActionConfirm>
        </div>
      </Permission>
    </div>
  )
}

export default OperationLog
