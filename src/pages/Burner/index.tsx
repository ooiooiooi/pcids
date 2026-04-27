import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select, Radio, Switch, Checkbox, Alert } from 'antd'
import { PlusOutlined, SyncOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { burnerApi } from '../../services/api'
import { Permission } from '../../hooks'

const Burner: React.FC = () => {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '', status: undefined as number | undefined })
  const [editingBurner, setEditingBurner] = useState<any>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [keepAdding, setKeepAdding] = useState(false)
  const [createStrategy, setCreateStrategy] = useState(1)
  const [editStrategy, setEditStrategy] = useState(1)

  useEffect(() => { fetchBurners() }, [params])

  const fetchBurners = async () => {
    setLoading(true)
    try {
      const res: any = await burnerApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const handleCreate = async (values: any) => {
    try {
      const payload = { ...values, strategy: createStrategy, is_enabled: values.is_enabled }
      // The name logic is currently handled differently in the prototype,
      // it seems they use `type` as the "烧录器" dropdown and no name field. 
      // Let's ensure name is set (if missing) to type + random or just type
      if (!payload.name) payload.name = payload.type
      
      await burnerApi.create(payload)
      message.success('创建成功')
      if (!keepAdding) {
        setIsCreateModalOpen(false)
      }
      createForm.resetFields()
      setCreateStrategy(1)
      fetchBurners()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      const payload = { ...values, strategy: editStrategy, is_enabled: values.is_enabled }
      if (!payload.name) payload.name = payload.type
      
      await burnerApi.update(editingBurner.id, payload)
      message.success('更新成功')
      setIsEditModalOpen(false)
      fetchBurners()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await burnerApi.delete(id)
      message.success('删除成功')
      fetchBurners()
    } catch { /* ignore */ }
  }

  const statusFilterOptions = [
    { value: 0, label: '空闲' },
    { value: 1, label: '离线' },
    { value: 2, label: '占用' },
  ]

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: 'success', text: '空闲' },
    1: { color: 'default', text: '离线' },
    2: { color: 'processing', text: '占用' },
    3: { color: 'error', text: '禁用' },
  }

  const columns = [
    { title: '烧录器名称', dataIndex: 'name', key: 'name', width: 140 },
    { 
      title: 'SN/物理端口', 
      key: 'sn_port', 
      width: 200,
      render: (_: any, record: any) => {
        return record.strategy === 1 ? record.sn : record.port
      }
    },
    { title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 160, render: (val: string) => val ? val.replace('T', ' ').substring(0, 16) : '-' },
    { title: '修改人', dataIndex: 'modified_by', key: 'modified_by', width: 100 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (s: number, record: any) => {
      // In the prototype, if it's disabled, it should be shown as disabled
      if (record.is_enabled === false) {
        return <Tag color="error">禁用</Tag>
      }
      return <Tag color={statusMap[s]?.color}>{statusMap[s]?.text}</Tag>
    } },
    {
      title: '操作', key: 'action', width: 140,
      render: (_: any, record: any) => (
        <Space>
          <Permission code="burner:edit">
            <Button type="link" onClick={() => { 
              setEditingBurner(record)
              editForm.setFieldsValue({
                ...record,
                is_enabled: record.is_enabled ?? true
              })
              setEditStrategy(record.strategy || 1)
              setIsEditModalOpen(true) 
            }}>编辑</Button>
          </Permission>
          <Permission code="burner:delete">
            <Button type="link" danger onClick={() => handleDelete(record.id)}>删除</Button>
          </Permission>
        </Space>
      ),
    },
  ]

  const formBody = (form: any, strategy: number, setStrategy: (val: number) => void, onFinish: (values: any) => void) => (
    <Form form={form} layout="horizontal" labelCol={{ span: 5 }} wrapperCol={{ span: 18 }} onFinish={onFinish}>
      <Alert 
        message="物理端口位置识别的烧录器发生物理位置更改时，请重新获取新的物理位置并保存" 
        type="info" 
        showIcon={false}
        style={{ marginBottom: 20, color: '#2b52d9', backgroundColor: '#eef2ff', borderColor: '#d0d8fb' }}
      />
      <Form.Item label="烧录器" name="type" rules={[{ required: true, message: '请选择烧录器' }]} required>
        <Select placeholder="请选择类型" options={[
          { value: 'J_LINK V11', label: 'J_LINK V11' }, { value: 'PWLINK V2', label: 'PWLINK V2' },
          { value: 'GDLINK', label: 'GDLINK' }, { value: 'ST_LINK', label: 'ST_LINK' }, { value: 'AL321', label: 'AL321' }
        ]} />
      </Form.Item>
      
      <Form.Item label="物理位置" name="location">
        <Input placeholder="例如：USB插槽1" />
      </Form.Item>

      <Form.Item label="识别策略">
        <Radio.Group value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          <Radio value={1}>按SN序列号识别</Radio>
          <Radio value={2}>按物理端口位置识别</Radio>
        </Radio.Group>
      </Form.Item>

      {strategy === 1 && (
        <Form.Item 
          label="SN标识码" 
          name="sn" 
          required 
          rules={[{ required: true, message: '请输入SN标识码' }]}
          extra={<div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>推荐 J-LINK、ST-LINK 等支持序列号的设备使用</div>}
        >
          <Input 
            placeholder="例如：37FF71064E573436F2FC1443" 
            suffix={<a style={{ display: 'flex', alignItems: 'center', gap: 4 }}><SyncOutlined /> 获取标识码</a>}
          />
        </Form.Item>
      )}

      {strategy === 2 && (
        <Form.Item 
          label="物理端口" 
          name="port" 
          required 
          rules={[{ required: true, message: '请输入物理端口' }]}
          extra={<div style={{ color: '#ff4d4f', fontSize: 12, marginTop: 4 }}>无SN设备专用。绑定后严禁更换电脑USB插口，以防热插拔漂移导致错烧</div>}
        >
          <Input 
            placeholder="例如：Pot#0003.Hub#0001" 
            suffix={<a style={{ display: 'flex', alignItems: 'center', gap: 4 }}><SyncOutlined /> 获取当前位置</a>}
          />
        </Form.Item>
      )}

      <Form.Item label="启用" name="is_enabled" valuePropName="checked" initialValue={true}>
        <Switch />
      </Form.Item>

      <Form.Item label="描述" name="description">
        <Input.TextArea rows={3} placeholder="烧录器描述和备注信息" />
      </Form.Item>
    </Form>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><h1 style={{ fontSize: 16, margin: 0 }}>烧录器管理</h1><p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理烧录器设备和状态</p></div>
        <Permission code="burner:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>新增烧录器</Button>
        </Permission>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
          <Input placeholder="搜索烧录器名称/状态" style={{ width: 250 }}
            onPressEnter={(e: any) => setParams({ ...params, page: 1, keyword: e.target.value })} />
          <Select placeholder="状态" style={{ width: 120 }} allowClear options={statusFilterOptions}
            onChange={(val) => setParams({ ...params, status: val })} />
          <Button type="primary" onClick={() => setParams({ ...params, page: 1 })}>搜索</Button>
        </div>
        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>
        <div style={{ color: '#999', fontSize: 12, marginBottom: 8 }}>状态说明: 空闲=启用且设备在线; 离线=启用且设备离线; 占用=启用且正在烧录; 禁用=设备被逻辑禁用</div>
        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>

      <Modal title="新增烧录器" open={isCreateModalOpen} onOk={() => createForm.submit()}
        okText="新增" cancelText="取消"
        width={600}
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields(); setCreateStrategy(1); setKeepAdding(false) }}
        footer={(_, { OkBtn, CancelBtn }) => (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Checkbox checked={keepAdding} onChange={(e) => setKeepAdding(e.target.checked)}>继续新增</Checkbox>
            <div>
              <CancelBtn />
              <OkBtn />
            </div>
          </div>
        )}
      >
        {formBody(createForm, createStrategy, setCreateStrategy, handleCreate)}
      </Modal>

      <Modal title="编辑烧录器" open={isEditModalOpen} onOk={() => editForm.submit()}
        okText="保存" cancelText="取消"
        width={600}
        onCancel={() => setIsEditModalOpen(false)}>
        {formBody(editForm, editStrategy, setEditStrategy, handleUpdate)}
      </Modal>
    </div>
  )
}

export default Burner
