import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
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
      await burnerApi.create(values)
      message.success('创建成功')
      setIsCreateModalOpen(false)
      createForm.resetFields()
      fetchBurners()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      await burnerApi.update(editingBurner.id, values)
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

  const statusEditOptions = [
    { value: 0, label: '空闲' },
    { value: 1, label: '离线' },
    { value: 2, label: '占用' },
    { value: 3, label: '禁用' },
  ]

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: 'success', text: '空闲' },
    1: { color: 'default', text: '离线' },
    2: { color: 'processing', text: '占用' },
    3: { color: 'error', text: '禁用' },
  }

  const columns = [
    { title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 160 },
    { title: '烧录器名称', dataIndex: 'name', key: 'name', width: 140 },
    { title: '修改人', dataIndex: 'modified_by', key: 'modified_by', width: 100 },
    {
      title: '操作', key: 'action', width: 140,
      render: (_: any, record: any) => (
        <Space>
          <Permission code="burner:edit">
            <Button type="link" onClick={() => { setEditingBurner(record); editForm.setFieldsValue(record); setIsEditModalOpen(true) }}>编辑</Button>
          </Permission>
          <Permission code="burner:delete">
            <Button type="link" danger onClick={() => handleDelete(record.id)}>删除</Button>
          </Permission>
        </Space>
      ),
    },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (s: number) => <Tag color={statusMap[s]?.color}>{statusMap[s]?.text}</Tag> },
  ]

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
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields() }}>
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item label="烧录器名称" name="name" rules={[{ required: true, message: '请输入烧录器名称' }]}><Input /></Form.Item>
          <Form.Item label="类型" name="type" rules={[{ required: true, message: '请选择烧录器类型' }]}>
            <Select placeholder="请选择类型" options={[
              { value: 'J-Link', label: 'J-Link' }, { value: 'ST-Link', label: 'ST-Link' },
              { value: 'DAPLink', label: 'DAPLink' }, { value: 'Other', label: 'Other' },
            ]} />
          </Form.Item>
          <Form.Item label="SN" name="sn"><Input placeholder="例如：37FF71064E573436" /></Form.Item>
          <Form.Item label="物理端口" name="port"><Input placeholder="例如：USB 插槽 1" /></Form.Item>
          <Form.Item label="描述" name="description"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="编辑烧录器" open={isEditModalOpen} onOk={() => editForm.submit()}
        onCancel={() => setIsEditModalOpen(false)}>
        <Form form={editForm} layout="vertical" onFinish={handleUpdate}>
          <Form.Item label="烧录器名称" name="name" rules={[{ required: true, message: '请输入烧录器名称' }]}><Input /></Form.Item>
          <Form.Item label="类型" name="type" rules={[{ required: true, message: '请选择烧录器类型' }]}>
            <Select placeholder="请选择类型" options={[
              { value: 'J-Link', label: 'J-Link' }, { value: 'ST-Link', label: 'ST-Link' },
              { value: 'DAPLink', label: 'DAPLink' }, { value: 'Other', label: 'Other' },
            ]} />
          </Form.Item>
          <Form.Item label="SN" name="sn"><Input /></Form.Item>
          <Form.Item label="物理端口" name="port"><Input /></Form.Item>
          <Form.Item label="状态" name="status">
            <Select options={statusEditOptions} />
          </Form.Item>
          <Form.Item label="描述" name="description"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default Burner
