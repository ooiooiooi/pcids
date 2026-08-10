export type ProtocolModuleKind = 'can' | 'canfd' | 'serial' | 'ethernet' | 'gpio_io'

export const classicalCanAllowedLengths = [0, 1, 2, 3, 4, 5, 6, 7, 8]
export const canFdAllowedLengths = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]
export const canFdLengthToDlcMap: Record<number, number> = {
  0: 0,
  1: 1,
  2: 2,
  3: 3,
  4: 4,
  5: 5,
  6: 6,
  7: 7,
  8: 8,
  12: 9,
  16: 10,
  20: 11,
  24: 12,
  32: 13,
  48: 14,
  64: 15,
}

export const ipv4Pattern = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

export const getIpValidationError = (value: unknown) => {
  const text = String(value || '').trim()
  if (!text) return '请输入IP地址'
  if (!ipv4Pattern.test(text)) return 'IP地址格式不正确'
  return ''
}

export const getTargetIpValidationError = (value: unknown) => {
  const baseError = getIpValidationError(value)
  if (baseError) return baseError
  const text = String(value || '').trim()
  const firstOctet = Number(text.split('.')[0])
  if (text === '0.0.0.0' || text === '255.255.255.255' || (firstOctet >= 224 && firstOctet <= 239)) {
    return '目标IP不能使用 0.0.0.0、广播或组播地址'
  }
  return ''
}

export const ipValidator = async (_: unknown, value: unknown) => {
  const error = getIpValidationError(value)
  if (error) {
    throw new Error(error)
  }
}

export const targetIpValidator = async (_: unknown, value: unknown) => {
  const error = getTargetIpValidationError(value)
  if (error) {
    throw new Error(error)
  }
}

export const getEthernetConfigurationError = (
  modeInput: unknown,
  values: Record<string, unknown>,
) => {
  const mode = normalizeEthernetTransportMode(modeInput)
  const timeout = Number(values.timeout)
  if (!Number.isInteger(timeout) || timeout < 100 || timeout > 120000) {
    return '超时时间必须在 100-120000ms 范围内'
  }
  if (mode === 'UDP') {
    const localIp = String(values.local_ip || '').trim()
    const targetIp = String(values.target_ip || '').trim()
    const localPort = Number(values.local_port)
    const targetPort = Number(values.target_port)
    if (localIp === targetIp && localPort === targetPort) {
      return 'UDP 本地地址与目标地址不能完全相同，否则会形成本机回环并造成误判'
    }
  }
  return ''
}

export const shouldHydrateProtocolFormFromSession = ({
  currentSessionId,
  isChannelConnected,
  connectedModuleKind,
  currentModuleKind,
}: {
  currentSessionId?: number | null
  isChannelConnected: boolean
  connectedModuleKind?: string | null
  currentModuleKind: ProtocolModuleKind
}) => Boolean(currentSessionId && isChannelConnected && connectedModuleKind === currentModuleKind && currentModuleKind !== 'gpio_io')

export const getProtocolFormSyncKey = ({
  currentSessionId,
  isChannelConnected,
  connectedModuleKind,
  currentModuleKind,
}: {
  currentSessionId?: number | null
  isChannelConnected: boolean
  connectedModuleKind?: string | null
  currentModuleKind: ProtocolModuleKind
}) => `${currentModuleKind}|${currentSessionId ?? 'none'}|${shouldHydrateProtocolFormFromSession({
  currentSessionId,
  isChannelConnected,
  connectedModuleKind,
  currentModuleKind,
}) ? 'session' : 'default'}`

export const filterProtocolTrafficLogs = <T extends { direction?: unknown }>(logs: T[]) =>
  logs.filter((log) => {
    const direction = String(log?.direction || '').trim().toLowerCase()
    return direction === 'rx' || direction === 'tx'
  })

const parseJsonConfigValue = (value: unknown) => {
  if (!value) return {}
  if (typeof value === 'object') return value as Record<string, unknown>
  try {
    const parsed = JSON.parse(String(value))
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

const normalizeEthernetTransportMode = (value: unknown) => {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'tcp' || normalized === 'tcp client' || normalized === 'tcp_client') return 'TCP Client'
  if (normalized === 'tcp server' || normalized === 'tcp_server') return 'TCP Server'
  if (normalized === 'udp') return 'UDP'
  return 'TCP Client'
}

const pickNonEmptyFields = (source: Record<string, unknown>, fields: string[]) => Object.fromEntries(
  fields
    .filter((field) => source[field] !== null && source[field] !== undefined && source[field] !== '')
    .map((field) => [field, source[field]]),
)

const canAuthoritativeDeviceFields = [
  'backend_key',
  'adapter_key',
  'adapter_name',
  'adapter_device',
  'adapter_serial',
  'com_port',
  'physical_channel',
  'physical_channel_options',
  'detected_devices',
  'adapter_options',
  'channels',
  'vid',
  'pid',
  'sdk_device_index',
]

const canEditableUserFields = [
  'baud_rate',
  'bitrate',
  'id_format',
  'frame_format',
  'remote_frame',
  'data_length',
  'dlc',
  'arb_baud_rate',
  'arb_bitrate',
  'data_baud_rate',
  'data_bitrate',
  'brs',
  'termination_enabled',
  'canfd_non_iso',
]

export const mergeProtocolConnectionConfig = ({
  protocol,
  responseConfigInput,
  requestedConfigInput,
  ethernetLocalIpOptions = [],
}: {
  protocol: ProtocolModuleKind
  responseConfigInput: unknown
  requestedConfigInput: unknown
  ethernetLocalIpOptions?: Array<{ label: string; value: string }>
}) => {
  const responseConfig = parseJsonConfigValue(responseConfigInput)
  const requestedConfig = parseJsonConfigValue(requestedConfigInput)

  if (protocol === 'ethernet') {
    const merged = {
      ...Object.fromEntries(Object.entries(requestedConfig).filter(([, value]) => value !== null && value !== undefined && value !== '')),
      ...responseConfig,
    }
    const protocolMode = normalizeEthernetTransportMode(
      responseConfig.transport_protocol || responseConfig.protocol || requestedConfig.transport_protocol || requestedConfig.protocol,
    )
    const localIpValues = ethernetLocalIpOptions.map((item) => item.value)
    const localIpOptions = Array.isArray(responseConfig.local_ip_options) && responseConfig.local_ip_options.length
      ? responseConfig.local_ip_options
      : localIpValues
    return {
      ...merged,
      transport_protocol: protocolMode,
      protocol: protocolMode,
      local_ip_options: localIpOptions,
      channel_options:
        Array.isArray(responseConfig.channel_options) && responseConfig.channel_options.length
          ? responseConfig.channel_options
          : localIpOptions,
    }
  }

  if (protocol === 'can' || protocol === 'canfd') {
    const allowedUserConfig = pickNonEmptyFields(requestedConfig, canEditableUserFields)
    const authoritativeBackendDeviceConfig = pickNonEmptyFields(responseConfig, canAuthoritativeDeviceFields)
    return {
      ...responseConfig,
      ...allowedUserConfig,
      ...authoritativeBackendDeviceConfig,
    }
  }

  return {
    ...responseConfig,
    ...Object.fromEntries(Object.entries(requestedConfig).filter(([, value]) => value !== null && value !== undefined && value !== '')),
  }
}

export const parseCanFrameId = (value: unknown, isExtended: boolean) => {
  const text = String(value || '').trim()
  if (!text) throw new Error('请输入帧ID')
  const parsed = text.toLowerCase().startsWith('0x') ? Number.parseInt(text, 16) : Number.parseInt(text, 10)
  if (!Number.isInteger(parsed) || parsed < 0) throw new Error('帧ID必须是合法的十六进制或十进制数值')
  const maxValue = isExtended ? 0x1fffffff : 0x7ff
  if (parsed > maxValue) throw new Error(isExtended ? '扩展帧 ID 范围必须为 0~0x1FFFFFFF' : '标准帧 ID 范围必须为 0~0x7FF')
  return parsed
}

export const validateCanPayloadLength = (protocol: 'can' | 'canfd', value: unknown) => {
  const parsed = Number(value)
  if (!Number.isInteger(parsed)) throw new Error('数据长度(Bytes)必须是整数')
  const allowed = protocol === 'canfd' ? canFdAllowedLengths : classicalCanAllowedLengths
  if (!allowed.includes(parsed)) {
    throw new Error(protocol === 'canfd' ? 'CAN FD 数据长度仅支持 0~8、12、16、20、24、32、48、64 字节' : 'Classical CAN 数据长度必须为 0~8 字节')
  }
  return parsed
}

export const canLengthValidator = (protocol: 'can' | 'canfd') => async (_: unknown, value: unknown) => {
  validateCanPayloadLength(protocol, value)
}

export const canFrameIdValidator = (isExtended: boolean) => async (_: unknown, value: unknown) => {
  parseCanFrameId(value, isExtended)
}

export const parseProtocolPayloadLength = (value: unknown, dataType: 'HEX' | 'ASCII' = 'HEX') => {
  const text = String(value || '').trim()
  if (!text) return 0
  if (dataType === 'ASCII') return new TextEncoder().encode(text).length
  const normalized = text.replaceAll(',', ' ').replaceAll('\n', ' ').replaceAll('\t', ' ')
  const tokens = normalized.split(' ').filter(Boolean)
  if (!tokens.length) return 0
  for (const token of tokens) {
    const item = token.toLowerCase().startsWith('0x') ? token.slice(2) : token
    if (!item || item.length > 2 || /[^0-9a-f]/i.test(item)) {
      throw new Error('HEX 数据格式不正确')
    }
  }
  return tokens.length
}

export const ethernetPayloadValidator = (dataType: 'HEX' | 'ASCII') => async (_: unknown, value: unknown) => {
  const payloadLength = parseProtocolPayloadLength(value, dataType)
  if (payloadLength <= 0) {
    throw new Error('请输入要发送的数据')
  }
}

export const validateCanPayloadConsistency = ({
  protocol,
  payload,
  declaredLength,
  dataType,
  isRemoteFrame,
}: {
  protocol: 'can' | 'canfd'
  payload: unknown
  declaredLength: unknown
  dataType: 'HEX' | 'ASCII'
  isRemoteFrame: boolean
}) => {
  const normalizedLength = validateCanPayloadLength(protocol, declaredLength)
  const actualLength = parseProtocolPayloadLength(payload, dataType)
  if (isRemoteFrame) {
    if (actualLength > 0) throw new Error('远程帧不能填写发送数据，DLC 将使用你配置的数据长度(Bytes)')
    return { declaredLength: normalizedLength, actualLength: 0 }
  }
  if (protocol === 'can') {
    if (actualLength > normalizedLength) {
      throw new Error('输入数据长度不能超过配置的数据长度(DLC)')
    }
    return { declaredLength: normalizedLength, actualLength }
  }
  if (actualLength !== normalizedLength) {
    throw new Error('普通数据帧要求输入数据长度与配置长度严格一致，系统不会自动补零')
  }
  return { declaredLength: normalizedLength, actualLength }
}

export const canFdLengthToDlc = (length: number) => {
  if (!(length in canFdLengthToDlcMap)) {
    throw new Error('CAN FD 数据长度仅支持 0~8、12、16、20、24、32、48、64 字节')
  }
  return canFdLengthToDlcMap[length]
}
