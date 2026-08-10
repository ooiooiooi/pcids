import { useEffect, useMemo, useRef, useState } from 'react'
import {
  App as AntdApp,
  AutoComplete,
  Badge,
  Button,
  Checkbox,
  Col,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
} from 'antd'
import {
  DeleteOutlined,
  DownOutlined,
  DisconnectOutlined,
  FilterFilled,
  LinkOutlined,
  SearchOutlined,
  SendOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { productApi, protocolTestApi } from '../../services/api'
import { consumeBackendServiceError } from '../../services/backendErrorCenter'
import { API_BASE_URL } from '../../services/backendRuntime'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime, formatDateTimeWithMs, formatTimeWithMs } from '../../utils/dateTime'
import { ActionButtonGroup, ActionLinkButton } from '../../components/ActionButton'
import UserIdentity from '../../components/UserIdentity'
import { Permission } from '../../hooks'
import {
  canFdAllowedLengths,
  canFdLengthToDlc,
  canFrameIdValidator,
  canLengthValidator,
  filterProtocolTrafficLogs,
  getEthernetConfigurationError,
  getProtocolFormSyncKey,
  ethernetPayloadValidator,
  ipValidator,
  mergeProtocolConnectionConfig,
  targetIpValidator,
  validateCanPayloadConsistency,
  shouldHydrateProtocolFormFromSession,
} from './formUtils'
import ActionConfirm from '../../components/ActionConfirm'
import EllipsisText from '../../components/EllipsisText'

type ProtocolKind = 'can' | 'canfd' | 'serial' | 'ethernet'
type ModuleKind = ProtocolKind | 'gpio_io'
type ModuleView = 'protocol' | 'gpio'
type GpioDebugTab = 'single' | 'batch'
type GpioBatchRow = {
  key: string
  pin: string
  selected: boolean
  mode: '输出' | '输入'
  target_level: '高电平' | '低电平'
  expected_level: '高电平' | '低电平' | '不判定'
  current_level: string
  result: string
}

const gpioModuleKey: ModuleKind = 'gpio_io'
const createDefaultGpioBatchRows = (): GpioBatchRow[] =>
  Array.from({ length: 16 }, (_, index) => ({
    key: `GPIO${index}`,
    pin: `GPIO${index}`,
    selected: true,
    mode: index % 2 === 0 ? '输出' : '输入',
    target_level: '低电平',
    expected_level: '不判定',
    current_level: '-',
    result: '-',
  }))

const protocolSubTabs: Array<{ key: ProtocolKind; label: string; hint: string }> = [
  { key: 'can', label: 'CAN协议', hint: 'USB-CAN 适配器' },
  { key: 'canfd', label: 'CAN FD协议', hint: '高速 CAN FD 通道' },
  { key: 'serial', label: '串口', hint: '自动探测串口设备' },
  { key: 'ethernet', label: '以太网', hint: '本机网络通道' },
]
const gpioModeOptions = [
  { label: '输出', value: '输出' },
  { label: '输入 (单次读取)', value: '输入 (单次读取)' },
]
const gpioLevelOptions = [
  { label: '高电平', value: '高电平' },
  { label: '低电平', value: '低电平' },
]
const ACTIVE_PROTOCOL_SESSION_STORAGE_KEY = 'pcids-active-protocol-session'
const gpioExpectedLevelOptions = [
  { label: '高电平', value: '高电平' },
  { label: '低电平', value: '低电平' },
  { label: '不判定', value: '不判定' },
]

const canBaudRateOptions = ['125kbps', '250kbps', '500kbps', '1Mbps'].map((value) => ({ label: value, value }))
const canFdDataBaudRateOptions = ['1Mbps', '2Mbps', '4Mbps', '5Mbps', '8Mbps'].map((value) => ({ label: value, value }))
const canDlcOptions = [0, 1, 2, 3, 4, 5, 6, 7, 8].map((value) => ({ label: value.toString(), value }))
const canFdDlcOptions = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64].map((value) => ({ label: value.toString(), value }))
const yesNoOptions = [
  { label: '是', value: true },
  { label: '否', value: false },
]
const canFdStandardOptions = [
  { label: 'CAN FD ISO', value: false },
  { label: 'CAN FD 非ISO', value: true },
]
const serialBaudRateOptions = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600].map((value) => ({ label: `${value}`, value }))
const ethernetTransportOptions = ['TCP Client', 'TCP Server', 'UDP'].map((value) => ({ label: value, value }))
const compactFormItemStyle: React.CSSProperties = { marginBottom: 10 }
const fullWidthStyle: React.CSSProperties = { width: '100%' }

const normalizeModuleKind = (value: any): ModuleKind => {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'gpio' || normalized === 'gpio_io' || normalized === 'gpio-io') return 'gpio_io'
  if (normalized === 'canfd') return 'canfd'
  if (normalized === 'serial') return 'serial'
  if (normalized === 'ethernet') return 'ethernet'
  return 'can'
}

const normalizeEthernetMode = (value: any) => {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'tcp' || normalized === 'tcp client' || normalized === 'tcp_client') return 'TCP Client'
  if (normalized === 'tcp server' || normalized === 'tcp_server') return 'TCP Server'
  if (normalized === 'udp') return 'UDP'
  return 'TCP Client'
}

const sortLogsNewestFirst = (logs: any[]) =>
  [...logs].sort((left, right) => {
    const timeDiff = dayjs(right?.timestamp).valueOf() - dayjs(left?.timestamp).valueOf()
    if (Number.isFinite(timeDiff) && timeDiff !== 0) return timeDiff
    return Number(right?.id || 0) - Number(left?.id || 0)
  })

const firstFilled = (...values: any[]) => {
  for (const value of values) {
    if (value === null || value === undefined) continue
    const text = String(value).trim()
    if (text) return text
  }
  return ''
}

const pickConnectionConfig = (protocol: ModuleKind, values: Record<string, any>) => {
  if (protocol === 'can') {
    return {
      backend_key: values.backend_key,
      adapter_key: values.adapter_key,
      com_port: values.com_port,
      adapter_device: values.com_port,
      physical_channel: values.physical_channel,
      channel: values.physical_channel,
      baud_rate: values.baud_rate,
      bitrate: values.baud_rate,
      id_format: values.id_format,
      frame_format: values.id_format,
      remote_frame: !!values.remote_frame,
      termination_enabled: !!values.termination_enabled,
      data_length: values.data_length,
      dlc: values.data_length,
    }
  }
  if (protocol === 'canfd') {
    return {
      adapter_key: values.adapter_key,
      physical_channel: values.physical_channel,
      channel: values.physical_channel,
      arb_baud_rate: values.arb_baud_rate,
      arb_bitrate: values.arb_baud_rate,
      data_baud_rate: values.brs ? values.data_baud_rate : values.arb_baud_rate,
      data_bitrate: values.brs ? values.data_baud_rate : values.arb_baud_rate,
      id_format: values.id_format,
      frame_format: values.id_format,
      brs: values.brs !== false,
      termination_enabled: !!values.termination_enabled,
      canfd_non_iso: !!values.canfd_non_iso,
      data_length: values.data_length,
      dlc: canFdLengthToDlc(Number(values.data_length ?? 0)),
    }
  }
  if (protocol === 'serial') {
    return {
      com_port: values.com_port,
      baud_rate: values.baud_rate,
      auto_append_crlf: !!values.auto_append_crlf,
      length_bytes: values.length_bytes,
      data_bits: values.data_bits,
      stop_bits: values.stop_bits,
      parity: values.parity,
      flow_control: values.flow_control,
    }
  }
  if (protocol === 'ethernet') {
    return {
      transport_protocol: normalizeEthernetMode(values.protocol),
      protocol: normalizeEthernetMode(values.protocol),
      local_ip: values.local_ip,
      target_ip: values.target_ip,
      target_port: values.target_port,
      local_port: values.local_port,
      listen_port: values.listen_port,
      timeout: values.timeout,
    }
  }
  if (protocol === gpioModuleKey) {
    const selectedWchPort = String(values.wch_serial_port || values.com_port || '').trim()
    return {
      pin: values.pin,
      mode: values.mode,
      target_level: values.target_level,
      pull_mode: values.pull_mode,
      expected_level: values.expected_level,
      current_level: values.current_level,
      trigger_type: values.trigger_type,
      timeout_ms: values.timeout_ms,
      wch_serial_port: selectedWchPort,
      com_port: selectedWchPort,
      gpio_transport_kind: 'serial',
      gpio_transport_config: selectedWchPort
        ? {
            kind: 'serial',
            com_port: selectedWchPort,
            baud_rate: 115200,
            data_type: 'ASCII',
            data_bits: 8,
            stop_bits: 1,
            parity: 'NONE',
            flow_control: 'NONE',
          }
        : undefined,
    }
  }
  return {}
}

const getConnectionValidationFields = (protocol: ModuleKind, ethernetMode?: string) => {
  if (protocol === 'can') {
    return ['adapter_key', 'physical_channel', 'baud_rate', 'id_format', 'data_length', 'termination_enabled']
  }
  if (protocol === 'canfd') {
    return ['adapter_key', 'physical_channel', 'arb_baud_rate', 'brs', 'data_baud_rate', 'id_format', 'data_length', 'termination_enabled']
  }
  if (protocol === 'serial') {
    return ['com_port', 'baud_rate', 'length_bytes', 'data_bits', 'stop_bits', 'parity', 'flow_control', 'auto_append_crlf']
  }
  if (protocol === 'ethernet') {
    const mode = normalizeEthernetMode(ethernetMode)
    if (mode === 'TCP Server') return ['protocol', 'local_ip', 'listen_port', 'timeout']
    if (mode === 'UDP') return ['protocol', 'local_ip', 'local_port', 'target_ip', 'target_port', 'timeout']
    return ['protocol', 'target_ip', 'target_port', 'timeout']
  }
  if (protocol === gpioModuleKey) {
    return ['wch_serial_port', 'pin', 'mode', 'target_level', 'pull_mode', 'expected_level', 'current_level', 'trigger_type', 'timeout_ms']
  }
  return []
}

const mergeConnectionConfig = (
  protocol: ModuleKind,
  responseConfigInput: any,
  requestedConfigInput: any,
  ethernetLocalIpOptions: Array<{ label: string; value: string }> = [],
) => mergeProtocolConnectionConfig({ protocol, responseConfigInput, requestedConfigInput, ethernetLocalIpOptions })

const protocolSnapshotLabelMap: Record<string, string> = {
  method: '连接方式',
  transport_protocol: '传输协议',
  local_ip: '本地IP',
  local_ip_options: '本地IP候选',
  channel_options: '通道候选',
  target_ip: '目标IP',
  target_port: '目标端口',
  listen_port: '监听端口',
  local_port: '本地端口',
  timeout: '超时时间(ms)',
  timeout_ms: '超时时间(ms)',
  protocol: '协议模式',
  data_type: '数据类型',
  remote_ip: '远端IP',
  remote_port: '远端端口',
  validation_result: '验证结果',
  validation_detail: '验证详情',
  validation_code: '验证代码',
  reply_frame_received: '已收到回复',
  validated_at: '验证时间',
  payload_length: '数据长度',
  channel: '通道',
  physical_channel: '物理通道',
  physical_channel_options: '物理通道候选',
  adapter_key: '适配器',
  adapter_serial: '适配器序列号',
  adapter_options: '适配器候选',
  adapter_name: '适配器名称',
  adapter_device: '适配器设备',
  adapter_source: '适配来源',
  detected_devices: '探测设备',
  probe_summary: '探测摘要',
  com_port: '串口号',
  baud_rate: '波特率',
  auto_append_crlf: '自动追加换行',
  length_bytes: '长度(Bytes)',
  data_bits: '数据位',
  stop_bits: '停止位',
  parity: '校验位',
  flow_control: '流控制',
  frame_id: '帧ID',
  dlc: '数据长度(Bytes)',
  data_length: '数据长度(Bytes)',
  bitrate: '波特率',
  arb_baud_rate: '仲裁段波特率',
  arb_bitrate: '仲裁段波特率',
  data_baud_rate: '数据段波特率',
  data_bitrate: '数据段波特率',
  brs: 'CANFD加速',
  canfd_non_iso: 'CANFD标准',
  id_format: '标识符格式',
  frame_format: '帧格式',
  remote_frame: '远程帧',
  termination_enabled: '内部120Ω终端电阻',
  pin: '引脚',
  mode: '模式',
  action: '动作',
  target_level: '目标电平',
  pull_mode: '上下拉',
  expected_level: '期望电平',
  current_level: '当前电平',
  trigger_type: '触发方式',
  supports_readback: '支持回读',
}

const positiveIntegerValidator = (label: string) => (_: any, value: any) => {
  if (value === undefined || value === null || value === '') return Promise.reject(new Error(`请输入${label}`))
  if (!Number.isInteger(Number(value)) || Number(value) <= 0) return Promise.reject(new Error(`${label}必须为正整数`))
  return Promise.resolve()
}
const portValidator = (_: any, value: any) => {
  if (value === undefined || value === null || value === '') return Promise.reject(new Error('请输入端口'))
  if (!Number.isInteger(Number(value)) || Number(value) < 1 || Number(value) > 65535) return Promise.reject(new Error('端口范围必须为 1-65535'))
  return Promise.resolve()
}
const ethernetTimeoutValidator = (_: any, value: any) => {
  if (!Number.isInteger(Number(value)) || Number(value) < 100 || Number(value) > 120000) {
    return Promise.reject(new Error('超时时间必须在 100-120000ms 范围内'))
  }
  return Promise.resolve()
}
const protocolStyleText = `
.pcids-protocol-switch {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pcids-protocol-switch__item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  border: none;
  background: #f3f4f7;
  color: #5c6475;
  border-radius: 14px;
  padding: 12px 16px;
  cursor: pointer;
  transition: all .2s ease;
  box-shadow: inset 0 0 0 1px rgba(31, 42, 92, 0.04);
}
.pcids-protocol-switch__item:hover {
  background: #e8ecf8;
  color: #30406f;
}
.pcids-protocol-switch__item--active {
  background: linear-gradient(180deg, #5f72ff 0%, #4b5ee9 100%);
  color: #fff;
  box-shadow: 0 10px 24px rgba(76, 86, 233, 0.24);
}
.pcids-protocol-switch__item--disabled,
.pcids-protocol-switch__item--disabled:hover {
  background: #f5f5f5;
  color: #b1b7c4;
  cursor: not-allowed;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.04);
}
.pcids-protocol-switch__hint {
  font-size: 12px;
  opacity: .72;
}
.pcids-segment {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 999px;
  background: #efefef;
}
.pcids-segment__btn {
  border: none;
  background: transparent;
  color: #6f6f74;
  padding: 4px 10px;
  border-radius: 999px;
  cursor: pointer;
  transition: all .2s ease;
  font-size: 12px;
  line-height: 1;
}
.pcids-segment__btn:hover {
  color: #4c56e9;
}
.pcids-segment__btn--active {
  background: linear-gradient(180deg, #5f72ff 0%, #4b5ee9 100%);
  color: #fff;
  box-shadow: 0 4px 12px rgba(76, 86, 233, 0.3);
}
.pcids-payload-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: auto;
}
.pcids-payload-label__text {
  line-height: 1.2;
}
.pcids-module-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 4px;
  border-radius: 999px;
  background: #f3f4f7;
}
.pcids-module-switch__btn {
  border: none;
  background: transparent;
  color: #596277;
  border-radius: 999px;
  min-height: 32px;
  padding: 0 18px;
  font-size: 13px;
  cursor: pointer;
}
.pcids-module-switch__btn:disabled {
  cursor: not-allowed;
  opacity: .56;
}
.pcids-module-switch__btn--active {
  background: #fff;
  color: #1f2a5c;
  box-shadow: 0 6px 18px rgba(31, 42, 92, 0.08);
}
.pcids-protocol-workspace {
  display: grid;
  grid-template-columns: 188px minmax(460px, 500px) minmax(520px, 1fr);
  gap: 18px;
  min-height: 0;
  flex: 1;
}
.pcids-protocol-workspace--gpio {
  grid-template-columns: minmax(560px, .9fr) minmax(520px, 1.1fr);
}
.pcids-protocol-panel {
  min-width: 0;
  min-height: 0;
  border: 1px solid #edf0f7;
  border-radius: 8px;
  background: #fff;
}
.pcids-protocol-panel--config {
  padding: 14px 18px;
  background: #fbfcff;
}
.pcids-protocol-panel--log {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
@media (max-width: 1500px) {
  .pcids-protocol-workspace {
    grid-template-columns: 160px minmax(360px, .95fr) minmax(400px, 1.05fr);
    gap: 12px;
  }
  .pcids-protocol-workspace--gpio {
    grid-template-columns: minmax(500px, 1fr) minmax(400px, 1fr);
  }
  .pcids-protocol-panel--config {
    padding: 12px;
  }
}
.pcids-protocol-config-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  min-width: 0;
}
.pcids-protocol-config-title {
  flex: 1 1 auto;
  min-width: 0;
}
.pcids-protocol-config-title h3 {
  margin: 0;
  color: #1f2a5c;
  font-size: 16px;
  line-height: 24px;
}
.pcids-protocol-config-title div {
  margin-top: 6px;
  color: #7d89b0;
  font-size: 12px;
  line-height: 18px;
}
.pcids-channel-toolbar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  max-width: 100%;
}
.pcids-channel-toolbar .ant-btn {
  height: 32px;
  padding-inline: 12px;
}
.pcids-channel-toolbar__status {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #e5e9f2;
  border-radius: 999px;
  background: #fff;
}
.pcids-protocol-target-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 4px 0 16px;
}
.pcids-protocol-target-row__main {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
}
.pcids-protocol-target-row__switch {
  flex: 0 0 auto;
}
.pcids-protocol-main-tabs .ant-tabs-nav {
  margin-bottom: 0;
}
.pcids-protocol-target-row .ant-badge-status-text {
  color: #7d8798;
}
.pcids-protocol-log-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid #edf0f7;
  background: #fbfcff;
}
.pcids-protocol-log-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #1f2a5c;
  font-size: 16px;
  font-weight: 700;
}
.pcids-protocol-log-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  height: 0;
}
.pcids-live-log {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #fff;
}
.pcids-live-log__header {
  display: grid;
  grid-template-columns: 138px 92px 120px 100px 90px minmax(220px, 1fr);
  gap: 0;
  border-bottom: 1px solid #ececec;
  background: #fafafa;
}
.pcids-live-log__head {
  padding: 12px 10px;
  font-size: 13px;
  font-weight: 600;
  color: #4b5567;
}
.pcids-live-log__body {
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  max-height: 560px;
}
.pcids-live-log__row {
  display: grid;
  grid-template-columns: 138px 92px 120px 100px 90px minmax(220px, 1fr);
  border-bottom: 1px solid #f0f0f0;
}
.pcids-live-log__row--anomaly {
  background: #fff2f0;
}
.pcids-live-log__cell {
  padding: 12px 10px;
  font-size: 13px;
  color: #2f3640;
  word-break: break-all;
}
.pcids-live-log__cell--anomaly {
  color: #cf1322;
  font-weight: 600;
}
.pcids-live-log__cell--muted {
  color: #9aa3b2;
}
.pcids-live-log__tag {
  display: inline-flex;
  align-items: center;
  font-weight: 600;
}
.pcids-live-log__tag--system {
  color: #6b7280;
}
.pcids-live-log__tag--action {
  color: #3b82f6;
}
.pcids-live-log__tag--event {
  color: #16a34a;
}
.pcids-live-log__level--high {
  color: #16a34a;
  font-weight: 600;
}
.pcids-live-log__level--low {
  color: #ef4444;
  font-weight: 600;
}
.pcids-gpio-wch {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #e6efff;
  border-radius: 8px;
  background: #f8fbff;
}
.pcids-gpio-wch__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.pcids-gpio-wch__title {
  color: #1f2a5c;
  font-size: 13px;
  font-weight: 700;
}
.pcids-gpio-wch__hint {
  margin-top: 2px;
  color: #697386;
  font-size: 12px;
}
.pcids-gpio-wch__selected {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-top: 10px;
  padding: 7px 8px;
  border: 1px solid #eef2f7;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
}
.pcids-gpio-wch__port {
  color: #1d4ed8;
  font-weight: 700;
  white-space: nowrap;
}
.pcids-gpio-wch__chip {
  color: #15803d;
  white-space: nowrap;
}
.pcids-gpio-wch__desc {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: #374151;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pcids-gpio-wch__serial {
  color: #86909c;
  white-space: nowrap;
}
.pcids-gpio-tabs {
  margin-top: 12px;
}
.pcids-gpio-tabs .ant-tabs-nav {
  margin-bottom: 12px;
}
.pcids-gpio-batch {
  min-width: 0;
}
.pcids-gpio-batch__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.pcids-gpio-batch__table {
  overflow: auto;
  border-radius: 8px;
  border: 1px solid #eef1f6;
  background: #fff;
  max-height: calc(100vh - 430px);
}
.pcids-gpio-batch__head,
.pcids-gpio-batch__row {
  display: grid;
  grid-template-columns: 58px 92px 112px 126px 112px 96px;
  min-width: 596px;
  align-items: center;
}
.pcids-gpio-batch__head {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #fafafa;
  color: #1f2329;
  font-weight: 600;
  border-bottom: 1px solid #eef1f6;
}
.pcids-gpio-batch__head > div,
.pcids-gpio-batch__row > div {
  padding: 9px 12px;
  min-width: 0;
}
.pcids-gpio-batch__head-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.pcids-gpio-batch__head-cell--select {
  gap: 6px;
}
.pcids-gpio-batch__head-label {
  line-height: 1.2;
}
.pcids-gpio-batch__head-trigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: none;
  background: transparent;
  color: #1f2329;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  cursor: pointer;
}
.pcids-gpio-batch__head-trigger:hover {
  color: #165dff;
}
.pcids-gpio-batch__head-trigger-text {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pcids-gpio-batch__head-trigger-icon {
  color: #b7b7b7;
  font-size: 15px;
}
.pcids-gpio-batch__head-check {
  display: inline-flex;
  align-items: center;
  min-height: 16px;
  flex: 0 0 auto;
}
.pcids-gpio-batch__preset-panel {
  width: 180px;
  padding: 8px;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(31, 42, 92, 0.14);
}
.pcids-gpio-batch__preset-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 6px;
  border-radius: 6px;
}
.pcids-gpio-batch__preset-option:hover {
  background: #f5f8ff;
}
.pcids-gpio-batch__preset-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #eef1f6;
}
.pcids-gpio-batch__row {
  border-bottom: 1px solid #f0f2f5;
}
.pcids-gpio-batch__row:last-child {
  border-bottom: none;
}
.pcids-gpio-batch__muted {
  color: #86909c;
}
.pcids-gpio-batch__pass {
  color: #16a34a;
}
.pcids-gpio-batch__fail {
  color: #cf1322;
}
.pcids-gpio-batch__pass::before,
.pcids-gpio-batch__fail::before {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 6px;
  border-radius: 50%;
  vertical-align: middle;
}
.pcids-gpio-batch__pass::before {
  background: #2ac769;
}
.pcids-gpio-batch__fail::before {
  background: #ff4d4f;
}
.pcids-detail-log-table .ant-table-thead > tr > th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #fafafa;
}
.pcids-detail-log-table .ant-table-tbody > tr.pcids-detail-log-table__row--anomaly > td {
  background: #fff2f0;
}
.pcids-detail-log-table .ant-table-tbody > tr.pcids-detail-log-table__row--anomaly:hover > td {
  background: #fff1f0 !important;
}
.pcids-inline-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid #e5e9f2;
  border-radius: 8px;
  background: #fff;
}
.pcids-inline-option__label {
  color: #4b5567;
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
}
.pcids-inline-switch-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
  padding-right: 6px;
}
`

const getDefaultProtocolFormValues = (protocol: ModuleKind) => {
  switch (protocol) {
    case 'can':
      return {
        channel: '',
        backend_key: '',
        adapter_key: '',
        com_port: '',
        physical_channel: '',
        baud_rate: '500kbps',
        id_format: '标准帧(11位)',
        remote_frame: false,
        termination_enabled: false,
        data_length: 8,
        frame_id: '',
        data: '',
      }
    case 'canfd':
      return {
        adapter_key: '',
        physical_channel: '',
        arb_baud_rate: '500kbps',
        data_baud_rate: '2Mbps',
        id_format: '标准帧(11位)',
        brs: false,
        termination_enabled: true,
        canfd_non_iso: false,
        data_length: 8,
        frame_id: '',
        data: '',
      }
    case 'serial':
      return {
        com_port: '',
        baud_rate: 115200,
        auto_append_crlf: false,
        length_bytes: 64,
        data_bits: 8,
        stop_bits: 1,
        parity: 'NONE',
        flow_control: 'NONE',
        data: '',
      }
    case 'ethernet':
      return {
        protocol: 'TCP Client',
        local_ip: '',
        target_ip: '',
        target_port: 8080,
        local_port: 8080,
        listen_port: 8080,
        timeout: 3000,
        data: '',
      }
    case 'gpio_io':
      return {
        pin: 'GPIO0',
        wch_serial_port: '',
        com_port: '',
        mode: '输出',
        target_level: '高电平',
        pull_mode: '无 (浮空)',
        expected_level: '高电平',
        current_level: '',
        trigger_type: '上升沿',
        timeout_ms: 5000,
      }
    default:
      return {}
  }
}

const parseJsonConfig = (value: any) => {
  if (!value) return {}
  if (typeof value === 'object') return value
  try {
    const parsed = JSON.parse(String(value))
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

const normalizeProtocolFormValues = (protocol: ModuleKind, configInput: any) => {
  const config = parseJsonConfig(configInput)
  const defaults = getDefaultProtocolFormValues(protocol)
  switch (protocol) {
    case 'can':
      return {
        ...defaults,
        channel: config.physical_channel || config.channel || defaults.channel,
        backend_key: config.backend_key || defaults.backend_key,
        adapter_key: config.adapter_key || defaults.adapter_key,
        com_port: config.com_port || config.adapter_device || defaults.com_port,
        physical_channel: config.physical_channel || config.channel || defaults.physical_channel,
        baud_rate: config.baud_rate || config.bitrate || defaults.baud_rate,
        id_format: config.id_format || config.frame_format || defaults.id_format,
        remote_frame: typeof config.remote_frame === 'boolean' ? config.remote_frame : defaults.remote_frame,
        termination_enabled: typeof config.termination_enabled === 'boolean' ? config.termination_enabled : defaults.termination_enabled,
        data_length: config.data_length ?? config.dlc ?? defaults.data_length,
      }
    case 'canfd':
      return {
        ...defaults,
        adapter_key: config.adapter_key || defaults.adapter_key,
        physical_channel: config.physical_channel || config.channel || defaults.physical_channel,
        arb_baud_rate: config.arb_baud_rate || config.arb_bitrate || defaults.arb_baud_rate,
        data_baud_rate: config.data_baud_rate || config.data_bitrate || defaults.data_baud_rate,
        id_format: config.id_format || config.frame_format || defaults.id_format,
        brs: typeof config.brs === 'boolean' ? config.brs : defaults.brs,
        termination_enabled: typeof config.termination_enabled === 'boolean' ? config.termination_enabled : defaults.termination_enabled,
        canfd_non_iso: typeof config.canfd_non_iso === 'boolean' ? config.canfd_non_iso : defaults.canfd_non_iso,
        data_length:
          config.data_length ??
          (typeof config.dlc === 'number' && Number.isInteger(config.dlc) && config.dlc >= 0 && config.dlc <= 15
            ? canFdAllowedLengths[config.dlc] ?? defaults.data_length
            : defaults.data_length),
      }
    case 'ethernet':
      return {
        ...defaults,
        protocol: normalizeEthernetMode(config.protocol || config.transport_protocol || defaults.protocol),
        local_ip: config.local_ip || defaults.local_ip,
        target_ip: config.target_ip || config.ip || defaults.target_ip,
        target_port: config.target_port || config.port || defaults.target_port,
        local_port: config.local_port || defaults.local_port,
        listen_port: config.listen_port || defaults.listen_port,
        timeout: config.timeout || defaults.timeout,
      }
    case 'gpio_io':
      return {
        ...defaults,
        pin: config.pin || defaults.pin,
        wch_serial_port:
          config.wch_serial_port ||
          config.com_port ||
          config.gpio_transport_config?.com_port ||
          (Array.isArray(config.wch_serial_ports) ? config.wch_serial_ports[0] : '') ||
          defaults.wch_serial_port,
        com_port: config.com_port || config.gpio_transport_config?.com_port || defaults.com_port,
        mode: config.mode || defaults.mode,
        target_level: config.target_level || config.level || defaults.target_level,
        pull_mode: config.pull_mode || defaults.pull_mode,
        expected_level: config.expected_level || defaults.expected_level,
        current_level: config.current_level || '',
        trigger_type: config.trigger_type || config.interrupt || defaults.trigger_type,
        timeout_ms: config.timeout_ms || defaults.timeout_ms,
      }
    default:
      return { ...defaults, ...config }
  }
}

const pickMutableProtocolFormValues = (protocol: ModuleKind, values: Record<string, any>) => {
  switch (protocol) {
    case 'can':
    case 'canfd':
      return {
        frame_id: values.frame_id,
        data: values.data,
      }
    case 'serial':
    case 'ethernet':
      return {
        data: values.data,
      }
    case 'gpio_io':
      return Object.fromEntries(
        Object.entries({
          pin: values.pin,
          wch_serial_port: values.wch_serial_port,
          com_port: values.com_port,
          mode: values.mode,
          target_level: values.target_level,
          pull_mode: values.pull_mode,
          expected_level: values.expected_level,
          current_level: values.current_level,
          trigger_type: values.trigger_type,
          timeout_ms: values.timeout_ms,
        }).filter(([, value]) => value !== undefined),
      )
    default:
      return {}
  }
}

const normalizeProtocolErrorMessage = (messageText: any, fallback: string) => {
  const rawText = String(messageText || '').trim()
  if (!rawText) return fallback
  const normalized = rawText.toLowerCase()
  if (
    normalized.includes('device_busy') ||
    normalized.includes('channel_busy') ||
    normalized.includes('其他程序占用') ||
    normalized.includes('通道已被占用') ||
    (normalized.includes('device_index') && normalized.includes('pnp'))
  ) {
    return '通道被占用，请先关闭其他占用程序后重试'
  }
  return rawText
}

const getErrorMessage = (error: any, fallback: string) =>
  normalizeProtocolErrorMessage(error?.response?.data?.detail || error?.message, fallback)

const readPersistedProtocolSessionId = () => {
  try {
    const raw = sessionStorage.getItem(ACTIVE_PROTOCOL_SESSION_STORAGE_KEY)
    if (!raw) return 0
    const parsed = JSON.parse(raw)
    return Number(parsed?.id || 0)
  } catch {
    return 0
  }
}

const persistProtocolSessionId = (sessionId: number) => {
  if (sessionId > 0) {
    sessionStorage.setItem(ACTIVE_PROTOCOL_SESSION_STORAGE_KEY, JSON.stringify({ id: sessionId }))
  }
}

const clearPersistedProtocolSessionId = () => {
  sessionStorage.removeItem(ACTIVE_PROTOCOL_SESSION_STORAGE_KEY)
}

const disconnectProtocolSessionSilently = async (sessionId: number, keepalive = false) => {
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  try {
    await fetch(`${API_BASE_URL}/protocol-tests/${sessionId}/disconnect`, {
      method: 'POST',
      headers,
      body: '{}',
      keepalive,
    })
  } catch {
    /* ignore */
  }
}

const getFirstFormErrorMessage = (errorInfo: any, fallback = '请检查表单填写内容') => {
  const firstField = Array.isArray(errorInfo?.errorFields)
    ? errorInfo.errorFields.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    : null
  return firstField?.errors?.[0] || fallback
}

const snapshotDateTimeKeys = new Set([
  'validated_at',
  'created_at',
  'updated_at',
  'login_time',
  'operation_time',
])

const ethernetSnapshotFieldOrder: Record<string, string[]> = {
  'TCP Client': ['transport_protocol', 'local_ip', 'target_ip', 'target_port', 'timeout', 'data_type'],
  UDP: ['transport_protocol', 'local_ip', 'local_port', 'target_ip', 'target_port', 'timeout', 'data_type'],
  'TCP Server': ['transport_protocol', 'local_ip', 'listen_port', 'timeout', 'data_type'],
}

const getEthernetSnapshotFieldOrder = (config: Record<string, any>) => {
  const mode = normalizeEthernetMode(config.transport_protocol || config.protocol || config.method)
  return ethernetSnapshotFieldOrder[mode] || ethernetSnapshotFieldOrder['TCP Client']
}

const shouldHideSnapshotField = (config: Record<string, any>, key: string) => {
  const hasEthernetFields = [
    'local_ip',
    'target_ip',
    'target_port',
    'listen_port',
    'local_port',
    'remote_ip',
    'remote_port',
  ].some((field) => field in config)

  if (!hasEthernetFields) return false

  return !getEthernetSnapshotFieldOrder(config).includes(key)
}

const Protocol: React.FC = () => {
  const { message } = AntdApp.useApp()
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)
  const [channelActionLoading, setChannelActionLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [detailLogs, setDetailLogs] = useState<any[]>([])
  const [products, setProducts] = useState<any[]>([])
  const [selectedTarget, setSelectedTarget] = useState<string>('')
  const [currentSession, setCurrentSession] = useState<any>(null)
  const [selectedRecord, setSelectedRecord] = useState<any>(null)
  const [activeTab, setActiveTab] = useState<'test' | 'record'>('test')
  const [detailTab, setDetailTab] = useState<'summary' | 'logs' | 'config'>('summary')
  const [activeModule, setActiveModule] = useState<ModuleView>('protocol')
  const [protocolSubTab, setProtocolSubTab] = useState<ProtocolKind>('can')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [recordProtocolFilter, setRecordProtocolFilter] = useState<string>('all')
  const [recordExecutorFilter, setRecordExecutorFilter] = useState<string>('all')
  const [scannedChannelConfigs, setScannedChannelConfigs] = useState<Partial<Record<ModuleKind, Record<string, any>>>>({})
  const [channelScanLoadingMap, setChannelScanLoadingMap] = useState<Partial<Record<ModuleKind, boolean>>>({})
  const [executorOptions, setExecutorOptions] = useState<string[]>([])
  const [txCount, setTxCount] = useState(0)
  const [rxCount, setRxCount] = useState(0)
  const [dataType, setDataType] = useState<'HEX' | 'ASCII'>('HEX')
  const [deletingRecordId, setDeletingRecordId] = useState<number | null>(null)
  const [gpioDebugTab, setGpioDebugTab] = useState<GpioDebugTab>('single')
  const [gpioBatchRows, setGpioBatchRows] = useState<GpioBatchRow[]>(() => createDefaultGpioBatchRows())
  const [gpioBatchLoading, setGpioBatchLoading] = useState(false)
  const [gpioBatchModePreset, setGpioBatchModePreset] = useState<'输出' | '输入' | undefined>('输出')
  const [gpioBatchLevelPreset, setGpioBatchLevelPreset] = useState<'高电平' | '低电平' | undefined>('低电平')
  const [gpioBatchModeDraft, setGpioBatchModeDraft] = useState<'输出' | '输入' | undefined>('输出')
  const [gpioBatchLevelDraft, setGpioBatchLevelDraft] = useState<'高电平' | '低电平' | undefined>('低电平')
  const [gpioBatchModeDropdownOpen, setGpioBatchModeDropdownOpen] = useState(false)
  const [gpioBatchLevelDropdownOpen, setGpioBatchLevelDropdownOpen] = useState(false)
  const [protocolForm] = Form.useForm()
  const canFdBrsEnabled = Form.useWatch('brs', protocolForm)
  const canRemoteFrameEnabled = Boolean(Form.useWatch('remote_frame', protocolForm))
  const ethernetProtocolMode = normalizeEthernetMode(Form.useWatch('protocol', protocolForm))
  const gpioMode = String(Form.useWatch('mode', protocolForm) || '输出')
  const selectedWchSerialPort = String(Form.useWatch('wch_serial_port', protocolForm) || '').trim()
  const gpioModeRef = useRef('输出')
  const currentSessionRef = useRef<any>(null)
  const unloadDisconnectSessionIdRef = useRef<number | null>(null)

  const cleanupPersistedProtocolSession = async () => {
    const sessionId = readPersistedProtocolSessionId()
    if (sessionId <= 0) return
    try {
      await protocolTestApi.disconnect(sessionId)
    } catch {
      /* ignore stale disconnect errors */
    } finally {
      clearPersistedProtocolSessionId()
    }
  }

  const canColumns = useMemo(
    () => [
      { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 180 },
      { title: '方向', dataIndex: 'direction', key: 'direction', width: 90 },
      { title: '帧ID', dataIndex: 'frame_id', key: 'frame_id', width: 120 },
      { title: '数据长度(Bytes)', dataIndex: 'dlc', key: 'dlc', width: 120 },
      { title: '数据(DATA)', dataIndex: 'data', key: 'data' },
    ],
    [],
  )

  const canFdColumns = useMemo(
    () => [
      { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 180 },
      { title: '方向', dataIndex: 'direction', key: 'direction', width: 90 },
      { title: '帧ID', dataIndex: 'frame_id', key: 'frame_id', width: 120 },
      { title: '数据长度(Bytes)', dataIndex: 'dlc', key: 'dlc', width: 120 },
      { title: '数据(DATA)', dataIndex: 'data', key: 'data' },
    ],
    [],
  )

  const serialColumns = useMemo(
    () => [
      { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 180 },
      { title: '方向', dataIndex: 'direction', key: 'direction', width: 90 },
      { title: '长度(Bytes)', dataIndex: 'dlc', key: 'dlc', width: 110 },
      { title: '数据(Hex/ASCII)', dataIndex: 'data', key: 'data' },
    ],
    [],
  )

  const ethernetColumns = useMemo(
    () => [
      { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 180 },
      { title: '方向', dataIndex: 'direction', key: 'direction', width: 90 },
      { title: '源地址', dataIndex: 'src_addr', key: 'src_addr', width: 140 },
      { title: '目标地址', dataIndex: 'dst_addr', key: 'dst_addr', width: 140 },
      { title: '协议', dataIndex: 'protocol', key: 'protocol', width: 90 },
      { title: '端口', dataIndex: 'port', key: 'port', width: 80 },
      { title: '数据(DATA)', dataIndex: 'data', key: 'data', render: (value: string) => <EllipsisText value={value} /> },
    ],
    [],
  )

  const gpioColumns = useMemo(
    () => [
      { title: '时间戳', dataIndex: 'timestamp', key: 'timestamp', width: 180 },
      { title: '方向', dataIndex: 'direction', key: 'direction', width: 90 },
      { title: '引脚', dataIndex: 'frame_id', key: 'frame_id', width: 120 },
      { title: '模式', dataIndex: 'mode', key: 'mode', width: 90 },
      { title: '电平', dataIndex: 'level', key: 'level', width: 90 },
      { title: '说明', dataIndex: 'data', key: 'data' },
    ],
    [],
  )

  const protocolLabelMap: Record<string, string> = {
    can: 'CAN',
    canfd: 'CAN FD',
    serial: '串口',
    ethernet: '以太网',
    gpio: 'GPIO物理引脚',
    gpio_io: 'GPIO物理引脚',
  }

  const protocolTagStyleMap: Record<string, React.CSSProperties> = {
    can: { background: '#e9ccff', color: '#8f4af5' },
    canfd: { background: '#cfe0ff', color: '#3e83ff' },
    serial: { background: '#d9f4df', color: '#27ae60' },
    ethernet: { background: '#cdefff', color: '#2f90e8' },
    gpio: { background: '#ffe9a8', color: '#d79b00' },
    gpio_io: { background: '#ffe9a8', color: '#d79b00' },
  }

  const renderExecutorBadge = (value: string) => {
    const text = String(value || '').trim()
    return <UserIdentity fallbackName={text || '-'} avatarSize={23} />
  }

  const recordColumns = [
    {
      title: (
        <span>任务编号</span>
      ),
      dataIndex: 'task_no',
      key: 'task_no',
      width: 130,
      render: (value: string) => value || '-',
    },
    { title: '测试对象', dataIndex: 'target', key: 'target', width: 180 },
    {
      title: '执行人员',
      dataIndex: 'executor',
      key: 'executor',
      width: 130,
      render: (value: string) => renderExecutorBadge(value),
    },
    {
      title: '类型',
      dataIndex: 'protocol',
      key: 'protocol',
      width: 100,
      render: (value: string) => {
        const key = String(value || '').toLowerCase()
        const style = protocolTagStyleMap[key] || { background: '#f0f0f0', color: '#666' }
        const label = protocolLabelMap[key] || value || '-'
        return (
          <span style={{ ...style, display: 'inline-block', padding: '2px 10px', borderRadius: 8, lineHeight: '20px', whiteSpace: 'pre-line' }}>
            {label}
          </span>
        )
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (value: string) => {
        if (!value) return '-'
        const text = formatDateTime(value)
        return text.replace(' ', '\n')
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 170,
      fixed: 'right' as const,
      render: (_: any, record: any) => (
        <ActionButtonGroup compact>
          <ActionLinkButton onClick={() => openRecordDetail(record.id)}>详情</ActionLinkButton>
          <Permission code="protocol:export">
            <Dropdown
              menu={{
                items: [
                  { key: 'html', label: '导出 HTML' },
                  { key: 'pdf', label: '导出 PDF' },
                ],
                onClick: ({ key }) => {
                  if (key === 'html') handleDownloadReportHtml(record.id)
                  if (key === 'pdf') handleExportPdf(record.id)
                },
              }}
              trigger={['click']}
            >
              <ActionLinkButton>
                导出 <DownOutlined style={{ fontSize: 10 }} />
              </ActionLinkButton>
            </Dropdown>
          </Permission>
          <Permission code="protocol:delete">
            <ActionConfirm
              title="删除执行记录"
              description={`确认删除执行记录 ${record?.task_no || record?.id} 吗？`}
              okText="确认删除"
              cancelText="取消"
              confirmLoading={deletingRecordId === record.id}
              onConfirm={() => handleDeleteRecord(record.id)}
            >
              <ActionLinkButton danger>删除</ActionLinkButton>
            </ActionConfirm>
          </Permission>
        </ActionButtonGroup>
      ),
    },
  ]

  const selectedTargetLabel = useMemo(() => {
    return products.find((item) => String(item.id) === selectedTarget)?.name || selectedTarget || ''
  }, [products, selectedTarget])

  const sessionConfig = useMemo(() => parseJsonConfig(currentSession?.config ?? currentSession?.config_json), [currentSession])
  const isChannelConnected = currentSession?.status === 1
  const currentModuleKind: ModuleKind = activeModule === 'gpio' ? gpioModuleKey : protocolSubTab
  const connectedModuleKind = normalizeModuleKind(currentSession?.protocol)
  const connectedEthernetMode = normalizeEthernetMode(sessionConfig.transport_protocol || sessionConfig.protocol)
  const ethernetChannelState = String(sessionConfig.channel_state || '').trim().toLowerCase()
  const isEthernetServerPeerReady = connectedEthernetMode !== 'TCP Server' || Boolean(sessionConfig.peer_connected)
  const isCurrentProtocolConfigLocked = isChannelConnected && connectedModuleKind === currentModuleKind
  const isEthernetConnectionLocked =
    currentModuleKind === 'ethernet' && isCurrentProtocolConfigLocked
  const isCurrentProtocolSession = Boolean(currentSession?.id) && connectedModuleKind === currentModuleKind
  const displayedLogs = isCurrentProtocolSession ? filterProtocolTrafficLogs(dataSource) : []
  const displayedLogsNewestFirst = useMemo(() => sortLogsNewestFirst(displayedLogs), [displayedLogs])
  const detailLogsNewestFirst = useMemo(() => sortLogsNewestFirst(filterProtocolTrafficLogs(detailLogs)), [detailLogs])
  const displayedTxCount = isCurrentProtocolSession ? txCount : 0
  const displayedRxCount = isCurrentProtocolSession ? rxCount : 0
  const shouldHydrateCurrentProtocolForm = useMemo(
    () =>
      shouldHydrateProtocolFormFromSession({
        currentSessionId: currentSession?.id,
        isChannelConnected,
        connectedModuleKind,
        currentModuleKind,
      }),
    [currentSession?.id, isChannelConnected, connectedModuleKind, currentModuleKind],
  )
  const protocolFormSyncKey = useMemo(
    () =>
      getProtocolFormSyncKey({
        currentSessionId: currentSession?.id,
        isChannelConnected,
        connectedModuleKind,
        currentModuleKind,
      }),
    [currentSession?.id, isChannelConnected, connectedModuleKind, currentModuleKind],
  )
  const operationCount = displayedTxCount
  const eventCount = displayedRxCount
  const currentModuleScannedConfig = useMemo(
    () => parseJsonConfig(scannedChannelConfigs[currentModuleKind]),
    [currentModuleKind, scannedChannelConfigs],
  )
  const currentModuleChannelConfig = shouldHydrateCurrentProtocolForm ? sessionConfig : currentModuleScannedConfig
  const currentModuleChannelScanLoading = Boolean(channelScanLoadingMap[currentModuleKind])
  const gpioWchSerialDevices = useMemo(
    () => (Array.isArray(currentModuleChannelConfig.wch_serial_devices) ? currentModuleChannelConfig.wch_serial_devices : []),
    [currentModuleChannelConfig.wch_serial_devices],
  )
  const selectedWchSerialDevice = useMemo(
    () =>
      gpioWchSerialDevices.find((item: any) => {
        const device = String(item?.device || '').trim()
        return device && device === selectedWchSerialPort
      }),
    [gpioWchSerialDevices, selectedWchSerialPort],
  )
  const gpioWchSerialOptions = useMemo(
    () =>
      gpioWchSerialDevices
        .map((item: any) => {
          const device = String(item?.device || '').trim()
          if (!device) return null
          const chip = String(item?.chip || 'WCH').trim()
          const description = String(item?.description || '').trim()
          return {
            value: device,
            label: description ? `${device} · ${chip} · ${description}` : `${device} · ${chip}`,
          }
        })
        .filter(Boolean) as Array<{ value: string; label: string }>,
    [gpioWchSerialDevices],
  )
  const gpioPinOptions = useMemo(
    () =>
      Array.from({ length: 16 }, (_, index) => ({
        label: `GPIO${index}`,
        value: `GPIO${index}`,
      })),
    [],
  )
  const serialPortOptions = useMemo(() => {
    const ports = Array.isArray(currentModuleChannelConfig.serial_ports) ? currentModuleChannelConfig.serial_ports : []
    const normalized = ports
      .map((item: any) => String(item || '').trim())
      .filter(Boolean)
      .map((item: string) => ({ label: item, value: item }))
    return normalized
  }, [currentModuleChannelConfig.serial_ports])
  const ethernetLocalIpOptions = useMemo(() => {
    const options = Array.isArray(currentModuleChannelConfig.local_ip_options) ? currentModuleChannelConfig.local_ip_options : []
    const values = options
      .map((item: any) => String(item || '').trim())
      .filter(Boolean)
    if (currentModuleChannelConfig.local_ip && !values.includes(String(currentModuleChannelConfig.local_ip))) {
      values.unshift(String(currentModuleChannelConfig.local_ip))
    }
    return values.map((value: string) => ({ label: value, value }))
  }, [currentModuleChannelConfig.local_ip, currentModuleChannelConfig.local_ip_options])
  const canAdapterOptions = useMemo(() => {
    const fromBackend = Array.isArray(currentModuleChannelConfig.adapter_options) ? currentModuleChannelConfig.adapter_options : []
    if (fromBackend.length) {
      return fromBackend.map((item: any) => ({ label: String(item?.label || item?.value || ''), value: String(item?.value || '') })).filter((item: any) => item.value)
    }
    const detected = Array.isArray(currentModuleChannelConfig.detected_devices) ? currentModuleChannelConfig.detected_devices : []
    return detected
      .map((item: any) => {
        const adapterName = String(item?.adapter_name || item?.label || 'CAN 适配器').trim()
        const serialNumber = String(item?.serial_number || '').trim()
        return {
          label: serialNumber ? `${adapterName} / ${serialNumber}` : adapterName,
          value: String(item?.adapter_key || '').trim(),
        }
      })
      .filter((item: any) => item.value)
  }, [currentModuleChannelConfig.adapter_options, currentModuleChannelConfig.detected_devices])
  const selectedCanAdapterKey = Form.useWatch('adapter_key', protocolForm) || currentModuleChannelConfig.adapter_key
  const selectedCanDeviceMeta = useMemo(() => {
    const detectedDevices = Array.isArray(currentModuleChannelConfig.detected_devices) ? currentModuleChannelConfig.detected_devices : []
    if (selectedCanAdapterKey) {
      return detectedDevices.find((item: any) => String(item?.adapter_key || '') === String(selectedCanAdapterKey || '')) || null
    }
    return detectedDevices[0] || null
  }, [currentModuleChannelConfig.detected_devices, selectedCanAdapterKey])
  const canPhysicalChannelOptions = useMemo(() => {
    const channels = Array.isArray(selectedCanDeviceMeta?.channels)
      ? selectedCanDeviceMeta.channels.map((item: any) => String(item?.name || item?.label || '').trim()).filter(Boolean)
      : Array.isArray(currentModuleChannelConfig.physical_channel_options)
        ? currentModuleChannelConfig.physical_channel_options.map((item: any) => String(item || '').trim()).filter(Boolean)
        : []
    const values = [...channels]
    const currentValue = String(currentModuleChannelConfig.physical_channel || currentModuleChannelConfig.channel || '').trim()
    if (currentValue && !values.includes(currentValue)) values.unshift(currentValue)
    return values.map((value: string) => ({ label: value, value }))
  }, [currentModuleChannelConfig.channel, currentModuleChannelConfig.physical_channel, currentModuleChannelConfig.physical_channel_options, selectedCanAdapterKey, selectedCanDeviceMeta])
  const activeChannelText = useMemo(() => {
    if (!(isChannelConnected && connectedModuleKind === currentModuleKind)) return '未建立通道'
    if (currentModuleKind === 'ethernet') {
      if (connectedEthernetMode === 'TCP Server') {
        const listenAddress = `${sessionConfig.local_ip || '-'}:${sessionConfig.listen_port || '-'}`
        return sessionConfig.peer_connected
          ? `客户端已接入 ${sessionConfig.remote_ip || '-'}:${sessionConfig.remote_port || '-'}`
          : `监听中 ${listenAddress}`
      }
      if (connectedEthernetMode === 'UDP') {
        return `已绑定 ${sessionConfig.local_ip || '-'}:${sessionConfig.local_port || '-'}`
      }
      return `已连接 ${sessionConfig.remote_ip || sessionConfig.target_ip || '-'}:${sessionConfig.remote_port || sessionConfig.target_port || '-'}`
    }
    return String(
      sessionConfig.physical_channel ||
      sessionConfig.channel ||
      sessionConfig.com_port ||
      sessionConfig.adapter_name ||
      sessionConfig.local_ip ||
      sessionConfig.pin ||
      '已连接',
    )
  }, [connectedEthernetMode, connectedModuleKind, currentModuleKind, isChannelConnected, sessionConfig])
  const activeChannelColor = useMemo(() => {
    if (!(isChannelConnected && connectedModuleKind === currentModuleKind)) return '#d9d9d9'
    if (currentModuleKind === 'ethernet' && ethernetChannelState === 'listening') return '#faad14'
    if (currentModuleKind === 'ethernet' && ethernetChannelState === 'disconnected') return '#ff4d4f'
    return '#52c41a'
  }, [connectedModuleKind, currentModuleKind, ethernetChannelState, isChannelConnected])
  const getLogColumns = () => {
    switch (currentModuleKind) {
      case 'can':
        return canColumns
      case 'canfd':
        return canFdColumns
      case 'serial':
        return serialColumns
      case 'ethernet':
        return ethernetColumns
      case 'gpio_io':
        return gpioColumns
      default:
        return canColumns
    }
  }

  const formatProtocolDirection = (direction: string) => {
    if (direction === 'Tx') return 'Tx'
    if (direction === 'Rx') return 'Rx'
    return '系统'
  }

  const formatGpioLogKind = (direction: string) => {
    if (direction === 'Tx') return '操作'
    if (direction === 'Rx') return '事件'
    return '系统'
  }

  const getGpioLogPresentation = (log: any, config: Record<string, any> = sessionConfig) => {
    const messageText = String(log?.data || '')
    const configMode = String(config.mode || '-')
    const mode =
      messageText.includes('监听') || String(config.action || '').includes('listen')
        ? '监听'
        : messageText.includes('读取') || String(config.action || '').includes('read')
          ? '输入'
          : messageText.includes('设置') || String(config.action || '').includes('set')
            ? '输出'
            : configMode
    const levelText = messageText.includes('高电平') ? '高电平' : messageText.includes('低电平') ? '低电平' : '-'
    const kind = formatGpioLogKind(String(log?.direction || 'System'))
    return {
      timestamp: formatTimeWithMs(log?.timestamp),
      kind,
      pin: String(log?.frame_id || config.pin || '-'),
      mode,
      level: levelText,
      description: messageText || '-',
    }
  }

  const getEthernetLogPresentation = (log: any, config: Record<string, any>, record?: any) => {
    const transport = normalizeEthernetMode(config.transport_protocol || config.protocol || config.method)
    const localIp = String(config.local_ip || record?.ip_address || '-')
    const remoteIp = String(config.remote_ip || config.target_ip || '-')
    const remotePort = config.remote_port || config.target_port
    const direction = String(log?.direction || 'System')
    const normalizedDirection = direction.toLowerCase()
    const isRx = normalizedDirection === 'rx'
    const isTx = normalizedDirection === 'tx'
    const isTrafficLog = isRx || isTx
    const srcAddr = firstFilled(log?.src_addr, log?.source_addr, log?.source, log?.src)
    const dstAddr = firstFilled(log?.dst_addr, log?.target_addr, log?.destination_addr, log?.dest, log?.dst)
    const protocolText = firstFilled(log?.protocol, transport === 'UDP' ? 'UDP' : 'TCP')
    const portValue = firstFilled(log?.port, log?.src_port, log?.dst_port, isRx ? remotePort : (transport === 'TCP Server' ? remotePort : (config.target_port || remotePort)))
    return {
      ...log,
      direction,
      src_addr: srcAddr || (isTrafficLog ? (isRx ? remoteIp : localIp) : '-'),
      dst_addr: dstAddr || (isTrafficLog ? (isRx ? localIp : remoteIp) : '-'),
      protocol: isTrafficLog ? protocolText : '-',
      port: isTrafficLog ? (portValue || '-') : '-',
    }
  }

  const getProtocolLogMeta = (protocol: string) => {
    const normalized = normalizeModuleKind(protocol)
    if (normalized === gpioModuleKey) {
      return {
        title: '操作日志',
        countText: `统计 操作${operationCount} / 事件${eventCount}`,
        countLabels: { tx: '操作量', rx: '事件量', total: '总量' },
      }
    }
    return {
      title: '通信日志',
      countText: `统计 Tx ${displayedTxCount} / Rx ${displayedRxCount}`,
      countLabels: { tx: '发送帧数 (Tx)', rx: '接收帧数 (Rx)', total: '总帧数' },
    }
  }

  const normalizeLogText = (value: any) => String(value || '').trim().toLowerCase()

const extractPayloadDisplayText = (value: any) => {
  const text = String(value ?? '').trim()
  if (!text) return '-'
  const marker = 'payload='
  const markerIndex = text.toLowerCase().indexOf(marker)
  if (markerIndex < 0) return text
  const payloadText = text.slice(markerIndex + marker.length).trim()
  return payloadText || '-'
}

  const isSuccessLog = (log: any) => {
    const text = normalizeLogText(log?.data)
    if (!text) return false
    return [
      '验证通过',
      '测试通过',
      '已成功',
      '发送成功',
      '成功接收到',
      '读取值与期望值一致',
      '回读值与设定值完全一致',
      '按 api 调用成功退化判定通过',
      '在设定超时时间内成功',
      '在预设超时时间内收到回复',
      'reply frame received',
      'passed',
    ].some((item) => text.includes(item))
  }

  const getAnomalyCount = (logs: any[]) =>
    logs.filter((log: any) => isAnomalyLog(log)).length

  const isAnomalyLog = (log: any) => {
    const text = normalizeLogText(log?.data)
    if (!text || isSuccessLog(log)) return false
    return ['error', 'fail', '异常', '错误', '超时', 'timeout', '未通过', '通道异常', 'nack', 'crc', '总线错误'].some((item) => text.includes(item))
  }

  const formatElapsedDuration = (startValue: any, endValue: any) => {
    const start = dayjs(startValue)
    const end = dayjs(endValue)
    if (!start.isValid() || !end.isValid()) return '-'
    const diffMs = Math.max(end.diff(start, 'millisecond'), 0)
    if (diffMs < 1000) return `${diffMs} 毫秒`
    if (diffMs < 60_000) {
      const seconds = diffMs / 1000
      const formatted = seconds >= 10 ? seconds.toFixed(1) : seconds.toFixed(3)
      return `${formatted.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')} 秒`
    }
    const minutes = Math.floor(diffMs / 60_000)
    const seconds = (diffMs % 60_000) / 1000
    if (seconds === 0) return `${minutes} 分钟`
    const formattedSeconds = seconds >= 10 ? seconds.toFixed(1) : seconds.toFixed(3)
    return `${minutes} 分 ${formattedSeconds.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')} 秒`
  }

  const selectedRecordConfig = useMemo(() => parseJsonConfig(selectedRecord?.config ?? selectedRecord?.config_json), [selectedRecord])
  const selectedRecordLogMeta = getProtocolLogMeta(selectedRecord?.protocol)
  const currentLogMeta = getProtocolLogMeta(currentModuleKind)
  const selectedRecordConfigEntries = useMemo(() => {
    const formatSnapshotValue = (key: string, value: any) =>
      value === null || value === undefined || value === ''
        ? '-'
        : snapshotDateTimeKeys.has(key)
          ? formatDateTime(
              typeof value === 'string' || typeof value === 'number' || value instanceof Date
                ? value
                : String(value),
            )
          : typeof value === 'object'
            ? JSON.stringify(value, null, 2)
            : String(value)
    const hasEthernetFields = ['local_ip', 'target_ip', 'target_port', 'listen_port', 'local_port'].some(
      (field) => field in selectedRecordConfig,
    )
    if (hasEthernetFields) {
      return getEthernetSnapshotFieldOrder(selectedRecordConfig).map((key) => {
        const value =
          key === 'transport_protocol'
            ? selectedRecordConfig.transport_protocol || selectedRecordConfig.protocol || selectedRecordConfig.method
            : selectedRecordConfig[key]
        return {
          key,
          label: protocolSnapshotLabelMap[key] || key.replace(/_/g, ' '),
          value: formatSnapshotValue(key, value),
        }
      })
    }
    return Object.entries(selectedRecordConfig)
      .filter(([key]) => !shouldHideSnapshotField(selectedRecordConfig, key))
      .map(([key, value]) => ({
        key,
        label: protocolSnapshotLabelMap[key] || key.replace(/_/g, ' '),
        value: formatSnapshotValue(key, value),
      }))
  }, [selectedRecordConfig])
  const selectedRecordEndTime = useMemo(() => {
    return (
      selectedRecordConfig?.validated_at ||
      detailLogs[detailLogs.length - 1]?.timestamp ||
      selectedRecord?.updated_at ||
      selectedRecord?.created_at ||
      null
    )
  }, [detailLogs, selectedRecord?.created_at, selectedRecord?.updated_at, selectedRecordConfig])
  const selectedRecordElapsedText = useMemo(
    () => formatElapsedDuration(selectedRecord?.created_at, selectedRecordEndTime),
    [selectedRecord?.created_at, selectedRecordEndTime],
  )

  const renderDetailLogTable = (record: any) => {
    const moduleKind = normalizeModuleKind(record?.protocol)
    if (!detailLogsNewestFirst.length) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" style={{ marginTop: 48 }} />
    }

    if (moduleKind === gpioModuleKey) {
      return (
        <div className="pcids-live-log" style={{ minHeight: 420 }}>
          <div className="pcids-live-log__header">
            {['时间戳', '方向', '引脚', '模式', '电平', '说明'].map((item) => (
              <div key={item} className="pcids-live-log__head">{item}</div>
            ))}
          </div>
          <div className="pcids-live-log__body">
            {detailLogsNewestFirst.map((log: any) => {
              const row = getGpioLogPresentation(log, selectedRecordConfig)
              const isAnomaly = isAnomalyLog(log)
              const kindClass =
                row.kind === '操作'
                  ? 'pcids-live-log__tag pcids-live-log__tag--action'
                  : row.kind === '事件'
                    ? 'pcids-live-log__tag pcids-live-log__tag--event'
                    : 'pcids-live-log__tag pcids-live-log__tag--system'
              const levelClass = isAnomaly ? 'pcids-live-log__cell--anomaly' : row.level === '高电平' ? 'pcids-live-log__level--high' : row.level === '低电平' ? 'pcids-live-log__level--low' : ''
              return (
                <div key={log.id} className={`pcids-live-log__row ${isAnomaly ? 'pcids-live-log__row--anomaly' : ''}`}>
                  <div className="pcids-live-log__cell">{row.timestamp}</div>
                  <div className="pcids-live-log__cell"><span className={kindClass}>{row.kind}</span></div>
                  <div className="pcids-live-log__cell">{row.pin}</div>
                  <div className="pcids-live-log__cell">{row.mode}</div>
                  <div className={`pcids-live-log__cell ${levelClass}`}>{row.level}</div>
                  <div
                    className={[
                      'pcids-live-log__cell',
                      row.kind === '系统' ? 'pcids-live-log__cell--muted' : '',
                      isAnomaly ? 'pcids-live-log__cell--anomaly' : '',
                    ].filter(Boolean).join(' ')}
                  >
                    {row.description}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )
    }

    const baseColumns =
      moduleKind === 'canfd'
        ? canFdColumns
        : moduleKind === 'serial'
          ? serialColumns
          : moduleKind === 'ethernet'
            ? ethernetColumns
            : canColumns
    const detailDataSource =
      moduleKind === 'ethernet'
        ? detailLogsNewestFirst.map((log: any) => getEthernetLogPresentation(log, selectedRecordConfig, record))
        : detailLogsNewestFirst
    const columns = baseColumns.map((col: any) => ({
      ...col,
      render: (text: any, row: any) => {
        const kind = formatProtocolDirection(String(row.direction || 'System'))
        const anomaly = isAnomalyLog(row)
        const color = anomaly ? '#cf1322' : kind === 'Rx' ? '#16a34a' : kind === 'Tx' ? '#3b82f6' : 'rgba(0,0,0,0.45)'
        const value =
          col.key === 'timestamp'
            ? formatDateTimeWithMs(text)
            : col.key === 'direction'
              ? kind
              : col.key === 'data'
                ? extractPayloadDisplayText(text)
              : (text ?? '-')
        return <span style={{ color, fontWeight: anomaly ? 600 : 400, wordBreak: 'break-all' }}>{value}</span>
      },
    }))
    return (
      <Table
        className="pcids-detail-log-table"
        columns={columns}
        dataSource={detailDataSource}
        rowKey="id"
        rowClassName={(row: any) => (isAnomalyLog(row) ? 'pcids-detail-log-table__row--anomaly' : '')}
        pagination={false}
        size="small"
        scroll={{ y: 460, x: 'max-content' }}
        sticky
      />
    )
  }

  const fetchProducts = async () => {
    try {
      const res: any = await productApi.getList({ page: 1, page_size: 100 })
      const rows = res?.data || []
      setProducts(rows)
      if (!selectedTarget && rows[0]?.id) {
        setSelectedTarget(String(rows[0].id))
      }
    } catch {
      /* ignore */
    }
  }

  const fetchSessionLogs = async (sessionId: number, silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res: any = await protocolTestApi.getLogs(sessionId, { page: 1, page_size: 200 })
      if (res?.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
        setTxCount(res.tx || 0)
        setRxCount(res.rx || 0)
        setCurrentSession((prev: any) =>
          prev
            ? {
                ...prev,
                status: res.status,
                tx: res.tx || 0,
                rx: res.rx || 0,
                config: res.config ?? prev.config,
                config_json: res.config_json ?? prev.config_json,
              }
            : prev,
        )
      }
    } catch {
      /* interceptor handles it */
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const fetchRecords = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res: any = await protocolTestApi.getRecords({
        page,
        page_size: pageSize,
        keyword: keyword || undefined,
        protocol: recordProtocolFilter === 'all' ? undefined : recordProtocolFilter,
        executor: recordExecutorFilter === 'all' ? undefined : recordExecutorFilter,
      })
      if (res?.code === 0) {
        setDataSource(res.data || [])
        setTotal(res.total || 0)
        setExecutorOptions(res.executors || [])
      }
    } catch {
      /* interceptor handles it */
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const openRecordDetail = async (recordId: number) => {
    setIsDetailOpen(true)
    setSelectedRecord(null)
    setDetailLogs([])
    setDetailTab('summary')
    setDetailLoading(true)
    try {
      const res: any = await protocolTestApi.getRecordDetail(recordId)
      if (res?.code === 0) {
        setSelectedRecord(res.data)
        const logRes: any = await protocolTestApi.getLogs(recordId, { page: 1, page_size: 200 })
        if (logRes?.code === 0) {
          setDetailLogs(logRes.data || [])
        }
      }
    } catch {
      /* interceptor handles it */
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    fetchProducts()
  }, [])

  useEffect(() => {
    if (activeTab === 'test') {
      if (currentSession?.id) {
        fetchSessionLogs(currentSession.id)
      } else {
        setDataSource([])
        setTotal(0)
        setTxCount(0)
        setRxCount(0)
      }
      return
    }
    fetchRecords()
  }, [activeTab, page, pageSize, keyword, recordProtocolFilter, recordExecutorFilter, currentSession?.id])

  useEffect(() => {
    if (activeTab !== 'test' || !currentSession?.id || currentSession.status !== 1) return
    const timer = window.setInterval(() => {
      fetchSessionLogs(currentSession.id, true)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [activeTab, currentSession?.id, currentSession?.status])

  useEffect(() => {
    const activeConfig = shouldHydrateCurrentProtocolForm ? sessionConfig : currentModuleScannedConfig
    const mutableValues = pickMutableProtocolFormValues(currentModuleKind, protocolForm.getFieldsValue())
    protocolForm.setFieldsValue({
      ...normalizeProtocolFormValues(currentModuleKind, activeConfig),
      ...mutableValues,
    })
  }, [protocolForm, currentModuleKind, protocolFormSyncKey, shouldHydrateCurrentProtocolForm, currentModuleScannedConfig, sessionConfig])

  useEffect(() => {
    if (activeTab !== 'test' || shouldHydrateCurrentProtocolForm) return
    let cancelled = false

    const scanAvailableChannels = async () => {
      setChannelScanLoadingMap((prev) => ({ ...prev, [currentModuleKind]: true }))
      try {
        const currentValues = protocolForm.getFieldsValue()
        const requestedConfig = pickConnectionConfig(currentModuleKind, currentValues)
        const res: any = await protocolTestApi.scanChannel({
          target: selectedTargetLabel || undefined,
          protocol: currentModuleKind,
          config: requestedConfig,
        })
        if (cancelled || res?.code !== 0) return
        const resolvedKind = normalizeModuleKind(res.data?.protocol || currentModuleKind)
        const mergedConfig = mergeConnectionConfig(resolvedKind, res.data?.config, requestedConfig)
        setScannedChannelConfigs((prev) => ({ ...prev, [resolvedKind]: mergedConfig }))
      } catch (error: any) {
        if (cancelled) return
        if (consumeBackendServiceError(error)) return
        message.warning(getErrorMessage(error, '自动扫描可用通道失败，请确认后端服务已重启并可访问当前设备'))
      } finally {
        if (!cancelled) {
          setChannelScanLoadingMap((prev) => ({ ...prev, [currentModuleKind]: false }))
        }
      }
    }

    void scanAvailableChannels()

    return () => {
      cancelled = true
    }
  }, [activeTab, currentModuleKind, protocolForm, selectedTargetLabel, shouldHydrateCurrentProtocolForm])

  useEffect(() => {
    if (activeModule !== 'gpio') return
    const nextMode = gpioMode || '输出'
    if (gpioModeRef.current === nextMode) return
    gpioModeRef.current = nextMode
    protocolForm.setFieldValue('current_level', '')
  }, [activeModule, gpioMode, protocolForm])

  useEffect(() => {
    currentSessionRef.current = currentSession
    const sessionId = Number(currentSession?.id || 0)
    if (sessionId > 0 && Number(currentSession?.status) === 1) {
      persistProtocolSessionId(sessionId)
    } else {
      clearPersistedProtocolSessionId()
      unloadDisconnectSessionIdRef.current = null
    }
  }, [currentSession])

  useEffect(() => {
    void cleanupPersistedProtocolSession()
  }, [])

  useEffect(() => {
    const releaseActiveSession = () => {
      const session = currentSessionRef.current
      const sessionId = Number(session?.id || 0)
      if (sessionId <= 0 || Number(session?.status) !== 1) {
        clearPersistedProtocolSessionId()
        unloadDisconnectSessionIdRef.current = null
        return
      }
      persistProtocolSessionId(sessionId)
      if (unloadDisconnectSessionIdRef.current === sessionId) {
        return
      }
      unloadDisconnectSessionIdRef.current = sessionId
      void disconnectProtocolSessionSilently(sessionId, true)
    }

    window.addEventListener('pagehide', releaseActiveSession)
    window.addEventListener('beforeunload', releaseActiveSession)
    return () => {
      window.removeEventListener('pagehide', releaseActiveSession)
      window.removeEventListener('beforeunload', releaseActiveSession)
    }
  }, [])

  useEffect(() => {
    if (currentModuleKind !== 'can' || !canRemoteFrameEnabled) return
    protocolForm.setFieldValue('data', '')
  }, [currentModuleKind, canRemoteFrameEnabled, protocolForm])

  const openConnectionError = (title: string, error: any, fallback: string) => {
    if (consumeBackendServiceError(error)) {
      return
    }
    message.error(`${title}：${getErrorMessage(error, fallback)}`)
  }

  const handleConnectChannel = async () => {
    if (!selectedTargetLabel) {
      message.warning('请先选择测试对象，建立通道前需要先选择板卡测试对象')
      return
    }
    setChannelActionLoading(true)
    try {
      await cleanupPersistedProtocolSession()
      const values = await protocolForm.validateFields(getConnectionValidationFields(currentModuleKind, protocolForm.getFieldValue('protocol')))
      if (currentModuleKind === 'ethernet') {
        const configurationError = getEthernetConfigurationError(values.protocol, values)
        if (configurationError) {
          message.warning(configurationError)
          return
        }
      }
      const requestedConfig = {
        ...pickConnectionConfig(currentModuleKind, values),
        ...(currentModuleKind !== gpioModuleKey ? { data_type: dataType } : {}),
        ...((currentModuleKind === 'can')
          ? {
              backend_key: values.backend_key || selectedCanDeviceMeta?.backend_key || currentModuleChannelConfig.backend_key || '',
              adapter_serial: selectedCanDeviceMeta?.serial_number || currentModuleChannelConfig.adapter_serial || '',
              adapter_device: values.com_port || selectedCanDeviceMeta?.adapter_device || selectedCanDeviceMeta?.device || currentModuleChannelConfig.adapter_device || '',
              com_port:
                values.com_port ||
                selectedCanDeviceMeta?.adapter_device ||
                currentModuleChannelConfig.com_port ||
                currentModuleChannelConfig.adapter_device ||
                '',
            }
          : (currentModuleKind === 'canfd')
          ? {
              adapter_serial: selectedCanDeviceMeta?.serial_number || currentModuleChannelConfig.adapter_serial || '',
              adapter_device: selectedCanDeviceMeta?.device || selectedCanDeviceMeta?.pnp_device_id || currentModuleChannelConfig.adapter_device || '',
            }
          : {}),
      }
      const res: any = await protocolTestApi.connectChannel({
        target: selectedTargetLabel || '未命名目标',
        protocol: currentModuleKind,
        config: requestedConfig,
      })
      if (res?.code === 0) {
        const resolvedKind = normalizeModuleKind(res.data?.protocol)
        const mergedConfig = mergeConnectionConfig(
          resolvedKind,
          res.data?.config,
          requestedConfig,
          resolvedKind === 'ethernet' ? ethernetLocalIpOptions : [],
        )
        setCurrentSession({
          ...res.data,
          protocol: resolvedKind,
          config: mergedConfig,
          config_json: JSON.stringify(mergedConfig),
        })
        persistProtocolSessionId(Number(res.data?.id || 0))
        if (resolvedKind === gpioModuleKey) {
          setActiveModule('gpio')
        } else {
          setActiveModule('protocol')
          setProtocolSubTab(resolvedKind as ProtocolKind)
        }
        protocolForm.setFieldsValue(normalizeProtocolFormValues(resolvedKind, mergedConfig))
        await fetchSessionLogs(res.data.id)
        message.success(res.data?.probe_summary || '通道连接成功')
      }
    } catch (error: any) {
      if (error?.errorFields) {
        message.warning(getFirstFormErrorMessage(error))
        return
      }
      openConnectionError('通道连接失败', error, '通道建立失败')
    } finally {
      setChannelActionLoading(false)
    }
  }

  const handleDisconnect = async () => {
    if (!currentSession?.id) return
    setChannelActionLoading(true)
    try {
      await protocolTestApi.disconnect(currentSession.id)
      clearPersistedProtocolSessionId()
      setCurrentSession((prev: any) => (prev ? { ...prev, status: 2 } : prev))
      await fetchSessionLogs(currentSession.id)
      message.success('通道已断开，板卡选择和协议切换已恢复可编辑状态')
    } catch (error: any) {
      openConnectionError('断开通道失败', error, '通道断开失败')
    } finally {
      setChannelActionLoading(false)
    }
  }

  const handleGpioPinChange = (value: string) => {
    protocolForm.setFieldsValue({
      pin: value,
      current_level: '',
    })
  }

  const handleGpioModeChange = (value: string) => {
    protocolForm.setFieldsValue({
      mode: value,
      current_level: '',
    })
    gpioModeRef.current = value
  }

  const handleClassicCanAdapterChange = (value: string) => {
    const detectedDevices = Array.isArray(currentModuleChannelConfig.detected_devices) ? currentModuleChannelConfig.detected_devices : []
    const selectedDevice = detectedDevices.find((item: any) => String(item?.adapter_key || '') === String(value || ''))
    const channels = Array.isArray(selectedDevice?.channels)
      ? selectedDevice.channels.map((item: any) => String(item?.name || item?.label || '').trim()).filter(Boolean)
      : []
    const nextPhysicalChannel = channels[0] || ''
    protocolForm.setFieldsValue({
      adapter_key: String(selectedDevice?.adapter_key || value || '').trim(),
      backend_key: String(selectedDevice?.backend_key || '').trim(),
      com_port: String(selectedDevice?.adapter_device || selectedDevice?.device || '').trim(),
      physical_channel: nextPhysicalChannel,
      channel: nextPhysicalChannel,
      termination_enabled: String(selectedDevice?.backend_key || '').trim() === 'usbcanfd_200u',
    })
  }

  const handleSend = async () => {
    if (!currentSession?.id) {
      message.warning('请先连接通道')
      return
    }
    try {
      const values = await protocolForm.validateFields()
      const payloadData =
        currentModuleKind === gpioModuleKey
          ? undefined
          : values.data || undefined
      if (currentModuleKind === 'can' || currentModuleKind === 'canfd') {
        validateCanPayloadConsistency({
          protocol: currentModuleKind,
          payload: payloadData,
          declaredLength: values.data_length,
          dataType,
          isRemoteFrame: currentModuleKind === 'can' && !!values.remote_frame,
        })
      }
      const runtimeConfig =
        currentModuleKind === 'can'
          ? {
              backend_key: values.backend_key || selectedCanDeviceMeta?.backend_key || currentModuleChannelConfig.backend_key || '',
              adapter_key: values.adapter_key,
              com_port:
                values.com_port ||
                selectedCanDeviceMeta?.adapter_device ||
                currentModuleChannelConfig.com_port ||
                currentModuleChannelConfig.adapter_device ||
                '',
              physical_channel: values.physical_channel,
              channel: values.physical_channel,
              adapter_serial: selectedCanDeviceMeta?.serial_number || currentModuleChannelConfig.adapter_serial || '',
              adapter_device: values.com_port || selectedCanDeviceMeta?.adapter_device || selectedCanDeviceMeta?.device || currentModuleChannelConfig.adapter_device || '',
              baud_rate: values.baud_rate,
              bitrate: values.baud_rate,
              id_format: values.id_format,
              frame_format: values.id_format,
              remote_frame: !!values.remote_frame,
              termination_enabled: !!values.termination_enabled,
              data_length: values.data_length,
              dlc: values.data_length,
              data_type: dataType,
            }
          : currentModuleKind === 'canfd'
            ? {
                adapter_key: values.adapter_key,
                physical_channel: values.physical_channel,
                channel: values.physical_channel,
                adapter_serial: selectedCanDeviceMeta?.serial_number || currentModuleChannelConfig.adapter_serial || '',
                adapter_device: selectedCanDeviceMeta?.device || selectedCanDeviceMeta?.pnp_device_id || currentModuleChannelConfig.adapter_device || '',
                arb_baud_rate: values.arb_baud_rate,
                arb_bitrate: values.arb_baud_rate,
                data_baud_rate: values.brs ? values.data_baud_rate : values.arb_baud_rate,
                data_bitrate: values.brs ? values.data_baud_rate : values.arb_baud_rate,
                id_format: values.id_format,
                frame_format: values.id_format,
                brs: values.brs !== false,
                termination_enabled: !!values.termination_enabled,
                canfd_non_iso: !!values.canfd_non_iso,
                data_length: values.data_length,
                dlc: canFdLengthToDlc(Number(values.data_length ?? 0)),
                data_type: dataType,
              }
          : currentModuleKind === 'serial'
            ? {
                com_port: values.com_port,
                serial_ports: serialPortOptions.map((item: { label: string; value: string }) => item.value),
                baud_rate: values.baud_rate,
                auto_append_crlf: !!values.auto_append_crlf,
                length_bytes: values.length_bytes,
                data_bits: values.data_bits,
                stop_bits: values.stop_bits,
                parity: values.parity,
                flow_control: values.flow_control,
                data_type: dataType,
              }
            : currentModuleKind === 'ethernet'
              ? {
                  transport_protocol: normalizeEthernetMode(values.protocol),
                  protocol: normalizeEthernetMode(values.protocol),
                  local_ip: values.local_ip,
                  local_ip_options: ethernetLocalIpOptions.map((item: { label: string; value: string }) => item.value),
                  local_port: values.local_port,
                  listen_port: values.listen_port,
                  target_ip: values.target_ip,
                  target_port: values.target_port,
                  timeout: values.timeout,
                  data_type: dataType,
                }
            : {
                pin: values.pin,
                mode: values.mode,
                action:
                  values.mode === '输出'
                    ? 'set_level'
                    : values.mode === '输入 (单次读取)'
                      ? 'read_level'
                      : 'listen',
                target_level: values.target_level,
                pull_mode: values.pull_mode,
                expected_level: values.expected_level,
                current_level: values.current_level,
                trigger_type: values.trigger_type,
                timeout_ms: values.timeout_ms,
              }
      const res: any = await protocolTestApi.send(currentSession.id, {
        frame_id: values.frame_id || values.pin || undefined,
        dlc:
          currentModuleKind === 'serial'
            ? values.length_bytes
            : currentModuleKind === 'canfd'
              ? canFdLengthToDlc(Number(values.data_length ?? 0))
              : values.data_length,
        data: payloadData,
        config: runtimeConfig,
      })
      message.success(res?.message || '数据发送成功')
      await fetchSessionLogs(currentSession.id)
    } catch (e: any) {
      if (e?.errorFields) {
        message.warning(getFirstFormErrorMessage(e))
        return
      }
      if (consumeBackendServiceError(e)) {
        return
      }
      message.error(e?.response?.data?.detail || '发送失败')
      if (currentSession?.id) {
        await fetchSessionLogs(currentSession.id, true)
      }
    }
  }

  const handleClearLogs = async () => {
    if (!currentSession?.id) return
    try {
      await protocolTestApi.clearLogs(currentSession.id)
      message.success('日志已清空')
      await fetchSessionLogs(currentSession.id)
    } catch (error: any) {
      if (consumeBackendServiceError(error)) return
      message.error('清空失败')
    }
  }

  const handleDeleteRecord = async (recordId: number) => {
    setDeletingRecordId(recordId)
    try {
      await protocolTestApi.deleteRecord(recordId)
      message.success('删除成功')
      if (selectedRecord?.id === recordId) {
        setIsDetailOpen(false)
        setSelectedRecord(null)
      }
      fetchRecords()
    } catch (error: any) {
      if (consumeBackendServiceError(error)) return
      message.error('删除失败')
    } finally {
      setDeletingRecordId(null)
    }
  }

  const handleOpenReportHtml = async (recordId: number, print = false) => {
    setReportLoading(true)
    const popup = window.open('', '_blank')
    try {
      const blobData: any = await protocolTestApi.getReportHtml(recordId, print)
      const blob = blobData instanceof Blob ? blobData : new Blob([blobData], { type: 'text/html;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      if (!popup) {
        window.URL.revokeObjectURL(url)
        message.warning('请允许浏览器打开新窗口')
        return
      }
      popup.location.href = url
      window.setTimeout(() => window.URL.revokeObjectURL(url), 60000)
    } catch (error: any) {
      if (consumeBackendServiceError(error)) return
      popup?.close()
      message.error('报告打开失败')
    } finally {
      setReportLoading(false)
    }
  }

  const handleDownloadReportHtml = async (recordId: number) => {
    setReportLoading(true)
    try {
      const blobData: any = await protocolTestApi.getReportHtml(recordId, false)
      const blob = blobData instanceof Blob ? blobData : new Blob([blobData], { type: 'text/html;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `protocol_report_${recordId}.html`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    } catch (error: any) {
      if (consumeBackendServiceError(error)) return
      message.error('报告下载失败')
    } finally {
      setReportLoading(false)
    }
  }

  const handleExportPdf = async (recordId: number) => {
    await handleOpenReportHtml(recordId, true)
  }

  const handleSearch = () => {
    setPage(1)
    fetchRecords()
  }

  const renderTargetSelect = () => (
    <Select
      value={selectedTarget || undefined}
      style={{ width: 240 }}
      placeholder="选择测试对象"
      onChange={(value) => setSelectedTarget(value)}
      disabled={isChannelConnected || channelActionLoading}
      showSearch
      optionFilterProp="children"
    >
      {products.map((item) => (
        <Select.Option key={item.id} value={String(item.id)}>{item.name}</Select.Option>
      ))}
    </Select>
  )

  const renderChannelToolbar = () => (
    <div className="pcids-channel-toolbar">
      {isChannelConnected ? (
        <Button type="default" danger icon={<DisconnectOutlined />} onClick={handleDisconnect} loading={channelActionLoading}>
          断开通道
        </Button>
      ) : (
        <Button type="primary" icon={<LinkOutlined />} onClick={handleConnectChannel} loading={channelActionLoading}>
          连接通道
        </Button>
      )}
      <span className="pcids-channel-toolbar__status">
        <Badge
          color={activeChannelColor}
          text={isChannelConnected && connectedModuleKind === currentModuleKind ? activeChannelText : '未建立'}
        />
      </span>
    </div>
  )

  const renderProtocolSectionHeader = (title = '协议配置') => (
    <div>
      <div className="pcids-protocol-config-head">
        <div className="pcids-protocol-config-title">
          <h3>{title}</h3>
          <div>
            {isChannelConnected && connectedModuleKind === currentModuleKind
              ? `当前通道：${activeChannelText}`
              : currentModuleChannelScanLoading
                ? '正在自动扫描可用通道，请稍候建立连接'
                : '请先建立通道，再进行协议发送与日志采集'}
          </div>
        </div>
        {renderChannelToolbar()}
      </div>
    </div>
  )

  const renderPayloadLabel = (
    <div className="pcids-payload-label">
      <span className="pcids-payload-label__text">{currentModuleKind === 'can' && canRemoteFrameEnabled ? '数据(远程帧不发送数据)' : '数据'}</span>
      <div className="pcids-segment">
        <button
          type="button"
          className={`pcids-segment__btn ${dataType === 'HEX' ? 'pcids-segment__btn--active' : ''}`}
          onClick={() => setDataType('HEX')}
          disabled={currentModuleKind === 'can' && canRemoteFrameEnabled}
        >
          HEX
        </button>
        <button
          type="button"
          className={`pcids-segment__btn ${dataType === 'ASCII' ? 'pcids-segment__btn--active' : ''}`}
          onClick={() => setDataType('ASCII')}
          disabled={currentModuleKind === 'can' && canRemoteFrameEnabled}
        >
          ASCII
        </button>
      </div>
    </div>
  )
  const renderModuleSwitch = () => (
    <div className="pcids-module-switch">
      <button
        type="button"
        className={`pcids-module-switch__btn ${activeModule === 'protocol' ? 'pcids-module-switch__btn--active' : ''}`}
        onClick={() => {
          if (isChannelConnected) return
          setActiveModule('protocol')
        }}
        disabled={isChannelConnected}
      >
        通信协议
      </button>
      <button
        type="button"
        className={`pcids-module-switch__btn ${activeModule === 'gpio' ? 'pcids-module-switch__btn--active' : ''}`}
        onClick={() => {
          if (isChannelConnected) return
          setActiveModule('gpio')
        }}
        disabled={isChannelConnected}
      >
        GPIO物理引脚
      </button>
    </div>
  )
  const renderInlineSwitchField = (name: string, label: string, disabled = false) => (
    <Form.Item style={{ ...compactFormItemStyle, marginTop: 30 }}>
      <div className="pcids-inline-switch-row">
        <span className="pcids-inline-option__label">{label}</span>
        <Form.Item name={name} valuePropName="checked" noStyle>
          <Switch disabled={disabled} />
        </Form.Item>
      </div>
    </Form.Item>
  )
  const renderInlineCheckboxField = (name: string, label: string, disabled = false) => (
    <Form.Item style={compactFormItemStyle}>
      <div className="pcids-inline-option">
        <span className="pcids-inline-option__label">{label}</span>
        <Form.Item name={name} valuePropName="checked" noStyle>
          <Checkbox disabled={disabled} />
        </Form.Item>
      </div>
    </Form.Item>
  )
  const renderEthernetLocalIpField = () => (
    <AutoComplete
      disabled={isEthernetConnectionLocked}
      options={ethernetLocalIpOptions}
      notFoundContent={currentModuleChannelScanLoading ? '正在自动扫描本机网络地址' : '未获取到本机网络地址'}
      filterOption={(inputValue, option) =>
        String(option?.value ?? option?.label ?? '')
          .toLowerCase()
          .includes(String(inputValue || '').toLowerCase())
      }
    >
      <Input autoComplete="off" placeholder="例如：127.0.0.1 或 192.168.0.10" disabled={isEthernetConnectionLocked} />
    </AutoComplete>
  )

  const refreshGpioWchSerialDevices = async () => {
    setChannelScanLoadingMap((prev) => ({ ...prev, [gpioModuleKey]: true }))
    try {
      const currentValues = protocolForm.getFieldsValue()
      const requestedConfig = pickConnectionConfig(gpioModuleKey, currentValues)
      const res: any = await protocolTestApi.scanChannel({
        target: selectedTargetLabel || undefined,
        protocol: gpioModuleKey,
        config: requestedConfig,
      })
      if (res?.code !== 0) return
      const mergedConfig = mergeConnectionConfig(gpioModuleKey, res.data?.config, requestedConfig)
      setScannedChannelConfigs((prev) => ({ ...prev, [gpioModuleKey]: mergedConfig }))
    } catch (error: any) {
      if (consumeBackendServiceError(error)) return
      message.warning(getErrorMessage(error, '搜索 WCH 串口失败，请检查设备连接和驱动'))
    } finally {
      setChannelScanLoadingMap((prev) => ({ ...prev, [gpioModuleKey]: false }))
    }
  }

  const renderGpioWchSerialDevices = () => (
    <div className="pcids-gpio-wch">
      <div className="pcids-gpio-wch__head">
        <div>
          <div className="pcids-gpio-wch__title">WCH USB 串口</div>
          <div className="pcids-gpio-wch__hint">
            {currentModuleChannelScanLoading
              ? '正在搜索 WCH USB 串口'
              : gpioWchSerialDevices.length
                ? `已检测到 ${gpioWchSerialDevices.length} 个 WCH 串口`
                : '未检测到 WCH 串口'}
          </div>
        </div>
        <Button size="small" icon={<SearchOutlined />} onClick={refreshGpioWchSerialDevices} loading={currentModuleChannelScanLoading}>
          搜索WCH USB串口
        </Button>
      </div>
      {gpioWchSerialDevices.length ? (
        <div className="pcids-gpio-wch__select">
          <Form.Item
            label="串口号"
            name="wch_serial_port"
            style={compactFormItemStyle}
            rules={[{ required: true, message: '请选择 WCH USB 串口' }]}
          >
            <Select
              options={gpioWchSerialOptions}
              placeholder="请选择 WCH USB 串口"
              loading={currentModuleChannelScanLoading}
              showSearch
              optionFilterProp="label"
              onChange={(value) => {
                protocolForm.setFieldsValue({
                  wch_serial_port: value,
                  com_port: value,
                })
                setScannedChannelConfigs((prev) => {
                  const current = parseJsonConfig(prev[gpioModuleKey])
                  return {
                    ...prev,
                    [gpioModuleKey]: {
                      ...current,
                      wch_serial_port: value,
                      com_port: value,
                      gpio_transport_kind: 'wch_gpio',
                      gpio_transport_config: {
                        ...(current.gpio_transport_config && typeof current.gpio_transport_config === 'object' ? current.gpio_transport_config : {}),
                        kind: 'wch_gpio',
                        com_port: value,
                        pin_base_index: 0,
                      },
                    },
                  }
                })
              }}
            />
          </Form.Item>
          {selectedWchSerialDevice ? (
            <div className="pcids-gpio-wch__selected">
              <span className="pcids-gpio-wch__port">{String(selectedWchSerialDevice.device || selectedWchSerialPort)}</span>
              <span className="pcids-gpio-wch__chip">{String(selectedWchSerialDevice.chip || 'WCH')}</span>
              <span className="pcids-gpio-wch__desc">{String(selectedWchSerialDevice.description || 'WCH USB 串口设备')}</span>
            </div>
          ) : null}
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 WCH 串口" style={{ margin: '8px 0 0' }} />
      )}
    </div>
  )

  const handleGpioAction = async (action: 'set_level' | 'read_level' | 'listen') => {
    if (!currentSession?.id) {
      message.warning('请先连接 GPIO 引脚通道')
      return
    }
    try {
      const formValues = protocolForm.getFieldsValue()
      const values = await protocolForm.validateFields(
        action === 'set_level'
          ? ['pin', 'mode', 'target_level']
          : action === 'read_level'
            ? ['pin', 'mode', 'expected_level']
            : ['pin', 'mode', 'trigger_type', 'timeout_ms'],
      )
      const runtimeConfig = {
        pin: values.pin,
        mode: values.mode,
        action,
        target_level: values.target_level,
        pull_mode: formValues.pull_mode,
        expected_level: values.expected_level,
        current_level: formValues.current_level,
        trigger_type: values.trigger_type,
        timeout_ms: values.timeout_ms,
        wch_serial_port: formValues.wch_serial_port,
        com_port: formValues.com_port || formValues.wch_serial_port,
        gpio_transport_config: {
          ...(formValues.gpio_transport_config && typeof formValues.gpio_transport_config === 'object' ? formValues.gpio_transport_config : {}),
          kind: 'wch_gpio',
          com_port: formValues.com_port || formValues.wch_serial_port,
          pin_base_index: 0,
        },
      }
      const res: any = await protocolTestApi.send(currentSession.id, {
        frame_id: values.pin,
        config: runtimeConfig,
      })
      const nextCurrentLevel = res?.data?.current_level || res?.data?.config?.current_level
      if (nextCurrentLevel) {
        protocolForm.setFieldValue('current_level', nextCurrentLevel)
      }
      message.success(res?.message || 'GPIO 操作成功')
      await fetchSessionLogs(currentSession.id)
    } catch (e: any) {
      if (e?.errorFields) return
      if (consumeBackendServiceError(e)) {
        return
      }
      message.error(e?.response?.data?.detail || 'GPIO 操作失败')
      if (currentSession?.id) {
        await fetchSessionLogs(currentSession.id, true)
      }
    }
  }

  const updateGpioBatchRow = (key: string, patch: Partial<GpioBatchRow>) => {
    setGpioBatchRows((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)))
  }

  const selectedGpioBatchRowCount = gpioBatchRows.filter((row) => row.selected).length
  const allGpioBatchRowsSelected = gpioBatchRows.length > 0 && selectedGpioBatchRowCount === gpioBatchRows.length
  const partiallySelectedGpioBatchRows = selectedGpioBatchRowCount > 0 && selectedGpioBatchRowCount < gpioBatchRows.length

  const setAllGpioBatchRowsSelected = (selected: boolean) => {
    setGpioBatchRows((rows) => rows.map((row) => ({ ...row, selected })))
  }

  const applyGpioBatchMode = (mode: '输出' | '输入') => {
    setGpioBatchModePreset(mode)
    setGpioBatchRows((rows) => rows.map((row) => (row.selected ? { ...row, mode } : row)))
  }

  const applyGpioBatchTargetLevel = (targetLevel: '高电平' | '低电平') => {
    setGpioBatchLevelPreset(targetLevel)
    setGpioBatchRows((rows) =>
      rows.map((row) => (row.selected ? { ...row, target_level: targetLevel } : row)),
    )
  }

  const renderGpioBatchPresetDropdown = <T extends string>(
    draftValue: T | undefined,
    setDraftValue: (value: T | undefined) => void,
    options: Array<{ label: string; value: T }>,
    onReset: () => void,
    onConfirm: () => void,
  ) => (
    <div className="pcids-gpio-batch__preset-panel">
      {options.map((option) => (
        <label key={option.value} className="pcids-gpio-batch__preset-option">
          <Checkbox checked={draftValue === option.value} onChange={(event) => setDraftValue(event.target.checked ? option.value : undefined)} />
          <span>{option.label}</span>
        </label>
      ))}
      <div className="pcids-gpio-batch__preset-footer">
        <Button size="small" onClick={onReset}>重置</Button>
        <Button size="small" type="primary" onClick={onConfirm}>确定</Button>
      </div>
    </div>
  )

  const runGpioBatchAction = async (action: 'batch_read' | 'batch_write') => {
    if (!currentSession?.id) {
      message.warning('请先连接 GPIO 引脚通道')
      return
    }
    const selectedRows = gpioBatchRows.filter((row) => row.selected)
    if (!selectedRows.length) {
      message.warning('请至少选择一个 GPIO 引脚')
      return
    }
    setGpioBatchLoading(true)
    try {
      const formValues = protocolForm.getFieldsValue()
      const batchItems = selectedRows.map((row) => ({
        pin: row.pin,
        selected: row.selected,
        mode: action === 'batch_read' ? row.mode : '输出',
        target_level: row.target_level,
        expected_level: action === 'batch_read' ? row.expected_level : row.target_level,
      }))
      const res: any = await protocolTestApi.send(currentSession.id, {
        frame_id: 'GPIO-BATCH',
        config: {
          action,
          mode: action === 'batch_read' ? '输入 (单次读取)' : '输出',
          batch_items: batchItems,
          wch_serial_port: formValues.wch_serial_port,
          com_port: formValues.com_port || formValues.wch_serial_port,
          gpio_transport_config: {
            ...(formValues.gpio_transport_config && typeof formValues.gpio_transport_config === 'object' ? formValues.gpio_transport_config : {}),
            kind: 'wch_gpio',
            com_port: formValues.com_port || formValues.wch_serial_port,
            pin_base_index: 0,
          },
        },
      })
      const resultItems = Array.isArray(res?.data?.items) ? res.data.items : []
      setGpioBatchRows((rows) =>
        rows.map((row) => {
          const next = resultItems.find((item: any) => String(item?.pin || '') === row.pin)
          if (!next) return row
          const nextTargetLevel =
            next.target_level === '高电平' || next.target_level === '低电平'
              ? next.target_level
              : row.target_level
          return {
            ...row,
            mode: action === 'batch_write' ? '输出' : row.mode,
            target_level: nextTargetLevel,
            current_level: next.current_level || (action === 'batch_write' ? nextTargetLevel : '-'),
            result: next.result || (next.passed ? '通过' : '未通过'),
          }
        }),
      )
      message.success(res?.message || 'GPIO 批量操作完成')
      await fetchSessionLogs(currentSession.id)
    } catch (e: any) {
      if (consumeBackendServiceError(e)) return
      message.error(e?.response?.data?.detail || 'GPIO 批量操作失败')
      if (currentSession?.id) {
        await fetchSessionLogs(currentSession.id, true)
      }
    } finally {
      setGpioBatchLoading(false)
    }
  }

  const renderGpioBatchTable = () => (
    <div className="pcids-gpio-batch">
      <div className="pcids-gpio-batch__toolbar">
        <Button icon={<SearchOutlined />} onClick={() => runGpioBatchAction('batch_read')} loading={gpioBatchLoading} disabled={!currentSession?.id || currentSession?.status !== 1}>
          批量读取
        </Button>
        <Button type="primary" icon={<SendOutlined />} onClick={() => runGpioBatchAction('batch_write')} loading={gpioBatchLoading} disabled={!currentSession?.id || currentSession?.status !== 1}>
          批量下发
        </Button>
      </div>
      <div className="pcids-gpio-batch__table">
        <div className="pcids-gpio-batch__head">
          <div className="pcids-gpio-batch__head-cell pcids-gpio-batch__head-cell--select">
            <Checkbox
              className="pcids-gpio-batch__head-check"
              checked={allGpioBatchRowsSelected}
              indeterminate={partiallySelectedGpioBatchRows}
              onChange={(event) => setAllGpioBatchRowsSelected(event.target.checked)}
            />
            <div className="pcids-gpio-batch__head-label">选择</div>
          </div>
          <div className="pcids-gpio-batch__head-cell">
            <div className="pcids-gpio-batch__head-label">引脚</div>
          </div>
          <div className="pcids-gpio-batch__head-cell">
            <Dropdown
              open={gpioBatchModeDropdownOpen}
              onOpenChange={(open) => {
                setGpioBatchModeDropdownOpen(open)
                if (open) setGpioBatchModeDraft(gpioBatchModePreset)
              }}
              trigger={['click']}
              dropdownRender={() =>
                renderGpioBatchPresetDropdown<'输出' | '输入'>(
                  gpioBatchModeDraft,
                  setGpioBatchModeDraft,
                  [{ label: '输出', value: '输出' }, { label: '输入', value: '输入' }],
                  () => {
                    setGpioBatchModeDraft(undefined)
                    setGpioBatchModePreset(undefined)
                  },
                  () => {
                    if (gpioBatchModeDraft) {
                      applyGpioBatchMode(gpioBatchModeDraft)
                    } else {
                      setGpioBatchModePreset(undefined)
                    }
                    setGpioBatchModeDropdownOpen(false)
                  },
                )
              }
            >
              <button type="button" className="pcids-gpio-batch__head-trigger">
                <span className="pcids-gpio-batch__head-trigger-text">方向</span>
                <FilterFilled className="pcids-gpio-batch__head-trigger-icon" />
              </button>
            </Dropdown>
          </div>
          <div className="pcids-gpio-batch__head-cell">
            <Dropdown
              open={gpioBatchLevelDropdownOpen}
              onOpenChange={(open) => {
                setGpioBatchLevelDropdownOpen(open)
                if (open) setGpioBatchLevelDraft(gpioBatchLevelPreset)
              }}
              trigger={['click']}
              dropdownRender={() =>
                renderGpioBatchPresetDropdown<'高电平' | '低电平'>(
                  gpioBatchLevelDraft,
                  setGpioBatchLevelDraft,
                  [{ label: '高电平', value: '高电平' }, { label: '低电平', value: '低电平' }],
                  () => {
                    setGpioBatchLevelDraft(undefined)
                    setGpioBatchLevelPreset(undefined)
                  },
                  () => {
                    if (gpioBatchLevelDraft) {
                      applyGpioBatchTargetLevel(gpioBatchLevelDraft)
                    } else {
                      setGpioBatchLevelPreset(undefined)
                    }
                    setGpioBatchLevelDropdownOpen(false)
                  },
                )
              }
            >
              <button type="button" className="pcids-gpio-batch__head-trigger">
                <span className="pcids-gpio-batch__head-trigger-text">目标电平</span>
                <FilterFilled className="pcids-gpio-batch__head-trigger-icon" />
              </button>
            </Dropdown>
          </div>
          <div className="pcids-gpio-batch__head-cell">
            <div className="pcids-gpio-batch__head-label">当前电平</div>
          </div>
          <div className="pcids-gpio-batch__head-cell">
            <div className="pcids-gpio-batch__head-label">结果</div>
          </div>
        </div>
        <div className="pcids-gpio-batch__body">
          {gpioBatchRows.map((row) => (
            <div className="pcids-gpio-batch__row" key={row.key}>
              <div>
                <Checkbox checked={row.selected} onChange={(event) => updateGpioBatchRow(row.key, { selected: event.target.checked })} />
              </div>
              <div>{row.pin}</div>
              <div>
                <Select
                  value={row.mode}
                  size="small"
                  options={[{ label: '输出', value: '输出' }, { label: '输入', value: '输入' }]}
                  onChange={(value) => updateGpioBatchRow(row.key, { mode: value })}
                />
              </div>
              <div>
                {row.mode === '输出' ? (
                  <Select
                    value={row.target_level}
                    size="small"
                    options={gpioLevelOptions}
                    onChange={(value) => updateGpioBatchRow(row.key, { target_level: value })}
                  />
                ) : (
                  <span className="pcids-gpio-batch__muted">--</span>
                )}
              </div>
              <div>{row.current_level || '-'}</div>
              <div>
                <span className={row.result === '通过' || row.result === '已读取' ? 'pcids-gpio-batch__pass' : row.result === '未通过' ? 'pcids-gpio-batch__fail' : ''}>
                  {row.result || '-'}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )

  const renderGpioForm = () => (
    <Form form={protocolForm} layout="vertical">
      {renderProtocolSectionHeader('GPIO 验证参数配置')}
      {renderGpioWchSerialDevices()}
      <Tabs
        className="pcids-gpio-tabs"
        activeKey={gpioDebugTab}
        onChange={(key) => setGpioDebugTab(key as GpioDebugTab)}
        items={[
          { key: 'single', label: '单点调试' },
          { key: 'batch', label: '批量验证' },
        ]}
      />
      {gpioDebugTab === 'batch' ? renderGpioBatchTable() : (
      <Row gutter={12}>
        <Col span={12}>
          <Form.Item label="引脚选择" name="pin" style={compactFormItemStyle} rules={[{ required: true, message: '请选择 GPIO 引脚' }]}>
            <Select options={gpioPinOptions} onChange={handleGpioPinChange} disabled={false} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="模式" name="mode" style={compactFormItemStyle} rules={[{ required: true, message: '请选择模式' }]}>
            <Select options={gpioModeOptions} onChange={handleGpioModeChange} disabled={false} />
          </Form.Item>
        </Col>
        {gpioMode === '输出' && (
          <>
            <Col span={12}>
              <Form.Item label="目标电平" name="target_level" style={compactFormItemStyle}>
                <Select options={gpioLevelOptions} disabled={false} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <div style={{ paddingTop: 30 }}>
                <Button type="primary" block icon={<SendOutlined />} onClick={() => handleGpioAction('set_level')} disabled={!currentSession?.id || currentSession?.status !== 1}>
                  设置电平
                </Button>
              </div>
            </Col>
          </>
        )}
        {gpioMode === '输入 (单次读取)' && (
          <>
            <Col span={12}>
              <Form.Item label="期望电平" name="expected_level" style={compactFormItemStyle}>
                  <Select options={gpioExpectedLevelOptions} disabled={false} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="当前电平" name="current_level" style={compactFormItemStyle}>
                <Input readOnly placeholder="点击读取电平后自动回填" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <div style={{ paddingTop: 30 }}>
                <Button type="primary" block icon={<SearchOutlined />} onClick={() => handleGpioAction('read_level')} disabled={!currentSession?.id || currentSession?.status !== 1}>
                  读取电平
                </Button>
              </div>
            </Col>
          </>
        )}
      </Row>
      )}
    </Form>
  )

  const renderProtocolForm = () => {
    switch (protocolSubTab) {
      case 'can':
        return (
          <Form form={protocolForm} layout="vertical">
            {renderProtocolSectionHeader()}
            <Form.Item name="backend_key" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="adapter_key" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="com_port" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="physical_channel" hidden>
              <Input />
            </Form.Item>
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item label="适配器" name="adapter_key" style={compactFormItemStyle} rules={[{ required: true, message: '请选择适配器' }]}>
                  <Select
                    options={canAdapterOptions}
                    disabled={isCurrentProtocolConfigLocked}
                    loading={currentModuleChannelScanLoading}
                    placeholder={currentModuleChannelScanLoading ? '正在自动扫描经典 CAN 适配器' : '自动扫描后选择可用适配器'}
                    notFoundContent={currentModuleChannelScanLoading ? '正在自动扫描经典 CAN 适配器' : '未获取到本机经典 CAN 适配器'}
                    onChange={handleClassicCanAdapterChange}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="物理通道" name="physical_channel" style={compactFormItemStyle} rules={[{ required: true, message: '请选择物理通道' }]}>
                  <Select
                    options={canPhysicalChannelOptions}
                    disabled={isCurrentProtocolConfigLocked}
                    loading={currentModuleChannelScanLoading}
                    placeholder={currentModuleChannelScanLoading ? '等待适配器返回物理通道' : '选择可用物理通道'}
                    notFoundContent={currentModuleChannelScanLoading ? '等待适配器返回物理通道' : '当前适配器未返回可用物理通道'}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="波特率" name="baud_rate" style={compactFormItemStyle}>
                  <Select options={canBaudRateOptions} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="标识符格式" name="id_format" style={compactFormItemStyle}>
                  <Select options={[{ label: '标准帧(11位)', value: '标准帧(11位)' }, { label: '扩展帧(29位)', value: '扩展帧(29位)' }]} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="数据长度(DLC)" name="data_length" style={compactFormItemStyle} rules={[{ validator: canLengthValidator('can') }]}>
                  <Select options={canDlcOptions.map((item) => ({ ...item, label: `${item.value}` }))} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="帧ID" name="frame_id" style={compactFormItemStyle} rules={[({ getFieldValue }) => ({ validator: canFrameIdValidator(String(getFieldValue('id_format') || '').includes('扩展')) })]}>
                  <Input name="frame_id" autoComplete="off" placeholder="0x" />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item style={compactFormItemStyle}>
                  <div className="pcids-inline-switch-row" style={{ justifyContent: 'flex-start', gap: 12 }}>
                    <span className="pcids-inline-option__label">远程帧</span>
                    <Form.Item name="remote_frame" valuePropName="checked" noStyle>
                      <Switch disabled={isCurrentProtocolConfigLocked} />
                    </Form.Item>
                  </div>
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item style={compactFormItemStyle}>
                  <div className="pcids-inline-switch-row" style={{ justifyContent: 'flex-start', gap: 12 }}>
                    <span className="pcids-inline-option__label">内部120Ω终端电阻</span>
                    <Form.Item name="termination_enabled" valuePropName="checked" noStyle>
                      <Switch
                        disabled={
                          isCurrentProtocolConfigLocked ||
                          String(selectedCanDeviceMeta?.backend_key || currentModuleChannelConfig.backend_key || '') === 'zqwl_ucan_cdc'
                        }
                      />
                    </Form.Item>
                  </div>
                </Form.Item>
              </Col>
            </Row>
            <Form.Item
              label={renderPayloadLabel}
              name="data"
              style={compactFormItemStyle}
              extra="输入长度必须等于配置的数据长度"
            >
              <Input.TextArea
                name="data"
                autoComplete="off"
                rows={3}
                disabled={canRemoteFrameEnabled}
                placeholder={canRemoteFrameEnabled ? '远程帧不发送数据，仅按 DLC 请求对端响应' : dataType === 'HEX' ? '0x' : '输入ASCII数据'}
              />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend} disabled={!currentSession?.id || currentSession?.status !== 1}>发送</Button>
          </Form>
        )
      case 'canfd':
        return (
          <Form form={protocolForm} layout="vertical">
            {renderProtocolSectionHeader()}
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item label="适配器" name="adapter_key" style={compactFormItemStyle} rules={[{ required: true, message: '请选择适配器' }]}>
                  <Select
                    options={canAdapterOptions}
                    disabled={isCurrentProtocolConfigLocked}
                    loading={currentModuleChannelScanLoading}
                    placeholder={currentModuleChannelScanLoading ? '正在自动扫描 CAN 适配器' : '自动扫描后选择可用适配器'}
                    notFoundContent={currentModuleChannelScanLoading ? '正在自动扫描 CAN 适配器' : '未获取到本机 CAN 适配器'}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="物理通道" name="physical_channel" style={compactFormItemStyle} rules={[{ required: true, message: '请选择物理通道' }]}>
                  <Select
                    options={canPhysicalChannelOptions}
                    disabled={isCurrentProtocolConfigLocked}
                    loading={currentModuleChannelScanLoading}
                    placeholder={currentModuleChannelScanLoading ? '等待 SDK 返回物理通道' : '选择 SDK 返回的物理通道'}
                    notFoundContent={currentModuleChannelScanLoading ? '等待 SDK 返回物理通道' : '当前适配器未返回可用物理通道'}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="仲裁段波特率" name="arb_baud_rate" style={compactFormItemStyle}>
                  <Select options={canBaudRateOptions} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="CANFD标准" name="canfd_non_iso" style={compactFormItemStyle}>
                  <Select options={canFdStandardOptions} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="CANFD加速" name="brs" style={compactFormItemStyle}>
                  <Select options={yesNoOptions} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="数据段波特率" style={compactFormItemStyle}>
                  {canFdBrsEnabled === false ? (
                    <Input value="跟随仲裁段" disabled />
                  ) : (
                    <Form.Item name="data_baud_rate" noStyle>
                      <Select options={canFdDataBaudRateOptions} disabled={isCurrentProtocolConfigLocked} />
                    </Form.Item>
                  )}
                </Form.Item>
              </Col>
              <Col span={12}>
                {renderInlineSwitchField('termination_enabled', '内部120Ω终端电阻', isCurrentProtocolConfigLocked)}
              </Col>
              <Col span={12}>
                <Form.Item label="标识符格式" name="id_format" style={compactFormItemStyle}>
                  <Select options={[{ label: '标准帧(11位)', value: '标准帧(11位)' }, { label: '扩展帧(29位)', value: '扩展帧(29位)' }]} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="数据长度(Bytes)" name="data_length" style={compactFormItemStyle} rules={[{ validator: canLengthValidator('canfd') }]}>
                  <Select options={canFdDlcOptions} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Form.Item
                  label="帧ID"
                  name="frame_id"
                  style={compactFormItemStyle}
                  rules={[({ getFieldValue }) => ({ validator: canFrameIdValidator(String(getFieldValue('id_format') || '').includes('扩展')) })]}
                >
                  <Input name="frame_id" autoComplete="off" placeholder="0x" />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item
              label={renderPayloadLabel}
              name="data"
              style={compactFormItemStyle}
              extra="输入长度必须等于配置的数据长度"
            >
              <Input.TextArea name="data" autoComplete="off" rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend} disabled={!currentSession?.id || currentSession?.status !== 1}>发送</Button>
          </Form>
        )
      case 'serial':
        return (
          <Form form={protocolForm} layout="vertical">
            {renderProtocolSectionHeader()}
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item label="串口号" name="com_port" style={compactFormItemStyle} rules={[{ required: true, message: '请选择串口号' }]}>
                  <Select
                    options={serialPortOptions}
                    disabled={isCurrentProtocolConfigLocked}
                    loading={currentModuleChannelScanLoading}
                    placeholder={currentModuleChannelScanLoading ? '正在自动扫描本机串口' : '自动扫描后选择可用串口'}
                    notFoundContent={currentModuleChannelScanLoading ? '正在自动扫描本机串口' : '未获取到本机串口'}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="波特率" name="baud_rate" style={compactFormItemStyle}>
                  <Select options={serialBaudRateOptions} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="长度(Bytes)" name="length_bytes" style={compactFormItemStyle} rules={[{ validator: positiveIntegerValidator('长度(Bytes)') }]}>
                  <InputNumber min={1} precision={0} style={fullWidthStyle} placeholder="请输入正整数" disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="数据位" name="data_bits" style={compactFormItemStyle}>
                  <Select options={[{ label: '8', value: 8 }, { label: '7', value: 7 }, { label: '6', value: 6 }]} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="停止位" name="stop_bits" style={compactFormItemStyle}>
                  <Select options={[{ label: '1', value: 1 }, { label: '1.5', value: 1.5 }, { label: '2', value: 2 }]} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="校验位" name="parity" style={compactFormItemStyle}>
                  <Select options={[{ label: 'NONE', value: 'NONE' }, { label: 'ODD', value: 'ODD' }, { label: 'EVEN', value: 'EVEN' }]} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Form.Item label="流控制" name="flow_control" style={compactFormItemStyle}>
                  <Select options={[{ label: 'NONE', value: 'NONE' }, { label: 'RTS/CTS', value: 'RTS/CTS' }, { label: 'XON/XOFF', value: 'XON/XOFF' }]} disabled={isCurrentProtocolConfigLocked} />
                </Form.Item>
              </Col>
              <Col span={24}>
                {renderInlineCheckboxField('auto_append_crlf', '自动追加换行符 (CRLF)', isCurrentProtocolConfigLocked)}
              </Col>
            </Row>
            <Form.Item label={renderPayloadLabel} name="data" style={compactFormItemStyle}>
              <Input.TextArea name="data" autoComplete="off" rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button type="primary" block icon={<SendOutlined />} onClick={handleSend} disabled={!currentSession?.id || currentSession?.status !== 1}>发送</Button>
          </Form>
        )
      case 'ethernet':
        return (
          <Form form={protocolForm} layout="vertical">
            {renderProtocolSectionHeader()}
            <Row gutter={12}>
              <Col span={24}>
                <Form.Item label="协议模式" name="protocol" style={compactFormItemStyle}>
                  <Select options={ethernetTransportOptions} disabled={isEthernetConnectionLocked} />
                </Form.Item>
              </Col>
              {ethernetProtocolMode === 'TCP Client' && (
                <>
                  <Col span={12}>
                    <Form.Item label="目标IP" name="target_ip" style={compactFormItemStyle} rules={[{ validator: targetIpValidator }]} validateTrigger="onBlur">
                      <Input name="target_ip" autoComplete="off" placeholder="例如：192.168.0.10" disabled={isEthernetConnectionLocked} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="目标端口" name="target_port" style={compactFormItemStyle} rules={[{ validator: portValidator }]}>
                      <InputNumber min={1} max={65535} precision={0} style={fullWidthStyle} placeholder="1-65535" disabled={isEthernetConnectionLocked} />
                    </Form.Item>
                  </Col>
                </>
              )}
              {ethernetProtocolMode === 'TCP Server' && (
                <>
                  <Col span={12}>
                    <Form.Item label="本地IP" name="local_ip" style={compactFormItemStyle} rules={[{ validator: ipValidator }]} validateTrigger="onBlur">
                      {renderEthernetLocalIpField()}
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="监听端口" name="listen_port" style={compactFormItemStyle} rules={[{ validator: portValidator }]}>
                      <InputNumber min={1} max={65535} precision={0} style={fullWidthStyle} placeholder="1-65535" disabled={isEthernetConnectionLocked} />
                    </Form.Item>
                  </Col>
                </>
              )}
              {ethernetProtocolMode === 'UDP' && (
                <>
                  <Col span={12}>
                    <Form.Item label="本地IP" name="local_ip" style={compactFormItemStyle} rules={[{ validator: ipValidator }]} validateTrigger="onBlur">
                      {renderEthernetLocalIpField()}
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="本地端口" name="local_port" style={compactFormItemStyle} rules={[{ validator: portValidator }]}>
                      <InputNumber min={1} max={65535} precision={0} style={fullWidthStyle} placeholder="1-65535" disabled={isEthernetConnectionLocked} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="目标IP" name="target_ip" style={compactFormItemStyle} rules={[{ validator: targetIpValidator }]} validateTrigger="onBlur">
                      <Input name="target_ip" autoComplete="off" placeholder="例如：192.168.0.10" disabled={isEthernetConnectionLocked} />
                    </Form.Item>
                  </Col>
                  <Col span={12}>
                    <Form.Item label="目标端口" name="target_port" style={compactFormItemStyle} rules={[{ validator: portValidator }]}>
                      <InputNumber min={1} max={65535} precision={0} style={fullWidthStyle} placeholder="1-65535" disabled={isEthernetConnectionLocked} />
                    </Form.Item>
                  </Col>
                </>
              )}
              <Col span={24}>
                <Form.Item label="操作超时(ms)" name="timeout" style={compactFormItemStyle} rules={[{ validator: ethernetTimeoutValidator }]}>
                  <InputNumber min={100} max={120000} precision={0} style={fullWidthStyle} placeholder="100-120000" disabled={isEthernetConnectionLocked} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item label={renderPayloadLabel} name="data" style={compactFormItemStyle} rules={[{ validator: ethernetPayloadValidator(dataType) }]}>
              <Input.TextArea name="data" autoComplete="off" rows={3} placeholder={dataType === 'HEX' ? '0x' : '输入ASCII数据'} />
            </Form.Item>
            <Button
              type="primary"
              block
              icon={<SendOutlined />}
              onClick={handleSend}
              disabled={!currentSession?.id || currentSession?.status !== 1 || !isEthernetServerPeerReady}
            >
              发送
            </Button>
          </Form>
        )
      default:
        return <Empty description="该协议尚未配置表单" />
    }
  }

  const renderLogTable = () => {
    if (!displayedLogsNewestFirst.length) {
      return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" style={{ marginTop: 48 }} />
    }

    if (currentModuleKind === gpioModuleKey) {
      return (
        <div className="pcids-live-log">
          <div className="pcids-live-log__header">
            {['时间戳', '方向', '引脚', '模式', '电平', '说明'].map((item) => (
              <div key={item} className="pcids-live-log__head">{item}</div>
            ))}
          </div>
          <div className="pcids-live-log__body">
            {displayedLogsNewestFirst.map((log: any) => {
              const row = getGpioLogPresentation(log)
              const isAnomaly = isAnomalyLog(log)
              const kindClass =
                row.kind === '操作'
                  ? 'pcids-live-log__tag pcids-live-log__tag--action'
                  : row.kind === '事件'
                    ? 'pcids-live-log__tag pcids-live-log__tag--event'
                    : 'pcids-live-log__tag pcids-live-log__tag--system'
              const levelClass = isAnomaly ? 'pcids-live-log__cell--anomaly' : row.level === '高电平' ? 'pcids-live-log__level--high' : row.level === '低电平' ? 'pcids-live-log__level--low' : ''
              return (
                <div key={log.id} className={`pcids-live-log__row ${isAnomaly ? 'pcids-live-log__row--anomaly' : ''}`}>
                  <div className="pcids-live-log__cell">{row.timestamp}</div>
                  <div className="pcids-live-log__cell"><span className={kindClass}>{row.kind}</span></div>
                  <div className="pcids-live-log__cell">{row.pin}</div>
                  <div className="pcids-live-log__cell">{row.mode}</div>
                  <div className={`pcids-live-log__cell ${levelClass}`}>{row.level}</div>
                  <div
                    className={[
                      'pcids-live-log__cell',
                      row.kind === '系统' ? 'pcids-live-log__cell--muted' : '',
                      isAnomaly ? 'pcids-live-log__cell--anomaly' : '',
                    ].filter(Boolean).join(' ')}
                  >
                    {row.description}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )
    }

    const columns = getLogColumns().map((col: any) => ({
      ...col,
      render: (_text: any, record: any) => {
        const row = currentModuleKind === 'ethernet' ? getEthernetLogPresentation(record, sessionConfig, currentSession) : record
        const kind = formatProtocolDirection(String(record.direction || 'System'))
        const color = kind === 'Rx' ? '#16a34a' : kind === 'Tx' ? '#3b82f6' : 'rgba(0,0,0,0.45)'
        const cellText = row?.[col.dataIndex ?? col.key]
        const value =
          col.key === 'timestamp'
            ? formatDateTimeWithMs(cellText)
            : col.key === 'direction'
              ? kind
              : col.key === 'data'
                ? extractPayloadDisplayText(cellText)
                : (cellText ?? '-')
        return <span style={{ color, wordBreak: 'break-all' }}>{value}</span>
      },
    }))

    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Table
          columns={columns}
          dataSource={displayedLogsNewestFirst}
          rowKey="id"
          loading={loading}
          pagination={false}
          scroll={{ y: 'calc(100vh - 400px)' }}
          size="middle"
          style={{ flex: 1 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" /> }}
        />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#fff', borderRadius: 6, padding: 24, overflow: 'auto' }}>
      <style>{protocolStyleText}</style>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div className="client-page-title">
          <h1>{activeModule === 'gpio' ? 'GPIO物理引脚配置' : '通信协议验证'}</h1>
          <p className="client-page-subtitle">{activeModule === 'gpio' ? '独立管理 GPIO 物理引脚的设置、读取与监听' : '配置协议通道、发送验证数据并查看实时通信日志'}</p>
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <Tabs
          className="pcids-protocol-main-tabs"
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key as 'test' | 'record'); setPage(1) }}
          items={[
            { key: 'test', label: '协议测试' },
            { key: 'record', label: '执行记录' },
          ]}
        />

        {activeTab === 'test' && (
          <div style={{ marginTop: 4, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div className="pcids-protocol-target-row">
              <div className="pcids-protocol-target-row__main">
                {renderTargetSelect()}
                <Badge
                  color={isChannelConnected ? activeChannelColor : '#d9d9d9'}
                  text={isChannelConnected ? `${selectedTargetLabel || '测试对象'} · ${activeChannelText}` : '请选择开发板后建立通道'}
                />
              </div>
              <div className="pcids-protocol-target-row__switch">
                {renderModuleSwitch()}
              </div>
            </div>
            <div className={`pcids-protocol-workspace ${activeModule === 'gpio' ? 'pcids-protocol-workspace--gpio' : ''}`}>
              {activeModule === 'protocol' && (
                <div className="pcids-protocol-panel" style={{ padding: 12 }}>
                  <div className="pcids-protocol-switch">
                    {protocolSubTabs.map((item) => {
                      const disabled = isChannelConnected && connectedModuleKind !== item.key
                      return (
                        <button
                          key={item.key}
                          type="button"
                          className={[
                            'pcids-protocol-switch__item',
                            protocolSubTab === item.key ? 'pcids-protocol-switch__item--active' : '',
                            disabled ? 'pcids-protocol-switch__item--disabled' : '',
                          ].filter(Boolean).join(' ')}
                          onClick={() => {
                            if (disabled) return
                            setProtocolSubTab(item.key)
                          }}
                          disabled={disabled}
                        >
                          <span>{item.label}</span>
                          <span className="pcids-protocol-switch__hint">{item.hint}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
              <div className="pcids-protocol-panel pcids-protocol-panel--config">
                {activeModule === 'gpio' ? renderGpioForm() : renderProtocolForm()}
              </div>
              <div className="pcids-protocol-panel pcids-protocol-panel--log">
                <div className="pcids-protocol-log-head">
                  <div className="pcids-protocol-log-title">
                    <SwapOutlined /> {currentLogMeta.title}
                  </div>
                  <Space>
                    <Button type="text" icon={<DeleteOutlined />} size="small" style={{ color: '#86909c' }} onClick={handleClearLogs} disabled={!currentSession?.id}>清空</Button>
                    <Badge color="green" text={currentLogMeta.countText} style={{ background: '#f6ffed', padding: '2px 8px', borderRadius: 4, border: '1px solid #b7eb8f' }} />
                  </Space>
                </div>
                <div className="pcids-protocol-log-body">
                  {renderLogTable()}
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'record' && (
          <>
            <div style={{ marginBottom: 16, marginTop: 16 }}>
              <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', alignItems: 'center', flexWrap: 'wrap' }}>
                <Select
                  value={recordProtocolFilter}
                  style={{ width: 140 }}
                  onChange={(value) => {
                    setRecordProtocolFilter(value)
                    setPage(1)
                  }}
                  options={[
                    { label: '所有协议', value: 'all' },
                    { label: 'CAN', value: 'can' },
                    { label: 'CAN FD', value: 'canfd' },
                    { label: '串口', value: 'serial' },
                    { label: '以太网', value: 'ethernet' },
                    { label: 'GPIO物理引脚', value: 'gpio_io' },
                    { label: 'GPIO物理引脚(历史)', value: 'gpio' },
                  ]}
                />
                <Select
                  value={recordExecutorFilter}
                  style={{ width: 160 }}
                  onChange={(value) => {
                    setRecordExecutorFilter(value)
                    setPage(1)
                  }}
                  options={[{ label: '所有执行人员', value: 'all' }, ...executorOptions.map((item) => ({ label: item, value: item }))]}
                />
                <Input
                  id="protocol-record-search"
                  name="protocolKeyword"
                  autoComplete="off"
                  className="pcids-list-search"
                  placeholder="请输入测试对象"
                  allowClear
                  prefix={<SearchOutlined />}
                  value={keyword}
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
              scroll={{ x: 'max-content' }}
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
              size="middle"
            />
          </>
        )}
      </div>

      <Drawer
        title={<span style={{ fontSize: 16, fontWeight: 700 }}>任务详情</span>}
        placement="right"
        width={500}
        open={isDetailOpen}
        onClose={() => {
          setIsDetailOpen(false)
          setSelectedRecord(null)
          setDetailLogs([])
          setDetailTab('summary')
        }}
        closable
        extra={
          selectedRecord ? (
            <Dropdown
              menu={{
                items: [
                  { key: 'html-preview', label: '预览 HTML' },
                  { key: 'html', label: '导出 HTML' },
                  { key: 'pdf', label: '导出 PDF' },
                ],
                onClick: ({ key }) => {
                  if (!selectedRecord?.id) return
                  if (key === 'html-preview') handleOpenReportHtml(selectedRecord.id)
                  if (key === 'html') handleDownloadReportHtml(selectedRecord.id)
                  if (key === 'pdf') handleExportPdf(selectedRecord.id)
                },
              }}
              trigger={['click']}
            >
              <Button type="text" loading={reportLoading}>
                导出 <DownOutlined style={{ fontSize: 10 }} />
              </Button>
            </Dropdown>
          ) : null
        }
        styles={{ body: { padding: 0, background: '#fff' } }}
      >
        {detailLoading && <div style={{ padding: 24 }}>加载中...</div>}
        {!detailLoading && !selectedRecord && <Empty description="暂无数据" style={{ marginTop: 80 }} />}
        {selectedRecord && (
          <div>
            <Tabs
              activeKey={detailTab}
              onChange={(key) => setDetailTab(key as 'summary' | 'logs' | 'config')}
              items={[
                { key: 'summary', label: '任务概要' },
                { key: 'logs', label: selectedRecordLogMeta.title },
                { key: 'config', label: '协议配置参数' },
              ]}
              style={{ padding: '0 20px' }}
            />
            <div style={{ padding: '8px 20px 20px' }}>
              {detailTab === 'summary' && (
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 20 }}>测试基本信息</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', rowGap: 14, columnGap: 10, marginBottom: 24, color: '#1f2a5c' }}>
                    <span style={{ color: '#8c8c8c' }}>测试任务编号</span>
                    <span>{selectedRecord.task_no || '-'}</span>
                    <span style={{ color: '#8c8c8c' }}>测试对象</span>
                    <span>{selectedRecord.target || '-'}</span>
                    <span style={{ color: '#8c8c8c' }}>协议类型</span>
                    <span>
                      <span style={{ ...(protocolTagStyleMap[String(selectedRecord.protocol || '').toLowerCase()] || { background: '#f0f0f0', color: '#666' }), display: 'inline-block', padding: '2px 10px', borderRadius: 999 }}>
                        {protocolLabelMap[String(selectedRecord.protocol || '').toLowerCase()] || selectedRecord.protocol || '-'}
                      </span>
                    </span>
                    <span style={{ color: '#8c8c8c' }}>判定结论</span>
                    <span style={{ color: selectedRecordConfig.validation_result === 'passed' ? '#52c41a' : '#cf1322' }}>
                      {selectedRecordConfig.validation_result === 'passed' ? '测试通过' : selectedRecordConfig.validation_code === 'gpio_read_skip' ? '仅记录读取结果' : '测试未通过'}
                    </span>
                    <span style={{ color: '#8c8c8c' }}>执行时间</span>
                    <span>{formatDateTime(selectedRecord.created_at)}</span>
                    <span style={{ color: '#8c8c8c' }}>已用时间</span>
                    <span>{selectedRecordElapsedText}</span>
                  </div>

                  <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 16 }}>{selectedRecordLogMeta.title === '操作日志' ? '操作统计' : '通信统计'}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div style={{ border: '1px solid #f0f0f0', borderRadius: 12, padding: 16 }}>
                      <div style={{ color: '#8c8c8c', marginBottom: 10 }}>{selectedRecordLogMeta.countLabels.tx}</div>
                      <div style={{ fontSize: 30, fontWeight: 700, color: '#4b5ee9' }}>{selectedRecord.tx || 0}</div>
                    </div>
                    <div style={{ border: '1px solid #f0f0f0', borderRadius: 12, padding: 16 }}>
                      <div style={{ color: '#8c8c8c', marginBottom: 10 }}>{selectedRecordLogMeta.countLabels.rx}</div>
                      <div style={{ fontSize: 30, fontWeight: 700, color: '#52c41a' }}>{selectedRecord.rx || 0}</div>
                    </div>
                    <div style={{ border: '1px solid #f0f0f0', borderRadius: 12, padding: 16 }}>
                      <div style={{ color: '#8c8c8c', marginBottom: 10 }}>异常帧数</div>
                      <div style={{ fontSize: 30, fontWeight: 700, color: '#ff4d4f' }}>{getAnomalyCount(detailLogs)}</div>
                    </div>
                    <div style={{ border: '1px solid #f0f0f0', borderRadius: 12, padding: 16 }}>
                      <div style={{ color: '#8c8c8c', marginBottom: 10 }}>{selectedRecordLogMeta.countLabels.total}</div>
                      <div style={{ fontSize: 30, fontWeight: 700, color: '#1f2a5c' }}>{(selectedRecord.tx || 0) + (selectedRecord.rx || 0)}</div>
                    </div>
                  </div>

                  <div style={{ marginTop: 28, fontSize: 14, fontWeight: 700, marginBottom: 14 }}>异常记录</div>
                  {getAnomalyCount(detailLogs) > 0 ? (
                    <div style={{ color: '#ff4d4f' }}>检测到 {getAnomalyCount(detailLogs)} 条异常相关日志，请切换到日志页签查看。</div>
                  ) : (
                    <div style={{ color: '#8c8c8c', textAlign: 'center', padding: '18px 0' }}>本次测试未检测到异常记录</div>
                  )}
                </div>
              )}

              {detailTab === 'logs' && (
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 16 }}>{selectedRecordLogMeta.title}</div>
                  {renderDetailLogTable(selectedRecord)}
                </div>
              )}

              {detailTab === 'config' && (
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 18 }}>
                    {(protocolLabelMap[String(selectedRecord.protocol || '').toLowerCase()] || selectedRecord.protocol || '协议')}参数快照
                  </div>
                  <div style={{ display: 'grid', gap: 12 }}>
                    {selectedRecordConfigEntries.map((item) => (
                      <div
                        key={item.key}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: '132px minmax(0, 1fr)',
                          columnGap: 14,
                          alignItems: 'start',
                          padding: '12px 14px',
                          border: '1px solid #f0f0f0',
                          borderRadius: 10,
                          background: '#fafafa',
                        }}
                      >
                        <div
                          style={{
                            color: '#8c8c8c',
                            fontSize: 12,
                            lineHeight: '20px',
                            fontWeight: 500,
                            textTransform: 'none',
                            wordBreak: 'break-word',
                          }}
                        >
                          {item.label}
                        </div>
                        <pre
                          style={{
                            margin: 0,
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                            overflowWrap: 'anywhere',
                            color: '#1f2a5c',
                            fontSize: 12,
                            lineHeight: '20px',
                            fontFamily: 'Consolas, "Courier New", monospace',
                          }}
                        >
                          {item.value}
                        </pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </Drawer>
    </div>
  )
}

export default Protocol
