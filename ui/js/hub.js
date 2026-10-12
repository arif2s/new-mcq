let hubSessions = [];
let hubSelected = new Set();
let isEditing = false;

async function loadSessions() {
    try {
        const res = await fetch('/api/sessions/history');
        if (res.ok) {
            hubSessions = await res.json();
            renderHub();
        }
    } catch(e) {
        console.error("Failed to load sessions", e);
    }
}

function renderHub() {
    const container = document.getElementById('hub-container');
    if(!container) return;

    container.innerHTML = '';

    if(hubSessions.length === 0) {
        container.innerHTML = '<p class="text-gray-400">No sessions found in history.</p>';
        return;
    }

    hubSessions.forEach(session => {
        const div = document.createElement('div');
        div.className = 'glass-card p-4 mb-4 flex justify-between items-center cursor-pointer hover:border-brand-500 transition-colors';

        // Formatting Date
        const dateStr = new Date(session.timestamp).toLocaleString();

        let selectHtml = '';
        if (isEditing) {
            const isChecked = hubSelected.has(session.id) ? 'checked' : '';
            selectHtml = `<input type="checkbox" class="mr-4 w-5 h-5 accent-brand-500" data-id="${session.id}" ${isChecked}>`;
        }

        div.innerHTML = `
            <div class="flex items-center">
                ${selectHtml}
                <div>
                    <h4 class="font-bold text-lg">${session.subject_name || 'Mixed Session'}</h4>
                    <p class="text-sm text-gray-400">${dateStr} &bull; ${session.total_questions} Questions &bull; ${session.accuracy.toFixed(1)}% Accuracy</p>
                </div>
            </div>
            <div class="flex gap-2">
                <button class="secondary-btn btn-review" data-id="${session.id}">Review</button>
            </div>
        `;

        if(isEditing) {
            const cb = div.querySelector('input[type="checkbox"]');
            cb.addEventListener('change', (e) => {
                if(e.target.checked) hubSelected.add(session.id);
                else hubSelected.delete(session.id);
            });
            div.addEventListener('click', (e) => {
                if(e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON') {
                    cb.checked = !cb.checked;
                    cb.dispatchEvent(new Event('change'));
                }
            });
        }

        const reviewBtn = div.querySelector('.btn-review');
        if(reviewBtn) {
            reviewBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                // To do: implement detailed session review view if needed.
                // For now, it just shows an alert as placeholder.
                alert("Review details for session: " + session.id);
            });
        }

        container.appendChild(div);
    });
}

async function deleteSelectedSessions() {
    if (hubSelected.size === 0) return;
    if (confirm(`Delete ${hubSelected.size} session(s)?`)) {
        try {
            await fetch('/api/sessions/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ session_ids: Array.from(hubSelected) })
            });
            hubSelected.clear();
            isEditing = false;
            document.getElementById('hub-edit-btn').classList.remove('bg-brand-600', 'text-white');
            document.getElementById('hub-delete-btn').style.display = 'none';
            loadSessions();
        } catch(e) {
            console.error(e);
            alert("Failed to delete sessions");
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadSessions();

    const editBtn = document.getElementById('hub-edit-btn');
    const deleteBtn = document.getElementById('hub-delete-btn');

    if(editBtn) {
        editBtn.addEventListener('click', () => {
            isEditing = !isEditing;
            hubSelected.clear();
            if(isEditing) {
                editBtn.classList.add('bg-brand-600', 'text-white');
                deleteBtn.style.display = 'block';
            } else {
                editBtn.classList.remove('bg-brand-600', 'text-white');
                deleteBtn.style.display = 'none';
            }
            renderHub();
        });
    }

    if(deleteBtn) {
        deleteBtn.addEventListener('click', deleteSelectedSessions);
    }
});
