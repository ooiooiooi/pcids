import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Select, Popconfirm } from 'antd'
import { PlusOutlined, UserOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { userApi, roleApi } from '../../services/api'
import { Permission } from '../../hooks'

const User: React.FC = () => {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [roles, setRoles] = useState<any[]>([])
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '', role_id: undefined as number | undefined, status: undefined as number | undefined })
  const [editingUser, setEditingUser] = useState<any>(null)
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
      await userApi.create(values)
      message.success('创建成功')
      setIsCreateModalOpen(false)
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
    { title: '角色', dataIndex: 'role', key: 'role', width: 120,
      render: (_: any, record: any) => roles.find((r) => r.id === record.role_id)?.name || '-',
    },
    { title: '用户账号', dataIndex: 'username', key: 'username', width: 150 },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180 },
    {
      title: '操作', key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Permission code="user:edit">
            <Button type="link" onClick={() => { setEditingUser(record); editForm.setFieldsValue(record); setIsEditModalOpen(true) }}>编辑</Button>
          </Permission>
          <Permission code="user:delete">
            <Popconfirm title="确认删除该用户？" onConfirm={() => handleDeleteUser(record.id)}>
              <Button type="link" danger>删除</Button>
            </Popconfirm>
          </Permission>
          <Permission code="user:reset_pwd">
            <Popconfirm title="确认重置该用户密码？" onConfirm={async () => {
              try { await userApi.resetPassword(record.id); message.success('密码已重置为默认密码') }
              catch { /* interceptor handles it */ }
            }}>
              <Button type="link">重置密码</Button>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
    { title: '用户名', dataIndex: 'display_name', key: 'display_name', width: 120,
      render: (_: any, record: any) => record.display_name || record.nick || '-',
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (status: number) => <Tag color={status === 1 ? 'green' : 'red'}>{status === 1 ? '启用' : '禁用'}</Tag>,
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><h1 style={{ fontSize: 16, margin: 0 }}>用户管理</h1><p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理系统用户和权限</p></div>
        <Permission code="user:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>新增用户</Button>
        </Permission>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
          <Input placeholder="请输入用户名" style={{ width: 200 }} prefix={<UserOutlined />}
            onPressEnter={(e: any) => setParams({ ...params, page: 1, keyword: e.target.value })} />
          <Select placeholder="所有角色" style={{ width: 150 }} allowClear
            onChange={(val) => setParams({ ...params, role_id: val })}
            options={roles.map((r) => ({ value: r.id, label: r.name }))} />
          <Select placeholder="所有状态" style={{ width: 120 }} allowClear
            onChange={(val) => setParams({ ...params, status: val })}
            options={[{ value: 1, label: '启用' }, { value: 0, label: '禁用' }]} />
          <Button type="primary" onClick={() => setParams({ ...params, page: 1 })}>搜索</Button>
        </div>
        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>
          共 {total} 条
        </div>
        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>

      {/* Create Modal */}
      <Modal title="新增用户" open={isCreateModalOpen}
        onOk={() => createForm.submit()}
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields() }}>
        <Form form={createForm} layout="vertical" onFinish={handleCreate}>
          <Form.Item label="用户账号" name="username" rules={[{ required: true, message: '请输入用户账号' }]}><Input /></Form.Item>
          <Form.Item label="用户名" name="display_name" rules={[{ required: true, message: '请输入用户名' }]}><Input /></Form.Item>
          <Form.Item label="邮箱" name="email"><Input /></Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}><Input.Password /></Form.Item>
          <Form.Item label="角色" name="role_id"><Select placeholder="请选择角色" options={roles.map((r) => ({ value: r.id, label: r.name }))} /></Form.Item>
        </Form>
      </Modal>

      {/* Edit Modal */}
      <Modal title="编辑用户" open={isEditModalOpen}
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
