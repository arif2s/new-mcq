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

            chip.addEventListener('click', () => {
                const inspectorContent = document.getElementById('inspector-content');
                if (inspectorContent) {
                    inspectorContent.innerHTML = `<strong>Selected Entity:</strong><br/>${escapeHTML(topic)}<br/><br/>You can perform further actions on this entity here.`;
                }
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
                const res = await fetch(`/api/notes/launch-sumatra?file_path=${encodeURIComponent(filePath)}&page=${page}`);
                const data = await res.json();
                if (data.status !== 'success') console.error('Error launching SumatraPDF:', data.message);
            } catch (err) {
                console.error('Failed to launch PDF:', err);
            }
            return;
        }

        // Handle Obsidian Links (Bug fixed: removed duplicate 'file' declaration)
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
