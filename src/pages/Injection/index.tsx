import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select, Popconfirm, Tabs } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { injectionApi } from '../../services/api'
import { Permission } from '../../hooks'

const Injection: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [injectionType, setInjectionType] = useState('')
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState<number | undefined>(undefined)
  const [activeTab, setActiveTab] = useState('scenario')
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [form] = Form.useForm()

  const injectionTypes = [
    { value: 'power_off', label: '断电模拟' },
    { value: 'storage_full', label: '存储不足' },
    { value: 'network_error', label: '网络中断' },
    { value: 'permission_error', label: '权限缺失' },
  ]

  const typeMap: Record<string, { color: string; text: string }> = {
    power_off: { color: 'red', text: '断电模拟' },
    storage_full: { color: 'orange', text: '存储不足' },
    network_error: { color: 'blue', text: '网络中断' },
    permission_error: { color: 'purple', text: '权限缺失' },
  }

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: 'default', text: '等待' },
    1: { color: 'processing', text: '进行中' },
    2: { color: 'success', text: '完成' },
    3: { color: 'error', text: '失败' },
  }

  const fetchData = async () => {
    setLoading(true)
    try {
      const res: any = await injectionApi.getList({
        page,
        page_size: 10,
        keyword: keyword || undefined,
        status: statusFilter,
        type: activeTab === 'scenario' ? 'scenario' : 'record',
      } as any)
      setDataSource(res?.data || [])
      setTotal(res?.total || 0)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [page, statusFilter, activeTab])

  const handleSearch = () => {
    setPage(1)
    fetchData()
  }

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      const config: any = {}
      if (values.delay) config.delay = values.delay
      if (values.path) config.path = values.path
      await injectionApi.create({
        type: values.type,
        target: values.target,
        config: JSON.stringify(config),
      })
      message.success('注入任务创建成功')
      setIsModalOpen(false)
      form.resetFields()
      setInjectionType('')
      fetchData()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await injectionApi.delete(id)
      message.success('删除成功')
      fetchData()
    } catch {
      message.error('删除失败')
    }
  }

  const handleExecute = async (id: number) => {
    try {
      await injectionApi.execute(id)
      message.success('注入任务已开始执行')
      fetchData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '执行失败')
    }
  }

  const scenarioColumns = [
    {
      title: '异常类型', dataIndex: 'type', key: 'type',
      render: (type: string) => <Tag color={typeMap[type]?.color}>{typeMap[type]?.text || type}</Tag>,
    },
    { title: '目标', dataIndex: 'target', key: 'target' },
    { title: '状态', dataIndex: 'status', key: 'status',
      render: (status: number) => <Tag color={statusMap[status]?.color}>{statusMap[status]?.text}</Tag>,
    },
    { title: '结果', dataIndex: 'result', key: 'result', ellipsis: true },
    {
      title: '操作', key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Permission code="injection:execute">
            <Button type="link" onClick={() => handleExecute(record.id)}>执行</Button>
          </Permission>
          <Button type="link" onClick={() => { setSelectedRecord(record); setIsDetailOpen(true) }}>详情</Button>
          <Permission code="injection:delete">
            <Popconfirm title="确认删除" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" danger>删除</Button>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
  ]

  const recordColumns = [
    { title: '执行状态', dataIndex: 'exec_status', key: 'exec_status', width: 100,
      render: (status: number) => <Tag color={statusMap[status]?.color}>{statusMap[status]?.text}</Tag>,
    },
    { title: '序号', dataIndex: 'index', key: 'index', width: 60 },
    {
      title: '操作', key: 'action', width: 100,
      render: (_: any, record: any) => (
        <Button type="link" onClick={() => { setSelectedRecord(record); setIsDetailOpen(true) }}>执行详情</Button>
      ),
    },
    { title: '异常类型', dataIndex: 'type', key: 'type', width: 120,
      render: (type: string) => <Tag color={typeMap[type]?.color}>{typeMap[type]?.text || type}</Tag>,
    },
    { title: '结果', dataIndex: 'result', key: 'result', width: 120 },
    { title: '测试对象', dataIndex: 'target', key: 'target', width: 150 },
    { title: '执行人员', dataIndex: 'executor', key: 'executor', width: 100 },
    { title: '执行时间', dataIndex: 'exec_time', key: 'exec_time', width: 160 },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 16, margin: 0 }}>异常注入</h1>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>模拟各种异常场景以测试系统容错能力</p>
        </div>
        <Permission code="injection:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsModalOpen(true)}>新增注入</Button>
        </Permission>
      </div>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setPage(1) }}
          items={[
            { key: 'scenario', label: '异常场景' },
            { key: 'record', label: '执行记录' },
          ]}
        />

        <div style={{ marginBottom: 16, display: 'flex', gap: 12, marginTop: 16 }}>
          <Input placeholder="搜索目标或类型" prefix={<SearchOutlined />} style={{ width: 250 }}
            value={keyword} onChange={(e) => setKeyword(e.target.value)} onPressEnter={handleSearch} />
          <Select placeholder="状态筛选" allowClear style={{ width: 150 }}
            value={statusFilter} onChange={(val) => { setStatusFilter(val); setPage(1) }}
            options={[
              { value: 0, label: '等待' }, { value: 1, label: '进行中' },
              { value: 2, label: '完成' }, { value: 3, label: '失败' },
            ]} />
          <Button type="primary" onClick={handleSearch}>搜索</Button>
        </div>

        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>

        <Table
          columns={activeTab === 'scenario' ? scenarioColumns : recordColumns}
          dataSource={dataSource}
          rowKey="id"
          loading={loading}
          pagination={{ current: page, pageSize: 10, total, onChange: (p) => setPage(p) }}
        />
      </Card>

      <Modal
        title="新增异常注入"
        open={isModalOpen}
        onOk={handleCreate}
        onCancel={() => { setIsModalOpen(false); form.resetFields(); setInjectionType('') }}
      >
        <Form form={form} layout="vertical">
          <Form.Item label="异常类型" name="type" rules={[{ required: true, message: '请选择异常类型' }]}>
            <Select placeholder="请选择异常类型" options={injectionTypes} onChange={setInjectionType} />
          </Form.Item>
          <Form.Item label="执行目标" name="target" rules={[{ required: true, message: '请输入执行目标' }]}>
            <Input placeholder="请输入执行目标" />
          </Form.Item>
          {injectionType === 'power_off' && (
            <Form.Item label="延迟时间 (秒)" name="delay">
              <Input type="number" defaultValue={1} />
            </Form.Item>
          )}
          {injectionType === 'storage_full' && (
            <Form.Item label="填充路径" name="path">
              <Input placeholder="/path/to/dir" />
            </Form.Item>
          )}
        </Form>
      </Modal>

      <Modal title="注入详情" open={isDetailOpen} onCancel={() => setIsDetailOpen(false)} footer={null}>
        {selectedRecord && (
          <div>
            <p><strong>类型：</strong>{typeMap[selectedRecord.type]?.text || selectedRecord.type}</p>
            <p><strong>目标：</strong>{selectedRecord.target}</p>
            <p><strong>状态：</strong>{statusMap[selectedRecord.status]?.text}</p>
            <p><strong>结果：</strong>{selectedRecord.result || '-'}</p>
            {selectedRecord.config && (
              <p><strong>配置：</strong><pre>{selectedRecord.config}</pre></p>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}

export default Injection
