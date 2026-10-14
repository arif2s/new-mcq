window.escapeHTML = function(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
};

document.addEventListener('DOMContentLoaded', async () => {
    // --- Navigation & Core App Logic ---

    // Sidebar Collapse
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.left-rail');
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
        });
    }

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
        });
    });

    // --- Theme Toggle & Wallpaper Rotation Engine ---
    const WALLPAPER_ROTATION_MS = 2 * 60 * 60 * 1000; // 2 hours
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

    function getNextWallpaperUrl(theme) {
        const list = wallpapersCache[theme] || [];
        const now = Date.now();
        let lastChange = parseInt(localStorage.getItem('lastWallpaperChangeTimestamp') || '0', 10);
        let bgIndex = parseInt(localStorage.getItem('currentBgIndex') || '0', 10);

        if (!lastChange) {
            lastChange = now;
            bgIndex = 0;
            localStorage.setItem('lastWallpaperChangeTimestamp', lastChange.toString());
            localStorage.setItem('currentBgIndex', bgIndex.toString());
        } else if (now - lastChange >= WALLPAPER_ROTATION_MS) {
            bgIndex = bgIndex + 1;
            lastChange = now;
            localStorage.setItem('lastWallpaperChangeTimestamp', lastChange.toString());
            localStorage.setItem('currentBgIndex', bgIndex.toString());
        }

        if (list && list.length > 0) {
            const filename = list[bgIndex % list.length];
            return `/img/${theme}/${filename}`;
        }
        return `/img/bg-${theme}.jpg`;
    }

    const updateThemeAndBackground = () => {
        const theme = body.classList.contains('dark-theme') ? 'dark' : 'light';
        const imgUrl = getNextWallpaperUrl(theme);

        const img = new Image();
        img.crossOrigin = "Anonymous";
        img.onload = () => {
            const bgEl = document.querySelector('.background-animation');
            if (bgEl) {
                bgEl.style.backgroundImage = `url(${imgUrl})`;
            }
            extractAndApplyPalette(img, theme);
        };
        img.onerror = () => {
            const fallbackUrl = `/img/bg-${theme}.jpg`;
            const bgEl = document.querySelector('.background-animation');
            if (bgEl) {
                bgEl.style.backgroundImage = `url(${fallbackUrl})`;
            }
            const fallbackImg = new Image();
            fallbackImg.crossOrigin = "Anonymous";
            fallbackImg.onload = () => extractAndApplyPalette(fallbackImg, theme);
            fallbackImg.src = fallbackUrl;
        };
        img.src = imgUrl;
    };

    themeToggleBtn?.addEventListener('click', () => {
        const isLight = body.classList.contains('light-theme');
        const newTheme = isLight ? 'dark-theme' : 'light-theme';
        body.classList.replace(isLight ? 'light-theme' : 'dark-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        updateThemeAndBackground();
    });

    // Check periodically (every 10 seconds) if 2 hours have elapsed
    setInterval(() => {
        const lastChange = parseInt(localStorage.getItem('lastWallpaperChangeTimestamp') || '0', 10);
        if (lastChange && (Date.now() - lastChange >= WALLPAPER_ROTATION_MS)) {
            updateThemeAndBackground();
        }
    }, 10000);

    // Initial load
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) body.className = savedTheme;
    await fetchWallpapersList();
    updateThemeAndBackground();

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
        const maxL = Math.max(l1, l2);
        const minL = Math.min(l1, l2);
        return (maxL + 0.05) / (minL + 0.05);
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
                const r = imgData[p];
                const g = imgData[p + 1];
                const b = imgData[p + 2];
                sumR += r;
                sumG += g;
                sumB += b;

                const [h, s, l] = rgbToHsl(r, g, b);
                pixelsWithLum.push({ r, g, b, l, h, s });

                // Filter: Ignore extreme black (L < 0.08), extreme white (L > 0.92), and near-grey (S < 0.12)
                if (l >= 0.08 && l <= 0.92 && s >= 0.12) {
                    filteredHues.push(h);
                }
            }

            const avgBgR = Math.round(sumR / totalPixels);
            const avgBgG = Math.round(sumG / totalPixels);
            const avgBgB = Math.round(sumB / totalPixels);

            pixelsWithLum.sort((a, b) => a.l - b.l);
            const tenthIndex = Math.max(1, Math.floor(totalPixels * 0.10));

            let darkSumR = 0, darkSumG = 0, darkSumB = 0;
            for (let k = 0; k < tenthIndex; k++) {
                darkSumR += pixelsWithLum[k].r;
                darkSumG += pixelsWithLum[k].g;
                darkSumB += pixelsWithLum[k].b;
            }
            const darkBgR = Math.round(darkSumR / tenthIndex);
            const darkBgG = Math.round(darkSumG / tenthIndex);
            const darkBgB = Math.round(darkSumB / tenthIndex);

            let brightSumR = 0, brightSumG = 0, brightSumB = 0;
            const startIndex = totalPixels - tenthIndex;
            for (let k = startIndex; k < totalPixels; k++) {
                brightSumR += pixelsWithLum[k].r;
                brightSumG += pixelsWithLum[k].g;
                brightSumB += pixelsWithLum[k].b;
            }
            const brightBgR = Math.round(brightSumR / tenthIndex);
            const brightBgG = Math.round(brightSumG / tenthIndex);
            const brightBgB = Math.round(brightSumB / tenthIndex);

            quadrantDataList.push({
                avgBg: [avgBgR, avgBgG, avgBgB],
                darkBg: [darkBgR, darkBgG, darkBgB],
                brightBg: [brightBgR, brightBgG, brightBgB]
            });
        });

        // Dominant Hue Extraction
        let domHue = 220; // Default hue
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

        // Harmonic Shifts: Shade 1: H, Shade 2: H+15, Shade 3: H-15, Shade 4: H+30
        const hueShifts = [0, 15, -15, 30];

        quadrantDataList.forEach((qData, i) => {
            let h = (domHue + hueShifts[i] + 360) % 360;

            // Clamping Saturation: 0.25-0.50 (Dark) vs 0.20-0.45 (Light)
            let s = (theme === 'dark') ? 0.35 : 0.30;
            // Clamping Lightness: 0.12-0.22 (Dark) vs 0.82-0.92 (Light)
            let l = (theme === 'dark') ? 0.17 : 0.87;

            const [rCard, gCard, bCard] = hslToRgb(h, s, l);
            document.documentElement.style.setProperty(`--card-color-${i + 1}`, `${rCard}, ${gCard}, ${bCard}`);

            // Step 4A — Alpha-Blended Effective RGB (alpha = 0.78)
            const alpha = 0.78;
            const blend = (cardC, bgC) => Math.round(alpha * cardC + (1 - alpha) * bgC);

            const effAvg = [
                blend(rCard, qData.avgBg[0]),
                blend(gCard, qData.avgBg[1]),
                blend(bCard, qData.avgBg[2])
            ];
            const effDark = [
                blend(rCard, qData.darkBg[0]),
                blend(gCard, qData.darkBg[1]),
                blend(bCard, qData.darkBg[2])
            ];
            const effBright = [
                blend(rCard, qData.brightBg[0]),
                blend(gCard, qData.brightBg[1]),
                blend(bCard, qData.brightBg[2])
            ];

            // Step 4B — Relative Luminance Calculations
            const lumAvg = getRelativeLuminance(effAvg[0], effAvg[1], effAvg[2]);
            const lumDark = getRelativeLuminance(effDark[0], effDark[1], effDark[2]);
            const lumBright = getRelativeLuminance(effBright[0], effBright[1], effBright[2]);

            // Candidate Primary Text
            const darkTextLum = getRelativeLuminance(15, 23, 42);   // #0F172A
            const lightTextLum = getRelativeLuminance(248, 250, 252); // #F8FAFC

            const crDarkTextAvg = getContrastRatio(darkTextLum, lumAvg);
            const crDarkTextDark = getContrastRatio(darkTextLum, lumDark);
            const crDarkTextBright = getContrastRatio(darkTextLum, lumBright);
            const minCrDarkText = Math.min(crDarkTextAvg, crDarkTextDark, crDarkTextBright);

            const crLightTextAvg = getContrastRatio(lightTextLum, lumAvg);
            const crLightTextDark = getContrastRatio(lightTextLum, lumDark);
            const crLightTextBright = getContrastRatio(lightTextLum, lumBright);
            const minCrLightText = Math.min(crLightTextAvg, crLightTextDark, crLightTextBright);

            // Step 4C — Variable Assignment
            if (minCrLightText >= minCrDarkText) {
                // Light text
                document.documentElement.style.setProperty(`--text-on-card-${i + 1}`, '#F8FAFC');
                document.documentElement.style.setProperty(`--text-muted-on-card-${i + 1}`, '#CBD5E1');
                document.documentElement.style.setProperty(`--card-text-shadow-${i + 1}`, '0 1px 2px rgba(0, 0, 0, 0.35)');
            } else {
                // Dark text
                document.documentElement.style.setProperty(`--text-on-card-${i + 1}`, '#0F172A');
                document.documentElement.style.setProperty(`--text-muted-on-card-${i + 1}`, '#334155');
                document.documentElement.style.setProperty(`--card-text-shadow-${i + 1}`, '0 1px 1px rgba(255, 255, 255, 0.5)');
            }
        });
    }

    // ==========================================
    // 4. GLOBAL EVENT DELEGATION (CITATIONS & MODALS)
    // ==========================================

    // Help Modal Logic
    const helpModal = document.getElementById('help-modal');
    document.getElementById('open-help-btn')?.addEventListener('click', () => {
        if(helpModal) helpModal.style.display = 'flex';
    });
    document.getElementById('close-help-btn')?.addEventListener('click', () => {
        if(helpModal) helpModal.style.display = 'none';
    });
    helpModal?.addEventListener('click', (e) => {
        if (e.target === helpModal) helpModal.style.display = 'none';
    });

    // Global Citation Click Handler (Makes PDF, Obsidian, ZIM links work anywhere)
    document.addEventListener('click', async (e) => {
        const target = e.target;

        // 1. SumatraPDF Launcher
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

        // 2. Obsidian Deep Linker
        const obsBtn = target.closest('.citation-obsidian');
        if (obsBtn) {
            e.preventDefault();
            const vault = obsBtn.dataset.vault || 'Primary';
            const file = obsBtn.dataset.file;
            window.location.href = `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
            return;
        }

        // 3. Local ZIM Article Viewer
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
                        const safeHTML = window.DOMPurify ? window.DOMPurify.sanitize(data.html) : window.escapeHTML(data.html);
                        contentArea.innerHTML = safeHTML;
                        viewerContainer.style.display = 'flex';
                    }
                }
            } catch (err) {
                console.error('Failed to load ZIM article:', err);
            }
            return;
        }

        // 4. Close ZIM Viewer
        const closeZimBtn = target.closest('#close-zim-viewer-btn');
        if (closeZimBtn) {
            const viewerContainer = document.getElementById('zim-viewer-container');
            if (viewerContainer) viewerContainer.style.display = 'none';
            const contentArea = document.getElementById('zim-content-area');
            if (contentArea) contentArea.innerHTML = '';
        }
    });

    // ==========================================
    // 5. SETTINGS DATA LOADERS
    // ==========================================

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

    // Load dynamic models into the settings pane
    loadModels('/api/models/cpu_embedding_models', 'cpu-embed-model-select', '/api/models/set_embedding_model');
    loadModels('/api/models/flashrank_models', 'flashrank-model-select', '/api/models/set_flashrank_model');
});
