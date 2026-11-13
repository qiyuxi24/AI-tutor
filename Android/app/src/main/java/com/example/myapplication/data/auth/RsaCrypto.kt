package com.example.myapplication.data.auth

import android.util.Base64
import java.security.KeyFactory
import java.security.PublicKey
import java.security.spec.X509EncodedKeySpec
import javax.crypto.Cipher
import javax.crypto.spec.OAEPParameterSpec
import javax.crypto.spec.PSource
import java.security.spec.MGF1ParameterSpec

/**
 * 登录密码的 RSA-OAEP 加密。
 *
 * ## 这个文件为什么值得单独存在
 *
 * 后端 `backend/app/core/transport_crypto.py` 用的填充是
 * **`OAEP(mgf=MGF1(SHA256), algorithm=SHA256)`**。
 *
 * Android 侧如果只写 `Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding")`，
 * 得到的是 **algorithm=SHA256 但 mgf=MGF1(SHA1)** —— 这是 JCA 的历史默认值。
 * 后端用 SHA256 去验 MGF1，必然解密失败，并返回 400 `[E-AUTH-007]`。
 * 那个报错信息只看得出"解密失败"，看不出是 MGF1 的摘要算法不对，所以必须
 * **显式传 [OAEPParameterSpec]**，不能指望算法名里那串字。
 *
 * 方向也容易搞反：客户端持有**公钥**，做的是**加密**（`ENCRYPT_MODE`）；
 * 服务端才解密。写成 `DECRYPT_MODE` 会抛
 * `InvalidKeyException: No installed provider supports this key`。
 */
object RsaCrypto {

    private const val TRANSFORMATION = "RSA/ECB/OAEPWithSHA-256AndMGF1Padding"

    /**
     * 把明文密码加密成后端可解的形式。
     *
     * @param publicKeyPem `GET /api/v1/auth/public-key` 返回的 PEM 文本
     *   （含 `-----BEGIN PUBLIC KEY-----` 头尾）。
     * @return **Base64 后的密文**——后端要求的就是 base64 字符串，不是原始字节。
     */
    fun encryptPassword(plainPassword: String, publicKeyPem: String): String {
        val key = parsePublicKey(publicKeyPem)
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(
                Cipher.ENCRYPT_MODE,
                key,
                // ⚠️ 三个参数都不能省：algorithm / MGF1 的摘要 / 无 label。
                // 只写算法名会落到 MGF1-SHA1，后端必然解不开。
                OAEPParameterSpec(
                    "SHA-256",
                    "MGF1",
                    MGF1ParameterSpec.SHA256,
                    PSource.PSpecified.DEFAULT,
                ),
            )
        }
        val cipherBytes = cipher.doFinal(plainPassword.toByteArray(Charsets.UTF_8))
        // NO_WRAP：不能带换行，否则后端 base64 解码会失败
        return Base64.encodeToString(cipherBytes, Base64.NO_WRAP)
    }

    /**
     * 解析 PEM 公钥。
     *
     * 用 `Base64.decode` 而不是手工去头尾再解，是因为服务端 PEM 里带 `\n`，
     * 手工处理容易漏掉行尾换行。`MIME` 模式会忽略换行符。
     */
    private fun parsePublicKey(pem: String): PublicKey {
        val base64 = pem
            .replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("\\n", "")
            .filterNot { it.isWhitespace() }
        val der = Base64.decode(base64, Base64.DEFAULT)
        return KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(der))
    }
}
