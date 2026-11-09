// ui/js/notes.js - Lecture Topic Pad, Multi-Column Cards, Line-by-Line Lock-on-Enter & Right Sidebar Launcher

/**
 * Computes 8 distinct, harmonious glassmorphic shade variations derived
 * from the active background wallpaper hue and saturation variables.
 */
export function computeNoteCardShadeStyle(shadeIndex = 0) {
  const rootStyle = typeof window !== 'undefined' && window.getComputedStyle ? getComputedStyle(document.documentElement) : null;
  const baseHue = (rootStyle && Number.parseFloat(rootStyle.getPropertyValue('--bg-dominant-hue'))) || 218;
  const baseSat = (rootStyle && Number.parseFloat(rootStyle.getPropertyValue('--bg-dominant-sat'))) || 42;
  const isLightMode = typeof document !== 'undefined' &&
                      (document.documentElement.classList.contains('light') ||
                       document.body.classList.contains('light-theme') ||
                       document.body.classList.contains('light-mode'));

  const shadeOffsets = [
    { dh: 0,   ds: 0,   dl: 0 },
    { dh: 18,  ds: 6,   dl: 3 },
    { dh: -18, ds: 4,   dl: -2 },
    { dh: 34,  ds: -4,  dl: 4 },
    { dh: -32, ds: 8,   dl: 2 },
    { dh: 50,  ds: -6,  dl: -3 },
    { dh: -48, ds: 5,   dl: 5 },
    { dh: 12,  ds: 10,  dl: -4 }
  ];

  const offset = shadeOffsets[Math.abs(Number(shadeIndex) || 0) % shadeOffsets.length];
  const h = (baseHue + offset.dh + 360) % 360;
  const s = Math.max(18, Math.min(78, baseSat + offset.ds));
  const l = isLightMode ? Math.min(94, 86 + offset.dl) : Math.max(12, Math.min(26, 17 + offset.dl));
  const borderL = isLightMode ? l - 14 : l + 16;

  return {
    background: `linear-gradient(155deg, hsla(${h}, ${s}%, ${l}%, 0.82) 0%, hsla(${(h + 14) % 360}, ${s}%, ${Math.max(8, l - 4)}%, 0.90) 100%)`,
    borderColor: `hsla(${h}, ${Math.min(90, s + 12)}%, ${borderL}%, 0.38)`,
    accentColor: `hsl(${h}, ${Math.min(95, s + 20)}%, ${isLightMode ? 38 : 72}%)`,
    rawH: h
  };
}

/**
 * Right Sidebar Reference Links & Custom Keyword MCQ Launcher Drawer
 */
export function renderTopicRightSidebar(drawerEl, item, onLaunchCustomQuiz, onOpenReferenceLink) {
  if (!drawerEl || !item) return;
  drawerEl.classList.remove('hidden', 'translate-x-full');

  const refs = item.references || { obsidian: [], pdf: [], zim: [] };
  const allRefs = [
    ...(refs.obsidian || []).map((r) => ({ ...r, _type: 'obsidian', _badge: 'OBS' })),
    ...(refs.pdf || []).map((r) => ({ ...r, _type: 'pdf', _badge: 'PDF' })),
    ...(refs.zim || []).map((r) => ({ ...r, _type: 'zim', _badge: 'ZIM' }))
  ];

  const safeTopic = (item.topic_text || '').replace(/"/g, '&quot;');

  drawerEl.innerHTML = `
    <aside class="w-80 sm:w-96 h-full flex flex-col rounded-2xl bg-slate-900/95 border border-slate-700/80 p-4 shadow-2xl backdrop-blur-xl text-slate-100">
      <div class="flex items-start justify-between gap-2 pb-3 border-b border-slate-800">
        <div class="min-w-0 flex-1">
          <span class="text-[10px] font-bold uppercase tracking-wider text-sky-400">Selected Lecture Topic</span>
          <h3 class="text-base font-extrabold text-white mt-0.5 truncate" title="${safeTopic}">${safeTopic}</h3>
        </div>
        <button type="button" id="btn-close-topic-drawer" class="text-slate-400 hover:text-white text-lg leading-none px-1 cursor-pointer">&times;</button>
      </div>

      <!-- Custom Keyword MCQ Launcher Card -->
      <div class="my-3.5 p-3.5 rounded-xl bg-gradient-to-br from-emerald-950/60 to-slate-900 border border-emerald-500/35">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-bold text-emerald-300">Practice Custom Keyword MCQs</span>
          <span class="px-2 py-0.5 rounded-full text-[11px] font-mono font-bold bg-emerald-500/20 text-emerald-300">
            ${item.mcq_count || 0} Matched
          </span>
        </div>
        <div class="flex items-center gap-2 mb-3">
          <select id="custom-mcq-limit" class="flex-1 rounded-lg bg-slate-950 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
            <option value="10">10 Questions</option>
            <option value="20" selected>20 Questions</option>
            <option value="30">30 Questions</option>
            <option value="50">50 Questions</option>
          </select>
          <select id="custom-mcq-mode" class="flex-1 rounded-lg bg-slate-950 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200">
            <option value="learn" selected>Learn Mode</option>
            <option value="test">Timed Test</option>
          </select>
        </div>
        <button
          type="button"
          id="btn-start-topic-mcq"
          class="w-full py-2 px-3 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs shadow-lg shadow-emerald-500/20 transition cursor-pointer"
        >
          ▶ Start "${safeTopic}" MCQ Quiz
        </button>
      </div>

      <!-- Reference Links List -->
      <div class="flex items-center justify-between text-xs font-semibold text-slate-400 mb-2">
        <span>Verified Reference Links (${allRefs.length})</span>
      </div>

      <div id="drawer-ref-list" class="flex-1 overflow-y-auto space-y-2 pr-1">
        ${allRefs.length === 0 ? `
          <div class="p-4 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-400 text-center">
            No local references matched for "${safeTopic}".
          </div>
        ` : allRefs.map((ref, idx) => `
          <div class="drawer-ref-card p-3 rounded-xl bg-slate-950/70 hover:bg-slate-800/80 border border-slate-800 hover:border-sky-500/40 cursor-pointer transition"
               data-ref-idx="${idx}">
            <div class="flex items-center justify-between gap-2">
              <span class="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${
                ref._type === 'obsidian' ? 'bg-purple-500/20 text-purple-300' :
                ref._type === 'pdf' ? 'bg-rose-500/20 text-rose-300' :
                'bg-blue-500/20 text-blue-300'
              }">${ref._badge}</span>
              ${ref.page ? `<span class="text-[11px] font-mono text-slate-400">Page ${ref.page}</span>` : ''}
            </div>
            <div class="text-xs font-bold text-slate-100 mt-1 truncate">${(ref.title || 'Reference Document').replace(/"/g, '&quot;')}</div>
            ${ref.heading ? `<div class="text-[11px] text-sky-400/90 truncate">${ref.heading.replace(/"/g, '&quot;')}</div>` : ''}
            ${ref.snippet ? `<p class="text-[11px] text-slate-400 mt-1 line-clamp-2">${ref.snippet.replace(/"/g, '&quot;')}</p>` : ''}
          </div>
        `).join('')}
      </div>
    </aside>
  `;

  drawerEl.querySelector('#btn-close-topic-drawer')?.addEventListener('click', () => {
    drawerEl.classList.add('hidden');
  });

  drawerEl.querySelector('#btn-start-topic-mcq')?.addEventListener('click', () => {
    const limit = Number(drawerEl.querySelector('#custom-mcq-limit')?.value) || 20;
    const mode = drawerEl.querySelector('#custom-mcq-mode')?.value || 'learn';
    if (typeof onLaunchCustomQuiz === 'function') {
      onLaunchCustomQuiz({ keyword: item.topic_text, limit, mode });
    }
  });

  drawerEl.querySelector('#drawer-ref-list')?.addEventListener('click', (e) => {
    const card = e.target.closest('.drawer-ref-card');
    if (!card) return;
    const refObj = allRefs[Number(card.dataset.refIdx)];
    if (refObj && typeof onOpenReferenceLink === 'function') {
      onOpenReferenceLink(refObj);
    }
  });
}

/**
 * Line-by-Line Topic Editor with Synchronous Lock-on-Enter (<2ms)
 * and Single-Click (220ms debounced) vs Double-Click Disambiguation.
 */
export class LectureNoteEditor {
  constructor(workspaceEl, rightDrawerEl, onBackToGridCallback, onLaunchCustomQuiz, onOpenReferenceLink) {
    this.workspaceEl = workspaceEl;
    this.rightDrawerEl = rightDrawerEl;
    this.onBack = onBackToGridCallback;
    this.onLaunchCustomQuiz = onLaunchCustomQuiz;
    this.onOpenReferenceLink = onOpenReferenceLink;
    this.note = null;
    this.items = [];
    this.clickTimer = null;
    this.activeItemId = null;
    this.titleSaveTimer = null;
  }

  async openNote(noteId) {
    const res = await fetch(`/api/notes/lecture-notes/${encodeURIComponent(noteId)}`);
    if (!res.ok) throw new Error('Failed to load note');
    const data = await res.json();
    this.note = data.note;
    this.items = Array.isArray(data.items) ? data.items : [];
    this.activeItemId = null;
    this.renderEditor();
  }

  _escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  _escapeAttr(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  renderEditor() {
    const shade = computeNoteCardShadeStyle(this.note.shade_index || 0);
    this.workspaceEl.innerHTML = `
      <div class="flex flex-col h-full rounded-2xl border p-5 shadow-2xl backdrop-blur-md transition-colors"
           style="background: ${shade.background}; border-color: ${shade.borderColor};">
        <!-- Header Bar -->
        <div class="flex flex-wrap items-center justify-between gap-3 pb-4 mb-4 border-b border-white/10">
          <div class="flex items-center gap-3 flex-1 min-w-[240px]">
            <button type="button" id="btn-close-note"
              class="px-3 py-1.5 rounded-xl bg-slate-900/70 hover:bg-slate-800 text-slate-200 text-xs font-semibold border border-white/10 transition cursor-pointer">
              ← All Notes
            </button>
            <input
              type="text"
              id="lecture-note-title-input"
              value="${this._escapeAttr(this.note.title)}"
              placeholder="Enter Note Name (e.g., ENT Day 1)..."
              class="flex-1 bg-transparent text-lg font-extrabold text-white focus:outline-none focus:ring-2 focus:ring-sky-400/50 rounded-lg px-2 py-1"
            />
          </div>
          <div class="flex items-center gap-2 text-[11px] text-slate-300">
            <!-- Shade Picker Strip -->
            <div class="flex items-center gap-1.5 mr-2" id="shade-picker-strip">
              ${[0, 1, 2, 3, 4, 5, 6, 7].map((sIdx) => {
                const s = computeNoteCardShadeStyle(sIdx);
                const isSelected = (this.note.shade_index || 0) === sIdx;
                return `
                  <button type="button" class="shade-dot w-4 h-4 rounded-full border transition cursor-pointer ${
                    isSelected ? 'ring-2 ring-white scale-110' : 'opacity-70 hover:opacity-100'
                  }" data-shade="${sIdx}" style="background: ${s.accentColor}; border-color: ${s.borderColor};" title="Palette variation ${sIdx + 1}"></button>
                `;
              }).join('')}
            </div>
            <span id="note-autosave-indicator" class="px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
              ✓ Auto-saved
            </span>
          </div>
        </div>

        <div class="text-[11px] text-slate-300/80 mb-2 flex items-center justify-between px-2">
          <span><strong>Tip:</strong> Press <kbd class="px-1.5 py-0.5 rounded bg-slate-900/80 border border-white/10">Enter</kbd> to lock topic &amp; move to next line. <strong>Single-click</strong> topic for References &amp; MCQs; <strong>Double-click</strong> topic text to edit.</span>
          <span class="uppercase tracking-wider font-semibold text-slate-400">References &amp; MCQs</span>
        </div>

        <!-- Locked Topic Lines Container -->
        <div id="note-topic-lines-list" class="flex-1 overflow-y-auto space-y-1.5 pr-1">
          ${this.items.map((item, idx) => this._renderLockedTopicRowHtml(item, idx + 1)).join('')}
        </div>

        <!-- Active New-Line Input at Bottom -->
        <div class="mt-3 pt-3 border-t border-white/10 flex items-center gap-3 bg-slate-950/55 rounded-xl px-3.5 py-2.5 border border-sky-500/30">
          <span class="text-xs font-mono text-sky-400 select-none" id="next-line-num">#${this.items.length + 1}</span>
          <input
            type="text"
            id="new-topic-line-input"
            placeholder="Type a medical topic (e.g., Cholesteatoma, Otosclerosis, Rinne Test) and press Enter..."
            autocomplete="off"
            class="flex-1 bg-transparent text-sm text-white placeholder-slate-400 focus:outline-none"
          />
          <span class="text-[11px] text-slate-400 hidden sm:inline">Press Enter ↵</span>
        </div>
      </div>
    `;

    this._bindEditorEvents();
    this.workspaceEl.querySelector('#new-topic-line-input')?.focus();
  }

  _renderLockedTopicRowHtml(item, lineNumber) {
    const isResolving = Boolean(item._resolving);
    const isSelected = this.activeItemId === item.item_id;

    return `
      <div class="note-topic-row group flex items-center justify-between gap-3 px-3.5 py-2.5 rounded-xl border transition-all cursor-pointer select-none ${
        isSelected
          ? 'bg-sky-500/20 border-sky-400/60 shadow-md'
          : 'bg-slate-950/45 hover:bg-slate-950/70 border-white/10'
      }" data-item-id="${item.item_id}" data-locked="true">
        <!-- Left: Line Number & Locked Topic Name -->
        <div class="flex items-center gap-3 min-w-0 flex-1">
          <span class="text-xs font-mono text-slate-400 w-6 shrink-0">${lineNumber}.</span>
          <span class="topic-display-text text-sm font-semibold text-slate-100 truncate" title="Single-click for References & MCQs | Double-click to edit">
            ${this._escapeHtml(item.topic_text)}
          </span>
          <input
            type="text"
            class="topic-inline-editor hidden flex-1 bg-slate-900 text-sm font-semibold text-white px-2.5 py-1 rounded-lg border border-amber-400 focus:outline-none"
            value="${this._escapeAttr(item.topic_text)}"
          />
        </div>

        <!-- Right End: Resolved Reference Links Count & MCQ Count Badges -->
        <div class="topic-right-badges flex items-center gap-2 shrink-0">
          ${isResolving ? `
            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] bg-slate-800 text-sky-300 animate-pulse">
              <span class="w-1.5 h-1.5 rounded-full bg-sky-400 animate-ping"></span>
              Scanning Refs &amp; MCQs...
            </span>
          ` : `
            <span class="px-2 py-0.5 rounded-md text-[11px] font-semibold ${
              item.obsidian_count > 0 ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30' : 'bg-slate-900/60 text-slate-500'
            }" title="Obsidian Notes">
              OBS ${item.obsidian_count || 0}
            </span>
            <span class="px-2 py-0.5 rounded-md text-[11px] font-semibold ${
              item.pdf_count > 0 ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-slate-900/60 text-slate-500'
            }" title="PDF Textbook Pages">
              PDF ${item.pdf_count || 0}
            </span>
            <span class="px-2 py-0.5 rounded-md text-[11px] font-semibold ${
              item.zim_count > 0 ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30' : 'bg-slate-900/60 text-slate-500'
            }" title="ZIM Articles">
              ZIM ${item.zim_count || 0}
            </span>
            <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
              item.mcq_count > 0 ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'bg-slate-900/60 text-slate-500'
            }" title="Matching MCQs Available">
              ${item.mcq_count || 0} MCQs
            </span>
          `}
          <button type="button" class="btn-delete-topic-line opacity-0 group-hover:opacity-100 text-slate-400 hover:text-rose-400 px-1.5 transition cursor-pointer" title="Remove line">
            &times;
          </button>
        </div>
      </div>
    `;
  }

  _bindEditorEvents() {
    const closeBtn = this.workspaceEl.querySelector('#btn-close-note');
    const titleInput = this.workspaceEl.querySelector('#lecture-note-title-input');
    const newLineInput = this.workspaceEl.querySelector('#new-topic-line-input');
    const listEl = this.workspaceEl.querySelector('#note-topic-lines-list');
    const shadeStrip = this.workspaceEl.querySelector('#shade-picker-strip');

    closeBtn?.addEventListener('click', () => {
      if (this.rightDrawerEl) this.rightDrawerEl.classList.add('hidden');
      this.onBack();
    });

    shadeStrip?.addEventListener('click', (e) => {
      const dot = e.target.closest('.shade-dot');
      if (!dot) return;
      this.note.shade_index = Number(dot.dataset.shade) || 0;
      this._saveNoteMetadata();
      this.renderEditor();
    });

    titleInput?.addEventListener('input', () => {
      this.note.title = titleInput.value.trim() || 'Untitled Note';
      if (this.titleSaveTimer) clearTimeout(this.titleSaveTimer);
      this.titleSaveTimer = setTimeout(() => this._saveNoteMetadata(), 300);
    });

    newLineInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const rawTopic = newLineInput.value.trim();
        if (!rawTopic) return;
        newLineInput.value = '';
        this._commitNewTopicLineOptimistic(rawTopic);
      }
    });

    listEl?.addEventListener('click', (e) => {
      const deleteBtn = e.target.closest('.btn-delete-topic-line');
      if (deleteBtn) {
        e.stopPropagation();
        const row = deleteBtn.closest('.note-topic-row');
        if (row) this._deleteTopicLine(row.dataset.itemId);
        return;
      }

      const row = e.target.closest('.note-topic-row');
      if (!row || row.dataset.editing === 'true') return;

      if (this.clickTimer) clearTimeout(this.clickTimer);
      this.clickTimer = setTimeout(() => {
        this.clickTimer = null;
        this._onSingleClickTopicRow(row.dataset.itemId);
      }, 220);
    });

    listEl?.addEventListener('dblclick', (e) => {
      const row = e.target.closest('.note-topic-row');
      if (!row) return;
      e.preventDefault();
      e.stopPropagation();

      if (this.clickTimer) {
        clearTimeout(this.clickTimer);
        this.clickTimer = null;
      }
      this._enterInlineEditMode(row);
    });
  }

  async _saveNoteMetadata() {
    try {
      await fetch('/api/notes/lecture-notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          note_id: this.note.note_id,
          title: this.note.title,
          shade_index: this.note.shade_index,
          is_pinned: this.note.is_pinned ? 1 : 0
        })
      });
      const ind = this.workspaceEl.querySelector('#note-autosave-indicator');
      if (ind) {
        ind.textContent = '✓ Auto-saved';
        ind.className = 'px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30';
      }
    } catch (err) {
      console.error('[LectureNoteEditor] Failed saving note metadata:', err);
    }
  }

  async _commitNewTopicLineOptimistic(topicText) {
    const tempId = `item_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const lineOrder = this.items.length;
    const newItem = {
      item_id: tempId,
      note_id: this.note.note_id,
      line_order: lineOrder,
      topic_text: topicText,
      mcq_count: 0,
      ref_count: 0,
      obsidian_count: 0,
      pdf_count: 0,
      zim_count: 0,
      references: { obsidian: [], pdf: [], zim: [] },
      _resolving: true
    };

    this.items.push(newItem);
    const listEl = this.workspaceEl.querySelector('#note-topic-lines-list');
    if (listEl) {
      listEl.insertAdjacentHTML('beforeend', this._renderLockedTopicRowHtml(newItem, this.items.length));
      listEl.scrollTop = listEl.scrollHeight;
    }
    const nextNumEl = this.workspaceEl.querySelector('#next-line-num');
    if (nextNumEl) nextNumEl.textContent = `#${this.items.length + 1}`;

    await this._resolveAndPersistTopicItem(newItem);
  }

  _enterInlineEditMode(rowEl) {
    rowEl.dataset.editing = 'true';
    const textSpan = rowEl.querySelector('.topic-display-text');
    const inputEl = rowEl.querySelector('.topic-inline-editor');
    if (!textSpan || !inputEl) return;

    textSpan.classList.add('hidden');
    inputEl.classList.remove('hidden');
    inputEl.focus();
    inputEl.select();

    const commitEdit = async () => {
      if (rowEl.dataset.editing !== 'true') return;
      rowEl.dataset.editing = 'false';
      const updatedText = inputEl.value.trim() || textSpan.textContent.trim();
      textSpan.textContent = updatedText;
      inputEl.classList.add('hidden');
      textSpan.classList.remove('hidden');

      const item = this.items.find((i) => i.item_id === rowEl.dataset.itemId);
      if (item && item.topic_text !== updatedText) {
        item.topic_text = updatedText;
        item._resolving = true;
        this._refreshRowDom(item);
        await this._resolveAndPersistTopicItem(item, true);
      }
    };

    inputEl.onkeydown = (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        commitEdit();
      } else if (ev.key === 'Escape') {
        rowEl.dataset.editing = 'false';
        inputEl.value = textSpan.textContent.trim();
        inputEl.classList.add('hidden');
        textSpan.classList.remove('hidden');
      }
    };
    inputEl.onblur = () => commitEdit();
  }

  async _resolveAndPersistTopicItem(item, forceRefresh = false) {
    try {
      const res = await fetch(`/api/notes/lecture-notes/${encodeURIComponent(this.note.note_id)}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item_id: item.item_id,
          line_order: item.line_order,
          topic_text: item.topic_text,
          force_refresh: forceRefresh
        })
      });
      if (!res.ok) throw new Error('Resolution failed');
      const saved = await res.json();
      Object.assign(item, saved, { _resolving: false });
      this._refreshRowDom(item);

      if (this.activeItemId === item.item_id) {
        this._onSingleClickTopicRow(item.item_id);
      }
    } catch (err) {
      item._resolving = false;
      this._refreshRowDom(item);
      console.error('[LectureNoteEditor] Failed resolving topic line:', err);
    }
  }

  _refreshRowDom(item) {
    const idx = this.items.findIndex((i) => i.item_id === item.item_id);
    const existingRow = this.workspaceEl.querySelector(`.note-topic-row[data-item-id="${CSS.escape(item.item_id)}"]`);
    if (existingRow && idx !== -1) {
      existingRow.outerHTML = this._renderLockedTopicRowHtml(item, idx + 1);
    }
  }

  _onSingleClickTopicRow(itemId) {
    this.activeItemId = itemId;
    const item = this.items.find((i) => i.item_id === itemId);
    if (!item) return;

    this.workspaceEl.querySelectorAll('.note-topic-row').forEach((r) => {
      const isCur = r.dataset.itemId === itemId;
      r.classList.toggle('bg-sky-500/20', isCur);
      r.classList.toggle('border-sky-400/60', isCur);
      r.classList.toggle('shadow-md', isCur);
      r.classList.toggle('bg-slate-950/45', !isCur);
      r.classList.toggle('border-white/10', !isCur);
    });

    renderTopicRightSidebar(this.rightDrawerEl, item, this.onLaunchCustomQuiz, this.onOpenReferenceLink);
  }

  async _deleteTopicLine(itemId) {
    try {
      await fetch(`/api/notes/lecture-notes/${encodeURIComponent(this.note.note_id)}/items/${encodeURIComponent(itemId)}`, {
        method: 'DELETE'
      });
      this.items = this.items.filter((i) => i.item_id !== itemId);
      this.items.forEach((i, idx) => { i.line_order = idx; });
      if (this.activeItemId === itemId) {
        this.activeItemId = null;
        if (this.rightDrawerEl) this.rightDrawerEl.classList.add('hidden');
      }
      const listEl = this.workspaceEl.querySelector('#note-topic-lines-list');
      if (listEl) {
        listEl.innerHTML = this.items.map((it, idx) => this._renderLockedTopicRowHtml(it, idx + 1)).join('');
      }
      const nextNumEl = this.workspaceEl.querySelector('#next-line-num');
      if (nextNumEl) nextNumEl.textContent = `#${this.items.length + 1}`;
    } catch (err) {
      console.error('[LectureNoteEditor] Failed deleting topic line:', err);
    }
  }
}

/**
 * Main Controller orchestrating Multi-Column Background-Tinted Cards Grid,
 * Pagination (12 notes/page), Search, Pinning, and Note Opening.
 */
export class MyNotesController {
  constructor() {
    this.container = null;
    this.currentPage = 1;
    this.pageSize = 12;
    this.totalPages = 1;
    this.searchQuery = '';
    this.searchDebounceTimer = null;
    this.editor = null;
    this.isEditing = false;
  }

  init(containerEl) {
    this.container = containerEl;
    this.renderLayout();
    this.loadNotes();
  }

  renderLayout() {
    this.container.innerHTML = `
      <div class="w-full h-full flex flex-col p-6 animate-fade-in overflow-hidden">
        <!-- Top Toolbar -->
        <header class="flex flex-wrap items-center justify-between gap-4 pb-4 mb-4 border-b border-gray-700/50 shrink-0">
          <div>
            <div class="flex items-center gap-2">
              <span class="text-xs font-bold uppercase tracking-wider text-brand-400 glass-panel px-2.5 py-1 rounded-md">Lecture Topic Pad</span>
              <span id="notes-total-badge" class="text-xs text-muted font-mono">0 Notes</span>
            </div>
            <h1 class="text-2xl font-black text-white mt-1">My Notes</h1>
          </div>

          <div class="flex items-center gap-3 flex-wrap">
            <div class="relative w-64">
              <input
                type="search"
                id="notes-search-input"
                placeholder="Search notes or topics..."
                class="w-full glass-input rounded-xl px-3.5 py-2 text-xs text-slate-100 placeholder-slate-400"
                value="${this.searchQuery}"
              />
            </div>
            <button
              type="button"
              id="btn-create-new-note"
              class="primary-btn px-4 py-2 text-xs font-bold rounded-xl flex items-center gap-2 cursor-pointer shadow-lg"
            >
              + Create New Note
            </button>
          </div>
        </header>

        <!-- Main Viewport: Either Grid View or Editor View -->
        <div id="notes-view-content" class="flex-1 min-h-0 overflow-hidden relative">
          <!-- Multi-Column Grid View -->
          <div id="notes-grid-view" class="h-full flex flex-col">
            <div id="my-notes-grid" class="flex-1 overflow-y-auto pr-1" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(245px, 1fr)); gap: 1rem; align-content: start;">
              <!-- Note Cards injected here -->
            </div>

            <!-- Pagination Controls -->
            <div class="pt-3 mt-2 border-t border-gray-800 flex items-center justify-between text-xs text-slate-400 shrink-0">
              <span id="notes-page-label">Page 1 of 1</span>
              <div class="flex items-center gap-2">
                <button type="button" id="btn-notes-prev-page" class="secondary-btn px-3 py-1.5 rounded-lg disabled:opacity-40 cursor-pointer">
                  &lt; Prev
                </button>
                <button type="button" id="btn-notes-next-page" class="secondary-btn px-3 py-1.5 rounded-lg disabled:opacity-40 cursor-pointer">
                  Next &gt;
                </button>
              </div>
            </div>
          </div>

          <!-- Split Workspace: Note Editor (Left) + Right Drawer (Right) -->
          <div id="notes-editor-container" class="hidden h-full flex gap-4 min-h-0 overflow-hidden">
            <div id="my-notes-editor-workspace" class="flex-1 min-h-0 min-w-0"></div>
            <div id="my-notes-right-sidebar" class="hidden shrink-0"></div>
          </div>
        </div>
      </div>
    `;

    this._bindLayoutEvents();
  }

  _bindLayoutEvents() {
    const searchInput = this.container.querySelector('#notes-search-input');
    const createBtn = this.container.querySelector('#btn-create-new-note');
    const prevBtn = this.container.querySelector('#btn-notes-prev-page');
    const nextBtn = this.container.querySelector('#btn-notes-next-page');

    searchInput?.addEventListener('input', (e) => {
      this.searchQuery = e.target.value.trim();
      if (this.searchDebounceTimer) clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = setTimeout(() => {
        this.currentPage = 1;
        this.loadNotes();
      }, 180);
    });

    createBtn?.addEventListener('click', () => this.createNewNote());

    prevBtn?.addEventListener('click', () => {
      if (this.currentPage > 1) {
        this.currentPage--;
        this.loadNotes();
      }
    });

    nextBtn?.addEventListener('click', () => {
      if (this.currentPage < this.totalPages) {
        this.currentPage++;
        this.loadNotes();
      }
    });

    const gridEl = this.container.querySelector('#my-notes-grid');
    gridEl?.addEventListener('click', (e) => {
      const pinBtn = e.target.closest('.btn-pin-note');
      if (pinBtn) {
        e.stopPropagation();
        this.togglePin(pinBtn.dataset.noteId, pinBtn.dataset.pinned === 'true');
        return;
      }
      const delBtn = e.target.closest('.btn-delete-note');
      if (delBtn) {
        e.stopPropagation();
        if (confirm('Delete this note and all its topic lines?')) {
          this.deleteNote(delBtn.dataset.noteId);
        }
        return;
      }
      const card = e.target.closest('.lecture-note-card');
      if (card) {
        this.openNoteEditor(card.dataset.noteId);
      }
    });
  }

  async loadNotes() {
    try {
      const qParams = new URLSearchParams({
        page: String(this.currentPage),
        page_size: String(this.pageSize),
        q: this.searchQuery
      });
      const res = await fetch(`/api/notes/lecture-notes?${qParams.toString()}`);
      if (!res.ok) throw new Error('Failed to load lecture notes');
      const data = await res.json();

      this.totalPages = data.total_pages || 1;
      this.renderNotesGrid(data.notes || [], data.total_items || 0);
    } catch (err) {
      console.error('[MyNotesController] Failed loading notes:', err);
    }
  }

  renderNotesGrid(notes, totalItems) {
    const gridEl = this.container.querySelector('#my-notes-grid');
    const badgeEl = this.container.querySelector('#notes-total-badge');
    const labelEl = this.container.querySelector('#notes-page-label');
    const prevBtn = this.container.querySelector('#btn-notes-prev-page');
    const nextBtn = this.container.querySelector('#btn-notes-next-page');

    if (badgeEl) badgeEl.textContent = `${totalItems} Note${totalItems === 1 ? '' : 's'}`;
    if (labelEl) labelEl.textContent = `Page ${this.currentPage} of ${this.totalPages}`;
    if (prevBtn) prevBtn.disabled = this.currentPage <= 1;
    if (nextBtn) nextBtn.disabled = this.currentPage >= this.totalPages;

    if (!gridEl) return;

    if (notes.length === 0) {
      gridEl.innerHTML = `
        <div class="col-span-full text-center py-16 text-muted glass-panel rounded-2xl border border-gray-700/50 p-8">
          <p class="text-base font-bold text-slate-200 mb-1">No lecture notes found</p>
          <p class="text-xs text-slate-400 mb-4">Create your first lecture topic note to capture key medical terms heard in class.</p>
          <button type="button" class="primary-btn px-4 py-2 text-xs font-bold rounded-xl" id="btn-empty-create-note">
            + Create New Note
          </button>
        </div>
      `;
      gridEl.querySelector('#btn-empty-create-note')?.addEventListener('click', () => this.createNewNote());
      return;
    }

    gridEl.innerHTML = notes.map((n) => {
      const shade = computeNoteCardShadeStyle(n.shade_index || 0);
      const topItems = n.top_items || [];
      const updatedDate = n.updated_at ? new Date(n.updated_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';

      return `
        <div class="lecture-note-card rounded-2xl border p-4 flex flex-col justify-between shadow-lg hover:shadow-2xl transition-all cursor-pointer group hover:-translate-y-1 select-none"
             data-note-id="${n.note_id}"
             style="background: ${shade.background}; border-color: ${shade.borderColor}; min-height: 220px;">
          <div>
            <!-- Header: Title & Pin/Delete Actions -->
            <div class="flex items-start justify-between gap-2 mb-2 pb-2 border-b border-white/10">
              <div class="min-w-0 flex-1">
                <h3 class="font-extrabold text-sm text-white truncate" title="${n.title}">${n.title}</h3>
                <span class="text-[10px] text-slate-300/80 font-mono">${updatedDate}</span>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <button type="button" class="btn-pin-note p-1 text-xs rounded hover:bg-white/10 transition cursor-pointer ${
                  n.is_pinned ? 'text-amber-300' : 'text-slate-400 opacity-0 group-hover:opacity-100'
                }" data-note-id="${n.note_id}" data-pinned="${n.is_pinned}" title="${n.is_pinned ? 'Unpin' : 'Pin note'}">
                  ${n.is_pinned ? '📌' : '📍'}
                </button>
                <button type="button" class="btn-delete-note p-1 text-xs text-slate-400 hover:text-rose-400 rounded hover:bg-white/10 opacity-0 group-hover:opacity-100 transition cursor-pointer"
                        data-note-id="${n.note_id}" title="Delete note">
                  &times;
                </button>
              </div>
            </div>

            <!-- Preview List of Topics -->
            <div class="space-y-1.5 my-2">
              ${topItems.length === 0 ? `
                <p class="text-[11px] text-slate-300/60 italic py-3 text-center">Empty note — click to add topics</p>
              ` : topItems.map((item, idx) => `
                <div class="flex items-center justify-between gap-2 text-xs text-slate-200 bg-slate-950/40 rounded-lg px-2.5 py-1">
                  <span class="truncate flex-1 font-medium">${idx + 1}. ${item.topic_text}</span>
                  <div class="flex items-center gap-1 shrink-0 text-[10px] font-mono">
                    ${item.ref_count > 0 ? `<span class="text-sky-300 font-bold">${item.ref_count}r</span>` : ''}
                    ${item.mcq_count > 0 ? `<span class="text-emerald-300 font-bold">${item.mcq_count}q</span>` : ''}
                  </div>
                </div>
              `).join('')}
              ${n.total_topics > topItems.length ? `
                <p class="text-[10px] text-slate-300/70 text-right pr-1">+${n.total_topics - topItems.length} more</p>
              ` : ''}
            </div>
          </div>

          <!-- Footer Summary -->
          <div class="pt-2 mt-2 border-t border-white/10 flex items-center justify-between text-[11px] text-slate-300 font-semibold">
            <span>${n.total_topics} Topics • ${n.total_refs} Refs • ${n.total_mcqs} MCQs</span>
            <span class="text-sky-300 group-hover:underline">Open →</span>
          </div>
        </div>
      `;
    }).join('');
  }

  async createNewNote() {
    try {
      const shadeIndex = Math.floor(Math.random() * 8);
      const res = await fetch('/api/notes/lecture-notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: 'Untitled Note',
          shade_index: shadeIndex,
          is_pinned: 0
        })
      });
      if (!res.ok) throw new Error('Create failed');
      const data = await res.json();
      await this.openNoteEditor(data.note.note_id);
    } catch (err) {
      console.error('[MyNotesController] Failed creating note:', err);
    }
  }

  async togglePin(noteId, currentPinned) {
    try {
      await fetch('/api/notes/lecture-notes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          note_id: noteId,
          is_pinned: currentPinned ? 0 : 1
        })
      });
      this.loadNotes();
    } catch (err) {
      console.error('[MyNotesController] Failed toggling pin:', err);
    }
  }

  async deleteNote(noteId) {
    try {
      await fetch(`/api/notes/lecture-notes/${encodeURIComponent(noteId)}`, {
        method: 'DELETE'
      });
      this.loadNotes();
    } catch (err) {
      console.error('[MyNotesController] Failed deleting note:', err);
    }
  }

  async openNoteEditor(noteId) {
    const gridView = this.container.querySelector('#notes-grid-view');
    const editorContainer = this.container.querySelector('#notes-editor-container');
    const workspaceEl = this.container.querySelector('#my-notes-editor-workspace');
    const rightDrawerEl = this.container.querySelector('#my-notes-right-sidebar');

    if (gridView) gridView.classList.add('hidden');
    if (editorContainer) editorContainer.classList.remove('hidden');

    this.isEditing = true;
    this.editor = new LectureNoteEditor(
      workspaceEl,
      rightDrawerEl,
      () => {
        // Callback to return to grid view
        this.isEditing = false;
        if (editorContainer) editorContainer.classList.add('hidden');
        if (gridView) gridView.classList.remove('hidden');
        this.loadNotes();
      },
      (customQuizParams) => {
        // Launch custom keyword quiz
        if (typeof window.startCustomTopicQuiz === 'function') {
          window.startCustomTopicQuiz(customQuizParams.keyword);
        } else if (window.switchTab) {
          window.switchTab('quiz-view');
        }
      },
      (refObj) => {
        // Open reference document
        const type = refObj._type || refObj.source_type || 'pdf';
        const target = refObj.file_path || refObj.path || refObj.title || '';
        const page = refObj.page || 1;
        if (typeof window.openLocalResource === 'function') {
          window.openLocalResource(type, target, page, { anchor: refObj.heading });
        }
      }
    );

    await this.editor.openNote(noteId);
  }
}

// Global initialization singleton helper
window.MyNotesController = MyNotesController;
export default MyNotesController;
