/**
 * Table of Contents Hierarchy Correction Controller (`ui/js/analysis_toc.js`).
 *
 * Requirements:
 * 1. Transition: Launched when clicking "Finish Layout Review".
 * 2. UI Workspace:
 *    - Left Pane: Displays rendered document page with boundary highlights.
 *    - Right Pane: Active nested layout list tree of all headings extracted
 *      throughout the document, referencing parent indices from DocumentHeadingTracker.
 * 3. Heading Re-Calibration Controls:
 *    - Clickable heading nodes highlight their boundary on the page layout view.
 *    - Interactive arrow buttons next to each tree item and keyboard ArrowLeft / ArrowRight
 *      to shift heading tiers (H1 through H6).
 *    - Changes dynamically restructure visual indentation depth and save instantly to SQLite.
 * 4. Final Ingestion & Export:
 *    - "Commit & Ingest" button calls POST /api/analysis/finalize to compile reading order,
 *      save markdown, update status to completed, and ingest to LanceDB.
 *    - Green validation checkmark is displayed next to the file row in the knowledge list.
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

    // Module State
    const tocState = {
        currentFilePath: "",
        currentPage: 1,
        totalPages: 1,
        pageDimensions: { width: 612.0, height: 792.0 },
        pageScale: 1.0,
        headings: [], // Array of heading items from /api/analysis/toc
        selectedHeadingId: null,
        isCommitting: false,
    };

    let dom = {};

    function cacheDOM() {
        dom = {
            screen: document.getElementById("layout-toc-screen"),
            layoutScreen: document.getElementById("layout-analysis-screen"),
            backToLayoutBtn: document.getElementById("toc-back-to-layout-btn"),
            fileBadge: document.getElementById("toc-file-badge"),
            commitIngestBtn: document.getElementById("btn-commit-ingest"),
            saveStatus: document.getElementById("toc-save-status"),
            prevPageBtn: document.getElementById("toc-prev-page"),
            nextPageBtn: document.getElementById("toc-next-page"),
            pageNumLabel: document.getElementById("toc-page-num"),
            canvasStage: document.getElementById("toc-canvas-stage"),
            canvas: document.getElementById("toc-page-canvas"),
            highlightLayer: document.getElementById("toc-highlight-layer"),
            headingCountBadge: document.getElementById("toc-heading-count"),
            treeContainer: document.getElementById("toc-tree-container"),
        };
    }

    // Public entry point to open TOC Validation
    async function openTocValidation(filePath, startPage = 1) {
        cacheDOM();
        if (!dom.screen) return;

        tocState.currentFilePath = filePath;
        tocState.currentPage = startPage || 1;
        tocState.selectedHeadingId = null;

        // Hide other views, activate TOC screen
        document.querySelectorAll(".view-section").forEach((sec) => sec.classList.remove("active"));
        dom.screen.classList.add("active");

        const fileName = filePath ? filePath.split(/[/\\]/).pop() : "Document";
        if (dom.fileBadge) {
            dom.fileBadge.textContent = fileName;
            dom.fileBadge.title = filePath;
        }

        bindEvents();
        await Promise.all([loadTocHeadings(), loadPagePreview(tocState.currentPage)]);
    }
    window.openTocValidation = openTocValidation;
    window.initTocWorkspace = function () {
        if (!tocState.currentFilePath) {
            const selectEl = document.getElementById("analysis-file-select");
            const fp = selectEl ? selectEl.value : "";
            if (fp) openTocValidation(fp, 1);
        }
    };

    function bindEvents() {
        if (dom.backToLayoutBtn) {
            dom.backToLayoutBtn.onclick = () => {
                if (dom.screen) dom.screen.classList.remove("active");
                if (dom.layoutScreen) dom.layoutScreen.classList.add("active");
                if (typeof window.initAnalysisWorkspace === "function") {
                    window.initAnalysisWorkspace();
                }
            };
        }

        if (dom.prevPageBtn) {
            dom.prevPageBtn.onclick = async () => {
                if (tocState.currentPage > 1) {
                    tocState.currentPage--;
                    await loadPagePreview(tocState.currentPage);
                }
            };
        }

        if (dom.nextPageBtn) {
            dom.nextPageBtn.onclick = async () => {
                if (tocState.currentPage < tocState.totalPages) {
                    tocState.currentPage++;
                    await loadPagePreview(tocState.currentPage);
                }
            };
        }

        if (dom.commitIngestBtn) {
            dom.commitIngestBtn.onclick = handleCommitAndIngest;
        }

        // Keyboard navigation for shifting heading tiers (ArrowLeft / ArrowRight)
        window.removeEventListener("keydown", handleKeyDown);
        window.addEventListener("keydown", handleKeyDown);
    }

    function handleKeyDown(e) {
        if (!dom.screen || !dom.screen.classList.contains("active")) return;
        if (!tocState.selectedHeadingId) return;

        // Prevent shifting when user is typing inside an input or textarea
        if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;

        if (e.key === "ArrowLeft") {
            e.preventDefault();
            shiftHeadingLevel(tocState.selectedHeadingId, -1);
        } else if (e.key === "ArrowRight") {
            e.preventDefault();
            shiftHeadingLevel(tocState.selectedHeadingId, 1);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            navigateHeadingSelection(-1);
        } else if (e.key === "ArrowDown") {
            e.preventDefault();
            navigateHeadingSelection(1);
        }
    }

    function navigateHeadingSelection(delta) {
        if (!tocState.headings.length) return;
        const currIdx = tocState.headings.findIndex((h) => h.id === tocState.selectedHeadingId);
        let nextIdx = 0;
        if (currIdx !== -1) {
            nextIdx = Math.max(0, Math.min(tocState.headings.length - 1, currIdx + delta));
        }
        const targetNode = tocState.headings[nextIdx];
        if (targetNode) {
            selectHeadingNode(targetNode.id);
            const el = document.getElementById(`toc-item-${targetNode.id}`);
            el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
    }

    // Load document TOC from backend
    async function loadTocHeadings() {
        if (!tocState.currentFilePath) return;

        try {
            const params = new URLSearchParams({ file_path: tocState.currentFilePath });
            const resp = await fetch(`/api/analysis/toc?${params.toString()}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            tocState.headings = data.headings || [];
            if (dom.headingCountBadge) {
                dom.headingCountBadge.textContent = `${tocState.headings.length} heading${tocState.headings.length === 1 ? "" : "s"}`;
            }

            renderHeadingsTree();

            // Default selection: First heading if available
            if (tocState.headings.length > 0 && !tocState.selectedHeadingId) {
                selectHeadingNode(tocState.headings[0].id);
            }
        } catch (err) {
            console.error("Failed to load document TOC:", err);
            if (dom.treeContainer) {
                dom.treeContainer.innerHTML = `
                    <div class="p-6 text-center text-muted">
                        <p class="text-xs">No headings detected for this document.</p>
                        <p class="text-[11px] text-gray-500 mt-1">Review layout boxes and tag headings in the layout editor.</p>
                    </div>
                `;
            }
        }
    }

    // Render interactive headings tree
    function renderHeadingsTree() {
        if (!dom.treeContainer) return;
        dom.treeContainer.innerHTML = "";

        if (!tocState.headings.length) {
            dom.treeContainer.innerHTML = `
                <div class="p-6 text-center text-muted">
                    <p class="text-xs font-semibold">No headings found</p>
                    <p class="text-[11px] text-gray-500 mt-1">Tag layout boxes as 'Heading' in the Layout Editor.</p>
                </div>
            `;
            return;
        }

        tocState.headings.forEach((h) => {
            const level = Math.max(1, Math.min(6, Number(h.heading_level || 1)));
            const isSelected = h.id === tocState.selectedHeadingId;

            const item = document.createElement("div");
            item.id = `toc-item-${h.id}`;
            item.className = `toc-node-item toc-tier-${level} p-2.5 rounded-xl flex items-center justify-between gap-2.5 cursor-pointer select-none ${
                isSelected ? "selected ring-1 ring-brand-500" : ""
            }`;

            const parentInfo = h.parent_index !== null && h.parent_index !== undefined
                ? `Parent #${h.parent_index + 1}`
                : "Top Level";

            item.innerHTML = `
                <div class="flex items-center gap-2.5 min-w-0 flex-1">
                    <span class="toc-badge toc-badge-h${level}">H${level}</span>
                    <div class="min-w-0 flex-1">
                        <p class="text-xs font-medium text-gray-100 truncate">${escapeHTML(h.title || "Untitled Heading")}</p>
                        <div class="flex items-center gap-2 text-[10px] text-muted">
                            <span class="font-mono">P.${h.page_number}</span>
                            <span>•</span>
                            <span class="truncate max-w-[150px]" title="${escapeHTML(h.breadcrumb || "")}">${escapeHTML(parentInfo)}</span>
                        </div>
                    </div>
                </div>

                <div class="flex items-center gap-1 shrink-0" onclick="event.stopPropagation()">
                    <button
                        type="button"
                        class="toc-arrow-btn"
                        data-id="${escapeHTML(h.id)}"
                        data-action="promote"
                        title="Promote tier (e.g. H2 -> H1)"
                        ${level <= 1 ? "disabled" : ""}
                    >&larr;</button>
                    <button
                        type="button"
                        class="toc-arrow-btn"
                        data-id="${escapeHTML(h.id)}"
                        data-action="demote"
                        title="Demote tier (e.g. H1 -> H2)"
                        ${level >= 6 ? "disabled" : ""}
                    >&rarr;</button>
                </div>
            `;

            // Node selection click handler
            item.addEventListener("click", () => {
                selectHeadingNode(h.id);
            });

            // Arrow button actions
            const promoteBtn = item.querySelector('[data-action="promote"]');
            const demoteBtn = item.querySelector('[data-action="demote"]');
            promoteBtn?.addEventListener("click", (e) => {
                e.stopPropagation();
                shiftHeadingLevel(h.id, -1);
            });
            demoteBtn?.addEventListener("click", (e) => {
                e.stopPropagation();
                shiftHeadingLevel(h.id, 1);
            });

            dom.treeContainer.appendChild(item);
        });
    }

    // Select heading node and highlight its boundary on the page canvas
    async function selectHeadingNode(headingId) {
        tocState.selectedHeadingId = headingId;

        // Update active class on tree nodes
        document.querySelectorAll(".toc-node-item").forEach((el) => {
            el.classList.toggle("selected", el.id === `toc-item-${headingId}`);
            el.classList.toggle("ring-1", el.id === `toc-item-${headingId}`);
            el.classList.toggle("ring-brand-500", el.id === `toc-item-${headingId}`);
        });

        const heading = tocState.headings.find((h) => h.id === headingId);
        if (!heading) return;

        // Switch to the target page if different
        if (heading.page_number && heading.page_number !== tocState.currentPage) {
            tocState.currentPage = heading.page_number;
            await loadPagePreview(tocState.currentPage);
        } else {
            renderHighlightBox(heading);
        }
    }

    // Shift heading tier level up or down (1 to 6) and save instantly
    async function shiftHeadingLevel(headingId, delta) {
        const heading = tocState.headings.find((h) => h.id === headingId);
        if (!heading) return;

        const currentLvl = Number(heading.heading_level || 1);
        const newLvl = Math.max(1, Math.min(6, currentLvl + delta));
        if (newLvl === currentLvl) return;

        heading.heading_level = newLvl;

        // Recalculate parent indices and breadcrumbs client-side for immediate visual response
        recalculateHierarchy();
        renderHeadingsTree();

        // Highlight updated selection
        selectHeadingNode(headingId);

        // Instant SQLite persistence
        try {
            showSaveStatus("Saving...");
            const resp = await fetch("/api/analysis/update-heading", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: headingId, heading_level: newLvl }),
            });

            if (resp.ok) {
                showSaveStatus("✓ Tier saved");
            } else {
                showSaveStatus("Save error");
            }
        } catch (err) {
            console.error("Failed to update heading level:", err);
            showSaveStatus("Network error");
        }
    }

    // Recalculate parent_index and breadcrumbs dynamically
    function recalculateHierarchy() {
        const stack = []; // [level, index, title]
        tocState.headings.forEach((h, idx) => {
            const lvl = Number(h.heading_level || 1);
            const title = h.title || `Heading ${idx + 1}`;

            while (stack.length > 0 && stack[stack.length - 1][0] >= lvl) {
                stack.pop();
            }

            h.index = idx;
            h.parent_index = stack.length > 0 ? stack[stack.length - 1][1] : null;
            h.breadcrumb = stack.length > 0
                ? stack.map((s) => s[2]).concat([title]).join(" > ")
                : title;

            stack.push([lvl, idx, title]);
        });
    }

    // Load page canvas preview for pageNum
    async function loadPagePreview(pageNum) {
        if (!tocState.currentFilePath) return;

        if (dom.pageNumLabel) {
            dom.pageNumLabel.textContent = `Page ${pageNum}`;
        }
        if (dom.prevPageBtn) {
            dom.prevPageBtn.disabled = pageNum <= 1;
        }

        try {
            const params = new URLSearchParams({
                file_path: tocState.currentFilePath,
                page_number: String(pageNum),
            });
            const resp = await fetch(`/api/analysis/page-data?${params.toString()}`);
            if (!resp.ok) return;
            const data = await resp.json();

            tocState.pageDimensions = data.dimensions || { width: 612.0, height: 792.0 };
            renderPageCanvas(data.page_image_base64);

            // Re-render highlight box if selected heading is on this page
            const selectedHeading = tocState.headings.find((h) => h.id === tocState.selectedHeadingId);
            if (selectedHeading && selectedHeading.page_number === pageNum) {
                renderHighlightBox(selectedHeading);
            } else if (dom.highlightLayer) {
                dom.highlightLayer.innerHTML = "";
            }
        } catch (err) {
            console.error("Failed to load page preview:", err);
        }
    }

    // Render Canvas background image or placeholder
    function renderPageCanvas(base64Image) {
        if (!dom.canvas || !dom.canvasStage) return;

        const pWidth = tocState.pageDimensions.width || 612.0;
        const pHeight = tocState.pageDimensions.height || 792.0;

        const maxWidth = Math.min(620, (dom.canvasStage.parentElement?.clientWidth || 650) - 40);
        const scale = maxWidth / pWidth;
        tocState.pageScale = scale;

        const stageWidth = Math.round(pWidth * scale);
        const stageHeight = Math.round(pHeight * scale);

        dom.canvas.width = stageWidth;
        dom.canvas.height = stageHeight;
        dom.canvas.style.width = `${stageWidth}px`;
        dom.canvas.style.height = `${stageHeight}px`;

        dom.canvasStage.style.width = `${stageWidth}px`;
        dom.canvasStage.style.height = `${stageHeight}px`;

        const ctx = dom.canvas.getContext("2d");
        if (!ctx) return;

        if (base64Image) {
            const img = new Image();
            img.onload = () => {
                ctx.clearRect(0, 0, stageWidth, stageHeight);
                ctx.drawImage(img, 0, 0, stageWidth, stageHeight);
            };
            img.src = `data:image/png;base64,${base64Image}`;
        } else {
            ctx.fillStyle = "#1e293b";
            ctx.fillRect(0, 0, stageWidth, stageHeight);
            ctx.fillStyle = "#64748b";
            ctx.font = "14px monospace";
            ctx.textAlign = "center";
            ctx.fillText(`Page ${tocState.currentPage}`, stageWidth / 2, stageHeight / 2);
        }
    }

    // Render boundary highlight box over selected heading node
    function renderHighlightBox(heading) {
        if (!dom.highlightLayer) return;
        dom.highlightLayer.innerHTML = "";

        const pWidth = tocState.pageDimensions.width || 612.0;
        const pHeight = tocState.pageDimensions.height || 792.0;
        const scale = tocState.pageScale || 1.0;

        let left = heading.x0;
        let top = heading.y0;
        let width = heading.x1 - heading.x0;
        let height = heading.y1 - heading.y0;

        // If normalized coordinates [0.0, 1.0]
        if (heading.x1 <= 1.0 && heading.y1 <= 1.0 && heading.x1 > 0) {
            left = heading.x0 * pWidth;
            top = heading.y0 * pHeight;
            width = (heading.x1 - heading.x0) * pWidth;
            height = (heading.y1 - heading.y0) * pHeight;
        }

        const box = document.createElement("div");
        box.className = "toc-highlight-box";
        box.style.left = `${Math.round(left * scale)}px`;
        box.style.top = `${Math.round(top * scale)}px`;
        box.style.width = `${Math.round(width * scale)}px`;
        box.style.height = `${Math.round(height * scale)}px`;

        dom.highlightLayer.appendChild(box);
    }

    // Commit & Ingest Engine: Invokes /api/analysis/finalize
    async function handleCommitAndIngest() {
        if (!tocState.currentFilePath || tocState.isCommitting) return;

        tocState.isCommitting = true;
        if (dom.commitIngestBtn) {
            dom.commitIngestBtn.disabled = true;
            dom.commitIngestBtn.innerHTML = `
                <svg class="animate-spin w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                </svg>
                <span>Finalizing &amp; Ingesting...</span>
            `;
        }

        try {
            showSaveStatus("Ingesting to LanceDB...");
            const resp = await fetch("/api/analysis/finalize", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    file_path: tocState.currentFilePath,
                    export_to_lancedb: true,
                }),
            });

            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            showSaveStatus("✓ Ingestion Complete!");

            // Mark file as validated in Knowledge Vault list
            if (typeof window.markFileValidatedInVault === "function") {
                window.markFileValidatedInVault(tocState.currentFilePath);
            }

            // Return to Knowledge Vault view after 1.5 seconds
            setTimeout(() => {
                if (typeof window.switchTab === "function") {
                    window.switchTab("vault-view");
                } else {
                    document.querySelectorAll(".view-section").forEach((s) => s.classList.remove("active"));
                    document.getElementById("vault-view")?.classList.add("active");
                }
            }, 1200);
        } catch (err) {
            console.error("Finalize ingestion failed:", err);
            showSaveStatus("Error during ingestion");
            alert("Failed to commit and ingest document: " + (err.message || err));
        } finally {
            tocState.isCommitting = false;
            if (dom.commitIngestBtn) {
                dom.commitIngestBtn.disabled = false;
                dom.commitIngestBtn.innerHTML = `
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                    <span>Commit &amp; Ingest</span>
                `;
            }
        }
    }

    function showSaveStatus(msg) {
        if (!dom.saveStatus) return;
        dom.saveStatus.textContent = msg;
        dom.saveStatus.classList.remove("hidden");
        setTimeout(() => {
            dom.saveStatus.classList.add("hidden");
        }, 2500);
    }
})();


// --- Phase 7 Undo/Redo TOC State History & DocumentFragment Tree Builder ---
(function () {
    const _tocUndoStack = [];
    const _tocRedoStack = [];

    window.pushTocHistorySnapshot = function (tocTree) {
        try {
            _tocUndoStack.push(JSON.stringify(tocTree || []));
            if (_tocUndoStack.length > 30) _tocUndoStack.shift();
            _tocRedoStack.length = 0;
        } catch (_err) {}
    };

    window.undoTocEdit = function (currentTree) {
        if (!_tocUndoStack.length) return null;
        try {
            _tocRedoStack.push(JSON.stringify(currentTree || []));
            return JSON.parse(_tocUndoStack.pop());
        } catch (_err) {
            return null;
        }
    };
})();

// Zero-Reflow TOC Observer & rAF Split-Pane Controller
class DossierTocController {
    constructor(dossierScrollContainer, tocListEl) {
        this.scrollContainer = dossierScrollContainer;
        this.tocListEl = tocListEl;
        this.observer = null;
        this.activeId = null;
    }

    mount() {
        this.disconnect();
        if (!this.scrollContainer || !this.tocListEl) return;
        const headings = Array.from(this.scrollContainer.querySelectorAll('h2, h3'));
        this.tocListEl.innerHTML = '';

        const fragment = document.createDocumentFragment();
        headings.forEach((heading, idx) => {
            if (!heading.id) {
                heading.id = `dossier-sec-${idx}`;
            }
            const li = document.createElement('li');
            li.className = heading.tagName === 'H3' ? 'pl-3 text-xs' : 'text-xs font-medium';
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.dataset.targetId = heading.id;
            btn.className = 'toc-item w-full text-left py-1 px-2 rounded transition text-slate-400 hover:text-slate-100';
            btn.textContent = heading.textContent;
            li.appendChild(btn);
            fragment.appendChild(li);
        });
        this.tocListEl.appendChild(fragment);

        this.observer = new IntersectionObserver(
            (entries) => {
                for (const entry of entries) {
                    if (entry.isIntersecting) {
                        this.setActiveHeading(entry.target.id);
                        break;
                    }
                }
            },
            {
                root: this.scrollContainer,
                rootMargin: '-10% 0px -75% 0px',
                threshold: 0
            }
        );

        headings.forEach((h) => this.observer.observe(h));

        this.tocListEl.onclick = (e) => {
            const btn = e.target.closest('button[data-target-id]');
            if (!btn) return;
            const target = this.scrollContainer.querySelector(`#${CSS.escape(btn.dataset.targetId)}`);
            target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        };
    }

    setActiveHeading(id) {
        if (this.activeId === id) return;
        this.activeId = id;
        this.tocListEl.querySelectorAll('.toc-item').forEach((btn) => {
            const isCurrent = btn.dataset.targetId === id;
            btn.classList.toggle('bg-sky-500/15', isCurrent);
            btn.classList.toggle('text-sky-400', isCurrent);
            btn.setAttribute('aria-current', isCurrent ? 'location' : 'false');
        });
    }

    disconnect() {
        if (this.observer) {
            this.observer.disconnect();
            this.observer = null;
        }
    }
}

function bindRafGatedSplitPane(splitContainerEl, handleSelector) {
    if (!window.interact || !splitContainerEl) return;
    let pendingWidthPercent = null;
    let rafScheduled = false;

    window.interact(handleSelector).draggable({
        axis: 'x',
        listeners: {
            start() {
                splitContainerEl.classList.add('select-none', 'pointer-events-none');
            },
            move(event) {
                const rect = splitContainerEl.getBoundingClientRect();
                const relX = Math.min(Math.max(event.clientX - rect.left, rect.width * 0.25), rect.width * 0.75);
                pendingWidthPercent = (relX / rect.width) * 100;

                if (!rafScheduled) {
                    rafScheduled = true;
                    requestAnimationFrame(() => {
                        if (pendingWidthPercent !== null) {
                            splitContainerEl.style.setProperty('--left-pane-width', `${pendingWidthPercent.toFixed(2)}%`);
                        }
                        rafScheduled = false;
                    });
                }
            },
            end() {
                splitContainerEl.classList.remove('select-none', 'pointer-events-none');
            }
        }
    });
}

window.DossierTocController = DossierTocController;
window.bindRafGatedSplitPane = bindRafGatedSplitPane;
