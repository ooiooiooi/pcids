import { useEffect, useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  Menu,
  message,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
} from 'antd'
import {
  DeleteOutlined,
  DisconnectOutlined,
  DownloadOutlined,
  LinkOutlined,
  SearchOutlined,
  SendOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { productApi, protocolTestApi } from '../../services/api'

type ProtocolKind = 'can' | 'canfd' | 'serial' | 'ethernet' | 'gpio'

const Protocol: React.FC = () => {
  const [isConnectModalOpen, setIsConnectModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [products, setProducts] = useState<any[]>([])
  const [selectedTarget, setSelectedTarget] = useState<string>('')
  const [currentSession, setCurrentSession] = useState<any>(null)
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [activeTab, setActiveTab] = useState<'test' | 'record'>('test')
  const [protocolSubTab, setProtocolSubTab] = useState<ProtocolKind>('can')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [txCount, setTxCount] = useState(0)
  const [rxCount, setRxCount] = useState(0)
  const [dataType, setDataType] = useState<'HEX' | 'ASCII'>('HEX')
  const [connectForm] = Form.useForm()
  const [protocolForm] = Form.useForm()

  const protocolSubTabs = [
    { key: 'can', label: 'CAN协议' },
    { key: 'canfd', label: 'CAN FD协议' },
    { key: 'serial', label: '串口' },
    { key: 'ethernet', label: '以太网' },
    { key: 'gpio', label: 'GPIO' },
  ]

  const logColumns = useMemo(
    () => [
      {
        title: '时间戳',
        dataIndex: 'timestamp',
        key: 'timestamp',
        width: 180,
        render: (value: string) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss.SSS') : '-'),
      },
      {
        title: '方向',
        dataIndex: 'direction',
        key: 'direction',
        width: 90,
        render: (value: string) => {
          const color = value === 'Rx' ? 'success' : value === 'Tx' ? 'processing' : 'default'
          return <Tag color={color}>{value || '-'}</Tag>
        },
      },
      {
        title: '标识/引脚',
        dataIndex: 'frame_id',
        key: 'frame_id',
        width: 140,
        render: (value: string) => value || '-',
      },
      {
        title: 'DLC/端口',
        dataIndex: 'dlc',
        key: 'dlc',
        width: 100,
        render: (value: string | number | null) => (value ?? '-') as any,
      },
      {
        title: '数据/状态',
        dataIndex: 'data',
        key: 'data',
        render: (value: string) => <span style={{ wordBreak: 'break-all' }}>{value || '-'}</span>,
      },
    ],
    [],
  )

  const recordColumns = [
    { title: '测试对象', dataIndex: 'target', key: 'target', width: 180 },
    {
      title: '协议',
      dataIndex: 'protocol',
      key: 'protocol',
      width: 120,
      render: (value: string) => value?.toUpperCase?.() || value || '-',
    },
    {
      title: '执行人员',
      dataIndex: 'executor',
      key: 'executor',
      width: 100,
      render: (value: string) => value || '-',
    },
    {
      title: 'Tx/Rx',
      key: 'stats',
      width: 100,
      render: (_: any, record: any) => `${record.tx ?? 0} / ${record.rx ?? 0}`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (value: number) => <Tag color={value === 1 ? 'processing' : 'default'}>{value === 1 ? '已连接' : '已断开'}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Button type="link" onClick={() => openRecordDetail(record.id)}>执行详情</Button>
          <Button type="link" danger onClick={() => handleDeleteRecord(record.id)}>删除</Button>
        </Space>
      ),
    },
  ]

  const selectedTargetLabel = useMemo(() => {
    return products.find((item) => String(item.id) === selectedTarget)?.name || selectedTarget || ''
  }, [products, selectedTarget])

  const fetchProducts = async () => {
    try {
      const res: any = await productApi.getList({ page: 1, page_size: 100 })
      const rows = res?.data || []
      setProducts(rows)
      if (!selectedTarget && rows[0]?.id) {
        setSelectedTarget(String(rows[0].id))
      }
    } catch {
      /* ignore */
    }
  }

  const fetchSessionLogs = async (sessionId: number, silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res: any = await protocolTestApi.getLogs(sessionId, { page: 1, page_size: 200 })
      if (res?.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
        setTxCount(res.tx || 0)
        setRxCount(res.rx || 0)
        setCurrentSession((prev: any) => (prev ? { ...prev, status: res.status, tx: res.tx || 0, rx: res.rx || 0 } : prev))
      }
    } catch {
      /* interceptor handles it */
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const fetchRecords = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res: any = await protocolTestApi.getRecords({
        page,
        page_size: 10,
        keyword: keyword || undefined,
      })
      if (res?.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
      }
    } catch {
      /* interceptor handles it */
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const openRecordDetail = async (recordId: number) => {
    setIsDetailOpen(true)
    setSelectedRecord(null)
    setDetailLoading(true)
    try {
      const res: any = await protocolTestApi.getRecordDetail(recordId)
      if (res?.code === 0) setSelectedRecord(res.data)
    } catch {
      /* interceptor handles it */
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    fetchProducts()
  }, [])

  useEffect(() => {
    if (activeTab === 'test') {
      if (currentSession?.id) {
        fetchSessionLogs(currentSession.id)
      } else {
        setDataSource([])
        setTotal(0)
        setTxCount(0)
        setRxCount(0)
      }
      return
    }
    fetchRecords()
  }, [activeTab, page, keyword, currentSession?.id])

  useEffect(() => {
    if (activeTab !== 'test' || !currentSession?.id || currentSession.status !== 1) return
    const timer = window.setInterval(() => {
      fetchSessionLogs(currentSession.id, true)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [activeTab, currentSession?.id, currentSession?.status])

  const handleConnect = async () => {
    try {
      const values = await connectForm.validateFields()
      const payload = {
        target: selectedTargetLabel || values.ip || values.com_port || '未命名目标',
        protocol: protocolSubTab,
        config: {
          method: values.method,
          ip: values.ip,
          port: values.port,
          com_port: values.com_port,
          baud_rate: values.baud_rate,
        },
      }
      const res: any = await protocolTestApi.connect(payload)
      if (res?.code === 0) {
        setCurrentSession(res.data)
        setIsConnectModalOpen(false)
        message.success('设备连接成功')
        await fetchSessionLogs(res.data.id)
      }
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '连接失败')
    }
  }

  const handleDisconnect = async () => {
    if (!currentSession?.id) return
    try {
      await protocolTestApi.disconnect(currentSession.id)
      message.success('设备已断开连接')
      setCurrentSession((prev: any) => (prev ? { ...prev, status: 2 } : prev))
      await fetchSessionLogs(currentSession.id)
    } catch {
      message.error('断开失败')
    }
  }

  const handleSend = async () => {
    if (!currentSession?.id) {
      message.warning('请先连接设备')
      return
    }
    try {
      const values = await protocolForm.validateFields()
      await protocolTestApi.send(currentSession.id, {
        frame_id: values.frame_id || values.pin || undefined,
        dlc: values.dlc,
        data: values.data || values.level || undefined,
      })
      message.success('数据发送成功')
      await fetchSessionLogs(currentSession.id)
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '发送失败')
    }
  }

  const handleClearLogs = async () => {
    if (!currentSession?.id) return
    try {
      await protocolTestApi.clearLogs(currentSession.id)
      message.success('日志已清空')
      await fetchSessionLogs(currentSession.id)
    } catch {
      message.error('清空失败')
    }
  }

  const handleDeleteRecord = async (recordId: number) => {
    try {
      await protocolTestApi.deleteRecord(recordId)
      message.success('删除成功')
      if (selectedRecord?.id === recordId) {
        setIsDetailOpen(false)
        setSelectedRecord(null)
      }
      fetchRecords()
    } catch {
      message.error('删除失败')
    }
  }

  const handlePreviewReport = async (recordId: number, print = false) => {
    setReportLoading(true)
    try {
      const blobData: any = await protocolTestApi.getReportHtml(recordId, print)
      const blob = blobData instanceof Blob ? blobData : new Blob([blobData], { type: 'text/html;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      const popup = window.open(url, '_blank')
      if (!popup) {
        window.URL.revokeObjectURL(url)
        message.warning('请允许浏览器打开新窗口')
        return
      }
      window.setTimeout(() => window.URL.revokeObjectURL(url), 60000)
    } catch {
      message.error('报告打开失败')
    } finally {
      setReportLoading(false)
    }
  }

  const handleDownloadReport = async (recordId: number) => {
    setReportLoading(true)
    try {
      const blobData: any = await protocolTestApi.getReportCsv(recordId)
      const blob = blobData instanceof Blob ? blobData : new Blob([blobData], { type: 'text/csv;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `protocol_report_${recordId}.csv`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    } catch {
      message.error('报告下载失败')
    } finally {
      setReportLoading(false)
    }
  }

  const renderPayloadLabel = (
    <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%' }}>
      <span>数据</span>
      <Space>
        <Button size="small" type={dataType === 'HEX' ? 'primary' : 'default'} onClick={() => setDataType('HEX')}>HEX</Button>
        <Button size="small" type={dataType === 'ASCII' ? 'primary' : 'default'} onClick={() => setDataType('ASCII')}>ASCII</Button>
      </Space>
    </div>
  )

  const renderProtocolForm = () => {
    switch (protocolSubTab) {
      case 'can':
      case 'canfd':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>总线数据发送</h3>
            <Form.Item label="帧ID" name="frame_id" rules={[{ required: true, message: '请输入帧ID' }]}>
              <Input placeholder="例如：18DAF110" />
            </Form.Item>
            <Form.Item label="数据长度(DLC)" name="dlc" initialValue={protocolSubTab === 'canfd' ? 64 : 8}>
              <Select options={(protocolSubTab === 'canfd' ? [8, 12, 16, 20, 24, 32, 48, 64] : [1, 2, 3, 4, 5, 6, 7, 8]).map((n) => ({ label: n.toString(), value: n }))} />
            </Form.Item>
            <Form.Item label={renderPayloadLabel} name="data">
              <Input.TextArea rows={4} placeholder={dataType === 'HEX' ? '例如：02 10 03 00' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'serial':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>串口数据发送</h3>
            <Form.Item label={renderPayloadLabel} name="data" rules={[{ required: true, message: '请输入待发送数据' }]}>
              <Input.TextArea rows={5} placeholder={dataType === 'HEX' ? '例如：AA BB CC DD' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'ethernet':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>网络数据发送</h3>
            <Form.Item label="数据" name="data" rules={[{ required: true, message: '请输入待发送数据' }]}>
              <Input.TextArea rows={5} placeholder="请输入待发送报文" />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      case 'gpio':
        return (
          <Form form={protocolForm} layout="vertical">
            <h3 style={{ marginTop: 0, marginBottom: 16 }}>GPIO 控制</h3>
            <Form.Item label="引脚" name="pin" initialValue="GPIO0" rules={[{ required: true }]}>
              <Select options={[0, 1, 2, 5].map((n) => ({ label: `GPIO${n}`, value: `GPIO${n}` }))} />
            </Form.Item>
            <Form.Item label="电平" name="level" initialValue="HIGH" rules={[{ required: true }]}>
              <Select options={[{ label: '高电平', value: 'HIGH' }, { label: '低电平', value: 'LOW' }]} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend}>发送</Button>
          </Form>
        )
      default:
        return <Empty description="该协议尚未配置表单" />
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 16, margin: 0 }}>通信协议验证</h1>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>测试和验证设备通信协议的正确性</p>
        </div>
      </div>

      <Card
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { padding: '0 24px 24px', flex: 1, display: 'flex', flexDirection: 'column' } }}
      >
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key as 'test' | 'record'); setPage(1) }}
          items={[
            { key: 'test', label: '协议测试' },
            { key: 'record', label: '执行记录' },
          ]}
        />

        {activeTab === 'test' && (
          <div style={{ marginTop: 16, flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', gap: 16, marginBottom: 24, alignItems: 'center' }}>
              <Select value={selectedTarget || undefined} style={{ width: 240 }} placeholder="选择测试对象" onChange={(value) => setSelectedTarget(value)}>
                {products.map((item) => (
                  <Select.Option key={item.id} value={String(item.id)}>{item.name}</Select.Option>
                ))}
              </Select>
              {currentSession?.status === 1 ? (
                <Button type="link" danger icon={<DisconnectOutlined />} onClick={handleDisconnect}>断开连接</Button>
              ) : (
                <Button type="link" icon={<LinkOutlined />} onClick={() => setIsConnectModalOpen(true)}>连接设备</Button>
              )}
              {currentSession?.id ? <Tag color={currentSession.status === 1 ? 'processing' : 'default'}>当前会话 #{currentSession.id}</Tag> : null}
            </div>

            <Row gutter={40} style={{ flex: 1 }}>
              <Col span={4}>
                <Menu
                  mode="inline"
                  selectedKeys={[protocolSubTab]}
                  items={protocolSubTabs}
                  onClick={({ key }) => setProtocolSubTab(key as ProtocolKind)}
                  style={{ borderRight: 'none', background: 'transparent', fontSize: 14, color: '#4e5969' }}
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
                    <Button type="text" icon={<DeleteOutlined />} size="small" style={{ color: '#86909c' }} onClick={handleClearLogs} disabled={!currentSession?.id}>清空</Button>
                    <Badge color="green" text={`统计 Tx ${txCount} / Rx ${rxCount}`} style={{ background: '#f6ffed', padding: '2px 8px', borderRadius: 4, border: '1px solid #b7eb8f' }} />
                  </Space>
                </div>
                <div style={{ flex: 1, border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
                  <Table
                    columns={logColumns}
                    dataSource={dataSource}
                    rowKey="id"
                    loading={loading}
                    pagination={false}
                    scroll={{ y: 'calc(100vh - 400px)' }}
                    size="middle"
                    locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" /> }}
                  />
                </div>
              </Col>
            </Row>
          </div>
        )}

        {activeTab === 'record' && (
          <>
            <div style={{ marginBottom: 16, display: 'flex', gap: 12, marginTop: 16 }}>
              <Input
                placeholder="搜索测试对象"
                prefix={<SearchOutlined />}
                style={{ width: 250 }}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onPressEnter={() => { setPage(1); fetchRecords() }}
              />
              <Button type="primary" onClick={() => { setPage(1); fetchRecords() }}>搜索</Button>
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
        title="协议记录详情"
        open={isDetailOpen}
        onCancel={() => setIsDetailOpen(false)}
        footer={selectedRecord ? (
          <Space>
            <Button loading={reportLoading} onClick={() => handlePreviewReport(selectedRecord.id)}>预览 HTML</Button>
            <Button loading={reportLoading} onClick={() => handlePreviewReport(selectedRecord.id, true)}>打印</Button>
            <Button type="primary" loading={reportLoading} icon={<DownloadOutlined />} onClick={() => handleDownloadReport(selectedRecord.id)}>下载 CSV</Button>
          </Space>
        ) : null}
      >
        {selectedRecord && (
          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 12 }}>
            <strong>测试对象</strong><span>{selectedRecord.target || '-'}</span>
            <strong>协议</strong><span>{selectedRecord.protocol?.toUpperCase?.() || selectedRecord.protocol || '-'}</span>
            <strong>执行人员</strong><span>{selectedRecord.executor || '-'}</span>
            <strong>连接状态</strong><span>{selectedRecord.status === 1 ? '已连接' : '已断开'}</span>
            <strong>统计</strong><span>Tx {selectedRecord.tx || 0} / Rx {selectedRecord.rx || 0}</span>
            <strong>IP 地址</strong><span>{selectedRecord.ip_address || '-'}</span>
            <strong>创建时间</strong><span>{selectedRecord.created_at ? dayjs(selectedRecord.created_at).format('YYYY-MM-DD HH:mm:ss') : '-'}</span>
            <strong>连接配置</strong>
            <pre style={{ margin: 0, padding: 12, background: '#fafafa', borderRadius: 6, border: '1px solid #f0f0f0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{selectedRecord.config_json || '{}'}</pre>
          </div>
        )}
        {!selectedRecord && !detailLoading && <Empty description="暂无数据" />}
      </Modal>

      <Modal
        title="连接设备"
        open={isConnectModalOpen}
        onOk={handleConnect}
        onCancel={() => { setIsConnectModalOpen(false); connectForm.resetFields() }}
      >
        <Form form={connectForm} layout="vertical" initialValues={{ method: protocolSubTab === 'serial' ? 'serial' : 'tcp', baud_rate: 115200 }}>
          <Form.Item label="连接方式" name="method" rules={[{ required: true }]}>
            <Select>
              <Select.Option value="tcp">TCP/IP</Select.Option>
              <Select.Option value="serial">串口 (Serial)</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.method !== currentValues.method}>
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
                  <Form.Item label="波特率" name="baud_rate" rules={[{ required: true }]}>
                    <Select>
                      <Select.Option value={9600}>9600</Select.Option>
                      <Select.Option value={115200}>115200</Select.Option>
                      <Select.Option value={921600}>921600</Select.Option>
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
