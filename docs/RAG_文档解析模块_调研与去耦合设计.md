# RAG 文档解析模块：调研与去耦合设计

> 创建：2026-09-12
> 背景：用户提出「RAG 模块新增文件解析（含电子书格式）+ 解析模块调研 + 去耦合设计」。
> 涉及代码：`backend/app/core/kb/parsers/`（本次新增 `book.py`）。
> 结论先行，选型对比与设计原则在后，末尾是改动清单与待办边界。

---

## 一、核心结论

1. **解析层的骨架已经是对的**：`parsers/` 是「注册表 + 策略」结构（`ParserRegistry` 按扩展名路由 → `BaseParser` 子类实现），新增格式 = 新增一个类 + 注册一行，3 个调用方（`kb_manager` 上传 / `collector` 抓取 / `rag_pipeline` 入库）零改动。**本次电子书接入没有改动任何调用方**，即是对该结构的最好验证。
2. **真正的耦合在外围三处**（不在 parsers 内部）：格式白名单在 3 个地方各写一份、解析结果契约在 `parse_document` 处被截断成纯文本（元信息/章节结构丢弃）、OCR 能力被 `pdf → image` 私有符号横向引用。见 §2.2。
3. **电子书支持用标准库就够了**：EPUB 本质是「zip + XHTML + OPF 清单」，FB2 本质是 XML，`zipfile` + `xml.etree` 即可，**零新依赖**。MOBI/AZW3/DJVU 依赖外部能力（mobi 库 / Calibre / djvulibre），沿用既有 `legacy.py` 的「能力探测，装则注册」策略。
4. **不引入 unstructured / docling / markitdown 等解析全家桶**：它们体积与依赖重（torch/onnx 系模型），能力重心在「版面还原、表格、公式、多模态」，与本项目已有栈（PyMuPDF + RapidOCR）重叠且收益不匹配成本。保持「按需引入单点库」的现状，不引系统级框架。

---

## 二、现状诊断

### 2.1 已支持格式与实现位置

| 格式 | 解析器 | 依赖 | 文件 |
|---|---|---|---|
| `.txt .md .log .json .csv .py .js` 等 30+ | `TextParser` | 标准库（多编码尝试） | `parsers/text.py` |
| `.pdf` | `PdfParser` | PyMuPDF；无文本层时 RapidOCR 逐页 OCR 回退 | `parsers/pdf.py` |
| `.docx` | `DocxParser` | python-docx（段落 + 表格） | `parsers/docx.py` |
| `.pptx` | `PptxParser` | python-pptx（文本框 + 表格 + 备注） | `parsers/pptx.py` |
| `.png .jpg .bmp .webp .tiff .gif` | `ImageOcrParser` | RapidOCR（可选依赖，装则注册） | `parsers/image.py` |
| `.doc .ppt .xls` | `LegacyParser` | textract / LibreOffice / antiword 等（探测） | `parsers/legacy.py` |
| **`.epub .fb2`（本次新增）** | `EpubParser` / `Fb2Parser` | **零依赖**（zipfile + ElementTree） | `parsers/book.py` |
| **`.mobi .azw .azw3`（本次新增）** | `MobiParser` | mobi 库或 Calibre（探测） | `parsers/book.py` |
| **`.djvu`（本次新增）** | `DjvuParser` | djvulibre 的 `djvutxt`（探测） | `parsers/book.py` |

调用链（新格式自动生效的原因）：

```
POST /kb/upload ─► kb_manager.upload_and_index ─┐
collector/manager.py 抓取入库 ─────────────────┼─► parse_document(filename, bytes)
rag_pipeline 上传入库 ──────────────────────────┘        │
                                        ParserRegistry（ext → parser）
                                                          │
                                        BaseParser.parse → ParseResult(text, meta)
```

`is_supported()` 在 `kb_manager` 上传前作为白名单闸门；解析文本 < `MIN_PARSE_TEXT_LEN=200` 视为图片型/不可解析并拒收。

### 2.2 耦合清单（按影响排序）

| # | 现象 | 位置 | 影响 | 处置 |
|---|---|---|---|---|
| 1 | 格式白名单写 3 份：前端 `acceptTypes`、`registry._parsers`、`kb_manager.IMAGE_EXTS` | `KbPanel.vue` / `parsers/` / `kb_manager.py` | 新增格式必须记得改前端，否则用户选不到文件（本次已手工同步） | 建议 P1：后端暴露 `GET /kb/formats`（直接复用 `supported_extensions()`），前端拉取 |
| 2 | 解析结果契约只有文本：`parse_document` 只回 `(text, ext)`，`ParseResult.meta` 在门面处被丢弃 | `parsers/__init__.py` | 页码/章节/作者/来源元信息下游拿不到；分块器只能用「首行前 30 字」当 heading，电子书章节层级丢失 | P2：给 `parse_document` 加可选 `verbose=True` 返回 `ParseResult`，chunking 消费 `meta["sections"]` |
| 3 | OCR 能力横向耦合：`pdf.py` 私有导入 `image.RapidOcrEngine` / `_have_rapidocr` | `parsers/pdf.py` → `parsers/image.py` | 换 OCR 引擎要改两个文件；跨模块引用私有符号（与 AGENTS.md §3.6 已收敛的同类问题同源） | P2：抽 `parsers/ocr.py` 作为能力层，pdf/image 共用 |
| 4 | `BaseParser._ok(meta=...)` 的关键字参数被 `**meta` 吞掉，实际产出 `meta={"meta": {...}}` | `parsers/base.py` | `PdfParser`/`ImageOcrParser` 的页数、OCR 引擎标记实际全部丢失（`meta["pages"]` 取不到） | **本次已修**（改为显式 `meta: Optional[dict]` 形参），pdf/image 的元信息顺带恢复 |
| 5 | 门槛值 `MIN_PARSE_TEXT_LEN=200` 对图册/漫画类电子书偏严 | `kb_manager.py` | 正常电子书无影响；纯图集会被拒 | 保持（有明确报错文案）；真出现需求再按格式分档 |

---

## 三、调研

### 3.1 电子书格式地形图

| 格式 | 容器/结构 | 常见度 | DRM | 解析难度 | 本项目方案 |
|---|---|---|---|---|---|
| **EPUB 2/3** | zip（`META-INF/container.xml` → OPF 清单 + spine + XHTML 章节） | 高（开放标准，微信读书/多看/Calibre 都能导出） | 无 | 低 | ✅ 标准库实现：`EpubParser` |
| **FB2** | 单文件 XML（`<body><section><p>`） | 中（俄语圈；国内少量自制） | 无 | 低 | ✅ 标准库实现：`Fb2Parser` |
| **MOBI / AZW / AZW3(KF8)** | PalmDOC 容器；KF8 为 epub-like 内部结构 | 高（Kindle 生态） | **常带 DRM** | 高 | ⚠️ 可选：`mobi` 库或 Calibre；DRM 文件明确不支持 |
| **DJVU** | 专有压缩 + 可选文字层 | 中（中文扫描书/古籍） | 无 | 中（需外部工具） | ⚠️ 可选：`djvutxt`（无文字层的扫描件仍需 OCR） |
| **PDF** | — | 高 | 可加密 | — | ✅ 既有（PyMuPDF + OCR 回退） |
| **CBZ / CBR** | zip / rar 图片集（漫画） | 中 | 无 | 低（复用 OCR 逐页） | ⛔ 暂不做（YAGNI，需要时约 15 行可接） |
| **CHM / LIT / PDB / AZW4(KFX)** | 各自专有 | 低 | 部分有 | 高 | ⛔ 不做 |

> DRM 立场：只解析**无 DRM** 的电子书；带 DRM 的会解析失败并回落到「不支持」提示。这是能力边界，也是合规边界。

### 3.2 库选型对比（为什么标准库）

| 方案 | 体积/依赖 | 覆盖 | 结论 |
|---|---|---|---|
| **标准库 `zipfile` + `xml.etree`** | 0 | EPUB（spine 顺序、章节标题、段落）、FB2 | ✅ **采用**：读场景够用，无维护风险 |
| `ebooklib` | 轻 | EPUB2/3 读写（官方描述 Kindle 支持仍在开发中） | ⛔ 不引入：只用它的读能力，而我们要自定义「按 spine + 段落边界」输出，收益 < 一个依赖 |
| `mobi`（PyPI） | 轻（纯 Python） | MOBI/AZW；**KF8/AZW3 支持有限**，维护停滞 | ⚠️ 作为可选探测项，失败即降级 Calibre |
| `kindleunpack` | 中 | AZW3/KF8 解包最完整 | ⛔ 暂不引入；若后续真需要，只替换 `MobiParser._via_mobi` 单点 |
| **Calibre `ebook-convert`** | 重（外部程序） | 全格式互转，事实标准 | ⚠️ 可选外部能力（探测 `ebook-convert`） |
| `pandoc` / `pypandoc` | 中（外部二进制） | EPUB→HTML/Markdown 质量好 | ⛔ 不引入：多一个外部二进制的部署成本，标准库已覆盖 |
| `unstructured` / `docling` / `markitdown` | 很重（torch/onnx/模型） | 版面、表格、公式、多模态 | ⛔ 不引入（见 §1.4）；`markitdown` 的「格式→转换器注册表」思路与本包同构，可作参考 |
| PDF 专项（PyMuPDF4LLM / Marker / MinerU / olmOCR） | 各自独立 | 版面还原、公式、表格 | 保持现状；未来若做 PDF 专项再单点评估（对比见 `docs/RAG_召回与重排优化调研.md`、`RAG_参考资料与学习路线.md`） |

### 3.3 业界做法印证

- **unstructured**：`partition_epub` 的实现是「先转成 HTML，再走 `partition_html`」——与我们的判断一致：EPUB 的正文就是 XHTML，**不需要为 EPUB 单独引入库**。（来源：[unstructured/partition/epub.py](https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/epub.py)）
- **MarkItDown（微软）**：轻量统一入口，每个格式一个转换器 + 统一输出，与本 `parsers/` 的注册表结构同构，可作为「不重造轮子」的对照。
- **RAG 解析层的通用分层**：`解析 → 结构化 blocks → 分块 → 索引`。我们的现状是「解析 → 纯文本 → 分块」，**契约在纯文本处截断**（耦合 #2），这是后续提升检索质量的第一顺位改造点。

### 3.4 本项目的取舍理由（为什么「够用即止」）

1. 教材/教辅场景的主力是 PDF 与 EPUB，这两者已覆盖绝大多数需求。
2. 依赖成本必须与收益匹配：一个只为一小撮用户服务的格式，不值得进 `requirements.txt` 硬依赖；因此 MOBI/DJVU 走**能力探测**（与 `legacy.py` 一致），`install.ps1` / Docker 无需任何改动。
3. 解析质量的下一个瓶颈**不是格式覆盖率**（PDF/EPUB 已覆盖），而是**结构信息丢失**（耦合 #2）与版面识别，把预算投在那里收益更高。

---

## 四、去耦合设计

### 4.1 分层结构

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

### 4.2 新增一个格式的步骤（已简化为 2 步）

```python
# 1. 新建解析器类（parsers/xxx.py）
class XxxParser(BaseParser):
    name = "xxx"
    extensions = {"xxx"}
    @classmethod
    def installed(cls) -> bool:      # 可选：有外部依赖时做能力探测
        return _have_module("xxxlib") or _have_tool("xxx-cli")
    def parse(self, filename: str, content: bytes) -> ParseResult:
        ...

# 2. 在 parsers/__init__.py::_register_builtin 注册（有依赖的加 if XxxParser.installed()）
target.register(XxxParser())
```

本次电子书接入的完整注册代码（`parsers/__init__.py`）：

```python
target.register(EpubParser())
target.register(Fb2Parser())
if MobiParser.installed():
    target.register(MobiParser())
if DjvuParser.installed():
    target.register(DjvuParser())
```

### 4.3 三条去耦合原则（本次实现即范例）

| 原则 | 体现 |
|---|---|
| **能力探测下沉到解析器自身** | `MobiParser.installed()` / `DjvuParser.installed()` 由类自己回答「本机能不能干这件事」，门面只做「装了就注册」的决策 → 缺依赖 = 该格式不支持（上传时立即提示），而不是运行时 500 |
| **跨格式复用走组合，不走继承/复制** | `MobiParser` 解包出 EPUB 后**直接复用 `EpubParser`**，不复制 XHTML→文本逻辑；XHTML→文本只有 `book._xhtml_to_text` 一份 |
| **失败不抛异常，返回 `ParseResult(ok=False, error=…)`** | 保持既有约定（`registry.parse` 兜底捕获一切异常），错误信息自带安装指引（如「未检测到 mobi 库或 Calibre（ebook-convert）」），前端可直接展示给用户 |

### 4.4 待办与 YAGNI 边界

| 优先级 | 事项 | 触发条件 |
|---|---|---|
| P1 | 前端格式白名单改由后端提供（`GET /kb/formats` 复用 `supported_extensions()`） | 格式继续增加、或再出现「后端支持但前端选不到」 |
| P2 | 解析契约升级为结构化（章节/页码/标题层级），分块器用真实 heading | 检索质量实测显示章节语义混杂时（电子书章节已用 `【第 N 章：标题】` 兜底） |
| P2 | OCR 能力抽成 `parsers/ocr.py`，pdf/image 共用 | 换 OCR 引擎或加表格/版面识别时 |
| P3 | CBZ/CBR（漫画/图集，复用 `ImageOcrParser` 逐页 OCR） | 有实际资料需求 |
| P3 | AZW3(KF8) 完整支持（把 `mobi` 库换成 `kindleunpack` 实现） | 有 Kindle 用户反馈解析失败 |
| — | DRM 电子书、CHM/LIT/PDB | **不做**（合规与收益双重理由） |

---

## 五、本次改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/core/kb/parsers/book.py` | **新增**：`EpubParser`（zipfile + OPF spine + XHTML→文本）、`Fb2Parser`、`MobiParser`（探测 mobi 库/Calibre）、`DjvuParser`（探测 djvutxt） |
| `backend/app/core/kb/parsers/__init__.py` | 注册电子书解析器（EPUB/FB2 常驻；Kindle/DJVU 探测注册）+ 包 docstring 更新 |
| `backend/app/core/kb/parsers/base.py` | 修 `_ok()`：`meta` 改为显式形参（原先 `meta={...}` 被 `**meta` 吞成 `{"meta": {...}}`，pdf/image 元信息全部丢失） |
| `backend/app/core/kb/parser.py` | 兼容薄封装的格式说明补电子书一行 |
| `backend/requirements.txt` | 电子书可选依赖注释（`mobi` / Calibre / djvulibre），**不新增硬依赖** |
| `frontend/src/components/KbPanel.vue` | 上传 `accept` 列表补 `.epub,.fb2,.mobi,.azw,.azw3,.djvu` |
| `backend/tests/test_book_parser.py` | **新增**：内存现造 EPUB 验证 spine 顺序/段落边界/脚本剔除/元信息/失败软化，FB2 正文与坏 XML，注册表路由 |

### 实现要点（book.py）

- **按 spine（阅读顺序）取章节**，而非 zip 内存储顺序；spine 缺失时退化为清单内全部 HTML 项（兼容非法但常见的 EPUB）。
- **块级标签转段落边界**（`</p>` → `\n\n`）而非空格：`chunk_text` 按空行切段，段落结构直接影响分块质量（这是不复用 `web_tool._html_to_text` 的原因，后者会把全文压成一行）。
- 章节标题取 XHTML `<title>`，输出 `【第 N 章：标题】` 首行，兼作分块 heading；书名/作者进 `meta`，书名同时作为正文首行（否则分块后无法追溯出处）。
- 编码：`utf-8 → utf-8-sig → gbk → gb18030` 依次尝试；FB2 声明编码与实际字节不符时去声明重解析。

---

## 六、验证

```bash
# 新增用例
backend/venv/Scripts/python.exe -m pytest backend/tests/test_book_parser.py -q      # 6 passed
# 全量回归（无 LLM 真实调用）
backend/venv/Scripts/python.exe -m pytest backend/tests -q -m "not llm_api"          # 543 passed, 7 deselected
```

手工验收：在知识库面板上传一本 `.epub` → 应能入库并在 `/kb/tree` 看到；用 `/kb/search` 或对话检索应命中其中的句子；上传带 DRM 的 `.azw3` 应收到「格式不支持/解析失败」的友好提示而非 500。
