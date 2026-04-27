import { Table, Button, Space, Input, Modal, Form, message, Tag, Select, Popconfirm, Switch, Checkbox, Typography, Row, Col } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { userApi, roleApi } from '../../services/api'
import { Permission } from '../../hooks'
import dayjs from 'dayjs'

const { Title } = Typography

const User: React.FC = () => {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [roles, setRoles] = useState<any[]>([])
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '', role_id: undefined as number | undefined, status: undefined as number | undefined, sort_field: 'created_at', sort_order: 'desc' })
  const [editingUser, setEditingUser] = useState<any>(null)
  const [continueAdd, setContinueAdd] = useState(true)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()

  useEffect(() => { fetchRoles() }, [])
  useEffect(() => { fetchUsers() }, [params])

  const fetchUsers = async () => {
    setLoading(true)
    try {
      const res: any = await userApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const fetchRoles = async () => {
    try {
      const res: any = await roleApi.getList()
      if (res.code === 0) setRoles(res.data || [])
    } catch { /* ignore */ }
  }

  const handleCreate = async (values: any) => {
    try {
      const payload = {
        ...values,
        status: values.status ? 1 : 0
      }
      await userApi.create(payload)
      message.success('创建成功')
      if (!continueAdd) {
        setIsCreateModalOpen(false)
      }
      createForm.resetFields()
      fetchUsers()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      await userApi.update(editingUser.id, values)
      message.success('更新成功')
      setIsEditModalOpen(false)
      fetchUsers()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDeleteUser = async (id: number) => {
    try {
      await userApi.delete(id)
      message.success('删除成功')
      fetchUsers()
    } catch { /* interceptor handles it */ }
  }

  const columns = [
    { title: '用户账号', dataIndex: 'username', key: 'username', width: 180 },
    { 
      title: '用户名', 
      dataIndex: 'display_name', 
      key: 'display_name', 
      width: 150,
      render: (_: any, record: any) => {
        const name = record.display_name || record.username || '-'
        const displayAvatar = name.substring(Math.max(0, name.length - 2))
        return (
          <Space>
            <div style={{ width: 24, height: 24, borderRadius: '50%', backgroundColor: '#4f46e5', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12 }}>
              {displayAvatar}
            </div>
            {name}
          </Space>
        )
      },
    },
    { 
      title: '角色', 
      dataIndex: 'role', 
      key: 'role', 
      width: 120,
      render: (_: any, record: any) => {
        const roleName = roles.find((r) => r.id === record.role_id)?.name
        if (!roleName) return '-'
        const color = roleName.includes('管理') ? 'magenta' : 'blue'
        return <Tag color={color} style={{ borderRadius: 10 }}>{roleName}</Tag>
      },
    },
    { 
      title: '创建时间', 
      dataIndex: 'created_at', 
      key: 'created_at', 
      width: 180,
      sorter: true,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-' 
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (status: number) => <Tag color={status === 1 ? 'success' : 'warning'} style={{ borderRadius: 10 }}>{status === 1 ? '启用' : '禁用'}</Tag>,
    },
    {
      title: '操作', key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Permission code="user:edit">
            <a onClick={() => { setEditingUser(record); editForm.setFieldsValue(record); setIsEditModalOpen(true) }}>编辑</a>
          </Permission>
          <Permission code="user:reset_pwd">
            <Popconfirm title="确认重置该用户密码？" onConfirm={async () => {
              try { await userApi.resetPassword(record.id); message.success('密码已重置为默认密码') }
              catch { /* interceptor handles it */ }
            }}>
              <a>重置密码</a>
            </Popconfirm>
          </Permission>
          <Permission code="user:delete">
            <Popconfirm title="确认删除该用户？" onConfirm={() => handleDeleteUser(record.id)}>
              <a style={{ color: '#ff4d4f' }}>删除</a>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: '0 24px 24px', background: '#fff', minHeight: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 0', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>用户管理</Title>
        <Permission code="user:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>新增用户</Button>
        </Permission>
      </div>

      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16, gap: 16 }}>
        <Select defaultValue="所有角色" style={{ width: 180 }} allowClear
          onChange={(val) => setParams({ ...params, role_id: val ? Number(val) : undefined })}
          options={roles.map((r) => ({ value: r.id, label: r.name }))} />
        <Select defaultValue="所有状态" style={{ width: 180 }} allowClear
          onChange={(val) => setParams({ ...params, status: val !== undefined ? Number(val) : undefined })}
          options={[{ value: 1, label: '启用' }, { value: 0, label: '禁用' }]} />
        <Input prefix={<SearchOutlined />} placeholder="请输入用户名" style={{ width: 240 }}
          onPressEnter={(e: any) => setParams({ ...params, page: 1, keyword: e.target.value })} 
          onChange={(e) => setParams({ ...params, keyword: e.target.value })}
        />
      </div>

      <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13, textAlign: 'center' }}>
        共 {total} 条
      </div>
      <Table 
        columns={columns} 
        dataSource={dataSource} 
        rowKey="id" 
        loading={loading}
        onChange={(pagination, _filters, sorter: any) => {
          setParams({
            ...params,
            page: pagination.current || 1,
            page_size: pagination.pageSize || 10,
            sort_field: sorter.field || 'created_at',
            sort_order: sorter.order === 'ascend' ? 'asc' : 'desc'
          })
        }}
        pagination={{ 
          total, 
          pageSize: params.page_size, 
          current: params.page,
          showTotal: (t) => `共 ${t} 条`
        }} 
      />

      {/* Create Modal */}
      <Modal title="新增用户" open={isCreateModalOpen}
        width={500}
        maskClosable={false}
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields() }}
        footer={[
          <div key="footer-wrapper" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Checkbox checked={continueAdd} onChange={e => setContinueAdd(e.target.checked)}>继续新增</Checkbox>
            <Space>
              <Button onClick={() => { setIsCreateModalOpen(false); createForm.resetFields() }}>取消</Button>
              <Button type="primary" onClick={() => createForm.submit()}>新增</Button>
            </Space>
          </div>
        ]}
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreate} initialValues={{ status: false }}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item label="用户名" name="display_name" rules={[{ required: true, message: '请输入用户名' }]}><Input placeholder="请输入用户名" /></Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="用户账号" name="username" rules={[{ required: true, message: '请输入用户账号' }]}><Input placeholder="请输入用户账号" /></Form.Item>
            </Col>
          </Row>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}><Input.Password placeholder="请输入密码" /></Form.Item>
          <Form.Item label="角色" name="role_id"><Select placeholder="请选择角色" options={roles.map((r) => ({ value: r.id, label: r.name }))} /></Form.Item>
          <Form.Item label="状态" name="status" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
          <Form.Item label="备注" name="remark"><Input.TextArea rows={3} placeholder="请输入备注信息" /></Form.Item>
        </Form>
      </Modal>

      {/* Edit Modal */}
      <Modal title="编辑用户" open={isEditModalOpen}
        maskClosable={false}
        onOk={() => editForm.submit()}
        onCancel={() => setIsEditModalOpen(false)}>
        <Form form={editForm} layout="vertical" onFinish={handleUpdate}>
          <Form.Item label="用户名" name="display_name"><Input /></Form.Item>
          <Form.Item label="邮箱" name="email"><Input /></Form.Item>
          <Form.Item label="角色" name="role_id"><Select placeholder="请选择角色" options={roles.map((r) => ({ value: r.id, label: r.name }))} /></Form.Item>
          <Form.Item label="状态" name="status"><Select options={[{ value: 1, label: '启用' }, { value: 0, label: '禁用' }]} /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

export default User
