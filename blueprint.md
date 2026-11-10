# Global Context Map & Architecture Blueprint (`blueprint.md`)

## 1. System Overview & Constraints

- **Core Purpose**: Offline-first medical MCQ, spaced repetition, RAG search & local AI synthesis platform.
- **Runtime Environment**: Python 3.10+ async backend (FastAPI/Uvicorn), SQLite (WAL mode), pywebview (Windows Edge Chromium) or browser client, Vanilla HTML5/JS/CSS frontend.
- **Tech Stack**:
  - *Backend*: FastAPI, Uvicorn, SQLite3, Tantivy (BM25 lexical index), LanceDB (CPU vector + FTS hybrid search), ONNX Runtime (SapBERT 768d / MiniLM 384d), FlashRank (ms-marco-TinyBERT-L-2-v2 / ms-marco-MiniLM-L-6-v2 cross-encoder reranking), SciSpaCy (UMLS/MeSH ontologies & SciPy sparse TF-IDF matcher), Docling v2 & Docling v3 (visual layout, booktabs reconstruction, OCR line-crop re-reading & PP-OCR ONNX layout parser), Datalab Indra OCR ONNX (text/math/scanned PDF parser), & PyMuPDF / AdaptivePdfLayoutEngine (PDF text extraction), LibZim (ZIM reader), Tiktoken.
  - *Frontend*: Vanilla HTML5/JS (ES6+), CSS3 with glassmorphism theme CSS variables (`ui/css/main.css`), Marked.js, KaTeX, DOMPurify, interact.js. Local offline vendor fallback assets automatically checked and cached under `ui/vendor/`. High-frequency actions use segmented button groups (`.segmented-group` / `.segmented-btn` with `role="radiogroup"` / `role="radio"`) with two-way binding to underlying `<select>` elements.
  - *External Readers / Bridges*: SumatraPDF (`-reuse-instance`), Obsidian URI protocol (`obsidian://`), LM Studio API (`http://127.0.0.1:1234/v1`), In-app ZIM Document Reader modal (`#zim-viewer-container`, 1200px max-width, translucent backdrop filter, `body.zim-reader-open` viewport isolation, backdrop click & Escape dismissal).
- **Clinical Spaced Repetition Engine**:
  - FSRS v4.5 scheduling engine with immediate repetition for lapses: rating 'Again' (`grade == 1`) enforces `interval_days = 0`, `interval_seconds = 0`, and `due_timestamp = current_ts`, ensuring wrong questions immediately queue for the next quiz set.
  - Due review & wrong answers consolidation: Overdue reviews and wrongly answered questions are unified immediately into a single Knowledge Hub session (`sess_spaced_repetition_due` titled `🧠 Due & Wrong Review Set`) via `sync_consolidated_due_session`, with real-time Dashboard alert notifications and question set curation prioritizing unattempted questions before falling back to attempted questions.
- **Hardware Budgets & Execution Constraints**:
  - *VRAM Budget & GPU Policy*:
    - Default Baseline: Zero VRAM for routine backend processes (`CUDA_VISIBLE_DEVICES=""`), reserving 100% VRAM (4.2–4.8 GB on 6 GB GPUs) for local LM Studio GGUF models.
    - Selective & Opportunistic GPU Acceleration: Workloads exhibiting substantial throughput improvements (>=8x-15x speedup) may selectively utilize GPU execution if and only if explicit criteria are met:
      1. *High-Yield Workloads*: Indra OCR line recognition and reading order (`rec.onnx`, `order.onnx`), Docling Heron visual layout inference, and heavy offline batch embeddings during large PDF vault indexing.
      2. *Memory Arena Capping*: When GPU execution is active, ONNX Runtime `CUDAExecutionProvider` or PyTorch CUDA memory allocator MUST be strictly bounded (e.g., `gpu_mem_limit <= 1.2 GB` or `max_split_size_mb`) to prevent memory starvation of LM Studio.
      3. *Opportunistic Fallback*: On GPU OOM, CUDA initialization failure, or concurrent LM Studio generation, operations must gracefully fall back to CPU execution without throwing unhandled exceptions.
      4. *Configuration & Toggle*: Controlled via environment variable (`ENABLE_BACKEND_GPU=0|1`) or `config.py` setting, defaulting to `0` (CPU-only) to protect baseline user hardware.
  - *CPU Execution Time*: Multi-stage hybrid search pipeline < 250ms on CPU (enforced via cached singletons for embedder, reranker, and LanceDB).
  - *RAM Allocation*: Tantivy < 250 MB; LanceDB + CPUEmbedding < 500 MB; Primary SQLite MMAP cap = 512 MB; Ontology KB SQLite MMAP cap = 256 MB; SciSpaCy NLP + Linker < 500 MB; Docling CPU Converter + FlashRank + LRU Caches < 1.0 GB; **total Python process < 3.5 GB** (operating within ~5 GB available system RAM).
  - *LLM Context Limit*: Hard budget capped at 4096 tokens per request via `tiktoken` truncation.

---

## 2. Directory & Module Registry

```
.
├── config.py                          # Global paths, environment overrides, server/window config, model catalogs, and atomic vendor cacher.
├── run.py                             # Application entry point; sets environment, downloads vendor assets, boots server & browser.
├── requirements.txt                   # Production and testing Python dependency specifications (restricts numpy<2.0.0, pyarrow<17.0.0).
├── Ignore_list.txt                    # Medical stopwords, noise phrases, and ignored tokens synchronized with SQLite ignored_topics.
├── agents/
│   ├── __init__.py                    # Central package re-exporting auditor and topic_extractor from agents.tema_q.
│   └── tema_q/
│       ├── __init__.py                # Tema-Q package marker.
│       ├── topic_extractor.py         # MedSpaCy entity extraction, layperson synonym mapping, CUI binding, ConText negation, and sibling filtering.
│       ├── subtopic_router.py         # Question-anchored clinical prose sanitizer (stripping tables/captions/images) and 7-domain subtopic router.
│       ├── json_utils.py              # Resilient LLM JSON repair, bracket balancer, <think> block stripper, and Pydantic schema validation.
│       ├── pipeline.py                # 2-pass iterative LLM synthesis coordinator with token budgeting, VLM page notes, and LM Studio integration.
│       ├── auditor.py                 # Primary clinical discrepancy, staging variance detection, word budget enforcement, and JSON node merger.
│       ├── session_processor.py       # Async session processor for background RAG, multi-article context budgeting (500w/1000w), and task execution.
│       ├── prompts.py                 # Strict token-budgeted medical RAG prompts, multi-article synthesis templates, and stop-token definitions.
│       └── telemetry.py               # Full-pipeline diagnostic telemetry, HeadingDispatchTrace, PipelineDiagnosticTrace, and automated anomaly detection.
├── core/
│   ├── database.py                    # SQLite schema initialization, WAL/MMAP PRAGMAs, question_metadata tracking, and atomic transaction helpers.
│   ├── model_lifecycle.py             # LazyModelHolder for low-RAM lazy loading and idle memory eviction of heavy models.
│   ├── queue_manager.py               # Asynchronous priority task queue worker (P1/P2/P3), persistent job recovery, and WebSocket broadcaster.
│   ├── scheduler.py                   # FSRS v4.5 spaced repetition scheduling algorithm, retrievability decay, and dynamic retention tuning.
│   ├── topic_filter.py                # Ignore_list.txt stopword & token validation filter (is_valid_topic, filter_topics).
│   └── reporter.py                    # Jinja2 HTML dossier report compiler for clinical topic syntheses with path traversal and XSS guards.
├── parsers/
│   ├── embedding_engine.py            # CPUEmbeddingEngine running ONNX SapBERT/MiniLM (384/768d) lazily on CPU (optional GPU) with active switching.
│   ├── lancedb_engine.py              # LanceDB hybrid vector storage and FTS engine for hierarchical document chunks with SQL fallback.
│   ├── tantivy_engine.py              # Low-latency BM25 lexical inverted index builder, stale .lock cleanup, and depth-scored multi-depth searcher.
│   ├── heading_toc_calibrator.py      # Shared multi-pass H1-H6 heading hierarchy, 9D visual vectors, and lazy/streaming TOC calibrator.
│   ├── pdf_topic_indexer.py           # Full-PDF hierarchical topic indexer (pdf_topic_index), sibling-safe page resolver, and pypdfium2 VLM renderer.
│   ├── docling/                       # Modularized Docling layout engine and vision processing subpackage (replaces legacy docling_parser.py).
│   │   ├── __init__.py                # Package facade exporting cleaner, converter, engine, geometry, inspector, and models.
│   │   ├── models.py                  # Docling model weight paths, integrity checks, and offline verification.
│   │   ├── converter.py               # DocumentConverter singleton factory and multi-engine OCR routing (auto, rapidocr, easyocr).
│   │   ├── cleaner.py                 # Medical text sanitization, dehyphenation, drop-cap joining, OCR token repair heuristics.
│   │   ├── geometry.py                # Bounding box math, overlap/containment tests, and color distance algorithms.
│   │   ├── inspector.py               # Pixel-level visual analyzer, stroke-density profiling, line-crop re-OCR (RapidOCR/EasyOCR).
│   │   └── engine.py                  # AdaptivePdfLayoutEngine 6-stage layout extraction pipeline with LanceDB ingestion export.
│   ├── indra/                         # Modularized Datalab Indra OCR subpackage (replaces legacy indra_parser.py).
│   │   ├── __init__.py                # Package facade exporting profiles, types, rules, strategies, engine, and refiner.
│   │   ├── types.py                   # Type definitions (BBox, RGB, PageElement, PageElements, TableGrid, ThreadSafeLRUCache) with achromatic -1.0 hue sentinels.
│   │   ├── profiles.py                # Document layout profiles (NativeDigital, Textbook, Booklet, Article), 7-factor heading weights, and asymmetric gutters.
│   │   ├── rules.py                   # Granular 12-family prefix catalog, SharedLineProcessor (clean, classify, enrich), icon bullet & TOC dot-leader filters, deskew math.
│   │   ├── strategies.py              # Layout strategy hierarchy, vertical wrapped element merging (_merge_wrapped_elements), acronym tail recovery, and subheadings.
│   │   ├── engine.py                  # IndraModularEngine, FallbackChain (ONNX, pdfplumber, pypdfium2), 2D table grid builder, chromatic pill banner recovery.
│   │   ├── document.py                # Dual-mode lazy & streaming TOC tracking (DocumentHeadingTracker) with v37 disk caching and cross-page hierarchy compilation.
│   │   ├── heading_calibrator.py      # Multi-step layout box and heading calibrator with hard exclusions, HSV color hues, sibling outline consistency, and consensus scoring.
│   │   └── refiner.py                 # IndraHybridLayoutRefiner and interactive SVG bounding box overlay generator with deduplicated heading emission.
│   ├── pdf_extractor.py               # Lightweight PyMuPDF fallback fast text slicer with page heading detection and layout reconstruction.
│   ├── obsidian_reader.py             # Obsidian markdown frontmatter stripper, wikilink cleaner, and section reader.
│   ├── zim_reader.py                  # LibZim C-bindings reader for offline Wikipedia/medical .zim archives, asset extraction, and scoped HTML styling.
│   ├── reranker.py                    # FlashRank (ms-marco-TinyBERT-L-2-v2) cross-encoder reranker with active model switching and RRF merging.
│   ├── scispacy_linker_engine.py      # SciSpaCy NLP pipeline loader, covering index SQLite pool, SciPy sparse TF-IDF matcher & batched linker.
│   └── schema.py                      # Canonical AST schema (BoundingBox, ParsedBlock, HeadingNode, DocumentSection, ParsedDocument, clamp_heading_levels).
├── server/
│   ├── app.py                         # FastAPI app initialization, startup maintenance, stale Tantivy lock clearing, GZip, WS endpoints, and static mounts.
│   ├── ws_manager.py                  # Global WebSocket connection manager for live task/indexing progress with concurrent broadcast and dead-socket pruning.
│   ├── routes_analysis.py             # Interactive PDF visual layout analysis, bbox editing, heading tree, and LanceDB finalization.
│   ├── routes_csv.py                  # Endpoints for CSV topic listing, question retrieval, filtering, FSRS summary, and background CSV ingestion.
│   ├── routes_mcq.py                  # MCQ topic extraction, scoring, synthesis lookup, batch update triggers, and atomic grading/FSRS calculation.
│   ├── routes_models.py               # Hardware-constrained model manager, active model switcher, diagnostic endpoints, API key masking, & linker index builder.
│   ├── routes_notes.py                # External app bridges (SumatraPDF launch, Obsidian native URI, ZIM asset/article proxy).
│   ├── routes_queue.py                # RAG task queue state management, topic ignoring, priority overrides, and queue clear/retry.
│   ├── routes_search.py               # Multi-source hybrid search API (LanceDB + Tantivy + ZIM + SciSpaCy) with timeout-tolerant parallel execution.
│   ├── routes_sessions.py             # Quiz session persistence, answer recording, session history, due repetition consolidation, due summary, and dashboard prompts.
│   ├── routes_sources.py              # Source management (PDF, Obsidian, ZIM), wallpaper listing, safe path validation, chunked upload streaming, and background indexer.
│   └── routes_state.py                # Persistent state GET/PUT storage, RFC 7396 recursive merge patch, and vendor asset check.
├── docs/
│   ├── idea.md                        # Architecture concepts, pipeline designs, and feature roadmaps.
│   ├── mcq_blueprint.md               # Detailed MCQ test runner and spaced repetition technical specifications.
│   └── verification_report.md         # Verification report for 3-article subtopic synthesis and audit pipeline.
└── ui/
    ├── index.html                     # Main SPA HTML shell with responsive subviews and modal containers (non-blocking deferred scripts).
    ├── css/
    │   ├── main.css                   # Glassmorphism UI tokens, theme CSS variables, grid styles, MediaWiki overrides, KaTeX fonts, and utility classes.
    │   └── animations.css             # Keyframe animations for transitions, loaders, and badges.
    ├── vendor/                        # Offline vendor assets (tailwind.js, marked.min.js, purify.min.js, katex.min.js, auto-render.min.js, interact.min.js).
    └── js/
        ├── app.js                     # Root UI navigation, tab switching, dynamic palette extraction, citation delegation, and safe markdown rendering.
        ├── state.js                   # UI persistence sync layer (AppPersistence) interfacing with /api/state/ with 300ms debounce and localStorage backup.
        ├── quiz.js                    # Quiz runner, MCQ presentation, option handling, FSRS ratings, hotkeys 1-4/A-D, and inspector integration.
        ├── hub.js                     # Knowledge Hub session list, topic cards, synthesis carousel, and status indicators.
        ├── search.js                  # Search view controller, race-safe search with AbortController, inspector filters, and citation launcher.
        ├── queue.js                   # Live task queue status table and controls.
        ├── vault.js                   # Source management panel, index trigger, and progress bar binder.
        ├── dashboard.js               # Performance dashboard, dynamic prompt banner carousel (weak topics, subject rotation, motivation), and rank simulation.
        ├── expertise.js               # Clinical domain mastery breakdown and radar/progress UI.
        ├── habits.js                  # Habit streak tracker, custom daily target goals, and activity heatmaps.
        ├── settings.js                # System settings, active model switching, prefetch downloader, model diagnostics, SciSpaCy manager, and state export/import.
        ├── test_features.js           # Interactive feature test bench (PDF, DB search, models, KaTeX, pipeline runner).
        ├── analysis.js                # Visual PDF layout analysis workspace, bbox drag/resize (interact.js), category tagger, auto-save.
        ├── analysis_toc.js            # Table of Contents hierarchical validation workspace, heading tree, tier shifting (H1-H6).
        ├── reader.js                  # Reader view initialization wrapper, in-app document viewer, and modal controller.
        └── notes.js                   # 'My Notes' Lecture Topic Pad controller, wallpaper-derived card shades, pagination, topic line-locking, and inline editing.
```

---

## 3. Public Interfaces & Signatures

### Root Modules
- `config.py`:
  - Paths: `BASE_DIR: Path`, `DATA_DIR: Path`, `CSV_DIR: Path`, `MCQS_DIR: Path`, `VAULTS_DIR: Path`, `PDFS_DIR: Path`, `ZIM_DIR: Path`, `MODELS_DIR: Path`, `HF_CACHE_DIR: Path`, `FLASHRANK_CACHE_DIR: Path`, `SCISPACY_MODELS_DIR: Path`, `LINKERS_DIR: Path`, `KBS_DIR: Path`, `ONTOLOGY_DB_PATH: Path`, `DB_PATH: Path`, `INDEX_PATH: Path`, `LANCEDB_PATH: Path`, `STORAGE_DIR: Path`, `REPORTS_DIR: Path`, `HIGHLIGHTED_DIR: Path`, `RESULTS_DIR: Path`, `UI_DIR: Path`, `VENDOR_DIR: Path`
  - Aliases: `TANTIVY_DIR = INDEX_PATH`, `LANCEDB_DIR = LANCEDB_PATH`, `DEFAULT_PORT = SERVER_PORT`, `LM_STUDIO_BASE_URL = LM_STUDIO_URL`
  - Server & App: `SERVER_HOST: str`, `SERVER_PORT: int`, `APP_TITLE: str`, `WINDOW_WIDTH: int`, `WINDOW_HEIGHT: int`
  - LLM & Models: `LM_STUDIO_URL: str`, `LM_STUDIO_TIMEOUT: float`, `DEFAULT_MODEL: str`, `DEFAULT_EMBEDDING_MODEL: str`, `DEFAULT_FLASHRANK_MODEL: str`, `MAX_CONTEXT_TOKENS: int = 4096`, `SCISPACY_MODEL_CATALOG: dict`
  - Functions: `ensure_directories() -> None`, `ensure_offline_vendor_assets() -> None`
- `run.py`: `open_browser(url: str) -> None`, `main() -> None`
- `Ignore_list.txt`: Static stopword and noise token repository loaded by `core.topic_filter`.

### Agents Modules (`agents/`)
- `agents.__init__`: Central package re-exporting `detect_clinical_variances`, `merge_json_nodes`, `filter_novel_secondary_sentences`, `audit_synthesized_note`, `extract_topics`, `extract_topics_local`, `filter_sibling_topics`, `get_topic_aliases`, `generate_candidate_variants`, `canonicalize_clinical_phrase`, `extract_clinical_modifiers`, `extract_all_clinical_modifiers`, `get_ignored_topics_set`, `add_ignored_topic`, `remove_ignored_topic`, `normalize_topic_key`, `resolve_zim_topic`, `get_medspacy_model` from `agents.tema_q`.
- `agents.tema_q.topic_extractor`:
  - `extract_topics(text: str, explanation: str = "", strategy: str = "medspacy", use_zim_refinement: bool = False, use_tantivy_refinement: bool = False, **kwargs) -> list[str]`
  - `clean_html_prose(text: str) -> str`: Robustly strips HTML/XML tags and converts inline/block elements into clear whitespace boundaries, preventing word concatenation across tags and filtering malformed HTML fragments.
  - `extract_topics_local(question: str = "", options: Optional[Any] = None, explanation: str = "", strategy: str = "medspacy", use_zim_refinement: bool = False, use_tantivy_refinement: bool = False, text: Optional[str] = None, **kwargs) -> dict[str, Any]`: Uses MedSpaCy ConText negation and clinical target rules to bind CUIs and semantic categories directly, bypassing brittle subclause splitting to prevent false-positive clause negation (e.g. "dyspnea but no asthma"); uses thread-local SQLite pooling (`_get_thread_db_connection()`) and LRU caching (`maxsize=16384`).
  - `filter_sibling_topics(query: str, chunks: list[dict]) -> list[dict]`
  - `get_topic_aliases(term: str, max_aliases: int = 20) -> list[str]`
  - `generate_candidate_variants(term: str) -> list[str]`
  - `canonicalize_clinical_phrase(raw_phrase: str) -> tuple[str, str]`
  - `extract_clinical_modifiers(text: str) -> dict[str, str]`
  - `extract_all_clinical_modifiers(text: str) -> dict[str, set[str]]`
  - `get_ignored_topics_set(force_refresh: bool = False) -> set[str]`
  - `add_ignored_topic(term: str) -> bool`
  - `remove_ignored_topic(term: str) -> bool`
  - `normalize_topic_key(text: str) -> str`
  - `resolve_zim_topic(term: str) -> dict[str, Any]`
  - `get_medspacy_model() -> Any`
- `agents.tema_q.pipeline`:
  - `get_http_client() -> httpx.AsyncClient`
  - `close_http_client() -> None`
  - `parse_llm_json(response_str: str) -> Union[dict, list]`
  - `count_tokens(text: str) -> int`
  - `truncate_to_token_limit(text: str, max_tokens: int) -> str`
  - `call_llm_async(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str`
  - `resolve_documents(search_results: dict) -> list[dict[str, Any]]`
  - `resolve_primary_and_secondary_docs(search_results: dict) -> tuple[str, str]`
  - `extract_topics_from_mcq(question: str, options: dict, explanation: str = "", skip_lm: bool = True, extractor_strategy: str = "medspacy") -> dict`
  - `generate_mcq_explanation(question_data: dict[str, Any]) -> str`
  - `run_tema_q_synthesis(topic: str, search_results: dict, mcq_context: Optional[dict] = None, skip_lm: bool = False) -> dict`
- `agents.tema_q.auditor`:
  - `detect_clinical_variances(base_text: str, secondary_text: str) -> str`: Fast heuristic keyword, clinical staging, and dosage mismatch detector across passages; returns formatted Markdown variance callouts.
  - `merge_json_nodes(base_json: Optional[dict], second_json: Optional[dict], metadata: Optional[dict] = None) -> dict`: Deterministic Python subtopic node merger; combines clinical points using token Jaccard similarity ($\ge 0.75$) deduplication, attributes source/page metadata, merges conditional contexts, and attaches variance audits.
- `agents.tema_q.session_processor`:
  - `process_session_questions(session_id: str, worker: Optional[Any] = None) -> None`
  - `process_session_llm_tasks(session_id: str, worker: Optional[Any] = None) -> None`
- `agents.tema_q.subtopic_router`:
  - `clean_prose_for_synthesis(text: str) -> str`: Strips Markdown/HTML tables, image links, embeds, figure captions, and visual clutter to produce clean clinical prose.
  - `sanitize_clinical_prose(text: str) -> str`: Pre-processes text by converting HTML headings to markdown, safely stripping HTML tags while preserving inequalities, and eliminating figures and visual clutter without discarding terminal clinical statements.
  - `split_clinical_sentences(text: str) -> list[str]`: Protects Latin/medical abbreviations (`e.g.`, `i.e.`, `vs.`, `dr.`, `prof.`, `st.`, `fig.`, `figs.`, `tab.`, `tabs.`, `sp.`, `ssp.`, `et al.`, `i.v.`, `i.m.`, `p.o.`, `s.c.`, `b.i.d.`, `t.i.d.`, `q.i.d.`, `q.d.`, `q.h.s.`, `p.r.n.`, `c.f.`) from false terminal sentence splits.
  - `route_paragraphs_to_subtopics(paragraphs: list[str], domain: str) -> dict[str, list[str]]`: Routes prose paragraphs across 7 domain-specific subtopic schemas (`DOMAIN_SUBTOPIC_SCHEMAS`) via lexical keyword and ONNX embedding scoring.
  - `normalize_subtopic_stem(term: str) -> str`: Normalizes subtopic stems and plural forms (`-oses` -> `-osis`, `-ies` -> `-y`, `-es`, `-s`).
- `agents.tema_q.json_utils`:
  - `parse_llm_json(raw: str) -> dict[str, Any] | list[Any]`: Resilient JSON repair engine; strips `<think>` reasoning and markdown fences, fixes trailing commas, balances brackets via LIFO lexical scanner (`_repair_truncated_json_lifo`), and parses arbitrary LLM outputs safely.
  - `extract_and_repair_json(raw_text: str) -> tuple[str, bool]`: Extracts JSON snippets bounded by delimiters and invokes lexical repair on truncated or unbalanced cutoffs.
  - `_repair_truncated_json_lifo(json_str: str) -> str`: Single-pass quote- and escape-aware lexical state machine maintaining a LIFO delimiter stack to repair truncated JSON structures, dangling key-value cut points (`: null`), and trailing commas.
  - `parse_llm_pydantic(raw: str, schema_cls: type[T]) -> T | None`: Repairs JSON and validates schema via Pydantic (`ExtractedTopicsPayload`, `SubtopicCluster`, `SubtopicRoutePayload`, `SynthesizedNotePayload`, `MCQItemPayload`, `AuditVerdictPayload`).
- `agents.tema_q.prompts`: `STOP_TOKENS: list[str]`, `PASS1_CLINICAL_SYSTEM_PROMPT: str`, `PASS2_AUDIT_SYSTEM_PROMPT: str`, `DISTRACTOR_ANALYSIS_PROMPT: str`, `EXPLANATION_GENERATION_PROMPT: str`, `VLM_PAGE_SYNTHESIS_SYSTEM_PROMPT: str`, `MULTI_ARTICLE_SYNTHESIS_PROMPT_TEMPLATE: str`
- `agents.tema_q.telemetry`:
  - `class HeadingDispatchTrace`: Tracks heading block dispatches, breadcrumbs (`H1 > H2 > H3`), prompt lengths, generation times, and token usage.
  - `class PipelineDiagnosticTrace`: Full telemetry trace capturing model name, topic, execution duration, auditor merge events, token counts, and automated anomaly flags.
  - `persist_diagnostic_trace(trace: PipelineDiagnosticTrace, trace_dir: Optional[Path] = None) -> str`: Persists serialized diagnostic trace JSON to disk (`storage/reports/pipeline_traces/`).

### Core Modules (`core/`)
- `core.database`:
  - `get_db_connection() -> sqlite3.Connection` (configured per-connection with `WAL`, `busy_timeout=20000`, `foreign_keys=ON`, `synchronous=NORMAL`, `mmap_size=536870912`, `temp_store=MEMORY`, `cache_size=-64000`)
  - `atomic_transaction(conn: sqlite3.Connection)`: Context manager executing `BEGIN IMMEDIATE` transaction ensuring atomic claiming, isolation, and immediate lock resolution across worker threads.
  - `initialize_database() -> None` (executes 4-stage initialization: table & FTS5 trigger creation -> idempotent column & `task_queue` FK removal migrations -> index creation -> `csv_questions_fts_docsize` external-content FTS5 rebuild check)
  - `add_column_safely(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None`: Safe dynamic schema migration helper validating identifiers against `^[a-zA-Z0-9_]+$` and types against `_ALLOWED_COLUMN_TYPES` to eliminate DDL injection.
  - `normalize_db_file_path(file_path: str) -> str`: Normalizes file paths to lowercase forward-slash canonical strings to guarantee SQLite foreign and primary key consistency across OS platforms.
  - `record_question_attempt(conn: sqlite3.Connection, question_id: str | int, is_correct: bool, attempt_ts: float | None = None, verified_topics: list | None = None, cached_refs: dict | None = None, llm_summary: str | None = None) -> None`: Fast indexed upsert into `question_metadata` tracking attempts, wrong counts, and cached references/summaries.
  - `update_question_verified_topics(conn: sqlite3.Connection, question_id: str | int, verified_topics: list, cached_refs: dict | None = None, llm_summary: str | None = None) -> None`: Updates or initializes verified topics and cached metadata for a question in `question_metadata`.
- `core.queue_manager`:
  - `class PriorityQueueWorker`:
    - `start() -> None`
    - `stop() -> None`
    - `_process_loop() -> None`
  - `queue_worker: PriorityQueueWorker`
  - `is_llm_processing_enabled() -> bool`
  - `get_session_queue_info(session_id: str) -> dict[str, Any]`
  - `prioritize_session_tasks(session_id: str) -> int`
- `core.scheduler`:
  - `class CachedFSRSConfigProvider`: Thread-safe in-memory cache with 300s TTL for FSRS retention and interval parameters, preventing N+1 database queries on high-throughput quiz runs; supports `invalidate()` upon parameter mutations.
  - `get_fsrs_parameters() -> dict[str, Any]`: Reads user-configured FSRS parameters (`requested_retention`, `max_interval_days`, `easy_bonus`, `hard_factor`) from SQLite `user_state` with safe defaults via `CachedFSRSConfigProvider`.
  - `map_fsrs_state_to_status(state: int, stability: float = 0.0, reps: int = 0) -> str`: Maps FSRS numeric state and stability to UI status labels (`'new'`, `'learning'`, `'review'`, `'mastered'`).
  - `calculate_fsrs_next_interval(rating: int, state: int, difficulty: float, stability: float, reps: int, lapses: int, now: Optional[float] = None, requested_retention: Optional[float] = None, max_interval_days: Optional[int] = None) -> dict[str, Any]`: Computes updated FSRS v4.5 metrics using exact power-law interval scaling ($I = S \cdot \text{scaler}$ adhering to user-configured retention and capped at `max_interval_days`), $D_0(3)$ mean reversion, stability floor protection ($\ge 0.1$), and optional `due_timestamp` calculation; when `rating == 1` ('Again'), forces `interval_days = 0`, `interval_seconds = 0`, and sets `due_timestamp = now` so lapsed cards repeat on the next quiz set; returns `FSRS Stats Dict`.
- `core.model_lifecycle`:
  - `class LazyModelHolder`: Thread-safe lazy loader and memory releaser (`is_loaded`, `get()`, `release_memory()`, `unload_if_idle(ttl_override)`) supporting zero-VRAM baseline and automatic idle memory eviction.
  - `get_gpu_enabled_subprocess_env() -> dict[str, str]`: Generates a clean child subprocess environment removing empty or "-1" `CUDA_VISIBLE_DEVICES` so external LLMs can access GPU while backend remains bounded on CPU.
- `core.topic_filter`:
  - `load_ignore_set() -> frozenset[str]`: LRU-cached loader reading stopwords and operational noise tokens from `Ignore_list.txt`.
  - `is_valid_topic(topic: str | None) -> bool`: Validates candidate topics; rejects strings < 3 chars, purely non-alphanumeric text, and phrases consisting entirely of ignored tokens.
  - `filter_topics(topics: list[str]) -> list[str]`: Filters and deduplicates topic lists while preserving original sequence.
- `core.reporter`:
  - `generate_topic_report(topic: str, search_results: Optional[Union[dict, list]] = None, synthesis_output: Optional[dict] = None) -> str`: Compiles structured HTML dossier in `storage/reports/` adhering to `SynthesisOutputDict` (`display_title`, `enhanced_explanation`, `unified_article`, `distractor_analysis`, `variance_audit`, `top_references`); implements Invariant 13 polymorphic reference normalization, markdown-to-HTML rendering, and process/thread-safe atomic temp file replacement.

### Parsers Modules (`parsers/`)
- `parsers.embedding_engine`:
  - `get_configured_embedding_model(force_refresh: bool = False) -> str`
  - `set_configured_embedding_model(model_id: str) -> None`
  - `get_embedding_engine(model_id: Optional[str] = None) -> CPUEmbeddingEngine`
  - `is_embedding_model_downloaded(model_id: str) -> bool`
  - `download_embedding_model(model_id: str) -> dict[str, Any]`
  - `class CPUEmbeddingEngine`:
    - `__init__(model_id: Optional[str] = None)` (defers ONNX session creation until first `embed()`)
    - `get_dimension() -> int`
    - `embed(texts: list[str]) -> list[list[float]]`
    - `embed_single(text: str) -> list[float]`
  - `MODEL_DIMENSIONS: dict[str, int]` (SapBERT `768`, MiniLM `384`)
- `parsers.lancedb_engine`:
  - `build_lancedb_schema(vector_dim: int = 384) -> pyarrow.Schema`
  - `class LanceDBEngine`:
    - `__init__(db_path: str = str(LANCEDB_PATH), table_name: str = "medical_knowledge", vector_dim: Optional[int] = None)` (resolves active dimension via `get_embedding_engine().get_dimension()` if `vector_dim` is omitted)
    - `insert(records: list[dict]) -> None`
    - `vector_search(query_vector: list[float], limit: int = 25) -> list[dict]`
    - `fts_search(query_string: str, limit: int = 25) -> list[dict]`
    - `reset_table(vector_dim: Optional[int] = None) -> None`
    - `create_fts_index() -> None`
    - `count_rows() -> int`
- `parsers.tantivy_engine`:
  - `get_schema() -> tantivy.Schema`
  - `get_index(reset: bool = False) -> tantivy.Index`
  - `search_index(query_str: str, limit_per_source: int = 3) -> dict[str, list[dict]]`
- `parsers.heading_toc_calibrator`:
  - `build_document_toc_and_hierarchy(file_path: Union[str, Path], root_title: str = "", full_scan: bool = False, progress_callback: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]`: Multi-pass consensus heading classifier and TOC builder using 7-factor consensus scoring over 9D visual vectors, with bounded disk caching.
  - `score_and_resolve_heading_candidate(element: Any, profile: LayoutProfile, doc_font_stats: Dict[str, Any], page_no: int, total_pages: int, active_stack: Dict[int, str]) -> Tuple[bool, int, float]`: Evaluates visual typography, geometry, case, styling, and outline prefixes to score heading probability and tier.
  - `classify_outline_prefix(text: str) -> Tuple[int, str]`: Detects structured prefix patterns (e.g. `1.1.2`, `Chapter 4`, `A.`) mapping to standard heading tiers.
- `parsers.pdf_topic_indexer`:
  - `ensure_pdf_topic_index_table() -> None`: Initializes the `pdf_topic_index` SQLite table and lookup indices.
  - `index_pdf_document_topics(file_path: Union[str, Path], root_title: str = "", engine_source: str = "hybrid_toc") -> int`: Full-PDF topic indexer recording section page ranges, parent topics, and normalized keys.
  - `lookup_pdf_topic_pages(topic: str, pdf_path: Optional[str] = None, exclude_siblings: bool = True) -> List[Dict[str, Any]]`: Sibling-safe topic resolver disambiguating mutual exclusions (e.g. Bronchopneumonia vs Lobar Pneumonia).
  - `render_topic_pages_to_base64(pdf_path: Union[str, Path], start_page: int, end_page: int, max_pages: int = 3, dpi: int = 150) -> List[str]`: Renders PDF pages directly to base64-encoded PNG images for LM Studio VLM ingestion.
- `parsers.docling`:
  - Re-exports `AdaptivePdfLayoutEngine`, `clean_pdf_text`, `is_ocr_garbled_text`, `repair_ocr_token_heuristics`, `get_docling_converter`, `serialize_docling_ast`, `VisualPageInspector`, `get_rapidocr_line_engine`, `get_easyocr_line_engine`, `DOCLING_REQUIRED_MODELS`, `is_docling_models_downloaded`, `download_docling_models`, `ensure_offline_mode_if_downloaded`, `rect_area`, `rects_overlap`, `point_in_rect`, and `rgb_distance`.
  - `parsers.docling.models`: `DOCLING_REQUIRED_MODELS: tuple`, `is_docling_models_downloaded() -> bool`, `download_docling_models() -> dict[str, Any]`, `ensure_offline_mode_if_downloaded() -> None`.
  - `parsers.docling.converter`: `get_docling_converter(ocr_engine: str = "auto", ...) -> DocumentConverter`, `serialize_docling_ast(doc) -> dict[str, Any]`.
  - `parsers.docling.cleaner`: `clean_pdf_text(text: str) -> str`, `is_ocr_garbled_text(text: str) -> bool`, `repair_ocr_token_heuristics(text: str) -> str`.
  - `parsers.docling.geometry`: `rect_area(rect) -> float`, `rects_overlap(r1, r2) -> bool`, `point_in_rect(pt, rect) -> bool`, `rgb_distance(c1, c2) -> float`.
  - `parsers.docling.inspector`: `VisualPageInspector(page_image: np.ndarray)`, `get_rapidocr_line_engine() -> Any`, `get_easyocr_line_engine() -> Any`.
  - `parsers.docling.engine`: `class AdaptivePdfLayoutEngine` implementing multi-stage layout extraction, bounding box computation, typography clustering, booktabs table reconstruction, and LanceDB ingestion (`ingest_to_lancedb(file_path, source_article, main_topic)`). Also provides consolidated heading calibration `extract_page_with_docling_v1_heading_opt`.
- `parsers.indra`:
  - Re-exports core profiles, types, rules, strategies, engine components, and refiner functions:
  - `parsers.indra.types`: `BBox`, `RGB`, `ElementRole`, `HeadingPrototype`, `PageElement`, `PageElements`, `TableGrid`, `ThreadSafeLRUCache`; initializes `fg_hue = -1.0` and `bg_hue = -1.0` as achromatic sentinels, adds consensus tracking fields (`indent_ratio`, `line_count`, `confidence`, `embedded_toc_level`, `rotation`, `indra_role`).
  - `parsers.indra.profiles`: `ColumnMode`, `CoordinateZone`, `FurnitureSuppressionPolicy`, `HeadingWeightPolicy`, `LAYOUT_PROFILES`, `LayoutProfile`, `PageGeometryPolicy`, `StrategyKey`; incorporates 7-factor heading weights, asymmetric gutter thresholds, and expanded header zone ratios.
  - `parsers.indra.rules`: `RegexCatalog` (12 prefix families including `ALPHA_LOWER`, `ALPHA_UPPER`, `DECIMAL`; regex filters for `ICON_BULLET_MARKER`, `TOC_DOT_LEADER`, `CONTRIBUTOR_ROW`, `SITUATION_HEADER`), `SharedLineProcessor(clean, classify, enrich)`, `deskew_bbox_norm(bbox, angle)`, `enrich_lines(lines, ...)`, `overlap_ratio(b1, b2)`, `rgb_distance(c1, c2)`, `rgb_saturation(rgb)`, `rgb_to_hsv(rgb)`, `union_bbox(boxes)`.
  - `parsers.indra.strategies`: `LayoutStrategy`, `NativeDigitalStrategy`, `ScannedTwoColumnTextbookStrategy`, `IllustratedBookletStrategy`, `AcademicArticleStrategy`, `LayoutStrategyRegistry`; provides `_merge_wrapped_elements` (parenthetical acronym tails, *Situation multi-line exemptions) and clinical subheading classification a)-d).
  - `parsers.indra.engine`: `FallbackChain`, `IndraModularEngine(mode, math, furniture, dpi, threads, image_ocr)`, `reconstruct_2d_table_grid(words, ...)`, `_crosses_dynamic_channel`, `_detect_and_ocr_chromatic_pill_banners`, physiological subscript attachment (PaO2, PaCO2) with zero-VRAM CPU/opportunistic GPU routing, `extract_page_with_indra`, `extract_pdf_structure`, `extract_structured_layout`, `ingest_to_lancedb`, and consolidated heading calibration `extract_page_with_indra_base_heading_opt`.
  - `parsers.indra.document`: `DocumentHeadingTracker(root_title)`, `get_or_create_document_tracker(file_path, default_title, reset, target_page, full_scan, progress_callback)`; supports dual-mode lazy & streaming TOC tracking with v37 disk caching (`_get_toc_disk_cache_path`).
  - `parsers.indra.heading_calibrator`: `calibrate_headings_multistep(elements: list, profile: LayoutProfile) -> list`; multi-step layout box and heading calibrator with hard exclusions, HSV color-hue extraction differentiating chromatic headings, sibling outline consistency, prose demotion vetoes, and bidirectional consensus scoring (threshold >= 0.60).
- `parsers.indra.refiner`: `IndraHybridLayoutRefiner(page_elements)`, `build_layout_svg_overlay(page_elements, width, height)`; deduplicates consensus lines and suppresses duplicate heading/list item emissions in output markdown.
- `parsers.schema`:
  - Canonical AST data schema defining `BoundingBox`, `ParsedBlock`, `HeadingNode`, `DocumentSection`, `ParsedDocument`, and `clamp_heading_levels(sections)` re-exported across Docling and Indra.
- `parsers.pdf_extractor`:
  - `clean_pdf_text(text: str) -> str`: Normalizes typographical ligatures (fi, fl, ffi), repairs line-break hyphenations, strips null bytes and form feeds, and cleans whitespace.
  - `extract_page_slice(file_path: Union[str, Path], page_number: int) -> str`: LRU-cached (maxsize=256) text extraction with natural reading order (`sort=True`) and bounds safety (rejects `page_number < 1`).
  - `extract_page_with_headings(page: Any, default_title: str = "") -> dict[str, Any]`: Fast layout extractor using font-size heuristics and margin bounds (filters running headers/footers); produces dictionaries conforming to `ChunkDict` (`page`, `page_number`, `heading`, `heading_path`, `subheading`, `title`, `text`, `snippet`, `length`, `article_length`, `doc_length`).
  - `extract_pdf_pages(file_path: Union[str, Path], default_title: str = "") -> list[dict[str, Any]]`: Complete document extractor returning full `ChunkDict` records with deterministic MD5 IDs for Tantivy BM25 and LanceDB indexing when `pdfEngine == 'fitz'`.
  - `extract_pdf_structure(file_path: Union[str, Path]) -> list[dict[str, Any]]`: Drop-in interoperability alias for `extract_pdf_pages()`.
  - `get_pdf_page_count(file_path: Union[str, Path]) -> int`: Fast trailer-based page count reader.

- `parsers.obsidian_reader`:
  - `read_markdown_body(file_path: Union[str, Path], allowed_root: Optional[Union[str, Path]] = None) -> str`: Reads Obsidian notes with strict path traversal confinement (`is_relative_to`), null-byte checks, and start-anchored YAML frontmatter stripping preserving intermediate markdown rules.
  - `extract_obsidian_sections(file_path: Union[str, Path]) -> list[dict]`: Layout-aware section extractor segmenting notes by heading level (H1-H6).
  - `read_obsidian_for_browser(file_path: Union[str, Path]) -> dict[str, Any]`: Prepares notes for SPA rendering, resolving wikilinks and embedding section anchors.
  - `heading_slug(text: str) -> str`: Deterministic URI-safe slug generator for section heading anchors (`heading-{slug}`).
  - `extract_heading_sections(markdown_body: str) -> list[dict[str, Any]]`: Slices markdown bodies into hierarchical heading sections with slugs, line ranges, and cleaned titles.
  - `class ObsidianNoteRecord`: Structured dataclass representing note metadata, titles, book affiliations, links, and search texts.
  - `class ObsidianVaultGraph`: In-memory and SQLite-backed graph indexer caching notes (`obsidian_notes`), clinical subheading synonyms (`CLINICAL_SUBHEADING_SYNONYMS`), and unified section search (`search_notes_and_sections`).
  - `convert_obsidian_markdown_to_html_ready(markdown_text: str, current_book: str = "") -> str`: Prepares obsidian markdown into HTML-ready text with anchor IDs and obsidian-wikilink attributes.
- `parsers.zim_reader`:
  - `class ZimAsset(tuple)`: Polyfill supporting `tuple[bytes, str]` unpacking and dictionary/property access (`.content`, `.mimetype`, `.zim_path`).
  - `get_enabled_zim_paths(force_refresh: bool = False) -> list[str]`
  - `get_archive(zim_path: str) -> Optional[Any]`
  - `resolve_zim_topic(term: str) -> dict[str, Any]`: Uses thread-local SQLite pooling (`_get_thread_db_connection()`) and bounded LRU caching (`_cached_resolve_zim_topic`, maxsize=16384).
  - `get_zim_article(zim_path: str, title: str, max_chars: int = 4500) -> str`
  - `get_zim_article_html(title_or_path: str, zim_path: Optional[str] = None) -> dict[str, Any]`: Scopes extracted HTML inside `<div class="mw-parser-output">`, rewrites media and SVG asset URLs (`<image>`, `<object>`, `<embed>`, `<source>`), and sanitizes inline styles on Wikipedia navboxes while preserving structural widths.
  - `get_zim_asset(asset_path: str, zim_path: str = "") -> Optional[ZimAsset]`
  - `search_zim_articles(query: str, limit: int = 3) -> list[ChunkDict]`
  - `expand_query_via_zim(query: str) -> str`
- `parsers.reranker`:
  - `get_configured_flashrank_model(force_refresh: bool = False) -> str`
  - `set_configured_flashrank_model(model_name: str) -> None`
  - `is_flashrank_model_downloaded(model_name: str) -> bool`
  - `download_flashrank_model(model_name: str) -> dict[str, Any]`
  - `get_ranker(model_name: Optional[str] = None) -> Optional[Ranker]`
  - `rerank_chunks(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]`
- `parsers.scispacy_linker_engine`:
  - `get_concept_aliases(term: str) -> list[str]`: LRU-cached (maxsize=4096) concept synonym and alias resolver using thread-local SQLite covering index connections with 256 MB MMAP.
  - `get_concept_aliases_batch(terms: Iterable[str]) -> dict[str, list[str]]`: High-throughput batched synonym resolver across biomedical terms.
  - `resolve_ontology_concept(term: str, enable_fuzzy_tfidf: bool = True, min_tfidf_score: float = 0.84) -> dict`: Multi-stage clinical concept resolver; uses B-Tree exact lookups (<0.2ms) followed by cross-matcher argmax TF-IDF evaluation across MeSH, RxNorm, and HPO.
  - `resolve_ontology_concepts_batch(terms: Iterable[str], enable_fuzzy_tfidf: bool = True, min_tfidf_score: float = 0.84) -> dict[str, dict]`: Batched concept resolver with vectorized SciPy sparse matrix lookup.
  - `extract_and_link_entities(text: str, model_id: Optional[str] = None) -> list[dict[str, Any]]`: Combined biomedical entity extractor and ontology concept linker.
  - `build_offline_ontology_index(selected_ontologies: Optional[list[str]] = None, target_kbs: Optional[list[str]] = None, force_rebuild: bool = True, progress_callback: Optional[callable] = None, **kwargs) -> dict`: Streams definitions into `ontology_kb.db` in low-RAM batches; clears in-memory caches and applies B-Tree covering indices.
  - `build_ontology_database`: Backward-compatible alias for `build_offline_ontology_index`.
  - `load_scispacy_pipeline(model_id: Optional[str] = None) -> list[Any]`: Thread-safe, cached loader for biomedical spaCy pipelines enforcing `spacy.require_cpu()` and zero VRAM allocation.
  - `is_scispacy_model_downloaded(model_id: str) -> bool`: Checks local `data/models/scispacy/` extracted folders and Python packages.
  - `download_scispacy_model(model_id: str) -> dict`: Downloads and extracts models atomically without orphaned temporary files.
  - `get_configured_scispacy_model() -> str`: Reads persistent model selection from `user_state`.
  - `set_configured_scispacy_model(model_id: str) -> None`: Updates active SciSpaCy model selection and clears pipeline cache.
  - `get_ontology_files_status() -> dict`: Scans local disk definitions and compiled concepts.
  - `normalize_alias(text: str) -> str`: LRU-cached (maxsize=8192) medical syntax canonicalizer (e.g., handles inversion of inverted MeSH descriptors).

### Server Routes & API Endpoints (`server/`)
- `server.app`:
  - Lifespan & Application Instance: `app = FastAPI(title=APP_TITLE, lifespan=lifespan)`
  - Middleware: `CORSMiddleware(allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])`
  - Lifespan Hooks: Startup initializes directories (`ensure_directories()`), executes schema migrations (`initialize_database()`), binds the primary asyncio event loop to `ws_manager.manager`, starts the background priority worker (`queue_worker.start()`), and enqueues initial CSV precompute (`task_type = 'BATCH_PRECOMPUTE'`, `status = 'pending'`). Teardown safely offloads worker termination (`asyncio.to_thread(queue_worker.stop)`) and closes shared HTTP clients (`close_http_client()`).
  - WebSocket Endpoints: `WS /ws` & `WS /ws/` (Primary live WebSocket broadcast hub for queue and session status), `WS /ws/indexing` (Indexing updates), `WS /ws/tasks` (Task updates) managed via `ws_manager.manager`.
  - Static Mounts: `/vendor` -> `VENDOR_DIR`, `/ui` -> `UI_DIR`, `/reports` -> `REPORTS_DIR`, `/` -> `UI_DIR` (with fallback `GET /` serving `UI_DIR / "index.html"`).
- `server.ws_manager`:
  - `class ConnectionManager`:
    - `connect(ws: WebSocket, channel: str = "all") -> None`: Idempotent connection registration with per-socket write lock and channel assignment (`"all"`, `"indexing"`, `"tasks"`).
    - `disconnect(ws: WebSocket) -> None`: Safely removes connection and unregisters locks.
    - `broadcast_task_update(task_id: int, status: str, topic: str = "", session_id: Optional[str] = None, progress: int = 0, **kwargs) -> None`: Emits `task_update` schema (`type="task_update"`).
    - `broadcast_session_update(session_id: str, *args, refs_status: Optional[str] = None, llm_status: Optional[str] = None, topics_reviewed: Optional[bool] = None, **kwargs) -> None`: Emits `session_update` schema with dual signature compatibility.
    - `broadcast_indexing_progress(source_id: Union[str, int] = "", *args, percent: Optional[int] = None, message: Optional[str] = None, status: str = "running", **kwargs) -> None`: Emits `indexing_progress` schema.
    - `broadcast_sync(payload: dict, channel: str = "all") -> None`: Thread-safe cross-loop dispatcher for synchronous background workers via `run_coroutine_threadsafe`.
    - `broadcast_task_update_sync(...)`
    - `broadcast_session_update_sync(...)`
    - `broadcast_indexing_progress_sync(...)`
  - Singletons: `manager = ConnectionManager()`, `ws_manager = manager` (exported aliases).

- `server.routes_analysis`:
  - `GET /api/analysis/files` -> `get_analysis_files() -> list[dict[str, Any]]`: Scans indexed PDF sources and lists analysis progress, reviewed page counts, and total pages.
  - `POST /api/analysis/initialize` -> `initialize_analysis(req: InitializeAnalysisRequest) -> dict[str, Any]`: Starts or loads layout analysis session for a PDF, registering layout profile in `layout_analysis_progress`.
  - `GET /api/analysis/page-data` -> `get_page_data(file_path: str, page_number: int, profile_id: Optional[str] = None) -> dict[str, Any]`: Slices page image as base64 PNG, extracts elements via Indra layout engine or stored `user_annotated_bboxes`, returning SVG overlay and extracted markdown.
  - `POST /api/analysis/save-page` -> `save_page_annotations(req: SavePageRequest) -> dict[str, Any]`: Atomically updates page bounding box annotations in `user_annotated_bboxes` within a single SQLite transaction and marks page as reviewed in `reviewed_pages`.
  - `POST /api/analysis/save-markdown` -> `save_markdown_override(req: SaveMarkdownRequest) -> dict[str, Any]`: Persists manual full-document or page-level markdown override text.
  - `GET /api/analysis/toc` -> `get_document_toc(file_path: str) -> dict[str, Any]`: Compiles hierarchical document heading tree from saved page bounding boxes (`element_type='header'`) and layout strategies.
  - `POST /api/analysis/update-heading` -> `update_heading_level(req: UpdateHeadingRequest) -> dict[str, Any]`: Modifies heading tier level (H1-H6) for an annotated header bounding box in `user_annotated_bboxes`.
  - `POST /api/analysis/pipeline-benchmark`: Executes deterministic multi-model benchmark evaluation across RAG synthesis stages.
  - `POST /api/analysis/finalize` -> `finalize_analysis(req: FinalizeAnalysisRequest) -> dict[str, Any]`: Compiles all reviewed page bboxes into unified markdown, optionally exports to LanceDB via `parsers.docling.engine.ingest_to_lancedb()`, marks progress as `completed`, and updates source status.
- `server.routes_csv`:
  - `scan_and_ingest_csvs() -> dict[str, Any]`: Recursively scans `CSV_DIR` and `MCQS_DIR`, normalizes diverse option/answer representations (numbers 1-4, bracketed letters, verbatim text matches), and executes chunked (`BATCH_SIZE=1000`) `INSERT OR IGNORE` queries into `csv_questions`.
  - `load_csvs_background`: Backward-compatibility callable alias for `scan_and_ingest_csvs`.
  - `POST /api/csv/reload`, `POST /reload`, `GET /api/csv/reload`, `GET /reload` -> `reload_csvs(background_tasks: Optional[BackgroundTasks] = None) -> dict[str, Any]`: Offloads re-scanning to worker threads via `asyncio.to_thread` and returns execution metrics.
  - `GET /api/csv/subjects`, `POST /api/csv/subjects`, `GET /api/mcq/subjects`, `POST /api/mcq/subjects` -> `get_subjects() -> list[SubjectResponse]`: Aggregates question counts per subject file stem.
  - `GET /api/csv/topics`, `POST /api/csv/topics`, `GET /api/mcq/topics`, `POST /api/mcq/topics` -> `get_topics(subject: Optional[str] = None) -> list[TopicResponse]`: Lists topic counts with optional subject filtering.
  - `POST /api/csv/search_count` & `POST /api/mcq/search_count` -> `search_count(req: GenerateSetRequest) -> dict[str, Any]`: Computes matching question count over FTS5 and subject/topic filters.
  - `POST /api/csv/generate_set` & `POST /api/mcq/generate_set` -> `generate_set(req: GenerateSetRequest) -> dict[str, Any]`: Curates question set prioritizing overdue cards (`due_timestamp <= unixepoch('now')`) and learning state questions (`state IN (1, 3)`) ahead of unreviewed questions so wrong questions repeat on the next quiz set.
  - `GET /api/csv/fsrs_summary`, `POST /api/csv/fsrs_summary`, `GET /api/mcq/fsrs_summary`, `POST /api/mcq/fsrs_summary` -> `get_fsrs_summary() -> dict[str, Any]`: Real-time FSRS card status distribution (`new`, `learning`, `review`, `mastered`).
  - `GET /api/csv/db_status` & `GET /api/mcq/db_status` -> `get_db_status() -> dict[str, Any]`: Checks question bank population.
  - `POST /api/csv/build_db` & `POST /api/mcq/build_db` -> `build_db(background_tasks: BackgroundTasks) -> dict[str, Any]`: Offloads background CSV ingestion.
  - `GET /api/csv/diagnostics/lm_studio` & `GET /api/mcq/diagnostics/lm_studio` -> `check_lm_studio() -> dict[str, Any]`: Legacy compatibility alias for `/api/models/diagnostics/lm_studio`.
  - `POST /api/mcq/topics_status`, `POST /api/mcq/topics/status`, `POST /api/csv/topics_status` -> `topics_status(req: TopicsStatusRequest) -> dict[str, Any]`: Checks existing dossiers, references (via polymorphic validation), and ignored status.
- `server.routes_mcq`:
  - `GET /api/mcq/question/{question_id}/verified_topics`: Retrieves strictly verified topics for a question from `question_metadata` and `extracted_topics`.
  - `POST /api/mcq/extract_topics` & `POST /api/mcq/topics/extract` -> `extract_mcq_topics(payload: TopicExtractionPayload) -> dict[str, Any]`
  - `POST /api/mcq/score` -> `score_question(payload: ScorePayload) -> dict[str, Any]`
  - `GET /api/mcq/synthesis?topic=...&resolve_if_missing=true` -> `get_synthesis(topic: str, resolve_if_missing: bool = True) -> dict[str, Any]`
  - `POST /api/mcq/batch_generate_references` & `POST /api/mcq/topics/generate_references` -> `batch_generate_references(payload: BatchTopicRequest) -> dict[str, Any]`
  - `POST /api/mcq/batch_generate_explanations` & `POST /api/mcq/topics/generate_explanations` -> `batch_generate_explanations(payload: BatchTopicRequest) -> dict[str, Any]`
  - `GET /api/mcq/spaced_repetition_config` -> `get_spaced_repetition_config() -> dict[str, Any]`: Retrieves active FSRS parameters (`requested_retention`, `max_interval_days`, `easy_bonus`, `hard_factor`).
  - `POST /api/mcq/spaced_repetition_config` -> `set_spaced_repetition_config(req: SpacedRepetitionConfigRequest) -> dict[str, Any]`: Updates and persists user-configured FSRS parameters to SQLite `user_state`.
  - `POST /api/mcq/batch_update_all_references` -> `batch_update_all_verified_references() -> dict[str, Any]`: Aggregates all distinct verified topics across `question_metadata`, `extracted_topics`, and `session_questions` to refresh reference links in batch.
  - `POST /api/mcq/batch_update_all_summaries` -> `batch_update_all_verified_summaries() -> dict[str, Any]`: Aggregates all distinct verified topics across `question_metadata`, `extracted_topics`, and `session_questions` to enqueue missing LM Studio summaries in batch.
- `server.routes_models`:
  - `GET /api/models/lm-studio-available`: Fast probe checking LM Studio availability and active loaded model metadata.
  - `POST /api/models/unload`: Evicts idle LLM models from host memory to free RAM/VRAM.
  - `GET /api/models/cpu_embeddings`, `POST /api/models/cpu_embeddings`, `GET /api/models/cpu_embedding_models`, `POST /api/models/cpu_embedding_models` -> `get_cpu_embedding_models() -> dict[str, Any]`: Lists embedding models (including SapBERT Clinical Fulltext ONNX), disk caching status, and active model selection from `user_state`.
  - `GET /api/models/flashrank_models` & `POST /api/models/flashrank_models` -> `get_flashrank_models() -> dict[str, Any]`: Lists FlashRank cross-encoder reranking models and download status.
  - `GET /api/models/scispacy_models` & `POST /api/models/scispacy_models` -> `get_scispacy_models() -> dict[str, Any]`: Lists SciSpaCy biomedical NER models, RAM footprints, and installation status.
  - `GET /api/models/indra_status` -> `get_indra_status() -> dict[str, Any]`: Reports local download state of Datalab Indra OCR core and LaTeX math ONNX models.
  - `POST /api/models/set_active` & `POST /api/models/select` -> `set_active_model(payload: SetActiveModelRequest) -> dict[str, Any]`: Persists choice to `user_state` and invokes engine model setters.
  - `POST /api/models/download_all` & `POST /api/models/download_all_defaults` -> `download_all_defaults(background_tasks: BackgroundTasks) -> dict[str, Any]`: Enqueues background pre-fetching of default embedding, FlashRank, SciSpaCy, Docling, Indra OCR, and UI vendor assets.
  - `GET /api/models/download_all_status` -> `get_download_all_status() -> dict[str, Any]`: Polls real-time progress of the one-click offline bundle installer.
  - `POST /api/models/download` -> `download_model(request: ModelDownloadRequest, background_tasks: BackgroundTasks) -> dict[str, Any]`: Downloads embedding, FlashRank, or SciSpaCy models with zero VRAM and thread-safe file handling.
  - `POST /api/models/scispacy/download` -> `download_scispacy_model_direct(request: ModelDownloadRequest, background_tasks: BackgroundTasks) -> dict[str, Any]`: Dedicated alias endpoint for SciSpaCy model downloads.
  - `GET /api/models/scispacy/download_status?model_name=...` -> `get_scispacy_download_status(model_name: str) -> dict[str, Any]`: Polls real-time download and extraction progress for a SciSpaCy model.
  - `GET /api/models/linkers/status` -> `get_linkers_status() -> dict[str, Any]`: Reports status of raw offline ontology files and compiled SQLite B-Tree index.
  - `POST /api/models/linkers/build` & `POST /api/models/linkers/build_index` -> `build_linkers_index(request: BuildIndexRequest, background_tasks: BackgroundTasks) -> dict[str, Any]`: Compiles offline ontology definitions and aliases into `ontology_kb.db` in background.
  - `GET /api/models/linkers/build_progress` -> `get_linkers_build_progress() -> dict[str, Any]`: Returns real-time progress percentage and current compilation stage for `ontology_kb.db`.
  - `GET /api/models/diagnostic/lm_studio` & `GET /api/models/diagnostics/lm_studio` -> `diagnostic_lm_studio() -> dict[str, Any]`: Canonical probe for local LM Studio HTTP server endpoint without blocking the async event loop.
  - `GET /api/models/diagnostic/db` & `GET /api/models/diagnostics/db` -> `diagnostic_database() -> dict[str, Any]`: Canonical inspection of SQLite database existence, WAL journal file, and core table row counts (`mcq_bank`, `csv_questions`, `task_queue`, `quiz_sessions`, etc.).
  - `GET /api/models/diagnostic/sumatrapdf` & `GET /api/models/diagnostics/sumatrapdf` -> `diagnostic_sumatrapdf() -> dict[str, Any]`: Canonical verification of SumatraPDF desktop executable discovery on Windows.
  - `GET /api/models/diagnostic/tantivy` & `GET /api/models/diagnostics/tantivy` -> `diagnostic_tantivy() -> dict[str, Any]`: Canonical inspection of Tantivy inverted index segment files and indexed document count.
  - `GET /api/models/diagnostic/all` & `GET /api/models/diagnostics/all` -> `diagnostic_all() -> dict[str, Any]`: Runs parallel offline health checks across LM Studio, SQLite, SumatraPDF, Tantivy, and Ontologies.
  - `POST /api/models/test_embed` -> `test_embed_model(payload: TestEmbedPayload) -> dict[str, Any]`: Generates CPU embedding for input text using cached singleton and measures latency.
  - `POST /api/models/test_rerank` -> `test_rerank_model(payload: TestRerankPayload) -> dict[str, Any]`: Reranks input passages against query adhering to Invariant 9 (`text` key contract).
- `server.routes_notes`:
  - `POST /api/notes/qa_metrics` & `POST /api/notes/qa-metrics` -> `calculate_qa_metrics(payload: QAMetricsRequest) -> dict[str, Any]`: Computes char/token ratios and Levenshtein distance offloaded to a worker thread via `asyncio.to_thread`.
  - `GET /api/notes/launch-sumatra` & `GET /api/notes/launch_sumatra` -> `launch_sumatra(file_path: str, page: Optional[int] = None, page_number: Optional[int] = None) -> dict[str, str]`: Deep-links SumatraPDF on Windows adhering to Invariant 6 (`-reuse-instance -page <num>`) with path/extension validation (`.pdf`) and binary auto-discovery across `%LOCALAPPDATA%` and `%ProgramFiles%`.
  - `GET /api/notes/obsidian` & `GET /api/notes/obsidian-note` -> `get_obsidian_note(file_path: Optional[str] = None, title: Optional[str] = None, anchor: Optional[str] = None) -> dict[str, Any]`: Formats note for browser reader; handles URL unquoting, strips `#Heading` anchors from file stems, and prunes hidden directories (`.obsidian`, `.trash`).
  - `POST /api/notes/obsidian-native` & `POST /api/notes/open-obsidian` -> `open_obsidian_native(req: OpenObsidianRequest) -> dict[str, Any]`: Launches native desktop Obsidian via `obsidian://open?vault=...&file=...` adhering to Invariant 6; supports cross-platform invocation (`os.startfile`, macOS `open`, Linux `xdg-open`).
  - `GET /api/notes/zim-article` & `GET /api/notes/zim_article` -> `zim_article(article_id: Optional[str] = None, title: Optional[str] = None, zim_path: Optional[str] = None) -> dict[str, Any]`: Extracts in-browser HTML from offline ZIM archives.
  - `GET /api/notes/zim-asset` & `GET /api/notes/zim_asset` -> `zim_asset(path: str, zim_path: Optional[str] = None) -> Response`: Serves binary media assets from ZIM archives; polymorphically unpacks dicts, tuples, and `ZimAsset` polyfills with `Cache-Control` response headers.
  - `GET /api/notes/lecture-notes`: Returns paginated list of user lecture notes (`user_lecture_notes`), item counts, and card shades.
  - `GET /api/notes/lecture-notes/{note_id}`: Retrieves full lecture note record and ordered item list (`user_lecture_note_items`).
  - `POST /api/notes/lecture-notes`: Creates a new lecture note or updates existing title, pin status, and wallpaper shade index.
  - `DELETE /api/notes/lecture-notes/{note_id}`: Cascading deletion of lecture note and associated items.
  - `POST /api/notes/lecture-notes/{note_id}/items`: Creates or updates a lecture note line item, resolving medical topic reference counts and caching reference details.
  - `DELETE /api/notes/lecture-notes/{note_id}/items/{item_id}`: Deletes a specific lecture note line item.
- `server.routes_queue`:
  - `GET /api/queue/topic_files_sync_status`: Compares `Ignore_list.txt` and `topic.txt` against SQLite and returns diff counts.
  - `POST /api/queue/topic_files_sync_apply`: Synchronizes flat files and database tables bidirectionally.
  - `DELETE /api/queue/remove_verified_topic` & `POST /api/queue/remove_verified_topic`: Removes verified topic association from a question.
  - `POST /api/queue/enqueue`: Batch enqueues topic synthesis tasks.
  - `POST /api/queue/extract_topics` -> `extract_topics(req: PrepareRequest) -> dict[str, Any]`
  - `POST /api/queue/ignore_topic` -> `ignore_topic(req: IgnoreTopicRequest) -> dict[str, Any]`
  - `DELETE /api/queue/ignore_topic` & `POST /api/queue/unignore_topic` -> `unignore_topic(topic: Optional[str] = None, req: Optional[IgnoreTopicRequest] = None) -> dict[str, Any]`
  - `GET /api/queue/ignored_topics` -> `list_ignored_topics() -> dict[str, Any]`
  - `POST /api/queue/prioritize` -> `prioritize_queue_item(req: PrioritizeRequest) -> dict[str, Any]`
  - `POST /api/queue/prepare` & `POST /api/queue/prepare_questions` -> `prepare_questions(req: PrepareTopicsRequest) -> dict[str, Any]`
  - `POST /api/queue/clear_failed` & `DELETE /api/queue/failed` -> `clear_failed_tasks() -> dict[str, Any]`
  - `POST /api/queue/clear_completed` -> `clear_completed_tasks() -> dict[str, Any]`
  - `POST /api/queue/retry_failed` -> `retry_failed_tasks() -> dict[str, Any]`
  - `GET /api/queue/status` -> `get_queue_status() -> dict[str, Any]`
- `server.routes_search`:
  - `POST /api/search` -> `perform_search(req: SearchRequest) -> dict[str, Any]`:
    - Validates `SearchRequest(query: str, limit: int = 10, source_type: str = "all", enable_rerank: bool = True)`.
    - Executes multi-stage pipeline: Aliases + ZIM expansion -> Tantivy BM25 + LanceDB (Vector + FTS) + Direct ZIM B-Tree -> Single-File Deduplication (atomic page/text/heading sync) -> Sibling filtering -> Zero-VRAM FlashRank reranker.
    - Returns: `{"status": "success", "top_hit": ChunkDict | None, "all_results": SearchResultsDict, "candidates": list[ChunkDict], "query": str, "aliases": list[str]}`.
  - `GET /api/search/diagnostics/{lm_studio|db|sumatrapdf|tantivy}`: Legacy compatibility aliases for `/api/models/diagnostics/*`.
- `server.routes_sessions`:
  - `sync_consolidated_due_session(conn) -> None`: Consolidates overdue and learning questions along with wrongly answered questions into unified `sess_spaced_repetition_due`, maintaining full synchronization of `topic_list_json` and `session_questions` with verified topics.
  - `GET /api/sessions/checklist-analytics`: Returns historical checklist completion rates and SVG analytics.
  - `PATCH /api/sessions/topics/select`: Optimistically updates topic selection within a quiz session.
  - `GET /api/sessions/due_summary`
  - `GET /api/sessions/due_summary` -> `get_due_summary() -> dict[str, Any]`: Returns `{status: "success", total_due: int, session_id: Optional[str]}`.
  - `GET /api/sessions/dashboard_prompts` -> `get_dashboard_prompts() -> dict[str, Any]`: Computes intelligent banner list (due reviews, high-yield weak topics derived from NLP-extracted topics, subject rotations unattempted in 3+ days, and rotating encouragement messages).
  - `POST /api/sessions/save` -> `save_session(session: SessionCreate) -> dict[str, Any]`
  - `GET /api/sessions/history` -> `get_session_history() -> list[dict[str, Any]]`: Retrieves session history with dynamic question count and answered stats; if `auto_remove_completed_sets` is enabled in `user_state`, automatically cascades deletion of fully completed/revised sessions (excluding `sess_spaced_repetition_due`).
  - `GET /api/sessions/{session_id}` -> `get_session_by_id(session_id: str) -> dict[str, Any]`
  - `GET /api/sessions/{session_id}/questions` & `GET /api/sessions/questions/{session_id}` -> `get_session_questions(session_id: str) -> list[dict[str, Any]]`
  - `GET /api/sessions/{session_id}/extract_draft_topics` -> `get_or_extract_session_topics(session_id: str) -> dict[str, Any]`
  - `POST /api/sessions/{session_id}/update_topics` -> `update_session_topics(session_id: str, req: UpdateSessionTopicsRequest) -> dict[str, Any]`
  - `POST /api/sessions/{session_id}/retry` -> `retry_session_llm_tasks(session_id: str) -> dict[str, Any]`
  - `POST /api/sessions/{session_id}/reprocess` -> `reprocess_session(session_id: str) -> dict[str, Any]`
  - `POST /api/sessions/delete` & `DELETE /api/sessions/{session_id}` -> `delete_sessions(req: DeleteSessionsRequest) -> dict[str, Any]`
  - `GET /api/sessions/{session_id}/topics` -> `get_session_topics_and_notes(session_id: str) -> list[dict[str, Any]]`
  - `GET /api/sessions/{session_id}/inspector` -> `get_session_inspector_details(session_id: str) -> dict[str, Any]`
- `server.routes_sources`:
  - `GET /api/sources/wallpapers` -> `get_wallpapers() -> dict[str, list[str]]`: Lists dark/light wallpaper filenames from `ui/img/dark` and `ui/img/light` with root fallbacks.
  - `GET /api/sources` -> `get_sources() -> list[dict[str, Any]]`: Queries configured sources from `knowledge_sources` table.
  - `POST /api/sources` -> `add_source(payload: SourcePayload) -> dict[str, str]`: Upserts knowledge source record into SQLite.
  - `POST /api/sources/{source_id}/toggle` -> `toggle_source(source_id: str) -> dict[str, Any]`: Toggles source `enabled` state.
  - `PATCH /api/sources/{source_id}/engine` & `POST /api/sources/{source_id}/engine` -> `update_source_engine(source_id: str, payload: EnginePayload) -> dict[str, Any]`: Updates `pdfEngine` (`fitz`, `docling`, `docling_v2`, `docling_v3`, `docling_v1_heading_opt`, `indra`, `indra_math`, `indra_ocr`, `indra_base_heading_opt`) on a configured PDF source.
  - `DELETE /api/sources/{source_id}` -> `delete_source(source_id: str) -> dict[str, str]`: Deletes source from SQLite.
  - `POST /api/sources/index` -> `index_sources(background_tasks: BackgroundTasks) -> dict[str, str]`: Concurrency-guarded background indexer trigger (returns `status="busy"` if already running).
  - `POST /api/sources/open_pdf` -> `open_pdf(payload: OpenPdfPayload) -> dict[str, str]`: Windows deep-link launcher for SumatraPDF using `-reuse-instance` and `-page` flags with `.pdf` path validation and auto-discovery in `%LOCALAPPDATA%` and `%ProgramFiles%`.
  - `GET /api/sources/asset?path=...` -> `get_asset(path: str) -> FileResponse`: Serves Obsidian attachments with path traversal checks (`is_relative_to`) and mimetype sniffing.
  - `POST /api/sources/build_zim_dict` -> `build_zim_dict() -> dict[str, Any]`: Zero-copy C++ B-tree archive inspection updating item counts.
  - `POST /api/sources/test_pdf_extract` -> `test_pdf_extract(payload: TestPdfExtractPayload) -> dict[str, Any]`: Interactive PDF layout comparator testing Fitz text, heuristic headings, Docling OCR engines (`auto`, `rapidocr`, `easyocr`), and Indra OCR layout parsers.
- `server.routes_state`:
  - `GET /api/state/ignored-topics`, `POST /api/state/ignored-topics`, `DELETE /api/state/ignored-topics/{topic_norm}`: Manages permanently ignored topics in SQLite `ignored_topics`.
  - `GET /api/state/extraction-config`, `PUT /api/state/extraction-config`: Manages document extraction settings (`explicit_profile_id`, `fallback_provider_primary`, `dpi`, `threads`, `math`, `furniture`).
  - `GET /api/state/verify_vendor`, `POST /api/state/verify_vendor`: Verifies offline vendor assets integrity.
  - `GET /api/state/vendor_status` -> `get_vendor_status() -> dict[str, Any]`: Checks presence and size (>128 bytes) of offline vendor scripts (Tailwind, Marked, DOMPurify, KaTeX) in `ui/vendor/`.
  - `POST /api/state/vendor_download` -> `trigger_vendor_download() -> dict[str, Any]`: Triggers atomic download of vendor assets via `config.ensure_offline_vendor_assets()`.
  - `GET /api/state/export` -> `export_state() -> dict[str, Any]`: Exports all key-value entries from `user_state`.
  - `POST /api/state/import` -> `import_state(payload: Union[StateImportPayload, dict]) -> dict[str, Any]`: Restores and upserts key-value pairs into `user_state`.
  - `GET /api/state` & `GET /api/state/` -> `get_all_state() -> dict[str, Any]`: Fetches all persistent user state records.
  - `POST /api/state` & `POST /api/state/` -> `save_state(state: Union[StateUpdate, dict]) -> dict[str, str]`: Polymorphic upsert supporting both `StateUpdate` models (`key`, `value`) and multi-key flat dictionaries.
  - `PATCH /api/state` & `PATCH /api/state/` -> `patch_all_state(state: Union[StateUpdate, dict]) -> dict[str, str]`: Batch partial update.
  - `GET /api/state/{key}` -> `get_state(key: str) -> Any`: Retrieves deserialized state value (dict, list, primitive) or `{}` if not found.
  - `PUT /api/state/{key}` & `POST /api/state/{key}` -> `put_state_key(key: str, payload: Any) -> dict[str, str]`: Direct key replacement.
  - `PATCH /api/state/{key}` -> `patch_state_key(key: str, payload: Any) -> dict[str, Any]`: Deep merge patch for dictionary states without sibling key loss (`AppPersistence.updateState()`).
  - `DELETE /api/state/{key}` -> `delete_state_key(key: str) -> dict[str, str]`: Deletes state entry from SQLite.

### Frontend Modules (`ui/js/`)
- `ui/js/app.js`: `switchTab(tabName: str)`, `openInspector(forceOpen: bool = true)`, `closeInspector()`, `openDocumentReader({title, sourceType, htmlContent, markdownContent, filePath, anchor})` (renders 1200px max-width glass card, sets `body.zim-reader-open`, dismisses on backdrop click or Escape), `closeDocumentReader()`, `renderSafeMarkdown(text: str)` (safe markdown and KaTeX renderer with DOMPurify MathML/SVG whitelisting), `throttleWithRAF(fn)` (requestAnimationFrame drag/scroll throttling), `computeWsBackoffDelay(attempt)` (exponential WebSocket reconnect backoff), `escapeHTML(str: str)`, `extractAndApplyPalette(imgElement: HTMLImageElement)`, `updateThemeAndBackground()`, citation delegation on click (`data-source-type`, `data-file-path`, `data-page`).
- `ui/js/state.js`: `AppPersistence.fetchState()`, `AppPersistence.saveState()`, `AppPersistence.updateState(patch)`, `queueDebouncedStatePatch(key, val, delayMs=300)` (debounced state sync layer with immediate localStorage backup via `saveStateToLocalBackup`), tracks `dailyTargets.customGoals` for custom topic/subject study targets.
- `ui/js/quiz.js`: `initQuizSetup()`, `beginQuizRunner()`, `handleSelectOption(optLetter: str)`, `handleQuitQuiz()` (commits partial completions to test history and habit streaks without requiring full submission), `updateInspectorForQuestion(q)`, `renderTopicVerificationModalDOM()` (records and restores `scrollTop` position of `#topic-editor-content`), `openExplanationNoteModal(q)` (opens expanded Markdown/KaTeX explanation note modal `#explanation-note-modal`, dismissible via backdrop click or Escape), hotkey listeners (1-4, A-D), ms latency recording, separates FSRS rating controls (`#fsrs-rating-container`) and CSV explanation (`#feedback-container`) into distinct cards.
- `ui/js/hub.js`: `renderHub()`, `openTopicCarousel(topicKey: str)`, `openTopicCarouselForQuiz(topicKey: str)`, `openSessionReview(sessionId: str)`
- `ui/js/search.js`: `executeRaceSafeSearch(query: str)` (race-safe search with AbortController cancellation and 200ms debounce), `performGlobalSearch(query: str)`, `renderCentralSearchResults(results)`, `renderSearchFilterInspector()`
- `ui/js/queue.js`: `fetchQueueStatus()`, `renderQueue()`, `initQueueWebSocket()`
- `ui/js/vault.js`: `fetchSources()`, `renderSources()`, `updateProgressUI(percent, msg)`
- `ui/js/dashboard.js`: `initDashboard()`, `fetchDashboardPrompts()`, `renderBannerCarousel()` (rotates dynamic carousel of up to 10 banners: spaced repetition due reviews, NLP weak topics, 3+ day subject rotation prompts, and motivational messages with time-of-day/velocity adaptation and CSS glow animations), `handleBannerAction(banner)`, `startBannerAutoCycle()`, `startGradualBannerRotation()`, `renderRankEditor()`, `simulateRank()` (calculates Avg Rank across trailing 200 questions).
- `ui/js/expertise.js`: `initExpertiseMap()`
- `ui/js/habits.js`: `initHabits()`, renders heatmap intensity classes (`.heatmap-cell-0` to `.heatmap-cell-4`), custom daily target tracking (`dailyTargets.customGoals`).
- `ui/js/settings.js`: `initSettings()`, `loadDiagnostics()`, `loadScispacyModels()`, manual triggers `#btn-update-all-refs` and `#btn-update-all-summaries` for bulk reference and LM Studio summary updates.
- `ui/js/test_features.js`: `initTestFeatures()`, `renderPdfExtractorView()`, `renderDbQueriesView()`, multi-engine OCR dropdown routing (`auto`, `rapidocr`, `easyocr`).
- `ui/js/analysis.js`: `initAnalysisWorkspace()`, `loadFilesList()`, `loadPageData(file, page)`, `renderPageCanvas()`, `renderLayoutBoxes()`, `savePageAnnotations()`, `autoSave()`. Manages dual-pane visual workspace, interactive drag/resize via `ui/vendor/interact.min.js`, category retagging (`text`, `header`, `table`, `figure`), reading order flow, and 3-second debounced persistence.
- `ui/js/analysis_toc.js`: `openTocValidation(file_path)`, `initTocWorkspace()`, `loadTocHeadings()`, `renderHeadingsTree()`, `shiftHeadingTier(boxId, delta)`, `commitAndIngestDocument()`. Controls Table of Contents validation dual-pane screen, bounding box focus on canvas, nested heading tree manipulation (H1-H6), and LanceDB finalization trigger.
- `ui/js/reader.js`: `initReaderView()`, `openDocumentInReader(sourceType: str, path: str, pageOrAnchor: str)`, deep heading scrolling via `anchorId` / `anchor` resolving against `id="heading-..."`, and font-size controls (`.font-zoom-ctrl-group`).
- `ui/js/notes.js`: `MyNotesController`, `LectureNoteEditor`; manages 'My Notes' Lecture Topic Pad, 8 glassmorphic card shades, paginated note cards (12/page), quick MCQ launcher (`startCombinedTopicsQuiz`), <2ms optimistic Enter line-locking, double-click inline editor, and debounced Right Sidebar inspection.

---

## 4. Shared Data Models, Schemas & State

### SQLite Tables (`core/database.py`)
```sql
-- MCQ Bank: Primary question bank with FSRS v4.5 scheduling
mcq_bank (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT NOT NULL,
  source_tag TEXT DEFAULT 'General',
  question TEXT NOT NULL,
  opt_a TEXT NOT NULL,
  opt_b TEXT NOT NULL,
  opt_c TEXT NOT NULL,
  opt_d TEXT NOT NULL,
  correct_opt TEXT NOT NULL CHECK(correct_opt IN ('A', 'B', 'C', 'D')),
  csv_explanation TEXT,
  llm_explanation TEXT DEFAULT '',
  image_path TEXT,
  state INTEGER DEFAULT 0,
  difficulty REAL DEFAULT 0.0,
  stability REAL DEFAULT 0.0,
  reps INTEGER DEFAULT 0,
  lapses INTEGER DEFAULT 0,
  last_review REAL,
  due_timestamp REAL DEFAULT 0.0,
  created_at REAL DEFAULT (unixepoch('now'))
);
CREATE INDEX idx_mcq_due ON mcq_bank (due_timestamp, state);

-- CSV Questions & FTS5: Mass ingested questions + sub-ms fulltext search
csv_questions (
  id TEXT PRIMARY KEY,
  file_path TEXT,
  topic_name TEXT,
  question TEXT,
  opt_a TEXT,
  opt_b TEXT,
  opt_c TEXT,
  opt_d TEXT,
  correct_answer TEXT,
  explanation TEXT,
  llm_explanation TEXT DEFAULT '',
  state INTEGER DEFAULT 0,
  difficulty REAL DEFAULT 0.0,
  stability REAL DEFAULT 0.0,
  reps INTEGER DEFAULT 0,
  lapses INTEGER DEFAULT 0,
  last_review REAL,
  due_timestamp REAL DEFAULT 0.0
);
CREATE INDEX idx_csv_topic ON csv_questions(topic_name);
CREATE INDEX idx_csv_due ON csv_questions(due_timestamp, state);

csv_questions_fts USING fts5 (
  id UNINDEXED, question, opt_a, opt_b, opt_c, opt_d,
  content='csv_questions', content_rowid='rowid'
);
-- Maintained via triggers: csv_questions_ai, csv_questions_ad, csv_questions_au

-- Task Queue: Priority task queue (P1=Immediate, P2=Active Session, P3=Batch)
-- Note: mcq_id is TEXT without a strict mcq_bank FK to support both mcq_bank (int) and csv_questions (TEXT) IDs
task_queue (
  task_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_type TEXT NOT NULL,
  priority INTEGER DEFAULT 3,
  mcq_id TEXT,
  session_id TEXT,
  topic_name TEXT,
  payload TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  error_message TEXT,
  created_at REAL DEFAULT (unixepoch('now')),
  updated_at REAL DEFAULT (unixepoch('now'))
);
CREATE INDEX idx_task_priority ON task_queue (status, priority, task_id);
CREATE INDEX idx_task_session ON task_queue (session_id, status);
CREATE INDEX idx_task_dedup ON task_queue (task_type, topic_name, status);

-- Cached Syntheses: RAG topic dossiers and multi-source match indicators
cached_syntheses (
  topic_key TEXT PRIMARY KEY,
  display_title TEXT NOT NULL,
  enhanced_explanation TEXT,
  distractor_analysis TEXT,
  unified_article TEXT,
  variance_audit TEXT DEFAULT '',
  top_references TEXT,
  has_obsidian_note INTEGER DEFAULT 0,
  has_pdf_match INTEGER DEFAULT 0,
  has_zim_match INTEGER DEFAULT 0,
  created_at REAL DEFAULT (unixepoch('now')),
  updated_at REAL DEFAULT (unixepoch('now'))
);

-- Configured Offline Knowledge Sources
knowledge_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  path TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  itemCount INTEGER DEFAULT 0,
  lastIndexed TEXT DEFAULT 'Never',
  pdfEngine TEXT DEFAULT 'fitz'
);

-- Quiz Sessions & Knowledge Hub Cards
quiz_sessions (
  id TEXT PRIMARY KEY,
  subject_name TEXT NOT NULL,
  custom_title TEXT DEFAULT '',
  date TEXT NOT NULL,
  timestamp REAL NOT NULL,
  last_attempted REAL DEFAULT 0.0,
  total_questions INTEGER NOT NULL,
  correct_answers INTEGER NOT NULL DEFAULT 0,
  wrong_answers INTEGER NOT NULL DEFAULT 0,
  unanswered INTEGER NOT NULL DEFAULT 0,
  score INTEGER NOT NULL DEFAULT 0,
  max_score INTEGER NOT NULL DEFAULT 0,
  accuracy REAL NOT NULL DEFAULT 0.0,
  time_limit_seconds INTEGER,
  time_used_seconds INTEGER NOT NULL DEFAULT 0,
  mode TEXT NOT NULL DEFAULT 'learn',
  is_completed INTEGER DEFAULT 0,
  topics_reviewed INTEGER DEFAULT 0,
  refs_status TEXT DEFAULT 'none',
  llm_status TEXT DEFAULT 'none',
  topic_list_json TEXT DEFAULT '[]',
  created_at REAL DEFAULT (unixepoch('now'))
);

-- Session Questions: Individual MCQ Records + LLM Detailed Explanations
session_questions (
  session_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  selected_option TEXT,
  is_correct INTEGER NOT NULL DEFAULT 0,
  timed_out INTEGER NOT NULL DEFAULT 0,
  time_spent_ms INTEGER NOT NULL DEFAULT 0,
  timestamp REAL NOT NULL,
  question_text TEXT,
  opt_a TEXT,
  opt_b TEXT,
  opt_c TEXT,
  opt_d TEXT,
  correct_answer TEXT,
  explanation TEXT,
  llm_explanation TEXT DEFAULT '',
  topic TEXT,
  question_topics_json TEXT DEFAULT '[]',
  PRIMARY KEY (session_id, question_id),
  FOREIGN KEY(session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE
);
CREATE INDEX idx_session_questions_sid ON session_questions (session_id);

-- Extracted Topics per Question
extracted_topics (
  question_id TEXT NOT NULL,
  topic_name TEXT NOT NULL,
  is_main_topic INTEGER DEFAULT 0,
  verified INTEGER DEFAULT 0,
  selected INTEGER DEFAULT 1,
  PRIMARY KEY (question_id, topic_name)
);
CREATE INDEX idx_extracted_topics_qid ON extracted_topics (question_id);

-- Permanent Ignore List
ignored_topics (
  topic_norm TEXT PRIMARY KEY,
  original_text TEXT NOT NULL,
  created_at REAL DEFAULT (unixepoch('now'))
);

-- ZIM Title Lookup Cache
zim_titles (
  title TEXT PRIMARY KEY
);

-- Persistent User & App State
user_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at REAL DEFAULT (unixepoch('now'))
);

-- Layout Analysis Progress (PDF Layout & Page State Tracking)
layout_analysis_progress (
  file_path TEXT PRIMARY KEY,
  status TEXT DEFAULT 'pending',
  total_pages INTEGER DEFAULT 0,
  reviewed_pages_json TEXT DEFAULT '[]',
  active_profile TEXT DEFAULT 'native_digital',
  final_markdown_override TEXT DEFAULT NULL,
  updated_at REAL DEFAULT (unixepoch('now'))
);

-- User Annotated Bounding Boxes
user_annotated_bboxes (
  id TEXT PRIMARY KEY,
  file_path TEXT NOT NULL,
  page_number INTEGER NOT NULL,
  x0 REAL,
  y0 REAL,
  x1 REAL,
  y1 REAL,
  element_type TEXT NOT NULL,
  heading_level INTEGER DEFAULT 0,
  display_order INTEGER NOT NULL,
  raw_text TEXT DEFAULT '',
  is_custom_drawn INTEGER DEFAULT 0,
  updated_at REAL DEFAULT (unixepoch('now')),
  FOREIGN KEY(file_path) REFERENCES layout_analysis_progress(file_path) ON DELETE CASCADE
);
CREATE INDEX idx_annotated_bboxes_page ON user_annotated_bboxes (file_path, page_number);

-- PDF Topic Index (Full-PDF Hierarchical Section & Topic Index)
pdf_topic_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pdf_path TEXT NOT NULL,
  pdf_title TEXT NOT NULL,
  engine_source TEXT NOT NULL DEFAULT 'hybrid_toc',
  heading_level INTEGER NOT NULL,
  topic_title TEXT NOT NULL,
  topic_norm TEXT NOT NULL,
  parent_topic TEXT NOT NULL,
  parent_norm TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  heading_path_norm TEXT NOT NULL,
  start_page INTEGER NOT NULL,
  end_page INTEGER NOT NULL,
  start_y0 REAL NOT NULL DEFAULT 0.0,
  end_y1 REAL NOT NULL DEFAULT 1.0,
  updated_at REAL DEFAULT (unixepoch('now'))
);
CREATE INDEX idx_pti_topic_norm ON pdf_topic_index(topic_norm);
CREATE INDEX idx_pti_parent_norm ON pdf_topic_index(parent_norm);
CREATE INDEX idx_pti_pdf_path ON pdf_topic_index(pdf_path);

-- Fast Question Metadata & Attempt Tracking
question_metadata (
  question_id TEXT PRIMARY KEY,
  attempt_count INTEGER DEFAULT 0,
  wrong_count INTEGER DEFAULT 0,
  last_is_correct INTEGER DEFAULT 1,
  last_attempt_ts REAL DEFAULT 0.0,
  verified_topics_json TEXT DEFAULT '[]',
  cached_references_json TEXT DEFAULT '{}',
  cached_llm_summary TEXT DEFAULT '',
  updated_at REAL DEFAULT (unixepoch('now'))
);
CREATE INDEX idx_qm_attempt ON question_metadata(attempt_count);

-- Obsidian Notes: Cached Obsidian markdown note graph and clinical section indexing
obsidian_notes (
  note_id TEXT PRIMARY KEY,
  book_name TEXT NOT NULL,
  category TEXT NOT NULL,
  file_name TEXT NOT NULL,
  raw_title TEXT NOT NULL,
  clean_title TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  abs_path TEXT NOT NULL,
  is_hub_index INTEGER DEFAULT 0,
  headings_json TEXT DEFAULT '[]',
  child_links_json TEXT DEFAULT '[]',
  assets_json TEXT DEFAULT '[]',
  parent_note_ids_json TEXT DEFAULT '[]',
  parent_titles_json TEXT DEFAULT '[]',
  breadcrumb_path TEXT DEFAULT '',
  contextual_search_text TEXT DEFAULT '',
  content_markdown TEXT DEFAULT '',
  created_at REAL DEFAULT (unixepoch('now')),
  updated_at REAL DEFAULT (unixepoch('now'))
);
CREATE INDEX idx_obs_clean_title ON obsidian_notes (clean_title);
CREATE INDEX idx_obs_book ON obsidian_notes (book_name);

-- User Lecture Notes: 'My Notes' Lecture Topic Pad cards
user_lecture_notes (
  note_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT 'Untitled Note',
  shade_index INTEGER DEFAULT 0,
  is_pinned INTEGER DEFAULT 0,
  created_at REAL DEFAULT (unixepoch('now')),
  updated_at REAL DEFAULT (unixepoch('now'))
);
CREATE INDEX idx_uln_updated ON user_lecture_notes (is_pinned DESC, updated_at DESC);

-- User Lecture Note Items: Structured clinical topic line items within a lecture note
user_lecture_note_items (
  item_id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL,
  line_order INTEGER NOT NULL DEFAULT 0,
  topic_text TEXT NOT NULL,
  topic_key TEXT NOT NULL,
  mcq_count INTEGER DEFAULT 0,
  ref_count INTEGER DEFAULT 0,
  obsidian_count INTEGER DEFAULT 0,
  pdf_count INTEGER DEFAULT 0,
  zim_count INTEGER DEFAULT 0,
  references_json TEXT DEFAULT '{}',
  updated_at REAL DEFAULT (unixepoch('now')),
  FOREIGN KEY(note_id) REFERENCES user_lecture_notes(note_id) ON DELETE CASCADE
);
CREATE INDEX idx_ulni_note_order ON user_lecture_note_items (note_id, line_order ASC);
CREATE INDEX idx_qm_wrong ON question_metadata(wrong_count);
CREATE INDEX idx_qm_last_attempt ON question_metadata(last_attempt_ts);
```

### LanceDB Index Schema (`parsers/lancedb_engine.py`)
- **Table Name**: `medical_knowledge` (located at `LANCEDB_PATH`)
- **PyArrow Schema**:
  ```python
  pa.schema([
      pa.field("id", pa.string()),
      pa.field("vector", pa.list_(pa.float32(), vector_dim)),  # 384 (MiniLM) or 768 (SapBERT)
      pa.field("source_article", pa.string()),
      pa.field("file_path", pa.string()),
      pa.field("main_topic", pa.string()),
      pa.field("subheading", pa.string()),
      pa.field("heading_path", pa.string()),
      pa.field("chunk_index", pa.int32()),
      pa.field("page_number", pa.int32()),
      pa.field("article_length", pa.int32()),
      pa.field("parent_content", pa.string()),
      pa.field("child_content", pa.string()),
  ])
  ```
  *(Note: Output search dicts normalize `child_content -> text` & `snippet`, `subheading -> heading` & `section_title`, `source_article -> title`, `page_number -> page`, `article_length -> doc_length`, and `source="pdf"` to strictly satisfy `ChunkDict`).*

### Tantivy Inverted Index Schema (`parsers/tantivy_engine.py`)
- **Path**: `INDEX_PATH`
- **Fields**:
  - `doc_id`: `TEXT | STORED`
  - `source_type`: `TEXT | STORED` (`"obsidian"` | `"pdf"`)
  - `file_path`: `TEXT | STORED`
  - `page_number`: `I64 | STORED`
  - `title`: `TEXT | STORED | tokenizer="en_stem"`
  - `heading_path`: `TEXT | STORED | tokenizer="en_stem"`
  - `doc_length`: `I64 | STORED`
  - `snippet`: `TEXT | STORED | tokenizer="en_stem"`
  - `document_body`: `TEXT | UNSTORED | tokenizer="en_stem"`
  - `obsidian_body`: `TEXT | STORED | tokenizer="en_stem"`

### Search & Internal Dict Contracts
- `ChunkDict`:
  ```python
  {
      "id": str,
      "doc_id": str,                    # Interop alias for id
      "text": str,                      # Raw passage text used by FlashRank reranker (MANDATORY)
      "snippet": str,                   # Snippet preview (<400-800 chars)
      "title": str,                     # Article title or textbook name
      "heading": str,                   # Section title or hierarchical heading path
      "heading_path": str,              # Full heading hierarchy
      "source": "obsidian" | "pdf" | "zim",
      "file_path": str,
      "page": int,                      # 1-based page number
      "page_number": int,               # 1-based page number (duplicate for safe interop)
      "doc_length": int,                # Total character length of parent document
      "article_length": int,            # Interop duplicate for doc_length
      "score": float,                   # Composite BM25 + depth score or LanceDB similarity
      "rerank_score": float,            # FlashRank cross-encoder output score
      "parent_id": str | None,
  }
  ```
- `SearchResultsDict` (Tantivy `search_index()` output / RAG input / `/api/search` grouped return):
  ```python
  {
      "obsidian": list[ChunkDict],
      "pdf": list[ChunkDict],
      "zim": list[ChunkDict],           # Empty list stub from Tantivy; populated by parsers.zim_reader
      "lancedb": list[ChunkDict],       # Dynamic vector search group
  }
  ```
- `TopicExtractorOutputDict` (Output of `extract_topics_local()`):
  ```python
  {
      "main_topic": str,
      "all_topics": list[str],
      "topic_details": list[{
          "name": str,
          "topic_name": str,            # Contract match for extracted_topics table
          "is_main_topic": int,         # 1 or 0
          "verified": int,              # 1 or 0
          "selected": int,              # 1 or 0
          "confidence": "high" | "low",
          "semantic_category": "disease" | "microorganism" | "anatomy" | "drug_chemical" | "procedure" | "unknown",
          "cui": str,
          "score": float,
      }],
  }
  ```
- `McqContextDict` (MCQ payload passed into RAG synthesis):
  ```python
  {
      "question": str,
      "options": dict[str, str],        # e.g., {"A": "...", "B": "...", "C": "...", "D": "..."}
      "correct_answer": str,            # "A" | "B" | "C" | "D"
      "explanation": str,               # Source CSV explanation
  }
  ```
- `SynthesisOutputDict` (Returned by `run_tema_q_synthesis()` -> stored in `cached_syntheses` & Jinja reporter):
  ```python
  {
      "topic_key": str,
      "display_title": str,
      "enhanced_explanation": str,
      "distractor_analysis": str,
      "unified_article": str,
      "variance_audit": str,
      "top_references": list[ChunkDict],
  }
  ```

### Key API DTOs & JSON Payloads
- **Dashboard Prompt Banner DTO** (`server/routes_sessions.py` -> `ui/js/dashboard.js`):
  ```json
  {
    "id": "weak_topic_kawasaki_disease",
    "type": "weak_topic | due_review | switch_subject | encouragement",
    "title": "Target Weak Topic: Kawasaki Disease 🎯",
    "message": "You've missed 3 questions related to Kawasaki Disease. A focused review will solidify this concept!",
    "icon": "🎯",
    "badge": "High Yield Weak Area",
    "action": "practice_topic | review_due | switch_subject",
    "action_label": "Practice Kawasaki Disease →",
    "topic": "Kawasaki Disease",
    "subject": "Pediatrics",
    "session_id": "sess_spaced_repetition_due"
  }
  ```
- **Due Repetition Summary DTO** (`GET /api/sessions/due_summary`):
  ```json
  {
    "status": "success",
    "total_due": 12,
    "session_id": "sess_spaced_repetition_due"
  }
  ```
- `SearchRequest`:
  ```python
  {
      "query": str,
      "limit": int = 10,                # Range: 1 to 100
      "source_type": str = "all",       # "all" | "obsidian" | "pdf" | "zim"
      "enable_rerank": bool = True,     # Cross-encoder toggle
  }
  ```
- `TopicExtractionPayload`:
  ```python
  {
      "mcq_text": str = "",
      "question": Optional[str] = None,
      "options": Optional[Union[dict[str, str], list[str], Any]] = None,
      "explanation": str = "",
      "strategy": Optional[str] = None,
      "use_zim_refinement": bool = False,
      "use_tantivy_refinement": bool = False,
  }
  ```
- `ScorePayload`:
  ```python
  {
      "mcq_id": int | str,
      "rating": int,                    # 1=Again, 2=Hard, 3=Good, 4=Easy (FSRS v4.5)
      "session_id": Optional[str] = None,
  }
  ```
- `BatchTopicRequest`:
  ```python
  {
      "topics": list[str],
      "mode": str = "missing",          # "missing" | "all"
  }
  ```
- `TopicStatusRequest`:
  ```python
  {
      "topics": list[str],
  }
  ```
- `GenerateSetRequest`: `{ "subject": str, "topics": list[str], "count": int, "mode": str }`
- `AnswerRecord`:
  ```python
  {
      "question_id": str,
      "selected_option": Optional[str] = None,
      "is_correct": bool = False,
      "timed_out": bool = False,
      "time_spent_ms": int = 0,
      "timestamp": Optional[float] = None,
  }
  ```
- `SessionCreate`:
  ```python
  {
      "id": str,
      "subject_name": str = "Mixed Topics",
      "custom_title": Optional[str] = "",
      "date": Optional[str] = None,
      "timestamp": Optional[float] = None,
      "total_questions": int = 0,
      "correct_answers": int = 0,
      "wrong_answers": int = 0,
      "accuracy": float = 0.0,
      "mode": str = "learn",
      "is_completed": Optional[bool] = None,
      "topics_reviewed": bool = False,
      "topic_list": Optional[list[Any]] = None,
      "questions": list[dict[str, Any]] = [],
      "answers": list[AnswerRecord] = [],
  }
  ```
- `InitializeAnalysisRequest`:
  ```python
  {
      "file_path": str,                 # Absolute or relative path to PDF file
      "profile_id": Optional[str] = "native_digital",  # Layout profile key
  }
  ```
- `BBoxItem`:
  ```python
  {
      "id": Optional[str] = None,       # Unique ID for bounding box
      "x0": float,                      # Left coordinate
      "y0": float,                      # Top coordinate
      "x1": float,                      # Right coordinate
      "y1": float,                      # Bottom coordinate
      "element_type": str,              # "text" | "header" | "table" | "figure"
      "heading_level": Optional[int] = 0, # 1-6 if header, else 0
      "display_order": int,             # Sequential reading order index
      "raw_text": Optional[str] = "",   # Extracted or edited text
      "is_custom_drawn": Optional[int] = 0, # 1 if created manually by user, 0 otherwise
  }
  ```
- `SavePageRequest`:
  ```python
  {
      "file_path": str,
      "page_number": int,               # 1-based page number (ge=1)
      "bboxes": list[BBoxItem],         # Active bounding boxes for this page
  }
  ```
- `SaveMarkdownRequest`:
  ```python
  {
      "file_path": str,
      "markdown_text": str,             # Markdown text override
  }
  ```
- `UpdateHeadingRequest`:
  ```python
  {
      "id": str,                        # Bounding box ID
      "heading_level": int,             # Heading tier level (1 <= ge <= 6)
  }
  ```
- `FinalizeAnalysisRequest`:
  ```python
  {
      "file_path": str,
      "export_to_lancedb": bool = True, # Ingest compiled markdown into LanceDB
      "output_filename": Optional[str] = None,
  }
  ```
- `UpdateSessionTopicsRequest`:
  ```python
  {
      "topic_list": list[dict[str, Any]],
      "confirm": bool = False,
      "topics_reviewed": Optional[bool] = None,
  }
  ```
- `DeleteSessionsRequest`:
  ```python
  {
      "session_ids": list[str],
  }
  ```
- `SetActiveModelPayload`: `{ "model_type": "embedding" | "reranker" | "scispacy", "model_id": str }`
- `FSRS Stats Dict`: `{ "state": int, "difficulty": float, "stability": float, "reps": int, "lapses": int, "interval_seconds": int }`

### SQLite Enum Values & Serialized JSON Fields
- `task_queue.task_type`:
  - *Canonical*: `"SYNTHESIZE_TOPIC" | "EXPLAIN_MCQ" | "PROCESS_SESSION" | "BATCH_PRECOMPUTE"`
  - *Legacy Compatibility Aliases*: `"SESSION_PROCESSING" | "SESSION_EXPLAIN" | "MCQ_EXPLAIN" | "synthesis"` (normalized/accepted by `PriorityQueueWorker`)
- `task_queue.status`: `"pending" | "processing" | "completed" | "failed" | "ignored"`
- `mcq_bank.state` / `csv_questions.state` (FSRS v4.5): `0=New, 1=Learning, 2=Review, 3=Relearning`
- `cached_syntheses.top_references` (JSON): `list[{ "title": str, "source_type": str, "file_path": str, "page": int, "snippet": str }]`
- `quiz_sessions.topic_list_json` (JSON): `list[str]`
- `session_questions.question_topics_json` (JSON): `list[str]`
- `user_state.value` (JSON/string): Key-value store payloads

### WebSocket Message Payloads (`server/ws_manager.py` -> `ui/js/*.js`)
- `task_update`: `{ "type": "task_update", "task_id": int, "status": str, "topic": str, "session_id": str | None, "progress": int }`
- `session_update`: `{ "type": "session_update", "session_id": str, "refs_status": str, "llm_status": str }`
- `indexing_progress`: `{ "type": "indexing_progress", "source_id": str, "percent": int, "message": str, "status": "running" | "done" | "error" }`

### Frontend Global State & CSS Variables (`ui/`)
- `AppPersistence` (`user_state` keys):
  - `theme`: `"light" | "dark"`
  - `activeWallpaper`: `str` (filename or path)
  - `llmEnabled`: `bool`
  - `selectedEmbeddingModel`: `str`
  - `flashrankModel`: `str`
  - `dailyGoal`: `int`
  - `streakData`: `dict`
- Dynamic CSS Custom Properties (`ui/css/main.css` across `:root` and `body.dark-theme`):
  - Theme neutrals: `--c-neutral-100`, `--c-neutral-200`, `--c-neutral-300`, `--c-neutral-400`, `--c-neutral-500`
  - Glass tokens: `--bg-primary`, `--bg-glass`, `--text-primary`, `--text-muted`, `--accent-primary`, `--accent-secondary`, `--border-glass`
  - Segmented controls: `--segmented-bg`, `--segmented-border`, `--segmented-active-bg`
  - Code & readers: `--code-bg`, `.modal-backdrop`, `.glass-card`, `body.zim-reader-open`
  - High-contrast card overrides on panels and badges ensuring WCAG AA compliance across light and dark themes.
- UI Interaction Controls:
  - Segmented button groups: `.segmented-group` and `.segmented-btn` with `role="radiogroup"` / `role="radio"` retain underlying form `<select>` elements for seamless two-way state synchronization.
  - ZIM Document Reader modal: `#zim-viewer-container` renders articles up to 1200px wide within `.glass-card` on a translucent backdrop, isolates background scrolling via `body.zim-reader-open`, and dismisses upon clicking outside or pressing Escape.
  - Explanation Note Modal: `#explanation-note-modal` presents expanded Markdown/KaTeX explanation notes on clicking the review explanation card, closing on backdrop click or Escape.
- Citation Click Delegation:
  - Elements with `.citation-link` carry `data-source-type="pdf"|"obsidian"|"zim"`, `data-file-path="..."`, `data-page="..."`. Delegated at `document` level in `ui/js/app.js`.

---

## 5. Cross-File Dependency & Data Flow Map

```
1. Quiz MCQ Execution & FSRS Scoring Pipeline:
   ui/js/quiz.js -> POST /api/mcq/score -> core/scheduler.py (calculate_fsrs_next_interval)
     └── mutates -> SQLite (mcq_bank / csv_questions due_timestamp, stability, difficulty, reps)

2. Multi-Stage Hybrid RAG Search Pipeline:
   ui/js/search.js -> POST /api/search -> server/routes_search.py
     ├── agents/tema_q/topic_extractor.py (expand_aliases & layperson_synonyms)
     ├── parsers/zim_reader.py (expand_query_via_zim & direct search_zim_articles)
     ├── parsers/tantivy_engine.py (BM25 lexical lookup over INDEX_PATH + depth scoring)
     ├── parsers/lancedb_engine.py + parsers/embedding_engine.py (Dense CPU vector lookup + FTS fallback)
     ├── Single-File Deduplication (atomic page/text/heading sync to highest base score hit)
     ├── agents/tema_q/topic_extractor.py (filter_sibling_topics)
     └── parsers/reranker.py (FlashRank cross-encoder reranking on candidate['text'])
           └── Return Results JSON (ChunkDict & SearchResultsDict) -> ui/js/search.js

3. Asynchronous RAG Synthesis & Queue Pipeline:
   server/routes_queue.py / routes_sessions.py -> Enqueue Task into SQLite task_queue (status='pending')
     └── core/queue_manager.py (PriorityQueueWorker loop)
           ├── parsers/tantivy_engine.py & lancedb_engine.py (Retrieve topic references)
           ├── agents/tema_q/pipeline.py (Pass 1 Clinical + Pass 2 Audit + Distractor Analysis via LM Studio)
           ├── agents/tema_q/auditor.py (Detect discrepancies, staging variances & merge JSON subtopic nodes)
           ├── core/reporter.py (Generate Jinja2 HTML report in storage/reports)
           ├── mutates -> SQLite (cached_syntheses including variance_audit)
           └── server/ws_manager.py (Broadcast WS notification to ui/js/queue.js & hub.js)

4. Theme & Palette Dynamic Engine:
   ui/js/app.js (fetchWallpapersList) -> GET /api/sources/wallpapers -> Load Canvas
     └── extractAndApplyPalette() -> Calculates HSL hues, contrast ratios, luminance -> updates CSS variables on :root

5. Spaced Repetition Due Review & Dashboard Prompt Carousel:
   ui/js/dashboard.js -> GET /api/sessions/dashboard_prompts & GET /api/sessions/due_summary
     ├── server/routes_sessions.py (sync_consolidated_due_session) -> Checks due_timestamp <= unixepoch('now')
     │     └── Excludes questions wrong within 6h cooldown (last_review > now - 21600)
     ├── server/routes_sessions.py (Computes weak topics via NLP-extracted topics + subject switches)
     └── ui/js/dashboard.js (renderBannerCarousel) -> Rotates interactive carousel with direct quiz runner launch

6. In-App Document Reader & ZIM Article Viewer:
   Citation Click / Knowledge Hub -> ui/js/app.js (openDocumentReader)
     ├── GET /api/notes/zim-article -> parsers/zim_reader.py (get_zim_article_html)
     │     ├── Scopes inside <div class="mw-parser-output">
     │     ├── Rewrites media and SVG embeds (<image>, <object>, <embed>, <source>)
     │     └── Sanitizes inline styling on MediaWiki navboxes
     ├── DOMPurify.sanitize() with full SVG & table tags -> Mounts into #zim-content-area
     └── Applies body.zim-reader-open, expands to 1200px max-width, dismisses on backdrop click or Escape

7. PDF Visual Layout Analysis & Ingestion Pipeline:
   ui/js/analysis.js -> POST /api/analysis/initialize -> SQLite layout_analysis_progress
     ├── GET /api/analysis/page-data -> parsers/indra/engine.py (IndraModularEngine fallback chain)
     │     └── Renders base64 canvas + interactive SVG overlays (.bbox-overlay-element via interact.js)
     ├── User drags/resizes bboxes, edits categories/reading order -> 3-sec debounced save
     │     └── POST /api/analysis/save-page -> Batched SQLite transaction into user_annotated_bboxes
     ├── Finish Layout Review -> ui/js/analysis_toc.js -> GET /api/analysis/toc
     │     └── parsers/indra/document.py (DocumentHeadingTracker) -> Generates nested H1-H6 heading tree
     ├── User adjusts heading tiers (Arrow keys / buttons) -> POST /api/analysis/update-heading
     └── Commit & Ingest -> POST /api/analysis/finalize
           ├── Compiles verified document Markdown
           ├── parsers/docling/engine.py (ingest_to_lancedb) -> LanceDB vector & FTS table
           └── Updates layout_analysis_progress status='completed' -> Vault UI displays green checkmark

8. Obsidian Markdown Vault Graph & Section Navigation:
   ui/js/reader.js -> GET /api/notes/obsidian-note -> parsers/obsidian_reader.py
     ├── ObsidianVaultGraph index check -> caches into SQLite (obsidian_notes)
     ├── Strips YAML frontmatter (\A---
?
...
?
---) preserving internal markdown dividers
     ├── convert_obsidian_markdown_to_html_ready() -> rewrites wikilinks to .obsidian-wikilink anchors
     ├── Resolves section heading slugs (heading_slug) for deep scroll navigation
     └── In-app reader mounts rendered DOM with font-size controls and citation delegation

9. 'My Notes' Lecture Topic Pad & Quick Quiz Launch:
   ui/js/notes.js -> GET /api/notes/lecture-notes -> Render paginated glassmorphic note cards
     ├── Enter key -> Optimistic line-locking (<2ms) -> POST /api/notes/lecture-notes/{note_id}/items
     │     └── Server queries SQLite (mcq_bank, csv_questions, obsidian_notes, pdf_topic_index, zim_titles)
     │     └── Caches reference counts and returns populated topic item
     ├── Single click on line item -> Debounced Right Sidebar Reference Inspector update
     ├── Double click on note title -> LectureNoteEditor inline title editing
     └── Click 'Do MCQ' button -> window.startCombinedTopicsQuiz(topics, noteTitle) -> Launches quiz session across all contained topics
```

---

## 6. Active Features & Architectural Invariants

### Architectural Invariants
1. **Backend VRAM Baseline & Selective GPU Acceleration Policy**: By default, `CUDA_VISIBLE_DEVICES=""` is enforced at application startup and across general modules to guarantee 100% VRAM headroom (4.2–4.8 GB) for local LM Studio GGUF model execution. High-yield, throughput-critical workloads (specifically Indra OCR line recognition and reading order ONNX inference, Docling Heron visual layout parsing, and heavy offline batch embeddings during large PDF vault indexing) may selectively utilize GPU execution if and only if: (a) explicit config/environment activation is present (`ENABLE_BACKEND_GPU=1`), (b) CUDA memory allocations are strictly capped via ONNX Runtime session options (`gpu_mem_limit <= 1.2 GB`), and (c) any CUDA initialization error, OOM exception, or concurrent LLM synthesis pass triggers immediate, seamless in-process fallback to CPU execution without corrupting worker threads or dropping tasks.
2. **Main Thread DLL Pre-warming**: `onnxruntime`, `lancedb`, and `tantivy` MUST be imported at the very top of `server/app.py` to prevent Windows Access Violation segfaults.
3. **Database Concurrency, Cache Caps & Migration Ordering**:
   - Every SQLite connection returned by `get_db_connection()` MUST apply connection-scoped PRAGMAs (`journal_mode = WAL`, `busy_timeout = 20000`, `foreign_keys = ON`, `synchronous = NORMAL`, `mmap_size = 536870912`, `temp_store = MEMORY`, `cache_size = -64000`).
   - Primary SQLite MMAP MUST be capped at 512 MB (`536870912`) and `ontology_kb.db` MMAP at 256 MB (`268435456`) so the entire Python process (including all cached CPU ML singletons) stays strictly below the **3.5 GB total RAM ceiling**.
   - Schema initialization (`initialize_database()`) MUST execute column additions (`_safe_add_column`) and legacy `task_queue` FK removal (`_migrate_task_queue_schema`) BEFORE executing `CREATE INDEX IF NOT EXISTS`, and MUST inspect `csv_questions_fts_docsize` (not `csv_questions_fts`) when checking external-content FTS5 synchronization.
4. **Token Caps**: Prompts to LM Studio MUST be tiktoken-bounded to 4096 tokens total context length via `truncate_to_token_limit`.
5. **DOM & Security Safety**: Frontend vanilla JS MUST NEVER raw-interpolate user strings into `innerHTML`; use `escapeHTML()`, `DOMPurify.sanitize()`, or DOM element construction.
6. **External Readers Interop & Path Safety**: SumatraPDF invocation MUST validate `.pdf` file extensions and use `-reuse-instance` and `-page <num>`. Obsidian URI invocation MUST use `obsidian://open?vault=...&file=...`.
7. **Offline Asset Self-Containment**: Vendor scripts (Tailwind, Marked, DOMPurify, KaTeX, interact.js) MUST be saved under `ui/vendor/` and automatically checked and downloaded at startup using atomic temp-file replacement (`config.ensure_offline_vendor_assets()`) to avoid 0-byte file corruption and guarantee complete offline execution.
8. **Lazy Search Singletons, Active Model Switching & Vector Dimension Sync**: Embedding models, LanceDB instances, FlashRank rerankers, and Docling converter instances MUST be cached singletons; never re-instantiate them per query to ensure search latency stays < 250ms on CPU. Active model switching (`POST /api/models/set_active`) MUST persist choices to `user_state` and clear in-memory caches via `set_configured_embedding_model()`, `set_configured_flashrank_model()`, and `set_configured_scispacy_model()`. LanceDB table vector dimensions (`384` vs `768`) MUST match `get_embedding_engine().get_dimension()` and trigger `reset_table()` if the configured embedding model dimension changes.
9. **Cross-Encoder Contract**: Any search candidate passed to `parsers/reranker.py` MUST contain an explicit `"text"` key containing the passage body.
10. **Tantivy Search Result Inspection**: Tantivy search returns a mapping `{"obsidian": [...], "pdf": [...], "zim": []}` (where `"zim"` is an empty list stub populated downstream by `parsers.zim_reader`). Check `any(len(v) > 0 for v in hits.values())` rather than `bool(hits)`.
11. **ZIM Contract & Cache Protection**: All search hits from `parsers.zim_reader` must satisfy `ChunkDict` (including `source="zim"`, `text`, `id`, `heading_path`, `page_number`). `get_zim_asset` must return a polyfill (`ZimAsset`) supporting both `tuple[bytes, str]` unpacking and dictionary field lookup. Extracted ZIM HTML MUST be scoped inside `<div class="mw-parser-output">`.
12. **Single-File Deduplication Metadata Integrity**: When merging chunks belonging to the same physical file or ZIM entry, `page`, `page_number`, `heading`, `text`, and `snippet` MUST be synchronized atomically to the highest base-scoring passage to avoid desynchronized page citations in downstream document readers.
13. **Reference Resolution Polymorphism & Safe Inspection**: Endpoints, background workers, and dossier report generators consuming `cached_syntheses.top_references` or search hits (`get_synthesis`, `get_topics_status`, `batch_generate_references`, `generate_topic_report`) MUST inspect references polymorphically across dictionaries (`SearchResultsDict`), lists (`list[ChunkDict]`), or null values without assuming a dictionary structure or calling `.values()`, preventing 500 runtime crashes during RAG report lookups.
14. **Queue Synthesis Deduplication**: Background topic synthesis triggers (`batch_generate_explanations`, `session_processor`, `prepare_questions`) MUST check `task_queue` for existing active tasks (`status IN ('pending', 'processing')`) under the canonical `task_type = 'SYNTHESIZE_TOPIC'` (assisted by `idx_task_dedup`) before enqueuing to prevent duplicate task flooding.
15. **Cascade-Safe Parent Upsert Integrity**: SQLite tables referenced by child tables with `FOREIGN KEY ... ON DELETE CASCADE` (specifically `quiz_sessions` referencing `session_questions`) MUST execute `INSERT ... ON CONFLICT(id) DO UPDATE SET` rather than `INSERT OR REPLACE` to prevent unintended cascade-deletion of associated questions when saving sessions or updating metadata.
16. **Lifespan Non-Blocking Startup/Teardown & Subsystem Cleanup**: FastAPI startup MUST bind the running event loop to `ws_manager` and start `queue_worker.start()`. During shutdown, all thread-blocking background workers (`queue_worker.stop()`) MUST execute off the main asyncio loop via `asyncio.to_thread` to prevent event-loop lockups during process termination, and shared persistent network clients (e.g. `httpx.AsyncClient` in `agents.tema_q.pipeline`) must be explicitly closed via `close_http_client()` to prevent socket resource leaks.
17. **Tantivy Index Lock & Resource Safety**: Tantivy index writers MUST be isolated from concurrent indexing tasks via an asynchronous lock (`_INDEXING_LOCK`), commits MUST be offloaded to separate threads (`asyncio.to_thread(writer.commit)`), and all exception pathways MUST explicitly release or discard the writer to prevent lingering `.lock` files on `INDEX_PATH`.
18. **WebSocket Thread & Loop Boundary Isolation**: Background worker threads dispatching live WebSocket notifications MUST use thread-safe dispatchers (`asyncio.run_coroutine_threadsafe`) targeting the primary FastAPI event loop, serialize writes per socket connection, construct WebSocket URLs using `window.location.protocol` (`wss://` or `ws://`) and `window.location.host`, and conform verbatim to canonical message schemas (`type="task_update"`, `type="session_update"`, `type="indexing_progress"`).
19. **UTF-8 BOM and Code Block Protection in Markdown Parsers**: Obsidian markdown parsers MUST decode using `utf-8-sig` to automatically strip Windows UTF-8 Byte Order Marks, and track fenced code block boundaries (` ``` ` / `~~~`) to prevent code comments (e.g., `# python comment`) from being falsely parsed as document section headings.
20. **Chunked Database Ingestion & Transaction Boundaries**: Large dataset ingestion operations (e.g. `csv_questions` mass loading) MUST commit records in discrete batches (`BATCH_SIZE <= 1000`) rather than holding long-lived write transactions, preventing SQLite database locks (`busy_timeout`) on concurrent quiz and search worker threads.
21. **HTML Shell & Deferred Script Integrity**: The application HTML shell file `ui/index.html` serves as the primary SPA entry point. All vendor and application JavaScript files MUST be included with `defer` attributes, interactive buttons MUST use explicit `type="button"`, and theme persistence MUST initialize immediately via an inline IIFE reading `localStorage.getItem("theme")`.
22. **Docling & Indra Offline Execution Safety**: Layout and OCR parsers (`parsers/docling/` and `parsers/indra/`) MUST set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` (plus `INDRA_OCR_MODEL_DIR` for Indra) when local model weights are detected, enforcing 100% offline execution without online Hugging Face Hub hangs.
23. **Non-Collapsing Inspector on Background Topic Extraction**: In `ui/js/app.js` and `ui/js/quiz.js`, `openInspector(forceOpen)` defaults `forceOpen` to `true`. When called with `forceOpen = false` during automatic background updates (`updateInspectorForQuestion`), inspector content MUST update without reopening the right sidebar if it was collapsed by the user.
24. **PDF Layout Analysis & Bounding Box Transaction Isolation**: Page bounding box modifications (`POST /api/analysis/save-page`) MUST be persisted atomically within a single SQLite transaction (clearing existing bboxes for the specific page and inserting active `BBoxItem` records) to prevent partial annotation writes. Heading level updates (`POST /api/analysis/update-heading`) must update `user_annotated_bboxes` immediately, and document finalization (`POST /api/analysis/finalize`) must mark `layout_analysis_progress(status='completed')` and reflect validated state across Vault source lists without deleting raw bounding box review history.
25. **Spaced Repetition Due Review & Wrong Answers Consolidation**: Due repetition questions and wrongly answered questions MUST be consolidated into a single Knowledge Hub session (`sess_spaced_repetition_due` titled `🧠 Due & Wrong Review Set`) via `sync_consolidated_due_session`. Questions answered incorrectly or due for review appear immediately in due review counts (`GET /api/sessions/due_summary`) and dashboard prompt reminders (`GET /api/sessions/dashboard_prompts`). In `core/scheduler.py`, rating 'Again' (`grade == 1`) configures `interval_days = 0`, `interval_seconds = 0`, and sets `due_timestamp = current_ts`, and `generate_set` prioritizes unattempted questions first before falling back to attempted questions when question count is limited.
26. **ZIM Reader Modal Width, Translucent Backdrop & Asset Tag Rewrite Integrity**: In `ui/js/app.js` and `ui/css/main.css`, the ZIM viewer (`#zim-viewer-container`) MUST expand to a 1200px max-width `.glass-card` centered over a translucent backdrop filter, apply `body.zim-reader-open` to hide underlying `.view-section` text while keeping the body wallpaper visible, and close cleanly via `window.closeDocumentReader()` upon clicking anywhere outside the central article card or pressing Escape. In `parsers/zim_reader.py`, asset rewriting MUST capture SVG embedded references (`<image xlink:href="...">`, `<object data="...">`, `<embed src="...">`, `<source srcset="...">`) in addition to standard `<img>` and `<a>` tags, and sanitize hardcoded inline colors on MediaWiki navboxes while preserving structural widths.
27. **Segmented Controls Two-Way Binding & Accessibility Role Contract**: UI segmented button groups (`.segmented-group` and `.segmented-btn`) replacing select dropdowns for frequent actions (source type, layout profile, obsidian click mode, wallpaper timer, database target) MUST retain the underlying `<select>` element in the DOM, synchronize selections bidirectionally between button clicks and `<select>` change events, and declare `role="radiogroup"` on the container and `role="radio"` / `aria-checked` on individual option buttons for full assistive accessibility.
28. **Indra OCR Achromatic Hue Sentinels & Multi-Line Continuation Integrity**: In `parsers/indra/types.py`, default hue values for `PageElement` and `HeadingPrototype` MUST be initialized to `-1.0` (`fg_hue = -1.0`, `bg_hue = -1.0`) to unambiguously differentiate achromatic elements (pure grays, whites, blacks) from chromatic elements with hue 0.0 (red). In `parsers/indra/strategies.py`, `_merge_wrapped_elements` MUST merge vertical wrapped lines sharing identical column index and visual styles within `v_gap <= 0.038`, specifically exempting parenthetical acronym tails (`(COPD):`) and *Situation headers from being broken into fragmented blocks.
29. **Theme Contrast & Dark/Light Semantic Palette Variable Isolation**: In `ui/css/main.css`, theme neutrals (`--c-neutral-100` through `--c-neutral-500`), segmented variables (`--segmented-bg`, `--segmented-border`, `--segmented-active-bg`), and code backgrounds (`--code-bg`) MUST be defined across `:root` and `body.dark-theme`, providing contextual overrides on panels and cards for Tailwind dark-mode utilities (`text-gray-*`, `text-slate-*`, `text-white`) to ensure high contrast and WCAG AA readability without altering colored badges or buttons.
30. **Multi-Pass Heading Calibration & Base Layout Preservation**: The heading calibration engines (`parsers/heading_toc_calibrator.py`, `parsers/indra/engine.py`, and `parsers/docling/engine.py`) MUST preserve 100% of the underlying base OCR text extraction, word positions, and reading order (Marker/Surya for Indra, TableFormer for Docling v1), augmenting output strictly with non-destructive H1-H6 heading tier classification and hierarchical breadcrumbs. In `parsers/pdf_topic_indexer.py`, lookups against `pdf_topic_index` MUST enforce sibling mutual exclusion (`SIBLING_MUTUAL_EXCLUSIONS`) to prevent cross-topic pollution (e.g. Bronchopneumonia vs Lobar Pneumonia), and page-to-picture base64 rendering for LM Studio Vision-LM (VLM) synthesis MUST fall back gracefully to structured plain text if VLM execution fails or is unsupported.
31. **Question Metadata Tracking, Session Auto-Removal & Dynamic FSRS Tuning**: Question attempt metrics (`attempt_count`, `wrong_count`, `last_is_correct`, `last_attempt_ts`) and verified clinical topics MUST be recorded atomically into `question_metadata` via `record_question_attempt` on every quiz completion. FSRS interval calculations in `core/scheduler.py` dynamically scale according to user-configured retention and interval caps (`requested_retention`, `max_interval_days`) persisted in `user_state`. When `auto_remove_completed_sets` is enabled, fetching session history (`GET /api/sessions/history`) automatically purges fully revised study sets from `quiz_sessions` and child tables (excluding `sess_spaced_repetition_due`), and reviewed questions with future due timestamps are purged immediately from the consolidated review set.
32. **Domain-Anchored Subtopic Routing & Word-Budget Enforcement**: Multi-source synthesis pipelines (`agents/tema_q/subtopic_router.py`, `agents/tema_q/session_processor.py`, `agents/tema_q/auditor.py`) MUST strip tables, images, and captions before routing prose across the 7 canonical medical domain subtopic schemas (`DOMAIN_SUBTOPIC_SCHEMAS`). Synthesis contexts MUST strictly enforce hard token and word ceilings: maximum 500 words for primary articles, maximum 1000 words for merged secondary articles, and secondary sentences MUST be pre-filtered via token containment and staging/numeric novelty (`filter_novel_secondary_sentences`) to prevent duplicate passage pollution.
33. **Resilient LLM Output Repair & Pydantic Schema Validation**: All JSON outputs received from LM Studio or local LLM generation MUST pass through `agents/tema_q/json_utils.py` (`parse_llm_json` and `parse_llm_pydantic`). Processing MUST cleanly strip `<think>...</think>` internal reasoning blocks and markdown backtick fences, balance unclosed brackets/braces, strip invalid trailing commas, and validate against strongly-typed Pydantic schemas before ingestion into application state or databases.
34. **Topic Stopword Filtering & Ignore List Bidirectional Synchronization**: Candidate topic extraction (`agents/tema_q/topic_extractor.py`), full-PDF TOC indexing (`parsers/pdf_topic_indexer.py`), and dossier report generation (`core/reporter.py`) MUST validate all topic strings against `core/topic_filter.py` (`is_valid_topic`). Strings shorter than 3 characters, purely non-alphanumeric noise, and phrases formed exclusively from `Ignore_list.txt` stopwords MUST be rejected. Ignored topics and frequent note topics (>2 occurrences) MUST synchronize bidirectionally between SQLite tables (`ignored_topics`, `topic_note_counts`) and local flat files (`Ignore_list.txt`, `tests/fixtures/topic.txt`).
35. **Server Lifespan Maintenance & Safe Upload Streaming**: FastAPI server startup MUST invoke `run_startup_maintenance()`, ensure runtime storage directories exist, clear stale 0-byte Tantivy lockfiles, and register `GZipMiddleware(minimum_size=1000)`. Uploaded files (`server/routes_sources.py`) MUST undergo null byte and path traversal validation (`validate_safe_source_path`) and stream to disk in discrete 1 MB chunks (`stream_upload_to_disk`) with incremental SHA-256 digest computation to guard against memory exhaustion during large vault imports.
36. **Frontend Race-Safe Search & Performance Throttling**: Interactive UI search operations (`ui/js/search.js`) MUST enforce query cancellation via `AbortController` and 200ms input debounce (`executeRaceSafeSearch`) to eliminate out-of-order response races. Heavy visual updates (drag events in layout analysis and scroll events) MUST be throttled using `requestAnimationFrame` (`throttleWithRAF`), state updates debounced at 300ms with localStorage backup (`queueDebouncedStatePatch`), and WebSocket reconnection guarded by exponential backoff (`computeWsBackoffDelay`).
37. **Safe Dynamic Schema Migrations & DDL Identifier Sanitization**: In `core/database.py`, runtime schema migrations MUST execute via `add_column_safely(conn, table_name, column_name, column_type)` to eliminate SQL and DDL injection vectors. Table and column identifiers MUST match strict regex `^[a-zA-Z0-9_]+$`, and column types MUST resolve against the strict allowlist `_ALLOWED_COLUMN_TYPES` (`INTEGER`, `TEXT`, `REAL`, `BLOB`, `NUMERIC`, `BOOLEAN`, `DATETIME`). Attempted additions of existing columns MUST fail silently and idempotently without raising duplicate column exceptions.
38. **Canonical SQLite File Path Normalization**: In `core/database.py` and `parsers/pdf_topic_indexer.py`, file system paths persisted across primary keys, foreign keys, and indexing lookups MUST be canonicalized via `normalize_db_file_path(file_path: str) -> str`. The helper normalizes absolute paths to lowercase forward-slash representation (`Path(file_path).resolve().as_posix().lower()`), preventing path mismatch bugs between Windows drive letters, mixed backslashes/slashes, and case-varying directory lookups in tables such as `layout_analysis_progress`, `user_annotated_bboxes`, and `pdf_topic_index`.
39. **Quiz Sessions Sole Primary Key Constraint**: In `core/database.py`, `docs/mcq_blueprint.md`, and `blueprint.md`, the `quiz_sessions` table MUST define `id TEXT PRIMARY KEY` as its sole uniqueness constraint. No secondary or composite unique constraints may be placed on `title` or `custom_title`, permitting users to create multiple study sessions with identical or repeated topic names over time. Child table `session_questions` references `quiz_sessions(id) ON DELETE CASCADE`.
40. **Cached FSRS Spaced Repetition Configuration**: In `core/scheduler.py`, access to user-configured spaced repetition retention and interval limits MUST route through `CachedFSRSConfigProvider` with a thread-safe 300-second TTL cache. High-frequency quiz scoring loops and session generators must not execute N+1 database queries against `user_state`. Mutation endpoints in `server/routes_mcq.py` and `server/routes_state.py` MUST explicitly call `CachedFSRSConfigProvider.invalidate()` upon configuration updates to maintain live consistency.
41. **Consolidated Review Set Topic Synchronization**: In `server/routes_sessions.py`, `sync_consolidated_due_session` MUST continuously reconcile the active topic list (`topic_list_json`) and child rows (`session_questions`) of `sess_spaced_repetition_due` with strictly verified topics. Questions due for review or answered incorrectly immediately aggregate verified topics from `question_metadata`, `extracted_topics` (`verified = 1`), and `session_questions`. When questions are mastered or due timestamps are pushed into the future, both the question rows and associated orphan topic entries MUST be purged from the review set.
42. **Precision Counter Basis Points Arithmetic**: In `ui/js/habits.js`, all telemetry metrics, goal counters, and habit completion percentages calculated by `PrecisionCounterController` MUST operate using integer basis points (1 unit = 10,000 bp). Floating-point arithmetic is strictly prohibited in cumulative counter mutations to eliminate IEEE 754 precision drift. Visual counter updates MUST animate smoothly via cubic ease-out `requestAnimationFrame` interpolations and debounce database synchronization to 350ms.
43. **Focus Mode & Passive Activity Wellness Engine**: In `ui/js/habits.js` and `ui/js/dashboard.js`, `FocusModeEngine` tracks study time using wall-clock intervals (`Date.now()`) with passive interaction listeners (`pointerdown`, `keydown`, `wheel`, `quiz:answer_submitted`). An automated 5-minute check-in modal engages during extended sessions with a 10-second auto-pause countdown, contextual wellness guidance (hydration, 20-20-20 visual relief, ergonomic stretches), and subtle Web Audio chime notifications, syncing live state to `DashboardStudyBanner`.
44. **Hierarchical Heading Breadcrumb Dispatch & Telemetry**: In `agents/tema_q/subtopic_router.py` and `agents/tema_q/telemetry.py`, multi-source RAG synthesis pipelines MUST preserve hierarchical heading breadcrumbs (`H1 > H2 > H3`) across all prose chunks. Document blocks are tagged with contextual heading hierarchies (`primary_heading_sections`), and dispatch runs record detailed telemetry via `HeadingDispatchTrace` and `PipelineDiagnosticTrace`. Traces capturing model provenance, token usage, auditor merge events, and automated anomaly flags MUST be persisted as JSON under `storage/reports/pipeline_traces/`.
45. **Obsidian Vault Graph Indexing & Section Slug Slicing**: In `parsers/obsidian_reader.py`, markdown vaults are indexed into a connected graph using `ObsidianVaultGraph` and cached in `obsidian_notes`. YAML frontmatter MUST be stripped anchored strictly at the start of the file (`\A---\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)`, count=1) to prevent stripping intermediate horizontal divider rules (`---`). Heading sections are sliced into discrete anchorable units with deterministic slugs (`heading_slug`), expanding clinical subheading synonyms (`CLINICAL_SUBHEADING_SYNONYMS`) for unified section search (`search_notes_and_sections`).
46. **Lecture Topic Pad Glassmorphism & Optimistic Locking**: In `ui/js/notes.js`, `server/routes_notes.py`, and `core/database.py`, the 'My Notes' Lecture Topic Pad organizes study notes into 8 wallpaper-derived glassmorphic card shades. Note lines feature optimistic line-locking (<2ms latency on Enter key), debounced Right Sidebar reference inspection on single click, and double-click inline title editing (`LectureNoteEditor`). Each note card provides a direct 'Do MCQ' button (`.btn-do-mcq-note`) launching `window.startCombinedTopicsQuiz(topics, noteTitle)` across all contained topic items.
47. **LIFO Stack Lexical State Machine for Truncated JSON Repair**: In `agents/tema_q/json_utils.py`, all malformed or truncated LLM JSON payloads MUST be pre-repaired using `_repair_truncated_json_lifo` before schema parsing. The single-pass quote- and escape-aware lexical scanner tracks delimiters on a LIFO stack (`{` -> `}`, `[` -> `]`), resolves dangling key-value cut points with `: null`, strips trailing commas, and closes unclosed braces, preventing parse exceptions on token budget truncations.
48. **Atomic Queue Task Claiming with Immediate Isolation**: In `core/queue_manager.py`, `PriorityQueueWorker._claim_highest_priority_task` and `AsyncQueueWorker._fetch_and_claim_task` MUST claim tasks inside an atomic transaction block (`with atomic_transaction(conn):`) using `BEGIN IMMEDIATE`. Task claiming executes an atomic `UPDATE ... RETURNING` query with a fallback conditional `UPDATE ... WHERE task_id = ? AND LOWER(status) = 'pending'` checking `rowcount == 0` to prevent duplicate task claiming on autocommit connections.
49. **Mode A Lazy Bidirectional H1 Probing & Running Header Preservation**: In `parsers/heading_toc_calibrator.py` and `parsers/indra/document.py`, Mode A TOC extraction MUST probe backwards to identify chapter start H1 boundaries and forwards for subsequent chapter boundaries, clearing probing artifacts and assembling pages in monotonic forward order. Running header furniture filters MUST check `el.bbox_norm[3] <= 0.08 and el.rel_size < 1.35 and not el.is_bold` to prevent true chapter headers (e.g. `CHAPTER 1: INTRODUCTION`) from being falsely stripped.
50. **Accessible WAI-ARIA Semantics for Quiz Option Cards**: In `ui/js/quiz.js`, multiple-choice question options MUST implement full WAI-ARIA radiogroup semantics (`role="radiogroup"` on option lists, `role="radio"` and `aria-checked` on option cards). Options MUST support comprehensive keyboard navigation via numeric keys '1'-'4', alphabet keys 'A'-'D', Arrow keys, and Enter/Space activation, maintaining complete accessibility compliance.
51. **Medical HTML Cleaning & Logging Hygiene**: In `agents/tema_q/topic_extractor.py`, medical candidate text extraction MUST clean input prose using `clean_html_prose`, converting HTML/XML inline and block tags into whitespace boundaries to prevent word concatenation across tags. Windows asyncio Proactor loops MUST catch and suppress `ConnectionResetError` [WinError 10054] during socket teardown in `run.py` and `server/app.py`, and PyFastNER syntax warnings MUST be suppressed via `_PyFastNERLogFilter`.
52. **Candidate vs Ignored Topic Chip Styling & Mutual Exclusivity**: In `ui/js/quiz.js` and `server/routes_queue.py`, topic review modals MUST strictly enforce mutual exclusivity between verified and ignored/excluded topics. Ignored topics MUST display with greyed text in a red-tinted strike-through container (`border-red-500/40 bg-red-500/15 line-through`), visually separated from doubtful candidate topics in plain grey. Topics sort with unreviewed candidates first, confirmed topics next-to-last, and excluded topics last, each featuring an inline '✕' removal button.
