function initSettings() {
    const state = window.AppState.get();

    // Set Target Form Values
    const qTargetEl = document.getElementById('set-q-target');
    const cTargetEl = document.getElementById('set-c-target');
    if (qTargetEl && state.dailyTargets) qTargetEl.value = state.dailyTargets.questionsTarget;
    if (cTargetEl && state.dailyTargets) cTargetEl.value = state.dailyTargets.correctTarget;

    // Set Queue Limits Form Values
    const qLimitEl = document.getElementById('set-q-limit');
    const qhLimitEl = document.getElementById('set-qh-limit');
    if (qLimitEl) qLimitEl.value = state.processingQueueLimit || 10;
    if (qhLimitEl) qhLimitEl.value = state.processingQueueHardLimit || 100;
}

async function saveSettings() {
    const qTarget = parseInt(document.getElementById('set-q-target').value) || 50;
    const cTarget = parseInt(document.getElementById('set-c-target').value) || 40;
    const qLimit = parseInt(document.getElementById('set-q-limit').value) || 10;
    const qhLimit = parseInt(document.getElementById('set-qh-limit').value) || 100;

    const updates = {
        dailyTargets: { ...window.AppState.get().dailyTargets, questionsTarget: qTarget, correctTarget: cTarget },
        processingQueueLimit: qLimit,
        processingQueueHardLimit: qhLimit
    };

    window.AppState.update(updates);
    alert('Settings Saved Successfully!');
}

function handleExport() {
    if (window.AppState && window.AppState.exportData) {
        window.AppState.exportData();
    }
}

function handleImport(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (e) => {
        if (window.AppState && window.AppState.importData) {
            const success = await window.AppState.importData(e.target.result);
            if (success) {
                alert('Data imported successfully! The application state has been restored.');
                initSettings();
            } else {
                alert('Failed to import data. Please ensure the JSON file is a valid backup.');
            }
        }
        event.target.value = ''; // Reset input to allow re-importing the same file
    };
    reader.readAsText(file);
}

document.addEventListener('stateUpdated', initSettings);
document.addEventListener('DOMContentLoaded', () => {
    initSettings();
    document.getElementById('save-settings-btn')?.addEventListener('click', saveSettings);

    // Backup & Restore bindings (Ensure corresponding IDs exist in ui/index.html)
    document.getElementById('export-data-btn')?.addEventListener('click', handleExport);
    document.getElementById('import-data-file')?.addEventListener('change', handleImport);
    document.getElementById('import-data-btn')?.addEventListener('click', () => {
        document.getElementById('import-data-file')?.click();
    });
});
