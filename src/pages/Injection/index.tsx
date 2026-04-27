import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select, Popconfirm, Tabs, Row, Col, Checkbox, InputNumber, Radio } from 'antd'
import { SearchOutlined, EditOutlined, CaretRightOutlined, PlusOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { injectionApi, productApi } from '../../services/api'
import { Permission } from '../../hooks'

const Injection: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [isConfigOpen, setIsConfigOpen] = useState(false)
  const [isExecOpen, setIsExecOpen] = useState(false)
  const [injectionType, setInjectionType] = useState('')
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [boards, setBoards] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState<number | undefined>(undefined)
  const [activeTab, setActiveTab] = useState('scenario')
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [form] = Form.useForm()
  const [execForm] = Form.useForm()

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

      const bRes: any = await productApi.getList({ page: 1, page_size: 100 })
      setBoards(bRes?.data || [])
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
      
      // Map form values to config based on injection type
      if (injectionType === 'power_off') {
        config.duration = values.power_duration
        config.strategy = values.power_strategy
      } else if (injectionType === 'storage_full') {
        config.method = values.storage_method
        config.location = values.storage_location
        if (values.storage_location === 'custom') config.custom_location = values.storage_custom_location
        config.size = values.storage_size
        if (values.storage_size === 'custom') config.custom_size = values.storage_custom_size
        config.strategy = values.storage_strategy
      } else if (injectionType === 'network_error') {
        config.type = values.network_type
        config.duration = values.network_duration
        if (values.network_duration === 'custom') config.custom_duration = values.network_custom_duration
        config.strategy = values.network_strategy
      }

      const payload = {
        type: injectionType,
        target: 'placeholder', // we edit this later or handle differently
        config: JSON.stringify(config),
        status: 0
      }

      if (selectedRecord) {
        await injectionApi.update(selectedRecord.id, payload)
        message.success('配置保存成功')
      } else {
        await injectionApi.create(payload)
        message.success('创建成功')
      }
      
      setIsConfigOpen(false)
      form.resetFields()
      setInjectionType('')
      setSelectedRecord(null)
      fetchData()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '保存失败')
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

  const handleExecute = async () => {
    try {
      const values = await execForm.validateFields()
      if (!selectedRecord) return
      
      // Update target and execute
      await injectionApi.update(selectedRecord.id, { target: values.target })
      await injectionApi.execute(selectedRecord.id)
      
      message.success('注入任务已开始执行')
      setIsExecOpen(false)
      execForm.resetFields()
      setSelectedRecord(null)
      fetchData()
    } catch (e: any) {
      if (e?.errorFields) return
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
            <Button type="link" onClick={() => { setSelectedRecord(record); setIsExecOpen(true) }}>执行</Button>
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

  const getScenarioConfig = (type: string, configJson: string) => {
    try {
      const c = JSON.parse(configJson)
      if (type === 'power_off') {
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>持续时长&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.duration === 'custom' ? c.custom_duration : c.duration} 秒</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>恢复策略&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.strategy === 'auto' ? '自动恢复' : '手动恢复'}</span></div>
          </>
        )
      }
      if (type === 'storage_full') {
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>填充方式&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.method === 'single' ? '创建单个大文件' : '创建多个小文件'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>填充位置及大小&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.location === 'custom' ? c.custom_location : c.location}, {c.size === 'custom' ? c.custom_size + '%' : c.size}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>恢复策略&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.strategy === 'auto' ? '测试完成后自动...' : '手动清理'}</span></div>
          </>
        )
      }
      if (type === 'network_error') {
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>中断类型&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.type === 'disconnect' ? '完全中断' : c.type === 'packet_loss' ? '高丢包率' : '高延迟'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>持续时长&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.duration === 'custom' ? c.custom_duration : c.duration} 秒</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>恢复策略&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.strategy === 'auto' ? '超时后自动恢复...' : '手动恢复'}</span></div>
          </>
        )
      }
      if (type === 'permission_error') {
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>权限变更对象&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>烧录目录</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>变更类型&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>移除写权限</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>影响范围&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>当前用户...</span></div>
          </>
        )
      }
    } catch {
      return null
    }
  }

  const scenarioCards = [
    {
      id: 1,
      type: 'power_off',
      title: '断电异常模拟',
      config_json: '{"duration":5,"strategy":"auto"}'
    },
    {
      id: 2,
      type: 'storage_full',
      title: '存储不足模拟',
      config_json: '{"method":"single","location":"/tmp","size":50,"strategy":"auto"}'
    },
    {
      id: 3,
      type: 'network_error',
      title: '网络中断模拟',
      config_json: '{"type":"disconnect","duration":10,"strategy":"auto"}'
    },
    {
      id: 4,
      type: 'permission_error',
      title: '权限缺失模拟',
      config_json: '{"target":"burn_dir","type":"remove_write","scope":"current_user"}'
    }
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

        {activeTab === 'scenario' && (
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            {scenarioCards.map(item => (
              <Col xs={24} sm={12} md={8} lg={6} key={item.id}>
                <Card 
                  hoverable 
                  bodyStyle={{ padding: 20 }} 
                  style={{ borderRadius: 8, height: '100%', display: 'flex', flexDirection: 'column' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
                    <div style={{ 
                      width: 32, height: 32, borderRadius: 8, 
                      background: '#F0F5FF', color: '#4045D6', 
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      marginRight: 12
                    }}>
                      <SearchOutlined style={{ fontSize: 16 }} />
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 'bold' }}>{item.title}</div>
                  </div>
                  
                  <div style={{ flex: 1, marginBottom: 20 }}>
                    {getScenarioConfig(item.type, item.config_json)}
                  </div>
                  
                  <div style={{ display: 'flex', gap: 12 }}>
                    <Button 
                      type="primary" 
                      icon={<CaretRightOutlined />} 
                      onClick={() => {
                        setSelectedRecord(item)
                        setIsExecOpen(true)
                      }}
                      style={{ flex: 1 }}
                    >
                      执行
                    </Button>
                    <Button 
                      icon={<EditOutlined />} 
                      onClick={() => {
                        setSelectedRecord(item)
                        setInjectionType(item.type)
                        try {
                          const c = JSON.parse(item.config_json)
                          if (item.type === 'power_off') {
                            form.setFieldsValue({
                              power_duration: c.duration,
                              power_strategy: c.strategy,
                            })
                          } else if (item.type === 'storage_full') {
                            form.setFieldsValue({
                              storage_method: c.method,
                              storage_location: ['/tmp', '/var/tmp'].includes(c.location) ? c.location : 'custom',
                              storage_custom_location: ['/tmp', '/var/tmp'].includes(c.location) ? undefined : c.location,
                              storage_size: [50, 80].includes(c.size) ? c.size : 'custom',
                              storage_custom_size: [50, 80].includes(c.size) ? undefined : c.size,
                              storage_strategy: c.strategy,
                            })
                          } else if (item.type === 'network_error') {
                            form.setFieldsValue({
                              network_type: c.type,
                              network_duration: [10, 30].includes(c.duration) ? c.duration : 'custom',
                              network_custom_duration: [10, 30].includes(c.duration) ? undefined : c.duration,
                              network_strategy: c.strategy,
                            })
                          }
                        } catch {}
                        setIsConfigOpen(true)
                      }}
                      style={{ flex: 1 }}
                    >
                      编辑
                    </Button>
                  </div>
                </Card>
              </Col>
            ))}
          </Row>
        )}

        {activeTab === 'record' && (
          <>
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
              columns={recordColumns}
              dataSource={dataSource}
              rowKey="id"
              loading={loading}
              pagination={{ current: page, pageSize: 10, total, onChange: (p) => setPage(p) }}
            />
          </>
        )}
      </Card>

      <Modal
        title={selectedRecord?.title + "编辑"}
        open={isConfigOpen}
        onOk={() => form.submit()}
        onCancel={() => { setIsConfigOpen(false); form.resetFields(); setInjectionType('') }}
        width={500}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate} initialValues={{ 
          power_duration: 5, power_strategy: 'auto',
          storage_method: 'single', storage_location: '/tmp', storage_size: 50, storage_strategy: 'auto',
          network_type: 'disconnect', network_duration: 10, network_strategy: 'auto',
        }}>
          {injectionType === 'power_off' && (
            <>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>参数配置</div>
              <Form.Item label="持续时间" name="power_duration" style={{ marginBottom: 24 }}>
                <Radio.Group>
                  <Radio value={5}>5秒</Radio>
                  <Radio value={10}>10秒</Radio>
                  <Radio value={30}>30秒</Radio>
                  <Radio value="custom">自定义 <InputNumber size="small" style={{ width: 60, margin: '0 8px' }} /> 秒</Radio>
                </Radio.Group>
              </Form.Item>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>恢复策略</div>
              <Form.Item name="power_strategy">
                <Radio.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <Radio value="auto">自动恢复供电</Radio>
                  <Radio value="manual">保持断电状态，手动恢复</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}

          {injectionType === 'storage_full' && (
            <>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>参数配置</div>
              <Form.Item label="填充方式" name="storage_method" style={{ marginBottom: 16 }}>
                <Radio.Group>
                  <Radio value="single">创建单个大文件</Radio>
                  <Radio value="multi">创建多个小文件</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="填充位置" name="storage_location" style={{ marginBottom: 16 }}>
                <Radio.Group>
                  <Radio value="/tmp">/tmp</Radio>
                  <Radio value="/var/tmp">/var/tmp</Radio>
                  <Radio value="custom">自定义路径 <Input size="small" style={{ width: 120, marginLeft: 8 }} placeholder="/path/to/dir" /></Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="填充大小" name="storage_size" style={{ marginBottom: 24 }}>
                <Radio.Group>
                  <Radio value={50}>50%可用空间</Radio>
                  <Radio value={80}>80%可用空间</Radio>
                  <Radio value="custom">自定义 <InputNumber size="small" style={{ width: 60, margin: '0 8px' }} /> %</Radio>
                </Radio.Group>
              </Form.Item>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>恢复策略</div>
              <Form.Item name="storage_strategy">
                <Radio.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <Radio value="auto">测试完成后自动清理临时文件</Radio>
                  <Radio value="manual">保留临时文件（需手动清理）</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}

          {injectionType === 'network_error' && (
            <>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>参数配置</div>
              <Form.Item label="中断类型" name="network_type" style={{ marginBottom: 16 }}>
                <Radio.Group>
                  <Radio value="disconnect">完全断开</Radio>
                  <Radio value="packet_loss">高丢包率 (80%)</Radio>
                  <Radio value="latency">高延迟 (2s)</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="持续时长" name="network_duration" style={{ marginBottom: 24 }}>
                <Radio.Group>
                  <Radio value={10}>10秒</Radio>
                  <Radio value={30}>30秒</Radio>
                  <Radio value="custom">自定义 <InputNumber size="small" style={{ width: 60, margin: '0 8px' }} /> 秒</Radio>
                </Radio.Group>
              </Form.Item>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>恢复策略</div>
              <Form.Item name="network_strategy">
                <Radio.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <Radio value="auto">超时后自动恢复网络配置</Radio>
                  <Radio value="manual">保持中断状态，手动恢复</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}

          {injectionType === 'permission_error' && (
            <>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>参数配置</div>
              <Form.Item label="权限变更对象" name="permission_target" style={{ marginBottom: 16 }}>
                <Radio.Group>
                  <Radio value="burn_dir">烧录目录</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="变更类型" name="permission_type" style={{ marginBottom: 16 }}>
                <Radio.Group>
                  <Radio value="remove_write">移除写权限</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="影响范围" name="permission_scope" style={{ marginBottom: 24 }}>
                <Radio.Group>
                  <Radio value="current_user">当前用户</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>

      <Modal
        title="选择执行目标"
        open={isExecOpen}
        onOk={() => execForm.submit()}
        onCancel={() => { setIsExecOpen(false); execForm.resetFields() }}
      >
        <Form form={execForm} layout="horizontal" onFinish={handleExecute}>
          <Form.Item label="选择执行目标" name="target" rules={[{ required: true, message: '请选择执行目标' }]}>
            <Select placeholder="请选择板卡" options={boards.map(b => ({ label: b.name, value: b.id }))} />
          </Form.Item>
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
