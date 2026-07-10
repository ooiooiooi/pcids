import { useEffect, useState } from 'react'
import { Modal, Button, Typography } from 'antd'
import { CloseCircleFilled } from '@ant-design/icons'
import { setBackendServiceNoticeActive, subscribeBackendServiceError } from '../services/backendErrorCenter'
import type { BackendServiceErrorPayload } from '../services/backendErrorClassifier'

const { Text } = Typography

const BackendServiceErrorNotice: React.FC = () => {
  const [open, setOpen] = useState(false)
  const [payload, setPayload] = useState<BackendServiceErrorPayload | null>(null)

  useEffect(() => {
    return subscribeBackendServiceError((nextPayload) => {
      setPayload(nextPayload)
      setBackendServiceNoticeActive(true)
      setOpen(true)
    })
  }, [])

  return (
    <Modal
      open={open}
      closable={false}
      footer={null}
      centered
      width={400}
      maskClosable={false}
      styles={{
        body: { padding: '32px 24px' },
      }}
    >
      <div style={{ textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <CloseCircleFilled style={{ fontSize: 64, color: '#ff4d4f', marginBottom: 24 }} />
        <div style={{ fontSize: 20, fontWeight: 600, color: '#1f2329', marginBottom: 12 }}>
          {payload?.summary || '服务异常'}
        </div>
        <Text type="secondary" style={{ fontSize: 14, marginBottom: 32 }}>
          {payload?.description || '服务异常，请重启软件'}
        </Text>
        <Button 
          type="primary" 
          size="large"
          onClick={() => {
            setOpen(false)
            setBackendServiceNoticeActive(false)
          }} 
          style={{ width: '100%', borderRadius: 8, fontWeight: 500 }}
        >
          我知道了
        </Button>
      </div>
    </Modal>
  )
}

export default BackendServiceErrorNotice
