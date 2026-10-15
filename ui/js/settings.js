// ui/js/settings.js

let cpuModelsMeta = [];
let flashrankModelsMeta = [];

function ensurePhase5SettingsCards() {
    const settingsView = document.getElementById('settings-view');
    if (!settingsView || document.getElementById('phase5-extra-settings')) return;

    const wrapper = settingsView.querySelector('.w-full') || settingsView;
    const saveBtnContainer = document.getElementById('save-settings-btn')?.parentElement;

    const extraContainer = document.createElement('div');
    extraContainer.id = 'phase5-extra-settings';
    extraContainer.className = 'grid-2col mt-20';
    extraContainer.innerHTML = `
        <!-- AI & Obsidian Behavior Card -->
        <div class="card glass-card">
            <h3>AI Synthesis &amp; Note Viewer</h3>

            <div class="form-group mt-5">
                <label class="flex items-center justify-between cursor-pointer">
                    <span>Enable LM Studio Processing (LLM Notes &amp; MCQ Breakdown)</span>
                    <input type="checkbox" id="set-llm-enabled" class="w-5 h-5 accent-brand-500 cursor-pointer" checked />
                </label>
                <p class="text-xs text-muted mt-1">
                    When turned OFF, only verified reference links (PDF, Obsidian, ZIM) are shown in the Right Sidebar without calling LM Studio.
                </p>
            </div>

            <div class="form-group mt-10">
                <label for="set-obsidian-mode">Obsidian Note Click Behavior:</label>
                <select id="set-obsidian-mode" class="glass-select">
                    <option value="browser">Render Note in Browser (LaTeX, Markdown Tables, HTML, Images)</option>
                    <option value="app">Open Directly in Obsidian Desktop App (obsidian://)</option>
                </select>
            </div>

            <div class="form-group mt-10">
                <label for="set-extractor-strategy">Medical Topic Extractor Engine:</label>
                <select id="set-extractor-strategy" class="glass-select">
                    <option value="medspacy">medSpaCy Clinical NER + ZIM Synonym Canonicalizer</option>
                    <option value="regex">Fast Clinical Phrase Regex + ZIM Canonicalizer</option>
                </select>
            </div>
        </div>

        <!-- Background Transparency & Wallpaper Rotation Card -->
        <div class="card glass-card">
            <h3>Appearance, Transparency &amp; Wallpaper</h3>

            <div class="form-group mt-5">
                <div class="flex justify-between items-center mb-1">
                    <label for="set-panel-opacity" class="mb-0">Non-Card Area Opacity (App Frame &amp; Sidebars):</label>
                    <span id="set-panel-opacity-val" class="text-xs font-mono font-bold text-brand-400">78%</span>
                </div>
                <input type="range" id="set-panel-opacity" min="0.15" max="0.95" step="0.02" value="0.78" class="w-full accent-brand-500" />
                <p class="text-xs text-muted mt-1">Lower values make non-card areas more transparent so more of the background wallpaper shines through.</p>
            </div>

            <div class="form-group mt-10">
                <div class="flex justify-between items-center mb-1">
                    <label for="set-card-opacity" class="mb-0">Card Surface Opacity:</label>
                    <span id="set-card-opacity-val" class="text-xs font-mono font-bold text-brand-400">82%</span>
                </div>
                <input type="range" id="set-card-opacity" min="0.40" max="0.98" step="0.02" value="0.82" class="w-full accent-brand-500" />
            </div>

            <div class="form-group mt-10">
                <label for="set-wallpaper-interval">Wallpaper Rotation Duration:</label>
                <div class="flex gap-3 items-center">
                    <select id="set-wallpaper-interval" class="glass-select flex-1">
                        <option value="5">Every 5 Minutes</option>
                        <option value="15">Every 15 Minutes</option>
                        <option value="30">Every 30 Minutes</option>
                        <option value="60">Every 1 Hour</option>
                        <option value="120">Every 2 Hours</option>
                        <option value="360">Every 6 Hours</option>
                        <option value="0">Never (Manual Only)</option>
                    </select>
                    <button type="button" id="change-wallpaper-now-btn" class="secondary-btn whitespace-nowrap px-4 py-3 text-xs">
                        🖼️ Change Now
                    </button>
                </div>
            </div>
        </div>
    `;

    if (saveBtnContainer) {
        wrapper.insertBefore(extraContainer, saveBtnContainer);
    } else {
        wrapper.appendChild(extraContainer);
    }

    // Enhance Model Configuration card with explicit "Download for Offline Use" buttons & status badges
    const embedSelect = document.getElementById('cpu-embed-model-select');
    if (embedSelect && !document.getElementById('download-embed-model-btn')) {
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between gap-3 mt-2';
        row.innerHTML = `
            <span id="embed-model-dl-badge" class="badge bg-warning">Checking cache...</span>
            <button type="button" id="download-embed-model-btn" class="secondary-btn px-3 py-1.5 text-xs">
                ⬇️ Download for Offline Use
            </button>
        `;
        embedSelect.parentElement.appendChild(row);
    }

    const flashSelect = document.getElementById('flashrank-model-select');
    if (flashSelect && !document.getElementById('download-flashrank-model-btn')) {
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between gap-3 mt-2';
        row.innerHTML = `
            <span id="flashrank-model-dl-badge" class="badge bg-warning">Checking cache...</span>
            <button type="button" id="download-flashrank-model-btn" class="secondary-btn px-3 py-1.5 text-xs">
                ⬇️ Download for Offline Use
            </button>
        `;
        flashSelect.parentElement.appendChild(row);
    }

    // Add Offline Vendor Assets diagnostic row to System Diagnostics list
    const diagList = document.getElementById('diagnostics-list');
    if (diagList && !document.getElementById('diag-vendor-assets')) {
        const li = document.createElement('li');
        li.innerHTML = `<span class="diag-label">Offline UI Libraries (Vendor Cache):</span> <span id="diag-vendor-assets" class="badge bg-warning">Checking...</span>`;
        diagList.appendChild(li);
    }
}

function initSettings() {
    ensurePhase5SettingsCards();
    const state = window.AppState ? window.AppState.get() : {};

    const qTargetEl = document.getElementById('set-q-target');
    const cTargetEl = document.getElementById('set-c-target');
    if (qTargetEl && state.dailyTargets) qTargetEl.value = state.dailyTargets.questionsTarget ?? 50;
    if (cTargetEl && state.dailyTargets) cTargetEl.value = state.dailyTargets.correctTarget ?? 40;

    const qLimitEl = document.getElementById('set-q-limit');
    const qhLimitEl = document.getElementById('set-qh-limit');
    if (qLimitEl) qLimitEl.value = state.processingQueueLimit || 10;
    if (qhLimitEl) qhLimitEl.value = state.processingQueueHardLimit || 100;

    const llmToggle = document.getElementById('set-llm-enabled');
    if (llmToggle) llmToggle.checked = state.llmEnabled !== false;

    const obsMode = document.getElementById('set-obsidian-mode');
    if (obsMode) obsMode.value = state.obsidianOpenMode || 'browser';

    const extStrategy = document.getElementById('set-extractor-strategy');
    if (extStrategy) extStrategy.value = state.extractorStrategy || 'medspacy';

    const panelOpacityEl = document.getElementById('set-panel-opacity');
    const panelOpacityVal = document.getElementById('set-panel-opacity-val');
    const pAlpha = typeof state.panelOpacity === 'number' ? state.panelOpacity : 0.78;
    if (panelOpacityEl) panelOpacityEl.value = pAlpha;
    if (panelOpacityVal) panelOpacityVal.textContent = `${Math.round(pAlpha * 100)}%`;

    const cardOpacityEl = document.getElementById('set-card-opacity');
    const cardOpacityVal = document.getElementById('set-card-opacity-val');
    const cAlpha = typeof state.cardOpacity === 'number' ? state.cardOpacity : 0.82;
    if (cardOpacityEl) cardOpacityEl.value = cAlpha;
    if (cardOpacityVal) cardOpacityVal.textContent = `${Math.round(cAlpha * 100)}%`;

    const wpIntervalEl = document.getElementById('set-wallpaper-interval');
    if (wpIntervalEl) {
        const mins = state.wallpaperIntervalMinutes !== undefined ? String(state.wallpaperIntervalMinutes) : '120';
        wpIntervalEl.value = mins;
    }
}

async function saveSettings() {
    const qTarget = parseInt(document.getElementById('set-q-target')?.value) || 50;
    const cTarget = parseInt(document.getElementById('set-c-target')?.value) || 40;
    const qLimit = parseInt(document.getElementById('set-q-limit')?.value) || 10;
    const qhLimit = parseInt(document.getElementById('set-qh-limit')?.value) || 100;

    const llmEnabled = document.getElementById('set-llm-enabled')
        ? document.getElementById('set-llm-enabled').checked
        : true;
    const obsidianOpenMode = document.getElementById('set-obsidian-mode')?.value || 'browser';
    const extractorStrategy = document.getElementById('set-extractor-strategy')?.value || 'medspacy';
    const panelOpacity = parseFloat(document.getElementById('set-panel-opacity')?.value || '0.78');
    const cardOpacity = parseFloat(document.getElementById('set-card-opacity')?.value || '0.82');
    const wallpaperIntervalMinutes = parseInt(document.getElementById('set-wallpaper-interval')?.value ?? '120', 10);

    const updates = {
        dailyTargets: {
            ...(window.AppState.get().dailyTargets || {}),
            questionsTarget: qTarget,
            correctTarget: cTarget
        },
        processingQueueLimit: qLimit,
        processingQueueHardLimit: qhLimit,
        llmEnabled,
        skipLMStudio: !llmEnabled,
        obsidianOpenMode,
        extractorStrategy,
        panelOpacity,
        cardOpacity,
        wallpaperIntervalMinutes: isNaN(wallpaperIntervalMinutes) ? 120 : wallpaperIntervalMinutes
    };

    window.AppState.update(updates);

    const btn = document.getElementById('save-settings-btn');
    if (btn) {
        const orig = btn.textContent;
        btn.textContent = '✓ Settings Saved!';
        setTimeout(() => { btn.textContent = orig; }, 1800);
    }
}

function updateModelBadge(selectId, badgeId, modelsList) {
    const selectEl = document.getElementById(selectId);
    const badgeEl = document.getElementById(badgeId);
    if (!selectEl || !badgeEl) return;

    const selectedModel = modelsList.find(m => m.id === selectEl.value);
    if (selectedModel && selectedModel.downloaded) {
        badgeEl.textContent = '✅ Ready Offline';
        badgeEl.className = 'badge bg-ok';
    } else {
        badgeEl.textContent = '☁️ Not Downloaded (Click to Cache)';
        badgeEl.className = 'badge bg-warning';
    }
}

async function loadModelSelectors() {
    ensurePhase5SettingsCards();

    // 1. CPU Embedding Models
    const embedSelect = document.getElementById('cpu-embed-model-select');
    if (embedSelect) {
        try {
            const res = await fetch('/api/models/cpu_embedding_models');
            const data = await res.json();
            cpuModelsMeta = data.models || [];
            embedSelect.innerHTML = cpuModelsMeta.map(m => `
                <option value="${window.escapeHTML(m.id)}" ${m.id === data.active ? 'selected' : ''}>
                    ${window.escapeHTML(m.name)} ${m.downloaded ? '(Offline Ready)' : ''}
                </option>
            `).join('');
            updateModelBadge('cpu-embed-model-select', 'embed-model-dl-badge', cpuModelsMeta);
        } catch (_) {
            embedSelect.innerHTML = '<option value="Xenova/all-MiniLM-L6-v2">all-MiniLM-L6-v2 (Default)</option>';
        }

        embedSelect.onchange = async (e) => {
            updateModelBadge('cpu-embed-model-select', 'embed-model-dl-badge', cpuModelsMeta);
            await fetch('/api/models/set_embedding_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_name: e.target.value })
            });
        };
    }

    // 2. FlashRank Reranker Models
    const flashSelect = document.getElementById('flashrank-model-select');
    if (flashSelect) {
        try {
            const res = await fetch('/api/models/flashrank_models');
            const data = await res.json();
            flashrankModelsMeta = data.models || [];
            flashSelect.innerHTML = flashrankModelsMeta.map(m => `
                <option value="${window.escapeHTML(m.id)}" ${m.id === data.active ? 'selected' : ''}>
                    ${window.escapeHTML(m.name)} ${m.downloaded ? '(Offline Ready)' : ''}
                </option>
            `).join('');
            updateModelBadge('flashrank-model-select', 'flashrank-model-dl-badge', flashrankModelsMeta);
        } catch (_) {
            flashSelect.innerHTML = '<option value="ms-marco-TinyBERT-L-2-v2">ms-marco-TinyBERT-L-2-v2 (Default)</option>';
        }

        flashSelect.onchange = async (e) => {
            updateModelBadge('flashrank-model-select', 'flashrank-model-dl-badge', flashrankModelsMeta);
            await fetch('/api/models/set_flashrank_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model_name: e.target.value })
            });
        };
    }
}

async function triggerModelDownload(modelType, selectId, badgeId, btnId) {
    const selectEl = document.getElementById(selectId);
    const badgeEl = document.getElementById(badgeId);
    const btnEl = document.getElementById(btnId);
    if (!selectEl || !selectEl.value) return;

    const modelName = selectEl.value;
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.textContent = '⏳ Downloading...';
    }
    if (badgeEl) {
        badgeEl.textContent = 'Downloading weights...';
        badgeEl.className = 'badge bg-warning';
    }

    try {
        const res = await fetch('/api/models/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model_type: modelType, model_name: modelName })
        });
        const data = await res.json();
        if (data.status === 'success' || data.downloaded) {
            await loadModelSelectors();
            if (badgeEl) {
                badgeEl.textContent = '✅ Ready Offline';
                badgeEl.className = 'badge bg-ok';
            }
        } else {
            if (badgeEl) {
                badgeEl.textContent = `❌ Error: ${data.message || 'Offline'}`;
                badgeEl.className = 'badge bg-warning';
            }
        }
    } catch (e) {
        if (badgeEl) {
            badgeEl.textContent = '❌ Network Unreachable';
            badgeEl.className = 'badge bg-warning';
        }
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.textContent = '⬇️ Download for Offline Use';
        }
    }
}

function handleExport() {
    if (window.AppState && window.AppState.exportData) {
        window.AppState.exportData();
    }
}

function handleImport(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
        if (window.AppState && window.AppState.importData) {
            const success = await window.AppState.importData(e.target.result);
            if (success) {
                alert('Backup imported successfully!');
                initSettings();
            } else {
                alert('Failed to import backup. Please verify the JSON file.');
            }
        }
        event.target.value = '';
    };
    reader.readAsText(file);
}

document.addEventListener('stateUpdated', initSettings);
document.addEventListener('DOMContentLoaded', () => {
    initSettings();
    loadModelSelectors();

    document.getElementById('save-settings-btn')?.addEventListener('click', saveSettings);

    // Live transparency sliders
    document.getElementById('set-panel-opacity')?.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        const label = document.getElementById('set-panel-opacity-val');
        if (label) label.textContent = `${Math.round(val * 100)}%`;
        if (window.AppState) window.AppState.applyAppearance({ ...window.AppState.get(), panelOpacity: val });
    });

    document.getElementById('set-card-opacity')?.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        const label = document.getElementById('set-card-opacity-val');
        if (label) label.textContent = `${Math.round(val * 100)}%`;
        if (window.AppState) window.AppState.applyAppearance({ ...window.AppState.get(), cardOpacity: val });
    });

    // Change Wallpaper Now button
    document.getElementById('change-wallpaper-now-btn')?.addEventListener('click', () => {
        if (typeof window.changeWallpaperNow === 'function') {
            window.changeWallpaperNow();
        }
    });

    // On-Demand Offline Model Download buttons
    document.getElementById('download-embed-model-btn')?.addEventListener('click', () => {
        triggerModelDownload('embedding', 'cpu-embed-model-select', 'embed-model-dl-badge', 'download-embed-model-btn');
    });

    document.getElementById('download-flashrank-model-btn')?.addEventListener('click', () => {
        triggerModelDownload('flashrank', 'flashrank-model-select', 'flashrank-model-dl-badge', 'download-flashrank-model-btn');
    });

    // Backup & Restore
    document.getElementById('export-data-btn')?.addEventListener('click', handleExport);
    document.getElementById('import-data-file')?.addEventListener('change', handleImport);
    document.getElementById('import-data-btn')?.addEventListener('click', () => {
        document.getElementById('import-data-file')?.click();
    });
});
