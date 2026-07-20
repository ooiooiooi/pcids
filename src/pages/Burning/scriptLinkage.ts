export type AssociationCandidate = string | undefined | null

export type BurnerLike = {
  id?: number
  name?: string
  type?: string
  sn?: string
  port?: string
  is_system?: number
  task_type?: string
  associated_board?: string
  associated_burner?: string
  associated_ide?: string
}

export type BoardLike = {
  name?: string
  chip_model?: string
  chip_type?: string
}

export type ScriptLike = BurnerLike & {
  id?: number
  default_config_json?: string | null
}

export type SelectOption = {
  label: string
  value: string
}

export type ScriptSelectParameterDescriptor = {
  field: string
  label: string
  value: string
  options: SelectOption[]
  disabled: boolean
}

// 不同“执行操作”对应侧栏需要显示的烧录参数。
// 只列需要的字段；不列出的字段在侧栏隐藏，但保留原值不重置。
// 这里只覆盖 AL321 / 通用 FPGA 烧录脚本里常见的执行操作。
export const OPERATION_FIELD_VISIBILITY: Record<string, string[]> = {
  // 临时下载到 SRAM：界面展示 接口类型 / 擦除方式 / 执行操作 / 完成后动作
  SRAM: ['interfaceType', 'eraseMode', 'executionOperation', 'completionAction'],
  'SRAM下载': ['interfaceType', 'eraseMode', 'executionOperation', 'completionAction'],
  // 永久烧录到 Flash：界面展示 接口类型 / 擦除方式 / QSPI连接方式 / 执行操作 /
  // 完成后动作 / ZynqMP FSBL文件(.elf) / Flash偏移地址
  FLASH: [
    'interfaceType',
    'eraseMode',
    'qspiFlashModel',
    'executionOperation',
    'completionAction',
    'targetConfigFile',
    'startAddress',
  ],
  'Flash固化': [
    'interfaceType',
    'eraseMode',
    'qspiFlashModel',
    'executionOperation',
    'completionAction',
    'targetConfigFile',
    'startAddress',
  ],
  // 纯 JTAG 操作：通常只需要 接口类型 / 执行操作 / 完成后动作
  JTAG: ['interfaceType', 'executionOperation', 'completionAction'],
}

// 把“执行操作”的原始取值归一化后再匹配映射表 key
export const normalizeExecutionOperationKey = (raw: any): string => {
  const text = String(raw || '').trim()
  if (!text) return ''
  // 优先按原始值匹配
  if (OPERATION_FIELD_VISIBILITY[text]) return text
  // 兼容小写/去空格
  const compact = text.toLowerCase().replace(/[\s_-]+/g, '')
  for (const key of Object.keys(OPERATION_FIELD_VISIBILITY)) {
    if (key.toLowerCase().replace(/[\s_-]+/g, '') === compact) return key
  }
  return text
}

export const isFieldVisibleForOperation = (field: string, executionOperation: any): boolean => {
  const opKey = normalizeExecutionOperationKey(executionOperation)
  if (!opKey) return true // 未选择执行操作时全展示，避免用户空白页
  const allowed = OPERATION_FIELD_VISIBILITY[opKey]
  if (!allowed) return true // 未知执行操作时全展示
  return allowed.includes(field)
}

const hasDefinedField = (config: Record<string, any> | null | undefined, field: string) =>
  Boolean(config && Object.prototype.hasOwnProperty.call(config, field) && config[field] !== undefined && config[field] !== null)

const normalizeAssociationValue = (value: any) =>
  String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[_\-\s]+/g, '')
    .replace(/[()（）]/g, '')

const associationTokenMatchesCandidate = (token: string, candidate: string) => {
  if (!token || !candidate) return false
  if (token === candidate) return true
  if (token.length <= 4 || candidate.length <= 4) return false
  return candidate.includes(token) || token.includes(candidate)
}

export const hasConfiguredAssociation = (value: any) => Boolean(String(value || '').trim())

export const matchAssociation = (association: any, candidates: AssociationCandidate[]) => {
  const source = String(association || '').trim()
  if (!source) return true
  const tokens = source
    .split(/[，,;/|]+/)
    .map((item) => normalizeAssociationValue(item))
    .filter(Boolean)
  if (tokens.length === 0) return true
  const normalizedCandidates = candidates
    .map((item) => normalizeAssociationValue(item))
    .filter(Boolean)
  return tokens.some((token) => normalizedCandidates.some((candidate) => associationTokenMatchesCandidate(token, candidate)))
}

export const getCompatibleBoardScripts = ({
  scripts,
  platform,
  selectedBurner,
  selectedBoard,
}: {
  scripts: ScriptLike[]
  platform: 'board' | 'os' | 'hybrid' | null
  selectedBurner?: BurnerLike | null
  selectedBoard?: BoardLike | null
}) => {
  if (platform === 'hybrid') {
    const matched = scripts.filter((script) => {
      const taskType = String(script?.task_type || '').trim().toLowerCase()
      if (taskType && taskType !== 'hybrid') return false
      return matchAssociation(script.associated_board, [
        selectedBoard?.name,
        selectedBoard?.chip_model,
        selectedBoard?.chip_type,
      ])
    })
    const systemScripts = matched.filter((script) => Number(script.is_system || 0) === 1)
    return systemScripts.length > 0 ? systemScripts : matched
  }

  if (platform !== 'board' || !selectedBurner) {
    return []
  }

  const matched = scripts.filter((script) => {
    if (String(script?.task_type || 'board') !== 'board') return false
    if (!hasConfiguredAssociation(script?.associated_burner)) return false

    const burnerMatch = matchAssociation(script.associated_burner, [
      selectedBurner?.name,
      selectedBurner?.type,
      selectedBurner?.sn,
      selectedBurner?.port,
    ])
    const boardMatch = matchAssociation(script.associated_board, [
      selectedBoard?.name,
      selectedBoard?.chip_model,
      selectedBoard?.chip_type,
    ])

    return burnerMatch && boardMatch
  })

  const systemScripts = matched.filter((script) => Number(script.is_system || 0) === 1)
  return systemScripts.length > 0 ? systemScripts : matched
}

const toSelectOptions = (items: any[]) =>
  items
    .map((item) => String(item ?? '').trim())
    .filter(Boolean)
    .map((item) => ({ label: resolveScriptConfigDisplayText(item, item), value: item }))

const buildIndexOptions = (initialValue: any) => {
  const start = Number(initialValue ?? 0)
  const safeStart = Number.isFinite(start) ? Math.max(0, start) : 0
  return Array.from({ length: 8 }, (_, index) => {
    const value = String(Math.max(index, safeStart))
    return { label: value, value }
  })
}

const looksLikeMojibake = (value: string) => {
  if (!value) return false
  if (value.includes('�') || value.includes('□')) return true
  const hasCjk = /[\u4e00-\u9fff]/.test(value)
  if (hasCjk) return false
  return /[ÃÂÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]/.test(value)
}

export const resolveScriptConfigDisplayText = (value: any, fallback: string) => {
  const text = String(value ?? '').trim()
  if (!text || looksLikeMojibake(text)) return fallback
  return text
}

export const buildScriptSelectParameterDescriptors = ({
  defaultConfig,
  currentValues,
  enabled,
}: {
  defaultConfig?: Record<string, any> | null
  currentValues: Record<string, any>
  enabled: boolean
}): ScriptSelectParameterDescriptor[] => {
  if (!defaultConfig) return []

  const descriptors: ScriptSelectParameterDescriptor[] = []
  const pushDescriptor = (field: string, label: string, options: SelectOption[]) => {
    if (!options.length) return
    descriptors.push({
      field,
      label,
      value: String(currentValues[field] ?? ''),
      options,
      disabled: !enabled || options.length === 1,
    })
  }

  pushDescriptor(
    'interfaceType',
    resolveScriptConfigDisplayText(defaultConfig.interface_type_label, '接口类型'),
    toSelectOptions(defaultConfig.interface_type_options || []),
  )
  pushDescriptor('eraseMode', resolveScriptConfigDisplayText(defaultConfig.erase_mode_label, '擦除方式'), toSelectOptions(defaultConfig.erase_mode_options || []))
  pushDescriptor('writeSpeed', resolveScriptConfigDisplayText(defaultConfig.speed_label, '烧录速度(khz)'), toSelectOptions(defaultConfig.speed_options || []))
  pushDescriptor('qspiFlashModel', resolveScriptConfigDisplayText(defaultConfig.qspi_flash_model_label, 'QSPI Flash型号'), toSelectOptions(defaultConfig.qspi_flash_model_options || []))
  pushDescriptor('loaderType', resolveScriptConfigDisplayText(defaultConfig.loader_type_label, 'Loader 类型'), toSelectOptions(defaultConfig.loader_type_options || []))
  pushDescriptor('programVoltage', resolveScriptConfigDisplayText(defaultConfig.program_voltage_label, '编程电压'), toSelectOptions(defaultConfig.program_voltage_options || []))
  pushDescriptor('eepromWrite', resolveScriptConfigDisplayText(defaultConfig.eeprom_write_label, 'EEPROM 是否烧写'), toSelectOptions(defaultConfig.eeprom_write_options || []))
  pushDescriptor('writeConfigBits', resolveScriptConfigDisplayText(defaultConfig.write_config_bits_label, '写入配置位'), toSelectOptions(defaultConfig.write_config_bits_options || []))
  pushDescriptor('executionOperation', resolveScriptConfigDisplayText(defaultConfig.execution_operation_label, '执行操作'), toSelectOptions(defaultConfig.execution_operation_options || []))
  pushDescriptor('bichinaBurnMode', resolveScriptConfigDisplayText(defaultConfig.bichina_burn_mode_label, 'Bichina烧录参数'), toSelectOptions(defaultConfig.bichina_burn_mode_options || []))
  pushDescriptor('preErase', resolveScriptConfigDisplayText(defaultConfig.pre_erase_label, '编程前擦除'), toSelectOptions(defaultConfig.pre_erase_options || []))
  pushDescriptor('blankCheck', resolveScriptConfigDisplayText(defaultConfig.blank_check_label, '空白检查'), toSelectOptions(defaultConfig.blank_check_options || []))
  pushDescriptor('executeProgram', resolveScriptConfigDisplayText(defaultConfig.execute_program_label, '执行编程'), toSelectOptions(defaultConfig.execute_program_options || []))
  pushDescriptor('tckFrequency', resolveScriptConfigDisplayText(defaultConfig.tck_frequency_label, 'TCK 频率'), toSelectOptions(defaultConfig.tck_frequency_options || []))
  pushDescriptor('formatSdCard', resolveScriptConfigDisplayText(defaultConfig.format_sd_card_label, '拷贝前格式化 SD 卡'), toSelectOptions(defaultConfig.format_sd_card_options || []))
  pushDescriptor('completionAction', resolveScriptConfigDisplayText(defaultConfig.completion_action_label, '完成后动作'), toSelectOptions(defaultConfig.completion_action_options || []))

  if (defaultConfig.jtag_chain_index !== undefined || Array.isArray(defaultConfig.jtag_chain_index_options)) {
    pushDescriptor(
      'jtagChainIndex',
      resolveScriptConfigDisplayText(defaultConfig.jtag_chain_index_label, 'JTAG链路序号'),
      Array.isArray(defaultConfig.jtag_chain_index_options) && defaultConfig.jtag_chain_index_options.length > 0
        ? toSelectOptions(defaultConfig.jtag_chain_index_options)
        : buildIndexOptions(defaultConfig.jtag_chain_index),
    )
  }

  if (defaultConfig.cable_index !== undefined || Array.isArray(defaultConfig.cable_index_options)) {
    pushDescriptor(
      'cableIndex',
      resolveScriptConfigDisplayText(defaultConfig.cable_index_label, 'Cable Index'),
      Array.isArray(defaultConfig.cable_index_options) && defaultConfig.cable_index_options.length > 0
        ? toSelectOptions(defaultConfig.cable_index_options)
        : buildIndexOptions(defaultConfig.cable_index),
    )
  }

  return descriptors
}

export const getSupportedScriptConfigFields = (defaultConfig?: Record<string, any> | null) => {
  if (!defaultConfig) return [] as string[]

  const fields = new Set<string>()
  const addIf = (condition: boolean, field: string) => {
    if (condition) fields.add(field)
  }

  addIf(hasDefinedField(defaultConfig, 'interface_type') || Array.isArray(defaultConfig.interface_type_options), 'interfaceType')
  addIf(Array.isArray(defaultConfig.erase_mode_options), 'eraseMode')
  addIf(Array.isArray(defaultConfig.speed_options), 'writeSpeed')
  addIf(Array.isArray(defaultConfig.qspi_flash_model_options), 'qspiFlashModel')
  addIf(Array.isArray(defaultConfig.loader_type_options), 'loaderType')
  addIf(hasDefinedField(defaultConfig, 'target_config_file') || Boolean(defaultConfig.target_config_file_label), 'targetConfigFile')
  addIf(hasDefinedField(defaultConfig, 'gel_init_script') || Boolean(defaultConfig.gel_init_script_label), 'gelInitScript')
  addIf(hasDefinedField(defaultConfig, 'jtag_chain_index') || Array.isArray(defaultConfig.jtag_chain_index_options), 'jtagChainIndex')
  addIf(Array.isArray(defaultConfig.program_voltage_options), 'programVoltage')
  addIf(Array.isArray(defaultConfig.eeprom_write_options), 'eepromWrite')
  addIf(Array.isArray(defaultConfig.write_config_bits_options), 'writeConfigBits')
  addIf(Array.isArray(defaultConfig.execution_operation_options), 'executionOperation')
  addIf(Array.isArray(defaultConfig.bichina_burn_mode_options), 'bichinaBurnMode')
  addIf(Array.isArray(defaultConfig.pre_erase_options), 'preErase')
  addIf(Array.isArray(defaultConfig.blank_check_options), 'blankCheck')
  addIf(Array.isArray(defaultConfig.execute_program_options), 'executeProgram')
  addIf(Array.isArray(defaultConfig.tck_frequency_options), 'tckFrequency')
  addIf(hasDefinedField(defaultConfig, 'cable_index') || Array.isArray(defaultConfig.cable_index_options), 'cableIndex')
  addIf(hasDefinedField(defaultConfig, 'sd_target_path') || Boolean(defaultConfig.sd_target_path_label), 'sdTargetPath')
  addIf(Array.isArray(defaultConfig.format_sd_card_options), 'formatSdCard')
  addIf(Array.isArray(defaultConfig.completion_action_options), 'completionAction')

  addIf(hasDefinedField(defaultConfig, 'start_address') || Boolean(defaultConfig.start_address_label), 'startAddress')

  return Array.from(fields)
}
