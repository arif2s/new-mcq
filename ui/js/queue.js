// ui/js/queue.js

let isQueueViewActive = false;
let queueSocket = null;

async function fetchQueueStatus() {
    try {
        const res = await fetch('/api/queue/status');
        if (res.ok) {
            const status = await res.json();
            renderQueue(status);
        }
    } catch (e) {
        console.error("Failed to fetch queue status:", e);
    }
}

function formatTaskTitle(task) {
    const taskType = (task.task_type || '').toUpperCase();
    const topic = task.topic_name || task.topic || '';

    if (taskType === 'SESSION_PROCESSING') {
        return `📦 Session Reference & Note Pipeline (${task.session_id || 'Session'})`;
    }
    if (taskType === 'MCQ_EXPLAIN' || topic.startsWith('MCQ:')) {
        const qLabel = task.question ? task.question : `Question ${topic.replace('MCQ:', '')}`;
        return `📝 Detailed MCQ Breakdown: ${qLabel}`;
    }
    if (taskType === 'BATCH_PRECOMPUTE' && !topic) {
        return `📂 CSV Question Bank Precomputation`;
    }
    if (topic) {
        return `🩺 Clinical Topic Synthesis: ${topic}`;
    }
    if (task.question) {
        return task.question;
    }
    return `Synthesis Task #${task.id}`;
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

    let slicePending = 0;
    let sliceProcessing = 0;
    let sliceFailed = 0;

    const fragment = document.createDocumentFragment();
    const tasks = Array.isArray(status.tasks) ? status.tasks : [];

    if (tasks.length > 0) {
        tasks.forEach((task, idx) => {
            if (task.status === 'PENDING') slicePending++;
            else if (task.status === 'PROCESSING') sliceProcessing++;
            else if (task.status === 'FAILED') sliceFailed++;

            const div = document.createElement('div');
            let statusColor = 'text-muted';
            if (task.status === 'COMPLETED') statusColor = 'text-success-500';
            else if (task.status === 'FAILED') statusColor = 'text-danger-500';
            else if (task.status === 'PROCESSING') statusColor = 'text-brand-400 animate-pulse';

            div.className = 'glass-panel p-4 rounded-xl mb-3 flex flex-col sm:flex-row justify-between sm:items-center gap-3 transition-colors border border-gray-700/50';

            const safeTitle = window.escapeHTML(formatTaskTitle(task));
            const updatedTime = task.updated_at
                ? new Date((task.updated_at < 1e11 ? task.updated_at * 1000 : task.updated_at)).toLocaleTimeString()
                : 'Just now';

            const canPrioritize = task.status === 'PENDING' && Number(task.priority || 3) > 1 && (task.session_id || task.id);

            div.innerHTML = `
                <div class="flex-1 min-w-0 pr-2">
                    <div class="font-medium truncate text-sm" title="${safeTitle}">${safeTitle}</div>
                    <div class="text-xs text-muted mt-1.5 flex flex-wrap items-center gap-2">
                        <span class="glass-panel px-2 py-0.5 rounded font-mono text-[10px]">ID: #${task.id}</span>
                        ${formatPriorityBadge(task.priority)}
                        ${task.session_id ? `<span class="glass-panel px-2 py-0.5 rounded font-mono text-[10px] truncate max-w-[140px]">${window.escapeHTML(task.session_id)}</span>` : ''}
                        <span>Updated: ${updatedTime}</span>
                    </div>
                    ${task.status === 'FAILED' && task.error_message ? `
                        <div class="mt-2 text-[11px] text-danger-400 bg-danger-500/10 border border-danger-500/20 rounded-lg px-2.5 py-1 truncate" title="${window.escapeHTML(task.error_message)}">
                            ⚠️ ${window.escapeHTML(task.error_message)}
                        </div>
                    ` : ''}
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    ${canPrioritize ? `
                        <button type="button" class="queue-prio-btn secondary-btn px-2.5 py-1 text-[11px] font-semibold"
                                data-session-id="${window.escapeHTML(task.session_id || '')}"
                                data-task-id="${task.id}"
                                title="Raise to #1 Priority in Queue">
                            🚀 Move to #1
                        </button>
                    ` : ''}
                    <div class="font-bold text-xs uppercase tracking-wider ${statusColor} px-3 py-1 rounded-full glass-panel">
                        ${window.escapeHTML(task.status)}
                    </div>
                </div>
            `;
            fragment.appendChild(div);
        });

        tasksList.innerHTML = '';
        tasksList.appendChild(fragment);

        // Wire "Move to #1" priority boost buttons
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

    // Update numeric summary cards using accurate SQL counts (falling back to slice counts if omitted)
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

async function retryFailedTasks() {
    try {
        await fetch('/api/queue/retry_failed', { method: 'POST' });
        fetchQueueStatus();
    } catch (e) {
        console.error(e);
    }
}

function initQueueWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = `${protocol}${window.location.host}/ws/tasks`;

    queueSocket = new WebSocket(wsUrl);

    queueSocket.onopen = () => {
        const workerBadge = document.getElementById('queue-worker-status');
        if (workerBadge) {
            workerBadge.textContent = 'Live';
            workerBadge.className = 'badge bg-ok ml-2';
        }
    };

    queueSocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if ((data.event === 'TASK_UPDATE' || data.event === 'SESSION_STATUS_UPDATE') && isQueueViewActive) {
                fetchQueueStatus();
            }
        } catch (e) {
            console.error("Failed to parse queue WebSocket message:", e);
        }
    };

    queueSocket.onclose = () => {
        const workerBadge = document.getElementById('queue-worker-status');
        if (workerBadge) {
            workerBadge.textContent = 'Reconnecting...';
            workerBadge.className = 'badge bg-warning ml-2';
        }
        setTimeout(initQueueWebSocket, 5000);
    };
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
    document.getElementById('queue-retry-btn')?.addEventListener('click', retryFailedTasks);

    initQueueWebSocket();
});
