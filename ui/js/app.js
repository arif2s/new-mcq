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
            ADD_TAGS: ['img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'sup', 'sub', 'mark', 'details', 'summary', 'kbd'],
            ADD_ATTR: ['target', 'class', 'style', 'src', 'alt', 'width', 'height', 'data-file', 'data-vault', 'data-filepath', 'data-page', 'data-article-id']
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
        html = html.replace(block.id, renderedMath);
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
window.openInspector = function(title, htmlContent) {
    const sidebar = document.getElementById('right-sidebar');
    const content = document.getElementById('inspector-content');
    if (!sidebar || !content) return;

    // Remove inline width: 0 overrides so the 3rd column pops out cleanly
    sidebar.classList.remove('collapsed');
    sidebar.style.width = '380px';
    sidebar.style.padding = '20px';
    sidebar.style.border = '';
    sidebar.style.overflowY = 'auto';

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
// Unified In-Browser Document Reader (Obsidian & ZIM)
// =========================================================
window.openDocumentReader = function({ title, sourceType, htmlContent, markdownContent, filePath }) {
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
    wrapper.className = 'max-w-4xl mx-auto glass-card p-8 prose max-w-none';

    if (markdownContent !== undefined && markdownContent !== null) {
        window.renderMarkdownAndMath(markdownContent, wrapper);
    } else if (htmlContent !== undefined && htmlContent !== null) {
        const safeHTML = window.DOMPurify
            ? window.DOMPurify.sanitize(htmlContent, {
                ADD_TAGS: ['img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'style'],
                ADD_ATTR: ['class', 'style', 'src', 'href', 'data-article-id', 'data-file']
            })
            : htmlContent;
        wrapper.innerHTML = safeHTML;
        if (window.renderMathInElement) {
            try { window.renderMathInElement(wrapper); } catch (_) {}
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
                    body: JSON.stringify({ file_path: filePath })
                }).catch(() => {
                    window.location.href = `obsidian://open?path=${encodeURIComponent(filePath)}`;
                });
            });
        }, 0);
    }

    contentArea.appendChild(wrapper);
    viewerContainer.style.display = 'flex';
};

// Global helper for opening PDF / Obsidian / ZIM resources from anywhere
window.openLocalResource = async function(type, pathOrId, extraPageOrTitle = 1) {
    if (type === 'pdf') {
        try {
            const res = await fetch('/api/sources/open_pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: pathOrId, page: parseInt(extraPageOrTitle) || 1 })
            });
            const data = await res.json();
            if (data.status !== 'success') console.error('SumatraPDF error:', data.message);
        } catch (err) {
            console.error('Failed to launch PDF:', err);
        }
        return;
    }

    if (type === 'obsidian') {
        const mode = (window.AppState && window.AppState.get().obsidianOpenMode) || 'browser';
        if (mode === 'app') {
            try {
                const res = await fetch('/api/notes/open-obsidian', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: pathOrId })
                });
                const data = await res.json();
                if (data.status !== 'success') {
                    window.location.href = `obsidian://open?path=${encodeURIComponent(pathOrId)}`;
                }
            } catch (_) {
                window.location.href = `obsidian://open?path=${encodeURIComponent(pathOrId)}`;
            }
        } else {
            try {
                const res = await fetch(`/api/notes/obsidian-note?file_path=${encodeURIComponent(pathOrId)}`);
                const data = await res.json();
                if (data.status === 'success') {
                    window.openDocumentReader({
                        title: data.title || extraPageOrTitle || 'Obsidian Note',
                        sourceType: 'obsidian',
                        markdownContent: data.markdown,
                        filePath: data.file_path || pathOrId
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

    if (type === 'zim') {
        try {
            const res = await fetch(`/api/notes/zim-article?article_id=${encodeURIComponent(pathOrId)}`);
            const data = await res.json();
            if (data.status === 'success') {
                window.openDocumentReader({
                    title: data.title || pathOrId,
                    sourceType: 'zim',
                    htmlContent: data.html
                });
            } else {
                alert(data.message || `ZIM article "${pathOrId}" not found.`);
            }
        } catch (err) {
            console.error('Failed to load ZIM article:', err);
        }
    }
};

document.addEventListener('DOMContentLoaded', async () => {
    // Ensure AppState is loaded first so opacity & wallpaper timers are active
    if (window.AppState && window.AppState.fetch) {
        await window.AppState.fetch();
    }

    // Ensure Right Inspector starts collapsed cleanly
    window.closeInspector();
    document.getElementById('close-inspector-btn')?.addEventListener('click', window.closeInspector);

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
        navButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.target === targetId));
        viewSections.forEach(sec => sec.classList.toggle('active', sec.id === targetId));
    };

    navButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const targetId = e.currentTarget.dataset.target;
            switchTab(targetId);
            if (targetId === 'settings-view' && typeof window.refreshDiagnostics === 'function') {
                window.refreshDiagnostics();
            }
            if (targetId === 'knowledge-hub-view' && typeof window.loadSessions === 'function') {
                window.loadSessions();
            }
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

    themeToggleBtn?.addEventListener('click', () => {
        const isLight = body.classList.contains('light-theme');
        const newTheme = isLight ? 'dark-theme' : 'light-theme';
        body.classList.replace(isLight ? 'light-theme' : 'dark-theme', newTheme);
        localStorage.setItem('theme', newTheme);
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
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        canvas.width = 64;
        canvas.height = 64;
        ctx.drawImage(img, 0, 0, 64, 64);

        const regions = [
            { x: 0, y: 0, w: 32, h: 32 },
            { x: 32, y: 0, w: 32, h: 32 },
            { x: 0, y: 32, w: 32, h: 32 },
            { x: 32, y: 32, w: 32, h: 32 }
        ];

        const filteredHues = [];
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
                if (l >= 0.08 && l <= 0.92 && s >= 0.12) filteredHues.push(h);
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
    }

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

    // Standalone MCQ Entity Extractor Tab (#home-view)
    const extractBtn = document.getElementById('extract-btn');
    if (extractBtn) {
        extractBtn.addEventListener('click', async () => {
            const textEl = document.getElementById('mcq-text');
            const chipsContainer = document.getElementById('topics-container');
            const expContainer = document.getElementById('explanation-container');
            if (!textEl || !textEl.value.trim()) return;

            extractBtn.disabled = true;
            extractBtn.textContent = 'Extracting...';
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
                        <span class="topic-chip ${d.verified ? 'border-success-500 underline decoration-success-500 font-semibold' : 'opacity-60'}"
                              data-topic="${window.escapeHTML(d.name)}"
                              title="${d.verified ? 'Verified Medical Topic (Click to view references)' : 'Unverified / Doubtful Topic'}">
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
                                    window.renderMarkdownAndMath(
                                        sData.unified_article || sData.enhanced_explanation || `References resolved for **${topic}**.`,
                                        expContainer
                                    );
                                }
                            }
                        });
                    });
                }
            } catch (e) {
                console.error('Entity extraction failed:', e);
            } finally {
                extractBtn.disabled = false;
                extractBtn.textContent = 'Extract Entities';
            }
        });
    }

    // Global Click Delegation for PDF, Obsidian, and ZIM Citations
    document.addEventListener('click', async (e) => {
        const target = e.target;

        const pdfBtn = target.closest('.citation-pdf');
        if (pdfBtn) {
            e.preventDefault();
            await window.openLocalResource('pdf', pdfBtn.dataset.filepath, pdfBtn.dataset.page || 1);
            return;
        }

        const obsBtn = target.closest('.citation-obsidian');
        if (obsBtn) {
            e.preventDefault();
            const filePath = obsBtn.dataset.filepath || obsBtn.dataset.file;
            await window.openLocalResource('obsidian', filePath, obsBtn.textContent);
            return;
        }

        const zimBtn = target.closest('.citation-zim');
        if (zimBtn) {
            e.preventDefault();
            const articleId = zimBtn.dataset.articleId || zimBtn.textContent.trim();
            await window.openLocalResource('zim', articleId);
            return;
        }

        const closeZimBtn = target.closest('#close-zim-viewer-btn');
        if (closeZimBtn) {
            const viewerContainer = document.getElementById('zim-viewer-container');
            if (viewerContainer) viewerContainer.style.display = 'none';
            const contentArea = document.getElementById('zim-content-area');
            if (contentArea) contentArea.innerHTML = '';
        }
    });
});
