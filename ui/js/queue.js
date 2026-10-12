let queuePollTimer = null;

async function fetchQueueStatus() {
    try {
        const res = await fetch('/api/queue/status');
        if (res.ok) {
            const status = await res.json();
            renderQueue(status);
        }
    } catch(e) {
        console.error("Failed to fetch queue status", e);
    }
}

function renderQueue(status) {
    const container = document.getElementById('queue-container');
    if(!container) return;

    document.getElementById('queue-total').textContent = `Total: ${status.total_tasks}`;
    document.getElementById('queue-pending').textContent = `Pending: ${status.pending}`;
    document.getElementById('queue-processing').textContent = `Processing: ${status.processing}`;
    document.getElementById('queue-completed').textContent = `Completed: ${status.completed}`;
    document.getElementById('queue-failed').textContent = `Failed: ${status.failed}`;

    // Check if worker is running
    const workerBadge = document.getElementById('queue-worker-status');
    if(status.worker_running) {
        workerBadge.textContent = "Worker Active";
        workerBadge.className = "badge bg-success-500 text-white ml-2";
    } else {
        workerBadge.textContent = "Worker Stopped";
        workerBadge.className = "badge bg-danger-500 text-white ml-2";
    }

    const tasksList = document.getElementById('queue-tasks-list');
    tasksList.innerHTML = '';

    if(status.recent_tasks && status.recent_tasks.length > 0) {
        status.recent_tasks.forEach(task => {
            const div = document.createElement('div');
            let statusColor = 'text-gray-400';
            if(task.status === 'completed') statusColor = 'text-success-400';
            else if(task.status === 'failed') statusColor = 'text-danger-400';
            else if(task.status === 'processing') statusColor = 'text-brand-400';

            div.className = 'glass-card p-3 mb-2 flex justify-between items-center';
            div.innerHTML = `
                <div>
                    <div class="font-bold">${task.task_type}</div>
                    <div class="text-xs text-gray-400">ID: ${task.id.substring(0,8)}... &bull; Prio: ${task.priority}</div>
                    ${task.error ? `<div class="text-xs text-danger-400 mt-1">${task.error}</div>` : ''}
                </div>
                <div class="font-bold ${statusColor}">${task.status.toUpperCase()}</div>
            `;
            tasksList.appendChild(div);
        });
    } else {
        tasksList.innerHTML = '<p class="text-gray-400 p-4">No recent tasks.</p>';
    }
}

async function clearFailedTasks() {
    try {
        await fetch('/api/queue/clear_failed', { method: 'POST' });
        fetchQueueStatus();
    } catch(e) {
        console.error(e);
    }
}

async function retryFailedTasks() {
    try {
        await fetch('/api/queue/retry_failed', { method: 'POST' });
        fetchQueueStatus();
    } catch(e) {
        console.error(e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    // Only poll when viewing the queue tab to save resources
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const target = e.target.getAttribute('data-target');
            if (target === 'queue-view') {
                fetchQueueStatus();
                if(queuePollTimer) clearInterval(queuePollTimer);
                queuePollTimer = setInterval(fetchQueueStatus, 3000);
            } else {
                if(queuePollTimer) {
                    clearInterval(queuePollTimer);
                    queuePollTimer = null;
                }
            }
        });
    });

    document.getElementById('queue-clear-btn')?.addEventListener('click', clearFailedTasks);
    document.getElementById('queue-retry-btn')?.addEventListener('click', retryFailedTasks);
});
