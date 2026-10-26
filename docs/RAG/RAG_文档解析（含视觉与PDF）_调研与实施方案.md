# RAG 文档解析（含视觉与 PDF）：调研与实施方案

> 创建：2026-09-12（格式覆盖与去耦合）；**v2 合并（本次）**：并入 2026-09-13《RAG 视觉解析策略_调研与实施方案》，覆盖「解析层」全部范围。
> 定位：RAG **解析层**的选型、契约与实施，分两半——
> ① **格式覆盖与去耦合**（原《文档解析模块》）：parsers 注册表支持哪些格式、外围耦合在哪、怎么加新格式。
> ② **视觉与版面**（原《视觉解析策略》）：文档里的「图」和「版面」用什么引擎、怎么按页路由、怎么控成本、怎么防污染、逐页探针契约。
> 与另两份 RAG 文档分工：
> - `docs/RAG/RAG_去耦合与目录检索调研.md` —— **检索层**去耦合（RAG 路由器 + Parent-Child），不涉及解析。
> - `docs/RAG/RAG_图谱混合检索与真实嵌入评测_报告.md` —— 召回/融合策略评测。
> - **本文件** —— 解析层（parsers/格式覆盖 → 视觉与版面/逐页探针契约）。
> 来源（两份原文件已删除）：`RAG_文档解析模块_调研与去耦合设计.md`（2026-09-12）、`RAG_视觉解析策略_调研与实施方案.md`（2026-09-13）。
> ⚠️ 章节号已重排；对被删原文件的 § 引用请按 §八 尾部的「编号对照」对齐。

---

## 一、核心结论（先看这段）

**A. 格式覆盖与去耦合（原《文档解析模块》）**

1. **解析层骨架已对**：`parsers/` 是「注册表 + 策略」（`ParserRegistry` 按扩展名路由 → `BaseParser` 子类），新增格式 = 新增类 + 注册一行，3 个调用方（`kb_manager` 上传 / `collector` 抓取 / `rag_pipeline` 入库）零改动。
2. **真正的耦合在外围三处**（不在 parsers 内部）：格式白名单各写一份、`parse_document` 处契约被截断成纯文本、OCR 能力被 `pdf → image` 私有符号横向引用。见 §2.3。
3. **电子书支持用标准库就够**：EPUB = zip + XHTML + OPF 清单，FB2 = XML，`zipfile` + `xml.etree` **零依赖**；MOBI/AZW3/DJVU 沿用 `legacy.py` 的「能力探测，装则注册」。
4. **不引入 unstructured / docling / markitdown 解析全家桶**：重依赖，能力重心与本项目已有栈（PyMuPDF + RapidOCR）重叠且收益不匹配成本。

**B. 视觉与版面（原《视觉解析策略》）**

5. **「直传 PDF 给模型」这条路不存在**：M3 的 OpenAI 兼容 chat 支持 `image_url`（URL/base64、`detail` 分档、单图 ≤10MB、请求体 ≤64MB），但 content parts **只有 text / image_url / video_url，没有 PDF / document / file**。视觉解析必须**自己把页面渲染成图**（PyMuPDF 在手）——省掉的只是「理解」，不是「投喂」。
6. **现有短板不是「识别精度不够」，而是「根本没识」**：扫描判定**整份文档级**（混合型 PDF 静默丢内容，属真 bug）；**内嵌图片/图表 100% 丢弃**；表格线性化打乱；公式乱码无检测。这些是**能力与逻辑缺陷**，不是模型选得不好。见 §2.2。
7. **成本出乎意料：专用 OCR API 比通用视觉模型便宜约 31 倍**。`qwen-vl-ocr`（阿里百炼，华北2）输入 **¥0.3/M**、输出 **¥0.5/M**；MiniMax-M3 同档位 **¥4.20/M / ¥16.80/M**。200 页教材：`qwen-vl-ocr` 全量 ≈ **¥0.3/本**，M3 视觉全量 ≈ **¥10/本**。
8. **本机无 NVIDIA GPU**（`nvidia-smi` 不存在）+ Docker 部署 → **本地 DocVLM 路线否决**（MinerU 2.5-1.2B / olmOCR-7B / PaddleOCR-VL 在 CPU 不可用；唯一 CPU 可行的 Granite-Docling-258M 要引 torch + transformers 且精度不足）。数据留档 §3.3。
9. **推荐形态：云端专用 OCR 为主 + 本地提取为旁路，互为校验**。干净单栏页走本地（无损、零延迟）；扫描/复杂版面/公式/表格页走 `qwen-vl-ocr`；**图/图表走通用 VLM（`qwen3-vl-plus`）描述 + caption 配对入独立 chunk**。**M3 仅作降级备用**（复用 `llm/fallback.py`）。
10. **顺序上先做不花钱的那一半**：P0（逐页探针 + 契约升级 + 结构化分块，¥0）；**没有 P0 的 P1 无法验收**。
11. **最大风险不是钱，是污染**：在建知识库，模型抄错会永久写进向量库。**交叉校验闸门（§6.4）是必备件**。

---

## 二、现状诊断

### 2.1 解析骨架与已支持格式

`parsers/` 为「注册表 + 策略」：`ParserRegistry` 按扩展名路由 → `BaseParser` 子类实现；`is_supported()` 在 `kb_manager` 上传前作为白名单闸门；解析文本 < `MIN_PARSE_TEXT_LEN=200` 视为图片型/不可解析并拒收。

| 格式 | 解析器 | 依赖 | 文件 |
|---|---|---|---|
| `.txt .md .log .json .csv` 等 30+ | `TextParser` | 标准库（多编码尝试） | `parsers/text.py` |
| `.pdf` | `PdfParser` | PyMuPDF；扫描页 RapidOCR 回退 | `parsers/pdf.py` |
| `.docx` / `.pptx` | `DocxParser` / `PptxParser` | python-docx / python-pptx | `parsers/docx.py` / `pptx.py` |
| `.png .jpg .bmp .webp .tiff .gif` | `ImageOcrParser` | RapidOCR（可选依赖，装则注册） | `parsers/image.py` |
| `.doc .ppt .xls` | `LegacyParser` | textract / LibreOffice / antiword 等（探测） | `parsers/legacy.py` |
| **`.epub .fb2`** | `EpubParser` / `Fb2Parser` | **零依赖**（zipfile + ElementTree） | `parsers/book.py` |
| **`.mobi .azw .azw3`** | `MobiParser` | mobi 库或 Calibre（探测） | `parsers/book.py` |
| **`.djvu`** | `DjvuParser` | djvulibre 的 `djvutxt`（探测） | `parsers/book.py` |

调用链（新格式自动生效的原因）：

```
POST /kb/upload ─► kb_manager.upload_and_index ─┐
collector/manager.py 抓取入库 ─────────────────┼─► parse_document(filename, bytes)
rag_pipeline 上传入库 ──────────────────────────┘        │
                                        ParserRegistry（ext → parser）
                                                          │
                                        BaseParser.parse → ParseResult(text, meta)
```

### 2.2 缺陷清单（PDF，按严重度排序）

> 这是「视觉与版面」这一半的动因清单。`test_pdf_parser.py` 的回归防线对齐本表。

| # | 现象 | 位置 | 严重度 | 说明 |
|---|---|---|---|---|
| 1 | **扫描判定是整份文档级**：`SCAN_TEXT_THRESHOLD = 20` 对**全文** `strip()` 后长度判定 | `parsers/pdf.py:24, 44-47` | **严重（静默丢内容）** | 100 页 PDF，95 页扫描 + 5 页文本层 → 全文文本量远超 20 字符 → **OCR 永不触发 → 95% 内容静默消失，且不报错**。这是数据损坏，不是质量问题 |
| 2 | **解析契约在门面处被截断**：`parse_document()` 返回 `(text, ext)`，`ParseResult.meta` 被丢弃 | `parsers/__init__.py:100-105` | **严重** | 下游无法得知「这份文本用什么方式、几页、有没有 OCR、质量如何」→ 既无法路由也无法验收 |
| 3 | **图片/插图/图表 100% 丢弃**：无抽取、无 caption 配对、无描述 | 全链路无相关代码 | **严重（能力为零）** | 用户直觉中的「识图能力不足」，实际是「识图能力为零」。图表是教材信息密度最高的部分 |
| 4 | 表格被线性化打乱：`get_text("text")` 顺序输出，行列关系丢失 | `parsers/pdf.py:41` | 高 | PyMuPDF 自带 `page.find_tables()` 可解，零新依赖，未用 |
| 5 | 公式/特殊字体出乱码且无检测 | 同上 | 高 | 数学/CS 教材核心内容被破坏成不可检索字符，且无告警 |
| 6 | 页眉页脚污染 + 多栏阅读顺序错 | 同上 | 中 | 多栏 PDF 的块序按 y 坐标重排即可缓解 |
| 7 | 分块是纯字符窗（500/50），`heading` 取首行前 30 字 | `kb_manager.py:54-90` | 中 | **结构在解析之后又被抹平**——只升级解析，收益会被这一层吃掉一半 |
| 8 | OCR 能力横向耦合：`pdf.py` 私有导入 `image.RapidOcrEngine` / `_have_rapidocr` | `parsers/pdf.py:64` | 中 | 换引擎要改两个文件；与 AGENTS.md §3.6 同类问题同源。**本方案的 P1 依赖它** |
| 9 | 解析同步内联在 HTTP 请求内 | `kb_manager.py:157-208` | 中 | 云引擎逐页解析 200 页 = 分钟级 → 必然请求超时。必须异步任务化 |
| 10 | 无多模态基建：`call_llm` 固定 `max_tokens=2000` | `core/llm/call.py:41` | 中 | `build_api_messages` content 直通可放 parts；但转写一页都不够 |
| 11 | `parsers/ocr.py` 能力层不存在 | — | 中 | 本方案把它提升为 P1 前置 |
| 12 | 无 `data/kb/` 目录、无实测评测集 | `data/` | 中 | 知识库无上传文件 → 评测集需先自建（§6.1） |

**一句话总结现状**：**「解析 → 纯文本 → 按字符切」**，契约在纯文本处截断，版面/结构/图表全部丢失。

### 2.3 耦合清单（格式覆盖侧，按影响排序）

| # | 现象 | 位置 | 影响 | 处置 |
|---|---|---|---|---|
| 1 | 格式白名单写 3 份：前端 `acceptTypes`、`registry._parsers`、`kb_manager.IMAGE_EXTS` | `KbPanel.vue` / `parsers/` / `kb_manager.py` | 新增格式必须记得改前端，否则用户选不到文件 | 建议 P1：后端暴露 `GET /kb/formats`（复用 `supported_extensions()`），前端拉取 |
| 2 | 解析结果契约只有文本：`parse_document` 只回 `(text, ext)` | `parsers/__init__.py` | 页码/章节/作者/来源元信息下游拿不到；分块器只能用「首行前 30 字」当 heading（同 §2.2 #7） | P2：`parse_document` 加 `verbose=True` 返回 `ParseResult`（与 §2.2 #2 同一改造） |
| 3 | **OCR 能力横向耦合**：`pdf.py` 私有导入 `image.RapidOcrEngine` / `_have_rapidocr` | `parsers/pdf.py` → `parsers/image.py` | 换 OCR 引擎要改两个文件；跨模块引用私有符号 | P2：抽 `parsers/ocr.py` 作为能力层（视觉方案 P1 已把它提前实施，见 §八） |
| 4 | `BaseParser._ok(meta=...)` 的关键字参数被 `**meta` 吞掉，实际产出 `meta={"meta": {...}}` | `parsers/base.py` | `PdfParser`/`ImageOcrParser` 的页数、OCR 引擎标记全部丢失 | **已修**（改为显式 `meta: Optional[dict]` 形参） |
| 5 | 门槛值 `MIN_PARSE_TEXT_LEN=200` 对图册/漫画类偏严 | `kb_manager.py` | 纯图集会被拒 | 保持（有明确报错文案）；真出现需求再按格式分档 |

---

## 三、调研

### 3.1 电子书格式地形图与库选型

**格式地形（EPUB/FB2 常驻；MOBI/AZW3、DJVU 探测；CBZ/CBR、CHM/LIT/PDB 不做）**：EPUB 2/3 = zip（`META-INF/container.xml` → OPF 清单 + spine + XHTML），开放标准、无 DRM、难度低 → ✅ 标准库 `EpubParser`；FB2 = 单文件 XML，无 DRM、难度低 → ✅ 标准库 `Fb2Parser`；MOBI/AZW/KF8 = PalmDOC 容器，**常带 DRM**、难度高 → ⚠️ 可选 `mobi` 库或 Calibre；DJVU = 专有压缩 + 可选文字层，需外部工具 → ⚠️ 可选 `djvutxt`（无文字层仍需 OCR）；PDF = 既有（PyMuPDF + OCR 回退）。

> DRM 立场：只解析**无 DRM** 的电子书；带 DRM 的解析失败并回落到「不支持」提示。这是能力边界，也是合规边界。

**库选型对比（为什么标准库）**：

| 方案 | 体积/依赖 | 覆盖 | 结论 |
|---|---|---|---|
| **标准库 `zipfile` + `xml.etree`** | 0 | EPUB（spine 顺序、章节标题、段落）、FB2 | ✅ **采用**：读场景够用，无维护风险 |
| `ebooklib` | 轻 | EPUB2/3 读写 | ⛔ 不引入：只用读能力、收益 < 一个依赖 |
| `mobi`（PyPI） | 轻 | MOBI/AZW；KF8/AZW3 支持有限，维护停滞 | ⚠️ 可选探测项，失败即降级 Calibre |
| `kindleunpack` | 中 | AZW3/KF8 解包最完整 | ⛔ 暂不引入；如需要只替换 `MobiParser._via_mobi` 单点 |
| **Calibre `ebook-convert`** | 重（外部程序） | 全格式互转 | ⚠️ 可选外部能力（探测） |
| `pandoc` / `pypandoc` | 中（外部二进制） | EPUB→HTML/MD 质量好 | ⛔ 不引入：标准库已覆盖 |
| `unstructured` / `docling` / `markitdown` | 很重（torch/onnx） | 版面、表格、公式、多模态 | ⛔ 不引入；`markitdown` 的「格式→转换器注册表」思路与本包同构，可作参考 |
| PDF 专项（PyMuPDF4LLM / Marker / MinerU / olmOCR） | 各自独立 | 版面/公式/表格 | 保持现状；未来若做 PDF 专项再单点评估（见 §3.3） |

> 业界印证：**unstructured** 的 `partition_epub` 是「先转 HTML，再走 `partition_html`」——判断一致：EPUB 正文就是 XHTML，不需单独引库；**MarkItDown（微软）** 每格式一个转换器 + 统一输出，与本 `parsers/` 注册表同构。
> RAG 解析层通用分层：`解析 → 结构化 blocks → 分块 → 索引`；本项目现状是「解析 → 纯文本 → 分块」，**契约在纯文本处截断**，这是提升检索质量的第一顺位改造点。

### 3.2 PDF 不是一个问题，是六个

不同页面的失效原因完全不同，**这是「按页路由」而非「全局选型」的根本原因**。

| 页面类型 | 典型特征 | 现有链路表现 | 需要的引擎能力 |
|---|---|---|---|
| A. 干净数字版（单栏正文） | 文本层完整、无大图 | **基本正确** | 本地提取（增强结构即可） |
| B. 数字版复杂版面（双栏/表格/公式） | 文本层有但顺序/结构错 | 表格打乱、公式乱码、栏序错 | 版面还原 + 结构输出 |
| C. 纯扫描件 | 无文本层 | **仅当整份 <20 字符才 OCR**（缺陷 #1） | OCR + 版面还原 |
| D. 混合型（部分扫描） | 部分页无文本层 | **完全不处理**（缺陷 #1） | 逐页判定 |
| E. 图文混排（图表/示意图为主） | 大图 + 少量说明文字 | **图全丢**（缺陷 #3） | 视觉理解（描述图，非抄文字） |
| F. 数学/STEM 密集 | 公式、特殊字体、符号表 | 乱码（缺陷 #5） | 公式 → LaTeX |

**关键认知**：C/D 是**逻辑缺陷**（要么全 OCR 要么全不 OCR），E 是**能力缺失**（根本没有这条路径），F 是**渲染/字体问题**。三者修复手段不同，不要混为一个「解析精度」问题。

### 3.3 四条技术路线与关键数据

| 路线 | 代表 | 精度 | 速度 | 成本 | 本项目适配 |
|---|---|---|---|---|---|
| **① 本地传统 OCR** | RapidOCR（现有）、PaddleOCR、Tesseract | 字符级尚可，**无版面/表格/公式** | 快（CPU 可行） | 0 | ✅ **保留**为扫描件兜底与离线能力 |
| **② 本地 DocVLM** | MinerU 2.5-1.2B、olmOCR-7B、PaddleOCR-VL、Granite-Docling-258M、Marker 2 | **最高**（olmOCR-bench 72.7–85.8） | 需 GPU | 0（硬件高） | ⛔ **否决**（无 GPU，见下） |
| **③ 云端专用 OCR API** | **`qwen-vl-ocr`**（阿里百炼） | 高（专为文档/表格/公式训练） | 快（RPM 600–6000） | **¥0.3/¥0.5 每 M token** | ✅ **主力** |
| **④ 云端通用 VLM** | `qwen3-vl-plus`、MiniMax-M3、Gemini Flash | 高（通用理解强） | 中（8–15s/页） | ¥1/¥10（qwen3-vl-plus）、¥4.2/¥16.8（M3） | ✅ **仅用于「描述图」** + M3 作降级备用 |

> **②③的分工不等于「便宜的干脏活」**：`qwen-vl-ocr` 是**转写**专精模型，让它「描述电路图讲什么」是错配；`qwen3-vl-plus` 抄整页文字比专用 OCR 贵且未必更准。**按「转写 vs 理解」分工，而非按价格分工。**

**（a）本机硬件实测**：`nvidia-smi` 不存在 → 无 NVIDIA 驱动/GPU；部署是 Docker 容器 → **本地 DocVLM 直接出局**。

**（b）本地 DocVLM 横评（留档，供未来换硬件复查）**：Granite-Docling-258M（258M）CPU **6–8s/页**（200 页/本 = **20–27 分钟**），扫描件 76.8%、跨页表格 71%，代价 torch+transformers；MinerU 2.5（1.2B）与 olmOCR-7B 在 CPU **不可行**（olmOCR-7B 扫描件 **96.4%** 最高）。旁证 olmOCR-bench（1,403 PDF / 8,400 单测）：Marker 2 **76.0%** / MinerU pipeline **72.7%** / Docling **50.3%**；全页 VLM 档 Chandra 2 **85.8** / olmOCR 2 **82.4** / Gemini Flash 3.5（API）**76.4**。数据源 YOMXXX 横评（2026-05-22，个人博客，含 affiliate、方法未公开 → **仅作趋势参考，未作选型依据**）。

**（c）云端专用 OCR 硬参数（可直接按此写代码）**：

| 项 | `qwen-vl-ocr`（动态） | `qwen-vl-ocr-2025-11-20` | `qwen-vl-ocr-latest` |
|---|---|---|---|
| 上下文 / 最大输入 / 最大输出 | 38,192 / 30,000 / **8,192** | 同上 | 同上 |
| `max_tokens` 实际上限 | **4,096**（提额需报商务） | 8,192 | 8,192 |
| 价格（北京，输入/输出 元/M） | **0.3 / 0.5** | **0.3 / 0.5** | **0.3 / 0.5** |
| Batch File（北京） | **0.15 / 0.25**（仅动态版与 latest） | — | — |
| RPM / TPM（北京） | 600 / 6M | **6,000 / 30M** | **6,000 / 30M** |
| 图片 token 换算 | 28×28 px/token | **32×32 px/token** | 32×32 px/token |
| 结构化输出 / Function Calling | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ |

> **选型要点（易踩坑）**：**不要用动态版 `qwen-vl-ocr`**（北京仅 600 RPM，`max_tokens` 默认=上限=**4096**）；应显式锁 **`qwen-vl-ocr-2025-11-20`** 或 `-latest`（8,192 输出 / 6,000 RPM）。旧快照（`-2025-08-28` 等）单价 **¥5/¥5**，比新版贵约 16 倍 —— **版本选择直接决定成本**。
> 另注：文档中出现 **`qwen3.5-ocr`**（`max_tokens` 默认与上限均 **32,768**，32×32 px/token），对「一请求多页」最友好，**但价格与限流未载，实施前需在控制台核实**。

**（d）图片传入的两条调用路径**：
- **OpenAI 兼容** `POST /compatible-mode/v1/chat/completions`：`image_url` 支持 URL 或 **Base64 Data URL**；自带 `min_pixels`/`max_pixels`；可自定义 prompt。**拿不到内置任务**（`ocr_options` 是 DashScope 侧参数）。
- **DashScope 原生** `/api/v1/services/aigc/multimodal-generation/generation`：内置任务 `ocr_options.task`（`text_recognition` / `document_parsing`→LaTeX / `table_parsing`→HTML / `formula_recognition`→LaTeX / `key_information_extraction` / `multi_lan` / `advanced_recognition`→含坐标）。需另写 HTTP 调用（httpx 已装，**零新依赖**，但脱离 `chat_create` 基座）。

**（e）MiniMax-M3 视觉参数（备用引擎）**：`image_url` 支持 URL/base64，`detail` = low(≤600 tok)/default(1k–3k)/high(数千–15k+)，单图 ≤10MB，请求体 ≤64MB；`n` 仅 1；**无 PDF/document content part**。

### 3.4 成本模型（决策的核心算式）

单页成本（A4 @200dpi ≈ 3,780 输入 token；密排中文一页 ≈ 800 输出 token）：

| 引擎 | 单页 | 200 页/本 | 1000 页 |
|---|---|---|---|
| `qwen-vl-ocr-2025-11-20` | **¥0.0015** | **¥0.31** | ¥1.5 |
| 同上 + Batch File | ¥0.00075 | ¥0.15 | ¥0.75 |
| `qwen3-vl-plus`（≤32k 档） | ¥0.012 | ¥2.4 | ¥12 |
| **MiniMax-M3**（`detail=high`） | **¥0.047** | **¥9.4** | ¥47 |

三个决策：① **专用 OCR 比通用视觉便宜约 31 倍** → 「按页抠成本」不再是设计目标，路由服务于**正确性**；② 干净数字页上本地提取**无损**，路由判据 = 页面复杂度，**不是价格**；③ 成本大头会从「转写」转到「理解」：`qwen3-vl-plus` 描述图约 ¥0.004/张，一本教材 30–60 张有效图 ≈ **¥0.2**，合计 <¥0.6/本。**成本不是瓶颈，正确性与工程复杂度才是。**

---

## 四、方案设计

### 4.1 解析层去耦合分层与「新增格式」步骤

```
调用方（3 处，只认一个稳定契约）
   │   parse_document(filename, content) -> (text, ext)        ← 稳定门面，新增格式零改动
   ▼
parsers/__init__.py  ── 门面：注册内置解析器 + 能力探测 + 启用日志
   ▼
ParserRegistry  ── 路由：ext(小写无点) → BaseParser 实例；支持覆盖注册（同扩展名后注册者胜）
   ▼
BaseParser 子类（text / pdf / docx / pptx / image-ocr / legacy / book）
   │   meta 携带元信息（pages / slides / chapters / via / title…）
   ▼
依赖层：标准库 → 已装第三方库 → 外部工具（shutil.which 探测）
```

**新增格式只需 2 步**：

```
1) 新建 parsers/xxx.py：class XxxParser(BaseParser)（name / extensions；可选 classmethod installed() 做能力探测）
2) 在 parsers/__init__.py::_register_builtin 注册：target.register(XxxParser())（有依赖的加 if XxxParser.installed():）
```

**三条去耦合原则**：① **能力探测下沉到解析器自身**（`installed()` 由类自己回答「本机能不能干」→ 缺依赖 = 上传时立即提示，而非运行时 500）；② **跨格式复用走组合不走继承/复制**（`MobiParser` 解包出 EPUB 后直接复用 `EpubParser`）；③ **失败不抛异常，返回 `ParseResult(ok=False, error=…)`**，错误信息自带安装指引。

### 4.2 PDF 五层结构（L0–L4）

```
PDF bytes → L0 逐页探针（零成本、纯本地；每页出文本密度/乱码率/图占比/栏数/表格数）
                │ route
   L1 本地增强（局部提取） │ L2 云端专用 OCR（qwen-vl-ocr） │ L3 图/图表理解（qwen3-vl-plus）
                └──────────┴───────────┬───────────┘
   L4 交叉校验（文本层 vs 视觉结果）+ figure chunk
                └──► 结构化 Markdown → 结构化分块（按标题/页/元素类型）→ 向量 + whoosh
```

### 4.3 L0 逐页探针契约（P0 的全部内容，也是整套方案的地基）

每页计算（全部基于 PyMuPDF，零新依赖）：

| 指标 | 取法 | 用途 |
|---|---|---|
| `text_chars` | `len(page.get_text("text").strip())` | 文本层是否存在 |
| `garbage_ratio` | 私用区（U+E000–U+F8FF、U+FB00–U+FB4F 等）+ 稀有符号占比 | 检测公式/特殊字体乱码（缺陷 #5） |
| `image_area_ratio` | `Σ page.get_image_info()[i]["bbox"] 面积 / page.rect 面积` | 判定扫描页 / 图文页（缺陷 #3 前提） |
| `column_count` | `get_text("blocks")` 的 x 起点一维聚类（间隙 > 页面宽 8% 视为分栏） | 阅读顺序（缺陷 #6） |
| `table_count` | `len(page.find_tables().tables)` | 结构还原（缺陷 #4，PyMuPDF 自带） |
| `is_scanned` | `text_chars < 50 且 image_area_ratio > 0.6` | 扫描页判定（**逐页**，修缺陷 #1） |

路由决策（示例阈值，实施时用真实语料校准）：

| 条件 | 去向 |
|---|---|
| `text_chars ≥ 300` 且 `garbage_ratio < 1%` 且 `column_count == 1` 且 `image_area_ratio < 20%` | **L1 本地**（干净页，应占多数） |
| 其余（扫描 / 多栏 / 乱码 / 大图 / 有表格） | **L2 云端 OCR** |
| 页面含 `image_area_ratio > 3%` 的独立图元（排除整页扫描） | 额外走 **L3** |

### 4.4 L1 本地增强（不改依赖，只改用法）

针对「干净页」，把 `get_text("text")` 换成结构化提取：

- **`get_text("dict"/"rawdict")`** → 保留 block/span 的 bbox、字号、字体名 → **按字号聚类还原标题层级**（`#/##/###`），按 bbox 排序还原阅读顺序，去页眉页脚（检测「同一 y 带在多页重复出现」的文本）。
- **`page.find_tables()`** → 表格 → Markdown/HTML 结构（内建，**零新依赖**）。
- 输出统一为**结构化 Markdown**（见 §4.6），与 L2 产物同构 → 下游分块器只认一种格式。

### 4.5 L2 云端专用 OCR 与 L3 图表理解

**L2（主力：`qwen-vl-ocr-2025-11-20`）**：

- **渲染**：`page.get_pixmap(dpi=200)`（文本页可降至 150dpi 省 token）→ PNG → base64 Data URL。
- **调用路径（两阶段）**：**阶段一（推荐先做）** OpenAI 兼容 + 自定义 prompt（复用现有 client 与 `fallback`）；**阶段二（可选增强）** DashScope 原生 `ocr_options.task` 按元素分派（代价：新增 HTTP 路径、脱离 `chat_create`）。**不要一开始就上阶段二**。
- **批大小由输出上限倒推**：`max_tokens=8,192` → 单请求输出预算 ≈ 6,000 token ≈ 密排中文 4–6 页 → **文本页 3–4 页/批**；**含公式或表格的页 1 页/批**（LaTeX/HTML 体积是纯文本 3–5 倍，截断会毁整页）。若 `qwen3.5-ocr`（32,768 输出）确认可用，批可提至 8–12 页。
- **并发**：显式锁 `-2025-11-20`（6,000 RPM / 30M TPM）→ 并发 8–16 安全，遇 429 指数退避。**不要用动态版**。
- **`detail` 概念不存在**（那是 MiniMax 的参数）；改用 `min_pixels`/`max_pixels` 控分辨率与 token。

**L3（图与图表理解，当前完全缺失、性价比最高）**：

- **抽取**：`page.get_image_info()` 拿每个图元 bbox 与像素；过滤装饰性小图（面积 < 页面 3%）、跨页 hash 去重水印/logo。
- **caption 配对**：**用本地几何信息，不用 OCR 坐标** —— 在同页文本块中找与图 bbox 中心最近、且位于图下方/上方的块（「图 3-2 xxx」正则优先）。本地几何比云端坐标更精确且免费。
- **描述**：图元 → `qwen3-vl-plus`（¥1/¥10）+ prompt「描述这张图在讲什么，含坐标轴/图例/关键数值」。**L2 抄字，L3 理解。**
- **入库**：**独立 chunk**（`chunk_type="figure"`），内容 = `caption + 描述`，元信息带 `page`/`image_path`/`bbox`/`caption`（图与正文 embedding 分布差异大，混在一起会互相污染检索）。

### 4.6 统一输出契约（结构化 Markdown）

**L1 与 L2 必须产出同一种格式**，否则分块器要分叉：

```markdown
<<<PAGE 12>>>
## 2.3 二叉树的遍历

前序遍历的顺序是……

| 遍历方式 | 顺序 | 用途 |
|---|---|---|
| 前序 | 根-左-右 | 复制树 |

$$\text{Height}(T) = 1 + \max(h_L, h_R)$$

![图 2-4 二叉树遍历示意](figure:page12_img0)
图 2-4 二叉树遍历示意：三种遍历访问节点的先后顺序。
<<<PAGE 13>>>
```

要点：**页标记**（校验对齐、失败定位、增量重跑的最小单位）、**标题层级**（分块依据）、**`$...$`/`$$...$$` 公式**（可被检索）、**表格保留结构**、**图占位 + caption**（关联 figure chunk）。

### 4.7 结构化分块（否则前面全部白做）

**缺陷 #7 必须一起修**，否则结构信息在分块层被抹平：

- 按 `<<<PAGE n>>>` 与标题层级切分（而非字符窗）；超长小节再按段落滑窗。
- `heading` 取**真实标题**（层级路径，如 `2.3 二叉树的遍历`），不再取「首行前 30 字」。
- chunk 携带 `page` / `chunk_type`（text/table/formula/figure）/ `section_path` → 检索结果可溯源、可做同节扩展（与现有 parent-child 扩展协同）。
- 表格与公式**不拆散**（一个表格 = 一个 chunk，即使超长）。

### 4.8 解析链路的上下文约束

> **范围说明**：这**只覆盖解析链路自己**的上下文约束，不是「对话/Agent 侧上下文工程」——后者单独立项（见 `docs/上下文工程/`）。

| 约束 | 做法 |
|---|---|
| **单请求输入预算** | `max_pixels` 控分辨率：文本页 150dpi、复杂页 200dpi、图表页按原尺寸。拒绝「无脑塞 8192×8192」 |
| **单请求输出预算** | 由 `max_tokens` 倒推批大小（§4.5）；**宁可多请求，不可被截断**（`finish_reason == "length"` 判失败并重试） |
| **无状态化 + 页级缓存** | 每页独立请求 → 按 `(file_hash, page_no, engine, prompt_version)` 缓存；引擎/prompt 升级只重跑受影响页，**重跑不重复计费** |
| **提示词角色约束** | system 明确「你是文档转写器：只转写，不总结、不补全、不翻译、不解释；不可辨认处输出 `[?]`；保留页眉页脚但用标记包裹」。**防幻觉第一道闸门** |
| **失败降级链** | 单页失败 → 重试 2 次（退避）→ 降级到本地结果 → 标 `page_status="degraded"`；**不因单页失败丢整份文档** |
| **成本记账** | 每次调用记 `tokens / ¥ / engine / page`（`usage.prompt_tokens_details.image_tokens` 可取图 token）→ 落 `meta.cost` |
| **并发与背压** | 全局并发上限（默认 8）+ 令牌桶对齐 RPM/TPM；429 退避不重试风暴 |
| **增量与幂等** | 同一文件重复上传命中缓存直接复用；`file_hash` 变化才重跑 |

**工程前置（必做）**：① **解析异步任务化**（缺陷 #9）—— 复用 `core/collector/manager.py` 的任务模式（创建/进度/取消/统计），前端上传后显示进度；② **抽 `parsers/ocr.py` 能力层**（缺陷 #8）—— `pdf.py`/`image.py` 共用引擎接口，云引擎与 RapidOCR 都是该层实现，可插拔/可降级/可 A/B；③ **`parse_document` 契约升级**（缺陷 #2）—— `verbose=True` 返回完整 `ParseResult`（含 `meta`），旧签名不变。

---

## 五、分期实施

| 阶段 | 内容 | API 成本 | 触发条件 | 交付 |
|---|---|---|---|---|
| **P0** | 逐页探针（修 #1）+ `parsers/ocr.py` 能力层（#8/#11）+ `parse_document` 契约回传 meta（#2）+ 结构化分块与页标记（#7） | **¥0** | 立即 | 可在真实资料上量化「基线丢了多少内容」；可跑实验组 1 |
| **P1** | 异步任务化（#9）+ `qwen-vl-ocr-2025-11-20` 接入（OpenAI 兼容路径）+ 页级缓存 + 成本记账 + 交叉校验闸门（§6.4） | 一本 ≈ ¥0.3 | P0 验收 | 实验组 2；扫描/复杂页可用 |
| **P2** | 图表抽取 + caption 几何配对 + `qwen3-vl-plus` 描述 + figure chunk | 一本 ≈ ¥0.2 | P1 稳定 | 实验组 3；「识图」从零到有 |
| **P3** | DashScope 内置任务（`table_parsing`/`formula_recognition`）分派；表格结构增强；doc 级成本配额；`qwen3.5-ocr` 评估 | — | P2 数据显示表格/公式仍短板 | 结构精度提升 |

> **P1 的三道前置闸门（缺一不可）**：① P0 的探针与基线可用；② 后端能异步返回进度；③ ~~`DASHSCOPE_API_KEY` 欠费~~ → **已解除**（2026-09-26 用户确认充值；云 OCR 与嵌入同账号）。

---

## 六、验证

### 6.1 评测集（`data/` 无上传文件，必须自建）

自建 **30–50 页**标注集，覆盖 §3.2 的六类页面（A/B/C/D/E/F 各 5–8 页），人工产出 reference Markdown。**至少含 1 份真实扫描件、1 份双栏论文、1 份含跨页表格的教材章节、1 份公式密集页。**

### 6.2 指标

| 维度 | 指标 |
|---|---|
| 文本保真 | 归一化后字符错误率 CER |
| 表格 | cell 级还原率（含合并单元格）、跨页表格是否拼回 |
| 公式 | LaTeX 归一化后的 BLEU / 精确匹配率 |
| 阅读顺序 | 块序列与 reference 的 Kendall τ |
| 图表 | caption 配对准确率、描述人工可行性打分（1–5） |
| 端到端 | **同一批资料入库后 `/kb/search` 的 Recall@5 对照**（现状 vs 新方案）—— 最终唯一有意义的指标 |

### 6.3 对照实验

- 基线：现状（PyMuPDF 文本层 + 整份触发 RapidOCR）
- 实验组 1：仅 P0（逐页探针 + 结构化分块，不接云引擎）→ 在 C/D/E 类页面会显著暴露基线缺陷
- 实验组 2：P0 + L2（云 OCR）→ 主要救 C/D/B
- 实验组 3：P0 + L2 + L3（图表）→ 只在 E 类页面有增益，对「教材插图题」可能决定性

### 6.4 交叉校验闸门（防污染，**必备件**）

同页若存在可用文本层（探针 `text_chars ≥ 300` 且 `garbage_ratio < 1%`）→ 计算本地提取与视觉结果的**归一化字符相似度**：

| 相似度 | 处置 |
|---|---|
| ≥ 0.95 | 采用视觉结果 |
| 0.7–0.95 | 采用视觉结果，标 `needs_review`，记日志 |
| < 0.7 | **保留本地结果**，标 `needs_review`，**拒绝用视觉结果覆盖** |
| 无文本层（纯扫描） | 无校验基线 → 标 `unverified`，**抽样人工复核**（或二次模型交叉比对） |

**没有这一层，本方案会降低知识库的可信度。** 另有两条硬闸门：`finish_reason == "length"` → 判失败重试；输出长度 < 本地文本长度的 50% 且本地可用 → 拒绝覆盖。

---

## 七、风险与边界

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **幻觉污染知识库**（最严重） | §6.4 交叉校验闸门 + `needs_review` 标记 + 抽样人工 + 提示词角色约束 + 「拒绝覆盖」规则 |
| 2 | 单一供应商（DashScope） | MiniMax-M3 作降级备用引擎（已具视觉能力，复用 `llm/fallback.py`）；`VISION_PARSE_ENABLED` 开关一键回退纯本地 |
| 3 | 隐私/版权：教材内容上传第三方 | 需用户显式确认（**新引入**的性质——现状只有对话内容外发，解析是纯本地）。建议：默认开关 + 文档说明 + 单文件可选「仅本地解析」 |
| 4 | 成本失控 | 页数上限 + doc 级预算 + 逐次记账 + 默认并发上限；单价量级已确认（一本 <¥0.6），失控风险低 |
| 5 | 长文档超时 | P1 异步任务化（硬前置） |
| 6 | 输出被截断 | `finish_reason == "length"` 判失败；批大小由 `max_tokens` 倒推 |
| 7 | 版本漂移导致成本/限额突变 | **显式锁版本号**（`-2025-11-20`），不用动态版；版本写配置而非代码 |
| 8 | 探针阈值失准（误判路由） | 阈值用 §6.1 评测集校准；误判成本低（视觉与本地结果都保留，L4 兜） |

**明确不做（YAGNI 边界）**：本地 DocVLM 部署（无 GPU）；把 M3 作为解析主力（贵 31 倍且非 OCR 专用，保留为备用）；自建 PDF 渲染/版面检测模型；阿里云「文档智能」等垂直服务；CBZ/CBR 漫画图集逐页 OCR；引入 `unstructured`/`docling`/`markitdown` 解析全家桶；DRM 电子书与 CHM/LIT/PDB。

**待办与 YAGNI 边界（格式覆盖侧）**：P1 前端格式白名单改由后端提供（`GET /kb/formats`）；P2 解析契约升级为结构化（章节/页码/标题层级）；P2 OCR 能力抽成 `parsers/ocr.py`；P3 CBZ/CBR；P3 AZW3(KF8) 完整支持（`kindleunpack`）。

---

## 八、实施记录

### 8.1 电子书接入（2026-09-12，格式覆盖）

| 文件 | 改动 |
|---|---|
| `backend/app/core/kb/parsers/book.py` | **新增**：`EpubParser`（zipfile + OPF spine + XHTML→文本）、`Fb2Parser`、`MobiParser`（探测 mobi 库/Calibre）、`DjvuParser`（探测 `djvutxt`） |
| `backend/app/core/kb/parsers/__init__.py` | 注册电子书解析器（EPUB/FB2 常驻；Kindle/DJVU 探测注册） |
| `backend/app/core/kb/parsers/base.py` | 修 `_ok()`：`meta` 改显式形参（原先被 `**meta` 吞成 `{"meta": {...}}`，pdf/image 元信息全部丢失） |
| `backend/app/core/kb/parser.py` | 兼容薄封装的格式说明补电子书一行 |
| `backend/requirements.txt` | 电子书可选依赖注释（`mobi`/Calibre/djvulibre），**不新增硬依赖** |
| `frontend/src/components/KbPanel.vue` | 上传 `accept` 列表补 `.epub,.fb2,.mobi,.azw,.azw3,.djvu` |
| `backend/tests/test_book_parser.py` | **新增**：内存现造 EPUB 验证 spine 顺序/段落边界/脚本剔除/元信息/失败软化，FB2 正文与坏 XML，注册表路由 |

**实现要点（book.py）**：按 **spine（阅读顺序）**取章节（spine 缺失时退化为全部 HTML 项）；块级标签转段落边界（`</p>` → `\n\n`）而非空格；章节标题取 XHTML `<title>`，输出 `【第 N 章：标题】` 首行兼作分块 heading；书名/作者进 `meta` 且书名作正文首行；编码 `utf-8 → utf-8-sig → gbk → gb18030` 依次尝试，FB2 声明编码不符时去声明重解析。

**验证**：`test_book_parser.py` **6 passed**；全量 `pytest -m "not llm_api"` **543 passed, 7 deselected**。手工：上传 `.epub` 应能入库并在 `/kb/tree` 看到；带 DRM 的 `.azw3` 应收到「格式不支持/解析失败」友好提示而非 500。

### 8.2 视觉解析本地部分（P0 前半，2026-09-13）

> 范围：**不花一分钱、不引入新依赖**的那一半。云端 OCR（P1）与图表理解（P2）未动，§五 分期不变。

| 缺陷 | 处置 | 落点 |
|---|---|---|
| #1 扫描判定整份文档级（**静默丢内容**） | **已修**：判定下沉到页。每页出探针，只对扫描页 `(图占比 > 0.01 且 无文本，或 文本 < 50 且 图占比 > 0.6)` 走 OCR | `parsers/pdf.py`（`probe_pages` / `_probe_page`） |
| #2 解析契约被截断（meta 丢失） | **已修**：`parse_document(filename, content, verbose=True)` 回传完整 `ParseResult`，旧签名 `(text, ext)` 默认不变 | `parsers/__init__.py`；上传链路已消费 `pages/via/scanned_pages/ocr_pages/ocr_missing/garbage_ratio` |
| #8 OCR 能力横向耦合 | **已修**：抽 `parsers/ocr.py` 能力层（`have_ocr`/`ocr_lines`/`render_pdf_page`），pdf 与 image 共用 | `parsers/ocr.py`（新增）、`parsers/image.py`、`parsers/pdf.py` |
| #11 `parsers/ocr.py` 不存在 | **已建**（同上） | 同上 |
| #7 分块纯字符窗、heading 取首行 30 字 | **部分已修**：页标记 `<<<PAGE n>>>` 剥离并把**起始页**记入 `chunk["page"]`；Markdown 标题/电子书章节行成为**分块边界**，`heading` 取真实标题。**未做**：按页切分、`page`/`chunk_type`/`section_path` 落库（需 `doc_chunks` 与 whoosh schema 变更 + 重建索引） | `kb_manager.py::chunk_text` / `_iter_paragraphs`；页标记契约在 `parsers/base.py` |

**仍未做（顺序即建议顺序）**：#3 图表抽取 + caption 配对（P2）；#4 表格提取（`find_tables()` 只在跑探针，正文仍线性化）；#5 公式乱码（只有 `garbage_ratio` 检测，无修复）；#6 多栏阅读顺序重排（有 `column_count` 信号未重排）；#9 解析异步任务化（P1 硬前置）；#10 多模态基建（`call_llm` 固定 `max_tokens=2000`）。

**验证**：`test_pdf_parser.py` **11 passed**（新增，覆盖混合型 PDF 只对扫描页 OCR、纯文本不触发、OCR 不可用时其余页不丢、`MAX_OCR_PAGES` 预算、探针信号、`verbose` 契约）；`test_chunking.py` 新增页标记/标题边界/无结构回退用例；全量 `pytest -m "not llm_api"` **596 passed, 7 deselected**。

> ⚠️ **已入库的旧文件不会自动受益**：`heading`/`page` 是入库时计算的，需重新上传（或重建索引）才生效。

**编号对照（对被删原文件的 § 引用）**：本文 §2.2 = 原《视觉解析策略》§2.2（缺陷清单，`#1` 起同号）；本文 §2.3 = 原《文档解析模块》§2.2（耦合清单）；本文 §4.1/§4.6 = 原《文档解析模块》§4 / 原《视觉解析策略》§4.6（统一输出契约，`parsers/base.py`、`kb_manager.py` 引用此号）；本文 §8.1 = 原《文档解析模块》§五/§六；本文 §8.2 = 原《视觉解析策略》§十。

---

## 九、附录：关键数据来源

| 结论 | 来源 | 可信度 |
|---|---|---|
| M3 支持 `image_url`（base64/URL）、无 PDF content part、定价 | MiniMax 官方文档（OpenAI SDK 页 / 按量计费页） | 高（官方） |
| `qwen-vl-ocr` 能力/价格/限流/`max_tokens` 上限/像素换算 | 阿里云百炼官方文档（`qwenvl-ocr` 页 + Qwen-OCR API 参考，更新于 2026-09-10） | 高（官方） |
| `qwen3-vl-plus` 价格/上下文/结构化输出 | 阿里云百炼官方文档 | 高（官方） |
| 本地 DocVLM 精度/速度/显存 | YOMXXX 横评（2026-05-22，个人博客，含 affiliate，方法未公开） | **中低（仅作趋势参考）** |
| olmOCR-bench 分档与吞吐 | Datalab 自跑结果（经 MarkTechPost 报道，2026-07-25） | **中（厂商自评，可复现）** |
| 本机无 GPU | 本机 `nvidia-smi` 实测 | 高 |
| 代码缺陷定位 | 本项目源码逐行查证（附行号） | 高 |
| EPUB/FB2 解析选型、unstructured/MarkItDown 做法 | unstructured/partition/epub.py、MarkItDown 仓库 | 中（开源实现） |

> **所有单价均为文档原价，不含限时优惠；实施前需在控制台复核。**
