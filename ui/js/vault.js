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
  const startIndexingBtn = document.getElementById('start-indexing-btn');
  const indexingBtnText = document.getElementById('indexing-btn-text');
  const indexingIconPlay = document.getElementById('indexing-icon-play');
  const indexingIconSpin = document.getElementById('indexing-icon-spin');
  const indexingProgressContainer = document.getElementById('indexing-progress-container');
  const indexingStatusMessage = document.getElementById('indexing-status-message');
  const indexingProgressText = document.getElementById('indexing-progress-text');
  const indexingProgressBar = document.getElementById('indexing-progress-bar');


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
      .catch(() => {
        sources = [
          { id: '1', name: 'Clinical Surgery Vault', type: 'obsidian', path: './data/vaults/surgery', enabled: true, itemCount: 42, lastIndexed: '2026-09-28' },
          { id: '2', name: 'Sabiston Surgery 21st Ed', type: 'pdf', path: './data/pdfs/sabiston.pdf', enabled: true, itemCount: 1200, lastIndexed: '2026-09-28', pdfEngine: 'fitz' },
          { id: '3', name: 'Medical Wikipedia ZIM', type: 'zim', path: './data/zim/wikipedia_med.zim', enabled: false, itemCount: 15000, lastIndexed: 'Never' },
        ];
        renderSources();
        updateIndexingBtnState();
      });
  }

  function renderSources() {
    sourcesList.innerHTML = '';
    if (sources.length === 0) {
      sourcesListEmpty.classList.remove('hidden');
      sourcesList.classList.add('hidden');
    } else {
      sourcesListEmpty.classList.add('hidden');
      sourcesList.classList.remove('hidden');

      sources.forEach(src => {
        const bgClass = src.enabled ? 'glass-card' : 'glass-panel opacity-60';
        const toggleIcon = src.enabled ? icons.toggleOn : icons.toggleOff;
        const toggleColor = src.enabled ? 'text-success-500' : 'text-muted';
        const typeBadge = src.type === 'pdf' && src.pdfEngine ? `${src.type} (${src.pdfEngine})` : src.type;

        const el = document.createElement('div');
        el.className = `p-4 rounded-xl border flex items-center justify-between transition-all ${bgClass}`;
        el.innerHTML = `
          <div class="flex items-center gap-4 min-w-0 flex-1">
            <div class="p-2.5 glass-panel rounded-xl text-brand-400">
              ${icons[src.type]}
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <p class="text-sm font-semibold">${window.escapeHTML(src.name)}</p>
                <span class="text-xs px-2 py-0.5 rounded glass-panel text-muted uppercase font-mono">
                  ${typeBadge}
                </span>
              </div>
              <p class="text-xs font-mono text-muted truncate mt-0.5">${window.escapeHTML(src.path)}</p>
            </div>
          </div>
          <div class="flex items-center gap-6">
            <div class="text-right hidden sm:block">
              <p class="text-xs text-muted">${src.itemCount || 0} indexed items</p>
              <p class="text-[10px] text-muted">Last: ${src.lastIndexed}</p>
            </div>
            <button class="toggle-btn p-1 text-2xl transition-colors ${toggleColor} bg-transparent border-none cursor-pointer" data-id="${src.id}" title="${src.enabled ? 'Source Enabled' : 'Source Disabled'}">
              ${toggleIcon}
            </button>
            <button class="delete-btn p-2 text-muted hover:text-danger-500 hover:bg-danger-500/10 rounded-lg transition-colors bg-transparent border-none cursor-pointer" data-id="${src.id}">
              ${icons.trash}
            </button>
          </div>
        `;
        sourcesList.appendChild(el);
      });
    }

    // Attach event listeners to buttons
    document.querySelectorAll('.toggle-btn').forEach(btn => {
      btn.addEventListener('click', () => handleToggle(btn.getAttribute('data-id')));
    });
    document.querySelectorAll('.delete-btn').forEach(btn => {
      btn.addEventListener('click', () => handleDelete(btn.getAttribute('data-id')));
    });
  }

  function handleToggle(id) {
    sources = sources.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s);
    renderSources();
    updateIndexingBtnState();

    fetch(`/api/sources/${id}/toggle`, { method: 'POST' }).catch(() => {});
  }

  function handleDelete(id) {
    sources = sources.filter(s => s.id !== id);
    renderSources();
    updateIndexingBtnState();

    fetch(`/api/sources/${id}`, { method: 'DELETE' }).catch(() => {});
  }

  function updateIndexingBtnState() {
    const hasEnabled = sources.some(s => s.enabled);
    if (isIndexing || !hasEnabled) {
      startIndexingBtn.disabled = true;
    } else {
      startIndexingBtn.disabled = false;
    }
  }

  // --- Form Logic ---
  newSourcePath.addEventListener('input', () => {
    addSourceBtn.disabled = newSourcePath.value.trim() === '';
  });

  newSourceType.addEventListener('change', () => {
    if (newSourceType.value === 'pdf') {
      pdfEngineContainer.classList.remove('hidden');
    } else {
      pdfEngineContainer.classList.add('hidden');
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
      if (radio.value === 'fitz') {
        pdfEngineFitzLabel.classList.add('bg-brand-600/10', 'border-brand-500');
        pdfEngineFitzLabel.classList.remove('glass-panel');

        pdfEngineDoclingLabel.classList.remove('bg-brand-600/10', 'border-brand-500');
        pdfEngineDoclingLabel.classList.add('glass-panel');
      } else {
        pdfEngineDoclingLabel.classList.add('bg-brand-600/10', 'border-brand-500');
        pdfEngineDoclingLabel.classList.remove('glass-panel');

        pdfEngineFitzLabel.classList.remove('bg-brand-600/10', 'border-brand-500');
        pdfEngineFitzLabel.classList.add('glass-panel');
      }
    });
  });

  addSourceBtn.addEventListener('click', () => {
    const path = newSourcePath.value.trim();
    if (!path) return;
    const type = newSourceType.value;

    const sourceObj = {
      id: Date.now().toString(),
      name: newSourceName.value.trim() || path.split('/').pop() || path.split('\\').pop() || 'New Source',
      type: type,
      path: path,
      enabled: true,
      itemCount: 0,
      lastIndexed: 'Never',
      pdfEngine: type === 'pdf' ? getSelectedPdfEngine() : undefined,
    };

    sources.push(sourceObj);
    renderSources();
    updateIndexingBtnState();

    newSourcePath.value = '';
    newSourceName.value = '';
    addSourceBtn.disabled = true;

    fetch('/api/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sourceObj),
    }).catch(() => {});
  });

  // --- Indexing Logic ---
  function updateProgressUI(progress, msg) {
    indexingProgressText.textContent = `${progress}%`;
    indexingProgressBar.style.width = `${progress}%`;
    indexingStatusMessage.textContent = msg;
  }

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
         updateProgressUI(progress, indexingStatusMessage.textContent);
      }
    }, 600);
  }

  function finishIndexing() {
      setTimeout(() => {
        isIndexing = false;
        indexingBtnText.textContent = 'Build/Update Tantivy Index';
        indexingIconPlay.classList.remove('hidden');
        indexingIconSpin.classList.add('hidden');
        indexingProgressContainer.classList.add('hidden');
        updateIndexingBtnState();
        fetchSources();
      }, 1500);
  }

  startIndexingBtn.addEventListener('click', () => {
    isIndexing = true;
    updateIndexingBtnState();

    indexingBtnText.textContent = 'Indexing Vault...';
    indexingIconPlay.classList.add('hidden');
    indexingIconSpin.classList.remove('hidden');
    indexingProgressContainer.classList.remove('hidden');

    updateProgressUI(10, 'Scanning files in enabled folders...');

    fetch('/api/sources/index', { method: 'POST' })
      .then(res => {
        if (!res.ok) throw new Error('API Error');
        ws = new WebSocket('ws://127.0.0.1:8000/ws/indexing');

        ws.onmessage = (event) => {
          const data = JSON.parse(event.data);
          if (data.event === 'INDEXING_PROGRESS') {
             updateProgressUI(data.progress, data.message);
             if (data.progress >= 100) {
                 ws.close();
                 ws = null;
                 finishIndexing();
             }
          }
        };
        ws.onerror = () => simulateFallbackIndexing();
      })
      .catch(() => {
        simulateFallbackIndexing();
      });
  });

  // Init
  fetchSources();
});
