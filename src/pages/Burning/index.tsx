import { Table, Button, Space, Modal, message, Tag, Popconfirm, Select, Input, Row, Col, Typography, Checkbox, InputNumber } from 'antd'
import { PlusOutlined, SearchOutlined, DesktopOutlined, AppstoreOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { taskApi, productApi, burnerApi, scriptApi, repositoryApi } from '../../services/api'
import { Permission } from '../../hooks'
import dayjs from 'dayjs'

const { Title } = Typography

const osList = [
  { id: 1, name: '银河麒麟', icon: 'https://gw.alipayobjects.com/zos/rmsportal/nxoTqGljSgexOQkLsvOM.png' },
  { id: 2, name: '鸿蒙', icon: 'https://gw.alipayobjects.com/zos/rmsportal/nxoTqGljSgexOQkLsvOM.png' },
  { id: 3, name: '翼辉SylixOS', icon: 'https://gw.alipayobjects.com/zos/rmsportal/nxoTqGljSgexOQkLsvOM.png' },
  { id: 4, name: '统信UOS', icon: 'https://gw.alipayobjects.com/zos/rmsportal/nxoTqGljSgexOQkLsvOM.png' },
]

const osTypeMap: Record<number, string> = {
  1: 'kylin',
  2: 'harmony',
  3: 'yinghui',
  4: 'uos',
}

const parseJsonSafe = (value?: string | null) => {
  if (!value) return null
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

const Burning: React.FC = () => {
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

  // Wizard state
  const [isWizardOpen, setIsWizardOpen] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)
  const [platform, setPlatform] = useState<'board' | 'os' | null>(null)
  const [wizardData, setWizardData] = useState<any>({
    software: null,
    boardId: null,
    osId: null,
    options: ['local', 'integrity'],
    retryCount: 1,
  })

  // Dependent data
  const [boards, setBoards] = useState<any[]>([])
  const [burners, setBurners] = useState<any[]>([])
  const [scripts, setScripts] = useState<any[]>([])
  const [repositories, setRepositories] = useState<any[]>([])
  const [filterBoards, setFilterBoards] = useState<any[]>([])
  const [artifactKeyword, setArtifactKeyword] = useState('')

  useEffect(() => { 
    fetchTasks()
    fetchFilterBoards()
  }, [params])

  useEffect(() => {
    if (!isDetailOpen || !detailTask?.id || detailTask.status !== 1) return
    const timer = window.setInterval(() => {
      fetchTaskDetail(detailTask.id, true)
      fetchTasks()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [isDetailOpen, detailTask?.id, detailTask?.status, params.page, params.page_size, params.status, params.board_name, params.keyword, params.sort_field, params.sort_order])

  useEffect(() => {
    if (!isConsistencyOpen || !consistencyTask?.id || consistencyTask.status !== 1) return
    const timer = window.setInterval(() => {
      fetchConsistencyTask(consistencyTask.id, true)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [isConsistencyOpen, consistencyTask?.id, consistencyTask?.status])

  const fetchFilterBoards = async () => {
    try {
      const res: any = await productApi.getList({ page: 1, page_size: 100 })
      setFilterBoards(res?.data || [])
    } catch { /* ignore */ }
  }

  const fetchTasks = async () => {
    setLoading(true)
    try {
      const res: any = await taskApi.getList(params)
      if (res.code === 0) { setDataSource(res.data || []); setTotal(res.total || 0) }
    } catch { /* interceptor handles it */ }
    finally { setLoading(false) }
  }

  const fetchWizardData = async () => {
    try {
      const [prodRes, burnerRes, scriptRes, repoRes]: any[] = await Promise.all([
        productApi.getList({ page: 1, page_size: 100 }),
        burnerApi.getList({ page: 1, page_size: 100 }),
        scriptApi.getList({ page: 1, page_size: 100 }),
        repositoryApi.getList({ page: 1, page_size: 100 })
      ])
      setBoards(prodRes?.data || [])
      setBurners(burnerRes?.data || [])
      setScripts(scriptRes?.data || [])
      setRepositories((repoRes?.data || []).filter((item: any) => item.file_url))
    } catch { /* ignore */ }
  }

  const handleOpenWizard = () => {
    setCurrentStep(0)
    setPlatform(null)
    setWizardData({ software: null, boardId: null, osId: null, options: ['local', 'integrity'], retryCount: 1 })
    setArtifactKeyword('')
    fetchWizardData()
    setIsWizardOpen(true)
  }

  const handleNext = () => {
    if (currentStep === 0 && !platform) {
      message.warning('请选择平台')
      return
    }
    if (currentStep === 0 && !wizardData.software) {
      message.warning('请选择可执行文件')
      return
    }
    if (currentStep === 1) {
      if (platform === 'board' && !wizardData.boardId) {
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

  const selectedRepository = repositories.find((repo) => repo.id === wizardData.software)

  const handleWizardFinish = async () => {
    try {
      if (!selectedRepository) {
        message.warning('请选择可执行文件')
        return
      }
      if (platform === 'board' && !wizardData.burnerId) {
        message.warning('请选择烧录器')
        return
      }
      if (platform === 'board' && !wizardData.scriptId) {
        message.warning('请选择烧录脚本')
        return
      }
      if (platform === 'os' && !wizardData.targetIp) {
        message.warning('请输入目标地址')
        return
      }
      const configPayload = {
        task_type: platform,
        platform,
        retries: Number(wizardData.retryCount || 0),
        keep_local: wizardData.options?.includes('local'),
        integrity: wizardData.options?.includes('integrity'),
        version_check: wizardData.options?.includes('version'),
        expected_checksum: selectedRepository.sha256 || selectedRepository.md5 || undefined,
        history_checksum: selectedRepository.sha256 || selectedRepository.md5 || undefined,
        script_id: wizardData.scriptId,
        os_type: platform === 'os' ? osTypeMap[wizardData.osId] : undefined,
        extra_config: wizardData.config || undefined,
      }
      await taskApi.create({
        software_name: selectedRepository.name,
        repository_id: selectedRepository.id,
        task_type: platform || undefined,
        board_name: platform === 'board' ? boards.find(b => b.id === wizardData.boardId)?.name : osList.find(o => o.id === wizardData.osId)?.name,
        config_json: JSON.stringify(configPayload),
        target_ip: wizardData.targetIp,
        target_port: wizardData.targetPort ? Number(wizardData.targetPort) : undefined,
        product_id: platform === 'board' ? wizardData.boardId : undefined,
        burner_id: wizardData.burnerId,
        script_id: wizardData.scriptId,
        keep_local: configPayload.keep_local ? 1 : 0,
        integrity: configPayload.integrity ? 1 : 0,
        expected_checksum: configPayload.expected_checksum,
        version_check: configPayload.version_check ? 1 : 0,
        history_checksum: configPayload.history_checksum,
      })
      message.success('任务创建成功')
      setIsWizardOpen(false)
      fetchTasks()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await taskApi.delete(id)
      message.success('删除成功')
      fetchTasks()
    } catch { /* ignore */ }
  }

  const handleExecute = async (id: number) => {
    try {
      await taskApi.execute(id)
      message.success('任务已启动')
      fetchTasks()
    } catch { /* interceptor handles it */ }
  }

  const fetchTaskDetail = async (id: number, silent = false) => {
    if (!silent) setDetailLoading(true)
    try {
      const res: any = await taskApi.getById(id)
      if (res.code === 0) setDetailTask(res.data)
    } catch { /* interceptor handles it */ }
    finally {
      if (!silent) setDetailLoading(false)
    }
  }

  const fetchConsistencyTask = async (id: number, silent = false) => {
    if (!silent) setReportLoading(true)
    try {
      const res: any = await taskApi.getById(id)
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

  const handlePreviewConsistencyReport = async (taskId: number, print = false) => {
    setReportLoading(true)
    try {
      const htmlBlob: any = await taskApi.getConsistencyReportHtml(taskId, print)
      const blob = htmlBlob instanceof Blob ? htmlBlob : new Blob([htmlBlob], { type: 'text/html;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      const popup = window.open(url, '_blank')
      if (!popup) {
        window.URL.revokeObjectURL(url)
        message.warning('请允许浏览器打开新窗口')
        return
      }
      setTimeout(() => window.URL.revokeObjectURL(url), 60000)
    } catch {
      message.error('报告打开失败')
    } finally {
      setReportLoading(false)
    }
  }

  const handleDownloadConsistencyCsv = async (taskId: number) => {
    setReportLoading(true)
    try {
      const blobData: any = await taskApi.getConsistencyReportCsv(taskId)
      const blob = blobData instanceof Blob ? blobData : new Blob([blobData], { type: 'text/csv;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `consistency_report_task_${taskId}.csv`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    } catch {
      message.error('报告下载失败')
    } finally {
      setReportLoading(false)
    }
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
    0: { color: 'default', text: '等待' },
    1: { color: 'processing', text: '执行中' },
    2: { color: 'success', text: '完成' },
    3: { color: 'error', text: '失败' },
  }

  const columns = [
    { title: '序号', key: 'index', width: 60, render: (_: any, __: any, index: number) => index + 1 },
    { title: '烧录安装目标', key: 'target', width: 180, render: (_: any, record: any) => record.board_name || record.target_ip || '-' },
    {
      title: '软件及版本',
      dataIndex: 'software_name',
      key: 'software_name',
      render: (text: string, record: any) => {
        return (
          <Space>
            <span>{text}</span>
            {record.software_version ? <Tag color="blue" style={{ borderRadius: 10 }}>{record.software_version}</Tag> : null}
          </Space>
        )
      },
    },
    { 
      title: '执行时间', 
      dataIndex: 'created_at', 
      key: 'created_at', 
      sorter: true,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-' 
    },
    {
      title: '执行人',
      dataIndex: 'executor',
      key: 'executor',
      render: (text: string) => {
        const name = text || '-'
        return (
          <Space>
            <div style={{ width: 20, height: 20, borderRadius: '50%', backgroundColor: '#4f46e5', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10 }}>
              {name === '-' ? '-' : name.substring(Math.max(0, name.length - 2))}
            </div>
            {name}
          </Space>
        )
      },
    },
    { title: '状态', dataIndex: 'status', key: 'status', render: (s: number) => (
      <span style={{ color: statusMap[s]?.color === 'success' ? '#52c41a' : statusMap[s]?.color === 'processing' ? '#1890ff' : '#f5222d' }}>
        <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', backgroundColor: 'currentColor', marginRight: 6 }}></span>
        {statusMap[s]?.text}
      </span>
    ) },
    { title: '版本一致性报告', dataIndex: 'consistency_report', key: 'consistency_report', render: (_: any, record: any) => <a onClick={() => handleOpenConsistency(record.id)}>查看报告</a> },
    { title: '操作', key: 'action', render: (_: any, record: any) => (
        <Space>
          {(record.status === 0 || record.status === 3) && (
            <Permission code="burning:execute">
              <a onClick={() => handleExecute(record.id)}>执行</a>
            </Permission>
          )}
          <a onClick={() => handleOpenDetail(record.id)}>详情</a>
          <a onClick={() => handleOpenConsistency(record.id)}>报告</a>
          <Permission code="burning:delete">
            <Popconfirm title="确认删除" onConfirm={() => handleDelete(record.id)}>
              <a style={{ color: '#ff4d4f' }}>删除</a>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
  ]

  const artifactRows = repositories
    .filter((item) => {
      if (!artifactKeyword) return true
      const keyword = artifactKeyword.toLowerCase()
      return [
        item.name,
        item.version,
        item.repo_id,
        item.project_key,
        item.tenant,
      ].some((value) => String(value || '').toLowerCase().includes(keyword))
    })
    .map((item) => ({
      key: item.id,
      label: item.name,
      value: item.id,
      version: item.version || '-',
      responsible: item.tenant || item.repo_id || '-',
      publishTime: item.updated_at ? dayjs(item.updated_at).format('YYYY-MM-DD HH:mm') : '-',
      path: item.project_key || item.repo_id || '本地制品仓',
    }))

  const detailConfig = detailTask ? parseJsonSafe(detailTask.config_json) : null
  const consistencyConclusion = consistencyTask?.consistency_passed === 1
    ? { color: 'success', text: '一致通过' }
    : consistencyTask?.consistency_passed === 0
      ? { color: 'error', text: '不一致告警' }
      : { color: 'default', text: '未比对' }
  const integrityConclusion = consistencyTask?.integrity_passed === 1
    ? { color: 'success', text: '完整性通过' }
    : consistencyTask?.integrity_passed === 0
      ? { color: 'error', text: '完整性失败' }
      : { color: 'default', text: '未校验' }

  const fileColumns = [
    { title: '软件名称及版本', dataIndex: 'label' },
    { title: '版本', dataIndex: 'version', render: (v: string) => <Tag color="blue" style={{ borderRadius: 10 }}>{v}</Tag> },
    { title: '项目责任人', dataIndex: 'responsible', render: (text: string) => {
      const name = text || '张三'
      return (
        <Space>
          <div style={{ width: 20, height: 20, borderRadius: '50%', backgroundColor: '#4f46e5', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10 }}>{name.substring(Math.max(0, name.length - 2))}</div>
          {name}
        </Space>
      )
    } },
    { title: '发布时间', dataIndex: 'publishTime' },
    { title: '路径', dataIndex: 'path' },
  ]

  const boardColumns = [
    { title: '板卡序列号', dataIndex: 'serial_number' },
    { title: '板卡名称', dataIndex: 'name' },
    { title: '芯片类型', dataIndex: 'chip_type', render: (t: string) => <Tag color={t === 'ARM' ? 'blue' : t === 'FPGA' ? 'magenta' : 'purple'} style={{ borderRadius: 10 }}>{t}</Tag> },
    { title: '板卡图片', dataIndex: 'board_image', render: (img: string) => (
      <Space>
        <img src={img} alt="board" style={{ width: 40, height: 30, objectFit: 'cover', borderRadius: 2 }} />
        <span style={{ color: '#666' }}>板卡详情</span>
      </Space>
    ) },
  ]

  const [boardFilter, setBoardFilter] = useState({ type: '全部', keyword: '' })

  const filteredBoards = boards.filter(b => {
    const matchType = boardFilter.type === '全部' || b.chip_type === boardFilter.type
    const matchKeyword = !boardFilter.keyword || b.name?.includes(boardFilter.keyword) || b.serial_number?.includes(boardFilter.keyword)
    return matchType && matchKeyword
  })

  // Wizard UI
  if (isWizardOpen) {
    const totalSteps = platform === 'os' ? 2 : 3
    return (
      <div style={{ padding: '0 24px 24px', background: '#fff', minHeight: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 0', borderBottom: '1px solid #f0f0f0', marginBottom: 24 }}>
          <Title level={4} style={{ margin: 0 }}>烧录安装管理</Title>
          <a onClick={() => setIsWizardOpen(false)} style={{ color: '#666' }}>&lt;返回</a>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 24 }}>
          <span style={{ color: '#4045D6', fontWeight: 'bold' }}>任务向导</span>
          <span style={{ color: '#999' }}>步骤 {currentStep + 1}/{totalSteps}</span>
        </div>

        {currentStep === 0 && (
          <div>
            <div style={{ marginBottom: 32 }}>
              <div style={{ marginBottom: 16, fontWeight: 'bold' }}>选择平台</div>
              <Space size="large">
                <div 
                  onClick={() => setPlatform('board')}
                  style={{ width: 140, height: 80, border: `1px solid ${platform === 'board' ? '#4045D6' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: platform === 'board' ? '#F0F5FF' : '#fff' }}
                >
                  <AppstoreOutlined style={{ fontSize: 28, color: platform === 'board' ? '#4045D6' : '#1d2129', marginBottom: 8 }} />
                  <span style={{ color: platform === 'board' ? '#4045D6' : '#1d2129' }}>板卡</span>
                </div>
                <div 
                  onClick={() => setPlatform('os')}
                  style={{ width: 140, height: 80, border: `1px solid ${platform === 'os' ? '#4045D6' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: platform === 'os' ? '#F0F5FF' : '#fff' }}
                >
                  <DesktopOutlined style={{ fontSize: 28, color: platform === 'os' ? '#4045D6' : '#1d2129', marginBottom: 8 }} />
                  <span style={{ color: platform === 'os' ? '#4045D6' : '#1d2129' }}>操作系统</span>
                </div>
              </Space>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                <div style={{ fontWeight: 'bold' }}>选择可执行文件</div>
                <Input
                  prefix={<SearchOutlined />}
                  placeholder="请输入可执行文件名称"
                  style={{ width: 240 }}
                  value={artifactKeyword}
                  onChange={(e) => setArtifactKeyword(e.target.value)}
                />
              </div>
              <Table 
                columns={fileColumns} 
                dataSource={artifactRows} 
                pagination={false} 
                rowKey="value" 
                size="small" 
                rowSelection={{
                  type: 'radio',
                  selectedRowKeys: wizardData.software ? [wizardData.software] : [],
                  onChange: (selectedRowKeys) => {
                    setWizardData({ ...wizardData, software: selectedRowKeys[0] })
                  },
                }}
                onRow={(record) => ({
                  onClick: () => {
                    setWizardData({ ...wizardData, software: record.value })
                  }
                })}
              />
            </div>
            <div style={{ textAlign: 'right', marginTop: 24 }}>
              <Button type="primary" onClick={handleNext}>下一步 &gt;</Button>
            </div>
          </div>
        )}

        {currentStep === 1 && platform === 'board' && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <div style={{ fontWeight: 'bold' }}>选择板卡</div>
              <Space>
                <span style={{ color: '#666' }}>芯片类型</span>
                <Select value={boardFilter.type} onChange={v => setBoardFilter({ ...boardFilter, type: v })} style={{ width: 120 }}>
                  <Select.Option value="全部">全部</Select.Option>
                  <Select.Option value="ARM">ARM</Select.Option>
                  <Select.Option value="PIC">PIC</Select.Option>
                  <Select.Option value="DSP">DSP</Select.Option>
                  <Select.Option value="FPGA">FPGA</Select.Option>
                  <Select.Option value="Altera-CPLD">Altera-CPLD</Select.Option>
                </Select>
                <Input prefix={<SearchOutlined />} placeholder="请输入板卡名称/序列号" value={boardFilter.keyword} onChange={e => setBoardFilter({ ...boardFilter, keyword: e.target.value })} style={{ width: 200 }} />
              </Space>
            </div>
            <Table 
              columns={boardColumns} 
              dataSource={filteredBoards} 
              pagination={{ total: filteredBoards.length, pageSize: 5 }} 
              rowKey="id" 
              size="small" 
              rowSelection={{
                type: 'radio',
                selectedRowKeys: wizardData.boardId ? [wizardData.boardId] : [],
                onChange: (selectedRowKeys) => {
                  setWizardData({ ...wizardData, boardId: selectedRowKeys[0] })
                },
              }}
              onRow={(record) => ({
                onClick: () => {
                  setWizardData({ ...wizardData, boardId: record.id })
                }
              })}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
              <Button onClick={handlePrev}>&lt; 上一步</Button>
              <Button type="primary" onClick={handleNext}>下一步 &gt;</Button>
            </div>
          </div>
        )}

        {currentStep === 1 && platform === 'os' && (
          <div>
            <Row gutter={48}>
              <Col span={14}>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 16, fontWeight: 'bold' }}>选择操作系统</div>
                  <Space size="middle">
                    {osList.map(os => (
                      <div 
                        key={os.id}
                        onClick={() => setWizardData({ ...wizardData, osId: os.id })}
                        style={{ width: 120, height: 80, border: `1px solid ${wizardData.osId === os.id ? '#4045D6' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', background: wizardData.osId === os.id ? '#F0F5FF' : '#fff' }}
                      >
                        <img src={os.icon} alt={os.name} style={{ width: 32, height: 32, marginBottom: 8 }} />
                        <span style={{ fontSize: 12, color: wizardData.osId === os.id ? '#4045D6' : '#333' }}>{os.name}</span>
                      </div>
                    ))}
                  </Space>
                </div>
                <Row gutter={16} style={{ marginBottom: 24 }}>
                  <Col span={12}>
                    <div style={{ marginBottom: 8 }}>目标地址</div>
                    <Input placeholder="请输入目标IP地址" value={wizardData.targetIp} onChange={e => setWizardData({ ...wizardData, targetIp: e.target.value })} />
                  </Col>
                  <Col span={12}>
                    <div style={{ marginBottom: 8 }}>目标端口</div>
                    <Input placeholder="请输入目标端口" value={wizardData.targetPort} onChange={e => setWizardData({ ...wizardData, targetPort: e.target.value })} />
                  </Col>
                </Row>
                <div>
                  <div style={{ marginBottom: 8 }}>参数配置</div>
                  <Input.TextArea rows={4} placeholder="配置参数(JSON格式)" value={wizardData.config} onChange={e => setWizardData({ ...wizardData, config: e.target.value })} />
                </div>
              </Col>
              <Col span={10} style={{ borderLeft: '1px solid #f0f0f0' }}>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 16, fontWeight: 'bold' }}>烧录选项</div>
                  <Checkbox.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }} value={wizardData.options} onChange={v => setWizardData({ ...wizardData, options: v })}>
                    <Checkbox value="local">可执行文件留存本地</Checkbox>
                    <Checkbox value="version">版本校验 <Tag color="blue" style={{ borderRadius: 10, marginLeft: 8 }}>1</Tag></Checkbox>
                    <Checkbox value="integrity">完整性校验(MD5|SHA256)</Checkbox>
                  </Checkbox.Group>
                </div>
                <div>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>备注</div>
                  <Input.TextArea rows={4} placeholder="备注信息" />
                </div>
              </Col>
            </Row>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 40 }}>
              <Button onClick={handlePrev}>&lt; 上一步</Button>
              <Button type="primary" onClick={handleWizardFinish}>完成</Button>
            </div>
          </div>
        )}

        {currentStep === 2 && platform === 'board' && (
          <div>
            <Row gutter={48}>
              <Col span={14}>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>选择IDE</div>
                  <Select style={{ width: '100%' }} value={wizardData.ide || 'Code Composer Studio'} onChange={v => setWizardData({ ...wizardData, ide: v })} options={[
                    { label: 'Code Composer Studio', value: 'Code Composer Studio' },
                    { label: 'IAR Embedded Workbench For Arm', value: 'IAR Embedded Workbench For Arm' },
                    { label: 'Keil uVision', value: 'Keil uVision' },
                    { label: 'MPLAB', value: 'MPLAB' },
                    { label: 'STM32CubeIDE', value: 'STM32CubeIDE' },
                    { label: 'Vitis', value: 'Vitis' },
                    { label: 'Vivado', value: 'Vivado' },
                    { label: 'WindRiver Workbench', value: 'WindRiver Workbench' },
                    { label: 'FTP', value: 'FTP' },
                    { label: 'TFTP', value: 'TFTP' },
                    { label: 'J_LINK', value: 'J_LINK' },
                  ]} />
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>选择烧录器</div>
                  <Select style={{ width: '100%' }} placeholder="请选择烧录器" value={wizardData.burnerId} onChange={v => setWizardData({ ...wizardData, burnerId: v })} options={burners.map(b => ({ label: b.name, value: b.id }))} />
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>选择烧录脚本</div>
                  <Select style={{ width: '100%' }} placeholder="请选择脚本" value={wizardData.scriptId} onChange={v => setWizardData({ ...wizardData, scriptId: v })} options={scripts.map(s => ({ label: s.name, value: s.id }))} />
                </div>
                <div>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>参数配置</div>
                  <Input.TextArea rows={4} placeholder="配置参数(JSON格式)" value={wizardData.config} onChange={e => setWizardData({ ...wizardData, config: e.target.value })} />
                </div>
              </Col>
              <Col span={10} style={{ borderLeft: '1px solid #f0f0f0' }}>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 16, fontWeight: 'bold' }}>烧录选项</div>
                  <Checkbox.Group style={{ display: 'flex', flexDirection: 'column', gap: 12 }} value={wizardData.options} onChange={v => setWizardData({ ...wizardData, options: v })}>
                    <Checkbox value="local">可执行文件留存本地</Checkbox>
                    <Checkbox value="version">版本校验</Checkbox>
                    <Checkbox value="integrity">完整性校验(MD5|SHA256)</Checkbox>
                  </Checkbox.Group>
                </div>
                <div style={{ marginBottom: 24 }}>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>烧录失败重试次数</div>
                  <Space>
                    <InputNumber min={0} max={5} value={wizardData.retryCount} onChange={v => setWizardData({ ...wizardData, retryCount: v })} />
                    <span style={{ color: '#999', fontSize: 12 }}>默认重试次数1次，最多5次</span>
                  </Space>
                </div>
                <div>
                  <div style={{ marginBottom: 8, fontWeight: 'bold' }}>备注</div>
                  <Input.TextArea rows={4} placeholder="备注信息" />
                </div>
              </Col>
            </Row>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 40 }}>
              <Button onClick={handlePrev}>&lt; 上一步</Button>
              <Button type="primary" onClick={handleWizardFinish}>完成</Button>
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
        <Title level={4} style={{ margin: 0 }}>烧录安装管理</Title>
        <Permission code="burning:add">
          <Button type="primary" icon={<PlusOutlined />} onClick={handleOpenWizard}>创建任务</Button>
        </Permission>
      </div>

      <div style={{ marginBottom: 24 }}>
        <span style={{ color: '#4045D6', borderBottom: '2px solid #4045D6', paddingBottom: 8, cursor: 'pointer', fontWeight: 'bold' }}>烧录安装任务历史</span>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16, gap: 16 }}>
        <Select defaultValue="all" style={{ width: 180 }} onChange={v => setParams({ ...params, page: 1, board_name: v === 'all' ? undefined : v } as any)}>
          <Select.Option value="all">所有烧录安装目标</Select.Option>
          {filterBoards.map(b => (
            <Select.Option key={b.id} value={b.name}>{b.name}</Select.Option>
          ))}
          {osList.map(o => (
            <Select.Option key={`os_${o.id}`} value={o.name}>{o.name}</Select.Option>
          ))}
        </Select>
        <Select defaultValue="all" style={{ width: 120 }} onChange={v => setParams({ ...params, page: 1, status: v === 'all' ? undefined : Number(v) })}>
          <Select.Option value="all">所有状态</Select.Option>
          <Select.Option value="1">执行中</Select.Option>
          <Select.Option value="2">完成</Select.Option>
        </Select>
        <Input.Search
          prefix={<SearchOutlined />}
          placeholder="请输入软件名称"
          style={{ width: 240 }}
          allowClear
          value={taskKeywordInput}
          onChange={(e) => {
            const nextValue = e.target.value
            setTaskKeywordInput(nextValue)
            if (!nextValue.trim()) {
              handleTaskKeywordSearch('')
            }
          }}
          onSearch={handleTaskKeywordSearch}
        />
      </div>

      <Table 
        columns={columns} 
        dataSource={dataSource} 
        rowKey="id" 
        loading={loading}
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
          showTotal: (t) => `共 ${t} 条`
        }} 
      />

      <Modal
        title="任务详情"
        open={isDetailOpen}
        onCancel={() => setIsDetailOpen(false)}
        footer={null}
        width={760}
      >
        {detailTask && (
          <div style={{ display: 'grid', gap: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', rowGap: 12 }}>
              <strong>软件名称</strong><span>{detailTask.software_name}</span>
              <strong>执行人</strong><span>{detailTask.executor || '-'}</span>
              <strong>烧录目标</strong><span>{detailTask.board_name || detailTask.target_ip || '-'}</span>
              <strong>状态</strong><span><Tag color={statusMap[detailTask.status]?.color}>{statusMap[detailTask.status]?.text}</Tag></span>
              <strong>仓库ID</strong><span>{detailTask.repository_id || '-'}</span>
              <strong>仓库名称</strong><span>{detailTask.repository_name || '-'}</span>
              <strong>项目标识</strong><span>{detailTask.project_key || '-'}</span>
              <strong>租户</strong><span>{detailTask.tenant || '-'}</span>
              <strong>制品版本</strong><span>{detailTask.software_version || '-'}</span>
              <strong>烧录器名称</strong><span>{detailTask.burner_name || '-'}</span>
              <strong>脚本ID</strong><span>{detailTask.script_id || '-'}</span>
              <strong>脚本名称</strong><span>{detailTask.script_name || '-'}</span>
              <strong>烧录器ID</strong><span>{detailTask.burner_id || '-'}</span>
              <strong>重试次数</strong><span>{detailTask.attempt_count || 0} / {detailTask.max_retries || 0}</span>
              <strong>回滚次数</strong><span>{detailTask.rollback_count || 0}</span>
              <strong>回滚结果</strong><span>{detailTask.rollback_result || '-'}</span>
              <strong>创建时间</strong><span>{detailTask.created_at?.replace('T', ' ').substring(0, 19)}</span>
            </div>
            <div>
              <strong>校验状态</strong>
              <div style={{ marginTop: 8 }}>
                <Space>
                  <Tag color={detailTask.integrity_passed === 1 ? 'success' : detailTask.integrity_passed === 0 ? 'error' : 'default'}>
                    {detailTask.integrity_passed === 1 ? '完整性通过' : detailTask.integrity_passed === 0 ? '完整性失败' : '未校验'}
                  </Tag>
                  <Tag color={detailTask.consistency_passed === 1 ? 'success' : detailTask.consistency_passed === 0 ? 'error' : 'default'}>
                    {detailTask.consistency_passed === 1 ? '版本一致' : detailTask.consistency_passed === 0 ? '版本不一致' : '未比对'}
                  </Tag>
                </Space>
              </div>
            </div>
            <div>
              <strong>执行日志</strong>
              <pre style={{ marginTop: 8, padding: 12, background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {detailTask.result || '暂无执行日志'}
              </pre>
            </div>
            {detailTask.last_error && (
              <div>
                <strong>最后错误</strong>
                <pre style={{ marginTop: 8, padding: 12, background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {detailTask.last_error}
                </pre>
              </div>
            )}
            {(detailTask.expected_checksum || detailTask.current_sha256 || detailTask.current_md5 || detailTask.history_checksum) && (
              <div style={{ display: 'grid', gap: 8 }}>
                <strong>校验数据</strong>
                <div style={{ fontSize: 12, wordBreak: 'break-all' }}>制品文件：{detailTask.file_url || '-'}</div>
                <div style={{ fontSize: 12, wordBreak: 'break-all' }}>期望校验码：{detailTask.expected_checksum || '-'}</div>
                <div style={{ fontSize: 12, wordBreak: 'break-all' }}>当前 SHA256：{detailTask.current_sha256 || '-'}</div>
                <div style={{ fontSize: 12, wordBreak: 'break-all' }}>当前 MD5：{detailTask.current_md5 || '-'}</div>
                <div style={{ fontSize: 12, wordBreak: 'break-all' }}>历史基线：{detailTask.history_checksum || '-'}</div>
              </div>
            )}
            {detailConfig && (
              <div>
                <strong>任务配置</strong>
                <pre style={{ marginTop: 8, padding: 12, background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {JSON.stringify(detailConfig, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
        {!detailTask && !detailLoading && <div>暂无数据</div>}
      </Modal>

      <Modal 
        title={
          <Space>
            <span>一致性报告</span>
            <Tag color={consistencyConclusion.color} style={{ borderRadius: 10, margin: 0 }}>{consistencyConclusion.text}</Tag>
          </Space>
        }
        open={isConsistencyOpen} 
        onCancel={() => setIsConsistencyOpen(false)} 
        footer={
          consistencyTask ? (
            <Space>
              {consistencyTask.consistency_passed === 0 && !consistencyTask.override_confirmed && (
                <Permission code="burning:override">
                  <Button onClick={() => handleOverride(consistencyTask.id)}>强制覆盖</Button>
                </Permission>
              )}
              <Button loading={reportLoading} onClick={() => handlePreviewConsistencyReport(consistencyTask.id)}>预览 HTML</Button>
              <Button loading={reportLoading} onClick={() => handlePreviewConsistencyReport(consistencyTask.id, true)}>打印</Button>
              <Button type="primary" loading={reportLoading} onClick={() => handleDownloadConsistencyCsv(consistencyTask.id)}>下载 CSV</Button>
            </Space>
          ) : null
        } 
        width={560}
      >
        {consistencyTask && (
          <div style={{ paddingTop: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
              <span style={{ color: '#666' }}>烧录安装目标</span>
              <span style={{ fontWeight: 'bold' }}>{consistencyTask.board_name || consistencyTask.target_ip || '-'}</span>
            </div>

            <div style={{ marginBottom: 16 }}>
              <Space>
                <Tag color={consistencyConclusion.color}>{consistencyConclusion.text}</Tag>
                <Tag color={integrityConclusion.color}>{integrityConclusion.text}</Tag>
                {consistencyTask.override_confirmed ? <Tag color="warning">已允许覆盖</Tag> : null}
              </Space>
            </div>
            
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ color: '#666' }}>当前版本</span>
                <Space>
                  <span style={{ fontWeight: 'bold' }}>{consistencyTask.software_name}</span>
                  <Tag color="blue" style={{ borderRadius: 10, margin: 0 }}>{consistencyTask.software_version || '当前任务'}</Tag>
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
                  <Tag color="blue" style={{ borderRadius: 10, margin: 0 }}>{consistencyTask.software_version || '历史基线'}</Tag>
                </Space>
              </div>
              <div style={{ border: '1px solid #91caff', background: '#e6f7ff', padding: '4px 8px', borderRadius: 4, fontSize: 12, color: '#1890ff', wordBreak: 'break-all' }}>
                校验码：{consistencyTask.history_checksum || '-'}
              </div>
            </div>

            {consistencyTask.expected_checksum && (
              <div style={{ marginTop: 16 }}>
                <div style={{ marginBottom: 8, color: '#666' }}>完整性期望值</div>
                <div style={{ border: '1px solid #d9d9d9', background: '#fafafa', padding: '4px 8px', borderRadius: 4, fontSize: 12, wordBreak: 'break-all' }}>
                  {consistencyTask.expected_checksum}
                </div>
              </div>
            )}
            <div style={{ marginTop: 16, display: 'grid', gap: 8, fontSize: 12 }}>
              <div>执行人：{consistencyTask.executor || '-'}</div>
              <div>仓库：{consistencyTask.repository_name || '-'}</div>
              <div>项目标识：{consistencyTask.project_key || '-'}</div>
              <div>租户：{consistencyTask.tenant || '-'}</div>
              <div>烧录器：{consistencyTask.burner_name || '-'}</div>
              <div>执行脚本：{consistencyTask.script_name || '-'}</div>
              <div>执行次数：{consistencyTask.attempt_count || 0} / {consistencyTask.max_retries || 0}</div>
              <div>回滚次数：{consistencyTask.rollback_count || 0}</div>
              <div>回滚结果：{consistencyTask.rollback_result || '-'}</div>
              <div style={{ wordBreak: 'break-all' }}>制品文件：{consistencyTask.file_url || '-'}</div>
            </div>
          </div>
        )}
        {!consistencyTask && !reportLoading && <div>暂无数据</div>}
      </Modal>
    </div>
  )
}

export default Burning
