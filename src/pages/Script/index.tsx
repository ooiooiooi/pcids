import { Table, Button, Input, Modal, Form, App as AntdApp, Select, Checkbox, Space } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useState, useEffect, useMemo, useRef } from 'react'
import { scriptApi, productApi, burnerApi } from '../../services/api'
import { Permission, usePermission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton } from '../../components/ActionButton'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'

const IDE_OPTIONS = [
  'Code Composer Studio',
  'IAR Embedded Workbench',
  'Keil uVision',
  'MPLAB',
  'STM32CubeIDE',
  'Vitis',
  'Vivado',
  'WindRiver Workbench',
]

const NONE_ASSOCIATED_IDE_VALUE = '__NONE_ASSOCIATED_IDE__'

const SCRIPT_LANG_OPTIONS = [
  { value: 'python', label: '.py(python)' },
  { value: 'shell', label: '.sh(shell)' },
  { value: 'PowerShell', label: '.ps1(Power Shell)' },
  { value: 'TCL', label: '.tcl(TCL)' },
  { value: 'nodejs', label: '.js(Node.js)' },
  { value: 'bat', label: '.bat(Windows Batch)' },
]

const TASK_TYPE_OPTIONS = [
  { value: 'board', label: '板卡烧录' },
  { value: 'os', label: '操作系统' },
  { value: 'hybrid', label: '混合协同' },
]

const TASK_TYPE_MAP: Record<string, { label: string; color: string }> = {
  board: { label: '板卡烧录', color: 'orange' },
  os: { label: '操作系统', color: 'blue' },
  hybrid: { label: '混合协同', color: 'purple' },
}

const STATUS_MAP: Record<number, { label: string; color: string }> = {
  0: { label: '启用', color: 'green' },
  2: { label: '禁用', color: 'default' },
}

const formatScriptLang = (type: string) => {
  const normalized = String(type || '')
  const map: Record<string, string> = {
    python: '.py',
    shell: '.sh',
    PowerShell: '.ps1',
    TCL: '.tcl',
    nodejs: '.js',
    bat: '.bat',
  }
  return map[normalized] || `.${normalized}`.toLowerCase()
}

const getDeviceModelLabel = (value?: string) => {
  const text = String(value || '').trim()
  if (!text) return ''
  if (text === 'SD卡写入' || text === 'SD卡文件写入') {
    return 'SD读卡器'
  }
  return text
}

const buildAssociatedBurnerOptions = (burners: any[]) => {
  const seen = new Set<string>()
  return burners.reduce((acc: Array<{ value: string; label: string }>, burner: any) => {
    const typeValue = String(burner?.type || '').trim()
    if (!typeValue || seen.has(typeValue)) {
      return acc
    }
    seen.add(typeValue)
    acc.push({ value: typeValue, label: getDeviceModelLabel(typeValue) })
    return acc
  }, [])
}

const pillBaseStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  minWidth: 38,
  height: 24,
  padding: '0 9px',
  borderRadius: 8,
  fontSize: 12,
  fontWeight: 500,
  lineHeight: '24px',
  whiteSpace: 'nowrap' as const,
}

const getLangPillStyle = (type: string) => {
  const normalized = String(type || '').toLowerCase()
  const styleMap: Record<string, { color: string; background: string }> = {
    python: { color: '#5B8FF9', background: '#DCEAFE' },
    shell: { color: '#49AA19', background: '#D9F7BE' },
    powershell: { color: '#9254DE', background: '#E9D5FF' },
    tcl: { color: '#13A8A8', background: '#D6F5F3' },
    nodejs: { color: '#2F54EB', background: '#DCE8FF' },
    bat: { color: '#5B8FF9', background: '#DCEAFE' },
  }
  return styleMap[normalized] || { color: '#5B8FF9', background: '#DCEAFE' }
}

const getTaskPillStyle = (taskType: string) => {
  const styleMap: Record<string, { color: string; background: string }> = {
    board: { color: '#FA8C16', background: '#FFF1E8' },
    os: { color: '#13A8A8', background: '#D6F5F3' },
    hybrid: { color: '#9254DE', background: '#F3E8FF' },
  }
  return styleMap[String(taskType || 'board')] || styleMap.board
}

const getSourcePillStyle = (isSystem: boolean) => (
  isSystem
    ? { color: '#5B8FF9', background: '#DCEAFE' }
    : { color: '#49AA19', background: '#D9F7BE' }
)

const getStatusPillStyle = (status: number) => (
  status === 0
    ? { color: '#3CB371', background: '#D8F5E5' }
    : { color: '#8C8C8C', background: '#F2F3F5' }
)

const normalizeMultiValue = (value?: string | string[] | null) => {
  if (Array.isArray(value)) {
    return Array.from(new Set(value.map((item) => String(item || '').trim()).filter(Boolean)))
  }
  return Array.from(new Set(String(value || '').split(/[，,;/|]+/).map((item) => item.trim()).filter(Boolean)))
}

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

const buildScriptPayload = (values: any, options?: { forceBoardTaskType?: boolean }) => {
  const nextTaskType = options?.forceBoardTaskType ? 'board' : String(values?.task_type || 'board').trim() || 'board'
  return {
    ...values,
    task_type: nextTaskType,
    associated_ide: values?.associated_ide === NONE_ASSOCIATED_IDE_VALUE ? '' : (values?.associated_ide || ''),
    associated_board: normalizeMultiValue(values?.associated_board).join(','),
    content: String(values?.content || ''),
  }
}

const escapeHtml = (value: string) =>
  String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

const highlightScriptContent = (source: string, language: string) => {
  void language
  return escapeHtml(source || '') || '&nbsp;'
}

type ScriptCodeEditorProps = {
  value?: string
  onChange?: (value: string) => void
  language?: string
  readOnly?: boolean
}

const ScriptCodeEditor: React.FC<ScriptCodeEditorProps> = ({ value, onChange, language, readOnly = false }) => {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const lineRef = useRef<HTMLDivElement | null>(null)
  const codeRef = useRef<HTMLPreElement | null>(null)
  const content = String(value || '')
  const highlightedHtml = useMemo(() => highlightScriptContent(content, String(language || '')), [content, language])
  const lineCount = Math.max(content.split('\n').length, 1)

  const syncScroll = () => {
    if (!textareaRef.current) return
    const { scrollTop, scrollLeft } = textareaRef.current
    if (lineRef.current) lineRef.current.scrollTop = scrollTop
    if (codeRef.current) {
      codeRef.current.scrollTop = scrollTop
      codeRef.current.scrollLeft = scrollLeft
    }
  }

  return (
    <div className="script-code-editor">
      <div className="script-code-editor__frame">
        <div ref={lineRef} className="script-code-editor__gutter" aria-hidden="true">
          {Array.from({ length: lineCount }, (_, index) => (
            <div key={index} className="script-code-editor__line-number">
              {index + 1}
            </div>
          ))}
        </div>
        <div className="script-code-editor__content">
          <pre
            ref={codeRef}
            className="script-code-editor__highlight"
            aria-hidden="true"
            dangerouslySetInnerHTML={{ __html: `${highlightedHtml}\n` }}
          />
          <textarea
            ref={textareaRef}
            className="script-code-editor__input"
            spellCheck={false}
            value={content}
            readOnly={readOnly}
            onChange={(event) => onChange?.(event.target.value)}
            onScroll={syncScroll}
          />
        </div>
      </div>
    </div>
  )
}

const Script: React.FC = () => {
  const { hasPermission } = usePermission()
  const { message } = AntdApp.useApp()
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isContentModalOpen, setIsContentModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({
    page: 1,
    page_size: 10,
    keyword: '',
    script_type: undefined as string | undefined,
    script_source: undefined as string | undefined,
    task_type: undefined as string | undefined,
    sort_field: 'updated_at',
    sort_order: 'desc',
  })
  const [searchKeyword, setSearchKeyword] = useState('')
  const [filterChipType, setFilterChipType] = useState<string | undefined>()
  const [filterBurner, setFilterBurner] = useState<string | undefined>()
  const [form] = Form.useForm()
  const [contentForm] = Form.useForm()
  const [contentEditingId, setContentEditingId] = useState<number | null>(null)
  const [isBasicEditOpen, setIsBasicEditOpen] = useState(false)
  const [basicEditForm] = Form.useForm()
  const [editingBasicId, setEditingBasicId] = useState<number | null>(null)
  const [isViewOpen, setIsViewOpen] = useState(false)
  const [viewForm] = Form.useForm()
  const [viewingRecord, setViewingRecord] = useState<any>(null)
  const [savingViewBindings, setSavingViewBindings] = useState(false)
  const [keepAdding, setKeepAdding] = useState(false)
  const [editingScriptType, setEditingScriptType] = useState('shell')
  const [contentModalReadOnly, setContentModalReadOnly] = useState(false)
  const [deletingScriptId, setDeletingScriptId] = useState<number | null>(null)
  const canViewScript = hasPermission('script:view')
  const canEditScript = hasPermission('script:edit')
  const ideSelectOptions = useMemo(() => ([
    { value: NONE_ASSOCIATED_IDE_VALUE, label: '不关联IDE' },
    ...IDE_OPTIONS.map((ide) => ({ value: ide, label: ide })),
  ]), [])

  const [products, setProducts] = useState<any[]>([])
  const [burners, setBurners] = useState<any[]>([])
  const associatedBurnerOptions = buildAssociatedBurnerOptions(burners)
  const productChipMap = useMemo(() => {
    return products.reduce((acc, product) => {
      if (product?.name) {
        acc[product.name] = product?.chip_type || ''
      }
      return acc
    }, {} as Record<string, string>)
  }, [products])
  const chipTypeOptions = useMemo(() => {
    return Array.from(new Set(products.map((item) => String(item?.chip_type || '').trim()).filter(Boolean)))
      .map((chipType) => ({ value: chipType, label: chipType }))
  }, [products])
  const filteredScripts = useMemo(() => {
    return dataSource.filter((item) => {
      const matchesChip = !filterChipType || productChipMap[String(item?.associated_board || '')] === filterChipType
      const matchesBurner = !filterBurner || String(item?.associated_burner || '') === filterBurner
      return matchesChip && matchesBurner
    })
  }, [dataSource, filterChipType, filterBurner, productChipMap])
  const displayTotal = filterChipType || filterBurner ? filteredScripts.length : total

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
        burnerApi.getList({ page: 1, page_size: 1000, include_runtime_status: false })
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
        ...buildScriptPayload(values, { forceBoardTaskType: true }),
        status: 0,
      }
      const res: any = await scriptApi.create(payload)
      if (res.code === 0) {
        message.success('创建成功')
        if (!keepAdding) {
          setIsModalOpen(false)
        }
        form.resetFields()
        form.setFieldsValue({ associated_ide: NONE_ASSOCIATED_IDE_VALUE })
        fetchScripts()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleEdit = async (values: any) => {
    if (!contentEditingId) return
    try {
      const res: any = await scriptApi.update(contentEditingId, { content: String(values?.content || '') })
      if (res.code === 0) {
        message.success('更新成功')
        setIsContentModalOpen(false)
        contentForm.resetFields()
        setContentEditingId(null)
        setContentModalReadOnly(false)
        fetchScripts()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDelete = async (id: number) => {
    setDeletingScriptId(id)
    try {
      const res: any = await scriptApi.delete(id)
      if (res.code === 0) {
        message.success('删除成功')
        fetchScripts()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    } finally {
      setDeletingScriptId(null)
    }
  }

  const openContentModal = async (record: any) => {
    setContentEditingId(record.id)
    setEditingScriptType(String(record?.type || 'shell'))
    setContentModalReadOnly(Boolean(record?.is_system))
    try {
      const res: any = await scriptApi.getContent(record.id)
      if (res.code === 0) {
        contentForm.setFieldsValue({ content: res.data?.content || '' })
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '脚本内容加载失败')
    }
    setIsContentModalOpen(true)
  }

  const handleBasicEdit = async (values: any) => {
    if (!editingBasicId) return
    try {
      const payload = {
        ...buildScriptPayload(values),
        status: editingBasicId ? (dataSource.find((item) => item.id === editingBasicId)?.status ?? 0) : 0,
      }
      const res: any = await scriptApi.update(editingBasicId, payload)
      if (res.code === 0) {
        message.success('更新成功')
        setIsBasicEditOpen(false)
        basicEditForm.resetFields()
        fetchScripts()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const openBasicEdit = (record: any) => {
    if (record.is_system) {
      message.info('系统级脚本由系统统一维护，当前不允许编辑')
      return
    }
    setEditingBasicId(record.id)
    basicEditForm.setFieldsValue({
      name: record.name,
      type: record.type,
      task_type: record.task_type || 'board',
      associated_ide: record.associated_ide || NONE_ASSOCIATED_IDE_VALUE,
      associated_board: normalizeMultiValue(record.associated_board),
      associated_burner: record.associated_burner,
      description: record.description,
    })
    setIsBasicEditOpen(true)
  }

  const openView = async (record: any) => {
    setViewingRecord(record)
    try {
      await fetchDependencies()
    } catch (_e) {
      // ignore; 拉取失败不阻塞弹窗打开
    }
    try {
      const detailRes: any = await scriptApi.getById(record.id)
      const detail = detailRes?.code === 0 ? (detailRes.data || record) : record
      setViewingRecord(detail)
      viewForm.setFieldsValue({
        name: detail.name,
        type: detail.type,
        task_type: detail.task_type || 'board',
        associated_ide: detail.associated_ide || NONE_ASSOCIATED_IDE_VALUE,
        associated_board: normalizeMultiValue(detail.associated_board),
        associated_burner: detail.associated_burner,
        description: detail.description,
      })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '脚本详情加载失败')
      setViewingRecord(record)
      viewForm.setFieldsValue({
        name: record.name,
        type: record.type,
        task_type: record.task_type || 'board',
        associated_ide: record.associated_ide || NONE_ASSOCIATED_IDE_VALUE,
        associated_board: normalizeMultiValue(record.associated_board),
        associated_burner: record.associated_burner,
        description: record.description,
      })
    }
    setIsViewOpen(true)
  }

  const handleViewBindingSave = async () => {
    if (!viewingRecord?.id) return
    try {
      setSavingViewBindings(true)
      await viewForm.validateFields()
      const values = viewForm.getFieldsValue()
      const payload = { associated_board: normalizeMultiValue(values?.associated_board).join(',') }
      const res: any = await scriptApi.update(viewingRecord.id, payload)
      if (res?.code === 0) {
        message.success('绑定关系已更新')
        setViewingRecord((prev: any) => prev ? ({
          ...prev,
          associated_board: payload.associated_board,
        }) : prev)
        setIsViewOpen(false)
        viewForm.resetFields()
        setViewingRecord(null)
        await fetchScripts()
      } else {
        message.error(res?.message || '绑定关系更新失败')
      }
    } catch (e: any) {
      if (e?.errorFields) {
        message.error('请检查表单填写是否正确')
      } else {
        message.error(e?.response?.data?.detail || '绑定关系更新失败')
      }
    } finally {
      setSavingViewBindings(false)
    }
  }

  const renderModifier = (_: any, record: any) => {
    return (
      <UserIdentity
        user={record?.modifier_user}
        fallbackName={record?.modified_by}
        avatarSize={23}
      />
    )
  }

  const formBody = (
    form: any,
    options: { isCreate: boolean; readOnly?: boolean; editableFields?: string[]; onFinish?: (values: any) => void; vertical?: boolean }
  ) => {
    const editableFields = new Set(options.editableFields || [])
    const isFieldDisabled = (fieldName: string) => Boolean(options.readOnly && !editableFields.has(fieldName))
    const gridClassName = options.vertical ? 'script-form-grid script-form-grid--single' : 'script-form-grid'

    return (
      <Form
        form={form}
        layout="vertical"
        colon={false}
        labelWrap
        onFinish={options.onFinish}
        scrollToFirstError
        onFinishFailed={(errorInfo) => {
          message.warning(getFirstFormErrorMessage(errorInfo))
        }}
        className={`${options.readOnly ? 'script-basic-form script-basic-form--readonly' : 'script-basic-form'} script-form-layout`}
      >
        <div className="script-form-section">
          <div className="script-form-section__title">基础信息</div>
          <div className={gridClassName}>
          <Form.Item
            className="script-form-grid__full"
            label="脚本名称"
            name="name"
            rules={options.readOnly ? undefined : [{ required: true, message: '请输入脚本名称' }]}
          >
            <Input name="name" autoComplete="off" placeholder="请输入脚本名称" disabled={isFieldDisabled('name')} />
          </Form.Item>
          <Form.Item
            label="任务类型"
            name="task_type"
            rules={options.readOnly ? undefined : [{ required: true, message: '请选择任务类型' }]}
            initialValue="board"
            required
          >
            <Select
              placeholder="请选择任务类型"
              options={options.isCreate ? [{ value: 'board', label: '板卡烧录' }] : TASK_TYPE_OPTIONS}
              disabled
            />
          </Form.Item>
          <Form.Item
            label="脚本语言"
            name="type"
            rules={options.readOnly ? undefined : [{ required: true, message: '请选择脚本语言' }]}
          >
            <Select
              placeholder="请选择脚本语言"
              options={SCRIPT_LANG_OPTIONS}
              disabled={isFieldDisabled('type')}
            />
          </Form.Item>
          <Form.Item
            label="设备型号"
            name="associated_burner"
            dependencies={['task_type']}
            required
            rules={[
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (String(getFieldValue('task_type') || 'board') !== 'board' || String(value || '').trim()) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('板卡烧录脚本必须选择设备型号'))
                },
              }),
            ]}
          >
            <Select
              className="script-form-burner-select"
              placeholder="请选择设备型号"
              allowClear
              options={associatedBurnerOptions}
              disabled={isFieldDisabled('associated_burner')}
              onOpenChange={(open) => {
                if (open && burners.length === 0) fetchDependencies()
              }}
            />
          </Form.Item>
          <Form.Item label="关联IDE" name="associated_ide">
            <Select
              placeholder="请选择关联IDE"
              allowClear
              options={ideSelectOptions}
              disabled={isFieldDisabled('associated_ide')}
            />
          </Form.Item>
          <Form.Item
            className="script-form-grid__full"
            label="关联板卡"
            name="associated_board"
            dependencies={['task_type']}
            required
            rules={[
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (String(getFieldValue('task_type') || 'board') !== 'board' || normalizeMultiValue(value).length) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('板卡烧录脚本必须选择关联板卡'))
                },
              }),
            ]}
          >
            <Select
              className="script-form-board-select"
              placeholder="请选择关联板卡"
              mode="multiple"
              allowClear
              options={products.map((p) => ({ value: p.name, label: p.name }))}
              disabled={isFieldDisabled('associated_board')}
              onOpenChange={(open) => {
                if (open && products.length === 0) fetchDependencies()
              }}
            />
          </Form.Item>
          <Form.Item className="script-form-grid__full" label="描述" name="description">
            <Input.TextArea name="description" autoComplete="off" rows={3} placeholder="脚本描述和备注信息" disabled={isFieldDisabled('description')} />
          </Form.Item>
          </div>
        </div>

      </Form>
    )
  }

  const handleSearch = () => {
    setParams((prev) => ({
      ...prev,
      keyword: searchKeyword.trim(),
      page: 1,
    }))
  }

  const columns = [
    {
      title: '脚本名称',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      render: (_: string, record: any) => (
        canViewScript
          ? (
            <Button type="link" className="script-name-link" onClick={() => openView(record)}>
              {record.name}
            </Button>
            )
          : <span>{record.name}</span>
      ),
    },
    {
      title: '脚本语言',
      dataIndex: 'type',
      key: 'type',
      width: 80,
      render: (type: string) => {
        const pillStyle = getLangPillStyle(type)
        return <span style={{ ...pillBaseStyle, color: pillStyle.color, background: pillStyle.background }}>{formatScriptLang(type)}</span>
      },
    },
    {
      title: '来源',
      dataIndex: 'is_system',
      key: 'source',
      width: 80,
      render: (isSystem: any) => {
        const pillStyle = getSourcePillStyle(Boolean(isSystem))
        return <span style={{ ...pillBaseStyle, color: pillStyle.color, background: pillStyle.background }}>{isSystem ? '内置' : '自定义'}</span>
      },
    },
    {
      title: '任务类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 100,
      render: (taskType: string) => {
        const info = TASK_TYPE_MAP[String(taskType || 'board')] || TASK_TYPE_MAP.board
        const pillStyle = getTaskPillStyle(taskType)
        return <span style={{ ...pillBaseStyle, color: pillStyle.color, background: pillStyle.background }}>{info.label}</span>
      },
    },
    {
      title: '设备型号',
      dataIndex: 'associated_burner',
      key: 'associated_burner',
      width: 120,
      render: (val: string) => getDeviceModelLabel(val) || '--',
    },
    {
      title: '关联板卡',
      dataIndex: 'associated_board',
      key: 'associated_board',
      width: 160,
      render: (val: string) => <EllipsisText value={val || '--'} />,
    },
    {
      title: '修改人',
      dataIndex: 'modified_by',
      key: 'modified_by',
      width: 140,
      render: renderModifier,
    },
    {
      title: '修改时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      sorter: true,
      width: 190,
      render: (val: string) => formatDateTime(val, '--')
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (status: number) => {
        const info = STATUS_MAP[status] || STATUS_MAP[2]
        const pillStyle = getStatusPillStyle(status)
        return <span style={{ ...pillBaseStyle, color: pillStyle.color, background: pillStyle.background }}>{info.label}</span>
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 176,
      fixed: 'right' as const,
      render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          {!record.is_system ? (
            <>
              <Permission code="script:edit">
                <ActionLinkButton onClick={() => openBasicEdit(record)}>
                  编辑
                </ActionLinkButton>
              </Permission>
              <Permission code="script:edit">
                <ActionLinkButton onClick={() => openContentModal(record)}>
                  脚本详情
                </ActionLinkButton>
              </Permission>
              <Permission code="script:delete">
                <ActionConfirm
                  title="删除脚本"
                  description={`确认删除脚本“${record.name || record.id}”吗？`}
                  okText="确认删除"
                  cancelText="取消"
                  confirmLoading={deletingScriptId === record.id}
                  onConfirm={() => handleDelete(record.id)}
                >
                  <ActionLinkButton danger>删除</ActionLinkButton>
                </ActionConfirm>
              </Permission>
            </>
          ) : (
            <Permission code="script:view">
              <ActionLinkButton onClick={() => openContentModal(record)}>
                脚本详情
              </ActionLinkButton>
            </Permission>
          )}
          {!canViewScript && record.is_system ? <span style={{ color: 'rgba(0,0,0,0.35)' }}>-</span> : null}
        </ActionButtonGroup>
      ),
    },
  ]

  return (
    <div className="script-page" style={{ height: '100%', background: '#fff', borderRadius: 6, padding: '18px 20px 16px', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
        <div className="client-page-title">
          <h1>脚本管理</h1>
          <p className="client-page-subtitle">维护烧录脚本、适配板卡关系与执行参数</p>
        </div>
        <Permission code="script:add">
          <PagePrimaryButton icon={<PlusOutlined />} onClick={() => {
            setIsModalOpen(true)
            form.setFieldsValue({ associated_ide: NONE_ASSOCIATED_IDE_VALUE })
          }}>
            新增脚本
          </PagePrimaryButton>
        </Permission>
      </div>

      <div className="script-filter-bar" style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <Select
          className="rounded-select"
          placeholder="全部芯片类型"
          style={{ width: 166 }}
          allowClear
          value={filterChipType}
          options={chipTypeOptions}
          onChange={(val) => {
            setFilterChipType(val)
            setParams((prev) => ({ ...prev, page: 1 }))
          }}
        />
        <Select
          className="rounded-select"
          placeholder="全部设备型号"
          style={{ width: 154 }}
          allowClear
          value={filterBurner}
          options={associatedBurnerOptions}
          onChange={(val) => {
            setFilterBurner(val)
            setParams((prev) => ({ ...prev, page: 1 }))
          }}
        />
        <Input
          id="script-search-keyword"
          name="scriptKeyword"
          autoComplete="off"
          className="pcids-list-search"
          placeholder="请输入脚本名称"
          allowClear
          value={searchKeyword}
          prefix={<SearchOutlined />}
          onChange={(e) => setSearchKeyword(e.target.value)}
          onPressEnter={handleSearch}
          onBlur={handleSearch}
        />
      </div>

      <div style={{ background: '#fff', borderRadius: 8 }}>
        <Table
          columns={columns}
          dataSource={filteredScripts}
          rowKey="id"
          loading={loading}
          scroll={{ x: 'max-content' }}
          onChange={(pagination, _filters, sorter: any) => {
            setParams({
              ...params,
              page: pagination.current || 1,
              page_size: pagination.pageSize || params.page_size,
              sort_field: sorter.field || 'updated_at',
              sort_order: sorter.order === 'ascend' ? 'asc' : 'desc'
            })
          }}
          pagination={{
            total: displayTotal,
            pageSize: params.page_size,
            current: params.page,
            showSizeChanger: false,
            showTotal: (t) =>
              renderListPaginationTotal(t, params.page_size, (pageSize) =>
                setParams({ ...params, page: 1, page_size: pageSize }),
              ),
          }}
          size="middle"
          bordered={false}
          style={{ marginBottom: 6 }}
        />
      </div>

      <Modal
        title="新增脚本"
        className="pcids-modal pcids-modal--form device-form-modal script-form-modal script-form-modal--create"
        open={isModalOpen}
        onOk={() => form.submit()}
        onCancel={() => {
          setIsModalOpen(false)
          form.resetFields()
          form.setFieldsValue({ associated_ide: NONE_ASSOCIATED_IDE_VALUE })
          setKeepAdding(false)
        }}
        okText="新增" cancelText="取消"
        footer={(_, { OkBtn, CancelBtn }) => (
          <div className="pcids-modal__footer-split script-create-modal__footer">
            <Checkbox className="pcids-modal__continue" name="keepAdding" checked={keepAdding} onChange={(e) => setKeepAdding(e.target.checked)}>继续新增</Checkbox>
            <Space className="pcids-modal__footer-actions" size={12}>
              <CancelBtn />
              <OkBtn />
            </Space>
          </div>
        )}
      >
        {formBody(form, { isCreate: true, onFinish: handleCreate })}
      </Modal>

      <Modal
        title={contentModalReadOnly ? '查看脚本详情' : '脚本详情'}
        className="pcids-modal pcids-modal--form pcids-modal--body-fill device-form-modal script-form-modal script-content-modal"
        open={isContentModalOpen}
        onOk={() => contentForm.submit()}
        onCancel={() => {
          setIsContentModalOpen(false)
          contentForm.resetFields()
          setContentEditingId(null)
          setContentModalReadOnly(false)
        }}
        okText="保存"
        cancelText="关闭"
        footer={(_, { OkBtn, CancelBtn }) => (
          <div className="script-detail-modal__footer">
            <Space size={12}>
              <CancelBtn />
              {contentModalReadOnly ? null : <OkBtn />}
            </Space>
          </div>
        )}
      >
        <Form form={contentForm} layout="vertical" onFinish={handleEdit}>
          <Form.Item name="content" style={{ marginBottom: 0 }}>
            <ScriptCodeEditor language={editingScriptType} readOnly={contentModalReadOnly} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑脚本"
        className="pcids-modal pcids-modal--form device-form-modal script-form-modal"
        open={isBasicEditOpen}
        onOk={() => basicEditForm.submit()}
        onCancel={() => {
          setIsBasicEditOpen(false)
          basicEditForm.resetFields()
        }}
        okText="保存" cancelText="取消"
      >
        {formBody(basicEditForm, { isCreate: false, onFinish: handleBasicEdit })}
      </Modal>

      <Modal
        title="脚本基础信息"
        className="pcids-modal pcids-modal--form device-form-modal script-form-modal script-view-modal"
        open={isViewOpen}
        onCancel={() => {
          setIsViewOpen(false)
          viewForm.resetFields()
          setViewingRecord(null)
        }}
        footer={(
          <Space>
            <Button
              onClick={() => {
                setIsViewOpen(false)
                viewForm.resetFields()
                setViewingRecord(null)
              }}
            >
              关闭
            </Button>
            {canEditScript ? (
              <Button type="primary" loading={savingViewBindings} onClick={handleViewBindingSave}>
                保存
              </Button>
            ) : null}
          </Space>
        )}
      >
        {formBody(viewForm, { isCreate: false, readOnly: true, editableFields: ['associated_board'] })}
      </Modal>
    </div>
  )
}

export default Script
