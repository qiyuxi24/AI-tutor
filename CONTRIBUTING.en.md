<!-- base: CONTRIBUTING.md @ c54d571 (2026-09-12). English mirror of the Chinese contribution guide — keep both in sync; spec: docs/README_编写规范.md §6. -->

# Contributing to TutorAgent

[简体中文](CONTRIBUTING.md) ｜ **English**

Thank you for your interest in TutorAgent — whether you are here to report a bug, fix documentation, or contribute knowledge-graph data for a new subject, this guide has one goal: **get you running the project within 10 minutes and show you how to submit your change**.

---

## 1. What you can contribute

| Contribution type | Typical content | Open an issue first? |
|---|---|---|
| Bug report | Reproduction steps + environment + expected vs. actual behaviour | No, just open an issue |
| Documentation fix | Typos, dead links, outdated commands, bilingual drift | **No**, a pull request is fine |
| Tests | Additional edge cases, fixing flaky cases | Not for small changes; discuss new test frameworks or dependencies first |
| Code | New features, refactoring, performance, supporting another model | **Yes** — align on the approach in an issue to avoid wasted work |
| Data | New subject graphs, evaluation sets, glossaries | Yes — state the data source and licence first |
| Ecosystem | Prompt templates, tool integrations, deployment scripts | Yes |

> Before starting, search both **open and closed** issues to avoid duplicate discussions; if you want to take on an existing issue, leave a comment to claim it.

---

## 2. Development environment

### Prerequisites

| Item | Requirement |
|---|---|
| Python | 3.10+ (3.13 is used for development and CI in this project) |
| Node.js | 18+ (frontend Vite) |
| Database | None — SQLite requires zero configuration |

### Fastest path (Windows PowerShell)

```powershell
.\install.ps1                 # create venv + install backend deps + frontend node_modules
copy .env.example .env        # the root .env is the single source of truth
notepad .env                  # fill in LLM_API_KEY / DASHSCOPE_API_KEY / SECRET_KEY
.\start.ps1                   # start backend and frontend → http://localhost:5173
```

### Manual setup

```bash
# Backend (cwd = backend)
cd backend
python -m venv venv
venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (second terminal, cwd = frontend)
cd frontend
npm install
npm run dev
```

### Four hard constraints (ignore them and it breaks)

1. **Always use `backend/venv/Scripts/python.exe`**: the system Python (3.11) ships a FastAPI / Starlette version that is too old and fails to import.
2. **`uvicorn` must run with `--workers 1`**: the EventBus per-user queues and the in-process scheduled GC assume a single process; multiple workers each keep their own queues and events are lost.
3. **There is exactly one configuration source: the root `.env`** (read by `backend/app/core/config.py`). Do not create a second `.env` under `backend/`, and never commit `.env`.
4. **Chat model and embeddings are configured independently**: the chat model (`LLM_API_KEY` + `LLM_BASE_URL` + `MODEL_NAME`) and embeddings (`DASHSCOPE_API_KEY`, fixed to Alibaba text-embedding-v4) do not affect each other — change one, leave the other alone.

---

## 3. Running the tests (the single hard gate before submitting)

Offline unit / integration tests (the LLM and embedding network calls are mocked, so no API key is required):

```bash
cd backend
venv\Scripts\python.exe -m pytest tests -q -m "not llm_api"
```

**Expected output**:

```
745 passed, 7 deselected
```

- `7 deselected` are the real-API cases marked `llm_api` (`tests/test_agent_loop_real_api.py`): they need real keys plus `pytest-asyncio`, so do not force them locally without keys; CI excludes them with the very same command.
- When you open a pull request, **paste the output of that command** — it is also the acceptance criterion used by CI (`.github/workflows/backend-tests.yml`).
- If you touch retrieval logic, also run the offline evaluation (`mock` mode needs no key):

```bash
cd backend
venv\Scripts\python.exe scripts/eval_rag.py --embed mock
```

---

## 4. Coding conventions and hard constraints

Guiding principle: **follow the existing style and layering of the repository first, personal preference second**.

| Topic | Convention |
|---|---|
| Layering | `api/v1/` holds thin HTTP shells only; orchestration lives in `services/`; domain logic lives in `core/`. Never import `api/` from `core/`. |
| Single source of truth | Config = root `.env`; run records = `core/agent/store.py`; embedding calls = `core/llm/embed.py::embed_texts`; tool registry = `core/agent_tools/registry.py::_TOOL_SPECS`. **New capabilities attach to existing entry points — never create a parallel copy.** |
| Minimal implementation | YAGNI: first ask "is this needed now / can the standard library or existing code already do it"; do not add abstraction layers or dependencies for hypothetical needs. |
| Error handling | Error codes live in `core/error_codes.py`; exception messages start with `[E-XXX]` and go through `log_error()`; tools return text such as "操作失败: …" to the model instead of raising bare exceptions. |
| User isolation | Graph / profile / knowledge base / run records are all partitioned by `user_id`; create `KnowledgeGraph(user_id)` per use and `close()` it afterwards — never share an instance across coroutines. |
| `user_id` source | Always resolved from the JWT (`Depends(get_current_user)`); **never** pass it in the body or query string. |
| Prompts | Externalized as Jinja2 templates (`data/prompts/*.j2`); do not hard-code long prompts into Python strings. |
| New LLM calls | Go through `core/llm/` (fallback chain and retries are built in); do not `httpx.post` a model endpoint from business code. |
| New tools | Create one module per tool under `core/agent_tools/tools/` (copy any module and edit five spots), then add one line to `NATIVE_SPECS` in `tools/__init__.py`. See `core/agent_tools/tools/README.md`. |
| Comments | Only where the "why" is not obvious; do not narrate what the code does line by line. |

> Changing the architecture (splitting modules, altering contracts, changing table schemas)? Read the architecture contract and the "known couplings and pitfalls" section in [`AGENTS.md`](AGENTS.md) first, then open an issue to discuss.

---

## 5. Commits and pull requests

### Branch naming

| Prefix | Purpose |
|---|---|
| `feature/short-description` | New feature |
| `fix/short-description` | Bug fix |
| `docs/short-description` | Documentation |
| `refactor/short-description` | Refactoring (no behaviour change) |

### Commit format

```
<type>: <one-line description>
```

| type | Meaning |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Refactoring, no behaviour change |
| `test` | Test additions / corrections |
| `chore` | Build, dependencies, tooling |

Requirements:

- One commit does one thing; **do not mix unrelated changes into the same pull request** (it holds the whole PR back).
- Split commits by **file family** (each commit should be a working intermediate state), not by time or topic across files, so no historical commit breaks imports.

### Pull request template

```markdown
## What changed
(one-sentence summary + key files)

## Why
(linked issue: Closes #xx; or the pain point)

## How it was verified
- [ ] `backend/venv/Scripts/python.exe -m pytest tests -q -m "not llm_api"` → 745 passed, 7 deselected
- [ ] Manual path: (describe the screens or endpoints you exercised)

## Impact
- [ ] Does it change architecture contracts / table schemas / environment variables (if so, update AGENTS.md and .env.example)
- [ ] Are both the Chinese and English documents in sync (see section 6)
```

### Do not commit these

| Path | Reason |
|---|---|
| `.env` and any key files | Already in `.gitignore`; leaking them is serious |
| `data/` (graph / profile / conversations / KB index), `backend/data/` | Local runtime data, rebuildable |
| `logs/`, `*.bak`, `whoosh_index/` | Generated artefacts |
| `.research/` | External benchmark repositories (cloned third-party sources) |
| `docs/比赛/官方材料/` | Licence and usage restrictions on first-hand material |
| `venv/`, `node_modules/`, `dist/` | Dependencies and build output |

> Exceptions: `data/prompts/*.j2` (prompt templates) and `data/*.md` (documents) **are** committed.

---

## 6. Documentation and bilingual duty (specific to this repository)

The facade documents are maintained in both Chinese and English:

| Master (source of truth) | Derived mirror |
|---|---|
| `README.md` | `README.en.md` |
| `CONTRIBUTING.md` | `CONTRIBUTING.en.md` |

Rules (full spec: [`docs/README_编写规范.md`](docs/README_编写规范.md), section 6):

1. **Editing only the master file is not enough**: added/removed/renamed sections, commands, ports, environment variables and test counts must be mirrored **in the same commit**.
2. Section numbering / order, table row counts and code blocks must correspond one-to-one.
3. Code block contents are identical character for character (commands, paths, URLs and badge syntax are **not translated**).
4. After syncing, update the `<!-- base: README.md @ <commit> (date) -->` comment at the top of the mirror.
5. Purely cosmetic changes may lag, but should be picked up in the next sync.

> Syncing after an architecture, command or number change costs five minutes; skipping it leaves the English version three months behind.

---

## 7. AI-assisted contributions

Patches generated or rewritten with AI **are welcome**, provided that:

- You have **read and run** them yourself: the commands execute, the tests pass, and the layering and YAGNI conventions of this repository are respected.
- You verify that figures and claims are true before submitting (do not paste AI-fabricated benchmarks or paths).
- Your pull request states which parts were AI-assisted, so reviewers know where to focus.
- **The contributor remains responsible for the submitted content** — "the AI wrote it" is not an excuse.

---

## 8. Communication and response

- All discussion happens in public issue / PR comments (security issues are the exception — contact the maintainer privately); public discussion is itself a contribution.
- When reporting a problem, give full context: environment (OS / Python / Node versions), reproduction steps, expected vs. actual behaviour, and the complete error output.
- Maintainers are volunteers — please be patient; if there has been no response for over a week, a polite nudge in the same issue is fine.
- If changes are requested, please respond; if you can no longer continue, say so, so someone else can take over.
- In disagreements, respect the maintainer's final decision; if you strongly disagree, you are free to fork.

---

Thanks for the time you spend on TutorAgent — happy hacking.
