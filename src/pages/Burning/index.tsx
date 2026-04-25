import { Card, Table, Button, Space, Modal, Form, message, Tag, Popconfirm, Tabs, Select, InputNumber, Input, List } from 'antd'
import { FireOutlined, FileTextOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { taskApi } from '../../services/api'
import { Permission } from '../../hooks'

const softwareOptions = [
  { label: 'U-boot 2023.04', value: 'U-boot 2023.04' },
  { label: 'Linux Kernel 5.10', value: 'Linux Kernel 5.10' },
  { label: 'RootFS Buildroot 2023.02', value: 'RootFS Buildroot 2023.02' },
  { label: 'Device Tree v1.2', value: 'Device Tree v1.2' },
  { label: 'Application Firmware 2.1.0', value: 'Application Firmware 2.1.0' },
]

const Burning: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [isConsistencyOpen, setIsConsistencyOpen] = useState(false)
  const [isOverrideOpen, setIsOverrideOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [activeTab, setActiveTab] = useState('all')
  const [params, setParams] = useState({ page: 1, page_size: 10, status: undefined as number | undefined })
  const [detailTask, setDetailTask] = useState<any>(null)
  const [form] = Form.useForm()

  const tabStatusMap: Record<string, number | undefined> = {
    all: undefined,
    waiting: 0,
    running: 1,
    done: 2,
    failed: 3,
  }

  useEffect(() => { fetchTasks() }, [params])

  const fetchTasks = async () => {
    setLoading(true)
    try {
      const res: any = await taskApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const handleTabChange = (key: string) => {
    setActiveTab(key)
    setParams({ page: 1, page_size: 10, status: tabStatusMap[key] })
  }

  const handleCreate = async (values: any) => {
    try {
      await taskApi.create(values)
      message.success('任务创建成功')
      setIsModalOpen(false)
      form.resetFields()
      fetchTasks()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await taskApi.delete(id)
      message.success('删除成功')
      fetchTasks()
    } catch { /* ignore */ }
  }

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: 'default', text: '等待' },
    1: { color: 'processing', text: '进行中' },
    2: { color: 'success', text: '完成' },
    3: { color: 'error', text: '失败' },
  }

  const columns = [
    {
      title: '软件及版本',
      dataIndex: 'software_name',
      key: 'software_name',
      width: 180,
      render: (text: string, record: any) => {
        const version = record.software_version || ''
        return version ? `${text} ${version}` : (text || '-')
      },
    },
    {
      title: '序号',
      key: 'index',
      width: 60,
      render: (_: any, __: any, index: number) => index + 1,
    },
    {
      title: '执行人',
      dataIndex: 'executor',
      key: 'executor',
      width: 100,
      render: (text: string) => text || '-',
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Button type="link" onClick={() => { setDetailTask(record); setIsDetailOpen(true) }}>详情</Button>
          <Permission code="burning:delete">
            <Popconfirm title="确认删除" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" danger>删除</Button>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: number) => <Tag color={statusMap[s]?.color}>{statusMap[s]?.text}</Tag>,
    },
    {
      title: '烧录安装目标',
      key: 'target',
      width: 180,
      render: (_: any, record: any) => record.board_name || record.target_ip || '-',
    },
    {
      title: '版本一致性报告',
      dataIndex: 'consistency_report',
      key: 'consistency_report',
      width: 120,
      render: (text: string) => text || '-',
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <span style={{ color: 'rgba(28,31,35,0.5)', fontSize: 14, cursor: 'pointer' }} onClick={() => window.history.back()}>&lt;返回</span>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><h1 style={{ fontSize: 16, margin: 0 }}>烧录安装管理</h1><p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理烧录任务和固件安装</p></div>
        <Space>
          <Permission code="burning:add">
            <Button type="primary" icon={<FireOutlined />} onClick={() => setIsModalOpen(true)}>创建任务</Button>
          </Permission>
          <Button icon={<FileTextOutlined />} onClick={() => setIsConsistencyOpen(true)}>一致性报告</Button>
          <Button icon={<CheckCircleOutlined />} onClick={() => setIsOverrideOpen(true)}>强制覆盖</Button>
        </Space>
      </div>

      <Card>
        <Tabs activeKey={activeTab} onChange={handleTabChange} items={[
          { key: 'all', label: '全部' },
          { key: 'waiting', label: '等待' },
          { key: 'running', label: '进行中' },
          { key: 'done', label: '完成' },
          { key: 'failed', label: '失败' },
        ]} />

        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13, marginTop: 16 }}>共 {total} 条</div>

        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>

      <Modal title="创建烧录任务" open={isModalOpen} onOk={() => form.submit()}
        onCancel={() => { setIsModalOpen(false); form.resetFields() }} width={600}>
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item label="软件名称" name="software_name" rules={[{ required: true, message: '请选择软件名称' }]}>
            <Select options={softwareOptions} placeholder="请选择软件名称" />
          </Form.Item>
          <Form.Item label="板卡名称" name="board_name"><Input placeholder="请输入板卡名称" /></Form.Item>
          <Form.Item label="目标 IP 地址" name="target_ip"><Input placeholder="请输入目标 IP 地址" /></Form.Item>
          <Form.Item label="目标端口" name="target_port"><InputNumber placeholder="请输入目标端口" style={{ width: '100%' }} /></Form.Item>
          <Form.Item label="配置参数" name="config_json">
            <Input.TextArea rows={4} placeholder='例如：{"baudrate": 115200}' />
          </Form.Item>
          <Form.Item label="备注信息" name="result"><Input.TextArea rows={3} placeholder="请输入备注信息" /></Form.Item>
        </Form>
      </Modal>

      <Modal title="任务详情" open={isDetailOpen} onCancel={() => setIsDetailOpen(false)} footer={null} width={600}>
        {detailTask && (
          <div>
            <p><strong>软件名称：</strong>{detailTask.software_name}</p>
            <p><strong>板卡名称：</strong>{detailTask.board_name || '-'}</p>
            <p><strong>目标 IP：</strong>{detailTask.target_ip || '-'}</p>
            <p><strong>目标端口：</strong>{detailTask.target_port || '-'}</p>
            <p><strong>状态：</strong><Tag color={statusMap[detailTask.status]?.color}>{statusMap[detailTask.status]?.text}</Tag></p>
            <p><strong>执行文件：</strong>{detailTask.executable || '-'}</p>
            <p><strong>结果：</strong>{detailTask.result || '-'}</p>
            {detailTask.config_json && <p><strong>配置：</strong><pre>{detailTask.config_json}</pre></p>}
            <p><strong>创建时间：</strong>{detailTask.created_at?.replace('T', ' ').substring(0, 19)}</p>
            <hr style={{ margin: '16px 0', borderColor: '#f0f0f0' }} />
            <p><strong>执行日志：</strong></p>
            <div style={{ background: '#1e1e1e', borderRadius: 4, padding: 12, maxHeight: 320, overflow: 'auto' }}>
              <List size="small" dataSource={[
                { time: '14:23:01', text: '开始执行烧录任务: ARM开发板固件烧录' },
                { time: '14:23:02', text: '连接烧录器: J-Link' },
                { time: '14:23:03', text: '烧录器连接成功' },
                { time: '14:23:04', text: '识别目标设备: STM32F407VG' },
                { time: '14:23:05', text: '开始擦除芯片...' },
                { time: '14:23:07', text: '芯片擦除完成' },
                { time: '14:23:08', text: '开始烧录固件: firmware.hex' },
                { time: '14:23:15', text: '烧录进度:65%' },
                { time: '14:23:20', text: '固件烧录完成' },
                { time: '14:23:20', text: '固件烧录完成' },
              ]} renderItem={(item) => (
                <List.Item style={{ padding: '2px 0', borderBottom: 'none', minHeight: 'auto' }}>
                  <span style={{ color: '#888', fontFamily: 'monospace', marginRight: 12, fontSize: 12 }}>{item.time}</span>
                  <span style={{
                    color: item.text.includes('完成') || item.text.includes('成功') ? '#4caf50'
                      : item.text.includes('烧录进度') ? '#ffc107'
                        : '#e0e0e0',
                    fontFamily: 'monospace',
                    fontSize: 12,
                  }}>{item.text}</span>
                </List.Item>
              )} />
            </div>
          </div>
        )}
      </Modal>

      <Modal title="一致性报告" open={isConsistencyOpen} onCancel={() => setIsConsistencyOpen(false)} footer={null} width={600}>
        <p style={{ color: '#999' }}>一致性报告功能开发中，将根据烧录履历生成固件版本一致性分析报告。</p>
      </Modal>

      <Modal title="强制覆盖" open={isOverrideOpen} onCancel={() => setIsOverrideOpen(false)} footer={[
        <Button key="cancel" onClick={() => setIsOverrideOpen(false)}>取消</Button>,
        <Button key="confirm" type="primary" danger onClick={() => { message.info('强制覆盖功能开发中'); setIsOverrideOpen(false) }}>确认覆盖</Button>,
      ]}>
        <p>强制覆盖将重新烧录已完成的板卡，是否继续？</p>
      </Modal>
    </div>
  )
}

export default Burning
