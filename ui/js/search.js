// ui/js/search.js

document.addEventListener('DOMContentLoaded', () => {
    const globalSearchInput = document.getElementById('global-search');
    const missingDbModal = document.getElementById('missing-db-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const goToSettingsBtn = document.getElementById('go-to-settings-btn');

    // =========================================================
    // 1. GLOBAL HYBRID SEARCH -> 3RD-COLUMN RIGHT INSPECTOR
    // =========================================================
    function buildSearchCitationCard(item, rankIdx) {
        const source = (item.source || 'lancedb').toLowerCase();
        const title = item.title || item.main_topic || 'Untitled Document';
        const heading = item.heading || item.heading_path || item.subheading || '';
        const page = item.page_number || item.page || 1;
        const filePath = item.file_path || item.source_article || '';
        const snippet = item.snippet || item.child_content || item.parent_content || '';
        const docLen = item.doc_length || item.article_length || snippet.length || 0;
        const lenLabel = docLen > 1000 ? `${(docLen / 1000).toFixed(1)}k chars` : `${docLen} chars`;

        let actionClass = 'citation-obsidian';
        let badgeLabel = '🟣 Obsidian';
        let dataAttrs = `data-filepath="${window.escapeHTML(filePath)}" data-file="${window.escapeHTML(title)}"`;

        if (source === 'pdf' || filePath.toLowerCase().endsWith('.pdf')) {
            actionClass = 'citation-pdf';
            badgeLabel = `📕 PDF • Pg ${page}`;
            dataAttrs = `data-filepath="${window.escapeHTML(filePath)}" data-page="${page}"`;
        } else if (source === 'zim' || String(item.doc_id || '').startsWith('zim_')) {
            actionClass = 'citation-zim';
            badgeLabel = '🌐 ZIM Article';
            const articleId = item.article_id || title;
            dataAttrs = `data-article-id="${window.escapeHTML(articleId)}"`;
        }

        const cleanSnippet = snippet
            ? window.escapeHTML(snippet.substring(0, 260)) + (snippet.length > 260 ? '...' : '')
            : 'Click to open full reference document.';

        return `
            <div class="glass-card p-4 rounded-xl border ${rankIdx === 0 ? 'border-brand-500/50 shadow-md' : 'border-gray-700/50'} transition-all">
                <div class="flex items-center justify-between gap-2 mb-1.5">
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded glass-panel text-brand-300 uppercase">
                        ${badgeLabel}
                    </span>
                    <span class="text-[10px] font-mono text-muted" title="Comprehensive article length">
                        ${lenLabel}
                    </span>
                </div>

                <h5 class="font-bold text-sm mb-1 truncate" title="${window.escapeHTML(title)}">
                    ${window.escapeHTML(title)}
                </h5>

                ${heading && heading !== title ? `
                    <p class="text-[11px] text-brand-400 font-medium mb-2 truncate" title="${window.escapeHTML(heading)}">
                        📑 ${window.escapeHTML(heading)}
                    </p>
                ` : ''}

                <p class="text-xs text-muted leading-relaxed mb-3">
                    ${cleanSnippet}
                </p>

                <a href="#" class="${actionClass} w-full py-2 px-3 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 no-underline"
                   ${dataAttrs}>
                    Open Reference Document ↗
                </a>
            </div>
        `;
    }

    async function performGlobalSearch(query) {
        if (!query) return;

        try {
            // Check if Tantivy index or ZIM sources exist
            const diagRes = await fetch('/api/models/diagnostics/tantivy').catch(() =>
                fetch('/api/search/diagnostics/tantivy')
            );
            if (diagRes && diagRes.ok) {
                const diagData = await diagRes.json();
                if (!diagData.exists && missingDbModal) {
                    missingDbModal.style.display = 'flex';
                    return;
                }
            }
        } catch (_) {}

        if (window.openInspector) {
            window.openInspector(
                `Search: ${query}`,
                `<div class="text-center py-12 text-muted text-xs animate-pulse">
                    Searching hybrid LanceDB vectors, Tantivy BM25 &amp; ZIM encyclopedia for <strong>${window.escapeHTML(query)}</strong>...
                </div>`
            );
        }

        try {
            const searchResp = await fetch('/api/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            });
            const searchData = await searchResp.json();

            // Flatten candidates and grouped all_results while avoiding duplicates
            const seenIds = new Set();
            const mergedHits = [];

            const pushHit = (item, fallbackSource) => {
                if (!item) return;
                const key = `${item.doc_id || ''}_${item.file_path || ''}_${item.title || ''}_${item.page_number || item.page || 1}`;
                if (seenIds.has(key)) return;
                seenIds.add(key);
                mergedHits.push({ ...item, source: item.source || fallbackSource || 'lancedb' });
            };

            (searchData.candidates || []).forEach(c => pushHit(c, c.source));
            if (searchData.all_results && typeof searchData.all_results === 'object') {
                Object.entries(searchData.all_results).forEach(([srcType, list]) => {
                    if (Array.isArray(list)) {
                        list.forEach(item => pushHit(item, srcType));
                    }
                });
            }

            let html = `
                <div class="space-y-4 text-xs pb-8">
                    <div class="glass-panel p-3.5 rounded-xl border border-brand-500/30 flex items-center justify-between gap-2">
                        <div class="min-w-0 flex-1">
                            <span class="text-[10px] text-muted uppercase block">Query Topic</span>
                            <p class="font-bold text-sm text-brand-300 truncate">${window.escapeHTML(query)}</p>
                        </div>
                        <button type="button" id="search-create-quiz-btn" class="primary-btn px-3 py-2 text-xs font-bold rounded-lg shrink-0">
                            🎯 Create Quiz Set
                        </button>
                    </div>
            `;

            if (mergedHits.length === 0) {
                html += `
                    <div class="text-center py-10 glass-panel rounded-xl p-6 text-muted">
                        <p class="font-semibold text-sm mb-1">No Reference Matches Found</p>
                        <p class="text-xs">No indexed PDF, Obsidian, or ZIM articles matched "${window.escapeHTML(query)}".</p>
                    </div>
                `;
            } else {
                html += `
                    <div class="flex items-center justify-between px-1">
                        <span class="text-[11px] font-bold uppercase tracking-wider text-muted">
                            Top Depth-Ranked References (${mergedHits.length})
                        </span>
                    </div>
                    <div class="space-y-3">
                        ${mergedHits.map((hit, idx) => buildSearchCitationCard(hit, idx)).join('')}
                    </div>
                `;
            }

            html += `</div>`;
            if (window.openInspector) {
                window.openInspector(`Search: ${query}`, html);
                document.getElementById('search-create-quiz-btn')?.addEventListener('click', () => {
                    if (typeof window.startCustomTopicQuiz === 'function') {
                        window.startCustomTopicQuiz(query);
                    }
                });
            }
        } catch (err) {
            console.error("Hybrid search error:", err);
            if (window.openInspector) {
                window.openInspector(
                    `Search Error`,
                    `<div class="text-center py-8 text-danger-400 text-xs">Failed to execute search: ${window.escapeHTML(err.message)}</div>`
                );
            }
        }
    }

    if (globalSearchInput) {
        globalSearchInput.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const query = globalSearchInput.value.trim();
                if (query) await performGlobalSearch(query);
            }
        });
    }

    if (closeModalBtn && missingDbModal) {
        closeModalBtn.addEventListener('click', () => {
            missingDbModal.style.display = 'none';
        });
    }

    if (goToSettingsBtn && missingDbModal) {
        goToSettingsBtn.addEventListener('click', () => {
            missingDbModal.style.display = 'none';
            const navSettingsBtn = document.querySelector('.nav-btn[data-target="settings-view"]');
            if (navSettingsBtn) navSettingsBtn.click();
        });
    }

    // =========================================================
    // 2. SETTINGS "BUILD DATABASE" BUTTON (CSV + HYBRID INDEX)
    // =========================================================
    const buildDbBtn = document.getElementById('build-db-btn');
    const buildProgressContainer = document.getElementById('build-progress-container');
    const buildProgress = document.getElementById('build-progress');
    const buildStatus = document.getElementById('build-status');

    if (buildDbBtn) {
        buildDbBtn.addEventListener('click', async () => {
            buildDbBtn.disabled = true;
            if (buildProgressContainer) buildProgressContainer.style.display = 'block';
            if (buildProgress) {
                buildProgress.style.width = '8%';
                buildProgress.classList.add('progress-indeterminate');
            }
            if (buildStatus) {
                buildStatus.style.display = 'block';
                buildStatus.textContent = 'Loading CSV question banks & starting hybrid indexer...';
            }

            try {
                // 1. Trigger CSV Question Bank reload (data/csv & data/mcqs)
                await fetch('/api/mcq/build_db', { method: 'POST' }).catch(() => {});

                // 2. Connect to WebSocket for live Tantivy/LanceDB indexing progress
                const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
                const wsUrl = `${protocol}${window.location.host}/ws/indexing`;
                let indexingWs = null;

                try {
                    indexingWs = new WebSocket(wsUrl);
                    indexingWs.onmessage = (event) => {
                        try {
                            const msg = JSON.parse(event.data);
                            if (msg.event === 'INDEXING_PROGRESS') {
                                if (buildProgress) {
                                    buildProgress.classList.remove('progress-indeterminate');
                                    buildProgress.style.width = `${msg.progress}%`;
                                }
                                if (buildStatus) {
                                    buildStatus.textContent = `${msg.message} (${msg.progress}%)`;
                                }
                                if (msg.progress >= 100) {
                                    buildDbBtn.disabled = false;
                                    indexingWs.close();
                                    refreshDiagnostics();
                                }
                            }
                        } catch (_) {}
                    };
                } catch (_) {}

                // 3. Start Vault Indexing
                const response = await fetch('/api/sources/index', { method: 'POST' });
                const data = await response.json();

                if (data.status !== 'started') {
                    if (buildStatus) buildStatus.textContent = 'CSV Question Bank updated.';
                    if (buildProgress) {
                        buildProgress.classList.remove('progress-indeterminate');
                        buildProgress.style.width = '100%';
                    }
                    buildDbBtn.disabled = false;
                    refreshDiagnostics();
                }
            } catch (err) {
                console.error("Error building database:", err);
                if (buildStatus) buildStatus.textContent = 'Error starting build process.';
                if (buildProgress) buildProgress.classList.remove('progress-indeterminate');
                buildDbBtn.disabled = false;
            }
        });
    }

    // =========================================================
    // 3. SYSTEM DIAGNOSTICS (LM STUDIO, DB ROWS, SUMATRA, VENDOR CACHE)
    // =========================================================
    const refreshDiagBtn = document.getElementById('btn-refresh-diagnostics') || document.getElementById('refresh-diag-btn');

    async function refreshDiagnostics() {
        const diagLmStudio = document.getElementById('diag-lm-studio-status') || document.getElementById('diag-lm-studio');
        const diagDbRows = document.getElementById('diag-db-status') || document.getElementById('diag-db-rows');
        const diagSumatra = document.getElementById('diag-sumatra-status') || document.getElementById('diag-sumatra');
        const diagVendor = document.getElementById('diag-vendor-status') || document.getElementById('diag-vendor-assets');

        [diagLmStudio, diagDbRows, diagSumatra, diagVendor].forEach(el => {
            if (el) {
                el.textContent = 'Checking...';
                el.className = 'badge bg-warning';
            }
        });

        try {
            const [lmResp, dbResp, sumatraResp, vendorResp] = await Promise.all([
                fetch('/api/models/diagnostics/lm_studio').catch(() => fetch('/api/search/diagnostics/lm_studio')).catch(() => ({ ok: false })),
                fetch('/api/models/diagnostics/db').catch(() => fetch('/api/search/diagnostics/db')).catch(() => ({ ok: false })),
                fetch('/api/models/diagnostics/sumatrapdf').catch(() => fetch('/api/search/diagnostics/sumatrapdf')).catch(() => ({ ok: false })),
                fetch('/api/state/vendor_status').catch(() => ({ ok: false }))
            ]);

            if (lmResp && lmResp.ok && diagLmStudio) {
                const lmData = await lmResp.json();
                const isOk = lmData.status === 'ok' || lmData.status === 'success';
                diagLmStudio.textContent = isOk ? 'Connected (Ready)' : 'Offline / Disconnected';
                diagLmStudio.className = isOk ? 'badge bg-ok' : 'badge bg-warning';
            }

            if (dbResp && dbResp.ok && diagDbRows) {
                const dbData = await dbResp.json();
                if (dbData.status === 'ok') {
                    const sqliteCnt = dbData.sqlite_row_count ?? dbData.row_count ?? 0;
                    const lanceCnt = dbData.lancedb_row_count;
                    diagDbRows.textContent = lanceCnt !== undefined
                        ? `SQLite: ${sqliteCnt} | LanceDB: ${lanceCnt}`
                        : `${sqliteCnt} rows`;
                    diagDbRows.className = 'badge bg-ok';
                } else {
                    diagDbRows.textContent = 'Error';
                    diagDbRows.className = 'badge bg-warning';
                }
            }

            if (sumatraResp && sumatraResp.ok && diagSumatra) {
                const sumatraData = await sumatraResp.json();
                diagSumatra.textContent = sumatraData.available ? 'Available' : 'Not Found in PATH';
                diagSumatra.className = sumatraData.available ? 'badge bg-ok' : 'badge bg-warning';
            }

            if (vendorResp && vendorResp.ok && diagVendor) {
                const vData = await vendorResp.json();
                if (vData.offline_ready) {
                    diagVendor.textContent = '✅ All Cached Offline';
                    diagVendor.className = 'badge bg-ok';
                } else {
                    const missing = Object.entries(vData.assets || {})
                        .filter(([_, ok]) => !ok)
                        .map(([k]) => k);
                    diagVendor.textContent = `Missing (${missing.length}): Connect once`;
                    diagVendor.className = 'badge bg-warning';
                }
            }
        } catch (err) {
            console.error("Error fetching diagnostics:", err);
        }
    }

    window.refreshDiagnostics = refreshDiagnostics;

    if (refreshDiagBtn) {
        refreshDiagBtn.addEventListener('click', refreshDiagnostics);
    }
});
