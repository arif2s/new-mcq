const DEFAULT_PERSISTENCE = {
    dailyTargets: { questionsTarget: 50, correctTarget: 40, reviewTarget: 20 },
    streakDays: 0,
    longestStreak: 0,
    habitLog: [],
    testResults: [],
    ankiCards: [],
    subjects: [],
    reviewQueue: [],
    topicExpertise: {},
    rankSimConfigs: [],
    totalQuestionsEver: 0,
    processingQueueLimit: 10,
    processingQueueHardLimit: 100
};

let appState = JSON.parse(JSON.stringify(DEFAULT_PERSISTENCE));

async function fetchState() {
    try {
        const res = await fetch('/api/state/');
        if (!res.ok) return null;
        const data = await res.json();
        if (data.app_persistence) {
            appState = { ...DEFAULT_PERSISTENCE, ...data.app_persistence };
        }
    } catch (e) {
        console.error('Failed to fetch state', e);
    }
    return appState;
}

async function saveState() {
    try {
        await fetch('/api/state/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: 'app_persistence', value: appState }),
        });
    } catch (e) {
        console.error('Failed to save state', e);
    }
}

function updateState(updates) {
    appState = { ...appState, ...updates };
    saveState();
    // Dispatch event so UI components can re-render
    document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));
}

function exportState() {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(appState, null, 2));
    const downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", `medisearch_backup_${new Date().toISOString().split('T')[0]}.json`);
    document.body.appendChild(downloadAnchorNode); // Required for Firefox
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
}

async function importState(jsonString) {
    try {
        const parsed = JSON.parse(jsonString);
        if (parsed && typeof parsed === 'object') {
            appState = { ...DEFAULT_PERSISTENCE, ...parsed };
            await saveState();
            document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));
            return true;
        }
    } catch (e) {
        console.error("Invalid JSON file", e);
    }
    return false;
}

window.AppState = {
    get: () => appState,
    fetch: fetchState,
    save: saveState,
    update: updateState,
    exportData: exportState,
    importData: importState
};
