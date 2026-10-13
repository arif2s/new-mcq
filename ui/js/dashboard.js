// ui/js/dashboard.js

function escapeHTML(str) {
    if (typeof str !== 'string') return '';
    return str.replace(/[&<>'"]/g, tag => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[tag] || tag));
}

// Helper to get local date strings
function getTodayString() {
    return new Date().toISOString().split('T')[0];
}

function getYesterdayString() {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().split('T')[0];
}

// Compute daily stats for "Today vs Yesterday" and Hourly Heatmap
function getDayStats(state, dateStr) {
    const results = state.testResults.filter(r => {
        const d = new Date(r.timestamp);
        return d.toISOString().split('T')[0] === dateStr;
    });

    const hourlyStats = new Map();
    let questionsAnswered = 0;
    let correctAnswers = 0;
    let timeSpentSeconds = 0;

    results.forEach(r => {
        questionsAnswered += r.totalQuestions;
        correctAnswers += r.correctAnswers;
        timeSpentSeconds += r.timeUsedSeconds;

        const hour = new Date(r.timestamp).getHours();
        if (!hourlyStats.has(hour)) {
            hourlyStats.set(hour, { hour, questionsAnswered: 0, timeSpentSeconds: 0 });
        }
        const hStat = hourlyStats.get(hour);
        hStat.questionsAnswered += r.totalQuestions;
        hStat.timeSpentSeconds += r.timeUsedSeconds;
    });

    return {
        questionsAnswered,
        correctAnswers,
        timeSpentSeconds,
        accuracy: questionsAnswered > 0 ? (correctAnswers / questionsAnswered) * 100 : 0,
        hourlyStats: Array.from(hourlyStats.values())
    };
}

// Enhanced Rank Simulation with Banding
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

    const TOTAL_CANDIDATES = 200000; // Base assumption
    const percentile = Math.max(0, ((TOTAL_CANDIDATES - estimatedRank) / TOTAL_CANDIDATES) * 100).toFixed(1);

    // Mock bands for UI completeness if none exist
    const bands = config.bands && config.bands.length > 0 ? config.bands : [
        { label: "1-100", marksMin: config.maxMarks * 0.85, marksMax: config.maxMarks, rankMin: 1, rankMax: 100, outlook: "Top-tier" },
        { label: "100-3.5k", marksMin: config.maxMarks * 0.70, marksMax: config.maxMarks * 0.85 - 1, rankMin: 101, rankMax: 3500, outlook: "Clinical branches in range" }
    ];

    let currentBand = null;
    let nextBand = null;

    const sortedBands = [...bands].sort((a, b) => a.marksMin - b.marksMin);
    for (let i = 0; i < sortedBands.length; i++) {
        if (projectedMarks >= sortedBands[i].marksMin && projectedMarks <= sortedBands[i].marksMax) {
            currentBand = sortedBands[i];
            if (i < sortedBands.length - 1) nextBand = sortedBands[i+1];
            break;
        }
    }

    if (!currentBand && sortedBands.length > 0 && projectedMarks < sortedBands[0].marksMin) {
        nextBand = sortedBands[0];
    }

    const marksToNextBand = nextBand ? nextBand.marksMin - projectedMarks : 0;

    return { projectedMarks, estimatedRank, percentile, rawMarks, currentBand, nextBand, marksToNextBand };
}

// ---------------------------------------------------------
// UI Rendering Pipeline
// ---------------------------------------------------------

function initDashboard() {
    const state = window.AppState.get();
    if (!state) return;

    const todayStats = getDayStats(state, getTodayString());
    const yesterdayStats = getDayStats(state, getYesterdayString());

    const dsAccuracy = document.getElementById('ds-accuracy');
    const dsTests = document.getElementById('ds-tests');
    const dsTime = document.getElementById('ds-time');

    const totalTests = state.testResults.length;
    const avgAccuracy = totalTests > 0 ? state.testResults.reduce((a, r) => a + r.accuracy, 0) / totalTests : 0;
    const totalTimeMin = Math.round(state.testResults.reduce((a, r) => a + r.timeUsedSeconds, 0) / 60);

    if(dsAccuracy) dsAccuracy.textContent = `${Math.round(avgAccuracy)}%`;
    if(dsTests) dsTests.textContent = totalTests;
    if(dsTime) dsTime.textContent = `${totalTimeMin}m`;

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

    let extContainer = document.getElementById('ds-extended-container');
    if (!extContainer) {
        extContainer = document.createElement('div');
        extContainer.id = 'ds-extended-container';
        document.getElementById('dashboard-view').appendChild(extContainer);
    }

    let html = `<div class="grid md:grid-cols-2 gap-6 mb-8">`;
    html += renderTodayVsYesterday(todayStats, yesterdayStats);
    html += renderHourlyActivity(todayStats);
    html += `</div>`;

    html += renderRankSimulations(state);
    html += renderRecentResults(state);

    extContainer.innerHTML = html;
    attachRankEditorListeners();
}

// ---------------------------------------------------------
// Sub-renderers
// ---------------------------------------------------------

function getTrendHTML(today, yesterday, isTime = false) {
    if (yesterday === 0 && today === 0) return `<span class="text-gray-500">-</span>`;
    if (yesterday === 0) return `<span class="text-success-500">+100%</span>`;
    const diff = ((today - yesterday) / yesterday) * 100;

    if (diff > 5) return `<span class="text-${isTime ? 'brand-400' : 'success-500'}">+${Math.abs(diff).toFixed(0)}%</span>`;
    if (diff < -5) return `<span class="text-danger-500">-${Math.abs(diff).toFixed(0)}%</span>`;
    return `<span class="text-gray-500">Same</span>`;
}

function renderTodayVsYesterday(todayStats, yesterdayStats) {
    return `
    <div class="bg-gray-800/60 rounded-2xl p-6 border border-gray-700">
        <h2 class="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
            ⚡ Today vs Yesterday
        </h2>
        <div class="space-y-4">
            <div class="flex justify-between text-sm">
                <span class="text-gray-400">Questions</span>
                <div>
                    <span class="text-gray-500">${yesterdayStats.questionsAnswered} &rarr; </span>
                    <span class="text-gray-200 font-medium">${todayStats.questionsAnswered}</span>
                    <span class="ml-2 w-16 inline-block text-right">${getTrendHTML(todayStats.questionsAnswered, yesterdayStats.questionsAnswered)}</span>
                </div>
            </div>
            <div class="flex justify-between text-sm">
                <span class="text-gray-400">Correct</span>
                <div>
                    <span class="text-gray-500">${yesterdayStats.correctAnswers} &rarr; </span>
                    <span class="text-gray-200 font-medium">${todayStats.correctAnswers}</span>
                    <span class="ml-2 w-16 inline-block text-right">${getTrendHTML(todayStats.correctAnswers, yesterdayStats.correctAnswers)}</span>
                </div>
            </div>
            <div class="flex justify-between text-sm">
                <span class="text-gray-400">Accuracy</span>
                <div>
                    <span class="text-gray-500">${yesterdayStats.accuracy.toFixed(0)}% &rarr; </span>
                    <span class="text-gray-200 font-medium">${todayStats.accuracy.toFixed(0)}%</span>
                    <span class="ml-2 w-16 inline-block text-right">${getTrendHTML(todayStats.accuracy, yesterdayStats.accuracy)}</span>
                </div>
            </div>
        </div>
    </div>`;
}

function renderHourlyActivity(todayStats) {
    const hourlyData = Array.from({ length: 24 }, (_, i) => {
        const hStat = todayStats.hourlyStats.find(h => h.hour === i);
        return { hour: i, questions: hStat ? hStat.questionsAnswered : 0 };
    });
    const maxQ = Math.max(...hourlyData.map(h => h.questions), 1);

    let barsHtml = hourlyData.map(h => {
        const height = (h.questions / maxQ) * 100;
        const bg = h.questions > 0 ? 'bg-brand-500 hover:bg-brand-400' : 'bg-gray-700';
        const displayHeight = h.questions > 0 ? Math.max(height, 5) : 2;
        return `
            <div class="flex-1 flex flex-col items-center group relative cursor-crosshair">
                <div class="w-full rounded-t transition-all ${bg}" style="height: ${displayHeight}%;"></div>
                ${h.questions > 0 ? `<div class="absolute bottom-full mb-2 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs opacity-0 group-hover:opacity-100 transition-opacity z-10 whitespace-nowrap text-white">${h.questions} Qs</div>` : ''}
            </div>
        `;
    }).join('');

    return `
    <div class="bg-gray-800/60 rounded-2xl p-6 border border-gray-700">
        <h2 class="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
            🕒 Today's Activity by Hour
        </h2>
        <div class="flex items-end gap-1 h-24 mb-2 border-b border-gray-700 pb-1">
            ${barsHtml}
        </div>
        <div class="flex justify-between text-xs text-gray-500">
            <span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span>
        </div>
    </div>`;
}

function renderRankSimulations(state) {
    const rankConfigs = state.rankSimConfigs || [];
    const recentResults = [...state.testResults].sort((a,b) => b.timestamp - a.timestamp).slice(0, 10);

    const processedRankSims = rankConfigs.map(config => {
        const testsWithRank = recentResults.filter(r => r.totalQuestions > 0).map(r => ({
             ...r, sim: simulateRank(config, r.correctAnswers, r.wrongAnswers, r.totalQuestions)
        }));
        if (testsWithRank.length === 0) return null;
        const latest = testsWithRank[0];
        const best = testsWithRank.reduce((b, t) => t.sim.estimatedRank < b.sim.estimatedRank ? t : b, testsWithRank[0]);
        return { config, testsWithRank, latest, best };
    }).filter(s => s !== null);

    let html = `<div class="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8 relative">
        <div class="flex items-center justify-between mb-4">
            <h3 class="text-lg font-semibold text-gray-200 flex items-center gap-2">🏅 Preparedness & Rank Trend</h3>
            <button id="open-rank-editor-btn" class="px-3 py-1.5 bg-gray-700 text-gray-300 rounded-lg text-sm hover:bg-gray-600 transition-colors">
                ⚙️ Edit Rank Data
            </button>
        </div>`;

    if (processedRankSims.length === 0) {
        html += `<p class="text-gray-500 text-sm">Take a quiz to see rank projections.</p></div>`;
        return html;
    }

    processedRankSims.forEach(simData => {
        const config = simData.config;
        const sim = simData.latest.sim;

        html += `
        <div class="mb-6 last:mb-0 border border-gray-700 rounded-xl p-4 bg-gray-900/30">
            <div class="flex justify-between items-center mb-3">
                <p class="text-sm font-semibold text-brand-400">${escapeHTML(config.examName)}</p>
                <span class="text-xs bg-gray-800 px-2 py-1 rounded text-gray-400">Total: ${config.maxMarks} Marks</span>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div class="bg-gray-800 rounded-lg p-3 text-center border border-gray-700">
                    <p class="text-xs text-gray-400 mb-1">Latest Rank</p>
                    <p class="text-xl font-bold text-brand-400">${sim.estimatedRank.toLocaleString()}</p>
                    <p class="text-[10px] text-gray-500">${sim.projectedMarks} Marks</p>
                </div>
                <div class="bg-gray-800 rounded-lg p-3 text-center border border-gray-700">
                    <p class="text-xs text-gray-400 mb-1">Best Rank</p>
                    <p class="text-xl font-bold text-success-500">${simData.best.sim.estimatedRank.toLocaleString()}</p>
                    <p class="text-[10px] text-gray-500">${simData.best.sim.projectedMarks} Marks</p>
                </div>
                <div class="bg-gray-800 rounded-lg p-3 text-center border border-gray-700">
                    <p class="text-xs text-gray-400 mb-1">Percentile</p>
                    <p class="text-xl font-bold text-purple-400">${sim.percentile}%</p>
                </div>
                <div class="bg-gray-800 rounded-lg p-3 text-center border border-gray-700">
                    <p class="text-xs text-gray-400 mb-1">Score Breakdown</p>
                    <p class="text-sm font-bold text-gray-200 mt-1">
                        <span class="text-success-500">${simData.latest.correctAnswers}✓</span>
                        <span class="text-danger-500">${simData.latest.wrongAnswers}✗</span>
                    </p>
                </div>
            </div>

            ${sim.nextBand && sim.marksToNextBand > 0 ? `
            <div class="bg-brand-500/10 border border-brand-500/30 rounded-lg p-3 text-sm">
                <p class="font-semibold text-brand-400 mb-1">+${sim.marksToNextBand} marks to reach AIR${sim.nextBand.label}</p>
                <p class="text-xs text-gray-400">≈ ${Math.ceil(sim.marksToNextBand / config.marksPerCorrect)} more correct answers needed.</p>
            </div>` : ''}
        </div>`;
    });

    html += `</div>`;
    return html;
}

function renderRecentResults(state) {
    const recentResults = [...state.testResults].sort((a,b) => b.timestamp - a.timestamp).slice(0, 10);

    let html = `<div class="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
        <h3 class="text-lg font-semibold text-gray-200 mb-4">📝 Recent Test Results</h3>`;

    if (recentResults.length === 0) {
        html += '<p class="text-gray-500 text-sm">No tests taken yet. Upload a CSV and start a quiz!</p></div>';
        return html;
    }

    html += `<div class="overflow-x-auto">
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
            <tbody>`;

    recentResults.forEach((r, i) => {
        const accClass = r.accuracy >= 70 ? 'text-success-500' : r.accuracy >= 50 ? 'text-warning-500' : 'text-danger-500';
        const modeClass = r.mode === 'learn' ? 'bg-purple-500/20 text-purple-400' : 'bg-brand-500/20 text-brand-400';
        html += `
        <tr class="border-b border-gray-700/50 ${i % 2 !== 0 ? 'bg-gray-800/30' : ''}">
            <td class="py-2.5 px-2 text-gray-300">${new Date(r.timestamp).toLocaleDateString()}</td>
            <td class="py-2.5 px-2 text-gray-300 truncate max-w-[150px]">${escapeHTML(r.subjectName)}</td>
            <td class="py-2.5 px-2"><span class="px-2 py-0.5 rounded-full text-xs uppercase ${modeClass}">${escapeHTML(r.mode || 'test')}</span></td>
            <td class="py-2.5 px-2 text-center text-gray-300">${r.totalQuestions}</td>
            <td class="py-2.5 px-2 text-center text-gray-300">${r.score || r.correctAnswers}</td>
            <td class="py-2.5 px-2 text-center font-medium ${accClass}">${r.accuracy.toFixed(1)}%</td>
        </tr>`;
    });

    html += `</tbody></table></div></div>`;
    return html;
}

// ---------------------------------------------------------
// Rank Configuration Editor Modal Logic
// ---------------------------------------------------------

let localRankConfigs = [];

function attachRankEditorListeners() {
    const btn = document.getElementById('open-rank-editor-btn');
    if (btn) {
        btn.addEventListener('click', openRankEditor);
    }
}

function openRankEditor() {
    const state = window.AppState.get();
    // Deep clone to avoid mutating state directly before saving
    localRankConfigs = JSON.parse(JSON.stringify(state.rankSimConfigs || []));

    // Ensure at least one config exists to edit
    if (localRankConfigs.length === 0) {
        localRankConfigs.push({
            examName: "NEET PG (Default)",
            totalQuestions: 200,
            marksPerCorrect: 4,
            negativePerWrong: 1,
            maxMarks: 800,
            dataPoints: [
                { marks: 700, rank: 100 },
                { marks: 600, rank: 5000 },
                { marks: 500, rank: 25000 }
            ],
            bands: []
        });
    }

    renderRankEditor();
}

function closeRankEditor() {
    const modal = document.getElementById('rank-editor-modal');
    if (modal) modal.remove();
}

function saveRankEditor() {
    // Clean up derived values before saving
    const cleaned = localRankConfigs.map(c => ({
        ...c,
        maxMarks: c.totalQuestions * c.marksPerCorrect,
        dataPoints: [...c.dataPoints].sort((a, b) => b.marks - a.marks),
    }));

    window.AppState.update({ rankSimConfigs: cleaned });
    closeRankEditor();
    initDashboard(); // Re-render dashboard with new stats
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

    const config = localRankConfigs[0]; // For simplicity in Vanilla JS, edit the first config or map them. We'll map the first.

    modal.innerHTML = `
    <div class="modal glass-modal w-full max-w-2xl text-left flex flex-col max-h-[90vh]">
        <div class="flex justify-between items-center border-b border-gray-700 pb-4 mb-4">
            <h2 class="text-xl font-bold text-gray-100">Edit Rank Simulation Data</h2>
            <button id="close-rank-modal-x" class="text-gray-400 hover:text-white bg-transparent border-none cursor-pointer">✕</button>
        </div>

        <div class="overflow-y-auto flex-1 pr-2">
            <div class="bg-gray-800/50 rounded-xl p-4 border border-gray-700 mb-6">
                <h3 class="text-sm font-semibold text-gray-300 mb-3">Exam Settings</h3>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                        <label class="text-xs text-gray-400 block mb-1">Exam Name</label>
                        <input type="text" id="re-exam-name" value="${escapeHTML(config.examName)}" class="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-white">
                    </div>
                    <div>
                        <label class="text-xs text-gray-400 block mb-1">Total Qs</label>
                        <input type="number" id="re-total-qs" value="${config.totalQuestions}" class="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-white">
                    </div>
                    <div>
                        <label class="text-xs text-gray-400 block mb-1">Marks (+)</label>
                        <input type="number" id="re-marks-correct" value="${config.marksPerCorrect}" class="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-white">
                    </div>
                    <div>
                        <label class="text-xs text-gray-400 block mb-1">Negative (-)</label>
                        <input type="number" id="re-negative" value="${config.negativePerWrong}" class="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-white">
                    </div>
                </div>
            </div>

            <div class="bg-gray-800/50 rounded-xl p-4 border border-gray-700">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-sm font-semibold text-gray-300">Marks → Rank Cutoffs</h3>
                    <button id="re-add-row-btn" class="px-3 py-1 bg-brand-600/20 text-brand-400 rounded hover:bg-brand-600/40 text-xs border-none cursor-pointer">
                        + Add Row
                    </button>
                </div>

                <div id="re-data-points-container" class="space-y-2">
                    ${config.dataPoints.map((dp, idx) => `
                        <div class="flex gap-3 items-center">
                            <input type="number" data-idx="${idx}" data-field="marks" value="${dp.marks}" class="re-dp-input flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" placeholder="Marks">
                            <input type="number" data-idx="${idx}" data-field="rank" value="${dp.rank}" class="re-dp-input flex-1 bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" placeholder="Rank">
                            <button data-idx="${idx}" class="re-dp-del p-2 text-gray-500 hover:text-danger-500 bg-transparent border-none cursor-pointer">🗑️</button>
                        </div>
                    `).join('')}
                </div>
            </div>
        </div>

        <div class="border-t border-gray-700 pt-4 mt-4 flex justify-end gap-3">
            <button id="close-rank-modal-btn" class="px-4 py-2 text-gray-400 hover:text-white bg-transparent border-none cursor-pointer text-sm">Cancel</button>
            <button id="save-rank-modal-btn" class="px-6 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-lg font-medium cursor-pointer border-none text-sm">Save Configuration</button>
        </div>
    </div>
    `;

    modal.style.display = 'flex';

    // Bind local state update listeners
    document.getElementById('re-exam-name').oninput = (e) => localRankConfigs[0].examName = e.target.value;
    document.getElementById('re-total-qs').oninput = (e) => localRankConfigs[0].totalQuestions = parseInt(e.target.value) || 200;
    document.getElementById('re-marks-correct').oninput = (e) => localRankConfigs[0].marksPerCorrect = parseFloat(e.target.value) || 4;
    document.getElementById('re-negative').oninput = (e) => localRankConfigs[0].negativePerWrong = parseFloat(e.target.value) || 1;

    document.querySelectorAll('.re-dp-input').forEach(input => {
        input.oninput = (e) => {
            const idx = parseInt(e.target.dataset.idx);
            const field = e.target.dataset.field;
            localRankConfigs[0].dataPoints[idx][field] = parseFloat(e.target.value) || 0;
        };
    });

    document.querySelectorAll('.re-dp-del').forEach(btn => {
        btn.onclick = (e) => {
            const idx = parseInt(e.currentTarget.dataset.idx);
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

// ---------------------------------------------------------
// Init Listener
// ---------------------------------------------------------

document.addEventListener('stateUpdated', initDashboard);
document.addEventListener('DOMContentLoaded', async () => {
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
