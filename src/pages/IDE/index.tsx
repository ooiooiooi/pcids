import { Card, Table, Input, Space, Button, Tag, Popconfirm, message } from 'antd'
import { useState, useEffect } from 'react'
import { Permission } from '../../hooks'

// IDE management - placeholder page (prototype references IDE management under 资产管理)
// This page can be expanded based on actual IDE management requirements

const IDE: React.FC = () => {
  const [loading] = useState(false)
  const [dataSource, setDataSource] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [params, setParams] = useState({ page: 1, page_size: 10, keyword: '' })

  // Placeholder data - to be connected to backend API when IDE management spec is defined
  useEffect(() => {
    setDataSource([])
    setTotal(0)
  }, [params])

  const columns = [
    { title: 'IDE 名称', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type', render: (val: string) => <Tag>{val || '-'}</Tag> },
    { title: '版本', dataIndex: 'version', key: 'version' },
    { title: '关联板卡', dataIndex: 'board', key: 'board' },
    {
      title: '操作', key: 'action',
      render: (_: any, _record: any) => (
        <Space>
          <Permission code="ide:edit">
            <Button type="link">编辑</Button>
          </Permission>
          <Permission code="ide:delete">
            <Popconfirm title="确认删除" onConfirm={() => message.info('删除功能待实现')}>
              <Button type="link" danger>删除</Button>
            </Popconfirm>
          </Permission>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div><h1>IDE管理</h1><p style={{ color: '#999' }}>管理集成开发环境配置</p></div>
      </div>

      <Card>
        <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
          <Input placeholder="请输入IDE名称" style={{ width: 200 }}
            onPressEnter={(e: any) => setParams({ ...params, page: 1, keyword: e.target.value })} />
          <Button type="primary" onClick={() => setParams({ ...params, page: 1 })}>搜索</Button>
        </div>
        <div style={{ marginBottom: 8, color: '#999' }}>
          共 {total} 条
        </div>
        <Table columns={columns} dataSource={dataSource} rowKey="id" loading={loading}
          pagination={{ total, pageSize: params.page_size, current: params.page,
            onChange: (page) => setParams({ ...params, page }) }} />
      </Card>
    </div>
  )
}

export default IDE
