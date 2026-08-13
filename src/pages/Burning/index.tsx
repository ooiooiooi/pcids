import { Table, Button, Space, Modal, App as AntdApp, Tag, Select, Input, InputNumber, Row, Col, Typography, Checkbox, Drawer, Badge, Tabs, Tooltip } from 'antd'
import { PlusOutlined, SearchOutlined, DesktopOutlined, AppstoreOutlined, MinusOutlined, SyncOutlined, LinkOutlined, QuestionCircleOutlined, CopyOutlined } from '@ant-design/icons'
import { Fragment, useState, useEffect, useMemo, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { taskApi, productApi, burnerApi, scriptApi, repositoryApi } from '../../services/api'
import { Permission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime, parseServerDateTime } from '../../utils/dateTime'
import { resolveMediaUrl } from '../../utils/mediaUrl'
import { API_BASE_URL } from '../../services/backendRuntime'
import { getRepositoryProjectContext, REPOSITORY_PROJECT_CONTEXT_EVENT } from '../../utils/repositoryProjectContext'
import BoardDetailPanel from '../../components/BoardDetailPanel'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton, PageSecondaryButton } from '../../components/ActionButton'
import { buildScriptSelectParameterDescriptors, filterExecutionOptionsForScript, getCompatibleBoardScripts, getSupportedScriptConfigFields, hasConfiguredAssociation, isFieldVisibleForOperation, matchAssociation, resolveScriptConfigDisplayText } from './scriptLinkage'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm, { ActionConfirmDialog } from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'
import { resolveArtifactSelectionAfterRefresh } from './artifactSelectionState'
import KylinIcon from '../../assets/images/os-kylin-logo.svg'
import HarmonyIcon from '../../assets/images/os-harmony.svg'
import SylixIcon from '../../assets/images/os-sylixos.png'
import UosIcon from '../../assets/images/os-uos.png'

const { Title } = Typography
const MAX_TASK_TIMEOUT_SECONDS = 7200

const subscribeTaskEvents = (taskId: number, onData: (data: any) => void) => {
  const controller = new AbortController()
  let terminalReceived = false

  const connect = async () => {
    while (!controller.signal.aborted) {
      try {
        const token = localStorage.getItem('token')
        const response = await fetch(`${API_BASE_URL}/tasks/${taskId}/events`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
          signal: controller.signal,
          cache: 'no-store',
        })
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`)

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (!controller.signal.aborted) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (!line.trim()) continue
            const event = JSON.parse(line)
            if (event?.code === 0 && event?.data) {
              onData(event.data)
              terminalReceived = ![1, 4].includes(Number(event.data.status))
            }
          }
        }
        if (terminalReceived) return
      } catch (error: any) {
        if (controller.signal.aborted || error?.name === 'AbortError') return
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000))
    }
  }

  void connect()
  return () => controller.abort()
}

const detailSectionTitleStyle = {
  fontSize: 14,
  fontWeight: 600,
  color: '#1D2129',
  marginBottom: 14,
}

// 历史背景：曾经根据 PowerShell GBK 渲染误报“乱码”反转过数据库
// 干净字段，会反向制造乱码。这里仅做只读归一化，不再做主动反转。
const decodeMojibakeString = (raw?: any): string => {
  if (raw === undefined || raw === null) return ''
  return String(raw)
}

const getBurningRequestErrorMessage = (error: any, fallback: string): string => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const validationMessages = detail
      .map((item: any) => {
        const field = Array.isArray(item?.loc)
          ? item.loc.filter((part: any) => part !== 'body').join('.')
          : ''
        const text = String(item?.msg || '').trim()
        return text ? (field ? `${field}：${text}` : text) : ''
      })
      .filter(Boolean)
    if (validationMessages.length) return validationMessages.join('；')
  }
  const responseMessage = String(error?.response?.data?.message || '').trim()
  if (responseMessage) return responseMessage
  if (!error?.response && String(error?.message || '').trim()) {
    return `${fallback}：${String(error.message).trim()}。请确认后端服务和网络连接正常。`
  }
  return fallback
}

const detailSubSectionTitleStyle = {
  fontSize: 14,
  fontWeight: 600,
  color: '#1D2129',
  marginTop: 4,
  marginBottom: 14,
  paddingBottom: 10,
  borderBottom: '1px dashed #E5E6EB',
}

const detailFieldGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
  columnGap: 28,
  rowGap: 18,
}

const detailFieldLabelStyle = {
  fontSize: 13,
  color: '#86909C',
  lineHeight: '20px',
  marginBottom: 6,
}

const detailFieldValueStyle = {
  fontSize: 14,
  color: '#1D2129',
  lineHeight: '22px',
  wordBreak: 'break-word' as const,
}

const detailBlockValueStyle = {
  minHeight: 36,
  padding: '8px 12px',
  borderRadius: 6,
  background: '#F5F6F7',
  color: '#4E5969',
  fontSize: 12,
  lineHeight: '20px',
  wordBreak: 'break-all' as const,
}

const detailSummaryGridStyle = {
  display: 'grid',
  gridTemplateColumns: '120px minmax(0, 1fr)',
  rowGap: 14,
  columnGap: 16,
  fontSize: 14,
}

const osList = [
  { id: 1, name: '银河麒麟', icon: KylinIcon },
  { id: 2, name: '鸿蒙', icon: HarmonyIcon },
  { id: 3, name: '翼辉SylixOS', icon: SylixIcon },
  { id: 4, name: '统信UOS', icon: UosIcon },
]

const DEFAULT_OS_ID = osList.find((item) => item.name === '银河麒麟')?.id || osList[0]?.id || null

const osTypeMap: Record<number, string> = {
  1: 'kylin',
  2: 'harmony',
  3: 'yinghui',
  4: 'uos',
}

const osNameMap: Record<string, string> = {
  kylin: '银河麒麟',
  harmony: '鸿蒙',
  yinghui: '翼辉SylixOS',
  uos: '统信UOS',
}

const getTaskTargetText = (task?: any) => {
  const config = parseJsonSafe(task?.config_json) || {}
  const taskType = String(task?.task_type || config?.task_type || config?.platform || 'board').trim().toLowerCase()
  if (taskType === 'os') {
    const osType = String(config?.os_type || '').trim().toLowerCase()
    const osName = osNameMap[osType] || '操作系统'
    if (osType === 'harmony') {
      const deviceId = String(config?.harmony_device_id || task?.harmony_device_id || '').trim()
      return deviceId ? `${osName}|${deviceId}` : osName
    }
    const targetIp = String(task?.target_ip || config?.target_ip || '').trim()
    return targetIp ? `${osName} | ${targetIp}` : osName
  }
  return String(task?.board_name || task?.target_ip || '').trim() || '-'
}

const renderSingleLineTooltipText = (value: any) => {
  const text = String(value || '').trim() || '-'
  return <EllipsisText value={text} />
}

const BOARD_IDE_OPTIONS = [
  '',
  'Code Composer Studio',
  'IAR Embedded Workbench For Arm',
  'Keil uVision',
  'MPLAB',
  'STM32CubeIDE',
  'Vitis',
  'Vivado',
  'WindRiver Workbench',
]

const HYBRID_BURN_MODE_OPTIONS = [
  { label: 'TFTP+串口（系统异常/未分区）', value: 'TFTP+串口' },
]
const HYBRID_BAUD_RATE_OPTIONS = ['9600', '19200', '38400', '57600', '115200'].map((item) => ({ label: item, value: item }))
const SYLIXOS_HYBRID_SCRIPT_NAME = 'sylixos_ls2k_ftp_serial_flash'
const SYLIXOS_HYBRID_DEFAULTS = {
  burnMode: 'TFTP+串口',
  transferProtocol: 'TFTP+串口',
  serverPort: '69',
  baudRate: '115200',
  serialLoginUser: 'root',
  serialPasswordless: true,
  ftpLoginUser: 'root',
  ftpLoginPassword: 'root',
  ftpPasswordless: false,
  boardTargetAddress: '192.168.1.230',
  localIp: '192.168.1.100',
  targetPath: '/media/hdd0',
}
const CHIP_TYPE_OPTIONS = ['ARM', 'PIC', 'DSP', 'FPGA', 'Altera-CPLD', '其他']

const getChipTagColor = (chipType?: string) => {
  if (chipType === 'ARM') return 'blue'
  if (chipType === 'PIC') return 'green'
  if (chipType === 'DSP') return 'purple'
  if (chipType === 'FPGA') return 'magenta'
  if (chipType === 'Altera-CPLD') return 'cyan'
  if (chipType === '其他') return 'default'
  return 'default'
}

const isValidPort = (value: any) => {
  const num = Number(value)
  return Number.isInteger(num) && num >= 1 && num <= 65535
}

const extractArtifactExtension = (...candidates: any[]) => {
  for (const candidate of candidates) {
    const text = String(candidate || '').trim()
    if (!text) continue
    const normalized = text.replace(/\\/g, '/').split('/').pop()?.split('?')[0]?.split('#')[0]?.trim().toLowerCase() || ''
    if (!normalized.includes('.')) continue
    return normalized.split('.').pop() || ''
  }
  return ''
}

const hasInvalidWhitespace = (value: any) => /\s/.test(String(value || '').trim())

const firstFilledText = (...values: Array<any>) => {
  for (const value of values) {
    const text = String(value || '').trim()
    if (text) return text
  }
  return ''
}

const uniqueByTextKey = <T extends Record<string, any>>(items: T[], buildKey: (item: T) => string) => {
  const seen = new Set<string>()
  const output: T[] = []
  items.forEach((item) => {
    const key = buildKey(item).trim().toLowerCase()
    if (!key || seen.has(key)) return
    seen.add(key)
    output.push(item)
  })
  return output
}

const getBurnerDisplayPriority = (item: any) => {
  const statusScore = Number(item?.status) === 0 ? 8 : 0
  const enabledScore = item?.is_enabled === false || item?.is_enabled === 0 ? 0 : 4
  const detectedScore = String(item?.modified_by || '').trim().toLowerCase() === 'system' ? 0 : 2
  const namedScore = String(item?.name || '').trim() === String(item?.type || '').trim() ? 0 : 1
  return statusScore + enabledScore + detectedScore + namedScore
}

const parseJsonSafe = (value?: string | null) => {
  if (!value) return null
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

const parseBoardBurnInterfaces = (value?: string | null) => {
  if (!value) return []
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.map((item) => String(item)).filter(Boolean) : []
  } catch {
    return String(value)
      .split(/[，,;/|]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  }
}

const getRepositoryLocationState = (item: any) => {
  const fileDetail = item?.file_detail || {}
  const localPath = String(item?.local_path || fileDetail?.local_path || item?.file_url || '').trim()
  const serverPath = String(item?.server_path || fileDetail?.server_path || '').trim()
  const serverTarget = String(item?.server_target || fileDetail?.server_target || item?.storage_target || fileDetail?.storage_target || '').trim()
  const localExists = Boolean(item?.local_exists ?? fileDetail?.local_exists ?? localPath)
  const serverExists = Boolean(item?.server_exists ?? fileDetail?.server_exists ?? serverPath ?? serverTarget)
  const remoteDownloadable = Boolean(item?.remote_downloadable ?? item?.download_uri ?? fileDetail?.download_url ?? fileDetail?.download_url_with_id)
  return {
    localExists: localExists && Boolean(localPath),
    localPath: localPath || '',
    serverExists: serverExists && Boolean(serverPath || serverTarget),
    serverPath: serverPath || '',
    serverTarget: serverTarget || '',
    remoteDownloadable,
  }
}

const getArtifactLocationInfo = (item: any) => {
  const locationState = getRepositoryLocationState(item)
  const localPath = locationState.localPath || ''
  const serverPath = locationState.serverTarget || locationState.serverPath || ''
  if (locationState.localExists && locationState.serverExists) {
    return {
      value: '服务器|本地',
      color: 'blue',
      detail: [localPath, serverPath].filter(Boolean).join(' | ') || '-',
      filters: ['本地', '服务器'],
      installSource: 'local',
    }
  }
  if (locationState.serverExists) {
    return { value: '服务器', color: 'blue', detail: serverPath || '-', filters: ['服务器'], installSource: 'server' }
  }
  if (locationState.localExists || item?.source_type === 'local_upload') {
    return { value: '本地', color: 'blue', detail: localPath || '-', filters: ['本地'], installSource: 'local' }
  }
  return {
    value: 'CodeArts',
    color: 'blue',
    detail: item?.display_path || item?.download_uri || '-',
    filters: ['CodeArts'],
    installSource: 'codearts',
  }
}

const getArtifactCurrentPath = (item: any) => {
  const locationState = getRepositoryLocationState(item)
  const localPath = locationState.localPath || ''
  const serverPath = locationState.serverTarget || locationState.serverPath || ''
  if (locationState.localExists && locationState.serverExists) {
    return [localPath, serverPath].filter(Boolean).join(' | ') || '-'
  }
  if (locationState.serverExists) return serverPath || '-'
  if (locationState.localExists || item?.source_type === 'local_upload') return localPath || '-'
  return item?.display_path || item?.download_uri || '-'
}

const createInitialWizardData = (initialState?: any) => ({
  software: initialState?.softwareId || null,
  installSource: 'codearts',
  boardId: null,
  osId: initialState?.osId || (initialState?.taskType === 'os' ? DEFAULT_OS_ID : null),
  burnerId: null,
  scriptId: null,
  ide: '',
  interfaceType: 'SWD',
  eraseMode: '全片擦除',
  writeSpeed: '1000',
  startAddress: '',
  qspiFlashModel: '',
  loaderType: '',
  targetConfigFile: '',
  gelInitScript: '',
  jtagChainIndex: '0',
  programVoltage: '',
  eepromWrite: '',
  writeConfigBits: '',
  executionOperation: '',
  bichinaBurnMode: '',
  preErase: '',
  blankCheck: '',
  executeProgram: '',
  tckFrequency: '',
  cableIndex: '0',
  sdTargetPath: '',
  formatSdCard: '',
  completionAction: '复位运行',
  options: ['local', 'integrity'],
  retryCount: 1,
  timeoutMinutes: 120,
  targetIp: '',
  targetPort: '',
  connectionProtocol: 'SSH',
  deploymentMode: 'FTP',
  harmonyDeviceId: '',
  ftpPort: '21',
  authType: 'password',
  loginUsername: 'root',
  loginPassword: '',
  loginPasswordless: false,
  privateKeyPath: '',
  installDir: '/apps',
  bootAutostart: false,
  burnMode: SYLIXOS_HYBRID_DEFAULTS.burnMode,
  transferProtocol: SYLIXOS_HYBRID_DEFAULTS.transferProtocol,
  serverPort: SYLIXOS_HYBRID_DEFAULTS.serverPort,
  serialPort: '',
  baudRate: SYLIXOS_HYBRID_DEFAULTS.baudRate,
  serialLoginUser: SYLIXOS_HYBRID_DEFAULTS.serialLoginUser,
  serialLoginPassword: '',
  serialPasswordless: SYLIXOS_HYBRID_DEFAULTS.serialPasswordless,
  systemUsername: 'root',
  systemPassword: 'root',
  ftpLoginUser: SYLIXOS_HYBRID_DEFAULTS.ftpLoginUser,
  ftpLoginPassword: SYLIXOS_HYBRID_DEFAULTS.ftpLoginPassword,
  ftpPasswordless: SYLIXOS_HYBRID_DEFAULTS.ftpPasswordless,
  boardTargetAddress: SYLIXOS_HYBRID_DEFAULTS.boardTargetAddress,
  localIp: SYLIXOS_HYBRID_DEFAULTS.localIp,
  targetPath: SYLIXOS_HYBRID_DEFAULTS.targetPath,
  remark: '',
  config: '',
})

const SCRIPT_INPUT_DRAFT_STORAGE_KEY = 'pcids-burning-script-input-drafts-v1'
const REMEMBERED_SCRIPT_PARAM_FIELDS = new Set([
  'interfaceType',
  'eraseMode',
  'writeSpeed',
  'startAddress',
  'qspiFlashModel',
  'loaderType',
  'targetConfigFile',
  'gelInitScript',
  'jtagChainIndex',
  'programVoltage',
  'eepromWrite',
  'writeConfigBits',
  'executionOperation',
  'bichinaBurnMode',
  'preErase',
  'blankCheck',
  'executeProgram',
  'tckFrequency',
  'cableIndex',
  'sdTargetPath',
  'formatSdCard',
  'completionAction',
])

const readScriptInputDrafts = (): Record<string, Record<string, string>> => {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(SCRIPT_INPUT_DRAFT_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

const readScriptInputDraft = (scriptId: number | string | null | undefined) => {
  const normalizedScriptId = Number(scriptId || 0)
  if (!normalizedScriptId) return {}
  return readScriptInputDrafts()[String(normalizedScriptId)] || {}
}

const persistScriptInputDraft = (scriptId: number | string | null | undefined, field: string, value: any) => {
  const normalizedScriptId = Number(scriptId || 0)
  if (!normalizedScriptId || !REMEMBERED_SCRIPT_PARAM_FIELDS.has(field) || typeof window === 'undefined') return
  try {
    const drafts = readScriptInputDrafts()
    const scriptKey = String(normalizedScriptId)
    const currentDraft = drafts[scriptKey] && typeof drafts[scriptKey] === 'object' ? drafts[scriptKey] : {}
    drafts[scriptKey] = {
      ...currentDraft,
      [field]: String(value ?? ''),
    }
    window.localStorage.setItem(SCRIPT_INPUT_DRAFT_STORAGE_KEY, JSON.stringify(drafts))
  } catch {
    /* ignore */
  }
}

const Burning: React.FC = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const { message } = AntdApp.useApp()
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, status: undefined as number | undefined, board_name: undefined as string | undefined, keyword: undefined as string | undefined, sort_field: 'created_at', sort_order: 'desc' })
  const [detailTask, setDetailTask] = useState<any>(null)
  const [consistencyTask, setConsistencyTask] = useState<any>(null)
  const [isConsistencyOpen, setIsConsistencyOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)
  const [taskKeywordInput, setTaskKeywordInput] = useState('')
  const [currentProject, setCurrentProject] = useState(getRepositoryProjectContext)

  // Wizard state
  const [isWizardOpen, setIsWizardOpen] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)
  const [platform, setPlatform] = useState<'board' | 'os' | 'hybrid' | null>(null)
  const [wizardData, setWizardData] = useState<any>(createInitialWizardData())

  // Dependent data
  const [boards, setBoards] = useState<any[]>([])
  const [burners, setBurners] = useState<any[]>([])
  const [scripts, setScripts] = useState<any[]>([])
  const [scriptDetailsMap, setScriptDetailsMap] = useState<Record<number, any>>({})
  const [repositories, setRepositories] = useState<any[]>([])
  const [burnerOnlineMap, setBurnerOnlineMap] = useState<Record<number, boolean | undefined>>({})
  const [burnerStatusMap, setBurnerStatusMap] = useState<Record<number, number | undefined>>({})
  const [burnerScanLoading, setBurnerScanLoading] = useState(false)
  const [burnerScanError, setBurnerScanError] = useState('')
  const burnerScanInFlightRef = useRef(false)
  const pendingBurnerScanRef = useRef<any[] | null>(null)
  const [filterBoards, setFilterBoards] = useState<any[]>([])
  const [artifactKeyword, setArtifactKeyword] = useState('')
  const [artifactLocationFilter, setArtifactLocationFilter] = useState('全部')
  const [artifactPage, setArtifactPage] = useState(1)
  const [artifactPageSize, setArtifactPageSize] = useState(5)
  const artifactPageUserControlledRef = useRef(false)
  const artifactSelectionUserTouchedRef = useRef(false)
  const [wizardLocalIps, setWizardLocalIps] = useState<string[]>([])
  const [wizardSerialPorts, setWizardSerialPorts] = useState<string[]>([])
  const [wizardHarmonyDevices, setWizardHarmonyDevices] = useState<Array<{ id: string; name: string }>>([])
  const [wizardContextLoading, setWizardContextLoading] = useState(false)
  const [hybridConnectionTesting, setHybridConnectionTesting] = useState(false)
  const [osConnectionTesting, setOsConnectionTesting] = useState(false)
  const [osConnectionResult, setOsConnectionResult] = useState<any>(null)
  const [osFieldErrors, setOsFieldErrors] = useState<Record<string, string>>({})
  const [previewBoardImage, setPreviewBoardImage] = useState('')
  const [boardDetailRecord, setBoardDetailRecord] = useState<any>(null)
  const [isBoardDetailOpen, setIsBoardDetailOpen] = useState(false)
  const [versionBaselineReady, setVersionBaselineReady] = useState(false)
  const [versionBaselineChecksum, setVersionBaselineChecksum] = useState('')
  const [hybridConnectionPassed, setHybridConnectionPassed] = useState(false)
  const [wizardSubmitLoading, setWizardSubmitLoading] = useState(false)
  const [wizardBoardPage, setWizardBoardPage] = useState(1)
  const [wizardBoardPageSize, setWizardBoardPageSize] = useState(5)
  const [deletingTaskId, setDeletingTaskId] = useState<number | null>(null)
  const [terminatingTaskId, setTerminatingTaskId] = useState<number | null>(null)
  const [terminateDialogTask, setTerminateDialogTask] = useState<any | null>(null)
  const [terminateReason, setTerminateReason] = useState('')
  const isTaskActive = (task?: any) => [1, 4].includes(Number(task?.status))
  const runningTaskIds = useMemo(
    () => dataSource
      .filter((item) => isTaskActive(item) && Number(item?.id))
      .map((item) => Number(item.id)),
    [dataSource],
  )
  useEffect(() => { 
    fetchTasks()
    fetchFilterBoards()
  }, [params, currentProject.projectKey])

  useEffect(() => {
    fetchRepositories()
  }, [currentProject.projectKey])

  useEffect(() => {
    const handleProjectChange = () => {
      setCurrentProject(getRepositoryProjectContext())
      setParams((prev) => ({ ...prev, page: 1 }))
      setIsDetailOpen(false)
      setDetailTask(null)
      setIsConsistencyOpen(false)
      setConsistencyTask(null)
    }
    window.addEventListener(REPOSITORY_PROJECT_CONTEXT_EVENT, handleProjectChange)
    window.addEventListener('storage', handleProjectChange)
    return () => {
      window.removeEventListener(REPOSITORY_PROJECT_CONTEXT_EVENT, handleProjectChange)
      window.removeEventListener('storage', handleProjectChange)
    }
  }, [])

  useEffect(() => {
    if (!isDetailOpen || !detailTask?.id || !isTaskActive(detailTask)) return
    return subscribeTaskEvents(detailTask.id, (next) => {
      setDetailTask((prev: any) => prev?.id === next.id ? { ...prev, ...next } : prev)
      setDataSource((prev) => prev.map((item) => item.id === next.id ? { ...item, ...next } : item))
    })
  }, [isDetailOpen, detailTask?.id, detailTask?.status])

  useEffect(() => {
    if (!isConsistencyOpen || !consistencyTask?.id || !isTaskActive(consistencyTask)) return
    return subscribeTaskEvents(consistencyTask.id, (next) => {
      setConsistencyTask((prev: any) => prev?.id === next.id ? { ...prev, ...next } : prev)
    })
  }, [isConsistencyOpen, consistencyTask?.id, consistencyTask?.status])

  useEffect(() => {
    if (!runningTaskIds.length) return
    const unsubscribers = runningTaskIds.map((taskId) => subscribeTaskEvents(taskId, (next) => {
      setDataSource((prev) => prev.map((item) => item.id === next.id ? { ...item, ...next } : item))
      if (!isTaskActive(next)) {
        fetchTasks(true)
      }
    }))
    return () => unsubscribers.forEach((unsubscribe) => unsubscribe())
  }, [runningTaskIds.join(',')])

  const fetchFilterBoards = async () => {
    try {
      const res: any = await productApi.getList({ page: 1, page_size: 100 })
      setFilterBoards(res?.data || [])
    } catch { /* ignore */ }
  }

  const fetchTasks = async (silent = false) => {
    if (!currentProject.projectKey) {
      setDataSource([])
      setTotal(0)
      setLoading(false)
      return
    }
    if (!silent) setLoading(true)
    try {
      const res: any = await taskApi.getList({
        ...params,
        project_key: currentProject.projectKey,
      }, silent)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally {
      if (!silent) setLoading(false)
    }
  }

  const fetchRepositories = async () => {
    if (!currentProject.projectKey) {
      setRepositories([])
      return
    }
    try {
      const res: any = await repositoryApi.getList({ page: 1, page_size: 500, _ts: Date.now() })
      if (res?.code === 0) setRepositories(Array.isArray(res.data) ? res.data : [])
    } catch {
      /* ignore */
    }
  }

  const applyBurnerRuntimeState = (burnerList: any[]) => {
    const nextOnlineMap: Record<number, boolean> = {}
    const nextStatusMap: Record<number, number> = {}
    burnerList.forEach((burner) => {
      const id = Number(burner?.id)
      if (!id) return
      const status = Number(burner?.status)
      if ([0, 1, 2, 3].includes(status)) {
        nextStatusMap[id] = status
        nextOnlineMap[id] = status === 0 || status === 2
      }
    })
    setBurnerOnlineMap(nextOnlineMap)
    setBurnerStatusMap(nextStatusMap)
  }

  const fetchWizardData = async () => {
    try {
      const [prodRes, burnerRes, scriptRes, repoRes]: any[] = await Promise.all([
        productApi.getList({ page: 1, page_size: 100 }),
        burnerApi.getList({ page: 1, page_size: 100, include_runtime_status: false }),
        scriptApi.getList({ page: 1, page_size: 100 }),
        repositoryApi.getList({ page: 1, page_size: 500, _ts: Date.now() })
      ])
      const nextBoards = (prodRes?.data || []).map((item: any) => ({
        ...item,
        board_image: resolveMediaUrl(item?.board_image),
      }))
      const nextBurners = uniqueByTextKey([...(burnerRes?.data || [])].sort((a, b) => getBurnerDisplayPriority(b) - getBurnerDisplayPriority(a)), (item) =>
        [item?.id, item?.type || item?.name, item?.host_type || 'local', item?.agent_url || '', item?.sn || '', item?.port || ''].join('|'),
      )
      const nextScripts = uniqueByTextKey(scriptRes?.data || [], (item) =>
        [item?.name, item?.task_type || 'board', item?.associated_burner || '', item?.associated_board || '', item?.associated_ide || item?.ide_name || ''].join('|'),
      )
      const nextRepositories = repoRes?.data || []
      setBoards(nextBoards)
      setBurners(nextBurners)
      setScripts(nextScripts)
      setScriptDetailsMap({})
      setRepositories(nextRepositories)
      applyBurnerRuntimeState(nextBurners)
      return {
        boards: nextBoards,
        burners: nextBurners,
        scripts: nextScripts,
        repositories: nextRepositories,
      }
    } catch { /* ignore */ }
    return {
      boards: [],
      burners: [],
      scripts: [],
      repositories: [],
    }
  }

  const fetchTaskWizardContext = async () => {
    try {
      const res: any = await taskApi.getWizardContext()
      if (res?.code === 0) {
        const localIps = Array.isArray(res?.data?.local_ips) ? res.data.local_ips.map((item: any) => String(item)).filter(Boolean) : []
        const serialPorts = Array.isArray(res?.data?.serial_ports) ? res.data.serial_ports.map((item: any) => String(item)).filter(Boolean) : []
        const harmonyDevices = Array.isArray(res?.data?.harmony_devices)
          ? res.data.harmony_devices
              .map((item: any) => ({ id: String(item?.id || '').trim(), name: String(item?.name || item?.id || '').trim() }))
              .filter((item: any) => item.id)
          : []
        setWizardLocalIps(localIps)
        setWizardSerialPorts(serialPorts)
        setWizardHarmonyDevices(harmonyDevices)
        return {
          localIps,
          serialPorts,
          harmonyDevices,
          defaultLocalIp: String(res?.data?.default_local_ip || '').trim(),
          defaultSerialPort: String(res?.data?.default_serial_port || '').trim(),
          defaultHarmonyDevice: String(res?.data?.default_harmony_device || '').trim(),
        }
      }
    } catch {
      /* ignore */
    }
    return {
      localIps: [],
      serialPorts: [],
      harmonyDevices: [],
      defaultLocalIp: '',
      defaultSerialPort: '',
      defaultHarmonyDevice: '',
    }
  }

  const refreshTaskWizardContext = async () => {
    setWizardContextLoading(true)
    try {
      const wizardContext = await fetchTaskWizardContext()
      setWizardData((prev: any) => {
        const scannedSerialPorts = wizardContext.serialPorts || []
        const currentSerialPort = String(prev.serialPort || '').trim()
        return {
          ...prev,
          serialPort: currentSerialPort && scannedSerialPorts.includes(currentSerialPort)
            ? currentSerialPort
            : (wizardContext.defaultSerialPort || ''),
          localIp: prev.localIp || wizardContext.defaultLocalIp || '',
        }
      })
    } finally {
      setWizardContextLoading(false)
    }
  }

  const scanBurnerOnlineStatus = async (burnerList = burners): Promise<void> => {
    if (!burnerList.length) {
      setBurnerOnlineMap({})
      setBurnerStatusMap({})
      setBurnerScanError('')
      return
    }
    const targetIds = burnerList
      .map((item: any) => Number(item?.id))
      .filter((id) => Number.isFinite(id) && id > 0)
      .sort((left, right) => left - right)
    const scanKey = targetIds.join(',')
    if (burnerScanInFlightRef.current) {
      pendingBurnerScanRef.current = burnerList
      return
    }
    burnerScanInFlightRef.current = true
    setBurnerScanLoading(true)
    setBurnerScanError('')
    try {
      const res: any = await burnerApi.getList({
        page: 1,
        page_size: Math.max(targetIds.length, 1),
        ids: scanKey,
        include_runtime_status: true,
      })
      const runtimeBurners = Array.isArray(res?.data) ? res.data : []
      const runtimeById = new Map(runtimeBurners.map((item: any) => [Number(item?.id), item]))
      setBurners((prev) =>
        prev.map((item) => {
          const runtime = runtimeById.get(Number(item.id))
          return runtime ? { ...item, ...runtime } : item
        }),
      )
      applyBurnerRuntimeState(runtimeBurners)
    } catch {
      setBurnerScanError('设备状态检测失败，请稍后重试')
    } finally {
      burnerScanInFlightRef.current = false
      setBurnerScanLoading(false)
      const pendingBurners = pendingBurnerScanRef.current
      pendingBurnerScanRef.current = null
      if (pendingBurners) {
        const pendingKey = pendingBurners
          .map((item: any) => Number(item?.id))
          .filter((id) => Number.isFinite(id) && id > 0)
          .sort((left, right) => left - right)
          .join(',')
        if (pendingKey && pendingKey !== scanKey) {
          void scanBurnerOnlineStatus(pendingBurners)
        }
      }
    }
  }

  const refreshRecommendedBurnerStatus = async () => {
    await scanBurnerOnlineStatus(visibleBurners)
  }

  const handleOpenWizard = async (initialState?: any) => {
    setCurrentStep(0)
    setPlatform(initialState?.taskType || 'board')
    artifactSelectionUserTouchedRef.current = false
    const nextWizardData = {
      ...createInitialWizardData(initialState),
      installSource: 'codearts',
    }
    setWizardData(nextWizardData)
    setBurnerOnlineMap({})
    setBurnerStatusMap({})
    setBurnerScanError('')
    setArtifactKeyword('')
    setArtifactLocationFilter('全部')
    artifactPageUserControlledRef.current = false
    setArtifactPage(1)
    setWizardBoardPage(1)
    setHybridConnectionPassed(false)
    setOsConnectionResult(null)
    setIsWizardOpen(true)
    const wizardDeps = await fetchWizardData()
    const requestedSoftwareId = nextWizardData.software || initialState?.softwareId || null
    const defaultSoftwareId =
      wizardDeps.repositories.find((repo: any) => Number(repo?.id) === Number(requestedSoftwareId))?.id ||
      wizardDeps.repositories.find((repo: any) => hasRepositoryVersion(repo))?.id ||
      null
    setWizardData((prev: any) => ({
      ...prev,
      software: resolveArtifactSelectionAfterRefresh({
        currentSoftware: prev.software,
        requestedSoftware: requestedSoftwareId,
        defaultSoftware: defaultSoftwareId,
        availableSoftwareIds: wizardDeps.repositories.map((repo: any) => repo?.id),
        userTouched: artifactSelectionUserTouchedRef.current,
      }),
      boardId: wizardDeps.boards[0]?.id || null,
    }))
    fetchTaskWizardContext().then((wizardContext) => {
      setWizardData((prev: any) => ({
        ...prev,
        localIp: prev.localIp || wizardContext.defaultLocalIp || '',
        serialPort: prev.serialPort || wizardContext.defaultSerialPort || '',
        harmonyDeviceId: prev.harmonyDeviceId || wizardContext.defaultHarmonyDevice || '',
      }))
    })
  }

  useEffect(() => {
    const state = location.state as any
    if (state?.openWizard) {
      handleOpenWizard(state)
      // Clear state to avoid reopening on refresh
      navigate('/burning', { replace: true, state: {} })
    }
  }, [location.state])

  const handleNext = () => {
    if (currentStep === 0 && !platform) {
      message.warning('请选择任务场景')
      return
    }
    if (currentStep === 0 && !wizardData.software) {
      message.warning('请选择可执行文件')
      return
    }
    const selectedSoftwareRow = artifactRows.find((item) => Number(item.value) === Number(wizardData.software))
    if (currentStep === 0 && selectedSoftwareRow && !selectedSoftwareRow.selectable) {
      message.warning('该制品仓库记录未维护版本号，请先补齐版本后再创建任务')
      return
    }
    if (currentStep === 0 && !hasRepositoryVersion(selectedRepository)) {
      message.warning('当前制品仓库记录未维护版本号，请先补齐版本后再创建任务')
      return
    }
    if (currentStep === 1) {
      if ((platform === 'board' || platform === 'hybrid') && !wizardData.boardId) {
        message.warning('请选择板卡')
        return
      }
      if (platform === 'os' && !wizardData.osId) {
        message.warning('请选择操作系统')
        return
      }
    }
    setCurrentStep(currentStep + 1)
  }

  const handlePrev = () => setCurrentStep(currentStep - 1)

  const handlePlatformChange = (nextPlatform: 'board' | 'os' | 'hybrid') => {
    setPlatform(nextPlatform)
    if (nextPlatform === 'os') {
      setWizardData((prev: any) => ({
        ...prev,
        osId: prev.osId || DEFAULT_OS_ID,
      }))
    }
  }

  const updateWizardField = (key: string, value: any) => {
    if (['targetIp', 'targetPort', 'connectionProtocol', 'authType', 'loginUsername', 'loginPassword', 'loginPasswordless', 'privateKeyPath'].includes(key)) {
      setOsConnectionResult(null)
    }
    if (['burnMode', 'transferProtocol', 'serverPort', 'serialPort', 'ftpLoginUser', 'ftpLoginPassword', 'ftpPasswordless', 'boardTargetAddress', 'localIp', 'targetPath', 'serialLoginUser', 'serialLoginPassword', 'serialPasswordless', 'baudRate'].includes(key)) {
      setHybridConnectionPassed(false)
    }
    setOsFieldErrors((prev) => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
    if (REMEMBERED_SCRIPT_PARAM_FIELDS.has(key)) {
      persistScriptInputDraft(selectedScript?.id, key, value)
    }
    setWizardData((prev: any) => {
      const next = { ...prev, [key]: value }
      if (key === 'burnMode') {
        next.transferProtocol = value
        const normalizedMode = String(value || '').trim().toUpperCase()
        next.serverPort = normalizedMode.startsWith('TFTP+') ? '69' : normalizedMode.startsWith('SFTP+') ? '22' : '21'
      }
      if ((key === 'transferProtocol' || key === 'burnMode') && String(value || '').trim().toUpperCase().startsWith('FTP+')) {
        next.ftpPasswordless = false
      }
      return next
    })
  }

  const adjustWizardNumber = (key: 'retryCount' | 'timeoutMinutes', step: number, min: number, max: number) => {
    setWizardData((prev: any) => {
      const current = Number.isFinite(Number(prev[key])) ? Number(prev[key]) : min
      const next = Math.min(max, Math.max(min, current + step))
      return { ...prev, [key]: next }
    })
  }

  const updateWizardNumber = (key: 'retryCount' | 'timeoutMinutes', value: number | null, min: number, max: number) => {
    const nextValue = value === null || !Number.isFinite(Number(value))
      ? min
      : Math.min(max, Math.max(min, Math.trunc(Number(value))))
    updateWizardField(key, nextValue)
  }

  const handleHybridConnectionTest = async () => {
    const hybridMode = String(wizardData.burnMode || '').trim()
    const isFtpHybridMode = hybridMode.toUpperCase().startsWith('FTP+')
    if (!hybridMode) {
      message.warning('请选择烧录模式')
      return
    }
    if (!String(wizardData.boardTargetAddress || '').trim()) {
      message.warning('请输入设置板卡地址')
      return
    }
    if (hasInvalidWhitespace(wizardData.boardTargetAddress)) {
      message.warning('设置板卡地址格式不正确，请勿包含空格')
      return
    }
    if (!String(wizardData.serialPort || '').trim()) {
      message.warning('请选择串口')
      return
    }
    if (isFtpHybridMode && !String(wizardData.ftpLoginUser || '').trim()) {
      message.warning('请输入FTP登录用户')
      return
    }
    if (isFtpHybridMode && wizardData.ftpPasswordless) {
      message.warning('FTP 协议不支持免登录，请填写 FTP 登录密码')
      return
    }
    if (isFtpHybridMode && !wizardData.ftpPasswordless && !String(wizardData.ftpLoginPassword || '').trim()) {
      message.warning('请输入FTP登录密码')
      return
    }
    setHybridConnectionTesting(true)
    try {
      const res: any = await taskApi.testHybridConnection({
        burn_mode: hybridMode,
        transfer_protocol: hybridMode,
        target_ip: String(wizardData.boardTargetAddress || '').trim(),
        server_port: Number(wizardData.serverPort || 69),
        serial_port: String(wizardData.serialPort || '').trim(),
        ftp_login_user: String(wizardData.ftpLoginUser || '').trim(),
        ftp_login_password: String(wizardData.ftpLoginPassword || ''),
        ftp_passwordless: Boolean(wizardData.ftpPasswordless),
      })
      if (res?.data?.success) {
        setHybridConnectionPassed(true)
        message.success(res?.data?.message || '连接测试成功')
      } else {
        setHybridConnectionPassed(false)
        message.error(res?.data?.message || '连接测试失败')
      }
    } catch (error: any) {
      setHybridConnectionPassed(false)
      const targetIp = String(wizardData.boardTargetAddress || '').trim() || '-'
      const targetPort = Number(wizardData.serverPort || 69)
      const responseDetail = String(
        error?.response?.data?.detail
        || error?.response?.data?.message
        || '',
      ).trim()
      const isTimeout = String(error?.code || '').toUpperCase() === 'ECONNABORTED'
        || /timeout/i.test(String(error?.message || ''))
      message.error(
        responseDetail
        || (isTimeout
          ? `连接测试超时：${targetIp}:${targetPort}。请检查目标地址、端口和网络连接。`
          : `连接测试请求失败：${targetIp}:${targetPort}。请稍后重试并检查目标机服务。`),
      )
    } finally {
      setHybridConnectionTesting(false)
    }
  }

  const handleOsConnectionTest = async () => {
    if (!validateOsRequiredFields(false)) {
      return
    }
    setOsConnectionTesting(true)
    try {
      const res: any = await taskApi.testOsConnection({
        os_type: selectedOsType,
        target_ip: String(wizardData.targetIp || '').trim(),
        target_port: Number(wizardData.targetPort || 22),
        ftp_port: Number(wizardData.ftpPort || 21),
        deployment_mode: selectedOsType === 'yinghui' ? 'FTP' : String(wizardData.deploymentMode || '').trim(),
        harmony_device_id: String(wizardData.harmonyDeviceId || '').trim(),
        login_username: String(wizardData.loginUsername || '').trim(),
        login_passwordless: Boolean(wizardData.loginPasswordless),
        login_password: wizardData.authType === 'password' && !wizardData.loginPasswordless ? String(wizardData.loginPassword || '') : '',
        auth_type: wizardData.authType,
        private_key_path: wizardData.authType === 'key' ? String(wizardData.privateKeyPath || '').trim() : '',
        install_dir: String(wizardData.installDir || '').trim(),
      })
      const result = res?.data || null
      setOsConnectionResult(result)
      if (result?.success) message.success('连接测试通过')
      else message.error(result?.message || '连接测试失败')
    } catch (error: any) {
      const targetIp = String(wizardData.targetIp || '').trim() || '-'
      const targetPort = Number(wizardData.targetPort || 22)
      const responseDetail = String(
        error?.response?.data?.detail
        || error?.response?.data?.message
        || '',
      ).trim()
      const isTimeout = String(error?.code || '').toUpperCase() === 'ECONNABORTED'
        || /timeout/i.test(String(error?.message || ''))
      const failureMessage = responseDetail
        || (isTimeout
          ? `SSH 连接测试超时：${targetIp}:${targetPort}。请检查目标 IP、SSH 端口和网络路由。`
          : `连接测试请求失败：${targetIp}:${targetPort}。目标机上的 PCIDS 服务未返回测试结果，请重试并检查后端日志。`)
      const failureResult = { success: false, message: failureMessage }
      setOsConnectionResult(failureResult)
      message.error(failureMessage)
    } finally {
      setOsConnectionTesting(false)
    }
  }

  const selectedRepository = repositories.find((repo) => repo.id === wizardData.software)
  const effectiveInstallSource = selectedRepository
    ? getArtifactLocationInfo(selectedRepository).installSource
    : 'codearts'
  const selectedOsType = wizardData.osId ? osTypeMap[Number(wizardData.osId)] : ''
  const isHarmonyOs = selectedOsType === 'harmony'
  const isSylixOs = selectedOsType === 'yinghui'
  const isSshOnlyOs = selectedOsType === 'kylin' || selectedOsType === 'uos'
  const selectedBurner = burners.find((item) => item.id === wizardData.burnerId)
  const selectedBoard = boards.find((item) => item.id === wizardData.boardId)
  const selectedScript = scriptDetailsMap[wizardData.scriptId] || scripts.find((item) => item.id === wizardData.scriptId)
  const selectedBurnerOnline = wizardData.burnerId ? burnerOnlineMap[wizardData.burnerId] : undefined
  const selectedBurnerBusy = wizardData.burnerId
    ? burnerStatusMap[wizardData.burnerId] === 2 || Number(selectedBurner?.status) === 2
    : false
  const selectedBoardName = selectedBoard?.name
  const selectedArtifactExtension = extractArtifactExtension(
    selectedRepository?.name,
    selectedRepository?.file_url,
    selectedRepository?.server_saved_path,
    selectedRepository?.download_url,
  )
  useEffect(() => {
    if (platform !== 'os' || !selectedOsType) return
    setOsConnectionResult(null)
    setWizardData((prev: any) => {
      const next: any = { ...prev }
      if (selectedOsType === 'harmony') {
        next.connectionProtocol = 'HDC'
        next.targetPort = ''
        next.authType = 'none'
        next.loginUsername = ''
        next.loginPassword = ''
        next.installDir = next.installDir || '/data/local/tmp'
        next.harmonyDeviceId = next.harmonyDeviceId || wizardHarmonyDevices[0]?.id || ''
      } else if (selectedOsType === 'yinghui') {
        next.deploymentMode = 'FTP'
        next.ftpPort = String(next.ftpPort || '21')
        next.targetPort = String(next.ftpPort || '21')
        next.authType = 'password'
        next.loginUsername = next.loginUsername || 'root'
        next.loginPasswordless = Boolean(next.loginPasswordless)
        if (next.loginPasswordless) next.loginPassword = ''
        next.installDir = next.installDir || '/apps'
      } else {
        next.connectionProtocol = 'SSH'
        next.targetPort = String(next.targetPort || '22')
        next.authType = next.authType === 'none' ? 'password' : (next.authType || 'password')
        next.loginUsername = next.loginUsername || 'root'
        next.installDir = next.installDir || '/opt/control-app'
      }
      return next
    })
  }, [platform, selectedOsType, wizardHarmonyDevices])

  const showOsValidationErrors = (errors: Record<string, string>) => {
    setOsFieldErrors(errors)
    const firstMessage = Object.values(errors)[0]
    if (firstMessage) {
      message.warning(firstMessage)
    }
    return Object.keys(errors).length === 0
  }

  const validateOsRequiredFields = (requireConnectionTest: boolean) => {
    const errors: Record<string, string> = {}
    if (!wizardData.osId) errors.osId = '请选择操作系统'
    if (isHarmonyOs) {
      if (!String(wizardData.harmonyDeviceId || '').trim()) errors.harmonyDeviceId = '请选择鸿蒙设备'
    } else if (isSylixOs) {
      if (!String(wizardData.targetIp || '').trim()) errors.targetIp = '请输入目标地址'
      else if (hasInvalidWhitespace(wizardData.targetIp)) errors.targetIp = '目标地址格式不正确，请勿包含空格'
      if (!isValidPort(wizardData.ftpPort)) errors.ftpPort = 'FTP端口需在1-65535之间'
      if (!String(wizardData.loginUsername || '').trim()) errors.loginUsername = '请输入登录用户'
      if (!wizardData.loginPasswordless && !String(wizardData.loginPassword || '').trim()) errors.loginPassword = '请输入登录密码，或勾选免密登录'
      if (!String(wizardData.installDir || '').trim()) errors.installDir = '请输入安装目录'
    } else {
      if (!String(wizardData.targetIp || '').trim()) errors.targetIp = '请输入目标地址'
      else if (hasInvalidWhitespace(wizardData.targetIp)) errors.targetIp = '目标地址格式不正确，请勿包含空格'
      if (!isValidPort(wizardData.targetPort)) errors.targetPort = '目标端口需在1-65535之间'
      if (isSshOnlyOs && wizardData.connectionProtocol !== 'SSH') errors.connectionProtocol = '银河麒麟/统信UOS仅支持SSH连接'
      if (!String(wizardData.authType || '').trim()) errors.authType = '请选择认证方式'
      if (!String(wizardData.loginUsername || '').trim()) errors.loginUsername = '请输入登录用户名'
      if (wizardData.authType === 'password' && !String(wizardData.loginPassword || '').trim()) errors.loginPassword = '请输入登录密码'
      if (!String(wizardData.installDir || '').trim()) errors.installDir = '请输入安装目录'
    }
    if (requireConnectionTest && !osConnectionResult?.success) {
      errors.connectionTest = '请先完成连接测试并确保通过'
    }
    return showOsValidationErrors(errors)
  }

  const osFieldStatus = (field: string) => (osFieldErrors[field] ? 'error' : undefined)
  const renderOsFieldError = (field: string) =>
    osFieldErrors[field] ? <div style={{ color: '#ff4d4f', fontSize: 12, marginTop: 4 }}>{osFieldErrors[field]}</div> : null

  const isSelectedScriptSystem = Number(selectedScript?.is_system || 0) === 1
  const supportsWriteVerify = platform !== 'board' || !selectedScript?.id || isSelectedScriptSystem
  const selectedScriptDefaultConfig = isSelectedScriptSystem ? parseJsonSafe(selectedScript?.default_config_json) : null
  const requiredScriptConfigFields = Array.isArray(selectedScriptDefaultConfig?.required_fields)
    ? selectedScriptDefaultConfig.required_fields.map((item: any) => String(item))
    : []
  const isRequiredScriptConfig = (fieldName: string) =>
    Boolean(selectedScriptDefaultConfig?.[`${fieldName}_required`] || requiredScriptConfigFields.includes(fieldName))
  const scriptFieldConfigKeyMap: Record<string, string> = {
    interfaceType: 'interface_type',
    eraseMode: 'erase_mode',
    writeSpeed: 'write_speed_khz',
    startAddress: 'start_address',
    qspiFlashModel: 'qspi_flash_model',
    loaderType: 'loader_type',
    targetConfigFile: 'target_config_file',
    gelInitScript: 'gel_init_script',
    jtagChainIndex: 'jtag_chain_index',
    programVoltage: 'program_voltage',
    eepromWrite: 'eeprom_write',
    writeConfigBits: 'write_config_bits',
    executionOperation: 'execution_operation',
    bichinaBurnMode: 'bichina_burn_mode',
    preErase: 'pre_erase',
    blankCheck: 'blank_check',
    executeProgram: 'execute_program',
    tckFrequency: 'tck_frequency',
    cableIndex: 'cable_index',
    sdTargetPath: 'sd_target_path',
    formatSdCard: 'format_sd_card',
    completionAction: 'completion_action',
  }
  const isRequiredScriptField = (fieldName: string) => {
    const configKey = scriptFieldConfigKeyMap[fieldName]
    if (!configKey) return false
    if (configKey === 'start_address') {
      return isRequiredScriptConfig(configKey) || selectedArtifactExtension === 'bin'
    }
    return isRequiredScriptConfig(configKey)
  }
  const hasTargetConfigFile = selectedScriptDefaultConfig?.target_config_file !== undefined || selectedScriptDefaultConfig?.target_config_file_label
  const hasGelInitScript = selectedScriptDefaultConfig?.gel_init_script !== undefined || selectedScriptDefaultConfig?.gel_init_script_label
  const hasPreErase = Array.isArray(selectedScriptDefaultConfig?.pre_erase_options) && selectedScriptDefaultConfig.pre_erase_options.length > 0
  const hasBlankCheck = Array.isArray(selectedScriptDefaultConfig?.blank_check_options) && selectedScriptDefaultConfig.blank_check_options.length > 0
  const hasExecuteProgram = Array.isArray(selectedScriptDefaultConfig?.execute_program_options) && selectedScriptDefaultConfig.execute_program_options.length > 0
  const isCpldMode = hasPreErase || hasBlankCheck || hasExecuteProgram
  const hasSdTargetPath = selectedScriptDefaultConfig?.sd_target_path !== undefined || selectedScriptDefaultConfig?.sd_target_path_label
  const hasSelectedValidScript = Boolean(selectedScript?.id)
  const speedLabel = resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.speed_label, '烧录速度(khz)')
  const supportedScriptConfigFields = getSupportedScriptConfigFields(selectedScriptDefaultConfig)
  const supportedScriptConfigFieldsKey = supportedScriptConfigFields.join(',')
  const isAl321Script = selectedScript?.name === 'al321_fpga_mcu_flash'
  const isXds510plusScript = selectedScript?.name === 'xds510plus_dsp_flash'
  const isAl321FlashOperation = isAl321Script && wizardData.executionOperation === 'Flash固化'
  const scriptInputParameterDescriptors = [
    hasSdTargetPath
      ? {
          field: 'sdTargetPath',
          label: resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.sd_target_path_label, '目标SD卡位置'),
          placeholder: '请输入写入位置',
        }
      : null,
    hasTargetConfigFile && (!isAl321Script || isAl321FlashOperation)
      ? {
          field: 'targetConfigFile',
          label: resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.target_config_file_label, '目标配置文件'),
          placeholder: resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.target_config_file_placeholder, '请输入目标配置文件路径'),
          hint: resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.target_config_file_hint, ''),
        }
      : null,
    hasGelInitScript
      ? {
          field: 'gelInitScript',
          label: resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.gel_init_script_label, 'GEL 初始化脚本'),
          placeholder: '请输入 GEL 初始化脚本路径',
        }
      : null,
    supportedScriptConfigFields.includes('startAddress') && (!isAl321Script || isAl321FlashOperation)
      ? {
          field: 'startAddress',
          label: resolveScriptConfigDisplayText(selectedScriptDefaultConfig?.start_address_label, '起始地址'),
          placeholder: '请输入起始地址',
        }
      : null,
  ].filter(Boolean) as Array<{ field: string; label: string; placeholder: string; hint?: string }>
  const resolveTaskTimeoutSeconds = (source?: any) => {
    if (source?.timeout_seconds !== undefined && source?.timeout_seconds !== null && source?.timeout_seconds !== '') {
      return Number(source.timeout_seconds) || 120
    }
    if (source?.timeout_minutes !== undefined && source?.timeout_minutes !== null && source?.timeout_minutes !== '') {
      return Number(source.timeout_minutes) || 120
    }
    return 120
  }
  const versionCheckDisabled = !versionBaselineReady
  const keepLocalDisabled = effectiveInstallSource === 'local'
  const keepLocalTip = '在线安装时会按本次烧录器部署位置自动保留到本地或服务器; 不勾选则任务完成后自动清理临时下载包'
  const versionCheckTip = versionCheckDisabled ? '该软件版本为首次烧录，版本校验不可选' : '按历史标准版本校验当前可执行文件的一致性'
  const renderOptionWithTip = (label: string, tip: string) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span>{label}</span>
      <Tooltip title={tip}>
        <QuestionCircleOutlined style={{ color: '#86909c', fontSize: 13 }} onClick={(event) => event.preventDefault()} />
      </Tooltip>
    </span>
  )
  const effectiveWizardOptions = useMemo(() => {
    let options = filterExecutionOptionsForScript(wizardData.options, supportsWriteVerify)
    if (keepLocalDisabled) options = options.filter((item: string) => item !== 'local')
    if (versionCheckDisabled) options = options.filter((item: string) => item !== 'version')
    return options
  }, [keepLocalDisabled, supportsWriteVerify, versionCheckDisabled, wizardData.options])
  const getRepositoryVersionText = (repo?: any) =>
    firstFilledText(
      repo?.version,
      repo?.file_detail?.version,
      repo?.file_detail?.build_version,
    )

  const getRepositoryChecksum = (repo?: any) =>
    firstFilledText(
      repo?.sha256,
      repo?.file_detail?.sha256,
      repo?.file_detail?.checksums?.sha256,
      repo?.md5,
      repo?.file_detail?.md5,
      repo?.file_detail?.checksums?.md5,
    )

  useEffect(() => {
    if (!isWizardOpen || !selectedRepository?.id) {
      setVersionBaselineReady(false)
      setVersionBaselineChecksum('')
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const res: any = await taskApi.getVersionBaselineStatus(Number(selectedRepository.id))
        if (cancelled) return
        const historyChecksum = String(res?.data?.history_checksum || '').trim()
        const hasBaseline = Boolean(res?.data?.has_baseline) && Boolean(historyChecksum)
        setVersionBaselineReady(hasBaseline)
        setVersionBaselineChecksum(historyChecksum)
      } catch {
        if (!cancelled) {
          setVersionBaselineReady(false)
          setVersionBaselineChecksum('')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isWizardOpen, selectedRepository?.id])

  useEffect(() => {
    if (!versionCheckDisabled || !Array.isArray(wizardData.options) || !wizardData.options.includes('version')) return
    updateWizardField('options', wizardData.options.filter((item: string) => item !== 'version'))
  }, [versionCheckDisabled, wizardData.options])

  useEffect(() => {
    if (!keepLocalDisabled || !Array.isArray(wizardData.options) || !wizardData.options.includes('local')) return
    updateWizardField('options', wizardData.options.filter((item: string) => item !== 'local'))
  }, [keepLocalDisabled, wizardData.options])

  const validateCommonTaskOptions = () => {
    const retryCount = Number(wizardData.retryCount ?? 0)
    if (!Number.isInteger(retryCount) || retryCount < 0 || retryCount > 5) {
      message.warning('烧录失败重试次数需在0-5之间')
      return false
    }
    const timeoutSeconds = Number(wizardData.timeoutMinutes ?? 120)
    if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > MAX_TASK_TIMEOUT_SECONDS) {
      message.warning(`任务超时时间需在1-${MAX_TASK_TIMEOUT_SECONDS}秒之间`)
      return false
    }
    if (wizardData.options?.includes('integrity') && !getRepositoryChecksum(selectedRepository)) {
      message.warning('当前软件缺少MD5/SHA256校验值，无法启用完整性校验')
      return false
    }
    if (wizardData.options?.includes('version') && (versionCheckDisabled || !versionBaselineChecksum)) {
      message.warning('该软件版本为首次烧录，暂不可启用版本校验')
      return false
    }
    return true
  }

  const validateOsConfig = () => {
    if (!validateOsRequiredFields(true)) return false
    return validateCommonTaskOptions()
    if (!wizardData.osId) {
      message.warning('请选择操作系统')
      return false
    }
    if (isHarmonyOs) {
      if (!String(wizardData.harmonyDeviceId || '').trim()) {
        message.warning('请选择鸿蒙设备')
        return false
      }
      return validateCommonTaskOptions()
    }
    if (isSylixOs) {
      if (!String(wizardData.targetIp || '').trim()) {
        message.warning('请输入目标地址')
        return false
      }
      if (hasInvalidWhitespace(wizardData.targetIp)) {
        message.warning('目标地址格式不正确，请勿包含空格')
        return false
      }
      if (!isValidPort(wizardData.ftpPort)) {
        message.warning('FTP端口需在1-65535之间')
        return false
      }
      if (!String(wizardData.loginUsername || '').trim()) {
        message.warning('请输入登录用户')
        return false
      }
      if (!wizardData.loginPasswordless && !String(wizardData.loginPassword || '').trim()) {
        message.warning('请输入登录密码，或勾选免密登录')
        return false
      }
      if (!String(wizardData.installDir || '').trim()) {
        message.warning('请输入安装目录')
        return false
      }
      return validateCommonTaskOptions()
    }
    if (!String(wizardData.targetIp || '').trim()) {
      message.warning('请输入目标地址')
      return false
    }
    if (hasInvalidWhitespace(wizardData.targetIp)) {
      message.warning('目标地址格式不正确，请勿包含空格')
      return false
    }
    if (!String(wizardData.targetPort || '').trim()) {
      message.warning('请输入目标端口')
      return false
    }
    if (!isValidPort(wizardData.targetPort)) {
      message.warning('目标端口需在1-65535之间')
      return false
    }
    if (!String(wizardData.connectionProtocol || '').trim()) {
      message.warning('请选择连接协议')
      return false
    }
    if (isSshOnlyOs && wizardData.connectionProtocol !== 'SSH') {
      message.warning('银河麒麟/统信UOS仅支持SSH连接')
      return false
    }
    if (!String(wizardData.authType || '').trim()) {
      message.warning('请选择认证方式')
      return false
    }
    if (!String(wizardData.loginUsername || '').trim()) {
      message.warning('请输入登录用户名')
      return false
    }
    if (wizardData.authType === 'password' && !String(wizardData.loginPassword || '').trim()) {
      message.warning('请输入登录密码')
      return false
    }
    if (!String(wizardData.installDir || '').trim()) {
      message.warning('请输入安装目录')
      return false
    }
    if (!osConnectionResult?.success) {
      message.warning('请先完成连接测试并确保通过')
      return false
    }
    return validateCommonTaskOptions()
  }

  const validateHybridConfig = () => {
    const hybridMode = String(wizardData.burnMode || '').trim()
    const requiresPartitionFtpAuth = selectedScript?.name === SYLIXOS_HYBRID_SCRIPT_NAME
    if (!wizardData.boardId) {
      message.warning('请选择板卡')
      return false
    }
    if (!hybridMode) {
      message.warning('请选择烧录模式')
      return false
    }
    if (!wizardData.scriptId || !selectedScript) {
      message.warning('请选择混合协同执行脚本')
      return false
    }
    if (!String(wizardData.serialPort || '').trim()) {
      message.warning('请选择串口')
      return false
    }
    if (!String(wizardData.baudRate || '').trim()) {
      message.warning('请选择波特率')
      return false
    }
    if (!String(wizardData.serialLoginUser || '').trim()) {
      message.warning('请输入串口登录用户')
      return false
    }
    if (!wizardData.serialPasswordless && !String(wizardData.serialLoginPassword || '').trim()) {
      message.warning('请输入串口登录密码')
      return false
    }
    if (requiresPartitionFtpAuth && !String(wizardData.ftpLoginUser || '').trim()) {
      message.warning('请输入板卡当前FTP登录用户')
      return false
    }
    if (requiresPartitionFtpAuth && wizardData.ftpPasswordless) {
      message.warning('FTP 协议不支持免登录，请填写 FTP 登录密码')
      return false
    }
    if (requiresPartitionFtpAuth && !wizardData.ftpPasswordless && !String(wizardData.ftpLoginPassword || '').trim()) {
      message.warning('请输入板卡当前FTP登录密码')
      return false
    }
    if (!String(wizardData.boardTargetAddress || '').trim()) {
      message.warning('请输入设置板卡地址')
      return false
    }
    if (hasInvalidWhitespace(wizardData.boardTargetAddress)) {
      message.warning('设置板卡地址格式不正确，请勿包含空格')
      return false
    }
    if (!String(wizardData.localIp || '').trim()) {
      message.warning('请选择本地IP')
      return false
    }
    if (!String(wizardData.targetPath || '').trim()) {
      message.warning('请输入目标路径')
      return false
    }
    if (!hybridConnectionPassed) {
      message.warning('请先完成连接测试并确保通过')
      return false
    }
    return validateCommonTaskOptions()
  }

  const validateBoardConfig = () => {
    const supportedFieldSet = new Set(supportedScriptConfigFields)
    if (!wizardData.boardId) {
      message.warning('请选择板卡')
      return false
    }
    if (!wizardData.burnerId) {
      message.warning('请选择设备')
      return false
    }
    if (!selectedBurner) {
      message.warning('所选设备不存在，请重新选择')
      return false
    }
    if (burnerScanLoading) {
      message.warning('设备状态仍在检测中，请等待刷新完成后再提交')
      return false
    }
    if (selectedBurner.is_enabled === false || selectedBurner.is_enabled === 0) {
      message.warning('所选设备已被禁用，请更换其他设备')
      return false
    }
    if (selectedBurnerBusy) {
      message.warning('所选设备正在执行其他烧录任务，请等待任务结束或更换设备')
      return false
    }
    if (burnerOnlineMap[wizardData.burnerId] !== true) {
      message.warning(burnerScanError || '尚未确认所选设备在线，请先刷新设备状态')
      return false
    }
    if (!wizardData.scriptId || !selectedScript) {
      message.warning('请选择烧录脚本')
      return false
    }
    if (supportedFieldSet.has('interfaceType') && isRequiredScriptField('interfaceType') && !String(wizardData.interfaceType || '').trim()) {
      message.warning('请选择接口类型')
      return false
    }
    if (supportedFieldSet.has('eraseMode') && isRequiredScriptField('eraseMode') && !String(wizardData.eraseMode || '').trim()) {
      message.warning('请选择擦除方式')
      return false
    }
    if (supportedFieldSet.has('writeSpeed') && isRequiredScriptField('writeSpeed') && !String(wizardData.writeSpeed || '').trim()) {
      message.warning(`请选择${speedLabel}`)
      return false
    }
    if (supportedFieldSet.has('qspiFlashModel') && isRequiredScriptField('qspiFlashModel') && !String(wizardData.qspiFlashModel || '').trim()) {
      message.warning('请选择QSPI Flash型号')
      return false
    }
    if (supportedFieldSet.has('loaderType') && isRequiredScriptField('loaderType') && !String(wizardData.loaderType || '').trim()) {
      message.warning('请选择Loader类型')
      return false
    }
    if (supportedFieldSet.has('targetConfigFile') && isRequiredScriptConfig('target_config_file') && !String(wizardData.targetConfigFile || '').trim()) {
      message.warning('请输入目标配置文件')
      return false
    }
    if (supportedFieldSet.has('gelInitScript') && isRequiredScriptConfig('gel_init_script') && !String(wizardData.gelInitScript || '').trim()) {
      message.warning('请输入GEL初始化脚本')
      return false
    }
    if (supportedFieldSet.has('startAddress') && isRequiredScriptField('startAddress') && !String(wizardData.startAddress || '').trim()) {
      message.warning('请输入起始地址')
      return false
    }
    if (supportedFieldSet.has('jtagChainIndex') && isRequiredScriptField('jtagChainIndex') && (wizardData.jtagChainIndex === undefined || wizardData.jtagChainIndex === null || String(wizardData.jtagChainIndex).trim() === '')) {
      message.warning('请输入JTAG链路序号')
      return false
    }
    if (supportedFieldSet.has('programVoltage') && isRequiredScriptField('programVoltage') && !String(wizardData.programVoltage || '').trim()) {
      message.warning('请选择编程电压')
      return false
    }
    if (supportedFieldSet.has('eepromWrite') && isRequiredScriptField('eepromWrite') && !String(wizardData.eepromWrite || '').trim()) {
      message.warning('请选择EEPROM是否擦写')
      return false
    }
    if (supportedFieldSet.has('writeConfigBits') && isRequiredScriptField('writeConfigBits') && !String(wizardData.writeConfigBits || '').trim()) {
      message.warning('请选择写入配置位')
      return false
    }
    if (supportedFieldSet.has('executionOperation') && isRequiredScriptField('executionOperation') && !String(wizardData.executionOperation || '').trim()) {
      message.warning('请选择执行操作')
      return false
    }
    if (isAl321FlashOperation) {
      if (!String(wizardData.targetConfigFile || '').trim()) {
        message.warning('请选择 ZynqMP ELF 文件')
        return false
      }
      if (!String(wizardData.targetConfigFile || '').trim().toLowerCase().endsWith('.elf')) {
        message.warning('ZynqMP ELF 文件必须是 .elf 格式')
        return false
      }
      if (!String(wizardData.qspiFlashModel || '').trim()) {
        message.warning('请选择 QSPI 连接方式')
        return false
      }
    }
    if (supportedFieldSet.has('bichinaBurnMode') && isRequiredScriptField('bichinaBurnMode') && !String(wizardData.bichinaBurnMode || '').trim()) {
      message.warning('请选择Bichina烧录参数')
      return false
    }
    if (supportedFieldSet.has('preErase') && isRequiredScriptField('preErase') && !String(wizardData.preErase || '').trim()) {
      message.warning(hasPreErase && isCpldMode ? '请选择擦除器件' : '请选择编程前擦除')
      return false
    }
    if (supportedFieldSet.has('blankCheck') && isRequiredScriptField('blankCheck') && !String(wizardData.blankCheck || '').trim()) {
      message.warning('请选择空白检查')
      return false
    }
    if (supportedFieldSet.has('executeProgram') && isRequiredScriptField('executeProgram') && !String(wizardData.executeProgram || '').trim()) {
      message.warning('请选择执行编程')
      return false
    }
    if (supportedFieldSet.has('tckFrequency') && isRequiredScriptField('tckFrequency') && !String(wizardData.tckFrequency || '').trim()) {
      message.warning('请选择TCK频率')
      return false
    }
    if (supportedFieldSet.has('cableIndex') && isRequiredScriptField('cableIndex') && (wizardData.cableIndex === undefined || wizardData.cableIndex === null || String(wizardData.cableIndex).trim() === '')) {
      message.warning('请输入Cable Index')
      return false
    }
    if (supportedFieldSet.has('sdTargetPath') && isRequiredScriptConfig('sd_target_path') && !String(wizardData.sdTargetPath || '').trim()) {
      message.warning('请输入目标SD卡位置')
      return false
    }
    if (supportedFieldSet.has('formatSdCard') && isRequiredScriptField('formatSdCard') && !String(wizardData.formatSdCard || '').trim()) {
      message.warning('请选择是否格式化SD卡')
      return false
    }
    if (supportedFieldSet.has('completionAction') && isRequiredScriptField('completionAction') && !String(wizardData.completionAction || '').trim()) {
      message.warning('请选择完成后动作')
      return false
    }
    return validateCommonTaskOptions()
  }

  const getTaskVersionText = (record?: any) => {
    const matchedRepository =
      repositories.find((repo) => Number(repo?.id) === Number(record?.repository_id)) ||
      repositories.find((repo) => repo?.project_key === record?.project_key && repo?.name === record?.repository_name) ||
      null
    return firstFilledText(getRepositoryVersionText(matchedRepository), record?.software_version)
  }
  const getTaskSoftwareName = (record?: any) => firstFilledText(record?.software_name, record?.repository_name) || '-'
  const getTaskProjectName = (record?: any) =>
    firstFilledText(record?.project_name, record?.project_key === currentProject.projectKey ? currentProject.projectName : '') || '-'
  
  const getFileSourceInfo = (task?: any) => {
    const config = parseJsonSafe(task?.config_json)
    const installSource = String(config?.install_source || '').trim()
    const taskLocalPath = String(task?.local_path || task?.file_detail?.local_path || '').trim()
    const taskServerPath = String(task?.server_path || task?.file_detail?.server_path || '').trim()
    const taskServerTarget = String(task?.server_target || task?.storage_target || task?.file_detail?.server_target || task?.file_detail?.storage_target || '').trim()
    const taskDisplayPath = String(task?.display_path || task?.download_uri || task?.file_url || task?.project_key || '').trim()
    if (installSource === 'server' && (task?.server_exists || taskServerPath || taskServerTarget)) {
      return { sourceType: '服务器', sourcePath: taskServerTarget || taskServerPath || '-' }
    }
    if (installSource === 'local' && (task?.local_exists || taskLocalPath)) {
      return { sourceType: '本地', sourcePath: taskLocalPath || '-' }
    }
    if (task?.local_exists || taskLocalPath || task?.source_type === 'local_upload') {
      return { sourceType: '本地', sourcePath: taskLocalPath || taskDisplayPath || '-' }
    }
    if (task?.server_exists || taskServerPath || taskServerTarget) {
      return { sourceType: '服务器', sourcePath: taskServerTarget || taskServerPath || '-' }
    }
    if (taskDisplayPath) {
      return { sourceType: 'CodeArts 制品仓库', sourcePath: taskDisplayPath }
    }
    const matchedRepository =
      repositories.find((repo) => Number(repo?.id) === Number(task?.repository_id)) ||
      repositories.find((repo) => repo?.project_key === task?.project_key && repo?.name === task?.repository_name) ||
      null
    if (matchedRepository) {
      const locationState = getRepositoryLocationState(matchedRepository)
      if (installSource === 'server' && locationState.serverExists) {
        return {
          sourceType: '服务器',
          sourcePath: locationState.serverTarget || locationState.serverPath || '-',
        }
      }
      if (installSource === 'local' && locationState.localExists) {
        return {
          sourceType: '本地',
          sourcePath: locationState.localPath || '-',
        }
      }
      if (locationState.localExists) {
        return {
          sourceType: '本地',
          sourcePath: locationState.localPath || '-',
        }
      }
      if (locationState.serverExists) {
        return {
          sourceType: '服务器',
          sourcePath: locationState.serverTarget || locationState.serverPath || '-',
        }
      }
      return {
        sourceType: 'CodeArts 制品仓库',
        sourcePath: matchedRepository?.display_path || matchedRepository?.download_uri || task?.file_url || '-',
      }
    }
    if (installSource === 'server') {
      return { sourceType: '服务器', sourcePath: task?.file_url || '-' }
    }
    if (installSource === 'local') {
      return { sourceType: '本地', sourcePath: task?.file_url || '-' }
    }
    return { sourceType: 'CodeArts 制品仓库', sourcePath: task?.file_url || task?.project_key || '-' }
  }

  const getTaskChipType = (task?: any) =>
    firstFilledText(
      task?.chip_type,
      filterBoards.find((item) => Number(item?.id) === Number(task?.product_id))?.chip_type,
    )

  const formatTaskDuration = (task?: any) => {
    const startAt = task?.started_at
    const endAt = task?.finished_at || (isTaskActive(task) ? new Date() : null)
    if (!startAt || !endAt) return '-'
    const seconds = Math.max(parseServerDateTime(endAt).diff(parseServerDateTime(startAt), 'second'), 0)
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const remainSeconds = seconds % 60
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${remainSeconds.toString().padStart(2, '0')}`
  }

  const parseTaskLogLine = (line: string) => {
    const timestamped = line.match(/^\[([^\]]+)\]\s+\[([^\]]+)\]\s*(.*)$/)
    if (timestamped) {
      return {
        time: parseServerDateTime(timestamped[1]).format('HH:mm:ss'),
        tag: timestamped[2],
        content: timestamped[3],
      }
    }
    const legacy = line.match(/^\[([^\]]+)\]\s*(.*)$/)
    if (legacy) {
      return { time: '', tag: legacy[1], content: legacy[2] }
    }
    return { time: '', tag: '', content: line }
  }

  const getMergedTaskConfig = (task?: any) => {
    const config = parseJsonSafe(task?.config_json)
    const scriptDefaults = parseJsonSafe(task?.script_default_config_json)
    return { ...(scriptDefaults || {}), ...(config || {}) }
  }

  const getTaskProgressPercent = (task?: any) => {
    if (task?.status === 2) return 100
    return Math.max(0, Math.min(Number(task?.progress_percent || 0), 100))
  }

  const renderDetailValue = (value?: any) => (
    <div style={detailFieldValueStyle}>{firstFilledText(decodeMojibakeString(value)) || '-'}</div>
  )

  const renderDetailBlockValue = (value?: any) => (
    <div style={detailBlockValueStyle}>{firstFilledText(decodeMojibakeString(value)) || '-'}</div>
  )

  const renderParamField = (label: string, value?: any) => (
    <div>
      <div style={detailFieldLabelStyle}>{label}</div>
      {renderDetailValue(value)}
    </div>
  )

  const renderParamBlockField = (label: string, value?: any) => (
    <div style={{ gridColumn: '1 / -1' }}>
      <div style={detailFieldLabelStyle}>{label}</div>
      {renderDetailBlockValue(value)}
    </div>
  )
  
  const renderTaskParams = (task?: any) => {
    const taskConfig = parseJsonSafe(task?.config_json) || {}
    const mergedConfig = getMergedTaskConfig(task)
    const taskScriptDefaults = parseJsonSafe(task?.script_default_config_json)
    const taskSupportedScriptConfigFields = new Set(getSupportedScriptConfigFields(taskScriptDefaults))
    const taskType = taskConfig?.task_type || taskConfig?.platform || mergedConfig?.task_type || mergedConfig?.platform || 'board'
    const completionActionLabel = resolveScriptConfigDisplayText(taskScriptDefaults?.completion_action_label, '完成后动作')
    const boardParamItems: Array<{ key: string; label: string; value: any; block?: boolean }> = []
    const addBoardParam = (key: string, label: string, value: any, options?: { block?: boolean; includeWhenEmpty?: boolean }) => {
      const includeWhenEmpty = Boolean(options?.includeWhenEmpty)
      const hasSupportedField = taskSupportedScriptConfigFields.has(key)
      const normalizedValue = decodeMojibakeString(value)
      const hasValue = normalizedValue !== undefined && normalizedValue !== null && String(normalizedValue).trim() !== ''
      if (!hasSupportedField && !hasValue && !includeWhenEmpty) return
      boardParamItems.push({ key, label, value, block: options?.block })
    }
    
    if (taskType === 'os') {
      return (
        <div>
          <div style={detailSubSectionTitleStyle}>连接信息</div>
          <div style={detailFieldGridStyle}>
            {renderParamField('目标地址', task?.target_ip || taskConfig?.target_ip)}
            {renderParamField('目标端口', task?.target_port || taskConfig?.target_port)}
            {renderParamField('连接协议', taskConfig?.connection_protocol || 'SSH')}
            {renderParamField('认证方式', taskConfig?.auth_type === 'key' ? '密钥认证' : '密码认证')}
            {renderParamField('登录用户名', taskConfig?.login_username)}
            {taskConfig?.auth_type === 'key' ? renderParamField('密钥地址', taskConfig?.private_key_path || '默认 SSH 密钥') : null}
          </div>

          <div style={{ ...detailSubSectionTitleStyle, marginTop: 24 }}>安装参数</div>
          <div style={detailFieldGridStyle}>
            {renderParamBlockField('安装目录', taskConfig?.install_dir)}
            {renderParamField('开机自启', Boolean(taskConfig?.boot_autostart) ? '已开启' : '未开启')}
            {renderParamField('可执行文件留存', Boolean(taskConfig?.keep_local ?? task?.keep_local) ? '已开启' : '未开启')}
            {renderParamField('版本一致性校验', Boolean(taskConfig?.version_check ?? task?.version_check) ? '已开启' : '未开启')}
            {renderParamField('失败重试次数', `${taskConfig?.retries ?? 1}`)}
            {renderParamField('任务超时时间', `${resolveTaskTimeoutSeconds(taskConfig)} 秒`)}
            {renderParamBlockField('备注', taskConfig?.remark)}
          </div>
        </div>
      )
    }

    if (taskType === 'hybrid') {
      return (
        <div>
          <div style={detailSubSectionTitleStyle}>选择烧录模式</div>
          <div style={detailFieldGridStyle}>
            {renderParamField('烧录模式', taskConfig?.burn_mode)}
            {renderParamField('执行脚本', task?.script_name)}
          </div>

          <div style={{ ...detailSubSectionTitleStyle, marginTop: 24 }}>目标连接信息</div>
          <div style={detailFieldGridStyle}>
            {renderParamField('串口', taskConfig?.serial_port)}
            {renderParamField('波特率', taskConfig?.baud_rate)}
            {renderParamField('服务端口', taskConfig?.server_port || task?.target_port)}
            {renderParamField('串口登录用户', taskConfig?.serial_login_user)}
            {renderParamField('FTP登录用户', taskConfig?.ftp_login_user)}
            {renderParamBlockField('设置板卡地址', taskConfig?.configured_board_address || taskConfig?.board_target_address || task?.target_ip)}
            {renderParamField('本地IP', taskConfig?.local_ip)}
            {renderParamBlockField('目标路径', taskConfig?.target_path)}
          </div>

          <div style={{ ...detailSubSectionTitleStyle, marginTop: 24 }}>烧录选项</div>
          <div style={detailFieldGridStyle}>
            {renderParamField('可执行文件留存', Boolean(taskConfig?.keep_local ?? task?.keep_local) ? '已开启' : '未开启')}
            {renderParamField('版本校验', Boolean(taskConfig?.version_check ?? task?.version_check) ? '已开启' : '未开启')}
            {renderParamField('完整性校验(MD5|SHA256)', Boolean(taskConfig?.integrity ?? task?.integrity) ? '已开启' : '未开启')}
            {renderParamField('烧录失败重试次数', `${taskConfig?.retries ?? 1}`)}
            {renderParamField('任务超时时间', `${resolveTaskTimeoutSeconds(taskConfig)} 秒`)}
            {renderParamBlockField('备注', taskConfig?.remark)}
          </div>
        </div>
      )
    }
    
    return (
      <div>
        <div style={detailSubSectionTitleStyle}>烧录器 & 脚本</div>
        <div style={detailFieldGridStyle}>
          <div>
            <div style={detailFieldLabelStyle}>烧录器</div>
            {renderDetailValue(mergedConfig?.burner_name || task?.burner_name)}
          </div>
          <div>
            <div style={detailFieldLabelStyle}>芯片类型</div>
            {getTaskChipType(task) ? (
              <Tag color="blue" style={{ borderRadius: 10, margin: 0 }}>{getTaskChipType(task)}</Tag>
            ) : renderDetailValue('-')}
          </div>
          <div style={{ gridColumn: '1 / -1' }}>
            <div style={detailFieldLabelStyle}>烧录脚本</div>
            {renderDetailValue(task?.script_name)}
          </div>
          <div>
            <div style={detailFieldLabelStyle}>IDE</div>
            {renderDetailValue(taskConfig?.ide_name || task?.script_ide_name || '无IDE')}
          </div>
        </div>

        <div style={{ ...detailSubSectionTitleStyle, marginTop: 24 }}>烧录参数</div>
        <div style={detailFieldGridStyle}>
          {(() => {
            const sidebarExecutionOperation = String(taskConfig?.execution_operation || '').trim()
            const sidebarShowField = (field: string) => isFieldVisibleForOperation(field, sidebarExecutionOperation)
            addBoardParam('targetFilePath', mergedConfig?.target_file_path_label || '目标文件路径', taskConfig?.target_file_path || task?.executable, { block: true, includeWhenEmpty: true })
            if (sidebarShowField('interfaceType')) addBoardParam('interfaceType', resolveScriptConfigDisplayText(taskScriptDefaults?.interface_type_label, '接口类型'), taskConfig?.interface_type)
            if (sidebarShowField('executionOperation')) addBoardParam('executionOperation', resolveScriptConfigDisplayText(taskScriptDefaults?.execution_operation_label, '执行操作'), taskConfig?.execution_operation)
            if (sidebarShowField('eraseMode')) addBoardParam('eraseMode', resolveScriptConfigDisplayText(taskScriptDefaults?.erase_mode_label, '擦除方式'), taskConfig?.erase_mode)
            if (sidebarShowField('qspiFlashModel')) addBoardParam('qspiFlashModel', resolveScriptConfigDisplayText(taskScriptDefaults?.qspi_flash_model_label, 'QSPI Flash型号'), taskConfig?.qspi_flash_model)
            if (sidebarShowField('targetConfigFile')) addBoardParam('targetConfigFile', resolveScriptConfigDisplayText(taskScriptDefaults?.target_config_file_label, '目标配置文件'), taskConfig?.target_config_file, { block: true })
            addBoardParam('gelInitScript', resolveScriptConfigDisplayText(taskScriptDefaults?.gel_init_script_label, 'GEL 初始化脚本'), taskConfig?.gel_init_script, { block: true })
            if (sidebarShowField('startAddress')) addBoardParam('startAddress', resolveScriptConfigDisplayText(taskScriptDefaults?.start_address_label, '起始地址'), taskConfig?.start_address)
            if (sidebarShowField('writeSpeed')) addBoardParam('writeSpeed', resolveScriptConfigDisplayText(taskScriptDefaults?.speed_label, '烧录速度(khz)'), taskConfig?.write_speed_khz ? `${taskConfig?.write_speed_khz}` : undefined)
            addBoardParam('loaderType', resolveScriptConfigDisplayText(taskScriptDefaults?.loader_type_label, 'Loader 类型'), taskConfig?.loader_type)
            addBoardParam('jtagChainIndex', resolveScriptConfigDisplayText(taskScriptDefaults?.jtag_chain_index_label, 'JTAG链路序号'), taskConfig?.jtag_chain_index)
            addBoardParam('programVoltage', resolveScriptConfigDisplayText(taskScriptDefaults?.program_voltage_label, '编程电压'), taskConfig?.program_voltage)
            addBoardParam('eepromWrite', resolveScriptConfigDisplayText(taskScriptDefaults?.eeprom_write_label, 'EEPROM 是否烧写'), taskConfig?.eeprom_write)
            addBoardParam('writeConfigBits', resolveScriptConfigDisplayText(taskScriptDefaults?.write_config_bits_label, '写入配置位'), taskConfig?.write_config_bits)
            addBoardParam('bichinaBurnMode', resolveScriptConfigDisplayText(taskScriptDefaults?.bichina_burn_mode_label, 'Bichina烧录参数'), taskConfig?.bichina_burn_mode)
            addBoardParam('preErase', resolveScriptConfigDisplayText(taskScriptDefaults?.pre_erase_label, '编程前擦除'), taskConfig?.pre_erase)
            addBoardParam('blankCheck', resolveScriptConfigDisplayText(taskScriptDefaults?.blank_check_label, '空白检查'), taskConfig?.blank_check)
            addBoardParam('executeProgram', resolveScriptConfigDisplayText(taskScriptDefaults?.execute_program_label, '执行编程'), taskConfig?.execute_program)
            addBoardParam('tckFrequency', resolveScriptConfigDisplayText(taskScriptDefaults?.tck_frequency_label, 'TCK 频率'), taskConfig?.tck_frequency)
            addBoardParam('cableIndex', resolveScriptConfigDisplayText(taskScriptDefaults?.cable_index_label, 'Cable Index'), taskConfig?.cable_index)
            addBoardParam('sdTargetPath', resolveScriptConfigDisplayText(taskScriptDefaults?.sd_target_path_label, '目标SD卡位置'), taskConfig?.sd_target_path, { block: true })
            addBoardParam('formatSdCard', resolveScriptConfigDisplayText(taskScriptDefaults?.format_sd_card_label, '拷贝前格式化 SD 卡'), taskConfig?.format_sd_card)
            if (sidebarShowField('completionAction')) addBoardParam('completionAction', completionActionLabel, taskConfig?.completion_action)
            return boardParamItems.map(item =>
              item.block ? (
                <Fragment key={item.key}>{renderParamBlockField(item.label, item.value)}</Fragment>
              ) : (
                <Fragment key={item.key}>{renderParamField(item.label, item.value)}</Fragment>
              ),
            )
          })()}
        </div>

        <div style={{ ...detailSubSectionTitleStyle, marginTop: 24 }}>执行选项</div>
        <div style={detailFieldGridStyle}>
          {renderParamField('可执行文件留存', Boolean(taskConfig?.keep_local ?? task?.keep_local) ? '已开启' : '未开启')}
          {renderParamField('版本一致性校验', Boolean(taskConfig?.version_check ?? task?.version_check) ? '已开启' : '未开启')}
          {renderParamField('完整性校验(MD5|SHA256)', Boolean(taskConfig?.integrity ?? task?.integrity) ? '已开启' : '未开启')}
          {renderParamField('写入后校验', Boolean(taskConfig?.write_verify ?? task?.write_verify) ? '已开启' : '未开启')}
          {renderParamField('烧录失败重试次数', `${taskConfig?.retries ?? 1}`)}
          {renderParamField('任务超时时间', `${resolveTaskTimeoutSeconds(taskConfig)} 秒`)}
          {renderParamBlockField('备注', taskConfig?.remark)}
        </div>
      </div>
    )
  }
  
  const hasConsistencyReport = (record?: any) => {
    if (Number(record?.version_check || 0) !== 1) return false
    return Boolean(
      Number(record?.consistency_passed) === 0 ||
      Number(record?.consistency_passed) === 1 ||
      firstFilledText(record?.current_sha256, record?.current_md5, record?.history_checksum, record?.expected_checksum),
    )
  }
  const hasRepositoryVersion = (repo?: any) => Boolean(getRepositoryVersionText(repo))
  const burnerBoundScripts = scripts.filter((script: any) => {
    if (platform !== 'board') return false
    if (String(script?.task_type || 'board') !== 'board') return false
    if (!hasConfiguredAssociation(script?.associated_burner)) return false
    return matchAssociation(script.associated_board, [selectedBoard?.name, selectedBoard?.chip_model, selectedBoard?.chip_type])
  })
  const recommendedBurners = burners
    .filter((burner) =>
      burnerBoundScripts.some((script: any) =>
        matchAssociation(script.associated_burner, [burner.name, burner.type, burner.sn, burner.port]),
      ),
    )
    .sort((a, b) => {
      const aOnline = burnerOnlineMap[a.id] === true ? 1 : 0
      const bOnline = burnerOnlineMap[b.id] === true ? 1 : 0
      const aBusy = burnerStatusMap[a.id] === 2 || Number(a.status) === 2 ? 1 : 0
      const bBusy = burnerStatusMap[b.id] === 2 || Number(b.status) === 2 ? 1 : 0
      const aEnabled = a.is_enabled !== false && a.is_enabled !== 0 ? 1 : 0
      const bEnabled = b.is_enabled !== false && b.is_enabled !== 0 ? 1 : 0
      if (aEnabled !== bEnabled) return bEnabled - aEnabled
      if (aBusy !== bBusy) return aBusy - bBusy
      if (aOnline !== bOnline) return bOnline - aOnline
      return String(a.name || '').localeCompare(String(b.name || ''))
    })
  const visibleBurners = recommendedBurners
  const busyBurnerCount = visibleBurners.filter((item) => burnerStatusMap[item.id] === 2 || Number(item.status) === 2).length
  const onlineBurnerCount = visibleBurners.filter((item) => burnerOnlineMap[item.id] === true && burnerStatusMap[item.id] !== 2 && Number(item.status) !== 2).length
  const offlineBurnerCount = visibleBurners.filter((item) => burnerOnlineMap[item.id] === false).length
  const recommendedBurnerRank = new Map(recommendedBurners.map((item, index) => [item.id, index]))
  const sortedBurners = [...visibleBurners].sort((a, b) => {
    const aRank = recommendedBurnerRank.has(a.id) ? recommendedBurnerRank.get(a.id)! : Number.MAX_SAFE_INTEGER
    const bRank = recommendedBurnerRank.has(b.id) ? recommendedBurnerRank.get(b.id)! : Number.MAX_SAFE_INTEGER
    if (aRank !== bRank) return aRank - bRank
    return String(a.name || '').localeCompare(String(b.name || ''))
  })
  const burnerSelectOptions = sortedBurners.map((burner) => {
    const online = burnerOnlineMap[burner.id]
    const runtimeStatus = burnerStatusMap[burner.id] ?? Number(burner.status)
    const isBusy = runtimeStatus === 2
    const isEnabled = burner.is_enabled !== false && burner.is_enabled !== 0
    const nodeDisplayLabel = String(burner.node_display_label || '').trim()
    let statusTag = <Tag style={{ borderRadius: 10, margin: 0 }}>待检测</Tag>
    if (!isEnabled) {
      statusTag = <Tag color="error" style={{ borderRadius: 10, margin: 0 }}>禁用</Tag>
    } else if (isBusy) {
      statusTag = <Tag color="warning" style={{ borderRadius: 10, margin: 0 }}>占用</Tag>
    } else if (online === true) {
      statusTag = <Tag color="success" style={{ borderRadius: 10, margin: 0 }}>空闲</Tag>
    } else if (online === false) {
      statusTag = <Tag color="default" style={{ borderRadius: 10, margin: 0 }}>离线</Tag>
    }
    return {
      label: burner.name,
      value: burner.id,
      disabled: !isEnabled || isBusy || online !== true,
      recommended: recommendedBurnerRank.has(burner.id),
      dropdownMeta: [burner.type || '未知型号', nodeDisplayLabel || '本地'].filter(Boolean).join(' · '),
      statusTag,
    }
  })
  const visibleBoardScripts = getCompatibleBoardScripts({
    scripts,
    platform,
    selectedBurner,
    selectedBoard,
  })
  const systemBoardScripts = visibleBoardScripts.filter((script: any) => Number(script.is_system || 0) === 1)
  const scriptSelectParameterDescriptors = buildScriptSelectParameterDescriptors({
    defaultConfig: selectedScriptDefaultConfig,
    currentValues: wizardData,
    enabled: Boolean(selectedScript?.id),
  }).filter((item) => item.field !== 'qspiFlashModel' || !isAl321Script || isAl321FlashOperation)
  const xds510plusFieldOrder = ['interfaceType', 'eraseMode', 'targetConfigFile', 'completionAction']
  const scriptParameterDescriptors = [
    ...scriptSelectParameterDescriptors.map((item) => ({ ...item, control: 'select' as const })),
    ...scriptInputParameterDescriptors.map((item) => ({ ...item, control: 'input' as const })),
  ].sort((left, right) => {
    if (!isXds510plusScript) return 0
    return xds510plusFieldOrder.indexOf(left.field) - xds510plusFieldOrder.indexOf(right.field)
  })

  useEffect(() => {
    if ((platform !== 'board' && platform !== 'hybrid') || !selectedScript?.id || !selectedScriptDefaultConfig) return
    const resolvedIde = firstFilledText(selectedScriptDefaultConfig?.ide_name, selectedScript?.associated_ide, selectedScript?.ide_name)
    const activeConfigFields = new Set(supportedScriptConfigFields)
    const rememberedDraft = readScriptInputDraft(selectedScript.id)
    const selectDescriptorMap = new Map(
      buildScriptSelectParameterDescriptors({
        defaultConfig: selectedScriptDefaultConfig,
        currentValues: {},
        enabled: true,
      }).map((item) => [item.field, item.options.map((option) => String(option.value))]),
    )
    const resolveSelectFieldValue = (fieldName: string, configuredValue: any, previousValue: any) => {
      const optionValues = selectDescriptorMap.get(fieldName) || []
      const normalizedRememberedValue = String(rememberedDraft?.[fieldName] ?? '').trim()
      if (normalizedRememberedValue && optionValues.includes(normalizedRememberedValue)) {
        return normalizedRememberedValue
      }
      const normalizedConfiguredValue = String(configuredValue ?? '').trim()
      if (normalizedConfiguredValue && optionValues.includes(normalizedConfiguredValue)) {
        return normalizedConfiguredValue
      }
      const normalizedPreviousValue = String(previousValue ?? '').trim()
      if (normalizedPreviousValue && optionValues.includes(normalizedPreviousValue)) {
        return normalizedPreviousValue
      }
      return optionValues[0] || ''
    }
    const resolveTextFieldValue = (fieldName: string, configuredValue: any) => {
      const rememberedValue = String(rememberedDraft?.[fieldName] ?? '').trim()
      if (rememberedValue) {
        return rememberedValue
      }
      const normalizedConfiguredValue = String(configuredValue ?? '').trim()
      if (normalizedConfiguredValue) {
        return String(configuredValue)
      }
      return ''
    }
    const resolveHybridTextValue = (configKey: string, previousValue: any) => {
      const configuredValue = selectedScriptDefaultConfig?.[configKey]
      const normalizedConfiguredValue = String(configuredValue ?? '').trim()
      if (normalizedConfiguredValue) {
        return String(configuredValue)
      }
      return previousValue
    }
    const resolveHybridBooleanValue = (configKey: string, previousValue: any) => {
      if (!Object.prototype.hasOwnProperty.call(selectedScriptDefaultConfig, configKey)) {
        return previousValue
      }
      return Boolean(selectedScriptDefaultConfig?.[configKey])
    }
    setWizardData((prev: any) => ({
      ...prev,
      ide: resolvedIde || prev.ide,
      ...(platform === 'hybrid'
        ? {
            burnMode: resolveHybridTextValue('burn_mode', prev.burnMode),
            transferProtocol: resolveHybridTextValue('transfer_protocol', prev.transferProtocol),
            serverPort: resolveHybridTextValue('server_port', prev.serverPort),
            baudRate: resolveHybridTextValue('baud_rate', prev.baudRate),
            serialLoginUser: resolveHybridTextValue('serial_login_user', prev.serialLoginUser),
            serialPasswordless: resolveHybridBooleanValue('serial_passwordless', prev.serialPasswordless),
            ftpLoginUser: resolveHybridTextValue('ftp_login_user', prev.ftpLoginUser),
            ftpLoginPassword: resolveHybridTextValue('ftp_login_password', prev.ftpLoginPassword),
            ftpPasswordless: resolveHybridBooleanValue('ftp_passwordless', prev.ftpPasswordless),
            boardTargetAddress: resolveHybridTextValue('configured_board_address', resolveHybridTextValue('board_target_address', prev.boardTargetAddress)),
            localIp: resolveHybridTextValue('local_ip', prev.localIp),
            targetPath: resolveHybridTextValue('target_path', prev.targetPath),
          }
        : {}),
      interfaceType: activeConfigFields.has('interfaceType')
        ? resolveSelectFieldValue('interfaceType', selectedScriptDefaultConfig.interface_type, prev.interfaceType)
        : '',
      eraseMode: activeConfigFields.has('eraseMode')
        ? resolveSelectFieldValue('eraseMode', selectedScriptDefaultConfig.erase_mode, prev.eraseMode)
        : '',
      writeSpeed:
        activeConfigFields.has('writeSpeed')
          ? resolveSelectFieldValue('writeSpeed', selectedScriptDefaultConfig.write_speed_khz, prev.writeSpeed)
          : '',
      startAddress:
        activeConfigFields.has('startAddress')
          ? resolveTextFieldValue('startAddress', selectedScriptDefaultConfig.start_address)
          : '',
      qspiFlashModel:
        activeConfigFields.has('qspiFlashModel')
          ? resolveSelectFieldValue('qspiFlashModel', selectedScriptDefaultConfig.qspi_flash_model, prev.qspiFlashModel)
          : '',
      loaderType:
        activeConfigFields.has('loaderType')
          ? resolveSelectFieldValue('loaderType', selectedScriptDefaultConfig.loader_type, prev.loaderType)
          : '',
      targetConfigFile:
        activeConfigFields.has('targetConfigFile')
          ? resolveTextFieldValue('targetConfigFile', selectedScriptDefaultConfig.target_config_file)
          : '',
      gelInitScript:
        activeConfigFields.has('gelInitScript')
          ? resolveTextFieldValue('gelInitScript', selectedScriptDefaultConfig.gel_init_script)
          : '',
      jtagChainIndex:
        activeConfigFields.has('jtagChainIndex')
          ? resolveSelectFieldValue('jtagChainIndex', selectedScriptDefaultConfig.jtag_chain_index, prev.jtagChainIndex)
          : '',
      programVoltage:
        activeConfigFields.has('programVoltage')
          ? resolveSelectFieldValue('programVoltage', selectedScriptDefaultConfig.program_voltage, prev.programVoltage)
          : '',
      eepromWrite:
        activeConfigFields.has('eepromWrite')
          ? resolveSelectFieldValue('eepromWrite', selectedScriptDefaultConfig.eeprom_write, prev.eepromWrite)
          : '',
      writeConfigBits:
        activeConfigFields.has('writeConfigBits')
          ? resolveSelectFieldValue('writeConfigBits', selectedScriptDefaultConfig.write_config_bits, prev.writeConfigBits)
          : '',
      executionOperation:
        activeConfigFields.has('executionOperation')
          ? resolveSelectFieldValue('executionOperation', selectedScriptDefaultConfig.execution_operation, prev.executionOperation)
          : '',
      bichinaBurnMode:
        activeConfigFields.has('bichinaBurnMode')
          ? resolveSelectFieldValue('bichinaBurnMode', selectedScriptDefaultConfig.bichina_burn_mode, prev.bichinaBurnMode)
          : '',
      preErase:
        activeConfigFields.has('preErase')
          ? resolveSelectFieldValue('preErase', selectedScriptDefaultConfig.pre_erase, prev.preErase)
          : '',
      blankCheck:
        activeConfigFields.has('blankCheck')
          ? resolveSelectFieldValue('blankCheck', selectedScriptDefaultConfig.blank_check, prev.blankCheck)
          : '',
      executeProgram:
        activeConfigFields.has('executeProgram')
          ? resolveSelectFieldValue('executeProgram', selectedScriptDefaultConfig.execute_program, prev.executeProgram)
          : '',
      tckFrequency:
        activeConfigFields.has('tckFrequency')
          ? resolveSelectFieldValue('tckFrequency', selectedScriptDefaultConfig.tck_frequency, prev.tckFrequency)
          : '',
      cableIndex:
        activeConfigFields.has('cableIndex')
          ? resolveSelectFieldValue('cableIndex', selectedScriptDefaultConfig.cable_index, prev.cableIndex)
          : '',
      sdTargetPath:
        activeConfigFields.has('sdTargetPath')
          ? resolveTextFieldValue('sdTargetPath', selectedScriptDefaultConfig.sd_target_path)
          : '',
      formatSdCard:
        activeConfigFields.has('formatSdCard')
          ? resolveSelectFieldValue('formatSdCard', selectedScriptDefaultConfig.format_sd_card, prev.formatSdCard)
          : '',
      completionAction: activeConfigFields.has('completionAction')
        ? resolveSelectFieldValue('completionAction', selectedScriptDefaultConfig.completion_action, prev.completionAction)
        : '',
      options: Array.isArray(selectedScriptDefaultConfig.options)
        ? Array.from(
            new Set([
              ...((Array.isArray(prev.options) ? prev.options : []).map((item: any) => String(item))),
              ...selectedScriptDefaultConfig.options.map((item: any) => String(item)),
            ]),
          )
        : prev.options,
      retryCount:
        selectedScriptDefaultConfig.retry_count !== undefined && selectedScriptDefaultConfig.retry_count !== null
          ? Number(selectedScriptDefaultConfig.retry_count)
          : prev.retryCount,
      timeoutMinutes:
        selectedScriptDefaultConfig.timeout_seconds !== undefined && selectedScriptDefaultConfig.timeout_seconds !== null
          ? Number(selectedScriptDefaultConfig.timeout_seconds)
          : selectedScriptDefaultConfig.timeout_minutes !== undefined && selectedScriptDefaultConfig.timeout_minutes !== null
          ? Number(selectedScriptDefaultConfig.timeout_minutes)
          : prev.timeoutMinutes,
    }))
  }, [platform, selectedScript?.id, selectedScript?.default_config_json, supportedScriptConfigFieldsKey])

  useEffect(() => {
    if ((platform !== 'board' && platform !== 'hybrid') || selectedScript?.id) return
    setWizardData((prev: any) => (
      Number(prev.timeoutMinutes) === 120 ? prev : { ...prev, timeoutMinutes: 120 }
    ))
  }, [platform, selectedScript?.id])

  useEffect(() => {
    if ((platform !== 'board' && platform !== 'hybrid') || !selectedScript?.id || isSelectedScriptSystem) return
    setWizardData((prev: any) => ({
      ...prev,
      interfaceType: '',
      eraseMode: '',
      writeSpeed: '',
      startAddress: '',
      qspiFlashModel: '',
      loaderType: '',
      targetConfigFile: '',
      gelInitScript: '',
      jtagChainIndex: '',
      programVoltage: '',
      eepromWrite: '',
      writeConfigBits: '',
      executionOperation: '',
      bichinaBurnMode: '',
      preErase: '',
      blankCheck: '',
      executeProgram: '',
      tckFrequency: '',
      cableIndex: '',
      sdTargetPath: '',
      formatSdCard: '',
      completionAction: '',
    }))
  }, [platform, selectedScript?.id, isSelectedScriptSystem])

  useEffect(() => {
    const scriptId = Number(wizardData.scriptId || 0)
    if (!scriptId) return
    if (scriptDetailsMap[scriptId]?.default_config_json) return
    let cancelled = false
    ;(async () => {
      try {
        const res: any = await scriptApi.getById(scriptId)
        const nextScript = res?.data
        if (!cancelled && nextScript?.id) {
          setScriptDetailsMap((prev) => ({ ...prev, [nextScript.id]: nextScript }))
          setScripts((prev) => prev.map((item) => (item.id === nextScript.id ? { ...item, ...nextScript } : item)))
        }
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [wizardData.scriptId, scriptDetailsMap])

  const recommendedBurnerIds = recommendedBurners
    .map((item) => Number(item.id))
    .sort((left, right) => left - right)
  const visibleBoardScriptIds = visibleBoardScripts.map((item) => item.id).join(',')
  const recommendedBurnerIdsKey = recommendedBurnerIds.join(',')
  const recommendedBurnerStateKey = recommendedBurners.map((item) => `${item.id}:${String(burnerOnlineMap[item.id])}:${String(burnerStatusMap[item.id] ?? item.status)}`).join(',')
  const recommendedBoardScriptIdsKey = burnerBoundScripts.map((item: any) => item.id).join(',')

  useEffect(() => {
    if (platform !== 'board' || !wizardData.boardId) return
    const isBurnerAvailable = (item: any) =>
      item.is_enabled !== false &&
      item.is_enabled !== 0 &&
      burnerOnlineMap[item.id] === true &&
      burnerStatusMap[item.id] !== 2 &&
      Number(item.status) !== 2
    const preferredBurner =
      recommendedBurners.find(isBurnerAvailable) ||
      recommendedBurners.find((item) => item.is_enabled !== false && item.is_enabled !== 0) ||
      recommendedBurners[0]
    setWizardData((prev: any) => {
      const currentRecommended = recommendedBurners.find((item) => item.id === prev.burnerId)
      const currentRecommendedUsable =
        currentRecommended &&
        isBurnerAvailable(currentRecommended)
      const nextBurnerId = currentRecommendedUsable ? prev.burnerId : preferredBurner?.id
      if (nextBurnerId === prev.burnerId) {
        return prev
      }
      return {
        ...prev,
        burnerId: nextBurnerId,
        scriptId: undefined,
      }
    })
  }, [platform, wizardData.boardId, recommendedBurnerIdsKey, recommendedBurnerStateKey, recommendedBoardScriptIdsKey, selectedBoardName])

  useEffect(() => {
    if (platform !== 'board' && platform !== 'hybrid') return
    if (visibleBoardScripts.length > 0) {
      const stillMatched = wizardData.scriptId && visibleBoardScripts.some((item) => item.id === wizardData.scriptId)
      if (!stillMatched) {
        const preferredScript =
          platform === 'hybrid'
            ? visibleBoardScripts.find((item) => item.name === SYLIXOS_HYBRID_SCRIPT_NAME) || visibleBoardScripts[0]
            : visibleBoardScripts[0]
        updateWizardField('scriptId', preferredScript.id)
      }
    } else {
      if (wizardData.scriptId) {
        updateWizardField('scriptId', undefined)
      }
    }
  }, [platform, wizardData.scriptId, wizardData.burnerId, wizardData.boardId, visibleBoardScriptIds])

  useEffect(() => {
    if (!isWizardOpen || platform !== 'board' || currentStep !== 2) return
    scanBurnerOnlineStatus(visibleBurners)
  }, [isWizardOpen, platform, currentStep, recommendedBurnerIdsKey])

  const handleWizardFinish = async () => {
    if (wizardSubmitLoading) return
    try {
      if (!selectedRepository) {
        message.warning('请选择可执行文件')
        return
      }
      if (!hasRepositoryVersion(selectedRepository)) {
        message.warning('当前制品仓库记录未维护版本号，请先补齐版本后再创建任务')
        return
      }
      if (platform === 'board' && !validateBoardConfig()) {
        return
      }
      if (platform === 'os' && !validateOsConfig()) {
        return
      }
      if (platform === 'hybrid' && !validateHybridConfig()) {
        return
      }
      const activeBoardConfigKeys = new Set(
        platform === 'board' || platform === 'hybrid'
          ? supportedScriptConfigFields
              .map((fieldName) => scriptFieldConfigKeyMap[fieldName as keyof typeof scriptFieldConfigKeyMap])
              .filter(Boolean)
          : [],
      )
      const includeBoardConfigKey = (configKey: string) =>
        platform === 'board' || platform === 'hybrid' ? activeBoardConfigKeys.has(configKey) : false
      setWizardSubmitLoading(true)
      const configPayload = {
        task_type: platform,
        platform,
        install_source: effectiveInstallSource,
        retries: Number(wizardData.retryCount || 0),
        keep_local: effectiveWizardOptions.includes('local'),
        integrity: effectiveWizardOptions.includes('integrity'),
        version_check: effectiveWizardOptions.includes('version'),
        write_verify: effectiveWizardOptions.includes('writeVerify'),
        expected_checksum: getRepositoryChecksum(selectedRepository) || undefined,
        history_checksum: effectiveWizardOptions.includes('version') ? versionBaselineChecksum || undefined : undefined,
        script_id: platform === 'board' || platform === 'hybrid' ? wizardData.scriptId : undefined,
        os_type: platform === 'os' ? osTypeMap[wizardData.osId] : undefined,
        connection_protocol: platform === 'os' ? wizardData.connectionProtocol : undefined,
        deployment_mode: platform === 'os' && isSylixOs ? 'FTP' : undefined,
        harmony_device_id: platform === 'os' && isHarmonyOs ? String(wizardData.harmonyDeviceId || '').trim() : undefined,
        ftp_port: platform === 'os' && isSylixOs ? Number(wizardData.ftpPort || 21) || 21 : undefined,
        boot_autostart: platform === 'os' && isSylixOs ? Boolean(wizardData.bootAutostart) : undefined,
        auth_type: platform === 'os' ? wizardData.authType : undefined,
        login_username: platform === 'os' ? String(wizardData.loginUsername || '').trim() || undefined : undefined,
        login_passwordless: platform === 'os' && isSylixOs ? Boolean(wizardData.loginPasswordless) : undefined,
        login_password: platform === 'os' && (wizardData.authType === 'password' || isSylixOs) && !wizardData.loginPasswordless ? String(wizardData.loginPassword || '') : undefined,
        private_key_path: platform === 'os' && !isSylixOs && wizardData.authType === 'key' ? String(wizardData.privateKeyPath || '').trim() || undefined : undefined,
        install_dir: platform === 'os' ? String(wizardData.installDir || '').trim() : undefined,
        ide_name: platform === 'board' ? String(wizardData.ide || '').trim() || undefined : undefined,
        burner_name: platform === 'board' ? String(selectedBurner?.name || '').trim() || undefined : undefined,
        burner_type: platform === 'board' ? String(selectedBurner?.type || '').trim() || undefined : undefined,
        interface_type: includeBoardConfigKey('interface_type') ? String(wizardData.interfaceType || '').trim() || undefined : undefined,
        erase_mode: includeBoardConfigKey('erase_mode') ? String(wizardData.eraseMode || '').trim() || undefined : undefined,
        write_speed_khz: includeBoardConfigKey('write_speed_khz') ? Number(wizardData.writeSpeed || 0) || undefined : undefined,
        start_address: includeBoardConfigKey('start_address') ? String(wizardData.startAddress || '').trim() || undefined : undefined,
        qspi_flash_model: includeBoardConfigKey('qspi_flash_model') ? String(wizardData.qspiFlashModel || '').trim() || undefined : undefined,
        loader_type: includeBoardConfigKey('loader_type') ? String(wizardData.loaderType || '').trim() || undefined : undefined,
        target_config_file: includeBoardConfigKey('target_config_file') ? String(wizardData.targetConfigFile || '').trim() || undefined : undefined,
        gel_init_script: includeBoardConfigKey('gel_init_script') ? String(wizardData.gelInitScript || '').trim() || undefined : undefined,
        jtag_chain_index: includeBoardConfigKey('jtag_chain_index') ? Number(wizardData.jtagChainIndex || 0) : undefined,
        program_voltage: includeBoardConfigKey('program_voltage') ? String(wizardData.programVoltage || '').trim() || undefined : undefined,
        eeprom_write: includeBoardConfigKey('eeprom_write') ? String(wizardData.eepromWrite || '').trim() || undefined : undefined,
        write_config_bits: includeBoardConfigKey('write_config_bits') ? String(wizardData.writeConfigBits || '').trim() || undefined : undefined,
        execution_operation: includeBoardConfigKey('execution_operation') ? String(wizardData.executionOperation || '').trim() || undefined : undefined,
        bichina_burn_mode: includeBoardConfigKey('bichina_burn_mode') ? String(wizardData.bichinaBurnMode || '').trim() || undefined : undefined,
        pre_erase: includeBoardConfigKey('pre_erase') ? String(wizardData.preErase || '').trim() || undefined : undefined,
        blank_check: includeBoardConfigKey('blank_check') ? String(wizardData.blankCheck || '').trim() || undefined : undefined,
        execute_program: includeBoardConfigKey('execute_program') ? String(wizardData.executeProgram || '').trim() || undefined : undefined,
        tck_frequency: includeBoardConfigKey('tck_frequency') ? String(wizardData.tckFrequency || '').trim() || undefined : undefined,
        cable_index: includeBoardConfigKey('cable_index') ? Number(wizardData.cableIndex || 0) : undefined,
        sd_target_path: includeBoardConfigKey('sd_target_path') ? String(wizardData.sdTargetPath || '').trim() || undefined : undefined,
        format_sd_card: includeBoardConfigKey('format_sd_card') ? String(wizardData.formatSdCard || '').trim() || undefined : undefined,
        completion_action: includeBoardConfigKey('completion_action') ? String(wizardData.completionAction || '').trim() || undefined : undefined,
        burn_mode: platform === 'hybrid' ? String(wizardData.burnMode || '').trim() || undefined : undefined,
        transfer_protocol: platform === 'hybrid' ? String(wizardData.burnMode || '').trim() || undefined : undefined,
        serial_port: platform === 'hybrid' ? String(wizardData.serialPort || '').trim() || undefined : undefined,
        baud_rate: platform === 'hybrid' ? String(wizardData.baudRate || '').trim() || undefined : undefined,
        serial_login_user: platform === 'hybrid' ? String(wizardData.serialLoginUser || '').trim() || undefined : undefined,
        serial_login_password: platform === 'hybrid' && !wizardData.serialPasswordless ? String(wizardData.serialLoginPassword || '') : undefined,
        serial_passwordless: platform === 'hybrid' ? Boolean(wizardData.serialPasswordless) : undefined,
        system_username: platform === 'hybrid' ? String(wizardData.systemUsername || '').trim() || undefined : undefined,
        system_password: platform === 'hybrid' ? String(wizardData.systemPassword || '') || undefined : undefined,
        ftp_login_user: platform === 'hybrid' ? String(wizardData.ftpLoginUser || '').trim() || undefined : undefined,
        ftp_login_password: platform === 'hybrid' && !wizardData.ftpPasswordless ? String(wizardData.ftpLoginPassword || '') : undefined,
        ftp_passwordless: platform === 'hybrid' ? Boolean(wizardData.ftpPasswordless) : undefined,
        server_port: platform === 'hybrid' ? Number(wizardData.serverPort || 69) || 69 : undefined,
        configured_board_address: platform === 'hybrid' ? String(wizardData.boardTargetAddress || '').trim() || undefined : undefined,
        board_target_address: platform === 'hybrid' ? String(wizardData.boardTargetAddress || '').trim() || undefined : undefined,
        local_ip: platform === 'hybrid' ? String(wizardData.localIp || '').trim() || undefined : undefined,
        target_path: platform === 'hybrid' ? String(wizardData.targetPath || '').trim() || undefined : undefined,
        timeout_seconds: Number(wizardData.timeoutMinutes || 120),
        remark: String(wizardData.remark || '').trim() || undefined,
        extra_config: wizardData.config || undefined,
      }
      const createRes: any = await taskApi.create({
        software_name: selectedRepository.name,
        repository_id: selectedRepository.id,
        task_type: platform || undefined,
        board_name:
          platform === 'board' || platform === 'hybrid'
            ? boards.find(b => b.id === wizardData.boardId)?.name
            : osList.find(o => o.id === wizardData.osId)?.name,
        config_json: JSON.stringify(configPayload),
        target_ip: platform === 'os' && !isHarmonyOs ? wizardData.targetIp : platform === 'hybrid' ? String(wizardData.boardTargetAddress || '').trim() || undefined : undefined,
        target_port:
          platform === 'os' && isSylixOs
            ? Number(wizardData.ftpPort || 21)
            : platform === 'os' && wizardData.targetPort
              ? Number(wizardData.targetPort)
              : platform === 'hybrid'
                ? Number(wizardData.serverPort || 69)
              : undefined,
        product_id: platform === 'board' || platform === 'hybrid' ? wizardData.boardId : undefined,
        burner_id: platform === 'board' ? wizardData.burnerId : undefined,
        script_id: platform === 'board' || platform === 'hybrid' ? wizardData.scriptId : undefined,
        keep_local: configPayload.keep_local ? 1 : 0,
        integrity: configPayload.integrity ? 1 : 0,
        expected_checksum: configPayload.expected_checksum,
        version_check: configPayload.version_check ? 1 : 0,
        history_checksum: configPayload.history_checksum,
        // 创建成功后由后端直接推上执行轨道，不会再额外生成新任务
        auto_execute: true,
      })
      const isAutoExecuted = createRes?.data?.auto_executed === true || createRes?.data?.auto_executed === 1
      const createdTaskId = Number(createRes?.data?.id || 0)
      const createdTaskNo = String(createRes?.data?.task_no || createRes?.data?.id || '-')
      // 改用受控轻提示（参考登录成功的轻提示风格），避免大弹窗遮挡列表。
      // 用稳定 key：重复提交时只更新内容，不叠加多个 message。
      const tipText = isAutoExecuted
        ? `任务创建成功（${createdTaskNo}），已自动开始执行`
        : `任务创建成功（${createdTaskNo}），等待手动启动执行`
      message.open({
        key: `task-created-${createdTaskId || 'unknown'}`,
        type: 'success',
        content: tipText,
        duration: 4,
      })
      setIsWizardOpen(false)
      await fetchTasks(true)
    } catch (e: any) {
      const errorText = getBurningRequestErrorMessage(e, '创建失败，请检查上方必填项和设备配置')
      message.error(errorText)
    } finally {
      setWizardSubmitLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    setDeletingTaskId(id)
    try {
      await taskApi.delete(id)
      message.success('删除成功')
      fetchTasks()
    } catch (e: any) {
      message.error(getBurningRequestErrorMessage(e, '删除失败'))
    } finally {
      setDeletingTaskId(null)
    }
  }

  const handleTerminate = async (id: number, reason: string) => {
    setTerminatingTaskId(id)
    try {
      const res: any = await taskApi.terminate(id, { reason })
      message.success(res?.message || '任务终止请求已提交')
      if (detailTask?.id === id) {
        fetchTaskDetail(id, true)
      }
      fetchTasks()
    } catch { /* interceptor handles it */ }
    finally {
      setTerminatingTaskId(null)
    }
  }

  const openTerminateDialog = (task: any) => {
    setTerminateDialogTask(task)
    setTerminateReason('')
  }

  const closeTerminateDialog = () => {
    if (terminatingTaskId) return
    setTerminateDialogTask(null)
    setTerminateReason('')
  }

  const handleCopyDetailLog = async () => {
    const text = String(detailTask?.result || '').trim()
    if (!text) {
      message.warning('暂无可复制日志')
      return
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.style.position = 'fixed'
        textarea.style.left = '-9999px'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      message.success('日志已复制')
    } catch {
      message.error('复制失败')
    }
  }

  const confirmTerminateTask = async () => {
    const targetTaskId = Number(terminateDialogTask?.id || 0)
    const reason = terminateReason.trim()
    if (!targetTaskId) return
    if (!reason) {
      message.warning('请填写终止原因')
      return
    }
    await handleTerminate(targetTaskId, reason)
    setTerminateDialogTask(null)
    setTerminateReason('')
  }

  const handleExecute = async (id: number) => {
    const messageKey = `execute-task-${id}`
    message.open({ key: messageKey, type: 'loading', content: '正在启动烧录安装任务，请稍候...', duration: 0 })
    try {
      const res: any = await taskApi.execute(id)
      const nextTaskId = Number(res?.data?.id || 0)
      message.open({
        key: messageKey,
        type: 'success',
        content: res?.message || '已复制当前任务并启动执行，可打开详情实时查看执行状态和日志',
        duration: 4,
      })
      if (detailTask?.id === id && nextTaskId) {
        setIsDetailOpen(true)
        fetchTaskDetail(nextTaskId, true)
      }
      await fetchTasks(true)
    } catch (e: any) {
      message.open({
        key: messageKey,
        type: 'error',
        content: getBurningRequestErrorMessage(e, '任务启动失败，请检查任务配置、设备连接和执行工具后重试'),
        duration: 5,
      })
    }
  }

  const fetchTaskDetail = async (id: number, silent = false) => {
    if (!silent) setDetailLoading(true)
    try {
      const res: any = await taskApi.getById(id, silent)
      if (res.code === 0) {
        setDetailTask(res.data)
        if (silent && !isTaskActive(res.data)) fetchTasks(true)
      }
    } catch { /* interceptor handles it */ }
    finally {
      if (!silent) setDetailLoading(false)
    }
  }

  const fetchConsistencyTask = async (id: number, silent = false) => {
    if (!silent) setReportLoading(true)
    try {
      const res: any = await taskApi.getById(id, silent)
      if (res.code === 0) setConsistencyTask(res.data)
    } catch { /* interceptor handles it */ }
    finally {
      if (!silent) setReportLoading(false)
    }
  }

  const handleOpenDetail = async (id: number) => {
    setIsDetailOpen(true)
    setDetailTask(null)
    await fetchTaskDetail(id)
  }

  const handleOpenConsistency = async (id: number) => {
    setIsConsistencyOpen(true)
    setConsistencyTask(null)
    await fetchConsistencyTask(id)
  }

  const handleOverride = async (taskId: number) => {
    try {
      await taskApi.override(taskId)
      message.success('已允许强制覆盖')
      await handleOpenConsistency(taskId)
      fetchTasks()
    } catch { /* interceptor handles it */ }
  }

  const handleTaskKeywordSearch = (value?: string) => {
    const keyword = value?.trim()
    setParams((prev) => ({
      ...prev,
      page: 1,
      keyword: keyword || undefined,
    }))
  }

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: 'default', text: '待执行' },
    1: { color: 'processing', text: '执行中' },
    2: { color: 'success', text: '成功' },
    3: { color: 'error', text: '失败' },
    4: { color: 'warning', text: '终止中' },
    5: { color: 'default', text: '已终止' },
  }
  const getTaskStatusMeta = (record?: any) => {
    const status = Number(record?.status)
    return statusMap[status] || { color: 'default', text: '未知' }
  }

  const targetFilterOptions = [
    { label: '所有烧录安装目标', value: 'all' },
    ...filterBoards.map((board) => ({ label: board.name, value: board.name })),
    ...osList.map((os) => ({ label: os.name, value: os.name })),
  ]

  const statusFilterOptions = [
    { label: '所有状态', value: 'all' },
    { label: '待执行', value: '0' },
    { label: '执行中', value: '1' },
    { label: '成功', value: '2' },
    { label: '失败', value: '3' },
    { label: '终止中', value: '4' },
    { label: '已终止', value: '5' },
  ]

  const columns = [
    { title: '序号', key: 'index', width: 60, render: (_: any, __: any, index: number) => index + 1 },
    { title: '任务编号', dataIndex: 'task_no', key: 'task_no', width: 130, render: (value: string) => value || '-' },
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', width: 160, render: (_: string, record: any) => getTaskProjectName(record) },
    { title: '烧录安装目标', key: 'target', width: 220, render: (_: any, record: any) => renderSingleLineTooltipText(getTaskTargetText(record)) },
    {
      title: '软件及版本',
      dataIndex: 'software_name',
      key: 'software_name',
      width: 220,
      render: (_text: string, record: any) => {
        const softwareName = getTaskSoftwareName(record)
        const versionText = getTaskVersionText(record)
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, width: '100%' }}>
            <EllipsisText value={softwareName} style={{ flex: '0 1 auto', color: '#2f3747' }} />
            {versionText ? (
              <Tag color="blue" style={{ borderRadius: 10, marginInlineEnd: 0, flex: '0 0 auto' }}>
                {versionText}
              </Tag>
            ) : (
              <span style={{ color: '#b7bfcc', flex: '0 0 auto' }}>--</span>
            )}
          </div>
        )
      },
    },
    { 
      title: '执行时间', 
      dataIndex: 'created_at', 
      key: 'created_at', 
      sorter: true,
      width: 190,
      render: (t: string) => formatDateTime(t),
    },
    {
      title: '执行人',
      dataIndex: 'executor',
      key: 'executor',
      render: (text: string) => {
        const name = text || '-'
        return <UserIdentity fallbackName={name} avatarSize={23} />
      },
    },
    { title: '状态', dataIndex: 'status', key: 'status', render: (_s: number, record: any) => {
      const statusMeta = getTaskStatusMeta(record)
      return (
        <span
          style={{
            color:
              statusMeta.color === 'success'
                ? '#52c41a'
                : statusMeta.color === 'processing'
                  ? '#1890ff'
                  : statusMeta.color === 'warning'
                    ? '#faad14'
                    : statusMeta.color === 'default'
                      ? '#8c8c8c'
                      : '#f5222d',
          }}
        >
          <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', backgroundColor: 'currentColor', marginRight: 6 }}></span>
          {statusMeta.text}
        </span>
      )
    } },
    {
      title: '版本一致性报告',
      dataIndex: 'consistency_report',
      key: 'consistency_report',
      width: 130,
      render: (_: any, record: any) =>
        hasConsistencyReport(record) ? (
          <Permission code="burning:report">
            <ActionLinkButton onClick={() => handleOpenConsistency(record.id)}>查看报告</ActionLinkButton>
          </Permission>
        ) : (
          <span style={{ color: '#b7bfcc' }}>--</span>
        ),
    },
    { title: '操作', key: 'action', width: 180, fixed: 'right' as const, render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          {Number(record.status) === 1 ? (
            <Permission code="burning:terminate">
              <ActionLinkButton danger onClick={() => openTerminateDialog(record)}>终止</ActionLinkButton>
            </Permission>
          ) : null}
          {Number(record.status) === 4 ? (
            <Permission code="burning:terminate">
              <ActionLinkButton danger disabled>终止中</ActionLinkButton>
            </Permission>
          ) : null}
          {!isTaskActive(record) ? (
            <Permission code="burning:add">
              <ActionLinkButton onClick={() => handleExecute(record.id)}>执行</ActionLinkButton>
            </Permission>
          ) : null}
          <ActionLinkButton onClick={() => handleOpenDetail(record.id)}>详情</ActionLinkButton>
          {!isTaskActive(record) ? (
            <Permission code="burning:delete">
              <ActionConfirm
                title="删除任务"
                description="删除后不可恢复，是否继续？"
                okText="确认删除"
                cancelText="取消"
                confirmLoading={deletingTaskId === record.id}
                onConfirm={() => handleDelete(record.id)}
              >
                <ActionLinkButton danger>删除</ActionLinkButton>
              </ActionConfirm>
            </Permission>
          ) : null}
        </ActionButtonGroup>
      ),
    },
  ]

  const artifactRows = currentProject.projectKey ? repositories
    .filter((item) => {
      if (item?.project_key !== currentProject.projectKey) return false
      const locationInfo = getArtifactLocationInfo(item)
      const matchLocation = artifactLocationFilter === '全部' || locationInfo.filters?.includes(artifactLocationFilter)
      if (!matchLocation) return false
      if (!artifactKeyword) return true
      const keyword = artifactKeyword.toLowerCase()
      return [
        item.name,
        getRepositoryVersionText(item),
        item.repo_id,
        item.project_key,
        item.tenant,
        item.display_path,
        item.file_url,
        item.download_uri,
        locationInfo.value,
        locationInfo.detail,
      ].some((value) => String(value || '').toLowerCase().includes(keyword))
    })
    .map((item) => {
      const locationInfo = getArtifactLocationInfo(item)
      return {
        key: item.id,
        label: item.name,
        value: item.id,
        installSource: locationInfo.installSource,
        version: getRepositoryVersionText(item) || '-',
        rawVersion: getRepositoryVersionText(item) || '',
        md5: firstFilledText(item.md5, item.file_detail?.md5, item.file_detail?.checksums?.md5) || '',
        sha256: firstFilledText(item.sha256, item.file_detail?.sha256, item.file_detail?.checksums?.sha256) || '',
        selectable: hasRepositoryVersion(item),
        responsible: item.tenant || item.repo_id || '-',
        publishTime: formatDateTime(item.updated_at),
        path: getArtifactCurrentPath(item),
        location: locationInfo.value,
        locationColor: locationInfo.color,
        locationDetail: locationInfo.detail,
      }
    }) : []

  const selectedInstallSourceLabel =
    (effectiveInstallSource === 'server'
      ? '服务器'
      : effectiveInstallSource === 'codearts'
        ? 'CodeArts'
        : '本地')

  useEffect(() => {
    artifactPageUserControlledRef.current = false
    setArtifactPage(1)
  }, [artifactKeyword, artifactLocationFilter, currentProject.projectKey])

  useEffect(() => {
    const lastPage = Math.max(1, Math.ceil(artifactRows.length / artifactPageSize))
    if (artifactPage > lastPage) setArtifactPage(lastPage)
  }, [artifactRows.length, artifactPage, artifactPageSize])

  useEffect(() => {
    if (!isWizardOpen || currentStep !== 0 || artifactRows.length === 0) return
    const selectedIndex = artifactRows.findIndex((item) => Number(item.value) === Number(wizardData.software))
    const currentRecord = selectedIndex >= 0 ? artifactRows[selectedIndex] : null
    if (currentRecord?.installSource && currentRecord.installSource !== wizardData.installSource) {
      setWizardData((prev: any) => ({
        ...prev,
        installSource: currentRecord.installSource,
      }))
    }
    if (!artifactPageUserControlledRef.current && selectedIndex >= 0) {
      setArtifactPage(Math.floor(selectedIndex / artifactPageSize) + 1)
    }
  }, [isWizardOpen, currentStep, wizardData.software, wizardData.installSource, artifactRows.length, artifactPageSize])

  const consistencyConclusion = consistencyTask?.consistency_passed === 1
    ? { color: 'success', text: '通过' }
    : consistencyTask?.consistency_passed === 0
      ? { color: 'error', text: '不通过' }
      : { color: 'default', text: '未比对' }
  const fileColumns = [
    {
      title: '软件名称',
      dataIndex: 'label',
      width: 220,
      render: (value: string) => <EllipsisText value={value} />,
    },
    {
      title: '版本',
      dataIndex: 'version',
      width: 100,
      render: (v: string, record: any) =>
        record.selectable ? (
          <Tag color="blue" style={{ borderRadius: 10 }}>{v}</Tag>
        ) : (
          <span style={{ color: '#b7bfcc' }}>未维护版本</span>
        ),
    },
    {
      title: '项目责任人',
      dataIndex: 'responsible',
      width: 180,
      render: (text: string) => {
        const name = text || '-'
        return (
          <div style={{ width: '100%', minWidth: 0, overflow: 'hidden' }}>
            <UserIdentity fallbackName={name} avatarSize={23} />
          </div>
        )
      },
    },
    {
      title: '发布时间',
      dataIndex: 'publishTime',
      width: 190,
      render: (value: string) => <EllipsisText value={value} />,
    },
    {
      title: '路径',
      dataIndex: 'path',
      width: 260,
      render: (value: string) => (
        <div style={{ maxWidth: 260 }}>
          <EllipsisText value={value} />
        </div>
      ),
    },
    {
      title: '文件位置',
      dataIndex: 'location',
      width: 110,
      render: (value: string, record: any) => (
        <span title={record.locationDetail}>
          <Tag color={record.locationColor} style={{ borderRadius: 10 }}>{value}</Tag>
        </span>
      ),
    },
  ]

  const openBoardImagePreview = (imageUrl?: string) => {
    const nextUrl = resolveMediaUrl(imageUrl)
    if (!nextUrl) return
    setPreviewBoardImage(nextUrl)
  }

  const openBoardDetail = (record: any) => {
    setBoardDetailRecord(record)
    setIsBoardDetailOpen(true)
  }

  const boardColumns = [
    { title: '板卡序列号', dataIndex: 'serial_number' },
    {
      title: '板卡名称',
      dataIndex: 'name',
      render: (name: string, record: any) => (
        <ActionLinkButton
          onClick={(event) => {
            event.stopPropagation()
            openBoardDetail(record)
          }}
        >
          {name || '-'}
        </ActionLinkButton>
      ),
    },
    { title: '芯片类型', dataIndex: 'chip_type', render: (t: string) => <Tag color={getChipTagColor(t)} style={{ borderRadius: 10 }}>{t}</Tag> },
    { title: '板卡图片', dataIndex: 'board_image', render: (img: string) => (
      <Space>
        {img ? (
          <img
            src={img}
            alt="board"
            onClick={(event) => {
              event.stopPropagation()
              openBoardImagePreview(img)
            }}
            style={{ width: 40, height: 30, objectFit: 'cover', borderRadius: 2, cursor: 'pointer', border: '1px solid #f0f0f0' }}
          />
        ) : (
          <span style={{ color: '#999' }}>-</span>
        )}
      </Space>
    ) },
  ]

  const [boardFilter, setBoardFilter] = useState({ type: '全部', keyword: '' })

  const filteredBoards = boards.filter(b => {
    const matchType = boardFilter.type === '全部' || b.chip_type === boardFilter.type
    const matchKeyword = !boardFilter.keyword || b.name?.includes(boardFilter.keyword)
    return matchType && matchKeyword
  })

  useEffect(() => {
    if (currentStep !== 1 || (platform !== 'board' && platform !== 'hybrid')) return
    const selectedIndex = filteredBoards.findIndex((board) => Number(board.id) === Number(wizardData.boardId))
    const nextPage = selectedIndex >= 0 ? Math.floor(selectedIndex / wizardBoardPageSize) + 1 : 1
    setWizardBoardPage((currentPage) => currentPage === nextPage ? currentPage : nextPage)
  }, [currentStep, platform, wizardData.boardId, wizardBoardPageSize, boards, boardFilter.type, boardFilter.keyword])

  const sectionTitleStyle: React.CSSProperties = { marginBottom: 16, fontSize: 15, fontWeight: 600, color: '#1d2129' }
  const fieldLabelStyle: React.CSSProperties = { marginBottom: 8, fontSize: 14, fontWeight: 500, color: '#1d2129' }
  const helperTextStyle: React.CSSProperties = { marginTop: 6, fontSize: 12, color: '#86909c' }

  // Wizard UI
  if (isWizardOpen) {
    const totalSteps = platform === 'os' ? 2 : 3
    const counterShellStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', border: '1px solid #d9d9d9', borderRadius: 6, overflow: 'hidden', background: '#fff' }
    const counterButtonStyle: React.CSSProperties = { width: 36, height: 32, border: 0, borderRadius: 0, color: '#666' }
    const counterValueWrapStyle: React.CSSProperties = { width: 82, height: 32, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderLeft: '1px solid #f0f0f0', borderRight: '1px solid #f0f0f0' }
    const counterValueInputStyle: React.CSSProperties = { width: 70, height: 30, fontSize: 14, color: '#1d2129', textAlign: 'center' }
    const requiredLabel = (text: string) => (
      <span>
        <span style={{ color: '#f53f3f', marginRight: 2 }}>*</span>
        {text}
      </span>
    )
    const renderCounterField = (key: 'retryCount' | 'timeoutMinutes', min: number, max: number) => (
      <div style={counterShellStyle}>
        <Button style={counterButtonStyle} icon={<MinusOutlined />} onClick={() => adjustWizardNumber(key, -1, min, max)} />
        <div style={counterValueWrapStyle}>
          <InputNumber
            className="wizard-counter-input"
            controls={false}
            min={min}
            max={max}
            precision={0}
            value={Number(wizardData[key] ?? min)}
            onChange={(value) => updateWizardNumber(key, value, min, max)}
            style={counterValueInputStyle}
          />
        </div>
        <Button style={counterButtonStyle} icon={<PlusOutlined />} onClick={() => adjustWizardNumber(key, 1, min, max)} />
      </div>
    )
    return (
      <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingBottom: 16, borderBottom: '1px solid #f0f0f0', marginBottom: 24 }}>
          <div className="client-page-title">
            <Title level={4}>烧录安装管理</Title>
            <p className="client-page-subtitle">按向导创建烧录、安装与一致性验证任务</p>
          </div>
          <ActionLinkButton className="ui-action-link-muted" onClick={() => setIsWizardOpen(false)}>&lt;返回</ActionLinkButton>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 24 }}>
          <span style={{ color: '#4045D6', fontWeight: 'bold' }}>任务向导</span>
          <span style={{ color: '#999' }}>步骤 {currentStep + 1}/{totalSteps}</span>
        </div>
        {currentStep === 0 && (
          <div>
            <div style={{ marginBottom: 32 }}>
              <div style={{ marginBottom: 16, fontWeight: 'bold' }}>任务场景</div>
              <Space size="large">
                <div 
                  onClick={() => handlePlatformChange('board')}
                  style={{ width: 140, height: 80, border: `1px solid ${platform === 'board' ? '#4045D6' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: platform === 'board' ? '#F0F5FF' : '#fff' }}
                >
                  <AppstoreOutlined style={{ fontSize: 28, color: platform === 'board' ? '#4045D6' : '#1d2129', marginBottom: 8 }} />
                  <span style={{ color: platform === 'board' ? '#4045D6' : '#1d2129' }}>板卡烧录</span>
                </div>
                <div 
                  onClick={() => handlePlatformChange('os')}
                  style={{ width: 140, height: 80, border: `1px solid ${platform === 'os' ? '#4045D6' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: platform === 'os' ? '#F0F5FF' : '#fff' }}
                >
                  <DesktopOutlined style={{ fontSize: 28, color: platform === 'os' ? '#4045D6' : '#1d2129', marginBottom: 8 }} />
                  <span style={{ color: platform === 'os' ? '#4045D6' : '#1d2129' }}>操作系统应用安装</span>
                </div>
                <div
                  onClick={() => handlePlatformChange('hybrid')}
                  style={{ width: 140, height: 80, border: `1px solid ${platform === 'hybrid' ? '#4045D6' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: platform === 'hybrid' ? '#F0F5FF' : '#fff' }}
                >
                  <LinkOutlined style={{ fontSize: 28, color: platform === 'hybrid' ? '#4045D6' : '#1d2129', marginBottom: 8 }} />
                  <span style={{ color: platform === 'hybrid' ? '#4045D6' : '#1d2129' }}>混合协同</span>
                </div>
              </Space>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <div style={{ fontWeight: 'bold' }}>选择可执行文件</div>
                <Space>
                  <span style={{ color: '#ff7d00', fontSize: 12 }}>仅可选择已维护版本号的制品</span>
                  <span style={{ color: '#666' }}>文件位置</span>
                  <Select
                    value={artifactLocationFilter}
                    onChange={(value) => setArtifactLocationFilter(value)}
                    style={{ width: 120 }}
                    options={[
                      { label: '全部', value: '全部' },
                      { label: 'CodeArts', value: 'CodeArts' },
                      { label: '本地', value: '本地' },
                      { label: '服务器', value: '服务器' },
                    ]}
                  />
                  <Input
                    prefix={<SearchOutlined />}
                    placeholder="请输入可执行文件名称"
                    style={{ width: 240 }}
                    value={artifactKeyword}
                    onChange={(e) => setArtifactKeyword(e.target.value)}
                  />
                </Space>
              </div>
              <Table 
                columns={fileColumns} 
                dataSource={artifactRows} 
                tableLayout="fixed"
                scroll={{ x: 1020 }}
                pagination={{
                  current: artifactPage,
                  pageSize: artifactPageSize,
                  total: artifactRows.length,
                  showSizeChanger: false,
                  onChange: (page, pageSize) => {
                    artifactPageUserControlledRef.current = true
                    if (pageSize && pageSize !== artifactPageSize) {
                      setArtifactPageSize(pageSize)
                    }
                    setArtifactPage(page)
                  },
                  showTotal: (t) =>
                    renderListPaginationTotal(t, artifactPageSize, (size) => {
                      artifactPageUserControlledRef.current = true
                      setArtifactPage(1)
                      setArtifactPageSize(size)
                    }, {
                      pageSizeOptions: [5, 10, 20],
                    }),
                }}
                rowKey="value" 
                size="small" 
                locale={{ emptyText: '暂无可执行文件，请先同步 CodeArts 或下载到本地/服务器' }}
                rowSelection={{
                  type: 'radio',
                  selectedRowKeys: wizardData.software ? [wizardData.software] : [],
                  getCheckboxProps: (record: any) => ({
                    disabled: !record.selectable,
                  }),
                  onChange: (selectedRowKeys) => {
                    const selectedRow = artifactRows.find((item) => item.value === selectedRowKeys[0])
                    if (selectedRow && !selectedRow.selectable) {
                      message.warning('该制品仓库记录未维护版本号，暂不能用于创建烧录任务')
                      return
                    }
                    artifactSelectionUserTouchedRef.current = true
                    setWizardData((prev: any) => ({
                      ...prev,
                      software: selectedRowKeys[0],
                      installSource: selectedRow?.installSource || prev.installSource || 'local',
                    }))
                  },
                }}
                onRow={(record) => ({
                  onClick: () => {
                    if (!record.selectable) {
                      message.warning('该制品仓库记录未维护版本号，暂不能用于创建烧录任务')
                      return
                    }
                    artifactSelectionUserTouchedRef.current = true
                    setWizardData((prev: any) => ({
                      ...prev,
                      software: record.value,
                      installSource: record.installSource || prev.installSource || 'local',
                    }))
                  }
                })}
              />
            </div>
            <div style={{ textAlign: 'right', marginTop: 24 }}>
              <PagePrimaryButton onClick={handleNext}>下一步 &gt;</PagePrimaryButton>
            </div>
          </div>
        )}

        {currentStep === 1 && (platform === 'board' || platform === 'hybrid') && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <div style={{ fontWeight: 'bold' }}>选择板卡</div>
              <Space>
                <Select value={boardFilter.type} onChange={v => setBoardFilter({ ...boardFilter, type: v })} style={{ width: 152 }}>
                  <Select.Option value="全部">全部芯片类型</Select.Option>
                  {CHIP_TYPE_OPTIONS.map((item) => (
                    <Select.Option key={item} value={item}>{item}</Select.Option>
                  ))}
                </Select>
                <Input className="pcids-list-search" placeholder="请输入板卡名称" prefix={<SearchOutlined />} value={boardFilter.keyword} allowClear onChange={e => setBoardFilter({ ...boardFilter, keyword: e.target.value })} />
              </Space>
            </div>
            <Table
              columns={boardColumns}
              dataSource={filteredBoards}
              pagination={{
                total: filteredBoards.length,
                current: wizardBoardPage,
                pageSize: wizardBoardPageSize,
                showSizeChanger: true,
                pageSizeOptions: [5, 10, 20, 50],
                onChange: (page) => setWizardBoardPage(page),
                onShowSizeChange: (_current, size) => setWizardBoardPageSize(size),
                showTotal: (t) =>
                  renderListPaginationTotal(t, wizardBoardPageSize, (size) => setWizardBoardPageSize(size), {
                    pageSizeOptions: [5, 10, 20, 50],
                  }),
              }}
              rowKey="id" 
              size="small" 
              rowSelection={{
                type: 'radio',
                selectedRowKeys: wizardData.boardId ? [wizardData.boardId] : [],
                onChange: (selectedRowKeys) => {
                    updateWizardField('boardId', selectedRowKeys[0])
                },
              }}
              onRow={(record) => ({
                onClick: () => {
                    updateWizardField('boardId', record.id)
                }
              })}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
              <PageSecondaryButton onClick={handlePrev}>&lt; 上一步</PageSecondaryButton>
              <PagePrimaryButton onClick={handleNext}>下一步 &gt;</PagePrimaryButton>
            </div>
            <Modal
              title="产品详情"
              open={isBoardDetailOpen}
              onCancel={() => setIsBoardDetailOpen(false)}
              footer={<Button type="primary" className="board-detail-close-button" onClick={() => setIsBoardDetailOpen(false)}>关闭</Button>}
              closable={false}
              className="pcids-modal pcids-modal--wide board-detail-modal"
            >
              {boardDetailRecord && (
                <BoardDetailPanel
                  record={boardDetailRecord}
                  burnInterfaceText={parseBoardBurnInterfaces(boardDetailRecord.burn_interface).join('、')}
                  communicationInterfaceText={parseBoardBurnInterfaces(boardDetailRecord.interface).join('、')}
                  onPreviewImage={openBoardImagePreview}
                />
              )}
            </Modal>
            <Modal className="pcids-modal pcids-modal--preview" open={Boolean(previewBoardImage)} footer={null} onCancel={() => setPreviewBoardImage('')}>
              {previewBoardImage ? (
                <img src={previewBoardImage} alt="board-preview" style={{ width: '100%', maxHeight: '75vh', objectFit: 'contain' }} />
              ) : null}
            </Modal>
          </div>
        )}

        {currentStep === 1 && platform === 'os' && (
          <div>
            <Row gutter={40}>
              <Col span={15}>
                <div style={{ marginBottom: 28 }}>
                  <div style={sectionTitleStyle}>选择操作系统</div>
                  <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                    {osList.map((os) => {
                      const selected = wizardData.osId === os.id
                      return (
                        <div
                          key={os.id}
                          onClick={() => updateWizardField('osId', os.id)}
                          style={{
                            width: 128,
                            height: 86,
                            border: `1px solid ${selected ? '#4045D6' : '#d9d9d9'}`,
                            borderRadius: 8,
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            justifyContent: 'center',
                            cursor: 'pointer',
                            background: selected ? '#F0F5FF' : '#fff',
                          }}
                        >
                          <img src={os.icon} alt={os.name} style={{ width: 34, height: 34, objectFit: 'contain', marginBottom: 8 }} />
                          <span style={{ fontSize: 12, color: selected ? '#4045D6' : '#4e5969' }}>{os.name}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>

                {isHarmonyOs ? (
                  <>
                    <div style={{ marginBottom: 24 }}>
                      <div style={sectionTitleStyle}>目标连接信息</div>
                      <Row gutter={40}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>连接协议</div>
                          <Select disabled style={{ width: '100%' }} value="HDC" options={[{ label: 'HDC', value: 'HDC' }]} />
                        </Col>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>设备列表</div>
                          <Select
                            style={{ width: '100%' }}
                            status={osFieldStatus('harmonyDeviceId')}
                            value={wizardData.harmonyDeviceId || undefined}
                            placeholder="请选择设备"
                            options={wizardHarmonyDevices.map((item) => ({ label: item.name || item.id, value: item.id }))}
                            onChange={(value) => updateWizardField('harmonyDeviceId', value)}
                            notFoundContent="未检测到HDC设备"
                          />
                          {renderOsFieldError('harmonyDeviceId')}
                        </Col>
                      </Row>
                    </div>
                  </>
                ) : isSylixOs ? (
                  <>
                    <div style={{ marginBottom: 24 }}>
                      <div style={sectionTitleStyle}>部署方式选择</div>
                      <div style={fieldLabelStyle}>部署方式</div>
                      <Select
                        style={{ width: 220 }}
                        disabled
                        value="FTP"
                        options={[{ label: 'FTP', value: 'FTP' }]}
                      />
                    </div>
                    <div style={{ marginBottom: 24 }}>
                      <div style={sectionTitleStyle}>目标连接信息</div>
                      <Row gutter={40} style={{ marginBottom: 16 }}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('目标地址')}</div>
                          <Input status={osFieldStatus('targetIp')} placeholder="请输入目标IP地址" value={wizardData.targetIp} onChange={(e) => updateWizardField('targetIp', e.target.value)} />
                          {renderOsFieldError('targetIp')}
                        </Col>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('FTP端口')}</div>
                          <Input status={osFieldStatus('ftpPort')} value={wizardData.ftpPort} onChange={(e) => updateWizardField('ftpPort', e.target.value.replace(/[^\d]/g, ''))} />
                          {renderOsFieldError('ftpPort')}
                        </Col>
                      </Row>
                      <Row gutter={40} style={{ marginBottom: 16 }}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('登录用户')}</div>
                          <Input status={osFieldStatus('loginUsername')} value={wizardData.loginUsername} onChange={(e) => updateWizardField('loginUsername', e.target.value)} />
                          {renderOsFieldError('loginUsername')}
                        </Col>
                        <Col span={12}>
                          <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <span>{wizardData.loginPasswordless ? '登录密码' : requiredLabel('登录密码')}</span>
                            <Checkbox
                              checked={Boolean(wizardData.loginPasswordless)}
                              onChange={(e) => {
                                updateWizardField('loginPasswordless', e.target.checked)
                                if (e.target.checked) updateWizardField('loginPassword', '')
                              }}
                            >
                              免密登录
                            </Checkbox>
                          </div>
                          <Input.Password
                            className="pcids-deploy-password"
                            status={osFieldStatus('loginPassword')}
                            disabled={Boolean(wizardData.loginPasswordless)}
                            placeholder={wizardData.loginPasswordless ? '将使用空密码登录' : '请输入登录密码'}
                            value={wizardData.loginPassword}
                            onChange={(e) => updateWizardField('loginPassword', e.target.value)}
                          />
                          {renderOsFieldError('loginPassword')}
                        </Col>
                      </Row>
                      <Row gutter={40}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('安装目录')}</div>
                          <Input status={osFieldStatus('installDir')} placeholder="/apps" value={wizardData.installDir} onChange={(e) => updateWizardField('installDir', e.target.value)} />
                          {renderOsFieldError('installDir')}
                        </Col>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>启动选项</div>
                          <Checkbox checked={Boolean(wizardData.bootAutostart)} onChange={(e) => updateWizardField('bootAutostart', e.target.checked)}>
                            开机自启
                          </Checkbox>
                        </Col>
                      </Row>
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ marginBottom: 24 }}>
                      <div style={sectionTitleStyle}>目标连接信息</div>
                      <Row gutter={16} style={{ marginBottom: 18 }}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('目标地址')}</div>
                          <Input status={osFieldStatus('targetIp')} placeholder="请输入目标IP地址" value={wizardData.targetIp} onChange={(e) => updateWizardField('targetIp', e.target.value)} />
                          {renderOsFieldError('targetIp')}
                        </Col>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('目标端口')}</div>
                          <Input status={osFieldStatus('targetPort')} placeholder="请输入目标端口" value={wizardData.targetPort} onChange={(e) => updateWizardField('targetPort', e.target.value.replace(/[^\d]/g, ''))} />
                          {renderOsFieldError('targetPort')}
                        </Col>
                      </Row>
                      <Row gutter={16} style={{ marginBottom: 18 }}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>连接协议</div>
                          <Select disabled style={{ width: '100%' }} value="SSH" options={[{ label: 'SSH', value: 'SSH' }]} />
                        </Col>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>认证方式</div>
                          <Select
                            style={{ width: '100%' }}
                            value={wizardData.authType}
                            onChange={(value) => updateWizardField('authType', value)}
                            options={[
                              { label: '密钥认证', value: 'key' },
                              { label: '密码认证', value: 'password' },
                            ]}
                          />
                        </Col>
                      </Row>
                      <Row gutter={16}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>{requiredLabel('登录用户名')}</div>
                          <Input status={osFieldStatus('loginUsername')} placeholder="请输入登录用户名" value={wizardData.loginUsername} onChange={(e) => updateWizardField('loginUsername', e.target.value)} />
                          {renderOsFieldError('loginUsername')}
                        </Col>
                        <Col span={12}>
                          {wizardData.authType === 'password' ? (
                            <>
                              <div style={fieldLabelStyle}>{requiredLabel('登录密码')}</div>
                              <Input.Password
                                className="pcids-deploy-password"
                                status={osFieldStatus('loginPassword')}
                                placeholder="请输入登录密码"
                                value={wizardData.loginPassword}
                                onChange={(e) => updateWizardField('loginPassword', e.target.value)}
                              />
                              {renderOsFieldError('loginPassword')}
                            </>
                          ) : (
                            <>
                              <div style={fieldLabelStyle}>密钥地址</div>
                              <Input
                                status={osFieldStatus('privateKeyPath')}
                                placeholder="请输入私钥文件地址，留空则使用默认 SSH 密钥"
                                value={wizardData.privateKeyPath}
                                onChange={(e) => updateWizardField('privateKeyPath', e.target.value)}
                              />
                              {renderOsFieldError('privateKeyPath')}
                            </>
                          )}
                        </Col>
                      </Row>
                    </div>
                    <div style={{ marginBottom: 18 }}>
                      <div style={sectionTitleStyle}>安装参数</div>
                      <div style={fieldLabelStyle}>{requiredLabel('安装目录')}</div>
                      <Input status={osFieldStatus('installDir')} placeholder="请输入安装目录" value={wizardData.installDir} onChange={(e) => updateWizardField('installDir', e.target.value)} style={{ width: 'calc(50% - 8px)' }} />
                      <div style={{ width: 'calc(50% - 8px)' }}>{renderOsFieldError('installDir')}</div>
                    </div>
                  </>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
                  <Button type="primary" ghost loading={osConnectionTesting} onClick={handleOsConnectionTest}>
                    连接测试
                  </Button>
                </div>
                {renderOsFieldError('connectionTest')}
              </Col>
              <Col span={9} style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 32 }}>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>安装选项</div>
                  <div style={{ marginBottom: 12, color: '#4e5969', fontSize: 13 }}>
                    制品来源：{selectedInstallSourceLabel}
                  </div>
                  <Checkbox.Group value={effectiveWizardOptions} onChange={(value) => updateWizardField('options', versionCheckDisabled ? value.filter((item) => item !== 'version') : value)} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                    <Checkbox value="local" disabled={keepLocalDisabled}>{renderOptionWithTip('保留可执行文件', keepLocalTip)}</Checkbox>
                    <Checkbox value="version" disabled={versionCheckDisabled}>{renderOptionWithTip('版本校验', versionCheckTip)}</Checkbox>
                    <Checkbox value="integrity">完整性校验(MD5|SHA256)</Checkbox>
                  </Checkbox.Group>
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>烧录失败重试次数 <span style={{ ...helperTextStyle, marginLeft: 8 }}>默认重试次数1次，最多5次</span></div>
                  {renderCounterField('retryCount', 0, 5)}
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>任务超时时间(S)</div>
                  {renderCounterField('timeoutMinutes', 1, MAX_TASK_TIMEOUT_SECONDS)}
                </div>
                <div>
                  <div style={sectionTitleStyle}>备注</div>
                  <Input.TextArea rows={4} placeholder="备注信息" value={wizardData.remark} onChange={(e) => updateWizardField('remark', e.target.value)} />
                </div>
              </Col>
            </Row>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 40 }}>
              <PageSecondaryButton onClick={handlePrev}>&lt; 上一步</PageSecondaryButton>
              <PagePrimaryButton loading={wizardSubmitLoading} onClick={handleWizardFinish}>完成</PagePrimaryButton>
            </div>
          </div>
        )}

        {currentStep === 2 && platform === 'hybrid' && (
          <div>
            <Row gutter={40}>
              <Col span={15}>
                <Row gutter={16} style={{ marginBottom: 20 }}>
                  <Col span={12}>
                    <div style={fieldLabelStyle}>烧录模式</div>
                    <Select style={{ width: '100%' }} value={wizardData.burnMode} onChange={(value) => updateWizardField('burnMode', value)} options={HYBRID_BURN_MODE_OPTIONS} />
                  </Col>
                  <Col span={12}>
                    <div style={fieldLabelStyle}>{requiredLabel('服务端口')}</div>
                    <Input value={wizardData.serverPort} readOnly />
                  </Col>
                </Row>

                <div style={{ marginBottom: 20 }}>
                  <div style={sectionTitleStyle}>混合协同执行脚本</div>
                  <Select
                    key={`hybrid-script-${wizardData.boardId || 'none'}`}
                    style={{ width: '100%' }}
                    placeholder={wizardData.boardId ? '请选择混合协同执行脚本' : '请先选择板卡'}
                    value={wizardData.scriptId}
                    onChange={(v) => updateWizardField('scriptId', v)}
                    options={visibleBoardScripts.map((s) => ({ label: s.name, value: s.id }))}
                    disabled={!wizardData.boardId}
                  />
                  {wizardData.boardId ? (
                    <div style={helperTextStyle}>当前板卡可选 {visibleBoardScripts.length} 个混合协同脚本，脚本将在文件下发后通过串口执行</div>
                  ) : null}
                </div>

                <div style={{ marginBottom: 20 }}>
                  <div style={sectionTitleStyle}>目标连接信息</div>
                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={12}>
                      <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span>串口</span>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                          <ActionLinkButton loading={hybridConnectionTesting} onClick={handleHybridConnectionTest}>
                            {hybridConnectionPassed ? '重新测试' : '连接测试'}
                          </ActionLinkButton>
                          <ActionLinkButton loading={wizardContextLoading} onClick={refreshTaskWizardContext}>刷新串口</ActionLinkButton>
                        </span>
                      </div>
                      <Select
                        style={{ width: '100%' }}
                        value={wizardData.serialPort || undefined}
                        onChange={(value) => updateWizardField('serialPort', value)}
                        options={wizardSerialPorts.map((item) => ({ label: item, value: item }))}
                        placeholder={wizardContextLoading ? '正在扫描串口' : '请选择真实串口'}
                        notFoundContent={wizardContextLoading ? '正在扫描串口' : '未扫描到串口'}
                      />
                    </Col>
                    <Col span={12}>
                      <div style={fieldLabelStyle}>波特率</div>
                      <Select style={{ width: '100%' }} value={wizardData.baudRate} onChange={(value) => updateWizardField('baudRate', value)} options={HYBRID_BAUD_RATE_OPTIONS} />
                    </Col>
                  </Row>

                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={12}>
                      <div style={fieldLabelStyle}>{requiredLabel('串口登录用户')}</div>
                      <Input value={wizardData.serialLoginUser} onChange={(e) => updateWizardField('serialLoginUser', e.target.value)} />
                    </Col>
                    <Col span={12}>
                      <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span>{requiredLabel('串口登录密码')}</span>
                        <Checkbox checked={wizardData.serialPasswordless} onChange={(e) => updateWizardField('serialPasswordless', e.target.checked)}>免登录</Checkbox>
                      </div>
                      <Input.Password className="pcids-deploy-password" value={wizardData.serialLoginPassword} disabled={wizardData.serialPasswordless} onChange={(e) => updateWizardField('serialLoginPassword', e.target.value)} />
                    </Col>
                  </Row>

                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={12}>
                      <div style={fieldLabelStyle}>{requiredLabel('设置板卡地址')}</div>
                      <Input placeholder="请输入目标IP地址" value={wizardData.boardTargetAddress} onChange={(e) => updateWizardField('boardTargetAddress', e.target.value)} />
                    </Col>
                    <Col span={12}>
                      <div style={fieldLabelStyle}>本地IP</div>
                      <Select
                        style={{ width: '100%' }}
                        value={wizardData.localIp || undefined}
                        onChange={(value) => updateWizardField('localIp', value)}
                        options={Array.from(new Set([String(wizardData.localIp || '').trim(), ...wizardLocalIps].filter(Boolean))).map((item) => ({ label: item, value: item }))}
                        placeholder="请选择本地IP"
                      />
                    </Col>
                  </Row>

                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={12}>
                      <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>{requiredLabel('当前FTP登录用户')}</span>
                        <Tooltip title="用于连接板卡当前正在运行的 FTP 服务，只负责上传 hdd0/hdd1，不会修改系统账户。首次烧录通常为 root。">
                          <QuestionCircleOutlined style={{ color: '#86909C', fontSize: 14, cursor: 'help' }} />
                        </Tooltip>
                      </div>
                      <Input
                        value={wizardData.ftpLoginUser}
                        onChange={(e) => updateWizardField('ftpLoginUser', e.target.value)}
                      />
                    </Col>
                    <Col span={12}>
                      <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>{requiredLabel('当前FTP登录密码')}</span>
                        <Tooltip title="填写板卡当前已经生效的密码。重复烧录时，应填写上一次烧录后设置并已生效的密码。">
                          <QuestionCircleOutlined style={{ color: '#86909C', fontSize: 14, cursor: 'help' }} />
                        </Tooltip>
                      </div>
                      <Input.Password
                        className="pcids-deploy-password"
                        value={wizardData.ftpLoginPassword}
                        onChange={(e) => updateWizardField('ftpLoginPassword', e.target.value)}
                      />
                    </Col>
                  </Row>

                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={12}>
                      <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>新增用户名</span>
                        <Tooltip title="写入 hdd1 的目标系统账户。新建用户为普通用户；填写 root 时仅更新 root 密码，不改变其权限。">
                          <QuestionCircleOutlined style={{ color: '#86909C', fontSize: 14, cursor: 'help' }} />
                        </Tooltip>
                      </div>
                      <Input
                        className="pcids-system-account-input"
                        name="sylixos-system-account-username"
                        autoComplete="new-password"
                        value={wizardData.systemUsername}
                        maxLength={32}
                        onChange={(e) => updateWizardField('systemUsername', e.target.value)}
                      />
                    </Col>
                    <Col span={12}>
                      <div style={{ ...fieldLabelStyle, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>新增密码</span>
                        <Tooltip title="写入 hdd1/etc/shadow，任务完成后重启并从 hdd1 启动才会生效。">
                          <QuestionCircleOutlined style={{ color: '#86909C', fontSize: 14, cursor: 'help' }} />
                        </Tooltip>
                      </div>
                      <Input.Password
                        className="pcids-system-account-input"
                        name="sylixos-system-account-password"
                        autoComplete="new-password"
                        value={wizardData.systemPassword}
                        maxLength={128}
                        onChange={(e) => updateWizardField('systemPassword', e.target.value)}
                      />
                    </Col>
                  </Row>

                  <div>
                    <div style={fieldLabelStyle}>{requiredLabel('目标路径')}</div>
                    <Input value={wizardData.targetPath} onChange={(e) => updateWizardField('targetPath', e.target.value)} />
                    {selectedScript?.name === SYLIXOS_HYBRID_SCRIPT_NAME ? (
                      <div style={helperTextStyle}>
                        TFTP 救援模式：PMON 加载制品原文件名，执行 g 后分区，并上传内置 hdd0/hdd1 备份
                      </div>
                    ) : null}
                  </div>
                </div>
              </Col>

              <Col span={9} style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 32 }}>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>烧录选项</div>
                  <Checkbox.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }} value={effectiveWizardOptions} onChange={(value) => updateWizardField('options', versionCheckDisabled ? value.filter((item) => item !== 'version') : value)}>
                    <Checkbox value="local" disabled={keepLocalDisabled}>{renderOptionWithTip('保留可执行文件', keepLocalTip)}</Checkbox>
                    <Checkbox value="version" disabled={versionCheckDisabled}>{renderOptionWithTip('版本校验', versionCheckTip)}</Checkbox>
                    <Checkbox value="integrity">完整性校验(MD5|SHA256)</Checkbox>
                  </Checkbox.Group>
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>烧录失败重试次数 <span style={{ ...helperTextStyle, marginLeft: 8 }}>默认重试次数1次，最多5次</span></div>
                  {renderCounterField('retryCount', 0, 5)}
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>任务超时时间(S)</div>
                  {renderCounterField('timeoutMinutes', 1, MAX_TASK_TIMEOUT_SECONDS)}
                </div>
                <div>
                  <div style={sectionTitleStyle}>备注</div>
                  <Input.TextArea rows={4} placeholder="备注信息" value={wizardData.remark} onChange={(e) => updateWizardField('remark', e.target.value)} />
                </div>
              </Col>
            </Row>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 40 }}>
              <PageSecondaryButton onClick={handlePrev}>&lt; 上一步</PageSecondaryButton>
              <PagePrimaryButton loading={wizardSubmitLoading} onClick={handleWizardFinish}>完成</PagePrimaryButton>
            </div>
          </div>
        )}

        {currentStep === 2 && platform === 'board' && (
          <div>
            <Row gutter={40}>
              <Col span={15}>
                <Row gutter={16} style={{ marginBottom: 20 }}>
                  <Col span={12}>
                    <div style={{ ...sectionTitleStyle, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span>选择设备/安装通道</span>
                      <Button
                        type="link"
                        size="small"
                        icon={<SyncOutlined />}
                        loading={burnerScanLoading}
                        onClick={() => refreshRecommendedBurnerStatus()}
                        style={{ paddingInline: 0 }}
                      >
                        刷新状态
                      </Button>
                    </div>
                    <Select
                      style={{ width: '100%' }}
                      placeholder={recommendedBurners.length > 0 ? '请选择推荐烧录器' : '当前板卡未匹配到推荐烧录器'}
                      value={wizardData.burnerId}
                      onChange={(v) => setWizardData((prev: any) => ({ ...prev, burnerId: v, scriptId: undefined }))}
                      options={burnerSelectOptions}
                      loading={burnerScanLoading}
                      disabled={recommendedBurners.length === 0}
                      optionRender={(option: any) => (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                              <EllipsisText value={option.data.label} />
                              {option.data.recommended ? <Tag color="gold" style={{ marginLeft: 8, borderRadius: 10 }}>推荐</Tag> : null}
                            </div>
                            <div style={{ color: '#999', fontSize: 12 }}>{option.data.dropdownMeta}</div>
                          </div>
                          {option.data.statusTag}
                        </div>
                      )}
                    />
                    <div style={helperTextStyle}>
                      {burnerScanLoading
                        ? '正在检测推荐烧录器在线状态...'
                        : `已检测 ${recommendedBurners.length} 个推荐烧录器，空闲 ${onlineBurnerCount} 个，占用 ${busyBurnerCount} 个，离线 ${offlineBurnerCount} 个，占用/离线设备不可选`}
                    </div>
                    <div style={helperTextStyle}>
                      {recommendedBurners.length > 0
                        ? `当前仅展示推荐烧录器，共 ${recommendedBurners.length} 个`
                        : '当前板卡没有匹配到推荐烧录器，请先检查烧录脚本绑定关系'}
                    </div>
                    {burnerScanError ? <div style={{ ...helperTextStyle, color: '#ff4d4f' }}>{burnerScanError}</div> : null}
                    {selectedBurnerBusy ? <div style={{ ...helperTextStyle, color: '#faad14' }}>当前选择的设备正在执行其他任务，请等待任务结束或更换设备</div> : null}
                    {selectedBurnerOnline === false ? <div style={{ ...helperTextStyle, color: '#ff4d4f' }}>当前选择的设备离线，请切换为在线设备</div> : null}
                  </Col>
                  <Col span={12}>
                    <div style={sectionTitleStyle}>选择烧录脚本</div>
                    <Select
                      key={`${wizardData.boardId || 'none'}-${wizardData.burnerId || 'none'}`}
                      style={{ width: '100%' }}
                      placeholder={wizardData.burnerId ? '请选择烧录脚本' : '请先选择设备/安装通道'}
                      value={wizardData.scriptId}
                      onChange={(v) => updateWizardField('scriptId', v)}
                      options={visibleBoardScripts.map((s) => ({ label: s.name, value: s.id }))}
                      disabled={!wizardData.burnerId}
                    />
                    {wizardData.burnerId ? (
                      <div style={helperTextStyle}>
                        当前条件下可选 {visibleBoardScripts.length} 个脚本
                        {systemBoardScripts.length > 0 ? '，已优先显示系统脚本' : ''}
                      </div>
                    ) : null}
                    {burnerBoundScripts.length > 0 ? <div style={helperTextStyle}>已按烧录器与脚本的绑定关系自动匹配脚本</div> : null}
                    {wizardData.burnerId && visibleBoardScripts.length === 0 ? (
                      <div style={helperTextStyle}>当前设备所属型号下没有匹配脚本，请调整烧录器或去脚本管理维护关联关系</div>
                    ) : null}
                  </Col>
                </Row>
                <div style={{ marginBottom: 20 }}>
                  <div style={sectionTitleStyle}>选择IDE</div>
                  <Select
                    style={{ width: '100%' }}
                    value={wizardData.ide}
                    onChange={(v) => setWizardData((prev: any) => ({ ...prev, ide: v }))}
                    options={BOARD_IDE_OPTIONS.map((item) => ({ label: item || '不选择IDE', value: item }))}
                  />
                </div>
                <div style={{ marginBottom: 16 }}>
                  <div style={sectionTitleStyle}>烧录配置</div>
                  {!hasSelectedValidScript ? (
                    <>
                      <div style={{ ...helperTextStyle, marginBottom: 12 }}>请选择合法烧录脚本后，系统将自动加载该脚本唯一匹配的专属配置参数</div>
                      <Row gutter={16}>
                        <Col span={12}>
                          <div style={fieldLabelStyle}>脚本专属参数</div>
                          <Select style={{ width: '100%' }} disabled placeholder="请先选择合法烧录脚本" />
                        </Col>
                      </Row>
                    </>
                  ) : !isSelectedScriptSystem ? (
                    <div style={{ ...helperTextStyle, marginBottom: 12 }}>
                      自定义脚本暂不支持动态配置烧录参数，请在脚本内容中自行处理执行参数。
                    </div>
                  ) : (
                    <>
                      <div style={{ ...helperTextStyle, marginBottom: 12 }}>已根据当前烧录器与脚本自动匹配并加载专属配置参数</div>
                      {scriptParameterDescriptors.length > 0 ? (
                        <Row gutter={16} style={{ rowGap: 16 }}>
                          {scriptParameterDescriptors.map((item) => (
                            <Col key={item.field} span={12}>
                              <div style={fieldLabelStyle}>{isRequiredScriptField(item.field) ? requiredLabel(item.label) : item.label}</div>
                              {item.control === 'select' ? (
                                <Select
                                  style={{ width: '100%' }}
                                  value={item.value}
                                  onChange={(value) => updateWizardField(item.field, value)}
                                  options={item.options}
                                  disabled={item.disabled}
                                />
                              ) : (
                                <>
                                  <Input
                                    placeholder={item.placeholder}
                                    value={wizardData[item.field]}
                                    onChange={(event) => updateWizardField(item.field, event.target.value)}
                                  />
                                  {item.hint ? <div style={{ ...helperTextStyle, marginTop: 6 }}>{item.hint}</div> : null}
                                </>
                              )}
                            </Col>
                          ))}
                        </Row>
                      ) : (
                        <div style={helperTextStyle}>当前脚本未提供可调整的专属参数</div>
                      )}
                    </>
                  )}
                </div>
              </Col>
              <Col span={9} style={{ borderLeft: '1px solid #f0f0f0', paddingLeft: 32 }}>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>执行选项</div>
                  <div style={{ marginBottom: 12, color: '#4e5969', fontSize: 13 }}>
                    制品来源：{selectedInstallSourceLabel}
                  </div>
                  <Checkbox.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }} value={effectiveWizardOptions} onChange={v => updateWizardField('options', versionCheckDisabled ? v.filter((item) => item !== 'version') : v)}>
                    <Checkbox value="local" disabled={keepLocalDisabled}>{renderOptionWithTip('保留可执行文件', keepLocalTip)}</Checkbox>
                    <Checkbox value="version" disabled={versionCheckDisabled}>{renderOptionWithTip('版本校验', versionCheckTip)}</Checkbox>
                    <Checkbox value="integrity">完整性校验(MD5|SHA256)</Checkbox>
                    {supportsWriteVerify ? <Checkbox value="writeVerify">写入后校验</Checkbox> : null}
                  </Checkbox.Group>
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>烧录失败重试次数</div>
                  {renderCounterField('retryCount', 0, 5)}
                  <div style={helperTextStyle}>默认重试次数1次，最多5次</div>
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={sectionTitleStyle}>任务超时时间(S)</div>
                  {renderCounterField('timeoutMinutes', 1, MAX_TASK_TIMEOUT_SECONDS)}
                </div>
                <div>
                  <div style={sectionTitleStyle}>备注</div>
                  <Input.TextArea rows={4} placeholder="备注信息" value={wizardData.remark} onChange={(e) => updateWizardField('remark', e.target.value)} />
                </div>
              </Col>
            </Row>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 40 }}>
              <Button onClick={handlePrev}>&lt; 上一步</Button>
              <PagePrimaryButton loading={wizardSubmitLoading} onClick={handleWizardFinish}>完成</PagePrimaryButton>
            </div>
          </div>
        )}
      </div>
    )
  }

  // Main List View
  return (
    <div style={{ padding: '0 24px 24px', background: '#fff', minHeight: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 0', marginBottom: 16 }}>
        <div className="client-page-title">
          <Title level={4}>烧录安装管理</Title>
          <p className="client-page-subtitle">跟踪任务历史、执行状态与一致性报告</p>
        </div>
        <Permission code="burning:add">
          <PagePrimaryButton icon={<PlusOutlined />} onClick={handleOpenWizard}>创建任务</PagePrimaryButton>
        </Permission>
      </div>

      <div style={{ marginBottom: 24 }}>
        <span style={{ color: '#4045D6', borderBottom: '2px solid #4045D6', paddingBottom: 8, cursor: 'pointer', fontWeight: 'bold' }}>烧录安装任务历史</span>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <Select
          showSearch
          value={params.board_name || 'all'}
          style={{ width: 220 }}
          placeholder="请选择烧录安装目标"
          options={targetFilterOptions}
          optionFilterProp="label"
          filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
          onChange={(v) => setParams({ ...params, page: 1, board_name: v === 'all' ? undefined : String(v) } as any)}
        />
        <Select
          value={typeof params.status === 'number' ? String(params.status) : 'all'}
          style={{ width: 140 }}
          options={statusFilterOptions}
          onChange={(v) => setParams({ ...params, page: 1, status: v === 'all' ? undefined : Number(v) })}
        />
        <Input
          className="pcids-list-search"
          placeholder="请输入软件名称/任务编号/执行人"
          title="请输入软件名称/任务编号/执行人"
          allowClear
          value={taskKeywordInput}
          prefix={<SearchOutlined />}
          onChange={(e) => {
            const nextValue = e.target.value
            setTaskKeywordInput(nextValue)
            if (!nextValue.trim()) {
              handleTaskKeywordSearch('')
            }
          }}
          onPressEnter={(e: any) => handleTaskKeywordSearch(String(e?.target?.value || ''))}
        />
      </div>

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

      <Drawer
        className="burning-detail-drawer"
        title="烧录安装任务详情"
        placement="right"
        width={392}
        onClose={() => setIsDetailOpen(false)}
        open={isDetailOpen}
        styles={{
          header: { padding: '18px 20px 0', borderBottom: 'none' },
          body: { padding: '0 20px 20px', height: 'calc(100vh - 58px)', overflow: 'hidden' },
        }}
      >
        {detailLoading && <div style={{ padding: 20, textAlign: 'center' }}>加载中...</div>}
        {detailTask && !detailLoading && (
          <Tabs
            defaultActiveKey="summary"
            tabBarStyle={{ marginBottom: 16 }}
            style={{ height: '100%' }}
            items={[
              {
                key: 'summary',
                label: '任务概要',
                children: (
                  <div style={{ paddingTop: 2, height: 'calc(100vh - 130px)', overflow: 'hidden', paddingBottom: 12, display: 'flex', flexDirection: 'column' }}>
                    <div style={detailSectionTitleStyle}>任务基本信息</div>
                    <div style={detailSummaryGridStyle}>
                      <div style={{ color: '#86909C' }}>任务编号</div>
                      <div style={detailFieldValueStyle}>{detailTask.task_no || '-'}</div>

                      <div style={{ color: '#86909C' }}>项目名称</div>
                      <div style={detailFieldValueStyle}>{getTaskProjectName(detailTask)}</div>

                      <div style={{ color: '#86909C' }}>烧录安装目标</div>
                      <div style={detailFieldValueStyle}>{getTaskTargetText(detailTask)}</div>

                      <div style={{ color: '#86909C' }}>执行状态</div>
                      <div>
                        <Badge
                          status={
                            getTaskStatusMeta(detailTask).color === 'success' ? 'success' :
                            getTaskStatusMeta(detailTask).color === 'error' ? 'error' :
                            getTaskStatusMeta(detailTask).color === 'processing' ? 'processing' : 'default'
                          }
                          text={getTaskStatusMeta(detailTask).text || '未知'}
                        />
                      </div>

                      {detailTask.last_error ? (
                        <Fragment>
                          <div style={{ color: '#86909C' }}>失败原因</div>
                          <div style={{ 
                            ...detailFieldValueStyle, 
                            color: '#F53F3F', 
                            fontWeight: 500,
                            maxHeight: 120,
                            overflowY: 'auto',
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-all',
                            paddingRight: 8
                          }}>
                            {decodeMojibakeString(detailTask.last_error)}
                          </div>
                        </Fragment>
                      ) : null}

                      <div style={{ color: '#86909C' }}>执行人</div>
                      <div style={detailFieldValueStyle}>{detailTask.executor || '-'}</div>

                      <div style={{ color: '#86909C' }}>创建时间</div>
                      <div style={detailFieldValueStyle}>{formatDateTime(detailTask.created_at)}</div>

                      <div style={{ color: '#86909C' }}>已用时间</div>
                      <div style={detailFieldValueStyle}>{formatTaskDuration(detailTask)}</div>

                      <div style={{ color: '#86909C' }}>进度</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ flex: 1, height: 6, background: '#F2F3F5', borderRadius: 99, overflow: 'hidden' }}>
                          <div
                            style={{
                              width: `${getTaskProgressPercent(detailTask)}%`,
                              height: '100%',
                              borderRadius: 99,
                              background: detailTask.status === 3 ? '#F53F3F' : '#4080FF',
                              transition: 'width 0.3s',
                            }}
                          />
                        </div>
                        <span style={{ fontSize: 12, color: '#4E5969' }}>{getTaskProgressPercent(detailTask)}%</span>
                      </div>
                    </div>

                    <div style={{ ...detailSectionTitleStyle, marginTop: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span>执行日志</span>
                      <Button
                        type="link"
                        size="small"
                        icon={<CopyOutlined />}
                        disabled={!detailTask.result}
                        onClick={handleCopyDetailLog}
                        style={{ padding: 0, height: 22 }}
                      >
                        复制日志
                      </Button>
                    </div>
                    <div style={{
                      background: '#F7F8FA',
                      borderRadius: 8,
                      padding: '12px 14px',
                      flex: 1,
                      minHeight: 280,
                      overflow: 'auto',
                      fontFamily: 'monospace',
                      fontSize: 12,
                      lineHeight: 1.7,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}>
                      {detailTask.result ? (
                        detailTask.result.split('\n').map((line: string, i: number) => {
                          const { time, tag, content } = parseTaskLogLine(line)
                          const normalizedTag = tag.toLowerCase()
                          const isError = ['error', 'failed', 'timeout'].some((value) => normalizedTag.includes(value)) || line.includes('失败') || line.includes('异常')
                          const isWarning = normalizedTag.includes('warning') || normalizedTag.includes('warn') || line.includes('警告')
                          const isSuccess = normalizedTag.includes('success') || normalizedTag.includes('done') || line.includes('成功')

                          return (
                            <div key={i} style={{ display: 'grid', gridTemplateColumns: time ? '54px minmax(0, auto) minmax(0, 1fr)' : tag ? 'minmax(0, auto) minmax(0, 1fr)' : 'minmax(0, 1fr)', columnGap: 8, color: isError ? '#F53F3F' : isWarning ? '#D46B08' : isSuccess ? '#00B42A' : '#4E5969' }}>
                              {time ? <span style={{ color: '#86909C', flexShrink: 0 }}>{time}</span> : null}
                              {tag ? <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>[{tag}]</span> : null}
                              <span style={{ minWidth: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{content}</span>
                            </div>
                          )
                        })
                      ) : (
                        <div style={{ color: '#86909C', textAlign: 'center', padding: 20 }}>暂无日志记录</div>
                      )}
                    </div>
                  </div>
                ),
              },
              {
                key: 'config',
                label: '软件及任务配置',
                children: (
                  <div style={{ paddingTop: 2, height: 'calc(100vh - 130px)', overflowY: 'auto', overscrollBehavior: 'contain', paddingBottom: 20 }}>
                    <div style={detailSectionTitleStyle}>软件及版本信息</div>
                    <div style={detailFieldGridStyle}>
                      <div>
                        <div style={detailFieldLabelStyle}>软件名称</div>
                        {renderDetailValue(getTaskSoftwareName(detailTask))}
                      </div>
                      <div>
                        <div style={detailFieldLabelStyle}>软件版本</div>
                        {getTaskVersionText(detailTask) ? (
                          <Tag color="blue" style={{ borderRadius: 10, margin: 0 }}>{getTaskVersionText(detailTask)}</Tag>
                        ) : renderDetailValue('-')}
                      </div>
                      <div>
                        <div style={detailFieldLabelStyle}>文件来源</div>
                        {renderDetailValue(getFileSourceInfo(detailTask).sourceType)}
                      </div>
                      <div>
                        <div style={detailFieldLabelStyle}>文件路径</div>
                        {renderDetailValue(getFileSourceInfo(detailTask).sourcePath)}
                      </div>
                      {detailTask.current_md5 || detailTask.expected_checksum ? (
                        <div style={{ gridColumn: '1 / -1' }}>
                          <div style={detailFieldLabelStyle}>{detailTask.current_md5 ? 'MD5' : '校验和'}</div>
                          {renderDetailBlockValue(detailTask.current_md5 || detailTask.expected_checksum)}
                        </div>
                      ) : null}
                      {detailTask.current_sha256 ? (
                        <div style={{ gridColumn: '1 / -1' }}>
                          <div style={detailFieldLabelStyle}>SHA256</div>
                          {renderDetailBlockValue(detailTask.current_sha256)}
                        </div>
                      ) : null}
                    </div>

                    <div style={{ ...detailSectionTitleStyle, marginTop: 24 }}>任务参数配置</div>
                    {renderTaskParams(detailTask)}
                  </div>
                ),
              },
            ]}
          />
        )}
        {!detailTask && !detailLoading && <div style={{ padding: 20, textAlign: 'center', color: '#999' }}>暂无数据</div>}
      </Drawer>

      <Modal 
        title={
          <Space>
            <span>版本一致性报告</span>
            <Tag color={consistencyConclusion.color} style={{ borderRadius: 10, margin: 0 }}>{consistencyConclusion.text}</Tag>
          </Space>
        }
        className="pcids-modal pcids-modal--form"
        open={isConsistencyOpen} 
        onCancel={() => setIsConsistencyOpen(false)} 
        footer={
          consistencyTask?.consistency_passed === 0 && !consistencyTask.override_confirmed ? (
            <Space>
              <Button onClick={() => handleOverride(consistencyTask.id)}>强制覆盖</Button>
            </Space>
          ) : null
        } 
      >
        {consistencyTask && (
          <div style={{ paddingTop: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <span style={{ color: '#666' }}>烧录安装目标</span>
              <span style={{ fontWeight: 'bold' }}>{getTaskTargetText(consistencyTask)}</span>
            </div>

            {consistencyTask.override_confirmed ? (
              <div style={{ marginBottom: 16 }}>
                <Tag color="warning">已允许覆盖</Tag>
              </div>
            ) : null}
            
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ color: '#666' }}>当前版本</span>
                <Space>
                  <span style={{ fontWeight: 'bold' }}>{consistencyTask.software_name}</span>
                  <Tag color="blue" style={{ borderRadius: 10, margin: 0 }}>{getTaskVersionText(consistencyTask) || '当前任务'}</Tag>
                </Space>
              </div>
              <div style={{ border: '1px solid #91caff', background: '#e6f7ff', padding: '4px 8px', borderRadius: 4, fontSize: 12, color: '#1890ff', wordBreak: 'break-all' }}>
                校验码：{consistencyTask.current_sha256 || consistencyTask.current_md5 || '-'}
              </div>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ color: '#666' }}>历史版本</span>
                <Space>
                  <span style={{ fontWeight: 'bold' }}>{consistencyTask.software_name}</span>
                  <Tag color="blue" style={{ borderRadius: 10, margin: 0 }}>{getTaskVersionText(consistencyTask) || '历史基线'}</Tag>
                </Space>
              </div>
              <div style={{ border: '1px solid #91caff', background: '#e6f7ff', padding: '4px 8px', borderRadius: 4, fontSize: 12, color: '#1890ff', wordBreak: 'break-all' }}>
                校验码：{consistencyTask.history_checksum || '-'}
              </div>
            </div>

          </div>
        )}
        {!consistencyTask && !reportLoading && <div>暂无数据</div>}
      </Modal>

      <ActionConfirmDialog
        open={!!terminateDialogTask}
        title="终止任务"
        description="终止请求提交后，系统会先执行资源清理与状态收尾，再将任务更新为“已终止”。"
        okText="提交终止"
        cancelText="取消"
        confirmLoading={!!terminatingTaskId}
        onConfirm={confirmTerminateTask}
        onCancel={closeTerminateDialog}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ color: '#4e5969', fontSize: 13 }}>
            任务编号：{terminateDialogTask?.task_no || '-'}
          </div>
          <Input.TextArea
            value={terminateReason}
            onChange={(e) => setTerminateReason(e.target.value)}
            placeholder="请输入终止原因，便于后续审计追踪"
            autoSize={{ minRows: 3, maxRows: 5 }}
            maxLength={500}
            showCount
          />
        </div>
      </ActionConfirmDialog>

    </div>
  )
}

export default Burning
