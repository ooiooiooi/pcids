import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Checkbox, App as AntdApp } from 'antd'
import { UserOutlined, LockOutlined, CloseOutlined } from '@ant-design/icons'
import { authApi } from '../../services/api'
import { permissionApi } from '../../services/permission'
import { usePermission } from '../../hooks'
import LoginIllustration from '../../assets/images/login-illustration.png'
import SoftwareLogo from '../../assets/images/software-logo.svg'

interface LoginForm {
  account: string
  password: string
}

const Login: React.FC = () => {
  const navigate = useNavigate()
  const { message } = AntdApp.useApp()
  const [loading, setLoading] = useState(false)
  const [remember, setRemember] = useState(false)
  const { setPermissions, setMenus } = usePermission()

  const onFinish = async (values: LoginForm) => {
    setLoading(true)
    try {
      const response: any = await authApi.login(values.account, values.password)

      localStorage.setItem('token', response.access_token)
      localStorage.setItem('user', JSON.stringify({ username: values.account }))

      const [permsRes, menusRes] = await Promise.all([
        permissionApi.getMyPermissions().catch(() => ({ code: 1, data: [] })),
        permissionApi.getMenus().catch(() => ({ code: 1, data: [] })),
      ])

      const permsData = (permsRes as any).code === 0 ? (permsRes as any).data : []
      if (permsData.length > 0) {
        setPermissions(permsData)
      }

      const menusData = (menusRes as any).code === 0 ? (menusRes as any).data : []
      if (menusData.length > 0) {
        setMenus(menusData)
      }

      message.success('登录成功')
      navigate('/')
    } catch (error: any) {
      console.error('Login failed:', error)
      message.error(error?.response?.data?.detail || error?.message || '登录失败，请检查账号密码或网络连接')
    } finally {
      setLoading(false)
    }
  }

  const handleClose = () => {
    // 适配 Electron 窗口关闭或普通网页关闭
    try {
      if ((window as any).electronAPI) {
        (window as any).electronAPI.send('window-close')
      } else {
        window.close()
      }
    } catch (e) {
      console.error(e)
    }
  }

  return (
    <div style={{ 
      height: '100vh', 
      width: '100vw', 
      display: 'flex', 
      background: '#fff', 
      position: 'relative'
    }}>
      {/* Close Button */}
      <CloseOutlined 
        onClick={handleClose}
        style={{ 
          position: 'absolute', 
          top: 24, 
          right: 24, 
          fontSize: 24, 
          color: '#86909c', 
          cursor: 'pointer',
          zIndex: 10,
          fontWeight: 'lighter'
        }} 
      />

      {/* Left Pane */}
      <div style={{ 
        flex: '0 0 50%', 
        background: '#f7f8fb', 
        display: 'flex', 
        flexDirection: 'column', 
        padding: '60px 48px',
        position: 'relative'
      }}>
          {/* Logo & Title */}
          <div style={{ display: 'flex', alignItems: 'flex-start', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', color: 'var(--pcids-brand-color)', fontSize: 32, fontWeight: 800 }}>
              <img src={SoftwareLogo} alt="软件图标" style={{ width: 40, height: 40, marginRight: 12, objectFit: 'contain' }} />
              <span>程控安装部署系统</span>
            </div>
            <div style={{ color: '#86909c', fontSize: 16, marginTop: 12, letterSpacing: 1 }}>
              安全可靠的嵌入式软件安装性测试解决方案
            </div>
          </div>

          {/* Illustration */}
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px 0' }}>
            <img 
              src={LoginIllustration} 
              alt="Login Illustration" 
              style={{ width: '100%', maxWidth: 400, objectFit: 'contain' }} 
            />
          </div>

          {/* Version */}
          <div style={{ textAlign: 'center', color: '#c9cdd4', fontSize: 16 }}>
            v1.0.0
          </div>
        </div>

        {/* Right Pane */}
        <div style={{ 
          flex: '0 0 50%', 
          padding: '60px 80px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center'
        }}>
          <div style={{ marginBottom: 48 }}>
            <h2 style={{ fontSize: 32, color: '#1d2129', fontWeight: 800, margin: 0 }}>欢迎回来</h2>
            <p style={{ color: '#86909c', fontSize: 16, marginTop: 12 }}>请输入您的账号信息</p>
          </div>

          <Form layout="vertical" onFinish={onFinish} requiredMark={false} size="large">
            <Form.Item
              label={<span style={{ fontSize: 18, fontWeight: 500, color: '#1d2129' }}>账号</span>}
              name="account"
              rules={[{ required: true, message: '请输入账号' }]}
              style={{ marginBottom: 28 }}
            >
              <Input
                prefix={<UserOutlined style={{ color: '#c9cdd4', fontSize: 20, marginRight: 8 }} />}
                placeholder="请输入账号"
                style={{ borderRadius: 8, height: 52, fontSize: 16 }}
              />
            </Form.Item>

            <Form.Item
              label={<span style={{ fontSize: 18, fontWeight: 500, color: '#1d2129' }}>密码</span>}
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
              style={{ marginBottom: 28 }}
            >
              <Input.Password
                prefix={<LockOutlined style={{ color: '#c9cdd4', fontSize: 20, marginRight: 8 }} />}
                placeholder="请输入密码"
                style={{ borderRadius: 8, height: 52, fontSize: 16 }}
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 48 }}>
              <Checkbox 
                checked={remember} 
                onChange={(e) => setRemember(e.target.checked)}
                style={{ fontSize: 16, color: '#4e5969' }}
              >
                记住密码
              </Checkbox>
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                block
                style={{
                  background: '#4361ee',
                  border: 'none',
                  height: 52,
                  borderRadius: 8,
                  fontSize: 18,
                  fontWeight: 600,
                  letterSpacing: 2
                }}
              >
                登录
              </Button>
            </Form.Item>
          </Form>
        </div>
    </div>
  )
}

export default Login
