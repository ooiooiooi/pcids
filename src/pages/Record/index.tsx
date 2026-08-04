import { Table, Input, DatePicker, Tabs, Select, Tag, Modal } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { recordApi } from '../../services/api'
import { renderListPaginationTotal } from '../../utils/pagination'
import { getRepositoryProjectContext, REPOSITORY_PROJECT_CONTEXT_EVENT } from '../../utils/repositoryProjectContext'
import UserIdentity from '../../components/UserIdentity'
import EllipsisText from '../../components/EllipsisText'
import { formatDateTime } from '../../utils/dateTime'

const { RangePicker } = DatePicker

function firstFilled(...values: Array<any>) {
  for (const value of values) {
    if (value === null || value === undefined) continue
    const text = String(value).trim()
    if (text) return text
  }
  return ''
}

function parseRecordLogData(value: any) {
  if (!value) return {}
  try {
    const parsed = typeof value === 'string' ? JSON.parse(value) : value
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

const Record: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [activeTab, setActiveTab] = useState('burn')
  const [currentProject, setCurrentProject] = useState(getRepositoryProjectContext)
  const [detailRecord, setDetailRecord] = useState<any>(null)
  const [params, setParams] = useState({
    page: 1, page_size: 10,
    keyword: '', result: '',
    sort_field: 'operation_time', sort_order: 'desc',
    start_date: '', end_date: '', os_name: ''
  })

  useEffect(() => {
    fetchRecords()
  }, [params, activeTab, currentProject.projectKey])

  useEffect(() => {
    const handleProjectChange = () => {
      setCurrentProject(getRepositoryProjectContext())
      setParams((prev) => ({ ...prev, page: 1 }))
      setDetailRecord(null)
    }
    window.addEventListener(REPOSITORY_PROJECT_CONTEXT_EVENT, handleProjectChange)
    window.addEventListener('storage', handleProjectChange)
    return () => {
      window.removeEventListener(REPOSITORY_PROJECT_CONTEXT_EVENT, handleProjectChange)
      window.removeEventListener('storage', handleProjectChange)
    }
  }, [])

  const fetchRecords = async () => {
    if (!currentProject.projectKey) {
      setDataSource([])
      setTotal(0)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const res: any = await recordApi.getList({
        ...params,
        type: activeTab === 'burn' ? 'burn' : 'install',
        project_key: currentProject.projectKey,
      })
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
    return <span style={{ whiteSpace: 'nowrap' }}>{formatDateTime(t)}</span>
  }

  const getRecordSoftwareName = (record?: any) => firstFilled(record?.software_name, record?.repository_name) || '-'
  const getRecordProjectName = (record?: any) =>
    firstFilled(record?.project_name, record?.project_key === currentProject.projectKey ? currentProject.projectName : '') || '-'

  const getRecordVersionText = (record?: any) => {
    const explicitVersion = firstFilled(record?.software_version)
    if (explicitVersion) return explicitVersion
    const softwareText = String(record?.software_name || '').trim()
    const parts = softwareText.split(/\s+/)
    if (parts.length > 1) {
      const maybeVersion = parts.slice(1).join(' ').trim()
      if (/^v?\d+(\.\d+)+/i.test(maybeVersion)) return maybeVersion
    }
    return ''
  }

  const renderSoftware = (_text: string, record: any) => {
    const softwareName = getRecordSoftwareName(record)
    const versionText = getRecordVersionText(record)
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <EllipsisText value={softwareName} style={{ flex: '0 1 auto', color: '#2f3747' }} />
        {versionText ? (
          <Tag color="blue" style={{ borderRadius: 10, marginInlineEnd: 0, flex: '0 0 auto' }}>
            {versionText}
          </Tag>
        ) : (
          <span style={{ color: '#b7bfcc', flex: '0 0 auto' }}>未维护版本</span>
        )}
      </div>
    )
  }

  const renderOperator = (_text: string, record: any) => {
    return (
      <UserIdentity
        user={record?.operator_user}
        fallbackName={record?.operator}
        avatarSize={23}
      />
    )
  }

  const renderServerTime = (t: string) => {
    if (!t) return '-'
    return <span style={{ whiteSpace: 'nowrap' }}>{formatDateTime(t)}</span>
  }

  const renderEllipsisText = (value: any) => {
    const text = firstFilled(value) || '-'
    return <EllipsisText value={text} />
  }

  const getInstallAddressOrDevice = (record?: any) => {
    const logData = parseRecordLogData(record?.log_data)
    const osName = firstFilled(record?.os_name, logData.os_name)
    const target = firstFilled(record?.target, logData.target)
    const harmonyDeviceId = firstFilled(
      record?.harmony_device_id,
      logData.harmony_device_id,
      osName.includes('鸿蒙') && target.includes('|') ? target.split('|').pop() : '',
    )
    if (osName.includes('鸿蒙') && harmonyDeviceId) return harmonyDeviceId
    return firstFilled(record?.ip_address, target) || '-'
  }

  const burnColumns = [
    { title: '任务编号', dataIndex: 'task_no', key: 'task_no', width: 130, render: (value: string) => value || '-' },
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', width: 160, render: (_: string, record: any) => getRecordProjectName(record) },
    { title: '板卡序列号', dataIndex: 'serial_number', key: 'serial_number', width: 120, render: renderEllipsisText },
    { title: '板卡名称', dataIndex: 'board_name', key: 'board_name', width: 150, render: renderEllipsisText },
    { title: '软件名称及版本', dataIndex: 'software_name', key: 'software_name', width: 220, render: renderSoftware },
    { 
      title: '可执行文件提取记录',
      key: 'extract_record', 
      width: 170,
      render: (_: any, record: any) => {
        const logData = parseRecordLogData(record.log_data)
        return renderServerTime(logData.extract_time || record.created_at)
      }
    },
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time', width: 170, sorter: true, render: renderTime },
    { title: '操作者', dataIndex: 'operator', key: 'operator', width: 120, render: renderOperator },
    { title: '烧录结果', dataIndex: 'result', key: 'result', render: renderResult },
    { title: '备注', dataIndex: 'remark', key: 'remark', width: 240, render: renderEllipsisText },
  ]

  const installColumns = [
    { title: '任务编号', dataIndex: 'task_no', key: 'task_no', width: 130, render: (value: string) => value || '-' },
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', width: 160, render: (_: string, record: any) => getRecordProjectName(record) },
    { title: 'IP地址/设备号', dataIndex: 'ip_address', key: 'ip_address', width: 150, render: (_: string, record: any) => renderEllipsisText(getInstallAddressOrDevice(record)) },
    { title: '操作系统', dataIndex: 'os_name', key: 'os_name', render: (t: string) => t || '-' },
    { title: '软件名称及版本', dataIndex: 'software_name', key: 'software_name', width: 220, render: renderSoftware },
    { 
      title: '可执行文件提取记录',
      key: 'extract_record', 
      width: 170,
      render: (_: any, record: any) => {
        const logData = parseRecordLogData(record.log_data)
        return renderServerTime(logData.extract_time || record.created_at)
      }
    },
    { title: '操作时间', dataIndex: 'operation_time', key: 'operation_time', width: 170, sorter: true, render: renderTime },
    { title: '操作者', dataIndex: 'operator', key: 'operator', width: 120, render: renderOperator },
    { title: '安装结果', dataIndex: 'result', key: 'result', render: renderResult },
    { title: '备注', dataIndex: 'remark', key: 'remark', width: 240, render: renderEllipsisText },
  ]

  const columns = activeTab === 'burn' ? burnColumns : installColumns
  const searchPlaceholderFull = activeTab === 'burn'
    ? '请输入序列号/板卡名称/操作人'
    : '请输入IP地址/设备号/软件名称/板卡名称/操作人'
  const detailLogData = parseRecordLogData(detailRecord?.log_data)
  const detailRows = detailRecord ? [
    { label: '任务编号', value: detailRecord.task_no || '-' },
    { label: '项目名称', value: getRecordProjectName(detailRecord) },
    { label: '目标', value: detailRecord.target || detailRecord.board_name || detailRecord.ip_address || '-' },
    { label: activeTab === 'burn' ? '板卡序列号' : 'IP地址/设备号', value: activeTab === 'burn' ? (detailRecord.serial_number || '-') : getInstallAddressOrDevice(detailRecord) },
    { label: activeTab === 'burn' ? '板卡名称' : '操作系统', value: activeTab === 'burn' ? (detailRecord.board_name || '-') : (detailRecord.os_name || '-') },
    { label: '软件名称', value: getRecordSoftwareName(detailRecord) },
    { label: '软件版本', value: getRecordVersionText(detailRecord) || '-' },
    { label: '操作人', value: detailRecord.operator || '-' },
    { label: '操作时间', value: formatDateTime(detailRecord.operation_time) },
    { label: activeTab === 'burn' ? '烧录结果' : '安装结果', value: detailRecord.result || '-' },
    { label: '详细内容', value: detailRecord.detail_content || detailLogData.detail_content || detailLogData.last_error || '-' },
    { label: '提取时间', value: formatDateTime(detailLogData.extract_time || detailRecord.created_at) },
    { label: '备注', value: detailRecord.remark || '-' },
  ] : []

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <div style={{ marginBottom: 16 }}>
        <div className="client-page-title">
          <h1>履历记录</h1>
          <p className="client-page-subtitle">查看烧录、安装、目标系统与执行结果历史</p>
        </div>
      </div>

      <div style={{ background: '#fff', borderRadius: 8 }}>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setParams({ ...params, page: 1 }) }}
          items={[
            { key: 'burn', label: '烧录记录' },
            { key: 'install', label: '安装记录' },
          ]}
        />

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
          {activeTab === 'install' ? (
            <Select value={params.os_name || '全部操作系统'} onChange={(v) => setParams({ ...params, page: 1, os_name: v === '全部操作系统' ? '' : v })} style={{ width: 150 }}>
              <Select.Option value="全部操作系统">全部操作系统</Select.Option>
              <Select.Option value="银河麒麟">银河麒麟</Select.Option>
              <Select.Option value="翼辉">翼辉</Select.Option>
              <Select.Option value="鸿蒙">鸿蒙</Select.Option>
              <Select.Option value="统信">统信</Select.Option>
            </Select>
          ) : null}
          <RangePicker
            showTime
            onChange={(_, dateStrings) => setParams({ ...params, start_date: dateStrings[0] || '', end_date: dateStrings[1] || '' })}
          />
          <Input
            className="pcids-list-search"
            placeholder={searchPlaceholderFull}
            title={searchPlaceholderFull}
            allowClear
            value={params.keyword}
            prefix={<SearchOutlined />}
            onChange={(e) => {
              const nextValue = e.target.value
              setParams({ ...params, keyword: nextValue, page: 1 })
            }}
            onPressEnter={(e: any) => setParams({ ...params, keyword: e.target.value, page: 1 })}
          />
        </div>

        <Table 
          columns={columns} 
          dataSource={dataSource} 
          rowKey="id" 
          loading={loading}
          onRow={(record) => ({
            style: { cursor: 'pointer' },
            onClick: () => setDetailRecord(record),
          })}
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
            showSizeChanger: false,
            showTotal: (t) =>
              renderListPaginationTotal(t, params.page_size, (pageSize) =>
                setParams({ ...params, page: 1, page_size: pageSize }),
              ),
          }} 
        />
        <Modal
          title={activeTab === 'burn' ? '烧录履历详情' : '安装履历详情'}
          className="pcids-modal pcids-modal--wide"
          open={Boolean(detailRecord)}
          onCancel={() => setDetailRecord(null)}
          footer={null}
        >
          <div style={{ display: 'grid', gridTemplateColumns: '110px minmax(0, 1fr)', gap: '14px 18px', paddingTop: 4 }}>
            {detailRows.map((item) => (
              <div key={item.label} style={{ display: 'contents' }}>
                <div style={{ color: '#86909c' }}>{item.label}</div>
                <div style={{ color: '#1d2129', wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>{item.value}</div>
              </div>
            ))}
          </div>
        </Modal>
      </div>
    </div>
  )
}

export default Record
