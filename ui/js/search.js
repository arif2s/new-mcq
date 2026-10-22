// ui/js/search.js

document.addEventListener('DOMContentLoaded', () => {
    const globalSearchInput = document.getElementById('global-search');
    const missingDbModal = document.getElementById('missing-db-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const goToSettingsBtn = document.getElementById('go-to-settings-btn');

    const escapeHTML = window.escapeHTML || ((str) =>
        String(str ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;')
    );

    // Memory state for central search page
    let currentSearchQuery = '';
    let currentSearchAliases = [];
    let rawSearchHits = []; // Unique single-file results
    let activeResultLimit = 10; // Default limit: 10
    let activeSourceFilter = 'all'; // Default source: 'all'

    // =========================================================
    // 1. CITATION CARD BUILDER FOR CENTRAL SEARCH PAGE
    // =========================================================
    function buildSearchCitationCard(item, rankIdx) {
        const rawSource = String(item.source || 'obsidian').toLowerCase();
        const title = item.title || item.main_topic || 'Untitled Document';
        const heading = item.heading || item.heading_path || item.subheading || '';
        const page = item.page_number || item.page || 1;
        const filePath = item.file_path || item.source_article || '';
        const snippet = item.snippet || item.child_content || item.parent_content || item.text || '';
        const docLen = item.doc_length || item.article_length || snippet.length || 0;
        const lenLabel = docLen > 1000 ? `${(docLen / 1000).toFixed(1)}k chars` : `${docLen} chars`;
        const scoreVal = item.rerank_score ?? item.score;

        let sourceType = 'obsidian';
        let actionClass = 'citation-link citation-obsidian';
        let badgeLabel = '🟣 Obsidian Note';
        let dataAttrs = `data-source-type="obsidian" data-file-path="${escapeHTML(filePath || title)}" data-filepath="${escapeHTML(filePath || title)}" data-file="${escapeHTML(title)}"`;

        if (rawSource === 'pdf' || filePath.toLowerCase().endsWith('.pdf')) {
            sourceType = 'pdf';
            actionClass = 'citation-link citation-pdf';
            badgeLabel = `📕 PDF Textbook • Pg ${page}`;
            dataAttrs = `data-source-type="pdf" data-file-path="${escapeHTML(filePath)}" data-filepath="${escapeHTML(filePath)}" data-page="${page}"`;
        } else if (rawSource === 'zim' || filePath.toLowerCase().endsWith('.zim') || String(item.doc_id || '').startsWith('zim')) {
            sourceType = 'zim';
            actionClass = 'citation-link citation-zim';
            badgeLabel = '🌐 ZIM Encyclopedia Article';
            const articleId = item.article_id || title;
            dataAttrs = `data-source-type="zim" data-article-id="${escapeHTML(articleId)}" data-file-path="${escapeHTML(filePath)}" data-filepath="${escapeHTML(filePath)}"`;
        }

        const cleanSnippet = snippet
            ? escapeHTML(snippet.substring(0, 320)) + (snippet.length > 320 ? '...' : '')
            : 'Click to open full reference document.';

        return `
            <div class="glass-card p-5 rounded-2xl border ${rankIdx === 0 ? 'border-brand-500/60 shadow-lg' : 'border-gray-700/50'} transition-all hover:border-brand-400">
                <div class="flex items-center justify-between gap-2 mb-2 flex-wrap">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="text-xs font-bold px-2.5 py-0.5 rounded-md glass-panel text-brand-300 uppercase tracking-wider">
                            ${badgeLabel}
                        </span>
                        ${item.alias_hit_count > 1 ? `
                            <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" title="Matched multiple aliases/synonyms">
                                ⚡ ${item.alias_hit_count} alias matches
                            </span>
                        ` : ''}
                        ${scoreVal !== undefined ? `
                            <span class="text-[10px] font-mono px-2 py-0.5 rounded glass-panel text-muted" title="Hybrid Rerank Score">
                                Score: ${Number(scoreVal).toFixed(2)}
                            </span>
                        ` : ''}
                    </div>
                    <span class="text-xs font-mono text-muted" title="Document Article Length">
                        ${lenLabel}
                    </span>
                </div>

                <h4 class="font-bold text-base text-neutral-100 mb-1 truncate" title="${escapeHTML(title)}">
                    ${escapeHTML(title)}
                </h4>

                ${heading && heading !== title ? `
                    <p class="text-xs text-brand-300 font-semibold mb-2 truncate" title="${escapeHTML(heading)}">
                        📑 Heading: ${escapeHTML(heading)}
                    </p>
                ` : ''}

                <p class="text-xs text-muted leading-relaxed mb-4">
                    ${cleanSnippet}
                </p>

                <div class="flex items-center justify-between gap-3 pt-2 border-t border-gray-700/40">
                    <span class="text-[11px] font-mono text-neutral-400 truncate max-w-[60%]" title="${escapeHTML(filePath || title)}">
                        ${escapeHTML(filePath || (sourceType === 'zim' ? 'ZIM Archive' : title))}
                    </span>
                    <a href="#" class="${actionClass} primary-btn py-2 px-4 rounded-xl text-xs font-bold inline-flex items-center gap-1.5 no-underline shrink-0"
                       ${dataAttrs}>
                        Open Reference Document ↗
                    </a>
                </div>
            </div>
        `;
    }

    // =========================================================
    // 2. CENTRAL SEARCH RESULTS PAGE RENDERER
    // =========================================================
    function _matchesSourceFilter(h, filterName) {
        if (filterName === 'all') return true;
        const src = String(h.source || '').toLowerCase();
        const fp = String(h.file_path || '').toLowerCase();
        const docId = String(h.doc_id || '').toLowerCase();
        if (filterName === 'pdf') return src === 'pdf' || fp.endsWith('.pdf');
        if (filterName === 'obsidian') return src === 'obsidian' || fp.endsWith('.md');
        if (filterName === 'zim') return src === 'zim' || fp.endsWith('.zim') || docId.startsWith('zim');
        return true;
    }

    function renderCentralSearchResults() {
        const container = document.getElementById('search-results-container');
        const queryDisplay = document.getElementById('search-query-display');
        const countBadge = document.getElementById('search-results-count-badge');
        const aliasesDisplay = document.getElementById('search-aliases-display');

        if (queryDisplay) {
            queryDisplay.textContent = currentSearchQuery ? `Search: "${currentSearchQuery}"` : 'Search Results';
        }

        if (aliasesDisplay) {
            if (currentSearchAliases && currentSearchAliases.length > 1) {
                aliasesDisplay.innerHTML = `<span class="font-semibold text-brand-300">Aliases &amp; Synonyms Included:</span> ${currentSearchAliases.map(a => `<span class="glass-panel px-1.5 py-0.5 rounded text-[11px] font-mono mr-1">${escapeHTML(a)}</span>`).join('')}`;
            } else {
                aliasesDisplay.textContent = 'Searched exact match and clinical variants.';
            }
        }

        const filtered = rawSearchHits.filter(h => _matchesSourceFilter(h, activeSourceFilter));
        const limitNum = activeResultLimit === 'all' ? filtered.length : (parseInt(activeResultLimit, 10) || 10);
        const sliced = filtered.slice(0, limitNum);

        if (countBadge) {
            countBadge.textContent = `${sliced.length} of ${filtered.length} unique matching files shown`;
        }

        if (!container) return;

        if (rawSearchHits.length === 0) {
            container.innerHTML = `
                <div class="text-center py-16 glass-panel rounded-2xl p-8 text-muted">
                    <p class="font-bold text-base text-neutral-200 mb-1">No Matching Files Found</p>
                    <p class="text-xs text-muted mb-4">No indexed PDF textbooks, Obsidian notes, or ZIM articles matched "${escapeHTML(currentSearchQuery)}".</p>
                    <button type="button" id="btn-empty-search-diag" class="secondary-btn text-xs px-3.5 py-2 rounded-xl cursor-pointer">
                        Check Diagnostics / Build Index
                    </button>
                </div>
            `;
            document.getElementById('btn-empty-search-diag')?.addEventListener('click', () => {
                if (typeof window.switchTab === 'function') window.switchTab('rag-vault-view');
            });
            return;
        }

        if (sliced.length === 0) {
            container.innerHTML = `
                <div class="text-center py-12 glass-panel rounded-2xl p-6 text-muted">
                    <p class="font-semibold text-sm mb-1">No results for source filter "${escapeHTML(activeSourceFilter.toUpperCase())}"</p>
                    <p class="text-xs mb-3">Try changing the source filter in the Right Sidebar to "All Sources".</p>
                </div>
            `;
            return;
        }

        container.innerHTML = `
            <div class="space-y-4">
                ${sliced.map((hit, idx) => buildSearchCitationCard(hit, idx)).join('')}
            </div>
        `;
    }

    // =========================================================
    // 3. RIGHT SIDEBAR INSPECTOR FOR SEARCH FILTERS
    // =========================================================
    function renderSearchFilterInspector() {
        if (!window.openInspector) return;

        const totalUnique = rawSearchHits.length;
        const pdfCount = rawSearchHits.filter(h => _matchesSourceFilter(h, 'pdf')).length;
        const obsCount = rawSearchHits.filter(h => _matchesSourceFilter(h, 'obsidian')).length;
        const zimCount = rawSearchHits.filter(h => _matchesSourceFilter(h, 'zim')).length;

        const limitOptions = [5, 10, 20, 30, 'all'];

        let html = `
            <div class="space-y-5 text-xs">
                <div class="glass-panel p-3.5 rounded-xl border border-brand-500/30">
                    <span class="text-[10px] text-muted uppercase font-bold tracking-wider block mb-0.5">Active Query</span>
                    <p class="font-bold text-sm text-brand-300 truncate">${escapeHTML(currentSearchQuery || 'None')}</p>
                    <p class="text-[11px] text-neutral-400 mt-1">
                        Found <strong>${totalUnique}</strong> unique matching file(s) across PDF, Obsidian, and ZIM.
                    </p>
                </div>

                <!-- 1. Results Limit Filter -->
                <div class="glass-card p-4 rounded-xl space-y-2.5">
                    <div class="flex items-center justify-between">
                        <label class="font-bold text-neutral-200 text-xs flex items-center gap-1.5">
                            🎯 Results Display Limit
                        </label>
                        <span class="text-[10px] font-mono text-brand-400 font-bold">Top ${activeResultLimit}</span>
                    </div>
                    <p class="text-[11px] text-muted leading-relaxed">
                        Control how many top depth-ranked unique file matches to show on the central results page.
                    </p>
                    <div class="grid grid-cols-5 gap-1.5 pt-1">
                        ${limitOptions.map(lim => {
                            const isSel = String(activeResultLimit) === String(lim);
                            return `
                                <button type="button" class="btn-search-limit ${isSel ? 'primary-btn font-bold' : 'secondary-btn'} text-xs py-1.5 rounded-lg text-center cursor-pointer" data-limit="${lim}">
                                    ${lim === 'all' ? 'All' : lim}
                                </button>
                            `;
                        }).join('')}
                    </div>
                </div>

                <!-- 2. Knowledge Source Filter -->
                <div class="glass-card p-4 rounded-xl space-y-2.5">
                    <label class="font-bold text-neutral-200 text-xs block mb-1">
                        📚 Knowledge Source Filter
                    </label>
                    <div class="space-y-1.5">
                        <button type="button" class="btn-search-source w-full text-left p-2.5 rounded-xl border flex items-center justify-between cursor-pointer transition-all ${activeSourceFilter === 'all' ? 'border-brand-500 bg-brand-500/20 text-neutral-100 font-bold' : 'border-gray-700/50 glass-panel text-muted hover:border-brand-500/50'}" data-source="all">
                            <span>🔍 All Sources</span>
                            <span class="badge bg-ok font-mono text-[10px]">${totalUnique}</span>
                        </button>
                        <button type="button" class="btn-search-source w-full text-left p-2.5 rounded-xl border flex items-center justify-between cursor-pointer transition-all ${activeSourceFilter === 'pdf' ? 'border-brand-500 bg-brand-500/20 text-neutral-100 font-bold' : 'border-gray-700/50 glass-panel text-muted hover:border-brand-500/50'}" data-source="pdf">
                            <span>📕 PDF Textbooks</span>
                            <span class="badge bg-ok font-mono text-[10px]">${pdfCount}</span>
                        </button>
                        <button type="button" class="btn-search-source w-full text-left p-2.5 rounded-xl border flex items-center justify-between cursor-pointer transition-all ${activeSourceFilter === 'obsidian' ? 'border-brand-500 bg-brand-500/20 text-neutral-100 font-bold' : 'border-gray-700/50 glass-panel text-muted hover:border-brand-500/50'}" data-source="obsidian">
                            <span>🟣 Obsidian Notes</span>
                            <span class="badge bg-ok font-mono text-[10px]">${obsCount}</span>
                        </button>
                        <button type="button" class="btn-search-source w-full text-left p-2.5 rounded-xl border flex items-center justify-between cursor-pointer transition-all ${activeSourceFilter === 'zim' ? 'border-brand-500 bg-brand-500/20 text-neutral-100 font-bold' : 'border-gray-700/50 glass-panel text-muted hover:border-brand-500/50'}" data-source="zim">
                            <span>🌐 ZIM Encyclopedia</span>
                            <span class="badge bg-ok font-mono text-[10px]">${zimCount}</span>
                        </button>
                    </div>
                </div>

                <!-- 3. Alias Expansion Summary -->
                <div class="glass-card p-4 rounded-xl space-y-2">
                    <label class="font-bold text-neutral-200 text-xs block">
                        🧬 Included Aliases &amp; Synonyms
                    </label>
                    <p class="text-[11px] text-muted">
                        Search automatically includes clinical variants and layperson synonyms (e.g. <em>heart attack</em> &rarr; <em>myocardial infarction, myocardial ischemia</em>), collapsing duplicate file matches.
                    </p>
                    <div class="flex flex-wrap gap-1 pt-1">
                        ${(currentSearchAliases || []).map(a => `
                            <span class="text-[10px] px-2 py-0.5 rounded-md glass-panel text-brand-300 font-mono border border-brand-500/20">
                                ${escapeHTML(a)}
                            </span>
                        `).join('')}
                    </div>
                </div>
            </div>
        `;

        window.openInspector("Search Filters", html, true);

        const inspectorEl = document.getElementById('inspector-content');
        if (!inspectorEl) return;

        inspectorEl.querySelectorAll('.btn-search-limit').forEach(btn => {
            btn.addEventListener('click', () => {
                const lim = btn.dataset.limit;
                activeResultLimit = lim === 'all' ? 'all' : (parseInt(lim, 10) || 10);
                renderCentralSearchResults();
                renderSearchFilterInspector();
            });
        });

        inspectorEl.querySelectorAll('.btn-search-source').forEach(btn => {
            btn.addEventListener('click', () => {
                activeSourceFilter = btn.dataset.source || 'all';
                renderCentralSearchResults();
                renderSearchFilterInspector();
            });
        });
    }

    window.renderSearchFilterInspector = renderSearchFilterInspector;

    // Bind "Create Quiz Set from Search" button once at init
    document.getElementById('search-create-quiz-btn')?.addEventListener('click', () => {
        if (currentSearchQuery && typeof window.startCustomTopicQuiz === 'function') {
            window.startCustomTopicQuiz(currentSearchQuery);
        }
    });

    // =========================================================
    // 4. PERFORM GLOBAL HYBRID SEARCH
    // =========================================================
    async function performGlobalSearch(query) {
        if (!query || !String(query).trim()) return;
        const cleanQuery = String(query).trim();

        try {
            const diagRes = await fetch('/api/search/diagnostics/tantivy').catch(() =>
                fetch('/api/models/diagnostics/tantivy')
            );
            if (diagRes && diagRes.ok) {
                const diagData = await diagRes.json();
                const hasIndex = Boolean(diagData.exists || diagData.is_built || diagData.has_sources || (diagData.doc_count && diagData.doc_count > 0));
                if (!hasIndex && missingDbModal) {
                    missingDbModal.style.display = 'flex';
                    return;
                }
            }
        } catch (_) {}

        currentSearchQuery = cleanQuery;

        if (typeof window.switchTab === 'function') {
            window.switchTab('search-results-view');
        } else {
            const searchView = document.getElementById('search-results-view');
            if (searchView) {
                document.querySelectorAll('.view-section').forEach(s => s.classList.remove('active'));
                searchView.classList.add('active');
            }
        }

        const container = document.getElementById('search-results-container');
        if (container) {
            container.innerHTML = `
                <div class="text-center py-16 text-muted glass-panel rounded-2xl p-8 border border-brand-500/30">
                    <svg class="animate-spin h-8 w-8 mx-auto text-brand-400 mb-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    <p class="font-bold text-sm text-neutral-100 mb-1">Searching Knowledge Vaults...</p>
                    <p class="text-xs text-muted">Resolving LanceDB vectors, Tantivy BM25, and ZIM archives for <strong>${escapeHTML(cleanQuery)}</strong> (including aliases &amp; synonyms)...</p>
                </div>
            `;
        }

        try {
            const searchResp = await fetch('/api/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: cleanQuery, limit: 50 })
            });
            const searchData = await searchResp.json();

            rawSearchHits = searchData.candidates || [];
            currentSearchAliases = searchData.aliases || [cleanQuery];

            renderCentralSearchResults();
            renderSearchFilterInspector();
        } catch (err) {
            console.error("Hybrid search error:", err);
            if (container) {
                container.innerHTML = `
                    <div class="text-center py-12 glass-panel rounded-2xl p-6 text-danger-400">
                        <p class="font-bold text-sm mb-1">Search Execution Failed</p>
                        <p class="text-xs">${escapeHTML(err.message)}</p>
                    </div>
                `;
            }
        }
    }

    window.performGlobalSearch = performGlobalSearch;

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
    // 5. SETTINGS "BUILD DATABASE" BUTTON (CSV + HYBRID INDEX)
    // =========================================================
    const buildDbBtn = document.getElementById('build-db-btn');
    const buildProgressContainer = document.getElementById('build-progress-container');
    const buildProgress = document.getElementById('build-progress');
    const buildStatus = document.getElementById('build-status');

    if (buildDbBtn) {
        const handleBuildProgress = (msg) => {
            if (!msg || !buildDbBtn.disabled) return;
            const msgType = msg.type || msg.event || '';
            if (msgType !== 'indexing_progress' && msgType !== 'INDEXING_PROGRESS') return;

            const pct = msg.percent !== undefined ? msg.percent : (msg.progress !== undefined ? msg.progress : 0);
            if (buildProgress) {
                buildProgress.classList.remove('progress-indeterminate');
                buildProgress.style.width = `${pct}%`;
            }
            if (buildStatus && msg.message) {
                buildStatus.textContent = `${msg.message} (${pct}%)`;
            }
            const st = String(msg.status || '').toLowerCase();
            if (pct >= 100 || st === 'done' || st === 'error') {
                buildDbBtn.disabled = false;
                if (typeof window.refreshDiagnostics === 'function') {
                    window.refreshDiagnostics();
                }
            }
        };

        document.addEventListener('ws:indexing_progress', (e) => {
            handleBuildProgress(e.detail);
        });

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
                await fetch('/api/mcq/build_db', { method: 'POST' }).catch(() => {});

                const response = await fetch('/api/sources/index', { method: 'POST' });
                const data = await response.json();

                if (data.status !== 'started' && data.status !== 'busy') {
                    if (buildStatus) buildStatus.textContent = 'CSV Question Bank updated.';
                    if (buildProgress) {
                        buildProgress.classList.remove('progress-indeterminate');
                        buildProgress.style.width = '100%';
                    }
                    buildDbBtn.disabled = false;
                    if (typeof window.refreshDiagnostics === 'function') {
                        window.refreshDiagnostics();
                    }
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
    // 6. FALLBACK LEGACY DIAGNOSTICS (Only if settings.js is not loaded)
    // =========================================================
    if (typeof window.refreshDiagnostics !== 'function') {
        const refreshDiagBtn = document.getElementById('refresh-diag-btn');

        const legacyRefreshDiagnostics = async () => {
            const diagLmStudio = document.getElementById('diag-lm-studio');
            const diagDbRows = document.getElementById('diag-db-rows');
            const diagSumatra = document.getElementById('diag-sumatra');
            const diagVendor = document.getElementById('diag-vendor-assets');

            [diagLmStudio, diagDbRows, diagSumatra, diagVendor].forEach(el => {
                if (el) {
                    el.textContent = 'Checking...';
                    el.className = 'badge bg-warning';
                }
            });

            try {
                const [lmResp, dbResp, sumatraResp, vendorResp] = await Promise.all([
                    fetch('/api/search/diagnostics/lm_studio').catch(() => ({ ok: false })),
                    fetch('/api/search/diagnostics/db').catch(() => ({ ok: false })),
                    fetch('/api/search/diagnostics/sumatrapdf').catch(() => ({ ok: false })),
                    fetch('/api/state/verify_vendor').catch(() => ({ ok: false }))
                ]);

                if (lmResp && lmResp.ok && diagLmStudio) {
                    const lmData = await lmResp.json();
                    const isOk = lmData.status === 'ok' || lmData.status === 'success' || lmData.online;
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
                    }
                }

                if (sumatraResp && sumatraResp.ok && diagSumatra) {
                    const sumatraData = await sumatraResp.json();
                    const isAvail = Boolean(sumatraData.available || sumatraData.installed);
                    diagSumatra.textContent = isAvail ? 'Available' : 'Not Found in PATH';
                    diagSumatra.className = isAvail ? 'badge bg-ok' : 'badge bg-warning';
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
        };

        window.refreshDiagnostics = legacyRefreshDiagnostics;
        if (refreshDiagBtn) {
            refreshDiagBtn.addEventListener('click', legacyRefreshDiagnostics);
        }
    }
});