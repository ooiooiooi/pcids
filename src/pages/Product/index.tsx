import { Table, Button, Input, Modal, Form, App as AntdApp, Tag, Select, Upload, Checkbox } from 'antd'
import { PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useState, useEffect, type ReactNode } from 'react'
import { productApi } from '../../services/api'
import { Permission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import BoardDetailPanel from '../../components/BoardDetailPanel'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton } from '../../components/ActionButton'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'
import { resolveMediaUrl } from '../../utils/mediaUrl'

const BURN_INTERFACE_OPTIONS = [
  { value: 'SWD', label: 'SWD' },
  { value: 'JTAG', label: 'JTAG' },
  { value: 'CJTAG', label: 'cJTAG' },
  { value: 'UART', label: 'UART' },
  { value: 'ICSP', label: 'ICSP' },
]
const COMM_INTERFACE_OPTIONS = ['以太网', 'USB', 'CAN', 'SPI', 'RS-485', 'PCIe', 'I²C', 'UART', 'SWD', 'JTAG', 'cJTAG', 'ICSP'].map((item) => ({ value: item, label: item }))
const CHIP_TYPE_OPTIONS = ['ARM', 'PIC', 'DSP', 'FPGA', 'Altera-CPLD', '其他']

const parseMultiValue = (value?: string | null) => {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.map((item) => String(item)) : []
  } catch {
    return String(value)
      .split(/[，,;/|]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  }
}

const PRODUCT_FIELD_LABELS: Record<string, string> = {
  name: '板卡名称',
  chip_type: '芯片类型',
  chip_model: '芯片型号',
  serial_number: '序列号',
  burn_interface: '烧录接口',
  interface: '通信接口',
  config_description: '配置说明',
  usage_description: '使用说明',
  board_image: '板卡图片',
}

const PRODUCT_REQUIRED_MESSAGES: Record<string, string> = {
  name: '请输入板卡名称',
  chip_type: '请选择芯片类型',
  burn_interface: '请选择烧录接口',
  board_image: '请上传板卡图片',
}

const normalizeValidationMessage = (field: string, rawMessage?: string) => {
  const messageText = String(rawMessage || '').trim()
  if (!messageText) return '输入内容不符合要求'
  if (messageText === 'Field required') {
    return PRODUCT_REQUIRED_MESSAGES[field] || `${PRODUCT_FIELD_LABELS[field] || field}不能为空`
  }
  const maxLengthMatch = messageText.match(/at most (\d+) characters/i)
  if (maxLengthMatch) return `长度不能超过${maxLengthMatch[1]}个字符`
  const minLengthMatch = messageText.match(/at least (\d+) characters/i)
  if (minLengthMatch) return `长度不能少于${minLengthMatch[1]}个字符`
  if (/valid string/i.test(messageText)) return '请输入正确的文本内容'
  return messageText
}

const getReadableApiErrorMessage = (detail: any, fallback: string) => {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const firstItem = detail[0]
    const path = Array.isArray(firstItem?.loc) ? firstItem.loc : []
    const field = String(path[path.length - 1] || '').trim()
    const label = PRODUCT_FIELD_LABELS[field] || field
    const friendlyMessage = normalizeValidationMessage(field, firstItem?.msg)
    return label ? `${label}：${friendlyMessage}` : friendlyMessage
  }
  return fallback
}

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

type ProductFormFieldProps = {
  label: string
  name: string
  form: any
  children: ReactNode
  rules?: any[]
  help?: ReactNode | null
}

const ProductFormField = ({
  label,
  name,
  form,
  children,
  rules,
  help,
}: ProductFormFieldProps) => (
  <div className="product-board-form__field">
    <div className="product-board-form__label">
      {Array.isArray(rules) && rules.some((rule) => Boolean(rule?.required)) ? (
        <span className="product-board-form__required-mark" aria-hidden="true">*</span>
      ) : null}
      {label}
    </div>
    <Form.Item name={name} noStyle rules={rules}>
      {children}
    </Form.Item>
    <Form.Item noStyle shouldUpdate>
      {() => {
        const errors = form.getFieldError(name)
        const messageText = errors[0] || help || ''
        if (!messageText) return null
        return <div className="product-board-form__help">{messageText}</div>
      }}
    </Form.Item>
  </div>
)

const Product: React.FC = () => {
  const { message } = AntdApp.useApp()
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '', chip_type: undefined as string | undefined, sort_field: 'updated_at', sort_order: 'desc' })
  const [editingProduct, setEditingProduct] = useState<any>(null)
  const [detailProduct, setDetailProduct] = useState<any>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [searchName, setSearchName] = useState('')
  const [filterChipType, setFilterChipType] = useState<string>('全部芯片')
  const [keepAdding, setKeepAdding] = useState(false)
  const [createImageUrl, setCreateImageUrl] = useState<string>('')
  const [editImageUrl, setEditImageUrl] = useState<string>('')
  const [uploading, setUploading] = useState(false)
  const [previewImageUrl, setPreviewImageUrl] = useState('')
  const [deletingProductId, setDeletingProductId] = useState<number | null>(null)

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
      const payload: any = {
        name: String(values.name || '').trim(),
        chip_type: values.chip_type,
        chip_model: String(values.chip_model || '').trim() || undefined,
        serial_number: String(values.serial_number || '').trim() || undefined,
        burn_interface: values.burn_interface?.length ? JSON.stringify(values.burn_interface) : undefined,
        interface: values.interface?.length ? JSON.stringify(values.interface) : undefined,
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
      if (e?.errorFields) return
      if (e?.response?.status === 422) {
        applyValidationErrors(createForm, e?.response?.data?.detail, '新增板卡')
        return
      }
      message.error(getReadableApiErrorMessage(e?.response?.data?.detail, '新增板卡失败'))
    }
  }

  const handleUpdate = async (values: any) => {
    try {
      const payload: any = {
        name: String(values.name || '').trim(),
        chip_type: values.chip_type,
        chip_model: String(values.chip_model || '').trim() || undefined,
        serial_number: values.serial_number,
        burn_interface: values.burn_interface?.length ? JSON.stringify(values.burn_interface) : undefined,
        interface: values.interface?.length ? JSON.stringify(values.interface) : undefined,
        config_description: values.config_description || undefined,
        usage_description: values.usage_description || undefined,
        board_image: values.board_image || undefined,
      }
      await productApi.update(editingProduct.id, payload)
      message.success('更新成功')
      setIsEditModalOpen(false)
      fetchProducts()
    } catch (e: any) {
      if (e?.errorFields) return
      if (e?.response?.status === 422) {
        applyValidationErrors(editForm, e?.response?.data?.detail, '编辑板卡')
        return
      }
      message.error(getReadableApiErrorMessage(e?.response?.data?.detail, '编辑板卡失败'))
    }
  }

  const handleDelete = async (id: number) => {
    setDeletingProductId(id)
    try {
      await productApi.delete(id)
      message.success('删除成功')
      fetchProducts()
    } catch (e: any) {
      message.error(getReadableApiErrorMessage(e?.response?.data?.detail, '删除板卡失败'))
    } finally {
      setDeletingProductId(null)
    }
  }

  const chipColorMap: Record<string, string> = {
    ARM: 'blue', PIC: 'green', FPGA: 'purple', DSP: 'orange', 'Altera-CPLD': 'cyan', 其他: 'default',
  }

  const applyValidationErrors = (form: any, detail: any, actionText: string) => {
    if (!Array.isArray(detail)) {
      message.error(`${actionText}失败，请检查输入内容`)
      return
    }

    const fieldErrors: Array<{ name: string; errors: string[] }> = []
    const summaryMessages: string[] = []

    detail.forEach((item: any) => {
      const path = Array.isArray(item?.loc) ? item.loc : []
      const field = String(path[path.length - 1] || '').trim()
      if (!field) return
      const friendlyMessage = normalizeValidationMessage(field, item?.msg)
      fieldErrors.push({ name: field, errors: [friendlyMessage] })
      const label = PRODUCT_FIELD_LABELS[field] || field
      summaryMessages.push(`${label}：${friendlyMessage}`)
    })

    if (fieldErrors.length) {
      form.setFields(fieldErrors)
    }

    message.error(summaryMessages[0] || `${actionText}失败，请检查输入内容`)
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

  const columns = [
    { title: '板卡序列号', dataIndex: 'serial_number', key: 'serial_number', width: 160 },
    {
      title: '板卡名称',
      dataIndex: 'name',
      key: 'name',
      width: 240,
      render: (name: string, record: any) => (
        <ActionLinkButton onClick={() => { setDetailProduct(record); setIsDetailOpen(true) }}>
          <EllipsisText value={name} />
        </ActionLinkButton>
      ),
    },
    { title: '芯片类型', dataIndex: 'chip_type', key: 'chip_type', width: 120, render: (type: string) => <Tag color={chipColorMap[type] || 'default'}>{type}</Tag> },
    { title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 190, sorter: true, render: (val: string) => formatDateTime(val) },
    { title: '修改人', dataIndex: 'modified_by', key: 'modified_by', width: 140, render: renderModifier },
    {
      title: '板卡图片',
      dataIndex: 'board_image',
      key: 'board_image',
      width: 120,
      align: 'center' as const,
      render: (url: string) => {
        const imageUrl = resolveMediaUrl(url)
        return imageUrl ? (
          <div style={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
            <img
              src={imageUrl}
              alt="board"
              onClick={() => setPreviewImageUrl(imageUrl)}
              style={{ width: 56, height: 36, objectFit: 'cover', borderRadius: 4, cursor: 'pointer', border: '1px solid #f0f0f0' }}
            />
          </div>
        ) : '-'
      },
    },
    { title: '操作', key: 'action', width: 200, fixed: 'right' as const,
      render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          <Permission code="product:edit">
            <ActionLinkButton
              onClick={() => {
                setEditingProduct(record)
                editForm.setFieldsValue({
                  name: record.name,
                  chip_type: record.chip_type,
                  chip_model: record.chip_model,
                  serial_number: record.serial_number,
                  burn_interface: parseMultiValue(record.burn_interface),
                  interface: parseMultiValue(record.interface),
                  config_description: record.config_description,
                  usage_description: record.usage_description,
                  board_image: record.board_image,
                })
                setEditImageUrl(resolveMediaUrl(record.board_image))
                setIsEditModalOpen(true)
              }}
            >
              编辑
            </ActionLinkButton>
          </Permission>
          <Permission code="product:delete">
            <ActionConfirm
              title="删除板卡"
              description={`确认删除板卡“${record.name || record.id}”吗？删除后将无法恢复，且可能影响烧录任务选择板卡与关联配置展示。`}
              okText="确认删除"
              cancelText="取消"
              confirmLoading={deletingProductId === record.id}
              onConfirm={() => handleDelete(record.id)}
            >
              <ActionLinkButton danger>删除</ActionLinkButton>
            </ActionConfirm>
          </Permission>
        </ActionButtonGroup>
      ),
    },
  ]

  const uploadTo = async (file: File) => {
    setUploading(true)
    try {
      const res: any = await productApi.uploadImage(file)
      if (res.code !== 0) throw new Error(res.message || '上传失败')
      return String(res?.data?.url || '')
    } finally {
      setUploading(false)
    }
  }

  const formBody = (
    form: any,
    imageUrl: string,
    setImageUrl: (v: string) => void,
    onFinish: (v: any) => void,
    options?: { autoGenerateSerial?: boolean }
  ) => (
    <Form
      form={form}
      layout="vertical"
      colon={false}
      className="product-board-form"
      onFinish={onFinish}
      scrollToFirstError
      onFinishFailed={(errorInfo) => {
        message.warning(getFirstFormErrorMessage(errorInfo))
      }}
    >
      <div className="product-board-form__grid">
        <ProductFormField label="板卡名称" name="name" form={form} rules={[{ required: true, message: '请输入板卡名称' }]}>
          <Input name="name" autoComplete="organization-title" placeholder="请输入板卡名称" />
        </ProductFormField>
        <ProductFormField label="序列号" name="serial_number" form={form}>
          <Input name="serial_number" autoComplete="off" placeholder={options?.autoGenerateSerial ? '留空则自动生成序列号' : '请输入序列号'} />
        </ProductFormField>
        <ProductFormField label="芯片类型" name="chip_type" form={form} rules={[{ required: true, message: '请选择芯片类型' }]}>
          <Select placeholder="请选择芯片类型" options={CHIP_TYPE_OPTIONS.map((t) => ({ value: t, label: t }))} />
        </ProductFormField>
        <ProductFormField label="芯片型号" name="chip_model" form={form}>
          <Input name="chip_model" autoComplete="off" placeholder="请输入具体芯片型号" />
        </ProductFormField>
        <ProductFormField label="烧录接口" name="burn_interface" form={form} rules={[{ required: true, message: '请选择烧录接口' }]}>
          <Select mode="multiple" placeholder="请选择烧录接口" options={BURN_INTERFACE_OPTIONS} maxTagCount={2} />
        </ProductFormField>
        <ProductFormField label="通信接口" name="interface" form={form}>
          <Select mode="multiple" placeholder="请选择通信接口" options={COMM_INTERFACE_OPTIONS} maxTagCount={2} />
        </ProductFormField>
      </div>

      <ProductFormField label="配置说明" name="config_description" form={form}>
        <Input.TextArea name="config_description" autoComplete="off" placeholder="请输入内容" autoSize={{ minRows: 3, maxRows: 3 }} />
      </ProductFormField>

      <ProductFormField label="使用说明" name="usage_description" form={form}>
        <Input.TextArea name="usage_description" autoComplete="off" placeholder="请输入内容" autoSize={{ minRows: 3, maxRows: 3 }} />
      </ProductFormField>

      <ProductFormField
        label="板卡图片"
        name="board_image"
        form={form}
        help="只能上传jpg/png文件，大小不超过2M"
      >
        <div className="product-board-form__upload">
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
                setImageUrl(resolveMediaUrl(url))
                form.setFieldValue('board_image', url)
                form.setFields([{ name: 'board_image', errors: [] }])
                options.onSuccess?.({ url })
              } catch (e: any) {
                options.onError?.(e)
                message.error(getReadableApiErrorMessage(e?.response?.data?.detail, e?.message || '上传失败'))
              }
            }}
          >
            <ActionLinkButton disabled={uploading}>上传图片</ActionLinkButton>
          </Upload>
          {imageUrl ? (
            <div className="product-board-form__preview">
              <img src={resolveMediaUrl(imageUrl)} alt="board" style={{ width: 100, height: 68, objectFit: 'cover', borderRadius: 6, border: '1px solid #f0f0f0' }} />
            </div>
          ) : null}
        </div>
      </ProductFormField>
    </Form>
  )

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: '18px 20px 24px', overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, gap: 16, flexWrap: 'wrap' }}>
        <div className="client-page-title">
          <h1>产品管理</h1>
          <p className="client-page-subtitle">维护板卡型号、芯片参数、接口配置与使用说明</p>
        </div>
        <Permission code="product:add">
          <PagePrimaryButton
            icon={<PlusOutlined />}
            onClick={() => setIsCreateModalOpen(true)}
          >
            新增板卡
          </PagePrimaryButton>
        </Permission>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginBottom: 8, gap: 14, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, paddingTop: 2, paddingBottom: 2, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <Select
            className="product-filter-control"
            value={filterChipType}
            style={{ width: 164 }}
            options={[{ value: '全部芯片', label: '全部芯片' }, ...CHIP_TYPE_OPTIONS.map((t) => ({ value: t, label: t }))]}
            onChange={(val) => {
              setFilterChipType(val)
              setParams((prev) => ({ ...prev, page: 1, chip_type: val === '全部芯片' ? undefined : val }))
            }}
          />
          <Input
            id="product-search-keyword"
            name="productKeyword"
            autoComplete="off"
            className="pcids-list-search"
            placeholder="请输入板卡名称/修改人"
            allowClear
            value={searchName}
            prefix={<SearchOutlined />}
            onChange={(e) => {
              const nextValue = e.target.value
              setSearchName(nextValue)
              if (!nextValue) {
                setParams((prev) => ({ ...prev, page: 1, keyword: '' }))
              }
            }}
            onPressEnter={() => setParams({ ...params, page: 1, keyword: searchName, chip_type: filterChipType === '全部芯片' ? undefined : filterChipType })}
          />
        </div>
      </div>

      <div style={{ background: '#fff', borderRadius: 8 }}>
        <Table 
          columns={columns} 
          dataSource={dataSource} 
          rowKey="id" 
          loading={loading}
          scroll={{ x: 'max-content' }}
          onChange={(pagination, _filters, sorter: any) => {
            setParams({
              ...params,
              page: pagination.current || 1,
              page_size: pagination.pageSize || 10,
              sort_field: sorter.field || 'updated_at',
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
      </div>

      <Modal
        title="新增板卡"
        open={isCreateModalOpen}
        className="pcids-modal pcids-modal--wide product-board-modal"
        onOk={() => createForm.submit()}
        okText="新增"
        cancelText="取消"
        onCancel={() => { setIsCreateModalOpen(false); createForm.resetFields(); setCreateImageUrl(''); setKeepAdding(false) }}
        footer={(_, { OkBtn, CancelBtn }) => (
          <div className="pcids-modal__footer-split">
            <Checkbox className="pcids-modal__continue" name="keepAdding" checked={keepAdding} onChange={(e) => setKeepAdding(e.target.checked)}>继续新增</Checkbox>
            <div className="pcids-modal__footer-actions">
              <CancelBtn />
              <OkBtn />
            </div>
          </div>
        )}
      >
        {formBody(createForm, createImageUrl, setCreateImageUrl, handleCreate, { autoGenerateSerial: true })}
      </Modal>

      <Modal
        title="编辑板卡"
        open={isEditModalOpen}
        className="pcids-modal pcids-modal--wide product-board-modal"
        onOk={() => editForm.submit()}
        okText="保存"
        cancelText="取消"
        onCancel={() => { setIsEditModalOpen(false); setEditImageUrl('') }}
      >
        {formBody(editForm, editImageUrl, setEditImageUrl, handleUpdate)}
      </Modal>

      <Modal
        title="产品详情"
        open={isDetailOpen}
        onCancel={() => setIsDetailOpen(false)}
        footer={<Button type="primary" className="board-detail-close-button" onClick={() => setIsDetailOpen(false)}>关闭</Button>}
        closable={false}
        className="pcids-modal pcids-modal--wide board-detail-modal"
      >
        {detailProduct && (
          <BoardDetailPanel
            record={detailProduct}
            burnInterfaceText={parseMultiValue(detailProduct.burn_interface).join('、')}
            communicationInterfaceText={parseMultiValue(detailProduct.interface).join('、')}
            onPreviewImage={(imageUrl) => setPreviewImageUrl(String(imageUrl || ''))}
          />
        )}
      </Modal>
      <Modal className="pcids-modal pcids-modal--preview" open={Boolean(previewImageUrl)} footer={null} onCancel={() => setPreviewImageUrl('')}>
        {previewImageUrl ? <img src={previewImageUrl} alt="board-preview" style={{ width: '100%', maxHeight: '75vh', objectFit: 'contain' }} /> : null}
      </Modal>
    </div>
  )
}

export default Product
