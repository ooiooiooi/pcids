import { Card, Table, Button, Input, Modal, Form, message, Tabs, Space, Select } from 'antd'
import { PlusOutlined, SearchOutlined, LinkOutlined, DisconnectOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { protocolTestApi } from '../../services/api'
import { Permission } from '../../hooks'

const Protocol: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isConnectModalOpen, setIsConnectModalOpen] = useState(false)
  const [isConnected, setIsConnected] = useState(false)
  const [connectForm] = Form.useForm()
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [activeTab, setActiveTab] = useState('test')
  const [protocolSubTab, setProtocolSubTab] = useState('can')
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [form] = Form.useForm()

  const canColumns = [
    { title: '帧ID', dataIndex: 'frame_id', key: 'frame_id', width: 80 },
    { title: 'DLC', dataIndex: 'dlc', key: 'dlc', width: 60 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '数据', dataIndex: 'data', key: 'data', width: 200 },
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
  ]

  const ethernetColumns = [
    { title: '源地址', dataIndex: 'src_addr', key: 'src_addr', width: 130 },
    { title: '目标地址', dataIndex: 'dst_addr', key: 'dst_addr', width: 130 },
    { title: '协议', dataIndex: 'protocol', key: 'protocol', width: 80 },
    { title: '端口', dataIndex: 'port', key: 'port', width: 60 },
    { title: '数据', dataIndex: 'data', key: 'data', ellipsis: true },
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
  ]

  const serialColumns = [
    { title: '端口', dataIndex: 'port', key: 'port', width: 80 },
    { title: '波特率', dataIndex: 'baud_rate', key: 'baud_rate', width: 100 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '数据 (Hex/ASCII)', dataIndex: 'data', key: 'data', width: 200 },
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
  ]

  const gpioColumns = [
    { title: '引脚', dataIndex: 'pin', key: 'pin', width: 80 },
    { title: '模式', dataIndex: 'mode', key: 'mode', width: 80 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80 },
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
  ]

  const canFdColumns = [
    { title: '帧ID', dataIndex: 'frame_id', key: 'frame_id', width: 80 },
    { title: 'DLC', dataIndex: 'dlc', key: 'dlc', width: 60 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '数据', dataIndex: 'data', key: 'data', width: 200 },
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
  ]

  const recordColumns = [
    { title: '执行人员', dataIndex: 'executor', key: 'executor', width: 100 },
    { title: '序号', dataIndex: 'index', key: 'index', width: 60 },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160 },
    {
      title: '操作', key: 'action',
      render: (_: any, record: any) => (
        <Button type="link" onClick={() => { setSelectedRecord(record); setIsDetailOpen(true) }}>执行详情</Button>
      ),
    },
    { title: '测试对象', dataIndex: 'target', key: 'target', width: 150 },
    { title: '类型', dataIndex: 'type', key: 'type', width: 100 },
  ]

  const getColumns = () => {
    if (activeTab === 'record') return recordColumns
    switch (protocolSubTab) {
      case 'can': return canColumns
      case 'ethernet': return ethernetColumns
      case 'serial': return serialColumns
      case 'gpio': return gpioColumns
      case 'canfd': return canFdColumns
      default: return canColumns
    }
  }

  const fetchData = async () => {
    setLoading(true)
    try {
      const res: any = await protocolTestApi.getList({
        page,
        page_size: 10,
        keyword: keyword || undefined,
        type: activeTab === 'test' ? 'test' : 'record',
        protocol: activeTab === 'test' ? protocolSubTab : undefined,
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
  }, [page, keyword, activeTab, protocolSubTab])

  const handleSearch = () => {
    setPage(1)
    fetchData()
  }

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      await protocolTestApi.create({
        target: values.target,
        address: values.address || undefined,
        data: values.data || undefined,
      })
      message.success('测试创建成功')
      setIsModalOpen(false)
      form.resetFields()
      fetchData()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const protocolSubTabs = [
    { key: 'can', label: 'CAN协议' },
    { key: 'ethernet', label: '以太网' },
    { key: 'serial', label: '串口' },
    { key: 'gpio', label: 'GPIO' },
    { key: 'canfd', label: 'CAN FD协议' },
  ]

  const handleConnect = async () => {
    try {
      await connectForm.validateFields()
      message.success('设备连接成功')
      setIsConnected(true)
      setIsConnectModalOpen(false)
    } catch (e: any) {
      if (e?.errorFields) return
      message.error('连接失败')
    }
  }

  const handleDisconnect = () => {
    message.success('设备已断开连接')
    setIsConnected(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 16, margin: 0 }}>通信协议验证</h1>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>测试和验证设备通信协议的正确性</p>
        </div>
        <Space>
          {isConnected ? (
            <Button danger icon={<DisconnectOutlined />} onClick={handleDisconnect}>断开连接</Button>
          ) : (
            <Button type="default" icon={<LinkOutlined />} onClick={() => setIsConnectModalOpen(true)}>连接设备</Button>
          )}
          <Permission code="protocol:add">
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsModalOpen(true)}>新建测试</Button>
          </Permission>
        </Space>
      </div>

      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setPage(1) }}
          items={[
            { key: 'test', label: '协议测试' },
            { key: 'record', label: '执行记录' },
          ]}
        />

        {activeTab === 'test' && (
          <Tabs
            activeKey={protocolSubTab}
            onChange={(key) => { setProtocolSubTab(key); setPage(1) }}
            items={protocolSubTabs}
            style={{ marginTop: 16 }}
          />
        )}

        <div style={{ marginBottom: 16, display: 'flex', gap: 12, marginTop: 16 }}>
          <Input placeholder="搜索测试对象或地址" prefix={<SearchOutlined />} style={{ width: 250 }}
            value={keyword} onChange={(e) => setKeyword(e.target.value)} onPressEnter={handleSearch} />
          <Button type="primary" onClick={handleSearch}>搜索</Button>
        </div>

        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>

        <Table
          columns={getColumns()}
          dataSource={dataSource}
          rowKey="id"
          loading={loading}
          pagination={{ current: page, pageSize: 10, total, onChange: (p) => setPage(p) }}
        />
      </Card>

      <Modal title="新建协议测试" open={isModalOpen} onOk={handleCreate}
        onCancel={() => { setIsModalOpen(false); form.resetFields() }}>
        <Form form={form} layout="vertical">
          <Form.Item label="测试对象" name="target" rules={[{ required: true, message: '请输入测试对象' }]}>
            <Input placeholder="例如：UART0, SPI1, I2C0" />
          </Form.Item>
          <Form.Item label="地址" name="address">
            <Input placeholder="0x 开头，例如：0x40000000" />
          </Form.Item>
          <Form.Item label="数据" name="data">
            <Input placeholder="空格分隔的十六进制数据，例如：0x01 0x02 0x03" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="测试详情" open={isDetailOpen} onCancel={() => setIsDetailOpen(false)} footer={null}>
        {selectedRecord && (
          <div>
            <p><strong>测试对象：</strong>{selectedRecord.target}</p>
            <p><strong>地址：</strong>{selectedRecord.address || '-'}</p>
            <p><strong>数据：</strong>{selectedRecord.data || '-'}</p>
            <p><strong>结果：</strong>{selectedRecord.result || '-'}</p>
          </div>
        )}
      </Modal>
      <Modal title="连接设备" open={isConnectModalOpen} onOk={handleConnect}
        onCancel={() => { setIsConnectModalOpen(false); connectForm.resetFields() }}>
        <Form form={connectForm} layout="vertical">
          <Form.Item label="连接方式" name="method" initialValue="tcp" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="tcp">TCP/IP</Select.Option>
              <Select.Option value="serial">串口 (Serial)</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prevValues, currentValues) => prevValues.method !== currentValues.method}
          >
            {({ getFieldValue }) => {
              const method = getFieldValue('method')
              return method === 'tcp' ? (
                <>
                  <Form.Item label="IP地址" name="ip" rules={[{ required: true, message: '请输入IP地址' }]}>
                    <Input placeholder="例如：192.168.1.100" />
                  </Form.Item>
                  <Form.Item label="端口" name="port" rules={[{ required: true, message: '请输入端口号' }]}>
                    <Input type="number" placeholder="例如：8080" />
                  </Form.Item>
                </>
              ) : (
                <>
                  <Form.Item label="COM端口" name="com_port" rules={[{ required: true, message: '请选择或输入COM端口' }]}>
                    <Input placeholder="例如：COM3" />
                  </Form.Item>
                  <Form.Item label="波特率" name="baud_rate" initialValue={115200} rules={[{ required: true }]}>
                    <Select>
                      <Select.Option value={9600}>9600</Select.Option>
                      <Select.Option value={115200}>115200</Select.Option>
                    </Select>
                  </Form.Item>
                </>
              )
            }}
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default Protocol
