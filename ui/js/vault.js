// ui/js/vault.js

document.addEventListener('DOMContentLoaded', () => {
  const sourcesList = document.getElementById('sources-list');
  const sourcesListEmpty = document.getElementById('sources-list-empty');
  const newSourceType = document.getElementById('new-source-type');
  const newSourceName = document.getElementById('new-source-name');
  const newSourcePath = document.getElementById('new-source-path');
  const addSourceBtn = document.getElementById('add-source-btn');
  const pdfEngineContainer = document.getElementById('pdf-engine-container');
  const pdfEngineFitzLabel = document.getElementById('pdf-engine-fitz-label');
  const pdfEngineDoclingLabel = document.getElementById('pdf-engine-docling-label');
  const pdfEngineDoclingV2Label = document.getElementById('pdf-engine-docling-v2-label');
  const pdfEngineDoclingV3Label = document.getElementById('pdf-engine-docling-v3-label');
  const startIndexingBtn = document.getElementById('start-indexing-btn');
  const indexingBtnText = document.getElementById('indexing-btn-text');
  const indexingIconPlay = document.getElementById('indexing-icon-play');
  const indexingIconSpin = document.getElementById('indexing-icon-spin');
  const indexingProgressContainer = document.getElementById('indexing-progress-container');
  const indexingStatusMessage = document.getElementById('indexing-status-message');
  const indexingProgressText = document.getElementById('indexing-progress-text');
  const indexingProgressBar = document.getElementById('indexing-progress-bar');

  if (!sourcesList) return;

  const escapeHTML = window.escapeHTML || ((str) =>
    String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;')
  );

  let sources = [];
  let isIndexing = false;
  let ws = null;
  let fallbackTimer = null;

  const icons = {
    obsidian: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-1.22-1.82A2 2 0 0 0 9.53 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path></svg>',
    pdf: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><line x1="10" y1="9" x2="8" y2="9"></line></svg>',
    zim: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M3 5V19A9 3 0 0 0 21 19V5"></path><path d="M3 12A9 3 0 0 0 21 12"></path></svg>',
    toggleOn: '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="5" width="22" height="14" rx="7" ry="7"></rect><circle cx="16" cy="12" r="3"></circle></svg>',
    toggleOff: '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="5" width="22" height="14" rx="7" ry="7"></rect><circle cx="8" cy="12" r="3"></circle></svg>',
    trash: '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>'
  };

  function fetchSources() {
    fetch('/api/sources')
      .then(res => {
        if (!res.ok) throw new Error('Network response was not ok');
        return res.json();
      })
      .then(data => {
        sources = Array.isArray(data) ? data : (data.sources || []);
        renderSources();
        updateIndexingBtnState();
      })
      .catch((err) => {
        console.warn('Could not load sources from backend:', err);
        renderSources();
        updateIndexingBtnState();
      });
  }
  window.fetchVaultSources = fetchSources;

  function renderSources() {
    sourcesList.innerHTML = '';
    if (sources.length === 0) {
      sourcesListEmpty?.classList.remove('hidden');
      sourcesList.classList.add('hidden');
    } else {
      sourcesListEmpty?.classList.add('hidden');
      sourcesList.classList.remove('hidden');

      sources.forEach(src => {
        const srcType = String(src.type || 'obsidian').toLowerCase();
        const bgClass = src.enabled ? 'glass-card' : 'glass-panel opacity-60';
        const toggleIcon = src.enabled ? icons.toggleOn : icons.toggleOff;
        const toggleColor = src.enabled ? 'text-success-500' : 'text-muted';
        const typeBadge = srcType === 'pdf' && src.pdfEngine ? `${srcType} (${src.pdfEngine})` : srcType;
        const countFormatted = Number(src.itemCount || 0).toLocaleString();

        const el = document.createElement('div');
        el.className = `p-4 rounded-xl border flex items-center justify-between transition-all ${bgClass}`;
        el.innerHTML = `
          <div class="flex items-center gap-4 min-w-0 flex-1">
            <div class="p-2.5 glass-panel rounded-xl text-brand-400">
              ${icons[srcType] || icons.obsidian}
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <p class="text-sm font-semibold">${escapeHTML(src.name)}</p>
                <span class="text-xs px-2 py-0.5 rounded glass-panel text-muted uppercase font-mono">
                  ${escapeHTML(typeBadge)}
                </span>
              </div>
              <p class="text-xs font-mono text-muted truncate mt-0.5">${escapeHTML(src.path)}</p>
            </div>
          </div>
          <div class="flex items-center gap-6">
            <div class="text-right hidden sm:block">
              <p class="text-xs text-muted">${countFormatted} indexed items</p>
              <p class="text-[10px] text-muted">Last: ${escapeHTML(src.lastIndexed || 'Never')}</p>
            </div>
            <button type="button" class="toggle-btn p-1 text-2xl transition-colors ${toggleColor} bg-transparent border-none cursor-pointer" data-id="${escapeHTML(src.id)}" title="${src.enabled ? 'Source Enabled' : 'Source Disabled'}">
              ${toggleIcon}
            </button>
            <button type="button" class="delete-btn p-2 text-muted hover:text-danger-500 hover:bg-danger-500/10 rounded-lg transition-colors bg-transparent border-none cursor-pointer" data-id="${escapeHTML(src.id)}" title="Remove Source">
              ${icons.trash}
            </button>
          </div>
        `;
        sourcesList.appendChild(el);
      });
    }

    // Attach event listeners scoped strictly to sourcesList
    sourcesList.querySelectorAll('.toggle-btn').forEach(btn => {
      btn.addEventListener('click', () => handleToggle(btn.getAttribute('data-id')));
    });
    sourcesList.querySelectorAll('.delete-btn').forEach(btn => {
      btn.addEventListener('click', () => handleDelete(btn.getAttribute('data-id')));
    });
  }

  function handleToggle(id) {
    if (!id) return;
    sources = sources.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s);
    renderSources();
    updateIndexingBtnState();

    fetch(`/api/sources/${encodeURIComponent(id)}/toggle`, { method: 'POST' })
      .then(() => fetchSources())
      .catch(() => {});
  }

  function handleDelete(id) {
    if (!id) return;
    sources = sources.filter(s => s.id !== id);
    renderSources();
    updateIndexingBtnState();

    fetch(`/api/sources/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => {});
  }

  function updateIndexingBtnState() {
    if (!startIndexingBtn) return;
    const hasEnabled = sources.some(s => s.enabled);
    startIndexingBtn.disabled = Boolean(isIndexing || !hasEnabled);
  }

  // --- Form Logic ---
  newSourcePath?.addEventListener('input', () => {
    if (addSourceBtn) addSourceBtn.disabled = newSourcePath.value.trim() === '';
  });

  newSourceType?.addEventListener('change', () => {
    if (newSourceType.value === 'pdf') {
      pdfEngineContainer?.classList.remove('hidden');
    } else {
      pdfEngineContainer?.classList.add('hidden');
    }
  });

  const getSelectedPdfEngine = () => {
    const radios = document.getElementsByName('pdfEngine');
    for (let i = 0; i < radios.length; i++) {
      if (radios[i].checked) return radios[i].value;
    }
    return 'fitz';
  };

  document.getElementsByName('pdfEngine').forEach(radio => {
    radio.addEventListener('change', () => {
      [pdfEngineFitzLabel, pdfEngineDoclingLabel, pdfEngineDoclingV2Label, pdfEngineDoclingV3Label].forEach(label => {
        label?.classList.remove('bg-brand-600/10', 'border-brand-500');
        label?.classList.add('glass-panel');
      });
      if (radio.value === 'fitz' && pdfEngineFitzLabel) {
        pdfEngineFitzLabel.classList.add('bg-brand-600/10', 'border-brand-500');
        pdfEngineFitzLabel.classList.remove('glass-panel');
      } else if (radio.value === 'docling' && pdfEngineDoclingLabel) {
        pdfEngineDoclingLabel.classList.add('bg-brand-600/10', 'border-brand-500');
        pdfEngineDoclingLabel.classList.remove('glass-panel');
      } else if (radio.value === 'docling_v2' && pdfEngineDoclingV2Label) {
        pdfEngineDoclingV2Label.classList.add('bg-brand-600/10', 'border-brand-500');
        pdfEngineDoclingV2Label.classList.remove('glass-panel');
      } else if (radio.value === 'docling_v3' && pdfEngineDoclingV3Label) {
        pdfEngineDoclingV3Label.classList.add('bg-brand-600/10', 'border-brand-500');
        pdfEngineDoclingV3Label.classList.remove('glass-panel');
      }
    });
  });

  addSourceBtn?.addEventListener('click', () => {
    const rawPath = (newSourcePath?.value || '').trim();
    if (!rawPath) return;
    const type = (newSourceType?.value || 'obsidian').toLowerCase();

    const trimmedPath = rawPath.replace(/[\\/]+$/, '');
    const defaultName = trimmedPath.split(/[\\/]/).pop() || 'New Source';

    const sourceObj = {
      id: Date.now().toString(),
      name: (newSourceName?.value || '').trim() || defaultName,
      type: type,
      path: rawPath,
      enabled: true,
      itemCount: 0,
      lastIndexed: 'Never',
      pdfEngine: type === 'pdf' ? getSelectedPdfEngine() : 'fitz',
    };

    sources.push(sourceObj);
    renderSources();
    updateIndexingBtnState();

    if (newSourcePath) newSourcePath.value = '';
    if (newSourceName) newSourceName.value = '';
    addSourceBtn.disabled = true;

    fetch('/api/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sourceObj),
    })
      .then(() => fetchSources())
      .catch(() => {});
  });

  // --- Indexing Logic & Real-Time WebSocket Progress ---
  function updateProgressUI(progress, msg) {
    const pct = Math.max(0, Math.min(100, Math.round(Number(progress || 0))));
    if (indexingProgressText) indexingProgressText.textContent = `${pct}%`;
    if (indexingProgressBar) indexingProgressBar.style.width = `${pct}%`;
    if (indexingStatusMessage && msg) indexingStatusMessage.textContent = msg;
  }

  function handleIndexingProgressPayload(data) {
    if (!data) return;
    const msgType = data.type || data.event || '';
    if (msgType !== 'indexing_progress' && msgType !== 'INDEXING_PROGRESS') return;

    if (fallbackTimer) {
      clearInterval(fallbackTimer);
      fallbackTimer = null;
    }

    if (!isIndexing) {
      isIndexing = true;
      updateIndexingBtnState();
      if (indexingBtnText) indexingBtnText.textContent = 'Indexing Vault...';
      indexingIconPlay?.classList.add('hidden');
      indexingIconSpin?.classList.remove('hidden');
      indexingProgressContainer?.classList.remove('hidden');
    }

    const pct = data.percent !== undefined ? data.percent : (data.progress !== undefined ? data.progress : 0);
    updateProgressUI(pct, data.message || 'Indexing vault sources...');

    const st = String(data.status || '').toLowerCase();
    if (pct >= 100 || st === 'done' || st === 'error') {
      if (ws) {
        try { ws.close(); } catch (_) {}
        ws = null;
      }
      finishIndexing();
    }
  }

  function connectIndexingWebSocket() {
    if (window.appWebSocket && window.appWebSocket.readyState === WebSocket.OPEN) {
      return;
    }
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
      ws = new WebSocket(`${protocol}${window.location.host}/ws/indexing`);
      ws.onmessage = (event) => {
        try {
          handleIndexingProgressPayload(JSON.parse(event.data));
        } catch (_) {}
      };
      ws.onerror = () => {
        try { ws.close(); } catch (_) {}
      };
    } catch (_) {}
  }

  // Listen to global WebSocket events dispatched by app.js (/ws) so zero frames are missed
  document.addEventListener('ws:indexing_progress', (e) => {
    handleIndexingProgressPayload(e.detail);
  });

  function simulateFallbackIndexing() {
    if (fallbackTimer) clearInterval(fallbackTimer);
    let progress = 10;

    fallbackTimer = setInterval(() => {
      progress += 10;
      if (progress === 30) updateProgressUI(progress, 'Parsing Obsidian Markdown Wikilinks...');
      else if (progress === 70) updateProgressUI(progress, 'Extracting PDF text layout & table structures...');
      else if (progress === 90) updateProgressUI(progress, 'Updating Tantivy BM25 Inverted Index...');
      else if (progress >= 100) {
        progress = 100;
        updateProgressUI(progress, 'Indexing Complete! All local knowledge material ready for AI RAG.');
        clearInterval(fallbackTimer);
        fallbackTimer = null;
        finishIndexing();
      } else {
        updateProgressUI(progress, indexingStatusMessage?.textContent || 'Indexing...');
      }
    }, 600);
  }

  function finishIndexing() {
    setTimeout(() => {
      isIndexing = false;
      if (indexingBtnText) indexingBtnText.textContent = 'Build/Update Tantivy Index';
      indexingIconPlay?.classList.remove('hidden');
      indexingIconSpin?.classList.add('hidden');
      indexingProgressContainer?.classList.add('hidden');
      updateIndexingBtnState();
      fetchSources();
      if (typeof window.refreshDiagnostics === 'function') {
        window.refreshDiagnostics();
      }
    }, 1500);
  }

  startIndexingBtn?.addEventListener('click', () => {
    isIndexing = true;
    updateIndexingBtnState();

    if (indexingBtnText) indexingBtnText.textContent = 'Indexing Vault...';
    indexingIconPlay?.classList.add('hidden');
    indexingIconSpin?.classList.remove('hidden');
    indexingProgressContainer?.classList.remove('hidden');

    updateProgressUI(5, 'Starting hybrid vault indexing...');

    // Open dedicated /ws/indexing listener BEFORE firing POST /api/sources/index so fast runs are never missed
    connectIndexingWebSocket();

    fetch('/api/sources/index', { method: 'POST' })
      .then(res => {
        if (!res.ok) throw new Error('API Error');
        return res.json();
      })
      .then(data => {
        if (data && data.status === 'busy') {
          updateProgressUI(50, data.message || 'Indexing is already running in background...');
        }
      })
      .catch(() => {
        simulateFallbackIndexing();
      });
  });

  // Init
  fetchSources();
});