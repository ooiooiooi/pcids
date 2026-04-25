import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { productApi } from '../../services/api'
import { Permission } from '../../hooks'

const Product: React.FC = () => {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '', chip_type: undefined as string | undefined })
  const [editingProduct, setEditingProduct] = useState<any>(null)
  const [detailProduct, setDetailProduct] = useState<any>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()

  useEffect(() => { fetchProducts() }, [params])

  const fetchProducts = async () => {
    setLoading(true)
    try {
      const res: any = await productApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const handleCreate = async (values: any) => {
    try {
      await productApi.create(values)
      message.success('创建成功')
      setIsCreateModalOpen(false)
      createForm.resetFields()
      fetchProducts()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      await productApi.update(editingProduct.id, values)
      message.success('更新成功')
      setIsEditModalOpen(false)
      fetchProducts()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await productApi.delete(id)
      message.success('删除成功')
      fetchProducts()
    } catch { /* ignore */ }
  }

  const chipTypes = ['ARM', 'PIC', 'DSP', 'FPGA', 'Altera-CPLD']

  const chipColorMap: Record<string, string> = {
    ARM: 'blue', PIC: 'green', FPGA: 'purple', DSP: 'orange', 'Altera-CPLD': 'cyan',
  }

  const columns = [
    { title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 160, render: (val: string) => val ? val.replace('T', ' ').substring(0, 16) : '-' },
    { title: '芯片类型', dataIndex: 'chip_type', key: 'chip_type', width: 100, render: (type: string) => <Tag color={chipColorMap[type] || 'default'}>{type}</Tag> },
    { title: '板卡序列号', dataIndex: 'serial_number', key: 'serial_number', width: 140 },
    { title: '修改人', dataIndex: 'modified_by', key: 'modified_by', width: 100 },
    { title: '操作', key: 'action', width: 180,
      render: (_: any, record: any) => (
        <Space>
          <Button type="link" onClick={() => { setDetailProduct(record); setIsDetailOpen(true) }}>详情</Button>
          <Permission code="product:edit">
            <Button type="link" onClick={() => { setEditingProduct(record); editForm.setFieldsValue(record); setIsEditModalOpen(true) }}>编辑</Button>
          </Permission>
          <Permission code="product:delete">
            <Button type="link" danger onClick={() => handleDelete(record.id)}>删除</Button>
          </Permission>
        </Space>
      ),
    },
    { title: '板卡图片', dataIndex: 'board_image', key: 'board_image', width: 80, render: (url: string) => url ? <img src={url} alt="board" style={{width:56,height:36,objectFit:'cover',borderRadius:4}} /> : '-' },
    { title: '板卡名称', dataIndex: 'name', key: 'name' },
  ]

  const createFormJSX = (
    <Form form={createForm} layout="vertical" onFinish={handleCreate}>
      <Form.Item label="板卡名称" name="name" rules={[{ required: true, message: '请输入板卡名称' }]}><Input /></Form.Item>
      <Form.Item label="芯片类型" name="chip_type" rules={[{ required: true, message: '请选择芯片类型' }]}>
        <Select placeholder="请选择芯片类型" options={chipTypes.map((t) => ({ value: t, label: t }))} />
      </Form.Item>
      <Form.Item label="板卡序列号" name="serial_number"><Input /></Form.Item>
      <Form.Item label="板卡图片" name="board_image"><Input placeholder="请输入图片URL" /></Form.Item>
      <Form.Item label="修改人" name="modified_by"><Input /></Form.Item>
    </Form>
  )

  const editFormJSX = (
    <Form form={editForm} layout="vertical" onFinish={handleUpdate}>
      <Form.Item label="板卡名称" name="name" rules={[{ required: true, message: '请输入板卡名称' }]}><Input /></Form.Item>
      <Form.Item label="芯片类型" name="chip_type" rules={[{ required: true, message: '请选择芯片类型' }]}>
        <Select placeholder="请选择芯片类型" options={chipTypes.map((t) => ({ value: t, label: t }))} />
      </Form.Item>
      <Form.Item label="板卡序列号" name="serial_number"><Input /></Form.Item>
      <Form.Item label="板卡图片" name="board_image"><Input placeholder="请输入图片URL" /></Form.Item>
      <Form.Item label="修改人" name="modified_by"><Input /></Form.Item>
    </Form>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><h1 style={{ fontSize: 16, margin: 0 }}>产品管理</h1><p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理芯片型号和产品配置</p></div>
        <Permission code="product:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>新增板卡</Button>
        </Permission>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
          <Input placeholder="请输入板卡名称" style={{ width: 200 }}
            onPressEnter={(e: any) => setParams({ ...params, page: 1, keyword: e.target.value })} />
          <Select placeholder="芯片类型" style={{ width: 150 }} allowClear
            options={[{ value: '', label: '全部' }, ...chipTypes.map((t) => ({ value: t, label: t }))]}
            onChange={(val) => setParams({ ...params, chip_type: val || undefined })} />
          <Button type="primary" onClick={() => setParams({ ...params, page: 1 })}>搜索</Button>
        </div>
        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>
        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>

      <Modal title="新增板卡" open={isCreateModalOpen} onOk={() => createForm.submit()}
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields() }}>
        {createFormJSX}
      </Modal>

      <Modal title="编辑板卡" open={isEditModalOpen} onOk={() => editForm.submit()}
        onCancel={() => setIsEditModalOpen(false)}>
        {editFormJSX}
      </Modal>

      <Modal title="板卡详情" open={isDetailOpen} onCancel={() => setIsDetailOpen(false)} footer={null}>
        {detailProduct && (
          <div>
            <p><strong>板卡名称：</strong>{detailProduct.name}</p>
            <p><strong>芯片类型：</strong>{detailProduct.chip_type}</p>
            <p><strong>板卡序列号：</strong>{detailProduct.serial_number || '-'}</p>
            <p><strong>修改时间：</strong>{detailProduct.updated_at?.replace('T', ' ').substring(0, 19) || '-'}</p>
            <p><strong>修改人：</strong>{detailProduct.modified_by || '-'}</p>
          </div>
        )}
      </Modal>
    </div>
  )
}

export default Product
