/**
 * ui/js/settings.js - Settings & Offline Resource Controller
 *
 * Manages:
 * 1. App appearance, glassmorphic opacities, and wallpaper rotation timers.
 * 2. LLM synthesis toggles and Obsidian viewer/native launcher preferences.
 * 3. CPU embedding & FlashRank cross-encoder offline downloads.
 * 4. SciSpaCy biomedical NER pipeline selector, installer, and progress tracking.
 * 5. Offline ontology linkers (UMLS, MeSH, RxNorm, HPO, GO) detection & streaming SQLite indexer.
 * 6. System diagnostics (LM Studio, SQLite WAL, SumatraPDF, Tantivy, Vendor Cache).
 */

(function () {
  "use strict";

  // Polling Intervals
  let _scispacyPollTimer = null;
  let _indexerPollTimer = null;

  // DOM Helper
  const $ = (id) => document.getElementById(id);
  const escapeHTML = (str) =>
    window.escapeHTML
      ? window.escapeHTML(str)
      : String(str || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#039;");

  /* =========================================================================
   * 1. State Synchronization & General Settings
   * ========================================================================= */

  function initGeneralSettings() {
    const state = window.AppState ? window.AppState.get() : {};

    // LLM Synthesis Toggle
    const llmToggle = $("setting-llm-enabled");
    if (llmToggle) {
      llmToggle.checked = state.llmEnabled !== false && !state.skipLMStudio;
      llmToggle.addEventListener("change", (e) => {
        const enabled = e.target.checked;
        window.AppState.update({
          llmEnabled: enabled,
          skipLMStudio: !enabled,
        });
      });
    }

    // Obsidian Open Mode (In-Browser Reader vs obsidian:// Protocol)
    const obsidianSelect = $("setting-obsidian-mode");
    if (obsidianSelect) {
      obsidianSelect.value = state.obsidianOpenMode || "browser";
      obsidianSelect.addEventListener("change", (e) => {
        window.AppState.update({ obsidianOpenMode: e.target.value });
      });
    }

    // Panel Opacity Slider (--panel-alpha)
    const panelSlider = $("setting-panel-alpha");
    const panelVal = $("setting-panel-alpha-val");
    if (panelSlider) {
      const alpha = state.panelOpacity !== undefined ? state.panelOpacity : (state.panelAlpha !== undefined ? state.panelAlpha : 0.78);
      panelSlider.value = alpha;
      if (panelVal) panelVal.textContent = Math.round(alpha * 100) + "%";

      panelSlider.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        if (panelVal) panelVal.textContent = Math.round(val * 100) + "%";
        if (window.AppState && window.AppState.applyAppearance) {
          window.AppState.applyAppearance({ ...window.AppState.get(), panelOpacity: val, panelAlpha: val });
        }
      });
      panelSlider.addEventListener("change", (e) => {
        const val = parseFloat(e.target.value);
        window.AppState.update({ panelOpacity: val, panelAlpha: val });
      });
    }

    // Card Opacity Slider (--card-alpha)
    const cardSlider = $("setting-card-alpha");
    const cardVal = $("setting-card-alpha-val");
    if (cardSlider) {
      const alpha = state.cardOpacity !== undefined ? state.cardOpacity : (state.cardAlpha !== undefined ? state.cardAlpha : 0.82);
      cardSlider.value = alpha;
      if (cardVal) cardVal.textContent = Math.round(alpha * 100) + "%";

      cardSlider.addEventListener("input", (e) => {
        const val = parseFloat(e.target.value);
        if (cardVal) cardVal.textContent = Math.round(val * 100) + "%";
        if (window.AppState && window.AppState.applyAppearance) {
          window.AppState.applyAppearance({ ...window.AppState.get(), cardOpacity: val, cardAlpha: val });
        }
      });
      cardSlider.addEventListener("change", (e) => {
        const val = parseFloat(e.target.value);
        window.AppState.update({ cardOpacity: val, cardAlpha: val });
      });
    }

    // Wallpaper Timer Interval
    const wpTimerSelect = $("setting-wallpaper-timer");
    if (wpTimerSelect) {
      wpTimerSelect.value = state.wallpaperIntervalMinutes || "30";
      wpTimerSelect.addEventListener("change", (e) => {
        window.AppState.update({
          wallpaperIntervalMinutes: parseInt(e.target.value, 10),
        });
      });
    }

    // Rotate Wallpaper Now Button
    const btnRotateWp = $("btn-rotate-wallpaper-now");
    if (btnRotateWp) {
      btnRotateWp.addEventListener("click", () => {
        if (window.changeWallpaperNow) {
          window.changeWallpaperNow();
        }
      });
    }
  }

  /* =========================================================================
   * 2. CPU Embedding & FlashRank Model Management
   * ========================================================================= */

  async function loadEmbeddingModels() {
    const container = $("embedding-models-list");
    if (!container) return;

    try {
      const res = await fetch("/api/models/cpu_embedding_models");
      if (!res.ok) throw new Error("Failed to load embedding models");
      const data = await res.json();

      container.innerHTML = (data.models || [])
        .map((m) => {
          const isDownloaded = m.downloaded;
          return `
          <div class="flex items-center justify-between p-3.5 rounded-xl border border-gray-700/50 glass-panel mb-2.5 transition-all">
            <div class="flex-1 pr-4">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-sm text-neutral-100">${escapeHTML(m.name)}</span>
                ${m.default ? '<span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">Default</span>' : ""}
              </div>
              <p class="text-xs text-neutral-400 mt-0.5">Dim: ${m.dimension}d | Size: ~${m.size_mb} MB</p>
            </div>
            <div>
              ${
                isDownloaded
                  ? '<span class="inline-flex items-center gap-1.5 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg font-medium"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg> Ready Offline</span>'
                  : `<button data-id="${escapeHTML(m.id)}" data-type="embedding" class="btn-download-model text-xs px-3.5 py-1.5 rounded-lg font-medium bg-emerald-600 hover:bg-emerald-500 text-white transition-colors flex items-center gap-1.5 shadow-sm">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
                      Download
                    </button>`
              }
            </div>
          </div>
        `;
        })
        .join("");

      _bindDownloadButtons(container, loadEmbeddingModels);
    } catch (err) {
      container.innerHTML = `<div class="text-xs text-rose-400 p-2">Error loading embedding models: ${escapeHTML(err.message)}</div>`;
    }
  }

  async function loadFlashrankModels() {
    const container = $("flashrank-models-list");
    if (!container) return;

    try {
      const res = await fetch("/api/models/flashrank_models");
      if (!res.ok) throw new Error("Failed to load FlashRank models");
      const data = await res.json();

      container.innerHTML = (data.models || [])
        .map((m) => {
          const isDownloaded = m.downloaded;
          return `
          <div class="flex items-center justify-between p-3.5 rounded-xl border border-gray-700/50 bg-white/5 backdrop-blur-md mb-2.5 transition-all">
            <div class="flex-1 pr-4">
              <div class="flex items-center gap-2">
                <span class="font-semibold text-sm text-neutral-100">${escapeHTML(m.name)}</span>
                ${m.default ? '<span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30">Default</span>' : ""}
              </div>
              <p class="text-xs text-neutral-400 mt-0.5">Size: ~${m.size_mb} MB</p>
            </div>
            <div>
              ${
                isDownloaded
                  ? '<span class="inline-flex items-center gap-1.5 text-xs text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-lg font-medium"><svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg> Ready Offline</span>'
                  : `<button data-id="${escapeHTML(m.id)}" data-type="flashrank" class="btn-download-model text-xs px-3.5 py-1.5 rounded-lg font-medium bg-emerald-600 hover:bg-emerald-500 text-white transition-colors flex items-center gap-1.5 shadow-sm">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
                      Download
                    </button>`
              }
            </div>
          </div>
        `;
        })
        .join("");

      _bindDownloadButtons(container, loadFlashrankModels);
    } catch (err) {
      container.innerHTML = `<div class="text-xs text-rose-400 p-2">Error loading FlashRank models: ${escapeHTML(err.message)}</div>`;
    }
  }

  function _bindDownloadButtons(container, refreshFn) {
    container.querySelectorAll(".btn-download-model").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const modelId = btn.getAttribute("data-id");
        const modelType = btn.getAttribute("data-type");

        btn.disabled = true;
        btn.innerHTML = `<svg class="animate-spin w-3.5 h-3.5 text-white inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg> Downloading...`;

        try {
          const res = await fetch("/api/models/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model_type: modelType, model_name: modelId }),
          });
          const result = await res.json();
          if (!res.ok) throw new Error(result.detail || "Download failed");
          refreshFn();
        } catch (err) {
          alert(`Download failed: ${err.message}`);
          btn.disabled = false;
          btn.textContent = "Retry Download";
        }
      });
    });
  }

  /* =========================================================================
   * 3. SciSpaCy Biomedical NER Pipeline Selector & Downloader
   * ========================================================================= */

  let _scispacyModelsCache = [];

  async function loadScispacyModels() {
    const selectEl = $("select-scispacy-model");
    const detailsEl = $("scispacy-model-details");
    const statusBadge = $("scispacy-model-status-badge");
    const downloadBtn = $("btn-download-scispacy");

    if (!selectEl) return;

    try {
      const res = await fetch("/api/models/scispacy_models");
      if (!res.ok) throw new Error("Failed to query SciSpaCy models");
      const data = await res.json();
      _scispacyModelsCache = data.models || [];

      // Populate Select Dropdown
      const previousVal = selectEl.value;
      selectEl.innerHTML = _scispacyModelsCache
        .map((m) => {
          const star = m.recommended ? "⭐ " : "";
          const installedText = m.installed ? " (Installed)" : "";
          return `<option value="${escapeHTML(m.id)}">${star}${escapeHTML(m.name)}${installedText}</option>`;
        })
        .join("");

      if (previousVal && _scispacyModelsCache.some((m) => m.id === previousVal)) {
        selectEl.value = previousVal;
      } else {
        const rec = _scispacyModelsCache.find((m) => m.recommended);
        if (rec) selectEl.value = rec.id;
      }

      renderSelectedScispacyModel();
    } catch (err) {
      if (detailsEl) {
        detailsEl.innerHTML = `<span class="text-xs text-rose-400">Failed loading SciSpaCy pipelines: ${escapeHTML(err.message)}</span>`;
      }
    }
  }

  function renderSelectedScispacyModel() {
    const selectEl = $("select-scispacy-model");
    const detailsEl = $("scispacy-model-details");
    const statusBadge = $("scispacy-model-status-badge");
    const downloadBtn = $("btn-download-scispacy");
    const progressContainer = $("scispacy-download-progress-container");

    if (!selectEl || !_scispacyModelsCache.length) return;
    const selectedId = selectEl.value;
    const model = _scispacyModelsCache.find((m) => m.id === selectedId);
    if (!model) return;

    if (detailsEl) {
      detailsEl.innerHTML = `
        <div class="text-xs text-neutral-300 mb-1 leading-relaxed">${escapeHTML(model.description)}</div>
        <div class="flex items-center gap-3 text-[11px] text-neutral-400">
          <span>📦 Package: <b>~${model.size_mb} MB</b></span>
          <span>⚡ RAM Footprint: <b>~${model.ram_mb} MB</b></span>
          <span>🏷️ Version: <b>${escapeHTML(model.version)}</b></span>
        </div>
      `;
    }

    if (model.installed) {
      if (statusBadge) {
        statusBadge.className =
          "px-2.5 py-1 rounded-md text-xs font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1.5";
        statusBadge.innerHTML = `<svg class="w-3.5 h-3.5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg> Installed & Ready`;
      }
      if (downloadBtn) {
        downloadBtn.disabled = true;
        downloadBtn.className =
          "text-xs px-4 py-2 rounded-lg font-medium bg-white/10 text-neutral-400 cursor-not-allowed flex items-center gap-1.5";
        downloadBtn.textContent = "Pipeline Ready";
      }
      if (progressContainer) progressContainer.classList.add("hidden");
    } else {
      if (statusBadge) {
        statusBadge.className =
          "px-2.5 py-1 rounded-md text-xs font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30";
        statusBadge.textContent = "Not Downloaded";
      }
      if (downloadBtn) {
        downloadBtn.disabled = false;
        downloadBtn.className =
          "text-xs px-4 py-2 rounded-lg font-medium bg-emerald-600 hover:bg-emerald-500 text-white transition-colors flex items-center gap-1.5 shadow-md cursor-pointer";
        downloadBtn.innerHTML = `
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          Download & Install Offline
        `;
      }
    }
  }

  function initScispacyEvents() {
    const selectEl = $("select-scispacy-model");
    if (selectEl) {
      selectEl.addEventListener("change", renderSelectedScispacyModel);
    }

    const downloadBtn = $("btn-download-scispacy");
    if (downloadBtn) {
      downloadBtn.addEventListener("click", async () => {
        const selectEl = $("select-scispacy-model");
        if (!selectEl) return;
        const modelId = selectEl.value;

        downloadBtn.disabled = true;
        downloadBtn.innerHTML = `<svg class="animate-spin w-4 h-4 text-white inline mr-1" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg> Starting...`;

        try {
          const res = await fetch("/api/models/scispacy/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model_type: "scispacy", model_name: modelId }),
          });
          const result = await res.json();
          if (!res.ok) throw new Error(result.detail || "Download initialization failed");

          startScispacyPolling(modelId);
        } catch (err) {
          alert(`SciSpaCy Download Error: ${err.message}`);
          renderSelectedScispacyModel();
        }
      });
    }
  }

  function startScispacyPolling(modelId) {
    if (_scispacyPollTimer) clearInterval(_scispacyPollTimer);

    const progressContainer = $("scispacy-download-progress-container");
    const progressBar = $("scispacy-download-progress-bar");
    const progressLabel = $("scispacy-download-progress-label");
    const progressMsg = $("scispacy-download-progress-msg");

    if (progressContainer) progressContainer.classList.remove("hidden");

    _scispacyPollTimer = setInterval(async () => {
      try {
        const res = await fetch(`/api/models/scispacy/download_status?model_name=${encodeURIComponent(modelId)}`);
        if (!res.ok) return;
        const data = await res.json();

        const pct = Math.max(0, Math.min(100, Math.round(data.progress || 0)));
        if (progressBar) progressBar.style.width = pct + "%";
        if (progressLabel) progressLabel.textContent = pct + "%";
        if (progressMsg) progressMsg.textContent = data.message || "Downloading package...";

        if (data.status === "completed") {
          clearInterval(_scispacyPollTimer);
          _scispacyPollTimer = null;
          await loadScispacyModels();
        } else if (data.status === "failed") {
          clearInterval(_scispacyPollTimer);
          _scispacyPollTimer = null;
          alert(`SciSpaCy installation failed: ${data.message || data.error}`);
          await loadScispacyModels();
        }
      } catch (e) {
        console.warn("Polling SciSpaCy status error:", e);
      }
    }, 1000);
  }

  /* =========================================================================
   * 4. Offline Ontology Linkers (UMLS, MeSH, RxNorm, HPO, GO) Controller
   * ========================================================================= */

  async function loadLinkersStatus() {
    const listContainer = $("ontology-sources-list");
    const dbCard = $("ontology-db-summary");
    const buildBtn = $("btn-build-ontology-index");

    if (!listContainer) return;

    try {
      const res = await fetch("/api/models/linkers/status");
      if (!res.ok) throw new Error("Failed to load ontology linkers status");
      const data = await res.json();

      const ontologies = data.ontologies || {};
      const kbs = ["umls", "mesh", "rxnorm", "hpo", "go"];

      // Render Detected Ontologies Cards with Checkboxes
      listContainer.innerHTML = kbs
        .map((key) => {
          const info = ontologies[key] || {};
          const detected = Boolean(info.detected);
          const hasAliases = Boolean(info.has_concept_aliases);
          const hasJsonl = Boolean(info.jsonl_path);
          const hasSparse = Boolean(info.has_sparse_matrix);

          return `
          <div class="p-3 rounded-xl border ${detected ? "border-gray-700/50 glass-panel" : "border-rose-500/20 bg-rose-500/5"} flex flex-col justify-between">
            <div>
              <div class="flex items-center justify-between mb-2">
                <label class="inline-flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" class="ontology-kb-checkbox rounded glass-input text-emerald-500 focus:ring-0" value="${key}" ${detected ? "checked" : "disabled"}>
                  <span class="font-bold text-sm text-neutral-100 uppercase tracking-wide">${key}</span>
                </label>
                <span class="text-[10px] font-semibold px-2 py-0.5 rounded ${detected ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30" : "glass-panel text-neutral-500 border border-gray-700/50"}">
                  ${detected ? `${info.size_mb} MB` : "Missing"}
                </span>
              </div>
              <div class="space-y-1 text-[11px] text-neutral-400">
                <div class="flex items-center gap-1.5">
                  <span class="${hasAliases ? "text-emerald-400" : "text-neutral-500"}">${hasAliases ? "✓" : "✗"}</span>
                  <span>concept_aliases.json</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <span class="${hasJsonl ? "text-emerald-400" : "text-neutral-500"}">${hasJsonl ? "✓" : "✗"}</span>
                  <span>Definitions (.jsonl)</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <span class="${hasSparse ? "text-emerald-400" : "text-neutral-500"}">${hasSparse ? "✓" : "✗"}</span>
                  <span>TF-IDF Vectors (.npz)</span>
                </div>
              </div>
            </div>
          </div>
        `;
        })
        .join("");

      // Render Semantic Type Tree Status
      const tuiInfo = ontologies.semantic_type_tree || {};
      const tuiBadge = $("tui-tree-status-badge");
      if (tuiBadge) {
        tuiBadge.className = tuiInfo.detected
          ? "text-[11px] text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20"
          : "text-[11px] text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20";
        tuiBadge.textContent = tuiInfo.detected ? "✓ TUI Filter Tree Present" : "⚠️ TUI Tree Missing";
      }

      // Render Compiled SQLite Status
      const db = data.database || {};
      if (dbCard) {
        if (db.exists && db.concepts_count > 0) {
          dbCard.innerHTML = `
            <div class="flex items-center justify-between text-xs mb-1">
              <span class="text-neutral-300 font-semibold flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                Compiled SQLite Index Ready
              </span>
              <span class="text-neutral-400 font-mono text-[11px]">${db.size_mb} MB</span>
            </div>
            <div class="flex items-center gap-4 text-[11px] text-neutral-400 font-mono">
              <span>Concepts: <b class="text-neutral-200">${db.concepts_count.toLocaleString()}</b></span>
              <span>Aliases: <b class="text-neutral-200">${db.aliases_count.toLocaleString()}</b></span>
            </div>
          `;
        } else {
          dbCard.innerHTML = `
            <div class="text-xs text-amber-300 font-medium mb-0.5">⚡ Ontology Database Not Compiled</div>
            <div class="text-[11px] text-neutral-400">Compile the detected S3 linkers to enable 0-RAM, sub-millisecond UMLS synonym resolution.</div>
          `;
        }
      }

      if (buildBtn) {
        buildBtn.disabled = data.total_ontologies_detected === 0;
      }
    } catch (err) {
      if (listContainer) {
        listContainer.innerHTML = `<div class="text-xs text-rose-400 col-span-3">Failed to inspect linker folders: ${escapeHTML(err.message)}</div>`;
      }
    }
  }

  function initLinkersEvents() {
    const buildBtn = $("btn-build-ontology-index");
    if (!buildBtn) return;

    buildBtn.addEventListener("click", async () => {
      const selectedKbs = [];
      document.querySelectorAll(".ontology-kb-checkbox:checked").forEach((cb) => {
        selectedKbs.push(cb.value);
      });

      if (!selectedKbs.length) {
        alert("Please select at least one detected ontology to index.");
        return;
      }

      const forceRebuild = Boolean($("checkbox-force-rebuild-ontology")?.checked);
      if (forceRebuild && !confirm("Rebuilding from scratch will re-stream all JSONL and alias files. Proceed?")) {
        return;
      }

      buildBtn.disabled = true;
      buildBtn.innerHTML = `<svg class="animate-spin w-4 h-4 text-white inline mr-1" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg> Starting Compiler...`;

      try {
        const res = await fetch("/api/models/linkers/build_index", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kbs: selectedKbs, force_rebuild: forceRebuild }),
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.detail || "Compiler trigger failed");

        startIndexerPolling();
      } catch (err) {
        alert(`Indexing error: ${err.message}`);
        buildBtn.disabled = false;
        buildBtn.textContent = "⚡ Build / Update Offline Ontology Index";
      }
    });
  }

  function startIndexerPolling() {
    if (_indexerPollTimer) clearInterval(_indexerPollTimer);

    const progressContainer = $("ontology-index-progress-container");
    const progressBar = $("ontology-index-progress-bar");
    const progressLabel = $("ontology-index-progress-label");
    const stageLabel = $("ontology-index-stage-label");
    const msgLabel = $("ontology-index-progress-msg");
    const buildBtn = $("btn-build-ontology-index");

    if (progressContainer) progressContainer.classList.remove("hidden");

    _indexerPollTimer = setInterval(async () => {
      try {
        const res = await fetch("/api/models/linkers/build_progress");
        if (!res.ok) return;
        const data = await res.json();

        const pct = Math.max(0, Math.min(100, Math.round(data.progress || 0)));
        if (progressBar) progressBar.style.width = pct + "%";
        if (progressLabel) progressLabel.textContent = pct + "%";
        if (stageLabel) stageLabel.textContent = data.current_stage || "Processing...";
        if (msgLabel) msgLabel.textContent = data.message || "Streaming concepts into SQLite...";

        if (data.status === "completed") {
          clearInterval(_indexerPollTimer);
          _indexerPollTimer = null;
          if (buildBtn) {
            buildBtn.disabled = false;
            buildBtn.textContent = "⚡ Build / Update Offline Ontology Index";
          }
          await loadLinkersStatus();
        } else if (data.status === "failed") {
          clearInterval(_indexerPollTimer);
          _indexerPollTimer = null;
          alert(`Ontology indexing failed: ${data.error || data.message}`);
          if (buildBtn) {
            buildBtn.disabled = false;
            buildBtn.textContent = "⚡ Build / Update Offline Ontology Index";
          }
          await loadLinkersStatus();
        }
      } catch (e) {
        console.warn("Polling ontology indexer error:", e);
      }
    }, 1200);
  }

  /* =========================================================================
   * 5. Comprehensive Offline System Diagnostics
   * ========================================================================= */

  async function loadDiagnostics() {
    const refreshBtn = $("btn-refresh-diagnostics");
    if (refreshBtn) {
      refreshBtn.disabled = true;
      refreshBtn.classList.add("opacity-60");
    }

    try {
      const res = await fetch("/api/models/diagnostics/all");
      if (!res.ok) throw new Error("Failed to load diagnostic telemetry");
      const data = await res.json();

      // 1. LM Studio Diagnostic Badge & Details
      const lmEl = $("diag-lm-studio-status");
      if (lmEl) {
        const lm = data.lm_studio || {};
        if (lm.online) {
          const modelName = lm.active_model ? escapeHTML(lm.active_model) : "No Model Loaded";
          lmEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span class="font-semibold text-emerald-300">Online (${lm.latency_ms} ms)</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1 truncate">Model: <b class="text-neutral-200">${modelName}</b></div>
          `;
        } else {
          lmEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-rose-500"></span>
              <span class="font-semibold text-rose-300">Offline</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1">LM Studio Server not reachable on :1234</div>
          `;
        }
      }

      // 2. Database SQLite Diagnostic
      const dbEl = $("diag-db-status");
      if (dbEl) {
        const db = data.database || {};
        if (db.status === "ok") {
          const qs = db.row_counts?.csv_questions || 0;
          const sess = db.row_counts?.quiz_sessions || 0;
          dbEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
              <span class="font-semibold text-emerald-300">WAL Active (${db.size_mb} MB)</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1">Questions: <b class="text-neutral-200">${qs.toLocaleString()}</b> | Sessions: <b class="text-neutral-200">${sess.toLocaleString()}</b></div>
          `;
        } else {
          dbEl.innerHTML = `<span class="text-rose-400 text-xs font-semibold">Database Error</span>`;
        }
      }

      // 3. SumatraPDF Integration
      const sumatraEl = $("diag-sumatra-status");
      if (sumatraEl) {
        const sumatra = data.sumatrapdf || {};
        if (sumatra.installed) {
          sumatraEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
              <span class="font-semibold text-emerald-300">Installed</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1 truncate" title="${escapeHTML(sumatra.path)}">${escapeHTML(sumatra.path)}</div>
          `;
        } else {
          sumatraEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
              <span class="font-semibold text-amber-300">Not Found</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1">Falls back to in-browser layout reader</div>
          `;
        }
      }

      // 4. Tantivy BM25 Lexical Engine
      const tantivyEl = $("diag-tantivy-status");
      if (tantivyEl) {
        const tantivy = data.tantivy || {};
        if (tantivy.status === "ok") {
          tantivyEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
              <span class="font-semibold text-emerald-300">Indexed (${tantivy.size_mb} MB)</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1">Indexed Documents: <b class="text-neutral-200">${(tantivy.doc_count || 0).toLocaleString()}</b></div>
          `;
        } else {
          tantivyEl.innerHTML = `
            <div class="flex items-center gap-2">
              <span class="w-2.5 h-2.5 rounded-full bg-neutral-500"></span>
              <span class="font-semibold text-neutral-400">Empty Index</span>
            </div>
            <div class="text-[11px] text-neutral-400 mt-1">Add vault sources in Knowledge Vault</div>
          `;
        }
      }

      // 5. Vendor Assets Offline Readiness Check
      const vendorRes = await fetch("/api/state/vendor_status");
      if (vendorRes.ok) {
        const vData = await vendorRes.json();
        const vendorEl = $("diag-vendor-status");
        if (vendorEl) {
          if (vData.offline_ready) {
            vendorEl.innerHTML = `
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-emerald-400"></span>
                <span class="font-semibold text-emerald-300">100% Cached</span>
              </div>
              <div class="text-[11px] text-neutral-400 mt-1">Tailwind, Marked, KaTeX cached in ui/vendor/</div>
            `;
          } else {
            vendorEl.innerHTML = `
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
                <span class="font-semibold text-amber-300">Partial Cache</span>
              </div>
              <div class="text-[11px] text-neutral-400 mt-1">Some vendor libraries loading from CDN</div>
            `;
          }
        }
      }
    } catch (err) {
      console.warn("Diagnostics telemetry error:", err);
    } finally {
      if (refreshBtn) {
        refreshBtn.disabled = false;
        refreshBtn.classList.remove("opacity-60");
      }
    }
  }

  // Expose global refresh function to window for cross-file contracts
  window.refreshDiagnostics = loadDiagnostics;

  /* =========================================================================
   * 6. Master Lifecycle Initialization
   * ========================================================================= */

  function initSettings() {
    initGeneralSettings();
    loadEmbeddingModels();
    loadFlashrankModels();
    loadScispacyModels();
    initScispacyEvents();
    loadLinkersStatus();
    initLinkersEvents();
    loadDiagnostics();

    const refreshDiagBtn = $("btn-refresh-diagnostics");
    if (refreshDiagBtn) {
      refreshDiagBtn.addEventListener("click", loadDiagnostics);
    }
  }

  // Hook into DOM ready or tab switches
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSettings);
  } else {
    initSettings();
  }

  // Re-sync UI inputs when AppState updates externally
  document.addEventListener("stateUpdated", () => {
    initGeneralSettings();
  });
})();
