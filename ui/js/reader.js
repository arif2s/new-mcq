// ui/js/reader.js
/**
 * In-Browser Document Reader Controller (ZIM & Obsidian History Stack + Escape Key)
 * & Native PDF Parsing QA Engine (/api/sources/test_pdf_extract + /api/notes/qa_metrics)
 */

(function () {
    "use strict";

    const escapeHTML = (str) =>
        window.escapeHTML
            ? window.escapeHTML(str)
            : String(str ?? "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;")
                  .replace(/"/g, "&quot;")
                  .replace(/'/g, "&#039;");

    // =========================================================
    // 1. IN-BROWSER READER HISTORY & KEYBOARD SHORTCUTS
    // =========================================================
    const readerHistory = [];
    window._readerHistory = readerHistory;
    let isNavigatingBack = false;

    function enhanceDocumentReaderOverlay() {
        const viewerContainer = document.getElementById("zim-viewer-container");
        if (!viewerContainer) return;

        // Close reader modal on Escape key
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && viewerContainer.style.display === "flex") {
                if (typeof window.closeDocumentReader === "function") {
                    window.closeDocumentReader();
                } else {
                    viewerContainer.style.display = "none";
                    const contentArea = document.getElementById("zim-content-area");
                    if (contentArea) contentArea.innerHTML = "";
                    document.body.classList.remove("zim-reader-open");
                }
                readerHistory.length = 0;
            }
        });

        // Setup font size zoom controls (+/-) for Obsidian / ZIM document reader
        initReaderFontZoomControls();

        // Wrap window.openDocumentReader to support Back navigation across linked ZIM/Obsidian articles
        const baseOpenReader = window.openDocumentReader;
        if (typeof baseOpenReader === "function" && !baseOpenReader._enhancedByReaderJs) {
            const wrappedOpenReader = function (opts) {
                if (!isNavigatingBack && opts) {
                    readerHistory.push({ ...opts });
                }
                isNavigatingBack = false;
                baseOpenReader(opts);
                const contentArea = document.getElementById("zim-content-area");
                if (contentArea && (!opts || !opts.anchor)) {
                    contentArea.scrollTop = 0;
                    if (typeof contentArea.scrollTo === "function") {
                        contentArea.scrollTo({ top: 0, left: 0, behavior: "auto" });
                    }
                }
                renderReaderBackButton();
                applyReaderFontSize();
            };
            wrappedOpenReader._enhancedByReaderJs = true;
            window.openDocumentReader = wrappedOpenReader;
        }
    }

    const MIN_READER_FONT = 12;
    const MAX_READER_FONT = 26;
    const DEFAULT_READER_FONT = 15;
    let currentReaderFontSize = parseInt(localStorage.getItem('reader_font_size') || String(DEFAULT_READER_FONT), 10);
    if (isNaN(currentReaderFontSize) || currentReaderFontSize < MIN_READER_FONT || currentReaderFontSize > MAX_READER_FONT) {
        currentReaderFontSize = DEFAULT_READER_FONT;
    }

    function applyReaderFontSize() {
        const viewerContainer = document.getElementById("zim-viewer-container");
        if (viewerContainer) {
            viewerContainer.style.setProperty('--modal-reader-font-size', `${currentReaderFontSize}px`);
        }
        const label = document.getElementById("reader-font-size-label");
        if (label) {
            const pct = Math.round((currentReaderFontSize / DEFAULT_READER_FONT) * 100);
            label.textContent = `${pct}%`;
        }
        try {
            localStorage.setItem('reader_font_size', String(currentReaderFontSize));
        } catch (_) {}
    }

    function initReaderFontZoomControls() {
        const decBtn = document.getElementById("reader-font-decrease-btn");
        const incBtn = document.getElementById("reader-font-increase-btn");
        const label = document.getElementById("reader-font-size-label");

        if (decBtn && !decBtn._hasFontZoomListener) {
            decBtn._hasFontZoomListener = true;
            decBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                if (currentReaderFontSize > MIN_READER_FONT) {
                    currentReaderFontSize = Math.max(MIN_READER_FONT, currentReaderFontSize - 1);
                    applyReaderFontSize();
                }
            });
        }

        if (incBtn && !incBtn._hasFontZoomListener) {
            incBtn._hasFontZoomListener = true;
            incBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                if (currentReaderFontSize < MAX_READER_FONT) {
                    currentReaderFontSize = Math.min(MAX_READER_FONT, currentReaderFontSize + 1);
                    applyReaderFontSize();
                }
            });
        }

        if (label && !label._hasFontZoomListener) {
            label._hasFontZoomListener = true;
            label.addEventListener("click", (e) => {
                e.stopPropagation();
                currentReaderFontSize = DEFAULT_READER_FONT;
                applyReaderFontSize();
            });
        }

        applyReaderFontSize();
    }

    function renderReaderBackButton() {
        const viewerContainer = document.getElementById("zim-viewer-container");
        if (!viewerContainer) return;
        const header = viewerContainer.querySelector(".overlay-header");
        if (!header) return;

        let backBtn = document.getElementById("reader-history-back-btn");
        if (readerHistory.length <= 1) {
            if (backBtn) backBtn.style.display = "none";
            return;
        }

        if (!backBtn) {
            backBtn = document.createElement("button");
            backBtn.id = "reader-history-back-btn";
            backBtn.type = "button";
            backBtn.className = "secondary-btn px-3 py-1.5 text-xs font-semibold rounded-lg mr-3 cursor-pointer";
            backBtn.textContent = "← Previous Article";
            backBtn.addEventListener("click", () => {
                if (readerHistory.length > 1) {
                    readerHistory.pop(); // Remove current
                    const prev = readerHistory[readerHistory.length - 1];
                    if (prev && typeof window.openDocumentReader === "function") {
                        isNavigatingBack = true;
                        window.openDocumentReader(prev);
                    }
                }
            });
            header.insertBefore(backBtn, header.firstChild);
        }
        backBtn.style.display = "inline-flex";
    }

    // =========================================================
    // 2. NATIVE PDF PARSING QA ENGINE (Scoped to #qa-mount-point)
    // =========================================================
    function initPdfParsingQA(customMount = null) {
        const mountPoint = customMount || document.getElementById("qa-mount-point");
        // Strictly guard: never append to document.body if #qa-mount-point is not on the page
        if (!mountPoint) return;

        mountPoint.innerHTML = `
            <div class="glass-card p-6 rounded-2xl border border-gray-700/50 space-y-4">
                <div class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-700/50 pb-3">
                    <div>
                        <h3 class="text-base font-bold">🔬 PDF Extraction &amp; Layout QA Engine</h3>
                        <p class="text-xs text-muted">Compare PyMuPDF (Fitz) baseline text against Docling / Heuristic Markdown and compute Levenshtein &amp; token retention metrics.</p>
                    </div>
                    <button type="button" id="qa-btn-metrics" class="primary-btn px-4 py-2 rounded-xl text-xs font-bold cursor-pointer">
                        ⚡ Calculate QA Metrics
                    </button>
                </div>

                <div class="grid md:grid-cols-2 gap-4">
                    <div class="space-y-1.5">
                        <label class="text-xs font-semibold text-muted uppercase tracking-wider block">
                            Original PDF Baseline (Raw Fitz Text)
                        </label>
                        <textarea id="qa-original-text" rows="7"
                                  class="w-full glass-input rounded-xl p-3 text-xs font-mono leading-relaxed focus:border-brand-500 outline-none"
                                  placeholder="Paste original baseline PDF text here..."></textarea>
                    </div>

                    <div class="space-y-1.5">
                        <label class="text-xs font-semibold text-muted uppercase tracking-wider block">
                            Parsed Structured Markdown (Docling / Headings)
                        </label>
                        <textarea id="qa-parsed-text" rows="7"
                                  class="w-full glass-input rounded-xl p-3 text-xs font-mono leading-relaxed focus:border-brand-500 outline-none"
                                  placeholder="Paste parsed Markdown text here..."></textarea>
                    </div>
                </div>

                <div id="qa-metrics-output" class="glass-panel rounded-xl p-4 border border-gray-700/50 text-xs text-muted">
                    Enter or extract text above and click <strong>Calculate QA Metrics</strong> to inspect character ratio, token ratio, and Levenshtein edit distance.
                </div>
            </div>
        `;

        const btnMetrics = document.getElementById("qa-btn-metrics");
        const outDiv = document.getElementById("qa-metrics-output");

        btnMetrics?.addEventListener("click", async () => {
            const origText = document.getElementById("qa-original-text")?.value || "";
            const parsedText = document.getElementById("qa-parsed-text")?.value || "";

            if (!origText.trim() && !parsedText.trim()) {
                if (outDiv) {
                    outDiv.innerHTML = `<span class="text-warning-400">Please provide text in at least one pane before computing QA metrics.</span>`;
                }
                return;
            }

            btnMetrics.disabled = true;
            btnMetrics.textContent = "Computing...";

            try {
                const response = await fetch("/api/notes/qa_metrics", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        original_text: origText,
                        parsed_text: parsedText,
                    }),
                });
                const data = await response.json();

                if (outDiv) {
                    outDiv.innerHTML = `
                        <div class="grid grid-cols-3 gap-3 text-center">
                            <div class="glass-card p-3 rounded-lg">
                                <span class="text-[10px] text-muted uppercase block">Character Ratio</span>
                                <span class="text-base font-bold text-brand-300 font-mono">${Number(data.char_ratio || 0).toFixed(4)}</span>
                            </div>
                            <div class="glass-card p-3 rounded-lg">
                                <span class="text-[10px] text-muted uppercase block">Token Ratio</span>
                                <span class="text-base font-bold text-emerald-400 font-mono">${Number(data.token_ratio || 0).toFixed(4)}</span>
                            </div>
                            <div class="glass-card p-3 rounded-lg">
                                <span class="text-[10px] text-muted uppercase block">Levenshtein Distance</span>
                                <span class="text-base font-bold text-amber-300 font-mono px-2 py-0.5 rounded bg-amber-500/15 border border-amber-500/30">${Number(data.levenshtein_distance || 0).toLocaleString()}</span>
                            </div>
                        </div>
                    `;
                }
            } catch (e) {
                console.error("QA Metrics Error:", e);
                if (outDiv) {
                    outDiv.innerHTML = `<span class="text-danger-400">Error calculating metrics: ${escapeHTML(e.message)}</span>`;
                }
            } finally {
                btnMetrics.disabled = false;
                btnMetrics.textContent = "⚡ Calculate QA Metrics";
            }
        });
    }

    window.initPdfParsingQA = initPdfParsingQA;

    document.addEventListener("DOMContentLoaded", () => {
        enhanceDocumentReaderOverlay();
        initPdfParsingQA();
    });
})();

// --- Phase 7 Safe Reader Markdown Hydration & Selection Lookup Hook ---
(function () {
    window.renderReaderContentSafely = function (markdownText, containerEl, anchorId = null) {
        let renderedHtml = "";
        if (typeof window.renderSafeMarkdown === "function") {
            renderedHtml = window.renderSafeMarkdown(markdownText, containerEl);
        } else if (containerEl) {
            containerEl.textContent = markdownText || "";
        }

        if (anchorId) {
            setTimeout(() => {
                const cleanId = String(anchorId).trim().replace(/^#/, "");
                const targetHeading = document.getElementById(cleanId);
                if (targetHeading && typeof targetHeading.scrollIntoView === "function") {
                    targetHeading.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            }, 60);
        }
        return renderedHtml;
    };

    // Global listener for interactive Obsidian wikilinks inside in-browser document reader
    document.addEventListener("click", (e) => {
        const link = e.target?.closest?.("a.obsidian-wikilink");
        if (!link) return;
        const targetNote = link.dataset.noteTarget;
        const book = link.dataset.book || "";
        const anchor = link.dataset.anchor || link.getAttribute("href")?.replace(/^#/, "") || "";

        if (anchor && (!targetNote || targetNote === "#")) {
            e.preventDefault();
            const targetHeading = document.getElementById(anchor);
            if (targetHeading && typeof targetHeading.scrollIntoView === "function") {
                targetHeading.scrollIntoView({ behavior: "smooth", block: "start" });
            }
            return;
        }

        if (targetNote && typeof window.openLocalResource === "function") {
            e.preventDefault();
            window.openLocalResource("obsidian", targetNote, targetNote, { book, anchor });
        }
    });
})();
