<!-- base: README.md @ c54d571 (2026-09-12). English mirror of the Chinese README — keep both in sync; spec: docs/工程实践/README_编写规范.md §6. -->

# TutorAgent — A Knowledge-Graph-Driven Adaptive Tutoring Agent

[简体中文](README.md) ｜ **English**

> An AI study companion for university students: not just Q&A, but an active learning system with **a map** (knowledge graph), **a path** (learning planning), **memory** (learner profile) and **feedback** (AI quizzes + progress dashboard).

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## What is this

TutorAgent combines a conversational AI tutor with a knowledge graph and a personal learner profile, so the AI moves from "you ask, it answers" to active tutoring: it remembers what you have learned, sees the whole knowledge landscape, plans what to learn next, and automatically turns conversations into structured notes.

It targets three pain points of self-directed learning:

- **Fragmented, aimless learning** — the structure is invisible, so the pieces never connect → visualized knowledge graph + topologically sorted learning paths
- **No feedback after studying** — you cannot see your mastery or where the gaps are → mastery modeling + AI quizzes + progress dashboard
- **Note-taking is painful** — writing notes while learning is chaotic → conversations become notes; graph nodes and the learner profile accumulate automatically

Unlike general chat tools (ChatGPT / Kimi) that forget everything once the conversation ends, knowledge managers (Notion / Obsidian) that store but never teach, and MOOC platforms that give everyone the same page, TutorAgent fuses **conversation, knowledge graph, learner profile and path recommendation** into one system — an active learning partner, not a passive Q&A tool.

---

## Architecture

### Layered view

```
┌────────────────────── Frontend: Vue 3 SPA ─────────────────────────────┐
│  Chat │ Knowledge graph │ Dashboard │ Quiz │ KB │ Collector            │
│        Element Plus + D3 force-directed skill tree + Markdown / LaTeX  │
└───────────────────▲──────────────────────┬────────────────────────────┘
       SSE streaming text / events     REST queries (graph / quiz / KB / profile)
┌───────────────────┴──────────────────────▼───────── FastAPI backend ───┐
│  api/v1: auth chat conversations knowledge profile rag kb quiz collector│
│  chat_service: stream text first → background Agent Loop (true multi-  │
│    round LLM ↔ tool orchestration)                                     │
│    · Chat / Agent model: MiniMax-M3 (OpenAI-compatible + function call)│
│    · Toolset = graph CRUD & paths, profile notes, rag_search (graph|KB)│
│  Domain subsystems                                                     │
│    knowledge_graph / graph_middleware (subject→board slicing) /        │
│    graph_analyzer                                                      │
│    rag_pipeline (RagSource protocol, parallel multi-source fusion)     │
│    hybrid_search (vector + Whoosh BM25 + RRF) + kb/parsers + graph_gen │
│    quiz generation │ collector │ user profile │ conversations          │
│  Cross-cutting: event_bus │ error_codes │ rate_limiter │ llm/ │ config │
└────────┬──────────────────────────┬────────────────────────┬───────────┘
   SQLite (graph / chat / quiz / collector)  Node Markdown  Learner profile JSON
```

### Two core loops

1. **Learning loop (the more you use it, the better it knows you)**: conversation → the Agent Loop calls graph/profile tools → node mastery and the learner profile update → dashboard and learning path recompute → the next conversation uses all of it for weak-point guidance and recommendations.
2. **Knowledge loop (feed material in, then query it)**: upload textbooks (PDF/Word/PPT/images/text) or collect web pages with Collector → parsers route by type with automatic fallback for optional dependencies → chunking + vectorization and BM25 dual index → hybrid retrieval (wide recall → RRF fusion → provenance + parent expansion) → answered in conversation by the `rag_search` tool with citations.

### What happens in one conversation

User message → pure-rule routing (greetings / very short input skip RAG) → learner context injection + on-demand retrieval → **streaming the answer body first** (no perceived latency) → background `agent_loop.run_agent_loop` decides which tools to call (create/update graph nodes, update the profile, query the knowledge base) → graph and dashboard refresh automatically via SSE events, with no manual page reload.

---

## Core features

| Module | Description | Status |
|------|------|------|
| **Chat = Agent Loop** | Four modes (adaptive guidance / free talk / recursive teaching / path recommendation); background multi-round tool loop (max 5 rounds, per-tool timeout, trace persisted) | ✅ |
| **Streaming + events** | SSE token-by-token output plus background graph-operation events that refresh the UI automatically | ✅ |
| **Knowledge graph** | D3 force-directed visualization + CRUD + topologically sorted learning path (Kahn) + search-to-focus | ✅ |
| **Skill-tree graph** | Nodes colored by mastery level + legend + learning-path highlight + weak-point pulse | ✅ |
| **Progress dashboard** | Mastered / learning / not-started counts, average mastery, estimated time, weak points + per-subject progress | ✅ |
| **Learner profile v2** | Structured JSON (basic/goals/knowledge/learning/preferences/ai_notes) updated dynamically | ✅ |
| **File knowledge base (KB)** | Tree-style management; upload PDF/Word/PPT/images/text → parse, chunk, vectorize | ✅ |
| **Hybrid retrieval RAG** | Vector + Whoosh BM25 wide recall (30 each) → RRF fusion → path provenance + parent expansion | ✅ |
| **Agentic RAG** | `rag_search` tool: the LLM retrieves from graph / KB on demand and cites sources | ✅ |
| **Textbook → graph** | graph_generator builds graph nodes from subject textbooks with semantic deduplication | ✅ |
| **AI quiz generation** | Questions generated from textbook KB retrieval (single/multi choice, true-false, fill-in, short answer); rule-based or LLM grading | ✅ |
| **Collector** | Discovers and ingests subject material from Wikipedia/Wikibooks etc. (resumable + dual copyright modes) | ✅ |
| **Run records & evidence replay** | Every run is persisted to `agent_runs` (full thinking, complete tool arguments and results, final answer); events carry `run_id` for source-level review | ✅ |
| **Model fallback chain** | Silent failover to the backup service on quota exhaustion / auth failure / persistent errors — tutoring sessions never break | ✅ |
| **Dual copyright modes** | Personal mode (fair use) / commercial mode (L0 open licences only), filtered at the retrieval layer (L2) | ✅ |

## Tech stack

| Layer | Technology | Notes |
|------|------|------|
| **Frontend** | Vue 3 + Vite + Pinia + Element Plus + D3.js | SPA, force-directed skill tree, Markdown/LaTeX rendering |
| **Backend** | Python FastAPI + Uvicorn + Jinja2 | REST + SSE streaming, unified error codes |
| **AI chat** | MiniMax-M3 (OpenAI-compatible, function calling) | Main model is swappable to Alibaba qwen (see Configuration); retries with exponential backoff + jitter |
| **Embeddings** | Alibaba text-embedding-v4 (fixed, independent of the chat model) | Vector index / RAG recall |
| **Retrieval** | SQLite vector store + Whoosh BM25 + RRF fusion | `backend/app/core/hybrid_search/` |
| **Parsing** | PyMuPDF / python-docx / python-pptx / RapidOCR | Registry + strategy pattern, optional-dependency fallback |
| **Storage** | SQLite (graph / chat / quiz / collector) + node Markdown + profile JSON | Zero configuration, isolated per user |
| **Auth** | JWT (python-jose) + bcrypt | Register / login / token authentication |
| **Deployment** | Docker single container (Nginx:80 + Uvicorn:8000) + docker-compose | healthcheck + data volumes + SSE reverse proxy |

---

## Quick start

### Prerequisites

- Python 3.10+ (3.11 / 3.13 recommended for development)
- Node.js 18+

### One-command install & start (Windows PowerShell)

```powershell
# 1. Install dependencies (backend venv + frontend node_modules)
.\install.ps1

# 2. Configure the environment (the root .env is the single source of truth, shared by local and Docker)
copy .env.example .env
notepad .env        # fill in LLM_API_KEY (MiniMax) / DASHSCOPE_API_KEY / SECRET_KEY

# 3. Start (auto: container if Docker is available, otherwise local dev mode)
.\start.ps1
#    .\start.ps1 -Local          force local dev mode (hot reload + admin panel 8001/5174)
#    .\start.ps1 -Docker -Force  force container mode and rebuild (-Port 8081 to change port)
```

Then open http://localhost:5173 (an admin account is created on first start; **for production always set `DEFAULT_ADMIN_PASSWORD` in the root `.env`** — never expose the default `admin/admin123`, see the configuration table below).

### Docker deployment (Linux server)

```bash
# After configuring DASHSCOPE_API_KEY / SECRET_KEY in the root .env:
docker compose up -d --build
# Visit http://<server-ip>:8080
```

> Data is persisted by mounting `data/{knowledge,conversations,profiles}` + `backend/data`; `.dockerignore` excludes `.env` so container environment variables are never overwritten.

### Manual development

```bash
# Backend (reads the root .env automatically — do not create a second .env under backend/)
cd backend
python -m venv venv && venv\Scripts\activate   # Windows; Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
copy ..\.env.example ..\.env                     # Linux/macOS: cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8000

# Frontend (open another terminal)
cd frontend
npm install
npm run dev
```

### Service endpoints

| Service | URL |
|------|------|
| Frontend | http://localhost:5173 |
| Backend API / Swagger | http://localhost:8000 / /docs |
| Health check | http://localhost:8000/api/health |

---

## Configuration (root `.env`, template: `.env.example`)

| Variable | Required | Description |
|------|------|------|
| `LLM_API_KEY` | ✅* | Key for the main chat/Agent model (MiniMax China site by default); leave empty to fall back to `DASHSCOPE_API_KEY` and run Alibaba qwen |
| `LLM_BASE_URL` | ❌ | Defaults to `https://api.minimaxi.com/v1`; switch to the DashScope compatible endpoint when using qwen |
| `MODEL_NAME` | ❌ | Defaults to `MiniMax-M3` (1M context / multimodal); alternatives `MiniMax-M2.7` / `M2.5` / `qwen-plus` |
| `DASHSCOPE_API_KEY` | ✅ | Alibaba Cloud Bailian key: **required for text-embedding-v4** (independent of the chat model) |
| `EMBED_BASE_URL` | ❌ | Embedding endpoint, defaults to Alibaba Bailian; usually no change needed |
| `FALLBACK_LLM_API_KEY` | ❌ | Key of the backup chat service (model fallback chain): on quota exhaustion / auth failure / persistent errors the request is retried on the backup **silently**, so tutoring continues |
| `FALLBACK_LLM_BASE_URL` | ❌ | OpenAI-compatible endpoint of the backup service (takes effect only together with `FALLBACK_MODEL_NAME`) |
| `FALLBACK_MODEL_NAME` | ❌ | Backup model name (use an independent key from a **different vendor** for real redundancy; empty = single-model legacy behaviour) |
| `LLM_TIMEOUT` | ❌ | Request timeout, default 120 s |
| `SECRET_KEY` | ✅ | JWT signing key; startup is refused if missing |
| `CORS_ALLOW_ORIGINS` | ❌ | Comma-separated allowlist, localhost:5173 by default |
| `DEFAULT_ADMIN_PASSWORD` | ❌ | When set, an `admin` account is created on first start |

---

## Project structure

```
├── frontend/                 # Vue 3 frontend
│   └── src/
│       ├── views/            # Home (chat) / Knowledge (graph) / Dashboard / Quiz / Collector / Settings / Login
│       ├── components/       # ForceGraph (skill tree) / ChatArea / NodeDetail / ActivityBar ...
│       ├── stores/           # Pinia: authStore / chatStore
│       ├── api/              # axios + SSE wrappers
│       └── utils/            # feedback / errorCodes / theme
│
├── backend/                  # FastAPI backend
│   └── app/
│       ├── api/v1/           # auth/chat/conversations/knowledge/profile/rag/kb/quiz/collector
│       ├── services/         # chat_service (streaming + background Agent Loop orchestration)
│       └── core/
│           ├── agent_loop.py            # Agent multi-round loop (LLM ↔ tools)
│           ├── agent_run_store.py       # Run records (single source of truth, evidence-level JSON)
│           ├── agent_tools.py           # Tool registry (KG_TOOLS and the dispatcher are generated)
│           ├── knowledge_graph.py / graph_middleware.py / graph_analyzer.py
│           ├── rag_pipeline/            # RagSource protocol + routing + multi-source fusion
│           ├── hybrid_search/           # Vector + Whoosh BM25 + RRF / weighted fusion
│           ├── kb/                      # Tree knowledge base + parsers + graph_generator
│           ├── rag/  quiz/  collector/  # Graph RAG / AI quizzes / material collection
│           ├── profile/                 # Learner profile (schema / store / render / facade)
│           ├── llm/                     # LLM primitives: clients / embed / messages / retry / fallback / call
│           └── event_bus.py / error_codes.py / rate_limiter.py /
│               config.py / prompt_loader.py / token_counter.py
│
├── data/                     # Runtime data (never committed, isolated per user)
│   ├── knowledge/            # Graph db + node Markdown
│   ├── conversations/ profiles/ prompts/
│   └── collector/
├── Dockerfile / docker-compose.yml / nginx.conf / entrypoint.sh   # Production deployment
├── install.ps1 / start.ps1   # Windows one-command install/start
├── README.md / README.en.md  # Chinese master document + English mirror (switch at the top)
├── CONTRIBUTING.md           # Contribution guide: environment, test commands, commit rules
├── AGENTS.md                 # Architecture contract index (for AI coding agents and developers)
└── docs/                     # Research, design documents and the README writing spec
```

## Quality and testing

- **745 offline tests pass** (LLM / embedding network calls are mocked, so no internet is required; 752 tests are collected in total, of which 7 real-API tests are excluded by the `llm_api` marker):
  chunking / fusion / parent expansion / routing / pipeline exception isolation / sparse index / graph slicing / prerequisite inference / mastery bucketing / the `rag_search` tool / the full upload-and-retrieve path / user isolation / collector registry / commercial-mode filtering / Agent Loop and run records.
  Reproduce with (cwd = `backend`):

  ```bash
  backend/venv/Scripts/python.exe -m pytest tests -q -m "not llm_api"   # → 835 passed, 7 deselected
  ```

- **Offline evaluation set** (reusing CMRC2018, 256 documents / 1000 queries): `backend/scripts/eval_rag.py`
  mock baselines: vector R@1 = 0.470 / BM25 0.964 / hybrid(RRF) 0.766 / fuse(weighted α = 0.6) 0.818 (rerun with `--embed api` once the real text-embedding-v4 quota is restored).
- **Engineering conventions**: YAGNI minimal implementations, prompts externalized as templates (Jinja2), a single configuration source (root `.env`), unified error codes, per-user isolation.
- **Methodology disclosure**: mocking replaces only the LLM / embedding **network calls**, which keeps CI hermetic and repeatable; chunking, retrieval, graph and Agent Loop logic all run for real. Real-model end-to-end runs (chat / streaming / tool calling) and real-embedding evaluation are executed separately in a deployment with API keys.

---

## Documentation index

- [Project Wiki](https://github.com/qiyuxi24/AI-tutor/wiki) — module-by-module guide: quick start / architecture / agent kernel / knowledge graph / hybrid retrieval / API reference / FAQ (in Chinese)
- [README writing spec](docs/工程实践/README_编写规范.md) (v3: facade template / bilingual maintenance / contribution guide, in Chinese) ｜ [Contributing](CONTRIBUTING.en.md) ｜ [中文 README](README.md)
- [AGENTS.md development handbook](AGENTS.md) — architecture contracts, module responsibilities, and where to plug in new tools/endpoints (in Chinese)
- [RAG decoupling & directory retrieval research](docs/RAG/RAG_去耦合与目录检索调研.md) ｜ [RAG recall & rerank research](docs/RAG/RAG_召回与重排优化调研.md) ｜ [Agentic RAG research](docs/RAG/RAG_参考资料与学习路线.md) (in Chinese)
- [Agent Loop redesign](docs/AgentLoop/AgentLoop_重构设计讨论.md) ｜ [Agent Loop industry research](docs/AgentLoop/AgentLoop_业界调研与学习路线.md) (in Chinese)
- [Knowledge-graph frame-of-reference contract](docs/知识图谱/知识图谱_参照系契约.md) ｜ [Knowledge-graph module structure research](docs/知识图谱/知识图谱_模块结构与封装调研.md) (in Chinese)
- [AI quiz generation research](docs/教学模块/QUIZ_出题逻辑调研.md) ｜ [Collector design discussion](docs/教育资料采集/教育资料采集模块_设计讨论.md) (in Chinese)
- [Docker learning path & engineering deployment](docs/运维部署/Docker_学习路径与工程化部署.md) ｜ [Production launch & daily operations guide](docs/运维部署/运维_生产上线与日常运营指南.md) ｜ [Benchmark project comparison](docs/调研对标/标杆项目对标分析.md) (in Chinese)

## Contributing

Issues and pull requests are welcome: bug reports, documentation fixes, bilingual sync, new subject graph data, retrieval evaluation reruns, and more.
Before you start, read [CONTRIBUTING.en.md](CONTRIBUTING.en.md) (environment setup, test commands, commit rules) — the Chinese contributor guide is [here](CONTRIBUTING.md); before submitting, make sure the `-m "not llm_api"` test suite passes and follow the project's existing layering and YAGNI minimal-implementation conventions. If you change the architecture, commands or quantitative figures, **update both the Chinese and English READMEs**.

## License

[MIT](LICENSE)
