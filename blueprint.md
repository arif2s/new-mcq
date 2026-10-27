# Global Context Map & Architecture Blueprint (`blueprint.md`)

## 1. System Overview & Constraints

- **Core Purpose**: Offline-first medical MCQ, spaced repetition, RAG search & local AI synthesis platform.
- **Runtime Environment**: Python 3.10+ async backend (FastAPI/Uvicorn), SQLite (WAL mode), pywebview (Windows Edge Chromium) or browser client, Vanilla HTML5/JS/CSS frontend.
- **Tech Stack**:
  - *Backend*: FastAPI, Uvicorn, SQLite3, Tantivy (BM25 lexical index), LanceDB (CPU vector + FTS hybrid search), ONNX Runtime (SapBERT 768d / MiniLM 384d), FlashRank (ms-marco-TinyBERT-L-2-v2 / ms-marco-MiniLM-L-6-v2 CPU cross-encoder reranking), SciSpaCy (UMLS/MeSH ontologies & SciPy sparse TF-IDF matcher), Docling v2 & Docling v3 (visual layout, booktabs reconstruction, OCR line-crop re-reading & PP-OCR ONNX layout parser) & PyMuPDF / AdaptivePdfLayoutEngine (PDF text extraction), LibZim (ZIM reader), Tiktoken.
  - *Frontend*: Vanilla HTML5/JS (ES6+), CSS3 with glassmorphism theme CSS variables (`ui/css/main.css`), Marked.js, KaTeX, DOMPurify. Local offline vendor fallback assets automatically checked and cached under `ui/vendor/`.
  - *External Readers / Bridges*: SumatraPDF (`-reuse-instance`), Obsidian URI protocol (`obsidian://`), LM Studio API (`http://127.0.0.1:1234/v1`).
- **Strict Hardware & Performance Budgets**:
  - *VRAM Budget*: Zero VRAM for backend processes (`CUDA_VISIBLE_DEVICES=""`); 100% VRAM (4.2–4.8 GB) reserved for local LM Studio GGUF models.
  - *CPU Execution Time*: Multi-stage hybrid search pipeline < 250ms on CPU (enforced via cached singletons for embedder, reranker, and LanceDB).
  - *RAM Allocation*: Tantivy < 250 MB; LanceDB + CPUEmbedding < 500 MB; Primary SQLite MMAP cap = 512 MB; Ontology KB SQLite MMAP cap = 256 MB; SciSpaCy NLP + Linker < 500 MB; Docling CPU Converter + FlashRank + LRU Caches < 1.0 GB; **total Python process < 3.5 GB** (operating within ~5 GB available system RAM).
  - *LLM Context Limit*: Hard budget capped at 4096 tokens per request via `tiktoken` truncation.

---

## 2. Directory & Module Registry

```
.
├── config.py                          # Global paths, environment overrides, server/window config, model catalogs, and atomic vendor cacher.
├── run.py                             # Application entry point; sets environment, downloads vendor assets, boots server & browser.
├── auditor.py                         # Compatibility re-exporter for agents.tema_q.auditor (clinical variance & JSON node merger).
├── dummy.py                           # Fallback topic extractor for testing environments.
├── agents/
│   ├── topic_extractor.py             # Compatibility re-exporter for topic extraction functions.
│   └── tema_q/
│       ├── topic_extractor.py         # SciSpaCy entity extraction, layperson synonym mapping, and sibling topic filtering.
│       ├── pipeline.py                # 2-pass iterative LLM synthesis coordinator with token budgeting and LM Studio integration.
│       ├── auditor.py                 # Primary clinical discrepancy, staging variance detection, and deterministic JSON node merger engine.
│       ├── session_processor.py       # Async session processor for background RAG and LLM task execution.
│       └── prompts.py                 # Strict token-budgeted medical RAG prompts and stop-token definitions.
├── core/
│   ├── database.py                    # SQLite schema initialization, WAL/MMAP PRAGMAs, migrations, and thread-safe connection pooling.
│   ├── queue_manager.py               # Asynchronous priority task queue worker (P1/P2/P3) and WebSocket state broadcaster.
│   ├── scheduler.py                   # FSRS v4.5 spaced repetition scheduling algorithm.
│   └── reporter.py                    # Jinja2 HTML dossier report compiler for clinical topic syntheses.
├── parsers/
│   ├── embedding_engine.py            # CPUEmbeddingEngine running ONNX SapBERT/MiniLM (384/768d) lazily on CPU with active model switching.
│   ├── lancedb_engine.py              # LanceDB hybrid vector storage and FTS engine for hierarchical document chunks.
│   ├── tantivy_engine.py              # Low-latency BM25 lexical inverted index builder and depth-scored multi-depth searcher.
│   ├── docling_parser.py              # Layout-aware PDF textbook section & table extractor using Docling ML / PyMuPDF.
│   ├── docling_v2_parser.py           # Standalone Docling v2 layout parser with AdaptivePdfLayoutEngine base64/SVG rendering.
│   ├── docling_v3_parser.py           # Standalone Docling v3 layout engine with dark-pixel de-bleeding & PP-OCR ONNX re-reading.
│   ├── pdf_extractor.py               # Lightweight PyMuPDF fallback fast text slicer with page heading detection.
│   ├── pdf_parser.py                  # Standalone 6-stage layout reconstruction pipeline (PyMuPDF AdaptivePdfLayoutEngine).
│   ├── obsidian_reader.py             # Obsidian markdown frontmatter stripper, wikilink cleaner, and section reader.
│   ├── zim_reader.py                  # LibZim C-bindings reader for offline Wikipedia/medical .zim archives and asset extraction.
│   ├── reranker.py                    # FlashRank (ms-marco-TinyBERT-L-2-v2) CPU cross-encoder reranker with active model switching.
│   └── scispacy_linker_engine.py      # SciSpaCy NLP pipeline loader, covering index SQLite pool, SciPy sparse TF-IDF matcher & batched linker.
├── server/
│   ├── app.py                         # FastAPI app initialization, startup/shutdown lifespans, WS endpoints, and static file mounting.
│   ├── ws_manager.py                  # Global WebSocket connection manager for live task/indexing progress.
│   ├── routes_csv.py                  # Endpoints for CSV topic listing, question retrieval, filtering, and FSRS summary.
│   ├── routes_csv_loader.py           # Background loader scanning data/csv and auto-ingesting questions into SQLite.
│   ├── routes_mcq.py                  # MCQ topic extraction, scoring, synthesis lookup, and batch generation triggers.
│   ├── routes_models.py               # Hardware-constrained model manager, active model switcher, diagnostic endpoints, & linker index builder.
│   ├── routes_notes.py                # External app bridges (SumatraPDF launch, Obsidian native URI, ZIM asset/article proxy).
│   ├── routes_queue.py                # RAG task queue state management, topic ignoring, priority overrides, and queue clear/retry.
│   ├── routes_search.py               # Multi-source hybrid search API (LanceDB + Tantivy + ZIM + SciSpaCy).
│   ├── routes_sessions.py             # Quiz session persistence, answer recording, session history, and RAG session reprocessing.
│   ├── routes_sources.py              # Source management (PDF, Obsidian, ZIM), wallpaper listing, and background indexer execution.
│   └── routes_state.py                # Persistent state GET/PUT storage and vendor asset check.
└── ui/
    ├── index.html                     # Main SPA HTML shell with responsive subviews and modal containers (mirrored at root index.html).
    ├── css/
    │   ├── main.css                   # Glassmorphism UI tokens, theme CSS variables, grid styles, MediaWiki overrides, and utility classes.
    │   └── animations.css             # Keyframe animations for transitions, loaders, and badges.
    └── js/
        ├── app.js                     # Root UI navigation, tab switching, dynamic palette extraction, and citation click delegation.
        ├── state.js                   # UI persistence sync layer (`AppPersistence`) interfacing with `/api/state/`.
        ├── quiz.js                    # Quiz runner, MCQ presentation, option handling, and inspector integration.
        ├── hub.js                     # Knowledge Hub session list, topic cards, synthesis carousel, and status indicators.
        ├── search.js                  # Search view controller, inspector filters, result rendering, and citation launcher.
        ├── queue.js                   # Live task queue status table and controls.
        ├── vault.js                   # Source management panel, index trigger, and progress bar binder.
        ├── dashboard.js               # Performance dashboard, daily statistics, and rank simulation.
        ├── expertise.js               # Clinical domain mastery breakdown and radar/progress UI.
        ├── habits.js                  # Habit streak tracker and activity heatmaps.
        ├── settings.js                # System settings, model diagnostics, SciSpaCy model manager, and state export/import.
        ├── test_features.js           # Interactive feature test bench (PDF, DB search, models, KaTeX, pipeline runner).
        └── reader.js                  # Reader view initialization wrapper and in-app document viewer.
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
- `auditor.py`: Compatibility re-exporter forwarding `detect_clinical_variances` and `merge_json_nodes` from `agents.tema_q.auditor`.
- `dummy.py`: `extract_topics(text: str) -> list[str]`

### Agents Modules (`agents/`)
- `agents.topic_extractor`: Re-exports `extract_topics`, `extract_topics_local`, `filter_sibling_topics`, `get_topic_aliases`, `generate_candidate_variants`, `canonicalize_clinical_phrase`, `extract_clinical_modifiers`, `extract_all_clinical_modifiers`, `get_ignored_topics_set`, `add_ignored_topic`, `remove_ignored_topic`, `normalize_topic_key`, `resolve_zim_topic`, `get_medspacy_model` from `agents.tema_q.topic_extractor`.
- `agents.tema_q.topic_extractor`:
  - `extract_topics(text: str, explanation: str = "", strategy: str = "medspacy", use_zim_refinement: bool = False, use_tantivy_refinement: bool = False, **kwargs) -> list[str]`
  - `extract_topics_local(question: str = "", options: Optional[Any] = None, explanation: str = "", strategy: str = "medspacy", use_zim_refinement: bool = False, use_tantivy_refinement: bool = False, text: Optional[str] = None, **kwargs) -> dict[str, Any]`
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
- `agents.tema_q.prompts`: `STOP_TOKENS: list[str]`, `PASS1_CLINICAL_SYSTEM_PROMPT: str`, `PASS2_AUDIT_SYSTEM_PROMPT: str`, `DISTRACTOR_ANALYSIS_PROMPT: str`, `EXPLANATION_GENERATION_PROMPT: str`

### Core Modules (`core/`)
- `core.database`:
  - `get_db_connection() -> sqlite3.Connection` (configured per-connection with `WAL`, `busy_timeout=20000`, `foreign_keys=ON`, `synchronous=NORMAL`, `mmap_size=536870912`, `temp_store=MEMORY`, `cache_size=-64000`)
  - `initialize_database() -> None` (executes 4-stage initialization: table & FTS5 trigger creation -> idempotent column & `task_queue` FK removal migrations -> index creation -> `csv_questions_fts_docsize` external-content FTS5 rebuild check)
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
  - `map_fsrs_state_to_status(state: int, stability: float = 0.0, reps: int = 0) -> str`: Maps FSRS numeric state and stability to UI status labels (`'new'`, `'learning'`, `'review'`, `'mastered'`).
  - `calculate_fsrs_next_interval(rating: int, state: int, difficulty: float, stability: float, reps: int, lapses: int, now: Optional[float] = None) -> dict[str, Any]`: Computes updated FSRS v4.5 metrics using exact power-law interval scaling ($I = S \cdot 1.0$ at 90% retention), $D_0(3)$ mean reversion, stability floor protection ($\ge 0.1$), and optional `due_timestamp` calculation; returns `FSRS Stats Dict`.
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
- `parsers.docling_parser`:
  - `get_converter() -> DocumentConverter`: Thread-safe singleton loader with `accelerator_options.device = "cpu"` and `num_threads = 4`.
  - `extract_structured_layout(file_path: Union[str, Path], page_number: int) -> str`: LRU-cached (maxsize=128) layout markdown extractor for specific pages with graceful fallback to `parsers.pdf_extractor.extract_page_slice()`.
  - `extract_pdf_structure(file_path: Union[str, Path]) -> list[dict]`: Full document parser preserving hierarchical headings, markdown tables, lists, formulas, and text; returns dictionaries strictly conforming to `ChunkDict` (`page`, `page_number`, `heading`, `heading_path`, `subheading`, `text`, `article_length`, `doc_length`).
  - `ingest_to_lancedb(file_path: Union[str, Path], source_article: str, main_topic: str = "") -> int`: Extracts sections and ingests chunks into LanceDB using cached singleton `get_embedding_engine()` and batched ONNX embeddings, enforcing PyArrow schema types.
- `parsers.docling_v2_parser`:
  - `is_docling_models_downloaded() -> bool`
  - `download_docling_models() -> dict[str, Any]`
  - `clean_pdf_text(text: str) -> str`
  - `extract_pdf_structure(file_path: Union[str, Path]) -> list[dict[str, Any]]`
  - `extract_structured_layout(file_path: Union[str, Path], page_number: int) -> str`
  - `extract_page_with_docling_v2(file_path: Union[str, Path], page_number: int, ocr_engine: str = "auto") -> dict[str, Any]`
  - `extract_page_with_adaptive_layout(file_path: Union[str, Path], page_number: int) -> dict[str, Any]`
  - `class AdaptivePdfLayoutEngine`: 5-tier typography profiling, dynamic heading stack tracking, and layout bounding box overlay generator.
- `parsers.docling_v3_parser`:
  - `is_docling_models_downloaded() -> bool`
  - `download_docling_models() -> dict[str, Any]`
  - `clean_pdf_text(text: str) -> str`
  - `extract_pdf_structure(file_path: Union[str, Path]) -> list[dict[str, Any]]`
  - `extract_structured_layout(file_path: Union[str, Path], page_number: int) -> str`
  - `extract_page_with_docling_v3(file_path: Union[str, Path], page_number: int, ocr_engine: str = "auto") -> dict[str, Any]`
  - `extract_page_with_adaptive_layout(file_path: Union[str, Path], page_number: int) -> dict[str, Any]`
  - `class AdaptivePdfLayoutEngine`: Visual page inspector with line-crop PP-OCR ONNX re-reading, dark-pixel de-bleeding, vector chart obstacle isolation, and booktabs table reconstruction.
- `parsers.pdf_extractor`:
  - `clean_pdf_text(text: str) -> str`: Normalizes typographical ligatures (fi, fl, ffi), repairs line-break hyphenations, strips null bytes and form feeds, and cleans whitespace.
  - `extract_page_slice(file_path: Union[str, Path], page_number: int) -> str`: LRU-cached (maxsize=256) text extraction with natural reading order (`sort=True`) and bounds safety (rejects `page_number < 1`).
  - `extract_page_with_headings(page: Any, default_title: str = "") -> dict[str, Any]`: Fast layout extractor using font-size heuristics and margin bounds (filters running headers/footers); produces dictionaries conforming to `ChunkDict` (`page`, `page_number`, `heading`, `heading_path`, `subheading`, `title`, `text`, `snippet`, `length`, `article_length`, `doc_length`).
  - `extract_pdf_pages(file_path: Union[str, Path], default_title: str = "") -> list[dict[str, Any]]`: Complete document extractor returning full `ChunkDict` records with deterministic MD5 IDs for Tantivy BM25 and LanceDB indexing when `pdfEngine == 'fitz'`.
  - `extract_pdf_structure(file_path: Union[str, Path]) -> list[dict[str, Any]]`: Drop-in interoperability alias for `extract_pdf_pages()`.
  - `get_pdf_page_count(file_path: Union[str, Path]) -> int`: Fast trailer-based page count reader.
- `parsers.pdf_parser`:
  - Re-exports `AdaptivePdfLayoutEngine`, `clean_pdf_text`, and `extract_page_with_adaptive_layout` providing a standalone 6-stage layout reconstruction engine (typography profiling, shaded callout isolation, column splitting, recursive XY-cut ordering, heading stack continuation).
- `parsers.obsidian_reader`:
  - `read_markdown_body(file_path: Union[str, Path]) -> str`: LRU-cached (maxsize=512) frontmatter, comment, and wikilink stripper; handles Windows UTF-8 BOM (`utf-8-sig`) and returns cleaned plain text.
  - `extract_obsidian_sections(file_path: Union[str, Path]) -> list[dict]`: Layout-aware section extractor; protects fenced code blocks from false heading parsing, suppresses phantom sections, and returns complete dictionaries adhering strictly to `ChunkDict` (`id`, `doc_id`, `text`, `snippet`, `title`, `heading`, `heading_path`, `subheading`, `source="obsidian"`, `file_path`, `page=1`, `page_number=1`, `doc_length`, `article_length`).
  - `read_obsidian_for_browser(file_path: Union[str, Path]) -> dict[str, Any]`: Prepares notes for SPA client rendering; rewrites image embeds to `/api/sources/asset` and wikilinks to `.citation-link` anchors carrying `data-source-type="obsidian"`, `data-file-path`, `data-page="1"`, and optional `data-anchor`.
- `parsers.zim_reader`:
  - `class ZimAsset(tuple)`: Polyfill supporting `tuple[bytes, str]` unpacking and dictionary/property access (`.content`, `.mimetype`, `.zim_path`).
  - `get_enabled_zim_paths(force_refresh: bool = False) -> list[str]`
  - `get_archive(zim_path: str) -> Optional[Any]`
  - `resolve_zim_topic(term: str) -> dict[str, Any]`
  - `get_zim_article(zim_path: str, title: str, max_chars: int = 4500) -> str`
  - `get_zim_article_html(title_or_path: str, zim_path: Optional[str] = None) -> dict[str, Any]` (scopes extracted HTML inside `<div class="mw-parser-output">`)
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
- `server.routes_csv_loader`:
  - `scan_and_ingest_csvs() -> dict[str, Any]`: Recursively scans `CSV_DIR` and `MCQS_DIR`, normalizes diverse option/answer representations (numbers 1-4, bracketed letters, verbatim text matches), and executes chunked (`BATCH_SIZE=1000`) `INSERT OR IGNORE` queries into `csv_questions`.
  - `load_csvs_background`: Backward-compatibility callable alias for `scan_and_ingest_csvs`.
  - `POST /api/csv/reload`, `POST /reload`, `GET /api/csv/reload`, `GET /reload` -> `reload_csvs(background_tasks: Optional[BackgroundTasks] = None) -> dict[str, Any]`: Offloads re-scanning to worker threads via `asyncio.to_thread` and returns execution metrics.
- `server.routes_csv`:
  - `GET /api/csv/subjects`, `POST /api/csv/subjects`, `GET /api/mcq/subjects`, `POST /api/mcq/subjects` -> `get_subjects() -> list[SubjectResponse]`: Aggregates question counts per subject file stem.
  - `GET /api/csv/topics`, `POST /api/csv/topics`, `GET /api/mcq/topics`, `POST /api/mcq/topics` -> `get_topics(subject: Optional[str] = None) -> list[TopicResponse]`: Lists topic counts with optional subject filtering.
  - `POST /api/csv/search_count` & `POST /api/mcq/search_count` -> `search_count(req: GenerateSetRequest) -> dict[str, Any]`: Computes matching question count over FTS5 and subject/topic filters.
  - `POST /api/csv/generate_set` & `POST /api/mcq/generate_set` -> `generate_set(req: GenerateSetRequest) -> dict[str, Any]`: Curates question set with FSRS due-priority ordering and randomized fallbacks.
  - `GET /api/csv/fsrs_summary`, `POST /api/csv/fsrs_summary`, `GET /api/mcq/fsrs_summary`, `POST /api/mcq/fsrs_summary` -> `get_fsrs_summary() -> dict[str, Any]`: Real-time FSRS card status distribution (`new`, `learning`, `review`, `mastered`).
  - `GET /api/csv/db_status` & `GET /api/mcq/db_status` -> `get_db_status() -> dict[str, Any]`: Checks question bank population.
  - `POST /api/csv/build_db` & `POST /api/mcq/build_db` -> `build_db(background_tasks: BackgroundTasks) -> dict[str, Any]`: Offloads background CSV ingestion.
  - `GET /api/csv/diagnostics/lm_studio` & `GET /api/mcq/diagnostics/lm_studio` -> `check_lm_studio() -> dict[str, Any]`: Legacy compatibility alias for `/api/models/diagnostics/lm_studio`.
  - `POST /api/mcq/topics_status`, `POST /api/mcq/topics/status`, `POST /api/csv/topics_status` -> `topics_status(req: TopicsStatusRequest) -> dict[str, Any]`: Checks existing dossiers, references (via polymorphic validation), and ignored status.
- `server.routes_mcq`:
  - `POST /api/mcq/extract_topics` & `POST /api/mcq/topics/extract` -> `extract_mcq_topics(payload: TopicExtractionPayload) -> dict[str, Any]`
  - `POST /api/mcq/score` -> `score_question(payload: ScorePayload) -> dict[str, Any]`
  - `GET /api/mcq/synthesis?topic=...&resolve_if_missing=true` -> `get_synthesis(topic: str, resolve_if_missing: bool = True) -> dict[str, Any]`
  - `POST /api/mcq/batch_generate_references` & `POST /api/mcq/topics/generate_references` -> `batch_generate_references(payload: BatchTopicRequest) -> dict[str, Any]`
  - `POST /api/mcq/batch_generate_explanations` & `POST /api/mcq/topics/generate_explanations` -> `batch_generate_explanations(payload: BatchTopicRequest) -> dict[str, Any]`
- `server.routes_models`:
  - `GET /api/models/cpu_embeddings`, `POST /api/models/cpu_embeddings`, `GET /api/models/cpu_embedding_models`, `POST /api/models/cpu_embedding_models` -> `get_cpu_embedding_models() -> dict[str, Any]`: Lists embedding models (including SapBERT Clinical Fulltext ONNX), disk caching status, and active model selection from `user_state`.
  - `GET /api/models/flashrank_models` & `POST /api/models/flashrank_models` -> `get_flashrank_models() -> dict[str, Any]`: Lists FlashRank cross-encoder reranking models and download status.
  - `GET /api/models/scispacy_models` & `POST /api/models/scispacy_models` -> `get_scispacy_models() -> dict[str, Any]`: Lists SciSpaCy biomedical NER models, RAM footprints, and installation status.
  - `POST /api/models/set_active` -> `set_active_model(payload: SetActiveModelPayload) -> dict[str, Any]`: Persists choice to `user_state` and invokes engine model setters.
  - `POST /api/models/download_all` -> `download_all_models(background_tasks: BackgroundTasks) -> dict[str, Any]`: Enqueues background pre-fetching of all catalog models.
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
- `server.routes_queue`:
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
  - `POST /api/sessions/save` -> `save_session(session: SessionCreate) -> dict[str, Any]`
  - `GET /api/sessions/history` -> `get_session_history() -> list[dict[str, Any]]`
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
  - `DELETE /api/sources/{source_id}` -> `delete_source(source_id: str) -> dict[str, str]`: Deletes source from SQLite.
  - `POST /api/sources/index` -> `index_sources(background_tasks: BackgroundTasks) -> dict[str, str]`: Concurrency-guarded background indexer trigger (returns `status="busy"` if already running).
  - `POST /api/sources/open_pdf` -> `open_pdf(payload: OpenPdfPayload) -> dict[str, str]`: Windows deep-link launcher for SumatraPDF using `-reuse-instance` and `-page` flags with `.pdf` path validation and auto-discovery in `%LOCALAPPDATA%` and `%ProgramFiles%`.
  - `GET /api/sources/asset?path=...` -> `get_asset(path: str) -> FileResponse`: Serves Obsidian attachments with path traversal checks (`is_relative_to`) and mimetype sniffing.
  - `POST /api/sources/build_zim_dict` -> `build_zim_dict() -> dict[str, Any]`: Zero-copy C++ B-tree archive inspection updating item counts.
  - `POST /api/sources/test_pdf_extract` -> `test_pdf_extract(payload: TestPdfExtractPayload) -> dict[str, Any]`: Interactive PDF layout comparator testing Fitz text, heuristic headings, Docling v2, and Docling v3 ML layout parsers.
- `server.routes_state`:
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
- `ui/js/app.js`: `switchTab(tabName: str)`, `openInspector(forceOpen: bool = true)`, `closeInspector()`, `extractAndApplyPalette(imgElement: HTMLImageElement)`, `updateThemeAndBackground()`, citation delegation on click (`data-source-type`, `data-file-path`, `data-page`).
- `ui/js/state.js`: `AppPersistence.fetchState()`, `AppPersistence.saveState()`, `AppPersistence.updateState(patch)`
- `ui/js/quiz.js`: `initQuizSetup()`, `beginQuizRunner()`, `handleSelectOption(optLetter: str)`, `updateInspectorForQuestion(q)`, `renderTopicVerificationModalDOM()`
- `ui/js/hub.js`: `renderHub()`, `openTopicCarousel(topicKey: str)`, `openTopicCarouselForQuiz(topicKey: str)`, `openSessionReview(sessionId: str)`
- `ui/js/search.js`: `performGlobalSearch(query: str)`, `renderCentralSearchResults(results)`, `renderSearchFilterInspector()`
- `ui/js/queue.js`: `fetchQueueStatus()`, `renderQueue()`, `initQueueWebSocket()`
- `ui/js/vault.js`: `fetchSources()`, `renderSources()`, `updateProgressUI(percent, msg)`
- `ui/js/dashboard.js`: `initDashboard()`, `renderRankEditor()`
- `ui/js/expertise.js`: `initExpertiseMap()`
- `ui/js/habits.js`: `initHabits()`
- `ui/js/settings.js`: `initSettings()`, `loadDiagnostics()`, `loadScispacyModels()`
- `ui/js/test_features.js`: `initTestFeatures()`, `renderPdfExtractorView()`, `renderDbQueriesView()`
- `ui/js/reader.js`: `initReaderView()`, `openDocumentInReader(sourceType: str, path: str, pageOrAnchor: str)`

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
- Dynamic CSS Variables (`:root` mutated by `ui/js/app.js` based on wallpaper extraction):
  - `--bg-primary`, `--bg-glass`, `--text-primary`, `--text-muted`, `--accent-primary`, `--accent-secondary`, `--border-glass`
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
```

---

## 6. Active Features & Architectural Invariants

### Architectural Invariants
1. **Zero VRAM for Backend**: `CUDA_VISIBLE_DEVICES=""` must remain set at application startup (`run.py`, top of `server/app.py`, `parsers/embedding_engine.py`, `parsers/lancedb_engine.py`, `parsers/reranker.py`, `parsers/docling_parser.py`, `parsers/docling_v2_parser.py`, `parsers/docling_v3_parser.py`, `parsers/scispacy_linker_engine.py`, `parsers/pdf_extractor.py`, `parsers/pdf_parser.py`, `parsers/obsidian_reader.py`, `agents/tema_q/topic_extractor.py`, `agents/tema_q/auditor.py`, `auditor.py`, `server/routes_search.py`, `server/routes_sources.py`, `server/routes_state.py`, `server/routes_queue.py`, `server/routes_notes.py`, `server/routes_models.py`, `server/routes_csv.py`, `server/routes_csv_loader.py`, `server/ws_manager.py`, `core/database.py`, and `core/reporter.py`). All vector embeddings, reranking, spaCy biomedical pipelines, and Docling ML layout parsing MUST execute strictly on CPU.
2. **Main Thread DLL Pre-warming**: `onnxruntime`, `lancedb`, and `tantivy` MUST be imported at the very top of `server/app.py` to prevent Windows Access Violation segfaults.
3. **Database Concurrency, Cache Caps & Migration Ordering**:
   - Every SQLite connection returned by `get_db_connection()` MUST apply connection-scoped PRAGMAs (`journal_mode = WAL`, `busy_timeout = 20000`, `foreign_keys = ON`, `synchronous = NORMAL`, `mmap_size = 536870912`, `temp_store = MEMORY`, `cache_size = -64000`).
   - Primary SQLite MMAP MUST be capped at 512 MB (`536870912`) and `ontology_kb.db` MMAP at 256 MB (`268435456`) so the entire Python process (including all cached CPU ML singletons) stays strictly below the **3.5 GB total RAM ceiling**.
   - Schema initialization (`initialize_database()`) MUST execute column additions (`_safe_add_column`) and legacy `task_queue` FK removal (`_migrate_task_queue_schema`) BEFORE executing `CREATE INDEX IF NOT EXISTS`, and MUST inspect `csv_questions_fts_docsize` (not `csv_questions_fts`) when checking external-content FTS5 synchronization.
4. **Token Caps**: Prompts to LM Studio MUST be tiktoken-bounded to 4096 tokens total context length via `truncate_to_token_limit`.
5. **DOM & Security Safety**: Frontend vanilla JS MUST NEVER raw-interpolate user strings into `innerHTML`; use `escapeHTML()`, `DOMPurify.sanitize()`, or DOM element construction.
6. **External Readers Interop & Path Safety**: SumatraPDF invocation MUST validate `.pdf` file extensions and use `-reuse-instance` and `-page <num>`. Obsidian URI invocation MUST use `obsidian://open?vault=...&file=...`.
7. **Offline Asset Self-Containment**: Vendor scripts (Tailwind, Marked, DOMPurify, KaTeX) MUST be saved under `ui/vendor/` and automatically checked and downloaded at startup using atomic temp-file replacement (`config.ensure_offline_vendor_assets()`) to avoid 0-byte file corruption and guarantee complete offline execution.
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
21. **HTML Shell & Deferred Script Integrity**: The application HTML shell files (`ui/index.html` and root `index.html`) MUST remain identical. All vendor and application JavaScript files MUST be included with `defer` attributes, interactive buttons MUST use explicit `type="button"`, and theme persistence MUST initialize immediately via an inline IIFE reading `localStorage.getItem("theme")`.
22. **Docling Offline Execution Safety**: Docling parsers (`parsers/docling_v2_parser.py`, `parsers/docling_v3_parser.py`) MUST set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` environment variables when local model files are detected, enforcing 100% offline layout conversion without online Hugging Face Hub hangs.
23. **Non-Collapsing Inspector on Background Topic Extraction**: In `ui/js/app.js` and `ui/js/quiz.js`, `openInspector(forceOpen)` defaults `forceOpen` to `true`. When called with `forceOpen = false` during automatic background updates (`updateInspectorForQuestion`), inspector content MUST update without reopening the right sidebar if it was collapsed by the user.
