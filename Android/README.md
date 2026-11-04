# AI Tutor · Android 客户端

原生 Kotlin + Compose（不是 WebView 套壳）。当前是**可构建的骨架**：能编出 APK、能装、界面只有一个 Hello World，业务代码一行都还没写。

这份文件是**给人看的**（队友从这里开始）。给 AI agent 看的操作手册在根目录 [`AGENTS.md`](../AGENTS.md) §5，两者视角不同，别互相复制。

---

## 0. 五分钟跑起来

**前置**：JDK 21 + Android SDK（本机已装，见下方"本机环境"）。不需要另装 Gradle，模板自带 wrapper。

```powershell
cd Android

# 1) 编出 APK（第一次约 1.5–6 分钟，之后几十秒）
.\gradlew.bat :app:assembleDebug
#    产物：app/build/outputs/apk/debug/app-debug.apk

# 2) 装到已连接的设备/模拟器
.\gradlew.bat :app:installDebug

# 3) 联调后端（关键一步，别跳过）
#    先起后端：在仓库根目录跑 .\start.ps1
#    然后把手机的 8000 端口映射到 PC 的 8000
adb reverse tcp:8000 tcp:8000
```

App 里的 `baseUrl` 用 `http://127.0.0.1:8000`。

> 为什么是 `adb reverse` 而不是填电脑局域网 IP：后端默认只监听 `127.0.0.1`（`start.ps1` 里写死的），手机用局域网 IP 连不上；`adb reverse` 会让手机上的 127.0.0.1:8000 转发到 PC，不用改后端也不用查 IP。走这条路时 `network_security_config.xml` 已经放行了 `127.0.0.1`，不用额外配置。

**本机环境（已确认可用，别自己重装）**

| 项 | 值 |
|---|---|
| JDK | `D:\program files\Android stdio\jbr`（openjdk 21.0.10） |
| 编译 SDK | `android-36.1`（本机只有 `android-35` 与 `android-36.1`，**没有 36**，所以 `compileSdk` 里那行 `minorApiLevel = 1` 不能删） |
| Gradle wrapper | 9.4.1（自动下载） |
| 构建验证 | 2026-11-04：`BUILD SUCCESSFUL in 1m 29s`，36 tasks，APK 12.4 MB |

---

## 1. 技术栈与硬约束

`AGP 9.2.1` / `Kotlin 2.2.10` / `Compose BOM 2026.02.01` / `minSdk 24` / `targetSdk 36` / `JDK 21`。

网络层：**OkHttp 4.12.0 + Gson 2.11.0**。不引 Retrofit —— 对话流和常驻事件都是 SSE，本来就要裸 OkHttp 流式读，再套一层 Retrofit 只会让两个 HTTP 抽象互相翻译。REST 和 SSE 共用一个 `OkHttpClient`。

**四条容易踩的（写代码前先看，这几条不写就会出错）**

1. **依赖只能加到 `gradle/libs.versions.toml`**，再在 `app/build.gradle.kts` 引用。`settings.gradle.kts` 里设了 `FAIL_ON_PROJECT_REPOS`，在模块里写 `repositories { }` 会直接构建失败。
2. **`settings.gradle.kts` 的 plugins 块只有 `android-application` + `kotlin-compose`**。AGP 9 内置 Kotlin 插件，**不要**再加 `org.jetbrains.kotlin.android`，加了会报插件冲突。
3. **AGP 9 是新 DSL**：`compileSdk { version = release(36) { minorApiLevel = 1 } }`（不是 `compileSdk = 36`）、`buildTypes.release { optimization { enable = false } }`（不是 `isMinifyEnabled`）。
4. **`INTERNET` 权限**：模板原本一条 `<uses-permission>` 都没有，缺了它所有网络请求抛 `SecurityException`（长得像"网络错误"，极难查）。已补，别删。

---

## 2. 后端契约（只写客户端需要知道的）

后端是唯一真值 —— 有疑问直接读 `backend/app/api/v1/*.py` 的 docstring 和 Pydantic model，别信任何文档的转述。

| 用途 | 端点 | 关键点 |
|---|---|---|
| 取公钥 | `GET /api/v1/auth/public-key` | `{"public_key": "<PEM>"}`。进程级缓存一次即可，别每次登录都拉 |
| 登录 / 注册 | `POST /api/v1/auth/login`、`/auth/register` | body `{"username","password"}`，**password 必须是 RSA-OAEP 加密后的 base64**，不是明文。明文长度 6–50。登录回 `{"token","user":{"id","username","role"}}` |
| 对话（流式） | `POST /api/v1/chat/stream` | body `{messages:[{role,content}], current_node?, kb_node_ids?, kb_node_name?, conv_id?}`，返回 `text/event-stream` |
| 常驻事件 | `GET /api/v1/knowledge/events?token=<JWT>` | JWT 放在 **query** 里（EventSource 带不了 header）。断线重连后拉 `GET /api/v1/knowledge/notices/pending` 补离线通知 |

**三条必须照做的**

- **RSA 加密要显式指定 OAEP 参数**。后端用 `OAEP(mgf=MGF1(SHA256), algorithm=SHA256)`；Android 侧必须：

  ```kotlin
  val cipher = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding")
  cipher.init(Cipher.ENCRYPT_MODE, publicKey, OAEPParameterSpec(
      "SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT
  ))
  ```

  只写 `"RSA/ECB/OAEPWithSHA-256AndMGF1Padding"` 会用到系统默认的 **MGF1-SHA1** → 后端必然解密失败，返回 400 `[E-AUTH-007]`，而这个报错完全看不出是 MGF1 的问题。**这是最容易卡一整天的一条。**

- **SSE 要忽略注释帧**。后端每 15 秒发一行 `: keepalive`（冒号开头），防止 nginx 300 秒空闲超时掐断连接。解析时**以 `:` 开头的行整行跳过**，否则 JSON 解析直接崩。另外注意正文增量的字段名是 **`token`** 不是 `text`。

- **SSE 事件白名单只有 5 类**：`text_delta`（→ `{"token": …}`）、`thinking`、`tool_start`、`tool_result`、`agent_start`、`agent_done`。服务端对未知类型**静默丢弃** —— 所以"等不到某个事件"不是解析写错了，是后端不发。

其余：限流是登录 5 次/60 秒/IP、对话 20 次/60 秒/IP，超限返回 **429 + `Retry-After`**（秒），要照着做退避。错误响应统一 `{"detail": "[E-XXX] …"}`，`401` = token 失效。

参考实现：前端 `frontend/src/api/index.js`（REST + SSE 的实际收发、`Authorization` 头、`AbortController` 取消、错误分流）、`stores/authStore.js` + `utils/crypto.js`（登录链路与公钥缓存）。**抄"请求怎么发、错误怎么处理"，不要抄 UI** —— 那是桌面布局，390px 下必然重叠。

---

## 3. 界面骨架（建议，可讨论）

四个底部 tab，和主流 AI 知识库产品保持一致的肌肉记忆：

| tab | 内容 | 第一版范围 |
|---|---|---|
| **对话** | 聊天流 + 输入框；支持引用已有知识库文件 | 核心，优先级最高 |
| **知识库** | 文件列表 / 上传 / 拍照上传 / 解析状态 | 核心 |
| **学习** | 知识点列表（**第一版不做力导向图**，做列表 + 详情）、掌握度、出题入口 | 核心 |
| **我的** | 账号、设置、模型配置 | 简单 |

**图谱第一版不做力导向 SVG 画布**：手机上 300+ 节点拖拽体验很差，且这块工作量最大。先用列表 + 详情页把「有哪些知识点、我掌握到什么程度」讲清楚，图谱可视化留到第二版（后端数据都是现成的）。

---

## 4. 代码放哪

当前只有 `MainActivity.kt` 一个文件，新代码按这个分层落：

```
app/src/main/java/<your/package>/
├── MainActivity.kt          # 只有 setContent
├── data/                    # OkHttp client、DTO（Gson）、Repository、SSE 事件解析
├── domain/                  # 用例（可以薄，别为了分层而分层）
└── ui/
    ├── theme/              # 已有 Material3 动态取色，直接用 MyApplicationTheme
    └── feature/
        ├── chat/           # 对话 tab
        ├── library/        # 知识库 tab
        ├── learn/          # 学习 tab
        └── me/             # 我的
```

---

## 5. 第一个里程碑（建议两周内）

**跑通一条完整链路**：登录 → 发一句话 → 看到流式回复一个字一个字冒出来。

拆成可验收的四步：

1. **网络层**：`OkHttpClient` + Gson DTO + 错误分流（把 `[E-XXX]` 解析出来），有一个能手动调 `GET /auth/public-key` 的入口。
2. **登录**：RSA 加密 → `POST /auth/login` → 拿到 JWT → 用 `EncryptedSharedPreferences` 或 `DataStore` 存起来（**别存明文 SharedPreferences**）。这一步过了就说明 OAEP 参数没写错。
3. **SSE 客户端**：裸 OkHttp 读 `text/event-stream`，按 `\n\n` 切帧、跳过 `:` 注释行、解析 `data:` 里的 JSON。**先用一个假的本地 SSE 端点或 curl 试通，再接真后端** —— 这一步的坑最多（分帧、粘包、取消、重连）。
4. **对话页**：一个 `LazyColumn` 渲染消息 + 一个输入框，接上第 3 步的流。

四步都过了，再往下铺知识库和学习 tab —— 这时候网络层和 SSE 已经稳定，后面是纯 UI 工作量。

---

## 6. 哪些事不要自己拍板

这些是产品/契约层面的决定，问了再动，返工成本很高：

- **改包名 / 应用名** —— 要同步 6 处（`namespace`、`applicationId`、目录结构、所有 `import`、`themes.xml` 的 `Theme.MyApplication`、`ExampleInstrumentedTest.kt` 里的包名断言）。约定好一次改完。
- **加新依赖** —— 尤其"要不要引 Retrofit / Room / Hilt / Coil"。加之前说一声，避免两套 HTTP 抽象或两套 DI 并存。
- **改后端接口** —— 后端在另一条线上开发，改契约要两边对齐。
- **iOS / 平板 / 桌面端** —— 当前只在 Android 上做，别的端不在这一版范围。

## 7. 提交前自查

```powershell
.\gradlew.bat :app:assembleDebug      # 能编过
.\gradlew.bat :app:testDebugUnitTest  # JVM 单测（有就写，没有不强制）
```

- 提交按文件族拆，**不要 `git add -A`** —— 这个仓库常有多个会话/多个人同时改。
- `local.properties`（含本机 SDK 路径）、`build/`、`.gradle/` 已在 `.gitignore` 里，别提交。
- 别把 keystore / 签名密码提交进仓库。
