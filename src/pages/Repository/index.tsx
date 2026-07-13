import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  App as AntdApp,
  Alert,
  Button,
  Checkbox,
  Col,
  Dropdown,
  Form,
  Input,
  Modal,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tree,
} from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  DeleteOutlined,
  DownOutlined,
  EllipsisOutlined,
  FileOutlined,
  FolderOutlined,
  ReloadOutlined,
  SearchOutlined,
  UserOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { ActionButtonGroup, ActionLinkButton, PagePrimaryButton, PageSecondaryButton } from '../../components/ActionButton'
import EmptyStateIllustration from '../../assets/images/repository-empty-state.svg'
import { repositoryApi, userApi } from '../../services/api'
import { renderListPaginationTotal } from '../../utils/pagination'
import { formatDateTime } from '../../utils/dateTime'
import { patchTreeHiddenInputs } from '../../utils/treeAccessibility'
import { getRepositoryProjectContext, setRepositoryProjectContext } from '../../utils/repositoryProjectContext'
import UserIdentity from '../../components/UserIdentity'
import ActionConfirm, { ActionConfirmDialog } from '../../components/ActionConfirm'
import { Permission, usePermission } from '../../hooks'
import EllipsisText from '../../components/EllipsisText'

type AnyNode = DataNode & Record<string, any>
type InstallSource = 'local' | 'server' | 'codearts'
type DownloadTarget = 'local' | 'server'
type CodeartsRepositoryMode = 'release' | 'private'

function normalizeServerLocationValue(value?: string | null, serverPath?: string | null) {
  const text = String(value || '').trim()
  if (!text) return ''
  if (text.toLowerCase() === 'local' && !String(serverPath || '').trim()) return ''
  return text
}

function formatBytes(val?: number | null) {
  if (!val && val !== 0) return '-'
  const n = Number(val)
  if (!Number.isFinite(n)) return '-'
  const kb = n / 1024
  if (kb < 1024) return `${Math.max(kb, 0.01).toFixed(2)} KB`
  const mb = kb / 1024
  return `${mb.toFixed(2)} MB`
}

function normalizeDisplaySize(size?: number | string | null, displaySize?: string | number | null) {
  const raw = displaySize ?? size
  if (raw === undefined || raw === null || raw === '') return '-'

  if (typeof raw === 'number') return formatBytes(raw)

  const text = String(raw).trim()
  if (!text) return '-'

  const matched = text.match(/^([\d.]+)\s*(B|KB|MB|GB)?$/i)
  if (!matched) return text

  const value = Number(matched[1])
  if (!Number.isFinite(value)) return '-'

  const unit = String(matched[2] || 'B').toUpperCase()
  if (unit === 'KB') return formatBytes(value * 1024)
  if (unit === 'MB') return formatBytes(value * 1024 * 1024)
  if (unit === 'GB') return formatBytes(value * 1024 * 1024 * 1024)
  return formatBytes(value)
}

function collectLeafFiles(node?: AnyNode | null): AnyNode[] {
  if (!node) return []
  if (node.node_type === 'file' || node.isLeaf) return [node]
  const children = Array.isArray(node.children) ? (node.children as AnyNode[]) : []
  return children.flatMap((child) => collectLeafFiles(child))
}

function firstFilled(...values: Array<any>) {
  for (const value of values) {
    if (value === null || value === undefined) continue
    const text = String(value).trim()
    if (text) return text
  }
  return ''
}

function getUserDisplayName(user?: Record<string, any> | null) {
  return firstFilled(user?.display_name, user?.username, user?.name, user?.nickname, '-') || '-'
}

function renderUserIdentity(
  user?: Record<string, any> | null,
  options?: {
    avatarSize?: number
    nameColor?: string
    nameWeight?: number
    secondaryText?: string
  },
) {
  return (
    <UserIdentity
      user={user}
      avatarSize={options?.avatarSize ?? 23}
      nameColor={options?.nameColor ?? '#2b2f36'}
      nameWeight={options?.nameWeight ?? 500}
      secondaryText={options?.secondaryText}
      gap={10}
    />
  )
}

function filterTreeByKeyword(nodes: AnyNode[], keyword: string): AnyNode[] {
  const kw = keyword.trim().toLowerCase()
  if (!kw) return nodes
  const walk = (items: AnyNode[]): AnyNode[] => {
    const out: AnyNode[] = []
    for (const n of items) {
      const titleStr = String(n.title ?? '').toLowerCase()
      const children = Array.isArray(n.children) ? walk(n.children as AnyNode[]) : []
      const hit = titleStr.includes(kw)
      if (hit || children.length > 0) {
        out.push({ ...n, children })
      }
    }
    return out
  }
  return walk(nodes)
}

function filterTreeByProject(nodes: AnyNode[], projectKey: string): AnyNode[] {
  const key = projectKey.trim()
  if (!key) return nodes
  return nodes.filter((node) => String(node.key) === key)
}

function guessCodeartsMetaFromKey(key: string) {
  const m = /^ver_(.+?)_(.+?)_(.+)$/.exec(key)
  if (!m) return null
  return { project_id: m[1], package_id: m[2], version_id: m[3] }
}

function getNodeLocationState(node?: AnyNode | null) {
  const fileDetail = (node?.file_detail || {}) as Record<string, any>
  const storageLocation = String(node?.storage_location ?? fileDetail.storage_location ?? '').trim().toLowerCase()
  const explicitLocalPath = String(node?.local_path ?? fileDetail.local_path ?? '').trim()
  const fallbackFileUrl = storageLocation !== 'server' ? String(node?.file_url ?? '').trim() : ''
  const localPath = explicitLocalPath || fallbackFileUrl
  const serverPath = String(node?.server_path ?? fileDetail.server_path ?? '').trim()
  const serverTarget = normalizeServerLocationValue(
    String(node?.server_target ?? fileDetail.server_target ?? node?.storage_target ?? fileDetail.storage_target ?? '').trim(),
    serverPath,
  )
  const localExists = Boolean(node?.local_exists || fileDetail.local_exists || (localPath && storageLocation !== 'server'))
  const serverExists = Boolean(node?.server_exists || fileDetail.server_exists || serverPath || serverTarget)
  const remoteDownloadable = Boolean(node?.remote_downloadable ?? node?.download_uri ?? fileDetail.download_url ?? fileDetail.download_url_with_id)

  return {
    localExists: localExists && Boolean(localPath),
    localPath: localPath || '',
    serverExists: serverExists && Boolean(serverPath || serverTarget),
    serverPath: serverPath || '',
    serverTarget: serverTarget || '',
    remoteDownloadable,
  }
}

function formatNodeFileLocation(node?: AnyNode | null) {
  const locationState = getNodeLocationState(node)
  const values: string[] = []
  if (locationState.localExists && locationState.localPath) {
    values.push(`本地： ${locationState.localPath}`)
  }
  if (locationState.serverExists) {
    const serverValue = normalizeServerLocationValue(locationState.serverPath || locationState.serverTarget, locationState.serverPath)
    if (serverValue) {
      values.push(`服务器： ${serverValue}`)
    }
  }
  return values.length > 0 ? values.join('\n') : '-'
}

function mergeLocationStateIntoNode(node: any, locationState: Record<string, any>) {
  const nextFileDetail = {
    ...(node.file_detail || {}),
    local_exists: locationState.local_exists ?? node.local_exists,
    local_path: locationState.local_path ?? node.local_path,
    server_exists: locationState.server_exists ?? node.server_exists,
    server_path: locationState.server_path ?? node.server_path,
    server_target: locationState.server_target ?? node.server_target,
    storage_location: locationState.storage_location ?? node.storage_location,
    storage_target: locationState.storage_target ?? node.storage_target,
    storage_path: locationState.storage_path ?? node.storage_path,
  }
  return {
    ...node,
    file_detail: nextFileDetail,
    local_exists: locationState.local_exists ?? node.local_exists,
    local_path: locationState.local_path ?? node.local_path,
    server_exists: locationState.server_exists ?? node.server_exists,
    server_path: locationState.server_path ?? node.server_path,
    server_target: locationState.server_target ?? node.server_target,
    storage_location: locationState.storage_location ?? node.storage_location,
    storage_target: locationState.storage_target ?? node.storage_target,
    storage_path: locationState.storage_path ?? node.storage_path,
    available_locations: locationState.available_locations ?? node.available_locations,
  }
}

function normalizeChecksum(value: any, length: number) {
  const text = String(value ?? '').trim().toLowerCase()
  if (!text || text === '-' || text === '--' || text === 'null' || text === 'none') return ''
  const exact = new RegExp(`^[a-f0-9]{${length}}$`)
  if (exact.test(text)) return text
  const match = text.match(new RegExp(`(^|[^a-f0-9])([a-f0-9]{${length}})([^a-f0-9]|$)`))
  return match?.[2] || ''
}

function findChecksumValue(value: any, algorithm: 'md5' | 'sha256'): string {
  const length = algorithm === 'sha256' ? 64 : 32
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findChecksumValue(item, algorithm)
      if (found) return found
    }
    return ''
  }
  if (value && typeof value === 'object') {
    const preferredKeys = [
      algorithm,
      algorithm.toUpperCase(),
      algorithm.replace('sha', 'sha-'),
      algorithm.replace('sha', 'SHA-'),
      `${algorithm}_sum`,
      `${algorithm}_value`,
      `${algorithm}_checksum`,
      `${algorithm}_digest`,
      `file_${algorithm}`,
      `file${algorithm}`,
      `${algorithm}sum`,
      'hash',
      'digest',
      'checksum',
      'check_sum',
    ]
    for (const key of preferredKeys) {
      if (Object.prototype.hasOwnProperty.call(value, key)) {
        const found = findChecksumValue(value[key], algorithm)
        if (found) return found
      }
    }
    for (const [key, child] of Object.entries(value)) {
      const keyText = key.toLowerCase().replace(/[-_]/g, '')
      if (keyText.includes(algorithm)) {
        const found = findChecksumValue(child, algorithm)
        if (found) return found
      }
    }
    for (const child of Object.values(value)) {
      const found = findChecksumValue(child, algorithm)
      if (found) return found
    }
    return ''
  }
  return normalizeChecksum(value, length)
}

const CODEARTS_FORM_DRAFT_KEY = 'pcids.repository.codeartsFormDraft'
const CODEARTS_FORM_SECRET_DRAFT_KEY = 'pcids.repository.codeartsFormSecretDraft'
const CODEARTS_PRIVATE_FORM_DRAFT_KEY = 'pcids.repository.codeartsPrivateFormDraft'
const CODEARTS_PRIVATE_FORM_SECRET_DRAFT_KEY = 'pcids.repository.codeartsPrivateFormSecretDraft'
const CODEARTS_PRIVATE_TEST_DEFAULTS = {
  domain_name: 'CWGY',
  username: 'cwgy-57373',
  password: String((import.meta as any).env?.VITE_CODEARTS_PRIVATE_TEST_PASSWORD || ''),
  region: 'cn-cq-1',
  project_id: 'cf8f1be184bd4eb484b79139484b673a',
  repo_id_0: 'cn-cq-1_bf7bbb8002b04002bd78a65557e7b7e4_generic_0',
}

const Repository: React.FC = () => {
  const navigate = useNavigate()
  const { hasPermission } = usePermission()
  const { message } = AntdApp.useApp()
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeInitialized, setTreeInitialized] = useState(false)
  const [treeRaw, setTreeRaw] = useState<any[]>([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [selectedNodeKey, setSelectedNodeKey] = useState<string>('')
  const treeContainerRef = useRef<HTMLDivElement | null>(null)
  const nodeMapRef = useRef<Map<string, AnyNode>>(new Map())

  const [codeartsCfg, setCodeartsCfg] = useState<any>({})
  const [codeartsConnected, setCodeartsConnected] = useState(false)
  const [codeartsConnectionDetail, setCodeartsConnectionDetail] = useState('')
  const [projectOptions, setProjectOptions] = useState<Array<{ label: string; value: string; repositoryMode: CodeartsRepositoryMode }>>([])
  const [currentProjectKey, setCurrentProjectKey] = useState<string>('')
  const initialProjectContextRef = useRef(getRepositoryProjectContext())

  const [isCreateProjectOpen, setIsCreateProjectOpen] = useState(false)
  const [configurationModeOverride, setConfigurationModeOverride] = useState<CodeartsRepositoryMode | null>(null)
  const [isMemberPermissionOpen, setIsMemberPermissionOpen] = useState(false)
  const [createProjectSubmitting, setCreateProjectSubmitting] = useState(false)
  const [createProjectError, setCreateProjectError] = useState('')
  const [createProjectForm] = Form.useForm()

  const [membersLoading, setMembersLoading] = useState(false)
  const [members, setMembers] = useState<any[]>([])
  const [userDirectory, setUserDirectory] = useState<Record<string, any>>({})
  const memberTableContainerRef = useRef<HTMLDivElement | null>(null)
  const [memberActionColumnFixed, setMemberActionColumnFixed] = useState(false)
  const [memberKeyword, setMemberKeyword] = useState('')
  const [isInviteOpen, setIsInviteOpen] = useState(false)
  const [inviteKeyword, setInviteKeyword] = useState('')
  const [inviteCandidatesLoading, setInviteCandidatesLoading] = useState(false)
  const [inviteCandidates, setInviteCandidates] = useState<any[]>([])
  const [inviteSelectedUsernames, setInviteSelectedUsernames] = useState<string[]>([])
  const [inviteRole, setInviteRole] = useState<'admin' | 'member'>('member')
  const [inviteSubmitting, setInviteSubmitting] = useState(false)
  const [isRoleChangeOpen, setIsRoleChangeOpen] = useState(false)
  const [roleForm] = Form.useForm()
  const [roleChangingUser, setRoleChangingUser] = useState<any>(null)

  const [permLoading, setPermLoading] = useState(false)
  const [permSaving, setPermSaving] = useState(false)
  const [permConfig, setPermConfig] = useState<any>({})
  const [effectiveProjectPermissions, setEffectiveProjectPermissions] = useState<Record<string, boolean>>({})
  const [canManageCurrentProjectPermissions, setCanManageCurrentProjectPermissions] = useState(false)
  const [permGroup, setPermGroup] = useState<'admin' | 'member'>('admin')
  const [permDraft, setPermDraft] = useState<Record<string, boolean>>({})
  const [permSaveNotice, setPermSaveNotice] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadTaskNotice, setDownloadTaskNotice] = useState<{ type: 'info' | 'success' | 'error'; message: string } | null>(null)
  const [syncTaskNotice, setSyncTaskNotice] = useState<{ type: 'info' | 'success' | 'error'; message: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [syncingCodearts, setSyncingCodearts] = useState(false)
  const [deleteConfirmModal, setDeleteConfirmModal] = useState<{
    open: boolean
    node: AnyNode | null
    scope: 'local' | 'server' | 'all' | null
    title: string
  }>({
    open: false,
    node: null,
    scope: null,
    title: '',
  })
  const [deleteProjectOpen, setDeleteProjectOpen] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
  const [deletingMemberId, setDeletingMemberId] = useState<number | null>(null)

  const treeData: AnyNode[] = useMemo(() => {
    const map = new Map<string, AnyNode>()
    nodeMapRef.current = map

    const build = (items: any[], parentTitles: string[] = []): AnyNode[] => {
      const out: AnyNode[] = []
      for (const it of items || []) {
        const key = String(it.key ?? it._id ?? it.title ?? Math.random())
        const title = String(it.title ?? '')
        if (
          key.startsWith('repo_0_default') ||
          key.startsWith('proj_default') ||
          key.includes('pkg_default') ||
          key.includes('ver_default') ||
          title === '项目1' ||
          title === '制品仓库1'
        ) {
          continue
        }
        const meta = guessCodeartsMetaFromKey(key)
        const next: AnyNode = {
          title,
          key,
          isLeaf: Boolean(it.isLeaf),
          children: Array.isArray(it.children) ? build(it.children, [...parentTitles, title]) : undefined,
          raw: it,
          path_titles: [...parentTitles, title],
          repo_id: it.repo_id ?? null,
          file_url: it.file_url ?? null,
          size: it.size ?? null,
          version: it.version ?? null,
          md5: it.md5 ?? null,
          sha256: it.sha256 ?? null,
          download_count: it.download_count ?? null,
          last_download_time: it.last_download_time ?? null,
          project_id: it.project_id ?? meta?.project_id ?? null,
          package_id: it.package_id ?? meta?.package_id ?? null,
          version_id: it.version_id ?? meta?.version_id ?? null,
          download_uri: it.download_uri ?? null,
          display_path: it.display_path ?? null,
          remote_repo_id: it.remote_repo_id ?? null,
          repo_detail: it.repo_detail ?? null,
          file_detail: it.file_detail ?? null,
          local_exists: it.local_exists ?? null,
          local_path: it.local_path ?? null,
          server_exists: it.server_exists ?? null,
          server_path: it.server_path ?? null,
          server_target: it.server_target ?? null,
          storage_location: it.storage_location ?? null,
          storage_target: it.storage_target ?? null,
          storage_path: it.storage_path ?? null,
          available_locations: it.available_locations ?? [],
          remote_downloadable: it.remote_downloadable ?? null,
          source_type: it.source_type ?? null,
          repository_mode: it.repository_mode ?? it.repo_detail?.repository_mode ?? null,
        }
        if (key.startsWith('proj_')) next.node_type = 'project'
        else if (key.startsWith('repo_sync_')) next.node_type = 'repository'
        else if (key.startsWith('pkg_')) next.node_type = 'package'
        else if (key.startsWith('ver_') || next.isLeaf) next.node_type = 'file'
        else next.node_type = 'folder'
        map.set(key, next)
        out.push(next)
      }
      return out
    }

    return build(treeRaw, [])
  }, [treeRaw])

  const selectedNode = useMemo(() => {
    if (!selectedNodeKey) return null
    return nodeMapRef.current.get(selectedNodeKey) || null
  }, [selectedNodeKey, treeData])

  const visibleTreeData = useMemo(() => filterTreeByProject(treeData, currentProjectKey), [treeData, currentProjectKey])
  const filteredTreeData = useMemo(() => filterTreeByKeyword(visibleTreeData, searchKeyword), [visibleTreeData, searchKeyword])

  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => {
      patchTreeHiddenInputs(treeContainerRef.current, 'repository-tree')
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [filteredTreeData, expandedKeys, selectedKeys, treeLoading])

  const isCodeartsConnected = codeartsConnected
  const repositoryMode: CodeartsRepositoryMode = codeartsCfg?.repository_mode === 'private' ? 'private' : 'release'
  const configurationMode: CodeartsRepositoryMode = configurationModeOverride || repositoryMode
  const visibleProjectOptions = useMemo(
    () => projectOptions.filter((project) => project.repositoryMode === repositoryMode),
    [projectOptions, repositoryMode],
  )
  const canSyncRepository = hasPermission('repository:sync')
  const canManageProjectPermissions = hasPermission('repository:perm_change')
  const canDeleteRepository = hasPermission('repository:delete')
  const canDownloadRepository = hasPermission('repository:download')
  const canCreateBurningTask = hasPermission('burning:add')
  const projectAllows = (key: string) => !currentProjectKey || effectiveProjectPermissions[key] === true
  const canManageProjectPermissionsEffective = canManageProjectPermissions && canManageCurrentProjectPermissions
  const canInviteProjectMembers = canManageProjectPermissions && projectAllows('invite_user')
  const canDeleteProjectMembers = canManageProjectPermissions && projectAllows('delete_user')
  const canDeleteCurrentProject = canDeleteRepository && projectAllows('delete_project')
  const canInstallProjectArtifact = canCreateBurningTask && projectAllows('mark_flash_file')
  const canDownloadProjectArtifact = canDownloadRepository && projectAllows('download_file')
  const canDeleteProjectArtifact = canDeleteRepository && projectAllows('delete_file')

  const refreshTree = async () => {
    setTreeLoading(true)
    try {
      const res: any = await repositoryApi.getTree({ mode: 'online' })
      if (res?.code === 0) {
        setTreeRaw(res.data || [])
      }
    } catch (e: any) {
      const errDetail = e?.response?.data?.detail
      message.error(errDetail || '本地项目加载失败，请稍后重试')
      setTreeRaw([])
    } finally {
      setTreeLoading(false)
      setTreeInitialized(true)
    }
  }

  const patchTreeNodeByRepoId = (repoId: number | string | undefined, patcher: (node: any) => any) => {
    if (repoId === undefined || repoId === null || repoId === '') return
    const targetRepoId = String(repoId)
    const walk = (items: any[]): any[] =>
      (items || []).map((item) => {
        const nextChildren = Array.isArray(item.children) ? walk(item.children) : item.children
        const nextItem = nextChildren === item.children ? item : { ...item, children: nextChildren }
        return String(item.repo_id ?? '') === targetRepoId ? patcher(nextItem) : nextItem
      })
    setTreeRaw((prev) => walk(prev))
  }

  const refreshCodeartsConfig = async (projectKey = currentProjectKey) => {
    try {
      const params = { project_key: projectKey || undefined }
      const [res, statusRes]: any[] = await Promise.all([
        repositoryApi.getCodeartsConfig(params),
        repositoryApi.getCodeartsStatus(params),
      ])
      if (res?.code === 0) setCodeartsCfg(res.data || {})
      const connected = Boolean(statusRes?.code === 0 && statusRes?.data?.connected)
      setCodeartsConnected(connected)
      setCodeartsConnectionDetail(String(statusRes?.data?.detail || ''))
    } catch {
      setCodeartsConnected(false)
      setCodeartsConnectionDetail('CodeArts 连接检测失败')
    }
  }

  const inferRegionFromRepoId = (repoId?: string) => {
    const text = String(repoId || '').trim()
    const match = text.match(/^(cn-[a-z]+-\d+)_/)
    return match?.[1] || ''
  }

  const loadCodeartsFormDraft = () => {
    const draftKey = configurationMode === 'private' ? CODEARTS_PRIVATE_FORM_DRAFT_KEY : CODEARTS_FORM_DRAFT_KEY
    const secretDraftKey = configurationMode === 'private' ? CODEARTS_PRIVATE_FORM_SECRET_DRAFT_KEY : CODEARTS_FORM_SECRET_DRAFT_KEY
    let draft: Record<string, any> = {}
    let secretDraft: Record<string, any> = {}
    try {
      draft = JSON.parse(window.localStorage.getItem(draftKey) || '{}')
    } catch {
      draft = {}
    }
    try {
      secretDraft = JSON.parse(window.sessionStorage.getItem(secretDraftKey) || '{}')
    } catch {
      secretDraft = {}
    }
    return { ...draft, ...secretDraft }
  }

  const persistCodeartsFormDraft = (values: Record<string, any>) => {
    const draftKey = configurationMode === 'private' ? CODEARTS_PRIVATE_FORM_DRAFT_KEY : CODEARTS_FORM_DRAFT_KEY
    const secretDraftKey = configurationMode === 'private' ? CODEARTS_PRIVATE_FORM_SECRET_DRAFT_KEY : CODEARTS_FORM_SECRET_DRAFT_KEY
    const plainDraft = {
      domain_name: String(values.domain_name || '').trim(),
      username: String(values.username || '').trim(),
      region: String(values.region || '').trim(),
      project_id: String(values.project_id || '').trim(),
      repo_id_0: String(values.repo_id_0 || '').trim(),
    }
    const secretDraft = {
      password: String(values.password || ''),
    }
    window.localStorage.setItem(draftKey, JSON.stringify(plainDraft))
    window.sessionStorage.setItem(secretDraftKey, JSON.stringify(secretDraft))
  }

  useEffect(() => {
    refreshCodeartsConfig()
  }, [currentProjectKey])

  useEffect(() => {
    refreshTree()
  }, [])

  useEffect(() => {
    if (!isCreateProjectOpen) return
    const repoId0 = configurationMode === 'private'
      ? String(codeartsCfg?.private_repo_id || '')
      : Array.isArray(codeartsCfg?.repo_ids) && codeartsCfg.repo_ids.length > 0 ? String(codeartsCfg.repo_ids[0]) : ''
    const inferredRegion = inferRegionFromRepoId(repoId0)
    const values: any = configurationMode === 'private'
      ? { ...CODEARTS_PRIVATE_TEST_DEFAULTS, repository_mode: configurationMode }
      : {
          repository_mode: configurationMode,
          domain_name: codeartsCfg?.domain_name || '',
          username: codeartsCfg?.username || '',
          password: '',
          region: codeartsCfg?.region || inferredRegion || '',
          project_id: codeartsCfg?.project_id || '',
          repo_id_0: repoId0,
        }
    const draftValues = loadCodeartsFormDraft()
    const mergedValues = configurationMode === 'private'
      ? values
      : {
          ...values,
          ...draftValues,
          repository_mode: configurationMode,
          region: String(draftValues.region || values.region || inferRegionFromRepoId(draftValues.repo_id_0 || values.repo_id_0) || '').trim(),
        }
    if (isCreateProjectOpen) createProjectForm.setFieldsValue(mergedValues)
  }, [isCreateProjectOpen, codeartsCfg, createProjectForm, configurationMode])

  useEffect(() => {
    if (!treeInitialized) return

    const projects: Array<{ label: string; value: string; repositoryMode: CodeartsRepositoryMode }> = []
    const walk = (items: AnyNode[]) => {
      for (const n of items) {
        if (String(n.key).startsWith('proj_')) {
          projects.push({
            label: String(n.title),
            value: String(n.key),
            repositoryMode: n.repository_mode === 'private' ? 'private' : 'release',
          })
        }
        if (Array.isArray(n.children) && n.children.length > 0) walk(n.children as AnyNode[])
      }
    }
    walk(treeData)
    setProjectOptions(projects)
    const restoredProjectKey = currentProjectKey || initialProjectContextRef.current.projectKey
    const currentProject = projects.find((project) => project.value === restoredProjectKey)
    if (currentProject) {
      if (currentProjectKey !== currentProject.value) setCurrentProjectKey(currentProject.value)
      setRepositoryProjectContext({ projectKey: currentProject.value, projectName: currentProject.label })
      setExpandedKeys((prev) => (prev.includes(currentProject.value) ? prev : [...prev, currentProject.value]))
    } else if (projects.length > 0) {
      const fallbackProject = projects[0]
      setCurrentProjectKey(fallbackProject.value)
      setRepositoryProjectContext({ projectKey: fallbackProject.value, projectName: fallbackProject.label })
      setExpandedKeys([fallbackProject.value])
    }
    if (projects.length === 0) {
      setCurrentProjectKey('')
      setRepositoryProjectContext({ projectKey: '', projectName: '' })
    }
  }, [treeData, treeInitialized, currentProjectKey])

  const renderTreeTitle = (node: AnyNode) => {
    const icon = node.isLeaf ? <FileOutlined /> : <FolderOutlined />
    return (
      <span className="repository-tree-title">
        <span className="repository-tree-title-icon" style={{ color: node.isLeaf ? '#4045D6' : 'rgba(0,0,0,0.45)' }}>{icon}</span>
        <EllipsisText className="repository-tree-title-text" value={String(node.title)} />
      </span>
    )
  }

  const deriveProjectKey = () => {
    if (selectedNode?.project_id) return `proj_${selectedNode.project_id}`
    if (selectedNode?.node_type === 'project' && String(selectedNode.key).startsWith('proj_')) return String(selectedNode.key)
    if (currentProjectKey) return currentProjectKey
    return ''
  }

  const handleProjectChange = (projectKey: string) => {
    const selectedProject = projectOptions.find((item) => item.value === projectKey)
    setCurrentProjectKey(projectKey)
    setRepositoryProjectContext({ projectKey, projectName: selectedProject?.label || '' })
    setSelectedKeys([projectKey])
    setSelectedNodeKey(projectKey)
    setExpandedKeys([projectKey])
    setSearchKeyword('')
  }

  const loadMembers = async (projectKey: string) => {
    setMembersLoading(true)
    try {
      const res: any = await repositoryApi.listProjectMembers(projectKey)
      if (res?.code === 0) setMembers(Array.isArray(res.data) ? res.data : [])
    } catch {
      /* ignore */
    } finally {
      setMembersLoading(false)
    }
  }

  const loadUserDirectory = async () => {
    try {
      const res: any = await userApi.getList({ page: 1, page_size: 500 })
      if (res?.code === 0) {
        const users = Array.isArray(res.data) ? res.data : []
        const nextMap: Record<string, any> = {}
        for (const user of users) {
          const username = String(user?.username || '').trim()
          const id = String(user?.id ?? '').trim()
          if (username) nextMap[`username:${username}`] = user
          if (id) nextMap[`id:${id}`] = user
        }
        setUserDirectory(nextMap)
      }
    } catch {
      /* ignore */
    }
  }

  const loadPermissions = async (projectKey: string) => {
    setPermLoading(true)
    try {
      const res: any = await repositoryApi.getProjectPermissions(projectKey)
      if (res?.code === 0) {
        const next = res.data || {}
        setPermConfig(next)
        setEffectiveProjectPermissions({ ...(next?._effective_permissions || {}) })
        setCanManageCurrentProjectPermissions(Boolean(next?._can_manage_permissions))
        const nextGroup: 'admin' | 'member' = permGroup || 'admin'
        setPermGroup(nextGroup)
        setPermDraft({ ...(next?.[nextGroup] || {}) })
        setPermSaveNotice(null)
      }
    } catch {
      /* ignore */
    } finally {
      setPermLoading(false)
    }
  }

  useEffect(() => {
    if (!isMemberPermissionOpen || !currentProjectKey) return
    loadMembers(currentProjectKey)
    loadPermissions(currentProjectKey)
    loadUserDirectory()
  }, [isMemberPermissionOpen, currentProjectKey])

  useEffect(() => {
    if (!currentProjectKey) {
      setEffectiveProjectPermissions({})
      setCanManageCurrentProjectPermissions(false)
      return
    }
    loadPermissions(currentProjectKey)
  }, [currentProjectKey])

  const jumpToWizard = (repoId: string | number, filename: string, installSource: InstallSource) => {
    const ext = filename.split('.').pop()?.toLowerCase() || ''
    const osExtensions = ['iso', 'img', 'qcow2', 'ova', 'raw', 'qcow', 'vdi', 'vhd']
    const taskType = osExtensions.includes(ext) ? 'os' : 'board'
    navigate('/burning', { state: { openWizard: true, softwareId: Number(repoId), taskType, installSource } })
  }

  const showSyncBlockedModal = (detail: string) => {
    const parts = String(detail || '')
      .split('：')
      .map((item) => item.trim())
      .filter(Boolean)
    const title = parts[0] || '当前项目暂时无法同步'
    const body = parts.slice(1).join('：') || detail
    const lines = body
      .split('；')
      .map((item) => item.trim())
      .filter(Boolean)

    Modal.warning({
      title,
      width: 720,
      okText: '知道了',
      content: (
        <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', lineHeight: 1.8 }}>
          {lines.map((line, index) => (
            <div key={`${index}-${line}`}>{line}</div>
          ))}
        </div>
      ),
    })
  }

  const showCreateProjectValidationError = (errorInfo: any) => {
    const firstError = errorInfo?.errorFields?.find((field: any) => Array.isArray(field?.errors) && field.errors.length > 0)
    const msg = firstError?.errors?.[0]
    setCreateProjectError(msg || '请先完善表单信息')
    message.warning(msg || '请先完善表单信息')
  }

  const showCreateProjectRequestError = (error: any) => {
    const status = Number(error?.response?.status || 0)
    const detail = String(error?.response?.data?.detail || '').trim()

    if (!error?.response) {
      setCreateProjectError('本地后端服务不可用或网络请求失败，请确认后端已启动后重试。')
      message.error('本地后端服务不可用或网络请求失败，请确认后端已启动后重试。')
      return
    }

    setCreateProjectError(detail || `请求失败${status ? `（${status}）` : ''}，请稍后重试。`)
    message.error(detail || `请求失败${status ? `（${status}）` : ''}，请稍后重试。`)
  }

  const handleSyncCurrentProject = async () => {
    const projectKey = deriveProjectKey()
    if (!projectKey || !projectKey.startsWith('proj_')) {
      message.error('请先选择需要同步的项目')
      return
    }

    const projectNode = nodeMapRef.current.get(projectKey)
    const projectId = String(projectNode?.project_id || projectKey.replace(/^proj_/, '')).trim()

    if (!projectId) {
      message.error('未找到当前项目的 CodeArts 项目标识')
      return
    }
    const payload: { project_id: string; full_refresh: boolean } = {
      project_id: projectId,
      full_refresh: true,
    }

    setSyncingCodearts(true)
    const messageKey = `repository-sync-${projectKey}`
    const syncingText = '正在同步 CodeArts，请稍候...'
    setSyncTaskNotice({ type: 'info', message: syncingText })
    message.open({ key: messageKey, type: 'loading', content: syncingText, duration: 0 })
    try {
      const res: any = await repositoryApi.syncCodeartsProject(payload)
      if (res?.code === 0) {
        const syncedCount = Number(res?.data?.synced_count || 0)
        const successText = `CodeArts 同步成功，共同步 ${syncedCount} 个文件`
        setSyncTaskNotice({ type: 'success', message: successText })
        message.open({ key: messageKey, type: 'success', content: successText, duration: 3 })
        await refreshCodeartsConfig()
        await refreshTree()
        setCurrentProjectKey(projectKey)
        setRepositoryProjectContext({
          projectKey,
          projectName: projectOptions.find((item) => item.value === projectKey)?.label || projectId,
        })
        setSelectedKeys([projectKey])
        setSelectedNodeKey(projectKey)
      } else {
        const errorText = String(res?.message || res?.detail || '同步当前项目失败')
        setSyncTaskNotice({ type: 'error', message: errorText })
        message.open({ key: messageKey, type: 'error', content: errorText, duration: 5 })
      }
    } catch (e: any) {
      const detail = String(e?.response?.data?.detail || '').trim()
      if (e?.response?.status === 409 && detail) {
        setSyncTaskNotice({ type: 'error', message: detail })
        message.open({ key: messageKey, type: 'error', content: detail, duration: 5 })
        showSyncBlockedModal(detail)
      } else {
        const errorText = detail || '同步当前项目失败'
        setSyncTaskNotice({ type: 'error', message: errorText })
        message.open({ key: messageKey, type: 'error', content: errorText, duration: 5 })
      }
    } finally {
      setSyncingCodearts(false)
    }
  }

  const handleRepositoryModeChange = async (value: string | number) => {
    const nextMode: CodeartsRepositoryMode = value === 'private' ? 'private' : 'release'
    if (nextMode === repositoryMode) return
    const targetProject = projectOptions.find((project) => project.repositoryMode === nextMode)
    if (!targetProject) {
      setCreateProjectError(nextMode === 'private' ? '首次使用私有库，请配置仓库 ID' : '')
      setConfigurationModeOverride(nextMode)
      setIsCreateProjectOpen(true)
      return
    }
    handleProjectChange(targetProject.value)
    message.success(`已切换到${nextMode === 'private' ? '私有库' : '发布库'}项目，需要更新数据时请点击同步CodeArts`)
  }

  const handleDownloadArtifact = async (target: DownloadTarget, node: AnyNode, jumpAfterDownload = false) => {
    if (!node?.project_id || !node?.download_uri) return
    const messageKey = `repository-download-${node.repo_id || node.key}-${target}`
    message.open({
      key: messageKey,
      type: 'loading',
      content: target === 'server' ? '正在下载制品并传输到服务器，请稍候...' : '正在下载制品到本地，请稍候...',
      duration: 0,
    })
    setDownloadTaskNotice({
      type: 'info',
      message: target === 'server' ? '正在下载 CodeArts 制品并传输到服务器...' : '正在下载 CodeArts 制品到本地...',
    })
    setDownloading(true)
    try {
      const res: any = await repositoryApi.downloadCodeartsArtifactToServer({
        project_id: String(node.project_id),
        download_uri: String(node.download_uri || ''),
        name: String(node.title || 'CodeArts制品'),
        id: node.repo_id ? Number(node.repo_id) : undefined,
        target
      })
      if (res?.code === 0) {
        const responseLocationState = res?.data?.location_state || {}
        if (node.repo_id && Object.keys(responseLocationState).length > 0) {
          patchTreeNodeByRepoId(node.repo_id, (item) => mergeLocationStateIntoNode(item, responseLocationState))
        }
        const localPath = String(responseLocationState?.local_path || res?.data?.local_path || '').trim()
        const serverPath = String(responseLocationState?.server_path || res?.data?.server_path || '').trim()
        const serverTarget = String(responseLocationState?.server_target || res?.data?.server_target || '').trim()
        const locationLines = [
          localPath ? `本地： ${localPath}` : '',
          serverPath ? `服务器： ${serverPath}` : (serverTarget ? `服务器： ${serverTarget}` : ''),
        ].filter(Boolean)
        const locationText = locationLines.join('\n')
        if (target === 'server') {
          const targetServer = res?.data?.target_server
          if (targetServer && targetServer !== 'local') {
            message.open({ key: messageKey, type: 'success', content: `已下载并传输到目标服务器：${targetServer}`, duration: 5 })
          } else {
            const savedPath = String(serverPath || res?.data?.saved_path || '').trim()
            message.open({ key: messageKey, type: 'success', content: savedPath ? `已下载到服务器：${savedPath}` : '已下载到服务器', duration: 5 })
          }
        } else {
          const savedPath = String(localPath || res?.data?.saved_path || '').trim()
          message.open({ key: messageKey, type: 'success', content: savedPath ? `已下载到本地：${savedPath}` : '已下载到本地', duration: 5 })
        }
        setDownloadTaskNotice({
          type: 'success',
          message: locationText || (target === 'server'
            ? `制品已成功传输到服务器：${String(res?.data?.saved_path || res?.data?.target_server || '')}`
            : `制品已成功下载到本地：${String(res?.data?.saved_path || '')}`),
        })
        await refreshTree()
        const idToJump = node.repo_id ? Number(node.repo_id) : undefined
        if (jumpAfterDownload && idToJump) {
          jumpToWizard(idToJump, String(node.title || ''), target)
        }
      }
    } catch (e: any) {
      const errorText = e?.response?.data?.detail || (target === 'server' ? '下载到服务器失败' : '下载到本地失败')
      message.open({
        key: messageKey,
        type: 'error',
        content: errorText,
        duration: 6,
      })
      setDownloadTaskNotice({ type: 'error', message: errorText })
    } finally {
      setDownloading(false)
    }
  }

  const handleDeleteArtifact = async (scope: 'local' | 'server' | 'all', node: AnyNode) => {
    if (!node?.repo_id) return
    const titleMap = {
      local: '删除本地制品',
      server: '删除服务器制品',
      all: '删除全部制品',
    } as const
    setDeleteConfirmModal({
      open: true,
      node,
      scope,
      title: titleMap[scope],
    })
  }

  const handleDeleteConfirm = async () => {
    const node = deleteConfirmModal.node
    const scope = deleteConfirmModal.scope
    if (!node?.repo_id || !scope) return
    setDeleting(true)
    try {
      const res: any = await repositoryApi.deleteArtifact(Number(node.repo_id), scope)
      if (res?.code === 0) {
        message.success('删除成功')
        if (selectedNodeKey && node.key === selectedNodeKey) {
          setSelectedKeys([])
          setSelectedNodeKey('')
        }
        setDeleteConfirmModal({ open: false, node: null, scope: null, title: '' })
        await refreshTree()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const handleDeleteCurrentProject = async () => {
    if (!currentProjectKey) {
      message.error('请先选择项目')
      return
    }
    setDeletingProject(true)
    try {
      const res: any = await repositoryApi.deleteProject(currentProjectKey)
      if (res?.code === 0) {
        message.success('删除成功')
        setSelectedKeys([])
        setSelectedNodeKey('')
        setCurrentProjectKey('')
        setRepositoryProjectContext({ projectKey: '', projectName: '' })
        setDeleteProjectOpen(false)
        refreshTree()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '项目删除失败')
    } finally {
      setDeletingProject(false)
    }
  }

  const actionState = useMemo(() => {
    const node = selectedNode
    if (!node?.repo_id) {
      return {
        showOnlineInstall: false,
        installItems: [] as Array<{ key: InstallSource; label: string }>,
        directInstallSource: null as InstallSource | null,
        downloadItems: [] as Array<{ key: InstallSource; label: string }>,
        deleteItems: [] as Array<{ key: 'local' | 'server' | 'all'; label: string; danger?: boolean }>,
      }
    }
    const locationState = getNodeLocationState(node)
    const installItems: Array<{ key: InstallSource; label: string }> = []
    const downloadItems: Array<{ key: InstallSource; label: string }> = []
    const deleteItems: Array<{ key: 'local' | 'server' | 'all'; label: string; danger?: boolean }> = []

    if (!isCodeartsConnected && locationState.localExists) installItems.push({ key: 'local', label: '本地安装' })
    if (isCodeartsConnected && locationState.remoteDownloadable) {
      downloadItems.push({ key: 'server', label: locationState.serverExists ? '重新下载到服务器' : '下载到服务器' })
      downloadItems.push({ key: 'local', label: locationState.localExists ? '重新下载到本地' : '下载到本地' })
    }
    if (locationState.localExists && locationState.serverExists) {
      deleteItems.push({ key: 'local', label: '删除本地', danger: true })
      deleteItems.push({ key: 'server', label: '删除服务器', danger: true })
      deleteItems.push({ key: 'all', label: '删除全部', danger: true })
    } else if (locationState.localExists) {
      deleteItems.push({ key: 'local', label: '删除本地', danger: true })
    } else if (locationState.serverExists) {
      deleteItems.push({ key: 'server', label: '删除服务器', danger: true })
    }

    return {
      showOnlineInstall: isCodeartsConnected && locationState.remoteDownloadable,
      installItems,
      directInstallSource: installItems.length === 1 ? installItems[0].key : null,
      downloadItems,
      deleteItems,
    }
  }, [isCodeartsConnected, selectedNode])

  const handleOnlineInstallClick = (node: AnyNode) => {
    const locationState = getNodeLocationState(node)
    if (!isCodeartsConnected) {
      message.warning(codeartsConnectionDetail || 'CodeArts 当前无法连接，请使用已下载到本地的制品进行离线安装')
      return
    }
    if (!locationState.remoteDownloadable) {
      message.warning('当前制品没有可用的 CodeArts 下载地址')
      return
    }
    jumpToWizard(Number(node.repo_id), String(node.title || ''), 'codearts')
  }

  const moreMenuItems = useMemo(() => {
    const items: Array<{ key: string; label: string; danger?: boolean }> = []
    if (canSyncRepository) items.push({ key: 'create-project', label: '新增项目' })
    if (canInviteProjectMembers || canManageProjectPermissionsEffective) items.push({ key: 'member-permission', label: '项目成员及权限' })
    if (canDeleteCurrentProject) items.push({ key: 'delete-project', label: '删除当前项目', danger: true })
    return items
  }, [canDeleteCurrentProject, canInviteProjectMembers, canManageProjectPermissionsEffective, canSyncRepository])

  const handleMoreMenuClick = async ({ key }: { key: string }) => {
    if (key === 'create-project') {
      await refreshCodeartsConfig()
      setConfigurationModeOverride(null)
      setCreateProjectError('')
      setIsCreateProjectOpen(true)
    }
    if (key === 'member-permission') {
      if (!currentProjectKey) {
        message.error('请先选择项目')
        return
      }
      setIsMemberPermissionOpen(true)
    }
    if (key === 'delete-project') {
      setDeleteProjectOpen(true)
    }
  }

  const RepoFormField = ({
    label,
    name,
    form,
    children,
    rules,
    validateTrigger,
    validateStatus,
    help,
    valuePropName,
  }: {
    label: string
    name: string
    form: any
    children: ReactNode
    rules?: any[]
    validateTrigger?: string
    validateStatus?: 'success' | 'error' | 'validating'
    help?: ReactNode | null
    valuePropName?: string
  }) => (
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

  const createOrSyncProjectFormJSX = (
    <Form
      layout="vertical"
      form={createProjectForm}
      onValuesChange={(_, allValues) => persistCodeartsFormDraft(allValues)}
      onFinishFailed={(errorInfo) => {
        showCreateProjectValidationError(errorInfo)
      }}
      onFinish={async (values) => {
        setCreateProjectSubmitting(true)
        setCreateProjectError('')
        try {
          const repoIds = [String(values.repo_id_0 || '').trim()].filter(Boolean)
          const inferredRegion = inferRegionFromRepoId(repoIds[0])
          const payload: any = {
            enabled: true,
            repository_mode: configurationMode,
            domain_name: String(values.domain_name || '').trim(),
            username: String(values.username || '').trim(),
            password: String(values.password || '').trim() || undefined, // undefined if empty to avoid overriding with empty string
            region: String(values.region || inferredRegion || '').trim(),
            project_id: String(values.project_id || '').trim(),
            ...(configurationMode === 'private' ? { private_repo_id: repoIds[0] || '' } : { repo_ids: repoIds }),
          }
          persistCodeartsFormDraft(values)
          await repositoryApi.setCodeartsConfig(payload)
          const res: any = await repositoryApi.syncCodeartsProject({
            project_id: payload.project_id,
            full_refresh: true,
          })
          if (res?.code === 0) {
            const syncedCount = Number(res?.data?.synced_count || 0)
            const skippedCount = Number(res?.data?.skipped_count || 0)
            const successText = `同步成功，已落地 ${syncedCount} 个文件${skippedCount > 0 ? `，跳过 ${skippedCount} 个文件` : ''}`
            message.success(successText)
            setCreateProjectError('')
            setIsCreateProjectOpen(false)
            setConfigurationModeOverride(null)
            createProjectForm.resetFields()
            const newProjectKey = `proj_${payload.project_id}`
            await refreshTree()
            setCurrentProjectKey(newProjectKey)
            setRepositoryProjectContext({ projectKey: newProjectKey, projectName: payload.project_id })
            setSelectedKeys([newProjectKey])
            setSelectedNodeKey(newProjectKey)
            await refreshCodeartsConfig(newProjectKey)
          }
        } catch (e: any) {
          showCreateProjectRequestError(e)
        } finally {
          setCreateProjectSubmitting(false)
        }
      }}
    >
      {createProjectError ? (
        <Alert
          type="error"
          showIcon
          closable
          style={{ marginBottom: 16 }}
          message={createProjectError}
          onClose={() => setCreateProjectError('')}
        />
      ) : null}
      <Form.Item name="repository_mode" hidden>
        <Input name="repository_mode" autoComplete="off" />
      </Form.Item>
      {configurationMode === 'release' ? (
        <Form.Item name="repo_id_0" hidden>
          <Input name="repo_id_0" autoComplete="off" />
        </Form.Item>
      ) : null}
      <div
        style={{
          marginBottom: 18,
          padding: '8px 12px',
          border: '1px solid #8f96ff',
          borderRadius: 12,
          background: '#eef0ff',
          color: '#5a67f8',
          fontSize: 12,
          lineHeight: '20px',
        }}
      >
        配置CodeArts用户、项目ID等信息，用于获取当前用户项目下指定制品仓库信息
      </div>
      <RepoFormField label="IAM用户名" name="username" form={createProjectForm} rules={[{ required: true, message: '请输入IAM用户名' }]}>
        <Input
          name="username"
          autoComplete="username"
          placeholder="请输入入账号"
          prefix={<UserOutlined style={{ color: '#c9cdd4' }} />}
        />
      </RepoFormField>
      <RepoFormField
        label="IAM密码"
        name="password"
        form={createProjectForm}
        rules={[{ required: true, message: '请输入IAM密码' }]}
      >
        <Input.Password
          name="password"
          autoComplete="current-password"
          placeholder="请输入入密码"
          prefix={<LockOutlined style={{ color: '#c9cdd4' }} />}
        />
      </RepoFormField>
      <RepoFormField label="租户名(DOMAIN NAME)" name="domain_name" form={createProjectForm} rules={[{ required: true, message: '请输入租户名' }]}>
        <Input name="domain_name" autoComplete="organization" placeholder="请输入租户名" />
      </RepoFormField>
      <RepoFormField label="区域" name="region" form={createProjectForm} rules={[{ required: true, message: '请输入区域' }]}>
        <Input name="region" autoComplete="off" placeholder="例如：cn-east-3" />
      </RepoFormField>
      <RepoFormField label="项目ID" name="project_id" form={createProjectForm} rules={[{ required: true, message: '请输入项目ID' }]}>
        <Input name="project_id" autoComplete="off" placeholder="请输入项目ID" />
      </RepoFormField>
      {configurationMode === 'private' ? (
        <RepoFormField label="仓库ID" name="repo_id_0" form={createProjectForm} rules={[{ required: true, message: '请输入仓库ID' }]}>
          <Input name="repo_id_0" autoComplete="off" placeholder="请输入仓库ID" />
        </RepoFormField>
      ) : null}
    </Form>
  )

  const membersEnriched = useMemo(() => {
    return members.map((member) => {
      const selfProfile =
        userDirectory[`id:${String(member?.user_id ?? '')}`] ||
        userDirectory[`username:${String(member?.username || '')}`] ||
        null
      const inviterProfile =
        userDirectory[`id:${String(member?.inviter_user_id ?? '')}`] ||
        userDirectory[`username:${String(member?.inviter_username || '')}`] ||
        null

      return {
        ...member,
        display_name: firstFilled(member?.display_name, selfProfile?.display_name, member?.username),
        avatar_url: firstFilled(member?.avatar_url, selfProfile?.avatar_url, selfProfile?.avatar),
        inviter_display_name: firstFilled(member?.inviter_display_name, inviterProfile?.display_name, member?.inviter_username),
        inviter_avatar_url: firstFilled(member?.inviter_avatar_url, inviterProfile?.avatar_url, inviterProfile?.avatar),
      }
    })
  }, [members, userDirectory])

  const membersFiltered = useMemo(() => {
    const kw = memberKeyword.trim()
    if (!kw) return membersEnriched
    return membersEnriched.filter((m) => {
      const displayName = String(getUserDisplayName(m) || '')
      const username = String(m.username || '')
      return displayName.includes(kw) || username.includes(kw)
    })
  }, [membersEnriched, memberKeyword])

  useEffect(() => {
    if (!isMemberPermissionOpen) return
    const container = memberTableContainerRef.current
    if (!container) return
    const memberTableScrollThreshold = 900
    const updateFixedState = () => {
      const width = container.clientWidth || 0
      setMemberActionColumnFixed(width > 0 && width < memberTableScrollThreshold)
    }
    updateFixedState()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => updateFixedState())
    observer.observe(container)
    return () => observer.disconnect()
  }, [isMemberPermissionOpen, membersFiltered.length])

  const adminCount = useMemo(() => members.filter((m) => m.role === 'admin').length, [members])
  const memberCount = useMemo(() => members.filter((m) => m.role !== 'admin').length, [members])

  const memberModalCardStyle = {
    border: '1px solid #e8ebf2',
    borderRadius: 10,
    background: '#fff',
    boxShadow: '0 6px 18px rgba(15, 23, 42, 0.04)',
  } as const

  const memberModalPanelHeaderStyle = {
    padding: '12px 16px',
    background: '#f6f8fc',
    borderBottom: '1px solid #edf0f5',
    color: '#61656d',
    fontSize: 13,
    fontWeight: 600,
  } as const

  const memberColumns = [
    {
      title: '用户',
      dataIndex: 'username',
      key: 'username',
      width: 190,
      render: (_: string, record: any) =>
        renderUserIdentity(record, {
          avatarSize: 23,
          secondaryText: record.username && record.display_name && record.display_name !== record.username ? record.username : '',
        }),
    },
    {
      title: '加入时间',
      dataIndex: 'joined_at',
      key: 'joined_at',
      width: 180,
      render: (val: string) => formatDateTime(val),
    },
    {
      title: '邀请人',
      dataIndex: 'inviter_username',
      key: 'inviter_username',
      width: 170,
      render: (_: string, record: any) => {
        const inviterName = firstFilled(record?.inviter_display_name, record?.inviter_username, '-')
        if (inviterName === '-') return '-'
        return renderUserIdentity(
          {
            username: record?.inviter_username,
            display_name: record?.inviter_display_name,
            inviter_avatar_url: record?.inviter_avatar_url,
          },
          { avatarSize: 23 },
        )
      },
    },
    {
      title: '用户组',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (val: string) => (
        <span style={{ color: '#4a4f57', fontWeight: 500 }}>{val === 'admin' ? '管理员' : '成员'}</span>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 156,
      fixed: memberActionColumnFixed ? ('right' as const) : undefined,
      render: (_: any, record: any) => (
          <ActionButtonGroup compact>
            {canManageProjectPermissionsEffective ? (
              <ActionLinkButton
                onClick={() => {
                  setRoleChangingUser(record)
                  roleForm.setFieldsValue({ role: record.role || 'member' })
                  setIsRoleChangeOpen(true)
                }}
              >
                权限变更
              </ActionLinkButton>
            ) : null}
            {canDeleteProjectMembers ? (
              <ActionConfirm
                title="删除成员"
                description={`确认删除成员 ${record.username}？`}
                okText="确认删除"
                cancelText="取消"
                confirmLoading={deletingMemberId === Number(record.user_id)}
                onConfirm={async () => {
                  setDeletingMemberId(Number(record.user_id))
                  try {
                    const res: any = await repositoryApi.deleteProjectMember(currentProjectKey, Number(record.user_id))
                    if (res?.code === 0) {
                      message.success('删除成功')
                      setMembers((prev) => prev.filter((x) => x.user_id !== record.user_id))
                    }
                  } catch (e: any) {
                    message.error(e?.response?.data?.detail || '成员删除失败')
                  } finally {
                    setDeletingMemberId(null)
                  }
                }}
              >
                <ActionLinkButton danger>删除</ActionLinkButton>
              </ActionConfirm>
            ) : null}
          </ActionButtonGroup>
      ),
    },
  ]

  const fetchInviteCandidates = async (kw: string) => {
    setInviteCandidatesLoading(true)
    try {
      const res: any = await userApi.getList({ page: 1, page_size: 50, keyword: kw || undefined })
      if (res?.code === 0) setInviteCandidates(Array.isArray(res.data) ? res.data : [])
    } catch {
      /* ignore */
    } finally {
      setInviteCandidatesLoading(false)
    }
  }

  const openInviteModal = () => {
    if (!currentProjectKey) {
      message.error('请先选择项目')
      return
    }
    setInviteKeyword('')
    setInviteSelectedUsernames([])
    setInviteRole('member')
    setIsInviteOpen(true)
    loadUserDirectory()
    fetchInviteCandidates('')
  }

  const displayedInviteCandidates = useMemo(() => {
    const kw = inviteKeyword.trim()
    if (!kw) return inviteCandidates
    return inviteCandidates.filter((u) => {
      const displayName = String(getUserDisplayName(u) || '')
      const username = String(u.username || '')
      return displayName.includes(kw) || username.includes(kw)
    })
  }, [inviteCandidates, inviteKeyword])

  const selectedInviteUsers = useMemo(() => {
    return inviteSelectedUsernames.map((username) => {
      const profile =
        inviteCandidates.find((user) => String(user?.username || '') === username) ||
        userDirectory[`username:${username}`] ||
        null
      return {
        username,
        display_name: firstFilled(profile?.display_name, username),
        avatar_url: firstFilled(profile?.avatar_url, profile?.avatar),
      }
    })
  }, [inviteSelectedUsernames, inviteCandidates, userDirectory])

  const permissionsKeys = ['invite_user', 'delete_user', 'delete_project', 'mark_flash_file', 'download_file', 'delete_file'] as const
  const permissionsLabels: Record<(typeof permissionsKeys)[number], string> = {
    invite_user: '邀请用户',
    delete_user: '删除用户',
    delete_project: '删除项目',
    mark_flash_file: '烧录/安装文件',
    download_file: '下载文件',
    delete_file: '删除文件',
  }

  const togglePermAll = (checked: boolean) => {
    const next: Record<string, boolean> = {}
    for (const k of permissionsKeys) next[k] = checked
    setPermDraft(next)
  }

  const permAllChecked = useMemo(() => permissionsKeys.every((k) => Boolean(permDraft[k])), [permDraft])
  const permAllIndeterminate = useMemo(() => {
    const vals = permissionsKeys.map((k) => Boolean(permDraft[k]))
    return vals.some(Boolean) && !vals.every(Boolean)
  }, [permDraft])

  const membersPanelJSX = (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        {canInviteProjectMembers ? (
          <PagePrimaryButton onClick={openInviteModal}>邀请成员</PagePrimaryButton>
        ) : null}
        <Input
          className="pcids-list-search"
          placeholder="请输入成员名称"
          value={memberKeyword}
          onChange={(e) => setMemberKeyword(e.target.value)}
          allowClear
          prefix={<SearchOutlined />}
        />
      </div>
      <div ref={memberTableContainerRef} style={memberModalCardStyle}>
        <Table
          className={`repository-member-table ${memberActionColumnFixed ? 'repository-member-table--scrollable' : 'repository-member-table--fit'}`}
          columns={memberColumns as any}
          dataSource={membersFiltered}
          rowKey="user_id"
          loading={membersLoading}
          scroll={memberActionColumnFixed ? { x: 840 } : undefined}
          pagination={{
            pageSize: 5,
            showSizeChanger: false,
            showTotal: (t) =>
              renderListPaginationTotal(t, 5, () => {}, {
                pageSizeOptions: [5],
                disablePageSizeChange: true,
              }),
          }}
          size="middle"
          tableLayout={memberActionColumnFixed ? 'fixed' : 'auto'}
          style={{ borderRadius: 10, overflow: 'hidden' }}
          locale={{ emptyText: '暂无成员' }}
        />
      </div>
    </div>
  )

  const permissionsPanelJSX = (
    <div className="repository-permission-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <div style={{ fontWeight: 600, color: '#2b2f36' }}>权限设置</div>
        {canManageProjectPermissionsEffective ? (
          <Button
            type="primary"
            loading={permSaving}
            disabled={!currentProjectKey || permLoading}
            style={{ minWidth: 80, height: 32, borderRadius: 4, fontWeight: 600, boxShadow: 'none' }}
            onClick={async () => {
              if (!currentProjectKey) {
                message.error('请先选择项目')
                return
              }
              setPermSaving(true)
              try {
                const payload: any = { group: permGroup }
                for (const k of permissionsKeys) payload[k] = Boolean(permDraft[k])
                const res: any = await repositoryApi.setProjectPermissions(currentProjectKey, payload)
                if (res?.code === 0) {
                  const groupLabel = permGroup === 'admin' ? '管理员组' : '成员组'
                  message.success(`${groupLabel}权限设置已保存`)
                  setPermSaveNotice({ type: 'success', message: `${groupLabel}权限设置已保存` })
                  setPermConfig(res.data || {})
                  setEffectiveProjectPermissions({ ...(res.data?._effective_permissions || {}) })
                  setCanManageCurrentProjectPermissions(Boolean(res.data?._can_manage_permissions))
                }
              } catch (e: any) {
                message.error(e?.response?.data?.detail || '权限保存失败')
                setPermSaveNotice({ type: 'error', message: e?.response?.data?.detail || '权限保存失败' })
              } finally {
                setPermSaving(false)
              }
            }}
          >
            保存
          </Button>
        ) : null}
      </div>
      {permSaveNotice ? (
        <Alert
          showIcon
          closable
          type={permSaveNotice.type}
          message={permSaveNotice.message}
          onClose={() => setPermSaveNotice(null)}
          style={{ marginBottom: 14 }}
        />
      ) : null}
      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 190, ...memberModalCardStyle, overflow: 'hidden' }}>
          <div style={memberModalPanelHeaderStyle}>用户组</div>
          <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Button
              type={permGroup === 'admin' ? 'primary' : 'default'}
              style={{ height: 38, justifyContent: 'space-between', borderRadius: 8, fontWeight: 600, boxShadow: 'none' }}
              onClick={() => {
                setPermGroup('admin')
                setPermDraft({ ...(permConfig?.admin || {}) })
                setPermSaveNotice(null)
              }}
            >
              管理员（{adminCount}）
            </Button>
            <Button
              type={permGroup === 'member' ? 'primary' : 'default'}
              style={{ height: 38, justifyContent: 'space-between', borderRadius: 8, fontWeight: 600, boxShadow: 'none' }}
              onClick={() => {
                setPermGroup('member')
                setPermDraft({ ...(permConfig?.member || {}) })
                setPermSaveNotice(null)
              }}
            >
              成员（{memberCount}）
            </Button>
          </div>
        </div>
        <div style={{ flex: 1, ...memberModalCardStyle, overflow: 'hidden' }}>
          <div style={memberModalPanelHeaderStyle}>项目权限设置</div>
          <div style={{ padding: '14px 16px 16px' }}>
            <Checkbox indeterminate={permAllIndeterminate} checked={permAllChecked} onChange={(e) => togglePermAll(e.target.checked)}>
              全选
            </Checkbox>
            <div style={{ height: 14 }} />
            <Row gutter={[12, 12]}>
              {permissionsKeys.map((k) => (
                <Col span={12} key={k}>
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      minHeight: 40,
                      padding: '0 12px',
                      border: '1px solid #edf0f5',
                      borderRadius: 8,
                      background: '#fbfcfe',
                    }}
                  >
                    <Checkbox checked={Boolean(permDraft[k])} onChange={(e) => setPermDraft((prev) => ({ ...prev, [k]: e.target.checked }))}>
                      {permissionsLabels[k]}
                    </Checkbox>
                  </label>
                </Col>
              ))}
            </Row>
            {permLoading && <div style={{ marginTop: 10, color: 'rgba(0,0,0,0.45)' }}>加载中…</div>}
          </div>
        </div>
      </div>
    </div>
  )

  const detailPairs = useMemo(() => {
    if (!selectedNode) return []
    const repoDetail = (selectedNode.repo_detail || {}) as Record<string, any>
    const fileDetail = (selectedNode.file_detail || {}) as Record<string, any>
    const projectName = String(repoDetail.name || repoDetail.project_name || selectedNode.path_titles?.[0] || selectedNode.title || '-')
    const repoFormat = String(repoDetail.format || 'Generic')
    const descendantFiles = collectLeafFiles(selectedNode)
    const firstLeaf = descendantFiles[0]
    const firstLeafDetail = ((firstLeaf?.file_detail || {}) as Record<string, any>)
    const relativePath =
      selectedNode.node_type === 'project'
        ? '--'
        : String(
            selectedNode.display_path ||
              (Array.isArray(selectedNode.path_titles) ? `/${selectedNode.path_titles.slice(1).join('/')}` : '-') ||
              '-',
          )
    const downloadUrl =
      selectedNode.node_type === 'project'
        ? '--'
        : firstFilled(
            fileDetail.download_url,
            fileDetail.download_url_with_id,
            firstLeafDetail.download_url,
            firstLeafDetail.download_url_with_id,
            selectedNode.download_uri,
            '-',
          )
    const displaySize = normalizeDisplaySize(selectedNode.size, fileDetail.size || fileDetail.display_size || selectedNode.raw?.display_size)
    const totalFileCount = descendantFiles.length
    const totalBytes = descendantFiles.reduce((sum, item) => sum + Number(item.size || 0), 0)
    const aggregatedSize = formatBytes(totalBytes)
    const fileLocation = formatNodeFileLocation(selectedNode)
    const createdBy = firstFilled(fileDetail.created_user_name, fileDetail.createdBy, firstLeafDetail.created_user_name, firstLeafDetail.createdBy, repoDetail.created_user_name, repoDetail.createdUserName, '-')
    const createdTime = firstFilled(fileDetail.created_time, fileDetail.created, firstLeafDetail.created_time, firstLeafDetail.created, repoDetail.created_time, repoDetail.createdTime, '-')
    const modifiedBy = firstFilled(fileDetail.modified_user_name, fileDetail.modifiedBy, firstLeafDetail.modified_user_name, firstLeafDetail.modifiedBy, repoDetail.modified_user_name, repoDetail.modifiedUserName, '-')
    const modifiedTime = firstFilled(fileDetail.modified_time, fileDetail.modified_time_to_string, fileDetail.lastModified, firstLeafDetail.modified_time, firstLeafDetail.modified_time_to_string, firstLeafDetail.lastModified, repoDetail.modified_time, repoDetail.modifiedTime, '-')
    const repoDescription = firstFilled(repoDetail.description, repoDetail.project_desc, '-')

    if (selectedNode.node_type === 'project') {
      return [
        { label: '仓库名称', value: projectName },
        { label: '制品类型', value: repoFormat },
        { label: '相对路径', value: '--' },
        { label: '下载地址', value: '--' },
        { label: '创建人', value: createdBy },
        { label: '创建时间', value: formatDateTime(createdTime) },
        { label: '修改人', value: modifiedBy },
        { label: '修改时间', value: formatDateTime(modifiedTime) },
        { label: '制品数量 / 大小', value: `${repoDetail.artifact_count ?? totalFileCount} / ${repoDetail.total_size_mb ? `${repoDetail.total_size_mb} MB` : aggregatedSize}` },
        { label: '仓库描述', value: repoDescription },
      ]
    }

    if (selectedNode.node_type === 'folder' || selectedNode.node_type === 'repository') {
      return [
        { label: '仓库名称', value: projectName },
        { label: '制品类型', value: repoFormat },
        { label: '相对路径', value: relativePath },
        { label: '下载地址', value: downloadUrl },
        { label: '创建人', value: createdBy },
        { label: '创建时间', value: formatDateTime(createdTime) },
        { label: '修改人', value: modifiedBy },
        { label: '修改时间', value: formatDateTime(modifiedTime) },
        { label: '制品数量 / 大小', value: `${totalFileCount} / ${aggregatedSize}` },
      ]
    }

    if (selectedNode.node_type === 'file') {
      return [
        { label: '仓库名称', value: projectName },
        { label: '制品类型', value: repoFormat },
        { label: '相对路径', value: relativePath },
        { label: '下载地址', value: downloadUrl },
        { label: '发布版本', value: fileDetail.version || fileDetail.build_version || '-' },
        { label: '创建人', value: createdBy },
        { label: '创建时间', value: formatDateTime(createdTime) },
        { label: '修改人', value: modifiedBy },
        { label: '修改时间', value: formatDateTime(modifiedTime) },
        { label: '大小', value: displaySize },
        { label: '文件所在位置', value: fileLocation },
      ]
    }

    return [
      { label: '名称', value: String(selectedNode.title || '-') },
      { label: '相对路径', value: relativePath },
      { label: '仓库名称', value: projectName },
    ]
  }, [selectedNode])

  const checksumPairs = useMemo(() => {
    if (!selectedNode) return []
    if (selectedNode.node_type === 'project') return []
    const fileDetail = (selectedNode.file_detail || {}) as Record<string, any>
    const checksums = (fileDetail.checksums || {}) as Record<string, any>
    if (selectedNode.node_type === 'file') {
      const sha256 = findChecksumValue([fileDetail, checksums, selectedNode], 'sha256')
      const md5 = findChecksumValue([fileDetail, checksums, selectedNode], 'md5')
      return [
        { label: 'SHA-256', value: firstFilled(sha256, '--') },
        { label: 'MD5', value: firstFilled(md5, '--') },
      ]
    }
    return [
      { label: 'SHA-256', value: '--' },
      { label: 'MD5', value: '--' },
    ]
  }, [selectedNode])

  return (
    <>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
          <div className="client-page-title">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <h1>制品仓库</h1>
              <Tag
                color={isCodeartsConnected ? 'green' : 'default'}
                title={isCodeartsConnected ? '当前项目可正常连接 CodeArts' : codeartsConnectionDetail || '当前项目无法连接 CodeArts'}
                style={{ marginInlineEnd: 0 }}
              >
                {isCodeartsConnected ? 'CodeArts已连接' : 'CodeArts未连接'}
              </Tag>
            </div>
            <p className="client-page-subtitle">同步 CodeArts 项目、管理制品文件与成员权限</p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Segmented
            value={repositoryMode}
            disabled={!canSyncRepository || syncingCodearts}
            onChange={handleRepositoryModeChange}
            options={[
              { label: '发布库', value: 'release' },
              { label: '私有库', value: 'private' },
            ]}
          />
          <Permission code="repository:sync">
            <ActionLinkButton icon={<ReloadOutlined />} loading={syncingCodearts} onClick={handleSyncCurrentProject}>
              同步CodeArts
            </ActionLinkButton>
          </Permission>
          <Select
            value={currentProjectKey || undefined}
            style={{ width: 200 }}
            options={visibleProjectOptions}
            placeholder="选择项目"
            showSearch
            allowClear={false}
            optionFilterProp="label"
            filterOption={(input, option) => String(option?.label ?? '').toLowerCase().includes(input.trim().toLowerCase())}
            notFoundContent="暂无匹配项目"
            popupMatchSelectWidth={200}
            onChange={handleProjectChange}
            suffixIcon={<DownOutlined style={{ color: '#86909c', pointerEvents: 'none' }} />}
            className="rounded-select repository-project-select"
          />
          {moreMenuItems.length > 0 ? (
            <Dropdown
              menu={{ items: moreMenuItems as any, onClick: handleMoreMenuClick as any }}
              trigger={['click']}
            >
              <Button
                type="text"
                aria-label="更多项目操作"
                icon={<EllipsisOutlined style={{ fontSize: 22 }} />}
                style={{ width: 36, height: 36, padding: 0, boxShadow: 'none' }}
              />
            </Dropdown>
          ) : null}
        </div>
      </div>
      <ActionConfirmDialog
        title={deleteConfirmModal.title || '删除制品'}
        open={deleteConfirmModal.open}
        onCancel={() => setDeleteConfirmModal({ open: false, node: null, scope: null, title: '' })}
        onConfirm={handleDeleteConfirm}
        okText="删除"
        cancelText="取消"
        confirmLoading={deleting}
        getContainer={false}
        destroyOnHidden
      >
        确认删除该制品文件？
      </ActionConfirmDialog>

      <ActionConfirmDialog
        title="删除当前项目"
        open={deleteProjectOpen}
        onCancel={() => {
          if (deletingProject) return
          setDeleteProjectOpen(false)
        }}
        onConfirm={handleDeleteCurrentProject}
        okText="确认删除"
        cancelText="取消"
        confirmLoading={deletingProject}
        getContainer={false}
        destroyOnHidden
      >
        确认删除当前项目？
      </ActionConfirmDialog>

      {syncTaskNotice ? (
        <Alert
          showIcon
          closable
          type={syncTaskNotice.type}
          message={syncTaskNotice.message}
          onClose={() => setSyncTaskNotice(null)}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      <div style={{ display: 'flex', gap: 16, height: 'calc(100vh - 180px)' }}>
        <div className="repository-tree-panel" style={{ width: 260, background: '#fff', border: '1px solid #f0f0f0', borderRadius: 6, padding: 12, display: 'flex', flexDirection: 'column' }}>
          <Input
            id="repository-tree-search"
            name="repositoryKeyword"
            autoComplete="off"
            placeholder="请输入搜索关键词"
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            allowClear
            prefix={<SearchOutlined />}
            className="pcids-list-search"
            style={{ marginBottom: 12 }}
          />
          <Spin spinning={treeLoading} wrapperClassName="tree-spin-wrapper repository-tree-spin" style={{ flex: 1, overflow: 'hidden' }}>
            <div ref={treeContainerRef} className="repository-tree-scroll">
              <Tree
                className="repository-file-tree"
                treeData={filteredTreeData}
                titleRender={renderTreeTitle as any}
                expandedKeys={expandedKeys}
                onExpand={(keys) => setExpandedKeys(keys as string[])}
                selectedKeys={selectedKeys}
                onSelect={(keys, info) => {
                  const nextKeys = keys as string[]
                  setSelectedKeys(nextKeys)
                  const key = nextKeys[0] ? String(nextKeys[0]) : ''
                  setSelectedNodeKey(key)
                  if (info?.node?.project_id) {
                    const projectKey = `proj_${info.node.project_id}`
                    const projectName = String(projectOptions.find((item) => item.value === projectKey)?.label || '').trim()
                    setCurrentProjectKey(projectKey)
                    setRepositoryProjectContext({ projectKey, projectName })
                  }
                  if (String(key).startsWith('proj_')) {
                    const projectName = String(info?.node?.title || projectOptions.find((item) => item.value === String(key))?.label || '').trim()
                    setCurrentProjectKey(String(key))
                    setRepositoryProjectContext({ projectKey: String(key), projectName })
                    setExpandedKeys((prev) => (prev.includes(String(key)) ? prev : [...prev, String(key)]))
                  }
                }}
                showLine
                blockNode
              />
            </div>
          </Spin>
        </div>

        <div style={{ flex: 1, background: '#fff', border: '1px solid #f0f0f0', borderRadius: 6, padding: 24, overflow: 'auto' }}>
          {!selectedNode && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'rgba(0,0,0,0.45)' }}>
              <img src={EmptyStateIllustration} alt="Empty State" style={{ width: 600, maxWidth: '80%', marginBottom: 20 }} />
              <div>请选择左侧项目/制品查看详细信息</div>
            </div>
          )}

          {selectedNode && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
                <div style={{ fontSize: 18, fontWeight: 600 }}>详细信息</div>
                {selectedNode.repo_id && (
                  <Space size={16}>
                    {actionState.showOnlineInstall && canDownloadProjectArtifact && canInstallProjectArtifact ? (
                      <PagePrimaryButton loading={downloading} onClick={() => handleOnlineInstallClick(selectedNode)}>
                        在线安装
                      </PagePrimaryButton>
                    ) : null}
                    {actionState.directInstallSource && canInstallProjectArtifact ? (
                      <PagePrimaryButton
                        onClick={() => jumpToWizard(Number(selectedNode.repo_id), String(selectedNode.title || ''), actionState.directInstallSource as InstallSource)}
                      >
                        离线安装
                      </PagePrimaryButton>
                    ) : null}
                    {!actionState.directInstallSource && actionState.installItems.length > 0 && canInstallProjectArtifact ? (
                      <Dropdown
                        menu={{
                          items: actionState.installItems,
                          onClick: ({ key }) => jumpToWizard(Number(selectedNode.repo_id), String(selectedNode.title || ''), key as InstallSource),
                        }}
                      >
                        <PagePrimaryButton>
                          离线安装 <DownOutlined />
                        </PagePrimaryButton>
                      </Dropdown>
                    ) : null}
                    {actionState.downloadItems.length > 0 && canDownloadProjectArtifact ? (
                      <Dropdown
                        menu={{
                          items: actionState.downloadItems,
                          onClick: ({ key }) => handleDownloadArtifact(key as DownloadTarget, selectedNode, false),
                        }}
                        trigger={['click']}
                        disabled={downloading}
                      >
                        <ActionLinkButton>
                          下载 <DownOutlined />
                        </ActionLinkButton>
                      </Dropdown>
                    ) : null}
                    {actionState.deleteItems.length > 0 && canDeleteProjectArtifact ? (
                      <Dropdown
                        menu={{
                          items: actionState.deleteItems,
                          onClick: ({ key }) => handleDeleteArtifact(key as 'local' | 'server' | 'all', selectedNode),
                        }}
                        trigger={['click']}
                      >
                        <ActionLinkButton danger>
                          删除 <DownOutlined />
                        </ActionLinkButton>
                      </Dropdown>
                    ) : null}
                  </Space>
                )}
              </div>
              {downloadTaskNotice ? (
                <Alert
                  showIcon
                  closable
                  type={downloadTaskNotice.type}
                  message={downloadTaskNotice.message}
                  onClose={() => setDownloadTaskNotice(null)}
                  style={{ marginBottom: 20 }}
                />
              ) : null}
              <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', rowGap: 16, columnGap: 16 }}>
                {detailPairs.map((p) => (
                  <div key={p.label} style={{ display: 'contents' }}>
                    <div style={{ color: 'rgba(0,0,0,0.65)' }}>{p.label}</div>
                    <div style={{ color: 'rgba(0,0,0,0.88)', wordBreak: 'break-all', whiteSpace: 'pre-line' }}>{p.value}</div>
                  </div>
                ))}
              </div>
              {checksumPairs.length > 0 ? (
                <div style={{ marginTop: 32 }}>
                  <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 24 }}>校验和</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', rowGap: 16, columnGap: 16 }}>
                    {checksumPairs.map((p) => (
                      <div key={p.label} style={{ display: 'contents' }}>
                        <div style={{ color: 'rgba(0,0,0,0.65)' }}>{p.label}</div>
                        <div style={{ color: 'rgba(0,0,0,0.88)', wordBreak: 'break-all' }}>{p.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>

      <Modal
        className="pcids-modal pcids-modal--xl repository-member-permission-modal"
        title="项目成员及权限"
        open={isMemberPermissionOpen}
        footer={null}
        destroyOnHidden
        onCancel={() => setIsMemberPermissionOpen(false)}
      >
        <Tabs
          animated={false}
          tabBarStyle={{ marginBottom: 16 }}
          items={[
            { key: 'members', label: '项目成员', children: membersPanelJSX },
            { key: 'permissions', label: '权限设置', children: permissionsPanelJSX },
          ]}
        />
      </Modal>

      <Modal
        className="pcids-modal pcids-modal--form user-form-modal"
        title="新建项目"
        open={isCreateProjectOpen}
        okText="确 定"
        cancelText="取 消"
        confirmLoading={createProjectSubmitting}
        destroyOnHidden
        onOk={async () => {
          try {
            await createProjectForm.validateFields()
            createProjectForm.submit()
          } catch (errorInfo) {
            showCreateProjectValidationError(errorInfo)
          }
        }}
        onCancel={() => {
          if (createProjectSubmitting) return
          setCreateProjectError('')
          setIsCreateProjectOpen(false)
          setConfigurationModeOverride(null)
          createProjectForm.resetFields()
        }}
      >
        {createOrSyncProjectFormJSX}
      </Modal>
      <Modal
        className="pcids-modal pcids-modal--form pcids-modal--body-tight"
        title="邀请成员"
        open={isInviteOpen}
        destroyOnHidden
        footer={null}
        onCancel={() => {
          if (inviteSubmitting) return
          setIsInviteOpen(false)
        }}
      >
        <div style={{ padding: '14px 10px 0', display: 'flex', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0, border: '1px solid #e8ebf2', borderRadius: 4, overflow: 'hidden', background: '#fff' }}>
            <div style={{ height: 42, padding: '0 14px', display: 'flex', alignItems: 'center', background: '#f5f7fb', borderBottom: '1px solid #e8ebf2', color: '#4a4f57', fontWeight: 600 }}>
              从组织架构邀请
            </div>
            <div style={{ padding: '12px 12px 0' }}>
              <Input
                className="pcids-list-search"
                placeholder="请输入关键字"
                value={inviteKeyword}
                allowClear
                onChange={(e) => setInviteKeyword(e.target.value)}
                onPressEnter={(e: any) => fetchInviteCandidates(String(e?.target?.value || ''))}
                prefix={<SearchOutlined />}
                suffix={inviteCandidatesLoading ? <Spin size="small" /> : null}
              />
            </div>
            <div style={{ height: 276, overflow: 'auto', marginTop: 10 }}>
              {inviteCandidatesLoading ? (
                <div style={{ color: 'rgba(0,0,0,0.45)', padding: '32px 12px', textAlign: 'center' }}>加载中…</div>
              ) : displayedInviteCandidates.length === 0 ? (
                <div style={{ color: 'rgba(0,0,0,0.45)', padding: '32px 12px', textAlign: 'center' }}>暂无数据</div>
              ) : (
                displayedInviteCandidates.map((u) => {
                  const username = String(u.username || '')
                  const checked = inviteSelectedUsernames.includes(username)
                  return (
                    <div
                      key={username}
                      onClick={() => {
                        setInviteSelectedUsernames((prev) => (checked ? prev.filter((x) => x !== username) : Array.from(new Set([...prev, username]))))
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 12,
                        minHeight: 54,
                        padding: '0 12px',
                        cursor: 'pointer',
                        background: checked ? '#f0f4ff' : '#fff',
                        borderTop: '1px solid #edf0f5',
                      }}
                    >
                      {renderUserIdentity(u, {
                        avatarSize: 23,
                        secondaryText: u.username && u.display_name && u.display_name !== u.username ? u.username : '',
                      })}
                      <Checkbox
                        checked={checked}
                        onChange={(e: any) => {
                          e.stopPropagation()
                          const nextChecked = e.target.checked
                          setInviteSelectedUsernames((prev) => (nextChecked ? Array.from(new Set([...prev, username])) : prev.filter((x) => x !== username)))
                        }}
                      />
                    </div>
                  )
                })
              )}
            </div>
          </div>
          <div style={{ flex: 1, minWidth: 0, border: '1px solid #e8ebf2', borderRadius: 4, overflow: 'hidden', background: '#fff' }}>
            <div style={{ height: 42, padding: '0 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#f5f7fb', borderBottom: '1px solid #e8ebf2' }}>
              <span style={{ color: '#4a4f57', fontWeight: 600 }}>已选成员</span>
              <ActionLinkButton danger onClick={() => setInviteSelectedUsernames([])}>
                清空
              </ActionLinkButton>
            </div>
            <div style={{ height: 332, overflow: 'auto' }}>
              {selectedInviteUsers.length === 0 ? (
                <div style={{ color: 'rgba(0,0,0,0.45)', padding: '32px 12px', textAlign: 'center' }}>请选择用户</div>
              ) : (
                selectedInviteUsers.map((user) => (
                  <div
                    key={user.username}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 12,
                      minHeight: 54,
                      padding: '0 14px',
                      borderTop: '1px solid #edf0f5',
                    }}
                  >
                    {renderUserIdentity(user, {
                      avatarSize: 23,
                      secondaryText: user.username && user.display_name && user.display_name !== user.username ? user.username : '',
                    })}
                    <Button
                      type="text"
                      icon={<DeleteOutlined style={{ color: '#8b9098' }} />}
                      onClick={() => setInviteSelectedUsernames((prev) => prev.filter((x) => x !== user.username))}
                    />
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '18px 10px 18px', gap: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#4a4f57', whiteSpace: 'nowrap' }}>
            <span style={{ fontWeight: 500 }}>添加为：</span>
            <Select
              value={inviteRole}
              variant="borderless"
              suffixIcon={<DownOutlined style={{ color: '#6b74ff' }} />}
              popupMatchSelectWidth={120}
              style={{ minWidth: 92, color: '#5062f6', fontWeight: 600 }}
              onChange={(v) => setInviteRole(v)}
              options={[
                { label: '管理员', value: 'admin' },
                { label: '成员', value: 'member' },
              ]}
            />
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <PageSecondaryButton
              onClick={() => {
                if (inviteSubmitting) return
                setIsInviteOpen(false)
              }}
            >
              取消
            </PageSecondaryButton>
            <PagePrimaryButton
              loading={inviteSubmitting}
              onClick={async () => {
                if (inviteSelectedUsernames.length === 0) {
                  message.error('请选择用户')
                  return
                }
                setInviteSubmitting(true)
                try {
                  const results = await Promise.allSettled(
                    inviteSelectedUsernames.map((username) => repositoryApi.inviteProjectMember(currentProjectKey, { username, role: inviteRole })),
                  )
                  const okCount = results.filter((r) => r.status === 'fulfilled' && (r as any).value?.code === 0).length
                  const failCount = results.length - okCount
                  if (okCount > 0 && failCount === 0) message.success('邀请成功')
                  if (okCount > 0 && failCount > 0) message.warning(`已邀请 ${okCount} 人，${failCount} 人邀请失败`)
                  if (okCount === 0) {
                    const rejected = results.find((r) => r.status === 'rejected') as PromiseRejectedResult | undefined
                    const reason = rejected?.reason
                    message.error(reason?.response?.data?.detail || '邀请失败')
                    return
                  }
                  setIsInviteOpen(false)
                  setInviteSelectedUsernames([])
                  const listRes: any = await repositoryApi.listProjectMembers(currentProjectKey)
                  if (listRes?.code === 0) setMembers(Array.isArray(listRes.data) ? listRes.data : [])
                } catch (e: any) {
                  message.error(e?.response?.data?.detail || '邀请失败')
                } finally {
                  setInviteSubmitting(false)
                }
              }}
            >
              确定
            </PagePrimaryButton>
          </div>
        </div>
      </Modal>

      <Modal
        title="权限变更"
        className="pcids-modal pcids-modal--compact"
        open={isRoleChangeOpen}
        okText="确 定"
        cancelText="取 消"
        onOk={() => roleForm.submit()}
        onCancel={() => setIsRoleChangeOpen(false)}
      >
        <Form
          layout="vertical"
          form={roleForm}
          onFinish={async (values) => {
            if (!roleChangingUser) return
            try {
              const res: any = await repositoryApi.updateProjectMemberRole(currentProjectKey, Number(roleChangingUser.user_id), { role: values.role })
              if (res?.code === 0) {
                message.success('更新成功')
                setIsRoleChangeOpen(false)
                setMembers((prev) => prev.map((m) => (m.user_id === roleChangingUser.user_id ? { ...m, role: values.role } : m)))
              }
            } catch {
              message.error('更新失败')
            }
          }}
        >
          <Form.Item label="用户组" name="role" rules={[{ required: true, message: '请选择用户组' }]}>
            <Select
              options={[
                { label: '管理员', value: 'admin' },
                { label: '成员', value: 'member' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
      </div>
    </>
  )
}

export default Repository
