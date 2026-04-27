import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select, Switch, Checkbox, Row, Col } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { scriptApi, productApi, burnerApi } from '../../services/api'
import { Permission } from '../../hooks'

const IDE_OPTIONS = [
  'Code Composer Studio',
  'IAR Embedded Workbench',
  'Keil uVision',
  'MPLAB',
  'STM32CubeIDE',
  'Vitis',
]

const SCRIPT_TYPES = [
  'PowerShell',
  'python',
  'shell',
  'TCL',
  'IAR Embedded Workbench',
  'Code Composer Studio',
  'Keil uVision',
]

const TYPE_COLORS: Record<string, string> = {
  PowerShell: 'blue',
  python: 'green',
  shell: 'orange',
  TCL: 'purple',
  'IAR Embedded Workbench': 'cyan',
  'Code Composer Studio': 'magenta',
  'Keil uVision': 'red',
}

const STATUS_MAP: Record<number, { label: string; color: string }> = {
  0: { label: '空闲', color: 'green' },
  1: { label: '占用', color: 'processing' },
  2: { label: '离线', color: 'default' },
}

const Script: React.FC = () => {
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({
    page: 1,
    page_size: 10,
    keyword: '',
    script_type: undefined as string | undefined,
  })
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()
  const [editingId, setEditingId] = useState<number | null>(null)
  const [isBasicEditOpen, setIsBasicEditOpen] = useState(false)
  const [basicEditForm] = Form.useForm()
  const [editingBasicId, setEditingBasicId] = useState<number | null>(null)
  const [keepAdding, setKeepAdding] = useState(false)

  const [products, setProducts] = useState<any[]>([])
  const [burners, setBurners] = useState<any[]>([])

  useEffect(() => {
    fetchScripts()
  }, [params])

  useEffect(() => {
    fetchDependencies()
  }, [])

  const fetchDependencies = async () => {
    try {
      const [prodRes, burnerRes]: any = await Promise.all([
        productApi.getList({ page: 1, page_size: 1000 }),
        burnerApi.getList({ page: 1, page_size: 1000 })
      ])
      
      // Update logic: directly use data array, ensure we handle missing res gracefully
      const productsData = prodRes?.data || []
      const burnersData = burnerRes?.data || []
      
      setProducts(productsData)
      setBurners(burnersData)
    } catch {
      // ignore
    }
  }

  const fetchScripts = async () => {
    setLoading(true)
    try {
      const res: any = await scriptApi.getList(params)
      if (res.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
      }
    } catch {
      // error handled by interceptor
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = async (values: any) => {
    try {
      const payload = {
        ...values,
        status: values.status ? 0 : 2 // 0: 启用(空闲), 2: 禁用(离线)
      }
      const res: any = await scriptApi.create(payload)
      if (res.code === 0) {
        message.success('创建成功')
        if (!keepAdding) {
          setIsModalOpen(false)
        }
        form.resetFields()
        fetchScripts()
      }
    } catch {
      // error handled by interceptor
    }
  }

  const handleEdit = async (values: any) => {
    if (!editingId) return
    try {
      const res: any = await scriptApi.update(editingId, values)
      if (res.code === 0) {
        message.success('更新成功')
        setIsEditOpen(false)
        editForm.resetFields()
        fetchScripts()
      }
    } catch {
      // error handled by interceptor
    }
  }

  const handleDelete = async (id: number) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除此脚本吗？',
      onOk: async () => {
        try {
          const res: any = await scriptApi.delete(id)
          if (res.code === 0) {
            message.success('删除成功')
            fetchScripts()
          }
        } catch {
          // error handled by interceptor
        }
      },
    })
  }

  const openEdit = async (record: any) => {
    setEditingId(record.id)
    try {
      const res: any = await scriptApi.getContent(record.id)
      if (res.code === 0) {
        editForm.setFieldsValue({ content: res.data?.content || '' })
      }
    } catch {
      // ignore
    }
    setIsEditOpen(true)
  }

  const handleBasicEdit = async (values: any) => {
    if (!editingBasicId) return
    try {
      const payload = {
        ...values,
        status: values.status ? 0 : 2
      }
      const res: any = await scriptApi.update(editingBasicId, payload)
      if (res.code === 0) {
        message.success('更新成功')
        setIsBasicEditOpen(false)
        basicEditForm.resetFields()
        fetchScripts()
      }
    } catch {
      // error handled by interceptor
    }
  }

  const openBasicEdit = (record: any) => {
    setEditingBasicId(record.id)
    basicEditForm.setFieldsValue({
      name: record.name,
      type: record.type,
      associated_ide: record.associated_ide,
      associated_board: record.associated_board,
      associated_burner: record.associated_burner,
      status: record.status !== 2, // 2 is disabled/offline
      description: record.description,
    })
    setIsBasicEditOpen(true)
  }

  const handleToggleStatus = async (record: any) => {
    try {
      const newStatus = record.status === 0 ? 2 : 0
      const res: any = await scriptApi.update(record.id, { status: newStatus })
      if (res.code === 0) {
        message.success(newStatus === 0 ? '启用成功' : '禁用成功')
        fetchScripts()
      }
    } catch {
      // error handled by interceptor
    }
  }

  const formBody = (form: any, isCreate: boolean, onFinish: (values: any) => void) => (
    <Form form={form} layout="horizontal" labelCol={{ span: 6 }} wrapperCol={{ span: 18 }} onFinish={onFinish}>
      <Form.Item
        label="脚本名称"
        name="name"
        rules={[{ required: true, message: '请输入脚本名称' }]}
        labelCol={{ span: 3 }}
        wrapperCol={{ span: 21 }}
      >
        <Input placeholder="请输入脚本名称" />
      </Form.Item>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            label="脚本类型"
            name="type"
            rules={[{ required: true, message: '请选择脚本类型' }]}
          >
            <Select
              placeholder="请选择脚本类型"
              options={SCRIPT_TYPES.map((t) => ({ value: t, label: t }))}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="关联烧录器" name="associated_burner">
            <Select
              placeholder="请选择关联烧录器"
              allowClear
              options={burners.map((b) => ({ value: b.name, label: b.name }))}
              onDropdownVisibleChange={(open) => {
                if (open && burners.length === 0) fetchDependencies()
              }}
            />
          </Form.Item>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="关联IDE" name="associated_ide">
            <Select
              placeholder="请选择关联IDE"
              allowClear
              options={IDE_OPTIONS.map((ide) => ({ value: ide, label: ide }))}
            />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="关联板卡" name="associated_board">
            <Select
              placeholder="请选择关联板卡"
              allowClear
              options={products.map((p) => ({ value: p.name, label: p.name }))}
              onDropdownVisibleChange={(open) => {
                if (open && products.length === 0) fetchDependencies()
              }}
            />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item label="状态" name="status" valuePropName="checked" initialValue={true} labelCol={{ span: 3 }} wrapperCol={{ span: 21 }}>
        <Switch checkedChildren="启用" unCheckedChildren="禁用" />
      </Form.Item>

      <Form.Item label="描述" name="description" labelCol={{ span: 3 }} wrapperCol={{ span: 21 }}>
        <Input.TextArea rows={3} placeholder="脚本描述和备注信息" />
      </Form.Item>

      {isCreate && (
        <Form.Item label="脚本内容" name="content" labelCol={{ span: 3 }} wrapperCol={{ span: 21 }}>
          <Input.TextArea rows={8} placeholder="请输入脚本内容" />
        </Form.Item>
      )}
    </Form>
  )

  const columns = [
    {
      title: '脚本名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '关联IDE',
      dataIndex: 'associated_ide',
      key: 'associated_ide',
      render: (value: string) =>
        value ? <Tag>{value}</Tag> : null,
    },
    {
      title: 'IDE名称',
      dataIndex: 'ide_name',
      key: 'ide_name',
    },
    {
      title: '关联板卡',
      dataIndex: 'associated_board',
      key: 'associated_board',
    },
    {
      title: '脚本类型',
      dataIndex: 'type',
      key: 'type',
      render: (type: string) => (
        <Tag color={TYPE_COLORS[type] || 'default'}>{type}</Tag>
      ),
    },
    {
      title: '关联烧录器',
      dataIndex: 'associated_burner',
      key: 'associated_burner',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: number) => {
        const info = STATUS_MAP[status] || STATUS_MAP[2]
        return <Tag color={info.color}>{info.label}</Tag>
      },
    },
    {
      title: '修改时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
    },
    {
      title: '修改人',
      dataIndex: 'modified_by',
      key: 'modified_by',
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Permission code="script:edit">
            <Switch
              checked={record.status === 0}
              checkedChildren="启用"
              unCheckedChildren="禁用"
              onChange={() => handleToggleStatus(record)}
            />
          </Permission>
          <Permission code="script:edit">
            <Button type="link" icon={<EditOutlined />} onClick={() => openEdit(record)}>
              脚本编辑
            </Button>
          </Permission>
          <Permission code="script:edit">
            <Button type="link" icon={<PlusOutlined />} onClick={() => openBasicEdit(record)}>
              编辑
            </Button>
          </Permission>
          <Permission code="script:delete">
            <Button type="link" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record.id)}>
              删除
            </Button>
          </Permission>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 16, margin: 0 }}>脚本管理</h1>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理烧录脚本和执行逻辑</p>
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          <Permission code="script:add">
            <Button icon={<PlusOutlined />} onClick={() => setIsModalOpen(true)}>
              新增脚本
            </Button>
          </Permission>
        </div>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
          <Input
            placeholder="请输入脚本名称"
            style={{ width: 200 }}
            value={params.keyword}
            onChange={(e) => setParams({ ...params, keyword: e.target.value })}
            onPressEnter={() => setParams({ ...params, page: 1 })}
          />
          <Select
            placeholder="请选择类型"
            style={{ width: 200 }}
            allowClear
            options={SCRIPT_TYPES.map((t) => ({ value: t, label: t }))}
            onChange={(val) => setParams({ ...params, script_type: val })}
          />
          <Button type="primary" onClick={() => setParams({ ...params, page: 1 })}>
            搜索
          </Button>
        </div>

        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>

        <Table
          columns={columns}
          dataSource={dataSource}
          rowKey="id"
          loading={loading}
          pagination={{
            total,
            pageSize: params.page_size,
            current: params.page,
            onChange: (page) => setParams({ ...params, page }),
          }}
        />
      </Card>

      <Modal
        title="新增脚本"
        open={isModalOpen}
        onOk={() => form.submit()}
        onCancel={() => {
          setIsModalOpen(false)
          form.resetFields()
          setKeepAdding(false)
        }}
        width={700}
        okText="新增" cancelText="取消"
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
        {formBody(form, true, handleCreate)}
      </Modal>

      <Modal
        title="脚本编辑"
        open={isEditOpen}
        onOk={() => editForm.submit()}
        onCancel={() => {
          setIsEditOpen(false)
          editForm.resetFields()
        }}
        width={800}
        okText="保存"
        cancelText="取消"
        footer={(_, { OkBtn, CancelBtn }) => (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Button onClick={() => message.info('测试功能开发中')}>测试</Button>
            <div>
              <CancelBtn />
              <OkBtn />
            </div>
          </div>
        )}
      >
        <Form form={editForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item name="content" style={{ marginBottom: 0 }}>
            <Input.TextArea
              rows={20}
              placeholder="请输入脚本内容"
              style={{
                backgroundColor: '#282c34',
                color: '#abb2bf',
                fontFamily: 'Menlo, Monaco, "Courier New", monospace',
                border: 'none',
                padding: '16px'
              }}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑脚本"
        open={isBasicEditOpen}
        onOk={() => basicEditForm.submit()}
        onCancel={() => {
          setIsBasicEditOpen(false)
          basicEditForm.resetFields()
        }}
        width={700}
        okText="保存" cancelText="取消"
      >
        {formBody(basicEditForm, false, handleBasicEdit)}
      </Modal>
    </div>
  )
}

export default Script
