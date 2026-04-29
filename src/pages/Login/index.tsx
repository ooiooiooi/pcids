import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Checkbox, Card, App as AntdApp } from 'antd'
import { UserOutlined, LockOutlined, CloseOutlined } from '@ant-design/icons'
import { authApi } from '../../services/api'
import { permissionApi } from '../../services/permission'
import { usePermission } from '../../hooks'

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

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#f2f3f5',
      }}
    >
      <Card
        style={{
          width: 420,
          borderRadius: 6,
          boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          position: 'relative'
        }}
        styles={{ body: { padding: '32px 40px' } }}
      >
        <CloseOutlined style={{ position: 'absolute', top: 16, right: 16, fontSize: 16, color: '#86909c', cursor: 'pointer' }} />
        
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            width: 64, height: 64, margin: '0 auto 16px',
            background: '#4045D6',
            borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
            position: 'relative'
          }}>
            {/* CPU Style Icon */}
            <div style={{ width: 24, height: 24, border: '2px solid #fff', borderRadius: 4, background: 'transparent' }}></div>
            {/* Top pins */}
            <div style={{ position: 'absolute', top: -6, left: '25%', width: 3, height: 6, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', top: -6, left: '50%', transform: 'translateX(-50%)', width: 3, height: 6, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', top: -6, right: '25%', width: 3, height: 6, background: '#4045D6', borderRadius: 1 }}></div>
            {/* Bottom pins */}
            <div style={{ position: 'absolute', bottom: -6, left: '25%', width: 3, height: 6, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', bottom: -6, left: '50%', transform: 'translateX(-50%)', width: 3, height: 6, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', bottom: -6, right: '25%', width: 3, height: 6, background: '#4045D6', borderRadius: 1 }}></div>
            {/* Left pins */}
            <div style={{ position: 'absolute', left: -6, top: '25%', width: 6, height: 3, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', left: -6, top: '50%', transform: 'translateY(-50%)', width: 6, height: 3, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', left: -6, bottom: '25%', width: 6, height: 3, background: '#4045D6', borderRadius: 1 }}></div>
            {/* Right pins */}
            <div style={{ position: 'absolute', right: -6, top: '25%', width: 6, height: 3, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', right: -6, top: '50%', transform: 'translateY(-50%)', width: 6, height: 3, background: '#4045D6', borderRadius: 1 }}></div>
            <div style={{ position: 'absolute', right: -6, bottom: '25%', width: 6, height: 3, background: '#4045D6', borderRadius: 1 }}></div>
          </div>
          <h2 style={{ marginBottom: 8, color: '#4045D6', fontSize: 20, fontWeight: 'bold', fontFamily: 'Microsoft YaHei' }}>
            程控安装部署系统
          </h2>
          <p style={{ color: '#86909c', fontSize: 13, fontFamily: 'PingFang SC', margin: 0 }}>
            安全可靠的软件镜像安装性测试解决方案
          </p>
        </div>

        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item
            name="account"
            label={<span style={{ color: '#1d2129', fontSize: 14, fontWeight: 500, fontFamily: 'PingFang SC' }}>账号</span>}
            rules={[{ required: true, message: '请输入账号' }]}
            style={{ marginBottom: 20 }}
          >
            <Input
              prefix={<UserOutlined style={{ color: '#c9cdd4', marginRight: 4 }} />}
              placeholder="请输入账号"
              style={{ height: 40, borderRadius: 4, borderColor: '#e5e6eb' }}
            />
          </Form.Item>

          <Form.Item
            name="password"
            label={<span style={{ color: '#1d2129', fontSize: 14, fontWeight: 500, fontFamily: 'PingFang SC' }}>密码</span>}
            rules={[{ required: true, message: '请输入密码' }]}
            style={{ marginBottom: 16 }}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: '#c9cdd4', marginRight: 4 }} />}
              placeholder="请输入密码"
              style={{ height: 40, borderRadius: 4, borderColor: '#e5e6eb' }}
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 24 }}>
            <Checkbox checked={remember} onChange={(e) => setRemember(e.target.checked)}
              style={{ fontFamily: 'PingFang SC', fontSize: 14, color: '#4e5969' }}>
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
                background: '#4045D6',
                border: 'none',
                height: 40,
                borderRadius: 4,
                fontSize: 16,
                fontWeight: 500,
                fontFamily: 'PingFang SC',
                color: '#fff',
              }}
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  )
}

export default Login
