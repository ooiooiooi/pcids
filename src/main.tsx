import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider, App as AntdApp, message } from 'antd'
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
import LoginLog from './pages/LoginLog'
import OperationLog from './pages/OperationLog'
import User from './pages/User'
import Role from './pages/Role'
import BackendServiceErrorNotice from './components/BackendServiceErrorNotice'
import LicenseGate from './components/LicenseGate'
import { installGlobalBackendFetchGuard, installGlobalBackendMessageGuard } from './services/backendErrorCenter'
import './styles/index.css'

// 配置 Ant Design 主题
const theme = {
  token: {
    colorPrimary: '#4361ee',
    colorSuccess: '#3DD07B',
    colorWarning: '#F5C400',
    colorError: '#F53F3F',
    borderRadius: 6,
    fontFamily: "'PingFang SC', 'AlibabaPuHuiTi', 'Microsoft YaHei', sans-serif",
  },
  components: {
    Button: {
      borderRadius: 6,
      controlHeight: 38,
    },
    Input: {
      borderRadius: 10,
      controlHeight: 32,
    },
    Select: {
      borderRadius: 10,
      controlHeight: 32,
    },
    DatePicker: {
      borderRadius: 10,
      controlHeight: 32,
    },
    Card: {
      borderRadiusLG: 6,
    },
  },
}

import { useOutletContext } from 'react-router-dom'

// Workbench 包装组件，用于传递 context
const WorkbenchWrapper = () => {
  const context = useOutletContext<any>()
  return <Workbench onOpenMessage={context?.openMessage} />
}

installGlobalBackendFetchGuard()
installGlobalBackendMessageGuard(message)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntdApp style={{ height: '100%' }}>
        <BackendServiceErrorNotice />
        <LicenseGate>
          <HashRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/" element={<App />}>
                <Route index element={<Navigate to="/workbench" replace />} />
                <Route path="workbench" element={<WorkbenchWrapper />} />
                <Route path="repository" element={<Repository />} />
                <Route path="burning" element={<Burning />} />
                <Route path="injection" element={<Injection />} />
                <Route path="protocol" element={<Protocol />} />
                <Route path="record" element={<Record />} />
                <Route path="product" element={<Product />} />
                <Route path="burner" element={<Burner />} />
                <Route path="script" element={<Script />} />
                <Route path="log/login" element={<LoginLog />} />
                <Route path="log/operation" element={<OperationLog />} />
                <Route path="user" element={<User />} />
                <Route path="role" element={<Role />} />
              </Route>
            </Routes>
          </HashRouter>
        </LicenseGate>
      </AntdApp>
    </ConfigProvider>
  </React.StrictMode>,
)
