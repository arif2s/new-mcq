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
    let isNavigatingBack = false;

    function enhanceDocumentReaderOverlay() {
        const viewerContainer = document.getElementById("zim-viewer-container");
        if (!viewerContainer) return;

        // Close reader modal on Escape key
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && viewerContainer.style.display === "flex") {
                viewerContainer.style.display = "none";
                const contentArea = document.getElementById("zim-content-area");
                if (contentArea) contentArea.innerHTML = "";
                readerHistory.length = 0;
            }
        });

        // Wrap window.openDocumentReader to support Back navigation across linked ZIM/Obsidian articles
        const baseOpenReader = window.openDocumentReader;
        if (typeof baseOpenReader === "function" && !baseOpenReader._enhancedByReaderJs) {
            const wrappedOpenReader = function (opts) {
                if (!isNavigatingBack && opts) {
                    readerHistory.push({ ...opts });
                }
                isNavigatingBack = false;
                baseOpenReader(opts);
                renderReaderBackButton();
            };
            wrappedOpenReader._enhancedByReaderJs = true;
            window.openDocumentReader = wrappedOpenReader;
        }
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