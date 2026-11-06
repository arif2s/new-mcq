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

let _dueSummaryData = { total_due: 0, session_id: null };
let _dashboardBanners = [];
let _activeBannerIndex = 0;
let _bannerAutoCycleTimer = null;
let _bannerRotationIntervalTimer = null;
const MAX_ACTIVE_BANNERS = 10;
const BANNER_AUTO_CYCLE_MS = 7000;       // Auto advance to next banner every 7s
const BANNER_REPLACE_CYCLE_MS = 60000;   // Gradually retire/replace 1 older banner every 60s

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

async function fetchDashboardPrompts() {
    try {
        const res = await fetch('/api/sessions/dashboard_prompts');
        if (res.ok) {
            const data = await res.json();
            const incoming = Array.isArray(data.banners) ? data.banners : [];
            if (incoming.length > 0) {
                // Always prioritize action banners (due_review, weak_topic, switch_subject) at the front
                // and update existing banners in-place so updated counts are immediately visible.
                const priorityBanners = incoming.filter(b => b.type !== 'encouragement');
                const encouragementBanners = incoming.filter(b => b.type === 'encouragement');

                _dashboardBanners = [
                    ...priorityBanners,
                    ...encouragementBanners
                ].slice(0, MAX_ACTIVE_BANNERS);

                // If there is an active due_review banner, focus carousel on index 0
                if (priorityBanners.some(b => b.type === 'due_review')) {
                    _activeBannerIndex = 0;
                } else if (_activeBannerIndex >= _dashboardBanners.length) {
                    _activeBannerIndex = 0;
                }
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

        ${Number(_dueSummaryData.total_due || 0) > 0 ? `
        <!-- Persistent Spaced Repetition & Wrong Questions Notification Alert -->
        <div id="dash-due-notification-alert" class="glass-card border-2 border-warning-500/60 bg-warning-500/15 p-5 rounded-2xl mb-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-[0_0_20px_rgba(234,179,8,0.2)] animate-fade-in">
          <div class="flex items-center gap-4">
            <div class="w-12 h-12 rounded-2xl bg-warning-500/20 border border-warning-500/40 flex items-center justify-center text-2xl shrink-0">
              🔔
            </div>
            <div>
              <div class="flex items-center gap-2 flex-wrap">
                <h2 class="text-base font-bold text-warning-300">
                  Spaced Repetition &amp; Wrong Questions Ready for Review
                </h2>
                <span class="px-2.5 py-0.5 text-[11px] font-bold uppercase rounded-full bg-warning-500/30 text-warning-200 border border-warning-400/40">
                  ${Number(_dueSummaryData.total_due)} Due
                </span>
              </div>
              <p class="text-xs text-muted mt-1">
                ${Number(_dueSummaryData.total_due)} wrongly answered or scheduled review question${Number(_dueSummaryData.total_due) === 1 ? ' has' : 's have'} been added to your consolidated review set in Knowledge Hub.
              </p>
            </div>
          </div>
          <div class="flex items-center gap-2.5 shrink-0 self-end sm:self-center">
            <button type="button" id="dash-open-due-hub-btn" class="secondary-btn px-3.5 py-2 rounded-xl text-xs font-semibold cursor-pointer">
              📚 View in Hub
            </button>
            <button type="button" id="dash-start-due-quiz-btn" class="primary-btn px-4 py-2 rounded-xl text-xs font-bold shadow-lg cursor-pointer">
              ▶️ Practice Now (${Number(_dueSummaryData.total_due)})
            </button>
          </div>
        </div>
        ` : ''}

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

    document.getElementById('dash-open-due-hub-btn')?.addEventListener('click', () => {
        if (typeof window.loadSessions === 'function') window.loadSessions();
        const hubNav = document.querySelector('.nav-btn[data-target="knowledge-hub-view"]');
        if (hubNav) hubNav.click();
    });

    document.getElementById('dash-start-due-quiz-btn')?.addEventListener('click', () => {
        handleBannerAction({
            action: 'review_due',
            session_id: _dueSummaryData.session_id || 'sess_spaced_repetition_due'
        });
    });

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

    if (currentBanner.type === 'due_review') {
        borderBgClass = 'border-warning-500/50 bg-warning-500/10 shadow-[0_0_15px_rgba(234,179,8,0.2)]';
        titleClass = 'text-warning-300';
        badgeClass = 'bg-warning-500/20 text-warning-300 border border-warning-500/30';
    } else if (currentBanner.type === 'weak_topic') {
        borderBgClass = 'border-danger-500/40 bg-danger-500/10 shadow-[0_0_15px_rgba(239,68,68,0.15)]';
        titleClass = 'text-danger-300';
        badgeClass = 'bg-danger-500/20 text-danger-300 border border-danger-500/30';
    } else if (currentBanner.type === 'switch_subject') {
        borderBgClass = 'border-cyan-500/40 bg-cyan-500/10 shadow-[0_0_15px_rgba(6,182,212,0.15)]';
        titleClass = 'text-cyan-300';
        badgeClass = 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30';
    } else if (currentBanner.type === 'encouragement') {
        borderBgClass = 'border-emerald-500/40 bg-emerald-500/10 shadow-[0_0_15px_rgba(16,185,129,0.15)]';
        titleClass = 'text-emerald-300';
        badgeClass = 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
    }

    container.innerHTML = `
        <div id="dash-banner-card" class="glass-card border ${borderBgClass} p-5 rounded-2xl flex flex-col md:flex-row items-center justify-between gap-4 animate-fade-in transition-all relative overflow-hidden group">
            <!-- Icon & Content -->
            <div class="flex items-center gap-4 flex-1 w-full">
                <span class="text-3xl select-none shrink-0">${currentBanner.icon || '💡'}</span>
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
        // Gradually retire/replace 1 older banner and populate new prompts
        try {
            const res = await fetch('/api/sessions/dashboard_prompts');
            if (res.ok) {
                const data = await res.json();
                const incoming = Array.isArray(data.banners) ? data.banners : [];
                if (incoming.length > 0) {
                    // Retire one older encouragement banner
                    const retireIdx = _dashboardBanners.findIndex(b => b.type === 'encouragement');
                    if (retireIdx !== -1) {
                        _dashboardBanners.splice(retireIdx, 1);
                    } else if (_dashboardBanners.length >= MAX_ACTIVE_BANNERS) {
                        _dashboardBanners.shift();
                    }

                    // Pick an incoming banner not currently displayed
                    const currentIds = new Set(_dashboardBanners.map(b => b.id));
                    const candidate = incoming.find(b => !currentIds.has(b.id)) || incoming[Math.floor(Math.random() * incoming.length)];
                    if (candidate && _dashboardBanners.length < MAX_ACTIVE_BANNERS) {
                        _dashboardBanners.push(candidate);
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