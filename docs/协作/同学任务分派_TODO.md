# 任务 TODO

谁有空谁领，commit message 里带上任务 ID 说一声就行，比如 `T4: 删节点清题库 node_id 孤儿`。不用报备，也不用回填表格。做完把验收命令的输出贴一下。

## 开工前

- 现在有 36 个文件改了还没提交（P0/P1 的修复）。在同一台机器上接着做就不用管，但别覆盖；要从 GitHub clone 的话先让 lead 提交，否则拿到的是旧代码。
- 先读 `AGENTS.md`，命令和硬约束都在里面。
- 后端用 `backend/venv/Scripts/python.exe`。本机沙箱里 pytest 直连会因 `PermissionError: [WinError 5]` 全量报错，用兜底脚本（内容贴在文末）；换普通开发机就没这问题。
- 提交按文件族路径限定 `git add`，不 `git add -A`，不主动 push。

## 任务

### 文档 / 测试 / 工程

- [ ] **T1** 刷新参考文档：两份架构文档里的基线数字还停在 10-10（1654 / 1549 / 41 passed），改成现在的值或标成历史快照；`重构实施计划_可执行清单.md §1.4` 里废弃的 `.dsh_local/run_pytest.py` 换成 `pytestrun/run.py`；`AGENTS.md §6` 路由表补一句「下面几份文档只在本机、clone 没有」。
  改 `docs/架构/` 那两份 + `AGENTS.md` 路由段。不动 `.gitignore`、不动代码。
- [ ] **T2** `backend/tests/test_prompt_loader.py:45-57` 里断言了 `"资深教师"` / `"全局底线"` / `"教学方式"` 这些具体文案，提示词改个词就误红。改成结构性断言（值被注入、没有残留 `{{`）。`:60` 之后的注入守卫要留着，那是单花括号 bug 的回归线。
  只改这一个文件。验收：单独跑绿；把模板某个 `{{ }}` 改回 `{ }`，守卫要红。
- [ ] **T3** 错误码双源：后端 `core/error_codes.py` 与前端 `frontend/src/utils/errorCodes.js` 可能漂移。新建 `backend/tests/test_error_codes_parity.py`，比对两边码集合（前端多留的写显式白名单）。
  验收：单跑绿；临时在后端加个假码，测试要红。依据 `重构实施计划_可执行清单.md §6` 决策 ⑦。
- [ ] **T4** 删图谱节点时题库还有孤儿：`core/quiz/quiz_store.py:303` 的 `delete_by_knowledge_points` 只按 `questions.knowledge_point` 删，`node_id` 列（`:47` 建表、`:349` 读出）没管到。扩展 WHERE 覆盖 `node_id`，空入参仍是 no-op、库不存在仍返回 0。新建 `backend/tests/test_quiz_node_id_cleanup.py`，先写会红的测试再改。
  测试全在 `tmp_path` 造库，别碰真实 `data/`。
- [ ] **T5** 清低风险 lint 债：全量 `ruff check backend/app --select F,E9` 还有 29 条 F401 + 2 条 F541（`core/kg/paths.py:221`、`core/quiz/exporter.py:224`）。删掉没用的 import / 多余的 `f`，把这两类加进根 `ruff.toml` 的 `select`。
  46 条 F405（`import *`）不在这条里，别动。
- [ ] **T6** `frontend-admin/` 现在 0 个测试。沿用 `frontend/` 同款 vitest 5 + jsdom，不引新框架，给 `src/router.js:30-39` 的守卫写 3 例（无 token 跳登录、有 token 访问登录页跳首页、有 token 放行），再给 `Layout.vue` / `Admins.vue` 的 JSON.parse 护栏补一条。
  改 `frontend-admin/**`，不碰 `frontend/` 和后端。

### Android（3 人，各领一组）

`Android/**` 是独立目录，跟上面几条不冲突，可以同时开工。开工前三人先把接口和 DTO 定一下，A 顺手写个内存假实现，B/C 就能直接写页面，不用等。包名、`baseUrl` 放法、依赖范围这几件事三人一次定完，之后谁都别单独改（`Android/README.md §6`）。

- [ ] **W0** 共同：定 `AuthApi` / `ChatApi` / `KbApi` / `SseEvent` 的签名和 DTO；`baseUrl` 放 `buildConfigField` 或 `local.properties`（每人 LAN IP 不同，别硬编码、别入库）；依赖只用已有的 OkHttp 4.12 + Gson 2.11 + Compose BOM，不引 Retrofit / Room / Hilt / Coil。
- [ ] **A · 引擎**：OkHttp 单例 + Bearer 拦截器（401 重登、429 读 `Retry-After`）、公钥缓存 + RSA-OAEP 加密、JWT 安全存储、登录页、SSE 帧解析器（按 `\n\n` 切帧、跳过 `:` 注释行、`token` 字段、`final` 兜底、`[DONE]`、可取消）及其 JVM 单测，最后换掉假实现跑通里程碑一。
  写 `data/remote/`、`data/auth/`、`ui/feature/login/`、单测。
- [ ] **B · 对话 + 学习**：会话列表/新建/改名/删除（墓碑）、逐 token 气泡、发送中可取消、thinking 与工具卡片、把选中的 `kb_node_ids` 带进 `/chat/stream`；知识点列表 + 详情 + 下一个该学、出题判分闭环。
  写 `ui/feature/chat/`、`ui/feature/learn/`、`data/repo/chat/`、`data/repo/learn/`。第一版不做力导向图谱画布。
- [ ] **C · 壳 + 知识库 + 我的**：4 个底部 tab 和主题接线、目录树、上传（别设 120s 超时）、正文/原件预览（原件走 OkHttp 取字节，别直接塞 URL，带不了 JWT）、把选中范围给 B、我的页（账号/画像/模型/用量）、常驻事件长连接（JWT 在 query）+ 断线补拉。
  写 `ui/shell/`、`ui/feature/library/`、`ui/feature/me/`、`data/repo/kb/`、`data/repo/me/`。请求内事件别走这条长连接，会抢走对话流。

谁先做完、谁卡住了，三个人自己换一下手上的条目就行，commit 里写清楚。

## 参考

- `AGENTS.md`：命令、硬约束、文档路由。
- `docs/架构/重构实施计划_可执行清单.md`：任务 ID、做法、验收、§6 需要拍板的点。
- `docs/架构/整体架构_问题清单与重构方案.md`：问题清单与进展。
- `Android/README.md`：tab 骨架、代码分层、里程碑一四步、哪些事不要自己拍板；`AGENTS.md §5`：工具链和 6 条坑。
- 后端契约以代码为准：`backend/app/api/v1/*.py` 的 docstring 和 `backend/app/models/schemas.py`。前端 REST/SSE 的参考实现是 `frontend/src/api/index.js`。

几份文档被 `.gitignore` 忽略，本地有、clone 没有，别当成自己漏了：`.codebuddy/memory/MEMORY.md`、`docs/项目历程_决策与效果记录.md`、`docs/知识沉淀_跨会话决策归档.md`、`docs/架构/演示口径与操作卡.md`、`docs/归档/`、`docs/教育资料采集/`、`docs/运维部署/Docker_学习路径与工程化部署.md`。

⚠️ `AGENTS.md §5.3.5` 的 RSA 方向本轮已从 `DECRYPT_MODE` 改成 `ENCRYPT_MODE`，但没提交。从 GitHub clone 会拿到错的那句，照抄登录必失败 `400 [E-AUTH-007]`。A 做登录前确认一下自己手上是改过的版本。

## 验收基线（别跑低）

后端 `1946 collected / 7 deselected` = `1945 passed + 1 skipped`；后台 `43 passed`；前端 `26 passed (3 files)`；`ruff check backend/app backend/tests backend-admin/app` 全绿。`1 skipped` 和 7 条 `llm_api` 是有意留的，别去修。

兜底脚本 `.dsh_local/pytestrun/run.py`（`.gitignore` 忽略，新 clone 要自建）：

```python
import pathlib, sys
_orig = pathlib.Path.mkdir
def _mkdir(self, mode=0o777, parents=False, exist_ok=False):
    return _orig(self, 0o777, parents, exist_ok)
pathlib.Path.mkdir = _mkdir
import pytest
sys.exit(pytest.main(sys.argv[1:]))
```

```
backend/venv/Scripts/python.exe .dsh_local/pytestrun/run.py backend/tests -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp_<任务ID>
backend/venv/Scripts/python.exe .dsh_local/pytestrun/run.py backend-admin/tests -q -m "not llm_api" -p no:cacheprovider --basetemp=.dsh_local/pytestrun/tmp_<任务ID>_admin
```

两个套件必须分开跑，包名都叫 `app`，合跑会 `ModuleNotFoundError`。Android 用 `.\gradlew.bat :app:assembleDebug` 和 `:app:testDebugUnitTest`；沙箱下 gradle 要写 `~/.gradle`，切「完全权限」或把 `GRADLE_USER_HOME` 指到工作区内。

## 这些先别动

安全默认姿态（后台 `admin123` / 无 TLS / JWT 吊销 / prompt 注入）、拆 `chat_service.py`、拆前端 store、B3-3 主动推题、放行上面那些被忽略的文档、46 条 F405、`vue-tsc`（项目 0 个 `.ts` 文件）——都要么改生产行为、要么得先拍板。清单在 `docs/运维部署/运维_线上实例安全评估报告_2026-10-19.md` 和架构文档 §6。

A5「零引用模块接入口」的前提也变了：`pipeline_ingest` 已被 `kb_manager.py`、`chapterizer` 已被 `graph_generator.py` 引用，只剩 `quiz_splitter` 还是零引用，动手前重新核一下。
