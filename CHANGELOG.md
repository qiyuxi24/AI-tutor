# 更新日志

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
[语义化版本](https://semver.org/lang/zh-CN/)（`MAJOR.MINOR.PATCH`）。

**版本真源只有两处**：`backend/app/__init__.py::__version__`（`main.py` 的
`FastAPI(version=…)` 读它）与 `frontend/package.json`。
发版流程 = 改这两处 → 在本文件顶部加一节 → `git tag -a vX.Y.Z`。

---

## [1.2.0] - 2026-10-26

前端界面与部署。

### 变更

- **界面改造**：新手引导重做；学习路径板与图谱交互（`PathBoard` / `ForceGraph` /
  `GraphSearch` / `GraphSubjectBar`）；对话区与输入区、消息气泡；知识库面板与节点
  详情、设置面板。图标统一为描边 SVG（不再用 emoji 当图标），字号/间距改用设计 token。
- **`deploy.ps1`**：目标服务器改为**必须显式指定**（`-Server user@host`），不再默认
  推往某台具体机器。
- **`vite.config.js`**：代理目标支持 `VITE_API_TARGET` 覆盖（8000 被占用时用），
  默认行为不变。

### 修复

- **反向代理后的限流边界**：容器里 uvicorn 只信任 `127.0.0.1`，而 nginx 的上游 peer
  是 docker 网桥地址 → 应用层拿到的 `client_ip` 恒为网桥地址，**所有访客共用一个
  限流桶**（一个人就能把全站挡在门外）。compose 补 `FORWARDED_ALLOW_IPS`
  （默认 `127.0.0.1,172.16.0.0/12`）。

验证：`npm run build` 通过（10.55s，仅 chunk 体积/动态导入告警）；前端无自动化测试，
交互为手工核对；本版本未改后端，`backend/tests` 不受影响。

---

## [1.1.0] - 2026-10-26

后端行为增量：检索/删除正确性、并发边界、试卷导出、运维后台安全。

### 新增

- **试卷导出四种格式**：docx / pdf / md / tsv（Anki 卡片），`format` 走白名单校验；
  前端出题页与卡片有导出入口。测试要求"媒体类型正确 + 字节流非空"并覆盖非法取值。
- **`llm/fallback.user_candidates(user_id)`**：把「用户自配模型 → 系统默认档」的
  候选链拼装收敛成一处，转写等非对话场景复用同一套主→备降级语义。

### 变更

- **删除即清索引，收口到 kg 层**：`remove_node` / `remove_subject` 内部调
  `_purge_rag_index_for_nodes`，API 层不再手写清理。原先 Agent `delete_node` 与
  「采纳 AI 建议」直删会留下索引 → 已删知识点仍被按需检索注入。
- **同步调用点改走 `core/runtime` 桥**：`rag_search`（顺带删掉自己的线程池与临时
  loop，−20 行）、`section_generator`、`chat_quiz`、`kg_taxonomy`、`rag_pipeline/sources`；
  `chat_service` 的后台任务改 `runtime.spawn`（持强引用防 GC），并在客户端断连时
  取消正在跑的 agent 任务（新增回归 `test_learn_stream_path`）。

### 修复

- **运维后台拒绝弱启动**：`ADMIN_SECRET_KEY` 为空或仍是源码默认值时 **拒绝启动**
  —— 原先忘配等于把后台公开；用量页统计口径与后端对齐。
- `test_agent_loop_real_api`：证据断言不再绑定"最后一条"，收尾追加 `loop_stop`
  不会误报（改为断言"存在且非空"）。

验证：`backend/tests` 1775 passed；`backend-admin/tests` 43 passed（两个套件分开跑）。

---

## [1.0.1] - 2026-10-26

### 变更

- **文档归档整理**：阶段性调研稿收进 `docs/归档/`，删除已被正式方案取代的 17 篇；
  根目录 4 个 `TODO_*.md` 并入 `TODO.md`；两篇《知识图谱加载与注入_*》并入
  `docs/知识图谱/知识图谱_检索与注入_方法与现状.md`。
- 代码与测试中 20 余处 `docs/...` / `TODO_*.md` 引用同步到新路径 —— **纯注释与
  文档字符串，无逻辑改动**（这类引用改漏不报错，只会让后来者按图索骥扑空）。

无代码行为变更。

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
