/**
 * 跨语言往返验证（配合 backend/scripts/smoke_transport_crypto.py）：
 * 读取 PEM 公钥文件 → 用前端同款加密逻辑加密密码 → 输出 base64 密文到 stdout
 *
 * 用法: node scripts/verify-crypto.mjs <pem文件路径> <明文密码>
 */
import { readFileSync } from 'node:fs'
import { encryptWithPem } from '../src/utils/crypto.js'

const [pemPath, password] = process.argv.slice(2)
const pem = readFileSync(pemPath, 'utf-8')
const cipher = await encryptWithPem(pem, password)
process.stdout.write(cipher)
