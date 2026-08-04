import { Table, Button, Space, Input, Modal, Form, App as AntdApp, Tag, Select, Radio, Switch, Checkbox, Alert, Empty, Dropdown } from 'antd'
import { PlusOutlined, SyncOutlined, DownOutlined, SearchOutlined, DesktopOutlined, CloudServerOutlined, ApartmentOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { burnerApi } from '../../services/api'
import { consumeBackendServiceError } from '../../services/backendErrorCenter'
import { Permission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton, PageSecondaryButton } from '../../components/ActionButton'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'
import { buildBurnerNodeMetadataReset } from './formState'

const DEVICE_CATEGORY_OPTIONS = [
  { value: 'burner', label: '烧录器' },
  { value: 'sd_reader', label: 'SD读卡器' },
]

const BURNER_MODEL_OPTIONS = [
  { value: 'J-LINK', label: 'J-LINK' },
  { value: 'PWLINK2', label: 'PWLINK2' },
  { value: 'GDLINK', label: 'GDLINK' },
  { value: 'SWD下载器', label: 'SWD下载器' },
  { value: 'AL321', label: 'AL321' },
  { value: 'ST-LINK', label: 'ST-LINK' },
  { value: 'HDSC CCID', label: 'HDSC CCID' },
  { value: 'XDS510plus', label: 'XDS510plus' },
  { value: 'MPLAB ICD 3 DV164035', label: 'MPLAB ICD 3 DV164035' },
  { value: 'Altera Blaster II', label: 'Altera Blaster II' },
  { value: 'Gowin USB Cable', label: 'Gowin USB Cable' },
]

const INTERFACE_OPTIONS = [
  { value: 'SWD', label: 'SWD' },
  { value: 'JTAG', label: 'JTAG' },
  { value: 'CJTAG', label: 'cJTAG' },
  { value: 'UART', label: 'UART' },
  { value: 'ICSP', label: 'ICSP' },
]
const CHIP_OPTIONS = ['ARM', 'TI DSP', 'FPGA', 'PIC', 'CPLD'].map((item) => ({ value: item, label: item }))
const CARD_TYPE_OPTIONS = ['SD', 'MicroSD', 'SDHC', 'SDXC'].map((item) => ({ value: item, label: item }))
const DEVICE_TYPE_FILTER_OPTIONS = [
  { text: '烧录器', value: 'burner' },
  { text: 'SD读卡器', value: 'sd_reader' },
]
type DeviceTypeFilterMode = 'single' | 'multiple'
const DEVICE_TYPE_FILTER_MODE_OPTIONS = [
  { label: '单选', value: 'single' },
  { label: '多选', value: 'multiple' },
] as const
const LEGACY_BURNER_NAME_MAP: Record<string, string> = {
  'J-LINK V11': 'J-LINK',
  'J_LINK V11': 'J-LINK',
  'PWLINK V2': 'PWLINK2',
  'ST_LINK': 'ST-LINK',
  'ST-LINK V2': 'ST-LINK',
  'MPLAB ICD 3': 'MPLAB ICD 3 DV164035',
  'TI XDS510 Plus': 'XDS510plus',
}
const BURNER_INTERFACE_ORDER = INTERFACE_OPTIONS.map((item) => item.value)
const CHIP_ORDER = CHIP_OPTIONS.map((item) => item.value)
const BURNER_CAPABILITY_MAP: Record<string, { supported_interfaces: string[]; supported_chips: string[] }> = {
  J_LINK: { supported_interfaces: ['SWD', 'JTAG', 'CJTAG'], supported_chips: ['ARM'] },
  PWLINK2: { supported_interfaces: ['SWD', 'JTAG'], supported_chips: ['ARM'] },
  GDLINK: { supported_interfaces: ['SWD', 'JTAG'], supported_chips: ['ARM'] },
  SWD下载器: { supported_interfaces: ['SWD'], supported_chips: ['ARM'] },
  AL321: { supported_interfaces: ['JTAG'], supported_chips: ['FPGA'] },
  ST_LINK: { supported_interfaces: ['SWD', 'JTAG', 'CJTAG'], supported_chips: ['ARM'] },
  HDSC_CCID: { supported_interfaces: ['UART'], supported_chips: ['ARM'] },
  XDS510PLUS: { supported_interfaces: ['JTAG'], supported_chips: ['TI DSP'] },
  MPLAB_ICD_3_DV164035: { supported_interfaces: ['ICSP'], supported_chips: ['PIC'] },
  ALTERA_BLASTER_II: { supported_interfaces: ['JTAG'], supported_chips: ['FPGA', 'CPLD'] },
  ALTERA_BLASTER_Ⅱ: { supported_interfaces: ['JTAG'], supported_chips: ['FPGA', 'CPLD'] },
  GOWIN_USB_CABLE: { supported_interfaces: ['JTAG'], supported_chips: ['FPGA'] },
}

const normalizeDeviceCategoryValues = (values?: Array<string | number | boolean | null | undefined>) =>
  Array.from(new Set((values || []).map((item) => String(item || '').trim()).filter(Boolean)))

const normalizeBurnerModelKey = (value?: string) =>
  String(LEGACY_BURNER_NAME_MAP[String(value || '').trim()] || value || '')
    .trim()
    .replace(/Ⅱ/g, 'II')
    .replace(/[^A-Za-z0-9\u4e00-\u9fa5]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toUpperCase()

const sortByPresetOrder = (values: string[], order: string[]) =>
  Array.from(new Set(values))
    .filter((item) => order.includes(item))
    .sort((left, right) => order.indexOf(left) - order.indexOf(right))

const getBurnerCapabilityConfig = (deviceModel?: string) => {
  const config = BURNER_CAPABILITY_MAP[normalizeBurnerModelKey(deviceModel)]
  if (!config) {
    return { supported_interfaces: [], supported_chips: [] }
  }
  return {
    supported_interfaces: sortByPresetOrder(config.supported_interfaces, BURNER_INTERFACE_ORDER),
    supported_chips: sortByPresetOrder(config.supported_chips, CHIP_ORDER),
  }
}

const requiresPhysicalPortStrategy = (deviceModel?: string) => normalizeBurnerModelKey(deviceModel) === 'XDS510PLUS'

const parseBurnerConfig = (configJson?: string) => {
  try {
    return JSON.parse(configJson || '{}') || {}
  } catch {
    return {}
  }
}

const normalizeAgentAddress = (value: any) => String(value || '').trim().replace(/\/+$/, '').toLowerCase()

const inferDeviceCategory = (record: any) => {
  const config = parseBurnerConfig(record?.config_json)
  if (config.device_category === 'sd_reader' || record?.type === 'SD卡文件写入' || String(record?.name || '').includes('SD')) {
    return 'sd_reader'
  }
  return 'burner'
}

const buildFormValues = (record?: any) => {
  if (!record) {
    return {
      device_category: 'burner',
      device_model: undefined,
      supported_interfaces: [],
      supported_chips: [],
      supported_card_types: [],
      mount_path: '',
      host_type: 'local',
      agent_url: '',
      is_enabled: true,
      usb_binding: {},
    }
  }

  const config = parseBurnerConfig(record.config_json)
  const deviceCategory = inferDeviceCategory(record)
  const deviceModel = deviceCategory === 'burner' ? (config.device_model || record.type) : undefined
  const burnerCapability = getBurnerCapabilityConfig(deviceModel)
  return {
    ...record,
    device_category: deviceCategory,
    device_model: deviceModel,
    supported_interfaces: deviceCategory === 'burner' ? (burnerCapability.supported_interfaces.length ? burnerCapability.supported_interfaces : (config.supported_interfaces || [])) : [],
    supported_chips: deviceCategory === 'burner' ? (burnerCapability.supported_chips.length ? burnerCapability.supported_chips : (config.supported_chips || [])) : [],
    supported_card_types: config.supported_card_types || [],
    mount_path: config.mount_path || '',
    usb_binding: config.usb_binding || {},
    host_type: record.host_type || (record.agent_url ? 'agent' : 'local'),
    host_name: record.host_name || '',
    host_address: record.host_address || '',
    agent_url: record.agent_url || '',
    is_enabled: record.is_enabled ?? true,
  }
}

const buildBurnerPayload = (values: any, strategy: number) => {
  const deviceCategory = values.device_category || 'burner'
  const deviceModel = deviceCategory === 'burner' ? values.device_model : 'SD卡文件写入'
  const runtimeStrategy = deviceCategory === 'sd_reader' ? 1 : (requiresPhysicalPortStrategy(deviceModel) ? 2 : strategy)
  const burnerCapability = getBurnerCapabilityConfig(deviceModel)
  const config = {
    device_category: deviceCategory,
    device_model: deviceCategory === 'burner' ? values.device_model || '' : '',
    supported_interfaces: deviceCategory === 'burner' ? burnerCapability.supported_interfaces : [],
    supported_chips: deviceCategory === 'burner' ? burnerCapability.supported_chips : [],
    supported_card_types: deviceCategory === 'sd_reader' ? values.supported_card_types || [] : [],
    mount_path: deviceCategory === 'sd_reader' ? values.mount_path || '' : '',
    usb_binding: deviceCategory === 'burner' ? values.usb_binding || {} : {},
  }

  return {
    name: values.name || deviceModel,
    type: deviceModel,
    location: values.location,
    strategy: runtimeStrategy,
    sn: deviceCategory === 'burner' && runtimeStrategy === 1 ? values.sn : '',
    // SN 绑定也保留首次扫描到的物理端口，用于后续扫描判断设备是否换位。
    port: values.port || '',
    is_enabled: values.is_enabled,
    description: values.description,
    agent_url: values.host_type === 'agent' ? (values.agent_url || '') : '',
    host_type: values.host_type || (values.agent_url ? 'agent' : 'local'),
    host_name: values.host_name || '',
    host_address: values.host_address || '',
    config_json: JSON.stringify(config),
  }
}

const getDeviceTypeInfo = (record: any) => {
  const deviceCategory = inferDeviceCategory(record)
  if (deviceCategory === 'sd_reader') {
    return { label: 'SD读卡器', color: 'blue' }
  }
  return { label: '烧录器', color: 'purple' }
}

const getNodeDisplayInfo = (record: any) => {
  const hostType = String(record?.host_type || '').trim().toLowerCase()
  const displayLabel = String(record?.node_display_label || '').trim()
  if (displayLabel) {
    if (record?.node_is_local) {
      return { label: displayLabel, color: '#eef2ff', textColor: '#4b6bfb' }
    }
    if (hostType === 'server') {
      return { label: displayLabel, color: '#eaf5e7', textColor: '#5aa03f' }
    }
    if (hostType === 'agent' || record?.agent_url) {
      return { label: displayLabel, color: '#eaf5e7', textColor: '#5aa03f' }
    }
    return { label: displayLabel, color: '#eef2ff', textColor: '#4b6bfb' }
  }
  if (record?.host_name) {
    return { label: record.host_name, color: '#eaf5e7', textColor: '#5aa03f' }
  }
  if (record?.host_address) {
    return { label: record.host_address, color: '#eef2ff', textColor: '#4b6bfb' }
  }
  if (hostType === 'server') {
    return { label: '服务器', color: '#eaf5e7', textColor: '#5aa03f' }
  }
  if (record?.agent_url) {
    try {
      const hostname = new URL(record.agent_url).hostname || '局域网节点'
      return { label: hostname, color: '#eaf5e7', textColor: '#5aa03f' }
    } catch {
      return { label: '局域网节点', color: '#eaf5e7', textColor: '#5aa03f' }
    }
  }
  return { label: '本地', color: '#eef2ff', textColor: '#4b6bfb' }
}

const getBindingDisplay = (record: any) => {
  const config = parseBurnerConfig(record?.config_json)
  if (inferDeviceCategory(record) === 'sd_reader') {
    return config.mount_path || record?.sn || record?.port || '-'
  }
  return record?.strategy === 1 ? (record?.sn || '-') : (record?.port || '-')
}

const LOCATION_MODE_OPTIONS = [
  {
    value: 'local',
    title: '本地',
    description: '连接在当前操作终端 PC 上',
    icon: DesktopOutlined,
    tip: '设备连接在当前操作机器上，系统将直接调用本机设备驱动，离线模式下可用',
    tipColor: '#43b85c',
    tipBackground: '#f6ffed',
    tipBorder: '#b7eb8f',
  },
  {
    value: 'server',
    title: '服务器',
    description: '连接在系统服务端主机上',
    icon: CloudServerOutlined,
    tip: '设备连接在当前服务所在主机上，扫描和执行均由服务端完成',
    tipColor: '#52c41a',
    tipBackground: '#f6ffed',
    tipBorder: '#b7eb8f',
  },
  {
    value: 'agent',
    title: '局域网其他节点',
    description: '连接在局域网内其他机器上',
    icon: ApartmentOutlined,
    tip: '需要填写下位机代理地址，系统将通过代理扫描并调用远端设备',
    tipColor: '#4f6ef7',
    tipBackground: '#eef2ff',
    tipBorder: '#c7d2fe',
  },
]

const EMPTY_DISCOVERY_RESULT = {
  scope: 'local',
  nodes: [],
  changed_bindings: [],
  unregistered_devices: [],
  probe_only_devices: [],
  total_scanned: 0,
  total_probe_only: 0,
}

type ScanSelectState = {
  open: boolean
  mode: 'create' | 'edit'
  strategy: number
  field: 'sn' | 'port'
  options: any[]
  value?: string
}
const renderDiscoveryText = (value?: string | null, fallback = '-') => {
  const text = String(value || '').trim()
  if (!text) return fallback
  const displayText = text.length > 20 ? `${text.slice(0, 20)}...` : text
  return <EllipsisText value={displayText} title={text} />
}

const buildDiscoveryCreateValues = (candidate: any) => {
  const isProbeOnly = Boolean(candidate?.probe_only)
  const deviceCategory = candidate?.device_category === 'sd_reader' ? 'sd_reader' : 'burner'
  const lockedHostType = ['local', 'server', 'agent'].includes(String(candidate?.node_type || '').trim().toLowerCase())
    ? String(candidate?.node_type || '').trim().toLowerCase()
    : (candidate?.agent_url ? 'agent' : 'local')
  return {
    ...buildFormValues(),
    device_category: deviceCategory,
    device_model: deviceCategory === 'burner' && !isProbeOnly ? candidate?.type : undefined,
    name: candidate?.detected_name || candidate?.type || '',
    host_type: lockedHostType,
    host_name: candidate?.host_name || '',
    host_address: candidate?.host_address || '',
    agent_url: candidate?.agent_url || '',
    locked_host_type: lockedHostType,
    sn: candidate?.sn || '',
    port: candidate?.port || '',
    usb_binding: candidate?.usb_binding || {},
    is_enabled: true,
  }
}

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

const Burner: React.FC = () => {
  const { message, modal } = AntdApp.useApp()
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [isDiscoveryModalOpen, setIsDiscoveryModalOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [updating, setUpdating] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [listError, setListError] = useState('')
  const [deviceTypeFilterMode, setDeviceTypeFilterMode] = useState<DeviceTypeFilterMode>('multiple')
  const [params, setParams] = useState({
    page: 1,
    page_size: 10,
    keyword: '',
    status: undefined as number | undefined,
    node_scope: undefined as string | undefined,
    device_categories: undefined as string | undefined,
    sort_field: 'updated_at',
    sort_order: 'desc',
  })
  const [keywordInput, setKeywordInput] = useState('')
  const [editingBurner, setEditingBurner] = useState<any>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [keepAdding, setKeepAdding] = useState(false)
  const [createStrategy, setCreateStrategy] = useState(1)
  const [editStrategy, setEditStrategy] = useState(1)
  const [scanLoading, setScanLoading] = useState<'create-sn' | 'create-port' | 'edit-sn' | 'edit-port' | null>(null)
  const [discoveryScope, setDiscoveryScope] = useState<'local' | 'all'>('local')
  const [discoveryLoading, setDiscoveryLoading] = useState(false)
  const [discoveryResult, setDiscoveryResult] = useState<any>(EMPTY_DISCOVERY_RESULT)
  const [discoveryActionKey, setDiscoveryActionKey] = useState<string | null>(null)
  const [deletingBurnerId, setDeletingBurnerId] = useState<number | null>(null)
  const [scanSelect, setScanSelect] = useState<ScanSelectState>({
    open: false,
    mode: 'create',
    strategy: 1,
    field: 'sn',
    options: [],
    value: undefined,
  })
  const createDeviceCategory = Form.useWatch('device_category', createForm) || 'burner'
  const editDeviceCategory = Form.useWatch('device_category', editForm) || 'burner'
  const createDeviceModel = Form.useWatch('device_model', createForm)
  const editDeviceModel = Form.useWatch('device_model', editForm)
  const createHostType = Form.useWatch('host_type', createForm) || 'local'
  const editHostType = Form.useWatch('host_type', editForm) || 'local'
  const createLockedHostType = Form.useWatch('locked_host_type', createForm) || ''
  const editLockedHostType = Form.useWatch('locked_host_type', editForm) || ''
  const createIsEnabled = Form.useWatch('is_enabled', createForm) !== false
  const editIsEnabled = Form.useWatch('is_enabled', editForm) !== false

  useEffect(() => { fetchBurners() }, [params])

  useEffect(() => {
    if (!isDiscoveryModalOpen) return undefined
    const frameId = window.requestAnimationFrame(() => {
      document.querySelectorAll<HTMLElement>('.device-discovery-table .ant-table-cell').forEach((cell) => {
        const text = (cell.innerText || '').trim()
        if (text) cell.title = text
      })
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [isDiscoveryModalOpen, discoveryResult])

  useEffect(() => {
    if (createDeviceCategory !== 'burner') {
      createForm.setFieldsValue({ supported_interfaces: [], supported_chips: [], sn: undefined, port: undefined })
      return
    }
    const capability = getBurnerCapabilityConfig(createDeviceModel)
    createForm.setFieldsValue({
      supported_interfaces: capability.supported_interfaces,
      supported_chips: capability.supported_chips,
    })
  }, [createDeviceCategory, createDeviceModel, createForm])

  useEffect(() => {
    if (createDeviceCategory === 'burner' && requiresPhysicalPortStrategy(createDeviceModel)) {
      setCreateStrategy(2)
      createForm.setFieldsValue({ sn: undefined })
    }
  }, [createDeviceCategory, createDeviceModel, createForm])

  useEffect(() => {
    if (editDeviceCategory !== 'burner') {
      editForm.setFieldsValue({ supported_interfaces: [], supported_chips: [], sn: undefined, port: undefined })
      return
    }
    const capability = getBurnerCapabilityConfig(editDeviceModel)
    editForm.setFieldsValue({
      supported_interfaces: capability.supported_interfaces,
      supported_chips: capability.supported_chips,
    })
  }, [editDeviceCategory, editDeviceModel, editForm])

  useEffect(() => {
    if (editDeviceCategory === 'burner' && requiresPhysicalPortStrategy(editDeviceModel)) {
      setEditStrategy(2)
      editForm.setFieldsValue({ sn: undefined })
    }
  }, [editDeviceCategory, editDeviceModel, editForm])

  const fetchBurners = async (options?: { includeRuntimeStatus?: boolean }) => {
    const includeRuntimeStatus = Boolean(options?.includeRuntimeStatus)
    setLoading(true)
    setListError('')
    try {
      const res: any = await burnerApi.getList({
        ...params,
        include_runtime_status: includeRuntimeStatus,
      })
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch (error: any) {
      if (consumeBackendServiceError(error)) {
        setListError('')
        return
      }
      const detail = String(error?.response?.data?.detail || '').trim()
      const fallbackMessage = detail || '设备列表加载失败，请稍后重试'
      setListError(fallbackMessage)
      message.error(fallbackMessage)
    }
    finally { setLoading(false) }
  }

  useEffect(() => {
    return undefined
  }, [])

  const getBurnerSubmitErrorDetail = (error: any, fallback: string) =>
    String(error?.response?.data?.detail || fallback)

  const isPhysicalPortConflictError = (error: any, payload?: any) => {
    const detail = String(error?.response?.data?.detail || '').replace(/\s+/g, '')
    const isPortStrategySubmit = Number(payload?.strategy || 1) === 2 && String(payload?.port || '').trim()
    const errorCode = String(
      error?.response?.headers?.['x-pcids-error-code']
      || error?.response?.headers?.['X-PCIDS-Error-Code']
      || ''
    ).trim()
    const isSnConflict = detail.includes('SN')
    if (error?.response?.status === 409 && errorCode === 'BURNER_PORT_BOUND') {
      return true
    }
    return error?.response?.status === 409
      && isPortStrategySubmit
      && !isSnConflict
      && detail.includes('位置')
      && detail.includes('设备')
      && (detail.includes('绑定') || detail.includes('占用'))
  }

  const finishCreateSuccess = async () => {
    message.success('创建成功')
    if (!keepAdding) {
      setIsCreateModalOpen(false)
    }
    createForm.resetFields()
    createForm.setFieldsValue(buildFormValues())
    setCreateStrategy(1)
    await fetchBurners()
  }

  const finishUpdateSuccess = async () => {
    message.success('更新成功')
    setIsEditModalOpen(false)
    await fetchBurners()
  }

  const confirmForceRebind = (detail: string, onConfirm: () => Promise<void>) =>
    modal.confirm({
      title: '物理端口已被绑定',
      content: (
        <div>
          <div>{detail}</div>
          <div style={{ marginTop: 8 }}>该物理端口已被某个烧录器绑定，是否换绑到当前设备？确认后会清除原烧录器的物理端口，并把该端口绑定到当前设备。</div>
        </div>
      ),
      okText: '确定换绑',
      cancelText: '取消',
      centered: true,
      onOk: async () => {
        try {
          await onConfirm()
        } catch (error: any) {
          message.error(getBurnerSubmitErrorDetail(error, '转绑保存失败'))
          throw error
        }
      },
    })

  const handleCreate = async (values: any) => {
    const payload = buildBurnerPayload(values, createStrategy)
    setCreating(true)
    try {
      await burnerApi.create(payload, { skipAutoErrorMessage: true } as any)
      await finishCreateSuccess()
    } catch (e: any) {
      if (e?.errorFields) return
      if (isPhysicalPortConflictError(e, payload)) {
        const detail = getBurnerSubmitErrorDetail(e, '当前物理位置已被其他设备绑定')
        confirmForceRebind(detail, async () => {
          await burnerApi.create({ ...payload, force_rebind_port: true }, { skipAutoErrorMessage: true } as any)
          await finishCreateSuccess()
        })
        return
      }
      message.error(getBurnerSubmitErrorDetail(e, '创建失败'))
    } finally {
      setCreating(false)
    }
  }

  const handleUpdate = async (values: any) => {
    const payload = buildBurnerPayload(values, editStrategy)
    setUpdating(true)
    try {
      await burnerApi.update(editingBurner.id, payload, { skipAutoErrorMessage: true } as any)
      await finishUpdateSuccess()
    } catch (e: any) {
      if (e?.errorFields) return
      if (isPhysicalPortConflictError(e, payload)) {
        const detail = getBurnerSubmitErrorDetail(e, '当前物理位置已被其他设备绑定')
        confirmForceRebind(detail, async () => {
          await burnerApi.update(editingBurner.id, { ...payload, force_rebind_port: true }, { skipAutoErrorMessage: true } as any)
          await finishUpdateSuccess()
        })
        return
      }
      message.error(getBurnerSubmitErrorDetail(e, '更新失败'))
    } finally {
      setUpdating(false)
    }
  }

  const openCreateModal = (candidate?: any) => {
    createForm.resetFields()
    createForm.setFieldsValue(candidate ? buildDiscoveryCreateValues(candidate) : buildFormValues())
    setCreateStrategy(candidate ? (candidate?.device_category === 'sd_reader' ? 1 : (candidate?.sn ? 1 : 2)) : 1)
    setKeepAdding(false)
    setIsCreateModalOpen(true)
  }

  const runDiscovery = async (scope = discoveryScope, showMessage = true) => {
    setDiscoveryLoading(true)
    try {
      const res: any = await burnerApi.discovery({ scope })
      if (res?.code === 0) {
        setDiscoveryResult(res?.data || EMPTY_DISCOVERY_RESULT)
        const statusById = new Map(
          (res?.data?.status_updates || []).map((item: any) => [Number(item?.id), Number(item?.status)]),
        )
        setDataSource((current) => current.map((item: any) => (
          statusById.has(Number(item?.id))
            ? { ...item, status: statusById.get(Number(item?.id)) }
            : item
        )))
        if (showMessage) {
          message.success(res?.message || '扫描完成')
        }
        await fetchBurners()
      }
    } catch (error: any) {
      message.error('设备扫描失败，请稍后重试')
    } finally {
      setDiscoveryLoading(false)
    }
  }

  const handleDiscoveryCreate = (candidate: any) => {
    setIsDiscoveryModalOpen(false)
    openCreateModal(candidate)
  }

  const handleUpdateBinding = async (item: any) => {
    const currentBinding = item?.current_binding || {}
    const existingRecord = dataSource.find((record: any) => Number(record?.id) === Number(item?.burner_id))
    const nextConfig = {
      ...parseBurnerConfig(existingRecord?.config_json || item?.burner_config_json),
      usb_binding: currentBinding?.usb_binding || {},
    }
    const updatePayload = {
      sn: currentBinding?.sn || null,
      port: currentBinding?.port || null,
      agent_url: currentBinding?.agent_url || null,
      host_type: ['local', 'server', 'agent'].includes(String(currentBinding?.node_type || '').trim().toLowerCase())
        ? String(currentBinding?.node_type || '').trim().toLowerCase()
        : (currentBinding?.agent_url ? 'agent' : 'local'),
      host_name: currentBinding?.host_name || null,
      host_address: currentBinding?.host_address || null,
      config_json: JSON.stringify(nextConfig),
    }
    setDiscoveryActionKey(`bind-${item?.burner_id}`)
    try {
      await burnerApi.update(item?.burner_id, updatePayload, { skipAutoErrorMessage: true } as any)
      message.success('绑定已更新')
      await runDiscovery(discoveryScope, false)
    } catch (error: any) {
      if (isPhysicalPortConflictError(error, { ...updatePayload, strategy: item?.strategy })) {
        const detail = getBurnerSubmitErrorDetail(error, '当前物理位置已被其他设备绑定')
        confirmForceRebind(detail, async () => {
          await burnerApi.update(
            item?.burner_id,
            { ...updatePayload, force_rebind_port: true },
            { skipAutoErrorMessage: true } as any,
          )
          message.success('绑定已更新，原设备位置已解除')
          await runDiscovery(discoveryScope, false)
        })
      } else {
        message.error(getBurnerSubmitErrorDetail(error, '更新绑定失败，请稍后重试'))
      }
    } finally {
      setDiscoveryActionKey(null)
    }
  }

  const handleDelete = async (id: number) => {
    setDeletingBurnerId(id)
    try {
      await burnerApi.delete(id, { skipAutoErrorMessage: true } as any)
      message.success('删除成功')
      fetchBurners()
    } catch (error: any) {
      const detail = String(error?.response?.data?.detail || '').trim()
      message.error(detail || '删除失败，请稍后重试')
    } finally {
      setDeletingBurnerId(null)
    }
  }

  const closeCreateModal = () => {
    setIsCreateModalOpen(false)
    createForm.resetFields()
    createForm.setFieldsValue(buildFormValues())
    setCreateStrategy(1)
    setKeepAdding(false)
  }

  const closeEditModal = () => {
    setIsEditModalOpen(false)
    editForm.resetFields()
  }

  const buildScanOptions = (candidates: any[], type: string, field: 'sn' | 'port') => {
    const seen = new Set<string>()
    const burnerCandidates = (candidates || []).filter((candidate) => candidate?.device_category !== 'sd_reader')
    const typedCandidates = burnerCandidates.filter(
      (candidate) => !candidate?.probe_only && String(candidate?.type || '') === String(type || ''),
    )
    const compatibleProbeCandidates = burnerCandidates.filter((candidate) => (
      candidate?.probe_only
      && Array.isArray(candidate?.possible_types)
      && candidate.possible_types.some((possibleType: string) => String(possibleType || '') === String(type || ''))
    ))
    const selectableCandidates = typedCandidates.length
      ? typedCandidates
      : compatibleProbeCandidates
    return selectableCandidates
      .map((candidate) => ({
        ...candidate,
        value: String(candidate?.[field] || '').trim(),
      }))
      .filter((candidate) => candidate.value)
      .filter((candidate) => {
        if (seen.has(candidate.value)) return false
        seen.add(candidate.value)
        return true
      })
  }

  const getScanDeviceId = (candidate: any) => {
    const binding = candidate?.usb_binding || {}
    return String(
      binding.pnp_device_id
      || binding.container_id
      || candidate?.candidate_id
      || candidate?.raw_port
      || '',
    ).trim()
  }

  const applyScanSelection = (state: ScanSelectState, value?: string) => {
    const nextValue = String(value || state.value || '').trim()
    if (!nextValue) {
      message.warning(state.field === 'sn' ? '请选择 SN 标识码' : '请选择物理端口')
      return
    }
    const form = state.mode === 'create' ? createForm : editForm
    const selected = state.options.find((item) => item.value === nextValue)
    form.setFieldsValue({
      [state.field]: nextValue,
      port: selected?.port || form.getFieldValue('port'),
      usb_binding: selected?.usb_binding || form.getFieldValue('usb_binding') || {},
      agent_url: selected?.agent_url || form.getFieldValue('agent_url'),
      host_type: selected?.node_type || (selected?.agent_url ? 'agent' : form.getFieldValue('host_type')),
      host_name: selected?.host_name || '',
      host_address: selected?.host_address || '',
      name: form.getFieldValue('name') || selected?.detected_name || selected?.type || '',
    })
    setScanSelect((prev) => ({ ...prev, open: false, value: undefined }))
    message.success(state.field === 'sn' ? '已选择 SN 标识码' : '已选择物理端口')
  }

  const handleScan = async (mode: 'create' | 'edit', strategy: number) => {
    const form = mode === 'create' ? createForm : editForm
    const deviceCategory = form.getFieldValue('device_category') || 'burner'
    const type = deviceCategory === 'sd_reader' ? 'SD卡文件写入' : form.getFieldValue('device_model')
    const hostType = form.getFieldValue('host_type')
    const agentUrl = String(form.getFieldValue('agent_url') || '').trim()
    const runtimeStrategy = deviceCategory === 'sd_reader' ? 1 : strategy
    if (!type) {
      message.warning('请先选择设备型号')
      return
    }
    if (hostType === 'agent' && !agentUrl) {
      message.warning('请先填写下位机 Agent 地址')
      return
    }
    const loadingKey = `${mode}-${runtimeStrategy === 1 ? 'sn' : 'port'}` as typeof scanLoading
    setScanLoading(loadingKey)
    try {
      const field = runtimeStrategy === 1 ? 'sn' : 'port'
      const discoveryRes: any = await burnerApi.discovery({
        scope: agentUrl ? 'all' : 'local',
        editing_burner_id: mode === 'edit' ? editingBurner?.id : undefined,
        agent_url: agentUrl || undefined,
      })
      const candidates = mode === 'edit'
        ? [...(discoveryRes?.data?.selectable_devices || []), ...(discoveryRes?.data?.probe_only_devices || [])]
        : [...(discoveryRes?.data?.unregistered_devices || []), ...(discoveryRes?.data?.probe_only_devices || [])]
      const requestedNodeCandidates = candidates.filter((candidate: any) => {
        if (hostType === 'agent') {
          return normalizeAgentAddress(candidate?.agent_url) === normalizeAgentAddress(agentUrl)
        }
        return !normalizeAgentAddress(candidate?.agent_url)
      })
      const options = buildScanOptions(requestedNodeCandidates, type, field)
      if (options.length === 1) {
        applyScanSelection({
          open: false,
          mode,
          strategy: runtimeStrategy,
          field,
          options,
          value: options[0].value,
        })
      } else if (options.length > 1) {
        setScanSelect({
          open: true,
          mode,
          strategy: runtimeStrategy,
          field,
          options,
          value: options[0]?.value,
        })
      } else {
        message.warning(field === 'sn' ? '未检测到可用的未登记 SN 标识码' : '未检测到可用的未登记物理端口')
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '设备扫描失败，请稍后重试')
    } finally {
      setScanLoading(null)
    }
  }

  const statusFilterOptions = [
    { value: 0, label: '空闲' },
    { value: 1, label: '离线' },
    { value: 2, label: '占用' },
    { value: 3, label: '禁用' },
  ]

  const nodeFilterOptions = [
    { value: 'all', label: '全部节点' },
    { value: 'local', label: '本地节点' },
    { value: 'server', label: '服务器节点' },
    { value: 'agent', label: '代理节点' },
  ]

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: '#edf9ee', text: '空闲' },
    1: { color: '#f3f3f3', text: '离线' },
    2: { color: '#fff5df', text: '占用' },
    3: { color: '#fff1f0', text: '禁用' },
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

  const currentDeviceTypeFilters = normalizeDeviceCategoryValues((params.device_categories || '').split(','))
  const applyDeviceCategoryFilter = (values: string[]) => {
    const normalized = normalizeDeviceCategoryValues(values)
    setParams({
      ...params,
      page: 1,
      device_categories: normalized.length ? normalized.join(',') : undefined,
    })
  }

  const columns = [
    { title: '设备名称', dataIndex: 'name', key: 'name', width: 170 },
    {
      title: '设备类型',
      key: 'device_type',
      width: 110,
      filteredValue: currentDeviceTypeFilters.length ? currentDeviceTypeFilters : null,
      filterDropdown: ({ selectedKeys, setSelectedKeys, confirm }: any) => {
        const normalizedSelectedKeys = normalizeDeviceCategoryValues(selectedKeys as string[])
        const nextSelectedKeys = (values: string[]) => {
          const normalized = normalizeDeviceCategoryValues(values)
          setSelectedKeys(normalized)
        }
        const handleModeChange = (mode: DeviceTypeFilterMode) => {
          setDeviceTypeFilterMode(mode)
          if (mode === 'single' && normalizedSelectedKeys.length > 1) {
            nextSelectedKeys([normalizedSelectedKeys[0]])
          }
        }
        const handleOptionToggle = (value: string) => {
          if (deviceTypeFilterMode === 'single') {
            nextSelectedKeys([value])
            return
          }
          const nextValues = normalizedSelectedKeys.includes(value)
            ? normalizedSelectedKeys.filter((item) => item !== value)
            : [...normalizedSelectedKeys, value]
          nextSelectedKeys(nextValues)
        }
        const handleApply = () => {
          applyDeviceCategoryFilter(normalizedSelectedKeys)
          confirm()
        }
        const handleClear = () => {
          nextSelectedKeys([])
        }
        const handleReset = () => {
          setDeviceTypeFilterMode('multiple')
          nextSelectedKeys([])
          applyDeviceCategoryFilter([])
          confirm()
        }
        return (
          <div style={{ width: 'min(280px, calc(100vw - 32px))', padding: 12 }}>
            <div style={{ marginBottom: 12 }}>
              <div style={{ marginBottom: 8, fontSize: 12, color: '#8c8c8c' }}>筛选模式</div>
              <Radio.Group
                size="small"
                optionType="button"
                buttonStyle="solid"
                value={deviceTypeFilterMode}
                options={DEVICE_TYPE_FILTER_MODE_OPTIONS as any}
                onChange={(event) => handleModeChange(event.target.value)}
              />
            </div>
            <div style={{ display: 'grid', gap: 8, marginBottom: 12 }}>
              {DEVICE_TYPE_FILTER_OPTIONS.map((item) => (
                <label
                  key={item.value}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 10px',
                    borderRadius: 8,
                    border: normalizedSelectedKeys.includes(item.value) ? '1px solid #4f6ef7' : '1px solid #f0f0f0',
                    background: normalizedSelectedKeys.includes(item.value) ? '#f5f8ff' : '#fff',
                    cursor: 'pointer',
                  }}
                >
                  {deviceTypeFilterMode === 'single' ? (
                    <Radio checked={normalizedSelectedKeys.includes(item.value)} onChange={() => handleOptionToggle(item.value)} />
                  ) : (
                    <Checkbox checked={normalizedSelectedKeys.includes(item.value)} onChange={() => handleOptionToggle(item.value)} />
                  )}
                  <span style={{ fontSize: 14, color: '#1f1f1f' }}>{item.text}</span>
                </label>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <Button size="small" onClick={handleClear}>清空</Button>
              <Space size={8} wrap>
                <Button size="small" onClick={handleReset}>重置</Button>
                <Button size="small" type="primary" onClick={handleApply}>确定</Button>
              </Space>
            </div>
          </div>
        )
      },
      render: (_: any, record: any) => {
        const info = getDeviceTypeInfo(record)
        return <Tag color={info.color} style={{ borderRadius: 10, paddingInline: 8 }}>{info.label}</Tag>
      },
    },
    { 
      title: 'SN/物理端口/挂载', 
      key: 'sn_port', 
      width: 230,
      render: (_: any, record: any) => getBindingDisplay(record)
    },
    {
      title: '节点位置',
      key: 'node_position',
      width: 130,
      render: (_: any, record: any) => {
        const nodeInfo = getNodeDisplayInfo(record)
        return (
          <span style={{ background: nodeInfo.color, color: nodeInfo.textColor, borderRadius: 10, padding: '2px 10px', display: 'inline-block', lineHeight: '20px' }}>
            {nodeInfo.label}
          </span>
        )
      },
    },
    { title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 190, sorter: true, render: (val: string) => formatDateTime(val) },
    { title: '修改人', dataIndex: 'modified_by', key: 'modified_by', width: 140, render: renderModifier },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (s: number, record: any) => {
      // In the prototype, if it's disabled, it should be shown as disabled
      if (record.is_enabled === false) {
        return <span style={{ background: '#fff1f0', color: '#ff4d4f', borderRadius: 10, padding: '2px 10px', display: 'inline-block', lineHeight: '20px' }}>禁用</span>
      }
      const statusInfo = statusMap[s] || statusMap[1]
      const textColorMap: Record<number, string> = {
        0: '#52c41a',
        1: '#8c8c8c',
        2: '#faad14',
        3: '#ff4d4f',
      }
      return <span style={{ background: statusInfo.color, color: textColorMap[s] || '#8c8c8c', borderRadius: 10, padding: '2px 10px', display: 'inline-block', lineHeight: '20px' }}>{statusInfo.text}</span>
    } },
    {
      title: '操作', key: 'action', width: 140, fixed: 'right' as const,
      render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          <Permission code="burner:edit">
            <ActionLinkButton onClick={() => { 
              setEditingBurner(record)
              editForm.setFieldsValue(buildFormValues(record))
              setEditStrategy(record.strategy || 1)
              setIsEditModalOpen(true) 
            }}>编辑</ActionLinkButton>
          </Permission>
          <Permission code="burner:delete">
            <ActionConfirm
              title="删除设备"
              description={`确认删除设备“${record.name || record.type || record.id}”吗？`}
              okText="确认删除"
              cancelText="取消"
              confirmLoading={deletingBurnerId === record.id}
              onConfirm={() => handleDelete(record.id)}
            >
              <ActionLinkButton danger>删除</ActionLinkButton>
            </ActionConfirm>
          </Permission>
        </ActionButtonGroup>
      ),
    },
  ]

  const formBody = (
    mode: 'create' | 'edit',
    form: any,
    strategy: number,
    setStrategy: (val: number) => void,
    deviceCategory: string,
    deviceModel: string | undefined,
    hostType: string,
    lockedHostType: string,
    isEnabled: boolean,
    onFinish: (values: any) => void,
  ) => {
    const physicalPortOnly = requiresPhysicalPortStrategy(deviceModel)
    const activeLocationOption = LOCATION_MODE_OPTIONS.find((item) => item.value === hostType) || LOCATION_MODE_OPTIONS[0]
    const normalizedLockedHostType = String(lockedHostType || '').trim().toLowerCase()
    const locationLocked = ['local', 'server', 'agent'].includes(normalizedLockedHostType)
    const lockedLocationOption = LOCATION_MODE_OPTIONS.find((item) => item.value === normalizedLockedHostType)

    return (
      <Form
        form={form}
        layout="vertical"
        className="device-form-layout"
        onFinish={onFinish}
        scrollToFirstError
        onFinishFailed={(errorInfo) => {
          message.warning(getFirstFormErrorMessage(errorInfo))
        }}
      >
        <Form.Item name="usb_binding" hidden>
          <Input />
        </Form.Item>
        <Form.Item name="host_name" hidden>
          <Input />
        </Form.Item>
        <Form.Item name="host_address" hidden>
          <Input />
        </Form.Item>
        <div className="device-form-banner">
          <Alert
            message="物理端口位置识别的设备发生物理位置更改时，请重新获取新的物理位置并保存"
            type="info"
            showIcon={false}
          />
        </div>

        <div className="device-form-section">
          <div className="device-form-section__title">基础信息</div>
          <div className="device-form-grid">
            <Form.Item label="选择设备类型" name="device_category" rules={[{ required: true, message: '请选择设备类型' }]} required>
              <Select placeholder="请选择设备类型" options={DEVICE_CATEGORY_OPTIONS} />
            </Form.Item>

            {deviceCategory === 'burner' ? (
              <Form.Item label="选择烧录器" name="device_model" rules={[{ required: true, message: '请选择烧录器' }]} required>
                <Select placeholder="请选择烧录器" options={BURNER_MODEL_OPTIONS} />
              </Form.Item>
            ) : (
              <div className="device-form-grid__placeholder" />
            )}

            <Form.Item className="device-form-grid__full" label="设备名称" name="name" rules={[{ required: true, message: '请输入设备名称' }]} required>
              <Input name="name" autoComplete="organization-title" placeholder={deviceCategory === 'sd_reader' ? '请输入设备名称，如 SD 卡读卡器 #1' : '请输入设备名称，如 J-LINK #1'} />
            </Form.Item>

            {deviceCategory === 'burner' ? (
              <>
                <Form.Item label="支持接口" name="supported_interfaces" rules={[{ required: true, message: '请选择支持接口' }]} required>
                  <Select
                    mode="multiple"
                    disabled
                    placeholder="系统按型号自动带出支持接口"
                    options={INTERFACE_OPTIONS}
                  />
                </Form.Item>

                <Form.Item label="支持芯片" name="supported_chips" rules={[{ required: true, message: '请选择支持芯片' }]} required>
                  <Select
                    mode="multiple"
                    disabled
                    placeholder="系统按型号自动带出支持芯片"
                    options={CHIP_OPTIONS}
                  />
                </Form.Item>

                <div className="device-form-inline-note device-form-grid__full">
                  已根据烧录器型号自动映射支持接口与支持芯片，无需手动调整
                </div>
              </>
            ) : (
              <>
                <Form.Item label="支持卡类型" name="supported_card_types" rules={[{ required: isEnabled, message: '请选择支持卡类型' }]} required={isEnabled}>
                  <Select mode="multiple" placeholder="请选择支持卡类型" options={CARD_TYPE_OPTIONS} />
                </Form.Item>

                <Form.Item label="挂载路径" name="mount_path" rules={[{ required: isEnabled, message: '请输入挂载路径' }]} required={isEnabled}>
                  <Input name="mount_path" autoComplete="off" placeholder="例如：/Volumes/SDCARD" />
                </Form.Item>
              </>
            )}
          </div>
        </div>

        <div className="device-form-section">
          <div className="device-form-section__title">位置信息</div>
          <div className="device-form-grid">
            <Form.Item className="device-form-grid__full" label="设备位置" name="host_type" rules={[{ required: true, message: '请选择设备位置' }]} required>
              <div className="device-location-grid">
                {LOCATION_MODE_OPTIONS.map((item) => {
                  const Icon = item.icon
                  const active = hostType === item.value
                  const disabled = locationLocked && normalizedLockedHostType !== item.value
                  return (
                    <div
                      key={item.value}
                      className={`device-location-card${active ? ' device-location-card--active' : ''}${disabled ? ' device-location-card--disabled' : ''}`}
                      onClick={() => {
                        if (disabled) return
                        if (form.getFieldValue('host_type') !== item.value) {
                          form.setFieldsValue(buildBurnerNodeMetadataReset())
                        }
                        form.setFieldValue('host_type', item.value)
                      }}
                      aria-disabled={disabled}
                    >
                      <div className="device-location-card__icon">
                        <Icon />
                      </div>
                      <div className="device-location-card__title">{item.title}</div>
                      <div className="device-location-card__desc">{item.description}</div>
                    </div>
                  )
                })}
              </div>
            </Form.Item>
            <Form.Item name="locked_host_type" hidden>
              <Input />
            </Form.Item>

            <div
              className="device-location-tip device-form-grid__full"
              style={{
                borderColor: activeLocationOption.tipBorder,
                background: activeLocationOption.tipBackground,
                color: activeLocationOption.tipColor,
              }}
            >
              {activeLocationOption.tip}
            </div>
            {locationLocked && lockedLocationOption ? (
              <div className="device-form-inline-note device-form-grid__full">
                该设备来自扫描结果，设备位置已锁定为“{lockedLocationOption.title}”，不可切换到其他节点。
              </div>
            ) : null}

            {hostType === 'agent' ? (
              <Form.Item className="device-form-grid__full" label="代理地址" name="agent_url" rules={[{ required: true, message: '请输入代理地址' }]} required>
                <Input
                  name="agent_url"
                  autoComplete="url"
                  placeholder="例如：http://192.168.1.20:8000"
                  onChange={() => {
                    form.setFieldsValue(buildBurnerNodeMetadataReset())
                  }}
                />
              </Form.Item>
            ) : (
              <Form.Item name="agent_url" hidden>
                <Input name="agent_url" autoComplete="off" />
              </Form.Item>
            )}

            <Form.Item className="device-form-grid__full" label="物理位置" name="location">
              <Input name="location" autoComplete="off" placeholder="例如：USB 插槽 1" />
            </Form.Item>

            {deviceCategory === 'burner' ? (
              <Form.Item className="device-form-grid__full" label="识别策略">
                <Radio.Group className="device-strategy-group" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                  <Radio value={1} disabled={physicalPortOnly}>按 SN 序列号识别</Radio>
                  <Radio value={2}>按物理端口位置识别</Radio>
                </Radio.Group>
              </Form.Item>
            ) : null}

            {deviceCategory === 'burner' && strategy === 1 ? (
              <>
                <Form.Item name="port" hidden>
                  <Input name="port" autoComplete="off" />
                </Form.Item>
                <Form.Item
                  className="device-form-grid__full"
                  label="SN 标识码"
                  name="sn"
                  required={isEnabled}
                  rules={[{ required: isEnabled, message: '请输入 SN 标识码' }]}
                  extra={<div className="device-field-extra">推荐 J-LINK、ST-LINK 等支持序列号的设备使用</div>}
                >
                  <Input
                    name="sn"
                    autoComplete="off"
                    placeholder="例如：37FF71064E573436F2FC1443"
                    suffix={(
                      <Button
                        type="link"
                        size="small"
                        className="device-suffix-action"
                        loading={scanLoading === 'create-sn' || scanLoading === 'edit-sn'}
                        onClick={() => handleScan(mode, 1)}
                        icon={<SyncOutlined />}
                      >
                        获取标识码
                      </Button>
                    )}
                  />
                </Form.Item>
              </>
            ) : null}

            {deviceCategory === 'burner' && strategy === 2 ? (
              <Form.Item
                className="device-form-grid__full"
                label="物理端口"
                name="port"
                required={isEnabled}
                rules={[{ required: isEnabled, message: '请输入物理端口' }]}
                extra={<div className="device-field-extra device-field-extra--warning">无 SN 设备专用。绑定后请勿更换 USB 插口，避免热插拔漂移导致错烧</div>}
              >
                <Input
                  name="port"
                  autoComplete="off"
                  placeholder="例如：Port_#0003.Hub_#0001"
                  suffix={(
                    <Button
                      type="link"
                      size="small"
                      className="device-suffix-action"
                      loading={scanLoading === 'create-port' || scanLoading === 'edit-port'}
                      onClick={() => handleScan(mode, 2)}
                      icon={<SyncOutlined />}
                    >
                      获取当前位置
                    </Button>
                  )}
                />
              </Form.Item>
            ) : null}

            <div className="device-form-grid__full device-form-toggle-row">
              <Form.Item label="启用状态" name="is_enabled" valuePropName="checked" initialValue={true}>
                <Switch />
              </Form.Item>
              <div className="device-form-toggle-row__hint">保存后立即生效，可在列表页继续修改启用状态</div>
            </div>

            {deviceCategory === 'burner' ? (
              <Form.Item className="device-form-grid__full" label="描述" name="description">
                <Input.TextArea name="description" autoComplete="off" rows={3} placeholder="补充填写设备用途、适配板卡或备注信息" />
              </Form.Item>
            ) : null}
          </div>
        </div>
      </Form>
    )
  }

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
        <div className="client-page-title">
          <h1>设备管理</h1>
          <p className="client-page-subtitle">维护烧录器、连接位置、能力标签与在线状态</p>
        </div>
        <Permission code="burner:add">
          <PagePrimaryButton
            icon={<PlusOutlined />}
            onClick={() => {
              openCreateModal()
            }}
          >
            新增设备
          </PagePrimaryButton>
        </Permission>
      </div>

      <div style={{ background: '#fff', borderRadius: 8 }}>
        <div style={{ marginBottom: 18, display: 'flex', justifyContent: 'flex-end', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <Permission code="burner:scan">
            <Dropdown
              menu={{
                items: [
                  { key: 'local', label: '扫描本地' },
                  { key: 'all', label: '扫描所有节点' },
                ],
                onClick: async ({ key }) => {
                  const scope = key as 'local' | 'all'
                  setDiscoveryScope(scope)
                  setIsDiscoveryModalOpen(true)
                  await runDiscovery(scope)
                },
              }}
              trigger={['click']}
            >
              <ActionLinkButton>
                扫描方式 <DownOutlined />
              </ActionLinkButton>
            </Dropdown>
          </Permission>
          <Space wrap size={12}>
            <Select
              style={{ width: 140 }}
              value={params.node_scope || 'all'}
              options={nodeFilterOptions}
              onChange={(value) => setParams({ ...params, page: 1, node_scope: value === 'all' ? undefined : value })}
            />
            <Select
              style={{ width: 130 }}
              value={typeof params.status === 'number' ? params.status : 'all'}
              options={[{ value: 'all', label: '全部状态' }, ...statusFilterOptions]}
              onChange={(value) => setParams({ ...params, page: 1, status: value === 'all' ? undefined : Number(value) })}
            />
          </Space>
          <Input
            id="burner-search-keyword"
            name="burnerKeyword"
            autoComplete="off"
            className="pcids-list-search"
            placeholder="请输入烧录器名称"
            allowClear
            value={keywordInput}
            prefix={<SearchOutlined />}
            onChange={(e) => setKeywordInput(e.target.value)}
            onPressEnter={() => setParams({ ...params, page: 1, keyword: keywordInput })}
          />
        </div>
        {listError ? (
          <Alert
            type="error"
            showIcon
            closable
            message={listError}
            style={{ marginBottom: 16 }}
            onClose={() => setListError('')}
          />
        ) : null}
        <Table 
          columns={columns} 
          dataSource={dataSource} 
          rowKey="id" 
          loading={loading}
          scroll={{ x: 'max-content' }}
          onChange={(pagination, filters, sorter: any) => {
            const selectedDeviceCategories = Array.isArray(filters?.device_type)
              ? filters.device_type.map((item) => String(item)).filter(Boolean)
              : []
            setParams({
              ...params,
              page: pagination.current || 1,
              page_size: pagination.pageSize || 10,
              device_categories: selectedDeviceCategories.length ? selectedDeviceCategories.join(',') : undefined,
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
        title="新增设备"
        className="pcids-modal pcids-modal--form device-form-modal device-form-modal--create"
        open={isCreateModalOpen}
        onCancel={closeCreateModal}
        footer={
          <div className="pcids-modal__footer-split">
            <Checkbox className="pcids-modal__continue" name="keepAdding" checked={keepAdding} onChange={(e) => setKeepAdding(e.target.checked)}>继续新增</Checkbox>
            <Space className="pcids-modal__footer-actions">
              <PageSecondaryButton onClick={closeCreateModal} disabled={creating}>
                取消
              </PageSecondaryButton>
              <PagePrimaryButton loading={creating} onClick={() => createForm.submit()}>
                新增
              </PagePrimaryButton>
            </Space>
          </div>
        }
      >
        {formBody('create', createForm, createStrategy, setCreateStrategy, createDeviceCategory, createDeviceModel, createHostType, createLockedHostType, createIsEnabled, handleCreate)}
      </Modal>

      <Modal title="编辑设备" open={isEditModalOpen} onOk={() => editForm.submit()} confirmLoading={updating}
        className="pcids-modal pcids-modal--form device-form-modal"
        okText="保存" cancelText="取消"
        onCancel={closeEditModal} cancelButtonProps={{ disabled: updating }}>
        {formBody('edit', editForm, editStrategy, setEditStrategy, editDeviceCategory, editDeviceModel, editHostType, editLockedHostType, editIsEnabled, handleUpdate)}
      </Modal>

      <Modal
        title={scanSelect.field === 'sn' ? '选择 SN 标识码' : '选择物理端口'}
        open={scanSelect.open}
        className="pcids-modal pcids-modal--compact"
        okText="确定"
        cancelText="取消"
        onOk={() => applyScanSelection(scanSelect)}
        onCancel={() => setScanSelect((prev) => ({ ...prev, open: false, value: undefined }))}
      >
        <Select
          style={{ width: '100%' }}
          value={scanSelect.value}
          optionLabelProp="title"
          placeholder={scanSelect.field === 'sn' ? '请选择 SN 标识码' : '请选择物理端口'}
          options={scanSelect.options.map((item) => {
            const deviceId = getScanDeviceId(item)
            if (scanSelect.field === 'sn') {
              return {
                value: item.value,
                title: item.value,
                label: `${item.value}${item.port ? `（${item.port}）` : ''}`,
              }
            }
            return {
              value: item.value,
              title: item.value,
              label: (
                <div style={{ lineHeight: 1.45, padding: '2px 0' }}>
                  <div>物理端口：{item.value}</div>
                  <div style={{ color: '#667085', fontSize: 12, overflowWrap: 'anywhere' }}>
                    设备 ID：{deviceId || '未获取'}
                  </div>
                  {item.sn ? <div style={{ color: '#667085', fontSize: 12 }}>SN：{item.sn}</div> : null}
                </div>
              ),
            }
          })}
          onChange={(value) => setScanSelect((prev) => ({ ...prev, value }))}
        />
      </Modal>

      <Modal
        title="扫描结果"
        open={isDiscoveryModalOpen}
        onCancel={() => setIsDiscoveryModalOpen(false)}
        className="pcids-modal pcids-modal--xl pcids-modal--body-fill"
        footer={[
          <Button key="rescan" type="primary" ghost loading={discoveryLoading} onClick={() => runDiscovery(discoveryScope)}>
            重新扫描
          </Button>,
          <Button key="close" onClick={() => setIsDiscoveryModalOpen(false)}>
            关闭
          </Button>,
        ]}
      >
        <style>
          {`
            .device-discovery-table .ant-table-cell {
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            }
            .device-discovery-table .ant-table-cell > * {
              min-width: 0;
              overflow: hidden;
              text-overflow: ellipsis;
            }
          `}
        </style>
        <Alert
          message={`本次共扫描到 ${discoveryResult?.total_scanned || 0} 个设备，端口变化 ${discoveryResult?.changed_bindings?.length || 0} 个，未登记设备 ${discoveryResult?.unregistered_devices?.length || 0} 个`}
          description={`扫描方式：${discoveryScope === 'all' ? '扫描所有节点' : '扫描本地'}；扫描结果会同步更新数据库中的设备真实状态`}
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 8 }}>
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>检测到端口变化</div>
          {(discoveryResult?.changed_bindings || []).length ? (
            <Table
              className="device-discovery-table"
              size="small"
              rowKey={(record: any) => `changed-${record.burner_id}`}
              pagination={false}
              columns={[
                { title: '设备名称', dataIndex: 'burner_name', key: 'burner_name', width: 140 },
                { title: '设备型号', dataIndex: 'burner_type', key: 'burner_type', width: 150 },
                {
                  title: '原绑定',
                  key: 'original_binding',
                  width: 220,
                  render: (_: any, record: any) => (
                    <div style={{ minWidth: 0 }}>
                      {renderDiscoveryText(record?.original_binding?.node_label)}
                      <div style={{ color: '#999', fontSize: 12, minWidth: 0 }}>{renderDiscoveryText(record?.original_binding?.port)}</div>
                    </div>
                  ),
                },
                {
                  title: '当前检测',
                  key: 'current_binding',
                  width: 220,
                  render: (_: any, record: any) => (
                    <div style={{ minWidth: 0 }}>
                      {renderDiscoveryText(record?.current_binding?.node_label)}
                      <div style={{ color: '#999', fontSize: 12, minWidth: 0 }}>{renderDiscoveryText(record?.current_binding?.port)}</div>
                    </div>
                  ),
                },
                {
                  title: '识别方式',
                  key: 'strategy',
                  width: 110,
                  render: (_: any, record: any) => <Tag color="blue">{record?.strategy === 2 ? '物理端口' : 'SN绑定'}</Tag>,
                },
                {
                  title: '操作',
                  key: 'action',
                  width: 120,
                  fixed: 'right' as const,
                  render: (_: any, record: any) => (
                    <Button
                      type="link"
                      loading={discoveryActionKey === `bind-${record?.burner_id}`}
                      onClick={() => handleUpdateBinding(record)}
                    >
                      更新绑定
                    </Button>
                  ),
                },
              ]}
              dataSource={discoveryResult?.changed_bindings || []}
              tableLayout="fixed"
              scroll={{ x: 'max-content' }}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未检测到端口变化设备" />
          )}
        </div>

        <div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>发现未登记设备</div>
          {[...(discoveryResult?.unregistered_devices || []), ...(discoveryResult?.probe_only_devices || [])].length ? (
            <Table
              className="device-discovery-table"
              size="small"
              rowKey={(record: any) => record.candidate_id}
              pagination={false}
              columns={[
                { title: '检测名称', dataIndex: 'detected_name', key: 'detected_name', width: 180 },
                {
                  title: '设备类型',
                  key: 'type',
                  width: 120,
                  render: (_: any, record: any) => {
                    if (record?.probe_only) return <Tag color="default">未知设备</Tag>
                    const info = getDeviceTypeInfo({ type: record?.type, config_json: JSON.stringify({ device_category: record?.device_category }) })
                    return <Tag color={info.color}>{info.label}</Tag>
                  },
                },
                { title: '设备型号', dataIndex: 'type', key: 'type_model', width: 150, render: (_: any, record: any) => record?.probe_only ? '未知' : renderDiscoveryText(record?.type) },
                { title: '所在节点', dataIndex: 'node_label', key: 'node_label', width: 140 },
                {
                  title: 'SN/端口',
                  key: 'binding',
                  render: (_: any, record: any) => (
                    <div style={{ minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: 4, minWidth: 0 }}>
                        <span style={{ flex: '0 0 auto' }}>SN:</span>
                        <span style={{ minWidth: 0, flex: 1 }}>{renderDiscoveryText(record?.sn, '')}</span>
                      </div>
                      <div style={{ display: 'flex', gap: 4, minWidth: 0, color: '#999', fontSize: 12 }}>
                        <span style={{ flex: '0 0 auto' }}>端口:</span>
                        <span style={{ minWidth: 0, flex: 1 }}>{renderDiscoveryText(record?.port)}</span>
                      </div>
                    </div>
                  ),
                },
                {
                  title: '操作',
                  key: 'action',
                  width: 120,
                  fixed: 'right' as const,
                  render: (_: any, record: any) => (
                    <Button type="link" onClick={() => handleDiscoveryCreate(record)}>
                      新增设备
                    </Button>
                  ),
                },
              ]}
              dataSource={[...(discoveryResult?.unregistered_devices || []), ...(discoveryResult?.probe_only_devices || [])]}
              tableLayout="fixed"
              scroll={{ x: 'max-content' }}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未发现未登记设备" />
          )}
        </div>
        </div>
      </Modal>
    </div>
  )
}

export default Burner
