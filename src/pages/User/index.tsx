import { Table, Space, Input, Modal, Form, App as AntdApp, Tag, Select, Switch, Checkbox, Typography } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useEffect, useState, type ReactNode } from 'react'
import { userApi, roleApi } from '../../services/api'
import { Permission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton, PageSecondaryButton } from '../../components/ActionButton'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'

const { Title } = Typography

const DISPLAY_NAME_PATTERN = /^[A-Za-z0-9\u4e00-\u9fa5]+$/
const ACCOUNT_PATTERN = /^[A-Za-z][A-Za-z0-9]{3,19}$/
const SENSITIVE_NAME_TOKENS = ['admin', '管理员', '系统', 'root', '官方', '客服', '测试']
const WEAK_PASSWORDS = new Set(['123456', '12345678', '123456789', 'password', 'password123', 'admin123', 'qwerty', 'qwerty123', 'abc12345', '11111111'])
const KEYBOARD_SEQUENCES = ['1234', '2345', '3456', '4567', '5678', '6789', 'abcd', 'bcde', 'cdef', 'qwer', 'wert', 'asdf', 'sdfg', 'zxcv', 'xcvb']

const validateDisplayNameValue = (value: string) => {
  const text = String(value || '').trim()
  if (!text) return '请输入用户名'
  if (text.length < 2 || text.length > 16) return '用户名长度需为2-16个字符'
  if (/^\d/.test(text)) return '用户名首字符不能为数字'
  if (!DISPLAY_NAME_PATTERN.test(text)) return '用户名仅支持中文、英文、数字，不能包含空格或特殊符号'
  if (/^\d+$/.test(text)) return '用户名不能为纯数字'
  if (SENSITIVE_NAME_TOKENS.some((item) => text.toLowerCase().includes(item))) return '用户名不能包含敏感词'
  return ''
}

const validateAccountFormat = (value: string) => {
  const text = String(value || '').trim()
  if (!text) return '请输入账号（4~20位字母数字）'
  if (text.length < 4 || text.length > 20) return '用户账号长度需为4-20个字符'
  if (!/^[A-Za-z]/.test(text)) return '用户账号必须以字母开头'
  if (!ACCOUNT_PATTERN.test(text)) return '用户账号仅支持字母和数字'
  return ''
}

const evaluatePasswordStrength = (password: string, username: string) => {
  const value = String(password || '')
  const account = String(username || '').trim()
  let score = 0
  const messages: string[] = []
  const categories = [
    /[A-Z]/.test(value),
    /[a-z]/.test(value),
    /\d/.test(value),
    /[^A-Za-z0-9]/.test(value),
  ].filter(Boolean).length

  if (value.length >= 8 && value.length <= 32) score += 25
  else messages.push('长度需为8-32位')

  if (categories >= 2) score += 25
  else messages.push('至少包含两类字符组合')

  if (value && value.toLowerCase() !== account.toLowerCase()) score += 20
  else if (value) messages.push('不能与用户账号相同')

  if (value && !WEAK_PASSWORDS.has(value.toLowerCase())) score += 15
  else if (value) messages.push('不能使用常见弱密码')

  if (value && !/(.)\1\1/.test(value)) score += 15
  else if (value) messages.push('不能包含连续重复字符')

  if (value && !KEYBOARD_SEQUENCES.some((item) => value.toLowerCase().includes(item))) {
    score += 10
  } else if (value) {
    messages.push('不能包含规律键盘序列')
  }

  const clamped = Math.min(score, 100)
  if (!value) return { percent: 0, level: '未输入', color: '#d9d9d9', valid: false, message: '请输入密码（8~32位，字母、数字和特殊字符至少两种组合）' }
  if (messages.length) return { percent: clamped, level: '弱', color: '#ff4d4f', valid: false, message: messages[0] || '密码不符合规则' }
  if (clamped < 75) return { percent: clamped, level: '中', color: '#faad14', valid: true, message: '密码强度中等，可继续增强' }
  return { percent: clamped, level: '强', color: '#52c41a', valid: true, message: '密码强度良好' }
}

type UserFormFieldProps = {
  label: string
  name: string
  form: any
  children: ReactNode
  rules?: any[]
  validateTrigger?: string
  validateStatus?: 'success' | 'error' | 'validating'
  help?: ReactNode | null
  valuePropName?: string
}

const UserFormField = ({
  label,
  name,
  form,
  children,
  rules,
  validateTrigger,
  validateStatus,
  help,
  valuePropName,
}: UserFormFieldProps) => (
  <div className="user-form-field">
    <div className="user-form-field__label">{label}</div>
    <Form.Item
      name={name}
      noStyle
      rules={rules}
      validateTrigger={validateTrigger}
      valuePropName={valuePropName}
    >
      {children}
    </Form.Item>
    <Form.Item noStyle shouldUpdate>
      {() => {
        const errors = form.getFieldError(name)
        const messageText = errors[0] || help || ''
        if (!messageText) return null
        return (
          <div className={`user-form-field__help${validateStatus ? ` user-form-field__help--${validateStatus}` : errors.length ? ' user-form-field__help--error' : ''}`}>
            {messageText}
          </div>
        )
      }}
    </Form.Item>
  </div>
)

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

const User: React.FC = () => {
  const { message } = AntdApp.useApp()
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [roles, setRoles] = useState<any[]>([])
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '', role_id: 'all' as number | 'all', status: 'all' as number | 'all', sort_field: 'created_at', sort_order: 'desc' })
  const [editingUser, setEditingUser] = useState<any>(null)
  const [continueAdd, setContinueAdd] = useState(true)
  const [usernameCheckState, setUsernameCheckState] = useState<'idle' | 'checking' | 'available' | 'taken'>('idle')
  const [usernameCheckMessage, setUsernameCheckMessage] = useState('')
  const [roleFilterOpen, setRoleFilterOpen] = useState(false)
  const [statusFilterOpen, setStatusFilterOpen] = useState(false)
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const createUsername = Form.useWatch('username', createForm)

  useEffect(() => { fetchRoles() }, [])
  useEffect(() => { fetchUsers() }, [params])
  useEffect(() => {
    if (!isCreateModalOpen) return
    const formatError = validateAccountFormat(createUsername || '')
    if (!createUsername) {
      setUsernameCheckState('idle')
      setUsernameCheckMessage('')
      return
    }
    if (formatError) {
      setUsernameCheckState('taken')
      setUsernameCheckMessage(formatError)
      return
    }
    setUsernameCheckState('checking')
    setUsernameCheckMessage('正在检查账号可用性...')
    const timer = window.setTimeout(async () => {
      try {
        await userApi.checkUsername(String(createUsername).trim())
        setUsernameCheckState('available')
        setUsernameCheckMessage('账号可用')
      } catch (error: any) {
        setUsernameCheckState('taken')
        setUsernameCheckMessage(error?.response?.data?.detail || '账号已存在')
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [createUsername, isCreateModalOpen])

  const fetchUsers = async () => {
    setLoading(true)
    try {
      const res: any = await userApi.getList({
        ...params,
        role_id: params.role_id === 'all' ? undefined : params.role_id,
        status: params.status === 'all' ? undefined : params.status,
      })
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
      if (usernameCheckState === 'checking') {
        message.warning('正在校验账号可用性，请稍候')
        return
      }
      if (usernameCheckState === 'taken') {
        message.error(usernameCheckMessage || '账号不可用')
        return
      }
      const payload = {
        ...values,
        status: values.status ? 1 : 0
      }
      const res: any = await userApi.create(payload)
      if (res?.code !== 0) {
        throw new Error(res?.message || '创建失败')
      }
      message.success('创建成功')
      if (!continueAdd) {
        setIsCreateModalOpen(false)
      }
      createForm.resetFields()
      createForm.setFieldsValue({ status: true })
      setUsernameCheckState('idle')
      setUsernameCheckMessage('')
      fetchUsers()
    } catch (e: any) {
      if (!e?.errorFields) {
        const detail = e?.response?.data?.detail || '创建失败'
        if (String(detail).includes('密码')) {
          createForm.setFields([{ name: 'password', errors: [detail] }])
        }
        message.error(detail)
      }
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      const payload = {
        ...values,
        status: values.status ? 1 : 0
      }
      await userApi.update(editingUser.id, payload)
      message.success('更新成功')
      setIsEditModalOpen(false)
      fetchUsers()
    } catch (e: any) {
      if (!e?.errorFields) message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDeleteUser = async (id: number) => {
    setDeletingUserId(id)
    try {
      await userApi.delete(id)
      message.success('删除成功')
      fetchUsers()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
    finally {
      setDeletingUserId(null)
    }
  }

  const columns = [
    { title: '用户账号', dataIndex: 'username', key: 'username', width: '16%', render: (value: string) => <EllipsisText value={value} /> },
    { 
      title: '用户名', 
      dataIndex: 'display_name', 
      key: 'display_name', 
      width: '18%',
      render: (_: any, record: any) => {
        const name = record.display_name || record.username || '-'
        return (
          <UserIdentity
            user={{ display_name: record.display_name, username: record.username, avatar_url: record.avatar_url }}
            fallbackName={name}
            avatarSize={23}
          />
        )
      },
    },
    { 
      title: '角色', 
      dataIndex: 'role', 
      key: 'role', 
      width: '12%',
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
      width: '16%',
      sorter: true,
      render: (t: string) => formatDateTime(t),
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: '10%',
      render: (status: number) => <Tag color={status === 1 ? 'success' : 'warning'} style={{ borderRadius: 10 }}>{status === 1 ? '启用' : '禁用'}</Tag>,
    },
    {
      title: '操作', key: 'action', width: '28%', fixed: 'right' as const,
      render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          <Permission code="user:edit">
            <ActionLinkButton onClick={() => { 
              setEditingUser(record); 
              editForm.setFieldsValue({
                ...record,
                status: record.status === 1
              }); 
              setIsEditModalOpen(true) 
            }}>编辑</ActionLinkButton>
          </Permission>
          <Permission code="user:reset_pwd">
            <ActionConfirm
              title="重置密码"
              description={`确认重置用户“${record.username}”的密码吗？`}
              okText="确认重置"
              cancelText="取消"
              onConfirm={async () => {
                try {
                  await userApi.resetPassword(record.id)
                  message.success({ content: '密码已重置为ca123456', duration: 3 })
                }
                catch { /* interceptor handles it */ }
              }}
            >
              <ActionLinkButton>重置密码</ActionLinkButton>
            </ActionConfirm>
          </Permission>
          <Permission code="user:delete">
            <ActionConfirm
              title="确认删除该用户？"
              description={`确认删除用户“${record.username}”吗？`}
              onConfirm={() => handleDeleteUser(record.id)}
              okText="确认删除"
              cancelText="取消"
              confirmLoading={deletingUserId === record.id}
            >
              <ActionLinkButton danger>删除</ActionLinkButton>
            </ActionConfirm>
          </Permission>
        </ActionButtonGroup>
      ),
    },
  ]

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div className="client-page-title">
          <Title level={4}>用户管理</Title>
          <p className="client-page-subtitle">管理登录账号、角色归属、状态与安全操作</p>
        </div>
        <Permission code="user:add">
          <PagePrimaryButton icon={<PlusOutlined />} onClick={() => {
            setIsCreateModalOpen(true)
            createForm.resetFields()
            createForm.setFieldsValue({ status: true })
            setUsernameCheckState('idle')
            setUsernameCheckMessage('')
          }}>新增用户</PagePrimaryButton>
        </Permission>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <Select
          value={params.role_id}
          style={{ width: 140 }}
          open={roleFilterOpen}
          onOpenChange={setRoleFilterOpen}
          onChange={(val) => setParams({ ...params, page: 1, role_id: val === 'all' ? 'all' : Number(val) })}
          options={[{ value: 'all', label: '所有角色' }, ...roles.map((r) => ({ value: r.id, label: r.name }))]}
        />
        <Select
          value={params.status}
          style={{ width: 140 }}
          open={statusFilterOpen}
          onOpenChange={setStatusFilterOpen}
          onChange={(val) => setParams({ ...params, page: 1, status: val === 'all' ? 'all' : Number(val) })}
          options={[{ value: 'all', label: '所有状态' }, { value: 1, label: '启用' }, { value: 0, label: '禁用' }]}
        />
        <Input id="user-search-keyword" name="userKeyword" autoComplete="off" className="pcids-list-search" prefix={<SearchOutlined />} placeholder="请输入用户名/账户" allowClear
          onPressEnter={(e: any) => setParams({ ...params, page: 1, keyword: e.target.value })} 
          onChange={(e) => setParams({ ...params, keyword: e.target.value })}
        />
      </div>
      <Table 
        columns={columns} 
        dataSource={dataSource} 
        rowKey="id" 
        loading={loading}
        tableLayout="fixed"
        scroll={{ x: 980 }}
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
          showSizeChanger: false,
          showTotal: (t) =>
            renderListPaginationTotal(t, params.page_size, (pageSize) =>
              setParams({ ...params, page: 1, page_size: pageSize }),
            ),
        }} 
      />

      {/* Create Modal */}
      <Modal title="新增用户" open={isCreateModalOpen}
        className="pcids-modal pcids-modal--form user-form-modal"
        width={640}
        maskClosable={false}
        onCancel={() => {
          setIsCreateModalOpen(false)
          createForm.resetFields()
          setUsernameCheckState('idle')
          setUsernameCheckMessage('')
        }}
        footer={[
          <div key="footer-wrapper" className="user-form-modal__footer">
            <Checkbox className="user-form-modal__continue" name="continueAdd" checked={continueAdd} onChange={e => setContinueAdd(e.target.checked)}>继续新增</Checkbox>
            <Space>
              <PageSecondaryButton onClick={() => {
                setIsCreateModalOpen(false)
                createForm.resetFields()
                setUsernameCheckState('idle')
                setUsernameCheckMessage('')
              }}>取消</PageSecondaryButton>
              <PagePrimaryButton onClick={() => createForm.submit()}>新增</PagePrimaryButton>
            </Space>
          </div>
        ]}
      >
        <Form
          className="user-form-modal__form"
          form={createForm}
          onFinish={handleCreate}
          initialValues={{ status: true }}
          scrollToFirstError
          onFinishFailed={(errorInfo) => {
            message.warning(getFirstFormErrorMessage(errorInfo))
          }}
        >
          <div className="user-form-modal__grid">
            <UserFormField
              label="用户名"
              name="display_name"
              form={createForm}
              validateTrigger="onBlur"
              rules={[{
                validator: async (_rule: any, value: any) => {
                  const error = validateDisplayNameValue(value)
                  if (error) throw new Error(error)
                },
              }]}
            >
                <Input name="display_name" autoComplete="name" placeholder="请输入用户名" />
            </UserFormField>
            <UserFormField
              label="用户账号"
              name="username"
              form={createForm}
              validateStatus={usernameCheckState === 'available' ? 'success' : usernameCheckState === 'taken' ? 'error' : usernameCheckState === 'checking' ? 'validating' : undefined}
              help={usernameCheckState === 'idle' ? undefined : usernameCheckMessage}
              rules={[{
                validator: async (_rule: any, value: any) => {
                  const error = validateAccountFormat(value)
                  if (error) throw new Error(error)
                },
              }]}
            >
                <Input name="username" autoComplete="username" placeholder="请输入账号（4~20位字母数字）" />
            </UserFormField>
          </div>
          <UserFormField
            label="密码"
            name="password"
            form={createForm}
            rules={[{
              validator: async (_rule: any, value: any) => {
                const result = evaluatePasswordStrength(value, createForm.getFieldValue('username'))
                if (!result.valid) throw new Error(result.message)
              },
            }]}
          >
            <Input.Password name="password" autoComplete="new-password" placeholder="请输入密码（8~32位，字母、数字和特殊字符至少两种组合）" />
          </UserFormField>
          <UserFormField label="角色" name="role_id" form={createForm}>
            <Select id="user-create-role" placeholder="请选择角色" options={roles.map((r) => ({ value: r.id, label: r.name }))} />
          </UserFormField>
          <UserFormField label="状态" name="status" form={createForm} valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </UserFormField>
          <UserFormField label="备注" name="remark" form={createForm}>
            <Input.TextArea name="remark" autoComplete="off" rows={3} placeholder="请输入备注信息" />
          </UserFormField>
        </Form>
      </Modal>

      {/* Edit Modal */}
      <Modal title="用户编辑" open={isEditModalOpen}
        className="pcids-modal pcids-modal--form user-form-modal"
        maskClosable={false}
        width={640}
        onOk={() => editForm.submit()}
        okText="保存"
        cancelText="取消"
        onCancel={() => setIsEditModalOpen(false)}>
        <Form
          className="user-form-modal__form"
          form={editForm}
          onFinish={handleUpdate}
          scrollToFirstError
          onFinishFailed={(errorInfo) => {
            message.warning(getFirstFormErrorMessage(errorInfo))
          }}
        >
          <div className="user-form-modal__grid">
            <UserFormField
              label="用户名"
              name="display_name"
              form={editForm}
              validateTrigger="onBlur"
              rules={[{
                validator: async (_rule: any, value: any) => {
                  const error = validateDisplayNameValue(value)
                  if (error) throw new Error(error)
                },
              }]}
            >
                <Input name="display_name" autoComplete="name" placeholder="请输入用户名" />
            </UserFormField>
            <UserFormField label="用户账号" name="username" form={editForm} rules={[{ required: true, message: '请输入用户账号' }]}>
              <Input name="username" autoComplete="username" placeholder="请输入用户账号" disabled />
            </UserFormField>
          </div>
          <UserFormField label="角色" name="role_id" form={editForm}>
            <Select id="user-edit-role" placeholder="请选择角色" options={roles.map((r) => ({ value: r.id, label: r.name }))} />
          </UserFormField>
          <UserFormField label="状态" name="status" form={editForm} valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </UserFormField>
          <UserFormField label="备注" name="remark" form={editForm}>
            <Input.TextArea name="remark" autoComplete="off" rows={3} placeholder="请输入备注信息" />
          </UserFormField>
        </Form>
      </Modal>
    </div>
  )
}

export default User
