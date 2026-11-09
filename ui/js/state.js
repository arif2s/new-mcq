// ui/js/state.js

const DEFAULT_PERSISTENCE = {
    dailyTargets: { questionsTarget: 50, correctTarget: 40, reviewTarget: 20, customGoals: [], checklistTitle: 'Daily Study Plan', dailyChecklist: [] },
    customBannerMessages: [
        "Consistency beats intensity — master one clinical mechanism at a time.",
        "Review every distractor rationale: knowing why options B, C, and D fail wins board exams.",
        "High-yield tip: Combine textbook reading in Focus Mode with targeted 20-MCQ sets."
    ],
    streakDays: 0,
    longestStreak: 0,
    habitLog: [],
    testResults: [],
    ankiCards: [],
    fsrsCounts: { new: 0, learning: 0, review: 0, mastered: 0, total: 0 },
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
    pdfParser: 'docling_v2', // 'docling_v2' | 'fitz' | 'docling'
    panelOpacity: 0.78, // Non-card area transparency (0.10 - 0.95)
    cardOpacity: 0.82,  // Card area transparency (0.35 - 0.98)
    wallpaperIntervalMinutes: 120, // Wallpaper rotation duration in minutes (0 = Never)
    embeddingModel: 'sentence-transformers/all-MiniLM-L6-v2',
    flashrankModel: 'ms-marco-TinyBERT-L-2-v2',
    scispacyModel: 'en_core_sci_md'
};

let appState = JSON.parse(JSON.stringify(DEFAULT_PERSISTENCE));
let _stateLoadedFromServer = false;

function _getLocalDateString(dateObj = new Date()) {
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const day = String(dateObj.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function _normalizeModelIds(stateObj) {
    if (!stateObj || typeof stateObj !== 'object') return;
    if (stateObj.embeddingModel === 'Xenova/all-MiniLM-L6-v2') {
        stateObj.embeddingModel = 'sentence-transformers/all-MiniLM-L6-v2';
    }
    if (!stateObj.scispacyModel) {
        stateObj.scispacyModel = stateObj.scispacy_model || 'en_core_sci_md';
    }
}

function applyAppearanceVariables(stateObj = appState) {
    const root = document.documentElement;
    if (!root) return;

    const rawPanel = typeof stateObj.panelOpacity === 'number'
        ? stateObj.panelOpacity
        : (typeof stateObj.panelAlpha === 'number' ? stateObj.panelAlpha : 0.78);
    const panelAlpha = Math.min(0.95, Math.max(0.10, rawPanel));

    const rawCard = typeof stateObj.cardOpacity === 'number'
        ? stateObj.cardOpacity
        : (typeof stateObj.cardAlpha === 'number' ? stateObj.cardAlpha : 0.82);
    const cardAlpha = Math.min(0.98, Math.max(0.35, rawCard));

    root.style.setProperty('--panel-alpha', panelAlpha.toString());
    root.style.setProperty('--card-alpha', cardAlpha.toString());

    const isDark = document.body ? document.body.classList.contains('dark-theme') : true;
    if (isDark) {
        root.style.setProperty('--panel-bg', `rgba(15, 23, 42, ${panelAlpha})`);
        root.style.setProperty('--input-bg', `rgba(15, 23, 42, ${Math.min(0.95, panelAlpha + 0.05)})`);
    } else {
        root.style.setProperty('--panel-bg', `rgba(255, 255, 255, ${panelAlpha})`);
        root.style.setProperty('--input-bg', `rgba(255, 255, 255, ${Math.min(0.95, panelAlpha + 0.05)})`);
    }
}

function _getPersistableState(stateObj = appState) {
    // Strip synthetic in-memory ankiCards array so we never bloat SQLite user_state with 50k dummy objects
    const copy = { ...stateObj };
    delete copy.ankiCards;
    return copy;
}

async function fetchState() {
    try {
        const res = await fetch('/api/state/');
        if (res.ok) {
            const data = await res.json();
            const persisted = (data && data.app_persistence && typeof data.app_persistence === 'object')
                ? data.app_persistence
                : (data && typeof data === 'object' && !('status' in data) ? data : null);

            if (persisted) {
                appState = { ...DEFAULT_PERSISTENCE, ...persisted };
                _normalizeModelIds(appState);
                if (typeof persisted.llmEnabled === 'boolean') {
                    appState.skipLMStudio = !persisted.llmEnabled;
                } else if (typeof persisted.skipLMStudio === 'boolean') {
                    appState.llmEnabled = !persisted.skipLMStudio;
                }
                _stateLoadedFromServer = true;
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
            if (fsrsData.status === 'success' && fsrsData.counts) {
                appState.fsrsCounts = {
                    new: Number(fsrsData.counts.new || 0),
                    learning: Number(fsrsData.counts.learning || 0),
                    review: Number(fsrsData.counts.review || 0),
                    mastered: Number(fsrsData.counts.mastered || 0),
                    total: Number(fsrsData.total || 0)
                };
                // Keep a bounded synthetic array in memory for any legacy UI chart readers
                const syntheticCards = [];
                const total = appState.fsrsCounts.total;
                const scale = total > 2000 ? 2000 / total : 1;
                Object.entries(fsrsData.counts).forEach(([status, count]) => {
                    const scaledCount = Math.round(Number(count || 0) * scale);
                    for (let i = 0; i < scaledCount; i++) {
                        syntheticCards.push({ status });
                    }
                });
                appState.ankiCards = syntheticCards;
            }
        }
    } catch (_) {}

    applyAppearanceVariables(appState);
    document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));
    return appState;
}

async function saveState(partialPatch = null) {
    try {
        if (partialPatch && typeof partialPatch === 'object') {
            const cleanPatch = { ...partialPatch };
            delete cleanPatch.ankiCards;
            await fetch('/api/state/app_persistence', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cleanPatch),
            });
        } else {
            await fetch('/api/state/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: 'app_persistence', value: _getPersistableState(appState) }),
            });
        }
    } catch (e) {
        console.error('Failed to save state:', e);
    }
}

async function updateState(updates) {
    if (!updates || typeof updates !== 'object') return appState;

    const patch = { ...updates };
    if ('llmEnabled' in patch && !('skipLMStudio' in patch)) {
        patch.skipLMStudio = !patch.llmEnabled;
    } else if ('skipLMStudio' in patch && !('llmEnabled' in patch)) {
        patch.llmEnabled = !patch.skipLMStudio;
    }

    _normalizeModelIds(patch);
    appState = { ...appState, ...patch };
    applyAppearanceVariables(appState);
    document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));

    if (_stateLoadedFromServer) {
        await saveState(patch);
    } else {
        await saveState(patch);
        _stateLoadedFromServer = true;
    }
    return appState;
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
        const prev = new Date(activeDates[i - 1] + 'T00:00:00');
        const curr = new Date(activeDates[i] + 'T00:00:00');
        const diffDays = Math.round((curr - prev) / 86400000);
        if (diffDays === 1) {
            currentRun++;
            if (currentRun > longest) longest = currentRun;
        } else if (diffDays > 1) {
            currentRun = 1;
        }
    }

    const now = new Date();
    const todayStr = _getLocalDateString(now);
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = _getLocalDateString(yesterday);

    const lastActive = activeDates[activeDates.length - 1];
    let activeStreak = 0;
    if (lastActive === todayStr || lastActive === yesterdayStr) {
        activeStreak = 1;
        for (let i = activeDates.length - 1; i > 0; i--) {
            const curr = new Date(activeDates[i] + 'T00:00:00');
            const prev = new Date(activeDates[i - 1] + 'T00:00:00');
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
    const today = _getLocalDateString(new Date());

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
        const qid = q.id || q.question_id;
        const ans = quizAnswersMap
            ? (quizAnswersMap instanceof Map ? quizAnswersMap.get(qid) : quizAnswersMap[qid])
            : null;
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
        const isCorr = Boolean(ans.isCorrect !== undefined ? ans.isCorrect : ans.is_correct);
        const correct = prev.correct + (isCorr ? 1 : 0);
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
    const cleanDump = _getPersistableState(appState);
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(cleanDump, null, 2));
    const downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", `medisearch_backup_${_getLocalDateString(new Date())}.json`);
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
}

async function importState(jsonString) {
    try {
        const parsed = typeof jsonString === 'string' ? JSON.parse(jsonString) : jsonString;
        if (parsed && typeof parsed === 'object') {
            const payload = (parsed.state && parsed.state.app_persistence)
                ? parsed.state.app_persistence
                : (parsed.app_persistence ? parsed.app_persistence : parsed);
            appState = { ...DEFAULT_PERSISTENCE, ...payload };
            _normalizeModelIds(appState);
            applyAppearanceVariables(appState);
            await saveState();
            _stateLoadedFromServer = true;
            document.dispatchEvent(new CustomEvent('stateUpdated', { detail: appState }));
            return true;
        }
    } catch (e) {
        console.error("Invalid JSON backup file:", e);
    }
    return false;
}

// Monotonic State Store & Resilient WebSocket Synchronizer
class AppStateStore {
    constructor() {
        this.state = {
            queue: new Map(),
            sessions: new Map(),
            models: { loaded: false, activeModel: null, vramLocked: false },
            connectionStatus: 'DISCONNECTED',
            lastSeqTimestamp: 0
        };
        this.listeners = new Set();
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectDelay = 15000;
        this.syncAbortController = null;
    }

    subscribe(listener) {
        this.listeners.add(listener);
        return () => this.listeners.delete(listener);
    }

    notify(changedSlice) {
        for (const listener of this.listeners) {
            try {
                listener(this.state, changedSlice);
            } catch (err) {
                console.error('[AppStateStore] Listener error:', err);
            }
        }
    }

    applyServerPatch(patch, eventTimestamp = Date.now()) {
        const ts = Number(eventTimestamp) || Date.now();
        // Reject stale out-of-order payloads using high-water mark timestamp
        if (ts < this.state.lastSeqTimestamp) {
            return false;
        }
        this.state.lastSeqTimestamp = ts;

        if (Array.isArray(patch.queueTasks) || Array.isArray(patch.tasks)) {
            const taskList = patch.queueTasks || patch.tasks;
            for (const task of taskList) {
                const taskId = task.id || task.task_id;
                if (!taskId) continue;
                const existing = this.state.queue.get(taskId);
                if (!existing || (task.updatedAt || ts) >= (existing.updatedAt || 0)) {
                    this.state.queue.set(taskId, { ...existing, ...task, updatedAt: task.updatedAt || ts });
                }
            }
            this.notify('queue');
        }

        if (patch.sessionUpdate || patch.session) {
            const s = patch.sessionUpdate || patch.session;
            const sid = s.session_id || s.id;
            if (sid) {
                const existing = this.state.sessions.get(sid);
                if (!existing || (s.updatedAt || ts) >= (existing.updatedAt || 0)) {
                    this.state.sessions.set(sid, { ...existing, ...s, updatedAt: ts });
                    this.notify('sessions');
                }
            }
        }
        return true;
    }

    async reconcileFullState() {
        if (this.syncAbortController) {
            this.syncAbortController.abort();
        }
        this.syncAbortController = new AbortController();
        const { signal } = this.syncAbortController;

        try {
            const [stateRes, queueRes] = await Promise.all([
                fetch('/api/state/', { signal, headers: { 'Cache-Control': 'no-cache' } }).catch(() => null),
                fetch('/api/queue/status', { signal, headers: { 'Cache-Control': 'no-cache' } }).catch(() => null)
            ]);

            let serverTs = Date.now();
            if (stateRes && stateRes.ok) {
                const stateData = await stateRes.json();
                if (stateData.timestamp) serverTs = Math.max(serverTs, Number(stateData.timestamp));
            }
            if (queueRes && queueRes.ok) {
                const queueData = await queueRes.json();
                const tasks = queueData.tasks || [];
                this.state.queue.clear();
                for (const task of tasks) {
                    const taskId = task.id || task.task_id;
                    if (taskId) {
                        this.state.queue.set(taskId, { ...task, updatedAt: serverTs });
                    }
                }
            }
            this.state.lastSeqTimestamp = serverTs;
            this.notify('hydration');
        } catch (err) {
            if (err.name !== 'AbortError') {
                console.error('[AppStateStore] Reconciliation error:', err);
            }
        }
    }

    connectWebSocket(wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`) {
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        this.state.connectionStatus = 'CONNECTING';
        this.notify('connection');
        try {
            this.ws = new WebSocket(wsUrl);
            window.appWebSocket = this.ws;

            this.ws.onopen = async () => {
                this.reconnectAttempts = 0;
                this.state.connectionStatus = 'CONNECTED';
                this.notify('connection');
                await this.reconcileFullState();
            };

            this.ws.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    const ts = payload.timestamp ? Number(payload.timestamp) : Date.now();
                    this.applyServerPatch(payload, ts);
                } catch (e) {
                    console.error('[AppStateStore] Malformed WS frame:', e);
                }
            };

            this.ws.onclose = () => {
                this.state.connectionStatus = 'DISCONNECTED';
                this.notify('connection');
                const baseDelay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), this.maxReconnectDelay);
                const jitter = Math.random() * 400;
                this.reconnectAttempts += 1;
                setTimeout(() => this.connectWebSocket(wsUrl), baseDelay + jitter);
            };

            this.ws.onerror = () => {
                try { this.ws.close(); } catch (_) {}
            };
        } catch (err) {
            this.state.connectionStatus = 'DISCONNECTED';
            this.notify('connection');
            const baseDelay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), this.maxReconnectDelay);
            const jitter = Math.random() * 400;
            this.reconnectAttempts += 1;
            setTimeout(() => this.connectWebSocket(wsUrl), baseDelay + jitter);
        }
    }
}

const appStateStore = new AppStateStore();

window.AppStateStore = AppStateStore;
window.appStateStore = appStateStore;

window.AppState = {
    get: () => appState,
    fetch: fetchState,
    save: saveState,
    update: updateState,
    recordQuizCompletion,
    applyAppearance: applyAppearanceVariables,
    exportData: exportState,
    importData: importState,
    store: appStateStore
};

// --- Phase 7 Debounced State Persistence & LocalStorage Fallback ---
(function () {
    const LOCAL_STATE_KEY = "tema_q_cached_state_v1";
    let _saveTimer = null;
    let _pendingPatch = {};

    window.saveStateToLocalBackup = function (stateObj) {
        try {
            if (stateObj && typeof stateObj === "object") {
                window.localStorage.setItem(LOCAL_STATE_KEY, JSON.stringify(stateObj));
            }
        } catch (_err) {}
    };

    window.loadStateFromLocalBackup = function () {
        try {
            const raw = window.localStorage.getItem(LOCAL_STATE_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (_err) {
            return null;
        }
    };

    window.queueDebouncedStatePatch = function (partialState, delayMs = 300) {
        if (partialState && typeof partialState === "object") {
            Object.assign(_pendingPatch, partialState);
            window.saveStateToLocalBackup(_pendingPatch);
        }
        if (_saveTimer) {
            clearTimeout(_saveTimer);
        }
        _saveTimer = setTimeout(async function () {
            const payload = Object.assign({}, _pendingPatch);
            _pendingPatch = {};
            _saveTimer = null;
            if (!Object.keys(payload).length) return;
            try {
                await fetch("/api/state", {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
            } catch (_err) {}
        }, delayMs);
    };
})();
