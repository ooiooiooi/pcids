import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Form, Input, Button, Checkbox, Card, message } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { authApi } from '../../services/api'
import { permissionApi } from '../../services/permission'
import { usePermission } from '../../hooks'

interface LoginForm {
  account: string
  password: string
}

const Login: React.FC = () => {
  const navigate = useNavigate()
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
        background: '#fff',
      }}
    >
      <Card
        style={{
          width: 400,
          borderRadius: 6,
          boxShadow: '0 3px 6px rgba(0,0,0,0.15)',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            width: 50, height: 50, margin: '0 auto 12px',
            background: 'linear-gradient(135deg, #EC5E43 0%, #4045D6 100%)',
            borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <span style={{ color: '#fff', fontSize: 24, fontWeight: 'bold' }}>⚙</span>
          </div>
          <h2 style={{ marginBottom: 6, color: '#4045D6', fontSize: 18, fontWeight: 'bold', fontFamily: 'Microsoft YaHei' }}>
            程控安装部署系统
          </h2>
          <p style={{ color: 'rgba(0, 0, 0, 0.5)', fontSize: 12, fontFamily: 'AlibabaPuHuiTi' }}>
            安全可靠的软件镜像安装性测试解决方案
          </p>
        </div>

        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item
            name="account"
            label={<span style={{ color: '#000', fontSize: 14, fontFamily: 'PingFang SC' }}>账号</span>}
            rules={[{ required: true, message: '请输入账号' }]}
          >
            <Input
              prefix={<UserOutlined style={{ color: 'rgba(0,0,0,0.25)' }} />}
              placeholder="请输入账号"
              style={{ height: 32, borderRadius: 2 }}
            />
          </Form.Item>

          <Form.Item
            name="password"
            label={<span style={{ color: '#000', fontSize: 14, fontFamily: 'PingFang SC' }}>密码</span>}
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined style={{ color: 'rgba(0,0,0,0.25)' }} />}
              placeholder="请输入密码"
              style={{ height: 32, borderRadius: 2 }}
            />
          </Form.Item>

          <Form.Item>
            <Checkbox checked={remember} onChange={(e) => setRemember(e.target.checked)}
              style={{ fontFamily: 'Microsoft YaHei', fontSize: 12 }}>
              记住密码
            </Checkbox>
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              style={{
                width: 240,
                background: 'linear-gradient(135deg, #EC5E43 0%, #4045D6 100%)',
                border: 'none',
                height: 32,
                borderRadius: 6,
                fontSize: 14,
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
