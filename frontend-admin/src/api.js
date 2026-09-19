// 统一的 API 客户端：自动带 token，401 时退回登录页
import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1/admin' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    // 登录接口本身的 401 交给页面提示，其余 401 一律视为登录态失效
    const isLogin = err.config?.url?.endsWith('/login')
    if (err.response?.status === 401 && !isLogin) {
      localStorage.removeItem('admin_token')
      localStorage.removeItem('admin_info')
      window.location.replace('/login')
    }
    return Promise.reject(err)
  }
)

/** 从接口错误里取可展示的提示文案（兼容 FastAPI 的 pydantic 校验错误数组） */
export const errorMessage = (err, fallback = '操作失败') => {
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  return fallback
}

export default api
