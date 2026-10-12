// ui/js/dashboard.js

function escapeHTML(str) {
    if (typeof str !== 'string') return '';
    return str.replace(/[&<>'"]/g, tag => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[tag] || tag));
}

// Ported Rank Simulation Math
function simulateRank(config, correct, wrong, actualQuestions) {
    if (actualQuestions === 0) return { projectedMarks: 0, estimatedRank: 0, percentile: "0.0" };

    const rawMarks = (correct * config.marksPerCorrect) - (wrong * config.negativePerWrong);
    let projectedMarks = rawMarks * (config.totalQuestions / actualQuestions);
    projectedMarks = Math.max(0, Math.round(projectedMarks));

    const dp = [...(config.dataPoints || [])].sort((a, b) => b.marks - a.marks);
    let estimatedRank = 50000; // Default fallback

    if (dp.length > 0) {
        if (projectedMarks >= dp[0].marks) {
            estimatedRank = dp[0].rank;
        } else if (projectedMarks <= dp[dp.length - 1].marks) {
            estimatedRank = dp[dp.length - 1].rank;
        } else {
            for (let i = 0; i < dp.length - 1; i++) {
                if (projectedMarks <= dp[i].marks && projectedMarks >= dp[i+1].marks) {
                    const markDiff = dp[i].marks - dp[i+1].marks;
                    const rankDiff = dp[i+1].rank - dp[i].rank;
                    const markOffset = dp[i].marks - projectedMarks;
                    const fraction = markDiff === 0 ? 0 : markOffset / markDiff;
                    estimatedRank = Math.round(dp[i].rank + (fraction * rankDiff));
                    break;
                }
            }
        }
    }

    const TOTAL_CANDIDATES = 200000; // Base assumption for percentile
    const percentile = Math.max(0, ((TOTAL_CANDIDATES - estimatedRank) / TOTAL_CANDIDATES) * 100).toFixed(1);

    return { projectedMarks, estimatedRank, percentile };
}

function initDashboard() {
    const state = window.AppState.get();
    if (!state) return;

    // --- 1. Basic Stats Calculation ---
    const totalTests = state.testResults.length;
    const avgAccuracy = totalTests > 0
        ? Math.round(state.testResults.reduce((acc, r) => acc + r.accuracy, 0) / totalTests)
        : 0;
    const totalTimeMinutes = Math.round(state.testResults.reduce((acc, r) => acc + r.timeUsedSeconds, 0) / 60);

    const dsAccuracy = document.getElementById('ds-accuracy');
    const dsTests = document.getElementById('ds-tests');
    const dsTime = document.getElementById('ds-time');

    if(dsAccuracy) dsAccuracy.textContent = `${avgAccuracy}%`;
    if(dsTests) dsTests.textContent = totalTests;
    if(dsTime) dsTime.textContent = `${totalTimeMinutes}m`;

    // --- 2. Daily Targets ---
    const today = new Date().toISOString().split('T')[0];
    const todayStats = { questionsAnswered: 0, correctAnswers: 0, timeSpentSeconds: 0 };
    const todayHabit = state.habitLog.find(h => h.date === today);

    if (todayHabit) {
        todayStats.questionsAnswered = todayHabit.questionsAnswered;
        todayStats.correctAnswers = todayHabit.correctAnswers;
    }

    const targets = state.dailyTargets;
    const qProgress = targets.questionsTarget > 0 ? (todayStats.questionsAnswered / targets.questionsTarget) * 100 : 100;
    const cProgress = targets.correctTarget > 0 ? (todayStats.correctAnswers / targets.correctTarget) * 100 : 100;

    const qProgEl = document.getElementById('ds-q-prog');
    const cProgEl = document.getElementById('ds-c-prog');

    if(qProgEl) {
        qProgEl.style.width = `${Math.min(100, qProgress)}%`;
        document.getElementById('ds-q-text').textContent = `${todayStats.questionsAnswered} / ${targets.questionsTarget}`;
    }
    if(cProgEl) {
        cProgEl.style.width = `${Math.min(100, cProgress)}%`;
        document.getElementById('ds-c-text').textContent = `${todayStats.correctAnswers} / ${targets.correctTarget}`;
    }

    // --- 3. Extended Analytics Container ---
    let extContainer = document.getElementById('ds-extended-container');
    if (!extContainer) {
        extContainer = document.createElement('div');
        extContainer.id = 'ds-extended-container';
        document.getElementById('dashboard-view').appendChild(extContainer);
    }

    // Data Processing
    const ankiCards = state.ankiCards || [];
    const ankiStats = ankiCards.reduce((acc, c) => {
        acc[c.status] = (acc[c.status] || 0) + 1;
        return acc;
    }, { mastered: 0, review: 0, learning: 0, new: 0 });

    const subjectPerformance = (state.subjects || []).map(s => {
        const sResults = state.testResults.filter(r => r.subjectName === s.name);
        const avg = sResults.length > 0 ? sResults.reduce((a, r) => a + r.accuracy, 0) / sResults.length : 0;
        return { name: s.name, tests: sResults.length, avgAccuracy: avg };
    });

    const recentResults = [...state.testResults].sort((a,b) => b.timestamp - a.timestamp).slice(0, 10);
    const rankConfigs = state.rankSimConfigs || [];

    const processedRankSims = rankConfigs.map(config => {
        const testsWithRank = recentResults.filter(r => r.totalQuestions > 0).map(r => ({
             ...r, sim: simulateRank(config, r.correctAnswers, r.wrongAnswers, r.totalQuestions)
        }));
        if (testsWithRank.length === 0) return null;
        const latest = testsWithRank[0];
        const best = testsWithRank.reduce((b, t) => t.sim.estimatedRank < b.sim.estimatedRank ? t : b, testsWithRank[0]);
        return { config, testsWithRank, latest, best };
    }).filter(s => s !== null);

    // Build Extended HTML
    let html = `<div class="grid md:grid-cols-2 gap-6 mb-8">`;

    // Spaced Repetition UI
    const totalAnki = ankiCards.length || 1; // prevent div by zero
    html += `
        <div class="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
          <h3 class="text-lg font-semibold text-gray-200 mb-4">🧠 Spaced Repetition Status</h3>
          <div class="space-y-3">
            ${[
                { label: 'Mastered', count: ankiStats.mastered, color: 'success-500', bg: 'bg-success-500' },
                { label: 'Review', count: ankiStats.review, color: 'brand-400', bg: 'bg-brand-500' },
                { label: 'Learning', count: ankiStats.learning, color: 'warning-500', bg: 'bg-warning-500' },
                { label: 'New', count: ankiStats.new, color: 'gray-400', bg: 'bg-gray-500' }
            ].map(stat => `
                <div class="flex items-center justify-between">
                  <span class="text-sm text-gray-400">${stat.label}</span>
                  <div class="flex items-center gap-2">
                    <div class="w-32 bg-gray-700 rounded-full h-2">
                      <div class="${stat.bg} h-2 rounded-full" style="width: ${(stat.count / totalAnki) * 100}%"></div>
                    </div>
                    <span class="text-sm text-${stat.color} font-medium w-8 text-right">${stat.count}</span>
                  </div>
                </div>
            `).join('')}
          </div>
        </div>
    `;

    // Subject Performance UI
    html += `<div class="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
        <h3 class="text-lg font-semibold text-gray-200 mb-4">📚 Subject Performance</h3>
        ${subjectPerformance.length === 0 ? '<p class="text-gray-500 text-sm">No subjects loaded yet.</p>' : `
        <div class="space-y-3">
            ${subjectPerformance.map(sp => {
                const barColor = sp.avgAccuracy >= 70 ? 'bg-success-500' : sp.avgAccuracy >= 50 ? 'bg-warning-500' : 'bg-danger-500';
                return `
                <div class="bg-gray-900/50 rounded-lg p-3">
                  <div class="flex justify-between items-center mb-1">
                    <span class="text-sm font-medium text-gray-300 truncate">${escapeHTML(sp.name)}</span>
                    <span class="text-xs text-gray-400 shrink-0">${sp.tests} tests</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <div class="flex-1 bg-gray-700 rounded-full h-2">
                      <div class="h-2 rounded-full ${barColor}" style="width: ${sp.avgAccuracy}%"></div>
                    </div>
                    <span class="text-xs text-gray-400 w-12 text-right">${sp.avgAccuracy.toFixed(0)}%</span>
                  </div>
                </div>`;
            }).join('')}
        </div>`}
    </div></div>`;

    // Rank Simulation UI
    if (processedRankSims.length > 0) {
        html += `<div class="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-lg font-semibold text-gray-200 flex items-center gap-2">🏅 Preparedness — Rank Trend</h3>
            </div>`;

        processedRankSims.forEach(simData => {
            html += `
            <div class="mb-6 last:mb-0">
              <p class="text-sm text-gray-400 mb-3">${escapeHTML(simData.config.examName)}</p>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div class="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p class="text-xs text-gray-400">Latest Rank</p>
                  <p class="text-xl font-bold text-brand-400">${simData.latest.sim.estimatedRank.toLocaleString()}</p>
                  <p class="text-xs text-gray-500">${simData.latest.sim.projectedMarks}/${simData.config.maxMarks}</p>
                </div>
                <div class="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p class="text-xs text-gray-400">Best Rank</p>
                  <p class="text-xl font-bold text-success-500">${simData.best.sim.estimatedRank.toLocaleString()}</p>
                  <p class="text-xs text-gray-500">${simData.best.sim.projectedMarks}/${simData.config.maxMarks}</p>
                </div>
                <div class="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p class="text-xs text-gray-400">Latest Percentile</p>
                  <p class="text-xl font-bold text-purple-400">${simData.latest.sim.percentile}%</p>
                </div>
                <div class="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p class="text-xs text-gray-400">Best Percentile</p>
                  <p class="text-xl font-bold text-warning-500">${simData.best.sim.percentile}%</p>
                </div>
              </div>
            </div>`;
        });
        html += `</div>`;
    }

    // Recent Results Table
    html += `<div class="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
        <h3 class="text-lg font-semibold text-gray-200 mb-4">📝 Recent Test Results</h3>
        ${recentResults.length === 0 ? '<p class="text-gray-500 text-sm">No tests taken yet. Upload a CSV and start a quiz!</p>' : `
        <div class="overflow-x-auto">
            <table class="w-full text-sm text-left">
              <thead>
                <tr class="border-b border-gray-700">
                  <th class="py-3 px-2 text-gray-400 font-medium">Date</th>
                  <th class="py-3 px-2 text-gray-400 font-medium">Subject</th>
                  <th class="py-3 px-2 text-gray-400 font-medium">Mode</th>
                  <th class="py-3 px-2 text-gray-400 font-medium text-center">Q's</th>
                  <th class="py-3 px-2 text-gray-400 font-medium text-center">Score</th>
                  <th class="py-3 px-2 text-gray-400 font-medium text-center">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                ${recentResults.map((r, i) => {
                    const accClass = r.accuracy >= 70 ? 'text-success-500' : r.accuracy >= 50 ? 'text-warning-500' : 'text-danger-500';
                    const modeClass = r.mode === 'learn' ? 'bg-purple-500/20 text-purple-400' : 'bg-brand-500/20 text-brand-400';
                    return `
                    <tr class="border-b border-gray-700/50 ${i % 2 === 0 ? '' : 'bg-gray-800/30'}">
                      <td class="py-2.5 px-2 text-gray-300">${new Date(r.timestamp).toLocaleDateString()}</td>
                      <td class="py-2.5 px-2 text-gray-300 truncate max-w-[150px]">${escapeHTML(r.subjectName)}</td>
                      <td class="py-2.5 px-2"><span class="px-2 py-0.5 rounded-full text-xs uppercase ${modeClass}">${escapeHTML(r.mode)}</span></td>
                      <td class="py-2.5 px-2 text-center text-gray-300">${r.totalQuestions}</td>
                      <td class="py-2.5 px-2 text-center text-gray-300">${r.score}</td>
                      <td class="py-2.5 px-2 text-center font-medium ${accClass}">${r.accuracy.toFixed(1)}%</td>
                    </tr>`;
                }).join('')}
              </tbody>
            </table>
        </div>`}
    </div>`;

    extContainer.innerHTML = html;
}

document.addEventListener('stateUpdated', initDashboard);
document.addEventListener('DOMContentLoaded', async () => {
    // Await state load if it hasn't happened yet
    if (window.AppState && window.AppState.fetch) {
        await window.AppState.fetch();
    }
    initDashboard();

    // Re-render when dashboard nav is clicked to ensure fresh data
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (e.target.getAttribute('data-target') === 'dashboard-view') {
                initDashboard();
            }
        });
    });
});
