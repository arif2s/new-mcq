// ui/js/dashboard.js

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

function initDashboard() {
    if (!window.AppState || !window.AppState.get) return;
    const state = window.AppState.get();
    if (!state) return;

    const dashContainer = document.getElementById('dashboard-view');
    if (!dashContainer) return;

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

    // Rank Simulations
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
        return { config, testsWithRank, latest, best };
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
          <!-- SR Status -->
          <div class="glass-card card-shade-3">
            <h3 class="text-lg font-semibold mb-4 opacity-90">🧠 Spaced Repetition Status</h3>
            <div class="space-y-4 mt-2">
              ${['mastered', 'review', 'learning', 'new'].map(level => {
                  const count = ankiStats[level] || 0;
                  const pct = totalAnkiCards > 0 ? (count / totalAnkiCards) * 100 : 0;
                  const colors = {
                      mastered: 'bg-success-500',
                      review: 'bg-brand-500',
                      learning: 'bg-warning-500',
                      new: 'bg-gray-500'
                  };
                  return `
                  <div class="flex items-center justify-between">
                    <span class="text-sm capitalize w-20">${level}</span>
                    <div class="flex-1 mx-4 glass-panel rounded-full h-2.5 shadow-inner">
                      <div class="${colors[level]} h-2.5 rounded-full" style="width: ${pct.toFixed(1)}%"></div>
                    </div>
                    <span class="text-sm font-medium w-14 text-right font-mono">${count.toLocaleString()}</span>
                  </div>`;
              }).join('')}
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
            <!-- Hourly Activity -->
            <div class="glass-card">
                <h3 class="text-lg font-semibold mb-4 opacity-90">🕒 Today's Activity by Hour</h3>
                <div class="flex items-end gap-1 h-32 mb-2 border-b border-gray-700/50 pb-1">
                    ${hourlyData.map(h => `
                        <div class="flex-1 flex flex-col items-center group relative cursor-crosshair h-full justify-end">
                            <div class="w-full rounded-t transition-all ${h.questions > 0 ? 'bg-brand-500 hover:bg-brand-400 shadow-[0_0_8px_rgba(139,92,246,0.6)]' : 'glass-panel'}"
                                 style="height: ${h.questions > 0 ? Math.max((h.questions / maxQ) * 100, 5) : 2}%;"></div>
                            ${h.questions > 0 ? `<div class="absolute bottom-full mb-2 glass-card rounded px-2 py-1 text-xs opacity-0 group-hover:opacity-100 transition-opacity z-10 whitespace-nowrap">${h.questions} Qs (${h.hour}:00)</div>` : ''}
                        </div>
                    `).join('')}
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
                                <p class="text-xs text-muted mb-1">Percentile</p>
                                <p class="text-lg font-bold text-purple-400">${simData.latest.sim.percentile}%</p>
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