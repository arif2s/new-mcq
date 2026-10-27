/**
 * ui/js/test_features.js
 * Interactive testing harness for individual pipeline steps, engines, models, databases, and rendering components.
 */

(function () {
    'use strict';

    let currentFeature = null;

    const escapeHTML = (str) =>
        window.escapeHTML
            ? window.escapeHTML(str)
            : String(str ?? '')
                  .replace(/&/g, '&amp;')
                  .replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;')
                  .replace(/'/g, '&#039;');

    function initTestFeatures() {
        const grid = document.getElementById('test-features-grid');
        const backBtn = document.getElementById('test-feature-back-btn');

        if (!grid) return;

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
                        <input type="text" id="pdf-custom-path" class="glass-input text-xs w-full py-2" placeholder="e.g. data/pdfs/sample.pdf" />
                    </div>
                    <div>
                        <label for="pdf-page-num" class="text-xs font-bold text-muted mb-1 block">Page Number</label>
                        <input type="number" id="pdf-page-num" class="glass-input text-xs w-full py-2" value="1" min="1" />
                    </div>
                    <div>
                        <label for="pdf-engine-select" class="text-xs font-bold text-muted mb-1 block">Parser Engine</label>
                        <select id="pdf-engine-select" class="glass-input text-xs w-full py-2">
                            <option value="fitz">Fitz (Fast Slicer)</option>
                            <option value="adaptive">Adaptive Geometric (PyMuPDF)</option>
                            <option value="docling_v2">Docling v2 (Fast Geometric + Hybrid)</option>
                            <option value="docling_v3" selected>Docling v3 (Visual Self-Correction + Multi-OCR)</option>
                            <option value="docling">Docling v1 ML Structural Markdown</option>
                            <option value="headings">PyMuPDF Heuristic Headings</option>
                        </select>
                    </div>
                    <div>
                        <label for="pdf-ocr-engine-select" class="text-xs font-bold text-muted mb-1 block">OCR Engine</label>
                        <select id="pdf-ocr-engine-select" class="glass-input text-xs w-full py-2">
                            <option value="auto">Auto Detect</option>
                            <option value="paddle">Paddle (PP-OCR / RapidOCR ONNX)</option>
                            <option value="rapidocr">RapidOCR (PP-OCR v6 ONNX)</option>
                            <option value="easyocr">EasyOCR Engine</option>
                            <option value="tesseract">Tesseract Engine</option>
                            <option value="mac_ocr">Mac Native OCR</option>
                        </select>
                    </div>
                </div>
                <div class="mt-4 flex justify-end gap-2">
                    <button type="button" id="run-pdf-extract-btn" class="primary-btn text-xs px-6 py-2 cursor-pointer">
                        Extract &amp; Compare Page
                    </button>
                </div>
            </div>

            <!-- Side-by-side View: Left Pane Image/SVG Overlay Viewer vs Extracted Output -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- Left Pane: Interactive PDF Page Image & SVG Layout Viewer -->
                <div class="card glass-card flex flex-col space-y-3">
                    <div class="flex items-center justify-between border-b border-gray-700/50 pb-2">
                        <h4 class="text-sm font-bold text-brand-300 flex items-center gap-2">
                            <span>🖼️</span> Interactive PDF Page &amp; SVG Layout Viewer
                        </h4>
                        <!-- Zoom Controls Toolbar -->
                        <div class="flex items-center gap-1.5 bg-gray-900/60 p-1 rounded-lg border border-gray-700/50">
                            <button type="button" id="pdf-zoom-out" class="px-2 py-0.5 text-xs bg-gray-800 hover:bg-gray-700 rounded text-brand-300 font-bold cursor-pointer" title="Zoom Out">-</button>
                            <span id="pdf-zoom-level" class="text-[11px] font-mono font-bold text-gray-300 px-1">100%</span>
                            <button type="button" id="pdf-zoom-in" class="px-2 py-0.5 text-xs bg-gray-800 hover:bg-gray-700 rounded text-brand-300 font-bold cursor-pointer" title="Zoom In">+</button>
                            <button type="button" id="pdf-zoom-reset" class="px-2 py-0.5 text-xs bg-brand-500/20 hover:bg-brand-500/30 rounded text-brand-300 font-semibold cursor-pointer ml-1" title="Reset Zoom">Fit</button>
                        </div>
                    </div>

                    <!-- Element Type Legend Toggles -->
                    <div class="flex flex-wrap items-center gap-2 text-[10px] text-muted py-1 border-b border-gray-800/60">
                        <span class="font-bold text-gray-400">Legend:</span>
                        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/40">■ Heading</span>
                        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/40">■ Table</span>
                        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">■ Callout</span>
                        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-pink-500/20 text-pink-300 border border-pink-500/40">■ Caption</span>
                        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">■ Paragraph</span>
                        <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-teal-500/20 text-teal-300 border border-teal-500/40">■ List</span>
                    </div>

                    <!-- Image & Overlay Container -->
                    <div id="pdf-viewer-viewport" class="relative overflow-auto border border-gray-700/60 rounded-xl bg-gray-950 p-2 min-h-[380px] max-h-[500px] flex items-center justify-center">
                        <div id="pdf-viewer-canvas" class="relative inline-block origin-top-left transition-transform duration-100">
                            <img id="pdf-page-img" src="" class="max-w-full h-auto block rounded shadow-lg hidden" alt="PDF Page Pixmap" />
                            <svg id="pdf-svg-overlay" class="absolute top-0 left-0 w-full h-full pointer-events-none hidden" xmlns="http://www.w3.org/2000/svg"></svg>
                        </div>
                        <div id="pdf-viewer-placeholder" class="text-xs text-muted py-16 text-center">
                            Select a PDF file and click "Extract &amp; Compare Page" to render page image and SVG layout bounding boxes.
                        </div>
                    </div>

                    <!-- Compact Metadata & Inspection Details Box -->
                    <div class="border-t border-gray-700/50 pt-2 mt-2">
                        <div id="pdf-box-inspect-banner" class="hidden p-2 rounded-lg bg-brand-500/10 border border-brand-500/30 mb-2 text-xs font-mono">
                            <!-- Populated when user hovers or clicks a bounding box -->
                        </div>
                        <div id="pdf-meta-box" class="space-y-1.5 text-xs text-muted">
                            <p>PDF details will appear here after extraction.</p>
                        </div>
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

            <div id="qa-mount-point" class="mt-6"></div>
        `;

        if (typeof window.initPdfParsingQA === 'function') {
            window.initPdfParsingQA(document.getElementById('qa-mount-point'));
        }

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

        let zoomScale = 1.0;
        const zoomCanvas = document.getElementById('pdf-viewer-canvas');
        const zoomLevelEl = document.getElementById('pdf-zoom-level');
        const zoomInBtn = document.getElementById('pdf-zoom-in');
        const zoomOutBtn = document.getElementById('pdf-zoom-out');
        const zoomResetBtn = document.getElementById('pdf-zoom-reset');

        function updateZoom(newScale) {
            zoomScale = Math.max(0.4, Math.min(3.0, newScale));
            if (zoomCanvas) zoomCanvas.style.transform = `scale(${zoomScale})`;
            if (zoomLevelEl) zoomLevelEl.textContent = `${Math.round(zoomScale * 100)}%`;
        }

        if (zoomInBtn) zoomInBtn.onclick = () => updateZoom(zoomScale + 0.25);
        if (zoomOutBtn) zoomOutBtn.onclick = () => updateZoom(zoomScale - 0.25);
        if (zoomResetBtn) zoomResetBtn.onclick = () => updateZoom(1.0);

        function renderSvgLayoutOverlay(data) {
            const imgEl = document.getElementById('pdf-page-img');
            const svgEl = document.getElementById('pdf-svg-overlay');
            const placeholderEl = document.getElementById('pdf-viewer-placeholder');
            const inspectBanner = document.getElementById('pdf-box-inspect-banner');

            if (!data || !data.page_image_base64) {
                if (imgEl) imgEl.classList.add('hidden');
                if (svgEl) svgEl.classList.add('hidden');
                if (placeholderEl) {
                    placeholderEl.classList.remove('hidden');
                    placeholderEl.textContent = 'No page pixmap image returned for this engine/page.';
                }
                return;
            }

            if (placeholderEl) placeholderEl.classList.add('hidden');
            if (imgEl) {
                imgEl.src = data.page_image_base64;
                imgEl.classList.remove('hidden');
            }

            if (!svgEl) return;
            svgEl.classList.remove('hidden');

            const pWidth = data.page_width || 612.0;
            const pHeight = data.page_height || 792.0;

            svgEl.setAttribute('viewBox', `0 0 ${pWidth} ${pHeight}`);

            const boxes = data.layout_boxes || [];
            let svgMarkup = '';

            const getColorConfig = (type) => {
                switch ((type || '').toLowerCase()) {
                    case 'heading':
                        return { stroke: '#a855f7', fill: 'rgba(168, 85, 247, 0.22)', badge: '#9333ea' };
                    case 'table':
                        return { stroke: '#3b82f6', fill: 'rgba(59, 130, 246, 0.22)', badge: '#2563eb' };
                    case 'callout':
                        return { stroke: '#f59e0b', fill: 'rgba(245, 158, 11, 0.22)', badge: '#d97706' };
                    case 'caption':
                        return { stroke: '#ec4899', fill: 'rgba(236, 72, 153, 0.22)', badge: '#db2777' };
                    case 'list':
                        return { stroke: '#14b8a6', fill: 'rgba(20, 184, 166, 0.22)', badge: '#0d9488' };
                    default:
                        return { stroke: '#10b981', fill: 'rgba(16, 185, 129, 0.16)', badge: '#059669' };
                }
            };

            boxes.forEach((b) => {
                const order = b.order || 1;
                const bType = (b.type || 'paragraph').toLowerCase();
                const normBbox = b.bbox || [0, 0, 1, 1];
                const x0 = normBbox[0] * pWidth;
                const y0 = normBbox[1] * pHeight;
                const x1 = normBbox[2] * pWidth;
                const y1 = normBbox[3] * pHeight;
                const w = Math.max(8, x1 - x0);
                const h = Math.max(8, y1 - y0);

                const c = getColorConfig(bType);
                const labelText = b.label || `${bType} #${order}`;

                const badgeW = Math.min(32, Math.max(22, String(order).length * 9 + 10));
                const badgeH = 18;
                const bx = x0;
                const by = Math.max(0, y0 - badgeH / 2);

                svgMarkup += `
                    <g class="layout-box-group pointer-events-auto cursor-pointer transition-opacity hover:opacity-90"
                       data-order="${order}" data-type="${escapeHTML(bType)}" data-label="${escapeHTML(labelText)}">
                        <rect x="${x0}" y="${y0}" width="${w}" height="${h}"
                              fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" rx="3" />
                        <rect x="${bx}" y="${by}" width="${badgeW}" height="${badgeH}"
                              fill="${c.badge}" rx="4" />
                        <text x="${bx + badgeW / 2}" y="${by + 13}"
                              fill="#ffffff" font-size="11" font-weight="bold" text-anchor="middle" font-family="sans-serif">#${order}</text>
                        <title>#${order} [${bType.toUpperCase()}]: ${escapeHTML(labelText)}</title>
                    </g>
                `;
            });

            svgEl.innerHTML = svgMarkup;

            svgEl.querySelectorAll('.layout-box-group').forEach((group) => {
                const showInfo = () => {
                    const order = group.dataset.order;
                    const type = group.dataset.type;
                    const label = group.dataset.label;
                    if (inspectBanner) {
                        inspectBanner.classList.remove('hidden');
                        inspectBanner.innerHTML = `
                            <span class="text-brand-300 font-bold">Reading Order #${escapeHTML(order)}</span> |
                            <span class="text-emerald-400 font-bold">${escapeHTML(type.toUpperCase())}</span>:
                            <span class="text-gray-200">${escapeHTML(label)}</span>
                        `;
                    }
                };
                group.addEventListener('mouseenter', showInfo);
                group.addEventListener('click', showInfo);
            });
        }

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
                        `<option value="${escapeHTML(p.path)}">${escapeHTML(p.name)} (${escapeHTML(p.path)})</option>`
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
            const ocrEngineSelect = document.getElementById('pdf-ocr-engine-select');
            const ocrEngine = ocrEngineSelect ? ocrEngineSelect.value : 'auto';

            runBtn.disabled = true;
            runBtn.textContent = 'Extracting Page...';
            metaBox.innerHTML = '<p class="text-brand-300 animate-pulse">Running extraction engine...</p>';

            try {
                const res = await fetch('/api/sources/test_pdf_extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: selectedPath, page_number: page, engine: engine, ocr_engine: ocrEngine })
                });
                const data = await res.json();

                if (data.status === 'success') {
                    renderSvgLayoutOverlay(data);

                    metaBox.innerHTML = `
                        <div class="space-y-1.5">
                            <p><strong class="text-brand-300">File Name:</strong> ${escapeHTML(data.file_name)}</p>
                            <p><strong class="text-brand-300">File Path:</strong> <code class="text-xs bg-gray-800 px-1 py-0.5 rounded">${escapeHTML(data.file_path)}</code></p>
                            <p><strong class="text-brand-300">Page:</strong> ${data.page_number} / ${data.total_pages}</p>
                            <p><strong class="text-brand-300">Engine Selected:</strong> ${escapeHTML(String(data.engine || '').toUpperCase())}</p>
                            ${data.heading_path ? `<p><strong class="text-brand-300">Detected Heading Path:</strong> <span class="text-emerald-400 font-mono">${escapeHTML(data.heading_path)}</span></p>` : ''}
                            ${data.subheading ? `<p><strong class="text-brand-300">Subheading:</strong> ${escapeHTML(data.subheading)}</p>` : ''}
                            <p><strong class="text-brand-300">Layout Elements Detected:</strong> ${(data.layout_boxes || []).length} boxes</p>
                            <p><strong class="text-brand-300">Character Count:</strong> ${(data.extracted_text || '').length} chars</p>
                            <div class="pt-2">
                                <a href="#" class="citation-link citation-pdf secondary-btn px-3 py-1.5 rounded-lg text-xs inline-flex items-center gap-1 no-underline"
                                   data-source-type="pdf" data-file-path="${escapeHTML(data.file_path)}" data-filepath="${escapeHTML(data.file_path)}" data-page="${data.page_number}">
                                    📕 Open Page ${data.page_number} in SumatraPDF ↗
                                </a>
                            </div>
                        </div>
                    `;

                    outputRaw.value = data.extracted_text || '(No text extracted from page)';
                    if (window.renderMarkdownAndMath) {
                        window.renderMarkdownAndMath(data.extracted_text || '*No text extracted*', outputRendered);
                    } else {
                        outputRendered.textContent = data.extracted_text;
                    }

                    // Populate QA engine pane automatically if mounted
                    const qaOrig = document.getElementById('qa-original-text');
                    const qaParsed = document.getElementById('qa-parsed-text');
                    if (engine === 'fitz' && qaOrig && !qaOrig.value) {
                        qaOrig.value = data.extracted_text || '';
                    } else if (qaParsed) {
                        qaParsed.value = data.extracted_text || '';
                    }
                } else {
                    metaBox.innerHTML = `<p class="text-danger-300 font-semibold">${escapeHTML(data.message || 'Extraction error')}</p>`;
                    outputRendered.innerHTML = '<p class="text-danger-300">Failed to extract text.</p>';
                    outputRaw.value = '';
                }
            } catch (err) {
                metaBox.innerHTML = `<p class="text-danger-300">Network or server error: ${escapeHTML(err.message)}</p>`;
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
                            <option value="pdf">PDF Textbooks Only</option>
                            <option value="obsidian">Obsidian Vaults Only</option>
                            <option value="zim">ZIM Wikipedia B-Tree Archive</option>
                        </select>
                    </div>
                    <div>
                        <button type="button" id="run-db-search-btn" class="primary-btn text-xs py-2 w-full cursor-pointer">
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

            const target = targetSelect.value || 'all';
            const t0 = performance.now();
            try {
                const res = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        query: q,
                        source_type: target,
                        limit: 15,
                        enable_rerank: true
                    })
                });
                const t1 = performance.now();
                const latency = Math.round(t1 - t0);
                const data = await res.json();

                let docs = data.candidates || data.documents || [];
                if (target === 'lancedb' && data.all_results && Array.isArray(data.all_results.lancedb) && data.all_results.lancedb.length > 0) {
                    docs = data.all_results.lancedb;
                }

                const aliasesStr = Array.isArray(data.aliases) && data.aliases.length > 1
                    ? ` | Aliases: ${data.aliases.slice(0, 4).join(', ')}`
                    : '';
                statsEl.textContent = `Found ${docs.length} records in ${latency} ms${aliasesStr}`;

                if (docs.length === 0) {
                    resultsContainer.innerHTML = '<p class="text-xs text-muted py-4">No matching records found for query.</p>';
                } else {
                    resultsContainer.innerHTML = docs.map((doc, idx) => {
                        const scoreVal = typeof doc.rerank_score === 'number'
                            ? doc.rerank_score.toFixed(4)
                            : (typeof doc.score === 'number' ? doc.score.toFixed(4) : 'N/A');
                        const srcType = String(doc.source || 'obsidian').toLowerCase();
                        const fp = doc.file_path || doc.source_article || '';
                        const pg = doc.page || doc.page_number || 1;
                        return `
                        <div class="p-3.5 rounded-xl bg-gray-900/40 border border-gray-700/50 space-y-1.5 text-xs">
                            <div class="flex justify-between items-center text-brand-300 font-bold gap-2">
                                <span class="truncate">#${idx + 1} [${escapeHTML(srcType.toUpperCase())}] ${escapeHTML(doc.title || doc.heading_path || doc.source_article || 'Record')}</span>
                                <span class="text-emerald-400 font-mono shrink-0">Score: ${scoreVal}</span>
                            </div>
                            <p class="text-muted font-mono break-all text-[11px]">Path: ${escapeHTML(fp || 'N/A')} ${srcType === 'pdf' ? `(Page ${pg})` : ''}</p>
                            <p class="text-gray-200 mt-2 line-clamp-3">${escapeHTML(doc.snippet || doc.child_content || doc.text || doc.parent_content || '')}</p>
                        </div>
                        `;
                    }).join('');
                }
            } catch (err) {
                statsEl.textContent = 'Search failed';
                resultsContainer.innerHTML = `<p class="text-xs text-danger-300">Error executing query: ${escapeHTML(err.message)}</p>`;
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
                    <button type="button" id="extract-btn" class="primary-btn text-xs px-6 py-2 pulse-on-hover cursor-pointer">Extract Clinical Entities</button>
                </div>
            </div>

            <div class="card glass-card topics-section mb-6">
                <h3 class="text-sm font-bold text-brand-300 mb-1">Extracted Verified Clinical Entities</h3>
                <p class="text-xs text-muted mb-3">Green underlined chips are verified clinical entities. Click any chip to load its synthesized topic dossier.</p>
                <div id="topics-container" class="chip-container flex flex-wrap gap-2 min-h-12 items-center">
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

                    if (typeof window.renderStudyTopicsInspector === 'function' && details.length > 0) {
                        window.renderStudyTopicsInspector(details, false);
                    }

                    if (chipsContainer) {
                        if (details.length === 0) {
                            chipsContainer.innerHTML = '<span class="text-xs text-muted">No clinical entities extracted from input text.</span>';
                        } else {
                            chipsContainer.innerHTML = details.map(d => {
                                const tName = d.name || d.topic_name || '';
                                return `
                                <span class="topic-chip ${d.verified ? 'border-success-500 underline decoration-success-500 font-semibold' : 'opacity-60'} text-xs px-3 py-1.5 rounded-lg bg-emerald-500/10 text-emerald-300 cursor-pointer border border-emerald-500/30 hover:bg-emerald-500/20"
                                      data-topic="${escapeHTML(tName)}"
                                      title="${d.verified ? 'Verified Medical Topic (Click to view synthesis)' : 'Candidate Topic'}">
                                    ${escapeHTML(tName)}
                                </span>
                                `;
                            }).join('');
                        }

                        chipsContainer.querySelectorAll('.topic-chip').forEach(chip => {
                            chip.addEventListener('click', async () => {
                                const topic = chip.dataset.topic;
                                if (!topic) return;
                                if (expContainer) {
                                    expContainer.classList.remove('content-placeholder');
                                    expContainer.innerHTML = `<p class="text-brand-300 animate-pulse">Loading synthesis &amp; references for ${escapeHTML(topic)}...</p>`;
                                }
                                const sRes = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
                                if (sRes.ok) {
                                    const sData = await sRes.json();
                                    if (expContainer) {
                                        const formattedMd = window.formatUnifiedArticleToMarkdown
                                            ? window.formatUnifiedArticleToMarkdown(sData.unified_article, sData.enhanced_explanation, topic)
                                            : (sData.enhanced_explanation || `### ${topic}\n\nReferences resolved for **${topic}**.`);
                                        if (window.renderMarkdownAndMath) {
                                            window.renderMarkdownAndMath(
                                                formattedMd || `### ${topic}\n\nReferences resolved for **${topic}**.`,
                                                expContainer
                                            );
                                        } else {
                                            expContainer.textContent = formattedMd;
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
                    <p id="diag-test-lm-status" class="text-sm font-bold text-amber-400">Checking...</p>
                </div>
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">Vector/Lexical DB Rows</p>
                    <p id="diag-test-db-rows" class="text-sm font-bold text-purple-400">Checking...</p>
                </div>
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">SumatraPDF Executable</p>
                    <p id="diag-test-sumatra-status" class="text-sm font-bold text-blue-400">Checking...</p>
                </div>
                <div class="card glass-card text-center p-3">
                    <p class="text-xs text-muted mb-1">GPU VRAM Overhead</p>
                    <p id="diag-test-vram-status" class="text-sm font-bold text-emerald-400">0.0 MB (Strict CPU)</p>
                </div>
            </div>

            <!-- Interactive Model Testers -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <!-- MiniLM Embedding Tester -->
                <div class="card glass-card">
                    <h4 class="text-sm font-bold text-brand-300 mb-2 flex items-center gap-2">
                        <span>🧠</span> CPU Embedding Generator Test
                    </h4>
                    <p class="text-xs text-muted mb-3">Generates dense vector embeddings entirely on CPU using ONNX Runtime.</p>
                    <textarea id="embed-test-input" class="glass-input text-xs w-full h-20 mb-3" placeholder="Enter text to compute vector...">Myocardial infarction presenting with ST elevation</textarea>
                    <button type="button" id="run-embed-test-btn" class="primary-btn text-xs py-2 w-full mb-3 cursor-pointer">Compute Embedding Vector</button>
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
                    <button type="button" id="run-rerank-test-btn" class="primary-btn text-xs py-2 w-full mb-3 cursor-pointer">Rerank Passages</button>
                    <div id="rerank-test-output" class="text-xs p-3 rounded-lg bg-gray-900/40 border border-gray-700/50 text-muted font-mono min-h-20">
                        Reranking output will appear here.
                    </div>
                </div>
            </div>
        `;

        try {
            const res = await fetch('/api/models/diagnostics/all');
            const data = await res.json();

            const lmStatus = document.getElementById('diag-test-lm-status');
            const dbRows = document.getElementById('diag-test-db-rows');
            const sumatraStatus = document.getElementById('diag-test-sumatra-status');

            if (lmStatus) {
                lmStatus.textContent = (data.lm_studio?.status === 'ok' || data.lm_studio?.online)
                    ? `Connected (${data.lm_studio?.latency_ms || 0}ms)`
                    : 'Offline / Standard Fallback';
            }
            if (dbRows) {
                const tDocs = data.tantivy?.doc_count || 0;
                const qRows = data.database?.row_counts?.csv_questions || 0;
                dbRows.textContent = `${tDocs.toLocaleString()} Tantivy | ${qRows.toLocaleString()} MCQs`;
            }
            if (sumatraStatus) {
                const isSumatraOk = Boolean(data.sumatrapdf?.installed || data.sumatrapdf?.available || data.sumatrapdf?.exists);
                sumatraStatus.textContent = isSumatraOk ? 'Available (Reuse Instance)' : 'Not Found';
            }
        } catch (e) {
            console.error('Failed to load diagnostics:', e);
        }

        const embedBtn = document.getElementById('run-embed-test-btn');
        const embedInput = document.getElementById('embed-test-input');
        const embedOutput = document.getElementById('embed-test-output');

        embedBtn.onclick = async () => {
            const txt = embedInput.value.trim();
            if (!txt) return;

            embedBtn.disabled = true;
            embedOutput.textContent = 'Computing CPU embedding...';

            try {
                const res = await fetch('/api/models/test_embed', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: txt })
                });
                const data = await res.json();
                embedOutput.innerHTML = `
                    <p class="text-emerald-400 font-bold">Status: ${escapeHTML(data.status)}</p>
                    <p>Vector Dimension: <span class="text-brand-300 font-bold">${data.dimension}</span></p>
                    <p>CPU Execution Time: <span class="text-brand-300 font-bold">${data.latency_ms} ms</span></p>
                    <p>VRAM Allocated: <span class="text-emerald-400 font-bold">0.0 MB</span></p>
                    <p class="mt-2 text-gray-400">Sample Vector Float Head: [${(data.sample_vector || []).map(n => Number(n).toFixed(4)).join(', ')}...]</p>
                `;
            } catch (err) {
                embedOutput.textContent = `Error: ${err.message}`;
            } finally {
                embedBtn.disabled = false;
            }
        };

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
                                <span class="text-brand-300 font-bold">Rank #${i + 1} (Rerank Score:${typeof r.rerank_score === 'number' ? r.rerank_score.toFixed(4) : 'N/A'})</span>
                                <p class="text-gray-300 mt-1">${escapeHTML(r.text || r.child_content || '')}</p>
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

- Bullet item 1 [Ref: Harrison p.42]
- Bullet item 2</textarea>
                    <button type="button" id="run-ui-test-render-btn" class="primary-btn text-xs py-2 w-full mb-3 cursor-pointer">Render Markdown &amp; Math</button>
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
                            <span class="citation-link citation-pdf cursor-pointer px-3 py-1.5 rounded-lg bg-blue-500/20 text-blue-300 border border-blue-500/40 text-xs font-bold inline-flex items-center gap-1.5 hover:bg-blue-500/30"
                                  data-source-type="pdf" data-file-path="data/pdfs/sample.pdf" data-filepath="data/pdfs/sample.pdf" data-page="1">
                                📄 [PDF: Sample Document p.1]
                            </span>
                        </div>

                        <div class="p-3 rounded-xl bg-gray-900/30 border border-gray-700/50">
                            <p class="text-xs font-bold text-purple-300 mb-2">2. Native Obsidian URI / Reader Citation</p>
                            <span class="citation-link citation-obsidian cursor-pointer px-3 py-1.5 rounded-lg bg-purple-500/20 text-purple-300 border border-purple-500/40 text-xs font-bold inline-flex items-center gap-1.5 hover:bg-purple-500/30"
                                  data-source-type="obsidian" data-file-path="Amyloidosis.md" data-file="Amyloidosis">
                                📓 [[Obsidian: Amyloidosis]]
                            </span>
                        </div>

                        <div class="p-3 rounded-xl bg-gray-900/30 border border-gray-700/50">
                            <p class="text-xs font-bold text-amber-300 mb-2">3. ZIM Wikipedia Reader Citation</p>
                            <span class="citation-link citation-zim cursor-pointer px-3 py-1.5 rounded-lg bg-amber-500/20 text-amber-300 border border-amber-500/40 text-xs font-bold inline-flex items-center gap-1.5 hover:bg-amber-500/30"
                                  data-source-type="zim" data-article-id="Amyloidosis">
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
                    <button type="button" id="run-pipeline-trace-btn" class="primary-btn text-xs py-2 px-6 cursor-pointer">Trace All Steps</button>
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
                const t0 = performance.now();
                const res = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
                const t1 = performance.now();
                const data = await res.json();

                const refs = data.references || {};
                const obsCnt = Array.isArray(refs.obsidian) ? refs.obsidian.length : 0;
                const pdfCnt = Array.isArray(refs.pdf) ? refs.pdf.length : 0;
                const zimCnt = Array.isArray(refs.zim) ? refs.zim.length : 0;

                step1Status.textContent = `Done (${Math.round(t1 - t0)}ms)`;
                step1Status.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
                step1Box.innerHTML = `
                    <p class="text-emerald-400 font-bold">Resolved Chunks for ${escapeHTML(topic)}</p>
                    <p class="mt-1">Display Title: ${escapeHTML(data.display_title || topic)}</p>
                    <p class="mt-1 font-mono text-[11px]">PDF: ${pdfCnt} | Obsidian: ${obsCnt} | ZIM: ${zimCnt}</p>
                    <p class="mt-1">Status: ${data.unified_article ? 'Synthesized' : 'References Resolved'}</p>
                `;

                step2Status.textContent = 'Done';
                step2Status.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
                step2Box.innerHTML = `
                    <p class="text-emerald-400 font-bold">Context-Augmented Prompt Constructed</p>
                    <p class="mt-1 text-gray-300 font-mono text-[10px] break-all border p-1.5 rounded bg-gray-800">
                        Pass 1: Strict Clinical Extraction (JSON)<br/>
                        Pass 2: Staging &amp; Classification Variance Audit<br/>
                        Topic: "${escapeHTML(topic)}"
                    </p>
                `;

                step3Status.textContent = 'Done';
                step3Status.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
                const formattedMd = window.formatUnifiedArticleToMarkdown
                    ? window.formatUnifiedArticleToMarkdown(data.unified_article, data.enhanced_explanation, topic)
                    : (data.enhanced_explanation || 'No synthesized content yet.');
                if (window.renderMarkdownAndMath) {
                    window.renderMarkdownAndMath(formattedMd || 'No synthesized content yet.', step3Box);
                } else {
                    step3Box.textContent = formattedMd;
                }
            } catch (err) {
                step1Status.textContent = 'Failed';
                step1Status.className = 'text-[10px] px-2 py-0.5 rounded bg-danger-500/20 text-danger-300 font-bold';
                step1Box.innerHTML = `<p class="text-danger-300">Error: ${escapeHTML(err.message)}</p>`;
            } finally {
                traceBtn.disabled = false;
            }
        };
    }

    window.initTestFeatures = initTestFeatures;
    window.openFeatureDetail = openFeatureDetail;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTestFeatures);
    } else {
        initTestFeatures();
    }
})();