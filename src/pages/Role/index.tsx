import { Card, Table, Button, Space, Input, Modal, Form, message, Tag, Popconfirm, Checkbox, Select, Tree } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import type { DataNode } from 'antd/es/tree'
import { useMemo, useState, useEffect } from 'react'
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
  const [allPermissions, setAllPermissions] = useState<any[]>([])
  const [allMenus, setAllMenus] = useState<any[]>([])
  const [searchName, setSearchName] = useState('')
  const [keepAdding, setKeepAdding] = useState(true)
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [selectedTreeKeys, setSelectedTreeKeys] = useState<string[]>([])
  const [checkedPermIds, setCheckedPermIds] = useState<number[]>([])

  useEffect(() => { fetchRoles() }, [params])
  useEffect(() => { fetchPermissionsAndMenus() }, [])

  const fetchPermissionsAndMenus = async () => {
    try {
      const [permsRes, menusRes]: any = await Promise.all([permissionApi.getPermissions(), permissionApi.getMenus()])
      if (permsRes?.code === 0) setAllPermissions(permsRes.data || [])
      if (menusRes?.code === 0) setAllMenus(menusRes.data || [])
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
      const payload: any = {
        name: String(values.name || '').trim(),
        description: values.description || undefined,
        status: values.disabled ? 0 : 1,
        data_scope: values.data_scope,
        permission_ids: checkedPermIds,
      }
      await roleApi.create(payload)
      message.success('创建成功')
      if (keepAdding) {
        createForm.resetFields()
        setCheckedPermIds([])
        setSelectedTreeKeys([])
      } else {
        setIsCreateModalOpen(false)
        createForm.resetFields()
        setCheckedPermIds([])
        setSelectedTreeKeys([])
      }
      fetchRoles()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      const payload: any = {
        name: String(values.name || '').trim(),
        description: values.description || undefined,
        status: values.disabled ? 0 : 1,
        data_scope: values.data_scope,
        permission_ids: checkedPermIds,
      }
      await roleApi.update(editingRole.id, payload)
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

  const permByMenuId = useMemo(() => {
    const map = new Map<number, any[]>()
    for (const p of allPermissions || []) {
      const mid = Number(p.menu_id || 0)
      if (!mid) continue
      const arr = map.get(mid) || []
      arr.push(p)
      map.set(mid, arr)
    }
    for (const [, arr] of map.entries()) {
      arr.sort((a: any, b: any) => {
        const ta = String(a.type || '')
        const tb = String(b.type || '')
        if (ta !== tb) return ta === 'menu' ? -1 : 1
        return Number(a.id) - Number(b.id)
      })
    }
    return map
  }, [allPermissions])

  type AnyNode = DataNode & { kind: 'group' | 'perm'; perm_id?: number }

  const permissionTreeData: AnyNode[] = useMemo(() => {
    const build = (menus: any[]): AnyNode[] => {
      return (menus || []).map((m: any) => {
        const menuId = Number(m.id)
        const perms = (permByMenuId.get(menuId) || []).filter((p: any) => p.type === 'button')
        const viewPerm = (permByMenuId.get(menuId) || []).find((p: any) => p.type === 'menu')
        const childrenMenus = Array.isArray(m.children) ? m.children : []

        const children: AnyNode[] = []
        if (childrenMenus.length > 0) children.push(...build(childrenMenus))
        if (perms.length > 0) {
          children.push(
            ...perms.map((p: any) => ({
              key: `perm_${p.id}`,
              title: p.name,
              isLeaf: true,
              kind: 'perm' as const,
              perm_id: Number(p.id),
            })),
          )
        }

        if (viewPerm) {
          return {
            key: `perm_${viewPerm.id}`,
            title: m.name,
            children: children.length > 0 ? children : undefined,
            kind: 'perm' as const,
            perm_id: Number(viewPerm.id),
          }
        }

        return {
          key: `menu_${menuId}`,
          title: m.name,
          children: children.length > 0 ? children : undefined,
          kind: 'group' as const,
        }
      })
    }
    return build(allMenus)
  }, [allMenus, permByMenuId])

  const allExpandableKeys = useMemo(() => {
    const keys: string[] = []
    const walk = (nodes: AnyNode[]) => {
      for (const n of nodes) {
        if (Array.isArray(n.children) && n.children.length > 0) {
          keys.push(String(n.key))
          walk(n.children as AnyNode[])
        }
      }
    }
    walk(permissionTreeData)
    return keys
  }, [permissionTreeData])

  const allPermIds = useMemo(() => allPermissions.map((p: any) => Number(p.id)).filter((x: any) => Number.isFinite(x)), [allPermissions])
  const isAllSelected = useMemo(() => allPermIds.length > 0 && allPermIds.every((id) => checkedPermIds.includes(id)), [allPermIds, checkedPermIds])

  const checkedKeys = useMemo(() => checkedPermIds.map((id) => `perm_${id}`), [checkedPermIds])

  const normalizeCheckedKeyList = (keys: any): string[] => {
    const raw = Array.isArray(keys) ? keys : Array.isArray(keys?.checked) ? keys.checked : []
    return raw.map((k: any) => String(k))
  }

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
            <Button
              type="link"
              onClick={async () => {
                try {
                  await roleApi.update(record.id, { status: record.status === 1 ? 0 : 1 })
                  message.success('状态更新成功')
                  fetchRoles()
                } catch { /* ignore */ }
              }}
            >
              {record.status === 1 ? '禁用' : '启用'}
            </Button>
          </Permission>
          <Permission code="role:edit">
            <Button type="link" onClick={() => {
              setEditingRole(record)
              editForm.setFieldsValue({
                name: record.name,
                description: record.description,
                disabled: record.status !== 1,
                data_scope: record.data_scope || 'all',
              })
              setCheckedPermIds(record.permission_ids || [])
              setExpandedKeys(allExpandableKeys)
              setSelectedTreeKeys([])
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
          <Button type="primary" icon={<PlusOutlined />} onClick={() => {
            createForm.resetFields()
            createForm.setFieldsValue({ disabled: false, data_scope: 'all' })
            setCheckedPermIds([])
            setExpandedKeys(allExpandableKeys)
            setSelectedTreeKeys([])
            setIsCreateModalOpen(true)
          }}>新增角色</Button>
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
      <RoleEditModal
        title="新增角色"
        open={isCreateModalOpen}
        form={createForm}
        isCreate
        keepAdding={keepAdding}
        setKeepAdding={setKeepAdding}
        permissionTreeData={permissionTreeData}
        expandedKeys={expandedKeys}
        setExpandedKeys={setExpandedKeys}
        selectedTreeKeys={selectedTreeKeys}
        setSelectedTreeKeys={setSelectedTreeKeys}
        checkedKeys={checkedKeys}
        onTreeCheck={(keys) => {
          const ids = normalizeCheckedKeyList(keys)
            .filter((k) => k.startsWith('perm_'))
            .map((k) => Number(k.slice('perm_'.length)))
            .filter((n) => Number.isFinite(n))
          setCheckedPermIds(ids)
        }}
        onTreeSelect={(keys) => setSelectedTreeKeys(keys as string[])}
        onToggleExpandAll={() => setExpandedKeys((prev) => (prev.length > 0 ? [] : allExpandableKeys))}
        onToggleCheckAll={() => setCheckedPermIds(isAllSelected ? [] : allPermIds)}
        onFinish={handleCreate}
        onOk={() => createForm.submit()}
        onCancel={() => {
          setIsCreateModalOpen(false)
          createForm.resetFields()
          setCheckedPermIds([])
          setSelectedTreeKeys([])
        }}
      />

      {/* Edit Modal */}
      <RoleEditModal
        title="编辑角色"
        open={isEditModalOpen}
        form={editForm}
        isCreate={false}
        keepAdding={false}
        setKeepAdding={() => {}}
        permissionTreeData={permissionTreeData}
        expandedKeys={expandedKeys}
        setExpandedKeys={setExpandedKeys}
        selectedTreeKeys={selectedTreeKeys}
        setSelectedTreeKeys={setSelectedTreeKeys}
        checkedKeys={checkedKeys}
        onTreeCheck={(keys) => {
          const ids = normalizeCheckedKeyList(keys)
            .filter((k) => k.startsWith('perm_'))
            .map((k) => Number(k.slice('perm_'.length)))
            .filter((n) => Number.isFinite(n))
          setCheckedPermIds(ids)
        }}
        onTreeSelect={(keys) => setSelectedTreeKeys(keys as string[])}
        onToggleExpandAll={() => setExpandedKeys((prev) => (prev.length > 0 ? [] : allExpandableKeys))}
        onToggleCheckAll={() => setCheckedPermIds(isAllSelected ? [] : allPermIds)}
        onFinish={handleUpdate}
        onOk={() => editForm.submit()}
        onCancel={() => setIsEditModalOpen(false)}
      />
    </div>
  )
}

interface RoleEditModalProps {
  title: string
  open: boolean
  form: any
  isCreate: boolean
  keepAdding: boolean
  setKeepAdding: (val: boolean) => void
  permissionTreeData: DataNode[]
  expandedKeys: string[]
  setExpandedKeys: (keys: string[]) => void
  selectedTreeKeys: string[]
  setSelectedTreeKeys: (keys: string[]) => void
  checkedKeys: string[]
  onTreeCheck: (keys: any) => void
  onTreeSelect: (keys: any) => void
  onToggleExpandAll: () => void
  onToggleCheckAll: () => void
  onFinish: (values: any) => void
  onOk: () => void
  onCancel: () => void
}

const RoleEditModal = ({
  title,
  open,
  form,
  isCreate,
  keepAdding,
  setKeepAdding,
  permissionTreeData,
  expandedKeys,
  setExpandedKeys,
  selectedTreeKeys,
  setSelectedTreeKeys,
  checkedKeys,
  onTreeCheck,
  onTreeSelect,
  onToggleExpandAll,
  onToggleCheckAll,
  onFinish,
  onOk,
  onCancel,
}: RoleEditModalProps) => (
  <Modal title={title} open={open} onOk={onOk} onCancel={onCancel} width={640} okText={isCreate ? '新增' : '保存'}>
    <Form form={form} layout="vertical" onFinish={onFinish} initialValues={{ disabled: false, data_scope: 'all' }}>
      <Form.Item label="角色名称" name="name" rules={[{ required: true, message: '请输入角色名称' }]}>
        <Input placeholder="请输入角色名称" />
      </Form.Item>

      <Form.Item label="状态" name="disabled" valuePropName="checked">
        <Checkbox>禁用</Checkbox>
      </Form.Item>

      <Form.Item label="菜单权限">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <Button size="small" onClick={onToggleExpandAll}>
            展开/折叠
          </Button>
          <Button size="small" onClick={onToggleCheckAll}>
            全选/全不选
          </Button>
        </div>
        <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 8, maxHeight: 360, overflow: 'auto' }}>
          <Tree
            checkable
            showLine
            treeData={permissionTreeData}
            expandedKeys={expandedKeys}
            onExpand={(keys) => setExpandedKeys(keys as string[])}
            selectedKeys={selectedTreeKeys}
            onSelect={(keys) => { setSelectedTreeKeys(keys as string[]); onTreeSelect(keys) }}
            checkedKeys={checkedKeys}
            onCheck={(keys) => onTreeCheck(keys as any)}
          />
        </div>
      </Form.Item>

      <Form.Item label="数据权限" name="data_scope" rules={[{ required: true, message: '请选择数据权限' }]}>
        <Select
          options={[
            { label: '全部数据权限', value: 'all' },
            { label: '所属项目数据权限', value: 'project' },
            { label: '仅本人数据权限', value: 'self' },
          ]}
        />
      </Form.Item>

      <Form.Item label="备注" name="description">
        <Input.TextArea rows={2} placeholder="请输入备注" />
      </Form.Item>

      {isCreate && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Checkbox checked={keepAdding} onChange={(e) => setKeepAdding(e.target.checked)}>
            继续新增
          </Checkbox>
          <div />
        </div>
      )}
    </Form>
  </Modal>
)

export default Role
