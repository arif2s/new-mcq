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
                            <option value="indra">Indra OCR ONNX (Auto Text + OCR)</option>
                            <option value="indra_base_heading_opt">Indra Base (Pure OCR + Heading Opt)</option>
                            <option value="indra_math">Indra OCR ONNX (+ LaTeX Math)</option>
                            <option value="indra_ocr">Indra OCR ONNX (Force Full OCR)</option>
                            <option value="adaptive">Adaptive Geometric (PyMuPDF)</option>
                            <option value="docling_v2">Docling v2 (Fast Geometric + Hybrid)</option>
                            <option value="docling_v1_heading_opt">Docling v1 (TableFormer + Heading Opt)</option>
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
                            <option value="indra">Indra OCR ONNX</option>
                            <option value="indra_math">Indra OCR (+ LaTeX Math)</option>
                            <option value="easyocr">EasyOCR Engine</option>
                            <option value="tesseract">Tesseract Engine</option>
                            <option value="mac_ocr">Mac Native OCR</option>
                        </select>
                    </div>
                </div>
                <div class="mt-3 flex items-center justify-between">
                    <div class="flex items-center gap-4 text-xs text-muted">
                        <label class="inline-flex items-center gap-1.5 cursor-pointer">
                            <input type="checkbox" id="indra-math-chk" class="rounded glass-input">
                            <span>Indra LaTeX Math (--math)</span>
                        </label>
                        <label class="inline-flex items-center gap-1.5 cursor-pointer">
                            <input type="checkbox" id="indra-furniture-chk" class="rounded glass-input">
                            <span>Keep Furniture/Headers (--keep-furniture)</span>
                        </label>
                    </div>
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

            boxes.forEach((b, idx) => {
                const order = typeof b.order === 'number' ? b.order : idx + 1;
                const bType = (b.type || (b.label ? (b.label.toLowerCase().includes('header') ? 'heading' : (b.label.toLowerCase().includes('list') ? 'list' : 'paragraph')) : 'paragraph')).toLowerCase();
                const rawBox = b.bbox_norm || b.bbox || [0, 0, 1, 1];
                const isNorm = rawBox[0] <= 1.05 && rawBox[1] <= 1.05 && rawBox[2] <= 1.05 && rawBox[3] <= 1.05;
                const x0 = isNorm ? rawBox[0] * pWidth : rawBox[0];
                const y0 = isNorm ? rawBox[1] * pHeight : rawBox[1];
                const x1 = isNorm ? rawBox[2] * pWidth : rawBox[2];
                const y1 = isNorm ? rawBox[3] * pHeight : rawBox[3];
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
            const indraMath = document.getElementById('indra-math-chk')?.checked || false;
            const indraFurniture = document.getElementById('indra-furniture-chk')?.checked || false;

            runBtn.disabled = true;
            runBtn.textContent = 'Extracting Page...';
            metaBox.innerHTML = '<p class="text-brand-300 animate-pulse">Running extraction engine...</p>';

            try {
                const res = await fetch('/api/sources/test_pdf_extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        file_path: selectedPath,
                        page_number: page,
                        engine: engine,
                        ocr_engine: ocrEngine,
                        indra_math: indraMath,
                        indra_furniture: indraFurniture,
                    })
                });
                const data = await res.json();

                if (data.status === 'success') {
                    renderSvgLayoutOverlay(data);

                    metaBox.innerHTML = `
                        <div class="space-y-1.5">
                            <p><strong class="text-brand-300">File Name:</strong> ${escapeHTML(data.file_name)}</p>
                            <p><strong class="text-brand-300">File Path:</strong> <code class="text-xs bg-gray-800 px-1 py-0.5 rounded">${escapeHTML(data.file_path)}</code></p>
                            <p><strong class="text-brand-300">Page:</strong> ${data.page_number} / ${data.total_pages}</p>
                            <p><strong class="text-brand-300">Engine Selected:</strong> ${escapeHTML(String(data.engine || '').toUpperCase())} ${data.ocr_engine ? `<span class="text-muted">(OCR: ${escapeHTML(data.ocr_engine)})</span>` : ''} ${data.elapsed_ms ? `<span class="text-emerald-400 font-mono">[${escapeHTML(data.elapsed_ms)} ms]</span>` : ''}</p>
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
                        <label class="text-xs font-bold text-muted mb-1 block">Database Target</label>
                        <div class="segmented-group w-full flex flex-wrap" id="db-target-group" role="radiogroup" aria-label="Database Target Selection">
                            <button type="button" class="segmented-btn btn-sm flex-1 active" data-value="all" role="radio" aria-checked="true">All</button>
                            <button type="button" class="segmented-btn btn-sm flex-1" data-value="lancedb" role="radio" aria-checked="false">LanceDB</button>
                            <button type="button" class="segmented-btn btn-sm flex-1" data-value="pdf" role="radio" aria-checked="false">PDF</button>
                            <button type="button" class="segmented-btn btn-sm flex-1" data-value="obsidian" role="radio" aria-checked="false">Obsidian</button>
                            <button type="button" class="segmented-btn btn-sm flex-1" data-value="zim" role="radio" aria-checked="false">ZIM</button>
                        </div>
                        <select id="db-target-select" class="hidden" aria-hidden="true">
                            <option value="all" selected>Hybrid Search</option>
                            <option value="lancedb">LanceDB Vector &amp; FTS</option>
                            <option value="pdf">PDF Textbooks Only</option>
                            <option value="obsidian">Obsidian Vaults Only</option>
                            <option value="zim">ZIM Wikipedia B-Tree Archive</option>
                        </select>
                    </div>
                    <div>
                        <button type="button" id="run-db-search-btn" class="primary-btn text-xs py-2 w-full cursor-pointer flex items-center justify-center gap-1.5 shadow-md">
                            <span>🔍</span> Search Target
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
        const targetGroup = document.getElementById('db-target-group');
        const searchBtn = document.getElementById('run-db-search-btn');

        if (targetGroup && targetSelect) {
            targetGroup.querySelectorAll('.segmented-btn').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const val = btn.dataset.value;
                    targetGroup.querySelectorAll('.segmented-btn').forEach((b) => {
                        b.classList.toggle('active', b === btn);
                        b.setAttribute('aria-checked', b === btn ? 'true' : 'false');
                    });
                    targetSelect.value = val;
                });
            });
        }
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
    // 6. Pipeline Runner View (1-2-3 Pipeline Inspector & Multi-Model Benchmark)
    // =========================================================
    function renderPipelineRunnerView(container) {
        container.innerHTML = `
            <div class="card glass-card mb-6">
                <div class="flex flex-col gap-4">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label for="pipeline-topic-input" class="text-xs font-bold text-muted mb-1 block">Clinical Topic / Query</label>
                            <input type="text" id="pipeline-topic-input" class="glass-input text-xs w-full py-2" value="Falciparum Malaria" placeholder="Enter clinical topic (e.g. Falciparum Malaria)..." />
                        </div>
                        <div>
                            <div class="flex items-center justify-between mb-1">
                                <label class="text-xs font-bold text-muted block">Available LM Studio Models</label>
                                <button type="button" id="refresh-lm-models-btn" class="text-[10px] text-brand-400 hover:text-brand-300 underline cursor-pointer">Refresh Models</button>
                            </div>
                            <div id="lm-models-checkbox-container" class="flex flex-wrap gap-2 p-2 rounded bg-gray-900/60 border border-gray-700/50 min-h-9 items-center text-xs">
                                <span class="text-gray-500 text-[11px]">Loading LM Studio models...</span>
                            </div>
                        </div>
                    </div>

                    <div class="flex flex-wrap items-center justify-between gap-4 pt-2 border-t border-gray-800">
                        <label class="flex items-center gap-2 cursor-pointer text-xs text-gray-300">
                            <input type="checkbox" id="pipeline-heading-wise-chk" checked class="rounded bg-gray-800 border-gray-700 text-brand-500 focus:ring-brand-500" />
                            <span>Heading-Wise LM Studio Dispatch (H1 > H2 > H3 Breadcrumbs)</span>
                        </label>
                        <div class="flex gap-2">
                            <button type="button" id="run-pipeline-trace-btn" class="secondary-btn text-xs py-2 px-4 cursor-pointer">Run Single-Model Pipeline Trace</button>
                            <button type="button" id="run-pipeline-benchmark-btn" class="primary-btn text-xs py-2 px-4 cursor-pointer font-bold">Benchmark Selected LM Studio Models</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Leaderboard / Model Scorecard Table (Benchmark Mode) -->
            <div id="pipeline-leaderboard-card" class="card glass-card mb-6 hidden">
                <h4 class="text-xs font-bold text-brand-300 mb-3 flex items-center justify-between">
                    <span>🏆 Multi-Model Benchmark Leaderboard</span>
                    <span id="benchmark-winner-badge" class="text-[10px] px-2.5 py-1 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 font-bold"></span>
                </h4>
                <div class="overflow-x-auto">
                    <table class="w-full text-xs text-left text-gray-300">
                        <thead class="text-[10px] uppercase bg-gray-800/80 text-gray-400 border-b border-gray-700">
                            <tr>
                                <th class="py-2 px-3">Rank</th>
                                <th class="py-2 px-3">Model ID</th>
                                <th class="py-2 px-3 text-right">Composite Score</th>
                                <th class="py-2 px-3 text-right">JSON Valid %</th>
                                <th class="py-2 px-3 text-right">Dosage / Staging Ret %</th>
                                <th class="py-2 px-3 text-right">Points</th>
                                <th class="py-2 px-3 text-right">Speed (tok/s)</th>
                                <th class="py-2 px-3 text-right">Time (ms)</th>
                                <th class="py-2 px-3 text-center">Anomalies</th>
                            </tr>
                        </thead>
                        <tbody id="pipeline-leaderboard-tbody" class="divide-y divide-gray-800 font-mono">
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Anomalies Alert Banner -->
            <div id="pipeline-anomalies-banner" class="mb-6 hidden"></div>

            <!-- 5-Stage Interactive Pipeline Waterfall -->
            <div class="space-y-4">
                <!-- Stage 1 & 2: Retrieval & Document Resolution -->
                <div class="card glass-card">
                    <div class="flex items-center justify-between cursor-pointer" onclick="document.getElementById('stage-retrieval-body').classList.toggle('hidden')">
                        <h4 class="text-xs font-bold text-brand-300 flex items-center gap-2">
                            <span>🔍</span> Stage 1 &amp; 2: Multi-Source Retrieval &amp; Document Resolution
                        </h4>
                        <span id="stage-retrieval-badge" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-400">Ready</span>
                    </div>
                    <div id="stage-retrieval-body" class="mt-3 pt-3 border-t border-gray-800 text-xs hidden">
                        <div id="stage-retrieval-content" class="text-gray-400">No retrieval run yet.</div>
                    </div>
                </div>

                <!-- Stage 3: Extracted Heading Tree & CPU Solar Pruning -->
                <div class="card glass-card">
                    <div class="flex items-center justify-between cursor-pointer" onclick="document.getElementById('stage-headings-body').classList.toggle('hidden')">
                        <h4 class="text-xs font-bold text-brand-300 flex items-center gap-2">
                            <span>🌲</span> Stage 3: Hierarchical Heading Tree &amp; CPU Solar Sentence Pruning
                        </h4>
                        <span id="stage-headings-badge" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-400">Ready</span>
                    </div>
                    <div id="stage-headings-body" class="mt-3 pt-3 border-t border-gray-800 text-xs hidden">
                        <div id="stage-headings-content" class="text-gray-400">No heading segmentation run yet.</div>
                    </div>
                </div>

                <!-- Stage 4: Heading-Wise LM Studio Dispatches -->
                <div class="card glass-card">
                    <div class="flex items-center justify-between cursor-pointer" onclick="document.getElementById('stage-dispatches-body').classList.toggle('hidden')">
                        <h4 class="text-xs font-bold text-brand-300 flex items-center gap-2">
                            <span>⚡</span> Stage 4: Heading-Wise LM Studio Prompts, Responses &amp; Dosage Fidelity
                        </h4>
                        <span id="stage-dispatches-badge" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-400">Ready</span>
                    </div>
                    <div id="stage-dispatches-body" class="mt-3 pt-3 border-t border-gray-800 text-xs hidden">
                        <div id="stage-dispatches-content" class="text-gray-400">No LLM dispatches recorded yet.</div>
                    </div>
                </div>

                <!-- Stage 5: Final Rendered Synthesis & Variance Audit -->
                <div class="card glass-card">
                    <div class="flex items-center justify-between cursor-pointer" onclick="document.getElementById('stage-synthesis-body').classList.toggle('hidden')">
                        <h4 class="text-xs font-bold text-brand-300 flex items-center gap-2">
                            <span>📄</span> Stage 5: Unified Clinical Synthesis &amp; Discrepancy Audit
                        </h4>
                        <div class="flex items-center gap-2">
                            <button type="button" id="download-trace-json-btn" class="hidden text-[10px] px-2 py-0.5 rounded bg-brand-500/20 text-brand-300 hover:bg-brand-500/30">Download Trace JSON</button>
                            <span id="stage-synthesis-badge" class="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-400">Ready</span>
                        </div>
                    </div>
                    <div id="stage-synthesis-body" class="mt-3 pt-3 border-t border-gray-800 text-xs hidden">
                        <div id="stage-synthesis-content" class="text-gray-400">No synthesis output generated yet.</div>
                    </div>
                </div>
            </div>
        `;

        const topicInput = document.getElementById('pipeline-topic-input');
        const headingWiseChk = document.getElementById('pipeline-heading-wise-chk');
        const refreshModelsBtn = document.getElementById('refresh-lm-models-btn');
        const modelsCheckboxContainer = document.getElementById('lm-models-checkbox-container');
        const traceBtn = document.getElementById('run-pipeline-trace-btn');
        const benchmarkBtn = document.getElementById('run-pipeline-benchmark-btn');

        const leaderboardCard = document.getElementById('pipeline-leaderboard-card');
        const leaderboardTbody = document.getElementById('pipeline-leaderboard-tbody');
        const winnerBadge = document.getElementById('benchmark-winner-badge');
        const anomaliesBanner = document.getElementById('pipeline-anomalies-banner');
        const downloadTraceBtn = document.getElementById('download-trace-json-btn');

        let activeTraceData = null;
        let lastBenchmarkResults = null;

        async function fetchAvailableModels() {
            modelsCheckboxContainer.innerHTML = '<span class="text-gray-500 text-[11px] animate-pulse">Checking LM Studio...</span>';
            try {
                const res = await fetch('/api/models/lm-studio-available');
                const data = await res.json();
                if (!data.online || !data.models || data.models.length === 0) {
                    modelsCheckboxContainer.innerHTML = `
                        <span class="text-amber-400 text-[11px]">LM Studio offline or 0 models loaded. Using default: <b>${escapeHTML(data.default_model || 'local-model')}</b></span>
                    `;
                    return;
                }
                modelsCheckboxContainer.innerHTML = data.models.map((m, idx) => `
                    <label class="inline-flex items-center gap-1.5 px-2 py-1 rounded bg-gray-800 border border-gray-700 text-[11px] cursor-pointer hover:border-brand-500/50">
                        <input type="checkbox" name="lm-benchmark-model" value="${escapeHTML(m)}" ${idx === 0 ? 'checked' : ''} class="rounded bg-gray-900 border-gray-700 text-brand-500" />
                        <span class="text-gray-200">${escapeHTML(m)}</span>
                    </label>
                `).join('');
            } catch (err) {
                modelsCheckboxContainer.innerHTML = `<span class="text-red-400 text-[11px]">Error loading models: ${escapeHTML(err.message)}</span>`;
            }
        }

        refreshModelsBtn.onclick = fetchAvailableModels;
        fetchAvailableModels();

        function getSelectedModelIds() {
            const checked = modelsCheckboxContainer.querySelectorAll('input[type="checkbox"]:checked');
            return Array.from(checked).map(cb => cb.value);
        }

        function renderAnomalies(anomalies) {
            if (!anomalies || anomalies.length === 0) {
                anomaliesBanner.classList.add('hidden');
                anomaliesBanner.innerHTML = '';
                return;
            }
            anomaliesBanner.classList.remove('hidden');
            anomaliesBanner.innerHTML = `
                <div class="p-3 rounded-xl bg-amber-950/30 border border-amber-600/40 text-amber-200 text-xs">
                    <p class="font-bold flex items-center gap-1.5 mb-1 text-amber-300">
                        <span>⚠️</span> Pipeline Bottleneck &amp; Anomaly Warnings (${anomalies.length})
                    </p>
                    <div class="space-y-1 mt-2">
                        ${anomalies.map(a => `
                            <div class="flex items-start gap-2">
                                <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${a.severity === 'critical' ? 'bg-red-500/20 text-red-300 border border-red-500/40' : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'}">${escapeHTML(a.code)}</span>
                                <span class="text-[11px] text-gray-300">${escapeHTML(a.detail)}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        function populateTelemetryWaterfall(telemetry, synthPayload) {
            activeTraceData = telemetry;
            if (!telemetry) return;

            renderAnomalies(telemetry.anomaly_flags);

            // Stage 1 & 2
            const retBadge = document.getElementById('stage-retrieval-badge');
            const retContent = document.getElementById('stage-retrieval-content');
            const rawHits = telemetry.circle2_raw_hits_summary || {};
            const resolvedDocs = telemetry.circle2_resolved_documents || [];
            retBadge.textContent = `${resolvedDocs.length} Docs Resolved`;
            retBadge.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
            retContent.innerHTML = `
                <div class="grid grid-cols-3 gap-2 mb-3 text-center">
                    <div class="p-2 rounded bg-gray-800/80 border border-gray-700">ZIM Hits: <span class="text-brand-300 font-bold">${rawHits.zim || 0}</span></div>
                    <div class="p-2 rounded bg-gray-800/80 border border-gray-700">PDF Hits: <span class="text-brand-300 font-bold">${rawHits.pdf || 0}</span></div>
                    <div class="p-2 rounded bg-gray-800/80 border border-gray-700">Obsidian Hits: <span class="text-brand-300 font-bold">${rawHits.obsidian || 0}</span></div>
                </div>
                <div class="space-y-1.5">
                    ${resolvedDocs.map(d => `
                        <div class="p-2 rounded bg-gray-900/60 border border-gray-800 flex justify-between items-center text-[11px]">
                            <span class="font-bold text-gray-200">[${escapeHTML(d.source_type?.toUpperCase() || 'REF')}] ${escapeHTML(d.source_label || 'Document')} (p.${d.page || 1})</span>
                            <span class="text-gray-400 font-mono">${d.words || 0} words</span>
                        </div>
                    `).join('')}
                </div>
            `;
            document.getElementById('stage-retrieval-body').classList.remove('hidden');

            // Stage 3
            const hBadge = document.getElementById('stage-headings-badge');
            const hContent = document.getElementById('stage-headings-content');
            const docHeadings = telemetry.extracted_headings_per_doc || [];
            const solarPruned = telemetry.solar_pruned_sentences || [];
            const pass2Pruned = telemetry.pass2_pre_filtered_sentences || [];
            const totalHeadings = docHeadings.reduce((acc, dh) => acc + (dh.headings_count || 0), 0);
            hBadge.textContent = `${totalHeadings} Headings | ${solarPruned.length} Pruned`;
            hBadge.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
            hContent.innerHTML = `
                <div class="space-y-2 mb-4">
                    <p class="font-bold text-gray-300 text-[11px]">Extracted Heading Breadcrumbs per Source:</p>
                    ${docHeadings.map(dh => `
                        <div class="p-2 rounded bg-gray-900/60 border border-gray-800">
                            <div class="flex justify-between font-bold text-gray-300 text-[11px] mb-1">
                                <span>${escapeHTML(dh.source_label)}</span>
                                <span class="text-brand-400 font-mono">${dh.headings_count} headings (${dh.total_words} words)</span>
                            </div>
                            <div class="flex flex-wrap gap-1">
                                ${(dh.headings || []).map(h => `
                                    <span class="px-2 py-0.5 rounded bg-gray-800 text-[10px] text-gray-300 font-mono">${escapeHTML(h)}</span>
                                `).join('')}
                            </div>
                        </div>
                    `).join('')}
                </div>
                ${(solarPruned.length > 0 || pass2Pruned.length > 0) ? `
                    <details class="p-2 rounded bg-gray-900/40 border border-gray-800">
                        <summary class="cursor-pointer text-[11px] font-bold text-amber-300">View CPU-Pruned Sentences (${solarPruned.length} Solar + ${pass2Pruned.length} Pass 2)</summary>
                        <div class="mt-2 space-y-1 text-[10px] font-mono max-h-48 overflow-y-auto">
                            ${solarPruned.map(s => `
                                <div class="p-1 rounded bg-gray-800/60 text-gray-400 flex justify-between gap-2">
                                    <span>${escapeHTML(s.sentence)}</span>
                                    <span class="text-amber-400 whitespace-nowrap">Score: ${s.relevance_score} (${escapeHTML(s.reason)})</span>
                                </div>
                            `).join('')}
                            ${pass2Pruned.map(p => `
                                <div class="p-1 rounded bg-gray-800/60 text-gray-400 flex justify-between gap-2">
                                    <span>${escapeHTML(p.sentence)}</span>
                                    <span class="text-purple-400 whitespace-nowrap">Pass 2 (${escapeHTML(p.reason)})</span>
                                </div>
                            `).join('')}
                        </div>
                    </details>
                ` : '<p class="text-gray-500 text-[11px]">No sentences dropped during CPU filtering.</p>'}
            `;
            document.getElementById('stage-headings-body').classList.remove('hidden');

            // Stage 4
            const dispBadge = document.getElementById('stage-dispatches-badge');
            const dispContent = document.getElementById('stage-dispatches-content');
            const dispatches = telemetry.llm_dispatches || [];
            dispBadge.textContent = `${dispatches.length} LM Studio Calls`;
            dispBadge.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
            dispContent.innerHTML = dispatches.length === 0 ? '<p class="text-gray-500">No dispatches recorded.</p>' : `
                <div class="space-y-3">
                    ${dispatches.map((disp, idx) => `
                        <div class="p-3 rounded-xl bg-gray-900/60 border border-gray-800">
                            <div class="flex flex-wrap items-center justify-between gap-2 mb-2">
                                <div class="flex items-center gap-2">
                                    <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono ${disp.pass_number === 1 ? 'bg-blue-500/20 text-blue-300' : 'bg-purple-500/20 text-purple-300'}">Pass ${disp.pass_number}</span>
                                    <span class="font-bold text-gray-200 text-xs">${escapeHTML(disp.heading_breadcrumb)}</span>
                                </div>
                                <div class="flex items-center gap-3 text-[10px] font-mono text-gray-400">
                                    <span>Speed: <b class="text-emerald-400">${disp.tokens_per_sec} tok/s</b></span>
                                    <span>Latency: <b class="text-brand-300">${disp.latency_ms} ms</b></span>
                                    <span>Dosage Ret: <b class="${disp.retention_score >= 0.8 ? 'text-emerald-400' : 'text-amber-400'}">${Math.round(disp.retention_score * 100)}%</b></span>
                                </div>
                            </div>
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px] font-mono">
                                <details class="bg-gray-800/80 p-2 rounded border border-gray-700">
                                    <summary class="cursor-pointer text-gray-300 font-bold">Prompt Sent (${disp.input_word_count} words)</summary>
                                    <pre class="mt-1 whitespace-pre-wrap text-gray-400 text-[10px] max-h-40 overflow-y-auto">${escapeHTML(disp.prompt_sent)}</pre>
                                </details>
                                <details class="bg-gray-800/80 p-2 rounded border border-gray-700">
                                    <summary class="cursor-pointer text-gray-300 font-bold">Raw LLM Response (${disp.parsed_points_count} points extracted)</summary>
                                    <pre class="mt-1 whitespace-pre-wrap text-emerald-300 text-[10px] max-h-40 overflow-y-auto">${escapeHTML(disp.raw_llm_response)}</pre>
                                </details>
                            </div>
                            ${disp.source_dosages_and_stages && disp.source_dosages_and_stages.length > 0 ? `
                                <div class="mt-2 pt-2 border-t border-gray-800 flex flex-wrap gap-1 items-center text-[10px]">
                                    <span class="text-gray-400">Dosages/Stages:</span>
                                    ${(disp.retained_dosages_and_stages || []).map(d => `<span class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-mono">✓ ${escapeHTML(d)}</span>`).join('')}
                                    ${(disp.missing_dosages_and_stages || []).map(d => `<span class="px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 font-mono">✗ ${escapeHTML(d)}</span>`).join('')}
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
            `;
            document.getElementById('stage-dispatches-body').classList.remove('hidden');

            // Stage 5
            const synBadge = document.getElementById('stage-synthesis-badge');
            const synContent = document.getElementById('stage-synthesis-content');
            synBadge.textContent = 'Compiled';
            synBadge.className = 'text-[10px] px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold';
            const mdText = synthPayload?.enhanced_explanation || synthPayload?.unified_article || 'Synthesis complete.';
            synContent.innerHTML = `
                <div class="prose prose-invert max-w-none text-xs p-4 rounded-xl bg-gray-900/60 border border-gray-800 mb-3">
                    ${window.marked ? window.marked.parse(mdText) : escapeHTML(mdText)}
                </div>
                ${synthPayload?.variance_audit ? `
                    <div class="p-3 rounded-xl bg-amber-950/20 border border-amber-700/40 text-amber-200 text-xs mb-3">
                        <p class="font-bold mb-1">⚖️ Clinical Variance &amp; Discrepancy Audit:</p>
                        <div class="prose prose-invert text-xs">${window.marked ? window.marked.parse(synthPayload.variance_audit) : escapeHTML(synthPayload.variance_audit)}</div>
                    </div>
                ` : ''}
            `;
            document.getElementById('stage-synthesis-body').classList.remove('hidden');

            downloadTraceBtn.classList.remove('hidden');
            downloadTraceBtn.onclick = () => {
                const blob = new Blob([JSON.stringify(telemetry, null, 2)], { type: 'application/json' });
                const u = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = u;
                a.download = `pipeline_trace_${telemetry.topic || 'topic'}_${Date.now()}.json`;
                a.click();
                URL.revokeObjectURL(u);
            };
        }

        traceBtn.onclick = async () => {
            const topic = topicInput.value.trim();
            if (!topic) return;

            traceBtn.disabled = true;
            benchmarkBtn.disabled = true;
            leaderboardCard.classList.add('hidden');

            const selectedModels = getSelectedModelIds();
            const modelToUse = selectedModels[0] || '';

            try {
                const res = await fetch('/api/analysis/pipeline-benchmark', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        topic: topic,
                        model_ids: modelToUse ? [modelToUse] : [],
                        heading_wise: headingWiseChk.checked,
                        limit_per_source: 3
                    })
                });
                const data = await res.json();
                if (data.model_results && data.model_results.length > 0) {
                    const single = data.model_results[0];
                    populateTelemetryWaterfall(single.synthesis?.pipeline_telemetry, single.synthesis);
                }
            } catch (err) {
                alert(`Pipeline trace error: ${err.message}`);
            } finally {
                traceBtn.disabled = false;
                benchmarkBtn.disabled = false;
            }
        };

        benchmarkBtn.onclick = async () => {
            const topic = topicInput.value.trim();
            if (!topic) return;

            const selectedModels = getSelectedModelIds();
            if (selectedModels.length === 0) {
                alert('Please select at least one LM Studio model to benchmark.');
                return;
            }

            benchmarkBtn.disabled = true;
            traceBtn.disabled = true;
            benchmarkBtn.textContent = 'Benchmarking Models Sequentially...';

            try {
                const res = await fetch('/api/analysis/pipeline-benchmark', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        topic: topic,
                        model_ids: selectedModels,
                        heading_wise: headingWiseChk.checked,
                        limit_per_source: 3
                    })
                });
                const data = await res.json();
                lastBenchmarkResults = data;

                leaderboardCard.classList.remove('hidden');
                winnerBadge.textContent = `Winner: ${data.winner_model_id || 'None'}`;

                leaderboardTbody.innerHTML = (data.model_results || []).map((mr, idx) => {
                    const sc = mr.scorecard || {};
                    return `
                        <tr class="hover:bg-gray-800/50 cursor-pointer ${idx === 0 ? 'bg-amber-500/10' : ''}" data-model-idx="${idx}">
                            <td class="py-2.5 px-3 font-bold ${idx === 0 ? 'text-amber-400' : 'text-gray-400'}">#${idx + 1}</td>
                            <td class="py-2.5 px-3 font-bold text-gray-200">${escapeHTML(sc.model_id || mr.model_id)}</td>
                            <td class="py-2.5 px-3 text-right font-bold ${idx === 0 ? 'text-amber-400' : 'text-brand-300'}">${sc.composite_score}</td>
                            <td class="py-2.5 px-3 text-right text-gray-300">${sc.json_success_rate}%</td>
                            <td class="py-2.5 px-3 text-right text-gray-300">${sc.dosage_staging_retention_rate}%</td>
                            <td class="py-2.5 px-3 text-right text-gray-300">${sc.clinical_points_extracted}</td>
                            <td class="py-2.5 px-3 text-right text-emerald-400">${sc.avg_tokens_per_sec}</td>
                            <td class="py-2.5 px-3 text-right text-gray-400">${sc.total_latency_ms}</td>
                            <td class="py-2.5 px-3 text-center">
                                <span class="px-1.5 py-0.5 rounded text-[10px] ${sc.anomaly_count > 0 ? 'bg-amber-500/20 text-amber-300' : 'bg-emerald-500/20 text-emerald-300'}">${sc.anomaly_count}</span>
                            </td>
                        </tr>
                    `;
                }).join('');

                leaderboardTbody.querySelectorAll('tr[data-model-idx]').forEach(tr => {
                    tr.onclick = () => {
                        const idx = parseInt(tr.dataset.modelIdx, 10);
                        const mr = data.model_results[idx];
                        if (mr && mr.synthesis) {
                            populateTelemetryWaterfall(mr.synthesis.pipeline_telemetry, mr.synthesis);
                        }
                    };
                });

                if (data.model_results && data.model_results.length > 0) {
                    const topModel = data.model_results[0];
                    populateTelemetryWaterfall(topModel.synthesis?.pipeline_telemetry, topModel.synthesis);
                }
            } catch (err) {
                alert(`Benchmark error: ${err.message}`);
            } finally {
                benchmarkBtn.disabled = false;
                traceBtn.disabled = false;
                benchmarkBtn.textContent = 'Benchmark Selected LM Studio Models';
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