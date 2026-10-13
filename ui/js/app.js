window.escapeHTML = function(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
};

document.addEventListener('DOMContentLoaded', () => {
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

    // Theme Toggle & Background Engine
    const themeToggleBtn = document.getElementById('theme-toggle');
    const body = document.body;
    let currentBgIndex = 1;
    const TOTAL_BGS = 4; // Cycles through bg1.jpg to bg4.jpg

    const updateThemeAndBackground = () => {
        const theme = body.classList.contains('dark-theme') ? 'dark' : 'light';
        // Simulate grabbing from structured light/dark folders
        const imgUrl = `/img/${theme}/bg${currentBgIndex}.jpg`;

        const img = new Image();
        img.crossOrigin = "Anonymous";
        img.onload = () => {
            document.querySelector('.background-animation').style.backgroundImage = `url(${imgUrl})`;
            extractAndApplyPalette(img, theme);
        };
        // Fallback to default if dynamic images are missing in user's directory
        img.onerror = () => {
            document.querySelector('.background-animation').style.backgroundImage = `url(/img/bg-${theme}.jpg)`;
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

    setInterval(() => {
        currentBgIndex = (currentBgIndex % TOTAL_BGS) + 1;
        updateThemeAndBackground();
    }, 60000); // Rotate background every 60 seconds

    // Init saved theme
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) body.className = savedTheme;
    updateThemeAndBackground();

    // --- Color Extraction Algorithms ---
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
            r = hue2rgb(p, q, h + 1/3);
            g = hue2rgb(p, q, h);
            b = hue2rgb(p, q, h - 1/3);
        }
        return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
    }

    function rgbToHsl(r, g, b) {
        r /= 255; g /= 255; b /= 255;
        const max = Math.max(r, g, b), min = Math.min(r, g, b);
        let h, s, l = (max + min) / 2;
        if (max === min) h = s = 0;
        else {
            const d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            switch (max) {
                case r: h = (g - b) / d + (g < b ? 6 : 0); break;
                case g: h = (b - r) / d + 2; break;
                case b: h = (r - g) / d + 4; break;
            }
            h /= 6;
        }
        return [h, s, l];
    }

    function extractAndApplyPalette(img, theme) {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        canvas.width = img.width; canvas.height = img.height;
        ctx.drawImage(img, 0, 0);

        // Define 4 quadrants for 4 primary UI shade variables
        const regions = [
            { x: 0, y: 0, w: canvas.width/2, h: canvas.height/2 },
            { x: canvas.width/2, y: 0, w: canvas.width/2, h: canvas.height/2 },
            { x: 0, y: canvas.height/2, w: canvas.width/2, h: canvas.height/2 },
            { x: canvas.width/2, y: canvas.height/2, w: canvas.width/2, h: canvas.height/2 }
        ];

        regions.forEach((region, i) => {
            const data = ctx.getImageData(region.x, region.y, region.w, region.h).data;
            let r = 0, g = 0, b = 0, count = 0;
            // Sample every 16th pixel for performance
            for (let j = 0; j < data.length; j += 64) {
                r += data[j]; g += data[j+1]; b += data[j+2]; count++;
            }
            if(count === 0) return;
            r = Math.round(r / count); g = Math.round(g / count); b = Math.round(b / count);

            // Algorithmic Aesthetic Normalization
            let [h, s, l] = rgbToHsl(r, g, b);

            // Ensure minimum saturation so UI isn't fully grey
            s = Math.max(s, 0.25);

            // Constrain lightness for text contrast mapping
            l = theme === 'dark'
                ? Math.max(0.1, Math.min(l, 0.25)) // Keep dark themes dark
                : Math.max(0.75, Math.min(l, 0.9)); // Keep light themes light

            const [fr, fg, fb] = hslToRgb(h, s, l);

            // Export to CSS variables
            document.documentElement.style.setProperty(`--card-color-${i+1}`, `${fr}, ${fg}, ${fb}`);

            // Calculate luminance for WCAG contrast text colors
            const brightness = (fr * 299 + fg * 587 + fb * 114) / 1000;
            const textColor = brightness > 128 ? '#111827' : '#F9FAFB'; // Dark gray vs White
            document.documentElement.style.setProperty(`--text-on-card-${i+1}`, textColor);
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
