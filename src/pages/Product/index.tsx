import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select, Upload, Checkbox, Row, Col } from 'antd'
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
  const [searchName, setSearchName] = useState('')
  const [filterChipType, setFilterChipType] = useState<string | undefined>(undefined)
  const [keepAdding, setKeepAdding] = useState(false)
  const [createImageUrl, setCreateImageUrl] = useState<string>('')
  const [editImageUrl, setEditImageUrl] = useState<string>('')
  const [uploading, setUploading] = useState(false)

  useEffect(() => { fetchProducts() }, [params])

  const fetchProducts = async () => {
    setLoading(true)
    try {
      const res: any = await productApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const formatTime = (val?: string) => (val ? String(val).replace('T', ' ').substring(0, 16) : '-')

  const handleCreate = async (values: any) => {
    try {
      const payload: any = {
        name: String(values.name || '').trim(),
        chip_type: values.chip_type,
        serial_number: String(values.serial_number || '').trim(),
        voltage: values.voltage || undefined,
        temp_range: values.temp_range || undefined,
        interface: values.interface || undefined,
        config_description: values.config_description || undefined,
        usage_description: values.usage_description || undefined,
        board_image: values.board_image,
      }
      await productApi.create(payload)
      message.success('创建成功')
      if (keepAdding) {
        createForm.resetFields()
        setCreateImageUrl('')
      } else {
        setIsCreateModalOpen(false)
        createForm.resetFields()
        setCreateImageUrl('')
      }
      fetchProducts()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      const payload: any = {
        name: String(values.name || '').trim(),
        chip_type: values.chip_type,
        serial_number: values.serial_number,
        voltage: values.voltage || undefined,
        temp_range: values.temp_range || undefined,
        interface: values.interface || undefined,
        config_description: values.config_description || undefined,
        usage_description: values.usage_description || undefined,
        board_image: values.board_image || undefined,
      }
      await productApi.update(editingProduct.id, payload)
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
    { title: '板卡序列号', dataIndex: 'serial_number', key: 'serial_number', width: 160 },
    {
      title: '板卡名称',
      dataIndex: 'name',
      key: 'name',
      width: 240,
      ellipsis: true,
    },
    { title: '芯片类型', dataIndex: 'chip_type', key: 'chip_type', width: 120, render: (type: string) => <Tag color={chipColorMap[type] || 'default'}>{type}</Tag> },
    { title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 180, render: (val: string) => formatTime(val) },
    { title: '修改人', dataIndex: 'modified_by', key: 'modified_by', width: 120, render: (v: string) => v || '-' },
    {
      title: '板卡图片',
      dataIndex: 'board_image',
      key: 'board_image',
      width: 120,
      render: (url: string) => url ? <img src={url} alt="board" style={{ width: 56, height: 36, objectFit: 'cover', borderRadius: 4 }} /> : '-',
    },
    { title: '操作', key: 'action', width: 200,
      render: (_: any, record: any) => (
        <Space>
          <Button type="link" onClick={() => { setDetailProduct(record); setIsDetailOpen(true) }}>详情</Button>
          <Permission code="product:edit">
            <Button
              type="link"
              onClick={() => {
                setEditingProduct(record)
                editForm.setFieldsValue({
                  name: record.name,
                  chip_type: record.chip_type,
                  serial_number: record.serial_number,
                  voltage: record.voltage,
                  temp_range: record.temp_range,
                  interface: record.interface,
                  config_description: record.config_description,
                  usage_description: record.usage_description,
                  board_image: record.board_image,
                })
                setEditImageUrl(record.board_image || '')
                setIsEditModalOpen(true)
              }}
            >
              编辑
            </Button>
          </Permission>
          <Permission code="product:delete">
            <Button type="link" danger onClick={() => handleDelete(record.id)}>删除</Button>
          </Permission>
        </Space>
      ),
    },
  ]

  const uploadTo = async (file: File) => {
    setUploading(true)
    try {
      const res: any = await productApi.uploadImage(file)
      if (res.code !== 0) throw new Error(res.message || '上传失败')
      return res?.data?.url as string
    } finally {
      setUploading(false)
    }
  }

  const formBody = (form: any, imageUrl: string, setImageUrl: (v: string) => void, onFinish: (v: any) => void) => (
    <Form form={form} layout="vertical" onFinish={onFinish}>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="板卡名称" name="name" rules={[{ required: true, message: '请输入板卡名称' }]}>
            <Input placeholder="请输入板卡名称" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="芯片类型" name="chip_type" rules={[{ required: true, message: '请选择芯片类型' }]}>
            <Select placeholder="请选择芯片类型" options={chipTypes.map((t) => ({ value: t, label: t }))} />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="序列号" name="serial_number" rules={[{ required: true, message: '请输入序列号' }]}>
            <Input placeholder="请输入序列号" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="工作电压" name="voltage">
            <Input placeholder="请输入工作电压" />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="工作温度范围" name="temp_range">
            <Input placeholder="请输入工作温度范围" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="通信接口" name="interface">
            <Input placeholder="请输入通信接口" />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item label="配置说明" name="config_description">
        <Input.TextArea placeholder="请输入内容" autoSize={{ minRows: 3, maxRows: 3 }} />
      </Form.Item>

      <Form.Item label="使用说明" name="usage_description">
        <Input.TextArea placeholder="请输入内容" autoSize={{ minRows: 3, maxRows: 3 }} />
      </Form.Item>

      <Form.Item
        label="板卡图片"
        name="board_image"
        rules={[{ required: true, message: '请上传板卡图片' }]}
      >
        <div>
          <Upload
            accept=".jpg,.jpeg,.png"
            showUploadList={false}
            beforeUpload={(file) => {
              const ok = file.type === 'image/jpeg' || file.type === 'image/png'
              if (!ok) message.error('只能上传 jpg/png 文件')
              return ok || Upload.LIST_IGNORE
            }}
            customRequest={async (options: any) => {
              try {
                const url = await uploadTo(options.file as File)
                setImageUrl(url)
                form.setFieldValue('board_image', url)
                options.onSuccess?.({ url })
              } catch (e: any) {
                options.onError?.(e)
                message.error(e?.response?.data?.detail || e?.message || '上传失败')
              }
            }}
          >
            <Button type="link" disabled={uploading}>上传图片</Button>
            <span style={{ marginLeft: 8, color: 'rgba(0,0,0,0.45)' }}>只能上传 jpg/png 文件</span>
          </Upload>
          {imageUrl ? (
            <div style={{ marginTop: 8 }}>
              <img src={imageUrl} alt="board" style={{ width: 120, height: 80, objectFit: 'cover', borderRadius: 6 }} />
            </div>
          ) : null}
        </div>
      </Form.Item>
    </Form>
  )

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h1 style={{ fontSize: 16, margin: 0 }}>产品管理</h1>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <Permission code="product:add">
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>新增板卡</Button>
          </Permission>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ color: 'rgba(0,0,0,0.65)' }}>芯片类型</span>
            <Select
              placeholder="请选择芯片"
              style={{ width: 180 }}
              allowClear
              value={filterChipType}
              options={chipTypes.map((t) => ({ value: t, label: t }))}
              onChange={(val) => setFilterChipType(val)}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <Input
              placeholder="请输入板卡名称"
              style={{ width: 260 }}
              allowClear
              value={searchName}
              onChange={(e) => setSearchName(e.target.value)}
              onPressEnter={() => setParams({ ...params, page: 1, keyword: searchName, chip_type: filterChipType })}
            />
            <Button type="primary" onClick={() => setParams({ ...params, page: 1, keyword: searchName, chip_type: filterChipType })}>搜索</Button>
          </div>
        </div>
        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{
            total,
            pageSize: params.page_size,
            current: params.page,
            showSizeChanger: false,
            showQuickJumper: false,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (page) => setParams({ ...params, page }),
          }} />
      </Card>

      <Modal
        title="新增板卡"
        open={isCreateModalOpen}
        width={900}
        onOk={() => createForm.submit()}
        okText="新增"
        cancelText="取消"
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields(); setCreateImageUrl(''); setKeepAdding(false) }}
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
        {formBody(createForm, createImageUrl, setCreateImageUrl, handleCreate)}
      </Modal>

      <Modal
        title="编辑板卡"
        open={isEditModalOpen}
        width={900}
        onOk={() => editForm.submit()}
        okText="保存"
        cancelText="取消"
        onCancel={() => { setIsEditModalOpen(false); setEditImageUrl('') }}
      >
        {formBody(editForm, editImageUrl, setEditImageUrl, handleUpdate)}
      </Modal>

      <Modal title="产品详情" open={isDetailOpen} onCancel={() => setIsDetailOpen(false)} footer={<Button type="primary" onClick={() => setIsDetailOpen(false)}>关闭</Button>} width={600}>
        {detailProduct && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20, paddingTop: 10 }}>
            {detailProduct.board_image ? (
              <div style={{ textAlign: 'center' }}>
                <img src={detailProduct.board_image} alt="board" style={{ maxWidth: '100%', maxHeight: 240, objectFit: 'contain', borderRadius: 8 }} />
              </div>
            ) : (
              <div style={{ height: 160, backgroundColor: '#f5f5f5', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999' }}>
                暂无图片
              </div>
            )}

            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 18, fontWeight: 500 }}>{detailProduct.name}</span>
              <Tag color={chipColorMap[detailProduct.chip_type] || 'blue'} style={{ margin: 0, borderRadius: 10 }}>
                {detailProduct.chip_type}
              </Tag>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px 24px' }}>
              <div style={{ display: 'flex' }}>
                <span style={{ color: '#666', width: 80, flexShrink: 0 }}>序列号</span>
                <span style={{ color: '#333', wordBreak: 'break-all' }}>{detailProduct.serial_number || '-'}</span>
              </div>
              <div style={{ display: 'flex' }}>
                <span style={{ color: '#666', width: 80, flexShrink: 0 }}>通信接口</span>
                <span style={{ color: '#333', wordBreak: 'break-all' }}>{detailProduct.interface || '-'}</span>
              </div>
              <div style={{ display: 'flex' }}>
                <span style={{ color: '#666', width: 80, flexShrink: 0 }}>工作电压</span>
                <span style={{ color: '#333', wordBreak: 'break-all' }}>{detailProduct.voltage || '-'}</span>
              </div>
              <div style={{ display: 'flex' }}>
                <span style={{ color: '#666', width: 80, flexShrink: 0 }}>工作温度范围</span>
                <span style={{ color: '#333', wordBreak: 'break-all' }}>{detailProduct.temp_range || '-'}</span>
              </div>
            </div>

            <div style={{ display: 'flex' }}>
              <span style={{ color: '#666', width: 80, flexShrink: 0 }}>配置说明</span>
              <span style={{ color: '#333', flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{detailProduct.config_description || '-'}</span>
            </div>
            <div style={{ display: 'flex' }}>
              <span style={{ color: '#666', width: 80, flexShrink: 0 }}>使用说明</span>
              <span style={{ color: '#333', flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{detailProduct.usage_description || '-'}</span>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

export default Product
