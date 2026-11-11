// ui/js/app.js

window.escapeHTML = function(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
};

const _PURIFY_ALLOWED_TAGS = [
    'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'sup', 'sub',
    'mark', 'details', 'summary', 'kbd', 'style', 'figure', 'figcaption',
    'span', 'div', 'p', 'b', 'i', 'strong', 'em', 'code', 'pre', 'a',
    'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'br',
    'caption', 'col', 'colgroup', 'section', 'header', 'footer', 'nav', 'article', 'aside', 'main',
    'svg', 'g', 'path', 'rect', 'circle', 'line', 'polyline', 'polygon', 'text', 'tspan',
    'image', 'use', 'defs', 'clipPath', 'pattern', 'linearGradient', 'radialGradient', 'stop', 'foreignObject', 'title', 'desc'
];

const _PURIFY_ALLOWED_ATTRS = [
    'id', 'target', 'class', 'style', 'src', 'href', 'xlink:href', 'alt', 'title', 'width', 'height', 'align', 'valign', 'loading',
    'colspan', 'rowspan', 'cellpadding', 'cellspacing', 'border', 'role', 'aria-label', 'aria-hidden',
    'data-file', 'data-file-path', 'data-filepath', 'data-vault',
    'data-page', 'data-article-id', 'data-title', 'data-source-type', 'data-anchor',
    'data-note-target', 'data-book', 'data-callout',
    'viewBox', 'viewbox', 'preserveAspectRatio', 'xmlns', 'xmlns:xlink', 'version',
    'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit',
    'stroke-dasharray', 'stroke-dashoffset', 'stroke-opacity', 'fill-opacity', 'opacity',
    'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'dx', 'dy',
    'text-anchor', 'font-size', 'font-family', 'font-weight', 'dominant-baseline', 'transform'
];

/**
 * Converts a unified_article JSON string/object into clean clinical Markdown
 * so raw JSON braces are never rendered into UI views.
 */
window.formatUnifiedArticleToMarkdown = function(unifiedRaw, fallbackExplanation = "", topicTitle = "") {
    if (!unifiedRaw && fallbackExplanation) {
        return String(fallbackExplanation);
    }
    if (!unifiedRaw) return "";

    let parsed = unifiedRaw;
    if (typeof unifiedRaw === 'string') {
        const trimmed = unifiedRaw.trim();
        if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
            try {
                parsed = JSON.parse(trimmed);
            } catch (_) {
                return trimmed;
            }
        } else {
            return trimmed;
        }
    }

    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        const lines = [];
        const heading = parsed.subtopic || topicTitle || parsed.section || 'Clinical Synthesis';
        lines.push(`### ${heading}`);

        if (parsed.conditional_context && String(parsed.conditional_context).trim()) {
            lines.push(`\n**Clinical Context:** ${String(parsed.conditional_context).trim()}`);
        }

        const pts = Array.isArray(parsed.clinical_points) ? parsed.clinical_points : [];
        if (pts.length > 0) {
            lines.push('');
            pts.forEach(pt => {
                if (typeof pt === 'string' && pt.trim()) {
                    lines.push(`- ${pt.trim()}`);
                } else if (pt && typeof pt === 'object') {
                    const text = pt.point || pt.text || pt.fact || pt.content || '';
                    if (!text) return;
                    const src = pt.source || pt.source_article || '';
                    const pg = pt.page || pt.page_number || '';
                    const refBadge = src ? ` *(Ref: ${src}${pg ? ` p.${pg}` : ''})*` : '';
                    lines.push(`- ${text}${refBadge}`);
                }
            });
        } else if (parsed.raw_text && String(parsed.raw_text).trim()) {
            lines.push(`\n${String(parsed.raw_text).trim()}`);
        } else if (fallbackExplanation && String(fallbackExplanation).trim()) {
            lines.push(`\n${String(fallbackExplanation).trim()}`);
        }

        const disc = parsed.discrepancies || parsed.variance_audit || '';
        if (disc && String(disc).trim() && !['none', 'n/a'].includes(String(disc).trim().toLowerCase())) {
            lines.push(`\n#### Clinical Discrepancies & Staging Variances\n${String(disc).trim()}`);
        }

        return lines.join('\n').trim();
    }

    return String(fallbackExplanation || unifiedRaw || '');
};

/**
 * Renders Markdown (including tables | | and inline HTML), protects LaTeX math blocks
 * ($...$ and $$...$$) from markdown underscore mangling, sanitizes via DOMPurify,
 * and triggers KaTeX math rendering if available.
 */
window.renderMarkdownAndMath = function(rawText, containerEl = null) {
    if (!rawText) {
        if (containerEl) containerEl.innerHTML = '';
        return '';
    }

    const mathBlocks = [];
    let protectedText = String(rawText)
        .replace(/\$\$([\s\S]+?)\$\$/g, (_, math) => {
            const id = `@@MATH_DISPLAY_${mathBlocks.length}@@`;
            mathBlocks.push({ id, math: math.trim(), display: true });
            return id;
        })
        .replace(/\$([^\$\n]+?)\$/g, (_, math) => {
            const id = `@@MATH_INLINE_${mathBlocks.length}@@`;
            mathBlocks.push({ id, math: math.trim(), display: false });
            return id;
        });

    let html = protectedText;
    if (window.marked) {
        try {
            html = window.marked.parse(protectedText, { gfm: true, breaks: true });
        } catch (e) {
            html = window.escapeHTML(protectedText).replace(/\n/g, '<br/>');
        }
    } else {
        html = window.escapeHTML(protectedText).replace(/\n/g, '<br/>');
    }

    if (window.DOMPurify) {
        html = window.DOMPurify.sanitize(html, {
            ADD_TAGS: _PURIFY_ALLOWED_TAGS,
            ADD_ATTR: _PURIFY_ALLOWED_ATTRS
        });
    }

    // Restore math blocks and render via KaTeX if loaded
    mathBlocks.forEach(block => {
        let renderedMath = block.display ? `$$${window.escapeHTML(block.math)}$$` : `$${window.escapeHTML(block.math)}$`;
        if (window.katex) {
            try {
                renderedMath = window.katex.renderToString(block.math, {
                    displayMode: block.display,
                    throwOnError: false
                });
            } catch (_) {}
        }
        html = html.replace(block.id, () => renderedMath);
    });

    if (containerEl) {
        containerEl.innerHTML = html;
        if (window.renderMathInElement) {
            try {
                window.renderMathInElement(containerEl, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false },
                        { left: '\\(', right: '\\)', display: false },
                        { left: '[', right: ']', display: true }
                    ],
                    throwOnError: false
                });
            } catch (_) {}
        }
    }
    return html;
};

// =========================================================
// Contextual 3rd-Column Right Inspector Controller
// =========================================================
window.openInspector = function(title, htmlContent, forceOpen = true) {
    const sidebar = document.getElementById('right-sidebar');
    const content = document.getElementById('inspector-content');
    if (!sidebar || !content) return;

    const isCollapsed = sidebar.classList.contains('collapsed');

    if (forceOpen || !isCollapsed) {
        sidebar.classList.remove('collapsed');
        sidebar.style.width = '380px';
        sidebar.style.padding = '20px';
        sidebar.style.border = '';
        sidebar.style.overflowY = 'auto';
    } else {
        sidebar.classList.add('collapsed');
        sidebar.style.width = '0px';
        sidebar.style.padding = '0px';
        sidebar.style.border = 'none';
    }

    const titleEl = sidebar.querySelector('h3');
    if (titleEl && title) {
        titleEl.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
            <span>${window.escapeHTML(title)}</span>
        `;
    }

    content.innerHTML = htmlContent;
};

window.closeInspector = function() {
    const sidebar = document.getElementById('right-sidebar');
    if (!sidebar) return;
    sidebar.classList.add('collapsed');
    sidebar.style.width = '0px';
    sidebar.style.padding = '0px';
    sidebar.style.border = 'none';
};

// =========================================================
// Study Topics Right Sidebar Topic List Controller
// =========================================================
window.currentStudyTopics = ['Amyloidosis', 'Amoxicillin', 'Myocardial Infarction'];

function openRegenerateModal(type) {
    const modal = document.getElementById('regenerate-confirm-modal');
    const title = document.getElementById('regen-modal-title');
    const desc = document.getElementById('regen-modal-desc');
    const btnMissing = document.getElementById('regen-opt-missing');
    const btnAll = document.getElementById('regen-opt-all');
    const btnCancel = document.getElementById('regen-opt-cancel');

    if (!modal) return;

    if (type === 'refs') {
        if (title) title.textContent = "Generate Reference Links";
        if (desc) desc.textContent = "Do you want to generate reference links for all topics or only for topics without references?";
        if (btnMissing) btnMissing.textContent = "Only for Topics Without References";
        if (btnAll) btnAll.textContent = "Regenerate for All Topics";
    } else {
        if (title) title.textContent = "Generate LLM Explanations";
        if (desc) desc.textContent = "Do you want to generate LLM explanations for all topics or only for topics without explanations?";
        if (btnMissing) btnMissing.textContent = "Only for Topics Without Explanations";
        if (btnAll) btnAll.textContent = "Regenerate for All Topics";
    }

    modal.style.display = 'flex';

    const handleChoice = async (mode) => {
        modal.style.display = 'none';
        const topics = window.currentStudyTopics;
        if (!topics || topics.length === 0) return;

        if (type === 'refs') {
            if (window.showToast) window.showToast("Resolving reference links for topics...");
            try {
                const res = await fetch('/api/mcq/topics/generate_references', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ topics: topics, mode: mode })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (window.showToast) window.showToast(`Reference links generated for ${data.count} topic(s)!`);
                    window.renderStudyTopicsInspector(null, false);
                }
            } catch (e) {
                console.error("Failed to generate references:", e);
            }
        } else {
            if (window.showToast) window.showToast("Queuing LLM explanation tasks...");
            try {
                const res = await fetch('/api/mcq/topics/generate_explanations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ topics: topics, mode: mode })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (window.showToast) window.showToast(`Enqueued ${data.count} LLM synthesis task(s)!`);
                    window.renderStudyTopicsInspector(null, false);
                }
            } catch (e) {
                console.error("Failed to generate explanations:", e);
            }
        }
    };

    if (btnMissing) btnMissing.onclick = () => handleChoice('missing');
    if (btnAll) btnAll.onclick = () => handleChoice('all');
    if (btnCancel) btnCancel.onclick = () => { modal.style.display = 'none'; };
}

window.renderStudyTopicsInspector = async function(topicsList = null, forceOpen = true) {
    if (Array.isArray(topicsList) && topicsList.length > 0) {
        window.currentStudyTopics = topicsList.map(t => (typeof t === 'string' ? t : t.name)).filter(Boolean);
    }

    const topics = window.currentStudyTopics;
    if (!topics || topics.length === 0) {
        window.openInspector(
            "Study Topics List",
            `<div class="space-y-4 text-xs p-2 text-center text-muted">
                <p>Extract entities in MCQ Extractor to load study topics here.</p>
            </div>`,
            forceOpen
        );
        return;
    }

    let statuses = {};
    try {
        const statusRes = await fetch('/api/mcq/topics/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topics: topics })
        });
        if (statusRes.ok) {
            const data = await statusRes.json();
            statuses = data.topics || {};
        }
    } catch (e) {
        console.warn("Failed to fetch topic statuses:", e);
    }

    let html = `<div class="flex flex-col h-full text-xs space-y-4 justify-between">`;
    html += `<div class="space-y-2 overflow-y-auto flex-1 pr-1">`;
    html += `<p class="text-[11px] text-muted mb-2">Click any topic to open notes &amp; references:</p>`;

    topics.forEach((topic, idx) => {
        const st = statuses[topic] || { has_refs: false, has_note: false };
        html += `
            <div class="study-topic-item glass-panel p-3 rounded-xl border border-gray-700/50 hover:border-brand-500 cursor-pointer transition-all flex items-center justify-between gap-2"
                 data-topic="${window.escapeHTML(topic)}">
                <div class="min-w-0 flex-1">
                    <p class="font-bold text-xs truncate text-neutral-100">${idx + 1}. ${window.escapeHTML(topic)}</p>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-mono ${st.has_refs ? 'bg-brand-600/20 text-brand-300 border border-brand-500/30' : 'opacity-40 glass-panel'}" title="${st.has_refs ? 'Reference links resolved' : 'No reference links'}">
                        🔗 ${st.has_refs ? 'Refs' : 'No Refs'}
                    </span>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-mono ${st.has_note ? 'bg-success-500/20 text-success-300 border border-success-500/30' : 'opacity-40 glass-panel'}" title="${st.has_note ? 'LLM explanation ready' : 'No LLM note'}">
                        🤖 ${st.has_note ? 'Note' : 'No Note'}
                    </span>
                </div>
            </div>
        `;
    });
    html += `</div>`;

    html += `
        <div class="pt-3 border-t border-gray-700/50 space-y-2 mt-auto">
            <button id="btn-sidebar-gen-refs" class="secondary-btn w-full py-2.5 text-xs font-bold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer">
                🔗 Generate Reference Links
            </button>
            <button id="btn-sidebar-gen-llm" class="primary-btn w-full py-2.5 text-xs font-bold rounded-xl flex items-center justify-center gap-1.5 cursor-pointer">
                🤖 Generate LLM Explanations
            </button>
        </div>
    `;
    html += `</div>`;

    window.openInspector("Study Topics List", html, forceOpen);

    const contentEl = document.getElementById('inspector-content');
    contentEl?.querySelectorAll('.study-topic-item').forEach(item => {
        item.addEventListener('click', async () => {
            const topic = item.dataset.topic;
            if (!topic) return;

            const carouselContainer = document.getElementById('hub-carousel-container');
            if (carouselContainer && carouselContainer.style.display === 'flex' && typeof window.selectTopicInCarousel === 'function') {
                const selected = window.selectTopicInCarousel(topic);
                if (selected) return;
            }

            const expContainer = document.getElementById('explanation-container');
            if (expContainer) {
                expContainer.classList.remove('content-placeholder');
                expContainer.innerHTML = `<div class="text-center py-8 text-muted animate-pulse">Loading synthesis for ${window.escapeHTML(topic)}...</div>`;
            }
            try {
                const sRes = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
                if (sRes.ok) {
                    const sData = await sRes.json();
                    if (expContainer) {
                        let textToRender = window.formatUnifiedArticleToMarkdown(
                            sData.unified_article,
                            sData.enhanced_explanation,
                            topic
                        );
                        if (!textToRender) {
                            textToRender = `### ${topic}\n\nReferences resolved for **${topic}**.`;
                        }
                        window.renderMarkdownAndMath(textToRender, expContainer);
                        expContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                }
            } catch (e) {
                console.error("Failed to load topic synthesis:", e);
            }
        });
    });

    document.getElementById('btn-sidebar-gen-refs')?.addEventListener('click', () => {
        openRegenerateModal('refs');
    });

    document.getElementById('btn-sidebar-gen-llm')?.addEventListener('click', () => {
        openRegenerateModal('llm');
    });
};

// =========================================================
// Unified In-Browser Document Reader (Obsidian & ZIM)
// =========================================================
window.closeDocumentReader = function() {
    const viewerContainer = document.getElementById('zim-viewer-container');
    if (viewerContainer) viewerContainer.style.display = 'none';
    const contentArea = document.getElementById('zim-content-area');
    if (contentArea) contentArea.innerHTML = '';
    document.body.classList.remove('zim-reader-open');
    if (window._readerHistory) window._readerHistory.length = 0;
};

window.openDocumentReader = function({ title, sourceType, htmlContent, markdownContent, filePath, anchor }) {
    const viewerContainer = document.getElementById('zim-viewer-container');
    const contentArea = document.getElementById('zim-content-area');
    if (!viewerContainer || !contentArea) return;

    const headerTitle = viewerContainer.querySelector('.overlay-header h2');
    if (headerTitle) {
        const badge = sourceType === 'obsidian' ? 'Obsidian Note' : 'ZIM Encyclopedia';
        headerTitle.innerHTML = `${window.escapeHTML(title || 'Document Viewer')} <span class="badge bg-ok ml-2">${badge}</span>`;
    }

    contentArea.innerHTML = '';
    const wrapper = document.createElement('div');
    wrapper.className = 'w-full max-w-6xl mx-auto glass-card p-8 prose';

    if (markdownContent !== undefined && markdownContent !== null) {
        window.renderMarkdownAndMath(markdownContent, wrapper);
    } else if (htmlContent !== undefined && htmlContent !== null) {
        const safeHTML = window.DOMPurify
            ? window.DOMPurify.sanitize(htmlContent, {
                ADD_TAGS: _PURIFY_ALLOWED_TAGS,
                ADD_ATTR: _PURIFY_ALLOWED_ATTRS
            })
            : htmlContent;
        wrapper.innerHTML = safeHTML;
        if (window.renderMathInElement) {
            try {
                window.renderMathInElement(wrapper, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false },
                        { left: '\\(', right: '\\)', display: false },
                        { left: '[', right: ']', display: true }
                    ],
                    throwOnError: false
                });
            } catch (_) {}
        }
    }

    if (filePath && sourceType === 'obsidian') {
        const footer = document.createElement('div');
        footer.className = 'mt-6 pt-4 border-t border-gray-700/50 flex justify-between items-center text-xs text-muted';
        footer.innerHTML = `
            <span class="font-mono truncate">${window.escapeHTML(filePath)}</span>
            <button id="open-in-obsidian-native-btn" class="secondary-btn px-3 py-1.5 text-xs">Open in Obsidian App ↗</button>
        `;
        wrapper.appendChild(footer);
        setTimeout(() => {
            document.getElementById('open-in-obsidian-native-btn')?.addEventListener('click', () => {
                fetch('/api/notes/open-obsidian', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: filePath, anchor: anchor || '' })
                }).catch(() => {
                    window.location.href = `obsidian://open?path=${encodeURIComponent(filePath)}`;
                });
            });
        }, 0);
    }

    contentArea.appendChild(wrapper);
    document.body.classList.add('zim-reader-open');
    viewerContainer.style.display = 'flex';

    // Scroll to heading anchor if specified
    if (anchor) {
        setTimeout(() => {
            const cleanAnchor = String(anchor).trim().replace(/^#/, '');
            const targetHeading = document.getElementById(cleanAnchor);
            if (targetHeading && typeof targetHeading.scrollIntoView === "function") {
                targetHeading.scrollIntoView({ behavior: 'smooth', block: 'start' });
                return;
            }
            const normAnchor = cleanAnchor.toLowerCase();
            const headings = wrapper.querySelectorAll('h1, h2, h3, h4, h5, h6');
            for (const h of headings) {
                if (
                    (h.id && h.id.toLowerCase() === normAnchor) ||
                    (h.textContent || '').trim().toLowerCase().includes(normAnchor)
                ) {
                    h.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    break;
                }
            }
        }, 60);
    }
};

// Global helper for opening PDF / Obsidian / ZIM resources from anywhere
window.openLocalResource = async function(type, pathOrId, extraPageOrTitle = 1, extraMeta = {}) {
    if (!pathOrId) return;
    const normType = String(type || '').toLowerCase();

    if (normType === 'pdf') {
        try {
            const res = await fetch('/api/sources/open_pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: pathOrId,
                    page: parseInt(extraPageOrTitle, 10) || 1
                })
            });
            const data = await res.json();
            if (data.status !== 'success') console.error('SumatraPDF error:', data.message);
        } catch (err) {
            console.error('Failed to launch PDF:', err);
        }
        return;
    }

    if (normType === 'obsidian') {
        const mode = (window.AppState && window.AppState.get().obsidianOpenMode) || 'browser';
        const anchor = extraMeta?.anchor || '';
        if (mode === 'app') {
            try {
                const res = await fetch('/api/notes/open-obsidian', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        file_path: pathOrId,
                        title: typeof extraPageOrTitle === 'string' ? extraPageOrTitle : '',
                        anchor: anchor
                    })
                });
                const data = await res.json();
                if (data.status !== 'success' && data.uri) {
                    window.location.href = data.uri;
                }
            } catch (_) {
                window.location.href = `obsidian://open?path=${encodeURIComponent(pathOrId)}`;
            }
        } else {
            try {
                const qParams = new URLSearchParams();
                if (pathOrId.includes('/') || pathOrId.includes('\\') || pathOrId.endsWith('.md')) {
                    qParams.set('file_path', pathOrId);
                } else {
                    qParams.set('title', pathOrId);
                }
                if (anchor) qParams.set('anchor', anchor);
                const res = await fetch(`/api/notes/obsidian-note?${qParams.toString()}`);
                const data = await res.json();
                if (data.status === 'success') {
                    window.openDocumentReader({
                        title: data.title || extraPageOrTitle || 'Obsidian Note',
                        sourceType: 'obsidian',
                        markdownContent: data.markdown,
                        filePath: data.file_path || pathOrId,
                        anchor: anchor
                    });
                } else {
                    alert(data.message || 'Could not load Obsidian note.');
                }
            } catch (err) {
                console.error('Failed to load Obsidian note in browser:', err);
            }
        }
        return;
    }

    if (normType === 'zim') {
        try {
            const qParams = new URLSearchParams({ article_id: pathOrId });
            if (extraMeta?.zimPath) qParams.set('zim_path', extraMeta.zimPath);
            const res = await fetch(`/api/notes/zim-article?${qParams.toString()}`);
            const data = await res.json();
            if (data.status === 'success') {
                window.openDocumentReader({
                    title: data.title || pathOrId,
                    sourceType: 'zim',
                    htmlContent: data.html,
                    filePath: data.zim_path || extraMeta?.zimPath || ''
                });
            } else {
                alert(data.message || `ZIM article "${pathOrId}" not found.`);
            }
        } catch (err) {
            console.error('Failed to load ZIM article:', err);
        }
    }
};

// =========================================================
// Global Live WebSocket Hub Client (Auto-Reconnecting)
// =========================================================
(function initGlobalWebSocket() {
    let ws = null;
    let reconnectTimer = null;
    let reconnectAttempts = 0;

    function connectWS() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${window.location.host}/ws`;
        try {
            ws = new WebSocket(wsUrl);
            window.appWebSocket = ws;

            ws.onopen = async () => {
                reconnectAttempts = 0;
                if (window.appStateStore && typeof window.appStateStore.reconcileFullState === 'function') {
                    await window.appStateStore.reconcileFullState();
                }
            };

            ws.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    const ts = payload.timestamp ? Number(payload.timestamp) : Date.now();
                    const msgType = payload.type || payload.event || '';

                    if (window.appStateStore && typeof window.appStateStore.applyServerPatch === 'function') {
                        const patchObj = {};
                        if (msgType === 'task_update' || msgType === 'TASK_UPDATE') {
                            patchObj.queueTasks = [payload.task || payload];
                        } else if (msgType === 'session_update' || msgType === 'SESSION_STATUS_UPDATE') {
                            patchObj.sessionUpdate = payload.session || payload;
                        }
                        if (Object.keys(patchObj).length > 0) {
                            window.appStateStore.applyServerPatch(patchObj, ts);
                        }
                    }

                    document.dispatchEvent(new CustomEvent('ws:message', { detail: payload }));

                    if (msgType === 'task_update' || msgType === 'TASK_UPDATE') {
                        document.dispatchEvent(new CustomEvent('ws:task_update', { detail: payload }));
                    } else if (msgType === 'session_update' || msgType === 'SESSION_STATUS_UPDATE') {
                        document.dispatchEvent(new CustomEvent('ws:session_update', { detail: payload }));
                    } else if (msgType === 'indexing_progress' || msgType === 'INDEXING_PROGRESS') {
                        document.dispatchEvent(new CustomEvent('ws:indexing_progress', { detail: payload }));
                    }
                } catch (_) {}
            };

            ws.onclose = () => {
                if (reconnectTimer) clearTimeout(reconnectTimer);
                const delay = window.computeWsBackoffDelay ? window.computeWsBackoffDelay(reconnectAttempts) : 3000;
                reconnectAttempts += 1;
                reconnectTimer = setTimeout(connectWS, delay);
            };

            ws.onerror = () => {
                try { ws.close(); } catch (_) {}
            };
        } catch (_) {
            if (reconnectTimer) clearTimeout(reconnectTimer);
            const delay = window.computeWsBackoffDelay ? window.computeWsBackoffDelay(reconnectAttempts) : 5000;
            reconnectAttempts += 1;
            reconnectTimer = setTimeout(connectWS, delay);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', connectWS);
    } else {
        connectWS();
    }
})();

document.addEventListener('DOMContentLoaded', async () => {
    if (window.AppState && window.AppState.fetch) {
        await window.AppState.fetch();
    }
    if (typeof window.loadSettingsFromServer === 'function') {
        await window.loadSettingsFromServer();
    }

    window.closeInspector();
    document.getElementById('close-inspector-btn')?.addEventListener('click', () => {
        window.closeInspector();
        if (typeof window.clearActiveInspectorSession === 'function') {
            window.clearActiveInspectorSession();
        }
    });

    // Sidebar Collapse
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.left-rail');
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
        });
    }

    // Tab Navigation
    const navButtons = document.querySelectorAll('.nav-btn');
    const viewSections = document.querySelectorAll('.view-section');

    const switchTab = (targetId) => {
        if (typeof window.closeTopicCarousel === 'function') {
            window.closeTopicCarousel();
        } else {
            const carousel = document.getElementById('hub-carousel-container');
            if (carousel) carousel.style.display = 'none';
        }

        const reviewContainer = document.getElementById('hub-review-container');
        if (reviewContainer) reviewContainer.style.display = 'none';

        if (typeof window.closeDocumentReader === 'function') {
            window.closeDocumentReader();
        } else {
            const zimViewer = document.getElementById('zim-viewer-container');
            if (zimViewer) zimViewer.style.display = 'none';
            document.body.classList.remove('zim-reader-open');
        }

        const hubMainContainer = document.getElementById('hub-main-container');
        if (hubMainContainer && targetId === 'knowledge-hub-view') {
            hubMainContainer.style.display = 'block';
        }
        const hubHeader = document.querySelector('#knowledge-hub-view .view-header');
        if (hubHeader && targetId === 'knowledge-hub-view') {
            hubHeader.style.display = 'flex';
        }

        viewSections.forEach(sec => {
            sec.style.display = '';
            sec.classList.toggle('active', sec.id === targetId);
        });
        navButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.target === targetId));

        if (targetId === 'search-results-view') {
            if (typeof window.renderSearchFilterInspector === 'function') {
                window.renderSearchFilterInspector();
            }
        } else if (targetId === 'home-view') {
            window.closeInspector();
        } else if (targetId === 'dashboard-view') {
            if (typeof window.renderStudyTopicsInspector === 'function') {
                window.renderStudyTopicsInspector(null, false);
            }
        } else if (targetId === 'quiz-view') {
            if (window.currentQuiz && Array.isArray(window.currentQuiz.questions) && window.currentQuiz.questions[window.currentIndex]) {
                if (typeof window.updateInspectorForQuestion === 'function') {
                    window.updateInspectorForQuestion(window.currentQuiz.questions[window.currentIndex]);
                }
            } else {
                window.closeInspector();
            }
        } else if (targetId === 'knowledge-hub-view') {
            if (typeof window.renderHubInspectorIfActive === 'function') {
                window.renderHubInspectorIfActive();
            } else {
                window.closeInspector();
            }
        } else {
            window.closeInspector();
        }
    };

    window.switchTab = switchTab;

    function triggerViewActivation(targetId) {
        if (targetId === 'settings-view' && typeof window.refreshDiagnostics === 'function') {
            window.refreshDiagnostics();
        }
        if (targetId === 'knowledge-hub-view' && typeof window.loadSessions === 'function') {
            window.loadSessions();
        }
        if (targetId === 'dashboard-view') {
            if (typeof window.initDashboard === 'function') {
                window.initDashboard();
            } else if (typeof window.updateQuestionsLeftToReview === 'function') {
                window.updateQuestionsLeftToReview();
            }
        }
        if (targetId === 'habit-tracker-view' && typeof window.initHabits === 'function') {
            window.initHabits();
        }
        if (targetId === 'expertise-view' && typeof window.initExpertiseMap === 'function') {
            window.initExpertiseMap();
        }
        if (targetId === 'home-view') {
            if (typeof window.initTestFeatures === 'function') {
                window.initTestFeatures();
            }
        }
        if (targetId === 'layout-analysis-screen' && typeof window.initAnalysisWorkspace === 'function') {
            window.initAnalysisWorkspace();
        }
        if (targetId === 'layout-toc-screen' && typeof window.initTocWorkspace === 'function') {
            window.initTocWorkspace();
        }
        if (targetId === 'my-notes-view') {
            if (!window._myNotesInstance && window.MyNotesController) {
                const notesContainer = document.getElementById('my-notes-view');
                if (notesContainer) {
                    window._myNotesInstance = new window.MyNotesController();
                    window._myNotesInstance.init(notesContainer);
                }
            } else if (window._myNotesInstance && typeof window._myNotesInstance.loadNotes === 'function') {
                window._myNotesInstance.loadNotes();
            }
        }
    }

    // Wrap switchTab to always invoke triggerViewActivation and chart cleanup
    const originalSwitchTab = window.switchTab;
    window.switchTab = function(targetId) {
        if (typeof window.destroyAllChartsOnViewUnmount === 'function') {
            window.destroyAllChartsOnViewUnmount();
        }
        originalSwitchTab(targetId);
        triggerViewActivation(targetId);
    };

    navButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const targetId = e.currentTarget.dataset.target;
            window.switchTab(targetId);
        });
    });

    // =========================================================
    // Theme Toggle, Dynamic Wallpaper Timer & WCAG Palette Engine
    // =========================================================
    const themeToggleBtn = document.getElementById('theme-toggle');
    const body = document.body;
    let wallpapersCache = { dark: [], light: [] };

    async function fetchWallpapersList() {
        try {
            const res = await fetch('/api/sources/wallpapers');
            if (res.ok) {
                const data = await res.json();
                wallpapersCache = data || { dark: [], light: [] };
            }
        } catch (err) {
            console.warn('Failed to fetch wallpaper list from backend:', err);
        }
    }

    function getRotationIntervalMs() {
        const mins = (window.AppState && window.AppState.get().wallpaperIntervalMinutes !== undefined)
            ? Number(window.AppState.get().wallpaperIntervalMinutes)
            : 120;
        if (mins <= 0) return Infinity;
        return mins * 60 * 1000;
    }

    function getNextWallpaperUrl(theme, forceAdvance = false) {
        const list = wallpapersCache[theme] || [];
        const now = Date.now();
        const intervalMs = getRotationIntervalMs();
        let lastChange = parseInt(localStorage.getItem('lastWallpaperChangeTimestamp') || '0', 10);
        let bgIndex = parseInt(localStorage.getItem('currentBgIndex') || '0', 10);

        if (!lastChange) {
            lastChange = now;
            bgIndex = 0;
            localStorage.setItem('lastWallpaperChangeTimestamp', lastChange.toString());
            localStorage.setItem('currentBgIndex', bgIndex.toString());
        } else if (forceAdvance || (intervalMs !== Infinity && (now - lastChange >= intervalMs))) {
            bgIndex = bgIndex + 1;
            lastChange = now;
            localStorage.setItem('lastWallpaperChangeTimestamp', lastChange.toString());
            localStorage.setItem('currentBgIndex', bgIndex.toString());
        }

        if (list && list.length > 0) {
            const filename = list[bgIndex % list.length];
            return `/img/${theme}/${encodeURIComponent(filename)}`;
        }
        return `/img/bg-${theme}.jpg`;
    }

    const updateThemeAndBackground = (forceAdvance = false) => {
        const theme = body.classList.contains('dark-theme') ? 'dark' : 'light';
        if (window.AppState && window.AppState.applyAppearance) {
            window.AppState.applyAppearance();
        }
        const imgUrl = getNextWallpaperUrl(theme, forceAdvance);

        const img = new Image();
        img.crossOrigin = "Anonymous";
        img.onload = () => {
            const bgEl = document.querySelector('.background-animation');
            if (bgEl) bgEl.style.backgroundImage = `url("${imgUrl}")`;
            extractAndApplyPalette(img, theme);
        };
        img.onerror = () => {
            const fallbackUrl = `/img/bg-${theme}.jpg`;
            const bgEl = document.querySelector('.background-animation');
            if (bgEl) bgEl.style.backgroundImage = `url("${fallbackUrl}")`;
            const fallbackImg = new Image();
            fallbackImg.crossOrigin = "Anonymous";
            fallbackImg.onload = () => extractAndApplyPalette(fallbackImg, theme);
            fallbackImg.src = fallbackUrl;
        };
        img.src = imgUrl;
    };

    window.changeWallpaperNow = function() {
        updateThemeAndBackground(true);
    };

    // =========================================================
    // Dynamic Wallpaper-Derived Glassmorphic Palette & Tokens
    // =========================================================
    const AdaptiveThemeAndContrastEngine = {
        baseHSL: { h: 220, s: 45 },
        getActiveTheme() {
            return (body.classList.contains('light-theme') || document.documentElement.classList.contains('light'))
                ? 'light'
                : 'dark';
        },
        applyGlobalDerivedTokens() {
            const theme = this.getActiveTheme(); // "light" or "dark"
            const { h, s } = this.baseHSL;
            const hAccent = (h + 24) % 360;
            const hBadge = (h + 332) % 360;
            const root = document.documentElement.style;

            root.setProperty("--bg-hue", `${h}`);
            root.setProperty("--bg-sat", `${s}%`);
            root.setProperty("--hue-primary", `${h}`);
            root.setProperty("--hue-accent", `${hAccent}`);
            root.setProperty("--hue-badge", `${hBadge}`);

            if (theme === "light") {
                // Light Mode Glassmorphic Surfaces & Cards
                root.setProperty("--glass-surface-bg", `hsla(${h}, ${Math.min(s, 45)}%, 94%, 0.72)`);
                root.setProperty("--glass-card-bg", `linear-gradient(145deg, hsla(${h}, ${Math.min(s, 50)}%, 95%, 0.78), hsla(${hAccent}, ${Math.min(s, 44)}%, 90%, 0.68))`);
                root.setProperty("--glass-card-hover-bg", `linear-gradient(145deg, hsla(${h}, ${Math.min(s, 55)}%, 92%, 0.86), hsla(${hAccent}, ${Math.min(s, 48)}%, 87%, 0.80))`);
                root.setProperty("--glass-border", `hsla(${h}, ${s}%, 65%, 0.42)`);
                root.setProperty("--glass-highlight-edge", `hsla(0, 0%, 100%, 0.65)`);

                // Dynamic Buttons ('Explanation', 'Start Quiz', 'Study Topics Covered') & CSV Badge in Light Mode
                root.setProperty("--btn-primary-bg", `linear-gradient(135deg, hsla(${h}, 72%, 38%, 0.92), hsla(${hAccent}, 68%, 32%, 0.92))`);
                root.setProperty("--btn-primary-text", `#F8FAFC`);
                root.setProperty("--btn-primary-border", `hsla(${h}, 75%, 30%, 0.55)`);

                root.setProperty("--btn-secondary-bg", `hsla(${h}, ${Math.min(s + 10, 55)}%, 86%, 0.82)`);
                root.setProperty("--btn-secondary-text", `hsl(${h}, 45%, 14%)`);
                root.setProperty("--btn-secondary-border", `hsla(${h}, ${s}%, 58%, 0.48)`);

                root.setProperty("--btn-study-topics-bg", `linear-gradient(135deg, hsla(${hAccent}, 65%, 88%, 0.88), hsla(${h}, 60%, 84%, 0.88))`);
                root.setProperty("--btn-study-topics-text", `hsl(${hAccent}, 62%, 18%)`);
                root.setProperty("--btn-study-topics-border", `hsla(${hAccent}, 58%, 55%, 0.50)`);

                root.setProperty("--badge-csv-bg", `hsla(${hBadge}, 62%, 88%, 0.85)`);
                root.setProperty("--badge-csv-text", `hsl(${hBadge}, 65%, 20%)`);
                root.setProperty("--badge-csv-border", `hsla(${hBadge}, 55%, 60%, 0.45)`);

                root.setProperty("--text-derived-primary", `hsl(${h}, 36%, 11%)`);
                root.setProperty("--text-derived-muted", `hsl(${h}, 22%, 30%)`);
            } else {
                // Dark Mode Glassmorphic Surfaces & Cards
                root.setProperty("--glass-surface-bg", `hsla(${h}, ${s}%, 13%, 0.68)`);
                root.setProperty("--glass-card-bg", `linear-gradient(145deg, hsla(${h}, ${s}%, 17%, 0.74), hsla(${hAccent}, ${Math.max(s - 6, 24)}%, 12%, 0.66))`);
                root.setProperty("--glass-card-hover-bg", `linear-gradient(145deg, hsla(${h}, ${Math.min(s + 8, 64)}%, 21%, 0.82), hsla(${hAccent}, ${s}%, 15%, 0.76))`);
                root.setProperty("--glass-border", `hsla(${h}, ${Math.min(s + 12, 70)}%, 68%, 0.24)`);
                root.setProperty("--glass-highlight-edge", `hsla(${h}, 80%, 85%, 0.14)`);

                // Dynamic Buttons ('Explanation', 'Start Quiz', 'Study Topics Covered') & CSV Badge in Dark Mode
                root.setProperty("--btn-primary-bg", `linear-gradient(135deg, hsla(${h}, 74%, 46%, 0.88), hsla(${hAccent}, 70%, 38%, 0.88))`);
                root.setProperty("--btn-primary-text", `#F8FAFC`);
                root.setProperty("--btn-primary-border", `hsla(${h}, 80%, 66%, 0.42)`);

                root.setProperty("--btn-secondary-bg", `hsla(${h}, ${s}%, 22%, 0.78)`);
                root.setProperty("--btn-secondary-text", `hsl(${h}, 30%, 95%)`);
                root.setProperty("--btn-secondary-border", `hsla(${h}, ${s}%, 65%, 0.32)`);

                root.setProperty("--btn-study-topics-bg", `linear-gradient(135deg, hsla(${hAccent}, 58%, 24%, 0.84), hsla(${h}, 54%, 20%, 0.84))`);
                root.setProperty("--btn-study-topics-text", `hsl(${hAccent}, 85%, 88%)`);
                root.setProperty("--btn-study-topics-border", `hsla(${hAccent}, 70%, 62%, 0.38)`);

                root.setProperty("--badge-csv-bg", `hsla(${hBadge}, 56%, 22%, 0.82)`);
                root.setProperty("--badge-csv-text", `hsl(${hBadge}, 85%, 86%)`);
                root.setProperty("--badge-csv-border", `hsla(${hBadge}, 68%, 62%, 0.36)`);

                root.setProperty("--text-derived-primary", `hsl(${h}, 28%, 96%)`);
                root.setProperty("--text-derived-muted", `hsl(${h}, 18%, 75%)`);
            }
        }
    };
    window.AdaptiveThemeAndContrastEngine = AdaptiveThemeAndContrastEngine;

    themeToggleBtn?.addEventListener('click', () => {
        const isLight = body.classList.contains('light-theme');
        const newTheme = isLight ? 'dark-theme' : 'light-theme';
        body.classList.replace(isLight ? 'light-theme' : 'dark-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        AdaptiveThemeAndContrastEngine.applyGlobalDerivedTokens();
        updateThemeAndBackground(false);
    });

    setInterval(() => {
        const intervalMs = getRotationIntervalMs();
        if (intervalMs === Infinity) return;
        const lastChange = parseInt(localStorage.getItem('lastWallpaperChangeTimestamp') || '0', 10);
        if (lastChange && (Date.now() - lastChange >= intervalMs)) {
            updateThemeAndBackground(true);
        }
    }, 10000);

    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) body.className = savedTheme;
    await fetchWallpapersList();
    updateThemeAndBackground(false);

    // --- Color Palette Extraction & WCAG Luminance Math ---
    function hslToRgb(h, s, l) {
        let r, g, b;
        if (s === 0) r = g = b = l;
        else {
            const hue2rgb = (p, q, t) => {
                if (t < 0) t += 1;
                if (t > 1) t -= 1;
                if (t < 1/6) return p + (q - p) * 6 * t;
                if (t < 1/2) return q;
                if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
                return p;
            };
            const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
            const p = 2 * l - q;
            r = hue2rgb(p, q, (h / 360) + 1/3);
            g = hue2rgb(p, q, (h / 360));
            b = hue2rgb(p, q, (h / 360) - 1/3);
        }
        return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
    }

    function rgbToHsl(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        const max = Math.max(r, g, b), min = Math.min(r, g, b);
        let h = 0, s = 0, l = (max + min) / 2;
        if (max !== min) {
            const d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            switch (max) {
                case r: h = (g - b) / d + (g < b ? 6 : 0); break;
                case g: h = (b - r) / d + 2; break;
                case b: h = (r - g) / d + 4; break;
            }
            h *= 60;
        }
        return [h, s, l];
    }

    function getRelativeLuminance(r, g, b) {
        const getLin = (c) => {
            const cs = c / 255;
            return cs <= 0.04045 ? cs / 12.92 : Math.pow((cs + 0.055) / 1.055, 2.4);
        };
        return 0.2126 * getLin(r) + 0.7152 * getLin(g) + 0.0722 * getLin(b);
    }

    function getContrastRatio(l1, l2) {
        return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    }

    function extractAndApplyPalette(img, theme) {
        try {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            canvas.width = 256;
            canvas.height = 256;
            ctx.drawImage(img, 0, 0, 256, 256);

            const regions = [
                { x: 0, y: 0, w: 128, h: 128 },
                { x: 128, y: 0, w: 128, h: 128 },
                { x: 0, y: 128, w: 128, h: 128 },
                { x: 128, y: 128, w: 128, h: 128 }
            ];

            const filteredHues = [];
            const vividPixels = [];
            const quadrantDataList = [];

            regions.forEach((region) => {
                const imgData = ctx.getImageData(region.x, region.y, region.w, region.h).data;
                let sumR = 0, sumG = 0, sumB = 0;
                const totalPixels = imgData.length / 4;
                const pixelsWithLum = [];

                for (let p = 0; p < imgData.length; p += 4) {
                    const r = imgData[p], g = imgData[p + 1], b = imgData[p + 2];
                    sumR += r; sumG += g; sumB += b;
                    const [h, s, l] = rgbToHsl(r, g, b);
                    pixelsWithLum.push({ r, g, b, l, h, s });
                    if (l >= 0.08 && l <= 0.92 && s >= 0.12) {
                        filteredHues.push(h);
                    }
                    // Collect chromatic, non-muddy, non-monochrome pixels for artistic button extraction
                    if (s >= 0.22 && l >= 0.15 && l <= 0.85) {
                        vividPixels.push({ h, s, l, r, g, b });
                    }
                }

                pixelsWithLum.sort((a, b) => a.l - b.l);
                const tenthIndex = Math.max(1, Math.floor(totalPixels * 0.10));

                let darkSumR = 0, darkSumG = 0, darkSumB = 0;
                for (let k = 0; k < tenthIndex; k++) {
                    darkSumR += pixelsWithLum[k].r; darkSumG += pixelsWithLum[k].g; darkSumB += pixelsWithLum[k].b;
                }

                let brightSumR = 0, brightSumG = 0, brightSumB = 0;
                for (let k = totalPixels - tenthIndex; k < totalPixels; k++) {
                    brightSumR += pixelsWithLum[k].r; brightSumG += pixelsWithLum[k].g; brightSumB += pixelsWithLum[k].b;
                }

                quadrantDataList.push({
                    avgBg: [Math.round(sumR / totalPixels), Math.round(sumG / totalPixels), Math.round(sumB / totalPixels)],
                    darkBg: [Math.round(darkSumR / tenthIndex), Math.round(darkSumG / tenthIndex), Math.round(darkSumB / tenthIndex)],
                    brightBg: [Math.round(brightSumR / tenthIndex), Math.round(brightSumG / tenthIndex), Math.round(brightSumB / tenthIndex)]
                });
            });

            let domHue = 220;
            if (filteredHues.length > 0) {
                let sinSum = 0, cosSum = 0;
                filteredHues.forEach(h => {
                    const rad = (h * Math.PI) / 180;
                    sinSum += Math.sin(rad);
                    cosSum += Math.cos(rad);
                });
                let avgRad = Math.atan2(sinSum, cosSum);
                if (avgRad < 0) avgRad += 2 * Math.PI;
                domHue = (avgRad * 180) / Math.PI;
            }

            // --- 1. Dynamic Artistic Button Hue Extraction from Picture ---
            // Choose an artistic, vibrant hue derived directly from the picture.
            // If the wallpaper has vivid chromatic pixels, pick an artistic hue with good saturation.
            let buttonHue = domHue;
            if (vividPixels.length > 0) {
                // Select a representative artistic color from vivid pixels (e.g. weighted towards higher saturation)
                vividPixels.sort((a, b) => (b.s * 0.6 + Math.abs(b.l - 0.5) * -0.4) - (a.s * 0.6 + Math.abs(a.l - 0.5) * -0.4));
                // Sample from top 15% most vibrant colors to get an artistic, distinct accent hue
                const topSampleCount = Math.max(1, Math.floor(vividPixels.length * 0.15));
                const pickIndex = Math.floor(Math.random() * topSampleCount);
                buttonHue = vividPixels[pickIndex].h;
            } else {
                // Harmonic offset if image is near-monochrome
                buttonHue = (domHue + 28) % 360;
            }

            // Calculate WCAG AA contrast-safe button colors
            const btnSat = 0.76;
            let btnLum = theme === 'dark' ? 0.44 : 0.40;
            let [rBtn, gBtn, bBtn] = hslToRgb(buttonHue, btnSat, btnLum);
            let btnRelLum = getRelativeLuminance(rBtn, gBtn, bBtn);
            const whiteRelLum = getRelativeLuminance(255, 255, 255);
            const darkRelLum = getRelativeLuminance(15, 23, 42);

            let crWhite = getContrastRatio(btnRelLum, whiteRelLum);
            let crDark = getContrastRatio(btnRelLum, darkRelLum);

            let btnTextColor = '#FFFFFF';
            let btnTextShadow = '0 1px 2px rgba(0, 0, 0, 0.45)';

            if (crWhite >= crDark) {
                // Target white text with at least 4.5:1 contrast ratio
                while (crWhite < 4.5 && btnLum > 0.20) {
                    btnLum -= 0.02;
                    [rBtn, gBtn, bBtn] = hslToRgb(buttonHue, btnSat, btnLum);
                    btnRelLum = getRelativeLuminance(rBtn, gBtn, bBtn);
                    crWhite = getContrastRatio(btnRelLum, whiteRelLum);
                }
                btnTextColor = '#FFFFFF';
                btnTextShadow = '0 1px 2px rgba(0, 0, 0, 0.45)';
            } else {
                // Target dark text with at least 4.5:1 contrast ratio
                while (crDark < 4.5 && btnLum < 0.85) {
                    btnLum += 0.02;
                    [rBtn, gBtn, bBtn] = hslToRgb(buttonHue, btnSat, btnLum);
                    btnRelLum = getRelativeLuminance(rBtn, gBtn, bBtn);
                    crDark = getContrastRatio(btnRelLum, darkRelLum);
                }
                btnTextColor = '#0F172A';
                btnTextShadow = '0 1px 1px rgba(255, 255, 255, 0.4)';
            }

            const hoverLum = btnTextColor === '#FFFFFF'
                ? Math.min(btnLum + 0.07, 0.65)
                : Math.max(btnLum - 0.07, 0.35);

            const [rHover, gHover, bHover] = hslToRgb((buttonHue + 12) % 360, btnSat, hoverLum);

            const hueH = Math.round(buttonHue);
            const hueH2 = Math.round((buttonHue + 18) % 360);
            const sPct = Math.round(btnSat * 100);
            const lPct = Math.round(btnLum * 100);
            const hlPct = Math.round(hoverLum * 100);

            const primaryBg = `linear-gradient(135deg, hsl(${hueH}, ${sPct}%, ${lPct}%), hsl(${hueH2}, ${sPct}%, ${hlPct}%))`;
            const primaryHoverBg = `linear-gradient(135deg, hsl(${Math.round((hueH + 8) % 360)}, ${sPct}%, ${hlPct}%), hsl(${Math.round((hueH + 24) % 360)}, ${sPct}%, ${lPct}%))`;
            const primaryShadow = `0 4px 15px rgba(${rBtn}, ${gBtn}, ${bBtn}, 0.38)`;
            const primaryHoverShadow = `0 6px 20px rgba(${rBtn}, ${gBtn}, ${bBtn}, 0.52)`;

            document.documentElement.style.setProperty('--primary-btn-bg', primaryBg);
            document.documentElement.style.setProperty('--primary-btn-hover-bg', primaryHoverBg);
            document.documentElement.style.setProperty('--primary-btn-text', btnTextColor);
            document.documentElement.style.setProperty('--primary-btn-shadow', primaryShadow);
            document.documentElement.style.setProperty('--primary-btn-hover-shadow', primaryHoverShadow);
            document.documentElement.style.setProperty('--primary-btn-text-shadow', btnTextShadow);
            document.documentElement.style.setProperty('--accent-color', `rgb(${rBtn}, ${gBtn}, ${bBtn})`);
            document.documentElement.style.setProperty('--accent-hover', `rgb(${rHover}, ${gHover}, ${bHover})`);
            document.documentElement.style.setProperty('--input-focus', `rgba(${rBtn}, ${gBtn}, ${bBtn}, 0.35)`);

            document.documentElement.style.setProperty('--bg-dominant-hue', String(Math.round(domHue)));
            document.documentElement.style.setProperty('--bg-dominant-sat', String(Math.round(btnSat * 100)));

            // Compute saturation-weighted average RGB & clamp saturation between 32% and 62%
            let sumSatR = 0, sumSatG = 0, sumSatB = 0, totalSatWeight = 0;
            const fullImgData = ctx.getImageData(0, 0, 256, 256).data;
            for (let p = 0; p < fullImgData.length; p += 16) {
                const r = fullImgData[p], g = fullImgData[p + 1], b = fullImgData[p + 2];
                const [ph, ps, pl] = rgbToHsl(r, g, b);
                if (pl >= 0.05 && pl <= 0.95) {
                    const weight = Math.max(0.08, ps);
                    sumSatR += r * weight;
                    sumSatG += g * weight;
                    sumSatB += b * weight;
                    totalSatWeight += weight;
                }
            }
            let baseHue = Math.round(domHue);
            let baseSat = Math.round(btnSat * 100);
            if (totalSatWeight > 0) {
                const avgR = Math.round(sumSatR / totalSatWeight);
                const avgG = Math.round(sumSatG / totalSatWeight);
                const avgB = Math.round(sumSatB / totalSatWeight);
                const [wH, wS, wL] = rgbToHsl(avgR, avgG, avgB);
                if (wS > 0.08) baseHue = Math.round(wH);
                baseSat = Math.round(wS * 100);
            }
            const clampedSat = Math.max(32, Math.min(62, baseSat));
            AdaptiveThemeAndContrastEngine.baseHSL = { h: baseHue, s: clampedSat };
            AdaptiveThemeAndContrastEngine.applyGlobalDerivedTokens();

            // Contrast-safe brand text tokens derived from wallpaper accent
            if (theme === 'dark') {
                const [rB4, gB4, bB4] = hslToRgb(buttonHue, 0.85, 0.75);
                const [rB3, gB3, bB3] = hslToRgb(buttonHue, 0.80, 0.82);
                document.documentElement.style.setProperty('--c-brand-400', `rgb(${rB4}, ${gB4}, ${bB4})`);
                document.documentElement.style.setProperty('--c-brand-300', `rgb(${rB3}, ${gB3}, ${bB3})`);
            } else {
                const [rB4, gB4, bB4] = hslToRgb(buttonHue, 0.85, 0.35);
                const [rB3, gB3, bB3] = hslToRgb(buttonHue, 0.80, 0.28);
                document.documentElement.style.setProperty('--c-brand-400', `rgb(${rB4}, ${gB4}, ${bB4})`);
                document.documentElement.style.setProperty('--c-brand-300', `rgb(${rB3}, ${gB3}, ${bB3})`);
            }

            const hueShifts = [0, 15, -15, 30];
            const alpha = (window.AppState && typeof window.AppState.get().cardOpacity === 'number')
                ? window.AppState.get().cardOpacity
                : 0.78;

            quadrantDataList.forEach((qData, i) => {
                const h = (domHue + hueShifts[i] + 360) % 360;
                const s = (theme === 'dark') ? 0.35 : 0.30;
                const l = (theme === 'dark') ? 0.17 : 0.87;

                const [rCard, gCard, bCard] = hslToRgb(h, s, l);
                document.documentElement.style.setProperty(`--card-color-${i + 1}`, `${rCard}, ${gCard}, ${bCard}`);

                const blend = (cardC, bgC) => Math.round(alpha * cardC + (1 - alpha) * bgC);
                const effAvg = [blend(rCard, qData.avgBg[0]), blend(gCard, qData.avgBg[1]), blend(bCard, qData.avgBg[2])];
                const effDark = [blend(rCard, qData.darkBg[0]), blend(gCard, qData.darkBg[1]), blend(bCard, qData.darkBg[2])];
                const effBright = [blend(rCard, qData.brightBg[0]), blend(gCard, qData.brightBg[1]), blend(bCard, qData.brightBg[2])];

                const lumAvg = getRelativeLuminance(...effAvg);
                const lumDark = getRelativeLuminance(...effDark);
                const lumBright = getRelativeLuminance(...effBright);

                const darkTextLum = getRelativeLuminance(15, 23, 42);
                const lightTextLum = getRelativeLuminance(248, 250, 252);

                const minCrDarkText = Math.min(
                    getContrastRatio(darkTextLum, lumAvg),
                    getContrastRatio(darkTextLum, lumDark),
                    getContrastRatio(darkTextLum, lumBright)
                );
                const minCrLightText = Math.min(
                    getContrastRatio(lightTextLum, lumAvg),
                    getContrastRatio(lightTextLum, lumDark),
                    getContrastRatio(lightTextLum, lumBright)
                );

                if (minCrLightText >= minCrDarkText) {
                    document.documentElement.style.setProperty(`--text-on-card-${i + 1}`, '#F8FAFC');
                    document.documentElement.style.setProperty(`--text-muted-on-card-${i + 1}`, '#CBD5E1');
                    document.documentElement.style.setProperty(`--card-text-shadow-${i + 1}`, '0 1px 2px rgba(0, 0, 0, 0.35)');
                } else {
                    document.documentElement.style.setProperty(`--text-on-card-${i + 1}`, '#0F172A');
                    document.documentElement.style.setProperty(`--text-muted-on-card-${i + 1}`, '#334155');
                    document.documentElement.style.setProperty(`--card-text-shadow-${i + 1}`, '0 1px 1px rgba(255, 255, 255, 0.5)');
                }
            });
        } catch (err) {
            console.debug('Palette extraction skipped:', err);
        }
    }

    /**
     * Localized wallpaper luminance and contrast detector.
     * Evaluates the active wallpaper background color in a specific bounding client rect or normalized (x, y)
     * position, returning high-contrast text color, muted text color, and text shadow irrespective of light/dark theme.
     */
    window.getLocalizedContrastForElement = function(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') {
            return { textColor: '#FFFFFF', mutedColor: '#CBD5E1', isDarkBg: true };
        }
        try {
            const rect = element.getBoundingClientRect();
            const vpW = window.innerWidth || 1920;
            const vpH = window.innerHeight || 1080;
            const cx = Math.max(0, Math.min(1, (rect.left + rect.width / 2) / vpW));
            const cy = Math.max(0, Math.min(1, (rect.top + rect.height / 2) / vpH));

            // Map (cx, cy) to quadrant (1: top-left, 2: top-right, 3: bottom-left, 4: bottom-right)
            const qIdx = (cy < 0.5 ? (cx < 0.5 ? 1 : 2) : (cx < 0.5 ? 3 : 4));
            const rootStyle = getComputedStyle(document.documentElement);
            const cardColorStr = rootStyle.getPropertyValue(`--card-color-${qIdx}`).trim();
            if (cardColorStr) {
                const parts = cardColorStr.split(',').map(s => parseInt(s.trim(), 10));
                if (parts.length >= 3) {
                    const lum = getRelativeLuminance(parts[0], parts[1], parts[2]);
                    const isDarkBg = lum < 0.38;
                    return {
                        textColor: isDarkBg ? '#F8FAFC' : '#0F172A',
                        mutedColor: isDarkBg ? '#CBD5E1' : '#334155',
                        textShadow: isDarkBg ? '0 1px 2px rgba(0, 0, 0, 0.45)' : '0 1px 1px rgba(255, 255, 255, 0.6)',
                        isDarkBg
                    };
                }
            }
        } catch (_) {}
        return { textColor: '#F8FAFC', mutedColor: '#CBD5E1', isDarkBg: true };
    };

    // =========================================================
    // Global Event Delegation (Citations, Modals & MCQ Extractor)
    // =========================================================
    const helpModal = document.getElementById('help-modal');
    document.getElementById('open-help-btn')?.addEventListener('click', () => {
        if (helpModal) helpModal.style.display = 'flex';
    });
    document.getElementById('close-help-btn')?.addEventListener('click', () => {
        if (helpModal) helpModal.style.display = 'none';
    });
    helpModal?.addEventListener('click', (e) => {
        if (e.target === helpModal) helpModal.style.display = 'none';
    });

    // Initialize Test Features Tab (#home-view)
    if (document.getElementById('home-view')?.classList.contains('active')) {
        if (typeof window.initTestFeatures === 'function') {
            window.initTestFeatures();
        }
    }

    // Unified Document-Level Click Delegation for PDF, Obsidian, and ZIM Citations
    document.addEventListener('click', async (e) => {
        const target = e.target;
        if (!target || typeof target.closest !== 'function') return;

        const citeEl = target.closest('.citation-link, .citation-pdf, .citation-obsidian, .citation-zim');
        if (citeEl) {
            e.preventDefault();
            const ds = citeEl.dataset || {};
            const sourceType = (
                ds.sourceType ||
                (citeEl.classList.contains('citation-pdf') ? 'pdf' : '') ||
                (citeEl.classList.contains('citation-obsidian') ? 'obsidian' : '') ||
                (citeEl.classList.contains('citation-zim') ? 'zim' : '')
            ).toLowerCase();

            const resolvedPath = ds.filePath || ds.filepath || ds.file || '';
            const pageNum = parseInt(ds.page || '1', 10) || 1;
            const anchor = ds.anchor || '';
            const articleId = ds.articleId || ds.title || citeEl.textContent.trim();

            if (sourceType === 'pdf') {
                await window.openLocalResource('pdf', resolvedPath, pageNum);
                return;
            }
            if (sourceType === 'obsidian') {
                await window.openLocalResource('obsidian', resolvedPath || articleId, citeEl.textContent.trim(), { anchor });
                return;
            }
            if (sourceType === 'zim') {
                await window.openLocalResource('zim', articleId || resolvedPath, 1, { zimPath: resolvedPath });
                return;
            }
        }

        const closeZimBtn = target.closest('#close-zim-viewer-btn');
        if (closeZimBtn) {
            window.closeDocumentReader();
            return;
        }

        // Allow clicking anywhere outside text content area (glass-card) to close article
        const viewerContainer = document.getElementById('zim-viewer-container');
        if (viewerContainer && viewerContainer.style.display === 'flex') {
            if (target === viewerContainer || target.id === 'zim-content-area') {
                window.closeDocumentReader();
                return;
            }
        }
    });
});

// --- Phase 7 Unified Safe Markdown + KaTeX Renderer, WS Backoff & rAF Drag Throttling ---
(function () {
    const SAFE_PURIFY_CONFIG = {
        ADD_TAGS: ["math", "semantics", "mrow", "mi", "mo", "mn", "msup", "msub", "mfrac", "msqrt", "mtext", "annotation", "svg", "path", "line", "rect", "mark"],
        ADD_ATTR: ["id", "class", "style", "src", "href", "alt", "width", "loading", "xmlns", "display", "encoding", "viewBox", "d", "preserveAspectRatio", "stroke", "fill", "data-page", "data-source", "data-note-target", "data-book", "data-anchor", "data-callout"],
        FORBID_TAGS: ["script", "iframe", "object", "embed", "form"],
        FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "onfocus"]
    };

    window.renderSafeMarkdown = function (markdownText, targetElement) {
        const raw = String(markdownText || "");
        let html = raw;
        if (typeof window.marked !== "undefined" && typeof window.marked.parse === "function") {
            html = window.marked.parse(raw);
        }
        if (typeof window.DOMPurify !== "undefined" && typeof window.DOMPurify.sanitize === "function") {
            html = window.DOMPurify.sanitize(html, SAFE_PURIFY_CONFIG);
        }
        if (targetElement && targetElement instanceof HTMLElement) {
            targetElement.innerHTML = html;
            if (typeof window.renderMathInElement === "function") {
                try {
                    window.renderMathInElement(targetElement, {
                        delimiters: [
                            { left: "$$", right: "$$", display: true },
                            { left: "$", right: "$", display: false },
                            { left: "\\(", right: "\\)", display: false },
                            { left: "\\[", right: "\\]", display: true }
                        ],
                        throwOnError: false
                    });
                } catch (_err) {}
            }
        }
        return html;
    };

    window.throttleWithRAF = function (fn) {
        let rafId = null;
        return function (...args) {
            if (rafId !== null) return;
            const context = this;
            rafId = window.requestAnimationFrame(function () {
                rafId = null;
                fn.apply(context, args);
            });
        };
    };

    window.computeWsBackoffDelay = function (attempt) {
        const base = Math.min(10000, 1000 * Math.pow(2, Math.max(0, Number(attempt) || 0)));
        const jitter = Math.floor(Math.random() * 250);
        return Math.min(10000, base + jitter);
    };
})();
