// ui/js/dashboard.js

// Central Chart & Canvas Lifecycle Registry
const chartRegistry = new Map();

function renderExpertiseChart(canvasId, rawClusters, renderBuilderFn) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    if (chartRegistry.has(canvasId)) {
        const prevInstance = chartRegistry.get(canvasId);
        try {
            if (typeof prevInstance.destroy === 'function') {
                prevInstance.destroy();
            } else if (typeof prevInstance.dispose === 'function') {
                prevInstance.dispose();
            }
        } catch (e) {
            console.warn(`[ChartRegistry] Cleanup warning for ${canvasId}:`, e);
        }
        chartRegistry.delete(canvasId);
    }

    const safeClusters = Array.isArray(rawClusters)
        ? rawClusters.map((item) => ({ ...item, sources: item.sources ? [...item.sources] : [] }))
        : [];

    const sortedByMastery = [...safeClusters].sort((a, b) => {
        const vDiff = (b.verified ? 1 : 0) - (a.verified ? 1 : 0);
        if (vDiff !== 0) return vDiff;
        return (Number(b.score) || 0) - (Number(a.score) || 0);
    });

    if (typeof renderBuilderFn === 'function') {
        const newInstance = renderBuilderFn(canvas.getContext('2d'), sortedByMastery);
        if (newInstance) {
            chartRegistry.set(canvasId, newInstance);
        }
        return newInstance;
    }
    return null;
}

function destroyAllChartsOnViewUnmount() {
    for (const [canvasId, instance] of chartRegistry.entries()) {
        try {
            if (instance && typeof instance.destroy === 'function') {
                instance.destroy();
            } else if (instance && typeof instance.dispose === 'function') {
                instance.dispose();
            }
        } catch (_) {}
        chartRegistry.delete(canvasId);
    }
}

window.chartRegistry = chartRegistry;
window.renderExpertiseChart = renderExpertiseChart;
window.destroyAllChartsOnViewUnmount = destroyAllChartsOnViewUnmount;

const DEFAULT_RANK_CONFIG = {
    examName: "NEET PG (Default)",
    totalQuestions: 200,
    marksPerCorrect: 4,
    negativePerWrong: 1,
    maxMarks: 800,
    dataPoints: [
        { marks: 700, rank: 100 },
        { marks: 600, rank: 5000 },
        { marks: 500, rank: 25000 },
        { marks: 400, rank: 60000 }
    ],
    bands: []
};

function _normalizeTimestampMs(ts) {
    const num = Number(ts || 0);
    if (!num) return 0;
    return num < 1e11 ? num * 1000 : num;
}

function getLocalDateString(dateObj = new Date()) {
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const day = String(dateObj.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function getTodayString() {
    return getLocalDateString(new Date());
}

function getDayStats(state, dateStr) {
    const results = (state.testResults || []).filter(r => {
        if (r.date && String(r.date).slice(0, 10) === dateStr) return true;
        const tsMs = _normalizeTimestampMs(r.timestamp || r.lastAttempted || r.last_attempted);
        if (!tsMs) return false;
        return getLocalDateString(new Date(tsMs)) === dateStr;
    });

    const hourlyStats = new Map();
    let questionsAnswered = 0;
    let correctAnswers = 0;
    let timeSpentSeconds = 0;

    results.forEach(r => {
        const qCount = Number(r.totalQuestions ?? r.total_questions ?? 0);
        const cCount = Number(r.correctAnswers ?? r.correct_answers ?? 0);
        const tSec = Number(r.timeUsedSeconds ?? r.time_used_seconds ?? 0);

        questionsAnswered += qCount;
        correctAnswers += cCount;
        timeSpentSeconds += tSec;

        const tsMs = _normalizeTimestampMs(r.timestamp || r.lastAttempted || r.last_attempted);
        const hour = tsMs ? new Date(tsMs).getHours() : new Date().getHours();
        if (!hourlyStats.has(hour)) {
            hourlyStats.set(hour, { hour, questionsAnswered: 0, timeSpentSeconds: 0 });
        }
        const hStat = hourlyStats.get(hour);
        hStat.questionsAnswered += qCount;
        hStat.timeSpentSeconds += tSec;
    });

    // Fallback to habitLog entry for today if testResults had no matching timestamp
    if (questionsAnswered === 0 && Array.isArray(state.habitLog)) {
        const todayHabit = state.habitLog.find(h => h && h.date === dateStr);
        if (todayHabit) {
            questionsAnswered = Number(todayHabit.questionsAnswered || 0);
            correctAnswers = Number(todayHabit.correctAnswers || 0);
            timeSpentSeconds = Number(todayHabit.timeSpentSeconds || 0);
        }
    }

    return {
        questionsAnswered,
        correctAnswers,
        timeSpentSeconds,
        accuracy: questionsAnswered > 0 ? (correctAnswers / questionsAnswered) * 100 : 0,
        hourlyStats: Array.from(hourlyStats.values())
    };
}

function simulateRank(config, correct, wrong, actualQuestions) {
    const totalActual = Number(actualQuestions || 0);
    if (totalActual <= 0) return { projectedMarks: 0, estimatedRank: 0, percentile: "0.0", rawMarks: 0 };

    const cVal = Number(correct || 0);
    const wVal = Number(wrong || 0);
    const marksPerCorr = Number(config.marksPerCorrect || 4);
    const negPerWrong = Number(config.negativePerWrong ?? 1);
    const examTotalQs = Number(config.totalQuestions || 200);

    const rawMarks = (cVal * marksPerCorr) - (wVal * negPerWrong);
    let projectedMarks = rawMarks * (examTotalQs / totalActual);
    projectedMarks = Math.max(0, Math.round(projectedMarks));

    const dp = [...(config.dataPoints || [])].sort((a, b) => Number(b.marks) - Number(a.marks));
    let estimatedRank = 50000;

    if (dp.length > 0) {
        if (projectedMarks >= dp[0].marks) {
            estimatedRank = Number(dp[0].rank || 1);
        } else if (projectedMarks <= dp[dp.length - 1].marks) {
            estimatedRank = Number(dp[dp.length - 1].rank || 50000);
        } else {
            for (let i = 0; i < dp.length - 1; i++) {
                if (projectedMarks <= dp[i].marks && projectedMarks >= dp[i + 1].marks) {
                    const markDiff = dp[i].marks - dp[i + 1].marks;
                    const rankDiff = dp[i + 1].rank - dp[i].rank;
                    const markOffset = dp[i].marks - projectedMarks;
                    const fraction = markDiff === 0 ? 0 : markOffset / markDiff;
                    estimatedRank = Math.round(dp[i].rank + (fraction * rankDiff));
                    break;
                }
            }
        }
    }

    const TOTAL_CANDIDATES = 200000;
    const percentile = Math.max(0, ((TOTAL_CANDIDATES - estimatedRank) / TOTAL_CANDIDATES) * 100).toFixed(1);
    return { projectedMarks, estimatedRank, percentile, rawMarks };
}

let _dueSummaryData = { total_due: 0, session_id: null };
let _dashboardBanners = [];
let _activeBannerIndex = 0;
let _bannerAutoCycleTimer = null;
let _bannerRotationIntervalTimer = null;
let _newQuestionsAddedCount = 0;
const MAX_ACTIVE_BANNERS = 10;
const BANNER_AUTO_CYCLE_MS = 7000;       // Auto advance to next banner every 7s
const BANNER_REPLACE_CYCLE_MS = 15000;   // Dynamically fetch and pop up / rotate banners every 15s

// Reserved quota configuration (Requirements 1, 2 & 3)
// 2 for weak topics, 2 for revision sets, 2 for motivation, 2 for habit tracking / daily goals, rest for others
const CAROUSEL_RESERVED_QUOTAS = {
    weak_topic: 2,
    review_set: 2,
    motivation: 2,
    habit_goal: 2
};

function _getBannerCategory(banner) {
    if (!banner) return 'other';
    const type = banner.type;
    if (type === 'weak_topic') return 'weak_topic';
    if (type === 'due_review' || type === 'new_review_questions' || banner.action === 'review_due') return 'review_set';
    if (type === 'encouragement' || type === 'motivation') return 'motivation';
    if (type === 'habit_goal' || type === 'habit') return 'habit_goal';
    return 'other';
}

/**
 * Builds dynamic habit tracking / daily goals banners from AppState.
 */
function generateHabitGoalBanners() {
    const banners = [];
    if (!window.AppState || typeof window.AppState.get !== 'function') return banners;
    const state = window.AppState.get() || {};
    const dailyTargets = state.dailyTargets || { questionsTarget: 50 };
    const targetQuestions = Math.max(1, Number(dailyTargets.questionsTarget || 50));

    // Calculate today's stats
    const todayStr = getTodayString();
    let todayCount = 0;
    if (Array.isArray(state.habitLog)) {
        const todayHabit = state.habitLog.find(h => h && String(h.date).slice(0, 10) === todayStr);
        if (todayHabit) todayCount = Number(todayHabit.questionsAnswered || 0);
    }
    if (todayCount === 0 && Array.isArray(state.testResults)) {
        const resultsToday = state.testResults.filter(r => {
            if (r.date && String(r.date).slice(0, 10) === todayStr) return true;
            const tsMs = _normalizeTimestampMs(r.timestamp || r.lastAttempted || r.last_attempted);
            return tsMs && getLocalDateString(new Date(tsMs)) === todayStr;
        });
        todayCount = resultsToday.reduce((acc, r) => acc + Number(r.totalQuestions ?? r.total_questions ?? 0), 0);
    }

    const pct = Math.min(100, Math.round((todayCount / targetQuestions) * 100));
    const streakDays = Number(state.streakDays || 0);

    // 1. General Daily Goal Banner
    if (todayCount >= targetQuestions) {
        banners.push({
            id: 'habit_goal_completed_banner',
            type: 'habit_goal',
            title: `Daily Goal Achieved! 🏆 (${todayCount}/${targetQuestions} Qs)`,
            message: `Outstanding work! You smashed today's goal of ${targetQuestions} questions. ${streakDays > 0 ? `Current streak: ${streakDays} day${streakDays === 1 ? '' : 's'} 🔥` : 'Keep the momentum going!'}`,
            icon: '🏆',
            badge: `${pct}% Complete`,
            action: 'view_habits',
            action_label: 'View Habits →'
        });
    } else {
        const remaining = targetQuestions - todayCount;
        banners.push({
            id: 'habit_goal_daily_banner',
            type: 'habit_goal',
            title: `Daily Goal: ${todayCount}/${targetQuestions} Qs (${pct}%) 🎯`,
            message: `${remaining} more question${remaining === 1 ? '' : 's'} needed today to reach your goal. Consistency turns practice into mastery!`,
            icon: '🎯',
            badge: `${pct}% Done`,
            action: 'view_habits',
            action_label: 'Open Tracker →'
        });
    }

    // 2. Custom Daily Goals or Streak Banner
    const customGoals = Array.isArray(dailyTargets.customGoals) ? dailyTargets.customGoals : [];
    if (customGoals.length > 0) {
        const activeCustom = customGoals[0];
        const cTarget = Math.max(1, Number(activeCustom.targetQuestions || 10));
        let cCount = 0;
        if (Array.isArray(state.testResults)) {
            const todayTests = state.testResults.filter(r => {
                if (r.date && String(r.date).slice(0, 10) === todayStr) return true;
                const tsMs = _normalizeTimestampMs(r.timestamp || r.lastAttempted || r.last_attempted);
                return tsMs && getLocalDateString(new Date(tsMs)) === todayStr;
            });
            const filterVal = (activeCustom.value || '').toLowerCase().trim();
            todayTests.forEach(r => {
                const subj = (r.subjectName || r.subject_name || '').toLowerCase();
                const customT = (r.customTitle || r.custom_title || '').toLowerCase();
                if (subj.includes(filterVal) || customT.includes(filterVal)) {
                    cCount += Number(r.totalQuestions ?? r.total_questions ?? 0);
                }
            });
        }
        const cPct = Math.min(100, Math.round((cCount / cTarget) * 100));
        banners.push({
            id: `habit_goal_custom_${_to_synthesis_key(activeCustom.name || activeCustom.value || 'custom')}`,
            type: 'habit_goal',
            title: `Custom Goal: ${activeCustom.name || activeCustom.value} 📌`,
            message: `Progress: ${cCount}/${cTarget} Qs (${cPct}%). Target focus on ${activeCustom.targetType || 'subject'}: ${activeCustom.value}.`,
            icon: '📌',
            badge: `${cPct}% Completed`,
            action: 'view_habits',
            action_label: 'View Goal →'
        });
    } else {
        banners.push({
            id: 'habit_streak_banner',
            type: 'habit_goal',
            title: `Study Streak: ${streakDays} Day${streakDays === 1 ? '' : 's'} 🔥`,
            message: streakDays > 0
                ? `You've studied ${streakDays} day${streakDays === 1 ? '' : 's'} consecutively. Keep answering questions today to preserve your flame!`
                : "Start a study streak today! Answering even a few questions every day builds lasting clinical recall.",
            icon: '🔥',
            badge: `${streakDays} Days`,
            action: 'view_habits',
            action_label: 'Habit Log →'
        });
    }

    return banners;
}

/**
 * Inserts or updates an incoming banner according to reserved carousel quotas (Requirement 1 & 2):
 * - 2 slots reserved for weak_topic
 * - 2 slots reserved for review_set
 * - 2 slots reserved for motivation
 * - 2 slots reserved for habit_goal
 * - Remaining slots filled with other types (e.g. switch_subject).
 * - New banners replace unreserved/oldest banners first before replacing currently shown banner of the same type.
 */
function integrateBannerWithReservations(newBanner) {
    if (!newBanner || !newBanner.id) return;
    const existingIdx = _dashboardBanners.findIndex(b => b.id === newBanner.id);
    if (existingIdx !== -1) {
        _dashboardBanners[existingIdx] = newBanner;
        return;
    }

    const cat = _getBannerCategory(newBanner);
    const catBanners = _dashboardBanners.filter(b => _getBannerCategory(b) === cat);
    const quota = CAROUSEL_RESERVED_QUOTAS[cat] ?? 2;

    if (_dashboardBanners.length < MAX_ACTIVE_BANNERS) {
        if (catBanners.length < quota) {
            _dashboardBanners.push(newBanner);
            return;
        }
    }

    // If banner list is at or above capacity, or category quota is full:
    // First, try to replace oldest unreserved banner (cat 'other' or category exceeding its quota)
    let replaceIdx = -1;
    for (let i = 0; i < _dashboardBanners.length; i++) {
        if (i === _activeBannerIndex) continue; // Do not replace the currently viewed banner
        const bCat = _getBannerCategory(_dashboardBanners[i]);
        const bQuota = CAROUSEL_RESERVED_QUOTAS[bCat] ?? 0;
        const bCatCount = _dashboardBanners.filter(b => _getBannerCategory(b) === bCat).length;
        if (bCat === 'other' || bCatCount > bQuota) {
            replaceIdx = i;
            break;
        }
    }

    // If no unreserved banner can be replaced, replace oldest banner of the same category
    if (replaceIdx === -1) {
        for (let i = 0; i < _dashboardBanners.length; i++) {
            if (i === _activeBannerIndex) continue;
            if (_getBannerCategory(_dashboardBanners[i]) === cat) {
                replaceIdx = i;
                break;
            }
        }
    }

    // If still no replacement found, replace oldest non-active banner
    if (replaceIdx === -1) {
        for (let i = 0; i < _dashboardBanners.length; i++) {
            if (i !== _activeBannerIndex) {
                replaceIdx = i;
                break;
            }
        }
    }

    if (replaceIdx !== -1) {
        _dashboardBanners[replaceIdx] = newBanner;
    } else if (_dashboardBanners.length < MAX_ACTIVE_BANNERS) {
        _dashboardBanners.push(newBanner);
    }
}

/**
 * Removes a specific banner by id (Requirement 3).
 * When review banner action is clicked, it is removed and frees space for other banners.
 */
function removeBannerById(bannerId) {
    if (!bannerId) return;
    const idx = _dashboardBanners.findIndex(b => b.id === bannerId);
    if (idx !== -1) {
        _dashboardBanners.splice(idx, 1);
        if (_activeBannerIndex >= _dashboardBanners.length) {
            _activeBannerIndex = Math.max(0, _dashboardBanners.length - 1);
        }
        renderBannerCarousel();
    }
}

async function fetchDueSummary() {
    try {
        const res = await fetch('/api/sessions/due_summary');
        if (res.ok) {
            _dueSummaryData = await res.json();
        }
    } catch (e) {
        console.warn("Failed to fetch due summary:", e);
    }
}

/**
 * Creates or updates the "n questions left to review" banner in-place.
 */
function updateQuestionsLeftBanner(totalDue) {
    const count = Number(totalDue || 0);
    const existingIdx = _dashboardBanners.findIndex(b => b.id === 'due_review_banner' || b.type === 'due_review');

    if (count > 0) {
        const bannerObj = {
            id: 'due_review_banner',
            type: 'due_review',
            title: `${count} question${count === 1 ? '' : 's'} left to review 🧠`,
            message: `You have ${count} question${count === 1 ? '' : 's'} left to review in your consolidated review set.`,
            icon: '🧠',
            badge: `${count} Left to Review`,
            action: 'review_due',
            action_label: `Review Set (${count}) →`,
            session_id: _dueSummaryData.session_id || 'sess_spaced_repetition_due',
            count: count
        };

        if (existingIdx !== -1) {
            _dashboardBanners[existingIdx] = bannerObj;
        } else {
            integrateBannerWithReservations(bannerObj);
        }
    } else if (existingIdx !== -1) {
        _dashboardBanners.splice(existingIdx, 1);
    }

    if (_dashboardBanners.length > MAX_ACTIVE_BANNERS) {
        _dashboardBanners = _dashboardBanners.slice(0, MAX_ACTIVE_BANNERS);
    }
}

/**
 * Creates or updates the "n new questions to review" banner in-place.
 */
function updateNewQuestionsBanner(newCount) {
    const count = Number(newCount || 0);
    const existingIdx = _dashboardBanners.findIndex(b => b.id === 'new_review_questions_banner' || b.type === 'new_review_questions');

    if (count > 0) {
        _newQuestionsAddedCount = count;
        const bannerObj = {
            id: 'new_review_questions_banner',
            type: 'new_review_questions',
            title: `${count} new question${count === 1 ? '' : 's'} to review 🔔`,
            message: `${count} new question${count === 1 ? '' : 's'} from your latest session ${count === 1 ? 'has' : 'have'} been added to your review set.`,
            icon: '🔔',
            badge: `${count} New to Review`,
            action: 'review_due',
            action_label: `Review Now (${count}) →`,
            session_id: _dueSummaryData.session_id || 'sess_spaced_repetition_due',
            count: count
        };

        if (existingIdx !== -1) {
            _dashboardBanners[existingIdx] = bannerObj;
        } else {
            integrateBannerWithReservations(bannerObj);
        }
    } else if (existingIdx !== -1) {
        _dashboardBanners.splice(existingIdx, 1);
    }

    if (_dashboardBanners.length > MAX_ACTIVE_BANNERS) {
        _dashboardBanners = _dashboardBanners.slice(0, MAX_ACTIVE_BANNERS);
    }
}

/**
 * Public update methods for external triggers
 */
window.updateQuestionsLeftToReview = async function() {
    await fetchDueSummary();
    const count = Number(_dueSummaryData.total_due || 0);
    updateQuestionsLeftBanner(count);
    renderBannerCarousel();
};

window.updateNewQuestionsToReview = function(addedCount) {
    updateNewQuestionsBanner(addedCount);
    // Focus carousel on the new questions banner when new questions are added
    const bannerIdx = _dashboardBanners.findIndex(b => b.id === 'new_review_questions_banner');
    if (bannerIdx !== -1) {
        _activeBannerIndex = bannerIdx;
    }
    renderBannerCarousel();
};

async function fetchDashboardPrompts() {
    try {
        const res = await fetch('/api/sessions/dashboard_prompts');
        if (res.ok) {
            const data = await res.json();
            const incoming = Array.isArray(data.banners) ? data.banners : [];

            // 1. Revision set banners (up to 2 slots reserved)
            const incomingDue = incoming.filter(b => b.id === 'due_review_banner' || b.type === 'due_review');
            const dueCount = incomingDue.length > 0 ? Number(incomingDue[0].count || _dueSummaryData.total_due || 0) : Number(_dueSummaryData.total_due || 0);
            updateQuestionsLeftBanner(dueCount);

            if (_newQuestionsAddedCount > 0) {
                updateNewQuestionsBanner(_newQuestionsAddedCount);
            }

            // 2. Habit tracking & daily goals banners (up to 2 slots reserved)
            const habitBanners = generateHabitGoalBanners();
            for (const hb of habitBanners) {
                integrateBannerWithReservations(hb);
            }

            // 3. Integrate weak topics, motivation, and other banners with slot quotas
            const otherIncoming = incoming.filter(b => b.id !== 'due_review_banner' && b.type !== 'due_review' && b.id !== 'new_review_questions_banner' && b.type !== 'new_review_questions');
            for (const b of otherIncoming) {
                integrateBannerWithReservations(b);
            }

            if (_activeBannerIndex >= _dashboardBanners.length) {
                _activeBannerIndex = 0;
            }
        }
    } catch (e) {
        console.warn("Failed to fetch dashboard prompts:", e);
    }
}

async function initDashboard() {
    if (!window.AppState || !window.AppState.get) return;
    const state = window.AppState.get();
    if (!state) return;

    const dashContainer = document.getElementById('dashboard-view');
    if (!dashContainer) return;

    await fetchDueSummary();
    await fetchDashboardPrompts();

    const escapeHTML = window.escapeHTML || ((s) => String(s || ''));

    // Aggregations
    const testResults = (state.testResults || []).map(r => {
        const totalQ = Number(r.totalQuestions ?? r.total_questions ?? 0);
        const corrQ = Number(r.correctAnswers ?? r.correct_answers ?? 0);
        const wrongQ = Number(r.wrongAnswers ?? r.wrong_answers ?? Math.max(0, totalQ - corrQ));
        const acc = r.accuracy !== undefined && r.accuracy !== null
            ? Number(r.accuracy)
            : (totalQ > 0 ? (corrQ / totalQ) * 100 : 0);
        const tsMs = _normalizeTimestampMs(r.timestamp || r.lastAttempted || r.last_attempted) || Date.now();
        return {
            ...r,
            subjectName: r.subjectName || r.subject_name || r.custom_title || 'General',
            mode: r.mode || 'learn',
            totalQuestions: totalQ,
            correctAnswers: corrQ,
            wrongAnswers: wrongQ,
            score: Number(r.score ?? corrQ),
            accuracy: acc,
            timeUsedSeconds: Number(r.timeUsedSeconds ?? r.time_used_seconds ?? 0),
            timestamp: tsMs
        };
    });

    const rankSimConfigs = (Array.isArray(state.rankSimConfigs) && state.rankSimConfigs.length > 0)
        ? state.rankSimConfigs
        : [DEFAULT_RANK_CONFIG];

    const totalTests = testResults.length;
    const avgAccuracy = totalTests > 0
        ? testResults.reduce((a, r) => a + Number(r.accuracy || 0), 0) / totalTests
        : 0;
    const totalTimeMinutes = Math.round(
        testResults.reduce((a, r) => a + Number(r.timeUsedSeconds || 0), 0) / 60
    );
    const recentResults = [...testResults].sort((a, b) => b.timestamp - a.timestamp).slice(0, 10);
    const sumFromResults = testResults.reduce((a, r) => a + r.totalQuestions, 0);
    const totalQuestionsAnswered = Math.max(Number(state.totalQuestionsEver || 0), sumFromResults);

    const todayStats = getDayStats({ ...state, testResults }, getTodayString());

    // Spaced Repetition Stats (Prefer exact SQLite fsrsCounts from state.js, fallback to ankiCards)
    let ankiStats = { mastered: 0, review: 0, learning: 0, new: 0 };
    let totalAnkiCards = 0;
    if (state.fsrsCounts && Number(state.fsrsCounts.total || 0) > 0) {
        ankiStats = {
            mastered: Number(state.fsrsCounts.mastered || 0),
            review: Number(state.fsrsCounts.review || 0),
            learning: Number(state.fsrsCounts.learning || 0),
            new: Number(state.fsrsCounts.new || 0)
        };
        totalAnkiCards = Number(state.fsrsCounts.total || 0);
    } else {
        const ankiCards = state.ankiCards || [];
        ankiStats = ankiCards.reduce((acc, c) => {
            if (c && c.status) acc[c.status] = (acc[c.status] || 0) + 1;
            return acc;
        }, { mastered: 0, review: 0, learning: 0, new: 0 });
        totalAnkiCards = ankiCards.length;
    }

    // Subject Performance (merge state.subjects with any subjects present in testResults)
    const subjectNamesSet = new Set();
    (state.subjects || []).forEach(s => {
        if (s && s.name) subjectNamesSet.add(s.name);
    });
    testResults.forEach(r => {
        if (r.subjectName) subjectNamesSet.add(r.subjectName);
    });

    const subjectPerformance = Array.from(subjectNamesSet).map(name => {
        const sr = testResults.filter(r => r.subjectName === name);
        const avg = sr.length > 0 ? sr.reduce((a, r) => a + r.accuracy, 0) / sr.length : 0;
        return { name, tests: sr.length, avgAccuracy: avg };
    }).sort((a, b) => b.tests - a.tests || b.avgAccuracy - a.avgAccuracy);

    // Rank Simulations: Compute trailing window of up to 200 questions across sorted recent results
    const sortedAllResults = [...testResults].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    let past200Correct = 0;
    let past200Wrong = 0;
    let past200Total = 0;
    for (const r of sortedAllResults) {
        const tQ = Number(r.totalQuestions || 0);
        if (tQ <= 0) continue;
        const remaining = 200 - past200Total;
        if (remaining <= 0) break;
        if (tQ <= remaining) {
            past200Correct += Number(r.correctAnswers || 0);
            past200Wrong += Number(r.wrongAnswers || 0);
            past200Total += tQ;
        } else {
            const ratio = remaining / tQ;
            past200Correct += Math.round(Number(r.correctAnswers || 0) * ratio);
            past200Wrong += Math.round(Number(r.wrongAnswers || 0) * ratio);
            past200Total += remaining;
            break;
        }
    }

    const processedRankSims = rankSimConfigs.map(config => {
        const testsWithRank = recentResults.filter(r => r.totalQuestions > 0).map(r => ({
            ...r,
            sim: simulateRank(config, r.correctAnswers, r.wrongAnswers, r.totalQuestions)
        }));
        if (testsWithRank.length === 0) return null;
        const latest = testsWithRank[0];
        const best = testsWithRank.reduce(
            (b, t) => t.sim.estimatedRank < b.sim.estimatedRank ? t : b,
            testsWithRank[0]
        );

        // Average rank calculated from past 200 questions
        const avg200Sim = past200Total > 0
            ? simulateRank(config, past200Correct, past200Wrong, past200Total)
            : latest.sim;

        return { config, testsWithRank, latest, best, avg200Sim, past200Total };
    }).filter(s => s !== null);

    // Hourly Data Formatting
    const hourlyData = Array.from({ length: 24 }, (_, i) => {
        const hStat = todayStats.hourlyStats.find(h => h.hour === i);
        return { hour: i, questions: hStat ? hStat.questionsAnswered : 0 };
    });
    const maxQ = Math.max(...hourlyData.map(h => h.questions), 1);

    // --- Dynamic Render ---
    dashContainer.innerHTML = `
      <div class="w-full p-6 animate-fade-in">
        <div class="mb-8">
            <h1 class="text-3xl font-bold mb-2">📊 Dashboard</h1>
            <p class="subtitle">Your learning analytics at a glance</p>
        </div>

        <!-- Dynamic Multi-Banner Recommendations & Encouragement Carousel -->
        <div id="dash-banner-carousel-container" class="mb-8">
            <!-- Rendered by renderBannerCarousel() -->
        </div>

        <!-- Today's Summary -->
        <div class="glass-card card-shade-1 mb-8">
          <h2 class="text-lg font-semibold mb-4 opacity-90">Today's Summary</h2>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div class="text-center">
              <p class="text-3xl font-bold">${todayStats.questionsAnswered}</p>
              <p class="text-xs mt-1 text-muted">Questions Answered</p>
            </div>
            <div class="text-center">
              <p class="text-3xl font-bold">${todayStats.correctAnswers}</p>
              <p class="text-xs mt-1 text-muted">Correct Answers</p>
            </div>
            <div class="text-center">
              <p class="text-3xl font-bold">${todayStats.accuracy.toFixed(1)}%</p>
              <p class="text-xs mt-1 text-muted">Accuracy</p>
            </div>
            <div class="text-center">
              <p class="text-3xl font-bold">${Math.round(todayStats.timeSpentSeconds / 60)}m</p>
              <p class="text-xs mt-1 text-muted">Time Spent</p>
            </div>
          </div>
        </div>

        <!-- Overall Stats -->
        <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
          <div class="glass-card card-shade-2 text-center" style="padding: 16px;">
            <p class="text-xl font-bold">${totalTests}</p>
            <p class="text-xs text-muted mt-1">Tests Taken</p>
          </div>
          <div class="glass-card card-shade-2 text-center" style="padding: 16px;">
            <p class="text-xl font-bold">${avgAccuracy.toFixed(1)}%</p>
            <p class="text-xs text-muted mt-1">Avg Accuracy</p>
          </div>
          <div class="glass-card card-shade-2 text-center" style="padding: 16px;">
            <p class="text-xl font-bold">${totalQuestionsAnswered.toLocaleString()}</p>
            <p class="text-xs text-muted mt-1">Questions Done</p>
          </div>
          <div class="glass-card card-shade-2 text-center" style="padding: 16px;">
            <p class="text-xl font-bold">${totalTimeMinutes}m</p>
            <p class="text-xs text-muted mt-1">Total Time</p>
          </div>
          <div class="glass-card card-shade-2 text-center" style="padding: 16px;">
            <p class="text-xl font-bold">${state.streakDays || 0}</p>
            <p class="text-xs text-muted mt-1">Day Streak 🔥</p>
          </div>
          <div class="glass-card card-shade-2 text-center" style="padding: 16px;">
            <p class="text-xl font-bold">${state.longestStreak || 0}</p>
            <p class="text-xs text-muted mt-1">Best Streak</p>
          </div>
        </div>

        <div class="grid md:grid-cols-2 gap-6 mb-8">
          <!-- SR Status (Requirement 9: Flexible Effort-Based Scaling and Colorful Bars) -->
          <div class="glass-card card-shade-3">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-lg font-semibold opacity-90">🧠 Spaced Repetition Status</h3>
              <span class="text-xs text-muted font-mono">${(ankiStats.mastered + ankiStats.review + ankiStats.learning).toLocaleString()} active / ${totalAnkiCards.toLocaleString()} total</span>
            </div>
            <div class="space-y-4 mt-2">
              ${(() => {
                  // Total active deck cards engaged by the user
                  const activeDeckCount = (ankiStats.mastered || 0) + (ankiStats.review || 0) + (ankiStats.learning || 0);
                  // Daily effort benchmark: today's questions answered gives immediate positive expansion
                  const todayEffort = Math.max(Number(todayStats.questionsAnswered || 0), 10);
                  // Effective denominator for active card progress: flexible benchmark prevents 200k+ new cards from flattening active bars
                  const activeDenominator = Math.max(activeDeckCount, todayEffort * 3, 20);

                  const barConfigs = [
                      {
                          level: 'mastered',
                          label: 'Mastered',
                          color: 'bg-gradient-to-r from-emerald-500 to-teal-400 shadow-[0_0_10px_rgba(16,185,129,0.5)]',
                          textColor: 'text-emerald-400'
                      },
                      {
                          level: 'review',
                          label: 'Review',
                          color: 'bg-gradient-to-r from-blue-500 to-indigo-500 shadow-[0_0_10px_rgba(59,130,246,0.5)]',
                          textColor: 'text-blue-400'
                      },
                      {
                          level: 'learning',
                          label: 'Learning',
                          color: 'bg-gradient-to-r from-amber-500 to-orange-400 shadow-[0_0_10px_rgba(245,158,11,0.5)]',
                          textColor: 'text-amber-400'
                      },
                      {
                          level: 'new',
                          label: 'New Pool',
                          color: 'bg-gradient-to-r from-purple-500 to-pink-500 shadow-[0_0_8px_rgba(168,85,247,0.4)]',
                          textColor: 'text-purple-300'
                      }
                  ];

                  return barConfigs.map(cfg => {
                      const count = ankiStats[cfg.level] || 0;
                      let widthPct = 0;
                      if (cfg.level === 'new') {
                          // Unstudied pool: relative to total cards or safe baseline
                          widthPct = totalAnkiCards > 0 ? Math.min(100, Math.max(5, (count / totalAnkiCards) * 100)) : 0;
                      } else {
                          // Active cards: flexible effort-based scaling relative to activeDenominator with minimum visual visibility
                          if (count > 0) {
                              widthPct = Math.min(100, Math.max(8, (count / activeDenominator) * 100));
                          }
                      }

                      return `
                      <div class="flex items-center justify-between">
                        <span class="text-sm capitalize font-medium w-24 ${cfg.textColor}">${cfg.label}</span>
                        <div class="flex-1 mx-4 glass-panel rounded-full h-3 shadow-inner border border-gray-700/50 overflow-hidden">
                          <div class="${cfg.color} h-3 rounded-full transition-all duration-700" style="width: ${widthPct.toFixed(1)}%"></div>
                        </div>
                        <span class="text-sm font-bold w-16 text-right font-mono">${count.toLocaleString()}</span>
                      </div>`;
                  }).join('');
              })()}
            </div>
          </div>

          <!-- Subject Performance -->
          <div class="glass-card card-shade-4">
            <h3 class="text-lg font-semibold mb-4 opacity-90">📚 Subject Performance</h3>
            ${subjectPerformance.length === 0 ? '<p class="text-muted text-sm">No subjects loaded yet.</p>' : `
            <div class="space-y-3 max-h-48 overflow-y-auto pr-2">
              ${subjectPerformance.map(sp => `
                <div class="glass-panel rounded-lg p-3 border border-gray-700/50">
                  <div class="flex justify-between items-center mb-1">
                    <span class="text-sm font-medium truncate flex-1">${escapeHTML(sp.name)}</span>
                    <span class="text-xs text-muted ml-2">${sp.tests} test${sp.tests === 1 ? '' : 's'}</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <div class="flex-1 glass-panel rounded-full h-2">
                      <div class="h-2 rounded-full ${sp.avgAccuracy >= 70 ? 'bg-success-500' : sp.avgAccuracy >= 50 ? 'bg-warning-500' : 'bg-danger-500'}" style="width: ${Math.min(100, Math.max(0, sp.avgAccuracy))}%"></div>
                    </div>
                    <span class="text-xs w-10 text-right font-mono">${sp.avgAccuracy.toFixed(0)}%</span>
                  </div>
                </div>
              `).join('')}
            </div>`}
          </div>
        </div>

        <div class="grid md:grid-cols-2 gap-6 mb-8">
            <!-- Hourly Activity (Requirement 9: Flexible Scaling & Colorful Bars) -->
            <div class="glass-card">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold opacity-90">🕒 Today's Activity by Hour</h3>
                    <span class="text-xs text-brand-300 font-medium">Peak: ${maxQ} Qs/hr</span>
                </div>
                <div class="flex items-end gap-1.5 h-32 mb-2 border-b border-gray-700/50 pb-1">
                    ${hourlyData.map(h => {
                        // Dynamic colorful styling based on effort and hour period
                        let barColor = 'glass-panel';
                        if (h.questions > 0) {
                            const ratio = h.questions / maxQ;
                            if (ratio >= 0.75) {
                                barColor = 'bg-gradient-to-t from-pink-500 via-purple-500 to-indigo-400 shadow-[0_0_12px_rgba(236,72,153,0.7)]';
                            } else if (ratio >= 0.4) {
                                barColor = 'bg-gradient-to-t from-cyan-500 to-blue-400 shadow-[0_0_10px_rgba(6,182,212,0.6)]';
                            } else {
                                barColor = 'bg-gradient-to-t from-emerald-500 to-teal-400 shadow-[0_0_8px_rgba(16,185,129,0.5)]';
                            }
                        }
                        // Flexible height scaling: minimum 8% for any activity, scaled to peak
                        const barHeight = h.questions > 0 ? Math.max(Math.round((h.questions / maxQ) * 100), 8) : 2;

                        return `
                        <div class="flex-1 flex flex-col items-center group relative cursor-crosshair h-full justify-end">
                            <div class="w-full rounded-t transition-all ${barColor} hover:brightness-125"
                                 style="height: ${barHeight}%;"></div>
                            ${h.questions > 0 ? `<div class="absolute bottom-full mb-2 glass-card border border-brand-500/40 rounded-lg px-2.5 py-1 text-xs opacity-0 group-hover:opacity-100 transition-opacity z-10 whitespace-nowrap shadow-lg font-bold">${h.questions} Qs (${h.hour}:00)</div>` : ''}
                        </div>`;
                    }).join('')}
                </div>
                <div class="flex justify-between text-xs text-muted">
                    <span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span>
                </div>
            </div>

            <!-- Rank Simulation Preparedness -->
            <div class="glass-card relative">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold opacity-90">🏅 Rank Preparedness</h3>
                    <button id="open-rank-editor-btn" class="px-3 py-1.5 secondary-btn rounded-lg text-xs transition-colors cursor-pointer">
                        ⚙️ Edit Config
                    </button>
                </div>
                ${processedRankSims.length === 0 ? '<p class="text-muted text-sm mt-4">Take a test to see rank projections.</p>' : `
                <div class="space-y-4 max-h-48 overflow-y-auto pr-2">
                    ${processedRankSims.map(simData => `
                    <div class="border border-gray-700/50 rounded-xl p-4 glass-panel">
                        <div class="flex justify-between items-center mb-3">
                            <p class="text-sm font-semibold opacity-90">${escapeHTML(simData.config.examName)}</p>
                            <span class="text-xs glass-panel px-2 py-1 rounded opacity-70">Max: ${simData.config.maxMarks || (simData.config.totalQuestions * simData.config.marksPerCorrect)}</span>
                        </div>
                        <div class="grid grid-cols-3 gap-3">
                            <div class="glass-panel rounded-lg p-2 text-center">
                                <p class="text-xs text-muted mb-1">Latest Rank</p>
                                <p class="text-lg font-bold text-brand-400">${simData.latest.sim.estimatedRank.toLocaleString()}</p>
                            </div>
                            <div class="glass-panel rounded-lg p-2 text-center">
                                <p class="text-xs text-muted mb-1">Best Rank</p>
                                <p class="text-lg font-bold text-success-500">${simData.best.sim.estimatedRank.toLocaleString()}</p>
                            </div>
                            <div class="glass-panel rounded-lg p-2 text-center">
                                <p class="text-xs text-muted mb-1">Avg Rank (Past 200 Qs)</p>
                                <p class="text-lg font-bold text-purple-400">${simData.avg200Sim.estimatedRank.toLocaleString()}</p>
                            </div>
                        </div>
                    </div>
                    `).join('')}
                </div>`}
            </div>
        </div>

        <!-- Recent Results -->
        <div class="glass-card">
          <h3 class="text-lg font-semibold mb-4 opacity-90">📝 Recent Test Results</h3>
          ${recentResults.length === 0 ? '<p class="text-muted text-sm">No tests taken yet.</p>' : `
          <div class="overflow-x-auto">
            <table class="w-full text-sm text-left">
              <thead>
                <tr class="border-b border-gray-700/50 text-muted">
                  <th class="py-3 px-2 font-medium">Date</th>
                  <th class="py-3 px-2 font-medium">Subject</th>
                  <th class="py-3 px-2 font-medium">Mode</th>
                  <th class="py-3 px-2 font-medium text-center">Q's</th>
                  <th class="py-3 px-2 font-medium text-center">Score</th>
                  <th class="py-3 px-2 font-medium text-center">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                ${recentResults.map((r, i) => `
                  <tr class="border-b border-gray-700/50 ${i % 2 === 0 ? '' : 'glass-panel'}">
                    <td class="py-2.5 px-2 font-medium">${new Date(r.timestamp).toLocaleDateString()}</td>
                    <td class="py-2.5 px-2 truncate max-w-[150px]">${escapeHTML(r.subjectName)}</td>
                    <td class="py-2.5 px-2">
                      <span class="px-2 py-0.5 rounded-full text-xs font-medium border border-gray-700/50 glass-panel">${escapeHTML(r.mode)}</span>
                    </td>
                    <td class="py-2.5 px-2 text-center">${r.totalQuestions}</td>
                    <td class="py-2.5 px-2 text-center">${r.score}</td>
                    <td class="py-2.5 px-2 text-center font-medium ${r.accuracy >= 70 ? 'text-success-500' : r.accuracy >= 50 ? 'text-warning-500' : 'text-danger-500'}">
                      ${r.accuracy.toFixed(1)}%
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          </div>`}
        </div>
      </div>
    `;

    attachRankEditorListeners();

    renderBannerCarousel();
    startBannerAutoCycle();
    startGradualBannerRotation();
}

function renderBannerCarousel() {
    const container = document.getElementById('dash-banner-carousel-container');
    if (!container) return;

    if (_dashboardBanners.length === 0) {
        container.innerHTML = '';
        return;
    }

    if (_activeBannerIndex >= _dashboardBanners.length) {
        _activeBannerIndex = 0;
    } else if (_activeBannerIndex < 0) {
        _activeBannerIndex = _dashboardBanners.length - 1;
    }

    const currentBanner = _dashboardBanners[_activeBannerIndex];
    const escapeHTML = window.escapeHTML || ((s) => String(s || ''));

    // Theme and styling classes based on banner type
    let borderBgClass = 'border-brand-500/40 bg-brand-500/10 shadow-[0_0_15px_rgba(139,92,246,0.15)]';
    let titleClass = 'text-brand-300';
    let badgeClass = 'bg-brand-500/20 text-brand-300 border border-brand-500/30';

    let animCardClass = '';
    let iconAnimClass = '';

    if (currentBanner.type === 'due_review') {
        borderBgClass = 'border-warning-500/50 bg-warning-500/10 shadow-[0_0_15px_rgba(234,179,8,0.2)]';
        titleClass = 'text-warning-300';
        badgeClass = 'bg-warning-500/20 text-warning-300 border border-warning-500/30';
    } else if (currentBanner.type === 'new_review_questions') {
        borderBgClass = 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_15px_rgba(245,158,11,0.2)]';
        titleClass = 'text-amber-300';
        badgeClass = 'bg-amber-500/20 text-amber-300 border border-amber-500/30';
    } else if (currentBanner.type === 'weak_topic') {
        borderBgClass = 'border-danger-500/40 bg-danger-500/10 shadow-[0_0_15px_rgba(239,68,68,0.15)]';
        titleClass = 'text-danger-300';
        badgeClass = 'bg-danger-500/20 text-danger-300 border border-danger-500/30';
        animCardClass = 'banner-anim-warning';
    } else if (currentBanner.type === 'warning') {
        borderBgClass = 'border-red-500/40 bg-red-500/10 shadow-[0_0_15px_rgba(239,68,68,0.2)]';
        titleClass = 'text-red-300';
        badgeClass = 'bg-red-500/20 text-red-300 border border-red-500/30';
        animCardClass = 'banner-anim-warning';
    } else if (currentBanner.type === 'switch_subject') {
        borderBgClass = 'border-cyan-500/40 bg-cyan-500/10 shadow-[0_0_15px_rgba(6,182,212,0.15)]';
        titleClass = 'text-cyan-300';
        badgeClass = 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30';
    } else if (currentBanner.type === 'encouragement') {
        borderBgClass = 'border-emerald-500/40 bg-emerald-500/10 shadow-[0_0_15px_rgba(16,185,129,0.15)]';
        titleClass = 'text-emerald-300';
        badgeClass = 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
        animCardClass = 'banner-anim-encouragement';
        iconAnimClass = 'banner-sparkle-icon';
    } else if (currentBanner.type === 'motivation') {
        borderBgClass = 'border-amber-500/40 bg-amber-500/10 shadow-[0_0_15px_rgba(245,158,11,0.18)]';
        titleClass = 'text-amber-300';
        badgeClass = 'bg-amber-500/20 text-amber-300 border border-amber-500/30';
        animCardClass = 'banner-anim-motivation';
        iconAnimClass = 'banner-sparkle-icon';
    } else if (currentBanner.type === 'habit_goal') {
        borderBgClass = 'border-purple-500/40 bg-purple-500/10 shadow-[0_0_15px_rgba(168,85,247,0.15)]';
        titleClass = 'text-purple-300';
        badgeClass = 'bg-purple-500/20 text-purple-300 border border-purple-500/30';
    }

    if (currentBanner.anim_style === 'sparkle') {
        iconAnimClass = 'banner-sparkle-icon';
    } else if (currentBanner.anim_style === 'pulse') {
        animCardClass = 'banner-anim-warning';
    }

    container.innerHTML = `
        <div id="dash-banner-card" class="glass-card border ${borderBgClass} ${animCardClass} p-5 rounded-2xl flex flex-col md:flex-row items-center justify-between gap-4 animate-fade-in transition-all relative overflow-hidden group">
            <!-- Icon & Content -->
            <div class="flex items-center gap-4 flex-1 w-full">
                <span class="text-3xl select-none shrink-0 ${iconAnimClass}">${currentBanner.icon || '💡'}</span>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 mb-1 flex-wrap">
                        <h3 class="text-base font-bold ${titleClass} truncate max-w-md">
                            ${escapeHTML(currentBanner.title)}
                        </h3>
                        ${currentBanner.badge ? `
                            <span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full ${badgeClass}">
                                ${escapeHTML(currentBanner.badge)}
                            </span>
                        ` : ''}
                    </div>
                    <p class="text-xs text-muted leading-relaxed">
                        ${escapeHTML(currentBanner.message)}
                    </p>
                </div>
            </div>

            <!-- Controls & Action Button -->
            <div class="flex items-center gap-3 shrink-0 self-end md:self-center">
                ${currentBanner.action_label ? `
                    <button type="button" id="dash-banner-action-btn" class="primary-btn px-4 py-2 rounded-xl text-xs font-bold shadow-lg cursor-pointer flex items-center gap-1.5 hover:scale-105 transition-all">
                        ${escapeHTML(currentBanner.action_label)}
                    </button>
                ` : ''}

                <!-- Carousel Navigation Controls -->
                <div class="flex items-center gap-1 glass-panel px-2 py-1 rounded-xl border border-gray-700/50">
                    <button type="button" id="dash-banner-prev-btn" title="Previous Banner" class="text-muted hover:text-white p-1 rounded transition-colors cursor-pointer text-sm">
                        ◀
                    </button>
                    <span class="text-[11px] font-mono text-muted px-1">
                        ${_activeBannerIndex + 1}/${_dashboardBanners.length}
                    </span>
                    <button type="button" id="dash-banner-next-btn" title="Next Banner" class="text-muted hover:text-white p-1 rounded transition-colors cursor-pointer text-sm">
                        ▶
                    </button>
                </div>
            </div>

            <!-- Carousel Pagination Dots -->
            <div class="absolute bottom-1.5 left-1/2 -translate-x-1/2 flex items-center gap-1.5 opacity-60 group-hover:opacity-100 transition-opacity">
                ${_dashboardBanners.map((_, idx) => `
                    <span class="w-1.5 h-1.5 rounded-full transition-all cursor-pointer ${idx === _activeBannerIndex ? 'bg-white w-4' : 'bg-gray-500 hover:bg-gray-300'}"
                          data-banner-idx="${idx}"></span>
                `).join('')}
            </div>
        </div>
    `;

    // Attach interaction listeners
    const card = document.getElementById('dash-banner-card');
    if (card) {
        card.addEventListener('mouseenter', () => clearInterval(_bannerAutoCycleTimer));
        card.addEventListener('mouseleave', () => startBannerAutoCycle());
    }

    const prevBtn = document.getElementById('dash-banner-prev-btn');
    if (prevBtn) {
        prevBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            _activeBannerIndex = (_activeBannerIndex - 1 + _dashboardBanners.length) % _dashboardBanners.length;
            renderBannerCarousel();
        });
    }

    const nextBtn = document.getElementById('dash-banner-next-btn');
    if (nextBtn) {
        nextBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            _activeBannerIndex = (_activeBannerIndex + 1) % _dashboardBanners.length;
            renderBannerCarousel();
        });
    }

    container.querySelectorAll('[data-banner-idx]').forEach(dot => {
        dot.addEventListener('click', (e) => {
            e.stopPropagation();
            const idx = parseInt(dot.getAttribute('data-banner-idx'), 10);
            if (!isNaN(idx)) {
                _activeBannerIndex = idx;
                renderBannerCarousel();
            }
        });
    });

    const actionBtn = document.getElementById('dash-banner-action-btn');
    if (actionBtn) {
        actionBtn.addEventListener('click', () => handleBannerAction(currentBanner));
    }
}

function startBannerAutoCycle() {
    clearInterval(_bannerAutoCycleTimer);
    if (_dashboardBanners.length <= 1) return;
    _bannerAutoCycleTimer = setInterval(() => {
        _activeBannerIndex = (_activeBannerIndex + 1) % _dashboardBanners.length;
        renderBannerCarousel();
    }, BANNER_AUTO_CYCLE_MS);
}

function startGradualBannerRotation() {
    clearInterval(_bannerRotationIntervalTimer);
    _bannerRotationIntervalTimer = setInterval(async () => {
        // Regularly refresh questions left to review and rotate carousel prompts
        try {
            await fetchDueSummary();
            const res = await fetch('/api/sessions/dashboard_prompts');
            if (res.ok) {
                const data = await res.json();
                const incoming = Array.isArray(data.banners) ? data.banners : [];
                if (incoming.length > 0) {
                    // Always update "questions left to review" banner in-place
                    const incomingDue = incoming.filter(b => b.id === 'due_review_banner' || b.type === 'due_review');
                    const dueCount = incomingDue.length > 0 ? Number(incomingDue[0].count || _dueSummaryData.total_due || 0) : Number(_dueSummaryData.total_due || 0);
                    updateQuestionsLeftBanner(dueCount);

                    if (_newQuestionsAddedCount > 0) {
                        updateNewQuestionsBanner(_newQuestionsAddedCount);
                    }

                    // Refresh habit tracking / daily goals banners
                    const habitBanners = generateHabitGoalBanners();
                    for (const hb of habitBanners) {
                        integrateBannerWithReservations(hb);
                    }

                    const otherIncoming = incoming.filter(b => b.id !== 'due_review_banner' && b.type !== 'due_review' && b.id !== 'new_review_questions_banner' && b.type !== 'new_review_questions');

                    // Pick an incoming candidate banner not currently in carousel and integrate with quota rules
                    const currentIds = new Set(_dashboardBanners.map(b => b.id));
                    const candidate = otherIncoming.find(b => !currentIds.has(b.id));
                    if (candidate) {
                        integrateBannerWithReservations(candidate);
                    }
                    renderBannerCarousel();
                }
            }
        } catch (e) {
            console.debug("Banner rotation check skipped:", e);
        }
    }, BANNER_REPLACE_CYCLE_MS);
}

async function handleBannerAction(banner) {
    if (!banner) return;

    if (banner.action === 'review_due') {
        const sessionId = banner.session_id || _dueSummaryData.session_id || 'sess_spaced_repetition_due';
        const bannerIdToRemove = banner.id;
        // Requirement 3: When review button is clicked, remove the banner to free carousel space
        removeBannerById(bannerIdToRemove);

        try {
            const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/questions`);
            if (res.ok) {
                const questions = await res.json();
                if (questions && questions.length > 0 && typeof window.launchPreparedQuiz === 'function') {
                    window.launchPreparedQuiz({
                        sessionId: sessionId,
                        customTitle: `🧠 Spaced Repetition Due Review (${questions.length})`,
                        subjectName: 'Due Reviews',
                        questions: questions,
                        topicsReviewed: true,
                        config: { mode: 'learn', spacedRepetition: true, count: questions.length }
                    });
                    return;
                }
            }
            const hubNav = document.querySelector('.nav-btn[data-target="knowledge-hub-view"]');
            if (hubNav) hubNav.click();
        } catch (e) {
            console.error("Failed to launch due review quiz:", e);
        }
    } else if (banner.action === 'practice_topic' && banner.topic) {
        // Launch a targeted quiz on this weak extracted topic
        try {
            const payload = {
                subjects: null,
                topics: [banner.topic],
                count: 10,
                spaced_repetition: true
            };
            const res = await fetch('/api/mcq/generate_set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status === 'success' && Array.isArray(data.questions) && data.questions.length > 0 && typeof window.launchPreparedQuiz === 'function') {
                window.launchPreparedQuiz({
                    sessionId: `sess_topic_${Date.now()}`,
                    customTitle: `🎯 Targeted Practice: ${banner.topic}`,
                    subjectName: banner.topic,
                    questions: data.questions,
                    topicsReviewed: true,
                    config: { mode: 'learn', spacedRepetition: true, count: data.questions.length }
                });
                return;
            }
        } catch (e) {
            console.error("Failed to generate topic set:", e);
        }
        // Fallback to quiz setup tab
        const quizNav = document.querySelector('.nav-btn[data-target="quiz-view"]');
        if (quizNav) quizNav.click();
    } else if (banner.action === 'search_topic_notes' && banner.topic) {
        // Study notes recommendation: perform global search or open reader on this topic
        if (typeof window.performGlobalSearch === 'function') {
            window.performGlobalSearch(banner.topic);
            return;
        }
        const searchInput = document.getElementById('global-search-input');
        if (searchInput) {
            searchInput.value = banner.topic;
            searchInput.dispatchEvent(new Event('input', { bubbles: true }));
            searchInput.focus();
        }
    } else if (banner.action === 'view_habits') {
        const habitNav = document.querySelector('.nav-btn[data-target="habit-tracker-view"]');
        if (habitNav) habitNav.click();
    } else if (banner.action === 'switch_subject' && banner.subject) {
        // Launch or switch to subject
        try {
            const payload = {
                subjects: [banner.subject],
                topics: null,
                count: 15,
                spaced_repetition: true
            };
            const res = await fetch('/api/mcq/generate_set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status === 'success' && Array.isArray(data.questions) && data.questions.length > 0 && typeof window.launchPreparedQuiz === 'function') {
                window.launchPreparedQuiz({
                    sessionId: `sess_sub_${Date.now()}`,
                    customTitle: `📚 Rotation: ${banner.subject}`,
                    subjectName: banner.subject,
                    questions: data.questions,
                    topicsReviewed: true,
                    config: { mode: 'learn', spacedRepetition: true, count: data.questions.length, subject: banner.subject }
                });
                return;
            }
        } catch (e) {
            console.error("Failed to generate subject set:", e);
        }
        const quizNav = document.querySelector('.nav-btn[data-target="quiz-view"]');
        if (quizNav) quizNav.click();
    }
}

window.initDashboard = initDashboard;

// ---------------------------------------------------------
// Rank Configuration Editor Modal Logic
// ---------------------------------------------------------
let localRankConfigs = [];

function attachRankEditorListeners() {
    const btn = document.getElementById('open-rank-editor-btn');
    if (btn) btn.addEventListener('click', openRankEditor);
}

function openRankEditor() {
    const state = window.AppState.get();
    localRankConfigs = JSON.parse(JSON.stringify(state.rankSimConfigs || []));

    if (localRankConfigs.length === 0) {
        localRankConfigs.push(JSON.parse(JSON.stringify(DEFAULT_RANK_CONFIG)));
    }
    renderRankEditor();
}

function closeRankEditor() {
    const modal = document.getElementById('rank-editor-modal');
    if (modal) modal.remove();
}

function saveRankEditor() {
    const cleaned = localRankConfigs.map(c => ({
        ...c,
        maxMarks: Number(c.totalQuestions || 200) * Number(c.marksPerCorrect || 4),
        dataPoints: [...(c.dataPoints || [])].sort((a, b) => Number(b.marks) - Number(a.marks)),
    }));

    window.AppState.update({ rankSimConfigs: cleaned });
    closeRankEditor();
    initDashboard();
}

function renderRankEditor() {
    let modal = document.getElementById('rank-editor-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'rank-editor-modal';
        modal.className = 'modal-backdrop fade-enter';
        modal.style.zIndex = '200';
        document.body.appendChild(modal);
    }

    const config = localRankConfigs[0];
    const escapeHTML = window.escapeHTML || ((s) => String(s || ''));

    modal.innerHTML = `
    <div class="glass-modal w-full max-w-2xl text-left flex flex-col max-h-[90vh]">
        <div class="flex justify-between items-center border-b border-gray-700/50 pb-4 mb-4">
            <h2 class="text-xl font-bold">Edit Rank Simulation Data</h2>
            <button id="close-rank-modal-x" class="text-muted hover:text-brand-400 bg-transparent border-none cursor-pointer">✕</button>
        </div>

        <div class="overflow-y-auto flex-1 pr-2">
            <div class="glass-panel rounded-xl p-4 border border-gray-700/50 mb-6">
                <h3 class="text-sm font-semibold mb-3">Exam Settings</h3>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                        <label class="text-xs text-muted block mb-1">Exam Name</label>
                        <input type="text" id="re-exam-name" value="${escapeHTML(config.examName)}" class="w-full glass-input rounded px-3 py-1.5 text-sm focus:border-brand-500 outline-none transition-colors">
                    </div>
                    <div>
                        <label class="text-xs text-muted block mb-1">Total Qs</label>
                        <input type="number" id="re-total-qs" value="${config.totalQuestions}" class="w-full glass-input rounded px-3 py-1.5 text-sm focus:border-brand-500 outline-none transition-colors">
                    </div>
                    <div>
                        <label class="text-xs text-muted block mb-1">Marks (+)</label>
                        <input type="number" id="re-marks-correct" value="${config.marksPerCorrect}" class="w-full glass-input rounded px-3 py-1.5 text-sm focus:border-brand-500 outline-none transition-colors">
                    </div>
                    <div>
                        <label class="text-xs text-muted block mb-1">Negative (-)</label>
                        <input type="number" id="re-negative" value="${config.negativePerWrong}" class="w-full glass-input rounded px-3 py-1.5 text-sm focus:border-brand-500 outline-none transition-colors">
                    </div>
                </div>
            </div>

            <div class="glass-panel rounded-xl p-4 border border-gray-700/50">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-sm font-semibold">Marks → Rank Cutoffs</h3>
                    <button id="re-add-row-btn" class="px-3 py-1 secondary-btn text-xs cursor-pointer">
                        + Add Row
                    </button>
                </div>

                <div id="re-data-points-container" class="space-y-2">
                    ${config.dataPoints.map((dp, idx) => `
                        <div class="flex gap-3 items-center">
                            <input type="number" data-idx="${idx}" data-field="marks" value="${dp.marks}" class="re-dp-input flex-1 glass-input rounded px-3 py-1.5 text-sm focus:border-brand-500 outline-none transition-colors" placeholder="Marks">
                            <input type="number" data-idx="${idx}" data-field="rank" value="${dp.rank}" class="re-dp-input flex-1 glass-input rounded px-3 py-1.5 text-sm focus:border-brand-500 outline-none transition-colors" placeholder="Rank">
                            <button data-idx="${idx}" class="re-dp-del p-2 text-muted hover:text-danger-500 bg-transparent border-none cursor-pointer transition-colors">🗑️</button>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>

        <div class="border-t border-gray-700/50 pt-4 mt-4 flex justify-end gap-3">
            <button id="close-rank-modal-btn" class="px-4 py-2 secondary-btn text-sm cursor-pointer">Cancel</button>
            <button id="save-rank-modal-btn" class="px-6 py-2 primary-btn rounded-lg font-medium cursor-pointer text-sm shadow-lg">Save Configuration</button>
        </div>
    </div>
    `;

    // Local state wiring
    document.getElementById('re-exam-name').oninput = (e) => localRankConfigs[0].examName = e.target.value;
    document.getElementById('re-total-qs').oninput = (e) => localRankConfigs[0].totalQuestions = parseInt(e.target.value, 10) || 200;
    document.getElementById('re-marks-correct').oninput = (e) => localRankConfigs[0].marksPerCorrect = parseFloat(e.target.value) || 4;
    document.getElementById('re-negative').oninput = (e) => localRankConfigs[0].negativePerWrong = parseFloat(e.target.value) ?? 1;

    document.querySelectorAll('.re-dp-input').forEach(input => {
        input.oninput = (e) => {
            const idx = parseInt(e.target.dataset.idx, 10);
            const field = e.target.dataset.field;
            localRankConfigs[0].dataPoints[idx][field] = parseFloat(e.target.value) || 0;
        };
    });

    document.querySelectorAll('.re-dp-del').forEach(btn => {
        btn.onclick = (e) => {
            const idx = parseInt(e.currentTarget.dataset.idx, 10);
            localRankConfigs[0].dataPoints.splice(idx, 1);
            renderRankEditor();
        };
    });

    document.getElementById('re-add-row-btn').onclick = () => {
        localRankConfigs[0].dataPoints.push({ marks: 0, rank: 0 });
        renderRankEditor();
    };

    document.getElementById('close-rank-modal-x').onclick = closeRankEditor;
    document.getElementById('close-rank-modal-btn').onclick = closeRankEditor;
    document.getElementById('save-rank-modal-btn').onclick = saveRankEditor;
}

// Bind to event lifecycle
document.addEventListener('stateUpdated', initDashboard);
document.addEventListener('DOMContentLoaded', () => {
    initDashboard();
});

// --- Phase 7 Flicker-Free KPI Counter Patching ---
(function () {
    window.updateDashboardKpiText = function (selectorOrEl, nextValue) {
        const el = typeof selectorOrEl === "string" ? document.querySelector(selectorOrEl) : selectorOrEl;
        if (!el) return false;
        const formatted = String(nextValue ?? "");
        if (el.textContent !== formatted) {
            el.textContent = formatted;
            return true;
        }
        return false;
    };
})();
