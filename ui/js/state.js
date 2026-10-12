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
            // Ensure habit log is parsed properly if needed
            return appState;
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

window.AppState = {
    get: () => appState,
    fetch: fetchState,
    save: saveState,
    update: updateState
};
