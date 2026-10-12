function initSettings() {
    const state = window.AppState.get();

    // Set Target Form Values
    document.getElementById('set-q-target').value = state.dailyTargets.questionsTarget;
    document.getElementById('set-c-target').value = state.dailyTargets.correctTarget;

    // Set Queue Limits Form Values
    document.getElementById('set-q-limit').value = state.processingQueueLimit;
    document.getElementById('set-qh-limit').value = state.processingQueueHardLimit;
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

document.addEventListener('stateUpdated', initSettings);
document.addEventListener('DOMContentLoaded', () => {
    initSettings();
    document.getElementById('save-settings-btn')?.addEventListener('click', saveSettings);
});
