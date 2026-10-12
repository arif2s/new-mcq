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
        console.error("Failed to fetch queue status", e);
    }
}

function renderQueue(status) {
    const tasksList = document.getElementById('queue-tasks-list');
    if (!tasksList) return;

    let pendingCount = 0;
    let processingCount = 0;
    let failedCount = 0;

    const fragment = document.createDocumentFragment();

    if (status.tasks && status.tasks.length > 0) {
        status.tasks.forEach(task => {
            // Tally current active states
            if (task.status === 'PENDING') pendingCount++;
            else if (task.status === 'PROCESSING') processingCount++;
            else if (task.status === 'FAILED') failedCount++;

            const div = document.createElement('div');
            let statusColor = 'text-gray-400';

            if (task.status === 'COMPLETED') statusColor = 'text-success-500';
            else if (task.status === 'FAILED') statusColor = 'text-danger-500';
            else if (task.status === 'PROCESSING') statusColor = 'text-brand-400';

            div.className = 'bg-gray-900/50 border border-gray-700 p-4 rounded-xl mb-3 flex justify-between items-center transition-colors hover:bg-gray-800/80';

            // Safe text escaping to prevent XSS
            const escapeMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' };
            const safeQuestion = task.question ? task.question.replace(/[&<>'"]/g, c => escapeMap[c] || c) : 'Synthesis Task';

            div.innerHTML = `
                <div class="flex-1 min-w-0 pr-4">
                    <div class="font-medium text-gray-200 truncate">${safeQuestion}</div>
                    <div class="text-xs text-gray-500 mt-1.5 flex items-center gap-2">
                        <span class="bg-gray-800 px-2 py-0.5 rounded font-mono text-[10px]">ID: ${task.id}</span>
                        <span>Updated: ${new Date(task.updated_at * 1000).toLocaleTimeString()}</span>
                    </div>
                </div>
                <div class="font-bold text-xs uppercase tracking-wider ${statusColor} shrink-0 px-3 py-1 rounded-full bg-gray-800/50 border border-gray-700">
                    ${task.status}
                </div>
            `;
            fragment.appendChild(div);
        });
        tasksList.innerHTML = '';
        tasksList.appendChild(fragment);
    } else {
        tasksList.innerHTML = `
            <div class="text-center py-12 border border-gray-800 border-dashed rounded-xl bg-gray-900/20">
                <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mx-auto text-gray-600 mb-3"><path d="M22 12h-4l-3 9L9 3l-3 9H2"></path></svg>
                <p class="text-gray-400 text-sm">No recent tasks in queue.</p>
            </div>
        `;
    }

    // Safely update the numeric summary cards without overriding layout text
    const pendingEl = document.getElementById('queue-pending');
    const processingEl = document.getElementById('queue-processing');
    const completedEl = document.getElementById('queue-completed');
    const failedEl = document.getElementById('queue-failed');
    const totalHeadingEl = document.getElementById('queue-total');

    if (pendingEl) pendingEl.textContent = pendingCount;
    if (processingEl) processingEl.textContent = processingCount;
    if (completedEl) completedEl.textContent = status.completed_tasks || 0;
    if (failedEl) failedEl.textContent = failedCount;

    if (totalHeadingEl) {
        totalHeadingEl.textContent = `Recent Tasks (${status.total_tasks || 0} Lifetime Total)`;
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
        console.log("Queue WebSocket Connected");
        const workerBadge = document.getElementById('queue-worker-status');
        if (workerBadge) {
            workerBadge.textContent = 'Live';
            workerBadge.className = 'badge bg-success-500/20 text-success-400 border border-success-500/30 ml-2';
        }
    };

    queueSocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            // Eliminate HTTP polling: only fetch when a TASK_UPDATE occurs AND the user is actively viewing the tab
            if (data.event === 'TASK_UPDATE' && isQueueViewActive) {
                fetchQueueStatus();
            }
        } catch (e) {
            console.error("Failed to parse websocket message:", e);
        }
    };

    queueSocket.onclose = () => {
        console.warn("Queue WebSocket Disconnected. Reconnecting in 5s...");
        const workerBadge = document.getElementById('queue-worker-status');
        if (workerBadge) {
            workerBadge.textContent = 'Reconnecting...';
            workerBadge.className = 'badge bg-warning/20 text-warning border border-warning/30 ml-2';
        }
        setTimeout(initQueueWebSocket, 5000);
    };
}

document.addEventListener('DOMContentLoaded', () => {
    // Tie into the existing navigation logic from app.js to detect when the Queue tab is visible
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const target = e.currentTarget.getAttribute('data-target');
            if (target === 'queue-view') {
                isQueueViewActive = true;
                fetchQueueStatus(); // Fetch immediately once upon opening
            } else {
                isQueueViewActive = false;
            }
        });
    });

    document.getElementById('queue-clear-btn')?.addEventListener('click', clearFailedTasks);
    document.getElementById('queue-retry-btn')?.addEventListener('click', retryFailedTasks);

    // Initialize the real-time websocket connection on load
    initQueueWebSocket();
});
