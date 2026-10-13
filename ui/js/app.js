// ui/js/app.js

document.addEventListener('DOMContentLoaded', () => {
    // --- Safe Escape Helper ---
    const escapeHTML = (str) => {
        if (typeof str !== 'string') return '';
        return str.replace(/[&<>'"]/g, tag => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
        }[tag] || tag));
    };

    // --- Navigation Logic (Tab Switching) ---
    const navButtons = document.querySelectorAll('.nav-btn');
    const viewSections = document.querySelectorAll('.view-section');

    const switchTab = (targetId) => {
        navButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.target === targetId));
        viewSections.forEach(sec => sec.classList.toggle('active', sec.id === targetId));
    };

    navButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const targetId = e.currentTarget.dataset.target;
            switchTab(targetId);

            // If switching to settings, refresh diagnostics automatically
            if (targetId === 'settings-view' && typeof window.refreshDiagnostics === 'function') {
                window.refreshDiagnostics();
            }
        });
    });

    // --- Theme Toggle Logic ---
    const themeToggleBtn = document.getElementById('theme-toggle');
    const body = document.body;
    const savedTheme = localStorage.getItem('theme');

    if (savedTheme) {
        body.className = savedTheme;
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        body.className = 'dark-theme';
    }

    themeToggleBtn?.addEventListener('click', () => {
        const isLight = body.classList.contains('light-theme');
        const newTheme = isLight ? 'dark-theme' : 'light-theme';
        body.classList.replace(isLight ? 'light-theme' : 'dark-theme', newTheme);
        localStorage.setItem('theme', newTheme);
    });

    // --- Help Modal Logic ---
    const helpModal = document.getElementById('help-modal');
    const openHelpBtn = document.getElementById('open-help-btn');
    const closeHelpBtn = document.getElementById('close-help-btn');

    if (openHelpBtn && helpModal) {
        openHelpBtn.addEventListener('click', () => helpModal.style.display = 'flex');
    }
    if (closeHelpBtn && helpModal) {
        closeHelpBtn.addEventListener('click', () => helpModal.style.display = 'none');
    }
    // Close modal on click outside
    if (helpModal) {
        helpModal.addEventListener('click', (e) => {
            if (e.target === helpModal) helpModal.style.display = 'none';
        });
    }

    // --- Synthesis Inspector Fetch Logic ---
    async function fetchAndDisplaySynthesis(topic) {
        const inspectorContent = document.getElementById('inspector-content');
        if (!inspectorContent) return;

        inspectorContent.innerHTML = `<div class="text-center text-gray-400 mt-10">
            <svg class="animate-spin h-8 w-8 mx-auto text-brand-500 mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            Synthesizing local notes for <strong>${escapeHTML(topic)}</strong>...
        </div>`;

        try {
            const res = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
            if (!res.ok) throw new Error('Synthesis fetch failed or topic is not yet processed.');
            const data = await res.json();

            let html = `<div class="space-y-4 text-xs">`;

            if (data.enhanced_explanation) {
                html += `
                    <div class="bg-gray-800/80 border border-gray-700 rounded-xl p-3">
                        <h4 class="font-semibold text-brand-300 mb-2">📖 High-Yield Synopsis</h4>
                        <div class="text-gray-300 leading-relaxed whitespace-pre-wrap">${escapeHTML(data.enhanced_explanation)}</div>
                    </div>
                `;
            }

            if (data.distractor_analysis && data.distractor_analysis !== 'N/A') {
                html += `
                    <div class="bg-gray-800/80 border border-gray-700 rounded-xl p-3">
                        <h4 class="font-semibold text-purple-300 mb-2">✨ Distractor Rationale</h4>
                        <div class="text-gray-300 leading-relaxed whitespace-pre-wrap">${escapeHTML(data.distractor_analysis)}</div>
                    </div>
                `;
            }

            // Extract Discrepancies if they exist in the unified article
            const variancesMatch = data.unified_article ? data.unified_article.match(/### Discrepancies(?:\s*&\s*Variances)?\n([\s\S]*)/i) : null;
            if (variancesMatch) {
                html += `
                    <div class="bg-warning-500/10 border border-warning-500/30 rounded-xl p-3">
                        <h4 class="font-semibold text-warning-400 mb-1 flex items-center gap-1.5">⚠️ Clinical Variance Detected</h4>
                        <p class="text-gray-300 text-[11px] leading-relaxed whitespace-pre-wrap">${escapeHTML(variancesMatch[1].trim())}</p>
                    </div>
                `;
            }

            if (data.references && Object.keys(data.references).length > 0) {
                html += `<div class="bg-gray-800/80 border border-gray-700 rounded-xl p-3">
                    <h4 class="font-semibold text-gray-300 mb-2">📄 Verified Source References</h4>
                    <div class="space-y-2">`;

                if (data.references.pdf) {
                    data.references.pdf.forEach(pdf => {
                        html += `<button class="citation-pdf w-full text-left p-2 bg-gray-900/60 hover:bg-gray-900 rounded-lg border border-gray-700/50 flex items-center justify-between transition-colors" data-filepath="${escapeHTML(pdf.file_path)}" data-page="${pdf.page || 1}">
                            <div class="overflow-hidden">
                                <p class="font-medium text-brand-400 truncate">${escapeHTML(pdf.title)}</p>
                                <p class="text-[10px] text-gray-500">SumatraPDF • Page ${pdf.page || 1}</p>
                            </div>
                        </button>`;
                    });
                }
                if (data.references.obsidian) {
                    data.references.obsidian.forEach(obs => {
                        html += `<button class="citation-obsidian w-full text-left p-2 bg-gray-900/60 hover:bg-gray-900 rounded-lg border border-gray-700/50 flex items-center justify-between transition-colors" data-file="${escapeHTML(obs.title)}">
                            <div class="overflow-hidden">
                                <p class="font-medium text-purple-400 truncate">${escapeHTML(obs.title)}</p>
                                <p class="text-[10px] text-gray-500">Obsidian Vault Note</p>
                            </div>
                        </button>`;
                    });
                }
                if (data.references.zim) {
                     data.references.zim.forEach(zim => {
                        html += `<button class="citation-zim w-full text-left p-2 bg-gray-900/60 hover:bg-gray-900 rounded-lg border border-gray-700/50 flex items-center justify-between transition-colors" data-article-id="${escapeHTML(zim.file_path || zim.title)}">
                            <div class="overflow-hidden">
                                <p class="font-medium text-blue-400 truncate">${escapeHTML(zim.title)}</p>
                                <p class="text-[10px] text-gray-500">ZIM Article</p>
                            </div>
                        </button>`;
                    });
                }

                html += `</div></div>`;
            }

            html += `</div>`;
            inspectorContent.innerHTML = html;

        } catch (err) {
            console.error("Inspector Panel Error:", err);
            inspectorContent.innerHTML = `<div class="text-danger-500 p-4 bg-danger-500/10 border border-danger-500/30 rounded-lg">
                <p class="font-bold">Error loading synthesis</p>
                <p class="text-xs text-gray-400 mt-1">${escapeHTML(err.message)}</p>
                <p class="text-xs text-gray-500 mt-2">Check the Processing Queue to see if LM Studio has finished generating notes for this topic.</p>
            </div>`;
        }
    }

    // --- MCQ Topic Extraction Logic ---
    const extractBtn = document.getElementById('extract-btn');
    const topicsContainer = document.getElementById('topics-container');

    const renderTopics = (topics) => {
        if (!topicsContainer) return;
        topicsContainer.innerHTML = '';
        if (!topics || topics.length === 0) {
            topicsContainer.innerHTML = '<div class="text-muted">No entities found.</div>';
            return;
        }

        const fragment = document.createDocumentFragment();
        topics.forEach(topic => {
            const chip = document.createElement('div');
            chip.className = 'topic-chip';
            chip.textContent = topic; // Safe text injection

            // Wire up the click to fetch synthesis instead of just echoing text
            chip.addEventListener('click', () => {
                fetchAndDisplaySynthesis(topic);
            });
            fragment.appendChild(chip);
        });
        topicsContainer.appendChild(fragment);
    };

    extractBtn?.addEventListener('click', async () => {
        const mcqTextEl = document.getElementById('mcq-text');
        const mcqText = mcqTextEl?.value.trim();
        const useZimRefinement = document.getElementById('use-zim-refinement')?.checked || false;
        const useTantivyRefinement = document.getElementById('use-tantivy-refinement')?.checked || false;

        if (!mcqText) return alert('Please enter some MCQ text.');

        extractBtn.disabled = true;
        extractBtn.textContent = 'Extracting...';
        if (topicsContainer) topicsContainer.innerHTML = '';

        try {
            const response = await fetch('/api/mcq/topics/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    mcq_text: mcqText,
                    use_zim_refinement: useZimRefinement,
                    use_tantivy_refinement: useTantivyRefinement
                })
            });

            if (!response.ok) throw new Error(`Server error: ${response.statusText}`);

            const data = await response.json();
            renderTopics(data.topics || []);
        } catch (error) {
            console.error('Error extracting topics:', error);
            alert('Failed to extract topics. Please check the console for details.');
        } finally {
            extractBtn.disabled = false;
            extractBtn.textContent = 'Extract Entities';
        }
    });

    // --- Application Bridging Logic (Global Event Delegation) ---
    document.addEventListener('click', async (e) => {
        const target = e.target;

        // Handle PDF Citations
        const pdfBtn = target.closest('.citation-pdf');
        if (pdfBtn) {
            e.preventDefault();
            const filePath = pdfBtn.dataset.filepath;
            const page = pdfBtn.dataset.page || 1;
            try {
                const res = await fetch('/api/sources/open_pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: filePath, page: parseInt(page) })
                });
                const data = await res.json();
                if (data.status !== 'success') console.error('Error launching SumatraPDF:', data.message);
            } catch (err) {
                console.error('Failed to launch PDF:', err);
            }
            return;
        }

        // Handle Obsidian Links
        const obsBtn = target.closest('.citation-obsidian');
        if (obsBtn) {
            e.preventDefault();
            const vault = obsBtn.dataset.vault || 'Primary';
            const file = obsBtn.dataset.file;
            window.location.href = `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
            return;
        }

        // Handle ZIM Articles
        const zimBtn = target.closest('.citation-zim');
        if (zimBtn) {
            e.preventDefault();
            const articleId = zimBtn.dataset.articleId;
            try {
                const res = await fetch(`/api/notes/zim-article?article_id=${encodeURIComponent(articleId)}`);
                const data = await res.json();
                if (data.status === 'success') {
                    const viewerContainer = document.getElementById('zim-viewer-container');
                    const contentArea = document.getElementById('zim-content-area');
                    if (viewerContainer && contentArea) {
                        // STRICT XSS MITIGATION: Sanitize offline ZIM HTML before injecting
                        const safeHTML = window.DOMPurify ? window.DOMPurify.sanitize(data.html) : escapeHTML(data.html);
                        contentArea.innerHTML = safeHTML;
                        viewerContainer.style.display = 'flex';
                    }
                } else {
                    console.error('Error loading ZIM article:', data.message);
                }
            } catch (err) {
                console.error('Failed to load ZIM article:', err);
            }
            return;
        }

        // Close ZIM Viewer
        const closeZimBtn = target.closest('#close-zim-viewer-btn');
        if (closeZimBtn) {
            const viewerContainer = document.getElementById('zim-viewer-container');
            const contentArea = document.getElementById('zim-content-area');
            if (viewerContainer) viewerContainer.style.display = 'none';
            if (contentArea) contentArea.innerHTML = ''; // Clear memory to free RAM
        }
    });

    // --- Settings / Diagnostics Data Loaders ---
    const loadModels = (endpoint, selectId, apiRoute) => {
        const selectEl = document.getElementById(selectId);
        if (!selectEl) return;

        fetch(endpoint)
            .then(res => res.json())
            .then(data => {
                selectEl.innerHTML = '';
                const fragment = document.createDocumentFragment();
                data.models.forEach(model => {
                    const opt = document.createElement('option');
                    opt.value = model.id;
                    opt.textContent = model.name;
                    opt.selected = (model.id === data.active);
                    fragment.appendChild(opt);
                });
                selectEl.appendChild(fragment);
            })
            .catch(() => {
                selectEl.innerHTML = '<option value="">Error loading models</option>';
            });

        selectEl.addEventListener('change', (e) => {
            fetch(apiRoute, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_name: e.target.value })
            });
        });
    };

    // Load dynamic models for settings pane
    loadModels('/api/models/cpu_embedding_models', 'cpu-embed-model-select', '/api/models/set_embedding_model');
    loadModels('/api/models/flashrank_models', 'flashrank-model-select', '/api/models/set_flashrank_model');
});
