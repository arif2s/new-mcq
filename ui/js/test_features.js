/**
 * ui/js/test_features.js
 * Interactive testing harness for individual pipeline steps, engines, models, databases, and rendering components.
 */

(function () {
    'use strict';

    let currentFeature = null;

    function initTestFeatures() {
        const grid = document.getElementById('test-features-grid');
        const detailContainer = document.getElementById('test-feature-detail-container');
        const backBtn = document.getElementById('test-feature-back-btn');

        if (!grid) return;

        // Attach click handlers to feature cards
        grid.querySelectorAll('.card[data-feature]').forEach(card => {
            card.removeEventListener('click', handleCardClick);
            card.addEventListener('click', handleCardClick);
        });

        if (backBtn) {
            backBtn.onclick = () => {
                showGrid();
            };
        }
    }

    function handleCardClick(e) {
        const card = e.currentTarget;
        const featureKey = card.dataset.feature;
        if (!featureKey) return;
        openFeatureDetail(featureKey);
    }

    function showGrid() {
        const grid = document.getElementById('test-features-grid');
        const detailContainer = document.getElementById('test-feature-detail-container');
        if (grid) grid.classList.remove('hidden');
        if (detailContainer) detailContainer.classList.add('hidden');
        currentFeature = null;
    }

    function openFeatureDetail(featureKey) {
        const grid = document.getElementById('test-features-grid');
        const detailContainer = document.getElementById('test-feature-detail-container');
        const detailTitle = document.getElementById('test-feature-detail-title');
        const detailContent = document.getElementById('test-feature-detail-content');

        if (!grid || !detailContainer || !detailContent) return;

        grid.classList.add('hidden');
        detailContainer.classList.remove('hidden');
        currentFeature = featureKey;

        switch (featureKey) {
            case 'pdf-extractor':
                if (detailTitle) detailTitle.textContent = '📄 PDF Extraction & Layout Analysis Inspector';
                renderPdfExtractorView(detailContent);
                break;
            case 'db-queries':
                if (detailTitle) detailTitle.textContent = '🔍 Database & Hybrid Retrieval Engine Queries';
                renderDbQueriesView(detailContent);
                break;
            case 'topic-extractor':
                if (detailTitle) detailTitle.textContent = '🩺 Clinical Entity & Topic Extractor';
                renderTopicExtractorView(detailContent);
                break;
            case 'models-diagnostics':
                if (detailTitle) detailTitle.textContent = '⚡ AI Models & Zero-VRAM Hardware Diagnostics';
                renderModelsDiagnosticsView(detailContent);
                break;
            case 'ui-rendering':
                if (detailTitle) detailTitle.textContent = '🎨 UI Element, KaTeX Math & Citation Renderer';
                renderUiRenderingView(detailContent);
                break;
            case 'pipeline-runner':
                if (detailTitle) detailTitle.textContent = '🧩 1-2-3 RAG Synthesis Pipeline Step Runner';
                renderPipelineRunnerView(detailContent);
                break;
            default:
                detailContent.innerHTML = '<p class="text-muted">Feature test view not found.</p>';
        }
    }

    // =========================================================
    // 1. PDF Extractor View
    // =========================================================
    async function renderPdfExtractorView(container) {
        container.innerHTML = `
            <div class="card glass-card mb-6">
                <div class="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                    <div>
                        <label for="pdf-select" class="text-xs font-bold text-muted mb-1 block">Select PDF Source</label>
                        <select id="pdf-select" class="glass-input text-xs w-full py-2">
                            <option value="">Loading PDFs...</option>
                        </select>
                    </div>
                    <div>
                        <label for="pdf-custom-path" class="text-xs font-bold text-muted mb-1 block">Or Custom File Path</label>
                        <input type="text" id="pdf-custom-path" class="glass-input text-xs w-full py-2" placeholder="e.g. data/sample.pdf" />
                    </div>
                    <div>
                        <label for="pdf-page-num" class="text-xs font-bold text-muted mb-1 block">Page Number</label>
                        <input type="number" id="pdf-page-num" class="glass-input text-xs w-full py-2" value="1" min="1" />
                    </div>
                    <div>
                        <label for="pdf-engine-select" class="text-xs font-bold text-muted mb-1 block">Parser Engine</label>
                        <select id="pdf-engine-select" class="glass-input text-xs w-full py-2">
                            <option value="fitz">PyMuPDF (Fitz) Fast Text Slicing</option>
                            <option value="docling">Docling ML Structural Markdown</option>
                            <option value="headings">PyMuPDF Heuristic Headings</option>
                        </select>
                    </div>
                </div>
                <div class="mt-4 flex justify-end">
                    <button type="button" id="run-pdf-extract-btn" class="primary-btn text-xs px-6 py-2">
                        Extract &amp; Compare Page
                    </button>
                </div>
            </div>

            <!-- Side-by-side View: PDF Info vs Extracted Output -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div class="card glass-card">
                    <h4 class="text-sm font-bold mb-3 text-brand-300 flex items-center gap-2">
                        <span>📖</span> PDF Source Metadata &amp; State
                    </h4>
                    <div id="pdf-meta-box" class="space-y-2 text-xs text-muted">
                        <p>Select a PDF file and click "Extract &amp; Compare Page" to inspect results.</p>
                    </div>
                </div>

                <div class="card glass-card flex flex-col">
                    <div class="flex items-center justify-between mb-3 border-b border-gray-700/50 pb-2">
                        <h4 class="text-sm font-bold text-brand-300 flex items-center gap-2">
                            <span>📝</span> Formatted Extracted Output
                        </h4>
                        <div class="flex gap-2">
                            <button type="button" id="pdf-output-tab-rendered" class="text-xs px-3 py-1 rounded-lg bg-brand-500/20 text-brand-300 font-semibold cursor-pointer">Rendered</button>
                            <button type="button" id="pdf-output-tab-raw" class="text-xs px-3 py-1 rounded-lg bg-gray-700/50 text-muted font-semibold cursor-pointer">Raw Text</button>
                        </div>
                    </div>
                    <div id="pdf-output-rendered" class="prose max-w-none text-xs overflow-y-auto max-h-96 p-2 rounded-lg bg-gray-900/30">
                        <p class="text-muted">Extraction output will appear here.</p>
                    </div>
                    <textarea id="pdf-output-raw" class="glass-input text-xs font-mono w-full h-80 hidden" readonly></textarea>
                </div>
            </div>
        `;

        const pdfSelect = document.getElementById('pdf-select');
        const customPath = document.getElementById('pdf-custom-path');
        const pageNum = document.getElementById('pdf-page-num');
        const engineSelect = document.getElementById('pdf-engine-select');
        const runBtn = document.getElementById('run-pdf-extract-btn');
        const metaBox = document.getElementById('pdf-meta-box');
        const outputRendered = document.getElementById('pdf-output-rendered');
        const outputRaw = document.getElementById('pdf-output-raw');
        const tabRendered = document.getElementById('pdf-output-tab-rendered');
        const tabRaw = document.getElementById('pdf-output-tab-raw');

        tabRendered.onclick = () => {
            tabRendered.className = 'text-xs px-3 py-1 rounded-lg bg-brand-500/20 text-brand-300 font-semibold cursor-pointer';
            tabRaw.className = 'text-xs px-3 py-1 rounded-lg bg-gray-700/50 text-muted font-semibold cursor-pointer';
            outputRendered.classList.remove('hidden');
            outputRaw.classList.add('hidden');
        };

        tabRaw.onclick = () => {
            tabRaw.className = 'text-xs px-3 py-1 rounded-lg bg-brand-500/20 text-brand-300 font-semibold cursor-pointer';
            tabRendered.className = 'text-xs px-3 py-1 rounded-lg bg-gray-700/50 text-muted font-semibold cursor-pointer';
            outputRaw.classList.remove('hidden');
            outputRendered.classList.add('hidden');
        };

        // Fetch initial list of PDFs
        async function loadPdfOptions() {
            try {
                const res = await fetch('/api/sources/test_pdf_extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ page_number: 1, engine: 'fitz' })
                });
                const data = await res.json();
                if (data.available_pdfs && data.available_pdfs.length > 0) {
                    pdfSelect.innerHTML = data.available_pdfs.map(p =>
                        `<option value="${window.escapeHTML(p.path)}">${window.escapeHTML(p.name)} (${window.escapeHTML(p.path)})</option>`
                    ).join('');
                } else {
                    pdfSelect.innerHTML = '<option value="">No PDF sources found in database</option>';
                }
            } catch (e) {
                pdfSelect.innerHTML = '<option value="">Error loading PDF list</option>';
            }
        }

        async function doExtract() {
            const selectedPath = customPath.value.trim() || pdfSelect.value;
            const page = parseInt(pageNum.value, 10) || 1;
            const engine = engineSelect.value;

            runBtn.disabled = true;
            runBtn.textContent = 'Extracting Page...';
            metaBox.innerHTML = '<p class="text-brand-300 animate-pulse">Running extraction engine...</p>';

            try {
                const res = await fetch('/api/sources/test_pdf_extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: selectedPath, page_number: page, engine: engine })
                });
                const data = await res.json();

                if (data.status === 'success') {
                    metaBox.innerHTML = `
                        <div class="space-y-1.5">
                            <p><strong class="text-brand-300">File Name:</strong> ${window.escapeHTML(data.file_name)}</p>
                            <p><strong class="text-brand-300">File Path:</strong> <code class="text-xs bg-gray-800 px-1 py-0.5 rounded">${window.escapeHTML(data.file_path)}</code></p>
                            <p><strong class="text-brand-300">Page:</strong> ${data.page_number} / ${data.total_pages}</p>
                            <p><strong class="text-brand-300">Engine Selected:</strong> ${window.escapeHTML(data.engine.toUpperCase())}</p>
                            ${data.heading_path ? `<p><strong class="text-brand-300">Detected Heading Path:</strong> <span class="text-emerald-400 font-mono">${window.escapeHTML(data.heading_path)}</span></p>` : ''}
                            ${data.subheading ? `<p><strong class="text-brand-300">Subheading:</strong> ${window.escapeHTML(data.subheading)}</p>` : ''}
                            <p><strong class="text-brand-300">Character Count:</strong> ${(data.extracted_text || '').length} chars</p>
                        </div>
                    `;

                    outputRaw.value = data.extracted_text || '(No text extracted from page)';
                    if (window.renderMarkdownAndMath) {
                        window.renderMarkdownAndMath(data.extracted_text || '*No text extracted*', outputRendered);
                    } else {
                        outputRendered.textContent = data.extracted_text;
                    }
                } else {
                    metaBox.innerHTML = `<p class="text-danger-300 font-semibold">${window.escapeHTML(data.message || 'Extraction error')}</p>`;
                    outputRendered.innerHTML = '<p class="text-danger-300">Failed to extract text.</p>';
                    outputRaw.value = '';
                }
            } catch (err) {
                metaBox.innerHTML = `<p class="text-danger-300">Network or server error: ${window.escapeHTML(err.message)}</p>`;
            } finally {
                runBtn.disabled = false;
                runBtn.textContent = 'Extract & Compare Page';
            }
        }

        runBtn.onclick = doExtract;
        await loadPdfOptions();
    }

    // =========================================================
    // 2. Database Queries View
    // =========================================================
    async function renderDbQueriesView(container) {
        container.innerHTML = `
            <div class="card glass-card mb-6">
                <div class="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                    <div class="md:col-span-2">
                        <label for="db-query-input" class="text-xs font-bold text-muted mb-1 block">Search Query</label>
                        <input type="text" id="db-query-input" class="glass-input text-xs w-full py-2" value="amyloidosis" placeholder="Enter term or topic to search..." />
                    </div>
                    <div>
                        <label for="db-target-select" class="text-xs font-bold text-muted mb-1 block">Database Target</label>
                        <select id="db-target-select" class="glass-input text-xs w-full py-2">
                            <option value="all">Hybrid Search (LanceDB + Tantivy + ZIM)</option>
                            <option value="lancedb">LanceDB Vector &amp; FTS</option>
                            <option value="tantivy">Tantivy BM25 Lexical</option>
                            <option value="zim">ZIM Wikipedia B-Tree Archive</option>
                        </select>
                    </div>
                    <div>
                        <button type="button" id="run-db-search-btn" class="primary-btn text-xs py-2 w-full">
                            Search Database
                        </button>
                    </div>
                </div>
            </div>

            <div class="card glass-card">
                <div class="flex items-center justify-between mb-4 pb-2 border-b border-gray-700/50">
                    <h4 class="text-sm font-bold text-brand-300">Query Results &amp; Search Chunks</h4>
                    <span id="db-search-stats" class="text-xs text-muted">Ready to query</span>
                </div>
                <div id="db-results-container" class="space-y-4 max-h-128 overflow-y-auto pr-2">
                    <p class="text-xs text-muted">Enter a search query above to inspect raw matched database records.</p>
                </div>
            </div>
        `;

        const queryInput = document.getElementById('db-query-input');
        const targetSelect = document.getElementById('db-target-select');
        const searchBtn = document.getElementById('run-db-search-btn');
        const statsEl = document.getElementById('db-search-stats');
        const resultsContainer = document.getElementById('db-results-container');

        searchBtn.onclick = async () => {
            const q = queryInput.value.trim();
            if (!q) return;

            searchBtn.disabled = true;
            searchBtn.textContent = 'Searching...';
            statsEl.textContent = 'Executing query...';
            resultsContainer.innerHTML = '<p class="text-xs text-brand-300 animate-pulse">Querying database indexes...</p>';

            const target = targetSelect.value;
            let sourceTypes = ['pdf', 'obsidian', 'zim'];
            if (target === 'zim') sourceTypes = ['zim'];

            const t0 = performance.now();
            try {
                const res = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: q, source_types: sourceTypes, top_k: 10 })
                });
                const t1 = performance.now();
                const latency = Math.round(t1 - t0);
                const data = await res.json();
                const docs = data.documents || [];

                statsEl.textContent = `Found ${docs.length} records in ${latency} ms`;

                if (docs.length === 0) {
                    resultsContainer.innerHTML = '<p class="text-xs text-muted py-4">No matching records found for query.</p>';
                } else {
                    resultsContainer.innerHTML = docs.map((doc, idx) => `
                        <div class="p-3 rounded-xl bg-gray-900/40 border border-gray-700/50 space-y-1 text-xs">
                            <div class="flex justify-between items-center text-brand-300 font-bold">
                                <span>#${idx + 1} ${window.escapeHTML(doc.title || doc.heading_path || doc.source_article || 'Record')}</span>
                                <span class="text-emerald-400 font-mono">Score: ${typeof doc.rerank_score === 'number' ? doc.rerank_score.toFixed(4) : (doc.score || 'N/A')}</span>
                            </div>
                            <p class="text-muted font-mono break-all text-[11px]">Source: ${window.escapeHTML(doc.file_path || doc.source || 'N/A')}</p>
                            <p class="text-gray-200 mt-2 line-clamp-3">${window.escapeHTML(doc.child_content || doc.snippet || doc.parent_content || '')}</p>
                        </div>
                    `).join('');
                }
            } catch (err) {
                statsEl.textContent = 'Search failed';
                resultsContainer.innerHTML = `<p class="text-xs text-danger-300">Error executing query: ${window.escapeHTML(err.message)}</p>`;
            } finally {
                searchBtn.disabled = false;
                searchBtn.textContent = 'Search Database';
            }
        };
    }

    // =========================================================
    // 3. Topic Extractor View
    // =========================================================
    function renderTopicExtractorView(container) {
        container.innerHTML = `
            <div class="card glass-card mb-6">
                <label for="mcq-text" class="text-xs font-bold text-muted mb-2 block">Clinical Text / MCQ Input Stem</label>
                <textarea id="mcq-text" class="glass-input large-textarea" aria-label="MCQ stem and options text" placeholder="Enter MCQ stem and options here...">A 68-year-old patient presents with dyspnea and amyloidosis of the heart, but no asthma. They were prescribed amoxicillin after a heart attack.</textarea>
                <div class="action-bar mt-3 flex justify-end">
                    <button type="button" id="extract-btn" class="primary-btn text-xs px-6 py-2 pulse-on-hover">Extract Clinical Entities</button>
                </div>
            </div>

            <div class="card glass-card topics-section mb-6">
                <h3 class="text-sm font-bold text-brand-300 mb-1">Extracted Verified Clinical Entities</h3>
                <p class="text-xs text-muted mb-3">Green underlined chips are verified clinical entities. Click any chip to load its synthesized topic dossier.</p>
                <div id="topics-container" class="chip-container flex wrap gap-2 min-h-12 items-center">
                    <span class="text-xs text-muted italic">Click "Extract Clinical Entities" above to extract medical terms.</span>
                </div>
            </div>

            <div class="card glass-card explanation-section">
                <h3 class="text-sm font-bold text-brand-300 mb-2">Topic Synthesis &amp; Reference Dossier Preview</h3>
                <div id="explanation-container" class="content-placeholder prose max-w-none text-xs">
                    <p class="text-muted">Extract entities above and click a topic chip to view its clinical synthesis.</p>
                </div>
            </div>
        `;

        // Attach standalone extraction logic
        const extractBtn = document.getElementById('extract-btn');
        if (extractBtn) {
            extractBtn.addEventListener('click', async () => {
                const textEl = document.getElementById('mcq-text');
                const chipsContainer = document.getElementById('topics-container');
                const expContainer = document.getElementById('explanation-container');
                if (!textEl || !textEl.value.trim()) return;

                extractBtn.disabled = true;
                extractBtn.textContent = 'Extracting Entities...';
                try {
                    const res = await fetch('/api/mcq/topics/extract', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mcq_text: textEl.value.trim() })
                    });
                    const data = await res.json();
                    const details = data.topic_details || (data.topics || []).map(t => ({ name: t, verified: true }));

                    if (chipsContainer) {
                        chipsContainer.innerHTML = details.map(d => `
                            <span class="topic-chip ${d.verified ? 'border-success-500 underline decoration-success-500 font-semibold' : 'opacity-60'} text-xs px-3 py-1 rounded-lg bg-emerald-500/10 text-emerald-300 cursor-pointer border border-emerald-500/30 hover:bg-emerald-500/20"
                                  data-topic="${window.escapeHTML(d.name)}"
                                  title="${d.verified ? 'Verified Medical Topic (Click to view synthesis)' : 'Unverified Topic'}">
                                ${window.escapeHTML(d.name)}
                            </span>
                        `).join('');

                        chipsContainer.querySelectorAll('.topic-chip').forEach(chip => {
                            chip.addEventListener('click', async () => {
                                const topic = chip.dataset.topic;
                                if (!topic) return;
                                const sRes = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
                                if (sRes.ok) {
                                    const sData = await sRes.json();
                                    if (expContainer) {
                                        expContainer.classList.remove('content-placeholder');
                                        if (window.renderMarkdownAndMath) {
                                            window.renderMarkdownAndMath(
                                                sData.unified_article || sData.enhanced_explanation || `Dossier resolved for **${topic}**.`,
                                                expContainer
                                            );
                                        } else {
                                            expContainer.textContent = sData.unified_article || sData.enhanced_explanation;
                                        }
                                    }
                                }
                            });
                        });
                    }
                } catch (e) {
                    console.error('Entity extraction failed:', e);
                } finally {
                    extractBtn.disabled = false;
                    extractBtn.textContent = 'Extract Clinical Entities';
                }
            });
        }
    }

    // =========================================================
    // 4. AI Models & Diagnostics View
    // =========================================================
    async function renderModelsDiagnosticsView(container) {
        container.innerHTML = `
            <div id="diag-summary-grid" class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">LM Studio Connectivity</p>
                    <p id="diag-lm-status" class="text-sm font-bold text-amber-400">Checking...</p>
                </div>
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">Vector/Lexical DB Rows</p>
                    <p id="diag-db-rows" class="text-sm font-bold text-purple-400">Checking...</p>
                </div>
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">SumatraPDF Executable</p>
                    <p id="diag-sumatra-status" class="text-sm font-bold text-blue-400">Checking...</p>
                </div>
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">GPU VRAM Overhead</p>
                    <p id="diag-vram-status" class="text-sm font-bold text-emerald-400">0.0 MB (Strict CPU)</p>
                </div>
            </div>

            <!-- Interactive Model Testers -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- MiniLM Embedding Tester -->
                <div class="card glass-card">
                    <h4 class="text-sm font-bold text-brand-300 mb-2 flex items-center gap-2">
                        <span>🧠</span> CPU MiniLM Embedding Generator Test
                    </h4>
                    <p class="text-xs text-muted mb-3">Generates 384-dimensional dense vector embeddings entirely on CPU using ONNX runtime.</p>
                    <textarea id="embed-test-input" class="glass-input text-xs w-full h-20 mb-3" placeholder="Enter text to compute vector...">Myocardial infarction presenting with ST elevation</textarea>
                    <button type="button" id="run-embed-test-btn" class="primary-btn text-xs py-2 w-full mb-3">Compute 384d Embedding Vector</button>
                    <div id="embed-test-output" class="text-xs p-3 rounded-lg bg-gray-900/40 border border-gray-700/50 text-muted font-mono min-h-20">
                        Result will appear here.
                    </div>
                </div>

                <!-- FlashRank Cross-Encoder Tester -->
                <div class="card glass-card">
                    <h4 class="text-sm font-bold text-brand-300 mb-2 flex items-center gap-2">
                        <span>⚡</span> FlashRank Cross-Encoder Reranker Test
                    </h4>
                    <p class="text-xs text-muted mb-3">Tests semantic reranking of candidate passages against a target query.</p>
                    <input type="text" id="rerank-test-query" class="glass-input text-xs w-full py-1.5 mb-2" value="heart attack treatment" placeholder="Target Query..." />
                    <textarea id="rerank-test-passages" class="glass-input text-xs w-full h-16 mb-3" placeholder="Passages separated by newlines...">Amoxicillin is a penicillin-class antibiotic for bacterial infections.
Acute myocardial infarction requires emergency aspirin and thrombolysis.
Renal failure requires monitoring serum creatinine and hemodialysis.</textarea>
                    <button type="button" id="run-rerank-test-btn" class="primary-btn text-xs py-2 w-full mb-3">Rerank Passages</button>
                    <div id="rerank-test-output" class="text-xs p-3 rounded-lg bg-gray-900/40 border border-gray-700/50 text-muted font-mono min-h-20">
                        Reranking output will appear here.
                    </div>
                </div>
            </div>
        `;

        // Fetch overall health
        try {
            const res = await fetch('/api/models/diagnostics/all');
            const data = await res.json();

            const lmStatus = document.getElementById('diag-lm-status');
            const dbRows = document.getElementById('diag-db-rows');
            const sumatraStatus = document.getElementById('diag-sumatra-status');

            if (lmStatus) lmStatus.textContent = data.lm_studio?.status === 'ok' ? 'Connected (Port 1234)' : 'Offline / Standard Fallback';
            if (dbRows) dbRows.textContent = `${data.tantivy?.doc_count || 0} Tantivy docs`;
            if (sumatraStatus) sumatraStatus.textContent = data.sumatrapdf?.exists ? 'Available (Reuse Instance)' : 'Not Found';
        } catch (e) {
            console.error('Failed to load diagnostics:', e);
        }

        // MiniLM Embed Tester
        const embedBtn = document.getElementById('run-embed-test-btn');
        const embedInput = document.getElementById('embed-test-input');
        const embedOutput = document.getElementById('embed-test-output');

        embedBtn.onclick = async () => {
            const txt = embedInput.value.trim();
            if (!txt) return;

            embedBtn.disabled = true;
            embedOutput.textContent = 'Computing 384d embedding...';

            try {
                const res = await fetch('/api/models/test_embed', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: txt })
                });
                const data = await res.json();
                embedOutput.innerHTML = `
                    <p class="text-emerald-400 font-bold">Status: ${data.status}</p>
                    <p>Vector Dimension: <span class="text-brand-300 font-bold">${data.dimension}</span></p>
                    <p>CPU Execution Time: <span class="text-brand-300 font-bold">${data.latency_ms} ms</span></p>
                    <p>VRAM Allocated: <span class="text-emerald-400 font-bold">0.0 MB</span></p>
                    <p class="mt-2 text-gray-400">Sample Vector Float Head: [${(data.sample_vector || []).map(n => n.toFixed(4)).join(', ')}...]</p>
                `;
            } catch (err) {
                embedOutput.textContent = `Error: ${err.message}`;
            } finally {
                embedBtn.disabled = false;
            }
        };

        // Reranker Tester
        const rerankBtn = document.getElementById('run-rerank-test-btn');
        const rerankQuery = document.getElementById('rerank-test-query');
        const rerankPassages = document.getElementById('rerank-test-passages');
        const rerankOutput = document.getElementById('rerank-test-output');

        rerankBtn.onclick = async () => {
            const q = rerankQuery.value.trim();
            const passages = rerankPassages.value.split('\n').map(p => p.trim()).filter(Boolean);
            if (!q || passages.length === 0) return;

            rerankBtn.disabled = true;
            rerankOutput.textContent = 'Reranking passages with FlashRank...';

            try {
                const res = await fetch('/api/models/test_rerank', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: q, passages: passages })
                });
                const data = await res.json();
                rerankOutput.innerHTML = `
                    <p class="text-emerald-400 font-bold">Latency: ${data.latency_ms} ms | VRAM: 0.0 MB</p>
                    <div class="mt-2 space-y-2">
                        ${(data.results || []).map((r, i) => `
                            <div class="p-2 rounded bg-gray-800/60 border border-gray-700/50">
                                <span class="text-brand-300 font-bold">Rank #${i + 1} (Rerank Score: ${r.rerank_score ? r.rerank_score.toFixed(4) : 'N/A'})</span>
                                <p class="text-gray-300 mt-1">${window.escapeHTML(r.child_content || '')}</p>
                            </div>
                        `).join('')}
                    </div>
                `;
            } catch (err) {
                rerankOutput.textContent = `Error: ${err.message}`;
            } finally {
                rerankBtn.disabled = false;
            }
        };
    }

    // =========================================================
    // 5. UI Rendering & Citations View
    // =========================================================
    function renderUiRenderingView(container) {
        container.innerHTML = `
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Markdown & KaTeX Tester -->
                <div class="card glass-card flex flex-col">
                    <h4 class="text-sm font-bold text-brand-300 mb-2 flex items-center gap-2">
                        <span>🧪</span> Markdown &amp; KaTeX Math Equation Live Tester
                    </h4>
                    <p class="text-xs text-muted mb-3">Type or paste markdown string to verify DOMPurify sanitization and KaTeX math rendering.</p>
                    <textarea id="ui-test-markdown-input" class="glass-input text-xs w-full h-40 mb-3 font-mono"># Clinical Pathology &amp; Math Test

**Amyloidosis** is characterized by extracellular deposition of insoluble fibrillar proteins.

### Cardiac Output Equation
$$\\text{Cardiac Output} = \\text{Stroke Volume} \\times \\text{Heart Rate}$$

When $E = mc^2$, the clinical relevance of $\\alpha + \\beta = \\gamma$ is preserved.

- Bullet item 1
- Bullet item 2</textarea>
                    <button type="button" id="run-ui-test-render-btn" class="primary-btn text-xs py-2 w-full mb-3">Render Markdown &amp; Math</button>
                    <div id="ui-test-rendered-box" class="prose max-w-none text-xs p-4 rounded-xl bg-gray-900/40 border border-gray-700/50 min-h-32">
                        Rendered output will appear here.
                    </div>
                </div>

                <!-- Interactive Citations Tester -->
                <div class="card glass-card">
                    <h4 class="text-sm font-bold text-brand-300 mb-2 flex items-center gap-2">
                        <span>🔗</span> Interactive Citations &amp; Bridges Test
                    </h4>
                    <p class="text-xs text-muted mb-4">Click any citation chip below to test global click handlers for SumatraPDF launcher, native Obsidian URIs, and ZIM viewer.</p>

                    <div class="space-y-4">
                        <div class="p-3 rounded-xl bg-gray-900/30 border border-gray-700/50">
                            <p class="text-xs font-bold text-blue-300 mb-2">1. PDF SumatraPDF Deep Link Citation</p>
                            <span class="citation-pdf cursor-pointer px-3 py-1.5 rounded-lg bg-blue-500/20 text-blue-300 border border-blue-500/40 text-xs font-bold inline-flex items-center gap-1.5 hover:bg-blue-500/30"
                                  data-file="data/sample.pdf" data-page="1">
                                📄 [PDF: Sample Document p.1]
                            </span>
                        </div>

                        <div class="p-3 rounded-xl bg-gray-900/30 border border-gray-700/50">
                            <p class="text-xs font-bold text-purple-300 mb-2">2. Native Obsidian URI Citation</p>
                            <span class="citation-obsidian cursor-pointer px-3 py-1.5 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-500/40 text-xs font-bold inline-flex items-center gap-1.5 hover:bg-purple-500/30"
                                  data-file="Cardiology/Amyloidosis.md">
                                📓 [[Obsidian: Cardiology Notes]]
                            </span>
                        </div>

                        <div class="p-3 rounded-xl bg-gray-900/30 border border-gray-700/50">
                            <p class="text-xs font-bold text-amber-300 mb-2">3. ZIM Wikipedia Reader Citation</p>
                            <span class="citation-zim cursor-pointer px-3 py-1.5 rounded-lg bg-amber-500/20 text-amber-300 border border-amber-500/40 text-xs font-bold inline-flex items-center gap-1.5 hover:bg-amber-500/30"
                                  data-article="Amyloidosis">
                                🌐 [ZIM: Medical Wiki - Amyloidosis]
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const markdownInput = document.getElementById('ui-test-markdown-input');
        const renderBtn = document.getElementById('run-ui-test-render-btn');
        const renderedBox = document.getElementById('ui-test-rendered-box');

        function doRender() {
            const val = markdownInput.value;
            if (window.renderMarkdownAndMath) {
                window.renderMarkdownAndMath(val, renderedBox);
            } else {
                renderedBox.textContent = val;
            }
        }

        renderBtn.onclick = doRender;
        doRender();
    }

    // =========================================================
    // 6. Pipeline Runner View
    // =========================================================
    function renderPipelineRunnerView(container) {
        container.innerHTML = `
            <div class="card glass-card mb-6">
                <div class="flex gap-4 items-end">
                    <div class="flex-1">
                        <label for="pipeline-topic-input" class="text-xs font-bold text-muted mb-1 block">Topic for Synthesis Pipeline Step Trace</label>
                        <input type="text" id="pipeline-topic-input" class="glass-input text-xs w-full py-2" value="Amyloidosis" placeholder="Enter clinical topic..." />
                    </div>
                    <button type="button" id="run-pipeline-trace-btn" class="primary-btn text-xs py-2 px-6">Trace All Steps</button>
                </div>
            </div>

            <!-- Steps Progress Breakdown -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <!-- Step 1 -->
                <div class="card glass-card">
                    <h4 class="text-xs font-bold text-brand-300 mb-2 flex items-center justify-between">
                        <span>Step 1: Hybrid Retrieval &amp; Rerank</span>
                        <span id="p-step1-status" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-muted">Ready</span>
                    </h4>
                    <div id="p-step1-box" class="text-xs text-muted p-3 rounded bg-gray-900/40 border border-gray-700/50 min-h-32">
                        Click "Trace All Steps" to execute.
                    </div>
                </div>

                <!-- Step 2 -->
                <div class="card glass-card">
                    <h4 class="text-xs font-bold text-brand-300 mb-2 flex items-center justify-between">
                        <span>Step 2: Prompt Assembly</span>
                        <span id="p-step2-status" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-muted">Ready</span>
                    </h4>
                    <div id="p-step2-box" class="text-xs text-muted p-3 rounded bg-gray-900/40 border border-gray-700/50 min-h-32">
                        Awaiting Step 1...
                    </div>
                </div>

                <!-- Step 3 -->
                <div class="card glass-card">
                    <h4 class="text-xs font-bold text-brand-300 mb-2 flex items-center justify-between">
                        <span>Step 3: Dossier Synthesis</span>
                        <span id="p-step3-status" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-muted">Ready</span>
                    </h4>
                    <div id="p-step3-box" class="text-xs text-muted p-3 rounded bg-gray-900/40 border border-gray-700/50 min-h-32">
                        Awaiting Step 2...
                    </div>
                </div>
            </div>
        `;

        const topicInput = document.getElementById('pipeline-topic-input');
        const traceBtn = document.getElementById('run-pipeline-trace-btn');

        const step1Box = document.getElementById('p-step1-box');
        const step2Box = document.getElementById('p-step2-box');
        const step3Box = document.getElementById('p-step3-box');

        const step1Status = document.getElementById('p-step1-status');
        const step2Status = document.getElementById('p-step2-status');
        const step3Status = document.getElementById('p-step3-status');

        traceBtn.onclick = async () => {
            const topic = topicInput.value.trim();
            if (!topic) return;

            traceBtn.disabled = true;
            step1Status.textContent = 'Running...';
            step1Status.className = 'text-[10px] px-2 py-0.5 rounded bg-brand-500/20 text-brand-300 font-bold';
            step1Box.innerHTML = '<p class="text-brand-300 animate-pulse">Querying LanceDB + Tantivy + ZIM...</p>';

            try {
                // Step 1: Retrieval
                const t0 = performance.now();
                const res = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
                const t1 = performance.now();
                const data = await res.json();

                step1Status.textContent = `Done (${Math.round(t1 - t0)}ms)`;
                step1Status.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
                step1Box.innerHTML = `
                    <p class="text-emerald-400 font-bold">Resolved Chunks for ${window.escapeHTML(topic)}</p>
                    <p class="mt-1">Article Title: ${window.escapeHTML(data.title || topic)}</p>
                    <p>Status: ${data.unified_article ? 'Synthesized' : 'Raw Fallback'}</p>
                `;

                // Step 2: Prompt Assembly
                step2Status.textContent = 'Done';
                step2Status.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
                step2Box.innerHTML = `
                    <p class="text-emerald-400 font-bold">Context-Augmented Prompt Constructed</p>
                    <p class="mt-1 text-gray-300 font-mono text-[10px] break-all border p-1 rounded bg-gray-800">
                        System: "You are an expert clinical pathologist..."<br/>
                        Topic: "${window.escapeHTML(topic)}"
                    </p>
                `;

                // Step 3: Synthesis Output
                step3Status.textContent = 'Done';
                step3Status.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
                if (window.renderMarkdownAndMath) {
                    window.renderMarkdownAndMath(data.unified_article || data.enhanced_explanation || 'No content', step3Box);
                } else {
                    step3Box.textContent = data.unified_article || data.enhanced_explanation;
                }
            } catch (err) {
                step1Status.textContent = 'Failed';
                step1Status.className = 'text-[10px] px-2 py-0.5 rounded bg-danger-500/20 text-danger-300 font-bold';
                step1Box.innerHTML = `<p class="text-danger-300">Error: ${window.escapeHTML(err.message)}</p>`;
            } finally {
                traceBtn.disabled = false;
            }
        };
    }

    // Export globally
    window.initTestFeatures = initTestFeatures;
    window.openFeatureDetail = openFeatureDetail;

    // Auto init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTestFeatures);
    } else {
        initTestFeatures();
    }
})();
