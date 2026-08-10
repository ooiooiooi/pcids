import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { App as AntdApp, Button, Spin, Tag } from 'antd'
import {
  CheckCircleOutlined,
  CopyOutlined,
  KeyOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { licenseApi } from '../services/api'
import DesktopWindowControls from './DesktopWindowControls'
import SoftwareLogo from '../assets/images/software-logo.svg'

interface LicenseStatus {
  valid: boolean
  state: string
  message: string
  machine_code: string
  machine_fingerprint: string
  license_path: string
  license?: {
    license_id?: string
    customer_name?: string
    installation_no?: number
    installation_limit?: number
    expires_at?: string | null
  } | null
}

const LicenseGate = ({ children }: { children: ReactNode }) => {
  const { message } = AntdApp.useApp()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<LicenseStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [serviceError, setServiceError] = useState('')

  const loadStatus = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const response: any = await licenseApi.getStatus()
      setStatus(response.data)
      setServiceError('')
    } catch (error: any) {
      setServiceError(error?.response?.data?.detail || '暂时无法连接本机授权服务')
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
    const handleInvalidLicense = () => void loadStatus(true)
    window.addEventListener('pcids-license-invalid', handleInvalidLicense)
    const timer = window.setInterval(() => void loadStatus(true), 5000)
    return () => {
      window.removeEventListener('pcids-license-invalid', handleInvalidLicense)
      window.clearInterval(timer)
    }
  }, [loadStatus])

  const initialChecking = loading && !status && !serviceError
  const showGate = Boolean(serviceError || (status && !status.valid) || (!loading && !status))
  const isDesktopRuntime = Boolean(window.electronAPI?.windowControls)

  useEffect(() => {
    if (!showGate) return
    const viewportElements = [document.documentElement, document.body, document.getElementById('root')].filter(Boolean) as HTMLElement[]
    viewportElements.forEach((element) => element.classList.add('pcids-license-viewport'))
    if (isDesktopRuntime) window.electronAPI?.windowControls.setMode('login')
    return () => viewportElements.forEach((element) => element.classList.remove('pcids-license-viewport'))
  }, [isDesktopRuntime, showGate])

  // Keep the current desktop window mode while the fast local check runs.
  // Rendering the gate here would briefly resize a refreshed main window to
  // the login dimensions before the valid response arrives.
  if (initialChecking) return null
  if (!showGate) return <>{children}</>

  const copyMachineCode = async () => {
    if (!status?.machine_code) return
    await navigator.clipboard.writeText(status.machine_code)
    message.success('机器码已复制')
  }

  const importLicense = async (file?: File) => {
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    setImporting(true)
    try {
      const response: any = await licenseApi.importLicense(form)
      setStatus(response.data)
      setServiceError('')
      message.success('License 已安装并生效')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || 'License 导入失败')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const stateLabel: Record<string, string> = {
    missing: '未授权',
    expired: '已过期',
    machine_mismatch: '机器不匹配',
    public_key_error: '授权组件异常',
    invalid: '授权无效',
  }

  return (
    <div className="license-gate">
      {isDesktopRuntime ? (
        <div className="login-desktop-titlebar">
          <div className="login-desktop-titlebar__brand">
            <img src={SoftwareLogo} alt="" />
            <span>程控安装部署系统</span>
          </div>
          <DesktopWindowControls />
        </div>
      ) : null}

      <main className="license-gate__content">
        <section className="license-gate__identity">
          <img src={SoftwareLogo} alt="软件图标" />
          <div>
            <span className="license-gate__eyebrow">PCIDS DESKTOP</span>
            <h1>软件授权</h1>
            <p>当前计算机需要有效的离线机器授权。</p>
          </div>
          <SafetyCertificateOutlined className="license-gate__watermark" />
        </section>

        <section className="license-gate__panel">
          {loading && !status ? (
            <div className="license-gate__loading">
              <Spin size="large" />
              <span>正在检查本机授权</span>
            </div>
          ) : serviceError ? (
            <div className="license-gate__state">
              <KeyOutlined />
              <Tag color="error">服务未就绪</Tag>
              <h2>无法读取授权状态</h2>
              <p>{serviceError}</p>
              <Button icon={<ReloadOutlined />} onClick={() => void loadStatus()}>重新检查</Button>
            </div>
          ) : (
            <>
              <div className="license-gate__heading">
                <div>
                  <Tag color="warning">{stateLabel[status?.state || ''] || '需要授权'}</Tag>
                  <h2>{status?.message || '尚未安装 License'}</h2>
                  <p>运行独立签发工具生成本机授权，或导入已签发的 License 文件。</p>
                </div>
                <KeyOutlined />
              </div>

              <div className="license-gate__machine">
                <span>本机机器码</span>
                <strong>{status?.machine_code}</strong>
                <Button type="text" icon={<CopyOutlined />} title="复制机器码" aria-label="复制机器码" onClick={() => void copyMachineCode()} />
              </div>

              <dl className="license-gate__details">
                <div>
                  <dt>License 位置</dt>
                  <dd>{status?.license_path}</dd>
                </div>
                <div>
                  <dt>授权方式</dt>
                  <dd>Ed25519 签名 / 本机绑定</dd>
                </div>
              </dl>

              <div className="license-gate__actions">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".lic,application/json"
                  hidden
                  onChange={(event) => void importLicense(event.target.files?.[0])}
                />
                <Button type="primary" icon={<UploadOutlined />} loading={importing} onClick={() => fileInputRef.current?.click()}>
                  导入 License
                </Button>
                <Button icon={<ReloadOutlined />} onClick={() => void loadStatus()}>重新检查</Button>
              </div>

              <div className="license-gate__assurance">
                <CheckCircleOutlined />
                <span>授权成功后，登录、数据同步、烧录安装和硬件测试功能将自动开放。</span>
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  )
}

export default LicenseGate
