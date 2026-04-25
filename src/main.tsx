import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import Login from './pages/Login'
import Workbench from './pages/Workbench'
import Repository from './pages/Repository'
import Burning from './pages/Burning'
import Injection from './pages/Injection'
import Protocol from './pages/Protocol'
import Record from './pages/Record'
import Product from './pages/Product'
import Burner from './pages/Burner'
import Script from './pages/Script'
import IDE from './pages/IDE'
import LoginLog from './pages/LoginLog'
import OperationLog from './pages/OperationLog'
import User from './pages/User'
import Role from './pages/Role'
import './styles/index.css'

// 配置 Ant Design 主题
const theme = {
  token: {
    colorPrimary: '#4045D6',
    colorSuccess: '#3DD07B',
    colorWarning: '#F5C400',
    colorError: '#F53F3F',
    borderRadius: 6,
    fontFamily: "'PingFang SC', 'AlibabaPuHuiTi', 'Microsoft YaHei', sans-serif",
  },
  components: {
    Button: {
      borderRadius: 6,
      controlHeight: 32,
    },
    Input: {
      borderRadius: 2,
      controlHeight: 32,
    },
    Select: {
      borderRadius: 2,
      controlHeight: 32,
    },
    Card: {
      borderRadiusLG: 6,
    },
  },
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <HashRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<App />}>
            <Route index element={<Navigate to="/workbench" replace />} />
            <Route path="workbench" element={<Workbench />} />
            <Route path="repository" element={<Repository />} />
            <Route path="burning" element={<Burning />} />
            <Route path="injection" element={<Injection />} />
            <Route path="protocol" element={<Protocol />} />
            <Route path="record" element={<Record />} />
            <Route path="product" element={<Product />} />
            <Route path="burner" element={<Burner />} />
            <Route path="script" element={<Script />} />
            <Route path="ide" element={<IDE />} />
            <Route path="log/login" element={<LoginLog />} />
            <Route path="log/operation" element={<OperationLog />} />
            <Route path="user" element={<User />} />
            <Route path="role" element={<Role />} />
          </Route>
        </Routes>
      </HashRouter>
    </ConfigProvider>
  </React.StrictMode>,
)
