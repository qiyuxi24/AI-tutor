/**
 * 传输加密工具：登录/注册密码的 RSA-OAEP-SHA256 加密。
 *
 * 用 node-forge 而非 Web Crypto（crypto.subtle）：后者仅安全上下文
 * （HTTPS/localhost）可用，当前部署地址 http://<内网IP>:8080 下为 undefined。
 *
 * 密文格式：base64(RSA-OAEP-SHA256(密码))，与后端 core/transport_crypto.py 对应。
 */
import forge from 'node-forge'
import { apiClient } from '../api/index.js'

// 模块级公钥缓存；服务端密钥轮换后由调用方带 refresh 强制刷新
let cachedPem = null

/**
 * 用指定 PEM 公钥加密密码（纯函数，无网络）
 * @param {string} publicKeyPem PEM 格式公钥（来自 GET /api/v1/auth/public-key）
 * @param {string} password 明文密码
 * @returns {Promise<string>} base64 密文
 */
export async function encryptWithPem(publicKeyPem, password) {
  const key = forge.pki.publicKeyFromPem(publicKeyPem)
  // forge 按 latin1 处理字符串：先显式转 UTF-8 字节串，否则非 ASCII 密码会变错误字节
  const data = forge.util.encodeUtf8(password)
  const md = forge.md.sha256.create()
  const encrypted = key.encrypt(data, 'RSA-OAEP', {
    md,
    mgf1: { md: forge.md.sha256.create() },
  })
  return forge.util.encode64(encrypted)
}

/**
 * 获取后端 RSA 公钥（带缓存）
 * @param {boolean} refresh true 时强制重新拉取（密钥失配重试场景）
 */
export async function fetchPublicKey(refresh = false) {
  if (!refresh && cachedPem) return cachedPem
  const { data } = await apiClient.get('/api/v1/auth/public-key')
  cachedPem = data.public_key
  return cachedPem
}

/**
 * 加密密码（自动取公钥）
 * @param {string} password 明文密码
 * @param {{refresh?: boolean}} options refresh=true 强制刷新公钥缓存
 */
export async function encryptPassword(password, { refresh = false } = {}) {
  const pem = await fetchPublicKey(refresh)
  return encryptWithPem(pem, password)
}
