import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Checkbox,
  Col,
  Dropdown,
  Form,
  Input,
  message,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tree,
  Upload,
} from 'antd'
import type { DataNode } from 'antd/es/tree'
import {
  DeleteOutlined,
  DownOutlined,
  EllipsisOutlined,
  FileOutlined,
  FolderOutlined,
  PlusOutlined,
  ReloadOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { repositoryApi, userApi } from '../../services/api'

type AnyNode = DataNode & Record<string, any>

function triggerBrowserDownload(blobData: Blob, filename: string) {
  const blob = blobData instanceof Blob ? blobData : new Blob([blobData])
  const objectUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(objectUrl)
}

function formatDateTime(val?: string | null) {
  if (!val) return '-'
  return val.replace('T', ' ').substring(0, 19)
}

function formatBytes(val?: number | null) {
  if (!val && val !== 0) return '-'
  const n = Number(val)
  if (!Number.isFinite(n)) return '-'
  if (n < 1024) return `${n} B`
  const kb = n / 1024
  if (kb < 1024) return `${kb.toFixed(2)} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${mb.toFixed(2)} MB`
  const gb = mb / 1024
  return `${gb.toFixed(2)} GB`
}

function pickDisplaySize(size?: number | null, displaySize?: string | null) {
  if (displaySize) return displaySize
  return formatBytes(size)
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

function deriveApiBaseUrl() {
  return window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://127.0.0.1:8000/api'
    : '/api'
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

function guessCodeartsMetaFromKey(key: string) {
  const m = /^ver_(.+?)_(.+?)_(.+)$/.exec(key)
  if (!m) return null
  return { project_id: m[1], package_id: m[2], version_id: m[3] }
}

const CODEARTS_FORM_DRAFT_KEY = 'pcids.repository.codeartsFormDraft'
const CODEARTS_FORM_SECRET_DRAFT_KEY = 'pcids.repository.codeartsFormSecretDraft'

const Repository: React.FC = () => {
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeRaw, setTreeRaw] = useState<any[]>([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [selectedNodeKey, setSelectedNodeKey] = useState<string>('')
  const nodeMapRef = useRef<Map<string, AnyNode>>(new Map())

  const [codeartsCfg, setCodeartsCfg] = useState<any>({})
  const [projectOptions, setProjectOptions] = useState<Array<{ label: string; value: string }>>([])
  const [currentProjectKey, setCurrentProjectKey] = useState<string>('')

  const [isCreateProjectOpen, setIsCreateProjectOpen] = useState(false)
  const [isMemberPermissionOpen, setIsMemberPermissionOpen] = useState(false)
  const [createProjectSubmitting, setCreateProjectSubmitting] = useState(false)
  const [createProjectForm] = Form.useForm()

  const [membersLoading, setMembersLoading] = useState(false)
  const [members, setMembers] = useState<any[]>([])
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
  const [permGroup, setPermGroup] = useState<'admin' | 'member'>('admin')
  const [permDraft, setPermDraft] = useState<Record<string, boolean>>({})
  const [downloading, setDownloading] = useState(false)

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
          repo_detail: it.repo_detail ?? null,
          file_detail: it.file_detail ?? null,
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

  const filteredTreeData = useMemo(() => filterTreeByKeyword(treeData, searchKeyword), [treeData, searchKeyword])

  const isCodeartsConnected = useMemo(() => {
    const enabled = Boolean(codeartsCfg?.enabled)
    const baseUrl = String(codeartsCfg?.base_url || '').trim()
    const tokenPresent = Boolean(codeartsCfg?.token_present)
    const username = String(codeartsCfg?.username || '').trim()
    return enabled && (Boolean(baseUrl) || tokenPresent || Boolean(username))
  }, [codeartsCfg])

  const refreshTree = async () => {
    setTreeLoading(true)
    try {
      const res: any = await repositoryApi.getTree({ mode: 'offline' })
      if (res?.code === 0) {
        setTreeRaw(res.data || [])
      }
    } catch (e: any) {
      const errDetail = e?.response?.data?.detail
      message.error(errDetail || '本地项目加载失败，请稍后重试')
      setTreeRaw([])
    } finally {
      setTreeLoading(false)
    }
  }

  const refreshCodeartsConfig = async () => {
    try {
      const res: any = await repositoryApi.getCodeartsConfig()
      if (res?.code === 0) setCodeartsCfg(res.data || {})
    } catch {
      /* ignore */
    }
  }

  const inferRegionFromRepoId = (repoId?: string) => {
    const text = String(repoId || '').trim()
    const match = text.match(/^(cn-[a-z]+-\d+)_/)
    return match?.[1] || ''
  }

  const loadCodeartsFormDraft = () => {
    let draft: Record<string, any> = {}
    let secretDraft: Record<string, any> = {}
    try {
      draft = JSON.parse(window.localStorage.getItem(CODEARTS_FORM_DRAFT_KEY) || '{}')
    } catch {
      draft = {}
    }
    try {
      secretDraft = JSON.parse(window.sessionStorage.getItem(CODEARTS_FORM_SECRET_DRAFT_KEY) || '{}')
    } catch {
      secretDraft = {}
    }
    return { ...draft, ...secretDraft }
  }

  const persistCodeartsFormDraft = (values: Record<string, any>) => {
    const plainDraft = {
      domain_name: String(values.domain_name || '').trim(),
      username: String(values.username || '').trim(),
      region: String(values.region || '').trim(),
      tenant_name: String(values.tenant_name || '').trim(),
      tenant_id: String(values.tenant_id || '').trim(),
      project_id: String(values.project_id || '').trim(),
      repo_id_0: String(values.repo_id_0 || '').trim(),
      repo_ids_extra: Array.isArray(values.repo_ids_extra) ? values.repo_ids_extra.map((x: any) => String(x || '').trim()).filter(Boolean) : [],
      download_username: String(values.download_username || '').trim(),
    }
    const secretDraft = {
      password: String(values.password || ''),
      download_password: String(values.download_password || ''),
    }
    window.localStorage.setItem(CODEARTS_FORM_DRAFT_KEY, JSON.stringify(plainDraft))
    window.sessionStorage.setItem(CODEARTS_FORM_SECRET_DRAFT_KEY, JSON.stringify(secretDraft))
  }

  useEffect(() => {
    refreshCodeartsConfig()
    refreshTree()
  }, [])

  useEffect(() => {
    if (!isCreateProjectOpen) return
    const repoId0 = Array.isArray(codeartsCfg?.repo_ids) && codeartsCfg.repo_ids.length > 0 ? String(codeartsCfg.repo_ids[0]) : ''
    const inferredRegion = inferRegionFromRepoId(repoId0)
    const repoIdsExtra =
      Array.isArray(codeartsCfg?.repo_ids) && codeartsCfg.repo_ids.length > 1 ? codeartsCfg.repo_ids.slice(1).map((x: any) => String(x)) : []
    const values: any = {
      domain_name: codeartsCfg?.domain_name || '',
      username: codeartsCfg?.username || '',
      password: '',
      region: codeartsCfg?.region || inferredRegion || '',
      tenant_name: codeartsCfg?.tenant_name || '',
      tenant_id: codeartsCfg?.tenant_id || '',
      project_id: codeartsCfg?.project_id || '',
      repo_id_0: repoId0,
      repo_ids_extra: repoIdsExtra,
      download_username: codeartsCfg?.download_username || '',
      download_password: '',
    }
    const draftValues = loadCodeartsFormDraft()
    const mergedValues = {
      ...values,
      ...draftValues,
      region: String(draftValues.region || values.region || inferRegionFromRepoId(draftValues.repo_id_0 || values.repo_id_0) || '').trim(),
    }
    if (isCreateProjectOpen) createProjectForm.setFieldsValue(mergedValues)
  }, [isCreateProjectOpen, codeartsCfg, createProjectForm])

  useEffect(() => {
    const projects: Array<{ label: string; value: string }> = []
    const walk = (items: AnyNode[]) => {
      for (const n of items) {
        if (String(n.key).startsWith('proj_')) {
          projects.push({ label: String(n.title), value: String(n.key) })
        }
        if (Array.isArray(n.children) && n.children.length > 0) walk(n.children as AnyNode[])
      }
    }
    walk(treeData)
    setProjectOptions(projects)
    if (!currentProjectKey && projects.length > 0) setCurrentProjectKey(projects[0].value)
    if (projects.length === 0) setCurrentProjectKey('')
  }, [treeData])

  const renderTreeTitle = (node: AnyNode) => {
    const icon = node.isLeaf ? <FileOutlined /> : <FolderOutlined />
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, width: '100%' }}>
        <span style={{ color: node.isLeaf ? '#4045D6' : 'rgba(0,0,0,0.45)' }}>{icon}</span>
        <span style={{ color: 'rgba(0,0,0,0.88)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {String(node.title)}
        </span>
      </span>
    )
  }

  const deriveProjectKey = () => {
    if (selectedNode?.project_id) return `proj_${selectedNode.project_id}`
    if (selectedNode?.node_type === 'project' && String(selectedNode.key).startsWith('proj_')) return String(selectedNode.key)
    if (currentProjectKey) return currentProjectKey
    return ''
  }

  const ensureProjectKey = () => {
    const projectKey = deriveProjectKey()
    if (!projectKey) {
      message.error('请先选择项目')
      return ''
    }
    setCurrentProjectKey(projectKey)
    return projectKey
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

  const loadPermissions = async (projectKey: string) => {
    setPermLoading(true)
    try {
      const res: any = await repositoryApi.getProjectPermissions(projectKey)
      if (res?.code === 0) {
        const next = res.data || {}
        setPermConfig(next)
        const nextGroup: 'admin' | 'member' = permGroup || 'admin'
        setPermGroup(nextGroup)
        setPermDraft({ ...(next?.[nextGroup] || {}) })
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
  }, [isMemberPermissionOpen, currentProjectKey])

  const handleDownloadToServer = async () => {
    if (!selectedNode || !selectedNode.project_id || !selectedNode.download_uri) return
    setDownloading(true)
    try {
      const res: any = await repositoryApi.downloadCodeartsArtifactToServer({
        project_id: String(selectedNode.project_id),
        download_uri: String(selectedNode.download_uri || ''),
        name: String(selectedNode.title || 'CodeArts制品'),
        id: selectedNode.repo_id ? Number(selectedNode.repo_id) : undefined,
        target: 'server'
      })
      if (res?.code === 0) {
        const targetServer = res?.data?.target_server
        if (targetServer && targetServer !== 'local') {
          message.success(`已下载并传输到目标服务器：${targetServer}`)
        } else {
          const savedPath = String(res?.data?.saved_path || '').trim()
          message.success(savedPath ? `已下载到本地服务器：${savedPath}` : '已下载到服务器')
        }
        refreshTree()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '传输到服务器失败')
    } finally {
      setDownloading(false)
    }
  }

  const handleDownloadToLocal = async () => {
    if (!selectedNode || !selectedNode.project_id || !selectedNode.download_uri) return
    setDownloading(true)
    try {
      const res: any = await repositoryApi.downloadCodeartsArtifactToServer({
        project_id: String(selectedNode.project_id),
        download_uri: String(selectedNode.download_uri || ''),
        name: String(selectedNode.title || 'CodeArts制品'),
        id: selectedNode.repo_id ? Number(selectedNode.repo_id) : undefined,
        target: 'local'
      })
      if (res?.code === 0) {
        message.success(`已下载到本地配置目录：${res?.data?.saved_path || ''}`)
        refreshTree()
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '下载到本地失败')
    } finally {
      setDownloading(false)
    }
  }

  const handleDownloadLocalFile = async () => {
    if (!selectedNode?.file_url) return
    message.info(`文件已在本地目录: ${selectedNode.file_url}`)
  }

  const handleDeleteLocalFile = async () => {
    if (!selectedNode?.repo_id || !selectedNode?.file_url) return
    Modal.confirm({
      title: '删除本地文件',
      content: '确认删除该制品文件？',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          const res: any = await repositoryApi.delete(Number(selectedNode.repo_id))
          if (res?.code === 0) {
            message.success('删除成功')
            setSelectedKeys([])
            setSelectedNodeKey('')
            refreshTree()
          }
        } catch {
          /* ignore */
        }
      },
    })
  }

  const handleDownloadMenuClick = async ({ key }: { key: string }) => {
    if (key === 'server') {
      await handleDownloadToServer()
    }
    if (key === 'local') {
      await handleDownloadToLocal()
    }
  }

  const remoteDownloadMenuItems = [
    { key: 'server', label: '服务器' },
    { key: 'local', label: '本地' },
  ]

  const moreMenuItems = [
    { key: 'create-project', label: '新增项目' },
    { key: 'member-permission', label: '项目成员及权限' },
    { key: 'delete-project', label: '删除当前项目', danger: true },
  ]

  const handleMoreMenuClick = async ({ key }: { key: string }) => {
    if (key === 'create-project') {
      await refreshCodeartsConfig()
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
      Modal.confirm({
        title: '删除当前项目',
        content: '确认删除当前项目？',
        okText: '删除',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: async () => {
          if (!currentProjectKey) {
            message.error('请先选择项目')
            return
          }
          try {
            const res: any = await repositoryApi.deleteProject(currentProjectKey)
            if (res?.code === 0) {
              message.success('删除成功')
              setSelectedKeys([])
              setSelectedNodeKey('')
              setCurrentProjectKey('')
              refreshTree()
            }
          } catch {
            /* ignore */
          }
        },
      })
    }
  }

  const createOrSyncProjectFormJSX = (
    <Form
      layout="vertical"
      form={createProjectForm}
      onValuesChange={(_, allValues) => persistCodeartsFormDraft(allValues)}
      onFinish={async (values) => {
        setCreateProjectSubmitting(true)
        try {
          const repoIds = [
            String(values.repo_id_0 || '').trim(),
            ...(Array.isArray(values.repo_ids_extra) ? values.repo_ids_extra.map((x: any) => String(x || '').trim()) : []),
          ].filter(Boolean)
          const inferredRegion = inferRegionFromRepoId(repoIds[0])
          const payload: any = {
            enabled: true,
            domain_name: String(values.domain_name || '').trim(),
            username: String(values.username || '').trim(),
            password: String(values.password || '').trim() || undefined, // undefined if empty to avoid overriding with empty string
            region: String(values.region || inferredRegion || '').trim(),
            tenant_name: String(values.tenant_name || '').trim(),
            tenant_id: String(values.tenant_id || '').trim(),
            project_id: String(values.project_id || '').trim(),
            repo_ids: repoIds,
            download_username: String(values.download_username || '').trim(),
            download_password: String(values.download_password || '').trim() || undefined,
          }
          persistCodeartsFormDraft(values)
          const res: any = await repositoryApi.syncCodeartsProject(payload)
          if (res?.code === 0) {
            const syncedCount = Number(res?.data?.synced_count || 0)
            const skippedCount = Number(res?.data?.skipped_count || 0)
            message.success(`同步成功，已落地 ${syncedCount} 个文件${skippedCount > 0 ? `，跳过 ${skippedCount} 个文件` : ''}`)
            setIsCreateProjectOpen(false)
            createProjectForm.resetFields()
            refreshCodeartsConfig()
            refreshTree()
          }
        } catch (e: any) {
          const errDetail = e?.response?.data?.detail
          message.error(errDetail || 'CodeArts 同步失败，请稍后重试')
        } finally {
          setCreateProjectSubmitting(false)
        }
      }}
    >
      <div style={{ background: 'rgba(0,0,0,0.03)', padding: '12px 16px', borderRadius: 6, marginBottom: 16, color: 'rgba(0,0,0,0.65)' }}>
        配置华为云 IAM 认证和项目标识。新增项目会先查询项目信息，再按项目发布库文件路径构建左侧树。
      </div>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="账号名(Domain Name)" name="domain_name" rules={[{ required: true, message: '请输入账号名' }]}>
            <Input placeholder="华为云主账号名称" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="IAM用户名(Username)" name="username" rules={[{ required: true, message: '请输入IAM用户名' }]}>
            <Input placeholder="IAM用户名" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="IAM密码(Password)" name="password">
            <Input.Password placeholder="未修改可留空" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="区域(Region)" name="region" rules={[{ required: true, message: '请输入区域(如 cn-north-4)' }]}>
            <Input placeholder="例如: cn-north-4" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="租户ID(Tenant ID，可选)" name="tenant_id">
            <Input placeholder="新接口暂不强依赖，可选填写" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="项目ID(Project ID)" name="project_id" rules={[{ required: true, message: '请输入项目ID' }]}>
            <Input placeholder="请输入项目ID" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="免密下载用户名(可选)" name="download_username">
            <Input placeholder="免认证下载用户名" />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item label="免密下载密码(可选)" name="download_password">
            <Input.Password placeholder="免认证下载密码" />
          </Form.Item>
        </Col>
      </Row>
      <Row gutter={16}>
        <Col span={12}>
          <Form.Item label="目标仓库ID(可选)" name="repo_id_0">
            <Input placeholder="可选，用于和项目实际仓库做一致性校验" />
          </Form.Item>
        </Col>
      </Row>
      <Form.List name="repo_ids_extra">
        {(fields, { add, remove }) => (
          <>
            {fields.map((field, idx) => (
              <Row gutter={16} key={field.key}>
                <Col span={12}>
                  <Form.Item
                    {...field}
                    label={idx === 0 ? '附加仓库ID(可选)' : '附加仓库ID(可选)'}
                  >
                    <Input placeholder="请输入仓库ID" />
                  </Form.Item>
                </Col>
                <Col span={12} style={{ display: 'flex', alignItems: 'flex-end' }}>
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => remove(field.name)}
                  >
                    删除
                  </Button>
                </Col>
              </Row>
            ))}
            <div style={{ marginTop: 4 }}>
              <Button type="dashed" block icon={<PlusOutlined />} onClick={() => add()}>
                新增附加仓库ID
              </Button>
            </div>
          </>
        )}
      </Form.List>
    </Form>
  )

  const membersFiltered = useMemo(() => {
    const kw = memberKeyword.trim()
    if (!kw) return members
    return members.filter((m) => String(m.username || '').includes(kw))
  }, [members, memberKeyword])

  const adminCount = useMemo(() => members.filter((m) => m.role === 'admin').length, [members])
  const memberCount = useMemo(() => members.filter((m) => m.role !== 'admin').length, [members])

  const memberColumns = [
    {
      title: '用户',
      dataIndex: 'username',
      key: 'username',
      width: 140,
      render: (val: string) => (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 24, height: 24, borderRadius: 999, background: 'rgba(64,69,214,0.12)', color: '#4045D6', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
            <TeamOutlined style={{ fontSize: 12 }} />
          </span>
          <span>{val || '-'}</span>
        </span>
      ),
    },
    { title: '邀请人', dataIndex: 'inviter_username', key: 'inviter_username', width: 140, render: (val: string) => val || '-' },
    {
      title: '用户组',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (val: string) => <Tag color={val === 'admin' ? 'blue' : 'default'}>{val === 'admin' ? '管理员' : '成员'}</Tag>,
    },
    { title: '加入时间', dataIndex: 'joined_at', key: 'joined_at', width: 180, render: (val: string) => formatDateTime(val) },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: any, record: any) => (
        <Space>
          <Button
            type="link"
            onClick={() => {
              setRoleChangingUser(record)
              roleForm.setFieldsValue({ role: record.role || 'member' })
              setIsRoleChangeOpen(true)
            }}
          >
            权限变更
          </Button>
          <Button
            type="link"
            danger
            onClick={() => {
              Modal.confirm({
                title: '删除成员',
                content: `确认删除成员 ${record.username}？`,
                okText: '删除',
                okButtonProps: { danger: true },
                cancelText: '取消',
                onOk: async () => {
                  try {
                    const res: any = await repositoryApi.deleteProjectMember(currentProjectKey, Number(record.user_id))
                    if (res?.code === 0) {
                      message.success('删除成功')
                      setMembers((prev) => prev.filter((x) => x.user_id !== record.user_id))
                    }
                  } catch {
                    /* ignore */
                  }
                },
              })
            }}
          >
            删除
          </Button>
        </Space>
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
    fetchInviteCandidates('')
  }

  const displayedInviteCandidates = useMemo(() => {
    const kw = inviteKeyword.trim()
    if (!kw) return inviteCandidates
    return inviteCandidates.filter((u) => String(u.username || '').includes(kw))
  }, [inviteCandidates, inviteKeyword])

  const displayedInviteUsernames = useMemo(
    () => displayedInviteCandidates.map((u) => String(u.username || '')).filter(Boolean),
    [displayedInviteCandidates],
  )

  const inviteAllChecked = useMemo(() => {
    if (displayedInviteUsernames.length === 0) return false
    return displayedInviteUsernames.every((u) => inviteSelectedUsernames.includes(u))
  }, [displayedInviteUsernames, inviteSelectedUsernames])

  const inviteAllIndeterminate = useMemo(() => {
    const selectedInView = inviteSelectedUsernames.filter((u) => displayedInviteUsernames.includes(u))
    return selectedInView.length > 0 && !inviteAllChecked
  }, [inviteSelectedUsernames, displayedInviteUsernames, inviteAllChecked])

  const permissionsKeys = ['invite_user', 'delete_user', 'delete_project', 'mark_flash_file', 'download_file'] as const
  const permissionsLabels: Record<(typeof permissionsKeys)[number], string> = {
    invite_user: '邀请用户',
    delete_user: '删除用户',
    delete_project: '删除项目',
    mark_flash_file: '标记可烧录/安装文件',
    download_file: '下载文件',
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
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Input
          placeholder="请输入成员名称"
          value={memberKeyword}
          onChange={(e) => setMemberKeyword(e.target.value)}
          style={{ width: 220 }}
          allowClear
        />
        <Button type="primary" onClick={openInviteModal}>
          邀请成员
        </Button>
      </div>
      <Table
        columns={memberColumns as any}
        dataSource={membersFiltered}
        rowKey="user_id"
        loading={membersLoading}
        pagination={{ pageSize: 5, showSizeChanger: false, showQuickJumper: true }}
      />
    </div>
  )

  const permissionsPanelJSX = (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ fontWeight: 600, color: 'rgba(0,0,0,0.88)' }}>权限设置</div>
        <Button
          type="primary"
          loading={permSaving}
          disabled={!currentProjectKey || permLoading}
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
                message.success('保存成功')
                setPermConfig(res.data || {})
              }
            } catch {
              /* ignore */
            } finally {
              setPermSaving(false)
            }
          }}
        >
          保存
        </Button>
      </div>
      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 180 }}>
          <div style={{ padding: '10px 12px', background: 'rgba(0,0,0,0.03)', borderRadius: 6, fontWeight: 600 }}>用户组</div>
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Button
              type={permGroup === 'admin' ? 'primary' : 'default'}
              onClick={() => {
                setPermGroup('admin')
                setPermDraft({ ...(permConfig?.admin || {}) })
              }}
            >
              管理员（{adminCount}）
            </Button>
            <Button
              type={permGroup === 'member' ? 'primary' : 'default'}
              onClick={() => {
                setPermGroup('member')
                setPermDraft({ ...(permConfig?.member || {}) })
              }}
            >
              成员（{memberCount}）
            </Button>
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ padding: '10px 12px', background: 'rgba(0,0,0,0.03)', borderRadius: 6, fontWeight: 600 }}>项目权限设置</div>
          <div style={{ marginTop: 14 }}>
            <Checkbox indeterminate={permAllIndeterminate} checked={permAllChecked} onChange={(e) => togglePermAll(e.target.checked)}>
              全选
            </Checkbox>
            <div style={{ height: 12 }} />
            <Row gutter={[12, 12]}>
              {permissionsKeys.map((k) => (
                <Col span={12} key={k}>
                  <Checkbox checked={Boolean(permDraft[k])} onChange={(e) => setPermDraft((prev) => ({ ...prev, [k]: e.target.checked }))}>
                    {permissionsLabels[k]}
                  </Checkbox>
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
    const displaySize = pickDisplaySize(selectedNode.size, fileDetail.size || fileDetail.display_size || selectedNode.raw?.display_size)
    const totalFileCount = descendantFiles.length
    const totalBytes = descendantFiles.reduce((sum, item) => sum + Number(item.size || 0), 0)
    const aggregatedSize = formatBytes(totalBytes)
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
      return [
        { label: 'SHA-256', value: firstFilled(fileDetail.sha256, checksums.sha256, selectedNode.sha256, '--') },
        { label: 'MD5', value: firstFilled(fileDetail.md5, checksums.md5, selectedNode.md5, '--') },
      ]
    }
    return [
      { label: 'SHA-256', value: '--' },
      { label: 'MD5', value: '--' },
    ]
  }, [selectedNode])

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'rgba(0,0,0,0.88)' }}>制品仓库</div>
          <Tag color={isCodeartsConnected ? 'green' : 'default'}>{isCodeartsConnected ? 'CodeArts已连接' : 'CodeArts未连接'}</Tag>
          <Button type="link" icon={<ReloadOutlined />} onClick={() => { refreshCodeartsConfig(); refreshTree() }}>
            同步CodeArts
          </Button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Select
            value={currentProjectKey || undefined}
            style={{ width: 200 }}
            options={projectOptions}
            placeholder="选择项目"
            onChange={(val) => setCurrentProjectKey(val)}
            allowClear
          />
          <Dropdown
            menu={{ items: moreMenuItems as any, onClick: handleMoreMenuClick as any }}
            trigger={['click']}
          >
            <Button icon={<EllipsisOutlined />} />
          </Dropdown>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ width: 260, background: '#fff', border: '1px solid #f0f0f0', borderRadius: 6, padding: 12 }}>
          <Input
            placeholder="请输入搜索关键词"
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            allowClear
            style={{ marginBottom: 12 }}
          />
          <Spin spinning={treeLoading}>
            <Tree
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
                if (info?.node?.project_id) setCurrentProjectKey(`proj_${info.node.project_id}`)
                if (String(key).startsWith('proj_')) setCurrentProjectKey(String(key))
              }}
              showLine
              blockNode
              height={520}
            />
          </Spin>
        </div>

        <div style={{ flex: 1, background: '#fff', border: '1px solid #f0f0f0', borderRadius: 6, padding: 16 }}>
          {!selectedNode && (
            <div style={{ color: 'rgba(0,0,0,0.45)' }}>请选择左侧项目/制品查看详细信息</div>
          )}

          {selectedNode && (
            <div>
              {((selectedNode.project_id && selectedNode.download_uri) || (selectedNode.repo_id && selectedNode.file_url)) && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
                  <Space>
                    {selectedNode.repo_id && !selectedNode.file_url && selectedNode.download_uri && (
                      <Button type="primary" loading={downloading} onClick={handleDownloadToLocal}>
                        在线安装
                      </Button>
                    )}
                    {selectedNode.repo_id && selectedNode.file_url && (
                      <Button type="primary" disabled>
                        离线安装
                      </Button>
                    )}
                    {selectedNode.project_id && selectedNode.download_uri && (
                      <Dropdown
                        menu={{ items: remoteDownloadMenuItems as any, onClick: handleDownloadMenuClick as any }}
                        trigger={['click']}
                        disabled={downloading}
                      >
                        <Button type="link">
                          下载 <DownOutlined />
                        </Button>
                      </Dropdown>
                    )}
                  </Space>
                </div>
              )}
              <div
                style={{
                  background: '#fff',
                  border: '1px solid #f0f0f0',
                  borderRadius: 8,
                  padding: 16,
                  boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
                }}
              >
                        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>详细信息</div>
                        <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', rowGap: 10, columnGap: 16 }}>
                          {detailPairs.map((p) => (
                            <div key={p.label} style={{ display: 'contents' }}>
                              <div style={{ color: 'rgba(0,0,0,0.65)' }}>{p.label}</div>
                              <div style={{ color: 'rgba(0,0,0,0.88)', wordBreak: 'break-all' }}>{p.value}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                      {checksumPairs.length > 0 ? (
                        <div
                          style={{
                            marginTop: 16,
                            background: '#fff',
                            border: '1px solid #f0f0f0',
                            borderRadius: 8,
                            padding: 16,
                            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
                          }}
                        >
                          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>校验和</div>
                          <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', rowGap: 10, columnGap: 16 }}>
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
        title="项目成员及权限"
        open={isMemberPermissionOpen}
        width={800}
        footer={null}
        onCancel={() => setIsMemberPermissionOpen(false)}
      >
        <Tabs
          items={[
            { key: 'members', label: '项目成员', children: membersPanelJSX },
            { key: 'permissions', label: '权限设置', children: permissionsPanelJSX },
          ]}
        />
      </Modal>

      <Modal
        title="新建项目"
        open={isCreateProjectOpen}
        width={656}
        okText="确 定"
        cancelText="取 消"
        confirmLoading={createProjectSubmitting}
        onOk={() => createProjectForm.submit()}
        onCancel={() => {
          if (createProjectSubmitting) return
          setIsCreateProjectOpen(false)
          createProjectForm.resetFields()
        }}
      >
        {createOrSyncProjectFormJSX}
      </Modal>
      <Modal
        title="邀请成员"
        open={isInviteOpen}
        width={595}
        okText="确 定"
        cancelText="取 消"
        confirmLoading={inviteSubmitting}
        onOk={async () => {
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
            if (okCount > 0) message.success('邀请成功')
            setIsInviteOpen(false)
            setInviteSelectedUsernames([])
            const listRes: any = await repositoryApi.listProjectMembers(currentProjectKey)
            if (listRes?.code === 0) setMembers(Array.isArray(listRes.data) ? listRes.data : [])
          } catch {
            /* ignore */
          } finally {
            setInviteSubmitting(false)
          }
        }}
        onCancel={() => {
          if (inviteSubmitting) return
          setIsInviteOpen(false)
        }}
      >
        <div style={{ display: 'flex', gap: 16 }}>
          <div style={{ width: 260 }}>
            <Input.Search
              placeholder="请输入用户名"
              value={inviteKeyword}
              allowClear
              loading={inviteCandidatesLoading}
              onChange={(e) => setInviteKeyword(e.target.value)}
              onSearch={(v) => fetchInviteCandidates(String(v || ''))}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 }}>
              <Checkbox
                indeterminate={inviteAllIndeterminate}
                checked={inviteAllChecked}
                onChange={(e: any) => {
                  const checked = e.target.checked
                  if (checked) {
                    setInviteSelectedUsernames((prev) => Array.from(new Set([...prev, ...displayedInviteUsernames])))
                  } else {
                    setInviteSelectedUsernames((prev) => prev.filter((u) => !displayedInviteUsernames.includes(u)))
                  }
                }}
              >
                全选
              </Checkbox>
              <Button type="link" onClick={() => setInviteSelectedUsernames([])}>
                清空
              </Button>
            </div>
            <div style={{ marginTop: 8, border: '1px solid rgba(0,0,0,0.1)', borderRadius: 6, height: 260, overflow: 'auto', padding: '8px 12px' }}>
              {inviteCandidatesLoading ? (
                <div style={{ color: 'rgba(0,0,0,0.45)', padding: '8px 0' }}>加载中…</div>
              ) : displayedInviteCandidates.length === 0 ? (
                <div style={{ color: 'rgba(0,0,0,0.45)', padding: '8px 0' }}>暂无数据</div>
              ) : (
                displayedInviteCandidates.map((u) => {
                  const username = String(u.username || '')
                  const checked = inviteSelectedUsernames.includes(username)
                  return (
                    <div key={username} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
                      <Checkbox
                        checked={checked}
                        onChange={(e: any) => {
                          const nextChecked = e.target.checked
                          setInviteSelectedUsernames((prev) => (nextChecked ? Array.from(new Set([...prev, username])) : prev.filter((x) => x !== username)))
                        }}
                      />
                      <span style={{ color: 'rgba(0,0,0,0.88)' }}>{username || '-'}</span>
                    </div>
                  )
                })
              )}
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>用户组</div>
              <Select
                value={inviteRole}
                style={{ width: 200 }}
                onChange={(v) => setInviteRole(v)}
                options={[
                  { label: '管理员', value: 'admin' },
                  { label: '成员', value: 'member' },
                ]}
              />
            </div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>已选择（{inviteSelectedUsernames.length}）</div>
            <div style={{ border: '1px solid rgba(0,0,0,0.1)', borderRadius: 6, height: 260, overflow: 'auto', padding: '8px 12px' }}>
              {inviteSelectedUsernames.length === 0 ? (
                <div style={{ color: 'rgba(0,0,0,0.45)', padding: '8px 0' }}>请选择用户</div>
              ) : (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {inviteSelectedUsernames.map((u) => (
                    <Tag
                      key={u}
                      closable
                      onClose={(e: any) => {
                        e.preventDefault()
                        setInviteSelectedUsernames((prev) => prev.filter((x) => x !== u))
                      }}
                    >
                      {u}
                    </Tag>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </Modal>

      <Modal
        title="权限变更"
        open={isRoleChangeOpen}
        width={595}
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
  )
}

export default Repository
