document.addEventListener('DOMContentLoaded', () => {
    // Navigation logic (Tab Switching)
    const navButtons = document.querySelectorAll('.nav-btn');
    const viewSections = document.querySelectorAll('.view-section');

    function switchTab(targetId) {
        // Remove active class from all buttons and sections
        navButtons.forEach(btn => btn.classList.remove('active'));
        viewSections.forEach(sec => sec.classList.remove('active'));

        // Add active class to target
        const targetBtn = document.querySelector(`.nav-btn[data-target="${targetId}"]`);
        const targetSec = document.getElementById(targetId);

        if (targetBtn) targetBtn.classList.add('active');
        if (targetSec) targetSec.classList.add('active');
    }

    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-target');
            switchTab(targetId);

            // If switching to settings, refresh diagnostics automatically
            if(targetId === 'settings-view' && window.refreshDiagnostics) {
                window.refreshDiagnostics();
            }
        });
    });

    // Theme Toggle Logic
    const themeToggleBtn = document.getElementById('theme-toggle');
    const body = document.body;

    // Check local storage for theme preference
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) {
        body.className = savedTheme;
    } else {
        // Default to dark if user prefers dark mode OS wide
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            body.className = 'dark-theme';
        }
    }

    themeToggleBtn.addEventListener('click', () => {
        if (body.classList.contains('light-theme')) {
            body.classList.replace('light-theme', 'dark-theme');
            localStorage.setItem('theme', 'dark-theme');
        } else {
            body.classList.replace('dark-theme', 'light-theme');
            localStorage.setItem('theme', 'light-theme');
        }
    });

    // Extraction Logic
    const extractBtn = document.getElementById('extract-btn');
    const mcqTextEl = document.getElementById('mcq-text');
    const useZimEl = document.getElementById('use-zim-refinement');
    const useTantivyEl = document.getElementById('use-tantivy-refinement');
    const topicsContainer = document.getElementById('topics-container');

    if (extractBtn) {
        extractBtn.addEventListener('click', async () => {
            const mcqText = mcqTextEl.value.trim();
            const useZimRefinement = useZimEl.checked;
            const useTantivyRefinement = useTantivyEl.checked;

            if (!mcqText) {
                alert('Please enter some MCQ text.');
                return;
            }

            // Show loading state
            extractBtn.disabled = true;
            extractBtn.textContent = 'Extracting...';
            topicsContainer.innerHTML = '';

            try {
                const response = await fetch('/api/mcq/topics/extract', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        mcq_text: mcqText,
                        use_zim_refinement: useZimRefinement,
                        use_tantivy_refinement: useTantivyRefinement
                    })
                });

                if (!response.ok) {
                    throw new Error(`Server error: ${response.statusText}`);
                }

                const data = await response.json();
                const topics = data.topics || [];

                renderTopics(topics);
            } catch (error) {
                console.error('Error extracting topics:', error);
                alert('Failed to extract topics. Please check the console for details.');
            } finally {
                extractBtn.disabled = false;
                extractBtn.textContent = 'Extract Entities';
            }
        });
    }

    function renderTopics(topics) {
        if (topics.length === 0) {
            const emptyMsg = document.createElement('div');
            emptyMsg.textContent = 'No entities found.';
            emptyMsg.className = 'text-muted';
            topicsContainer.appendChild(emptyMsg);
            return;
        }

        topics.forEach(topic => {
            const chip = document.createElement('div');
            chip.className = 'topic-chip';
            chip.textContent = topic;

            // Add click interaction for validation
            chip.addEventListener('click', () => {
                const inspectorContent = document.getElementById('inspector-content');
                if (inspectorContent) {
                    inspectorContent.innerHTML = `<strong>Selected Entity:</strong><br/>${topic}<br/><br/>You can perform further actions on this entity here.`;
                }
            });

            topicsContainer.appendChild(chip);
        });
    }

    // --- Application Bridging Logic ---
    document.addEventListener('click', async (e) => {
        // Handle PDF Citations
        if (e.target.classList.contains('citation-pdf')) {
            e.preventDefault();
            const filePath = e.target.getAttribute('data-filepath');
            const page = e.target.getAttribute('data-page') || 1;
            try {
                const res = await fetch(`/api/notes/launch-sumatra?file_path=${encodeURIComponent(filePath)}&page=${page}`);
                const data = await res.json();
                if (data.status !== 'success') {
                    console.error('Error launching SumatraPDF:', data.message);
                }
            } catch (err) {
                console.error('Failed to launch PDF:', err);
            }
        }

        // Handle Obsidian Links
        if (e.target.classList.contains('citation-obsidian')) {
            e.preventDefault();
            const vault = e.target.getAttribute('data-vault');
            const file = e.target.getAttribute('data-file');
            window.location.href = `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
        }

        // Handle ZIM Articles
        if (e.target.classList.contains('citation-zim')) {
            e.preventDefault();
            const articleId = e.target.getAttribute('data-article-id');
            try {
                const res = await fetch(`/api/notes/zim-article?article_id=${encodeURIComponent(articleId)}`);
                const data = await res.json();
                if (data.status === 'success') {
                    const viewerContainer = document.getElementById('zim-viewer-container');
                    const contentArea = document.getElementById('zim-content-area');
                    if (viewerContainer && contentArea) {
                        contentArea.innerHTML = data.html;
                        viewerContainer.style.display = 'flex';
                    }
                } else {
                    console.error('Error loading ZIM article:', data.message);
                }
            } catch (err) {
                console.error('Failed to load ZIM article:', err);
            }
        }
    });

    const closeZimBtn = document.getElementById('close-zim-viewer-btn');
    if (closeZimBtn) {
        closeZimBtn.addEventListener('click', () => {
            const viewerContainer = document.getElementById('zim-viewer-container');
            if (viewerContainer) viewerContainer.style.display = 'none';
        });
    }

    // --- Settings / Diagnostics Data Loaders ---
    // (Models dropdowns)
    const cpuEmbedSelect = document.getElementById('cpu-embed-model-select');
    if (cpuEmbedSelect) {
        fetch('/api/models/cpu_embedding_models')
            .then(res => res.json())
            .then(data => {
                cpuEmbedSelect.innerHTML = '';
                data.models.forEach(model => {
                    const opt = document.createElement('option');
                    opt.value = model.id;
                    opt.textContent = model.name;
                    if (model.id === data.active) opt.selected = true;
                    cpuEmbedSelect.appendChild(opt);
                });
            })
            .catch(err => {
                cpuEmbedSelect.innerHTML = '<option value="">Error loading models</option>';
            });

        cpuEmbedSelect.addEventListener('change', (e) => {
            fetch('/api/models/set_embedding_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_name: e.target.value })
            });
        });
    }

    const flashrankSelect = document.getElementById('flashrank-model-select');
    if (flashrankSelect) {
        fetch('/api/models/flashrank_models')
            .then(res => res.json())
            .then(data => {
                flashrankSelect.innerHTML = '';
                data.models.forEach(model => {
                    const opt = document.createElement('option');
                    opt.value = model.id;
                    opt.textContent = model.name;
                    if (model.id === data.active) opt.selected = true;
                    flashrankSelect.appendChild(opt);
                });
            })
            .catch(err => {
                flashrankSelect.innerHTML = '<option value="">Error loading models</option>';
            });

        flashrankSelect.addEventListener('change', (e) => {
            fetch('/api/models/set_flashrank_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_name: e.target.value })
            });
        });
    }
});