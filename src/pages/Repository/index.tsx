import { Card, Table, Button, Space, Input, Modal, Form, message, Popconfirm, Tree, Select, Tag, Checkbox } from 'antd'
import { PlusOutlined, SyncOutlined, SearchOutlined, UserAddOutlined, KeyOutlined, UserOutlined } from '@ant-design/icons'
import { useState, useEffect } from 'react'
import { repositoryApi } from '../../services/api'
import { Permission } from '../../hooks'

const Repository: React.FC = () => {
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [isSyncModalOpen, setIsSyncModalOpen] = useState(false)
  const [isDetailModalOpen, setIsDetailModalOpen] = useState(false)
  const [isInviteModalOpen, setIsInviteModalOpen] = useState(false)
  const [isPermChangeModalOpen, setIsPermChangeModalOpen] = useState(false)
  const [isMemberPermModalOpen, setIsMemberPermModalOpen] = useState(false)
  const [activeModal, setActiveModal] = useState<'sync' | 'create'>('create')
  const [activeMemberTab, setActiveMemberTab] = useState<'members' | 'permissions'>('members')
  const [loading, setLoading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [selectedArtifact, setSelectedArtifact] = useState<any>(null)
  const [createForm] = Form.useForm()
  const [syncForm] = Form.useForm()
  const [inviteForm] = Form.useForm()
  const [permChangeForm] = Form.useForm()

  const treeData = [
    {
      title: '制品仓库1', key: 'repo1',
      children: [
        { title: '项目', key: 'repo1-project', children: [
          { title: '包', key: 'repo1-pkg' },
        ]},
      ],
    },
    {
      title: '制品仓库2', key: 'repo2',
      children: [
        { title: '项目', key: 'repo2-project', children: [
          { title: '包', key: 'repo2-pkg' },
        ]},
      ],
    },
  ]

  const columns = [
    { title: '仓库名称', dataIndex: 'name', key: 'name', width: 200 },
    { title: '制品类型', dataIndex: 'type', key: 'type', width: 100 },
    { title: '相对路径', dataIndex: 'path', key: 'path', ellipsis: true },
    { title: '创建人', dataIndex: 'creator', key: 'creator', width: 100 },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (val: string) => val?.replace('T', ' ').substring(0, 19) || '-',
    },
    { title: '修改人', dataIndex: 'modifier', key: 'modifier', width: 100 },
    {
      title: '操作', key: 'action', width: 160,
      render: (_: any, record: any) => (
        <Space>
          <Button type="link" onClick={() => { setSelectedArtifact(record); setIsDetailModalOpen(true) }}>详细信息</Button>
          <Permission code="repository:delete">
            <Popconfirm title="确认删除" onConfirm={() => handleDelete(record.id)}>
              <Button type="link" danger>删除</Button>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
  ]

  const fetchData = async () => {
    setLoading(true)
    try {
      const res: any = await repositoryApi.getList({
        page, page_size: 10, keyword: keyword || undefined,
      })
      setDataSource(res?.data || [])
      setTotal(res?.total || 0)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }

  useEffect(() => { fetchData() }, [page])

  const handleSearch = () => { setPage(1); fetchData() }

  const handleCreate = async () => {
    try {
      const values = await createForm.validateFields()
      await repositoryApi.create(values)
      message.success('创建成功')
      setIsCreateModalOpen(false)
      createForm.resetFields()
      fetchData()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await repositoryApi.delete(id)
      message.success('删除成功')
      fetchData()
    } catch { message.error('删除失败') }
  }

  const handleSync = async () => {
    try {
      await syncForm.validateFields()
      message.success('同步已开始')
      setIsSyncModalOpen(false)
      syncForm.resetFields()
    } catch (e: any) {
      if (e?.errorFields) return
    }
  }

  const handleInvite = async () => {
    try {
      await inviteForm.validateFields()
      message.success('邀请已发送')
      setIsInviteModalOpen(false)
      inviteForm.resetFields()
    } catch (e: any) {
      if (e?.errorFields) return
    }
  }

  const handlePermChange = async () => {
    try {
      await permChangeForm.validateFields()
      message.success('权限已变更')
      setIsPermChangeModalOpen(false)
      permChangeForm.resetFields()
    } catch (e: any) {
      if (e?.errorFields) return
    }
  }

  const openSyncModal = () => { setActiveModal('sync'); setIsSyncModalOpen(true) }
  const openCreateModal = () => { setActiveModal('create'); setIsCreateModalOpen(true) }

  const renderModalForm = (type: 'sync' | 'create') => (
    <Form layout="vertical">
      <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }]}>
        <Input placeholder="请输入账号" prefix={<UserOutlined style={{ color: 'rgba(0,0,0,0.25)' }} />} />
      </Form.Item>
      <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }]}>
        <Input.Password placeholder="请输入密码" prefix={<KeyOutlined style={{ color: 'rgba(0,0,0,0.25)' }} />} />
      </Form.Item>
      <Form.Item label="仓库ID" name="repo_id" rules={[{ required: true, message: '请输入仓库ID' }]}>
        <Input placeholder="请输入仓库ID" />
      </Form.Item>
      {type === 'create' && (
        <Form.Item label="项目ID" name="project_id" rules={[{ required: true, message: '请输入项目ID' }]}>
          <Input placeholder="请输入项目ID" />
        </Form.Item>
      )}
      {type === 'sync' && (
        <Form.Item label="项目ID" name="project_id">
          <Input placeholder="96f1830bbf2849fc9eac465636b464c9" />
        </Form.Item>
      )}
      <Form.Item label="租户名" name="tenant" rules={[{ required: true, message: '请输入租户名' }]}>
        <Input placeholder="请输入租户名" />
      </Form.Item>
      <Form.Item label="租户ID" name="tenant_id" rules={[{ required: true, message: '请输入租户ID' }]}>
        <Input placeholder="请输入租户ID" />
      </Form.Item>
      {type === 'sync' && (
        <div style={{ textAlign: 'center', marginTop: 8 }}>
          <Button icon={<PlusOutlined />} type="dashed" block>十 新增仓库ID</Button>
        </div>
      )}
    </Form>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 16, margin: 0 }}>制品仓库</h1>
          <Tag color="success" style={{ marginLeft: 8 }}>CodeArts已连接</Tag>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)' }}>管理 CodeArts 制品仓库和项目</p>
        </div>
        <Space>
          <Permission code="repository:sync">
            <Button icon={<SyncOutlined />} onClick={openSyncModal}>同步CodeArts</Button>
          </Permission>
          <Permission code="repository:invite">
            <Button icon={<UserAddOutlined />} onClick={() => setIsInviteModalOpen(true)}>邀请成员</Button>
          </Permission>
          <Permission code="repository:perm_change">
            <Button icon={<KeyOutlined />} onClick={() => setIsPermChangeModalOpen(true)}>权限变更</Button>
          </Permission>
          <Button icon={<KeyOutlined />} onClick={() => setIsMemberPermModalOpen(true)}>项目成员及权限</Button>
          <Permission code="repository:add">
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>新建项目</Button>
          </Permission>
        </Space>
      </div>

      <Card>
        <div style={{ display: 'flex', gap: 16 }}>
          <div style={{ width: 200, borderRight: '1px solid #f0f0f0', paddingRight: 16 }}>
            <div style={{ marginBottom: 12 }}>
              <Select placeholder="选择项目" style={{ width: '100%' }} allowClear
                options={dataSource.map((r) => ({ value: r.id, label: r.name }))} />
            </div>
            <Tree
              defaultExpandAll
              selectedKeys={[]}
              treeData={treeData}
              onSelect={(keys) => {
                if (keys[0]) fetchData()
              }}
            />
          </div>

          <div style={{ flex: 1 }}>
            <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
              <Input placeholder="请输入搜索关键词" prefix={<SearchOutlined />} style={{ width: 300 }}
                value={keyword} onChange={(e) => setKeyword(e.target.value)} onPressEnter={handleSearch} />
              <Button type="primary" onClick={handleSearch}>搜索</Button>
            </div>

            <div style={{ marginBottom: 8, color: 'rgba(51,51,51,1)', fontSize: 13 }}>共 {total} 条</div>

            <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
              pagination={{ current: page, pageSize: 10, total, onChange: (p) => setPage(p) }} />
          </div>
        </div>
      </Card>

      {/* New Project / Sync Config Modal */}
      <Modal
        title={activeModal === 'create' ? '新建项目' : '同步配置'}
        open={isCreateModalOpen || (isSyncModalOpen && activeModal === 'sync')}
        onOk={activeModal === 'create' ? handleCreate : handleSync}
        onCancel={() => {
          if (activeModal === 'create') { setIsCreateModalOpen(false); createForm.resetFields() }
          else { setIsSyncModalOpen(false); syncForm.resetFields() }
        }}>
        <p style={{ color: 'rgba(0,0,0,0.45)', marginBottom: 16, fontSize: 12 }}>
          {activeModal === 'create'
            ? '配置CodeArts用户、项目ID及仓库ID等信息，用于获取当前用户项目下指定制品仓库信息'
            : '配置当前用户项目下获取指定制品仓库信息'}
        </p>
        {activeModal === 'create' ? (
          <Form form={createForm} layout="vertical" onFinish={handleCreate}>
            {renderModalForm('create')}
          </Form>
        ) : (
          <Form form={syncForm} layout="vertical" onFinish={handleSync}>
            {renderModalForm('sync')}
          </Form>
        )}
      </Modal>

      {/* Detail Info Modal */}
      <Modal title="详细信息" open={isDetailModalOpen} onCancel={() => setIsDetailModalOpen(false)}
        footer={[
          <Button key="download" onClick={() => message.info('下载到本地')}>下载到本地</Button>,
          <Button key="delLocal" onClick={() => message.info('已删除本地文件')}>删除本地文件</Button>,
          <Button key="onlineInstall" type="primary" onClick={() => message.info('开始在线安装')}>在线安装</Button>,
          <Button key="localInstall" onClick={() => message.info('开始本地安装')}>本地安装</Button>,
        ]}>
        <div style={{ lineHeight: 2.2, fontSize: 13 }}>
          <div><b>仓库名称</b><span style={{ float: 'right' }}>{selectedArtifact?.name || '制品仓库'}</span></div>
          <div><b>制品类型</b><span style={{ float: 'right' }}>{selectedArtifact?.type || 'Generic'}</span></div>
          <div><b>相对路径</b><span style={{ float: 'right' }}>{selectedArtifact?.path || '/项目/包/xx.bin'}</span></div>
          <div><b>创建人</b><span style={{ float: 'right' }}>{selectedArtifact?.creator || '-'}</span></div>
          <div><b>创建时间</b><span style={{ float: 'right' }}>{selectedArtifact?.created_at?.replace('T', ' ').substring(0, 19) || '-'}</span></div>
          <div><b>修改人</b><span style={{ float: 'right' }}>{selectedArtifact?.modifier || '-'}</span></div>
          <div><b>大小</b><span style={{ float: 'right' }}>{selectedArtifact?.size || '2311.58 KB'}</span></div>
          <div><b>最后下载时间</b><span style={{ float: 'right' }}>{selectedArtifact?.last_download_time || '-'}</span></div>
          <div><b>下载次数</b><span style={{ float: 'right' }}>{selectedArtifact?.download_count || 0}</span></div>
          <div style={{ borderTop: '1px solid #f0f0f0', marginTop: 12, paddingTop: 12 }}>
            <b>校验和</b>
          </div>
          <div style={{ marginTop: 8, wordBreak: 'break-all' }}><b>SHA-256</b><br />{selectedArtifact?.sha256 || '-'}</div>
          <div style={{ marginTop: 4, wordBreak: 'break-all' }}><b>MD5</b><br />{selectedArtifact?.md5 || '-'}</div>
        </div>
      </Modal>

      {/* Invite Member Modal */}
      <Modal title="邀请成员" open={isInviteModalOpen} onOk={handleInvite}
        onCancel={() => { setIsInviteModalOpen(false); inviteForm.resetFields() }}>
        <Form form={inviteForm} layout="vertical" onFinish={handleInvite}>
          <div style={{ marginBottom: 12 }}>
            <Input placeholder="请输入关键字" prefix={<SearchOutlined />} />
          </div>
          <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Checkbox /> <span style={{ fontWeight: 'bold' }}>已选成员</span>
          </div>
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            columns={[
              { title: '用户', dataIndex: 'username', key: 'username' },
              {
                title: '添加为', key: 'role', width: 160,
                render: (_: any, record: any) => (
                  <Select defaultValue="member" style={{ width: 120 }}>
                    <Select.Option value="admin">管理员</Select.Option>
                    <Select.Option value="member">成员</Select.Option>
                  </Select>
                ),
              },
            ]}
            dataSource={[
              { id: 1, username: '李四' },
              { id: 2, username: '王五' },
            ]}
          />
        </Form>
      </Modal>

      {/* Permission Change Modal */}
      <Modal title="权限变更" open={isPermChangeModalOpen} onOk={handlePermChange}
        onCancel={() => { setIsPermChangeModalOpen(false); permChangeForm.resetFields() }}>
        <Form form={permChangeForm} layout="vertical" onFinish={handlePermChange}>
          <Form.Item label="成员" name="user" rules={[{ required: true, message: '请选择成员' }]}>
            <Input placeholder="请输入成员名称" />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true, message: '请选择角色' }]}>
            <Select placeholder="请选择">
              <Select.Option value="admin">管理员</Select.Option>
              <Select.Option value="member">成员</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      {/* Member Permission Modal */}
      <Modal
        title="项目成员及权限"
        open={isMemberPermModalOpen}
        onCancel={() => setIsMemberPermModalOpen(false)}
        width={700}
        footer={[<Button key="close" onClick={() => setIsMemberPermModalOpen(false)}>关闭</Button>]}
      >
        <div style={{ marginBottom: 16 }}>
          <Space size="large">
            <span
              style={{
                fontWeight: activeMemberTab === 'members' ? 'bold' : 'normal',
                cursor: 'pointer',
                paddingBottom: 8,
                borderBottom: activeMemberTab === 'members' ? '2px solid #4045D6' : 'none',
              }}
              onClick={() => setActiveMemberTab('members')}>项目成员</span>
            <span
              style={{
                fontWeight: activeMemberTab === 'permissions' ? 'bold' : 'normal',
                cursor: 'pointer',
                paddingBottom: 8,
                borderBottom: activeMemberTab === 'permissions' ? '2px solid #4045D6' : 'none',
              }}
              onClick={() => setActiveMemberTab('permissions')}>权限设置</span>
          </Space>
        </div>

        {activeMemberTab === 'members' ? (
          <div>
            <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Input placeholder="请输入成员名称" prefix={<SearchOutlined />} style={{ width: 250 }} />
              <Button type="primary" icon={<UserAddOutlined />} size="small">邀请成员</Button>
            </div>
            <Table size="small" rowKey="id" pagination={{ pageSize: 5, showTotal: (t) => `共 ${t} 条` }}
              columns={[
                { title: '邀请人', dataIndex: 'inviter', key: 'inviter' },
                { title: '用户', dataIndex: 'username', key: 'username' },
                { title: '用户组', dataIndex: 'group', key: 'group',
                  render: (v: string) => <Tag color={v === '管理员' ? 'blue' : 'default'}>{v}</Tag> },
                { title: '加入时间', dataIndex: 'joined_at', key: 'joined_at' },
                {
                  title: '操作', key: 'action',
                  render: (_: any, record: any) => (
                    <Space>
                      <Button type="link" size="small" onClick={() => setIsPermChangeModalOpen(true)}>权限变更</Button>
                      <Popconfirm title="确认删除该成员？" onConfirm={() => message.success('已删除')}>
                        <Button type="link" size="small" danger>删除</Button>
                      </Popconfirm>
                    </Space>
                  ),
                },
              ]}
              dataSource={[
                { id: 1, inviter: '王五', username: '赵四', group: '管理员', joined_at: '2024-08-03 10:25:54' },
                { id: 2, inviter: '王五', username: '王二', group: '成员', joined_at: '2024-08-02 10:25:54' },
                { id: 3, inviter: '王五', username: '李三', group: '成员', joined_at: '2024-08-01 10:25:54' },
              ]}
            />
          </div>
        ) : (
          <div>
            <div style={{ display: 'flex', gap: 32, marginBottom: 24 }}>
              <div>
                <b>用户组</b>
                <div style={{ marginTop: 8 }}>
                  <Tag color="blue">管理员（1）</Tag>
                  <Tag>成员（2）</Tag>
                </div>
              </div>
            </div>
            <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 16 }}>
              <h4 style={{ fontSize: 14, marginBottom: 16 }}>项目权限设置</h4>
              <Checkbox checked style={{ marginBottom: 8, display: 'block' }}>全选</Checkbox>
              <Space direction="vertical" size={4}>
                <Checkbox checked>邀请用户</Checkbox>
                <Checkbox checked>删除用户</Checkbox>
                <Checkbox checked>删除项目</Checkbox>
                <Checkbox checked>标记可烧录/安装文件</Checkbox>
                <Checkbox checked>下载文件</Checkbox>
              </Space>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

export default Repository
