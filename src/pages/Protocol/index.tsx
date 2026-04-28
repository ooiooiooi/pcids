import { Card, Table, Button, Input, Modal, Form, message, Tabs, Space, Select, Row, Col, Switch, Menu, Badge } from 'antd'
import { SearchOutlined, LinkOutlined, DisconnectOutlined, SwapOutlined, DeleteOutlined, SendOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { protocolTestApi } from '../../services/api'

const Protocol: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isConnectModalOpen, setIsConnectModalOpen] = useState(false)
  const [isConnected, setIsConnected] = useState(false)
  const [connectForm] = Form.useForm()
  const [protocolForm] = Form.useForm()
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [txCount, setTxCount] = useState(13)
  const [rxCount, setRxCount] = useState(11)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [activeTab, setActiveTab] = useState('test')
  const [protocolSubTab, setProtocolSubTab] = useState('can')
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [form] = Form.useForm()
  const [dataType, setDataType] = useState<'HEX' | 'ASCII'>('HEX')

  const canColumns = [
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '帧ID', dataIndex: 'frame_id', key: 'frame_id', width: 80 },
    { title: 'DLC', dataIndex: 'dlc', key: 'dlc', width: 60 },
    { title: '数据(DATA)', dataIndex: 'data', key: 'data' },
  ]

  const ethernetColumns = [
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
    { title: '源地址', dataIndex: 'src_addr', key: 'src_addr', width: 130 },
    { title: '目标地址', dataIndex: 'dst_addr', key: 'dst_addr', width: 130 },
    { title: '协议', dataIndex: 'protocol', key: 'protocol', width: 80 },
    { title: '端口', dataIndex: 'port', key: 'port', width: 60 },
    { title: '数据(DATA)', dataIndex: 'data', key: 'data', ellipsis: true },
  ]

  const serialColumns = [
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '端口', dataIndex: 'port', key: 'port', width: 80 },
    { title: '波特率', dataIndex: 'baud_rate', key: 'baud_rate', width: 100 },
    { title: '数据(Hex/ASCII)', dataIndex: 'data', key: 'data' },
  ]

  const gpioColumns = [
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '引脚', dataIndex: 'pin', key: 'pin', width: 80 },
    { title: '模式', dataIndex: 'mode', key: 'mode', width: 80 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80 },
    { title: '数据', dataIndex: 'data', key: 'data' },
  ]

  const canFdColumns = [
    { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 120 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 60 },
    { title: '帧ID', dataIndex: 'frame_id', key: 'frame_id', width: 80 },
    { title: 'DLC', dataIndex: 'dlc', key: 'dlc', width: 60 },
    { title: '数据(DATA)', dataIndex: 'data', key: 'data' },
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
      
      // Add mock data to visualize the log list according to the prototype
      const mockLogData = [
        { id: 1, timestamp: '08:37:37.498', direction: 'Rx', frame_id: '18DA10F1', dlc: 8, data: '06 50 03 00 00 00 00 00', port: 'COM3', baud_rate: 115200, pin: 'GPIO5', mode: 'OUTPUT', status: 'HIGH' },
        { id: 2, timestamp: '08:37:37.294', direction: 'Tx', frame_id: '18DAF110', dlc: 8, data: '02 10 03 00 00 00 00 00', port: 'COM3', baud_rate: 115200, pin: 'GPIO5', mode: 'INPUT', status: 'LOW' },
        { id: 3, timestamp: '08:37:23.176', direction: 'Rx', frame_id: '0x456', dlc: 8, data: 'EE FF 00 11 (ACK)', port: 'COM3', baud_rate: 115200, pin: 'GPIO5', mode: 'OUTPUT', status: 'HIGH' },
        { id: 4, timestamp: '08:37:22.574', direction: 'Tx', frame_id: '0x123', dlc: 8, data: 'AA BB CC DD', port: 'COM3', baud_rate: 115200, pin: 'GPIO5', mode: 'INPUT', status: 'LOW' },
        { id: 5, timestamp: '08:37:10.548', direction: 'Syste', frame_id: 'INFO', dlc: '--', data: '连接成功: Channel 0, 500kbps', port: 'COM3', baud_rate: 115200, pin: 'Syste', mode: 'INFO', status: '--' },
      ]

      setDataSource(activeTab === 'test' ? mockLogData : res?.data || [])
      setTotal(activeTab === 'test' ? 5 : res?.total || 0)
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
    { key: 'canfd', label: 'CAN FD协议' },
    { key: 'serial', label: '串口' },
    { key: 'ethernet', label: '以太网' },
    { key: 'gpio', label: 'GPIO' },
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

  const handleSend = async () => {
    try {
      const values = await protocolForm.validateFields()
      console.log('Sending data:', values)
      setTxCount(c => c + 1)
      message.success('数据发送成功')
      
      // Simulate receiving an ACK after sending
      setTimeout(() => {
        setRxCount(c => c + 1)
      }, 500)
    } catch (e) {
      console.error(e)
    }
  }

  const renderProtocolForm = () => {
    switch (protocolSubTab) {
      case 'can':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>协议配置</h3>
            <Form.Item label="通道" name="channel" initialValue="CAN1">
              <Select options={[{ label: 'CAN1', value: 'CAN1' }, { label: 'CAN2', value: 'CAN2' }]} />
            </Form.Item>
            <Form.Item label="波特率" name="baud_rate" initialValue="100 kbps">
              <Select options={[{ label: '100 kbps', value: '100 kbps' }, { label: '250 kbps', value: '250 kbps' }, { label: '500 kbps', value: '500 kbps' }]} />
            </Form.Item>
            <Form.Item label="标识符格式" name="id_format" initialValue="标准帧(11位)">
              <Select options={[{ label: '标准帧(11位)', value: '标准帧(11位)' }, { label: '扩展帧(29位)', value: '扩展帧(29位)' }]} />
            </Form.Item>
            <Form.Item label="远程帧" name="remote_frame" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="数据长度(DLC)" name="dlc" initialValue={8}>
              <Select options={[1, 2, 3, 4, 5, 6, 7, 8].map(n => ({ label: n.toString(), value: n }))} />
            </Form.Item>
            <Form.Item label="帧ID" name="frame_id">
              <Input placeholder="0x" />
            </Form.Item>
            <Form.Item label={
              <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                <span>数据</span>
                <Space>
                  <Button size="small" type={dataType === 'HEX' ? 'primary' : 'default'} onClick={() => setDataType('HEX')}>HEX</Button>
                  <Button size="small" type={dataType === 'ASCII' ? 'primary' : 'default'} onClick={() => setDataType('ASCII')}>ASCII</Button>
                </Space>
              </div>
            } name="data">
              <Input.TextArea rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'canfd':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>协议配置</h3>
            <Form.Item label="仲裁段波特率" name="arb_baud_rate" initialValue="1000 kbps">
              <Select options={[{ label: '1000 kbps', value: '1000 kbps' }, { label: '500 kbps', value: '500 kbps' }]} />
            </Form.Item>
            <Form.Item label="数据段波特率" name="data_baud_rate" initialValue="2 Mbps">
              <Select options={[{ label: '2 Mbps', value: '2 Mbps' }, { label: '4 Mbps', value: '4 Mbps' }, { label: '8 Mbps', value: '8 Mbps' }]} />
            </Form.Item>
            <Form.Item label="标识符格式" name="id_format" initialValue="标准帧(11位)">
              <Select options={[{ label: '标准帧(11位)', value: '标准帧(11位)' }, { label: '扩展帧(29位)', value: '扩展帧(29位)' }]} />
            </Form.Item>
            <Form.Item label="比特率切换(BRS)" name="brs" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="数据长度(DLC)" name="dlc" initialValue={64}>
              <Select options={[8, 12, 16, 20, 24, 32, 48, 64].map(n => ({ label: n.toString(), value: n }))} />
            </Form.Item>
            <Form.Item label="帧ID" name="frame_id">
              <Input placeholder="0x" />
            </Form.Item>
            <Form.Item label={
              <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                <span>数据</span>
                <Space>
                  <Button size="small" type={dataType === 'HEX' ? 'primary' : 'default'} onClick={() => setDataType('HEX')}>HEX</Button>
                  <Button size="small" type={dataType === 'ASCII' ? 'primary' : 'default'} onClick={() => setDataType('ASCII')}>ASCII</Button>
                </Space>
              </div>
            } name="data">
              <Input.TextArea rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'serial':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>协议配置</h3>
            <Form.Item label="串口号" name="com_port" initialValue="COM1">
              <Select options={[{ label: 'COM1', value: 'COM1' }, { label: 'COM2', value: 'COM2' }, { label: 'COM3', value: 'COM3' }]} />
            </Form.Item>
            <Form.Item label="波特率" name="baud_rate" initialValue={115200}>
              <Select options={[{ label: '9600', value: 9600 }, { label: '115200', value: 115200 }, { label: '921600', value: 921600 }]} />
            </Form.Item>
            <Form.Item label="数据位" name="data_bits" initialValue={8}>
              <Select options={[{ label: '8', value: 8 }, { label: '7', value: 7 }, { label: '6', value: 6 }]} />
            </Form.Item>
            <Form.Item label="停止位" name="stop_bits" initialValue={1}>
              <Select options={[{ label: '1', value: 1 }, { label: '1.5', value: 1.5 }, { label: '2', value: 2 }]} />
            </Form.Item>
            <Form.Item label="校验位" name="parity" initialValue="NONE">
              <Select options={[{ label: 'NONE', value: 'NONE' }, { label: 'ODD', value: 'ODD' }, { label: 'EVEN', value: 'EVEN' }]} />
            </Form.Item>
            <Form.Item label="流控制" name="flow_control" initialValue="NONE">
              <Select options={[{ label: 'NONE', value: 'NONE' }, { label: 'RTS/CTS', value: 'RTS/CTS' }, { label: 'XON/XOFF', value: 'XON/XOFF' }]} />
            </Form.Item>
            <Form.Item label={
              <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                <span>数据</span>
                <Space>
                  <Button size="small" type={dataType === 'HEX' ? 'primary' : 'default'} onClick={() => setDataType('HEX')}>HEX</Button>
                  <Button size="small" type={dataType === 'ASCII' ? 'primary' : 'default'} onClick={() => setDataType('ASCII')}>ASCII</Button>
                </Space>
              </div>
            } name="data">
              <Input.TextArea rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'ethernet':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>协议配置</h3>
            <Form.Item label="协议" name="protocol" initialValue="TCP">
              <Select options={[{ label: 'TCP', value: 'TCP' }, { label: 'UDP', value: 'UDP' }]} />
            </Form.Item>
            <Form.Item label="目标IP" name="target_ip">
              <Input placeholder="" />
            </Form.Item>
            <Form.Item label="端口号" name="port">
              <Input placeholder="" />
            </Form.Item>
            <Form.Item label="超时时间(ms)" name="timeout">
              <Input placeholder="" />
            </Form.Item>
            <Form.Item label={
              <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
                <span>数据</span>
                <Space>
                  <Button size="small" type={dataType === 'HEX' ? 'primary' : 'default'} onClick={() => setDataType('HEX')}>HEX</Button>
                  <Button size="small" type={dataType === 'ASCII' ? 'primary' : 'default'} onClick={() => setDataType('ASCII')}>ASCII</Button>
                </Space>
              </div>
            } name="data">
              <Input.TextArea rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'gpio':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>协议配置</h3>
            <Form.Item label="引脚选择" name="pin" initialValue="GPIO0">
              <Select options={[{ label: 'GPIO0', value: 'GPIO0' }, { label: 'GPIO1', value: 'GPIO1' }, { label: 'GPIO2', value: 'GPIO2' }, { label: 'GPIO5', value: 'GPIO5' }]} />
            </Form.Item>
            <Form.Item label="模式" name="mode" initialValue="输出">
              <Select options={[{ label: '输出', value: '输出' }, { label: '输入', value: '输入' }]} />
            </Form.Item>
            <Form.Item label="电平" name="level" initialValue="低电平">
              <Select options={[{ label: '低电平', value: '低电平' }, { label: '高电平', value: '高电平' }]} />
            </Form.Item>
            <Form.Item label="中断触发" name="interrupt" initialValue="低电平触发">
              <Select options={[{ label: '低电平触发', value: '低电平触发' }, { label: '高电平触发', value: '高电平触发' }, { label: '上升沿触发', value: '上升沿触发' }, { label: '下降沿触发', value: '下降沿触发' }]} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      default:
        return <div>该协议尚未配置表单</div>
    }
  }

  const renderLogTable = () => {
    if (protocolSubTab === 'ethernet') {
      const ethLogData = [
        '[2025-12-17 15:21:14.110]# Server connected from local 192.168.0.5:57110',
        '[2025-12-17 18:53:01.219]# RECV ASCII/21 from SERVER <<<SSH-2.0-OpenSSH_8.0',
        '[2025-12-17 15:21:14.110]# Server connected from local 192.168.0.5:57110',
        '[2025-12-17 18:53:01.219]# RECV ASCII/21 from SERVER <<<SSH-2.0-OpenSSH_8.0'
      ]

      return (
        <div style={{ 
          background: '#fafafa', 
          border: 'none', 
          padding: 16, 
          fontFamily: 'monospace', 
          height: '100%',
          overflowY: 'auto'
        }}>
          {ethLogData.map((log, index) => (
            <div key={index} style={{ color: '#52c41a', marginBottom: 8, wordBreak: 'break-all' }}>{log}</div>
          ))}
        </div>
      )
    }

    // 渲染带有颜色标记的日志表格
    const columns = getColumns().map((col: any) => {
      if (col.key === 'direction') {
        return {
          ...col,
          render: (text: string) => (
            <span style={{ color: text === 'Rx' ? '#52c41a' : '#1890ff' }}>{text}</span>
          )
        }
      }
      if (col.key === 'data') {
        return {
          ...col,
          render: (text: string, record: any) => {
            if (record.direction === 'Rx') return <span style={{ color: '#52c41a' }}>{text}</span>
            if (record.direction === 'Tx') return <span style={{ color: '#1890ff' }}>{text}</span>
            return <span style={{ color: 'rgba(0,0,0,0.45)' }}>{text}</span>
          }
        }
      }
      if (col.key === 'timestamp' || col.key === 'frame_id' || col.key === 'dlc' || col.key === 'port' || col.key === 'baud_rate' || col.key === 'pin' || col.key === 'mode' || col.key === 'status') {
        return {
          ...col,
          render: (text: string, record: any) => {
            if (record.direction === 'Rx') return <span style={{ color: '#52c41a' }}>{text}</span>
            if (record.direction === 'Tx') return <span style={{ color: '#1890ff' }}>{text}</span>
            return <span style={{ color: 'rgba(0,0,0,0.45)' }}>{text}</span>
          }
        }
      }
      return col
    })

    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Table
          columns={columns}
          dataSource={dataSource}
          rowKey="id"
          loading={loading}
          pagination={false}
          scroll={{ y: 'calc(100vh - 400px)' }}
          size="middle"
          style={{ flex: 1 }}
        />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 16, margin: 0 }}>通信协议验证</h1>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>测试和验证设备通信协议的正确性</p>
        </div>
      </div>

      <Card style={{ flex: 1, display: 'flex', flexDirection: 'column' }} bodyStyle={{ padding: '0 24px 24px', flex: 1, display: 'flex', flexDirection: 'column' }}>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setPage(1) }}
          items={[
            { key: 'test', label: '协议测试' },
            { key: 'record', label: '执行记录' },
          ]}
        />

        {activeTab === 'test' && (
          <div style={{ marginTop: 16, flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', gap: 16, marginBottom: 24, alignItems: 'center' }}>
              <Select defaultValue="stm32f407" style={{ width: 240 }}>
                <Select.Option value="stm32f407">STM32F407开发板</Select.Option>
                <Select.Option value="ti">TI开发板</Select.Option>
              </Select>
              {isConnected ? (
                <Button type="link" danger icon={<DisconnectOutlined />} onClick={handleDisconnect}>断开连接</Button>
              ) : (
                <Button type="link" icon={<LinkOutlined />} onClick={() => setIsConnectModalOpen(true)}>连接设备</Button>
              )}
            </div>
            
            <Row gutter={40} style={{ flex: 1 }}>
              <Col span={4}>
                <Menu
                  mode="inline"
                  selectedKeys={[protocolSubTab]}
                  items={protocolSubTabs}
                  onClick={({ key }) => { setProtocolSubTab(key); setPage(1) }}
                  style={{ 
                    borderRight: 'none', 
                    background: 'transparent',
                    fontSize: 14,
                    color: '#4e5969'
                  }}
                />
              </Col>
              <Col span={8}>
                <div style={{ background: '#fafafa', padding: 24, borderRadius: 8, height: '100%', border: '1px solid #f0f0f0' }}>
                  {renderProtocolForm()}
                </div>
              </Col>
              <Col span={12} style={{ display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div style={{ fontSize: 16, fontWeight: 'bold', color: '#1890ff', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <SwapOutlined /> 通信日志
                  </div>
                  <Space>
                    <Button type="text" icon={<DeleteOutlined />} size="small" style={{ color: '#86909c' }} onClick={() => { setTxCount(0); setRxCount(0); setDataSource([]) }}>清空</Button>
                    <Badge color="green" text={`统计 Tx ${txCount} / Rx ${rxCount}`} style={{ background: '#f6ffed', padding: '2px 8px', borderRadius: 4, border: '1px solid #b7eb8f' }} />
                  </Space>
                </div>
                <div style={{ flex: 1, border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
                  {renderLogTable()}
                </div>
              </Col>
            </Row>
          </div>
        )}

        {activeTab === 'record' && (
          <>
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
          </>
        )}
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
