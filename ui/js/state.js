// ui/js/state.js

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
    processingQueueHardLimit: 100,

    // Phase 5 Feature Settings
    llmEnabled: true,
    skipLMStudio: false,
    obsidianOpenMode: 'browser', // 'browser' | 'app'
    extractorStrategy: 'medspacy', // 'medspacy' | 'regex'
    pdfParser: 'fitz', // 'fitz' | 'docling'
    panelOpacity: 0.78, // Non-card area transparency (0.10 - 0.95)
    cardOpacity: 0.82,  // Card area transparency (0.40 - 0.98)
    wallpaperIntervalMinutes: 120, // Wallpaper rotation duration in minutes (0 = Never)
    embeddingModel: 'Xenova/all-MiniLM-L6-v2',
    flashrankModel: 'ms-marco-TinyBERT-L-2-v2'
};

let appState = JSON.parse(JSON.stringify(DEFAULT_PERSISTENCE));

function applyAppearanceVariables(stateObj = appState) {
    const root = document.documentElement;
    const panelAlpha = typeof stateObj.panelOpacity === 'number'
        ? Math.min(0.95, Math.max(0.10, stateObj.panelOpacity))
        : 0.78;
    const cardAlpha = typeof stateObj.cardOpacity === 'number'
        ? Math.min(0.98, Math.max(0.35, stateObj.cardOpacity))
        : 0.82;

    root.style.setProperty('--panel-alpha', panelAlpha.toString());
    root.style.setProperty('--card-alpha', cardAlpha.toString());

    const isDark = document.body.classList.contains('dark-theme');
    if (isDark) {
        root.style.setProperty('--panel-bg', `rgba(15, 23, 42, ${panelAlpha})`);
        root.style.setProperty('--input-bg', `rgba(15, 23, 42, ${Math.min(0.95, panelAlpha + 0.05)})`);
    } else {
        root.style.setProperty('--panel-bg', `rgba(255, 255, 255, ${panelAlpha})`);
        root.style.setProperty('--input-bg', `rgba(255, 255, 255, ${Math.min(0.95, panelAlpha + 0.05)})`);
    }
}

async function fetchState() {
    try {
        const res = await fetch('/api/state/');
        if (res.ok) {
            const data = await res.json();
            if (data && data.app_persistence) {
                appState = { ...DEFAULT_PERSISTENCE, ...data.app_persistence };
                if (typeof data.app_persistence.llmEnabled === 'boolean') {
                    appState.skipLMStudio = !data.app_persistence.llmEnabled;
                } else if (typeof data.app_persistence.skipLMStudio === 'boolean') {
                    appState.llmEnabled = !data.app_persistence.skipLMStudio;
                }
            }
        }
    } catch (e) {
        console.warn('Offline or failed to fetch state from server:', e);
    }

    // Sync FSRS card summary from SQLite question bank if available
    try {
        const fsrsRes = await fetch('/api/mcq/fsrs_summary');
        if (fsrsRes.ok) {
            const fsrsData = await fsrsRes.json();
            if (fsrsData.status === 'success' && fsrsData.counts && fsrsData.total > 0) {
                const syntheticCards = [];
                Object.entries(fsrsData.counts).forEach(([status, count]) => {
                    for (let i = 0; i < count; i++) {
                        syntheticCards.push({ status });
                    }
                });
                appState.ankiCards = syntheticCards;
            }
        }
    } catch (_) {}

    applyAppearanceVariables(appState);
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
        console.error('Failed to save state:', e);
    }
}

function updateState(updates) {
    if ('llmEnabled' in updates && !('skipLMStudio' in updates)) {
        updates.skipLMStudio = !updates.llmEnabled;
    } else if ('skipLMStudio' in updates && !('llmEnabled' in updates)) {
        updates.llmEnabled = !updates.skipLMStudio;
    }

    appState = { ...appState, ...updates };
    applyAppearanceVariables(appState);
    saveState();
    document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));
}

function recalculateStreaks(habitLog) {
    const activeDates = Array.from(
        new Set(
            (habitLog || [])
                .filter(h => (h.questionsAnswered || 0) > 0 && h.date)
                .map(h => h.date)
        )
    ).sort();

    if (activeDates.length === 0) {
        return { streakDays: 0, longestStreak: 0 };
    }

    let longest = 1;
    let currentRun = 1;

    for (let i = 1; i < activeDates.length; i++) {
        const prev = new Date(activeDates[i - 1] + 'T00:00:00Z');
        const curr = new Date(activeDates[i] + 'T00:00:00Z');
        const diffDays = Math.round((curr - prev) / 86400000);
        if (diffDays === 1) {
            currentRun++;
            if (currentRun > longest) longest = currentRun;
        } else if (diffDays > 1) {
            currentRun = 1;
        }
    }

    const todayStr = new Date().toISOString().split('T')[0];
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = yesterday.toISOString().split('T')[0];

    const lastActive = activeDates[activeDates.length - 1];
    let activeStreak = 0;
    if (lastActive === todayStr || lastActive === yesterdayStr) {
        activeStreak = 1;
        for (let i = activeDates.length - 1; i > 0; i--) {
            const curr = new Date(activeDates[i] + 'T00:00:00Z');
            const prev = new Date(activeDates[i - 1] + 'T00:00:00Z');
            if (Math.round((curr - prev) / 86400000) === 1) {
                activeStreak++;
            } else {
                break;
            }
        }
    }

    return {
        streakDays: activeStreak,
        longestStreak: Math.max(longest, activeStreak, appState.longestStreak || 0)
    };
}

function recordQuizCompletion(result, answeredQuestions, quizAnswersMap) {
    const state = appState;
    const testResults = [...(state.testResults || []), result];
    const today = new Date().toISOString().split('T')[0];

    const habitLog = [...(state.habitLog || [])];
    let habit = habitLog.find(h => h.date === today);
    if (!habit) {
        habit = { date: today, questionsAnswered: 0, correctAnswers: 0, timeSpentSeconds: 0, subjects: [] };
        habitLog.push(habit);
    }
    habit.questionsAnswered += result.totalQuestions || 0;
    habit.correctAnswers += result.correctAnswers || 0;
    habit.timeSpentSeconds += result.timeUsedSeconds || 0;
    if (result.subjectName && !(habit.subjects || []).includes(result.subjectName)) {
        habit.subjects = [...(habit.subjects || []), result.subjectName];
    }

    // Update Subject Registry
    const subjects = [...(state.subjects || [])];
    if (result.subjectName && !subjects.some(s => s.name === result.subjectName)) {
        subjects.push({ name: result.subjectName, questions: [] });
    }

    // Update Per-Topic Expertise Map
    const topicExpertise = { ...(state.topicExpertise || {}) };
    (answeredQuestions || []).forEach(q => {
        const ans = quizAnswersMap ? quizAnswersMap[q.id] : null;
        if (!ans) return;
        const topic = q.topic_name || q.topic || result.subjectName || 'General';
        const subject = q.subject_name || result.subjectName || 'General';
        const key = `${subject}::${topic}`;

        const prev = topicExpertise[key] || {
            topic,
            subject,
            attempted: 0,
            correct: 0,
            totalQuestions: 20,
            accuracy: 0,
            level: 'beginner',
            trend: 'stable',
            lastAttempted: Date.now()
        };

        const prevAcc = prev.accuracy || 0;
        const attempted = prev.attempted + 1;
        const correct = prev.correct + (ans.isCorrect ? 1 : 0);
        const accuracy = (correct / attempted) * 100;

        let level = 'beginner';
        if (accuracy >= 85 && attempted >= 3) level = 'expert';
        else if (accuracy >= 70 && attempted >= 2) level = 'advanced';
        else if (accuracy >= 50) level = 'intermediate';

        let trend = 'stable';
        if (attempted > 1) {
            if (accuracy > prevAcc + 2) trend = 'improving';
            else if (accuracy < prevAcc - 2) trend = 'declining';
        }

        topicExpertise[key] = {
            ...prev,
            topic,
            subject,
            attempted,
            correct,
            totalQuestions: Math.max(prev.totalQuestions || 20, attempted),
            accuracy,
            level,
            trend,
            lastAttempted: Date.now()
        };
    });

    const streaks = recalculateStreaks(habitLog);

    updateState({
        testResults,
        habitLog,
        subjects,
        topicExpertise,
        streakDays: streaks.streakDays,
        longestStreak: streaks.longestStreak,
        totalQuestionsEver: (state.totalQuestionsEver || 0) + (result.totalQuestions || 0)
    });
}

function exportState() {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(appState, null, 2));
    const downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", `medisearch_backup_${new Date().toISOString().split('T')[0]}.json`);
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
}

async function importState(jsonString) {
    try {
        const parsed = JSON.parse(jsonString);
        if (parsed && typeof parsed === 'object') {
            appState = { ...DEFAULT_PERSISTENCE, ...parsed };
            applyAppearanceVariables(appState);
            await saveState();
            document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));
            return true;
        }
    } catch (e) {
        console.error("Invalid JSON backup file:", e);
    }
    return false;
}

window.AppState = {
    get: () => appState,
    fetch: fetchState,
    save: saveState,
    update: updateState,
    recordQuizCompletion,
    applyAppearance: applyAppearanceVariables,
    exportData: exportState,
    importData: importState
};
