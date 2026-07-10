import { Card, Table, Button, Input, Modal, Form, App as AntdApp, Tag, Select, Tabs, Row, Col, InputNumber, Radio, Drawer, Divider, Checkbox } from 'antd'
import { SearchOutlined, CaretRightOutlined, PlusOutlined, ApiOutlined, DatabaseOutlined, DisconnectOutlined, SafetyCertificateOutlined, ReloadOutlined, ThunderboltOutlined, CloseOutlined } from '@ant-design/icons'
import { useEffect, useMemo, useRef, useState } from 'react'
import { injectionApi, productApi } from '../../services/api'
import { Permission } from '../../hooks'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { PagePrimaryButton } from '../../components/ActionButton'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'

const scenarioTemplates = [
  {
    id: 1,
    type: 'power_off',
    title: '断电模拟',
    config_json: JSON.stringify({ duration_seconds: 5, strategy: 'auto' }),
  },
  {
    id: 2,
    type: 'storage_full',
    title: '存储不足',
    config_json: JSON.stringify({ method: 'single', location: '/tmp', size: 50, strategy: 'auto' }),
  },
  {
    id: 3,
    type: 'network_error',
    title: '网络中断',
    config_json: JSON.stringify({ type: 'disconnect', duration_seconds: 30, ssh_port: 22, auth_type: 'password', packet_loss_percent: 80, packet_correlation_percent: 25, latency_ms: 2000, latency_jitter_ms: 200 }),
  },
  {
    id: 4,
    type: 'permission_error',
    title: '权限缺失',
    config_json: JSON.stringify({
      target_ip: '',
      ssh_port: 22,
      auth_type: 'password',
      target_path_mode: 'etc_app_conf',
      target_path: '/etc/app.conf',
      change_type: 'remove_write',
      root_protect: true,
      duration_seconds: 600,
      recovery_strategy: 'auto',
    }),
  },
] as const

const permissionChangeMeta: Record<string, { title: string; desc: string; badge?: string }> = {
  remove_write: {
    title: '移除写权限 (模拟只读)',
    desc: '模拟进程对目标路径无写入权限，验证程序的写保护日志、磁盘退出保护及告警上报逻辑。',
    badge: '默认',
  },
  remove_read: {
    title: '移除读权限',
    desc: '模拟目标路径无法被读取（如证书、私钥或动态配置文件缺失），验证程序启动校验和读取流程异常。',
    badge: 'chmod a-r',
  },
  remove_exec: {
    title: '移除执行权限',
    desc: '针对特定可执行脚本或二进制工具链移除运行权限，验证外部进程拉起异常及异常退出拦截行为。',
    badge: 'chmod a-x',
  },
}

const resolvePermissionTargetPath = (config: Record<string, any>) => {
  return String(config.target_path || '').trim() || '/etc/app.conf'
}

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

const Injection: React.FC = () => {
  const { message } = AntdApp.useApp()
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [isActionOpen, setIsActionOpen] = useState(false)
  const [isMonitorOpen, setIsMonitorOpen] = useState(false)
  const [injectionType, setInjectionType] = useState('')
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [boards, setBoards] = useState<any[]>([])
  const [powerPorts, setPowerPorts] = useState<any[]>([])
  const [scanningPowerPorts, setScanningPowerPorts] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState<number | 'all'>('all')
  const [recordTypeFilter, setRecordTypeFilter] = useState<string>('all')
  const [activeTab, setActiveTab] = useState('scenario')
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [selectedScenario, setSelectedScenario] = useState<any>(null)
  const [monitorRunId, setMonitorRunId] = useState<number | null>(null)
  const [monitorDetail, setMonitorDetail] = useState<any>(null)
  const [detailRecord, setDetailRecord] = useState<any>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [storageConnectionLoading, setStorageConnectionLoading] = useState(false)
  const [storageConnectionResult, setStorageConnectionResult] = useState<any>(null)
  const [networkConnectionLoading, setNetworkConnectionLoading] = useState(false)
  const [networkConnectionResult, setNetworkConnectionResult] = useState<any>(null)
  const [permissionConnectionLoading, setPermissionConnectionLoading] = useState(false)
  const [deletingRunId, setDeletingRunId] = useState<number | null>(null)
  const [permissionConnectionResult, setPermissionConnectionResult] = useState<any>(null)
  const [networkInterfaces, setNetworkInterfaces] = useState<string[]>([])
  const [actionForm] = Form.useForm()
  const pollingRef = useRef<number | null>(null)

  const injectionTypeAliases: Record<string, string> = {
    断电模拟: 'power_off',
    存储不足: 'storage_full',
    网络中断: 'network_error',
    权限缺失: 'permission_error',
  }

  const normalizeInjectionType = (type?: string) => injectionTypeAliases[type || ''] || type || ''

  const typeMap: Record<string, { color: string; text: string }> = {
    power_off: { color: 'red', text: '断电模拟' },
    storage_full: { color: 'orange', text: '存储不足' },
    network_error: { color: 'blue', text: '网络中断' },
    permission_error: { color: 'purple', text: '权限缺失' },
  }

  const recordTypeTextMap: Record<string, string> = {
    power_off: '断电模拟',
    storage_full: '存储注入',
    network_error: '网络中断',
    permission_error: '权限缺失',
  }

  const statusMap: Record<number, { color: string; text: string }> = {
    0: { color: 'default', text: '等待' },
    1: { color: 'processing', text: '进行中' },
    2: { color: 'success', text: '完成' },
    3: { color: 'error', text: '失败' },
    4: { color: 'warning', text: '终止' },
  }

  const getRecordTypeText = (type?: string) => recordTypeTextMap[normalizeInjectionType(type)] || type || '-'

  const fetchData = async () => {
    setLoading(true)
    try {
      const res: any = await injectionApi.getList({
        page,
        page_size: pageSize,
        keyword: keyword || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        injection_type: recordTypeFilter === 'all' ? undefined : recordTypeFilter,
        type: activeTab === 'scenario' ? 'scenario' : 'record',
      } as any)
      setDataSource(res?.data || [])
      setTotal(res?.total || 0)

      const bRes: any = await productApi.getList({ page: 1, page_size: 100 })
      setBoards(bRes?.data || [])
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
  }, [page, pageSize, statusFilter, recordTypeFilter, activeTab])

  useEffect(() => {
    return () => {
      if (pollingRef.current) window.clearInterval(pollingRef.current)
    }
  }, [])

  const scanPowerPorts = async () => {
    setScanningPowerPorts(true)
    try {
      const res: any = await injectionApi.scanPowerPorts()
      setPowerPorts(Array.isArray(res?.data) ? res.data : [])
    } catch {
      setPowerPorts([])
    } finally {
      setScanningPowerPorts(false)
    }
  }

  const parseConfig = (configJson?: string) => {
    try {
      return JSON.parse(configJson || '{}')
    } catch {
      return {}
    }
  }

  const openUnifiedModal = async (scenario: any) => {
    const normalizedType = normalizeInjectionType(scenario.type)
    const config = parseConfig(scenario.config || scenario.config_json)
    const durationValue = Number(config.duration_seconds || config.duration || 5)
    const durationPreset = [5, 10, 30].includes(durationValue) ? durationValue : 'custom'
    setSelectedScenario(scenario)
    setSelectedRecord(null)
    setInjectionType(normalizedType)
    setStorageConnectionResult(null)
    setNetworkConnectionResult(null)
    setPermissionConnectionResult(null)
    setNetworkInterfaces([])
    actionForm.resetFields()
    actionForm.setFieldsValue({
      target: scenario.target && scenario.target !== '未配置目标' ? scenario.target : config.default_target,
      power_port: config.power_port || undefined,
      power_duration: durationPreset,
      power_duration_custom: durationPreset === 'custom' ? durationValue : undefined,
      power_strategy: config.strategy || 'auto',
      storage_method: config.method || 'single',
      storage_location: config.location || '/tmp',
      storage_custom_location: config.custom_location,
      storage_size: [50, 80].includes(Number(config.size)) ? Number(config.size) : 'custom',
      storage_custom_size: [50, 80].includes(Number(config.size)) ? undefined : (Number(config.size) > 0 ? Number(config.size) : undefined),
      storage_strategy: config.strategy || 'auto',
      storage_target_ip: config.target_ip || '',
      storage_ssh_port: Number(config.ssh_port || 22),
      storage_login_username: config.login_username || 'root',
      storage_auth_type: config.auth_type === 'ssh_key' ? 'ssh_key' : 'password',
      storage_login_password: config.login_password || '',
      storage_ssh_private_key_path: config.ssh_private_key_path || '',
      network_target_ip: config.target_ip || config.default_target,
      network_ssh_port: Number(config.ssh_port || 22),
      network_login_username: config.login_username || 'root',
      network_auth_type: config.auth_type === 'ssh_key' ? 'ssh_key' : 'password',
      network_login_password: config.login_password || '',
      network_ssh_private_key_path: config.ssh_private_key_path || '',
      network_interface: config.network_interface,
      network_type: 'disconnect',
      network_duration_seconds: Number(config.duration_seconds || config.duration || 30),
      network_recovery_strategy: config.recovery_strategy || config.strategy || 'auto',
      network_packet_loss_percent: Number(config.packet_loss_percent || 80),
      network_packet_correlation_percent: Number(config.packet_correlation_percent || 25),
      network_latency_ms: Number(config.latency_ms || 2000),
      network_latency_jitter_ms: Number(config.latency_jitter_ms || 200),
      permission_target_ip: config.target_ip || config.default_target || '',
      permission_ssh_port: Number(config.ssh_port || 22),
      permission_login_username: config.login_username || 'root',
      permission_auth_type: config.auth_type === 'ssh_key' ? 'ssh_key' : 'password',
      permission_login_password: config.login_password || '',
      permission_ssh_private_key_path: config.ssh_private_key_path || '',
      permission_target_path: resolvePermissionTargetPath(config),
      permission_change_type: config.change_type || 'remove_write',
      permission_root_protect: typeof config.root_protect === 'boolean' ? config.root_protect : true,
      permission_duration_mode: Number(config.duration_seconds || 600) === 300 ? 300 : Number(config.duration_seconds || 600) === 600 ? 600 : 'custom',
      permission_duration_custom: ![300, 600].includes(Number(config.duration_seconds || 600)) ? Number(config.duration_seconds || 600) : undefined,
      permission_recovery_strategy: config.recovery_strategy || 'auto',
    })
    setIsModalOpen(false)
    setIsActionOpen(true)
    if (normalizedType === 'power_off') {
      await scanPowerPorts()
    }
  }

  const loadMonitorDetail = async (runId: number) => {
    try {
      const res: any = await injectionApi.getRunDetail(runId)
      setMonitorDetail(res?.data || null)
      if ((res?.data?.exec_status ?? 1) !== 1 && pollingRef.current) {
        window.clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    } catch {
      // ignore
    }
  }

  const loadRecordDetail = async (runId: number) => {
    setDetailLoading(true)
    try {
      const res: any = await injectionApi.getRunDetail(runId)
      setDetailRecord(res?.data || null)
    } catch {
      setDetailRecord(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleSearch = () => {
    if (page !== 1) {
      setPage(1)
      return
    }
    fetchData()
  }

  const openRunMonitor = async (runId: number, execStatus?: number) => {
    setIsMonitorOpen(true)
    setMonitorRunId(runId)
    await loadMonitorDetail(runId)
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current)
      pollingRef.current = null
    }
    if ((execStatus ?? 1) === 1) {
      pollingRef.current = window.setInterval(() => {
        loadMonitorDetail(runId)
      }, 1500)
    }
  }

  const buildScenarioPayload = (values: any) => {
    const normalizedType = normalizeInjectionType(injectionType)
    const targetValue = normalizedType === 'network_error'
      ? String(values.network_target_ip || '').trim()
      : normalizedType === 'storage_full'
        ? String(values.storage_target_ip || '').trim()
      : normalizedType === 'permission_error'
        ? String(values.permission_target_ip || '').trim()
      : String(values.target || '').trim()
    const config: any = { default_target: targetValue }

    if (normalizedType === 'power_off') {
      const durationSeconds = values.power_duration === 'custom'
        ? Number(values.power_duration_custom || 0)
        : Number(values.power_duration || 0)
      config.duration_seconds = durationSeconds
      config.duration = durationSeconds
      config.strategy = values.power_strategy
      config.run_mode = 'foreground'
      config.power_port = values.power_port
      const selectedPort = powerPorts.find((item) => item.port === values.power_port)
      config.power_label = selectedPort?.label || values.power_port
    } else if (normalizedType === 'storage_full') {
      config.method = values.storage_method
      config.location = values.storage_location
      if (values.storage_location === 'custom') config.custom_location = values.storage_custom_location
      config.size = values.storage_size === 'custom' ? Number(values.storage_custom_size || 0) : Number(values.storage_size || 0)
      config.strategy = values.storage_strategy
      config.target_ip = targetValue
      config.ssh_port = Number(values.storage_ssh_port || 22)
      config.login_username = String(values.storage_login_username || '').trim()
      config.auth_type = values.storage_auth_type
      if (values.storage_auth_type === 'password') {
        config.login_password = String(values.storage_login_password || '')
      } else {
        config.ssh_private_key_path = String(values.storage_ssh_private_key_path || '').trim()
      }
    } else if (normalizedType === 'network_error') {
      config.target_ip = targetValue
      config.ssh_port = Number(values.network_ssh_port || 22)
      config.login_username = String(values.network_login_username || '').trim()
      config.auth_type = values.network_auth_type
      if (values.network_auth_type === 'password') {
        config.login_password = String(values.network_login_password || '')
      } else {
        config.ssh_private_key_path = String(values.network_ssh_private_key_path || '').trim()
      }
      config.network_interface = values.network_interface
      config.type = values.network_type
      config.duration_seconds = Number(values.network_duration_seconds || 30)
      config.duration = Number(values.network_duration_seconds || 30)
      config.recovery_strategy = values.network_recovery_strategy || 'auto'
      if (values.network_type === 'packet_loss') {
        config.packet_loss_percent = Number(values.network_packet_loss_percent || 80)
        config.packet_correlation_percent = Number(values.network_packet_correlation_percent || 25)
      }
      if (values.network_type === 'latency') {
        config.latency_ms = Number(values.network_latency_ms || 2000)
        config.latency_jitter_ms = Number(values.network_latency_jitter_ms || 200)
      }
    } else if (normalizedType === 'permission_error') {
      const targetPath = String(values.permission_target_path || '').trim()
      const durationSeconds = values.permission_duration_mode === 'custom'
        ? Number(values.permission_duration_custom || 0)
        : Number(values.permission_duration_mode || 0)

      config.target_ip = targetValue
      config.ssh_port = Number(values.permission_ssh_port || 22)
      config.login_username = String(values.permission_login_username || '').trim()
      config.auth_type = values.permission_auth_type
      if (values.permission_auth_type === 'password') {
        config.login_password = String(values.permission_login_password || '')
      } else {
        config.ssh_private_key_path = String(values.permission_ssh_private_key_path || '').trim()
      }
      config.target_path_mode = 'custom_absolute'
      config.target_path = targetPath
      config.change_type = values.permission_change_type
      config.root_protect = Boolean(values.permission_root_protect)
      config.duration_seconds = durationSeconds
      config.duration = durationSeconds
      config.recovery_strategy = values.permission_recovery_strategy
    }

    return {
      type: normalizedType,
      target: targetValue,
      config: JSON.stringify(config),
      status: 0,
    }
  }

  const handleExecute = async () => {
    try {
      const values = await actionForm.validateFields()
      const payload = buildScenarioPayload(values)
      const normalizedType = normalizeInjectionType(payload.type)
      const runMode = 'foreground'
      let injectionId = selectedScenario?.persisted ? Number(selectedScenario.id) : 0

      if (selectedScenario?.persisted) {
        await injectionApi.update(injectionId, payload)
      } else {
        const createRes: any = await injectionApi.create(payload)
        injectionId = Number(createRes?.data?.id || 0)
      }
      const execRes: any = await injectionApi.execute(injectionId)
      message.success('异常注入已开始执行')
      setIsActionOpen(false)
      setSelectedScenario(null)
      actionForm.resetFields()
      fetchData()

      if (normalizedType === 'power_off' || normalizedType === 'network_error' || normalizedType === 'storage_full' || normalizedType === 'permission_error') {
        const nextRunId = Number(execRes?.data?.run_id || 0)
        if (nextRunId) {
          if (normalizedType === 'network_error' || normalizedType === 'storage_full' || normalizedType === 'permission_error' || runMode === 'foreground') {
            setMonitorRunId(nextRunId)
            setIsMonitorOpen(true)
            await loadMonitorDetail(nextRunId)
            if (pollingRef.current) window.clearInterval(pollingRef.current)
            pollingRef.current = window.setInterval(() => {
              loadMonitorDetail(nextRunId)
            }, 1500)
          } else {
            setIsMonitorOpen(false)
            setMonitorRunId(null)
            setMonitorDetail(null)
            if (pollingRef.current) {
              window.clearInterval(pollingRef.current)
              pollingRef.current = null
            }
            setActiveTab('record')
            setPage(1)
            message.info('断电模拟已转入后台托管运行，可在执行记录中查看状态和恢复供电')
          }
        }
      }
    } catch (e: any) {
      if (e?.errorFields) {
        message.warning(getFirstFormErrorMessage(e))
        return
      }
      message.error(e?.response?.data?.detail || '执行失败')
    }
  }

  const handleEmergencyRecover = async (runId = monitorRunId, source: 'monitor' | 'detail' = 'monitor') => {
    if (!runId) return
    try {
      await injectionApi.recoverRun(runId)
      const currentType = monitorDetail?.type || detailRecord?.type
      message.success(
        currentType === 'network_error'
          ? '已发送紧急恢复网络指令'
          : currentType === 'permission_error'
            ? '已发送紧急恢复权限指令'
            : '已发送紧急恢复上电指令'
      )
      if (source === 'monitor') {
        await loadMonitorDetail(runId)
      }
      if (source === 'detail') {
        await loadRecordDetail(runId)
        fetchData()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '恢复失败')
    }
  }

  const handleStopRun = async (record: any) => {
    try {
      await injectionApi.recoverRun(record.id)
      message.success('已触发终止操作')
      if (monitorRunId === record.id) {
        await loadMonitorDetail(record.id)
      }
      fetchData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '终止失败')
    }
  }

  const getScenarioConfig = (type: string, configJson: string) => {
    try {
      const c = JSON.parse(configJson)
      const normalizedType = normalizeInjectionType(type)
      if (normalizedType === 'power_off') {
        const recovery = c.strategy || c.recovery || 'auto'
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>持续时长&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.duration === 'custom' ? c.custom_duration : c.duration} 秒</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>恢复策略&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{recovery === 'auto' ? '自动恢复' : '手动恢复'}</span></div>
          </>
        )
      }
      if (normalizedType === 'storage_full') {
        const method = c.method || (c.fill === 'large_file' ? 'single' : 'multi')
        const location = c.location === 'custom' ? c.custom_location : (c.location || c.custom_location || '/tmp')
        const size = c.size === 'custom' ? `${c.custom_size}%` : (typeof c.size === 'number' ? `${c.size}%` : c.size)
        const recovery = c.strategy || c.recovery || 'auto'
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>填充方式&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{method === 'single' ? '创建单个大文件' : '创建多个小文件'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>填充位置及大小&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{location}, {size}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>恢复策略&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{recovery === 'auto' ? '测试完成后自动清理' : '手动清理'}</span></div>
          </>
        )
      }
      if (normalizedType === 'network_error') {
        const networkType = c.type === 'full' ? 'disconnect' : c.type
        const durationSeconds = c.duration_seconds || c.duration || 30
        const recovery = c.recovery_strategy || c.strategy || 'auto'
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>中断类型&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{networkType === 'disconnect' ? '完全中断' : networkType === 'packet_loss' ? '高丢包率' : '高延迟'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>连接目标&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.target_ip || c.default_target || '-'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>作用网卡&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.network_interface || '-'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>持续时长&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{durationSeconds} 秒</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>恢复策略&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{recovery === 'manual' ? '手动恢复' : '自动恢复'}</span></div>
          </>
        )
      }
      if (normalizedType === 'permission_error') {
        const permissionType = c.change_type === 'remove_exec' ? '移除执行权限' : c.change_type === 'remove_read' ? '移除读权限' : '移除写权限'
        const durationSeconds = Number(c.duration_seconds || c.duration || 600)
        const targetPath = resolvePermissionTargetPath(c)
        return (
          <>
            <div style={{ color: '#666', fontSize: 12 }}>连接目标&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{c.target_ip || c.default_target || '-'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>作用路径&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{targetPath || '-'}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>变更类型&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{permissionType}</span></div>
            <div style={{ color: '#666', fontSize: 12 }}>持续时长&nbsp;&nbsp;&nbsp;&nbsp;<span style={{ color: '#333' }}>{durationSeconds >= 60 ? `${Math.floor(durationSeconds / 60)} 分钟` : `${durationSeconds} 秒`}</span></div>
          </>
        )
      }
    } catch {
      return null
    }
  }

  const scenarioCardMap = new Map<string, any>()
  dataSource.forEach((item: any) => {
    const normalizedType = normalizeInjectionType(item.type)
    if (normalizedType && !scenarioCardMap.has(normalizedType)) {
      scenarioCardMap.set(normalizedType, item)
    }
  })

  const scenarioCards = scenarioTemplates.map((template) => {
    const persistedItem = scenarioCardMap.get(template.type)
    if (!persistedItem) {
      return { ...template, persisted: false }
    }
    return {
      ...persistedItem,
      raw_type: persistedItem.type,
      id: persistedItem.id,
      type: template.type,
      title: typeMap[template.type]?.text || template.title,
      config_json: persistedItem.config || template.config_json,
      persisted: true,
    }
  })

  const recordColumns = [
    {
      title: '任务编号',
      dataIndex: 'task_no',
      key: 'task_no',
      width: 130,
      render: (value: string, record: any) => value || record.id,
    },
    { title: '测试对象', dataIndex: 'target', key: 'target', width: 230, render: (value: string) => <EllipsisText value={value} /> },
    {
      title: '异常类型',
      dataIndex: 'type',
      key: 'type',
      width: 130,
      render: (type: string) => {
        const normalizedType = normalizeInjectionType(type)
        return <Tag color={typeMap[normalizedType]?.color}>{getRecordTypeText(type)}</Tag>
      },
    },
    {
      title: '执行人员',
      dataIndex: 'executor',
      key: 'executor',
      width: 180,
      render: (_: string, record: any) => (
        <div style={{ minWidth: 0 }}>
          <UserIdentity
            user={record?.executor_user}
            fallbackName={record?.executor}
            avatarSize={23}
          />
        </div>
      ),
    },
    {
      title: '时间',
      dataIndex: 'exec_time',
      key: 'exec_time',
      width: 180,
      render: (value: string) => formatDateTime(value),
    },
    {
      title: '执行状态',
      dataIndex: 'exec_status',
      key: 'exec_status',
      width: 120,
      render: (status: number) => <Tag color={statusMap[status]?.color}>{statusMap[status]?.text || '-'}</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 210,
      fixed: 'right' as const,
      render: (_: any, record: any) => {
        const normalizedType = normalizeInjectionType(record.type)
        const canStop = record.exec_status === 1 && ['power_off', 'network_error', 'permission_error'].includes(normalizedType)
        const canDelete = record.exec_status !== 1
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, whiteSpace: 'nowrap' }}>
            {canStop ? (
              <Button type="link" danger style={{ padding: 0 }} onClick={() => handleStopRun(record)}>
                停止
              </Button>
            ) : null}
            <Button type="link" style={{ padding: 0 }} onClick={() => openRunMonitor(record.id, record.exec_status)}>
              执行详情
            </Button>
            {canDelete ? (
              <ActionConfirm
                title="删除执行记录"
                description={`确认删除任务 ${record.task_no || record.id} 吗？`}
                okText="删除"
                cancelText="取消"
                confirmLoading={deletingRunId === record.id}
                onConfirm={async () => {
                  setDeletingRunId(record.id)
                  try {
                    await injectionApi.deleteRun(record.id)
                    message.success('删除成功')
                    fetchData()
                  } catch (e: any) {
                    message.error(e?.response?.data?.detail || '删除失败')
                  } finally {
                    setDeletingRunId(null)
                  }
                }}
              >
                <Permission code="injection:delete">
                  <Button type="link" danger style={{ padding: 0 }}>
                    删除
                  </Button>
                </Permission>
              </ActionConfirm>
            ) : null}
          </div>
        )
      },
    },
  ]

  const selectedDurationMode = Form.useWatch('power_duration', actionForm)
  const selectedStorageLocation = Form.useWatch('storage_location', actionForm)
  const storageCustomLocation = Form.useWatch('storage_custom_location', actionForm)
  const selectedStorageSize = Form.useWatch('storage_size', actionForm)
  const storageCustomSize = Form.useWatch('storage_custom_size', actionForm)
  const selectedStorageAuthType = Form.useWatch('storage_auth_type', actionForm)
  const storageTargetIp = Form.useWatch('storage_target_ip', actionForm)
  const storageLoginUsername = Form.useWatch('storage_login_username', actionForm)
  const storageLoginPassword = Form.useWatch('storage_login_password', actionForm)
  const storageSshPrivateKeyPath = Form.useWatch('storage_ssh_private_key_path', actionForm)
  const selectedNetworkType = Form.useWatch('network_type', actionForm)
  const selectedNetworkAuthType = Form.useWatch('network_auth_type', actionForm)
  const networkTargetIp = Form.useWatch('network_target_ip', actionForm)
  const networkLoginUsername = Form.useWatch('network_login_username', actionForm)
  const networkLoginPassword = Form.useWatch('network_login_password', actionForm)
  const networkSshPrivateKeyPath = Form.useWatch('network_ssh_private_key_path', actionForm)
  const networkInterfaceValue = Form.useWatch('network_interface', actionForm)
  const networkDurationSecondsValue = Form.useWatch('network_duration_seconds', actionForm)
  const networkRecoveryStrategy = Form.useWatch('network_recovery_strategy', actionForm)
  const networkPacketLossPercentValue = Form.useWatch('network_packet_loss_percent', actionForm)
  const networkPacketCorrelationPercentValue = Form.useWatch('network_packet_correlation_percent', actionForm)
  const networkLatencyMsValue = Form.useWatch('network_latency_ms', actionForm)
  const networkLatencyJitterMsValue = Form.useWatch('network_latency_jitter_ms', actionForm)
  const selectedPermissionAuthType = Form.useWatch('permission_auth_type', actionForm)
  const permissionTargetIp = Form.useWatch('permission_target_ip', actionForm)
  const permissionLoginUsername = Form.useWatch('permission_login_username', actionForm)
  const permissionLoginPassword = Form.useWatch('permission_login_password', actionForm)
  const permissionSshPrivateKeyPath = Form.useWatch('permission_ssh_private_key_path', actionForm)
  const permissionTargetPath = Form.useWatch('permission_target_path', actionForm)
  const permissionChangeType = Form.useWatch('permission_change_type', actionForm)
  const permissionRootProtect = Form.useWatch('permission_root_protect', actionForm)
  const permissionDurationMode = Form.useWatch('permission_duration_mode', actionForm)
  const permissionDurationCustom = Form.useWatch('permission_duration_custom', actionForm)
  const permissionRecoveryStrategy = Form.useWatch('permission_recovery_strategy', actionForm)

  useEffect(() => {
    if (injectionType !== 'storage_full') return
    setStorageConnectionResult(null)
  }, [actionForm, injectionType, storageTargetIp, storageLoginUsername, selectedStorageAuthType])

  useEffect(() => {
    if (injectionType !== 'network_error') return
    setNetworkConnectionResult(null)
    setNetworkInterfaces([])
    actionForm.setFieldValue('network_interface', undefined)
  }, [actionForm, injectionType, networkTargetIp, networkLoginUsername, selectedNetworkAuthType])

  useEffect(() => {
    if (injectionType !== 'permission_error') return
    setPermissionConnectionResult(null)
  }, [actionForm, injectionType, permissionTargetIp, permissionLoginUsername, selectedPermissionAuthType])

  const powerPortOptions = useMemo(() => (
    powerPorts.map((item) => ({
      value: item.port,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <span>{item.label || item.port}</span>
          <Tag color="success" style={{ marginInlineEnd: 0 }}>在线</Tag>
        </div>
      ),
    }))
  ), [powerPorts])

  const handleNetworkConnectionTest = async () => {
    try {
      const values = await actionForm.validateFields([
        'network_target_ip',
        'network_ssh_port',
        'network_login_username',
        'network_auth_type',
        ...(selectedNetworkAuthType === 'ssh_key' ? ['network_ssh_private_key_path'] : ['network_login_password']),
      ])
      setNetworkConnectionLoading(true)
      const payload = {
        target_ip: String(values.network_target_ip || '').trim(),
        ssh_port: Number(values.network_ssh_port || 22),
        login_username: String(values.network_login_username || '').trim(),
        auth_type: values.network_auth_type,
        login_password: values.network_auth_type === 'password' ? String(values.network_login_password || '') : '',
        ssh_private_key_path: values.network_auth_type === 'ssh_key' ? String(values.network_ssh_private_key_path || '').trim() : '',
      }
      const res: any = await injectionApi.testNetworkErrorConnection(payload)
      const nextInterfaces = Array.isArray(res?.data?.interfaces) ? res.data.interfaces : []
      setNetworkConnectionResult(res?.data || null)
      setNetworkInterfaces(nextInterfaces)
      const currentInterface = actionForm.getFieldValue('network_interface')
      if (!currentInterface && nextInterfaces.length === 1) {
        actionForm.setFieldValue('network_interface', nextInterfaces[0])
      } else if (currentInterface && !nextInterfaces.includes(currentInterface)) {
        actionForm.setFieldValue('network_interface', undefined)
      }
      if (res?.data?.success) {
        message.success('连接测试通过')
      }
    } catch (e: any) {
      if (!e?.errorFields) {
        setNetworkConnectionResult(null)
        setNetworkInterfaces([])
      }
    } finally {
      setNetworkConnectionLoading(false)
    }
  }

  const handleStorageConnectionTest = async () => {
    try {
      const values = await actionForm.validateFields([
        'storage_target_ip',
        'storage_ssh_port',
        'storage_login_username',
        'storage_auth_type',
        ...(selectedStorageAuthType === 'ssh_key' ? ['storage_ssh_private_key_path'] : ['storage_login_password']),
      ])
      setStorageConnectionLoading(true)
      const payload = {
        target_ip: String(values.storage_target_ip || '').trim(),
        ssh_port: Number(values.storage_ssh_port || 22),
        login_username: String(values.storage_login_username || '').trim(),
        auth_type: values.storage_auth_type,
        login_password: values.storage_auth_type === 'password' ? String(values.storage_login_password || '') : '',
        ssh_private_key_path: values.storage_auth_type === 'ssh_key' ? String(values.storage_ssh_private_key_path || '').trim() : '',
      }
      const res: any = await injectionApi.testStorageFullConnection(payload)
      setStorageConnectionResult(res?.data || null)
      if (res?.data?.success) {
        message.success('连接测试通过')
      }
    } catch (e: any) {
      if (!e?.errorFields) {
        setStorageConnectionResult(null)
      }
    } finally {
      setStorageConnectionLoading(false)
    }
  }

  const handlePermissionConnectionTest = async () => {
    try {
      const values = await actionForm.validateFields([
        'permission_target_ip',
        'permission_ssh_port',
        'permission_login_username',
        'permission_auth_type',
        ...(selectedPermissionAuthType === 'ssh_key' ? ['permission_ssh_private_key_path'] : ['permission_login_password']),
      ])
      setPermissionConnectionLoading(true)
      const payload = {
        target_ip: String(values.permission_target_ip || '').trim(),
        ssh_port: Number(values.permission_ssh_port || 22),
        login_username: String(values.permission_login_username || '').trim(),
        auth_type: values.permission_auth_type,
        login_password: values.permission_auth_type === 'password' ? String(values.permission_login_password || '') : '',
        ssh_private_key_path: values.permission_auth_type === 'ssh_key' ? String(values.permission_ssh_private_key_path || '').trim() : '',
      }
      const res: any = await injectionApi.testPermissionErrorConnection(payload)
      setPermissionConnectionResult(res?.data || null)
      if (res?.data?.success) {
        message.success('连接测试通过')
      }
    } catch (e: any) {
      if (!e?.errorFields) {
        setPermissionConnectionResult(null)
      }
    } finally {
      setPermissionConnectionLoading(false)
    }
  }

  const isStorageExecuteReady = () => {
    const values = actionForm.getFieldsValue()
    if (!String(values.storage_target_ip || '').trim()) return false
    if (!String(values.storage_ssh_port || '').trim()) return false
    if (!String(values.storage_login_username || '').trim()) return false
    if (!String(values.storage_auth_type || '').trim()) return false
    if (values.storage_auth_type === 'password' && !String(values.storage_login_password || '').trim()) return false
    if (values.storage_auth_type === 'ssh_key' && !String(values.storage_ssh_private_key_path || '').trim()) return false
    if (!String(values.storage_method || '').trim()) return false
    if (!String(values.storage_location || '').trim()) return false
    if (values.storage_location === 'custom' && !String(values.storage_custom_location || '').trim()) return false
    if (values.storage_size === 'custom') {
      const customSize = Number(values.storage_custom_size)
      if (!Number.isFinite(customSize) || customSize < 1 || customSize > 99) return false
    } else if (![50, 80].includes(Number(values.storage_size))) {
      return false
    }
    return String(values.storage_strategy || '').trim() !== ''
  }

  const isNetworkExecuteReady = () => {
    const values = actionForm.getFieldsValue()
    if (!String(values.network_target_ip || '').trim()) return false
    if (!String(values.network_ssh_port || '').trim()) return false
    if (!String(values.network_login_username || '').trim()) return false
    if (!String(values.network_auth_type || '').trim()) return false
    if (values.network_auth_type === 'password' && !String(values.network_login_password || '').trim()) return false
    if (values.network_auth_type === 'ssh_key' && !String(values.network_ssh_private_key_path || '').trim()) return false
    if (!String(values.network_interface || '').trim()) return false
    if (!String(values.network_type || '').trim()) return false
    if (!String(values.network_duration_seconds || '').trim()) return false
    if (!String(values.network_recovery_strategy || '').trim()) return false
    if (values.network_type === 'packet_loss') {
      return String(values.network_packet_loss_percent || '').trim() !== '' && String(values.network_packet_correlation_percent || '').trim() !== ''
    }
    if (values.network_type === 'latency') {
      return String(values.network_latency_ms || '').trim() !== '' && String(values.network_latency_jitter_ms || '').trim() !== ''
    }
    return true
  }

  const isPermissionExecuteReady = () => {
    const values = actionForm.getFieldsValue()
    if (!String(values.permission_target_ip || '').trim()) return false
    if (!String(values.permission_ssh_port || '').trim()) return false
    if (!String(values.permission_login_username || '').trim()) return false
    if (!String(values.permission_auth_type || '').trim()) return false
    if (values.permission_auth_type === 'password' && !String(values.permission_login_password || '').trim()) return false
    if (values.permission_auth_type === 'ssh_key' && !String(values.permission_ssh_private_key_path || '').trim()) return false
    if (!permissionConnectionResult?.success) return false
    const targetPath = String(values.permission_target_path || '').trim()
    if (!targetPath.startsWith('/')) return false
    if (!String(values.permission_change_type || '').trim()) return false
    if (!String(values.permission_duration_mode || '').trim()) return false
    if (values.permission_duration_mode === 'custom') {
      const customDuration = Number(values.permission_duration_custom)
      if (!Number.isFinite(customDuration) || customDuration < 1 || customDuration > 86400) return false
    }
    return String(values.permission_recovery_strategy || '').trim() !== ''
  }

  const storageExecuteReady = useMemo(() => isStorageExecuteReady(), [
    actionForm,
    storageTargetIp,
    storageLoginUsername,
    selectedStorageAuthType,
    storageLoginPassword,
    storageSshPrivateKeyPath,
    selectedStorageLocation,
    storageCustomLocation,
    selectedStorageSize,
    storageCustomSize,
  ])

  const networkExecuteReady = useMemo(() => isNetworkExecuteReady(), [
    actionForm,
    networkTargetIp,
    networkLoginUsername,
    selectedNetworkAuthType,
    networkLoginPassword,
    networkSshPrivateKeyPath,
    networkInterfaceValue,
    selectedNetworkType,
    networkDurationSecondsValue,
    networkRecoveryStrategy,
    networkPacketLossPercentValue,
    networkPacketCorrelationPercentValue,
    networkLatencyMsValue,
    networkLatencyJitterMsValue,
  ])

  const permissionExecuteReady = useMemo(() => isPermissionExecuteReady(), [
    actionForm,
    permissionConnectionResult,
    permissionTargetIp,
    permissionLoginUsername,
    selectedPermissionAuthType,
    permissionLoginPassword,
    permissionSshPrivateKeyPath,
    permissionTargetPath,
    permissionChangeType,
    permissionRootProtect,
    permissionDurationMode,
    permissionDurationCustom,
    permissionRecoveryStrategy,
  ])

  const renderConnectionResultPanel = (result: any, loginUser: string, mode: 'storage' | 'network' | 'permission' = 'network') => {
    if (!result) return null
    const commandChecks = result?.checks?.command_checks || {}
    const systemInfo = result?.checks?.system_info || {}
    const rows = mode === 'permission'
      ? [
          `SSH 登录权限：${loginUser || '-'} (${result?.checks?.auth_ok ? 'OK' : 'FAIL'})`,
          `chmod 命令：${commandChecks?.chmod ? '已安装' : '缺失'}`,
          `chattr 命令：${commandChecks?.chattr ? '已安装' : '缺失'}`,
          `lsattr 命令：${commandChecks?.lsattr ? '已安装' : '缺失'}`,
          `系统内核：${systemInfo?.kernel || '-'}`,
          `sudo 提权：${commandChecks?.sudo ? '免密通过' : '未通过'}`,
        ]
      : [
          `SSH 登录权限：${loginUser || '-'} (${result?.checks?.auth_ok ? 'OK' : 'FAIL'})`,
          `tc 工具链（iproute2）：${commandChecks?.tc ? '已安装' : '缺失'}`,
          `iptables 防火墙：${commandChecks?.iptables ? '已安装' : '缺失'}`,
          `at 守护进程：${commandChecks?.at ? '运行可用' : '不可用'}`,
          `系统内核：${systemInfo?.kernel || '-'}`,
          `sudo 提权：${commandChecks?.sudo ? '免密通过' : '未通过'}`,
        ]
    return (
      <div style={{ marginBottom: 20, border: '1px solid #D9DDEA', borderRadius: 8, background: '#F7F9FC', padding: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10, color: '#1F2937', fontWeight: 600 }}>
          <span>系统环境自检</span>
          <span style={{ color: result.success ? '#19B67A' : '#F54B4B' }}>
            {result.success ? '自检通过，无阻塞性风险' : '自检未通过'}
          </span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 20px', fontSize: 12, color: '#059669' }}>
          {rows.map((item) => (
            <div key={item}>{item}</div>
          ))}
        </div>
      </div>
    )
  }

  const formatMonitorDuration = (seconds: number) => {
    if (!Number.isFinite(seconds) || seconds <= 0) return '-'
    if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} 分钟`
    return `${seconds} 秒`
  }

  const closeMonitorDrawer = () => {
    setIsMonitorOpen(false)
    setMonitorRunId(null)
    setMonitorDetail(null)
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }

  const renderMonitorConsole = () => {
    const normalizedType = normalizeInjectionType(monitorDetail?.type)
    const detailConfig = typeof monitorDetail?.config === 'string'
      ? parseConfig(monitorDetail.config)
      : (monitorDetail?.config || {})
    const statusMeta = statusMap[monitorDetail?.exec_status ?? 1] || { color: 'processing', text: '进行中' }
    const canRecover = Boolean(monitorDetail?.can_recover) && (monitorDetail?.exec_status ?? 1) === 1
    let title = '异常注入实时监控台'
    let accentColor = '#7C8CFF'
    let accentShadow = 'rgba(124,140,255,0.16)'
    let actionText = ''
    let fallbackLog = '等待执行日志...'
    let cards: Array<{ label: string; value: any }> = []

    if (normalizedType === 'power_off') {
      const durationSeconds = Number(detailConfig.duration_seconds || detailConfig.duration || 0)
      title = '断电模拟实时监控台'
      accentColor = '#FF7A45'
      accentShadow = 'rgba(255,122,69,0.16)'
      actionText = '紧急恢复上电'
      cards = [
        { label: '执行目标', value: monitorDetail?.target || '-' },
        { label: '控制串口', value: monitorDetail?.power_port || detailConfig.power_port || '-' },
        { label: '持续时长', value: formatMonitorDuration(durationSeconds) },
      ]
    } else if (normalizedType === 'network_error') {
      const networkType = detailConfig.type === 'full' ? 'disconnect' : (detailConfig.type || monitorDetail?.network_type)
      const durationSeconds = Number(detailConfig.duration_seconds || detailConfig.duration || 30)
      const recoveryStrategy = detailConfig.recovery_strategy || detailConfig.strategy || monitorDetail?.recovery_strategy || 'auto'
      title = '网络中断实时监控台'
      accentColor = '#5FD3B3'
      accentShadow = 'rgba(95,211,179,0.16)'
      actionText = '紧急恢复网络'
      cards = [
        { label: '目标IP', value: detailConfig.target_ip || monitorDetail?.target || '-' },
        { label: '作用网卡', value: monitorDetail?.network_interface || detailConfig.network_interface || '-' },
        { label: '中断类型', value: networkType === 'disconnect' ? '完全中断' : networkType === 'packet_loss' ? '高丢包率' : '高延迟' },
        { label: '持续时长', value: formatMonitorDuration(durationSeconds) },
        { label: '恢复策略', value: recoveryStrategy === 'manual' ? '手动恢复' : '自动恢复' },
      ]
    } else if (normalizedType === 'storage_full') {
      const storageLocation = detailConfig.location === 'custom'
        ? (detailConfig.custom_location || '/tmp')
        : (detailConfig.location || '/tmp')
      const storageSize = Number(detailConfig.size || 0) > 0 ? `${detailConfig.size}%` : '-'
      title = '存储注入实时监控台'
      accentColor = '#7C8CFF'
      accentShadow = 'rgba(124,140,255,0.16)'
      cards = [
        { label: '目标IP', value: detailConfig.target_ip || monitorDetail?.target || '-' },
        { label: '填充位置', value: storageLocation },
        { label: '填充大小', value: storageSize },
        { label: '恢复策略', value: detailConfig.strategy === 'manual' ? '测试完成后手动清理' : '测试完成后自动清理' },
      ]
    } else if (normalizedType === 'permission_error') {
      const durationSeconds = Number(detailConfig.duration_seconds || detailConfig.duration || 600)
      const changeMeta = permissionChangeMeta[detailConfig.change_type || 'remove_write'] || permissionChangeMeta.remove_write
      title = '权限缺失实时监控台'
      accentColor = '#8B5CF6'
      accentShadow = 'rgba(139,92,246,0.16)'
      actionText = '紧急恢复权限'
      fallbackLog = `等待权限缺失执行日志...\n[PARAM] 故障类型: ${changeMeta.title}\n[PARAM] 作用路径: ${detailConfig.target_path || '-'}`
      cards = [
        { label: '目标IP', value: detailConfig.target_ip || monitorDetail?.target || '-' },
        { label: '作用路径', value: detailConfig.target_path || '-' },
        { label: '缺失类型', value: changeMeta.title },
        { label: '持续时长', value: formatMonitorDuration(durationSeconds) },
      ]
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#F6F7FB' }}>
        <div style={{ padding: '16px 18px', borderBottom: '1px solid #E6E8F2', background: '#fff' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ width: 12, height: 12, borderRadius: '50%', background: accentColor, boxShadow: `0 0 0 6px ${accentShadow}` }} />
            <div style={{ fontSize: 15, fontWeight: 700, color: '#1F2937' }}>{title}</div>
            <Tag color={statusMeta.color} style={{ marginInlineEnd: 0 }}>
              {statusMeta.text}
            </Tag>
          </div>
        </div>
        <div style={{ flex: 1, minHeight: 0, padding: 12, overflow: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            {cards.map((item) => (
              <Card key={item.label} size="small" styles={{ body: { padding: 12 } }} style={{ borderRadius: 10, borderColor: '#E6EAF5' }}>
                <div style={{ fontSize: 12, color: '#7B8194' }}>{item.label}</div>
                <div style={{ marginTop: 6, fontWeight: 600, color: '#1F2937', wordBreak: 'break-all' }}>{item.value}</div>
              </Card>
            ))}
          </div>
          <div style={{ border: '1px solid #D9DDEA', borderRadius: 10, background: '#fff', minHeight: 520, padding: 14 }}>
            <div style={{ color: '#4B5563', fontSize: 12, lineHeight: 1.85, whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>
              {monitorDetail?.result || fallbackLog}
            </div>
          </div>
        </div>
        <div style={{ borderTop: '1px solid #E6E8F2', padding: 14, background: '#fff', display: 'grid', gap: 12 }}>
          {actionText ? (
            <Button
              type="primary"
              danger
              icon={<ThunderboltOutlined />}
              disabled={!canRecover}
              onClick={() => handleEmergencyRecover()}
              style={{ width: '100%', height: 42, borderRadius: 10, boxShadow: 'none' }}
            >
              {actionText}
            </Button>
          ) : null}
          <Button style={{ width: '100%', height: 40, borderRadius: 10 }} onClick={closeMonitorDrawer}>
            关闭监控台
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div style={{ height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <div className="client-page-title">
            <h1>异常注入</h1>
            <p className="client-page-subtitle">模拟断电、存储、网络与权限异常并跟踪恢复状态</p>
          </div>
        </div>
        <Permission code="injection:add">
          <PagePrimaryButton icon={<PlusOutlined />} onClick={() => setIsModalOpen(true)}>新增注入</PagePrimaryButton>
        </Permission>
      </div>

      <div style={{ background: '#fff', borderRadius: 8 }}>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setPage(1) }}
          items={[
            { key: 'scenario', label: '异常场景' },
            { key: 'record', label: '执行记录' },
          ]}
        />

        {activeTab === 'scenario' && (
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            {scenarioCards.map(item => (
              <Col xs={24} sm={12} md={8} lg={6} key={item.id} style={{ display: 'flex' }}>
                <Card 
                  hoverable 
                  styles={{ body: { padding: 20, display: 'flex', flexDirection: 'column', height: '100%' } }}
                  style={{ borderRadius: 8, flex: 1, display: 'flex', flexDirection: 'column' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
                    <div style={{ 
                      width: 32, height: 32, borderRadius: 8, 
                      background: '#F0F5FF', color: '#4045D6', 
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      marginRight: 12
                    }}>
                      {item.type === 'power_off' && <ApiOutlined style={{ fontSize: 16 }} />}
                      {item.type === 'storage_full' && <DatabaseOutlined style={{ fontSize: 16 }} />}
                      {item.type === 'network_error' && <DisconnectOutlined style={{ fontSize: 16 }} />}
                      {item.type === 'permission_error' && <SafetyCertificateOutlined style={{ fontSize: 16 }} />}
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 'bold' }}>{item.title}</div>
                  </div>
                  
                  <div style={{ flex: 1, marginBottom: 20 }}>
                    {getScenarioConfig(item.type, item.config_json)}
                  </div>
                  
                  <div style={{ display: 'flex', gap: 12, marginTop: 'auto' }}>
                    <Button 
                      type="primary" 
                      icon={<CaretRightOutlined />} 
                      onClick={() => openUnifiedModal(item)}
                      style={{ flex: 1 }}
                    >
                      执行
                    </Button>
                  </div>
                </Card>
              </Col>
            ))}
          </Row>
        )}

        {activeTab === 'record' && (
          <>
            <div style={{ marginTop: 16, marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div />
              <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', flexWrap: 'wrap', flex: '1 1 auto' }}>
                <Select
                  style={{ width: 180 }}
                  value={recordTypeFilter}
                  onChange={(val) => { setRecordTypeFilter(val); setPage(1) }}
                  options={[
                    { value: 'all', label: '全部异常' },
                    { value: 'power_off', label: '断电模拟' },
                    { value: 'storage_full', label: '存储注入' },
                    { value: 'network_error', label: '网络中断' },
                    { value: 'permission_error', label: '权限缺失' },
                  ]}
                />
                <Select
                  style={{ width: 150 }}
                  value={statusFilter}
                  onChange={(val) => { setStatusFilter(val); setPage(1) }}
                  options={[
                    { value: 'all', label: '所有状态' },
                    { value: 1, label: '进行中' },
                    { value: 2, label: '完成' },
                    { value: 3, label: '失败' },
                    { value: 4, label: '终止' },
                  ]}
                />
                <Input
                  className="pcids-list-search"
                  placeholder="请输入测试对象/执行人员/任务编号"
                  prefix={<SearchOutlined />}
                  value={keyword}
                  allowClear
                  onChange={(e) => setKeyword(e.target.value)}
                  onPressEnter={handleSearch}
                />
              </div>
            </div>

            <Table
              columns={recordColumns}
              dataSource={dataSource}
              rowKey="id"
              loading={loading}
              tableLayout="fixed"
              scroll={{ x: 980 }}
              pagination={{
                current: page,
                pageSize,
                total,
                onChange: (nextPage) => setPage(nextPage),
                showSizeChanger: false,
                showTotal: (count) =>
                  renderListPaginationTotal(count, pageSize, (size) => {
                    setPage(1)
                    setPageSize(size)
                  }),
              }}
            />
          </>
        )}
      </div>

      <Modal
        title="选择异常类型"
        className="pcids-modal pcids-modal--wide"
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
      >
        <Row gutter={[16, 16]}>
          {scenarioTemplates.map((item) => (
            <Col xs={24} sm={12} key={item.id}>
              <Card hoverable onClick={() => openUnifiedModal(item)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: 8,
                    background: '#F0F5FF', color: '#4045D6',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    {item.type === 'power_off' && <ApiOutlined style={{ fontSize: 16 }} />}
                    {item.type === 'storage_full' && <DatabaseOutlined style={{ fontSize: 16 }} />}
                    {item.type === 'network_error' && <DisconnectOutlined style={{ fontSize: 16 }} />}
                    {item.type === 'permission_error' && <SafetyCertificateOutlined style={{ fontSize: 16 }} />}
                  </div>
                  <div>
                    <div style={{ fontWeight: 'bold' }}>{item.title}</div>
                    <div style={{ color: '#666', fontSize: 12 }}>选择模板后在弹窗中完成配置与执行</div>
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Modal>

      <Modal
        title={(
          <span>{typeMap[injectionType]?.text || '异常注入'}</span>
        )}
        className="pcids-modal pcids-modal--form"
        open={isActionOpen}
        onOk={handleExecute}
        okText="执行"
        cancelText="取消"
        onCancel={() => { setIsActionOpen(false); actionForm.resetFields(); setInjectionType(''); setSelectedScenario(null); setStorageConnectionResult(null); setNetworkConnectionResult(null); setPermissionConnectionResult(null); setNetworkInterfaces([]) }}
        okButtonProps={{ disabled: injectionType === 'network_error' ? !networkExecuteReady : injectionType === 'storage_full' ? !storageExecuteReady : injectionType === 'permission_error' ? !permissionExecuteReady : false }}
      >
        <Form form={actionForm} layout="vertical" requiredMark={false}>
          {!['network_error', 'storage_full', 'permission_error'].includes(injectionType) && (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>选择执行目标</span>} name="target" rules={[{ required: true, message: '请选择执行目标' }]}>
                    <Select placeholder="请选择板卡" options={boards.map((b) => ({ label: b.name, value: b.name }))} />
                  </Form.Item>
                </Col>
                {injectionType === 'power_off' && (
                  <Col span={12}>
                    <Form.Item
                      label={(
                        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
                          <span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>选择控制电源</span>
                          <Button
                            type="link"
                            size="small"
                            icon={<ReloadOutlined />}
                            onClick={scanPowerPorts}
                            loading={scanningPowerPorts}
                            style={{ paddingInline: 0, height: 'auto' }}
                          >
                            扫描串口
                          </Button>
                        </div>
                      )}
                      name="power_port"
                      rules={[{ required: true, message: '请选择控制电源' }]}
                    >
                      <Select
                        placeholder="请选择已识别的 DPS1816S 电源串口"
                        options={powerPortOptions}
                        notFoundContent={scanningPowerPorts ? '扫描中...' : '暂无可用电源串口'}
                      />
                    </Form.Item>
                  </Col>
                )}
              </Row>
              <Divider style={{ margin: '8px 0 20px' }} />
            </>
          )}

          {injectionType === 'storage_full' && (
            <>
              <div style={{ marginBottom: 12, padding: '10px 14px', border: '1px solid #F1D394', borderRadius: 8, background: '#FFF9E8', color: '#B26A00', fontSize: 12, lineHeight: 1.7 }}>
                目标设备前置要求：运行 Linux 系统，已开放 SSH 并允许当前平台登录；具备 `sudo` 提权及文件写入权限，建议预先确认目标磁盘剩余空间。
              </div>
              <div style={{ fontWeight: 700, marginBottom: 16 }}>目标设备与认证</div>
              <Row gutter={16}>
                <Col span={16}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>目标IP地址</span>} name="storage_target_ip" rules={[{ required: true, message: '请输入目标IP地址' }]}>
                    <Input placeholder="192.168.1.105" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>SSH端口</span>} name="storage_ssh_port" rules={[{ required: true, message: '请输入SSH端口' }]}>
                    <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={16}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>登录用户名</span>} name="storage_login_username" rules={[{ required: true, message: '请输入登录用户名' }]}>
                    <Input placeholder="root" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>认证方式</span>} name="storage_auth_type" rules={[{ required: true, message: '请选择认证方式' }]}>
                    <Select options={[{ label: '密码认证', value: 'password' }, { label: 'SSH密钥认证', value: 'ssh_key' }]} />
                  </Form.Item>
                </Col>
                <Col span={24}>
                  {selectedStorageAuthType === 'ssh_key' ? (
                    <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>SSH私钥路径</span>} name="storage_ssh_private_key_path" rules={[{ required: true, message: '请输入SSH私钥路径' }]}>
                      <Input placeholder="/root/.ssh/id_rsa" />
                    </Form.Item>
                  ) : (
                    <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>登录密码</span>} name="storage_login_password" rules={[{ required: true, message: '请输入登录密码' }]}>
                      <Input.Password placeholder="请输入 SSH 登录密码" />
                    </Form.Item>
                  )}
                </Col>
              </Row>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12, marginBottom: 16 }}>
                <Button type="primary" ghost loading={storageConnectionLoading} onClick={handleStorageConnectionTest}>连接测试</Button>
                {storageConnectionResult && (
                  <div style={{ color: storageConnectionResult.success ? '#19B67A' : '#F54B4B', fontWeight: 600 }}>
                    {storageConnectionResult.success ? '测试通过' : '测试失败'}
                  </div>
                )}
              </div>
              {renderConnectionResultPanel(storageConnectionResult, storageLoginUsername)}
              <Divider style={{ margin: '8px 0 20px' }} />
            </>
          )}

          {injectionType === 'network_error' && (
            <>
              <div style={{ marginBottom: 12, padding: '10px 14px', border: '1px solid #F1D394', borderRadius: 8, background: '#FFF9E8', color: '#B26A00', fontSize: 12, lineHeight: 1.7 }}>
                目标设备前置要求：运行 Linux 系统；已开放 SSH 并允许当前平台登录；具备 `sudo` 提权能力，并确保系统已安装 `iptables`。
              </div>
              <div style={{ fontWeight: 700, marginBottom: 16 }}>目标设备与认证</div>
              <Row gutter={16}>
                <Col span={16}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>目标IP地址</span>} name="network_target_ip" rules={[{ required: true, message: '请输入目标IP地址' }]}>
                    <Input placeholder="192.168.1.105" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>SSH端口</span>} name="network_ssh_port" rules={[{ required: true, message: '请输入SSH端口' }]}>
                    <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={16}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>登录用户名</span>} name="network_login_username" rules={[{ required: true, message: '请输入登录用户名' }]}>
                    <Input placeholder="root" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>认证方式</span>} name="network_auth_type" rules={[{ required: true, message: '请选择认证方式' }]}>
                    <Select options={[{ label: '密码认证', value: 'password' }, { label: 'SSH密钥认证', value: 'ssh_key' }]} />
                  </Form.Item>
                </Col>
                <Col span={24}>
                  {selectedNetworkAuthType === 'ssh_key' ? (
                    <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>SSH私钥路径</span>} name="network_ssh_private_key_path" rules={[{ required: true, message: '请输入SSH私钥路径' }]}>
                      <Input placeholder="/root/.ssh/id_rsa" />
                    </Form.Item>
                  ) : (
                    <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>登录密码</span>} name="network_login_password" rules={[{ required: true, message: '请输入登录密码' }]}>
                      <Input.Password placeholder="请输入 SSH 登录密码" />
                    </Form.Item>
                  )}
                </Col>
              </Row>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12, marginBottom: 16 }}>
                <Button type="primary" ghost loading={networkConnectionLoading} onClick={handleNetworkConnectionTest}>连接测试</Button>
                {networkConnectionResult && (
                  <div style={{ color: networkConnectionResult.success ? '#19B67A' : '#F54B4B', fontWeight: 600 }}>
                    {networkConnectionResult.success ? '测试通过' : '测试失败'}
                  </div>
                )}
              </div>
              {renderConnectionResultPanel(networkConnectionResult, networkLoginUsername)}
              <Divider style={{ margin: '8px 0 20px' }} />
            </>
          )}

          {injectionType === 'permission_error' && (
            <>
              <div style={{ marginBottom: 12, padding: '10px 14px', border: '1px solid #F1D394', borderRadius: 8, background: '#FFF9E8', color: '#B26A00', fontSize: 12, lineHeight: 1.7 }}>
                目标设备前置要求：运行 Linux 系统；已开放 SSH 并允许当前平台登录；具备 sudo 或目标路径的变更权限；系统已安装 `chmod`/`chattr` 等基础命令。
              </div>
              <div style={{ fontWeight: 700, marginBottom: 16 }}>目标设备与认证</div>
              <Row gutter={16}>
                <Col span={16}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>目标IP地址</span>} name="permission_target_ip" rules={[{ required: true, message: '请输入目标IP地址' }]}>
                    <Input placeholder="192.168.1.105" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>SSH端口</span>} name="permission_ssh_port" rules={[{ required: true, message: '请输入SSH端口' }]}>
                    <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={16}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>登录用户名</span>} name="permission_login_username" rules={[{ required: true, message: '请输入登录用户名' }]}>
                    <Input placeholder="root" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>认证方式</span>} name="permission_auth_type" rules={[{ required: true, message: '请选择认证方式' }]}>
                    <Select options={[{ label: '密码认证', value: 'password' }, { label: 'SSH密钥认证', value: 'ssh_key' }]} />
                  </Form.Item>
                </Col>
                <Col span={24}>
                  {selectedPermissionAuthType === 'ssh_key' ? (
                    <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>SSH私钥路径</span>} name="permission_ssh_private_key_path" rules={[{ required: true, message: '请输入SSH私钥路径' }]}>
                      <Input placeholder="/root/.ssh/id_rsa" />
                    </Form.Item>
                  ) : (
                    <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>登录密码</span>} name="permission_login_password" rules={[{ required: true, message: '请输入登录密码' }]}>
                      <Input.Password placeholder="请输入 SSH 登录密码" />
                    </Form.Item>
                  )}
                </Col>
              </Row>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12, marginBottom: 16 }}>
                <Button type="primary" ghost loading={permissionConnectionLoading} onClick={handlePermissionConnectionTest}>连接测试</Button>
                {permissionConnectionResult && (
                  <div style={{ color: permissionConnectionResult.success ? '#19B67A' : '#F54B4B', fontWeight: 600 }}>
                    {permissionConnectionResult.success ? '测试通过' : '测试失败'}
                  </div>
                )}
              </div>
              {renderConnectionResultPanel(permissionConnectionResult, permissionLoginUsername, 'permission')}
              <Divider style={{ margin: '8px 0 20px' }} />
            </>
          )}
          {injectionType === 'power_off' && (
            <>
              <div style={{ fontWeight: 700, marginBottom: 16, color: '#243057' }}>参数配置</div>
              <div style={{ marginBottom: 20 }}>
                <div style={{ marginBottom: 12, fontWeight: 600, color: '#4D5A82' }}>持续时间</div>
                <div style={{ display: 'flex', alignItems: 'center', columnGap: 10, rowGap: 8, flexWrap: 'wrap' }}>
                  <Form.Item name="power_duration" style={{ marginBottom: 0 }}>
                    <Radio.Group>
                      <Radio value={5}>5秒</Radio>
                      <Radio value={10}>10秒</Radio>
                      <Radio value={30}>30秒</Radio>
                      <Radio value="custom">自定义</Radio>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item name="power_duration_custom" style={{ marginBottom: 0 }} rules={selectedDurationMode === 'custom' ? [{ required: true, message: '请输入持续时间' }] : []}>
                    <InputNumber min={1} max={3600} style={{ width: 80 }} disabled={selectedDurationMode !== 'custom'} />
                  </Form.Item>
                </div>
              </div>
              <div style={{ marginBottom: 12, fontWeight: 600, color: '#4D5A82' }}>恢复策略</div>
              <Form.Item name="power_strategy" style={{ marginBottom: 0 }}>
                <Radio.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <Radio value="auto">自动恢复供电（推荐）</Radio>
                  <Radio value="manual">保持断电状态，手动恢复</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}

          {injectionType === 'storage_full' && (
            <>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>参数配置</div>
              <Form.Item label="填充方式" name="storage_method" style={{ marginBottom: 16 }} rules={[{ required: true, message: '请选择填充方式' }]}>
                <Radio.Group>
                  <Radio value="single">创建单个大文件</Radio>
                  <Radio value="multi">创建多个小文件</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item label="填充位置" name="storage_location" style={{ marginBottom: 16 }} rules={[{ required: true, message: '请选择填充位置' }]}>
                <Radio.Group>
                  <Radio value="/tmp">/tmp</Radio>
                  <Radio value="/var/tmp">/var/tmp</Radio>
                  <Radio value="custom">自定义路径</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item noStyle shouldUpdate>
                {() => actionForm.getFieldValue('storage_location') === 'custom' ? (
                  <Form.Item name="storage_custom_location" style={{ marginTop: -6, marginBottom: 16 }} rules={[{ required: true, message: '请输入自定义路径' }]}>
                    <Input placeholder="/path/to/dir" />
                  </Form.Item>
                ) : null}
              </Form.Item>
              <Form.Item label="填充大小" name="storage_size" style={{ marginBottom: 24 }} rules={[{ required: true, message: '请选择填充大小' }]}>
                <Radio.Group>
                  <Radio value={50}>50%可用空间</Radio>
                  <Radio value={80}>80%可用空间</Radio>
                  <Radio value="custom">自定义</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item noStyle shouldUpdate>
                {() => actionForm.getFieldValue('storage_size') === 'custom' ? (
                  <Form.Item
                    name="storage_custom_size"
                    style={{ marginTop: -12, marginBottom: 16 }}
                    rules={[
                      { required: true, message: '请输入填充大小' },
                      {
                        validator: (_, value) => {
                          const size = Number(value)
                          if (!Number.isFinite(size) || size < 1 || size > 99) {
                            return Promise.reject(new Error('填充大小需在 1-99 之间'))
                          }
                          return Promise.resolve()
                        },
                      },
                    ]}
                  >
                    <InputNumber min={1} max={99} style={{ width: '100%' }} />
                  </Form.Item>
                ) : null}
              </Form.Item>
              <div style={{ fontWeight: 'bold', marginBottom: 16 }}>恢复策略</div>
              <Form.Item name="storage_strategy">
                <Radio.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <Radio value="auto">测试完成后自动清理临时文件</Radio>
                  <Radio value="manual">保留临时文件（需手动清理）</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}

          {injectionType === 'network_error' && (
            <>
              <div style={{ fontWeight: 700, marginBottom: 16 }}>参数配置</div>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>作用网卡</span>} name="network_interface" rules={[{ required: true, message: '请选择作用网卡' }]}>
                    <Select placeholder={networkInterfaces.length ? '请选择作用网卡' : '请先执行连接测试'} disabled={!networkInterfaces.length} options={networkInterfaces.map((item) => ({ label: item, value: item }))} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>持续时长</span>} name="network_duration_seconds" rules={[{ required: true, message: '请输入持续时长' }]}>
                    <InputNumber min={1} max={3600} style={{ width: '100%' }} addonAfter="秒" />
                  </Form.Item>
                </Col>
              </Row>
              <div style={{ fontWeight: 700, marginBottom: 12 }}>中断类型</div>
              <Form.Item name="network_type" rules={[{ required: true, message: '请选择中断类型' }]} style={{ marginBottom: 0 }}>
                <Radio.Group className="injection-full-width-radio-group" style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <Radio value="disconnect" style={{ width: '100%', marginInlineEnd: 0, display: 'flex', alignItems: 'flex-start' }}>
                    <div style={{ flex: 1, minWidth: 0, border: selectedNetworkType === 'disconnect' ? '1px solid #4F63FF' : '1px solid #D9DDEA', borderRadius: 10, padding: '14px 16px', background: selectedNetworkType === 'disconnect' ? '#EEF1FF' : '#fff' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                        <span style={{ fontWeight: 700, color: '#1F2937' }}>完全中断网络</span>
                        <Tag color="error" style={{ marginInlineEnd: 0 }}>默认</Tag>
                      </div>
                      <div style={{ color: '#7B8194', fontSize: 12 }}>后台默认采用安全边界模式：阻断除 SSH 管理端口外的全部业务流量，无需额外参数。</div>
                    </div>
                  </Radio>
                </Radio.Group>
              </Form.Item>
              <div style={{ marginTop: 20, marginBottom: 12, fontWeight: 700 }}>恢复策略</div>
              <Form.Item name="network_recovery_strategy" style={{ marginBottom: 0 }} rules={[{ required: true, message: '请选择恢复策略' }]}>
                <Radio.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <Radio value="auto">自动恢复网络（推荐）</Radio>
                  <Radio value="manual">保持中断状态，手动恢复</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}

          {injectionType === 'permission_error' && (
            <>
              <div style={{ fontWeight: 700, marginBottom: 16 }}>参数配置</div>
              <Form.Item
                label={<span><span style={{ color: '#FF4D4F', marginRight: 4 }}>*</span>作用路径</span>}
                name="permission_target_path"
                rules={[
                  { required: true, message: '请输入绝对路径' },
                  {
                    validator: (_, value) => {
                      if (!String(value || '').trim().startsWith('/')) {
                        return Promise.reject(new Error('请输入以 / 开头的绝对路径'))
                      }
                      return Promise.resolve()
                    },
                  },
                ]}
              >
                <Input placeholder="请输入目标文件或文件夹的绝对路径 (如 /opt/app/data)" />
              </Form.Item>

              <div style={{ marginBottom: 12, fontWeight: 700 }}>缺失类型</div>
              <Form.Item name="permission_change_type" rules={[{ required: true, message: '请选择缺失类型' }]} style={{ marginBottom: 20 }}>
                <Radio.Group className="injection-full-width-radio-group" style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {Object.entries(permissionChangeMeta).map(([value, meta]) => (
                    <Radio key={value} value={value} style={{ width: '100%', marginInlineEnd: 0, display: 'flex', alignItems: 'flex-start' }}>
                      <div style={{ flex: 1, minWidth: 0, border: permissionChangeType === value ? '1px solid #4F63FF' : '1px solid #D9DDEA', borderRadius: 10, padding: '14px 16px', background: permissionChangeType === value ? '#EEF1FF' : '#fff' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                          <span style={{ fontWeight: 700, color: '#1F2937' }}>{meta.title}</span>
                          {meta.badge ? <Tag color={value === 'remove_write' ? 'error' : 'success'} style={{ marginInlineEnd: 0 }}>{meta.badge}</Tag> : null}
                        </div>
                        <div style={{ color: '#7B8194', fontSize: 12 }}>{meta.desc}</div>
                        {value === 'remove_write' && permissionChangeType === 'remove_write' && (
                          <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 8, border: '1px solid #D9DDEA', background: '#fff' }}>
                            <Form.Item name="permission_root_protect" valuePropName="checked" noStyle>
                              <Checkbox>针对 Root 生效（需使用 root 登录或免密 sudo；将验证 Root 无法写入）</Checkbox>
                            </Form.Item>
                          </div>
                        )}
                      </div>
                    </Radio>
                  ))}
                </Radio.Group>
              </Form.Item>

              <div style={{ marginBottom: 12, fontWeight: 700 }}>持续时长</div>
              <Row gutter={12} align="middle" style={{ marginBottom: 20 }}>
                <Col flex="auto">
                  <Form.Item name="permission_duration_mode" style={{ marginBottom: 0 }}>
                    <Radio.Group>
                      <Radio value={300}>5 分钟</Radio>
                      <Radio value={600}>10 分钟</Radio>
                      <Radio value="custom">自定义</Radio>
                    </Radio.Group>
                  </Form.Item>
                </Col>
                <Col flex="130px">
                  <Form.Item name="permission_duration_custom" style={{ marginBottom: 0 }} rules={permissionDurationMode === 'custom' ? [{ required: true, message: '请输入持续时长' }] : []}>
                    <InputNumber min={1} max={86400} style={{ width: '100%' }} disabled={permissionDurationMode !== 'custom'} addonAfter="秒" />
                  </Form.Item>
                </Col>
              </Row>

              <div style={{ marginBottom: 12, fontWeight: 700 }}>恢复策略</div>
              <Form.Item name="permission_recovery_strategy" style={{ marginBottom: 0 }}>
                <Radio.Group>
                  <Radio value="auto">自动恢复</Radio>
                  <Radio value="manual">手动恢复</Radio>
                </Radio.Group>
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>

      <Drawer
        title={null}
        placement="right"
        open={isMonitorOpen}
        width={420}
        destroyOnHidden
        closeIcon={<CloseOutlined />}
        onClose={closeMonitorDrawer}
        styles={{ body: { padding: 0 } }}
      >
        {renderMonitorConsole()}
      </Drawer>

      <Modal
        title="注入详情"
        className="pcids-modal pcids-modal--compact"
        open={isDetailOpen}
        onCancel={() => { setIsDetailOpen(false); setDetailRecord(null); setSelectedRecord(null) }}
        footer={null}
      >
        {selectedRecord && (
          <div>
            <p><strong>类型：</strong>{typeMap[normalizeInjectionType(selectedRecord.type)]?.text || selectedRecord.type}</p>
            <p><strong>目标：</strong>{selectedRecord.target}</p>
            <p><strong>状态：</strong>{statusMap[selectedRecord.exec_status ?? selectedRecord.status]?.text}</p>
            <p><strong>结果：</strong>{detailRecord?.result || selectedRecord.result || '-'}</p>
            {normalizeInjectionType(selectedRecord.type) === 'power_off' && (
              <>
                <p><strong>控制串口：</strong>{detailRecord?.power_port || '-'}</p>
                <p><strong>恢复策略：</strong>{detailRecord?.recovery_strategy === 'manual' ? '手动恢复' : '自动恢复'}</p>
                <Button
                  type="primary"
                  danger
                  icon={<ThunderboltOutlined />}
                  disabled={!detailRecord?.can_recover}
                  loading={detailLoading}
                  onClick={() => handleEmergencyRecover(selectedRecord.id, 'detail')}
                  style={{ width: '100%', marginBottom: 16 }}
                >
                  紧急恢复上电
                </Button>
              </>
            )}
            {normalizeInjectionType(selectedRecord.type) === 'network_error' && (
              <>
                <p><strong>作用网卡：</strong>{detailRecord?.network_interface || '-'}</p>
                <p><strong>中断类型：</strong>{detailRecord?.network_type === 'disconnect' ? '完全中断网络' : detailRecord?.network_type === 'packet_loss' ? '高丢包率' : '高延迟'}</p>
                <p><strong>恢复策略：</strong>{detailRecord?.recovery_strategy === 'manual' ? '手动恢复' : '自动恢复'}</p>
                <p><strong>SSH端口：</strong>{detailRecord?.ssh_port || '-'}</p>
                <Button
                  type="primary"
                  danger
                  icon={<ThunderboltOutlined />}
                  disabled={!detailRecord?.can_recover}
                  loading={detailLoading}
                  onClick={() => handleEmergencyRecover(selectedRecord.id, 'detail')}
                  style={{ width: '100%', marginBottom: 16 }}
                >
                  紧急恢复网络
                </Button>
              </>
            )}
            {normalizeInjectionType(selectedRecord.type) === 'permission_error' && (
              <>
                <p><strong>作用路径：</strong>{detailRecord?.target_path || '-'}</p>
                <p><strong>缺失类型：</strong>{detailRecord?.change_type === 'remove_exec' ? '移除执行权限' : detailRecord?.change_type === 'remove_read' ? '移除读权限' : '移除写权限'}</p>
                <p><strong>恢复策略：</strong>{detailRecord?.recovery_strategy === 'manual' ? '手动恢复' : '自动恢复'}</p>
                <Button
                  type="primary"
                  danger
                  icon={<ThunderboltOutlined />}
                  disabled={!detailRecord?.can_recover}
                  loading={detailLoading}
                  onClick={() => handleEmergencyRecover(selectedRecord.id, 'detail')}
                  style={{ width: '100%', marginBottom: 16 }}
                >
                  紧急恢复权限
                </Button>
              </>
            )}
            {selectedRecord.config && (
              <p><strong>配置：</strong><pre>{selectedRecord.config}</pre></p>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}

export default Injection
