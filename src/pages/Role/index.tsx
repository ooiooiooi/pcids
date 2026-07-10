import { Table, Button, Input, Modal, Form, App as AntdApp, Tag, Checkbox, Select, Tree, Switch, Space, Grid } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import type { DataNode } from 'antd/es/tree'
import { useMemo, useState, useEffect, useRef } from 'react'
import { roleApi } from '../../services/api'
import { permissionApi } from '../../services/permission'
import { Permission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton, PageSecondaryButton } from '../../components/ActionButton'
import { patchTreeHiddenInputs } from '../../utils/treeAccessibility'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

const Role: React.FC = () => {
  const screens = Grid.useBreakpoint()
  const { message } = AntdApp.useApp()
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
  const [createPermissionExpanded, setCreatePermissionExpanded] = useState(false)
  const [editPermissionExpanded, setEditPermissionExpanded] = useState(false)
  const [createPermissionError, setCreatePermissionError] = useState(false)
  const [editPermissionError, setEditPermissionError] = useState(false)
  const [deletingRoleId, setDeletingRoleId] = useState<number | null>(null)

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
      if (!checkedPermIds.length) {
        setCreatePermissionError(true)
        message.error('请至少选择一项菜单权限')
        return
      }
      const payload: any = {
        name: String(values.name || '').trim(),
        description: values.description || undefined,
        status: values.status ? 1 : 0,
        data_scope: values.data_scope,
        permission_ids: checkedPermIds,
      }
      const res: any = await roleApi.create(payload)
      if (res?.code !== 0) throw new Error(res?.message || '角色新增失败')
      message.success('角色新增成功')
      if (keepAdding) {
        createForm.resetFields()
        setCheckedPermIds([])
        setSelectedTreeKeys([])
        setExpandedKeys([])
        setCreatePermissionExpanded(false)
        setCreatePermissionError(false)
        createForm.setFieldsValue({ status: true, data_scope: 'all' })
      } else {
        setIsCreateModalOpen(false)
        createForm.resetFields()
        setCheckedPermIds([])
        setSelectedTreeKeys([])
        setExpandedKeys([])
        setCreatePermissionExpanded(false)
        setCreatePermissionError(false)
      }
      fetchRoles()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || e?.message || '角色新增失败')
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      if (!checkedPermIds.length) {
        setEditPermissionError(true)
        message.error('请至少选择一项菜单权限')
        return
      }
      const payload: any = {
        name: String(values.name || '').trim(),
        description: values.description || undefined,
        status: values.status ? 1 : 0,
        data_scope: values.data_scope,
        permission_ids: checkedPermIds,
      }
      await roleApi.update(editingRole.id, payload)
      message.success('更新成功')
      setIsEditModalOpen(false)
      setEditPermissionError(false)
      fetchRoles()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDelete = async (id: number) => {
    setDeletingRoleId(id)
    try {
      await roleApi.delete(id)
      message.success('删除成功')
      fetchRoles()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    } finally {
      setDeletingRoleId(null)
    }
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
        if (viewPerm) {
          children.push({
            key: `perm_${viewPerm.id}`,
            title: viewPerm.name || `${m.name}查看`,
            isLeaf: true,
            kind: 'perm' as const,
            perm_id: Number(viewPerm.id),
          })
        }
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
    {
      title: '角色名称',
      dataIndex: 'name',
      key: 'name',
      width: screens.md ? '34%' : '30%',
      render: (name: string) => <EllipsisText value={name} />,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: screens.md ? 110 : 90, align: 'center' as const,
      render: (status: number) => (
        <Tag color={status === 1 ? 'green' : 'red'}>{status === 1 ? '启用' : '禁用'}</Tag>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: screens.md ? '26%' : '28%',
      render: (value: string) => <EllipsisText value={formatDateTime(value)} />,
    },
    {
      title: '操作', key: 'action', width: screens.md ? '24%' : '28%', align: 'right' as const, fixed: 'right' as const,
      render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          <Permission code="role:edit">
            <ActionLinkButton
              onClick={async () => {
                try {
                  await roleApi.update(record.id, { status: record.status === 1 ? 0 : 1 })
                  message.success('状态更新成功')
                  fetchRoles()
                } catch (e: any) {
                  message.error(e?.response?.data?.detail || '状态更新失败')
                }
              }}
            >
              {record.status === 1 ? '禁用' : '启用'}
            </ActionLinkButton>
          </Permission>
          <Permission code="role:edit">
            <ActionLinkButton onClick={() => {
              setEditingRole(record)
              editForm.setFieldsValue({
                name: record.name,
                description: record.description,
                status: record.status === 1,
                data_scope: record.data_scope || 'all',
              })
              setCheckedPermIds(record.permission_ids || [])
              setExpandedKeys([])
              setEditPermissionExpanded(false)
              setEditPermissionError(false)
              setSelectedTreeKeys([])
              setIsEditModalOpen(true)
            }}>编辑</ActionLinkButton>
          </Permission>
          <Permission code="role:delete">
            <ActionConfirm
              title="删除角色"
              description={`确认删除角色“${record.name || record.id}”吗？`}
              onConfirm={() => handleDelete(record.id)}
              okText="确认删除"
              cancelText="取消"
              confirmLoading={deletingRoleId === record.id}
            >
              <ActionLinkButton danger>删除</ActionLinkButton>
            </ActionConfirm>
          </Permission>
        </ActionButtonGroup>
      ),
    },
  ]

  return (
    <div
      style={{
        height: '100%',
        background: '#fff',
        borderRadius: 6,
        padding: screens.lg ? 24 : screens.md ? 20 : 16,
        overflow: 'auto',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: screens.md ? 'flex-start' : 'stretch',
          gap: 16,
          flexWrap: 'wrap',
          marginBottom: screens.md ? 18 : 16,
        }}
      >
        <div style={{ flex: '1 1 320px', minWidth: 0 }}>
          <div className="client-page-title">
            <h1>角色管理</h1>
            <p className="client-page-subtitle">管理系统角色、菜单权限与操作授权范围</p>
          </div>
        </div>
        <Permission code="role:add">
          <PagePrimaryButton icon={<PlusOutlined />} onClick={() => {
            createForm.resetFields()
            createForm.setFieldsValue({ status: true, data_scope: 'all' })
            setCheckedPermIds([])
            setExpandedKeys([])
            setCreatePermissionExpanded(false)
            setCreatePermissionError(false)
            setSelectedTreeKeys([])
            setIsCreateModalOpen(true)
          }}>新增角色</PagePrimaryButton>
        </Permission>
      </div>

      <div style={{ background: '#fff', borderRadius: 8 }}>
        <div
          style={{
            marginBottom: 16,
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 12,
            flexWrap: 'wrap',
            alignItems: 'center',
          }}
        >
          <Input
            id="role-search-name"
            name="roleKeyword"
            autoComplete="off"
            className="pcids-list-search"
            placeholder="请输入角色名称"
            allowClear
            prefix={<SearchOutlined />}
            value={searchName}
            onChange={(e) => setSearchName(e.target.value)}
            onPressEnter={() => setParams({ ...params, page: 1, keyword: searchName })}
            style={{ marginLeft: 'auto' }}
          />
        </div>
        <Table
          columns={columns}
          dataSource={dataSource}
          rowKey="id"
          loading={loading}
          tableLayout="fixed"
          scroll={{ x: 760 }}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }),
            showSizeChanger: false,
            showTotal: (t) =>
              renderListPaginationTotal(t, params.page_size, (pageSize) =>
                setParams({ ...params, page: 1, page_size: pageSize }),
              ),
          }}
        />
      </div>

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
        permissionExpanded={createPermissionExpanded}
        permissionError={createPermissionError}
        onTreeCheck={(keys) => {
          const ids = normalizeCheckedKeyList(keys)
            .filter((k) => k.startsWith('perm_'))
            .map((k) => Number(k.slice('perm_'.length)))
            .filter((n) => Number.isFinite(n))
          setCheckedPermIds(ids)
          if (ids.length > 0) setCreatePermissionError(false)
        }}
        onTreeSelect={(keys) => setSelectedTreeKeys(keys as string[])}
        onToggleExpandAll={() => {
          const nextExpanded = !createPermissionExpanded
          setCreatePermissionExpanded(nextExpanded)
          setExpandedKeys(nextExpanded ? allExpandableKeys : [])
        }}
        onToggleCheckAll={() => {
          const nextIds = isAllSelected ? [] : allPermIds
          setCheckedPermIds(nextIds)
          setCreatePermissionError(nextIds.length === 0)
        }}
        onFinish={handleCreate}
        onOk={() => createForm.submit()}
        onCancel={() => {
          setIsCreateModalOpen(false)
          createForm.resetFields()
          setCheckedPermIds([])
          setSelectedTreeKeys([])
          setExpandedKeys([])
          setCreatePermissionExpanded(false)
          setCreatePermissionError(false)
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
        permissionExpanded={editPermissionExpanded}
        permissionError={editPermissionError}
        onTreeCheck={(keys) => {
          const ids = normalizeCheckedKeyList(keys)
            .filter((k) => k.startsWith('perm_'))
            .map((k) => Number(k.slice('perm_'.length)))
            .filter((n) => Number.isFinite(n))
          setCheckedPermIds(ids)
          if (ids.length > 0) setEditPermissionError(false)
        }}
        onTreeSelect={(keys) => setSelectedTreeKeys(keys as string[])}
        onToggleExpandAll={() => {
          const nextExpanded = !editPermissionExpanded
          setEditPermissionExpanded(nextExpanded)
          setExpandedKeys(nextExpanded ? allExpandableKeys : [])
        }}
        onToggleCheckAll={() => {
          const nextIds = isAllSelected ? [] : allPermIds
          setCheckedPermIds(nextIds)
          setEditPermissionError(nextIds.length === 0)
        }}
        onFinish={handleUpdate}
        onOk={() => editForm.submit()}
        onCancel={() => {
          setIsEditModalOpen(false)
          setEditPermissionExpanded(false)
          setEditPermissionError(false)
          setExpandedKeys([])
        }}
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
  permissionExpanded: boolean
  permissionError: boolean
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
  permissionExpanded,
  permissionError,
  onTreeCheck,
  onTreeSelect,
  onToggleExpandAll,
  onToggleCheckAll,
  onFinish,
  onOk,
  onCancel,
}: RoleEditModalProps) => {
  const { message } = AntdApp.useApp()
  const treeContainerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return undefined

    const frameId = window.requestAnimationFrame(() => {
      patchTreeHiddenInputs(treeContainerRef.current, isCreate ? 'role-create-tree' : 'role-edit-tree')
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [open, isCreate, permissionTreeData, expandedKeys, selectedTreeKeys, checkedKeys])

  return (
    <Modal
      title={title}
      className={isCreate ? 'pcids-modal pcids-modal--form' : 'pcids-modal pcids-modal--form'}
      open={open}
      footer={
        isCreate ? (
          <div className="pcids-modal__footer-split">
            <Checkbox className="pcids-modal__continue" name="keepAdding" checked={keepAdding} onChange={(e) => setKeepAdding(e.target.checked)}>
              继续新增
            </Checkbox>
            <Space className="pcids-modal__footer-actions">
              <PageSecondaryButton onClick={onCancel}>取消</PageSecondaryButton>
              <PagePrimaryButton onClick={onOk}>新增</PagePrimaryButton>
            </Space>
          </div>
        ) : undefined
      }
      okText={isCreate ? '新增' : '保存'}
      onOk={isCreate ? undefined : onOk}
      onCancel={onCancel}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={onFinish}
        initialValues={{ status: true, data_scope: 'all' }}
        scrollToFirstError
        onFinishFailed={(errorInfo) => {
          message.warning(getFirstFormErrorMessage(errorInfo))
        }}
      >
        <Form.Item label="角色名称" name="name" rules={[{ required: true, message: '请输入角色名称' }]}>
          <Input name="name" autoComplete="organization-title" placeholder="请输入角色名称" />
        </Form.Item>

        <Form.Item label="状态" name="status" valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="禁用" />
        </Form.Item>

        <Form.Item
          label={
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 12, flexWrap: 'nowrap' }}>
              <span>菜单权限</span>
              <Space size={8} wrap={false}>
                <Button size="small" onClick={onToggleExpandAll}>
                  {permissionExpanded ? '折叠' : '展开'}
                </Button>
                <Button size="small" onClick={onToggleCheckAll}>
                  {checkedKeys.length ? '全不选' : '全选'}
                </Button>
              </Space>
            </div>
          }
          htmlFor={isCreate ? 'role-create-tree-sr' : 'role-edit-tree-sr'}
          validateStatus={permissionError ? 'error' : undefined}
          help={permissionError ? '请至少选择一项菜单权限' : undefined}
        >
          <div
            ref={treeContainerRef}
            style={{
              border: `1px solid ${permissionError ? '#ff4d4f' : '#f0f0f0'}`,
              borderRadius: 6,
              padding: 8,
              maxHeight: permissionExpanded ? 360 : undefined,
              overflow: 'auto',
            }}
          >
            {!permissionExpanded ? (
              <div style={{ color: '#8c8c8c', padding: '4px 8px' }}>已折叠菜单权限，点击“展开”查看全部配置</div>
            ) : (
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
            )}
          </div>
        </Form.Item>

        <Form.Item label="数据权限" name="data_scope" rules={[{ required: true, message: '请选择数据权限' }]}>
          <Select
            id="data_scope"
            options={[
              { label: '全部数据权限', value: 'all' },
              { label: '所属项目数据权限', value: 'project' },
              { label: '仅本人数据权限', value: 'self' },
            ]}
          />
        </Form.Item>

        <Form.Item label="备注" name="description">
          <Input.TextArea name="description" autoComplete="off" rows={2} placeholder="请输入备注" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default Role
