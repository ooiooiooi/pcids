/**
 * API 服务封装
 */
import axios, { AxiosInstance } from 'axios'
import { message } from 'antd'

const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' 
  ? 'http://127.0.0.1:8000/api' 
  : '/api'

// 创建 axios 实例
const request: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    return response.data
  },
  (error) => {
    if (error.response) {
      const { status, data } = error.response
      const requestUrl = String(error.config?.url || '')
      const isAuthApi = requestUrl.includes('/auth/')
      const shouldRedirectToLogin = status === 401 && isAuthApi

      if (shouldRedirectToLogin) {
        const currentHash = window.location.hash
        if (!currentHash.startsWith('#/login')) {
          localStorage.removeItem('token')
          localStorage.removeItem('user')
          window.location.href = '#/login'
        }
      } else if (status === 401) {
        if (!requestUrl.includes('/auth/login')) {
          message.error(data?.detail || '请求未通过认证')
        }
      } else if (status === 403) {
        // 如果是登录接口，不需要在这里弹出错误，交由页面处理
        if (!requestUrl.includes('/auth/login')) {
          if (data?.detail) {
            message.error(data.detail)
          } else {
            message.error('无权限访问')
          }
        }
      } else if (status === 404) {
        message.error('资源不存在')
      } else {
        // 对于其他错误，如果也是登录接口，也交由页面处理
        if (!requestUrl.includes('/auth/login')) {
          message.error(data?.detail || '请求失败')
        }
      }
    } else {
      message.error('网络错误，请检查后端服务是否启动')
    }
    return Promise.reject(error)
  }
)

export default request

// 认证服务
export const authApi = {
  login: (username: string, password: string) => {
    const params = new URLSearchParams()
    params.append('username', username)
    params.append('password', password)
    return request.post('/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
  },
  getMe: () => request.get('/auth/me'),
  updateMe: (data: any) => request.put('/auth/me', data),
  updatePassword: (data: any) => request.put('/auth/password', data),
  uploadAvatar: (data: FormData) => request.post('/auth/avatar', data, {
    headers: { 'Content-Type': 'multipart/form-data' }
  }),
}

// 消息服务
export const messageApi = {
  getList: (params?: { page?: number; page_size?: number; is_read?: number }) =>
    request.get('/messages', { params }),
  readAll: () => request.put('/messages/read-all'),
}

// 用户服务
export const userApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; role_id?: number; status?: number; sort_field?: string; sort_order?: string }) =>
    request.get('/users', { params }),
  getById: (id: number) => request.get(`/users/${id}`),
  create: (data: { username: string; password: string; email?: string; role_id?: number }) =>
    request.post('/users', data),
  update: (id: number, data: { email?: string; role_id?: number; status?: number }) =>
    request.put(`/users/${id}`, data),
  delete: (id: number) => request.delete(`/users/${id}`),
  resetPassword: (id: number) => request.put(`/users/${id}/reset-password`),
}

// 角色服务
export const roleApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string }) =>
    request.get('/roles', { params }),
  create: (data: { name: string; description?: string; status?: number; data_scope?: string; permission_ids?: number[] }) =>
    request.post('/roles', data),
  update: (id: number, data: { name?: string; description?: string; status?: number; data_scope?: string; permission_ids?: number[] }) =>
    request.put(`/roles/${id}`, data),
  delete: (id: number) => request.delete(`/roles/${id}`),
}

// 产品服务
export const productApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; chip_type?: string; sort_field?: string; sort_order?: string }) =>
    request.get('/products', { params }),
  create: (data: {
    name: string
    chip_type: string
    serial_number: string
    voltage?: string
    temp_range?: string
    interface?: string
    config_description?: string
    usage_description?: string
    board_image: string
  }) =>
    request.post('/products', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/products/${id}`, data),
  delete: (id: number) => request.delete(`/products/${id}`),
  uploadImage: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request.post('/products/upload-image', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}

// 烧录器服务
export const burnerApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; status?: number; sort_field?: string; sort_order?: string }) =>
    request.get('/burners', { params }),
  create: (data: { name: string; type: string }) =>
    request.post('/burners', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/burners/${id}`, data),
  delete: (id: number) => request.delete(`/burners/${id}`),
  scan: (data?: { burner_id?: number; type?: string; location?: string; strategy?: number }) => request.post('/burners/scan', data),
}

// 脚本服务
export const scriptApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; sort_field?: string; sort_order?: string }) =>
    request.get('/scripts', { params }),
  create: (data: { name: string; type: string; content: string }) =>
    request.post('/scripts', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/scripts/${id}`, data),
  delete: (id: number) => request.delete(`/scripts/${id}`),
  getContent: (id: number) => request.get(`/scripts/${id}/content`),
}

// 烧录任务服务
export const taskApi = {
  getList: (params?: { page?: number; page_size?: number; status?: number; sort_field?: string; sort_order?: string; board_name?: string; keyword?: string }) =>
    request.get('/tasks', { params }),
  create: (data: { software_name: string; repository_id?: number; task_type?: 'board' | 'os'; board_name?: string; config_json?: string; target_ip?: string; target_port?: number; product_id?: number; burner_id?: number; script_id?: number; agent_url?: string; keep_local?: number; integrity?: number; expected_checksum?: string; version_check?: number; history_checksum?: string }) =>
    request.post('/tasks', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/tasks/${id}`, data),
  delete: (id: number) => request.delete(`/tasks/${id}`),
  getById: (id: number) => request.get(`/tasks/${id}`),
  execute: (id: number) => request.post(`/tasks/${id}/execute`),
  override: (id: number) => request.post(`/tasks/${id}/override`),
  getConsistencyReportHtml: (id: number, print = false) =>
    request.get(`/tasks/${id}/consistency/report/html`, { params: { print: print ? 1 : 0 }, responseType: 'blob' as any }),
  getConsistencyReportCsv: (id: number) =>
    request.get(`/tasks/${id}/consistency/report/csv`, { responseType: 'blob' as any }),
}

// 履历记录服务
export const recordApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; sort_field?: string; sort_order?: string; serial_number?: string; start_date?: string; end_date?: string; os_name?: string }) =>
    request.get('/records', { params }),
  create: (data: Record<string, any>) =>
    request.post('/records', data),
}

// 日志服务
export const logApi = {
  getLoginLogs: (params?: { page?: number; page_size?: number; user_id?: number; start_date?: string; end_date?: string }) =>
    request.get('/logs/login', { params }),
  getOperationLogs: (params?: { page?: number; page_size?: number; module?: string; start_date?: string; end_date?: string }) =>
    request.get('/logs/operation', { params }),
  clearLoginLogs: () => request.delete('/logs/login/clear'),
  clearOperationLogs: () => request.delete('/logs/operation/clear'),
}

// 制品仓库服务
export const repositoryApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string }) =>
    request.get('/repositories', { params }),
  create: (data: { name: string; repo_id?: string; tenant?: string; description?: string; version?: string; file_url?: string; size?: number; md5?: string; sha256?: string; project_key?: string }) =>
    request.post('/repositories', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/repositories/${id}`, data),
  delete: (id: number) => request.delete(`/repositories/${id}`),
  getById: (id: number) => request.get(`/repositories/${id}`),
  uploadFile: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request.post('/repositories/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getTree: (params?: { mode?: 'online' | 'offline' }) => request.get('/repositories/tree', { params }),
  getCodeartsConfig: () => request.get('/repositories/codearts/config'),
  setCodeartsConfig: (data: Record<string, any>) => request.post('/repositories/codearts/config', data),
  syncCodeartsProject: (data: Record<string, any>) => request.post('/repositories/codearts/sync', data),
  importCodeartsArtifact: (data: { project_id: string; package_id: string; version_id: string; repo_id?: string; download_uri?: string; name?: string; version?: string; description?: string }) =>
    request.post('/repositories/codearts/import', data),
  downloadCodeartsArtifactToServer: (data: { project_id: string; download_uri: string; name?: string; id?: number; target?: 'server' | 'local' }) =>
    request.post('/repositories/codearts/download/server', data),
  downloadCodeartsArtifactToLocal: (params: { project_id: string; download_uri: string; name?: string }) =>
    request.get('/repositories/codearts/download/local', { params, responseType: 'blob' }),
  downloadLocalRepositoryFile: (id: number) =>
    request.get(`/repositories/${id}/download`, { responseType: 'blob' }),
  listProjectMembers: (projectKey: string) => request.get(`/repositories/projects/${projectKey}/members`),
  inviteProjectMember: (projectKey: string, data: { username: string; role?: 'admin' | 'member' }) =>
    request.post(`/repositories/projects/${projectKey}/members`, data),
  updateProjectMemberRole: (projectKey: string, userId: number, data: { role: 'admin' | 'member' }) =>
    request.put(`/repositories/projects/${projectKey}/members/${userId}`, data),
  deleteProjectMember: (projectKey: string, userId: number) => request.delete(`/repositories/projects/${projectKey}/members/${userId}`),
  getProjectPermissions: (projectKey: string) => request.get(`/repositories/projects/${projectKey}/permissions`),
  setProjectPermissions: (projectKey: string, data: Record<string, any>) => request.put(`/repositories/projects/${projectKey}/permissions`, data),
  deleteProject: (projectKey: string) => request.delete(`/repositories/projects/${projectKey}`),
}

// 异常注入服务
export const injectionApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; status?: number; type?: string }) =>
    request.get('/injections', { params }),
  create: (data: { type: string; target: string; config?: string }) =>
    request.post('/injections', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/injections/${id}`, data),
  delete: (id: number) => request.delete(`/injections/${id}`),
  getById: (id: number) => request.get(`/injections/${id}`),
  execute: (id: number) => request.post(`/injections/${id}/execute`),
}

// 通信协议测试服务
export const protocolTestApi = {
  getList: (params?: { page?: number; page_size?: number; keyword?: string; result?: string }) =>
    request.get('/protocol-tests', { params }),
  create: (data: { target: string; address?: string; data?: string }) =>
    request.post('/protocol-tests', data),
  update: (id: number, data: Record<string, any>) =>
    request.put(`/protocol-tests/${id}`, data),
  delete: (id: number) => request.delete(`/protocol-tests/${id}`),
  getById: (id: number) => request.get(`/protocol-tests/${id}`),
  connect: (data: { target: string; protocol: string; config?: Record<string, any> }) =>
    request.post('/protocol-tests/connect', data),
  disconnect: (sessionId: number) =>
    request.post(`/protocol-tests/${sessionId}/disconnect`),
  send: (sessionId: number, data: { frame_id?: string; dlc?: number; data?: string }) =>
    request.post(`/protocol-tests/${sessionId}/send`, data),
  getLogs: (sessionId: number, params?: { page?: number; page_size?: number }) =>
    request.get(`/protocol-tests/${sessionId}/logs`, { params }),
  clearLogs: (sessionId: number) =>
    request.post(`/protocol-tests/${sessionId}/logs/clear`),
  getRecords: (params?: { page?: number; page_size?: number; keyword?: string; protocol?: string; executor?: string }) =>
    request.get('/protocol-tests/records', { params }),
  getRecordDetail: (recordId: number) =>
    request.get(`/protocol-tests/records/${recordId}`),
  deleteRecord: (recordId: number) =>
    request.delete(`/protocol-tests/records/${recordId}`),
  getReportHtml: (recordId: number, print = false) =>
    request.get(`/protocol-tests/${recordId}/report/html`, { params: { print: print ? 1 : 0 }, responseType: 'blob' as any }),
  getReportCsv: (recordId: number) =>
    request.get(`/protocol-tests/${recordId}/report/csv`, { responseType: 'blob' as any }),
}

// 工作台服务
export const dashboardApi = {
  getStats: () => request.get('/dashboard/stats'),
}
