# 更新日志

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)（`MAJOR.MINOR.PATCH`）。

**版本真源只有两处**：`backend/app/__init__.py::__version__`（`main.py` 的
`FastAPI(version=…)` 读它）与 `frontend/package.json`。
发版流程 = 改这两处 → 在本文件顶部加一节 → `git tag -a vX.Y.Z`。

---

## [1.0.0] - 2026-10-26

首个把「外部资料 → 讲义 → 知识库 → RAG」闭环打通的版本；同时收口了 SSRF 防护
与同步/异步边界两个长期隐患。

### 新增

- **视频课程导入**：粘贴 B 站（或 yt-dlp 支持的站点）链接 → 生成讲义 → 直接入
  知识库并参与 RAG（`POST /kb/import/lecture`，后台长任务 + 进度事件）。
  取词三条路，**按成本排序**：
  1. B 站平台字幕直取（字幕轨 + AI 总结两条来源，只发 HTTP，零 API 成本、不需要 ffmpeg）
  2. yt-dlp 字幕文件（其余站点 / B 站平台字幕缺失时的降级）
  3. 语音转写（yt-dlp 下音频 → ffmpeg 抽音轨并按 2 分钟切段 → `llm/transcribe.py`）
- **B 站扫码登录**：`GET /kb/bili/login/{qrcode,poll}` + 导入页二维码弹窗；凭证落
  `data/bili/cookies.txt`（Netscape 格式，已 `.gitignore`），**只留服务端**，
  yt-dlp 与平台字幕接口共用同一份；删除该文件 = 退出登录。
- **依赖门禁按路径判定**（`video_import.missing_requirements`）：只查「这条链接 +
  这个模式」真正要的东西——B 站字幕直取零依赖可跑。
- 前端：视频导入页（依赖自检 / 阶段进度 / 讲义预览）、`import_progress` 与
  `kb_updated` 两类事件。

### 变更

- **SSRF 防护上移** 至 `core/net_guard.py`（原 `agent_tools/net_guard.py`）：
  `is_blocked_url` 被工具 / 采集适配器 / KB 导入 / `/llm/models` 共用，放在工具包里
  造成包级环 → 5 处被迫函数内惰性 import。上移后环消失，其中 2 处恢复模块级
  import；新增回归锁住「单一实现」，旧位置不得再出现。
- **同步→异步桥** `core/runtime.py`：协程经 `run_coroutine_threadsafe` 回主事件
  循环，避免工作线程里新建临时 loop 复用 AsyncOpenAI 单例 / whoosh writer 造成的
  **静默降级**（不报错但功能残缺）。

### 修复

- `aisubtitle.hdslb.com` 对连发请求返回 403 时，不再把整次导入炸掉：退让后重试
  一次，两次失败只降级给语音转写。
- 「没拿到字幕」的原因现在可分辨并会回显：`-101 账号未登录` / 该视频没有字幕轨 /
  WBI 密钥缺失 —— 三者排查方向完全不同，此前被压成同一句话。

### 已知限制

- **ASR 分支未在真机验证**（本机缺 ffmpeg 与 `ASR_MODEL`，只有离线单测）。
- 讲义入库是**文件级**：知识库能看、能检索，但不会自动拆成图谱知识点
  （需另行触发资料分析）。
- 「从浏览器读 cookie」的路子已弃用：Edge/Chrome 127+ 使用 App-Bound 加密，
  外部程序必然解不开（实测 Edge 156 → `Failed to decrypt with DPAPI`）。

---

## [0.9-competition] - 2026-10-10

比赛提交版（tag `v0.9-competition`，提交 `8e88600`）。此版本未单独维护条目，
改动见该 tag 之前的提交历史。
