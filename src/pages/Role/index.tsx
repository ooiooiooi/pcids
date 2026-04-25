import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Popconfirm } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { roleApi } from '../../services/api'
import { permissionApi } from '../../services/permission'
import { Permission } from '../../hooks'

const Role: React.FC = () => {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '' })
  const [editingRole, setEditingRole] = useState<any>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [selectedPermissionIds, setSelectedPermissionIds] = useState<number[]>([])
  const [allPermissions, setAllPermissions] = useState<any[]>([])
  const [searchName, setSearchName] = useState('')

  useEffect(() => { fetchRoles() }, [params])
  useEffect(() => { fetchPermissions() }, [])

  const fetchPermissions = async () => {
    try {
      const res: any = await permissionApi.getPermissions()
      if (res.code === 0) setAllPermissions(res.data || [])
    } catch { /* ignore */ }
  }

  const fetchRoles = async () => {
    setLoading(true)
    try {
      const res: any = await roleApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const handleCreate = async (values: any) => {
    try {
      await roleApi.create({ ...values, permission_ids: selectedPermissionIds })
      message.success('创建成功')
      setIsCreateModalOpen(false)
      createForm.resetFields()
      setSelectedPermissionIds([])
      fetchRoles()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      await roleApi.update(editingRole.id, { ...values, permission_ids: selectedPermissionIds })
      message.success('更新成功')
      setIsEditModalOpen(false)
      fetchRoles()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await roleApi.delete(id)
      message.success('删除成功')
      fetchRoles()
    } catch { /* ignore */ }
  }

  const groupedPermissions: Record<string, any[]> = {}
  allPermissions.forEach((p: any) => {
    const module = p.code?.split(':')[0] || 'other'
    if (!groupedPermissions[module]) groupedPermissions[module] = []
    groupedPermissions[module].push(p)
  })

  const columns = [
    { title: '角色名称', dataIndex: 'name', key: 'name', width: 200 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (status: number) => (
        <Tag color={status === 1 ? 'green' : 'red'}>{status === 1 ? '启用' : '禁用'}</Tag>
      ),
    },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180 },
    {
      title: '操作', key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Permission code="role:edit">
            <Button type="link" onClick={() => {
              setEditingRole(record)
              editForm.setFieldsValue({ name: record.name, description: record.description })
              setSelectedPermissionIds(record.permission_ids || [])
              setIsEditModalOpen(true)
            }}>编辑</Button>
          </Permission>
          <Permission code="role:delete">
            <Popconfirm title="确认删除" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" danger>删除</Button>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><h1 style={{ fontSize: 16, margin: 0 }}>角色管理</h1><p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理系统角色和权限配置</p></div>
        <Permission code="role:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateModalOpen(true)}>新增角色</Button>
        </Permission>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
          <Input placeholder="请输入角色名称" style={{ width: 200 }}
            value={searchName}
            onChange={(e) => setSearchName(e.target.value)}
            onPressEnter={() => setParams({ ...params, page: 1, keyword: searchName })} />
          <Button type="primary" onClick={() => setParams({ ...params, page: 1, keyword: searchName })}>搜索</Button>
        </div>
        <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>
        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>

      {/* Create Modal */}
      <PermissionEditModal
        title="新增角色"
        open={isCreateModalOpen}
        form={createForm}
        selectedPermissionIds={selectedPermissionIds}
        setSelectedPermissionIds={setSelectedPermissionIds}
        groupedPermissions={groupedPermissions}
        onFinish={handleCreate}
        onOk={() => createForm.submit()}
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields(); setSelectedPermissionIds([]) }}
      />

      {/* Edit Modal */}
      <PermissionEditModal
        title="编辑角色"
        open={isEditModalOpen}
        form={editForm}
        selectedPermissionIds={selectedPermissionIds}
        setSelectedPermissionIds={setSelectedPermissionIds}
        groupedPermissions={groupedPermissions}
        onFinish={handleUpdate}
        onOk={() => editForm.submit()}
        onCancel={() => setIsEditModalOpen(false)}
      />
    </div>
  )
}

interface PermissionEditModalProps {
  title: string
  open: boolean
  form: any
  selectedPermissionIds: number[]
  setSelectedPermissionIds: (ids: number[] | ((prev: number[]) => number[])) => void
  groupedPermissions: Record<string, any[]>
  onFinish: (values: any) => void
  onOk: () => void
  onCancel: () => void
}

const PermissionEditModal = ({ title, open, form, selectedPermissionIds, setSelectedPermissionIds, groupedPermissions, onFinish, onOk, onCancel }: PermissionEditModalProps) => (
  <Modal title={title}
    open={open} onOk={onOk} onCancel={onCancel} width={600}>
    <Form form={form} layout="vertical" onFinish={onFinish}>
      <Form.Item label="角色名称" name="name" rules={[{ required: true, message: '请输入角色名称' }]}><Input /></Form.Item>
      <Form.Item label="描述" name="description"><Input.TextArea rows={2} /></Form.Item>
      <Form.Item label="权限配置">
        <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid #d9d9d9', borderRadius: 4, padding: 12 }}>
          {Object.entries(groupedPermissions).map(([module, perms]) => (
            <div key={module} style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 'bold', marginBottom: 6, color: '#666' }}>{module}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {(perms as any[]).map((p: any) => (
                  <Tag key={p.id} color={selectedPermissionIds.includes(p.id) ? 'blue' : 'default'}
                    style={{ cursor: 'pointer', margin: 0, padding: '2px 8px' }}
                    onClick={() => {
                      if (selectedPermissionIds.includes(p.id)) {
                        setSelectedPermissionIds(selectedPermissionIds.filter((id: number) => id !== p.id))
                      } else {
                        setSelectedPermissionIds([...selectedPermissionIds, p.id])
                      }
                    }}>
                    {p.name}
                  </Tag>
                ))}
              </div>
            </div>
          ))}
          {Object.keys(groupedPermissions).length === 0 && <p style={{ color: '#999' }}>暂无权限数据</p>}
        </div>
      </Form.Item>
    </Form>
  </Modal>
)

export default Role
