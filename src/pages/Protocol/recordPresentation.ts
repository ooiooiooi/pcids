export type ProtocolRecordKind = 'can' | 'canfd' | 'serial' | 'ethernet' | 'gpio_io'

const normalizeProtocolKind = (value: unknown): ProtocolRecordKind => {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'gpio' || normalized === 'gpio-io' || normalized === 'gpio_io') return 'gpio_io'
  if (normalized === 'canfd' || normalized === 'can_fd') return 'canfd'
  if (normalized === 'serial' || normalized === 'uart') return 'serial'
  if (normalized === 'ethernet' || normalized === 'tcp' || normalized === 'udp') return 'ethernet'
  return 'can'
}

const normalizeEthernetMode = (value: unknown) => {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'tcp server' || normalized === 'tcp_server' || normalized === 'server') return 'TCP Server'
  if (normalized === 'udp') return 'UDP'
  return 'TCP Client'
}

const firstDefined = (config: Record<string, any>, keys: string[]) => {
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(config, key) && config[key] !== undefined && config[key] !== null) {
      return config[key]
    }
  }
  return undefined
}

const snapshotAliases: Record<string, string[]> = {
  physical_channel: ['physical_channel', 'channel'],
  baud_rate: ['baud_rate', 'bitrate'],
  id_format: ['id_format', 'frame_format'],
  arb_baud_rate: ['arb_baud_rate', 'arb_bitrate', 'baud_rate', 'bitrate'],
  data_baud_rate: ['data_baud_rate', 'data_bitrate'],
  transport_protocol: ['transport_protocol', 'protocol', 'method'],
  wch_serial_port: ['wch_serial_port', 'com_port'],
}

const commonOrders: Record<Exclude<ProtocolRecordKind, 'ethernet' | 'gpio_io'>, string[]> = {
  can: ['physical_channel', 'baud_rate', 'id_format', 'remote_frame', 'termination_enabled', 'data_type'],
  canfd: ['physical_channel', 'arb_baud_rate', 'data_baud_rate', 'brs', 'id_format', 'termination_enabled', 'canfd_non_iso', 'data_type'],
  serial: ['com_port', 'baud_rate', 'data_bits', 'stop_bits', 'parity', 'flow_control', 'auto_append_crlf', 'data_type'],
}

export const getProtocolSnapshotFieldOrder = (
  protocol: unknown,
  config: Record<string, any>,
) => {
  const kind = normalizeProtocolKind(protocol)
  if (kind === 'ethernet') {
    const mode = normalizeEthernetMode(firstDefined(config, snapshotAliases.transport_protocol))
    if (mode === 'TCP Server') {
      return ['transport_protocol', 'local_ip', 'listen_port', 'timeout', 'data_type']
    }
    if (mode === 'UDP') {
      return ['transport_protocol', 'local_ip', 'local_port', 'target_ip', 'target_port', 'timeout', 'data_type']
    }
    return ['transport_protocol', 'target_ip', 'target_port', 'timeout', 'data_type']
  }
  if (kind === 'gpio_io') {
    const hasBatchItems = Array.isArray(config.batch_items) && config.batch_items.length > 0
    return hasBatchItems
      ? ['wch_serial_port', 'action', 'mode']
      : ['wch_serial_port', 'action', 'pin', 'mode', 'target_level', 'pull_mode', 'expected_level', 'trigger_type', 'timeout_ms', 'current_level']
  }
  return commonOrders[kind]
}

export const getProtocolSnapshotItems = (
  protocol: unknown,
  configInput: unknown,
) => {
  const config = configInput && typeof configInput === 'object' && !Array.isArray(configInput)
    ? configInput as Record<string, any>
    : {}
  return getProtocolSnapshotFieldOrder(protocol, config).map((key) => ({
    key,
    value: firstDefined(config, snapshotAliases[key] || [key]),
  }))
}

export type GpioBatchSnapshotItem = {
  key: string
  pin: string
  mode: string
  expectedLevel: string
  currentLevel: string
  result: string
}

export const getGpioBatchSnapshotItems = (configInput: unknown): GpioBatchSnapshotItem[] => {
  const config = configInput && typeof configInput === 'object' && !Array.isArray(configInput)
    ? configInput as Record<string, any>
    : {}
  if (!Array.isArray(config.batch_items)) return []
  return config.batch_items.map((item: any, index: number) => {
    const pin = String(item?.pin || '-').trim() || '-'
    const expectedLevel = String(item?.target_level || item?.expected_level || '-').trim() || '-'
    const currentLevel = String(item?.current_level || '-').trim() || '-'
    const passed = item?.passed === true || String(item?.result || '').trim() === '通过'
    return {
      key: `${pin}-${index}`,
      pin,
      mode: String(item?.mode || config.mode || '-').trim() || '-',
      expectedLevel,
      currentLevel,
      result: String(item?.result || (passed ? '通过' : '未通过')).trim() || '-',
    }
  })
}
