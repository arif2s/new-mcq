// ui/js/queue.js

let isQueueViewActive = false;
let queueSocket = null;

function _isQueueVisible() {
    const viewEl = document.getElementById('queue-view');
    return Boolean(isQueueViewActive || (viewEl && viewEl.classList.contains('active')));
}

let _queueFetchToken = 0;
async function fetchQueueStatus() {
    const currentToken = ++_queueFetchToken;
    try {
        const res = await fetch('/api/queue/status', {
            headers: { 'Cache-Control': 'no-cache' }
        });
        if (res.ok) {
            const status = await res.json();
            if (currentToken === _queueFetchToken) {
                // Synchronize through monotonic store if available to prevent stale overwrite
                if (window.appStateStore && Array.isArray(status.tasks)) {
                    const ts = Number(status.timestamp) || Date.now();
                    window.appStateStore.applyServerPatch({ tasks: status.tasks }, ts);
                }
                renderQueue(status);
            }
        }
    } catch (e) {
        console.error("Failed to fetch queue status:", e);
    }
}
window.fetchQueueStatus = fetchQueueStatus;

function formatTaskTitle(task) {
    const taskType = String(task.task_type || '').toUpperCase();
    const topic = task.topic_name || task.topic || '';
    const taskId = task.task_id ?? task.id ?? 0;

    if (taskType === 'PROCESS_SESSION' || taskType === 'SESSION_PROCESSING') {
        return `📦 Session Reference & Note Pipeline (${task.session_id || 'Session'})`;
    }
    if (taskType === 'EXPLAIN_MCQ' || taskType === 'MCQ_EXPLAIN' || topic.startsWith('MCQ:')) {
        const qLabel = task.question ? task.question : `Question ${topic.replace('MCQ:', '')}`;
        return `📝 Detailed MCQ Breakdown: ${qLabel}`;
    }
    if (taskType === 'BATCH_PRECOMPUTE') {
        return `📂 ${topic || 'CSV Question Bank Precomputation'}`;
    }
    if (topic) {
        return `🩺 Clinical Topic Synthesis: ${topic}`;
    }
    if (task.question) {
        return task.question;
    }
    return `Synthesis Task #${taskId}`;
}

function formatPriorityBadge(priority) {
    const p = Number(priority || 3);
    if (p === 1) {
        return `<span class="px-2 py-0.5 rounded bg-brand-500/20 text-brand-300 border border-brand-500/30 font-mono text-[10px]">⚡ P1 Top</span>`;
    }
    if (p === 2) {
        return `<span class="px-2 py-0.5 rounded glass-panel text-muted font-mono text-[10px]">P2 Session</span>`;
    }
    return `<span class="px-2 py-0.5 rounded glass-panel text-muted opacity-70 font-mono text-[10px]">P3 Batch</span>`;
}

function renderQueue(status) {
    const tasksList = document.getElementById('queue-tasks-list');
    if (!tasksList) return;

    const escapeHTML = window.escapeHTML || ((s) => String(s ?? ''));

    let slicePending = 0;
    let sliceProcessing = 0;
    let sliceFailed = 0;

    const fragment = document.createDocumentFragment();
    const tasks = Array.isArray(status.tasks) ? status.tasks : [];

    if (tasks.length > 0) {
        tasks.forEach((task) => {
            const stUpper = String(task.status || 'pending').toUpperCase();
            const taskId = task.task_id ?? task.id ?? 0;

            if (stUpper === 'PENDING') slicePending++;
            else if (stUpper === 'PROCESSING') sliceProcessing++;
            else if (stUpper === 'FAILED') sliceFailed++;

            const div = document.createElement('div');
            let statusColor = 'text-muted';
            if (stUpper === 'COMPLETED') statusColor = 'text-success-500';
            else if (stUpper === 'FAILED') statusColor = 'text-danger-500';
            else if (stUpper === 'PROCESSING') statusColor = 'text-brand-400 animate-pulse';

            div.className = 'glass-panel p-4 rounded-xl mb-3 flex flex-col sm:flex-row justify-between sm:items-center gap-3 transition-colors border border-gray-700/50';

            const safeTitle = escapeHTML(formatTaskTitle(task));
            const updatedTime = task.updated_at
                ? new Date((task.updated_at < 1e11 ? task.updated_at * 1000 : task.updated_at)).toLocaleTimeString()
                : 'Just now';

            const canPrioritize = stUpper === 'PENDING' && Number(task.priority || 3) > 1 && (task.session_id || taskId);

            div.innerHTML = `
                <div class="flex-1 min-w-0 pr-2">
                    <div class="font-medium truncate text-sm" title="${safeTitle}">${safeTitle}</div>
                    <div class="text-xs text-muted mt-1.5 flex flex-wrap items-center gap-2">
                        <span class="glass-panel px-2 py-0.5 rounded font-mono text-[10px]">ID: #${taskId}</span>
                        ${formatPriorityBadge(task.priority)}
                        ${task.session_id ? `<span class="glass-panel px-2 py-0.5 rounded font-mono text-[10px] truncate max-w-[140px]">${escapeHTML(task.session_id)}</span>` : ''}
                        <span>Updated: ${updatedTime}</span>
                    </div>
                    ${stUpper === 'FAILED' && task.error_message ? `
                        <div class="mt-2 text-[11px] text-danger-400 bg-danger-500/10 border border-danger-500/20 rounded-lg px-2.5 py-1 truncate" title="${escapeHTML(task.error_message)}">
                            ⚠️ ${escapeHTML(task.error_message)}
                        </div>
                    ` : ''}
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    ${canPrioritize ? `
                        <button type="button" class="queue-prio-btn secondary-btn px-2.5 py-1 text-[11px] font-semibold cursor-pointer"
                                data-session-id="${escapeHTML(task.session_id || '')}"
                                data-task-id="${taskId}"
                                title="Raise to #1 Priority in Queue">
                            🚀 Move to #1
                        </button>
                    ` : ''}
                    <div class="font-bold text-xs uppercase tracking-wider ${statusColor} px-3 py-1 rounded-full glass-panel">
                        ${escapeHTML(stUpper)}
                    </div>
                </div>
            `;
            fragment.appendChild(div);
        });

        tasksList.innerHTML = '';
        tasksList.appendChild(fragment);

        tasksList.querySelectorAll('.queue-prio-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const sessionId = btn.dataset.sessionId;
                const taskId = parseInt(btn.dataset.taskId, 10);
                btn.disabled = true;
                btn.textContent = 'Boosting...';
                try {
                    await fetch('/api/queue/prioritize', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: sessionId || null,
                            task_id: isNaN(taskId) ? null : taskId
                        })
                    });
                    await fetchQueueStatus();
                } catch (e) {
                    console.error("Failed to prioritize task:", e);
                    btn.disabled = false;
                    btn.textContent = '🚀 Move to #1';
                }
            });
        });
    } else {
        tasksList.innerHTML = `
            <div class="text-center py-12 border border-gray-800 border-dashed rounded-xl glass-panel">
                <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mx-auto text-muted mb-3"><path d="M22 12h-4l-3 9L9 3l-3 9H2"></path></svg>
                <p class="text-muted text-sm">No tasks in the processing queue.</p>
            </div>
        `;
    }

    const pendingEl = document.getElementById('queue-pending');
    const processingEl = document.getElementById('queue-processing');
    const completedEl = document.getElementById('queue-completed');
    const failedEl = document.getElementById('queue-failed');
    const totalHeadingEl = document.getElementById('queue-total');

    if (pendingEl) pendingEl.textContent = status.pending_tasks ?? slicePending;
    if (processingEl) processingEl.textContent = status.processing_tasks ?? sliceProcessing;
    if (completedEl) completedEl.textContent = status.completed_tasks ?? 0;
    if (failedEl) failedEl.textContent = status.failed_tasks ?? sliceFailed;

    if (totalHeadingEl) {
        totalHeadingEl.textContent = `Recent Tasks (${status.total_tasks ?? tasks.length} Lifetime Total)`;
    }
}

async function clearFailedTasks() {
    try {
        await fetch('/api/queue/clear_failed', { method: 'POST' });
        fetchQueueStatus();
    } catch (e) {
        console.error(e);
    }
}

async function clearCompletedTasks() {
    try {
        await fetch('/api/queue/clear_completed', { method: 'POST' });
        fetchQueueStatus();
    } catch (e) {
        console.error(e);
    }
}

async function retryFailedTasks() {
    try {
        await fetch('/api/queue/retry_failed', { method: 'POST' });
        fetchQueueStatus();
    } catch (e) {
        console.error(e);
    }
}

function _setWorkerBadge(live) {
    const workerBadge = document.getElementById('queue-worker-status');
    if (!workerBadge) return;
    if (live) {
        workerBadge.textContent = 'Live';
        workerBadge.className = 'badge bg-ok ml-2';
    } else {
        workerBadge.textContent = 'Reconnecting...';
        workerBadge.className = 'badge bg-warning ml-2';
    }
}

function initQueueWebSocket() {
    document.addEventListener('ws:task_update', () => {
        _setWorkerBadge(true);
        if (_isQueueVisible()) fetchQueueStatus();
    });
    document.addEventListener('ws:session_update', () => {
        _setWorkerBadge(true);
        if (_isQueueVisible()) fetchQueueStatus();
    });

    if (window.appWebSocket) {
        _setWorkerBadge(window.appWebSocket.readyState === WebSocket.OPEN);
        return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = `${protocol}${window.location.host}/ws/tasks`;

    try {
        queueSocket = new WebSocket(wsUrl);
        queueSocket.onopen = () => _setWorkerBadge(true);
        queueSocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                const msgType = data.type || data.event || '';
                if (
                    (msgType === 'task_update' || msgType === 'TASK_UPDATE' ||
                     msgType === 'session_update' || msgType === 'SESSION_STATUS_UPDATE') &&
                    _isQueueVisible()
                ) {
                    fetchQueueStatus();
                }
            } catch (e) {
                console.error("Failed to parse queue WebSocket message:", e);
            }
        };
        queueSocket.onclose = () => {
            _setWorkerBadge(false);
            setTimeout(() => {
                if (!window.appWebSocket) initQueueWebSocket();
            }, 5000);
        };
    } catch (_) {}
}

document.addEventListener('DOMContentLoaded', () => {
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const target = e.currentTarget.getAttribute('data-target');
            if (target === 'queue-view') {
                isQueueViewActive = true;
                fetchQueueStatus();
            } else {
                isQueueViewActive = false;
            }
        });
    });

    document.getElementById('queue-clear-btn')?.addEventListener('click', clearFailedTasks);
    document.getElementById('queue-clear-completed-btn')?.addEventListener('click', clearCompletedTasks);
    document.getElementById('queue-retry-btn')?.addEventListener('click', retryFailedTasks);

    if (_isQueueVisible()) {
        fetchQueueStatus();
    }
    initQueueWebSocket();
});

// --- Phase 7 Queue Job Retry & Cancel API Helpers ---
(function () {
    window.cancelQueueJobById = async function (jobId) {
        if (!jobId) return false;
        try {
            const resp = await fetch(`/api/queue/${encodeURIComponent(jobId)}`, { method: "DELETE" });
            return resp.ok;
        } catch (_err) {
            return false;
        }
    };
})();

// Tema-Q Pipeline, Model Lifecycle & Ignore-List Controller
class PipelineAndModelController {
    constructor(containerEl) {
        this.container = containerEl;
    }

    async mount() {
        if (!this.container) return;
        const [modelsRes, ignoredRes] = await Promise.all([
            fetch('/api/models/status').catch(() => ({ ok: false })),
            fetch('/api/state/ignored-topics').catch(() => ({ ok: false }))
        ]);
        const modelStatus = modelsRes && modelsRes.ok ? await modelsRes.json() : { loaded: false, model_name: 'Zero-VRAM Mode (CPU Safe)' };
        const ignoredData = ignoredRes && ignoredRes.ok ? await ignoredRes.json() : { topics: [] };

        this.render(modelStatus, ignoredData.topics || []);
    }

    render(modelStatus, ignoredTopics) {
        if (!this.container) return;
        this.container.innerHTML = `
            <div class="space-y-5 p-5 rounded-xl bg-slate-900/70 border border-slate-800 text-slate-200">
                <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
                    <div>
                        <h3 class="text-base font-semibold text-violet-400">Tema-Q Pipeline &amp; Model Lifecycle</h3>
                        <p class="text-xs text-slate-400">Active Model: <strong id="active-model-badge">${modelStatus.model_name || 'Unloaded (Zero-VRAM Mode)'}</strong></p>
                    </div>
                    <div class="flex gap-2">
                        <button type="button" id="btn-unload-vram" class="px-3 py-1.5 rounded-lg bg-amber-600/20 border border-amber-500/40 text-amber-300 hover:bg-amber-600/30 text-xs font-medium">
                            Purge / Unload LLM Weights
                        </button>
                        <button type="button" id="btn-batch-precompute" class="px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-semibold">
                            Trigger Batch Precompute
                        </button>
                    </div>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <label class="block text-xs">
                        <span class="text-slate-300 font-medium">Session Processing Depth</span>
                        <select id="pipeline-circle-mode" class="mt-1 w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-sm text-slate-200">
                            <option value="CIRCLE_2_ONLY">Circle 2 Only (Fast Deterministic Tantivy/LanceDB References)</option>
                            <option value="CIRCLE_3_FULL" selected>Circle 2 + Circle 3 (Full LLM Dossier &amp; Distractor Rationale)</option>
                        </select>
                    </label>

                    <label class="block text-xs">
                        <span class="text-slate-300 font-medium">Auditor Token Jaccard Deduplication Threshold (<span id="jaccard-val">0.75</span>)</span>
                        <input type="range" id="jaccard-slider" min="0.50" max="0.95" step="0.05" value="0.75" class="mt-2 w-full accent-violet-400" />
                    </label>
                </div>

                <div class="pt-3 border-t border-slate-800">
                    <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">
                        Permanent Ignore List (<code>ignored_topics</code>: <span id="ignored-count">${ignoredTopics.length}</span>)
                    </h4>
                    <div id="ignored-topics-list" class="flex flex-wrap gap-2 max-h-36 overflow-y-auto p-2 rounded-lg bg-slate-950/60 border border-slate-800/80">
                        ${ignoredTopics.length === 0
                            ? '<span class="text-xs text-slate-500" id="empty-ignore-msg">No clinical topics currently suppressed.</span>'
                            : ignoredTopics.map((t) => `
                                <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-slate-800 text-slate-300 border border-slate-700" data-topic-norm="${window.escapeHTML ? window.escapeHTML(t) : t}">
                                    <span>${window.escapeHTML ? window.escapeHTML(t) : t}</span>
                                    <button type="button" class="restore-topic-btn text-slate-400 hover:text-emerald-400 font-bold" aria-label="Restore topic ${window.escapeHTML ? window.escapeHTML(t) : t}">&times;</button>
                                </span>
                            `).join('')}
                    </div>
                </div>
            </div>
        `;

        this.bindEvents();
    }

    bindEvents() {
        const slider = this.container.querySelector('#jaccard-slider');
        const valSpan = this.container.querySelector('#jaccard-val');
        slider?.addEventListener('input', (e) => {
            valSpan.textContent = Number(e.target.value).toFixed(2);
        });

        this.container.querySelector('#btn-unload-vram')?.addEventListener('click', async () => {
            const res = await fetch('/api/models/unload', { method: 'POST' });
            if (res.ok) {
                const badge = this.container.querySelector('#active-model-badge');
                if (badge) badge.textContent = 'Unloaded (Zero-VRAM Mode)';
                if (typeof window.showToast === 'function') window.showToast('Model weights unloaded.');
            }
        });

        this.container.querySelector('#btn-batch-precompute')?.addEventListener('click', async () => {
            const mode = this.container.querySelector('#pipeline-circle-mode').value;
            const jaccard = Number(slider.value);
            const res = await fetch('/api/queue/enqueue', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_type: 'BATCH_PRECOMPUTE',
                    circle_mode: mode,
                    jaccard_threshold: jaccard
                })
            });
            if (res.ok) {
                if (typeof window.showToast === 'function') window.showToast('Batch precompute enqueued.');
                fetchQueueStatus();
            }
        });

        this.container.querySelector('#ignored-topics-list')?.addEventListener('click', async (e) => {
            const btn = e.target.closest('.restore-topic-btn');
            if (!btn) return;
            const badge = btn.closest('[data-topic-norm]');
            if (!badge) return;
            const topicNorm = badge.dataset.topicNorm;
            badge.remove();
            const countEl = this.container.querySelector('#ignored-count');
            if (countEl) {
                const current = parseInt(countEl.textContent || '0', 10);
                countEl.textContent = String(Math.max(0, current - 1));
            }
            await fetch(`/api/state/ignored-topics/${encodeURIComponent(topicNorm)}`, { method: 'DELETE' });
            if (typeof window.showToast === 'function') window.showToast(`Restored topic: ${topicNorm}`);
        });
    }
}

window.PipelineAndModelController = PipelineAndModelController;
