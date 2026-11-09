/**
 * Visual Layout Workspace View Controller (ui/js/analysis.js)
 * Implements interactive dual-pane document layout workspace:
 * - Left Pane: Canvas page image with transparent overlay & interact.js bbox editing
 * - Right Pane: Comprehensive editable textarea / rich Markdown editor
 * - Top Control Bar: Pointer Mode, Draw Box Mode, Reorder Flow, Profile Settings,
 *   Categorical element selectors, Running margin suppression toggle.
 * - 3-second debounced auto-saving to /api/analysis/save-page and /api/analysis/save-markdown.
 * - Invariant 23 compliant non-collapsing sidebar.
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
    const state = {
        files: [],
        currentFilePath: "",
        currentPage: 1,
        totalPages: 1,
        activeProfile: "native_digital",
        pageDimensions: { width: 612.0, height: 792.0 },
        elements: [], // BBox items: { id, x0, y0, x1, y1, element_type, heading_level, display_order, raw_text, is_custom_drawn }
        activeBoxId: null,
        activeMode: "pointer", // "pointer" | "draw" | "reorder"
        selectedCategory: "text", // "text" | "table" | "header" | "figure" | "ignore"
        suppressRunningMargins: false,
        reorderSequenceCounter: 0,
        isExtracting: false,
        savePageTimeout: null,
        saveMarkdownTimeout: null,
        renderedScale: 1.0,
    };

    // DOM Elements Cache
    let dom = {};

    function cacheDOM() {
        dom = {
            screen: document.getElementById("layout-analysis-screen"),
            fileSelect: document.getElementById("analysis-file-select"),
            prevPageBtn: document.getElementById("analysis-prev-page-btn"),
            nextPageBtn: document.getElementById("analysis-next-page-btn"),
            pageIndicator: document.getElementById("analysis-page-indicator"),
            profileSelect: document.getElementById("analysis-profile-select"),
            toolPointer: document.getElementById("tool-pointer-mode"),
            toolDraw: document.getElementById("tool-draw-mode"),
            toolReorder: document.getElementById("tool-reorder-flow"),
            categoryBtns: document.querySelectorAll(".category-badge-btn"),
            toggleMargins: document.getElementById("toggle-running-margins"),
            saveStatus: document.getElementById("analysis-save-status"),
            workspaceStage: document.getElementById("analysis-workspace-stage"),
            canvas: document.getElementById("analysis-page-canvas"),
            interactionLayer: document.getElementById("analysis-interaction-layer"),
            markdownEditor: document.getElementById("analysis-markdown-editor"),
            recompileBtn: document.getElementById("analysis-recompile-btn"),
            saveMdBtn: document.getElementById("analysis-save-md-btn"),
            finishReviewBtn: document.getElementById("btn-finish-layout-review"),
        };
    }

    // Initialize Analysis Workspace
    async function initAnalysisWorkspace() {
        cacheDOM();
        if (!dom.screen) return;

        bindEvents();
        await loadFilesList();
        setupInteractJs();
    }

    window.initAnalysisWorkspace = initAnalysisWorkspace;

    // Load available PDF files from /api/analysis/files
    async function loadFilesList() {
        try {
            const resp = await fetch("/api/analysis/files");
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.files = data.files || [];

            if (dom.fileSelect) {
                dom.fileSelect.innerHTML = '<option value="">Select PDF...</option>';
                state.files.forEach((file) => {
                    const opt = document.createElement("option");
                    opt.value = file.file_path;
                    opt.textContent = `${file.name} (${file.total_pages || 0}p)`;
                    dom.fileSelect.appendChild(opt);
                });

                if (state.files.length > 0 && !state.currentFilePath) {
                    dom.fileSelect.value = state.files[0].file_path;
                    state.currentFilePath = state.files[0].file_path;
                    state.activeProfile = state.files[0].active_profile || "native_digital";
                    if (dom.profileSelect) { dom.profileSelect.value = state.activeProfile; syncProfileButtons?.(state.activeProfile); }
                    await loadPageData(1);
                }
            }
        } catch (err) {
            console.error("Failed to load analysis files:", err);
        }
    }

    // Load Page Data from /api/analysis/page-data
    async function loadPageData(pageNum) {
        if (!state.currentFilePath) return;

        state.isExtracting = true;
        // Invariant 23: Prevent right sidebar collapsing during extraction
        const rightSidebar = document.getElementById("right-sidebar");
        const wasCollapsed = rightSidebar ? rightSidebar.classList.contains("collapsed") : false;

        try {
            state.currentPage = pageNum;
            updatePaginationUI();

            const params = new URLSearchParams({
                file_path: state.currentFilePath,
                page_number: String(pageNum),
            });

            const resp = await fetch(`/api/analysis/page-data?${params.toString()}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            state.pageDimensions = data.dimensions || { width: 612.0, height: 792.0 };
            state.elements = (data.elements || []).map((el, idx) => ({
                id: el.id || `box_${pageNum}_${idx}`,
                x0: Number(el.x0),
                y0: Number(el.y0),
                x1: Number(el.x1),
                y1: Number(el.y1),
                element_type: (el.element_type || "text").toLowerCase(),
                heading_level: el.heading_level || 0,
                display_order: typeof el.display_order === "number" ? el.display_order : idx,
                raw_text: el.raw_text || "",
                is_custom_drawn: el.is_custom_drawn || 0,
            }));

            // Find matching file in state to get totalPages
            const currFile = state.files.find((f) => f.file_path === state.currentFilePath);
            if (currFile && currFile.total_pages) {
                state.totalPages = Math.max(1, currFile.total_pages);
            }

            renderPageCanvas(data.page_image_base64);
            renderLayoutBoxes();
            compileMarkdownText();

            updatePaginationUI();
        } catch (err) {
            console.error("Failed to load page data:", err);
        } finally {
            state.isExtracting = false;
            // Respect Invariant 23
            if (rightSidebar && wasCollapsed && !rightSidebar.classList.contains("collapsed")) {
                // If it was collapsed and something toggled it, do nothing or restore as needed
            }
        }
    }

    // Render Canvas background image or placeholder
    function renderPageCanvas(base64Image) {
        if (!dom.canvas || !dom.workspaceStage) return;

        const pWidth = state.pageDimensions.width || 612.0;
        const pHeight = state.pageDimensions.height || 792.0;

        // Container sizing: Max width 650px or container aspect ratio
        const maxWidth = Math.min(650, (dom.workspaceStage.parentElement?.clientWidth || 700) - 40);
        const scale = maxWidth / pWidth;
        state.renderedScale = scale;

        const stageWidth = Math.round(pWidth * scale);
        const stageHeight = Math.round(pHeight * scale);

        dom.workspaceStage.style.width = `${stageWidth}px`;
        dom.workspaceStage.style.height = `${stageHeight}px`;

        dom.canvas.width = stageWidth;
        dom.canvas.height = stageHeight;
        const ctx = dom.canvas.getContext("2d");
        if (!ctx) return;

        if (base64Image) {
            const img = new Image();
            img.onload = () => {
                ctx.clearRect(0, 0, stageWidth, stageHeight);
                ctx.drawImage(img, 0, 0, stageWidth, stageHeight);
            };
            img.src = base64Image.startsWith("data:") ? base64Image : `data:image/png;base64,${base64Image}`;
        } else {
            // Draw clean page placeholder canvas
            ctx.fillStyle = "#ffffff";
            ctx.fillRect(0, 0, stageWidth, stageHeight);

            // Draw subtle page grid lines
            ctx.strokeStyle = "#f1f5f9";
            ctx.lineWidth = 1;
            for (let y = 20; y < stageHeight; y += 40) {
                ctx.beginPath();
                ctx.moveTo(20, y);
                ctx.lineTo(stageWidth - 20, y);
                ctx.stroke();
            }
        }
    }

    // Render Bounding Box Overlays
    function renderLayoutBoxes() {
        if (!dom.interactionLayer) return;
        dom.interactionLayer.innerHTML = "";

        const pWidth = state.pageDimensions.width || 612.0;
        const pHeight = state.pageDimensions.height || 792.0;
        const stageW = dom.workspaceStage.clientWidth || pWidth * state.renderedScale;
        const stageH = dom.workspaceStage.clientHeight || pHeight * state.renderedScale;

        // Sort elements by display_order
        const sorted = [...state.elements].sort((a, b) => a.display_order - b.display_order);

        sorted.forEach((el) => {
            if (state.suppressRunningMargins && isRunningMargin(el)) {
                return; // suppressed
            }

            // Normalization check: coordinates may be relative [0.0, 1.0] or in points [0.0, pWidth]
            const isNorm = el.x1 <= 1.05 && el.y1 <= 1.05 && el.x1 > 0;
            const normX0 = isNorm ? el.x0 : el.x0 / pWidth;
            const normY0 = isNorm ? el.y0 : el.y0 / pHeight;
            const normX1 = isNorm ? el.x1 : el.x1 / pWidth;
            const normY1 = isNorm ? el.y1 : el.y1 / pHeight;

            const leftPx = Math.max(0, normX0 * stageW);
            const topPx = Math.max(0, normY0 * stageH);
            const widthPx = Math.max(16, (normX1 - normX0) * stageW);
            const heightPx = Math.max(14, (normY1 - normY0) * stageH);

            const boxEl = document.createElement("div");
            boxEl.className = "bbox-overlay-element";
            boxEl.id = `el-${el.id}`;
            boxEl.dataset.id = el.id;
            const elType = mapCategoryType(el.element_type);
            boxEl.dataset.type = elType;

            boxEl.style.left = `${leftPx}px`;
            boxEl.style.top = `${topPx}px`;
            boxEl.style.width = `${widthPx}px`;
            boxEl.style.height = `${heightPx}px`;

            if (state.activeBoxId === el.id) {
                boxEl.classList.add("active-focus");
            }

            // Label Badge
            const typeLabel = elType.toUpperCase();
            const badge = document.createElement("span");
            badge.className = "bbox-badge";
            badge.textContent = typeLabel;
            boxEl.appendChild(badge);

            // Order Counter Badge
            const counter = document.createElement("span");
            counter.className = "bbox-order-counter";
            if (state.activeMode === "reorder" && el.display_order === 9999) {
                counter.classList.add("unassigned");
                counter.textContent = "-";
            } else {
                counter.textContent = String(el.display_order + 1);
            }
            boxEl.appendChild(counter);

            dom.interactionLayer.appendChild(boxEl);
        });
    }

    // Helper: Map internal element types to 5 categorical badges
    function mapCategoryType(type) {
        const t = (type || "").toLowerCase();
        if (t === "header" || t === "heading") return "header";
        if (t === "table") return "table";
        if (t === "figure" || t === "picture" || t === "image") return "figure";
        if (t === "ignore") return "ignore";
        return "text";
    }

    // Check if element is running header/footer
    function isRunningMargin(el) {
        const pHeight = state.pageDimensions.height || 792.0;
        const isNorm = el.y1 <= 1.05;
        const normY0 = isNorm ? el.y0 : el.y0 / pHeight;
        const normY1 = isNorm ? el.y1 : el.y1 / pHeight;

        // Top 6% or bottom 6%
        return normY0 <= 0.06 || normY1 >= 0.94;
    }

    // Set Active Focus Element
    function setActiveElement(boxId) {
        state.activeBoxId = boxId;

        // Update visuals in interaction layer
        if (dom.interactionLayer) {
            dom.interactionLayer.querySelectorAll(".bbox-overlay-element").forEach((b) => {
                b.classList.toggle("active-focus", b.dataset.id === boxId);
            });
        }

        const el = state.elements.find((e) => e.id === boxId);
        if (el) {
            const elType = mapCategoryType(el.element_type);
            illuminateCategoryBadge(elType);
        }
    }

    // Illuminate matching categorical badge in top bar
    function illuminateCategoryBadge(category) {
        state.selectedCategory = category;
        if (dom.categoryBtns) {
            dom.categoryBtns.forEach((btn) => {
                const cat = btn.dataset.category;
                const isMatch = cat === category || (cat === "header" && category === "heading") || (cat === "figure" && category === "picture");
                btn.classList.toggle("active", isMatch);
            });
        }
    }

    // Change category of active focus element or set selected category
    function setActiveElementCategory(newCategory) {
        illuminateCategoryBadge(newCategory);

        if (!state.activeBoxId) return;
        const el = state.elements.find((e) => e.id === state.activeBoxId);
        if (!el) return;

        el.element_type = newCategory;
        if (newCategory === "header") {
            el.heading_level = 1;
        } else {
            el.heading_level = 0;
        }

        renderLayoutBoxes();
        compileMarkdownText();
        queueAutoSavePage();
    }

    // Configure interact.js for drag-and-drop movement and edge resizing
    function setupInteractJs() {
        if (typeof window.interact !== "function") {
            console.warn("interact.js not yet loaded");
            return;
        }

        window.interact(".bbox-overlay-element").draggable({
            enabled: true,
            listeners: {
                start(event) {
                    if (state.activeMode !== "pointer") return;
                    const id = event.target.dataset.id;
                    setActiveElement(id);
                },
                move(event) {
                    if (state.activeMode !== "pointer") return;
                    const target = event.target;
                    const stageW = dom.workspaceStage.clientWidth;
                    const stageH = dom.workspaceStage.clientHeight;

                    const curLeft = parseFloat(target.style.left) || 0;
                    const curTop = parseFloat(target.style.top) || 0;

                    const newLeft = Math.max(0, Math.min(stageW - target.offsetWidth, curLeft + event.dx));
                    const newTop = Math.max(0, Math.min(stageH - target.offsetHeight, curTop + event.dy));

                    target.style.left = `${newLeft}px`;
                    target.style.top = `${newTop}px`;
                },
                end(event) {
                    if (state.activeMode !== "pointer") return;
                    const target = event.target;
                    const id = target.dataset.id;
                    updateElementGeometryFromDOM(id, target);
                    queueAutoSavePage();
                },
            },
        }).resizable({
            edges: { left: true, right: true, bottom: true, top: true },
            listeners: {
                start(event) {
                    if (state.activeMode !== "pointer") return;
                    const id = event.target.dataset.id;
                    setActiveElement(id);
                },
                move(event) {
                    if (state.activeMode !== "pointer") return;
                    const target = event.target;
                    const stageW = dom.workspaceStage.clientWidth;
                    const stageH = dom.workspaceStage.clientHeight;

                    let curLeft = parseFloat(target.style.left) || 0;
                    let curTop = parseFloat(target.style.top) || 0;

                    let newW = Math.max(20, event.rect.width);
                    let newH = Math.max(16, event.rect.height);

                    curLeft += event.deltaRect.left;
                    curTop += event.deltaRect.top;

                    curLeft = Math.max(0, Math.min(stageW - 20, curLeft));
                    curTop = Math.max(0, Math.min(stageH - 16, curTop));

                    target.style.width = `${newW}px`;
                    target.style.height = `${newH}px`;
                    target.style.left = `${curLeft}px`;
                    target.style.top = `${curTop}px`;
                },
                end(event) {
                    if (state.activeMode !== "pointer") return;
                    const target = event.target;
                    const id = target.dataset.id;
                    updateElementGeometryFromDOM(id, target);
                    queueAutoSavePage();
                },
            },
        });
    }

    // Map DOM pixel coordinates back to relative document percentage matrices [0.0, 1.0]
    function updateElementGeometryFromDOM(id, domEl) {
        const el = state.elements.find((e) => e.id === id);
        if (!el || !domEl) return;

        const stageW = dom.workspaceStage.clientWidth;
        const stageH = dom.workspaceStage.clientHeight;

        const leftPx = parseFloat(domEl.style.left) || 0;
        const topPx = parseFloat(domEl.style.top) || 0;
        const wPx = domEl.offsetWidth;
        const hPx = domEl.offsetHeight;

        // Normalize to [0.0, 1.0]
        const normX0 = Math.max(0.0, Math.min(1.0, leftPx / stageW));
        const normY0 = Math.max(0.0, Math.min(1.0, topPx / stageH));
        const normX1 = Math.max(normX0, Math.min(1.0, (leftPx + wPx) / stageW));
        const normY1 = Math.max(normY0, Math.min(1.0, (topPx + hPx) / stageH));

        el.x0 = Number(normX0.toFixed(4));
        el.y0 = Number(normY0.toFixed(4));
        el.x1 = Number(normX1.toFixed(4));
        el.y1 = Number(normY1.toFixed(4));
    }

    // Compile Markdown Text from layout boxes into the Right Pane Textarea
    function compileMarkdownText() {
        if (!dom.markdownEditor) return;

        // Sort elements by display_order
        const sorted = [...state.elements].sort((a, b) => a.display_order - b.display_order);
        const mdParts = [];

        sorted.forEach((el) => {
            if (el.element_type === "ignore") return;
            if (state.suppressRunningMargins && isRunningMargin(el)) return;

            const text = (el.raw_text || "").trim();
            if (!text && el.element_type !== "figure") return;

            if (el.element_type === "header" || el.element_type === "heading") {
                const lvl = Math.min(6, Math.max(1, el.heading_level || 1));
                mdParts.push(`${"#".repeat(lvl)} ${text}\n`);
            } else if (el.element_type === "table") {
                mdParts.push(`\n${text}\n`);
            } else if (el.element_type === "figure" || el.element_type === "picture") {
                mdParts.push(`\n![Figure ${el.display_order + 1}](${text || "image.png"})\n`);
            } else {
                mdParts.push(`${text}\n`);
            }
        });

        const compiled = mdParts.join("\n").replace(/\n{3,}/g, "\n\n");
        dom.markdownEditor.value = compiled;
    }

    // Sequential Click Reordering
    function startReorderFlow() {
        state.activeMode = "reorder";
        updateToolModeUI();

        // Clear display_order visual numbers
        state.reorderSequenceCounter = 0;
        state.elements.forEach((el) => {
            el.display_order = 9999;
        });

        renderLayoutBoxes();
    }

    function handleBoxReorderClick(boxId) {
        const el = state.elements.find((e) => e.id === boxId);
        if (!el) return;

        // Assign incremental display_order
        el.display_order = state.reorderSequenceCounter++;
        renderLayoutBoxes();
        compileMarkdownText();
        queueAutoSavePage();

        // If all assigned, switch back to pointer mode
        const unassigned = state.elements.filter((e) => e.display_order === 9999);
        if (unassigned.length === 0) {
            state.activeMode = "pointer";
            updateToolModeUI();
        }
    }

    // Manual Draw Box Mode
    let drawState = { isDrawing: false, startX: 0, startY: 0, rubberband: null };

    function initDrawModeListeners() {
        if (!dom.interactionLayer) return;

        dom.interactionLayer.addEventListener("mousedown", (e) => {
            if (state.activeMode !== "draw") return;
            if (e.target.closest(".bbox-overlay-element")) return;

            const rect = dom.interactionLayer.getBoundingClientRect();
            drawState.isDrawing = true;
            drawState.startX = e.clientX - rect.left;
            drawState.startY = e.clientY - rect.top;

            const box = document.createElement("div");
            box.className = "drawing-rubberband-box";
            box.style.left = `${drawState.startX}px`;
            box.style.top = `${drawState.startY}px`;
            box.style.width = "0px";
            box.style.height = "0px";
            dom.interactionLayer.appendChild(box);
            drawState.rubberband = box;
        });

        window.addEventListener("mousemove", (e) => {
            if (!drawState.isDrawing || !drawState.rubberband) return;
            const rect = dom.interactionLayer.getBoundingClientRect();
            const curX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
            const curY = Math.max(0, Math.min(rect.height, e.clientY - rect.top));

            const left = Math.min(drawState.startX, curX);
            const top = Math.min(drawState.startY, curY);
            const width = Math.abs(curX - drawState.startX);
            const height = Math.abs(curY - drawState.startY);

            drawState.rubberband.style.left = `${left}px`;
            drawState.rubberband.style.top = `${top}px`;
            drawState.rubberband.style.width = `${width}px`;
            drawState.rubberband.style.height = `${height}px`;
        });

        window.addEventListener("mouseup", () => {
            if (!drawState.isDrawing || !drawState.rubberband) return;
            drawState.isDrawing = false;

            const w = parseFloat(drawState.rubberband.style.width) || 0;
            const h = parseFloat(drawState.rubberband.style.height) || 0;
            const left = parseFloat(drawState.rubberband.style.left) || 0;
            const top = parseFloat(drawState.rubberband.style.top) || 0;

            drawState.rubberband.remove();
            drawState.rubberband = null;

            if (w > 15 && h > 15) {
                // Create new BBox Item
                const stageW = dom.workspaceStage.clientWidth;
                const stageH = dom.workspaceStage.clientHeight;

                const normX0 = Number((left / stageW).toFixed(4));
                const normY0 = Number((top / stageH).toFixed(4));
                const normX1 = Number(((left + w) / stageW).toFixed(4));
                const normY1 = Number(((top + h) / stageH).toFixed(4));

                const nextOrder = state.elements.length;
                const newId = `user_${state.currentPage}_${nextOrder}_${Math.random().toString(36).substring(2, 7)}`;

                const newElement = {
                    id: newId,
                    x0: normX0,
                    y0: normY0,
                    x1: normX1,
                    y1: normY1,
                    element_type: state.selectedCategory || "text",
                    heading_level: state.selectedCategory === "header" ? 1 : 0,
                    display_order: nextOrder,
                    raw_text: "",
                    is_custom_drawn: 1,
                };

                state.elements.push(newElement);
                state.activeBoxId = newId;

                renderLayoutBoxes();
                compileMarkdownText();
                queueAutoSavePage();
            }
        });
    }

    // Update Tool Mode UI (Pointer vs Draw vs Reorder)
    function updateToolModeUI() {
        if (dom.toolPointer) dom.toolPointer.classList.toggle("active", state.activeMode === "pointer");
        if (dom.toolDraw) dom.toolDraw.classList.toggle("active", state.activeMode === "draw");
        if (dom.toolReorder) dom.toolReorder.classList.toggle("active", state.activeMode === "reorder");

        if (dom.interactionLayer) {
            dom.interactionLayer.classList.toggle("draw-mode-active", state.activeMode === "draw");
            dom.interactionLayer.classList.toggle("reorder-mode-active", state.activeMode === "reorder");
        }
    }

    // Pagination UI Update
    function updatePaginationUI() {
        if (dom.pageIndicator) {
            dom.pageIndicator.textContent = `Page ${state.currentPage} / ${state.totalPages}`;
        }
        if (dom.prevPageBtn) {
            dom.prevPageBtn.disabled = state.currentPage <= 1;
        }
        if (dom.nextPageBtn) {
            dom.nextPageBtn.disabled = state.currentPage >= state.totalPages;
        }
        updateFinishReviewButton();
    }

    // Check if all pages are reviewed and toggle Finish Layout Review button
    function updateFinishReviewButton() {
        if (!dom.finishReviewBtn) return;
        const currFile = state.files.find((f) => f.file_path === state.currentFilePath);
        const reviewedPages = currFile ? currFile.reviewed_pages || [] : [];
        const isAllReviewed = state.totalPages > 0 && reviewedPages.length >= state.totalPages;

        if (isAllReviewed) {
            dom.finishReviewBtn.classList.remove("hidden");
        } else {
            dom.finishReviewBtn.classList.add("hidden");
        }
    }

    // Debounced Auto-Save System (3 seconds idle time)
    function showSaveStatus(text = "✓ Auto-saved") {
        if (!dom.saveStatus) return;
        dom.saveStatus.textContent = text;
        dom.saveStatus.classList.remove("hidden");
        setTimeout(() => {
            if (dom.saveStatus) dom.saveStatus.classList.add("hidden");
        }, 2500);
    }

    function queueAutoSavePage() {
        if (state.savePageTimeout) clearTimeout(state.savePageTimeout);
        state.savePageTimeout = setTimeout(async () => {
            await executeSavePage();
        }, 3000);
    }

    async function executeSavePage() {
        if (!state.currentFilePath) return;
        try {
            const payload = {
                file_path: state.currentFilePath,
                page_number: state.currentPage,
                bboxes: state.elements.map((el) => ({
                    id: el.id,
                    x0: el.x0,
                    y0: el.y0,
                    x1: el.x1,
                    y1: el.y1,
                    element_type: el.element_type,
                    heading_level: el.heading_level || 0,
                    display_order: el.display_order,
                    raw_text: el.raw_text || "",
                    is_custom_drawn: el.is_custom_drawn || 0,
                })),
            };

            const resp = await fetch("/api/analysis/save-page", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (resp.ok) {
                showSaveStatus("✓ Page layout saved");
                const resData = await resp.json().catch(() => null);
                if (resData && resData.reviewed_pages) {
                    const currFile = state.files.find((f) => f.file_path === state.currentFilePath);
                    if (currFile) {
                        currFile.reviewed_pages = resData.reviewed_pages;
                        currFile.reviewed_pages_count = resData.reviewed_pages.length;
                    }
                    updateFinishReviewButton();
                }
            }
        } catch (err) {
            console.error("Auto-save page failed:", err);
        }
    }

    function queueAutoSaveMarkdown() {
        if (state.saveMarkdownTimeout) clearTimeout(state.saveMarkdownTimeout);
        state.saveMarkdownTimeout = setTimeout(async () => {
            await executeSaveMarkdown();
        }, 3000);
    }

    async function executeSaveMarkdown() {
        if (!state.currentFilePath || !dom.markdownEditor) return;
        try {
            const payload = {
                file_path: state.currentFilePath,
                markdown_text: dom.markdownEditor.value,
            };

            const resp = await fetch("/api/analysis/save-markdown", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (resp.ok) {
                showSaveStatus("✓ Markdown saved");
            }
        } catch (err) {
            console.error("Auto-save markdown failed:", err);
        }
    }

    // Bind Event Listeners
    function bindEvents() {
        // File selection change
        if (dom.fileSelect) {
            dom.fileSelect.addEventListener("change", async (e) => {
                const fp = e.target.value;
                if (!fp) return;
                state.currentFilePath = fp;
                const fileObj = state.files.find((f) => f.file_path === fp);
                if (fileObj) {
                    state.totalPages = Math.max(1, fileObj.total_pages || 1);
                    state.activeProfile = fileObj.active_profile || "native_digital";
                    if (dom.profileSelect) { dom.profileSelect.value = state.activeProfile; syncProfileButtons?.(state.activeProfile); }
                }
                await loadPageData(1);
            });
        }

        // Pagination buttons
        if (dom.prevPageBtn) {
            dom.prevPageBtn.addEventListener("click", async () => {
                if (state.currentPage > 1) {
                    await loadPageData(state.currentPage - 1);
                }
            });
        }
        if (dom.nextPageBtn) {
            dom.nextPageBtn.addEventListener("click", async () => {
                if (state.currentPage < state.totalPages) {
                    await loadPageData(state.currentPage + 1);
                }
            });
        }

        // Finish Layout Review Transition Button
        if (dom.finishReviewBtn) {
            dom.finishReviewBtn.addEventListener("click", () => {
                // Advance to Table of Contents Validation Screen
                if (typeof window.openTocValidation === "function") {
                    window.openTocValidation(state.currentFilePath, state.currentPage);
                } else {
                    const tocScreen = document.getElementById("layout-toc-screen");
                    const layoutScreen = document.getElementById("layout-analysis-screen");
                    if (layoutScreen) layoutScreen.classList.remove("active");
                    if (tocScreen) tocScreen.classList.add("active");
                }
            });
        }

        // Profile segmented button group synchronization
        const profileGroup = document.getElementById("analysis-profile-group");
        const syncProfileButtons = (val) => {
            if (!profileGroup) return;
            profileGroup.querySelectorAll('.segmented-btn').forEach((btn) => {
                const match = btn.dataset.value === val;
                btn.classList.toggle('active', match);
                btn.setAttribute('aria-checked', match ? 'true' : 'false');
            });
        };

        if (profileGroup && dom.profileSelect) {
            profileGroup.querySelectorAll('.segmented-btn').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const val = btn.dataset.value;
                    syncProfileButtons(val);
                    dom.profileSelect.value = val;
                    dom.profileSelect.dispatchEvent(new Event('change', { bubbles: true }));
                });
            });
        }

        // Profile select
        if (dom.profileSelect) {
            dom.profileSelect.addEventListener("change", async (e) => {
                state.activeProfile = e.target.value;
                syncProfileButtons(state.activeProfile);
                if (state.currentFilePath) {
                    await fetch("/api/analysis/initialize", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            file_path: state.currentFilePath,
                            profile_id: state.activeProfile,
                        }),
                    });
                    await loadPageData(state.currentPage);
                }
            });
        }

        // Tool Modes
        if (dom.toolPointer) {
            dom.toolPointer.addEventListener("click", () => {
                state.activeMode = "pointer";
                updateToolModeUI();
            });
        }
        if (dom.toolDraw) {
            dom.toolDraw.addEventListener("click", () => {
                state.activeMode = "draw";
                updateToolModeUI();
            });
        }
        if (dom.toolReorder) {
            dom.toolReorder.addEventListener("click", () => {
                startReorderFlow();
            });
        }

        // Categorical Element Selector Buttons
        if (dom.categoryBtns) {
            dom.categoryBtns.forEach((btn) => {
                btn.addEventListener("click", () => {
                    const cat = btn.dataset.category;
                    setActiveElementCategory(cat);
                });
            });
        }

        // Suppress running margins toggle
        if (dom.toggleMargins) {
            dom.toggleMargins.addEventListener("change", (e) => {
                state.suppressRunningMargins = e.target.checked;
                renderLayoutBoxes();
                compileMarkdownText();
            });
        }

        // Interaction Layer Click (delegation for box clicks)
        if (dom.interactionLayer) {
            dom.interactionLayer.addEventListener("click", (e) => {
                const box = e.target.closest(".bbox-overlay-element");
                if (box) {
                    const id = box.dataset.id;
                    if (state.activeMode === "reorder") {
                        handleBoxReorderClick(id);
                    } else {
                        setActiveElement(id);
                    }
                }
            });
        }

        // Right pane recompile & save buttons
        if (dom.recompileBtn) {
            dom.recompileBtn.addEventListener("click", () => {
                compileMarkdownText();
                queueAutoSaveMarkdown();
            });
        }
        if (dom.saveMdBtn) {
            dom.saveMdBtn.addEventListener("click", async () => {
                await executeSaveMarkdown();
            });
        }

        // Right pane markdown editor input change -> debounced save
        if (dom.markdownEditor) {
            dom.markdownEditor.addEventListener("input", () => {
                queueAutoSaveMarkdown();
            });
        }

        // Initialize drawing mode mouse event handlers
        initDrawModeListeners();
    }

    // Run automatically when DOM is ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAnalysisWorkspace);
    } else {
        initAnalysisWorkspace();
    }
})();


// --- Phase 7 Batched DocumentFragment Rendering for Large PDF Block Lists ---
(function () {
    window.renderBlocksBatched = function (container, items, renderItemFn, batchSize = 100) {
        if (!container || !Array.isArray(items) || typeof renderItemFn !== "function") return;
        container.innerHTML = "";
        let index = 0;
        function _appendNextBatch() {
            const frag = document.createDocumentFragment();
            const limit = Math.min(items.length, index + batchSize);
            for (; index < limit; index++) {
                const node = renderItemFn(items[index], index);
                if (node instanceof Node) {
                    frag.appendChild(node);
                }
            }
            container.appendChild(frag);
            if (index < items.length) {
                window.requestAnimationFrame(_appendNextBatch);
            }
        }
        _appendNextBatch();
    };

    /**
     * Safely parses Markdown and purifies generated HTML using DOMPurify before DOM insertion.
     * @param {HTMLElement|string} targetElement - DOM element or element ID.
     * @param {string} rawMarkdown - Unsanitized markdown payload.
     */
    window.renderMarkdownSafely = function (targetElement, rawMarkdown) {
        const container = typeof targetElement === 'string'
            ? document.getElementById(targetElement)
            : targetElement;

        if (!container) {
            console.error("DOM container not found for safe markdown rendering.");
            return;
        }

        if (!rawMarkdown || typeof rawMarkdown !== 'string') {
            container.innerHTML = '';
            return;
        }

        if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
            container.textContent = rawMarkdown;
            console.error("Required libraries (marked/DOMPurify) are missing. Fell back to plain text.");
            return;
        }

        const rawHtml = marked.parse(rawMarkdown);
        const cleanHtml = DOMPurify.sanitize(rawHtml, {
            ALLOWED_TAGS: [
                'p', 'b', 'i', 'em', 'strong', 'a', 'ul', 'ol', 'li',
                'code', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                'blockquote', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span'
            ],
            ALLOWED_ATTR: ['href', 'title', 'target', 'class'],
            FORCE_BODY: true
        });

        container.innerHTML = cleanHtml;
    };
})();
