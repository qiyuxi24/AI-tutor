# AI-Tutor 改进清单

## P0 - 安全/紧急

- [x] **轮换泄露的 API Key** — `.gitignore` 已加固，`.env` 未被追踪，用户待自行轮换密钥
- [x] **默认管理员密码从环境变量读取** — `main.py` 改为从 `DEFAULT_ADMIN_PASSWORD` 环境变量读取，日志不再输出明文密码

## P1 - 功能缺口

- [x] **图谱搜索功能** — 新增 `GraphSearch.vue`，支持即时搜索、键盘导航、高亮匹配、聚焦节点
- [x] **对话知识点可跳转到图谱** — AI 回复中自动识别节点名并渲染为可点击链接，点击后切到图谱+聚焦；NodeDetail 前置知识/相关节点也可点击跳转
- [x] **REST API 统一权限保护** — `PUT /knowledge/node/{node_id}` 已修复，内容更新统一走 `kg.update_node_content()` 权限检查

## P2 - 用户体验闭环

- [ ] **学习进度仪表盘** — 展示已掌握/学习中/未开始的节点数量、学习时长等
- [x] **学习路径推荐** — 基于拓扑排序的学习路径推荐模块（问题拆解→骨架图谱→递归教学）
- [ ] **AI 建议审核面板** — 前端展示 `ai_suggestions.json` 中的待审核 add_node/add_edge 建议
- [x] **用户画像引导** — 新增 `OnboardingGuide.vue`，3 张卡片介绍核心功能，首次使用自动弹出
- [x] **三种模式精简为两种** — 合并 scaffolding/think_first/reverse_teaching → adaptive（自适应引导）+ free_talk（自由对话），减少用户决策负担，教学更自然

## P3 - 质量/体验打磨

- [ ] **LLM 请求重试机制** — 对网络抖动等瞬时错误增加 1-2 次重试
- [ ] **前端 toast 替换 alert** — HomeView、ForceGraph 等组件中的 `alert()` 改为 toast 通知
- [ ] **图谱重复节点处理** — `create_node_from_ai` 节点已存在时改为 `update_node_content` 追加内容
- [ ] **用户画像缓存** — `_build_system_prompt` 每次创建 UserProfile 实例，应缓存
- [ ] **CORS 配置化** — `allow_origins` 硬编码 `localhost:5173`，从环境变量读取
- [ ] **添加单元测试** — 至少为核心模块（knowledge_graph、user_profile、llm_client）添加测试
- [ ] **requirements.txt 锁定版本** — 固定依赖版本号

---

## 已完成

- [x] 创建改进清单
